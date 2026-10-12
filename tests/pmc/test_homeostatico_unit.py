"""Testes unitarios do modelo homeostatico e da suavizacao em pmc.py.

    python -m unittest tests.pmc.test_homeostatico_unit -v

Cobre: recuperacao de parametros com dados sinteticos exactos, formula de p_hat,
dados insuficientes, suavizacao e a causalidade (defeito M3, corrigir na Fase 2.5).
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
import ftlm  # noqa: E402
import pmc  # noqa: E402


def _fixture_exacto(dias=420, t1=45.0, t2=7.0, k1=1.5, k2=1.0, p0=200.0, passo_teste=2, seed=2):
    rng = np.random.RandomState(seed)
    base = datetime(2025, 1, 1)
    datas = [(base + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(dias)]
    loads = 40 + 30 * rng.rand(dias)
    serie = [{'date': d, 'load': float(l)} for d, l in zip(datas, loads)]
    e1 = ftlm.ewm(loads, t1)
    e2 = ftlm.ewm(loads, t2)
    cp = p0 + k1 * e1 - k2 * e2
    sessoes = []
    for i in range(0, dias, passo_teste):
        sessoes.append({'id': f'c{i}', 'date': datas[i], 'type': 'Bike', 'cp': float(cp[i])})
    return serie, sessoes, datas, cp


class RecuperacaoDeParametros(unittest.TestCase):

    def test_recupera_t1_t2_k1_k2_exactos(self):
        serie, sessoes, _d, _cp = _fixture_exacto()
        r = pmc.modelo_homeostatico(serie, sessoes)
        self.assertTrue(r['ajustado'], r['motivo'])
        self.assertEqual(r['t1'], 45.0)
        self.assertEqual(r['t2'], 7.0)
        self.assertAlmostEqual(r['k1'], 1.5, places=2)
        self.assertAlmostEqual(r['k2'], 1.0, places=2)
        self.assertAlmostEqual(r['p0'], 200.0, places=0)
        self.assertGreater(r['r2'], 0.9999)

    def test_p_hat_segue_a_formula(self):
        serie, sessoes, _d, _cp = _fixture_exacto()
        r = pmc.modelo_homeostatico(serie, sessoes)
        loads = np.array([s['load'] for s in serie])
        p_ref = r['p0'] + r['k1'] * ftlm.ewm(loads, r['t1']) - r['k2'] * ftlm.ewm(loads, r['t2'])
        p_saida = np.array([s['p_hat'] for s in r['serie']])
        np.testing.assert_allclose(p_saida, p_ref, atol=0.06)  # arredondado a 1 casa

    def test_poucos_pontos_de_cp_nao_ajusta(self):
        serie, sessoes, _d, _cp = _fixture_exacto()
        r = pmc.modelo_homeostatico(serie, sessoes[:5])
        self.assertFalse(r['ajustado'])
        self.assertEqual(r['motivo'], 'poucos_pontos_cp')
        self.assertEqual((r['t1'], r['t2']), (42.0, 7.0))
        self.assertEqual(r['n_testes'], 5)


class SuavizacaoRetrospectiva(unittest.TestCase):

    def test_savgol_preserva_polinomio_de_grau_3_no_interior(self):
        x = np.arange(200, dtype=float)
        y = 0.001 * x ** 3 - 0.2 * x ** 2 + 3 * x + 10
        s = pmc._savgol(y, 21, 3)
        np.testing.assert_allclose(s[15:-15], y[15:-15], rtol=1e-6, atol=1e-6)

    def test_savgol_atual_e_nao_causal_M3(self):
        """Caracteriza o defeito M3: a versao actual usa dados futuros.

        Este teste PASSA no codigo original, porque documenta a dependencia.
        Serve para que a versao causal seja comparada com esta.
        """
        rng = np.random.RandomState(4)
        y = np.cumsum(rng.normal(size=300))
        completo = pmc._savgol(y, 21, 3)
        parcial = pmc._savgol(y[:200], 21, 3)
        self.assertFalse(np.allclose(completo[150:200], parcial[150:200]))


class SuavizacaoCausal(unittest.TestCase):
    """Versao causal (Fase 2.5). Falha no codigo original: a funcao nao existe."""

    def test_savgol_causal_existe_e_ignora_o_futuro(self):
        rng = np.random.RandomState(4)
        y = np.cumsum(rng.normal(size=300))
        completo = pmc._savgol_causal(y, 21, 3)
        parcial = pmc._savgol_causal(y[:200], 21, 3)
        np.testing.assert_allclose(completo[:200], parcial, rtol=0, atol=1e-10, equal_nan=True)

    def test_banda_sd_causal_ignora_o_futuro(self):
        rng = np.random.RandomState(6)
        y = np.cumsum(rng.normal(size=300))
        completo = pmc._banda_sd_causal(y, 14)
        parcial = pmc._banda_sd_causal(y[:200], 14)
        np.testing.assert_allclose(completo[:200], parcial, rtol=0, atol=1e-10)

    def test_savgol_causal_segue_tendencia_linear(self):
        # polinomio de grau 3 sem ruido: a versao causal tem de o reproduzir
        # no fim (sem atraso de meia janela, que e o que a centrada evita)
        x = np.arange(120, dtype=float)
        y = 2.0 * x + 5.0
        s = pmc._savgol_causal(y, 21, 3)
        np.testing.assert_allclose(s[30:], y[30:], rtol=1e-6, atol=1e-6)

    def test_modelo_expoe_suavizacao_causal_sem_mudar_o_ajuste(self):
        serie, sessoes, _d, _cp = _fixture_exacto()
        r = pmc.modelo_homeostatico(serie, sessoes)
        self.assertIn('p_hat_suave_causal', r['serie'][0])
        self.assertEqual(len(r['serie']), len(serie))
        # a suavizacao retrospectiva continua presente, sem alteracao
        self.assertIn('p_hat_suave', r['serie'][0])


class BordasDaSuavizacaoCausal(unittest.TestCase):
    """Series curtas, valores ausentes e limites da janela da versao causal."""

    def test_serie_curta_devolve_o_proprio_valor(self):
        y = np.array([1.0, 2.0, 3.0])            # menos de grau+2 = 5 pontos
        np.testing.assert_allclose(pmc._savgol_causal(y, 21, 3), y)

    def test_serie_vazia(self):
        self.assertEqual(len(pmc._savgol_causal(np.array([]), 21, 3)), 0)
        self.assertEqual(len(pmc._banda_sd_causal(np.array([]), 14)), 0)

    def test_inicio_da_serie_usa_so_o_que_existe(self):
        x = np.arange(60, dtype=float)
        y = 2.0 * x + 5.0
        s = pmc._savgol_causal(y, 21, 3)
        self.assertAlmostEqual(s[0], y[0])        # 1 ponto: devolve o proprio valor
        self.assertAlmostEqual(s[3], y[3], places=6)  # 4 pontos, ainda abaixo de 5

    def test_limite_da_janela_savgol(self):
        """O dia i so depende de y[i-20..i] (janela 21)."""
        rng = np.random.RandomState(9)
        y = np.cumsum(rng.normal(size=120))
        base = pmc._savgol_causal(y, 21, 3)
        y_fora = y.copy(); y_fora[50 - 21] += 1000.0     # fora da janela de i=50
        np.testing.assert_allclose(pmc._savgol_causal(y_fora, 21, 3)[50], base[50])
        y_dentro = y.copy(); y_dentro[50 - 20] += 1000.0  # dentro da janela
        self.assertNotAlmostEqual(pmc._savgol_causal(y_dentro, 21, 3)[50], base[50])

    def test_limite_da_janela_banda(self):
        """A banda do dia i so depende de y[i-13..i] (janela 14)."""
        rng = np.random.RandomState(10)
        y = np.cumsum(rng.normal(size=120))
        base = pmc._banda_sd_causal(y, 14)
        y_fora = y.copy(); y_fora[50 - 14] += 1000.0
        np.testing.assert_allclose(pmc._banda_sd_causal(y_fora, 14)[50], base[50])
        y_dentro = y.copy(); y_dentro[50 - 13] += 1000.0
        self.assertNotAlmostEqual(pmc._banda_sd_causal(y_dentro, 14)[50], base[50])

    def test_nan_no_meio_nao_contamina_os_dias_seguintes(self):
        x = np.arange(120, dtype=float)
        y = 2.0 * x + 5.0
        y[40] = np.nan
        s = pmc._savgol_causal(y, 21, 3)
        self.assertTrue(np.isfinite(s[41:]).all())
        np.testing.assert_allclose(s[70:], y[70:], rtol=1e-6, atol=1e-6)

    def test_banda_com_menos_de_3_pontos_finitos_e_zero(self):
        y = np.array([1.0, np.nan, 3.0])          # 2 pontos finitos
        np.testing.assert_allclose(pmc._banda_sd_causal(y, 14), [0.0, 0.0, 0.0])

    def test_campos_causais_do_modelo_dependem_so_do_passado(self):
        """Com o p_hat ja calculado pelo modelo, a suavizacao no dia k so usa P[:k+1]."""
        serie, sessoes, _d, _cp = _fixture_exacto()
        r = pmc.modelo_homeostatico(serie, sessoes)
        P = np.array([s['p_hat'] for s in r['serie']], dtype=float)
        completo = pmc._savgol_causal(P, 21, 3)
        for k in (60, 150, 300):
            parcial = pmc._savgol_causal(P[:k], 21, 3)
            np.testing.assert_allclose(parcial, completo[:k], rtol=0, atol=1e-9)
        for k in (60, 150, 300):
            self.assertAlmostEqual(r['serie'][k]['p_hat_suave_causal'],
                                   round(float(completo[k]), 1), places=6)


if __name__ == '__main__':
    unittest.main()
