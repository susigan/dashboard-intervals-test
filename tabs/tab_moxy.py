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
    <button onclick="mxActualizar(1)" title="Mostra o que seria alterado, sem alterar nada.">Verificar</button>
    <button onclick="mxActualizar(0)" title="Reconcilia com a Intervals.icu.">Actualizar sessões</button>
    <span id="mxEstado" style="color:#8b949e;font-size:12px;margin-left:8px;"></span>
  </div>

  <div id="mxErro" style="display:none;color:#F85149;font-size:12px;
    border-left:3px solid #F85149;padding:6px 10px;margin:6px 0;"></div>

  <div id="mxDatas" style="margin:8px 0;"></div>
  <div id="mxCanais" style="margin:6px 0;"></div>

  <div class="controls" style="margin:4px 0;flex-wrap:wrap;gap:6px 14px;">
    <label class="sel">Alinhar por
      <select id="mxAlinha" onchange="mxAlinhar()">
        <option value="bloco">1.º bloco de trabalho</option>
        <option value="watts">degrau de watts equivalente</option>
        <option value="inicio">início do corte</option>
      </select>
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
  </div>

  <div class="chartbox" style="position:relative;">
    <canvas id="chMoxy" height="380"></canvas>
    <div id="mxTip" style="display:none;position:absolute;pointer-events:none;
      background:#161b22;border:1px solid #30363d;border-radius:6px;
      padding:6px 9px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
  </div>

  <div class="controls" id="mxOffsetsWrap" style="margin-top:4px;flex-wrap:wrap;gap:4px 12px;">
    <span style="color:#8b949e;font-size:12px;">Ajuste fino:</span>
    <span id="mxOffsets" style="display:flex;flex-wrap:wrap;gap:4px 12px;
      align-items:center;"></span>
  </div>

  <div class="controls" style="margin-top:6px;flex-wrap:wrap;gap:6px 12px;">
    <span style="color:#8b949e;font-size:12px;">Intervalo analisado:</span>
    <input type="range" id="mxIni" min="0" max="100" value="0" step="0.2"
           style="width:180px" oninput="mxSlider()">
    <input type="range" id="mxFim" min="0" max="100" value="100" step="0.2"
           style="width:180px" oninput="mxSlider()">
    <span id="mxCorteTxt" style="color:#c9d1d9;font-size:12px;"></span>
    <button onclick="mxAplicarProposta()" title="Repõe os cursores na proposta automática. Não grava.">Repor proposta</button>
    <button onclick="mxTudo()" title="Repõe os cursores na sessão inteira. Não grava.">Sessão inteira</button>
    <button onclick="mxGuardarCorte()" title="Grava este intervalo para esta sessão.">💾 Gravar este intervalo</button>
    <span id="mxCorteEstado" style="color:#8b949e;font-size:11px;"></span>
  </div>
  <p id="mxCorteNota" style="color:#8b949e;font-size:11px;margin:4px 0;"></p>
  <p style="color:#8b949e;font-size:11px;margin:0 0 8px 0;">
    <b>Repor proposta</b> e <b>Sessão inteira</b> só movem os cursores.
    <b>Gravar este intervalo</b> guarda-o para esta sessão. O limite de pausa
    que separa aquecimento de protocolo sai da mediana das pausas da própria
    sessão, porque o descanso varia de protocolo para protocolo.</p>

  <div id="mxBlocos" style="overflow-x:auto;margin-top:10px;"></div>
  <div id="mxDiag" style="margin-top:8px;"></div>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">Como os dados são tratados</summary>
    <div style="font-size:11px;color:#8b949e;margin-top:6px;">
      <p>Pipeline portado do pacote <b>mnirs</b> de Jem Arnold. A ordem não é
      arbitrária: <b>resample → substituir inválidos e outliers → filtrar →
      normalizar</b>. Filtrar antes de remover outliers espalha-os pelos
      vizinhos.</p>
      <p><b>Outliers</b> contra a <b>mediana</b> local, não a média — com a
      média, um pico desloca o próprio centro contra o qual está a ser julgado.</p>
      <p>A FC, o DFA-a1 e a respiração vêm todos da série de RR: se a cinta
      falha, os três herdam os buracos, e por isso levam o filtro de
      artefactos. O SmO2 e o THb vêm do Moxy e não são afectados.</p>
    </div>
  </details>

  <h2 style="font-size:15px;margin-top:18px;">Rede causal entre canais</h2>
  <div class="controls" style="flex-wrap:wrap;gap:6px 12px;">
    <button onclick="mxRede()">Calcular</button>
    <label class="sel"><input type="checkbox" id="mxRdDif" checked> diferenciar séries</label>
    <label class="sel"><input type="checkbox" id="mxRdCond" checked> condicionar aos watts</label>
    <label class="sel">Lag máx.
      <select id="mxRdLag"><option>3</option><option selected>5</option>
        <option>10</option></select></label>
    <label class="sel">Correlação mín.
      <select id="mxRdCorr"><option>0.2</option><option selected>0.3</option>
        <option>0.5</option></select></label>
    <span id="mxRdEstado" style="color:#8b949e;font-size:12px;"></span>
  </div>
  <div id="mxRede" style="overflow-x:auto;margin-top:6px;"></div>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">O que a rede diz e o que não diz</summary>
    <div style="font-size:11px;color:#8b949e;margin-top:6px;">
      <p>Adaptado do <b>PhysioNexus</b> (Evan Peikon). Correlação selecciona
      os pares, Granger dá-lhes direcção.</p>
      <p><b>Granger não é causalidade.</b> Mede precedência preditiva: se o
      passado de A ajuda a prever B além do que o passado de B já explica,
      A precede B. Num sistema com um controlador comum isso é uma pista,
      não uma prova.</p>
      <p><b>Condicionar aos watts</b> existe porque o protocolo é causa comum
      de tudo — os watts sobem por decisão tua e o resto responde. Sem
      condicionar, a rede redescobre o protocolo. A pergunta passa a ser: o
      canal A acrescenta poder preditivo sobre B <i>além do que a potência
      já explica</i>?</p>
      <p><b>Diferenciar</b> porque o Granger pressupõe séries estacionárias.
      Ou se diferenciam todas ou nenhuma — misturar compara níveis com
      variações.</p>
      <p>Os p são corrigidos por Benjamini-Hochberg. Pares onde ambos os
      sentidos passam e nenhum domina ficam marcados <b>ambíguos</b>.</p>
      <p><b>Canais mecânicos</b> — watts, cadência, torque, velocidade — entram
      só como controlo e nunca como nós da rede. São decisão tua, não resposta
      fisiológica, e testá-los como causas seria redescobrir o protocolo.</p>
      <p>O <b>limitador</b> sai do peso de cada sistema pelo F das arestas que
      dele partem menos as que nele chegam. Um sistema que só recebe está a
      responder; um que só emite está a impor o ritmo. Usa-se o F e não a
      contagem: uma aresta com F=169 e outra com F=17 não valem o mesmo.</p>
    </div>
  </details>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">Todas as sessões</summary>
    <div id="mxLista" style="overflow-x:auto;margin-top:6px;"></div>
  </details>

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
  est.textContent = MX_SESSOES.length + ' sessões com Moxy';
  if(!MX_SEL.length && MX_SESSOES.length) MX_SEL=[String(MX_SESSOES[0].id)];
  MX_SEL = MX_SEL.filter(id=>MX_SESSOES.some(x=>String(x.id)===id));
  mxDatasChips(); mxLista();
  if(MX_SEL.length) mxCarregar();
  else { MX=null; MX_DADOS={}; mxDraw(); mxBlocosTabela();
         document.getElementById('mxDiag').innerHTML=''; }
 }).catch(e=>{ est.textContent = 'erro: ' + e.message; });
}




