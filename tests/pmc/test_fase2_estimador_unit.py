"""Fase 2 — estimador experimental (modelo_estimador.py): puro, offline, deterministico.

Os testes marcados SINTETICO usam uma potencia verdadeira conhecida, escolhida pelo teste.
Verificam o comportamento do algoritmo; NAO sao evidencia sobre o atleta real nem sobre
capacidade preditiva (Fase 3).

    python -m pytest tests/pmc/test_fase2_estimador_unit.py
"""
import copy
import math
import os
import random
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import modelo_estimador as E  # noqa: E402
import modelo_testes as M  # noqa: E402

AT = '2026-12-31'


def rev(mod='Bike', dur=300, aid='a1', data='2026-01-01', watts=300.0, estado='confirmado',
        revisao=1, evento=None, anterior=None, hora=None, **kw):
    evento = evento or M._evento(anterior, estado)
    d = {'test_id': M.id_teste(mod, dur, aid), 'revisao': revisao, 'evento': evento,
         'estado': estado, 'estado_anterior': anterior, 'modalidade': mod, 'duracao_s': dur,
         'activity_id': aid, 'data': data, 'hora_local': hora, 'watts': watts,
         'elegibilidade': 'elegivel', 'metodo_confirmacao': 'manual',
         'confirmado_em': '2026-01-02T00:00:00' if estado == 'confirmado' else None,
         'aceita_elegibilidade_desconhecida': False}
    d.update(kw)
    return d


def ativ(*ids, ignore=False, **extra):
    return [{'id': i, 'icu_ignore_power': ignore, **extra} for i in ids]


def curvas_de(revs):
    return [{'activity_id': r['activity_id'], 'secs': [r['duracao_s']], 'watts': [r['watts']]}
            for r in revs]


def calc(revs, atividades='auto', curvas='auto', ate=AT, **kw):
    if atividades == 'auto':
        atividades = ativ(*{r['activity_id'] for r in revs})
    if curvas == 'auto':
        curvas = curvas_de(revs)
    return E.calcular_estado(revs, atividades, curvas, ate, **kw)


def item(res, mod='Bike', dur=300):
    return next(i for i in res['itens'] if i['modalidade'] == mod and i['duracao_s'] == dur)


def serie_sintetica(n, verdadeiro=300.0, sigma=0.0, seed=1, inicio=date(2026, 1, 1), passo=14,
                    mod='Bike', dur=300):
    rnd = random.Random(seed)
    return [rev(mod, dur, f'a{i}', (inicio + timedelta(days=passo * i)).isoformat(),
                round(verdadeiro + rnd.gauss(0, sigma), 6)) for i in range(n)]


class Inicializacao(unittest.TestCase):
    def test_sem_testes_nao_calibrado_sem_nivel_nem_incerteza(self):
        r = calc([])
        self.assertEqual(len(r['itens']), 12)            # 4 modalidades x 3 duracoes
        for i in r['itens']:
            self.assertEqual((i['estado_calibracao'], i['nivel_w'], i['incerteza']),
                             (E.EST_NAO_CALIBRADO, None, None))
            self.assertEqual((i['n_confirmados'], i['n_usadas'], i['n_excluidas']), (0, 0, 0))
            self.assertEqual(i['motivo_sem_incerteza'], 'sem_observacoes')

    def test_estado_inicial_nao_tem_referencia_nem_prior(self):
        s = E.estado_inicial()
        self.assertEqual((s['n'], s['mu'], s['p']), (0, None, None))

    def test_resultado_marcado_experimental_e_nao_validado(self):
        r = calc([])
        self.assertTrue(r['experimental'])
        self.assertFalse(r['validado_preditivamente'])

    def test_historico_none_ou_vazio(self):
        self.assertEqual(calc([], atividades=None, curvas=None)['itens'][0]['n_confirmados'], 0)
        self.assertEqual(E.estado_atual_por_teste(None), {})


class PrimeiraObservacao(unittest.TestCase):
    def test_um_teste_valido_da_estimativa_unica_sem_incerteza(self):
        i = item(calc([rev(watts=287.5)]))
        self.assertEqual(i['estado_calibracao'], E.EST_UNICA)
        self.assertEqual(i['nivel_w'], 287.5)
        self.assertIsNone(i['incerteza'])                      # nao estimavel, nao inventada
        self.assertEqual(i['motivo_sem_incerteza'], 'incerteza_nao_estimavel_com_1_observacao')
        self.assertEqual((i['n_confirmados'], i['n_usadas']), (1, 1))
        self.assertEqual(i['ultima_observacao'], '2026-01-01')


