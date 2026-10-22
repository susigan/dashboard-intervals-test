"""Servico do modelo IR: ligacao as observacoes MMP, historico, recalibracao e avaliacao preditiva.

    python -m pytest tests/pmc/test_modelo_ir_servico.py

Offline. Dados SINTETICOS: testam o codigo, nao o atleta nem o modelo. SQLite temporaria; curvas
gravadas com db.upsert_power_curves (o caminho da sincronizacao); deteccao real (oa.atualizar).
"""
import copy
import json
import os
import re
import sys
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_mmp_deteccao_automatica as T  # noqa: E402
import cp_fixture as CPF  # noqa: E402
import modelo_ir as ir  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_validacao as V  # noqa: E402
import observacoes_auto as oa  # noqa: E402

U, db, cv, APP = T.U, T.db, T.cv, T.APP

VERDADE = dict(p0=290.0, k1=0.01, k2=0.03, t1=45.0, t2=8.0)
D0 = date(2025, 1, 6)
AGORA = datetime(2026, 10, 17, 12, 0, 0)


import ir_duracao_legado as _LEG  # noqa: E402


def setUpModule():
    _LEG.iniciar()


def tearDownModule():
    _LEG.parar()


def cenario(n_semanas=64, verdade=None, semente=5, extra=0.6, ruido=0.0, tipo='Bike', prefixo='b'):
    """(curvas, atividades): uma sessao-teste por semana (curva com 1200 s = previsao do IR) e
    sessoes extra (so carga) nos outros dias. As cargas determinam o desempenho via IR (lag 1)."""
    v = verdade or VERDADE
    rng = np.random.default_rng(semente)
    n_dias = n_semanas * 7
    carga = np.where(rng.random(n_dias) < extra, rng.uniform(30, 120, n_dias), 0.0)
    perf = ir.desempenho(carga, v['p0'], v['k1'], v['k2'], v['t1'], v['t2'], 1)
    curvas, ativ = [], []
    for s in range(n_semanas):
        i = s * 7 + 3
        carga[i] = max(carga[i], 60.0)                       # a sessao-teste tambem tem carga
    perf = ir.desempenho(carga, v['p0'], v['k1'], v['k2'], v['t1'], v['t2'], 1)
    for i in range(n_dias):
        dia = (D0 + timedelta(days=i)).isoformat()
        eh_teste = (i % 7 == 3)
        if carga[i] <= 0 and not eh_teste:
            continue
        aid = f'{prefixo}{i}'
        ativ.append({'id': aid, 'type': 'Ride', 'start_date_local': dia + 'T07:00:00',
                     'icu_training_load': float(carga[i]), 'icu_ignore_power': False})
        if eh_teste:
            w = float(perf[i] + rng.normal(0, ruido)) if ruido else float(perf[i])
            curvas.append(cv(aid, tipo, dia, w1200=w))
    return curvas, ativ


class _Base(T.BaseDB):
    CURVAS = []
    ATIV = []

    def setUp(self):
        self.CURVAS, self.ATIV = cenario()
        # P0 global injetado pelo assento (ir_duracao_legado); a regra real esta em test_ir_p0_global.py
        _LEG.definir_p0()
        super().setUp()
        self.ativ = copy.deepcopy(self.ATIV)        # BaseDB.setUp usa atividades sem carga: trocamos

    def rec(self, **kw):
        kw.setdefault('agora', AGORA)
        return mis.recalibrar(origem='teste', **kw)

    def linhas(self, mod='Bike', dur=1200):
        return db.ajustes_ir_historico(mod, dur)

    def prepara(self):
        r = self.atualiza()
        self.assertTrue(r['ok'], r)
        return r


class CargaPorModalidade(unittest.TestCase):
    def test_soma_no_mesmo_dia_e_nao_mistura_modalidades(self):
        a = [{'id': '1', 'type': 'Ride', 'start_date_local': '2026-01-03T07:00:00', 'icu_training_load': 40},
             {'id': '2', 'type': 'VirtualRide', 'start_date_local': '2026-01-03T18:00:00', 'icu_training_load': 60},
             {'id': '3', 'type': 'Rowing', 'start_date_local': '2026-01-03T09:00:00', 'icu_training_load': 55},
             {'id': '4', 'type': 'WeightTraining', 'start_date_local': '2026-01-03T09:00:00', 'icu_training_load': 99}]
        c, info = mis.carga_por_modalidade(a)
        self.assertEqual(c['Bike'], {'2026-01-03': 100.0})
        self.assertEqual(c['Row'], {'2026-01-03': 55.0})
        self.assertEqual(c['Run'], {})
        self.assertNotIn('WeightTraining', c)

    def test_atividade_duplicada_conta_uma_vez_e_sem_carga_e_contada(self):
        a = [{'id': '1', 'type': 'Ride', 'start_date_local': '2026-01-03T07:00:00', 'icu_training_load': 40},
             {'id': '1', 'type': 'Ride', 'start_date_local': '2026-01-03T07:00:00', 'icu_training_load': 40},
             {'id': '2', 'type': 'Ride', 'start_date_local': '2026-01-04T07:00:00', 'icu_training_load': None},
             {'id': '3', 'type': 'Ride', 'start_date_local': '2026-01-05T07:00:00', 'icu_training_load': -5},
             {'id': '4', 'type': 'Ride', 'start_date_local': 'lixo', 'icu_training_load': 10}, 'x', None]
        c, info = mis.carga_por_modalidade(a)
        self.assertEqual(c['Bike'], {'2026-01-03': 40.0})
        self.assertEqual(info['n_atividades_sem_carga']['Bike'], 2)


