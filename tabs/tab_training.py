"""tab_training.py — aba de opções de treino classificadas por limitador.

Arquitectura:
  DADOS MOXY/VST → CONTEXTO por modalidade → training_master.db
  → FILTROS do utilizador → RANKING → PRINCIPAL/SUPLEMENTAR → CARDS

Não prescreve treino, não avalia execução, não força escolha.
O utilizador escolhe a opção que quer usar.
"""
from tabs.base import page

SLUG = 'training'

BODY = r"""
<div>
  <h1>Training</h1>
  <p class="sub">Opções de treino organizadas pelo limitador fisiológico actual.
    Não é prescrição — é um mapa de possibilidades. Você escolhe.</p>

  <!-- FILTROS NO TOPO -->
  <div id="trFiltros" style="display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 18px;align-items:flex-end;">
    <label class="sel">Modalidade
      <select id="fMod" onchange="trAplicarFiltros()">
        <option value="">Todas</option>
        <option value="bike">Bike</option>
        <option value="row">Row</option>
        <option value="ski">Ski</option>
        <option value="run">Run</option>
      </select>
    </label>
    <label class="sel">Zona
      <select id="fZona" onchange="trAplicarFiltros()">
        <option value="">Todas</option>
        <option value="Z1">Z1</option>
        <option value="Z2">Z2</option>
        <option value="Z3">Z3</option>
      </select>
    </label>
    <label class="sel">Tipo
      <select id="fTipo" onchange="trAplicarFiltros()">
        <option value="">Todos</option>
      </select>
    </label>
    <label class="sel">Limitador
      <select id="fLim" onchange="trAplicarFiltros()">
        <option value="">Todos</option>
      </select>
    </label>
    <label class="sel">Work
      <select id="fWork" onchange="trAplicarFiltros()">
        <option value=""  >Qualquer</option>
        <option value="lt1">  &lt; 1 min</option>
        <option value="1_3"> 1–3 min</option>
        <option value="3_5"> 3–5 min</option>
        <option value="5_10">5–10 min</option>
        <option value="10_20">10–20 min</option>
        <option value="gt20">&gt; 20 min</option>
      </select>
    </label>
    <button onclick="trAplicarFiltros()"
      style="padding:6px 14px;background:#1c2331;border:1px solid #5DADE2;
      color:#5DADE2;border-radius:6px;cursor:pointer;font-size:12px;">
      ↻ Atualizar
    </button>
  </div>

  <!-- CONTEXTO FISIOLÓGICO (cabeçalho) -->
  <div id="trContexto" style="margin-bottom:20px;"></div>

  <!-- OPÇÕES POR MODALIDADE -->
  <div id="trOpcoes"></div>

  <!-- BIBLIOTECA (expansível) -->
  <details style="margin-top:14px;">
    <summary style="cursor:pointer;font-size:12px;color:#8b949e;padding:4px 0;user-select:none;">
      ▼ Biblioteca de treinos adicionados
    </summary>
    <div style="margin-top:12px;">
      <div id="trBiblioteca" style="font-size:12px;color:#8b949e;">
        (sem treinos adicionados ainda — use a seção abaixo para adicionar)
      </div>
    </div>
  </details>

  <!-- ADICIONAR TREINO -->
  <details style="margin-top:8px;">
    <summary style="cursor:pointer;font-size:12px;color:#8b949e;padding:4px 0;user-select:none;">
      ▼ Adicionar treino à biblioteca pessoal
    </summary>
    <div style="margin-top:14px;" id="trFormAdd">
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-bottom:10px;">
        <label class="sel">Nome *<input id="addNome" type="text" style="width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;padding:5px 8px;margin-top:3px;"></label>
        <label class="sel">Modalidade *
          <select id="addMod" style="width:100%;">
            <option value="">—</option>
            <option value="bike">Bike</option><option value="row">Row</option>
            <option value="ski">Ski</option><option value="run">Run</option>
          </select>
        </label>
        <label class="sel">Tipo
          <select id="addTipo" style="width:100%;"><option value="">—</option></select>
        </label>
        <label class="sel">Zona
          <select id="addZona" style="width:100%;">
            <option value="">—</option>
            <option value="Z1">Z1</option><option value="Z2">Z2</option><option value="Z3">Z3</option>
          </select>
        </label>
        <label class="sel">Work (descrição)<input id="addWork" type="text" placeholder="ex: 4×8min" style="width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;padding:5px 8px;margin-top:3px;"></label>
        <label class="sel">Recovery<input id="addRec" type="text" placeholder="ex: 3min" style="width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;padding:5px 8px;margin-top:3px;"></label>
        <label class="sel">RPE esperado<input id="addRpe" type="text" placeholder="ex: 7–8" style="width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;padding:5px 8px;margin-top:3px;"></label>
      </div>
      <button onclick="trSalvarBiblioteca()"
        style="padding:6px 16px;background:#1c2331;border:1px solid #3FB950;color:#3FB950;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600;">
        Salvar na biblioteca
      </button>
      <span id="trAddStatus" style="font-size:11px;color:#8b949e;margin-left:10px;"></span>
    </div>
  </details>
</div>
"""

