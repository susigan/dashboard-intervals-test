"""IR orientado por MMP3 (180 s) e CP confirmado: referencias, elegibilidade do teste, entrada no CP Model, P0 e Solver.

    python -m pytest tests/pmc/test_ir_mmp3.py

Offline, dados SINTETICOS (testam o codigo, nao o atleta nem o modelo). Sem Intervals.icu e sem producao: SQLite
temporaria, leitor do CP Model substituido por instantaneos sinteticos, calendario de seasons injetado.
Aqui a duracao operacional do IR e a REAL (180 s); os testes do motor generico (test_modelo_ir_*) usam 1200 s.
"""
import copy
import json
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
import modelo_ir_servico as mis  # noqa: E402
import modelo_mmp3 as mm  # noqa: E402
import observacoes_auto as oa  # noqa: E402
import test_modelo_ir_servico as S  # noqa: E402

db = S.db
MODS = ('Bike', 'Row', 'Run', 'Ski')
ELEG = lambda aid: 'elegivel'  # noqa: E731


class _Tag3min(dict):
    """Todas as atividades trazem a tag de 3 min (o teste declarado); sobrepoe-se onde o teste a contraria."""
    def get(self, k, d=None):
        return super().get(k, ['3min'])


TAGS = _Tag3min()


def c180(aid, mod, dia, w, extra=None):
    """Curva de uma atividade com o MMP3 (180 s) e, opcionalmente, outras duracoes {secs: watts}."""
    pts = {180: w, **(extra or {})}
    secs = sorted(pts)
    return {'activity_id': aid, 'type': mod, 'date': dia, 'weight': 70.0, 'secs': secs, 'watts': [pts[s] for s in secs]}


# 2025: maximo all-time 340 W (junho); 2026 comeca mais baixo. Uma serie com >= 5 pontos validos (regra existente).
SERIE = [('a1', '2025-03-01', 300.0), ('a2', '2025-04-01', 310.0), ('a3', '2025-05-01', 320.0),
         ('a4', '2025-06-01', 340.0), ('a5', '2025-07-01', 330.0), ('a6', '2025-10-01', 335.0),
         ('b1', '2026-02-01', 250.0),      # 73,5 % do melhor: nao elegivel
         ('b2', '2026-04-01', 330.0),      # 97 %: elegivel SEM PR
         ('b3', '2026-05-01', 345.0),      # novo PR (elegivel)
         ('b4', '2026-06-01', 200.0)]      # muito abaixo


def curvas(mod='Bike', serie=SERIE):
    return [c180(f'{mod}-{a}', mod, d, w) for a, d, w in serie]


def ctx(cv, mod='Bike', ate='2026-06-15', **kw):
    return mm.contexto(cv, mod, ate, (), kw.pop('eleg', ELEG), kw.pop('marcos', None), tags=kw.pop('tags', TAGS), **kw)


# ═══ A. Referencias e estado do teste (modulo puro) ═══════════════════════════════════════════════

