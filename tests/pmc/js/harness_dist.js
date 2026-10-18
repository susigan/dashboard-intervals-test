// Harness: executa o JS real do bloco "Distribuicao da carga" (entre <<distribuicao-carga>>) num contexto vm
// com DOM, canvas e fetch simulados. As respostas vem de tests/pmc/dist_payload.py (servidor real, dados
// sinteticos). Sem rede. Uso: node harness_dist.js <pmc_js.js> <respostas_reais.json>.
setTimeout(() => { console.log(JSON.stringify({watchdog: 'sem fim apos 20 s'})); process.exit(3); }, 20000).unref();
const fs = require('fs'), vm = require('vm');
const todo = fs.readFileSync(process.argv[2], 'utf8');
const REAL = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const i0 = todo.indexOf('// <<distribuicao-carga>>'), i1 = todo.indexOf('// <</distribuicao-carga>>');
if (i0 < 0 || i1 < i0) { console.log(JSON.stringify({erro: 'marcadores nao encontrados'})); process.exit(2); }
const bloco = todo.slice(i0, i1);
const U = (m, w, d) => `/api/carga/distribuicao?metodo=${m}&janela=${w}&dias=${d}`;
const TL = 'training_load', TR = 'trimp';

function fakeG(rec) {
  const g = {fillStyle: '#000', strokeStyle: '#000'};
  ['beginPath', 'closePath', 'setLineDash', 'clearRect', 'save', 'restore', 'rect', 'clip', 'strokeRect', 'fill', 'arc']
    .forEach(k => { g[k] = () => {}; });
  g.moveTo = () => { rec.moveTo++; }; g.lineTo = () => { rec.lineTo++; }; g.stroke = () => { rec.stroke.push(g.strokeStyle); };
  g.fillRect = (x, y, w, h) => { rec.rects.push([g.fillStyle, x, y, w, h, g.globalAlpha === undefined ? 1 : g.globalAlpha]); };
  g.fillText = (s) => { rec.texts.push(String(s)); };
  return g;
}

