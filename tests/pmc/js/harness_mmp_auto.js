// Harness: executa o JS real do bloco "Deteccao automatica de observacoes MMP" (entre os
// marcadores <<registo-testes>>) num contexto vm com DOM e fetch simulados.
// As RESPOSTAS DO SERVIDOR NAO SAO ESCRITAS A MAO: vem de um ficheiro JSON gerado pelo codigo
// real (tests/pmc/mmp_auto_payload.py: Flask test client + SQLite temporaria + dados sinteticos).
// Sem rede. Uso: node harness_mmp_auto.js <pmc_js.js> <respostas_reais.json>. Imprime JSON {cenario: resultado}.
setTimeout(() => { console.log(JSON.stringify({watchdog: 'sem fim apos 20 s'})); process.exit(3); }, 20000).unref();
const fs = require('fs'), vm = require('vm');
const todo = fs.readFileSync(process.argv[2], 'utf8');
const REAL = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const i0 = todo.indexOf('// <<registo-testes>>'), i1 = todo.indexOf('// <</registo-testes>>');
if (i0 < 0 || i1 < i0) { console.log(JSON.stringify({erro: 'marcadores nao encontrados'})); process.exit(2); }
const bloco = todo.slice(i0, i1);
const PAYLOAD = REAL.get;

