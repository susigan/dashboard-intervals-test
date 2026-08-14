"""tab_metabol.py — Tab "Metabolismo": perfil metabólico por watts + pace.

Segue a mesma estrutura das outras tabs (ver tabs/__init__.py):
  SLUG, render(), api_data() — mais as funções de análise usadas por
  render()/api_data() e pelos endpoints /api/fisiologia/* em app.py.

Não escreve nada no .db — só lê o que fisiologia_worker.py já gravou.

DUAS ANÁLISES:

  1. perfil_por_modalidade(modalidade)
     "A X watts, o que é normal/esperado?"
     Quartis de potência CALCULADOS AGORA (não fixos) a partir da
     distribuição real de watts dessa modalidade — depois, dentro de
     cada faixa, quartis (p25/p50/p75) de cada métrica: valor no
     esforço (hr_medio_work, smo2_medio_work, ...) e tempo de resposta/
     recuperação (lag_*_50, rec_*_50).
     NOVO: Adiciona 'pace_medio' para Row/Ski.

  2. evolucao_temporal(modalidade, campo, watts_min, watts_max)
     "Este valor está a mudar ao longo do tempo, a esta potência?"
     Agrupa por mês (ou por período à escolha) dentro de uma faixa de
     watts fixa, para ver deriva longitudinal.
     NOVO: Adiciona 'pace_p50' para Row/Ski.

  3. grafico_perfil_metabolico(perfil)
     Plotly server-side. NOVO: labels X mostram watts + pace (Row/Ski).

Ambas as análises usam apenas linhas com valido=1.
"""

from flask import jsonify, request

import numpy as np
import sqlite3
from datetime import datetime

import drive_db_fisiologia as ddf
from tabs.base import page

SLUG = 'metabol'


# ── Métricas de VALOR ────────────────────────────────────────────────────
CAMPOS_VALOR = [
    'hr_plateau_work', 'smo2_plateau_work', 'thb_plateau_work',
    'resp_plateau_work', 'dfa1_plateau_work',
]

CAMPOS_EXTREMO = [
    'hr_extremo', 'smo2_extremo', 'thb_extremo', 'resp_extremo', 'dfa1_extremo',
]

CAMPOS_VALOR_API = [
    'hr_medio_work', 'smo2_medio_work', 'thb_medio_work',
    'resp_medio_work', 'dfa1_medio_work',
]

CAMPOS_TEMPO = [
    'lag_hr_50', 'lag_smo2_50', 'lag_thb_50', 'lag_resp_50', 'lag_dfa1_50',
    'rec_hr_50', 'rec_smo2_50', 'rec_thb_50', 'rec_resp_50', 'rec_dfa1_50',
]

TODOS_CAMPOS = CAMPOS_VALOR + CAMPOS_EXTREMO + CAMPOS_TEMPO + CAMPOS_VALOR_API

MIN_POR_FAIXA = 3


_PREFIXOS = ('hr', 'smo2', 'thb', 'resp', 'dfa1')
_METRICAS_GRAFICO = [
    ('hr', 'HR', 'bpm', '#1f77b4'),
    ('smo2', 'SmO₂', '%', '#ff7f0e'),
    ('thb', 'tHb', 'µM', '#2ca02c'),
    ('resp', 'Respiração', 'rpm', '#d62728'),
    ('dfa1', 'DFA-α1', '', '#9467bd'),
]


def _prefixo_de(campo):
    for p in _PREFIXOS:
        if campo.startswith(p + '_'):
            return p
    return None


def _conn():
    conn = ddf.get_conn()
    conn.row_factory = sqlite3.Row
    return conn


def _quartis(valores):
    """Resumo robusto de uma métrica."""
    if len(valores) == 0:
        return None
    vs = np.array([v for v in valores if v is not None], dtype=float)
    vs = vs[np.isfinite(vs)]
    if len(vs) < 2:
        return None
    return {
        'p10': round(float(np.percentile(vs, 10)), 2),
        'p25': round(float(np.percentile(vs, 25)), 2),
        'p50': round(float(np.percentile(vs, 50)), 2),
        'p75': round(float(np.percentile(vs, 75)), 2),
        'p90': round(float(np.percentile(vs, 90)), 2),
        'n': len(vs),
    }


