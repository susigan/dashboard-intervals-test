"""Nucleo do modelo IR GLOBAL de referencia (modelo_ir_global.py). Dados SINTETICOS.

Testam o codigo e a calibracao dos testes, nao o atleta. O global e uma analise separada do
modelo por modalidade; aqui prova-se que recupera o que se sabe (dinamica partilhada,
transferencia), que rejeita o que e falso (dinamica nao partilhada) e que diz 'nao sei' quando
os dados nao chegam.
"""
import copy
import json
import math
import os
import sys
import unittest
from unittest import mock
from datetime import date, timedelta

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
import modelo_ir as ir  # noqa: E402
import modelo_ir_global as G  # noqa: E402

D0 = date(2025, 1, 6)
P0 = {'Bike': 300.0, 'Row': 260.0, 'Run': 330.0, 'Ski': 200.0}
TRUE = dict(k1=4e-5, k2=1.2e-4, t1=45.0, t2=8.0)
SEM_BOOT = {'bootstrap_B': 0}


def gerar(lam=1.0, het=(), sigma=0.4, n_obs=None, semente=1, n_dias=520, verdade=None, mods=('Bike', 'Row', 'Run', 'Ski')):
    """(unidades, carga_mod). Verdade: y = P0_u*(1 + K1*E - K2*G), carga efetiva = propria + lam*outras."""
    n_obs = n_obs or {'Bike': 25, 'Row': 18, 'Run': 14, 'Ski': 10}
    rng = np.random.default_rng(semente)
    prob = {'Bike': 0.5, 'Row': 0.25, 'Run': 0.25, 'Ski': 0.12}
    carga = {m: np.where(rng.random(n_dias) < prob[m], rng.uniform(30, 120, n_dias), 0.0) for m in mods}
    tot = sum(carga.values())
    v0 = verdade or TRUE
    unidades = []
    for m in mods:
        pr = dict(v0)
        if m in het:
            pr = dict(k1=v0['k1'] * 0.3, k2=v0['k2'] * 2.0, t1=90.0, t2=3.0)
        c = carga[m] + lam * (tot - carga[m])
        perf = 1 + ir.desempenho(c, 0.0, pr['k1'], pr['k2'], pr['t1'], pr['t2'], 1)
        idx = np.sort(rng.choice(np.arange(40, n_dias), n_obs[m], replace=False))
        y = P0[m] * (perf[idx] + rng.normal(0, sigma / 100, n_obs[m]))
        unidades.append({'id': f'{m}:1200', 'modalidade': m,
                         'datas': [(D0 + timedelta(days=int(i))).isoformat() for i in idx], 'y': list(y)})
    cm = {m: {(D0 + timedelta(days=i)).isoformat(): float(v) for i, v in enumerate(c) if v > 0}
          for m, c in carga.items()}
    return unidades, cm


