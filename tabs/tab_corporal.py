"""Tab Composicao Corporal & Nutricao — dados dos Google Sheets."""

from flask import jsonify, request

import corporal
import sheets_client as sheets
from tabs.base import page

SLUG = 'corporal'


def api_data():
    wellness, corp, erros = sheets.carregar()
    linhas = corporal.preparar(corp or [], wellness or [])

    if not linhas:
        return jsonify({'error': 'sem dados corporais',
                        'sheets_ok': sheets.disponivel(),
                        'erros_sheets': erros, 'linhas': []})

    periodo = request.args.get('periodo', 'W')
    if periodo not in ('W', 'M', 'Q'):
        periodo = 'W'

    res = corporal.resumo(linhas, wellness)
    ag = corporal.agrupar(linhas, periodo)
    cal_r7 = res['r7']['calorias']

    return jsonify({
        'status': 'OK',
        'sheets_ok': sheets.disponivel(), 'erros_sheets': erros,
        'linhas': linhas,
        'resumo': {k: v for k, v in res.items() if k != 'r7'},
        'r7': res['r7'],
        'agrupado': ag,
        'periodo': periodo,
        'variacao': {'peso': corporal.variacao_com_bandas(ag, 'peso'),
                     'bf': corporal.variacao_com_bandas(ag, 'bf')},
        'bandas': {k: [{'pct': p, 'rotulo': r, 'cor': c}
                       for p, r, c in v] for k, v in corporal.BANDAS.items()},
        'macros': corporal.macros_percentagem(linhas),
        'lag': {'peso': corporal.lag_calorico(cal_r7, res['r7']['peso']),
                'bf': corporal.lag_calorico(cal_r7, res['r7']['bf'])},
    })


BODY = r"""
<h1>Composicao corporal &amp; nutricao</h1>
<div class="sub" id="sub">A carregar...</div>

<div class="cards" id="kpis"></div>

<h2>Peso, gordura corporal e calorias</h2>
<div class="sub">Media movel de 7 dias — remove o ruido diario de agua e glicogenio</div>
<div class="controls">
  <label class="sel">Janela
    <select id="janela">
      <option value="90" selected>90 dias</option>
      <option value="180">6 meses</option>
      <option value="365">1 ano</option>
      <option value="0">Tudo</option>
    </select></label>
</div>
<div class="chartbox">
  <div class="legend" id="lgPeso"></div>
  <canvas id="chPeso" height="280"></canvas>
</div>

<h2>Calorias e balanco energetico</h2>
<div class="chartbox">
  <div class="legend" id="lgCal"></div>
  <canvas id="chCal" height="240"></canvas>
</div>

<h2>Variacao com bandas de ganho e perda esperados</h2>
<div class="sub" id="subVar"></div>
<div class="controls">
  <label class="sel">Agrupar por
    <select id="periodo">
      <option value="W" selected>Semana</option>
      <option value="M">Mes</option>
      <option value="Q">Trimestre</option>
    </select></label>
  <label class="sel">Variavel
    <select id="varSel">
      <option value="peso" selected>Peso</option>
      <option value="bf">Gordura corporal</option>
    </select></label>
</div>
<div class="chartbox">
  <div class="legend" id="lgVar"></div>
  <canvas id="chVar" height="260"></canvas>
</div>

<h2>Macronutrientes</h2>
<div class="sub">Reparticao energetica calculada das gramas: carb e ptn a 4 kcal/g, gordura a 9</div>
<div class="controls">
  <label class="sel">Ver
    <select id="macroModo">
      <option value="pct" selected>Percentagem</option>
      <option value="g">Gramas</option>
    </select></label>
</div>
<div class="chartbox">
  <div class="legend" id="lgMacro"></div>
  <canvas id="chMacro" height="240"></canvas>
</div>

<h2>Lag calorico</h2>
<div class="sub">Ao fim de quantos dias uma mudanca nas calorias se reflecte no peso e na gordura.
  Correlacao de Spearman; so contam desfasamentos com p &lt; 0.10.</div>
<div class="wrap" style="max-height:300px"><table>
  <thead><tr id="lagHead"></tr></thead><tbody id="lagBody"></tbody></table></div>

<div class="sub" style="margin-top:20px">
  <a href="/api/corporal" target="_blank">JSON</a> &middot;
  <a href="/api/debug/sheets" target="_blank">Diagnostico dos Sheets</a>
</div>
"""

