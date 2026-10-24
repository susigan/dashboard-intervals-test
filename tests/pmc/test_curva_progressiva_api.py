"""API e pagina da Curva progressiva (Etapa 28). Base SIMULADA (mock): nunca toca em producao.

    INTERVALS_ICU_API_KEY=valor-ficticio-de-teste python3 -m unittest tests.pmc.test_curva_progressiva_api -v
"""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

import db  # noqa: E402
import app as aplicacao  # noqa: E402  (as rotas vivem em app.py)
from tests.pmc.test_curva_progressiva import fixture  # noqa: E402


def atividades_de(curvas):
    return [{'id': c['activity_id'], 'icu_ignore_power': False, 'type': c['type'],
             'start_date_local': c['date'] + 'T08:00:00'} for c in curvas]


class CurvaApi(unittest.TestCase):
    def setUp(self):
        self.curvas = fixture()
        self.client = aplicacao.app.test_client()
        self.patches = [
            mock.patch.object(db, 'ENABLED', True),
            mock.patch.object(db, 'load_power_curves', return_value=self.curvas),
            mock.patch.object(db, 'load_activities', return_value=atividades_de(self.curvas)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def get(self, qs):
        r = self.client.get('/api/curva-progressiva' + qs)
        return r.status_code, json.loads(r.data)

    def test_resposta_base_com_janela_42(self):
        code, j = self.get('?ate=2026-10-24')
        self.assertEqual(code, 200)
        self.assertTrue(j['ok'])
        self.assertEqual(j['serie'], 'envelope_janela_42d')
        self.assertEqual(j['estimativas_ajustadas'], 'nao_produzidas')
        self.assertEqual(j['n_curvas_lidas'], 4)
        bike = j['modalidades']['Bike']
        self.assertEqual({p['duracao_s'] for p in bike['pontos']}, {60, 300, 1200})

    def test_grelhas_diferentes_no_mesmo_pedido(self):
        _, j = self.get('?ate=2026-10-24&janela=historico')
        self.assertEqual(j['modalidades']['Row']['duracoes_disponiveis'], [60, 1200])
        self.assertEqual(j['modalidades']['Run']['duracoes_disponiveis'], [300])
        self.assertEqual(j['modalidades']['Ski']['estado'], 'sem_dados')
        self.assertEqual(j['serie'], 'envelope_historico_completo')

    def test_modalidade_e_duracao_pedidas_filtram(self):
        _, j = self.get('?ate=2026-10-24&modalidades=Bike&duracoes=300')
        self.assertEqual(list(j['modalidades']), ['Bike'])
        self.assertEqual([p['duracao_s'] for p in j['modalidades']['Bike']['pontos']], [300])

    def test_validacao_de_parametros(self):
        self.assertEqual(self.get('?modalidades=Swim')[0], 400)
        self.assertEqual(self.get('?duracoes=abc')[0], 400)
        self.assertEqual(self.get('?duracoes=0')[0], 400)
        self.assertEqual(self.get('?janela=ontem')[0], 400)
        self.assertEqual(self.get('?janela=0')[0], 400)
        self.assertEqual(self.get('?ate=24-10-2026')[0], 400)
        self.assertEqual(self.get('?evolucao=1&passo=0')[0], 400)

    def test_evolucao_sem_interpolacao(self):
        _, j = self.get('?ate=2026-10-24&evolucao=1&desde=2026-08-01&passo=7&modalidades=Row&janela=42')
        ev = j['evolucao']
        self.assertEqual(ev['as_of'][0], '2026-08-01')
        self.assertEqual(ev['as_of'][-1], '2026-10-24')
        self.assertIsNone(ev['modalidades']['Row']['60'][0])          # antes de existir o treino
        self.assertNotIn('300', ev['modalidades']['Row'])

    def test_repeticao_e_identica(self):
        a = self.get('?ate=2026-10-24&evolucao=1&desde=2026-08-01')[1]
        b = self.get('?ate=2026-10-24&evolucao=1&desde=2026-08-01')[1]
        self.assertEqual(a, b)

    def test_base_indisponivel_responde_erro_em_json(self):
        with mock.patch.object(db, 'ENABLED', False):
            code, j = self.get('?ate=2026-10-24')
        self.assertEqual(code, 200)
        self.assertFalse(j['ok'])
        self.assertIn('sem base', j['erro'])

    def test_atividade_ausente_fica_excluida_com_motivo(self):
        with mock.patch.object(db, 'load_activities', return_value=[]):
            _, j = self.get('?ate=2026-10-24')
        self.assertEqual(j['modalidades']['Bike']['pontos'], [])
        self.assertTrue(all('atividade_nao_encontrada' in x['motivo'] for x in j['exclusoes']))

    def test_pagina_renderiza_e_chama_a_api(self):
        r = self.client.get('/curva-progressiva')
        self.assertEqual(r.status_code, 200)
        html = r.data.decode('utf-8')
        self.assertIn('Curva progressiva', html)
        self.assertIn('/api/curva-progressiva', html)
        self.assertIn('href="/curva-progressiva"', html)              # entrada de navegacao
        self.assertIn('<canvas id="cv"', html)


if __name__ == '__main__':
    unittest.main()
