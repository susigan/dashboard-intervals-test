"""Rota /api/aquecimento/hrr (so' leitura) e estrutura do painel HRR.

Rota: a API e' simulada (mock de api_client.icu_get); nao ha chamada real.
Painel: verificacoes de texto. O desenho dos graficos nao e' validado aqui.
"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
RAIZ = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, RAIZ)  # 'app' fica na raiz; python -I nao a inclui

from test_hrr_unit import sessao  # noqa: E402

# Protocolo Bike real: a deteccao exige a sequencia completa (80 a 160 W).
PROTO_BIKE = [80, 100, 120, 140, 160]


def _streams_api(t, w, h, extra=None):
    lista = [{'type': 'time', 'data': t}, {'type': 'watts', 'data': w},
             {'type': 'heartrate', 'data': h}]
    for k, v in (extra or {}).items():
        lista.append({'type': k, 'data': v})
    return lista


class RotaHRR(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('INTERVALS_ICU_API_KEY', 'dummy-teste')
        try:
            import app as app_mod
        except Exception as e:                      # pragma: no cover
            raise unittest.SkipTest(f'app nao importa em teste: {type(e).__name__}: {e}')
        cls.app_mod = app_mod
        cls.client = app_mod.app.test_client()

    def _get(self, qs, icu_retorno):
        with mock.patch('api_client.icu_get', return_value=icu_retorno), \
             mock.patch.object(self.app_mod, '_duracao_atividade', return_value=None):
            return self.client.get('/api/aquecimento/hrr?' + qs)

    def test_sem_parametros_400(self):
        self.assertEqual(self.client.get('/api/aquecimento/hrr').status_code, 400)

    def test_erro_da_api_502(self):
        r = self._get('activity_id=i1&modalidade=Bike', (None, 'sem acesso'))
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()['status'], 'erro_api')

    def test_erro_interno_nao_expoe_traceback_ao_cliente(self):
        t, w, h, _ = sessao(PROTO_BIKE, rec_s=(150,) * 5, decl=(0.5,) * 5)
        segredo = 'SEGREDO_INTERNO_NAO_PUBLICO_42'
        with mock.patch('api_client.icu_get', return_value=(_streams_api(t, w, h), None)), \
             mock.patch.object(self.app_mod, '_duracao_atividade', return_value=None), \
             mock.patch('aquecimento_hrr.hrr_por_degrau',
                        side_effect=RuntimeError(segredo)), \
             self.assertLogs(self.app_mod.app.logger, level='ERROR') as log:
            r = self.client.get('/api/aquecimento/hrr?activity_id=i1&modalidade=Bike')
        self.assertEqual(r.status_code, 500)
        corpo = r.get_data(as_text=True)
        j = r.get_json()
        self.assertEqual(j['status'], 'erro_interno')
        self.assertIn('referencia', j)
        self.assertNotIn('trace', j)
        self.assertNotIn('Traceback', corpo)
        self.assertNotIn('File "', corpo)
        self.assertNotIn('.py', corpo)
        self.assertNotIn(segredo, corpo)
        # o detalhe completo fica no log do servidor, com a mesma referencia
        registo = '\n'.join(log.output)
        self.assertIn(j['referencia'], registo)
        self.assertIn(segredo, registo)

    def test_pedido_sem_parametros_nao_expoe_detalhes(self):
        r = self.client.get('/api/aquecimento/hrr?activity_id=i1')
        self.assertEqual(r.status_code, 400)
        self.assertNotIn('Traceback', r.get_data(as_text=True))

    def test_resposta_ok_sem_serie_por_omissao(self):
        t, w, h, _ = sessao(PROTO_BIKE, rec_s=(150,) * 5, decl=(0.5,) * 5)
        r = self._get('activity_id=i1&modalidade=Bike', (_streams_api(t, w, h), None))
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j['status'], 'ok')
        self.assertTrue(j['somente_leitura'])
        self.assertNotIn('serie_grafico', j)

    def test_serie_so_com_parametro(self):
        t, w, h, _ = sessao(PROTO_BIKE, rec_s=(150,) * 5, decl=(0.5,) * 5)
        r = self._get('activity_id=i1&modalidade=Bike&serie=1',
                      (_streams_api(t, w, h, {'smo2': [60.0] * len(t)}), None))
        j = r.get_json()
        self.assertIn('serie_grafico', j)
        self.assertIn('smo2', j['serie_grafico'])
        self.assertIn('smo2', j['degraus'][0]['fisio'])

    def test_valores_nao_dependem_do_parametro_serie(self):
        t, w, h, _ = sessao(PROTO_BIKE, rec_s=(150,) * 5, decl=(0.5,) * 5)
        dados = (_streams_api(t, w, h), None)
        a = self._get('activity_id=i1&modalidade=Bike', dados).get_json()['degraus']
        b = self._get('activity_id=i1&modalidade=Bike&serie=1', dados).get_json()['degraus']
        self.assertEqual(a, b)


class RotaHRRCorrecoes(unittest.TestCase):
    """Pedido com includeDefaults, diagnostico sem valores e metrica nova na resposta."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('INTERVALS_ICU_API_KEY', 'dummy-teste')
        try:
            import app as app_mod
        except Exception as e:                      # pragma: no cover
            raise unittest.SkipTest(f'app nao importa em teste: {type(e).__name__}: {e}')
        cls.app_mod = app_mod
        cls.client = app_mod.app.test_client()

    def _get(self, qs, icu_retorno):
        mock_icu = mock.Mock(return_value=icu_retorno)
        with mock.patch('api_client.icu_get', mock_icu), \
             mock.patch.object(self.app_mod, '_duracao_atividade', return_value=None):
            r = self.client.get('/api/aquecimento/hrr?' + qs)
        return r, mock_icu

    def test_pede_streams_com_include_defaults(self):
        t, w, h, _ = sessao(PROTO_BIKE, rec_s=(150,) * 5, decl=(0.5,) * 5)
        r, icu = self._get('activity_id=i1&modalidade=Bike', (_streams_api(t, w, h), None))
        self.assertEqual(r.status_code, 200)
        icu.assert_called_once()
        args, kwargs = icu.call_args
        self.assertEqual(args[0], '/activity/i1/streams')
        params = args[1] if len(args) > 1 else kwargs.get('params')
        self.assertEqual(params, {'includeDefaults': 'true'})

    def test_sem_fc_resposta_so_com_nomes(self):
        t, w, _, _ = sessao(PROTO_BIKE, rec_s=(150,) * 5, decl=(0.5,) * 5)
        segredo = 9876.5
        dados = _streams_api(t, w, [0] * len(t), extra={'smo2': [segredo] * len(t)})
        dados = [x for x in dados if x['type'] != 'heartrate']  # FC realmente ausente
        r, _ = self._get('activity_id=i1&modalidade=Bike', (dados, None))
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j['status'], 'sem_hr')
        self.assertEqual(j['tipos_recebidos'], ['smo2', 'time', 'watts'])
        corpo = r.get_data(as_text=True)
        self.assertNotIn(str(segredo), corpo)           # nenhum valor da serie
        self.assertEqual(j['degraus'], [])              # sem calculo por degrau

    def test_fc_vazia_e_sem_hr_com_nome(self):
        t, w, _, _ = sessao(PROTO_BIKE, rec_s=(150,) * 5, decl=(0.5,) * 5)
        dados = _streams_api(t, w, [])                  # heartrate existe mas vazio
        dados = [x if x['type'] != 'heartrate' else {'type': 'heartrate', 'data': []} for x in dados]
        r, _ = self._get('activity_id=i1&modalidade=Bike', (dados, None))
        j = r.get_json()
        self.assertEqual(j['status'], 'sem_hr')
        self.assertIn('heartrate', j['tipos_recebidos'])

    def test_rota_com_fc_depois_de_duplicado_allnull_calcula(self):
        t, w, h, _ = sessao(PROTO_BIKE, rec_s=(150,) * 5, decl=(0.5,) * 5)
        dados = _streams_api(t, w, h) + [{'type': 'heartrate', 'data': None, 'allNull': True}]
        r, _ = self._get('activity_id=i1&modalidade=Bike', (dados, None))
        j = r.get_json()
        self.assertEqual(j['status'], 'ok')
        self.assertNotIn('tipos_recebidos', j)

    def test_resposta_ok_traz_fc_media_rec_separada(self):
        t, w, h, _ = sessao(PROTO_BIKE, rec_s=(150,) * 5, decl=(0.5,) * 5)
        r, _ = self._get('activity_id=i1&modalidade=Bike', (_streams_api(t, w, h), None))
        j = r.get_json()
        self.assertEqual(j['status'], 'ok')
        d = j['degraus'][0]
        self.assertIn('fc_media_rec_1min', d)
        self.assertNotIn('hrr', d['fc_media_rec_1min'])
        self.assertNotIn('tipos_recebidos', j)          # so' em sem_hr


