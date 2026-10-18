// Harness: executa o JS real do bloco "Modelo IR: curvas de influencia" (entre <<modelo-ir>>) num
// contexto vm com DOM, canvas e fetch simulados. As RESPOSTAS NAO SAO ESCRITAS A MAO: vem de
// tests/pmc/ir_payload.py (Flask test client + SQLite temporaria + dados sinteticos). Sem rede.
// Uso: node harness_ir.js <pmc_js.js> <respostas_reais.json>. Imprime JSON {cenario: resultado}.
setTimeout(() => { console.log(JSON.stringify({watchdog: 'sem fim apos 20 s'})); process.exit(3); }, 20000).unref();
const fs = require('fs'), vm = require('vm');
const todo = fs.readFileSync(process.argv[2], 'utf8');
const REAL = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const i0 = todo.indexOf('// <<modelo-ir>>'), i1 = todo.indexOf('// <</modelo-ir>>');
if (i0 < 0 || i1 < i0) { console.log(JSON.stringify({erro: 'marcadores nao encontrados'})); process.exit(2); }
const bloco = todo.slice(i0, i1);
const U = (m, d, a) => `/api/modelo/ir/influencia?tipo=${m}&duracao=${d}` + (a ? '&atividade=' + encodeURIComponent(a) : '');

function fakeG(rec) {
  const g = {};
  ['beginPath', 'closePath', 'setLineDash', 'fillRect', 'clearRect', 'save', 'restore', 'rect', 'clip', 'strokeRect']
    .forEach(k => { g[k] = () => {}; });
  g.moveTo = () => { rec.moveTo++; }; g.lineTo = () => { rec.lineTo++; }; g.stroke = () => { rec.stroke++; };
  g.fill = () => { rec.fill++; }; g.arc = () => { rec.arc++; };
  g.fillText = (s) => { rec.texts.push(String(s)); };
  return g;
}

