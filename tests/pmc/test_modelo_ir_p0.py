"""P0 = CP do resultado guardado no CP Model (modelo_ir_p0 + Solver com P0 fixo + servico).

    python -m pytest tests/pmc/test_modelo_ir_p0.py

Offline. Dados SINTETICOS: testam o codigo, nao o atleta. Decisao final: P0 = CP, EXATAMENTE o valor de CP
(`cp_w`) do resultado selecionado e guardado; CP + W'/d foi rejeitado. A duracao e W' nao entram no P0.
"""
import copy
import json
import math
import os
import re
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, '..', '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cp_fixture as CPF  # noqa: E402
import modelo_ir as ir  # noqa: E402
import modelo_ir_p0 as p0m  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import test_modelo_ir_servico as S  # noqa: E402
import ir_duracao_legado as _LEG  # noqa: E402


def setUpModule():
    _LEG.iniciar()


def tearDownModule():
    _LEG.parar()

T, db, AGORA = S.T, S.db, S.AGORA
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))

CARREGAR_REAL = mis.carregar_instantaneos_cp          # antes de qualquer patch do _Base

M1, M2, M3, M2P = 'M1 (WLS-P)', 'M2 (WLS-1/t)', 'M3 (NL-2p)', '2p hiperbolico'
CP = CPF.CP_PADRAO


def inst(**kw):
    return CPF.instantaneo(**kw)


def p0m_all():
    return CPF.instantaneos_padrao()


# ── 1, 2, 3: P0 = CP (resolvedor puro) ───────────────────────────────────────────────────

class ResolvedorValido(unittest.TestCase):
    def test_01_p0_e_exatamente_igual_ao_cp_guardado(self):
        for cp in (290.0, 233.6268, 1e-3, 412.123456789012, 270.0):
            r = p0m.resolver_p0('Bike', 1200, [inst(cp=cp)])
            self.assertTrue(r['valida'], r)
            self.assertIs(type(r['valor_p0']), float)
            self.assertEqual(r['valor_p0'], cp)                    # == exato: sem arredondar nem somar nada
            self.assertEqual(r['cp_w'], cp)
        r = p0m.resolver_p0('Bike', 1200, [inst()])
        self.assertEqual((r['modalidade'], r['modelo_cp'], r['formulacao']), ('Bike', M1, 'p0_igual_cp'))
        self.assertEqual(r['equacao'], p0m.EQUACAO)
        self.assertEqual(r['instantaneo']['id'], 1)
        self.assertTrue(r['assinatura'])

    def test_02_alteracoes_de_wprime_nao_alteram_p0_nem_assinatura(self):
        base = p0m.resolver_p0('Bike', 1200, [inst(wp=24000.0)])
        for wp in (8000.0, 24000.0, 29999.0, 60000.0, 12.0, None):     # ate W' implausivel/ausente: o P0 e o mesmo
            r = p0m.resolver_p0('Bike', 1200, [inst(wp=wp)])
            self.assertTrue(r['valida'], (wp, r))
            self.assertEqual(r['valor_p0'], base['valor_p0'], wp)
            self.assertEqual(r['assinatura'], base['assinatura'], wp)
        # W' fora do intervalo so avisa (informativo): nao bloqueia nem muda o P0
        self.assertIn('wprime_ausente_ou_fora_do_intervalo_plausivel', p0m.resolver_p0('Bike', 1200, [inst(wp=12.0)])['avisos_cp'])
        self.assertNotIn('wprime_ausente_ou_fora_do_intervalo_plausivel', base['avisos_cp'])

    def test_03_a_duracao_do_ir_nao_altera_o_p0(self):
        rs = [p0m.resolver_p0('Bike', d, [inst()]) for d in (60, 300, 720, 1200, 3600, 1, None, 'x', float('nan'))]
        self.assertTrue(all(r['valida'] for r in rs))
        self.assertEqual({r['valor_p0'] for r in rs}, {CP})
        self.assertEqual({r['assinatura'] for r in rs}, {rs[0]['assinatura']})
        # sem janela do CP nem dominio de pontos: 60 s e 3600 s tem o mesmo P0
        self.assertEqual(p0m.resolver_p0('Row', 60, [inst(modalidade='Row')])['valor_p0'], CP)

    def test_o_p0_nao_e_cp_mais_wprime_sobre_d(self):
        r = p0m.resolver_p0('Bike', 300, [inst(cp=270.0, wp=24000.0)])
        self.assertEqual(r['valor_p0'], 270.0)
        self.assertNotAlmostEqual(r['valor_p0'], 270.0 + 24000.0 / 300.0, places=1)
        self.assertFalse(hasattr(p0m, 'FORMULACOES'))               # a interpretacao CP + W'/d foi removida

    def test_instantaneo_em_vigor_segue_a_ordem_do_api_cp_actual(self):
        velho = inst(id=1, data_referencia='2025-01-01', cp=260.0, wp=24000.0)
        novo = inst(id=2, data_referencia='2025-06-01', cp=270.0, wp=24000.0)
        self.assertEqual(p0m.instantaneo_em_vigor('Bike', [velho, novo])['id'], 2)
        self.assertEqual(p0m.instantaneo_em_vigor('Bike', [novo, velho])['id'], 2)
        self.assertIsNone(p0m.instantaneo_em_vigor('Run', [velho, novo]))

    def test_todos_os_modelos_do_cp_model_dao_o_seu_proprio_cp_sem_mudar_a_escolha(self):
        for nome in (M1, M2, M3, M2P, '3p hiperbolico', 'Ward-Smith', 'OM3CP', 'OMExp'):
            r = p0m.resolver_p0('Bike', 1200, [inst(modelo=nome, cp=281.5)])
            self.assertTrue(r['valida'], (nome, r))
            self.assertEqual((r['modelo_cp'], r['valor_p0']), (nome, 281.5))
            self.assertEqual('modelo_de_3_parametros' in r['avisos_cp'], nome in p0m.MODELOS_3P, nome)


