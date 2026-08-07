"""Tab PMC — Performance Management Chart.

Carga vem da Intervals.icu (icu_training_load, ja na base de dados).
Wellness e composicao corporal vem dos Google Sheets, como no dashboard
original: HRV/RMSSD, HRR, sono, stress, fadiga, humor, dores musculares,
peso, gordura, calorias e macros.
"""

from datetime import datetime, timedelta
from flask import jsonify, request

import db
import pmc
import sheets_client as sheets
from api_client import fetch_activities, norm_tipo, num
from config import CICLICOS, CORES_MOD
from tabs.base import page

SLUG = 'pmc'

_cache = {'wellness': None, 'corporal': None, 'time': None}
TTL = 1800   # 30 min


def _sheets(force=False):
    """Wellness e corporal, com cache — os sheets mudam uma vez por dia."""
    agora = datetime.now()
    if not force and _cache['time'] and (agora - _cache['time']).total_seconds() < TTL:
        return _cache['wellness'], _cache['corporal'], None

    w, e1 = sheets.carregar_wellness()
    c, e2 = sheets.carregar_corporal()
    _cache.update({'wellness': w, 'corporal': c, 'time': agora})
    erros = {k: v for k, v in (('wellness', e1), ('corporal', e2)) if v}
    return w, c, (erros or None)


def api_data():
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'sem actividades'}), 500

    sessoes = []
    for a in acts:
        d = (a.get('start_date_local') or '')[:10]
        if len(d) != 10:
            continue
        sessoes.append({
            'id': a.get('id'), 'date': d, 'type': norm_tipo(a.get('type')),
            'name': a.get('name'), 'tl': num(a.get('icu_training_load')),
            'horas': (num(a.get('elapsed_time')) or num(a.get('moving_time'))) / 3600,
            'rpe': a.get('icu_rpe'), 'xss': num(a.get('SS')),
            # proxies de performance para ajustar o gamma
            'cp': (num(a.get('icu_pm_cp')) or num(a.get('icu_rolling_ftp'))
                   or num(a.get('icu_pm_ftp')) or None),
            'w_prime': num(a.get('icu_pm_w_prime')) or None,
        })

    desde = request.args.get('desde') or None
    serie = pmc.calcular(sessoes, 'tl', desde=desde)
    mods = pmc.por_modalidade(sessoes, CICLICOS, 'tl', desde=desde)

    wellness, corporal, erros_sheets = _sheets()

    try:
        ftlm_res = pmc.calcular_ftlm(sessoes, wellness, serie, CICLICOS)
        erro_ftlm = None
    except Exception as e:
        import traceback
        traceback.print_exc()
        ftlm_res, erro_ftlm = None, f'{type(e).__name__}: {e}'

    fim = serie[-1] if serie else {}
    return jsonify({
        'status': 'OK',
        'serie': serie,
        'por_modalidade': mods,
        'sessoes': sessoes,
        'wellness': wellness or [],
        'corporal': corporal or [],
        'escala_1a5': sheets.ESCALA_1A5,
        'erros_sheets': erros_sheets,
        'sheets_ok': sheets.disponivel(),
        'actual': {
            'ctl': fim.get('ctl'), 'atl': fim.get('atl'),
            'tsb': fim.get('tsb'), 'ramp': fim.get('ramp'),
            'estado': pmc.estado_forma(fim.get('tsb')),
        },
        'alertas': pmc.alertas(serie, wellness),
        'ftlm': ftlm_res, 'erro_ftlm': erro_ftlm,
        'cores': CORES_MOD, 'ciclicos': CICLICOS,
    })


def api_sheets_debug():
    return jsonify(sheets.diagnostico())


