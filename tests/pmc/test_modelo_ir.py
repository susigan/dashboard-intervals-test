"""Testes do nucleo IR e do otimizador (modelo_ir.py). Dados sinteticos e a folha de referencia.

A folha (tests/pmc/dados/ir_excel_referencia.json) vem do EstimarConstantes.xlsx. Os dados
sinteticos NAO sao evidencia real: servem para provar formulas, recuperacao de parametros e
os estados de insuficiencia / nao identificabilidade.
"""
import json
import math
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
import modelo_ir as ir  # noqa: E402

with open(os.path.join(HERE, 'dados', 'ir_excel_referencia.json'), encoding='utf-8') as _f:
    XL = json.load(_f)


def naive_excel(tss, p0, k1, k2, t1, t2):
    """Copia literal das formulas das celulas (E5 = E4*EXP(-1/T1)+B5, ...): referencia independente."""
    e = g = 0.0
    out = []
    for b in tss:
        e = e * math.exp(-1.0 / t1) + b
        g = g * math.exp(-1.0 / t2) + b
        out.append(p0 + k1 * e - k2 * g)
    return out


def sintetico(n_obs, sigma, semente=3, n_dias=1100, verdade=None, lag=1, ini=60):
    rng = np.random.default_rng(semente)
    carga = np.where(rng.random(n_dias) < 0.6, rng.uniform(30, 120, n_dias), 0.0)
    v = verdade or dict(p0=230.0, k1=0.05, k2=0.12, t1=45.0, t2=8.0)
    perf = ir.desempenho(carga, v['p0'], v['k1'], v['k2'], v['t1'], v['t2'], lag)
    idx = np.sort(rng.choice(np.arange(ini, n_dias), n_obs, replace=False))
    y = perf[idx] + rng.normal(0, sigma, n_obs)
    return carga, idx, y, v


class FormulasDoExcel(unittest.TestCase):
    def test_reproduz_as_261_linhas_da_folha(self):
        p = XL['parametros']
        mine = ir.desempenho(XL['tss'], p['p0'], p['k1'], p['k2'], p['t1'], p['t2'], lag_dias=0)
        np.testing.assert_allclose(mine, XL['desempenho_excel'], rtol=0, atol=1e-9)

    def test_funcao_objetivo_coincide_com_L10(self):
        p = XL['parametros']
        idx = sorted(int(k) for k in XL['testes'])
        y = [XL['testes'][str(i)] for i in idx]
        sse = ir.soma_quadrados(XL['tss'], idx, y, p, lag_dias=0)
        self.assertAlmostEqual(sse, XL['soma_quadrados_excel'], places=6)

    def test_erro_e_previsto_menos_observado(self):
        p = XL['parametros']
        e = ir.erros(XL['tss'], [45], [215.0], p, 0)       # linha 49 da folha: J49 = I49 - C49
        self.assertAlmostEqual(float(e[0]), XL['desempenho_excel'][45] - 215.0, places=9)
        self.assertLess(float(e[0]), 0)                     # a folha da -15,4 (previsto < teste)

    def test_formulas_confirmadas_no_ficheiro(self):
        f = XL['formulas']
        self.assertEqual(f['E5'], '=(E4*EXP(-1/$L$6)+B5)')
        self.assertEqual(f['G5'], '=(G4*EXP(-1/$L$7)+B5)')
        self.assertEqual(f['I5'], '=$L$3+F5-H5')
        self.assertEqual(f['J5'], '=IF(ISBLANK(C5),"",I5-C5)')
        self.assertEqual(f['L10'], '=SUMSQ(J4:J264)')

    def test_recursao_igual_a_copia_literal_das_formulas(self):
        rng = np.random.default_rng(1)
        c = np.where(rng.random(300) < 0.5, rng.uniform(0, 150, 300), 0.0)
        a = ir.desempenho(c, 200, 0.7, 1.9, 31, 6, 0)
        np.testing.assert_allclose(a, naive_excel(c, 200, 0.7, 1.9, 31, 6), atol=1e-9)


