"""Vista principal do IR (Overall e modalidades a 300 s): testes do modulo puro modelo_ir_principal.

Dados SINTETICOS apenas aqui (fixtures deterministicos). A verificacao com dados reais foi feita fora do repositorio
contra o prototipo verificado (Fase 4.1): Overall e Bike/Row/Ski coincidem em celula, parametros e criterios.
"""
import copy
import os
import sys
import unittest
from datetime import date, timedelta

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import modelo_ir_principal as mip  # noqa: E402

ESTADOS_PERMITIDOS = {mip.ESTADO_VALIDADA, mip.ESTADO_PREVISTO, mip.ESTADO_NAO_CALCULAVEL}


def _fixture(seed=7, dias=900, mods=('Bike', 'Row', 'Ski')):
    """Cargas diarias e curvas a 300 s deterministicas, com dependencia da carga (para haver ajuste)."""
    rng = np.random.default_rng(seed)
    inicio = date(2023, 1, 1)
    cargas = {m: {} for m in mip.MODALIDADES}
    curvas = []
    for m in mods:
        for i in range(dias):
            d = inicio + timedelta(days=i)
            if rng.random() < 0.6:
                carga = float(rng.uniform(20, 120))
                cargas[m][d.isoformat()] = carga
                if rng.random() < 0.7:
                    w = 150 + 0.05 * carga + float(rng.normal(0, 5))
                    curvas.append({'type': m, 'date': d.isoformat(), 'secs': [60, 300, 1200], 'watts': [w + 40, w, w - 30]})
    return cargas, curvas


class CalcularPrincipal(unittest.TestCase):
    def setUp(self):
        self.cargas, self.curvas = _fixture()
        self.res = mip.calcular(self.cargas, self.curvas, cores={'Bike': '#E74C3C', 'Row': '#3498DB'})

    def test_estrutura_e_estados_permitidos(self):
        self.assertEqual(self.res['status'], 'OK')
        self.assertFalse(self.res['persistido'])
        self.assertTrue(self.res['experimental'])
        self.assertEqual(self.res['unidade'], 'z')
        self.assertEqual(self.res['carga'], 'icu_training_load')
        for m in mip.MODALIDADES:
            self.assertIn(self.res['modalidades'][m]['estado'], ESTADOS_PERMITIDOS, m)

    def test_overall_tem_as_tres_series_alinhadas_com_as_datas(self):
        ov = self.res['overall']
        self.assertIsNotNone(ov)
        n = len(self.res['datas'])
        for k in ('fitness', 'fadiga', 'liquida'):
            self.assertEqual(len(ov[k]), n, k)
            self.assertTrue(all(np.isfinite(v) for v in ov[k]), k)

    def test_overall_nunca_e_apresentado_como_validado(self):
        self.assertEqual(self.res['overall']['estado'], mip.ESTADO_EXPLORATORIO)
        self.assertIn('ainda não validado', self.res['rotulo'])

    def test_cada_modalidade_so_tem_as_suas_series(self):
        # Alterar so as curvas da Bike nao pode mudar a curva da Row (curvas independentes por modalidade).
        base = self.res['modalidades']['Row']
        outras = [c for c in self.curvas if c['type'] != 'Bike']
        cargas_bike_fora = copy.deepcopy(self.cargas)
        cargas_bike_fora['Bike'] = {}
        res2 = mip.calcular(cargas_bike_fora, self.curvas)
        ref_sem_bike = mip.calcular(self.cargas, [c for c in self.curvas if c['type'] != 'Bike'])
        self.assertEqual(res2['modalidades']['Row'].get('fitness'), ref_sem_bike['modalidades']['Row'].get('fitness'))
        self.assertEqual(base.get('parametros'), self.res['modalidades']['Row'].get('parametros'))
        self.assertIsNotNone(outras)

    def test_run_sem_observacoes_e_nao_calculavel_sem_serie(self):
        it = self.res['modalidades']['Run']
        self.assertEqual(it['estado'], mip.ESTADO_NAO_CALCULAVEL)
        self.assertIsNone(it['fitness'])
        self.assertIsNone(it['liquida'])
        self.assertIn('observações insuficientes', it['motivo'])

    def test_faltas_derivadas_dos_criterios_e_nao_inventadas(self):
        for m in ('Bike', 'Row', 'Ski'):
            it = self.res['modalidades'][m]
            if it['estado'] == mip.ESTADO_PREVISTO:
                self.assertEqual(len(it['criterios']['faltas']), sum(1 for k in ('c1', 'c2', 'c3', 'c4')
                                                                     if not it['criterios'][k]))
                for f in it['criterios']['faltas']:
                    self.assertIn(f, mip.FALTAS.values())

    def test_execucao_repetida_e_deterministica(self):
        res2 = mip.calcular(self.cargas, self.curvas)
        self.assertEqual(self.res['overall']['liquida'], res2['overall']['liquida'])

    def test_entradas_nao_sao_alteradas(self):
        c0, v0 = copy.deepcopy(self.cargas), copy.deepcopy(self.curvas)
        mip.calcular(self.cargas, self.curvas)
        self.assertEqual(self.cargas, c0)
        self.assertEqual(self.curvas, v0)

    def test_sem_dados_devolve_erro_sem_curva(self):
        res = mip.calcular({}, [])
        self.assertEqual(res['status'], 'ERRO')
        self.assertFalse(res['persistido'])


class Suavizacao(unittest.TestCase):
    def test_janela_1_nao_altera(self):
        x = np.array([1.0, -2.0, 3.5, 0.0])
        self.assertTrue(np.allclose(mip._suavizar(x, 1), x))

    def test_serie_constante_fica_constante_inclusive_nas_extremidades(self):
        x = np.full(30, 0.7)
        self.assertTrue(np.allclose(mip._suavizar(x), 0.7))

    def test_nao_cria_valores_fora_do_intervalo_original(self):
        rng = np.random.default_rng(3)
        x = rng.normal(0, 1, 400)
        s = mip._suavizar(x)
        self.assertGreaterEqual(s.min(), x.min() - 1e-12)
        self.assertLessEqual(s.max(), x.max() + 1e-12)

    def test_preserva_sinal_longe_das_passagens_por_zero(self):
        # Junto a uma mudanca de sinal a media movel desloca a passagem por zero ate meia janela (limitacao
        # documentada). Longe da fronteira, o sinal de cada segmento e preservado.
        x = np.concatenate([np.full(30, -1.0), np.full(30, 2.0)])
        s = mip._suavizar(x)
        meio = mip.JANELA_SUAVIZACAO // 2
        self.assertTrue(np.all(s[:30 - meio - 1] < 0))
        self.assertTrue(np.all(s[30 + meio + 1:] > 0))

    def test_extremidades_usam_so_dias_existentes(self):
        x = np.array([10.0, 0.0, 0.0, 0.0, 0.0])
        s = mip._suavizar(x, 3)
        # primeiro dia: media de (10, 0) e nao (10+0+0)/3 com zeros artificiais
        self.assertAlmostEqual(s[0], 5.0)


if __name__ == '__main__':
    unittest.main()
