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
    <label class="sel">Âmbito
      <select id="cpAmbito" onchange="cpAmbitoMudou()">
        <option value="season">season</option>
        <option value="365">últimos 365 dias</option>
        <option value="180">últimos 180 dias</option>
        <option value="730">últimos 2 anos</option>
      </select>
    </label>
    <label class="sel" id="cpSeasonWrap">Season
      <select id="cpSeason" onchange="cpCarregar()">
        <option value="">activa</option>
      </select>
    </label>
    <label class="sel">Conjunto
      <select id="cpModo" onchange="cpCarregar()">
        <option value="coerente" selected>coerente (uma só época)</option>
        <option value="season">só a season activa</option>
        <option value="recuo">recuo por duração</option>
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

  <h2>MMP usados</h2>
  <div id="cpMMP"></div>

  <h2>Curva de potência</h2>
  <div class="chartbox" style="position:relative;">
    <canvas id="chCP" height="320"></canvas>
    <div id="cpTip" style="display:none;position:absolute;pointer-events:none;
      background:#161b22;border:1px solid #30363d;border-radius:6px;
      padding:6px 9px;font-size:11px;color:#c9d1d9;z-index:5;max-width:220px;"></div>
  </div>
  <div id="cpLegenda" style="margin-top:6px;"></div>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">O que significa cada linha do gráfico</summary>
    <div id="cpGlossario" style="margin-top:6px;"></div>
  </details>

  <h2>Análise do ajuste</h2>
  <div style="display:flex;flex-wrap:wrap;gap:12px;">
    <div style="flex:1;min-width:320px;">
      <div style="color:#8b949e;font-size:12px;margin-bottom:4px;">
        W&prime; ponto a ponto — deveria ser constante</div>
      <div class="chartbox"><canvas id="chVelo" height="240"></canvas></div>
    </div>
    <div style="flex:1;min-width:320px;">
      <div style="color:#8b949e;font-size:12px;margin-bottom:4px;">
        Resíduos — observado menos previsto</div>
      <div class="chartbox"><canvas id="chResid" height="240"></canvas></div>
    </div>
  </div>
  <div id="cpAnaliseTexto" style="margin-top:6px;"></div>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">Diagnóstico Veloclinic em detalhe</summary>
    <div id="cpDetalhe" style="margin-top:6px;overflow-x:auto;"></div>
  </details>

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
let CP = null, HIST = null, CP_AJUSTES = '';
let CP_ESCALA = null, CP_PONTOS = [];