class ConvencoesDoNucleo(unittest.TestCase):
    def test_dias_sem_treino_so_decaem(self):
        c = np.zeros(40)
        c[5] = 100.0
        e, g = ir.componentes(c, 30, 7, 0)
        self.assertAlmostEqual(e[5], 100.0)
        for t in range(6, 40):
            self.assertAlmostEqual(e[t], e[t - 1] * math.exp(-1 / 30), places=10)
            self.assertAlmostEqual(g[t], g[t - 1] * math.exp(-1 / 7), places=10)
        self.assertTrue(np.all(e[:5] == 0) and np.all(g[:5] == 0))     # estado inicial 0

    def test_varias_atividades_no_mesmo_dia_somam(self):
        regs = [{'data': '2026-01-03', 'carga': 40}, {'data': '2026-01-03', 'carga': 60},
                {'data': '2026-01-05', 'carga': 10}]
        por_dia = ir.somar_por_dia(regs)
        self.assertEqual(por_dia, {'2026-01-03': 100.0, '2026-01-05': 10.0})
        c = ir.cargas_diarias(por_dia, '2026-01-01', '2026-01-06')
        self.assertEqual(c.tolist(), [0, 0, 100, 0, 10, 0])
        # equivale a uma unica atividade com a soma
        c2 = ir.cargas_diarias({'2026-01-03': 100.0, '2026-01-05': 10.0}, '2026-01-01', '2026-01-06')
        np.testing.assert_array_equal(c, c2)

    def test_cargas_invalidas_ignoradas(self):
        regs = [{'data': '2026-01-03', 'carga': None}, {'data': '2026-01-03', 'carga': float('nan')},
                {'data': 'lixo', 'carga': 5}, {'data': '2026-01-03', 'carga': True},
                {'data': '2026-01-03', 'carga': 7}]
        self.assertEqual(ir.somar_por_dia(regs), {'2026-01-03': 7.0})

    def test_sinais_k1_soma_k2_subtrai(self):
        c = np.zeros(120)
        c[10] = 100.0
        base = ir.desempenho(c, 200, 0, 0, 40, 7, 0)
        self.assertTrue(np.all(base == 200))
        so_k1 = ir.desempenho(c, 200, 0.5, 0, 40, 7, 0)
        so_k2 = ir.desempenho(c, 200, 0, 0.5, 40, 7, 0)
        self.assertTrue(np.all(so_k1[10:] > 200))
        self.assertTrue(np.all(so_k2[10:] < 200))

    def test_resposta_a_um_impulso_e_bifasica(self):
        c = np.zeros(150)
        c[0] = 100.0
        p = ir.desempenho(c, 0, 1.0, 2.0, 40, 7, 0)
        tau = np.arange(150)
        esperado = 100 * (1.0 * np.exp(-tau / 40) - 2.0 * np.exp(-tau / 7))
        np.testing.assert_allclose(p, esperado, atol=1e-9)
        self.assertLess(p[1], 0)            # a fadiga domina logo a seguir
        self.assertGreater(p[60], 0)        # depois a adaptacao fica acima de 0
        self.assertTrue(np.argmax(p) > 5)

    def test_lag_1_nao_inclui_a_carga_do_proprio_dia(self):
        c = np.zeros(60)
        c[30] = 100.0
        a0 = ir.desempenho(c, 200, 1, 2, 40, 7, 0)
        a1 = ir.desempenho(c, 200, 1, 2, 40, 7, 1)
        self.assertNotEqual(a0[30], 200.0)                 # lag 0: o dia do treino ja conta
        self.assertEqual(a1[30], 200.0)                    # lag 1: so conta a partir do dia seguinte
        np.testing.assert_allclose(a1[31:], a0[30:-1], atol=1e-12)

    def test_mudar_a_carga_de_hoje_nao_altera_o_previsor_com_lag_1(self):
        rng = np.random.default_rng(5)
        c = rng.uniform(0, 100, 80)
        d = c.copy()
        d[50] += 500
        a = ir.desempenho(c, 200, 0.1, 0.2, 40, 7, 1)
        b = ir.desempenho(d, 200, 0.1, 0.2, 40, 7, 1)
        np.testing.assert_array_equal(a[:51], b[:51])
        self.assertTrue(np.any(a[51:] != b[51:]))