async function cenario(nome, fn) {
  const els = {}, chamadas = [], canvases = {}, tips = {}, semData = [];
  const el = (id) => {
    if (!els[id]) { let h = ''; const o = {value: '', checked: false, onchange: null};
      if (id === 'irMod') o.value = 'Bike'; if (id === 'irDur') o.value = '1200';     // padroes do <select> real
      Object.defineProperty(o, 'innerHTML', {get: () => h, set: v => { h = v; }}); els[id] = o; }
    return els[id];
  };
  let respostas = {};
  const base = {};
  const mapa = {
    [U('Bike', 1200)]: REAL.bike1200, [U('Bike', 300)]: REAL.bike300, [U('Row', 300)]: REAL.row300_sem_ajuste,
    [U('Bike', 1200, REAL.ativ_id)]: REAL.bike1200_ativ, [U('Bike', 1200, REAL.xss_id)]: REAL.xss,
  };
  let falha = false, atrasos = {};
  const fetch = (url) => {
    chamadas.push(url);
    if (falha) return Promise.reject(new Error('rede'));
    const corpo = (url in respostas) ? respostas[url] : mapa[url];
    const p = () => Promise.resolve({json: () => Promise.resolve(corpo)});
    return atrasos[url] ? new Promise(r => setTimeout(() => r(p()), atrasos[url])) : p();
  };
  const sandbox = {
    document: {getElementById: el}, fetch, Array, Set, Math, JSON, String, Object, Number, Promise, Date, isFinite,
    console: {error() {}, log() {}}, setTimeout,
    ctx: (id, h) => { const rec = {moveTo: 0, lineTo: 0, stroke: 0, fill: 0, arc: 0, texts: []}; canvases[id] = rec;
      return {g: fakeG(rec), W: 600, H: h}; },
    noData: (g, W, H, msg) => { semData.push(msg || ''); },
    registarTip: (id, f) => { tips[id] = f; },
    linhaTip: (cor, nome, v) => `<div>${nome}:${v}</div>`,
  };
  vm.createContext(sandbox);
  vm.runInContext(bloco + '\n;globalThis.__api = {abrirIR, carregarIR, irShell, irInfoHtml, get J(){return IR_J;}, irSeries};', sandbox);
  const api = sandbox.__api;
  const esperar = (ms) => new Promise(r => setTimeout(r, ms || 15));
  return {nome, r: await fn({api, el, els, chamadas, canvases, tips, semData, falhar: (v) => { falha = v; },
                             atraso: (u, ms) => { atrasos[u] = ms; }, esperar, respostas})};
}
(async () => {
  const out = {};
  const run = async (n, f) => { const x = await cenario(n, f); out[x.nome] = x.r; };
  await run('definir_nao_pede_nada', async ({chamadas}) => ({chamadas: chamadas.length}));
  await run('fechado_nao_pede', async ({api, chamadas}) => { api.abrirIR({open: false}); await new Promise(r => setTimeout(r, 15)); return {chamadas: chamadas.length}; });
  await run('abrir_pede_um_get_uma_vez', async ({api, chamadas, esperar}) => {
    api.abrirIR({open: true}); await esperar(); api.abrirIR({open: true}); await esperar();
    return {chamadas: chamadas.slice()};
  });
  await run('render_completo', async ({api, els, el, canvases, tips, semData, esperar}) => {
    api.abrirIR({open: true}); await esperar();
    const cp = els.chIrComp, tip = tips.chIrComp ? tips.chIrComp(300) : null, tipFora = tips.chIrComp ? tips.chIrComp(2) : null;
    return {corpo: els.irExpCorpo.innerHTML, info: els.irInfo.innerHTML, leg1: els.lgIrComp.innerHTML, leg2: els.lgIrLiq.innerHTML,
            canvases, semData, tip, tipFora, opcoes: els.irAtiv.innerHTML, n_ativ: REAL.bike1200.atividades.length};
  });
  await run('trocar_modalidade_e_duracao', async ({api, el, els, chamadas, esperar}) => {
    api.abrirIR({open: true}); await esperar();
    el('irDur').value = '300'; el('irMod').value = 'Bike'; els.irDur.onchange(); await esperar();
    const a = chamadas.slice(); el('irMod').value = 'Row'; els.irMod.onchange(); await esperar();
    return {chamadas: chamadas.slice(), info300: null, infoRow: els.irInfo.innerHTML, a};
  });
  await run('escolher_atividade', async ({api, el, els, chamadas, canvases, esperar}) => {
    api.abrirIR({open: true}); await esperar();
    el('irAtiv').value = REAL.ativ_id; els.irAtiv.onchange(); await esperar();
    return {chamadas: chamadas.slice(), leg: els.lgIrComp.innerHTML, ativ: els.irAtiv.value, J: JSON.stringify(Object.keys(canvases))};
  });
  await run('mudar_modalidade_limpa_atividade', async ({api, el, els, chamadas, esperar}) => {
    api.abrirIR({open: true}); await esperar();
    el('irAtiv').value = REAL.ativ_id; els.irAtiv.onchange(); await esperar();
    el('irMod').value = 'Row'; el('irDur').value = '300'; els.irMod.onchange(); await esperar();
    return {chamadas: chamadas.slice(), ativ: els.irAtiv.value};
  });
  await run('sem_ajuste', async ({api, el, els, canvases, semData, esperar}) => {
    api.abrirIR({open: true}); await esperar(); el('irMod').value = 'Row'; el('irDur').value = '300'; els.irMod.onchange(); await esperar();
    return {info: els.irInfo.innerHTML, semData, comLinhas: canvases.chIrComp ? canvases.chIrComp.lineTo : null};
  });
  await run('nao_identificavel', async ({api, els, respostas, esperar}) => {
    respostas[U('Bike', 1200)] = REAL.bike1200_nao_ident; api.abrirIR({open: true}); await esperar();
    return {info: els.irInfo.innerHTML};
  });
  await run('insuficiente', async ({api, els, respostas, canvases, semData, esperar}) => {
    respostas[U('Bike', 1200)] = REAL.bike1200_insuficiente; api.abrirIR({open: true}); await esperar();
    return {info: els.irInfo.innerHTML, semData, linhas: canvases.chIrComp.lineTo};
  });
  await run('sem_convergencia', async ({api, els, respostas, canvases, semData, esperar}) => {
    respostas[U('Bike', 1200)] = REAL.bike1200_sem_convergencia; api.abrirIR({open: true}); await esperar();
    return {info: els.irInfo.innerHTML, semData, linhas: canvases.chIrComp.lineTo};
  });
  await run('falha_de_rede', async ({api, els, falhar, esperar}) => {
    falhar(true); api.abrirIR({open: true}); await esperar();
    return {info: els.irInfo.innerHTML};
  });
  await run('resposta_antiga_nao_sobrepoe_a_nova', async ({api, el, els, atraso, esperar}) => {
    atraso(U('Bike', 1200), 80);
    api.abrirIR({open: true});                                        // 1.o pedido, lento
    await esperar(5); el('irDur').value = '300'; els.irDur.onchange();  // 2.o pedido, rapido
    await esperar(150);
    return {J: api.J.duracao_s, info: els.irInfo.innerHTML.includes('Ajuste v')};
  });
  await run('series', async ({api}) => {
    const s = api.irSeries(REAL.bike1200_ativ), vazio = api.irSeries(REAL.bike1200_insuficiente), sem = api.irSeries(REAL.row300_sem_ajuste);
    return {s, vazio, sem, P0: REAL.bike1200_ativ.parametros.p0};
  });
  await run('xss_escapado', async ({api, el, els, esperar}) => {
    api.abrirIR({open: true}); await esperar();
    el('irAtiv').value = REAL.xss_id; els.irAtiv.onchange(); await esperar();
    return {opcoes: els.irAtiv.innerHTML, info: els.irInfo.innerHTML, leg: els.lgIrComp.innerHTML};
  });
  process.stdout.write(JSON.stringify(out) + '\n', () => process.exit(0));   // so sai depois de esvaziar o pipe (saida grande)
})();
