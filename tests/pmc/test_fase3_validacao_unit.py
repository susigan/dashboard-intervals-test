"""Fase 3 — validacao temporal (modelo_validacao.py): pipeline puro, offline, deterministico.

SEPARACAO: todos os dados aqui sao SINTETICOS e servem para testar o PIPELINE (vazamento,
contas, determinismo, suficiencia). Nao demonstram validade preditiva no atleta real: nao
existem observacoes reais neste ambiente.

    python -m pytest tests/pmc/test_fase3_validacao_unit.py
"""
import copy
import math
import os
import random
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
HERE = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import modelo_estimador as E  # noqa: E402
import modelo_validacao as V  # noqa: E402
import test_fase2_estimador_unit as T  # noqa: E402

D0 = date(2025, 1, 1)


def dia(i, passo=14):
    return (D0 + timedelta(days=passo * i)).isoformat()


def serie(valores, mod='Bike', dur=300, passo=14, prefixo='a', **kw):
    return [T.rev(mod, dur, f'{prefixo}{i}', dia(i, passo), v, **kw) for i, v in enumerate(valores)]


def passeio(n, ruido, deriva, seed, passo=14, base=300.0, mod='Bike', dur=300, prefixo='a'):
    r = random.Random(seed)
    mu, out = base, []
    for i in range(n):
        mu += r.gauss(0, deriva)
        out.append(T.rev(mod, dur, f'{prefixo}{i}', dia(i, passo), round(mu + r.gauss(0, ruido), 4)))
    return out


def valida(revs, modos=('retrospectivo',), atividades='auto', curvas='auto', **kw):
    if atividades == 'auto':
        atividades = T.ativ(*{r['activity_id'] for r in revs})
    if curvas == 'auto':
        curvas = T.curvas_de(revs)
    return V.validar(revs, atividades, curvas, modos=modos, **kw)


def it(res, modo='retrospectivo', mod='Bike', dur=300):
    return next(i for i in res['modos'][modo]['itens'] if i['modalidade'] == mod and i['duracao_s'] == dur)


class MetricasReferencia(unittest.TestCase):
    """Calculos de referencia feitos a mao."""

    def test_mae_rmse_erro_medio(self):
        m = V.metricas([1.0, -2.0, 3.0, -4.0])
        self.assertEqual(m['n'], 4)
        self.assertAlmostEqual(m['mae'], 2.5)
        self.assertAlmostEqual(m['rmse'], math.sqrt((1 + 4 + 9 + 16) / 4))
        self.assertAlmostEqual(m['erro_medio'], -0.5)

    def test_sinal_do_erro_previsao_menos_observado(self):
        r = it(valida(serie([300, 300, 300, 330])))        # M1 preve 300, observado 330
        prev = r['previsoes'][-1]
        self.assertEqual((prev['y'], prev['M1']), (330.0, 300.0))
        self.assertLess(r['metodos']['M1']['erro_medio'], 0)       # subestima

    def test_vazio(self):
        self.assertEqual(V.metricas([])['mae'], None)

    def test_diferenca_negativa_favorece_o_primeiro(self):
        d = V.diferenca([1.0, 1.0, 1.0], [2.0, 2.0, 2.0], 's', com_ic=False)
        self.assertAlmostEqual(d['d_mae'], -1.0)
        self.assertAlmostEqual(d['d_rmse'], -1.0)
        self.assertAlmostEqual(d['d_mae_relativo'], -0.5)
        self.assertEqual(d['motivo_sem_ic'], 'amostra_insuficiente')
        self.assertIsNone(d['ic95_d_mae'])

    def test_diferenca_mista_mae_e_rmse(self):
        # e2 = [0, 4], ex = [2, 2]: MAE igual (2), RMSE de e2 maior
        d = V.diferenca([0.0, 4.0], [2.0, 2.0], 's', com_ic=False)
        self.assertAlmostEqual(d['d_mae'], 0.0)
        self.assertGreater(d['d_rmse'], 0)

    def test_autocorrelacao(self):
        self.assertIsNone(V.autocorrelacao_lag1([1.0] * 5))
        x = [1.0, -1.0] * 10
        self.assertLess(V.autocorrelacao_lag1(x), -0.8)
        self.assertGreater(V.autocorrelacao_lag1([float(i) for i in range(20)]), 0.8)

    def test_bloco(self):
        self.assertEqual([V.comprimento_bloco(n) for n in (1, 8, 27, 64)], [1, 2, 3, 4])

    def test_bootstrap_em_blocos_deterministico_e_coerente(self):
        e2 = [1.0, -1.0, 2.0, -2.0, 1.5] * 6
        ex = [2.0, 2.0, 3.0, -3.0, 2.5] * 6
        a, b = V.ic_bloco(e2, ex, 'x'), V.ic_bloco(e2, ex, 'x')
        self.assertEqual(a, b)
        self.assertNotEqual(a, V.ic_bloco(e2, ex, 'y'))
        self.assertLess(a['ic95_d_mae'][1], 0)                    # M2 claramente melhor
        # erros identicos -> diferenca exatamente zero em todas as replicas
        z = V.ic_bloco(e2, e2, 'x')
        self.assertEqual((z['ic95_d_mae'], z['ic95_d_rmse']), ([0.0, 0.0], [0.0, 0.0]))


