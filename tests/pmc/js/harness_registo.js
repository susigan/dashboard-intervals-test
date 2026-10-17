// Harness: executa o JS real do bloco "Registo de testes de performance" (entre os
// marcadores <<registo-testes>>) num contexto vm com DOM e fetch simulados.
// Sem rede. Uso: node harness_registo.js <pmc_js.js>. Imprime JSON {cenario: resultado}.
setTimeout(() => { console.log(JSON.stringify({watchdog: 'sem fim apos 20 s'})); process.exit(3); }, 20000).unref();
const fs = require('fs'), vm = require('vm');
const todo = fs.readFileSync(process.argv[2], 'utf8');
const i0 = todo.indexOf('// <<registo-testes>>'), i1 = todo.indexOf('// <</registo-testes>>');
if (i0 < 0 || i1 < i0) { console.log(JSON.stringify({erro: 'marcadores nao encontrados'})); process.exit(2); }
const bloco = todo.slice(i0, i1);

const cand = (o) => Object.assign({
  test_id: 'Bike:1200:i1', modalidade: 'Bike', duracao_s: 1200, activity_id: 'i1', data: '2026-09-01',
  watts: 300.4, pct_do_melhor: 98.5, melhor_recente: 305, elegibilidade: 'elegivel', motivo_elegibilidade: null,
  estado_registo: 'pendente', revisao: null, confirmavel: true,
  requer_aceitar_elegibilidade_desconhecida: false, motivo_nao_confirmavel: null, aviso_elegibilidade: null}, o);
const PAYLOAD = {status: 'OK', persistencia: true, n: 5, candidatos: [
  cand({test_id: 'Bike:1200:i1', activity_id: 'i1', data: '2026-09-01'}),
  cand({test_id: 'Bike:1200:i2', activity_id: 'i2', data: '2026-09-08', elegibilidade: 'desconhecida',
        motivo_elegibilidade: 'campo_ausente', requer_aceitar_elegibilidade_desconhecida: true,
        aviso_elegibilidade: 'elegibilidade desconhecida'}),
  cand({test_id: 'Bike:1200:i3', activity_id: 'i3', data: '2026-09-15', elegibilidade: 'inelegivel',
        motivo_elegibilidade: 'icu_ignore_power', confirmavel: false,
        motivo_nao_confirmavel: 'icu_ignore_power=true: so pode ser rejeitado'}),
  cand({test_id: 'Row:300:i4', modalidade: 'Row', duracao_s: 300, activity_id: 'i4', data: '2026-09-20',
        estado_registo: 'confirmado', revisao: 1}),
  cand({test_id: 'Row:300:<b>x</b>', modalidade: 'Row', duracao_s: 300, activity_id: '<img src=x onerror=alert(1)>',
        data: '2026-09-21', estado_registo: 'rejeitado', revisao: 1}),
]};

