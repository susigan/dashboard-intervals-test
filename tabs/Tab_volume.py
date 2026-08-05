"""Tab Volume & Carga — agregados por periodo e modalidade."""

from flask import jsonify
from api_client import fetch_activities, norm_tipo, num, kj_da_atividade, classificar_rpe
from config import CICLICOS, CORES_MOD, ANOS_HISTORICO
from tabs.base import page

SLUG = 'volume'
ROUTE = '/'


def api_data():
    """Uma linha por sessao, com tudo o que os graficos precisam."""
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'Fetch failed'}), 500

    rows = []
    for a in acts:
        d = (a.get('start_date_local') or '')[:10]
        if len(d) != 10:
            continue
        secs = num(a.get('elapsed_time')) or num(a.get('moving_time'))
        dist = num(a.get('icu_distance')) or num(a.get('distance'))
        rows.append({
            'id': a.get('id'), 'date': d,
            'type': norm_tipo(a.get('type')), 'type_raw': a.get('type'),
            'horas': secs / 3600.0,
            'km': dist / 1000.0,
            'kj': kj_da_atividade(a),
            'kj_acima_ftp': num(a.get('icu_joules_above_ftp')) / 1000.0,
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
            'elev': num(a.get('total_elevation_gain')),
        })

    tipos_raw = {}
    for r in rows:
        tipos_raw.setdefault(r['type'], set()).add(r['type_raw'])
    datas = sorted(r['date'] for r in rows)

    return jsonify({
        'status': 'OK', 'count': len(rows), 'sessions': rows,
        'ciclicos': CICLICOS, 'cores': CORES_MOD,
        'periodo': {'de': datas[0] if datas else None,
                    'ate': datas[-1] if datas else None,
                    'anos': ANOS_HISTORICO},
        'type_mapping': {k: sorted(v) for k, v in sorted(tipos_raw.items())},
        'kj_fonte': 'icu_joules (integral do stream de potencia), fallback Z1+Z2+Z3 KJ',
    })


BODY = r"""
<h1>Volume &amp; Carga</h1>
<div class="sub" id="sub">A carregar...</div>

<div class="controls">
  <label class="sel">Periodo
    <select id="periodo">
      <option value="semana">Semana</option>
      <option value="mes" selected>Mes</option>
      <option value="ano">Ano</option>
    </select></label>
  <label class="sel">Vista
    <select id="modo">
      <option value="abs">Absolutos</option>
      <option value="pct">Percentagem</option>
    </select></label>
  <label class="sel">Ultimos
    <select id="janela">
      <option value="12">12</option><option value="26">26</option>
      <option value="52" selected>52</option><option value="0">Tudo</option>
    </select></label>
</div>
<div class="sub" id="mapping" style="margin-bottom:16px"></div>

<div class="cards" id="kpis"></div>

<h2>Horas por modalidade</h2>
<div class="chartbox"><div class="legend" id="lgHoras"></div><canvas id="chHoras"></canvas></div>

<h2>Distancia (km)</h2>
<div class="chartbox"><div class="legend" id="lgKm"></div><canvas id="chKm"></canvas></div>

<h2>Trabalho (kJ)</h2>
<div class="sub">Fonte: <code>icu_joules</code> &mdash; o integral do stream de potencia calculado pela Intervals.icu</div>
<div class="chartbox"><div class="legend" id="lgKj"></div><canvas id="chKj"></canvas></div>

<h2>Trabalho por zona</h2>
<div class="controls"><label class="sel">Modalidade
  <select id="modZona"><option value="">Todas</option></select></label></div>
<div class="chartbox"><div class="legend" id="lgZona"></div><canvas id="chZona"></canvas></div>

<h2>Horas por RPE</h2>
<div class="sub">Leve 1&ndash;4.9 &middot; Moderado 5&ndash;6.9 &middot; Pesado 7&ndash;10</div>
<div class="chartbox"><div class="legend" id="lgRpe"></div><canvas id="chRpe"></canvas></div>

<h2>Strain Score (XSS)</h2>
<div class="sub" id="subXss">Outliers removidos por modalidade (IQR &times; 3)</div>
<div class="chartbox"><div class="legend" id="lgXss"></div><canvas id="chXss"></canvas></div>

<h2>Training Load</h2>
<div class="chartbox"><div class="legend" id="lgTl"></div><canvas id="chTl"></canvas></div>

<h2>Sistema energetico</h2>
<div class="sub">oxidative = Aerobic &middot; glycolytic = Glycolytic &middot; sprint = Pmax</div>
<div class="chartbox"><div class="legend" id="lgSys"></div><canvas id="chSys"></canvas></div>

<h2>Tabela resumo</h2>
<div class="controls"><label class="sel">Metrica
  <select id="tblMetric">
    <option value="horas">Horas</option><option value="km">km</option>
    <option value="kj">kJ</option><option value="tl">Training Load</option>
    <option value="xss">XSS</option><option value="elev">Elevacao</option>
  </select></label></div>
<div class="wrap" style="max-height:420px"><table>
  <thead><tr id="tblHead"></tr></thead><tbody id="tblBody"></tbody></table></div>
"""