async function cenario(nome, fn) {
  const els = {}, chamadas = [], canvases = {}, tips = {}, semData = [];
  const el = (id) => {
    if (!els[id]) { let h = ''; const o = {value: '', onchange: null};
      if (id === 'dcMet') o.value = TL; if (id === 'dcJan') o.value = '28'; if (id === 'dcHoriz') o.value = '365';
      Object.defineProperty(o, 'innerHTML', {get: () => h, set: v => { h = v; }}); els[id] = o; }
    return els[id];
  };
  const respostas = {};
  const mapa = {[U(TL, 28, 365)]: REAL.tl28_365, [U(TL, 7, 365)]: REAL.tl7_365, [U(TL, 28, 90)]: REAL.tl28_90,
                [U(TR, 28, 365)]: REAL.tr28_365, [U(TR, 7, 365)]: REAL.tr7_365, [U(TR, 28, 90)]: REAL.tr28_90};
  let falha = false; const atrasos = {};
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
    ctx: (id, h) => { const rec = {rects: [], texts: [], moveTo: 0, lineTo: 0, stroke: []}; canvases[id] = rec; return {g: fakeG(rec), W: 600, H: h}; },
    noData: (g, W, H, msg) => { semData.push(msg || ''); },
    registarTip: (id, f) => { tips[id] = f; },
    linhaTip: (cor, nome, v) => `<div data-cor="${cor}">${nome}:${v}</div>`,
  };
  vm.createContext(sandbox);
  vm.runInContext(bloco + '\n;globalThis.__api = {abrirDistCarga, carregarDistCarga, dcShell, dcInfoHtml, dcNotasHtml, dcCompHtml, dcSeries, get J(){return DC_J;}};', sandbox);
  const esperar = (ms) => new Promise(r => setTimeout(r, ms || 15));
  return {nome, r: await fn({api: sandbox.__api, el, els, chamadas, canvases, tips, semData, falhar: (v) => { falha = v; },
                             atraso: (u, ms) => { atrasos[u] = ms; }, esperar, respostas})};
}
const xTip = (n, i) => 40 + (600 - 50) * (i + 0.5) / n;                     // coluna i do grafico empilhado (PL=40, PR=10)
const xDif = (n, i) => 40 + (600 - 50) * (n > 1 ? i / (n - 1) : 0.5);       // ponto i do grafico de diferencas
(async () => {
  const out = {};
  const run = async (n, f) => { const x = await cenario(n, f); out[x.nome] = x.r; };
  await run('definir_nao_pede_nada', async ({chamadas}) => ({chamadas: chamadas.length}));
  await run('fechado_nao_pede', async ({api, chamadas, esperar}) => { api.abrirDistCarga({open: false}); await esperar(); return {chamadas: chamadas.length}; });
  await run('abrir_pede_um_get_uma_vez', async ({api, chamadas, esperar}) => {
    api.abrirDistCarga({open: true}); await esperar(); api.abrirDistCarga({open: true}); await esperar();
    return {chamadas: chamadas.slice()};
  });
  await run('render_training_load', async ({api, els, canvases, tips, semData, esperar}) => {
    api.abrirDistCarga({open: true}); await esperar();
    const n = REAL.tl28_365.serie.datas.length;
    return {corpo: els.distCargaExpCorpo.innerHTML, info: els.dcInfo.innerHTML, notas: els.dcNotas.innerHTML, comp: els.dcComp.innerHTML,
            leg: els.lgDc.innerHTML, legDif: els.lgDcDif.innerHTML, rects: canvases.chDc.rects, texts: canvases.chDc.texts,
            dif: {linhas: canvases.chDcDif.lineTo, cores: canvases.chDcDif.stroke}, semData, n,
            tipUltimo: tips.chDc(xTip(n, n - 1)), tipFora: tips.chDc(2), tipDif: tips.chDcDif(xDif(n, n - 1))};
  });
  await run('render_trimp', async ({api, el, els, canvases, tips, semData, esperar}) => {
    api.abrirDistCarga({open: true}); await esperar();
    el('dcMet').value = TR; els.dcMet.onchange(); await esperar();
    const n = REAL.tr28_365.serie.datas.length;
    return {info: els.dcInfo.innerHTML, notas: els.dcNotas.innerHTML, comp: els.dcComp.innerHTML, leg: els.lgDc.innerHTML,
            rects: canvases.chDc.rects, semData, n, tipUltimo: tips.chDc(xTip(n, n - 1)), J: api.J.metodo};
  });
  await run('trocar_metodo_janela_e_periodo', async ({api, el, els, chamadas, esperar}) => {
    api.abrirDistCarga({open: true}); await esperar();
    el('dcMet').value = TR; els.dcMet.onchange(); await esperar();
    const a = chamadas.slice(); el('dcJan').value = '7'; els.dcJan.onchange(); await esperar();
    const b = chamadas.slice(); el('dcHoriz').value = '90'; el('dcJan').value = '28'; els.dcHoriz.onchange(); await esperar();
    return {chamadas: chamadas.slice(), a, b};
  });
  await run('estados_trimp_sem_treino_e_sem_trimp_valido', async ({api, el, els, canvases, tips, esperar}) => {
    api.abrirDistCarga({open: true}); await esperar();
    el('dcMet').value = TR; el('dcJan').value = '7'; els.dcMet.onchange(); await esperar();
    const S = REAL.tr7_365.serie, n = S.datas.length;
    const iSem = S.estado.indexOf('sem_treino'), iAus = S.estado.indexOf('carga_ausente'), iOkAus = S.estado.indexOf('ok_com_ausentes'), iOk = S.estado.indexOf('ok');
    return {iSem, iAus, iOkAus, iOk, tipSem: tips.chDc(xTip(n, iSem)), tipAus: tips.chDc(xTip(n, iAus)), tipOkAus: tips.chDc(xTip(n, iOkAus)),
            tipOk: iOk >= 0 ? tips.chDc(xTip(n, iOk)) : '', rects: canvases.chDc.rects, leg: els.lgDc.innerHTML, estados: [...new Set(S.estado)]};
  });
  await run('cobertura_incompleta_aviso', async ({api, el, els, esperar}) => {
    api.abrirDistCarga({open: true}); await esperar();
    el('dcMet').value = TR; els.dcMet.onchange(); await esperar();
    const trCob = els.dcInfo.innerHTML; el('dcMet').value = TL; els.dcMet.onchange(); await esperar();
    return {tr: trCob, tl: els.dcInfo.innerHTML};
  });
  await run('janela_incompleta', async ({api, els, canvases, semData, respostas, esperar}) => {
    respostas[U(TL, 28, 365)] = REAL.incompleta;
    api.abrirDistCarga({open: true}); await esperar();
    return {info: els.dcInfo.innerHTML, notas: els.dcNotas.innerHTML, comp: els.dcComp.innerHTML, semData, rects: canvases.chDc.rects.length};
  });
  await run('vazia', async ({api, els, respostas, semData, esperar}) => {
    respostas[U(TL, 28, 365)] = REAL.vazia; api.abrirDistCarga({open: true}); await esperar();
    return {info: els.dcInfo.innerHTML, comp: els.dcComp.innerHTML, semData};
  });
  await run('falha_de_rede', async ({api, els, falhar, semData, esperar}) => {
    falhar(true); api.abrirDistCarga({open: true}); await esperar();
    return {info: els.dcInfo.innerHTML, comp: els.dcComp.innerHTML, semData};
  });
  await run('resposta_antiga_nao_sobrepoe_a_nova', async ({api, el, els, atraso, esperar}) => {
    atraso(U(TL, 28, 365), 80);
    api.abrirDistCarga({open: true});
    await esperar(5); el('dcMet').value = TR; els.dcMet.onchange();
    await esperar(150);
    return {J: api.J.metodo};
  });
  await run('series', async ({api}) => {
    return {s: api.dcSeries(REAL.tl28_365), t: api.dcSeries(REAL.tr28_365), vazio: api.dcSeries(REAL.incompleta), nulo: api.dcSeries(null)};
  });
  process.stdout.write(JSON.stringify(out) + '\n', () => process.exit(0));
})();
