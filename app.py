#!/usr/bin/env python3
"""Intervals.icu API Proxy + Dashboard HTML"""

import os
import sys
import logging
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template_string
from dotenv import load_dotenv

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

_cache = {'activities': None, 'time': None}


def fetch_activities():
    import requests
    now = datetime.now()
    if _cache['activities'] and _cache['time']:
        if (now - _cache['time']).total_seconds() < 300:
            return _cache['activities']

    oldest = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    url = f"https://API_KEY:{API_KEY}@intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities"

    try:
        resp = requests.get(url, params={"oldest": oldest}, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        acts = result if isinstance(result, list) else result.get("data", [])
        _cache['activities'] = acts
        _cache['time'] = now
        return acts
    except Exception as e:
        print(f"Fetch error: {e}")
        return None


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


DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Intervals.icu Dashboard</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; padding:24px; background:#0e1117; color:#e6e6e6;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  h1 { font-size:22px; margin:0 0 4px; font-weight:600; }
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
  tr:hover td { background:#161b22; }
  .num { text-align:right; font-variant-numeric:tabular-nums; }
  .wrap { max-height:70vh; overflow:auto; border:1px solid #30363d; border-radius:8px; }
  .loading { color:#8b949e; padding:40px; text-align:center; }
  .count { color:#8b949e; font-size:12px; margin-bottom:8px; }
  a.dl { color:#5DADE2; font-size:13px; text-decoration:none; }
</style>
</head>
<body>
<h1>Intervals.icu Dashboard</h1>
<div class="sub">Athlete Susigan &middot; ultimos 365 dias</div>

<div class="cards" id="cards"></div>

<div class="controls">
  <input id="search" placeholder="Procurar nome...">
  <select id="typeFilter"><option value="">Todos os tipos</option></select>
  <a class="dl" href="/api/activities" target="_blank">JSON bruto</a>
</div>

<div class="count" id="count"></div>
<div class="wrap">
  <table>
    <thead><tr id="head"></tr></thead>
    <tbody id="body"><tr><td class="loading">A carregar...</td></tr></tbody>
  </table>
</div>

<script>
const COLS = [
  ['date','Data',0],['name','Nome',0],['type','Tipo',0],
  ['duration_min','Min',1],['distance_km','km',1],
  ['training_load','TL',1],['avg_watts','W',1],
  ['ftp','FTP',1],['avg_hr','HR',1],['max_hr','HR max',1],
  ['joules','Joules',1],['source','Fonte',0]
];
let data = [], sortKey = 'date', sortAsc = false;

function fmt(v, num) {
  if (v === null || v === undefined || v === '') return '-';
  if (num && typeof v === 'number') return v.toLocaleString('pt-PT');
  return v;
}

function render() {
  const q = document.getElementById('search').value.toLowerCase();
  const t = document.getElementById('typeFilter').value;
  let rows = data.filter(function(r){
    return (!q || (r.name||'').toLowerCase().indexOf(q) !== -1) && (!t || r.type === t);
  });
  rows.sort(function(a,b){
    var x = a[sortKey], y = b[sortKey];
    if (typeof x === 'number') return sortAsc ? x-y : y-x;
    x = String(x||''); y = String(y||'');
    return sortAsc ? x.localeCompare(y) : y.localeCompare(x);
  });
  document.getElementById('count').textContent = rows.length + ' de ' + data.length + ' atividades';
  document.getElementById('body').innerHTML = rows.map(function(r){
    return '<tr>' + COLS.map(function(c){
      return '<td class="' + (c[2]?'num':'') + '">' + fmt(r[c[0]], c[2]) + '</td>';
    }).join('') + '</tr>';
  }).join('');
}

function buildHead() {
  document.getElementById('head').innerHTML = COLS.map(function(c){
    return '<th class="' + (c[2]?'num':'') + '" data-k="' + c[0] + '">' + c[1] + '</th>';
  }).join('');
  document.querySelectorAll('th').forEach(function(th){
    th.onclick = function(){
      var k = th.dataset.k;
      if (sortKey === k) sortAsc = !sortAsc; else { sortKey = k; sortAsc = false; }
      render();
    };
  });
}

async function load() {
  buildHead();
  const res = await Promise.all([
    fetch('/api/stats').then(function(r){return r.json();}),
    fetch('/api/activities').then(function(r){return r.json();})
  ]);
  const s = res[0], a = res[1];
  document.getElementById('cards').innerHTML = [
    ['Atividades', s.total_activities],
    ['TL total', s.training_total_tl],
    ['TL medio', s.training_avg_tl],
    ['Distancia', Math.round(s.distance_total_km) + ' km'],
    ['Duracao media', s.duration_avg_min + ' min'],
    ['Watts medio', s.training_avg_watts + ' W'],
    ['HR medio', s.hr_avg + ' bpm'],
    ['Com potencia', s.coverage_with_power]
  ].map(function(c){
    var v = c[1];
    return '<div class="card"><div class="label">'+c[0]+'</div><div class="value">'+
      (typeof v==='number'?v.toLocaleString('pt-PT'):v)+'</div></div>';
  }).join('');

  data = a.activities || [];
  var types = Array.from(new Set(data.map(function(r){return r.type;}))).sort();
  document.getElementById('typeFilter').innerHTML =
    '<option value="">Todos os tipos</option>' +
    types.map(function(t){return '<option>'+t+'</option>';}).join('');
  render();
}

document.getElementById('search').oninput = render;
document.getElementById('typeFilter').onchange = render;
load();
</script>
</body>
</html>
"""


@app.route('/', methods=['GET'])
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200


@app.route('/api/activities', methods=['GET'])
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

    return jsonify({
        'status': 'OK',
        'total': len(acts),
        'returned': len(processed),
        'activities': processed,
    })


@app.route('/api/activities/<activity_id>', methods=['GET'])
def api_activity_detail(activity_id):
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'Fetch failed'}), 500

    act = None
    for a in acts:
        if a.get('id') == activity_id:
            act = a
            break
    if not act:
        return jsonify({'error': 'Not found'}), 404

    p = ActivityProcessor()
    return jsonify({
        'status': 'OK',
        'activity': {
            'id': p.get_activity_id(act),
            'date': p.get_start_date_local(act),
            'name': p.get_activity_name(act),
            'type': p.get_activity_type(act),
            'duration_sec': p.get_duration_seconds(act),
            'distance_km': round(p.get_distance_km(act), 2),
            'ftp': p.get_ftp(act),
            'avg_watts': p.get_avg_watts(act),
            'joules': p.get_joules(act),
            'training_load': p.get_training_load(act),
            'avg_hr': p.get_avg_hr(act),
            'max_hr': p.get_max_hr(act),
            'elevation_gain': round(p.get_elevation_gain_m(act), 0),
        }
    })


@app.route('/api/stats', methods=['GET'])
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


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
