"""tab_training.py — aba de decisão de treinamento.

Responsabilidade: "Dado tudo que já foi encontrado, quais estímulos de
treinamento são fisiologicamente plausíveis?"

Fluxo automático ao abrir a aba:
  /api/training/contexto          ← prioridade P1/P2/P3 (backend)
        ↓
  limitador_chave + modalidade
        ↓
  uma secção por modalidade
        ↓
  /api/training/executar × 4      ← Bike / Row / Ski / Run
        ↓
  cards organizados por relevance (principal → secundário → complementar)

training.py permanece engine puro — não acessa DB nem API.
Esta aba é a camada de apresentação da saída do engine.
"""

import os, sys
from tabs.base import page

SLUG = 'training'

_TABELA_PATH_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    'Tabela_Mestre_Training_Engine_V5__1_.xlsx'
)

BODY = """
<div>
  <h1>Training</h1>
  <p class="sub">Opções de treinamento fisiologicamente plausíveis com base nos achados existentes.</p>

  <!-- CABEÇALHO DE ANÁLISE FISIOLÓGICA ATUAL -->
  <div id="trCabecalho" style="margin-bottom:20px;"></div>

  <!-- SECÇÕES POR MODALIDADE (preenchidas automaticamente) -->
  <div id="trRecomendacoes"></div>

  <!-- SEPARADOR -->
  <hr style="border-color:#30363d;margin:28px 0 20px;">

  <!-- OVERRIDE MANUAL (avançado, colapsado) -->
  <details id="trOverride">
    <summary style="cursor:pointer;font-size:12px;color:#8b949e;padding:4px 0;user-select:none;">
      ▼ Modo manual — selecionar sessão específica
    </summary>
    <div style="margin-top:14px;">
      <div class="controls" style="margin-bottom:12px;display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;">
        <label class="sel">Modalidade:
          <select id="trModalidade">
            <option value="">— escolher —</option>
            <option value="Bike">Bike</option>
            <option value="Row">Row</option>
            <option value="Ski">Ski</option>
            <option value="Run">Run</option>
          </select>
        </label>
        <label class="sel">Sessão MOXY (Day 1):
          <select id="trMoxyId" style="min-width:220px;">
            <option value="">— escolher —</option>
          </select>
        </label>
        <label class="sel">Verificação VST (Day 2):
          <select id="trVstId" style="min-width:220px;">
            <option value="">— nenhuma —</option>
          </select>
        </label>
        <button onclick="trExecutarManual()"
          style="padding:7px 16px;background:#1c2331;border:1px solid #5DADE2;
          color:#5DADE2;border-radius:6px;cursor:pointer;font-size:13px;">
          ↻ Executar engine
        </button>
      </div>
      <div id="trResultadoManual"></div>
    </div>
  </details>

  <!-- BIBLIOTECA (filtros) -->
  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:12px;color:#8b949e;padding:4px 0;user-select:none;">
      ▼ Biblioteca de treinos — filtros
    </summary>
    <div style="margin-top:14px;">
      <div class="controls" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px;">
        <label class="sel">Modalidade:
          <select id="filtMod" onchange="trFiltrar()">
            <option value="">Todas</option>
            <option value="Bike">Bike</option>
            <option value="Row">Row</option>
            <option value="Ski">Ski</option>
            <option value="Run">Run</option>
          </select>
        </label>
        <label class="sel">Zona:
          <select id="filtZona" onchange="trFiltrar()">
            <option value="">Todas</option>
            <option value="Z1">Z1</option>
            <option value="Z2">Z2</option>
            <option value="Z3">Z3</option>
          </select>
        </label>
        <label class="sel">Limitador:
          <select id="filtLim" onchange="trFiltrar()">
            <option value="">Todos</option>
            <option value="entrega">Cardíaco / Entrega</option>
            <option value="utilizacao">Periférico / Utilização</option>
            <option value="respiratorio">Respiratório</option>
          </select>
        </label>
        <label class="sel">Relevância:
          <select id="filtRel" onchange="trFiltrar()">
            <option value="">Todas</option>
            <option value="principal">Principal</option>
            <option value="possível">Possível</option>
          </select>
        </label>
      </div>
      <div id="trBiblioteca" style="font-size:12px;color:#8b949e;">
        Execute o engine para ver a biblioteca.
      </div>
    </div>
  </details>
</div>
"""

