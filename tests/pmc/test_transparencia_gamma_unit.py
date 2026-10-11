"""Testes da transparencia do gamma (metadados de calcular_ftlm).

Dados sinteticos, sem rede, sem base de dados. Correm com:
    python -m unittest tests.pmc.test_transparencia_gamma_unit -v

Verifica:
  - origem 'padrao' quando o ajuste e rejeitado, com gamma aplicado = 0.35;
  - origem 'calibracao' quando aceito, com gamma aplicado = gamma encontrado;
  - r2_aplicado e o R2 da curva do gamma efetivamente aplicado (nao o do candidato);
  - campos pre-existentes continuam presentes (compatibilidade).
"""
import datetime as dt
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
import ftlm  # noqa: E402
import pmc  # noqa: E402

CAMPOS_ANTIGOS = ('gamma', 'r2', 'n', 'gamma_encontrado', 'aceite',
                  'na_fronteira', 'p_permutacao', 'motivo')


def _datas(n):
    d0 = dt.date(2024, 1, 1)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def _montar(cp_diario, carga, hrv=None, seed=0):
    """cp_diario: array com CP por dia (NaN onde nao ha sessao)."""
    n = len(carga)
    datas = _datas(n)
    serie = [{'date': d, 'load': float(c)} for d, c in zip(datas, carga)]
    sessoes = []
    for i, d in enumerate(datas):
        if np.isfinite(cp_diario[i]):
            sessoes.append({'id': str(i), 'date': d, 'type': 'Bike', 'tl': 60.0,
                            'cp': float(cp_diario[i])})
    rng = np.random.default_rng(seed)
    if hrv is None:
        hrv = 60.0 + rng.normal(0, 5, n)
    wellness = [{'date': d, 'hrv': float(h), 'stress': 3.0, 'soreness': 3.0,
                 'fatiga': 3.0, 'sleep_quality': 3.0} for d, h in zip(datas, hrv)]
    return serie, sessoes, wellness


class TransparenciaGamma(unittest.TestCase):

    def test_ajuste_rejeitado_aplica_padrao_e_r2_do_padrao(self):
        # CP sem relacao com a carga: ajuste deve ser rejeitado.
        rng = np.random.default_rng(3)
        n = 500
        carga = np.abs(rng.normal(60, 25, n))
        cp = np.full(n, np.nan)
        dias = np.arange(0, n, 2)
        cp[dias] = 200 + rng.normal(0, 5, len(dias))
        serie, sessoes, wellness = _montar(cp, carga, seed=3)
        out = pmc.calcular_ftlm(sessoes, wellness, serie, ['Bike'])
        p = out['gammas']['perf']
        self.assertFalse(p['aceite'])
        self.assertEqual(p['gamma'], ftlm.GAMMA_DEFAULT)
        self.assertEqual(p['origem'], 'padrao')
        self.assertIsNotNone(p['gamma_encontrado'])
        self.assertTrue(p['motivo'])          # o motivo da rejeicao existe
        # R2 mostrado = R2 da curva do gamma aplicado (0.35), nao o do candidato
        # Ajuste calculado diretamente com a mesma convencao de fit_gamma.
        a = ftlm._media_movel(np.asarray(cp, float), 3)
        ctl = ftlm.ftlm_fractional(np.asarray(carga, float), ftlm.GAMMA_DEFAULT,
                                   min(365, n))
        r2_padrao = round(float(ftlm._r2(ctl, a)), 4)
        self.assertEqual(p['r2_aplicado'], r2_padrao)
        self.assertEqual(p['lag'], 0)
        self.assertEqual(p['suavizacao'], 3)

    def test_ajuste_aceito_usa_gamma_encontrado_e_r2_igual(self):
        # CP gerada a partir de CTL com gamma 0.5 + ruido pequeno: deve ser aceito.
        rng = np.random.default_rng(7)
        n = 600
        carga = np.abs(rng.normal(60, 25, n))
        ctl = ftlm.ftlm_fractional(carga, 0.5, min(365, n))
        cp_sinal = 200 + 3.0 * ctl + rng.normal(0, 1.0, n)
        cp = np.full(n, np.nan)
        dias = np.arange(0, n, 2)
        cp[dias] = cp_sinal[dias]
        serie, sessoes, wellness = _montar(cp, carga, seed=7)
        out = pmc.calcular_ftlm(sessoes, wellness, serie, ['Bike'])
        p = out['gammas']['perf']
        self.assertTrue(p['aceite'], msg=p.get('motivo'))
        self.assertEqual(p['origem'], 'calibracao')
        self.assertEqual(p['gamma'], p['gamma_encontrado'])
        self.assertEqual(p['r2_aplicado'], p['r2'])

    def test_campos_antigos_preservados(self):
        rng = np.random.default_rng(11)
        n = 300
        carga = np.abs(rng.normal(60, 25, n))
        cp = np.full(n, np.nan)
        cp[::2] = 200 + rng.normal(0, 5, len(cp[::2]))
        serie, sessoes, wellness = _montar(cp, carga, seed=11)
        out = pmc.calcular_ftlm(sessoes, wellness, serie, ['Bike'])
        for bloco in (out['gammas']['perf'], out['gammas']['rec']):
            for campo in CAMPOS_ANTIGOS:
                self.assertIn(campo, bloco)
        mod = out['por_modalidade']['Bike']
        for campo in ('gamma', 'r2', 'n', 'gamma_fit', 'ctlg_pct', 'fase'):
            self.assertIn(campo, mod)

    def test_metadados_novos_presentes_em_gammas_e_modalidades(self):
        rng = np.random.default_rng(13)
        n = 300
        carga = np.abs(rng.normal(60, 25, n))
        cp = np.full(n, np.nan)
        cp[::2] = 200 + rng.normal(0, 5, len(cp[::2]))
        serie, sessoes, wellness = _montar(cp, carga, seed=13)
        out = pmc.calcular_ftlm(sessoes, wellness, serie, ['Bike'])
        for bloco, lag, suav in ((out['gammas']['perf'], 0, 3),
                                 (out['gammas']['rec'], 1, 0),
                                 (out['por_modalidade']['Bike'], 0, 3)):
            self.assertIn(bloco['origem'], ('calibracao', 'padrao'))
            self.assertEqual(bloco['lag'], lag)
            self.assertEqual(bloco['suavizacao'], suav)
            self.assertIn('r2_aplicado', bloco)

    def test_r2_gamma_aplicado_coincide_com_fit_gamma_no_mesmo_gamma(self):
        rng = np.random.default_rng(5)
        n = 400
        carga = np.abs(rng.normal(60, 25, n))
        alvo = np.full(n, np.nan)
        alvo[::2] = 3.0 * ftlm.ftlm_fractional(carga, 0.4, n)[::2] + rng.normal(0, 4, len(alvo[::2]))
        for lag, suav in ((0, 3), (1, 0)):
            f = ftlm.fit_gamma(carga, alvo, lag=lag, suavizar=suav,
                               max_lag=min(365, n), permutacoes=0)
            ctl = ftlm.ftlm_fractional(carga, f['gamma_encontrado'], min(365, n))
            r2 = pmc._r2_gamma_aplicado(ctl, alvo, lag, suav)
            self.assertEqual(r2, f['r2'])


if __name__ == '__main__':
    unittest.main()