class RecuperacaoDeParametros(unittest.TestCase):
    def test_sem_ruido_recupera_os_cinco_parametros(self):
        carga, idx, y, v = sintetico(80, 0.0)
        r = ir.ajustar(carga, idx, y, None, 260.0)
        self.assertIn(r['estado'], (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL))
        for k in ir.NOMES:
            self.assertAlmostEqual(r['parametros'][k], v[k], delta=0.02 * abs(v[k]) + 1e-3, msg=k)
        self.assertAlmostEqual(r['metricas']['sse'], 0.0, places=6)

    def test_com_ruido_moderado_recupera_e_e_identificavel(self):
        carga, idx, y, v = sintetico(60, 1.0, semente=11)
        r = ir.ajustar(carga, idx, y, None, 235.0)
        self.assertEqual(r['estado'], ir.EST_IDENTIFICAVEL, r['identificabilidade']['avisos'])
        self.assertAlmostEqual(r['parametros']['t1'], v['t1'], delta=0.2 * v['t1'])
        self.assertAlmostEqual(r['parametros']['t2'], v['t2'], delta=0.3 * v['t2'])
        self.assertAlmostEqual(r['parametros']['p0'], v['p0'], delta=0.05 * v['p0'])
        lo, hi = r['identificabilidade']['ic_perfil']['t1']
        self.assertLessEqual(lo, r['parametros']['t1'] + 1e-6)
        self.assertGreaterEqual(hi, r['parametros']['t1'] - 1e-6)
        self.assertGreater(hi, lo)

    def test_ic_de_perfil_tem_cobertura_razoavel_em_dados_sinteticos(self):
        # 24 sementes fixas (determinista). Nominal 95%; exige-se >= 75% para nao ser frageil
        # a amostragem, mas apanhar um IC estruturalmente estreito (antes da interpolacao: ~45%).
        dentro = {'t1': 0, 't2': 0}
        n = 24
        for s in range(300, 300 + n):
            carga, idx, y, v = sintetico(60, 1.0, semente=s)
            ic = ir.ajustar(carga, idx, y, None, 235.0)['identificabilidade']['ic_perfil']
            for k in dentro:
                dentro[k] += ic[k][0] <= v[k] <= ic[k][1]
        for k, c in dentro.items():
            self.assertGreaterEqual(c / n, 0.75, f'cobertura {k}: {c}/{n}')

    def test_p0_nao_fica_fixo_no_valor_inicial(self):
        carga, idx, y, v = sintetico(80, 0.0)
        for ini in (150.0, 230.0, 300.0):
            r = ir.ajustar(carga, idx, y, None, ini)
            self.assertAlmostEqual(r['parametros']['p0'], v['p0'], delta=5.0, msg=f'inicio {ini}')

    def test_valores_do_video_so_inicializam_nao_fixam(self):
        verdade = dict(p0=240.0, k1=0.03, k2=0.09, t1=70.0, t2=11.0)    # longe de 1/2/35/7
        carga, idx, y, v = sintetico(80, 0.0, semente=4, verdade=verdade)
        r = ir.ajustar(carga, idx, y, None, 245.0)
        self.assertNotEqual(round(r['parametros']['t1']), ir.INICIAL_VIDEO['t1'])
        for k in ('k1', 'k2', 't1', 't2'):
            self.assertAlmostEqual(r['parametros'][k], v[k], delta=0.05 * v[k] + 1e-3, msg=k)

    def test_parametros_ficam_dentro_dos_limites_configurados(self):
        carga, idx, y, v = sintetico(60, 1.0, semente=2)
        cfg = {'limites': {'t1': [20.0, 38.0], 't2': [1.0, 6.0]}}
        r = ir.ajustar(carga, idx, y, cfg, 235.0)
        p = r['parametros']
        self.assertLessEqual(p['t1'], 38.0 + 1e-9)
        self.assertLessEqual(p['t2'], 6.0 + 1e-9)
        self.assertEqual(r['limites']['t1'], [20.0, 38.0])
        # a verdade (45/8) esta fora da caixa: tem de ser dito, nao escondido
        self.assertEqual(r['estado'], ir.EST_NAO_IDENTIFICAVEL)
        self.assertTrue(any(a.startswith('parametro_no_limite') for a in r['identificabilidade']['avisos']))

    def test_determinista(self):
        carga, idx, y, _ = sintetico(50, 1.5, semente=9)
        a = ir.ajustar(carga, idx, y, None, 235.0)
        b = ir.ajustar(carga, idx, y, None, 235.0)
        self.assertEqual(a['parametros'], b['parametros'])
        self.assertEqual(a['estado'], b['estado'])

    def test_resultado_e_json_serializavel(self):
        carga, idx, y, _ = sintetico(40, 2.0, semente=6)
        json.dumps(ir.ajustar(carga, idx, y, None, 235.0))


