"""tab_recovery.py — prescrição diária a partir do HRV.

Cinco modelos, mas não cinco opiniões independentes: quatro saem todos do
LnRMSSD da manhã. A tab é construída à volta dessa distinção — a síntese
pesa por FAMÍLIA, e cada modelo mostra-se por si num dropdown.
"""

from tabs.base import page

SLUG = 'recovery'

BODY = """
<div class="wrap">

<h1>Recovery</h1>
<div class="controls">
  <label class="sel">Janela
    <select id="rcDias" onchange="rcCarregar()">
      <option value="90">90 dias</option>
      <option value="180" selected>180 dias</option>
      <option value="365">1 ano</option>
      <option value="730">2 anos</option>
    </select>
  </label>
  <span id="rcEstado" class="sub"></span>
</div>

<!-- a resposta do dia -->
<div id="rcSintese"></div>

<!-- a persistência: há quanto tempo se está assim -->
<div id="rcPersistencia"></div>

<h2 style="font-size:15px;margin-top:18px;">LnRMSSD e a banda</h2>
<div class="chartbox">
  <canvas id="rcCanvas" height="300"></canvas>
</div>
<div id="rcLegenda"></div>

<h2 style="font-size:15px;margin-top:18px;">Os modelos, um a um</h2>
<div id="rcModelos"></div>

<h2 style="font-size:15px;margin-top:18px;">O que anda com o quê</h2>
<div id="rcCorrelacoes"></div>

<h3 style="font-size:14px;margin-top:14px;">E qual dos modelos acompanha melhor o treino?</h3>
<div id="rcCorrModelos"></div>

<div id="rcCobertura"></div>

</div>
"""