class AtualizacaoSucessiva(unittest.TestCase):
    def test_calculo_a_mao_do_segundo_passo(self):
        # y1=300, y2=310, dt=14 d, q=0.01 -> P*pred=1.14, F=2.14, K=1.14/2.14
        i = item(calc([rev(aid='a', data='2026-01-01', watts=300.0),
                       rev(aid='b', data='2026-01-15', watts=310.0)]))
        k = 1.14 / 2.14
        self.assertAlmostEqual(i['nivel_w'], 300 + k * 10, places=9)
        self.assertEqual(i['estado_calibracao'], E.EST_EXPERIMENTAL)
        s2 = 100.0 / 2.14
        self.assertAlmostEqual(i['incerteza']['sigma_ruido_w'], math.sqrt(s2), places=9)
        self.assertEqual(i['incerteza']['graus_liberdade'], 1)

    def test_nivel_move_se_para_a_nova_observacao_sem_a_igualar(self):
        r = calc([rev(aid='a', watts=300.0), rev(aid='b', data='2026-02-01', watts=320.0)])
        self.assertTrue(300.0 < item(r)['nivel_w'] < 320.0)

    def test_fold_incremental_igual_ao_lote(self):
        revs = serie_sintetica(8, sigma=5.0)
        st = E.estado_inicial()
        for r in revs:
            st, info = E.passo(st, r)
            self.assertTrue(info['aceite'])
        lote = item(calc(revs))
        resumo = E.resumir(st, AT)
        self.assertEqual(resumo['nivel_w'], lote['nivel_w'])
        self.assertEqual(resumo['incerteza'], lote['incerteza'])


class ObservacaoInvalida(unittest.TestCase):
    def test_passo_invalido_devolve_o_mesmo_estado(self):
        st, _ = E.passo(E.estado_inicial(), {'test_id': 'x', 'data': '2026-01-01', 'watts': 300.0})
        antes = copy.deepcopy(st)
        for w in (0, -5, float('nan'), float('inf'), None, '300', True, [300]):
            novo, info = E.passo(st, {'test_id': 'y', 'data': '2026-02-01', 'watts': w})
            self.assertIs(novo, st)
            self.assertFalse(info['aceite'])
            self.assertEqual(info['motivo'], 'potencia_invalida')
        novo, info = E.passo(st, {'test_id': 'y', 'data': 'ontem', 'watts': 300.0})
        self.assertEqual((novo is st, info['motivo']), (True, 'data_invalida'))
        novo, info = E.passo(st, {'test_id': 'y', 'data': '2025-12-01', 'watts': 300.0})
        self.assertEqual((novo is st, info['motivo']), (True, 'fora_de_ordem'))
        self.assertEqual(st, antes)

    def test_potencia_invalida_no_historico_e_excluida_e_nao_corrompe(self):
        bons = [rev(aid='a', watts=300.0), rev(aid='b', data='2026-02-01', watts=310.0)]
        ref = item(calc(bons))
        for w in (0, -1, float('nan'), float('inf'), None, True, '300'):
            ruim = rev(aid='c', data='2026-03-01', watts=w)
            r = item(calc(bons + [ruim], curvas=curvas_de(bons),
                          atividades=ativ('a', 'b', 'c')))
            self.assertEqual(r['nivel_w'], ref['nivel_w'], w)
            self.assertEqual(r['incerteza'], ref['incerteza'], w)
            self.assertEqual(r['motivos_exclusao'], {'potencia_invalida': 1}, w)

    def test_modalidade_ou_duracao_fora_do_dominio_nao_entra(self):
        r = calc([rev(), rev(mod='Swim', aid='s'), rev(dur=90, aid='d')],
                 atividades=ativ('a1', 's', 'd'), curvas=[])
        self.assertEqual(sum(i['n_confirmados'] for i in r['itens']), 1)