class Referencias(unittest.TestCase):
    def test_1_inicial_congelada_durante_a_season(self):
        cv = curvas()
        a = mm.referencias(cv, 'Bike', '2026-02-15')
        b = mm.referencias(cv, 'Bike', '2026-09-30')                 # depois de dois PR da season
        self.assertEqual(a['mmp3_inicial_temporada'], b['mmp3_inicial_temporada'])
        self.assertEqual((a['mmp3_inicial_temporada']['watts'], a['mmp3_inicial_temporada']['data']), (340.0, '2025-06-01'))
        self.assertEqual(a['inicio_season'], '2026-01-01')

    def test_2_pr_posterior_nao_altera_a_inicial_e_o_pr_evolui(self):
        cv = curvas()
        antes = mm.referencias(cv, 'Bike', '2026-04-15')            # b2 (330) nao e PR
        depois = mm.referencias(cv, 'Bike', '2026-05-15')           # b3 (345) e PR
        self.assertEqual(antes['mmp3_pr_atual']['watts'], 340.0)
        self.assertEqual(depois['mmp3_pr_atual']['watts'], 345.0)
        self.assertTrue(depois['pr_atual_na_season_atual'])
        self.assertFalse(antes['pr_atual_na_season_atual'])
        self.assertEqual(antes['mmp3_inicial_temporada'], depois['mmp3_inicial_temporada'])
        self.assertEqual(depois['mmp3_inicial_temporada']['watts'], 340.0)

    def test_inicial_indisponivel_sem_dados_anteriores_nao_se_inventa(self):
        so_2026 = curvas(serie=[s for s in SERIE if s[1] >= '2026'])
        r = mm.referencias(so_2026, 'Bike', '2026-06-15')
        self.assertIsNone(r['mmp3_inicial_temporada'])
        self.assertEqual(r['mmp3_inicial_indisponivel'], 'sem_dados_validos_anteriores_ao_inicio_da_season')
        self.assertEqual(r['mmp3_pr_atual']['watts'], 345.0)         # o PR existe mesmo sem inicial

    def test_marcos_de_season_mandam_no_inicio(self):
        marcos = [('2025-11-01', 'S26'), ('2026-04-15', 'S26b')]
        self.assertEqual(mm.inicio_season('2026-05-01', marcos), '2026-04-15')
        r = mm.referencias(curvas(), 'Bike', '2026-05-20', marcos=marcos)
        self.assertEqual(r['inicio_season'], '2026-04-15')
        self.assertEqual(r['mmp3_inicial_temporada']['watts'], 340.0)  # melhor ANTES de 2026-04-15 (b2 de 01-04 inclusive)
        self.assertEqual(mm.inicio_season('2026-06-01', None), '2026-01-01')       # sem marcos: SEASON_INICIO_MES

    def test_atividade_com_icu_ignore_power_nao_entra_nas_referencias(self):
        cv = curvas()
        r = mm.referencias(cv, 'Bike', '2026-06-15', ignorar_ids={'Bike-b3'})
        self.assertEqual(r['mmp3_pr_atual']['watts'], 340.0)


