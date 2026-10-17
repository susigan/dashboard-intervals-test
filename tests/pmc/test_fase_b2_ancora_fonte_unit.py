"""Fase B2, passo 2 — origem do lag em calibrar_com_ancora.

`fonte`, `calibrado`, `motivo` e `lag_referencia` sao aditivos: `lag` mantem o
contrato anterior (inclui o valor de referencia no fallback). Sem rede/BD.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _serie(n=400):
    ini = datetime(2025, 1, 1)
    return [{'date': (ini + timedelta(days=i)).strftime('%Y-%m-%d'), 'load': 50.0 + (i % 7)}
            for i in range(n)]


def _curvas(tipo, n, w=300.0, ini=datetime(2025, 1, 1)):
    return [{'activity_id': f'{tipo}{i}', 'type': tipo,
             'date': (ini + timedelta(days=5 + 10 * i)).strftime('%Y-%m-%d'),
             'secs': [60, 300, 1200], 'watts': [None, None, w]} for i in range(n)]


FALLBACK = {'fonte': 'referencia', 'valor': 17,
            'motivo': 'nenhum lag deu correlacao com p < 0.1', 'testados': []}


def _dados(p, valor=14):
    return {'fonte': 'dados', 'valor': valor, 'r': 0.42, 'r2': 0.1764,
            'p_permutacao': p, 'p': 0.02, 'n': 30, 'testados': [],
            'forca': 'moderada', 'distribuicao_nula': {}}


class AncoraFonte(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import pmc
                import calibracao
                import db
        except BaseException as e:
            raise unittest.SkipTest(f'pmc nao importa: {type(e).__name__}: {e}')
        cls.pmc, cls.cal, cls.db = pmc, calibracao, db

    @staticmethod
    def _todas_elegiveis(curvas_por_mod):
        # icu_ignore_power=false explicito em todas as atividades: so assim entram na calibracao
        return [{'id': c['activity_id'], 'icu_ignore_power': False}
                for lst in curvas_por_mod.values() for c in lst]

    def _correr(self, curvas_por_mod, resposta_lag=None, mods=None, serie=None, atividades=None):
        if atividades is None:
            atividades = self._todas_elegiveis(curvas_por_mod)
        respostas = resposta_lag if isinstance(resposta_lag, dict) and 'fonte' not in resposta_lag \
            else None
        def lag_por_chamada(*a, **k):
            if respostas is not None:
                return respostas[lag_por_chamada.fila.pop(0)]
            return resposta_lag
        lag_por_chamada.fila = list(mods or curvas_por_mod)
        with mock.patch.object(self.db, 'load_power_curves',
                               side_effect=lambda tipo=None, desde=None: curvas_por_mod.get(tipo, [])), \
                mock.patch.object(self.cal, 'calibrar_lag', side_effect=lag_por_chamada):
            return self.pmc.calibrar_com_ancora(serie or _serie(), list(mods or curvas_por_mod),
                                                atividades=atividades)

    # ---- fallback e origem do lag -------------------------------------
    def test_fallback_nao_e_calibracao(self):
        e = self._correr({'Row': _curvas('Row', 32)}, FALLBACK)['por_modalidade']['Row']
        self.assertEqual(e['fonte'], 'referencia')
        self.assertIs(e['calibrado'], False)
        self.assertEqual(e['motivo'], 'nenhum lag deu correlacao com p < 0.1')
        self.assertEqual(e['lag_referencia'], 17)
        self.assertEqual(e['lag'], 17)                       # contrato anterior preservado
        self.assertEqual((e['r'], e['r2'], e['p_permutacao']), (None, None, None))
        self.assertIs(e['sobrevive'], False)

    def test_lag_estimado_dos_dados(self):
        e = self._correr({'Bike': _curvas('Bike', 32)}, _dados(0.298))['por_modalidade']['Bike']
        self.assertEqual(e['fonte'], 'dados')
        self.assertIs(e['calibrado'], True)
        self.assertIsNone(e['lag_referencia'])
        self.assertIsNone(e['motivo'])
        self.assertEqual((e['lag'], e['r'], e['r2']), (14, 0.42, 0.1764))

    # ---- calibrado != sobrevive ---------------------------------------
    def test_calibrado_e_sobrevive_sao_independentes(self):
        curvas = {'Bike': _curvas('Bike', 32)}
        nao = self._correr(curvas, _dados(0.298))['por_modalidade']['Bike']
        sim = self._correr(curvas, _dados(0.01))['por_modalidade']['Bike']
        self.assertEqual((nao['calibrado'], nao['sobrevive']), (True, False))
        self.assertEqual((sim['calibrado'], sim['sobrevive']), (True, True))

    def test_fallback_nunca_sobrevive_nem_e_lido_como_calibracao_bem_sucedida(self):
        r = self._correr({'Row': _curvas('Row', 32)}, FALLBACK)
        self.assertEqual(r['sobrevivem'], [])
        self.assertEqual(r['calibradas'], [])
        self.assertEqual(r['em_referencia'], ['Row'])
        self.assertIn('referencia', r['leitura'])
        self.assertIn('Row', r['leitura'])
        self.assertIn('nao uma calibracao', r['leitura'])

    # ---- amostra insuficiente -----------------------------------------
    def test_amostra_insuficiente(self):
        e = self._correr({'Run': _curvas('Run', 10)}, FALLBACK)['por_modalidade']['Run']
        self.assertEqual(e['fonte'], 'insuficiente')
        self.assertIs(e['calibrado'], False)
        self.assertEqual(e['n_testes'], 8)
        self.assertIn('precisa de 25', e['motivo'])
        self.assertIsNone(e['lag_referencia'])
        self.assertNotIn('lag', e)                           # contrato anterior: sem lag

    def test_listas_de_topo_por_modalidade(self):
        # Bike: dados (p alto); Row: referencia; Run: insuficiente
        curvas = {'Bike': _curvas('Bike', 32), 'Row': _curvas('Row', 32), 'Run': _curvas('Run', 10)}
        r = self._correr(curvas, {'Bike': _dados(0.298), 'Row': FALLBACK},
                         mods=['Bike', 'Row', 'Run'])
        pm = r['por_modalidade']
        self.assertEqual([pm[m]['fonte'] for m in ('Bike', 'Row', 'Run')],
                         ['dados', 'referencia', 'insuficiente'])
        self.assertEqual((r['calibradas'], r['em_referencia'], r['sobrevivem']),
                         (['Bike'], ['Row'], []))

    def test_sobrevivente_com_fallback_noutra_modalidade_mantem_leitura_original(self):
        curvas = {'Bike': _curvas('Bike', 32), 'Row': _curvas('Row', 32)}
        r = self._correr(curvas, {'Bike': _dados(0.01), 'Row': FALLBACK}, mods=['Bike', 'Row'])
        self.assertEqual(r['sobrevivem'], ['Bike'])
        self.assertTrue(r['leitura'].startswith('Bike: a CP nos dias de teste responde a carga 14 dias'))
        self.assertIn('Row', r['leitura'].split('Bike:')[1].split('responde')[1])

    # ---- calibrar_lag real (sem mock): o fallback chega ao resultado ----
    def test_fonte_propaga_a_partir_de_calibrar_lag_real(self):
        # 26 ancoras mas menos de 30 dias com alvo => calibrar_lag cai em referencia
        with mock.patch.object(self.db, 'load_power_curves',
                               side_effect=lambda tipo=None, desde=None: _curvas('Bike', 28)
                               if tipo == 'Bike' else []):
            r = self.pmc.calibrar_com_ancora(_serie(), ['Bike'],
                                             atividades=self._todas_elegiveis({'Bike': _curvas('Bike', 28)}))
        e = r['por_modalidade']['Bike']
        self.assertEqual(e['n_testes'], 26)
        self.assertEqual((e['fonte'], e['calibrado']), ('referencia', False))
        self.assertEqual((e['lag'], e['lag_referencia']), (self.cal.REFERENCIA['lag_super'],) * 2)
        self.assertIn('precisa de 30', e['motivo'])

    def test_sem_serie_devolve_none_como_antes(self):
        self.assertIsNone(self.pmc.calibrar_com_ancora([], ['Bike']))

    def test_nao_altera_algoritmo_nem_parametros(self):
        antes = dict(self.cal.REFERENCIA)
        with mock.patch.object(self.db, 'load_power_curves',
                               side_effect=lambda tipo=None, desde=None: _curvas('Bike', 32)) as m, \
                mock.patch.object(self.cal, 'calibrar_lag', return_value=FALLBACK) as lag:
            self.pmc.calibrar_com_ancora(_serie(), ['Bike'],
                                         atividades=self._todas_elegiveis({'Bike': _curvas('Bike', 32)}))
        # mesma chamada de sempre ao calculo estatistico (lags 5..28, sem sinal, chave lag_super)
        args = lag.call_args[0]
        self.assertEqual((list(args[2]), args[3], args[4]), (list(range(5, 29)), None, 'lag_super'))
        self.assertEqual(self.cal.REFERENCIA, antes)


if __name__ == '__main__':
    unittest.main()