class Confirmacao(unittest.TestCase):
    def test_rejeitado_nao_entra(self):
        r = item(calc([rev(estado='rejeitado', watts=999.0)]))
        self.assertEqual((r['estado_calibracao'], r['n_confirmados'], r['n_usadas']),
                         (E.EST_NAO_CALIBRADO, 0, 0))
        self.assertEqual(r['motivos_exclusao'], {'revisao_atual_rejeitada': 1})

    def test_confirmacao_explicita_ausente_nao_entra(self):
        r = item(calc([rev(confirmado_em=None)]))
        self.assertEqual(r['motivos_exclusao'], {'confirmacao_explicita_ausente': 1})
        r = item(calc([rev(metodo_confirmacao='protocolo')]))
        self.assertEqual(r['motivos_exclusao'], {'confirmacao_explicita_ausente': 1})


class RevisaoAtual(unittest.TestCase):
    def historico(self):
        r1 = rev(estado='confirmado', revisao=1, watts=300.0)
        r2 = rev(estado='rejeitado', revisao=2, anterior='confirmado')
        r3 = rev(estado='confirmado', revisao=3, anterior='rejeitado', evento='reconfirmacao')
        return r1, r2, r3

    def test_invalidacao_remove_a_observacao(self):
        r1, r2, _ = self.historico()
        i = item(calc([r1, r2]))
        self.assertEqual((i['estado_calibracao'], i['n_usadas']), (E.EST_NAO_CALIBRADO, 0))
        self.assertEqual(i['motivos_exclusao'], {'revisao_atual_rejeitada': 1})

    def test_reconfirmacao_volta_a_contar_uma_so_vez(self):
        r1, r2, r3 = self.historico()
        i = item(calc([r1, r2, r3]))
        self.assertEqual((i['n_confirmados'], i['n_usadas']), (1, 1))
        self.assertEqual(i['sequencia_usada'][0]['revisao'], 3)

    def test_revisoes_antigas_nao_contam_como_observacoes_adicionais(self):
        r1, _, _ = self.historico()
        self.assertEqual(item(calc([r1]))['n_usadas'], 1)
        r1, r2, r3 = self.historico()
        self.assertEqual(item(calc([r3, r1, r2]))['n_usadas'], 1)       # ordem de entrada irrelevante

    def test_so_a_revisao_atual_decide_os_watts(self):
        # se a revisao 1 tinha watts diferentes, so a revisao atual interessa
        r1 = rev(revisao=1, watts=250.0)
        r2 = rev(estado='rejeitado', revisao=2, anterior='confirmado', watts=250.0)
        r3 = rev(estado='confirmado', revisao=3, anterior='rejeitado', evento='reconfirmacao',
                 watts=300.0)
        i = item(calc([r1, r2, r3], curvas=curvas_de([r3])))
        self.assertEqual(i['nivel_w'], 300.0)

    def test_historico_ambiguo_para(self):
        r1, r2, r3 = self.historico()
        casos = {'lacuna': [r1, r3], 'repeticao': [r1, r1],
                 'anterior_incoerente': [r1, {**r2, 'estado_anterior': None}],
                 'estado_repetido': [r1, {**rev(revisao=2, anterior='confirmado',
                                                 evento='confirmacao')}],
                 'evento_incoerente': [r1, {**r2, 'evento': 'confirmacao'}],
                 'id_incoerente': [{**r1, 'test_id': 'Bike:300:outro'}],
                 'sem_primeira': [r2]}
        for nome, h in casos.items():
            with self.assertRaises(E.HistoricoAmbiguo, msg=nome):
                E.estado_atual_por_teste(h)