class Definicao(unittest.TestCase):
    def test_p0_em_fecho_minimiza_o_erro_e_o_residuo_e_ortogonal(self):
        rng = np.random.default_rng(3)
        f = rng.normal(0, 0.1, 30)
        y = 100 * (1 + f) + rng.normal(0, 1, 30)
        p0 = G._p0_fecho(y, f)
        z = 1 + f
        self.assertAlmostEqual(float(np.dot(y - p0 * z, z)), 0.0, places=9)
        for d in (-0.5, 0.5):
            self.assertGreater(np.sum((y - (p0 + d) * z) ** 2), np.sum((y - p0 * z) ** 2))

    def test_residuos_no_modelo_verdadeiro_sao_nulos_sem_ruido(self):
        un, cm = gerar(sigma=0.0)
        unds, _, _ = G._prepara(un, cm, G._cfg())
        r = G._residuos(unds, [TRUE['k1'], TRUE['k2'], TRUE['t1'], TRUE['t2'], 1.0], 1, G._grupos(unds))
        self.assertLess(float(np.max(np.abs(r))), 1e-9)
        # com outro lambda ja nao
        r0 = G._residuos(unds, [TRUE['k1'], TRUE['k2'], TRUE['t1'], TRUE['t2'], 0.0], 1, G._grupos(unds))
        self.assertGreater(float(np.max(np.abs(r0))), 1e-3)

    def test_carga_efetiva_propria_mais_lambda_vezes_as_outras_inclui_modalidades_sem_unidade(self):
        un, cm = gerar(mods=('Bike', 'Row', 'Run'))
        so_duas = [u for u in un if u['modalidade'] in ('Bike', 'Row')]
        unds, d0, d1 = G._prepara(so_duas, cm, G._cfg())            # Run nao tem unidade, mas conta como 'outras'
        bike = next(u for u in unds if u['modalidade'] == 'Bike')
        dia = lambda m: ir.cargas_diarias({k: v for k, v in cm[m].items() if k <= d1}, d0, d1)  # noqa: E731
        np.testing.assert_allclose(bike['propria'], dia('Bike'), atol=1e-9)
        np.testing.assert_allclose(bike['outras'], dia('Row') + dia('Run'), atol=1e-9)
        self.assertGreater(float(np.sum(dia('Run'))), 0)

    def test_unidades_do_alvo_sao_relativas_k_em_w_vem_de_k_relativo_e_p0(self):
        un, cm = gerar(sigma=0.3, semente=4)
        r = G.ajustar_global(un, cm, SEM_BOOT)
        p = r['parametros']
        for u in r['por_unidade']:
            self.assertAlmostEqual(u['k1_w_por_carga'], p['k1_rel'] * u['p0_w'], places=12)
            self.assertAlmostEqual(u['k2_w_por_carga'], p['k2_rel'] * u['p0_w'], places=12)
        # P0 em watts recupera a ordem de grandeza de cada unidade
        for u in r['por_unidade']:
            self.assertAlmostEqual(u['p0_w'], P0[u['modalidade']], delta=0.05 * P0[u['modalidade']])


