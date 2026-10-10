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

<h2>Evolucao temporal: peso, gordura e calorias</h2>
<div class="sub" id="subTemp">Linha: media movel de N dias sobre os registos diarios (mesma regra dos KPIs).
  Barras: resumo do periodo escolhido (mediana para peso e gordura; media para calorias e net).
  Pontos: registos diarios originais.</div>
<div class="controls">
  <label class="sel">Janela
    <select id="janela">
      <option value="90" selected>90 dias</option>
      <option value="180">6 meses</option>
      <option value="365">1 ano</option>
      <option value="0">Tudo</option>
    </select></label>
  <label class="sel">Media movel
    <select id="rollJan">
      <option value="7" selected>7 dias</option>
      <option value="14">14 dias</option>
      <option value="28">28 dias</option>
    </select></label>
  <label class="sel">Periodo
    <select id="granul">
      <option value="W" selected>Semana</option>
      <option value="M">Mes</option>
      <option value="Q">Trimestre</option>
      <option value="S">Semestre</option>
      <option value="A">Ano</option>
    </select></label>
</div>
<div class="chartbox">
  <div class="legend" id="lgPeso"></div>
  <canvas id="chPeso" height="220"></canvas>
</div>
<div class="chartbox">
  <div class="legend" id="lgBF"></div>
  <canvas id="chBF" height="200"></canvas>
</div>
<div class="chartbox">
  <div class="legend" id="lgCal"></div>
  <canvas id="chCal" height="200"></canvas>
</div>
<div class="chartbox">
  <div class="legend" id="lgNet"></div>
  <canvas id="chNet" height="200"></canvas>
</div>
<div class="sub" id="subTempInfo"></div>

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

// ── Visao temporal ─────────────────────────────────────────────────────────
// Tres camadas por metrica, todas derivadas de C.linhas (nada e alterado):
//   pontos   : registos diarios originais
//   rolling  : media movel de N dias de calendario. Mesma regra de
//              corporal.media_movel (que alimenta os KPIs): janela de N dias
//              terminada no dia, so dias com valor, minimo MIN_OBS_ROLL.
//              Calculada sobre o historico completo ANTES do recorte da Janela.
//   periodo  : resumo por periodo (semana ISO, mes, trimestre, semestre, ano):
//              mediana (peso, BF) ou media (calorias, net) dos dias com registo.
// A granularidade muda so a camada "periodo"; a media movel nao depende dela.
const SERIES_TEMP={
 peso:{titulo:'Peso',unid:'kg',stat:'mediana',cor:CORC.peso,minSpan:2},
 bf:{titulo:'Gordura corporal',unid:'%',stat:'mediana',cor:CORC.bf,minSpan:2},
 calorias:{titulo:'Calorias',unid:'kcal',stat:'media',cor:CORC.calorias,minSpan:1000},
 net:{titulo:'Net',unid:'kcal',stat:'media',cor:CORC.net,minSpan:1000}};
// dias minimos por periodo; abaixo disto o ponto do periodo aparece oco
const MIN_REG={W:3,M:7,Q:14,S:30,A:60};
const GRAN_NOME={W:'semana',M:'mes',Q:'trimestre',S:'semestre',A:'ano'};
// minimo de dias com registo dentro da janela rolling (igual a corporal.media_movel)
const MIN_OBS_ROLL=3;
const PAINEIS=[
 {canvas:'chPeso',legenda:'lgPeso',altura:220,metricas:['peso']},
 {canvas:'chBF',legenda:'lgBF',altura:200,metricas:['bf']},
 {canvas:'chCal',legenda:'lgCal',altura:200,metricas:['calorias']},
 {canvas:'chNet',legenda:'lgNet',altura:200,metricas:['net']}];
// camadas visiveis (alternadas pela legenda); pontos de calorias/net ficam
// desligados por padrao para nao poluir o painel
const ATIVAS={peso_pontos:true,peso_rolling:true,peso_periodo:true,
 bf_pontos:true,bf_rolling:true,bf_periodo:true,
 calorias_pontos:false,calorias_rolling:true,calorias_periodo:true,
 net_pontos:false,net_rolling:true,net_periodo:true};
// ultimo desenho (lido pelos testes; nao e usado pela interface)
let TEMP_ULT=null;