class ElegibilidadeAprendizagem(unittest.TestCase):
    def elig(self, atividades, aid='a1'):
        return E.elegibilidade_aprendizagem(aid, atividades)

    def motivo(self, atividades, **kw):
        i = item(calc([rev()], atividades=atividades, **kw))
        return i['motivos_exclusao'], i['n_usadas']

    def test_true_exclui(self):
        self.assertEqual(self.motivo(ativ('a1', ignore=True)),
                         ({'inelegivel:icu_ignore_power=true': 1}, 0))

    def test_false_explicito_nao_dispensa_as_outras_verificacoes(self):
        self.assertEqual(self.motivo(ativ('a1')), ({}, 1))
        # false, mas watts invalidos / nao confirmado / curva divergente -> continua excluido
        self.assertEqual(item(calc([rev(watts=0)], atividades=ativ('a1')))['n_usadas'], 0)
        self.assertEqual(item(calc([rev(estado='rejeitado')], atividades=ativ('a1')))['n_usadas'], 0)
        self.assertEqual(item(calc([rev()], atividades=ativ('a1'),
                                   curvas=[{'activity_id': 'a1', 'secs': [300],
                                            'watts': [301.0]}]))['motivos_exclusao'],
                         {'dados_divergente': 1})
        self.assertEqual(item(calc([rev()], atividades=ativ('a1'), curvas=[]))['motivos_exclusao'],
                         {'dados_sem_curva': 1})

    def test_ausente_none_ou_nao_booleano_e_desconhecida_e_exclui(self):
        for a in ([{'id': 'a1'}], [{'id': 'a1', 'icu_ignore_power': None}],
                  [{'id': 'a1', 'icu_ignore_power': 'false'}], [{'id': 'a1', 'icu_ignore_power': 0}]):
            self.assertEqual(self.elig(a)['estado'], E.ELEG_DESCONHECIDA, a)
            self.assertEqual(self.motivo(a),
                             ({'elegibilidade_desconhecida:icu_ignore_power_ausente': 1}, 0), a)

    def test_atividade_inexistente_ou_atividades_indisponiveis(self):
        self.assertEqual(self.elig(ativ('outra'))['motivo'], 'atividade_nao_encontrada')
        self.assertEqual(self.elig(None)['motivo'], 'atividades_indisponiveis')
        self.assertEqual(self.motivo(None)[1], 0)

    def test_confirmacao_manual_com_opt_in_nao_torna_conhecido_o_desconhecido(self):
        r = rev(elegibilidade='desconhecida', aceita_elegibilidade_desconhecida=True)
        i = item(calc([r], atividades=[{'id': 'a1'}]))
        self.assertEqual(i['n_usadas'], 0)
        self.assertEqual(i['n_confirmados'], 1)

    def test_elegibilidade_e_a_atual_nao_a_do_registo(self):
        # registada como elegivel, mas a atividade passou a icu_ignore_power=true
        i = item(calc([rev(elegibilidade='elegivel')], atividades=ativ('a1', ignore=True)))
        self.assertEqual(i['n_usadas'], 0)
        # registada como desconhecida (opt-in), agora explicitamente false -> entra
        r = rev(elegibilidade='desconhecida', aceita_elegibilidade_desconhecida=True)
        i = item(calc([r], atividades=ativ('a1')))
        self.assertEqual(i['n_usadas'], 1)
        self.assertEqual(i['sequencia_usada'][0]['elegibilidade_aprendizagem'], 'elegivel')

    def test_ignore_hr_nao_exclui_um_teste_de_potencia(self):
        for hr in (True, False, None):
            a = [{'id': 'a1', 'icu_ignore_power': False, 'icu_ignore_hr': hr}]
            self.assertEqual(self.motivo(a), ({}, 1), hr)
        a = [{'id': 'a1', 'icu_ignore_power': False, 'icu_ignore_hr': True}]
        self.assertIs(self.elig(a)['icu_ignore_hr'], True)         # so informativo
        self.assertEqual(self.elig(a)['estado'], E.ELEG_ELEGIVEL)

    def test_ignore_hr_nao_substitui_ignore_power(self):
        a = [{'id': 'a1', 'icu_ignore_power': True, 'icu_ignore_hr': False}]
        self.assertEqual(self.elig(a)['estado'], E.ELEG_INELEGIVEL)
        a = [{'id': 'a1', 'icu_ignore_hr': False}]                 # power ausente
        self.assertEqual(self.elig(a)['estado'], E.ELEG_DESCONHECIDA)

    def test_flag_da_atividade_nao_e_confundido_com_o_de_outra_atividade(self):
        a = ativ('a1') + ativ('a2', ignore=True)
        r = calc([rev(aid='a1'), rev(aid='a2', data='2026-02-01')], atividades=a)
        i = item(r)
        self.assertEqual([o['test_id'] for o in i['sequencia_usada']], ['Bike:300:a1'])
        self.assertEqual(i['motivos_exclusao'], {'inelegivel:icu_ignore_power=true': 1})

    def test_ambito_declarado_e_a_atividade_inteira(self):
        self.assertEqual(self.elig(ativ('a1'))['ambito'], 'atividade')

    def test_ignore_parts_nao_vazio_nao_e_associavel_ao_trecho(self):
        for p in ([{'start_index': 10, 'end_index': 20}], {'x': 1}, 'sim'):
            a = [{'id': 'a1', 'icu_ignore_power': False, 'ignore_parts': p}]
            e = self.elig(a)
            self.assertEqual((e['estado'], e['motivo']),
                             (E.ELEG_DESCONHECIDA, 'ignore_parts_nao_associavel_ao_trecho'), p)
        for p in (None, [], {}):
            a = [{'id': 'a1', 'icu_ignore_power': False, 'ignore_parts': p}]
            self.assertEqual(self.elig(a)['estado'], E.ELEG_ELEGIVEL, p)
        # com power=true continua inelegivel
        a = [{'id': 'a1', 'icu_ignore_power': True, 'ignore_parts': [1]}]
        self.assertEqual(self.elig(a)['estado'], E.ELEG_INELEGIVEL)

    def test_ids_repetidos_true_prevalece(self):
        a = [{'id': 'a1', 'icu_ignore_power': False}, {'id': 'a1', 'icu_ignore_power': True}]
        self.assertEqual(self.elig(a)['estado'], E.ELEG_INELEGIVEL)
        a = [{'id': 'a1', 'icu_ignore_power': False}, {'id': 'a1'}]
        self.assertEqual(self.elig(a)['estado'], E.ELEG_DESCONHECIDA)

    def test_nao_altera_as_atividades_recebidas(self):
        a = ativ('a1')
        c = copy.deepcopy(a)
        self.elig(a)
        self.assertEqual(a, c)