JS = r"""
let RC = null;

function rcCarregar(){
 const est=document.getElementById('rcEstado');
 est.textContent='a carregar...';
 const d=document.getElementById('rcDias').value;
 fetch('/api/recovery/dados?dias='+d).then(r=>r.json()).then(function(x){
  RC=x;
  if(x.status!=='ok'){
   est.textContent=x.motivo||x.mensagem||'sem dados';
   document.getElementById('rcSintese').innerHTML='';
   return;
  }
  const p=x.periodo;
  est.textContent=p.n_com_hrv+' de '+p.n_dias+' dias com HRV ('
   +p.cobertura_pct+'%) · '+p.de+' a '+p.ate;
  rcSintese(); rcPersistencia(); rcGrafico(); rcModelos();
  rcCorrelacoes(); rcCobertura();
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

// ── a resposta do dia ────────────────────────────────────────────────
function rcSintese(){
 const box=document.getElementById('rcSintese');
 const s=(RC||{}).sintese||{};
 if(!s.ok){ box.innerHTML='<p class="sub">'+(s.motivo||'')+'</p>'; return; }
 // sem medições recentes: dizer isso, e não o LOW por omissão da máquina
 // de estados — que é indistinguível de um LOW medido
 if(s.sem_dados){
  const q=s.qualidade||{};
  box.innerHTML='<div style="border:1px solid #F0883E;border-radius:6px;'
   +'padding:10px 12px;margin:8px 0;">'
   +'<b style="font-size:18px;color:#F0883E;">Sem prescrição para hoje</b>'
   +'<br><span style="font-size:12px;">'+s.leitura+'</span>'
   +'<br><span class="sub" style="font-size:11px;">'
   +q.n_recentes+' de '+q.janela+' dias com medição'
   +(q.dias_desde_ultima!=null
     ? ' · última há '+q.dias_desde_ultima+' dia(s)':'')+'</span>'
   +(s.motivos||[]).map(function(m){
     return '<p style="color:#F0883E;font-size:11px;margin:6px 0 0 0;">⚠ '
      +m+'</p>'; }).join('')
   +'<p style="font-size:11px;margin:6px 0 0 0;"><b>'+(s.o_que_fazer||'')
   +'</b></p>'
   +'<p class="sub" style="font-size:10px;margin-top:6px;">'+s.nota+'</p>'
   +'</div>';
  return;
 }
 const cor = s.estado==='carga' ? '#3FB950'
           : s.estado==='moderado' ? '#F0883E' : '#F85149';
 const rot = s.estado==='carga' ? 'Treinar forte'
           : s.estado==='moderado' ? 'Treino moderado' : 'Recuar';
 let h='<div style="border:1px solid '+cor+';border-radius:6px;'
  +'padding:10px 12px;margin:8px 0;">'
  +'<b style="font-size:20px;color:'+cor+';">'+rot+'</b> '
  +'<span class="sub">score '+s.score+'</span>'
  +'<br><span style="font-size:12px;">'+s.leitura+'</span>';

 // as famílias, com o peso de cada uma à vista
 h+='<table style="border-collapse:collapse;font-size:11px;margin-top:8px;">'
  +'<tr class="sub" style="text-align:left;">'
  +'<th style="padding-right:14px;">Família</th>'
  +'<th style="padding-right:14px;">Voto</th>'
  +'<th style="padding-right:14px;">Peso</th>'
  +'<th>Modelos</th></tr>';
 const nomes={lnrmssd:'LnRMSSD (rMSSD da manhã)', hf:'HF power',
              subjectivo:'Wellness (folha)'};
 Object.keys(s.familias||{}).forEach(function(k){
  const f=s.familias[k];
  const c = f.voto>0.3 ? '#3FB950' : (f.voto<-0.3 ? '#F85149' : '#8b949e');
  h+='<tr><td style="padding-right:14px;">'+(nomes[k]||k)+'</td>'
   +'<td style="padding-right:14px;color:'+c+';"><b>'
   +(f.voto>0?'+':'')+f.voto.toFixed(2)+'</b></td>'
   +'<td style="padding-right:14px;" class="sub">'
   +Math.round((s.pesos_usados[k]||0)*100)+'%</td>'
   +'<td class="sub">'+f.n_modelos
   +(f.unanime?' · unânime':' · dispersão '+f.dispersao)+'</td></tr>';
 });
 h+='</table>';
 (s.avisos||[]).forEach(function(a){
  h+='<p style="color:#F0883E;font-size:11px;margin:6px 0 0 0;">⚠ '+a
   +'</p>'; });
 h+='<p class="sub" style="font-size:10px;margin-top:6px;">'+s.nota+'</p>'
  +'</div>';

 // cada modelo, numa linha
 h+='<table style="width:100%;border-collapse:collapse;font-size:11px;">'
  +'<tr class="sub" style="text-align:left;border-bottom:1px solid #21262d;">'
  +'<th style="padding:5px;">Modelo</th><th>Família</th><th>Hoje</th>'
  +'<th>Valor</th><th>Voto</th></tr>'
  +(s.detalhe||[]).map(function(d){
    const c = d.voto>0 ? '#3FB950' : (d.voto<0 ? '#F85149' : '#8b949e');
    return '<tr style="border-bottom:1px solid #161b22;">'
     +'<td style="padding:5px;">'+d.modelo+'</td>'
     +'<td class="sub">'+(nomes[d.familia]||d.familia)+'</td>'
     +'<td><b>'+d.estado+'</b></td>'
     +'<td class="sub">'+rcValorHoje(d.modelo)+'</td>'
     +'<td style="color:'+c+';">'+(d.voto>0?'+':'')+d.voto+'</td></tr>';
   }).join('')
  +'</table>';
 box.innerHTML=h;
}

// O número que produziu o estado de hoje. Sem isto a tabela diz "abaixo"
// e não diz abaixo de quê, nem por quanto.
function rcValorHoje(modelo){
 const ult=a=>{ for(let i=(a||[]).length-1;i>=0;i--)
   if(a[i]!=null) return a[i]; return null; };
 const f=(v,d)=>v==null?'—':(+v).toFixed(d==null?3:d);
 if(modelo==='Plews'){
  const m=RC.swc||{};
  return f(ult(m.ln7))+' vs '+f(ult(m.swc_inf))+'–'+f(ult(m.swc_sup));
 }
 if(modelo==='Altini'){
  const a=RC.altini||{};
  const v=ult(RC.lnrmssd), b=ult(a.baseline), s=ult(a.sd);
  return (v!=null&&b!=null&&s)
    ? f(v)+' vs '+f(b)+' ('+((v-b)/s).toFixed(2)+' SD)' : '—';
 }
 if(modelo==='Javaloyes'){
  const j=RC.javaloyes||{};
  return f(ult(j.ln7))+' vs '+f(ult(j.swc_inf))+'–'+f(ult(j.swc_sup));
 }
 if(modelo==='Kiviniemi'){
  const k=RC.kiviniemi||{};
  return f(ult(k.hf))+' vs ref '+f(ult(k.referencia));
 }
 if(modelo==='PSlope'){
  const p=RC.pslope||{};
  return f(p.declive_actual,4)+' · '+p.dias_na_zona+'d na zona';
 }
 if(modelo==='Modelo β'){
  const b=RC.beta||{};
  return 'agudo '+f(b.agudo_hoje,1)+' · crónico '+f(b.cronico_hoje,1);
 }
 if(modelo==='Wellness'){
  const wl=RC.wellness||{};
  return (wl.n_campos||0)+' campos';
 }
 return '—';
}

// ── persistência: fica FORA dos dropdowns ────────────────────────────
// É o único modelo que responde a "há quanto tempo", e isso muda a
// leitura de tudo o resto: fadiga há um dia é o efeito de um treino,
// fadiga há cinco dias é outra coisa.
function rcPersistencia(){
 const box=document.getElementById('rcPersistencia');
 const p=(RC||{}).pslope||{};
 if(!p.ok){ box.innerHTML='<p class="sub">Persistência: '
   +(p.motivo||'')+'</p>'; return; }
 const l=p.leitura||{};
 const mau = ['Fadiga','NFOR crítico'].indexOf(p.zona_actual)>=0;
 const cor = mau ? '#F85149'
   : (['Recuperação','Supercompensação'].indexOf(p.zona_actual)>=0
      ? '#3FB950' : '#8b949e');
 let h='<div style="border-left:3px solid '+cor+';padding:8px 10px;'
  +'margin:8px 0;">'
  +'<b style="color:'+cor+';font-size:15px;">'+p.zona_actual+'</b> '
  +'<span class="sub">há '+p.dias_na_zona+' dia(s)'
  +(p.vs_media_historica?' · '+p.vs_media_historica+'× a tua média':'')
  +'</span>'
  +'<br><span style="font-size:12px;">'+(l.significa||'')+'</span>'
  +'<br><span class="sub" style="font-size:11px;">'+(l.contexto||'')+'</span>';
 if(l.aviso)
  h+='<p style="color:#F0883E;font-size:11px;margin:6px 0 0 0;">⚠ '+l.aviso
   +'</p>';
 box.innerHTML=h+'</div>';
}

// ── gráfico principal: LnRMSSD, banda SWC e estados ─────────────────
//
// Uma só figura para a família toda do LnRMSSD. Quatro gráficos separados
// dariam a impressão de quatro fontes; é o mesmo número visto de quatro
// maneiras, e vê-se melhor sobreposto.
function rcGrafico(){
 const cv=document.getElementById('rcCanvas');
 if(!cv) return;
 const dpr=window.devicePixelRatio||1;
 const larg=cv.parentNode.clientWidth||820;
 cv.width=larg*dpr; cv.height=300*dpr;
 cv.style.width='100%'; cv.style.height='300px';
 const g=cv.getContext('2d'); g.setTransform(dpr,0,0,dpr,0,0);
 const W=larg, H=300, PL=48, PR=14, PT=12, PB=26;
 const w=W-PL-PR, h=H-PT-PB;
 g.clearRect(0,0,W,H);

 const sw=(RC||{}).swc||{};
 const ln7=sw.ln7||[], sup=sw.swc_sup||[], inf=sw.swc_inf||[];
 const datas=RC.datas||[];
 const n=datas.length;
 if(!n){ return; }

 // escala: tudo o que vai ser desenhado
 let mn=null, mx=null;
 [ln7,sup,inf].forEach(function(s2){
  (s2||[]).forEach(function(v){
   if(v==null) return;
   if(mn===null||v<mn) mn=v; if(mx===null||v>mx) mx=v; }); });
 if(mn===null){ return; }
 const marg=(mx-mn)*0.12||0.1; mn-=marg; mx+=marg;
 const X=i=>PL+w*i/(n-1);
 const Y=v=>PT+h-(v-mn)/(mx-mn)*h;

 // Estados como FUNDO, e não como tira: a prescrição é o contexto em
 // que a linha se lê, não mais um dado ao lado dela. Pintar por trás
 // deixa ver o LnRMSSD contra a banda sem competir por espaço.
 const jpF=((RC||{}).javaloyes||{}).prescricao||[];
 if(jpF.length===n){
  const larguraCol=w/(n-1);
  let ini=0;
  for(let i=1;i<=n;i++){
   // pintar blocos contíguos do mesmo estado, não coluna a coluna:
   // 200 rectângulos com bordas produziam faixas visíveis
   if(i<n && jpF[i]===jpF[ini]) continue;
   const p=jpF[ini];
   if(p){
    const c = p==='HIGH'?'rgba(63,185,80,'
            : p==='REST'?'rgba(248,81,73,' : 'rgba(139,148,158,';
    g.fillStyle=c+(p==='LOW'?'0.06':'0.13')+')';
    g.fillRect(X(ini)-larguraCol/2, PT,
               (i-ini)*larguraCol, h);
   }
   ini=i;
  }
 }

 // grelha
 g.strokeStyle='#21262d'; g.lineWidth=1;
 for(let i=0;i<=4;i++){ const y=PT+h*i/4;
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke(); }

 // banda SWC preenchida
 let st=false;
 g.beginPath();
 for(let i=0;i<n;i++){ const v=sup[i];
  if(v==null){ st=false; continue; }
  st?g.lineTo(X(i),Y(v)):(g.moveTo(X(i),Y(v)),st=true); }
 for(let i=n-1;i>=0;i--){ const v=inf[i];
  if(v==null) continue; g.lineTo(X(i),Y(v)); }
 g.closePath(); g.fillStyle='rgba(93,173,226,0.12)'; g.fill();

 // limites da banda
 [[sup,'#5DADE2'],[inf,'#5DADE2']].forEach(function(par){
  g.strokeStyle=par[1]; g.globalAlpha=0.5; g.lineWidth=1;
  g.setLineDash([4,3]); g.beginPath(); let s3=false;
  for(let i=0;i<n;i++){ const v=par[0][i];
   if(v==null){ s3=false; continue; }
   s3?g.lineTo(X(i),Y(v)):(g.moveTo(X(i),Y(v)),s3=true); }
  g.stroke(); g.setLineDash([]); g.globalAlpha=1; });

 // LnRMSSD 7d
 g.strokeStyle='#c9d1d9'; g.lineWidth=1.8; g.beginPath(); st=false;
 for(let i=0;i<n;i++){ const v=ln7[i];
  if(v==null){ st=false; continue; }
  st?g.lineTo(X(i),Y(v)):(g.moveTo(X(i),Y(v)),st=true); }
 g.stroke();

 // eixos
 g.fillStyle='#8b949e'; g.font='10px sans-serif'; g.textAlign='right';
 for(let i=0;i<=4;i++){ const v=mx-(mx-mn)*i/4;
  g.fillText(v.toFixed(2), PL-6, PT+h*i/4+3); }
 g.textAlign='center';
 for(let i=0;i<=5;i++){ const k=Math.round((n-1)*i/5);
  g.fillText((datas[k]||'').slice(5), X(k), H-8); }
 g.textAlign='left';

 rcHover('rcCanvas', PL, w, n, [
  {rot:'LnRMSSD 7d', v:ln7, cor:'#c9d1d9', dec:3},
  {rot:'banda sup.', v:sup, cor:'#5DADE2', dec:3},
  {rot:'banda inf.', v:inf, cor:'#5DADE2', dec:3},
  {rot:'Javaloyes', v:jpF, cor:'#8b949e'}]);

 document.getElementById('rcLegenda').innerHTML=
  '<p class="sub" style="font-size:11px;">'
  +'<span style="color:#c9d1d9;">━</span> LnRMSSD 7d &nbsp; '
  +'<span style="color:#5DADE2;">▭</span> banda SWC (média₂₈ ± 0,5·SD) &nbsp; '
  +'<br>fundo: <span style="color:#3FB950;">■</span> HIGH '
  +'<span style="color:#8b949e;">■</span> LOW '
  +'<span style="color:#F85149;">■</span> REST'
  +'<br>Uma só figura para a família do LnRMSSD: quatro gráficos '
  +'separados dariam a impressão de quatro fontes independentes.</p>';
}

// ── gráficos dos modelos ─────────────────────────────────────────────
//
// O rcDesenhaMini genérico desenhava linhas sem escala nem referência:
// três séries com significados diferentes no mesmo eixo, e nada a dizer
// o que é "alto". Cada modelo passa a ter o seu, com as marcas que
// tornam o valor legível.

function rcMini(id, series, altura){
 return '<div class="chartbox" style="margin-top:6px;">'
  +'<canvas id="'+id+'" height="'+(altura||150)+'"></canvas></div>';
}

// ── hover ────────────────────────────────────────────────────────────
// Guarda a escala de cada canvas para converter a posição do rato em
// índice de dia. Sem isto não há forma de saber que dia está debaixo do
// cursor.
const RC_ESC={};

function rcHover(id, PL, w, n, linhas){
 RC_ESC[id]={PL:PL, w:w, n:n, linhas:linhas};
 const cv=document.getElementById(id);
 if(!cv || cv.__hv) return;
 cv.__hv=true;
 let tip=document.getElementById(id+'Tip');
 if(!tip){
  tip=document.createElement('div');
  tip.id=id+'Tip';
  tip.style.cssText='display:none;position:absolute;pointer-events:none;'
   +'background:#161b22;border:1px solid #30363d;border-radius:6px;'
   +'padding:6px 9px;font-size:11px;color:#c9d1d9;z-index:5;'
   +'white-space:nowrap;';
  (cv.parentNode||document.body).appendChild(tip);
  if(cv.parentNode) cv.parentNode.style.position='relative';
 }
 cv.addEventListener('mousemove', function(ev){
  const e=RC_ESC[id]; if(!e){ tip.style.display='none'; return; }
  const r=cv.getBoundingClientRect();
  const esc=(cv.width/r.width)/(window.devicePixelRatio||1);
  const mx=(ev.clientX-r.left)*esc;
  if(mx<e.PL||mx>e.PL+e.w){ tip.style.display='none'; return; }
  const i=Math.round((mx-e.PL)/e.w*(e.n-1));
  let h='<b>'+((RC.datas||[])[i]||'')+'</b>';
  e.linhas.forEach(function(l){
   const v=(l.v||[])[i];
   if(v==null) return;
   const txt = typeof v==='number'
     ? (l.dec!=null ? v.toFixed(l.dec) : v) : v;
   h+='<br><span style="color:'+(l.cor||'#8b949e')+';">'+l.rot+'</span> '
    +txt+(l.un||'');
  });
  tip.innerHTML=h; tip.style.display='block';
  tip.style.left=Math.min(ev.clientX-r.left+12, r.width-190)+'px';
  tip.style.top=Math.max(4, ev.clientY-r.top-34)+'px';
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

function _rcCtx(id, alt){
 const cv=document.getElementById(id);
 if(!cv) return null;
 const dpr=window.devicePixelRatio||1;
 const larg=(cv.parentNode&&cv.parentNode.clientWidth)||760;
 if(larg<50) return null;                    // dropdown fechado
 cv.width=larg*dpr; cv.height=alt*dpr;
 cv.style.width='100%'; cv.style.height=alt+'px';
 const g=cv.getContext('2d'); g.setTransform(dpr,0,0,dpr,0,0);
 g.clearRect(0,0,larg,alt);
 return {g:g, W:larg, H:alt};
}

function _rcEixoX(g, X, n, PT, h, H){
 const datas=RC.datas||[];
 g.fillStyle='#8b949e'; g.font='9px sans-serif'; g.textAlign='center';
 for(let i=0;i<=4;i++){ const k=Math.round((n-1)*i/4);
  g.fillText((datas[k]||'').slice(5), X(k), H-6); }
 g.textAlign='left';
}

// ── β: escala fixa 0–100, com as faixas que dão sentido ao número ────
function rcGraficoBeta(){
 const o=_rcCtx('rcBeta',150); if(!o) return;
 const b=(RC||{}).beta||{}; if(!b.ok) return;
 const g=o.g, W=o.W, H=o.H, PL=40, PR=54, PT=10, PB=18;
 const w=W-PL-PR, h=H-PT-PB, n=(RC.datas||[]).length;
 const X=i=>PL+w*i/(n-1);
 // β é um percentil: 0–100 SEMPRE. Escalar ao min-max faria 48 e 52
 // parecerem extremos opostos.
 const Y=v=>PT+h-(v/100)*h;

 // faixas: ≥60 fresco, ≤40 possível fadiga
 g.fillStyle='rgba(63,185,80,0.10)';  g.fillRect(PL,Y(100),w,Y(60)-Y(100));
 g.fillStyle='rgba(248,81,73,0.10)';  g.fillRect(PL,Y(40),w,Y(0)-Y(40));
 g.strokeStyle='#21262d'; g.lineWidth=1;
 [0,25,50,75,100].forEach(function(v){
  g.beginPath(); g.moveTo(PL,Y(v)); g.lineTo(PL+w,Y(v)); g.stroke(); });
 // a mediana pessoal
 g.strokeStyle='#8b949e'; g.setLineDash([3,3]);
 g.beginPath(); g.moveTo(PL,Y(50)); g.lineTo(PL+w,Y(50)); g.stroke();
 g.setLineDash([]);

 g.strokeStyle='#5DADE2'; g.lineWidth=2; g.beginPath(); let st=false;
 for(let i=0;i<n;i++){ const v=(b.beta||[])[i];
  if(v==null){ st=false; continue; }
  st?g.lineTo(X(i),Y(v)):(g.moveTo(X(i),Y(v)),st=true); }
 g.stroke();
 // ponto de hoje
 for(let i=n-1;i>=0;i--){ const v=(b.beta||[])[i];
  if(v==null) continue;
  g.fillStyle='#5DADE2'; g.beginPath(); g.arc(X(i),Y(v),3.5,0,6.284); g.fill();
  break; }

 g.fillStyle='#8b949e'; g.font='9px sans-serif'; g.textAlign='right';
 [0,25,50,75,100].forEach(function(v){ g.fillText(v, PL-5, Y(v)+3); });
 g.textAlign='left'; g.font='9px sans-serif';
 g.fillStyle='#3FB950'; g.fillText('fresco', PL+w+6, Y(80));
 g.fillStyle='#8b949e'; g.fillText('mediana', PL+w+6, Y(50)+3);
 g.fillStyle='#F85149'; g.fillText('fadiga?', PL+w+6, Y(20));
 _rcEixoX(g, X, n, PT, h, H);
}

// ── β agudo e crónico: barras em torno de zero ───────────────────────
// São diferenças, não níveis. Uma linha não mostra o sinal; barras a
// partir do zero mostram.
function rcGraficoBetaTend(){
 const o=_rcCtx('rcBetaTend',120); if(!o) return;
 const b=(RC||{}).beta||{}; if(!b.ok) return;
 const g=o.g, W=o.W, H=o.H, PL=40, PR=54, PT=10, PB=18;
 const w=W-PL-PR, h=H-PT-PB, n=(RC.datas||[]).length;
 let mx=0;
 [b.agudo,b.cronico].forEach(function(s2){ (s2||[]).forEach(function(v){
  if(v!=null && Math.abs(v)>mx) mx=Math.abs(v); }); });
 if(mx<3) mx=3;
 const X=i=>PL+w*i/(n-1);
 const Y=v=>PT+h/2-(v/mx)*(h/2);
 g.strokeStyle='#30363d'; g.lineWidth=1;
 g.beginPath(); g.moveTo(PL,Y(0)); g.lineTo(PL+w,Y(0)); g.stroke();
 const lc=w/n;
 [[b.agudo,'#3FB950',0],[b.cronico,'#F0883E',1]].forEach(function(par){
  g.fillStyle=par[1]; g.globalAlpha=0.55;
  for(let i=0;i<n;i++){ const v=(par[0]||[])[i];
   if(v==null) continue;
   const y=Y(v), y0=Y(0);
   g.fillRect(X(i)-lc/2+par[2]*lc/2, Math.min(y,y0),
              Math.max(1,lc/2), Math.abs(y-y0));
  }
  g.globalAlpha=1;
 });
 g.fillStyle='#8b949e'; g.font='9px sans-serif'; g.textAlign='right';
 [mx,0,-mx].forEach(function(v){
  g.fillText(v.toFixed(0), PL-5, Y(v)+3); });
 g.textAlign='left';
 g.fillStyle='#3FB950'; g.fillText('agudo 3d−7d', PL+w+6, PT+14);
 g.fillStyle='#F0883E'; g.fillText('crónico 7d−28d', PL+w+6, PT+26);
 _rcEixoX(g, X, n, PT, h, H);
 rcHover('rcBetaTend', PL, w, n, [
  {rot:'agudo', v:b.agudo, cor:'#3FB950', dec:1},
  {rot:'crónico', v:b.cronico, cor:'#F0883E', dec:1}]);
}

// ── PSlope: declive com as SEIS zonas pintadas ───────────────────────
// A zona é o que importa, e o número do declive sozinho não a mostra.
function rcGraficoSlope(){
 const o=_rcCtx('rcSlope',170); if(!o) return;
 const p=(RC||{}).pslope||{}; if(!p.ok) return;
 const g=o.g, W=o.W, H=o.H, PL=52, PR=96, PT=10, PB=18;
 const w=W-PL-PR, h=H-PT-PB, n=(RC.datas||[]).length;
 const m=p.media, sd=p.sd;
 const lim=[3,1,0.5,-0.5,-1,-2,-3].map(z=>m+z*sd);
 const mn=lim[lim.length-1], mx=lim[0];
 const X=i=>PL+w*i/(n-1);
 const Y=v=>PT+h-(v-mn)/(mx-mn)*h;

 const zonas=[
  ['Supercompensação','rgba(63,185,80,0.20)',1,3],
  ['Recuperação','rgba(63,185,80,0.12)',0.5,1],
  ['Estável','rgba(139,148,158,0.08)',-0.5,0.5],
  ['Declínio leve','rgba(240,136,62,0.10)',-1,-0.5],
  ['Fadiga','rgba(248,81,73,0.14)',-2,-1],
  ['NFOR crítico','rgba(248,81,73,0.26)',-3,-2]];
 zonas.forEach(function(z){
  const y1=Y(m+z[3]*sd), y2=Y(m+z[2]*sd);
  g.fillStyle=z[1]; g.fillRect(PL,y1,w,y2-y1);
  g.fillStyle='#8b949e'; g.font='9px sans-serif'; g.textAlign='left';
  if(y2-y1>11) g.fillText(z[0], PL+w+6, (y1+y2)/2+3);
 });
 g.strokeStyle='#30363d'; g.setLineDash([3,3]);
 g.beginPath(); g.moveTo(PL,Y(m)); g.lineTo(PL+w,Y(m)); g.stroke();
 g.setLineDash([]);

 g.strokeStyle='#A371F7'; g.lineWidth=2; g.beginPath(); let st=false;
 for(let i=0;i<n;i++){ const v=(p.declive||[])[i];
  if(v==null){ st=false; continue; }
  const vv=Math.max(mn,Math.min(mx,v));
  st?g.lineTo(X(i),Y(vv)):(g.moveTo(X(i),Y(vv)),st=true); }
 g.stroke();
 for(let i=n-1;i>=0;i--){ const v=(p.declive||[])[i];
  if(v==null) continue;
  g.fillStyle='#A371F7'; g.beginPath();
  g.arc(X(i),Y(Math.max(mn,Math.min(mx,v))),3.5,0,6.284); g.fill(); break; }

 g.fillStyle='#8b949e'; g.font='9px sans-serif'; g.textAlign='right';
 [mx,m,mn].forEach(function(v){ g.fillText(v.toFixed(3), PL-5, Y(v)+3); });
 g.textAlign='left';
 _rcEixoX(g, X, n, PT, h, H);
 rcHover('rcSlope', PL, w, n, [
  {rot:'declive', v:p.declive, cor:'#A371F7', dec:4},
  {rot:'zona', v:p.zonas, cor:'#8b949e'}]);
}

// ── SWC: os dias soltos, a média de 7d e a banda ─────────────────────
//
// O gráfico principal mostra a média de 7 dias contra a banda. Aqui
// mostram-se também os PONTOS DIÁRIOS — porque a banda é feita da
// dispersão deles, e só com a média não se vê de onde ela sai.
function rcGraficoSwc(){
 const o=_rcCtx('rcSwc',180); if(!o) return;
 const m=(RC||{}).swc||{}; if(m.ok===false) return;
 const g=o.g, W=o.W, H=o.H, PL=48, PR=14, PT=10, PB=18;
 const w=W-PL-PR, h=H-PT-PB, n=(RC.datas||[]).length;
 const ln=(RC||{}).lnrmssd||[];
 const ln7=m.ln7||[], sup=m.swc_sup||[], inf=m.swc_inf||[];

 let mn=null, mx=null;
 [ln, ln7, sup, inf].forEach(function(s2){ (s2||[]).forEach(function(v){
  if(v==null) return;
  if(mn===null||v<mn) mn=v; if(mx===null||v>mx) mx=v; }); });
 if(mn===null) return;
 const marg=(mx-mn)*0.08||0.1; mn-=marg; mx+=marg;
 const X=i=>PL+w*i/(n-1), Y=v=>PT+h-(v-mn)/(mx-mn)*h;

 // banda
 let st=false; g.beginPath();
 for(let i=0;i<n;i++){ const v=sup[i];
  if(v==null){ st=false; continue; }
  st?g.lineTo(X(i),Y(v)):(g.moveTo(X(i),Y(v)),st=true); }
 for(let i=n-1;i>=0;i--){ const v=inf[i];
  if(v==null) continue; g.lineTo(X(i),Y(v)); }
 g.closePath(); g.fillStyle='rgba(93,173,226,0.14)'; g.fill();

 g.strokeStyle='#21262d'; g.lineWidth=1;
 for(let i=0;i<=3;i++){ const y=PT+h*i/3;
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke(); }

 // Barras a partir da média de 28 dias, não pontos.
 //
 // O ponto mostra onde o dia está; a barra mostra QUANTO se afastou do
 // centro, que é a pergunta. E a altura acumula visualmente: uma série
 // de barras curtas para baixo lê-se de relance, uma nuvem de pontos não.
 const m28=m.media28||[];
 const lb=Math.max(1, Math.min(6, w/n*0.7));
 for(let i=0;i<n;i++){
  const v=ln[i]; if(v==null) continue;
  const centro = m28[i]!=null ? m28[i] : (ln7[i]!=null?ln7[i]:v);
  const lo=inf[i], hi=sup[i];
  let c='#8b949e';
  if(lo!=null) c = v>hi ? '#3FB950' : (v<lo ? '#F85149' : '#8b949e');
  const y=Y(v), y0=Y(centro);
  g.fillStyle=c; g.globalAlpha=0.5;
  g.fillRect(X(i)-lb/2, Math.min(y,y0), lb, Math.max(1,Math.abs(y-y0)));
 }
 g.globalAlpha=1;

 // média de 7 dias
 g.strokeStyle='#c9d1d9'; g.lineWidth=2; g.beginPath(); st=false;
 for(let i=0;i<n;i++){ const v=ln7[i];
  if(v==null){ st=false; continue; }
  st?g.lineTo(X(i),Y(v)):(g.moveTo(X(i),Y(v)),st=true); }
 g.stroke();
 for(let i=n-1;i>=0;i--){ const v=ln7[i];
  if(v==null) continue;
  g.fillStyle='#c9d1d9'; g.beginPath(); g.arc(X(i),Y(v),3.5,0,6.284);
  g.fill(); break; }

 g.fillStyle='#8b949e'; g.font='9px sans-serif'; g.textAlign='right';
 for(let i=0;i<=3;i++){ const v=mx-(mx-mn)*i/3;
  g.fillText(v.toFixed(2), PL-5, PT+h*i/3+3); }
 g.textAlign='left';
 _rcEixoX(g, X, n, PT, h, H);
 rcHover('rcSwc', PL, w, n, [
  {rot:'LnRMSSD', v:ln, cor:'#c9d1d9', dec:3},
  {rot:'média 7d', v:ln7, cor:'#c9d1d9', dec:3},
  {rot:'Plews', v:m.estado, cor:'#5DADE2'},
  {rot:'Altini', v:((RC||{}).altini||{}).estado, cor:'#A371F7'}]);
}

// ── Altini: barras do desvio face à baseline, coloridas pelo estado ──
function rcGraficoAltini(){
 const o=_rcCtx('rcAltini',160); if(!o) return;
 const a=(RC||{}).altini||{}; if(!a.ok) return;
 const g=o.g, W=o.W, H=o.H, PL=48, PR=14, PT=10, PB=18;
 const w=W-PL-PR, h=H-PT-PB, n=(RC.datas||[]).length;
 const ln=(RC||{}).lnrmssd||[];
 // desvio em unidades de SD: é assim que o critério é definido
 const z=[];
 for(let i=0;i<n;i++){
  const v=ln[i], b=(a.baseline||[])[i], s2=(a.sd||[])[i];
  z.push((v==null||b==null||!s2) ? null : (v-b)/s2);
 }
 let mx=1.5;
 z.forEach(function(v){ if(v!=null && Math.abs(v)>mx) mx=Math.abs(v); });
 mx=Math.min(mx,4);
 const X=i=>PL+w*i/(n-1), Y=v=>PT+h/2-(Math.max(-mx,Math.min(mx,v))/mx)*(h/2);

 // faixa do "normal": ±k SD
 g.fillStyle='rgba(139,148,158,0.10)';
 g.fillRect(PL, Y(a.k), w, Y(-a.k)-Y(a.k));
 g.strokeStyle='#30363d'; g.lineWidth=1;
 g.beginPath(); g.moveTo(PL,Y(0)); g.lineTo(PL+w,Y(0)); g.stroke();
 g.strokeStyle='#F85149'; g.globalAlpha=0.4; g.setLineDash([4,3]);
 g.beginPath(); g.moveTo(PL,Y(-a.k)); g.lineTo(PL+w,Y(-a.k)); g.stroke();
 g.setLineDash([]); g.globalAlpha=1;

 const lb=Math.max(1, Math.min(6, w/n*0.7));
 for(let i=0;i<n;i++){
  const v=z[i]; if(v==null) continue;
  const e=(a.estado||[])[i];
  const c = e==='pico' ? '#3FB950'
          : e==='supressão multi-dia' ? '#F85149'
          : e==='supressão' ? '#F0883E' : '#8b949e';
  const y=Y(v), y0=Y(0);
  g.fillStyle=c; g.globalAlpha=0.65;
  g.fillRect(X(i)-lb/2, Math.min(y,y0), lb, Math.max(1,Math.abs(y-y0)));
 }
 g.globalAlpha=1;
 g.fillStyle='#8b949e'; g.font='9px sans-serif'; g.textAlign='right';
 [mx,0,-mx].forEach(function(v){ g.fillText(v.toFixed(1)+'σ', PL-5, Y(v)+3); });
 g.textAlign='left';
 _rcEixoX(g, X, n, PT, h, H);
 rcHover('rcAltini', PL, w, n, [
  {rot:'desvio', v:z, cor:'#c9d1d9', dec:2, un:' SD'},
  {rot:'estado', v:a.estado, cor:'#A371F7'}]);
}

function rcRedesenhar(){
 rcGraficoSwc(); rcGraficoAltini();
 rcGraficoBeta(); rcGraficoBetaTend(); rcGraficoSlope();
}

// ── um dropdown por modelo ───────────────────────────────────────────
function rcModelos(){
 const box=document.getElementById('rcModelos');
 let h='';

 h+=rcBloco('Altini — o dia de hoje', RC.altini, function(m){
  const c = m.estado_hoje==='pico' ? '#3FB950'
          : m.estado_hoje==='normal' ? '#8b949e' : '#F85149';
  return '<p class="sub">'+m.metodo+'</p>'
   +'<p>Hoje: <b style="color:'+c+';">'+(m.estado_hoje||'—')+'</b>'
   +(m.dias_seguidos_suprimido
     ? ' <span class="sub">('+m.dias_seguidos_suprimido
       +' dia(s) seguido(s))</span>':'')+'</p>'
   +'<p style="font-size:12px;">'+(m.leitura||'')+'</p>'
   +rcMini('rcAltini', null, 160)
   +'<p class="sub" style="font-size:11px;">'+m.diferenca_do_plews+'</p>';
 });

 h+=rcBloco('Plews — a tendência da semana', RC.swc, function(m){
  const e=m.estado||[];
  const hoje=e[e.length-1];
  const cor = hoje==='acima' ? '#3FB950'
            : hoje==='abaixo' ? '#F85149' : '#8b949e';
  return '<p class="sub">'+m.metodo+'</p>'
   +'<p>Hoje: <b style="color:'+cor+';">'+(hoje||'—')+'</b> da banda '
   +'<span class="sub">(média₂₈ ± '+m.k_swc+'·SD)</span></p>'
   +rcMini('rcSwc', null, 180)
   +'<p class="sub" style="font-size:11px;">Os pontos são o LnRMSSD de '
   +'cada dia; a linha é a média de 7 dias. A banda vem da média e do '
   +'desvio dos últimos 28 — é a variação que se espera de ti quando '
   +'nada mudou.</p>';
 });

 h+=rcBloco('Javaloyes — máquina de estados', RC.javaloyes, function(m){
  const p=m.prescricao||[];
  return '<p class="sub">'+m.metodo+'</p>'
   +'<p>Hoje: <b>'+(p[p.length-1]||'—')+'</b> · sinal '
   +((m.sinal||[]).slice(-1)[0]||'—')+'</p>'
   +'<p class="sub" style="font-size:11px;">'+m.nota_baseline+'</p>'
   +rcEstados(p);
 });

 h+=rcBloco('Kiviniemi — HF power', RC.kiviniemi, function(m){
  const p=m.prescricao||[];
  return '<p class="sub">'+m.metodo+'</p>'
   +'<p>Hoje: <b>'+(p[p.length-1]||'—')+'</b> · sinal '
   +((m.sinal||[]).slice(-1)[0]||'—')+'</p>'
   +'<p class="sub" style="font-size:11px;">'+m.nota_log+'</p>'
   +rcEstados(p);
 });

 h+=rcBloco('Modelo β — três horizontes', RC.beta, function(m){
  return '<p class="sub">'+m.metodo+'</p>'
   +'<table style="border-collapse:collapse;font-size:12px;">'
   +'<tr><td style="padding-right:16px;">β frescura</td><td><b>'
   +m.beta_hoje+'</b> <span class="sub">/100</span></td></tr>'
   +'<tr><td style="padding-right:16px;">agudo (3d−7d)</td><td>'
   +(m.agudo_hoje!=null?m.agudo_hoje.toFixed(1):'—')+'</td></tr>'
   +'<tr><td style="padding-right:16px;">crónico (7d−28d)</td><td>'
   +(m.cronico_hoje!=null?m.cronico_hoje.toFixed(1):'—')+'</td></tr>'
   +'</table>'
   +rcMini('rcBeta', null, 150)
   +'<p class="sub" style="font-size:11px;">β de cada dia: percentil do '
   +'LnRMSSD na distribuição dos 28 dias anteriores.</p>'
   +rcMini('rcBetaTend', null, 120)
   +'<p class="sub" style="font-size:11px;margin-top:6px;">'+m.nota+'</p>';
 });

 h+=rcBloco('PSlope — declive e zonas', RC.pslope, function(m){
  return '<p class="sub">'+m.metodo+'</p>'
   +'<p>Declive hoje: <b>'+m.declive_actual+'</b> '
   +'<span class="sub">(média '+m.media+' · SD '+m.sd+')</span></p>'
   +rcMini('rcSlope', null, 170)
   +'<table style="border-collapse:collapse;font-size:11px;margin-top:8px;">'
   +'<tr class="sub" style="text-align:left;">'
   +'<th style="padding-right:14px;">Zona</th>'
   +'<th style="padding-right:14px;">Vezes</th>'
   +'<th style="padding-right:14px;">Média</th><th>Máximo</th></tr>'
   +Object.keys(m.historico||{}).map(function(k){
     const x=m.historico[k];
     const aq = k===m.zona_actual;
     return '<tr'+(aq?' style="color:#5DADE2;"':'')+'>'
      +'<td style="padding-right:14px;">'+k+(aq?' ←':'')+'</td>'
      +'<td style="padding-right:14px;">'+x.n_vezes+'</td>'
      +'<td style="padding-right:14px;">'+x.media+' d</td>'
      +'<td>'+x.max+' d</td></tr>'; }).join('')
   +'</table>';
 });

 // qualidade do emparelhamento HRV↔FC
 const rq=(RC||{}).rhr_qualidade;
 if(rq && rq.n_com_rhr){
  const misturado=(rq.fontes||[]).length>1;
  h+='<p class="sub" style="font-size:11px;margin:6px 0;'
   +(misturado?'color:#F0883E;':'')+'">'
   +'<b>FC de repouso:</b> '+rq.n_mesma_medicao+' de '+rq.n_com_rhr
   +' dias vieram do MESMO registo que o HRV'
   +(misturado?' · fontes misturadas: '+rq.fontes.join(', '):'')
   +'<br>'+rq.nota+'</p>';
 }

 h+=rcBloco('Wellness — folha diária', RC.wellness, function(m){
  return '<p class="sub">'+m.nota+'</p>'
   +'<table style="border-collapse:collapse;font-size:11px;">'
   +'<tr class="sub" style="text-align:left;">'
   +'<th style="padding-right:14px;">Campo</th><th style="padding-right:14px;">Hoje</th>'
   +'<th style="padding-right:14px;">Média 28d</th><th>z</th></tr>'
   +Object.keys(m.campos||{}).map(function(k){
     const c=m.campos[k];
     const cor = c.z>0.5?'#3FB950':(c.z<-0.5?'#F85149':'#8b949e');
     return '<tr><td style="padding-right:14px;">'+k+'</td>'
      +'<td style="padding-right:14px;">'+c.hoje+'</td>'
      +'<td style="padding-right:14px;" class="sub">'+c.media28+'</td>'
      +'<td style="color:'+cor+';">'+(c.z>0?'+':'')+c.z+'</td></tr>';
    }).join('')+'</table>';
 });
 box.innerHTML=h;
 // os canvas só existem depois do innerHTML; desenhar a seguir
 rcRedesenhar();
 // e outra vez quando um dropdown abre: um canvas escondido tem largura
 // zero e sairia em branco
 Array.prototype.forEach.call(
  document.querySelectorAll('#rcModelos details'), function(d){
   d.addEventListener('toggle', function(){ if(d.open) rcRedesenhar(); });
  });
}

function rcBloco(titulo, m, render){
 if(!m) return '';
 if(m.ok===false)
  return '<p class="sub" style="margin:4px 0;"><b>'+titulo+':</b> '
   +(m.motivo||'indisponível')+'</p>';
 let corpo='';
 try{ corpo=render(m); }catch(e){ corpo='<p class="sub">erro: '+e.message+'</p>'; }
 return '<details style="margin:6px 0;"><summary style="cursor:pointer;'
  +'font-size:12px;color:#8b949e;padding:4px 0;">'+titulo+'</summary>'
  +'<div style="margin-top:6px;">'+corpo+'</div></details>';
}

// últimos 14 dias de uma ou mais séries, em texto
function rcSerie(){
 const args=Array.prototype.slice.call(arguments);
 const datas=(RC.datas||[]).slice(-14);
 let h='<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr class="sub" style="text-align:left;"><th style="padding-right:12px;">Data</th>'
  +args.map(function(a){ return '<th style="padding-right:12px;">'+a[0]+'</th>'; }).join('')
  +'</tr>';
 datas.forEach(function(d,i){
  const n=(RC.datas||[]).length, k=n-14+i;
  h+='<tr><td style="padding-right:12px;" class="sub">'+d+'</td>'
   +args.map(function(a){
     const v=(a[1]||[])[k];
     return '<td style="padding-right:12px;">'
      +(v==null?'—':(+v).toFixed(3))+'</td>'; }).join('')+'</tr>';
 });
 return h+'</table>';
}

// últimos 21 estados, como uma tira
function rcEstados(pres){
 const datas=RC.datas||[];
 const n=pres.length;
 let h='<div style="display:flex;gap:2px;flex-wrap:wrap;margin-top:6px;">';
 for(let i=Math.max(0,n-21);i<n;i++){
  const p=pres[i];
  const c = p==='HIGH'?'#3FB950':(p==='REST'?'#F85149':'#8b949e');
  h+='<span title="'+(datas[i]||'')+': '+p+'" style="width:26px;'
   +'text-align:center;font-size:9px;padding:3px 0;border-radius:3px;'
   +'background:'+c+'22;color:'+c+';border:1px solid '+c+'55;">'
   +p.charAt(0)+'</span>';
 }
 return h+'</div><p class="sub" style="font-size:10px;">últimos 21 dias · '
  +'H alta intensidade · L baixa · R descanso</p>';
}

// ── correlações ──────────────────────────────────────────────────────
function rcCorrelacoes(){
 const box=document.getElementById('rcCorrelacoes');
 const c=(RC||{}).correlacoes||{};
 if(!c.ok){
  box.innerHTML='<p class="sub">'+(c.motivo||c.erro||'sem dados')+'</p>';
  return;
 }
 let h='<p class="sub" style="font-size:11px;">Cada série de carga e de '
  +'volume contra o HRV, com atrasos de 0 a 28 dias. A pergunta é: o que '
  +'treinaste tem eco na recuperação, e a que prazo?</p>';
 (c.notas||[]).forEach(function(n){
  h+='<p style="font-size:12px;border-left:3px solid #3FB950;'
   +'padding-left:8px;margin:6px 0;">'+n+'</p>'; });
 if(!(c.notas||[]).length)
  h+='<p class="sub">Nenhuma relação sobrevive à correcção para '
   +'comparações múltiplas. Não é falta de relação — é falta de dias '
   +'para a demonstrar.</p>';

 // a que ESCALA cada coisa se relaciona — é a leitura mais útil, e a
 // que os lags longos permitem fazer
 const esc=c.por_escala||{};
 const rotEsc={imediato:'Imediato (0–1 dia)', dias:'Dias (2–3)',
               semana:'Semana (7)', bloco:'Bloco (15–21)',
               mesociclo:'Mesociclo (28)'};
 if(Object.keys(esc).length){
  h+='<div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0;">';
  ['imediato','dias','semana','bloco','mesociclo'].forEach(function(k){
   if(!esc[k]) return;
   h+='<div style="flex:1;min-width:150px;border:1px solid #30363d;'
    +'border-radius:6px;padding:6px 9px;">'
    +'<span class="sub" style="font-size:10px;">'+rotEsc[k]+'</span><br>'
    +'<span style="font-size:12px;">'+esc[k].join(', ')+'</span></div>';
  });
  h+='</div>';
 }

 // melhor lag de cada série, AGRUPADO por origem — 20 linhas soltas
 // não se lêem; três grupos de sete lêem-se
 const mel=c.melhores||{};
 const grupo=function(k){
  if(/^kj|^tss/.test(k)) return 'Carga';
  if(/^horas|^distancia|^n_sessoes/.test(k)) return 'Volume';
  if(/^ctl|^atl|^tsb|^ftlm/.test(k)) return 'PMC e memória';
  if(/^hf_power|^rhr/.test(k)) return 'Fisiológico';
  return 'Wellness';
 };
 if(Object.keys(mel).length){
  h+='<table style="width:100%;border-collapse:collapse;font-size:11px;'
   +'margin-top:8px;">'
   +'<tr class="sub" style="text-align:left;border-bottom:1px solid #21262d;">'
   +'<th style="padding:5px;">Série</th><th>Atraso</th><th>Escala</th>'
   +'<th>rho</th><th>n</th><th>p</th></tr>'
   +(function(){
     const ordenadas=Object.keys(mel).sort(function(a,b){
      const ga=grupo(a), gb=grupo(b);
      if(ga!==gb) return ga.localeCompare(gb);
      return Math.abs(mel[b].rho)-Math.abs(mel[a].rho); });
     let gAnt=null, linhas='';
     ordenadas.forEach(function(k){
      const gr=grupo(k);
      if(gr!==gAnt){ gAnt=gr;
       linhas+='<tr><td colspan="6" style="padding:8px 5px 3px 5px;'
        +'color:#58A6FF;font-size:11px;">'+gr+'</td></tr>'; }
      linhas+=(function(k){
     const e=mel[k];
     const cor = Math.abs(e.rho)>=0.5 ? '#3FB950'
               : Math.abs(e.rho)>=0.3 ? '#E3B341' : '#8b949e';
     return '<tr style="border-bottom:1px solid #161b22;">'
      +'<td style="padding:5px;">'+k+'</td>'
      +'<td class="sub">'+(e.lag?e.lag+' dia(s)':'mesmo dia')+'</td>'
      +'<td class="sub">'+(e.escala||'')+'</td>'
      +'<td style="color:'+cor+';"><b>'+e.rho+'</b></td>'
      +'<td class="sub">'+e.n+'</td>'
      +'<td class="sub">'+e.p+'</td></tr>'; })(k);
     });
     return linhas;
    })()
   +'</table>';
 }

 // tudo, incluindo os lags que não passaram
 h+='<details style="margin-top:8px;"><summary style="cursor:pointer;'
  +'font-size:12px;color:#8b949e;padding:4px 0;">Todos os '+c.n_testes
  +' testes, por atraso</summary><div style="margin-top:6px;">'
  +'<table style="width:100%;border-collapse:collapse;font-size:11px;">'
  +'<tr class="sub" style="text-align:left;">'
  +'<th style="padding:4px;">Série</th><th>Atraso</th><th>Escala</th>'
  +'<th>rho</th><th>p</th><th>n</th></tr>'
  +(c.pares||[]).map(function(e){
    return '<tr'+(e.significativa?'':' style="opacity:.45;"')+'>'
     +'<td style="padding:4px;">'+e.serie+'</td>'
     +'<td>'+e.lag+'</td><td class="sub">'+(e.escala||'')+'</td>'
     +'<td>'+e.rho+'</td>'
     +'<td>'+e.p+'</td><td>'+e.n+'</td></tr>'; }).join('')
  +'</table>';
 if((c.saltados_por_circularidade||[]).length){
  h+='<p style="color:#F0883E;font-size:11px;margin-top:8px;">'
   +'<b>Não testados, por serem circulares:</b></p>'
   +'<ul style="margin:2px 0 0 16px;font-size:11px;color:#8b949e;">'
   +c.saltados_por_circularidade.map(function(s2){
     return '<li>'+s2.par+' — '+s2.motivo+'</li>'; }).join('')+'</ul>';
 }
 h+='<p class="sub" style="font-size:11px;margin-top:6px;">'+c.metodo+'</p>'
  +'<p class="sub" style="font-size:11px;">'+c.aviso+'</p>'
  +'</div></details>';
 box.innerHTML=h;
 rcCorrModelos();
}

// ── cada modelo contra a carga ───────────────────────────────────────
// A pergunta aqui não é "a carga mexe no HRV" — é "qual dos modelos
// acompanha melhor o que treinaste".
function rcCorrModelos(){
 const box=document.getElementById('rcCorrModelos');
 if(!box) return;
 const c=(RC||{}).correlacoes_modelos||{};
 if(!c.ok){
  box.innerHTML='<p class="sub">'+(c.motivo||c.erro||'sem dados')+'</p>';
  return;
 }
 let h='';
 if(c.conclusao)
  h+='<p style="font-size:12px;border-left:3px solid #5DADE2;'
   +'padding-left:8px;margin:6px 0;">'+c.conclusao+'</p>';

 // ranking dos alvos
 const pa=c.por_alvo||{};
 if(Object.keys(pa).length){
  h+='<table style="width:100%;border-collapse:collapse;font-size:11px;">'
   +'<tr class="sub" style="text-align:left;border-bottom:1px solid #21262d;">'
   +'<th style="padding:5px;">Modelo</th><th>Relações</th>'
   +'<th>Mais forte com</th><th>Atraso</th><th>rho</th></tr>'
   +(c.ranking||[]).map(function(k){
     const d=pa[k]; if(!d) return '';
     const cor = Math.abs(d.melhor_rho)>=0.4 ? '#3FB950'
               : Math.abs(d.melhor_rho)>=0.25 ? '#E3B341' : '#8b949e';
     return '<tr style="border-bottom:1px solid #161b22;">'
      +'<td style="padding:5px;"><b>'+k+'</b></td>'
      +'<td class="sub">'+d.n_relacoes+'</td>'
      +'<td>'+(d.melhor_serie||'—')+'</td>'
      +'<td class="sub">'+(d.melhor_lag?d.melhor_lag+' dia(s)':'mesmo dia')
      +'</td>'
      +'<td style="color:'+cor+';"><b>'+d.melhor_rho+'</b></td></tr>';
    }).join('')
   +'</table>';
 } else {
  h+='<p class="sub">Nenhuma relação sobrevive à correcção. Com '
   +c.n_testes+' testes, o corte é exigente — e é isso que impede a '
   +'tabela de se encher de achados falsos.</p>';
 }

 // matriz completa
 const mel=c.melhores||[];
 if(mel.length){
  h+='<details style="margin-top:8px;"><summary style="cursor:pointer;'
   +'font-size:12px;color:#8b949e;padding:4px 0;">Todas as relações '
   +'encontradas ('+mel.length+')</summary><div style="margin-top:6px;">'
   +'<table style="width:100%;border-collapse:collapse;font-size:11px;">'
   +'<tr class="sub" style="text-align:left;">'
   +'<th style="padding:4px;">Modelo</th><th>Série</th><th>Atraso</th>'
   +'<th>Escala</th><th>rho</th><th>n</th></tr>'
   +mel.slice().sort(function(a,b){
     return Math.abs(b.rho)-Math.abs(a.rho); }).map(function(e){
     return '<tr style="border-bottom:1px solid #161b22;">'
      +'<td style="padding:4px;">'+e.alvo+'</td>'
      +'<td>'+e.serie+'</td>'
      +'<td class="sub">'+e.lag+'d</td>'
      +'<td class="sub">'+e.escala+'</td>'
      +'<td>'+e.rho+'</td><td class="sub">'+e.n+'</td></tr>'; }).join('')
   +'</table>'
   +'<p class="sub" style="font-size:11px;margin-top:6px;">'+c.metodo+'</p>'
   +'<p class="sub" style="font-size:11px;">'+c.nota_ordinal+'</p>'
   +'</div></details>';
 }
 box.innerHTML=h;
}

function rcCobertura(){
 const p=(RC||{}).periodo||{};
 const box=document.getElementById('rcCobertura');
 if(p.cobertura_pct>=80){ box.innerHTML=''; return; }
 box.innerHTML='<p style="color:#F0883E;font-size:11px;margin-top:10px;">'
  +'⚠ só '+p.cobertura_pct+'% dos dias têm HRV. As janelas de 7 e 28 dias '
  +'contam dias de calendário, não medições — com buracos, uma "média de '
  +'28 dias" pode assentar em muito menos pontos do que parece.</p>';
}

rcCarregar();
"""


def render():
    from flask import render_template_string
    return render_template_string(page('Recovery', SLUG, BODY, JS))
