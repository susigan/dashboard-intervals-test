"""
fisiologia_worker_v2.py — Análise robusta de intervalos com detecção de artefatos.

NOVO APPROACH:
  1. Valida intervalo (≥60s, watts estável ±20W)
  2. Detecta/remove artefatos (quedas bruscas, picos)
  3. Calcula moving averages (30s, 60s)
  4. Extrai MAX HR, AVG respiração, MIN SMO2, DFA-α1 normalizado
  5. Grava em fisiologia_perfil.db (SQLite, Drive)
"""

import numpy as np
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='[%(name)s] %(message)s')
logger = logging.getLogger('fisiologia_worker')

import drive_db_fisiologia as ddf

# ── Migração automática ────────────────────────────────────────────────────
def _migrar_v2():
    """Cria as colunas novas para análise robusta."""
    try:
        conn = ddf.get_conn()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(fisiologia_intervalos)")
        colunas_existentes = {row[1] for row in cur.fetchall()}
        
        colunas_novas = [
            ('hr_max_60s', 'REAL'),
            ('hr_avg_60s', 'REAL'),
            ('hr_min_60s', 'REAL'),
            ('resp_avg_60s', 'REAL'),
            ('smo2_min_60s', 'REAL'),
            ('dfa1_clean', 'REAL'),
            ('intervalo_valido_analise', 'INTEGER'),
        ]
        
        para_adicionar = [c for c, _ in colunas_novas if c not in colunas_existentes]
        if para_adicionar:
            logger.info(f"Adicionando {len(para_adicionar)} colunas v2...")
            for col, tipo in colunas_novas:
                if col in para_adicionar:
                    cur.execute(f"ALTER TABLE fisiologia_intervalos ADD COLUMN {col} {tipo}")
                    logger.info(f"  ✓ {col}")
            conn.commit()
    except Exception as e:
        logger.error(f"ERRO na migração v2: {e}")
        raise
    finally:
        if conn:
            conn.close()

_migrar_v2()

# ── Detecção e remoção de artefatos ─────────────────────────────────────────
def _remover_artefatos(tempos, valores, tipo_metrica='hr', verbose=False):
    """
    Remove artefatos (quedas bruscas, picos) de uma série temporal.
    
    Args:
        tempos: array de timestamps (segundos)
        valores: array de valores (HR, DFA1, etc)
        tipo_metrica: 'hr', 'dfa1', 'smo2'
        
    Returns:
        valores_limpos: array com artefatos removidos/interpolados
    """
    if not len(valores) or not np.any(np.isfinite(valores)):
        return valores
    
    valores = np.array(valores, dtype=float)
    limpos = valores.copy()
    
    # Detectar outliers baseado no tipo
    if tipo_metrica == 'hr':
        # HR: queda >30 bpm em <5s é fault
        for i in range(1, len(valores)):
            if np.isfinite(valores[i]) and np.isfinite(valores[i-1]):
                delta = abs(valores[i] - valores[i-1])
                if delta > 30:  # queda brusca
                    limpos[i] = np.nan  # marca como inválido
                    if verbose:
                        logger.info(f"  Artefato HR detectado em t={tempos[i]:.1f}s (delta={delta:.1f})")
    
    elif tipo_metrica == 'dfa1':
        # DFA-α1: queda >0.5 em <5s é fault
        for i in range(1, len(valores)):
            if np.isfinite(valores[i]) and np.isfinite(valores[i-1]):
                delta = abs(valores[i] - valores[i-1])
                if delta > 0.5:
                    limpos[i] = np.nan
                    if verbose:
                        logger.info(f"  Artefato DFA-α1 detectado em t={tempos[i]:.1f}s (delta={delta:.3f})")
    
    # Interpolar gaps pequenos (≤3 pontos)
    for i in range(len(limpos)):
        if not np.isfinite(limpos[i]):
            vizinhos = []
            for j in range(max(0, i-3), min(len(limpos), i+4)):
                if j != i and np.isfinite(limpos[j]):
                    vizinhos.append(limpos[j])
            if vizinhos:
                limpos[i] = np.mean(vizinhos)
    
    return limpos


def _moving_average(valores, janela_s, tempos):
    """
    Calcula moving average de uma série com janela em segundos.
    
    Args:
        valores: array de valores
        janela_s: tamanho da janela em segundos
        tempos: array de timestamps (segundos)
        
    Returns:
        ma: moving average (mesmo tamanho de valores)
    """
    if len(valores) < 2:
        return valores
    
    # Calcular intervalo médio entre pontos
    dt_medio = (tempos[-1] - tempos[0]) / (len(tempos) - 1) if len(tempos) > 1 else 1
    n_pontos = max(1, int(janela_s / dt_medio))
    
    ma = np.convolve(valores, np.ones(n_pontos)/n_pontos, mode='same')
    return ma


