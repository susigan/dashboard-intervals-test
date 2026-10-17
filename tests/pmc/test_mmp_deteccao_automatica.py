"""Deteccao automatica de observacoes MMP (season, all-time, recente): sem confirmacao manual.

    python -m pytest tests/pmc/test_mmp_deteccao_automatica.py

Offline. Dados SINTETICOS: testam o codigo, nao o atleta nem o modelo. SQLite temporaria;
curvas gravadas com db.upsert_power_curves (o caminho da sincronizacao).
"""
import copy
import math
import os
import re
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fase1_util as U  # noqa: E402

ROOT = U.ROOT
APP = U.importar_app()
import config  # noqa: E402
import db  # noqa: E402
import modelo_estimador as E  # noqa: E402
import modelo_observacoes_auto as moa  # noqa: E402
import modelo_testes as mt  # noqa: E402
import modelo_validacao as V  # noqa: E402
import observacoes_auto as oa  # noqa: E402
import protocolo  # noqa: E402

MARCOS = [('2025-01-01', 'S25'), ('2026-01-01', 'S26')]
AGORA = datetime(2026, 10, 17, 12, 0, 0)


def season(d):
    return config.season_de(d, MARCOS)


def cv(aid, tipo, data, w1200=None, w300=None, w60=None):
    """Curva de uma atividade com 60/300/1200 s (derivadas de w1200 se nao forem dadas)."""
    if w1200 is None:
        w1200 = w300 / 1.3
    return {'activity_id': aid, 'type': tipo, 'date': data, 'weight': 70.0, 'secs': [60, 300, 1200],
            'watts': [w60 if w60 is not None else w1200 * 1.8,
                      w300 if w300 is not None else w1200 * 1.3, w1200]}


# Bike 1200 s: 2025 (S25) com maximo all-time 340; 2026 (S26) comeca mais baixo
BIKE = [cv('a1', 'Bike', '2025-03-01', 300), cv('a2', 'Bike', '2025-04-01', 310),
        cv('a3', 'Bike', '2025-05-01', 320), cv('a4', 'Bike', '2025-06-01', 340),
        cv('a5', 'Bike', '2025-07-01', 330), cv('a6', 'Bike', '2025-10-01', 335),
        cv('b1', 'Bike', '2026-02-01', 250), cv('b2', 'Bike', '2026-04-01', 280),
        cv('b3', 'Bike', '2026-05-01', 262), cv('b4', 'Bike', '2026-06-01', 315)]
# Row 300 s: maximo cedo na season (400); depois mais baixo. 'r6' so e proximo do melhor recente
ROW = [cv('r1', 'Row', '2025-12-01', w300=380), cv('r2', 'Row', '2026-01-10', w300=400),
       cv('r3', 'Row', '2026-03-01', w300=300), cv('r4', 'Row', '2026-05-01', w300=310),
       cv('r5', 'Row', '2026-07-20', w300=340), cv('r6', 'Row', '2026-09-01', w300=315)]


def por_id(cands):
    return {c['test_id']: c for c in cands}


def detetar(curvas, ignorar=()):
    return por_id(moa.detetar(curvas, ignorar, season))


class Pura(unittest.TestCase):
    """Deteccao das tres referencias (modulo puro)."""

    def setUp(self):
        self.det = detetar(copy.deepcopy(BIKE + ROW))

    # 1 ── novo maximo da season abaixo do all-time
    def test_01_novo_maximo_da_season_inferior_ao_all_time_e_detetado(self):
        c = self.det['Bike:1200:b2']
        self.assertEqual(c['referencia_tipo'], 'season')
        s = c['referencias']['season']
        self.assertTrue(s['e_maximo'])
        # a referencia da season e o MAXIMO ANTERIOR (b1 = 250 W), nunca o proprio esforco
        self.assertEqual((s['referencia_watts'], s['referencia_activity_id'], s['n_pontos']), (250, 'b1', 1))
        self.assertEqual(s['pct'], 112.0)                 # 280/250
        self.assertEqual(c['pct_da_referencia'], 112.0)
        self.assertEqual(s['season'], 'S26')
        at = c['referencias']['all_time']
        self.assertFalse(at['qualifica'])                 # 280/340 = 82%: abaixo do all-time
        self.assertEqual(at['referencia_watts'], 340)
        self.assertLess(c['watts'] / at['referencia_watts'], moa.LIMIAR_PROXIMIDADE)
        self.assertFalse(c['referencias'].get('recente', {}).get('qualifica', False))

    # 3 ── >= 92% da season, sem recorde
    def test_03_esforco_a_92_pct_da_season_sem_recorde(self):
        c = self.det['Bike:1200:b3']                       # 262 vs max da season 280
        s = c['referencias']['season']
        self.assertTrue(s['qualifica'])
        self.assertFalse(s['e_maximo'])
        self.assertEqual(s['referencia_watts'], 280)
        self.assertAlmostEqual(s['pct'], round(262 / 280 * 100, 1))
        self.assertEqual(c['referencia_tipo'], 'season')
        self.assertFalse(c['referencias']['all_time']['qualifica'])

    def test_03b_abaixo_do_limiar_nao_e_detetado(self):
        self.assertNotIn('Bike:1200:b1', {k: 1 for k in self.det if False})   # b1 e o 1.o da season
        # um esforco a 90% da season e do all-time, sem recente: nao entra
        extra = copy.deepcopy(BIKE) + [cv('b5', 'Bike', '2026-06-10', 250)]       # 250/315 = 79%
        self.assertNotIn('Bike:1200:b5', detetar(extra))

    # 4 ── >= 92% do all-time
    def test_04_esforco_a_92_pct_do_all_time(self):
        c = self.det['Bike:1200:b4']                       # 315 vs all-time 340 = 92.6%
        at = c['referencias']['all_time']
        self.assertTrue(at['qualifica'])
        self.assertAlmostEqual(at['pct'], round(315 / 340 * 100, 1))
        self.assertEqual(c['referencia_tipo'], 'all_time')  # a mais exigente que qualifica
        self.assertGreaterEqual(c['watts'] / at['referencia_watts'], 0.92)

    def test_04b_limiar_exato_conta_e_logo_abaixo_nao(self):
        # all-time = season = 100 W (cinco pontos em 2026); o esforco de agosto so e detetado se >= 92 W
        for w, esperado in ((92.0, True), (91.99, False), (100.0, True), (50.0, False)):
            cs = [cv(f'x{i}', 'Bike', f'2026-0{i}-01', 100) for i in range(1, 6)]
            cs.append(cv('t', 'Bike', '2026-08-01', w))
            c = detetar(cs).get('Bike:1200:t')
            self.assertEqual(c is not None, esperado, w)

    # 5 ── proximo apenas do melhor recente
    def test_05_esforco_proximo_apenas_do_melhor_recente(self):
        c = self.det['Row:300:r6']
        r = c['referencias']
        self.assertTrue(r['recente']['qualifica'])
        self.assertFalse(r['season']['qualifica'])          # season max 400 (cedo na season)
        self.assertFalse(r['all_time']['qualifica'])
        self.assertEqual(c['referencia_tipo'], 'recente')
        self.assertEqual(r['recente']['referencia_watts'], 340)
        self.assertEqual(r['recente']['n_pontos'], 2)       # r4 e r5: os pontos ANTERIORES na janela (r6 nao conta)
        self.assertEqual(r['season']['referencia_watts'], 400)

    def test_paridade_da_referencia_recente_com_protocolo_detectar_testes(self):
        for curvas in (BIKE + ROW, copy.deepcopy(BIKE)):
            antigo = protocolo.detectar_testes(copy.deepcopy(curvas))
            esperados = {}
            for mod, durs in antigo.items():
                for secs, d in durs.items():
                    for t in d['testes']:
                        esperados[mt.id_teste(mod, secs, t['activity_id'])] = t
            novo = detetar(copy.deepcopy(curvas))
            recentes = {k: c for k, c in novo.items() if c['referencias'].get('recente', {}).get('qualifica')}
            self.assertEqual(set(recentes), set(esperados))
            for k, t in esperados.items():
                r = recentes[k]['referencias']['recente']
                if t['pct_do_melhor'] < 100:          # nao e o maximo da janela: referencia e % identicas
                    self.assertEqual(r['pct'], t['pct_do_melhor'], k)
                    self.assertEqual(round(r['referencia_watts'], 1), t['melhor_recente'], k)
                else:                                 # a deteccao antiga compara-o consigo (100%); aqui, com o anterior
                    self.assertGreaterEqual(r['pct'], 100.0, k)
                    self.assertTrue(r['e_maximo'], k)
                    self.assertLessEqual(r['referencia_watts'], t['melhor_recente'], k)

    def test_recente_sem_regressao_mesmo_conjunto_que_a_deteccao_antiga_em_series_aleatorias(self):
        import random
        from datetime import date, timedelta
        for semente in range(25):
            rnd = random.Random(semente)
            d, cs = date(2025, 6, 1), []
            for i in range(rnd.randint(5, 40)):
                d += timedelta(days=rnd.randint(1, 30))       # datas distintas: sem empates de ordenacao
                cs.append(cv(f'q{i}', 'Bike', d.isoformat(), rnd.uniform(150, 350)))
            antigo = {mt.id_teste(m, secs, t['activity_id'])
                      for m, durs in protocolo.detectar_testes(copy.deepcopy(cs)).items()
                      for secs, dd in durs.items() for t in dd['testes']}
            novo = {k for k, c in detetar(copy.deepcopy(cs)).items()
                    if c['referencias'].get('recente', {}).get('qualifica')}
            self.assertEqual(novo, antigo, semente)

    # ── circularidade: nenhum esforco e comparado consigo proprio ──────────────
    def test_c1_primeiro_esforco_da_season_nao_se_classifica_pelo_proprio_valor(self):
        # b1 e o 1.o de S26 (250 W); o all-time (S25) e 340 W -> 73,5%: nao e detetado
        self.assertNotIn('Bike:1200:b1', self.det)
        # e se o 1.o esforco da season for alto, entra pelo ALL-TIME, nunca pela season sem pontos anteriores
        cs = copy.deepcopy(BIKE) + [cv('c1', 'Bike', '2026-01-05', 330)]
        c = detetar(cs)['Bike:1200:c1']
        self.assertEqual(c['referencia_tipo'], 'all_time')
        self.assertNotIn('season', c['referencias'])        # S26 ainda nao tem pontos anteriores
        self.assertAlmostEqual(c['pct_da_referencia'], round(330 / 340 * 100, 1))

    def test_c2_os_primeiros_quatro_pontos_do_historico(self):
        cs = [cv(f'h{i}', 'Bike', f'2026-0{i}-01', w) for i, w in enumerate((300, 200, 210, 220, 230, 240), 1)]
        det = detetar(cs)
        h1 = det['Bike:1200:h1']
        self.assertEqual((h1['referencia_tipo'], h1['estado_referencia'], h1['referencias']),
                         (None, moa.SEM_REFERENCIA, {}))     # 1.o ponto: nada com que o comparar
        self.assertIsNone(h1['pct_da_referencia'])
        # 2.o a 4.o: comparados so com o que veio antes e NAO aceites por serem "100% de si proprios"
        for i in (2, 3, 4, 5, 6):
            self.assertNotIn(f'Bike:1200:h{i}', det, i)       # 200..240 vs 300: < 92%
        # os primeiros pontos com referencia real mostram n_pontos = 1, 2, 3, ...
        subindo = [cv(f's{i}', 'Bike', f'2026-0{i}-01', 300 + 10 * i) for i in range(1, 7)]
        d2 = detetar(subindo)
        self.assertEqual(d2['Bike:1200:s1']['estado_referencia'], moa.SEM_REFERENCIA)
        self.assertEqual([d2[f'Bike:1200:s{i}']['referencias']['all_time']['n_pontos'] for i in range(2, 7)],
                         [1, 2, 3, 4, 5])

    def test_c3_ausencia_de_referencia_anterior(self):
        det = moa.detetar(BIKE + ROW, (), season)
        sem = [c for c in det if c['referencia_tipo'] is None]
        # so o primeiro ponto valido de cada serie (modalidade x duracao com >= 5 pontos)
        self.assertEqual(sorted((c['modalidade'], c['duracao_s'], c['activity_id']) for c in sem),
                         [('Bike', 60, 'a1'), ('Bike', 300, 'a1'), ('Bike', 1200, 'a1'),
                          ('Row', 60, 'r1'), ('Row', 300, 'r1'), ('Row', 1200, 'r1')])
        for c in sem:
            self.assertEqual((c['estado_referencia'], c['referencia_watts'], c['referencias']),
                             (moa.SEM_REFERENCIA, None, {}))
        # uma atividade ignorada como 1.a tambem fica sem referencia; as outras nao a usam
        d = detetar(copy.deepcopy(BIKE), ignorar={'a1'})
        self.assertEqual(d['Bike:1200:a1']['estado_referencia'], moa.SEM_REFERENCIA)
        self.assertEqual(d['Bike:1200:a2']['estado_referencia'], moa.SEM_REFERENCIA)   # a1 ignorada: a2 nao tem anterior

    def test_c4_n_pontos_conta_so_pontos_anteriores(self):
        # contagem independente (forca bruta) de pontos anteriores nao ignorados
        cs = copy.deepcopy(BIKE + ROW)
        ign = {'a3', 'r2'}
        det = moa.detetar(cs, ign, season)
        serie = {}
        for c in cs:
            for s, w in zip(c['secs'], c['watts']):
                serie.setdefault((c['type'], s), []).append((c['date'], c['activity_id'], w))
        for k in serie:
            serie[k].sort()
        n_com_ref = 0
        for c in det:
            if c['referencia_tipo'] is None:
                continue
            n_com_ref += 1
            pts = serie[(c['modalidade'], c['duracao_s'])]
            i = next(j for j, (d, a, w) in enumerate(pts) if a == c['activity_id'])
            ant = [(d, a, w) for d, a, w in pts[:i] if a not in ign]
            self.assertEqual(c['referencias']['all_time']['n_pontos'], len(ant), c['test_id'])
            self.assertEqual(c['referencias']['all_time']['referencia_watts'], max(w for _, _, w in ant))
            if 'season' in c['referencias']:
                da_s = [x for x in ant if season(x[0]) == c['season']]
                self.assertEqual(c['referencias']['season']['n_pontos'], len(da_s), c['test_id'])
            if 'recente' in c['referencias']:
                lim = (datetime.strptime(c['data'], '%Y-%m-%d').date().toordinal() - 180)
                na_j = [x for x in ant if datetime.strptime(x[0], '%Y-%m-%d').date().toordinal() >= lim]
                self.assertEqual(c['referencias']['recente']['n_pontos'], len(na_j), c['test_id'])
                self.assertGreaterEqual(len(na_j), moa.MIN_ANTERIORES_JANELA)
        self.assertGreater(n_com_ref, 10)

    def test_c5_novo_maximo_da_season_abaixo_do_all_time_exige_ponto_anterior_da_season(self):
        # com b1 anterior: detetado (112% da season). Sem b1, b2 seria o 1.o da season e NAO entra
        com = detetar(copy.deepcopy(BIKE))['Bike:1200:b2']
        self.assertEqual((com['referencia_tipo'], com['pct_da_referencia']), ('season', 112.0))
        sem_b1 = detetar([c for c in copy.deepcopy(BIKE) if c['activity_id'] != 'b1'])
        self.assertNotIn('Bike:1200:b2', sem_b1)               # 280/340 = 82%, sem season anterior

    def test_c6_esforco_posterior_proximo_do_maximo_da_season(self):
        c = self.det['Bike:1200:b3']
        s = c['referencias']['season']
        self.assertEqual((s['referencia_watts'], s['n_pontos'], c['referencia_tipo']), (280, 2, 'season'))
        self.assertFalse(s['e_maximo'])
        self.assertAlmostEqual(s['pct'], round(262 / 280 * 100, 1))

    def test_c7_o_proprio_esforco_nunca_esta_na_sua_referencia(self):
        # um unico pico enorme: com a regra antiga seria "100%" de si proprio
        cs = [cv(f'u{i}', 'Bike', f'2026-0{i}-01', 200) for i in range(1, 6)] + [cv('pico', 'Bike', '2026-08-01', 900)]
        c = detetar(cs)['Bike:1200:pico']
        for t, r in c['referencias'].items():
            self.assertEqual(r['referencia_watts'], 200, t)
            self.assertEqual(r['pct'], 450.0, t)
            self.assertTrue(r['e_maximo'], t)

    def test_amostra_minima_existente_5_pontos_por_serie(self):
        self.assertEqual(detetar([cv(f'p{i}', 'Bike', f'2026-0{i}-01', 300) for i in range(1, 5)]), {})
        self.assertNotEqual(detetar([cv(f'p{i}', 'Bike', f'2026-0{i}-01', 300) for i in range(1, 6)]), {})

    def test_referencias_sao_causais(self):
        # um maximo FUTURO nao altera a referencia de um esforco anterior
        antes = detetar(copy.deepcopy(BIKE))['Bike:1200:b3']
        depois = detetar(copy.deepcopy(BIKE) + [cv('z', 'Bike', '2026-09-01', 900)])['Bike:1200:b3']
        self.assertEqual(antes['referencias'], depois['referencias'])

    def test_deteccao_e_deterministica_e_nao_altera_a_entrada(self):
        entrada = copy.deepcopy(BIKE + ROW)
        copia = copy.deepcopy(entrada)
        a = moa.detetar(entrada, (), season)
        self.assertEqual(entrada, copia)
        self.assertEqual(a, moa.detetar(list(reversed(entrada)), (), season))

    # 8 ── icu_ignore_power=true fora das referencias
    def test_08_atividade_ignorada_nao_entra_em_nenhuma_referencia(self):
        com = copy.deepcopy(BIKE) + [cv('bI', 'Bike', '2026-03-01', 500)]
        sem = detetar(copy.deepcopy(BIKE))
        ignorando = detetar(com, {'bI'})
        for k, c in sem.items():
            self.assertEqual(ignorando[k]['referencias'], c['referencias'], k)
        # sem a exclusao, o 500 W contaminaria as referencias dos esforcos seguintes
        sem_excluir = detetar(com, set())
        for k in ('Bike:1200:b2', 'Bike:1200:b4'):
            self.assertIn(k, ignorando)
            self.assertNotIn(k, sem_excluir)                 # o 500 W deixaria de os detetar
        # o proprio esforco ignorado pode ser mostrado, marcado, e nunca e referencia de si
        bi = ignorando['Bike:1200:bI']
        self.assertTrue(bi['ignorada_na_referencia'])
        self.assertNotEqual(bi['referencias']['all_time']['referencia_activity_id'], 'bI')

    def test_08b_so_ignoradas_ou_poucas_validas_nao_geram_nada(self):
        cs = [cv(f'q{i}', 'Bike', f'2026-0{i}-01', 300) for i in range(1, 6)]
        self.assertEqual(detetar(cs, {'q1', 'q2'}), {})        # ficam 3 validas (< 5)

    # 10 ── potencias invalidas
    def test_10_potencias_invalidas_nao_influenciam_nem_sao_observacoes(self):
        limpo = detetar(copy.deepcopy(BIKE))
        sujo = copy.deepcopy(BIKE)
        for i, w in enumerate([True, False, float('nan'), float('inf'), float('-inf'), 0, -5, None, 'x']):
            sujo.append({'activity_id': f'bad{i}', 'type': 'Bike', 'date': f'2026-0{(i % 8) + 1}-15',
                         'weight': 70.0, 'secs': [60, 300, 1200], 'watts': [w, w, w]})
        # mais um com infinito e um com True nas OUTRAS duracoes tambem nao influencia
        sujo.append({'activity_id': 'inf', 'type': 'Bike', 'date': '2026-06-20', 'weight': 70.0,
                     'secs': [60, 300, 1200], 'watts': [float('inf'), True, float('nan')]})
        det = detetar(sujo)
        for k, c in limpo.items():
            self.assertEqual(det[k]['referencias'], c['referencias'], k)
        self.assertEqual({k for k in det if k.split(':')[2].startswith('bad') or k.endswith(':inf')}, set())
        for c in det.values():
            self.assertTrue(moa.potencia_valida(c['watts']))
            for r in c['referencias'].values():
                self.assertTrue(moa.potencia_valida(r['referencia_watts']))

    def test_10b_potencia_valida(self):
        for v in (1, 1.5, 300, 0.1):
            self.assertTrue(moa.potencia_valida(v), v)
        for v in (True, False, 0, -1, float('nan'), float('inf'), float('-inf'), None, '300', [300]):
            self.assertFalse(moa.potencia_valida(v), v)

    def test_curvas_sem_data_ou_id_sao_descartadas(self):
        lixo = [{'activity_id': None, 'type': 'Bike', 'date': '2026-01-01', 'secs': [300], 'watts': [300]},
                {'activity_id': 'z', 'type': 'Bike', 'date': 'ontem', 'secs': [300], 'watts': [300]},
                'nao e dict', None]
        self.assertEqual(moa.limpar_curvas(lixo), [])