class ResolvedorBloqueios(unittest.TestCase):
    def assertBloqueado(self, r, motivo):
        self.assertFalse(r['valida'], r)
        self.assertEqual(r['motivo'], motivo, r)
        self.assertIsNone(r['valor_p0'])
        self.assertTrue(r['detalhe'])

    def test_09_cp_invalido_ou_resultado_incompleto_bloqueia(self):
        self.assertBloqueado(p0m.resolver_p0('Bike', 1200, [inst(cp=None)]), p0m.M_SEM_INSTANTANEO)
        for mau in (float('nan'), float('inf'), -5.0, 0.0, True, 'x'):
            i = inst(); i['cp_w'] = mau                             # valor mau gravado na linha (o fixture arredonda)
            self.assertBloqueado(p0m.resolver_p0('Bike', 1200, [i]), p0m.M_CP)
        self.assertBloqueado(p0m.resolver_p0('Bike', 1200, [inst(modelo=None)]), p0m.M_SEM_MODELO)
        # modelos_json contradiz o CP gravado (integridade da gravacao)
        mj = {M1: {'cp': 200.0, 'wp': 9000, 'see_pct': 1.0, 'classico': True, 'pontos_usados': []}}
        self.assertBloqueado(p0m.resolver_p0('Bike', 1200, [inst(modelos_json=mj)]), p0m.M_INCONSISTENTE)

    def test_graus_de_liberdade_do_ajuste_do_cp(self):
        # n_pts <= k_params: o CP seria uma interpolacao exata, sem SEE%: nao e estimativa
        for n, k in ((2, 2), (3, 3), (1, 2)):
            i = inst(); i['n_pts'], i['k_params'] = n, k
            self.assertBloqueado(p0m.resolver_p0('Bike', 1200, [i]), p0m.M_GL)
        i = inst(); i['n_pts'], i['k_params'] = 3, 2
        self.assertTrue(p0m.resolver_p0('Bike', 1200, [i])['valida'])
        i = inst(); i['n_pts'], i['k_params'] = None, None           # nao verificavel: avisa, nao inventa
        r = p0m.resolver_p0('Bike', 1200, [i])
        self.assertTrue(r['valida'])
        self.assertIn('graus_de_liberdade_cp_nao_verificaveis', r['avisos_cp'])

    def test_03_sem_resultado_confirmado_nao_usa_o_menor_see(self):
        self.assertBloqueado(p0m.resolver_p0('Bike', 1200, []), p0m.M_SEM_INSTANTANEO)
        self.assertBloqueado(p0m.resolver_p0('Bike', 1200, None), p0m.M_SEM_INSTANTANEO)
        self.assertBloqueado(p0m.resolver_p0('Run', 1200, [inst(modalidade='Bike')]), p0m.M_SEM_INSTANTANEO)

    def test_03b_o_modelo_gravado_manda_mesmo_que_outro_tenha_menor_see(self):
        pts = [{'t': 180, 'w': 420.0}, {'t': 300, 'w': 350.0}, {'t': 1200, 'w': 290.0}]
        mj = {M1: {'cp': 260.0, 'wp': 30000, 'see_pct': 0.4, 'classico': True, 'pontos_usados': pts},
              M2: {'cp': 270.0, 'wp': 24000, 'see_pct': 2.5, 'classico': True, 'pontos_usados': pts}}
        r = p0m.resolver_p0('Bike', 1200, [inst(modelo=M2, cp=270.0, wp=24000.0, modelos_json=mj)])
        self.assertTrue(r['valida'], r)
        self.assertEqual((r['valor_p0'], r['modelo_cp']), (270.0, M2))
        self.assertIs(r['coincide_com_menor_see_classico'], False)
        r1 = p0m.resolver_p0('Bike', 1200, [inst(modelo=M1, cp=260.0, wp=30000.0, modelos_json=mj)])
        self.assertEqual(r1['valor_p0'], 260.0)
        self.assertIs(r1['coincide_com_menor_see_classico'], True)

    def test_07_modalidade_incompativel_e_comportamento_explicito_e_seguro(self):
        for m in ('Swim', '', None, 'Overall', 'bike'):
            self.assertBloqueado(p0m.resolver_p0(m, 300, p0m_all()), p0m.M_SEM_INSTANTANEO)
        self.assertBloqueado(p0m.resolver_p0('Bike', 300, [None, 'x', {}, {'modalidade': 'Bike'}, 3]), p0m.M_SEM_INSTANTANEO)

    def test_13_as_modalidades_sao_independentes(self):
        snaps = [inst(modalidade='Bike', cp=290.0, id=1), inst(modalidade='Run', cp=330.0, id=2),
                 inst(modalidade='Row', cp=210.0, id=3)]
        self.assertEqual([p0m.resolver_p0(m, 300, snaps)['valor_p0'] for m in ('Bike', 'Run', 'Row')], [290.0, 330.0, 210.0])
        self.assertFalse(p0m.resolver_p0('Ski', 300, snaps)['valida'])

    def test_a_assinatura_depende_de_modalidade_modelo_cp_e_data_de_referencia_apenas(self):
        a = p0m.resolver_p0('Bike', 300, [inst(data_referencia='2026-01-10')])['assinatura']
        for kw in ({'cp': 291.0}, {'modelo': M2}, {'data_referencia': '2026-06-10'}):
            self.assertNotEqual(a, p0m.resolver_p0('Bike', 300, [inst(**{'data_referencia': '2026-01-10', **kw})])['assinatura'], kw)
        for kw in ({'wp': 11000.0}, {'see_pct': 9.0}, {'pontos': ((300, 1.0), (1200, 2.0), (720, 3.0))}, {'id': 77},
                   {'data_gravacao': '2030-01-01 00:00:00'}):
            self.assertEqual(a, p0m.resolver_p0('Bike', 300, [inst(**{'data_referencia': '2026-01-10', **kw})])['assinatura'], kw)
        self.assertNotEqual(a, p0m.resolver_p0('Run', 300, [inst(modalidade='Run', data_referencia='2026-01-10')])['assinatura'])


