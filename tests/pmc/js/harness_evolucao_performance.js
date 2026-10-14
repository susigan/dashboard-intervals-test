// Harness: executa o JS real da pagina PMC com DOM simulado e renderiza a secao
// "Evolucao da performance" a partir de um payload (evolucao_performance gerado por
// pmc.evolucao_performance). Uso: node harness.js <pmc_js> <payload.json>
// Imprime um objeto JSON com checagens booleanas e textos renderizados.
const fs = require('fs'), vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const fixture = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

function fakeG() {
  const c = {lineTo: 0, moveTo: 0, fill: 0, stroke: 0};
  const g = {};
  ['beginPath','closePath','arc','setLineDash','fillRect','clearRect','save','restore','rect','clip','strokeRect','fillText']
    .forEach(k => { g[k] = () => {}; });
  g.moveTo = () => { c.moveTo++; };
  g.lineTo = () => { c.lineTo++; };
  g.stroke = () => { c.stroke++; };
  g.fill = () => { c.fill++; };
  g.measureText = () => ({width: 10});
  return {g, c};
}

const els = {};
function el(id) {
  if (!els[id]) els[id] = {id, innerHTML: '', textContent: '', value: '', checked: false,
                           options: [], dataset: {}, style: {}, onclick: null, onchange: null,
                           hidden: false};
  return els[id];
}
el('evolMod').value = 'Bike';
el('evolJanela').value = '180';
el('evolFonte').value = 'todas';

