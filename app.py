#!/usr/bin/env python3
"""Intervals.icu Dashboard v3 - detalhe completo com custom fields e streams"""

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

# Campos do OpenAPI spec. Tudo o que nao esta aqui = custom field do atleta.
STD_FIELDS = {
 'analysis_issues','analyzed','athlete_max_hr','attachments','average_altitude','average_cadence',
 'average_clouds','average_feels_like','average_heartrate','average_impact_loading_rate','average_speed',
 'average_stance_time','average_stance_time_balance','average_stance_time_percent','average_step_length',
 'average_stride','average_temp','average_vertical_oscillation','average_vertical_ratio',
 'average_vertical_speed','average_weather_temp','average_wind_gust','average_wind_speed',
 'average_leg_spring_stiffness','avg_lr_balance','calories','carbs_ingested','carbs_used','coach_tick',
 'coasting_time','commute','compliance','crank_length','created','custom_zones','decoupling','description',
 'device_name','device_watts','distance','elapsed_time','external_id','feel','file_sport_index','file_type',
 'gap','gap_model','gap_zone_times','gear','group','has_heartrate','has_segments','has_weather',
 'headwind_percent','hr_load','hr_load_type','icu_achievements','icu_athlete_id','icu_atl',
 'icu_average_watts','icu_cadence_z2','icu_chat_id','icu_color','icu_cooldown_time','icu_ctl',
 'icu_distance','icu_efficiency_factor','icu_ftp','icu_hr_zone_times','icu_hr_zones','icu_hrr',
 'icu_ignore_hr','icu_ignore_power','icu_ignore_time','icu_intensity','icu_intervals','icu_groups',
 'icu_intervals_edited','icu_joules','icu_joules_above_ftp','icu_lap_count','icu_max_wbal_depletion',
 'icu_median_time_delta','icu_pm_cp','icu_pm_ftp','icu_pm_ftp_secs','icu_pm_ftp_watts','icu_pm_p_max',
 'icu_pm_w_prime','icu_power_hr','icu_power_hr_z2','icu_power_hr_z2_mins','icu_power_spike_threshold',
 'icu_power_zones','icu_recording_time','icu_resting_hr','icu_rolling_cp','icu_rolling_ftp',
 'icu_rolling_ftp_delta','icu_rolling_p_max','icu_rolling_w_prime','icu_rpe','icu_sweet_spot_max',
 'icu_sweet_spot_min','icu_sync_date','icu_sync_error','icu_training_load','icu_training_load_data',
 'icu_variability_index','icu_w_prime','icu_warmup_time','icu_weight','icu_weighted_avg_watts',
 'icu_zone_times','id','ignore_pace','ignore_parts','ignore_velocity','interval_summary','kg_lifted',
 'lengths','lock_intervals','lthr','max_altitude','max_feels_like','max_heartrate','max_rain','max_snow',
 'max_speed','max_temp','max_weather_temp','min_altitude','min_feels_like','min_temp','min_weather_temp',
 'moving_time','name','oauth_client_id','oauth_client_name','p30s_exponent','p_max','pace','pace_load',
 'pace_load_type','pace_zone_times','pace_zones','paired_event_id','perceived_exertion',
 'polarization_index','pool_length','power_field','power_field_names','power_load','power_meter',
 'power_meter_battery','power_meter_serial','prevailing_wind_deg','race','recording_stops','route_id',
 'session_rpe','skyline_chart_bytes','source','ss_cp','ss_p_max','ss_w_prime','start_date',
 'start_date_local','strain_score','strava_id','stream_types','sub_type','tags','tailwind_percent',
 'threshold_pace','timezone','tiz_order','total_elevation_gain','total_elevation_loss','trainer','trimp',
 'type','use_elevation_correction','use_gap_zone_times','workout_shift_secs',
}

_cache = {'activities': None, 'time': None}


def icu_get(path, params=None):
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
                'id': p.get_activity_id(a), 'date': p.get_start_date_local(a)[:10],
                'name': p.get_activity_name(a), 'type': p.get_activity_type(a),
                'duration_min': round(p.get_duration_minutes(a), 1),
                'distance_km': round(p.get_distance_km(a), 1),
                'ftp': p.get_ftp(a), 'avg_watts': p.get_avg_watts(a),
                'joules': p.get_joules(a), 'training_load': p.get_training_load(a),
                'avg_hr': p.get_avg_hr(a), 'max_hr': p.get_max_hr(a),
                'source': p.get_source(a),
            })
        except Exception:
            continue
    return out


