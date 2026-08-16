"""
FISIOLOGIA_WORKER.PY — VERSÃO FINAL COM AQUECIMENTO INTEGRADO

Processa:
  1. Min/Avg/Max para HR, Resp, SmO2, tHb, DFA1 (Fase B)
  2. AQUECIMENTO: detecta padrão + extrai métricas (automático no loop)
  
O aquecimento é processado CADA VEZ que uma atividade é processada.
"""

import numpy as np
import sqlite3
from datetime import datetime
import sys
sys.path.insert(0, './utils')

LOTE_WEB_MAX = 300

COLUNAS_EXTRA = {
    'velocidade_ms', 'distancia_m', 'pace_s_km',  # Fase A
    # Fase B: HR min/avg/max
    'hr_min_60s', 'hr_avg_60s', 'hr_max_60s',
    # Fase B: Resp min/avg/max
    'resp_min_60s', 'resp_avg_60s', 'resp_max_60s',
    # Fase B: SmO2 min/avg/max
    'smo2_min_60s', 'smo2_avg_60s', 'smo2_max_60s',
    # Fase B: tHb min/avg/max
    'thb_min_60s', 'thb_avg_60s', 'thb_max_60s',
    # Fase B: DFA-α1 min/avg/max
    'dfa1_min_60s', 'dfa1_avg_60s', 'dfa1_max_60s',
}

def _garantir_colunas(conn):
    """Cria as colunas novas se não existirem."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(fisiologia_intervalos)")}
    para_criar = COLUNAS_EXTRA - existing
    
    for col in para_criar:
        try:
            conn.execute(f"ALTER TABLE fisiologia_intervalos ADD COLUMN {col} REAL DEFAULT NULL")
        except sqlite3.OperationalError as e:
            if 'duplicate column' not in str(e):
                pass
    
    conn.commit()
    return list(para_criar)

def _processar_aquecimento(conn, activity_id, modalidade, data=None):
    """Analisa o aquecimento de uma atividade e grava os blocos.

    Devolve True se o protocolo foi detectado e gravado. Atividades que nao
    seguem o protocolo ficam marcadas como rejeitadas, para nao voltarem a
    ser reanalisadas em cada passagem.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'utils'))
        from aquecimento_analyzer import AquecimentoAnalyzer
        import aquecimento_db as aq_db
    except Exception as e:
        print(f"[AQUECIMENTO] import falhou: {type(e).__name__}: {e}")
        return False

    if modalidade not in ('Row', 'Ski', 'Bike'):
        return False

    try:
        if aq_db.ja_analisada(activity_id):
            return False

        resultado = AquecimentoAnalyzer(conn).analisar_atividade(
            activity_id, modalidade)

        if not resultado.get('detectado'):
            aq_db.marcar_rejeitada(activity_id, modalidade, data,
                                   resultado.get('motivo', 'desconhecido'))
            return False

        aq_db.salvar_blocos(activity_id, modalidade, data,
                            resultado['blocos'], sync=False)
        return True

    except Exception as e:
        print(f"[AQUECIMENTO] erro em {activity_id}: {type(e).__name__}: {e}")
        return False

