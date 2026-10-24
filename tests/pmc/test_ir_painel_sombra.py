"""Etapa 15: painel IR sombra (comparacao, estados, lacunas, metricas e separacao de series).

Tres niveis:
  * PainelPuro: modelo_ir_painel.montar sobre respostas sinteticas (sem base de dados);
  * PainelEndpoint: rota /api/modelo/ir/sombra sobre dados sinteticos do cenario temporal (Flask test client);
  * PainelJS: executa o JS REAL do bloco <<ir-sombra>> (node + vm, sem rede) com respostas do endpoint.
    Salta se node nao existir (nao e falha silenciosa: aparece como SKIP no relatorio).

Nada aqui escreve em producao: as leituras usam a base de teste e um spy confirma que o GET nao emite escrita.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

import test_modelo_ir_servico as TS  # noqa: E402
import test_ir_walkforward_temporal as W  # noqa: E402
from test_ir_walkforward_temporal import dia_mais  # noqa: E402
import db  # noqa: E402
import modelo_ir_painel as mip  # noqa: E402
import modelo_validacao as V  # noqa: E402

TAB_PMC = os.path.join(ROOT, 'tabs', 'tab_pmc.py')
HARNESS = os.path.join(HERE, 'js', 'harness_ir_sombra.js')
ROTA = '/api/modelo/ir/sombra'


def setUpModule():
    TS.setUpModule()


def tearDownModule():
    TS.tearDownModule()


def _item(modalidade='Bike', dur=1200, modo='conhecido_na_epoca', previsoes=None, previstos=3, avaliaveis=2,
          rejeitados=1, lacunas=None, metricas=True, estado_suf='descritivo', veredicto=None):
    previsoes = previsoes if previsoes is not None else [
        {'test_id': 't1', 'data': '2025-02-01', 'y': 400.0, 'ir': 395.0, 'M0': 390.0, 'M1': 410.0, 'M2': 0.0,
         'n_treino': 9},
        {'test_id': 't2', 'data': '2025-02-05', 'y': 410.0, 'ir': 418.0, 'M0': 400.0, 'M1': 405.0, 'M2': 0.0,
         'n_treino': 10}]
    if avaliaveis == 0:
        previsoes = []
    met = ({'IR': {'n': 2, 'mae': 5.0, 'rmse': 5.0, 'erro_medio': -1.0},
            'M0': {'n': 2, 'mae': 8.0, 'rmse': 8.0, 'erro_medio': -2.0},
            'M1': {'n': 2, 'mae': 6.0, 'rmse': 6.0, 'erro_medio': 1.0},
            'M2': {'n': 2, 'mae': 7.0, 'rmse': 7.0, 'erro_medio': 0.0}} if metricas and avaliaveis else {})
    lac = lacunas if lacunas is not None else (
        [{'test_id': 't3', 'data': '2025-03-01', 'motivo': 'marcos_sem_registo_antes_do_alvo'}] if rejeitados else [])
    return {'modalidade': modalidade, 'duracao_s': dur, 'modo': modo,
            'n_alvos_previstos': previstos, 'n_alvos_avaliaveis': avaliaveis, 'n_alvos_rejeitados': rejeitados,
            'rejeicoes_por_categoria': {'marcos': rejeitados} if rejeitados else {},
            'alvos_sem_previsao_ir': {'marcos_sem_registo_antes_do_alvo': rejeitados} if rejeitados else {},
            'alvos_rejeitados': lac, 'previsoes': previsoes, 'metricas': met,
            'estado_suficiencia': estado_suf,
            'suficiencia': {'n_alvos': avaliaveis, 'n_datas_distintas': 2 if avaliaveis else 0,
                            'dias_cobertura': 4 if avaliaveis else 0,
                            'primeira': '2025-02-01' if avaliaveis else None,
                            'ultima': '2025-02-05' if avaliaveis else None,
                            'criterios': {'min_alvos': 20}},
            'veredicto': veredicto}


def _resposta(itens=None, natureza='temporal_incompleta', hist_t='incompleto', hist_c='disponivel', bloqueado=False,
              motivo=None, modo='conhecido_na_epoca', status='OK'):
    rm = {'natureza_avaliacao': natureza, 'bloqueado': bloqueado, 'motivo_bloqueio': motivo,
          'n_alvos_previstos': 3, 'n_alvos_avaliaveis': 2, 'n_alvos_rejeitados': 1}
    return {'status': status, 'resultado_por_modo': {modo: rm},
            'itens': [] if itens is None else itens,
            'natureza_avaliacao': {modo: natureza},
            'historico_temporal_estado': hist_t, 'historico_curvas_estado': hist_c,
            'limitacoes': ['limitacao de teste']}


# ── nivel 1: funcoes puras (sem base de dados) ─────────────────────────────────

class PainelPuro(unittest.TestCase):

    def test_constantes_de_suficiencia_coincidem_com_modelo_validacao(self):
        self.assertEqual(mip.SUF_SEM_ALVOS, V.EST_SEM_ALVOS)
        self.assertEqual(mip.SUF_DESCRITIVO, V.EST_DESCRITIVO)
        self.assertEqual(mip.SUF_AVALIAVEL, V.EST_AVALIAVEL)

    def test_quatro_estados_canonicos(self):
        self.assertEqual(mip.ESTADOS, ('disponivel', 'incompleto', 'vazio', 'indisponivel'))

    def test_temporal_com_lacunas_e_incompleto_e_mostra_pontos_disponiveis(self):
        v = mip.montar(_resposta(itens=[_item()]), 'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v['estado'], 'incompleto')
        self.assertEqual(len(v['serie']['sombra']), 2)
        self.assertEqual(len(v['lacunas']), 1)
        self.assertEqual(v['rejeicoes_por_motivo'], {'marcos_sem_registo_antes_do_alvo': 1})

    def test_temporal_sem_lacunas_e_disponivel(self):
        it = _item(rejeitados=0)
        v = mip.montar(_resposta(itens=[it], natureza='temporal_validada', hist_t='disponivel'),
                       'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v['estado'], 'disponivel')
        self.assertEqual(v['natureza'], 'temporal_validada')
        self.assertTrue(v['validacao_temporal'])

    def test_lacunas_sem_pontos_sao_incompletas_e_nao_vazias(self):
        # nada foi calculado, mas ha alvos rejeitados: o utilizador tem de ver as lacunas, nao um vazio neutro
        it = _item(previsoes=[], avaliaveis=0, previstos=2, rejeitados=2, metricas=False)
        v = mip.montar(_resposta(itens=[it]), 'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v['estado'], 'incompleto')
        self.assertEqual(len(v['lacunas']), 1)
        self.assertEqual(v['serie'], {'sombra': [], 'referencia_observada': []})

    def test_historico_temporal_vazio_bloqueia_e_nao_desenha(self):
        r = _resposta(itens=[], natureza='bloqueada_por_historico_insuficiente', hist_t='vazio', bloqueado=True,
                      motivo='historico_temporal_vazio')
        v = mip.montar(r, 'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v['estado'], 'vazio')
        self.assertTrue(v['bloqueado'])
        self.assertEqual(v['motivo_bloqueio'], 'historico_temporal_vazio')
        self.assertEqual(v['serie'], {'sombra': [], 'referencia_observada': []})
        self.assertFalse(v['validacao_temporal'])

    def test_historico_de_curvas_indisponivel_e_estado_indisponivel(self):
        r = _resposta(itens=[], natureza='bloqueada_por_historico_insuficiente', hist_c='indisponivel',
                      bloqueado=True, motivo='historico_curvas_indisponivel')
        self.assertEqual(mip.montar(r, 'Bike', 1200, 'conhecido_na_epoca')['estado'], 'indisponivel')

    def test_pedido_falhado_ou_erro_da_api_e_indisponivel_com_mensagem(self):
        v = mip.montar({'status': 'erro', 'mensagem': 'sem base de dados'}, 'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v['estado'], 'indisponivel')
        self.assertEqual(v['mensagem'], 'sem base de dados')
        self.assertIsNone(mip.montar(None, 'Bike', 1200, 'retrospectivo')['natureza'])

    def test_retrospectivo_sem_previsoes_e_vazio_e_nunca_validacao(self):
        r = _resposta(itens=[_item(modo='retrospectivo', previsoes=[], avaliaveis=0, previstos=0, rejeitados=0,
                                   lacunas=[], metricas=False)],
                      natureza='retrospectiva_valores_atuais', modo='retrospectivo')
        v = mip.montar(r, 'Bike', 1200, 'retrospectivo')
        self.assertEqual(v['estado'], 'vazio')
        self.assertTrue(v['nao_validacao_historica'])
        self.assertFalse(v['validacao_temporal'])
        self.assertIn('ATUAIS', ' '.join(v['avisos']))

    def test_series_separadas_sombra_so_ir_referencia_so_y(self):
        v = mip.montar(_resposta(itens=[_item()]), 'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual([p['valor'] for p in v['serie']['sombra']], [395.0, 418.0])
        self.assertEqual([p['valor'] for p in v['serie']['referencia_observada']], [400.0, 410.0])
        # os baselines M0/M1 (390.0 e 410.0 de M1 no alvo 1) nao entram nas series
        self.assertNotIn('M0', json.dumps(v['serie']))
        self.assertNotIn('M1', json.dumps(v['serie']))
        self.assertEqual({p['test_id'] for p in v['serie']['sombra']}, {'t1', 't2'})

    def test_sem_metricas_quando_nao_ha_alvos_avaliaveis(self):
        it = _item(previsoes=[], avaliaveis=0, previstos=2, rejeitados=2, metricas=False)
        v = mip.montar(_resposta(itens=[it]), 'Bike', 1200, 'conhecido_na_epoca')
        self.assertIsNone(v['metricas'])
        self.assertEqual(v['contagens']['avaliaveis'], 0)

    def test_metricas_com_amostra_periodo_e_diferenca_sem_intervalos(self):
        v = mip.montar(_resposta(itens=[_item()]), 'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v['metricas']['n_alvos'], 2)
        self.assertEqual(v['periodo'], ['2025-02-01', '2025-02-05'])
        self.assertAlmostEqual(v['metricas']['delta_mae_ir_menos_referencia']['M0'], -3.0)
        self.assertAlmostEqual(v['metricas']['delta_mae_ir_menos_referencia']['M1'], -1.0)
        self.assertNotIn('ic95', json.dumps(v['metricas']))

    def test_previsto_nao_avaliavel_e_pendente_e_nao_desenhado(self):
        v = mip.montar(_resposta(itens=[_item(previstos=3, avaliaveis=2)]), 'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v['contagens']['previsto_nao_avaliavel'], 1)
        self.assertEqual(len(v['serie']['sombra']), 2)          # so as avaliaveis aparecem

    def test_evidencia_so_com_temporal_validada_suficiente_e_favoravel(self):
        cases = [
            ('temporal_validada', 'avaliavel', 'ir_melhor_que_ambos_baselines', True),
            ('temporal_validada', 'descritivo', 'ir_melhor_que_ambos_baselines', False),
            ('temporal_incompleta', 'avaliavel', 'ir_melhor_que_ambos_baselines', False),
            ('temporal_validada', 'avaliavel', 'ir_pior_que_algum_baseline', False),
            ('retrospectiva_valores_atuais', 'avaliavel', 'ir_melhor_que_ambos_baselines', False),
        ]
        for nat, suf, verd, esperado in cases:
            it = _item(estado_suf=suf, veredicto=verd)
            v = mip.montar(_resposta(itens=[it], natureza=nat), 'Bike', 1200, 'conhecido_na_epoca')
            self.assertEqual(v['evidencia_para_revisao']['existe'], esperado, (nat, suf, verd))
        # a evidencia nunca declara promocao
        self.assertNotIn('aprov', json.dumps(mip.montar(_resposta(itens=[_item(estado_suf='avaliavel',
                                                                         veredicto='ir_melhor_que_ambos_baselines')],
                                                              natureza='temporal_validada'),
                                                        'Bike', 1200, 'conhecido_na_epoca')).lower())

    def test_veredicto_so_aparece_com_amostra_avaliavel(self):
        it = _item(estado_suf='descritivo', veredicto='ir_melhor_que_ambos_baselines')
        v = mip.montar(_resposta(itens=[it]), 'Bike', 1200, 'conhecido_na_epoca')
        self.assertIsNone(v['veredicto'])

    def test_filtro_so_usa_a_selecao_pedida(self):
        outro = _item(modalidade='Row', dur=300)
        mesma = _item(modalidade='Bike', dur=1200, previsoes=[{'test_id': 'x', 'data': '2025-04-01', 'y': 300.0,
                                                               'ir': 301.0, 'M0': 0, 'M1': 0, 'M2': 0,
                                                               'n_treino': 3}],
                      avaliaveis=1, previstos=1, rejeitados=0, lacunas=[], metricas=False)
        v = mip.montar(_resposta(itens=[outro, mesma], natureza='temporal_validada'), 'Bike', 1200,
                       'conhecido_na_epoca')
        self.assertEqual((v['modalidade'], v['duracao_s']), ('Bike', 1200))
        self.assertEqual(v['serie']['sombra'][0]['test_id'], 'x')
        self.assertEqual(len(v['serie']['sombra']), 1)

    def test_resposta_ambigua_nao_e_desenhada(self):
        v = mip.montar(_resposta(itens=[_item(), _item()]), 'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v['estado'], 'indisponivel')
        self.assertEqual(v['serie'], {'sombra': [], 'referencia_observada': []})

    def test_duracao_180_nao_tem_validacao_temporal_e_fica_explicito(self):
        v = mip.montar(_resposta(itens=[]), 'Bike', 180, 'conhecido_na_epoca')
        self.assertEqual(v['estado'], 'indisponivel')
        self.assertIn('Duracao sem validacao temporal', v['mensagem'])

    def test_selecao_invalida_nao_levanta(self):
        self.assertEqual(mip.montar(None, 'Foo', 1200, 'conhecido_na_epoca')['estado'], 'indisponivel')
        self.assertEqual(mip.montar(None, 'Bike', 1200, 'ambos')['estado'], 'indisponivel')

    def test_campos_opcionais_ausentes_nao_levantam_e_nada_e_inventado(self):
        # sem os estados de historico na resposta a prova e ausente: fica 'indisponivel', nunca 'vazio'
        v1 = mip.montar({'status': 'OK', 'resultado_por_modo': {'conhecido_na_epoca': {'bloqueado': False}}},
                        'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v1['estado'], 'indisponivel')
        self.assertEqual(v1['serie'], {'sombra': [], 'referencia_observada': []})
        # com historico declarado e sem itens, o estado e vazio (nao indisponivel)
        v1b = mip.montar({'status': 'OK', 'resultado_por_modo': {'conhecido_na_epoca': {'bloqueado': False}},
                          'historico_temporal_estado': 'disponivel', 'historico_curvas_estado': 'disponivel'},
                         'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v1b['estado'], 'vazio')
        v2 = mip.montar({'status': 'OK', 'resultado_por_modo': {'conhecido_na_epoca': {}}, 'itens': [{}]},
                        'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v2['estado'], 'indisponivel')
        v3 = mip.montar({'status': 'OK', 'resultado_por_modo': {'conhecido_na_epoca': {'bloqueado': False}},
                         'itens': [{'modalidade': 'Bike', 'duracao_s': 1200, 'modo': 'conhecido_na_epoca',
                                    'previsoes': [{'data': '2025-01-01'}]}]},
                        'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v3['serie']['sombra'], [])          # sem 'ir' nao ha ponto de sombra
        self.assertEqual(len(v3['serie']['referencia_observada']), 0)

    def test_modulo_puro_nao_importa_bd_nem_escreve(self):
        with open(os.path.join(ROOT, 'modelo_ir_painel.py'), encoding='utf-8') as f:
            codigo = f.read()
        self.assertNotRegex(codigo, r'^\s*(import|from)\s+(db|app|config)\b', re.M)
        self.assertIsNone(re.search(r'\b(INSERT|UPDATE|DELETE|CREATE)\b', codigo))


# ── nivel 2: rota sobre dados sinteticos (endpoint real) ───────────────────────

class PainelEndpoint(W.CenarioTemporal):

    def _preparar_completo(self):
        self.preparar_historico()
        primeiro = min(e['data'] for e in self._efeitos)
        self.registar_marcos(dia_mais(primeiro, 60))
        self.registar_yes_dia_a_dia()

    def _rota(self, tipo='Bike', dur=1200, modo='conhecido_na_epoca'):
        return self.c.get(f'{ROTA}?tipo={tipo}&duracao={dur}&modo={modo}')

    def _sem_escrita(self, fn):
        sqls = []
        original = db._exec

        def espiao(*a, **k):
            sqls.append(str(a[0]) if a else '')
            return original(*a, **k)

        with mock.patch.object(db, '_exec', side_effect=espiao):
            resultado = fn()
        escritas = [s for s in sqls if re.match(r'\s*(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE)\b', s, re.I)]
        return resultado, escritas

    def test_parametros_invalidos_devolvem_400(self):
        self.assertEqual(self.c.get(f'{ROTA}?tipo=Foo&duracao=1200&modo=conhecido_na_epoca').status_code, 400)
        self.assertEqual(self.c.get(f'{ROTA}?tipo=Bike&duracao=180&modo=conhecido_na_epoca').status_code, 400)
        self.assertEqual(self.c.get(f'{ROTA}?tipo=Bike&duracao=1200&modo=ambos').status_code, 400)

    def test_historico_completo_vista_coincide_com_a_validacao_da_mesma_selecao(self):
        self._preparar_completo()
        vista = self._rota().get_json()['vista']
        legado = self.c.get('/api/modelo/ir/validacao?tipo=Bike&duracao=1200&modo=conhecido_na_epoca').get_json()
        previsoes = [i for i in legado['itens'] if i['modalidade'] == 'Bike' and i['duracao_s'] == 1200]
        self.assertEqual(len(previsoes), 1)
        esperado = [(p['test_id'], p['ir']) for p in previsoes[0]['previsoes'] if p['ir'] is not None]
        self.assertEqual([(p['test_id'], p['valor']) for p in vista['serie']['sombra']], esperado)
        self.assertEqual(vista['modalidade'], 'Bike')
        self.assertEqual(vista['duracao_s'], 1200)
        self.assertIn(vista['estado'], ('disponivel', 'incompleto'))
        self.assertTrue(vista['validacao_temporal'])

    def test_historico_vazio_bloqueia_com_motivo(self):
        self.preparar_historico()
        vista = self._rota().get_json()['vista']
        self.assertEqual(vista['estado'], 'vazio')
        self.assertTrue(vista['bloqueado'])
        self.assertEqual(vista['serie'], {'sombra': [], 'referencia_observada': []})

    def test_retrospectivo_nunca_e_validacao_temporal(self):
        self._preparar_completo()
        vista = self._rota(modo='retrospectivo').get_json()['vista']
        self.assertTrue(vista['nao_validacao_historica'])
        self.assertFalse(vista['validacao_temporal'])
        self.assertFalse(vista['evidencia_para_revisao']['existe'])

    def test_sem_base_de_dados_e_indisponivel_e_explicito(self):
        with mock.patch.object(db, 'ENABLED', False):
            r = self._rota()
        # Etapa 16: erro real do endpoint propaga o codigo HTTP (503), nunca um 200 'OK' disfarçado.
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()['status'], 'erro')
        self.assertIn('base', r.get_json()['mensagem'].lower())

    def test_leitura_do_painel_nao_escreve(self):
        self._preparar_completo()
        _, escritas = self._sem_escrita(lambda: self._rota().get_json())
        self.assertEqual(escritas, [])

    def test_contrato_legado_de_validacao_continua_com_as_mesmas_chaves(self):
        self._preparar_completo()
        j = self.c.get('/api/modelo/ir/validacao?tipo=Bike&duracao=1200&modo=conhecido_na_epoca').get_json()
        for chave in ('historico_temporal', 'historico_temporal_estado', 'historico_temporal_estado_legado',
                      'historico_curvas_estado', 'natureza_avaliacao', 'resultado_por_modo', 'limitacoes', 'itens'):
            self.assertIn(chave, j)


# ── nivel 3: JS real do bloco <<ir-sombra>> (node + vm) ────────────────────────

class PainelJS(W.CenarioTemporal):

    def setUp(self):
        super().setUp()
        if not shutil.which('node'):
            self.skipTest('node nao disponivel')

    def _node(self, cenarios):
        with tempfile.TemporaryDirectory() as d:
            cam = os.path.join(d, 'cenarios.json')
            with open(cam, 'w', encoding='utf-8') as f:
                json.dump(cenarios, f)
            r = subprocess.run([shutil.which('node'), HARNESS, TAB_PMC, cam],
                               capture_output=True, text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        for k, v in out.items():
            self.assertNotIn('erro', v, (k, v))
        return out

    def _rota_json(self, tipo='Bike', dur=1200, modo='conhecido_na_epoca'):
        return self.c.get(f'{ROTA}?tipo={tipo}&duracao={dur}&modo={modo}').get_json()

    def _acoes(self, *extra, mod='Bike', dur='1200', modo='conhecido_na_epoca'):
        return [{'tipo': 'abrir'},
                {'tipo': 'selecionar', 'campo': 'somMod', 'valor': mod},
                {'tipo': 'selecionar', 'campo': 'somDur', 'valor': dur},
                {'tipo': 'selecionar', 'campo': 'somModo', 'valor': modo}] + list(extra)

    def _rodar(self, nome, corpo, acoes=None, respostas=None):
        c = {'respostas': respostas or [{'corpo': corpo}],
             'acoes': acoes or self._acoes({'tipo': 'carregar'})}
        return self._node({nome: c})[nome]

    def test_historico_completo_desenha_sombra_e_referencia_em_separado(self):
        self._preparar_completo()
        corpo = self._rota_json()
        out = self._rodar('completo', corpo)
        vista = corpo['vista']
        self.assertEqual(out['urls'], [f'{ROTA}?tipo=Bike&duracao=1200&modo=conhecido_na_epoca'])
        self.assertEqual(out['desenho']['arc'].count('#58a6ff'), len(vista['serie']['sombra']))
        self.assertEqual(out['desenho']['arc'].count('#f0883e'), len(vista['serie']['referencia_observada']))
        self.assertTrue(set(out['desenho']['arc']) <= {'#58a6ff', '#f0883e'})
        self.assertIn('Natureza', out['saida_html'])

    def _preparar_completo(self):
        self.preparar_historico()
        primeiro = min(e['data'] for e in self._efeitos)
        self.registar_marcos(dia_mais(primeiro, 60))
        self.registar_yes_dia_a_dia()

    def test_historico_incompleto_mostra_lacunas_e_so_pontos_disponiveis(self):
        self._preparar_completo()
        corpo = self._rota_json()
        out = self._rodar('incompleto', corpo)
        self.assertIn('Lacunas:', out['saida_html'])
        self.assertIn('Categorias:', out['saida_html'])
        self.assertEqual(out['desenho']['arc'].count('#58a6ff'), len(corpo['vista']['serie']['sombra']))

    def test_vazio_e_bloqueado_sem_linha_experimental(self):
        self.preparar_historico()
        corpo = self._rota_json()
        out = self._rodar('vazio', corpo)
        self.assertIn('Bloqueado:', out['saida_html'])
        self.assertNotIn('#58a6ff', out['desenho']['arc'])
        self.assertIn('nenhuma previsão foi calculada', out['saida_html'])
        self.assertIn('vazio', out['saida_html'])

    def test_indisponivel_quando_o_pedido_falha(self):
        out = self._rodar('falha', None, respostas=[{'falha': True}])   # o pedido rejeita: vista indisponivel no cliente
        self.assertIn('indispon', out['saida_html'])
        self.assertEqual(out['desenho']['arc'], [])

    def test_erro_real_da_api_mostra_a_mensagem_do_servidor(self):
        # Etapa 16: 503 com status 'erro' deixou de ser um texto generico: o cliente mostra a mensagem do servidor.
        out = self._rodar('erro_api', {'status': 'erro', 'mensagem': 'sem base de dados de teste'})
        self.assertIn('sem base de dados de teste', out['saida_html'])
        self.assertEqual(out['desenho']['arc'], [])

    def test_indisponivel_quando_a_base_nao_existe(self):
        with mock.patch.object(db, 'ENABLED', False):
            corpo = self._rota_json()
        out = self._rodar('sem_base', corpo)
        self.assertIn('indispon', out['saida_html'])
        self.assertEqual(out['desenho']['arc'], [])

    def test_sem_metricas_quando_nao_ha_alvos_avaliaveis(self):
        self._preparar_completo()
        corpo = self._rota_json('Row', 300)
        out = self._rodar('row_300', corpo, acoes=self._acoes({'tipo': 'carregar'}, mod='Row', dur='300'))
        self.assertIn('Sem métricas', out['saida_html'])
        self.assertNotIn('#58a6ff', out['desenho']['arc'])

    def test_retrospectivo_mostra_que_nao_e_validacao(self):
        self._preparar_completo()
        corpo = self._rota_json(modo='retrospectivo')
        out = self._rodar('retro', corpo, acoes=self._acoes({'tipo': 'carregar'}, modo='retrospectivo'))
        self.assertIn('não é validação histórica', out['saida_html'])
        self.assertNotIn('Temporal validada', out['saida_html'])
        self.assertIn('Sem evidencia suficiente', out['saida_html'])   # visivel mesmo sem metricas

    def test_filtros_correspondem_a_selecao_pedida(self):
        self._preparar_completo()
        corpo = self._rota_json('Row', 300)
        out = self._rodar('filtro', corpo, acoes=self._acoes({'tipo': 'carregar'}, mod='Row', dur='300'))
        self.assertEqual(out['urls'], [f'{ROTA}?tipo=Row&duracao=300&modo=conhecido_na_epoca'])
        self.assertIn('Row', out['saida_html'])
        self.assertIn('300 s', out['saida_html'])

    def test_mudar_selecao_limpa_o_resultado_anterior(self):
        self._preparar_completo()
        corpo = self._rota_json()
        out = self._rodar('mudar', corpo, acoes=self._acoes({'tipo': 'carregar'}, {'tipo': 'mudar'}))
        self.assertEqual(out['saida_html'], '')
        self.assertIn('Seleção alterada', out['aviso'])

    def test_operacional_fica_separado_e_sem_serie_em_w(self):
        self._preparar_completo()
        corpo = self._rota_json()
        ir_j = {'status': 'OK', 'modalidades': {'Bike': {'ajuste': {'versao_ajuste': 3, 'ajustado_em': '2026-10-01'},
                                                         'parametros': {'p0': 412.0}}}}
        c = {'respostas': [{'corpo': corpo}], 'ir_j': ir_j, 'acoes': self._acoes({'tipo': 'carregar'})}
        out = self._node({'op': c})['op']
        self.assertIn('ajuste v3', out['saida_html'])
        self.assertIn('não entra nesta comparação em W', out['saida_html'])
        self.assertTrue(set(out['desenho']['arc']) <= {'#58a6ff', '#f0883e'})

    def test_sem_operacional_carregado_mostra_aviso(self):
        self._preparar_completo()
        out = self._rodar('sem_op', self._rota_json())
        self.assertIn('abra o painel do IR operacional', out['saida_html'])

    def test_resposta_de_selecao_antiga_nao_substitui_a_atual(self):
        self._preparar_completo()
        bike = self._rota_json('Bike', 1200)
        row = self._rota_json('Row', 300)
        c = {'respostas': [{'corpo': bike, 'atraso': 80}, {'corpo': row, 'atraso': 0}],
             'acoes': [{'tipo': 'abrir'},
                       {'tipo': 'selecionar', 'campo': 'somMod', 'valor': 'Bike'},
                       {'tipo': 'selecionar', 'campo': 'somDur', 'valor': '1200'},
                       {'tipo': 'selecionar', 'campo': 'somModo', 'valor': 'conhecido_na_epoca'},
                       {'tipo': 'carregar_sem_aguardar'},
                       {'tipo': 'selecionar', 'campo': 'somMod', 'valor': 'Row'},
                       {'tipo': 'selecionar', 'campo': 'somDur', 'valor': '300'},
                       {'tipo': 'carregar_sem_aguardar'},
                       {'tipo': 'esperar', 'ms': 200}]}
        out = self._node({'corrida': c})['corrida']
        self.assertIn('Row', out['saida_html'])
        self.assertNotIn('Bike · 1200', out['saida_html'])

    def test_campos_opcionais_ausentes_nao_quebram_a_renderizacao(self):
        out = self._rodar('minimo', {'status': 'OK', 'vista': {'estado': 'disponivel'}})
        self.assertIn('disponivel', out['saida_html'])
        self.assertIn('Sem evidencia suficiente', out['saida_html'])    # texto por omissao, sem campos
        out2 = self._rodar('vazio_total', {})
        self.assertIn('indisponivel', out2['saida_html'])

    def test_bloco_js_so_le_a_rota_da_sombra_e_nao_escreve(self):
        with open(TAB_PMC, encoding='utf-8') as f:
            src = f.read()
        i0, i1 = src.index('// <<ir-sombra>>'), src.index('// <</ir-sombra>>')
        bloco = src[i0:i1]
        urls = set(re.findall(r"fetch\('([^']+)'", bloco)) | set(re.findall(r"fetch\(\"([^\"]+)\"", bloco))
        # Etapa 17: duas leituras GET (vista da sombra e estado do registo prospetivo). Nenhuma escrita.
        self.assertTrue(urls <= {'/api/modelo/ir/sombra?tipo=', '/api/modelo/ir/sombra/prospetiva'}, urls)
        self.assertNotIn("method:'POST'", bloco)
        self.assertNotIn('recalibrar', bloco)
        self.assertNotIn('sincroniz', bloco.lower())
        self.assertNotIn('ir/validacao', bloco)            # o painel usa a rota da vista, nao o contrato bruto


if __name__ == '__main__':
    unittest.main()


class PainelAuditoriaEtapa16(unittest.TestCase):
    """Regressoes da auditoria da Etapa 16. Cada teste falhava antes da correcao."""

    def _retro(self, previsoes, rej):
        it = _item(modo='retrospectivo', previsoes=previsoes, previstos=len(previsoes) + len(rej),
                   avaliaveis=len(previsoes), rejeitados=len(rej), lacunas=rej)
        it['alvos_rejeitados'] = rej
        return _resposta(itens=[it], natureza='retrospectiva_valores_atuais', hist_t='disponivel', modo='retrospectivo')

    def test_retrospectivo_com_lacunas_e_incompleto_nao_disponivel(self):
        rej = [{'test_id': 't3', 'data': '2025-03-01', 'motivo': 'sem_previsao'}]
        vista = mip.montar(self._retro([{'test_id': 't1', 'data': '2025-02-01', 'y': 400.0, 'ir': 395.0,
                                         'M0': 390.0, 'M1': 410.0, 'M2': 0.0, 'n_treino': 9}], rej),
                           'Bike', 1200, 'retrospectivo')
        self.assertEqual(vista['estado'], 'incompleto')
        self.assertEqual(vista['contagens']['rejeitados'], 1)

    def test_retrospectivo_so_com_lacunas_nao_esconde_as_lacunas(self):
        rej = [{'test_id': 't3', 'data': '2025-03-01', 'motivo': 'sem_previsao'}]
        vista = mip.montar(self._retro([], rej), 'Bike', 1200, 'retrospectivo')
        self.assertEqual(vista['estado'], 'incompleto')
        self.assertEqual(len(vista['lacunas']), 1)

    def test_retrospectivo_completo_continua_disponivel(self):
        vista = mip.montar(self._retro([{'test_id': 't1', 'data': '2025-02-01', 'y': 400.0, 'ir': 395.0,
                                         'M0': 390.0, 'M1': 410.0, 'M2': 0.0, 'n_treino': 9}], []),
                           'Bike', 1200, 'retrospectivo')
        self.assertEqual(vista['estado'], 'disponivel')

    def test_erro_da_api_e_distinto_de_historico_indisponivel(self):
        erro = mip.montar({'status': 'erro', 'mensagem': 'base em baixo'}, 'Bike', 1200, 'conhecido_na_epoca')
        hist = mip.montar(_resposta(hist_t='indisponivel', natureza='bloqueada_por_historico_insuficiente',
                                    bloqueado=True, motivo='historico_temporal_indisponivel'),
                          'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(erro['estado'], 'indisponivel')
        self.assertEqual(hist['estado'], 'indisponivel')
        self.assertEqual(erro['origem'], 'pedido_falhou')
        self.assertEqual(hist['origem'], 'historico')

    def test_sombra_com_erro_da_api_propaga_o_codigo_http(self):
        import app as appmod
        from unittest import mock as m
        from flask import jsonify
        self.app = appmod.app
        c = self.app.test_client()
        with m.patch.object(appmod, 'api_modelo_ir_validacao',
                            lambda: (jsonify({'status': 'erro', 'mensagem': 'sem base de dados'}), 503)):
            r = c.get(f'{ROTA}?tipo=Bike&duracao=1200&modo=conhecido_na_epoca')
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()['status'], 'erro')
        self.assertIn('sem base', r.get_json()['mensagem'])

    def test_resposta_ok_sem_vista_nao_se_disfarça_de_ok(self):
        import app as appmod
        from unittest import mock as m
        from flask import jsonify
        c = appmod.app.test_client()
        with m.patch.object(appmod, 'api_modelo_ir_validacao', lambda: (jsonify({'status': 'OK'}), 200)):
            r = c.get(f'{ROTA}?tipo=Bike&duracao=1200&modo=conhecido_na_epoca')
        self.assertEqual(r.get_json()['vista']['estado'], 'indisponivel')
        self.assertEqual(r.status_code, 200)
