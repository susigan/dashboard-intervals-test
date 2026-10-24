"""Etapa 24 - candidatos `a_verificar` (180 s) visiveis para revisao manual. SO LEITURA.

    python -m unittest tests.pmc.test_ir_candidatos_a_verificar

Sintetico e offline (sem base, sem rede). Garante que mostrar os candidatos NAO altera a regra de YES (2026-10-19),
as observacoes do IR (so `elegivel`) nem o P0.
"""
import copy
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import modelo_ir_servico as mis  # noqa: E402

ELEG = lambda aid: 'elegivel'  # noqa: E731
ATE = '2026-12-31'


def c180(aid, mod, dia, w):
    return {'activity_id': aid, 'type': mod, 'date': dia, 'weight': 70.0, 'secs': [180], 'watts': [w]}


def ativ(aid, dia, tags=None, intervalos=None):
    return {'id': aid, 'type': 'Ride', 'icu_ignore_power': False, 'start_date_local': dia + 'T08:00:00',
            'tags': tags if tags is not None else [], 'interval_summary': intervalos or []}


def cenario():
    """Bike: a0..a5 sem sinal (a5 = 330 W, o melhor, sem tag); a6 = 320 W COM tag '3min' (sinal -> YES)."""
    datas = ['2025-%02d-10' % (i + 1) for i in range(6)]
    cv = [c180('a%d' % i, 'Bike', datas[i], w) for i, w in enumerate([300.0] * 5 + [330.0])]
    cv.append(c180('a6', 'Bike', '2025-07-10', 320.0))
    at = [ativ('a0', datas[0]), ativ('a1', datas[1]), ativ('a2', datas[2]), ativ('a3', datas[3]),
          ativ('a4', datas[4], tags='["Moxy"]'),                      # tag em texto JSON (formato da base)
          ativ('a5', datas[5]),
          ativ('a6', '2025-07-10', tags=['3min'])]
    return cv, at


class CandidatosAVerificar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cv, cls.at = cenario()
        cls.ent = mis.entradas_mmp3(cls.cv, cls.at, ATE, [], [])
        cls.esf = cls.ent['Bike'][1]
        cls.por = {e['activity_id']: e for e in cls.esf}
        cls.av = mis.candidatos_a_verificar(cls.esf, cls.at)

    def test_1_a_verificar_listado_e_sem_sinal_nao_vira_yes(self):
        self.assertEqual(self.por['a5']['estado'], 'a_verificar')
        self.assertIn('a5', [c['activity_id'] for c in self.av])
        self.assertTrue(all(self.por[c['activity_id']]['estado'] == 'a_verificar' for c in self.av))
        self.assertEqual(len(self.av), sum(1 for e in self.esf if e['estado'] == 'a_verificar'))

    def test_2_elegivel_com_sinal_nao_aparece_como_a_verificar(self):
        self.assertEqual(self.por['a6']['estado'], 'elegivel')
        self.assertNotIn('a6', [c['activity_id'] for c in self.av])

    def test_3_campos_pedidos_potencia_referencia_tags_e_intervalo(self):
        c = next(x for x in self.av if x['activity_id'] == 'a5')
        self.assertEqual(c['watts'], 330.0)
        self.assertEqual(c['melhor_anterior_watts'], 300.0)            # referencia all-time causal anterior
        self.assertEqual(c['melhor_anterior_data'], '2025-01-10')      # empate de 300 W: o detetor fica com o primeiro
        self.assertIsNotNone(c['pct_do_melhor'])
        self.assertEqual(c['tags'], [])
        self.assertIsNone(c['intervalo'])
        self.assertIn('classe_intervalo', c)
        self.assertFalse(c['tag_3min'])

    def test_4_tags_em_texto_json_sao_lidas(self):
        c = next(x for x in self.av if x['activity_id'] == 'a4')
        self.assertEqual(c['tags'], ['Moxy'])

    def test_5_observacoes_do_ir_continuam_so_elegiveis(self):
        obs = {o['test_id'] for o in self.ent['Bike'][0]}
        self.assertNotIn(self.por['a5']['test_id'], obs)               # a_verificar nunca entra nas observacoes
        self.assertIn(self.por['a6']['test_id'], obs)                  # elegivel por tag continua a entrar

    def test_6_listar_nao_altera_estados_nem_p0(self):
        antes = copy.deepcopy(self.esf)
        p0_antes = mis.p0_global_das_entradas(self.ent)
        mis.candidatos_a_verificar(self.esf, self.at)
        self.assertEqual(self.esf, antes)
        self.assertEqual(mis.p0_global_das_entradas(self.ent), p0_antes)

    def test_7_contexto_mmp3_expoe_a_lista_sem_mudar_o_resto(self):
        with mock.patch.object(mis, 'impacto_cp_model', return_value={'ok': False}):
            ctx, _p0 = mis.contexto_mmp3(self.cv, self.at, ATE, None, [])
        self.assertEqual(ctx['Bike']['a_verificar'], self.av)
        self.assertEqual(ctx['Bike']['cp_model'], {'ok': False})       # o resto do contexto mantem-se
        self.assertIn('n_elegiveis', ctx['Bike'])

    def test_8_lista_vazia_quando_nao_ha_candidatos(self):
        self.assertEqual(mis.candidatos_a_verificar([], self.at), [])
        self.assertEqual(mis.candidatos_a_verificar(None, self.at), [])


if __name__ == '__main__':
    unittest.main()