class Independencia(unittest.TestCase):
    BASE = ([rev('Bike', 60, 'b60', watts=700.0), rev('Bike', 1200, 'b1200', watts=250.0),
             rev('Row', 300, 'r300', watts=280.0), rev('Ski', 300, 's300', watts=200.0),
             rev('Run', 300, 'u300', watts=320.0)]
            + serie_sintetica(4, 300.0, 4.0, mod='Bike', dur=300))

    def test_novo_teste_bike_p300_so_altera_bike_p300(self):
        antes = calc(self.BASE)
        novo = rev('Bike', 300, 'novo', '2026-06-01', 340.0)
        depois = calc(self.BASE + [novo])
        mudou = [(i['modalidade'], i['duracao_s']) for i, j in zip(antes['itens'], depois['itens'])
                 if i != j]
        self.assertEqual(mudou, [('Bike', 300)])
        self.assertNotEqual(item(antes)['nivel_w'], item(depois)['nivel_w'])

    def test_cada_uma_das_12_chaves_so_mexe_em_si(self):
        for m in M.MODALIDADES:
            for d in M.DURACOES:
                antes = calc(self.BASE)
                depois = calc(self.BASE + [rev(m, d, 'extra', '2026-06-01', 123.0)])
                mudou = [(i['modalidade'], i['duracao_s'])
                         for i, j in zip(antes['itens'], depois['itens']) if i != j]
                self.assertEqual(mudou, [(m, d)], (m, d))

    def test_mesma_potencia_em_modalidades_diferentes_nao_se_mistura(self):
        r = calc([rev('Bike', 300, 'x', watts=300.0), rev('Row', 300, 'y', watts=150.0)])
        self.assertEqual(item(r, 'Bike')['nivel_w'], 300.0)
        self.assertEqual(item(r, 'Row')['nivel_w'], 150.0)

    def test_filtro_tipo_duracao_nao_muda_o_valor(self):
        todo = calc(self.BASE)
        so = calc(self.BASE, tipo='Bike', duracao=300)
        self.assertEqual(len(so['itens']), 1)
        self.assertEqual(so['itens'][0], item(todo))

    def test_codigo_nao_partilha_estado_entre_chaves(self):
        src = open(os.path.join(ROOT, 'modelo_estimador.py'), encoding='utf-8').read()
        self.assertNotIn('global ', src)


