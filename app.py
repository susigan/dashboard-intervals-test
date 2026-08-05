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

# ── Normalizacao de modalidades (identica ao repo dashboard/config.py) ──
TYPE_MAP = {
    'VirtualSki': 'Ski', 'AlpineSki': 'Ski', 'Ski': 'Ski', 'NordicSki': 'Ski',
    'BackcountrySki': 'Ski', 'RollerSki': 'Ski',
    'VirtualRow': 'Row', 'Rowing': 'Row', 'Row': 'Row', 'Kayaking': 'Row',
    'VirtualRide': 'Bike', 'Cycling': 'Bike', 'Ride': 'Bike', 'Bike': 'Bike',
    'MountainBike': 'Bike', 'MountainBikeRide': 'Bike', 'GravelRide': 'Bike',
    'EBikeRide': 'Bike', 'Handcycle': 'Bike',
    'VirtualRun': 'Run', 'Running': 'Run', 'Run': 'Run', 'TrailRun': 'Run',
    'Treadmill': 'Run', 'Walk': 'Run', 'Hike': 'Run',
    'WeightTraining': 'WeightTraining', 'Workout': 'WeightTraining',
}
CICLICOS = ['Bike', 'Row', 'Run', 'Ski']
VALID_TYPES = CICLICOS + ['WeightTraining']
CORES_MOD = {'Bike': '#E74C3C', 'Row': '#3498DB', 'Run': '#2ECC71',
             'Ski': '#9B59B6', 'WeightTraining': '#F39C12', 'Other': '#7F8C8D'}


def norm_tipo(t):
    """AlpineSki/VirtualSki -> Ski, VirtualRide/Ride -> Bike, etc."""
    if not t:
        return 'Other'
    return TYPE_MAP.get(t, TYPE_MAP.get(str(t).strip(), 'Other'))


def kj_da_atividade(a):
    """kJ de trabalho.

    Prioridade identica ao repo dashboard (data.py:2548-2552):
      1) Z1KJ + Z2KJ + Z3KJ  (custom fields, dao decomposicao por zona)
      2) icu_joules / 1000   (fallback)
    Nas atividades verificadas os dois batem certo ao milesimo.
    """
    z = [a.get('Z1KJ'), a.get('Z2KJ'), a.get('Z3KJ')]
    if any(isinstance(v, (int, float)) for v in z):
        return float(sum(v for v in z if isinstance(v, (int, float))))
    j = a.get('icu_joules')
    return float(j) / 1000.0 if isinstance(j, (int, float)) else 0.0


def classificar_rpe(v):
    """Leve 1-4.9 | Moderado 5-6.9 | Pesado 7-10 (helpers.py:156)."""
    if not isinstance(v, (int, float)):
        return None
    v = float(v)
    if 1 <= v <= 4.9:
        return 'Leve'
    if 5 <= v <= 6.9:
        return 'Moderado'
    if 7 <= v <= 10:
        return 'Pesado'
    return None