const sandbox = {
  console, Math, Infinity, isFinite, Object, Array, Number, String, JSON, Date, RegExp,
  document: {
    getElementById: (id) => el(id),
    querySelectorAll: () => [],
    querySelector: () => null,
  },
  ctx: () => { const f = fakeG(); return {g: f.g, W: 600, H: 300}; },
  noData: () => {},
  hexRgba: (c) => c,
  registarTip: () => {},
  linhaTip: (cor, lab, v) => `<div>${lab}:${v}</div>`,
  D: null,
  setTimeout, clearTimeout,
  window: {addEventListener: () => {}, removeEventListener: () => {}, location: {search: ''}},
  fetch: () => new Promise(() => {}),
  localStorage: {getItem: () => null, setItem: () => {}},
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
vm.runInContext('D = ' + JSON.stringify({evolucao_performance: fixture}) + ';', sandbox);

// D e declarado com let no script da pagina: so vm.runInContext o redefine
function setD(obj) { vm.runInContext('D = ' + JSON.stringify(obj) + ';', sandbox); }

const r = {};
const txt = (s) => String(s || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

// 1) cabecalho e linhas por fonte, janela 180
sandbox.mostrarEvolucao();
r.cabecalho = el('evolHead').innerHTML.includes('Mediana anterior') &&
              el('evolHead').innerHTML.includes('Estado');
r.linha_eftp_atividade = el('evolBody').innerHTML.includes('eFTP da atividade');
r.linha_ftp_configurado = el('evolBody').innerHTML.includes('FTP configurado');
r.linha_eftp_nao_confirmado = el('evolBody').innerHTML.includes('EFTP (campo não confirmado)');
r.sem_rotulo_ftp_medido = !(el('evolBody').innerHTML + el('evolSub').innerHTML).includes('FTP medido');
r.melhores_1_5_12_20 = ['1 min', '5 min', '12 min', '20 min']
  .every(t => el('evolMeBody').innerHTML.includes(t));
r.texto_referencia = el('evolSub').innerHTML.includes('Referência');
r.texto_nao_validado_nao_citado = !el('evolSub').innerHTML.includes('homeostatico');

// 2) dados insuficientes aparecem com o motivo, e nao com numero
el('evolFonte').value = 'eftp_atividade';
el('evolJanela').value = '365';
sandbox.mostrarEvolucao();
const body365 = el('evolBody').innerHTML;
r.insuficiente_explicito = body365.includes('Dados insuficientes');
// nas linhas insuficientes, as celulas de mediana/diferenca nao mostram decimais
const linhasIns = body365.split('<tr>').filter(x => x.includes('Dados insuficientes'));
r.insuficiente_sem_numero_na_linha = linhasIns.length > 0 &&
  linhasIns.every(l => !/\d+\.\d/.test(l.split('Dados insuficientes')[0]));
// 3) filtro de fonte: so eFTP
el('evolFonte').value = 'eftp_nao_confirmado';
el('evolJanela').value = '180';
sandbox.mostrarEvolucao();
r.filtro_fonte_eftp = el('evolBody').innerHTML.includes('EFTP (campo não confirmado)') &&
                      !el('evolBody').innerHTML.includes('eFTP da atividade') &&
                      !el('evolBody').innerHTML.includes('FTP configurado');
// 3b) sem classificacao automatica no conteudo gerado (tabela e avisos)
el('evolFonte').value = 'todas';
sandbox.mostrarEvolucao();
const gerado = (el('evolBody').innerHTML + el('evolMeBody').innerHTML + el('evolAvisos').innerHTML).toLowerCase();
r.sem_classificacao_automatica = !['queda','piora','melhora'].some(p => gerado.includes(p));
// 4) qualidade: zeros e sequencias explicitas, sem tratar zero como valor
el('evolFonte').value = 'todas';
sandbox.mostrarEvolucao();
const avisos = el('evolAvisos').innerHTML;
r.zeros_tratados_como_ausencia = avisos.includes('zero(s) tratados como ausência');
r.sequencia_sinalizada_mantida = avisos.includes('suspeita, origem não confirmada, mantida e sinalizada');
r.nao_exclui_sem_evidencia = !avisos.includes('excluído(s) da tendência');
r.outliers_sinalizados_nao_removidos = avisos.includes('extremo(s) sinalizado(s), não removidos');
// 4b) eFTP suspeito (1 W): sinalizado e mantido, nunca removido
el('evolFonte').value = 'eftp_nao_confirmado';
sandbox.mostrarEvolucao();
const avEf = el('evolAvisos').innerHTML;
r.eftp_suspeito_sinalizado = avEf.includes('suspeito(s)') && avEf.includes('mantido(s) e sinalizado(s)');
r.eftp_suspeito_nao_excluido = !avEf.includes('excluído(s) da tendência');
el('evolFonte').value = 'todas';
// 5) modalidade sem dados: mensagem explicita
el('evolMod').value = 'Ski';
sandbox.mostrarEvolucao();
r.modalidade_sem_dados_explicita = el('evolBody').innerHTML.includes('Dados insuficientes');
// 6) falha da secao: mensagem de indisponivel, sem exceção
setD({evolucao_performance: {status: 'erro', erro: 'falha simulada'}});
sandbox.mostrarEvolucao();
r.erro_mostra_indisponivel = el('evolSub').innerHTML.includes('Evolução indisponível') &&
                             el('evolSub').innerHTML.includes('falha simulada');
r.erro_sem_linhas = el('evolBody').innerHTML === '';

// 7) redesenhar nao chama o desenho homeostatico no fluxo principal
// Os demais desenhos viram no-op, para que redesenhar() chegue ao fim e a checagem
// nao passe por excecao engolida antes de chegar a drawHomeo.
let chamadasHomeo = 0, concluiu = false;
sandbox.drawHomeo = () => { chamadasHomeo++; };
['drawPMC','drawCTLg','drawFMT','drawMatriz','drawEigen','drawAtencao'].forEach(n => { sandbox[n] = () => {}; });
setD({homeostatico: {ajustado: true}, evolucao_performance: fixture, ftlm: {}, fmt: {}});
try { sandbox.redesenhar(); concluiu = true; } catch (e) { concluiu = false; }
r.redesenhar_conclui = concluiu;
r.redesenhar_nao_chama_homeo = concluiu && chamadasHomeo === 0;

console.log(JSON.stringify(r));