class Recuperacao(unittest.TestCase):
    def _rodar(self, lam, semente, **kw):
        un, cm = gerar(lam=lam, semente=semente, **kw)
        return G.ajustar_global(un, cm, SEM_BOOT)

    def test_exato_sem_ruido(self):
        r = self._rodar(1.0, 1, sigma=0.0)
        p = r['parametros']
        self.assertLess(r['metricas']['sse_pct'], 1e-6)
        self.assertAlmostEqual(p['t1'], 45.0, delta=0.5)
        self.assertAlmostEqual(p['t2'], 8.0, delta=0.2)
        self.assertAlmostEqual(p['lambda'], 1.0, delta=0.02)
        self.assertAlmostEqual(p['k1_rel'], TRUE['k1'], delta=0.03 * TRUE['k1'])
        self.assertAlmostEqual(p['k2_rel'], TRUE['k2'], delta=0.03 * TRUE['k2'])

    def test_transferencia_total(self):
        r = self._rodar(1.0, 100)
        p, t = r['parametros'], r['transferencia']
        self.assertGreater(p['lambda'], 0.8)
        self.assertLess(t['p_lambda_0'], 0.05)                    # 'independentes' rejeitado
        self.assertGreater(t['p_lambda_1'], 0.05)                 # 'carga somada' nao
        self.assertIn('ha transferencia', t['leitura'])
        self.assertAlmostEqual(p['t1'], 45.0, delta=0.2 * 45)
        self.assertAlmostEqual(p['t2'], 8.0, delta=0.25 * 8)
        # Run (14 obs) e Ski (10) nao entram no teste de homogeneidade: a dinamica partilhada fica
        # assumida para elas e o estado nao pode ser 'identificavel'
        self.assertEqual(r['estado'], G.EST_NAO_IDENTIFICAVEL, r['identificabilidade'])
        self.assertEqual(r['identificabilidade']['avisos'], ['homogeneidade_parcial'])

    def test_transferencia_total_com_todas_as_unidades_testadas_e_identificavel(self):
        un, cm = gerar(lam=1.0, semente=102, n_obs={'Bike': 20, 'Row': 20, 'Run': 20, 'Ski': 20})
        r = G.ajustar_global(un, cm, SEM_BOOT)
        self.assertEqual(r['homogeneidade']['unidades_nao_testadas'], [])
        self.assertEqual(r['estado'], G.EST_IDENTIFICAVEL, r['identificabilidade'])
        self.assertFalse(r['homogeneidade']['rejeitada'])

    def test_sem_transferencia(self):
        r = self._rodar(0.0, 101)
        t = r['transferencia']
        self.assertLess(r['parametros']['lambda'], 0.15)
        self.assertLess(t['p_lambda_1'], 0.05)                    # 'carga somada' rejeitado
        self.assertGreater(t['p_lambda_0'], 0.05)
        self.assertIn('sem transferencia clara', t['leitura'])

    def test_transferencia_parcial(self):
        r = self._rodar(0.5, 102)
        t = r['transferencia']
        self.assertAlmostEqual(r['parametros']['lambda'], 0.5, delta=0.15)
        self.assertLess(t['p_lambda_0'], 0.05)
        self.assertLess(t['p_lambda_1'], 0.05)
        self.assertIn('parcial', t['leitura'])

    def test_calibracao_sob_dinamica_partilhada_poucos_falsos_alarmes(self):
        rej = 0
        for sd in range(400, 412):
            r = self._rodar(1.0 if sd % 2 else 0.0, sd)
            h = r['homogeneidade']
            if h.get('testavel'):
                rej += bool(h['p'] < 0.05)
        self.assertLessEqual(rej, 3)                              # nivel nominal 5% (12 casos): folga para o F indicativo

    def test_dinamica_nao_partilhada_e_detetada(self):
        r = self._rodar(1.0, 200, het=('Row', 'Run'))
        self.assertEqual(r['estado'], G.EST_HETEROGENEO)
        self.assertTrue(r['homogeneidade']['rejeitada'])
        self.assertIn('dinamica_nao_partilhada', r['identificabilidade']['avisos'])
        self.assertLess(r['homogeneidade']['p'], 0.01)

    def test_homogeneo_nao_e_marcado_heterogeneo(self):
        r = self._rodar(1.0, 100)
        self.assertNotEqual(r['estado'], G.EST_HETEROGENEO)
        self.assertFalse(r['homogeneidade']['rejeitada'])