class RecalibracaoEHistorico(_Base):
    def test_sem_curvas_nao_ha_nada_para_ajustar(self):
        # as observacoes do IR derivam das curvas (power_curves): sem curvas nao ha alvos
        with mock.patch.object(db, 'load_power_curves', return_value=[]):
            r = self.rec()
        self.assertTrue(r['ok'])
        self.assertTrue(all(u['acao'] == 'sem_observacoes' for u in r['unidades'].values()))
        self.assertEqual(self.linhas(), [])

    def test_ajusta_grava_todos_os_campos_e_recupera_parametros(self):
        self.prepara()
        r = self.rec()
        u = r['unidades']['Bike:1200']
        self.assertEqual(u['acao'], 'ajustado')
        self.assertEqual(u['motivo'], mis.MOTIVO_PRIMEIRO)
        (l,) = self.linhas()
        for c in db._COLS_IR:
            self.assertIn(c, l)
        self.assertEqual((l['modalidade'], l['duracao_s'], l['versao_ajuste']), ('Bike', 1200, 1))
        self.assertGreaterEqual(l['n_obs'], 15)
        self.assertIsNotNone(l['data_ini'])
        self.assertIsNotNone(l['data_fim'])
        self.assertEqual(l['versao_modelo'], ir.VERSAO_IR)
        self.assertEqual(l['origem'], 'teste')
        self.assertIsNotNone(l['funcao_objetivo'])
        self.assertTrue(l['convergiu'])
        self.assertEqual(l['configuracao']['ir']['limites']['t1'], [20.0, 120.0])
        self.assertIn(l['estado'], (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL))
        self.assertAlmostEqual(l['t1'], VERDADE['t1'], delta=0.05 * VERDADE['t1'])
        self.assertAlmostEqual(l['t2'], VERDADE['t2'], delta=0.1 * VERDADE['t2'])
        self.assertAlmostEqual(l['p0'], VERDADE['p0'], delta=3.0)
        self.assertEqual(l['metricas']['tipo'], 'ajuste_historico_dentro_da_amostra')
        json.dumps(l)

    def test_unidades_sem_observacoes_nao_geram_linhas(self):
        self.prepara()
        self.rec()
        # P0 = CP e igual para todas as duracoes (60 s inclusive): so geram linhas as unidades COM observacoes
        self.assertEqual({(l['modalidade'], l['duracao_s']) for l in db.ajustes_ir_historico()},
                         {('Bike', 1200)})        # uma unidade por modalidade: so a duracao operacional
        self.assertEqual({l['p0_inicial'] for l in db.ajustes_ir_historico()}, {_LEG.P0_PADRAO})
        self.assertEqual(self.rec()['unidades']['Row:1200']['acao'], 'sem_observacoes')

    def test_sem_novas_observacoes_nao_recalcula_nem_grava(self):
        self.prepara()
        self.rec()
        n = len(db.ajustes_ir_historico())
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=AssertionError('recalculou')):
            r = self.rec()
            r2 = self.rec(agora=AGORA + timedelta(hours=5))          # outra hora, mesmo ano
        for u in r['unidades'].values():
            self.assertIn(u['acao'], ('inalterado', 'sem_observacoes', mis.ACAO_BLOQUEADO))
        self.assertEqual(r2['n_ajustadas'], 0)
        self.assertEqual(len(db.ajustes_ir_historico()), n)

    def test_nova_deteccao_sem_novas_observacoes_nao_recalcula(self):
        self.prepara()
        self.rec()
        n = len(db.ajustes_ir_historico())
        self.atualiza(agora=AGORA + timedelta(days=1))               # deteccao idempotente
        r = self.rec()
        self.assertEqual(r['n_ajustadas'], 0)
        self.assertEqual(len(db.ajustes_ir_historico()), n)

    def test_novas_observacoes_geram_nova_versao_e_preservam_a_anterior(self):
        self.prepara()
        self.rec()
        v1 = copy.deepcopy(self.linhas()[0])
        extra_c, extra_a = cenario(n_semanas=70)
        novas_c = [c for c in extra_c if c['date'] > max(x['date'] for x in self.curvs)]
        self.assertTrue(novas_c)
        self.curvs += novas_c
        self.ativ += [a for a in extra_a if a['start_date_local'][:10] > max(
            x['start_date_local'][:10] for x in self.ATIV)]
        self.sync()
        self.atualiza(agora=AGORA + timedelta(days=2))
        r = self.rec(agora=AGORA + timedelta(days=2))
        self.assertEqual(r['unidades']['Bike:1200']['motivo'], mis.MOTIVO_NOVAS)
        ls = self.linhas()
        self.assertEqual([x['versao_ajuste'] for x in ls], [1, 2])
        self.assertEqual(ls[0], v1)                                  # a versao 1 ficou intacta
        self.assertGreater(ls[1]['n_obs'], ls[0]['n_obs'])
        self.assertNotEqual(ls[1]['assinatura'], ls[0]['assinatura'])
        self.assertGreater(ls[1]['entradas']['carga_ate'], ls[0]['entradas']['carga_ate'])

    def test_carga_depois_do_ultimo_esforco_nao_dispara_recalculo(self):
        self.prepara()
        self.rec()
        n = len(db.ajustes_ir_historico())
        ultimo = max(c['date'] for c in self.curvs)
        d = (date.fromisoformat(ultimo) + timedelta(days=3)).isoformat()
        self.ativ.append({'id': 'novo', 'type': 'Ride', 'start_date_local': d + 'T07:00:00',
                          'icu_training_load': 150.0, 'icu_ignore_power': False})
        r = self.rec()
        self.assertEqual(r['n_ajustadas'], 0)
        self.assertEqual(len(db.ajustes_ir_historico()), n)

    def test_carga_alterada_antes_do_ultimo_esforco_recalcula_com_motivo(self):
        self.prepara()
        self.rec()
        extra = next(a for a in self.ativ if a['id'] not in {c['activity_id'] for c in self.curvs})
        extra['icu_training_load'] += 40.0
        r = self.rec()
        self.assertEqual(r['unidades']['Bike:1200']['motivo'], mis.MOTIVO_CARGA)
        self.assertEqual(len(self.linhas()), 2)

    def test_observacao_excluida_por_elegibilidade_recalcula(self):
        self.prepara()
        self.rec()
        aid = self.curvs[20]['activity_id']
        for a in self.ativ:
            if a['id'] == aid:
                a['icu_ignore_power'] = True
        r = self.rec()
        self.assertEqual(r['unidades']['Bike:1200']['motivo'], mis.MOTIVO_ALTERADAS)
        l1, l2 = self.linhas()
        self.assertEqual(l2['n_obs'], l1['n_obs'] - 1)
        usadas = {o['test_id'] for o in l2['entradas']['observacoes']}
        self.assertFalse(any(aid in t for t in usadas))

    def test_alterar_configuracao_recalcula_e_fica_registado(self):
        self.prepara()
        self.rec()
        r = self.rec(config_ir={'limites': {'t1': [20.0, 100.0]}})
        self.assertEqual(r['unidades']['Bike:1200']['motivo'], mis.MOTIVO_CONFIG)
        self.assertEqual(self.linhas()[-1]['configuracao']['ir']['limites']['t1'], [20.0, 100.0])

    def test_forcar_gera_linha_nova_com_motivo_forcado(self):
        self.prepara()
        self.rec()
        r = self.rec(forcar=True)
        self.assertEqual(r['unidades']['Bike:1200']['motivo'], mis.MOTIVO_FORCADO)
        self.assertEqual(len(self.linhas()), 2)

    def test_historico_so_cresce(self):
        self.prepara()
        self.rec()
        k = lambda l: (l['modalidade'], l['duracao_s'], l['versao_ajuste'])   # noqa: E731
        antes = {k(l): l for l in db.ajustes_ir_historico()}
        self.rec(forcar=True)
        self.rec(forcar=True)
        depois = {k(l): l for l in db.ajustes_ir_historico()}
        for chave, linha in antes.items():
            self.assertEqual(depois[chave], linha)                  # nada foi alterado nem apagado
        self.assertEqual(len(depois), 3 * len(antes))
        with self.assertRaises(db.ConflitoAjusteIR):
            db.inserir_ajuste_ir({c: next(iter(antes.values())).get(c) for c in db._COLS_IR})
        self.assertEqual(len(db.ajustes_ir_historico()), len(depois))

    def test_unidade_com_poucos_dados_fica_registada_como_insuficiente_e_as_outras_ajustam(self):
        row_c = [cv(f'row{i}', 'Row', (D0 + timedelta(days=14 * i)).isoformat(), w1200=300.0 + (i % 3))
                 for i in range(10)]
        row_a = [{'id': f'row{i}', 'type': 'Rowing',
                  'start_date_local': (D0 + timedelta(days=14 * i)).isoformat() + 'T07:00:00',
                  'icu_training_load': 70.0, 'icu_ignore_power': False} for i in range(10)]
        self.curvs += row_c
        self.ativ += row_a
        self.sync()
        self.prepara()
        r = self.rec()
        self.assertEqual(r['unidades']['Bike:1200']['acao'], 'ajustado')
        self.assertEqual(r['unidades']['Row:1200']['estado'], ir.EST_INSUFICIENTE)
        (l,) = db.ajustes_ir_historico('Row', 1200)
        self.assertIsNone(l['t1'])
        self.assertIsNone(l['p0'])
        self.assertIsNone(l['funcao_objetivo'])
        self.assertIn('menos_de_15', l['metricas']['motivo_insuficiente'])
        self.assertEqual(l['n_obs'], 9)                       # a 1a deteccao nao tem referencia anterior
        # a carga do Row nao entrou no Bike
        bike = db.ultimo_ajuste_ir('Bike', 1200)
        self.assertEqual(bike['entradas']['carga_total'] > 0, True)
        self.assertNotIn('row0', json.dumps(bike['entradas']))

    def test_com_ruido_nas_observacoes_ajusta_e_diz_a_verdade_sobre_a_identificabilidade(self):
        c, a = cenario(ruido=1.5, semente=8)
        self.curvs[:] = c
        self.ativ[:] = a
        self.sync()
        self.prepara()
        self.rec()
        l = db.ultimo_ajuste_ir('Bike', 1200)
        self.assertIn(l['estado'], (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL))
        self.assertGreater(l['funcao_objetivo'], 1.0)           # erro dentro da amostra > 0
        self.assertLess(l['r2'], 1.0)
        if l['estado'] == ir.EST_IDENTIFICAVEL:
            self.assertEqual(l['identificabilidade']['avisos'], [])
        else:
            self.assertTrue(l['identificabilidade']['avisos'])
        self.assertIn(mis._leitura(l)[:20], next(i for i in mis.vista()['itens'] if i['duracao_s'] == 1200)['leitura'])

    def test_pocas_observacoes_nao_e_apresentado_como_ajuste(self):
        c, a = cenario(n_semanas=12)
        self.curvs[:] = c
        self.ativ[:] = a
        self.sync()
        self.prepara()
        self.rec()
        (l,) = self.linhas()
        self.assertEqual(l['estado'], ir.EST_INSUFICIENTE)
        self.assertIsNone(l['k1'])
        self.assertIn('leitura', mis.vista()['itens'][0])
        self.assertIn('insuficientes', mis.vista()['itens'][0]['leitura'])


