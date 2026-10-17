"""Fase B2, passo 4 — suficiencia por modalidade e veredicto global descritivo.

Os valores de golden_deteccao_fase_b2.json foram gerados com o protocolo.py do
commit 7e3bb7a (antes da Fase B2) sobre o conjunto sintetico de `_conjunto()`:
provam que contagens, datas, atrasos, urgencias e sugestoes nao mudaram.
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import protocolo as P  # noqa: E402

HOJE = datetime(2026, 10, 16)


class _Relogio(datetime):
    @classmethod
    def now(cls, tz=None):
        return HOJE


def _data(dias_atras):
    return (HOJE - timedelta(days=dias_atras)).strftime('%Y-%m-%d')


def _curva(aid, tipo, dias_atras, w60=None, w300=None, w1200=None):
    return {'activity_id': aid, 'type': tipo, 'date': _data(dias_atras),
            'secs': [60, 300, 1200], 'watts': [w60, w300, w1200]}


def _n_curvas_1200(tipo, n, w=300, rec=10, passo=5):
    return [_curva(f'{tipo}{i}', tipo, rec + passo * (n - 1 - i), w1200=w) for i in range(n)]


def _det(curvas):
    with mock.patch.object(P, 'datetime', _Relogio):
        return P.detectar_testes(curvas)


def _conjunto():
    x, cs = 12345, []
    cfg = {'Bike': (70, (450, 330, 280)), 'Row': (40, (500, 350, 270)), 'Run': (14, (520, 380, 300))}
    for m, (n, base) in cfg.items():
        for i in range(n):
            x = (x * 1103515245 + 12345) % 2147483648
            f = 0.84 + 0.18 * ((x >> 8) % 1000) / 1000
            x = (x * 1103515245 + 12345) % 2147483648
            g = 0.84 + 0.18 * ((x >> 8) % 1000) / 1000
            watts = [None if (i % 11 == 0) else round(base[0] * f, 1), round(base[1] * g, 1),
                     round(base[2] * f, 1) if i % 7 else None]
            cs.append({'activity_id': f'{m[0]}{i}', 'type': m,
                       'date': (HOJE - timedelta(days=4 + 5 * (n - 1 - i) + (i % 3 == 0))).strftime('%Y-%m-%d'),
                       'secs': [60, 300, 1200], 'watts': watts})
    return cs


def _resumo(det):
    with mock.patch.object(P, 'datetime', _Relogio):
        cob = P.cobertura(det)
        sug = P.sugerir(det)
        rob = P.robustez(det)
    return {
        'det': {m: {str(s): {'n': d['n_testes'], 'ultimo': d['ultimo']['date'],
                             'dias': d['dias_desde_ultimo'], 'atraso': d['em_atraso'],
                             'datas': [t['date'] for t in d['testes']],
                             'pct': [t['pct_do_melhor'] for t in d['testes']]}
                    for s, d in durs.items()} for m, durs in det.items()},
        'cob_total': cob['total'], 'cob_mod': cob['por_modalidade'], 'sug': sug, 'rob': rob}


class DeteccaoPreservada(unittest.TestCase):
    def test_contagens_datas_atrasos_sugestoes_iguais_ao_commit_anterior(self):
        golden = json.load(open(os.path.join(os.path.dirname(__file__), 'golden_deteccao_fase_b2.json')))
        atual = json.loads(json.dumps(_resumo(_det(_conjunto()))))
        self.assertEqual(atual['cob_total'], 159)
        for chave in ('det', 'cob_total', 'cob_mod', 'sug', 'rob'):
            self.assertEqual(atual[chave], golden[chave], chave)

    def test_cobertura_mantem_as_mesmas_chaves(self):
        cob = P.cobertura(_det(_conjunto()))
        self.assertEqual(set(cob), {'total', 'por_modalidade', 'veredicto'})


class VeredictoGlobalDescritivo(unittest.TestCase):
    def test_nao_afirma_suficiencia_com_total_alto(self):
        cob = P.cobertura(_det(_conjunto()))
        self.assertGreaterEqual(cob['total'], 30)
        v = cob['veredicto']
        self.assertNotIn('suficientes', v)
        self.assertNotIn('ha testes', v)
        self.assertIn(str(cob['total']), v)
        self.assertIn('3 modalidade', v)
        self.assertIn('por modalidade', v)

    def test_distingue_detetado_de_teste_confirmado(self):
        v = P.cobertura(_det(_n_curvas_1200('Bike', 8)))['veredicto']
        self.assertIn('Detetado nao e o mesmo que teste maximo confirmado', v)

    def test_sem_esforcos(self):
        cob = P.cobertura({})
        self.assertEqual((cob['total'], cob['por_modalidade']), (0, {}))
        self.assertIn('Nenhum esforco detetado', cob['veredicto'])
        self.assertNotIn('suficiente', cob['veredicto'])


class SuficienciaPorModalidade(unittest.TestCase):
    def test_limiares_reutilizados_sem_alteracao(self):
        self.assertEqual((P.ROBUSTEZ_BOM, P.ROBUSTEZ_PARCIAL, P.MINIMO_CALIBRACAO), (30, 12, 25))
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import inspect
                import pmc
        except BaseException as e:
            self.skipTest(f'pmc nao importa: {type(e).__name__}: {e}')
        self.assertEqual(inspect.signature(pmc.calibrar_com_ancora).parameters['minimo_testes'].default,
                         P.MINIMO_CALIBRACAO)

    def test_modalidades_avaliadas_independentemente(self):
        curvas = _n_curvas_1200('Bike', 32) + _n_curvas_1200('Run', 10)
        s = P.suficiencia_por_modalidade(_det(curvas), curvas)
        self.assertEqual((s['Bike']['n_candidatos'], s['Bike']['estado'], s['Bike']['atinge_minimo_calibracao']),
                         (30, 'bom', True))
        self.assertEqual((s['Run']['n_candidatos'], s['Run']['estado'], s['Run']['atinge_minimo_calibracao']),
                         (8, 'insuficiente', False))
        # o total global (38) nao torna a Run suficiente
        self.assertEqual(P.cobertura(_det(curvas))['total'], 38)

    def test_faixas_30_12_e_minimo_25(self):
        for n_curvas, n, estado, atinge in ((32, 30, 'bom', True), (31, 29, 'parcial', True),
                                            (27, 25, 'parcial', True), (26, 24, 'parcial', False),
                                            (14, 12, 'parcial', False), (13, 11, 'insuficiente', False)):
            curvas = _n_curvas_1200('Bike', n_curvas)
            e = P.suficiencia_por_modalidade(_det(curvas), curvas)['Bike']
            self.assertEqual((e['n_candidatos'], e['estado'], e['atinge_minimo_calibracao']),
                             (n, estado, atinge), n)

    def test_historico_de_potencia_curto_nao_e_tratado_como_defeito(self):
        # Run: 10 curvas em ~13 meses (gravacao de potencia recente)
        curvas = [_curva(f'r{i}', 'Run', 400 - 40 * i, w1200=300) for i in range(10)]
        e = P.suficiencia_por_modalidade(_det(curvas), curvas)['Run']
        h = e['historico_potencia']
        self.assertEqual((h['primeiro'], h['ultimo'], h['n_curvas']), (_data(400), _data(40), 10))
        self.assertAlmostEqual(h['meses'], 11.8, places=1)
        self.assertEqual(e['estado'], 'insuficiente')
        self.assertIn('Amostra: insuficiente', e['texto'])
        self.assertIn('11.8 meses de historico de potencia', e['texto'])
        self.assertNotIn('defeit', e['texto'].lower())
        self.assertNotIn('erro', e['texto'].lower())

    def test_modalidade_com_curvas_mas_sem_candidatos(self):
        curvas = _n_curvas_1200('Bike', 8) + [_curva('r0', 'Run', 5, w1200=300), _curva('r1', 'Run', 3, w1200=310)]
        s = P.suficiencia_por_modalidade(_det(curvas), curvas)
        self.assertEqual((s['Run']['n_candidatos'], s['Run']['estado'], s['Run']['ultimo']),
                         (0, 'insuficiente', None))
        self.assertEqual(s['Run']['historico_potencia']['n_curvas'], 2)
        self.assertIn('0 candidatos', s['Run']['texto'])

    def test_sem_curvas_nao_inventa_historico(self):
        curvas = _n_curvas_1200('Bike', 8)
        s = P.suficiencia_por_modalidade(_det(curvas))
        self.assertIsNone(s['Bike']['historico_potencia'])
        self.assertNotIn('historico de potencia', s['Bike']['texto'])

    def test_dias_distintos_e_outras_duracoes(self):
        curvas = _n_curvas_1200('Bike', 8)
        curvas += [_curva('x', 'Bike', 2, w1200=300), _curva('y', 'Bike', 2, w1200=300)]   # mesmo dia
        curvas += [_curva(f'p{i}', 'Bike', 60 - i * 3, w300=330) for i in range(8)]
        e = P.suficiencia_por_modalidade(_det(curvas), curvas)['Bike']
        self.assertEqual(e['n_candidatos'] - e['n_dias_distintos'], 1)
        self.assertEqual(e['outras_duracoes'], {'5 min max': 6})

    def test_nao_altera_a_deteccao(self):
        det = _det(_conjunto())
        antes = json.dumps(_resumo(det), sort_keys=True, default=str)
        P.suficiencia_por_modalidade(det, _conjunto())
        self.assertEqual(antes, json.dumps(_resumo(det), sort_keys=True, default=str))

    def test_ordem_estavel_por_modalidade(self):
        curvas = _conjunto()
        s = P.suficiencia_por_modalidade(_det(curvas), curvas)
        self.assertEqual(list(s), sorted(s))


class RotaProtocoloSuficiencia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import app as appmod
        except BaseException as e:
            raise unittest.SkipTest(f'app nao importa: {type(e).__name__}: {e}')
        cls.app = appmod

    def _get(self, curvas, url='/api/protocolo'):
        def carregar(tipo=None, desde=None):
            return [c for c in curvas if not tipo or c['type'] == tipo]
        with mock.patch.object(self.app.db, 'load_power_curves', side_effect=carregar), \
                mock.patch.object(P, 'datetime', _Relogio):
            return self.app.app.test_client().get(url)

    def test_chaves_do_topo_inalteradas_e_suficiencia_dentro_de_cobertura(self):
        r = self._get(_n_curvas_1200('Bike', 32) + _n_curvas_1200('Run', 10))
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(set(j), {'status', 'cobertura', 'sugestoes', 'robustez', 'detectados', 'como_funciona'})
        self.assertEqual(set(j['cobertura']), {'total', 'por_modalidade', 'veredicto', 'suficiencia'})
        self.assertEqual(j['cobertura']['total'], 38)
        suf = j['cobertura']['suficiencia']
        self.assertEqual(suf['Bike']['estado'], 'bom')
        self.assertEqual(suf['Run']['estado'], 'insuficiente')
        self.assertEqual(suf['Run']['n_candidatos'], 8)
        self.assertEqual(suf['Run']['historico_potencia']['n_curvas'], 10)
        self.assertIn('meses de historico de potencia', suf['Run']['texto'])
        self.assertNotIn('suficientes', j['cobertura']['veredicto'])

    def test_restante_da_resposta_igual_a_funcao(self):
        curvas = _conjunto()
        j = self._get(curvas).get_json()
        det = _det(curvas)
        with mock.patch.object(P, 'datetime', _Relogio):
            self.assertEqual(j['sugestoes'], json.loads(json.dumps(P.sugerir(det))))
            self.assertEqual(j['robustez'], json.loads(json.dumps(P.robustez(det))))
        self.assertEqual(j['cobertura']['por_modalidade'], json.loads(json.dumps(P.cobertura(det)['por_modalidade'])))
        self.assertNotIn('testes', j['detectados']['Bike']['1200'])

    def test_filtro_por_tipo_so_avalia_essa_modalidade(self):
        j = self._get(_n_curvas_1200('Bike', 32) + _n_curvas_1200('Run', 10), '/api/protocolo?tipo=Run').get_json()
        self.assertEqual(list(j['cobertura']['suficiencia']), ['Run'])

    def test_sem_curvas(self):
        j = self._get([]).get_json()
        self.assertEqual(j['cobertura']['suficiencia'], {})
        self.assertEqual((j['sugestoes'], j['detectados']), ([], {}))

    def test_falha_na_suficiencia_nao_derruba_a_resposta(self):
        with mock.patch.object(P, 'suficiencia_por_modalidade', side_effect=RuntimeError('boom')):
            r = self._get(_n_curvas_1200('Bike', 8, rec=40))
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertIn('RuntimeError', j['cobertura']['suficiencia']['erro'])
        self.assertEqual(j['sugestoes'][0]['secs'], 1200)
        self.assertEqual(j['cobertura']['total'], 6)


if __name__ == '__main__':
    unittest.main()
