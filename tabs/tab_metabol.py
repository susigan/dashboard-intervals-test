"""tab_metabol.py — Tab "Metabolismo": perfil metabólico por watts + pace."""

from flask import jsonify, request

import numpy as np
import sqlite3
from datetime import datetime

import drive_db_fisiologia as ddf
from tabs.base import page

SLUG = 'metabol'

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
    conn = _conn()
    resultado = conn.execute("""
        SELECT modalidade, COUNT(*) as n, COUNT(DISTINCT data) as n_dias, COUNT(DISTINCT activity_id) as n_atividades
        FROM fisiologia_intervalos
        WHERE valido = 1 AND watts_medio IS NOT NULL
        GROUP BY modalidade
        ORDER BY modalidade
    """).fetchall()
    return [
        {'modalidade': r['modalidade'], 'n': r['n'], 'n_dias': r['n_dias'], 'n_atividades': r['n_atividades']}
        for r in resultado
    ]


def perfil_por_modalidade(modalidade, min_n_total=15, n_faixas=10, so_plateau_valido=True):
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


def evolucao_temporal(modalidade, campo, watts_min=None, watts_max=None, agregacao='mes', min_por_periodo=3):
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


def validacao_lote_dfa(modalidade):
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
    resultado = evolucao_temporal(modalidade, campo, watts_min, watts_max, agregacao)
    if resultado.get('status') == 'ok':
        resultado['evolucao'] = resultado.pop('periodos', [])
        resultado['n_periodos'] = len(resultado['evolucao'])
    return resultado


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

function drawPerfil(){
 const canvasId='chPerfil';
 if(!PERFIL||PERFIL.status!=='ok'){
  if(!document.getElementById(canvasId))return;
  return;
 }
 const faixas=PERFIL.faixas;
 if(!faixas)return;
 const disponiveis=CAMPOS_METAB.filter(c=>faixas.some(f=>f[c]));
 if(!disponiveis.length)return;
}

function drawEvolucao(){
 const canvasId='chEvolucao';
 if(!EVOLUCAO||EVOLUCAO.status!=='ok'){
  return;
 }
}

async function carregarPerfil(){
 const modalidade=document.getElementById('modalidade').value;
 let d;
 try{ d=await fetch('/api/fisiologia/perfil?modalidade='+modalidade).then(r=>r.json()); }
 catch(e){ PERFIL={status:'erro'}; return; }
 PERFIL=d;
}

async function carregarEvolucao(){
 const modalidade=document.getElementById('modalidade').value;
 const campo=document.getElementById('campoEvolucao').value;
 const wmin=document.getElementById('wattsMin').value;
 const wmax=document.getElementById('wattsMax').value;
 const url='/api/fisiologia/evolucao?modalidade='+modalidade+'&campo='+campo+'&watts_min='+wmin+'&watts_max='+wmax;
 let d;
 try{ d=await fetch(url).then(r=>r.json()); }
 catch(e){ EVOLUCAO={status:'erro'}; return; }
 EVOLUCAO=d;
}

async function load(){
 let d;
 try{ d=await fetch('/api/metabol').then(r=>r.json()); }
 catch(e){ return; }
 MODALIDADES=d.modalidades||[];
 if(!MODALIDADES.length)return;

 const selMod=document.getElementById('modalidade');
 selMod.innerHTML=MODALIDADES.map(m=>'<option value="'+m.modalidade+'">'+m.modalidade+' ('+m.n+')</option>').join('');
 selMod.onchange=function(){ carregarPerfil(); carregarEvolucao(); };

 const selCampo=document.getElementById('campoEvolucao');
 const campos=(d.campos_valor||[]).concat(d.campos_tempo||[]);
 selCampo.innerHTML=campos.map(c=>'<option value="'+c+'">'+(LABEL_CAMPO_EVOL[c]||c)+'</option>').join('');
 selCampo.onchange=carregarEvolucao;

 carregarPerfil();
 carregarEvolucao();
}

load();
"""


def api_data():
    try:
        modalidades = modalidades_disponiveis()
    except Exception as e:
        return jsonify({'status': 'erro', 'modalidades': []})
    return jsonify({'status': 'ok', 'modalidades': modalidades,
                    'campos_valor': CAMPOS_VALOR, 'campos_tempo': CAMPOS_TEMPO})


def render():
    return page('Metabolismo', SLUG, BODY, JS)
