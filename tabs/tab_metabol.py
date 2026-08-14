"""tab_metabol.py — Tab "Metabolismo": perfil metabólico por watts.

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

  2. evolucao_temporal(modalidade, campo, watts_min, watts_max)
     "Este valor está a mudar ao longo do tempo, a esta potência?"
     Agrupa por mês (ou por período à escolha) dentro de uma faixa de
     watts fixa, para ver deriva longitudinal (ex.: será que o SmO2 a
     300W está diferente agora do que há 6 meses?).

  3. grafico_perfil_metabolico(perfil)
     Plotly server-side (import LAZY, dentro da função — mesma razão do
     fmt_graficos.py: se plotly não estiver instalado, só esta função
     falha, o resto do módulo continua a funcionar). Devolve go.Figure;
     a página injeta o HTML via /api/fisiologia/perfil_grafico.

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
# PREFERIR *_plateau_work (medido no stream, no fim do esforço, já
# estabilizado) a *_medio_work (média do lap inteiro dada pela API, que
# inclui todo o transitório e enviesa sistematicamente).
CAMPOS_VALOR = [
    'hr_plateau_work', 'smo2_plateau_work', 'thb_plateau_work',
    'resp_plateau_work', 'dfa1_plateau_work',
]

# Extremo atingido (janela que entra 30s no descanso) — capta o pico real
# de métricas com inércia, que de outra forma seria atribuído ao descanso.
CAMPOS_EXTREMO = [
    'hr_extremo', 'smo2_extremo', 'thb_extremo', 'resp_extremo', 'dfa1_extremo',
]

# Média do lap vinda da API — mantidos só para comparação/diagnóstico.
CAMPOS_VALOR_API = [
    'hr_medio_work', 'smo2_medio_work', 'thb_medio_work',
    'resp_medio_work', 'dfa1_medio_work',
]

# Métricas de TEMPO (lag de resposta e recovery) — "quanto demora"
CAMPOS_TEMPO = [
    'lag_hr_50', 'lag_smo2_50', 'lag_thb_50', 'lag_resp_50', 'lag_dfa1_50',
    'rec_hr_50', 'rec_smo2_50', 'rec_thb_50', 'rec_resp_50', 'rec_dfa1_50',
]

TODOS_CAMPOS = CAMPOS_VALOR + CAMPOS_EXTREMO + CAMPOS_TEMPO + CAMPOS_VALOR_API

MIN_POR_FAIXA = 3   # abaixo disto, não vale a pena mostrar quartis (ruído)


_PREFIXOS = ('hr', 'smo2', 'thb', 'resp', 'dfa1')


def _prefixo_de(campo):
    """'smo2_plateau_work' -> 'smo2'. None se não corresponder a métrica."""
    for p in _PREFIXOS:
        if campo.startswith(p + '_'):
            return p
    return None


def _conn():
    conn = ddf.get_conn()
    conn.row_factory = sqlite3.Row
    return conn


def _quartis(valores):
    """Resumo robusto de uma métrica.

    Inclui p10/p90 em vez de depender de min/max para os extremos: o
    mínimo e o máximo observados são estimadores ENVIESADOS pelo número
    de amostras (com mais intervalos, o max sobe sozinho mesmo sem
    qualquer mudança fisiológica). p10/p90 são muito mais estáveis.
    min/max continuam disponíveis, mas devem ser lidos sempre com o n.
    """
    vs = np.array([v for v in valores if v is not None], dtype=float)
    vs = vs[np.isfinite(vs)]
    if len(vs) < MIN_POR_FAIXA:
        return None
    return {
        'n': len(vs),
        'p10': round(float(np.percentile(vs, 10)), 2),
        'p25': round(float(np.percentile(vs, 25)), 2),
        'p50': round(float(np.percentile(vs, 50)), 2),
        'p75': round(float(np.percentile(vs, 75)), 2),
        'p90': round(float(np.percentile(vs, 90)), 2),
        'min': round(float(np.min(vs)), 2),
        'max': round(float(np.max(vs)), 2),
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


def perfil_por_modalidade(modalidade, min_n_total=15, n_faixas=10,
                          so_plateau_valido=True):
    """Curva watts -> métrica esperada, para 1 modalidade.

    Bins de LARGURA FIXA em watts (não por contagem de intervalos) —
    largura calculada a partir do range real de watts desta modalidade,
    para ter por omissão ~n_faixas bins, com um mínimo de 15W e máximo
    de 60W de largura por bin. Isto evita bins enormes tipo "172-288W"
    que escondiam picos reais (ex.: respiração a 40+ rpm só nos watts
    mais altos, "diluída" pela média de um bin demasiado largo).

    Bins sem nenhum intervalo dentro simplesmente não aparecem na saída
    — não inventamos zero nem interpolamos.

    n_faixas é agora um ALVO (quantos bins tentar ter), não um número
    fixo — o número real de bins na saída depende de quantos ficam com
    pelo menos MIN_POR_FAIXA pontos numa métrica.
    """
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
            'mensagem': (f'Só há {len(linhas)} intervalos válidos para {modalidade}. '
                        f'Precisa de pelo menos {min_n_total}. '
                        f'Continua a processar atividades e volta a tentar.'),
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
        # Máximo 30W: acima disso, faixas com fisiologia muito diferente
        # ficam misturadas (a respiração a 300W e a 200W não são a mesma
        # coisa). Mínimo 10W para não fragmentar em ruído.
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
            continue  # bin vazio -- não aparece, não inventamos dado

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
                        # a métrica nunca estabilizou neste intervalo: o
                        # valor não representa o esforço, seria enganador
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
    """Deriva longitudinal: como 'campo' varia ao longo do tempo, dentro
    de uma faixa de watts (fixa, definida por ti — ao contrário do
    perfil_por_modalidade, aqui a faixa não é recalculada, porque queres
    comparar sempre "a mesma pergunta" ao longo do tempo).

    Args:
        modalidade: 'Bike'/'Row'/'Ski'/'Run'
        campo: um de TODOS_CAMPOS, ex. 'smo2_medio_work', 'lag_hr_50'
        watts_min/watts_max: faixa fixa (None = sem limite desse lado)
        agregacao: 'mes' ou 'semana'
        min_por_periodo: mínimo de pontos para incluir um período

    Retorna lista ordenada por período: [{periodo, p25, p50, p75, n}, ...]
    """
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
        f"""SELECT data, {campo} as valor, watts_medio FROM fisiologia_intervalos
           WHERE {' AND '.join(cond)} ORDER BY data""",
        tuple(params)
    ).fetchall()

    if not linhas:
        return {'status': 'dados_insuficientes', 'modalidade': modalidade,
               'campo': campo, 'n_disponivel': 0}

    def _periodo(data_str):
        if agregacao == 'semana':
            dt = datetime.strptime(data_str, '%Y-%m-%d')
            ano, semana, _ = dt.isocalendar()
            return f'{ano}-W{semana:02d}'
        return data_str[:7]  # YYYY-MM

    grupos = {}
    watts_grupos = {}
    for l in linhas:
        p = _periodo(l['data'])
        grupos.setdefault(p, []).append(l['valor'])
        watts_grupos.setdefault(p, []).append(l['watts_medio'] if l['watts_medio'] else 0)

    saida = []
    for periodo in sorted(grupos.keys()):
        q = _quartis(grupos[periodo])
        if q and q['n'] >= min_por_periodo:
            registro = {'periodo': periodo, **q}
            # Adicionar pace para Row/Ski
            if modalidade in ['Row', 'Ski'] and watts_grupos[periodo]:
                watts_p50 = np.percentile([w for w in watts_grupos[periodo] if w > 0], 50)
                pace = _watts_para_pace(watts_p50, modalidade)
                if pace:
                    registro['pace_p50'] = pace
            saida.append(registro)

    return {
        'status': 'ok', 'modalidade': modalidade, 'campo': campo,
        'watts_min': watts_min, 'watts_max': watts_max, 'agregacao': agregacao,
        'n_periodos': len(saida), 'evolucao': saida,
    }


def modalidades_disponiveis():
    """Quantos intervalos válidos há já, por modalidade — para saber se
    já vale a pena chamar perfil_por_modalidade() em cada uma."""
    conn = _conn()
    linhas = conn.execute(
        """SELECT modalidade, COUNT(*) as n, COUNT(DISTINCT activity_id) as n_atividades
           FROM fisiologia_intervalos WHERE valido = 1
           GROUP BY modalidade ORDER BY n DESC"""
    ).fetchall()
    return [dict(l) for l in linhas]


# ══════════════════════════════════════════════════════════════════════════
# GRÁFICO — pequenos múltiplos: 1 linha por métrica, X = faixa de watts
# ══════════════════════════════════════════════════════════════════════════

# (métrica, unidade, cor) — mesma paleta usada em fmt_graficos.py, para
# os gráficos ficarem visualmente consistentes entre tabs.
_METRICAS_GRAFICO = [
    ('hr',   'HR',            'bpm', '#E74C3C'),
    ('smo2', 'SmO2',          '%',   '#F39C12'),
    ('thb',  'tHb',           'a.u.', '#2980B9'),
    ('resp', 'Respiração',    'rpm', '#1ABC9C'),
    ('dfa1', 'DFA-α1',        'index', '#9B59B6'),
]


def grafico_perfil_metabolico(perfil):
    """Pequenos múltiplos: 1 subplot por métrica, X = faixa de watts.

    Por métrica mostra:
      - linha+marcadores do p50 no ESFORÇO (cor sólida da métrica)
      - banda sombreada p25-p75 do esforço (mesma cor, translúcida)
      - linha tracejada cinza do p50 em REPOUSO (rec) — referência de
        baseline, para se perceber o delta esforço-vs-repouso

    Import de plotly é LAZY (dentro da função): se não estiver instalado,
    só esta função falha — perfil_por_modalidade()/evolucao_temporal()
    continuam a funcionar normalmente (endpoints JSON não dependem disto).

    Args:
        perfil: dict devolvido por perfil_por_modalidade() (status='ok')

    Returns:
        plotly.graph_objects.Figure
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    if perfil.get('status') != 'ok':
        raise ValueError(f"perfil sem dados suficientes: {perfil.get('mensagem', perfil.get('status'))}")

    faixas = perfil['faixas']
    labels_x = [f['faixa_watts'] for f in faixas]

    # só entram métricas com pelo menos 1 faixa com dado
    metricas_com_dado = [
        (chave, nome, unidade, cor) for chave, nome, unidade, cor in _METRICAS_GRAFICO
        if any(f.get(f'{chave}_medio_work') for f in faixas)
    ]
    if not metricas_com_dado:
        raise ValueError('nenhuma metrica com dados nas faixas deste perfil')

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

        # banda p25-p75 (duas linhas invisíveis + fill entre elas)
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

        # p50 repouso (referência, tracejado)
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
                f"({perfil['n_intervalos_total']} intervalos, {perfil['n_atividades']} atividades)",
            font=dict(size=14, color='#222')),
        paper_bgcolor='white', plot_bgcolor='white',
        height=180 * n_metricas + 80,
        margin=dict(t=60, b=50, l=70, r=20),
        hovermode='x unified',
        legend=dict(orientation='h', y=-0.06, font=dict(color='#222', size=10),
                   bgcolor='rgba(255,255,255,0.85)', borderwidth=0),
        font=dict(color='#222', size=11),
    )
    fig.update_xaxes(tickfont=dict(size=10, color='#333'), linecolor='#555', tickcolor='#555')
    fig.update_yaxes(tickfont=dict(size=10, color='#333'), linecolor='#555', tickcolor='#555',
                     gridcolor='rgba(0,0,0,0.06)')

    return fig