def downsample(arr, target=1500):
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
        out.append(round(sum(chunk) / len(chunk), 2) if chunk else None)
    return out


CSS = r"""
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:#0e1117; color:#e6e6e6;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
h1 { font-size:22px; margin:0 0 4px; font-weight:600; }
h2 { font-size:15px; margin:30px 0 10px; font-weight:600; color:#c9d1d9;
  border-bottom:1px solid #21262d; padding-bottom:6px; }
h3 { font-size:12px; margin:14px 0 6px; font-weight:600; color:#8b949e;
  text-transform:uppercase; letter-spacing:.6px; }
.sub { color:#8b949e; font-size:13px; margin-bottom:20px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:20px; }
.card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px 14px; }
.card .label { font-size:10px; text-transform:uppercase; letter-spacing:.5px; color:#8b949e; }
.card .value { font-size:21px; font-weight:600; color:#5DADE2; margin-top:3px; }
.controls { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; align-items:center; }
input, select { background:#161b22; border:1px solid #30363d; color:#e6e6e6;
  padding:8px 10px; border-radius:6px; font-size:13px; }
input { min-width:220px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; padding:9px 8px; background:#161b22; border-bottom:1px solid #30363d;
  color:#8b949e; font-weight:500; font-size:11px; text-transform:uppercase;
  cursor:pointer; user-select:none; position:sticky; top:0; }
th:hover { color:#5DADE2; }
td { padding:8px; border-bottom:1px solid #21262d; }
tbody tr.clickable { cursor:pointer; }
tbody tr:hover td { background:#1c2331; }
.num { text-align:right; font-variant-numeric:tabular-nums; }
.wrap { max-height:70vh; overflow:auto; border:1px solid #30363d; border-radius:8px; }
.loading { color:#8b949e; padding:40px; text-align:center; }
.count { color:#8b949e; font-size:12px; margin-bottom:8px; }
a { color:#5DADE2; text-decoration:none; font-size:13px; }
a:hover { text-decoration:underline; }
.chartbox { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px; margin-bottom:14px; }
canvas { width:100%; display:block; }
.legend { display:flex; gap:14px; font-size:12px; color:#8b949e; margin-bottom:8px; flex-wrap:wrap; }
.legend span i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; }
.kv { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:0 22px; font-size:13px; }
.kv div { padding:5px 0; border-bottom:1px solid #21262d; display:flex; justify-content:space-between; gap:10px; }
.kv .k { color:#8b949e; }
.kv .v { font-variant-numeric:tabular-nums; text-align:right; }
.kv .v.cf { color:#5DADE2; }
.toggles { display:flex; gap:12px; flex-wrap:wrap; font-size:12px; margin-bottom:10px; }
.toggles label { cursor:pointer; color:#8b949e; user-select:none; }
.toggles label.custom { color:#48C9B0; }
.pill { display:inline-block; background:#1c2331; border:1px solid #30363d; border-radius:12px;
  padding:3px 10px; font-size:11px; margin:2px 4px 2px 0; color:#5DADE2; }
.pill.custom { color:#48C9B0; border-color:#2d5a52; }
.zbar { display:flex; height:26px; border-radius:5px; overflow:hidden; margin:6px 0 4px; }
.zbar div { display:flex; align-items:center; justify-content:center; font-size:10px; color:#0e1117; font-weight:600; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
@media (max-width:800px){ .grid2 { grid-template-columns:1fr; } }
.err { color:#E74C3C; font-size:12px; }
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
  <a href="/api/activities" target="_blank">JSON</a>
  <a href="/api/debug/athlete" target="_blank">Debug atleta</a>
</div>
<div class="count" id="count"></div>
<div class="wrap"><table>
  <thead><tr id="head"></tr></thead>
  <tbody id="body"><tr><td class="loading">A carregar...</td></tr></tbody>
</table></div>
<script>
const COLS=[['date','Data',0],['name','Nome',0],['type','Tipo',0],['duration_min','Min',1],
 ['distance_km','km',1],['training_load','TL',1],['avg_watts','W',1],['ftp','FTP',1],
 ['avg_hr','HR',1],['max_hr','HR max',1],['joules','Joules',1],['source','Fonte',0]];
let data=[],sortKey='date',sortAsc=false;
function fmt(v,num){if(v===null||v===undefined||v==='')return '-';
 if(num&&typeof v==='number')return v.toLocaleString('pt-PT');return v;}
function render(){
 const q=document.getElementById('search').value.toLowerCase();
 const t=document.getElementById('typeFilter').value;
 let rows=data.filter(r=>(!q||(r.name||'').toLowerCase().indexOf(q)!==-1)&&(!t||r.type===t));
 rows.sort(function(a,b){var x=a[sortKey],y=b[sortKey];
  if(typeof x==='number')return sortAsc?x-y:y-x;
  x=String(x||'');y=String(y||'');return sortAsc?x.localeCompare(y):y.localeCompare(x);});
 document.getElementById('count').textContent=rows.length+' de '+data.length+' atividades';
 document.getElementById('body').innerHTML=rows.map(r=>'<tr class="clickable" onclick="location.href=\'/activity/'+r.id+'\'">'+
  COLS.map(c=>'<td class="'+(c[2]?'num':'')+'">'+fmt(r[c[0]],c[2])+'</td>').join('')+'</tr>').join('');
}
function buildHead(){
 document.getElementById('head').innerHTML=COLS.map(c=>'<th class="'+(c[2]?'num':'')+'" data-k="'+c[0]+'">'+c[1]+'</th>').join('');
 document.querySelectorAll('th').forEach(th=>th.onclick=function(){
  var k=th.dataset.k;if(sortKey===k)sortAsc=!sortAsc;else{sortKey=k;sortAsc=false;}render();});
}
async function load(){
 buildHead();
 const res=await Promise.all([fetch('/api/stats').then(r=>r.json()),fetch('/api/activities').then(r=>r.json())]);
 const s=res[0],a=res[1];
 document.getElementById('cards').innerHTML=[['Atividades',s.total_activities],['TL total',s.training_total_tl],
  ['TL medio',s.training_avg_tl],['Distancia',Math.round(s.distance_total_km)+' km'],
  ['Duracao media',s.duration_avg_min+' min'],['Watts medio',s.training_avg_watts+' W'],
  ['HR medio',s.hr_avg+' bpm'],['Com potencia',s.coverage_with_power]].map(c=>{var v=c[1];
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
  <canvas id="chart" height="360"></canvas>
</div>

<h2>Power vs HR &middot; decoupling</h2>
<div class="chartbox">
  <div class="legend" id="pvhLegend"></div>
  <canvas id="pvh" height="240"></canvas>
</div>

<h2>Curva de potencia</h2>
<div class="chartbox">
  <div class="legend" id="pcLegend"></div>
  <canvas id="pc" height="240"></canvas>
</div>

<h2>Distribuicao</h2>
<div class="grid2">
  <div class="chartbox"><div class="legend"><span><i style="background:#5DADE2"></i>Tempo por potencia</span></div>
    <canvas id="phist" height="200"></canvas></div>
  <div class="chartbox"><div class="legend"><span><i style="background:#E74C3C"></i>Tempo por HR</span></div>
    <canvas id="hhist" height="200"></canvas></div>
</div>

<h2>Tempo em zonas</h2>
<div id="zones"></div>

<h2>Custom fields do atleta</h2>
<div class="sub" id="cfCount"></div>
<div class="kv" id="customkv"></div>

<h2>Streams disponiveis</h2>
<div id="streamPills"></div>

<h2>Intervalos</h2>
<div class="wrap" style="max-height:360px"><table>
  <thead><tr id="ivHead"></tr></thead><tbody id="ivBody"></tbody></table></div>

<h2>Campos standard (Intervals.icu)</h2>
<div class="kv" id="rawkv"></div>

<div class="sub" style="margin-top:24px">
  <a href="/api/activity/__AID__/full" target="_blank">JSON completo</a> &middot;
  <a href="/api/activity/__AID__/debug" target="_blank">Debug de todos os endpoints</a>
</div>

<script>
const AID="__AID__";
const COLORS={watts:'#5DADE2',heartrate:'#E74C3C',cadence:'#F4D03F',altitude:'#58D68D',
 velocity_smooth:'#AF7AC5',temp:'#E67E22',smo2:'#48C9B0',thb:'#EC7063',torque:'#85929E',
 respiration:'#7FB3D5',dfa_a1:'#F1948A',RRa1:'#82E0AA',distance:'#566573',
 RespirationRateAlphaHRV:'#D7BDE2',hrv:'#F5B041',artifacts:'#5D6D7E'};
let STREAMS={},ACTIVE={},DATA=null;
function color(k){return COLORS[k]||'#8b949e';}
function ctx(id,h){const c=document.getElementById(id);const dpr=window.devicePixelRatio||1;
 const W=c.clientWidth;c.width=W*dpr;c.height=h*dpr;const g=c.getContext('2d');
 g.scale(dpr,dpr);g.clearRect(0,0,W,h);return {g:g,W:W,H:h};}
function noData(g,W,H,msg){g.fillStyle='#8b949e';g.font='13px sans-serif';g.fillText(msg||'Sem dados',20,30);}

function drawChart(){
 const o=ctx('chart',360),g=o.g,W=o.W,H=o.H;
 const PL=46,PR=46,PT=10,PB=26,w=W-PL-PR,h=H-PT-PB;
 const keys=Object.keys(ACTIVE).filter(k=>ACTIVE[k]&&STREAMS[k]&&STREAMS[k].length);
 if(!keys.length){noData(g,W,H,'Seleciona pelo menos uma serie');return;}
 const n=Math.max.apply(null,keys.map(k=>STREAMS[k].length));
 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 keys.forEach(function(k,idx){
  const s=STREAMS[k],vals=s.filter(v=>typeof v==='number');
  if(!vals.length)return;
  let mn=Math.min.apply(null,vals),mx=Math.max.apply(null,vals);
  if(mx===mn)mx=mn+1;
  g.strokeStyle=color(k);g.lineWidth=1.3;g.beginPath();let st=false;
  for(let i=0;i<s.length;i++){const v=s[i];if(typeof v!=='number'){st=false;continue;}
   const x=PL+w*i/(n-1),y=PT+h-(v-mn)/(mx-mn)*h;
   if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);}
  g.stroke();
  if(idx<2){const right=idx===1;g.fillStyle=color(k);g.font='10px sans-serif';
   g.textAlign=right?'left':'right';
   for(let i=0;i<=4;i++){const val=mx-(mx-mn)*i/4,y=PT+h*i/4;
    g.fillText(Math.round(val*10)/10,right?PL+w+6:PL-6,y+3);}
   g.textAlign='left';}
 });
 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='center';
 const el=window.__ELAPSED__||0;
 for(let i=0;i<=6;i++){const x=PL+w*i/6;g.fillText(Math.round(i/6*el/60)+'m',x,H-8);}
 g.textAlign='left';
}

function drawPvH(pvh){
 const o=ctx('pvh',240),g=o.g,W=o.W,H=o.H;
 const series=(pvh&&pvh.series)||[];
 if(!series.length){noData(g,W,H,'Sem power vs HR');return;}
 const PL=46,PR=46,PT=10,PB=26,w=W-PL-PR,h=H-PT-PB;
 const P=series.map(b=>b.watts),Hr=series.map(b=>b.hr);
 const pv=P.filter(v=>typeof v==='number'),hv=Hr.filter(v=>typeof v==='number');
 if(!pv.length||!hv.length){noData(g,W,H,'Sem power vs HR');return;}
 const pmn=Math.min.apply(null,pv),pmx=Math.max.apply(null,pv);
 const hmn=Math.min.apply(null,hv),hmx=Math.max.apply(null,hv);
 g.strokeStyle='#21262d';
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 function line(arr,mn,mx,col){g.strokeStyle=col;g.lineWidth=1.6;g.beginPath();let st=false;
  for(let i=0;i<arr.length;i++){const v=arr[i];if(typeof v!=='number'){st=false;continue;}
   const x=PL+w*i/(arr.length-1),y=PT+h-(v-mn)/((mx-mn)||1)*h;
   if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);}g.stroke();}
 line(P,pmn,pmx,'#5DADE2');line(Hr,hmn,hmx,'#E74C3C');
 g.font='10px sans-serif';g.textAlign='right';g.fillStyle='#5DADE2';
 for(let i=0;i<=4;i++)g.fillText(Math.round(pmx-(pmx-pmn)*i/4),PL-6,PT+h*i/4+3);
 g.textAlign='left';g.fillStyle='#E74C3C';
 for(let i=0;i<=4;i++)g.fillText(Math.round(hmx-(hmx-hmn)*i/4),PL+w+6,PT+h*i/4+3);
}

function drawPowerCurve(pc){
 const o=ctx('pc',240),g=o.g,W=o.W,H=o.H;
 const secs=pc&&pc.secs,watts=pc&&(pc.watts||pc.values);
 if(!secs||!watts||!secs.length){noData(g,W,H,'Sem curva de potencia');return;}
 const PL=46,PR=20,PT=10,PB=26,w=W-PL-PR,h=H-PT-PB;
 const lmin=Math.log10(Math.max(1,secs[0])),lmax=Math.log10(secs[secs.length-1]);
 const wv=watts.filter(v=>typeof v==='number');
 const mx=Math.max.apply(null,wv),mn=0;
 g.strokeStyle='#21262d';
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 g.strokeStyle='#5DADE2';g.lineWidth=1.6;g.beginPath();let st=false;
 for(let i=0;i<secs.length;i++){const v=watts[i];if(typeof v!=='number'){st=false;continue;}
  const x=PL+w*(Math.log10(Math.max(1,secs[i]))-lmin)/(lmax-lmin||1),y=PT+h-(v-mn)/(mx-mn||1)*h;
  if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);}
 g.stroke();
 g.fillStyle='#5DADE2';g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++)g.fillText(Math.round(mx-(mx-mn)*i/4)+'W',PL-6,PT+h*i/4+3);
 g.fillStyle='#8b949e';g.textAlign='center';
 [1,5,15,60,300,1200,3600].forEach(function(s){
  if(s<secs[0]||s>secs[secs.length-1])return;
  const x=PL+w*(Math.log10(s)-lmin)/(lmax-lmin||1);
  g.fillText(s<60?s+'s':(s/60)+'m',x,H-8);
  g.strokeStyle='#21262d';g.beginPath();g.moveTo(x,PT);g.lineTo(x,PT+h);g.stroke();});
 g.textAlign='left';
}

function drawHist(id,bins,col,unit){
 const o=ctx(id,200),g=o.g,W=o.W,H=o.H;
 if(!bins||!bins.length){noData(g,W,H,'Sem histograma');return;}
 const PL=44,PR=12,PT=10,PB=26,w=W-PL-PR,h=H-PT-PB;
 const mx=Math.max.apply(null,bins.map(b=>b.secs||0));
 const bw=w/bins.length;
 bins.forEach(function(b,i){
  const bh=h*(b.secs||0)/(mx||1);
  g.fillStyle=col;g.globalAlpha=.75;
  g.fillRect(PL+i*bw+1,PT+h-bh,bw-2,bh);g.globalAlpha=1;});
 g.fillStyle='#8b949e';g.font='9px sans-serif';g.textAlign='center';
 bins.forEach(function(b,i){ if(i%Math.ceil(bins.length/8)!==0)return;
  g.fillText(b.min,PL+i*bw+bw/2,H-8);});
 g.textAlign='right';
 for(let i=0;i<=3;i++)g.fillText(Math.round((mx-mx*i/3)/60)+'m',PL-6,PT+h*i/3+3);
 g.textAlign='left';
}

function drawZones(a){
 const el=document.getElementById('zones');
 let html='';
 const PZ=['#58D68D','#5DADE2','#F4D03F','#E67E22','#E74C3C','#C0392B','#8E44AD'];
 function bar(title,items){
  const tot=items.reduce((s,x)=>s+x.secs,0)||1;
  return '<h3>'+title+'</h3><div class="zbar">'+items.map(function(x,i){
   const pct=x.secs/tot*100;
   return '<div style="width:'+pct+'%;background:'+PZ[i%PZ.length]+'" title="'+x.label+'">'+
    (pct>6?Math.round(pct)+'%':'')+'</div>';}).join('')+'</div>'+
   '<div class="sub" style="margin:0">'+items.map(x=>x.label+' '+Math.round(x.secs/60)+'m').join(' · ')+'</div>';
 }
 if(Array.isArray(a.icu_zone_times)&&a.icu_zone_times.length)
  html+=bar('Potencia',a.icu_zone_times.map((z,i)=>({label:z.id||('Z'+(i+1)),secs:z.secs||z.seconds||0})));
 if(Array.isArray(a.icu_hr_zone_times)&&a.icu_hr_zone_times.length)
  html+=bar('Frequencia cardiaca',a.icu_hr_zone_times.map((s,i)=>({label:'Z'+(i+1),secs:s||0})));
 el.innerHTML=html||'<div class="sub">Sem dados de zonas</div>';
}

function fmtv(v){
 if(v===null||v===undefined)return '<span style="color:#484f58">null</span>';
 if(typeof v==='object')return '<span style="color:#8b949e">'+JSON.stringify(v).slice(0,70)+'</span>';
 if(typeof v==='number')return (Math.round(v*1000)/1000).toLocaleString('pt-PT');
 return String(v);
}

async function load(){
 const d=await fetch('/api/activity/'+AID+'/full').then(r=>r.json());
 if(d.error){document.getElementById('title').textContent='Erro: '+d.error;return;}
 DATA=d;
 const a=d.activity||{},cf=d.custom_fields||{};
 window.__ELAPSED__=a.elapsed_time||a.moving_time||0;
 document.getElementById('title').textContent=a.name||AID;
 document.getElementById('subtitle').textContent=
  (a.start_date_local||'')+'  ·  '+(a.type||'')+'  ·  '+(a.device_name||a.source||'');

 const cards=[['Duracao',Math.round((a.elapsed_time||0)/60)+' min'],
  ['Distancia',((a.icu_distance||a.distance||0)/1000).toFixed(1)+' km'],
  ['TL',a.icu_training_load],['NP',(a.icu_weighted_avg_watts||0)+' W'],
  ['FTP',(a.icu_pm_ftp||a.icu_ftp||0)+' W'],
  ['IF',a.icu_intensity!=null?(a.icu_intensity>3?a.icu_intensity.toFixed(0)+'%':a.icu_intensity.toFixed(2)):'-'],
  ['HR med',(a.average_heartrate||0)+' bpm'],['HR max',(a.max_heartrate||0)+' bpm'],
  ['kJ',Math.round((a.icu_joules||0)/1000)],['W prime',a.icu_pm_w_prime||0],
  ['W bal min',a.icu_max_wbal_depletion||0],
  ['Decoupling',(a.decoupling!=null?a.decoupling.toFixed(2):'-')+'%'],
  ['PI',a.polarization_index!=null?a.polarization_index.toFixed(2):'-'],
  ['VI',a.icu_variability_index!=null?a.icu_variability_index.toFixed(2):'-'],
  ['EF',a.icu_efficiency_factor!=null?a.icu_efficiency_factor.toFixed(2):'-'],
  ['kJ > FTP',Math.round((a.icu_joules_above_ftp||0)/1000)]];
 document.getElementById('cards').innerHTML=cards.map(c=>
  '<div class="card"><div class="label">'+c[0]+'</div><div class="value">'+(c[1]==null?'-':c[1])+'</div></div>').join('');

 STREAMS=d.streams||{};
 const names=Object.keys(STREAMS);
 const customSet=new Set(d.custom_stream_types||[]);
 document.getElementById('streamPills').innerHTML=
  (d.stream_types_available||names).map(t=>'<span class="pill'+(customSet.has(t)?' custom':'')+'">'+t+'</span>').join('')
  ||'<span class="sub">nenhum</span>';
 names.forEach(k=>{ACTIVE[k]=(k==='watts'||k==='heartrate');});
 document.getElementById('toggles').innerHTML=names.map(k=>
  '<label class="'+(customSet.has(k)?'custom':'')+'"><input type="checkbox" data-k="'+k+'" '+
  (ACTIVE[k]?'checked':'')+'> '+k+'</label>').join('');
 document.querySelectorAll('#toggles input').forEach(cb=>cb.onchange=function(){
  ACTIVE[cb.dataset.k]=cb.checked;updLegend();drawChart();});
 function updLegend(){document.getElementById('legend').innerHTML=names.filter(k=>ACTIVE[k])
  .map(k=>'<span><i style="background:'+color(k)+'"></i>'+k+'</span>').join('');}
 updLegend();drawChart();

 const pvh=d.power_vs_hr||{};
 document.getElementById('pvhLegend').innerHTML=
  '<span><i style="background:#5DADE2"></i>Power</span><span><i style="background:#E74C3C"></i>HR</span>'+
  (pvh.decoupling!=null?'<span>Decoupling '+pvh.decoupling.toFixed(2)+'%</span>':'')+
  (pvh.powerHr!=null?'<span>Power/HR '+pvh.powerHr.toFixed(3)+'</span>':'')+
  (pvh.powerHrFirst!=null?'<span>1a metade '+pvh.powerHrFirst.toFixed(3)+'</span>':'')+
  (pvh.powerHrSecond!=null?'<span>2a metade '+pvh.powerHrSecond.toFixed(3)+'</span>':'')+
  (pvh.hrLag!=null?'<span>HR lag '+pvh.hrLag+'s</span>':'');
 drawPvH(pvh);

 const pc=d.power_curve||{};
 document.getElementById('pcLegend').innerHTML='<span><i style="background:#5DADE2"></i>MMP (escala log)</span>'+
  (pc.vo2max_5m!=null?'<span>VO2max 5m '+pc.vo2max_5m.toFixed(1)+'</span>':'')+
  (pc.compound_score_5m!=null?'<span>Compound score '+Math.round(pc.compound_score_5m)+'</span>':'');
 drawPowerCurve(pc);

 drawHist('phist',d.power_histogram,'#5DADE2');
 drawHist('hhist',d.hr_histogram,'#E74C3C');
 drawZones(a);

 const cfKeys=Object.keys(cf).sort();
 document.getElementById('cfCount').textContent=cfKeys.length+' custom fields definidos por ti';
 document.getElementById('customkv').innerHTML=cfKeys.map(k=>
  '<div><span class="k">'+k+'</span><span class="v cf">'+fmtv(cf[k])+'</span></div>').join('');

 const ivs=(d.intervals&&(d.intervals.icu_intervals||d.intervals))||[];
 if(Array.isArray(ivs)&&ivs.length){
  const cols=['label','type','start_time','elapsed_time','distance','average_watts','max_watts',
   'weighted_average_watts','average_heartrate','max_heartrate','average_cadence','intensity','joules','decoupling'];
  document.getElementById('ivHead').innerHTML=cols.map(c=>'<th>'+c+'</th>').join('');
  document.getElementById('ivBody').innerHTML=ivs.map(iv=>'<tr>'+cols.map(function(c){
   var v=iv[c];return '<td class="num">'+(v==null?'-':(typeof v==='number'?Math.round(v*10)/10:v))+'</td>';
  }).join('')+'</tr>').join('');
 } else document.getElementById('ivBody').innerHTML='<tr><td class="loading">Sem intervalos</td></tr>';

 document.getElementById('rawkv').innerHTML=Object.keys(a).sort()
  .filter(k=>!(k in cf)).map(k=>'<div><span class="k">'+k+'</span><span class="v">'+fmtv(a[k])+'</span></div>').join('');
}
window.addEventListener('resize',function(){
 if(!DATA)return;drawChart();drawPvH(DATA.power_vs_hr||{});drawPowerCurve(DATA.power_curve||{});
 drawHist('phist',DATA.power_histogram,'#5DADE2');drawHist('hhist',DATA.hr_histogram,'#E74C3C');});
load();
</script></body></html>
"""


