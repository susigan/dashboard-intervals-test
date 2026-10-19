"""Curvas de influencia IR CONJUNTAS (modelo_ir_servico.curvas_conjuntas e /api/modelo/ir/conjunto).

    python -m pytest tests/pmc/test_modelo_ir_conjunto.py

Offline. Dados SINTETICOS. Estes testes verificam a IMPLEMENTACAO (alinhamento, normalizacao, decaimento,
estados, ausencia de escritas): NAO validam que as curvas prevejam o desempenho real. Os ajustes sao
fabricados com parametros conhecidos (sem otimizador) e comparados com um oraculo independente
(modelo_ir.curvas_influencia sobre as cargas diarias).
"""
import ast
import copy
import math
import os
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fase1_util as U  # noqa: E402

APP = U.importar_app()
import config  # noqa: E402
import db  # noqa: E402
import modelo_ir as ir  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402

ROOT = U.ROOT
TIPO_CRU = {'Bike': 'Ride', 'Row': 'Rowing', 'Run': 'Run', 'Ski': 'NordicSki'}
MODS = ['Bike', 'Row', 'Run', 'Ski']
D0 = date(2025, 3, 3)


import ir_duracao_legado as _LEG  # noqa: E402


def setUpModule():
    _LEG.iniciar()


def tearDownModule():
    _LEG.parar()


def dia(i):
    return (D0 + timedelta(days=i)).isoformat()


def ativ(mod, i, carga, aid=None, tipo=None):
    return {'id': aid or f'{mod}-{i}', 'type': tipo or TIPO_CRU[mod],
            'start_date_local': dia(i) + 'T07:00:00', 'icu_training_load': carga, 'name': f'{mod} {i}'}


def aj(mod, estado=ir.EST_NAO_IDENTIFICAVEL, dur=1200, p0=300.0, k1=0.02, k2=0.05, t1=40.0, t2=6.0, lag=1,
       primeira_obs=0, ultima_obs=120, avisos=None, versao=1):
    """Linha fabricada de modelo_ir_ajustes (o formato que db.ultimo_ajuste_ir devolve)."""
    ok = estado in (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL)
    obs = [{'data': dia(primeira_obs), 'watts': 280.0, 'test_id': f'{mod}-a'},
           {'data': dia(ultima_obs), 'watts': 290.0, 'test_id': f'{mod}-b'}] if ok else []
    return {'modalidade': mod, 'duracao_s': dur, 'versao_ajuste': versao, 'ajustado_em': '2026-10-01T10:00:00',
            'origem': 'teste', 'motivo': 'primeiro_ajuste', 'estado': estado,
            'p0': p0 if ok else None, 'k1': k1 if ok else None, 'k2': k2 if ok else None,
            't1': t1 if ok else None, 't2': t2 if ok else None, 'n_obs': 20,
            'data_ini': dia(primeira_obs) if ok else None, 'data_fim': dia(ultima_obs) if ok else None,
            'funcao_objetivo': 10.0, 'r2': 0.9, 'rmse': 2.5, 'convergiu': estado != ir.EST_SEM_CONVERGENCIA,
            'p0_inicial': p0, 'p0_origem': 'mmp3_ultimo_yes:Bike:1200:seam-290.0', 'assinatura': 'x', 'versao_modelo': 1,
            'configuracao': {'ir': {'lag_dias': lag}}, 'metricas': {'motivo_insuficiente': 'menos_de_15_observacoes'},
            'identificabilidade': {'avisos': avisos or []},
            'entradas': {'observacoes': obs, 'carga_digest': 'x', 'referencia': dia(ultima_obs), 'p0_inicial': p0}}


def cenario_base():
    """4 modalidades com ajustes conhecidos e cargas distintas; Ski comeca mais tarde."""
    ajs = {'Bike': aj('Bike', ir.EST_IDENTIFICAVEL, primeira_obs=0, ultima_obs=150),
           'Row': aj('Row', p0=250.0, k1=0.03, k2=0.04, t1=55.0, t2=9.0, primeira_obs=10, ultima_obs=140),
           'Run': aj('Run', p0=320.0, k1=0.015, k2=0.06, t1=30.0, t2=4.0, primeira_obs=20, ultima_obs=130),
           'Ski': aj('Ski', p0=200.0, k1=0.025, k2=0.05, t1=70.0, t2=12.0, primeira_obs=60, ultima_obs=150)}
    rng = np.random.default_rng(3)
    at = []
    for mod, ini in (('Bike', 0), ('Row', 10), ('Run', 20), ('Ski', 60)):
        for i in range(ini, 180):
            if rng.random() < 0.4:
                at.append(ativ(mod, i, float(round(rng.uniform(30, 120), 1))))
    return ajs, at


