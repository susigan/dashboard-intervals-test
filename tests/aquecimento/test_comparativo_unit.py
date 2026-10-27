"""Analise comparativa de Aquecimento (utils/aquecimento_comparativo.py).

Dados SINTETICOS e controlados, escritos a mao nos testes. Nao toca na base
de producao, no Drive nem na app. Cobre: selecao do periodo, inclusao e
exclusao de sessoes com motivo, agrupamento por escalao, agregacao por
periodo, n minimo, ausencia que nunca vira zero, zeros duvidosos e periodos
vazios ou com escalões incompletos.
"""
import math
import os
import sys
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'utils'))

import aquecimento_comparativo as cmp  # noqa: E402

HOJE = date(2026, 10, 27)


def bloco(aid, data, watts, hr=None, smo2=None, resp=None, dfa1=None,
          modalidade='Bike', interval=1, bloco_num=None):
    return {
        'activity_id': aid, 'data': data, 'modalidade': modalidade,
        'bloco_num': bloco_num or interval, 'interval_num': interval,
        'watts_alvo': watts, 'watts_real': watts - 1.0,
        'hr_avg': hr, 'smo2_avg': smo2, 'resp_avg': resp, 'dfa1_avg': dfa1,
    }


class Periodo(unittest.TestCase):

    def test_30_dias_inclui_hoje_e_recua_29(self):
        ini, fim = cmp.intervalo_periodo(HOJE, dias=30)
        self.assertEqual(fim, HOJE)
        self.assertEqual(ini, date(2026, 9, 28))

    def test_dias_fora_da_lista_e_rejeitado(self):
        with self.assertRaises(ValueError):
            cmp.intervalo_periodo(HOJE, dias=45)

    def test_personalizado_exige_inicio_e_fim(self):
        with self.assertRaises(ValueError):
            cmp.intervalo_periodo(HOJE, inicio=date(2026, 9, 1))

    def test_inicio_posterior_ao_fim_e_rejeitado(self):
        with self.assertRaises(ValueError):
            cmp.intervalo_periodo(HOJE, inicio=date(2026, 10, 2), fim=date(2026, 10, 1))


class Selecao(unittest.TestCase):

    def test_so_sessoes_dentro_do_periodo(self):
        blocos = [
            bloco('A', '2026-10-20', 120, hr=140),
            bloco('B', '2026-08-01', 120, hr=150),   # fora do periodo de 30 dias
        ]
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=30)
        self.assertEqual([s['activity_id'] for s in r['vista1']], ['A'])

    def test_sessao_sem_valor_e_excluida_com_motivo(self):
        blocos = [
            bloco('A', '2026-10-20', 120, hr=140),
            bloco('B', '2026-10-21', 120, hr=None),   # sem dados
        ]
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=30)
        self.assertEqual(r['sessoes']['incluidas'], 1)
        self.assertEqual(r['sessoes']['excluidas'], 1)
        self.assertEqual(r['sessoes']['excluidas_lista'][0]['motivo'], cmp.MOTIVO_SEM_DADOS)

    def test_sessao_so_com_zeros_e_excluida_como_duvidosa(self):
        blocos = [bloco('Z', '2026-10-20', 120, dfa1=0.0)]
        r = cmp.comparativo(blocos, 'Bike', 'dfa1', HOJE, dias=30)
        self.assertEqual(r['sessoes']['incluidas'], 0)
        self.assertEqual(r['sessoes']['excluidas_lista'][0]['motivo'], cmp.MOTIVO_SO_ZEROS)
        self.assertEqual(r['sessoes']['zeros_excluidos'], 1)

    def test_periodo_vazio_devolve_zero_sessoes(self):
        r = cmp.comparativo([], 'Bike', 'hr', HOJE, dias=90)
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['sessoes']['incluidas'], 0)
        self.assertEqual(r['vista1'], [])
        self.assertEqual(r['vista2']['periodos'], [])
        self.assertEqual(r['escaloes'], [])


class ValoresAusentes(unittest.TestCase):

    def test_ausencia_nunca_vira_zero(self):
        blocos = [bloco('A', '2026-10-20', 120, hr=None),
                  bloco('A', '2026-10-20', 140, hr=150)]
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=30)
        pontos = r['vista1'][0]['pontos']
        self.assertEqual([p['watts_alvo'] for p in pontos], [140])
        self.assertEqual(pontos[0]['valor'], 150)

    def test_zero_fica_fora_do_valor_e_conta_como_duvidoso(self):
        blocos = [bloco('A', '2026-10-20', 120, smo2=0.0),
                  bloco('A', '2026-10-20', 140, smo2=60.0)]
        r = cmp.comparativo(blocos, 'Bike', 'smo2', HOJE, dias=30)
        self.assertEqual([p['watts_alvo'] for p in r['vista1'][0]['pontos']], [140])
        self.assertEqual(r['sessoes']['zeros_excluidos'], 1)
        self.assertEqual(r['vista1'][0]['zeros'], 1)

    def test_zero_nao_entra_na_media(self):
        blocos = [bloco('A', '2026-10-20', 120, hr=0.0),
                  bloco('B', '2026-10-21', 120, hr=140.0),
                  bloco('C', '2026-10-22', 120, hr=160.0)]
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=30, min_n=1)
        e = r['vista2']['periodos'][0]['escaloes'][0]
        self.assertEqual(e['n'], 2)
        self.assertAlmostEqual(e['media'], 150.0)

    def test_escalao_incompleto_e_assinalado(self):
        blocos = [bloco('A', '2026-10-20', 120, hr=140),
                  bloco('A', '2026-10-20', 140, hr=150),
                  bloco('B', '2026-10-21', 120, hr=142)]   # B sem 140 W
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=30)
        sess_b = [s for s in r['vista1'] if s['activity_id'] == 'B'][0]
        self.assertEqual(sess_b['em_falta'], [140])
        self.assertEqual(r['sessoes']['incompletas'], 1)