class Ordenacao(unittest.TestCase):
    def test_entrada_em_qualquer_ordem_da_o_mesmo_resultado(self):
        revs = serie_sintetica(9, sigma=6.0, seed=3)
        ref = calc(revs)
        rnd = random.Random(0)
        for _ in range(5):
            x = revs[:]
            rnd.shuffle(x)
            self.assertEqual(calc(x), ref)

    def test_mesmo_dia_nao_depende_da_ordem(self):
        a = rev(aid='aaa', data='2026-03-01', watts=300.0, hora='2026-03-01T07:00:00')
        b = rev(aid='bbb', data='2026-03-01', watts=310.0, hora='2026-03-01T17:00:00')
        c = rev(aid='ccc', data='2026-03-01', watts=305.0)
        base = [rev(aid='z', data='2026-01-01', watts=295.0)]
        ref = item(calc(base + [a, b, c]))
        for perm in ([c, b, a], [b, a, c], [b, c, a]):
            self.assertEqual(item(calc(base + perm)), ref)
        # tambem sem horas (desempate por activity_id) e com o passo incremental
        x = [{**o, 'hora_local': None} for o in (a, b, c)]
        y = item(calc(base + x))
        self.assertEqual(item(calc(base + x[::-1])), y)

    def test_soma_das_inovacoes_e_invariante_no_mesmo_dia(self):
        # mesmo dia: dt=0 -> a verosimilhanca conjunta nao depende da ordem
        a = rev(aid='a', data='2026-03-01', watts=300.0)
        b = rev(aid='b', data='2026-03-01', watts=330.0)
        base = [rev(aid='z', data='2026-01-01', watts=295.0), rev(aid='y', data='2026-02-01', watts=299.0)]
        i1 = item(calc(base + [a, b]))['incerteza']['sigma_ruido_w']
        i2 = item(calc(base + [{**a, 'activity_id': 'zz', 'test_id': 'Bike:300:zz'}, b]))
        self.assertAlmostEqual(i1, item(calc(base + [b, a]))['incerteza']['sigma_ruido_w'], places=9)
        self.assertIsNotNone(i2)

    def test_dois_testes_no_mesmo_dia_sao_duas_medidas(self):
        r = item(calc([rev(aid='a', watts=300.0), rev(aid='b', watts=310.0)]))
        self.assertEqual(r['nivel_w'], 305.0)                 # Delta t = 0: media das duas
        self.assertEqual(r['n_usadas'], 2)


class Incerteza(unittest.TestCase):
    def test_incerteza_do_nivel_encolhe_com_mais_observacoes_coerentes(self):
        # SINTETICO: ruido sigma=5 em torno de 300 W; q pequeno e media de 30 repeticoes
        # (uma so amostra pode flutuar, porque s2 e estimado dos proprios dados)
        medias = []
        for n in (4, 12, 40):
            sds = [item(calc(serie_sintetica(n, 300.0, 5.0, seed=s, passo=3),
                             q=0.0005))['incerteza']['sd_w'] for s in range(30)]
            medias.append(sum(sds) / len(sds))
        self.assertTrue(medias[0] > medias[1] > medias[2], medias)

    def test_com_q_fixo_a_incerteza_tem_um_piso_e_nao_tende_para_zero(self):
        # limitacao documentada: o nivel pode variar (q>0), por isso mais dados nao
        # fazem a incerteza desaparecer
        sd = item(calc(serie_sintetica(150, 300.0, 5.0, seed=1, passo=7)))['incerteza']['sd_w']
        self.assertGreater(sd, 0.5)

    def test_incerteza_sobe_com_o_tempo_sem_novos_testes(self):
        revs = serie_sintetica(6, 300.0, 5.0)
        sd = lambda ate: item(calc(revs, ate=ate))['incerteza']['sd_w']
        ult = revs[-1]['data']
        d = date.fromisoformat(ult)
        self.assertLess(sd(ult), sd((d + timedelta(days=30)).isoformat()))
        self.assertLess(sd((d + timedelta(days=30)).isoformat()),
                        sd((d + timedelta(days=90)).isoformat()))
        # o nivel (media) nao se move sem observacao nova
        n = lambda ate: item(calc(revs, ate=ate))['nivel_w']
        self.assertEqual(n(ult), n((d + timedelta(days=90)).isoformat()))

    def test_um_atipico_aumenta_a_incerteza_e_e_sinalizado(self):
        # SINTETICO
        limpo = serie_sintetica(10, 300.0, 3.0, seed=5)
        sujo = limpo[:-1] + [{**limpo[-1], 'watts': 420.0}]
        a, b = item(calc(limpo)), item(calc(sujo, curvas=curvas_de(sujo)))
        self.assertGreater(b['incerteza']['sigma_ruido_w'], 2 * a['incerteza']['sigma_ruido_w'])
        self.assertTrue(b['trajetoria'][-1]['atipica'])
        self.assertFalse(any(p['atipica'] for p in a['trajetoria']))
        self.assertEqual(b['n_usadas'], 10)                   # sinaliza, nao exclui

    def test_incerteza_nunca_artificialmente_pequena(self):
        # observacoes identicas: variabilidade nula -> sem incerteza (nao ~0)
        i = item(calc([rev(aid=f'a{k}', data=f'2026-0{k + 1}-01', watts=300.0) for k in range(4)]))
        self.assertIsNone(i['incerteza'])
        self.assertEqual(i['motivo_sem_incerteza'], 'variabilidade_observada_nula')
        self.assertEqual(i['estado_calibracao'], E.EST_EXPERIMENTAL)

    def test_com_duas_observacoes_o_intervalo_e_muito_largo(self):
        i = item(calc([rev(aid='a', watts=300.0), rev(aid='b', data='2026-02-01', watts=310.0)]))
        lo, hi = i['incerteza']['ic95_w']
        self.assertGreater(hi - lo, 4 * i['incerteza']['sd_w'])     # t com 1 g.l. = 12.7

    def test_quantis_t(self):
        self.assertEqual(E.t975(1), 12.706)
        self.assertEqual(E.t975(30), 2.042)
        self.assertTrue(E.t975(31) >= E.t975(60) >= E.t975(500) >= 1.96)
        with self.assertRaises(ValueError):
            E.t975(0)

    def test_estimativa_e_incerteza_sao_campos_distintos(self):
        i = item(calc(serie_sintetica(4, 300.0, 3.0)))
        self.assertIn('nivel_w', i)
        self.assertIn('incerteza', i)
        self.assertNotIn('nivel_w', i['incerteza'])


