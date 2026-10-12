// Harness: executa o JS real da pagina PMC com DOM e canvas simulados.
const fs = require('fs'), vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const results = {};

function fakeG() {
  const c = {lineTo: 0, moveTo: 0, fill: 0, stroke: 0, texts: []};
  const g = {};
  ['beginPath','closePath','arc','setLineDash','fillRect','clearRect','save','restore','rect','clip','strokeRect']
    .forEach(k => { g[k] = () => {}; });
  g.moveTo = () => { c.moveTo++; };
  g.lineTo = () => { c.lineTo++; };
  g.stroke = () => { c.stroke++; };
  g.fill = () => { c.fill++; };
  g.fillText = (s) => { c.texts.push(String(s)); };
  g.measureText = () => ({width: 10});
  return {g, c};
}

function makeEnv(state) {
  const els = {};
  const el = (id) => {
    if (!els[id]) els[id] = {id, innerHTML: '', value: '', checked: false,
                              options: [], dataset: {}, style: {}, onclick: null, onchange: null};
    return els[id];
  };
  el('homeoVista').value = 'todo';
  el('homeoMod').value = '';
  el('homeoMods').checked = true;
  el('chHomeo');
  el('lgHomeo'); el('homeoAnalise'); el('subHomeo'); el('homeoKpis');
  el('hmHead'); el('hmBody');
  const noDataCalls = [];
  const canvas = {c: null};
  const sandbox = {
    console, Math, Infinity, isFinite, Object, Array, Number, String, JSON, Date, RegExp,
    document: {
      getElementById: (id) => el(id),
      querySelectorAll: () => [],
      querySelector: () => null,
    },
    ctx: (id, h) => { const f = fakeG(); canvas.c = f.c; return {g: f.g, W: 600, H: h || 300, _c: f.c}; },
    noData: (g, W, H, msg) => { noDataCalls.push(msg || ''); },
    hexRgba: (c, a) => c,
    registarTip: () => {},
    linhaTip: (cor, lab, v) => `<div>${lab}:${v}</div>`,
    D: state.D,
    setTimeout, clearTimeout,
    window: {addEventListener: () => {}, removeEventListener: () => {}, location: {search: ""}},
    fetch: () => new Promise(() => {}),
    localStorage: {getItem: () => null, setItem: () => {}},
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  vm.runInContext('D = ' + JSON.stringify(state.D) + ';', sandbox);
  return {sandbox, el, noDataCalls, canvas};
}

function serie(n, base) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const d = new Date(Date.UTC(2025, 0, 1 + i)).toISOString().slice(0, 10);
    out.push({date: d, p_hat: base + i % 7, p_hat_suave: base + (i % 5),
              banda_sup: base + 10, banda_inf: base - 10});
  }
  return out;
}

const VAL = {estado: 'valido', valido: true, motivo: 'ajuste valido (R² 0.0042)',
             capacidade: 'muito baixa (explica menos de 10% da variacao do CP)'};
const VAL_OK = {estado: 'valido', valido: true, motivo: 'ajuste valido (R² 0.43)', capacidade: null};
const REJ = {estado: 'rejeitado', valido: false, motivo: 'k_negativo'};
const PAD = {estado: 'padrao', valido: false, motivo: 'poucos_pontos_cp'};

function estado(ajustado, r2, validacao, extra) {
  return Object.assign({ajustado, r2, k1: 1.475, k2: 0.805, t1: 23, t2: 12,
                        n_testes: 985, p0: 122.3, motivo: ajustado ? 'ok' : 'k_negativo',
                        nota: 'nota', serie: serie(60, 120), validacao}, extra || {});
}

function caso(nome, H, HM, vista, verMods, modSel) {
  const env = makeEnv({D: {homeostatico: H, homeostatico_mod: HM, cores: {Bike: '#00f'}}});
  env.el('homeoVista').value = vista || 'todo';
  env.el('homeoMods').checked = verMods !== false;
  env.el('homeoMod').value = modSel || '';
  return env;
}