BODY = r"""
<h1>PMC — Performance Management Chart</h1>
<div class="sub" id="sub">A carregar...</div>

<div class="cards" id="kpis"></div>
<div id="faseCard"></div>
<div id="alertas"></div>

<h2>Fitness, fadiga e forma</h2>
<div class="sub">CTL 42 dias &middot; ATL 7 dias &middot; TSB = CTL &minus; ATL do dia anterior</div>
<div class="controls">
  <label class="sel">Janela
    <select id="janelaPMC">
      <option value="90">90 dias</option>
      <option value="180">6 meses</option>
      <option value="365" selected>1 ano</option>
      <option value="0">Tudo</option>
    </select></label>
</div>
<div class="chartbox">
  <div class="legend" id="lgPMC"></div>
  <canvas id="chPMC" height="320"></canvas>
</div>

<h2>CTL&gamma; — FTLM fraccionario</h2>
<div class="sub" id="subFTLM"></div>
<div class="chartbox">
  <div class="legend" id="lgFTLM"></div>
  <canvas id="chFTLM" height="280"></canvas>
</div>

<h2>CTL&gamma; por modalidade</h2>
<div class="sub">Cada modalidade tem o seu &gamma;, ajustado aos proprios dados</div>
<div class="chartbox">
  <div class="legend" id="lgCTLg"></div>
  <canvas id="chCTLg" height="240"></canvas>
</div>
<div class="wrap" style="max-height:280px;margin-bottom:14px"><table>
  <thead><tr id="gHead"></tr></thead><tbody id="gBody"></tbody></table></div>

<h2>FMT — tensor metrico de fadiga</h2>
<div class="sub" id="subFMT"></div>
<div class="chartbox">
  <div class="legend" id="lgFMT"></div>
  <canvas id="chFMT" height="220"></canvas>
</div>

<h2>CTL por modalidade (classico)</h2>
<div class="chartbox">
  <div class="legend" id="lgMod"></div>
  <canvas id="chMod" height="240"></canvas>
</div>

<h2>Wellness</h2>
<div class="sub" id="subW"></div>
<div class="toggles" id="togW"></div>
<div class="chartbox">
  <div class="legend" id="lgW"></div>
  <canvas id="chW" height="260"></canvas>
</div>

<h2>Composicao corporal e nutricao</h2>
<div class="sub" id="subC"></div>
<div class="toggles" id="togC"></div>
<div class="chartbox">
  <div class="legend" id="lgC"></div>
  <canvas id="chC" height="260"></canvas>
</div>

<div class="sub" style="margin-top:20px">
  <a href="/api/pmc" target="_blank">JSON</a> &middot;
  <a href="/api/debug/sheets" target="_blank">Diagnostico dos Google Sheets</a>
</div>
"""

