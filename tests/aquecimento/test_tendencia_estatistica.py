"""Tendencia ao longo do tempo (utils/aquecimento_comparativo.py).

Dados sinteticos e deterministicos: uma serie com descida real, uma sem tendencia,
e casos de dados insuficientes. Nao toca na base de dados.
"""
import os
import random
import sys
import unittest
from datetime import date, timedelta

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(RAIZ, 'utils'))

import aquecimento_comparativo as cmp  # noqa: E402


def _blocos(n_sessoes, passo_dias, fn, metrica='fc_rec1min_avg', seed=7):
    """fn(k, w) -> valor da sessao k no escalao w."""
    rnd = random.Random(seed)
    base = date(2025, 1, 1)
    out = []
    for k in range(n_sessoes):
        d = (base + timedelta(days=k * passo_dias)).isoformat()
        for w in (100, 120, 140):
            out.append({'activity_id': k, 'data': d, 'interval_num': 1, 'bloco_num': 1,
                        'watts_alvo': w, metrica: fn(k, w) + rnd.uniform(-1, 1)})
    return out


class MesesCalendario(unittest.TestCase):
    def test_recua_meses_e_limita_o_dia(self):
        self.assertEqual(cmp.menos_meses(date(2026, 11, 1), 3), date(2026, 8, 1))
        self.assertEqual(cmp.menos_meses(date(2026, 3, 31), 1), date(2026, 2, 28))
        self.assertEqual(cmp.menos_meses(date(2026, 2, 1), 36), date(2023, 2, 1))


class TendenciaFC(unittest.TestCase):
    def test_descida_real_e_melhora_para_fc_rec(self):
        blocos = _blocos(30, 10, lambda k, w: 120 + (w - 100) * 0.3 - k * 0.8)
        r = cmp.resumo_janela(blocos, 'fc_rec1min', '2025-01-01', '2026-12-31')
        t = r['tendencia']
        self.assertEqual(t['status'], 'ok')
        self.assertEqual(t['direcao'], 'descida')
        self.assertLess(t['declive_ano'], 0)
        self.assertLess(t['p'], 0.05)
        self.assertEqual(t['avaliacao'], 'melhorou')

    def test_subida_de_hr_e_piora(self):
        blocos = _blocos(30, 10, lambda k, w: 120 + (w - 100) * 0.3 + k * 0.8, metrica='hr_avg')
        t = cmp.resumo_janela(blocos, 'hr', '2025-01-01', '2026-12-31')['tendencia']
        self.assertEqual(t['direcao'], 'subida')
        self.assertEqual(t['avaliacao'], 'piorou')

    def test_sem_tendencia_nao_inventa_melhora(self):
        blocos = _blocos(30, 10, lambda k, w: 120 + (w - 100) * 0.3)
        t = cmp.resumo_janela(blocos, 'fc_rec1min', '2025-01-01', '2026-12-31')['tendencia']
        self.assertEqual(t['direcao'], 'sem_mudanca')
        self.assertEqual(t['avaliacao'], 'sem mudança clara')

    def test_metrica_sem_sentido_fixo_so_diz_direcao(self):
        blocos = _blocos(30, 10, lambda k, w: 22 - k * 0.3, metrica='resp_avg')
        t = cmp.resumo_janela(blocos, 'resp', '2025-01-01', '2026-12-31')['tendencia']
        self.assertEqual(t['direcao'], 'descida')
        self.assertIsNone(t['avaliacao'])

    def test_poucas_sessoes_e_insuficiente(self):
        blocos = _blocos(4, 10, lambda k, w: 120 - k)
        t = cmp.resumo_janela(blocos, 'fc_rec1min', '2025-01-01', '2026-12-31')['tendencia']
        self.assertEqual(t['status'], 'insuficiente')

    def test_janela_vazia_e_insuficiente_sem_erro(self):
        r = cmp.resumo_janela([], 'fc_rec1min', '2025-01-01', '2026-12-31')
        self.assertEqual(r['pontos'], [])
        self.assertEqual(r['tendencia']['status'], 'insuficiente')

    def test_potencia_nao_vira_tendencia(self):
        # Sessoes mais fortes ao longo do tempo (escalao mais alto), mas FC estavel
        # por escalao: nao deve aparecer como tendencia.
        blocos = []
        for k in range(30):
            d = (date(2025, 1, 1) + timedelta(days=k * 10)).isoformat()
            for w in (100, 120, 140):
                blocos.append({'activity_id': k, 'data': d, 'interval_num': 1, 'bloco_num': 1,
                               'watts_alvo': w, 'fc_rec1min_avg': 100 + (w - 100) * 0.3 + 0.0})
        t = cmp.resumo_janela(blocos, 'fc_rec1min', '2025-01-01', '2026-12-31')['tendencia']
        self.assertEqual(t['direcao'], 'sem_mudanca')


class ContratoJanelas(unittest.TestCase):
    def test_seis_janelas_na_ordem_do_painel(self):
        self.assertEqual([r for r, _ in cmp.JANELAS_MESES], ['3', '6', '12', '24', '36', 'todo'])


if __name__ == '__main__':
    unittest.main()