class InicializacaoDeP0NoServico(_Base):
    """O P0 deixou de ser inicializado por MMP do ano anterior / medianas: vem do CP Model e e fixo."""

    def test_p0_vem_do_cp_model_e_nao_do_mmp_do_ano_anterior(self):
        self.prepara()
        self.rec()                                                   # agora = 2026-10-17 -> ano anterior 2025
        l = self.linhas()[0]
        self.assertEqual(l['p0_origem'], 'mmp3_ultimo_yes:Bike:1200:seam-290.0')
        self.assertNotIn(l['p0_origem'], ('ano_anterior', 'primeiras_observacoes', 'mediana_dos_alvos'))
        self.assertEqual(l['p0_inicial'], 290.0)                     # P0 = CP (exato)
        self.assertIsNone(l['metricas']['p0_inicial_detalhe'])

    def test_mudar_a_data_nao_muda_a_origem_do_p0(self):
        self.prepara()
        self.rec(agora=datetime(2031, 3, 1, 12, 0, 0))               # ano anterior (2030) sem dados
        l = self.linhas()[0]
        self.assertEqual(l['p0_origem'], 'mmp3_ultimo_yes:Bike:1200:seam-290.0')
        self.assertEqual(l['p0_inicial'], 290.0)

    def test_p0_do_ajuste_e_exatamente_o_da_referencia_e_nao_foi_estimado(self):
        self.prepara()
        self.rec()
        l = self.linhas()[0]
        self.assertEqual(l['p0'], l['p0_inicial'])                    # P0 fixo: igual a referencia
        self.assertEqual(l['entradas']['p0_ref']['valor_p0'], l['p0'])


class AtualizacaoAutomatica(_Base):
    def test_apos_deteccao_recalibra_e_e_idempotente(self):
        r = self.prepara()
        out = mis.apos_deteccao(r, origem='sync')
        self.assertTrue(out['ok'])
        self.assertGreaterEqual(out['n_ajustadas'], 1)
        out2 = mis.apos_deteccao(r, origem='sync')
        self.assertEqual(out2['n_ajustadas'], 0)

    def test_deteccao_falhada_nao_recalibra(self):
        self.assertEqual(mis.apos_deteccao({'ok': False, 'erro': 'x'})['ignorado'], 'deteccao_sem_sucesso')
        self.assertEqual(mis.apos_deteccao(None)['ignorado'], 'deteccao_sem_sucesso')
        self.assertEqual(self.linhas(), [])

    def test_falha_do_ir_nao_estraga_quem_chama(self):
        with mock.patch.object(mis, 'recalibrar', side_effect=RuntimeError('boom')):
            out = mis.apos_deteccao({'ok': True})
        self.assertFalse(out['ok'])
        self.assertIn('boom', out['erro'])

    def test_atualizar_manual_encadeia_o_ir(self):
        rr = self.c.post('/api/protocolo/observacoes-auto/atualizar')
        self.assertEqual(rr.status_code, 200, rr.get_json())
        j = rr.get_json()
        self.assertIn('modelo_ir', j)
        self.assertTrue(j['modelo_ir']['ok'])
        self.assertGreaterEqual(len(db.ajustes_ir_historico()), 1)

    def test_sincronizacao_chama_a_deteccao_e_depois_o_ir(self):
        import sync
        src = open(os.path.join(T.ROOT, 'sync.py'), encoding='utf-8').read()
        i, j = src.index('observacoes_auto.atualizar'), src.index('modelo_ir_servico.apos_deteccao')
        self.assertLess(i, j)
        self.assertIn("resultado['modelo_ir']", src)
        self.assertTrue(hasattr(sync, 'sync_power_curves'))

    def _sync_real(self):
        import sync
        payload = {'secs': [60, 300, 1200],
                   'curves': [{'id': c['activity_id'], 'start_date_local': c['date'] + 'T07:00:00',
                               'weight': 70, 'watts': c['watts']} for c in copy.deepcopy(self.curvs)
                              if c['type'] == 'Bike']}

        def falso_get(url, params=None, **k):
            return (payload if 'activity-power-curves' in url else []), None
        return sync, mock.patch.object(sync, 'icu_get', side_effect=falso_get), \
            mock.patch.object(sync, 'tipos_reais', return_value={'Bike': ['Ride']}), \
            mock.patch.object(sync, 'athlete_id_real', return_value='0'), \
            mock.patch.object(oa, 'obter_marcos', return_value=(T.MARCOS, 'teste'))

    def test_sync_real_deteta_e_recalibra_o_ir_e_o_segundo_sync_nao_grava(self):
        sync, a, b, c_, d = self._sync_real()
        db._exec('DELETE FROM power_curves')
        with a, b, c_, d:
            r1 = sync.sync_power_curves()
            n1 = len(db.ajustes_ir_historico())
            r2 = sync.sync_power_curves()
        self.assertTrue(r1['ok'] and r1['observacoes_auto']['ok'], r1)
        self.assertTrue(r1['modelo_ir']['ok'], r1['modelo_ir'])
        self.assertGreaterEqual(r1['modelo_ir']['n_ajustadas'], 1)
        self.assertEqual({x['origem'] for x in db.ajustes_ir_historico()}, {'sync'})
        self.assertEqual(r2['modelo_ir']['n_ajustadas'], 0)           # mesmas entradas: nada novo
        self.assertEqual(len(db.ajustes_ir_historico()), n1)

    def test_falha_do_ir_dentro_do_sync_nao_estraga_a_sincronizacao_nem_a_deteccao(self):
        sync, a, b, c_, d = self._sync_real()
        for alvo in ('recalibrar', 'apos_deteccao'):
            with a, b, c_, d, mock.patch.object(mis, alvo, side_effect=RuntimeError('boom-ir')):
                r = sync.sync_power_curves()
            self.assertTrue(r['ok'], alvo)
            self.assertTrue(r['observacoes_auto']['ok'], alvo)       # a deteccao ja tinha corrido
            self.assertFalse(r['modelo_ir']['ok'], alvo)
            self.assertIn('boom-ir', r['modelo_ir']['erro'])

    def test_conflito_de_versao_entre_pedidos_simultaneos_e_ignorado_sem_erro(self):
        self.prepara()
        with mock.patch.object(db, 'inserir_ajuste_ir', side_effect=db.ConflitoAjusteIR('outro pedido')):
            r = self.rec()
        self.assertTrue(r['ok'])
        self.assertEqual(r['n_ajustadas'], 0)
        self.assertEqual(r['unidades']['Bike:1200']['acao'], 'conflito_ignorado')
        self.assertEqual(self.linhas(), [])
        self.assertEqual(self.rec()['unidades']['Bike:1200']['acao'], 'ajustado')   # recupera no pedido seguinte

    def test_otimizacao_falhada_nao_apaga_nem_substitui_o_ultimo_ajuste_valido(self):
        self.prepara()
        self.rec()
        v1 = copy.deepcopy(self.linhas()[0])
        self.assertIn(v1['estado'], (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL))
        extra_c, extra_a = cenario(n_semanas=70)
        self.curvs += [c for c in extra_c if c['date'] > max(x['date'] for x in self.curvs)]
        self.ativ += [a for a in extra_a if a['start_date_local'][:10] > max(
            x['start_date_local'][:10] for x in self.ATIV)]
        self.sync()
        self.atualiza(agora=AGORA + timedelta(days=2))
        falha = {'estado': ir.EST_SEM_CONVERGENCIA, 'parametros': None, 'metricas': {},
                 'obs': {'data_ini': v1['data_ini'], 'data_fim': v1['data_fim']},
                 'convergencia': {'convergiu': False}, 'identificabilidade': None}
        with mock.patch.object(ir, 'ajustar_observacoes', return_value=falha):
            r = self.rec(agora=AGORA + timedelta(days=2))
        self.assertEqual(r['unidades']['Bike:1200']['estado'], ir.EST_SEM_CONVERGENCIA)
        ls = self.linhas()
        self.assertEqual([x['versao_ajuste'] for x in ls], [1, 2])
        self.assertEqual(ls[0], v1)                                  # o ajuste valido ficou intacto
        self.assertIsNone(ls[1]['k1'])
        j = self.c.get('/api/modelo/ir/influencia?tipo=Bike&duracao=1200').get_json()
        # o ultimo ajuste VALIDO fica preservado e e apresentado como DESATUALIZADO, nunca como o ajuste novo
        self.assertTrue(j['ajuste_apresentado_e_antigo'])
        self.assertEqual(j['versao_ajuste'], 1)
        self.assertEqual(j['parametros']['k1'], v1['k1'])
        self.assertEqual(j['tentativa_mais_recente']['versao_ajuste'], 2)
        self.assertEqual(j['tentativa_mais_recente']['estado'], ir.EST_SEM_CONVERGENCIA)
        self.assertTrue(j['p0_desatualizado'])
        self.assertIsNotNone(j['curvas'])
        # sem novas entradas a falha nao e repetida em cada sync; forcar tenta de novo
        self.assertEqual(self.rec(agora=AGORA + timedelta(days=3))['unidades']['Bike:1200']['acao'], 'inalterado')
        r3 = self.rec(agora=AGORA + timedelta(days=3), forcar=True)['unidades']['Bike:1200']
        self.assertEqual((r3['acao'], r3['versao_ajuste']), ('ajustado', 3))
        self.assertIn(r3['estado'], (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL))
        self.assertEqual(self.linhas()[0], v1)

    def test_recalibrar_nao_chama_a_api_externa(self):
        self.prepara()
        import requests
        with mock.patch.object(requests, 'get', side_effect=AssertionError('rede')), \
                mock.patch.object(requests, 'post', side_effect=AssertionError('rede')):
            self.rec()
            mis.vista()