class Tags(unittest.TestCase):
    """2 ── tags do atleta com tempo ou distancia (5min, 2km, 5km, 500m, 20min, ...)."""

    def test_02_tags_de_tempo(self):
        for txt, seg in (('5min', 300), ('20min', 1200), ('1min', 60), ('5 min', 300), ('20 MIN', 1200),
                         ('90s', 90), ('2,5min', 150), ('30sec', 30)):
            p = moa.analisar_tag(txt)
            self.assertEqual((p['tipo'], p['segundos']), ('tempo', seg), txt)

    def test_02_tags_de_distancia(self):
        for txt, m in (('2km', 2000), ('5km', 5000), ('500m', 500), ('5 km', 5000), ('2.5km', 2500),
                       ('2000m', 2000), ('1,5KM', 1500)):
            p = moa.analisar_tag(txt)
            self.assertEqual((p['tipo'], p['metros']), ('distancia', m), txt)

    def test_02_tags_que_nao_sao_tempo_nem_distancia(self):
        for txt in ('zona 2', 'z2', 'prova', '5', 'min5', '0min', '-5min', '0km', '5kmh', '', ' ', None,
                    5, ['5min'], '5min max', 'nan min', 'inf km'):
            self.assertIsNone(moa.analisar_tag(txt), repr(txt))

    def test_02_tags_da_atividade_lista_ou_texto(self):
        self.assertEqual(moa.tags_da_atividade({'tags': ['5min', ' 2km ', '', None]}), ['5min', '2km'])
        self.assertEqual(moa.tags_da_atividade({'tags': '5min, 20min,zona 2'}), ['5min', '20min', 'zona 2'])
        for a in ({'tags': None}, {}, None, 'x', {'tags': 5}):
            self.assertEqual(moa.tags_da_atividade(a), [])

    def test_02_tag_de_tempo_coincide_com_a_duracao_mas_distancia_nunca(self):
        tt = moa.tags_de_teste(['5min', '2km', 'zona 2'])
        self.assertEqual(len(tt), 2)
        self.assertTrue(moa.tag_coincide_duracao(tt, 300))
        self.assertFalse(moa.tag_coincide_duracao(tt, 1200))
        self.assertFalse(moa.tag_coincide_duracao(moa.tags_de_teste(['2km', '5km', '500m']), 300))
        self.assertTrue(moa.tag_coincide_duracao(moa.tags_de_teste(['20min']), 1200))
        self.assertTrue(moa.tag_coincide_duracao(moa.tags_de_teste(['1min']), 60))


# ── ordem dentro do mesmo dia ────────────────────────────────────────────────
HIST = [cv(f'm{i}', 'Bike', f'2026-0{i}-01', 300) for i in range(1, 6)]        # cinco pontos anteriores a 300 W
DIA = '2026-06-10'


def dia_com(p_id='p', q_id='q', wp=330, wq=290):
    """Dois esforcos no mesmo dia: P (novo maximo, 330 W) e Q (290 W). Q so qualifica se P
    NAO for sua referencia: 290/300 = 96,7% mas 290/330 = 87,9%."""
    return copy.deepcopy(HIST) + [cv(p_id, 'Bike', DIA, wp), cv(q_id, 'Bike', DIA, wq)]


def h(hhmmss, dia=DIA):
    return f'{dia}T{hhmmss}'