class ConversaoPorFormulacao(unittest.TestCase):
    """Os modelos do CP Model (fit_m1/m2/m3/2p) produzem o seu CP; o P0 e esse CP, nao a potencia prevista."""
    PONTOS = [(420.0 + 1.0, 180.0), (350.0 - 2.0, 300.0), (310.0 + 1.5, 600.0), (296.0 - 0.8, 900.0),
              (290.0 + 0.7, 1200.0)]                       # (P, t) com ruido

    def _fit(self, nome):
        import utils.cp_model as cpm
        tests = self.PONTOS
        w = cpm.make_w([t for _, t in tests], 'nenhum')
        if nome == M1:
            cp, wp, _x, pp, *_ = cpm.fit_m1(tests, w)
        elif nome == M2:
            cp, wp, _x, pp, *_ = cpm.fit_m2(tests, w)
        elif nome == M3:
            cp, wp, _x, pp, *_ = cpm.fit_m3(tests, w)
        else:
            cp, wp, _x, pp = cpm.fit_2p_hyperbolic(tests)
        return cp, wp, pp

    def test_p0_e_o_cp_do_proprio_ajuste_do_cp_model_e_nao_a_potencia_prevista(self):
        for nome in (M1, M2, M3, M2P):
            cp, wp, pp = self._fit(nome)
            self.assertIsNotNone(cp, nome)
            snap = inst(modelo=nome, cp=cp, wp=wp, pontos=tuple((int(t), p) for p, t in self.PONTOS))
            for k, (p, t) in enumerate(self.PONTOS):
                r = p0m.resolver_p0('Bike', int(t), [snap])
                self.assertTrue(r['valida'], (nome, t, r))
                self.assertEqual(r['valor_p0'], cp, (nome, t))          # exatamente o CP do ajuste
                self.assertNotAlmostEqual(r['valor_p0'], pp[k], places=3)   # e nao a previsao CP + W'/t

    def test_documentacao_da_regra_no_proprio_modulo(self):
        self.assertEqual(p0m.EQUACAO.split('[')[0].strip(), 'P0 = CP')
        self.assertEqual(p0m.FORMULA_ID, 'p0_igual_cp')
        self.assertGreaterEqual(p0m.VERSAO_P0, 2)                   # a v1 era CP + W'/d (rejeitada)


# ── 8, 9: Solver com P0 fixo ─────────────────────────────────────────────────────────────

def dados_solver(p0=290.0, n=520, semente=3, ruido=0.0):
    rng = np.random.default_rng(semente)
    carga = np.where(rng.random(n) < 0.6, rng.uniform(30, 120, n), 0.0)
    perf = ir.desempenho(carga, p0, 0.01, 0.03, 45.0, 8.0, 1)
    d0 = date(2025, 1, 6)
    dias = [(d0 + timedelta(days=i)).isoformat() for i in range(n)]
    cpd = {dias[i]: float(carga[i]) for i in range(n) if carga[i] > 0}
    obs = [{'test_id': f't{i}', 'data': dias[i], 'watts': float(perf[i] + (rng.normal(0, ruido) if ruido else 0))}
           for i in range(30, n, 9)]
    return obs, cpd