function mxTabelaDegraus(){
 const box=document.getElementById('mxDiag');
 if(!MXC||!box) return;
 const pares=MXC.degraus_emparelhados||[];
 if(!pares.length) return;
 const canal=(MX.canais_nirs||[]).find(k=>k.indexOf('smo2')===0)
   ? 'smo2' : (MX.canais_nirs[0]||'').split('_')[0];
 let h='<h3 style="font-size:13px;color:#8b949e;margin:12px 0 4px 0;">'
  +'Degraus emparelhados por potência</h3>'
  +'<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr style="color:#8b949e;text-align:left;"><th style="padding-right:14px;">Watts</th>'
  + MXC.sessoes.map(function(s){
     return '<th style="padding-right:14px;">#'+s.indice+' '+canal+'</th>'; }).join('')
  +'<th>Δ</th></tr>';
 pares.forEach(function(p){
  const vs=MXC.sessoes.map(function(s){
   const d=p.por_sessao[String(s.indice-1)];
   return d ? d[canal] : null; });
  const validos=vs.filter(v=>v!=null);
  const delta=validos.length>1
   ? Math.round((validos[validos.length-1]-validos[0])*10)/10 : null;
  h+='<tr><td style="padding-right:14px;">'+p.watts_centro+' W'
   +(p.watts_min!==p.watts_max
     ? ' <span style="color:#6e7681;">('+p.watts_min+'–'+p.watts_max+')</span>'
     : '')+'</td>'
   + vs.map(function(v){ return '<td style="padding-right:14px;">'
       +(v!=null?v:'—')+'</td>'; }).join('')
   +'<td style="color:'+(delta==null?'#8b949e':delta>0?'#3FB950':'#F85149')+';">'
   +(delta==null?'—':(delta>0?'+':'')+delta)+'</td></tr>';
 });
 h+='</table><p style="color:#8b949e;font-size:11px;">Média de cada degrau, '
  +'descartando os primeiros 30 s: o SmO2 leva tempo a responder a uma '
  +'mudança de carga, e incluir a transição mistura o degrau novo com o '
  +'anterior. Δ = última sessão menos a primeira.</p>';
 box.innerHTML = h + box.innerHTML;
}

// MX_SEL: ids escolhidos. MX_DADOS: {id: resposta}. MX_OFF: desvio manual
// em segundos por id. Com uma sessao so', tudo funciona como antes.
let MX_SEL = [], MX_DADOS = {}, MX_OFF = {};