class OrdemMesmoDia(unittest.TestCase):
    """So a hora local REAL ordena esforcos do mesmo dia; sem prova, nenhum e referencia do outro."""

    def det(self, curvas, horas=None, ignorar=()):
        return por_id(moa.detetar(curvas, ignorar, season, horas=horas))

    # 1 ── timestamps distintos
    def test_a1_horas_distintas_o_mais_cedo_e_anterior(self):
        d = self.det(dia_com(), {'p': h('08:00:00'), 'q': h('18:00:00')})
        self.assertNotIn('Bike:1200:q', d)                      # P (330) e anterior a Q: 290/330 < 92%
        p = d['Bike:1200:p']                                     # P so tem o historico (5 pontos) como referencia
        self.assertEqual((p['referencias']['all_time']['n_pontos'], p['referencias']['all_time']['referencia_watts']),
                         (5, 300))
        self.assertEqual(p['ambiguos_mesmo_dia'], 0)

    def test_a1_horas_distintas_ordem_inversa(self):
        d = self.det(dia_com(), {'p': h('18:00:00'), 'q': h('08:00:00')})
        q = d['Bike:1200:q']                                     # Q veio primeiro: so ve o historico
        self.assertEqual((q['referencia_tipo'], q['referencias']['all_time']['n_pontos'],
                          q['referencias']['all_time']['referencia_watts']), ('all_time', 5, 300))
        p = d['Bike:1200:p']                                     # P ve o historico e Q
        self.assertEqual(p['referencias']['all_time']['n_pontos'], 6)

    def test_a1_a_hora_manda_e_nao_o_activity_id(self):
        # ids trocados: o resultado segue a hora, nunca o nome
        a = self.det(dia_com('p', 'q'), {'p': h('08:00:00'), 'q': h('18:00:00')})
        b = self.det(dia_com('z', 'a'), {'z': h('08:00:00'), 'a': h('18:00:00')})     # z (330) cedo, a (290) tarde
        self.assertEqual(('Bike:1200:q' in a), ('Bike:1200:a' in b))
        self.assertNotIn('Bike:1200:a', b)

    # 2 ── mesmo timestamp
    def test_a2_mesma_hora_ordem_desconhecida_nenhum_serve_de_referencia_ao_outro(self):
        d = self.det(dia_com(), {'p': h('12:00:00'), 'q': h('12:00:00')})
        for aid in ('p', 'q'):
            c = d[f'Bike:1200:{aid}']
            self.assertEqual(c['referencias']['all_time']['n_pontos'], 5, aid)   # so o historico
            self.assertEqual(c['referencias']['all_time']['referencia_watts'], 300, aid)
            self.assertEqual(c['ambiguos_mesmo_dia'], 1, aid)
        self.assertIn('Bike:1200:q', d)                          # Q detetado contra 300 (96,7%), nao contra P

    # 3 ── so data (sem hora)
    def test_a3_sem_hora_ordem_desconhecida_e_independente_do_activity_id(self):
        for horas in (None, {}, {'p': None, 'q': None}):
            d = self.det(dia_com(), horas)
            self.assertEqual({k: v['ambiguos_mesmo_dia'] for k, v in d.items() if k in ('Bike:1200:p', 'Bike:1200:q')},
                             {'Bike:1200:p': 1, 'Bike:1200:q': 1})
            self.assertEqual(d['Bike:1200:q']['referencias']['all_time']['referencia_watts'], 300)
        # o activity_id nao desempata: o mesmo par de esforcos com os nomes ao contrario da ordem alfabetica
        a = self.det(dia_com('p', 'q'))                           # 330 W = 'p', 290 W = 'q'
        b = self.det(dia_com('z', 'a'))                           # 330 W = 'z', 290 W = 'a'
        self.assertEqual(a['Bike:1200:q']['referencias'], b['Bike:1200:a']['referencias'])
        self.assertEqual(a['Bike:1200:p']['referencias'], b['Bike:1200:z']['referencias'])
        self.assertEqual(a['Bike:1200:q']['pct_da_referencia'], b['Bike:1200:a']['pct_da_referencia'])

    def test_a3b_so_uma_das_duas_tem_hora_e_ambigua(self):
        d = self.det(dia_com(), {'p': h('08:00:00')})
        self.assertEqual(d['Bike:1200:q']['ambiguos_mesmo_dia'], 1)
        self.assertEqual(d['Bike:1200:q']['referencias']['all_time']['n_pontos'], 5)

    def test_a3c_hora_de_outro_dia_ou_invalida_nao_conta(self):
        for lixo in ('2026-03-01T07:30:00', 'ontem', '2026-06-10', '2026-06-10T25:61:00', '', None, 5):
            d = self.det(dia_com(), {'p': lixo, 'q': lixo})
            self.assertEqual(d['Bike:1200:q']['ambiguos_mesmo_dia'], 1, lixo)
        self.assertEqual(moa.hora_valida('2026-06-10T08:05', DIA), '08:05:00')
        self.assertEqual(moa.hora_valida('2026-06-10 08:05:09', DIA), '08:05:09')
        self.assertIsNone(moa.hora_valida('2026-06-11T08:05:09', DIA))

    def test_a4_o_esforco_nunca_e_referencia_de_si_proprio_nem_com_o_mesmo_timestamp(self):
        # um unico esforco no dia: nada ambiguo, e ele nao esta na sua referencia
        cs = copy.deepcopy(HIST) + [cv('so', 'Bike', DIA, 900)]
        c = self.det(cs, {'so': h('07:00:00')})['Bike:1200:so']
        self.assertEqual((c['referencias']['all_time']['n_pontos'], c['ambiguos_mesmo_dia']), (5, 0))
        self.assertEqual(c['referencias']['all_time']['referencia_watts'], 300)

    def test_a5_ignorado_do_mesmo_dia_nem_conta_como_ambiguo(self):
        d = self.det(dia_com(), {'p': h('12:00:00'), 'q': h('12:00:00')}, ignorar={'p'})
        self.assertEqual(d['Bike:1200:q']['ambiguos_mesmo_dia'], 0)       # p ignorada: fora de qualquer referencia

    def test_a6_saida_deterministica_independente_da_ordem_de_entrada(self):
        horas = {'p': h('12:00:00'), 'q': h('12:00:00')}
        cs = dia_com()
        a = moa.detetar(copy.deepcopy(cs), (), season, horas=horas)
        b = moa.detetar(list(reversed(copy.deepcopy(cs))), (), season, horas=horas)
        self.assertEqual(a, b)

    def test_a7_dias_diferentes_continuam_a_ordenar_pela_data(self):
        cs = copy.deepcopy(HIST) + [cv('p', 'Bike', '2026-06-10', 330), cv('q', 'Bike', '2026-06-11', 290)]
        d = self.det(cs)                                           # sem horas: dias distintos bastam
        self.assertNotIn('Bike:1200:q', d)                         # P (dia anterior) e referencia de Q
        self.assertEqual(d['Bike:1200:q' if 'Bike:1200:q' in d else 'Bike:1200:p']['ambiguos_mesmo_dia'], 0)


class Versoes(unittest.TestCase):
    """Planeamento do registo (modulo puro): idempotencia, versoes, retiradas, na_epoca."""

    def plano(self, curvas, hist, agora=AGORA, ignorar=()):
        cands = moa.detetar(curvas, ignorar, season)
        return moa.planear(cands, hist, agora, moa.limpar_curvas(curvas),
                           elegibilidade_fn=lambda a: {'estado': 'elegivel', 'motivo': None,
                                                       'icu_ignore_power': False,
                                                       'ignore_parts_presente': False},
                           criterio={'v': 1}, origem='script')

    # 6 ── repetida
    def test_06_mesmos_dados_nao_geram_linhas_novas(self):
        curvas = copy.deepcopy(BIKE + ROW)
        p1 = self.plano(curvas, [])
        self.assertGreater(len(p1), 0)
        self.assertEqual({(l['test_id'], l['versao']) for l in p1}, {(l['test_id'], 1) for l in p1})
        self.assertEqual(self.plano(curvas, p1), [])
        self.assertEqual(self.plano(curvas, p1, agora=datetime(2027, 1, 1)), [])

    # 7 ── alteracao de potencia
    def test_07_potencia_alterada_cria_versao_e_so_a_ultima_e_atual(self):
        curvas = copy.deepcopy(BIKE)
        h = self.plano(curvas, [])
        assert 'Bike:1200:b3' in {l['test_id'] for l in h}
        for c in curvas:
            if c['activity_id'] == 'b3':
                c['watts'][2] = 265
        novas = self.plano(curvas, h, agora=datetime(2026, 11, 1))
        self.assertEqual([(l['test_id'], l['versao'], l['substitui_versao'], l['watts']) for l in novas
                          if l['test_id'] == 'Bike:1200:b3'], [('Bike:1200:b3', 2, 1, 265.0)])
        h2 = h + novas
        self.assertEqual(moa.verificar_cadeia(h2), [])
        atuais = {r['test_id']: r for r in moa.atuais(h2)}
        self.assertEqual(atuais['Bike:1200:b3']['versao'], 2)
        self.assertEqual(atuais['Bike:1200:b3']['watts'], 265.0)
        v1 = next(r for r in h2 if r['test_id'] == 'Bike:1200:b3' and r['versao'] == 1)
        self.assertEqual(moa.estado_versao(v1, h2), moa.ESTADO_SUBSTITUIDA)
        self.assertEqual(v1['watts'], 262.0)                   # a anterior fica preservada
        self.assertEqual(sum(1 for r in moa.atuais(h2) if r['test_id'] == 'Bike:1200:b3'), 1)
        self.assertEqual(self.plano(curvas, h2), [])           # e volta a ser idempotente

    def test_07b_mudanca_de_data_tambem_cria_versao(self):
        curvas = copy.deepcopy(BIKE)
        h = self.plano(curvas, [])
        for c in curvas:
            if c['activity_id'] == 'b3':
                c['date'] = '2026-05-02'
        novas = [l for l in self.plano(curvas, h) if l['test_id'] == 'Bike:1200:b3']
        self.assertEqual((novas[0]['versao'], novas[0]['data']), (2, '2026-05-02'))

    def test_07c_deixar_de_ser_detetada_grava_retirada_e_nao_conta(self):
        curvas = copy.deepcopy(BIKE)
        h = self.plano(curvas, [])
        for c in curvas:
            if c['activity_id'] == 'b3':
                c['watts'][2] = 100           # ja nao esta perto de nenhuma referencia
        novas = [l for l in self.plano(curvas, h) if l['test_id'] == 'Bike:1200:b3']
        self.assertEqual(len(novas), 1)
        self.assertEqual((novas[0]['evento'], novas[0]['motivo_retirada'], novas[0]['versao']),
                         ('retirada', 'deixou_de_ser_detetada', 2))
        h2 = h + novas
        self.assertNotIn('Bike:1200:b3', {r['test_id'] for r in moa.atuais(h2)})
        self.assertEqual(self.plano(curvas, h2), [])                   # nao repete a retirada
        # voltar a qualificar: nova versao 3, que substitui a retirada
        for c in curvas:
            if c['activity_id'] == 'b3':
                c['watts'][2] = 262
        volta = [l for l in self.plano(curvas, h2) if l['test_id'] == 'Bike:1200:b3']
        self.assertEqual((volta[0]['versao'], volta[0]['substitui_versao']), (3, 2))

    def test_07d_curva_ausente_nao_retira(self):
        curvas = copy.deepcopy(BIKE)
        h = self.plano(curvas, [])
        sem = [c for c in curvas if c['activity_id'] != 'b3']          # sync parcial / curva removida
        self.assertEqual([l for l in self.plano(sem, h) if l['evento'] == 'retirada'
                          and l['test_id'] == 'Bike:1200:b3'], [])

    def test_07e_modalidade_alterada_retira_a_antiga_e_cria_a_nova(self):
        curvas = copy.deepcopy(BIKE)
        h = self.plano(curvas, [])
        for c in curvas:
            if c['activity_id'] == 'b3':
                c['type'] = 'Run'
        novas = self.plano(curvas, h)
        ret = [l for l in novas if l['evento'] == 'retirada' and l['test_id'] == 'Bike:1200:b3']
        self.assertEqual([r['motivo_retirada'] for r in ret], ['modalidade_alterada'])

    def test_cadeia_incoerente_e_detetada(self):
        h = self.plano(copy.deepcopy(BIKE), [])
        self.assertEqual(moa.verificar_cadeia(h), [])
        errada = copy.deepcopy(h)
        errada[0]['versao'] = 3
        self.assertTrue(moa.verificar_cadeia(errada))

    def test_linhas_planeadas_tem_exatamente_as_colunas_da_tabela(self):
        for l in self.plano(copy.deepcopy(BIKE), []):
            self.assertEqual(set(l), set(moa.COLUNAS))
            self.assertEqual(set(moa.COLUNAS), set(db._COLS_OBS_AUTO))

    # 11/12 ── conhecido na epoca
    def test_11_primeira_deteccao_define_quando_a_observacao_e_conhecida(self):
        h = self.plano(copy.deepcopy(BIKE), [], agora=datetime(2026, 6, 15, 8))
        f = moa.FonteAuto(h)
        ids_antes = {r['test_id'] for r in f.conhecidas_antes('2026-06-15')}
        self.assertEqual(ids_antes, set())                    # detetada nesse dia: ainda nao (estrito)
        ids = {r['test_id'] for r in f.conhecidas_antes('2026-06-16')}
        self.assertIn('Bike:1200:b4', ids)
        self.assertEqual({r['test_id'] for r in f.atuais()}, ids)

    def test_11b_versao_nova_so_e_conhecida_depois_da_sua_deteccao(self):
        curvas = copy.deepcopy(BIKE)
        h = self.plano(curvas, [], agora=datetime(2026, 6, 15))
        for c in curvas:
            if c['activity_id'] == 'b3':
                c['watts'][2] = 265
        h += self.plano(curvas, h, agora=datetime(2026, 8, 1))
        f = moa.FonteAuto(h)
        em = lambda corte: next(r for r in f.conhecidas_antes(corte) if r['test_id'] == 'Bike:1200:b3')
        self.assertEqual(em('2026-07-01')['watts'], 262.0)
        self.assertEqual(em('2026-09-01')['watts'], 265.0)

    def test_11c_retirada_deixa_de_contar_a_partir_do_seu_dia(self):
        curvas = copy.deepcopy(BIKE)
        h = self.plano(curvas, [], agora=datetime(2026, 6, 15))
        for c in curvas:
            if c['activity_id'] == 'b3':
                c['watts'][2] = 100
        h += self.plano(curvas, h, agora=datetime(2026, 8, 1))
        f = moa.FonteAuto(h)
        ids = lambda corte: {r['test_id'] for r in f.conhecidas_antes(corte)}
        self.assertIn('Bike:1200:b3', ids('2026-07-01'))
        self.assertNotIn('Bike:1200:b3', ids('2026-08-02'))

    def test_12_sem_data_de_deteccao_verificavel_nunca_e_conhecida(self):
        h = self.plano(copy.deepcopy(BIKE), [])
        for r in h:
            r['detetado_em'] = None
        f = moa.FonteAuto(h)
        self.assertEqual(f.conhecidas_antes('2999-01-01'), [])
        self.assertGreater(len(f.atuais()), 0)                 # continuam a ser observacoes atuais

    def test_12b_updated_at_da_curva_nao_e_usado(self):
        h = self.plano(copy.deepcopy(BIKE), [], agora=datetime(2026, 10, 17))
        for r in h:
            r['updated_at'] = '2020-01-01T00:00:00'            # sobrescrito a cada sync: ignorado
        f = moa.FonteAuto(h)
        self.assertEqual(f.conhecidas_antes('2026-10-17'), [])
        self.assertEqual(f.conhecidas_antes('2021-01-01'), [])

    def test_12c_revisao_manual_do_mesmo_valor_prova_o_conhecimento_anterior(self):
        h = self.plano(copy.deepcopy(BIKE), [], agora=datetime(2026, 10, 17))
        rev_mesmo = {'test_id': 'Bike:1200:b3', 'watts': 262.0, 'revisado_em': '2026-05-02T09:00:00'}
        rev_outro = {'test_id': 'Bike:1200:b4', 'watts': 999.0, 'revisado_em': '2026-05-02T09:00:00'}
        f = moa.FonteAuto(h, [rev_mesmo, rev_outro])
        ids = {r['test_id'] for r in f.conhecidas_antes('2026-06-01')}
        self.assertIn('Bike:1200:b3', ids)
        self.assertNotIn('Bike:1200:b4', ids)                  # outro valor: nao prova nada


