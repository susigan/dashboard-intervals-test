// Harness: executa o JS real do bloco "Modelo IR: curvas de influencia conjuntas" (entre <<modelo-ir>>) num
// contexto vm com DOM, canvas e fetch simulados. As RESPOSTAS NAO SAO ESCRITAS A MAO: vem de
// tests/pmc/ir_payload.py (Flask test client + ajustes/atividades sinteticos). Sem rede.
// Uso: node harness_ir.js <pmc_js.js> <respostas_reais.json>. Imprime JSON {cenario: resultado}.
setTimeout(() => { console.log(JSON.stringify({watchdog: 'sem fim apos 20 s'})); process.exit(3); }, 20000).unref();
const fs = require('fs'), vm = require('vm');
const todo = fs.readFileSync(process.argv[2], 'utf8');
const REAL = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const i0 = todo.indexOf('// <<modelo-ir>>'), i1 = todo.indexOf('// <</modelo-ir>>');
if (i0 < 0 || i1 < i0) { console.log(JSON.stringify({erro: 'marcadores nao encontrados'})); process.exit(2); }
const bloco = todo.slice(i0, i1);
const U = '/api/modelo/ir/conjunto';            // um ajuste por modalidade (MMP3): sem seletor de duracao

function fakeG(rec) {
  const g = {globalAlpha: 1, lineWidth: 1, strokeStyle: '', fillStyle: ''};
  ['beginPath', 'closePath', 'fillRect', 'clearRect', 'save', 'restore', 'rect', 'clip', 'strokeRect']
    .forEach(k => { g[k] = () => {}; });
  g.setLineDash = (d) => { rec.dashes.push(d.join(',')); };
  g.moveTo = () => { rec.moveTo++; }; g.lineTo = () => { rec.lineTo++; };
  g.stroke = () => { rec.stroke++; rec.tracos.push({cor: g.strokeStyle, lw: g.lineWidth, a: g.globalAlpha}); };
  g.fill = () => { rec.fill++; }; g.arc = () => { rec.arc++; };
  g.fillText = (s) => { rec.texts.push(String(s)); };
  return g;
}

