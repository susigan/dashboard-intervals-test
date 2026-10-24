"""Testes do diagnostico de carga, da cobertura historica, da rastreabilidade e da analise season (experimental).

DADOS SINTETICOS (tests/pmc/ir_sintetico.py): usados para testar comportamento controlado. Nao concluem nada
sobre parametros reais. A baseline all-time vem de tests/pmc/fixtures/ir_all_time_base_24ddf4a.json, gerada com
o codigo do commit de base 24ddf4a sobre os mesmos dados sinteticos.
"""
import copy
import json
import os
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..')))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
import modelo_ir as ir  # noqa: E402
import modelo_ir_carga_diag as mcd  # noqa: E402
import modelo_ir_exploratorio as mex  # noqa: E402
import modelo_ir_season as ms  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import protocolo  # noqa: E402
import modelo_observacoes_auto as moa  # noqa: E402
import ir_sintetico as S  # noqa: E402

CONTROLE_SNAPSHOT = os.path.join(HERE, 'fixtures', 'ir_all_time_base_24ddf4a.json')


def _velho(i):
    return {k: i.get(k) for k in ('modalidade', 'duracao_s', 'estado', 'motivo', 'parametros', 'metricas',
                                  'identificabilidade', 'avisos', 'convergencia', 'n_obs', 'n_datas_distintas',
                                  'data_ini', 'data_fim', 'p0_inicial', 'serie_ini', 'curva', 'persistido', 'modo')}