function mxRede(){
 const ids=Object.keys(MX_DADOS);
 const est=document.getElementById('mxRdEstado');
 const box=document.getElementById('mxRede');
 if(!ids.length){ est.textContent='escolhe uma sessão'; return; }
 if(ids.length>1) est.textContent='usa a 1.ª sessão seleccionada';
 const id=ids[0];
 const c=mxCorteDe(id);
 const q='?lag='+document.getElementById('mxRdLag').value
  +'&corr='+document.getElementById('mxRdCorr').value
  +'&inicio='+Math.round(c[0])+'&fim='+Math.round(c[1])
  +(document.getElementById('mxRdDif').checked?'':'&diferenciar=0')
  +(document.getElementById('mxRdCond').checked?'':'&condicionar=0');
 est.textContent='a calcular...';
 fetch('/api/moxy/rede/'+id+q).then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){ est.textContent=d.mensagem||d.motivo||'sem dados';
   box.innerHTML=''; return; }
  est.textContent=d.n_pares_testados+' pares testados · '+d.n_dirigidas
   +' com direcção · '+d.n_indecisas+' ambíguos'
   +(d.controlo?' · condicionado a '+d.controlo:' · SEM condicionar');
  let h='';
  const L=d.limitador||{};
  if(L.sistema||L.leitura){
   const cores={periferico:'#F85149',cardiaco:'#58A6FF',
                respiratorio:'#3FB950',autonomico:'#A371F7'};
   const cor=cores[L.sistema]||'#8b949e';
   h+='<div style="border-left:3px solid '+cor+';padding:6px 10px;'
    +'margin-bottom:10px;">'
    +'<b style="color:'+cor+';">LIMITADOR: '
    +(L.sistema?L.sistema.toUpperCase():'indeterminado')+'</b><br>'
    +'<span style="font-size:12px;">'+(L.leitura||'')+'</span>';
   const cp=L.controlo_pct||{};
   const ks=Object.keys(cp).sort(function(a,b){ return cp[b]-cp[a]; });
   if(ks.length) h+='<br><span style="font-size:11px;color:#8b949e;">'
    +ks.map(function(k){ return k+' '+cp[k]+'%'; }).join(' · ')
    +' &nbsp;(peso pelo F das arestas que partem de cada sistema)</span>';
   h+='<br><span style="font-size:10px;color:#8b949e;">'+(L.aviso||'')
    +'</span></div>';
  }
  if(d.fontes && d.fontes.length)
   h+='<p style="font-size:12px;"><b style="color:#3FB950;">Fontes:</b> '
    +d.fontes.join(', ')+' &nbsp; <b style="color:#F0883E;">Sumidouros:</b> '
    +(d.sumidouros||[]).join(', ')+'</p>';
  if(d.mecanicos_excluidos && d.mecanicos_excluidos.length)
   h+='<p style="font-size:11px;color:#8b949e;">Mecânicos usados só como '
    +'controlo, nunca testados como causa: <b>'
    +d.mecanicos_excluidos.join(', ')+'</b>. "A potência precede a subida da '
    +'FC" não é um achado — é a definição de treinar.</p>';
  h+='<table style="border-collapse:collapse;font-size:11px;">'
   +'<tr style="color:#8b949e;text-align:left;">'
   +'<th style="padding-right:14px;">De</th><th style="padding-right:14px;">Para</th>'
   +'<th style="padding-right:14px;">F</th><th style="padding-right:14px;">p</th>'
   +'<th style="padding-right:14px;">Lag</th><th style="padding-right:14px;">r</th>'
   +'<th>Direcção</th></tr>';
  (d.arestas||[]).forEach(function(e){
   h+='<tr><td style="padding-right:14px;color:#3FB950;">'+e.de+'</td>'
    +'<td style="padding-right:14px;">'+e.para+'</td>'
    +'<td style="padding-right:14px;">'+e.f+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'+e.p+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'+e.lag+'s</td>'
    +'<td style="padding-right:14px;color:'
    +(e.sinal==='-'?'#F0883E':'#3FB950')+';">'+e.correlacao+'</td>'
    +'<td style="color:#8b949e;">'+(e.direccao||'')
    +(e.racio_f?' ('+e.racio_f+'×)':'')+'</td></tr>';
  });
  (d.indecisas||[]).filter(e=>e.direccao==='ambigua').forEach(function(e){
   h+='<tr style="opacity:.6;"><td style="padding-right:14px;">'+e.de+'</td>'
    +'<td style="padding-right:14px;">'+e.para+'</td>'
    +'<td style="padding-right:14px;">'+e.f+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'+e.p+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'+e.lag+'s</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'+e.correlacao+'</td>'
    +'<td style="color:#F0883E;">ambíguo</td></tr>';
  });
  h+='</table>';
  const dg=d.diagnostico||{};
  const dif=Object.keys(dg).filter(k=>dg[k] && dg[k].diferenciada);
  const exc=Object.keys(dg).filter(k=>dg[k] && dg[k].excluido);
  h+='<p style="color:#8b949e;font-size:11px;margin-top:6px;">'
   +'Corte de p corrigido: '+(d.p_corte_bh!=null?d.p_corte_bh:'nenhum par passou')
   +' · lag até '+d.max_lag+'s'
   +(dif.length?' · diferenciadas: '+dif.join(', '):' · nenhuma diferenciada')
   +(exc.length?' · excluídas: '+exc.map(k=>k+' ('+dg[k].excluido+')').join(', '):'')
   +'</p>';
  box.innerHTML=h;
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

function mxErro(msg){
 const e=document.getElementById('mxErro');
 if(!e) return;
 if(!msg){ e.style.display='none'; e.innerHTML=''; return; }
 e.style.display='block'; e.innerHTML=msg;
}