class SolverComP0Fixo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obs, cls.carga = dados_solver()

    def aj(self, p0_fixo, obs=None):
        return ir.ajustar_observacoes(obs or self.obs, self.carga, '2026-10-17', None, p0_fixo=p0_fixo)

    def test_08_p0_e_fixo_durante_o_ajuste_mesmo_quando_nao_serve_aos_dados(self):
        for fixo in (290.0, 250.0, 331.25):
            r = self.aj(fixo)
            self.assertEqual(r['parametros']['p0'], fixo)              # exatamente: o Solver nao o mexe
            self.assertEqual(r['p0_fixo'], fixo)
            self.assertEqual(r['p0_inicial']['valor'], fixo)
            self.assertEqual(r['p0_inicial']['origem'], 'p0_fixo_referencia_cp')
        # um P0 errado nao e "corrigido" pelo Solver: o ajuste fica pior
        self.assertGreater(self.aj(250.0)['metricas']['sse'], self.aj(290.0)['metricas']['sse'] + 1.0)

    def test_08b_o_ajuste_de_p0_fixo_nao_chama_nenhuma_inicializacao_de_p0(self):
        with mock.patch.object(ir, 'p0_inicial', side_effect=AssertionError('p0_inicial nao pode ser chamado')):
            r = self.aj(290.0)
        self.assertEqual(r['parametros']['p0'], 290.0)

    def test_09_k1_k2_t1_t2_continuam_estimados_pelo_solver(self):
        r = self.aj(290.0)
        p = r['parametros']
        self.assertEqual(r['identificabilidade']['parametros_livres'], ['k1', 'k2', 't1', 't2'])
        self.assertTrue(r['identificabilidade']['p0_fixo'])
        self.assertAlmostEqual(p['k1'], 0.01, delta=0.0005)
        self.assertAlmostEqual(p['k2'], 0.03, delta=0.0015)
        self.assertAlmostEqual(p['t1'], 45.0, delta=2.25)
        self.assertAlmostEqual(p['t2'], 8.0, delta=0.8)
        self.assertEqual(r['estado'], ir.EST_IDENTIFICAVEL)

    def test_09b_com_ruido_os_quatro_parametros_mexem_com_os_dados(self):
        o2, c2 = dados_solver(ruido=1.5, semente=8)
        a = ir.ajustar_observacoes(o2, c2, '2026-10-17', None, p0_fixo=290.0)['parametros']
        b = ir.ajustar_observacoes(o2[:-8], c2, '2026-10-17', None, p0_fixo=290.0)['parametros']
        self.assertEqual((a['p0'], b['p0']), (290.0, 290.0))
        self.assertTrue(any(abs(a[k] - b[k]) > 1e-9 for k in ('k1', 'k2', 't1', 't2')))

    def test_p0_fixo_invalido_e_rejeitado_e_nao_cai_para_p0_livre(self):
        for mau in (0, -1.0, float('nan'), float('inf'), True, 'x'):
            with self.assertRaises(ValueError, msg=str(mau)):
                self.aj(mau)

    def test_sem_p0_fixo_o_caminho_antigo_de_p0_livre_continua_igual(self):
        r = ir.ajustar_observacoes(self.obs, self.carga, '2026-10-17')
        self.assertFalse(r['identificabilidade']['p0_fixo'])
        self.assertEqual(r['identificabilidade']['parametros_livres'], ['p0', 'k1', 'k2', 't1', 't2'])
        self.assertAlmostEqual(r['parametros']['p0'], 290.0, delta=3.0)


# ── 10..14, 16: servico (base de dados temporaria) ──────────────────────────────────────

class _ServicoBase(S._Base):
    def refs(self, mod='Bike', dur=1200):
        return db.referencias_p0_historico(mod, dur)

    def cp(self, cp=270.0, wp=24000.0, **kw):
        return CPF.instantaneos_padrao() if (cp, wp) == (270.0, 24000.0) and not kw else [
            inst(cp=cp, wp=wp, **kw)]