class _Mock:
    """Aplica os ajustes/atividades fabricados no lugar da base (db.ultimo_ajuste_ir, etc.)."""

    def aplica(self, ajs, atividades):
        self.ajs = ajs
        self.at = atividades
        self.ps = [mock.patch.object(db, 'ultimo_ajuste_ir', side_effect=lambda t, d: self.ajs.get(t) if (self.ajs.get(t) or {}).get('duracao_s') == d else None),
                   mock.patch.object(db, 'ajustes_ir_historico', side_effect=lambda t=None, d=None: [self.ajs[t]] if self.ajs.get(t) else []),
                   mock.patch.object(db, 'load_activities', side_effect=lambda **k: self.at)]
        for p in self.ps:
            p.start()
            self.addCleanup(p.stop)

    def conj(self, **kw):
        return mis.curvas_conjuntas(kw.pop('duracao', 1200), atividades=self.at, **kw)


def carga_diaria(atividades, mod, d0, d1):
    c, _ = mis.carga_por_modalidade(atividades)
    return ir.cargas_diarias(c[mod], d0, d1)


class Alinhamento(_Mock, unittest.TestCase):
    def setUp(self):
        self.aplica(*cenario_base())
        self.r = self.conj()

    def test_eixo_comum_diario_sem_buracos(self):
        d = self.r['datas']
        self.assertEqual(d[0], self.r['data_ini'])
        self.assertEqual(d[-1], self.r['data_fim'])
        for a, b in zip(d, d[1:]):
            self.assertEqual((date.fromisoformat(b) - date.fromisoformat(a)).days, 1)

    def test_todas_as_series_tem_o_comprimento_do_eixo(self):
        n = len(self.r['datas'])
        for m in MODS:
            it = self.r['modalidades'][m]
            for k in ('positiva', 'negativa', 'liquida'):
                self.assertEqual(len(it['w'][k]), n)
                self.assertEqual(len(it['pct'][k]), n)

    def test_eixo_comeca_na_origem_mais_antiga_e_acaba_no_ultimo_dia(self):
        origens = [self.r['modalidades'][m]['inicio_serie'] for m in MODS]
        self.assertEqual(self.r['data_ini'], min(origens))
        self.assertEqual(self.r['data_fim'], max(self.r['modalidades'][m]['inicio_serie'] and
                         max(a['start_date_local'][:10] for a in self.at if config.TYPE_MAP[a['type']] == m)
                         for m in MODS))

    def test_antes_da_origem_e_ausente_nao_zero(self):
        d = self.r['datas']
        for m in MODS:
            it = self.r['modalidades'][m]
            i0 = d.index(it['inicio_serie'])
            for k in ('positiva', 'negativa', 'liquida'):
                self.assertTrue(all(v is None for v in it['w'][k][:i0]), (m, k))
                self.assertTrue(all(v is None for v in it['pct'][k][:i0]), (m, k))
                self.assertTrue(all(v is not None for v in it['w'][k][i0:]), (m, k))
        ski = self.r['modalidades']['Ski']
        self.assertGreater(d.index(ski['inicio_serie']), 0)          # Ski so comeca depois: ha nulos reais

    def test_origem_do_conjunto_igual_a_da_curva_individual(self):
        for m in MODS:
            j = mis.curva_influencia(m, 1200, atividades=self.at)
            self.assertEqual(self.r['modalidades'][m]['inicio_serie'], j['curvas']['datas'][0])

    def test_mesmo_eixo_apos_pedir_ate_posterior(self):
        r2 = self.conj(ate=dia(260))
        self.assertEqual(r2['data_fim'], dia(260))
        for m in MODS:
            self.assertEqual(len(r2['modalidades'][m]['w']['liquida']), len(r2['datas']))


