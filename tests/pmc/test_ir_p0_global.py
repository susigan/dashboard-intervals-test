"""P0 GLOBAL do IR = MMP3 (180 s) do registo `YES` mais recente, partilhado por Bike, Row, Ski e Run.

    python -m pytest tests/pmc/test_ir_p0_global.py

Offline, dados SINTETICOS (testam o codigo, nao o atleta). Duracao operacional REAL (180 s) e regra REAL do P0
(sem o assento ir_duracao_legado). SQLite temporaria; calendario de seasons injetado; sem Intervals.icu nem producao.
"""
import copy
import inspect
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
for _p in (HERE, ROOT, os.path.join(ROOT, 'utils')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')
import numpy as np  # noqa: E402

import modelo_ir as ir  # noqa: E402
import modelo_ir_p0 as p0m  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_mmp3 as mm  # noqa: E402
import observacoes_auto as oa  # noqa: E402
import test_ir_mmp3 as M  # noqa: E402
import test_modelo_ir_servico as S  # noqa: E402

db = S.db
MODS = ('Bike', 'Row', 'Ski', 'Run')                       # ordem de modelo_testes.MODALIDADES
TIPO_ATIV = {'Bike': 'Ride', 'Row': 'Rowing', 'Run': 'Running', 'Ski': 'NordicSki'}
DOW = {'Bike': 3, 'Row': 4, 'Ski': 5, 'Run': 6}             # dia da semana do esforco de cada modalidade
VERDADE = dict(p0=290.0, k1=0.01, k2=0.03, t1=45.0, t2=8.0)
D0 = date(2025, 1, 6)
N_SEM = 64
FIM = D0 + timedelta(days=N_SEM * 7 - 1)
AGORA = datetime(2026, 10, 17, 12, 0, 0)
ELEG = lambda aid: 'elegivel'  # noqa: E731


def esf(mod, aid, dia, w, estado=mm.E_ELEGIVEL, hora=None, pr=False):
    """Esforco avaliado (formato de modelo_mmp3.avaliar_esforcos) escrito a mao, para testar a regra pura."""
    return {'test_id': f'{mod}:180:{aid}', 'data': dia, 'watts': float(w), 'modalidade': mod, 'activity_id': aid,
            'inicio': f'{dia}T{hora}' if hora else None, 'pr': pr, 'pct_do_melhor': None, 'estado': estado,
            'motivo': None}


# ═══ A. Regra pura do P0 global ═══════════════════════════════════════════════════════════════════

class RegraDoP0Global(unittest.TestCase):
    def g(self, **por_mod):
        return mm.p0_global({m: por_mod.get(m, []) for m in MODS})

    def test_1_um_unico_yes_gera_o_p0_esperado(self):
        g = self.g(Bike=[esf('Bike', 'a', '2026-03-01', 318)])
        self.assertEqual((g['estado'], g['registo']['watts'], g['n_yes']), ('valida', 318.0, 1))
        r = p0m.resolver_p0('Bike', 180, g)
        self.assertTrue(r['valida'])
        self.assertEqual((r['valor_p0'], r['origem'], r['formulacao']), (318.0, 'mmp3_ultimo_yes', 'p0_mmp3_ultimo_yes'))

    def test_2_yes_mais_recente_substitui_o_anterior_mesmo_com_mmp3_menor(self):
        g = self.g(Bike=[esf('Bike', 'a', '2026-03-01', 340), esf('Bike', 'b', '2026-05-01', 300)])
        self.assertEqual(g['registo']['watts'], 300.0)            # nao e o melhor historico
        self.assertEqual(g['registo']['activity_id'], 'b')

    def test_3_um_no_posterior_nao_substitui_o_ultimo_yes(self):
        g = self.g(Bike=[esf('Bike', 'a', '2026-03-01', 320),
                         esf('Bike', 'b', '2026-06-01', 500, estado=mm.E_NAO_ELEGIVEL),
                         esf('Bike', 'c', '2026-07-01', 100, estado=mm.E_INSUFICIENTE)])
        self.assertEqual(g['registo']['activity_id'], 'a')
        self.assertEqual(g['n_yes'], 1)

    def test_o_ultimo_yes_entre_modalidades_e_global_e_a_hora_decide_no_mesmo_dia(self):
        g = self.g(Bike=[esf('Bike', 'a', '2026-03-01', 320, hora='07:00:00')],
                   Row=[esf('Row', 'r', '2026-03-01', 250, hora='09:30:00')],
                   Run=[esf('Run', 'u', '2026-02-20', 400)])
        self.assertEqual((g['registo']['modalidade'], g['registo']['watts']), ('Row', 250.0))
        # sem hora conhecida desempata o activity_id (deterministico)
        g2 = self.g(Bike=[esf('Bike', 'a', '2026-03-01', 320)], Row=[esf('Row', 'r', '2026-03-01', 250)])
        self.assertEqual(g2['registo']['activity_id'], 'r')

    def test_4_o_mesmo_p0_e_a_mesma_assinatura_nas_quatro_modalidades(self):
        g = self.g(Bike=[esf('Bike', 'a', '2026-03-01', 320)], Ski=[esf('Ski', 's', '2026-03-05', 210)])
        rs = [p0m.resolver_p0(m, 180, g) for m in MODS]
        self.assertEqual({r['valor_p0'] for r in rs}, {210.0})
        self.assertEqual(len({r['assinatura'] for r in rs}), 1)
        self.assertEqual({r['registo']['test_id'] for r in rs}, {'Ski:180:s'})

    def test_5_sem_yes_nao_ha_p0_nem_fallback(self):
        for por_mod, estado in (({}, 'ausente'),
                                ({'Bike': [esf('Bike', 'a', '2026-03-01', 320, estado=mm.E_INSUFICIENTE)]}, 'dados_insuficientes'),
                                ({'Bike': [esf('Bike', 'a', '2026-03-01', 320, estado=mm.E_NAO_ELEGIVEL)]}, 'ausente')):
            g = self.g(**por_mod)
            self.assertEqual((g['estado'], g['registo']), (estado, None))
            r = p0m.resolver_p0('Bike', 180, g)
            self.assertFalse(r['valida'])
            self.assertIsNone(r['valor_p0'])
            self.assertEqual(r['motivo'], p0m.M_SEM_YES)
            self.assertNotIn('assinatura', r)

    def test_registo_yes_com_watts_invalidos_nao_gera_p0(self):
        for w in (0, -1, float('nan'), float('inf')):
            r = p0m.resolver_p0('Bike', 180, self.g(Bike=[esf('Bike', 'a', '2026-03-01', w)]))
            self.assertFalse(r['valida'], w)

    def test_a_assinatura_depende_do_registo_e_nao_da_modalidade_da_unidade(self):
        g1 = self.g(Bike=[esf('Bike', 'a', '2026-03-01', 320)])
        g2 = self.g(Bike=[esf('Bike', 'a', '2026-03-01', 320), esf('Bike', 'b', '2026-04-01', 320)])    # mesmo valor, novo registo
        a1, a2 = (p0m.resolver_p0('Bike', 180, g)['assinatura'] for g in (g1, g2))
        self.assertNotEqual(a1, a2)
        self.assertEqual(a1, p0m.resolver_p0('Run', 180, g1)['assinatura'])

    def test_o_p0_nunca_e_o_melhor_historico_nem_o_pr(self):
        g = self.g(Bike=[esf('Bike', 'a', '2026-03-01', 345, pr=True), esf('Bike', 'b', '2026-04-01', 331, pr=False)])
        self.assertEqual(g['registo']['watts'], 331.0)

    def test_modulos_do_p0_nao_tocam_no_cp(self):
        for mod in (p0m, mis):
            src = inspect.getsource(mod)
            for proibido in ('cp_resultados', 'drive_db_perfil', 'carregar_instantaneos_cp', 'instantaneo_em_vigor'):
                self.assertNotIn(proibido, src, f'{mod.__name__}: {proibido}')


# ═══ B. Elegibilidade (YES) do esforco de 180 s ═══════════════════════════════════════════════════

def serie(vals, mod='Bike', ano='2025', fim=None):
    """Um esforco de 180 s por mes (todos na mesma season/all-time), em ordem; ids a0, a1, ..."""
    out = []
    for i, w in enumerate(vals):
        out.append(M.c180(f'{mod}-a{i}', mod, f'{ano}-{(i % 12) + 1:02d}-10', w))
    return out


class ElegibilidadeYes(unittest.TestCase):
    def estado(self, cv, aid, mod='Bike', ate='2026-12-31', eleg=ELEG):
        return next(e for e in mm.avaliar_esforcos(cv, mod, ate, (), eleg, tags=M.TAGS) if e['activity_id'] == aid)

    def test_6_usa_a_observacao_de_180_s_e_a_proximidade_existente(self):
        import protocolo
        self.assertEqual(mm.LIMIAR, protocolo.LIMIAR_ESFORCO)          # a regra existente, sem valor novo
        base = [300.0] * 5
        yes = self.estado(serie(base + [276.0]), 'Bike-a5')            # 276/300 = 0,92 (fronteira inclusiva)
        no = self.estado(serie(base + [275.0]), 'Bike-a5')             # 275/300 < 0,92
        self.assertEqual((yes['estado'], no['estado']), (mm.E_ELEGIVEL, mm.E_NAO_ELEGIVEL))
        self.assertEqual(no['motivo'], mm.M_ABAIXO)
        # so os 180 s contam: 300 s e 1200 s altos na mesma curva nao tornam um 180 s fraco elegivel
        cv = serie(base)
        cv.append(M.c180('Bike-x', 'Bike', '2026-02-10', 200.0, {300: 900.0, 1200: 800.0}))
        x = self.estado(cv, 'Bike-x')
        self.assertEqual((x['estado'], x['watts']), (mm.E_NAO_ELEGIVEL, 200.0))

    def test_7_dados_insuficientes_e_atividades_ignoradas_nao_sao_elegiveis(self):
        curta = serie([300.0, 300.0, 300.0, 300.0])                    # < 5 pontos: amostra minima existente
        e = self.estado(curta, 'Bike-a3')
        self.assertEqual((e['estado'], e['motivo']), (mm.E_INSUFICIENTE, mm.M_AMOSTRA))
        cv = serie([300.0] * 6)
        ig = self.estado(cv, 'Bike-a5', eleg=lambda a: 'inelegivel')   # icu_ignore_power
        self.assertEqual((ig['estado'], ig['motivo']), (mm.E_NAO_ELEGIVEL, mm.M_IGNORE_POWER))
        desc = self.estado(cv, 'Bike-a5', eleg=lambda a: 'desconhecida')
        self.assertEqual(desc['estado'], mm.E_INSUFICIENTE)
        for eleg in (lambda a: 'inelegivel', lambda a: 'desconhecida'):
            g = mm.p0_global({'Bike': mm.avaliar_esforcos(cv, 'Bike', '2026-12-31', (), eleg, tags=M.TAGS)})
            self.assertIsNone(g['registo'])

    def test_8_um_teste_elegivel_nao_precisa_de_ser_pr(self):
        cv = serie([300.0, 310.0, 320.0, 340.0, 330.0, 335.0])         # a5 (335) < PR 340 e elegivel
        e = self.estado(cv, 'Bike-a5')
        self.assertEqual((e['estado'], e['pr']), (mm.E_ELEGIVEL, False))
        g = mm.p0_global({'Bike': mm.avaliar_esforcos(cv, 'Bike', '2026-12-31', (), ELEG, tags=M.TAGS)})
        self.assertEqual(g['registo']['watts'], 335.0)                 # P0 = ultimo YES, nao o PR de 340

    def test_icu_ignore_power_nao_alimenta_as_referencias(self):
        cv = serie([300.0] * 5 + [276.0])
        ref = mm.referencias(cv, 'Bike', '2026-12-31', ignorar_ids={'Bike-a0'})
        self.assertEqual(ref['mmp3_pr_atual']['watts'], 300.0)


# ═══ C. Servico: referencia global, Solver, versoes, isolamento (180 s real, SQLite temporaria) ═════

def gerar(mod, n_semanas=N_SEM, semente=5, extra=0.6, pre=''):
    """(curvas, atividades) da modalidade: um esforco de 180 s por semana (= previsao do IR com a carga real) e sessoes
    so de carga nos outros dias; ids unicos entre modalidades."""
    rng = np.random.default_rng(semente)
    n = n_semanas * 7
    carga = np.where(rng.random(n) < extra, rng.uniform(30, 120, n), 0.0)
    for s in range(n_semanas):
        carga[s * 7 + DOW[mod] - 3] = max(carga[s * 7 + DOW[mod] - 3], 60.0)
    v = VERDADE
    perf = ir.desempenho(carga, v['p0'], v['k1'], v['k2'], v['t1'], v['t2'], 1)
    cvs, ativ = [], []
    for i in range(n):
        dia = (D0 + timedelta(days=i)).isoformat()
        teste = (i % 7 == DOW[mod] - 3)
        if carga[i] <= 0 and not teste:
            continue
        aid = f'{pre}{mod}-{i}'
        ativ.append({'id': aid, 'type': TIPO_ATIV[mod], 'start_date_local': dia + f'T0{DOW[mod]}:00:00',
                     'icu_training_load': float(carga[i]), 'icu_ignore_power': False,
                     'tags': ['3min'] if teste else []})
        if teste:
            cvs.append(M.c180(aid, mod, dia, float(perf[i])))
    return cvs, ativ


def quatro_modalidades():
    cv, at = [], []
    for k, m in enumerate(MODS):
        c, a = gerar(m, semente=5 + k)
        cv += c
        at += a
    return cv, at


def S_tags(ativ):
    return {a['id']: a.get('tags') or [] for a in ativ}


class _Base4(S._Base):
    def setUp(self):
        p = mock.patch.object(S, 'cenario', side_effect=lambda *a, **k: quatro_modalidades())
        p.start()
        self.addCleanup(p.stop)
        o = mock.patch.object(oa, 'obter_marcos', return_value=(None, oa.FONTE_SEASON_MES))
        o.start()
        self.addCleanup(o.stop)
        super().setUp()

    def adiciona(self, mod, aid, dia, w, hora='12:00:00', carga=40.0):
        """Acrescenta um esforco de 180 s (e a sua atividade) e sincroniza as curvas."""
        self.curvs.append(M.c180(aid, mod, dia, w))
        self.ativ.append({'id': aid, 'type': TIPO_ATIV[mod], 'start_date_local': f'{dia}T{hora}',
                          'icu_training_load': carga, 'icu_ignore_power': False,
                          'tags': ['3min']})
        self.sync()

    def esforcos(self, ate='2030-01-01'):
        return {m: mm.avaliar_esforcos([c for c in self.curvs if c['type'] == m], m, ate, (), ELEG,
                                       horas={a['id']: a['start_date_local'] for a in self.ativ},
                                       tags=S_tags(self.ativ)) for m in MODS}

    def oraculo(self, ate='2030-01-01'):
        """Ultimo YES por (data, hora, id): calculado a mao, independente de p0_global."""
        yes = [e for lst in self.esforcos(ate).values() for e in lst if e['estado'] == mm.E_ELEGIVEL]
        return max(yes, key=lambda e: (e['data'], (e['inicio'] or '')[11:19], str(e['activity_id'])))

    def linhas(self, mod='Bike'):
        return db.ajustes_ir_historico(mod, 180)

    def refs(self, mod='Bike'):
        return db.referencias_p0_historico(mod, 180)

    def ultimo_dia(self):
        return FIM + timedelta(days=30)


class P0GlobalNoServico(_Base4):
    def test_4_a_mesma_referencia_p0_nas_quatro_modalidades_e_registo_de_origem(self):
        orac = self.oraculo(AGORA.date().isoformat())
        r = self.rec()
        self.assertEqual(sorted(r['unidades']), sorted(f'{m}:180' for m in MODS))
        self.assertEqual({u['acao'] for u in r['unidades'].values()}, {'ajustado'})
        self.assertEqual(r['p0_global']['estado'], 'valida')
        for m in MODS:
            (ref,) = self.refs(m)
            self.assertEqual(ref['valor_p0'], orac['watts'], m)
            self.assertEqual(ref['formulacao'], 'p0_mmp3_ultimo_yes')
            self.assertEqual(ref['modelo_cp'], 'mmp3_ultimo_yes')        # proveniencia, nao CP
            self.assertEqual(ref['cp_resultado_ref']['test_id'], orac['test_id'])
            self.assertEqual(ref['parametros']['registo']['activity_id'], orac['activity_id'])
            self.assertEqual(ref['cp_data_referencia'], orac['data'])
            (l,) = self.linhas(m)
            self.assertEqual((l['p0'], l['p0_inicial']), (orac['watts'], orac['watts']), m)
            self.assertEqual(l['p0_origem'], f"mmp3_ultimo_yes:{orac['test_id']}")
        self.assertEqual(len({self.refs(m)[0]['assinatura'] for m in MODS}), 1)

    def test_solver_so_estima_k1_k2_t1_t2_com_p0_fixo(self):
        orac = self.oraculo(AGORA.date().isoformat())
        chamadas = []
        real = ir.ajustar_observacoes

        def espia(*a, **k):
            chamadas.append(k.get('p0_fixo'))
            return real(*a, **k)
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=espia):
            self.rec()
        self.assertEqual(chamadas, [orac['watts']] * 4)
        for m in MODS:
            (l,) = self.linhas(m)
            self.assertEqual(l['identificabilidade']['parametros_livres'], ['k1', 'k2', 't1', 't2'])
            self.assertEqual(l['configuracao']['p0']['modo'], 'mmp3_ultimo_yes_global')

    def test_5_sem_yes_nao_ha_fallback_para_cp_nem_ftp_nem_outra_duracao(self):
        self.curvs[:] = [c for c in self.curvs if c['type'] == 'Bike'][:3]       # < 5 pontos: nenhum YES
        db._exec('DELETE FROM power_curves')
        self.sync()
        with mock.patch('drive_db_perfil.get_conn', side_effect=AssertionError('nao devia ler o CP')):
            r = self.rec()
        self.assertEqual(r['p0_global']['estado'], 'dados_insuficientes')
        for u in r['unidades'].values():
            self.assertIn(u['acao'], ('sem_observacoes', mis.ACAO_BLOQUEADO))
        for m in MODS:
            self.assertEqual((self.refs(m), self.linhas(m)), ([], []))
        j = self.c.get('/api/modelo/ir/conjunto').get_json()
        self.assertEqual(j['p0_global']['estado'], 'dados_insuficientes')
        self.assertIsNone(j['p0_global']['registo'])
        self.assertEqual(j['estado'], 'sem_curvas')
        for m in MODS:
            self.assertIsNone(j['modalidades'][m]['parametros'])
            self.assertFalse(j['modalidades'][m]['tem_curva'])
            self.assertEqual(j['modalidades'][m]['p0']['atual']['valida'], False)

    def test_5b_sem_nenhuma_curva_o_estado_e_ausente(self):
        self.curvs[:] = []
        db._exec('DELETE FROM power_curves')
        r = self.rec()
        self.assertEqual(r['p0_global']['estado'], 'ausente')
        j = self.c.get('/api/modelo/ir/conjunto').get_json()
        self.assertEqual(j['p0_global']['estado'], 'ausente')

    def test_9_yes_mais_recente_muda_o_p0_e_recalibra_as_quatro_sem_duplicar_depois(self):
        self.rec()
        v1 = {m: copy.deepcopy(self.linhas(m)[0]) for m in MODS}
        ref1 = self.refs('Bike')[0]['valor_p0']
        novo = round(ref1 * 1.03, 3)                       # perto do melhor: YES; valor diferente
        self.adiciona('Row', 'Row-novo', (FIM + timedelta(days=5)).isoformat(), novo)
        self.assertEqual(self.oraculo()['activity_id'], 'Row-novo')
        chamadas = []
        real = ir.ajustar_observacoes
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=lambda *a, **k: (chamadas.append(k['p0_fixo']), real(*a, **k))[1]):
            r = self.rec(agora=AGORA + timedelta(days=1))
        self.assertEqual(chamadas, [novo] * 4)                                # o Solver correu nas 4, com o P0 novo
        self.assertEqual({u['acao'] for u in r['unidades'].values()}, {'ajustado'})
        for m in MODS:
            self.assertEqual([x['valor_p0'] for x in self.refs(m)], [ref1, novo], m)
            self.assertEqual([x['versao_referencia'] for x in self.refs(m)], [1, 2], m)
            self.assertEqual([x['versao_ajuste'] for x in self.linhas(m)], [1, 2], m)
            self.assertEqual(self.linhas(m)[0], v1[m], m)                        # historico intacto
            self.assertEqual(self.linhas(m)[1]['p0'], novo, m)
        self.assertEqual(r['unidades']['Bike:180']['motivo'], mis.MOTIVO_CP)
        # P0 igual e nada mais mudou: nenhuma versao nova nem chamada ao Solver
        n = {m: (len(self.refs(m)), len(self.linhas(m))) for m in MODS}
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=AssertionError('recalibrou sem motivo')):
            r2 = self.rec(agora=AGORA + timedelta(days=2))
        self.assertEqual({u['acao'] for u in r2['unidades'].values()}, {'inalterado'})
        self.assertEqual({m: (len(self.refs(m)), len(self.linhas(m))) for m in MODS}, n)

    def test_3_um_no_posterior_nao_muda_o_p0_nem_recalibra(self):
        self.rec()
        p0 = self.refs('Bike')[0]['valor_p0']
        self.adiciona('Bike', 'Bike-fraco', (FIM + timedelta(days=5)).isoformat(), 120.0)     # muito abaixo: NO
        e = next(x for x in self.esforcos()['Bike'] if x['activity_id'] == 'Bike-fraco')
        self.assertEqual(e['estado'], mm.E_NAO_ELEGIVEL)
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=AssertionError('recalibrou')):
            r = self.rec(agora=AGORA + timedelta(days=1))
        self.assertEqual(r['p0_global']['registo']['watts'], p0)
        self.assertEqual({u['acao'] for u in r['unidades'].values()}, {'inalterado'})
        self.assertEqual(len(self.refs('Bike')), 1)

    def test_10_falha_do_solver_numa_modalidade_preserva_o_ultimo_ajuste_e_nao_afeta_as_outras(self):
        self.rec()
        v1 = {m: copy.deepcopy(self.linhas(m)[0]) for m in MODS}
        novo = round(self.refs('Bike')[0]['valor_p0'] * 1.03, 3)
        self.adiciona('Bike', 'Bike-novo', (FIM + timedelta(days=5)).isoformat(), novo)
        real = ir.ajustar_observacoes
        ordem = []

        def falha_em_row(obs, cargas, referencia, cfg, p0_fixo=None):
            ordem.append(len(ordem))
            if ordem[-1] == 1:                                     # 2.a unidade = Row (ordem de modelo_testes.MODALIDADES)
                raise RuntimeError('solver rebentou')
            return real(obs, cargas, referencia, cfg, p0_fixo=p0_fixo)
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=falha_em_row):
            r = self.rec(agora=AGORA + timedelta(days=1))
        self.assertEqual(r['unidades']['Row:180']['acao'], mis.ACAO_FALHOU)
        for m in ('Bike', 'Ski', 'Run'):
            self.assertEqual(r['unidades'][f'{m}:180']['acao'], 'ajustado', m)    # as outras nao sao corrompidas
            self.assertEqual(self.linhas(m)[-1]['p0'], novo, m)
        self.assertEqual(self.linhas('Row')[0], v1['Row'])                          # o ultimo ajuste valido fica intacto
        j = self.c.get('/api/modelo/ir/conjunto').get_json()['modalidades']
        self.assertEqual(j['Row']['estado_ir'], mis.EI_ERRO_SOLVER)
        self.assertTrue(j['Row']['ajuste_apresentado_e_antigo'])
        self.assertEqual(j['Row']['parametros']['p0'], v1['Row']['p0'])              # parametros antigos, P0 antigo
        self.assertEqual(j['Row']['p0']['atual']['valor_p0'], novo)                  # nunca apresentado como recalibrado
        self.assertTrue(j['Row']['p0']['desatualizado'])
        self.assertIn('solver rebentou', j['Row']['excecao_solver'])
        self.assertIn(j['Bike']['estado_ir'], (mis.EI_VALIDO, mis.EI_NAO_IDENT))
        self.assertFalse(j['Bike']['p0']['desatualizado'])

    def test_ajuste_com_p0_antigo_e_marcado_desatualizado_mesmo_sem_nova_recalibracao(self):
        self.rec()
        novo = round(self.refs('Bike')[0]['valor_p0'] * 1.03, 3)
        self.adiciona('Ski', 'Ski-novo', (FIM + timedelta(days=5)).isoformat(), novo)      # P0 muda; ninguem recalibrou
        j = self.c.get('/api/modelo/ir/conjunto').get_json()
        for m in MODS:
            it = j['modalidades'][m]
            self.assertEqual(it['estado_ir'], mis.EI_DESATUALIZADO, m)
            self.assertEqual(it['p0']['atual']['valor_p0'], novo)
        self.assertEqual(j['p0_global']['registo']['activity_id'], 'Ski-novo')

    def test_11_recalibracao_numa_data_passada_nao_usa_dados_futuros(self):
        corte = D0 + timedelta(days=300)
        orac = self.oraculo(corte.isoformat())
        self.rec(agora=datetime(corte.year, corte.month, corte.day, 12))
        for m in MODS:
            (ref,) = self.refs(m)
            self.assertEqual(ref['valor_p0'], orac['watts'], m)
            self.assertLessEqual(ref['cp_data_referencia'], corte.isoformat(), m)
            (l,) = self.linhas(m)
            self.assertLessEqual(max(o['data'] for o in l['entradas']['observacoes']), corte.isoformat(), m)
        # o que existe depois do corte so entra numa recalibracao posterior, e a referencia antiga nao e reescrita
        antigo = copy.deepcopy(self.refs('Bike')[0])
        self.rec(agora=AGORA)
        self.assertEqual(self.refs('Bike')[0], antigo)
        self.assertEqual(len(self.refs('Bike')), 2)
        self.assertGreater(self.refs('Bike')[1]['cp_data_referencia'], antigo['cp_data_referencia'])

    def test_11b_dados_futuros_nao_alteram_o_p0_global_de_uma_data_passada(self):
        corte = (D0 + timedelta(days=300)).isoformat()
        com_futuro = mm.p0_global({m: v for m, v in self.esforcos(corte).items()})
        so_passado = mm.p0_global({m: mm.avaliar_esforcos([c for c in self.curvs if c['type'] == m and c['date'] <= corte],
                                                          m, corte, (), ELEG,
                                                          horas={a['id']: a['start_date_local'] for a in self.ativ},
                                                          tags=S_tags(self.ativ))
                                   for m in MODS})
        self.assertEqual(com_futuro, so_passado)

    def test_12_a_logica_antiga_de_60_300_1200_nao_influencia_o_novo_ir(self):
        # curvas com 300 s e 1200 s altos + historico de observacoes automaticas antigas (60/300/1200)
        for c in self.curvs:
            if c['type'] == 'Bike':
                c['secs'] += [300, 1200]
                c['watts'] += [900.0, 800.0]
        self.sync()
        antes = self.oraculo()
        self.prepara()                                          # corre a deteccao antiga (60/300/1200) e grava o historico
        with mock.patch.object(mis, 'validar_preditivo', side_effect=AssertionError('legado a alimentar o IR')), \
                mock.patch.object(mis, 'selecionar', side_effect=AssertionError('selecao antiga a alimentar o IR')):
            r = self.rec()
        self.assertEqual(sorted(r['unidades']), sorted(f'{m}:180' for m in MODS))
        self.assertEqual(r['p0_global']['registo']['watts'], antes['watts'])
        for m in MODS:
            self.assertNotEqual(self.refs(m)[0]['valor_p0'], 900.0)
            for d in (60, 300, 1200):
                self.assertEqual(db.ajustes_ir_historico(m, d), [], (m, d))
            obs = self.linhas(m)[0]['entradas']['observacoes']
            self.assertTrue(all(o['test_id'].startswith(f'{m}:180:') for o in obs))

    def test_cp_gravado_nao_aciona_nem_altera_o_ir(self):
        self.rec()
        n = {m: (len(self.refs(m)), len(self.linhas(m))) for m in MODS}
        r = mis.apos_cp_gravado({'escrito': {'cp': 1}}, 'Bike')
        self.assertEqual(r, {'ok': False, 'ignorado': 'p0_do_ir_nao_depende_do_cp'})
        self.assertEqual({m: (len(self.refs(m)), len(self.linhas(m))) for m in MODS}, n)


