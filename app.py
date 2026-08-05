#!/usr/bin/env python3
"""Intervals.icu Dashboard + Detalhe de Atividade + Debug"""

import os
import sys
import logging
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template_string
from dotenv import load_dotenv
import requests

load_dotenv()

API_KEY = os.getenv("INTERVALS_ICU_API_KEY", "").strip()
ATHLETE_ID = os.getenv("ATHLETE_ID", "0").strip()

if not API_KEY:
    print("ERRO: INTERVALS_ICU_API_KEY nao configurada")
    sys.exit(1)

print(f"Config carregada | ATHLETE_ID: {ATHLETE_ID}")

try:
    from helpers import ActivityProcessor
    print("ActivityProcessor importado")
except Exception as e:
    print(f"Erro import helpers: {e}")
    sys.exit(1)

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
logging.getLogger("werkzeug").setLevel(logging.WARNING)

BASE = "https://intervals.icu/api/v1"
AUTH = ("API_KEY", API_KEY)

_cache = {'activities': None, 'time': None}


def icu_get(path, params=None):
    """GET generico na API Intervals.icu. Devolve (data, erro)."""
    try:
        r = requests.get(f"{BASE}{path}", auth=AUTH, params=params or {}, timeout=25)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:200]}"
        return r.json(), None
    except Exception as e:
        return None, str(e)


def fetch_activities():
    now = datetime.now()
    if _cache['activities'] and _cache['time']:
        if (now - _cache['time']).total_seconds() < 300:
            return _cache['activities']

    oldest = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    data, err = icu_get(f"/athlete/{ATHLETE_ID}/activities", {"oldest": oldest})
    if err:
        print(f"Fetch error: {err}")
        return None
    acts = data if isinstance(data, list) else data.get("data", [])
    _cache['activities'] = acts
    _cache['time'] = now
    return acts


def process_all(acts):
    p = ActivityProcessor()
    out = []
    for a in acts:
        try:
            out.append({
                'id': p.get_activity_id(a),
                'date': p.get_start_date_local(a)[:10],
                'name': p.get_activity_name(a),
                'type': p.get_activity_type(a),
                'duration_min': round(p.get_duration_minutes(a), 1),
                'distance_km': round(p.get_distance_km(a), 1),
                'ftp': p.get_ftp(a),
                'avg_watts': p.get_avg_watts(a),
                'joules': p.get_joules(a),
                'training_load': p.get_training_load(a),
                'avg_hr': p.get_avg_hr(a),
                'max_hr': p.get_max_hr(a),
                'source': p.get_source(a),
            })
        except Exception:
            continue
    return out


def downsample(arr, target=1200):
    """Reduz serie para ~target pontos (media por bucket)."""
    if not arr:
        return []
    n = len(arr)
    if n <= target:
        return arr
    step = n / target
    out = []
    for i in range(target):
        lo = int(i * step)
        hi = max(lo + 1, int((i + 1) * step))
        chunk = [v for v in arr[lo:hi] if isinstance(v, (int, float))]
        out.append(round(sum(chunk) / len(chunk), 1) if chunk else None)
    return out


# ==================== HTML: LISTA ====================

