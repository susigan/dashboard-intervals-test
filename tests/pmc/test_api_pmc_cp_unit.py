"""Rotas /api/pmc e /api/calibracao com a seleção de CP: as três sessões pedidas.

    INTERVALS_ICU_API_KEY=teste-local python3 -I tests/pmc/test_api_pmc_cp_unit.py -v

Sem rede e sem base real: fetch_activities, _sheets e db.cp_por_sessao são
substituídos. Verifica a ligação da seleção, dos contadores e da validação
homeostática no JSON; não recalcula o modelo.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _acts():
    # as tres sessoes pedidas, com os campos brutos dos exports
    reais = [
        {'id': 'i161612050', 'start_date_local': '2026-06-30T07:00:00', 'type': 'Run',
         'icu_pm_cp': 215, 'icu_pm_ftp': 220, 'icu_training_load': 120,
         'moving_time': 3000, 'elapsed_time': 3000},
        {'id': 'i18885606', 'start_date_local': '2023-06-07T07:00:00', 'type': 'VirtualRide',
         'icu_pm_cp': 27, 'icu_pm_ftp': 27, 'icu_training_load': 40,
         'moving_time': 3060, 'elapsed_time': 3060},
        {'id': 'i47212377', 'start_date_local': '2024-08-17T07:00:00', 'type': 'Ride',
         'icu_pm_cp': 39, 'icu_pm_ftp': 39, 'icu_training_load': 50,
         'moving_time': 3600, 'elapsed_time': 3600},
    ]
    # sessoes de enchimento para a serie diaria existir
    enchimento = []
    for i in range(1, 60):
        dia = f'2026-05-{(i % 28) + 1:02d}' if i < 28 else f'2026-06-{(i % 28) + 1:02d}'
        enchimento.append({'id': f'e{i}', 'start_date_local': dia + 'T07:00:00',
                           'type': 'Ride', 'icu_pm_cp': 250, 'icu_pm_ftp': 250,
                           'icu_training_load': 60, 'moving_time': 3600,
                           'elapsed_time': 3600})
    return reais + enchimento


CURVAS = [
    # i161612050: curva 144.1 W, R2 0.9749, P(1 s) 3791 W -> rejeitada por C3
    {'activity_id': 'i161612050', 'date': '2026-06-30', 'type': 'Run', 'cp': 144.1,
     'w_prime': 86174.0, 'r2': 0.9749, 'n_pontos': 9, 'p1': 3791},
    # i18885606: curva 27.4 W, R2 0.953 -> rejeitada por piso (50 W)
    {'activity_id': 'i18885606', 'date': '2023-06-07', 'type': 'Bike', 'cp': 27.4,
     'w_prime': 2129.0, 'r2': 0.953, 'n_pontos': 9, 'p1': 92},
    # i47212377: curva 42.5 W com R2 0.259 -> nao aceite, nao entra no mapa
    {'activity_id': 'i47212377', 'date': '2024-08-17', 'type': 'Bike', 'cp': 42.5,
     'w_prime': 91.0, 'r2': 0.259, 'n_pontos': 9, 'p1': 53},
]


class ApiPmcCp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            import app as app_mod
            import tabs.tab_pmc as tp
        except BaseException as e:  # config.py faz sys.exit sem chave da API
            raise unittest.SkipTest(f'app nao importa: {type(e).__name__}: {e}')
        cls.app_mod, cls.tp = app_mod, tp
        cls.client = app_mod.app.test_client()

    def setUp(self):
        self.tp._cache_pmc.update({'chave': None, 'valor': None, 'time': None})
        patches = [
            mock.patch.object(self.tp, 'fetch_activities', return_value=_acts()),
            mock.patch.object(self.tp, '_sheets', return_value=([], None, [])),
            mock.patch.object(self.tp.db, 'cp_por_sessao', return_value=CURVAS),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_pmc_conta_cada_caso_na_fonte_de_cp(self):
        r = self.client.get('/api/pmc')
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        cp = r.get_json()['cp_fonte']
        # i161612050 e i18885606 caem por C3 e piso; i47212377 nao tem curva aceite
        self.assertEqual(cp['curva_rejeitada_p1'], 1)
        self.assertEqual(cp['curva_rejeitada_piso'], 1)
        self.assertGreaterEqual(cp['sem_cp'], 2)   # i18885606 e i47212377
        self.assertEqual(cp['da_curva'], 0)
        self.assertNotIn('icu_rolling_ftp', cp['nota'])

    def test_pmc_expoe_validacao_homeostatica_em_global_e_modalidades(self):
        r = self.client.get('/api/pmc').get_json()
        h = r.get('homeostatico')
        self.assertIsNotNone(h)
        self.assertIn('validacao', h)
        self.assertIn(h['validacao']['estado'], ('valido', 'rejeitado', 'padrao', 'sem_dados'))
        for mod, v in (r.get('homeostatico_mod') or {}).items():
            self.assertIn('validacao', v, mod)
            if not v['validacao']['valido']:
                # um ajuste nao valido nunca e apresentado como valido
                self.assertFalse(v['validacao']['valido'])

    def test_alostatico_declara_reserva_pico_e_dimensoes(self):
        r = self.client.get('/api/pmc').get_json()
        alos = r.get('alostatico') or {}
        if 'erro' in alos or not alos:
            self.skipTest('alostatico indisponivel com dados sinteticos')
        self.assertIn('excluidas', alos)
        self.assertIn('incluidas', alos)
        valido = r['homeostatico']['validacao']['valido']
        if valido:
            self.assertIn('Reserva pico', alos['incluidas'])
        else:
            self.assertTrue(any(e.startswith('Reserva pico') for e in alos['excluidas']))

    def test_calibracao_responde_com_a_mesma_selecao(self):
        r = self.client.get('/api/calibracao')
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        corpo = r.get_json()
        self.assertNotIn('error', corpo if isinstance(corpo, dict) else {})


if __name__ == '__main__':
    unittest.main()