function pad2(n){return String(n).padStart(2,'0');}
function dUTC(s){const p=s.split('-');return new Date(Date.UTC(+p[0],+p[1]-1,+p[2]));}
function sUTC(d){return d.getUTCFullYear()+'-'+pad2(d.getUTCMonth()+1)+'-'+pad2(d.getUTCDate());}
function addDias(d,n){return new Date(d.getTime()+n*86400000);}
function diaNum(s){return Math.round(dUTC(s).getTime()/86400000);}
function diaStr(n){return sUTC(new Date(n*86400000));}
function dataBR(s){return s.slice(8,10)+'/'+s.slice(5,7)+'/'+s.slice(0,4);}
function hojeLocal(){const d=new Date();return d.getFullYear()+'-'+pad2(d.getMonth()+1)+'-'+pad2(d.getDate());}

// periodo (ISO semana seg-dom, mes, trimestre, semestre, ano de calendario)
// que contem a data s
function periodoInfo(s,g){
 const d=dUTC(s),y=d.getUTCFullYear(),m=d.getUTCMonth();
 if(g==='W'){
  const ini=addDias(d,-((d.getUTCDay()+6)%7)), fim=addDias(ini,6);
  const th=addDias(ini,3), yy=th.getUTCFullYear();
  const wk=Math.ceil(((th-Date.UTC(yy,0,1))/86400000+1)/7);
  return {k:yy+'-W'+pad2(wk),ini:sUTC(ini),fim:sUTC(fim),rot:pad2(ini.getUTCDate())+'/'+pad2(ini.getUTCMonth()+1)};}
 if(g==='M')
  return {k:y+'-'+pad2(m+1),ini:y+'-'+pad2(m+1)+'-01',fim:sUTC(new Date(Date.UTC(y,m+1,0))),
   rot:pad2(m+1)+'/'+String(y).slice(2)};
 if(g==='Q'){const q=Math.floor(m/3);
  return {k:y+'-Q'+(q+1),ini:sUTC(new Date(Date.UTC(y,q*3,1))),fim:sUTC(new Date(Date.UTC(y,q*3+3,0))),
   rot:'T'+(q+1)+' '+String(y).slice(2)};}
 if(g==='S'){const h=m<6?0:1;
  return {k:y+'-S'+(h+1),ini:sUTC(new Date(Date.UTC(y,h*6,1))),fim:sUTC(new Date(Date.UTC(y,h*6+6,0))),
   rot:'S'+(h+1)+' '+String(y).slice(2)};}
 return {k:String(y),ini:y+'-01-01',fim:y+'-12-31',rot:String(y)};
}
// inicio do periodo seguinte
function proximoPeriodo(p,g){
 const d=dUTC(p.ini),y=d.getUTCFullYear(),m=d.getUTCMonth();
 if(g==='W') return sUTC(addDias(d,7));
 if(g==='M') return sUTC(new Date(Date.UTC(y,m+1,1)));
 if(g==='Q') return sUTC(new Date(Date.UTC(y,m+3,1)));
 if(g==='S') return sUTC(new Date(Date.UTC(y,m+6,1)));
 return (y+1)+'-01-01';
}
function estatistica(vals,tipo){
 const n=vals.length;
 if(tipo==='mediana'){const s=vals.slice().sort((a,b)=>a-b);
  return n%2?s[(n-1)/2]:(s[n/2-1]+s[n/2])/2;}
 return vals.reduce((a,b)=>a+b,0)/n;
}

// media movel de N dias de calendario, mesma regra de corporal.media_movel.
// Devolve {dia numerico: {v, n}} so para dias com minimo de observacoes.
function mediaMovel(full,campo,N){
 const val={}; let a=Infinity,b=-Infinity;
 full.forEach(r=>{const t=diaNum(r.date); if(t<a)a=t; if(t>b)b=t;
  const v=r[campo]; if(typeof v==='number'&&isFinite(v)) val[t]=v;});
 const out={};
 for(let d=a;d<=b;d++){
  let s=0,c=0;
  for(let k=d-N+1;k<=d;k++){ if(val[k]!==undefined){s+=val[k];c++;} }
  if(c>=MIN_OBS_ROLL) out[d]={v:s/c,n:c};
 }
 return out;
}