CSS = r"""
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:#0e1117; color:#e6e6e6;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
h1 { font-size:22px; margin:0 0 4px; font-weight:600; }
h2 { font-size:15px; margin:28px 0 10px; font-weight:600; color:#c9d1d9; }
.sub { color:#8b949e; font-size:13px; margin-bottom:24px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:24px; }
.card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px 16px; }
.card .label { font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:#8b949e; }
.card .value { font-size:24px; font-weight:600; color:#5DADE2; margin-top:4px; }
.controls { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; align-items:center; }
input, select { background:#161b22; border:1px solid #30363d; color:#e6e6e6;
  padding:8px 10px; border-radius:6px; font-size:13px; }
input { min-width:220px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; padding:10px 8px; background:#161b22; border-bottom:1px solid #30363d;
  color:#8b949e; font-weight:500; font-size:11px; text-transform:uppercase;
  cursor:pointer; user-select:none; position:sticky; top:0; }
th:hover { color:#5DADE2; }
td { padding:9px 8px; border-bottom:1px solid #21262d; }
tbody tr { cursor:pointer; }
tbody tr:hover td { background:#1c2331; }
.num { text-align:right; font-variant-numeric:tabular-nums; }
.wrap { max-height:70vh; overflow:auto; border:1px solid #30363d; border-radius:8px; }
.loading { color:#8b949e; padding:40px; text-align:center; }
.count { color:#8b949e; font-size:12px; margin-bottom:8px; }
a { color:#5DADE2; text-decoration:none; font-size:13px; }
a:hover { text-decoration:underline; }
.chartbox { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px; margin-bottom:16px; }
canvas { width:100%; display:block; }
.legend { display:flex; gap:16px; font-size:12px; color:#8b949e; margin-bottom:8px; flex-wrap:wrap; }
.legend span i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; }
.kv { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:6px 20px; font-size:13px; }
.kv div { padding:5px 0; border-bottom:1px solid #21262d; display:flex; justify-content:space-between; gap:10px; }
.kv .k { color:#8b949e; }
.kv .v { font-variant-numeric:tabular-nums; }
pre { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px;
  overflow:auto; max-height:600px; font-size:12px; line-height:1.5; color:#c9d1d9; }
.pill { display:inline-block; background:#1c2331; border:1px solid #30363d; border-radius:12px;
  padding:3px 10px; font-size:11px; margin:2px 4px 2px 0; color:#5DADE2; }
.toggles { display:flex; gap:14px; flex-wrap:wrap; font-size:12px; margin-bottom:10px; }
.toggles label { cursor:pointer; color:#8b949e; }
"""

LIST_HTML = r"""
<!DOCTYPE html><html lang="pt"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Intervals.icu Dashboard</title><style>__CSS__</style></head><body>
<h1>Intervals.icu Dashboard</h1>
<div class="sub">Athlete Susigan &middot; ultimos 365 dias &middot; clica numa linha para ver detalhe</div>
<div class="cards" id="cards"></div>
<div class="controls">
  <input id="search" placeholder="Procurar nome...">
  <select id="typeFilter"><option value="">Todos os tipos</option></select>
  <a href="/api/activities" target="_blank">JSON bruto</a>
</div>
<div class="count" id="count"></div>
<div class="wrap"><table>
  <thead><tr id="head"></tr></thead>
  <tbody id="body"><tr><td class="loading">A carregar...</td></tr></tbody>
</table></div>
<script>
const COLS = [['date','Data',0],['name','Nome',0],['type','Tipo',0],
  ['duration_min','Min',1],['distance_km','km',1],['training_load','TL',1],
  ['avg_watts','W',1],['ftp','FTP',1],['avg_hr','HR',1],['max_hr','HR max',1],
  ['joules','Joules',1],['source','Fonte',0]];
let data = [], sortKey = 'date', sortAsc = false;
function fmt(v,num){ if(v===null||v===undefined||v==='')return '-';
  if(num&&typeof v==='number')return v.toLocaleString('pt-PT'); return v; }
function render(){
  const q=document.getElementById('search').value.toLowerCase();
  const t=document.getElementById('typeFilter').value;
  let rows=data.filter(function(r){return (!q||(r.name||'').toLowerCase().indexOf(q)!==-1)&&(!t||r.type===t);});
  rows.sort(function(a,b){var x=a[sortKey],y=b[sortKey];
    if(typeof x==='number')return sortAsc?x-y:y-x;
    x=String(x||'');y=String(y||'');return sortAsc?x.localeCompare(y):y.localeCompare(x);});
  document.getElementById('count').textContent=rows.length+' de '+data.length+' atividades';
  document.getElementById('body').innerHTML=rows.map(function(r){
    return '<tr onclick="location.href=\'/activity/'+r.id+'\'">'+COLS.map(function(c){
      return '<td class="'+(c[2]?'num':'')+'">'+fmt(r[c[0]],c[2])+'</td>';}).join('')+'</tr>';}).join('');
}
function buildHead(){
  document.getElementById('head').innerHTML=COLS.map(function(c){
    return '<th class="'+(c[2]?'num':'')+'" data-k="'+c[0]+'">'+c[1]+'</th>';}).join('');
  document.querySelectorAll('th').forEach(function(th){th.onclick=function(){
    var k=th.dataset.k; if(sortKey===k)sortAsc=!sortAsc; else {sortKey=k;sortAsc=false;} render();};});
}
async function load(){
  buildHead();
  const res=await Promise.all([fetch('/api/stats').then(r=>r.json()),fetch('/api/activities').then(r=>r.json())]);
  const s=res[0],a=res[1];
  document.getElementById('cards').innerHTML=[['Atividades',s.total_activities],
    ['TL total',s.training_total_tl],['TL medio',s.training_avg_tl],
    ['Distancia',Math.round(s.distance_total_km)+' km'],['Duracao media',s.duration_avg_min+' min'],
    ['Watts medio',s.training_avg_watts+' W'],['HR medio',s.hr_avg+' bpm'],
    ['Com potencia',s.coverage_with_power]].map(function(c){var v=c[1];
    return '<div class="card"><div class="label">'+c[0]+'</div><div class="value">'+
      (typeof v==='number'?v.toLocaleString('pt-PT'):v)+'</div></div>';}).join('');
  data=a.activities||[];
  var types=Array.from(new Set(data.map(r=>r.type))).sort();
  document.getElementById('typeFilter').innerHTML='<option value="">Todos os tipos</option>'+
    types.map(t=>'<option>'+t+'</option>').join('');
  render();
}
document.getElementById('search').oninput=render;
document.getElementById('typeFilter').onchange=render;
load();
</script></body></html>
"""

