"""tab_metabol_v2.py — Tab "Metabolismo" com análise robusta de intervalos."""

from flask import jsonify, request
import numpy as np
import sqlite3
from datetime import datetime

import drive_db_fisiologia as ddf
from tabs.base import page

SLUG = 'metabol'

# ── Campos para análise (NOVOS v2) ──────────────────────────────────────────
CAMPOS_VALOR = [
    'hr_max_60s', 'hr_avg_60s',
    'resp_avg_60s',
    'smo2_min_60s',
    'dfa1_clean',
]

CAMPOS_TEMPO = []  # Não usamos mais lag/recovery

TODOS_CAMPOS = CAMPOS_VALOR + CAMPOS_TEMPO

# Labels para gráfico
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

_PREFIXOS = ('hr', 'resp', 'smo2', 'dfa1')

def _prefixo_de(campo):
    for p in _PREFIXOS:
        if campo.startswith(p):
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


def perfil_por_modalidade(modalidade, min_n_total=15, n_faixas=10):
    """Perfil watts -> métricas (análise robusta)."""
    conn = _conn()
    colunas = ", ".join(TODOS_CAMPOS)
    linhas = conn.execute(
        f"""SELECT watts_medio, data, activity_id, {colunas}
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
        
        # Pace para Row/Ski
        if modalidade in ['Row', 'Ski']:
            pace = _watts_para_pace(watts_centro, modalidade)
            if pace:
                faixa['pace_medio'] = pace
        
        # Métricas
        for campo in TODOS_CAMPOS:
            valores = [linhas[j][campo] for j in idxs]
            q = _quartis(valores)
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
    """Evolução temporal de uma métrica."""
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
        q = _quartis(grupos[periodo])
        if q and q['n'] >= min_por_periodo:
            registro = {'periodo': periodo, **q}
            if modalidade in ['Row', 'Ski'] and watts_grupos[periodo]:
                watts_p50 = np.percentile([w for w in watts_grupos[periodo] if w > 0], 50)
                pace = _watts_para_pace(watts_p50, modalidade)
                if pace:
                    registro['pace_p50'] = pace
            saida.append(registro)

    return {
        'status': 'ok',
        'modalidade': modalidade,
        'campo': campo,
        'watts_min': watts_min,
        'watts_max': watts_max,
        'periodos': saida,
        'gerado_em': datetime.now().isoformat(timespec='seconds'),
    }


# ── JavaScript do gráfico ───────────────────────────────────────────────────
BODY = r"""
<h1>Metabolismo — perfil por watts (análise robusta)</h1>
<div class="sub" id="sub">A carregar...</div>

<div class="controls">
  <label class="sel">Modalidade
    <select id="modalidade"></select></label>
</div>

<div id="avisoDados" class="sub" style="display:none;color:#E67E22"></div>

<h2>Perfil metabólico — valores estáveis nos últimos 60s</h2>
<div class="sub" id="subPerfil">A carregar...</div>
<div class="sub" style="font-size:11px;color:#8b949e">
  Análise robusta: Remove artefatos de sensor, usa moving averages 5-10s,
  calcula MAX HR, AVG respiração, MIN SMO2, DFA-α1 normalizado dos últimos 60s.
  Apenas intervalos válidos (≥60s, watts estável).</div>
<div class="legend" id="lgPerfil"></div>
<div class="chartbox">
  <canvas id="chPerfil" height="240"></canvas>
</div>

<h2>Evolução ao longo do tempo</h2>
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

function drawPerfil(){
 const canvasId='chPerfil';
 if(!PERFIL||PERFIL.status!=='ok'){
  const o0=ctx(canvasId,240); if(o0) noData(o0.g,o0.W,o0.H,(PERFIL&&PERFIL.mensagem)||'Sem dados');
  return;
 }
 const faixas=PERFIL.faixas;
 const disponiveis=Object.keys(CORES_METAB).filter(c=>faixas.some(f=>f[c]));

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
 if(!faixas.length){noData(g,W,H,'Sem faixas com dados');return;}
 if(!vis.length){noData(g,W,H,'Nenhuma métrica seleccionada');return;}

 const PL=62,PR=120,PB=30,PT=20,w=W-PL-PR,h=H-PT-PB;
 const xs=faixas.map(f=>f.watts_centro);
 const xmin=Math.min.apply(null,xs), xmax=Math.max.apply(null,xs);
 const X=v=> xmax>xmin ? PL+w*(v-xmin)/(xmax-xmin) : PL+w/2;

 function hexRgba(hex,a){const h=hex.replace('#','');
  return 'rgba('+parseInt(h.substring(0,2),16)+','+parseInt(h.substring(2,4),16)+','+
   parseInt(h.substring(4,6),16)+','+a+')';}

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

 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let k=0;k<=2;k++){const y=PT+h*k/2;
  g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}

 vis.forEach(function(c){
  const esc=escalas[c];
  const pts=esc.pts;
  
  g.fillStyle=hexRgba(CORES_METAB[c],0.08);
  g.beginPath();
  pts.forEach(function(f,j){const y=esc.Y(f[c].p75);
   if(j===0)g.moveTo(X(f.watts_centro),y); else g.lineTo(X(f.watts_centro),y);});
  for(let j=pts.length-1;j>=0;j--)g.lineTo(X(pts[j].watts_centro),esc.Y(pts[j][c].p25));
  g.closePath();g.fill();

  g.strokeStyle=CORES_METAB[c];g.lineWidth=2.2;g.beginPath();
  pts.forEach(function(f,j){const y=esc.Y(f[c].p50);
   if(j===0)g.moveTo(X(f.watts_centro),y); else g.lineTo(X(f.watts_centro),y);});
  g.stroke();
  
  g.fillStyle=CORES_METAB[c];
  pts.forEach(function(f){g.beginPath();g.arc(X(f.watts_centro),esc.Y(f[c].p50),2.5,0,7);g.fill();});
 });

 g.fillStyle='#8b949e';g.font='9px sans-serif';g.textAlign='right';
 vis.forEach(function(c){
  const esc=escalas[c];
  g.strokeStyle=CORES_METAB[c];g.lineWidth=1.5;g.beginPath();
  g.moveTo(PL+w,PT);g.lineTo(PL+w,PT+h);g.stroke();
  for(let k=0;k<=2;k++){
   const val=(esc.b-(esc.b-esc.a)*k/2).toFixed(1);
   const y=PT+h*k/2;
   g.fillText(val,PL+w+8,y+3);
   g.fillStyle=hexRgba(CORES_METAB[c],0.3);g.fillText(LABELS_METAB[c],PL+w+65,y-6);
   g.fillStyle='#8b949e';
  }
 });

 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='center';
 const step=Math.ceil(faixas.length/12);
 faixas.forEach(function(f,i){if(i%step!==0)return;
  g.fillText(Math.round(f.watts_centro)+'W',X(f.watts_centro),H-10);});
 g.font='bold 9px sans-serif';
 g.fillText('WATTS',PL+w/2,H-1);

 if(faixas.some(f=>f.pace_medio)){
  g.fillStyle='#FF6B6B';g.font='bold 10px sans-serif';g.textAlign='center';
  g.fillText('PACE (min:ss)',PL+w/2,8);
  faixas.forEach(function(f,i){if(i%step!==0)return;
   if(f.pace_medio) g.fillText(f.pace_medio,X(f.watts_centro),12);
  });
 }
}

