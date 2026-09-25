"""tab_training.py — aba de decisão de treinamento.

Responsabilidade: "Dado tudo que já foi encontrado, quais estímulos de
treinamento são fisiologicamente plausíveis?"

Fluxo:
  MOXY / VST / Intervenções / Rede Causal / Histórico
    ↓
  contexto montado pelo frontend via fetch aos endpoints existentes
    ↓
  /api/training/executar  (chama utils/training.executar)
    ↓
  resultado renderizado na UI

training.py permanece engine puro — não acessa DB nem API.
Esta aba é a camada de apresentação da saída do engine.
"""

import os, sys
from tabs.base import page

SLUG = 'training'

# Caminho para a Tabela_Mestre — configurável via variável de ambiente.
# O servidor usa TRAINING_TABELA_PATH se definido, senão tenta path relativo.
_TABELA_PATH_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    'Tabela_Mestre_Training_Engine_V5__1_.xlsx'
)

BODY = """
<div>
  <h1>Training</h1>
  <p class="sub">Opções de treinamento fisiologicamente plausíveis com base nos achados existentes.</p>

  <!-- SELECÇÃO DE MODALIDADE -->
  <div class="controls" style="margin-bottom:16px;">
    <label class="sel">Modalidade:
      <select id="trModalidade" onchange="trCarregar()">
        <option value="">— escolher —</option>
        <option value="Bike">Bike</option>
        <option value="Row">Row</option>
        <option value="Ski">Ski</option>
        <option value="Run">Run</option>
      </select>
    </label>
    <label class="sel" style="margin-left:12px;">Sessão MOXY (Day 1):
      <select id="trMoxyId" onchange="trCarregar()" style="min-width:220px;">
        <option value="">— escolher —</option>
      </select>
    </label>
    <label class="sel" style="margin-left:12px;">Verificação VST (Day 2):
      <select id="trVstId" onchange="trCarregar()" style="min-width:220px;">
        <option value="">— nenhuma —</option>
      </select>
    </label>
    <button onclick="trCarregar()"
      style="margin-left:12px;padding:7px 16px;background:#1c2331;border:1px solid #5DADE2;
      color:#5DADE2;border-radius:6px;cursor:pointer;font-size:13px;">
      ↻ Executar engine
    </button>
  </div>

  <div id="trEstado" style="font-size:12px;color:#8b949e;margin-bottom:12px;"></div>

  <!-- RESULTADO -->
  <div id="trResultado"></div>
</div>
"""

