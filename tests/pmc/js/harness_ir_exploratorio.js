// Harness: executa as funcoes PURAS de renderizacao do bloco "IR EXPLORATORIO" (entre <<ir-exploratorio>>)
// num contexto vm. Sem DOM nem rede. Uso: node harness_ir_exploratorio.js <pmc_js.js> <casos.json>.
// Imprime JSON {caso: {tabela_diag, exclusoes}} para o teste Python verificar.
setTimeout(() => { console.log(JSON.stringify({watchdog: 'sem fim apos 20 s'})); process.exit(3); }, 20000).unref();
const fs = require('fs'), vm = require('vm');
const todo = fs.readFileSync(process.argv[2], 'utf8');
const CASOS = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const i0 = todo.indexOf('// <<ir-exploratorio>>'), i1 = todo.indexOf('// <</ir-exploratorio>>');
if (i0 < 0 || i1 < i0) { console.log(JSON.stringify({erro: 'marcadores nao encontrados'})); process.exit(2); }
const sandbox = {Object, Number, String, Array, Math, isFinite, JSON, console: {log() {}, error() {}}};
vm.createContext(sandbox);
vm.runInContext(todo.slice(i0, i1) + '\n;globalThis.__f = {irxDiagCel, irxExclHtml};', sandbox);
const out = {};
for (const [nome, caso] of Object.entries(CASOS)) {
  out[nome] = {diag: sandbox.__f.irxDiagCel(caso.item || {}), exclusoes: sandbox.__f.irxExclHtml(caso.j || {})};
}
console.log(JSON.stringify(out));