class DadosInsuficientesEIdentificabilidade(unittest.TestCase):
    def test_poucas_observacoes(self):
        carga, idx, y, _ = sintetico(14, 0.5)
        r = ir.ajustar(carga, idx, y, None, 235.0)
        self.assertEqual(r['estado'], ir.EST_INSUFICIENTE)
        self.assertIsNone(r['parametros'])
        self.assertIn('menos_de_15_observacoes', r['motivo'])

    def test_os_9_testes_da_folha_sao_insuficientes_para_5_parametros(self):
        idx = sorted(int(k) for k in XL['testes'])
        y = [XL['testes'][str(i)] for i in idx]
        r = ir.ajustar(XL['tss'], idx, y, None, 274.0)
        self.assertEqual(r['estado'], ir.EST_INSUFICIENTE)       # a folha so converge porque fixa P0 e K1

    def test_cobertura_temporal_curta(self):
        carga, idx, y, _ = sintetico(30, 0.5, n_dias=400, ini=60)
        idx = np.sort(np.random.default_rng(1).choice(np.arange(100, 250), 30, replace=False))
        y = ir.desempenho(carga, 230, 0.05, 0.12, 45, 8, 1)[idx]
        datas = [f'2026-01-{1 + (i % 28):02d}' for i in range(30)]
        r = ir.ajustar(carga, idx, y, {'min_dias_cobertura': 500}, 235.0)
        self.assertEqual(r['estado'], ir.EST_INSUFICIENTE)
        self.assertIn('cobertura', r['motivo'])
        self.assertIsNotNone(datas)

    def test_poucas_datas_distintas(self):
        carga, _, _, _ = sintetico(20, 0.5)
        idx = np.array([100, 100, 100, 100, 200, 200, 200, 200, 300, 300, 300, 300, 400, 400, 400, 400, 500, 500, 500, 500])
        y = np.linspace(250, 270, 20)
        r = ir.ajustar(carga, idx, y, None, 255.0)
        self.assertEqual(r['estado'], ir.EST_INSUFICIENTE)
        self.assertIn('datas_distintas', r['motivo'])

    def test_sem_carga_ou_alvo_constante(self):
        _, idx, y, _ = sintetico(40, 1.0)
        r = ir.ajustar(np.zeros(1100), idx, y, None, 235.0)
        self.assertEqual(r['estado'], ir.EST_INSUFICIENTE)
        self.assertEqual(r['motivo'], 'sem_carga_no_periodo')
        carga, idx, _, _ = sintetico(40, 1.0)
        r2 = ir.ajustar(carga, idx, np.full(40, 280.0), None, 280.0)
        self.assertEqual(r2['estado'], ir.EST_INSUFICIENTE)
        self.assertEqual(r2['motivo'], 'alvo_sem_variacao')

    def test_carga_constante_nao_e_identificavel(self):
        carga = np.full(1100, 60.0)                       # E e G viram quase a mesma curva constante
        v = dict(p0=230.0, k1=0.05, k2=0.12, t1=45.0, t2=8.0)
        idx = np.sort(np.random.default_rng(2).choice(np.arange(200, 1100), 40, replace=False))
        y = ir.desempenho(carga, v['p0'], v['k1'], v['k2'], v['t1'], v['t2'], 1)[idx]
        y = y + np.random.default_rng(3).normal(0, 0.5, 40)
        r = ir.ajustar(carga, idx, y, None, 235.0)
        self.assertEqual(r['estado'], ir.EST_NAO_IDENTIFICAVEL)
        self.assertTrue(r['identificabilidade']['avisos'])

    def test_parametro_fora_dos_limites_da_verdade_fica_no_limite(self):
        verdade = dict(p0=230.0, k1=0.05, k2=0.12, t1=45.0, t2=0.5)     # T2 abaixo do minimo 1 d
        carga, idx, y, _ = sintetico(60, 0.3, semente=8, verdade=verdade)
        r = ir.ajustar(carga, idx, y, None, 235.0)
        self.assertEqual(r['estado'], ir.EST_NAO_IDENTIFICAVEL)
        self.assertIn('parametro_no_limite:t2:inferior', r['identificabilidade']['avisos'])

    def test_amostra_pequena_e_ruidosa_nao_e_apresentada_como_identificavel(self):
        carga, idx, y, _ = sintetico(16, 6.0, semente=21)
        r = ir.ajustar(carga, idx, y, None, 235.0)
        self.assertNotEqual(r['estado'], ir.EST_IDENTIFICAVEL, r['identificabilidade'])

    def test_limites_por_defeito_nao_sao_os_do_excel(self):
        L = ir.CONFIG_PADRAO['limites']
        self.assertNotEqual(L['t1'], [25, 50])
        self.assertNotEqual(L['t2'], [4, 10])
        self.assertLess(L['t2'][1], L['t1'][0])           # faixas disjuntas: T1 lenta, T2 rapida
        self.assertEqual(L['k1'][0], 0.0)                 # convencao de sinais
        self.assertEqual(L['k2'][0], 0.0)
        r_xl = XL['parametros']
        self.assertGreaterEqual(r_xl['t1'], L['t1'][0])   # o exemplo cabe, sem ser teto
        self.assertLessEqual(r_xl['t1'], L['t1'][1])
        self.assertLessEqual(r_xl['k2'], L['k2'][1])