DETAIL_HTML = r"""
<!DOCTYPE html><html lang="pt"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Atividade __AID__</title><style>__CSS__</style></head><body>
<a href="/">&larr; Voltar a lista</a>
<h1 id="title">A carregar...</h1>
<div class="sub" id="subtitle"></div>
<div class="cards" id="cards"></div>

<h2>Series temporais</h2>
<div class="toggles" id="toggles"></div>
<div class="chartbox">
  <div class="legend" id="legend"></div>
  <canvas id="chart" height="340"></canvas>
</div>

<h2>Power vs HR</h2>
<div class="chartbox">
  <div class="legend" id="pvhLegend"></div>
  <canvas id="pvh" height="260"></canvas>
</div>

<h2>Streams disponiveis</h2>
<div id="streamPills"></div>

<h2>Intervalos</h2>
<div class="wrap" style="max-height:340px"><table>
  <thead><tr id="ivHead"></tr></thead><tbody id="ivBody"></tbody></table></div>

<h2>Todos os campos (raw)</h2>
<div class="kv" id="rawkv"></div>

<h2>Debug JSON completo</h2>
<div class="sub"><a href="/api/activity/__AID__/debug" target="_blank">Abrir /api/activity/__AID__/debug</a></div>
<pre id="debug">A carregar...</pre>

<script>
const AID = "__AID__";
const COLORS = { watts:'#5DADE2', heartrate:'#E74C3C', cadence:'#F4D03F',
  altitude:'#58D68D', velocity_smooth:'#AF7AC5', temp:'#E67E22',
  smo2:'#48C9B0', thb:'#EC7063', torque:'#85929E', respiration:'#7FB3D5' };
let STREAMS = {}, ACTIVE = {};

function color(k){ return COLORS[k] || '#8b949e'; }

function drawChart(){
  const c = document.getElementById('chart');
  const dpr = window.devicePixelRatio || 1;
  const W = c.clientWidth, H = 340;
  c.width = W*dpr; c.height = H*dpr;
  const g = c.getContext('2d'); g.scale(dpr,dpr);
  g.clearRect(0,0,W,H);
  const PL=44, PR=44, PT=10, PB=26;
  const w=W-PL-PR, h=H-PT-PB;

  const keys = Object.keys(ACTIVE).filter(k=>ACTIVE[k] && STREAMS[k] && STREAMS[k].length);
  if(!keys.length){ g.fillStyle='#8b949e'; g.font='13px sans-serif';
    g.fillText('Sem series selecionadas',PL,PT+20); return; }

  const n = Math.max.apply(null, keys.map(k=>STREAMS[k].length));
  // grelha
  g.strokeStyle='#21262d'; g.lineWidth=1;
  for(let i=0;i<=4;i++){ const y=PT+h*i/4; g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke(); }

  keys.forEach(function(k,idx){
    const s = STREAMS[k];
    const vals = s.filter(v=>typeof v==='number');
    if(!vals.length) return;
    let mn=Math.min.apply(null,vals), mx=Math.max.apply(null,vals);
    if(mx===mn) mx=mn+1;
    g.strokeStyle=color(k); g.lineWidth=1.3; g.beginPath();
    let started=false;
    for(let i=0;i<s.length;i++){
      const v=s[i]; if(typeof v!=='number'){ started=false; continue; }
      const x=PL+w*i/(n-1), y=PT+h-(v-mn)/(mx-mn)*h;
      if(!started){ g.moveTo(x,y); started=true; } else g.lineTo(x,y);
    }
    g.stroke();
    // eixo do lado (primeiras 2 series)
    if(idx<2){
      const right = idx===1;
      g.fillStyle=color(k); g.font='10px sans-serif';
      g.textAlign = right ? 'left' : 'right';
      for(let i=0;i<=4;i++){
        const val = mx-(mx-mn)*i/4, y=PT+h*i/4;
        g.fillText(Math.round(val), right?PL+w+6:PL-6, y+3);
      }
      g.textAlign='left';
    }
  });
  // eixo tempo
  g.fillStyle='#8b949e'; g.font='10px sans-serif'; g.textAlign='center';
  for(let i=0;i<=6;i++){
    const frac=i/6, x=PL+w*frac;
    const secs = Math.round(frac*(window.__ELAPSED__||0));
    g.fillText(Math.floor(secs/60)+'m', x, H-8);
  }
  g.textAlign='left';
}

function drawPvH(series){
  const c=document.getElementById('pvh');
  const dpr=window.devicePixelRatio||1;
  const W=c.clientWidth,H=260;
  c.width=W*dpr;c.height=H*dpr;
  const g=c.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,W,H);
  if(!series||!series.length){ g.fillStyle='#8b949e';g.font='13px sans-serif';
    g.fillText('Sem dados de power vs HR',20,30); return; }
  const PL=44,PR=44,PT=10,PB=26,w=W-PL-PR,h=H-PT-PB;
  const P=series.map(b=>b.power!=null?b.power:b.watts),
        Hr=series.map(b=>b.hr!=null?b.hr:b.heartrate);
  const pv=P.filter(v=>typeof v==='number'), hv=Hr.filter(v=>typeof v==='number');
  if(!pv.length||!hv.length){ g.fillStyle='#8b949e';g.font='13px sans-serif';
    g.fillText('Sem dados de power vs HR',20,30); return; }
  const pmn=Math.min.apply(null,pv),pmx=Math.max.apply(null,pv);
  const hmn=Math.min.apply(null,hv),hmx=Math.max.apply(null,hv);
  g.strokeStyle='#21262d';
  for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
  function line(arr,mn,mx,col){
    g.strokeStyle=col;g.lineWidth=1.6;g.beginPath();let st=false;
    for(let i=0;i<arr.length;i++){const v=arr[i];if(typeof v!=='number'){st=false;continue;}
      const x=PL+w*i/(arr.length-1),y=PT+h-(v-mn)/((mx-mn)||1)*h;
      if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);}
    g.stroke();
  }
  line(P,pmn,pmx,'#5DADE2'); line(Hr,hmn,hmx,'#E74C3C');
  g.font='10px sans-serif';g.textAlign='right';g.fillStyle='#5DADE2';
  for(let i=0;i<=4;i++){g.fillText(Math.round(pmx-(pmx-pmn)*i/4),PL-6,PT+h*i/4+3);}
  g.textAlign='left';g.fillStyle='#E74C3C';
  for(let i=0;i<=4;i++){g.fillText(Math.round(hmx-(hmx-hmn)*i/4),PL+w+6,PT+h*i/4+3);}
}

function fmtv(v){
  if(v===null||v===undefined)return '<span style="color:#484f58">null</span>';
  if(typeof v==='object')return '<span style="color:#8b949e">'+JSON.stringify(v).slice(0,60)+'</span>';
  if(typeof v==='number')return v.toLocaleString('pt-PT');
  return String(v);
}

async function load(){
  const d = await fetch('/api/activity/'+AID+'/full').then(r=>r.json());
  if(d.error){ document.getElementById('title').textContent='Erro: '+d.error; return; }
  const a = d.activity || {};
  window.__ELAPSED__ = a.elapsed_time || a.moving_time || 0;

  document.getElementById('title').textContent = a.name || AID;
  document.getElementById('subtitle').textContent =
    (a.start_date_local||'') + '  ·  ' + (a.type||'') + '  ·  ' + (a.source||'');

  const cards=[['Duracao',Math.round((a.elapsed_time||0)/60)+' min'],
    ['Distancia',((a.icu_distance||a.distance||0)/1000).toFixed(1)+' km'],
    ['TL',a.icu_training_load],['NP',(a.icu_weighted_avg_watts||0)+' W'],
    ['FTP',(a.icu_pm_ftp||a.icu_ftp||0)+' W'],['IF',(a.icu_intensity||0).toFixed(2)],
    ['HR med',(a.average_heartrate||0)+' bpm'],['HR max',(a.max_heartrate||0)+' bpm'],
    ['kJ',Math.round((a.icu_joules||0)/1000)],['W prime',a.icu_pm_w_prime||0],
    ['Decoupling',(a.decoupling!=null?a.decoupling.toFixed(1):'-')+'%'],
    ['PI',(a.polarization_index!=null?a.polarization_index.toFixed(2):'-')]];
  document.getElementById('cards').innerHTML=cards.map(function(c){
    return '<div class="card"><div class="label">'+c[0]+'</div><div class="value">'+
      (c[1]==null?'-':c[1])+'</div></div>';}).join('');

  // streams
  STREAMS = d.streams || {};
  const names = Object.keys(STREAMS);
  document.getElementById('streamPills').innerHTML =
    (d.stream_types_available||names).map(t=>'<span class="pill">'+t+'</span>').join('') ||
    '<span class="sub">nenhum</span>';

  const prefer=['watts','heartrate','cadence','altitude','velocity_smooth'];
  names.forEach(function(k){ ACTIVE[k] = (k==='watts'||k==='heartrate'); });
  document.getElementById('toggles').innerHTML = names.map(function(k){
    return '<label><input type="checkbox" data-k="'+k+'" '+(ACTIVE[k]?'checked':'')+'> '+k+'</label>';
  }).join('');
  document.querySelectorAll('#toggles input').forEach(function(cb){
    cb.onchange=function(){ ACTIVE[cb.dataset.k]=cb.checked; updLegend(); drawChart(); };
  });
  function updLegend(){
    document.getElementById('legend').innerHTML = names.filter(k=>ACTIVE[k]).map(function(k){
      return '<span><i style="background:'+color(k)+'"></i>'+k+'</span>';}).join('');
  }
  updLegend(); drawChart();

  // power vs hr
  const pvh = d.power_vs_hr || {};
  document.getElementById('pvhLegend').innerHTML =
    '<span><i style="background:#5DADE2"></i>Power</span>'+
    '<span><i style="background:#E74C3C"></i>HR</span>'+
    (pvh.decoupling!=null?'<span>Decoupling: '+pvh.decoupling.toFixed(2)+'%</span>':'')+
    (pvh.powerHr!=null?'<span>Power/HR: '+pvh.powerHr.toFixed(2)+'</span>':'')+
    (pvh.hrLag!=null?'<span>HR lag: '+pvh.hrLag+'s</span>':'');
  drawPvH(pvh.series);

  // intervalos
  const ivs = (d.intervals && (d.intervals.icu_intervals||d.intervals)) || [];
  if(Array.isArray(ivs) && ivs.length){
    const cols=['label','type','start_time','elapsed_time','distance',
      'average_watts','max_watts','average_heartrate','max_heartrate','average_cadence'];
    document.getElementById('ivHead').innerHTML=cols.map(c=>'<th>'+c+'</th>').join('');
    document.getElementById('ivBody').innerHTML=ivs.map(function(iv){
      return '<tr>'+cols.map(function(c){
        var v=iv[c]; return '<td class="num">'+(v==null?'-':(typeof v==='number'?Math.round(v*10)/10:v))+'</td>';
      }).join('')+'</tr>';}).join('');
  } else {
    document.getElementById('ivBody').innerHTML='<tr><td class="loading">Sem intervalos</td></tr>';
  }

  // raw fields
  document.getElementById('rawkv').innerHTML = Object.keys(a).sort().map(function(k){
    return '<div><span class="k">'+k+'</span><span class="v">'+fmtv(a[k])+'</span></div>';
  }).join('');

  document.getElementById('debug').textContent = JSON.stringify(d.meta||{}, null, 2);
}
window.addEventListener('resize', function(){ drawChart(); });
load();
</script></body></html>
"""


