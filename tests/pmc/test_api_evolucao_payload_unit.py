"""Integracao: tabs/tab_pmc.api_data com a chave evolucao_performance.

    python -m unittest tests.pmc.test_api_evolucao_payload_unit -v

Sem Intervals, Sheets nem base real: fetch_activities, _sheets, db.cp_por_sessao e
db.load_power_curves sao substituidos por fixtures. Verifica que o payload existente
nao muda, que zero e ausencia passam pelo caminho real da API, e que uma falha da
nova secao nao derruba o resto.
"""
import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'dummy-teste-local')

import pmc  # noqa: E402
import db  # noqa: E402
import tabs.tab_pmc as T  # noqa: E402
from app import app  # noqa: E402


def _acts(n=60, ftp_fn=None):
    base = date(2025, 1, 1)
    out = []
    for i in range(n):
        d = (base + timedelta(days=i)).isoformat()
        a = {'id': f'a{i}', 'start_date_local': d + 'T07:00:00', 'type': 'Ride',
             'name': 'x', 'icu_training_load': 40 + (i % 5) * 5,
             'moving_time': 3600, 'elapsed_time': 3600,
             'icu_pm_cp': 250.0, 'icu_pm_w_prime': 15000.0}
        if ftp_fn:
            v = ftp_fn(i)
            if v is not ...:
                a.update(v)
        out.append(a)
    return out


def _wellness(n=60):
    base = date(2025, 1, 1)
    return [{'date': (base + timedelta(days=i)).isoformat(), 'hrv': 62.0 + (i % 7) * 0.5,
             'sleep_quality': 3, 'stress': 3, 'soreness': 3, 'fatiga': 3}
            for i in range(n)]