class Bootstrap(unittest.TestCase):
    def test_determinista_e_corrige_o_f(self):
        un, cm = gerar(lam=1.0, semente=100)
        cfg = G._cfg({'bootstrap_B': 5, 'alfa': 0.5})
        unds, _, _ = G._prepara(un, cm, cfg)
        core = G._ajusta_pool(unds, cfg)
        est = G._estat_homogeneidade(unds, core[0], cfg, float(core[0][4]))
        a = G._bootstrap_homogeneidade(unds, core[0], cfg, est)
        b = G._bootstrap_homogeneidade(unds, core[0], cfg, est)
        self.assertEqual(a, b)                                    # semente derivada dos dados
        self.assertLessEqual(a['B_feito'], 5)
        self.assertEqual(a['metodo'], 'bootstrap_parametrico_sob_H0')
        self.assertGreater(a['p'], 0)

    def test_heterogeneo_percorre_todas_as_replicas_e_da_o_p_minimo(self):
        un, cm = gerar(lam=1.0, semente=200, het=('Row', 'Run'))
        cfg = G._cfg({'bootstrap_B': 9, 'alfa': 0.2})
        unds, _, _ = G._prepara(un, cm, cfg)
        core = G._ajusta_pool(unds, cfg)
        est = G._estat_homogeneidade(unds, core[0], cfg, float(core[0][4]))
        b = G._bootstrap_homogeneidade(unds, core[0], cfg, est)
        self.assertEqual(b['B_feito'], 9)
        self.assertEqual(b['excedencias'], 0)
        self.assertAlmostEqual(b['p'], 1 / 10)

    def test_para_cedo_quando_ja_nao_ha_rejeicao_possivel(self):
        un, cm = gerar(lam=1.0, semente=100)
        cfg = G._cfg({'bootstrap_B': 39, 'alfa': 0.05})
        unds, _, _ = G._prepara(un, cm, cfg)
        core = G._ajusta_pool(unds, cfg)
        est = G._estat_homogeneidade(unds, core[0], cfg, float(core[0][4]))
        est = {**est, 'sse_global': est['sse_por_unidade'] * 1.0}      # estatistica observada nula: todas as replicas a excedem
        b = G._bootstrap_homogeneidade(unds, core[0], cfg, est)
        self.assertLess(b['B_feito'], 39)
        self.assertGreater(b['p'], 0.05)

    def test_com_sinal_do_f_o_ajuste_global_usa_o_p_do_bootstrap(self):
        un, cm = gerar(lam=1.0, semente=200, het=('Row', 'Run'))
        r = G.ajustar_global(un, cm, {'bootstrap_B': 9, 'alfa': 0.2})
        h = r['homogeneidade']
        self.assertEqual(h['metodo_p'], 'bootstrap_parametrico_sob_H0')
        self.assertEqual(h['bootstrap']['B_feito'], 9)
        self.assertAlmostEqual(h['p'], (h['bootstrap']['excedencias'] + 1) / 10)
        self.assertLess(h['p_F_indicativo'], 0.2)
        self.assertTrue(h['rejeitada'])
        self.assertEqual(r['estado'], G.EST_HETEROGENEO)

    def test_replicas_falhadas_tornam_o_bootstrap_inconclusivo_e_nao_dao_veredicto(self):
        # Se quase todas as replicas falham, p = 1/(feitas+1) nao pode ficar abaixo de alfa: 'nao
        # rejeitado' seria ausencia de poder e nao evidencia. Tem de ficar inconclusivo.
        un, cm = gerar(lam=1.0, semente=200, het=('Row', 'Run'))
        real = G._estat_homogeneidade
        n = {'c': 0}

        def so_a_observada(*a, **k):
            n['c'] += 1
            return real(*a, **k) if n['c'] == 1 else None
        with mock.patch.object(G, '_estat_homogeneidade', so_a_observada):
            r = G.ajustar_global(un, cm, {'bootstrap_B': 9, 'alfa': 0.2})
        h = r['homogeneidade']
        self.assertEqual(h['bootstrap']['B_feito'], 0)
        self.assertFalse(h['conclusiva'])
        self.assertFalse(h['rejeitada'])
        self.assertIn('bootstrap_inconclusivo', r['identificabilidade']['avisos'])
        self.assertNotIn(r['estado'], (G.EST_IDENTIFICAVEL, G.EST_HETEROGENEO))

    def test_poucas_replicas_validas_nao_chegam_para_rejeitar_e_ficam_inconclusivas(self):
        # 3 replicas validas: o menor p possivel e 1/4 = 0,25 > alfa (0,2): 'nao rejeitado' nao diz nada
        un, cm = gerar(lam=1.0, semente=200, het=('Row', 'Run'))
        real = G._estat_homogeneidade
        n = {'c': 0}

        def so_tres_replicas(*a, **k):
            n['c'] += 1
            return real(*a, **k) if n['c'] <= 4 else None
        with mock.patch.object(G, '_estat_homogeneidade', so_tres_replicas):
            r = G.ajustar_global(un, cm, {'bootstrap_B': 9, 'alfa': 0.2})
        h = r['homogeneidade']
        self.assertEqual(h['bootstrap']['B_feito'], 3)
        self.assertFalse(h['bootstrap']['parou_cedo'])
        self.assertFalse(h['bootstrap']['conclusivo'])
        self.assertFalse(h['rejeitada'])
        self.assertIn('bootstrap_inconclusivo', r['identificabilidade']['avisos'])
        self.assertNotEqual(r['estado'], G.EST_IDENTIFICAVEL)

    def test_bootstrap_completo_e_conclusivo(self):
        un, cm = gerar(lam=1.0, semente=200, het=('Row', 'Run'))
        h = G.ajustar_global(un, cm, {'bootstrap_B': 9, 'alfa': 0.2})['homogeneidade']
        self.assertTrue(h['conclusiva'])
        self.assertFalse(h['bootstrap']['parou_cedo'])

    def test_paragem_precoce_fica_assinalada_e_continua_conclusiva(self):
        un, cm = gerar(lam=1.0, semente=100)
        cfg = G._cfg({'bootstrap_B': 39, 'alfa': 0.05})
        unds, _, _ = G._prepara(un, cm, cfg)
        core = G._ajusta_pool(unds, cfg)
        est = G._estat_homogeneidade(unds, core[0], cfg, float(core[0][4]))
        est = {**est, 'sse_global': est['sse_por_unidade'] * 1.0}
        b = G._bootstrap_homogeneidade(unds, core[0], cfg, est)
        self.assertTrue(b['parou_cedo'])
        self.assertTrue(b['conclusivo'])

    def test_triagem_pelo_f_so_corre_bootstrap_quando_ha_sinal(self):
        un, cm = gerar(lam=1.0, semente=100)
        r = G.ajustar_global(un, cm)                              # B por omissao, mas o F e alto: nao corre
        self.assertEqual(r['homogeneidade']['metodo_p'], 'F_indicativo')
        self.assertNotIn('bootstrap', r['homogeneidade'])


