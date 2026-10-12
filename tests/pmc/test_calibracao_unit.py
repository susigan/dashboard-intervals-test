"""Testes unitarios de calibracao.py e da entrada de HRV da calibracao (pmc.calcular_fmt).

    python -m unittest tests.pmc.test_calibracao_unit -v

Cobre: detrending, alinhamento temporal por lag, tratamento de NaN (HRV ausente)
e o defeito M1: HRV interpolada a contar como observacao real.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
import calibracao as cal  # noqa: E402
import pmc  # noqa: E402


class SemTendencia(unittest.TestCase):

    def test_serie_linear_tem_residuo_zero_no_interior(self):
        # media centrada de uma recta e a propria recta; so as bordas (janela
        # truncada) deixam de bater certo, por isso so se testa o interior
        x = np.arange(400, dtype=float) * 0.5 + 3.0
        r = cal.sem_tendencia(x, janela=90)
        np.testing.assert_allclose(r[90:-90], 0.0, atol=1e-9)

    def test_serie_constante_fica_zero(self):
        r = cal.sem_tendencia(np.full(300, 7.0), janela=30)
        np.testing.assert_allclose(r[40:-40], 0.0, atol=1e-12)

    def test_nan_sao_preservados_como_nan(self):
        x = np.arange(200, dtype=float)
        x[50:55] = np.nan
        r = cal.sem_tendencia(x, janela=30)
        self.assertTrue(np.isnan(r[50:55]).all())


class Pearson(unittest.TestCase):

    def test_relacao_exacta(self):
        x = np.arange(30, dtype=float)
        r, p, n = cal._pearson(x, 2 * x + 1)
        self.assertAlmostEqual(r, 1.0, places=12)
        self.assertEqual(n, 30)

    def test_pares_com_nan_sao_descartados(self):
        x = np.arange(30, dtype=float)
        y = 2 * x + 1
        y[::3] = np.nan
        _r, _p, n = cal._pearson(x, y)
        self.assertEqual(n, 20)

    def test_poucos_pontos_devolve_none(self):
        r, p, n = cal._pearson([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        self.assertIsNone(r)


class CalibrarLag(unittest.TestCase):

    def test_recupera_lag_exacto_sem_detrending(self):
        rng = np.random.RandomState(4)
        x = rng.normal(size=300)
        y = np.full(300, np.nan)
        y[3:] = x[:-3]          # y[t] = x[t-3]
        r = cal.calibrar_lag(x, y, lags=range(0, 10), destendenciar=False)
        self.assertEqual(r['valor'], 3)
        self.assertAlmostEqual(r['r'], 1.0, places=9)

    def test_n_conta_so_pares_observados(self):
        """Com HRV em falta (NaN), o n da calibracao tem de ser o das observacoes reais."""
        rng = np.random.RandomState(8)
        x = rng.normal(size=300)
        y = np.roll(x, 2) + 0.01 * rng.normal(size=300)
        y[100:110] = np.nan
        r = cal.calibrar_lag(x, y, lags=[2], destendenciar=False)
        # y tem 290 valores reais. Com lag 2, t=0 e t=1 ficam sem x[t-2]:
        # 290 - 2 = 288 pares completos.
        self.assertEqual(r['testados'][0]['n'], 288)

    def test_alvo_com_menos_de_30_dias_nao_calibra(self):
        y = np.full(200, np.nan)
        y[:20] = 1.0
        r = cal.calibrar_lag(np.arange(200.0), y, lags=range(0, 5))
        self.assertEqual(r['fonte'], 'referencia')


class CalibrarTudoEntradas(unittest.TestCase):

    def test_calibrar_tudo_sem_hrv_usa_referencia(self):
        out = cal.calibrar_tudo(carga=np.arange(200.0), hrv_trend=None)
        self.assertEqual(out['canal1_tau']['fonte'], 'referencia')
        self.assertEqual(out['canal2_lag']['fonte'], 'referencia')


class M1HrvInterpoladaNaCalibracao(unittest.TestCase):
    """DEFEITO M1 (corrigir na Fase 2.4). Falha no codigo original.

    calcular_fmt interpola lacunas curtas de HRV e a calibracao recebe essa
    serie ja interpolada. Os dias preenchidos passam como observacoes reais.
    Teste: 200 dias com HRV, 5 em falta (lacunas de 2 e 3 dias, interpoladas).
    A calibracao so pode receber 195 valores de HRV reais.
    """

    def _fixture(self, dias=200, em_falta=(50, 51, 120, 121, 122)):
        rng = np.random.RandomState(12)
        base = datetime(2025, 1, 1)
        datas = [(base + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(dias)]
        sessoes, wellness = [], []
        for i, d in enumerate(datas):
            sessoes.append({'id': f's{i}', 'date': d, 'type': 'Bike',
                            'tl': float(30 + 20 * rng.rand()), 'cp': 250.0 + rng.normal(0, 2),
                            'w_prime': 15000.0 + rng.normal(0, 50)})
            wellness.append({
                'date': d,
                'hrv': None if i in em_falta else float(60 + rng.normal(0, 3)),
                'sleep_quality': float(rng.randint(1, 6)),
                'stress': float(rng.randint(1, 6)),
                'soreness': float(rng.randint(1, 6)),
                'fatiga': float(rng.randint(1, 6)),
            })
        return sessoes, wellness

    def test_calibracao_recebe_so_hrv_real(self):
        sessoes, wellness = self._fixture()
        serie = pmc.calcular(sessoes, 'tl')
        capturado = {}

        original = cal.calibrar_tudo

        def capturar(**kwargs):
            capturado.update(kwargs)
            return {}

        cal.calibrar_tudo = capturar
        try:
            pmc.calcular_fmt(sessoes, wellness, serie, desde_hrv=None)
        finally:
            cal.calibrar_tudo = original

        self.assertIn('hrv_trend', capturado)
        hrv = capturado['hrv_trend']
        self.assertIsNotNone(hrv)
        L = len(hrv)
        # a janela util da calibracao acaba no ultimo dia: confirma-se que
        # os L ultimos dias sao os que foram passados a calibracao
        ultimos = [w for w in wellness][-L:]
        reais = [w for w in ultimos if w['hrv'] is not None]
        self.assertEqual(int(np.isfinite(hrv).sum()), len(reais),
                         'a calibracao esta a contar dias interpolados como medicoes')
        # cada valor finito e exactamente uma medicao real, sem preenchimento
        esperado = np.array([np.log(w['hrv']) if w['hrv'] is not None else np.nan
                             for w in ultimos])
        np.testing.assert_allclose(hrv, esperado, rtol=0, atol=1e-12, equal_nan=True)

if __name__ == '__main__':
    unittest.main()