# ══════════════════════════════════════════════════════════════════════════
# PÁGINA — SLUG/render()/api_data() (mesma estrutura das outras tabs)
# ══════════════════════════════════════════════════════════════════════════

def api_data():
    """Dados de arranque da página: quantos intervalos há por modalidade.

    O gráfico em si (perfil + evolução) é pedido à parte pelo JS, via
    /api/fisiologia/perfil_grafico e /api/fisiologia/evolucao — assim o
    utilizador escolhe a modalidade sem recarregar a página toda.
    """
    try:
        modalidades = modalidades_disponiveis()
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e), 'modalidades': []})
    return jsonify({'status': 'ok', 'modalidades': modalidades,
                    'campos_valor': CAMPOS_VALOR, 'campos_tempo': CAMPOS_TEMPO})


BODY = r"""
<h1>Metabolismo — perfil por watts</h1>
<div class="sub" id="sub">A carregar...</div>

<div class="controls">
  <label class="sel">Modalidade
    <select id="modalidade"></select></label>
</div>

<div id="avisoDados" class="sub" style="display:none;color:#E67E22"></div>

<h2>Perfil metabólico — o que é normal a cada faixa de watts</h2>
<div class="sub" id="subPerfil">A carregar...</div>
<div class="sub" style="font-size:11px;color:#8b949e">
  Valores medidos no <b>plateau</b> (fim do esforço, já estabilizado) — não a média do lap,
  que inclui o transitório e enviesa. Linha sólida = mediana; banda escura = p25-p75;
  banda clara = p10-p90; tracejado = pico atingido (janela que entra 30s no descanso).
  Intervalos em que a métrica nunca estabilizou são excluídos. Clica na legenda para
  ligar/desligar.</div>
<div class="legend" id="lgPerfil"></div>
<div class="chartbox">
  <canvas id="chPerfil" height="420"></canvas>
</div>

<h2>Evolução ao longo do tempo</h2>
<div class="sub">Mesma faixa de watts, mês a mês — para ver se o corpo está a
  responder de forma diferente com o tempo (adaptação, fadiga acumulada, etc.)</div>
<div class="controls">
  <label class="sel">Métrica
    <select id="campoEvolucao"></select></label>
  <label class="sel">Watts min <input type="number" id="wattsMin" value="200" style="width:70px"></label>
  <label class="sel">Watts max <input type="number" id="wattsMax" value="300" style="width:70px"></label>
  <button onclick="carregarEvolucao()">Actualizar</button>
</div>
<div class="chartbox">
  <canvas id="chEvolucao" height="240"></canvas>
</div>

<div class="sub" style="margin-top:20px">
  <a href="/api/fisiologia/status" target="_blank">JSON status</a> &middot;
  <a href="/api/fisiologia/perfil?modalidade=Row" target="_blank">JSON perfil (Row)</a> &middot;
  <a href="/api/fisiologia/processar?n=8" target="_blank">Processar mais 8 atividades</a>
</div>
"""

