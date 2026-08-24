"""tab_moxy.py — sessões com sensor NIRS.

Mostra a última sessão com Moxy da modalidade escolhida, com os streams de
SmO2 e THb limpos pelo pipeline do mnirs (Jem Arnold): resample, substituir
inválidos e outliers, filtrar, e opcionalmente normalizar.
"""

from tabs.base import page

SLUG = 'moxy'

BODY = """
<div class="wrap">

  <h1>Moxy</h1>

  <div class="controls">
    <button onclick="mxActualizar(1)" title="Mostra o que seria alterado, sem alterar nada.">Verificar</button>
    <button onclick="mxActualizar(0)" title="Reconcilia com a Intervals.icu: grava as sessões novas e alteradas, remove as apagadas lá.">Actualizar sessões</button>
    <label class="sel">Modalidade
      <select id="mxModalidade" onchange="mxSessoes()">
        <option value="">todas</option>
        <option>Bike</option><option>Row</option>
        <option>Ski</option><option>Run</option>
      </select>
    </label>
    <label class="sel">Sessão
      <select id="mxSessao" onchange="mxCarregar()" style="min-width:260px"></select>
    </label>
    <label class="sel">Normalizar
      <select id="mxNorm" onchange="mxCarregar()">
        <option value="">valores brutos</option>
        <option value="deslocar">base a zero (Δ)</option>
        <option value="reescalar">0–100% da amplitude</option>
      </select>
    </label>
    <label class="sel">Suavização
      <select id="mxFc" onchange="mxCarregar()">
        <option value="0.05">leve</option>
        <option value="0.02" selected>média</option>
        <option value="0.008">forte</option>
      </select>
    </label>
    <span id="mxEstado" style="color:#8b949e;font-size:12px;margin-left:8px;"></span>
  </div>

  <div class="controls" style="margin-bottom:4px;flex-wrap:wrap;gap:2px 10px;">
    <span style="color:#8b949e;font-size:12px;">Métricas:</span>
    <span id="mxCanais" style="display:flex;flex-wrap:wrap;gap:2px 10px;
      align-items:center;"></span>
  </div>

  <div class="chartbox" style="position:relative;">
    <canvas id="chMoxy" height="360"></canvas>
    <div id="mxTip" style="display:none;position:absolute;pointer-events:none;
      background:#161b22;border:1px solid #30363d;border-radius:6px;
      padding:6px 9px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
  </div>

  <div class="controls" style="margin-top:6px;flex-wrap:wrap;gap:6px 12px;">
    <span style="color:#8b949e;font-size:12px;">Intervalo analisado:</span>
    <input type="range" id="mxIni" min="0" max="100" value="0" step="0.2"
           style="width:180px" oninput="mxSlider()">
    <input type="range" id="mxFim" min="0" max="100" value="100" step="0.2"
           style="width:180px" oninput="mxSlider()">
    <span id="mxCorteTxt" style="color:#c9d1d9;font-size:12px;"></span>
    <button onclick="mxAplicarProposta()" title="Repõe os cursores na proposta automática. Não grava nada.">Repor proposta</button>
    <button onclick="mxTudo()" title="Repõe os cursores na sessão inteira. Não grava nada.">Sessão inteira</button>
    <button onclick="mxGuardarCorte()" title="Grava este intervalo para esta sessão. Ao reabrir, aparece já cortado.">💾 Gravar este intervalo</button>
    <span id="mxCorteEstado" style="color:#8b949e;font-size:11px;"></span>
  </div>
  <p id="mxCorteNota" style="color:#8b949e;font-size:11px;margin:4px 0;"></p>
  <p style="color:#8b949e;font-size:11px;margin:0 0 8px 0;">
    <b>Repor proposta</b> e <b>Sessão inteira</b> só movem os cursores — não
    gravam. <b>Gravar este intervalo</b> guarda o que está nos cursores para
    esta sessão; ao reabrires, aparece já cortada assim. O limite de pausa que
    separa aquecimento de protocolo não é fixo: sai da mediana das pausas da
    própria sessão, porque o descanso varia de protocolo para protocolo.</p>

  <div id="mxDiag" style="margin-top:8px;"></div>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">Como os dados são tratados</summary>
    <div style="font-size:11px;color:#8b949e;margin-top:6px;">
      <p>Pipeline portado do pacote <b>mnirs</b> de Jem Arnold. A ordem não é
      arbitrária: <b>resample → substituir inválidos e outliers → filtrar →
      normalizar</b>. Filtrar antes de remover outliers espalha-os pelos
      vizinhos; remover outliers antes de regularizar a amostragem faz a
      janela móvel cobrir períodos de tempo diferentes.</p>
      <p><b>Outliers</b> são detectados contra a <b>mediana</b> local, não a
      média — com a média, um pico isolado desloca o próprio centro contra o
      qual está a ser julgado e escapa à detecção. O corte de 3 corresponde à
      regra de Pearson.</p>
      <p><b>Normalizar:</b> o SmO2 não é medido numa escala absoluta.
      "Base a zero" preserva a amplitude e mostra a variação desde o início —
      assume que o início representa a mesma condição em todos os canais.
      "0–100%" reescala para a amplitude observada — assume que o mínimo e o
      máximo desta sessão representam a capacidade funcional do tecido, e
      perde a diferença de amplitude entre sensores.</p>
    </div>
  </details>

  <div id="mxLista" style="overflow-x:auto;margin-top:14px;"></div>

</div>
"""

