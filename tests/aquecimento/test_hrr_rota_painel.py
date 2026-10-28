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


def _ler(rel):
    with open(os.path.join(RAIZ, rel), encoding='utf-8') as f:
        return f.read()


class PainelHRR(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        s = _ler('tabs/tab_metabol.py')
        cls.html = s
        i, j = s.index('/* AQHRR_BEGIN */'), s.index('/* AQHRR_END */')
        cls.js = s[i:j]

    def test_ids_do_html_existem(self):
        for ident in ['id="aqHrr"', 'id="aqHrrAid"', 'id="aqHrrMod"', 'id="aqHrrVar"',
                      'id="chHrr1"', 'id="chHrr2"', 'id="chHrr3"', 'id="aqHrrTabela"',
                      'id="aqHrrMeta"']:
            self.assertIn(ident, self.html, ident)

    def test_botao_chama_carregar(self):
        self.assertIn('onclick="aqHrrCarregar()"', self.html)

    def test_pede_serie_e_rota_correta(self):
        self.assertIn("'/api/aquecimento/hrr?activity_id='", self.js)
        self.assertIn("'&serie=1'", self.js)

    def test_valida_id_antes_de_pedir(self):
        self.assertIn(r"/^i\d+$/", self.js)

    def test_funcoes_principais(self):
        for f in ['function aqHrrCarregar', 'function aqHrrRender', 'function aqHrrDesenha1',
                  'function aqHrrDesenha2', 'function aqHrrDesenha3', 'function aqHrrTabela']:
            self.assertIn(f, self.js, f)

    def test_tabela_escapa_texto_do_servidor(self):
        self.assertIn('aqcEsc(mot)', self.js)
        self.assertIn('aqcEsc(f.nome)', self.js)

    def test_sem_persistencia_no_painel(self):
        for proibido in ['localStorage', 'sessionStorage', 'method: \'POST\'', 'method:"POST"']:
            self.assertNotIn(proibido, self.js, proibido)

    def test_grafico_b_nao_cruza_inicio_do_degrau_seguinte(self):
        self.assertIn('tn - 15 - tf', self.js)

    def test_painel_comparativo_intacto(self):
        self.assertIn('/* AQCMP_BEGIN */', self.html)
        self.assertIn('/* AQCMP_END */', self.html)
        self.assertIn('aqBlocosCarregar(); aqCmpCarregar(); }', self.html)


if __name__ == '__main__':
    unittest.main()