class ReferenciaEHistorico(_ServicoBase):
    def test_a_referencia_guarda_toda_a_proveniencia(self):
        self.prepara()
        r = self.rec(instantaneos_cp=[inst(id=7, data_referencia='2025-03-04', data_gravacao='2025-03-05 09:00:00')])
        u = r['unidades']['Bike:1200']
        self.assertEqual(u['acao'], 'ajustado')
        (ref,) = self.refs()
        self.assertEqual((ref['modalidade'], ref['duracao_s'], ref['versao_referencia']), ('Bike', 1200, 1))
        self.assertAlmostEqual(ref['valor_p0'], 290.0, places=6)
        self.assertEqual(ref['modelo_cp'], M1)
        self.assertEqual(ref['formulacao'], p0m.FORMULA_ID)
        self.assertEqual((ref['cp_w'], ref['wp_j']), (290.0, 24000.0))
        self.assertEqual(ref['cp_data_referencia'], '2025-03-04')
        self.assertEqual(ref['cp_resultado_ref']['id'], 7)
        self.assertEqual(ref['conhecida_desde'][:19], AGORA.isoformat()[:19])
        self.assertIn('equacao', ref['parametros'])
        self.assertIn('pontos_usados', ref['parametros'])
        self.assertTrue(ref['assinatura'])
        self.assertEqual(ref['versao_modulo'], p0m.VERSAO_P0)
        # o ajuste fica ligado a essa referencia
        (l,) = [x for x in self.linhas() if x['duracao_s'] == 1200]
        self.assertEqual(l['entradas']['p0_ref']['versao_referencia'], 1)
        self.assertEqual(l['entradas']['p0_ref']['assinatura'], ref['assinatura'])
        self.assertEqual(l['p0_origem'], f'cp_model_confirmado:{M1}')
        self.assertEqual(l['p0'], ref['valor_p0'])
        json.dumps(ref)

    def test_10_novo_cp_cria_nova_referencia_preserva_a_anterior_e_reajusta(self):
        self.prepara()
        self.rec(instantaneos_cp=[inst(cp=270.0, wp=24000.0)])
        antes = copy.deepcopy(self.refs())
        agora2 = AGORA + timedelta(days=40)
        r = self.rec(agora=agora2, instantaneos_cp=[inst(id=2, cp=272.0, wp=24000.0, data_referencia='2026-01-01')])
        v1, v2 = self.refs()
        self.assertEqual(v1, antes[0])                                   # historico intacto, datas incluidas
        self.assertEqual(v2['versao_referencia'], 2)
        self.assertEqual(v2['valor_p0'], 272.0)
        self.assertEqual(v2['conhecida_desde'][:10], agora2.date().isoformat())
        self.assertEqual(v1['conhecida_desde'][:10], AGORA.date().isoformat())
        u = r['unidades']['Bike:1200']
        self.assertEqual((u['acao'], u['motivo']), ('ajustado', mis.MOTIVO_CP))
        l1, l2 = [x for x in self.linhas() if x['duracao_s'] == 1200]
        self.assertEqual((l1['versao_ajuste'], l2['versao_ajuste']), (1, 2))
        self.assertEqual(l1['p0'], 270.0)                              # o ajuste antigo nao foi tocado
        self.assertEqual(l2['p0'], 272.0)
        self.assertEqual(l2['entradas']['p0_ref']['versao_referencia'], 2)

    def test_10b_o_cp_mudar_so_gera_ajuste_quando_corre_o_fluxo_de_atualizacao(self):
        self.prepara()
        self.rec(instantaneos_cp=[inst()])
        # gravar outro resultado de CP nao toca no IR: nada corre sozinho
        n_ref, n_aj = len(self.refs()), len(self.linhas())
        _ = [inst(id=9, cp=300.0, wp=20000.0)]
        self.assertEqual((len(self.refs()), len(self.linhas())), (n_ref, n_aj))

    def test_11_repetir_sem_mudancas_nao_duplica_nem_versoes_nem_referencias(self):
        self.prepara()
        cp = [inst()]
        self.rec(instantaneos_cp=cp)
        n_ref, n_aj = len(db.referencias_p0_historico()), len(db.ajustes_ir_historico())
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=AssertionError('recalculou')):
            r = self.rec(agora=AGORA + timedelta(hours=3), instantaneos_cp=cp)
        self.assertEqual(r['n_ajustadas'], 0)
        self.assertEqual((len(db.referencias_p0_historico()), len(db.ajustes_ir_historico())), (n_ref, n_aj))
        self.assertEqual(r['unidades']['Bike:1200']['acao'], 'inalterado')

    def test_11b_gravacao_nova_so_com_wprime_diferente_nao_cria_referencia_nem_recalibra(self):
        self.prepara()
        self.rec(instantaneos_cp=[inst(id=1, wp=24000.0)])
        n, na = len(self.refs()), len(db.ajustes_ir_historico())
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=AssertionError('recalculou')):
            r = self.rec(agora=AGORA + timedelta(days=9),
                         instantaneos_cp=[inst(id=2, wp=19000.0, data_gravacao='2026-05-06 10:00:00')])
        self.assertEqual((len(self.refs()), len(db.ajustes_ir_historico())), (n, na))
        self.assertEqual(r['n_ajustadas'], 0)

    def test_11d_nova_data_de_referencia_com_o_mesmo_cp_cria_referencia_e_ajuste_novos(self):
        self.prepara()
        self.rec(instantaneos_cp=[inst(id=1, data_referencia='2025-01-01')])
        r = self.rec(agora=AGORA + timedelta(days=9),
                     instantaneos_cp=[inst(id=2, data_referencia='2026-05-05', data_gravacao='2026-05-06 10:00:00')])
        v1, v2 = self.refs()
        self.assertEqual((v1['valor_p0'], v2['valor_p0']), (CP, CP))
        self.assertEqual(v2['versao_referencia'], 2)
        self.assertEqual(r['unidades']['Bike:1200']['motivo'], mis.MOTIVO_REF_P0)
        self.assertEqual(len([x for x in self.linhas() if x['duracao_s'] == 1200]), 2)

    def test_11c_forcar_cria_ajuste_novo_mas_nao_referencia_nova(self):
        self.prepara()
        cp = [inst()]
        self.rec(instantaneos_cp=cp)
        r = self.rec(instantaneos_cp=cp, forcar=True)
        self.assertGreaterEqual(r['n_ajustadas'], 1)
        self.assertEqual(len(self.refs()), 1)
        self.assertEqual(r['unidades']['Bike:1200']['motivo'], mis.MOTIVO_FORCADO)

    def test_a_tabela_de_referencias_e_so_insert(self):
        src = open(os.path.join(ROOT, 'db.py'), encoding='utf-8').read()
        self.assertIsNone(re.search(r'(UPDATE|DELETE\s+FROM)\s+modelo_ir_p0_referencias', src, re.I))
        self.assertIsNone(re.search(r'DROP\s+TABLE[^\n]*modelo_ir_p0_referencias', src, re.I))
        self.assertEqual(len(re.findall(r'INSERT\s+INTO\s+modelo_ir_p0_referencias', src, re.I)), 1)
        with self.assertRaises(db.ConflitoReferenciaP0):
            self.prepara()
            self.rec(instantaneos_cp=[inst()])
            ref = dict(self.refs()[0])
            db.inserir_referencia_p0(ref)                                 # mesma versao

    def test_o_servico_nao_escreve_no_cp_model_nem_usa_inicializacoes_antigas(self):
        src = open(os.path.join(ROOT, 'modelo_ir_servico.py'), encoding='utf-8').read()
        corpo = src[src.index('def carregar_instantaneos_cp'):src.index('def garantir_referencia_p0')]
        self.assertRegex(corpo, r'SELECT')
        self.assertNotRegex(src + open(os.path.join(ROOT, 'modelo_ir_p0.py'), encoding='utf-8').read(),
                            r'(INSERT|UPDATE|DELETE)[^\n]*cp_resultados|upload\(')
        self.assertNotIn('ir.p0_inicial', src)                            # nenhuma inicializacao alternativa
        self.assertNotIn('mediana_dos_alvos', src)

    def test_rodar_com_p0_inicial_proibido_continua_a_ajustar(self):
        self.prepara()
        with mock.patch.object(ir, 'p0_inicial', side_effect=AssertionError('nao pode inicializar P0')):
            r = self.rec(instantaneos_cp=[inst()])
        self.assertEqual(r['unidades']['Bike:1200']['acao'], 'ajustado')


