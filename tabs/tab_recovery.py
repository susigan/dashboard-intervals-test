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

<h2 style="font-size:15px;margin-top:18px;">Os modelos, um a um</h2>
<div id="rcModelos"></div>

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
  rcSintese(); rcPersistencia(); rcModelos(); rcCobertura();
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

// ── a resposta do dia ────────────────────────────────────────────────
function rcSintese(){
 const box=document.getElementById('rcSintese');
 const s=(RC||{}).sintese||{};
 if(!s.ok){ box.innerHTML='<p class="sub">'+(s.motivo||'')+'</p>'; return; }
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
  +'<th>Voto</th></tr>'
  +(s.detalhe||[]).map(function(d){
    const c = d.voto>0 ? '#3FB950' : (d.voto<0 ? '#F85149' : '#8b949e');
    return '<tr style="border-bottom:1px solid #161b22;">'
     +'<td style="padding:5px;">'+d.modelo+'</td>'
     +'<td class="sub">'+(nomes[d.familia]||d.familia)+'</td>'
     +'<td><b>'+d.estado+'</b></td>'
     +'<td style="color:'+c+';">'+(d.voto>0?'+':'')+d.voto+'</td></tr>';
   }).join('')
  +'</table>';
 box.innerHTML=h;
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

// ── um dropdown por modelo ───────────────────────────────────────────
function rcModelos(){
 const box=document.getElementById('rcModelos');
 let h='';

 h+=rcBloco('SWC — Altini / Plews', RC.swc, function(m){
  const e=m.estado||[];
  return '<p class="sub">'+m.metodo+'</p>'
   +'<p>Hoje: <b>'+(e[e.length-1]||'—')+'</b> da banda '
   +'(média₂₈ ± '+m.k_swc+'·SD)</p>'
   +rcSerie(['LnRMSSD 7d', m.ln7], ['limite superior', m.swc_sup],
            ['limite inferior', m.swc_inf]);
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
   +'<p class="sub" style="font-size:11px;margin-top:6px;">'+m.nota+'</p>';
 });

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