class Insuficiencia(unittest.TestCase):
    def ok(self, r, motivo):
        self.assertEqual(r['estado'], G.EST_INSUFICIENTE)
        self.assertIsNone(r['parametros'])
        self.assertIn(motivo, r['motivo'])

    def test_menos_de_duas_unidades(self):
        un, cm = gerar()
        self.ok(G.ajustar_global(un[:1], cm), 'menos_de_2_unidades')

    def test_unidade_com_poucas_observacoes_e_excluida_e_listada(self):
        un, cm = gerar(n_obs={'Bike': 25, 'Row': 18, 'Run': 14, 'Ski': 3})
        r = G.ajustar_global(un, cm, SEM_BOOT)
        self.assertEqual(r['unidades_excluidas'], ['Ski:1200'])
        self.assertEqual({u['id'] for u in r['por_unidade']}, {'Bike:1200', 'Row:1200', 'Run:1200'})

    def test_poucas_observacoes_no_total(self):
        un, cm = gerar(n_obs={'Bike': 6, 'Row': 6, 'Run': 5, 'Ski': 5})
        self.ok(G.ajustar_global(un, cm), 'observacoes_no_total')

    def test_cobertura_temporal_curta(self):
        un, cm = gerar(n_dias=150, n_obs={'Bike': 25, 'Row': 20, 'Run': 20, 'Ski': 20})
        self.ok(G.ajustar_global(un, cm), 'cobertura')

    def test_poucas_datas_distintas(self):
        un, cm = gerar(n_obs={'Bike': 25, 'Row': 18, 'Run': 14, 'Ski': 10})
        seis = un[0]['datas'][:6]
        for u in un:                                              # todas as unidades nas mesmas 6 datas
            u['datas'] = (seis * 10)[:len(u['datas'])]
        self.ok(G.ajustar_global(un, cm), 'datas_distintas')

    def test_alvo_constante(self):
        un, cm = gerar()
        for u in un:
            u['y'] = [250.0] * len(u['y'])
        self.ok(G.ajustar_global(un, cm), 'alvo_constante')

    def test_sem_carga(self):
        un, cm = gerar()
        self.ok(G.ajustar_global(un, {m: {} for m in cm}), 'sem_carga')

    def test_nunca_levanta_com_entrada_degenerada(self):
        un, cm = gerar()
        for u in un:
            u['y'] = [float(v) for v in u['y']]
        r = G.ajustar_global([], cm)
        self.assertEqual(r['estado'], G.EST_INSUFICIENTE)