class Coerencia(_Mock, unittest.TestCase):
    def setUp(self):
        self.aplica(*cenario_base())
        self.r = self.conj()

    def test_liquida_e_positiva_menos_negativa_e_negativa_nao_negativa(self):
        for m in MODS:
            w = self.r['modalidades'][m]['w']
            for p, n, l in zip(w['positiva'], w['negativa'], w['liquida']):
                if p is None:
                    self.assertIsNone(n)
                    self.assertIsNone(l)
                    continue
                self.assertGreaterEqual(p, 0)
                self.assertGreaterEqual(n, 0)
                self.assertAlmostEqual(l, p - n, delta=2e-3)

    def test_resposta_completa_e_P0_mais_liquida_igual_a_equacao_do_ir(self):
        for m in MODS:
            j = mis.curva_influencia(m, 1200, atividades=self.at)
            it = self.r['modalidades'][m]
            d0 = j['curvas']['datas'][0]
            i0 = self.r['datas'].index(d0)
            P = it['parametros']
            for k, liq_resp in enumerate(j['curvas']['liquida']):
                esperado = it['w']['liquida'][i0 + k] + P['p0']
                self.assertAlmostEqual(esperado, liq_resp, delta=2e-3)

    def test_oraculo_independente_das_cargas_diarias(self):
        """pos = K1*E, neg = K2*G calculados de novo (sem passar por curva_influencia)."""
        for m in MODS:
            it = self.r['modalidades'][m]
            P, lag = it['parametros'], it['lag_dias']
            i0 = self.r['datas'].index(it['inicio_serie'])
            cargas = carga_diaria(self.at, m, it['inicio_serie'], self.r['data_fim'])
            e = np.zeros(len(cargas))
            g = np.zeros(len(cargas))
            for t in range(len(cargas)):                       # recorrencia explicita, sem lfilter
                e[t] = (e[t - 1] if t else 0.0) * math.exp(-1.0 / P['t1']) + cargas[t]
                g[t] = (g[t - 1] if t else 0.0) * math.exp(-1.0 / P['t2']) + cargas[t]
            for t in range(len(cargas)):
                ev = e[t - lag] if t - lag >= 0 else 0.0
                gv = g[t - lag] if t - lag >= 0 else 0.0
                self.assertAlmostEqual(it['w']['positiva'][i0 + t], P['k1'] * ev, delta=2e-3)
                self.assertAlmostEqual(it['w']['negativa'][i0 + t], P['k2'] * gv, delta=2e-3)

    def test_normalizacao_pct_e_cem_vezes_w_sobre_p0_com_negativa_negativa(self):
        for m in MODS:
            it = self.r['modalidades'][m]
            p0 = it['parametros']['p0']
            self.assertEqual(it['normalizacao'], 'pct_p0')
            for t, v in enumerate(it['w']['positiva']):
                if v is None:
                    continue
                self.assertAlmostEqual(it['pct']['positiva'][t], 100.0 * v / p0, delta=6e-3)
                self.assertAlmostEqual(it['pct']['negativa'][t], -100.0 * it['w']['negativa'][t] / p0, delta=6e-3)
                self.assertAlmostEqual(it['pct']['liquida'][t], 100.0 * it['w']['liquida'][t] / p0, delta=1e-2)
                self.assertLessEqual(it['pct']['negativa'][t], 0.0)

    def test_normalizacao_e_so_apresentacao_w_nao_depende_dela(self):
        base = self.r['modalidades']['Bike']['w']
        ajs, at = cenario_base()
        ajs['Bike']['p0'] = 1000.0                                        # outro P0: so muda a escala relativa
        self.aplica(ajs, at)
        r2 = self.conj()
        self.assertEqual(r2['modalidades']['Bike']['w'], base)
        self.assertNotEqual(r2['modalidades']['Bike']['pct']['positiva'], self.r['modalidades']['Bike']['pct']['positiva'])

    def test_invariancia_a_escala_da_carga(self):
        """K1*E nao muda se a carga for multiplicada por a e K1 dividido por a."""
        ajs, at = cenario_base()
        a = 7.0
        at2 = copy.deepcopy(at)
        for x in at2:
            if config.TYPE_MAP[x['type']] == 'Bike':
                x['icu_training_load'] *= a
        ajs2 = copy.deepcopy(ajs)
        ajs2['Bike']['k1'] /= a
        ajs2['Bike']['k2'] /= a
        self.aplica(ajs2, at2)
        r2 = self.conj()
        for k in ('positiva', 'negativa'):
            for x, y in zip(self.r['modalidades']['Bike']['w'][k], r2['modalidades']['Bike']['w'][k]):
                self.assertAlmostEqual(x, y, delta=5e-3)