function cpCarregar(){
 const mod = document.getElementById('cpModalidade').value;
 const sea = document.getElementById('cpSeason').value;
 const mp  = document.getElementById('cpMinPts').value;
 const est = document.getElementById('cpEstado');
 est.textContent = 'a calcular...';
 const amb = document.getElementById('cpAmbito').value;
 let q = '?min_pts=' + mp;
 if(amb === 'season'){ if(sea) q += '&season=' + encodeURIComponent(sea); }
 else { q += '&janela=' + amb; }
 q += '&modo=' + document.getElementById('cpModo').value;
 if(!document.getElementById('cpUsarPmax').checked) q += '&usar_pmax=0';
 if(!document.getElementById('cpExclDup').checked) q += '&duplicados=manter';
 q += CP_AJUSTES;
 fetch('/api/cp/modelos/' + mod + q).then(r=>r.json()).then(function(d){
  CP = d;
  if(d.status !== 'ok' || !d.ok){
   est.textContent = d.motivo || d.mensagem || 'sem dados';
   document.getElementById('cpModelos').innerHTML = '';
   cpDraw(); return;
  }
  est.textContent = d.n_mmp + ' MMP · ' + (d.ambito||'')
    + ' · ' + (d.n_registos||0) + ' curvas'
    + (d.melhor ? ' · menor SEE%: ' + d.melhor.nome : '')
    + (d.season_do_conjunto ? ' · conjunto de ' + d.season_do_conjunto : '')
    + (d.dispersao_datas_dias!=null ? ' · ' + d.dispersao_datas_dias + 'd de dispersão' : '')
    + (Object.keys(d.overrides_aplicados||{}).length ? ' · VALORES EDITADOS' : '');
  cpSeasons(d); cpValidacao(); cpMMPEdit(); cpTabela(); cpDraw();
  cpVeloDraw(); cpResidDraw(); cpAnalise(); cpGloss(); cpDetalhe();
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

function cpAmbitoMudou(){
 const amb = document.getElementById('cpAmbito').value;
 document.getElementById('cpSeasonWrap').style.display =
   amb === 'season' ? '' : 'none';
 cpCarregar();
}

// ── quadro editável dos MMP ──────────────────────────────────────────────
function cpMMPEdit(){
 const box = document.getElementById('cpMMP');
 if(!box) return;
 const todas = (CP && CP.todas_as_duracoes) || [];
 const usados = {};
 (CP.mmp_pts_full||[]).forEach(p=>{ usados[p.t] = true; });
 const ov = CP.overrides_aplicados || {};
 const pc = CP.pace_dos_mmp || {};
 if(!todas.length){ box.innerHTML=''; return; }
 let h = '<div style="color:#8b949e;font-size:11px;margin-bottom:6px;">'
  + 'Watts e segundos que entram no ajuste. Podes alterar qualquer um para '
  + 'ver quanto do CP depende dele — com um grau de liberdade, isso diz mais '
  + 'do que o SEE%. Cinzento = excluído do ajuste.</div>'
  + '<div style="display:flex;flex-wrap:wrap;align-items:flex-end;">';
 todas.forEach(function(p){
  const dentro = !!usados[p.t];
  const editado = ov[String(p.t)] != null;
  const dt = (CP.datas_dos_mmp||{})[String(p.t)] || '';
  const sea = (CP.seasons_dos_mmp||{})[String(p.t)] || '';
  const cor = editado ? '#E3B341' : dentro ? '#8b949e' : '#484f58';
  h += '<div style="margin-right:14px;margin-bottom:6px;opacity:'
   + (dentro?1:0.55) + ';">'
   + '<div style="color:' + cor + ';font-size:10px;">'
   + (editado ? 'editado' : (dt || '—')) + (sea ? ' · ' + sea : '') + '</div>'
   + '<input type="number" class="cpSec" value="' + p.t + '" style="width:64px" title="segundos">'
   + ' <input type="number" class="cpW" data-sec="' + p.t + '" value="'
   + Math.round(p.w) + '" style="width:70px" title="watts"> W'
   + (pc[String(p.t)] && pc[String(p.t)].texto
      ? '<div style="color:#79C0FF;font-size:10px;">' + pc[String(p.t)].texto
        + '</div>' : '')
   + '</div>';
 });
 h += '</div><div style="margin-top:6px;">'
  + '<button onclick="cpAplicarMMP()">Aplicar</button> '
  + '<button onclick="cpReporMMP()">Repor automáticos</button>'
  + '</div>';
 // conjunto de cada season, para se ver o que se ganha e perde em cada modo
 const cj = CP.conjuntos_por_season || {};
 const seasons = Object.keys(cj);
 if(seasons.length > 1){
  const durs = Object.keys(cj[seasons[0]].valores)
    .map(Number).sort(function(a,b){return a-b;});
  h += '<details style="margin-top:8px;"><summary style="cursor:pointer;'
   + 'font-size:12px;color:#8b949e;">Conjunto completo de cada season</summary>'
   + '<table style="border-collapse:collapse;font-size:11px;margin-top:6px;">'
   + '<tr style="color:#8b949e;text-align:left;"><th style="padding-right:14px;">Season</th>'
   + durs.map(function(d){ return '<th style="padding-right:14px;">'
       + (Math.round(d/60*10)/10) + 'min</th>'; }).join('')
   + '<th>Completo</th></tr>';
  seasons.forEach(function(sea){
   const c = cj[sea];
   const usada = sea === CP.season_do_conjunto;
   h += '<tr style="' + (usada?'background:rgba(88,166,255,0.08);':'') + '">'
    + '<td style="padding:4px 14px 4px 0;color:' + (usada?'#58A6FF':'#c9d1d9') + ';">'
    + sea + (usada?' ←':'') + '</td>'
    + durs.map(function(d){
       const v = c.valores[String(d)];
       return '<td style="padding-right:14px;color:' + (v?'#c9d1d9':'#484f58') + ';">'
         + (v ? Math.round(v)+'W' : '—') + '</td>'; }).join('')
    + '<td style="color:#8b949e;">' + c.n_duracoes + '/' + durs.length + '</td></tr>';
  });
  h += '</table><p style="color:#8b949e;font-size:11px;margin-top:6px;">'
   + 'No modo <b>coerente</b> usa-se a linha marcada: todas as durações da '
   + 'mesma época, para a curva descrever um atleta que existiu. O modo '
   + '<b>recuo</b> escolhe o melhor de cada coluna, o que preenche tudo mas '
   + 'pode misturar anos.</p></details>';
 }
 box.innerHTML = h;
}

function cpReporMMP(){
 CP_AJUSTES = '';
 cpCarregar();
}

function cpAplicarMMP(){
 const secs = Array.from(document.querySelectorAll('.cpSec')).map(i=>parseInt(i.value));
 const ws = Array.from(document.querySelectorAll('.cpW')).map(i=>parseFloat(i.value));
 const originais = {};
 (CP.todas_as_duracoes||[]).forEach(p=>{ originais[p.t] = p.w; });
 let q = '&duracoes=' + secs.filter(s=>s>0).join(',');
 secs.forEach(function(sec, i){
  if(!sec || !ws[i]) return;
  // só manda como override o que foi mesmo alterado
  if(originais[sec] == null || Math.abs(originais[sec] - ws[i]) > 0.5)
   q += '&mmp_' + sec + '=' + ws[i];
 });
 CP_AJUSTES = q;
 cpCarregar();
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
 const temPace = !!(CP.pace_dos_mmp);
 const sel = document.getElementById('cpModeloEscolhido');
 sel.innerHTML = nomes.map(n=>'<option>'+n+'</option>').join('');
 let h = '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
  +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
  +'<th style="padding:6px;">Modelo</th><th>CP</th><th>W\\u2032</th>'
  +'<th>SEE%</th><th>df</th><th>Pontos</th><th>Durações usadas</th></tr>';
 nomes.forEach(function(n,i){
  const m = ms[n];
  const df = m.n_pts - m.k_params;
  const wpOk = m.wp_kj==null || (m.wp_kj>=5 && m.wp_kj<=30);
  const cor = m.see_pct<2 ? '#3FB950' : m.see_pct<5 ? '#F0883E' : '#F85149';
  h += '<tr style="border-bottom:1px solid #161b22;'
    + (i===0?'background:rgba(88,166,255,0.06);':'') + '">'
    + '<td style="padding:6px;">' + n + (i===0?' <span style="color:#58A6FF;font-size:10px;">menor SEE%</span>':'') + '</td>'
    + '<td><b>' + m.cp + ' W</b></td>'
    + (temPace ? '<td style="color:#79C0FF;">' + (m.pace||'—') + '</td>' : '')
    + '<td style="color:' + (wpOk?'#c9d1d9':'#F85149') + ';">'
    + (m.wp_kj!=null ? m.wp_kj + ' kJ' : '—')
    + (wpOk?'':' <span style="font-size:10px;">implausível</span>') + '</td>'
    + '<td style="color:' + cor + ';">' + m.see_pct + '%</td>'
    + '<td style="color:' + (df<=1?'#F0883E':'#8b949e') + ';">' + df + '</td>'
    + '<td style="color:#8b949e;">' + m.n_pts + '</td>'
    + '<td style="color:#8b949e;">'
    + (m.pontos_usados||[]).map(p=>Math.round(p.t/60*10)/10+'min').join(', ')
    + '</td></tr>';
 });
 h += '</table>';
 const rp = CP.relacao_pace_watts;
 if(rp) h += '<p style="color:#79C0FF;font-size:11px;margin-top:6px;">'
   + (rp.suficiente
      ? 'Pace convertido a partir dos teus próprios pares potência–velocidade '
        + '(r²=' + rp.r2 + ', n=' + rp.n + ' sessões, ' + rp.watts_min + '–'
        + rp.watts_max + ' W). Depende da tua economia de corrida, não de '
        + 'fórmula genérica — e por isso só é fiável dentro desse intervalo '
        + 'de potências.'
      : 'Sem pace: ' + (rp.nota || rp.erro || 'dados insuficientes'))
   + '</p>';
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
 CP_ESCALA = {X:X, Y:Y, PL:PL, PT:PT, w:w, h:h,
              tmin:tmin, tmax:tmax, pmin:pmin, pmax:pmax, lt:lt, lT:lT};

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
 CP_PONTOS = [];
 pts.forEach(function(p){
  const x=X(p.t), y=Y(p.w);
  const usado = m && (m.pontos_usados||[]).some(u=>Math.abs(u.t-p.t)<1);
  g.fillStyle = usado ? '#c9d1d9' : '#6e7681';
  g.beginPath(); g.arc(x,y,usado?5:4,0,Math.PI*2); g.fill();
  g.strokeStyle='#0d1117'; g.stroke();
  CP_PONTOS.push({x:x, y:y, t:p.t, w:p.w, usado:usado});
 });

 // legenda
 g.textAlign='left'; g.font='10px sans-serif';
 nomes.slice(0,6).forEach(function(n,i){
  g.fillStyle=CP_CORES[i%CP_CORES.length];
  g.fillText(n, PL+w+6, PT+14+i*13);
 });
 g.font='11px sans-serif';
 cpLigarTip();
}

function cpLigarTip(){
 const cv=document.getElementById('chCP');
 const tip=document.getElementById('cpTip');
 if(!cv||!tip||cv._tipCP) return;
 cv._tipCP=true;
 cv.addEventListener('mousemove', function(ev){
  if(!CP_ESCALA || !CP){ tip.style.display='none'; return; }
  const r=cv.getBoundingClientRect();
  const esc=(cv.width/r.width)/(window.devicePixelRatio||1);
  const mx=(ev.clientX-r.left)*esc, my=(ev.clientY-r.top)*esc;
  const e=CP_ESCALA;

  let perto=null, dmin=14;
  CP_PONTOS.forEach(function(p){
   const d=Math.hypot(p.x-mx,p.y-my);
   if(d<dmin){ dmin=d; perto=p; }
  });
  if(perto){
   const k=String(perto.t);
   const rs=(CP.residuos||[]).find(x=>x.t===perto.t) || {};
   const pcp=(CP.pace_dos_mmp||{})[k];
   let h='<b>'+(Math.round(perto.t/60*10)/10)+' min · '+Math.round(perto.w)+' W'
    +(pcp&&pcp.texto?' · '+pcp.texto:'')+'</b>'
    +'<br><span style="color:#8b949e;">'+((CP.datas_dos_mmp||{})[k]||'—')
    +' · '+((CP.seasons_dos_mmp||{})[k]||'—')+'</span>'
    +'<br><span style="color:'+(perto.usado?'#3FB950':'#8b949e')+';">'
    +(perto.usado?'usado no ajuste':'fora do ajuste')+'</span>';
   if(rs.previsto!=null) h+='<br><span style="color:#8b949e;">previsto '
    +rs.previsto+' W · erro '+(rs.erro_w>0?'+':'')+rs.erro_w+' W ('+rs.erro_pct+'%)</span>';
   tip.innerHTML=h; tip.style.display='block';
   tip.style.left=Math.min(ev.clientX-r.left+14, r.width-230)+'px';
   tip.style.top=Math.max(4, ev.clientY-r.top-46)+'px';
   return;
  }

  if(mx<e.PL||mx>e.PL+e.w||my<e.PT||my>e.PT+e.h){ tip.style.display='none'; return; }
  const t = Math.exp(e.lt + (mx-e.PL)/e.w*(e.lT-e.lt));
  const nomes = Object.keys(CP.modelos||{}).sort(function(a,b){
    return CP.modelos[a].see_pct - CP.modelos[b].see_pct; }).slice(0,4);
  const mm = Math.floor(t/60), ss = Math.round(t%60);
  let h='<b>'+(mm?mm+' min ':'')+ss+' s</b>';
  nomes.forEach(function(n,i){
   const mo=CP.modelos[n];
   if(!mo.cp||!mo.wp) return;
   h+='<br><span style="color:'+CP_CORES[i%CP_CORES.length]+';">'+n+'</span> <b>'
    +Math.round(mo.cp+mo.wp/t)+' W</b>';
  });
  const temPace = !!(CP.pace_dos_mmp);
 const sel = document.getElementById('cpModeloEscolhido');
  const mo = (CP.modelos||{})[sel ? sel.value : ''] || CP.melhor;
  if(mo && mo.cp && mo.wp && t>0){
   h+='<br><span style="color:#8b949e;">'+Math.round(mo.wp/t)
    +' W acima do CP — esgota o W\u2032 exactamente neste tempo</span>';
  }
  tip.innerHTML=h; tip.style.display='block';
  tip.style.left=Math.min(ev.clientX-r.left+14, r.width-230)+'px';
  tip.style.top=Math.max(4, ev.clientY-r.top-30)+'px';
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

// ── W' ponto a ponto ─────────────────────────────────────────────────────
function cpVeloDraw(){
 const o = ctx('chVelo', 240); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const v = CP && CP.veloclinic;
 if(!v || !v.pontos || v.pontos.length<2){ noData(g,W,H,'Sem diagnóstico'); return; }
 const jn = CP.janela_cp || [120,1200];
 const pts = v.pontos.map(function(p){
  const mp = (CP.mmp_pts_full||[]).find(x=>Math.abs(x.w-p.p)<0.5) || {};
  return {p:p.p, wp:p.wp, t:mp.t, dentro: mp.t>=jn[0] && mp.t<=jn[1]};
 });
 const ps=pts.map(x=>x.p), ws=pts.map(x=>x.wp);
 const xa=Math.min.apply(null,ps)*0.95, xb=Math.max.apply(null,ps)*1.05;
 const ya=Math.min.apply(null,ws)*0.85, yb=Math.max.apply(null,ws)*1.12;
 const PL=58,PR=16,PT=14,PB=34,w=W-PL-PR,h=H-PT-PB;
 const X=v2=>PL+(v2-xa)/((xb-xa)||1)*w, Y=v2=>PT+h-(v2-ya)/((yb-ya)||1)*h;

 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='11px sans-serif';
 for(let i=0;i<=4;i++){
  const vv=ya+(yb-ya)*i/4, y=Y(vv);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText((vv/1000).toFixed(1)+'kJ', PL-6, y+4);
 }
 // média e banda de ±10%
 const dentro = pts.filter(x=>x.dentro);
 const base = dentro.length>=2 ? dentro : pts;
 const media = base.reduce((a,b)=>a+b.wp,0)/base.length;
 g.fillStyle='rgba(88,166,255,0.12)';
 g.fillRect(PL, Y(media*1.1), w, Math.abs(Y(media*0.9)-Y(media*1.1)));
 g.strokeStyle='#58A6FF'; g.setLineDash([5,4]);
 g.beginPath(); g.moveTo(PL,Y(media)); g.lineTo(PL+w,Y(media)); g.stroke();
 g.setLineDash([]);
 g.fillStyle='#58A6FF'; g.textAlign='left'; g.font='10px sans-serif';
 g.fillText('média ' + (media/1000).toFixed(2) + ' kJ ±10%', PL+4, Y(media)-5);

 g.font='11px sans-serif';
 pts.forEach(function(x){
  const px=X(x.p), py=Y(x.wp);
  g.fillStyle = x.dentro ? '#3FB950' : '#F0883E';
  g.beginPath(); g.arc(px,py,5,0,Math.PI*2); g.fill();
  g.strokeStyle='#0d1117'; g.stroke();
  g.fillStyle='#8b949e'; g.textAlign='center';
  g.fillText((x.t? Math.round(x.t/60*10)/10+'min' : Math.round(x.p)+'W'), px, py-10);
 });
 g.textAlign='center'; g.fillStyle='#8b949e';
 g.fillText('Potência (W)', PL+w/2, PT+h+22);
 g.textAlign='left'; g.font='10px sans-serif';
 g.fillStyle='#3FB950'; g.fillText('\u25CF dentro da janela', PL+4, PT+12);
 g.fillStyle='#F0883E'; g.fillText('\u25CF fora', PL+124, PT+12);
}

// ── resíduos ─────────────────────────────────────────────────────────────
function cpResidDraw(){
 const o = ctx('chResid', 240); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const rs = (CP && CP.residuos) || [];
 if(!rs.length){ noData(g,W,H,'Sem resíduos'); return; }
 const maxE = Math.max(2, Math.max.apply(null, rs.map(r=>Math.abs(r.erro_w)))*1.3);
 const PL=52,PR=16,PT=14,PB=34,w=W-PL-PR,h=H-PT-PB;
 const passo = w/rs.length;
 const Y = e => PT + h/2 - e/maxE*(h/2);

 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='11px sans-serif';
 [-maxE,-maxE/2,0,maxE/2,maxE].forEach(function(e){
  const y=Y(e);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText((e>0?'+':'')+e.toFixed(1)+'W', PL-6, y+4);
 });
 g.strokeStyle='#8b949e'; g.beginPath();
 g.moveTo(PL,Y(0)); g.lineTo(PL+w,Y(0)); g.stroke();

 rs.forEach(function(r,i){
  const cx=PL+passo*i+passo/2, bw=Math.min(38, passo*0.55);
  const y=Y(r.erro_w), y0=Y(0);
  g.fillStyle = r.no_ajuste
    ? (r.erro_w>=0 ? 'rgba(63,185,80,0.65)' : 'rgba(248,81,73,0.65)')
    : 'rgba(139,148,158,0.45)';
  g.fillRect(cx-bw/2, Math.min(y,y0), bw, Math.abs(y-y0)||1);
  g.fillStyle='#c9d1d9'; g.textAlign='center'; g.font='10px sans-serif';
  g.fillText((r.erro_w>0?'+':'')+r.erro_w, cx, y + (r.erro_w>=0?-6:14));
  g.fillStyle='#8b949e';
  g.fillText(Math.round(r.t/60*10)/10+'min', cx, PT+h+16);
  if(!r.no_ajuste) g.fillText('(fora)', cx, PT+h+28);
 });
 g.font='11px sans-serif';
}

function cpAnalise(){
 const el = document.getElementById('cpAnaliseTexto');
 if(!el) return;
 const v = CP && CP.veloclinic, vj = CP && CP.veloclinic_janela;
 const rs = (CP && CP.residuos) || [];
 const jn = CP.janela_cp || [120,1200];
 const noAjuste = rs.filter(r=>r.no_ajuste);
 const maxErro = noAjuste.length
   ? Math.max.apply(null, noAjuste.map(r=>Math.abs(r.erro_pct))) : null;
 let h = '';
 h += '<p style="font-size:11px;color:#8b949e;"><b style="color:#c9d1d9;">'
  + 'W\u2032 ponto a ponto</b> — t × (P − CP) calculado em cada duração. Se '
  + 'uma reserva finita única descrevesse o esforço, os pontos cairiam todos '
  + 'sobre a média. Verde são os que estão dentro de '
  + Math.round(jn[0]/60) + '–' + Math.round(jn[1]/60) + ' min, onde o modelo '
  + 'hiperbólico é válido; laranja os que estão fora e não deviam pesar.';
 if(v && vj) h += ' Aqui: ' + v.metricas.cv + '% de variação em todos os '
  + 'pontos contra ' + vj.metricas.cv + '% só dentro da janela.';
 h += '</p>';
 h += '<p style="font-size:11px;color:#8b949e;"><b style="color:#c9d1d9;">'
  + 'Resíduos</b> — quanto o modelo erra em cada duração, em watts. As '
  + 'coloridas entraram no ajuste; as cinzentas foram excluídas pelo grid '
  + 'search e servem de teste fora da amostra, que é a única verificação '
  + 'honesta com tão poucos pontos.';
 if(maxErro!=null) h += ' O maior erro dentro da amostra é ' + maxErro + '%.';
 const fora = rs.filter(r=>!r.no_ajuste);
 if(fora.length) h += ' Fora da amostra: '
  + fora.map(r=>Math.round(r.t/60*10)/10+'min erra '
      +(r.erro_w>0?'+':'')+r.erro_w+'W ('+r.erro_pct+'%)').join(', ') + '.';
 h += '</p>';
 el.innerHTML = h;
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
 let h = '';
 const jn = CP.janela_cp || [120,1200];
 [['Todos os pontos', CP.veloclinic],
  ['Só ' + Math.round(jn[0]/60) + '–' + Math.round(jn[1]/60) + ' min',
   CP.veloclinic_janela]].forEach(function(par){
  const v = par[1]; if(!v) return;
  h += '<p style="font-size:12px;margin:4px 0;"><b style="color:#8b949e;">'
    + par[0] + ' (n=' + v.n + ')</b> — ' + v.classificacao
    + '<br><span style="color:#8b949e;font-size:11px;">CV do W\u2032 = '
    + v.metricas.cv + '% · média ' + Math.round(v.metricas.mean/1000*100)/100
    + ' kJ · a tendência contra a potência explica '
    + v.metricas.efeito_declive_pct + '% do W\u2032</span></p>';
 });
 h += '<p style="color:#8b949e;font-size:11px;">Veloclinic: se o modelo de CP'
   + ' descrevesse bem o atleta, o W\u2032 calculado em cada ponto'
   + ' (t × (P − CP)) seria constante. A dispersão é o diagnóstico. Comparar'
   + ' as duas linhas diz se a inconsistência vem do modelo ou apenas dos'
   + ' pontos fora da janela onde ele é válido.</p>';
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
   + '<td><button class="cpDel" data-dt="' + dt + '" data-sea="'
   + (r.season||'') + '" style="background:none;border:none;color:#F85149;'
   + 'font-size:11px;cursor:pointer;padding:0;">apagar</button></td>'
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
window.addEventListener('resize', function(){
 cpDraw(); cpVeloDraw(); cpResidDraw(); histDraw(); });
"""


def render():
    from flask import render_template_string
    return render_template_string(page('CP Model', SLUG, BODY, JS))