class SemP0Valido(_ServicoBase):
    def test_04_e_13_sem_p0_valido_nao_ha_ajuste_nem_referencia(self):
        self.prepara()
        r = self.rec(instantaneos_cp=[])
        self.assertTrue(r['ok'])
        self.assertEqual(r['n_ajustadas'], 0)
        self.assertGreaterEqual(r['n_bloqueadas'], 1)
        for k, u in r['unidades'].items():
            if u['acao'] != 'sem_observacoes':
                self.assertEqual(u['acao'], mis.ACAO_BLOQUEADO, k)
                self.assertEqual(u['motivo'], p0m.M_SEM_INSTANTANEO)
                self.assertTrue(u['detalhe'])
        self.assertEqual(db.ajustes_ir_historico(), [])
        self.assertEqual(db.referencias_p0_historico(), [])

    def test_a_unidade_operacional_unica_ajusta_com_p0_igual_ao_cp(self):
        self.prepara()
        r = self.rec(instantaneos_cp=CPF.instantaneos_padrao())
        for d in (mis.DURACAO_IR,):          # uma unica duracao operacional por modalidade (180 s; 1200 s no seam de teste)
            self.assertEqual(r['unidades'][f'Bike:{d}']['acao'], 'ajustado', d)
            self.assertEqual(db.ultima_referencia_p0('Bike', d)['valor_p0'], CP, d)
            self.assertEqual(db.ultimo_ajuste_ir('Bike', d)['p0'], CP, d)

    def test_14_o_ultimo_ajuste_valido_nao_e_destruido_quando_a_atualizacao_e_bloqueada(self):
        self.prepara()
        self.rec(instantaneos_cp=[inst()])
        antes = copy.deepcopy(db.ajustes_ir_historico())
        refs = copy.deepcopy(db.referencias_p0_historico())
        sem_gl = inst(); sem_gl['n_pts'], sem_gl['k_params'] = 2, 2
        cp_neg = inst(); cp_neg['cp_w'] = -3.0
        for cp_mau in ([], [inst(modelo=None)], [cp_neg], [sem_gl]):
            r = self.rec(agora=AGORA + timedelta(days=3), instantaneos_cp=cp_mau, forcar=True, duracao=1200)
            u = r['unidades']['Bike:1200']
            self.assertEqual(u['acao'], mis.ACAO_BLOQUEADO)
            self.assertEqual(u['ultimo_ajuste']['versao_ajuste'], 1)
            self.assertEqual(r['n_ajustadas'], 0)
        self.assertEqual(db.ajustes_ir_historico(), antes)
        self.assertEqual(db.referencias_p0_historico(), refs)
        self.assertEqual(db.ultimo_ajuste_ir('Bike', 1200)['versao_ajuste'], 1)

    def test_nao_ha_fallback_silencioso_para_outros_p0(self):
        self.prepara()
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=AssertionError('nao pode ajustar')), \
                mock.patch.object(ir, 'p0_inicial', side_effect=AssertionError('nao pode inicializar P0')):
            r = self.rec(instantaneos_cp=[])
        self.assertEqual(r['n_ajustadas'], 0)

    def test_falha_a_ler_o_cp_model_bloqueia_com_o_motivo_e_nao_levanta(self):
        self.prepara()
        falso = mock.MagicMock()
        falso.get_conn.side_effect = RuntimeError('drive indisponivel')
        with mock.patch.dict(sys.modules, {'drive_db_perfil': falso}):
            lista, erro = CARREGAR_REAL()
        self.assertEqual(lista, [])
        self.assertIn('drive indisponivel', erro)
        with mock.patch.object(mis, 'carregar_instantaneos_cp', return_value=([], 'RuntimeError: x')):
            r = mis.recalibrar(origem='teste', agora=AGORA)
        self.assertEqual(r['n_ajustadas'], 0)
        self.assertEqual(r['unidades']['Bike:1200']['erro_leitura_cp'], 'RuntimeError: x')
        self.assertEqual(db.ajustes_ir_historico(), [])

    def test_carregar_instantaneos_le_so_da_tabela_do_cp_sem_escrever(self):
        import sqlite3
        con = sqlite3.connect(':memory:')
        con.execute('CREATE TABLE cp_resultados (id INTEGER PRIMARY KEY, data_referencia TEXT, data_gravacao TEXT,'
                    ' modalidade TEXT, season TEXT, modelo_escolhido TEXT, cp_w REAL, wp_j REAL, see_pct REAL,'
                    ' n_pts INTEGER, k_params INTEGER, modelos_json TEXT, origem TEXT)')
        con.execute("INSERT INTO cp_resultados VALUES (1,'2025-01-01','2025-01-02','Bike','2025',?,270,24000,1,5,2,'{}','m')",
                    (M1,))
        con.execute("INSERT INTO cp_resultados VALUES (2,'2025-01-01','2025-01-02','Run','2025',?,NULL,NULL,1,5,2,'{}','m')",
                    (M1,))
        falso = mock.MagicMock()
        falso.get_conn.return_value = mock.MagicMock(execute=con.execute, close=lambda: None)
        with mock.patch.dict(sys.modules, {'drive_db_perfil': falso}):
            lista, erro = CARREGAR_REAL()
        self.assertIsNone(erro)
        self.assertEqual([r['modalidade'] for r in lista], ['Bike'])       # sem CP nao e instantaneo valido
        self.assertEqual(con.execute('SELECT COUNT(*) FROM cp_resultados').fetchone()[0], 2)
        falso.upload.assert_not_called()