// Chips de data acima do grafico. Um clique escolhe, outro tira. Varias
// escolhidas = comparacao.
function mxDatasChips(){
 const box=document.getElementById('mxDatas');
 if(!box) return;
 if(!MX_SESSOES.length){
  box.innerHTML='<span style="color:#8b949e;font-size:12px;">Nenhuma sessão '
   +'com a tag "Moxy".</span>'; return; }
 box.innerHTML='<div style="display:flex;flex-wrap:wrap;gap:6px;">'
  + MX_SESSOES.map(function(s2){
   const i=MX_SEL.indexOf(String(s2.id));
   const on=i>=0;
   const cor=on?mxCorSessao(i):'#30363d';
   return '<button class="mxDia" data-id="'+s2.id+'" '
    + 'style="border:1px solid '+cor+';border-radius:14px;padding:3px 11px;'
    + 'background:'+(on?'rgba(88,166,255,0.10)':'transparent')+';'
    + 'color:'+(on?cor:'#8b949e')+';font-size:11px;cursor:pointer;">'
    + (on?'● ':'○ ') + s2.data
    + '<span style="opacity:.7;"> · '+(s2.modalidade||s2.tipo||'')+'</span>'
    + '</button>';
  }).join('') + '</div>';
 Array.prototype.forEach.call(box.querySelectorAll('.mxDia'), function(el){
  el.addEventListener('click', function(){
   const id=el.getAttribute('data-id');
   mxAlternarSessao(id, MX_SEL.indexOf(String(id))<0);
  });
 });
}

function mxCanaisEdit(){
 const box=document.getElementById('mxCanais');
 const ids=Object.keys(MX_DADOS);
 if(!box||!ids.length) return;
 const base=MX_DADOS[ids[0]];
 const nirs=base.canais_nirs||[], ctx2=base.canais_contexto||[];
 const todos=nirs.concat(ctx2);
 if(!Object.keys(MX_ON).length)
  nirs.forEach(function(k){ MX_ON[k]=true; });
 box.innerHTML = '<div style="display:flex;flex-wrap:wrap;gap:6px;'
  + 'align-items:center;">'
  + todos.map(function(k){
   const on = MX_ON[k] === true;
   const nirsQ = nirs.indexOf(k)>=0;
   const cor = MX_CORES[k]||'#c9d1d9';
   return '<button class="mxC" data-k="'+k+'" '
    + 'style="border:1px solid '+(on?cor:'#30363d')+';border-radius:14px;'
    + 'padding:3px 11px;background:'+(on?'rgba(255,255,255,0.05)':'transparent')
    + ';color:'+(on?cor:'#6e7681')+';font-size:11px;cursor:pointer;">'
    + (on?'● ':'○ ') + k + (nirsQ?'':' *') + '</button>';
  }).join('')
  + '<span style="color:#6e7681;font-size:10px;">* de contexto, não filtrado</span>'
  + '</div>';
 Array.prototype.forEach.call(box.querySelectorAll('.mxC'), function(el){
  el.addEventListener('click', function(){
   const k=el.getAttribute('data-k');
   MX_ON[k] = !(MX_ON[k]===true);
   mxCanaisEdit(); mxDraw();
  });
 });
}

function mxAlternarSessao(id, on){
 mxErro(null);
 id = String(id);
 if(on){ if(MX_SEL.indexOf(id)<0) MX_SEL.push(id); }
 else { MX_SEL = MX_SEL.filter(x=>x!==id); delete MX_DADOS[id]; delete MX_OFF[id]; }
 mxCarregar();
}

function mxCarregar(){
 if(!MX_SEL.length){
  MX=null; MX_DADOS={}; mxDraw(); mxDiagnostico(); mxBlocosTabela();
  document.getElementById('mxEstado').textContent='escolhe uma sessão';
  return;
 }
 const est=document.getElementById('mxEstado');
 const q = '?fc=' + document.getElementById('mxFc').value
   + (document.getElementById('mxNorm').value
      ? '&normalizar=' + document.getElementById('mxNorm').value : '');
 est.textContent='a carregar ' + MX_SEL.length + ' sessão(ões)...';
 Promise.all(MX_SEL.map(function(id){
  return fetch('/api/moxy/dados/'+id+q).then(r=>r.json())
   .then(function(d){ return {id:id, d:d}; })
   .catch(function(e){ return {id:id, d:{status:'erro',mensagem:e.message}}; });
 })).then(function(res){
  MX_DADOS={}; let maus=[];
  res.forEach(function(r){
   if(r.d && r.d.status==='ok'){ MX_DADOS[r.id]=r.d; }
   else {
    const s2=MX_SESSOES.find(x=>String(x.id)===r.id)||{};
    let m='<b>'+(s2.data||r.id)+'</b>: '+(r.d.mensagem||'erro desconhecido');
    if(r.d.detalhe_dos_streams)
     m+='<br><span style="color:#8b949e;">Streams: '
      + r.d.detalhe_dos_streams.map(function(x){
         return x.stream+' ('+x.n_pontos+')'; }).join(' · ')+'</span>';
    maus.push(m);
   }
  });
  mxErro(maus.length ? maus.join('<br>') : null);
  mxDatasChips();
  const ids=Object.keys(MX_DADOS);
  MX = ids.length===1 ? MX_DADOS[ids[0]] : null;
  if(ids.length===1) mxCorteInicial();
  est.textContent = ids.length + (ids.length===1?' sessão':' sessões a comparar')
   + (maus.length ? ' · ' + maus.length + ' com problema' : '');
  mxCanaisEdit(); mxAlinhar();
 });
}

