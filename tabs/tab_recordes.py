"""Tab Recordes — melhores esforcos por duracao, calculados do historico.

Substitui os custom fields MMP ("No (PR: 302w)"): aqui temos o valor, a data,
o recorde anterior e a progressao ao longo dos anos.
"""

from datetime import datetime
from flask import jsonify, request
import db
from config import CICLICOS, CORES_MOD, season_de, SEASON_INICIO_MES
from api_client import seasons_do_atleta
from tabs.base import page

SLUG = 'recordes'
ROUTE = '/recordes'


def api_data():
    """Recordes. Devolve o erro em JSON em vez de rebentar com 500, para a
    pagina poder dizer ao utilizador o que se passa."""
    tipo = request.args.get('tipo') or None
    desde = request.args.get('desde') or None

    if not db.ENABLED:
        return jsonify({'error': 'sem base de dados configurada',
                        'duracoes': [], 'melhores': {}, 'progressao': {},
                        'n_sessoes': 0, 'tipos_disponiveis': []})
    try:
        r = db.calcular_recordes(tipo, desde)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': f'{type(e).__name__}: {e}',
            'sugestao': 'o esquema da tabela pode estar desactualizado; '
                        'abre /api/db/recriar-curvas',
            'colunas_actuais': db.colunas_de('power_curves'),
            'duracoes': [], 'melhores': {}, 'progressao': {},
            'n_sessoes': 0, 'tipos_disponiveis': []})

    try:
        tipos = db._exec("SELECT DISTINCT type FROM power_curves ORDER BY type",
                         fetch='all')
        r['tipos_disponiveis'] = [t[0] for t in (tipos or []) if t[0]]
    except Exception:
        r['tipos_disponiveis'] = []
    r['cores'] = CORES_MOD
    r['tipo'] = tipo
    return jsonify(r)


def api_seasons():
    """Melhor curva por season, para sobrepor no grafico."""
    tipo = request.args.get('tipo') or None
    if not db.ENABLED:
        return jsonify({'error': 'sem base de dados', 'seasons': [],
                        'por_season': {}, 'duracoes': []})
    try:
        marcos = seasons_do_atleta()
        r = db.curvas_por_season(tipo, marcos)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'{type(e).__name__}: {e}',
                        'seasons': [], 'por_season': {}, 'duracoes': []})
    hoje = datetime.now().strftime('%Y-%m-%d')
    r['season_actual'] = season_de(hoje, marcos)
    r['fonte'] = 'calendario' if marcos else 'mes'
    r['marcos'] = [{'inicio': d, 'nome': n} for d, n in (marcos or [])]
    r['inicio_mes'] = SEASON_INICIO_MES
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

<h2>Comparar seasons</h2>
<div class="sub" id="subSeason">A carregar...</div>
<div class="toggles" id="seasonToggles"></div>
<div class="chartbox">
  <div class="legend" id="lgSeasons"></div>
  <canvas id="chSeasons" height="300"></canvas>
</div>
<div class="wrap" style="max-height:320px;margin-bottom:14px"><table>
  <thead><tr id="seasonHead"></tr></thead><tbody id="seasonBody"></tbody></table></div>

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

 registarTip('chCurva',function(mx_,my_,rw){
  const esc=rw/W, x=mx_/esc;
  if(x<PL||x>PL+w) return '';
  const X=s=>PL+w*(Math.log10(s)-lmin)/(lmax-lmin||1);
  let alvo=null,dist=1e9;
  durs.forEach(function(s){const d=Math.abs(X(s)-x);if(d<dist){dist=d;alvo=s;}});
  if(alvo===null) return '';
  const m=R.melhores[alvo]; if(!m) return '';
  let html='<div class="th">'+fmtD(alvo)+'</div>';
  html+=linhaTip('#5DADE2','Melhor',Math.round(m.watts)+' W');
  if(PESO) html+=linhaTip('#8b949e','W/kg',(m.watts/PESO).toFixed(2));
  if(m.date) html+='<div class="tr"><span>Quando</span><b>'+m.date+'</b></div>';
  if(m.name) html+='<div class="tr"><span>Sessao</span><b>'+m.name+'</b></div>';
  if(m.anterior_watts){
   const dif=Math.round(m.watts-m.anterior_watts);
   html+='<div class="tr" style="border-top:1px solid #30363d;margin-top:4px;'+
    'padding-top:4px"><span>Recorde anterior</span><b>'+
    Math.round(m.anterior_watts)+' W (+'+dif+')</b></div>';}
  return html;
 });
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