class CausalidadeNoWalkForward(_ServicoBase):
    def val(self, refs, modos=('retrospectivo', 'conhecido_na_epoca')):
        return mis.validar_preditivo(db.observacoes_auto_historico(), db.revisoes_testes_modelo(),
                                     self.ativ, db.load_power_curves() or [], modos=modos,
                                     tipo='Bike', duracao=1200, referencias_p0=refs)

    def test_12_p0_conhecido_antes_so_usa_referencias_ja_conhecidas(self):
        refs = [CPF.referencia_p0('Bike', 1200, 290.0, '2025-06-01T10:00:00', 1),
                CPF.referencia_p0('Bike', 1200, 300.0, '2026-02-01T10:00:00', 2)]
        f = mis.p0_conhecido_antes
        self.assertIsNone(f(refs, '2025-05-31'))
        self.assertIsNone(f(refs, '2025-06-01'))                  # estritamente anterior
        self.assertEqual(f(refs, '2025-06-02'), 290.0)
        self.assertEqual(f(refs, '2026-02-01'), 290.0)
        self.assertEqual(f(refs, '2026-03-01'), 300.0)
        self.assertIsNone(f([], '2026-03-01'))
        self.assertIsNone(f(None, '2026-03-01'))

    def test_12b_referencia_so_futura_nao_prevê_nada_e_diz_porque(self):
        self.prepara()
        futuro = [CPF.referencia_p0('Bike', 1200, 290.0, '2099-01-01T00:00:00')]
        j = self.val(futuro)
        for it in j['itens']:
            self.assertEqual(it['n_alvos_com_previsao_ir'], 0, it['modo'])
        retro = next(i for i in j['itens'] if i['modo'] == 'retrospectivo')
        self.assertIn('sem_referencia_p0_conhecida', retro['alvos_sem_previsao_ir'])
        sem = self.val([])
        self.assertTrue(all(i['n_alvos_com_previsao_ir'] == 0 for i in sem['itens']))

    def test_12c_uma_referencia_posterior_nao_altera_previsoes_anteriores(self):
        self.prepara()
        base = self.val([CPF.referencia_p0('Bike', 1200, 290.0, '2020-01-01T00:00:00', 1)])
        item = next(i for i in base['itens'] if i['modo'] == 'retrospectivo')
        self.assertGreater(item['n_alvos_com_previsao_ir'], 5)
        datas = sorted(p['data'] for p in item['previsoes'])
        corte = datas[len(datas) // 2]
        antes = {p['test_id']: p['ir'] for p in item['previsoes'] if p['data'] < corte}
        nova = [CPF.referencia_p0('Bike', 1200, 290.0, '2020-01-01T00:00:00', 1),
                CPF.referencia_p0('Bike', 1200, 340.0, f'{corte}T08:00:00', 2)]       # conhecida so em `corte`
        dep = next(i for i in self.val(nova)['itens'] if i['modo'] == 'retrospectivo')
        depois = {p['test_id']: p['ir'] for p in dep['previsoes'] if p['data'] < corte}
        self.assertEqual(antes, depois)
        mudou = [p for p in dep['previsoes'] if p['data'] > corte]
        self.assertTrue(mudou)

    def test_a_data_do_instantaneo_do_cp_nao_serve_de_data_de_conhecimento(self):
        self.prepara()
        r = self.rec(instantaneos_cp=[inst(data_referencia='2000-01-01', data_gravacao='2000-01-02 00:00:00')])
        (ref,) = self.refs()
        self.assertEqual(ref['cp_data_referencia'], '2000-01-01')
        self.assertEqual(ref['conhecida_desde'][:10], AGORA.date().isoformat())   # conhecida quando o IR a gravou
        j = mis.validar_preditivo(db.observacoes_auto_historico(), db.revisoes_testes_modelo(), self.ativ,
                                  db.load_power_curves() or [], modos=('retrospectivo',), tipo='Bike', duracao=1200)
        it = j['itens'][0]
        self.assertEqual(it['n_alvos_com_previsao_ir'], 0)        # os alvos (2025) sao anteriores a 2026-10-17
        self.assertIn('sem_referencia_p0_conhecida', it['alvos_sem_previsao_ir'])

    def test_a_previsao_usa_p0_fixo_da_referencia_e_nao_um_p0_livre(self):
        self.prepara()
        visto = []
        orig = ir.ajustar_observacoes

        def espia(*a, **k):
            visto.append(k.get('p0_fixo'))
            return orig(*a, **k)
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=espia):
            self.val([CPF.referencia_p0('Bike', 1200, 277.5)], modos=('retrospectivo',))
        self.assertTrue(visto)
        self.assertTrue(all(v == 277.5 for v in visto))