class DiasSemTreinoELag(_Mock, unittest.TestCase):
    def setUp(self):
        # uma unica carga isolada de 100 no dia 10 e mais nada: o resto e decaimento puro
        ajs = {'Bike': aj('Bike', ir.EST_IDENTIFICAVEL, p0=300.0, k1=0.1, k2=0.2, t1=40.0, t2=6.0,
                          primeira_obs=0, ultima_obs=60)}
        self.aplica(ajs, [ativ('Bike', 10, 100.0)])
        self.r = self.conj()
        self.it = self.r['modalidades']['Bike']
        self.i = self.r['datas'].index(dia(10))

    def test_lag_um_dia_a_carga_do_dia_nao_entra_no_proprio_dia(self):
        w = self.it['w']
        self.assertEqual(self.it['lag_dias'], 1)
        self.assertEqual(w['positiva'][self.i], 0.0)
        self.assertEqual(w['negativa'][self.i], 0.0)
        self.assertAlmostEqual(w['positiva'][self.i + 1], 0.1 * 100.0, delta=1e-3)
        self.assertAlmostEqual(w['negativa'][self.i + 1], 0.2 * 100.0, delta=1e-3)

    def test_dias_sem_treino_decaem_exponencialmente(self):
        w = self.it['w']
        for dt in (1, 5, 20):
            self.assertAlmostEqual(w['positiva'][self.i + 1 + dt], 10.0 * math.exp(-dt / 40.0), delta=2e-3)
            self.assertAlmostEqual(w['negativa'][self.i + 1 + dt], 20.0 * math.exp(-dt / 6.0), delta=2e-3)

    def test_antes_da_carga_e_zero_depois_da_origem_e_nao_ausente(self):
        i0 = self.r['datas'].index(self.it['inicio_serie'])
        self.assertEqual(self.it['w']['positiva'][i0], 0.0)           # origem com estado 0: valor existente (0)
        self.assertIsNotNone(self.it['w']['positiva'][i0])

    def test_ate_posterior_continua_o_decaimento_sem_inventar_carga(self):
        r2 = self.conj(ate=dia(200))
        w = r2['modalidades']['Bike']['w']
        self.assertAlmostEqual(w['positiva'][self.i + 1 + 100], 10.0 * math.exp(-100 / 40.0), delta=2e-3)
        self.assertEqual(len(w['positiva']), 201 - (r2['datas'].index(r2['datas'][0])))

    def test_lag_zero_do_ajuste_e_respeitado(self):
        ajs = {'Bike': aj('Bike', ir.EST_IDENTIFICAVEL, k1=0.1, k2=0.2, lag=0, primeira_obs=0, ultima_obs=60)}
        self.aplica(ajs, [ativ('Bike', 10, 100.0)])
        it = self.conj()['modalidades']['Bike']
        i = self.conj()['datas'].index(dia(10))
        self.assertEqual(it['lag_dias'], 0)
        self.assertAlmostEqual(it['w']['positiva'][i], 10.0, delta=1e-3)

    def test_lags_diferentes_ficam_assinalados(self):
        ajs = {'Bike': aj('Bike', ir.EST_IDENTIFICAVEL, lag=1, primeira_obs=0, ultima_obs=60),
               'Row': aj('Row', ir.EST_IDENTIFICAVEL, lag=0, primeira_obs=0, ultima_obs=60)}
        self.aplica(ajs, [ativ('Bike', 10, 100.0), ativ('Row', 10, 100.0)])
        r = self.conj()
        self.assertTrue(r['convencao_temporal']['lags_diferentes'])
        self.assertEqual(r['convencao_temporal']['lag_dias'], [0, 1])