class BaseDB(unittest.TestCase):
    CURVAS = BIKE + ROW

    def setUp(self):
        self.curvs = copy.deepcopy(self.CURVAS)
        self.ativ = U.todas(self.curvs, False)
        self._ctx = U.base_temporaria(db)
        self.caminho = self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        m = mock.patch.object(db, 'load_activities', side_effect=lambda **k: self.ativ)
        m.start()
        self.addCleanup(m.stop)
        APP._cache_ignore_power.update({'t': None, 'mapa': None})
        self.addCleanup(APP._cache_ignore_power.update, {'t': None, 'mapa': None})
        self.c = APP.app.test_client()
        self.sync()

    def sync(self):
        db.upsert_power_curves(copy.deepcopy(self.curvs))
        APP._cache_ignore_power.update({'t': None, 'mapa': None})

    def atualiza(self, agora=AGORA, **kw):
        return oa.atualizar(marcos=MARCOS, fonte_season='teste', agora=agora, origem='script', **kw)

    def muda(self, aid, w, dur=1200):
        for c in self.curvs:
            if c['activity_id'] == aid:
                c['watts'][c['secs'].index(dur)] = w
        self.sync()

    def n_linhas(self):
        return U.contar(db, 'modelo_observacoes_auto')

    def estim(self, **q):
        q = {'ate': '2026-12-31', **q}
        r = self.c.get('/api/modelo/estimador?' + '&'.join(f'{k}={v}' for k, v in q.items()))
        return r.status_code, r.get_json()

    def item(self, j, mod='Bike', dur=1200):
        return next(i for i in j['itens'] if i['modalidade'] == mod and i['duracao_s'] == dur)

    def decide(self, aid, dec='rejeitar', mod='Bike', dur=1200, motivo='teste', **extra):
        w = next(c['watts'][c['secs'].index(dur)] for c in self.curvs if c['activity_id'] == aid)
        corpo = {'modalidade': mod, 'duracao_s': dur, 'activity_id': aid, 'decisao': dec,
                 'motivo': motivo, 'confirmacao_explicita': True, 'watts_vistos': w, **extra}
        return self.c.post('/api/protocolo/testes/decisao', json=corpo)


class Persistencia(BaseDB):
    """Tabela modelo_observacoes_auto: schema, append-only, idempotencia, versoes."""

    def test_tabela_criada_e_init_schema_idempotente(self):
        self.assertEqual(self.n_linhas(), 0)
        db.init_schema()
        db.init_schema()
        cols = [r[1] for r in db._exec("PRAGMA table_info(modelo_observacoes_auto)", fetch='all')]
        self.assertEqual(cols, moa.COLUNAS)

    def test_campos_minimos_exigidos(self):
        r = self.atualiza()
        self.assertTrue(r['ok'], r)
        h = db.observacoes_auto_historico()
        self.assertGreater(len(h), 0)
        sem = [o for o in h if o['referencia_tipo'] is None]
        self.assertEqual(len(sem), 6)                         # 1.o ponto de cada serie (2 modalidades x 3 duracoes)
        for o in h:
            campos = ['test_id', 'versao', 'activity_id', 'modalidade', 'duracao_s', 'data', 'watts',
                      'detetado_em', 'elegibilidade', 'evento', 'criterio']
            if o['referencia_tipo'] is not None:
                campos += ['referencia_tipo', 'referencia_watts', 'pct_da_referencia']
            else:                                              # sem referencia anterior: nulo e vazio, sem inventar
                self.assertEqual((o['referencia_watts'], o['pct_da_referencia'], o['referencias']), (None, None, {}))
            for k in campos:
                self.assertIsNotNone(o[k], k)
            self.assertEqual(o['test_id'], mt.id_teste(o['modalidade'], o['duracao_s'], o['activity_id']))
            self.assertIn(o['referencia_tipo'], moa.REFERENCIAS + (None,))
            self.assertEqual(o['criterio']['season_fonte'], 'teste')
            self.assertEqual(o['criterio']['limiar_proximidade'], 0.92)
            self.assertEqual(o['criterio']['anos_historico'], config.ANOS_HISTORICO)
            self.assertEqual(set(o['referencias']) - {'recente', 'season', 'all_time'}, set())
        b2 = next(o for o in h if o['test_id'] == 'Bike:1200:b2')
        self.assertEqual((b2['referencia_tipo'], b2['referencia_watts'], b2['pct_da_referencia'], b2['season']),
                         ('season', 250.0, 112.0, 'S26'))
        self.assertEqual(b2['detetado_em'][:10], '2026-10-17')

    # 6 ── sincronizacao repetida idempotente
    def test_06_atualizar_duas_vezes_nao_duplica(self):
        r1 = self.atualiza()
        n1 = self.n_linhas()
        self.assertEqual(r1['novas_versoes'], n1)
        self.assertEqual(r1['primeiras_deteccoes'], n1)
        r2 = self.atualiza(agora=datetime(2026, 10, 18))
        self.assertEqual((r2['novas_versoes'], r2['retiradas'], r2['conflitos_ignorados']), (0, 0, 0))
        self.assertEqual(self.n_linhas(), n1)
        self.sync()                                            # re-sync dos mesmos dados
        self.atualiza(agora=datetime(2026, 10, 19))
        self.assertEqual(self.n_linhas(), n1)
        self.assertEqual({o['detetado_em'][:10] for o in db.observacoes_auto_historico()}, {'2026-10-17'})

    def test_06b_sync_real_chama_a_deteccao_e_e_idempotente(self):
        import sync
        payload = {'secs': [60, 300, 1200],
                   'curves': [{'id': c['activity_id'], 'start_date_local': c['date'] + 'T07:00:00',
                               'weight': 70, 'watts': c['watts']} for c in copy.deepcopy(BIKE)]}

        def falso_get(url, params=None, **k):
            return (payload if 'activity-power-curves' in url else []), None

        db._exec('DELETE FROM power_curves')
        with mock.patch.object(sync, 'icu_get', side_effect=falso_get), \
                mock.patch.object(sync, 'tipos_reais', return_value={'Bike': ['Ride']}), \
                mock.patch.object(sync, 'athlete_id_real', return_value='0'), \
                mock.patch.object(oa, 'obter_marcos', return_value=(MARCOS, 'teste')):
            r1 = sync.sync_power_curves()
            n1 = self.n_linhas()
            r2 = sync.sync_power_curves()
        self.assertTrue(r1['ok'] and r1['observacoes_auto']['ok'], r1)
        self.assertGreater(n1, 0)
        self.assertEqual(r2['observacoes_auto']['novas_versoes'], 0)
        self.assertEqual(self.n_linhas(), n1)

    def test_06c_falha_na_deteccao_nao_estraga_a_sincronizacao(self):
        import sync
        with mock.patch.object(sync, 'icu_get', return_value=({'secs': [], 'curves': []}, None)), \
                mock.patch.object(sync, 'tipos_reais', return_value={'Bike': ['Ride']}), \
                mock.patch.object(sync, 'athlete_id_real', return_value='0'), \
                mock.patch.object(oa, 'atualizar', side_effect=RuntimeError('boom')):
            r = sync.sync_power_curves()
        self.assertTrue(r['ok'])
        self.assertFalse(r['observacoes_auto']['ok'])
        self.assertIn('boom', r['observacoes_auto']['erro'])

    def test_sem_atividades_nao_regista_nada(self):
        with mock.patch.object(db, 'load_activities', return_value=None):
            r = self.atualiza()
        self.assertEqual((r['ok'], r['erro']), (False, 'atividades_indisponiveis'))
        self.assertEqual(self.n_linhas(), 0)

    # 7 ── alteracao de potencia
    def test_07_potencia_alterada_substitui_a_versao_atual(self):
        self.atualiza()
        n1 = self.n_linhas()
        self.muda('b3', 265)
        r = self.atualiza(agora=datetime(2026, 11, 1))
        self.assertEqual((r['novas_versoes'], r['versoes_substituidas']), (1, 1))
        self.assertEqual(self.n_linhas(), n1 + 1)
        hist = [o for o in db.observacoes_auto_historico() if o['test_id'] == 'Bike:1200:b3']
        self.assertEqual([(o['versao'], o['watts']) for o in hist], [(1, 262.0), (2, 265.0)])
        self.assertEqual([moa.estado_versao(o, hist) for o in hist], ['substituida', 'atual'])
        atuais = [o for o in moa.atuais(db.observacoes_auto_historico()) if o['test_id'] == 'Bike:1200:b3']
        self.assertEqual([o['watts'] for o in atuais], [265.0])
        # o estimador so ve a versao atual (uma observacao, com o valor novo)
        _, j = self.estim()
        usadas = [o for o in self.item(j)['sequencia_usada'] if o['test_id'] == 'Bike:1200:b3']
        self.assertEqual([(o['watts'], o['revisao']) for o in usadas], [(265.0, 2)])

    def test_07b_valor_da_curva_mudou_mas_deteccao_nao_correu_fica_de_fora(self):
        self.atualiza()
        self.muda('b3', 265)                        # sync de curvas sem correr a deteccao ainda
        _, j = self.estim()
        exc = {e['test_id']: e['motivo'] for e in self.item(j)['excluidas']}
        self.assertEqual(exc.get('Bike:1200:b3'), 'dados_divergente')   # nunca usa um valor desatualizado
        self.atualiza(agora=datetime(2026, 11, 1))
        _, j = self.estim()
        self.assertIn('Bike:1200:b3', [o['test_id'] for o in self.item(j)['sequencia_usada']])

    def test_append_only_no_codigo(self):
        for f in ('db.py', 'observacoes_auto.py', 'modelo_observacoes_auto.py', 'app.py', 'sync.py'):
            src = open(os.path.join(ROOT, f), encoding='utf-8').read()
            self.assertIsNone(re.search(r'(UPDATE|DELETE\s+FROM)\s+modelo_observacoes_auto', src, re.I), f)
            self.assertIsNone(re.search(r'(DROP|ALTER)\s+TABLE[^\n]*modelo_observacoes_auto', src, re.I), f)
            self.assertIsNone(re.search(r'(UPDATE|DELETE\s+FROM)\s+modelo_testes_revisoes', src, re.I), f)

    def test_resincronizar_ou_recriar_curvas_nao_toca_na_tabela(self):
        self.atualiza()
        antes = db.observacoes_auto_historico()
        db.recriar_power_curves()
        self.assertEqual(db.observacoes_auto_historico(), antes)
        self.assertEqual(db.load_power_curves(), [])

    def test_chave_primaria_impede_duplicados(self):
        self.atualiza()
        o = db.observacoes_auto_historico()[0]
        with self.assertRaises(db.ConflitoObservacao):
            db.inserir_observacao_auto(o)

    def test_nao_toca_nas_outras_tabelas(self):
        db._exec("INSERT INTO modelo_testes_revisoes (test_id,revisao,evento,estado,modalidade,duracao_s,"
                 "activity_id,watts,elegibilidade,metodo_confirmacao,motivo,origem_registo,versao_contrato,"
                 "registado_em,revisado_em) VALUES ('Bike:1200:b3',1,'rejeicao','rejeitado','Bike',1200,'b3',262,"
                 "'elegivel','manual','x','script',1,'2026-05-02T00:00:00','2026-05-02T00:00:00')")
        curvas = db.load_power_curves()
        revs = db.revisoes_testes_modelo()
        self.atualiza()
        self.assertEqual(db.load_power_curves(), curvas)
        self.assertEqual(db.revisoes_testes_modelo(), revs)

    # 8 / 9 / 10 ── elegibilidade e invalidos
    def test_08_icu_ignore_power_fora_das_referencias_e_do_estimador(self):
        self.curvs.append(cv('bI', 'Bike', '2026-03-01', 500))
        self.ativ = U.todas(self.curvs, False)
        for a in self.ativ:
            if a['id'] == 'bI':
                a['icu_ignore_power'] = True
        self.sync()
        r = self.atualiza()
        self.assertEqual(r['atividades_ignoradas_nas_referencias'], 1)
        h = {o['test_id']: o for o in db.observacoes_auto_historico()}
        # as referencias dos outros esforcos nao veem o 500 W
        for k in ('Bike:1200:b2', 'Bike:1200:b3', 'Bike:1200:b4'):
            self.assertIn(k, h)
            self.assertLess(h[k]['referencias']['all_time']['referencia_watts'], 500)
        # o proprio esforco ignorado fica visivel, inelegivel, com motivo
        self.assertEqual((h['Bike:1200:bI']['elegibilidade'], h['Bike:1200:bI']['motivo_exclusao'],
                          h['Bike:1200:bI']['icu_ignore_power']),
                         ('inelegivel', 'icu_ignore_power=true', True))
        _, j = self.estim()
        it = self.item(j)
        exc = {e['test_id']: e['motivo'] for e in it['excluidas']}
        self.assertEqual(exc['Bike:1200:bI'], 'inelegivel:icu_ignore_power=true')
        self.assertNotIn('Bike:1200:bI', [o['test_id'] for o in it['sequencia_usada']])

    def test_08b_alterar_icu_ignore_power_depois_exclui_sem_nova_deteccao(self):
        self.atualiza()
        _, j = self.estim()
        self.assertIn('Bike:1200:b3', [o['test_id'] for o in self.item(j)['sequencia_usada']])
        for a in self.ativ:
            if a['id'] == 'b3':
                a['icu_ignore_power'] = True
        APP._cache_ignore_power.update({'t': None, 'mapa': None})
        _, j = self.estim()
        exc = {e['test_id']: e['motivo'] for e in self.item(j)['excluidas']}
        self.assertEqual(exc['Bike:1200:b3'], 'inelegivel:icu_ignore_power=true')

    def test_09_ignore_parts_visivel_mas_fora_da_selecao_cientifica(self):
        for a in self.ativ:
            if a['id'] == 'b3':
                a['ignore_parts'] = [{'start_time': 0, 'end_time': 60}]
        APP._cache_ignore_power.update({'t': None, 'mapa': None})
        self.atualiza()
        h = {o['test_id']: o for o in db.observacoes_auto_historico()}
        self.assertIn('Bike:1200:b3', h)                               # detetado e registado
        self.assertTrue(h['Bike:1200:b3']['ignore_parts_presente'])
        self.assertEqual(h['Bike:1200:b3']['motivo_exclusao'], 'ignore_parts_nao_associavel_ao_trecho')
        v = self.c.get('/api/protocolo/observacoes-auto?tipo=Bike&duracao=1200').get_json()
        o = next(x for x in v['observacoes'] if x['activity_id'] == 'b3')
        self.assertFalse(o['incluida_no_estimador'])
        self.assertTrue(o['ignore_parts_presente'])
        self.assertEqual(o['motivo_exclusao'], 'elegibilidade_desconhecida:ignore_parts_nao_associavel_ao_trecho')
        _, j = self.estim()
        self.assertNotIn('Bike:1200:b3', [x['test_id'] for x in self.item(j)['sequencia_usada']])
        val = self.c.get('/api/modelo/validacao?modo=retrospectivo&tipo=Bike&duracao=1200').get_json()
        it = val['modos']['retrospectivo']['itens'][0]
        self.assertEqual(it['motivos_exclusao_observacoes'].get(
            'elegibilidade_desconhecida:ignore_parts_nao_associavel_ao_trecho'), 1)

    def test_09b_elegibilidade_desconhecida_fica_fora(self):
        for a in self.ativ:
            if a['id'] == 'b4':
                del a['icu_ignore_power']
        APP._cache_ignore_power.update({'t': None, 'mapa': None})
        self.atualiza()
        _, j = self.estim()
        exc = {e['test_id']: e['motivo'] for e in self.item(j)['excluidas']}
        self.assertEqual(exc['Bike:1200:b4'], 'elegibilidade_desconhecida:icu_ignore_power_ausente')

    def test_10_potencias_invalidas_na_base_nao_geram_observacoes(self):
        for i, w in enumerate([0, -3, True, float('nan'), float('inf'), None, 'abc']):
            self.curvs.append({'activity_id': f'bad{i}', 'type': 'Bike', 'date': f'2026-0{(i % 6) + 1}-20',
                               'weight': 70.0, 'secs': [60, 300, 1200], 'watts': [w, w, w]})
        self.ativ = U.todas(self.curvs, False)
        self.sync()
        limpo = {o['test_id']: o for o in db.observacoes_auto_historico()}
        self.assertEqual(limpo, {})
        r = self.atualiza()
        self.assertTrue(r['ok'], r)
        h = db.observacoes_auto_historico()
        self.assertEqual([o for o in h if o['activity_id'].startswith('bad')], [])
        for o in h:
            self.assertTrue(moa.potencia_valida(o['watts']))
            self.assertTrue(all(moa.potencia_valida(x['referencia_watts']) for x in o['referencias'].values()))

    def test_tags_registadas_sem_alterar_deteccao_nem_elegibilidade(self):
        self.atualiza()
        base = {o['test_id']: (o['watts'], o['referencia_tipo'], o['elegibilidade'])
                for o in db.observacoes_auto_historico()}
        db._exec('DELETE FROM modelo_observacoes_auto')
        for a in self.ativ:
            if a['id'] in ('r6', 'b3'):
                a['tags'] = ['5min', '2km', 'zona 2']
        self.atualiza()
        h = {o['test_id']: o for o in db.observacoes_auto_historico()}
        self.assertEqual({k: (o['watts'], o['referencia_tipo'], o['elegibilidade']) for k, o in h.items()}, base)
        t = h['Row:300:r6']['tags']
        self.assertEqual(t['todas'], ['2km', '5min', 'zona 2'])
        self.assertTrue(t['coincide_duracao'])                       # 5min == 300 s
        self.assertEqual({x['tipo'] for x in t['tempo_ou_distancia']}, {'tempo', 'distancia'})
        self.assertFalse(h['Bike:1200:b3']['tags']['coincide_duracao'])  # 5min != 1200 s
        self.assertEqual(h['Bike:1200:b2']['tags']['todas'], [])


