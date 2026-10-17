// Harness: executa o JS real da pagina PMC (load() + carregarSugestoes/mostrarSugestoes)
// num contexto vm com DOM e fetch simulados. Sem rede. Uso: node harness_sugestoes.js <pmc_js.js>
// Imprime JSON {cenario: {instantaneos: [...], erros: [...]}}.
// So' os desenhos pesados sao neutralizados (nao tocam nas sugestoes); load() e as
// sugestoes sao as reais. Os desenhos neutralizados contam as chamadas (desenhos),
// para provar que load() chegou a eles.
// Watchdog proprio: um cenario que nunca termina (ex.: pedido preso a D) falha depressa
// com mensagem clara em vez de esperar pelo timeout do chamador.
setTimeout(() => { console.log(JSON.stringify({watchdog: 'cenario sem fim apos 30 s'})); process.exit(3); }, 30000).unref();
const fs = require('fs'), vm = require('vm');
const base = fs.readFileSync(process.argv[2], 'utf8');
let src = base;
for (const n of ['drawPMC', 'mostrarFMT5', 'mostrarRegimeHoje', 'mostrarHomeo', 'mostrarAlos',
                 'mostrarDtrimpDkj', 'mostrarEficienciaKj', 'redesenhar'])
  src = src.replace(new RegExp('\\bfunction ' + n + '\\('),
                    'function ' + n + '(){globalThis.__n_' + n + '=(globalThis.__n_' + n + '||0)+1;} function __x_' + n + '(');
src = src.replace('async function montarExport(', 'async function montarExport(){return;} async function __x_montarExport(');

const PMC_OK = {serie: [{date: '2026-10-01'}, {date: '2026-10-02'}], actual: {ctl: 50, atl: 40, tsb: 10, ramp: 2, estado: {label: 'ok'}},
                sessoes: [1, 2, 3], alertas: [], cores: {Bike: '#E74C3C'}};
const PROT_OK = {status: 'OK', cobertura: {por_modalidade: {Bike: {'20 min max': {n: 32}}}},
                 sugestoes: [{modalidade: 'Bike', secs: 1200, nome: '20 min max', mede: 'limiar', dias_desde_ultimo: 40,
                              atraso_dias: 12, ultimo_valor: 300, ultima_data: '2026-09-01', urgencia: 'media'}]};
const tick = () => new Promise(r => setTimeout(r, 5));

async function cenario(modoPmc, modoProt, passos, protPayload) {
  const els = {}, calls = {'/api/pmc': 0, '/api/protocolo': 0}, pend = [], erros = [], consoleErros = [];
  const el = (id) => {
    if (!els[id]) {
      let h = ''; const o = {writes: 0, textContent: '', style: {}, value: '', checked: false, options: [], dataset: {}};
      Object.defineProperty(o, 'innerHTML', {get: () => h, set: v => { h = v; o.writes++; }});
      els[id] = o;
    }
    return els[id];
  };
  const resp = (body, ruim) => ({json: () => ruim ? Promise.reject(new SyntaxError('Unexpected token <')) : Promise.resolve(body)});
  function fetch(url) {
    calls[url] = (calls[url] || 0) + 1;
    const modo = url === '/api/pmc' ? modoPmc : modoProt, ok = url === '/api/pmc' ? PMC_OK : (protPayload || PROT_OK);
    if (modo === 'falha') return Promise.reject(new Error('rede'));
    if (modo === 'json_invalido') return Promise.resolve(resp(null, true));
    if (modo === 'erro') return Promise.resolve(resp({error: 'sem actividades'}));
    if (modo === 'lento') return new Promise(r => pend.push(() => r(resp(ok))));
    return Promise.resolve(resp(ok));
  }
  const ctx = {document: {getElementById: el, createElement: () => ({}), querySelectorAll: () => []},
               fetch, window: {addEventListener() {}, devicePixelRatio: 1},
               Math, Object, Array, JSON, String, Number, Date, Promise, isFinite, setTimeout, parseFloat, parseInt,
               Set, Map, Float64Array, Error};
  ctx.console = {error: (...a) => consoleErros.push(a.map(String).join(' ')), log() {}, warn() {}};
  vm.createContext(ctx);
  const onRej = e => erros.push(String(e)); process.on('unhandledRejection', onRej);
  const snap = (rot) => {
    const h = (els.sugestoesTeste || {}).innerHTML || '';
    return {rot, pmc: calls['/api/pmc'], prot: calls['/api/protocolo'],
            kpis: !!(els.kpis && els.kpis.innerHTML), sub: (els.sub || {}).textContent || (els.sub || {}).innerHTML || '',
            sug: !h ? 'vazio' : (/Todos os testes em dia/.test(h) ? 'em_dia' : 'cartoes'),
            cor: !h ? null : (h.includes('color:#E74C3C;font-weight:600') ? 'D' : (h.includes('color:#8b949e;font-weight:600') ? 'cinza' : '?')),
            desenhos: ctx.__n_drawPMC || 0,
            n_cartoes: (h.match(/font-weight:600/g) || []).length, writes: (els.sugestoesTeste || {}).writes || 0};
  };
  const out = [];
  try { vm.runInContext(src, ctx); } catch (e) { erros.push('init: ' + e.message); }
  await tick(); await tick();
  out.push(snap('inicial'));
  if (passos.includes('liberar')) { pend.splice(0).forEach(f => f()); await tick(); await tick(); out.push(snap('apos_liberar')); }
  if (passos.includes('recarregar')) { await ctx.load(); await tick(); out.push(snap('apos_load_repetido')); }
  process.off('unhandledRejection', onRej);
  return {instantaneos: out, erros, consoleErros};
}

(async () => {
  const R = {};
  R.sucesso = await cenario('ok', 'ok', ['recarregar']);
  R.pmc_falha_rede = await cenario('falha', 'ok', []);
  R.pmc_erro = await cenario('erro', 'ok', []);
  R.pmc_lento = await cenario('lento', 'ok', ['liberar']);
  R.protocolo_falha = await cenario('ok', 'falha', []);
  R.protocolo_json_invalido = await cenario('ok', 'json_invalido', []);
  R.protocolo_lento = await cenario('ok', 'lento', ['liberar']);
  // payload malformado (sugestoes nao e lista) + /api/pmc lento: /api/protocolo chega primeiro
  R.protocolo_malformado_pmc_lento = await cenario('lento', 'ok', ['liberar'], {status: 'OK', sugestoes: 'x', cobertura: {}});
  console.log(JSON.stringify(R));
})();
