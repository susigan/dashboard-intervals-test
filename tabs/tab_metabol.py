"""tab_metabol.py — CORRIGIDO: apenas campos que existem na BD."""
from flask import jsonify, request
import numpy as np
import sqlite3
from datetime import datetime
import drive_db_fisiologia as ddf
from tabs.base import page
SLUG = 'metabol'
# APENAS os campos que REALMENTE existem na BD:
# hr_max_60s, hr_avg_60s (sem hr_min!)
# resp_avg_60s (sem resp_min, resp_max!)
# smo2_min_60s (sem smo2_max, smo2_avg!)
# dfa1_clean (apenas um valor)
METRICAS_BASE = ['hr', 'resp', 'smo2', 'dfa1']

# Metricas que so aparecem se a BD as tiver mesmo preenchidas.
# O RRa1 vem do dispositivo e nem todas as atividades o tem;
# oferece-lo sem dados dava graficos vazios.
METRICAS_OPCIONAIS = ['rra1']

AGREGACOES = ['min', 'avg', 'max']
# MAP REAL: só os que existem!
CAMPOS_DB = {
    'hr': {
        'max': 'hr_max_60s',
        'avg': 'hr_avg_60s',
        # 'min' NÃO EXISTE
    },
    'resp': {
        'avg': 'resp_avg_60s',
        # 'min', 'max' NÃO EXISTEM
    },
    'smo2': {
        'min': 'smo2_min_60s',
        # 'max', 'avg' NÃO EXISTEM
    },
    'dfa1': {
        'avg': 'dfa1_clean',
        # 'min', 'max' NÃO EXISTEM (é um único valor)
    },
}
# AGREGAÇÕES DISPONÍVEIS por métrica
AGREGACOES_VALIDAS = {
    'hr': ['max', 'avg'],
    'resp': ['avg'],
    'smo2': ['min'],
    'dfa1': ['avg'],
}
CORES_METAB = {
    'hr': '#E74C3C',
    'resp': '#1ABC9C',
    'smo2': '#F39C12',
    'dfa1': '#9B59B6',
}
LABELS_METAB = {
    'hr': 'HR (bpm)',
    'resp': 'Respiração (rpm)',
    'smo2': 'SmO₂ (%)',
    'dfa1': 'DFA-α1 (clean)',
    'rra1': 'RR α1',
}

# Nem todas as colunas *_60s foram preenchidas pelo pipeline: por exemplo
# smo2_avg_60s esta vazia, porque o worker le e escreve na MESMA coluna.
# Para cada metrica/agregacao tentam-se varias colunas, pela ordem indicada,
# e usa-se a primeira que exista E tenha dados.
# Para cada metrica/agregacao, as colunas candidatas em dois grupos:
#
#   equivalentes -- medem A MESMA grandeza, podem substituir-se sem mudar a
#                   interpretacao (media da janela vs media do bloco: ambas
#                   sao "o valor tipico durante o esforco")
#   aproximadas  -- medem OUTRA COISA. baseline e' o estado ANTES da
#                   transicao, nao o minimo do esforco; extremo e' o pico
#                   numa janela que entra no descanso, nao o maximo do
#                   bloco. Usa-se so se for pedido explicitamente, e a
#                   resposta di-lo, porque muda o significado do numero.
FALLBACKS_DB = {
    ('hr', 'avg'):   {'equivalentes': ['hr_avg_60s', 'hr_medio_work',
                                       'hr_plateau_work'], 'aproximadas': []},
    ('hr', 'min'):   {'equivalentes': ['hr_min_60s'],
                      'aproximadas': ['hr_baseline']},
    ('hr', 'max'):   {'equivalentes': ['hr_max_60s'],
                      'aproximadas': ['hr_extremo']},
    ('smo2', 'avg'): {'equivalentes': ['smo2_avg_60s', 'smo2_medio_work',
                                       'smo2_plateau_work'], 'aproximadas': []},
    ('smo2', 'min'): {'equivalentes': ['smo2_min_60s'],
                      'aproximadas': ['smo2_extremo']},
    ('smo2', 'max'): {'equivalentes': ['smo2_max_60s'],
                      'aproximadas': ['smo2_baseline']},
    ('resp', 'avg'): {'equivalentes': ['resp_avg_60s', 'resp_medio_work',
                                       'resp_plateau_work'], 'aproximadas': []},
    ('resp', 'min'): {'equivalentes': ['resp_min_60s'],
                      'aproximadas': ['resp_baseline']},
    ('resp', 'max'): {'equivalentes': ['resp_max_60s'],
                      'aproximadas': ['resp_extremo']},
    ('dfa1', 'avg'): {'equivalentes': ['dfa1_avg_60s', 'dfa1_clean',
                                       'dfa1_medio_work'], 'aproximadas': []},
    ('dfa1', 'min'): {'equivalentes': ['dfa1_min_60s'],
                      'aproximadas': ['dfa1_extremo']},
    ('dfa1', 'max'): {'equivalentes': ['dfa1_max_60s'],
                      'aproximadas': ['dfa1_baseline']},
    ('rra1', 'avg'): {'equivalentes': ['rra1_avg_60s', 'rra1_medio_work',
                                       'rra1_plateau_work'], 'aproximadas': []},
    ('rra1', 'min'): {'equivalentes': ['rra1_min_60s'],
                      'aproximadas': ['rra1_extremo']},
    ('rra1', 'max'): {'equivalentes': ['rra1_max_60s'],
                      'aproximadas': ['rra1_baseline']},
}

# Abaixo desta fracao do total, a coluna e' pouco representativa e vale a
# pena preferir uma equivalente com mais cobertura.
COBERTURA_MINIMA = 0.40

_COBERTURA_CACHE = {}


def _contar(conn, col, existentes, total):
    if col not in existentes:
        return None
    try:
        return conn.execute(
            f"SELECT COUNT({col}) FROM fisiologia_intervalos WHERE valido = 1"
        ).fetchone()[0]
    except Exception:
        return None


def coluna_com_dados(conn, metrica, agregacao, permitir_aproximadas=False):
    """Melhor coluna para esta metrica/agregacao.

    Escolhe pela COBERTURA, nao pela ordem: 'a primeira que tem algum dado'
    fazia o SmO2 medio sair de uma coluna com 16 valores em 677 (2,4%),
    quando havia uma equivalente com 384.

    A preferida e' a *_60s se tiver cobertura razoavel; abaixo disso usa-se
    a equivalente com mais dados. Colunas aproximadas ficam de fora salvo
    pedido explicito -- medem outra grandeza.
    """
    chave = (metrica, agregacao)
    cache = (chave, permitir_aproximadas)
    if cache in _COBERTURA_CACHE:
        return _COBERTURA_CACHE[cache]

    try:
        existentes = {r[1] for r in conn.execute(
            "PRAGMA table_info(fisiologia_intervalos)")}
        total = conn.execute(
            "SELECT COUNT(*) FROM fisiologia_intervalos WHERE valido = 1"
        ).fetchone()[0] or 0
    except Exception:
        existentes, total = set(), 0

    grupos = FALLBACKS_DB.get(chave, {})
    equivalentes = grupos.get('equivalentes', [])

    contagens = [(c, _contar(conn, c, existentes, total)) for c in equivalentes]
    contagens = [(c, n) for c, n in contagens if n]

    escolhida = None
    if contagens:
        preferida, n_pref = contagens[0]
        melhor, n_melhor = max(contagens, key=lambda x: x[1])
        # fica-se pela preferida se ela cobrir o suficiente
        if total and n_pref >= COBERTURA_MINIMA * total:
            escolhida = preferida
        else:
            escolhida = melhor

    if not escolhida and permitir_aproximadas:
        aprox = [(c, _contar(conn, c, existentes, total))
                 for c in grupos.get('aproximadas', [])]
        aprox = [(c, n) for c, n in aprox if n]
        if aprox:
            escolhida = max(aprox, key=lambda x: x[1])[0]

    _COBERTURA_CACHE[cache] = escolhida
    return escolhida


def agregacoes_com_dados(conn, metrica):
    return [a for a in AGREGACOES if coluna_com_dados(conn, metrica, a)]


CAMPOS_DB = {
    m: {a: f'{m}_{a}_60s' for a in AGREGACOES} for m in METRICAS_BASE
}

AGREGACOES_VALIDAS = {m: list(AGREGACOES) for m in METRICAS_BASE}

CORES_METAB = {
    'hr': '#E74C3C',
    'resp': '#1ABC9C',
    'smo2': '#F39C12',
    'thb': '#3498DB',
    'dfa1': '#9B59B6',
}
LABELS_AGREGACAO = {
    'min': 'Mín',
    'max': 'Máx',
    'avg': 'Méd',
}
def _conn():
    conn = ddf.get_conn()
    conn.row_factory = sqlite3.Row
    return conn
def _fmt_pace(segundos):
    """Segundos -> 'm:ss'. None se o valor nao fizer sentido."""
    if segundos is None:
        return None
    try:
        segundos = float(segundos)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(segundos) or segundos <= 0 or segundos > 3600:
        return None
    return f'{int(segundos // 60)}:{int(segundos % 60):02d}'
def _pace_da_faixa_concept2(watts_medio, modalidade):
    """Calcula pace usando FÓRMULA Concepto2 baseada em WATTS.
    
    Isto é mais fiável que API porque watts vêm do sensor directamente.
    Concepto2: pace(seg/500m) = 500 / ((watts/2.8)^(1/3))
    """
    if watts_medio is None or watts_medio <= 0:
        return None
    
    try:
        watts = float(watts_medio)
        if watts <= 0:
            return None
        # Fórmula Concepto2 para segundos por 500m
        pace_500m = 500.0 / ((watts / 2.8) ** (1.0/3.0))
        
        if not np.isfinite(pace_500m) or pace_500m <= 0 or pace_500m > 3600:
            return None
        
        txt = _fmt_pace(pace_500m)
        return f'{txt} /500m' if txt else None
    except (ValueError, ZeroDivisionError):
        return None
def _pace_da_faixa(pace_s_km_mediano, modalidade):
    """Formata o pace medido para a unidade convencional da modalidade.
    
    Row/Ski: usam FÓRMULA Concepto2 (watts → pace)
    Run: usa dados reais (API), com filtro de credibilidade
    Bike: sem pace
    """
    if modalidade in ('Row', 'Ski'):
        return None  # Será calculado via _pace_da_faixa_concept2() baseado em watts
    
    if modalidade == 'Run':
        if pace_s_km_mediano is None:
            return None
        try:
            segundos = float(pace_s_km_mediano)
        except (TypeError, ValueError):
            return None
        
        # Filtro: pace >= 120s/km (2:00) é válido; < 120s é error
        if not np.isfinite(segundos) or segundos < 120 or segundos > 3600:
            return None
        
        txt = _fmt_pace(segundos)
        return f'{txt} /km' if txt else None
    
    return None
def cobertura_metricas(detalhe=True):
    """Que coluna alimenta cada metrica/agregacao, e com que cobertura."""
    conn = _conn()
    try:
        existentes = {r[1] for r in conn.execute(
            "PRAGMA table_info(fisiologia_intervalos)")}
        total = conn.execute(
            "SELECT COUNT(*) FROM fisiologia_intervalos WHERE valido = 1"
        ).fetchone()[0] or 0
    except Exception:
        existentes, total = set(), 0

    out = {'_total_intervalos': total}
    for m in list(METRICAS_BASE) + list(METRICAS_OPCIONAIS):
        out[m] = {}
        for a in AGREGACOES:
            grupos = FALLBACKS_DB.get((m, a), {})
            det = {}
            for tipo in ('equivalentes', 'aproximadas'):
                det[tipo] = {c: _contar(conn, c, existentes, total)
                             for c in grupos.get(tipo, [])}
            col = coluna_com_dados(conn, m, a)
            n = _contar(conn, col, existentes, total) if col else None
            pct = round(100.0 * n / total, 1) if (n and total) else 0
            out[m][a] = {
                'coluna_usada': col, 'n': n, 'cobertura_pct': pct,
                'preferida': (grupos.get('equivalentes') or [None])[0],
                'usou_alternativa': bool(
                    col and col != (grupos.get('equivalentes') or [None])[0]),
                'utilizavel': bool(col) and pct >= 20,
                'detalhe': det,
            }
    return out


def modalidades_disponiveis():
    conn = _conn()
    resultado = conn.execute("""
        SELECT modalidade, COUNT(*) as n, COUNT(DISTINCT data) as n_dias, 
               COUNT(DISTINCT activity_id) as n_atividades
        FROM fisiologia_intervalos
        WHERE valido = 1 AND watts_medio IS NOT NULL
        GROUP BY modalidade
        ORDER BY modalidade
    """).fetchall()
    return [
        {'modalidade': r['modalidade'], 'n': r['n'], 'n_dias': r['n_dias'], 'n_atividades': r['n_atividades']}
        for r in resultado
    ]
