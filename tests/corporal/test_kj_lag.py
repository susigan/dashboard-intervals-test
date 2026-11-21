"""kJ de atividade na aba corporal: dia a dia e lag semanal com peso / gordura.

Dados sinteticos e deterministicos. Nao toca na base de atividades.

Executar a partir da raiz do repositorio:
    python3 -m unittest tests/corporal/test_kj_lag.py -v
"""
import os
import random
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

import corporal  # noqa: E402


class KJDiario(unittest.TestCase):
    def test_soma_por_dia_e_zero_nos_dias_sem_atividade(self):
        pares = [('2025-01-06', 1000.0), ('2025-01-08', 500.0), ('2025-01-08', 200.0)]
        kj = corporal.kj_diario(pares, ate='2025-01-12')
        por = {r['date']: r['kj'] for r in kj}
        self.assertEqual(por['2025-01-06'], 1000.0)
        self.assertEqual(por['2025-01-07'], 0.0)      # dia sem atividade dentro da cobertura
        self.assertEqual(por['2025-01-08'], 700.0)    # duas sessoes no mesmo dia
        self.assertEqual(kj[-1]['date'], '2025-01-12')

    def test_antes_da_primeira_atividade_nao_ha_dados(self):
        kj = corporal.kj_diario([('2025-03-01', 800.0)], ate='2025-03-03')
        self.assertEqual(kj[0]['date'], '2025-03-01')

    def test_sem_atividades_devolve_lista_vazia(self):
        self.assertEqual(corporal.kj_diario([], ate='2025-01-01'), [])


def _serie(n_semanas, efeito_lag, seed=11):
    """kJ semanal aleatorio; peso reage a 'efeito_lag' vezes o kJ de 2 semanas antes."""
    rnd = random.Random(seed)
    inicio = date(2024, 1, 1)
    kj_sem = [rnd.uniform(3000, 9000) for _ in range(n_semanas)]
    kj_dia, linhas = [], []
    for s in range(n_semanas):
        for d in range(7):
            dia = inicio + timedelta(days=7 * s + d)
            kj_dia.append({'date': dia.isoformat(), 'kj': kj_sem[s] / 7})
            peso_base = 80.0
            if s >= 2:
                peso_base += efeito_lag * (kj_sem[s - 2] - 6000) / 1000
            linhas.append({'date': dia.isoformat(), 'peso': peso_base + rnd.uniform(-0.2, 0.2),
                           'bf': 18 + rnd.uniform(-0.1, 0.1)})
    return kj_dia, linhas


class LagKJ(unittest.TestCase):
    def test_detecta_lag_de_duas_semanas_com_sinal_negativo(self):
        kj_dia, linhas = _serie(40, efeito_lag=-0.5)
        r = corporal.lag_kj(kj_dia, linhas)['peso']
        por_lag = {x['lag_semanas']: x for x in r}
        self.assertIsNotNone(por_lag[2]['r'])
        self.assertLess(por_lag[2]['r'], -0.5)
        self.assertLess(por_lag[2]['p_ajust'], 0.05)
        self.assertGreater(por_lag[0]['p_ajust'], 0.05)

    def test_sem_relacao_nao_marca_nenhum_lag(self):
        kj_dia, linhas = _serie(40, efeito_lag=0.0, seed=5)
        r = corporal.lag_kj(kj_dia, linhas)['peso']
        self.assertTrue(all(x['p_ajust'] is None or x['p_ajust'] > 0.05 for x in r))

    def test_poucos_pares_nao_calcula(self):
        kj_dia, linhas = _serie(8, efeito_lag=-0.5)
        r = corporal.lag_kj(kj_dia, linhas)['bf']
        self.assertTrue(all(x['r'] is None for x in r))

    def test_devolve_peso_e_gordura_com_os_lags_pedidos(self):
        kj_dia, linhas = _serie(20, efeito_lag=0.0)
        out = corporal.lag_kj(kj_dia, linhas)
        self.assertEqual(sorted(out), ['bf', 'peso'])
        self.assertEqual([x['lag_semanas'] for x in out['peso']], list(corporal.KJ_LAG_SEMANAS))


if __name__ == '__main__':
    unittest.main()