# ==================== ROTAS HTML ====================

@app.route('/', methods=['GET'])
def index():
    return render_template_string(LIST_HTML.replace('__CSS__', CSS))


@app.route('/activity/<activity_id>', methods=['GET'])
def activity_page(activity_id):
    html = DETAIL_HTML.replace('__CSS__', CSS).replace('__AID__', activity_id)
    return render_template_string(html)


# ==================== API ====================

@app.route('/health')
def health():
    return jsonify({'status': 'healthy'}), 200


@app.route('/api/activities')
def api_activities():
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'Fetch failed'}), 500
    processed = process_all(acts)
    limit = request.args.get('limit', type=int)
    offset = request.args.get('offset', default=0, type=int)
    atype = request.args.get('type', type=str)
    if atype:
        processed = [a for a in processed if a['type'] == atype]
    if offset:
        processed = processed[offset:]
    if limit:
        processed = processed[:limit]
    return jsonify({'status': 'OK', 'total': len(acts),
                    'returned': len(processed), 'activities': processed})


@app.route('/api/stats')
def api_stats():
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'Fetch failed'}), 500
    p = ActivityProcessor()
    durations = [p.get_duration_seconds(a) for a in acts]
    distances = [p.get_distance_km(a) for a in acts]
    tls = [p.get_training_load(a) for a in acts]
    hrs = [p.get_avg_hr(a) for a in acts if p.has_hr_data(a)]
    watts = [p.get_avg_watts(a) for a in acts]
    joules = [p.get_joules(a) for a in acts]
    return jsonify({
        'status': 'OK',
        'total_activities': len(acts),
        'duration_avg_min': round(sum(durations)/len(durations)/60, 1) if durations else 0,
        'distance_total_km': round(sum(distances), 1),
        'distance_avg_km': round(sum(distances)/len(distances), 1) if distances else 0,
        'training_total_tl': sum(tls),
        'training_avg_tl': round(sum(tls)/len(tls), 1) if tls else 0,
        'training_avg_watts': round(sum(watts)/len(watts)) if watts else 0,
        'training_total_joules': sum(joules),
        'hr_avg': round(sum(hrs)/len(hrs)) if hrs else 0,
        'coverage_with_hr': len(hrs),
        'coverage_with_power': sum(1 for a in acts if p.get_ftp(a) > 0),
    })


