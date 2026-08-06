"""Tab Atividades — lista completa, ordenavel e filtravel."""

from flask import jsonify, request
from api_client import fetch_activities, norm_tipo, num, kj_da_atividade
from config import CORES_MOD
from tabs.base import page

SLUG = 'atividades'
ROUTE = '/atividades'


def api_data():
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'Fetch failed'}), 500

    out = []
    for a in acts:
        secs = num(a.get('elapsed_time')) or num(a.get('moving_time'))
        dist = num(a.get('icu_distance')) or num(a.get('distance'))
        raw = a.get('type')
        out.append({
            'id': a.get('id'), 'date': (a.get('start_date_local') or '')[:10],
            'name': a.get('name') or 'Sem nome',
            'type': norm_tipo(raw), 'type_raw': raw,
            'duration_min': round(secs / 60.0, 1),
            'distance_km': round(dist / 1000.0, 1),
            'kj': round(kj_da_atividade(a), 0),
            'training_load': num(a.get('icu_training_load')),
            'avg_watts': num(a.get('icu_weighted_avg_watts')) or num(a.get('icu_average_watts')),
            'ftp': num(a.get('icu_pm_ftp')) or num(a.get('icu_ftp')),
            'rpe': a.get('icu_rpe'), 'xss': round(num(a.get('SS')), 0),
            'avg_hr': num(a.get('average_heartrate')), 'max_hr': num(a.get('max_heartrate')),
            'source': a.get('source'),
            # tags vem null quando nao ha nenhuma, e lista de strings quando ha
            'tags': [t for t in (a.get('tags') or []) if t],
        })

    limit = request.args.get('limit', type=int)
    atype = request.args.get('type', type=str)
    if atype:
        out = [r for r in out if r['type'] == atype]
    if limit:
        out = out[:limit]
    todas_tags = sorted({t for r in out for t in r['tags']})
    return jsonify({'status': 'OK', 'total': len(out), 'cores': CORES_MOD,
                    'tags': todas_tags, 'activities': out})


BODY = r"""
<h1>Atividades</h1>
<div class="sub" id="sub">A carregar...</div>
<div class="controls">
  <input id="search" placeholder="Procurar nome...">
  <select id="typeFilter"><option value="">Todos os tipos</option></select>
  <select id="tagFilter"><option value="">Todas as tags</option></select>
  <label class="sel">De <input id="dtDe" type="date" style="min-width:auto"></label>
  <label class="sel">Ate <input id="dtAte" type="date" style="min-width:auto"></label>
  <a href="/api/atividades" target="_blank">JSON</a>
</div>
<div class="count" id="count"></div>
<div class="wrap"><table>
  <thead><tr id="head"></tr></thead>
  <tbody id="body"><tr><td class="loading">A carregar...</td></tr></tbody>
</table></div>
"""

JS = r"""
const COLS=[['date','Data',0],['name','Nome',0],['type','Tipo',0],['type_raw','Tipo API',0],
 ['duration_min','Min',1],['distance_km','km',1],['kj','kJ',1],['training_load','TL',1],
 ['avg_watts','W',1],['ftp','FTP',1],['rpe','RPE',1],['xss','XSS',1],
 ['avg_hr','HR',1],['max_hr','HR max',1],['tags','Tags',0],['source','Fonte',0]];
let data=[],CORES={},sortKey='date',sortAsc=false;

function fmt(v,num){if(v===null||v===undefined||v==='')return '-';
 if(num&&typeof v==='number')return v.toLocaleString('pt-PT');return v;}

function filtrados(){
 const q=document.getElementById('search').value.toLowerCase();
 const t=document.getElementById('typeFilter').value;
 const de=document.getElementById('dtDe').value;
 const ate=document.getElementById('dtAte').value;
 const tag=document.getElementById('tagFilter').value;
 return data.filter(r=>(!q||(r.name||'').toLowerCase().indexOf(q)!==-1)
  &&(!t||r.type===t)&&(!de||r.date>=de)&&(!ate||r.date<=ate)
  &&(!tag||(r.tags||[]).indexOf(tag)!==-1));
}

function render(){
 let rows=filtrados();
 rows.sort(function(a,b){var x=a[sortKey],y=b[sortKey];
  if(Array.isArray(x))x=x.join(',');
  if(Array.isArray(y))y=y.join(',');
  if(typeof x==='number')return sortAsc?x-y:y-x;
  x=String(x||'');y=String(y||'');return sortAsc?x.localeCompare(y):y.localeCompare(x);});
 document.getElementById('count').textContent=rows.length+' de '+data.length+' atividades';
 document.getElementById('body').innerHTML=rows.map(r=>
  '<tr class="clickable" onclick="location.href=\'/activity/'+r.id+'\'">'+
  COLS.map(function(c){
   let v;
   if(c[0]==='tags'){
    v=(r.tags&&r.tags.length)
      ? r.tags.map(t=>'<span class="pill">'+t+'</span>').join('') : '-';
   } else {
    v=fmt(r[c[0]],c[2]);
    if(c[0]==='type')v='<span style="color:'+(CORES[r.type]||'#8b949e')+'">'+v+'</span>';
   }
   return '<td class="'+(c[2]?'num':'')+'">'+v+'</td>';}).join('')+'</tr>').join('');
}

function buildHead(){
 document.getElementById('head').innerHTML=COLS.map(c=>
  '<th class="'+(c[2]?'num':'')+'" data-k="'+c[0]+'">'+c[1]+'</th>').join('');
 document.querySelectorAll('th').forEach(th=>th.onclick=function(){
  var k=th.dataset.k;if(sortKey===k)sortAsc=!sortAsc;else{sortKey=k;sortAsc=false;}render();});
}

async function load(){
 buildHead();
 const d=await fetch('/api/atividades').then(r=>r.json());
 if(d.error){document.getElementById('sub').textContent='Erro: '+d.error;return;}
 data=d.activities||[];CORES=d.cores||{};
 const datas=data.map(r=>r.date).filter(Boolean).sort();
 document.getElementById('sub').textContent=
  data.length+' sessoes de '+(datas[0]||'?')+' a '+(datas[datas.length-1]||'?')+
  ' - clica numa linha para ver o detalhe';
 const types=Array.from(new Set(data.map(r=>r.type))).sort();
 document.getElementById('typeFilter').innerHTML='<option value="">Todos os tipos</option>'+
  types.map(t=>'<option>'+t+'</option>').join('');
 const tags=d.tags||[];
 const selTag=document.getElementById('tagFilter');
 if(tags.length){
  selTag.innerHTML='<option value="">Todas as tags ('+tags.length+')</option>'+
   tags.map(t=>'<option>'+t+'</option>').join('');
 } else {
  selTag.innerHTML='<option value="">Sem tags</option>';
  selTag.disabled=true;
 }
 render();
}
['search','dtDe','dtAte'].forEach(id=>document.getElementById(id).oninput=render);
['typeFilter','tagFilter'].forEach(id=>document.getElementById(id).onchange=render);
load();
"""


def render():
    return page('Atividades', SLUG, BODY, JS)