class SemDuplaContagem(_Mock, unittest.TestCase):
    def setUp(self):
        self.ajs = {m: aj(m, ir.EST_IDENTIFICAVEL, primeira_obs=0, ultima_obs=60) for m in MODS}

    def serie(self, at, m='Bike'):
        self.aplica(self.ajs, at)
        return self.conj()['modalidades'][m]['w']

    def test_mesma_atividade_duas_vezes_conta_uma(self):
        a = ativ('Bike', 10, 80.0, aid='X')
        self.assertEqual(self.serie([a]), self.serie([a, copy.deepcopy(a)]))

    def test_duas_atividades_no_mesmo_dia_somam(self):
        um = self.serie([ativ('Bike', 10, 80.0, aid='A')])
        dois = self.serie([ativ('Bike', 10, 30.0, aid='A'), ativ('Bike', 10, 50.0, aid='B')])
        self.assertEqual(um, dois)

    def test_outras_modalidades_nao_entram_nesta_curva(self):
        so_bike = self.serie([ativ('Bike', 10, 80.0)])
        com_outras = self.serie([ativ('Bike', 10, 80.0), ativ('Row', 10, 99.0), ativ('Run', 11, 99.0),
                                 ativ('Ski', 12, 99.0)])
        self.assertEqual(so_bike, com_outras)

    def test_musculacao_nao_entra_em_nenhuma_curva(self):
        self.aplica(self.ajs, [ativ('Bike', 10, 80.0), ativ('Bike', 11, 500.0, tipo='WeightTraining')])
        r = self.conj()
        ref = self.serie([ativ('Bike', 10, 80.0)])
        self.assertEqual(r['modalidades']['Bike']['w'], ref)

    def test_carga_negativa_ou_ausente_nao_conta(self):
        a = [ativ('Bike', 10, 80.0), ativ('Bike', 11, -50.0, aid='n'), ativ('Bike', 12, None, aid='z')]
        self.assertEqual(self.serie(a), self.serie([ativ('Bike', 10, 80.0)]))


class EstadosSemCurva(_Mock, unittest.TestCase):
    def curvas_nulas(self, it):
        self.assertFalse(it['tem_curva'])
        self.assertIsNone(it['w'])
        self.assertIsNone(it['pct'])
        self.assertIsNone(it['plausibilidade'])
        self.assertIsNone(it['inicio_serie'])

    def test_sem_ajuste_nao_desenha_nem_inventa_zeros(self):
        self.aplica({'Bike': aj('Bike', ir.EST_IDENTIFICAVEL, primeira_obs=0, ultima_obs=60)},
                    [ativ('Bike', 10, 80.0), ativ('Row', 10, 80.0)])
        r = self.conj()
        for m in ('Row', 'Run', 'Ski'):
            it = r['modalidades'][m]
            self.assertEqual(it['estado'], 'sem_ajuste')
            self.curvas_nulas(it)
            self.assertIn('ainda nao ha ajuste', it['leitura'])
        self.assertEqual(r['modalidades_com_curva'], ['Bike'])
        self.assertEqual(r['modalidades_sem_curva'], ['Row', 'Run', 'Ski'])
        self.assertEqual(r['estado'], 'ok')

    def test_dados_insuficientes_e_sem_convergencia(self):
        self.aplica({'Bike': aj('Bike', ir.EST_INSUFICIENTE), 'Row': aj('Row', ir.EST_SEM_CONVERGENCIA)},
                    [ativ('Bike', 10, 80.0), ativ('Row', 10, 80.0)])
        r = self.conj()
        b, w = r['modalidades']['Bike'], r['modalidades']['Row']
        self.assertEqual((b['estado'], w['estado']), ('dados_insuficientes', 'sem_convergencia'))
        self.curvas_nulas(b)
        self.curvas_nulas(w)
        self.assertIsNone(b['parametros'])
        self.assertIn('dados insuficientes', b['leitura'])
        self.assertIn('nao convergiu', w['leitura'])
        self.assertEqual(r['estado'], 'sem_curvas')
        self.assertEqual(r['datas'], [])

    def test_sem_nenhum_ajuste_nada_e_calculado(self):
        self.aplica({}, [ativ('Bike', 10, 80.0)])
        r = self.conj()
        self.assertEqual(r['estado'], 'sem_curvas')
        self.assertEqual(r['datas'], [])
        self.assertTrue(all(not it['tem_curva'] for it in r['modalidades'].values()))

    def test_ajuste_valido_mas_sem_atividades_na_base(self):
        ajs = {'Bike': aj('Bike', ir.EST_IDENTIFICAVEL, primeira_obs=0, ultima_obs=60)}
        self.aplica(ajs, None)
        r = mis.curvas_conjuntas(1200, atividades=None)
        it = r['modalidades']['Bike']
        self.assertEqual(it['estado'], 'sem_atividades')
        self.curvas_nulas(it)
        self.assertIsNotNone(it['parametros'])                      # os parametros continuam visiveis na tabela

    def test_outra_duracao_nao_aproveita_ajuste_de_1200(self):
        self.aplica({'Bike': aj('Bike', ir.EST_IDENTIFICAVEL, dur=1200, primeira_obs=0, ultima_obs=60)},
                    [ativ('Bike', 10, 80.0)])
        r = mis.curvas_conjuntas(300, atividades=self.at)
        self.assertEqual(r['modalidades']['Bike']['estado'], 'sem_ajuste')