class ContratosEUI(_ServicoBase):
    def test_15_a_escolha_do_modelo_no_cp_model_nao_foi_alterada(self):
        arq = ['utils/cp_model.py', 'tabs/tab_cp_model.py', 'drive_db_perfil.py', 'perfil_schema.py']
        # Base de comparacao = ancestral comum com origin/main (antes das alteracoes do IR). NAO o HEAD: depois do
        # commit o diff contra HEAD e vazio e o teste deixaria de provar o que afirma (e a asserção seguinte
        # falharia). Se a base nao existir (clone raso, sem origin/main) o teste e ignorado, nao aprovado.
        try:
            mb = subprocess.run(['git', '-C', ROOT, 'merge-base', 'HEAD', 'origin/main'],
                                capture_output=True, text=True, timeout=30)
            if mb.returncode != 0 or not mb.stdout.strip():
                self.skipTest('sem origin/main para determinar a base de comparacao')
            base = mb.stdout.strip()
            sub = subprocess.run(['git', '-C', ROOT, 'diff', '--stat', base, '--'] + arq,
                                 capture_output=True, text=True, timeout=30)
            api = subprocess.run(['git', '-C', ROOT, 'diff', '-U0', base, '--', 'api_cp.py'],
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:           # pragma: no cover
            self.skipTest(f'git indisponivel: {e}')
        if sub.returncode != 0:
            self.skipTest('nao e um repositorio git')
        self.assertEqual(sub.stdout.strip(), '', 'o CP Model foi alterado:\n' + sub.stdout)
        # api_cp.py: unica alteracao permitida = o gancho do IR depois da gravacao (calculo, escolha do modelo,
        # persistencia e duracoes ficam como estavam); a unica linha removida e o fecho do jsonify, reescrito
        # para acrescentar a chave `modelo_ir`. Sem alteracao (base == HEAD) tambem passa.
        linhas = [l for l in api.stdout.splitlines() if l[:1] in '+-' and not l.startswith(('+++', '---'))]
        removidas = [l for l in linhas if l.startswith('-')]
        self.assertLessEqual(set(removidas), {"-                            'escrito': escrito, 'drive': detalhe})"})
        for l in (x for x in linhas if x.startswith('+')):
            self.assertRegex(l, r"modelo_ir|_mis|ir_res|_e\b|try:|except|'escrito': escrito, 'drive': detalhe,|IR:")
        # e o resolvedor so le: o instantaneo passado nao e mutado
        snap = [inst(), inst(modalidade='Run', id=2)]
        copia = copy.deepcopy(snap)
        for m in ('Bike', 'Run', 'Row'):
            p0m.resolver_p0(m, 300, snap)
        self.assertEqual(snap, copia)

    def test_16_contratos_dos_endpoints_existentes_mantem_se(self):
        self.prepara()
        with CPF.patch_loader():
            r = self.c.post('/api/modelo/ir/recalibrar', json={})
        self.assertEqual(r.status_code, 200, r.get_json())
        j = r.get_json()
        for k in ('ok', 'unidades', 'n_ajustadas', 'n_inalteradas', 'carga', 'versao_modelo'):
            self.assertIn(k, j)
        self.assertIn('n_bloqueadas', j)                               # aditivo
        g = self.c.get('/api/modelo/ir').get_json()
        for k in ('experimental', 'tipo_analise', 'aviso', 'limitacoes', 'itens'):
            self.assertIn(k, g)
        for it in g['itens']:
            for k in ('modalidade', 'duracao_s', 'versao_ajuste', 'p0', 'k1', 'k2', 't1', 't2', 'estado', 'leitura'):
                self.assertIn(k, it)
        with mock.patch.object(mis, 'carregar_instantaneos_cp', return_value=(CPF.instantaneos_padrao(), None)):
            c = self.c.get('/api/modelo/ir/conjunto?duracao=1200').get_json()
        for k in ('status', 'estado', 'datas', 'modalidades', 'overall', 'escala', 'modalidades_com_curva'):
            self.assertIn(k, c)
        it = c['modalidades']['Bike']
        for k in ('estado', 'parametros', 'ajuste', 'pct', 'w', 'normalizacao', 'plausibilidade', 'cor'):
            self.assertIn(k, it)
        self.assertFalse(c['overall']['disponivel'])                   # Overall continua desativado

    def test_a_curva_conjunta_mostra_o_p0_usado_e_o_estado_atual_do_cp(self):
        self.prepara()
        self.rec(instantaneos_cp=CPF.instantaneos_padrao())
        c = mis.curvas_conjuntas(1200, atividades=self.ativ, instantaneos_cp=CPF.instantaneos_padrao())
        bike = c['modalidades']['Bike']['p0']
        self.assertEqual(bike['origem_ajuste'], 'cp_model_confirmado')
        self.assertAlmostEqual(bike['usado_no_ajuste']['valor_p0'], 290.0, places=6)
        self.assertEqual(bike['usado_no_ajuste']['modelo_cp'], M1)
        self.assertEqual(bike['usado_no_ajuste']['versao_referencia'], 1)
        self.assertTrue(bike['atual']['valida'])
        self.assertEqual(bike['atual']['valor_p0'], 290.0)
        sem = mis.curvas_conjuntas(1200, atividades=self.ativ, instantaneos_cp=[])['modalidades']['Bike']['p0']
        self.assertFalse(sem['atual']['valida'])
        self.assertEqual(sem['atual']['motivo'], p0m.M_SEM_INSTANTANEO)
        # sem consultar o CP, o estado atual nao e inventado
        c2 = mis.curvas_conjuntas(1200, atividades=self.ativ)
        self.assertIsNone(c2['modalidades']['Bike']['p0']['atual'])
        # ajuste antigo (P0 livre, anterior ao CP Model) e assinalado como tal
        self.assertEqual(mis._estado_p0('Bike', 1200, None, None)['origem_ajuste'], 'p0_livre_anterior_ao_cp_model')

    def test_a_configuracao_do_ajuste_regista_que_o_p0_e_fixo_e_vem_do_cp(self):
        self.prepara()
        self.rec(instantaneos_cp=[inst()])
        l = db.ultimo_ajuste_ir('Bike', 1200)
        self.assertEqual(l['configuracao']['p0']['modo'], 'igual_ao_cp_do_resultado_guardado')
        self.assertEqual(l['configuracao']['p0']['parametros_estimados'], ['k1', 'k2', 't1', 't2'])
        self.assertTrue(l['identificabilidade']['p0_fixo'])
        self.assertEqual(l['identificabilidade']['parametros_livres'], ['k1', 'k2', 't1', 't2'])

    def test_ajuste_antigo_de_p0_livre_nao_conta_como_atual(self):
        self.prepara()
        cfg_antigo = mis._cfg_efetiva()
        cfg_antigo.pop('p0')
        self.assertNotEqual(ir.assinatura(cfg_antigo), ir.assinatura(mis._cfg_efetiva()))


if __name__ == '__main__':
    unittest.main()