class InicializacaoDeP0(unittest.TestCase):
    O = [{'test_id': 'a', 'data': '2025-03-01', 'watts': 250.0},
         {'test_id': 'b', 'data': '2025-11-10', 'watts': 270.0},
         {'test_id': 'c', 'data': '2025-12-20', 'watts': 280.0},
         {'test_id': 'd', 'data': '2024-06-01', 'watts': 200.0},
         {'test_id': 'e', 'data': '2026-02-01', 'watts': 300.0}]

    def test_ultimo_mmp_do_ano_anterior(self):
        v, origem, det = ir.p0_inicial(self.O, '2026-06-15')
        self.assertEqual((v, origem), (280.0, 'ano_anterior'))
        self.assertEqual(det['data'], '2025-12-20')

    def test_nao_usa_observacoes_de_outros_anos(self):
        v, origem, _ = ir.p0_inicial(self.O, '2025-06-15')        # ano anterior = 2024
        self.assertEqual((v, origem), (200.0, 'ano_anterior'))

    def test_varios_no_ultimo_dia_usa_a_media_sem_inventar_ordem(self):
        obs = self.O + [{'test_id': 'f', 'data': '2025-12-20', 'watts': 290.0}]
        v, origem, det = ir.p0_inicial(obs, '2026-06-15')
        self.assertEqual(v, 285.0)
        self.assertEqual(det['n_no_dia'], 2)

    def test_sem_historico_do_ano_anterior_usa_as_primeiras_observacoes(self):
        v, origem, det = ir.p0_inicial(self.O, '2030-01-01')      # ano anterior sem dados
        self.assertEqual(origem, 'primeiras_observacoes')
        self.assertEqual(v, 200.0 if False else float(np.median([200.0, 250.0, 270.0])))
        self.assertEqual(det['n'], 3)

    def test_sem_observacoes(self):
        v, origem, det = ir.p0_inicial([], '2026-01-01')
        self.assertEqual((v, origem), (None, None))
        self.assertEqual(det['motivo'], 'sem_observacoes')

    def test_ignora_registos_invalidos(self):
        obs = [{'test_id': 'x', 'data': None, 'watts': 300.0}, {'test_id': 'y', 'data': '2025-05-01', 'watts': 'a'},
               {'test_id': 'z', 'data': '2025-05-02', 'watts': 251.0}]
        v, origem, _ = ir.p0_inicial(obs, '2026-01-01')
        self.assertEqual(v, 251.0)