class Agrupamento(unittest.TestCase):

    def test_escaloes_ordenados_e_separados_por_watts(self):
        blocos = [bloco('A', '2026-10-20', 160, hr=160),
                  bloco('A', '2026-10-20', 100, hr=130),
                  bloco('A', '2026-10-20', 120, hr=140)]
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=30)
        self.assertEqual(r['escaloes'], [100, 120, 160])
        self.assertEqual([p['watts_alvo'] for p in r['vista1'][0]['pontos']], [100, 120, 160])

    def test_bloco_duplicado_no_mesmo_escalao_nao_soma(self):
        blocos = [bloco('A', '2026-10-20', 120, hr=140, interval=1),
                  bloco('A', '2026-10-20', 120, hr=999, interval=2, bloco_num=2)]
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=30)
        self.assertEqual(len(r['vista1'][0]['pontos']), 1)
        self.assertEqual(r['vista1'][0]['pontos'][0]['valor'], 140)

    def test_modulo_nao_refiltra_modalidade_o_chamador_filtra(self):
        blocos = [bloco('A', '2026-10-20', 120, hr=140, modalidade='Bike')]
        r = cmp.comparativo(blocos, 'Row', 'hr', HOJE, dias=30)
        # o chamador (listar_blocos) entrega so a modalidade pedida
        self.assertEqual(r['sessoes']['incluidas'], 1)  # o modulo nao re-filtra


class Agregacao(unittest.TestCase):

    def test_media_desvio_e_n_por_mes(self):
        blocos = [bloco('A', '2026-10-02', 120, hr=140),
                  bloco('B', '2026-10-10', 120, hr=150),
                  bloco('C', '2026-10-20', 120, hr=160)]
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=30, agregacao='mes', min_n=3)
        p = r['vista2']['periodos']
        self.assertEqual([x['periodo'] for x in p], ['2026-10'])
        e = p[0]['escaloes'][0]
        self.assertEqual(e['n'], 3)
        self.assertAlmostEqual(e['media'], 150.0)
        self.assertAlmostEqual(e['sd'], 10.0)          # amostral: sqrt(200/2)
        self.assertAlmostEqual(e['sem'], 10.0 / math.sqrt(3))
        self.assertTrue(e['suficiente'])

    def test_agregacao_por_semana_iso(self):
        blocos = [bloco('A', '2026-10-19', 120, hr=140),   # semana 43
                  bloco('B', '2026-10-26', 120, hr=150)]   # semana 44
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=30, agregacao='semana', min_n=1)
        self.assertEqual([x['periodo'] for x in r['vista2']['periodos']],
                         ['2026-S43', '2026-S44'])

    def test_n_abaixo_do_minimo_nao_e_suficiente_e_sem_sd_com_n1(self):
        blocos = [bloco('A', '2026-10-20', 120, hr=140)]
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=30, min_n=3)
        e = r['vista2']['periodos'][0]['escaloes'][0]
        self.assertEqual(e['n'], 1)
        self.assertFalse(e['suficiente'])
        self.assertIsNone(e['sd'])
        self.assertIsNone(e['sem'])

    def test_delta_entre_primeiro_e_ultimo_periodo_suficiente(self):
        blocos = [bloco('A', '2026-09-05', 120, hr=140),
                  bloco('B', '2026-09-06', 120, hr=142),
                  bloco('C', '2026-10-05', 120, hr=146),
                  bloco('D', '2026-10-06', 120, hr=148)]
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=60, agregacao='mes', min_n=2)
        d = r['vista2']['deltas']
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]['de'], d[0]['para']), ('2026-09', '2026-10'))
        self.assertAlmostEqual(d[0]['delta'], 147 - 141)

    def test_sem_delta_quando_so_ha_um_periodo_suficiente(self):
        blocos = [bloco('A', '2026-10-05', 120, hr=140)]
        r = cmp.comparativo(blocos, 'Bike', 'hr', HOJE, dias=30, min_n=1)
        self.assertEqual(r['vista2']['deltas'], [])


class Validacao(unittest.TestCase):

    def test_metrica_desconhecida_rejeitada(self):
        with self.assertRaises(ValueError):
            cmp.comparativo([], 'Bike', 'cadence', HOJE, dias=30)

    def test_thb_nao_e_metrica(self):
        self.assertNotIn('thb', cmp.METRICAS)

    def test_sem_cadencia_torque_elevacao(self):
        for k in cmp.METRICAS:
            self.assertNotIn(k, ('cadence', 'torque', 'elevation'))

    def test_agregacao_invalida_rejeitada(self):
        with self.assertRaises(ValueError):
            cmp.comparativo([], 'Bike', 'hr', HOJE, dias=30, agregacao='dia')

    def test_avisos_de_origem_e_zeros_presentes(self):
        texto = ' '.join(cmp.AVISOS).lower()
        self.assertIn('origem das médias por confirmar', texto)
        self.assertIn('zeros', texto)


if __name__ == '__main__':
    unittest.main()
