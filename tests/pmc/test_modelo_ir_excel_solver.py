"""Diagnostico do IR frente ao Solver da folha EstimarConstantes (so leitura; nao altera o modelo)."""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.path.insert(0, ROOT)
import ir_diagnostico as dg  # noqa: E402
import modelo_ir as ir  # noqa: E402


class ExcelSolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.xl = dg.folha()

    def test_1_sse_da_folha_reproduzido_com_lag_0(self):
        p = self.xl['parametros']
        idx = sorted(int(k) for k in self.xl['testes'])
        e = ir.erros(self.xl['tss'], idx, [self.xl['testes'][str(i)] for i in idx], p, 0)
        self.assertAlmostEqual(float(e @ e), self.xl['soma_quadrados_excel'], places=6)

    def test_2_otimo_sob_as_restricoes_do_solver_e_o_da_folha(self):
        o = dg.otimo_sob_restricoes_da_folha(self.xl)
        self.assertAlmostEqual(o['sse'], self.xl['soma_quadrados_excel'], places=3)
        # a folha esta nos limites do Solver (T1=25, T2=10): nao ha minimo interior
        self.assertEqual(o['t1'], 25.0)
        self.assertEqual(o['t2'], 10.0)

    def test_3_um_teste_domina_o_sse_da_folha(self):
        self.assertGreater(dg.peso_do_teste(self.xl, 113), 0.6)

    def test_4_simulacao_snr_alto_recupera_e_snr_baixo_nao_e_identificavel(self):
        cargas = dg.carga_sintetica()
        alto = dg.simular_um(cargas, 'A', n=120, amplitude=40.0, ruido=2.0, semente=1)
        self.assertEqual(alto['estado'][:6], 'ajusta')
        baixo = dg.simular_um(cargas, 'A', n=30, amplitude=5.0, ruido=15.0, semente=1)
        self.assertNotEqual(baixo.get('estado'), 'ajustado_identificavel')


if __name__ == '__main__':
    unittest.main()