class RecuperacaoSintetica(unittest.TestCase):
    """SINTETICO: potencia verdadeira conhecida. Comportamento do algoritmo, nao do atleta."""

    def test_sem_ruido_recupera_o_nivel_exato(self):
        i = item(calc(serie_sintetica(6, 312.0, 0.0)))
        self.assertAlmostEqual(i['nivel_w'], 312.0, places=9)

    def test_com_ruido_recupera_o_nivel_dentro_da_incerteza(self):
        dentro = 0
        for seed in range(40):
            i = item(calc(serie_sintetica(12, 300.0, 5.0, seed=seed, passo=2)))
            lo, hi = i['incerteza']['ic95_w']
            dentro += lo <= 300.0 <= hi
            self.assertLess(abs(i['nivel_w'] - 300.0), 6.0, seed)
        self.assertGreaterEqual(dentro, 34)        # ~95% esperado; folga para 40 repeticoes

    def test_acompanha_uma_subida_real_do_nivel(self):
        revs = []
        for k in range(12):
            nivel = 300.0 if k < 6 else 330.0
            revs.append(rev(aid=f'a{k}', data=(date(2026, 1, 1) + timedelta(days=14 * k)).isoformat(),
                            watts=nivel))
        i = item(calc(revs))
        self.assertGreater(i['nivel_w'], 322.0)
        self.assertLessEqual(i['nivel_w'], 330.0)

    def test_valor_atipico_isolado_nao_domina_o_nivel(self):
        base = serie_sintetica(12, 300.0, 2.0, seed=2, passo=7)
        sujo = base[:6] + [{**base[6], 'watts': 450.0}] + base[7:]
        i = item(calc(sujo, curvas=curvas_de(sujo)))
        self.assertLess(abs(i['nivel_w'] - 300.0), 25.0)
        self.assertTrue(any(p['atipica'] for p in i['trajetoria']))

    def test_sensibilidade_a_q_documentada(self):
        revs = serie_sintetica(10, 300.0, 5.0, seed=4)
        niveis = [item(calc(revs, q=q))['nivel_w'] for q in (0.001, 0.01, 0.1, 1.0)]
        for n in niveis:
            self.assertLess(abs(n - 300.0), 10.0)
        self.assertEqual(len(set(niveis)), 4)       # q influencia: e o hiperparametro assumido