JS = r"""
let SESS=[],CIC=[],CORES={};
const RPECOR={Leve:'#58D68D',Moderado:'#F4D03F',Pesado:'#E74C3C'};
const ZCOR={z1_kj:'#58D68D',z2_kj:'#F4D03F',z3_kj:'#E74C3C'};
const ZLBL={z1_kj:'Z1',z2_kj:'Z2',z3_kj:'Z3'};
const SYSCOR={aerobic:'#5DADE2',glycolytic:'#F39C12',sprint:'#E74C3C'};

function janela(data){
 const n=parseInt(document.getElementById('janela').value,10);
 return (n>0&&data.length>n)?data.slice(-n):data;
}
function opts(extra){
 return Object.assign({pct:document.getElementById('modo').value==='pct'},extra||{});
}

function redraw(){
 const per=document.getElementById('periodo').value;
 const cic=SESS.filter(r=>CIC.indexOf(r.type)!==-1);

 drawStack('chHoras','lgHoras',janela(pivot(cic,per,'type','horas',CIC)),CIC,CORES,opts({unit:'h',decimals:1}));
 drawStack('chKm','lgKm',janela(pivot(cic.filter(r=>r.km>0),per,'type','km',CIC)),CIC,CORES,opts({unit:'km'}));
 drawStack('chKj','lgKj',janela(pivot(cic.filter(r=>r.kj>0),per,'type','kj',CIC)),CIC,CORES,opts({unit:'kJ'}));

 const mz=document.getElementById('modZona').value;
 const zrows=mz?cic.filter(r=>r.type===mz):cic;
 drawStack('chZona','lgZona',janela(pivotCols(zrows,per,['z1_kj','z2_kj','z3_kj'])),
  ['z1_kj','z2_kj','z3_kj'],ZCOR,opts({unit:'kJ',labels:ZLBL}));

 drawStack('chRpe','lgRpe',janela(pivot(cic.filter(r=>r.rpe_cat),per,'rpe_cat','horas',
  ['Leve','Moderado','Pesado'])),['Leve','Moderado','Pesado'],RPECOR,opts({unit:'h',decimals:1}));

 const xssClean=limparOutliers(cic,'xss',3);
 drawStack('chXss','lgXss',janela(pivot(xssClean.filter(r=>r.xss>0),per,'type','xss',CIC)),CIC,CORES,opts());
 const nout=window.__NOUT__||0;
 document.getElementById('subXss').textContent=
  'Outliers removidos por modalidade (IQR x 3) - '+nout+' sessoes excluidas';

 drawStack('chTl','lgTl',janela(pivot(cic.filter(r=>r.tl>0),per,'type','tl',CIC)),CIC,CORES,opts());
 drawStack('chSys','lgSys',janela(pivotCols(cic,per,['aerobic','glycolytic','sprint'])),
  ['aerobic','glycolytic','sprint'],SYSCOR,opts());

 const ph=janela(pivot(cic,per,'type','horas',CIC));
 const pk=janela(pivot(cic.filter(r=>r.kj>0),per,'type','kj',CIC));
 const pd=janela(pivot(cic.filter(r=>r.km>0),per,'type','km',CIC));
 const pt=janela(pivot(cic.filter(r=>r.tl>0),per,'type','tl',CIC));
 function tot(arr,i){if(!arr.length)return 0;const d=arr[arr.length+i];
  return d?CIC.reduce((s,k)=>s+(d.vals[k]||0),0):0;}
 function delta(cur,prev){if(!prev)return '';
  const p=(cur-prev)/prev*100,col=p>=0?'#2ECC71':'#E74C3C';
  return '<span style="color:'+col+';font-size:11px"> '+(p>=0?'+':'')+p.toFixed(0)+'%</span>';}
 document.getElementById('kpis').innerHTML=[
  ['Horas',fmtH(tot(ph,-1)),delta(tot(ph,-1),tot(ph,-2))],
  ['km',Math.round(tot(pd,-1)),delta(tot(pd,-1),tot(pd,-2))],
  ['kJ',Math.round(tot(pk,-1)).toLocaleString('pt-PT'),delta(tot(pk,-1),tot(pk,-2))],
  ['Training Load',Math.round(tot(pt,-1)),delta(tot(pt,-1),tot(pt,-2))],
  ['Sessoes',cic.length,''],
  ['Modalidades',CIC.filter(m=>cic.some(r=>r.type===m)).length,'']
 ].map(k=>'<div class="card"><div class="label">'+k[0]+'</div><div class="value">'+
  k[1]+k[2]+'</div></div>').join('');

 tabela(per,cic);
}

function tabela(per,cic){
 const metric=document.getElementById('tblMetric').value;
 const rows=janela(pivot(cic.filter(r=>isFinite(r[metric])),per,'type',metric,CIC));
 document.getElementById('tblHead').innerHTML=
  ['Periodo'].concat(CIC).concat(['Total']).map((c,i)=>
   '<th class="'+(i?'num':'')+'">'+c+'</th>').join('');
 const dec=metric==='horas'?1:0;
 document.getElementById('tblBody').innerHTML=rows.slice().reverse().map(function(d){
  const tot=CIC.reduce((s,k)=>s+(d.vals[k]||0),0);
  return '<tr><td>'+d.periodo+'</td>'+CIC.map(function(k){
   const v=d.vals[k]||0,pct=tot?(v/tot*100).toFixed(0):0;
   return '<td class="num">'+(v?v.toFixed(dec)+
    ' <span style="color:#8b949e;font-size:11px">'+pct+'%</span>':'-')+'</td>';
  }).join('')+'<td class="num"><b>'+tot.toFixed(dec)+'</b></td></tr>';}).join('');
}

async function load(){
 const d=await fetch('/api/volume').then(r=>r.json());
 if(d.error){document.getElementById('sub').textContent='Erro: '+d.error;return;}
 SESS=d.sessions||[];CIC=d.ciclicos||[];CORES=d.cores||{};
 const p=d.periodo||{};
 document.getElementById('sub').textContent=
  d.count+' sessoes de '+(p.de||'?')+' a '+(p.ate||'?');
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
"""


def render():
    return page('Volume & Carga', SLUG, BODY, JS)