def perfil_por_modalidade(modalidade, campos_selecionados, min_n_total=15,
                          largura_bin_manual=50, so_estabilizados=False):
    """
    Perfil com PONDERAÇÃO.
    campos_selecionados: dict {metrica_base: agregacao}
    Ex: {'hr': 'max', 'resp': 'avg', 'smo2': 'min', 'dfa1': 'avg'}
    """
    conn = _conn()
    
    # VALIDAR que as agregações são válidas
    para_buscar, ignoradas = {}, {}
    for metrica_base, agregacao in campos_selecionados.items():
        if agregacao not in AGREGACOES:
            ignoradas[f'{metrica_base}_{agregacao}'] = 'agregacao desconhecida'
            continue
        coluna_db = coluna_com_dados(conn, metrica_base, agregacao)
        if not coluna_db:
            # a coluna existe mas esta vazia -> dizer, em vez de devolver nada
            ignoradas[f'{metrica_base}_{agregacao}'] = 'sem dados na BD'
            continue
        para_buscar[f'{metrica_base}_{agregacao}'] = coluna_db
    
    if not para_buscar:
        return {'status': 'erro',
                'mensagem': 'Nenhuma métrica com dados para esta selecção',
                'ignoradas': ignoradas}
    
    todas_colunas = set(['watts_medio', 'data', 'activity_id', 'interval_num'])
    # pace_s_km é opcional — criada pelo worker mas pode não existir ainda
    try:
        existentes = {r[1] for r in conn.execute("PRAGMA table_info(fisiologia_intervalos)")}
        if 'pace_s_km' in existentes:
            todas_colunas.add('pace_s_km')
    except:
        pass
    todas_colunas.update(para_buscar.values())
    colunas_str = ", ".join(todas_colunas)
    
    linhas = conn.execute(
        f"""SELECT {colunas_str}
           FROM fisiologia_intervalos
           WHERE modalidade = ? AND valido = 1 AND watts_medio IS NOT NULL
           ORDER BY data DESC, activity_id DESC, interval_num DESC""",
        (modalidade,)
    ).fetchall()
    if len(linhas) < min_n_total:
        return {'status': 'dados_insuficientes', 'modalidade': modalidade, 'n_disponivel': len(linhas)}
    n_linhas = len(linhas)
    corte = int(n_linhas * 0.3)
    pesos = np.ones(n_linhas)
    pesos[:corte] = 1.5   # linhas vem por data DESC: peso extra ao recente
    watts = np.array([l['watts_medio'] for l in linhas])

    # Outliers de potencia: no Run os watts sao estimados e um unico bloco a
    # 1900 W estica o eixo e esvazia o resto do grafico. Corta-se pelo metodo
    # de Tukey (1.5 x IQR), que nao assume distribuicao normal, e guarda-se
    # quantos sairam para o utilizador saber.
    q1, q3 = np.percentile(watts, [25, 75])
    iqr = q3 - q1
    lim_hi = q3 + 1.5 * iqr
    lim_lo = max(0, q1 - 1.5 * iqr)
    manter = (watts >= lim_lo) & (watts <= lim_hi)
    n_outliers = int((~manter).sum())
    watts_out = sorted({round(float(w)) for w in watts[~manter]}, reverse=True)[:5]
    if n_outliers and manter.sum() >= min_n_total:
        linhas = [l for l, k in zip(linhas, manter) if k]
        pesos = pesos[manter]
        watts = watts[manter]
        n_linhas = len(linhas)

    wmin, wmax = float(watts.min()), float(watts.max())
    # Gerar bins — APENAS até a última faixa com dados
    # Isto evita espaço vazio à direita (problema do Run)
    inicio = int(wmin // largura_bin_manual) * largura_bin_manual
    fim = int(wmax // largura_bin_manual) * largura_bin_manual  # sem +1
    if fim == inicio:
        fim += largura_bin_manual
    limites = list(np.arange(inicio, fim + largura_bin_manual, largura_bin_manual))
    faixas_saida = []
    for i in range(len(limites) - 1):
        lo, hi = limites[i], limites[i + 1]
        ultima = (i == len(limites) - 2)
        mask = (watts >= lo) & ((watts <= hi) if ultima else (watts < hi))
        idxs = np.where(mask)[0]
        if len(idxs) == 0:
            continue
        watts_centro = round((lo + hi) / 2, 1)
        faixa = {
            'faixa_watts': f'{lo:.0f}-{hi:.0f}W',
            'watts_min': round(float(lo), 1),
            'watts_max': round(float(hi), 1),
            'watts_centro': watts_centro,
            'n_intervalos': len(idxs),
        }
        
        # FASE A — pace por modalidade
        # Row/Ski: FÓRMULA Concepto2 (watts → pace), mais confiável
        # Run: dados API com FILTRO (pace >= 2:00/km)
        if modalidade in ('Row', 'Ski'):
            # Concepto2: pace = 500 / ((watts/2.8)^(1/3))
            pace_txt = _pace_da_faixa_concept2(watts_centro, modalidade)
            if pace_txt:
                faixa['pace_medio'] = pace_txt
        elif modalidade == 'Run':
            # Run: usar dados reais da API, filtrar valores inválidos
            paces = []
            for j in idxs:
                try:
                    v = linhas[j].get('pace_s_km') if hasattr(linhas[j], 'get') else linhas[j]['pace_s_km']
                except (IndexError, KeyError, TypeError, AttributeError):
                    v = None
                if v is not None and np.isfinite(v):
                    # Filtro: apenas pace >= 120s (>= 2:00/km)
                    if float(v) >= 120:
                        paces.append(float(v))
            if paces:
                try:
                    pace_mediano = float(np.median(paces))
                    pace_txt = _pace_da_faixa(pace_mediano, modalidade)
                    if pace_txt:
                        faixa['pace_medio'] = pace_txt
                        faixa['n_pace'] = len(paces)
                except (ValueError, TypeError):
                    pass
        
        # Para cada métrica selecionada (VALIDADA)
        for chave_unica, coluna_db in para_buscar.items():
            valores = [linhas[j][coluna_db] for j in idxs]
            pesos_faixa = pesos[idxs]
            
            vs_validos = []
            ps_validos = []
            for v, p in zip(valores, pesos_faixa):
                if v is not None and np.isfinite(v):
                    vs_validos.append(v)
                    ps_validos.append(p)
            
            if len(vs_validos) > 0:
                vs_arr = np.array(vs_validos)
                ps_arr = np.array(ps_validos)
                
                vs_sorted_idx = np.argsort(vs_arr)
                vs_sorted = vs_arr[vs_sorted_idx]
                ps_sorted = ps_arr[vs_sorted_idx]
                
                # Percentil ponderado: a posicao de cada valor e' o centro da
                # sua "fatia" de peso, nao o fim dela. A versao anterior usava
                # o acumulado simples, o que empurrava todos os percentis para
                # cima -- vies sistematico, pior quanto menos valores na faixa.
                ps_cum = (np.cumsum(ps_sorted) - 0.5 * ps_sorted) / np.sum(ps_sorted)

                def _pct(q):
                    if len(vs_sorted) == 1:
                        return float(vs_sorted[0])
                    return float(np.interp(q, ps_cum, vs_sorted))

                faixa[chave_unica] = {
                    'p10': round(_pct(0.10), 2),
                    'p25': round(_pct(0.25), 2),
                    'p50': round(_pct(0.50), 2),
                    'p75': round(_pct(0.75), 2),
                    'p90': round(_pct(0.90), 2),
                    'media': round(float(np.average(vs_arr, weights=ps_arr)), 2),
                    'n': len(vs_validos),
                }
        
        faixas_saida.append(faixa)
    return {
        'status': 'ok',
        'modalidade': modalidade,
        'n_intervalos_total': len(linhas),
        'campos_selecionados': campos_selecionados,
        'colunas_usadas': para_buscar,
        'ignoradas': ignoradas,
        'outliers_watts_removidos': n_outliers,
        'outliers_watts_valores': watts_out,
        'limite_outlier_watts': round(float(lim_hi), 1),
        'faixas': faixas_saida,
    }
def evolucao_temporal(modalidade, metrica, agregacao, watts_min=None,
                      watts_max=None, min_por_periodo=3, agrupar='mes'):
    """Evolucao temporal de uma metrica, por periodo.

    Correcoes face a versao anterior:
      - usa a coluna com mais cobertura (antes ia sempre a *_60s, que no SmO2
        medio tem 16 valores em 677)
      - a validacao da agregacao e' feita contra os dados, nao contra uma
        lista fixa que ja nao correspondia a' BD
      - o agrupamento e' escolhivel (semana / mes / trimestre / ano); antes
        era sempre mensal, independentemente do que se pedisse
      - periodos com poucas sessoes deixam de desaparecer em silencio: sao
        contados e devolvidos, para o utilizador saber que ha buracos
    """
    conn = _conn()

    coluna_db = coluna_com_dados(conn, metrica, agregacao)
    if not coluna_db:
        return {'status': 'erro',
                'mensagem': f'{metrica}/{agregacao} sem dados na BD'}

    cond = ["modalidade = ?", "valido = 1", f"{coluna_db} IS NOT NULL"]
    params = [modalidade]
    if watts_min is not None:
        cond.append("watts_medio >= ?")
        params.append(watts_min)
    if watts_max is not None:
        cond.append("watts_medio <= ?")
        params.append(watts_max)

    linhas = conn.execute(
        f"""SELECT data, watts_medio, {coluna_db} AS valor
            FROM fisiologia_intervalos
            WHERE {' AND '.join(cond)} ORDER BY data""",
        tuple(params)).fetchall()
    if not linhas:
        return {'status': 'dados_insuficientes', 'n_disponivel': 0,
                'coluna_usada': coluna_db}

    # outliers de potencia (Tukey): sem isto um bloco a 1900 W entra na media
    ws = np.array([l['watts_medio'] for l in linhas
                   if l['watts_medio'] is not None], dtype=float)
    n_out = 0
    if len(ws) >= 8:
        q1, q3 = np.percentile(ws, [25, 75])
        lim_hi = q3 + 1.5 * (q3 - q1)
        lim_lo = max(0, q1 - 1.5 * (q3 - q1))
        antes = len(linhas)
        linhas = [l for l in linhas
                  if l['watts_medio'] is None or lim_lo <= l['watts_medio'] <= lim_hi]
        n_out = antes - len(linhas)

    def chave(d):
        ano, mes = d[:4], d[5:7]
        if agrupar == 'ano':
            return ano
        if agrupar == 'trimestre':
            return f"{ano}-T{(int(mes) - 1) // 3 + 1}"
        if agrupar == 'semana':
            from datetime import date as _date
            iso = _date(int(ano), int(mes), int(d[8:10])).isocalendar()
            return f"{iso[0]}-S{iso[1]:02d}"
        return d[:7]

    grupos = {}
    for l in linhas:
        if not l['data']:
            continue
        try:
            grupos.setdefault(chave(l['data']), []).append(l['valor'])
        except Exception:
            continue

    saida, omitidos = [], 0
    for periodo in sorted(grupos):
        vs = [v for v in grupos[periodo] if v is not None and np.isfinite(v)]
        if len(vs) < min_por_periodo:
            omitidos += 1
            continue
        a = np.array(vs, dtype=float)
        saida.append({
            'periodo': periodo,
            'media': round(float(a.mean()), 2),
            'p10': round(float(np.percentile(a, 10)), 2),
            'p25': round(float(np.percentile(a, 25)), 2),
            'p50': round(float(np.percentile(a, 50)), 2),
            'p75': round(float(np.percentile(a, 75)), 2),
            'p90': round(float(np.percentile(a, 90)), 2),
            'n': len(vs),
        })

    return {
        'status': 'ok',
        'metrica': metrica,
        'agregacao': agregacao,
        'agrupar': agrupar,
        'coluna_usada': coluna_db,
        'e_alternativa': coluna_db != f'{metrica}_{agregacao}_60s',
        'outliers_watts_removidos': n_out,
        'periodos_omitidos': omitidos,
        'min_por_periodo': min_por_periodo,
        'n_total': sum(p['n'] for p in saida),
        'periodos': saida,
    }


BODY = r"""
<h1>Metabolismo</h1>
<div class="tabs" style="border-bottom:1px solid #21262d; margin-bottom:20px;">
  <button class="tab-btn active" data-tab="perfil_watts">Perfil por Watts</button>
  <button class="tab-btn" data-tab="aquecimento">Aquecimento</button>
  <button class="tab-btn" data-tab="perfilmet">Perfil Metabólico</button>
</div>
<div id="perfil_watts" class="tab-content active">
<div class="controls">
  <label class="sel">Modalidade
    <select id="modalidade"></select></label>
  <label class="sel">Bin size (watts)
    <select id="larguraBin">
      <option value="20">20W</option>
      <option value="50" selected>50W</option>
      <option value="100">100W</option>
    </select></label>
</div>
<div class="controls" id="agregacaoControls"></div>
<div class="controls" style="margin-bottom:4px;">
  <label class="sel" style="cursor:pointer;white-space:nowrap;">
    <input type="checkbox" id="soEstabilizados" style="vertical-align:middle;margin-right:4px;">
    <span style="vertical-align:middle;">Só blocos onde a métrica estabilizou</span></label>
  <span style="color:#8b949e;font-size:11px;margin-left:6px;">
    blocos curtos medem o caminho, não o efeito da potência</span>
  <span id="perfAviso" style="color:#8b949e;font-size:11px;margin-left:10px;"></span>
</div>
<h2>Perfil metabólico — ponderado (últimos 30% com 1.5x peso)</h2>
<div id="tooltip" style="position:absolute;background:#000;color:#fff;padding:8px;border-radius:3px;font-size:11px;display:none;z-index:1000;pointer-events:none;border:1px solid #666;white-space:nowrap;"></div>
<div class="legend" id="lgPerfil"></div>
<div class="chartbox">
  <canvas id="chPerfil" height="300"></canvas>
</div>
<h2>Evolução ao longo do tempo</h2>
<div class="controls">
  <label class="sel">Métrica
    <select id="metricaEvolucao"></select></label>
  <label class="sel">Agregação
    <select id="agregacaoEvolucao"></select></label>
  <label class="sel">Agrupar
    <select id="agruparEvolucao">
      <option value="semana">Semana</option>
      <option value="mes" selected>Mês</option>
      <option value="trimestre">Trimestre</option>
      <option value="ano">Ano</option>
    </select></label>
  <label class="sel">Watts min <input type="number" id="wattsMin" value="200" style="width:70px"></label>
  <label class="sel">Watts max <input type="number" id="wattsMax" value="350" style="width:70px"></label>
  <span id="evolAviso" style="color:#8b949e;font-size:11px;margin-left:8px;"></span>
  <button onclick="carregarEvolucao()">Actualizar</button>
  <span id="evolAviso" style="color:#8b949e;font-size:11px;margin-left:10px;"></span>
</div>
<div class="chartbox" style="position:relative;">
  <canvas id="chEvolucao" height="240"></canvas>
  <div id="evolTip" style="position:absolute;display:none;background:#0d1117;border:1px solid #30363d;
       border-radius:4px;padding:7px 9px;font-size:11px;color:#c9d1d9;pointer-events:none;z-index:50;
       white-space:nowrap;"></div>
</div>
</div>
<div id="perfilmet" class="tab-content">
  <div class="controls">
    <label class="sel">Modalidade
      <select id="pmModalidade">
        <option value="Bike">Bike</option>
        <option value="Row">Row</option>
        <option value="Ski">Ski</option>
        <option value="Run">Run</option>
      </select></label>
    <label class="sel">Altura (cm) <input type="number" id="pmAltura" value="186" style="width:70px"></label>
    <label class="sel">Idade <input type="number" id="pmIdade" value="40" style="width:60px"></label>
    <label class="sel">Peso (kg) <input type="number" id="pmPeso" step="0.1" placeholder="auto" style="width:75px"></label>
    <label class="sel">% gordura <input type="number" id="pmBf" step="0.1" placeholder="opcional" style="width:80px"></label>
    <span id="pmCorporal" style="color:#8b949e;font-size:11px;"></span>
    <button onclick="pmCarregar()">Actualizar</button>
    <span id="pmEstado" style="color:#8b949e;font-size:12px;margin-left:8px;"></span>
  </div>
  <div id="pmMMP" style="margin:8px 0;padding:8px 10px;background:#0d1117;
       border:1px solid #21262d;border-radius:6px;"></div>
  <div id="pmAviso" style="color:#F0883E;font-size:11px;margin-bottom:8px;"></div>
  <div id="pmResumo"></div>
  <h2>Substratos — gordura e hidratos vs potência</h2>
  <div class="chartbox" style="position:relative;">
    <canvas id="chSubstratos" height="300"></canvas>
    <div id="pmTip" style="position:absolute;display:none;background:#0d1117;border:1px solid #30363d;
         border-radius:4px;padding:7px 9px;font-size:11px;color:#c9d1d9;pointer-events:none;z-index:50;
         white-space:nowrap;"></div>
  </div>
  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">O que representa cada risca do gráfico</summary>
    <div id="pmGlossarioMarcos" style="margin-top:6px;"></div>
  </details>

  <h2>Zonas de treino — semáforo</h2>
  <div id="pmSemaforo" style="overflow-x:auto;"></div>
  <div id="pmForma" style="margin-top:8px;"></div>

  <h2>Zonas ancoradas no MLSS</h2>
  <div id="pmZonas" style="overflow-x:auto;"></div>
  <div class="controls" style="margin:6px 0 14px 0;">
    <label class="sel">Guardar como
      <input type="date" id="pmDataRef" style="width:150px"></label>
    <button onclick="pmGuardar()">Guardar instantâneo</button>
    <span id="pmGuardarEstado" style="color:#8b949e;font-size:12px;margin-left:8px;"></span>
  </div>
  <p style="color:#8b949e;font-size:11px;margin-top:-8px;">
    Grava este perfil e os intervalos dos campos na base histórica, para
    depois se poder ver o que mudou. O CP e os modelos gravam-se na tab
    CP-Model — aqui só o perfil metabólico e os campos.</p>

  <h2>Validação externa — HR × Watts</h2>
  <div class="controls" style="margin-bottom:8px;">
    <label class="sel"><input type="checkbox" id="pmExtTodas" checked onchange="pmExtCarregar()">
      todo o histórico (em vez de só a season)</label>
    <span id="pmExtEstado" style="color:#8b949e;font-size:12px;margin-left:8px;"></span>
  </div>
  <div class="controls" style="margin-bottom:4px;">
    <span style="color:#8b949e;font-size:11px;">Mostrar:</span>
    <label class="sel"><input type="checkbox" class="pmExtG" value="modelo" checked onchange="pmExtDraw()"> modelo</label>
    <label class="sel"><input type="checkbox" class="pmExtG" value="aerobio" checked onchange="pmExtDraw()"> LT1</label>
    <label class="sel"><input type="checkbox" class="pmExtG" value="limiar" checked onchange="pmExtDraw()"> LT2</label>
    <label class="sel"><input type="checkbox" class="pmExtG" value="vo2max" checked onchange="pmExtDraw()"> VO₂max</label>
    <label class="sel"><input type="checkbox" class="pmExtG" value="nuvem" checked onchange="pmExtDraw()"> nuvem</label>
    <label class="sel"><input type="checkbox" class="pmExtG" value="fixos" onchange="pmExtDraw()"> definições fixas</label>
  </div>
  <div class="chartbox" style="position:relative;">
    <canvas id="chExternos" height="360"></canvas>
    <div id="pmExtTip" style="display:none;position:absolute;pointer-events:none;
      background:#161b22;border:1px solid #30363d;border-radius:6px;
      padding:6px 9px;font-size:11px;color:#c9d1d9;z-index:5;max-width:200px;"></div>
  </div>
  <p style="color:#8b949e;font-size:11px;margin:4px 0 0 0;">
    Losangos: pontos do modelo de Mader. Riscas amarelas: mediana dos campos
    da Intervals.icu, cada um no eixo em que foi medido — verticais para os
    que vêm em watts, horizontais para os que vêm em bpm. Os campos em watts são convertidos para bpm (e os em bpm
    A nuvem cinzenta são os pares HR↔Watts reais das tuas sessões. A recta
    só é desenhada, e a conversão entre eixos só acontece, se o r² for
    suficiente — abaixo disso converter amplifica ruído e a dispersão que
    apareceria seria a da recta, não a dos métodos. Riscas a tracejado curto
    com asterisco são campos iguais em todas as sessões: definições do perfil,
    não medições, e por isso não contam como confirmação independente.</p>
  <div id="pmExtTabela" style="overflow-x:auto;margin-top:8px;"></div>
  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">O que é cada campo</summary>
    <div id="pmExtGlossario" style="margin-top:6px;"></div>
  </details>
  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">MMP usados e validade do modelo</summary>
    <div id="pmDetalhe" style="margin-top:6px;"></div>
  </details>
</div>

<div id="aquecimento" class="tab-content">
  <div class="tabs" id="aqModTabs" style="border-bottom:1px solid #21262d; margin-bottom:16px;"></div>
  <div class="controls" style="margin-bottom:12px;">
    <button id="aqBtnScan" onclick="aqScan()">Actualizar</button>
    <button id="aqBtnLst" onclick="aqListar()" style="margin-left:8px;">Datas analisadas</button>
    <a href="#" id="aqLnkDiag" onclick="aqPorque();return false;"
       style="margin-left:12px;color:#8b949e;font-size:11px;">diagnóstico</a>
    <span id="aqScanEstado" style="color:#8b949e;font-size:12px;margin-left:10px;"></span>
  </div>
  <div id="aqDiag" style="color:#8b949e;font-size:11px;margin-bottom:12px;"></div>
  <div class="controls">
    <label class="sel">Métrica
      <select id="aqMetrica">
        <option value="hr">HR (bpm)</option>
        <option value="smo2">SmO&#8322; (%)</option>
        <option value="resp">Respiração (rpm)</option>
        <option value="dfa1">DFA-&#945;1</option>
        <option value="hrw">HR por watt (eficiência)</option>
      </select></label>
    <label class="sel">Agregação
      <select id="aqAgregacao">
        <option value="avg" selected>Méd</option>
        <option value="min">Mín</option>
        <option value="max">Máx</option>
      </select></label>
    <label class="sel">Rolling (sessões)
      <select id="aqRolling"></select></label>
    <label class="sel" style="cursor:pointer;white-space:nowrap;">
      <input type="checkbox" id="aqMDC" checked style="vertical-align:middle;margin-right:4px;"><span style="vertical-align:middle;">Banda MDC&#8329;&#8325;</span></label>
    <label class="sel" style="cursor:pointer;white-space:nowrap;">
      <input type="checkbox" id="aqTrend" checked style="vertical-align:middle;margin-right:4px;"><span style="vertical-align:middle;">Tendência</span></label>
  </div>
  <h2 id="aqTituloRange">Dispersão por escalão de watts</h2>
  <div class="controls" style="margin-bottom:6px;">
    <span style="color:#8b949e;font-size:12px;">Sobrepor:</span>
    <label class="sel" style="cursor:pointer;"><input type="checkbox" class="aqOv" value="hr"> HR</label>
    <label class="sel" style="cursor:pointer;"><input type="checkbox" class="aqOv" value="smo2"> SmO&#8322;</label>
    <label class="sel" style="cursor:pointer;"><input type="checkbox" class="aqOv" value="resp"> Resp</label>
    <label class="sel" style="cursor:pointer;"><input type="checkbox" class="aqOv" value="dfa1"> DFA-&#945;1</label>
    <label class="sel" style="cursor:pointer;"><input type="checkbox" class="aqOv" value="hrw"> HR/W</label>
    <span style="color:#8b949e;font-size:11px;margin-left:8px;">(normalizado 0–1 quando há mais de uma)</span>
  </div>
  <div class="chartbox" style="position:relative;">
    <canvas id="chRange" height="300"></canvas>
    <div id="aqTip" style="position:absolute;display:none;background:#0d1117;border:1px solid #30363d;
         border-radius:4px;padding:7px 9px;font-size:11px;color:#c9d1d9;pointer-events:none;z-index:50;
         white-space:nowrap;"></div>
  </div>

  <h2 id="aqTitulo">Evolução temporal</h2>
  <div class="legend" id="aqLegenda"></div>
  <div class="chartbox"><canvas id="chAquecimento" height="320"></canvas></div>

  <details style="margin-top:18px;">
    <summary style="cursor:pointer;font-size:15px;font-weight:600;padding:6px 0;">Tendência por período</summary>
    <div id="aqTendencia" style="overflow-x:auto;margin-top:8px;"></div>
  </details>
  <details style="margin-top:8px;">
    <summary style="cursor:pointer;font-size:15px;font-weight:600;padding:6px 0;">Fiabilidade por escalão (SEM / MDC)</summary>
    <div id="aqTabela" style="overflow-x:auto;margin-top:8px;"></div>
  </details>
  <details style="margin-top:8px;">
    <summary style="cursor:pointer;font-size:15px;font-weight:600;padding:6px 0;">Efeito de treino no mesmo dia</summary>
    <div id="aqContexto" style="overflow-x:auto;margin-top:8px;"></div>
  </details>
</div>
<style>
.tabs { display:flex; gap:20px; }
.tab-btn { background:none; border:none; color:#8b949e; padding:10px 0; cursor:pointer; font-size:14px; border-bottom:2px solid transparent; }
.tab-btn.active { color:#fff; border-bottom-color:#fff; }
.tab-content { display:none; }
.tab-content.active { display:block; }
</style>
"""
JS = r"""
let MODALIDADES = [];
let COBERTURA_METRICAS = {};
let PERFIL = null;
let EVOLUCAO = null;
let isLoadingPerfil = false;
let isLoadingEvolucao = false;
const CORES_METAB = {
 hr:'#E74C3C', resp:'#1ABC9C', smo2:'#F39C12', dfa1:'#9B59B6', rra1:'#58A6FF',
};
const LABELS_METAB = {
 hr:'HR (bpm)', resp:'Respiração (rpm)', smo2:'SmO₂ (%)', dfa1:'DFA-α1 (clean)',
 rra1:'RR α1',
};
const LABELS_AGREGACAO = {
 min:'Mín', max:'Máx', avg:'Méd',
};
let METRICAS_BASE = ['hr', 'resp', 'smo2', 'dfa1'];
// AGREGAÇÕES REAIS (apenas as que existem na BD)
// Preenchido a partir de /api/fisiologia/cobertura_metricas no arranque.
// Estava fixo a mao ("apenas as que existem na BD"), o que deixou de ser
// verdade assim que o pipeline passou a preencher mais colunas.
let AGREGACOES_VALIDAS = {
 hr: ['max', 'avg'],
 resp: ['avg'],
 smo2: ['min'],
 dfa1: ['avg'],
};
let chartState = {chPerfil: {}, chEvolucao: {}};
let camposSelecionados = {hr:'max', resp:'avg', smo2:'min', dfa1:'avg'};
function ctx(canvasId, h){
 const canvas = document.getElementById(canvasId);
 if(!canvas) return null;
 canvas.height = h;
 const rect = canvas.getBoundingClientRect();
 canvas.width = rect.width;
 const g = canvas.getContext('2d');
 return {g: g, W: canvas.width, H: canvas.height};
}
function noData(g, W, H, msg){
 g.fillStyle = '#555';
 g.font = '14px sans-serif';
 g.textAlign = 'center';
 g.fillText(msg, W/2, H/2);
}
function ligado(canvasId, k){
 if(!chartState[canvasId]) chartState[canvasId] = {};
 if(chartState[canvasId][k] === undefined) chartState[canvasId][k] = true;
 return chartState[canvasId][k];
}
function alternar(canvasId, k){
 if(!chartState[canvasId]) chartState[canvasId] = {};
 chartState[canvasId][k] = !chartState[canvasId][k];
}
function drawPerfil(){
 console.log('[drawPerfil] Começando. PERFIL:', PERFIL?.status);
 const o = ctx('chPerfil', 300);
 if(!o) return;
 const g = o.g, W = o.W, H = o.H;
 
 if(!PERFIL || PERFIL.status !== 'ok'){
  console.warn('[drawPerfil] Erro:', PERFIL);
  noData(g, W, H, PERFIL?.mensagem || 'Sem dados');
  return;
 }
 
 const faixas = PERFIL.faixas;
 if(!faixas || !faixas.length){
  noData(g, W, H, 'Sem faixas');
  return;
 }
 
 const disponiveis = Object.keys(camposSelecionados).filter(m => faixas.some(f => f[m+'_'+camposSelecionados[m]]));
 
 document.getElementById('lgPerfil').innerHTML = disponiveis.map(function(m){
  const off = !ligado('chPerfil', m);
  const label = LABELS_METAB[m] + ' (' + LABELS_AGREGACAO[camposSelecionados[m]] + ')';
  return '<span class="tog'+(off?' off':'')+'" data-c="chPerfil" data-k="'+m+'" style="cursor:pointer;margin-right:15px;"><i style="display:inline-block;width:10px;height:10px;background:'+CORES_METAB[m]+';margin-right:5px;"></i>'+label+'</span>';
 }).join('');
 document.querySelectorAll('#lgPerfil span.tog').forEach(function(sp){
  sp.onclick = function(){ alternar(sp.dataset.c, sp.dataset.k); drawPerfil(); };
 });
 
 const vis = disponiveis.filter(m => ligado('chPerfil', m));
 if(!vis.length){
  noData(g, W, H, 'Nenhuma métrica');
  return;
 }
 
 const temPace = faixas.some(f => f.pace_medio);
 const PL = 100, PR = 120, PB = 40, PT = temPace ? 46 : 25, w = W - PL - PR, h = H - PT - PB;
 const xs = faixas.map(f => f.watts_centro);
 const xmin = Math.min.apply(null, xs), xmax = Math.max.apply(null, xs);
 const X = v => xmax > xmin ? PL + w*(v-xmin)/(xmax-xmin) : PL + w/2;
 
 function hexRgba(hex, a){
  const h = hex.replace('#', '');
  return 'rgba('+parseInt(h.substring(0,3),16)+','+parseInt(h.substring(3,5),16)+','+parseInt(h.substring(5,7),16)+','+a+')';
 }
 
 const escalas = {};
 vis.forEach(function(m){
  const pts = faixas.filter(f => f[m+'_'+camposSelecionados[m]]);
  let a = Infinity, b = -Infinity;
  pts.forEach(function(f){
   const q = f[m+'_'+camposSelecionados[m]];
   if(q.p10 < a) a = q.p10;
   if(q.p90 > b) b = q.p90;
  });
  if(!isFinite(a)){ a = 0; b = 1; }
  const marg = (b-a)*0.15 || 1;
  a -= marg; b += marg;
  const Y = v => PT + h - (v-a)/(b-a)*h;
  escalas[m] = {a: a, b: b, Y: Y, pts: pts, range_vis: {vmin: a, vmax: b}};
 });
 
 g.strokeStyle = '#21262d';
 g.lineWidth = 1;
 for(let k = 0; k <= 2; k++){
  const y = PT + h*k/2;
  g.beginPath();
  g.moveTo(PL, y);
  g.lineTo(PL+w, y);
  g.stroke();
 }
 
 vis.forEach(function(m){
  const esc = escalas[m];
  const pts = esc.pts;
  const chave = m+'_'+camposSelecionados[m];
  
  g.fillStyle = hexRgba(CORES_METAB[m], 0.08);
  g.beginPath();
  pts.forEach(function(f, j){
   const y = esc.Y(f[chave].p75);
   if(j === 0) g.moveTo(X(f.watts_centro), y);
   else g.lineTo(X(f.watts_centro), y);
  });
  for(let j = pts.length-1; j >= 0; j--){
   g.lineTo(X(pts[j].watts_centro), esc.Y(pts[j][chave].p25));
  }
  g.closePath();
  g.fill();
  
  g.strokeStyle = CORES_METAB[m];
  g.lineWidth = 2.5;
  g.beginPath();
  pts.forEach(function(f, j){
   const y = esc.Y(f[chave].p50);
   if(j === 0) g.moveTo(X(f.watts_centro), y);
   else g.lineTo(X(f.watts_centro), y);
  });
  g.stroke();
  
  g.fillStyle = CORES_METAB[m];
  pts.forEach(function(f){
   g.beginPath();
   g.arc(X(f.watts_centro), esc.Y(f[chave].p50), 3.5, 0, 7);
   g.fill();
  });
 });
 
 g.fillStyle = '#8b949e';
 g.font = '10px sans-serif';
 g.textAlign = 'center';
 faixas.forEach(function(f){
  g.fillText(Math.round(f.watts_centro)+'W', X(f.watts_centro), H-20);
 });
 // FASE A — faixa de pace medido, por cima do eixo dos watts
 if(temPace){
  g.fillStyle = '#FF6B6B';
  g.font = 'bold 10px sans-serif';
  g.textAlign = 'left';
  g.fillText('PACE', 8, 16);
  g.font = '9px sans-serif';
  g.textAlign = 'center';
  const passo = faixas.length > 10 ? 2 : 1;
  faixas.forEach(function(f, i){
   if(i % passo !== 0 || !f.pace_medio) return;
   g.fillText(f.pace_medio, X(f.watts_centro), 16);
  });
 }
 
 g.font = '9px sans-serif';
 g.textAlign = 'right';
 vis.forEach(function(m, idx){
  const esc = escalas[m];
  const cor = CORES_METAB[m];
  for(let k = 0; k <= 2; k++){
   const val = (esc.range_vis.vmax - (esc.range_vis.vmax-esc.range_vis.vmin)*k/2).toFixed(1);
   const y = PT + h*k/2;
   g.fillStyle = cor;
   g.fillText(val, PL - 10 - idx*50, y+3);
  }
 });
 
 const tooltip = document.getElementById('tooltip');
 const canvas = document.getElementById('chPerfil');
 canvas.onmousemove = function(evt){
  const rect = canvas.getBoundingClientRect();
  const mx = evt.clientX - rect.left;
  const my = evt.clientY - rect.top;
  
  if(mx < PL || mx > PL+w || my < PT || my > PT+h){
   tooltip.style.display = 'none';
   return;
  }
  
  const watts = xmin + (mx-PL)/w*(xmax-xmin);

  // A faixa MAIS PROXIMA, nao a primeira dentro de uma tolerancia fixa.
  // Com bins de 20 W varias faixas caiam dentro dos 30 W do criterio antigo
  // e o find() devolvia a primeira: o tooltip mostrava os valores de uma
  // faixa enquanto a linha por baixo do cursor era de outra.
  let faixa = null, melhor = Infinity;
  const larg = faixas.length > 1
    ? Math.abs(faixas[1].watts_centro - faixas[0].watts_centro) : 50;
  faixas.forEach(function(f){
   const d = Math.abs(f.watts_centro - watts);
   if(d < melhor){ melhor = d; faixa = f; }
  });
  if(melhor > larg * 0.75) faixa = null;
  
  if(faixa){
   let txt = '<b>'+faixa.faixa_watts+'</b><br/>'+faixa.n_intervalos+' int.<br/>';
   if(faixa.pace_medio) txt += '<span style="color:#FF6B6B">Pace: '+faixa.pace_medio+'</span><br/>';
   vis.forEach(function(m){
    const chave = m+'_'+camposSelecionados[m];
    if(faixa[chave]){
     txt += LABELS_METAB[m]+' ('+LABELS_AGREGACAO[camposSelecionados[m]]+'): '+faixa[chave].p50+'<br/>';
    }
   });
   // qual e' o ponto realmente destacado
   txt += '<span style="color:#8b949e;font-size:10px;">centro ' 
     + Math.round(faixa.watts_centro) + 'W</span>';
   tooltip.innerHTML = txt;
   tooltip.style.left = (evt.clientX + 10) + 'px';
   tooltip.style.top = (evt.clientY + 10) + 'px';
   tooltip.style.display = 'block';
  } else {
   tooltip.style.display = 'none';
  }
 };
}
let EVOL_PONTOS = [];

function ligarTipEvolucao(){
 const cv = document.getElementById('chEvolucao');
 const tip = document.getElementById('evolTip');
 if(!cv || !tip || cv._tipLigado) return;
 cv._tipLigado = true;
 cv.addEventListener('mousemove', function(ev){
  const r = cv.getBoundingClientRect();
  const mx = (ev.clientX - r.left) * (cv.width / r.width) / (window.devicePixelRatio||1);
  const my = (ev.clientY - r.top) * (cv.height / r.height) / (window.devicePixelRatio||1);
  let perto = null, dmin = 18;
  EVOL_PONTOS.forEach(function(pt){
   const d = Math.hypot(pt.x-mx, pt.y-my);
   if(d < dmin){ dmin = d; perto = pt; }
  });
  if(!perto){ tip.style.display='none'; return; }
  const p = perto.p;
  const lbl = LABELS_METAB[EVOLUCAO.metrica] || EVOLUCAO.metrica;
  tip.innerHTML = '<b>' + p.periodo + '</b> \u2014 ' + lbl
    + '<br>mediana <b>' + p.p50 + '</b>'
    + (p.media != null ? ' &nbsp;m\u00e9dia ' + p.media : '')
    + '<br><span style="color:#8b949e;">p25 ' + p.p25 + ' \u2013 p75 ' + p.p75
    + ' &nbsp;|&nbsp; n=' + p.n + '</span>';
  tip.style.display = 'block';
  tip.style.left = Math.min(ev.clientX - r.left + 12, r.width - 180) + 'px';
  tip.style.top  = (ev.clientY - r.top - 10) + 'px';
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

function drawEvolucao(){
 const o = ctx('chEvolucao', 240);
 if(!o) return;
 const g = o.g, W = o.W, H = o.H;
 
 if(!EVOLUCAO || EVOLUCAO.status !== 'ok'){
  noData(g, W, H, 'Sem dados');
  return;
 }
 
 const periodos = EVOLUCAO.periodos || [];
 if(!periodos.length){
  noData(g, W, H, 'Sem dados');
  return;
 }
 
 const metrica = EVOLUCAO.metrica;
 const cor = CORES_METAB[metrica] || '#999';
 const valores = periodos.map(p => p.p50);
 EVOL_PONTOS = [];
 const vmin = Math.min.apply(null, valores);
 const vmax = Math.max.apply(null, valores);
 const vmarg = (vmax - vmin) * 0.15 || 1;
 const va = vmin - vmarg;
 const vb = vmax + vmarg;
 
 const PL = 70, PR = 80, PB = 30, PT = 20, w = W - PL - PR, h = H - PT - PB;
 const Y = v => PT + h - (v - va)/(vb - va)*h;
 
 g.strokeStyle = '#21262d';
 g.lineWidth = 1;
 for(let k = 0; k <= 2; k++){
  const y = PT + h*k/2;
  g.beginPath();
  g.moveTo(PL, y);
  g.lineTo(PL+w, y);
  g.stroke();
 }
 
 g.fillStyle = 'rgba('+parseInt(cor.substring(1,3),16)+','+parseInt(cor.substring(3,5),16)+','+parseInt(cor.substring(5,7),16)+',0.12)';
 g.beginPath();
 periodos.forEach(function(p, i){
  const x = PL + w*i/(periodos.length-1||1);
  if(i === 0) g.moveTo(x, Y(p.p75));
  else g.lineTo(x, Y(p.p75));
 });
 for(let i = periodos.length-1; i >= 0; i--){
  const x = PL + w*i/(periodos.length-1||1);
  g.lineTo(x, Y(periodos[i].p25));
 }
 g.closePath();
 g.fill();
 
 g.strokeStyle = cor;
 g.lineWidth = 2.5;
 g.beginPath();
 periodos.forEach(function(p, i){
  const x = PL + w*i/(periodos.length-1||1);
  if(i === 0) g.moveTo(x, Y(p.p50));
  else g.lineTo(x, Y(p.p50));
 });
 g.stroke();
 
 g.fillStyle = cor;
 periodos.forEach(function(p, i){
  const x = PL + w*i/(periodos.length-1||1);
  g.beginPath();
  g.arc(x, Y(p.p50), 3, 0, 7);
  EVOL_PONTOS.push({x:x, y:Y(p.p50), p:p});
  if(i === periodos.length-1) ligarTipEvolucao();
  g.fill();
 });
 
 g.fillStyle = '#8b949e';
 g.font = '9px sans-serif';
 g.textAlign = 'center';
 const step = Math.max(1, Math.floor(periodos.length / 8));
 periodos.forEach(function(p, i){
  if(i % step !== 0) return;
  g.fillText(p.periodo, PL + w*i/(periodos.length-1||1), H-10);
 });
 
 g.fillStyle = cor;
 g.font = '9px sans-serif';
 g.textAlign = 'right';
 for(let k = 0; k <= 2; k++){
  const val = (vb - (vb-va)*k/2).toFixed(1);
  const y = PT + h*k/2;
  g.fillText(val, PL-5, y+3);
 }
}
async function carregarPerfil(){
 if(isLoadingPerfil) return;
 isLoadingPerfil = true;
 
 const modalidade = document.getElementById('modalidade').value;
 const largura = document.getElementById('larguraBin').value;
 const params = new URLSearchParams();
 params.append('largura_bin', largura);
 Object.entries(camposSelecionados).forEach(([m, a]) => params.append(m, a));
 
 const url = '/api/fisiologia/perfil_robusto/'+modalidade+'?'+params.toString();
 console.log('[carregarPerfil]', url);
 
 try{
  const d = await fetch(url).then(r => r.json());
  console.log('[carregarPerfil] OK:', d);
  PERFIL = d;
  const pa = document.getElementById('perfAviso');
  if(pa){
   const p = [];
   if(d.n_intervalos_total != null) p.push('n=' + d.n_intervalos_total);
   if(d.outliers_watts_removidos) p.push(d.outliers_watts_removidos + ' outlier(s) de watts');
   const ig = Object.keys(d.ignoradas || {});
   if(ig.length) p.push('sem dados: ' + ig.join(', '));
   pa.textContent = p.join(' | ');
  }
  drawPerfil();
 }catch(e){
  console.error('[carregarPerfil] ERRO:', e);
  PERFIL = {status: 'erro', mensagem: e.message};
  drawPerfil();
 }finally{
  isLoadingPerfil = false;
 }
}
async function carregarEvolucao(){
 if(isLoadingEvolucao) return;
 isLoadingEvolucao = true;
 
 const metrica = document.getElementById('metricaEvolucao').value;
 const agregacao = document.getElementById('agregacaoEvolucao').value;
 const modalidade = document.getElementById('modalidade').value;
 const wmin = document.getElementById('wattsMin').value || null;
 const wmax = document.getElementById('wattsMax').value || null;
 const url = '/api/fisiologia/evolucao_robusta?modalidade='+modalidade+'&metrica='+metrica+'&agregacao='+agregacao+(wmin?'&watts_min='+wmin:'')+(wmax?'&watts_max='+wmax:'') + '&agrupar=' + (document.getElementById('agruparEvolucao')||{value:'mes'}).value;
 
 console.log('[carregarEvolucao]', url);
 
 try{
  const d = await fetch(url).then(r => r.json());
  console.log('[carregarEvolucao] OK:', d);
  EVOLUCAO = d;
  const av = document.getElementById('evolAviso');
  if(av){
   const p = [];
   if(d.coluna_usada) p.push('fonte: ' + d.coluna_usada);
   if(d.n_total != null) p.push('n=' + d.n_total);
   if(d.outliers_watts_removidos) p.push(d.outliers_watts_removidos + ' outlier(s) de watts removido(s)');
   if(d.periodos_omitidos) p.push(d.periodos_omitidos + ' per\u00edodo(s) com menos de ' + d.min_por_periodo + ' sess\u00f5es omitido(s)');
   av.textContent = p.join(' | ');
  }
  drawEvolucao();
 }catch(e){
  console.error('[carregarEvolucao] ERRO:', e);
  EVOLUCAO = {status: 'erro'};
  drawEvolucao();
 }finally{
  isLoadingEvolucao = false;
 }
}
async function load(){
 try{
  const d = await fetch('/api/metabol').then(r => r.json());
  MODALIDADES = d.modalidades || [];
  if(!MODALIDADES.length) return;
  
  const selMod = document.getElementById('modalidade');
  selMod.innerHTML = MODALIDADES.map(m => '<option value="'+m.modalidade+'">'+m.modalidade+' ('+m.n+')</option>').join('');
  selMod.onchange = function(){
   console.log('[selMod.onchange]', this.value);
   carregarPerfil();
   carregarEvolucao();
  };
  
  const agregControls = document.getElementById('agregacaoControls');
  agregControls.innerHTML = METRICAS_BASE.map(m => {
   const aggs = AGREGACOES_VALIDAS[m] || [];
   const info = COBERTURA_METRICAS[m] || {};
   return '<label class="sel">'+LABELS_METAB[m]+': <select id="agr_'+m+'">'+
   aggs.map(a => {
     const c = info[a] || {};
     const suf = c.cobertura_pct != null ? ' ('+c.cobertura_pct+'%)' : '';
     return '<option value="'+a+'"'+(camposSelecionados[m]===a?' selected':'')+'>'
       + LABELS_AGREGACAO[a] + suf + '</option>';
   }).join('')+
   '</select></label>';
  }).join('');
  
  METRICAS_BASE.forEach(m => {
   const sel = document.getElementById('agr_'+m);
   if(sel){
    sel.onchange = function(){
     console.log('[agr_'+m+'].onchange', this.value);
     camposSelecionados[m] = this.value;
     carregarPerfil();
    };
   }
  });
  
  const chkEst = document.getElementById('soEstabilizados');
  if(chkEst) chkEst.onchange = carregarPerfil;
  const selAgrupar = document.getElementById('agruparEvolucao');
  if(selAgrupar) selAgrupar.onchange = carregarEvolucao;
  const selMetricaEvolucao = document.getElementById('metricaEvolucao');
  selMetricaEvolucao.innerHTML = METRICAS_BASE.map(m => '<option value="'+m+'">'+LABELS_METAB[m]+'</option>').join('');
  
  const selAgregacaoEvolucao = document.getElementById('agregacaoEvolucao');
  const primeiraMetrica = METRICAS_BASE[0];
  const primeiraAgregacao = AGREGACOES_VALIDAS[primeiraMetrica]?.[0] || 'avg';
  selAgregacaoEvolucao.innerHTML = (AGREGACOES_VALIDAS[primeiraMetrica] || []).map(a => '<option value="'+a+'">'+LABELS_AGREGACAO[a]+'</option>').join('');
  
  selMetricaEvolucao.onchange = function(){
   const aggs = AGREGACOES_VALIDAS[this.value] || [];
   selAgregacaoEvolucao.innerHTML = aggs.map(a => '<option value="'+a+'">'+LABELS_AGREGACAO[a]+'</option>').join('');
   carregarEvolucao();
  };
  selAgregacaoEvolucao.onchange = carregarEvolucao;
  
  const selBin = document.getElementById('larguraBin');
  selBin.onchange = function(){
   console.log('[selBin.onchange]', this.value);
   carregarPerfil();
  };
  
  carregarPerfil();
  carregarEvolucao();
 }catch(e){
  console.error('[load] ERRO:', e);
 }
}
document.querySelectorAll('.tab-btn').forEach(btn => {
 btn.addEventListener('click', function(){
  const tabName = this.dataset.tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  this.classList.add('active');
  document.getElementById(tabName).classList.add('active');
  if(tabName === 'aquecimento'){ aqDraw(); aqRange(); aqTabela(); }
  if(tabName === 'perfilmet'){ if(!PM) pmCarregar(); else pmDraw(); }
 });
});
// ═════ AQUECIMENTO ═════
let AQ_MOD = null, AQ_DADOS = null, AQ_MODS = [], aqLoading = false;
const AQ_LABELS = {hr:'HR (bpm)', smo2:'SmO\u2082 (%)', resp:'Respira\u00e7\u00e3o (rpm)', dfa1:'DFA-\u03b11', hrw:'HR por watt'};
const AQ_CORES_W = ['#58A6FF','#3FB950','#F0883E','#DB6D28','#F85149'];

function aqInit(){
 const sel = document.getElementById('aqRolling');
 if(sel && !sel.options.length){
  for(let i=1;i<=12;i++){
   const o = document.createElement('option');
   o.value = i; o.textContent = (i===1 ? 'sem' : i);
   sel.appendChild(o);
  }
 }
 ['aqMetrica','aqAgregacao','aqRolling'].forEach(function(id){
  const el = document.getElementById(id);
  if(el) el.addEventListener('change', aqCarregar);
 });
 Array.prototype.slice.call(document.querySelectorAll('.aqOv')).forEach(function(c){
  c.addEventListener('change', aqCarregarOverlay);
 });
 ['aqMDC','aqTrend'].forEach(function(id){
  const el = document.getElementById(id);
  if(el) el.addEventListener('change', aqDraw);
 });
 fetch('/api/aquecimento/estado').then(r=>r.json()).then(function(d){
  const box = document.getElementById('aqModTabs');
  if(!box) return;

  const diag = document.getElementById('aqDiag');
  if(diag){
   let txt = '';
   const rej = d.rejeitadas_por_motivo || [];
   if(rej.length){
    txt += '<b>Atividades ignoradas:</b> ' + rej.map(function(r){
     return r.modalidade + ' &times;' + r.n + ' (' + r.motivo + ')';
    }).join(' &nbsp;|&nbsp; ');
   }
   const cob = d.cobertura_colunas || {};
   const vazias = Object.keys(cob).filter(function(k){ return cob[k] === 0; });
   if(vazias.length){
    txt += (txt ? '<br>' : '') + '<b>Colunas sem dados na BD:</b> ' + vazias.join(', ')
        + ' \u2014 estas m\u00e9tricas usam coluna alternativa.';
   }
   diag.innerHTML = txt;
  }

  AQ_MODS = (d.modalidades||[]).filter(m=>m.modalidade);
  ['Row','Ski','Bike'].forEach(function(m){
   if(!AQ_MODS.some(x=>x.modalidade===m)) AQ_MODS.push({modalidade:m, n_sessoes:0});
  });
  if(!AQ_MODS.length){
   box.innerHTML = '<span style="color:#8b949e;padding:10px 0;">Nenhum aquecimento detectado. Corre /api/aquecimento/calibrar?modalidade=Row</span>';
   return;
  }
  box.innerHTML = '';
  AQ_MODS.forEach(function(m, i){
   const b = document.createElement('button');
   b.className = 'tab-btn' + (i===0 ? ' active' : '');
   b.textContent = m.modalidade + ' (' + m.n_sessoes + ')';
   b.onclick = function(){
    box.querySelectorAll('.tab-btn').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    AQ_MOD = m.modalidade;
    aqCarregar();
   };
   box.appendChild(b);
  });
  AQ_MOD = AQ_MODS[0].modalidade;
  aqCarregar();
 }).catch(function(e){ console.error('[aqInit]', e); });
}

function aqScan(){
 const btn = document.getElementById('aqBtnScan');
 const est = document.getElementById('aqScanEstado');
 btn.disabled = true;
 const mods = ['Row','Ski','Bike'];
 let i = 0, tot = {det:0, rej:0, sem:0};

 function passo(){
  if(i >= mods.length){
   est.textContent = tot.det + ' novos aquecimentos'
     + (tot.sem ? ' | ' + tot.sem + ' sem streams' : '');
   btn.disabled = false; aqInit(); return;
  }
  const m = mods[i];
  est.textContent = 'a analisar ' + m + '...';
  fetch('/api/aquecimento/ingerir?modalidade=' + m + '&limite=60')
  .then(r=>r.json()).then(function(d){
   if(d.status === 'ok'){
    tot.det += d.detectados||0; tot.rej += d.rejeitados||0;
    tot.sem += d.sem_streams_guardados||0;
   }
   i++; passo();
  }).catch(function(){ i++; passo(); });
 }
 passo();
}

function aqAuditar(){
 const est = document.getElementById('aqScanEstado');
 const diag = document.getElementById('aqDiag');
 est.textContent = 'a cruzar as listas de datas com a BD...';
 fetch('/api/aquecimento/auditoria').then(r=>r.json()).then(function(d){
  if(d.status !== 'ok'){ est.textContent = 'erro: ' + (d.mensagem||'?'); return; }
  est.textContent = '';
  let h = '<table style="width:100%;border-collapse:collapse;font-size:11px;margin-top:6px;">'
   + '<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
   + '<th style="padding:5px;">Modalidade</th><th>Datas na lista</th><th>J\u00e1 na BD</th>'
   + '<th>Por processar</th><th>Detectadas</th><th>Ignoradas</th><th>Porqu\u00ea</th></tr>';
  Object.keys(d.auditoria).forEach(function(m){
   const a = d.auditoria[m];
   if(a.erro){ h += '<tr><td colspan="7" style="padding:5px;color:#F85149;">'+m+': '+a.erro+'</td></tr>'; return; }
   const mot = Object.keys(a.motivos||{}).map(k=>k+' \u00d7'+a.motivos[k]).join(', ') || '\u2014';
   h += '<tr style="border-bottom:1px solid #161b22;">'
    + '<td style="padding:5px;">'+m+'</td>'
    + '<td>'+a.datas_declaradas+'</td>'
    + '<td>'+a.existem_na_bd_fisiologia+'</td>'
    + '<td style="color:'+(a.ausentes_da_bd>0?'#F0883E':'#8b949e')+';">'+a.ausentes_da_bd+'</td>'
    + '<td style="color:#3FB950;">'+a.detectadas+'</td>'
    + '<td>'+a.rejeitadas+'</td>'
    + '<td>'+mot+'</td></tr>';
  });
  h += '</table>';
  const primeiro = d.auditoria[Object.keys(d.auditoria)[0]];
  if(primeiro && primeiro.diagnostico) h += '<p style="margin-top:6px;">' + primeiro.diagnostico + '</p>';
  diag.innerHTML = h;
 }).catch(function(e){ est.textContent = 'erro: ' + e.message; });
}

function aqPorque(){
 const est = document.getElementById('aqScanEstado');
 const diag = document.getElementById('aqDiag');
 const mod = AQ_MOD || 'Bike';
 est.textContent = 'a ler os degraus reais de sess\u00f5es ignoradas...';
 fetch('/api/aquecimento/perfil?n=3&modalidade=' + mod)
 .then(r=>r.json()).then(function(d){
  est.textContent = '';
  if(d.status !== 'ok'){ diag.innerHTML = d.mensagem || 'sem sess\u00f5es'; return; }
  const p = d.protocolo_assumido || {};
  let h = '<b>' + (d.modalidade||'') + '</b> \u2014 protocolo assumido: '
    + (p.watts||[]).join('-') + 'W (\u00b1' + p.tol + 'W, m\u00edn '
    + p.min_blocos + ' degraus de ~5min)<br>'
    + '<span style="color:#8b949e;">Degraus reais encontrados nos primeiros 30 min:</span>';
  (d.sessoes||[]).forEach(function(sx){
   h += '<div style="margin-top:8px;"><b>' + (sx.data||'') + '</b> ' + sx.activity_id;
   if(sx.erro){ h += ' \u2014 <span style="color:#F85149;">' + sx.erro + '</span></div>'; return; }
   h += ' <span style="color:#8b949e;">(streams: ' + (sx.streams_presentes||[]).join(', ') + ')</span><br>';
   const dg = sx.diagnostico;
   if(dg && dg.passos){
    h += '<div style="margin:4px 0;">Escada alvo a alvo: ';
    h += dg.passos.map(function(p){
     const cor = p.estado === 'ok' ? '#3FB950' : '#F85149';
     return '<span style="color:'+cor+';">' + p.alvo_W + 'W: ' + p.estado
       + (p.watts_medidos!=null ? ' ('+p.watts_medidos+'W, '+p.duracao_s+'s)' : '')
       + (p.watts_por_ali!=null ? ' (por ali: '+p.watts_por_ali+'W)' : '') + '</span>';
    }).join(' &rarr; ');
    h += ' &nbsp;<b>' + dg.veredicto + '</b> (' + dg.degraus_ok + '/' + dg.min_blocos_exigido + ')</div>';
   }
   if(!(sx.degraus||[]).length){ h += '<i>nenhum patamar est\u00e1vel</i>'; }
   else {
    h += '<table style="border-collapse:collapse;font-size:11px;margin-top:3px;">'
      + '<tr style="color:#8b949e;text-align:left;"><th style="padding-right:12px;">In\u00edcio</th>'
      + '<th style="padding-right:12px;">Dura\u00e7\u00e3o</th><th>Watts</th></tr>';
    sx.degraus.forEach(function(g){
     const min = Math.floor(g.inicio_s/60) + ':' + String(g.inicio_s%60).padStart(2,'0');
     h += '<tr><td style="padding-right:12px;">'+min+'</td><td style="padding-right:12px;">'
       + g.duracao_s + 's</td><td>' + g.watts + 'W</td></tr>';
    });
    h += '</table>';
   }
   h += '</div>';
  });
  diag.innerHTML = h;
 }).catch(function(e){ est.textContent = 'erro: ' + e.message; });
}

function aqIngerir(){
 const btn = document.getElementById('aqBtnIng');
 const est = document.getElementById('aqScanEstado');
 btn.disabled = true;
 let total = {det:0, rej:0, sem:0, vistas:0};
 const mods = ['Row','Ski','Bike'];
 let i = 0;

 function passo(){
  if(i >= mods.length){
   est.textContent = total.det + ' aquecimentos novos | ' + total.rej + ' ignorados | '
     + total.sem + ' sem streams guardados';
   btn.disabled = false; aqInit(); return;
  }
  const m = mods[i];
  est.textContent = 'a analisar ' + m + ' a partir dos streams...';
  fetch('/api/aquecimento/ingerir?modalidade=' + m + '&limite=60')
  .then(r=>r.json()).then(function(d){
   if(d.status === 'ok'){
    total.det += d.detectados; total.rej += d.rejeitados;
    total.sem += d.sem_streams_guardados; total.vistas += d.atividades_vistas;
   }
   i++; passo();
  }).catch(function(){ i++; passo(); });
 }
 passo();
}

function aqForcar(){
 const btn = document.getElementById('aqBtnFor');
 const est = document.getElementById('aqScanEstado');
 const diag = document.getElementById('aqDiag');
 btn.disabled = true;
 const mods = ['Row','Ski','Bike'];
 let i = 0, linhas = [];

 function passo(){
  if(i >= mods.length){
   let h = '<table style="width:100%;border-collapse:collapse;font-size:11px;">'
    + '<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
    + '<th style="padding:5px;">Modalidade</th><th>Datas</th><th>Detectadas</th>'
    + '<th>Confian\u00e7a</th><th>Rejeitadas</th><th>Sem atividade</th><th>Sem streams</th></tr>';
   linhas.forEach(function(d){
    const nv = Object.keys(d.niveis_usados||{}).map(k=>k+':'+d.niveis_usados[k]).join(' ') || '\u2014';
    h += '<tr style="border-bottom:1px solid #161b22;"><td style="padding:5px;">'+d.modalidade+'</td>'
      + '<td>'+d.datas_no_ficheiro+'</td><td style="color:#3FB950;">'+d.detectados+'</td>'
      + '<td>'+nv+'</td><td>'+d.rejeitados+'</td><td>'+d.sem_atividade_na_bd+'</td>'
      + '<td style="color:'+(d.sem_streams_guardados>0?'#F0883E':'#8b949e')+';">'+d.sem_streams_guardados+'</td></tr>';
   });
   h += '</table>';
   const semStr = linhas.some(d=>d.sem_streams_guardados>0);
   if(semStr) h += '<p style="margin-top:6px;color:#F0883E;">Sess\u00f5es sem streams guardados: '
     + 'carrega outra vez com "trazer streams" para os descarregar.</p>';
   diag.innerHTML = h;
   est.textContent = '';
   btn.disabled = false; aqInit(); return;
  }
  const m = mods[i];
  est.textContent = 'a processar as datas confirmadas de ' + m + '...';
  fetch('/api/aquecimento/forcar_datas?modalidade=' + m + '&limite=40&trazer_streams=1')
  .then(r=>r.json()).then(function(d){
   if(d.status === 'ok') linhas.push(d);
   i++; passo();
  }).catch(function(){ i++; passo(); });
 }
 passo();
}

function aqTendencia(met, agr){
 const box = document.getElementById('aqTendencia');
 if(!box || !AQ_MOD) return;
 box.innerHTML = '<span style="color:#8b949e;font-size:11px;">a calcular...</span>';
 fetch('/api/aquecimento/tendencia?modalidade='+AQ_MOD+'&metrica='+met+'&agregacao='+agr)
 .then(r=>r.json()).then(function(d){
  if(d.status !== 'ok'){ box.innerHTML = '<span style="color:#8b949e;font-size:11px;">'
    + (d.mensagem||'sem dados') + '</span>'; return; }
  const seta = {'a subir':'\u2191', 'a descer':'\u2193', 'estavel':'\u2192'};
  let h = '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
   + '<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
   + '<th style="padding:6px;">Watts</th><th>Per\u00edodo</th><th>n</th>'
   + '<th>In\u00edcio \u2192 fim</th><th>Mudan\u00e7a</th><th>Por 30d</th>'
   + '<th>r\u00b2</th><th>Tend\u00eancia</th><th>Leitura</th></tr>';
  (d.escaloes||[]).forEach(function(e){
   const js = e.janelas||[];
   if(!js.length){
    h += '<tr><td style="padding:6px;">'+e.watts_alvo+'W</td>'
      + '<td colspan="8" style="color:#8b949e;">sess\u00f5es a menos</td></tr>';
    return;
   }
   js.forEach(function(j, idx){
    let cor = '#8b949e';
    if(j.leitura === 'melhoria') cor = '#3FB950';
    else if(j.leitura === 'piora') cor = '#F85149';
    const primeira = idx===0 ? e.watts_alvo+'W' : '';
    if(j.estado === 'dados insuficientes'){
     h += '<tr style="border-bottom:1px solid #161b22;"><td style="padding:6px;">'+primeira+'</td>'
       + '<td>'+j.janela+'</td><td>'+j.n+'</td>'
       + '<td colspan="6" style="color:#8b949e;">'+(j.nota||j.estado)+'</td></tr>';
     return;
    }
    h += '<tr style="border-bottom:1px solid #161b22;">'
      + '<td style="padding:6px;">'+primeira+'</td>'
      + '<td>'+j.janela+'</td><td>'+j.n+'</td>'
      + '<td style="color:#8b949e;">'+j.primeiro+' \u2192 '+j.ultimo+'</td>'
      + '<td style="color:'+cor+';">'+(j.mudanca>0?'+':'')+j.mudanca+'</td>'
      + '<td style="color:#8b949e;">'+(j.por_30_dias>0?'+':'')+j.por_30_dias+'</td>'
      + '<td style="color:#8b949e;">'+(j.r2!=null?j.r2:'\u2014')+'</td>'
      + '<td style="color:'+cor+';">'+(seta[j.estado]||'')+' '+j.estado+'</td>'
      + '<td style="color:'+cor+';">'+(j.leitura||'')
      + (j.aviso ? ' <span style="color:#F0883E;">\u26A0 '+j.aviso+'</span>' : '')
      + '</td></tr>';
   });
  });
  h += '</table><p style="color:#8b949e;font-size:11px;margin-top:6px;">'+(d.nota||'')+'</p>';
  box.innerHTML = h;
 }).catch(function(){ box.innerHTML = ''; });
}

function aqContexto(met, agr){
 const box = document.getElementById('aqContexto');
 if(!box || !AQ_MOD) return;
 box.innerHTML = '<span style="color:#8b949e;font-size:11px;">a calcular...</span>';
 fetch('/api/aquecimento/contexto?modalidade='+AQ_MOD+'&metrica='+met+'&agregacao='+agr)
 .then(r=>r.json()).then(function(d){
  if(d.status !== 'ok'){ box.innerHTML = '<span style="color:#8b949e;font-size:11px;">'
    + (d.mensagem||'sem dados') + '</span>'; return; }
  const dc = d.dias_por_contexto || {};
  const nome = {sessao_isolada:'S\u00f3 esta sess\u00e3o', forca_antes:'For\u00e7a antes',
                outra_ciclica:'Outra c\u00edclica no dia'};
  let h = '<span style="color:#8b949e;font-size:11px;">Dias: '
    + Object.keys(dc).map(k=>(nome[k]||k)+' '+dc[k]).join(' | ') + '</span>'
    + '<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:6px;">'
    + '<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
    + '<th style="padding:6px;">Watts</th><th>Contexto</th><th>n</th><th>M\u00e9dia</th>'
    + '<th>\u0394 vs isolada</th><th>MDC\u2089\u2085</th><th>Leitura</th></tr>';
  (d.escaloes||[]).forEach(function(e){
   const gs = e.grupos || {};
   Object.keys(gs).forEach(function(g, idx){
    const info = gs[g];
    let cor = '#8b949e';
    if(info.leitura === 'acima do ruido') cor = info.diferenca > 0 ? '#F0883E' : '#3FB950';
    h += '<tr style="border-bottom:1px solid #161b22;">'
      + '<td style="padding:6px;">' + (idx===0 ? e.watts_alvo+'W' : '') + '</td>'
      + '<td>' + (nome[g]||g) + '</td><td>' + info.n + '</td>'
      + '<td>' + info.media + '</td>'
      + '<td style="color:'+cor+';">' + (info.diferenca!=null ?
          (info.diferenca>0?'+':'')+info.diferenca : '\u2014') + '</td>'
      + '<td>' + (idx===0 && e.mdc95!=null ? Math.round(e.mdc95*100)/100 : '') + '</td>'
      + '<td style="color:'+cor+';">' + (info.leitura||'refer\u00eancia')
      + (info.aviso ? ' \u26A0 '+info.aviso : '') + '</td></tr>';
   });
  });
  h += '</table><p style="color:#8b949e;font-size:11px;margin-top:6px;">' + (d.nota||'') + '</p>';
  box.innerHTML = h;
 }).catch(function(){ box.innerHTML = ''; });
}

function aqListar(){
 const est = document.getElementById('aqScanEstado');
 const diag = document.getElementById('aqDiag');
 est.textContent = 'a carregar as sess\u00f5es analisadas...';
 fetch('/api/aquecimento/listagem').then(r=>r.json()).then(function(d){
  est.textContent = '';
  if(d.status !== 'ok'){ diag.innerHTML = d.mensagem || 'erro'; return; }
  const pm = d.por_modalidade || {};
  let h = '<b>' + d.total + ' sess\u00f5es analisadas</b> \u2014 '
    + Object.keys(pm).map(k=>k+': '+pm[k]).join(', ')
    + ' <span style="color:#8b949e;">(clica numa data para ver os degraus)</span>'
    + '<div style="max-height:320px;overflow-y:auto;margin-top:6px;">'
    + '<table style="width:100%;border-collapse:collapse;font-size:11px;">'
    + '<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
    + '<th style="padding:4px;">Data</th><th>Modalidade</th><th>Degraus</th>'
    + '<th>Watts alvo</th><th>Watts real</th><th>Tempo</th><th>M\u00e9tricas</th></tr>';
  // ordenar por data no cliente tambem: o utilizador quer ver o que e'
  // recente, nao o que e' de que modalidade
  const _ord = (d.sessoes||[]).slice().sort(function(a,b){
   return (b.data||'').localeCompare(a.data||''); });
  _ord.forEach(function(sx){
   const t = sx.tempo_total_s ? Math.round(sx.tempo_total_s/60)+'min' : '\u2014';
   h += '<tr style="border-bottom:1px solid #161b22;">'
     + '<td style="padding:4px;"><a href="#" onclick="aqDetalhe(\''+sx.activity_id
     + '\',\''+sx.modalidade+'\');return false;" style="color:#58A6FF;">'
     + (sx.data||'\u2014')+'</a></td>'
     + '<td style="color:#8b949e;">'+sx.modalidade+'</td>'
     + '<td>'+sx.n_blocos+'</td><td>'+(sx.alvos||'').split(',').join('-')+'W</td>'
     + '<td>'+(sx.watts_medio!=null?sx.watts_medio+'W':'\u2014')+'</td>'
     + '<td>'+t+'</td>'
     + '<td style="color:#8b949e;">'+(sx.metricas||[]).join(', ')+'</td></tr>';
  });
  h += '</table></div>';
  diag.innerHTML = h;
 }).catch(function(e){ est.textContent = 'erro: ' + e.message; });
}

function aqDetalhe(aid, mod){
 const diag = document.getElementById('aqDiag');
 fetch('/api/aquecimento/sessao/' + aid).then(r=>r.json()).then(function(d){
  if(d.status !== 'ok'){ return; }
  const s = d.sessao;
  let h = '<b>' + s.modalidade + ' ' + (s.data||'') + '</b> ' + aid
    + ' <a href="#" onclick="aqListar();return false;" style="color:#58A6FF;margin-left:8px;">&larr; voltar</a>'
    + '<table style="border-collapse:collapse;font-size:11px;margin-top:6px;">'
    + '<tr style="color:#8b949e;text-align:left;"><th style="padding-right:12px;">Bloco</th>'
    + '<th style="padding-right:12px;">Alvo</th><th style="padding-right:12px;">Real</th>'
    + '<th style="padding-right:12px;">Tempo</th><th style="padding-right:12px;">HR</th>'
    + '<th style="padding-right:12px;">SmO\u2082</th><th style="padding-right:12px;">Resp</th><th>DFA\u03b11</th></tr>';
  const v = x => x==null ? '\u2014' : (Math.round(x*100)/100);
  (s.blocos||[]).forEach(function(b){
   h += '<tr><td style="padding-right:12px;">'+b.bloco_num+'</td>'
     + '<td style="padding-right:12px;">'+b.watts_alvo+'W</td>'
     + '<td style="padding-right:12px;">'+v(b.watts_real)+'W</td>'
     + '<td style="padding-right:12px;">'+b.tempo_seg+'s</td>'
     + '<td style="padding-right:12px;">'+v(b.hr_avg)+'</td>'
     + '<td style="padding-right:12px;">'+v(b.smo2_avg)+'</td>'
     + '<td style="padding-right:12px;">'+v(b.resp_avg)+'</td>'
     + '<td>'+v(b.dfa1_avg)+'</td></tr>';
  });
  h += '</table>';
  diag.innerHTML = h;
 });
}

function aqCarregar(){
 if(!AQ_MOD || aqLoading) return;
 aqLoading = true;
 const met = document.getElementById('aqMetrica').value;
 const agr = document.getElementById('aqAgregacao').value;
 const rol = document.getElementById('aqRolling').value;
 let url = '/api/aquecimento/serie?modalidade='+AQ_MOD+'&metrica='+met+'&agregacao='+agr;
 if(rol && rol !== '1') url += '&rolling='+rol;
 fetch(url).then(r=>r.json()).then(function(d){
  AQ_DADOS = d;
  document.getElementById('aqTitulo').textContent =
    AQ_LABELS[met] + ' \u2014 ' + AQ_MOD + ' por escal\u00e3o de watts';
  aqDraw(); aqRange(); aqTabela(); aqTendencia(met, agr); aqContexto(met, agr);
 }).catch(function(e){
  console.error('[aqCarregar]', e);
  AQ_DADOS = {status:'erro'}; aqDraw();
 }).finally(function(){ aqLoading = false; });
}

function aqRegressao(ys){
 const n = ys.length;
 if(n < 2) return null;
 let sx=0, sy=0, sxy=0, sxx=0;
 for(let i=0;i<n;i++){ sx+=i; sy+=ys[i]; sxy+=i*ys[i]; sxx+=i*i; }
 const den = n*sxx - sx*sx;
 if(!den) return null;
 const m = (n*sxy - sx*sy)/den;
 return {m:m, b:(sy - m*sx)/n};
}

function aqDraw(){
 const o = ctx('chAquecimento', 320);
 if(!o) return;
 const g = o.g, W = o.W, H = o.H;
 if(!AQ_DADOS || AQ_DADOS.status !== 'ok' || !(AQ_DADOS.series||[]).length){
  noData(g, W, H, AQ_DADOS && AQ_DADOS.status === 'sem_dados'
    ? 'Nenhum aquecimento detectado nesta modalidade' : 'Sem dados');
  document.getElementById('aqLegenda').innerHTML = '';
  return;
 }
 const series = AQ_DADOS.series.filter(s => s.valores && s.valores.length);
 if(!series.length){ noData(g, W, H, 'Sem dados'); return; }

 const mostrarMDC = document.getElementById('aqMDC').checked;
 const mostrarTrend = document.getElementById('aqTrend').checked;

 let vmin = Infinity, vmax = -Infinity, nmax = 0;
 series.forEach(function(s){
  s.valores.forEach(function(v){ if(v<vmin) vmin=v; if(v>vmax) vmax=v; });
  if(s.valores.length > nmax) nmax = s.valores.length;
  const mdc = s.reliability && s.reliability.mdc95;
  if(mostrarMDC && mdc){
   const ult = s.valores[s.valores.length-1];
   if(ult-mdc < vmin) vmin = ult-mdc;
   if(ult+mdc > vmax) vmax = ult+mdc;
  }
 });
 const marg = (vmax-vmin)*0.15 || 1;
 const va = vmin-marg, vb = vmax+marg;
 const PL=70, PR=90, PB=34, PT=16, w=W-PL-PR, h=H-PT-PB;
 const Y = v => PT + h - (v-va)/(vb-va)*h;
 const X = i => PL + w*i/((nmax-1)||1);

 g.strokeStyle = '#21262d'; g.lineWidth = 1;
 g.fillStyle = '#8b949e'; g.font = '11px sans-serif'; g.textAlign = 'right';
 for(let k=0;k<=4;k++){
  const y = PT + h*k/4;
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.fillText((vb - (vb-va)*k/4).toFixed(1), PL-8, y+4);
 }

 series.forEach(function(s, si){
  const cor = AQ_CORES_W[si % AQ_CORES_W.length];
  const vals = s.valores;
  const rel = s.reliability || {};

  // banda MDC em torno da ultima observacao: fora dela = mudanca real
  if(mostrarMDC && rel.mdc95){
   const ult = vals[vals.length-1];
   const r = parseInt(cor.substring(1,3),16), gg = parseInt(cor.substring(3,5),16), bb = parseInt(cor.substring(5,7),16);
   g.fillStyle = 'rgba('+r+','+gg+','+bb+',' + (rel.fiavel ? 0.10 : 0.05) + ')';
   g.fillRect(PL, Y(ult+rel.mdc95), w, Y(ult-rel.mdc95)-Y(ult+rel.mdc95));
   g.strokeStyle = 'rgba('+r+','+gg+','+bb+',0.35)';
   g.setLineDash([3,3]); g.lineWidth = 1;
   [ult+rel.mdc95, ult-rel.mdc95].forEach(function(v){
    g.beginPath(); g.moveTo(PL, Y(v)); g.lineTo(PL+w, Y(v)); g.stroke();
   });
   g.setLineDash([]);
  }

  g.strokeStyle = cor; g.lineWidth = 2; g.beginPath();
  vals.forEach(function(v,i){ i ? g.lineTo(X(i),Y(v)) : g.moveTo(X(i),Y(v)); });
  g.stroke();

  g.fillStyle = cor;
  vals.forEach(function(v,i){ g.beginPath(); g.arc(X(i),Y(v),2.5,0,6.2832); g.fill(); });

  if(mostrarTrend){
   const reg = aqRegressao(vals);
   if(reg){
    g.strokeStyle = cor; g.lineWidth = 1.5; g.setLineDash([6,4]);
    g.beginPath();
    g.moveTo(X(0), Y(reg.b));
    g.lineTo(X(vals.length-1), Y(reg.m*(vals.length-1)+reg.b));
    g.stroke(); g.setLineDash([]);
   }
  }

  g.fillStyle = cor; g.font = '11px sans-serif'; g.textAlign = 'left';
  g.fillText(s.watts_alvo + 'W', PL+w+8, Y(vals[vals.length-1])+4);
 });

 // eixo X: ate 5 datas distribuidas + marcas verticais
 const s0 = series[0];
 if(s0.datas && s0.datas.length){
  const nd = s0.datas.length;
  const passos = Math.min(5, nd);
  g.fillStyle = '#8b949e'; g.font = '10px sans-serif';
  g.strokeStyle = '#21262d';
  for(let k=0;k<passos;k++){
   const i = Math.round(k*(nd-1)/((passos-1)||1));
   const x = X(i);
   g.beginPath(); g.moveTo(x, PT); g.lineTo(x, PT+h); g.stroke();
   g.textAlign = k===0 ? 'left' : (k===passos-1 ? 'right' : 'center');
   g.fillText(s0.datas[i].substring(5), x, H-12);
  }
  g.textAlign = 'center';
  g.fillText(s0.datas[0].substring(0,4), PL+w/2, H-1);
 }

 document.getElementById('aqLegenda').innerHTML = series.map(function(s, si){
  const cor = AQ_CORES_W[si % AQ_CORES_W.length];
  const wr = (s.watts_reais_medio != null)
    ? ' \u2014 real ' + s.watts_reais_medio.toFixed(0) + 'W' : '';
  return '<span style="margin-right:16px;color:'+cor+';">\u25CF ' + s.watts_alvo
   + 'W' + wr + ' (n=' + s.n + ')</span>';
 }).join('');
}

let AQ_OVERLAY = {};   // metrica -> series carregadas

function aqOverlaySel(){
 return Array.prototype.slice.call(document.querySelectorAll('.aqOv:checked'))
   .map(function(c){ return c.value; });
}

function aqCarregarOverlay(){
 const sel = aqOverlaySel();
 const agr = document.getElementById('aqAgregacao').value;
 if(!sel.length || !AQ_MOD){ AQ_OVERLAY = {}; aqRange(); return; }
 let pend = sel.length;
 const novo = {};
 sel.forEach(function(m){
  fetch('/api/aquecimento/serie?modalidade='+AQ_MOD+'&metrica='+m+'&agregacao='+agr)
  .then(r=>r.json()).then(function(d){
   if(d.status === 'ok') novo[m] = d.series;
  }).catch(function(){}).finally(function(){
   if(--pend === 0){ AQ_OVERLAY = novo; aqRange(); }
  });
 });
}

function aqRange(){
 const o = ctx('chRange', 300);
 if(!o) return;
 const g = o.g, W = o.W, H = o.H;
 if(!AQ_DADOS || AQ_DADOS.status !== 'ok' || !(AQ_DADOS.series||[]).length){
  noData(g, W, H, 'Sem dados'); return;
 }
 const metPrinc = document.getElementById('aqMetrica').value;

 // conjunto a desenhar: a metrica escolhida + as sobrepostas
 const conjuntos = [];
 conjuntos.push({metrica: metPrinc, series: AQ_DADOS.series});
 Object.keys(AQ_OVERLAY).forEach(function(m){
  if(m !== metPrinc) conjuntos.push({metrica: m, series: AQ_OVERLAY[m]});
 });
 const multi = conjuntos.length > 1;

 document.getElementById('aqTituloRange').textContent = multi
   ? 'Dispers\u00e3o por escal\u00e3o \u2014 ' + conjuntos.map(c=>AQ_LABELS[c.metrica]).join(' + ')
     + ' (normalizado)'
   : AQ_LABELS[metPrinc] + ' \u2014 dispers\u00e3o por escal\u00e3o (m\u00e9dia \u00b1 MDC\u2089\u2085)';

 // escala: valores reais quando ha' so uma metrica, 0-1 quando ha' varias
 conjuntos.forEach(function(c){
  let lo = Infinity, hi = -Infinity;
  (c.series||[]).forEach(function(s){
   (s.valores||[]).forEach(function(v){ if(v<lo) lo=v; if(v>hi) hi=v; });
  });
  c.lo = lo; c.hi = hi;
  c.norm = function(v){
   if(!multi) return v;
   return (c.hi === c.lo) ? 0.5 : (v - c.lo)/(c.hi - c.lo);
  };
 });

 let vmin = Infinity, vmax = -Infinity;
 conjuntos.forEach(function(c){
  (c.series||[]).forEach(function(s){
   (s.valores||[]).forEach(function(v){
    const nv = c.norm(v); if(nv<vmin) vmin=nv; if(nv>vmax) vmax=nv;
   });
   const m = s.reliability && s.reliability.mdc95;
   if(m && !multi){
    const md = s.valores.reduce((a,b)=>a+b,0)/s.valores.length;
    if(md-m < vmin) vmin = md-m;
    if(md+m > vmax) vmax = md+m;
   }
  });
 });
 if(!isFinite(vmin)){ noData(g, W, H, 'Sem dados'); return; }
 const marg = (vmax-vmin)*0.12 || 0.1;
 const va = vmin-marg, vb = vmax+marg;
 const PL=70, PR=30, PB=40, PT=16, w=W-PL-PR, h=H-PT-PB;
 const Y = v => PT + h - (v-va)/(vb-va)*h;

 const todosW = [];
 conjuntos.forEach(c => (c.series||[]).forEach(s => todosW.push(s.watts_alvo)));
 const wattsMin = Math.min.apply(null, todosW);
 const wattsMax = Math.max.apply(null, todosW);
 const span = (wattsMax - wattsMin) || 1;
 const X = wv => PL + (wv - wattsMin)/span * w;

 g.strokeStyle = '#21262d'; g.lineWidth = 1;
 g.fillStyle = '#8b949e'; g.font = '11px sans-serif'; g.textAlign = 'right';
 for(let k=0;k<=4;k++){
  const y = PT + h*k/4;
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  const val = vb - (vb-va)*k/4;
  g.fillText(multi ? val.toFixed(2) : val.toFixed(1), PL-8, y+4);
 }

 AQ_PONTOS = [];   // para o tooltip

 conjuntos.forEach(function(c, ci){
  const corBase = AQ_CORES_M[c.metrica] || AQ_CORES_W[ci % AQ_CORES_W.length];
  (c.series||[]).forEach(function(s, si){
   const vals = s.valores || [];
   if(!vals.length) return;
   const cor = multi ? corBase : AQ_CORES_W[si % AQ_CORES_W.length];
   const media = vals.reduce((a,b)=>a+b,0)/vals.length;
   const mdc = s.reliability && s.reliability.mdc95;
   const x = X(s.watts_alvo) + (multi ? (ci - (conjuntos.length-1)/2) * 12 : 0);
   const meia = Math.max(12, w/((c.series.length||1)*3.2));

   if(mdc && !multi){
    const r=parseInt(cor.substring(1,3),16), gg=parseInt(cor.substring(3,5),16), bb=parseInt(cor.substring(5,7),16);
    g.fillStyle = 'rgba('+r+','+gg+','+bb+',0.10)';
    g.fillRect(x-meia, Y(media+mdc), meia*2, Y(media-mdc)-Y(media+mdc));
    g.strokeStyle = 'rgba('+r+','+gg+','+bb+',0.4)'; g.setLineDash([3,3]);
    [media+mdc, media-mdc].forEach(function(v){
     g.beginPath(); g.moveTo(x-meia, Y(v)); g.lineTo(x+meia, Y(v)); g.stroke();
    });
    g.setLineDash([]);
   }

   g.fillStyle = multi ? 'rgba(139,148,158,0.35)' : 'rgba(139,148,158,0.55)';
   vals.forEach(function(v, i){
    const jx = x + ((i*37)%100/100 - 0.5) * meia * 1.4;
    const py = Y(c.norm(v));
    g.beginPath(); g.arc(jx, py, 2, 0, 6.2832); g.fill();
    AQ_PONTOS.push({x:jx, y:py, valor:v, metrica:c.metrica,
                    watts:s.watts_alvo, data:(s.datas||[])[i]});
   });

   const ym = Y(c.norm(media));
   g.strokeStyle = cor; g.lineWidth = 2;
   g.beginPath(); g.moveTo(x-meia*0.7, ym); g.lineTo(x+meia*0.7, ym); g.stroke();
   g.fillStyle = cor;
   g.beginPath(); g.arc(x, ym, 4, 0, 6.2832); g.fill();
   AQ_PONTOS.push({x:x, y:ym, valor:media, metrica:c.metrica, media:true,
                   watts:s.watts_alvo, n:s.n,
                   mdc:mdc, sem:(s.reliability||{}).sem});

   if(ci === 0){
    g.fillStyle = '#8b949e'; g.font = '11px sans-serif'; g.textAlign = 'center';
    g.fillText(s.watts_alvo + 'W', X(s.watts_alvo), H-20);
    g.fillText('n=' + s.n, X(s.watts_alvo), H-6);
   }
  });

  // linha a ligar as medias
  g.strokeStyle = corBase; g.lineWidth = 1.5; g.setLineDash([5,4]);
  g.beginPath();
  (c.series||[]).filter(s=>s.valores && s.valores.length).forEach(function(s, i){
   const md = s.valores.reduce((a,b)=>a+b,0)/s.valores.length;
   const x = X(s.watts_alvo) + (multi ? (ci - (conjuntos.length-1)/2) * 12 : 0);
   i ? g.lineTo(x, Y(c.norm(md))) : g.moveTo(x, Y(c.norm(md)));
  });
  g.stroke(); g.setLineDash([]);
 });

 if(multi){
  g.textAlign = 'left'; g.font = '11px sans-serif';
  conjuntos.forEach(function(c, i){
   g.fillStyle = AQ_CORES_M[c.metrica] || '#8b949e';
   g.fillText('\u25CF ' + AQ_LABELS[c.metrica] + ' ('
     + c.lo.toFixed(1) + '\u2013' + c.hi.toFixed(1) + ')', PL + 6 + i*150, PT + 12);
  });
 }
 aqLigarTooltip(o);
}

const AQ_CORES_M = {hr:'#F85149', smo2:'#F0883E', resp:'#3FB950',
                    dfa1:'#A371F7', hrw:'#58A6FF'};
let AQ_PONTOS = [];

function aqLigarTooltip(o){
 const cv = document.getElementById('chRange');
 const tip = document.getElementById('aqTip');
 if(!cv || !tip || cv._tipLigado) return;
 cv._tipLigado = true;
 cv.addEventListener('mousemove', function(ev){
  const r = cv.getBoundingClientRect();
  const mx = (ev.clientX - r.left) * (cv.width / r.width) / (window.devicePixelRatio||1);
  const my = (ev.clientY - r.top) * (cv.height / r.height) / (window.devicePixelRatio||1);
  let perto = null, dmin = 14;
  AQ_PONTOS.forEach(function(p){
   const d = Math.hypot(p.x-mx, p.y-my);
   if(d < dmin){ dmin = d; perto = p; }
  });
  if(!perto){ tip.style.display='none'; return; }
  const v = Math.round(perto.valor*1000)/1000;
  let html = '<b>' + (AQ_LABELS[perto.metrica]||perto.metrica) + '</b><br>'
    + perto.watts + 'W &nbsp; <b>' + v + '</b>';
  if(perto.media){
   html += '<br><span style="color:#8b949e;">m\u00e9dia de ' + perto.n + ' sess\u00f5es';
   if(perto.mdc) html += ' &nbsp;MDC ' + (Math.round(perto.mdc*100)/100);
   html += '</span>';
  } else if(perto.data){
   html += '<br><span style="color:#8b949e;">' + perto.data + '</span>';
  }
  tip.innerHTML = html;
  tip.style.display = 'block';
  tip.style.left = Math.min(ev.clientX - r.left + 12, r.width - 170) + 'px';
  tip.style.top  = (ev.clientY - r.top - 10) + 'px';
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

function aqTabela(){
 const box = document.getElementById('aqTabela');
 if(!box) return;
 if(!AQ_DADOS || AQ_DADOS.status !== 'ok'){ box.innerHTML = ''; return; }
 let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
  + '<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
  + '<th style="padding:6px;">Watts</th><th>n sess\u00f5es</th><th>Primeiro</th><th>\u00daltimo</th>'
  + '<th>\u0394</th><th>SEM</th><th>MDC\u2089\u2085</th><th>Interpreta\u00e7\u00e3o</th></tr>';
 AQ_DADOS.series.forEach(function(s){
  const v = s.valores || [];
  if(!v.length) return;
  const ini = v[0], fim = v[v.length-1], d = fim - ini;
  const rel = s.reliability || {};
  let interp, cor;
  if(rel.mdc95 == null){
   interp = rel.nota || 'sem SEM'; cor = '#8b949e';
  } else if(Math.abs(d) >= rel.mdc95){
   interp = 'mudan\u00e7a real (> MDC)'; cor = d > 0 ? '#3FB950' : '#F85149';
  } else {
   interp = 'dentro do ru\u00eddo'; cor = '#8b949e';
  }
  if(rel.mdc95 != null && !rel.fiavel) interp += ' \u26A0';
  html += '<tr style="border-bottom:1px solid #161b22;">'
   + '<td style="padding:6px;">' + s.watts_alvo + 'W</td>'
   + '<td>' + s.n + '</td>'
   + '<td>' + ini.toFixed(1) + '</td>'
   + '<td>' + fim.toFixed(1) + '</td>'
   + '<td style="color:' + cor + ';">' + (d>=0?'+':'') + d.toFixed(1) + '</td>'
   + '<td>' + (rel.sem != null ? rel.sem.toFixed(2) : '\u2014') + '</td>'
   + '<td>' + (rel.mdc95 != null ? rel.mdc95.toFixed(2) : '\u2014') + '</td>'
   + '<td style="color:' + cor + ';">' + interp + '</td></tr>';
 });
 html += '</table>'
  + '<p style="color:#8b949e;font-size:11px;margin-top:8px;">'
  + 'SEM estimado a partir de sess\u00f5es separadas por \u226410 dias (ru\u00eddo de medi\u00e7\u00e3o, '
  + 'n\u00e3o adapta\u00e7\u00e3o). MDC\u2089\u2085 = SEM \u00d7 1,96 \u00d7 \u221a2. '
  + '\u26A0 = menos de 10 pares, banda indicativa.</p>';
 box.innerHTML = html;
}

function carregarCobertura(){
 return fetch('/api/fisiologia/cobertura_metricas').then(r=>r.json()).then(function(d){
  if(d.status !== 'ok') return;
  COBERTURA_METRICAS = d.cobertura || {};
  // metricas opcionais (ex. RRa1) so entram se houver dados a serio
  (d.opcionais_com_dados || []).forEach(function(m){
   if(METRICAS_BASE.indexOf(m) === -1) METRICAS_BASE.push(m);
  });
  const novo = {};
  METRICAS_BASE.forEach(function(m){
   const info = COBERTURA_METRICAS[m] || {};
   // ordenar por cobertura e esconder as que quase nao tem dados
   const validas = Object.keys(info)
     .filter(a => info[a] && info[a].coluna_usada && info[a].cobertura_pct >= 5)
     .sort((a,b) => info[b].cobertura_pct - info[a].cobertura_pct);
   if(validas.length) novo[m] = validas;
  });
  if(Object.keys(novo).length) AGREGACOES_VALIDAS = novo;
  METRICAS_BASE.forEach(function(m){
   const aggs = AGREGACOES_VALIDAS[m] || [];
   if(aggs.length && aggs.indexOf(camposSelecionados[m]) === -1){
    camposSelecionados[m] = aggs[0];
   }
  });
 }).catch(function(){});
}

// ═════ PERFIL METABOLICO ═════
let PM = null;

function pmCarregar(usarManuais){
 const mod = document.getElementById('pmModalidade').value;
 const est = document.getElementById('pmEstado');
 const p = new URLSearchParams();
 ['Altura','Idade','Peso','Bf'].forEach(function(k){
  const el = document.getElementById('pm'+k);
  if(el && el.value !== '') p.set(k.toLowerCase(), el.value);
 });
 // O modo fica fixo em 'coerente': aqui o que interessa e' o perfil de um
 // momento concreto, e nao ha razao para o utilizador ter de escolher. Quem
 // precisa de comparar modos e' a tab CP-Model, que e' onde se decide o CP.
 p.set('modo', 'coerente');
 if(usarManuais){
  const secs = Array.prototype.slice.call(document.querySelectorAll('.pmSec'))
    .map(function(e){ return parseInt(e.value); }).filter(function(x){ return x>0; });
  if(secs.length) p.set('duracoes', secs.join(','));
  Array.prototype.slice.call(document.querySelectorAll('.pmW')).forEach(function(e, i){
   const sec = secs[i];
   if(sec && e.value !== '') p.set('mmp_'+sec, e.value);
  });
  const pmx = document.getElementById('pmPmax');
  if(pmx && pmx.value !== '') p.set('pmax', pmx.value);
 }
 est.textContent = 'a calcular...';
 fetch('/api/metabol/perfil_metabolico/'+mod+'?'+p.toString())
 .then(r=>r.json()).then(function(d){
  PM = d;
  const av = document.getElementById('pmAviso');
  if(d.status !== 'ok'){
   est.textContent = '';
   document.getElementById('pmResumo').innerHTML =
     '<span style="color:#8b949e;">' + (d.mensagem||'sem dados') + '</span>';
   av.textContent = (d.duracoes_em_falta_s||[]).length
     ? 'faltam MMP de: ' + d.duracoes_em_falta_s.map(x=>Math.round(x/60)+'min').join(', ') : '';
   pmDraw(); return;
  }
  const ct = d.corporal_trimestre || {};
  est.textContent = 'season ' + (d.season||'?') + ' · ' + d.n_curvas_na_season + ' curvas'
    + (ct.peso ? ' · peso ' + ct.peso + 'kg (média de ' + ct.n_peso + ' registos do trimestre)' : '')
    + (ct.bf ? ' · BF ' + ct.bf + '%' : '');
  av.textContent = [d.aviso_coerencia, d.aviso_recuo, d.aviso_datas]
    .filter(Boolean).join(' | ');
  pmCorporal(); pmResumo(); pmMMPEdit(); pmDraw(); pmSemaforo(); pmZonas(); pmDetalhe();
  pmExtCarregar();
  pmCarregarCP();
 }).catch(function(e){ est.textContent = 'erro: ' + e.message; });
}

// ═════ VALIDACAO EXTERNA — campos da Intervals.icu ═════
let PMEXT = null;
let PM_CP = null;

function pmGuardar(){
 const est = document.getElementById('pmGuardarEstado');
 est.textContent = 'a guardar...';
 fetch('/api/perfil/guardar', {method:'POST',
  headers:{'Content-Type':'application/json'},
  body: JSON.stringify({
   modalidade: document.getElementById('pmModalidade').value,
   season: (PM||{}).season,
   data_referencia: document.getElementById('pmDataRef').value || null,
   guardar: ['perfil','limiares']})})
 .then(r=>r.json()).then(function(d){
  if(d.status==='erro'){ est.textContent = 'erro: ' + d.mensagem; return; }
  const p = (d.escrito||{}).perfil || {};
  est.textContent = 'guardado em ' + d.data_referencia
   + (p.mlss_w ? ' · MLSS ' + p.mlss_w + ' W' : '')
   + (d.status==='gravado_sem_upload' ? ' (local; Drive falhou)' : '')
   + ' — histórico na tab CP-Model';
 }).catch(e=>{ est.textContent = 'erro: ' + e.message; });
}

function pmCorporal(){
 // Pre-preencher peso e %BF com a media do mes vinda da tab Corporal, em
 // vez de deixar em branco. Os campos ficam editaveis: sobrepor um valor
 // continua a funcionar, e a etiqueta diz de onde veio o que la' esta.
 const el = document.getElementById('pmCorporal');
 const c = (PM && PM.corporal_trimestre) || {};
 const ent = (PM && PM.entradas) || {};
 const iP = document.getElementById('pmPeso'), iB = document.getElementById('pmBf');
 if(iP && !iP.value && c.peso) iP.value = c.peso;
 if(iB && !iB.value && c.bf) iB.value = c.bf;
 if(!el) return;
 if(c.erro || c.erro_sheets){
  el.innerHTML = '<span style="color:#F0883E;">corporal indisponível: '
    + (c.erro || c.erro_sheets) + '</span>';
  return;
 }
 const partes = [];
 if(c.peso) partes.push('peso ' + c.peso + ' kg (média do ' + (c.janela_peso||'?')
   + ', n=' + c.n_peso + ', ' + c.min_peso + '–' + c.max_peso + ')');
 if(c.bf) partes.push('%BF ' + c.bf + ' (média do ' + (c.janela_bf||'?')
   + ', n=' + c.n_bf + ')');
 if(!partes.length){
  el.innerHTML = 'sem registos corporais recentes'
    + (c.ultima_data ? ' — o último é de ' + c.ultima_data : '')
    + (ent.peso ? '; a usar ' + ent.peso + ' kg das power curves' : '');
  return;
 }
 el.textContent = 'da tab Corporal: ' + partes.join(' · ');
}

function pmCarregarCP(){
 // O CP nao e' calculado aqui: vem da tab CP-Model, que corre os varios
 // modelos e deixa a escolha ao utilizador. Aqui so' se desenha, para se
 // ver onde ele cai em relacao ao MLSS e ao LT2 -- se ficarem longe, um
 // dos dois caminhos esta errado.
 const mod = document.getElementById('pmModalidade').value;
 const sea = (PM && PM.season) ? '?season=' + encodeURIComponent(PM.season) : '';
 PM_CP = null; PM_CP_INFO = null;
 fetch('/api/cp/actual/' + mod + sea).then(r=>r.json()).then(function(d){
  if(d && d.status==='ok' && d.cp_w){
   PM_CP = d.cp_w; PM_CP_INFO = d;
   PM_CP_NOME = d.modelo;
  }
  pmDraw(); pmGlossarioMarcos(); pmResumo();
 }).catch(function(){ PM_CP = null; PM_CP_INFO = null; pmDraw(); });
}
let PM_CP_NOME = null, PM_CP_INFO = null;

function pmGlossarioMarcos(){
 const el = document.getElementById('pmGlossarioMarcos');
 if(!el) return;
 const m = (PM && PM.mader) || {}, lm = (PM && PM.limiares) || {};
 const linhas = [
  ['FatMax', '#A371F7', m.fatmax_w, 'W',
   'Potência de oxidação máxima de gordura — o pico da curva verde. Não é '
   + 'um limiar: é onde a gordura contribui mais em valor absoluto. Treinar '
   + 'aqui não "queima mais gordura" a longo prazo do que treinar acima, '
   + 'porque o que conta é o gasto total e a adaptação.'],
  ['LT1', '#3FB950', lm.lt1_w, 'W',
   'Primeiro limiar de lactato, pelo ponto de quebra da curva — onde a '
   + 'subida deixa de ser plana. Fronteira entre o domínio moderado e o '
   + 'pesado; abaixo dele o lactato estabiliza indefinidamente. '
   + (lm.lt1_convencao_w!=null ? 'A convenção clássica de +0,5 mmol/L daria '
      + lm.lt1_convencao_w + ' W, mas pressupõe um lactato de repouso medido.' : '')],
  ['MLSS', '#58A6FF', m.mlss_at_w, 'W',
   'Máximo estado estacionário de lactato, do modelo de Mader: a potência '
   + 'mais alta em que a produção e a remoção ainda se equilibram. É o '
   + 'valor que ancora as zonas desta tab.'],
  ['LT2', '#F85149', lm.lt2_w, 'W',
   'Segundo limiar, pela máxima curvatura da curva de lactato — não pelo '
   + '4 mmol/L fixo. Deve ficar perto do MLSS'
   + (lm.lt2_vs_mlss_w!=null ? ' (aqui difere ' + lm.lt2_vs_mlss_w + ' W)' : '')
   + '; grande divergência significa que o modelo não descreve este atleta.'],
  ['CP', '#E3B341', PM_CP, 'W',
   'Critical Power, vindo da tab CP-Model'
   + (PM_CP_NOME ? ' (modelo ' + PM_CP_NOME + ')' : '')
   + (PM_CP_INFO && !PM_CP_INFO.confirmado
      ? '. Ainda não há instantâneo gravado para esta modalidade, portanto '
        + 'este é o de menor SEE% — que ninguém confirmou. Fixa-o na tab '
        + 'CP-Model antes de o usares para decidir alguma coisa'
      : (PM_CP_INFO && PM_CP_INFO.data_referencia
         ? '. Instantâneo de ' + PM_CP_INFO.data_referencia : ''))
   + '. É calculado por outro caminho — ajuste à curva de potência, não à '
   + 'curva de lactato — e por isso serve de verificação: CP e MLSS deviam '
   + 'ficar próximos. Se não ficarem, ou os MMP não são esforços máximos '
   + 'ou o modelo de Mader não serve aqui.'],
 ];
 let h = '';
 linhas.forEach(function(l){
  h += '<p style="font-size:11px;color:#8b949e;margin:6px 0;">'
   + '<b style="color:' + l[1] + ';">\u2502 ' + l[0] + '</b> '
   + (l[2]!=null ? '<b style="color:#c9d1d9;">' + Math.round(l[2]) + ' ' + l[3] + '</b>' : '<i>sem valor</i>')
   + '<br>' + l[4] + '</p>';
 });
 h += '<p style="font-size:11px;color:#8b949e;margin-top:8px;">O fundo está '
  + 'dividido nos três domínios de intensidade: <b style="color:#3FB950;">'
  + 'moderado</b> abaixo do LT1, onde o lactato estabiliza e o esforço é '
  + 'sustentável horas; <b style="color:#F0883E;">pesado</b> entre o LT1 e o '
  + 'MLSS, onde estabiliza mais alto e aguenta dezenas de minutos; '
  + '<b style="color:#F85149;">severo</b> acima do MLSS, onde não estabiliza '
  + 'e o esforço termina em exaustão. A fronteira não é uma convenção de '
  + 'zonas percentuais — é o que decide se o lactato chega ou não a um '
  + 'patamar.</p>';
 h += '<p style="font-size:11px;color:#8b949e;margin-top:8px;">As riscas '
  + 'horizontais partem de cada vertical até ao eixo e marcam quanta gordura '
  + 'e quantos hidratos estão a ser oxidados nesse ponto, em g/h — é a '
  + 'leitura que interessa para nutrição em prova.</p>';
 el.innerHTML = h;
}

function pmExtCarregar(){
 const mod = document.getElementById('pmModalidade').value;
 const est = document.getElementById('pmExtEstado');
 const todas = document.getElementById('pmExtTodas').checked ? '?todas=1' : '';
 est.textContent = 'a carregar...';
 fetch('/api/metabol/limiares_externos/'+mod+todas)
 .then(r=>r.json()).then(function(d){
  PMEXT = d;
  if(d.status !== 'ok'){
   est.textContent = d.mensagem || 'sem dados';
   document.getElementById('pmExtTabela').innerHTML = '';
   pmExtDraw(); return;
  }
  const rel = d.relacao_hr_watts || {};
  est.textContent = d.actividades + ' actividades · ' + d.ambito
    + (d.season ? ' ' + d.season : '')
    + ' · ' + (d.campos||[]).length + ' campos'
    + (d.ambito_explicado && d.ambito_explicado.indexOf('so a season')===0
       ? ' · n da season (histórico após a barra)' : '')
    + (rel.suficiente ? ' · HR↔W r²=' + rel.r2 + ' (n=' + rel.n + ')'
                      : ' · sem recta HR↔W');
  pmExtTabela(); pmExtDraw(); pmExtGlossario();
 }).catch(function(e){ est.textContent = 'erro: ' + e.message; });
}

function pmExtTabela(){
 const cs = (PMEXT && PMEXT.campos) || [];
 const box = document.getElementById('pmExtTabela');
 if(!cs.length){ box.innerHTML='<span style="color:#8b949e;">nenhum campo reconhecido</span>'; return; }
 const rel = PMEXT.relacao_hr_watts || {};

 // ── coerência entre estimativas independentes do mesmo limiar ──
 // separada por unidade: watts com watts, bpm com bpm
 const co = PMEXT.coerencia_por_grupo || {};
 let h='';
 if(Object.keys(co).length){
  h+='<table style="width:100%;border-collapse:collapse;font-size:12px;margin-bottom:8px;">'
   +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
   +'<th style="padding:6px;">Limiar</th><th>Unidade</th><th>Estimativas</th>'
   +'<th>Intervalo</th><th>Consenso</th><th>Amplitude</th><th>Modelo</th></tr>';
  Object.keys(co).forEach(function(k){
   const c=co[k];
   ['em_watts','em_bpm'].forEach(function(bk, bi){
    const b=c[bk]; if(!b) return;
    const disc = (b.discrepantes||[]).map(function(d){
      return d.campo+' '+d.valor+' ('+(d.desvio_pct>0?'+':'')+d.desvio_pct
        +'%'+(d.n!=null?', n='+d.n:'')+')'; }).join(' · ');
    const ap=b.amplitude_pct;
    const cor = ap==null ? '#8b949e' : ap<10 ? '#3FB950' : ap<25 ? '#F0883E' : '#F85149';
    h+='<tr style="border-bottom:1px solid #161b22;">'
     +'<td style="padding:6px;">'+(bi===0?c.rotulo:'')+'</td>'
     +'<td style="color:'+(b.unidade==='W'?'#c9d1d9':'#79C0FF')+';">'+b.unidade+'</td>'
     +'<td style="color:#8b949e;">'+b.detalhe.map(function(d){
         return d.campo+' '+d.valor; }).join(' · ')+'</td>'
     +'<td style="color:#8b949e;">'+b.min+'–'+b.max+'</td>'
     +'<td>'
     +(b.transicao
       ? '<b>'+b.transicao.inicio.valor+' → '+b.transicao.fim.valor+'</b>'
         +'<br><span style="color:#8b949e;font-size:10px;">transição de '
         +b.transicao.largura+' '+b.unidade+' ('+b.transicao.largura_pct+'%)<br>'
         +'início: '+b.transicao.inicio.metodos.join(', ')+'<br>'
         +'fim: '+b.transicao.fim.metodos.join(', ')+'</span>'
       : '<b>'+((b.consenso&&b.consenso.valor!=null)?b.consenso.valor:b.mediana)+'</b>')
     +(b.consenso && !b.transicao
       ? '<br><span style="color:#8b949e;font-size:10px;">'+b.consenso.n_metodos
         +' método'+(b.consenso.n_metodos===1?'':'s')
         +(b.consenso.n_metodos_fracos_excluidos
           ? ' · '+b.consenso.n_metodos_fracos_excluidos+' fraco'
             +(b.consenso.n_metodos_fracos_excluidos===1?'':'s')+' fora' : '')
         +'</span>' : '')
     +'</td>'
     +'<td style="color:'+cor+';">'+b.amplitude+(ap!=null?' ('+ap+'%)':'')+'</td>'
     +'<td>'+(bi===0 && c.modelo_w!=null ? c.modelo_w+' W' : '')+'</td></tr>'
     +(disc ? '<tr><td></td><td></td><td colspan="5" style="color:#F0883E;'
       +'font-size:10px;padding-bottom:4px;">fora do consenso: '+disc
       +'</td></tr>' : '');
   });
  });
  h+='</table>';
  h+='<p style="color:#8b949e;font-size:11px;margin:-4px 0 10px 0;">'
   +'Quando os métodos se separam em dois grupos com mais de 20% entre eles, '
   +'a coluna mostra <b>início → fim</b> em vez de um consenso. Um limiar não '
   +'é um ponto: é uma transição com largura, e métodos diferentes marcam '
   +'extremos diferentes dela. Forçar uma média entre os dois esconde '
   +'exactamente a informação que interessa para treinar.</p>';
 }
 const relF = rel.fiavel;
 h+='<p style="color:'+(relF?'#8b949e':'#F0883E')+';font-size:11px;margin:-4px 0 14px 0;">'
  + (relF
     ? 'Cada linha junta métodos independentes que apontam ao mesmo limiar, '
       + 'separados pela unidade em que foram medidos. Campos em watts nunca '
       + 'são comparados com campos em bpm: seria comparar uma medição com '
       + 'uma conversão.'
     : 'A recta HR↔Watts tem r²=' + (rel.r2!=null?rel.r2:'?') + ', abaixo do '
       + 'mínimo de ' + (rel.r2_minimo||0.5) + ' para converter. As conversões '
       + 'estão desligadas: cada campo aparece só na unidade em que foi '
       + 'medido. Converter com esta recta amplificava ruído — 11 bpm de '
       + 'diferença viravam 80 W, e o intervalo do limiar aparecia com 86% de '
       + 'amplitude quando os campos em watts concordavam a 1%.')
  + '</p>';

 // ── DFA-a1 desta modalidade, contra o 0.75 da literatura ──
 const ai = PMEXT.a1_individualizado || {};
 const ap = ai.a1_no_ponto;
 if(ap && ap.ok){
  const rep = ap.repetivel;
  h += '<div style="border:1px solid '+(rep?'#3FB950':'#F0883E')+';'
   + 'border-radius:6px;padding:8px 10px;margin-bottom:12px;'
   + 'background:rgba(121,192,255,0.05);">'
   + '<b style="color:#79C0FF;">DFA-a1 no ponto de inflexão — '
   + (PMEXT.modalidade||'') + '</b><br>'
   + '<span style="font-size:20px;color:#c9d1d9;">' + ap.mediana + '</span>'
   + '<span style="color:#8b949e;font-size:11px;"> (p25–p75 ' + ap.p25 + '–'
   + ap.p75 + ' · n=' + ap.n + ' aquecimentos · IQR '
   + (ap.iqr_relativo_pct!=null?ap.iqr_relativo_pct+'%':'?') + ')</span>'
   + '<span style="color:' + (rep?'#3FB950':'#F0883E') + ';font-size:11px;"> · '
   + (rep?'repetível':'NÃO repetível') + '</span>'
   + '<br><span style="color:#8b949e;font-size:11px;">'
   + 'A literatura usa <b>0,75</b> como alvo de VT1 para toda a gente. Este é '
   + 'o valor medido nas tuas escadas de aquecimento desta modalidade'
   + (ai.a1_inflexao_W && ai.a1_inflexao_W.ok
      ? ', que corresponde a <b>' + ai.a1_inflexao_W.mediana + ' W</b>' : '')
   + (ai.a1_inflexao_bpm && ai.a1_inflexao_bpm.ok
      ? ' e <b>' + ai.a1_inflexao_bpm.mediana + ' bpm</b>' : '')
   + '. ' + (rep
      ? 'É repetível entre aquecimentos, portanto serve de alvo individual.'
      : 'O intervalo interquartil passa os 15% da mediana: com este n não '
        + 'serve ainda como alvo, só como indicação.')
   + '</span></div>';
 } else if(ai && ai.erro){
  h += '<p style="color:#8b949e;font-size:11px;">DFA-a1 do aquecimento '
   + 'indisponível: ' + ai.erro + '</p>';
 }

 // ── tabela por grupo ──
 let grupoActual = null;
 h+='<table style="width:100%;border-collapse:collapse;font-size:12px;">'
  +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
  +'<th style="padding:6px;">Campo</th><th>n</th><th>p25</th><th>Mediana</th>'
  +'<th>p75</th><th>Watts</th><th>bpm</th><th>Último</th>'
  +'<th>Modelo</th><th>Δ</th></tr>';
 cs.forEach(function(c){
  if(c.grupo !== grupoActual){
   grupoActual = c.grupo;
   h+='<tr><td colspan="10" style="padding:10px 6px 4px 6px;color:#58A6FF;'
    +'font-size:11px;text-transform:uppercase;letter-spacing:0.5px;">'
    +(c.grupo_rotulo||c.grupo||'outros')+'</td></tr>';
  }
  const q=c.quartis||{}, u=c.ultimo||{}, cm=c.comparacao;
  let dcor='#8b949e', dtxt='—';
  if(cm){
   const ap=Math.abs(cm.diferenca_pct);
   dcor = ap<10 ? '#3FB950' : ap<25 ? '#F0883E' : '#F85149';
   dtxt = (cm.diferenca_pct>0?'+':'')+cm.diferenca_pct+'%';
  }
  h+='<tr style="border-bottom:1px solid #161b22;">'
   +'<td style="padding:6px;border-bottom:1px dotted #30363d;cursor:help;" '
   +'title="'+(c.descricao||'').replace(/"/g,'&quot;')
   +(c.compara_com?'\n\ncompara com: '+c.compara_com:'')
   +(c.origem?'\norigem: '+c.origem:'')+'">'+c.rotulo
   +(c.constante ? ' <span style="color:#F0883E;font-size:10px;" title="igual '
     +'em todas as sessões — é uma definição do perfil, não uma medição">'
     +'definição</span>' : '')
   +(c.usou_historico_por_falta_na_season
     ? ' <span style="color:#F0883E;font-size:10px;">histórico</span>' : '')
   +(c.origem === 'escadas de aquecimento'
     ? ' <span style="color:#79C0FF;font-size:10px;" title="calculado das '
       + 'escadas de aquecimento deste atleta, não é campo da Intervals.icu">'
       + 'aquecimento' + (c.repetivel === false ? ' · não repetível' : '')
       + (c.iqr_relativo_pct != null ? ' IQR ' + c.iqr_relativo_pct + '%' : '')
       + '</span>' : '')
   +'</td>'
   +'<td style="color:#8b949e;" title="'
   +(c.n_no_historico!=null? c.n_no_historico+' no histórico' : '')+'">'
   +(q.n!=null?q.n:'—')
   +(c.n_no_historico!=null && c.n_no_historico>q.n
     ? ' <span style="color:#6e7681;font-size:10px;">/'+c.n_no_historico+'</span>' : '')
   +'</td>'
   +'<td style="color:#8b949e;">'+(q.p25!=null?q.p25:'—')+'</td>'
   +'<td><b>'+(q.p50!=null?q.p50:'—')+'</b> '
   +'<span style="color:#8b949e;font-size:10px;">'+(c.unidade||'')+'</span></td>'
   +'<td style="color:#8b949e;">'+(q.p75!=null?q.p75:'—')+'</td>'
   +'<td style="color:'+(c.watts_medido!=null?'#c9d1d9':'#6e7681')+';">'
   +(c.watts_medido!=null?Math.round(c.watts_medido)
     : c.watts_convertido!=null?'('+Math.round(c.watts_convertido)+')':'—')+'</td>'
   +'<td style="color:'+(c.hr_medido!=null?'#c9d1d9':'#6e7681')+';">'
   +(c.hr_medido!=null?Math.round(c.hr_medido)
     : c.hr_convertido!=null?'('+Math.round(c.hr_convertido)+')':'—')+'</td>'
   +'<td style="color:#8b949e;">'+(u.valor!=null?u.valor+' · '+(u.data||''):'—')+'</td>'
   +'<td>'+(cm?cm.modelo:'—')+'</td>'
   +'<td style="color:'+dcor+';">'+dtxt+'</td></tr>';
 });
 h+='</table><p style="color:#8b949e;font-size:11px;margin-top:6px;">'
  +'Valores entre parênteses são conversões, não medições. O Δ só é '
  +'calculado quando há valor medido na unidade do modelo. Recta HR↔Watts'
  +(rel.suficiente ? ' (r²='+rel.r2+', n='+rel.n
     +(rel.descartados?', '+rel.descartados+' pares descartados por FC implausível':'')
     +')' : ' — que aqui não existe')
  +'. Δ = mediana menos o valor do modelo, em %.</p>';
 box.innerHTML=h;
}

let PMEXT_ESCALA = null, PMEXT_ITENS = [];

function pmMarca(g, x, y, cor, forma, oco){
 g.strokeStyle=cor; g.fillStyle=oco ? 'transparent' : cor; g.lineWidth=1.8;
 g.beginPath();
 if(forma === 'estrela'){
  for(let i=0;i<10;i++){
   const r = i%2 ? 3 : 7, a = -Math.PI/2 + i*Math.PI/5;
   const px = x + r*Math.cos(a), py = y + r*Math.sin(a);
   i ? g.lineTo(px,py) : g.moveTo(px,py);
  }
  g.closePath();
 } else {
  g.arc(x, y, 5, 0, Math.PI*2);
 }
 if(!oco) g.fill();
 g.stroke(); g.lineWidth=1;
}

function pmExtDraw(){
 const o = ctx('chExternos', 320);
 if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const rel = (PMEXT && PMEXT.relacao_hr_watts) || {};
 const nuvem = (PMEXT && PMEXT.nuvem_hr_watts) || [];
 // basta ter sido medido num dos eixos: cada um é desenhado no seu
 // filtros: sem eles o grafico fica ilegivel com 15 campos
 const _on = {};
 Array.prototype.forEach.call(document.querySelectorAll('.pmExtG'), function(e){
  _on[e.value] = e.checked; });
 if(!Object.keys(_on).length){
  ['modelo','aerobio','limiar','vo2max','nuvem'].forEach(k=>_on[k]=true); }
 const cs = ((PMEXT && PMEXT.campos) || []).filter(function(c){
   return (c.watts_medido!=null || c.hr_medido!=null)
     && _on[c.grupo]
     && (_on.fixos || !c.constante); });
 const md = (PMEXT && PMEXT.modelo) || {};
 const mdhr = (PMEXT && PMEXT.modelo_em_hr) || {};
 const refs = [
  {k:'fatmax_w', rot:'FatMax', cor:'#A371F7'},
  {k:'lt1_w',    rot:'LT1',    cor:'#3FB950'},
  {k:'mlss_at_w',rot:'MLSS',   cor:'#58A6FF'},
  {k:'lt2_w',    rot:'LT2',    cor:'#F85149'},
  {k:'pvo2max_w',rot:'Pvo\u2082max', cor:'#79C0FF'},
 ].filter(function(r){ return md[r.k]!=null && mdhr[r.k]!=null; });
 if(PM_CP) refs.push({k:'__cp', rot:'CP', cor:'#E3B341'});
 if(!_on.modelo) refs.length = 0;

 if(!nuvem.length && !cs.length && !refs.length){
  noData(g, W, H, 'Sem relação HR↔Watts nem campos comparáveis'); return; }

 const valW = r => r.k==='__cp' ? PM_CP : md[r.k];
 const valHR = r => r.k==='__cp'
   ? (rel.suficiente ? rel.declive_bpm_por_w*PM_CP + rel.intercepto_bpm : null)
   : mdhr[r.k];

 let xs=[], ys=[];
 nuvem.forEach(function(p){ xs.push(p.w); ys.push(p.hr); });
 cs.forEach(function(c){
  if(c.watts_medido!=null) xs.push(c.watts_medido);
  if(c.hr_medido!=null) ys.push(c.hr_medido);
 });
 refs.forEach(function(r){ if(valW(r)!=null){ xs.push(valW(r)); ys.push(valHR(r)); }});
 // O eixo X ia ate' ao maior watt da nuvem -- 588 W no Row, onde nao ha
 // nada depois dos 333 W. Isso comprimia a zona moderada e pesada, que e'
 // onde vivem todos os limiares, num terco da largura. Corta-se no p98 da
 // nuvem ou um pouco acima do marco mais alto, o que for maior.
 function _pct(v, q){
  if(!v.length) return null;
  const o = v.slice().sort(function(a,b){ return a-b; });
  return o[Math.min(o.length-1, Math.floor(q*(o.length-1)))];
 }
 const wsNuvem = nuvem.map(function(p){ return p.w; });
 const marcosW = [];
 cs.forEach(function(c){ if(c.watts_medido!=null) marcosW.push(c.watts_medido); });
 refs.forEach(function(r){ const v=valW(r); if(v!=null) marcosW.push(v); });
 const p98 = _pct(wsNuvem, 0.98);
 const tectoMarcos = marcosW.length ? Math.max.apply(null, marcosW)*1.12 : null;
 let xb = Math.max.apply(null, xs)*1.06;
 const corte = Math.max(p98||0, tectoMarcos||0);
 if(corte > 0 && corte < xb) xb = corte;
 const xa = Math.min.apply(null,xs)*0.92;
 const ya=Math.min.apply(null,ys)*0.94, yb=Math.max.apply(null,ys)*1.05;

 const PL=54, PR=118, PT=30, PB=40, w=W-PL-PR, h=H-PT-PB;
 const X = v => PL + (v-xa)/((xb-xa)||1)*w;
 const Y = v => PT + h - (v-ya)/((yb-ya)||1)*h;
 PMEXT_ESCALA = {X:X, Y:Y, PL:PL, PT:PT, w:w, h:h, xa:xa, xb:xb, ya:ya, yb:yb, rel:rel};

 // bandas dos três domínios, iguais às do gráfico de substratos
 // As bandas vem da TRANSICAO medida, quando existe, e nao do ponto
 // unico do modelo: uma transicao com 55 W de largura desenhada como uma
 // linha esconde precisamente o que interessa.
 const co0 = PMEXT.coerencia_por_grupo || {};
 function _faixa(grupo){
  const b = (co0[grupo]||{}).em_watts;
  if(!b) return null;
  if(b.transicao) return [b.transicao.inicio.valor, b.transicao.fim.valor];
  if(b.consenso && b.consenso.valor!=null)
   return [b.consenso.valor, b.consenso.valor];
  return null;
 }
 const fA = _faixa('aerobio'), fB = _faixa('limiar');
 const limA = fA ? fA[0] : md.lt1_w, limB = fB ? fB[0] : (md.mlss_at_w || md.lt2_w);
 PMEXT_ZONAS = [];
 if(limA && limB){
  PMEXT_ZONAS = [
   {de:xa, ate:limA, nome:'Moderado', cor:'rgba(63,185,80,0.07)',  rot:'#3FB950'},
   {de:limA, ate:limB, nome:'Pesado', cor:'rgba(240,136,62,0.07)', rot:'#F0883E'},
   {de:limB, ate:xb, nome:'Severo',   cor:'rgba(248,81,73,0.07)',  rot:'#F85149'}];
  PMEXT_ZONAS.forEach(function(z){
   const x0=X(Math.max(z.de,xa)), x1=X(Math.min(z.ate,xb));
   if(x1<=x0) return;
   g.fillStyle=z.cor; g.fillRect(x0, PT, x1-x0, h);
   g.fillStyle=z.rot; g.font='10px sans-serif'; g.textAlign='center';
   if(x1-x0>52) g.fillText(z.nome, (x0+x1)/2, PT-16);
  });
  // a largura das transicoes, sombreada por cima das zonas
  [[fA,'#3FB950','transição aeróbia'],[fB,'#F0883E','transição do limiar']]
   .forEach(function(t){
    if(!t[0] || t[0][0]===t[0][1]) return;
    const x0=X(Math.max(t[0][0],xa)), x1=X(Math.min(t[0][1],xb));
    if(x1<=x0) return;
    g.fillStyle=t[1]; g.globalAlpha=0.13; g.fillRect(x0, PT, x1-x0, h);
    g.globalAlpha=1;
    g.strokeStyle=t[1]; g.globalAlpha=0.55; g.lineWidth=1.5;
    g.beginPath(); g.moveTo(x0,PT+h+4); g.lineTo(x1,PT+h+4); g.stroke();
    g.globalAlpha=1; g.lineWidth=1;
    g.fillStyle=t[1]; g.font='9px sans-serif'; g.textAlign='center';
    if(x1-x0>70) g.fillText(Math.round(t[0][0])+'–'+Math.round(t[0][1])+'W',
                            (x0+x1)/2, PT+h+14);
   });
 }

 g.strokeStyle='#21262d'; g.lineWidth=1; g.fillStyle='#8b949e';
 g.font='11px sans-serif';
 for(let i=0;i<=4;i++){
  const yv=ya+(yb-ya)*i/4, y=Y(yv);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText(Math.round(yv)+' bpm', PL-6, y+4);
 }
 for(let i=0;i<=4;i++){
  const xv=xa+(xb-xa)*i/4, x=X(xv);
  g.textAlign='center'; g.fillText(Math.round(xv)+'W', x, PT+h+18);
 }

 if(_on.nuvem){
  g.fillStyle='rgba(139,148,158,0.22)';
  nuvem.forEach(function(p){
   g.beginPath(); g.arc(X(p.w), Y(p.hr), 1.6, 0, Math.PI*2); g.fill(); });
 }

 if(rel.suficiente && rel.fiavel){
  const a=rel.declive_bpm_por_w, b=rel.intercepto_bpm;
  g.strokeStyle='#8b949e'; g.setLineDash([6,4]); g.lineWidth=1.5;
  g.beginPath(); g.moveTo(X(xa), Y(a*xa+b)); g.lineTo(X(xb), Y(a*xb+b));
  g.stroke(); g.setLineDash([]); g.lineWidth=1;
 }

 // verticais e horizontais dos marcos do modelo
 PMEXT_ITENS = [];
 const ocupado = [];
 refs.slice().sort((a,b)=> valW(a)-valW(b)).forEach(function(r){
  const wv = valW(r), hv = valHR(r);
  if(wv==null || wv<xa || wv>xb) return;
  const x=X(wv);
  g.strokeStyle=r.cor; g.setLineDash([5,4]); g.lineWidth=1.2;
  g.beginPath(); g.moveTo(x,PT); g.lineTo(x,PT+h); g.stroke();
  if(hv!=null && rel.fiavel){
   const y=Y(hv);
   g.globalAlpha=0.5;
   g.beginPath(); g.moveTo(PL,y); g.lineTo(x,y); g.stroke();
   g.globalAlpha=1;
  }
  g.setLineDash([]); g.lineWidth=1;
  const txt = r.rot+' '+Math.round(wv)+'W';
  g.font='10px sans-serif';
  const larg=g.measureText(txt).width;
  let nivel=0;
  while(ocupado.some(o=>o.nivel===nivel && x-larg/2 < o.fim+6)) nivel++;
  ocupado.push({nivel:nivel, fim:x+larg/2});
  const yTxt = PT + 11 + nivel*12;
  g.fillStyle='#0d1117'; g.fillRect(x-larg/2-2, yTxt-9, larg+4, 12);
  g.fillStyle=r.cor; g.textAlign='center'; g.fillText(txt, x, yTxt);
  if(hv!=null) PMEXT_ITENS.push({x:x, y:Y(hv), rot:r.rot, cor:r.cor,
    w:wv, hr:hv, tipo:'modelo'});
 });

 // Cada campo é desenhado no eixo em que foi MEDIDO: os que vêm em watts
 // como risca vertical, os que vêm em bpm como risca horizontal. Sem recta
 // fiável não há ponto (x,y) — desenhar um seria inventar a coordenada que
 // falta e apresentá-la com a mesma autoridade da que foi medida.
 const ocupadoW = [], ocupadoH = [];
 cs.forEach(function(c){
  const cor = c.constante ? '#8b949e'
            : c.origem === 'escadas de aquecimento' ? '#79C0FF' : '#E3B341';
  const rot = c.rotulo + (c.constante ? ' *' : '');
  g.font='10px sans-serif';

  if(c.watts_medido != null && c.watts_medido >= xa && c.watts_medido <= xb){
   // Ponto no eixo, sem etiqueta: com 15 campos as etiquetas permanentes
   // tornavam o gráfico ilegível. O nome e os números vêm no hover.
   const x = X(c.watts_medido), y = PT+h-10;
   pmMarca(g, x, y, cor, 'ponto', c.constante);
   g.strokeStyle=cor; g.globalAlpha=0.30; g.setLineDash([3,4]);
   g.beginPath(); g.moveTo(x, PT); g.lineTo(x, y-7); g.stroke();
   g.setLineDash([]); g.globalAlpha=1;
   PMEXT_ITENS.push({x:x, y:y, rot:c.rotulo, cor:cor,
     w:c.watts_medido, hr:c.hr_convertido, tipo:'campo', eixo:'W',
     unidade:c.unidade, constante:c.constante, grupo:c.grupo_rotulo,
     descricao:c.descricao, origem:c.origem, q:c.quartis});
  }

  if(c.hr_medido != null && c.hr_medido >= ya && c.hr_medido <= yb){
   const y = Y(c.hr_medido), x = PL+12;
   pmMarca(g, x, y, cor, 'estrela', c.constante);
   g.strokeStyle=cor; g.globalAlpha=0.30; g.setLineDash([3,4]);
   g.beginPath(); g.moveTo(x+7, y); g.lineTo(PL+w, y); g.stroke();
   g.setLineDash([]); g.globalAlpha=1;
   PMEXT_ITENS.push({x:x, y:y, rot:c.rotulo, cor:cor,
     w:c.watts_convertido, hr:c.hr_medido, tipo:'campo', eixo:'bpm',
     unidade:c.unidade, constante:c.constante, grupo:c.grupo_rotulo,
     descricao:c.descricao, origem:c.origem, q:c.quartis});
  }
 });

 const foraDoCorte = wsNuvem.filter(function(v){ return v > xb; }).length;
 g.textAlign='left'; g.font='10px sans-serif';
 g.fillStyle='#8b949e'; g.fillText('\u2502 modelo', PL+w+8, PT+12);
 g.fillStyle='#E3B341';
 g.fillText('\u25CF medido em W', PL+w+8, PT+26);
 g.fillText('\u2605 medido em bpm', PL+w+8, PT+40);
 if(((PMEXT&&PMEXT.campos)||[]).some(function(c){
     return c.origem==='escadas de aquecimento'; })){
  g.fillStyle='#79C0FF';
  g.fillText('\u2500 DFA-a1 aquecimento', PL+w+8, PT+82);
 }
 g.fillStyle='#8b949e';
 g.fillText(!rel.suficiente ? 'sem recta'
            : rel.fiavel ? 'r\u00b2='+rel.r2+' n='+rel.n
            : 'r\u00b2='+rel.r2+' — conversão desligada',
            PL+w+8, PT+40);
 if(foraDoCorte){
  g.fillStyle='#6e7681';
  g.fillText(foraDoCorte + ' pontos > ' + Math.round(xb) + 'W', PL+w+8, PT+96);
 }
 if(rel.suficiente && !rel.fiavel){
  g.fillStyle='#F0883E';
  g.fillText('\u2502 medido em W', PL+w+8, PT+54);
  g.fillText('\u2500 medido em bpm', PL+w+8, PT+68);
 }
 g.font='11px sans-serif';
 pmExtLigarTip();
}

let PMEXT_ZONAS = [];

function pmExtLigarTip(){
 const cv=document.getElementById('chExternos');
 const tip=document.getElementById('pmExtTip');
 if(!cv||!tip||cv._tipExt) return;
 cv._tipExt=true;
 cv.addEventListener('mousemove', function(ev){
  const r=cv.getBoundingClientRect();
  const esc=(cv.width/r.width)/(window.devicePixelRatio||1);
  const mx=(ev.clientX-r.left)*esc, my=(ev.clientY-r.top)*esc;
  if(!PMEXT_ESCALA){ tip.style.display='none'; return; }
  const e=PMEXT_ESCALA;
  let perto=null, dmin=16;
  PMEXT_ITENS.forEach(function(p){
   const d=Math.hypot(p.x-mx, p.y-my);
   if(d<dmin){ dmin=d; perto=p; }
  });
  if(perto){
   let h='<b style="color:'+perto.cor+';">'+perto.rot+'</b>';
   h+='<br>'+Math.round(perto.w)+' W · '+Math.round(perto.hr)+' bpm';
   if(perto.tipo==='campo'){
    h+='<br><span style="color:#8b949e;font-size:10px;">'+(perto.grupo||'')
     +(perto.origem?' · '+perto.origem:'')+'</span>';
    if(perto.descricao) h+='<br><span style="color:#8b949e;font-size:10px;">'
     +perto.descricao.slice(0,150)+'</span>';
    if(perto.q) h+='<br><span style="color:#8b949e;">p25–p75 '+perto.q.p25
      +'–'+perto.q.p75+' '+(perto.unidade||'')+' · n='+perto.q.n+'</span>';
    h+='<br><span style="color:#8b949e;font-size:10px;">medido em '
      +(perto.medido||'').replace(/[()]/g,'').trim()
      +(perto.constante?' · definição fixa':'')+'</span>';
   } else {
    h+='<br><span style="color:#8b949e;font-size:10px;">valor do modelo</span>';
   }
   tip.innerHTML=h; tip.style.display='block';
   tip.style.left=Math.min(ev.clientX-r.left+14, r.width-200)+'px';
   tip.style.top=Math.max(4, ev.clientY-r.top-40)+'px';
   return;
  }
  // fora de um ponto: mostrar a leitura da recta naquele X
  if(mx<e.PL||mx>e.PL+e.w||my<e.PT||my>e.PT+e.h){ tip.style.display='none'; return; }
  const watts = e.xa + (mx-e.PL)/e.w*(e.xb-e.xa);
  const bpm = e.ya + (e.PT+e.h-my)/e.h*(e.yb-e.ya);
  const prev = e.rel.suficiente
    ? e.rel.declive_bpm_por_w*watts + e.rel.intercepto_bpm : null;
  const z=(PMEXT_ZONAS||[]).find(z=>watts>=z.de && watts<z.ate);
  let h='<b>'+Math.round(watts)+' W · '+Math.round(bpm)+' bpm</b>';
  if(z) h+=' <span style="color:'+z.rot+';">'+z.nome+'</span>';
  if(prev!=null) h+='<br><span style="color:#8b949e;">a recta prevê '
    +Math.round(prev)+' bpm aqui ('+(bpm>prev?'+':'')+Math.round(bpm-prev)+')</span>';
  tip.innerHTML=h; tip.style.display='block';
  tip.style.left=Math.min(ev.clientX-r.left+14, r.width-200)+'px';
  tip.style.top=Math.max(4, ev.clientY-r.top-30)+'px';
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

function pmExtGlossario(){
 const cs=(PMEXT&&PMEXT.campos)||[];
 let h='';
 cs.forEach(function(c){
  h+='<p style="font-size:11px;color:#8b949e;margin:4px 0;"><b style="color:#c9d1d9;">'
   +c.rotulo+'</b> ('+(c.unidade||'')+')'
   +(c.compara_com?' · compara com <code>'+c.compara_com+'</code>':' · sem equivalente no modelo')
   +'<br>'+c.descricao+'</p>';
 });
 if((PMEXT.campos_nao_encontrados||[]).length)
  h+='<p style="font-size:11px;color:#F0883E;">Não encontrados: '
   +PMEXT.campos_nao_encontrados.join(', ')
   +'. Ou não estão configurados na Intervals.icu, ou o nome difere dos '
   +'aliases em <code>CAMPOS_EXTERNOS</code>.</p>';
 const dup=PMEXT.campos_duplicados||{};
 if(Object.keys(dup).length){
  h+='<p style="font-size:11px;color:#8b949e;">Nomes que caem na mesma '
   +'definição e foram deixados de fora para não contar duas vezes: '
   +Object.keys(dup).map(function(k){return k+' ← '+dup[k].join(', ');}).join(' · ')
   +'</p>';
 }
 const nr=PMEXT.campos_por_reconhecer||[];
 if(nr.length)
  h+='<p style="font-size:11px;color:#8b949e;">Campos numéricos presentes e '
   +'ainda sem definição — se algum for o EBP ou a Fractional Utilization, '
   +'basta acrescentar o nome como alias: <code>'+nr.join('</code>, <code>')
   +'</code></p>';
 document.getElementById('pmExtGlossario').innerHTML=h;
}

function pmCartao(titulo, valor, sub, cor){
 return '<div style="flex:1;min-width:150px;background:#0d1117;border:1px solid #21262d;'
  + 'border-radius:6px;padding:10px 12px;margin:4px;">'
  + '<div style="color:#8b949e;font-size:11px;">' + titulo + '</div>'
  + '<div style="font-size:20px;font-weight:600;color:' + (cor||'#c9d1d9') + ';">' + valor + '</div>'
  + (sub ? '<div style="color:#8b949e;font-size:11px;margin-top:2px;">' + sub + '</div>' : '')
  + '</div>';
}

function pmResumo(){
 // declarado no topo: era const a meio da funcao e o cartao do MLSS,
 // que vem antes, caia na zona morta temporal -- o erro "Cannot access
 // '_pc' before initialization" matava o pmResumo inteiro e por isso
 // desapareciam todos os cartoes, nao so' o pace
 const pc = (PM && PM.pace) || {};
 const _pc = function(k){
  return pc[k] && pc[k].texto ? ' \u00b7 ' + pc[k].texto : '';
 };
 const m = PM.mader || {};
 let h = '<div style="display:flex;flex-wrap:wrap;">';
 h += pmCartao('VO\u2082max', (PM.vo2max||'—') + ' <span style="font-size:12px;">ml/min/kg</span>',
               PM.vo2max_validade, '#58A6FF');
 h += pmCartao('VLamax', (PM.vlamax||'—') + ' <span style="font-size:12px;">mmol/L/s</span>',
               PM.perfil + (PM.vlamax_saturado ? ' \u26A0 no limite do modelo' : ''), '#F0883E');
 h += pmCartao('MLSS / AT', (m.mlss_at_w||'—') + ' W' + _pc('mlss_at_w'),
               m.pct_vo2max_at ? m.pct_vo2max_at + '% do VO\u2082max' : '', '#3FB950');
 if(PM_CP_INFO && PM_CP_INFO.cp_w){
  const i = PM_CP_INFO;
  h += pmCartao('CP', Math.round(i.cp_w) + ' W',
    (i.wp_kj!=null ? "W\u2032 " + i.wp_kj + ' kJ · ' : '')
    + (i.modelo||'') + (i.confirmado ? '' : ' · não confirmado')
    + (m.mlss_at_w ? ' · ' + (i.cp_w>m.mlss_at_w?'+':'')
       + Math.round(i.cp_w-m.mlss_at_w) + ' W vs MLSS' : ''),
    i.confirmado ? '#E3B341' : '#8b949e');
 }
 if(m.pvo2max_w){
  const fu = m.fractional_utilization_pct;
  const fuNota = fu==null ? '' :
    (fu<75 ? 'tecto alto, chão baixo — trabalhar limiar'
     : fu<=85 ? 'intervalo habitual em endurance treinado'
     : 'chão encostado ao tecto — trabalhar VO\u2082max');
  h += pmCartao('Pvo\u2082max (MAP)', m.pvo2max_w + ' W',
                (fu!=null ? 'utilização fraccional ' + fu + '% · ' : '') + fuNota,
                '#A371F7');
 }
 h += pmCartao('FatMax', (m.fatmax_w||'—') + ' W',
               (m.pct_vo2max_fatmax ? m.pct_vo2max_fatmax + '% VO\u2082max' : '')
               + (m.fatmax_pct_mlss ? ' · ' + m.fatmax_pct_mlss + '% do MLSS' : ''), '#A371F7');
 h += pmCartao('Gordura no FatMax', (m.fat_no_fatmax_g_h||'—') + ' g/h', '');
 h += pmCartao('CHO no MLSS', (m.cho_no_at_g_h||'—') + ' g/h', '');
 const lim = PM.limiares || {};
 if(lim.lt1_w) h += pmCartao('LT1 (aeróbio)', lim.lt1_w + ' W' + _pc('lt1_w'),
   (lim.lt1_lactato!=null? lim.lt1_lactato + ' mmol/L' : '')
   + (lim.lt1_convencao_w!=null && lim.lt1_convencao_w!==lim.lt1_w
      ? ' · convenção +0,5 mmol/L daria ' + lim.lt1_convencao_w + ' W' : ''),
   '#3FB950');
 if(lim.lt2_w) h += pmCartao('LT2 (anaeróbio)', lim.lt2_w + ' W',
   (lim.lt2_lactato!=null? lim.lt2_lactato + ' mmol/L' : '')
   + (lim.lt2_vs_mlss_w!=null? ' · ' + (lim.lt2_vs_mlss_w>0?'+':'') + lim.lt2_vs_mlss_w + 'W vs MLSS' : ''),
   '#F85149');
 if(m.glicogenio) h += pmCartao('Glicogénio', m.glicogenio.total_g + ' g',
   m.glicogenio.nivel + ' · ' + m.glicogenio.musculo_kg + ' kg músculo');
 h += '</div>';
 document.getElementById('pmResumo').innerHTML = h;
}

let PM_PONTOS = [], PM_ESCALA = null, PM_ZONAS = [], PM_MARCOS = [];

function pmDraw(){
 const o = ctx('chSubstratos', 340);
 if(!o) return;
 const g = o.g, W = o.W, H = o.H;
 const curva = (PM && PM.mader && PM.mader.curva) || [];
 if(!curva.length){ noData(g, W, H, 'Sem dados'); return; }

 const m = PM.mader||{}, lm = PM.limiares||{};

 // Janela: começar no FatMax menos uma folga em vez de em 3 W. O varrimento
 // arranca praticamente do repouso, e mostrar isso tudo esmaga a zona que
 // interessa (140–260 W) nos últimos 20% do gráfico — foi por isso que os
 // marcos pareciam não existir: estavam todos empilhados na borda direita.
 const xsTodos = curva.map(p=>p.watts);
 const xbAll = Math.max.apply(null,xsTodos);
 const ancora = m.fatmax_w || lm.lt1_w || xbAll*0.4;
 const xa = Math.max(0, Math.min(ancora*0.45, xbAll*0.25));
 const xb = xbAll;
 const dentro = p => p.watts>=xa && p.watts<=xb;
 const vis = curva.filter(dentro);
 const maxG = Math.max.apply(null, vis.map(p=>Math.max(p.fat_g_h||0, p.cho_g_h||0))) || 1;

 const PL=62, PR=62, PB=46, PT=44, w=W-PL-PR, h=H-PT-PB;
 const X = v => PL + (v-xa)/((xb-xa)||1)*w;
 const Y = v => PT + h - v/maxG*h;
 PM_ESCALA = {X:X, Y:Y, PL:PL, PT:PT, w:w, h:h, xa:xa, xb:xb, maxG:maxG};

 // ── bandas dos três domínios de intensidade ───────────────────────────
 // Moderado abaixo do LT1, pesado entre LT1 e MLSS, severo acima. Não são
 // decoração: é a divisão que determina se o lactato estabiliza, se
 // estabiliza mais alto, ou se não estabiliza de todo.
 const limA = lm.lt1_w, limB = m.mlss_at_w || lm.lt2_w;
 PM_ZONAS = [];
 if(limA && limB){
  PM_ZONAS = [
   {de:xa,   ate:limA, nome:'Moderado', cor:'rgba(63,185,80,0.07)',  rot:'#3FB950',
    desc:'lactato estabiliza — sustentável horas'},
   {de:limA, ate:limB, nome:'Pesado',   cor:'rgba(240,136,62,0.07)', rot:'#F0883E',
    desc:'lactato estabiliza mais alto — sustentável dezenas de minutos'},
   {de:limB, ate:xb,   nome:'Severo',   cor:'rgba(248,81,73,0.07)',  rot:'#F85149',
    desc:'lactato não estabiliza — termina em exaustão'},
  ];
  PM_ZONAS.forEach(function(z){
   const x0=X(Math.max(z.de,xa)), x1=X(Math.min(z.ate,xb));
   if(x1<=x0) return;
   g.fillStyle=z.cor; g.fillRect(x0, PT, x1-x0, h);
   g.fillStyle=z.rot; g.font='10px sans-serif'; g.textAlign='center';
   if(x1-x0>52) g.fillText(z.nome, (x0+x1)/2, PT-6);
  });
 }

 // ── grelha ────────────────────────────────────────────────────────────
 g.strokeStyle='#21262d'; g.lineWidth=1; g.fillStyle='#8b949e';
 g.font='11px sans-serif'; g.textAlign='right';
 for(let k=0;k<=4;k++){
  const y=PT+h*k/4;
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.fillText(Math.round(maxG-maxG*k/4)+'', PL-8, y+4);
 }
 g.textAlign='center';
 for(let k=0;k<=5;k++){
  const wv = xa+(xb-xa)*k/5;
  g.fillText(Math.round(wv)+'W', X(wv), PT+h+18);
 }

 // ── curvas ────────────────────────────────────────────────────────────
 PM_PONTOS = [];
 [['fat_g_h','#3FB950','Gordura'],['cho_g_h','#F0883E','CHO']].forEach(function(cfg){
  const [campo, cor, nome] = cfg;
  g.strokeStyle=cor; g.lineWidth=2; g.beginPath();
  let primeiro=true;
  vis.forEach(function(p){
   if(p[campo]==null) return;
   const x=X(p.watts), y=Y(p[campo]);
   primeiro ? (g.moveTo(x,y), primeiro=false) : g.lineTo(x,y);
   PM_PONTOS.push({x:x,y:y,watts:p.watts,valor:p[campo],nome:nome,
                   lactato:p.lactato,vo2:p.vo2});
  });
  g.stroke();
 });

 // ── marcos ────────────────────────────────────────────────────────────
 PM_MARCOS = [
  [m.fatmax_w,   '#A371F7', 'FatMax'],
  [lm.lt1_w,     '#3FB950', 'LT1'],
  [m.mlss_at_w,  '#58A6FF', 'MLSS'],
  [lm.lt2_w,     '#F85149', 'LT2'],
  [PM_CP,        '#E3B341', 'CP'],
 ].filter(mk => mk[0] && mk[0]>=xa && mk[0]<=xb)
  .sort((a,b)=> a[0]-b[0]);

 // Escalonar as etiquetas por níveis: LT2, MLSS e CP caem quase no mesmo
 // sítio e sobrepunham-se. Cada etiqueta desce um nível enquanto colidir
 // com a anterior, em vez de alternar cegamente entre duas linhas.
 const ocupado = [];
 PM_MARCOS.forEach(function(mk){
  const wv = mk[0], cor = mk[1], rot = mk[2];
  const x = X(wv);
  g.strokeStyle=cor; g.setLineDash([5,4]); g.lineWidth=1.5;
  g.beginPath(); g.moveTo(x,PT); g.lineTo(x,PT+h); g.stroke();
  g.setLineDash([]); g.lineWidth=1;

  const txt = rot+' '+Math.round(wv)+'W';
  g.font='10px sans-serif';
  const larg = g.measureText(txt).width;
  let nivel = 0;
  while(ocupado.some(o => o.nivel===nivel && x-larg/2 < o.fim+6)) nivel++;
  ocupado.push({nivel:nivel, fim:x+larg/2});
  const yTxt = PT + 12 + nivel*12;
  g.fillStyle='#0d1117'; g.fillRect(x-larg/2-2, yTxt-9, larg+4, 12);
  g.fillStyle=cor; g.textAlign='center'; g.fillText(txt, x, yTxt);
 });

 g.textAlign='left'; g.font='11px sans-serif';
 g.fillStyle='#3FB950'; g.fillText('\u25CF Gordura (g/h)', PL+4, PT+h-6);
 g.fillStyle='#F0883E'; g.fillText('\u25CF CHO (g/h)', PL+112, PT+h-6);
 pmLigarTip();
}

function pmLigarTip(){
 const cv=document.getElementById('chSubstratos');
 const tip=document.getElementById('pmTip');
 if(!cv||!tip||cv._tipPM) return;
 cv._tipPM=true;
 cv.addEventListener('mousemove', function(ev){
  const r=cv.getBoundingClientRect();
  const mx=(ev.clientX-r.left)*(cv.width/r.width)/(window.devicePixelRatio||1);
  if(!PM_ESCALA){ tip.style.display='none'; return; }
  const e=PM_ESCALA;
  if(mx<e.PL || mx>e.PL+e.w){ tip.style.display='none'; pmDraw(); return; }
  // watts sob o cursor, e o ponto da curva mais próximo em X — assim o
  // tooltip responde em toda a altura do gráfico, não só em cima da linha
  const watts = e.xa + (mx-e.PL)/e.w*(e.xb-e.xa);
  let fat=null, cho=null, lac=null, vo2=null, melhorD=1e9;
  PM_PONTOS.forEach(function(p){
   const d=Math.abs(p.watts-watts);
   if(d<melhorD-1e-9){ melhorD=d; lac=p.lactato; vo2=p.vo2; }
  });
  PM_PONTOS.forEach(function(p){
   if(Math.abs(p.watts-watts) > melhorD+1e-6) return;
   if(p.nome==='Gordura') fat=p.valor; else cho=p.valor;
  });

  pmDraw();
  const g=cv.getContext('2d');
  const x=e.PL+(watts-e.xa)/(e.xb-e.xa)*e.w;
  g.strokeStyle='rgba(201,209,217,0.55)'; g.setLineDash([2,3]);
  g.beginPath(); g.moveTo(x,e.PT); g.lineTo(x,e.PT+e.h); g.stroke();
  g.setLineDash([]);
  [[fat,'#3FB950'],[cho,'#F0883E']].forEach(function(par){
   if(par[0]==null) return;
   g.fillStyle=par[1]; g.beginPath();
   g.arc(x, e.Y(par[0]), 4, 0, Math.PI*2); g.fill();
   g.strokeStyle='#0d1117'; g.stroke();
  });

  const z = (PM_ZONAS||[]).find(z => watts>=z.de && watts<z.ate);
  const perto = (PM_MARCOS||[]).slice().sort((a,b)=>
    Math.abs(a[0]-watts)-Math.abs(b[0]-watts))[0];
  let h='<b>'+Math.round(watts)+' W</b>';
  if(z) h+=' <span style="color:'+z.rot+';">'+z.nome+'</span>'
        +'<br><span style="color:#8b949e;font-size:10px;">'+z.desc+'</span>';
  if(fat!=null) h+='<br><span style="color:#3FB950;">Gordura</span> <b>'
    +(Math.round(fat*10)/10)+' g/h</b>';
  if(cho!=null) h+='<br><span style="color:#F0883E;">CHO</span> <b>'
    +(Math.round(cho*10)/10)+' g/h</b>';
  if(fat!=null&&cho!=null){
   const kcal = fat*9 + cho*4;
   h+='<br><span style="color:#8b949e;">'+Math.round(kcal)+' kcal/h · '
     +Math.round(cho*4/kcal*100)+'% de CHO</span>';
  }
  if(lac!=null) h+='<br><span style="color:#8b949e;">lactato '+lac+' mmol/L</span>';
  if(vo2!=null) h+='<br><span style="color:#8b949e;">VO\u2082 '+vo2+' ml/kg/min</span>';
  if(perto) h+='<br><span style="color:'+perto[1]+';font-size:10px;">'
    +Math.abs(Math.round(watts-perto[0]))+' W do '+perto[2]+'</span>';
  tip.innerHTML=h;
  tip.style.display='block';
  tip.style.left=Math.min(ev.clientX-r.left+14, r.width-190)+'px';
  tip.style.top=Math.max(4, ev.clientY-r.top-40)+'px';
 });
 cv.addEventListener('mouseleave',function(){
  tip.style.display='none'; pmDraw();
 });
}

function pmMMPEdit(){
 const box = document.getElementById('pmMMP');
 if(!box) return;
 const mm = (PM && PM.mmp_usados) || {};
 const dt = (PM && PM.datas_dos_mmp) || {};
 if(!Object.keys(mm).length){ box.innerHTML = ''; return; }
 let h = '<div style="color:#8b949e;font-size:11px;margin-bottom:6px;">'
   + 'MMP usados no cálculo — podes alterar os watts ou os segundos para testar</div>'
   + '<div style="display:flex;flex-wrap:wrap;align-items:flex-end;">';
 const se = (PM && PM.seasons_dos_mmp) || {};
 const rc = (PM && PM.recuou_de_season) || {};
 Object.keys(mm).forEach(function(sec, i){
  const cor = rc[sec] ? '#F0883E' : '#8b949e';
  const eti = (dt[sec]||'—') + (se[sec] && se[sec] !== 'manual'
    ? ' · ' + se[sec] + (rc[sec] ? ' (recuou)' : '') : '');
  h += '<div style="margin-right:14px;margin-bottom:6px;">'
    + '<div style="color:' + cor + ';font-size:10px;">' + eti + '</div>'
    + '<input type="number" class="pmSec" value="' + sec + '" style="width:64px" title="segundos">'
    + ' <span style="color:#8b949e;">s</span> '
    + '<input type="number" class="pmW" data-sec="' + sec + '" value="'
    + Math.round(mm[sec]) + '" style="width:70px" title="watts">'
    + ' <span style="color:#8b949e;">W</span></div>';
 });
 if(PM.pmax_w) h += '<div style="margin-right:14px;margin-bottom:6px;">'
   + '<div style="color:' + (PM.pmax_recuou ? '#F0883E' : '#8b949e')
   + ';font-size:10px;">' + (PM.pmax_data||'—')
   + (PM.pmax_season ? ' · ' + PM.pmax_season
      + (PM.pmax_recuou ? ' (recuou)' : '') : '') + '</div>'
   + 'Pmax <input type="number" id="pmPmax" value="' + Math.round(PM.pmax_w)
   + '" style="width:75px"> <span style="color:#8b949e;">W</span></div>';
 h += '<button onclick="pmCarregar(true)" style="margin-bottom:6px;">Recalcular</button>'
   + '<button onclick="pmCarregar(false)" style="margin-left:6px;margin-bottom:6px;">Repor automáticos</button>'
   + '</div>';
 if(Object.keys(PM.mmp_manuais||{}).length)
  h += '<div style="color:#F0883E;font-size:11px;">a usar valores manuais</div>';
 box.innerHTML = h;
}

function pmSemaforo(){
 const box = document.getElementById('pmSemaforo');
 const fb = document.getElementById('pmForma');
 if(!box) return;
 const zs = (PM && PM.zonas_semaforo) || [];
 if(!zs.length){ box.innerHTML=''; if(fb) fb.innerHTML=''; return; }
 let h = '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
  + '<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
  + '<th style="padding:6px;">Zona</th><th>Potência</th><th>Sensação</th>'
  + '<th>Respiração</th><th>Duração</th><th>% treino</th></tr>';
 zs.forEach(function(z){
  h += '<tr style="border-bottom:1px solid #161b22;">'
   + '<td style="padding:6px;border-left:3px solid '+z.cor+';">'+z.zona+'</td>'
   + '<td><b>'+z.de_w+' – '+z.ate_w+' W</b></td>'
   + '<td style="color:#8b949e;">'+z.sensacao+'</td>'
   + '<td style="color:#8b949e;">'+z.respiracao+'</td>'
   + '<td style="color:#8b949e;">'+z.duracao+'</td>'
   + '<td style="color:#8b949e;">'+z.pct_treino+'</td></tr>';
 });
 h += '</table><p style="color:#8b949e;font-size:11px;margin-top:6px;">'
  + 'Ancoradas no LT1 (fim do verde) e no MLSS (topo do limiar), não numa '
  + 'FTP. Z1+Z2 é a base anabólica; Z3 constrói se estiveres fresco e '
  + 'quebra se estiveres cansado; Z4+Z5 é catabólico e só adapta com '
  + 'nutrição e recuperação.</p>';
 box.innerHTML = h;

 const d = (PM && PM.diagnostico_curva) || {};
 if(!fb) return;
 if(d.ok){
  fb.innerHTML = '<div style="border-left:3px solid '+d.cor+';padding:6px 10px;">'
   + '<b style="color:'+d.cor+';">'+d.nome+'</b> — '+d.descricao
   + '<br><span style="color:#c9d1d9;">'+d.prescricao+'</span>'
   + '<br><span style="color:#8b949e;font-size:11px;">base '
   + d.base_relativa_pct+'% · aceleração no topo '+d.aceleracao_no_topo
   + 'x</span></div>';
 } else {
  fb.innerHTML = '<p style="color:#8b949e;font-size:11px;">'
   + '<b>Diagnóstico pela forma da curva:</b> ' + (d.motivo||'indisponível')
   + (d.o_que_falta ? '. Falta: ' + d.o_que_falta : '')
   + (d.base_relativa_pct!=null
      ? '<br>Medidas actuais: base ' + d.base_relativa_pct + '% · aceleração '
        + d.aceleracao_no_topo + 'x · lactato ' + (d.lactato||{}).lt1
        + ' no LT1 e ' + (d.lactato||{}).lt2 + ' no LT2 mmol/L.' : '')
   + '</p>';
 }
}

function pmZonas(){
 // (o pace aparece só na corrida, quando há recta ajustada)
 const z=(PM&&PM.zonas)||[];
 if(!z.length){document.getElementById('pmZonas').innerHTML='';return;}
 const temPace = z.some(function(x){ return x.pace_de || x.pace_ate; });
 let h='<table style="width:100%;border-collapse:collapse;font-size:12px;">'
  +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
  +'<th style="padding:6px;">Zona</th><th>% do MLSS</th><th>Watts</th>'
  +(temPace?'<th>Pace</th>':'')+'</tr>';
 z.forEach(function(x){
  h+='<tr style="border-bottom:1px solid #161b22;"><td style="padding:6px;">'+x.zona+'</td>'
   +'<td style="color:#8b949e;">'+x.pct_at+'</td><td>'+x.de_w+' – '+x.ate_w+' W</td>'
   +(temPace?'<td style="color:#79C0FF;">'
     +(x.pace_de&&x.pace_ate ? x.pace_ate+' – '+x.pace_de : '—')+'</td>':'')
   +'</tr>';
 });
 h+='</table><p style="color:#8b949e;font-size:11px;margin-top:6px;">'
  +'Ancoradas no MLSS do modelo, não numa FTP fixa.</p>';
 const rp=(PM&&PM.relacao_pace_watts);
 if(rp) h+='<p style="color:#79C0FF;font-size:11px;">'
  +(rp.suficiente
    ? 'Pace da recta potência–velocidade ajustada às tuas sessões (r²='
      + rp.r2 + ', n=' + rp.n + '). O pace mais rápido corresponde ao limite '
      + 'superior de watts da zona.'
    : 'Sem pace: ' + (rp.nota || rp.erro || 'dados insuficientes'))
  +'</p>';
 document.getElementById('pmZonas').innerHTML=h;
}

function pmDetalhe(){
 const d=PM||{};
 const mm=d.mmp_usados||{}, dt=d.datas_dos_mmp||{};
 const se=d.seasons_dos_mmp||{}, rc=d.recuou_de_season||{}, ql=d.qualidade_dos_mmp||{};
 let h='<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr style="color:#8b949e;text-align:left;"><th style="padding-right:16px;">Duração</th>'
  +'<th style="padding-right:16px;">Watts</th><th style="padding-right:16px;">Data</th>'
  +'<th style="padding-right:16px;">Season</th><th>Na season / histórico</th></tr>';
 Object.keys(mm).forEach(function(k){
  const q=ql[k]||{};
  const qtxt = q.racio_na_season!=null
    ? (q.melhor_na_season_w||'—')+' / '+(q.melhor_historico_w||'—')+' W = '
      +Math.round(q.racio_na_season*100)+'%'
    : '—';
  h+='<tr><td style="padding-right:16px;">'+Math.round(k/60)+' min</td>'
   +'<td style="padding-right:16px;">'+mm[k]+' W</td>'
   +'<td style="color:#8b949e;padding-right:16px;">'+(dt[k]||'—')+'</td>'
   +'<td style="color:'+(rc[k]?'#F0883E':'#8b949e')+';padding-right:16px;">'+(se[k]||'—')
   +(rc[k]?' (recuou)':'')+'</td>'
   +'<td style="color:'+(rc[k]?'#F0883E':'#8b949e')+';">'+qtxt+'</td></tr>';
 });
 if(d.pmax_w) h+='<tr><td style="padding-right:16px;">Pmax (1s)</td>'
   +'<td style="padding-right:16px;">'+Math.round(d.pmax_w)+' W</td>'
   +'<td style="color:#8b949e;padding-right:16px;">'+(d.pmax_data||'—')+'</td>'
   +'<td style="color:'+(d.pmax_recuou?'#F0883E':'#8b949e')+';padding-right:16px;">'
   +(d.pmax_season||'—')+(d.pmax_recuou?' (recuou)':'')+'</td>'
   +'<td style="color:#8b949e;">'+(d.pmax_racio_na_season!=null
     ? Math.round(d.pmax_racio_na_season*100)+'%' : '—')+'</td></tr>';
 h+='</table>';
 if(d.season_do_conjunto && d.season_do_conjunto !== d.season)
  h+='<p style="color:#F0883E;font-size:11px;">A season activa ('+(d.season||'?')
   +') não tinha todas as durações; conjunto completo de <b>'
   +d.season_do_conjunto+'</b>.</p>';
 if(d.limiar_esforco_maximo!=null)
  h+='<p style="color:#8b949e;font-size:11px;">Considera-se esforço máximo a partir de '
   +Math.round(d.limiar_esforco_maximo*100)+'% do melhor histórico dessa duração. '
   +'Abaixo disso recua para a season anterior. Critério de decisão, não constante '
   +'fisiológica — ajustável em <code>?limiar_max=</code>.</p>';
 if(d.n_curvas_na_season!=null)
  h+='<p style="color:#8b949e;font-size:11px;">'+d.n_curvas_na_season
   +' curvas na season '+(d.season||'?')+' de '+(d.n_curvas_total||0)
   +' na base'+(d.curvas_ignoradas ? ' · '+d.curvas_ignoradas+' ilegíveis' : '')
   +((d.seasons_disponiveis||[]).length>1
     ? ' · seasons: '+d.seasons_disponiveis.join(', ') : '')+'</p>';
 if(d.dispersao_datas_dias!=null)
  h+='<p style="color:#8b949e;font-size:11px;">MMP separados por '+d.dispersao_datas_dias+' dias.</p>';
 h+='<p style="color:#8b949e;font-size:11px;">'+(d.vo2max_validade||'')
  +'<br>VLamax é estimado a partir de potências máximas (konaendu/Mader), não medido. '
  +'Serve para acompanhar o próprio atleta ao longo do tempo, não como valor absoluto.</p>';
 document.getElementById('pmDetalhe').innerHTML=h;
}

(function(){
 const _dr = document.getElementById('pmDataRef');
 if(_dr && !_dr.value) _dr.value = new Date().toISOString().slice(0,10);
})();
aqInit();
carregarCobertura().then(load);
"""
def api_data():
    try:
        modalidades = modalidades_disponiveis()
    except Exception as e:
        return jsonify({'status': 'erro', 'modalidades': []})
    return jsonify({'status': 'ok', 'modalidades': modalidades, 'agregacoes_validas': AGREGACOES_VALIDAS})

def render():
    from flask import render_template_string
    return render_template_string(page(SLUG, 'Metabolismo', BODY, JS))