JS = """
let MX = null, MX_SESSOES = [], MX_ESC = null;
let MX_CORTE = null;   // [inicio_s, fim_s]

const MX_CORES = {smo2:'#F85149', thb:'#58A6FF', o2hb:'#3FB950',
                  hhb:'#A371F7', watts:'#6e7681', heartrate:'#E3B341',
                  respiration:'#79C0FF', dfa_a1:'#D2A8FF',
                  cadence:'#F0883E', velocity_smooth:'#3FB950',
                  torque:'#8b949e'};
// Escalas muito diferentes no mesmo grafico ficariam ilegiveis: o SmO2 anda
// nos 60, a potencia nos 250 e o DFA-a1 abaixo de 2. Cada canal e' normalizado
// ao seu proprio intervalo para o desenho, e o hover mostra sempre o valor
// real.
let MX_ON = {};

function mxActualizar(soVer){
 const est=document.getElementById('mxEstado');
 const nota=document.getElementById('mxCorteNota');
 est.textContent = soVer ? 'a verificar...' : 'a reconciliar...';
 fetch('/api/moxy/actualizar?dias=1095' + (soVer?'&so_diagnostico=1':''))
 .then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){ est.textContent='erro: '+(d.mensagem||''); return; }
  est.textContent = (soVer?'[verificação] ':'')
   + d.na_api+' na API · '+d.na_base_local+' locais · '
   + d.n_novas+' novas · '+d.n_orfas+' órfãs'
   + (soVer?'' : ' · '+(d.gravadas||0)+' gravadas');
  let h = 'Janela ' + (d.janela||[]).join(' a ')
   + ' em ' + (d.blocos_pedidos||[]).length + ' blocos de '
   + d.bloco_dias + ' dias: '
   + (d.blocos_pedidos||[]).map(function(b){ return b.de+' ('+b.n+')'; })
     .join(' · ');
  if((d.erros_api||[]).length)
   h += '<br><span style="color:#F85149;">Erros da API: '
     + d.erros_api.map(function(e){ return e.de+' — '+e.erro; }).join(' · ')
     + '</span>';
  if(d.n_novas) h += '<br><b>Novas:</b> ' + (d.novas||[]).join(', ');
  if(d.n_orfas) h += '<br><b>Órfãs' + (soVer?' (a remover)':' removidas')
   + ':</b> ' + (d.orfas||[]).join(', ');
  if(d.erro_gravar) h += '<br><span style="color:#F85149;">Erro ao gravar: '
   + d.erro_gravar + '</span>';
  if(d.descartadas_sem_data) h += '<br>' + d.descartadas_sem_data
   + ' descartadas por não terem data válida.';
  nota.innerHTML = h;
  if(!soVer) mxSessoes();
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

function mxSessoes(){
 const mod = document.getElementById('mxModalidade').value;
 const est = document.getElementById('mxEstado');
 est.textContent = 'a procurar sessões...';
 fetch('/api/moxy/sessoes' + (mod ? '?modalidade=' + mod : ''))
 .then(r=>r.json()).then(function(d){
  if(d.status !== 'ok'){ est.textContent = d.mensagem || 'erro'; return; }
  MX_SESSOES = d.sessoes || [];
  const sel = document.getElementById('mxSessao');
  sel.innerHTML = MX_SESSOES.map(function(s, i){
   return '<option value="'+s.id+'"'+(i===0?' selected':'')+'>'
    + s.data + ' · ' + (s.modalidade||s.tipo) + ' · ' + (s.nome||'')
    + (s.duracao_min ? ' ('+s.duracao_min+' min)' : '') + '</option>';
  }).join('');
  est.textContent = MX_SESSOES.length + ' sessões com Moxy';
  mxLista();
  if(MX_SESSOES.length) mxCarregar();
  else { MX = null; mxDraw(); document.getElementById('mxDiag').innerHTML=''; }
 }).catch(e=>{ est.textContent = 'erro: ' + e.message; });
}

function mxCarregar(){
 const id = document.getElementById('mxSessao').value;
 if(!id) return;
 const est = document.getElementById('mxEstado');
 const q = '?fc=' + document.getElementById('mxFc').value
   + (document.getElementById('mxNorm').value
      ? '&normalizar=' + document.getElementById('mxNorm').value : '');
 est.textContent = 'a carregar streams...';
 fetch('/api/moxy/dados/' + id + q).then(r=>r.json()).then(function(d){
  MX = d;
  if(d.status === 'removida'){
   est.textContent = 'sessão já não existe na Intervals.icu — removida da base';
   document.getElementById('mxCorteNota').textContent = d.mensagem || '';
   MX = null; mxDraw(); mxDiagnostico();
   mxSessoes();
   return;
  }
  if(d.status !== 'ok'){
   est.textContent = d.mensagem || 'sem dados';
   const det = d.detalhe_dos_streams;
   document.getElementById('mxCorteNota').innerHTML =
    (det
     ? '<b>Streams nesta sessão:</b> '
       + det.map(function(x){ return x.stream+' ('+x.n_pontos+')'; }).join(' · ')
       + '<br>Se algum destes for o sensor — um dev field pode chegar sem nome '
       + 'legível, tipo <code>dev_field_0_34</code> — diz qual e acrescento-o '
       + 'à lista de nomes reconhecidos.'
     : (d.streams_na_actividade
        ? 'Streams: ' + d.streams_na_actividade.join(', ') : ''));
   MX = null; mxDraw(); mxDiagnostico(); return;
  }
  const s = MX_SESSOES.find(function(x){ return String(x.id)===String(id); }) || {};
  est.textContent = (s.data||'') + ' · ' + Object.keys(d.canais||{}).length
   + ' canais · ' + (d.tempo||[]).length + ' pontos';
  mxCanaisEdit(); mxCorteInicial(); mxDraw(); mxDiagnostico();
 }).catch(e=>{ est.textContent = 'erro: ' + e.message; });
}

function mxCanaisEdit(){
 const box=document.getElementById('mxCanais');
 if(!box||!MX) return;
 const nirs=MX.canais_nirs||[], ctx2=MX.canais_contexto||[];
 const todos=nirs.concat(ctx2);
 if(!Object.keys(MX_ON).length)
  nirs.forEach(function(k){ MX_ON[k]=true; });
 box.innerHTML = todos.map(function(k){
  const on = MX_ON[k] === true;
  const nirsQ = nirs.indexOf(k)>=0;
  return '<label class="sel" style="white-space:nowrap;color:'
   + (MX_CORES[k]||'#c9d1d9') + ';">'
   + '<input type="checkbox" class="mxC" value="'+k+'"'+(on?' checked':'')
   + ' onchange="mxAlternar(this)"> ' + k + (nirsQ?'':' *') + '</label>';
 }).join('') + '<span style="color:#8b949e;font-size:10px;white-space:nowrap;">'
  + '* de contexto</span>';
}

function mxAlternar(el){
 MX_ON[el.value] = el.checked;
 mxDraw();
}

function mxCorteInicial(){
 // Prioridade ao corte guardado; sem ele, a proposta automatica; sem ela,
 // a sessao inteira. O que estiver a ser usado e' dito em texto, para nao
 // haver duvida sobre o que se esta a ver.
 const t=(MX&&MX.tempo)||[];
 if(!t.length){ MX_CORTE=null; return; }
 const t0=t[0], t1=t[t.length-1];
 const g=MX.corte_guardado, p=MX.corte_proposto;
 let origem;
 if(g && g.inicio_s!=null){ MX_CORTE=[g.inicio_s, g.fim_s]; origem='guardado'; }
 else if(p && p.ok){ MX_CORTE=[p.inicio_s, p.fim_s]; origem='proposto'; }
 else { MX_CORTE=[t0,t1]; origem='sessão inteira'; }
 const ini=document.getElementById('mxIni'), fim=document.getElementById('mxFim');
 ini.value = (MX_CORTE[0]-t0)/((t1-t0)||1)*100;
 fim.value = (MX_CORTE[1]-t0)/((t1-t0)||1)*100;
 mxCorteTexto(origem);
}

function mxCorteTexto(origem){
 const t=(MX&&MX.tempo)||[]; if(!t.length||!MX_CORTE) return;
 const f=v=>Math.floor(v/60)+':'+String(Math.round(v%60)).padStart(2,'0');
 document.getElementById('mxCorteTxt').textContent =
  f(MX_CORTE[0])+' → '+f(MX_CORTE[1])
  +'  ('+Math.round((MX_CORTE[1]-MX_CORTE[0])/60)+' min)';
 const p=MX.corte_proposto||{}, g=MX.corte_guardado;
 let n='';
 if(origem==='guardado') n='A usar o corte que guardaste em '
   +(g.data_gravacao||'?')+'.';
 else if(origem==='proposto') n='Proposta automática: '+(p.motivo||'')
   +(p.confianca?' · confiança '+p.confianca:'')+'.';
 else n='Sem proposta automática: '+(p.motivo||'')+'. A mostrar tudo.';
 const b=MX.blocos||{};
 if(b.ok) n+=' Detectados '+b.n_on+' blocos de trabalho e '+b.n_off
   +' de recuperação, com limiar em '+b.limiar_w+' W.';
 document.getElementById('mxCorteNota').textContent=n;
}

function mxSlider(){
 const t=(MX&&MX.tempo)||[]; if(!t.length) return;
 const t0=t[0], t1=t[t.length-1];
 let a=parseFloat(document.getElementById('mxIni').value);
 let b=parseFloat(document.getElementById('mxFim').value);
 if(a>b){ const c=a; a=b; b=c; }
 MX_CORTE=[t0+(t1-t0)*a/100, t0+(t1-t0)*b/100];
 mxCorteTexto('manual'); mxDraw(); mxDiagnostico();
}

function mxAplicarProposta(){
 const p=MX&&MX.corte_proposto;
 if(!p||!p.ok){ document.getElementById('mxCorteEstado').textContent
   = 'sem proposta'; return; }
 MX_CORTE=[p.inicio_s,p.fim_s];
 const t=MX.tempo, t0=t[0], t1=t[t.length-1];
 document.getElementById('mxIni').value=(p.inicio_s-t0)/((t1-t0)||1)*100;
 document.getElementById('mxFim').value=(p.fim_s-t0)/((t1-t0)||1)*100;
 mxCorteTexto('proposto'); mxDraw(); mxDiagnostico();
}

function mxTudo(){
 const t=(MX&&MX.tempo)||[]; if(!t.length) return;
 MX_CORTE=[t[0],t[t.length-1]];
 document.getElementById('mxIni').value=0;
 document.getElementById('mxFim').value=100;
 mxCorteTexto('sessão inteira'); mxDraw(); mxDiagnostico();
}

function mxGuardarCorte(){
 if(!MX||!MX_CORTE) return;
 const est=document.getElementById('mxCorteEstado');
 const s=MX_SESSOES.find(x=>String(x.id)===String(MX.activity_id))||{};
 est.textContent='a guardar...';
 fetch('/api/moxy/corte',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({activity_id:MX.activity_id,
   inicio_s:MX_CORTE[0], fim_s:MX_CORTE[1],
   modalidade:s.modalidade, data:s.data,
   proposto_s:(MX.corte_proposto||{}).inicio_s})})
 .then(r=>r.json()).then(function(d){
  est.textContent = d.status==='erro' ? 'erro: '+d.mensagem
    : 'guardado' + (d.status==='gravado_sem_upload' ? ' (local)' : '');
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

// indices dentro do corte
function mxJanela(){
 const t=(MX&&MX.tempo)||[];
 if(!t.length) return [0,0];
 if(!MX_CORTE) return [0,t.length-1];
 let a=0,b=t.length-1;
 for(let i=0;i<t.length;i++){ if(t[i]>=MX_CORTE[0]){ a=i; break; } }
 for(let i=t.length-1;i>=0;i--){ if(t[i]<=MX_CORTE[1]){ b=i; break; } }
 return [a,b];
}

function mxDraw(){
 const o = ctx('chMoxy', 360); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 if(!MX || !MX.canais){ noData(g,W,H,'Sem dados'); return; }
 const todos = MX.tempo || [];
 const jan = mxJanela();
 const t = todos.slice(jan[0], jan[1]+1);
 const activos = Object.keys(MX.canais).filter(k=>MX_ON[k]===true);
 if(!t.length || !activos.length){
  noData(g,W,H,'Escolhe pelo menos uma métrica'); return; }
 const rec = k => (MX.canais[k]||[]).slice(jan[0], jan[1]+1);

 const PL=54,PR=118,PT=18,PB=40,w=W-PL-PR,h=H-PT-PB;
 const t0=t[0], t1=t[t.length-1];
 const X=v=>PL+(v-t0)/((t1-t0)||1)*w;
 MX_ESC={X:X,PL:PL,PT:PT,w:w,h:h,t0:t0,t1:t1};

 // A potencia fica SEMPRE em fundo, mesmo sem estar seleccionada: sem ela
 // nao se sabe a que intensidade o SmO2 desceu, e isso e' metade da
 // leitura. Ocupa a metade de baixo e nao entra na escala dos outros.
 const wt = rec('watts');
 if(wt){
  const vs = wt.filter(v=>v!=null);
  const wmax = vs.length ? Math.max.apply(null, vs) : 0;
  if(wmax>0){
   g.fillStyle='rgba(139,148,158,0.13)';
   g.beginPath(); g.moveTo(PL, PT+h);
   wt.forEach(function(v,i){ if(v==null) return;
    g.lineTo(X(t[i]), PT+h-(v/wmax)*h*0.45); });
   g.lineTo(PL+w, PT+h); g.closePath(); g.fill();
   g.fillStyle='#6e7681'; g.font='10px sans-serif'; g.textAlign='left';
   g.fillText('watts (0–'+Math.round(wmax)+')', PL+4, PT+h-4);
  }
 }

 // escala partilhada pelos canais NIRS, para preservarem a relacao entre
 // si; os de contexto normalizam-se individualmente
 const nirs = activos.filter(k=>(MX.canais_nirs||[]).indexOf(k)>=0);
 const outros = activos.filter(k=>nirs.indexOf(k)<0 && k!=='watts');
 let lo=1e9, hi=-1e9;
 nirs.forEach(function(k){ rec(k).forEach(function(v){
  if(v==null) return; if(v<lo)lo=v; if(v>hi)hi=v; }); });
 if(lo>hi){ lo=0; hi=100; }
 const pad=(hi-lo)*0.08||1; lo-=pad; hi+=pad;
 const Y=v=>PT+h-(v-lo)/((hi-lo)||1)*h;

 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='11px sans-serif';
 for(let i=0;i<=4;i++){
  const v=lo+(hi-lo)*i/4, y=Y(v);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText(Math.round(v), PL-6, y+4);
 }
 g.textAlign='center';
 for(let i=0;i<=5;i++){
  const tv=t0+(t1-t0)*i/5;
  g.fillText(Math.floor(tv/60)+' min', X(tv), PT+h+18);
 }

 nirs.forEach(function(k){
  g.strokeStyle=MX_CORES[k]||'#c9d1d9'; g.lineWidth=2; g.beginPath();
  let primeiro=true;
  rec(k).forEach(function(v,i){
   if(v==null) return;
   const x=X(t[i]), y=Y(v);
   primeiro ? (g.moveTo(x,y), primeiro=false) : g.lineTo(x,y);
  });
  g.stroke(); g.lineWidth=1;
 });

 const esc={};
 outros.forEach(function(k){
  const vs=rec(k).filter(v=>v!=null);
  if(!vs.length) return;
  let a=Math.min.apply(null,vs), b=Math.max.apply(null,vs);
  if(b===a) b=a+1;
  esc[k]={lo:a,hi:b};
  const Y2=v=>PT+h-(v-a)/((b-a)||1)*h;
  g.strokeStyle=MX_CORES[k]||'#c9d1d9'; g.lineWidth=1; g.globalAlpha=0.6;
  g.beginPath();
  let primeiro=true;
  rec(k).forEach(function(v,i){
   if(v==null) return;
   const x=X(t[i]), y=Y2(v);
   primeiro ? (g.moveTo(x,y), primeiro=false) : g.lineTo(x,y);
  });
  g.stroke(); g.globalAlpha=1;
 });

 g.textAlign='left'; g.font='10px sans-serif';
 let li=0;
 nirs.forEach(function(k){
  g.fillStyle=MX_CORES[k]||'#c9d1d9';
  g.fillText('\u2500 '+k, PL+w+6, PT+12+(li++)*13);
 });
 outros.forEach(function(k){
  const e=esc[k];
  g.fillStyle=MX_CORES[k]||'#c9d1d9';
  g.fillText('\u2500 '+k+(e?' ('+Math.round(e.lo)+'–'+Math.round(e.hi)+')':''),
             PL+w+6, PT+12+(li++)*13);
 });
 if(outros.length){
  g.fillStyle='#6e7681';
  g.fillText('contexto: escala própria', PL+w+6, PT+12+li*13+6);
 }
 g.font='11px sans-serif';
 mxLigarTip();
}

function mxLigarTip(){
 const cv=document.getElementById('chMoxy');
 const tip=document.getElementById('mxTip');
 if(!cv||!tip||cv._tipMx) return;
 cv._tipMx=true;
 cv.addEventListener('mousemove', function(ev){
  if(!MX_ESC || !MX){ tip.style.display='none'; return; }
  const r=cv.getBoundingClientRect();
  const esc=(cv.width/r.width)/(window.devicePixelRatio||1);
  const mx=(ev.clientX-r.left)*esc;
  const e=MX_ESC;
  if(mx<e.PL||mx>e.PL+e.w){ tip.style.display='none'; return; }
  const tv=e.t0+(mx-e.PL)/e.w*(e.t1-e.t0);
  const t=MX.tempo||[];
  let idx=0, d=1e18;
  t.forEach(function(x,i){ const dd=Math.abs(x-tv); if(dd<d){d=dd; idx=i;} });
  const m=Math.floor(t[idx]/60), s=Math.round(t[idx]%60);
  let h='<b>'+m+':'+String(s).padStart(2,'0')+'</b>';
  Object.keys(MX.canais).forEach(function(k){
   const v=MX.canais[k][idx];
   if(v==null) return;
   h+='<br><span style="color:'+(MX_CORES[k]||'#c9d1d9')+';">'+k+'</span> '+v;
  });
  tip.innerHTML=h; tip.style.display='block';
  tip.style.left=Math.min(ev.clientX-r.left+14, r.width-160)+'px';
  tip.style.top=Math.max(4, ev.clientY-r.top-40)+'px';
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

function mxDiagnostico(){
 const box=document.getElementById('mxDiag');
 if(!box) return;
 const d=(MX&&MX.diagnostico)||{};
 const ks=Object.keys(d);
 if(!ks.length){ box.innerHTML=''; return; }
 let h='<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr style="color:#8b949e;text-align:left;"><th style="padding-right:14px;">Canal</th>'
  +'<th style="padding-right:14px;">Pontos</th><th style="padding-right:14px;">Inválidos</th>'
  +'<th style="padding-right:14px;">Outliers</th><th style="padding-right:14px;">Substituído</th>'
  +'<th>Filtro</th></tr>';
 ks.forEach(function(k){
  const x=d[k], f=x.filtro||{};
  const pct=x.pct_substituido;
  const cor = pct==null ? '#8b949e' : pct<5 ? '#3FB950' : pct<15 ? '#F0883E' : '#F85149';
  h+='<tr><td style="padding-right:14px;color:'+(MX_CORES[k]||'#c9d1d9')+';">'
   +k+'</td>'
   +'<td style="color:#8b949e;padding-right:14px;">'+(x.n_pontos||'—')+'</td>'
   +'<td style="color:#8b949e;padding-right:14px;">'+(x.invalidos||0)+'</td>'
   +'<td style="color:#8b949e;padding-right:14px;">'+(x.outliers||0)+'</td>'
   +'<td style="color:'+cor+';padding-right:14px;">'+(pct!=null?pct+'%':'—')+'</td>'
   +'<td style="color:#8b949e;">'+(f.metodo||'—')
   +(f.motivo?' ('+f.motivo+')':'')+'</td></tr>';
 });
 h+='</table>';
 const ar = MX.artefactos;
 if(ar && ar.pct_acima_do_limiar!=null){
  const c = ar.pct_acima_do_limiar<10 ? '#3FB950'
          : ar.pct_acima_do_limiar<30 ? '#F0883E' : '#F85149';
  h+='<p style="font-size:11px;color:#8b949e;border-left:2px solid '+c
   +';padding-left:8px;margin:6px 0;"><b style="color:'+c+';">'
   +ar.pct_acima_do_limiar+'%</b> dos pontos com artefacto na cinta acima de '
   +ar.limiar_usado+'%. '+ar.pontos_descartados+' pontos removidos da FC, '
   +'DFA-a1 e respiração antes de filtrar. O SmO2 e o THb vêm do Moxy e não '
   +'são afectados.</p>';
 }
 if(MX.streams_usados) h+='<p style="color:#8b949e;font-size:11px;">Streams: '
  +Object.keys(MX.streams_usados).map(function(k){
    return k+' \\u2190 '+MX.streams_usados[k]; }).join(' · ')+'</p>';
 box.innerHTML=h;
}

function mxLista(){
 const box=document.getElementById('mxLista');
 if(!box) return;
 if(!MX_SESSOES.length){
  box.innerHTML='<p style="color:#8b949e;font-size:12px;">Nenhuma sessão com '
   +'Moxy encontrada. Só entram actividades com a <b>tag</b> "Moxy" — o nome '
   +'da sessão é ignorado. Confirma a grafia na tab Atividades.</p>';
  return;
 }
 let h='<table style="width:100%;border-collapse:collapse;font-size:12px;">'
  +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
  +'<th style="padding:6px;">Data</th><th>Modalidade</th><th>Sessão</th>'
  +'<th>Duração</th><th>SmO2 médio</th><th>Tags</th></tr>';
 MX_SESSOES.forEach(function(s){
  h+='<tr style="border-bottom:1px solid #161b22;">'
   +'<td style="padding:6px;"><a href="#" style="color:#58A6FF;" '
   +'onclick="mxEscolher(\\''+s.id+'\\');return false;">'+s.data+'</a></td>'
   +'<td style="color:#8b949e;">'+(s.modalidade||s.tipo||'—')+'</td>'
   +'<td style="color:#8b949e;">'+(s.nome||'—')+'</td>'
   +'<td style="color:#8b949e;">'+(s.duracao_min?s.duracao_min+' min':'—')+'</td>'
   +'<td>'+(s.smo2_no_sumario!=null?Math.round(s.smo2_no_sumario):'—')+'</td>'
   +'<td style="color:#8b949e;">'+((s.tags||[]).join(', ')||'—')+'</td>'
   +'</tr>';
 });
 h+='</table>';
 box.innerHTML=h;
}

function mxEscolher(id){
 document.getElementById('mxSessao').value=id;
 mxCarregar();
}

mxSessoes();
window.addEventListener('resize', function(){ mxDraw(); });
"""


def render():
    from flask import render_template_string
    return render_template_string(page('Moxy', SLUG, BODY, JS))