// agrega linhas diarias por periodo; periodos vazios entre o primeiro e o
// ultimo registo aparecem com dias=0 e valores nulos (nada e preenchido)
function agregarTemporal(rows,g){
 const hoje=hojeLocal();
 const validas=rows.filter(r=>r&&r.date&&r.date<=hoje)
  .sort((a,b)=>a.date<b.date?-1:a.date>b.date?1:0);
 if(!validas.length) return [];
 const mapa={};
 validas.forEach(r=>{const info=periodoInfo(r.date,g);
  if(!mapa[info.k]) mapa[info.k]={info:info,dias:[]};
  mapa[info.k].dias.push(r);});
 const fimIni=periodoInfo(validas[validas.length-1].date,g).ini;
 const out=[];
 let ini=periodoInfo(validas[0].date,g).ini;
 while(ini<=fimIni){
  const info=periodoInfo(ini,g);
  const dias=mapa[info.k]?mapa[info.k].dias:[];
  const item={k:info.k,rot:info.rot,ini:info.ini,fim:info.fim,dias:dias.length,n:{},v:{}};
  Object.keys(SERIES_TEMP).forEach(s=>{
   const vals=dias.map(r=>r[s]).filter(v=>typeof v==='number'&&isFinite(v));
   item.n[s]=vals.length;
   item.v[s]=vals.length?estatistica(vals,SERIES_TEMP[s].stat):null;});
  out.push(item);
  ini=proximoPeriodo(info,g);
 }
 return out;
}
// recorte da Janela em dias de calendario, a partir do ultimo registo
function janelaDias(rows){
 const n=parseInt(document.getElementById('janela').value,10);
 if(!(n>0)||!rows.length) return rows.slice();
 const fim=rows.reduce((m,r)=>r.date>m?r.date:m,'');
 const corte=sUTC(addDias(dUTC(fim),-(n-1)));
 return rows.filter(r=>r.date>=corte);
}
function fmtV(v,unid){return unid==='kcal'?Math.round(v).toLocaleString('pt-PT'):v.toFixed(1);}

// limites com passo redondo (1, 2, 2.5 ou 5 x 10^k) e 4 intervalos
function niceLim(lo,hi){
 const fat=[1,2,2.5,5];
 for(let m=Math.pow(10,Math.floor(Math.log10(Math.max(hi-lo,1e-9)))-2);m<1e9;m*=10)
  for(const f of fat){const s=f*m,a=Math.floor(lo/s)*s;
   if(a+4*s>=hi) return [a,a+4*s];}
 return [lo,hi];
}
function limitesY(vals,minSpan){
 const lo=vals.reduce((a,b)=>b<a?b:a,Infinity), hi=vals.reduce((a,b)=>b>a?b:a,-Infinity);
 const mid=(lo+hi)/2, span=Math.max(hi-lo,minSpan)*1.2;
 return niceLim(mid-span/2,mid+span/2);
}

