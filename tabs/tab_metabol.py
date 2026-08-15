"""tab_metabol.py — Análise robusta v3: ponderação + tooltips + pace + evolução."""

from flask import jsonify, request
import numpy as np
import sqlite3
from datetime import datetime

import drive_db_fisiologia as ddf
from tabs.base import page

SLUG = 'metabol'

CAMPOS_VALOR = ['hr_max_60s', 'hr_avg_60s', 'resp_avg_60s', 'smo2_min_60s', 'dfa1_clean']
CAMPOS_TEMPO = []
TODOS_CAMPOS = CAMPOS_VALOR + CAMPOS_TEMPO

CORES_METAB = {
    'hr_max_60s': '#E74C3C',
    'hr_avg_60s': '#C0392B',
    'resp_avg_60s': '#1ABC9C',
    'smo2_min_60s': '#F39C12',
    'dfa1_clean': '#9B59B6',
}

LABELS_METAB = {
    'hr_max_60s': 'HR Max (bpm)',
    'hr_avg_60s': 'HR Avg (bpm)',
    'resp_avg_60s': 'Respiração (rpm)',
    'smo2_min_60s': 'SmO₂ Min (%)',
    'dfa1_clean': 'DFA-α1 (clean)',
}

def _conn():
    conn = ddf.get_conn()
    conn.row_factory = sqlite3.Row
    return conn