@app.route('/api/activity/<activity_id>/full')
def api_activity_full(activity_id):
    """Atividade + streams (downsampled) + power-vs-hr + intervalos."""
    act, err = icu_get(f"/activity/{activity_id}", {"intervals": "false"})
    if err:
        return jsonify({'error': err}), 502

    meta = {'endpoints': {}}

    # streams
    streams = {}
    stream_types = []
    sdata, serr = icu_get(f"/activity/{activity_id}/streams", {"includeDefaults": "true"})
    meta['endpoints']['streams'] = serr or f"{len(sdata) if sdata else 0} streams"
    if sdata and isinstance(sdata, list):
        for s in sdata:
            t = s.get('type') or s.get('name')
            if not t:
                continue
            stream_types.append(t)
            d = s.get('data')
            if isinstance(d, list) and d and not s.get('valueTypeIsArray'):
                if any(isinstance(v, (int, float)) for v in d):
                    streams[t] = downsample(d)

    # power vs hr
    pvh, perr = icu_get(f"/activity/{activity_id}/power-vs-hr")
    meta['endpoints']['power_vs_hr'] = perr or 'ok'

    # intervalos
    ivs, ierr = icu_get(f"/activity/{activity_id}/intervals")
    meta['endpoints']['intervals'] = ierr or 'ok'

    meta['stream_types_available'] = stream_types
    meta['activity_field_count'] = len(act) if isinstance(act, dict) else 0
    meta['null_fields'] = sorted([k for k, v in act.items() if v is None]) if isinstance(act, dict) else []

    return jsonify({
        'status': 'OK',
        'activity': act,
        'streams': streams,
        'stream_types_available': stream_types,
        'power_vs_hr': pvh or {},
        'intervals': ivs or {},
        'meta': meta,
    })