class Identificabilidade(_Mock, unittest.TestCase):
    def test_nao_identificavel_e_identificavel_distinguem_se(self):
        self.aplica({'Bike': aj('Bike', ir.EST_IDENTIFICAVEL, primeira_obs=0, ultima_obs=60),
                     'Row': aj('Row', ir.EST_NAO_IDENTIFICAVEL, primeira_obs=0, ultima_obs=60,
                               avisos=['parametro_no_limite:t1:inferior', 'parametro_no_limite:k2:superior',
                                       'ic_perfil_toca_limite:t1'])},
                    [ativ('Bike', 10, 80.0), ativ('Row', 10, 80.0)])
        r = self.conj()
        b, w = r['modalidades']['Bike'], r['modalidades']['Row']
        self.assertEqual((b['estado'], b['parametros_identificados']), ('identificavel', True))
        self.assertEqual((w['estado'], w['parametros_identificados']), ('nao_identificavel', False))
        self.assertEqual(w['no_limite'], [{'parametro': 't1', 'lado': 'inferior'},
                                          {'parametro': 'k2', 'lado': 'superior'}])
        self.assertIn('ic_perfil_toca_limite:t1', w['avisos_identificabilidade'])
        self.assertEqual(b['no_limite'], [])
        for it in (b, w):                                   # as metricas ja guardadas chegam intactas
            self.assertEqual(it['ajuste']['n_obs'], 20)
            self.assertEqual(it['ajuste']['rmse'], 2.5)
            self.assertEqual(it['ajuste']['r2'], 0.9)

    def test_valores_acima_de_100_pct_sao_assinalados_e_nao_cortados(self):
        # K2 enorme: a negativa ultrapassa em muito o P0 (como em Run/Ski com o export real)
        self.aplica({'Run': aj('Run', ir.EST_NAO_IDENTIFICAVEL, p0=100.0, k1=0.01, k2=2.0, t1=40.0, t2=10.0,
                               primeira_obs=0, ultima_obs=60)},
                    [ativ('Run', i, 60.0) for i in range(0, 60, 2)])
        it = self.conj()['modalidades']['Run']
        pl = it['plausibilidade']
        self.assertTrue(pl['excede'])
        self.assertEqual(pl['limite_pct'], 100.0)
        self.assertGreater(pl['dias_acima']['negativa'], 0)
        self.assertGreater(pl['max_abs_pct']['negativa'], 100.0)
        self.assertEqual(pl['max_abs_pct']['negativa'], max(abs(v) for v in it['pct']['negativa'] if v is not None))
        self.assertEqual(pl['dias_acima']['negativa'],
                         sum(1 for v in it['pct']['negativa'] if v is not None and abs(v) > 100.0))
        self.assertLess(min(v for v in it['pct']['negativa'] if v is not None), -100.0)   # sem corte

    def test_ajuste_plausivel_nao_e_assinalado(self):
        self.aplica({'Bike': aj('Bike', ir.EST_IDENTIFICAVEL, p0=300.0, k1=0.02, k2=0.03, primeira_obs=0, ultima_obs=60)},
                    [ativ('Bike', i, 50.0) for i in range(0, 60, 3)])
        pl = self.conj()['modalidades']['Bike']['plausibilidade']
        self.assertFalse(pl['excede'])
        self.assertEqual(pl['dias_acima'], {'positiva': 0, 'negativa': 0, 'liquida': 0})

    def test_nao_identificavel_nao_vira_identificavel_nem_muda_parametros(self):
        a = aj('Row', ir.EST_NAO_IDENTIFICAVEL, p0=250.0, k1=0.03, k2=0.04, t1=20.0, t2=1.0,
               primeira_obs=0, ultima_obs=60, avisos=['parametro_no_limite:t1:inferior'])
        self.aplica({'Row': a}, [ativ('Row', 5, 80.0)])
        it = self.conj()['modalidades']['Row']
        self.assertEqual(it['parametros'], {'p0': 250.0, 'k1': 0.03, 'k2': 0.04, 't1': 20.0, 't2': 1.0})
        self.assertFalse(it['parametros_identificados'])

    def test_p0_nao_positivo_nao_produz_percentagens(self):
        for p0 in (0.0, -5.0):
            ajs = {'Bike': aj('Bike', ir.EST_NAO_IDENTIFICAVEL, p0=p0, primeira_obs=0, ultima_obs=60)}
            self.aplica(ajs, [ativ('Bike', 10, 80.0)])
            it = self.conj()['modalidades']['Bike']
            self.assertTrue(it['tem_curva'])
            self.assertEqual(it['normalizacao'], 'indisponivel')
            self.assertEqual(it['motivo_normalizacao'], 'p0_invalido_ou_nao_positivo')
            self.assertIsNone(it['pct'])
            self.assertIsNone(it['plausibilidade'])
            self.assertIsNotNone(it['w'])                              # os watts continuam la

    def test_p0_nao_finito_nao_produz_percentagens(self):
        for p0 in (float('nan'), float('inf')):
            self.assertFalse(mis._p0_valido(p0))
        for p0 in (None, True, '300'):
            self.assertFalse(mis._p0_valido(p0))
        self.assertTrue(mis._p0_valido(300.0))


