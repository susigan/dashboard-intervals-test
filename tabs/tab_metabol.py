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


def perfil_por_modalidade(modalidade, min_n_total=20, n_faixas=4):
    """Curva watts -> métrica esperada, para 1 modalidade.

    Faixas de potência calculadas AGORA a partir dos dados reais desta
    modalidade (quartis dinâmicos) — nunca zonas fixas hardcoded.

    Retorna dict pronto para gráfico: 1 entrada por faixa de watts, cada
    uma com quartis de todas as métricas disponíveis (valor + tempo).
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
                        f'Precisa de pelo menos {min_n_total} para quartis com sentido. '
                        f'Continua a processar atividades e volta a tentar.'),
        }

    watts = np.array([l['watts_medio'] for l in linhas])
    n_datas = len(set(l['data'] for l in linhas))
    n_activities = len(set(l['activity_id'] for l in linhas))

    # Quartis de potência dinâmicos — recalculados sempre que há mais dados
    percentis_corte = np.linspace(0, 100, n_faixas + 1)
    cortes = np.percentile(watts, percentis_corte)
    # Garantir cortes estritamente crescentes (evita faixas vazias se houver
    # muitos valores repetidos, ex.: sessões sempre no mesmo alvo de watts)
    cortes = np.unique(cortes)

    faixas_saida = []
    for i in range(len(cortes) - 1):
        lo, hi = cortes[i], cortes[i + 1]
        ultima = (i == len(cortes) - 2)
        if ultima:
            mask = (watts >= lo) & (watts <= hi)
        else:
            mask = (watts >= lo) & (watts < hi)
        idxs = np.where(mask)[0]

        label = f'{lo:.0f}–{hi:.0f}W'
        faixa = {
            'faixa_watts': label,
            'watts_min': round(float(lo), 1),
            'watts_max': round(float(hi), 1),
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
        'watts_min_observado': round(float(watts.min()), 1),
        'watts_max_observado': round(float(watts.max()), 1),
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
<div class="sub">Faixas de potência calculadas a partir dos teus dados (não fixas).
  Linha sólida = valor mediano no esforço, banda = intervalo p25-p75, tracejado
  cinza = referência em repouso.</div>
<div id="graficoPerfil" class="chartbox">A carregar grafico...</div>

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
<div class="wrap" style="max-height:340px"><table>
  <thead><tr><th>Período</th><th class="num">n</th><th class="num">p25</th>
    <th class="num">p50</th><th class="num">p75</th></tr></thead>
  <tbody id="evolucaoBody"></tbody></table></div>

<div class="sub" style="margin-top:20px">
  <a href="/api/fisiologia/status" target="_blank">JSON status</a> &middot;
  <a href="/api/fisiologia/perfil?modalidade=Row" target="_blank">JSON perfil (Row)</a>
</div>
"""

JS = r"""
let MODALIDADES = [];
const LABEL_CAMPO = {
 hr_medio_work:'HR (esforço)', smo2_medio_work:'SmO2 (esforço)',
 thb_medio_work:'tHb (esforço)', resp_medio_work:'Respiração (esforço)',
 dfa1_medio_work:'DFA-α1 (esforço)',
 lag_hr_50:'Lag HR (s)', lag_smo2_50:'Lag SmO2 (s)', lag_thb_50:'Lag tHb (s)',
 lag_resp_50:'Lag Respiração (s)', lag_dfa1_50:'Lag DFA-α1 (s)',
 rec_hr_50:'Recovery HR (s)', rec_smo2_50:'Recovery SmO2 (s)',
 rec_thb_50:'Recovery tHb (s)', rec_resp_50:'Recovery Respiração (s)',
 rec_dfa1_50:'Recovery DFA-α1 (s)',
};

async function carregarGraficoPerfil(){
 const modalidade=document.getElementById('modalidade').value;
 const el=document.getElementById('graficoPerfil');
 const aviso=document.getElementById('avisoDados');
 el.innerHTML='A carregar grafico...'; aviso.style.display='none';
 let d;
 try{ d=await fetch('/api/fisiologia/perfil_grafico?modalidade='+modalidade).then(r=>r.json()); }
 catch(e){ el.innerHTML='<span class="err">Nao consegui carregar</span>'; return; }
 if(d.status!=='ok'){
  el.innerHTML='';
  aviso.style.display='block';
  aviso.textContent=d.mensagem||('Dados insuficientes para '+modalidade+
   ' ('+(d.n_disponivel||0)+' de '+(d.minimo_necessario||20)+' minimo).');
  return;
 }
 el.innerHTML=d.html;
 // reexecutar scripts injectados via innerHTML (necessario para o plotly.js do CDN correr)
 el.querySelectorAll('script').forEach(function(old){
  const s=document.createElement('script');
  if(old.src)s.src=old.src; else s.textContent=old.textContent;
  old.parentNode.replaceChild(s,old);
 });
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
 catch(e){ return; }
 const body=document.getElementById('evolucaoBody');
 if(d.status!=='ok'||!d.evolucao||!d.evolucao.length){
  body.innerHTML='<tr><td class="loading" colspan="5">Sem dados suficientes nesta faixa</td></tr>';
  return;
 }
 body.innerHTML=d.evolucao.map(function(p){
  return '<tr><td>'+p.periodo+'</td><td class="num">'+p.n+'</td>'+
   '<td class="num">'+p.p25+'</td><td class="num">'+p.p50+'</td>'+
   '<td class="num">'+p.p75+'</td></tr>';
 }).join('');
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
 selMod.onchange=function(){ carregarGraficoPerfil(); };

 const selCampo=document.getElementById('campoEvolucao');
 const campos=(d.campos_valor||[]).concat(d.campos_tempo||[]);
 selCampo.innerHTML=campos.map(c=>
  '<option value="'+c+'">'+(LABEL_CAMPO[c]||c)+'</option>').join('');
 selCampo.onchange=carregarEvolucao;

 carregarGraficoPerfil();
 carregarEvolucao();
}
load();
"""


def render():
    return page('Metabolismo', SLUG, BODY, JS)