JS = r"""
// ── Estado ───────────────────────────────────────────────────────────────
let TR_ULT_RESULTADO = null;

// ── Inicialização ─────────────────────────────────────────────────────────
(function(){
  trCarregarSessoesMoxy();
  trCarregarVst();
})();

// ── Carregar lista de sessões MOXY disponíveis ────────────────────────────
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

// ── Carregar lista de verificações VST disponíveis ────────────────────────
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

// ── Ponto de entrada principal ────────────────────────────────────────────
function trCarregar(){
  const modalidade = (document.getElementById('trModalidade')||{}).value||'';
  const moxyId     = (document.getElementById('trMoxyId')||{}).value||'';
  const vstId      = (document.getElementById('trVstId')||{}).value||'';
  const est = document.getElementById('trEstado');
  const out = document.getElementById('trResultado');

  if(!modalidade){
    out.innerHTML = trCardErro('dados_insuficientes',
      'Seleccione uma modalidade para executar o engine.');
    return;
  }
  if(est) est.textContent = 'a montar contexto e executar engine…';
  out.innerHTML = '<div class="loading">a calcular…</div>';

  // Montar contexto via fetches aos endpoints existentes
  Promise.all([
    moxyId ? fetch('/api/moxy/limiares/'+moxyId).then(r=>r.json()).catch(()=>null) : Promise.resolve(null),
    moxyId ? fetch('/api/moxy/rede/'+moxyId).then(r=>r.json()).catch(()=>null)     : Promise.resolve(null),
    moxyId ? fetch('/api/moxy/interpretacao/'+moxyId).then(r=>r.json()).catch(()=>null) : Promise.resolve(null),
    vstId  ? fetch('/api/moxy/vst/resultado/'+vstId).then(r=>r.json()).catch(()=>null)  : Promise.resolve(null),
    fetch('/api/moxy/analises'+(modalidade?'?modalidade='+encodeURIComponent(modalidade):'')).then(r=>r.json()).catch(()=>({analises:[]})),
    fetch('/api/cp/actual/'+encodeURIComponent(modalidade)).then(r=>r.json()).catch(()=>null),
  ]).then(function(res){
    const limiares    = res[0];
    const rede        = res[1];
    const interpretac = res[2];
    const vstResult   = res[3];
    const analises    = (res[4]||{}).analises || [];
    const cp          = res[5];

    // Construir contexto para training.executar
    const contexto = trMontarContexto(
      modalidade, moxyId, vstId,
      limiares, rede, interpretac, vstResult, analises, cp
    );

    // Chamar o engine via endpoint backend
    fetch('/api/training/executar', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(contexto),
    }).then(r=>r.json()).then(function(resultado){
      TR_ULT_RESULTADO = resultado;
      if(est) est.textContent = 'engine executado · '+(resultado.opcoes||[]).length+' opção(ões)';
      out.innerHTML = trRenderResultado(resultado, contexto);
    }).catch(function(e){
      out.innerHTML = trCardErro('erro', 'Erro ao executar engine: '+e.message);
    });
  }).catch(function(e){
    out.innerHTML = trCardErro('erro', 'Erro ao montar contexto: '+e.message);
  });
}

// ── Montar contexto ───────────────────────────────────────────────────────
function trMontarContexto(
  modalidade, moxyId, vstId,
  limiares, rede, interpretac, vstResult, analises, cp
){
  // BP1/BP2 da sessão MOXY seleccionada
  const lc = (limiares&&limiares.limiares_consenso)||{};
  const bp1_w = (lc.primeiro||{}).mediana||null;
  const bp2_w = (lc.segundo||{}).mediana||null;
  const cp_w  = cp&&cp.cp_w||null;

  // Achados: rede causal
  const redeLim = (rede&&rede.limitador)||{};
  const achadoRede = {
    sistema:    redeLim.sistema||null,
    rotulo:     redeLim.rotulo||null,
    pct:        redeLim.pct||null,
    disponivel: !!(rede&&rede.status==='ok'&&redeLim.sistema),
  };

  // Achados: intervenções (5-1-5)
  const intr = (interpretac&&interpretac.interpretacao)||{};
  const achadoInterv = {
    us_limitador:    (intr.us||{}).limitador||null,
    pc_limitador:    (intr.pc||{}).limitador||null,
    intervencao_nome: null,
    disponivel:      !!(interpretac&&interpretac.status==='ok'),
  };

  // Achados: VST
  let achadoVst = {disponivel: false};
  if(vstResult&&vstResult.status==='ok'){
    const lbp1 = vstResult.limiter_bp1||{};
    const lbp2 = vstResult.limiter_bp2||{};
    achadoVst = {
      disponivel: true,
      limiter_bp1_padrao:       lbp1.padrao||null,
      limiter_bp2_padrao:       lbp2.padrao||null,
      comparacao_bp1_status:    (vstResult.comparacao_bp1||{}).status||null,
      comparacao_bp2_status:    (vstResult.comparacao_bp2||{}).status||null,
      comparacao_recovery_bp1:  (vstResult.comparacao_recovery_bp1||{}).status||null,
      comparacao_recovery_bp2:  (vstResult.comparacao_recovery_bp2||{}).status||null,
      rpe_bp1:                  vstResult.comparacao_rpe_bp1||null,
      rpe_bp2:                  vstResult.comparacao_rpe_bp2||null,
    };
  }

  // Histórico: sessões MOXY com BP próprios
  const historico = (analises||[]).map(function(a){
    return {
      activity_id:         a.activity_id,
      modalidade:          a.modalidade,
      data:                a.data||'',
      bp1_w:               a.bp1_w||null,
      bp2_w:               a.bp2_w||null,
      hr_medio_por_zona:   null,  // não disponível via /analises — declarado ausente
      rf_medio_por_zona:   null,
      smo2_por_zona:       null,
      rpe_por_zona:        null,
      potencia_por_zona:   null,
      split_medio_por_zona: null,
      dose_executada:      null,
      resultado:           null,
      dados_por_bloco:     {tem_dados_por_bloco: false},
    };
  });

  return {
    modalidade: modalidade,
    vst_activity_id:  vstId||null,
    moxy_activity_id: moxyId||null,
    bp1_w: bp1_w,
    bp2_w: bp2_w,
    cp_w:  cp_w,
    achados: {
      rede_causal:  achadoRede,
      intervencoes: achadoInterv,
      vst:          achadoVst,
      moxy:         {disponivel: false},
    },
    historico: historico,
    pace_individual: null,
  };
}

// ── Renderizar resultado completo ─────────────────────────────────────────
function trRenderResultado(r, contexto){
  let h = '';

  // 1. CONTEXTO ATUAL
  h += trSecContexto(r, contexto);

  // 2. EVIDÊNCIAS
  h += trSecEvidencias(r);

  // 3. STATUS
  if(r.status === 'dados_insuficientes'){
    h += trCardErro('dados_insuficientes',
      'Dados estruturais insuficientes para executar o engine. '
      + (r.motivo||'')
      + '<br><br>Ausências: '+(r.dados_ausentes||[]).join(' · '));
    return h;
  }
  if(r.status === 'sem_opcao_plausivel'){
    h += '<div style="border:1px solid #F4D03F;border-radius:8px;padding:12px 16px;margin:12px 0;">'
      + '<b style="color:#F4D03F;">Sem opção plausível</b>'
      + '<p style="font-size:12px;color:#8b949e;margin:6px 0 0;">'
      + (r.motivo_sem_opcao||'Nenhuma regra sobreviveu aos filtros para os achados actuais.')+'</p>'
      + trDetalhesExcluidas(r.excluidas||[])
      + '</div>';
    return h;
  }

  // 4. OPÇÕES
  h += trSecOpcoes(r);

  // 5. REGRAS EXCLUÍDAS (auditabilidade)
  if((r.excluidas||[]).length){
    h += '<details style="margin:10px 0;">'
      + '<summary style="cursor:pointer;font-size:12px;color:#8b949e;padding:4px 0;">▼ Regras excluídas pelo engine ('+r.excluidas.length+')</summary>'
      + trDetalhesExcluidas(r.excluidas)
      + '</details>';
  }

  // 6. DADOS AUSENTES
  if((r.dados_ausentes||[]).length){
    h += '<details style="margin:6px 0;">'
      + '<summary style="cursor:pointer;font-size:12px;color:#8b949e;padding:4px 0;">▼ Dados ausentes ('+(r.dados_ausentes||[]).length+')</summary>'
      + '<div style="font-size:11px;color:#8b949e;padding:8px;">'
      + (r.dados_ausentes||[]).map(d=>'<div style="margin:2px 0;">· '+d+'</div>').join('')
      + '</div></details>';
  }

  return h;
}

// ── Secção: Contexto actual ───────────────────────────────────────────────
function trSecContexto(r, ctx){
  const bp1 = ctx.bp1_w ? Math.round(ctx.bp1_w)+'W' : '—';
  const bp2 = ctx.bp2_w ? Math.round(ctx.bp2_w)+'W' : '—';
  const lims = (r.achados_mapeados||[]).filter(a=>a.limitador_chave);

  let divs = (r.achados_mapeados||[]).map(function(a){
    if(!a.limitador_chave) return trBadge(
      'padrão sem chave: '+(a.nota||'').slice(0,60), '#8b949e');
    const cor = {entrega:'#5DADE2',utilizacao:'#3FB950',respiratorio:'#F4D03F',
                 fadiga:'#E67E22',mecanico:'#A371F7'}[a.limitador_chave]||'#8b949e';
    return trBadge(
      (a.limitador||a.limitador_chave)
      +' · '+(a.plausibilidade||'?')
      +' · '+a.fonte,
      cor);
  }).join('');

  let divs_div = (r.divergencias||[]).map(function(d){
    return '<div style="font-size:11px;color:#E67E22;margin:2px 0;">⚠ '+d.nota+'</div>';
  }).join('');

  return '<div class="card" style="margin-bottom:12px;padding:14px;">'
    + '<div style="font-size:9px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;">Contexto actual</div>'
    + '<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:13px;">'
    + '<div><span style="color:#8b949e;">Modalidade</span> <b style="color:#5DADE2;">'+ctx.modalidade+'</b></div>'
    + '<div><span style="color:#8b949e;">BP1</span> <b>'+bp1+'</b></div>'
    + '<div><span style="color:#8b949e;">BP2</span> <b>'+bp2+'</b></div>'
    + (ctx.cp_w?'<div><span style="color:#8b949e;">CP</span> <b style="color:#6e7681;">'+Math.round(ctx.cp_w)+'W (ref.)</b></div>':'')
    + '</div>'
    + '<div style="margin-top:8px;">' + divs + '</div>'
    + (divs_div?'<div style="margin-top:6px;">'+divs_div+'</div>':'')
    + '</div>';
}

// ── Secção: Evidências ────────────────────────────────────────────────────
function trSecEvidencias(r){
  const achs = r.achados_mapeados||[];
  if(!achs.length) return '';

  const porFonte = {};
  achs.forEach(function(a){ (porFonte[a.fonte]=porFonte[a.fonte]||[]).push(a); });

  let rows = Object.entries(porFonte).map(function([fonte, lista]){
    return '<tr><td style="color:#8b949e;width:120px;font-size:11px;">'+fonte+'</td>'
      + '<td style="font-size:12px;">'
      + lista.map(a=>(a.limitador||'sem chave')
        +'<span style="color:#6e7681;"> · '+(a.plausibilidade||'?')+'</span>'
        +(a.nota?'<br><span style="font-size:10px;color:#8b949e;">'+a.nota+'</span>':'')).join('<br>')
      + '</td></tr>';
  }).join('');

  return '<details style="margin:8px 0;">'
    + '<summary style="cursor:pointer;font-size:12px;color:#8b949e;padding:4px 0;">▼ Evidências por fonte</summary>'
    + '<table style="font-size:12px;margin-top:6px;"><tbody>'+rows+'</tbody></table>'
    + '</details>';
}

// ── Secção: Opções de treinamento ─────────────────────────────────────────
function trSecOpcoes(r){
  const ops = r.opcoes||[];
  if(!ops.length) return '<p style="color:#8b949e;font-size:13px;">Nenhuma opção retornada.</p>';

  let h = '<h2 style="margin-top:20px;">Opções de treinamento</h2>';
  ops.forEach(function(op){
    h += trRenderOpcao(op);
  });
  return h;
}

// ── Render de uma opção ───────────────────────────────────────────────────
function trRenderOpcao(op){
  const corZona = {Z1:'#1E3A5F',Z2:'#1B5E20',Z3:'#4A1C12'}[op.zona]||'#161b22';
  const corRel  = {principal:'#3FB950',possível:'#F4D03F',limiar:'#E67E22'}[op.relevance]||'#8b949e';
  const dose = op.dose||{};
  const refs = op.referencias_individuais||{};
  const prog = op.progressao||{};

  // Header do card
  let h = '<div style="border:1px solid #30363d;border-radius:8px;margin-bottom:14px;overflow:hidden;">'
    // Barra de cabeçalho com zona
    + '<div style="background:'+corZona+'22;border-bottom:1px solid #30363d;padding:10px 14px;'
    + 'display:flex;align-items:center;gap:10px;flex-wrap:wrap;">'
    + '<span style="font-size:11px;background:'+corZona+'55;color:#c9d1d9;'
    + 'border-radius:4px;padding:2px 8px;">'+op.zona+'</span>'
    + '<b style="font-size:14px;">'+op.training_type+' — '+op.format+'</b>'
    + '<span style="margin-left:auto;font-size:11px;color:'+corRel+';">'+op.relevance+'</span>'
    + '<span style="font-size:10px;color:#6e7681;">'+op.rule_id+'</span>'
    + '</div>'
    + '<div style="padding:12px 14px;">';

  // Adaptação e mecanismo
  h += '<div style="font-size:12px;color:#8b949e;margin-bottom:8px;">'
    + '<b style="color:#c9d1d9;">Adaptação:</b> '+op.adaptacao+'<br>'
    + '<b style="color:#c9d1d9;">Mecanismo:</b> '+op.mecanismo_alvo
    + '</div>';

  // Grid: dose | referências | monitoramento
  h += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:10px;">';

  // Dose
  h += '<div style="background:#0d1117;border-radius:6px;padding:10px;">'
    + '<div style="font-size:9px;color:#8b949e;text-transform:uppercase;margin-bottom:5px;">Dose</div>'
    + '<div style="font-size:12px;"><b>'+dose.work_range+'</b></div>'
    + (dose.recovery_range&&dose.recovery_range!=='—'?'<div style="font-size:11px;color:#8b949e;">Recovery: '+dose.recovery_range+'</div>':'')
    + '<div style="font-size:11px;color:#8b949e;margin-top:4px;">RPE esperado: '+op.expected_RPE_work+'</div>'
    + (dose.ponto_de_partida?'<div style="font-size:10px;color:#6e7681;margin-top:4px;">'
      + 'Ponto de partida: '+dose.ponto_de_partida
      + ' <span style="color:#484f58;">('+dose.ponto_de_partida_fonte+')</span>'
      + '</div>':'')
    + (dose.pace_referencia?'<div style="font-size:11px;color:#8b949e;margin-top:3px;">Pace ref.: '+dose.pace_referencia+'</div>':'')
    + (dose.nota_distancia&&dose.pace_referencia===null?'<div style="font-size:10px;color:#6e7681;margin-top:3px;">'+dose.nota_distancia+'</div>':'')
    + '</div>';

  // Referências individuais
  const refsDisponiveis = Object.entries(refs).filter(
    ([k,v])=>v&&v.disponivel&&k!=='RPE_faixa_tabela'
  );
  const refsTxt = refsDisponiveis.length
    ? refsDisponiveis.map(function([k,v]){
        let val = '';
        if(v.min!=null&&v.max!=null) val = v.min+'–'+v.max;
        else if(v.teto!=null) val = '≤'+v.teto;
        else if(v.trend) val = v.trend;
        return '<div style="font-size:11px;"><span style="color:#8b949e;">'+k+':</span> <b>'+val+'</b></div>';
      }).join('')
    : '<div style="font-size:11px;color:#8b949e;">Indisponíveis — histórico insuficiente</div>';

  h += '<div style="background:#0d1117;border-radius:6px;padding:10px;">'
    + '<div style="font-size:9px;color:#8b949e;text-transform:uppercase;margin-bottom:5px;">Referências individuais</div>'
    + refsTxt
    + '</div>';

  // Monitoramento
  h += '<div style="background:#0d1117;border-radius:6px;padding:10px;">'
    + '<div style="font-size:9px;color:#8b949e;text-transform:uppercase;margin-bottom:5px;">Monitorar</div>'
    + '<div style="font-size:11px;"><b>'+op.monitoramento.primary+'</b></div>'
    + (op.monitoramento.rules||[]).filter(Boolean).map(
        rule=>'<div style="font-size:10px;color:#8b949e;margin-top:3px;">'+rule+'</div>'
      ).join('')
    + '</div>';

  h += '</div>'; // fim grid

  // Critérios expandíveis (success / failure / work_stop / recovery_gate)
  h += '<details style="margin-top:6px;">'
    + '<summary style="cursor:pointer;font-size:11px;color:#8b949e;padding:3px 0;">▼ Critérios de sucesso, falha e interrupção</summary>'
    + '<div style="font-size:11px;padding:8px 0;color:#c9d1d9;">'
    + (op.success_rule?'<div style="margin-bottom:6px;"><b style="color:#3FB950;">✓ Sucesso:</b> '+op.success_rule+'</div>':'')
    + (op.failure_rule?'<div style="margin-bottom:6px;"><b style="color:#F4D03F;">⚠ Falha:</b> '+op.failure_rule+'</div>':'')
    + (op.work_stop_rule?'<div style="margin-bottom:6px;"><b style="color:#E74C3C;">✗ Interrupção:</b> '+op.work_stop_rule+'</div>':'')
    + (op.recovery_gate?'<div><b style="color:#5DADE2;">⟳ Recovery gate:</b> '+op.recovery_gate+'</div>':'')
    + '</div></details>';

  // Progressão
  h += '<details style="margin-top:4px;">'
    + '<summary style="cursor:pointer;font-size:11px;color:#8b949e;padding:3px 0;">▼ Progressão</summary>'
    + '<div style="font-size:11px;padding:8px 0;color:#c9d1d9;">'
    + '<div style="color:#8b949e;margin-bottom:4px;">'+prog.ordem+'</div>'
    + (prog.estado_derivado?'<div style="margin-bottom:4px;"><b>Estado:</b> '+prog.estado_derivado
      +' <span style="color:#6e7681;">('+prog.estado_fonte+')</span></div>':'')
    + (prog.nota?'<div style="color:#8b949e;font-size:10px;font-style:italic;">'+prog.nota+'</div>':'')
    + (prog.gate?'<div style="margin-top:4px;"><b>Gate:</b> '+prog.gate+'</div>':'')
    + (prog.regression_gate?'<div style="margin-top:4px;"><b>Regressão:</b> '+prog.regression_gate+'</div>':'')
    + '</div></details>';

  // Por que esta opção aparece (auditabilidade)
  h += '<details style="margin-top:4px;">'
    + '<summary style="cursor:pointer;font-size:11px;color:#8b949e;padding:3px 0;">▼ Por que esta opção aparece</summary>'
    + '<div style="font-size:11px;padding:8px 0;color:#8b949e;">'
    + '<div><b style="color:#c9d1d9;">Limitador:</b> '+op.limitador+'</div>'
    + '<div><b style="color:#c9d1d9;">Adaptação:</b> '+op.adaptacao+'</div>'
    + '<div><b style="color:#c9d1d9;">Mecanismo-alvo:</b> '+op.mecanismo_alvo+'</div>'
    + '<div><b style="color:#c9d1d9;">Regra da tabela:</b> '+op.rule_id+' (evidência '+op.evidence_level+')</div>'
    + '</div></details>';

  h += '</div></div>'; // fim padding + card
  return h;
}

// ── Detalhes das regras excluídas ─────────────────────────────────────────
function trDetalhesExcluidas(excluidas){
  if(!excluidas.length) return '';
  return '<div style="font-size:11px;padding:8px;">'
    + excluidas.map(e=>'<div style="color:#8b949e;margin:2px 0;">'
      + '<b style="color:#484f58;">'+e.rule_id+'</b> — '+e.motivo+'</div>').join('')
    + '</div>';
}

// ── Helpers ───────────────────────────────────────────────────────────────
function trBadge(txt, cor){
  return '<span style="font-size:10px;background:'+cor+'22;color:'+cor+';'
    + 'border:1px solid '+cor+'55;border-radius:4px;padding:2px 8px;'
    + 'margin:2px 4px 2px 0;display:inline-block;white-space:nowrap;">'
    + txt + '</span>';
}

function trCardErro(tipo, msg){
  const cor = tipo==='dados_insuficientes'?'#E67E22':'#E74C3C';
  return '<div style="border:1px solid '+cor+';border-radius:8px;padding:12px 16px;margin:12px 0;">'
    + '<b style="color:'+cor+';">'+(tipo==='dados_insuficientes'?'Dados insuficientes':'Erro')+'</b>'
    + '<p style="font-size:12px;color:#8b949e;margin:6px 0 0;">'+msg+'</p></div>';
}
"""


def render():
    from flask import render_template_string
    return render_template_string(page('Training', SLUG, BODY, JS))
