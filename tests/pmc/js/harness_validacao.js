// Harness: executa o JS real do painel "Validacao preditiva exploratoria" (marcadores
// <<validacao-exploratoria>>, mais o bloco do estimador que fornece estEsc) num contexto vm
// com DOM e fetch simulados. A resposta do servidor NAO e escrita a mao: vem de um ficheiro
// JSON gerado pelo endpoint real (Flask + SQLite temporaria + dados sinteticos).
// Uso: node harness_validacao.js <pmc_js.js> <resposta_real.json>. Imprime JSON {cenario: resultado}.
setTimeout(() => { console.log(JSON.stringify({watchdog: 'sem fim apos 20 s'})); process.exit(3); }, 20000).unref();
const fs = require('fs'), vm = require('vm');
const todo = fs.readFileSync(process.argv[2], 'utf8');
const REAL = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const corta = (a, b) => { const i = todo.indexOf(a), j = todo.indexOf(b);
  if (i < 0 || j < i) { console.log(JSON.stringify({erro: 'marcadores nao encontrados: ' + a})); process.exit(2); }
  return todo.slice(i, j); };
const codigo = corta('// <<estimador-experimental>>', '// <</estimador-experimental>>') + '\n' +
               corta('// <<validacao-exploratoria>>', '// <</validacao-exploratoria>>');

async function cenario(nome, resposta, fn) {
  const els = {}, chamadas = [];
  let falha = false;
  const el = (id) => { if (!els[id]) { let h = ''; const o = {};
    Object.defineProperty(o, 'innerHTML', {get: () => h, set: v => { h = v; }}); els[id] = o; } return els[id]; };
  const fetch = (url) => { chamadas.push(url);
    return falha ? Promise.reject(new Error('rede')) : Promise.resolve({json: () => Promise.resolve(resposta)}); };
  const ctx = {document: {getElementById: el}, fetch, Array, Set, Math, JSON, String, Object, Number, Promise,
               console: {error() {}, log() {}}};
  vm.createContext(ctx);
  vm.runInContext(codigo + '\n;globalThis.__api = {htmlValidacao, abrirValidacao, carregarValidacao};', ctx);
  const corpo = () => els.validacaoExpCorpo ? els.validacaoExpCorpo.innerHTML : '';
  return [nome, await fn({api: ctx.__api, corpo, chamadas, falhar: (v) => { falha = v; }})];
}
(async () => {
  const out = {};
  const run = async (n, r, f) => { const [k, v] = await cenario(n, r, f); out[k] = v; };
  await run('definir_nao_pede_nada', REAL, async ({chamadas}) => ({chamadas: chamadas.length}));
  await run('fechado_nao_pede', REAL, async ({api, chamadas}) => { api.abrirValidacao({open: false}); return {chamadas: chamadas.length}; });
  await run('abrir_pede_uma_vez', REAL, async ({api, chamadas, corpo}) => {
    api.abrirValidacao({open: true}); await new Promise(r => setTimeout(r, 20));
    api.abrirValidacao({open: true}); await new Promise(r => setTimeout(r, 20));
    return {chamadas, html: corpo()}; });
  await run('erro_de_rede_permite_repetir', REAL, async ({api, chamadas, corpo, falhar}) => {
    falhar(true); api.abrirValidacao({open: true}); await new Promise(r => setTimeout(r, 20));
    const msg = corpo(); falhar(false);
    api.abrirValidacao({open: true}); await new Promise(r => setTimeout(r, 20));
    return {msg, chamadas: chamadas.length, depois: corpo()}; });
  await run('resposta_de_erro', {status: 'erro', mensagem: '<b>x</b>&"'}, async ({api}) =>
    ({html: api.htmlValidacao({status: 'erro', mensagem: '<b>x</b>&"'})}));
  const ma = JSON.parse(JSON.stringify(REAL));
  ma.modos.retrospectivo.itens[0].modalidade = '<script>alert(1)</script>';
  await run('escape', ma, async ({api}) => ({html: api.htmlValidacao(ma)}));
  await run('erro_do_servidor_permite_repetir', {status: 'erro', mensagem: 'falhou'}, async ({api, chamadas, corpo}) => {
    api.abrirValidacao({open: true}); await new Promise(r => setTimeout(r, 20));
    api.abrirValidacao({open: true}); await new Promise(r => setTimeout(r, 20));
    return {chamadas: chamadas.length, html: corpo()}; });
  const inc = JSON.parse(JSON.stringify(REAL));
  for (const k of ['n_alvos', 'n_datas_distintas', 'dias_cobertura', 'criterios', 'orientacao_dados', 'metodos', 'estado_suficiencia'])
    delete inc.modos.retrospectivo.itens[0][k];
  delete inc.modos.conhecido_na_epoca.itens[0].n_alvos;
  await run('item_incompleto', inc, async ({api}) => ({html: api.htmlValidacao(inc)}));
  const amb = JSON.parse(JSON.stringify(REAL));
  amb.modos.retrospectivo.itens[0].n_origens_m1_ordem_ambigua = 2;
  await run('m1_ambiguo', amb, async ({api}) => ({html: api.htmlValidacao(amb)}));
  await run('sem_modo_na_epoca', REAL, async ({api}) => {
    const r = JSON.parse(JSON.stringify(REAL)); delete r.modos.conhecido_na_epoca;
    return {html: api.htmlValidacao(r)}; });
  await run('sem_itens', REAL, async ({api}) => ({html: api.htmlValidacao({status: 'OK', modos: {}})}));
  await run('real', REAL, async ({api}) => ({html: api.htmlValidacao(REAL)}));
  console.log(JSON.stringify(out));
})();
