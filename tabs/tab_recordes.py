"""Tab Recordes — melhores esforcos por duracao, calculados do historico.

Substitui os custom fields MMP ("No (PR: 302w)"): aqui temos o valor, a data,
o recorde anterior e a progressao ao longo dos anos.
"""

from flask import jsonify, request
import db
from config import CICLICOS, CORES_MOD
from tabs.base import page

SLUG = 'recordes'
ROUTE = '/recordes'


def api_data():
    tipo = request.args.get('tipo') or None
    desde = request.args.get('desde') or None
    r = db.calcular_recordes(tipo, desde)

    tipos = db._exec("SELECT DISTINCT type FROM power_curves ORDER BY type",
                     fetch='all') if db.ENABLED else []
    r['tipos_disponiveis'] = [t[0] for t in (tipos or [])]
    r['cores'] = CORES_MOD
    r['tipo'] = tipo
    return jsonify(r)


BODY = r"""
<h1>Recordes</h1>
<div class="sub" id="sub">A carregar...</div>

<div class="controls">
  <label class="sel">Modalidade <select id="tipo"></select></label>
  <label class="sel">Desde <input id="desde" type="date" style="min-width:auto"></label>
  <label class="sel">Duracao no grafico <select id="dur"></select></label>
</div>

<div class="cards" id="kpis"></div>

<h2>Curva de melhores esforcos</h2>
<div class="sub">Escala logaritmica. Cada ponto e o melhor de sempre para essa duracao.</div>
<div class="chartbox"><div class="legend" id="lgCurva"></div><canvas id="chCurva"></canvas></div>

<h2>Progressao do recorde</h2>
<div class="sub" id="subProg">Cada degrau e uma sessao que bateu o anterior.</div>
<div class="chartbox"><div class="legend" id="lgProg"></div><canvas id="chProg"></canvas></div>

<h2>Melhores de sempre</h2>
<div class="wrap" style="max-height:520px"><table>
  <thead><tr>
    <th>Duracao</th><th class="num">Watts</th><th class="num">W/kg</th>
    <th>Data</th><th>Sessao</th><th class="num">Anterior</th>
    <th class="num">Ganho</th><th>Recorde anterior de</th>
  </tr></thead>
  <tbody id="tbl"><tr><td class="loading">A carregar...</td></tr></tbody>
</table></div>
"""