@app.route('/api/activity/<activity_id>/debug')
def api_activity_debug(activity_id):
    """Despeja TUDO o que a API oferece para esta atividade."""
    out = {'activity_id': activity_id, 'endpoints': {}}

    probes = [
        ('activity',        f"/activity/{activity_id}", {"intervals": "true"}),
        ('streams',         f"/activity/{activity_id}/streams", {"includeDefaults": "true"}),
        ('intervals',       f"/activity/{activity_id}/intervals", None),
        ('power_vs_hr',     f"/activity/{activity_id}/power-vs-hr", None),
        ('power_curve',     f"/activity/{activity_id}/power-curve", None),
        ('hr_curve',        f"/activity/{activity_id}/hr-curve", None),
        ('pace_curve',      f"/activity/{activity_id}/pace-curve", None),
        ('power_histogram', f"/activity/{activity_id}/power-histogram", None),
        ('hr_histogram',    f"/activity/{activity_id}/hr-histogram", None),
        ('time_at_hr',      f"/activity/{activity_id}/time-at-hr", None),
        ('best_efforts',    f"/activity/{activity_id}/best-efforts", None),
        ('interval_stats',  f"/activity/{activity_id}/interval-stats", None),
        ('weather_summary', f"/activity/{activity_id}/weather-summary", None),
        ('segments',        f"/activity/{activity_id}/segments", None),
        ('hr_load_model',   f"/activity/{activity_id}/hr-load-model", None),
        ('custom_items',    f"/athlete/{ATHLETE_ID}/custom-item", None),
    ]

    for name, path, params in probes:
        data, err = icu_get(path, params)
        if err:
            out['endpoints'][name] = {'ok': False, 'error': err}
            continue
        info = {'ok': True, 'type': type(data).__name__}
        if isinstance(data, list):
            info['count'] = len(data)
            if name == 'streams':
                info['streams'] = [{
                    'type': s.get('type'),
                    'name': s.get('name'),
                    'custom': s.get('custom'),
                    'allNull': s.get('allNull'),
                    'valueTypeIsArray': s.get('valueTypeIsArray'),
                    'points': len(s.get('data') or []) if isinstance(s.get('data'), list) else None,
                    'sample': (s.get('data') or [])[:5] if isinstance(s.get('data'), list) else None,
                } for s in data]
            else:
                info['sample'] = data[:2]
        elif isinstance(data, dict):
            info['keys'] = sorted(data.keys())
            info['null_keys'] = sorted([k for k, v in data.items() if v is None])
            if name == 'activity':
                info['values'] = {k: v for k, v in data.items()
                                  if not isinstance(v, (list, dict))}
            else:
                info['sample'] = {k: (str(v)[:120] if isinstance(v, (list, dict)) else v)
                                  for k, v in list(data.items())[:40]}
        out['endpoints'][name] = info

    return jsonify(out)


@app.route('/api/debug/athlete')
def api_debug_athlete():
    """Perfil, custom fields, sport settings, wellness."""
    out = {}
    for name, path, params in [
        ('athlete',        f"/athlete/{ATHLETE_ID}", None),
        ('custom_item',    f"/athlete/{ATHLETE_ID}/custom-item", None),
        ('activity_tags',  f"/athlete/{ATHLETE_ID}/activity-tags", None),
        ('sport_settings', f"/athlete/{ATHLETE_ID}/sport-settings", None),
        ('training_plan',  f"/athlete/{ATHLETE_ID}/training-plan", None),
    ]:
        data, err = icu_get(path, params)
        if err:
            out[name] = {'ok': False, 'error': err}
        elif isinstance(data, dict):
            out[name] = {'ok': True, 'keys': sorted(data.keys()),
                         'values': {k: v for k, v in data.items()
                                    if not isinstance(v, (list, dict))}}
        else:
            out[name] = {'ok': True, 'count': len(data), 'sample': data[:5]}
    return jsonify(out)


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