class Endpoints(_Base):
    def test_get_ir_sem_ajustes(self):
        r = self.c.get('/api/modelo/ir')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j['itens'], [])
        self.assertTrue(j['experimental'])
        self.assertIn('Ajuste HISTORICO', j['aviso'])

    def test_post_recalibrar_e_get(self):
        self.prepara()
        r = self.c.post('/api/modelo/ir/recalibrar', json={})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertGreaterEqual(r.get_json()['n_ajustadas'], 1)
        r2 = self.c.post('/api/modelo/ir/recalibrar', json={})
        self.assertEqual(r2.get_json()['n_ajustadas'], 0)
        j = self.c.get('/api/modelo/ir?tipo=Bike&duracao=1200&historico=1').get_json()
        (it,) = j['itens']
        self.assertEqual(it['n_ajustes'], 1)
        self.assertIn('leitura', it)
        self.assertEqual(len(j['historico']), 1)
        self.assertNotIn('entradas', j['historico'][0])
        self.assertEqual(self.c.get('/api/modelo/ir?tipo=Foo').status_code, 400)

    def test_validacao_exige_tipo_e_modo_valido(self):
        self.assertEqual(self.c.get('/api/modelo/ir/validacao').status_code, 400)
        self.assertEqual(self.c.get('/api/modelo/ir/validacao?tipo=Bike&modo=x').status_code, 400)

    def test_endpoint_de_validacao_devolve_previsoes_separadas_do_ajuste(self):
        self.prepara()
        r = self.c.get('/api/modelo/ir/validacao?tipo=Bike&duracao=1200&modo=retrospectivo')
        self.assertEqual(r.status_code, 200, r.get_json())
        j = r.get_json()
        self.assertEqual(j['tipo_analise'], 'validacao_preditiva_ir_walk_forward')
        self.assertFalse(j['promocao'])
        (it,) = j['itens']
        self.assertIn('nao e ajuste historico', it['tipo'])
        self.assertEqual(db.ajustes_ir_historico(), [])                 # avaliar nunca grava


