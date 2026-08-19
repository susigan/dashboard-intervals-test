"""tab_cp_model.py — Critical Power, W' e histórico do perfil.

Os MMP vêm da tabela power_curves, sincronizada da API da Intervals.icu —
a mesma fonte do perfil metabólico, para o CP e o MLSS não assentarem em
números diferentes.

Quatro secções:
  1. Modelos de CP com SEE%, escolha do utilizador e curva P(t)
  2. Calculadora Concept2 (só Row e Ski)
  3. Gravação de instantâneos, com data de referência à escolha
  4. Histórico: como o CP e os limiares se moveram ao longo do tempo
"""

from tabs.base import page

SLUG = 'cp'

BODY = """
<div class="wrap">

  <h1>CP Model</h1>

  <div class="controls">
    <label class="sel">Modalidade
      <select id="cpModalidade" onchange="cpCarregar()">
        <option>Bike</option><option>Row</option>
        <option>Ski</option><option>Run</option>
      </select>
    </label>
    <label class="sel">Season
      <select id="cpSeason" onchange="cpCarregar()">
        <option value="">activa</option>
      </select>
    </label>
    <label class="sel">Mín. pontos
      <select id="cpMinPts" onchange="cpCarregar()">
        <option value="3" selected>3</option>
        <option value="4">4</option>
        <option value="5">5</option>
      </select>
    </label>
    <button onclick="cpCarregar()">Recalcular</button>
    <span id="cpEstado" style="color:#8b949e;font-size:12px;margin-left:8px;"></span>
  </div>

  <div id="cpValidacao" style="margin-bottom:12px;"></div>

  <div class="controls" style="margin-bottom:8px;">
    <label class="sel"><input type="checkbox" id="cpUsarPmax" checked onchange="cpCarregar()">
      ancorar 3 parâmetros no Pmax</label>
    <label class="sel"><input type="checkbox" id="cpExclDup" checked onchange="cpCarregar()">
      excluir durações com potência igual</label>
  </div>

  <h2>Modelos</h2>
  <div id="cpModelos" style="overflow-x:auto;"></div>

  <h2>Curva de potência</h2>
  <div class="chartbox"><canvas id="chCP" height="320"></canvas></div>
  <div id="cpLegenda" style="margin-top:6px;"></div>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">O que significa cada linha do gráfico</summary>
    <div id="cpGlossario" style="margin-top:6px;"></div>
  </details>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">Diagnóstico Veloclinic e MMP usados</summary>
    <div id="cpDetalhe" style="margin-top:6px;overflow-x:auto;"></div>
  </details>

  <div id="cpC2Bloco" style="display:none;">
    <h2>Calculadora Concept2 — Row / Ski</h2>
    <div class="controls">
      <label class="sel">Power Peak <input type="number" id="c2pp" value="0" style="width:80px"></label>
      <label class="sel">60 seg <input type="number" id="c260" value="0" style="width:80px"></label>
      <label class="sel">2 km ★ <input type="number" id="c22k" value="0" style="width:80px"></label>
      <label class="sel">6 km <input type="number" id="c26k" value="0" style="width:80px"></label>
      <label class="sel">60 min <input type="number" id="c260m" value="0" style="width:80px"></label>
      <button onclick="cpC2()">Calcular</button>
    </div>
    <div id="c2Tabela" style="overflow-x:auto;"></div>
    <div class="controls" style="margin-top:8px;">
      <label class="sel">Split /500 m
        <input type="text" id="c2split" value="2:00.00" style="width:90px"></label>
      <button onclick="cpC2()">Converter</button>
      <span id="c2SplitOut" style="color:#8b949e;font-size:12px;margin-left:8px;"></span>
    </div>
  </div>

  <h2>Guardar instantâneo</h2>
  <div class="controls">
    <label class="sel">Modelo de CP
      <select id="cpModeloEscolhido" style="min-width:160px"></select></label>
    <label class="sel">Data de referência
      <input type="date" id="cpDataRef" style="width:150px"></label>
    <label class="sel"><input type="checkbox" id="cpGravaCp" checked> CP</label>
    <label class="sel"><input type="checkbox" id="cpGravaPerfil" checked> perfil metabólico</label>
    <label class="sel"><input type="checkbox" id="cpGravaLim" checked> intervalos dos campos</label>
    <button onclick="cpGuardar()">Guardar</button>
    <span id="cpGuardarEstado" style="color:#8b949e;font-size:12px;margin-left:8px;"></span>
  </div>
  <p style="color:#8b949e;font-size:11px;">
    A data de referência é a data <b>a que o instantâneo diz respeito</b>, não
    a de hoje: podes gravar hoje um retrato de uma season passada. Gravar duas
    vezes a mesma modalidade, season e data substitui em vez de duplicar,
    portanto podes corrigir sem acumular lixo.</p>

  <h2>Histórico</h2>
  <div class="controls">
    <label class="sel">De <input type="date" id="cpHistDe" style="width:150px"></label>
    <label class="sel">Até <input type="date" id="cpHistAte" style="width:150px"></label>
    <button onclick="cpHistorico()">Ver</button>
    <span id="cpHistEstado" style="color:#8b949e;font-size:12px;margin-left:8px;"></span>
  </div>
  <div class="chartbox"><canvas id="chHist" height="280"></canvas></div>
  <div id="cpHistTabela" style="overflow-x:auto;margin-top:8px;"></div>

</div>
"""