class EstadoDoTeste(unittest.TestCase):
    def esforcos(self, cv=None, mod='Bike', ate='2026-06-15', **kw):
        return mm.avaliar_esforcos(cv or curvas(mod), mod, ate, (), kw.pop('eleg', ELEG), tags=kw.pop('tags', TAGS), **kw)

    def por_dia(self, esf, dia, pr=None):
        return mm.estado_no_dia(esf, dia, pr)

    def test_3_esforco_valido_de_180_s_e_elegivel(self):
        e = self.por_dia(self.esforcos(), '2026-04-01')
        self.assertEqual((e['estado'], e['watts']), (mm.E_ELEGIVEL, 330.0))
        self.assertEqual(e['texto'], 'Yes — 330 W')

    def test_5_teste_elegivel_sem_pr(self):
        e = self.por_dia(self.esforcos(), '2026-04-01')
        self.assertEqual(e['estado'], mm.E_ELEGIVEL)
        self.assertFalse(e['pr'])                                    # elegivel sem exigir recorde
        self.assertEqual(e['pct_do_melhor'], 97.1)

    def test_6_teste_com_pr(self):
        e = self.por_dia(self.esforcos(), '2026-05-01')
        self.assertEqual((e['estado'], e['pr']), (mm.E_ELEGIVEL, True))
        self.assertEqual(e['texto'], 'Yes — 345 W')

    def test_nao_elegivel_mostra_o_pr_como_referencia_sem_negar_a_atividade(self):
        c = ctx(curvas(), ate='2026-02-01')
        u = c['ultimo_esforco']
        self.assertEqual((u['estado'], u['watts'], u['data']), (mm.E_NAO_ELEGIVEL, 250.0, '2026-02-01'))
        self.assertEqual(u['texto'], 'No (PR: 340 W)')
        self.assertEqual(u['motivo'], mm.M_ABAIXO)

    def test_sem_teste_elegivel_num_dia_sem_esforco(self):
        e = self.por_dia(self.esforcos(), '2026-03-03')
        self.assertEqual((e['estado'], e['texto'], e['watts']), (mm.E_SEM_TESTE, 'Sem teste elegível', None))

    def test_4_dados_insuficientes_serie_curta_ou_sem_referencia(self):
        curta = curvas(serie=SERIE[:3])
        e = self.por_dia(self.esforcos(curta, ate='2025-05-15'), '2025-05-01')
        self.assertEqual((e['estado'], e['texto'], e['motivo']), (mm.E_INSUFICIENTE, 'Dados insuficientes', mm.M_AMOSTRA))
        primeiro = self.por_dia(self.esforcos(), '2025-03-01')       # 1.o ponto da serie: nada anterior para comparar
        self.assertEqual((primeiro['estado'], primeiro['motivo']), (mm.E_INSUFICIENTE, mm.M_SEM_REFERENCIA))

    def test_4_potencia_invalida_nao_gera_esforco(self):
        cv = curvas()
        for i, w in enumerate((0, -5, None, float('nan'), float('inf'), True, 'abc')):
            cv.append({'activity_id': f'x{i}', 'type': 'Bike', 'date': '2026-03-10', 'weight': 70.0,
                       'secs': [180], 'watts': [w]})
        esf = self.esforcos(cv)
        self.assertEqual([e for e in esf if e['data'] == '2026-03-10'], [])
        self.assertEqual(len(esf), len(SERIE))

    def test_icu_ignore_power_e_nao_elegivel_e_elegibilidade_desconhecida_e_insuficiente(self):
        ig = self.por_dia(self.esforcos(eleg=lambda a: 'inelegivel' if a == 'Bike-b2' else 'elegivel'), '2026-04-01')
        self.assertEqual((ig['estado'], ig['motivo']), (mm.E_NAO_ELEGIVEL, mm.M_IGNORE_POWER))
        desc = self.por_dia(self.esforcos(eleg=lambda a: 'desconhecida'), '2026-04-01')
        self.assertEqual((desc['estado'], desc['motivo']), (mm.E_INSUFICIENTE, mm.M_ELEG_DESCONHECIDA))
        sem_fn = self.por_dia(mm.avaliar_esforcos(curvas(), 'Bike', '2026-06-15', tags=TAGS), '2026-04-01')
        self.assertEqual(sem_fn['estado'], mm.E_INSUFICIENTE)          # sem informacao nunca se presume elegivel

    def test_7_cada_uma_das_quatro_modalidades(self):
        cv = sum((curvas(m) for m in MODS), [])
        for m in MODS:
            esf = mm.avaliar_esforcos(cv, m, '2026-06-15', (), ELEG, tags=TAGS)
            self.assertEqual(self.por_dia(esf, '2026-04-01')['texto'], 'Yes — 330 W', m)
            self.assertEqual({e['activity_id'] for e in esf}, {f'{m}-{a}' for a, _d, _w in SERIE}, m)   # sem mistura
            self.assertEqual(mm.referencias(cv, m, '2026-06-15')['mmp3_pr_atual']['watts'], 345.0, m)

    def test_8_outras_duracoes_nao_sao_mmp3(self):
        so_outras = [{'activity_id': f'o{i}', 'type': 'Bike', 'date': f'2026-0{1 + i % 5}-1{i % 9}', 'weight': 70.0,
                      'secs': [300, 720, 1200], 'watts': [400.0, 350.0, 330.0]} for i in range(8)]
        self.assertEqual(mm.avaliar_esforcos(so_outras, 'Bike', '2026-12-31', (), ELEG, tags=TAGS), [])
        misto = curvas() + [c180('y1', 'Bike', '2026-03-03', 100.0, {300: 500.0, 1200: 450.0})]
        e = self.por_dia(self.esforcos(misto), '2026-03-03')
        self.assertEqual(e['watts'], 100.0)                           # 180 s, nunca o 300 s (500) nem o 1200 s (450)
        self.assertEqual(mm.referencias(misto, 'Bike', '2026-06-15')['mmp3_pr_atual']['watts'], 345.0)

    def test_15_sem_fuga_de_dados_futuros(self):
        cv = curvas()
        ate_fev = mm.avaliar_esforcos(cv, 'Bike', '2026-02-01', (), ELEG)
        so_passado = mm.avaliar_esforcos([c for c in cv if c['date'] <= '2026-02-01'], 'Bike', '2026-02-01', (), ELEG)
        self.assertEqual(ate_fev, so_passado)
        com_futuro = cv + [c180('f1', 'Bike', '2026-12-01', 999.0)]
        self.assertEqual(mm.referencias(com_futuro, 'Bike', '2026-02-15'), mm.referencias(cv, 'Bike', '2026-02-15'))
        self.assertEqual(mm.avaliar_esforcos(com_futuro, 'Bike', '2026-06-15', (), ELEG),
                         mm.avaliar_esforcos(cv, 'Bike', '2026-06-15', (), ELEG))

    def test_observacoes_do_ir_sao_os_esforcos_elegiveis_com_ou_sem_pr(self):
        obs = mm.observacoes_ir(self.esforcos())
        datas = {o['data'] for o in obs}
        self.assertIn('2026-04-01', datas)                            # elegivel sem PR entra
        self.assertIn('2026-05-01', datas)                            # elegivel com PR entra
        self.assertNotIn('2026-02-01', datas)                         # nao elegivel nao entra
        self.assertNotIn('2025-03-01', datas)                         # sem referencia anterior nao entra
        self.assertTrue(all(o['test_id'].startswith('Bike:180:') for o in obs))

    def test_reutiliza_a_deteccao_e_o_limiar_existentes(self):
        import modelo_observacoes_auto as moa
        import protocolo
        self.assertEqual(mm.LIMIAR, protocolo.LIMIAR_ESFORCO)
        self.assertEqual(mm.LIMIAR, moa.LIMIAR_PROXIMIDADE)
        with mock.patch.object(moa, 'detetar', wraps=moa.detetar) as spy:
            mm.avaliar_esforcos(curvas(), 'Bike', '2026-06-15', (), ELEG)
        self.assertTrue(spy.called)
        self.assertEqual(set(spy.call_args.kwargs['testes']), {180})
        # a deteccao por omissao (60/300/1200) nao muda
        self.assertEqual(set(protocolo.TESTES), {60, 300, 1200})