JS = r"""
let MODALIDADES = [];
let PERFIL = null;
let EVOLUCAO = null;

const CORES_METAB = {
 hr_plateau_work:'#E74C3C', smo2_plateau_work:'#F39C12', thb_plateau_work:'#2980B9',
 resp_plateau_work:'#1ABC9C', dfa1_plateau_work:'#9B59B6',
};
const LABELS_METAB = {
 hr_plateau_work:'HR (bpm)', smo2_plateau_work:'SmO2 (%)', thb_plateau_work:'tHb (a.u.)',
 resp_plateau_work:'Respiração (rpm)', dfa1_plateau_work:'DFA-α1',
};
const EXTREMO_DE = {
 hr_plateau_work:'hr_extremo', smo2_plateau_work:'smo2_extremo',
 thb_plateau_work:'thb_extremo', resp_plateau_work:'resp_extremo',
 dfa1_plateau_work:'dfa1_extremo',
};
const CAMPOS_METAB = Object.keys(CORES_METAB);

const LABEL_CAMPO_EVOL = {
 hr_plateau_work:'HR (esforço, plateau)', smo2_plateau_work:'SmO2 (esforço, plateau)',
 thb_plateau_work:'tHb (esforço, plateau)', resp_plateau_work:'Respiração (esforço, plateau)',
 dfa1_plateau_work:'DFA-α1 (esforço, plateau)',
 hr_extremo:'HR (pico)', smo2_extremo:'SmO2 (pico)', thb_extremo:'tHb (pico)',
 resp_extremo:'Respiração (pico)', dfa1_extremo:'DFA-α1 (pico)',
 hr_medio_work:'HR (média lap API — enviesado)',
 smo2_medio_work:'SmO2 (média lap API — enviesado)',
 lag_hr_50:'Lag HR (s)', lag_smo2_50:'Lag SmO2 (s)', lag_thb_50:'Lag tHb (s)',
 lag_resp_50:'Lag Respiração (s)', lag_dfa1_50:'Lag DFA-α1 (s)',
 rec_hr_50:'Recovery HR (s)', rec_smo2_50:'Recovery SmO2 (s)',
 rec_thb_50:'Recovery tHb (s)', rec_resp_50:'Recovery Respiração (s)',
 rec_dfa1_50:'Recovery DFA-α1 (s)',
};

// ── Gráfico principal: PAINÉIS EMPILHADOS, X = watts partilhado ────────────
// Painéis separados (e não sobreposição) porque as métricas têm unidades
// incompatíveis: tHb varia entre 12.5-12.8 e DFA-a1 entre 0.6-1.4. Num par
// de eixos partilhado, uma delas fica esmagada numa linha recta.
function drawPerfil(){
 const canvasId='chPerfil';
 if(!PERFIL||PERFIL.status!=='ok'){
  const o0=ctx(canvasId,240); if(o0) noData(o0.g,o0.W,o0.H,(PERFIL&&PERFIL.mensagem)||'Sem dados');
  return;
 }
 const faixas=PERFIL.faixas;
 const disponiveis=CAMPOS_METAB.filter(c=>faixas.some(f=>f[c]));

 document.getElementById('lgPerfil').innerHTML=disponiveis.map(function(c){
  const off=!ligado(canvasId,c);
  return '<span class="tog'+(off?' off':'')+'" data-c="'+canvasId+'" data-k="'+c+'">'+
   '<i style="background:'+CORES_METAB[c]+'"></i>'+LABELS_METAB[c]+'</span>';
 }).join('');
 document.querySelectorAll('#lgPerfil span.tog').forEach(function(sp){
  sp.onclick=function(){ alternar(sp.dataset.c, sp.dataset.k); drawPerfil(); };});

 const vis=disponiveis.filter(c=>ligado(canvasId,c));
 const o=ctx(canvasId,240); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 if(!faixas.length){noData(g,W,H,'Sem faixas com dados suficientes');return;}
 if(!vis.length){noData(g,W,H,'Nenhuma metrica seleccionada');return;}

 const PL=62,PR=120,PB=30,PT=20,w=W-PL-PR,h=H-PT-PB;
 const xs=faixas.map(f=>f.watts_centro);
 const xmin=Math.min.apply(null,xs), xmax=Math.max.apply(null,xs);
 const X=v=> xmax>xmin ? PL+w*(v-xmin)/(xmax-xmin) : PL+w/2;

 function hexRgba(hex,a){const h=hex.replace('#','');
  return 'rgba('+parseInt(h.substring(0,2),16)+','+parseInt(h.substring(2,4),16)+','+
   parseInt(h.substring(4,6),16)+','+a+')';}

 // Calcular escalas para cada métrica
 const escalas={};
 vis.forEach(function(c){
  const pts=faixas.filter(f=>f[c]);
  let a=Infinity,b=-Infinity;
  pts.forEach(function(f){const q=f[c];
   if(q.p10<a)a=q.p10; if(q.p90>b)b=q.p90;});
  if(!isFinite(a)){a=0;b=1;}
  const marg=(b-a)*0.15||1; a-=marg; b+=marg;
  const Y=v=>PT+h-(v-a)/(b-a)*h;
  escalas[c]={a:a,b:b,Y:Y,pts:pts};
 });

 // Grelha horizontal
 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let k=0;k<=2;k++){const y=PT+h*k/2;
  g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}

 // Desenhar cada métrica
 vis.forEach(function(c){
  const esc=escalas[c];
  const pts=esc.pts;
  
  // banda p25-p75
  g.fillStyle=hexRgba(CORES_METAB[c],0.08);
  g.beginPath();
  pts.forEach(function(f,j){const y=esc.Y(f[c].p75);
   if(j===0)g.moveTo(X(f.watts_centro),y); else g.lineTo(X(f.watts_centro),y);});
  for(let j=pts.length-1;j>=0;j--)g.lineTo(X(pts[j].watts_centro),esc.Y(pts[j][c].p25));
  g.closePath();g.fill();

  // linha p50
  g.strokeStyle=CORES_METAB[c];g.lineWidth=2.2;g.beginPath();
  pts.forEach(function(f,j){const y=esc.Y(f[c].p50);
   if(j===0)g.moveTo(X(f.watts_centro),y); else g.lineTo(X(f.watts_centro),y);});
  g.stroke();
  
  // marcadores
  g.fillStyle=CORES_METAB[c];
  pts.forEach(function(f){g.beginPath();g.arc(X(f.watts_centro),esc.Y(f[c].p50),2.5,0,7);g.fill();});
 });

 // Eixos Y à direita (um para cada métrica)
 g.fillStyle='#8b949e';g.font='9px sans-serif';g.textAlign='right';
 vis.forEach(function(c){
  const esc=escalas[c];
  // linha de escala
  g.strokeStyle=CORES_METAB[c];g.lineWidth=1.5;g.beginPath();
  g.moveTo(PL+w,PT);g.lineTo(PL+w,PT+h);g.stroke();
  // labels
  for(let k=0;k<=2;k++){
   const val=(esc.b-(esc.b-esc.a)*k/2).toFixed(1);
   const y=PT+h*k/2;
   g.fillText(val,PL+w+8,y+3);
   g.fillStyle=hexRgba(CORES_METAB[c],0.3);g.fillText(LABELS_METAB[c],PL+w+65,y-6);
   g.fillStyle='#8b949e';
  }
 });

 // eixo X inferior (WATTS)
 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='center';
 const step=Math.ceil(faixas.length/12);
 faixas.forEach(function(f,i){if(i%step!==0)return;
  g.fillText(Math.round(f.watts_centro)+'W',X(f.watts_centro),H-10);});
 g.font='bold 9px sans-serif';
 g.fillText('WATTS',PL+w/2,H-1);

 // eixo X superior (PACE para Row/Ski)
 if(faixas.some(f=>f.pace_medio)){
  g.fillStyle='#FF6B6B';g.font='bold 10px sans-serif';g.textAlign='center';
  g.fillText('PACE (min:ss)',PL+w/2,8);
  faixas.forEach(function(f,i){if(i%step!==0)return;
   if(f.pace_medio) g.fillText(f.pace_medio,X(f.watts_centro),12);
  });
 }

 // Tooltips
 registarTip(canvasId,function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  if(x<PL||x>PL+w)return '';
  let melhor=null,md=Infinity;
  faixas.forEach(function(f){const d=Math.abs(X(f.watts_centro)-x);
   if(d<md){md=d;melhor=f;}});
  if(!melhor)return '';
  let html='<div class="th">'+melhor.faixa_watts+' &nbsp;('+melhor.n_intervalos+' intervalos)';
  if(melhor.pace_medio) html+=' &nbsp;→ Pace: '+melhor.pace_medio;
  html+='</div>';
  vis.forEach(function(c){const q=melhor[c]; if(!q)return;
   let txt=q.p50+' <span style="color:#8b949e">[p25-p75 '+q.p25+'–'+q.p75+' · n='+q.n;
   if(q.n_excluidos_sem_plateau)txt+=' · '+q.n_excluidos_sem_plateau+' excl.';
   txt+=']</span>';
   html+=linhaTip(CORES_METAB[c],LABELS_METAB[c],txt);
  });
  return html;
 });
}


// ── Gráfico de evolução temporal: 1 série, X = período ──────────────────────
function drawEvolucao(){
 const canvasId='chEvolucao';
 const o=ctx(canvasId,240); if(!o)return;
 const g=o.g,W=o.W,H=o.H;

 if(!EVOLUCAO||EVOLUCAO.status!=='ok'||!EVOLUCAO.evolucao||!EVOLUCAO.evolucao.length){
  noData(g,W,H,'Sem dados suficientes nesta faixa'); return;
 }
 const pontos=EVOLUCAO.evolucao;
 const campo=EVOLUCAO.campo;
 const cor=CORES_METAB[campo]||'#5DADE2';

 const PL=56,PR=16,PT=12,PB=28,w=W-PL-PR,h=H-PT-PB,n=pontos.length;
 const X=i=>PL+w*(n>1?i/(n-1):0.5);
 let a=Infinity,b=-Infinity;
 pontos.forEach(function(p){if(p.p25<a)a=p.p25; if(p.p75>b)b=p.p75;});
 const marg=(b-a)*0.15||1; a-=marg; b+=marg;
 const Y=v=>PT+h-(v-a)/(b-a)*h;

 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}

 function hexRgba(hex,alpha){
  const h=hex.replace('#','');
  const r=parseInt(h.substring(0,2),16),gg=parseInt(h.substring(2,4),16),bl=parseInt(h.substring(4,6),16);
  return 'rgba('+r+','+gg+','+bl+','+alpha+')';
 }

 g.fillStyle=hexRgba(cor,0.2);g.beginPath();
 pontos.forEach(function(p,i){const y=Y(p.p75); if(i===0)g.moveTo(X(i),y); else g.lineTo(X(i),y);});
 for(let i=n-1;i>=0;i--){g.lineTo(X(i),Y(pontos[i].p25));}
 g.closePath();g.fill();

 g.strokeStyle=cor;g.lineWidth=2;g.beginPath();
 pontos.forEach(function(p,i){const y=Y(p.p50); if(i===0)g.moveTo(X(i),y); else g.lineTo(X(i),y);});
 g.stroke();
 g.fillStyle=cor;
 pontos.forEach(function(p,i){g.beginPath();g.arc(X(i),Y(p.p50),3,0,7);g.fill();});

 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++)g.fillText((b-(b-a)*i/4).toFixed(1),PL-6,PT+h*i/4+3);
 g.textAlign='center';
 const step=Math.ceil(n/8);
 pontos.forEach(function(p,i){if(i%step!==0)return; g.fillText(p.periodo,X(i),H-8);});
 g.textAlign='left';

 registarTip(canvasId,function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  if(x<PL||x>PL+w)return '';
  const i=Math.round((x-PL)/w*(n-1));
  const p=pontos[i]; if(!p)return '';
  let html='<div class="th">'+p.periodo+' (n='+p.n+')</div>'+
   linhaTip(cor,LABELS_METAB[campo]||campo,
    p.p50+' &nbsp;<span style="color:#8b949e">[p25-p75: '+p.p25+'–'+p.p75+']</span>');
  if(p.pace_p50) html+='<div style="margin-top:4px;font-size:10px;color:#FF6B6B">Pace: '+p.pace_p50+'</div>';
  return html;
 });
}

async function carregarPerfil(){
 const modalidade=document.getElementById('modalidade').value;
 const aviso=document.getElementById('avisoDados');
 aviso.style.display='none';
 let d;
 try{ d=await fetch('/api/fisiologia/perfil?modalidade='+modalidade).then(r=>r.json()); }
 catch(e){ PERFIL={status:'erro'}; drawPerfil(); return; }
 PERFIL=d;
 if(d.status!=='ok'){
  aviso.style.display='block';
  aviso.textContent=d.mensagem||('Dados insuficientes para '+modalidade+'.');
 } else {
  document.getElementById('subPerfil').textContent=
   d.n_intervalos_total+' intervalos, '+d.n_atividades+' atividades, '+
   d.n_dias_distintos+' dias distintos · bins de '+d.largura_bin_watts+'W · '+
   'range '+d.watts_min_observado+'-'+d.watts_max_observado+'W';
 }
 drawPerfil();
}

async function carregarEvolucao(){
 const modalidade=document.getElementById('modalidade').value;
 const campo=document.getElementById('campoEvolucao').value;
 const wmin=document.getElementById('wattsMin').value;
 const wmax=document.getElementById('wattsMax').value;
 const url='/api/fisiologia/evolucao?modalidade='+modalidade+'&campo='+campo+
  '&watts_min='+wmin+'&watts_max='+wmax;
 let d;
 try{ d=await fetch(url).then(r=>r.json()); }
 catch(e){ EVOLUCAO={status:'erro'}; drawEvolucao(); return; }
 EVOLUCAO=d;
 drawEvolucao();
}

async function load(){
 let d;
 try{ d=await fetch('/api/metabol').then(r=>r.json()); }
 catch(e){ document.getElementById('sub').innerHTML='<span class="err">Nao consegui carregar</span>'; return; }
 MODALIDADES=d.modalidades||[];
 if(!MODALIDADES.length){
  document.getElementById('sub').innerHTML=
   '<span class="err">Ainda sem intervalos processados. Corre /api/fisiologia/processar primeiro.</span>';
  return;
 }
 document.getElementById('sub').textContent=
  MODALIDADES.map(m=>m.modalidade+': '+m.n+' intervalos ('+m.n_atividades+' atividades)').join(' · ');

 const selMod=document.getElementById('modalidade');
 selMod.innerHTML=MODALIDADES.map(m=>
  '<option value="'+m.modalidade+'">'+m.modalidade+' ('+m.n+')</option>').join('');
 selMod.onchange=function(){ carregarPerfil(); carregarEvolucao(); };

 const selCampo=document.getElementById('campoEvolucao');
 const campos=(d.campos_valor||[]).concat(d.campos_tempo||[]);
 selCampo.innerHTML=campos.map(c=>
  '<option value="'+c+'">'+(LABEL_CAMPO_EVOL[c]||c)+'</option>').join('');
 selCampo.onchange=carregarEvolucao;

 carregarPerfil();
 carregarEvolucao();
}

window.addEventListener('resize',function(){
 if(PERFIL)drawPerfil();
 if(EVOLUCAO)drawEvolucao();
});

load();
"""