class Overall(_Mock, unittest.TestCase):
    def test_overall_nao_esta_disponivel_e_explica_o_que_falta(self):
        self.aplica(*cenario_base())
        r = self.conj()
        o = r['overall']
        self.assertIs(o['disponivel'], False)
        self.assertGreater(len(o['motivo']), 40)
        self.assertGreaterEqual(len(o['falta']), 3)
        self.assertIn('/api/modelo/ir/global', o['referencia'])
        self.assertNotIn('overall', r['modalidades'])
        for k in ('w', 'pct', 'datas'):
            self.assertNotIn(k, o)

    def test_overall_nao_e_soma_nem_media_das_modalidades(self):
        """Garantia estrutural: nenhuma serie 'global' e derivada somando watts de modalidades."""
        src = open(os.path.join(ROOT, 'modelo_ir_servico.py'), encoding='utf-8').read()
        tree = ast.parse(src)
        f = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'curvas_conjuntas')
        nomes = {n.id for n in ast.walk(f) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(f) if isinstance(n, ast.Attribute)}
        for proibido in ('mean', 'average', 'median', 'analise_global', 'ajustar_global', 'sum_modalidades'):
            self.assertNotIn(proibido, nomes)
        chamadas = [n.func.id for n in ast.walk(f) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertNotIn('sum', chamadas)


class ApenasLeitura(_Mock, unittest.TestCase):
    def test_nao_recalibra_nem_chama_o_otimizador_nem_grava(self):
        self.aplica(*cenario_base())
        proibidos = []
        for obj, nome in ((mis, 'recalibrar'), (mis, 'analise_global'), (mis, 'validar_preditivo'),
                          (mis, 'apos_deteccao'), (ir, 'ajustar'), (ir, 'ajustar_observacoes'),
                          (db, 'inserir_ajuste_ir'), (db, 'inserir_observacao_auto'),
                          (db, 'inserir_revisao_teste'), (db, 'upsert_activities'), (db, 'upsert_power_curves')):
            p = mock.patch.object(obj, nome, side_effect=AssertionError(f'{nome} chamado pela visualizacao'))
            proibidos.append(p.start())
            self.addCleanup(p.stop)
        r = self.conj()
        self.assertEqual(r['estado'], 'ok')
        for m in proibidos:
            m.assert_not_called()

    def test_o_codigo_novo_nao_referencia_modelos_fora_do_ambito(self):
        src = open(os.path.join(ROOT, 'modelo_ir_servico.py'), encoding='utf-8').read()
        tree = ast.parse(src)
        fs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
              ('curvas_conjuntas', '_p0_valido', '_no_limite', '_estado_conjunto', '_plausibilidade')]
        self.assertEqual(len(fs), 5)
        for f in fs:
            nomes = {n.id for n in ast.walk(f) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(f) if isinstance(n, ast.Attribute)}
            for proibido in ('pmc', 'ftlm', 'calibracao', 'ctl', 'atl', 'tsb', 'recalibrar', 'ajustar',
                             'inserir_ajuste_ir', 'upsert_power_curves', 'sync', 'api_client', 'requests'):
                self.assertNotIn(proibido, nomes, f.name)

    def test_nao_altera_as_entradas(self):
        ajs, at = cenario_base()
        a0, t0 = copy.deepcopy(ajs), copy.deepcopy(at)
        self.aplica(ajs, at)
        self.conj()
        self.assertEqual((ajs, at), (a0, t0))

    def test_resultado_e_json_serializavel_e_deterministico(self):
        import json
        self.aplica(*cenario_base())
        a, b = self.conj(), self.conj()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))