def _arred(o):
    if isinstance(o, float):
        return round(o, 6)
    if isinstance(o, dict):
        return {k: _arred(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_arred(v) for v in o]
    return o


# ── A. all-time inalterado ──────────────────────────────────────────────────────────────────────────

class AllTimeInalterado(unittest.TestCase):
    """O resultado all-time sobre dados sinteticos e IDENTICO ao do codigo de base (snapshot 24ddf4a)."""

    def test_all_time_identico_ao_codigo_de_base(self):
        carga, sel, atuais, cfg = S.cenario()
        sel_all = {k: {'usadas': [dict(o, referencia_tipo='all_time') for o in v['usadas']]} for k, v in sel.items()}
        itens = mex.analisar(sel_all, {'Bike': carga}, '2025-03-01', cfg, (300,), anos_historico=5)
        with open(CONTROLE_SNAPSHOT, encoding='utf-8') as f:
            base = json.load(f)
        self.assertEqual(json.dumps(_arred([_velho(i) for i in itens]), sort_keys=True),
                         json.dumps(base, sort_keys=True))

    def test_enriquecer_usadas_nao_muda_quem_entra_nem_o_resultado(self):
        carga, sel, atuais, cfg = S.cenario()
        sel_all = {k: {'usadas': [dict(o, referencia_tipo='all_time') for o in v['usadas']]} for k, v in sel.items()}
        antes = mex.analisar(copy.deepcopy(sel_all), {'Bike': carga}, '2025-03-01', cfg, (300,))
        enr = mis.enriquecer_usadas(sel_all, atuais)
        depois = mex.analisar(enr, {'Bike': carga}, '2025-03-01', cfg, (300,))
        self.assertEqual(_arred([_velho(i) for i in antes]), _arred([_velho(i) for i in depois]))
        self.assertEqual(len(enr[('Bike', 300)]['usadas']), len(sel_all[('Bike', 300)]['usadas']))


# ── B. diagnostico de carga ──────────────────────────────────────────────────────────────────────────

def _atividades_mistas():
    return [
        {'id': 1, 'type': 'Ride', 'start_date_local': '2024-01-01T08:00', 'icu_training_load': 80},      # positiva
        {'id': 2, 'type': 'Ride', 'start_date_local': '2024-01-01T09:00'},                               # sem campo
        {'id': 3, 'type': 'Ride', 'start_date_local': '2024-01-02T08:00', 'icu_training_load': 0},      # zero valido
        {'id': 4, 'type': 'Ride', 'start_date_local': '2024-01-03T08:00', 'icu_training_load': -5},     # negativa
        {'id': 5, 'type': 'Ride', 'start_date_local': '2024-01-04T08:00', 'icu_training_load': '60'},   # texto
        {'id': 6, 'type': 'Ride', 'start_date_local': '2024-01-05T08:00', 'icu_training_load': True},   # bool
        {'id': 7, 'type': 'Ride', 'start_date_local': '2024-01-06T08:00', 'icu_training_load': float('nan')},
        {'id': 8, 'type': 'Ride', 'start_date_local': '2024-01-07T08:00', 'icu_training_load': float('inf')},
        {'id': 1, 'type': 'Ride', 'start_date_local': '2024-01-01T08:00', 'icu_training_load': 80},      # duplicada
        {'id': 9, 'type': 'Ride', 'start_date_local': '', 'icu_training_load': 10},                      # sem data
        {'id': 10, 'type': 'Weird', 'start_date_local': '2024-01-01T08:00', 'icu_training_load': 10},   # tipo
        'lixo',                                                                                         # nao dict
        {'id': 11, 'type': 'Run', 'start_date_local': '2024-01-01T08:00', 'icu_training_load': 12},
    ]


class DiagnosticoDeCarga(unittest.TestCase):
    """Dados sinteticos e de borda. A serie do diagnostico e a da producao, e as categorias somam exatamente."""

    def test_categorias_somam_exatamente_a_serie_de_producao(self):
        A = _atividades_mistas()
        serie, info = mis.carga_por_modalidade(A)
        dg = mcd.diagnostico_carga(A)
        for m in serie:
            cats = dg['por_modalidade'][m]
            pos = sum(v for k, v in cats.items() if k in mcd.ENTRAM_NA_SERIE)
            sem = sum(v for k, v in cats.items() if k in mcd.SEM_CARGA_NA_SERIE)
            self.assertEqual(pos, info['n_atividades_com_carga'][m], m)
            self.assertEqual(sem, info['n_atividades_sem_carga'][m], m)
            self.assertEqual(dg['serie'][m], serie[m], m)

    def test_nan_e_inf_nao_entram_na_serie(self):
        # bug concreto revelado pelos testes: +inf entrava na serie (contaminava o solver). Corrigido.
        serie, _ = mis.carga_por_modalidade(_atividades_mistas())
        self.assertNotIn('2024-01-07', serie['Bike'])
        self.assertTrue(all(np.isfinite(v) for v in serie['Bike'].values()))

    def test_categorias_explicitas_para_cada_caso(self):
        cats = mcd.diagnostico_carga(_atividades_mistas())['por_modalidade']['Bike']
        self.assertEqual(cats['carga_positiva'], 1)
        self.assertEqual(cats['sem_icu_training_load'], 1)
        self.assertEqual(cats['carga_zero_valida'], 1)
        self.assertEqual(cats['carga_negativa'], 1)
        self.assertEqual(cats['carga_nao_numerica'], 4)       # texto, bool, nan, +inf
        self.assertEqual(cats['duplicada'], 1)
        self.assertEqual(cats['sem_data'], 1)

    def test_dia_sem_atividade_e_zero_sem_corte_em_2023(self):
        """Nao existe regra de 2023: dia sem carga numerica e zero antes e depois de 2023-01-01."""
        A = [
            {'id': 'a', 'type': 'Ride', 'start_date_local': '2022-12-31T08:00', 'icu_training_load': 80},
            {'id': 'b', 'type': 'Ride', 'start_date_local': '2023-01-01T08:00', 'icu_training_load': 0},
            {'id': 'c', 'type': 'Ride', 'start_date_local': '2023-01-02T08:00'},
        ]
        serie, _ = mis.carga_por_modalidade(A)
        vec = {d: serie['Bike'].get(d, 0.0) for d in ('2022-12-30', '2022-12-31', '2023-01-01', '2023-01-02',
                                                       '2023-01-03')}
        self.assertEqual(vec, {'2022-12-30': 0.0, '2022-12-31': 80.0, '2023-01-01': 0.0,
                               '2023-01-02': 0.0, '2023-01-03': 0.0})

    def test_cobertura_da_carga_separada_da_das_observacoes(self):
        self.assertEqual(mcd.cobertura_carga({'2024-01-02': 5.0, '2024-01-09': 3.0}),
                         {'data_ini': '2024-01-02', 'data_fim': '2024-01-09', 'dias_com_carga_registada': 2})
        self.assertEqual(mcd.cobertura_carga({})['dias_com_carga_registada'], 0)


# ── D. cobertura historica e inicializacao do filtro (sintetico) ─────────────────────────────────────

class CoberturaHistorica(unittest.TestCase):

    def test_historico_antes_do_primeiro_esforco_e_contado(self):
        carga, sel, atuais, cfg = S.cenario(inicio='2023-01-01')
        carga_ext = {'2022-12-01': 50.0, **carga}                 # historico de 31 dias antes do 1.o dia
        u = mex.ajustar_unidade('Bike', 300, sel[('Bike', 300)]['usadas'], carga_ext, '2025-03-01', cfg,
                                anos_historico=5)
        h = u['cobertura_historico']
        self.assertEqual(h['carga_data_ini'], '2022-12-01')
        self.assertEqual(h['serie_ini'], '2022-12-01')
        self.assertEqual(h['dias_historico_antes_primeiro_esforco'],
                         (date(2023, 1, 1) - date(2022, 12, 1)).days)
        self.assertTrue(h['carga_cobre_primeiro_esforco'])
        self.assertEqual(h['janela_solver']['fim'], u['data_fim'])
        self.assertIn('nao_determinavel', h['lacunas'])

    def test_carga_que_comeca_depois_do_primeiro_esforco_nao_cobre(self):
        carga, sel, atuais, cfg = S.cenario(inicio='2023-01-01')
        tarde = {k: v for k, v in carga.items() if k >= '2023-06-01'}
        u = mex.ajustar_unidade('Bike', 300, sel[('Bike', 300)]['usadas'], tarde, '2025-03-01', cfg)
        self.assertFalse(u['cobertura_historico']['carga_cobre_primeiro_esforco'])

    def test_truncamento_da_sincronizacao_e_sinalizado(self):
        ref = '2026-10-22'
        inicio_janela = (date.fromisoformat(ref) - timedelta(days=round(365.25 * 5))).isoformat()
        carga, sel, atuais, cfg = S.cenario(inicio='2023-01-01')
        cortada = {inicio_janela: 60.0, **{k: v for k, v in carga.items() if k > inicio_janela}}
        u = mex.ajustar_unidade('Bike', 300, sel[('Bike', 300)]['usadas'], cortada, ref, cfg, anos_historico=5)
        self.assertIs(u['cobertura_historico']['truncamento_sincronizacao'], True)
        u2 = mex.ajustar_unidade('Bike', 300, sel[('Bike', 300)]['usadas'], carga, '2025-03-01', cfg,
                                 anos_historico=5)
        self.assertIs(u2['cobertura_historico']['truncamento_sincronizacao'], False)
        u3 = mex.ajustar_unidade('Bike', 300, sel[('Bike', 300)]['usadas'], carga, '2025-03-01', cfg)
        self.assertEqual(u3['cobertura_historico']['truncamento_sincronizacao'], 'nao_avaliado')

    def test_efeito_do_historico_inicial_sobre_a_previsao_quantificado(self):
        """Quantifica (sinteticos) quanto o historico anterior ao 1.o esforco muda a previsao.

        Historico real de 31 dias de carga 80 em dez/2022 contra o mesmo periodo sem historico (zeros)."""
        p = S.PARAMETROS_VERDADEIROS
        d_ini = date(2022, 12, 1)
        n = 120                                  # ate ~2023-03-31
        c_com = np.array([80.0 if i < 31 else 60.0 for i in range(n)])
        c_sem = c_com.copy()
        c_sem[:31] = 0.0                         # historico ausente tratado como repouso
        pred = lambda c: ir.desempenho(np.concatenate([c, [0.0]]), p['p0'], p['k1'], p['k2'], p['t1'], p['t2'], 1)  # noqa
        idx = (date(2023, 2, 1) - d_ini).days    # 2023-02-01: 62 dias depois do inicio da serie
        diff = abs(pred(c_com)[idx] - pred(c_sem)[idx])
        self.assertGreater(diff, 0.5, f'efeito do historico inicial (sintetico): {diff:.3f} W')

    def test_zeros_antes_do_inicio_nao_mudam_a_previsao(self):
        """Filtro iniciado em zero: prepender zeros e neutro (a ancoragem da serie nao altera o numero)."""
        c = np.random.default_rng(3).uniform(30, 100, 120)
        pred = lambda x: ir.desempenho(np.concatenate([x, [0.0]]), 600.0, 1.2, 2.5, 40.0, 6.0, 1)[-1]  # noqa
        self.assertAlmostEqual(pred(c), pred(np.concatenate([np.zeros(40), c])), places=9)


# ── E. rastreabilidade das exclusoes e observacoes ───────────────────────────────────────────────────

class Rastreabilidade(unittest.TestCase):

    def test_observacoes_do_solver_trazem_esforco_e_referencia(self):
        carga, sel, atuais, cfg = S.cenario()
        enr = mis.enriquecer_usadas(sel, atuais)
        u = mex.ajustar_unidade('Bike', 300, enr[('Bike', 300)]['usadas'], carga, '2025-03-01', cfg)
        o = u['observacoes'][0]
        self.assertTrue(o['incluida_no_solver'])
        self.assertTrue(o['atividade_esforco_id'].startswith('a'))
        self.assertEqual(o['referencia_tipo'], 'season')
        self.assertTrue(o['referencia_activity_id'].startswith('r'))
        self.assertEqual(len(u['observacoes']), u['n_obs'])

    def test_exclusoes_trazem_id_do_esforco_e_nao_entram_no_solver(self):
        exc = [{'modalidade': 'Bike', 'duracao_s': 300, 'test_id': 'Bike:300:x1', 'activity_id': 'a1',
                'data': '2024-02-01', 'watts': 262.0, 'motivo': 'referencia_nao_all_time:season',
                'referencia_tipo': 'season', 'referencia_activity_id': 'r9', 'referencia_data': '2024-01-20',
                'referencia_watts': 286.0, 'referencias': {'season': {'pct': 91.6}}}]
        res = mis.resumo_exclusoes({('Bike', 300): {'usadas': [], 'excluidas': exc}})
        e = res['lista'][0]
        self.assertEqual(e['atividade_esforco_id'], 'a1')
        self.assertEqual(e['referencia_activity_id'], 'r9')
        self.assertIs(e['incluida_no_solver'], False)


# ── C e F. analise season: fronteiras, gates, duplicados, classificacao ──────────────────────────────

MARCOS = [('2023-01-01', '2023/24'), ('2024-07-01', '2024/25')]


class SeasonFronteiras(unittest.TestCase):

    def test_fronteiras_vem_dos_marcos_reais_nao_do_ano_civil(self):
        fr = ms.fronteiras(MARCOS, '2025-03-01')
        self.assertEqual([(f['season'], f['inicio'], f['fim'], f['aberta']) for f in fr],
                         [('2023/24', '2023-01-01', '2024-06-30', False),
                          ('2024/25', '2024-07-01', '2025-03-01', True)])
        self.assertTrue(all(f['fonte'] == ms.FONTE_FRONTEIRAS for f in fr))

    def test_sem_marcos_nao_usa_ano_civil(self):
        self.assertEqual(ms.fronteiras(None, '2025-03-01'), [])
        self.assertEqual(ms.fronteiras([], '2025-03-01'), [])

    def test_sem_marcos_resultado_nao_calculado_e_sem_ajuste(self):
        carga, sel, atuais, cfg = S.cenario()
        r = ms.analisar(sel, atuais, {'Bike': carga}, '2025-03-01', None, cfg, (300,))
        self.assertEqual(r['status'], 'fronteiras_nao_confirmadas')
        bike = [i for i in r['itens'] if i['modalidade'] == 'Bike'][0]
        self.assertEqual(bike['classificacao'], ms.CLASS_NAO_CALCULADO)
        self.assertIsNone(bike['parametros'])
        self.assertGreater(bike['n_candidatas_season'], 0)

    def test_analise_exploratoria_sem_marcos_nao_consulta_calendario(self):
        with mock.patch('observacoes_auto.obter_marcos', side_effect=AssertionError('nao deve ir a rede')):
            r = mis.analise_exploratoria(historico_obs=[], atividades=[], curvas=[], referencia='2025-03-01')
        self.assertEqual(r['season']['status'], 'fronteiras_nao_confirmadas')


class SeasonAnalise(unittest.TestCase):

    def setUp(self):
        self.carga, self.sel, self.atuais, self.cfg = S.cenario()

    def _por_season(self, r):
        return {i['season']: i for i in r['itens'] if i['season']}

    def test_cada_season_com_fronteira_real_e_ajustada_separadamente(self):
        r = ms.analisar(self.sel, self.atuais, {'Bike': self.carga}, '2025-03-01', MARCOS, self.cfg, (300,))
        self.assertEqual(r['status'], 'OK')
        por = self._por_season(r)
        self.assertEqual(set(por), {'2023/24', '2024/25'})
        for s, it in por.items():
            self.assertEqual(it['fonte_fronteiras'], ms.FONTE_FRONTEIRAS)
            self.assertIs(it['persistido'], False)
            self.assertEqual(it['modo'], ms.MODO)
            for o in it['observacoes']:
                self.assertEqual(o['referencia_tipo'], 'season')
                self.assertTrue(o['atividade_esforco_id'])
                self.assertTrue(o['referencia_activity_id'])
                self.assertIs(o['incluida_no_solver'], True)
                f = [f for f in r['fronteiras'] if f['season'] == s][0]
                self.assertTrue(f['inicio'] <= o['data'] <= f['fim'], (s, o['data']))

    def test_sem_duplicados_dentro_do_conjunto_de_ajuste(self):
        dup = copy.deepcopy(self.sel)
        dup[('Bike', 300)]['usadas'].append(dict(dup[('Bike', 300)]['usadas'][0]))
        r = ms.analisar(dup, self.atuais, {'Bike': self.carga}, '2025-03-01', MARCOS, self.cfg, (300,))
        for it in r['itens']:
            ids = [o['test_id'] for o in it.get('observacoes', [])]
            self.assertEqual(len(ids), len(set(ids)))
        motivos = [e['motivo'] for it in r['itens'] for e in it.get('exclusoes', [])]
        self.assertIn('duplicada', motivos)

    def test_rotulo_inconsistente_e_excluido_com_motivo(self):
        at = copy.deepcopy(self.atuais)
        at[0]['season'] = '2024/25'                       # rotulo nao bate com a data
        r = ms.analisar(self.sel, at, {'Bike': self.carga}, '2025-03-01', MARCOS, self.cfg, (300,))
        motivos = [e['motivo'] for it in r['itens'] for e in it.get('exclusoes', [])]
        self.assertIn('rotulo_season_inconsistente', motivos)

    def test_observacoes_fora_das_fronteiras_nao_entram(self):
        r = ms.analisar(self.sel, self.atuais, {'Bike': self.carga}, '2024-10-01', MARCOS, self.cfg, (300,))
        motivos = [e['motivo'] for it in r['itens'] for e in it.get('exclusoes', [])]
        self.assertIn('fora_das_fronteiras_season', motivos)

    def test_colecao_pequena_fica_insuficiente_e_nao_valida(self):
        """Gate de 15 observacoes mantido: uma season pequena nao vira calibracao, e nao se relaxa o gate."""
        sel_pequeno = copy.deepcopy(self.sel)
        sel_pequeno[('Bike', 300)]['usadas'] = sel_pequeno[('Bike', 300)]['usadas'][:5]
        r = ms.analisar(sel_pequeno, self.atuais, {'Bike': self.carga}, '2025-03-01', MARCOS, self.cfg, (300,))
        for it in r['itens']:
            if it['season']:
                self.assertEqual(it['classificacao'], ms.CLASS_INSUFICIENTE)
                self.assertIs(it['valido_para_calibracao'], False)
                self.assertIsNone(it['parametros'])

    def test_convergencia_e_identificabilidade_sao_estados_distintos(self):
        r = ms.analisar(self.sel, self.atuais, {'Bike': self.carga}, '2025-03-01', MARCOS, self.cfg, (300,))
        for it in self._por_season(r).values():
            self.assertIn('convergiu', it['convergencia'] or {})
            self.assertIn('parametros_no_limite', it['identificabilidade'] or {})
            self.assertFalse('convergiu' in (it['identificabilidade'] or {}))

    def test_nao_identificavel_nao_e_apresentado_como_valido(self):
        falso = {'estado': ir.EST_NAO_IDENTIFICAVEL, 'parametros': {'p0': 1, 'k1': 1, 'k2': 1, 't1': 120, 't2': 1},
                 'convergencia': {'convergiu': True}, 'identificabilidade': {'parametros_no_limite': ['t1']},
                 'curva': {'datas': []}, 'motivo': None, 'n_obs': 20}
        with mock.patch('modelo_ir_exploratorio.ajustar_unidade', return_value=dict(falso, season=None)):
            r = ms.analisar(self.sel, self.atuais, {'Bike': self.carga}, '2025-03-01', MARCOS, self.cfg, (300,))
        for it in self._por_season(r).values():
            self.assertEqual(it['classificacao'], ms.CLASS_NAO_IDENTIFICAVEL)
            self.assertIs(it['valido_para_calibracao'], False)
            self.assertIsNone(it['curva'])

    def test_candidata_so_com_identificavel_convergido_e_sem_limite(self):
        ok = {'estado': ir.EST_IDENTIFICAVEL, 'parametros': {'p0': 600, 'k1': 1, 'k2': 2, 't1': 40, 't2': 6},
              'convergencia': {'convergiu': True}, 'identificabilidade': {'parametros_no_limite': []},
              'curva': {'datas': []}, 'motivo': None, 'n_obs': 20}
        limite = dict(ok, identificabilidade={'parametros_no_limite': ['t1']})
        self.assertEqual(ms._classificar(ok)[0], ms.CLASS_CANDIDATA)
        self.assertEqual(ms._classificar(limite)[0], ms.CLASS_VALIDACAO)

    def test_all_time_nao_e_alterado_pela_analise_season(self):
        sel_all = {k: {'usadas': [dict(o, referencia_tipo='all_time') for o in v['usadas']]} for k, v in
                   self.sel.items()}
        antes = copy.deepcopy(sel_all)
        mex.analisar(sel_all, {'Bike': self.carga}, '2025-03-01', self.cfg, (300,))
        ms.analisar(self.sel, self.atuais, {'Bike': self.carga}, '2025-03-01', MARCOS, self.cfg, (300,))
        self.assertEqual(sel_all, antes)

    def test_candidatas_nao_misturam_all_time(self):
        cand = ms.candidatas(self.sel, self.atuais)[('Bike', 300)]
        self.assertTrue(all(o['referencia_tipo'] == 'season' for o in cand))


# ── G. gates e limiares inalterados ──────────────────────────────────────────────────────────────────

class GatesInalterados(unittest.TestCase):

    def test_gates_e_limiar_continuam_os_mesmos(self):
        c = ir.CONFIG_PADRAO
        self.assertEqual((c['min_obs'], c['min_datas_distintas'], c['min_dias_cobertura']), (15, 12, 180))
        self.assertEqual(protocolo.LIMIAR_ESFORCO, 0.92)
        self.assertEqual(moa.LIMIAR_PROXIMIDADE, 0.92)

    def test_season_nao_relaxa_o_gate_de_cobertura(self):
        carga, sel, atuais, cfg = S.cenario()
        curta = {k: {'usadas': v['usadas'][:20]} for k, v in sel.items()}   # ~20 obs num periodo curto
        r = ms.analisar(curta, atuais, {'Bike': carga}, '2025-03-01', MARCOS, cfg, (300,))
        for it in r['itens']:
            if it.get('season') and it['n_obs'] < 15:
                self.assertEqual(it['classificacao'], ms.CLASS_INSUFICIENTE)


# ── F. frontend: all-time e season apresentados separadamente (verificacao de contrato) ─────────────

class ContratoDaResposta(unittest.TestCase):

    def test_resposta_tem_season_como_bloco_separado_do_itens(self):
        r = mis.analise_exploratoria(historico_obs=[], atividades=[], curvas=[], referencia='2025-03-01')
        self.assertIn('season', r)
        self.assertIn('itens', r)
        self.assertIsNot(r['season'], r['itens'])
        self.assertEqual(r['season']['modo'], ms.MODO)
        self.assertNotEqual(r['season']['modo'], r['modo'])


# ── F10. frontend: all-time e season em blocos separados (renderizacao real, via harness Node) ──────

class FrontendSeason(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import shutil
        import subprocess
        import tempfile
        if shutil.which('node') is None:
            raise unittest.SkipTest('node indisponivel')
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import tabs.tab_pmc as tp
        except BaseException as e:
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        itens = [
            {'modalidade': 'Bike', 'duracao_s': 300, 'season': '2023/24', 'inicio': '2023-01-01', 'fim': '2024-06-30',
             'data_ini': '2023-01-05', 'data_fim': '2024-05-01', 'n_obs': 20, 'classificacao': 'candidata_calibracao',
             'motivo_classificacao': 'identificavel', 'valido_para_calibracao': True,
             'parametros': {'p0': 600.1234, 'k1': 1.2, 'k2': 2.5, 't1': 40.0, 't2': 6.0},
             'tipos_referencia': ['season'], 'n_tambem_all_time': 3, 'ids_esforcos': ['a1', 'a2'],
             'exclusoes': []},
            {'modalidade': 'Ski', 'duracao_s': 300, 'season': '2024/25', 'inicio': '2024-07-01', 'fim': '2025-03-01',
             'data_ini': '2024-08-01', 'data_fim': '2025-01-01', 'n_obs': 16, 'classificacao': 'nao_identificavel',
             'motivo_classificacao': 'valores de fronteira', 'valido_para_calibracao': False,
             'parametros': {'p0': 1, 'k1': 1, 'k2': 1, 't1': 120, 't2': 1}, 'tipos_referencia': ['season'],
             'n_tambem_all_time': 0, 'ids_esforcos': ['b1'], 'exclusoes': [{'motivo': 'x'}]},
        ]
        j_com = {'status': 'OK', 'season': {
            'status': 'OK', 'fonte_fronteiras': 'calendario_SEASON_START', 'aviso': 'aviso season',
            'fronteiras': [{'season': '2023/24', 'inicio': '2023-01-01', 'fim': '2024-06-30', 'aberta': False},
                           {'season': '<script>x</script>', 'inicio': '2024-07-01', 'fim': '2025-03-01',
                            'aberta': True}],
            'itens': itens}}
        j_sem = {'status': 'OK', 'season': {'status': 'fronteiras_nao_confirmadas', 'aviso': 'a',
                                            'fronteiras': [], 'itens': []}}
        casos = {'com': {'j': j_com}, 'sem_marcos': {'j': j_sem}, 'antigo': {'j': {'status': 'OK'}}}
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, 'pmc_js.js'), os.path.join(d, 'casos.json')
            with open(a, 'w', encoding='utf-8') as f:
                f.write(tp.JS)
            with open(b, 'w', encoding='utf-8') as f:
                json.dump(casos, f)
            r = subprocess.run(['node', os.path.join(HERE, 'js', 'harness_ir_exploratorio.js'), a, b],
                               capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError(f'harness falhou: {r.stdout[-300:]} {r.stderr[-300:]}')
        cls.out = json.loads(r.stdout)

    def test_bloco_season_separado_do_all_time(self):
        h = self.out['com']['season']
        self.assertIn('Season — experimental, separada do all-time (não combinada)', h)
        self.assertIn('calendario_SEASON_START', h)

    def test_parametros_so_aparecem_quando_validos(self):
        h = self.out['com']['season']
        self.assertIn('p0=600.123', h)                        # candidata: parametros mostrados
        self.assertIn('valores não válidos', h)               # nao identificavel: rotulado
        self.assertEqual(h.count('p0='), 1)                    # o nao identificavel NAO mostra p0=1

    def test_season_com_html_e_escapada(self):
        h = self.out['com']['season']
        self.assertNotIn('<script>x</script>', h)
        self.assertIn('&lt;script&gt;', h)

    def test_sem_marcos_mostra_fronteiras_indisponiveis(self):
        h = self.out['sem_marcos']['season']
        self.assertIn('Fronteiras de season indisponíveis', h)
        self.assertIn('nenhum ajuste de season foi feito', h)
        self.assertNotIn('undefined', h)

    def test_resposta_antiga_sem_season_nao_quebra(self):
        self.assertEqual(self.out['antigo']['season'], '')


if __name__ == '__main__':
    unittest.main()
