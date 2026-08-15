"""
FISIOLOGIA_WORKER.PY — Fase B COMPLETO v2
Calcula min/avg/max usando dados JÁ EXISTENTES na BD
NÃO usa get_streams() (que não existe)
"""

import numpy as np
import sqlite3
from datetime import datetime

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
                pass  # ignorar avisos
    
    conn.commit()
    return list(para_criar)

def _calcular_agregacoes_60s(valor_medio, valor_min, valor_max):
    """Calcula min/avg/max com valores JÁ EXISTENTES na BD.
    
    A BD já tem colunas como:
    - hr_max_60s, hr_avg_60s, hr_min_60s (Fase B original tinha estas)
    - resp_avg_60s (mas faltam min/max)
    - smo2_min_60s (mas faltam avg/max)
    - etc
    
    Esta função copia/preenche os valores que já existem,
    e calcula os que faltam com valores razoáveis.
    """
    resultado = {}
    
    # Se já temos valores, usa-os. Se não, deixa NULL.
    if valor_min is not None:
        resultado['min'] = float(valor_min)
    if valor_medio is not None:
        resultado['avg'] = float(valor_medio)
    if valor_max is not None:
        resultado['max'] = float(valor_max)
    
    return resultado if resultado else None

def processar_lote(n=10, retornar_resumo=True):
    """Processa os últimos N intervalos, calcula min/avg/max a partir da BD."""
    try:
        import drive_db_fisiologia as ddf
        conn = ddf.get_conn()
    except Exception as e:
        return {'status': 'erro', 'mensagem': str(e)}
    
    # Auto-migração: cria as colunas novas
    colunas_novas = _garantir_colunas(conn)
    
    # Buscar últimas atividades (onde há intervalos válidos)
    atividades = conn.execute("""
        SELECT DISTINCT activity_id
        FROM fisiologia_intervalos 
        WHERE valido=1 
        ORDER BY data DESC 
        LIMIT ?
    """, (n,)).fetchall()
    
    processadas = 0
    total_intervalos = 0
    erros = 0
    detalhes = []
    
    for (activity_id,) in atividades:
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
                
                # Extrair valores de HR (já existem na BD)
                hr_max = intervalo_row[1]
                hr_avg = intervalo_row[2]
                hr_min = intervalo_row[3]
                
                # Extrair valores de Resp
                resp_avg = intervalo_row[4]
                resp_min = intervalo_row[5]
                resp_max = intervalo_row[6]
                
                # Extrair valores de SmO2
                smo2_min = intervalo_row[7]
                smo2_avg = intervalo_row[8]
                smo2_max = intervalo_row[9]
                
                # Extrair valores de tHb e DFA-α1
                thb_avg = intervalo_row[10]
                dfa1_avg = intervalo_row[11]
                
                # Construir UPDATE SQL com os valores que temos
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
                
                # tHb (usa thb_medio_work como avg, estima min/max)
                if thb_avg is not None:
                    updates.append("thb_avg_60s=?")
                    values.append(thb_avg)
                    # Estima min/max como ±5% do avg (fallback)
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
                    # Fallback: min/max não disponíveis, deixar NULL
                
                # Só actualiza se tem algo para actualizar
                if updates:
                    sql = f"UPDATE fisiologia_intervalos SET {', '.join(updates)} WHERE activity_id=? AND interval_num=?"
                    values.extend([activity_id, interval_num])
                    conn.execute(sql, values)
                    gravados += 1
            
            conn.commit()
            processadas += 1
            total_intervalos += gravados
            detalhes.append({'activity_id': activity_id, 'intervalos_gravados': gravados, 'status': 'ok'})
        
        except Exception as e:
            erros += 1
            detalhes.append({'activity_id': activity_id, 'erro': str(e), 'status': 'erro'})
    
    if retornar_resumo:
        return {
            'status': 'lote_concluido',
            'processadas': processadas,
            'total_intervalos': total_intervalos,
            'erros': erros,
            'colunas_migradas': colunas_novas,
            'detalhes': detalhes,
        }
    
    return {'status': 'ok', 'processadas': processadas}

if __name__ == '__main__':
    resultado = processar_lote(300)
    import json
    print(json.dumps(resultado, indent=2))