// 1) ajuste valido (R2 baixo, mas aceite pelo modelo): desenha, sem aviso, R2 visivel
{
  const env = caso('valido', estado(true, 0.0042, VAL), {Bike: estado(true, 0.3, VAL)}, 'todo', true);
  env.sandbox.drawHomeo();
  results.valido_desenha = env.canvas.c.lineTo > 0;
  results.valido_sem_aviso = env.noDataCalls.length === 0;
  results.valido_legenda_bike = env.el('lgHomeo').innerHTML.includes('Bike');
}
// 2) ajuste rejeitado (global): nao desenha previsao, mostra aviso com o motivo
{
  const env = caso('rejeitado', estado(false, 0, REJ), {}, 'todo', true);
  env.sandbox.drawHomeo();
  results.rejeitado_sem_linhas = env.canvas.c.lineTo === 0 && env.canvas.c.fill === 0;
  results.rejeitado_aviso = env.noDataCalls.some(m => m.startsWith('Sem ajuste valido') && m.includes('k_negativo'));
  results.rejeitado_limpa_legenda = env.el('lgHomeo').innerHTML === '';
}
// 3) parametros padrao: mesmo comportamento
{
  const env = caso('padrao', estado(false, 0, PAD), {}, 'todo', true);
  env.sandbox.drawHomeo();
  results.padrao_sem_linhas = env.canvas.c.lineTo === 0;
  results.padrao_aviso = env.noDataCalls.some(m => m.startsWith('Sem ajuste valido'));
}
// 4) modalidade rejeitada nao sobrepoe o grafico nem a legenda
{
  const env = caso('mods', estado(true, 0.43, VAL), {Bike: estado(false, 0, REJ)}, 'todo', true);
  env.sandbox.drawHomeo();
  results.mod_rejeitada_fora_da_legenda = !env.el('lgHomeo').innerHTML.includes('Bike');
}
// 5) cartoes K/T nao mostram parametros de um ajuste rejeitado
{
  const env = caso('kpi', estado(false, 0, REJ), {}, 'todo', true);
  env.sandbox.mostrarHomeo();
  const h = env.el('homeoKpis').innerHTML;
  results.kpi_sem_k1_numero = !h.includes('1.475') && !h.includes('0.805');
  results.subtitulo_aviso = env.el('subHomeo').innerHTML.includes('Sem ajuste valido');
}
// 6) cartoes com ajuste valido mostram os parametros e o R2
{
  const env = caso('kpi_ok', estado(true, 0.43, VAL), {}, 'todo', true);
  env.sandbox.mostrarHomeo();
  results.kpi_valido_mostra_k1 = env.el('homeoKpis').innerHTML.includes('1.475');
  results.subtitulo_valido_sem_aviso = !env.el('subHomeo').innerHTML.includes('Sem ajuste valido');
}
// 7) tabela: rejeitado nunca aparece como ajustado; valido mostra R2
{
  const env = caso('tab', estado(false, 0, REJ), {Bike: estado(false, 0, REJ)}, 'todo', true);
  env.sandbox.tabelaHomeo();
  const t = env.el('hmBody').innerHTML;
  results.tabela_rejeitado_nao_ajustado = !t.includes('ajustado (R');
  results.tabela_rejeitado_rotulo = t.includes('K&#8322; sairia negativo');
  results.tabela_sem_cor_verde = !t.includes('#2ECC71');
  const v = caso('tabv', estado(true, 0.43, VAL_OK), {}, 'todo', true);
  v.sandbox.tabelaHomeo();
  results.tabela_valida_verde = v.el('hmBody').innerHTML.includes('#2ECC71') &&
                                v.el('hmBody').innerHTML.includes('ajustado (R²');
}
// 9) capacidade explicativa: ajuste valido com R2 0,004 mostra o aviso; com R2 0,43 nao
{
  const env = caso('cap', estado(true, 0.0042, VAL), {}, 'todo', true);
  env.sandbox.mostrarHomeo();
  const sub = env.el('subHomeo').innerHTML;
  results.cap_baixa_subtitulo = sub.includes('Capacidade explicativa muito baixa') && !sub.includes('Sem ajuste valido');
  results.cap_baixa_kpi_rotulo = env.el('homeoKpis').innerHTML.includes('R² (muito baixo)');
  results.cap_baixa_motivo = env.sandbox.motivoHomeo(estado(true, 0.0042, VAL)).includes('capacidade explicativa muito baixa');
  env.sandbox.drawHomeo();
  results.cap_baixa_continua_desenhando = env.canvas.c.lineTo > 0;
  const ok = caso('cap_ok', estado(true, 0.43, VAL_OK), {}, 'todo', true);
  ok.sandbox.mostrarHomeo();
  results.cap_ok_sem_rotulo = !ok.el('subHomeo').innerHTML.includes('Capacidade explicativa') &&
                              !ok.el('homeoKpis').innerHTML.includes('muito baixo') &&
                              !ok.sandbox.motivoHomeo(estado(true, 0.43, VAL_OK)).includes('capacidade');
}
// 10) sem dados (global ausente): nao desenha, indica indisponivel, nao quebra
{
  const env = caso('sem', null, {}, 'todo', true);
  env.sandbox.drawHomeo();
  results.sem_dados_sem_linhas = env.canvas.c.lineTo === 0;
  results.sem_dados_aviso = env.noDataCalls.length > 0;
  env.sandbox.mostrarHomeo();
  results.sem_dados_subtitulo = env.el('subHomeo').innerHTML.includes('indisponivel');
}
// 8) motivo sem validacao (payload antigo) nunca vira 'ajustado'
{
  const env = makeEnv({D: {}});
  const m = env.sandbox.motivoHomeo({ajustado: true, motivo: 'ok', r2: 0.4});
  results.sem_validacao_nao_ajustado = !m.startsWith('ajustado');
}
console.log(JSON.stringify(results, null, 1));
const falhas = Object.entries(results).filter(([k, v]) => v !== true).map(([k]) => k);
if (falhas.length) { console.error('FALHAS: ' + falhas.join(', ')); process.exit(1); }