def _ler(rel):
    with open(os.path.join(RAIZ, rel), encoding='utf-8') as f:
        return f.read()


class PainelTendencia(unittest.TestCase):
    """Tendencia de FC rec. 1 min por periodo: sem sessao escolhida, agrupada."""

    @classmethod
    def setUpClass(cls):
        s = _ler('tabs/tab_metabol.py')
        cls.html = s
        i, j = s.index('/* AQTEND_BEGIN */'), s.index('/* AQTEND_END */')
        cls.js = s[i:j]

    def test_ids_do_html_existem(self):
        for ident in ['id="aqTend"', 'id="aqTendCont"', 'id="aqTendMeta"']:
            self.assertIn(ident, self.html, ident)

    def test_nao_ha_seccao_por_sessao(self):
        for proibido in ['aqHrr', 'chHrr1', 'Calcular HRR desta sessão', 'AQHRR_BEGIN']:
            self.assertNotIn(proibido, self.html, proibido)

    def test_nao_depende_de_sessao_escolhida(self):
        self.assertNotIn('aqBlocosSessao', self.js)
        self.assertNotIn('/api/aquecimento/hrr', self.js)

    def test_pede_o_comparativo_com_fc_rec_1min_agrupado(self):
        self.assertIn("'/api/aquecimento/comparativo?modalidade='", self.js)
        self.assertIn("'&metrica=fc_rec1min&dias='", self.js)
        self.assertIn("'&agregacao=mes&min_n=1'", self.js)

    def test_periodos_selecionaveis_sem_sessao(self):
        self.assertGreaterEqual(self.html.count('class="aqTendP"'), 3)
        for dias in ['value="30"', 'value="60"', 'value="90"']:
            self.assertIn(dias, self.html)

    def test_modalidade_vem_da_tab(self):
        self.assertIn('AQ_MOD', self.js)

    def test_escapa_texto_do_servidor(self):
        self.assertIn('aqcEsc(p.periodo)', self.js)
        self.assertIn('aqcEsc(j.mensagem', self.js)

    def test_sem_persistencia(self):
        for proibido in ['localStorage', 'sessionStorage', "method: 'POST'", 'method:"POST"']:
            self.assertNotIn(proibido, self.js, proibido)

    def test_hook_no_separador_aquecimento(self):
        self.assertIn('aqTendLigar(); aqTendCarregar(); }', self.html)

    def test_painel_comparativo_intacto(self):
        self.assertIn('/* AQCMP_BEGIN */', self.html)
        self.assertIn('/* AQCMP_END */', self.html)


if __name__ == '__main__':
    unittest.main()