class Identificabilidade(unittest.TestCase):
    def test_homogeneidade_nao_testavel_com_poucas_observacoes_por_unidade(self):
        un, cm = gerar(lam=1.0, semente=7, n_obs={'Bike': 12, 'Row': 10, 'Run': 9, 'Ski': 8}, n_dias=700)
        r = G.ajustar_global(un, cm, SEM_BOOT)
        self.assertIn('homogeneidade_nao_testavel', r['identificabilidade']['avisos'])
        self.assertNotEqual(r['estado'], G.EST_IDENTIFICAVEL)         # nao se afirma dinamica partilhada sem a testar
        self.assertFalse(r['homogeneidade']['testavel'])

    def test_unidades_fora_do_teste_de_homogeneidade_nao_ficam_como_identificaveis(self):
        # Run (14 obs) e Ski (10) ficam abaixo do minimo do teste (15): o teste so cobre Bike e Row.
        # Afirmar 'dinamica partilhada' para as 4 sem avisar era um defeito (auditoria).
        un, cm = gerar(lam=1.0, semente=300)
        r = G.ajustar_global(un, cm, SEM_BOOT)
        h = r['homogeneidade']
        self.assertEqual(sorted(d['id'] for d in h['unidades']), ['Bike:1200', 'Row:1200'])
        self.assertEqual(h['unidades_nao_testadas'], ['Run:1200', 'Ski:1200'])
        self.assertIn('homogeneidade_parcial', r['identificabilidade']['avisos'])
        self.assertNotEqual(r['estado'], G.EST_IDENTIFICAVEL)

    def test_com_todas_as_unidades_testadas_nao_ha_aviso_de_cobertura(self):
        un, cm = gerar(lam=1.0, semente=300, n_obs={'Bike': 20, 'Row': 20, 'Run': 20, 'Ski': 20})
        r = G.ajustar_global(un, cm, SEM_BOOT)
        self.assertEqual(r['homogeneidade']['unidades_nao_testadas'], [])
        self.assertNotIn('homogeneidade_parcial', r['identificabilidade']['avisos'])

    def test_parametro_no_limite_e_assinalado(self):
        un, cm = gerar(lam=1.0, semente=3)
        lim = copy.deepcopy(G.CONFIG_GLOBAL['limites'])
        lim['k1'] = [0.0, 1e-6]                                       # teto muito abaixo do verdadeiro
        r = G.ajustar_global(un, cm, {'limites': lim, **SEM_BOOT})
        self.assertIn('parametro_no_limite:k1', r['identificabilidade']['avisos'])
        self.assertNotEqual(r['estado'], G.EST_IDENTIFICAVEL)

    def test_transferencia_nao_identificada_quando_as_cargas_sao_colineares(self):
        un, cm = gerar(lam=1.0, semente=5)
        outras = {m: dict(cm['Bike']) for m in cm}                    # todas as modalidades com a mesma carga
        r = G.ajustar_global(un, outras, SEM_BOOT)
        self.assertIn('transferencia_nao_identificada', r['identificabilidade']['avisos'])
        self.assertNotEqual(r['estado'], G.EST_IDENTIFICAVEL)

    def test_estado_e_sempre_um_dos_conhecidos_e_sem_validade_preditiva(self):
        un, cm = gerar(semente=9)
        r = G.ajustar_global(un, cm, SEM_BOOT)
        self.assertIn(r['estado'], (G.EST_IDENTIFICAVEL, G.EST_NAO_IDENTIFICAVEL, G.EST_HETEROGENEO,
                                    G.EST_INSUFICIENTE, G.EST_SEM_CONVERGENCIA))
        self.assertEqual(r['validade_preditiva'], 'nao_avaliada')
        self.assertEqual(r['metricas']['tipo'], 'ajuste_historico_dentro_da_amostra')
        json.dumps(r)