class CurvasDeInfluencia(_Base):
    """Fase 'curvas de influencia': so le o ultimo ajuste guardado; nunca recalibra."""
    URL = '/api/modelo/ir/influencia?tipo=Bike&duracao=1200'

    def pronto(self):
        self.prepara()
        self.rec()
        return self.c.get(self.URL).get_json()

    def cargas_bike(self):
        c, _ = mis.carga_por_modalidade(self.ativ)
        return c['Bike']

    def test_sem_ajuste_nao_calcula_nem_grava_nada(self):
        with mock.patch.object(mis, 'recalibrar', side_effect=AssertionError('nao deve recalibrar')), \
                mock.patch.object(ir, 'ajustar', side_effect=AssertionError('nao deve ajustar')):
            r = self.c.get(self.URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['estado'], 'sem_ajuste')
        self.assertEqual(db.ajustes_ir_historico(), [])

    def test_parametros_vem_do_ultimo_ajuste_guardado_e_a_curva_e_a_equacao_do_modelo(self):
        j = self.pronto()
        (l,) = self.linhas()
        self.assertEqual(j['status'], 'OK')
        self.assertEqual(j['parametros'], {k: l[k] for k in ir.NOMES})
        self.assertEqual(j['versao_ajuste'], l['versao_ajuste'])
        for k in ('n_obs', 'data_ini', 'data_fim', 'funcao_objetivo', 'rmse', 'convergiu', 'estado'):
            self.assertEqual(j[k], l[k], k)
        cu = j['curvas']
        d0 = cu['datas'][0]
        cargas = ir.cargas_diarias(self.cargas_bike(), d0, cu['datas'][-1])
        esperado = ir.desempenho(cargas, **{k: l[k] for k in ir.NOMES}, lag_dias=1)
        np.testing.assert_allclose(cu['liquida'], esperado, atol=6e-4)
        e, g = ir.componentes(cargas, l['t1'], l['t2'], 1)
        np.testing.assert_allclose(cu['positiva'], l['k1'] * e, atol=6e-4)
        np.testing.assert_allclose(cu['negativa'], l['k2'] * g, atol=6e-4)
        np.testing.assert_allclose(np.array(cu['liquida']),
                                   l['p0'] + np.array(cu['positiva']) - np.array(cu['negativa']), atol=2e-3)
        self.assertTrue(all(v >= 0 for v in cu['positiva'] + cu['negativa']))
        json.dumps(j)

    def test_a_curva_passa_pelas_observacoes_usadas_no_ajuste(self):
        j = self.pronto()
        ix = {d: i for i, d in enumerate(j['curvas']['datas'])}
        self.assertEqual(len(j['observacoes']), j['n_obs'])
        for o in j['observacoes']:
            self.assertAlmostEqual(j['curvas']['liquida'][ix[o['data']]], o['watts'], delta=1.5)

    def test_soma_das_contribuicoes_individuais_e_a_resposta_agregada(self):
        j = self.pronto()
        v = j['verificacao_aditividade']
        self.assertGreater(v['n_atividades'], 100)
        for k in ('max_abs_dif_positiva', 'max_abs_dif_negativa', 'max_abs_dif_liquida'):
            self.assertLess(v[k], 1e-9, k)

    def test_soma_independente_das_contribuicoes_reproduz_a_curva_agregada_do_payload(self):
        with mock.patch.object(mis, 'MAX_ATIVIDADES_LISTA', 100000):
            j = self.pronto()
        P, lag, cu = j['parametros'], j['lag_dias'], j['curvas']
        n = len(cu['datas'])
        soma = {k: np.zeros(n) for k in ('positiva', 'negativa', 'liquida')}
        for a in j['atividades']:
            x = ir.contribuicao_atividade(n, cu['datas'].index(a['data']), a['carga'], P, lag)
            for k in soma:
                soma[k] += x[k]
        self.assertGreater(len(j['atividades']), 300)                      # a lista completa, nao so as recentes
        np.testing.assert_allclose(soma['positiva'], cu['positiva'], atol=1e-3)
        np.testing.assert_allclose(soma['negativa'], cu['negativa'], atol=1e-3)
        np.testing.assert_allclose(P['p0'] + soma['liquida'], cu['liquida'], atol=2e-3)
        self.assertAlmostEqual(sum(a['carga'] for a in j['atividades']), sum(cu['carga_dia']), delta=0.5)

    def test_contribuicao_de_uma_atividade_e_a_forma_fechada_e_nao_a_resposta_total(self):
        j = self.pronto()
        P, lag = j['parametros'], j['lag_dias']
        a = j['atividades'][len(j['atividades']) // 2]
        r = self.c.get(self.URL + '&atividade=' + a['id']).get_json()
        c = r['contribuicao_atividade']
        self.assertEqual(c['atividade']['id'], a['id'])
        datas = r['curvas']['datas']
        i0 = datas.index(a['data'])
        self.assertEqual(c['inicio_do_efeito'], datas[i0 + lag])
        for t in range(len(datas)):
            if t < i0 + lag:
                self.assertEqual((c['positiva'][t], c['negativa'][t], c['liquida'][t]), (0, 0, 0))
            else:
                u = t - lag - i0
                self.assertAlmostEqual(c['positiva'][t], a['carga'] * P['k1'] * np.exp(-u / P['t1']), delta=6e-4)
                self.assertAlmostEqual(c['negativa'][t], a['carga'] * P['k2'] * np.exp(-u / P['t2']), delta=6e-4)
        # uma atividade nao e a resposta agregada
        self.assertLess(max(c['positiva']), max(r['curvas']['positiva']))
        # sem atividade escolhida nao ha contribuicao
        self.assertIsNone(j['contribuicao_atividade'])

    def test_a_carga_do_proprio_dia_nao_entra_no_proprio_dia(self):
        j = self.pronto()
        self.assertEqual(j['lag_dias'], 1)
        a = j['atividades'][0]
        c = self.c.get(self.URL + '&atividade=' + a['id']).get_json()['contribuicao_atividade']
        i0 = j['curvas']['datas'].index(a['data'])
        self.assertEqual(c['liquida'][i0], 0.0)
        self.assertIn('nao explica o proprio valor', j['tratamento_da_carga']['proprio_dia'])
        self.assertIn('carga 0', j['tratamento_da_carga']['dias_sem_treino'])

    def test_o_aviso_diz_que_a_escala_nao_e_comparavel_entre_modalidades(self):
        self.prepara()
        self.rec()
        a = self.c.get(self.URL).get_json()['aviso']
        self.assertIn('nao sao comparaveis em valor absoluto entre modalidades', a)
        self.assertIn('sem influencia cruzada', a)
        self.assertIn('Nao e previsao validada', a)

    def test_dias_sem_treino_so_decaem(self):
        j = self.pronto()
        cu = j['curvas']
        P = j['parametros']
        for i in range(1, len(cu['datas'])):
            if i >= 2 and cu['carga_dia'][i - 1] == 0:
                self.assertLessEqual(cu['positiva'][i], cu['positiva'][i - 1] + 1e-6)
                self.assertLessEqual(cu['negativa'][i], cu['negativa'][i - 1] + 1e-6)

    def test_atividade_inexistente_ou_de_outra_modalidade_da_404(self):
        self.pronto()
        r = self.c.get(self.URL + '&atividade=nao-existe')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()['erro'], 'atividade_nao_encontrada')

    def test_validacao_dos_parametros_do_pedido(self):
        self.assertEqual(self.c.get('/api/modelo/ir/influencia').status_code, 400)
        self.assertEqual(self.c.get('/api/modelo/ir/influencia?tipo=Bike').status_code, 200)   # duracao opcional
        self.assertEqual(self.c.get('/api/modelo/ir/influencia?tipo=Foo&duracao=300').status_code, 400)
        self.assertEqual(self.c.get('/api/modelo/ir/influencia?tipo=Bike&duracao=999').status_code, 400)
        self.assertEqual(self.c.get(self.URL + '&ate=ontem').status_code, 400)

    def test_cada_modalidade_e_duracao_usa_o_seu_ajuste_e_a_sua_carga(self):
        self.prepara()
        self.rec()
        a = self.c.get('/api/modelo/ir/influencia?tipo=Bike&duracao=300').get_json()
        b = self.c.get(self.URL).get_json()
        for k in ir.NOMES:
            self.assertEqual(b['parametros'][k], db.ultimo_ajuste_ir('Bike', 1200)[k])
        self.assertEqual(b['duracao_s'], 1200)
        # as duracoes antigas nao sao operacionais: nao ha ajuste nelas e nada e herdado do operacional
        self.assertEqual(a['estado'], 'sem_ajuste')
        # uma modalidade sem ajuste nao herda a de outra
        self.assertEqual(self.c.get('/api/modelo/ir/influencia?tipo=Row').get_json()['estado'], 'sem_ajuste')

    def test_carga_de_outra_modalidade_nao_entra_na_curva(self):
        antes = self.pronto()
        self.ativ += [{'id': f'row{i}', 'type': 'Rowing',
                       'start_date_local': (D0 + timedelta(days=5 * i)).isoformat() + 'T07:00:00',
                       'icu_training_load': 500.0, 'icu_ignore_power': False} for i in range(40)]
        depois = self.c.get(self.URL).get_json()
        self.assertEqual(depois['curvas']['liquida'], antes['curvas']['liquida'])
        self.assertEqual([x['id'] for x in depois['atividades']], [x['id'] for x in antes['atividades']])

    def test_ler_a_curva_nao_recalibra_nem_grava(self):
        self.pronto()
        n = len(db.ajustes_ir_historico())
        with mock.patch.object(mis, 'recalibrar', side_effect=AssertionError('nao deve recalibrar')), \
                mock.patch.object(ir, 'ajustar', side_effect=AssertionError('nao deve ajustar')), \
                mock.patch.object(ir, 'ajustar_observacoes', side_effect=AssertionError('nao deve ajustar')):
            for _ in range(3):
                self.assertEqual(self.c.get(self.URL).status_code, 200)
        self.assertEqual(len(db.ajustes_ir_historico()), n)

    def test_carga_alterada_marca_ajuste_desatualizado_sem_recalibrar(self):
        j = self.pronto()
        self.assertFalse(j['ajuste_desatualizado'])
        for a in self.ativ[: len(self.ativ) // 2]:
            a['icu_training_load'] = a['icu_training_load'] + 25
        j2 = self.c.get(self.URL).get_json()
        self.assertTrue(j2['ajuste_desatualizado'])
        self.assertEqual(j2['versao_ajuste'], j['versao_ajuste'])       # continua a mostrar o ajuste guardado
        self.assertEqual(len(self.linhas()), 1)                          # e nao recalibrou para o desenhar

    def test_parametros_nao_identificaveis_vem_marcados_e_sem_fisiologia(self):
        self.prepara()
        self.rec()
        real = db.ultimo_ajuste_ir('Bike', 1200)
        falso = {**real, 'estado': ir.EST_NAO_IDENTIFICAVEL,
                 'identificabilidade': {'avisos': ['parametro_no_limite:t1']}}
        # so existe este ajuste (sem calibracao identificavel): e' desenhado, marcado como NAO valido
        with mock.patch.object(db, 'ultimo_ajuste_ir', return_value=falso), \
                mock.patch.object(db, 'ajustes_ir_historico', return_value=[falso]):
            j = self.c.get(self.URL).get_json()
        self.assertTrue(j['sem_calibracao_valida'])
        self.assertFalse(j['parametros_identificados'])
        self.assertIn('NAO identificaveis', j['aviso_parametros'])
        self.assertIn('nao fisiologia confirmada', j['aviso_parametros'])
        self.assertEqual(j['avisos_identificabilidade'], ['parametro_no_limite:t1'])
        self.assertIn('NAO identificaveis', j['leitura'])

    def test_ajuste_identificavel_nao_traz_o_aviso_de_nao_identificavel(self):
        j = self.pronto()
        if j['estado'] == ir.EST_IDENTIFICAVEL:
            self.assertTrue(j['parametros_identificados'])
            self.assertIsNone(j['aviso_parametros'])
        self.assertIn('Nao e previsao validada', j['aviso'])

    def test_dados_insuficientes_nao_desenham_curva(self):
        self.prepara()
        self.rec()
        real = db.ultimo_ajuste_ir('Bike', 1200)
        falso = {**real, 'estado': ir.EST_INSUFICIENTE, 'p0': None, 'k1': None, 'k2': None,
                 't1': None, 't2': None, 'versao_ajuste': real['versao_ajuste'] + 1}
        with mock.patch.object(db, 'ultimo_ajuste_ir', return_value=falso):
            j = self.c.get(self.URL).get_json()
        # ha um ajuste valido anterior: apresenta-se como antigo/desatualizado, com a tentativa mais recente a vista
        self.assertTrue(j['ajuste_apresentado_e_antigo'])
        self.assertEqual(j['versao_ajuste'], real['versao_ajuste'])
        self.assertEqual(j['tentativa_mais_recente']['estado'], ir.EST_INSUFICIENTE)
        # sem NENHUM ajuste valido na historia: sem parametros e sem curva
        with mock.patch.object(db, 'ultimo_ajuste_ir', return_value=falso), \
                mock.patch.object(db, 'ajustes_ir_historico', return_value=[falso]):
            j = self.c.get(self.URL).get_json()
        self.assertIsNone(j['parametros'])
        self.assertIsNone(j['curvas'])

    def test_sem_convergencia_nao_desenha_curva(self):
        self.prepara()
        self.rec()
        real = db.ultimo_ajuste_ir('Bike', 1200)
        sc = {**real, 'estado': ir.EST_SEM_CONVERGENCIA, 'versao_ajuste': real['versao_ajuste'] + 1}
        with mock.patch.object(db, 'ultimo_ajuste_ir', return_value=sc), \
                mock.patch.object(db, 'ajustes_ir_historico', return_value=[sc]):
            j = self.c.get(self.URL).get_json()
        self.assertIsNone(j['curvas'])                      # sem ajuste valido nao se desenha curva

    def test_historico_de_ajustes_fica_preservado_ao_ler(self):
        self.prepara()
        self.rec()
        antes = [dict(r) for r in self.linhas()]
        self.c.get(self.URL)
        self.assertEqual(self.linhas(), antes)

    def test_sem_fuga_temporal_a_curva_num_dia_nao_depende_da_carga_desse_dia_nem_do_futuro(self):
        j = self.pronto()
        datas = j['curvas']['datas']
        corte = datas[len(datas) // 2]
        for a in self.ativ:
            if a['start_date_local'][:10] >= corte:
                a['icu_training_load'] = a['icu_training_load'] * 4 + 50
        j2 = self.c.get(self.URL).get_json()
        i = datas.index(corte)
        for k in ('positiva', 'negativa', 'liquida'):
            self.assertEqual(j2['curvas'][k][: i + 1], j['curvas'][k][: i + 1], k)     # inclui o proprio dia do corte
        self.assertNotEqual(j2['curvas']['liquida'][i + 1], j['curvas']['liquida'][i + 1])

    def test_dias_depois_do_ajuste_ficam_marcados(self):
        j = self.pronto()
        f = j['curvas']['fora_do_ajuste_desde']
        if f is not None:
            self.assertGreater(f, j['data_fim'])
        longo = self.c.get(self.URL + '&ate=2099-01-01')
        self.assertEqual(longo.status_code, 200)
        cu = longo.get_json()['curvas']
        self.assertGreater(cu['datas'][-1], j['data_fim'])
        self.assertEqual(cu['fora_do_ajuste_desde'], next(d for d in cu['datas'] if d > j['data_fim']))


TIPO_ATIV = {'Bike': 'Ride', 'Row': 'Rowing', 'Run': 'Running'}
P0_MOD = {'Bike': 300.0, 'Row': 260.0, 'Run': 330.0}
OFF_MOD = {'Bike': 3, 'Row': 4, 'Run': 5}


def cenario_global(lam=1.0, het=(), semente=7, n_semanas=64, ruido=0.4):
    """(curvas, atividades) de Bike/Row/Run: um teste por semana e por modalidade, carga em todos os
    dias de treino. Verdade: y = P0_u*(1 + K1*E - K2*G) com carga efetiva = propria + lam*outras."""
    rng = np.random.default_rng(semente)
    n = n_semanas * 7
    carga = {m: np.where(rng.random(n) < p, rng.uniform(30, 120, n), 0.0)
             for m, p in (('Bike', .5), ('Row', .35), ('Run', .35))}
    for m in carga:
        for s in range(n_semanas):
            i = s * 7 + OFF_MOD[m]
            carga[m][i] = max(carga[m][i], 60.0)
    tot = sum(carga.values())
    curvas, ativ = [], []
    for m in carga:
        pr = dict(k1=1.2e-5, k2=2.4e-4, t1=95., t2=3.) if m in het else dict(k1=4e-5, k2=1.2e-4, t1=45., t2=8.)
        perf = 1 + ir.desempenho(carga[m] + lam * (tot - carga[m]), 0.0, pr['k1'], pr['k2'], pr['t1'], pr['t2'], 1)
        for i in range(n):
            if carga[m][i] <= 0:
                continue
            dia = (D0 + timedelta(days=i)).isoformat()
            aid = f'{m}-{i}'                                  # ids unicos entre modalidades (como na Intervals)
            ativ.append({'id': aid, 'type': TIPO_ATIV[m], 'start_date_local': dia + 'T07:00:00',
                         'icu_training_load': float(carga[m][i]), 'icu_ignore_power': False})
            if i % 7 == OFF_MOD[m]:
                w = float(P0_MOD[m] * (perf[i] + rng.normal(0, ruido / 100)))
                curvas.append(cv(aid, m, dia, w1200=w, w300=w * 1.3, w60=w * 1.8))
    return curvas, ativ


class AnaliseGlobal(_Base):
    """Global de referencia: analise separada, so leitura, uma duracao por grupo."""
    URL = '/api/modelo/ir/global?duracao=1200&rapido=1'

    def com(self, **kw):
        c, a = cenario_global(**kw)
        self.curvs[:] = c
        self.ativ[:] = a
        self.sync()
        self.prepara()

    def global_(self, url=None):
        r = self.c.get(url or self.URL)
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    def test_validacao_dos_parametros_do_pedido(self):
        self.assertEqual(self.c.get('/api/modelo/ir/global').status_code, 400)
        self.assertEqual(self.c.get('/api/modelo/ir/global?duracao=999').status_code, 400)
        r = self.c.get('/api/modelo/ir/global?duracao=300&tipo=Bike')
        self.assertEqual(r.status_code, 400)
        self.assertIn('nao aceita tipo', json.dumps(r.get_json()))

    def test_com_uma_so_modalidade_nao_ha_global(self):
        self.prepara()
        j = self.global_()
        self.assertEqual(j['estado'], 'global_dados_insuficientes')
        self.assertIn('menos_de_2_unidades', j['motivo'])
        self.assertIsNone(j['parametros'])
        self.assertEqual([u['id'] for u in j['grupo']['unidades']], ['Bike:1200'])

    def test_dinamica_partilhada_e_transferencia_total_sao_recuperadas(self):
        self.com(lam=1.0)
        j = self.global_()
        self.assertEqual(j['estado'], 'global_ajustado_identificavel', j['identificabilidade'])
        p = j['parametros']
        self.assertGreater(p['lambda'], 0.8)
        self.assertAlmostEqual(p['t1'], 45.0, delta=0.2 * 45)
        self.assertAlmostEqual(p['t2'], 8.0, delta=0.25 * 8)
        self.assertEqual({u['id'] for u in j['grupo']['unidades']}, {'Bike:1200', 'Row:1200', 'Run:1200'})
        self.assertEqual(j['grupo']['modalidades_sem_observacoes'], ['Ski'])
        self.assertIn('ha transferencia', j['transferencia']['leitura'])
        self.assertEqual(j['validade_preditiva'], 'nao_avaliada')
        self.assertIn('nao e previsao validada', j['aviso'])
        json.dumps(j)

    def test_dinamica_nao_partilhada_nao_e_apresentada_como_global(self):
        self.com(lam=1.0, het=('Row', 'Run'))
        j = self.global_()
        self.assertEqual(j['estado'], 'global_heterogeneo')
        self.assertIn('REJEITA', j['leitura'])
        self.assertIn('nao interpretar', j['leitura'])

    def test_so_usa_a_duracao_pedida_e_as_mesmas_observacoes_do_modelo_por_modalidade(self):
        self.com(lam=1.0)
        self.rec()
        j = self.global_()
        for u in j['grupo']['unidades']:
            m = u['id'].split(':')[0]
            self.assertEqual(u['n_obs'], db.ultimo_ajuste_ir(m, 1200)['n_obs'], m)
        j300 = self.global_('/api/modelo/ir/global?duracao=300&rapido=1')
        self.assertEqual(j300['grupo']['duracao_s'], 300)
        self.assertTrue(all(u['id'].endswith(':300') for u in j300['grupo']['unidades']))
        self.assertNotEqual(j300['parametros'], j['parametros'])

    def test_e_so_leitura_nao_grava_nem_recalibra_nem_altera_os_ajustes_por_modalidade(self):
        self.com(lam=1.0)
        self.rec()
        antes = [dict(r) for r in db.ajustes_ir_historico()]
        with mock.patch.object(mis, 'recalibrar', side_effect=AssertionError('nao deve recalibrar')), \
                mock.patch.object(db, 'inserir_ajuste_ir', side_effect=AssertionError('nao deve gravar')):
            self.global_()
            self.global_()
        self.assertEqual(db.ajustes_ir_historico(), antes)

    def test_traz_os_ajustes_por_modalidade_apenas_para_comparar(self):
        self.com(lam=1.0)
        self.rec()
        j = self.global_()
        pm = j['modelos_por_modalidade_para_comparar']
        self.assertEqual(set(pm), {'Bike', 'Row', 'Run'})
        for m, v in pm.items():
            ult = db.ultimo_ajuste_ir(m, 1200)
            self.assertEqual(v['parametros'], {k: ult[k] for k in ir.NOMES})
        self.assertIn('so informativo', j['nota_comparacao'])

    def test_sem_fuga_temporal_carga_posterior_as_observacoes_nao_entra(self):
        self.com(lam=1.0)
        a = self.global_()
        ultimo = max(o['data'] for o in self.obs_usadas())
        for i in range(1, 60):
            dia = (date.fromisoformat(ultimo) + timedelta(days=i)).isoformat()
            self.ativ.append({'id': f'futuro-{i}', 'type': 'Ride', 'start_date_local': dia + 'T07:00:00',
                              'icu_training_load': 400.0, 'icu_ignore_power': False})
        b = self.global_()
        self.assertEqual(a['parametros'], b['parametros'])

    def obs_usadas(self):
        sel = mis.selecionar(db.observacoes_auto_historico(), db.revisoes_testes_modelo(),
                             db.load_activities(), db.load_power_curves() or [])
        return [o for (m, d), v in sel.items() if d == 1200 for o in mis._obs_min(v['usadas'])]


class GuardasDeIsolamentoIR(_Base):
    """Auditoria dos testes de guarda: a tabela nova e a unica autorizada, o IR so escreve por
    um caminho, as rotas de leitura nao escrevem e nenhum modulo de calculo de producao o conhece."""
    ROTAS_SO_LEITURA = ('api_modelo_ir', 'api_modelo_ir_influencia', 'api_modelo_ir_global',
                        'api_modelo_ir_validacao')

    def _fonte(self, f):
        return open(os.path.join(T.ROOT, f), encoding='utf-8').read()

    def _tabelas_modelo(self):
        return {r[0] for r in db._exec("SELECT name FROM sqlite_master WHERE type='table'", fetch='all')
                if r[0].startswith('modelo')}

    def test_so_a_tabela_prevista_foi_autorizada_e_uma_tabela_inesperada_seria_detetada(self):
        esperado = {'modelo_ir_ajustes', 'modelo_ir_p0_referencias', 'modelo_mmp3_yes_historico', 'modelo_mmp3_yes_revisoes',
                    'modelo_observacoes_auto', 'modelo_testes_revisoes'}      # + historico de YES (append-only)
        self.assertEqual(self._tabelas_modelo(), esperado)
        db._exec('CREATE TABLE modelo_intrusa (x INTEGER)')
        try:
            self.assertNotEqual(self._tabelas_modelo(), esperado)         # a comparacao dos guardas falharia
        finally:
            db._exec('DROP TABLE modelo_intrusa')
        self.assertEqual(self._tabelas_modelo(), esperado)

    def test_modelo_ir_ajustes_e_so_insert_e_so_a_base_de_dados_escreve(self):
        src = self._fonte('db.py')
        self.assertIsNone(re.search(r'(UPDATE|DELETE\s+FROM)\s+modelo_ir_ajustes', src, re.I))
        self.assertIsNone(re.search(r'DROP\s+TABLE[^\n]*modelo_ir_ajustes', src, re.I))
        self.assertEqual(len(re.findall(r'INSERT\s+INTO\s+modelo_ir_ajustes', src, re.I)), 1)
        for f in os.listdir(T.ROOT):
            if f.endswith('.py') and f != 'db.py':
                self.assertNotRegex(self._fonte(f), r'(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+modelo_ir_ajustes', f)

    def test_recriar_power_curves_e_re_sync_nao_tocam_no_historico_do_ir(self):
        self.prepara()
        self.rec()
        antes = db.ajustes_ir_historico()
        self.assertTrue(antes)
        db.recriar_power_curves()
        self.assertEqual(db.ajustes_ir_historico(), antes)

    def test_rotas_de_leitura_do_ir_nao_escrevem_nem_recalibram_no_codigo(self):
        src = self._fonte('app.py')
        for nome in self.ROTAS_SO_LEITURA:
            i = src.index(f'def {nome}(')
            j = src.find('@app.route(', i)
            corpo = src[i:j if j > 0 else len(src)]
            self.assertNotRegex(corpo, r'inserir_ajuste_ir|recalibrar|apos_deteccao|_exec\(|INSERT|UPDATE|DELETE', nome)
        # a unica rota que recalibra e o POST explicito
        i = src.index('def api_modelo_ir_recalibrar(')
        self.assertIn("methods=['POST']", src[src.rindex('@app.route(', 0, i):i])

    def test_pedidos_de_leitura_do_ir_nao_alteram_nenhuma_tabela(self):
        self.prepara()
        self.rec()
        db._exec('SELECT 1', fetch='one')
        antes = db._conn.total_changes
        for u in ('/api/modelo/ir', '/api/modelo/ir?historico=1', '/api/modelo/ir/influencia?tipo=Bike&duracao=1200',
                  '/api/modelo/ir/influencia?tipo=Row&duracao=300', '/api/modelo/ir/global?duracao=1200&rapido=1',
                  '/api/modelo/ir/validacao?tipo=Bike&duracao=1200&modo=retrospectivo'):
            self.c.get(u)
        self.assertEqual(db._conn.total_changes, antes)

    def test_nenhum_modulo_de_calculo_de_producao_conhece_o_ir(self):
        for f in ('pmc.py', 'calibracao.py', 'ftlm.py', 'fmt.py', 'protocolo.py', 'export.py',
                  'api_recovery.py', 'modelo_testes.py', 'helpers.py', 'modelo_estimador.py', 'modelo_validacao.py'):
            src = self._fonte(f)
            self.assertNotRegex(src, r'modelo_ir', f)
        # sync.py so conhece o gancho (importado dentro da funcao e com falha isolada)
        sy = self._fonte('sync.py')
        self.assertEqual(sorted(set(re.findall(r'modelo_ir\w*', sy))), ['modelo_ir', 'modelo_ir_servico'])
        self.assertNotIn('modelo_ir_global', sy)
        self.assertNotRegex(self._fonte('modelo_ir.py'), r'^\s*(import|from)\s+(db|app|sync|requests)\b', )
        self.assertNotRegex(self._fonte('modelo_ir_global.py'), r'^\s*(import|from)\s+(db|app|sync|requests)\b')


class AvaliacaoPreditivaSemFuga(_Base):
    def val(self, modos=('retrospectivo',), **kw):
        kw.setdefault('referencias_p0', [CPF.referencia_p0('Bike', 1200)])      # conhecida desde 2020
        return mis.validar_preditivo(db.observacoes_auto_historico(), db.revisoes_testes_modelo(),
                                     self.ativ, db.load_power_curves() or [], modos=modos,
                                     tipo='Bike', duracao=1200, **kw)

    def test_estrutura_e_comparacao_nos_mesmos_alvos(self):
        self.prepara()
        (it,) = self.val()['itens']
        n = it['n_alvos_com_previsao_ir']
        self.assertGreater(n, 0)
        for k in ('M0', 'M1', 'M2', 'IR'):
            self.assertEqual(it['metricas'][k]['n'], n)
        self.assertEqual(len(it['previsoes']), n)
        self.assertGreater(it['n_alvos'], n)                           # os primeiros alvos nao tem IR (poucos dados)
        self.assertTrue(any(k.startswith('menos_de') or k in ('sem_historico', 'dados_insuficientes')
                            for k in it['alvos_sem_previsao_ir']))
        for p in it['previsoes']:
            self.assertGreaterEqual(p['n_treino'], 15)

    def test_previsoes_anteriores_nao_mudam_se_o_futuro_for_apagado_ou_alterado(self):
        self.prepara()
        completo = {p['test_id']: p for p in self.val()['itens'][0]['previsoes']}
        corte = sorted(completo.values(), key=lambda p: p['data'])[len(completo) // 2]['data']
        # 1) futuro apagado (atividades e curvas depois do corte nem existem)
        self.ativ = [a for a in self.ativ if a['start_date_local'][:10] <= corte]
        antes = [c for c in self.curvs if c['date'] <= corte]
        self.curvs[:] = antes
        self.sync()
        with mock.patch.object(db, 'load_activities', side_effect=lambda **k: self.ativ):
            truncado = {p['test_id']: p for p in self.val()['itens'][0]['previsoes']}
        comuns = [t for t in truncado if t in completo and completo[t]['data'] < corte]
        self.assertTrue(comuns)
        for t in comuns:
            self.assertEqual(truncado[t]['ir'], completo[t]['ir'], t)
            self.assertEqual(truncado[t]['n_treino'], completo[t]['n_treino'])

    def test_carga_futura_alterada_nao_muda_previsoes_passadas(self):
        self.prepara()
        antes = {p['test_id']: p for p in self.val()['itens'][0]['previsoes']}
        corte = sorted(p['data'] for p in antes.values())[len(antes) // 2]
        for a in self.ativ:
            if a['start_date_local'][:10] >= corte:
                a['icu_training_load'] = a['icu_training_load'] * 3 + 100
        depois = {p['test_id']: p for p in self.val()['itens'][0]['previsoes']}
        passados = [t for t, p in antes.items() if p['data'] < corte]
        self.assertTrue(passados)
        for t in passados:
            self.assertEqual(depois[t]['ir'], antes[t]['ir'], t)
        # e a partir do corte a previsao muda (o teste nao e vazio)
        self.assertTrue(any(depois[t]['ir'] != antes[t]['ir'] for t, p in antes.items()
                            if p['data'] > corte and t in depois))

    def test_mudar_o_watts_do_alvo_so_muda_o_erro_nao_a_previsao(self):
        self.prepara()
        base = {p['test_id']: p for p in self.val()['itens'][0]['previsoes']}
        alvo = sorted(base.values(), key=lambda p: p['data'])[len(base) // 2]
        aid = alvo['test_id'].split(':')[-1]
        self.muda(aid, 300.0 if alvo['y'] < 299 else 280.0)
        self.atualiza(agora=AGORA + timedelta(days=1))
        alt = {p['test_id']: p for p in self.val()['itens'][0]['previsoes']}
        if alvo['test_id'] in alt:
            self.assertEqual(alt[alvo['test_id']]['ir'], alvo['ir'])
            self.assertNotEqual(alt[alvo['test_id']]['y'], alvo['y'])

    def test_conhecido_na_epoca_sem_historico_temporal_nao_preve(self):
        self.prepara()                                                  # tudo detetado AGORA (2026-10-17)
        it = self.val(modos=('conhecido_na_epoca',))['itens'][0]
        self.assertEqual(it['n_alvos_com_previsao_ir'], 0)
        self.assertIsNone(it['veredicto'])
        self.assertEqual(it['estado_suficiencia'], V.EST_SEM_ALVOS)
        self.assertEqual(it['motivo_sem_veredicto'], 'sem_alvos_com_previsao_ir')

    def test_retrospectivo_e_conhecido_na_epoca_nao_se_confundem(self):
        self.prepara()
        j = self.val(modos=('retrospectivo', 'conhecido_na_epoca'))
        modos = {i['modo']: i for i in j['itens']}
        self.assertGreater(modos['retrospectivo']['n_alvos_com_previsao_ir'],
                           modos['conhecido_na_epoca']['n_alvos_com_previsao_ir'])

    def test_insuficiencia_nao_e_apresentada_como_falha_do_modelo(self):
        c, a = cenario(n_semanas=24)
        self.curvs[:] = c
        self.ativ[:] = a
        self.sync()
        self.prepara()
        it = self.val()['itens'][0]
        self.assertIn(it['estado_suficiencia'], (V.EST_SEM_ALVOS, V.EST_DESCRITIVO))
        self.assertIsNone(it['veredicto'])
        self.assertIn(it['motivo_sem_veredicto'], ('sem_alvos_com_previsao_ir', 'amostra_insuficiente_para_avaliar'))

    def test_veredicto_so_com_amostra_avaliavel_e_com_vocabulario_do_ir(self):
        self.prepara()
        it = self.val()['itens'][0]
        if it['estado_suficiencia'] == V.EST_AVALIAVEL:
            self.assertIn(it['veredicto'], ('ir_melhor_que_ambos_baselines', 'ir_pior_que_algum_baseline',
                                            'diferenca_nao_demonstrada'))
        else:
            self.assertIsNone(it['veredicto'])
        self.assertFalse(self.val()['promocao'])

    def test_erro_de_ajuste_historico_nao_entra_na_avaliacao(self):
        self.prepara()
        self.rec()
        a = self.val()['itens'][0]['metricas']['IR']
        with mock.patch.object(db, 'ajustes_ir_historico', side_effect=AssertionError('leu o historico')), \
                mock.patch.object(db, 'ultimo_ajuste_ir', side_effect=AssertionError('leu o ultimo')):
            b = self.val()['itens'][0]['metricas']['IR']
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