class BootstrapAmplitude(unittest.TestCase):
    def test_largura_do_ic95_corresponde_ao_erro_padrao(self):
        # SINTETICO: erros independentes -> largura ~ 3.92 * sd(d)/sqrt(n) (bloco pequeno pouco altera)
        rnd = random.Random(5)
        n = 400
        e2 = [rnd.gauss(0, 1) for _ in range(n)]
        ex = [rnd.gauss(0, 1) for _ in range(n)]
        d = [abs(a) - abs(b) for a, b in zip(e2, ex)]
        m = sum(d) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1))
        ic = V.ic_bloco(e2, ex, 'amp')['ic95_d_mae']
        razao = (ic[1] - ic[0]) / (3.92 * sd / math.sqrt(n))
        self.assertTrue(0.8 < razao < 1.3, razao)
        self.assertTrue(ic[0] < m < ic[1])


class BaselinesCorretos(unittest.TestCase):
    def test_m0_media_expansiva_m1_ultimo_valor(self):
        r = it(valida(serie([100, 200, 400, 300, 500])))
        p = {x['test_id']: x for x in r['previsoes']}
        ids = [f'Bike:300:a{i}' for i in range(5)]
        self.assertEqual([p[i]['M0'] for i in ids[1:]], [100.0, 150.0, 700 / 3, 250.0])
        self.assertEqual([p[i]['M1'] for i in ids[1:]], [100.0, 200.0, 400.0, 300.0])
        self.assertEqual([p[i]['y'] for i in ids[1:]], [200.0, 400.0, 300.0, 500.0])
        # M2 com 1 observacao de treino = esse valor; depois move-se entre M0 e M1/obs
        self.assertEqual(p[ids[1]]['M2'], 100.0)

    def test_m2_e_o_nivel_da_fase_2_com_q_predefinido(self):
        revs = serie([300, 310, 295, 320, 305])
        r = it(valida(revs))
        treino = [{'test_id': x['test_id'], 'activity_id': x['activity_id'], 'hora_local': None,
                   'data': x['data'], 'watts': x['watts']} for x in revs[:4]]
        esperado = E.estimar_serie(treino, revs[4]['data'], 0.01)['resumo']['nivel_w']
        self.assertEqual(r['previsoes'][-1]['M2'], esperado)

    def test_o_alvo_nunca_e_usado_para_se_prever_a_si_proprio(self):
        revs = serie([300, 300, 300, 300, 900])
        r = it(valida(revs))
        ult = r['previsoes'][-1]
        self.assertEqual(ult['M0'], 300.0)         # a media nao inclui o 900
        self.assertEqual(ult['M1'], 300.0)
        self.assertLess(ult['M2'], 400.0)

    def test_mesmas_origens_e_alvos_nos_tres_metodos(self):
        r = it(valida(passeio(25, 5, 1, 1)))
        self.assertEqual({r['metodos'][m]['n'] for m in ('M0', 'M1', 'M2')}, {r['n_alvos']})
        self.assertEqual(r['n_alvos'], 24)
        self.assertEqual(r['origens_excluidas'], {'sem_historico': 1})
        self.assertEqual(r['n_origens_excluidas'], 1)
        self.assertEqual(r['n_observacoes_usaveis'], 25)

    def test_primeira_observacao_nao_tem_previsao_e_e_contada(self):
        r = it(valida(serie([300])))
        self.assertEqual((r['n_alvos'], r['origens_excluidas']), (0, {'sem_historico': 1}))
        self.assertEqual(r['estado_suficiencia'], V.EST_SEM_ALVOS)


