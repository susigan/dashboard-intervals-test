"""Fase B2, passo 1 — testes de caracterizacao.

Fixam o comportamento ATUAL da deteccao e da ancora de calibracao ANTES das
alteracoes da Fase B2 (fonte/calibrado, suficiencia por modalidade,
elegibilidade). Se alguem mudar um limiar ou a regra de deteccao, estes testes
falham: a mudanca tem de ser deliberada, nao silenciosa.

Sem rede e sem base real: curvas sinteticas e o modulo `db` simulado.
"""
import inspect
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import protocolo as P  # noqa: E402

HOJE = datetime(2026, 10, 16)


class _Relogio(datetime):
    @classmethod
    def now(cls, tz=None):
        return HOJE


def _data(dias_atras):
    return (HOJE - timedelta(days=dias_atras)).strftime('%Y-%m-%d')


def _curva(aid, tipo, dias_atras, w60=None, w300=None, w1200=None):
    return {'activity_id': aid, 'type': tipo, 'date': _data(dias_atras),
            'secs': [60, 300, 1200], 'watts': [w60, w300, w1200]}


def _n_curvas_1200(tipo, n, w=300, rec=10, passo=5, prefixo='c'):
    """n sessoes de 20 min a w watts, a mais recente ha `rec` dias. Como as
    duas primeiras nao tem 3 pontos na janela, produzem-se n-2 testes."""
    return [_curva(f'{prefixo}{tipo}{i}', tipo, rec + passo * (n - 1 - i), w1200=w)
            for i in range(n)]


def _detectar(curvas):
    with mock.patch.object(P, 'datetime', _Relogio):
        return P.detectar_testes(curvas)


class LimiaresDeDeteccao(unittest.TestCase):
    def test_constantes(self):
        self.assertEqual(P.LIMIAR_ESFORCO, 0.92)
        self.assertEqual(P.JANELA_MELHOR, 180)
        self.assertEqual({s: (t['nome'], t['intervalo_dias']) for s, t in P.TESTES.items()},
                         {300: ('5 min max', 21), 1200: ('20 min max', 28), 60: ('1 min max', 28)})

    def test_defaults_da_funcao(self):
        sig = inspect.signature(P.detectar_testes)
        self.assertEqual(sig.parameters['limiar'].default, 0.92)
        self.assertEqual(sig.parameters['janela'].default, 180)

    def test_minimo_de_5_pontos_na_serie(self):
        self.assertEqual(_detectar(_n_curvas_1200('Bike', 4)), {})
        self.assertIn('Bike', _detectar(_n_curvas_1200('Bike', 5)))

    def test_minimo_de_3_pontos_na_janela_conta_o_proprio(self):
        # 5 pontos => os dois primeiros ficam sem 3 anteriores; sobram 3 testes
        d = _detectar(_n_curvas_1200('Bike', 5))['Bike'][1200]
        self.assertEqual(d['n_testes'], 3)

    def test_limiar_de_92_por_cento_e_inclusivo(self):
        base = _n_curvas_1200('Bike', 8, 300)
        # 276 W = 92 % exato de 300 W
        base.append(_curva('limite', 'Bike', 1, w1200=276))
        base.append(_curva('abaixo', 'Bike', 0, w1200=275))
        ids = {t['activity_id'] for t in _detectar(base)['Bike'][1200]['testes']}
        self.assertIn('limite', ids)
        self.assertNotIn('abaixo', ids)

    def test_o_proprio_esforco_entra_no_melhor_recente(self):
        # novo maximo: 100 % do melhor, porque o ponto avaliado faz parte da janela
        base = _n_curvas_1200('Bike', 6, 300) + [_curva('novo_max', 'Bike', 1, w1200=360)]
        t = [x for x in _detectar(base)['Bike'][1200]['testes'] if x['activity_id'] == 'novo_max'][0]
        self.assertEqual(t['pct_do_melhor'], 100.0)
        self.assertEqual(t['melhor_recente'], 360.0)

    def test_ausente_nao_e_zero(self):
        base = _n_curvas_1200('Bike', 8, 300)
        base.append(_curva('sem_1200', 'Bike', 1, w60=500, w1200=None))
        d = _detectar(base)['Bike']
        self.assertNotIn('sem_1200', {t['activity_id'] for t in d[1200]['testes']})

    def test_janela_de_180_dias_exclui_melhores_antigos(self):
        # um pico de 400 W ha 300 dias nao rebaixa o resto abaixo do limiar
        velho = [_curva('pico', 'Bike', 300, w1200=400)]
        recentes = _n_curvas_1200('Bike', 8, 300)
        ids = {t['activity_id'] for t in _detectar(velho + recentes)['Bike'][1200]['testes']}
        self.assertTrue(any(i.startswith('cBike') for i in ids))

    def test_mesma_data_conta_como_pontos_distintos(self):
        base = _n_curvas_1200('Bike', 6, 300)
        base += [_curva('dia_a', 'Bike', 2, w1200=300), _curva('dia_b', 'Bike', 2, w1200=300)]
        t = _detectar(base)['Bike'][1200]['testes']
        self.assertEqual([x['activity_id'] for x in t if x['date'] == _data(2)], ['dia_a', 'dia_b'])


