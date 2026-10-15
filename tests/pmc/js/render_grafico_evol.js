// Renderiza os graficos da evolucao com canvas registrado (sem navegador).
// Uso: node render_grafico_evol.js <pmc_js> <payload.json> <modalidade> <janela>
// Imprime JSON: por canvas, contagem de arcos (pontos), linhas, textos desenhados.
const fs = require('fs'), vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const fixture = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const MOD = process.argv[4], JAN = process.argv[5];
const registro = {};
function canvasRec(id) {
  const r = registro[id] = {arcs: 0, lines: 0, strokes: 0, texts: []};
  return {g: {
    beginPath() {}, fill() {}, font: '', fillStyle: '', strokeStyle: '', lineWidth: 0,
    textAlign: '', textBaseline: '', clearRect() {}, scale() {},
    arc() { r.arcs++; }, moveTo() {}, lineTo() { r.lines++; }, stroke() { r.strokes++; },
    fillText(t) { r.texts.push(String(t)); },
    fillRect() {}, measureText(t) { return {width: String(t).length * 6}; },
    rect() {}, closePath() {}, setLineDash() {}, save() {}, restore() {},
  }, W: 600, H: 240};
}
const els = {};
function el(id) {
  if (!els[id]) els[id] = {id, innerHTML: '', textContent: '', value: '', checked: false,
    options: [], dataset: {}, style: {}, onclick: null, onchange: null, hidden: false};
  if (id.startsWith('ev_') && !els[id].getContext) {
    els[id].clientWidth = 600;
    els[id].getContext = () => canvasRec(id).g;
  }
  return els[id];
}
const FONTE = process.argv[6] || 'todas';  // opcional: todas | eftp_atividade | ftp_configurado | eftp_nao_confirmado
el('evolMod').value = MOD; el('evolJanela').value = JAN; el('evolFonte').value = FONTE;
const sandbox = {
  console, Math, Infinity, isFinite, Object, Array, Number, String, JSON, Date, RegExp,
  document: {
    getElementById: (id) => el(id), querySelectorAll: () => [], querySelector: () => null,
    body: {},
  },
  noData: (g, W, H, msg) => { g.fillText(msg); },
  getComputedStyle: () => ({backgroundColor: 'rgb(13, 17, 23)'}),
  setTimeout, clearTimeout,
  window: {addEventListener: () => {}, removeEventListener: () => {}, location: {search: ''}},
  fetch: () => new Promise(() => {}),
  localStorage: {getItem: () => null, setItem: () => {}},
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
vm.runInContext('D = ' + JSON.stringify({evolucao_performance: fixture}) + ';', sandbox);
// opcional: series ocultas pela legenda (ex.: "60,720")
const OCULTAS = (process.argv[7] || '').split(',').filter(Boolean);
OCULTAS.forEach(k => vm.runInContext(`EV_OCULTAS[${JSON.stringify(k)}] = true;`, sandbox));
vm.runInContext('mostrarEvolucao();', sandbox);
// normalizacao calculada pela propria pagina (sem recalcular no teste)
registro.__norm = JSON.parse(vm.runInContext(
  `(function () {
     const MODS = (D.evolucao_performance || {}).modalidades || {};
     const MS = (MODS[${JSON.stringify(MOD)}] || {}).melhores_esforcos || {};
     return JSON.stringify(EV_DUR.map(d => ({k: d.k, rot: d.rot, n: evSerieNorm(MS[d.k], ${JSON.stringify(JAN)})})));
   })()`, sandbox));
registro.__ultimo_norm = JSON.parse(vm.runInContext('JSON.stringify(EV_ULTIMO_NORM)', sandbox));
registro.__tooltip = JSON.parse(vm.runInContext(
  `(function () {
     const MODS = (D.evolucao_performance || {}).modalidades || {};
     const MS = (MODS[${JSON.stringify(MOD)}] || {}).melhores_esforcos || {};
     const s = evSerieNorm(MS['60'], ${JSON.stringify(JAN)});
     const aj = evAjusteDur(evObsAjuste(s));
     const p = s.pontos.find(q => q.delta !== null);
     return JSON.stringify(p ? evTextoPonto({rot: '1 min', p: p, med: s.med, aj: aj}) : '');
   })()`, sandbox));
registro.__auditoria = JSON.parse(vm.runInContext('JSON.stringify(EV_AUDIT_SRC ? evAuditoriaHtml(EV_AUDIT_SRC) : "")', sandbox));
registro.__rotulos = JSON.parse(vm.runInContext(
  `JSON.stringify({elevada_no_limite: evSensRotulo(1, 1, true), baixa_logo_abaixo: evSensRotulo(0.999, 1, true),
    elevada_acima: evSensRotulo(1.0000001, 1, true), dispersao_zero: evSensRotulo(0.5, 0, true),
    sem_banda: evSensRotulo(0.5, null, true), sem_influente: evSensRotulo(null, 1, false),
    sd_negativo: evSensRotulo(0.5, -2, true), razao_nula: evSensRotulo(null, 1, true)})`, sandbox));
registro.__tabela_eftp = vm.runInContext(
  `(function () {
     const M = ((D.evolucao_performance || {}).modalidades || {})[${JSON.stringify(MOD)}] || {};
     return typeof evTabelaEftp === 'function' ? evTabelaEftp((M.fontes || {}).eftp_atividade) : '';
   })()`, sandbox);
registro.__html = el('evolGraficos').innerHTML;  // texto das janelas (para testes de texto)
registro.__tabelas = {  // tabelas, avisos e populacao (para testes de texto e regressao)
  eftp: el('evolBody').innerHTML, melhores: el('evolMeBody').innerHTML, cfg: el('evolCfgBody').innerHTML,
  avisos: el('evolAvisos').innerHTML, populacao: el('evolPopulacao').textContent, sub: el('evolSub').innerHTML};
process.stdout.write(JSON.stringify(registro));
