"""
FISIOLOGIA_WORKER.PY — Fase B COMPLETO
Calcula min/avg/max para TODAS as 5 métricas (HR, Resp, SmO2, tHb, DFA-α1)
"""

import numpy as np
import sqlite3
from datetime import datetime, timedelta

# Assumindo estrutura: fisiologia_intervalos com colunas de stream


# Limite de atividades por requisição web
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

def _fmt_pace(segundos):
    """Segundos -> 'm:ss'. None se invalido."""
    if segundos is None or segundos <= 0 or segundos > 3600:
        return None
    try:
        segundos = float(segundos)
        if not np.isfinite(segundos):
            return None
        return f'{int(segundos // 60)}:{int(segundos % 60):02d}'
    except (TypeError, ValueError):
        return None

def _velocidade_do_intervalo(item):
    """Extrai velocidade (m/s) do item da API."""
    try:
        v = item.get('velocity_data', {})
        if isinstance(v, dict):
            velocidade = v.get('average', 0)
        else:
            velocidade = float(v) if v else 0
        if 0.5 <= velocidade <= 15:  # 0.5-15 m/s é razoável
            return velocidade
    except (TypeError, ValueError, AttributeError):
        pass
    return None

def _pace_s_km(velocidade_ms):
    """Converte m/s -> segundos/km (genérico para Row/Ski/Run)."""
    if velocidade_ms is None or velocidade_ms <= 0:
        return None
    try:
        return 1000.0 / float(velocidade_ms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None

def _resumo_janela_60s(t_arr, v_arr, tipo_metrica='hr', janela_ma_s=60):
    """Calcula min/avg/max dos ÚLTIMOS 60 segundos de um array de métricas.
    
    Resultado: {'min': X, 'avg': Y, 'max': Z}
    """
    if len(t_arr) == 0 or len(v_arr) == 0:
        return None
    
    try:
        t_arr = np.array(t_arr, dtype=float)
        v_arr = np.array(v_arr, dtype=float)
        
        if len(t_arr) != len(v_arr):
            return None
        
        # Ultimos 60s
        t_max = t_arr[-1]
        t_min_janela = t_max - janela_ma_s
        
        mask = t_arr >= t_min_janela
        v_janela = v_arr[mask]
        
        if len(v_janela) == 0:
            return None
        
        # Filtrar NaN/inf
        v_validos = v_janela[np.isfinite(v_janela)]
        if len(v_validos) == 0:
            return None
        
        return {
            'min': float(np.min(v_validos)),
            'avg': float(np.mean(v_validos)),
            'max': float(np.max(v_validos)),
        }
    except (ValueError, TypeError, IndexError):
        return None

def _extrair_metricas_60s(streams_dict, modalidade='Row'):
    """Extrai min/avg/max para HR, Resp, SmO2, tHb, DFA-α1 dos últimos 60s.
    
    Retorna:
    {
        'hr_min_60s': X, 'hr_avg_60s': X, 'hr_max_60s': X,
        'resp_min_60s': X, 'resp_avg_60s': X, 'resp_max_60s': X,
        'smo2_min_60s': X, 'smo2_avg_60s': X, 'smo2_max_60s': X,
        'thb_min_60s': X, 'thb_avg_60s': X, 'thb_max_60s': X,
        'dfa1_min_60s': X, 'dfa1_avg_60s': X, 'dfa1_max_60s': X,
        'dfa1_clean': X  # mantém compatibilidade
    }
    """
    resultado = {}
    
    # HR — sempre tem
    hr_stream = streams_dict.get('heart_rate', {})
    if hr_stream:
        t_hr = hr_stream.get('time', [])
        v_hr = hr_stream.get('values', [])
        resumo_hr = _resumo_janela_60s(t_hr, v_hr, 'hr')
        if resumo_hr:
            resultado['hr_min_60s'] = resumo_hr['min']
            resultado['hr_avg_60s'] = resumo_hr['avg']
            resultado['hr_max_60s'] = resumo_hr['max']
    
    # Respiração
    resp_stream = streams_dict.get('respiration_rate', {})
    if resp_stream:
        t_resp = resp_stream.get('time', [])
        v_resp = resp_stream.get('values', [])
        resumo_resp = _resumo_janela_60s(t_resp, v_resp, 'resp')
        if resumo_resp:
            resultado['resp_min_60s'] = resumo_resp['min']
            resultado['resp_avg_60s'] = resumo_resp['avg']
            resultado['resp_max_60s'] = resumo_resp['max']
    
    # SmO2
    smo2_stream = streams_dict.get('smo2', {})
    if smo2_stream:
        t_smo2 = smo2_stream.get('time', [])
        v_smo2 = smo2_stream.get('values', [])
        resumo_smo2 = _resumo_janela_60s(t_smo2, v_smo2, 'smo2')
        if resumo_smo2:
            resultado['smo2_min_60s'] = resumo_smo2['min']
            resultado['smo2_avg_60s'] = resumo_smo2['avg']
            resultado['smo2_max_60s'] = resumo_smo2['max']
    
    # tHb
    thb_stream = streams_dict.get('thb', {})
    if thb_stream:
        t_thb = thb_stream.get('time', [])
        v_thb = thb_stream.get('values', [])
        resumo_thb = _resumo_janela_60s(t_thb, v_thb, 'thb')
        if resumo_thb:
            resultado['thb_min_60s'] = resumo_thb['min']
            resultado['thb_avg_60s'] = resumo_thb['avg']
            resultado['thb_max_60s'] = resumo_thb['max']
    
    # DFA-α1
    dfa1_stream = streams_dict.get('dfa1', {})
    if dfa1_stream:
        t_dfa = dfa1_stream.get('time', [])
        v_dfa = dfa1_stream.get('values', [])
        resumo_dfa = _resumo_janela_60s(t_dfa, v_dfa, 'dfa1')
        if resumo_dfa:
            resultado['dfa1_min_60s'] = resumo_dfa['min']
            resultado['dfa1_avg_60s'] = resumo_dfa['avg']
            resultado['dfa1_max_60s'] = resumo_dfa['max']
        # Manter compatibilidade: usar avg como dfa1_clean
        if 'dfa1_avg_60s' in resultado:
            resultado['dfa1_clean'] = resultado['dfa1_avg_60s']
    
    return resultado

def _garantir_colunas(conn):
    """Cria as colunas novas se não existirem (auto-migração)."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(fisiologia_intervalos)")}
    
    para_criar = COLUNAS_EXTRA - existing
    
    for col in para_criar:
        try:
            conn.execute(f"ALTER TABLE fisiologia_intervalos ADD COLUMN {col} REAL DEFAULT NULL")
        except sqlite3.OperationalError as e:
            if 'duplicate column' not in str(e):
                print(f"Aviso: {col} — {e}")
    
    conn.commit()
    return list(para_criar)

def processar_lote(n=10, retornar_resumo=True):
    """Processa os últimos N intervalos, calcula min/avg/max."""
    try:
        import drive_db_fisiologia as ddf
        conn = ddf.get_conn()
    except Exception as e:
        return {'status': 'erro', 'mensagem': str(e)}
    
    # Auto-migração
    colunas_novas = _garantir_colunas(conn)
    
    # Buscar últimas atividades
    atividades = conn.execute("""
        SELECT DISTINCT activity_id, modalidade 
        FROM fisiologia_intervalos 
        WHERE valido=1 
        ORDER BY data DESC 
        LIMIT ?
    """, (n,)).fetchall()
    
    processadas = 0
    total_intervalos = 0
    erros = 0
    detalhes = []
    
    for activity in atividades:
        activity_id = activity[0]
        modalidade = activity[1]
        
        try:
            # Buscar streams desta atividade
            # (assumindo que estão em drive_db_fisiologia.get_streams)
            import drive_db_fisiologia as ddf
            streams_dict, meta = ddf.get_streams(activity_id)
            
            # Extrair métricas
            metricas = _extrair_metricas_60s(streams_dict, modalidade)
            
            # Buscar intervalos desta atividade
            intervalos = conn.execute("""
                SELECT interval_num FROM fisiologia_intervalos 
                WHERE activity_id=? AND valido=1
                ORDER BY interval_num
            """, (activity_id,)).fetchall()
            
            gravados = 0
            for intervalo in intervalos:
                interval_num = intervalo[0]
                
                # Actualizar BD com as métricas
                update_sql = "UPDATE fisiologia_intervalos SET "
                set_clauses = [f"{k}=?" for k in metricas.keys()]
                update_sql += ", ".join(set_clauses)
                update_sql += " WHERE activity_id=? AND interval_num=?"
                
                values = list(metricas.values()) + [activity_id, interval_num]
                conn.execute(update_sql, values)
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