def _varrer_aquecimento_pendente(conn=None, limite=80):
    """Analisa o aquecimento das atividades ainda nao vistas, a partir dos
    STREAMS guardados no Postgres.

    Corre a seguir ao lote normal, por isso o utilizador nunca precisa de
    chamar nada a mao: as sessoes novas entram sozinhas. E' idempotente --
    o que ja foi aceite ou rejeitado nao volta a ser analisado.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'utils'))
        import aquecimento_db as aq_db
        import aquecimento_streams as aqs
        import aquecimento_analyzer as aa
        import db as _db
        from config import TYPE_MAP
    except Exception as e:
        # nao engolir: sem isto o lote reporta 0/0 sem dizer porque
        msg = f"{type(e).__name__}: {e}"
        print(f"[AQUECIMENTO] varrimento indisponivel: {msg}")
        return {'novos': 0, 'analisadas': 0, 'erro_import': msg}

    try:
        linhas = _db._exec(
            """SELECT id, date, type FROM activities
               WHERE type IS NOT NULL
               ORDER BY date DESC LIMIT ?""", (limite,), fetch='all') or []
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[AQUECIMENTO] query de atividades falhou: {msg}")
        return {'novos': 0, 'analisadas': 0, 'erro_query': msg}

    novos, vistas, sem_streams = 0, 0, 0
    for aid, data, tipo in linhas:
        mod = TYPE_MAP.get(tipo, tipo)
        if mod not in aa.PROTOCOLOS:
            continue
        aid = str(aid)
        try:
            if aq_db.ja_analisada(aid, aqs.VERSAO_DETECTOR):
                continue
            streams, _m = _db.get_streams(aid)
            if not streams:
                sem_streams += 1
                continue
            vistas += 1
            data_iso = str(data)[:10] if data else None
            dur = None
            try:
                d = _db._exec("""SELECT COALESCE(elapsed_time, moving_time)
                                 FROM activities WHERE id = ?""", (aid,), fetch='one')
                dur = float(d[0]) if d and d[0] else None
            except Exception:
                pass
            r = aqs.analisar_streams(streams, mod, aa.PROTOCOLOS, duracao_s=dur)
            if r.get('detectado'):
                aq_db.salvar_blocos(aid, mod, data_iso, r['blocos'], sync=False)
                novos += 1
            else:
                aq_db.marcar_rejeitada(aid, mod, data_iso,
                                       r.get('motivo', 'desconhecido'),
                                       versao=aqs.VERSAO_DETECTOR)
        except Exception as e:
            print(f"[AQUECIMENTO] {aid}: {type(e).__name__}: {e}")

    return {'novos': novos, 'analisadas': vistas,
            'sem_streams_guardados': sem_streams}


def _sync_aquecimento():
    """Envia a BD de aquecimento para o Drive uma unica vez, no fim do lote."""
    try:
        import sys, os
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'utils'))
        import aquecimento_db as aq_db
        return aq_db.sincronizar()
    except Exception as e:
        print(f"[AQUECIMENTO] sync final falhou: {type(e).__name__}: {e}")
        return False


def processar_lote(n=10, retornar_resumo=True):
    """Processa lote de atividades com Fase B + Aquecimento."""
    try:
        import drive_db_fisiologia as ddf
        conn = ddf.get_conn()
    except Exception as e:
        return {'status': 'erro', 'mensagem': str(e)}
    
    # Auto-migração
    colunas_novas = _garantir_colunas(conn)
    
    # Buscar últimas atividades
    atividades = conn.execute("""
        SELECT activity_id, modalidade, MAX(data) AS data
        FROM fisiologia_intervalos
        WHERE valido=1
        GROUP BY activity_id, modalidade
        ORDER BY data DESC
        LIMIT ?
    """, (n,)).fetchall()
    
    processadas = 0
    total_intervalos = 0
    aquecimentos_detectados = 0
    erros = 0
    detalhes = []
    
    for (activity_id, modalidade, data_atividade) in atividades:
        try:
            # FASE B: processar min/avg/max
            intervalos = conn.execute("""
                SELECT 
                    interval_num,
                    hr_max_60s, hr_avg_60s, hr_min_60s,
                    resp_avg_60s, resp_min_60s, resp_max_60s,
                    smo2_min_60s, smo2_avg_60s, smo2_max_60s,
                    thb_medio_work as thb_avg_60s,
                    dfa1_clean as dfa1_avg_60s
                FROM fisiologia_intervalos 
                WHERE activity_id=? AND valido=1
                ORDER BY interval_num
            """, (activity_id,)).fetchall()
            
            gravados = 0
            for intervalo_row in intervalos:
                interval_num = intervalo_row[0]
                
                hr_max = intervalo_row[1]
                hr_avg = intervalo_row[2]
                hr_min = intervalo_row[3]
                
                resp_avg = intervalo_row[4]
                resp_min = intervalo_row[5]
                resp_max = intervalo_row[6]
                
                smo2_min = intervalo_row[7]
                smo2_avg = intervalo_row[8]
                smo2_max = intervalo_row[9]
                
                thb_avg = intervalo_row[10]
                dfa1_avg = intervalo_row[11]
                
                updates = []
                values = []
                
                # HR
                if hr_min is not None:
                    updates.append("hr_min_60s=?")
                    values.append(hr_min)
                if hr_avg is not None:
                    updates.append("hr_avg_60s=?")
                    values.append(hr_avg)
                if hr_max is not None:
                    updates.append("hr_max_60s=?")
                    values.append(hr_max)
                
                # Resp
                if resp_min is not None:
                    updates.append("resp_min_60s=?")
                    values.append(resp_min)
                if resp_avg is not None:
                    updates.append("resp_avg_60s=?")
                    values.append(resp_avg)
                if resp_max is not None:
                    updates.append("resp_max_60s=?")
                    values.append(resp_max)
                
                # SmO2
                if smo2_min is not None:
                    updates.append("smo2_min_60s=?")
                    values.append(smo2_min)
                if smo2_avg is not None:
                    updates.append("smo2_avg_60s=?")
                    values.append(smo2_avg)
                if smo2_max is not None:
                    updates.append("smo2_max_60s=?")
                    values.append(smo2_max)
                
                # tHb
                if thb_avg is not None:
                    updates.append("thb_avg_60s=?")
                    values.append(thb_avg)
                    # Estimar min/max
                    if not conn.execute("SELECT thb_min_60s FROM fisiologia_intervalos WHERE activity_id=? AND interval_num=?", 
                                       (activity_id, interval_num)).fetchone()[0]:
                        updates.append("thb_min_60s=?")
                        values.append(thb_avg * 0.95)
                        updates.append("thb_max_60s=?")
                        values.append(thb_avg * 1.05)
                
                # DFA-α1
                if dfa1_avg is not None:
                    updates.append("dfa1_avg_60s=?")
                    values.append(dfa1_avg)
                
                if updates:
                    sql = f"UPDATE fisiologia_intervalos SET {', '.join(updates)} WHERE activity_id=? AND interval_num=?"
                    values.extend([activity_id, interval_num])
                    conn.execute(sql, values)
                    gravados += 1
            
            conn.commit()
            processadas += 1
            total_intervalos += gravados
            
            # NOVO: AQUECIMENTO — processar automaticamente
            aq_detectado = _processar_aquecimento(conn, activity_id, modalidade, data_atividade)
            if aq_detectado:
                aquecimentos_detectados += 1
            
            detalhes.append({
                'activity_id': activity_id,
                'modalidade': modalidade,
                'intervalos_gravados': gravados,
                'aquecimento_detectado': aq_detectado,
                'status': 'ok'
            })
        
        except Exception as e:
            erros += 1
            detalhes.append({
                'activity_id': activity_id,
                'erro': str(e),
                'status': 'erro'
            })
    
    if retornar_resumo:
        return {
            'status': 'lote_concluido',
            'processadas': processadas,
            'total_intervalos': total_intervalos,
            'aquecimentos_detectados': aquecimentos_detectados,
            'aquecimento_historico': _varrer_aquecimento_pendente(conn),
            'aquecimento_sincronizado': _sync_aquecimento(),
            'erros': erros,
            'colunas_migradas': colunas_novas,
            'detalhes': detalhes,
        }
    
    return {'status': 'ok', 'processadas': processadas}

if __name__ == '__main__':
    resultado = processar_lote(300)
    import json
    print(json.dumps(resultado, indent=2))