class SemFuga(unittest.TestCase):
    def test_cada_previsao_so_usa_observacoes_anteriores(self):
        revs = passeio(15, 5, 1, 4)
        vistos = []
        real = E.estimar_serie

        def espia(obs, ate=None, q=E.Q_DIA):
            vistos.append((ate, [o['data'] for o in obs]))
            return real(obs, ate, q)
        with mock.patch.object(E, 'estimar_serie', side_effect=espia):
            valida(revs)
        self.assertTrue(vistos)
        for ate, datas in vistos:
            self.assertTrue(all(d < ate for d in datas), (ate, datas))

    def test_filtro_defensivo_ignora_treino_futuro_passado_pelo_chamador(self):
        revs = serie([300, 310, 320, 999])
        obs = [{'test_id': r['test_id'], 'activity_id': r['activity_id'], 'hora_local': None,
                'data': r['data'], 'watts': r['watts']} for r in revs]
        p, _ = V.previsoes_metodos(obs, obs[2]['data'])           # inclui 320 (mesma data) e 999 (futuro)
        self.assertEqual(p['n_treino'], 2)
        self.assertEqual(p['M0'], 305.0)
        self.assertEqual(p['M1'], 310.0)

    def test_acrescentar_observacoes_futuras_nao_altera_previsoes_passadas(self):
        revs = passeio(30, 5, 1.5, 7)
        curto, longo = it(valida(revs[:20])), it(valida(revs))
        self.assertEqual(curto['previsoes'], longo['previsoes'][:len(curto['previsoes'])])

    def test_alterar_valores_futuros_nao_altera_previsoes_passadas(self):
        revs = passeio(30, 5, 1.5, 7)
        alt = copy.deepcopy(revs)
        for r in alt[20:]:
            r['watts'] *= 3
        a, b = it(valida(revs)), it(valida(alt, curvas=T.curvas_de(alt)))
        self.assertEqual(a['previsoes'][:19], b['previsoes'][:19])      # alvos 1..19 (indices 0..18)
        self.assertNotEqual(a['previsoes'][-1], b['previsoes'][-1])

    def test_selecao_de_q_aninhada_nao_usa_o_futuro(self):
        revs = passeio(30, 5, 1.5, 9)
        alt = copy.deepcopy(revs)
        for r in alt[20:]:
            r['watts'] += 500
        obs = lambda rs: [{'test_id': r['test_id'], 'activity_id': r['activity_id'], 'hora_local': None,
                           'data': r['data'], 'watts': r['watts']} for r in rs]
        alvo = revs[20]['data']
        self.assertEqual(V.escolher_q(obs(revs), alvo), V.escolher_q(obs(alt), alvo))

    def test_sse_incremental_equivale_ao_calculo_por_origem(self):
        t = sorted(obs := [{'test_id': r['test_id'], 'activity_id': r['activity_id'], 'hora_local': None,
                            'data': r['data'], 'watts': r['watts']} for r in passeio(25, 6, 2, 3, passo=5)],
                   key=E.chave_ordem)
        for q in V.GRELHA_Q:
            sse, cnt = V._sse_walk_forward(t, q)
            ref, c2 = 0.0, 0
            for j in range(1, len(t)):
                prior = [o for o in t[:j] if o['data'] < t[j]['data']]
                if prior:
                    ref += (E.estimar_serie(prior, t[j]['data'], q)['resumo']['nivel_w'] - t[j]['watts']) ** 2
                    c2 += 1
            self.assertAlmostEqual(sse, ref, places=6)
            self.assertEqual(cnt, c2)