JS = r"""
// ── Estado global ─────────────────────────────────────────────────────────
let TR_CONTEXTO    = null;   // {status:'ok', modalidades:{bike:{...}, ...}}
let TR_META        = null;   // {training_types, limiters, work_categories}
const TR_MODS = ['bike','row','ski','run'];
const TR_MOD_NOME = {bike:'Bike',row:'Row',ski:'Ski',run:'Run'};
const TR_MOD_COR  = {bike:'#5DADE2',row:'#3FB950',ski:'#A371F7',run:'#F4D03F'};
const TR_REL_COR  = {PRINCIPAL:'#3FB950',SUPLEMENTAR:'#8b949e',DISPONÍVEL:'#5DADE2'};
const TR_ZONA_BG  = {Z1:'#0d1b2a',Z2:'#0d1f10',Z3:'#2a0d0d'};

// ── Inicialização ─────────────────────────────────────────────────────────
(function(){
  Promise.all([
    fetch('/api/training/contexto').then(r=>r.json()),
    fetch('/api/training/meta').then(r=>r.json()),
  ]).then(function([ctx, meta]){
    TR_CONTEXTO = ctx;
    TR_META = meta;
    _popularFiltros(meta);
    trAplicarFiltros();
    trRenderContexto(ctx);
    trCarregarBiblioteca();
  }).catch(function(e){
    // Se contexto falhar, ainda tentar carregar os cards sem contexto fisiológico
    console.error('[Training] init falhou:', e);
    TR_CONTEXTO = {status:'ok', modalidades:{bike:{fonte:'ausente'},row:{fonte:'ausente'},ski:{fonte:'ausente'},run:{fonte:'ausente'}}};
    if(TR_META) _popularFiltros(TR_META);
    trAplicarFiltros();
    const warn=document.getElementById('trContexto');
    if(warn) warn.innerHTML='<div style="border:1px solid #F0883E;border-radius:6px;padding:8px 12px;font-size:11px;color:#F0883E;margin-bottom:8px;">⚠ Contexto fisiológico indisponível: '+e.message+'</div>';
  });
})();

// ── Popular filtros a partir dos metadados do DB ──────────────────────────
function _popularFiltros(meta){
  const sTipo = document.getElementById('fTipo');
  const sLim  = document.getElementById('fLim');
  const addT  = document.getElementById('addTipo');
  if(sTipo && meta && meta.training_types){
    meta.training_types.forEach(function(t){
      [sTipo, addT].forEach(function(sel){ if(!sel) return;
        const op=document.createElement('option');
        op.value=t.training_type_code; op.textContent=t.training_type_name;
        sel.appendChild(op);
      });
    });
  }
  if(sLim && meta && meta.limiters){
    meta.limiters.forEach(function(l){
      const op=document.createElement('option');
      op.value=l.limiter_code; op.textContent=l.limiter_name;
      sLim.appendChild(op);
    });
  }
}

// ── Cabeçalho de contexto fisiológico ────────────────────────────────────
function trRenderContexto(ctx){
  const box = document.getElementById('trContexto');
  if(!box || !ctx || ctx.status!=='ok') return;
  const mods = ctx.modalidades || {};

  let h = '<div style="border:1px solid #30363d;border-radius:8px;padding:10px 14px;'
    +'background:#161b22;margin-bottom:4px;">'
    +'<div style="font-size:9px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;'
    +'margin-bottom:8px;">Contexto Fisiológico Actual</div>'
    +'<div style="display:flex;flex-wrap:wrap;gap:14px;">';

  TR_MODS.forEach(function(m){
    const d = mods[m] || {};
    const cor = TR_MOD_COR[m]||'#8b949e';
    const lnome = d.limitador_nome||'—';
    const fonte = d.fonte==='vst'?'VST + MOXY':d.fonte==='moxy'?'MOXY':'Sem dados';
    const dias = d.dias_moxy!=null?' · há '+d.dias_moxy+'d':'';
    const bp = (d.bp1_w||d.bp2_w)
      ? '<div style="font-size:10px;color:#6e7681;">'
        +(d.bp1_w?'BP1 '+Math.round(d.bp1_w)+'W':'')
        +(d.bp1_w&&d.bp2_w?' · ':'')
        +(d.bp2_w?'BP2 '+Math.round(d.bp2_w)+'W':'')
        +'</div>' : '';
    h += '<div style="min-width:110px;">'
      +'<div style="font-size:10px;font-weight:700;color:'+cor+';margin-bottom:2px;">'
      +TR_MOD_NOME[m]+'</div>'
      +'<div style="font-size:13px;font-weight:700;color:'+(d.fonte==='ausente'?'#484f58':cor)+';margin-bottom:2px;">'
      +lnome+'</div>'
      +'<div style="font-size:10px;color:#6e7681;">'+fonte+dias+'</div>'
      +bp+'</div>';
  });

  h += '</div></div>';
  box.innerHTML = h;
}

// ── Aplicar filtros e re-renderizar todas as modalidades ──────────────────
function trAplicarFiltros(){
  const fMod  = document.getElementById('fMod').value;
  const fZona = document.getElementById('fZona').value;
  const fTipo = document.getElementById('fTipo').value;
  const fLim  = document.getElementById('fLim').value;
  const fWork = document.getElementById('fWork').value;

  const mods = fMod ? [fMod] : TR_MODS;
  const box = document.getElementById('trOpcoes');
  if(!box) return;
  box.innerHTML = '<div class="loading" style="font-size:12px;color:#8b949e;">a carregar opções…</div>';

  const ctx = (TR_CONTEXTO && TR_CONTEXTO.modalidades) || {};

  Promise.all(mods.map(function(m){
    const md = ctx[m] || {};
    // Limiter_code actual para marcar PRINCIPAL/SUPLEMENTAR
    const limCode = _limCodeFromCtx(md);
    const params = new URLSearchParams({modalidade: m});
    if(fZona) params.set('zona', fZona);
    if(fTipo) params.set('tipo', fTipo);
    if(fLim)  params.set('filtro_limitador', fLim);
    if(fWork) params.set('work', fWork);
    if(limCode) params.set('limitador', limCode);
    if(md.bp1_w)   params.set('bp1_w', md.bp1_w);
    if(md.bp2_w)   params.set('bp2_w', md.bp2_w);
    if(md.bp1_bpm) params.set('bp1_bpm', md.bp1_bpm);
    if(md.bp2_bpm) params.set('bp2_bpm', md.bp2_bpm);
    // Pontos observados VST Dia 2 — Trava 3: só vêm do contexto VST, nunca MOXY
    if(md.pontos_observados && md.pontos_observados.length){
      params.set('pontos_observados', JSON.stringify(md.pontos_observados));
    }
    params.set('n', '10');
    return fetch('/api/training/opcoes?'+params.toString())
      .then(r=>r.json())
      .then(function(d){ return {mod:m, data:d, ctx:md}; })
      .catch(function(e){ return {mod:m, data:{status:'erro',mensagem:'rede: '+(e&&e.message?e.message:String(e))}, ctx:md}; });
  })).then(function(resultados){
    let h = '';
    resultados.forEach(function(res){
      h += _renderSecaoMod(res.mod, res.data, res.ctx, fZona, fTipo, fLim, fWork);
    });
    box.innerHTML = h || '<div style="color:#8b949e;font-size:12px;">Sem opções encontradas.</div>';
  });
}

// Mapeia o contexto da modalidade → limiter_code do training_master
function _limCodeFromCtx(md){
  if(!md || md.fonte==='ausente') return null;
  // Usar sistema da Rede Causal → family → code
  const _MAP = {
    cardiaco:'CD','cardíaco':'CD',
    periferico:'UT','periférico':'UT',
    respiratorio:'RD','respiratório':'RD',
  };
  const s = (md.sistema||'').toLowerCase().trim();
  return _MAP[s] || null;
}

// ── Renderizar secção de uma modalidade ──────────────────────────────────
function _renderSecaoMod(mod, data, ctx, fZona, fTipo, fLim, fWork){
  const cor = TR_MOD_COR[mod]||'#8b949e';
  const nome = TR_MOD_NOME[mod]||mod;
  const lnome = ctx.limitador_nome||'—';
  const fonte = ctx.fonte==='vst'?'VST+MOXY':ctx.fonte==='moxy'?'MOXY':'Sem avaliação';

  let h = '<div style="margin-bottom:28px;">'
    +'<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:10px;">'
    +'<span style="font-size:16px;font-weight:700;color:'+cor+';">'+nome.toUpperCase()+'</span>'
    +'<span style="font-size:11px;color:#8b949e;">Limitador: <b style="color:'+cor+';">'+lnome+'</b>'
    +' · '+fonte+'</span>'
    +'</div>';

  if(data.status !== 'ok'){
    // Mostrar mensagem de erro com detalhe — não esconder com fallback genérico
    const errMsg = data.mensagem || '?';
    const isRede = errMsg.startsWith('rede:') || errMsg === 'rede';
    h += '<div style="font-size:11px;color:'+(isRede?'#F0883E':'#E74C3C')+';">'
      + (isRede ? '⚠ Não foi possível contactar /api/training/opcoes — verifique se o servidor está a correr.'
                : 'Erro ao carregar opções: ' + errMsg)
      + '</div>';
    h += '</div>'; return h;
  }

  const ops = data.opcoes || [];
  if(!ops.length){
    h += '<div style="font-size:12px;color:#8b949e;">Sem opções para os filtros seleccionados.</div>';
    h += '</div>'; return h;
  }

  // Com filtros activos: mostrar TODOS os resultados sem "Ver mais"
  // Sem filtros (todas zonas, todos tipos, etc.): mostrar 3 + "Ver mais"
  const temFiltrosActivos = !!(fMod || fZona || fTipo || fLim || fWork);

  if(temFiltrosActivos){
    // Filtro aplicado → todos os resultados visíveis, ordenados por ranking
    h += '<div style="display:flex;flex-wrap:wrap;gap:10px;">';
    ops.forEach(function(op){ h += _card(op, cor); });
    h += '</div>';
  } else {
    // Sem filtros → mostrar 3 primeiros (diversidade de zona) + Ver mais
    const visiveis = ops.slice(0,3);
    const resto    = ops.slice(3);
    h += '<div style="display:flex;flex-wrap:wrap;gap:10px;">';
    visiveis.forEach(function(op){ h += _card(op, cor); });
    h += '</div>';
    if(resto.length){
      h += '<details style="margin-top:8px;">'
        +'<summary style="cursor:pointer;font-size:11px;color:#484f58;">'
        +'▼ Ver mais opções ('+resto.length+')</summary>'
        +'<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:8px;">';
      resto.forEach(function(op){ h += _card(op, cor); });
      h += '</div></details>';
    }
  }

  h += '</div>';
  return h;
}

// ── Card de opção de treino ───────────────────────────────────────────────
function _card(op, corMod){
  const corRel  = TR_REL_COR[op.relacao]||'#8b949e';
  const bgZona  = TR_ZONA_BG[op.zone]||'#161b22';
  const relBg   = op.relacao==='PRINCIPAL'?'#1a3a1a':op.relacao==='SUPLEMENTAR'?'#1f1f1f':'#1a1a3a';

  // Limpar nomenclaturas internas dos campos visíveis
  const tipo = (op.training_type||'').replace(/^[A-Z]{1,2}-[A-Z]\d-\d+\s*/,'');
  const fmt  = (op.format||'');

  const workW  = op.work_watts || null;
  const workB  = op.work_bpm   || null;
  const fonte  = op.intensidade_fonte || 'generica';
  const nPts   = op.intensidade_n_pontos || 0;
  // Badge: observada (com N intervalos VST) ou estimativa genérica
  const badgeObs = (fonte === 'observada' || fonte === 'observada_ponto_unico')
    ? ('<div style="font-size:9px;font-weight:600;color:#2EA043;background:#0d2b12;'
       +'border:1px solid #2EA043;border-radius:3px;padding:1px 5px;margin-top:6px;display:inline-block;">'
       +'● Observada' + (nPts > 1 ? ' — ' + nPts + ' intervalos VST' : ' — 1 intervalo VST') + '</div>')
    : ('<div style="font-size:9px;color:#484f58;margin-top:6px;display:inline-block;">'
       +'○ Estimativa</div>');

  return '<div style="border:1px solid #30363d;border-radius:8px;width:230px;'
    +'overflow:hidden;flex-shrink:0;display:flex;flex-direction:column;">'
    // cabeçalho com zona e relação
    +'<div style="background:'+bgZona+';border-bottom:1px solid #30363d;'
    +'padding:7px 10px;display:flex;justify-content:space-between;align-items:center;">'
    +'<span style="font-size:10px;color:#8b949e;font-weight:600;">'+op.zone+'</span>'
    +'<span style="font-size:9px;font-weight:700;color:'+corRel+';background:'+relBg+';'
    +'padding:2px 7px;border-radius:4px;">'+op.relacao+'</span>'
    +'</div>'
    // corpo
    +'<div style="padding:10px 12px;flex:1;">'
    +'<div style="font-size:11px;color:#8b949e;margin-bottom:2px;">'+tipo+'</div>'
    +'<div style="font-size:13px;font-weight:600;color:#c9d1d9;margin-bottom:4px;">'+fmt+'</div>'
    +'<div style="font-size:11px;color:#8b949e;margin-bottom:6px;'
    +'border-left:2px solid '+corRel+';padding-left:6px;">'
    +'Favorece <b style="color:'+corRel+';">'+op.limiter_nome+'</b>'
    +'</div>'
    // métricas
    +'<div style="font-size:11px;display:grid;grid-template-columns:auto 1fr;gap:2px 8px;">'
    +'<span style="color:#6e7681;">Work</span><span>'+op.work_range+'</span>'
    +'<span style="color:#6e7681;">Recovery</span><span>'+(op.recovery_range&&op.recovery_range!=='—'?op.recovery_range:'—')+'</span>'
    +'<span style="color:#6e7681;">RPE</span><span>'+op.expected_rpe_work+'</span>'
    +(workW?'<span style="color:#5DADE2;">WORK W</span><span style="color:#5DADE2;font-weight:600;">'+workW+'</span>':'<span style="color:#6e7681;">WORK W</span><span style="color:#484f58;">—</span>')
    +(workB?'<span style="color:#E74C3C;">FC</span><span style="color:#E74C3C;">'+workB+'</span>':'<span style="color:#6e7681;">FC</span><span style="color:#484f58;">—</span>')
    +'</div>'
    // badge observada vs estimativa (Trava 4)
    + badgeObs
    // detalhes expansíveis — objectivo + mecanismo + notas de consolidação
    +((op.adaptation_target||op.mechanism_target||op.notes)
      ?'<details style="margin-top:8px;"><summary style="cursor:pointer;font-size:10px;color:#484f58;">▼ objectivo</summary>'
       +'<div style="font-size:10px;color:#8b949e;margin-top:4px;line-height:1.5;">'
       +(op.adaptation_target?'<b>Adaptação:</b> '+op.adaptation_target+'<br>':'')
       +(op.mechanism_target?'<b>Mecanismo:</b> '+op.mechanism_target+'<br>':'')
       +(op.notes?'<div style="font-size:10px;color:#484f58;margin-top:2px;">'+op.notes+'</div>':'')
       +'</div></details>':'')
    +'</div>'
    +'</div>';
}

// ── Biblioteca pessoal ────────────────────────────────────────────────────
function trCarregarBiblioteca(){
  fetch('/api/training/biblioteca').then(r=>r.json()).then(function(d){
    const box = document.getElementById('trBiblioteca');
    if(!box) return;
    const ps = (d.protocolos||[]).filter(p=>p.ativo);
    if(!ps.length){ box.innerHTML='<div style="font-size:12px;color:#8b949e;">(sem treinos adicionados)</div>'; return; }
    box.innerHTML = '<div style="display:flex;flex-wrap:wrap;gap:8px;">'
      +ps.map(function(p){
        const cor = TR_MOD_COR[p.modalidade.toLowerCase()]||'#8b949e';
        return '<div style="border:1px solid #30363d;border-radius:6px;padding:8px 10px;width:200px;">'
          +'<div style="font-size:10px;color:'+cor+';font-weight:600;">'+p.modalidade.toUpperCase()+'</div>'
          +'<div style="font-size:12px;font-weight:600;color:#c9d1d9;margin:3px 0;">'+p.nome+'</div>'
          +(p.tipo_treino?'<div style="font-size:10px;color:#8b949e;">'+p.tipo_treino+'</div>':'')
          +(p.work_seconds?'<div style="font-size:10px;color:#8b949e;">Work: '+Math.round(p.work_seconds/60)+'min</div>':'')
          +'</div>';
      }).join('')+'</div>';
  }).catch(function(){});
}

function trSalvarBiblioteca(){
  const nome=((document.getElementById('addNome')||{}).value||'').trim();
  const mod=((document.getElementById('addMod')||{}).value||'').trim();
  const st=document.getElementById('trAddStatus');
  if(!nome||!mod){ if(st) st.textContent='Nome e modalidade obrigatórios.'; return; }
  const payload={
    nome, modalidade: mod.charAt(0).toUpperCase()+mod.slice(1),
    tipo_treino:((document.getElementById('addTipo')||{}).value||null),
    zona:((document.getElementById('addZona')||{}).value||null),
    descricao:((document.getElementById('addWork')||{}).value||null),
    instrucoes:((document.getElementById('addRec')||{}).value||null),
    alvo_rpe_min:null, alvo_rpe_max:null,
    prioridade:'possível',
  };
  if(st) st.textContent='a salvar…';
  fetch('/api/training/biblioteca',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(r=>r.json()).then(function(d){
      if(d.status==='ok'){if(st) st.textContent='✓ Salvo.'; trCarregarBiblioteca();}
      else{if(st) st.textContent='Erro: '+(d.mensagem||'?');}
    }).catch(function(e){ if(st) st.textContent='Erro: '+e.message; });
}
"""


def render():
    from flask import render_template_string
    return render_template_string(page('Training', SLUG, BODY, JS))