# ═══ B. Entrada do ponto no CP Model (so leitura; M1/M2/M3 e a selecao do utilizador intactos) ═════════

def curvas_cp(com_180=True):
    """Bike, season 2026: 60/180/300/720/1200/3600 s em varias atividades (o CP Model usa o melhor de cada duracao)."""
    base = {60: 700.0, 180: 420.0, 300: 370.0, 720: 323.3, 1200: 310.0, 3600: 270.0}
    out = []
    for i in range(6):
        pts = {s: w - 10 + 2 * i for s, w in base.items()}
        if not com_180:
            pts.pop(180)
        secs = sorted(pts)
        out.append({'activity_id': f'k{i}', 'type': 'Bike', 'date': f'2026-0{1 + i}-10', 'weight': 70.0,
                    'secs': secs, 'watts': [pts[s] for s in secs]})
    return out


class EntradaNoCpModel(unittest.TestCase):
    def setUp(self):
        import cp_model as cpm
        self.cpm = cpm

    def test_180_e_duracao_extra_nas_quatro_modalidades_e_nunca_classica(self):
        for m in MODS:
            self.assertNotIn(180, self.cpm.DURACOES_CLASSICAS[m], m)
            self.assertIn(180, self.cpm.DURACOES_EXTRA[m], m)

    def test_9_novo_ponto_entra_pelo_fluxo_existente(self):
        r = mis.impacto_cp_model('Bike', curvas_cp(), '2026-12-31')
        self.assertEqual(r['ponto_mmp3']['watts'], 420.0)             # o melhor 180 s da season, vindo de pontos_de_curvas
        self.assertTrue(r['duracao_so_em_modelos_3p'])
        self.assertTrue(r['provisorio'])
        # identico ao fluxo direto (pontos_de_curvas + calcular_cp_completo) com as mesmas entradas
        registos = [{'date': c['date'], 'secs': c['secs'], 'watts': c['watts'], 'season': '2026'} for c in curvas_cp()]
        dados = self.cpm.pontos_de_curvas(registos, 'Bike', season_activa='2026', modo='coerente')
        res = self.cpm.calcular_cp_completo(dados, 'Bike')
        for nome, m in res['modelos'].items():
            self.assertEqual(r['modelos_provisorios'][nome]['cp'], m.get('cp'), nome)
        self.assertIn(180, {int(t) for _w, t in dados['all_mmp_pts_full']})

    def test_19_m1_m2_m3_nao_mudam_por_causa_do_mmp3(self):
        com = mis.impacto_cp_model('Bike', curvas_cp(True), '2026-12-31')['modelos_provisorios']
        sem = mis.impacto_cp_model('Bike', curvas_cp(False), '2026-12-31')['modelos_provisorios']
        for nome in ('M1 (WLS-P)', 'M2 (WLS-1/t)', 'M3 (NL-2p)'):
            self.assertEqual(com[nome]['cp'], sem[nome]['cp'], nome)
            self.assertEqual(com[nome]['n_pts'], sem[nome]['n_pts'], nome)
            self.assertEqual(com[nome]['wp'], sem[nome]['wp'], nome)


    def test_nao_ha_cp_gravado_nem_influencia_no_ir(self):
        r = mis.impacto_cp_model('Bike', curvas_cp(), '2026-12-31')
        self.assertNotIn('cp_gravado', r)
        self.assertFalse(r['influencia_no_ir'])           # o CP Model e informativo: nao define o P0 do IR

    def test_so_leitura_nao_altera_as_curvas_nem_chama_gravacao(self):
        cv = copy.deepcopy(curvas_cp())
        antes = copy.deepcopy(cv)
        with mock.patch.object(db, '_exec', side_effect=AssertionError('nao devia tocar na base')):
            mis.impacto_cp_model('Bike', cv, '2026-12-31')
        self.assertEqual(cv, antes)


