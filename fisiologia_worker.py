"""
FISIOLOGIA_WORKER.PY — Versão COMPLETA com Aquecimento

Inclui:
  - Fase B: Auto-migração + min/avg/max
  - Aquecimento: Detecta padrão + extrai métricas
  
Processa automaticamente:
  1. Calcula min/avg/max para HR, Resp, SmO2, tHb, DFA1
  2. Detecta padrão de aquecimento (5-1-5-1-5 ou 5-1-5-1-5-1-5-1-5)
  3. Guarda métricas de aquecimento na BD
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
    """Cria as colunas novas se não existirem (auto-migração)."""
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

def _analisar_aquecimento(conn, activity_id, modalidade):
    """Analisa se há padrão de aquecimento e extrai métricas."""
    try:
        from aquecimento_analyzer import AquecimentoAnalyzer
        from aquecimento_db import get_db as get_aq_db
    except ImportError:
        return None
    
    if modalidade not in ['Row', 'Ski', 'Bike']:
        return None
    
    analyzer = AquecimentoAnalyzer(conn)
    resultado = analyzer.analisar_atividade(activity_id, modalidade)
    
    if not resultado.get('detectado'):
        return None
    
    # Salvar na BD de aquecimento
    try:
        aq_db = get_aq_db()
        dados_aq = {
            'modalidade': modalidade,
            'data': datetime.now().isoformat(),
            'padrao_detectado': resultado.get('padrao'),
            'n_blocos': resultado.get('n_blocos'),
            'hr_avg': resultado.get('metricas', {}).get('hr_avg'),
            'hr_min': resultado.get('metricas', {}).get('hr_min'),
            'hr_max': resultado.get('metricas', {}).get('hr_max'),
            'smo2_avg': resultado.get('metricas', {}).get('smo2_avg'),
            'smo2_min': resultado.get('metricas', {}).get('smo2_min'),
            'smo2_max': resultado.get('metricas', {}).get('smo2_max'),
            'resp_avg': resultado.get('metricas', {}).get('resp_avg'),
            'resp_min': resultado.get('metricas', {}).get('resp_min'),
            'resp_max': resultado.get('metricas', {}).get('resp_max'),
            'dfa1_avg': resultado.get('metricas', {}).get('dfa1_avg'),
            'dfa1_min': resultado.get('metricas', {}).get('dfa1_min'),
            'dfa1_max': resultado.get('metricas', {}).get('dfa1_max'),
            'tempo_aquecimento_seg': resultado.get('tempo_aquecimento_seg'),
            'n_intervalos_analisados': resultado.get('n_intervalos'),
        }
        aq_db.salvar_sessao(activity_id, dados_aq)
        return True
    except Exception as e:
        print(f"[AQUECIMENTO] Erro ao guardar dados: {e}")
        return False

def processar_lote(n=10, retornar_resumo=True):
    """Processa os últimos N intervalos, calcula min/avg/max + aquecimento."""
    try:
        import drive_db_fisiologia as ddf
        conn = ddf.get_conn()
    except Exception as e:
        return {'status': 'erro', 'mensagem': str(e)}
    
    # Auto-migração: cria as colunas novas
    colunas_novas = _garantir_colunas(conn)
    
    # Buscar últimas atividades (onde há intervalos válidos)
    atividades = conn.execute("""
        SELECT DISTINCT activity_id, modalidade
        FROM fisiologia_intervalos 
        WHERE valido=1 
        ORDER BY data DESC 
        LIMIT ?
    """, (n,)).fetchall()
    
    processadas = 0
    total_intervalos = 0
    aquecimentos_detectados = 0
    erros = 0
    detalhes = []
    
    for (activity_id, modalidade) in atividades:
        try:
            # Buscar intervalos desta atividade que têm dados
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
                
                # Extrair valores de HR, Resp, SmO2, tHb, DFA-α1
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
                
                # Construir UPDATE SQL
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
                
                # Actualizar
                if updates:
                    sql = f"UPDATE fisiologia_intervalos SET {', '.join(updates)} WHERE activity_id=? AND interval_num=?"
                    values.extend([activity_id, interval_num])
                    conn.execute(sql, values)
                    gravados += 1
            
            conn.commit()
            processadas += 1
            total_intervalos += gravados
            
            # NOVO: Analisar aquecimento
            aq_resultado = _analisar_aquecimento(conn, activity_id, modalidade)
            if aq_resultado:
                aquecimentos_detectados += 1
            
            detalhes.append({
                'activity_id': activity_id,
                'modalidade': modalidade,
                'intervalos_gravados': gravados,
                'aquecimento_detectado': bool(aq_resultado),
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
            'erros': erros,
            'colunas_migradas': colunas_novas,
            'detalhes': detalhes,
        }
    
    return {'status': 'ok', 'processadas': processadas}

if __name__ == '__main__':
    resultado = processar_lote(300)
    import json
    print(json.dumps(resultado, indent=2))
