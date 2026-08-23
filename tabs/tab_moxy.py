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

  <div class="chartbox" style="position:relative;">
    <canvas id="chMoxy" height="360"></canvas>
    <div id="mxTip" style="display:none;position:absolute;pointer-events:none;
      background:#161b22;border:1px solid #30363d;border-radius:6px;
      padding:6px 9px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
  </div>

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

const MX_CORES = {smo2:'#F85149', thb:'#58A6FF', o2hb:'#3FB950',
                  hhb:'#A371F7', watts:'#8b949e', heartrate:'#E3B341'};

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
  if(d.status !== 'ok'){
   est.textContent = d.mensagem || 'sem dados';
   MX = null; mxDraw(); mxDiagnostico(); return;
  }
  const s = MX_SESSOES.find(function(x){ return String(x.id)===String(id); }) || {};
  est.textContent = (s.data||'') + ' · ' + Object.keys(d.canais||{}).length
   + ' canais · ' + (d.tempo||[]).length + ' pontos';
  mxDraw(); mxDiagnostico();
 }).catch(e=>{ est.textContent = 'erro: ' + e.message; });
}

function mxDraw(){
 const o = ctx('chMoxy', 360); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 if(!MX || !MX.canais){ noData(g,W,H,'Sem dados'); return; }
 const t = MX.tempo || [];
 const nirs = ['smo2','thb','o2hb','hhb'].filter(k=>MX.canais[k]);
 if(!t.length || !nirs.length){ noData(g,W,H,'Sem streams NIRS'); return; }

 let vmin=1e9, vmax=-1e9;
 nirs.forEach(function(k){ MX.canais[k].forEach(function(v){
  if(v==null) return; if(v<vmin)vmin=v; if(v>vmax)vmax=v; }); });
 if(vmin>vmax){ noData(g,W,H,'Sem valores'); return; }
 const pad=(vmax-vmin)*0.08 || 1; vmin-=pad; vmax+=pad;

 const PL=54,PR=54,PT=18,PB=40,w=W-PL-PR,h=H-PT-PB;
 const t0=t[0], t1=t[t.length-1];
 const X=v=>PL+(v-t0)/((t1-t0)||1)*w;
 const Y=v=>PT+h-(v-vmin)/((vmax-vmin)||1)*h;
 MX_ESC={X:X,Y:Y,PL:PL,PT:PT,w:w,h:h,t0:t0,t1:t1};

 // potência em fundo, para se ver a que intensidade
 const wt = MX.canais.watts;
 if(wt){
  let wmax=Math.max.apply(null, wt.filter(v=>v!=null));
  if(wmax>0){
   g.fillStyle='rgba(139,148,158,0.10)';
   g.beginPath(); g.moveTo(PL, PT+h);
   wt.forEach(function(v,i){ if(v==null) return;
    g.lineTo(X(t[i]), PT+h-(v/wmax)*h*0.5); });
   g.lineTo(PL+w, PT+h); g.closePath(); g.fill();
  }
 }

 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='11px sans-serif';
 for(let i=0;i<=4;i++){
  const v=vmin+(vmax-vmin)*i/4, y=Y(v);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText(Math.round(v), PL-6, y+4);
 }
 g.textAlign='center';
 for(let i=0;i<=5;i++){
  const tv=t0+(t1-t0)*i/5;
  const m=Math.floor(tv/60);
  g.fillText(m+' min', X(tv), PT+h+18);
 }

 nirs.forEach(function(k){
  g.strokeStyle=MX_CORES[k]||'#c9d1d9'; g.lineWidth=2; g.beginPath();
  let primeiro=true;
  MX.canais[k].forEach(function(v,i){
   if(v==null) return;
   const x=X(t[i]), y=Y(v);
   primeiro ? (g.moveTo(x,y), primeiro=false) : g.lineTo(x,y);
  });
  g.stroke(); g.lineWidth=1;
 });

 g.textAlign='left'; g.font='10px sans-serif';
 nirs.forEach(function(k,i){
  g.fillStyle=MX_CORES[k]||'#c9d1d9';
  g.fillText('\\u2500 '+k.toUpperCase(), PL+w+6, PT+12+i*14);
 });
 if(wt){ g.fillStyle='#6e7681'; g.fillText('\\u25AC watts', PL+w+6,
                                           PT+12+nirs.length*14); }
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
   +'Moxy encontrada. A marca é procurada no nome, na descrição e nos campos '
   +'de tags; sessões com SmO2 no sumário entram mesmo sem marca escrita.</p>';
  return;
 }
 let h='<table style="width:100%;border-collapse:collapse;font-size:12px;">'
  +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
  +'<th style="padding:6px;">Data</th><th>Modalidade</th><th>Sessão</th>'
  +'<th>Duração</th><th>SmO2 médio</th><th>Marca</th></tr>';
 MX_SESSOES.forEach(function(s){
  h+='<tr style="border-bottom:1px solid #161b22;">'
   +'<td style="padding:6px;"><a href="#" style="color:#58A6FF;" '
   +'onclick="mxEscolher(\\''+s.id+'\\');return false;">'+s.data+'</a></td>'
   +'<td style="color:#8b949e;">'+(s.modalidade||s.tipo||'—')+'</td>'
   +'<td style="color:#8b949e;">'+(s.nome||'—')+'</td>'
   +'<td style="color:#8b949e;">'+(s.duracao_min?s.duracao_min+' min':'—')+'</td>'
   +'<td>'+(s.smo2_no_sumario!=null?Math.round(s.smo2_no_sumario):'—')+'</td>'
   +'<td style="color:#8b949e;">'+(s.marca_no_texto?'tag':'SmO2 no sumário')+'</td>'
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
