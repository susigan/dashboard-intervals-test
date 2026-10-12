"""Selecao do CP do PMC sem curva aceita, e leitura do eFTP.

Sem rede e sem base real. Testa as funcoes puras de tabs/tab_pmc.py.
"""
import math
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class _Carrega(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tabs.tab_pmc as tp
        except BaseException as e:  # config.py faz sys.exit sem chave da API
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        cls.tp = tp


class CpPrecedencia(_Carrega):

    def test_icu_pm_cp_valido_e_escolhido(self):
        a = {'icu_pm_cp': 260, 'icu_pm_ftp': 250}
        self.assertEqual(self.tp.cp_bruto_da_atividade(a), 260.0)

    def test_icu_pm_cp_zero_cai_para_icu_pm_ftp(self):
        self.assertEqual(self.tp.cp_bruto_da_atividade(
            {'icu_pm_cp': 0, 'icu_pm_ftp': 250}), 250.0)

    def test_rolling_nunca_e_usado_como_cp(self):
        # o caso dos 313 fallbacks: so existe icu_rolling_ftp
        self.assertIsNone(self.tp.cp_bruto_da_atividade(
            {'icu_pm_cp': None, 'icu_pm_ftp': None, 'icu_rolling_ftp': 227}))

    def test_eftp_e_icu_ftp_nao_sao_cp(self):
        self.assertIsNone(self.tp.cp_bruto_da_atividade(
            {'EFTP': 228, 'icu_ftp': 251}))

    def test_sessao_antiga_sem_campos_fica_sem_cp(self):
        self.assertIsNone(self.tp.cp_bruto_da_atividade({}))


class CpValoresInvalidos(_Carrega):

    def test_zero_e_negativo_sao_recusados(self):
        for v in (0, 0.0, -5, -0.1):
            with self.subTest(v=v):
                self.assertIsNone(self.tp.cp_bruto_da_atividade({'icu_pm_cp': v}))

    def test_nan_e_infinito_sao_recusados(self):
        for v in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(v=v):
                self.assertIsNone(self.tp.cp_bruto_da_atividade({'icu_pm_cp': v}))

    def test_nan_em_icu_pm_cp_cai_para_icu_pm_ftp(self):
        self.assertEqual(self.tp.cp_bruto_da_atividade(
            {'icu_pm_cp': float('nan'), 'icu_pm_ftp': 240}), 240.0)

    def test_string_e_booleano_sao_recusados(self):
        self.assertIsNone(self.tp.cp_bruto_da_atividade({'icu_pm_cp': '150'}))
        self.assertIsNone(self.tp.cp_bruto_da_atividade({'icu_pm_cp': True}))
        self.assertIsNone(self.tp.cp_bruto_da_atividade({'icu_pm_cp': False}))

    def test_resultado_nunca_e_nao_finito_ou_nao_positivo(self):
        valores = [None, 0, -3, float('nan'), float('inf'), '', 'x', True, 1e-9, 180]
        for v in valores:
            for campo in ('icu_pm_cp', 'icu_pm_ftp'):
                with self.subTest(v=v, campo=campo):
                    r = self.tp.cp_bruto_da_atividade({campo: v})
                    if r is not None:
                        self.assertTrue(math.isfinite(r))
                        self.assertGreater(r, 0)

    def test_valor_baixo_mas_positivo_nao_e_filtrado_por_limiar(self):
        # sem limiar de plausibilidade justificado nos dados: 1 W passa.
        # Os dois casos de 1 W conhecidos vinham do rolling, que ja nao entra.
        self.assertEqual(self.tp.cp_bruto_da_atividade({'icu_pm_cp': 1}), 1.0)


class EftpLeitura(_Carrega):

    def test_le_a_chave_real_EFTP(self):
        self.assertEqual(self.tp.eftp_da_atividade({'EFTP': 255}), 255.0)

    def test_nao_le_a_chave_inexistente_icu_eftp(self):
        self.assertEqual(self.tp.eftp_da_atividade({'icu_eftp': 999}), 0.0)

    def test_ausente_ou_zero_ficam_em_zero(self):
        self.assertEqual(self.tp.eftp_da_atividade({}), 0.0)
        self.assertEqual(self.tp.eftp_da_atividade({'EFTP': 0}), 0.0)


class DoisPontosUsamAMesmaRegra(_Carrega):

    def test_nenhum_ponto_recalcula_a_cadeia_com_rolling(self):
        with open(os.path.join(ROOT, 'tabs', 'tab_pmc.py'), encoding='utf-8') as f:
            origem = f.read()
        self.assertNotIn("num(a.get('icu_rolling_ftp'))", origem)
        self.assertNotIn("num(a.get('icu_eftp'))", origem)
        self.assertGreaterEqual(origem.count('cp_bruto_da_atividade(a)'), 2)


if __name__ == '__main__':
    unittest.main()