class PrevisaoSemFugaTemporal(unittest.TestCase):
    def _cenario(self):
        rng = np.random.default_rng(12)
        d0 = '2024-01-01'
        n = 900
        carga = np.where(rng.random(n) < 0.6, rng.uniform(30, 120, n), 0.0)
        v = dict(p0=230.0, k1=0.05, k2=0.12, t1=45.0, t2=8.0)
        perf = ir.desempenho(carga, v['p0'], v['k1'], v['k2'], v['t1'], v['t2'], 1)
        from datetime import date, timedelta
        base = date.fromisoformat(d0)
        dia = lambda i: (base + timedelta(days=int(i))).isoformat()          # noqa: E731
        cpd = {dia(i): float(carga[i]) for i in range(n) if carga[i] > 0}
        idx = np.sort(rng.choice(np.arange(120, n), 45, replace=False))
        obs = [{'test_id': f't{i}', 'data': dia(i), 'watts': float(perf[i] + rng.normal(0, 1.0))} for i in idx]
        return obs, cpd, v, dia

    def test_prever_usa_so_cargas_anteriores_ao_alvo(self):
        obs, cpd, v, dia = self._cenario()
        alvo = obs[30]['data']
        p = ir.prever(cpd, v, alvo, 1)
        futuro = {**cpd, **{k: 999.0 for k in cpd if k >= alvo}}              # carga do dia e futura alterada
        self.assertEqual(ir.prever(futuro, v, alvo, 1), p)
        # coincide com a serie completa a lag 1 (que so usa ate ao dia anterior)
        from datetime import date
        d0 = min(cpd)
        c = ir.cargas_diarias(cpd, d0, max(cpd))
        i = (date.fromisoformat(alvo) - date.fromisoformat(d0)).days
        self.assertAlmostEqual(p, float(ir.desempenho(c, v['p0'], v['k1'], v['k2'], v['t1'], v['t2'], 1)[i]), places=9)

    def test_avaliar_preditivo_ignora_observacoes_futuras_no_treino(self):
        obs, cpd, v, dia = self._cenario()
        alvos = obs[32:34]
        sujo = lambda a: obs                                                   # devolve TUDO, inclusive futuro e o alvo   # noqa: E731
        limpo = lambda a: [o for o in obs if o['data'] < a['data']]            # noqa: E731
        a = ir.avaliar_preditivo(alvos, sujo, cpd)
        b = ir.avaliar_preditivo(alvos, limpo, cpd)
        for x, y in zip(a, b):
            self.assertEqual(x['ir'], y['ir'])
            self.assertEqual(x['n_treino'], y['n_treino'])
            self.assertTrue(all(o['data'] < x['data'] for o in limpo(x)))

    def test_alterar_o_valor_do_alvo_ou_do_futuro_nao_muda_a_previsao(self):
        obs, cpd, v, dia = self._cenario()
        alvo = obs[34]
        base = ir.avaliar_preditivo([alvo], lambda a: [o for o in obs if o['data'] < a['data']], cpd)[0]
        obs2 = [dict(o, watts=o['watts'] + 80) if o['data'] >= alvo['data'] else o for o in obs]
        alvo2 = dict(alvo, watts=alvo['watts'] + 80)
        cpd2 = {**cpd, **{k: 777.0 for k in cpd if k >= alvo['data']}}
        alt = ir.avaliar_preditivo([alvo2], lambda a: [o for o in obs2 if o['data'] < a['data']], cpd2)[0]
        self.assertEqual(base['ir'], alt['ir'])
        self.assertNotEqual(base['y'], alt['y'])                              # so o observado muda

    def test_treino_so_com_dados_anteriores_a_cada_alvo_e_ordem_crescente(self):
        obs, cpd, v, dia = self._cenario()
        vistos = []

        def treino(a):
            t = [o for o in obs if o['data'] < a['data']]
            vistos.append((a['data'], max((o['data'] for o in t), default=None)))
            return t
        res = ir.avaliar_preditivo(obs[30:36], treino, cpd)
        self.assertTrue(all(m is None or m < d for d, m in vistos))
        self.assertEqual([r['data'] for r in res], sorted(r['data'] for r in res))

    def test_preditivo_recusa_lag_zero(self):
        obs, cpd, _, _ = self._cenario()
        with self.assertRaises(ValueError):
            ir.avaliar_preditivo(obs[30:31], lambda a: obs, cpd, {'lag_dias': 0})

    def test_sem_historico_nao_preve(self):
        obs, cpd, _, _ = self._cenario()
        r = ir.avaliar_preditivo(obs[:1], lambda a: [], cpd)[0]
        self.assertIsNone(r['ir'])
        self.assertEqual(r['motivo'], 'sem_historico')

    def test_pouco_treino_nao_preve_em_vez_de_inventar(self):
        obs, cpd, _, _ = self._cenario()
        r = ir.avaliar_preditivo(obs[10:11], lambda a: [o for o in obs if o['data'] < a['data']], cpd)[0]
        self.assertIsNone(r['ir'])
        self.assertEqual(r['estado_ajuste'], ir.EST_INSUFICIENTE)

    def test_ajuste_historico_e_previsao_sao_coisas_diferentes(self):
        obs, cpd, v, _ = self._cenario()
        r = ir.ajustar_observacoes(obs, cpd, obs[-1]['data'])
        self.assertEqual(r['metricas']['tipo'], 'ajuste_historico_dentro_da_amostra')
        self.assertNotIn('previsao', json.dumps(r['metricas']).lower().replace('ajuste_historico', ''))