class PontosAntigosIntactos(unittest.TestCase):
    def test_4_o_ponto_de_180_s_nao_substitui_mmp1_5_12_20_60(self):
        import cp_model as cpm
        def pts(com):
            regs = [{'date': c['date'], 'secs': c['secs'], 'watts': c['watts'], 'season': '2026'} for c in curvas_cp(com)]
            d = cpm.pontos_de_curvas(regs, 'Bike', season_activa='2026', modo='coerente')
            return sorted((int(t), w) for w, t in d['all_mmp_pts_full'])
        com, sem = pts(True), pts(False)
        self.assertEqual([p for p in com if p[0] != 180], sem)         # todas as outras duracoes identicas, ponto a ponto
        self.assertEqual(len(com), len(sem) + 1)
        self.assertEqual(sum(1 for t, _ in com if t == 180), 1)
        for t in (300, 720, 1200):                                     # classicas 5/12/20 min continuam presentes
            self.assertIn(t, {x[0] for x in com})




class InterfaceEstatica(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, 'tabs', 'tab_pmc.py'), encoding='utf-8') as f:
            self.src = f.read()
        self.ir = self.src[self.src.index('// <<modelo-ir>>'):self.src.index('// <</modelo-ir>>')]

    def test_sem_seletor_de_duracao_no_ir(self):
        self.assertNotIn('irDur', self.ir)
        self.assertNotIn('IR_DURS', self.ir)

    def test_estados_distintos_na_interface(self):
        for k in ('sem_referencia_mmp3', 'erro_solver', 'desatualizado', 'nao_identificavel', 'valido'):
            self.assertIn(k, self.ir, k)

    def test_painel_preditivo_antigo_nao_se_apresenta_como_validacao_do_ir_mmp3(self):
        self.assertIn('Nao valida o IR baseado em MMP3', self.src)
        self.assertIn('LEGADO', self.src)

    def test_ui_nao_afirma_que_o_cp_e_a_origem_do_p0(self):
        self.assertNotRegex(self.ir, r'P0 = CP|CP confirmado|\(= P0\)|cp_gravado')
        self.assertIn('MMP3 do último', self.ir)
        self.assertIn('P0 global', self.ir)


if __name__ == '__main__':
    unittest.main()