class DadosInvalidosERevisoes(unittest.TestCase):
    def test_invalidos_rejeitados_e_inelegiveis_nao_sao_alvo_nem_treino(self):
        bons = serie([300, 305, 310, 315, 320], prefixo='b')
        ruins = [T.rev('Bike', 300, 'r1', dia(1), 0.0), T.rev('Bike', 300, 'r2', dia(2), float('nan')),
                 T.rev('Bike', 300, 'r3', dia(3), 999.0, estado='rejeitado'),
                 T.rev('Bike', 300, 'r4', dia(4), 777.0)]
        ativ = T.ativ(*{r['activity_id'] for r in bons + ruins[:3]}) + T.ativ('r4', ignore=True)
        ref = it(valida(bons))
        r = it(valida(bons + ruins, atividades=ativ, curvas=T.curvas_de(bons + ruins)))
        self.assertEqual(r['previsoes'], ref['previsoes'])
        self.assertEqual(r['motivos_exclusao_observacoes'],
                         {'inelegivel:icu_ignore_power=true': 1, 'potencia_invalida': 2,
                          'revisao_atual_rejeitada': 1})
        self.assertEqual(r['n_observacoes_usaveis'], 5)

    def test_invalidacao_posterior_remove_o_alvo_e_o_treino(self):
        r1 = T.rev('Bike', 300, 'x', dia(2), 400.0)
        r2 = T.rev('Bike', 300, 'x', dia(2), 400.0, estado='rejeitado', revisao=2, anterior='confirmado')
        base = serie([300, 305, 310, 315])
        a = it(valida(base + [r1, r2]))
        b = it(valida(base))
        self.assertEqual(a['previsoes'], b['previsoes'])

    def test_elegibilidade_desconhecida_fica_fora(self):
        revs = serie([300, 305, 310, 315])
        r = it(valida(revs, atividades=[{'id': 'a0', 'icu_ignore_power': False}]))
        self.assertEqual(r['n_observacoes_usaveis'], 1)
        self.assertEqual(r['motivos_exclusao_observacoes'],
                         {'elegibilidade_desconhecida:atividade_nao_encontrada': 3})

    def test_icu_ignore_hr_nao_exclui(self):
        revs = serie([300, 305, 310])
        ativ = [{'id': r['activity_id'], 'icu_ignore_power': False, 'icu_ignore_hr': True} for r in revs]
        self.assertEqual(it(valida(revs, atividades=ativ))['n_observacoes_usaveis'], 3)


class OrdenacaoDatasIguais(unittest.TestCase):
    def test_alvos_no_mesmo_dia_partilham_o_treino_e_nao_se_veem(self):
        revs = [T.rev('Bike', 300, 'p0', dia(0), 300.0), T.rev('Bike', 300, 'p1', dia(1), 310.0),
                T.rev('Bike', 300, 'm1', dia(2), 330.0), T.rev('Bike', 300, 'm2', dia(2), 370.0)]
        r = it(valida(revs))
        a, b = [p for p in r['previsoes'] if p['data'] == dia(2)]
        self.assertEqual((a['M0'], a['M1'], a['n_treino']), (b['M0'], b['M1'], b['n_treino']))
        self.assertEqual(a['M2'], b['M2'])
        self.assertEqual(a['n_treino'], 2)

    def test_resultado_nao_depende_da_ordem_de_entrada(self):
        revs = passeio(25, 5, 1, 2) + [T.rev('Bike', 300, 'dup', dia(10), 305.0)]
        ref = valida(revs, modos=V.MODOS)
        rnd = random.Random(0)
        for _ in range(3):
            x = revs[:]
            rnd.shuffle(x)
            self.assertEqual(valida(x, modos=V.MODOS), ref)