class ApiEvolucao(unittest.TestCase):

    def setUp(self):
        self.acts = _acts(ftp_fn=lambda i: {'icu_pm_ftp': 0.0} if i % 2 else ...)
        self.curvas = []
        self._g = {
            'fetch_activities': T.fetch_activities, '_sheets': T._sheets,
            'limite_hrv': T.limite_hrv, 'cp_por_sessao': db.cp_por_sessao,
            'load_power_curves': db.load_power_curves,
            'calcular_ftlm': pmc.calcular_ftlm, 'calcular_fmt': pmc.calcular_fmt,
            'evolucao_performance': pmc.evolucao_performance,
        }
        T.fetch_activities = lambda *a, **k: self.acts
        T._sheets = lambda force=False: (_wellness(), [], None)
        T.limite_hrv = lambda *a, **k: '2020-01-01'
        db.cp_por_sessao = lambda *a, **k: []
        db.load_power_curves = lambda *a, **k: list(self.curvas)
        pmc.calcular_ftlm = lambda *a, **k: None
        pmc.calcular_fmt = lambda *a, **k: {'params_usados': {}, 'calibracao': {}}
        T._cache_pmc.update({'chave': None, 'valor': None, 'time': None})
        # Guarda de rede: qualquer connect() a endereco nao local falha o teste.
        import socket
        self._tentativas_externas = []
        self._connect_orig = socket.socket.connect

        def _connect_guardado(sock, addr, *a, **k):
            host = addr[0] if isinstance(addr, tuple) else str(addr)
            if host not in ('127.0.0.1', '::1', 'localhost'):
                self._tentativas_externas.append(str(addr))
                raise OSError('acesso externo nao autorizado em teste: %s' % (addr,))
            return self._connect_orig(sock, addr, *a, **k)
        socket.socket.connect = _connect_guardado

    def tearDown(self):
        import socket
        socket.socket.connect = self._connect_orig
        self.assertEqual(self._tentativas_externas, [],
                         'payload tentou acesso externo nao autorizado')
        T.fetch_activities = self._g['fetch_activities']
        T._sheets = self._g['_sheets']
        T.limite_hrv = self._g['limite_hrv']
        db.cp_por_sessao = self._g['cp_por_sessao']
        db.load_power_curves = self._g['load_power_curves']
        pmc.calcular_ftlm = self._g['calcular_ftlm']
        pmc.calcular_fmt = self._g['calcular_fmt']
        pmc.evolucao_performance = self._g['evolucao_performance']

    def _payload(self):
        with app.test_request_context('/api/pmc'):
            resp = T.api_data()
            data = resp.get_json() if hasattr(resp, 'get_json') else resp[0].get_json()
        return data

    def test_chave_nova_presente_e_estrutura_minima(self):
        d = self._payload()
        self.assertEqual(d['status'], 'OK')
        self.assertIn('evolucao_performance', d)
        ev = d['evolucao_performance']
        self.assertEqual(ev['status'], 'ok')
        self.assertIn('Bike', ev['modalidades'])
        self.assertEqual(set(ev['modalidades']['Bike']['fontes']),
                         {'eftp_atividade', 'ftp_configurado', 'eftp_nao_confirmado'})

    def test_zero_cru_da_api_conta_como_zero_nao_como_valor(self):
        d = self._payload()
        q = d['evolucao_performance']['modalidades']['Bike']['fontes']['eftp_atividade']['qualidade']
        # 30 sessoes com icu_pm_ftp = 0.0 (indices impares); 30 sem o campo (pares)
        self.assertEqual(q['n_zero'], 30)
        self.assertEqual(q['n_ausentes'], 30)
        self.assertEqual(q['n_validos'], 0)

    def test_campo_ausente_e_ausencia_nao_zero(self):
        self.acts = _acts(ftp_fn=None)
        d = self._payload()
        q = d['evolucao_performance']['modalidades']['Bike']['fontes']['eftp_atividade']['qualidade']
        self.assertEqual(q['n_zero'], 0)
        self.assertEqual(q['n_ausentes'], 60)

    def test_sessoes_nao_recebem_campos_de_ftp_novos(self):
        d = self._payload()
        for s in d['sessoes']:
            self.assertNotIn('icu_pm_ftp', s)
            self.assertNotIn('icu_ftp', s)
            self.assertNotIn('ftp', s)

    def test_payload_existente_mantem_chaves_do_homeostatico(self):
        d = self._payload()
        for k in ('homeostatico', 'homeostatico_mod', 'alostatico', 'cp_fonte', 'fmt',
                  'ftlm', 'serie', 'sessoes', 'wellness', 'cores', 'ciclicos'):
            self.assertIn(k, d)

    def test_falha_da_nova_secao_nao_derruba_o_payload(self):
        def quebra(*a, **k):
            raise RuntimeError('falha simulada')
        pmc.evolucao_performance = quebra
        d = self._payload()
        self.assertEqual(d['status'], 'OK')
        self.assertEqual(d['evolucao_performance']['status'], 'erro')
        self.assertIn('falha simulada', d['evolucao_performance']['erro'])
        self.assertIn('serie', d)

    def test_falha_ao_ler_curvas_mantem_ftp_e_registra(self):
        def sem_banco(*a, **k):
            raise RuntimeError('banco indisponivel')
        db.load_power_curves = sem_banco
        d = self._payload()
        ev = d['evolucao_performance']
        self.assertEqual(ev['status'], 'ok')
        self.assertEqual(ev['modalidades']['Bike']['melhores_esforcos']['1200']['qualidade']['n_brutos'], 0)


class CacheVersao(unittest.TestCase):

    def test_versao_entra_na_chave_do_fmt(self):
        a = T._entradas_fmt([], [], [], '2020-01-01')
        self.assertEqual(a['versao_cache'], T.CACHE_VERSAO_PMC)
        self.assertEqual(T.CACHE_VERSAO_PMC, 4)

    def test_mudar_a_versao_muda_o_digest(self):
        antes = T._digest(T._entradas_fmt([], [], [], '2020-01-01'))
        original = T.CACHE_VERSAO_PMC
        try:
            T.CACHE_VERSAO_PMC = original + 1
            depois = T._digest(T._entradas_fmt([], [], [], '2020-01-01'))
        finally:
            T.CACHE_VERSAO_PMC = original
        self.assertNotEqual(antes, depois)


if __name__ == '__main__':
    unittest.main()