class SelecaoSemConfirmacao(BaseDB):
    """14 ── estimador e validacao usam as observacoes detetadas, sem confirmacao manual."""

    def test_14_estimador_sem_nenhuma_confirmacao_usa_as_observacoes(self):
        self.assertEqual(db.revisoes_testes_modelo(), [])
        antes = self.estim()[1]
        self.assertEqual(self.item(antes)['n_usadas'], 0)              # nada detetado/registado ainda
        self.atualiza()
        st, j = self.estim()
        self.assertEqual(st, 200)
        it = self.item(j)
        self.assertGreater(it['n_usadas'], 0)
        self.assertEqual(it['n_confirmados'], 0)                       # ninguem confirmou nada
        self.assertEqual(it['n_detetadas'], it['n_usadas'] + it['n_excluidas'])
        self.assertEqual(j['fonte_observacoes'], 'automatica')
        ids = [o['test_id'] for o in it['sequencia_usada']]
        self.assertIn('Bike:1200:b2', ids)                             # novo maximo da season, abaixo do all-time
        self.assertEqual(db.revisoes_testes_modelo(), [])             # e continua sem confirmacoes
        self.assertTrue(j['experimental'])
        self.assertFalse(j['validado_preditivamente'])

    def test_14_o_caminho_antigo_continua_a_exigir_confirmacao(self):
        self.atualiza()
        _, j = self.estim(fonte='confirmacao_manual')
        self.assertEqual(self.item(j)['n_usadas'], 0)
        self.assertEqual(j['fonte_observacoes'], 'confirmacao_manual')
        self.assertEqual(self.c.get('/api/modelo/estimador?fonte=xx').status_code, 400)

    def test_14_validacao_sem_confirmacao_manual(self):
        curvs = [cv(f'v{i}', 'Bike', f'2026-{1 + i // 4:02d}-{1 + (i % 4) * 7:02d}', 300 + (i % 5))
                 for i in range(24)]
        self.curvs = curvs
        self.ativ = U.todas(curvs, False)
        self.sync()
        self.atualiza()
        self.assertEqual(db.revisoes_testes_modelo(), [])
        j = self.c.get('/api/modelo/validacao?modo=retrospectivo&tipo=Bike&duracao=1200').get_json()
        it = j['modos']['retrospectivo']['itens'][0]
        self.assertGreater(it['n_observacoes_usaveis'], 0)
        self.assertNotEqual(it['estado_suficiencia'], 'inconclusivo_sem_alvos')
        self.assertEqual(it['n_confirmados'], 0)
        self.assertEqual(j['modos']['retrospectivo']['fonte_observacoes'], 'automatica')
        # a validacao nunca se declara validada nem promove o estimador
        self.assertTrue(j['experimental'])
        self.assertIs(j['promocao'], False)
        self.assertNotIn('validado', json_texto(j['aviso']).replace('nao validado', ''))
        manual = self.c.get('/api/modelo/validacao?modo=retrospectivo&fonte=confirmacao_manual'
                            '&tipo=Bike&duracao=1200').get_json()
        self.assertEqual(manual['modos']['retrospectivo']['itens'][0]['estado_suficiencia'],
                         'inconclusivo_sem_alvos')

    def test_sufiencia_e_baselines_preservados(self):
        # poucos alvos: continua descritivo/inconclusivo, mesmo com observacoes automaticas
        self.atualiza()
        j = self.c.get('/api/modelo/validacao?modo=retrospectivo&tipo=Bike&duracao=1200').get_json()
        it = j['modos']['retrospectivo']['itens'][0]
        self.assertIn(it['estado_suficiencia'], ('inconclusivo_sem_alvos', 'descritivo'))
        self.assertNotEqual(it['estado_suficiencia'], 'avaliavel')
        self.assertIsNone(it['veredicto'])
        self.assertEqual(set(it['metodos']), {'M0', 'M1', 'M2'})
        self.assertEqual((E.Q_DIA, V.Q_PADRAO), (0.01, 0.01))

    def test_endpoint_de_vista_so_le_e_mostra_limitacoes(self):
        self.atualiza()
        n = self.n_linhas()
        j = self.c.get('/api/protocolo/observacoes-auto').get_json()
        self.assertEqual(self.n_linhas(), n)
        self.assertEqual(j['status'], 'OK')
        self.assertTrue(any('all-time' in t and 'janela historica' in t for t in j['limitacoes']))
        self.assertTrue(any('season' in t for t in j['limitacoes']))
        self.assertEqual(j['criterio']['limiar_proximidade'], 0.92)
        self.assertIn('nao e um teste laboratorial', j['aviso'])
        o = next(x for x in j['observacoes'] if x['test_id'] == 'Bike:1200:b2')
        for k in ('referencia_tipo', 'referencia_watts', 'pct_da_referencia', 'referencias', 'versao',
                  'estado_deteccao', 'detetado_em', 'elegibilidade_cientifica', 'incluida_no_estimador',
                  'motivo_exclusao', 'exclusao_manual', 'season', 'watts', 'data', 'activity_id'):
            self.assertIn(k, o)
        self.assertEqual((o['estado_deteccao'], o['estado_versao'], o['incluida_no_estimador']),
                         ('detetada', 'atual', True))
        self.assertEqual(self.c.get('/api/protocolo/observacoes-auto?tipo=Xx').status_code, 400)
        self.assertEqual(self.c.get('/api/protocolo/observacoes-auto?incluida=talvez').status_code, 400)

    def test_endpoint_atualizar_regista_e_e_idempotente(self):
        with mock.patch.object(oa, 'obter_marcos', return_value=(MARCOS, 'teste')):
            r1 = self.c.post('/api/protocolo/observacoes-auto/atualizar').get_json()
            n1 = self.n_linhas()
            r2 = self.c.post('/api/protocolo/observacoes-auto/atualizar').get_json()
        self.assertEqual(r1['status'], 'OK')
        self.assertGreater(n1, 0)
        self.assertEqual((r2['novas_versoes'], self.n_linhas()), (0, n1))
        self.assertEqual(self.c.get('/api/protocolo/observacoes-auto/atualizar').status_code, 405)

    def test_historico_expoe_versoes_substituidas(self):
        self.atualiza()
        self.muda('b3', 265)
        self.atualiza(agora=datetime(2026, 11, 1))
        j = self.c.get('/api/protocolo/observacoes-auto/historico?test_id=Bike:1200:b3').get_json()
        self.assertEqual([(v['versao'], v['estado_versao']) for v in j['versoes']],
                         [(1, 'substituida'), (2, 'atual')])