def _watts_para_pace(watts, modalidade='Row'):
    """Converter watts para pace (min:ss) usando fórmula Concept2."""
    if modalidade not in ['Row', 'Ski']:
        return None
    if watts <= 0:
        return None
    
    FACTOR = 2.8
    pace_seg = 500.0 / ((watts / FACTOR) ** (1/3))
    min_val = int(pace_seg // 60)
    seg_val = int(pace_seg % 60)
    return f'{min_val}:{seg_val:02d}'


def modalidades_disponiveis():
    """Quantos intervalos há de cada modalidade (só válidos)."""
    conn = _conn()
    resultado = conn.execute("""
        SELECT modalidade, COUNT(*) as n, COUNT(DISTINCT data) as n_dias
        FROM fisiologia_intervalos
        WHERE valido = 1 AND watts_medio IS NOT NULL
        GROUP BY modalidade
        ORDER BY modalidade
    """).fetchall()
    return [
        {'modalidade': r['modalidade'], 'n_intervalos': r['n'], 'n_dias': r['n_dias']}
        for r in resultado
    ]


def perfil_por_modalidade(modalidade, min_n_total=15, n_faixas=10,
                          so_plateau_valido=True):
    """Curva watts -> métrica esperada. NOVO: adiciona pace para Row/Ski."""
    conn = _conn()
    flags = [f'{p}_atingiu_plateau' for p in _PREFIXOS]
    colunas = ", ".join(TODOS_CAMPOS + flags)
    linhas = conn.execute(
        """SELECT watts_medio, data, activity_id, """ + colunas + """
           FROM fisiologia_intervalos
           WHERE modalidade = ? AND valido = 1 AND watts_medio IS NOT NULL
           ORDER BY watts_medio""",
        (modalidade,)
    ).fetchall()

    if len(linhas) < min_n_total:
        return {
            'status': 'dados_insuficientes',
            'modalidade': modalidade,
            'n_disponivel': len(linhas),
            'minimo_necessario': min_n_total,
        }

    watts = np.array([l['watts_medio'] for l in linhas])
    n_datas = len(set(l['data'] for l in linhas))
    n_activities = len(set(l['activity_id'] for l in linhas))

    wmin, wmax = float(watts.min()), float(watts.max())
    intervalo_total = wmax - wmin

    if intervalo_total <= 0:
        largura_bin = 20.0
    else:
        largura_bin = intervalo_total / n_faixas
        largura_bin = max(10.0, min(30.0, largura_bin))

    limites = [wmin]
    v = wmin
    while v < wmax:
        v += largura_bin
        limites.append(v)
    if limites[-1] < wmax:
        limites.append(wmax + 0.01)

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
        
        # Adicionar pace para Row/Ski
        if modalidade in ['Row', 'Ski']:
            pace = _watts_para_pace(watts_centro, modalidade)
            if pace:
                faixa['pace_medio'] = pace
        
        for campo in TODOS_CAMPOS:
            prefixo = _prefixo_de(campo)
            usar_filtro = so_plateau_valido and campo in CAMPOS_VALOR and prefixo
            valores = []
            n_excluidos = 0
            for j in idxs:
                if usar_filtro:
                    flag = linhas[j][f'{prefixo}_atingiu_plateau']
                    if not flag:
                        n_excluidos += 1
                        continue
                valores.append(linhas[j][campo])
            q = _quartis(valores)
            if q is not None and n_excluidos:
                q['n_excluidos_sem_plateau'] = n_excluidos
            faixa[campo] = q
        faixas_saida.append(faixa)

    return {
        'status': 'ok',
        'modalidade': modalidade,
        'n_intervalos_total': len(linhas),
        'n_atividades': n_activities,
        'n_dias_distintos': n_datas,
        'largura_bin_watts': round(largura_bin, 1),
        'watts_min_observado': round(wmin, 1),
        'watts_max_observado': round(wmax, 1),
        'faixas': faixas_saida,
        'gerado_em': datetime.now().isoformat(timespec='seconds'),
    }


def evolucao_temporal(modalidade, campo, watts_min=None, watts_max=None,
                      agregacao='mes', min_por_periodo=3):
    """Deriva longitudinal. NOVO: adiciona pace para Row/Ski."""
    if campo not in TODOS_CAMPOS:
        return {'status': 'erro', 'mensagem': f'campo desconhecido: {campo}'}

    conn = _conn()
    cond = ["modalidade = ?", "valido = 1", f"{campo} IS NOT NULL"]
    params = [modalidade]

    if watts_min is not None:
        cond.append("watts_medio >= ?")
        params.append(watts_min)
    if watts_max is not None:
        cond.append("watts_medio <= ?")
        params.append(watts_max)

    linhas = conn.execute(
        f"""SELECT data, {campo} as valor, watts_medio
           FROM fisiologia_intervalos
           WHERE {' AND '.join(cond)} ORDER BY data""",
        tuple(params)
    ).fetchall()

    if not linhas:
        return {'status': 'dados_insuficientes', 'n_disponivel': 0}

    def _periodo(data_str):
        if agregacao == 'semana':
            try:
                dt = datetime.strptime(data_str, '%Y-%m-%d')
                ano, semana, _ = dt.isocalendar()
                return f'{ano}-W{semana:02d}'
            except:
                return data_str[:7]
        return data_str[:7]

    grupos = {}
    for l in linhas:
        p = _periodo(l['data'])
        grupos.setdefault(p, []).append({
            'valor': l['valor'],
            'watts': l['watts_medio']
        })

    periodos = []
    for periodo in sorted(grupos.keys()):
        valores = [g['valor'] for g in grupos[periodo]]
        watts_vals = [g['watts'] for g in grupos[periodo]]
        
        if len(valores) < min_por_periodo:
            continue

        q = _quartis(valores)
        if not q:
            continue
        
        q['periodo'] = periodo
        watts_p50 = np.percentile([w for w in watts_vals if w is not None], 50)
        
        # Adicionar pace para Row/Ski
        if modalidade in ['Row', 'Ski']:
            pace = _watts_para_pace(watts_p50, modalidade)
            if pace:
                q['pace_p50'] = pace
        
        periodos.append(q)

    return {
        'status': 'ok',
        'modalidade': modalidade,
        'campo': campo,
        'watts_min': watts_min,
        'watts_max': watts_max,
        'periodos': periodos,
        'gerado_em': datetime.now().isoformat(timespec='seconds'),
    }


def grafico_perfil_metabolico(perfil):
    """Gráfico Plotly. NOVO: labels X mostram watts + pace (Row/Ski)."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    if perfil.get('status') != 'ok':
        raise ValueError(f"perfil sem dados: {perfil.get('status')}")

    faixas = perfil['faixas']
    
    # Labels com pace adicionado (para Row/Ski)
    labels_x = []
    for f in faixas:
        watts_label = f['faixa_watts']
        if 'pace_medio' in f:
            labels_x.append(f"{watts_label}<br>{f['pace_medio']}")
        else:
            labels_x.append(watts_label)

    metricas_com_dado = [
        (chave, nome, unidade, cor) for chave, nome, unidade, cor in _METRICAS_GRAFICO
        if any(f.get(f'{chave}_medio_work') for f in faixas)
    ]
    if not metricas_com_dado:
        raise ValueError('nenhuma metrica com dados')

    n_metricas = len(metricas_com_dado)
    fig = make_subplots(
        rows=n_metricas, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=[f'{nome} ({unidade})' for _, nome, unidade, _ in metricas_com_dado],
    )

    def _hex_para_rgba(hex_cor, alpha):
        h = hex_cor.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f'rgba({r},{g},{b},{alpha})'

    for i, (chave, nome, unidade, cor) in enumerate(metricas_com_dado, start=1):
        campo_work = f'{chave}_medio_work'
        campo_rec = f'{chave}_medio_rec'

        p50 = [f[campo_work]['p50'] if f.get(campo_work) else None for f in faixas]
        p25 = [f[campo_work]['p25'] if f.get(campo_work) else None for f in faixas]
        p75 = [f[campo_work]['p75'] if f.get(campo_work) else None for f in faixas]
        rec_p50 = [f[campo_rec]['p50'] if f.get(campo_rec) else None for f in faixas]
        n_por_faixa = [f[campo_work]['n'] if f.get(campo_work) else 0 for f in faixas]

        # banda p25-p75
        fig.add_trace(go.Scatter(
            x=labels_x + labels_x[::-1],
            y=p75 + p25[::-1],
            fill='toself', fillcolor=_hex_para_rgba(cor, 0.15),
            line=dict(color='rgba(0,0,0,0)'),
            hoverinfo='skip', showlegend=False,
        ), row=i, col=1)

        # p50 esforço
        fig.add_trace(go.Scatter(
            x=labels_x, y=p50, mode='lines+markers',
            name=f'{nome} (esforço)', line=dict(color=cor, width=2),
            marker=dict(size=7),
            customdata=n_por_faixa,
            hovertemplate=f'{nome}: %{{y}}{unidade} (n=%{{customdata}})<extra></extra>',
            showlegend=(i == 1),
        ), row=i, col=1)

        # p50 repouso
        if any(v is not None for v in rec_p50):
            fig.add_trace(go.Scatter(
                x=labels_x, y=rec_p50, mode='lines+markers',
                name=f'{nome} (repouso)', line=dict(color='#8b949e', width=1.5, dash='dot'),
                marker=dict(size=5, symbol='circle-open'),
                hovertemplate=f'{nome} repouso: %{{y}}{unidade}<extra></extra>',
                showlegend=(i == 1),
            ), row=i, col=1)

    fig.update_layout(
        title=dict(
            text=f"Perfil Metabólico — {perfil['modalidade']} "
                f"({perfil['n_intervalos_total']} intervalos)",
            font=dict(size=14, color='#222')),
        paper_bgcolor='white', plot_bgcolor='white',
        height=180 * n_metricas + 80,
        margin=dict(t=60, b=80, l=70, r=20),
        hovermode='x unified',
        legend=dict(orientation='h', y=-0.08, font=dict(color='#222', size=10)),
        font=dict(color='#222', size=11),
    )
    fig.update_xaxes(tickfont=dict(size=10, color='#333'), linecolor='#555', tickcolor='#555')
    fig.update_yaxes(tickfont=dict(size=10, color='#333'), linecolor='#555', tickcolor='#555',
                     gridcolor='rgba(0,0,0,0.06)')

    return fig


def api_data():
    """Dados de arranque da página."""
    try:
        modalidades = modalidades_disponiveis()
    except Exception as e:
        return jsonify({'status': 'erro', 'modalidades': []})
    return jsonify({'status': 'ok', 'modalidades': modalidades,
                    'campos_valor': CAMPOS_VALOR, 'campos_tempo': CAMPOS_TEMPO})


def render():
    """Página HTML da tab."""
    from flask import render_template_string
    return render_template_string(page(SLUG, 'Metabolismo'))


def validacao_lote_dfa(modalidade):
    """Validar qualidade DFA-α1 de uma modalidade."""
    from utils.dfa_artifacts_analyzer import DFAArtifactAnalyzer
    
    analyzer = DFAArtifactAnalyzer(modalidade=modalidade)
    conn = _conn()
    
    intervalos = conn.execute("""
        SELECT dfa1_plateau_work, hr_plateau_work, hr_extremo, watts_medio
        FROM fisiologia_intervalos
        WHERE modalidade = ? AND valido = 1 AND dfa1_plateau_work IS NOT NULL
        LIMIT 500
    """, (modalidade,)).fetchall()

    if not intervalos:
        return {
            'modalidade': modalidade,
            'resumo': {'n_total': 0},
        }

    resultados = []
    for iv in intervalos:
        resultado = analyzer.analisar_intervalo(
            dfa1=float(iv['dfa1_plateau_work']) if iv['dfa1_plateau_work'] else 0.0,
            hr_medio=float(iv['hr_plateau_work']) if iv['hr_plateau_work'] else 100.0,
            hr_max=float(iv['hr_extremo']) if iv['hr_extremo'] else 110.0,
            artifact_percent=None,
            watts_medio=float(iv['watts_medio']) if iv['watts_medio'] else 0.0
        )
        resultados.append(resultado)

    resumo = analyzer.resumo_validacao(resultados)

    return {
        'modalidade': modalidade,
        'resumo': resumo,
        'detalhes_primeiros_10': [
            {
                'dfa1': r.dfa1_original,
                'valido': r.esta_valido,
                'confidence': r.confidence,
                'motivo': r.motivo,
            }
            for r in resultados[:10]
        ]
    }


def evolucao_temporal_com_pace(modalidade, campo, watts_min=None, watts_max=None, agregacao='mes'):
    """Wrapper: chama evolucao_temporal() e reformata para "evolucao" em vez de "periodos"."""
    resultado = evolucao_temporal(modalidade, campo, watts_min, watts_max, agregacao)
    if resultado.get('status') == 'ok':
        resultado['evolucao'] = resultado.pop('periodos', [])
        resultado['n_periodos'] = len(resultado['evolucao'])
    return resultado
