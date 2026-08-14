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


# Métricas de VALOR (patamar) — "quanto vale a X watts"
CAMPOS_VALOR = [
    'hr_medio_work', 'smo2_medio_work', 'thb_medio_work',
    'resp_medio_work', 'dfa1_medio_work',
]

# Métricas de TEMPO (lag de resposta e recovery) — "quanto demora"
CAMPOS_TEMPO = [
    'lag_hr_50', 'lag_smo2_50', 'lag_thb_50', 'lag_resp_50', 'lag_dfa1_50',
    'rec_hr_50', 'rec_smo2_50', 'rec_thb_50', 'rec_resp_50', 'rec_dfa1_50',
]

TODOS_CAMPOS = CAMPOS_VALOR + CAMPOS_TEMPO

MIN_POR_FAIXA = 3   # abaixo disto, não vale a pena mostrar quartis (ruído)


def _conn():
    conn = ddf.get_conn()
    conn.row_factory = sqlite3.Row
    return conn


def _quartis(valores):
    vs = np.array([v for v in valores if v is not None], dtype=float)
    vs = vs[np.isfinite(vs)]
    if len(vs) < MIN_POR_FAIXA:
        return None
    return {
        'n': len(vs),
        'p25': round(float(np.percentile(vs, 25)), 2),
        'p50': round(float(np.percentile(vs, 50)), 2),
        'p75': round(float(np.percentile(vs, 75)), 2),
        'min': round(float(np.min(vs)), 2),
        'max': round(float(np.max(vs)), 2),
    }


def perfil_por_modalidade(modalidade, min_n_total=15, n_faixas=8):
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
    linhas = conn.execute(
        """SELECT watts_medio, data, activity_id, """ + ", ".join(TODOS_CAMPOS) + """
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
        largura_bin = max(15.0, min(60.0, largura_bin))

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

        faixa = {
            'faixa_watts': f'{lo:.0f}-{hi:.0f}W',
            'watts_min': round(float(lo), 1),
            'watts_max': round(float(hi), 1),
            'watts_centro': round((lo + hi) / 2, 1),
            'n_intervalos': len(idxs),
        }
        for campo in TODOS_CAMPOS:
            valores = [linhas[j][campo] for j in idxs]
            faixa[campo] = _quartis(valores)
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
        f"""SELECT data, {campo} as valor FROM fisiologia_intervalos
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
    for l in linhas:
        p = _periodo(l['data'])
        grupos.setdefault(p, []).append(l['valor'])

    saida = []
    for periodo in sorted(grupos.keys()):
        q = _quartis(grupos[periodo])
        if q and q['n'] >= min_por_periodo:
            saida.append({'periodo': periodo, **q})

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
<div class="sub" id="subPerfil">Bins de largura fixa (não por contagem) — cada bin só
  aparece se tiver dados. Linha solida = mediana; banda escura = p25-p75; banda clara =
  min-max observado (para não esconder picos). Clica na legenda para ligar/desligar séries.</div>
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
 hr_medio_work:'#E74C3C', smo2_medio_work:'#F39C12', thb_medio_work:'#2980B9',
 resp_medio_work:'#1ABC9C', dfa1_medio_work:'#9B59B6',
};
const LABELS_METAB = {
 hr_medio_work:'HR (bpm)', smo2_medio_work:'SmO2 (%)', thb_medio_work:'tHb (a.u.)',
 resp_medio_work:'Respiração (rpm)', dfa1_medio_work:'DFA-α1',
};
const CAMPOS_METAB = Object.keys(CORES_METAB);

const LABEL_CAMPO_EVOL = {
 hr_medio_work:'HR (esforço)', smo2_medio_work:'SmO2 (esforço)',
 thb_medio_work:'tHb (esforço)', resp_medio_work:'Respiração (esforço)',
 dfa1_medio_work:'DFA-α1 (esforço)',
 lag_hr_50:'Lag HR (s)', lag_smo2_50:'Lag SmO2 (s)', lag_thb_50:'Lag tHb (s)',
 lag_resp_50:'Lag Respiração (s)', lag_dfa1_50:'Lag DFA-α1 (s)',
 rec_hr_50:'Recovery HR (s)', rec_smo2_50:'Recovery SmO2 (s)',
 rec_thb_50:'Recovery tHb (s)', rec_resp_50:'Recovery Respiração (s)',
 rec_dfa1_50:'Recovery DFA-α1 (s)',
};

