// Harness (Etapa 15): executa o JS REAL do bloco "IR SOMBRA" (entre <<ir-sombra>> e <</ir-sombra>> em
// tabs/tab_pmc.py) num contexto vm com DOM, canvas e fetch simulados. As respostas NAO sao escritas a mao:
// vem do endpoint real (tests/pmc/test_ir_painel_sombra.py, Flask test client sobre dados sinteticos).
// Sem rede. Nao escreve nada: so desenha.
// Uso: node harness_ir_sombra.js <tab_pmc.py> <cenarios.json>   -> imprime JSON {nome_cenario: resultado}
setTimeout(() => { console.log(JSON.stringify({watchdog: 'sem fim apos 20 s'})); process.exit(3); }, 20000).unref();
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const CEN = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const i0 = src.indexOf('// <<ir-sombra>>'), i1 = src.indexOf('// <</ir-sombra>>');
if (i0 < 0 || i1 < i0) { console.log(JSON.stringify({erro: 'marcadores nao encontrados'})); process.exit(2); }
const bloco = src.slice(i0, i1);

function novoCanvas(rec) {
  const g = {fillStyle: '', strokeStyle: '', lineWidth: 1};
  ['clearRect', 'beginPath', 'moveTo', 'lineTo'].forEach(k => { g[k] = () => { rec[k] = (rec[k] || 0) + 1; }; });
  g.stroke = () => { rec.stroke++; };
  g.fill = () => { rec.fill.push(g.fillStyle); };
  g.arc = () => { rec.arc.push(g.fillStyle); };
  g.fillText = (s) => { rec.texts.push(String(s)); };
  g.clearRect = () => { rec.clear++; };
  return {width: 640, height: 240, getContext: () => g};
}

async function correr(c) {
  const rec = {clear: 0, stroke: 0, fill: [], arc: [], texts: []};
  const canvas = novoCanvas(rec);
  const els = {};
  const mk = (id) => els[id] || (els[id] = {id, value: '', innerHTML: '', textContent: ''});
  const urls = [];
  let chamada = 0;
  const respostas = c.respostas || [];
  const sandbox = {
    console, Math, Date, Promise, setTimeout, Number, String, isFinite, encodeURIComponent, JSON, Array, Object,
    document: {
      getElementById(id) {
        if (id === 'somCanvas') {
          const s = els.somSaida;
          return (s && s.innerHTML.indexOf('id="somCanvas"') >= 0) ? canvas : null;
        }
        return mk(id);
      },
    },
    fetch(url) {
      urls.push(url);
      const r = respostas[Math.min(chamada, respostas.length - 1)] || {};
      chamada++;
      return new Promise((resolve, reject) => setTimeout(() => {
        if (r.falha) { reject(new Error('rede')); return; }
        resolve({json: () => Promise.resolve(r.corpo)});
      }, r.atraso || 0));
    },
  };
  if (c.ir_j !== undefined && c.ir_j !== null) sandbox.IR_J = c.ir_j;
  vm.createContext(sandbox);
  vm.runInContext(bloco, sandbox);
  const acoes = c.acoes || [];
  const driver = `(async () => {
    for (const a of __acoes) {
      if (a.tipo === 'abrir') abrirIRSombra({open: true});
      else if (a.tipo === 'selecionar') document.getElementById(a.campo).value = String(a.valor);
      else if (a.tipo === 'carregar') await somCarregar();
      else if (a.tipo === 'carregar_sem_aguardar') somCarregar();
      else if (a.tipo === 'mudar') somMudou();
      else if (a.tipo === 'prospetiva') prospCarregar();
      else if (a.tipo === 'esperar') await new Promise(r => setTimeout(r, a.ms));
    }
  })()`;
  sandbox.__acoes = acoes;
  await vm.runInContext(driver, sandbox);
  await new Promise(r => setTimeout(r, 120));   // deixa terminar pedidos ainda pendentes
  const s = els.somSaida || {innerHTML: ''};
  return {
    urls,
    saida_html: s.innerHTML,
    aviso: (els.somAviso || {}).textContent || '',
    shell_html: (els.irSombraCorpo || {}).innerHTML || '',
    prosp_html: (els.somProsp || {}).innerHTML || '',
    desenho: rec,
  };
}

(async () => {
  const out = {};
  for (const nome of Object.keys(CEN)) {
    try { out[nome] = await correr(CEN[nome]); }
    catch (e) { out[nome] = {erro: String(e && e.stack || e)}; }
  }
  console.log(JSON.stringify(out));
})();