JS = r"""
let C=null;
const CORC={peso:'#27ae60',bf:'#2980b9',calorias:'#F4D03F',net:'#48C9B0',
 carb:'#58D68D',ptn:'#AF7AC5',fat:'#E67E22'};
const LBLC={peso:'Peso (kg)',bf:'Gordura (%)',calorias:'Calorias',net:'Net',
 carb:'Carboidratos',ptn:'Proteina',fat:'Gordura alimentar'};

function jan(arr){
 const n=parseInt(document.getElementById('janela').value,10);
 return (n>0&&arr.length>n)?arr.slice(-n):arr;
}

// series com eixo proprio, para peso (kg) e gordura (%) coexistirem
function linhas(canvasId,legendId,dados,series,cores,labels,altura){
 const o=ctx(canvasId,altura||260); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 const PL=52,PR=52,PT=12,PB=24,w=W-PL-PR,h=H-PT-PB,n=dados.length;
 if(!n){noData(g,W,H);return;}
 const X=i=>PL+w*(n>1?i/(n-1):0.5);
 const vis=series.filter(s=>dados.some(d=>d[s]!=null));
 if(legendId)document.getElementById(legendId).innerHTML=vis.map(s=>
  '<span><i style="background:'+cores[s]+'"></i>'+(labels[s]||s)+'</span>').join('');
 if(!vis.length){noData(g,W,H);return;}

 const lim={};
 vis.forEach(function(s){
  let a=Infinity,b=-Infinity;
  dados.forEach(function(d){const v=d[s];if(v==null)return;
   if(v<a)a=v;if(v>b)b=v;});
  if(!isFinite(a)){a=0;b=1;}
  const marg=(b-a)*0.08||1;
  lim[s]=[a-marg,b+marg];});

 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}

 vis.forEach(function(s){
  const[a,b]=lim[s];
  g.strokeStyle=cores[s];g.lineWidth=2;g.beginPath();let st=false;
  dados.forEach(function(d,i){const v=d[s];if(v==null){st=false;return;}
   const y=PT+h-(v-a)/(b-a)*h;
   if(!st){g.moveTo(X(i),y);st=true;}else g.lineTo(X(i),y);});
  g.stroke();});

 g.font='10px sans-serif';
 vis.slice(0,2).forEach(function(s,idx){
  const[a,b]=lim[s],dir=idx===1;
  g.fillStyle=cores[s];g.textAlign=dir?'left':'right';
  for(let i=0;i<=4;i++){const v=b-(b-a)*i/4;
   g.fillText(v>=1000?Math.round(v):v.toFixed(1),dir?PL+w+6:PL-6,PT+h*i/4+3);}});
 g.fillStyle='#8b949e';g.textAlign='center';
 const step=Math.ceil(n/8);
 dados.forEach(function(d,i){if(i%step!==0)return;
  g.fillText((d.date||d.periodo||'').slice(0,7),X(i),H-8);});
 g.textAlign='left';

 registarTip(canvasId,function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  if(x<PL||x>PL+w)return '';
  const i=Math.round((x-PL)/w*(n-1));
  if(i<0||i>=n)return '';
  const d=dados[i];
  let html='<div class="th">'+(d.date||d.periodo)+'</div>';
  vis.forEach(function(s){if(d[s]==null)return;
   html+=linhaTip(cores[s],labels[s]||s,
    d[s]>=1000?Math.round(d[s]).toLocaleString('pt-PT'):d[s].toFixed(1));});
  return html;});
}

function juntarR7(campos){
 const idx={};
 campos.forEach(function(c){
  (C.r7[c]||[]).forEach(function(r){
   idx[r.date]=idx[r.date]||{date:r.date};
   idx[r.date][c]=r.valor;});});
 return Object.keys(idx).sort().map(k=>idx[k]);
}

function drawPeso(){ linhas('chPeso','lgPeso',jan(juntarR7(['peso','bf'])),
 ['peso','bf'],CORC,LBLC,280); }
function drawCal(){ linhas('chCal','lgCal',jan(juntarR7(['calorias','net'])),
 ['calorias','net'],CORC,LBLC,240); }

// barras de variacao com as bandas de referencia por cima
function drawVar(){
 const campo=document.getElementById('varSel').value;
 const dados=C.variacao[campo]||[];
 const o=ctx('chVar',260); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 const PL=54,PR=16,PT=12,PB=30,w=W-PL-PR,h=H-PT-PB,n=dados.length;
 if(!n){noData(g,W,H,'Sem dados suficientes');return;}
 const bandas=C.bandas[campo]||[];
 const rotulos=bandas.map(b=>b.rotulo);

 let mn=0,mx=0;
 dados.forEach(function(d){
  mn=Math.min(mn,d.delta);mx=Math.max(mx,d.delta);
  rotulos.forEach(function(r){if(d[r]!=null){mn=Math.min(mn,d[r]);mx=Math.max(mx,d[r]);}});});
 const marg=(mx-mn)*0.12||0.1; mn-=marg; mx+=marg;
 const Y=v=>PT+h-(v-mn)/(mx-mn)*h;
 const X=i=>PL+w*(i+0.5)/n;

 g.strokeStyle='#21262d';
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 g.strokeStyle='#8b949e';g.lineWidth=1;g.beginPath();
 g.moveTo(PL,Y(0));g.lineTo(PL+w,Y(0));g.stroke();

 const bw=Math.max(2,w/n*0.55);
 dados.forEach(function(d,i){
  const y0=Y(0),y1=Y(d.delta);
  g.fillStyle=d.delta>=0?CORC[campo]:'#E74C3C';
  g.globalAlpha=0.8;
  g.fillRect(X(i)-bw/2,Math.min(y0,y1),bw,Math.abs(y1-y0));
  g.globalAlpha=1;});

 bandas.forEach(function(b){
  g.strokeStyle=b.cor;g.lineWidth=1.4;
  g.setLineDash(Math.abs(b.pct)>0.005?[6,3]:[2,3]);
  g.beginPath();let st=false;
  dados.forEach(function(d,i){const v=d[b.rotulo];if(v==null)return;
   if(!st){g.moveTo(X(i),Y(v));st=true;}else g.lineTo(X(i),Y(v));});
  g.stroke();g.setLineDash([]);});

 document.getElementById('lgVar').innerHTML=
  '<span><i style="background:'+CORC[campo]+'"></i>Δ '+LBLC[campo]+'</span>'+
  bandas.map(b=>'<span><i style="background:'+b.cor+'"></i>'+b.rotulo+'</span>').join('');

 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++)g.fillText((mx-(mx-mn)*i/4).toFixed(2),PL-6,PT+h*i/4+3);
 g.textAlign='center';
 const step=Math.ceil(n/10);
 dados.forEach(function(d,i){if(i%step!==0)return;
  g.save();g.translate(X(i),H-8);
  if(n>14){g.rotate(-Math.PI/5);g.textAlign='right';}
  g.fillText(d.periodo,0,0);g.restore();});
 g.textAlign='left';

 registarTip('chVar',function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  const i=Math.floor((x/esc?x:x-PL)/(w/n));
  const j=Math.round((x-PL)/w*n-0.5);
  const d=dados[j]; if(!d)return '';
  const un=campo==='peso'?' kg':' %';
  let html='<div class="th">'+d.periodo+'</div>'+
   linhaTip(CORC[campo],'Δ',(d.delta>=0?'+':'')+d.delta+un)+
   '<div class="tr"><span>Valor</span><b>'+d.valor+un+'</b></div>'+
   '<div class="tr"><span>Base anterior</span><b>'+d.base+un+'</b></div>';
  bandas.forEach(function(b){if(d[b.rotulo]==null)return;
   html+=linhaTip(b.cor,b.rotulo,(d[b.rotulo]>=0?'+':'')+d[b.rotulo]+un);});
  const dentro=Math.abs(d.delta)<=Math.abs(d[rotulos[0]]||99);
  html+='<div class="tr" style="border-top:1px solid #30363d;margin-top:4px;'+
   'padding-top:4px"><span>Dentro do esperado</span><b style="color:'+
   (dentro?'#2ECC71':'#E67E22')+'">'+(dentro?'sim':'nao')+'</b></div>';
  return html;});
}

function drawMacro(){
 const modo=document.getElementById('macroModo').value;
 const sufixo=modo==='pct'?'_pct':'_g';
 const dados=jan((C.macros||[]).map(function(m){
  return {date:m.date,carb:m['carb'+sufixo],ptn:m['ptn'+sufixo],fat:m['fat'+sufixo]};}));
 const o=ctx('chMacro',240); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 const PL=48,PR=16,PT=12,PB=24,w=W-PL-PR,h=H-PT-PB,n=dados.length;
 if(!n){noData(g,W,H,'Sem dados de macros');return;}
 const cols=['carb','ptn','fat'];
 document.getElementById('lgMacro').innerHTML=cols.map(c=>
  '<span><i style="background:'+CORC[c]+'"></i>'+LBLC[c]+'</span>').join('');
 const mx=modo==='pct'?100:Math.max.apply(null,dados.map(d=>
  cols.reduce((a,c)=>a+(d[c]||0),0)));
 const X=i=>PL+w*(n>1?i/(n-1):0.5);
 g.strokeStyle='#21262d';
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 // areas empilhadas
 let base=new Array(n).fill(0);
 cols.forEach(function(c){
  g.fillStyle=CORC[c];g.globalAlpha=0.75;g.beginPath();
  dados.forEach(function(d,i){const y=PT+h-(base[i]+(d[c]||0))/mx*h;
   if(i===0)g.moveTo(X(i),y);else g.lineTo(X(i),y);});
  for(let i=n-1;i>=0;i--){g.lineTo(X(i),PT+h-base[i]/mx*h);}
  g.closePath();g.fill();g.globalAlpha=1;
  dados.forEach(function(d,i){base[i]+=(d[c]||0);});});
 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++)g.fillText(Math.round(mx-mx*i/4)+(modo==='pct'?'%':'g'),PL-6,PT+h*i/4+3);
 g.textAlign='center';
 const step=Math.ceil(n/8);
 dados.forEach(function(d,i){if(i%step!==0)return;
  g.fillText(d.date.slice(0,7),X(i),H-8);});
 g.textAlign='left';
 registarTip('chMacro',function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  if(x<PL||x>PL+w)return '';
  const i=Math.round((x-PL)/w*(n-1));
  const d=dados[i]; if(!d)return '';
  let html='<div class="th">'+d.date+'</div>';
  cols.forEach(c=>{if(d[c]!=null)
   html+=linhaTip(CORC[c],LBLC[c],d[c]+(modo==='pct'?'%':'g'));});
  return html;});
}

function tabelaLag(){
 document.getElementById('lagHead').innerHTML=
  ['Variavel','Lag optimo','r (Spearman)','p','n','Leitura']
   .map((c,i)=>'<th class="'+(i&&i<5?'num':'')+'">'+c+'</th>').join('');
 const linhas_=[];
 [['peso','Peso'],['bf','Gordura corporal']].forEach(function(par){
  const L=(C.lag||{})[par[0]];
  if(!L){return;}
  const m=L.melhor;
  if(m.r==null){
   linhas_.push('<tr><td>'+par[1]+'</td><td class="num" colspan="5" '+
    'style="color:#484f58">sem correlacao significativa (p &lt; 0.10)</td></tr>');
   return;}
  const cor=m.r<0?'#2ECC71':'#E67E22';
  const leitura=m.r<0
   ? 'mais calorias -> valor mais baixo '+m.lag+'d depois (contra-intuitivo, ver dados)'
   : 'mais calorias -> valor mais alto '+m.lag+'d depois';
  linhas_.push('<tr><td>'+par[1]+'</td>'+
   '<td class="num">'+m.lag+' dias</td>'+
   '<td class="num" style="color:'+cor+'">'+m.r+'</td>'+
   '<td class="num">'+m.p+'</td><td class="num">'+(m.n||'-')+'</td>'+
   '<td style="font-size:12px;color:#8b949e">'+leitura+'</td></tr>');});
 document.getElementById('lagBody').innerHTML=linhas_.join('')||
  '<tr><td class="loading">Sem dados</td></tr>';
}

async function load(){
 const per=document.getElementById('periodo').value;
 let d;
 try{ d=await fetch('/api/corporal?periodo='+per).then(r=>r.json()); }
 catch(e){ document.getElementById('sub').innerHTML=
   '<span class="err">Nao consegui carregar</span>'; return; }
 if(d.error){
  const msg=!d.sheets_ok
   ? 'Google Sheets nao ligado — define GCP_SERVICE_ACCOUNT. Ver /api/debug/sheets'
   : d.error;
  document.getElementById('sub').innerHTML='<span class="err">'+msg+'</span>';
  return; }
 C=d;
 const R=d.resumo;
 document.getElementById('sub').textContent=
  R.n_dias+' dias com registo, de '+R.de+' a '+R.ate;

 function seta(v,inverso){
  if(v==null)return '';
  const bom=inverso?v<0:v>0;
  return '<span style="font-size:11px;color:'+(bom?'#2ECC71':'#E74C3C')+'"> '+
   (v>=0?'+':'')+v+'</span>';}
 document.getElementById('kpis').innerHTML=[
  ['Peso',R.actual.peso!=null?R.actual.peso+' kg':'—',seta(R.tendencia_28d.peso,true)],
  ['Gordura',R.actual.bf!=null?R.actual.bf+' %':'—',seta(R.tendencia_28d.bf,true)],
  ['Calorias',R.actual.calorias!=null?Math.round(R.actual.calorias):'—',''],
  ['Net',R.actual.net!=null?Math.round(R.actual.net):'—',''],
  ['Registos de peso',R.cobertura.peso,''],
  ['Registos de calorias',R.cobertura.calorias,'']
 ].map(k=>'<div class="card"><div class="label">'+k[0]+'</div><div class="value">'+
  k[1]+k[2]+'</div></div>').join('');

 document.getElementById('subVar').innerHTML=
  'Bandas sobre o valor do periodo anterior — Peso ±0.30% a ±0.70%, '+
  'Gordura ±0.25% a ±0.65%. Barras dentro das bandas = variacao fisiologica normal.';

 drawPeso();drawCal();drawVar();drawMacro();tabelaLag();
}
document.getElementById('periodo').onchange=load;
['varSel'].forEach(id=>document.getElementById(id).onchange=drawVar);
document.getElementById('macroModo').onchange=drawMacro;
document.getElementById('janela').onchange=function(){
 if(!C)return; drawPeso();drawCal();drawMacro();};
window.addEventListener('resize',function(){
 if(!C)return; drawPeso();drawCal();drawVar();drawMacro();});
load();
"""


def render():
    return page('Composicao corporal', SLUG, BODY, JS)