function desenharPainel(p,dados,g,N,t0,t1){
 const camadas=[];
 p.metricas.forEach(m=>{const c=SERIES_TEMP[m];
  camadas.push({id:m+'_pontos',m:m,tipo:'pontos',nome:c.titulo+' · registos diarios'});
  camadas.push({id:m+'_rolling',m:m,tipo:'rolling',nome:c.titulo+' · media movel '+N+' dias'});
  camadas.push({id:m+'_periodo',m:m,tipo:'periodo',nome:c.titulo+' · '+c.stat+' por '+GRAN_NOME[g]});});
 const leg=document.getElementById(p.legenda);
 leg.innerHTML=camadas.map(L=>{const c=SERIES_TEMP[L.m];
  return '<span data-serie="'+L.id+'" style="cursor:pointer;opacity:'+(ATIVAS[L.id]?1:0.35)+'">'+
   '<i style="background:'+c.cor+'"></i>'+L.nome+' ('+c.unid+')</span>';}).join('');
 leg.onclick=function(ev){
  const sp=ev.target.closest?ev.target.closest('[data-serie]'):null; if(!sp) return;
  const id=sp.getAttribute('data-serie');
  const visiveis=camadas.filter(L=>ATIVAS[L.id]).length;
  if(ATIVAS[id]&&visiveis<=1) return;          // mantem pelo menos uma camada
  ATIVAS[id]=!ATIVAS[id]; drawTemporal();};

 const vis=camadas.filter(L=>ATIVAS[L.id]);
 const o=ctx(p.canvas,p.altura); if(!o) return;
 const G=o.g,W=o.W,H=o.H;
 const PL=60,PR=12,PT=12,PB=26,w=W-PL-PR,h=H-PT-PB;
 if(!vis.length){noData(G,W,H,'Nenhuma camada ativa');return;}
 const vals=[];
 vis.forEach(L=>{const d=dados[L.m];
  if(L.tipo==='pontos') d.pontos.forEach(q=>vals.push(q.v));
  if(L.tipo==='rolling') d.rolPts.forEach(q=>{if(q.v!=null)vals.push(q.v);});
  if(L.tipo==='periodo') d.periodos.forEach(q=>{if(q.v!=null)vals.push(q.v);});});
 if(!vals.length){noData(G,W,H,'Sem registos no intervalo');return;}
 const minSpan=Math.max.apply(null,p.metricas.map(m=>SERIES_TEMP[m].minSpan));
 const lim=limitesY(vals,minSpan), lo=lim[0], hi=lim[1];
 const X=t=>PL+w*(t1>t0?(t-t0)/(t1-t0):0.5);
 const Y=v=>PT+h-(v-lo)/(hi-lo)*h;
 const base=dados[p.metricas[0]].periodos;

 G.strokeStyle='#21262d';G.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;G.beginPath();G.moveTo(PL,y);G.lineTo(PL+w,y);G.stroke();}
 // periodos sem registos: faixa cinza (sem valor)
 base.forEach(q=>{if(q.dias===0){const a=X(Math.max(q.ini,t0)),b=X(Math.min(q.fim,t1));
  G.fillStyle='rgba(139,148,158,0.12)';G.fillRect(a,PT,Math.max(1,b-a),h);}});

 vis.forEach(L=>{
  const c=SERIES_TEMP[L.m], d=dados[L.m];
  if(L.tipo==='periodo'){
   G.strokeStyle=c.cor;G.lineWidth=4;G.globalAlpha=0.5;
   d.periodos.forEach(q=>{if(q.v==null)return;
    G.beginPath();G.moveTo(X(Math.max(q.ini,t0)),Y(q.v));G.lineTo(X(Math.min(q.fim,t1)),Y(q.v));G.stroke();});
   G.globalAlpha=1;
  } else if(L.tipo==='rolling'){
   G.strokeStyle=c.cor;G.lineWidth=2;G.beginPath();let st=false;
   d.rolPts.forEach(q=>{if(q.v==null){st=false;return;}
    if(!st){G.moveTo(X(q.t),Y(q.v));st=true;}else G.lineTo(X(q.t),Y(q.v));});
   G.stroke();
  } else {
   G.fillStyle=c.cor;G.globalAlpha=0.55;
   d.pontos.forEach(q=>{G.beginPath();G.arc(X(q.t),Y(q.v),2.2,0,2*Math.PI);G.fill();});
   G.globalAlpha=1;
  }});

 const u=SERIES_TEMP[p.metricas[0]].unid;
 G.font='10px sans-serif';G.fillStyle='#8b949e';G.textAlign='right';
 for(let i=0;i<=4;i++){const v=hi-(hi-lo)*i/4;G.fillText(fmtV(v,u),PL-6,PT+h*i/4+3);}
 G.textAlign='left';G.fillText(u,4,PT+8);
 G.fillStyle='#8b949e';G.textAlign='center';
 const step=Math.max(1,Math.ceil(base.length/8));
 base.forEach((q,i)=>{if(i%step!==0)return;G.fillText(q.rot,X(Math.max(q.ini,t0)),H-8);});
 G.textAlign='left';

 registarTip(p.canvas,function(mxp,myp,rw){
  const x=mxp*(W/rw);
  if(x<PL||x>PL+w) return '';
  const t=Math.round(t0+(x-PL)/w*(t1-t0));
  if(t<t0||t>t1) return '';
  let html='<div class="th">'+dataBR(diaStr(t))+'</div>';
  p.metricas.forEach(m=>{const c=SERIES_TEMP[m],d=dados[m];
   vis.filter(L=>L.m===m).forEach(L=>{
    if(L.tipo==='pontos'){const q=d.pontos.find(z=>z.t===t);
     if(q) html+=linhaTip(c.cor,c.titulo+' · registo',fmtV(q.v,c.unid)+' '+c.unid);}
    if(L.tipo==='rolling'){const q=d.rolPts[t-t0];
     html+=linhaTip(c.cor,c.titulo+' · media movel '+N+' dias',q.v==null?'sem media (menos de '+MIN_OBS_ROLL+' dias)':
      fmtV(q.v,c.unid)+' '+c.unid+' · '+q.n+' dias validos na janela de '+N);}
    if(L.tipo==='periodo'){const q=d.periodos.find(z=>z.ini<=t&&t<=z.fim);
     if(q) html+=linhaTip(c.cor,c.titulo+' · '+c.stat+' '+GRAN_NOME[g]+' '+q.k,
      q.dias===0?'sem registos no periodo':fmtV(q.v,c.unid)+' '+c.unid+' · n='+q.n+
      ' ('+q.dias+' dias)'+(q.n<MIN_REG[g]?' poucos registos':''));}
   });});
  return html;});
}