class Cores(_Mock, unittest.TestCase):
    def test_cores_vem_de_config_cores_mod(self):
        self.aplica(*cenario_base())
        r = self.conj()
        self.assertEqual(r['modalidades_ordem'], list(config.CICLICOS))
        for m in MODS:
            self.assertEqual(r['cores'][m], config.CORES_MOD[m])
            self.assertEqual(r['modalidades'][m]['cor'], config.CORES_MOD[m])
        self.assertEqual(len(set(r['cores'].values())), 4)


class RotaApi(_Mock, unittest.TestCase):
    def setUp(self):
        self.aplica(*cenario_base())
        p = mock.patch.object(db, 'ENABLED', True)
        p.start()
        self.addCleanup(p.stop)
        self.c = APP.app.test_client()
        self.u = '/api/modelo/ir/conjunto'

    def test_ok(self):
        r = self.c.get(self.u + '?duracao=1200')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual((j['status'], j['tipo_analise'], j['duracao_s']), ('OK', 'curvas_influencia_ir_conjunto', 1200))
        self.assertEqual(j['modalidades_com_curva'], MODS)

    def test_parametros_invalidos_400(self):
        for q, campo in (('?duracao=300', 'duracao'), ('?duracao=123', 'duracao'), ('?duracao=abc', 'duracao'),
                         ('?duracao=1200&tipo=Bike', 'tipo'), ('?duracao=1200&ate=ontem', 'ate'),
                         ('?duracao=1200&ate=2026-13-45', 'ate')):
            r = self.c.get(self.u + q)
            self.assertEqual(r.status_code, 400, q)
            self.assertEqual(r.get_json().get('campo'), campo, q)

    def test_sem_base_de_dados_503(self):
        with mock.patch.object(db, 'ENABLED', False):
            self.assertEqual(self.c.get(self.u + '?duracao=1200').status_code, 503)

    def test_so_get(self):
        for metodo in ('post', 'put', 'delete', 'patch'):
            self.assertEqual(getattr(self.c, metodo)(self.u + '?duracao=1200').status_code, 405, metodo)

    def test_erro_interno_devolve_500_json(self):
        with mock.patch.object(mis, 'curvas_conjuntas', side_effect=RuntimeError('x')):
            r = self.c.get(self.u + '?duracao=1200')
        self.assertEqual(r.status_code, 500)
        self.assertIn('RuntimeError', r.get_json()['mensagem'])


class SemEscritasNaBase(unittest.TestCase):
    """Com uma SQLite real: abrir/explorar o conjunto nao altera nenhuma tabela."""

    def test_nenhuma_tabela_muda(self):
        import test_modelo_ir_servico as S
        base = S._Base('prepara')
        base.setUp()
        try:
            base.prepara()
            base.rec()
            tabelas = [r[0] for r in db._exec("SELECT name FROM sqlite_master WHERE type='table'", fetch='all')]
            def foto():
                return {t: db._exec(f'SELECT COUNT(*) FROM "{t}"', fetch='one')[0] for t in tabelas if t != 'sqlite_sequence'}
            def conteudo():
                return {t: sorted(map(repr, db._exec(f'SELECT * FROM "{t}"', fetch='all') or []))
                        for t in tabelas if t != 'sqlite_sequence'}
            antes, c0 = foto(), conteudo()
            for q in ('', '?duracao=1200', '?duracao=1200&ate=2030-01-01'):
                r = base.c.get('/api/modelo/ir/conjunto' + q)
                self.assertEqual(r.status_code, 200, q)
            self.assertEqual(foto(), antes)
            self.assertEqual(conteudo(), c0)
            j = base.c.get('/api/modelo/ir/conjunto?duracao=1200').get_json()
            self.assertEqual(j['estado'], 'ok')
            self.assertEqual(j['modalidades_com_curva'], ['Bike'])           # cenario sintetico so tem Bike
            for m in ('Row', 'Run', 'Ski'):
                self.assertEqual(j['modalidades'][m]['estado'], 'sem_ajuste')
            direta = mis.curva_influencia('Bike', 1200, atividades=base.ativ)
            self.assertEqual(j['modalidades']['Bike']['parametros'], {k: direta['parametros'][k] for k in ir.NOMES})
            n = len(direta['curvas']['datas'])
            self.assertEqual(len(j['datas']), n)
            for x, y in zip(j['modalidades']['Bike']['w']['positiva'], direta['curvas']['positiva']):
                self.assertAlmostEqual(x, y, places=6)
        finally:
            base.doCleanups()


if __name__ == '__main__':
    unittest.main()