class LimiaresDeRobustezCoberturaEAtraso(unittest.TestCase):
    def _estado(self, n_curvas):
        det = _detectar(_n_curvas_1200('Bike', n_curvas))
        return det, P.robustez(det)['Bike']

    def test_robustez_30_e_12(self):
        # n_curvas - 2 = n_testes
        for n_curvas, n_testes, estado in ((32, 30, 'bom'), (31, 29, 'parcial'),
                                           (14, 12, 'parcial'), (13, 11, 'insuficiente')):
            _, r = self._estado(n_curvas)
            self.assertEqual((r['n'], r['estado']), (n_testes, estado), n_testes)

    def test_cobertura_total_e_chaves(self):
        det = _detectar(_n_curvas_1200('Bike', 32) + _n_curvas_1200('Row', 12))
        cob = P.cobertura(det)
        self.assertEqual(set(cob), {'total', 'por_modalidade', 'veredicto'})
        self.assertEqual(cob['total'], 30 + 10)
        self.assertEqual(set(cob['por_modalidade']), {'Bike', 'Row'})
        self.assertEqual(set(cob['por_modalidade']['Bike']['20 min max']),
                         {'n', 'ultimo', 'dias', 'em_atraso'})
        self.assertEqual(cob['por_modalidade']['Row']['20 min max']['n'], 10)

    def test_cobertura_veredicto_global_por_total_30_e_10(self):
        # caracteriza o limiar GLOBAL atual (30/10): o texto muda entre faixas
        def v(n_curvas):
            return P.cobertura(_detectar(_n_curvas_1200('Bike', n_curvas)))['veredicto']
        self.assertIn('ha testes suficientes', v(32))        # 30 testes
        self.assertIn('da para ver tendencia', v(31))        # 29
        self.assertIn('da para ver tendencia', v(12))        # 10
        self.assertIn('so 9 testes', v(11))                  # 9

    def test_atraso_urgencia_e_filtro_de_modalidades(self):
        det = _detectar(_n_curvas_1200('Bike', 8, rec=40) + _n_curvas_1200('Row', 8, rec=90))
        with mock.patch.object(P, 'datetime', _Relogio):
            sug = P.sugerir(det)
        self.assertEqual([(s['modalidade'], s['dias_desde_ultimo'], s['atraso_dias'], s['urgencia'])
                          for s in sug], [('Row', 90, 62, 'alta'), ('Bike', 40, 12, 'media')])
        self.assertEqual(set(sug[0]), {'modalidade', 'secs', 'nome', 'mede', 'dias_desde_ultimo',
                                       'atraso_dias', 'ultimo_valor', 'ultima_data', 'urgencia'})

    def test_sem_atraso_dentro_do_intervalo(self):
        det = _detectar(_n_curvas_1200('Bike', 8, rec=28))
        self.assertFalse(det['Bike'][1200]['em_atraso'])        # 28 d == intervalo
        det = _detectar(_n_curvas_1200('Bike', 8, rec=29))
        self.assertTrue(det['Bike'][1200]['em_atraso'])


