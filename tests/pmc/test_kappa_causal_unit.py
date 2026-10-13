"""Causalidade do κ exibido na UI (ftlm.kappa_fmt com normalização causal).

Regra testada: o valor em t só depende de dados até t. Acrescentar dados
posteriores não pode alterar nenhum valor anterior ao corte.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import ftlm  # noqa: E402


def _series(n, seed, dims=3):
    rng = np.random.default_rng(seed)
    base = [rng.normal(0, 1, n).cumsum() for _ in range(dims)]
    for s in base:
        s[rng.integers(0, n, n // 10)] = np.nan
    return base


class KappaCausalPrefixo(unittest.TestCase):
    def test_acrescentar_futuro_nao_altera_passado(self):
        for seed in (1, 2, 3):
            full = _series(400, seed)
            k_full, l_full = ftlm.kappa_fmt(full)
            for corte in (120, 250, 330):
                cut = [s[:corte] for s in full]
                k_cut, l_cut = ftlm.kappa_fmt(cut)
                a, b = k_full[:corte], k_cut
                ambos = np.isfinite(a) & np.isfinite(b)
                self.assertTrue(np.array_equal(np.isnan(a), np.isnan(b)),
                                f'seed {seed} corte {corte}: NaN mudou')
                self.assertTrue(np.array_equal(a[ambos], b[ambos]),
                                f'seed {seed} corte {corte}: valor mudou')
                a, b = l_full[:corte], l_cut
                ambos = np.isfinite(a) & np.isfinite(b)
                self.assertTrue(np.array_equal(a[ambos], b[ambos]))

    def test_causal_false_mantem_comportamento_global(self):
        s = _series(200, 7, dims=2)
        k_causal, _ = ftlm.kappa_fmt(s, causal=True)
        k_glob, _ = ftlm.kappa_fmt(s, causal=False)
        self.assertFalse(np.allclose(k_causal, k_glob, equal_nan=True))
        # o caminho global continua a usar z sobre a série inteira
        self.assertTrue(np.isfinite(k_glob).sum() > 0)


class ZScoreCausalBordas(unittest.TestCase):
    def test_aquecimento_ate_minimo(self):
        z = ftlm._zscore_causal(np.arange(20, dtype=float), minimo=10)
        self.assertTrue(np.all(np.isnan(z[:9])))
        self.assertTrue(np.isfinite(z[9:]).all())

    def test_valores_fechados(self):
        a = np.array([1., 2., 3., 4., 5., 6., 7., 8., 9., 10.])
        z = ftlm._zscore_causal(a, minimo=10)
        ref = (a[-1] - a.mean()) / a.std()
        self.assertAlmostEqual(z[-1], ref, places=9)

    def test_serie_constante_nao_quebra(self):
        z = ftlm._zscore_causal(np.full(30, 5.0), minimo=10)
        self.assertTrue(np.all(np.nan_to_num(z[10:]) == 0.0))

    def test_serie_curta_so_nan(self):
        z = ftlm._zscore_causal(np.arange(5, dtype=float), minimo=10)
        self.assertTrue(np.all(np.isnan(z)))

    def test_vazia(self):
        self.assertEqual(len(ftlm._zscore_causal(np.array([], dtype=float))), 0)

    def test_nan_no_meio_nao_entra_na_media(self):
        a = np.arange(30, dtype=float)
        a2 = a.copy()
        a2[15] = np.nan
        z1 = ftlm._zscore_causal(a)
        z2 = ftlm._zscore_causal(a2)
        self.assertTrue(np.isnan(z2[15]))
        self.assertTrue(np.isfinite(z2[29]))


class KappaBordas(unittest.TestCase):
    def test_lista_vazia(self):
        k, l = ftlm.kappa_fmt([])
        self.assertEqual(len(k), 0)

    def test_serie_curta_nao_levanta(self):
        k, l = ftlm.kappa_fmt([np.arange(15.0), np.arange(15.0) ** 1.1])
        self.assertEqual(len(k), 15)
        self.assertTrue(np.all(np.isnan(k)))

    def test_dimensao_constante_nao_levanta(self):
        s = _series(120, 11, dims=2)
        s.append(np.full(120, 3.0))
        k, l = ftlm.kappa_fmt(s)
        self.assertEqual(len(k), 120)


if __name__ == '__main__':
    unittest.main()