def _watts_para_pace(watts, modalidade='Row'):
    """Converte watts para pace (min:ss)."""
    if modalidade not in ['Row', 'Ski']:
        return None
    if watts <= 0:
        return None
    pace_seg = 500.0 / ((watts / 2.8) ** (1/3))
    min_val = int(pace_seg // 60)
    seg_val = int(pace_seg % 60)
    return f'{min_val}:{seg_val:02d}'

def _watts_para_pace_run(watts):
    """Converte watts para pace/km (run)."""
    if watts <= 0:
        return None
    pace_seg = 200.0 / np.sqrt(watts / 75.0)
    min_val = int(pace_seg // 60)
    seg_val = int(pace_seg % 60)
    return f'{min_val}:{seg_val:02d} /km'

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

def perfil_por_modalidade(modalidade, min_n_total=15, n_faixas=10, peso_ultimos=1.5):
    """Perfil com PONDERAÇÃO nos últimos intervalos."""
    conn = _conn()
    colunas = ", ".join(TODOS_CAMPOS)
    linhas = conn.execute(
        f"""SELECT watts_medio, data, activity_id, interval_num, {colunas}
           FROM fisiologia_intervalos
           WHERE modalidade = ? AND valido = 1 AND watts_medio IS NOT NULL
           ORDER BY data DESC, activity_id DESC, interval_num DESC""",
        (modalidade,)
    ).fetchall()

    if len(linhas) < min_n_total:
        return {
            'status': 'dados_insuficientes',
            'modalidade': modalidade,
            'n_disponivel': len(linhas),
            'minimo_necessario': min_n_total,
        }

    n_linhas = len(linhas)
    corte = int(n_linhas * 0.3)
    pesos = np.ones(n_linhas)
    pesos[:corte] = peso_ultimos

    watts = np.array([l['watts_medio'] for l in linhas])
    wmin, wmax = float(watts.min()), float(watts.max())
    intervalo_total = wmax - wmin
    largura_bin = max(10.0, min(30.0, intervalo_total / n_faixas if intervalo_total > 0 else 20.0))

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
        
        if modalidade in ['Row', 'Ski']:
            pace = _watts_para_pace(watts_centro, modalidade)
            if pace:
                faixa['pace_medio'] = pace
        elif modalidade == 'Run':
            pace = _watts_para_pace_run(watts_centro)
            if pace:
                faixa['pace_medio'] = pace
        
        for campo in TODOS_CAMPOS:
            valores = [linhas[j][campo] for j in idxs]
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
                
                ps_cum = np.cumsum(ps_sorted) / np.sum(ps_sorted)
                
                faixa[campo] = {
                    'p10': round(float(vs_sorted[min(np.searchsorted(ps_cum, 0.10), len(vs_sorted)-1)]), 2),
                    'p25': round(float(vs_sorted[min(np.searchsorted(ps_cum, 0.25), len(vs_sorted)-1)]), 2),
                    'p50': round(float(vs_sorted[min(np.searchsorted(ps_cum, 0.50), len(vs_sorted)-1)]), 2),
                    'p75': round(float(vs_sorted[min(np.searchsorted(ps_cum, 0.75), len(vs_sorted)-1)]), 2),
                    'p90': round(float(vs_sorted[min(np.searchsorted(ps_cum, 0.90), len(vs_sorted)-1)]), 2),
                    'n': len(vs_validos),
                }
        
        faixas_saida.append(faixa)

    return {
        'status': 'ok',
        'modalidade': modalidade,
        'n_intervalos_total': len(linhas),
        'faixas': faixas_saida,
    }

def evolucao_temporal(modalidade, campo, watts_min=None, watts_max=None, agregacao='mes', min_por_periodo=3):
    """Evolução temporal com ponderação."""
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
    watts_grupos = {}
    for l in linhas:
        p = _periodo(l['data'])
        grupos.setdefault(p, []).append(l['valor'])
        watts_grupos.setdefault(p, []).append(l['watts_medio'] if l['watts_medio'] else 0)

    saida = []
    for periodo in sorted(grupos.keys()):
        vs = [v for v in grupos[periodo] if v is not None and np.isfinite(v)]
        if len(vs) < min_por_periodo:
            continue
        
        vs_arr = np.array(vs)
        saida.append({
            'periodo': periodo,
            'p10': round(float(np.percentile(vs_arr, 10)), 2),
            'p25': round(float(np.percentile(vs_arr, 25)), 2),
            'p50': round(float(np.percentile(vs_arr, 50)), 2),
            'p75': round(float(np.percentile(vs_arr, 75)), 2),
            'p90': round(float(np.percentile(vs_arr, 90)), 2),
            'n': len(vs),
        })

    return {
        'status': 'ok',
        'campo': campo,
        'periodos': saida,
    }

BODY = r"""
<h1>Metabolismo — perfil por watts (análise robusta)</h1>
<div class="sub" id="sub">A carregar...</div>

<div class="controls">
  <label class="sel">Modalidade
    <select id="modalidade"></select></label>
</div>

<h2>Perfil metabólico — ponderado com últimos 30% de intervalos (1.5x peso)</h2>
<div id="tooltip" style="position:absolute;background:#000;color:#fff;padding:8px;border-radius:3px;font-size:11px;display:none;z-index:1000;pointer-events:none;border:1px solid #666;"></div>
<div class="legend" id="lgPerfil"></div>
<div class="chartbox">
  <canvas id="chPerfil" height="280"></canvas>
</div>

<h2>Evolução ao longo do tempo</h2>
<div class="controls">
  <label class="sel">Métrica
    <select id="campoEvolucao"></select></label>
  <label class="sel">Watts min <input type="number" id="wattsMin" value="200" style="width:70px"></label>
  <label class="sel">Watts max <input type="number" id="wattsMax" value="350" style="width:70px"></label>
  <button onclick="carregarEvolucao()">Actualizar</button>
</div>
<div class="chartbox">
  <canvas id="chEvolucao" height="240"></canvas>
</div>
"""

JS = r"""
let MODALIDADES = [];
let PERFIL = null;
let EVOLUCAO = null;

const CORES_METAB = {
 hr_max_60s:'#E74C3C', hr_avg_60s:'#C0392B', resp_avg_60s:'#1ABC9C',
 smo2_min_60s:'#F39C12', dfa1_clean:'#9B59B6',
};
const LABELS_METAB = {
 hr_max_60s:'HR Max (bpm)', hr_avg_60s:'HR Avg (bpm)', resp_avg_60s:'Respiração (rpm)',
 smo2_min_60s:'SmO₂ Min (%)', dfa1_clean:'DFA-α1 (clean)',
};

let chartState = {chPerfil: {}, chEvolucao: {}};

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
 const o = ctx('chPerfil', 280);
 if(!o) return;
 const g = o.g, W = o.W, H = o.H;
 
 if(!PERFIL || PERFIL.status !== 'ok'){
  noData(g, W, H, 'Sem dados');
  return;
 }
 
 const faixas = PERFIL.faixas;
 if(!faixas || !faixas.length){
  noData(g, W, H, 'Sem faixas');
  return;
 }
 
 const disponiveis = Object.keys(CORES_METAB).filter(c => faixas.some(f => f[c]));
 
 document.getElementById('lgPerfil').innerHTML = disponiveis.map(function(c){
  const off = !ligado('chPerfil', c);
  return '<span class="tog'+(off?' off':'')+'" data-c="chPerfil" data-k="'+c+'" style="cursor:pointer;margin-right:15px;"><i style="display:inline-block;width:10px;height:10px;background:'+CORES_METAB[c]+';margin-right:5px;"></i>'+LABELS_METAB[c]+'</span>';
 }).join('');
 document.querySelectorAll('#lgPerfil span.tog').forEach(function(sp){
  sp.onclick = function(){ alternar(sp.dataset.c, sp.dataset.k); drawPerfil(); };
 });
 
 const vis = disponiveis.filter(c => ligado('chPerfil', c));
 if(!vis.length){
  noData(g, W, H, 'Nenhuma métrica');
  return;
 }
 
 const PL = 70, PR = 120, PB = 35, PT = 25, w = W - PL - PR, h = H - PT - PB;
 const xs = faixas.map(f => f.watts_centro);
 const xmin = Math.min.apply(null, xs), xmax = Math.max.apply(null, xs);
 const X = v => xmax > xmin ? PL + w*(v-xmin)/(xmax-xmin) : PL + w/2;
 
 function hexRgba(hex, a){
  const h = hex.replace('#', '');
  return 'rgba('+parseInt(h.substring(0,2),16)+','+parseInt(h.substring(2,4),16)+','+parseInt(h.substring(4,6),16)+','+a+')';
 }
 
 const escalas = {};
 vis.forEach(function(c){
  const pts = faixas.filter(f => f[c]);
  let a = Infinity, b = -Infinity;
  pts.forEach(function(f){
   const q = f[c];
   if(q.p10 < a) a = q.p10;
   if(q.p90 > b) b = q.p90;
  });
  if(!isFinite(a)){ a = 0; b = 1; }
  const marg = (b-a)*0.15 || 1;
  a -= marg; b += marg;
  const Y = v => PT + h - (v-a)/(b-a)*h;
  escalas[c] = {a: a, b: b, Y: Y, pts: pts};
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
 
 vis.forEach(function(c){
  const esc = escalas[c];
  const pts = esc.pts;
  
  g.fillStyle = hexRgba(CORES_METAB[c], 0.08);
  g.beginPath();
  pts.forEach(function(f, j){
   const y = esc.Y(f[c].p75);
   if(j === 0) g.moveTo(X(f.watts_centro), y);
   else g.lineTo(X(f.watts_centro), y);
  });
  for(let j = pts.length-1; j >= 0; j--){
   g.lineTo(X(pts[j].watts_centro), esc.Y(pts[j][c].p25));
  }
  g.closePath();
  g.fill();
  
  g.strokeStyle = CORES_METAB[c];
  g.lineWidth = 2.5;
  g.beginPath();
  pts.forEach(function(f, j){
   const y = esc.Y(f[c].p50);
   if(j === 0) g.moveTo(X(f.watts_centro), y);
   else g.lineTo(X(f.watts_centro), y);
  });
  g.stroke();
  
  g.fillStyle = CORES_METAB[c];
  pts.forEach(function(f){
   g.beginPath();
   g.arc(X(f.watts_centro), esc.Y(f[c].p50), 3, 0, 7);
   g.fill();
  });
 });
 
 g.fillStyle = '#8b949e';
 g.font = '10px sans-serif';
 g.textAlign = 'center';
 faixas.forEach(function(f, i){
  if(i % 3 !== 0) return;
  g.fillText(Math.round(f.watts_centro)+'W', X(f.watts_centro), H-15);
 });
 
 if(faixas.some(f => f.pace_medio)){
  g.fillStyle = '#FF6B6B';
  g.font = 'bold 12px sans-serif';
  g.textAlign = 'center';
  g.fillText('PACE', PL + w/2, 15);
  faixas.forEach(function(f, i){
   if(i % 3 !== 0) return;
   if(f.pace_medio){
    g.font = '9px sans-serif';
    g.fillStyle = '#FF6B6B';
    g.fillText(f.pace_medio, X(f.watts_centro), 28);
   }
  });
 }
 
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
  const faixa = faixas.find(f => Math.abs(f.watts_centro - watts) < 15);
  
  if(faixa){
   tooltip.innerHTML = '<b>'+faixa.faixa_watts+'</b><br/>'+faixa.n_intervalos+' intervalos';
   tooltip.style.left = (evt.clientX + 10) + 'px';
   tooltip.style.top = (evt.clientY + 10) + 'px';
   tooltip.style.display = 'block';
  } else {
   tooltip.style.display = 'none';
  }
 };
}

function drawEvolucao(){
 const o = ctx('chEvolucao', 240);
 if(!o) return;
 const g = o.g, W = o.W, H = o.H;
 
 if(!EVOLUCAO || EVOLUCAO.status !== 'ok'){
  noData(g, W, H, 'Sem dados para este período');
  return;
 }
 
 const periodos = EVOLUCAO.periodos || [];
 if(!periodos.length){
  noData(g, W, H, 'Sem dados');
  return;
 }
 
 const PL = 70, PR = 80, PB = 30, PT = 20, w = W - PL - PR, h = H - PT - PB;
 const campo = document.getElementById('campoEvolucao').value;
 const cor = CORES_METAB[campo] || '#999';
 
 const valores = periodos.map(p => p.p50);
 const vmin = Math.min.apply(null, valores);
 const vmax = Math.max.apply(null, valores);
 const vmarg = (vmax - vmin) * 0.15 || 1;
 const va = vmin - vmarg;
 const vb = vmax + vmarg;
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
 
 g.font = '9px sans-serif';
 g.textAlign = 'right';
 for(let k = 0; k <= 2; k++){
  const val = (vb - (vb-va)*k/2).toFixed(1);
  const y = PT + h*k/2;
  g.fillText(val, PL-5, y+3);
 }
}

async function carregarPerfil(){
 const modalidade = document.getElementById('modalidade').value;
 try{
  const d = await fetch('/api/fisiologia/perfil_robusto/'+modalidade).then(r => r.json());
  PERFIL = d;
  if(PERFIL.status === 'ok') drawPerfil();
 }catch(e){
  console.error('Erro carregando perfil:', e);
  PERFIL = {status: 'erro'};
  drawPerfil();
 }
}

async function carregarEvolucao(){
 const modalidade = document.getElementById('modalidade').value;
 const campo = document.getElementById('campoEvolucao').value;
 const wmin = document.getElementById('wattsMin').value || null;
 const wmax = document.getElementById('wattsMax').value || null;
 const url = '/api/fisiologia/evolucao_robusta?modalidade='+modalidade+'&campo='+campo+(wmin?'&watts_min='+wmin:'')+(wmax?'&watts_max='+wmax:'');
 try{
  const d = await fetch(url).then(r => r.json());
  EVOLUCAO = d;
  drawEvolucao();
 }catch(e){
  console.error('Erro carregando evolução:', e);
  EVOLUCAO = {status: 'erro'};
  drawEvolucao();
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
   carregarPerfil();
   carregarEvolucao();
  };
  
  const selCampo = document.getElementById('campoEvolucao');
  const campos = Object.keys(LABELS_METAB);
  selCampo.innerHTML = campos.map(c => '<option value="'+c+'">'+LABELS_METAB[c]+'</option>').join('');
  selCampo.onchange = carregarEvolucao;
  
  carregarPerfil();
  carregarEvolucao();
 }catch(e){
  console.error('Erro no load:', e);
 }
}

load();
"""

def api_data():
    try:
        modalidades = modalidades_disponiveis()
    except Exception as e:
        return jsonify({'status': 'erro', 'modalidades': []})
    return jsonify({'status': 'ok', 'modalidades': modalidades, 'campos_valor': CAMPOS_VALOR})

def render():
    from flask import render_template_string
    return render_template_string(page(SLUG, 'Metabolismo (v3 - Robusto Completo)', BODY, JS))
