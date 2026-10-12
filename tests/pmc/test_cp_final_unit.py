"""Validacao do CP final do PMC: piso, P(1 s) e fallback. Funcoes puras, sem rede.

    INTERVALS_ICU_API_KEY=teste-local python -m unittest tests.pmc.test_cp_final_unit -v

Cada caso diz qual regra testa. A regra C3 so rejeita a curva quando P(1 s) passa
de 2000 W E a curva diverge mais de 20% do icu_pm_cp; um pico isolado nao basta.
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


def _sessao(sid, tipo='Bike', icu=None, campo=None):
    return {'id': sid, 'type': tipo, 'date': '2026-06-01', 'cp': icu,
            'cp_campo': campo, 'w_prime': None}


class PisoEPlausibilidade(_Carrega):

    def test_cp_abaixo_do_piso_nao_e_valido_em_bike_row_ski_run(self):
        for tipo in ('Bike', 'Row', 'Ski', 'Run'):
            self.assertFalse(self.tp.cp_valido(27.4, tipo), tipo)

    def test_piso_nao_se_aplica_a_modalidade_sem_cp(self):
        self.assertTrue(self.tp.cp_valido(27.4, 'WeightTraining'))
        self.assertTrue(self.tp.cp_valido(27.4, None))

    def test_valores_nao_finitos_nao_positivos_e_booleanos_sao_recusados(self):
        for v in (None, True, False, float('nan'), float('inf'), 0, -5, 'abc'):
            self.assertFalse(self.tp.cp_valido(v, 'Bike'), repr(v))

    def test_limite_exacto_do_piso_passa(self):
        self.assertTrue(self.tp.cp_valido(self.tp.PISO_CP_W, 'Bike'))

    def test_bruto_cai_para_ftp_quando_icu_pm_cp_esta_abaixo_do_piso(self):
        a = {'icu_pm_cp': 27, 'icu_pm_ftp': 250}
        self.assertEqual(self.tp.cp_bruto_da_atividade(a, 'Bike'), 250.0)
        self.assertEqual(self.tp._campo_cp(a, 'Bike'), 'icu_pm_ftp')

    def test_bruto_ambos_abaixo_do_piso_da_none(self):
        a = {'icu_pm_cp': 27, 'icu_pm_ftp': 27}
        self.assertIsNone(self.tp.cp_bruto_da_atividade(a, 'Bike'))
        self.assertIsNone(self.tp._campo_cp(a, 'Bike'))


class CurvaSuspeita(_Carrega):
    """C3: rejeita so com P(1 s) > 2000 W E divergencia > 20% do icu_pm_cp."""

    def test_pico_isolado_sem_divergencia_nao_rejeita(self):
        # sprint real: P(1 s) alto mas a curva concorda com a fonte
        self.assertFalse(self.tp.curva_suspeita(200, 2500, 205))

    def test_divergencia_sem_pico_nao_rejeita_por_este_criterio(self):
        self.assertFalse(self.tp.curva_suspeita(144, 1500, 215))

    def test_pico_mais_divergencia_rejeita(self):
        # caso i161612050: P(1 s)=3791 W, curva 144 W, icu_pm_cp 215 W
        self.assertTrue(self.tp.curva_suspeita(144.1, 3791, 215))

    def test_sem_icu_nao_ha_confirmacao_e_nao_rejeita(self):
        self.assertFalse(self.tp.curva_suspeita(144.1, 3791, None))

    def test_sem_p1_nao_rejeita(self):
        self.assertFalse(self.tp.curva_suspeita(144.1, None, 215))

    def test_limite_p1_exacto_nao_rejeita(self):
        self.assertFalse(self.tp.curva_suspeita(100, 2000, 200))


class SelecaoFinal(_Carrega):

    def _run(self, sessoes, curvas):
        cont = self.tp.selecionar_cp(sessoes, curvas)
        return cont

    def test_curva_valida_vence_e_conta_da_curva(self):
        s = [_sessao('a', icu=260, campo='icu_pm_cp')]
        c = {'a': {'cp': 255.0, 'w_prime': 18000, 'p1': 900, 'r2': 0.95}}
        cont = self._run(s, c)
        self.assertEqual(s[0]['cp'], 255.0)
        self.assertEqual(cont['da_curva'], 1)

    def test_curva_abaixo_do_piso_cai_para_icu_que_tambem_falha_e_da_none(self):
        # caso i18885606: curva 27.4 e icu_pm_cp 27 -> sem CP, nunca zero
        s = [_sessao('i18885606', icu=27, campo='icu_pm_cp')]
        s[0]['cp'] = None   # icu ja recusado pelo piso em cp_bruto_da_atividade
        c = {'i18885606': {'cp': 27.4, 'w_prime': 2129, 'p1': 92, 'r2': 0.953}}
        cont = self._run(s, c)
        self.assertIsNone(s[0]['cp'])
        self.assertEqual(cont['curva_rejeitada_piso'], 1)
        self.assertEqual(cont['sem_cp'], 1)
        self.assertEqual(cont['da_curva'], 0)

    def test_curva_suspeita_cai_para_icu_pm_cp(self):
        # caso i161612050: curva 144 com P(1 s) 3791 e icu 215 -> icu 215
        s = [_sessao('i161612050', tipo='Run', icu=215.0, campo='icu_pm_cp')]
        c = {'i161612050': {'cp': 144.1, 'w_prime': 86174, 'p1': 3791, 'r2': 0.9749}}
        cont = self._run(s, c)
        self.assertEqual(s[0]['cp'], 215.0)
        self.assertEqual(cont['curva_rejeitada_p1'], 1)
        self.assertEqual(cont['do_icu_pm_cp'], 1)
        self.assertEqual(cont['da_curva'], 0)

    def test_sem_curva_e_sem_icu_valido_da_none(self):
        s = [_sessao('x', icu=None)]
        cont = self._run(s, {})
        self.assertIsNone(s[0]['cp'])
        self.assertEqual(cont['sem_cp'], 1)

    def test_fallback_icu_pm_ftp_e_contado_como_ftp(self):
        s = [_sessao('y', icu=250.0, campo='icu_pm_ftp')]
        cont = self._run(s, {})
        self.assertEqual(s[0]['cp'], 250.0)
        self.assertEqual(cont['do_icu_pm_ftp'], 1)
        self.assertEqual(cont['do_icu_pm_cp'], 0)

    def test_weighttraining_nunca_recebe_cp(self):
        s = [_sessao('wt', tipo='WeightTraining', icu=300.0, campo='icu_pm_cp')]
        c = {'wt': {'cp': 280.0, 'w_prime': 1, 'p1': 500, 'r2': 0.9}}
        cont = self._run(s, c)
        self.assertIsNone(s[0]['cp'])
        self.assertEqual(cont['sem_cp'], 0)   # fora da contagem de CP, nao e falha

    def test_divergencia_moderada_so_alerta_nao_exclui(self):
        # 130 vs 100 = 30%: alerta diagnostico, mas a curva fica (sem P(1 s) > 2000)
        s = [_sessao('z', icu=100.0, campo='icu_pm_cp')]
        c = {'z': {'cp': 130.0, 'w_prime': 1, 'p1': 800, 'r2': 0.9}}
        cont = self._run(s, c)
        self.assertEqual(s[0]['cp'], 130.0)
        self.assertEqual(cont['da_curva'], 1)
        self.assertEqual(cont['alerta_divergencia_icu'], 1)

    def test_valores_invalidos_nunca_viram_cp(self):
        for v in (float('nan'), float('inf'), -10.0, True):
            s = [_sessao('n', icu=None)]
            c = {'n': {'cp': v, 'w_prime': 1, 'p1': 500, 'r2': 0.9}}
            self._run(s, c)
            self.assertIsNone(s[0]['cp'], repr(v))

    def test_cp_bruto_fallback_mantem_o_campo_de_origem(self):
        a = {'icu_pm_cp': None, 'icu_pm_ftp': 240, 'icu_rolling_ftp': 227}
        self.assertEqual(self.tp._campo_cp(a, 'Row'), 'icu_pm_ftp')
        self.assertEqual(self.tp.cp_bruto_da_atividade(a, 'Row'), 240.0)


class CasosEspecificados(_Carrega):
    """Os tres casos pedidos, com os valores extraidos dos exports de 2026-10-11.

    Fluxo igual ao do endpoint: o CP bruto vem de cp_bruto_da_atividade e a curva
    so entra no mapa se R2 >= 0.80 (como em tab_pmc.api_data).
    """

    def _sessao_real(self, sid, tipo, raw, data):
        s = {'id': sid, 'type': tipo, 'date': data,
             'cp': self.tp.cp_bruto_da_atividade(raw, tipo),
             'cp_campo': self.tp._campo_cp(raw, tipo), 'w_prime': None}
        return s

    def _rodar(self, s, curva):
        mapa = {}
        if curva and curva['r2'] >= 0.80:
            mapa[s['id']] = curva
        cont = self.tp.selecionar_cp([s], mapa)
        return s, cont

    def test_i161612050_curva_suspeita_cai_para_icu_pm_cp(self):
        # Run 2026-06-30. Curva 144.1 W (R2 0.9749), P(1 s)=3791 W, divergencia 33%
        # do icu_pm_cp=215 W. Decisao: curva rejeitada por C3; CP = icu_pm_cp 215.
        raw = {'icu_pm_cp': 215, 'icu_pm_ftp': 220}
        s = self._sessao_real('i161612050', 'Run', raw, '2026-06-30')
        curva = {'cp': 144.1, 'w_prime': 86174.0, 'p1': 3791, 'r2': 0.9749}
        s, cont = self._rodar(s, curva)
        self.assertEqual(s['cp'], 215.0)
        self.assertEqual(s['cp_campo'], 'icu_pm_cp')
        self.assertEqual(cont['curva_rejeitada_p1'], 1)
        self.assertEqual(cont['do_icu_pm_cp'], 1)
        self.assertEqual(cont['da_curva'], 0)

    def test_i18885606_curva_e_fonte_abaixo_do_piso_fica_sem_cp(self):
        # Bike 2023-06-07. Curva 27.4 W (R2 0.953); icu_pm_cp = icu_pm_ftp = 27 W.
        # Decisao: piso de 50 W recusa curva e fontes; CP = None, nunca zero.
        raw = {'icu_pm_cp': 27, 'icu_pm_ftp': 27}
        s = self._sessao_real('i18885606', 'Bike', raw, '2023-06-07')
        self.assertIsNone(s['cp'])   # cp_bruto ja recusou 27 W
        curva = {'cp': 27.4, 'w_prime': 2129.0, 'p1': 92, 'r2': 0.953}
        s, cont = self._rodar(s, curva)
        self.assertIsNone(s['cp'])
        self.assertEqual(cont['curva_rejeitada_piso'], 1)
        self.assertEqual(cont['sem_cp'], 1)
        self.assertEqual(cont['da_curva'], 0)

    def test_i47212377_fallback_39_w_fica_sem_cp(self):
        # Bike 2024-08-17, so fallback. Curva 42.5 W com R2 0.259 (nao aceite);
        # icu_pm_cp = icu_pm_ftp = 39 W. Decisao: 39 W < 50 W, CP = None.
        raw = {'icu_pm_cp': 39, 'icu_pm_ftp': 39}
        s = self._sessao_real('i47212377', 'Bike', raw, '2024-08-17')
        curva = {'cp': 42.5, 'w_prime': 91.0, 'p1': 53, 'r2': 0.259}
        s, cont = self._rodar(s, curva)
        self.assertIsNone(s['cp'])
        self.assertEqual(cont['da_curva'], 0)
        self.assertEqual(cont['sem_cp'], 1)

    def test_39_w_so_e_recusado_em_modalidade_com_cp(self):
        # o caso de 39 W e o piso: sem tipo, 39 W passa (nao e modalidade com CP)
        self.assertFalse(self.tp.cp_valido(39, 'Bike'))
        self.assertTrue(self.tp.cp_valido(39, None))


class RegistoDeRegras(_Carrega):

    def test_constantes_da_validacao(self):
        self.assertEqual(self.tp.PISO_CP_W, 50.0)
        self.assertEqual(self.tp.P1_MAX_W, 2000.0)
        self.assertEqual(self.tp.TOL_DIVERGENCIA, 0.20)
        self.assertEqual(set(self.tp.MODS_CP), {'Bike', 'Row', 'Ski', 'Run'})


if __name__ == '__main__':
    unittest.main()