def _extrair_metricas_60s(t_arr, hr_arr, resp_arr, smo2_arr, dfa1_arr):
    """
    Extrai métricas dos últimos 60s de um intervalo (limpo de artefatos).
    
    Returns:
        dict com: hr_max, hr_avg, hr_min, resp_avg, smo2_min, dfa1_clean
    """
    resultado = {
        'hr_max_60s': None,
        'hr_avg_60s': None,
        'hr_min_60s': None,
        'resp_avg_60s': None,
        'smo2_min_60s': None,
        'dfa1_clean': None,
    }
    
    if len(t_arr) < 2:
        return resultado
    
    # Últimos 60 segundos
    t_inicio_60s = max(t_arr[0], t_arr[-1] - 60)
    mask_60s = t_arr >= t_inicio_60s
    
    # HR: clean + moving avg
    if np.any(mask_60s) and len(hr_arr) > 0:
        hr_60s = hr_arr[mask_60s]
        hr_limpo = _remover_artefatos(t_arr[mask_60s], hr_60s, tipo_metrica='hr')
        hr_ma = _moving_average(hr_limpo, 5, t_arr[mask_60s])  # MA de 5s
        hr_valido = hr_ma[np.isfinite(hr_ma)]
        if len(hr_valido) > 0:
            resultado['hr_max_60s'] = float(np.nanmax(hr_ma))
            resultado['hr_avg_60s'] = float(np.nanmean(hr_ma))
            resultado['hr_min_60s'] = float(np.nanmin(hr_ma))
    
    # Respiração: moving avg
    if np.any(mask_60s) and len(resp_arr) > 0:
        resp_60s = resp_arr[mask_60s]
        resp_limpo = _remover_artefatos(t_arr[mask_60s], resp_60s, tipo_metrica='hr')
        resp_ma = _moving_average(resp_limpo, 5, t_arr[mask_60s])
        resp_valido = resp_ma[np.isfinite(resp_ma)]
        if len(resp_valido) > 0:
            resultado['resp_avg_60s'] = float(np.nanmean(resp_ma))
    
    # SMO2: mínimo de todo o intervalo
    if len(smo2_arr) > 0:
        smo2_valido = smo2_arr[np.isfinite(smo2_arr)]
        if len(smo2_valido) > 0:
            resultado['smo2_min_60s'] = float(np.nanmin(smo2_valido))
    
    # DFA-α1: limpar + normalizar
    if len(dfa1_arr) > 0:
        dfa1_limpo = _remover_artefatos(t_arr, dfa1_arr, tipo_metrica='dfa1')
        dfa1_ma = _moving_average(dfa1_limpo, 10, t_arr)  # MA de 10s para suavizar
        dfa1_valido = dfa1_ma[np.isfinite(dfa1_ma)]
        if len(dfa1_valido) > 0:
            resultado['dfa1_clean'] = float(np.nanmedian(dfa1_valido))
    
    return resultado


# ── Validação de intervalo ──────────────────────────────────────────────────
def _validar_intervalo(t_arr, watts_arr, dur_work_s):
    """
    Valida se intervalo é adequado para análise.
    
    Critérios:
      - ≥60 segundos de dados
      - Watts estável (p50 ±20W durante 60s)
      
    Returns:
        (válido: bool, motivo: str)
    """
    if dur_work_s < 60:
        return False, f"Intervalo curto ({dur_work_s}s < 60s)"
    
    # Watts estável?
    watts_limpos = watts_arr[np.isfinite(watts_arr)]
    if len(watts_limpos) > 0:
        watts_p50 = np.percentile(watts_limpos, 50)
        watts_range = np.percentile(watts_limpos, [25, 75])
        if watts_range[1] - watts_range[0] > 40:
            return False, f"Watts instável (IQR={watts_range[1]-watts_range[0]:.0f}W)"
    
    return True, "OK"


# ── Exportar função para fisiologia_worker antigo usar ──────────────────────
def analisar_intervalo_v2(t_arr, hr_arr, resp_arr, smo2_arr, dfa1_arr, 
                          watts_arr, dur_work_s):
    """
    Interface para chamar desde fisiologia_worker.py antigo.
    
    Returns:
        dict com todas as métricas extraídas
    """
    válido, motivo = _validar_intervalo(t_arr, watts_arr, dur_work_s)
    
    resultado = {
        'intervalo_valido_analise': 1 if válido else 0,
        'motivo_validacao': motivo,
    }
    
    if válido:
        metricas = _extrair_metricas_60s(t_arr, hr_arr, resp_arr, smo2_arr, dfa1_arr)
        resultado.update(metricas)
    
    return resultado