class ContratoAtualDaAncora(unittest.TestCase):
    """calibrar_com_ancora hoje: lag=valor devolvido por calibrar_lag, inclusive
    no fallback; minimo de 25; sobrevive = p_permutacao < 0.05."""

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
    def _serie(n=400):
        ini = datetime(2025, 1, 1)
        return [{'date': (ini + timedelta(days=i)).strftime('%Y-%m-%d'), 'load': 50.0}
                for i in range(n)]

    @staticmethod
    def _curvas(tipo, n, ini=datetime(2025, 1, 1)):
        return [{'activity_id': f'{tipo}{i}', 'type': tipo,
                 'date': (ini + timedelta(days=5 + 10 * i)).strftime('%Y-%m-%d'),
                 'secs': [60, 300, 1200], 'watts': [None, None, 300.0]} for i in range(n)]

    def _correr(self, curvas_por_mod, resposta_lag, mods=('Bike',)):
        pmc, cal, db = self.pmc, self.cal, self.db
        with mock.patch.object(db, 'load_power_curves',
                               side_effect=lambda tipo=None, desde=None: curvas_por_mod.get(tipo, [])), \
                mock.patch.object(cal, 'calibrar_lag', return_value=resposta_lag):
            return pmc.calibrar_com_ancora(self._serie(), list(mods))

    def test_minimo_por_omissao_e_25(self):
        self.assertEqual(inspect.signature(self.pmc.calibrar_com_ancora)
                         .parameters['minimo_testes'].default, 25)

    def test_p_minimo_e_lag_de_referencia(self):
        self.assertEqual(self.cal.P_MINIMO, 0.10)
        self.assertEqual(self.cal.REFERENCIA['lag_super'], 17)

    def test_amostra_insuficiente_so_n_e_motivo(self):
        r = self._correr({'Bike': self._curvas('Bike', 20)}, {})
        e = r['por_modalidade']['Bike']
        self.assertEqual(e['n_testes'], 18)
        self.assertIn('precisa de 25', e['motivo'])
        self.assertNotIn('lag', e)

    def test_fallback_hoje_devolve_lag_de_referencia_sem_r(self):
        fallback = {'fonte': 'referencia', 'valor': 17, 'motivo': 'nenhum lag deu correlacao com p < 0.1',
                    'testados': []}
        e = self._correr({'Bike': self._curvas('Bike', 32)}, fallback)['por_modalidade']['Bike']
        self.assertEqual(e['n_testes'], 30)
        self.assertEqual((e['lag'], e['r'], e['r2'], e['p_permutacao']), (17, None, None, None))
        self.assertFalse(e['sobrevive'])

    def test_sobrevive_exige_p_permutacao_abaixo_de_005(self):
        def dados(p):
            return {'fonte': 'dados', 'valor': 14, 'r': 0.42, 'r2': 0.1764, 'p_permutacao': p, 'n': 30,
                    'testados': [], 'forca': 'moderada', 'distribuicao_nula': {}}
        curvas = {'Bike': self._curvas('Bike', 32)}
        self.assertTrue(self._correr(curvas, dados(0.049))['por_modalidade']['Bike']['sobrevive'])
        self.assertFalse(self._correr(curvas, dados(0.05))['por_modalidade']['Bike']['sobrevive'])
        self.assertFalse(self._correr(curvas, dados(0.298))['por_modalidade']['Bike']['sobrevive'])

    def test_a_ancora_nao_altera_parametros_do_modelo(self):
        # so devolve um dicionario de diagnostico; nao ha estado partilhado a mudar
        antes = dict(self.cal.REFERENCIA)
        self._correr({'Bike': self._curvas('Bike', 32)},
                     {'fonte': 'referencia', 'valor': 17, 'motivo': 'x', 'testados': []})
        self.assertEqual(self.cal.REFERENCIA, antes)

    def test_serie_ancora_usa_todos_os_testes_detetados(self):
        det = _detectar(_n_curvas_1200('Bike', 8))
        s = P.serie_ancora(det, 'Bike', 1200)
        self.assertEqual(len(s), 6)
        self.assertEqual(set(s[0]), {'date', 'valor', 'modalidade', 'activity_id'})


if __name__ == '__main__':
    unittest.main()