JS = """
let CP = null, HIST = null;

function cpCarregar(){
 const mod = document.getElementById('cpModalidade').value;
 const sea = document.getElementById('cpSeason').value;
 const mp  = document.getElementById('cpMinPts').value;
 const est = document.getElementById('cpEstado');
 est.textContent = 'a calcular...';
 let q = '?min_pts=' + mp + (sea ? '&season=' + encodeURIComponent(sea) : '');
 if(!document.getElementById('cpUsarPmax').checked) q += '&usar_pmax=0';
 if(!document.getElementById('cpExclDup').checked) q += '&duplicados=manter';
 fetch('/api/cp/modelos/' + mod + q).then(r=>r.json()).then(function(d){
  CP = d;
  if(d.status !== 'ok' || !d.ok){
   est.textContent = d.motivo || d.mensagem || 'sem dados';
   document.getElementById('cpModelos').innerHTML = '';
   cpDraw(); return;
  }
  est.textContent = d.n_mmp + ' MMP · season ' + (d.season||'?')
    + (d.melhor ? ' · menor SEE%: ' + d.melhor.nome : '');
  cpSeasons(d); cpValidacao(); cpTabela(); cpDraw(); cpGloss(); cpDetalhe();
  document.getElementById('cpC2Bloco').style.display =
    d.tem_calculadora_c2 ? 'block' : 'none';
 }).catch(e=>{ est.textContent = 'erro: ' + e.message; });
}

function cpSeasons(d){
 const sel = document.getElementById('cpSeason');
 if(sel.options.length > 1) return;
 (d.seasons_disponiveis||[]).forEach(function(s){
  const o = document.createElement('option'); o.value = s; o.textContent = s;
  sel.appendChild(o);
 });
}

function cpValidacao(){
 const av = (CP && CP.validacao) || [];
 const box = document.getElementById('cpValidacao');
 if(!av.length){ box.innerHTML=''; return; }
 const cores = {alto:'#F85149', medio:'#F0883E', baixo:'#8b949e', ok:'#3FB950'};
 const icones = {alto:'\u25CF', medio:'\u25CF', baixo:'\u25CB', ok:'\u2713'};
 let h = '';
 av.forEach(function(a){
  const c = cores[a.gravidade] || '#8b949e';
  h += '<p style="font-size:11px;color:#8b949e;margin:4px 0;border-left:2px solid '
   + c + ';padding-left:8px;"><b style="color:' + c + ';">'
   + (icones[a.gravidade]||'') + '</b> ' + a.texto + '</p>';
 });
 box.innerHTML = h;
}

function cpTabela(){
 const ms = (CP && CP.modelos) || {};
 const nomes = Object.keys(ms).sort((a,b)=> ms[a].see_pct - ms[b].see_pct);
 const sel = document.getElementById('cpModeloEscolhido');
 sel.innerHTML = nomes.map(n=>'<option>'+n+'</option>').join('');
 let h = '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
  +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
  +'<th style="padding:6px;">Modelo</th><th>CP</th><th>W\\u2032</th>'
  +'<th>SEE%</th><th>k</th><th>Pontos</th><th>Durações usadas</th></tr>';
 nomes.forEach(function(n,i){
  const m = ms[n];
  const cor = m.see_pct<2 ? '#3FB950' : m.see_pct<5 ? '#F0883E' : '#F85149';
  h += '<tr style="border-bottom:1px solid #161b22;'
    + (i===0?'background:rgba(88,166,255,0.06);':'') + '">'
    + '<td style="padding:6px;">' + n + (i===0?' <span style="color:#58A6FF;font-size:10px;">menor SEE%</span>':'') + '</td>'
    + '<td><b>' + m.cp + ' W</b></td>'
    + '<td>' + (m.wp_kj!=null ? m.wp_kj + ' kJ' : '—') + '</td>'
    + '<td style="color:' + cor + ';">' + m.see_pct + '%</td>'
    + '<td style="color:#8b949e;">' + m.k_params + '</td>'
    + '<td style="color:#8b949e;">' + m.n_pts + '</td>'
    + '<td style="color:#8b949e;">'
    + (m.pontos_usados||[]).map(p=>Math.round(p.t/60*10)/10+'min').join(', ')
    + '</td></tr>';
 });
 h += '</table>';
 if(CP.mmp60_val)
  h += '<p style="color:#8b949e;font-size:11px;margin-top:6px;">MMP60 = '
    + Math.round(CP.mmp60_val) + ' W. Não entra em nenhum ajuste — serve de '
    + 'validação externa: um CP acima do MMP60 é impossível, porque o CP é '
    + 'por definição sustentável mais tempo do que 60 minutos.</p>';
 h += '<p style="color:#8b949e;font-size:11px;">' + (CP.nota_see||'') + '</p>';
 document.getElementById('cpModelos').innerHTML = h;
}

const CP_CORES = ['#58A6FF','#3FB950','#F0883E','#F85149','#A371F7',
                  '#E3B341','#79C0FF','#D2A8FF'];

function cpDraw(){
 const o = ctx('chCP', 320); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 if(!CP || !CP.ok){ noData(g,W,H,'Sem modelos para desenhar'); return; }
 const pts = CP.mmp_pts_full && CP.mmp_pts_full.length ? CP.mmp_pts_full : CP.mmp_pts;
 const curvas = CP.curvas || {};
 const nomes = Object.keys(CP.modelos||{}).sort((a,b)=>
   CP.modelos[a].see_pct - CP.modelos[b].see_pct);

 let tmin=1e9, tmax=0, pmin=1e9, pmax=0;
 pts.forEach(function(p){ tmin=Math.min(tmin,p.t); tmax=Math.max(tmax,p.t);
   pmin=Math.min(pmin,p.w); pmax=Math.max(pmax,p.w); });
 if(CP.mmp60_val){ tmax=Math.max(tmax,3600); pmin=Math.min(pmin,CP.mmp60_val); }
 tmin=Math.max(30,tmin*0.7); tmax=tmax*1.4;
 pmin=pmin*0.80; pmax=pmax*1.06;

 const PL=56, PR=104, PT=14, PB=42, w=W-PL-PR, h=H-PT-PB;
 const lt=Math.log(tmin), lT=Math.log(tmax);
 const X = t => PL + (Math.log(t)-lt)/((lT-lt)||1)*w;
 const Y = p => PT + h - (p-pmin)/((pmax-pmin)||1)*h;

 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='11px sans-serif';
 for(let i=0;i<=4;i++){
  const pv=pmin+(pmax-pmin)*i/4, y=Y(pv);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText(Math.round(pv)+'W', PL-6, y+4);
 }
 [60,180,300,720,1200,3600].forEach(function(t){
  if(t<tmin||t>tmax) return;
  const x=X(t);
  g.strokeStyle='#161b22'; g.beginPath(); g.moveTo(x,PT); g.lineTo(x,PT+h); g.stroke();
  g.fillStyle='#8b949e'; g.textAlign='center';
  g.fillText(t<3600? (t/60)+'min' : '60min', x, PT+h+18);
 });

 // curvas dos modelos
 nomes.forEach(function(n,i){
  const c = curvas[n]; if(!c || !c.length) return;
  g.strokeStyle=CP_CORES[i%CP_CORES.length];
  g.lineWidth = i===0 ? 2.5 : 1;
  g.globalAlpha = i===0 ? 1 : 0.45;
  g.beginPath();
  let primeiro=true;
  c.forEach(function(p){
   if(p.t<tmin||p.t>tmax) return;
   const x=X(p.t), y=Y(p.p);
   if(primeiro){ g.moveTo(x,y); primeiro=false; } else g.lineTo(x,y);
  });
  g.stroke(); g.globalAlpha=1; g.lineWidth=1;
 });

 // linha horizontal do CP do modelo escolhido
 const escolhido = document.getElementById('cpModeloEscolhido').value
   || (CP.melhor||{}).nome;
 const m = (CP.modelos||{})[escolhido];
 if(m && m.cp){
  const y=Y(m.cp);
  g.strokeStyle='#58A6FF'; g.setLineDash([6,4]); g.lineWidth=1.5;
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.setLineDash([]); g.lineWidth=1;
  g.fillStyle='#58A6FF'; g.textAlign='left';
  g.fillText('CP ' + Math.round(m.cp) + 'W', PL+w+6, y+4);
 }
 // MMP60 como validação
 if(CP.mmp60_val){
  const y=Y(CP.mmp60_val);
  g.strokeStyle='#8b949e'; g.setLineDash([2,4]);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke(); g.setLineDash([]);
  g.fillStyle='#8b949e'; g.textAlign='left';
  g.fillText('MMP60 ' + Math.round(CP.mmp60_val) + 'W', PL+w+6, y+4);
 }

 // pontos MMP reais
 pts.forEach(function(p){
  const x=X(p.t), y=Y(p.w);
  g.fillStyle='#c9d1d9'; g.beginPath(); g.arc(x,y,4,0,Math.PI*2); g.fill();
  g.strokeStyle='#0d1117'; g.stroke();
 });

 // legenda
 g.textAlign='left'; g.font='10px sans-serif';
 nomes.slice(0,6).forEach(function(n,i){
  g.fillStyle=CP_CORES[i%CP_CORES.length];
  g.fillText(n, PL+w+6, PT+14+i*13);
 });
 g.font='11px sans-serif';
}

function cpGloss(){
 const m = (CP.modelos||{})[(CP.melhor||{}).nome] || {};
 document.getElementById('cpGlossario').innerHTML =
  '<p style="font-size:11px;color:#8b949e;"><b style="color:#c9d1d9;">Pontos brancos</b>'
  + ' — os MMP reais, tirados das power curves da Intervals.icu. São o que o'
  + ' ajuste tenta reproduzir.</p>'
  + '<p style="font-size:11px;color:#8b949e;"><b style="color:#c9d1d9;">Curvas coloridas</b>'
  + ' — P(t) = CP + W\\u2032/t de cada modelo. A mais espessa é a de menor SEE%.'
  + ' Divergirem muito entre si nas durações longas é normal e é o próprio'
  + ' aviso: o CP está a ser extrapolado para fora do intervalo testado.</p>'
  + '<p style="font-size:11px;color:#8b949e;"><b style="color:#58A6FF;">Linha CP</b>'
  + ' — a assímptota horizontal do modelo escolhido: a potência que a curva'
  + ' nunca cruza para baixo. É a fronteira entre o domínio pesado e o'
  + ' severo, não a FTP.</p>'
  + '<p style="font-size:11px;color:#8b949e;"><b style="color:#8b949e;">Linha MMP60</b>'
  + ' — o melhor de 60 minutos, deixado deliberadamente fora de todos os'
  + ' ajustes. Se o CP ficar acima dele, o modelo está errado.</p>'
  + '<p style="font-size:11px;color:#8b949e;"><b style="color:#c9d1d9;">W\\u2032</b>'
  + ' — o trabalho finito disponível acima do CP, em kJ. Tempo até à'
  + ' exaustão a uma potência P é W\\u2032/(P − CP)'
  + (m.cp&&m.wp ? '; a ' + Math.round(m.cp+50) + ' W dá '
     + Math.round(m.wp/50) + ' s' : '') + '.</p>'
  + '<p style="font-size:11px;color:#8b949e;"><b style="color:#c9d1d9;">SEE%</b>'
  + ' — erro padrão do ajuste sobre a potência média. Mede proximidade aos'
  + ' pontos, não veracidade do CP.</p>';
}

function cpDetalhe(){
 const v = CP.veloclinic;
 let h = '';
 if(v){
  h += '<p style="font-size:12px;">' + v.classificacao + ' · CV do W\\u2032 = '
    + v.metricas.cv + '% · média ' + Math.round(v.metricas.mean/1000*100)/100
    + ' kJ · declive ' + v.metricas.slope + '</p>'
    + '<p style="color:#8b949e;font-size:11px;">Veloclinic: se o modelo de CP'
    + ' descrevesse bem o atleta, o W\\u2032 calculado em cada ponto'
    + ' (t × (P − CP)) seria constante. A dispersão desse valor é o'
    + ' diagnóstico; um declive marcado contra a potência significa que o'
    + ' modelo hiperbólico não serve neste intervalo de durações.</p>';
 }
 h += '<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr style="color:#8b949e;text-align:left;"><th style="padding-right:16px;">Duração</th>'
  +'<th style="padding-right:16px;">Watts</th><th style="padding-right:16px;">Data</th>'
  +'<th>Season</th></tr>';
 (CP.mmp_pts_full||[]).forEach(function(p){
  const k=String(p.t), rc=(CP.recuou_de_season||{})[k];
  h += '<tr><td style="padding-right:16px;">' + Math.round(p.t/60*10)/10 + ' min</td>'
    + '<td style="padding-right:16px;">' + p.w + ' W</td>'
    + '<td style="color:#8b949e;padding-right:16px;">'
    + ((CP.datas_dos_mmp||{})[k] || '—') + '</td>'
    + '<td style="color:' + (rc?'#F0883E':'#8b949e') + ';">'
    + ((CP.seasons_dos_mmp||{})[k] || '—') + (rc?' (recuou)':'') + '</td></tr>';
 });
 h += '</table>';
 document.getElementById('cpDetalhe').innerHTML = h;
}

function cpC2(){
 const q = new URLSearchParams({
  wpp: document.getElementById('c2pp').value || 0,
  w60seg: document.getElementById('c260').value || 0,
  w2k: document.getElementById('c22k').value || 0,
  w6k: document.getElementById('c26k').value || 0,
  w60min: document.getElementById('c260m').value || 0,
  split: document.getElementById('c2split').value || ''});
 fetch('/api/cp/c2?' + q).then(r=>r.json()).then(function(d){
  document.getElementById('c2SplitOut').textContent =
   d.watts_do_split ? d.watts_do_split + ' W' : '';
  const t = d.tabela || [];
  if(!t.length){
   document.getElementById('c2Tabela').innerHTML =
    '<span style="color:#8b949e;font-size:12px;">Insere os watts do 2 km.</span>';
   return;
  }
  let h = '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
   +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
   +'<th style="padding:6px;">Teste</th><th>Watts real</th><th>Split real</th>'
   +'<th>% actual</th><th>% ideal</th><th>Watts obj.</th><th>Split obj.</th>'
   +'<th>Δ</th></tr>';
  t.forEach(function(r){
   const dc = r.delta_w==null ? '#8b949e' : r.delta_w>=0 ? '#3FB950' : '#F0883E';
   h += '<tr style="border-bottom:1px solid #161b22;'
     + (r.teste==='2km'?'background:rgba(88,166,255,0.06);':'') + '">'
     + '<td style="padding:6px;">' + r.teste + (r.teste==='2km'?' ★':'') + '</td>'
     + '<td>' + (r.watts_real!=null?r.watts_real+' W':'—') + '</td>'
     + '<td style="color:#8b949e;">' + (r.split_real||'—') + '</td>'
     + '<td style="color:#8b949e;">' + (r.pct_actual!=null?r.pct_actual+'%':'—') + '</td>'
     + '<td style="color:#8b949e;">' + r.pct_ideal + '%</td>'
     + '<td><b>' + r.watts_objectivo + ' W</b></td>'
     + '<td>' + (r.split_objectivo||'—') + '</td>'
     + '<td style="color:' + dc + ';">'
     + (r.delta_w!=null? (r.delta_w>0?'+':'')+r.delta_w+' W' : '—') + '</td></tr>';
  });
  h += '</table><p style="color:#8b949e;font-size:11px;">' + (d.nota||'') + '</p>';
  document.getElementById('c2Tabela').innerHTML = h;
 });
}

function cpGuardar(){
 const est = document.getElementById('cpGuardarEstado');
 const quais = [];
 if(document.getElementById('cpGravaCp').checked) quais.push('cp');
 if(document.getElementById('cpGravaPerfil').checked) quais.push('perfil');
 if(document.getElementById('cpGravaLim').checked) quais.push('limiares');
 if(!quais.length){ est.textContent = 'nada seleccionado'; return; }
 est.textContent = 'a guardar...';
 fetch('/api/perfil/guardar', {method:'POST',
   headers:{'Content-Type':'application/json'},
   body: JSON.stringify({
     modalidade: document.getElementById('cpModalidade').value,
     season: document.getElementById('cpSeason').value || (CP||{}).season,
     data_referencia: document.getElementById('cpDataRef').value || null,
     modelo_cp: document.getElementById('cpModeloEscolhido').value || null,
     guardar: quais})})
 .then(r=>r.json()).then(function(d){
  if(d.status==='erro'){ est.textContent = 'erro: ' + d.mensagem; return; }
  est.textContent = 'guardado em ' + d.data_referencia
    + (d.status==='gravado_sem_upload' ? ' (local; Drive falhou: '+d.drive+')' : '');
  cpHistorico();
 }).catch(e=>{ est.textContent = 'erro: ' + e.message; });
}

function cpHistorico(){
 const mod = document.getElementById('cpModalidade').value;
 const de = document.getElementById('cpHistDe').value;
 const ate = document.getElementById('cpHistAte').value;
 const est = document.getElementById('cpHistEstado');
 est.textContent = 'a carregar...';
 let q = [];
 if(de) q.push('de=' + de);
 if(ate) q.push('ate=' + ate);
 fetch('/api/perfil/historico/' + mod + (q.length?'?'+q.join('&'):''))
 .then(r=>r.json()).then(function(d){
  HIST = d;
  if(d.status!=='ok'){ est.textContent = d.mensagem || 'sem dados'; histDraw(); return; }
  est.textContent = d.n_instantaneos + ' instantâneos · '
    + (d.cp||[]).length + ' registos de CP';
  histDraw(); histTabela();
 }).catch(e=>{ est.textContent = 'erro: ' + e.message; });
}

function histDraw(){
 const o = ctx('chHist', 280); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const ps = (HIST && HIST.perfil) || [];
 const cps = (HIST && HIST.cp) || [];
 if(ps.length + cps.length < 2){
  noData(g,W,H,'Guarda pelo menos dois instantâneos para ver a evolução'); return; }

 const series = [
  {k:'lt1_w',    rot:'LT1',    cor:'#3FB950', dados:ps},
  {k:'fatmax_w', rot:'FatMax', cor:'#A371F7', dados:ps},
  {k:'mlss_w',   rot:'MLSS',   cor:'#F0883E', dados:ps},
  {k:'lt2_w',    rot:'LT2',    cor:'#F85149', dados:ps},
  {k:'pvo2max_w',rot:'Pvo\\u2082max', cor:'#79C0FF', dados:ps},
  {k:'cp_w',     rot:'CP',     cor:'#58A6FF', dados:cps},
 ];
 let datas = [];
 series.forEach(s=> s.dados.forEach(r=>{ if(r[s.k]!=null) datas.push(r.data_referencia); }));
 datas = Array.from(new Set(datas)).sort();
 if(datas.length < 2){ noData(g,W,H,'Guarda instantâneos em datas diferentes'); return; }

 let vmin=1e9, vmax=0;
 series.forEach(s=> s.dados.forEach(r=>{
   if(r[s.k]!=null){ vmin=Math.min(vmin,r[s.k]); vmax=Math.max(vmax,r[s.k]); }}));
 vmin=vmin*0.9; vmax=vmax*1.06;

 const PL=54, PR=86, PT=14, PB=42, w=W-PL-PR, h=H-PT-PB;
 const X = d => PL + datas.indexOf(d)/((datas.length-1)||1)*w;
 const Y = v => PT + h - (v-vmin)/((vmax-vmin)||1)*h;

 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='11px sans-serif';
 for(let i=0;i<=4;i++){
  const vv=vmin+(vmax-vmin)*i/4, y=Y(vv);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText(Math.round(vv)+'W', PL-6, y+4);
 }
 datas.forEach(function(d,i){
  if(datas.length>8 && i%Math.ceil(datas.length/8)) return;
  g.fillStyle='#8b949e'; g.textAlign='center';
  g.fillText(d.slice(5), X(d), PT+h+18);
 });

 series.forEach(function(s,i){
  const pts = s.dados.filter(r=>r[s.k]!=null)
    .map(r=>({d:r.data_referencia, v:r[s.k]}))
    .sort((a,b)=> a.d<b.d?-1:1);
  if(!pts.length) return;
  g.strokeStyle=s.cor; g.lineWidth=2; g.beginPath();
  pts.forEach(function(p,j){
   const x=X(p.d), y=Y(p.v);
   if(j===0) g.moveTo(x,y); else g.lineTo(x,y);
  });
  g.stroke(); g.lineWidth=1;
  g.fillStyle=s.cor;
  pts.forEach(function(p){
   g.beginPath(); g.arc(X(p.d), Y(p.v), 3, 0, Math.PI*2); g.fill(); });
  g.textAlign='left';
  g.fillText(s.rot, PL+w+6, PT+14+i*14);
 });
}

function histTabela(){
 const ps = (HIST && HIST.perfil) || [];
 const cps = (HIST && HIST.cp) || [];
 const porData = {};
 ps.forEach(r=>{ porData[r.data_referencia] = Object.assign(
   porData[r.data_referencia]||{}, r); });
 cps.forEach(r=>{ porData[r.data_referencia] = Object.assign(
   porData[r.data_referencia]||{}, {cp_w:r.cp_w, wp_j:r.wp_j,
   modelo_escolhido:r.modelo_escolhido, see_pct:r.see_pct}); });
 const datas = Object.keys(porData).sort().reverse();
 if(!datas.length){
  document.getElementById('cpHistTabela').innerHTML =
   '<span style="color:#8b949e;font-size:12px;">Ainda não há instantâneos guardados.</span>';
  return;
 }
 let h = '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
  +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
  +'<th style="padding:6px;">Data</th><th>Season</th><th>CP</th><th>Modelo</th>'
  +'<th>LT1</th><th>FatMax</th><th>MLSS</th><th>LT2</th><th>Pvo\\u2082max</th>'
  +'<th>VO\\u2082max</th><th>VLamax</th><th>Peso</th><th></th></tr>';
 datas.forEach(function(dt){
  const r = porData[dt];
  const c = v => v==null ? '—' : Math.round(v*10)/10;
  h += '<tr style="border-bottom:1px solid #161b22;">'
   + '<td style="padding:6px;">' + dt + '</td>'
   + '<td style="color:#8b949e;">' + (r.season||'—') + '</td>'
   + '<td><b>' + c(r.cp_w) + '</b></td>'
   + '<td style="color:#8b949e;">' + (r.modelo_escolhido||'—') + '</td>'
   + '<td>' + c(r.lt1_w) + '</td><td>' + c(r.fatmax_w) + '</td>'
   + '<td>' + c(r.mlss_w) + '</td><td>' + c(r.lt2_w) + '</td>'
   + '<td>' + c(r.pvo2max_w) + '</td>'
   + '<td>' + c(r.vo2max) + '</td><td>' + c(r.vlamax) + '</td>'
   + '<td style="color:#8b949e;">' + c(r.peso_kg) + '</td>'
   + '<td><a href="#" style="color:#F85149;font-size:11px;" '
   + 'onclick="cpApagar(\\'' + dt + '\\',\\'' + (r.season||'') + '\\');return false;">apagar</a></td>'
   + '</tr>';
 });
 h += '</table>';

 // intervalos dos campos externos ao longo do tempo
 const lc = (HIST && HIST.limiares_por_campo) || {};
 const campos = Object.keys(lc).sort();
 if(campos.length){
  h += '<h3 style="font-size:13px;color:#8b949e;margin-top:16px;">Intervalos dos campos ao longo do tempo</h3>'
   + '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
   + '<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
   + '<th style="padding:6px;">Campo</th><th>Primeiro</th><th>Último</th>'
   + '<th>Δ mediana</th><th>Amplitude p25–p75</th></tr>';
  campos.forEach(function(k){
   const v = lc[k].slice().sort((a,b)=> a.data<b.data?-1:1);
   const a = v[0], b = v[v.length-1];
   const delta = (a.p50!=null && b.p50!=null) ? Math.round((b.p50-a.p50)*10)/10 : null;
   const cor = delta==null ? '#8b949e' : delta>0 ? '#3FB950' : '#F0883E';
   h += '<tr style="border-bottom:1px solid #161b22;">'
    + '<td style="padding:6px;">' + k + '</td>'
    + '<td style="color:#8b949e;">' + a.data + ': ' + a.p50 + '</td>'
    + '<td>' + b.data + ': <b>' + b.p50 + '</b></td>'
    + '<td style="color:' + cor + ';">' + (delta==null?'—':(delta>0?'+':'')+delta) + '</td>'
    + '<td style="color:#8b949e;">' + a.p25 + '–' + a.p75 + ' → ' + b.p25 + '–' + b.p75 + '</td>'
    + '</tr>';
  });
  h += '</table><p style="color:#8b949e;font-size:11px;">A amplitude p25–p75 a '
   + 'estreitar significa estimativas mais consistentes entre sessões; a '
   + 'alargar significa o contrário, e nesse caso a mediana move-se sem que '
   + 'isso queira dizer que o limiar mudou.</p>';
 }
 document.getElementById('cpHistTabela').innerHTML = h;
}

function cpApagar(data, season){
 if(!confirm('Apagar o instantâneo de ' + data + '?')) return;
 fetch('/api/perfil/apagar', {method:'POST',
   headers:{'Content-Type':'application/json'},
   body: JSON.stringify({
     modalidade: document.getElementById('cpModalidade').value,
     season: season, data_referencia: data})})
 .then(r=>r.json()).then(function(){ cpHistorico(); });
}

document.addEventListener('DOMContentLoaded', function(){
 const hoje = new Date().toISOString().slice(0,10);
 document.getElementById('cpDataRef').value = hoje;
 document.getElementById('cpHistAte').value = hoje;
 cpCarregar(); cpHistorico();
});
window.addEventListener('resize', function(){ cpDraw(); histDraw(); });
"""


def render():
    from flask import render_template_string
    return render_template_string(page('CP Model', SLUG, BODY, JS))