def render():
    return page('Metabolismo', SLUG, BODY, JS)


def validacao_lote_dfa(modalidade):
    """Validar qualidade DFA-α1."""
    from utils.dfa_artifacts_analyzer import DFAArtifactAnalyzer
    
    analyzer = DFAArtifactAnalyzer(modalidade=modalidade)
    conn = _conn()
    intervalos = conn.execute("""
        SELECT dfa1_plateau_work, hr_plateau_work, hr_extremo, watts_medio
        FROM fisiologia_intervalos
        WHERE modalidade = ? AND valido = 1 AND dfa1_plateau_work IS NOT NULL LIMIT 500
    """, (modalidade,)).fetchall()

    if not intervalos:
        return {'modalidade': modalidade, 'resumo': {'n_total': 0}}

    resultados = []
    for iv in intervalos:
        resultado = analyzer.analisar_intervalo(
            dfa1=float(iv['dfa1_plateau_work']) or 0.0,
            hr_medio=float(iv['hr_plateau_work']) or 100.0,
            hr_max=float(iv['hr_extremo']) or 110.0,
            watts_medio=float(iv['watts_medio']) or 0.0
        )
        resultados.append(resultado)

    resumo = analyzer.resumo_validacao(resultados)
    return {
        'modalidade': modalidade,
        'resumo': resumo,
        'detalhes_primeiros_10': [
            {'dfa1': r.dfa1_original, 'valido': r.esta_valido, 
             'confidence': r.confidence, 'motivo': r.motivo}
            for r in resultados[:10]
        ]
    }


def evolucao_temporal_com_pace(modalidade, campo, watts_min=None, watts_max=None, agregacao='mes'):
    """Wrapper: evolucao_temporal com nome para compatibilidade com API."""
    return evolucao_temporal(modalidade, campo, watts_min, watts_max, agregacao)
