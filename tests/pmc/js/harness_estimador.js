// Executa o JS real do bloco <<estimador-experimental>> com DOM e fetch simulados. Sem rede.
setTimeout(() => { console.log(JSON.stringify({watchdog: 1})); process.exit(3); }, 20000).unref();
const fs = require('fs'), vm = require('vm');
const todo = fs.readFileSync(process.argv[2], 'utf8');
const i0 = todo.indexOf('// <<estimador-experimental>>'), i1 = todo.indexOf('// <</estimador-experimental>>');
if (i0 < 0 || i1 < i0) { console.log(JSON.stringify({erro: 'marcadores'})); process.exit(2); }
const bloco = todo.slice(i0, i1);
const item = (o) => Object.assign({modalidade: 'Bike', duracao_s: 1200, estado_calibracao: 'nao_calibrado',
  n_confirmados: 0, n_usadas: 0, n_excluidas: 0, nivel_w: null, incerteza: null,
  motivo_sem_incerteza: 'sem_observacoes', ultima_observacao: null, motivos_exclusao: {}}, o);
const PAYLOAD = {status: 'OK', itens: [
  item({}),
  item({duracao_s: 300, estado_calibracao: 'experimental', n_confirmados: 3, n_usadas: 2, n_excluidas: 1,
        nivel_w: 310.26, incerteza: {sd_w: 5.83, ic95_w: [291.7, 328.6]}, ultima_observacao: '2026-02-10',
        motivos_exclusao: {'elegibilidade_desconhecida:icu_ignore_power_ausente': 1}}),
  item({modalidade: '<img src=x onerror=alert(1)>', estado_calibracao: 'estimativa_unica', n_usadas: 1,
        nivel_w: 250, motivo_sem_incerteza: 'incerteza_nao_estimavel_com_1_observacao'})]};
async function cen(resp, falha) {
  const els = {}, chamadas = [];
  const el = (id) => { if (!els[id]) { let h = ''; const o = {};
    Object.defineProperty(o, 'innerHTML', {get: () => h, set: v => { h = v; }}); els[id] = o; } return els[id]; };
  const fetch = (url, opts) => { chamadas.push({url, metodo: (opts && opts.method) || 'GET'});
    return falha ? Promise.reject(new Error('rede')) : Promise.resolve({json: () => Promise.resolve(resp)}); };
  const ctx = {document: {getElementById: el}, fetch, Array, Object, String, Promise, JSON, Math, console: {error() {}, log() {}}};
  vm.createContext(ctx);
  vm.runInContext(bloco + '\n;globalThis.__a={abrirEstimador,htmlEstimador};', ctx);
  return {ctx, el, chamadas, els};
}
(async () => {
  const out = {};
  let c = await cen(PAYLOAD);
  out.sem_pedido_ao_definir = c.chamadas.length;
  c.ctx.__a.abrirEstimador({open: false}); await new Promise(r => setTimeout(r, 5));
  out.fechado_nao_pede = c.chamadas.length;
  c.ctx.__a.abrirEstimador({open: true}); await new Promise(r => setTimeout(r, 5));
  c.ctx.__a.abrirEstimador({open: true}); await new Promise(r => setTimeout(r, 5));
  out.pedidos = c.chamadas.map(x => x.metodo + ' ' + x.url);
  out.html = c.el('estimadorExpCorpo').innerHTML;
  c = await cen({status: 'erro', mensagem: 'historico ambiguo'});
  c.ctx.__a.abrirEstimador({open: true}); await new Promise(r => setTimeout(r, 5));
  out.erro = c.el('estimadorExpCorpo').innerHTML;
  c = await cen(null, true);
  c.ctx.__a.abrirEstimador({open: true}); await new Promise(r => setTimeout(r, 5));
  out.rede = c.el('estimadorExpCorpo').innerHTML;
  console.log(JSON.stringify(out));
})();
