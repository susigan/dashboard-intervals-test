"""Testes da cache da PMC (tabs/tab_pmc.py): erros, janela (desde) e entradas.

    python -m unittest tests.pmc.test_cache_pmc -v

Sem dados reais nem credenciais: as chamadas a Intervals, Sheets e base de
dados sao substituidas por fixtures sinteticas. O CALCULO pesado do FMT e
substituido por um contador, para se ver se o resultado veio da cache.

Cobre: E1 (erro do FTLM perdido num cache hit), E2 (desde ignorado na cache da
calibracao), E3/M1 (chave sem HRV nem CP), e erros que nao podem ficar em cache.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'dummy-teste-local')

import pmc  # noqa: E402
import db  # noqa: E402
import tabs.tab_pmc as T  # noqa: E402
from app import app  # noqa: E402


def _acts(n=60, cp=250.0, w_prime=15000.0):
    base = datetime(2025, 1, 1)
    out = []
    for i in range(n):
        d = (base + timedelta(days=i)).strftime('%Y-%m-%d')
        out.append({'id': f'a{i}', 'start_date_local': d + 'T07:00:00', 'type': 'Ride',
                    'name': 'x', 'icu_training_load': 40 + (i % 5) * 5,
                    'moving_time': 3600, 'elapsed_time': 3600,
                    'icu_pm_cp': cp, 'icu_pm_w_prime': w_prime})
    return out


def _wellness(n=60, hrv=62.0):
    base = datetime(2025, 1, 1)
    out = []
    for i in range(n):
        d = (base + timedelta(days=i)).strftime('%Y-%m-%d')
        out.append({'date': d, 'hrv': hrv + (i % 7) * 0.5,
                    'sleep_quality': 3, 'stress': 3, 'soreness': 3, 'fatiga': 3})
    return out


class CacheTestBase(unittest.TestCase):

    def setUp(self):
        self.estado = {'acts': _acts(), 'wellness': _wellness(), 'ftlm_erro': False,
                       'fmt_erro_primeira_vez': False}
        self.chamadas_fmt = 0
        self.desdes_fmt = []
        self.chamadas_eventos = []
        self._guardados = {
            'fetch_activities': T.fetch_activities,
            '_sheets': T._sheets,
            'limite_hrv': T.limite_hrv,
            'cp_por_sessao': db.cp_por_sessao,
            'calcular_ftlm': pmc.calcular_ftlm,
            'calcular_fmt': pmc.calcular_fmt,
            'modelo_homeostatico': pmc.modelo_homeostatico,
            'indice_alostatico': pmc.indice_alostatico,
            'homeostatico_por_modalidade': pmc.homeostatico_por_modalidade,
            'dtrimp_dkj': pmc.dtrimp_dkj,
            'eficiencia_rolling': pmc.eficiencia_rolling,
            'alertas': pmc.alertas,
            'teste_eventos': pmc.teste_eventos,
            'calibrar_com_ancora': pmc.calibrar_com_ancora,
        }
        T.fetch_activities = lambda: self.estado['acts']
        T._sheets = lambda force=False: (self.estado['wellness'], None, [])
        T.limite_hrv = lambda *a, **k: '2020-01-01'
        db.cp_por_sessao = lambda: []
        pmc.alertas = lambda *a, **k: []
        pmc.modelo_homeostatico = lambda *a, **k: None
        pmc.indice_alostatico = lambda *a, **k: None
        pmc.homeostatico_por_modalidade = lambda *a, **k: {}
        pmc.dtrimp_dkj = lambda *a, **k: {}
        pmc.eficiencia_rolling = lambda *a, **k: {}
        pmc.calibrar_com_ancora = lambda *a, **k: {}
        pmc.teste_eventos = self._eventos
        pmc.calcular_ftlm = self._ftlm
        pmc.calcular_fmt = self._fmt
        T._cache_pmc.update({'chave': None, 'valor': None, 'time': None})
        T._cache_cal.update({'chave': None, 'valor': None, 'time': None})

    def tearDown(self):
        T.fetch_activities = self._guardados['fetch_activities']
        T._sheets = self._guardados['_sheets']
        T.limite_hrv = self._guardados['limite_hrv']
        db.cp_por_sessao = self._guardados['cp_por_sessao']
        pmc.calcular_ftlm = self._guardados['calcular_ftlm']
        pmc.calcular_fmt = self._guardados['calcular_fmt']
        pmc.modelo_homeostatico = self._guardados['modelo_homeostatico']
        pmc.indice_alostatico = self._guardados['indice_alostatico']
        pmc.homeostatico_por_modalidade = self._guardados['homeostatico_por_modalidade']
        pmc.dtrimp_dkj = self._guardados['dtrimp_dkj']
        pmc.eficiencia_rolling = self._guardados['eficiencia_rolling']
        pmc.alertas = self._guardados['alertas']
        pmc.teste_eventos = self._guardados['teste_eventos']
        pmc.calibrar_com_ancora = self._guardados['calibrar_com_ancora']

    # ── substitutos ──────────────────────────────────────────────────────
    def _ftlm(self, *a, **k):
        if self.estado['ftlm_erro']:
            raise RuntimeError('falha simulada FTLM')
        return None

    def _fmt(self, sessoes, wellness, serie, *a, **k):
        self.chamadas_fmt += 1
        self.desdes_fmt.append(k.get('desde_hrv'))
        if self.estado.get('fmt_erro_primeira_vez') and self.chamadas_fmt == 1:
            return {'erro': 'falha simulada FMT (primeira vez)'}
        hrv = [w.get('hrv') for w in (wellness or [])]
        return {'dimensoes': ['Load', 'HRV'], 'params_usados': {}, 'calibracao': {},
                'serie': [], 'soma_hrv': round(sum(v or 0 for v in hrv), 3),
                'desde_hrv_recebido': k.get('desde_hrv')}

    def _eventos(self, sessoes, wellness, serie, mods, desde=None):
        self.chamadas_eventos.append(desde)
        return {'desde_usado': desde}

    # ── chamadas ─────────────────────────────────────────────────────────
    def _chamar_data(self):
        with app.test_request_context('/api/pmc'):
            resp = T.api_data()
            return resp.get_json()

    def _chamar_cal(self, desde=None):
        q = f'/api/calibracao?desde={desde}' if desde else '/api/calibracao'
        with app.test_request_context(q):
            return T.api_calibracao_dados()


class CacheDoFtlm(CacheTestBase):
    """E1: um erro do FTLM nao pode desaparecer quando o FMT vem da cache."""

    def test_erro_ftlm_persiste_num_cache_hit_E1(self):
        self.estado['ftlm_erro'] = True
        r1 = self._chamar_data()
        r2 = self._chamar_data()          # FMT vem da cache
        self.assertIn('falha simulada FTLM', r1.get('erro_ftlm') or '')
        self.assertIn('falha simulada FTLM', r2.get('erro_ftlm') or '',
                      'o erro do FTLM desapareceu na segunda chamada')
        self.assertEqual(self.chamadas_fmt, 1, 'o FMT devia ter vindo da cache')

    def test_erro_do_fmt_nao_fica_em_cache(self):
        """Um erro devolvido pelo calculo do FMT nao e um resultado valido."""
        self.estado['fmt_erro_primeira_vez'] = True
        r1 = self._chamar_data()
        self.assertTrue((r1.get('fmt') or {}).get('erro'))
        r2 = self._chamar_data()
        self.assertEqual(self.chamadas_fmt, 2,
                         'o erro do FMT foi guardado em cache e nao foi recalculado')
        self.assertFalse((r2.get('fmt') or {}).get('erro'))

    def test_mesma_entrada_usa_cache(self):
        self._chamar_data()
        self._chamar_data()
        self.assertEqual(self.chamadas_fmt, 1)


class CacheDaCalibracao(CacheTestBase):
    """E2 (desde), e alteracoes de HRV e CP com a mesma data final e o mesmo numero de sessoes."""

    def test_desde_diferente_recalcula_e_devolve_a_janela_pedida_E2(self):
        r1 = self._chamar_cal(desde='2025-01-10')
        r2 = self._chamar_cal(desde='2025-02-01')
        self.assertEqual(r2['janela_hrv']['desde'], '2025-02-01')
        self.assertFalse(r2.get('de_cache'), 'a cache ignorou o desde')
        self.assertEqual(self.chamadas_eventos[-1], '2025-02-01')
        self.assertEqual(r1['janela_hrv']['desde'], '2025-01-10')

    def test_desde_chega_ao_calculo_e_a_janela_respondida_e_a_real(self):
        """A janela pedida e a usada no calculo, e a resposta reporta os dados dela."""
        r = self._chamar_cal(desde='2025-02-01')
        self.assertEqual(self.desdes_fmt[-1], '2025-02-01',
                         'o calculo nao recebeu o desde pedido')
        jan = r['janela_hrv']
        self.assertEqual(jan['desde'], '2025-02-01')
        self.assertEqual(jan['primeiro_dia_hrv'], '2025-02-01')
        self.assertGreaterEqual(jan['primeiro_dia_hrv'], '2025-02-01')

    def test_desde_mal_formado_devolve_erro_e_nao_calcula(self):
        r = self._chamar_cal(desde='01/02/2025')
        self.assertIn('YYYY-MM-DD', r.get('erro', ''))
        self.assertEqual(self.chamadas_fmt, 0)

    def test_alterar_hrv_no_mesmo_dia_recalcula(self):
        self._chamar_cal()
        n1 = self.chamadas_fmt
        for w in self.estado['wellness']:
            w['hrv'] = (w['hrv'] or 0) + 15.0      # mesmas datas, mesmo numero de sessoes
        self._chamar_cal()
        self.assertEqual(self.chamadas_fmt, n1 + 1, 'HRV alterada nao invalidou a cache')

    def test_alterar_cp_no_mesmo_dia_recalcula(self):
        self._chamar_cal()
        n1 = self.chamadas_fmt
        for a in self.estado['acts']:
            a['icu_pm_cp'] = 260.0                 # mesmas datas, mesmo numero de sessoes
        self._chamar_cal()
        self.assertEqual(self.chamadas_fmt, n1 + 1, 'CP alterado nao invalidou a cache')

    def test_mesma_entrada_usa_cache_da_calibracao(self):
        self._chamar_cal()
        r2 = self._chamar_cal()
        self.assertTrue(r2.get('de_cache'))
        self.assertEqual(self.chamadas_fmt, 1)

    def test_erro_de_calibracao_nao_fica_em_cache(self):
        original = pmc.calcular_fmt
        pmc.calcular_fmt = lambda *a, **k: {'erro': 'sem dados de HRV'}
        r1 = self._chamar_cal()
        self.assertEqual(r1.get('erro'), 'sem dados de HRV')
        pmc.calcular_fmt = original
        self.chamadas_fmt = 0
        r2 = self._chamar_cal()
        self.assertNotIn('erro', r2)
        self.assertFalse(r2.get('de_cache', False))


if __name__ == '__main__':
    unittest.main()