// ── alinhamento ─────────────────────────────────────────────────────────
// O relogio de cada sessao nao serve: uma tem 12 min de aquecimento e outra
// nao. Alinha-se pelo protocolo. Por omissao, pelo inicio do primeiro bloco
// de trabalho dentro do corte -- e' o instante que existe em todas e que
// significa o mesmo em todas.
function mxRefAlinhamento(id){
 const d=MX_DADOS[id]; if(!d) return 0;
 const modo=document.getElementById('mxAlinha').value;
 const corte=mxCorteDe(id);
 if(modo==='inicio') return corte[0];
 const bl=((d.blocos||{}).blocos)||[];
 const ons=bl.filter(b=>b.on && b.t1>=corte[0] && b.t0<=corte[1]);
 if(!ons.length) return corte[0];
 if(modo==='watts'){
  // degrau equivalente: o primeiro ON cuja potencia media mais se aproxima
  // da do primeiro ON da sessao de referencia. No Row e no Ski os watts
  // variam de sessao para sessao, por isso isto e' opcao e nao omissao.
  const ref=Object.keys(MX_DADOS)[0];
  if(id===ref) return ons[0].t0;
  const dr=MX_DADOS[ref];
  const cr=mxCorteDe(ref);
  const onsRef=(((dr.blocos||{}).blocos)||[])
    .filter(b=>b.on && b.t1>=cr[0] && b.t0<=cr[1]);
  if(!onsRef.length) return ons[0].t0;
  const alvo=onsRef[0].watts_medio;
  let melhor=ons[0], dmin=1e18;
  ons.forEach(function(b){
   const dd=Math.abs((b.watts_medio||0)-(alvo||0));
   if(dd<dmin){ dmin=dd; melhor=b; }
  });
  return melhor.t0;
 }
 return ons[0].t0;
}

function mxCorteDe(id){
 const d=MX_DADOS[id]; if(!d) return [0,0];
 const t=d.tempo||[];
 if(!t.length) return [0,0];
 if(MX_SEL.length===1 && MX_CORTE) return MX_CORTE;
 const g=d.corte_guardado, p=d.corte_proposto;
 if(g && g.inicio_s!=null) return [g.inicio_s, g.fim_s];
 if(p && p.ok) return [p.inicio_s, p.fim_s];
 return [t[0], t[t.length-1]];
}

function mxAlinhar(){
 mxOffsetsUI(); mxDraw(); mxDiagnostico(); mxBlocosTabela();
}

function mxOffsetsUI(){
 const box=document.getElementById('mxOffsets');
 if(!box) return;
 const ids=Object.keys(MX_DADOS);
 if(ids.length<2){ box.innerHTML=''; return; }
 box.innerHTML = ids.map(function(id,i){
  const s=MX_SESSOES.find(x=>String(x.id)===id)||{};
  const off=MX_OFF[id]||0;
  return '<label class="sel" style="white-space:nowrap;">'
   + '<span style="color:'+mxCorSessao(i)+';">'+(i+1)+'· '+(s.data||id)+'</span> '
   + '<input type="range" class="mxOff" data-id="'+id+'" data-i="'+i+'" '
   + 'min="-300" max="300" step="1" value="'+off+'" style="width:130px"> '
   + '<span id="mxOffTxt'+i+'">'+(off>0?'+':'')+off+'s</span></label>';
 }).join('');
 Array.prototype.forEach.call(box.querySelectorAll('.mxOff'), function(el){
  el.addEventListener('input', function(){
   mxOffset(el.getAttribute('data-id'), el.value);
  });
 });
}

function mxOffset(id, v){
 MX_OFF[id]=parseFloat(v);
 mxDraw(); mxBlocosTabela();
 const i=Object.keys(MX_DADOS).indexOf(String(id));
 const el=document.getElementById('mxOffTxt'+i);
 if(el) el.textContent=(v>0?'+':'')+v+'s';
}