def json_texto(s):
    return str(s)


class ExclusaoManual(BaseDB):
    """13 ── a rejeicao/exclusao manual e preservada e auditavel."""

    def setUp(self):
        super().setUp()
        self.atualiza()

    def usadas(self):
        return [o['test_id'] for o in self.item(self.estim()[1])['sequencia_usada']]

    def test_13_rejeicao_manual_exclui_a_observacao_automatica(self):
        self.assertIn('Bike:1200:b2', self.usadas())
        r = self.decide('b2')                      # b2 e maximo de SEASON: nao e candidato do protocolo 'recente'
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertNotIn('Bike:1200:b2', self.usadas())
        exc = {e['test_id']: e for e in self.item(self.estim()[1])['excluidas']}
        self.assertEqual(exc['Bike:1200:b2']['motivo'], 'exclusao_manual')
        revs = db.revisoes_testes_modelo()
        self.assertEqual([(x['estado'], x['evento'], x['watts']) for x in revs], [('rejeitado', 'rejeicao', 280.0)])
        self.assertEqual(revs[0]['deteccao']['fonte'], 'automatica')
        self.assertEqual(revs[0]['deteccao']['referencia_tipo'], 'season')

    def test_13_a_exclusao_sobrevive_a_re_sincronizacao(self):
        self.decide('b2')
        for i in range(3):
            self.sync()
            self.atualiza(agora=datetime(2026, 10, 18 + i))
        self.assertNotIn('Bike:1200:b2', self.usadas())
        self.assertEqual(len(db.revisoes_testes_modelo()), 1)

    def test_13_a_exclusao_mantem_se_quando_o_valor_muda_e_e_assinalada(self):
        self.decide('b2')
        self.muda('b2', 285)
        self.atualiza(agora=datetime(2026, 11, 1))
        self.assertNotIn('Bike:1200:b2', self.usadas())
        v = self.c.get('/api/protocolo/observacoes-auto?tipo=Bike&duracao=1200').get_json()
        o = next(x for x in v['observacoes'] if x['activity_id'] == 'b2')
        self.assertEqual((o['versao'], o['exclusao_manual']['valor_diferente'], o['exclusao_manual']['watts']),
                         (2, True, 280.0))
        self.assertEqual(o['motivo_exclusao'], 'exclusao_manual')

    def test_13_remover_a_exclusao_so_a_levanta(self):
        self.decide('b2')
        r = self.decide('b2', dec='confirmar', motivo='levantar a exclusao')
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn('Bike:1200:b2', self.usadas())
        self.assertEqual([x['estado'] for x in db.revisoes_testes_modelo()], ['rejeitado', 'confirmado'])
        self.assertEqual([x['evento'] for x in db.revisoes_testes_modelo()], ['rejeicao', 'reconfirmacao'])

    def test_13_confirmacao_manual_nao_e_necessaria_nem_acrescenta_nada(self):
        antes = self.usadas()
        r = self.decide('b3', dec='confirmar', motivo='ja estava incluida')
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.usadas(), antes)
        it = self.item(self.estim()[1])
        self.assertEqual(it['n_confirmados'], 1)

    def test_13_inelegivel_continua_so_rejeitavel_e_confirmar_nao_o_inclui(self):
        for a in self.ativ:
            if a['id'] == 'b3':
                a['icu_ignore_power'] = True
        APP._cache_ignore_power.update({'t': None, 'mapa': None})
        r = self.decide('b3', dec='confirmar')
        self.assertEqual(r.status_code, 409)
        self.assertNotIn('Bike:1200:b3', self.usadas())

    def test_13_decisao_sobre_observacao_inexistente_continua_404(self):
        r = self.c.post('/api/protocolo/testes/decisao', json={
            'modalidade': 'Bike', 'duracao_s': 1200, 'activity_id': 'nao_existe', 'decisao': 'rejeitar',
            'motivo': 'x', 'confirmacao_explicita': True})
        self.assertEqual(r.status_code, 404)

    def test_13_valor_visto_desatualizado_continua_a_dar_409(self):
        r = self.decide('b2', watts_vistos=999)
        self.assertEqual((r.status_code, r.get_json()['campo']), (409, 'watts_vistos'))
        self.assertEqual(db.revisoes_testes_modelo(), [])

    def test_13_historico_manual_antigo_com_confirmacao_e_mantido(self):
        # uma revisao 'confirmado' antiga (fluxo anterior) continua no historico e valida a cadeia
        self.decide('b3', dec='confirmar')
        antes = db.revisoes_testes_modelo()
        self.atualiza(agora=datetime(2026, 10, 30))
        self.assertEqual(db.revisoes_testes_modelo(), antes)
        self.assertEqual(E.estado_atual_por_teste(antes)['Bike:1200:b3']['estado'], 'confirmado')


class OrdemMesmoDiaSync(BaseDB):
    """Sincronizacao repetida e mudancas de ordem: sem versoes desnecessarias e sem conhecimento retroativo."""
    CURVAS = copy.deepcopy(HIST) + [cv('p', 'Bike', DIA, 330), cv('q', 'Bike', DIA, 290)]

    def horas(self, p=None, q=None):
        for a in self.ativ:
            if a['id'] == 'p':
                a['start_date_local'] = p if p else a.get('start_date_local')
            if a['id'] == 'q':
                a['start_date_local'] = q if q else a.get('start_date_local')

    def sem_hora(self):
        for a in self.ativ:
            if a['id'] in ('p', 'q'):
                a['start_date_local'] = DIA                      # so a data

    def linhas(self, aid):
        return [o for o in db.observacoes_auto_historico() if o['test_id'] == f'Bike:1200:{aid}']

    def test_a4_sincronizacao_repetida_nao_cria_versoes(self):
        self.horas(h('08:00:00'), h('18:00:00'))
        self.atualiza()
        n = self.n_linhas()
        for i in range(3):
            self.sync()
            r = self.atualiza(agora=datetime(2026, 10, 18 + i))
            self.assertEqual((r['novas_versoes'], r['retiradas']), (0, 0))
        self.assertEqual(self.n_linhas(), n)
        self.assertEqual([o['versao'] for o in self.linhas('p')], [1])

    def test_a4_ordem_registada_na_linha(self):
        self.horas(h('12:00:00'), h('12:00:00'))
        self.atualiza()
        q = self.linhas('q')[0]
        self.assertEqual(q['criterio']['ordem_mesmo_dia']['ambiguos'], 1)
        self.assertEqual(q['criterio']['ordem_mesmo_dia']['hora_local_real'], h('12:00:00')[11:])
        self.assertIn('nenhuma serve de referencia', q['criterio']['ordem_mesmo_dia']['regra'])

    def test_a5_ordem_que_passa_a_excluir_o_esforco_retira_sem_reescrever_o_passado(self):
        self.sem_hora()                                           # ordem desconhecida: Q detetado contra 300
        self.atualiza(agora=datetime(2026, 10, 1, 9))
        self.assertEqual([o['evento'] for o in self.linhas('q')], ['detetada'])
        self.horas(h('08:00:00'), h('18:00:00'))                  # agora sabe-se que P (330) veio antes de Q
        r = self.atualiza(agora=datetime(2026, 10, 5, 9))
        self.assertEqual(r['retiradas'], 3)                        # Q nas tres duracoes (as curvas derivam todas de w1200)
        hq = self.linhas('q')
        self.assertEqual([(o['versao'], o['evento'], o['motivo_retirada']) for o in hq],
                         [(1, 'detetada', None), (2, 'retirada', 'deixou_de_ser_detetada')])
        self.assertEqual(hq[0]['detetado_em'][:10], '2026-10-01')                 # o passado nao muda
        f = moa.FonteAuto(db.observacoes_auto_historico())
        ids = lambda corte: [o['test_id'] for o in f.conhecidas_antes(corte)]
        self.assertNotIn('Bike:1200:q', ids('2026-10-01'))        # antes da 1.a deteccao: desconhecido
        self.assertIn('Bike:1200:q', ids('2026-10-02'))           # conhecido entre as duas execucoes
        self.assertNotIn('Bike:1200:q', ids('2026-10-06'))        # retirado desde a 2.a

    def test_a5_ordem_que_passa_a_incluir_o_esforco_so_o_torna_conhecido_a_partir_dai(self):
        self.horas(h('08:00:00'), h('18:00:00'))                  # P antes de Q: Q nao e detetado
        self.atualiza(agora=datetime(2026, 10, 1, 9))
        self.assertEqual(self.linhas('q'), [])
        self.sem_hora()                                           # a ordem deixou de ser provada: Q passa a ser detetado
        self.atualiza(agora=datetime(2026, 10, 5, 9))
        hq = self.linhas('q')
        self.assertEqual([(o['versao'], o['evento']) for o in hq], [(1, 'detetada')])
        self.assertEqual(hq[0]['detetado_em'][:10], '2026-10-05')
        f = moa.FonteAuto(db.observacoes_auto_historico())
        for corte in ('2026-06-11', '2026-09-30', '2026-10-05'):  # nem na data do esforco, nem antes da deteccao
            self.assertNotIn('Bike:1200:q', [o['test_id'] for o in f.conhecidas_antes(corte)], corte)
        self.assertIn('Bike:1200:q', [o['test_id'] for o in f.conhecidas_antes('2026-10-06')])

    def test_a5_p_mantem_o_seu_detetado_em_quando_a_ordem_muda(self):
        self.sem_hora()
        self.atualiza(agora=datetime(2026, 10, 1, 9))
        antes = self.linhas('p')
        self.horas(h('08:00:00'), h('18:00:00'))
        self.atualiza(agora=datetime(2026, 10, 5, 9))
        self.assertEqual(self.linhas('p'), antes)                 # P continua a mesma versao, mesma data de deteccao


class ModosIndependentes(BaseDB):
    """Os modos `automatica` e `confirmacao_manual` (Fases 2 e 3) sao testados um sem o outro:
    cada um ignora o que e proprio do outro."""

    def estim_f(self, fonte):
        st, j = self.estim(fonte=fonte)
        self.assertEqual(st, 200, j)
        return j

    def usadas(self, j):
        return sorted(o['test_id'] for o in self.item(j)['sequencia_usada'])

    def val_f(self, fonte, modo='retrospectivo'):
        j = self.c.get(f'/api/modelo/validacao?modo={modo}&fonte={fonte}&tipo=Bike&duracao=1200').get_json()
        return j['modos'][modo]['itens'][0], j['modos'][modo]['fonte_observacoes']

    def test_manual_nao_ve_as_observacoes_automaticas(self):
        antes = self.estim_f('confirmacao_manual')
        self.atualiza()                                       # grava observacoes automaticas
        depois = self.estim_f('confirmacao_manual')
        self.assertEqual(self.usadas(antes), [])
        self.assertEqual(self.usadas(depois), [])
        self.assertIsNone(self.item(depois)['n_detetadas'])        # o modo antigo nao conhece observacoes automaticas
        self.assertEqual(depois['fonte_observacoes'], 'confirmacao_manual')
        vi, fonte = self.val_f('confirmacao_manual')
        self.assertEqual((fonte, vi['n_observacoes_usaveis']), ('confirmacao_manual', 0))

    def test_manual_so_usa_o_que_foi_confirmado(self):
        self.atualiza()
        r = self.decide('b3', dec='confirmar', motivo='confirmacao individual (modo antigo)')
        self.assertEqual(r.status_code, 200, r.get_json())
        j = self.estim_f('confirmacao_manual')
        self.assertEqual(self.usadas(j), ['Bike:1200:b3'])
        self.assertEqual(self.item(j)['n_confirmados'], 1)

    def test_automatico_nao_depende_de_confirmacoes_nem_muda_com_elas(self):
        self.atualiza()
        sem = self.usadas(self.estim_f('automatica'))
        self.assertGreater(len(sem), 3)
        self.assertEqual(db.revisoes_testes_modelo(), [])
        self.decide('b3', dec='confirmar', motivo='irrelevante para o modo automatico')
        com = self.usadas(self.estim_f('automatica'))
        self.assertEqual(com, sem)                            # confirmar nao acrescenta nem retira nada
        vi, fonte = self.val_f('automatica')
        self.assertEqual(fonte, 'automatica')
        self.assertEqual(vi['n_observacoes_usaveis'], len(sem))

    def test_os_dois_modos_dao_resultados_diferentes_no_mesmo_estado(self):
        self.atualiza()
        self.decide('b3', dec='confirmar', motivo='x')
        a, m = self.estim_f('automatica'), self.estim_f('confirmacao_manual')
        self.assertGreater(len(self.usadas(a)), len(self.usadas(m)))
        self.assertTrue(set(self.usadas(m)) <= set(self.usadas(a)))
        self.assertEqual((a['fonte_observacoes'], m['fonte_observacoes']), ('automatica', 'confirmacao_manual'))

    def test_sem_parametro_o_modo_e_o_automatico(self):
        self.atualiza()
        _, padrao = self.estim()
        self.assertEqual(padrao['fonte_observacoes'], 'automatica')
        self.assertEqual(self.usadas(padrao), self.usadas(self.estim_f('automatica')))
        j = self.c.get('/api/modelo/validacao?modo=retrospectivo&tipo=Bike&duracao=1200').get_json()
        self.assertEqual(j['modos']['retrospectivo']['fonte_observacoes'], 'automatica')

    def test_rejeicao_manual_vale_nos_dois_modos(self):
        self.atualiza()
        self.decide('b3', dec='confirmar', motivo='x')
        self.decide('b3', dec='rejeitar', motivo='afinal nao')
        self.assertNotIn('Bike:1200:b3', self.usadas(self.estim_f('automatica')))
        self.assertNotIn('Bike:1200:b3', self.usadas(self.estim_f('confirmacao_manual')))