class Invariancias(unittest.TestCase):
    def base(self):
        un, cm = gerar(lam=1.0, semente=100, sigma=0.3)
        return un, cm, G.ajustar_global(un, cm, SEM_BOOT)

    def test_reescalar_os_watts_de_uma_unidade_nao_muda_a_dinamica(self):
        un, cm, r0 = self.base()
        un2 = copy.deepcopy(un)
        un2[1]['y'] = [3.0 * v for v in un2[1]['y']]
        r1 = G.ajustar_global(un2, cm, SEM_BOOT)
        for k in ('k1_rel', 'k2_rel', 't1', 't2', 'lambda'):
            self.assertAlmostEqual(r1['parametros'][k], r0['parametros'][k], delta=1e-3 * abs(r0['parametros'][k]) + 1e-9, msg=k)
        u0 = {u['id']: u for u in r0['por_unidade']}
        u1 = {u['id']: u for u in r1['por_unidade']}
        self.assertAlmostEqual(u1['Row:1200']['p0_w'], 3 * u0['Row:1200']['p0_w'], delta=1e-3 * u0['Row:1200']['p0_w'] * 3)
        self.assertAlmostEqual(u1['Bike:1200']['p0_w'], u0['Bike:1200']['p0_w'], delta=1e-3 * u0['Bike:1200']['p0_w'])

    def test_a_ordem_das_unidades_nao_altera_o_resultado(self):
        un, cm, r0 = self.base()
        r1 = G.ajustar_global(list(reversed(un)), cm, SEM_BOOT)
        for k in ('k1_rel', 'k2_rel', 't1', 't2', 'lambda'):
            self.assertAlmostEqual(r1['parametros'][k], r0['parametros'][k], delta=1e-4 * abs(r0['parametros'][k]) + 1e-9, msg=k)
        self.assertEqual(r1['estado'], r0['estado'])

    def test_a_ordem_das_observacoes_dentro_da_unidade_nao_altera_o_resultado(self):
        un, cm, r0 = self.base()
        un2 = copy.deepcopy(un)
        for u in un2:
            z = list(zip(u['datas'], u['y']))[::-1]
            u['datas'], u['y'] = [a for a, _ in z], [b for _, b in z]
        r1 = G.ajustar_global(un2, cm, SEM_BOOT)
        self.assertAlmostEqual(r1['parametros']['t1'], r0['parametros']['t1'], places=4)

    def test_carga_posterior_a_ultima_observacao_nao_entra_sem_fuga_do_futuro(self):
        un, cm, r0 = self.base()
        ate = max(max(u['datas']) for u in un)
        cm2 = {m: dict(c) for m, c in cm.items()}
        d = date.fromisoformat(ate)
        for m in cm2:
            for i in range(1, 80):
                cm2[m][(d + timedelta(days=i)).isoformat()] = 500.0
        r1 = G.ajustar_global(un, cm2, SEM_BOOT)
        self.assertEqual(r1['parametros'], r0['parametros'])
        self.assertEqual(r1['metricas']['sse_pct'], r0['metricas']['sse_pct'])

    def test_a_configuracao_global_nao_e_mutada_nem_afeta_o_modelo_por_modalidade(self):
        c0, i0 = copy.deepcopy(G.CONFIG_GLOBAL), copy.deepcopy(ir.CONFIG_PADRAO)
        un, cm = gerar(semente=9)
        G.ajustar_global(un, cm, SEM_BOOT)
        self.assertEqual(G.CONFIG_GLOBAL, c0)
        self.assertEqual(ir.CONFIG_PADRAO, i0)

    def test_mesmos_limites_de_tempo_e_lag_do_modelo_por_modalidade(self):
        for k in ('t1', 't2'):
            self.assertEqual(G.CONFIG_GLOBAL['limites'][k], ir.CONFIG_PADRAO['limites'][k])
        self.assertEqual(G.CONFIG_GLOBAL['lag_dias'], ir.CONFIG_PADRAO['lag_dias'])
        self.assertLess(G.CONFIG_GLOBAL['limites']['t2'][1], G.CONFIG_GLOBAL['limites']['t1'][0])   # papeis nao trocam


if __name__ == '__main__':
    unittest.main()