class QESensibilidade(unittest.TestCase):
    def test_q_predefinido_e_respeitado(self):
        revs = passeio(20, 5, 2, 5)
        a, b = it(valida(revs)), it(valida(revs, q=0.1))
        self.assertNotEqual(a['previsoes'], b['previsoes'])
        self.assertEqual(valida(revs)['configuracao']['q_predefinido'], 0.01)
        self.assertEqual(valida(revs)['configuracao']['grelha_q'], list(V.GRELHA_Q))

    def test_linha_predefinida_da_sensibilidade_coincide_com_m2(self):
        r = it(valida(passeio(25, 5, 2, 6)))
        lin = [s for s in r['sensibilidade_q'] if s['predefinido']][0]
        self.assertEqual(lin['q'], 0.01)
        self.assertAlmostEqual(lin['mae'], r['metodos']['M2']['mae'], places=12)
        self.assertAlmostEqual(lin['rmse'], r['metodos']['M2']['rmse'], places=12)
        self.assertEqual({s['n'] for s in r['sensibilidade_q']}, {r['n_alvos']})

    def test_grelha_sempre_inclui_o_valor_atual_e_e_reproduzivel(self):
        r = it(valida(passeio(25, 5, 2, 6), q=0.02))
        self.assertIn(0.02, [s['q'] for s in r['sensibilidade_q']])
        self.assertEqual(it(valida(passeio(25, 5, 2, 6), q=0.02)), r)

    def test_selecao_aninhada_e_separada_e_nunca_entra_no_veredicto(self):
        revs = passeio(40, 5, 2, 8)
        r = it(valida(revs))
        n = r['selecao_q_aninhada']
        self.assertTrue(n['exploratoria'])
        self.assertEqual(n['n_origens_com_selecao'] + n['n_origens_sem_selecao'], r['n_alvos'])
        self.assertEqual(n['n_origens_sem_selecao'], V.MIN_TREINO_Q - 1)   # alvos com treino de 1..5
        self.assertEqual(n['m2_q_predefinido_mesmo_subconjunto']['n'], n['n_origens_com_selecao'])
        # o veredicto/diferencas so dependem de M0, M1, M2 predefinido
        r2 = it(valida(revs, grelha_q=(0.5,)))
        self.assertEqual(r['diferencas_m2_menos_baseline'], r2['diferencas_m2_menos_baseline'])
        self.assertEqual(r['veredicto'], r2['veredicto'])

    def test_treino_longo_mas_sem_erros_walk_forward_suficientes_mantem_o_predefinido(self):
        # 8 observacoes na MESMA data: nenhum erro walk-forward calculavel
        obs = [{'test_id': f't{i}', 'activity_id': f'a{i}', 'hora_local': None, 'data': '2025-01-01',
                'watts': 300.0 + i} for i in range(8)]
        self.assertEqual(V.escolher_q(obs, '2025-02-01'), (0.01, False))

    def test_sem_amostra_para_selecionar_mantem_o_predefinido(self):
        revs = serie([300, 310, 305, 315])
        q, ok = V.escolher_q([{'test_id': r['test_id'], 'activity_id': r['activity_id'], 'hora_local': None,
                               'data': r['data'], 'watts': r['watts']} for r in revs], '2030-01-01')
        self.assertEqual((q, ok), (0.01, False))
        self.assertEqual(it(valida(revs))['selecao_q_aninhada']['n_origens_com_selecao'], 0)