function drawEvolucao(){
 const canvasId='chEvolucao';
 if(!EVOLUCAO||EVOLUCAO.status!=='ok') return;
}

async function carregarPerfil(){
 const modalidade=document.getElementById('modalidade').value;
 try{ const d=await fetch('/api/fisiologia/perfil_robusto/'+modalidade).then(r=>r.json());
  PERFIL=d;
  if(PERFIL.status==='ok') drawPerfil();
 }catch(e){ PERFIL={status:'erro'}; }
}

async function carregarEvolucao(){
 const modalidade=document.getElementById('modalidade').value;
 const campo=document.getElementById('campoEvolucao').value;
 const wmin=document.getElementById('wattsMin').value;
 const wmax=document.getElementById('wattsMax').value;
 try{ const d=await fetch('/api/fisiologia/evolucao_robusta?modalidade='+modalidade+'&campo='+campo+'&watts_min='+wmin+'&watts_max='+wmax).then(r=>r.json());
  EVOLUCAO=d;
 }catch(e){ EVOLUCAO={status:'erro'}; }
}

async function load(){
 try{ const d=await fetch('/api/metabol').then(r=>r.json());
  MODALIDADES=d.modalidades||[];
  if(!MODALIDADES.length) return;
  
  const selMod=document.getElementById('modalidade');
  selMod.innerHTML=MODALIDADES.map(m=>'<option value="'+m.modalidade+'">'+m.modalidade+' ('+m.n+')</option>').join('');
  selMod.onchange=function(){ carregarPerfil(); carregarEvolucao(); };

  const selCampo=document.getElementById('campoEvolucao');
  const campos=Object.keys(LABELS_METAB);
  selCampo.innerHTML=campos.map(c=>'<option value="'+c+'">'+LABELS_METAB[c]+'</option>').join('');
  selCampo.onchange=carregarEvolucao;

  carregarPerfil();
  carregarEvolucao();
 }catch(e){}
}

load();
"""

def api_data():
    try:
        modalidades = modalidades_disponiveis()
    except Exception as e:
        return jsonify({'status': 'erro', 'modalidades': []})
    return jsonify({'status': 'ok', 'modalidades': modalidades,
                    'campos_valor': CAMPOS_VALOR})

def render():
    from flask import render_template_string
    return render_template_string(page(SLUG, 'Metabolismo (v2 - Robusto)', BODY, JS))