async function cenario(nome, fn) {
  const els = {}, chamadas = [], canvases = {}, tips = {}, semData = [];
  const el = (id) => {
    if (!els[id]) { let h = ''; const o = {value: '', checked: false, onchange: null};
      Object.defineProperty(o, 'innerHTML', {get: () => h, set: v => { h = v; }}); els[id] = o; }
    return els[id];
  };
  const respostas = {};
  const mapa = {[U]: REAL.normal};
  let falha = false; const atrasos = {};
  const fetch = (url, opts) => {
    chamadas.push({url, metodo: (opts && opts.method) || 'GET'});
    if (falha) return Promise.reject(new Error('rede'));
    const corpo = (url in respostas) ? respostas[url] : mapa[url];
    const p = () => Promise.resolve({json: () => Promise.resolve(corpo)});
    const d = atrasos[url]; delete atrasos[url];                  // o atraso aplica-se so ao pedido seguinte
    return d ? new Promise(r => setTimeout(() => r(p()), d)) : p();
  };
  const sandbox = {
    document: {getElementById: el}, fetch, Array, Set, Math, JSON, String, Object, Number, Promise, Date, isFinite,
    console: {error() {}, log() {}}, setTimeout,
    ctx: (id, h) => { const rec = {moveTo: 0, lineTo: 0, stroke: 0, fill: 0, arc: 0, texts: [], dashes: [], tracos: []}; canvases[id] = rec;
      return {g: fakeG(rec), W: 600, H: h}; },
    noData: (g, W, H, msg) => { semData.push(msg || ''); },
    registarTip: (id, f) => { tips[id] = f; },
    linhaTip: (cor, nome, v) => `<div data-cor="${cor}">${nome}:${v}</div>`,
  };
  vm.createContext(sandbox);
  vm.runInContext(bloco + '\n;globalThis.__api = {abrirIR, carregarIR, irShell, irSeries, irAlternar, irLegendaHtml, get J(){return IR_J;}, OFF: IR_OFF};', sandbox);
  const api = sandbox.__api;
  const esperar = (ms) => new Promise(r => setTimeout(r, ms || 15));
  return {nome, r: await fn({api, el, els, chamadas, canvases, tips, semData, falhar: (v) => { falha = v; },
                             atraso: (u, ms) => { atrasos[u] = ms; }, esperar, respostas})};
}
(async () => {
  const out = {};
  const run = async (n, f) => { const x = await cenario(n, f); out[x.nome] = x.r; };
  await run('definir_nao_pede_nada', async ({chamadas}) => ({chamadas: chamadas.length}));
  await run('fechado_nao_pede', async ({api, chamadas, esperar}) => { api.abrirIR({open: false}); await esperar(); return {chamadas: chamadas.length}; });
  await run('abrir_pede_um_get_uma_vez', async ({api, chamadas, esperar}) => {
    api.abrirIR({open: true}); await esperar(); api.abrirIR({open: true}); await esperar();
    return {chamadas: chamadas.slice()};
  });
  await run('render_normal', async ({api, els, canvases, tips, semData, esperar}) => {
    api.abrirIR({open: true}); await esperar();
    return {corpo: els.irExpCorpo.innerHTML, leg: els.lgIrConj.innerHTML, escala: els.irEscala.innerHTML, avisos: els.irAvisos.innerHTML,
            tabela: els.irTabela.innerHTML, overall: els.irOverall.innerHTML, mmp3: els.irMmp3.innerHTML, info: els.irInfo.innerHTML, canvas: canvases.chIrConj, semData,
            tip: tips.chIrConj(300), tipInicio: tips.chIrConj(54), tipFora: tips.chIrConj(2),
            canvases: Object.keys(canvases), series: api.irSeries(api.J).map(s => ({mod: s.mod, comp: s.comp, cor: s.cor, nid: s.nid, n: s.vals.length, lw: s.estilo.lw, dash: s.estilo.dash.join(',')}))};
  });
  await run('legenda_alterna_sem_pedir_nada', async ({api, els, chamadas, canvases, esperar}) => {
    api.abrirIR({open: true}); await esperar();
    const serie = () => canvases.chIrConj.tracos.filter(t => t.lw > 1);       // tracos de serie (a grelha usa lw 1)
    const antes = {chamadas: chamadas.length}, legInicial = els.lgIrConj.innerHTML;
    const s0 = serie().length, so_liquida0 = serie().every(t => t.lw === 2.6);   // por omissao: so liquida
    api.irAlternar('comp', 'positiva'); api.irAlternar('comp', 'negativa'); const s1 = serie().length;
    api.irAlternar('mod', 'Bike'); const semBike = els.lgIrConj.innerHTML, s2 = serie().length;
    return {antes, depoisChamadas: chamadas.length, legInicial, semBike, s0, s1, s2, so_liquida0, off: JSON.stringify(api.OFF)};
  });
  await run('todas_desligadas', async ({api, canvases, semData, esperar}) => {
    api.abrirIR({open: true}); await esperar();
    ['Bike', 'Row', 'Run', 'Ski'].forEach(m => api.irAlternar('mod', m));
    return {semData};
  });
  await run('misto', async ({api, els, respostas, canvases, tips, esperar}) => {
    respostas[U] = REAL.misto; api.abrirIR({open: true}); await esperar();
    return {avisos: els.irAvisos.innerHTML, leg: els.lgIrConj.innerHTML, tabela: els.irTabela.innerHTML, tracos: canvases.chIrConj.tracos,
            dashes: canvases.chIrConj.dashes, tip: tips.chIrConj(560), texts: canvases.chIrConj.texts,
            series: api.irSeries(api.J).map(s => ({mod: s.mod, comp: s.comp, nid: s.nid, dash: s.estilo.dash.join(',')}))};
  });
  await run('sem_curvas', async ({api, els, respostas, canvases, semData, chamadas, esperar}) => {
    respostas[U] = REAL.sem_curvas; api.abrirIR({open: true}); await esperar();
    return {info: els.irInfo.innerHTML, semData, leg: els.lgIrConj.innerHTML, avisos: els.irAvisos.innerHTML,
            tracos: canvases.chIrConj.tracos.length, series: api.irSeries(api.J).length, escala: els.irEscala.innerHTML};
  });
  await run('sem_ajuste_e_p0_invalido', async ({api, els, respostas, canvases, esperar}) => {
    respostas[U] = REAL.sem_ajuste_e_p0_invalido; api.abrirIR({open: true}); await esperar();
    return {leg: els.lgIrConj.innerHTML, avisos: els.irAvisos.innerHTML, escala: els.irEscala.innerHTML, tabela: els.irTabela.innerHTML,
            series: api.irSeries(api.J).map(s => s.mod + ':' + s.comp), tracos: canvases.chIrConj.tracos.length};
  });
  await run('diagnostico_normal', async ({api, els, respostas, esperar}) => {
    respostas[U] = REAL.misto; api.abrirIR({open: true}); await esperar();
    return {diag: els.irDiag.innerHTML};
  });
  await run('diagnostico_resposta_antiga', async ({api, els, respostas, esperar}) => {
    const antiga = JSON.parse(JSON.stringify(REAL.misto));           // resposta sem os campos novos
    Object.keys(antiga.modalidades).forEach(m => {
      ['tentativa_mais_recente', 'requisitos', 'convergencia', 'sem_calibracao_valida',
       'avisos_identificabilidade', 'ajuste_valido_anterior'].forEach(k => { delete antiga.modalidades[m][k]; });
    });
    respostas[U] = antiga; api.abrirIR({open: true}); await esperar();
    return {diag: els.irDiag.innerHTML, tabela: els.irTabela.innerHTML};
  });
  await run('diagnostico_misto', async ({api, els, respostas, esperar}) => {
    respostas[U] = REAL.misto; api.abrirIR({open: true}); await esperar();
    return {diag: els.irDiag.innerHTML};
  });
  await run('diagnostico_antigo_insuficiente', async ({api, els, respostas, esperar}) => {
    // formato anterior a Etapa 9: sem campos novos; Bike em dados insuficientes com ajuste v2 (sem calibracao valida)
    const antiga = JSON.parse(JSON.stringify(REAL.misto));
    Object.keys(antiga.modalidades).forEach(m => {
      ['tentativa_mais_recente', 'requisitos', 'convergencia', 'sem_calibracao_valida',
       'avisos_identificabilidade', 'ajuste_valido_anterior', 'estado_ajuste'].forEach(k => { delete antiga.modalidades[m][k]; });
    });
    antiga.modalidades.Bike = {estado: 'dados_insuficientes', ajuste: {versao_ajuste: 2}, p0: null,
                               parametros: null, curvas: null, leitura: 'dados insuficientes'};
    respostas[U] = antiga; api.abrirIR({open: true}); await esperar();
    return {diag: els.irDiag.innerHTML};
  });
  await run('desatualizado_e_falha', async ({api, els, respostas, esperar}) => {
    respostas[U] = REAL.desatualizado_e_falha; api.abrirIR({open: true}); await esperar();
    return {avisos: els.irAvisos.innerHTML, tabela: els.irTabela.innerHTML};
  });
  await run('sem_cp', async ({api, els, respostas, esperar}) => {
    respostas[U] = REAL.sem_cp; api.abrirIR({open: true}); await esperar();
    return {avisos: els.irAvisos.innerHTML, tabela: els.irTabela.innerHTML};
  });
  await run('falha_de_rede', async ({api, els, semData, falhar, esperar}) => {
    falhar(true); api.abrirIR({open: true}); await esperar();
    return {info: els.irInfo.innerHTML, semData, tabela: els.irTabela.innerHTML};
  });
  await run('resposta_antiga_nao_sobrepoe_a_nova', async ({api, respostas, atraso, esperar}) => {
    atraso(U, 80);
    api.carregarIR();                                                 // 1.o pedido (normal, 4 modalidades), lento
    await esperar(5); respostas[U] = REAL.misto; api.carregarIR();    // 2.o pedido (misto, 3 modalidades), rapido
    await esperar(150);
    return {n_mod: api.J.modalidades_com_curva.length};
  });
  await run('xss_escapado', async ({api, els, respostas, esperar}) => {
    respostas[U] = REAL.misto; api.abrirIR({open: true}); await esperar();
    return {avisos: els.irAvisos.innerHTML, tabela: els.irTabela.innerHTML, leg: els.lgIrConj.innerHTML};
  });
  await run('a_verificar_render', async ({api, els, respostas, esperar}) => {
    const j = JSON.parse(JSON.stringify(REAL.normal));                  // resposta REAL + candidatos injetados
    const mod = j.modalidades_ordem[0];
    j.modalidades[mod].mmp3 = Object.assign({}, j.modalidades[mod].mmp3 || {}, {a_verificar: [{
      test_id: 't1', activity_id: '<img src=x onerror=alert(1)>', data: '2025-05-10', watts: 330,
      melhor_anterior_watts: 300, melhor_anterior_data: '2025-01-10', pct_do_melhor: 110,
      tags: ['<b>Moxy</b>'], tag_3min: false, classe_intervalo: 'sem_dados_de_intervalos', intervalo: null}]});
    respostas[U] = j; api.abrirIR({open: true}); await esperar();
    return {mmp3: els.irMmp3.innerHTML};
  });
  process.stdout.write(JSON.stringify(out) + '\n', () => process.exit(0));
})();