async function cenario(nome, fn) {
  const els = {}, chamadas = [];
  const el = (id) => {
    if (!els[id]) { let h = ''; const o = {value: '', checked: false};
      Object.defineProperty(o, 'innerHTML', {get: () => h, set: v => { h = v; }}); els[id] = o; }
    return els[id];
  };
  let respostaPost = {status: 'OK', alterado: true};
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
    'regFiltro, carregarRegisto, regRender, get REG(){return REG;}, get MSG(){return REG_MSG;}};', ctx);
  const api = ctx.__api;
  const corpo = () => els.registoTestesCorpo ? els.registoTestesCorpo.innerHTML : '';
  const set = (o) => { if ('post' in o) respostaPost = o.post; if ('get' in o) respostaGet = o.get;
                       if ('falhaGet' in o) falhaGet = o.falhaGet; if ('falhaPost' in o) falhaPost = o.falhaPost; };
  return {nome, r: await fn({api, el, corpo, chamadas, set, els})};
}
(async () => {
  const out = {};
  const run = async (n, f) => { const x = await cenario(n, f); out[x.nome] = x.r; };

  await run('definir_nao_pede_nada', async ({chamadas}) => ({chamadas: chamadas.length}));
  await run('fechado_nao_pede', async ({api, chamadas}) => { api.abrirRegisto({open: false}); return {chamadas: chamadas.length}; });
  await run('abrir_pede_so_get_uma_vez', async ({api, chamadas, corpo}) => {
    api.abrirRegisto({open: true}); await new Promise(r => setTimeout(r, 10));
    api.abrirRegisto({open: true}); await new Promise(r => setTimeout(r, 10));
    return {chamadas: chamadas.map(c => c.metodo + ' ' + c.url), html: corpo()};
  });
  await run('html', async ({api, corpo}) => { await api.carregarRegisto(); return {html: corpo()}; });
  await run('sem_motivo_nao_envia', async ({api, chamadas, el}) => {
    await api.carregarRegisto(); api.regAbrir(0, 'confirmar'); el('regMotivo').value = '   ';
    await api.regEnviar(); return {posts: chamadas.filter(c => c.metodo === 'POST').length, msg: api.MSG};
  });
  await run('cancelar_nao_envia', async ({api, chamadas}) => {
    await api.carregarRegisto(); api.regAbrir(0, 'confirmar'); api.regCancelar();
    return {posts: chamadas.filter(c => c.metodo === 'POST').length};
  });
  await run('confirmar_envia_um_post_explicito', async ({api, chamadas, el}) => {
    await api.carregarRegisto(); api.regAbrir(0, 'confirmar'); el('regMotivo').value = 'teste padronizado';
    await api.regEnviar();
    return {posts: chamadas.filter(c => c.metodo === 'POST'), gets: chamadas.filter(c => c.metodo === 'GET').length, msg: api.MSG};
  });
  await run('desconhecida_envia_opt_in_so_se_marcado', async ({api, chamadas, el, corpo}) => {
    await api.carregarRegisto(); api.regAbrir(1, 'confirmar');
    const form = corpo();
    el('regMotivo').value = 'confirmo'; el('regOptin').checked = true; await api.regEnviar();
    return {form_tem_checkbox: /regOptin/.test(form), post: chamadas.filter(c => c.metodo === 'POST')[0].corpo};
  });
  await run('elegivel_sem_checkbox', async ({api, corpo}) => {
    await api.carregarRegisto(); api.regAbrir(0, 'confirmar'); return {tem: /regOptin/.test(corpo())};
  });
  await run('erro_do_servidor_e_mostrado', async ({api, chamadas, el, set}) => {
    await api.carregarRegisto(); set({post: {status: 'erro', mensagem: 'icu_ignore_power=true: so pode ser rejeitado'}});
    api.regAbrir(2, 'rejeitar'); el('regMotivo').value = 'x'; await api.regEnviar(); return {msg: api.MSG};
  });
  await run('falha_de_rede_no_post', async ({api, el, set}) => {
    await api.carregarRegisto(); set({falhaPost: true});
    api.regAbrir(0, 'confirmar'); el('regMotivo').value = 'x'; await api.regEnviar(); return {msg: api.MSG};
  });
  await run('falha_de_rede_no_get', async ({api, corpo, set}) => {
    set({falhaGet: true}); await api.carregarRegisto(); return {html: corpo()};
  });
  await run('payloads_estranhos', async ({api}) => ({
    nulo: api.htmlRegisto(null), erro: api.htmlRegisto({status: 'erro'}),
    sem_bd: api.htmlRegisto({status: 'OK', persistencia: false, candidatos: []}),
    vazio: api.htmlRegisto({status: 'OK', persistencia: true, candidatos: []})}));
  await run('filtros', async ({api, corpo}) => {
    await api.carregarRegisto(); api.regFiltro('mod', 'Row'); const row = corpo();
    api.regFiltro('mod', ''); api.regFiltro('est', 'confirmado'); const conf = corpo();
    return {row_tem_bike: /Bike 1200 s/.test(row), row_tem_row: /Row 300 s/.test(row), conf_linhas: (conf.match(/<tr><td>20/g) || []).length};
  });
  await run('revisao_mmp', async ({api, chamadas, el, corpo, set}) => {
    const P = {status: 'OK', persistencia: true, n: 3,
      resumo_pendencias: {pendentes: 2, pendentes_sem_decisao: 1, pendentes_valor_atualizado: 1},
      candidatos: [
        cand({test_id: 'Bike:1200:i1', activity_id: 'i1', data: '2026-09-01', watts: 225,
              estado_registo: 'pendente', revisao: 1, valor_atualizado: true,
              estado_ultima_revisao: 'confirmado',
              decisao_anterior: {revisao: 1, estado: 'confirmado', watts: 220},
              elegibilidade_estimador: 'elegivel', motivo_elegibilidade_estimador: null,
              elegivel_para_estimador: true}),
        cand({test_id: 'Bike:1200:i2', activity_id: 'i2', data: '2026-10-05', watts: 300,
              elegibilidade_estimador: 'desconhecida',
              motivo_elegibilidade_estimador: 'ignore_parts_nao_associavel_ao_trecho',
              elegivel_para_estimador: false}),
        cand({test_id: 'Row:300:i4', modalidade: 'Row', duracao_s: 300, activity_id: 'i4',
              data: '2026-09-20', estado_registo: 'confirmado', revisao: 1,
              elegibilidade_estimador: 'elegivel', elegivel_para_estimador: true})]};
    set({get: P}); await api.carregarRegisto();
    const html = corpo();
    api.regAbrir(0, 'confirmar'); const form0 = corpo();
    el('regMotivo').value = 'novo valor revisto'; await api.regEnviar();
    const post = chamadas.filter(c => c.metodo === 'POST')[0];
    set({get: P}); await api.carregarRegisto();
    api.regAbrir(1, 'confirmar'); const form1 = corpo(); api.regCancelar();
    api.regFiltro('de', '2026-10-01'); const dep = corpo();
    api.regFiltro('de', ''); api.regFiltro('ate', '2026-09-10'); const ate = corpo();
    api.regFiltro('ate', ''); api.regFiltro('est', 'pendente'); const pend = corpo();
    return {html, form0, form1, post: post && post.corpo,
            dep_i1: /Bike 1200 s de 2026-09-01/.test(dep), dep_i2: /2026-10-05/.test(dep),
            ate_i2: /2026-10-05/.test(ate), ate_i1: /2026-09-01/.test(ate),
            pend_linhas: (pend.match(/<tr><td>20/g) || []).length};
  });
  await run('conflito_409_valor_mudou', async ({api, chamadas, el, set}) => {
    const novo = Object.assign({}, PAYLOAD, {candidatos: PAYLOAD.candidatos.map((c, k) => k === 0 ?
      Object.assign({}, c, {watts: 305, valor_atualizado: true,
        decisao_anterior: {revisao: 1, estado: 'confirmado', watts: 300.4}}) : c)});
    await api.carregarRegisto();
    set({post: {status: 'erro', campo: 'watts_vistos', mensagem: 'o valor mudou'}, get: novo});
    api.regAbrir(0, 'confirmar'); el('regMotivo').value = 'x'; await api.regEnviar();
    return {msg: api.MSG, gets: chamadas.filter(c => c.metodo === 'GET').length,
            posts: chamadas.filter(c => c.metodo === 'POST').length, watts: api.REG.candidatos[0].watts};
  });
  await run('ignore_parts_texto', async ({api, el, corpo, set}) => {
    const P = {status: 'OK', persistencia: true, n: 2, candidatos: [
      cand({test_id: 'Bike:1200:i1', activity_id: 'i1', elegibilidade_estimador: 'desconhecida',
            motivo_elegibilidade_estimador: 'ignore_parts_nao_associavel_ao_trecho', elegivel_para_estimador: false}),
      cand({test_id: 'Bike:1200:i2', activity_id: 'i2', elegibilidade_estimador: 'inelegivel',
            motivo_elegibilidade_estimador: 'icu_ignore_power=true', elegivel_para_estimador: false})]};
    set({get: P}); await api.carregarRegisto();
    const lista = corpo();
    api.regAbrir(0, 'confirmar'); const formIp = corpo(); api.regCancelar();
    api.regAbrir(1, 'confirmar'); const formOutro = corpo(); api.regCancelar();
    return {lista, formIp, formOutro};
  });
  console.log(JSON.stringify(out));
})();