function mxCorSessao(i){
 return ['#F85149','#58A6FF','#3FB950','#E3B341','#A371F7','#F0883E'][i%6];
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
 if(b.ok) n+=' '+b.n_on+' blocos de trabalho e '+b.n_off+' de recuperação, '
   +'de '+(b.fonte||'?')+(b.limiar_w?' (limiar '+b.limiar_w+' W)':'')+'.';
 const p2=MX.corte_proposto||{};
 if(p2.aviso) n+=' '+p2.aviso+'.';
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
 const o = ctx('chMoxy', 380); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const ids=Object.keys(MX_DADOS);
 if(!ids.length){ noData(g,W,H,'Escolhe uma sessão na lista'); return; }
 const activos=Object.keys(MX_ON).filter(k=>MX_ON[k]===true);
 if(!activos.length){ noData(g,W,H,'Escolhe pelo menos uma métrica'); return; }

 // series alinhadas: tempo relativo ao ponto de referencia de cada sessao
 const series=[];
 ids.forEach(function(id, si){
  const d=MX_DADOS[id]; const t=d.tempo||[];
  const corte=mxCorteDe(id);
  const ref=mxRefAlinhamento(id)+(MX_OFF[id]||0);
  activos.forEach(function(k){
   const v=d.canais[k]; if(!v) return;
   const pts=[];
   for(let n=0;n<t.length;n++){
    if(t[n]<corte[0]||t[n]>corte[1]) continue;
    if(v[n]==null) continue;
    pts.push([t[n]-ref, v[n]]);
   }
   if(pts.length) series.push({
    id:id, canal:k, si:si, pts:pts,
    nirs:(d.canais_nirs||[]).indexOf(k)>=0,
    rotulo: ids.length>1 ? k+'_'+(si+1) : k});
  });
 });
 if(!series.length){ noData(g,W,H,'Sem dados no intervalo'); return; }

 let ta=1e18, tb=-1e18;
 series.forEach(function(s2){
  if(s2.pts[0][0]<ta) ta=s2.pts[0][0];
  if(s2.pts[s2.pts.length-1][0]>tb) tb=s2.pts[s2.pts.length-1][0];
 });

 const PL=54,PR=128,PT=18,PB=40,w=W-PL-PR,h=H-PT-PB;
 const X=v=>PL+(v-ta)/((tb-ta)||1)*w;
 MX_ESC={X:X,PL:PL,PT:PT,w:w,h:h,t0:ta,t1:tb};

 // Potencia de TODAS as sessoes em fundo, cada uma na sua cor. Sem isto
 // nao se ve porque e' que os degraus nao alinham: uma sessao pode comecar
 // a 140 W e outra a 117 W, e a diferenca so' aparece aqui.
 const wsers=[];
 ids.forEach(function(id, si){
  const d=MX_DADOS[id], t=d.tempo||[], v=d.canais.watts;
  if(!v) return;
  const corte=mxCorteDe(id), ref=mxRefAlinhamento(id)+(MX_OFF[id]||0);
  const pts=[];
  for(let n=0;n<t.length;n++){
   if(t[n]<corte[0]||t[n]>corte[1]||v[n]==null) continue;
   pts.push([t[n]-ref, v[n]]);
  }
  if(pts.length) wsers.push({si:si, pts:pts});
 });
 let wmaxG=0;
 wsers.forEach(function(ws){ ws.pts.forEach(function(p){
  if(p[1]>wmaxG) wmaxG=p[1]; }); });
 if(wmaxG>0){
  wsers.forEach(function(ws){
   const cor = ids.length>1 ? mxCorSessao(ws.si) : '#8b949e';
   g.strokeStyle=cor; g.globalAlpha=0.30; g.lineWidth=1;
   g.beginPath();
   ws.pts.forEach(function(p,n){
    const y=PT+h-(p[1]/wmaxG)*h*0.42;
    n?g.lineTo(X(p[0]),y):g.moveTo(X(p[0]),y);
   });
   g.stroke(); g.globalAlpha=1;
  });
  g.fillStyle='#6e7681'; g.font='10px sans-serif'; g.textAlign='left';
  g.fillText('watts em fundo (0–'+Math.round(wmaxG)+')', PL+4, PT+h-4);
 }

 // escala partilhada pelos NIRS; contexto normaliza-se por canal
 const nirs=series.filter(s2=>s2.nirs);
 let lo=1e18, hi=-1e18;
 nirs.forEach(s2=>s2.pts.forEach(function(p){
  if(p[1]<lo)lo=p[1]; if(p[1]>hi)hi=p[1]; }));
 if(lo>hi){ lo=0; hi=100; }
 const pad=(hi-lo)*0.08||1; lo-=pad; hi+=pad;
 const Y=v=>PT+h-(v-lo)/((hi-lo)||1)*h;

 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='11px sans-serif';
 for(let n=0;n<=4;n++){
  const v=lo+(hi-lo)*n/4, y=Y(v);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText(Math.round(v), PL-6, y+4);
 }
 // marca do zero: o ponto de alinhamento
 if(ta<0 && tb>0){
  g.strokeStyle='#8b949e'; g.setLineDash([4,4]);
  g.beginPath(); g.moveTo(X(0),PT); g.lineTo(X(0),PT+h); g.stroke();
  g.setLineDash([]);
  g.fillStyle='#8b949e'; g.textAlign='center'; g.font='9px sans-serif';
  g.fillText('alinhamento', X(0), PT+10);
  g.font='11px sans-serif';
 }
 g.textAlign='center'; g.fillStyle='#8b949e';
 for(let n=0;n<=5;n++){
  const tv=ta+(tb-ta)*n/5;
  const m=Math.floor(Math.abs(tv)/60);
  g.fillText((tv<0?'-':'')+m+' min', X(tv), PT+h+18);
 }

 // NIRS na escala comum; contexto na sua
 nirs.forEach(function(s2){
  g.strokeStyle=ids.length>1 ? mxCorSessao(s2.si) : (MX_CORES[s2.canal]||'#c9d1d9');
  g.lineWidth = ids.length>1 ? (s2.canal==='smo2'?2.2:1.4) : 2;
  g.setLineDash(mxTraco(s2.canal, ids.length));
  g.beginPath();
  s2.pts.forEach(function(p,n){ n?g.lineTo(X(p[0]),Y(p[1]))
                                 :g.moveTo(X(p[0]),Y(p[1])); });
  g.stroke(); g.setLineDash([]); g.lineWidth=1;
 });
 const outros=series.filter(s2=>!s2.nirs && s2.canal!=='watts');
 outros.forEach(function(s2){
  const vs=s2.pts.map(p=>p[1]);
  let a=Math.min.apply(null,vs), b=Math.max.apply(null,vs);
  if(b===a) b=a+1;
  const Y2=v=>PT+h-(v-a)/((b-a)||1)*h;
  g.strokeStyle=ids.length>1 ? mxCorSessao(s2.si) : (MX_CORES[s2.canal]||'#c9d1d9');
  g.globalAlpha=0.55; g.lineWidth=1;
  g.setLineDash(mxTraco(s2.canal, ids.length));
  g.beginPath();
  s2.pts.forEach(function(p,n){ n?g.lineTo(X(p[0]),Y2(p[1]))
                                 :g.moveTo(X(p[0]),Y2(p[1])); });
  g.stroke(); g.setLineDash([]); g.globalAlpha=1;
 });

 g.textAlign='left'; g.font='10px sans-serif';
 series.forEach(function(s2,n){
  g.fillStyle=ids.length>1 ? mxCorSessao(s2.si) : (MX_CORES[s2.canal]||'#c9d1d9');
  g.fillText('\u2500 '+s2.rotulo, PL+w+6, PT+12+n*12);
 });
 if(ids.length>1){
  g.fillStyle='#6e7681';
  g.fillText('cor = sessão · traço = canal', PL+w+6, PT+12+series.length*12+8);
 }
 g.font='11px sans-serif';
 mxLigarTip();
}

