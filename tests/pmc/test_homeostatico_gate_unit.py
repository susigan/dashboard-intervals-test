"""Gate de apresentacao do homeostatico: so um ajuste aceite pelo modelo aparece como ajuste.

    INTERVALS_ICU_API_KEY=teste-local python3 -I tests/pmc/test_homeostatico_gate_unit.py -v

Cobre: os estados (valido, rejeitado, padrao, sem_dados), o gate no indice
alostatico com a indisponibilidade explicita da 'Reserva pico', e o comportamento
do JavaScript da pagina (executado com node num DOM simulado, se node existir).
Nao ha limiar de R2 aqui: o R2 e mostrado, e o limiar e decisao separada.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pmc  # noqa: E402


def _ajuste(**kw):
    base = {'ajustado': True, 'motivo': 'ok', 'n_testes': 120, 'r2': 0.43,
            'tentativas': 40, 'k1': 1.4, 'k2': 0.7, 't1': 23.0, 't2': 12.0,
            'p0': 120.0, 'serie': []}
    base.update(kw)
    return base


class EstadoHomeostatico(unittest.TestCase):

    def test_ajuste_aceite_e_valido(self):
        e = pmc.estado_homeostatico(_ajuste())
        self.assertEqual(e['estado'], 'valido')
        self.assertTrue(e['valido'])

    def test_r2_baixo_aceite_pelo_modelo_continua_valido_e_mostra_o_r2(self):
        # caso real do global antes da correcao: R2 0.0534; o R2 e visivel, nao ha limiar
        e = pmc.estado_homeostatico(_ajuste(n_testes=985, r2=0.0534))
        self.assertTrue(e['valido'])
        self.assertIn('0.0534', e['motivo'])

    def test_k_negativo_com_tentativas_e_rejeitado(self):
        e = pmc.estado_homeostatico(_ajuste(ajustado=False, motivo='k_negativo',
                                            k1=2.0, k2=3.0, t1=42.0, t2=7.0))
        self.assertFalse(e['valido'])
        self.assertEqual(e['estado'], 'rejeitado')
        self.assertEqual(e['motivo'], 'k_negativo')

    def test_sem_tentativas_ou_poucos_pontos_e_padrao(self):
        e = pmc.estado_homeostatico(_ajuste(ajustado=False, motivo='poucos_pontos_cp',
                                            tentativas=0, n_testes=12,
                                            k1=2.0, k2=3.0, t1=42.0, t2=7.0))
        self.assertFalse(e['valido'])
        self.assertEqual(e['estado'], 'padrao')

    def test_padroes_nunca_sao_validos_mesmo_com_r2_zero(self):
        e = pmc.estado_homeostatico(_ajuste(ajustado=False, motivo='r2_nao_positivo',
                                            r2=0.0, k1=2.0, k2=3.0, t1=42.0, t2=7.0))
        self.assertFalse(e['valido'])

    def test_poucos_pontos_mesmo_com_ajuste_marcado_nao_e_valido(self):
        e = pmc.estado_homeostatico(_ajuste(n_testes=19))
        self.assertFalse(e['valido'])

    def test_sem_resultado(self):
        self.assertEqual(pmc.estado_homeostatico(None)['estado'], 'sem_dados')
        self.assertFalse(pmc.estado_homeostatico({})['valido'])

    def test_nao_ha_limiar_de_r2_no_modulo(self):
        self.assertFalse(hasattr(pmc, 'R2_MIN_HOMEO'))

    def test_nao_altera_o_resultado(self):
        r = _ajuste(serie=[{'date': '2026-01-01', 'p_hat': 100.0}])
        antes = copy.deepcopy(r)
        pmc.estado_homeostatico(r)
        self.assertEqual(r, antes)


def _serie_dias(n, fim='2026-10-11'):
    f = datetime.strptime(fim, '%Y-%m-%d')
    return [(f - timedelta(days=n - 1 - i)).strftime('%Y-%m-%d') for i in range(n)]


class GateNoIndiceAlostatico(unittest.TestCase):

    def _resultado(self, valido):
        datas = _serie_dias(200)
        serie = [{'date': d, 'load': 50.0, 'tsb': 0.0} for d in datas]
        ph = [{'date': d, 'p_hat': 100.0 + i * 0.5, 'p_hat_suave': 100.0 + i * 0.5}
              for i, d in enumerate(datas)]
        homeo = _ajuste(serie=ph) if valido else _ajuste(
            ajustado=False, motivo='k_negativo', r2=0.0, serie=ph)
        return pmc.indice_alostatico(serie, homeo, [])

    @staticmethod
    def _reserva(res):
        return next(d for d in res['dimensoes'] if d['dim'] == 'Reserva pico')

    def test_reserva_entra_com_ajuste_valido(self):
        res = self._resultado(valido=True)
        self.assertIsNotNone(self._reserva(res)['score'])
        self.assertTrue(res['reserva_pico']['disponivel'])
        self.assertEqual(res['p_hat_disponivel'], 200)

    def test_reserva_sai_com_indisponibilidade_explicita(self):
        res = self._resultado(valido=False)
        r = self._reserva(res)
        self.assertIsNone(r['score'])
        self.assertTrue(r['motivo'].startswith('indisponivel: k_negativo'), r['motivo'])
        self.assertFalse(res['reserva_pico']['disponivel'])
        self.assertEqual(res['p_hat_disponivel'], 0)

    def test_formula_lista_incluidas_e_excluidas_quando_reserva_indisponivel(self):
        res = self._resultado(valido=False)
        self.assertNotIn('Reserva pico', res['incluidas'])
        self.assertTrue(any(e.startswith('Reserva pico (indisponivel: k_negativo')
                            for e in res['excluidas']), res['excluidas'])
        self.assertIn('Excluidas: Reserva pico (indisponivel', res['formula'])
        for nome in res['incluidas']:
            self.assertIn(nome, res['formula'])

    def test_formula_com_reserva_inclui_a_reserva(self):
        res = self._resultado(valido=True)
        self.assertIn('Reserva pico', res['incluidas'])
        self.assertNotIn('indisponivel', res['formula'])
        self.assertEqual(res['n_dims'], len(res['incluidas']))

    def test_sem_reserva_o_total_tem_uma_dimensao_a_menos(self):
        com = self._resultado(valido=True)
        sem = self._resultado(valido=False)
        self.assertEqual(com['n_dims'], sem['n_dims'] + 1)


class CapacidadeExplicativa(unittest.TestCase):
    """Rotulo descritivo do R2. Nao muda a validade."""

    def test_r2_quase_zero_aceite_e_rotulado_muito_baixo(self):
        e = pmc.estado_homeostatico(_ajuste(r2=0.0042))
        self.assertTrue(e['valido'])
        self.assertIn('muito baixa', e['capacidade'])

    def test_r2_razoavel_aceite_sem_rotulo(self):
        e = pmc.estado_homeostatico(_ajuste(r2=0.43))
        self.assertTrue(e['valido'])
        self.assertIsNone(e['capacidade'])

    def test_rejeitado_nao_recebe_rotulo_de_capacidade(self):
        e = pmc.estado_homeostatico(_ajuste(ajustado=False, motivo='k_negativo', r2=0.0042))
        self.assertFalse(e['valido'])
        self.assertIsNone(e['capacidade'])

    def test_r2_nao_finito_fica_indeterminado(self):
        e = pmc.estado_homeostatico(_ajuste(r2=float('nan')))
        self.assertEqual(e['capacidade'], 'indeterminada')
        self.assertEqual(pmc.estado_homeostatico(_ajuste(r2=None))['capacidade'], 'indeterminada')


class ComportamentoDaPaginaJs(unittest.TestCase):
    """Executa o JavaScript real da pagina PMC com node e um DOM simulado."""

    @classmethod
    def setUpClass(cls):
        if shutil.which('node') is None:
            raise unittest.SkipTest('node nao disponivel')
        try:
            import tabs.tab_pmc as tp
        except BaseException as e:  # config.py faz sys.exit sem chave da API
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        cls.js = tp.JS

    def test_harness_homeostatico_passa(self):
        harness = os.path.join(os.path.dirname(__file__), 'js', 'harness_homeostatico.js')
        with tempfile.TemporaryDirectory() as tmp:
            caminho = os.path.join(tmp, 'pmc_js.js')
            with open(caminho, 'w', encoding='utf-8') as f:
                f.write(self.js)
            r = subprocess.run(['node', harness, caminho], capture_output=True,
                               text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        resultado = json.loads(r.stdout)
        self.assertTrue(all(v is True for v in resultado.values()), resultado)


if __name__ == '__main__':
    unittest.main()