class Reproducao(unittest.TestCase):
    def test_repeticao_identica_e_assinatura_estavel(self):
        revs = serie_sintetica(7, 300.0, 4.0)
        a, b = calc(copy.deepcopy(revs)), calc(copy.deepcopy(revs))
        self.assertEqual(a, b)
        self.assertEqual(len(a['assinatura']), 64)

    def test_assinatura_muda_com_dados_parametros_ou_data(self):
        revs = serie_sintetica(5, 300.0, 4.0)
        ref = calc(revs)['assinatura']
        self.assertNotEqual(ref, calc(revs, q=0.02)['assinatura'])
        self.assertNotEqual(ref, calc(revs[:-1])['assinatura'])
        self.assertNotEqual(ref, calc(revs, ate='2027-06-30')['assinatura'])

    def test_sequencia_usada_permite_refazer_o_calculo(self):
        revs = serie_sintetica(6, 300.0, 4.0)
        i = item(calc(revs))
        refeito = [{k: s[k] for k in ('test_id', 'activity_id', 'hora_local', 'data', 'watts')}
                   for s in i['sequencia_usada']]
        st = E.estimar_serie(refeito, AT)
        self.assertEqual(st['resumo']['nivel_w'], i['nivel_w'])
        self.assertEqual(st['resumo']['incerteza'], i['incerteza'])

    def test_nao_altera_as_entradas(self):
        revs = serie_sintetica(5, 300.0, 4.0)
        a, c = ativ(*{r['activity_id'] for r in revs}), curvas_de(revs)
        cr, ca, cc = copy.deepcopy(revs), copy.deepcopy(a), copy.deepcopy(c)
        E.calcular_estado(revs, a, c, AT)
        self.assertEqual((revs, a, c), (cr, ca, cc))

    def test_ate_tem_de_ser_explicito_e_valido(self):
        for ate in (None, '', '2026-13-40', 'hoje'):
            with self.assertRaises(ValueError):
                E.calcular_estado([], [], [], ate)


class SemFuturo(unittest.TestCase):
    def test_estimativa_historica_nao_usa_dados_posteriores(self):
        revs = serie_sintetica(10, 300.0, 4.0)
        corte = revs[5]['data']
        com_futuro = item(calc(revs, ate=corte))
        so_passado = item(calc(revs[:6], ate=corte))
        self.assertEqual(com_futuro['nivel_w'], so_passado['nivel_w'])
        self.assertEqual(com_futuro['incerteza'], so_passado['incerteza'])
        self.assertEqual(com_futuro['n_usadas'], 6)
        self.assertEqual(com_futuro['motivos_exclusao'], {'posterior_a_data_de_referencia': 4})

    def test_valor_futuro_extremo_nao_muda_o_passado(self):
        revs = serie_sintetica(6, 300.0, 4.0)
        futuro = rev(aid='fut', data='2027-06-01', watts=9999.0)
        corte = revs[-1]['data']
        a = item(calc(revs, ate=corte))
        b = item(calc(revs + [futuro], ate=corte, atividades=ativ('fut', *[r['activity_id'] for r in revs]),
                      curvas=curvas_de(revs + [futuro])))
        self.assertEqual(a['nivel_w'], b['nivel_w'])
        self.assertEqual(a['incerteza'], b['incerteza'])

    def test_trajetoria_e_causal(self):
        revs = serie_sintetica(8, 300.0, 4.0)
        completa = item(calc(revs))['trajetoria']
        for k in range(1, 8):
            parcial = item(calc(revs[:k]))['trajetoria']
            self.assertEqual(parcial, completa[:k])


class DadosAusentes(unittest.TestCase):
    def test_sem_atividades_nem_curvas_nada_entra(self):
        r = calc(serie_sintetica(4), atividades=None, curvas=None)
        i = item(r)
        self.assertEqual((i['n_confirmados'], i['n_usadas']), (4, 0))
        self.assertEqual(i['estado_calibracao'], E.EST_NAO_CALIBRADO)
        self.assertEqual(i['motivos_exclusao'], {'elegibilidade_desconhecida:atividades_indisponiveis': 4})

    def test_campos_em_falta_na_revisao_nao_rebentam(self):
        r = rev()
        for campo in ('watts', 'data', 'confirmado_em', 'modalidade', 'duracao_s'):
            x = {k: v for k, v in r.items() if k != campo}
            try:
                calc([x], atividades=ativ('a1'), curvas=curvas_de([r]))
            except E.HistoricoAmbiguo:
                pass      # ambiguidade declarada e aceitavel; excecao generica nao


if __name__ == '__main__':
    unittest.main()