@app.route('/')
def index():
    return render_template_string(LIST_HTML.replace('__CSS__', CSS))


@app.route('/activity/<activity_id>')
def activity_page(activity_id):
    return render_template_string(
        DETAIL_HTML.replace('__CSS__', CSS).replace('__AID__', activity_id))


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
        'status': 'OK', 'total_activities': len(acts),
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
    act, err = icu_get(f"/activity/{activity_id}")
    if err:
        return jsonify({'error': err}), 502

    custom_fields = {k: v for k, v in act.items() if k not in STD_FIELDS}

    streams, stream_types, custom_stream_types = {}, [], []
    sdata, serr = icu_get(f"/activity/{activity_id}/streams", {"includeDefaults": "true"})
    if sdata and isinstance(sdata, list):
        for s in sdata:
            t = s.get('type') or s.get('name')
            if not t or s.get('allNull'):
                continue
            stream_types.append(t)
            if s.get('custom'):
                custom_stream_types.append(t)
            d = s.get('data')
            if isinstance(d, list) and d and not s.get('valueTypeIsArray'):
                if any(isinstance(v, (int, float)) for v in d):
                    streams[t] = downsample(d)
    streams.pop('time', None)

    pvh, _ = icu_get(f"/activity/{activity_id}/power-vs-hr")
    ivs, _ = icu_get(f"/activity/{activity_id}/intervals")
    pcurve, _ = icu_get(f"/activity/{activity_id}/power-curve")
    phist, _ = icu_get(f"/activity/{activity_id}/power-histogram")
    hhist, _ = icu_get(f"/activity/{activity_id}/hr-histogram")

    if isinstance(pcurve, dict):
        pcurve = {k: pcurve.get(k) for k in
                  ('secs', 'watts', 'values', 'watts_per_kg', 'weight',
                   'vo2max_5m', 'compound_score_5m')}

    return jsonify({
        'status': 'OK',
        'activity': act,
        'custom_fields': custom_fields,
        'streams': streams,
        'stream_types_available': stream_types,
        'custom_stream_types': custom_stream_types,
        'power_vs_hr': pvh or {},
        'intervals': ivs or {},
        'power_curve': pcurve or {},
        'power_histogram': phist or [],
        'hr_histogram': hhist or [],
        'meta': {
            'custom_field_count': len(custom_fields),
            'stream_count': len(stream_types),
            'null_fields': sorted([k for k, v in act.items() if v is None]),
        },
    })