JS = r"""
let D=null;
const COR={ctl:'#5DADE2',atl:'#E74C3C',tsb:'#2ECC71',load:'#30363d'};
const CORW={hrv:'#5DADE2',rhr:'#E74C3C',sleep_hours:'#AF7AC5',
 sleep_quality:'#58D68D',stress:'#E67E22',fatiga:'#F4D03F',
 humor:'#48C9B0',soreness:'#EC7063',hf_power:'#7FB3D5'};
const LBLW={hrv:'HRV (rMSSD)',rhr:'HR repouso',sleep_hours:'Horas de sono',
 sleep_quality:'Qualidade do sono',stress:'Stress',fatiga:'Cansaco',
 humor:'Humor',soreness:'Dores musculares',hf_power:'HF power'};
const CORC={peso:'#5DADE2',bf:'#E74C3C',calorias:'#F4D03F',
 carb:'#58D68D',fat:'#E67E22',ptn:'#AF7AC5',net:'#48C9B0'};
const LBLC={peso:'Peso (kg)',bf:'Gordura (%)',calorias:'Calorias',
 carb:'Carboidratos (g)',fat:'Gordura (g)',ptn:'Proteina (g)',net:'Net'};
let ATIVW={},ATIVC={};

function janelaPMC(arr){
 const n=parseInt(document.getElementById('janelaPMC').value,10);
 return (n>0&&arr.length>n)?arr.slice(-n):arr;
}

// linhas sobre um eixo comum, com barras de carga por tras
function drawLinhas(canvasId,legendId,dados,series,cores,labels,opcoes){
 opcoes=opcoes||{};
 const o=ctx(canvasId,opcoes.height||300); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 const ativas=series.filter(s=>dados.some(d=>d[s]!=null));
 if(legendId)document.getElementById(legendId).innerHTML=ativas.map(s=>
  '<span class="tog'+(opcoes.off&&opcoes.off[s]?' off':'')+'" data-c="'+canvasId+
  '" data-k="'+s+'"><i style="background:'+(cores[s]||'#8b949e')+'"></i>'+
  ((labels&&labels[s])||s)+'</span>').join('');
 if(!dados.length){noData(g,W,H);return;}

 const vis=ativas.filter(s=>!(opcoes.off&&opcoes.off[s]));
 const PL=48,PR=48,PT=14,PB=28,w=W-PL-PR,h=H-PT-PB;
 const n=dados.length;
 const X=i=>PL+w*(n>1?i/(n-1):0.5);

 // bandas de fase ao fundo, para ler o contexto de cada periodo
 if(opcoes.fases&&D&&D.ftlm){
  const leg=D.ftlm.fases_legenda||{};
  let ini=0;
  for(let i=1;i<=dados.length;i++){
   const mudou=(i===dados.length)||(dados[i].fase!==dados[ini].fase);
   if(!mudou)continue;
   const f=leg[dados[ini].fase];
   if(f&&dados[ini].fase!=='TRANSITION'){
    g.fillStyle=hexRgba(f.cor,0.10);
    g.fillRect(X(ini),PT,Math.max(1,X(i-1)-X(ini)),h);}
   ini=i;}
 }
 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}

 // barras de carga diaria, escala propria, ao fundo
 if(opcoes.barras&&dados.some(d=>d[opcoes.barras])){
  const bv=dados.map(d=>d[opcoes.barras]||0);
  const bmx=Math.max.apply(null,bv)||1;
  const bw=Math.max(1,w/n*0.7);
  g.fillStyle='rgba(88,101,116,0.45)';
  dados.forEach(function(d,i){
   const v=d[opcoes.barras]||0; if(!v)return;
   const bh=h*0.32*v/bmx;
   g.fillRect(X(i)-bw/2,PT+h-bh,bw,bh);});
 }

 if(!vis.length){noData(g,W,H,'Todas as series desligadas');return;}

 // escala partilhada (TSB pode ser negativo)
 let mn=Infinity,mx=-Infinity;
 vis.forEach(s=>dados.forEach(function(d){
  const v=d[s]; if(v==null)return;
  if(v<mn)mn=v; if(v>mx)mx=v;}));
 if(!isFinite(mn)){noData(g,W,H);return;}
 if(mn>0)mn=0;
 if(mx===mn)mx=mn+1;
 const Y=v=>PT+h-(v-mn)/(mx-mn)*h;

 if(mn<0){                      // linha do zero, para o TSB
  g.strokeStyle='#484f58';g.setLineDash([3,3]);g.beginPath();
  g.moveTo(PL,Y(0));g.lineTo(PL+w,Y(0));g.stroke();g.setLineDash([]);}

 vis.forEach(function(s){
  g.strokeStyle=cores[s]||'#8b949e';g.lineWidth=1.8;g.beginPath();
  let st=false;
  dados.forEach(function(d,i){
   const v=d[s]; if(v==null){st=false;return;}
   const x=X(i),y=Y(v);
   if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);});
  g.stroke();});

 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++){const v=mx-(mx-mn)*i/4;
  g.fillText(Math.abs(v)>=100?Math.round(v):v.toFixed(1),PL-6,PT+h*i/4+3);}
 g.textAlign='center';
 const step=Math.ceil(n/8);
 dados.forEach(function(d,i){if(i%step!==0)return;
  g.fillText((d.date||'').slice(0,7),X(i),H-8);});
 g.textAlign='left';

 registarTip(canvasId,function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  if(x<PL||x>PL+w)return '';
  const i=Math.round((x-PL)/w*(n-1));
  if(i<0||i>=n)return '';
  const d=dados[i];
  let html='<div class="th">'+d.date+'</div>';
  vis.forEach(function(s){
   if(d[s]==null)return;
   html+=linhaTip(cores[s]||'#8b949e',(labels&&labels[s])||s,
    (Math.abs(d[s])>=100?Math.round(d[s]):d[s].toFixed(1)));});
  if(opcoes.barras&&d[opcoes.barras])
   html+=linhaTip('#586574','Carga',Math.round(d[opcoes.barras]));
  if(opcoes.fases&&d.fase&&D.ftlm){
   const f=(D.ftlm.fases_legenda||{})[d.fase];
   if(f)html+='<div class="tr" style="border-top:1px solid #30363d;margin-top:4px;'+
    'padding-top:4px"><span>Fase</span><b style="color:'+f.cor+'">'+f.label+'</b></div>';
   if(d.dctlg!=null)html+=linhaTip('#8b949e','ΔCTLγ',d.dctlg.toFixed(4)+'/d');}
  if(opcoes.estado&&d.tsb!=null){
   const e=estadoDe(d.tsb);
   html+='<div class="tr" style="border-top:1px solid #30363d;margin-top:4px;'+
    'padding-top:4px"><span>Forma</span><b style="color:'+e.cor+'">'+e.label+'</b></div>';}
  return html;});

 // clicar na legenda liga/desliga
 if(legendId)document.querySelectorAll('#'+legendId+' span.tog').forEach(function(sp){
  sp.onclick=function(){
   opcoes.off[sp.dataset.k]=!opcoes.off[sp.dataset.k];
   if(opcoes.redraw)opcoes.redraw();};});
}

function estadoDe(tsb){
 if(tsb>25)return{label:'Muito fresco',cor:'#5DADE2'};
 if(tsb>5)return{label:'Fresco',cor:'#2ECC71'};
 if(tsb>-10)return{label:'Neutro',cor:'#F4D03F'};
 if(tsb>-30)return{label:'Em carga',cor:'#E67E22'};
 return{label:'Muito carregado',cor:'#E74C3C'};
}

let OFFP={},OFFM={},OFFF={},OFFG={},OFFK={};
function hexRgba(h,a){h=h.replace('#','');
 return 'rgba('+parseInt(h.slice(0,2),16)+','+parseInt(h.slice(2,4),16)+','+
  parseInt(h.slice(4,6),16)+','+a+')';}

function drawFTLM(){
 if(!D.ftlm){const o=ctx('chFTLM',280);if(o)noData(o.g,o.W,o.H,'FTLM indisponivel');return;}
 const s=janelaPMC(D.ftlm.serie);
 drawLinhas('chFTLM','lgFTLM',s,['ctlg_perf','ctlg_rec'],
  {ctlg_perf:'#5DADE2',ctlg_rec:'#AF7AC5'},
  {ctlg_perf:'CTLγ perf',ctlg_rec:'CTLγ rec'},
  {off:OFFF,redraw:drawFTLM,height:280,fases:true});
}
function drawCTLg(){
 const pm=(D.ftlm||{}).por_modalidade||{};
 const mods=Object.keys(pm);
 if(!mods.length){const o=ctx('chCTLg',240);if(o)noData(o.g,o.W,o.H);return;}
 const porData={};
 mods.forEach(function(m){
  (pm[m].serie||[]).forEach(function(r){
   porData[r.date]=porData[r.date]||{date:r.date};
   porData[r.date][m]=r.ctlg;});});
 drawLinhas('chCTLg','lgCTLg',janelaPMC(Object.keys(porData).sort().map(k=>porData[k])),
  mods,D.cores,null,{off:OFFG,redraw:drawCTLg,height:240});
}
function drawFMT(){
 if(!D.ftlm){return;}
 drawLinhas('chFMT','lgFMT',janelaPMC(D.ftlm.serie),['kappa','lambda1'],
  {kappa:'#E74C3C',lambda1:'#F4D03F'},
  {kappa:'κ (instabilidade)',lambda1:'λ₁ (dominancia)'},
  {off:OFFK,redraw:drawFMT,height:220});
}
function tabelaGammas(){
 const pm=(D.ftlm||{}).por_modalidade||{};
 const mods=Object.keys(pm);
 document.getElementById('gHead').innerHTML=
  ['Modalidade','γ','R²','n','Sessoes','CTLγ actual','Fase']
   .map((c,i)=>'<th class="'+(i&&i<6?'num':'')+'">'+c+'</th>').join('');
 const leg=(D.ftlm||{}).fases_legenda||{};
 document.getElementById('gBody').innerHTML=mods.map(function(m){
  const v=pm[m], f=leg[v.fase]||{};
  return '<tr><td style="color:'+(D.cores[m]||'#e6e6e6')+'">'+m+'</td>'+
   '<td class="num">'+v.gamma+'</td><td class="num">'+v.r2+'</td>'+
   '<td class="num">'+v.n+'</td><td class="num">'+v.n_sessoes+'</td>'+
   '<td class="num">'+v.ctlg_actual+'</td>'+
   '<td style="color:'+(f.cor||'#8b949e')+'">'+(f.label||v.fase)+'</td></tr>';
 }).join('');
}
function drawPMC(){
 drawLinhas('chPMC','lgPMC',janelaPMC(D.serie),['ctl','atl','tsb'],COR,
  {ctl:'CTL (fitness)',atl:'ATL (fadiga)',tsb:'TSB (forma)'},
  {barras:'load',estado:true,off:OFFP,redraw:drawPMC,height:320});
}
function drawMod(){
 const mods=Object.keys(D.por_modalidade||{});
 if(!mods.length){const o=ctx('chMod',240);if(o)noData(o.g,o.W,o.H);return;}
 const porData={};
 mods.forEach(function(m){
  (D.por_modalidade[m]||[]).forEach(function(r){
   porData[r.date]=porData[r.date]||{date:r.date};
   porData[r.date][m]=r.ctl;});});
 const dados=janelaPMC(Object.keys(porData).sort().map(k=>porData[k]));
 drawLinhas('chMod','lgMod',dados,mods,D.cores,null,
  {off:OFFM,redraw:drawMod,height:240});
}
function drawW(){
 const s=Object.keys(CORW).filter(k=>ATIVW[k]);
 drawLinhas('chW','lgW',janelaPMC(D.wellness),s,CORW,LBLW,
  {off:{},redraw:drawW,height:260});
}
function drawC(){
 const s=Object.keys(CORC).filter(k=>ATIVC[k]);
 drawLinhas('chC','lgC',janelaPMC(D.corporal),s,CORC,LBLC,
  {off:{},redraw:drawC,height:260});
}

function togglesDe(dados,cores,labels,ativo,elId,fn){
 const presentes=Object.keys(cores).filter(k=>dados.some(d=>d[k]!=null));
 presentes.forEach(function(k,i){ if(!(k in ativo)) ativo[k]=i<2; });
 document.getElementById(elId).innerHTML=presentes.map(k=>
  '<label style="color:'+cores[k]+'"><input type="checkbox" data-k="'+k+'" '+
  (ativo[k]?'checked':'')+'> '+(labels[k]||k)+'</label>').join('')
  || '<span class="sub">sem dados</span>';
 document.querySelectorAll('#'+elId+' input').forEach(function(cb){
  cb.onchange=function(){ativo[cb.dataset.k]=cb.checked;fn();};});
 return presentes;
}

async function load(){
 let d;
 try{ d=await fetch('/api/pmc').then(r=>r.json()); }
 catch(e){ document.getElementById('sub').innerHTML=
   '<span class="err">Nao consegui carregar</span>'; return; }
 if(d.error){ document.getElementById('sub').innerHTML=
   '<span class="err">'+d.error+'</span>'; return; }
 D=d;

 const s=d.serie||[];
 document.getElementById('sub').textContent=
  s.length+' dias, de '+(s[0]||{}).date+' a '+(s[s.length-1]||{}).date;

 const a=d.actual||{},e=a.estado||{};
 document.getElementById('kpis').innerHTML=[
  ['CTL (fitness)',a.ctl,'#5DADE2'],
  ['ATL (fadiga)',a.atl,'#E74C3C'],
  ['TSB (forma)',a.tsb,e.cor||'#2ECC71'],
  ['Estado',e.label||'—',e.cor||'#8b949e'],
  ['Ramp 7d',a.ramp,(a.ramp>8?'#E67E22':'#5DADE2')],
  ['Sessoes',(d.sessoes||[]).length,'#5DADE2']
 ].map(k=>'<div class="card"><div class="label">'+k[0]+'</div>'+
  '<div class="value" style="color:'+k[2]+'">'+(k[1]==null?'—':k[1])+'</div></div>').join('');

 document.getElementById('alertas').innerHTML=(d.alertas||[]).map(function(al){
  const c=al.nivel==='aviso'?'#E67E22':'#5DADE2';
  return '<div style="border-left:3px solid '+c+';background:#161b22;'+
   'padding:9px 12px;margin-bottom:8px;border-radius:0 6px 6px 0;font-size:13px">'+
   al.texto+'</div>';}).join('');

 drawPMC(); drawMod(); drawFTLM();

 // ── fase actual, com ΔCTLγ e HRV em sigma ──
 const F=d.ftlm;
 if(d.erro_ftlm){
  document.getElementById('faseCard').innerHTML=
   '<div class="err" style="margin-bottom:10px">FTLM: '+d.erro_ftlm+'</div>';
 } else if(F&&F.fase_actual){
  const fa=F.fase_actual, fg=F.fase_global;
  const seta=(fa.dctlg>0?'&uarr;':'&darr;');
  const dv=fa.dctlg==null?'—':Math.abs(fa.dctlg).toFixed(4)+'/d';
  const hz=fa.hrv_z==null?'':' | HRV '+(fa.hrv_z>=0?'+':'')+fa.hrv_z.toFixed(2)+'&sigma;';
  let html='<div style="background:'+hexRgba(fa.cor,0.10)+';border-left:4px solid '+
   fa.cor+';padding:9px 14px;border-radius:0 5px 5px 0;margin-bottom:8px">'+
   '<b>Fase actual:</b> '+fa.label+' — '+fa.desc+'<br>'+
   '<small style="color:#8b949e">'+fa.dias+'d nesta fase | &Delta;CTL&gamma; '+
   seta+dv+hz+'</small></div>';
  if(fg&&fg.codigo!==fa.codigo){
   const ctb=Object.keys(fg.contribuicoes||{})
     .map(m=>m+' '+Math.round(fg.contribuicoes[m]*100)+'%').join(' · ');
   html+='<div style="background:'+hexRgba(fg.cor,0.10)+';border-left:4px solid '+
    fg.cor+';padding:9px 14px;border-radius:0 5px 5px 0;margin-bottom:8px">'+
    '<b>Fase global ponderada (por CTL&gamma;):</b> '+fg.label+'<br>'+
    '<small style="color:#8b949e">'+ctb+'</small></div>';}
  document.getElementById('faseCard').innerHTML=html;

  const g=F.gammas||{};
  document.getElementById('subFTLM').innerHTML=
   'Kernel Riemann-Liouville: CTL&gamma;(t) = &Sigma; Load(t&minus;k)&middot;k<sup>&gamma;&minus;1</sup>/&Gamma;(&gamma;) &middot; '+
   '&gamma;<sub>perf</sub> '+(g.perf?g.perf.gamma+' (R&sup2; '+g.perf.r2+')':'—')+
   ' &middot; &gamma;<sub>rec</sub> '+(g.rec?g.rec.gamma+' (R&sup2; '+g.rec.r2+')':'—');

  drawCTLg(); tabelaGammas(); drawFMT();
  const fm=F.fmt||{};
  document.getElementById('subFMT').innerHTML=
   '&kappa;(t) = trace(cov(&Delta;x)) em janela de 28d sobre '+
   (fm.dimensoes||[]).length+' dimensoes: '+(fm.dimensoes||[]).join(', ')+
   '. &kappa; alto = sistema a oscilar mais.';
 }

 // wellness
 const w=d.wellness||[];
 if(!d.sheets_ok||!w.length){
  const msg = !d.sheets_ok
    ? 'Google Sheets nao ligado — define GCP_SERVICE_ACCOUNT. Ver /api/debug/sheets'
    : ((d.erros_sheets&&d.erros_sheets.wellness)||'Sem dados de wellness');
  document.getElementById('subW').innerHTML='<span class="err">'+msg+'</span>';
 } else {
  document.getElementById('subW').textContent=
   w.length+' dias · escalas 1 a 5 (5 = melhor): sono, stress, cansaco, humor, dores';
  togglesDe(w,CORW,LBLW,ATIVW,'togW',drawW); drawW();
 }

 // corporal
 const c=d.corporal||[];
 if(!c.length){
  document.getElementById('subC').innerHTML='<span class="err">'+
   ((d.erros_sheets&&d.erros_sheets.corporal)||'Sem dados corporais')+'</span>';
 } else {
  document.getElementById('subC').textContent=c.length+' dias';
  togglesDe(c,CORC,LBLC,ATIVC,'togC',drawC); drawC();
 }
}
function redesenhar(){
 if(!D)return;
 drawPMC();drawMod();drawW();drawC();
 if(D.ftlm){drawFTLM();drawCTLg();drawFMT();}}
document.getElementById('janelaPMC').onchange=redesenhar;
window.addEventListener('resize',redesenhar);
load();
"""


def render():
    return page('PMC', SLUG, BODY, JS)