JS = r"""
// ── Estado global ─────────────────────────────────────────────────────────
let TR_CONTEXTO    = null;   // resultado de /api/training/contexto
let TR_RESULTADOS  = {};     // {modalidade: resultado_do_engine}
let TR_ULT_RESULTADO = null; // último resultado manual (compatibilidade)

const TR_MODALIDADES = ['Bike', 'Row', 'Ski', 'Run'];

const TR_COR_CHAVE = {
  entrega:'#5DADE2', utilizacao:'#3FB950',
  respiratorio:'#F4D03F', fadiga:'#E67E22', mecanico:'#A371F7'
};
const TR_COR_RELEVANCE = {
  principal:'#3FB950', 'possível':'#F4D03F', limiar:'#E67E22'
};
const TR_PRIORIDADE_ORDEM = {principal:0, 'possível':1, limiar:2};

// ── Inicialização automática ──────────────────────────────────────────────
(function(){
  trCarregarContexto();
  trCarregarSessoesMoxy();
  trCarregarVst();
})();

// ── P1/P2/P3: carregar contexto do backend ────────────────────────────────
function trCarregarContexto(){
  const cab = document.getElementById('trCabecalho');
  const rec = document.getElementById('trRecomendacoes');
  if(cab) cab.innerHTML = '<div class="loading" style="font-size:12px;color:#8b949e;">a determinar limitador actual…</div>';

  fetch('/api/training/contexto').then(r=>r.json()).then(function(ctx){
    TR_CONTEXTO = ctx;
    trRenderCabecalho(ctx);

    if(ctx.fonte === 'ausente' || !ctx.limitador_chave){
      if(rec) rec.innerHTML = trCardErro('dados_insuficientes',
        'Não foi encontrado um limitador fisiológico actual.<br>'
        +'Execute uma análise MOXY ou sincronize um conjunto VST para obter recomendações automáticas.<br>'
        +'Pode usar a biblioteca de treinos abaixo com os filtros disponíveis.');
      return;
    }

    // Executar engine para cada modalidade
    if(rec) rec.innerHTML = '<div class="loading" style="font-size:12px;color:#8b949e;">a calcular recomendações…</div>';
    trExecutarTodasModalidades(ctx);
  }).catch(function(e){
    if(cab) cab.innerHTML = trCardErro('erro', 'Erro ao obter contexto: '+e.message);
  });
}

// ── Cabeçalho de análise fisiológica atual ────────────────────────────────
function trRenderCabecalho(ctx){
  const cab = document.getElementById('trCabecalho');
  if(!cab) return;

  if(ctx.fonte === 'ausente' || !ctx.limitador_chave){
    cab.innerHTML = '<div style="border:1px solid #30363d;border-radius:8px;padding:12px 16px;'
      +'background:#0d1117;">'
      +'<div style="font-size:9px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px;">Análise Fisiológica</div>'
      +'<div style="font-size:13px;color:#8b949e;">Sem análise fisiológica recente disponível.</div>'
      +'</div>';
    return;
  }

  const cor = TR_COR_CHAVE[ctx.limitador_chave] || '#8b949e';
  const fonteLabel = ctx.fonte==='vst'
    ? 'MOXY + VST Verificação'
    : ctx.fonte==='moxy'
    ? 'MOXY (sem VST sincronizado)'
    : '—';

  let linhas = '';
  if(ctx.moxy_data){
    const dm = ctx.dias_moxy != null ? ' (há '+ctx.dias_moxy+' dia'+(ctx.dias_moxy!==1?'s':'')+')'  : '';
    linhas += '<div style="font-size:11px;color:#8b949e;">MOXY: '
      +(ctx.moxy_data||'').slice(0,10)+dm
      +(ctx.moxy_id?' · <span style="color:#484f58;">'+ctx.moxy_id+'</span>':'')+'</div>';
  }
  if(ctx.vst_id){
    const dv = ctx.dias_vst != null ? ' (há '+ctx.dias_vst+' dia'+(ctx.dias_vst!==1?'s':'')+')'  : '';
    linhas += '<div style="font-size:11px;color:#8b949e;">VST: '
      +(ctx.vst_data||'').slice(0,10)+dv
      +(ctx.vst_id?' · <span style="color:#484f58;">'+ctx.vst_id+'</span>':'')+'</div>';
  } else if(ctx.fonte==='moxy'){
    linhas += '<div style="font-size:11px;color:#6e7681;">VST: não sincronizado</div>';
  }

  const bp = (ctx.bp1_w||ctx.bp2_w)
    ? '<span style="font-size:11px;color:#8b949e;margin-left:12px;">'
      +(ctx.bp1_w?'BP1 '+Math.round(ctx.bp1_w)+'W':'')
      +(ctx.bp1_w&&ctx.bp2_w?' · ':'')
      +(ctx.bp2_w?'BP2 '+Math.round(ctx.bp2_w)+'W':'')
      +'</span>'
    : '';

  cab.innerHTML = '<div style="border:1px solid '+cor+'44;border-radius:8px;padding:12px 16px;background:#0d1117;">'
    +'<div style="font-size:9px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;">Análise Fisiológica Actual</div>'
    +'<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;">'
    +'<div style="font-size:20px;font-weight:700;color:'+cor+';">'
    +(ctx.limitador_nome||ctx.sistema||'—').toUpperCase()
    +(ctx.limitador_chave?' / '+ctx.limitador_chave.toUpperCase():'')
    +'</div>'+bp+'</div>'
    +'<div style="font-size:11px;color:#8b949e;margin-top:4px;">Fonte: '+fonteLabel+'</div>'
    +linhas
    +'</div>';
}

// ── Executar engine para todas as modalidades automaticamente ─────────────
function trExecutarTodasModalidades(ctx){
  const rec = document.getElementById('trRecomendacoes');
  TR_RESULTADOS = {};
  let pendentes = TR_MODALIDADES.length;
  const resultadosPorMod = {};

  TR_MODALIDADES.forEach(function(mod){
    const contexto = trMontarContextoDeCtx(ctx, mod);
    fetch('/api/training/executar',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(contexto),
    }).then(r=>r.json()).then(function(res){
      resultadosPorMod[mod] = {resultado: res, contexto: contexto};
      TR_RESULTADOS[mod] = res;
    }).catch(function(e){
      resultadosPorMod[mod] = {resultado: {status:'erro', motivo: e.message}, contexto: contexto};
    }).finally(function(){
      pendentes--;
      if(pendentes === 0){
        // Todos terminaram — renderizar por modalidade
        let h = '';
        TR_MODALIDADES.forEach(function(m){
          h += trRenderSecaoModalidade(m, resultadosPorMod[m], ctx);
        });
        if(rec) rec.innerHTML = h;
        // Popular biblioteca
        trPopularBiblioteca(resultadosPorMod);
      }
    });
  });
}

// ── Montar contexto a partir do resultado de /api/training/contexto ───────
function trMontarContextoDeCtx(ctx, modalidade){
  // Usa o limitador_chave já resolvido pelo backend (P1/P2/P3)
  // sem depender de MX_ULT_US nem de variáveis globais do browser
  const achadoRede = ctx.limitador_chave ? {
    sistema:    ctx.sistema,
    rotulo:     ctx.limitador_nome||null,
    pct:        null,
    disponivel: true,
  } : {disponivel: false};

  return {
    modalidade: modalidade,
    vst_activity_id:  ctx.vst_id||null,
    moxy_activity_id: ctx.moxy_id||null,
    bp1_w: ctx.bp1_w||null,
    bp2_w: ctx.bp2_w||null,
    cp_w:  null,
    achados: {
      rede_causal:  achadoRede,
      intervencoes: {disponivel: false},
      vst:          {disponivel: !!(ctx.vst_id)},
      moxy:         {disponivel: false},
    },
    historico: [],
    pace_individual: null,
  };
}

// ── Secção de recomendações por modalidade ────────────────────────────────
function trRenderSecaoModalidade(mod, entrada, ctx){
  const res = entrada ? entrada.resultado : null;
  const ops = (res&&res.opcoes)||[];

  // Ordenar por prioridade: principal primeiro
  const ordenadas = ops.slice().sort(function(a,b){
    return (TR_PRIORIDADE_ORDEM[a.relevance]||99) - (TR_PRIORIDADE_ORDEM[b.relevance]||99);
  });

  const corMod = {Bike:'#5DADE2',Row:'#3FB950',Ski:'#A371F7',Run:'#F4D03F'}[mod]||'#8b949e';

  let h = '<div style="margin-bottom:28px;">'
    +'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
    +'<span style="font-size:14px;font-weight:700;color:'+corMod+';">'+mod.toUpperCase()+'</span>'
    +(ctx&&ctx.bp1_w?'<span style="font-size:10px;color:#6e7681;">BP1 '+Math.round(ctx.bp1_w)
      +(ctx.bp2_w?' · BP2 '+Math.round(ctx.bp2_w):'')+'W</span>':'')
    +'</div>';

  if(!res || res.status==='erro'){
    h += '<div style="font-size:11px;color:#8b949e;padding:6px 0;">Erro ao calcular: '
      +((res&&res.motivo)||'desconhecido')+'</div>';
  } else if(res.status==='dados_insuficientes'){
    h += '<div style="font-size:11px;color:#8b949e;padding:6px 0;">Dados insuficientes — '
      +(res.motivo||'sem BP1/BP2 ou limitador')+'</div>';
  } else if(!ordenadas.length){
    h += '<div style="font-size:11px;color:#8b949e;padding:6px 0;">Sem treino recomendado para este limitador em '+mod+'.</div>';
  } else {
    // Cards em linha horizontal para compacidade
    h += '<div style="display:flex;flex-wrap:wrap;gap:10px;">';
    ordenadas.forEach(function(op){
      h += trCardCompacto(op, corMod);
    });
    h += '</div>';
  }
  h += '</div>';
  return h;
}

// ── Card compacto (visão de recomendação) ─────────────────────────────────
function trCardCompacto(op, corMod){
  const corRel = TR_COR_RELEVANCE[op.relevance] || '#8b949e';
  const corZona = {Z1:'#1E3A5F',Z2:'#1B5E20',Z3:'#4A1C12'}[op.zona]||'#161b22';
  const dose = op.dose||{};

  return '<div style="border:1px solid #30363d;border-radius:8px;width:220px;overflow:hidden;flex-shrink:0;">'
    +'<div style="background:'+corZona+'33;border-bottom:1px solid #30363d;padding:7px 10px;display:flex;justify-content:space-between;align-items:center;">'
    +'<span style="font-size:10px;background:'+corZona+'55;color:#c9d1d9;border-radius:3px;padding:1px 6px;">'+op.zona+'</span>'
    +'<span style="font-size:10px;color:'+corRel+';font-weight:600;">'+op.relevance.toUpperCase()+'</span>'
    +'</div>'
    +'<div style="padding:9px 10px;">'
    +'<div style="font-size:12px;font-weight:600;color:#c9d1d9;margin-bottom:3px;">'+op.training_type+'</div>'
    +'<div style="font-size:10px;color:#8b949e;margin-bottom:5px;">'+op.format+'</div>'
    +'<div style="font-size:10px;color:#8b949e;">Work: <b style="color:#c9d1d9;">'+dose.work_range+'</b></div>'
    +(dose.recovery_range&&dose.recovery_range!=='—'
      ?'<div style="font-size:10px;color:#8b949e;">Rec: '+dose.recovery_range+'</div>':'')
    +'<div style="font-size:10px;color:#8b949e;margin-top:3px;">RPE: '+op.expected_RPE_work+'</div>'
    +'<details style="margin-top:6px;">'
    +'<summary style="cursor:pointer;font-size:10px;color:#484f58;">▼ detalhe</summary>'
    +'<div style="font-size:10px;color:#8b949e;margin-top:4px;">'+op.adaptacao+'</div>'
    +'<div style="font-size:10px;color:#6e7681;">'+op.mecanismo_alvo+'</div>'
    +(op.success_rule?'<div style="font-size:10px;color:#3FB950;margin-top:3px;">✓ '+op.success_rule+'</div>':'')
    +'</details>'
    +'</div></div>';
}

// ── Popular biblioteca com todos os resultados ────────────────────────────
function trPopularBiblioteca(resultadosPorMod){
  // Colecionar todas as opções de todas as modalidades
  window._TR_TODAS_OPCOES = [];
  TR_MODALIDADES.forEach(function(mod){
    const entry = resultadosPorMod[mod];
    if(!entry) return;
    const ops = (entry.resultado.opcoes||[]);
    ops.forEach(function(op){ window._TR_TODAS_OPCOES.push({...op, _modalidade: mod}); });
  });
  trFiltrar();
}

// ── Filtros da biblioteca ─────────────────────────────────────────────────
function trFiltrar(){
  const todas = window._TR_TODAS_OPCOES||[];
  const mod  = (document.getElementById('filtMod')||{}).value||'';
  const zona = (document.getElementById('filtZona')||{}).value||'';
  const lim  = (document.getElementById('filtLim')||{}).value||'';
  const rel  = (document.getElementById('filtRel')||{}).value||'';

  const filtradas = todas.filter(function(op){
    if(mod  && op._modalidade !== mod)  return false;
    if(zona && op.zona !== zona)         return false;
    if(lim  && op.limitador !== lim && !op.limitador_chave?.includes(lim)) return false;
    if(rel  && op.relevance !== rel)     return false;
    return true;
  });

  const bib = document.getElementById('trBiblioteca');
  if(!bib) return;
  if(!filtradas.length){
    bib.innerHTML = '<div style="color:#8b949e;font-size:12px;">Nenhum treino encontrado com esses filtros.</div>';
    return;
  }
  // Agrupar por modalidade
  const porMod = {};
  filtradas.forEach(function(op){ (porMod[op._modalidade]=porMod[op._modalidade]||[]).push(op); });
  let h = '';
  Object.entries(porMod).forEach(function([m, ops]){
    const corMod = {Bike:'#5DADE2',Row:'#3FB950',Ski:'#A371F7',Run:'#F4D03F'}[m]||'#8b949e';
    h += '<div style="margin-bottom:16px;">'
      +'<div style="font-size:11px;font-weight:600;color:'+corMod+';margin-bottom:8px;">'+m.toUpperCase()
      +' ('+ops.length+')</div>'
      +'<div style="display:flex;flex-wrap:wrap;gap:8px;">';
    ops.slice().sort(function(a,b){
      return (TR_PRIORIDADE_ORDEM[a.relevance]||99)-(TR_PRIORIDADE_ORDEM[b.relevance]||99);
    }).forEach(function(op){ h+=trCardCompacto(op, corMod); });
    h += '</div></div>';
  });
  bib.innerHTML = h;
}

// ── Modo manual (override) ────────────────────────────────────────────────
function trCarregarSessoesMoxy(){
  fetch('/api/moxy/analises').then(r=>r.json()).then(function(d){
    const sel = document.getElementById('trMoxyId');
    if(!sel||d.status!=='ok') return;
    (d.analises||[]).forEach(function(a){
      const op = document.createElement('option');
      op.value = a.activity_id;
      op.textContent = (a.data||'').slice(0,10)+' · '+(a.modalidade||'?')
        +(a.bp1_w?' · BP1 '+Math.round(a.bp1_w)+'W':'')
        +(a.bp2_w?' · BP2 '+Math.round(a.bp2_w)+'W':'');
      sel.appendChild(op);
    });
  }).catch(function(){});
}

function trCarregarVst(){
  fetch('/api/moxy/vst/conjuntos_salvos').then(r=>r.json()).then(function(d){
    const sel = document.getElementById('trVstId');
    if(!sel||d.status!=='ok') return;
    (d.conjuntos||[]).forEach(function(c){
      const op = document.createElement('option');
      op.value = c.vst_activity_id;
      const ts = (c.analisado_em||'').slice(0,10);
      op.textContent = ts+' · '+(c.modalidade||'?')
        +' · VST '+c.vst_activity_id.slice(-6)
        +(c.bp1_status?' · '+c.bp1_status:'');
      sel.appendChild(op);
    });
  }).catch(function(){});
}

function trExecutarManual(){
  const modalidade = (document.getElementById('trModalidade')||{}).value||'';
  const moxyId     = (document.getElementById('trMoxyId')||{}).value||'';
  const vstId      = (document.getElementById('trVstId')||{}).value||'';
  const out = document.getElementById('trResultadoManual');

  if(!modalidade){
    if(out) out.innerHTML = trCardErro('dados_insuficientes','Seleccione uma modalidade.');
    return;
  }
  if(out) out.innerHTML = '<div class="loading">a calcular…</div>';

  Promise.all([
    moxyId ? fetch('/api/moxy/limiares/'+moxyId).then(r=>r.json()).catch(()=>null) : Promise.resolve(null),
    moxyId ? fetch('/api/moxy/rede/'+moxyId).then(r=>r.json()).catch(()=>null)     : Promise.resolve(null),
    vstId  ? fetch('/api/moxy/vst/resultado/'+vstId).then(r=>r.json()).catch(()=>null) : Promise.resolve(null),
    fetch('/api/cp/actual/'+encodeURIComponent(modalidade)).then(r=>r.json()).catch(()=>null),
  ]).then(function(res){
    const limiares = res[0], rede = res[1], vstResult = res[2], cp = res[3];
    const lc   = (limiares&&limiares.limiares_consenso)||{};
    const redeLim = (rede&&rede.limitador)||{};
    const contexto = {
      modalidade, vst_activity_id: vstId||null, moxy_activity_id: moxyId||null,
      bp1_w: (lc.primeiro||{}).mediana||null,
      bp2_w: (lc.segundo||{}).mediana||null,
      cp_w:  cp&&cp.cp_w||null,
      achados:{
        rede_causal:{sistema:redeLim.sistema||null,rotulo:redeLim.rotulo||null,
          pct:redeLim.pct||null,disponivel:!!(rede&&rede.status==='ok'&&redeLim.sistema)},
        intervencoes:{disponivel:false},
        vst:{disponivel:!!(vstResult&&vstResult.status==='ok')},
        moxy:{disponivel:false},
      },
      historico:[],
    };
    return fetch('/api/training/executar',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(contexto)});
  }).then(r=>r.json()).then(function(resultado){
    TR_ULT_RESULTADO = resultado;
    if(out) out.innerHTML = trRenderResultadoCompleto(resultado);
  }).catch(function(e){
    if(out) out.innerHTML = trCardErro('erro','Erro: '+e.message);
  });
}

// ── Renderizar resultado completo (modo manual) ───────────────────────────
function trRenderResultadoCompleto(r){
  if(r.status==='dados_insuficientes')
    return trCardErro('dados_insuficientes','Dados insuficientes: '+(r.motivo||''));
  if(r.status==='sem_opcao_plausivel')
    return '<div style="border:1px solid #F4D03F;border-radius:8px;padding:12px;margin:12px 0;">'
      +'<b style="color:#F4D03F;">Sem opção plausível</b>'
      +'<p style="font-size:12px;color:#8b949e;">'+(r.motivo_sem_opcao||'')+'</p></div>';

  let h = '<h3 style="margin:16px 0 10px;">Opções de treinamento</h3>';
  (r.opcoes||[]).forEach(function(op){ h+=trRenderOpcao(op); });
  if((r.excluidas||[]).length)
    h += '<details style="margin:8px 0;"><summary style="cursor:pointer;font-size:11px;color:#8b949e;">▼ Regras excluídas ('+r.excluidas.length+')</summary>'
      +trDetalhesExcluidas(r.excluidas)+'</details>';
  return h;
}

// ── Render de uma opção completa (modo manual) ────────────────────────────
function trRenderOpcao(op){
  const corZona = {Z1:'#1E3A5F',Z2:'#1B5E20',Z3:'#4A1C12'}[op.zona]||'#161b22';
  const corRel  = TR_COR_RELEVANCE[op.relevance]||'#8b949e';
  const dose = op.dose||{};
  const refs = op.referencias_individuais||{};
  const prog = op.progressao||{};

  let h = '<div style="border:1px solid #30363d;border-radius:8px;margin-bottom:14px;overflow:hidden;">'
    +'<div style="background:'+corZona+'22;border-bottom:1px solid #30363d;padding:10px 14px;'
    +'display:flex;align-items:center;gap:10px;flex-wrap:wrap;">'
    +'<span style="font-size:11px;background:'+corZona+'55;color:#c9d1d9;border-radius:4px;padding:2px 8px;">'+op.zona+'</span>'
    +'<b style="font-size:14px;">'+op.training_type+' — '+op.format+'</b>'
    +'<span style="margin-left:auto;font-size:11px;color:'+corRel+';">'+op.relevance+'</span>'
    +'<span style="font-size:10px;color:#6e7681;">'+op.rule_id+'</span>'
    +'</div>'
    +'<div style="padding:12px 14px;">';

  h += '<div style="font-size:12px;color:#8b949e;margin-bottom:8px;">'
    +'<b style="color:#c9d1d9;">Adaptação:</b> '+op.adaptacao+'<br>'
    +'<b style="color:#c9d1d9;">Mecanismo:</b> '+op.mecanismo_alvo+'</div>';

  h += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:10px;">';

  h += '<div style="background:#0d1117;border-radius:6px;padding:10px;">'
    +'<div style="font-size:9px;color:#8b949e;text-transform:uppercase;margin-bottom:5px;">Dose</div>'
    +'<div style="font-size:12px;"><b>'+dose.work_range+'</b></div>'
    +(dose.recovery_range&&dose.recovery_range!=='—'?'<div style="font-size:11px;color:#8b949e;">Recovery: '+dose.recovery_range+'</div>':'')
    +'<div style="font-size:11px;color:#8b949e;margin-top:4px;">RPE esperado: '+op.expected_RPE_work+'</div>'
    +(dose.ponto_de_partida?'<div style="font-size:10px;color:#6e7681;margin-top:4px;">Ponto de partida: '+dose.ponto_de_partida+'</div>':'')
    +'</div>';

  const refsDisp = Object.entries(refs).filter(([k,v])=>v&&v.disponivel&&k!=='RPE_faixa_tabela');
  h += '<div style="background:#0d1117;border-radius:6px;padding:10px;">'
    +'<div style="font-size:9px;color:#8b949e;text-transform:uppercase;margin-bottom:5px;">Referências individuais</div>'
    +(refsDisp.length
      ? refsDisp.map(function([k,v]){
          let val=v.min!=null&&v.max!=null?v.min+'–'+v.max:(v.teto!=null?'≤'+v.teto:(v.trend||''));
          return '<div style="font-size:11px;"><span style="color:#8b949e;">'+k+':</span> <b>'+val+'</b></div>';
        }).join('')
      : '<div style="font-size:11px;color:#8b949e;">Indisponíveis</div>')
    +'</div>';

  h += '<div style="background:#0d1117;border-radius:6px;padding:10px;">'
    +'<div style="font-size:9px;color:#8b949e;text-transform:uppercase;margin-bottom:5px;">Monitorar</div>'
    +'<div style="font-size:11px;"><b>'+op.monitoramento.primary+'</b></div>'
    +(op.monitoramento.rules||[]).filter(Boolean).map(r=>'<div style="font-size:10px;color:#8b949e;margin-top:3px;">'+r+'</div>').join('')
    +'</div>';

  h += '</div>';

  h += '<details style="margin-top:6px;"><summary style="cursor:pointer;font-size:11px;color:#8b949e;">▼ Critérios de sucesso, falha e interrupção</summary>'
    +'<div style="font-size:11px;padding:8px 0;color:#c9d1d9;">'
    +(op.success_rule?'<div style="margin-bottom:6px;"><b style="color:#3FB950;">✓ Sucesso:</b> '+op.success_rule+'</div>':'')
    +(op.failure_rule?'<div style="margin-bottom:6px;"><b style="color:#F4D03F;">⚠ Falha:</b> '+op.failure_rule+'</div>':'')
    +(op.work_stop_rule?'<div style="margin-bottom:6px;"><b style="color:#E74C3C;">✗ Interrupção:</b> '+op.work_stop_rule+'</div>':'')
    +(op.recovery_gate?'<div><b style="color:#5DADE2;">⟳ Recovery gate:</b> '+op.recovery_gate+'</div>':'')
    +'</div></details>';

  h += '<details style="margin-top:4px;"><summary style="cursor:pointer;font-size:11px;color:#8b949e;">▼ Por que esta opção aparece</summary>'
    +'<div style="font-size:11px;padding:8px 0;color:#8b949e;">'
    +'<div><b style="color:#c9d1d9;">Limitador:</b> '+op.limitador+'</div>'
    +'<div><b style="color:#c9d1d9;">Adaptação:</b> '+op.adaptacao+'</div>'
    +'<div><b style="color:#c9d1d9;">Mecanismo-alvo:</b> '+op.mecanismo_alvo+'</div>'
    +'<div><b style="color:#c9d1d9;">Regra da tabela:</b> '+op.rule_id+' (evidência '+op.evidence_level+')</div>'
    +'</div></details>';

  h += '</div></div>';
  return h;
}

function trDetalhesExcluidas(excluidas){
  if(!excluidas.length) return '';
  return '<div style="font-size:11px;padding:8px;">'
    +excluidas.map(e=>'<div style="color:#8b949e;margin:2px 0;">'
      +'<b style="color:#484f58;">'+e.rule_id+'</b> — '+e.motivo+'</div>').join('')
    +'</div>';
}

function trCardErro(tipo, msg){
  const cor = tipo==='dados_insuficientes'?'#E67E22':'#E74C3C';
  return '<div style="border:1px solid '+cor+';border-radius:8px;padding:12px 16px;margin:12px 0;">'
    +'<b style="color:'+cor+';">'+(tipo==='dados_insuficientes'?'Dados insuficientes':'Erro')+'</b>'
    +'<p style="font-size:12px;color:#8b949e;margin:6px 0 0;">'+msg+'</p></div>';
}
"""


def render():
    from flask import render_template_string
    return render_template_string(page('Training', SLUG, BODY, JS))