@app.route('/api/activity/<activity_id>/debug')
def api_activity_debug(activity_id):
    out = {'activity_id': activity_id, 'endpoints': {}}
    probes = [
        ('activity',        f"/activity/{activity_id}", {"intervals": "true"}),
        ('streams',         f"/activity/{activity_id}/streams", {"includeDefaults": "true"}),
        ('intervals',       f"/activity/{activity_id}/intervals", None),
        ('power_vs_hr',     f"/activity/{activity_id}/power-vs-hr", None),
        ('power_curve',     f"/activity/{activity_id}/power-curve", None),
        ('hr_curve',        f"/activity/{activity_id}/hr-curve", None),
        ('power_histogram', f"/activity/{activity_id}/power-histogram", None),
        ('hr_histogram',    f"/activity/{activity_id}/hr-histogram", None),
        ('time_at_hr',      f"/activity/{activity_id}/time-at-hr", None),
        ('best_efforts_w',  f"/activity/{activity_id}/best-efforts", {"stream": "watts"}),
        ('best_efforts_hr', f"/activity/{activity_id}/best-efforts", {"stream": "heartrate"}),
        ('interval_stats',  f"/activity/{activity_id}/interval-stats",
                            {"start_index": 0, "end_index": 60}),
        ('weather_summary', f"/activity/{activity_id}/weather-summary", None),
        ('segments',        f"/activity/{activity_id}/segments", None),
        ('hr_load_model',   f"/activity/{activity_id}/hr-load-model", None),
        ('power_spike',     f"/activity/{activity_id}/power-spike-model", None),
        ('map',             f"/activity/{activity_id}/map", None),
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
                    'type': s.get('type'), 'custom': s.get('custom'),
                    'allNull': s.get('allNull'),
                    'points': len(s.get('data') or []) if isinstance(s.get('data'), list) else None,
                    'sample': (s.get('data') or [])[:5] if isinstance(s.get('data'), list) else None,
                } for s in data]
            else:
                info['sample'] = data[:3]
        elif isinstance(data, dict):
            info['keys'] = sorted(data.keys())
            info['null_keys'] = sorted([k for k, v in data.items() if v is None])
            if name == 'activity':
                info['custom_fields'] = {k: v for k, v in data.items()
                                         if k not in STD_FIELDS and not isinstance(v, (list, dict))}
                info['standard_fields'] = {k: v for k, v in data.items()
                                           if k in STD_FIELDS and not isinstance(v, (list, dict))}
            else:
                info['sample'] = {k: (str(v)[:120] if isinstance(v, (list, dict)) else v)
                                  for k, v in list(data.items())[:40]}
        out['endpoints'][name] = info
    return jsonify(out)