// ── Gráfico principal: várias métricas sobrepostas, X = watts ──────────────
function drawPerfil(){
 const canvasId='chPerfil';
 const o=ctx(canvasId,420); if(!o)return;
 const g=o.g,W=o.W,H=o.H;

 if(!PERFIL||PERFIL.status!=='ok'){
  noData(g,W,H,(PERFIL&&PERFIL.mensagem)||'Sem dados'); return;
 }
 const faixas=PERFIL.faixas;
 if(!faixas.length){noData(g,W,H,'Sem faixas com dados suficientes');return;}

 const disponiveis=CAMPOS_METAB.filter(c=>faixas.some(f=>f[c]));
 document.getElementById('lgPerfil').innerHTML=disponiveis.map(function(c){
  const off=!ligado(canvasId,c);
  return '<span class="tog'+(off?' off':'')+'" data-c="'+canvasId+'" data-k="'+c+'">'+
   '<i style="background:'+CORES_METAB[c]+'"></i>'+LABELS_METAB[c]+'</span>';
 }).join('');
 document.querySelectorAll('#lgPerfil span.tog').forEach(function(sp){
  sp.onclick=function(){ alternar(sp.dataset.c, sp.dataset.k); drawPerfil(); };});

 const vis=disponiveis.filter(c=>ligado(canvasId,c));
 if(!vis.length){noData(g,W,H,'Nenhuma metrica seleccionada');return;}

 const PL=58,PR=58,PT=14,PB=32,w=W-PL-PR,h=H-PT-PB;
 const xs=faixas.map(f=>f.watts_centro);
 const xmin=Math.min.apply(null,xs), xmax=Math.max.apply(null,xs);
 const X=v=> xmax>xmin ? PL+w*(v-xmin)/(xmax-xmin) : PL+w/2;

 // escala propria por serie (mesma tecnica de tab_corporal: cada metrica
 // tem o seu proprio [min,max] mapeado para a mesma altura do grafico)
 const lim={};
 vis.forEach(function(c){
  let a=Infinity,b=-Infinity;
  faixas.forEach(function(f){const q=f[c]; if(!q)return;
   if(q.min<a)a=q.min; if(q.max>b)b=q.max;});
  if(!isFinite(a)){a=0;b=1;}
  const marg=(b-a)*0.12||1;
  lim[c]=[a-marg,b+marg];
 });

 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}

 function hexRgba(hex,alpha){
  const h=hex.replace('#','');
  const r=parseInt(h.substring(0,2),16),gg=parseInt(h.substring(2,4),16),b=parseInt(h.substring(4,6),16);
  return 'rgba('+r+','+gg+','+b+','+alpha+')';
 }

 vis.forEach(function(c){
  const[a,b]=lim[c];
  const Y=v=>PT+h-(v-a)/(b-a)*h;
  const pts=faixas.filter(f=>f[c]);
  if(!pts.length)return;

  // banda min-max (mais clara -- mostra os extremos reais, nao escondidos)
  g.fillStyle=hexRgba(CORES_METAB[c],0.10);
  g.beginPath();
  pts.forEach(function(f,i){const y=Y(f[c].max);
   if(i===0)g.moveTo(X(f.watts_centro),y); else g.lineTo(X(f.watts_centro),y);});
  for(let i=pts.length-1;i>=0;i--){g.lineTo(X(pts[i].watts_centro),Y(pts[i][c].min));}
  g.closePath();g.fill();

  // banda p25-p75
  g.fillStyle=hexRgba(CORES_METAB[c],0.22);
  g.beginPath();
  pts.forEach(function(f,i){const y=Y(f[c].p75);
   if(i===0)g.moveTo(X(f.watts_centro),y); else g.lineTo(X(f.watts_centro),y);});
  for(let i=pts.length-1;i>=0;i--){g.lineTo(X(pts[i].watts_centro),Y(pts[i][c].p25));}
  g.closePath();g.fill();

  // linha p50 + marcadores
  g.strokeStyle=CORES_METAB[c];g.lineWidth=2.2;g.beginPath();
  pts.forEach(function(f,i){const y=Y(f[c].p50);
   if(i===0)g.moveTo(X(f.watts_centro),y); else g.lineTo(X(f.watts_centro),y);});
  g.stroke();
  g.fillStyle=CORES_METAB[c];
  pts.forEach(function(f){g.beginPath();g.arc(X(f.watts_centro),Y(f[c].p50),3.2,0,7);g.fill();});
 });

 // eixos Y -- so as 2 primeiras series visiveis, para nao poluir
 g.font='10px sans-serif';
 vis.slice(0,2).forEach(function(c,idx){
  const[a,b]=lim[c],dir=idx===1;
  g.fillStyle=CORES_METAB[c];g.textAlign=dir?'left':'right';
  for(let i=0;i<=4;i++){const v=b-(b-a)*i/4;
   g.fillText(v.toFixed(1),dir?PL+w+6:PL-6,PT+h*i/4+3);}
 });

 // eixo X: watts (centro de cada bin)
 g.fillStyle='#8b949e';g.textAlign='center';
 const step=Math.ceil(faixas.length/14);
 faixas.forEach(function(f,i){if(i%step!==0)return;
  g.fillText(Math.round(f.watts_centro)+'W',X(f.watts_centro),H-8);});
 g.textAlign='left';

 registarTip(canvasId,function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  if(x<PL||x>PL+w)return '';
  let melhor=null,melhorD=Infinity;
  faixas.forEach(function(f){const d=Math.abs(X(f.watts_centro)-x); if(d<melhorD){melhorD=d;melhor=f;}});
  if(!melhor)return '';
  let html='<div class="th">'+melhor.faixa_watts+' (n='+melhor.n_intervalos+' intervalos)</div>';
  vis.forEach(function(c){const q=melhor[c]; if(!q)return;
   html+=linhaTip(CORES_METAB[c],LABELS_METAB[c],
    q.p50+' &nbsp;<span style="color:#8b949e">[p25-p75: '+q.p25+'–'+q.p75+
    ', min-max: '+q.min+'–'+q.max+', n='+q.n+']</span>');});
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
  return '<div class="th">'+p.periodo+' (n='+p.n+')</div>'+
   linhaTip(cor,LABELS_METAB[campo]||campo,
    p.p50+' &nbsp;<span style="color:#8b949e">[p25-p75: '+p.p25+'–'+p.p75+']</span>');
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
   'range observado '+d.watts_min_observado+'-'+d.watts_max_observado+'W';
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