function drawTemporal(){
 if(!C) return;
 const g=document.getElementById('granul').value;
 const N=parseInt(document.getElementById('rollJan').value,10)||7;
 const hoje=hojeLocal();
 const full=(C.linhas||[]).filter(r=>r&&r.date&&r.date<=hoje)
  .sort((x,y)=>x.date<y.date?-1:x.date>y.date?1:0);
 if(!full.length){PAINEIS.forEach(p=>{const o=ctx(p.canvas,p.altura);if(o)noData(o.g,o.W,o.H,'Sem registos');});return;}
 const fim=full[full.length-1].date;
 const jn=parseInt(document.getElementById('janela').value,10);
 const corte=(jn>0)?sUTC(addDias(dUTC(fim),-(jn-1))):full[0].date;
 const disp=full.filter(r=>r.date>=corte);
 const per=agregarTemporal(disp,g);
 const t0=diaNum(corte), t1=diaNum(fim);
 const todos={};
 PAINEIS.forEach(p=>{
  const dados={};
  p.metricas.forEach(m=>{
   const pontos=disp.filter(r=>typeof r[m]==='number'&&isFinite(r[m])).map(r=>({t:diaNum(r.date),v:r[m]}));
   const rm=mediaMovel(full,m,N);
   const rolPts=[];
   for(let t=t0;t<=t1;t++) rolPts.push({t:t,v:rm[t]?rm[t].v:null,n:rm[t]?rm[t].n:0});
   const periodos=per.map(q=>({k:q.k,rot:q.rot,ini:diaNum(q.ini),fim:diaNum(q.fim),dias:q.dias,v:q.v[m],n:q.n[m]}));
   dados[m]={pontos:pontos,rolPts:rolPts,periodos:periodos};
  });
  todos[p.canvas]=dados;
  desenharPainel(p,dados,g,N,t0,t1);
 });
 TEMP_ULT={g:g,N:N,corte:corte,fim:fim,t0:t0,t1:t1,dados:todos};
 const semReg=per.filter(q=>!q.dias).length;
 const poucos=per.filter(q=>q.dias>0&&q.dias<MIN_REG[g]).length;
 document.getElementById('subTempInfo').textContent=per.length+' periodos ('+GRAN_NOME[g]+'). '+
  semReg+' sem registos (linhas interrompidas); '+poucos+' com menos de '+MIN_REG[g]+
  ' dias (pontos ocos). Media movel de '+N+' dias calculada sobre os registos diarios, sem preencher lacunas.';
}

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

 drawTemporal();drawVar();drawMacro();tabelaLag();
}
document.getElementById('periodo').onchange=load;
['varSel'].forEach(id=>document.getElementById(id).onchange=drawVar);
document.getElementById('macroModo').onchange=drawMacro;
document.getElementById('janela').onchange=function(){
 if(!C)return; drawTemporal();drawMacro();};
document.getElementById('granul').onchange=function(){
 if(!C)return; drawTemporal();};
document.getElementById('rollJan').onchange=function(){
 if(!C)return; drawTemporal();};
window.addEventListener('resize',function(){
 if(!C)return; drawTemporal();drawVar();drawMacro();});
load();
"""


def render():
    return page('Composicao corporal', SLUG, BODY, JS)