def num(v, default=0.0):
    return float(v) if isinstance(v, (int, float)) else default


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
            raw_type = p.get_activity_type(a)
            out.append({
                'id': p.get_activity_id(a), 'date': p.get_start_date_local(a)[:10],
                'name': p.get_activity_name(a),
                'type': norm_tipo(raw_type), 'type_raw': raw_type,
                'duration_min': round(p.get_duration_minutes(a), 1),
                'distance_km': round(p.get_distance_km(a), 1),
                'ftp': p.get_ftp(a), 'avg_watts': p.get_avg_watts(a),
                'kj': round(kj_da_atividade(a), 1),
                'training_load': p.get_training_load(a),
                'rpe': a.get('icu_rpe'), 'xss': a.get('SS'),
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
.nav { display:flex; gap:4px; margin-bottom:18px; border-bottom:1px solid #21262d; }
.nav a { padding:9px 16px; font-size:13px; color:#8b949e; border-bottom:2px solid transparent;
  text-decoration:none; font-weight:500; }
.nav a:hover { color:#c9d1d9; text-decoration:none; }
.nav a.on { color:#5DADE2; border-bottom-color:#5DADE2; }
label.sel { font-size:12px; color:#8b949e; display:flex; align-items:center; gap:6px; }
"""

NAV = '''<div class="nav">
  <a href="/" class="__ON_VOL__">Volume</a>
  <a href="/atividades" class="__ON_ACT__">Atividades</a>
</div>'''

VOLUME_HTML = r"""
<!DOCTYPE html><html lang="pt"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Volume &amp; Carga</title><style>__CSS__</style></head><body>
__NAV__
<h1>Volume &amp; Carga</h1>
<div class="sub" id="sub">A carregar...</div>

<div class="controls">
  <label class="sel">Periodo
    <select id="periodo">
      <option value="semana">Semana</option>
      <option value="mes" selected>Mes</option>
      <option value="ano">Ano</option>
    </select>
  </label>
  <label class="sel">Vista
    <select id="modo">
      <option value="abs">Valores absolutos</option>
      <option value="pct">Percentagem (100%)</option>
    </select>
  </label>
  <label class="sel">Ultimos
    <select id="janela">
      <option value="12">12 periodos</option>
      <option value="26">26 periodos</option>
      <option value="52" selected>52 periodos</option>
      <option value="0">Tudo</option>
    </select>
  </label>
  <span id="mapping" class="sub" style="margin:0"></span>
</div>

<div class="cards" id="kpis"></div>

<h2>Horas por modalidade</h2>
<div class="chartbox"><div class="legend" id="lgHoras"></div>
  <canvas id="chHoras" height="260"></canvas></div>

<h2>Distancia (km) por modalidade</h2>
<div class="chartbox"><div class="legend" id="lgKm"></div>
  <canvas id="chKm" height="260"></canvas></div>

<h2>Trabalho (kJ) por modalidade</h2>
<div class="sub">kJ = Z1KJ + Z2KJ + Z3KJ &middot; fallback icu_joules/1000</div>
<div class="chartbox"><div class="legend" id="lgKj"></div>
  <canvas id="chKj" height="260"></canvas></div>

<h2>Trabalho por zona (Z1 / Z2 / Z3)</h2>
<div class="controls"><label class="sel">Modalidade
  <select id="modZona"><option value="">Todas</option></select></label></div>
<div class="chartbox"><div class="legend" id="lgZona"></div>
  <canvas id="chZona" height="260"></canvas></div>

<h2>Horas por RPE</h2>
<div class="sub">Leve 1&ndash;4.9 &middot; Moderado 5&ndash;6.9 &middot; Pesado 7&ndash;10</div>
<div class="chartbox"><div class="legend" id="lgRpe"></div>
  <canvas id="chRpe" height="260"></canvas></div>

<h2>Strain Score (XSS) por modalidade</h2>
<div class="sub">Outliers removidos por modalidade (IQR &times; 3), como no dashboard</div>
<div class="chartbox"><div class="legend" id="lgXss"></div>
  <canvas id="chXss" height="260"></canvas></div>

<h2>Training Load por modalidade</h2>
<div class="chartbox"><div class="legend" id="lgTl"></div>
  <canvas id="chTl" height="260"></canvas></div>

<h2>Sistema energetico</h2>
<div class="sub">oxidative = Aerobic &middot; glycolytic = Glycolytic &middot; sprint = Pmax</div>
<div class="chartbox"><div class="legend" id="lgSys"></div>
  <canvas id="chSys" height="260"></canvas></div>

<h2>Tabela resumo</h2>
<div class="controls"><label class="sel">Metrica
  <select id="tblMetric">
    <option value="horas">Horas</option><option value="km">km</option>
    <option value="kj">kJ</option><option value="tl">Training Load</option>
    <option value="xss">XSS</option>
  </select></label></div>
<div class="wrap" style="max-height:420px"><table>
  <thead><tr id="tblHead"></tr></thead><tbody id="tblBody"></tbody></table></div>

<script>
let SESS=[],CIC=[],CORES={},RPECOR={Leve:'#58D68D',Moderado:'#F4D03F',Pesado:'#E74C3C'};
const ZCOR={z1_kj:'#58D68D',z2_kj:'#F4D03F',z3_kj:'#E74C3C'};
const SYSCOR={aerobic:'#5DADE2',glycolytic:'#F39C12',sprint:'#E74C3C'};

function periodoDe(dateStr,tipo){
 const d=new Date(dateStr+'T00:00:00');
 if(tipo==='ano')return String(d.getFullYear());
 if(tipo==='mes')return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0');
 // semana ISO
 const t=new Date(Date.UTC(d.getFullYear(),d.getMonth(),d.getDate()));
 const dn=t.getUTCDay()||7; t.setUTCDate(t.getUTCDate()+4-dn);
 const y0=new Date(Date.UTC(t.getUTCFullYear(),0,1));
 const wk=Math.ceil(((t-y0)/86400000+1)/7);
 return t.getUTCFullYear()+'-W'+String(wk).padStart(2,'0');
}

function pivot(rows,periodo,groupKey,valueKey,groups){
 const map={};
 rows.forEach(function(r){
  const p=periodoDe(r.date,periodo);
  const g=typeof groupKey==='function'?groupKey(r):r[groupKey];
  if(g==null||groups.indexOf(g)===-1)return;
  const v=typeof valueKey==='function'?valueKey(r):r[valueKey];
  if(!isFinite(v))return;
  map[p]=map[p]||{}; map[p][g]=(map[p][g]||0)+v;
 });
 return Object.keys(map).sort().map(p=>({periodo:p,vals:map[p]}));
}

function pivotCols(rows,periodo,cols){
 const map={};
 rows.forEach(function(r){
  const p=periodoDe(r.date,periodo);
  map[p]=map[p]||{};
  cols.forEach(function(c){const v=r[c];if(isFinite(v))map[p][c]=(map[p][c]||0)+v;});
 });
 return Object.keys(map).sort().map(p=>({periodo:p,vals:map[p]}));
}

function janela(data){
 const n=parseInt(document.getElementById('janela').value,10);
 return (n>0&&data.length>n)?data.slice(-n):data;
}

function drawStack(canvasId,legendId,data,groups,cores,unit,decimals){
 data=janela(data);
 const c=document.getElementById(canvasId),dpr=window.devicePixelRatio||1;
 const W=c.clientWidth,H=260;
 c.width=W*dpr;c.height=H*dpr;
 const g=c.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,W,H);
 document.getElementById(legendId).innerHTML=groups.map(k=>
  '<span><i style="background:'+(cores[k]||'#8b949e')+'"></i>'+k+'</span>').join('');
 if(!data.length){g.fillStyle='#8b949e';g.font='13px sans-serif';g.fillText('Sem dados',20,30);return;}
 const pct=document.getElementById('modo').value==='pct';
 const PL=54,PR=14,PT=12,PB=34,w=W-PL-PR,h=H-PT-PB;
 const totals=data.map(d=>groups.reduce((s,k)=>s+(d.vals[k]||0),0));
 const mx=pct?100:Math.max.apply(null,totals.concat([1]));
 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 const bw=w/data.length,pad=Math.min(4,bw*0.18);
 data.forEach(function(d,i){
  const tot=totals[i]||1;let acc=0;
  groups.forEach(function(k){
   let v=d.vals[k]||0; if(!v)return;
   if(pct)v=v/tot*100;
   const bh=h*v/mx;
   g.fillStyle=cores[k]||'#8b949e';
   g.fillRect(PL+i*bw+pad/2,PT+h-acc-bh,bw-pad,bh);
   acc+=bh;});
 });
 // media
 if(!pct){
  const avg=totals.reduce((s,x)=>s+x,0)/totals.length;
  const y=PT+h-h*avg/mx;
  g.strokeStyle='#8b949e';g.setLineDash([4,4]);g.beginPath();
  g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();g.setLineDash([]);
  g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='left';
  g.fillText('media '+avg.toFixed(decimals||0)+(unit||''),PL+4,y-4);
 }
 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++){const v=mx-mx*i/4;
  g.fillText(pct?Math.round(v)+'%':(v>=1000?Math.round(v/1000)+'k':v.toFixed(decimals||0)),PL-6,PT+h*i/4+3);}
 g.textAlign='center';
 const step=Math.ceil(data.length/12);
 data.forEach(function(d,i){if(i%step!==0)return;
  g.save();g.translate(PL+i*bw+bw/2,H-8);
  if(data.length>16){g.rotate(-Math.PI/5);g.textAlign='right';}
  g.fillText(d.periodo,0,0);g.restore();});
 g.textAlign='left';
}

function limparOutliers(rows,col,factor){
 factor=factor||3;
 const out=rows.map(r=>Object.assign({},r));
 const mods=Array.from(new Set(out.map(r=>r.type)));
 let n=0;
 mods.forEach(function(m){
  const vals=out.filter(r=>r.type===m&&isFinite(r[col])&&r[col]>0).map(r=>r[col]).sort((a,b)=>a-b);
  if(vals.length<8)return;
  const q=p=>vals[Math.floor(p*(vals.length-1))];
  const q1=q(0.25),q3=q(0.75),iqr=q3-q1;
  if(iqr===0)return;
  const lo=q1-factor*iqr,hi=q3+factor*iqr;
  out.forEach(function(r){if(r.type===m&&isFinite(r[col])&&(r[col]<lo||r[col]>hi)){r[col]=0;n++;}});
 });
 window.__NOUT__=n;
 return out;
}

function fmtH(h){const H=Math.floor(h),M=Math.round((h-H)*60);return H+'h'+String(M).padStart(2,'0');}

function redraw(){
 const per=document.getElementById('periodo').value;
 const cic=SESS.filter(r=>CIC.indexOf(r.type)!==-1);

 drawStack('chHoras','lgHoras',pivot(cic,per,'type','horas',CIC),CIC,CORES,'h',1);
 drawStack('chKm','lgKm',pivot(cic.filter(r=>r.km>0),per,'type','km',CIC),CIC,CORES,'km',0);
 drawStack('chKj','lgKj',pivot(cic.filter(r=>r.kj>0),per,'type','kj',CIC),CIC,CORES,'kJ',0);

 const mz=document.getElementById('modZona').value;
 const zrows=mz?cic.filter(r=>r.type===mz):cic;
 drawStack('chZona','lgZona',pivotCols(zrows,per,['z1_kj','z2_kj','z3_kj']),
  ['z1_kj','z2_kj','z3_kj'],ZCOR,'kJ',0);

 drawStack('chRpe','lgRpe',pivot(cic.filter(r=>r.rpe_cat),per,'rpe_cat','horas',
  ['Leve','Moderado','Pesado']),['Leve','Moderado','Pesado'],RPECOR,'h',1);

 const xssClean=limparOutliers(cic,'xss',3);
 drawStack('chXss','lgXss',pivot(xssClean.filter(r=>r.xss>0),per,'type','xss',CIC),CIC,CORES,'',0);
 const nout=window.__NOUT__||0;

 drawStack('chTl','lgTl',pivot(cic.filter(r=>r.tl>0),per,'type','tl',CIC),CIC,CORES,'',0);
 drawStack('chSys','lgSys',pivotCols(cic,per,['aerobic','glycolytic','sprint']),
  ['aerobic','glycolytic','sprint'],SYSCOR,'',0);

 // KPIs do periodo mais recente vs anterior
 const ph=janela(pivot(cic,per,'type','horas',CIC));
 const pk=janela(pivot(cic.filter(r=>r.kj>0),per,'type','kj',CIC));
 const pd=janela(pivot(cic.filter(r=>r.km>0),per,'type','km',CIC));
 const pt=janela(pivot(cic.filter(r=>r.tl>0),per,'type','tl',CIC));
 function tot(arr,i){if(!arr.length)return 0;const d=arr[arr.length+i];
  return d?CIC.reduce((s,k)=>s+(d.vals[k]||0),0):0;}
 function delta(cur,prev){if(!prev)return '';
  const p=(cur-prev)/prev*100;
  const col=p>=0?'#2ECC71':'#E74C3C';
  return '<span style="color:'+col+';font-size:11px"> '+(p>=0?'+':'')+p.toFixed(0)+'%</span>';}
 const kpis=[['Horas',fmtH(tot(ph,-1)),delta(tot(ph,-1),tot(ph,-2))],
  ['km',Math.round(tot(pd,-1)),delta(tot(pd,-1),tot(pd,-2))],
  ['kJ',Math.round(tot(pk,-1)).toLocaleString('pt-PT'),delta(tot(pk,-1),tot(pk,-2))],
  ['Training Load',Math.round(tot(pt,-1)),delta(tot(pt,-1),tot(pt,-2))],
  ['Sessoes',cic.length,''],
  ['Outliers XSS',nout,'']];
 document.getElementById('kpis').innerHTML=kpis.map(k=>
  '<div class="card"><div class="label">'+k[0]+'</div><div class="value">'+k[1]+k[2]+'</div></div>').join('');

 tabela(per,cic);
}

function tabela(per,cic){
 const metric=document.getElementById('tblMetric').value;
 const rows=janela(pivot(cic.filter(r=>isFinite(r[metric])),per,'type',metric,CIC));
 const cols=['Periodo'].concat(CIC).concat(['Total']);
 document.getElementById('tblHead').innerHTML=cols.map((c,i)=>
  '<th class="'+(i?'num':'')+'">'+c+'</th>').join('');
 const dec=metric==='horas'?1:0;
 document.getElementById('tblBody').innerHTML=rows.slice().reverse().map(function(d){
  const tot=CIC.reduce((s,k)=>s+(d.vals[k]||0),0);
  return '<tr><td>'+d.periodo+'</td>'+CIC.map(function(k){
   const v=d.vals[k]||0;
   const pct=tot?(v/tot*100).toFixed(0):0;
   return '<td class="num">'+(v?v.toFixed(dec)+' <span style="color:#8b949e;font-size:11px">'+pct+'%</span>':'-')+'</td>';
  }).join('')+'<td class="num"><b>'+tot.toFixed(dec)+'</b></td></tr>';}).join('');
}

async function load(){
 const d=await fetch('/api/volume').then(r=>r.json());
 if(d.error){document.getElementById('sub').textContent='Erro: '+d.error;return;}
 SESS=d.sessions||[];CIC=d.ciclicos||[];CORES=d.cores||{};
 document.getElementById('sub').textContent=d.count+' sessoes nos ultimos 365 dias';
 const mp=d.type_mapping||{};
 document.getElementById('mapping').innerHTML=Object.keys(mp).map(k=>
  '<span class="pill">'+k+' &larr; '+mp[k].join(', ')+'</span>').join('');
 const sel=document.getElementById('modZona');
 CIC.forEach(function(m){if(SESS.some(r=>r.type===m)){
  const o=document.createElement('option');o.value=m;o.textContent=m;sel.appendChild(o);}});
 redraw();
}
['periodo','modo','janela','modZona','tblMetric'].forEach(id=>
 document.getElementById(id).onchange=redraw);
window.addEventListener('resize',function(){if(SESS.length)redraw();});
load();
</script></body></html>
"""


LIST_HTML = r"""
<!DOCTYPE html><html lang="pt"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Atividades</title><style>__CSS__</style></head><body>
__NAV__
<h1>Atividades</h1>
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
const COLS=[['date','Data',0],['name','Nome',0],['type','Tipo',0],['type_raw','Tipo API',0],
 ['duration_min','Min',1],['distance_km','km',1],['kj','kJ',1],['training_load','TL',1],
 ['avg_watts','W',1],['ftp','FTP',1],['rpe','RPE',1],['xss','XSS',1],
 ['avg_hr','HR',1],['max_hr','HR max',1],['source','Fonte',0]];
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
__NAV__
<a href="/atividades">&larr; Voltar a lista</a>
<h1 id="title">A carregar...</h1>
<div class="sub" id="subtitle"></div>
<div class="cards" id="cards"></div>

<h2>Series temporais</h2>
<div class="toggles" id="toggles"></div>
<div class="chartbox">
  <div class="legend" id="legend"></div>
  <canvas id="chart" height="360"></canvas>
</div>

<div id="nirsSection" style="display:none">
<h2>NIRS &middot; SmO<sub>2</sub> / THb</h2>
<div class="toggles" id="nirsToggles"></div>
<div class="chartbox">
  <div class="legend" id="nirsLegend"></div>
  <canvas id="nirs" height="260"></canvas>
</div>
<div class="cards" id="nirsCards"></div>
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
 velocity_smooth:'#AF7AC5',temp:'#E67E22',Temperature:'#D68910',smo2:'#48C9B0',smo2_2:'#1ABC9C',
 thb:'#EC7063',thb_2:'#CD6155',O2Hb:'#F39C12',HHb:'#9B59B6',DiffHb:'#E59866',torque:'#85929E',
 respiration:'#7FB3D5',dfa_a1:'#F1948A',RRa1:'#82E0AA',distance:'#566573',Speed:'#A569BD',
 RespirationRateAlphaHRV:'#D7BDE2',hrv:'#F5B041',artifacts:'#5D6D7E',
 GarminDistanceperStroke:'#7DCEA0',WorkperStrokeEstimated:'#BB8FCE'};
const NIRS=['smo2','thb','O2Hb','HHb','DiffHb'];
let STREAMS={},META=[],ACTIVE={},NACTIVE={},DATA=null;
function color(k){return COLORS[k]||'#8b949e';}
function metaOf(k){for(var i=0;i<META.length;i++)if(META[i].key===k)return META[i];return {key:k,label:k,type:k};}
function ctx(id,h){const c=document.getElementById(id);const dpr=window.devicePixelRatio||1;
 const W=c.clientWidth;c.width=W*dpr;c.height=h*dpr;const g=c.getContext('2d');
 g.scale(dpr,dpr);g.clearRect(0,0,W,h);return {g:g,W:W,H:h};}
function noData(g,W,H,msg){g.fillStyle='#8b949e';g.font='13px sans-serif';g.fillText(msg||'Sem dados',20,30);}

function drawSeries(canvasId,height,keys){
 const o=ctx(canvasId,height),g=o.g,W=o.W,H=o.H;
 const PL=46,PR=46,PT=10,PB=26,w=W-PL-PR,h=H-PT-PB;
 keys=keys.filter(k=>STREAMS[k]&&STREAMS[k].length);
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
function drawChart(){drawSeries('chart',360,Object.keys(ACTIVE).filter(k=>ACTIVE[k]));}
function drawNirs(){
 const keys=Object.keys(NACTIVE).filter(k=>NACTIVE[k]);
 if(!keys.length){const o=ctx('nirs',260);noData(o.g,o.W,o.H,'Sem canais NIRS selecionados');return;}
 drawSeries('nirs',260,keys);
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
 META=d.stream_meta||[];
 const names=Object.keys(STREAMS);
 const nirsKeys=META.filter(m=>NIRS.indexOf(m.type)!==-1&&m.plotted).map(m=>m.key);
 const mainKeys=names.filter(k=>nirsKeys.indexOf(k)===-1);

 document.getElementById('streamPills').innerHTML=META.map(function(m){
  const t=m.sensor_name?(m.type+' - '+m.sensor_name):m.key;
  return '<span class="pill'+(m.custom?' custom':'')+'"'+(m.plotted?'':' style="opacity:.45"')+
   ' title="'+(m.points||0)+' pontos">'+t+'</span>';}).join('')||'<span class="sub">nenhum</span>';

 // grafico principal
 mainKeys.forEach(k=>{ACTIVE[k]=(k==='watts'||k==='heartrate');});
 document.getElementById('toggles').innerHTML=mainKeys.map(function(k){
  const m=metaOf(k);
  return '<label class="'+(m.custom?'custom':'')+'"><input type="checkbox" data-k="'+k+'" '+
   (ACTIVE[k]?'checked':'')+'> '+(m.sensor_name||k)+'</label>';}).join('');
 document.querySelectorAll('#toggles input').forEach(cb=>cb.onchange=function(){
  ACTIVE[cb.dataset.k]=cb.checked;updLegend();drawChart();});
 function updLegend(){document.getElementById('legend').innerHTML=mainKeys.filter(k=>ACTIVE[k])
  .map(k=>'<span><i style="background:'+color(k)+'"></i>'+(metaOf(k).sensor_name||k)+'</span>').join('');}
 updLegend();drawChart();

 // grafico NIRS
 if(nirsKeys.length){
  document.getElementById('nirsSection').style.display='';
  nirsKeys.forEach(k=>{NACTIVE[k]=(metaOf(k).type==='smo2'||metaOf(k).type==='thb');});
  document.getElementById('nirsToggles').innerHTML=nirsKeys.map(function(k){
   const m=metaOf(k);
   return '<label class="'+(m.custom?'custom':'')+'"><input type="checkbox" data-k="'+k+'" '+
    (NACTIVE[k]?'checked':'')+'> '+(m.sensor_name||k)+'</label>';}).join('');
  document.querySelectorAll('#nirsToggles input').forEach(cb=>cb.onchange=function(){
   NACTIVE[cb.dataset.k]=cb.checked;updNirsLegend();drawNirs();});
  function updNirsLegend(){document.getElementById('nirsLegend').innerHTML=nirsKeys.filter(k=>NACTIVE[k])
   .map(k=>'<span><i style="background:'+color(k)+'"></i>'+(metaOf(k).sensor_name||k)+'</span>').join('');}
  updNirsLegend();drawNirs();
  // estatisticas por canal
  document.getElementById('nirsCards').innerHTML=nirsKeys.map(function(k){
   const v=STREAMS[k].filter(x=>typeof x==='number');
   if(!v.length)return '';
   const mn=Math.min.apply(null,v),mx=Math.max.apply(null,v);
   const avg=v.reduce((s,x)=>s+x,0)/v.length;
   const m=metaOf(k);
   return '<div class="card"><div class="label">'+(m.sensor_name||k)+'</div>'+
    '<div class="value">'+avg.toFixed(1)+'</div>'+
    '<div class="label" style="margin-top:4px">min '+mn.toFixed(1)+' · max '+mx.toFixed(1)+
    ' · amp '+(mx-mn).toFixed(1)+'</div></div>';}).join('');
 }

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
 if(!DATA)return;drawChart();
 if(Object.keys(NACTIVE).length)drawNirs();
 drawPvH(DATA.power_vs_hr||{});drawPowerCurve(DATA.power_curve||{});
 drawHist('phist',DATA.power_histogram,'#5DADE2');drawHist('hhist',DATA.hr_histogram,'#E74C3C');});
load();
</script></body></html>
"""


def _nav(active):
    return (NAV.replace('__ON_VOL__', 'on' if active == 'volume' else '')
               .replace('__ON_ACT__', 'on' if active == 'atividades' else ''))


@app.route('/')
def index():
    return render_template_string(
        VOLUME_HTML.replace('__CSS__', CSS).replace('__NAV__', _nav('volume')))


@app.route('/atividades')
def atividades_page():
    return render_template_string(
        LIST_HTML.replace('__CSS__', CSS).replace('__NAV__', _nav('atividades')))


@app.route('/activity/<activity_id>')
def activity_page(activity_id):
    return render_template_string(
        DETAIL_HTML.replace('__CSS__', CSS).replace('__AID__', activity_id)
                   .replace('__NAV__', _nav('atividades')))


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


@app.route('/api/volume')
def api_volume():
    """Agregados de volume por periodo e modalidade, a partir das atividades."""
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'Fetch failed'}), 500

    rows = []
    for a in acts:
        d = (a.get('start_date_local') or '')[:10]
        if len(d) != 10:
            continue
        tipo = norm_tipo(a.get('type'))
        secs = num(a.get('elapsed_time')) or num(a.get('moving_time'))
        dist = num(a.get('icu_distance')) or num(a.get('distance'))
        rows.append({
            'id': a.get('id'), 'date': d, 'type': tipo, 'type_raw': a.get('type'),
            'horas': secs / 3600.0,
            'km': dist / 1000.0,
            'kj': kj_da_atividade(a),
            'z1_kj': num(a.get('Z1KJ')), 'z2_kj': num(a.get('Z2KJ')), 'z3_kj': num(a.get('Z3KJ')),
            'z1_sec': num(a.get('Z1sec')), 'z2_sec': num(a.get('Z2sec')), 'z3_sec': num(a.get('Z3sec')),
            'tl': num(a.get('icu_training_load')),
            'rpe': a.get('icu_rpe'),
            'rpe_cat': classificar_rpe(a.get('icu_rpe')),
            'xss': num(a.get('SS')),
            'aerobic': num(a.get('Aerobic')),
            'glycolytic': num(a.get('Glycolytic')),
            'sprint': num(a.get('Pmax')),
            'epoc': num(a.get('EPOC')),
            'work_hour': num(a.get('WorkHour')),
        })

    tipos_raw = {}
    for r in rows:
        tipos_raw.setdefault(r['type'], set()).add(r['type_raw'])

    return jsonify({
        'status': 'OK',
        'count': len(rows),
        'sessions': rows,
        'ciclicos': CICLICOS,
        'cores': CORES_MOD,
        'type_mapping': {k: sorted(v) for k, v in sorted(tipos_raw.items())},
        'kj_note': 'kJ = Z1KJ+Z2KJ+Z3KJ, fallback icu_joules/1000',
    })


@app.route('/api/activity/<activity_id>/full')
def api_activity_full(activity_id):
    act, err = icu_get(f"/activity/{activity_id}")
    if err:
        return jsonify({'error': err}), 502

    custom_fields = {k: v for k, v in act.items() if k not in STD_FIELDS}
    act['_type_norm'] = norm_tipo(act.get('type'))
    act['_kj'] = round(kj_da_atividade(act), 2)

    streams, stream_meta = {}, []
    sdata, serr = icu_get(f"/activity/{activity_id}/streams", {"includeDefaults": "true"})
    if sdata and isinstance(sdata, list):
        for s in sdata:
            t = s.get('type')
            if not t or t == 'time' or s.get('allNull'):
                continue
            d = s.get('data')
            has_values = (isinstance(d, list) and d
                          and not s.get('valueTypeIsArray')
                          and any(isinstance(v, (int, float)) for v in d))
            # chave unica: se ja existe este type (ex. 2 sensores Moxy), sufixa
            key = t
            n = 2
            while key in streams or any(m['key'] == key for m in stream_meta):
                key = f"{t}_{n}"
                n += 1
            stream_meta.append({
                'key': key, 'type': t,
                'label': s.get('name') or t,
                'sensor_name': s.get('name'),
                'custom': bool(s.get('custom')),
                'points': len(d) if isinstance(d, list) else None,
                'plotted': bool(has_values),
            })
            if has_values:
                streams[key] = downsample(d)

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
        'stream_meta': stream_meta,
        'power_vs_hr': pvh or {},
        'intervals': ivs or {},
        'power_curve': pcurve or {},
        'power_histogram': phist or [],
        'hr_histogram': hhist or [],
        'meta': {
            'custom_field_count': len(custom_fields),
            'stream_count': len(stream_meta),
            'has_nirs': any(m['type'] in ('smo2', 'thb', 'O2Hb', 'HHb', 'DiffHb') for m in stream_meta),
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