class InterfaceEEndpoints(_Base4):
    def test_o_conjunto_traz_o_p0_global_a_origem_o_registo_e_o_estado(self):
        self.rec()
        orac = self.oraculo(AGORA.date().isoformat())
        j = self.c.get('/api/modelo/ir/conjunto').get_json()
        g = j['p0_global']
        self.assertEqual((g['estado'], g['origem'], g['registo']['watts'], g['registo']['test_id']),
                         ('valida', 'mmp3_ultimo_yes', orac['watts'], orac['test_id']))
        self.assertIsNotNone(g['registo']['inicio'])
        for m in MODS:
            it = j['modalidades'][m]
            self.assertEqual(it['p0']['atual']['valor_p0'], orac['watts'])
            self.assertEqual(it['p0']['origem_ajuste'], 'mmp3_ultimo_yes')
            self.assertFalse(it['p0']['desatualizado'])
            self.assertEqual(it['mmp3']['duracao_s'], 180)
            self.assertNotIn('cp_gravado', it['mmp3'].get('cp_model', {}))

    def test_endpoints_existentes_continuam_a_responder(self):
        self.rec()
        self.assertEqual(self.c.get('/api/modelo/ir').status_code, 200)
        self.assertEqual(self.c.get('/api/modelo/ir/influencia?tipo=Bike').status_code, 200)
        self.assertEqual(self.c.get('/api/modelo/ir/conjunto').status_code, 200)
        self.assertEqual(self.c.get('/api/modelo/ir/conjunto?duracao=300').status_code, 400)
        self.assertEqual(self.c.post('/api/modelo/ir/recalibrar', json={'duracao_s': 180}).status_code, 200)
        self.assertEqual(self.c.post('/api/modelo/ir/recalibrar', json={'duracao_s': 300}).status_code, 409)

    def test_validacao_preditiva_antiga_vem_marcada_como_legado(self):
        with mock.patch.object(mis, 'validar_preditivo', return_value={'itens': []}):
            r = self.c.get('/api/modelo/ir/validacao?tipo=Bike&duracao=1200')
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d['legado'])
        self.assertIn('nao valida o IR baseado em MMP3', d['aviso_legado'])

    def test_a_consulta_nao_grava_nada(self):
        self.rec()
        tabelas = [r[0] for r in db._exec("SELECT name FROM sqlite_master WHERE type='table'", fetch='all')]

        def foto():
            return {t: sorted(map(repr, db._exec(f'SELECT * FROM "{t}"', fetch='all') or []))
                    for t in tabelas if t != 'sqlite_sequence'}
        antes = foto()
        self.c.get('/api/modelo/ir/conjunto')
        self.c.get('/api/modelo/ir/influencia?tipo=Bike')
        self.assertEqual(foto(), antes)


class HistoricoDeReferencias(unittest.TestCase):
    def test_p0_conhecido_antes_e_causal(self):
        refs = [{'versao_referencia': 1, 'valor_p0': 300.0, 'conhecida_desde': '2026-03-01T10:00:00'},
                {'versao_referencia': 2, 'valor_p0': 310.0, 'conhecida_desde': '2026-06-01T10:00:00'}]
        f = mis.p0_conhecido_antes
        self.assertIsNone(f(refs, '2026-03-01'))
        self.assertEqual(f(refs, '2026-03-02'), 300.0)
        self.assertEqual(f(refs, '2026-06-01'), 300.0)
        self.assertEqual(f(refs, '2026-06-02'), 310.0)


if __name__ == '__main__':
    unittest.main()