// Traco por canal quando ha varias sessoes: a cor passa a identificar a
// sessao, portanto o canal precisa de outra dimensao visual.
function mxTraco(canal, nSessoes){
 if(nSessoes<2) return [];
 return {smo2:[], thb:[6,3], o2hb:[2,2], hhb:[8,3,2,3],
         heartrate:[4,2], respiration:[1,3], dfa_a1:[10,4],
         cadence:[3,3], torque:[5,5], velocity_smooth:[7,2]}[canal] || [];
}

function mxLigarTip(){
 const cv=document.getElementById('chMoxy');
 const tip=document.getElementById('mxTip');
 if(!cv||!tip||cv._tipMx) return;
 cv._tipMx=true;
 cv.addEventListener('mousemove', function(ev){
  // Le de MX_DADOS e nao de MX: em comparacao MX e' null, e o tooltip
  // deixava de funcionar exactamente quando era mais util.
  const ids=Object.keys(MX_DADOS);
  if(!MX_ESC || !ids.length){ tip.style.display='none'; return; }
  const r=cv.getBoundingClientRect();
  const esc=(cv.width/r.width)/(window.devicePixelRatio||1);
  const mx=(ev.clientX-r.left)*esc;
  const e=MX_ESC;
  if(mx<e.PL||mx>e.PL+e.w){ tip.style.display='none'; return; }
  const trel=e.t0+(mx-e.PL)/e.w*(e.t1-e.t0);   // tempo relativo ao alinhamento
  const m=Math.floor(Math.abs(trel)/60), sg=Math.round(Math.abs(trel)%60);
  let h='<b>'+(trel<0?'-':'')+m+':'+String(sg).padStart(2,'0')+'</b>'
   +(ids.length>1?' <span style="color:#8b949e;">do alinhamento</span>':'');
  const activos=Object.keys(MX_ON).filter(k=>MX_ON[k]===true);
  ids.forEach(function(id, si){
   const d=MX_DADOS[id];
   const t=d.tempo||[];
   const ref=mxRefAlinhamento(id)+(MX_OFF[id]||0);
   const alvo=trel+ref;                        // tempo absoluto nesta sessao
   let idx=-1, dmin=1e18;
   for(let n=0;n<t.length;n++){
    const dd=Math.abs(t[n]-alvo);
    if(dd<dmin){ dmin=dd; idx=n; }
   }
   if(idx<0 || dmin>5) return;                 // fora desta sessao
   const s2=MX_SESSOES.find(x=>String(x.id)===id)||{};
   if(ids.length>1)
    h+='<br><span style="color:'+mxCorSessao(si)+';font-weight:600;">'
     +(si+1)+'· '+(s2.data||id)+'</span>';
   // degrau em que estamos, com os watts medios do lap
   const bl=((d.blocos||{}).blocos)||[];
   const b=bl.find(x=>t[idx]>=x.t0 && t[idx]<=x.t1);
   if(b) h+='<br><span style="color:#8b949e;font-size:10px;">'
    +(b.on?'trabalho':'recuperação')
    +(b.watts_medio!=null?' · média '+Math.round(b.watts_medio)+' W':'')
    +(b.tipo?' ['+b.tipo+']':'')+'</span>';
   activos.forEach(function(k){
    const v=(d.canais[k]||[])[idx];
    if(v==null) return;
    h+='<br><span style="color:'
     +(ids.length>1?mxCorSessao(si):(MX_CORES[k]||'#c9d1d9'))+';">'
     +k+(ids.length>1?'_'+(si+1):'')+'</span> '+v;
   });
  });
  tip.innerHTML=h; tip.style.display='block';
  tip.style.left=Math.min(ev.clientX-r.left+14, r.width-200)+'px';
  tip.style.top=Math.max(4, ev.clientY-r.top-40)+'px';
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

function mxDiagnostico(){
 const box=document.getElementById('mxDiag');
 if(!box) return;
 const ids=Object.keys(MX_DADOS);
 if(ids.length>1){
  // com varias sessoes, mostrar so' o essencial de cada uma
  let hh='<table style="border-collapse:collapse;font-size:11px;">'
   +'<tr style="color:#8b949e;text-align:left;"><th style="padding-right:14px;">Sessão</th>'
   +'<th style="padding-right:14px;">Canais</th><th style="padding-right:14px;">Artefacto</th>'
   +'<th>Corte</th></tr>';
  ids.forEach(function(id,si){
   const dd=MX_DADOS[id], s2=MX_SESSOES.find(x=>String(x.id)===id)||{};
   const ar=dd.artefactos||{}, c=mxCorteDe(id);
   hh+='<tr><td style="padding-right:14px;color:'+mxCorSessao(si)+';">'
    +(si+1)+'· '+(s2.data||id)+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'
    +(dd.canais_nirs||[]).join(', ')+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'
    +(ar.pct_acima_do_limiar!=null?ar.pct_acima_do_limiar+'%':'—')+'</td>'
    +'<td style="color:#8b949e;">'+Math.round((c[1]-c[0])/60)+' min</td></tr>';
  });
  box.innerHTML=hh+'</table>';
  return;
 }
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
  +'<th style="padding:6px;width:30px;"></th><th>Data</th><th>Modalidade</th>'
  +'<th>Sessão</th><th>Duração</th><th>SmO2 médio</th><th>Tags</th></tr>';
 MX_SESSOES.forEach(function(s2){
  const on=MX_SEL.indexOf(String(s2.id))>=0;
  const i2=MX_SEL.indexOf(String(s2.id));
  h+='<tr style="border-bottom:1px solid #161b22;'
   +(on?'background:rgba(88,166,255,0.06);':'')+'">'
   +'<td style="padding:6px;"><input type="checkbox" class="mxSel" '
   +'data-id="'+s2.id+'"'+(on?' checked':'')+'></td>'
   +'<td'+(on?' style="color:'+mxCorSessao(i2)+';font-weight:600;"':'')+'>'
   +s2.data+(on?' <span style="font-size:10px;">('+(i2+1)+')</span>':'')+'</td>'
   +'<td style="color:#8b949e;">'+(s2.modalidade||s2.tipo||'—')+'</td>'
   +'<td style="color:#8b949e;">'+(s2.nome||'—')+'</td>'
   +'<td style="color:#8b949e;">'+(s2.duracao_min?s2.duracao_min+' min':'—')+'</td>'
   +'<td>'+(s2.smo2_no_sumario!=null?Math.round(s2.smo2_no_sumario):'—')+'</td>'
   +'<td style="color:#8b949e;">'+((s2.tags||[]).join(', ')||'—')+'</td></tr>';
 });
 h+='</table>';
 box.innerHTML=h;
 // handlers ligados em JS: aspas dentro de atributos HTML ja' partiram
 // este ficheiro uma vez, e voltam a partir a proxima alteracao
 Array.prototype.forEach.call(box.querySelectorAll('.mxSel'), function(el){
  el.addEventListener('change', function(){
   mxAlternarSessao(el.getAttribute('data-id'), el.checked);
  });
 });
}

// Tabela dos blocos de trabalho de cada sessao seleccionada. Existe para o
// alinhamento ser verificavel: se duas sessoes foram emparelhadas pelo
// primeiro bloco mas os degraus nao correspondem, ve-se aqui e corrige-se
// no ajuste fino, em vez de se descobrir a olho no grafico.
function mxBlocosTabela(){
 const box=document.getElementById('mxBlocos');
 if(!box) return;
 const ids=Object.keys(MX_DADOS);
 if(!ids.length){ box.innerHTML=''; return; }
 const cols=[];
 ids.forEach(function(id,si){
  const d=MX_DADOS[id];
  const corte=mxCorteDe(id);
  const ons=(((d.blocos||{}).blocos)||[])
    .filter(b=>b.on && b.t1>=corte[0] && b.t0<=corte[1]);
  const s2=MX_SESSOES.find(x=>String(x.id)===id)||{};
  cols.push({id:id, si:si, data:s2.data||id, ons:ons,
             ref:mxRefAlinhamento(id)+(MX_OFF[id]||0)});
 });
 const maxN=Math.max.apply(null, cols.map(c=>c.ons.length));
 if(!maxN){ box.innerHTML='<p style="color:#8b949e;font-size:11px;">Sem blocos '
   +'de trabalho detectados no intervalo.</p>'; return; }
 let h='<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr style="color:#8b949e;text-align:left;"><th style="padding-right:14px;">Degrau</th>'
  +cols.map(function(c){ return '<th style="padding-right:14px;color:'
    +mxCorSessao(c.si)+';">'+(c.si+1)+'· '+c.data+'</th>'; }).join('')
  +'<th>Δ watts</th></tr>';
 for(let k=0;k<maxN;k++){
  const vals=cols.map(function(c){ return c.ons[k]; });
  const ws=vals.filter(v=>v&&v.watts_medio!=null).map(v=>v.watts_medio);
  const dif=ws.length>1 ? Math.round(Math.max.apply(null,ws)-Math.min.apply(null,ws)) : null;
  const cor = dif==null ? '#8b949e' : dif<15 ? '#3FB950' : dif<40 ? '#F0883E' : '#F85149';
  h+='<tr><td style="padding-right:14px;color:#8b949e;">'+(k+1)+'</td>'
   +vals.map(function(v,n){
     if(!v) return '<td style="padding-right:14px;color:#484f58;">—</td>';
     const rel=Math.round(v.t0-cols[n].ref);
     return '<td style="padding-right:14px;">'
      +(v.watts_medio!=null?Math.round(v.watts_medio)+' W':'—')
      +' <span style="color:#8b949e;">'+Math.round(v.duracao_s)+'s'
      +' @'+(rel>=0?'+':'')+rel+'s'
      +(v.tipo?' · '+v.tipo.toLowerCase():'')+'</span></td>';
    }).join('')
   +'<td style="color:'+cor+';">'+(dif!=null?dif+' W':'—')+'</td></tr>';
 }
 h+='</table>';
 if(ids.length>1) h+='<p style="color:#8b949e;font-size:11px;margin-top:4px;">'
  +'@ é o instante do degrau relativo ao ponto de alinhamento. Se os degraus '
  +'da mesma linha tiverem watts muito diferentes (Δ a vermelho), o '
  +'emparelhamento está errado — corrige no ajuste fino ou muda o critério '
  +'de alinhamento.</p>';
 box.innerHTML=h;
}

function mxEscolher(id){
 mxAlternarSessao(id, MX_SEL.indexOf(String(id))<0);
}

mxSessoes();
window.addEventListener('resize', function(){ mxDraw(); });
"""


def render():
    from flask import render_template_string
    return render_template_string(page('Moxy', SLUG, BODY, JS))