@app.route('/api/debug/athlete')
def api_debug_athlete():
    out = {}
    for name, path in [
        ('athlete',        f"/athlete/{ATHLETE_ID}"),
        ('custom_item',    f"/athlete/{ATHLETE_ID}/custom-item"),
        ('activity_tags',  f"/athlete/{ATHLETE_ID}/activity-tags"),
        ('sport_settings', f"/athlete/{ATHLETE_ID}/sport-settings"),
        ('training_plan',  f"/athlete/{ATHLETE_ID}/training-plan"),
    ]:
        data, err = icu_get(path)
        if err:
            out[name] = {'ok': False, 'error': err}
        elif isinstance(data, dict):
            out[name] = {'ok': True, 'keys': sorted(data.keys()),
                         'values': {k: v for k, v in data.items() if not isinstance(v, (list, dict))}}
        else:
            if name == 'custom_item':
                out[name] = {'ok': True, 'count': len(data), 'items': [{
                    'id': it.get('id'), 'name': it.get('name'), 'type': it.get('type'),
                    'code': (it.get('content') or {}).get('code'),
                    'value_type': (it.get('content') or {}).get('type'),
                    'units': (it.get('content') or {}).get('units'),
                    'description': it.get('description'),
                } for it in data]}
            else:
                out[name] = {'ok': True, 'count': len(data), 'sample': data[:5]}
    return jsonify(out)


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