function falhou(msg){
 document.getElementById('sub').innerHTML='<span class="err">'+msg+'</span>';
 const t=document.getElementById('tbl');
 if(t)t.innerHTML='<tr><td class="loading">'+msg+'</td></tr>';
}

async function load(){
 const tipo=document.getElementById('tipo').value;
 const desde=document.getElementById('desde').value;
 const qs=[];
 if(tipo)qs.push('tipo='+tipo);
 if(desde)qs.push('desde='+desde);
 try{
  const resp=await fetch('/api/recordes'+(qs.length?'?'+qs.join('&'):''));
  if(!resp.ok){ falhou('O servidor devolveu '+resp.status+'. Corre /api/sync/curvas.'); return; }
  R=await resp.json();
 }catch(e){ falhou('Nao consegui carregar os recordes: '+e.message); return; }
 if(R && R.error){
  falhou(R.error + (R.sugestao ? ' — ' + R.sugestao : ''));
  return; }

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
 PESO=null;
 try{ kpis();drawCurva();drawProg();tabela(); }
 catch(e){ falhou('Erro a desenhar: '+e.message); }
}

// ─── Comparar seasons ────────────────────────────────────────────────────
let SE=null, SEON={};
const CORSEASON=['#5DADE2','#E74C3C','#2ECC71','#F4D03F','#AF7AC5',
                 '#48C9B0','#E67E22','#7FB3D5','#F1948A','#82E0AA'];
function corSeason(i){ return CORSEASON[i % CORSEASON.length]; }

function drawSeasons(){
 const o=ctx('chSeasons',300); if(!o) return;
 const g=o.g,W=o.W,H=o.H;
 const activas=(SE?SE.seasons:[]).filter(s=>SEON[s]);
 document.getElementById('lgSeasons').innerHTML=activas.map(function(s){
  const i=SE.seasons.indexOf(s);
  const n=SE.por_season[s].n_sessoes;
  return '<span><i style="background:'+corSeason(i)+'"></i>'+s+' ('+n+')</span>';}).join('');
 if(!activas.length){ noData(g,W,H,'Escolhe pelo menos uma season'); return; }

 const durs=SE.duracoes||[];
 if(!durs.length){ noData(g,W,H); return; }
 let mx=0;
 activas.forEach(s=>durs.forEach(function(d){
  const m=SE.por_season[s].melhores[d]; if(m&&m.watts>mx)mx=m.watts;}));
 if(!mx){ noData(g,W,H); return; }

 const PL=50,PR=16,PT=12,PB=28,w=W-PL-PR,h=H-PT-PB;
 const lmin=Math.log10(Math.max(1,durs[0])),lmax=Math.log10(durs[durs.length-1]);
 const X=s=>PL+w*(Math.log10(Math.max(1,s))-lmin)/((lmax-lmin)||1);
 const Y=v=>PT+h-v/mx*h;

 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 [1,5,15,60,300,1200,3600].forEach(function(s){
  if(s<durs[0]||s>durs[durs.length-1])return;
  g.strokeStyle='#21262d';g.beginPath();g.moveTo(X(s),PT);g.lineTo(X(s),PT+h);g.stroke();});

 activas.forEach(function(s){
  const i=SE.seasons.indexOf(s);
  g.strokeStyle=corSeason(i);g.lineWidth=1.8;g.beginPath();
  let st=false;
  durs.forEach(function(d){
   const m=SE.por_season[s].melhores[d];
   if(!m){st=false;return;}
   const x=X(d),y=Y(m.watts);
   if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);});
  g.stroke();});

 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++)g.fillText(Math.round(mx-mx*i/4)+'W',PL-6,PT+h*i/4+3);
 g.textAlign='center';
 [1,5,15,60,300,1200,3600].forEach(function(s){
  if(s<durs[0]||s>durs[durs.length-1])return;
  g.fillText(fmtD(s),X(s),H-8);});
 g.textAlign='left';

 registarTip('chSeasons',function(mx_,my_,rw){
  const esc=rw/W, x=mx_/esc;
  if(x<PL||x>PL+w) return '';
  let alvo=null,dist=1e9;
  durs.forEach(function(d){const dd=Math.abs(X(d)-x);if(dd<dist){dist=dd;alvo=d;}});
  if(alvo===null) return '';
  let html='<div class="th">'+fmtD(alvo)+'</div>';
  activas.forEach(function(s){
   const m=SE.por_season[s].melhores[alvo];
   if(!m) return;
   html+=linhaTip(corSeason(SE.seasons.indexOf(s)),s,Math.round(m.watts)+' W');});
  if(activas.length>1){
   const a=SE.por_season[activas[0]].melhores[alvo];
   const b=SE.por_season[activas[1]].melhores[alvo];
   if(a&&b){const dif=Math.round(a.watts-b.watts);
    html+='<div class="tr" style="border-top:1px solid #30363d;margin-top:4px;'+
     'padding-top:4px"><span>Diferenca</span><b style="color:'+
     (dif>=0?'#2ECC71':'#E74C3C')+'">'+(dif>=0?'+':'')+dif+' W</b></div>';}}
  return html;
 });
}