JS = r"""
let R=null,PESO=null;
function fmtD(s){
 if(s<60)return s+'s';
 if(s<3600){const m=s/60;return (m%1?m.toFixed(1):m)+'min';}
 const h=s/3600;return (h%1?h.toFixed(1):h)+'h';
}

function drawCurva(){
 const o=ctx('chCurva',280); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 const durs=R.duracoes||[];
 if(!durs.length){noData(g,W,H,'Sem curvas guardadas. Corre /api/sync/curvas');return;}
 const PL=54,PR=18,PT=12,PB=30,w=W-PL-PR,h=H-PT-PB;
 const vals=durs.map(s=>R.melhores[s].watts);
 const mx=Math.max.apply(null,vals),mn=0;
 const lmin=Math.log10(durs[0]),lmax=Math.log10(durs[durs.length-1]);
 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 g.strokeStyle='#5DADE2';g.lineWidth=1.8;g.beginPath();
 durs.forEach(function(s,i){
  const x=PL+w*(Math.log10(s)-lmin)/(lmax-lmin||1);
  const y=PT+h-(R.melhores[s].watts-mn)/(mx-mn||1)*h;
  i?g.lineTo(x,y):g.moveTo(x,y);});
 g.stroke();
 g.fillStyle='#5DADE2';
 durs.forEach(function(s){
  const x=PL+w*(Math.log10(s)-lmin)/(lmax-lmin||1);
  const y=PT+h-(R.melhores[s].watts-mn)/(mx-mn||1)*h;
  g.beginPath();g.arc(x,y,2.5,0,6.29);g.fill();});
 g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++)g.fillText(Math.round(mx-(mx-mn)*i/4)+'W',PL-6,PT+h*i/4+3);
 g.fillStyle='#8b949e';g.textAlign='center';
 [1,5,30,60,300,1200,3600].forEach(function(s){
  if(s<durs[0]||s>durs[durs.length-1])return;
  const x=PL+w*(Math.log10(s)-lmin)/(lmax-lmin||1);
  g.fillText(fmtD(s),x,H-8);
  g.strokeStyle='#21262d';g.beginPath();g.moveTo(x,PT);g.lineTo(x,PT+h);g.stroke();});
 g.textAlign='left';
 document.getElementById('lgCurva').innerHTML=
  '<span><i style="background:#5DADE2"></i>Melhor de sempre</span>'+
  '<span>'+durs.length+' duracoes</span>';
}

function drawProg(){
 const o=ctx('chProg',260); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 const s=parseInt(document.getElementById('dur').value,10);
 const hist=(R.progressao||{})[s]||[];
 document.getElementById('lgProg').innerHTML=
  '<span><i style="background:#F4D03F"></i>'+fmtD(s)+'</span><span>'+
  hist.length+' recordes</span>';
 if(hist.length<1){noData(g,W,H,'Sem progressao para esta duracao');return;}
 const PL=54,PR=18,PT=12,PB=30,w=W-PL-PR,h=H-PT-PB;
 const t0=new Date(hist[0].date).getTime();
 const t1=Date.now();
 const vals=hist.map(x=>x.watts);
 const mx=Math.max.apply(null,vals),mn=Math.min.apply(null,vals)*0.92;
 g.strokeStyle='#21262d';
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 const X=d=>PL+w*((new Date(d).getTime()-t0)/((t1-t0)||1));
 const Y=v=>PT+h-(v-mn)/((mx-mn)||1)*h;
 // degraus
 g.strokeStyle='#F4D03F';g.lineWidth=1.8;g.beginPath();
 hist.forEach(function(p,i){
  const x=X(p.date),y=Y(p.watts);
  if(i===0){g.moveTo(x,y);}else{g.lineTo(x,Y(hist[i-1].watts));g.lineTo(x,y);}
  if(i===hist.length-1){g.lineTo(PL+w,y);}});
 g.stroke();
 g.fillStyle='#F4D03F';
 hist.forEach(function(p){g.beginPath();g.arc(X(p.date),Y(p.watts),3,0,6.29);g.fill();});
 g.font='10px sans-serif';g.textAlign='right';g.fillStyle='#8b949e';
 for(let i=0;i<=4;i++)g.fillText(Math.round(mx-(mx-mn)*i/4)+'W',PL-6,PT+h*i/4+3);
 g.textAlign='center';
 for(let i=0;i<=5;i++){
  const t=t0+(t1-t0)*i/5;
  g.fillText(new Date(t).toISOString().slice(0,7),PL+w*i/5,H-8);}
 g.textAlign='left';
}

function tabela(){
 const durs=R.duracoes||[];
 if(!durs.length){
  document.getElementById('tbl').innerHTML=
   '<tr><td class="loading">Sem dados. Corre <b>/api/sync/curvas</b> primeiro.</td></tr>';
  return;}
 document.getElementById('tbl').innerHTML=durs.map(function(s){
  const m=R.melhores[s];
  const wkg=PESO?(m.watts/PESO).toFixed(2):'-';
  const ganho=m.anterior_watts?('+'+(m.watts-m.anterior_watts).toFixed(0)+'W'):'—';
  return '<tr class="clickable" onclick="location.href=\'/activity/'+m.activity_id+'\'">'+
   '<td><b>'+fmtD(s)+'</b></td>'+
   '<td class="num" style="color:#5DADE2"><b>'+Math.round(m.watts)+'</b></td>'+
   '<td class="num">'+wkg+'</td>'+
   '<td>'+m.date+'</td>'+
   '<td>'+(m.name||m.activity_id)+'</td>'+
   '<td class="num">'+(m.anterior_watts?Math.round(m.anterior_watts):'—')+'</td>'+
   '<td class="num" style="color:#2ECC71">'+ganho+'</td>'+
   '<td>'+(m.anterior_date||'—')+'</td></tr>';
 }).join('');
}

function kpis(){
 const durs=R.duracoes||[];
 const rec=[];
 Object.keys(R.progressao||{}).forEach(s=>(R.progressao[s]||[]).forEach(p=>rec.push(p)));
 rec.sort((a,b)=>b.date.localeCompare(a.date));
 const ultimo=rec[0];
 const ano=new Date().getFullYear();
 const esteAno=rec.filter(p=>p.date.slice(0,4)==String(ano)).length;
 document.getElementById('kpis').innerHTML=[
  ['Sessoes analisadas',R.n_sessoes],
  ['Duracoes',durs.length],
  ['Recordes este ano',esteAno],
  ['Ultimo recorde',ultimo?ultimo.date:'—'],
  ['FTP 20min',durs.indexOf(1200)>=0?Math.round(R.melhores[1200].watts*0.95)+' W':'—'],
  ['Pico 5s',durs.indexOf(5)>=0?Math.round(R.melhores[5].watts)+' W':'—'],
 ].map(k=>'<div class="card"><div class="label">'+k[0]+'</div><div class="value">'+
  k[1]+'</div></div>').join('');
}

async function load(){
 const tipo=document.getElementById('tipo').value;
 const desde=document.getElementById('desde').value;
 const qs=[];
 if(tipo)qs.push('tipo='+tipo);
 if(desde)qs.push('desde='+desde);
 R=await fetch('/api/recordes'+(qs.length?'?'+qs.join('&'):'')).then(r=>r.json());

 const sel=document.getElementById('tipo');
 if(!sel.options.length){
  sel.innerHTML='<option value="">Todas</option>'+
   (R.tipos_disponiveis||[]).map(t=>'<option>'+t+'</option>').join('');
 }
 const p=R.periodo||{};
 document.getElementById('sub').textContent=R.n_sessoes
  ? R.n_sessoes+' sessoes de '+(p.de||'?')+' a '+(p.ate||'?')
  : 'Sem curvas guardadas — abre /api/sync/curvas para as carregar';

 const dsel=document.getElementById('dur');
 const durs=R.duracoes||[];
 if(durs.length&&!dsel.options.length){
  dsel.innerHTML=durs.map(s=>'<option value="'+s+'"'+(s===1200?' selected':'')+'>'+
   fmtD(s)+'</option>').join('');
 }
 const m1200=(R.melhores||{})[1200];
 PESO=null;
 kpis();drawCurva();drawProg();tabela();
}
['tipo','desde'].forEach(id=>document.getElementById(id).onchange=load);
document.getElementById('dur').onchange=drawProg;
window.addEventListener('resize',function(){if(R){drawCurva();drawProg();}});
load();
"""


def render():
    return page('Recordes', SLUG, BODY, JS)
