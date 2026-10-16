// Executa a funcao filtrados() REAL da aba Atividades (string JS vinda do Python) num DOM falso.
// uso: node harness_filtros.js <entrada.json>   -> imprime {cenario: [ids]} em JSON.
const fs = require('fs'); const vm = require('vm');
const ent = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, value: '', textContent: '', innerHTML: '', disabled: false, dataset: {},
    style: {}, onclick: null, oninput: null, onchange: null, appendChild() {}, remove() {}, click() {} };
  return els[id];
}
const ctx = {
  console, setTimeout,
  URL: { createObjectURL() { return 'blob:x'; }, revokeObjectURL() {} },
  document: { getElementById: el, querySelectorAll: () => [], createElement: () => el('_a'), body: { appendChild() {} } },
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve(ent.resposta_api), headers: { get() { return null; } } }),
};
vm.createContext(ctx);
vm.runInContext(ent.js, ctx);
(async () => {
  await new Promise(r => setTimeout(r, 50));
  const out = {};
  for (const cen of ent.cenarios) {
    for (const k of ['search', 'typeFilter', 'tagFilter', 'dtDe', 'dtAte']) el(k).value = '';
    for (const [k, v] of Object.entries(cen.filtros)) el(k).value = v;
    out[cen.nome] = JSON.parse(vm.runInContext('JSON.stringify(filtrados().map(r => r.id))', ctx));
  }
  process.stdout.write(JSON.stringify(out));
})();