class Suficiencia(unittest.TestCase):
    def test_sem_dados(self):
        r = valida([])
        for i in r['modos']['retrospectivo']['itens'] if r['modos']['retrospectivo']['itens'] else []:
            self.assertEqual(i['estado_suficiencia'], V.EST_SEM_ALVOS)
        self.assertEqual(len(r['modos']['retrospectivo']['itens']), 12)
        for i in r['modos']['retrospectivo']['itens']:
            self.assertEqual((i['estado_suficiencia'], i['veredicto']), (V.EST_SEM_ALVOS, None))

    def test_amostra_pequena_e_descritiva_e_inconclusiva(self):
        r = it(valida(passeio(12, 5, 1, 1)))
        self.assertEqual(r['estado_suficiencia'], V.EST_DESCRITIVO)
        self.assertIsNone(r['veredicto'])
        for b in ('M0', 'M1'):
            d = r['diferencas_m2_menos_baseline'][b]
            self.assertIsNone(d['ic95_d_mae'])
            self.assertEqual(d['motivo_sem_ic'], 'amostra_insuficiente')
        self.assertIsNotNone(r['metodos']['M2']['mae'])          # metricas descritivas existem

    def test_muitos_alvos_mas_pouco_tempo_e_descritivo(self):
        r = it(valida(passeio(30, 5, 1, 1, passo=2)))             # 29 alvos em 58 dias
        self.assertEqual(r['estado_suficiencia'], V.EST_DESCRITIVO)

    def test_cada_criterio_de_suficiencia_e_vinculativo(self):
        # 19 alvos (20 obs), muitas datas e muito tempo -> falta 1 alvo
        r = it(valida(passeio(20, 5, 1, 1, passo=14)))
        self.assertEqual((r['n_alvos'], r['estado_suficiencia']), (19, V.EST_DESCRITIVO))
        r = it(valida(passeio(21, 5, 1, 1, passo=14)))
        self.assertEqual((r['n_alvos'], r['estado_suficiencia']), (20, V.EST_AVALIAVEL))
        # 20 alvos mas so 14 datas distintas
        revs = [T.rev('Bike', 300, f'a{i}', dia(i // 2 if i < 29 else 14, 30), 300 + i) for i in range(29)]
        r = it(valida(revs))
        self.assertGreaterEqual(r['n_alvos'], 20)
        self.assertLess(r['n_datas_distintas'], 15)
        self.assertEqual(r['estado_suficiencia'], V.EST_DESCRITIVO)
        # alvos e datas suficientes mas menos de 90 dias
        r = it(valida(passeio(40, 5, 1, 1, passo=2)))
        self.assertLess(r['dias_cobertura'], 90)
        self.assertEqual(r['estado_suficiencia'], V.EST_DESCRITIVO)

    def test_datas_distintas_contam(self):
        revs = [T.rev('Bike', 300, f'a{i}', dia(i // 3, 60), 300 + i) for i in range(60)]
        r = it(valida(revs))                                      # muitos alvos, so 20 datas
        self.assertEqual(r['n_datas_distintas'], 19)              # a 1.a data nao e alvo
        self.assertEqual(r['estado_suficiencia'], V.EST_AVALIAVEL)

    def test_avaliavel_tem_ic_e_veredicto_definido(self):
        r = it(valida(passeio(40, 5, 1, 1)))
        self.assertEqual(r['estado_suficiencia'], V.EST_AVALIAVEL)
        self.assertIn(r['veredicto'], ('m2_melhor_que_ambos_baselines', 'm2_pior_que_algum_baseline',
                                       'diferenca_nao_demonstrada'))
        for d in r['diferencas_m2_menos_baseline'].values():
            self.assertEqual(len(d['ic95_d_mae']), 2)
            self.assertEqual(d['bloco'], V.comprimento_bloco(r['n_alvos']))

    def test_veredicto_exige_ic_margem_e_rmse(self):
        d = lambda lo, hi, rel, rm: {'ic95_d_mae': [lo, hi], 'd_mae_relativo': rel, 'd_rmse': rm}
        ok = d(-3, -1, -0.2, -1)
        self.assertEqual(V.veredicto({'M0': ok, 'M1': ok}), 'm2_melhor_que_ambos_baselines')
        # diferenca pequena (<5%) mesmo com IC a excluir 0
        self.assertEqual(V.veredicto({'M0': d(-0.3, -0.1, -0.02, -0.1), 'M1': ok}), 'diferenca_nao_demonstrada')
        # IC a incluir 0
        self.assertEqual(V.veredicto({'M0': d(-3, 1, -0.2, -1), 'M1': ok}), 'diferenca_nao_demonstrada')
        # RMSE contraria o MAE
        self.assertEqual(V.veredicto({'M0': d(-3, -1, -0.2, 1), 'M1': ok}), 'diferenca_nao_demonstrada')
        # melhor so face a um baseline
        self.assertEqual(V.veredicto({'M0': ok, 'M1': d(-3, 1, -0.2, -1)}), 'diferenca_nao_demonstrada')
        self.assertEqual(V.veredicto({'M0': ok, 'M1': d(1, 3, 0.2, 1)}), 'm2_pior_que_algum_baseline')
        self.assertEqual(V.veredicto({'M0': None, 'M1': ok}), 'diferenca_nao_demonstrada')


class CalibracaoDoIntervalo(unittest.TestCase):
    def test_intervalo_preditivo_e_distinto_do_ic_do_nivel(self):
        revs = passeio(12, 5, 0.5, 1)
        r = it(valida(revs))
        p = r['previsoes'][-1]
        treino = [{'test_id': x['test_id'], 'activity_id': x['activity_id'], 'hora_local': None,
                   'data': x['data'], 'watts': x['watts']} for x in revs[:-1]]
        inc = E.estimar_serie(treino, revs[-1]['data'])['resumo']['incerteza']
        largura_nivel = inc['ic95_w'][1] - inc['ic95_w'][0]
        largura_pred = p['ic95_predito_m2'][1] - p['ic95_predito_m2'][0]
        self.assertGreater(largura_pred, largura_nivel)

    def test_amostra_pequena_nao_calcula_cobertura(self):
        c = it(valida(serie([300, 305, 310, 302, 308])))['calibracao_intervalo_preditivo']
        self.assertIsNone(c['cobertura_observada'])
        self.assertEqual(c['motivo_sem_cobertura'], 'amostra_insuficiente')
        self.assertIn('nao_validado', c['tipo'])

    def test_cobertura_sintetica_perto_do_nominal(self):
        # SINTETICO: ruido gaussiano em torno de nivel constante -> o modelo esta bem especificado
        cob = []
        for seed in range(6):
            c = it(valida(passeio(60, 5, 0.0, seed)))['calibracao_intervalo_preditivo']
            self.assertGreaterEqual(c['n_intervalos'], V.MIN_INTERVALOS_CALIBRACAO)
            cob.append(c['cobertura_observada'])
        self.assertGreater(sum(cob) / len(cob), 0.88)


class PipelineDiscrimina(unittest.TestCase):
    """SINTETICO: o pipeline nao favorece o estimador; resultados dependem do processo."""

    def test_com_deriva_moderada_m2_tem_menor_mae_que_ambos(self):
        medias = {m: 0.0 for m in ('M0', 'M1', 'M2')}
        for seed in range(6):
            r = it(valida(passeio(60, 8, 2.5, seed)))
            for m in medias:
                medias[m] += r['metodos'][m]['mae'] / 6
        self.assertLess(medias['M2'], medias['M0'])
        self.assertLess(medias['M2'], medias['M1'])

    def test_com_deriva_forte_e_ruido_baixo_q_predefinido_perde_para_m1(self):
        # q=0.01 e uma convencao: aqui e demasiado rigido e o pipeline mostra-o
        r = it(valida(passeio(60, 1, 6, 1)))
        self.assertLess(r['metodos']['M1']['mae'], r['metodos']['M2']['mae'])
        sens = {s['q']: s['mae'] for s in r['sensibilidade_q']}
        self.assertLess(sens[0.1], sens[0.001])

    def test_nivel_constante_m2_nao_e_melhor_que_a_media(self):
        for seed in range(4):
            r = it(valida(passeio(60, 5, 0.0, seed)))
            self.assertNotEqual(r['veredicto'], 'm2_melhor_que_ambos_baselines')

    def test_muita_amostra_e_deriva_pode_demonstrar_melhoria(self):
        r = it(valida(passeio(150, 8, 1.0, 1, passo=7)))
        self.assertEqual(r['veredicto'], 'm2_melhor_que_ambos_baselines')


class Modos(unittest.TestCase):
    def test_conhecido_na_epoca_sem_confirmacao_contemporanea_nao_tem_treino(self):
        # confirmadas todas muito depois: nada era conhecido na data de cada alvo
        revs = serie([300, 305, 310, 315], revisado_em='2026-06-01T10:00:00')
        r = it(valida(revs, modos=V.MODOS), 'conhecido_na_epoca')
        self.assertEqual((r['n_alvos'], r['origens_excluidas']), (0, {'sem_historico': 4}))
        self.assertGreater(r['treino_perdido_por_modo'], 0)
        self.assertEqual(it(valida(revs, modos=V.MODOS))['n_alvos'], 3)         # retrospectivo

    def test_conhecido_na_epoca_com_confirmacao_contemporanea_iguala_o_retrospectivo(self):
        revs = [T.rev('Bike', 300, f'a{i}', dia(i), 300.0 + i, revisado_em=dia(i) + 'T20:00:00')
                for i in range(25)]
        res = valida(revs, modos=V.MODOS)
        a, b = it(res, 'retrospectivo'), it(res, 'conhecido_na_epoca')
        self.assertEqual(a['previsoes'], b['previsoes'])
        self.assertEqual(b['treino_perdido_por_modo'], 0)

    def test_confirmacao_atrasada_so_de_algumas_reduz_o_treino(self):
        revs = [T.rev('Bike', 300, f'a{i}', dia(i), 300.0 + i,
                      revisado_em=(dia(i) + 'T20:00:00') if i != 2 else '2026-06-01T00:00:00')
                for i in range(6)]
        b = it(valida(revs, modos=V.MODOS), 'conhecido_na_epoca')
        p = {x['data']: x for x in b['previsoes']}
        self.assertEqual(p[dia(3)]['n_treino'], 2)           # a2 ainda nao era conhecida
        self.assertEqual(p[dia(5)]['n_treino'], 4)           # a2 continua desconhecida: 0,1,3,4
        self.assertGreater(b['treino_perdido_por_modo'], 0)

    def test_invalidacao_posterior_nao_apaga_o_que_era_conhecido(self):
        # no modo da epoca, a invalidacao so conta a partir do seu revisado_em
        r1 = T.rev('Bike', 300, 'x', dia(1), 330.0, revisado_em=dia(1) + 'T20:00:00')
        r2 = T.rev('Bike', 300, 'x', dia(1), 330.0, estado='rejeitado', revisao=2,
                   anterior='confirmado', revisado_em='2026-06-01T00:00:00')
        base = [T.rev('Bike', 300, f'b{i}', dia(i), 300.0, revisado_em=dia(i) + 'T20:00:00')
                for i in (0, 2, 3)]
        res = valida(base + [r1, r2], modos=V.MODOS)
        self.assertIn('Bike:300:b2', [p['test_id'] for p in it(res, 'conhecido_na_epoca')['previsoes']])
        # x esta rejeitada no estado atual: nao e alvo em nenhum modo
        for modo in V.MODOS:
            self.assertNotIn('Bike:300:x', [p['test_id'] for p in it(res, modo)['previsoes']])

    def test_modo_invalido(self):
        with self.assertRaises(ValueError):
            V.avaliar([], [], [], modo='outro')


class Independencia(unittest.TestCase):
    def test_outra_modalidade_ou_duracao_nao_altera_a_avaliacao(self):
        bike = passeio(25, 5, 1, 1)
        row = passeio(25, 5, 3, 2, mod='Row', prefixo='r')
        bike60 = passeio(25, 9, 1, 3, dur=60, base=700, prefixo='s')
        sozinho = it(valida(bike))
        junto = it(valida(bike + row + bike60))
        self.assertEqual(sozinho, junto)
        self.assertEqual(it(valida(bike + row), mod='Row'), it(valida(row), mod='Row'))

    def test_doze_unidades_sempre_presentes(self):
        itens = valida(passeio(5, 5, 1, 1))['modos']['retrospectivo']['itens']
        self.assertEqual(sorted((i['modalidade'], i['duracao_s']) for i in itens),
                         sorted((m, d) for m in ('Bike', 'Row', 'Ski', 'Run') for d in (60, 300, 1200)))


class Reproducao(unittest.TestCase):
    def test_duas_execucoes_dao_o_mesmo_resultado(self):
        revs = passeio(40, 5, 1.5, 11) + passeio(30, 7, 0.5, 12, mod='Row', dur=1200, base=200, prefixo='r')
        a = valida(copy.deepcopy(revs), modos=V.MODOS)
        b = valida(copy.deepcopy(revs), modos=V.MODOS)
        self.assertEqual(a, b)
        self.assertEqual(len(a['assinatura']), 64)

    def test_assinatura_muda_com_dados_e_parametros(self):
        revs = passeio(25, 5, 1, 1)
        ref = valida(revs)['assinatura']
        self.assertNotEqual(ref, valida(revs[:-1])['assinatura'])
        self.assertNotEqual(ref, valida(revs, q=0.02)['assinatura'])

    def test_nao_altera_as_entradas(self):
        revs = passeio(25, 5, 1, 1)
        a, c = T.ativ(*{r['activity_id'] for r in revs}), T.curvas_de(revs)
        cr, ca, cc = copy.deepcopy(revs), copy.deepcopy(a), copy.deepcopy(c)
        V.validar(revs, a, c)
        self.assertEqual((revs, a, c), (cr, ca, cc))

    def test_resultado_declara_ser_exploratorio_e_sem_promocao(self):
        r = valida([])
        self.assertTrue(r['experimental'])
        self.assertFalse(r['promocao'])
        self.assertIn('nao teste padronizado', r['alvo'])

    def test_historico_ambiguo_propaga(self):
        r1 = T.rev('Bike', 300, 'x', dia(0), 300.0)
        with self.assertRaises(E.HistoricoAmbiguo):
            V.validar([r1, {**r1, 'revisao': 3}], [], [])


if __name__ == '__main__':
    unittest.main()