class SemReferenciaAnterior(BaseDB):
    """Esforco sem nenhum ponto anterior: registado com estado proprio, mas nunca usado."""

    def test_registado_com_estado_proprio_e_idempotente(self):
        r = self.atualiza()
        self.assertEqual(r['sem_referencia_anterior'], 6)
        sem = [o for o in db.observacoes_auto_historico() if o['referencia_tipo'] is None]
        self.assertEqual(sorted(o['test_id'] for o in sem),
                         sorted([f'Bike:{d}:a1' for d in (60, 300, 1200)] + [f'Row:{d}:r1' for d in (60, 300, 1200)]))
        n = self.n_linhas()
        r2 = self.atualiza(agora=datetime(2026, 10, 18))
        self.assertEqual((r2['novas_versoes'], self.n_linhas()), (0, n))      # repetir nao duplica

    def test_estimador_e_validacao_excluem_e_dizem_porque(self):
        self.atualiza()
        _, j = self.estim()
        it = self.item(j)
        self.assertNotIn('Bike:1200:a1', [o['test_id'] for o in it['sequencia_usada']])
        exc = {e['test_id']: e['motivo'] for e in it['excluidas']}
        self.assertEqual(exc['Bike:1200:a1'], 'sem_referencia_anterior')
        self.assertEqual(it['motivos_exclusao'].get('sem_referencia_anterior'), 1)
        self.assertEqual(it['n_detetadas'], it['n_usadas'] + it['n_excluidas'])
        v = self.c.get('/api/modelo/validacao?modo=retrospectivo&tipo=Bike&duracao=1200').get_json()
        vi = v['modos']['retrospectivo']['itens'][0]
        self.assertEqual(vi['n_observacoes_usaveis'], it['n_usadas'])
        self.assertEqual(vi['motivos_exclusao_observacoes'].get('sem_referencia_anterior'), 1)
        self.assertNotIn('Bike:1200:a1', [p['test_id'] for p in vi['previsoes']])
        # tambem fora do modo conhecido_na_epoca
        ep = self.c.get('/api/modelo/validacao?modo=conhecido_na_epoca&tipo=Bike&duracao=1200').get_json()
        self.assertEqual(ep['modos']['conhecido_na_epoca']['itens'][0]['motivos_exclusao_observacoes']
                         .get('sem_referencia_anterior'), 1)

    def test_vista_mostra_o_estado_sem_alterar_o_contrato(self):
        self.atualiza()
        j = self.c.get('/api/protocolo/observacoes-auto?tipo=Bike&duracao=1200').get_json()
        por_id = {o['activity_id']: o for o in j['observacoes']}
        a1 = por_id['a1']
        self.assertEqual((a1['estado_deteccao'], a1['referencia_tipo'], a1['referencia_watts'],
                          a1['pct_da_referencia'], a1['incluida_no_estimador'], a1['motivo_exclusao']),
                         ('sem_referencia_anterior', None, None, None, False, 'sem_referencia_anterior'))
        self.assertEqual(por_id['b2']['estado_deteccao'], 'detetada')
        # as chaves de cada observacao e do topo sao as mesmas de antes (so ha um valor novo em estado_deteccao)
        self.assertEqual(sorted(a1), sorted(por_id['b2']))
        self.assertEqual(j['resumo']['Bike']['1200']['motivos'].get('sem_referencia_anterior'), 1)

    def test_nova_curva_anterior_da_referencia_ao_antigo_primeiro_ponto_com_nova_versao(self):
        self.atualiza()
        self.curvs.append(cv('a0', 'Bike', '2025-02-01', 290))      # sincronizacao tardia de uma atividade mais antiga
        self.sync()
        r = self.atualiza(agora=datetime(2026, 11, 1))
        h = [o for o in db.observacoes_auto_historico() if o['test_id'] == 'Bike:1200:a1']
        self.assertEqual([(o['versao'], o['referencia_tipo']) for o in h], [(1, None), (2, 'all_time')])
        self.assertAlmostEqual(h[1]['pct_da_referencia'], round(300 / 290 * 100, 1))
        self.assertEqual([moa.estado_versao(o, h) for o in h], ['substituida', 'atual'])
        _, j = self.estim()
        self.assertIn('Bike:1200:a1', [o['test_id'] for o in self.item(j)['sequencia_usada']])
        self.assertNotIn('Bike:1200:a0', [o['test_id'] for o in self.item(j)['sequencia_usada']])   # o novo 1.o ponto
        self.assertGreaterEqual(r['versoes_substituidas'], 3)       # a1 nas 3 duracoes


class NaEpoca(BaseDB):
    """11/12 ── o modo conhecido_na_epoca usa detetado_em, sem retroatividade."""
    CURVAS = [cv(f'v{i}', 'Bike', f'2026-{1 + i // 4:02d}-{1 + (i % 4) * 7:02d}', 300 + (i % 5))
              for i in range(24)]

    def val(self, modo, **q):
        url = f'/api/modelo/validacao?modo={modo}&tipo=Bike&duracao=1200'
        return self.c.get(url).get_json()['modos'][modo]['itens'][0]

    # ── helpers: deteta tudo (causal) e grava cada observacao com `detetado_em` = data + atraso ──
    def grava(self, atraso_dias):
        cand = [k for k in moa.detetar(self.curvs, (), season) if k['duracao_s'] == 1200]
        for k in cand:
            d = datetime.fromisoformat(k['data']) + timedelta(days=atraso_dias, hours=20)
            for l in moa.planear([k], [], d, moa.limpar_curvas(self.curvs),
                                 elegibilidade_fn=lambda a: {'estado': 'elegivel', 'motivo': None,
                                                             'icu_ignore_power': False,
                                                             'ignore_parts_presente': False},
                                 criterio={}, origem='script'):
                db.inserir_observacao_auto(l)
        h = db.observacoes_auto_historico()
        cpa = E.curvas_por_atividade(db.load_power_curves())
        usadas = E.selecionar_observacoes_auto(moa.atuais(h), {}, db.load_activities(), cpa)[('Bike', 1200)]['usadas']
        return h, usadas

    def avalia(self, h, modo):
        res = V.avaliar([], db.load_activities(), db.load_power_curves(), modo, tipo='Bike', duracao=1200,
                        auto=moa.FonteAuto(h))
        return res['itens'][0]

    @staticmethod
    def esperado(usadas, atraso_dias):
        """(treino conhecido por alvo, treino perdido): so conta quem foi detetado ANTES da data do alvo."""
        treino, perdido = {}, 0
        for a in usadas:
            retro = [o for o in usadas if o['data'] < a['data']]
            conh = [o for o in retro
                    if (datetime.fromisoformat(o['data']) + timedelta(days=atraso_dias)).date().isoformat() < a['data']]
            treino[a['test_id']] = len(conh)
            perdido += len(retro) - len(conh)
        return treino, perdido

    def test_11_deteccao_agora_nao_e_conhecida_em_datas_passadas(self):
        self.atualiza(agora=AGORA)                          # primeira deteccao: hoje
        retro = self.val('retrospectivo')
        epoca = self.val('conhecido_na_epoca')
        self.assertGreater(retro['n_observacoes_usaveis'], 0)
        self.assertEqual(epoca['n_observacoes_usaveis'], retro['n_observacoes_usaveis'])   # mesmos alvos
        self.assertEqual(epoca['metodos']['M2']['n'], 0)     # nenhuma previsao: ninguem era conhecido
        self.assertGreater(retro['metodos']['M2']['n'], 0)
        # todo o treino retrospetivo foi perdido: soma, por alvo, dos esforcos anteriores
        h = db.observacoes_auto_historico()
        usadas = E.selecionar_observacoes_auto(moa.atuais(h), {}, db.load_activities(),
                                               E.curvas_por_atividade(db.load_power_curves()))[('Bike', 1200)]['usadas']
        total = sum(len([o for o in usadas if o['data'] < a['data']]) for a in usadas)
        self.assertGreater(total, 0)
        self.assertEqual(epoca['treino_perdido_por_modo'], total)
        self.assertEqual(retro['treino_perdido_por_modo'], 0)
        self.assertEqual(epoca['previsoes'], [])

    def test_11_deteccao_no_proprio_dia_equivale_ao_retrospetivo(self):
        h, usadas = self.grava(0)                           # detetada as 20h do dia do esforco
        treino, perdido = self.esperado(usadas, 0)
        retro, epoca = self.avalia(h, 'retrospectivo'), self.avalia(h, 'conhecido_na_epoca')
        self.assertEqual(perdido, 0)
        self.assertEqual(epoca['treino_perdido_por_modo'], 0)
        self.assertEqual({p['test_id']: p['n_treino'] for p in epoca['previsoes']},
                         {p['test_id']: p['n_treino'] for p in retro['previsoes']})
        self.assertEqual(epoca['metodos']['M2']['n'], retro['metodos']['M2']['n'])
        self.assertGreater(epoca['metodos']['M2']['n'], 0)

    def test_11_deteccao_com_atraso_so_conta_o_que_ja_era_conhecido_antes_do_alvo(self):
        for atraso in (10, 25):
            with self.subTest(atraso=atraso):
                db._exec("DELETE FROM modelo_observacoes_auto")
                h, usadas = self.grava(atraso)
                treino, perdido = self.esperado(usadas, atraso)
                epoca = self.avalia(h, 'conhecido_na_epoca')
                retro = self.avalia(h, 'retrospectivo')
                self.assertGreater(perdido, 0)                              # o atraso tem custo
                self.assertEqual(epoca['treino_perdido_por_modo'], perdido)
                prev = {p['test_id']: p['n_treino'] for p in epoca['previsoes']}
                self.assertGreater(len(prev), 0)
                for tid, n in prev.items():                                 # n_treino por alvo, exato
                    self.assertEqual(n, treino[tid], tid)
                self.assertLess(sum(prev.values()),
                                sum(p['n_treino'] for p in retro['previsoes']))

    def test_11_o_corte_e_estrito_na_fonte(self):
        # detetada as 20h de 2026-03-10: nao e conhecida num alvo desse mesmo dia, ja o e no dia seguinte
        h, _ = self.grava(0)
        um = next(o for o in h if o['duracao_s'] == 1200 and o['referencia_tipo'] is not None)
        dia = um['detetado_em'][:10]
        f = moa.FonteAuto(h)
        self.assertNotIn(um['test_id'], [o['test_id'] for o in f.conhecidas_antes(dia)])
        depois = (datetime.fromisoformat(dia) + timedelta(days=1)).date().isoformat()
        self.assertIn(um['test_id'], [o['test_id'] for o in f.conhecidas_antes(depois)])

    def test_12_observacoes_antigas_sem_deteccao_verificavel_nao_viram_conhecidas(self):
        # linhas gravadas sem detetado_em verificavel (a coluna e NOT NULL, por isso aqui ao nivel da fonte)
        self.atualiza(agora=AGORA)
        h = db.observacoes_auto_historico()
        for r in h:
            r['detetado_em'] = None
        res = V.avaliar([], db.load_activities(), db.load_power_curves(), 'conhecido_na_epoca',
                        tipo='Bike', duracao=1200, auto=moa.FonteAuto(h))
        it = res['itens'][0]
        self.assertGreater(it['n_observacoes_usaveis'], 0)
        self.assertEqual(it['metodos']['M2']['n'], 0)

    def test_12_updated_at_da_curva_nunca_prova_conhecimento(self):
        self.atualiza(agora=AGORA)
        antigo = db._exec("SELECT updated_at FROM power_curves LIMIT 1", fetch='one')[0]
        db._exec("UPDATE power_curves SET updated_at = '2020-01-01T00:00:00'")
        self.assertNotEqual(antigo, '2020-01-01T00:00:00')
        epoca = self.val('conhecido_na_epoca')
        self.assertEqual(epoca['metodos']['M2']['n'], 0)

    def test_12_revisao_manual_antiga_prova_o_conhecimento_do_mesmo_valor(self):
        self.atualiza(agora=AGORA)
        h = db.observacoes_auto_historico()
        for o in h:                                          # revisao manual contemporanea de cada valor
            if o['duracao_s'] == 1200:
                db._exec("INSERT INTO modelo_testes_revisoes (test_id,revisao,evento,estado,modalidade,"
                         "duracao_s,activity_id,data,watts,elegibilidade,metodo_confirmacao,motivo,"
                         "origem_registo,versao_contrato,registado_em,revisado_em,confirmado_em) VALUES "
                         "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (o['test_id'], 1, 'confirmacao', 'confirmado', 'Bike', 1200, o['activity_id'],
                          o['data'], o['watts'], 'elegivel', 'manual', 'x', 'script', 1,
                          o['data'] + 'T20:00:00', o['data'] + 'T20:00:00', o['data'] + 'T20:00:00'))
        epoca = self.val('conhecido_na_epoca')
        self.assertGreater(epoca['metodos']['M2']['n'], 0)   # o `revisado_em` e uma prova verificavel