function tabelaSeasons(){
 if(!SE) return;
 const activas=SE.seasons.filter(s=>SEON[s]);
 const mostrar=[60,300,1200,3600].filter(d=>(SE.duracoes||[]).indexOf(d)!==-1);
 document.getElementById('seasonHead').innerHTML=
  ['Season','Sessoes','Periodo'].concat(mostrar.map(fmtD))
   .map((c,i)=>'<th class="'+(i>1?'num':'')+'">'+c+'</th>').join('');
 // referencia = season mais recente das activas, para calcular a diferenca
 const ref=activas.length?activas[0]:null;
 document.getElementById('seasonBody').innerHTML=activas.map(function(s){
  const v=SE.por_season[s];
  const cels=mostrar.map(function(d){
   const m=v.melhores[d];
   if(!m) return '<td class="num">-</td>';
   let extra='';
   if(ref&&s!==ref&&SE.por_season[ref].melhores[d]){
    const dif=SE.por_season[ref].melhores[d].watts-m.watts;
    const col=dif>=0?'#2ECC71':'#E74C3C';
    extra=' <span style="color:'+col+';font-size:11px">'+(dif>=0?'+':'')+Math.round(dif)+'</span>';
   }
   return '<td class="num">'+Math.round(m.watts)+'W'+extra+'</td>';}).join('');
  return '<tr><td>'+s+'</td><td class="num">'+v.n_sessoes+'</td>'+
   '<td>'+v.de+' a '+v.ate+'</td>'+cels+'</tr>';}).join('')
  || '<tr><td class="loading">Escolhe pelo menos uma season</td></tr>';
}

async function loadSeasons(){
 const tipo=document.getElementById('tipo').value;
 try{
  const resp=await fetch('/api/recordes/seasons'+(tipo?'?tipo='+tipo:''));
  SE=await resp.json();
 }catch(e){
  document.getElementById('subSeason').innerHTML=
   '<span class="err">Nao consegui carregar as seasons</span>'; return; }
 if(SE.error){ document.getElementById('subSeason').innerHTML=
   '<span class="err">'+SE.error+'</span>'; return; }

 const ss=SE.seasons||[];
 if(!ss.length){ document.getElementById('subSeason').textContent='Sem dados'; return; }

 // por defeito: season actual + a anterior
 SEON={}; ss.forEach((s,i)=>SEON[s]=(i<2));
 document.getElementById('subSeason').textContent=
  (SE.fonte==='calendario'
    ? 'Seasons do teu calendario (eventos SEASON_START)'
    : (SE.inicio_mes===1 ? 'Ano civil — sem SEASON_START no calendario'
       : 'Inicio no mes '+SE.inicio_mes+' — sem SEASON_START no calendario'))
  +' · actual: '+(SE.season_actual||'?');
 document.getElementById('seasonToggles').innerHTML=ss.map(function(s,i){
  return '<label style="color:'+corSeason(i)+'"><input type="checkbox" data-s="'+s+'" '+
   (SEON[s]?'checked':'')+'> '+s+'</label>';}).join('');
 document.querySelectorAll('#seasonToggles input').forEach(function(cb){
  cb.onchange=function(){ SEON[cb.dataset.s]=cb.checked; drawSeasons(); tabelaSeasons(); };});
 drawSeasons(); tabelaSeasons();
}

['tipo','desde'].forEach(id=>document.getElementById(id).onchange=load);
document.getElementById('tipo').addEventListener('change',loadSeasons);
document.getElementById('dur').onchange=drawProg;
window.addEventListener('resize',function(){
 if(R){drawCurva();drawProg();}
 if(SE){drawSeasons();}});
loadSeasons();
load();
"""


def render():
    return page('Recordes', SLUG, BODY, JS)
