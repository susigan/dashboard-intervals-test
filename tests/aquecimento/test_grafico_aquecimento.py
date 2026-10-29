"""Graficos HRR limitados ao aquecimento (nao a sessao inteira). Sintetico."""
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
RAIZ = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(RAIZ, 'utils'))

import aquecimento_hrr as hrr  # noqa: E402


def _r(ini1, fim1, rec1_fim, ini2, fim2, ultimo_rec=None):
    return {'degraus': [
        {'work': {'t_inicio_s': ini1, 't_fim_s': fim1}, 'rec': {'t_fim_s': rec1_fim}},
        {'work': {'t_inicio_s': ini2, 't_fim_s': fim2},
         'rec': {'t_fim_s': ultimo_rec}},
    ]}


class JanelaAquecimento(unittest.TestCase):

    def test_do_primeiro_esforco_ao_fim_da_ultima_recuperacao(self):
        self.assertEqual(hrr.janela_aquecimento(_r(100, 400, 470, 480, 780, 850)), (70.0, 880.0))

    def test_ultimo_degrau_sem_recuperacao_usa_2_min(self):
        self.assertEqual(hrr.janela_aquecimento(_r(100, 400, 470, 480, 780, None)), (70.0, 780 + 120 + 30.0))

    def test_sem_degraus_nao_ha_janela(self):
        self.assertIsNone(hrr.janela_aquecimento({'degraus': []}))
        self.assertIsNone(hrr.janela_aquecimento(None))


class SerieCortada(unittest.TestCase):

    def test_so_pontos_dentro_da_janela(self):
        t = list(range(0, 1000, 2))
        streams = {'time': t, 'watts': [100] * len(t), 'heartrate': [120] * len(t)}
        s = hrr.serie_grafico(streams, janela=(200, 300))
        self.assertTrue(all(200 <= x <= 300 for x in s['t']))
        self.assertGreater(len(s['t']), 0)

    def test_janela_sem_amostras_devolve_none(self):
        streams = {'time': [0, 1, 2], 'watts': [1, 1, 1], 'heartrate': [1, 1, 1]}
        self.assertIsNone(hrr.serie_grafico(streams, janela=(50, 60)))

    def test_sem_janela_mantem_a_sessao_inteira(self):
        t = list(range(0, 100))
        streams = {'time': t, 'watts': [1] * 100, 'heartrate': [1] * 100}
        s = hrr.serie_grafico(streams)
        self.assertEqual(s['t'][0], 0)
        self.assertEqual(s['t'][-1], 99)


if __name__ == '__main__':
    unittest.main()