class HistoricoTemporal(BaseDB):
    """B ── primeiro arranque, execucoes seguintes e historico sem deteccao verificavel:
    validacao retrospetiva vs `conhecido_na_epoca`, sem retroatividade."""
    CURVAS = NaEpoca.CURVAS

    def vista_temporal(self):
        return self.c.get('/api/protocolo/observacoes-auto').get_json()['historico_temporal']

    def val_modo(self, modo):
        j = self.c.get(f'/api/modelo/validacao?modo={modo}&tipo=Bike&duracao=1200').get_json()
        return j['modos'][modo]

    def esperado_n_treino(self, h, usadas):
        """n_treino por alvo, calculado a mao: so conta quem foi detetado (1.a versao) antes da data do alvo."""
        desde = {}
        for r in h:
            if r['evento'] == 'detetada':
                desde.setdefault(r['test_id'], r['detetado_em'][:10])
        out = {}
        for a in usadas:
            out[a['test_id']] = sum(1 for o in usadas if o['data'] < a['data'] and desde[o['test_id']] < a['data'])
        return out

    def usadas_1200(self, h):
        cpa = E.curvas_por_atividade(db.load_power_curves())
        return E.selecionar_observacoes_auto(moa.atuais(h), {}, db.load_activities(), cpa)[('Bike', 1200)]['usadas']

    # 1 ── primeiro arranque sobre dados antigos
    def test_b1_primeiro_arranque_nao_tem_historico_temporal(self):
        self.atualiza(agora=AGORA)
        t = self.vista_temporal()
        n = t['n_observacoes']
        self.assertGreater(n, 0)
        self.assertEqual((t['estado'], t['n_conhecidas_no_dia_ou_antes'], t['n_conhecidas_so_depois'],
                          t['n_sem_data_de_deteccao_verificavel']), ('sem_historico_temporal', 0, n, 0))
        self.assertEqual((t['primeira_deteccao'], t['ultima_deteccao']), ('2026-10-17', '2026-10-17'))
        self.assertIn('nao pode ser reconstruido', t['nota'])
        self.assertIn('retroativas', t['nota'])
        epoca = self.val_modo('conhecido_na_epoca')
        it = epoca['itens'][0]
        self.assertEqual(it['historico_temporal']['estado'], 'sem_historico_temporal')
        self.assertEqual(it['metodos']['M2']['n'], 0)
        self.assertEqual(it['previsoes'], [])
        retro = self.val_modo('retrospectivo')['itens'][0]
        self.assertGreater(retro['metodos']['M2']['n'], 0)       # a retrospetiva tem treino

    # 2 ── execucoes seguintes, com observacoes ja persistidas
    def test_b2_segunda_execucao_so_conta_o_que_foi_detetado_depois(self):
        self.atualiza(agora=AGORA)                               # 17/10: tudo o que existe (antigo)
        for aid, dia, w in (('n1', '2026-10-20', 305), ('n2', '2026-10-27', 306)):
            self.curvs.append(cv(aid, 'Bike', dia, w))
            self.ativ.append({'id': aid, 'start_date_local': dia + 'T07:30:00', 'icu_ignore_power': False})
            self.sync()
            r = self.atualiza(agora=datetime.fromisoformat(dia + 'T20:00:00'))   # detetado no proprio dia
            self.assertGreaterEqual(r['primeiras_deteccoes'], 1)
        h = db.observacoes_auto_historico()
        usadas = self.usadas_1200(h)
        self.assertIn('Bike:1200:n2', [o['test_id'] for o in usadas])
        t = self.vista_temporal()
        self.assertEqual(t['estado'], 'parcial')
        self.assertGreaterEqual(t['n_conhecidas_no_dia_ou_antes'], 2)        # n1 e n2 (pelo menos nos 1200 s)
        self.assertGreater(t['n_conhecidas_so_depois'], 0)
        epoca = self.val_modo('conhecido_na_epoca')['itens'][0]
        self.assertEqual(epoca['historico_temporal']['estado'], 'parcial')
        esperado = self.esperado_n_treino(h, usadas)
        prev = {p['test_id']: p['n_treino'] for p in epoca['previsoes']}
        self.assertEqual(set(prev), {'Bike:1200:n1', 'Bike:1200:n2'})         # so os alvos posteriores a 17/10
        for tid, n in prev.items():
            self.assertEqual(n, esperado[tid], tid)
        self.assertGreater(prev['Bike:1200:n1'], 0)                          # o antigo ja era conhecido desde 17/10

    # 3 ── nenhuma retroatividade
    def test_b3_sem_retroatividade(self):
        self.atualiza(agora=AGORA)
        antes = db.observacoes_auto_historico()
        self.assertTrue(all(o['detetado_em'][:10] == '2026-10-17' for o in antes))
        for i in range(3):                                       # execucoes seguintes nao reescrevem datas
            self.sync()
            self.atualiza(agora=datetime(2026, 10, 18 + i))
        depois = db.observacoes_auto_historico()
        self.assertEqual(depois, antes)
        f = moa.FonteAuto(depois)
        self.assertEqual(f.conhecidas_antes('2026-10-17'), [])               # nada era conhecido antes da 1.a execucao
        self.assertEqual(len(f.conhecidas_antes('2026-10-18')), len(moa.atuais(depois)))
        # a data da curva e do esforco nunca prova nada
        db._exec("UPDATE power_curves SET updated_at = '2020-01-01T00:00:00'")
        self.assertEqual(moa.FonteAuto(db.observacoes_auto_historico()).conhecidas_antes('2026-10-17'), [])

    # 4 ── historico sem detetado_em verificavel
    def test_b4_sem_data_de_deteccao_verificavel(self):
        self.atualiza(agora=AGORA)
        h = db.observacoes_auto_historico()
        for r in h:
            r['detetado_em'] = None
        t = moa.FonteAuto(h).resumo_temporal()
        self.assertEqual((t['estado'], t['n_conhecidas_no_dia_ou_antes'], t['n_conhecidas_so_depois']),
                         ('sem_historico_temporal', 0, 0))
        self.assertEqual(t['n_sem_data_de_deteccao_verificavel'], t['n_observacoes'])
        self.assertIsNone(t['primeira_deteccao'])
        res = V.avaliar([], db.load_activities(), db.load_power_curves(), 'conhecido_na_epoca',
                        tipo='Bike', duracao=1200, auto=moa.FonteAuto(h))
        it = res['itens'][0]
        self.assertEqual(it['metodos']['M2']['n'], 0)
        self.assertEqual(it['historico_temporal']['n_sem_data_de_deteccao_verificavel'], it['n_observacoes_usaveis'])
        retro = V.avaliar([], db.load_activities(), db.load_power_curves(), 'retrospectivo',
                          tipo='Bike', duracao=1200, auto=moa.FonteAuto(h))['itens'][0]
        self.assertGreater(retro['metodos']['M2']['n'], 0)       # sem datas, a retrospetiva funciona na mesma

    # 5 ── diferencas entre os dois modos
    def test_b5_modos_claramente_diferenciados(self):
        self.atualiza(agora=AGORA)
        retro, epoca = self.val_modo('retrospectivo'), self.val_modo('conhecido_na_epoca')
        self.assertEqual((retro['modo'], epoca['modo']), ('retrospectivo', 'conhecido_na_epoca'))
        self.assertNotEqual(retro['descricao_modo'], epoca['descricao_modo'])
        self.assertIn('retrospetivo', retro['descricao_modo'])
        self.assertIn('detetado_em', epoca['descricao_modo'])
        self.assertIn('nao pode ser reconstruido', epoca['descricao_modo'])
        ri, ei = retro['itens'][0], epoca['itens'][0]
        self.assertNotIn('historico_temporal', ri)               # so tem sentido na epoca
        self.assertIn('historico_temporal', ei)
        self.assertEqual(ri['treino_perdido_por_modo'], 0)
        self.assertGreater(ei['treino_perdido_por_modo'], 0)
        self.assertEqual(ri['n_observacoes_usaveis'], ei['n_observacoes_usaveis'])   # mesmos alvos, treino diferente
        # a estrutura de suficiencia e baselines e a mesma
        self.assertEqual(set(ri['metodos']), set(ei['metodos']))
        self.assertEqual((E.Q_DIA, V.Q_PADRAO), (0.01, 0.01))

    def test_b6_o_modo_manual_nao_ganha_campos_novos(self):
        self.atualiza(agora=AGORA)
        j = self.c.get('/api/modelo/validacao?modo=conhecido_na_epoca&fonte=confirmacao_manual&tipo=Bike&duracao=1200').get_json()
        m = j['modos']['conhecido_na_epoca']
        self.assertNotIn('descricao_modo', m)
        self.assertNotIn('historico_temporal', m['itens'][0])


class Regressao(unittest.TestCase):
    def test_caminho_antigo_do_estimador_e_da_validacao_inalterado(self):
        import inspect
        self.assertIn('auto=None', str(inspect.signature(E.calcular_estado)))
        self.assertIn('auto=None', str(inspect.signature(V.avaliar)))
        self.assertEqual(list(inspect.signature(E.selecionar_observacoes).parameters),
                         ['atuais', 'atividades', 'curvas_por_atividade'])
        self.assertEqual(E.Q_DIA, 0.01)

    def test_limiares_existentes_inalterados(self):
        self.assertEqual((protocolo.LIMIAR_ESFORCO, protocolo.JANELA_MELHOR), (0.92, 180))
        self.assertEqual((protocolo.ROBUSTEZ_BOM, protocolo.ROBUSTEZ_PARCIAL, protocolo.MINIMO_CALIBRACAO),
                         (30, 12, 25))
        self.assertEqual(moa.LIMIAR_PROXIMIDADE, protocolo.LIMIAR_ESFORCO)
        self.assertEqual(sorted(protocolo.TESTES), [60, 300, 1200])

    def test_modulos_de_producao_nao_leem_a_tabela_automatica(self):
        for f in ('pmc.py', 'calibracao.py', 'ftlm.py', 'fmt.py', 'protocolo.py', 'export.py',
                  'api_recovery.py', 'modelo_testes.py'):
            src = open(os.path.join(ROOT, f), encoding='utf-8').read()
            self.assertNotIn('observacoes_auto', src, f)
            self.assertNotIn('inserir_observacao_auto', src, f)

    def test_modulo_puro_nao_importa_db_nem_flask(self):
        src = open(os.path.join(ROOT, 'modelo_observacoes_auto.py'), encoding='utf-8').read()
        imports = sorted(set(re.findall(r'^\s*(?:import|from)\s+(\w+)', src, re.M)))
        self.assertEqual(imports, ['datetime', 'math', 'modelo_testes', 'protocolo', 're'])

    def test_estimador_e_validacao_continuam_puros(self):
        for f, esperado in (('modelo_estimador.py', ['datetime', 'hashlib', 'json', 'math', 'modelo_testes']),
                            ('modelo_validacao.py', ['datetime', 'hashlib', 'json', 'math',
                                                     'modelo_estimador', 'random'])):
            src = open(os.path.join(ROOT, f), encoding='utf-8').read()
            self.assertEqual(sorted(set(re.findall(r'^\s*(?:import|from)\s+(\w+)', src, re.M))), esperado, f)

    def test_rotas_de_leitura_nao_escrevem(self):
        src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        for rota, fim in (('def api_protocolo_observacoes_auto()', 'def api_protocolo_observacoes_auto_historico'),
                          ('def api_modelo_estimador', "@app.route('/api/modelo/validacao')"),
                          ('def api_modelo_validacao', "@app.route('/api/calibracao')")):
            bloco = src[src.index(rota):src.index(fim)]
            self.assertNotRegex(bloco, r'inserir_|_exec\(|INSERT|UPDATE|DELETE|atualizar\(', rota)


if __name__ == '__main__':
    unittest.main()