async function cenario(nome, fn) {
  const els = {}, chamadas = [];
  const el = (id) => {
    if (!els[id]) { let h = ''; const o = {value: '', checked: false};
      Object.defineProperty(o, 'innerHTML', {get: () => h, set: v => { h = v; }}); els[id] = o; }
    return els[id];
  };
  let respostaPost = REAL.post_decisao_ok;
  let respostaGet = PAYLOAD, falhaGet = false, falhaPost = false;
  const fetch = (url, opts) => {
    chamadas.push({url, metodo: (opts && opts.method) || 'GET', corpo: opts && opts.body ? JSON.parse(opts.body) : null});
    const post = opts && opts.method === 'POST';
    if ((post && falhaPost) || (!post && falhaGet)) return Promise.reject(new Error('rede'));
    return Promise.resolve({json: () => Promise.resolve(post ? respostaPost : respostaGet)});
  };
  const ctx = {document: {getElementById: el}, fetch, Array, Set, Math, JSON, String, Object, Number, Promise, Date,
               console: {error() {}, log() {}}};
  vm.createContext(ctx);
  vm.runInContext(bloco + '\n;globalThis.__api = {abrirRegisto, htmlRegisto, regAbrir, regCancelar, regEnviar, ' +
    'regFiltro, carregarRegisto, regRender, regAtualizar, get REG(){return REG;}, get MSG(){return REG_MSG;}};', ctx);
  const api = ctx.__api;
  const corpo = () => els.registoTestesCorpo ? els.registoTestesCorpo.innerHTML : '';
  const set = (o) => { if ('post' in o) respostaPost = o.post; if ('get' in o) respostaGet = o.get;
                       if ('falhaGet' in o) falhaGet = o.falhaGet; if ('falhaPost' in o) falhaPost = o.falhaPost; };
  // indice (na lista do servidor) da observacao com este test_id
  const ix = (tid) => api.REG.observacoes.findIndex(o => o.test_id === tid);
  return {nome, r: await fn({api, el, corpo, chamadas, set, els, ix})};
}
// linha <tr> de uma observacao: a 1.a celula comeca por "<modalidade> <duracao> s" e a 2.a traz o id da atividade
const linhaDe = (html, md, aid) => {
  for (const parte of html.split('<tr><td>').slice(1)) {
    const l = '<tr><td>' + parte.split('</tr>')[0];
    if (parte.startsWith(md + ' s') && l.includes('>' + aid + '</div>')) return l;
  }
  return null;
};
(async () => {
  const out = {};
  const run = async (n, f) => { const x = await cenario(n, f); out[x.nome] = x.r; };
  const posts = (c) => c.filter(x => x.metodo === 'POST');
  const linhasPedidas = {sem_ref: ['Bike 1200', 'a1'], tags: ['Bike 1200', 'a4'], ignore_parts: ['Bike 1200', 'a5'],
    ignore_power: ['Bike 1200', 'a6'], mesmo_dia: ['Bike 1200', 's1'], season: ['Bike 1200', 'b2'], excl_manual: ['Bike 1200', 'b3'],
    all_time: ['Bike 1200', 'b4'], recente: ['Row 300', 'r6']};

  await run('definir_nao_pede_nada', async ({chamadas}) => ({chamadas: chamadas.length}));
  await run('fechado_nao_pede', async ({api, chamadas}) => { api.abrirRegisto({open: false}); return {chamadas: chamadas.length}; });
  await run('abrir_pede_so_get_uma_vez', async ({api, chamadas, corpo}) => {
    api.abrirRegisto({open: true}); await new Promise(r => setTimeout(r, 10));
    api.abrirRegisto({open: true}); await new Promise(r => setTimeout(r, 10));
    return {chamadas: chamadas.map(c => c.metodo + ' ' + c.url), html: corpo()};
  });
  await run('html', async ({api, corpo}) => {
    await api.carregarRegisto(); const html = corpo();
    const linhas = {}; for (const [k, [md, a]] of Object.entries(linhasPedidas)) linhas[k] = linhaDe(html, md, a);
    return {html, linhas};
  });
  await run('html_parcial', async ({api, corpo, set}) => {
    set({get: REAL.get_parcial}); await api.carregarRegisto(); return {html: corpo()};
  });
  await run('excluir_sem_motivo_nao_envia', async ({api, chamadas, el, ix}) => {
    await api.carregarRegisto(); api.regAbrir(ix('Bike:1200:b2'), 'rejeitar'); el('regMotivo').value = '   ';
    await api.regEnviar(); return {posts: posts(chamadas).length, msg: api.MSG};
  });
  await run('cancelar_nao_envia', async ({api, chamadas, ix}) => {
    await api.carregarRegisto(); api.regAbrir(ix('Bike:1200:b2'), 'rejeitar'); api.regCancelar(); return {posts: posts(chamadas).length};
  });
  await run('excluir_envia_um_post_explicito', async ({api, chamadas, el, corpo, ix}) => {
    await api.carregarRegisto(); const i = ix('Bike:1200:b2'); api.regAbrir(i, 'rejeitar'); const form = corpo();
    el('regMotivo').value = 'esforco nao maximo'; await api.regEnviar();
    return {form, posts: posts(chamadas), gets: chamadas.filter(c => c.metodo === 'GET').length, msg: api.MSG,
            watts_servidor: api.REG.observacoes[i].watts};
  });
  await run('remover_exclusao_envia_confirmar', async ({api, chamadas, el, corpo, ix}) => {
    await api.carregarRegisto(); api.regAbrir(ix('Bike:1200:b3'), 'confirmar'); const form = corpo(); el('regMotivo').value = 'levantar';
    await api.regEnviar(); return {form, posts: posts(chamadas)};
  });
  await run('atualizar_deteccao', async ({api, chamadas, set}) => {
    await api.carregarRegisto();
    set({post: REAL.post_atualizar});
    await api.regAtualizar();
    return {chamadas: chamadas.map(c => c.metodo + ' ' + c.url), msg: api.MSG};
  });
  await run('erro_do_servidor_e_mostrado', async ({api, el, set, ix}) => {
    await api.carregarRegisto(); set({post: REAL.post_erro});
    api.regAbrir(ix('Bike:1200:b4'), 'rejeitar'); el('regMotivo').value = 'x'; await api.regEnviar(); return {msg: api.MSG};
  });
  await run('falha_de_rede_no_post', async ({api, el, set, ix}) => {
    await api.carregarRegisto(); set({falhaPost: true});
    api.regAbrir(ix('Bike:1200:b2'), 'rejeitar'); el('regMotivo').value = 'x'; await api.regEnviar(); return {msg: api.MSG};
  });
  await run('falha_de_rede_no_get', async ({api, corpo, set}) => {
    set({falhaGet: true}); await api.carregarRegisto(); return {html: corpo()};
  });
  await run('payloads_estranhos', async ({api}) => ({
    nulo: api.htmlRegisto(null), erro: api.htmlRegisto({status: 'erro'}),
    vazio: api.htmlRegisto({status: 'OK', observacoes: []}),
    sem_lista: api.htmlRegisto({status: 'OK'})}));
  await run('filtros', async ({api, corpo}) => {
    await api.carregarRegisto();
    const n = (h) => (h.match(/<tr><td>(Bike|Row|Ski|Run) /g) || []).length;
    const todas = n(corpo());
    api.regFiltro('mod', 'Row'); const row = corpo();
    api.regFiltro('mod', ''); api.regFiltro('inc', 'nao'); const fora = corpo();
    api.regFiltro('inc', 'sim'); const dentro = corpo();
    api.regFiltro('inc', ''); api.regFiltro('ref', 'all_time'); const at = corpo();
    api.regFiltro('ref', 'season'); const se = corpo();
    return {todas, row_linhas: n(row), row_bike: /Bike \d+ s/.test(row), row_row: /Row \d+ s/.test(row),
            fora_linhas: n(fora), dentro_linhas: n(dentro), at_linhas: n(at), season_linhas: n(se),
            total_servidor: api.REG.observacoes.length,
            incluidas_servidor: api.REG.observacoes.filter(o => o.incluida_no_estimador).length,
            all_time_servidor: api.REG.observacoes.filter(o => o.referencia_tipo === 'all_time').length,
            season_servidor: api.REG.observacoes.filter(o => o.referencia_tipo === 'season').length,
            row_servidor: api.REG.observacoes.filter(o => o.modalidade === 'Row').length};
  });
  await run('conflito_409_valor_mudou', async ({api, el, set, chamadas, ix}) => {
    await api.carregarRegisto();
    set({post: REAL.post_409});
    api.regAbrir(ix('Bike:1200:b4'), 'rejeitar'); el('regMotivo').value = 'x'; await api.regEnviar();
    return {msg: api.MSG, gets: chamadas.filter(c => c.metodo === 'GET').length, posts: posts(chamadas).length};
  });
  console.log(JSON.stringify(out));
})();