class CurvasDeInfluenciaNucleo(unittest.TestCase):
    P = dict(p0=230.0, k1=0.05, k2=0.12, t1=45.0, t2=8.0)

    def cargas(self, n=300, semente=2):
        rng = np.random.default_rng(semente)
        return np.where(rng.random(n) < 0.5, rng.uniform(20, 140, n), 0.0)

    def test_liquida_e_a_equacao_do_modelo_p0_mais_positiva_menos_negativa(self):
        for lag in (0, 1, 2):
            c = self.cargas()
            r = ir.curvas_influencia(c, self.P, lag)
            np.testing.assert_allclose(r['liquida'], ir.desempenho(c, lag_dias=lag, **self.P), atol=1e-12)
            np.testing.assert_allclose(r['liquida'], self.P['p0'] + r['positiva'] - r['negativa'], atol=1e-12)
            self.assertTrue(np.all(r['positiva'] >= 0) and np.all(r['negativa'] >= 0))

    def test_soma_das_contribuicoes_individuais_e_a_resposta_agregada(self):
        for lag in (0, 1, 3):
            c = self.cargas()
            r = ir.curvas_influencia(c, self.P, lag)
            soma = {k: np.zeros(len(c)) for k in ('positiva', 'negativa', 'liquida')}
            for i, v in enumerate(c):
                if v > 0:
                    x = ir.contribuicao_atividade(len(c), i, v, self.P, lag)
                    for k in soma:
                        soma[k] += x[k]
            np.testing.assert_allclose(soma['positiva'], r['positiva'], atol=1e-9)
            np.testing.assert_allclose(soma['negativa'], r['negativa'], atol=1e-9)
            np.testing.assert_allclose(soma['liquida'], r['liquida'] - self.P['p0'], atol=1e-9)

    def test_varias_atividades_no_mesmo_dia_somam_como_uma_so(self):
        n = 60
        a = ir.contribuicao_atividade(n, 10, 40.0, self.P, 1)
        b = ir.contribuicao_atividade(n, 10, 25.0, self.P, 1)
        junto = ir.contribuicao_atividade(n, 10, 65.0, self.P, 1)
        np.testing.assert_allclose(a['liquida'] + b['liquida'], junto['liquida'], atol=1e-12)

    def test_contribuicao_igual_a_resposta_de_um_impulso_unico(self):
        n, i, L = 80, 12, 25.0
        c = np.zeros(n)
        c[i] = L
        for lag in (0, 1):
            x = ir.contribuicao_atividade(n, i, L, self.P, lag)
            r = ir.curvas_influencia(c, self.P, lag)
            np.testing.assert_allclose(x['liquida'], r['liquida'] - self.P['p0'], atol=1e-12)

    def test_contribuicao_e_zero_antes_do_inicio_do_efeito_e_sem_fuga_do_futuro(self):
        n = 50
        for lag in (0, 1, 2):
            x = ir.contribuicao_atividade(n, 20, 50.0, self.P, lag)
            self.assertTrue(np.all(x['liquida'][: 20 + lag] == 0))
            self.assertGreater(x['positiva'][20 + lag], 0)
        # mudar a carga de um dia nao altera a curva nos dias anteriores (nem no proprio com lag 1)
        c = self.cargas(120)
        c[70] = 50.0
        c2 = c.copy()
        c2[70:] *= 3
        a, b = ir.curvas_influencia(c, self.P, 1), ir.curvas_influencia(c2, self.P, 1)
        np.testing.assert_array_equal(a['liquida'][:71], b['liquida'][:71])
        c3 = c.copy()
        c3[70:] *= 3
        a0, b0 = ir.curvas_influencia(c, self.P, 0), ir.curvas_influencia(c3, self.P, 0)
        self.assertNotEqual(a0['liquida'][70], b0['liquida'][70])             # lag 0: o proprio dia conta

    def test_atividade_fora_do_intervalo_ou_sem_carga_nao_contribui(self):
        for i, v in ((-1, 10.0), (99, 10.0), (3, 0.0)):
            x = ir.contribuicao_atividade(30, i, v, self.P, 1)
            self.assertTrue(np.all(x['liquida'] == 0))

    def test_decaimento_exponencial_e_valor_de_pico(self):
        x = ir.contribuicao_atividade(100, 0, 100.0, self.P, 1)
        self.assertAlmostEqual(x['positiva'][1], 100 * self.P['k1'], places=12)
        self.assertAlmostEqual(x['negativa'][1], 100 * self.P['k2'], places=12)
        self.assertAlmostEqual(x['positiva'][11] / x['positiva'][1], math.exp(-10 / self.P['t1']), places=12)
        self.assertAlmostEqual(x['negativa'][11] / x['negativa'][1], math.exp(-10 / self.P['t2']), places=12)
        # com K2 > K1 o efeito liquido imediato e negativo (fadiga) e depois passa a positivo (forma)
        self.assertLess(x['liquida'][1], 0)
        self.assertGreater(x['liquida'][60], 0)

    def test_folha_do_excel_decomposta_reproduz_a_coluna_I(self):
        p = XL['parametros']
        c = np.array(XL['tss'], float)
        r = ir.curvas_influencia(c, p, 0)
        np.testing.assert_allclose(r['liquida'], XL['desempenho_excel'], atol=1e-9)
        soma = np.zeros(len(c))
        for i, v in enumerate(c):
            if v > 0:
                soma += ir.contribuicao_atividade(len(c), i, v, p, 0)['liquida']
        np.testing.assert_allclose(soma + p['p0'], XL['desempenho_excel'], atol=1e-7)


class Assinatura(unittest.TestCase):
    def test_estavel_e_sensivel(self):
        a = {'x': [1, 2], 'y': {'k': 1.5}}
        self.assertEqual(ir.assinatura(a), ir.assinatura({'y': {'k': 1.5}, 'x': [1, 2]}))
        self.assertNotEqual(ir.assinatura(a), ir.assinatura({'x': [1, 2], 'y': {'k': 1.6}}))


if __name__ == '__main__':
    unittest.main()
