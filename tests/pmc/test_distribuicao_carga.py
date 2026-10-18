"""Distribuicao da carga por modalidade (Bike/Row/Run/Ski): nucleo puro. Dados SINTETICOS: provam a
aritmetica e as exclusoes, nao validade fisiologica nem preditiva."""
import copy
import os
import sys
import re
import unittest
import unittest.mock
from datetime import date, timedelta

os.environ.setdefault('INTERVALS_ICU_API_KEY', 'ficticia-teste')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import config  # noqa: E402
import distribuicao_carga as dc  # noqa: E402
import modelo_testes as mt  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402

D0 = date(2026, 1, 1)
_id = [0]


def ativ(tipo, dia, carga, aid=None, **extra):
    _id[0] += 1
    a = {'id': aid if aid is not None else _id[0], 'type': tipo,
         'start_date_local': (D0 + timedelta(days=dia)).isoformat() + 'T08:00:00',
         'icu_training_load': carga}
    a.update(extra)
    return a


def base():
    """Quatro modalidades, 10 dias; janela 7 termina no dia 9."""
    return [ativ('Ride', 0, 100), ativ('Row', 2, 50), ativ('Run', 4, 30), ativ('NordicSki', 6, 20),
            ativ('Ride', 8, 100), ativ('Run', 9, 10)]


def ultimo(r, mod):
    return r['serie']['pct'][mod][-1]


class FiltroDeModalidade(unittest.TestCase):
    def test_modalidades_sao_as_do_modelo_ir_e_do_config(self):
        self.assertEqual(dc.MODALIDADES, tuple(config.CICLICOS))
        self.assertEqual(set(dc.MODALIDADES), set(mt.MODALIDADES))
        self.assertEqual(set(dc.MODALIDADES), {'Bike', 'Row', 'Run', 'Ski'})

    def test_musculacao_e_nao_mapeados_ficam_fora_por_correspondencia_exata(self):
        for t in ('WeightTraining', 'Workout', 'Crossfit', 'Yoga', 'Swim', 'Pilates', '', None, 123,
                  'weighttraining', 'Ride ', 'Weight', 'Run2'):
            if t in config.TYPE_MAP and config.TYPE_MAP[t] in dc.MODALIDADES:
                continue
            self.assertIsNone(dc.modalidade_elegivel(t), t)
        self.assertEqual(dc.modalidade_elegivel('Ride'), 'Bike')
        self.assertEqual(dc.modalidade_elegivel('Run'), 'Run')

    def test_musculacao_nao_aparece_em_nenhuma_estrutura_do_resultado(self):
        ats = base() + [ativ('WeightTraining', 5, 500), ativ('Workout', 7, 300), ativ('Crossfit', 8, 200)]
        r = dc.distribuicao(ats, janela=7)
        self.assertEqual(set(r['serie']['pct']), set(dc.MODALIDADES))
        self.assertEqual(set(r['serie']['carga']), set(dc.MODALIDADES))
        self.assertEqual(set(r['cores']), set(dc.MODALIDADES))
        self.assertEqual(set(r['fonte_da_carga_pct']), set(dc.MODALIDADES))
        self.assertEqual(r['info']['n_fora_das_quatro_modalidades'], 3)
        self.assertNotIn('weight', repr(r['serie']).lower())

    def test_musculacao_com_carga_enorme_nao_altera_as_quatro_percentagens(self):
        a = dc.distribuicao(base(), janela=7)
        b = dc.distribuicao(base() + [ativ('WeightTraining', d, 10 ** 6) for d in range(0, 10)]
                            + [ativ('Workout', 5, 10 ** 7), ativ('Crossfit', 6, 10 ** 7)], janela=7)
        self.assertEqual(a['serie'], b['serie'])
        self.assertEqual(a['data_ini'], b['data_ini'])
        self.assertEqual(a['data_fim'], b['data_fim'])
        for m in dc.MODALIDADES:
            self.assertEqual(a['serie']['pct'][m], b['serie']['pct'][m])
        self.assertAlmostEqual(sum(ultimo(b, m) for m in dc.MODALIDADES), 100.0, places=2)

    def test_so_musculacao_nao_gera_serie(self):
        r = dc.distribuicao([ativ('WeightTraining', d, 100) for d in range(40)], janela=28)
        self.assertEqual(r['estado'], 'sem_atividades')
        self.assertNotIn('serie', r)

    def test_musculacao_nao_alarga_o_periodo(self):
        ats = base() + [ativ('WeightTraining', 400, 100), ativ('WeightTraining', -400, 100)]
        r = dc.distribuicao(ats, janela=7)
        self.assertEqual(r['ultima_atividade'], (D0 + timedelta(days=9)).isoformat())
        self.assertEqual(r['primeira_atividade'], D0.isoformat())

    def test_outras_modalidades_inelegiveis_ficam_fora(self):
        ats = base() + [ativ('Swim', 3, 400), ativ('Yoga', 4, 400), ativ('Pilates', 5, 400)]
        a = dc.distribuicao(base(), janela=7)
        b = dc.distribuicao(ats, janela=7)
        self.assertEqual(a['serie'], b['serie'])

    def test_tipos_do_mesmo_grupo_mapeiam_para_a_mesma_modalidade(self):
        for cru, mod in config.TYPE_MAP.items():
            if mod in dc.MODALIDADES:
                self.assertEqual(dc.modalidade_elegivel(cru), mod)


class JanelaEPercentagens(unittest.TestCase):
    def test_cargas_acumuladas_da_janela_incluem_o_dia_de_referencia(self):
        r = dc.distribuicao(base(), janela=7)
        i = r['serie']['datas'].index((D0 + timedelta(days=9)).isoformat())
        # janela [dia 3, dia 9]: Run 30 + Ski 20 + Bike 100 + Run 10 (Row do dia 2 e Bike do dia 0 ficam fora)
        self.assertEqual(r['serie']['carga']['Bike'][i], 100)
        self.assertEqual(r['serie']['carga']['Row'][i], 0)
        self.assertEqual(r['serie']['carga']['Run'][i], 40)
        self.assertEqual(r['serie']['carga']['Ski'][i], 20)
        self.assertEqual(r['serie']['total'][i], 160)

    def test_percentagens_e_soma_100(self):
        r = dc.distribuicao(base(), janela=7)
        i = len(r['serie']['datas']) - 1
        self.assertAlmostEqual(r['serie']['pct']['Bike'][i], 62.5, places=3)
        self.assertAlmostEqual(r['serie']['pct']['Run'][i], 25.0, places=3)
        self.assertAlmostEqual(r['serie']['pct']['Ski'][i], 12.5, places=3)
        for k in range(len(r['serie']['datas'])):
            if r['serie']['estado'][k].startswith('ok'):
                self.assertAlmostEqual(sum(r['serie']['pct'][m][k] for m in dc.MODALIDADES), 100.0, places=2)

    def test_fronteira_da_janela_o_dia_que_sai_deixa_de_contar(self):
        ats = [ativ('Ride', 0, 100), ativ('Run', 1, 100)]
        r = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=7)).isoformat())
        datas = r['serie']['datas']
        # dia 6 = ultimo dia da janela [0,6]: Bike e Run presentes; dia 7 = [1,7]: Bike saiu
        i6, i7 = datas.index((D0 + timedelta(days=6)).isoformat()), datas.index((D0 + timedelta(days=7)).isoformat())
        self.assertEqual(r['serie']['carga']['Bike'][i6], 100)
        self.assertEqual(r['serie']['carga']['Bike'][i7], 0)
        self.assertEqual(r['serie']['pct']['Run'][i7], 100.0)
        self.assertEqual(r['serie']['pct']['Bike'][i6], 50.0)

    def test_primeira_janela_so_existe_quando_completa(self):
        r = dc.distribuicao(base(), janela=7)
        self.assertEqual(r['data_ini'], (D0 + timedelta(days=6)).isoformat())
        r = dc.distribuicao(base(), janela=28)
        self.assertEqual(r['estado'], 'janela_incompleta')

    def test_janelas_permitidas_e_invalidas(self):
        ats = [ativ('Ride', d, 10) for d in range(100)]
        for w in (7, 28, 42, 90):
            self.assertEqual(dc.distribuicao(ats, janela=w)['estado'], 'ok')
        for w in (0, 1, 5, 29, 365, None, '28', 7.0 if False else -7):
            with self.assertRaises(ValueError):
                dc.distribuicao(ats, janela=w)

    def test_numerador_e_denominador_usam_os_mesmos_dias_em_todas_as_janelas(self):
        ats = []
        for d in range(120):
            ats.append(ativ(('Ride', 'Row', 'Run', 'NordicSki')[d % 4], d, 10 + (d * 7) % 23))
        for w in dc.JANELAS:
            r = dc.distribuicao(ats, janela=w)
            for k, dia in enumerate(r['serie']['datas']):
                dd = date.fromisoformat(dia)
                alvo = {m: 0.0 for m in dc.MODALIDADES}
                for a in ats:
                    x = date.fromisoformat(a['start_date_local'][:10])
                    if dd - timedelta(days=w - 1) <= x <= dd:
                        alvo[dc.modalidade_elegivel(a['type'])] += a['icu_training_load']
                for m in dc.MODALIDADES:
                    self.assertAlmostEqual(r['serie']['carga'][m][k], alvo[m], places=2)
                self.assertAlmostEqual(r['serie']['total'][k], sum(alvo.values()), places=2)

    def test_varias_atividades_no_mesmo_dia_somam(self):
        ats = [ativ('Ride', 0, 30), ativ('Ride', 0, 70), ativ('Run', 0, 100), ativ('Row', 3, 0)]
        r = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat())
        self.assertEqual(r['serie']['carga']['Bike'][0], 100)
        self.assertEqual(r['serie']['pct']['Bike'][0], 50.0)
        self.assertEqual(r['serie']['pct']['Run'][0], 50.0)

    def test_duplicados_pelo_mesmo_id_contam_uma_vez(self):
        ats = [ativ('Ride', 0, 100, aid='x1'), ativ('Ride', 0, 100, aid='x1'), ativ('Run', 0, 100, aid='x2')]
        r = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat())
        self.assertEqual(r['serie']['carga']['Bike'][0], 100)
        self.assertEqual(r['info']['n_duplicadas'], 1)
        # ids distintos com o mesmo conteudo NAO sao duplicados
        ats = [ativ('Ride', 0, 100, aid='y1'), ativ('Ride', 0, 100, aid='y2')]
        r = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat())
        self.assertEqual(r['serie']['carga']['Bike'][0], 200)

    def test_nao_altera_as_atividades_recebidas(self):
        ats = base() + [ativ('WeightTraining', 3, 99)]
        antes = copy.deepcopy(ats)
        dc.distribuicao(ats, janela=7)
        self.assertEqual(ats, antes)


class DiasSemTreinoEDadosAusentes(unittest.TestCase):
    def test_dia_de_descanso_conta_zero_e_nao_e_ausente(self):
        r = dc.distribuicao(base(), janela=7)
        self.assertTrue(all(n == 0 for n in r['serie']['n_ausentes_janela']))
        self.assertTrue(all(e == 'ok' for e in r['serie']['estado']))

    def test_carga_ausente_nao_vira_zero_silencioso(self):
        ats = base() + [ativ('Run', 8, None), ativ('Row', 8, float('nan')), ativ('Ski', 8, -5),
                        ativ('Ride', 8, 'abc'), ativ('Ride', 7, True)]
        r = dc.distribuicao(ats, janela=7)
        i = len(r['serie']['datas']) - 1
        self.assertEqual(r['serie']['estado'][i], 'ok_com_ausentes')
        self.assertEqual(r['serie']['n_ausentes_janela'][i], 5)
        self.assertEqual(r['info']['n_sem_carga']['Run'], 1)
        self.assertAlmostEqual(sum(r['serie']['pct'][m][i] for m in dc.MODALIDADES), 100.0, places=2)
        # o valor ausente nao contribui para nenhuma carga
        self.assertEqual(r['serie']['carga']['Row'][i], 0)

    def test_denominador_zero_sem_treino_nao_inventa_percentagens(self):
        ats = [ativ('Ride', 0, 100), ativ('Run', 20, 100)]
        r = dc.distribuicao(ats, janela=7)
        k = r['serie']['datas'].index((D0 + timedelta(days=12)).isoformat())
        self.assertEqual(r['serie']['estado'][k], 'sem_treino')
        for m in dc.MODALIDADES:
            self.assertIsNone(r['serie']['pct'][m][k])
            self.assertEqual(r['serie']['carga'][m][k], 0)
        self.assertEqual(r['serie']['total'][k], 0)

    def test_denominador_zero_por_carga_ausente_distingue_se_de_sem_treino(self):
        ats = [ativ('Ride', 0, 100), ativ('Run', 10, None), ativ('Run', 20, 100)]
        r = dc.distribuicao(ats, janela=7)
        k = r['serie']['datas'].index((D0 + timedelta(days=12)).isoformat())
        self.assertEqual(r['serie']['estado'][k], 'carga_ausente')
        self.assertIsNone(r['serie']['pct']['Run'][k])
        self.assertEqual(r['serie']['n_ausentes_janela'][k], 1)

    def test_carga_zero_valida_e_zero_nao_e_ausente(self):
        ats = [ativ('Ride', 0, 0), ativ('Run', 1, 0), ativ('Row', 2, 10)]
        r = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat())
        self.assertEqual(r['serie']['n_ausentes_janela'][0], 0)
        self.assertEqual(r['serie']['pct']['Row'][0], 100.0)

    def test_sem_atividades_ou_entradas_invalidas(self):
        for ats in (None, [], [None, 3, 'x', {}], [{'type': 'Ride'}], [{'type': 'Ride', 'start_date_local': 'lixo'}]):
            r = dc.distribuicao(ats)
            self.assertIn(r['estado'], ('sem_atividades',))
        r = dc.distribuicao([{'type': 'Ride', 'start_date_local': 'lixo', 'icu_training_load': 5}])
        self.assertEqual(r['info']['n_sem_data'], 1)

    def test_parametros_invalidos(self):
        for dias in (0, -1, 'a', True, dc.HORIZONTE_MAX + 1):
            with self.assertRaises(ValueError):
                dc.distribuicao(base(), janela=7, dias=dias)
        with self.assertRaises(ValueError):
            dc.distribuicao(base(), janela=7, ate='2026-13-40')

    def test_dias_limita_a_serie_aos_mais_recentes(self):
        ats = [ativ('Ride', d, 10) for d in range(100)]
        r = dc.distribuicao(ats, janela=7, dias=10)
        self.assertEqual(len(r['serie']['datas']), 10)
        self.assertEqual(r['data_fim'], (D0 + timedelta(days=99)).isoformat())


class UnidadeEComparabilidade(unittest.TestCase):
    def test_declara_unidade_formula_e_aviso_sem_causalidade(self):
        r = dc.distribuicao(base(), janela=7)
        self.assertIn('icu_training_load', r['unidade'])
        self.assertIn('100', r['formula'])
        self.assertIn('nao e influencia fisiologica', r['aviso'])
        self.assertEqual(r['tipo_analise'], 'distribuicao_observada_da_carga')

    def test_composicao_por_fonte(self):
        ats = [ativ('Ride', 0, 60, power_load=60), ativ('Ride', 1, 40, hr_load=40),
               ativ('Run', 2, 50, pace_load=50), ativ('Run', 3, 10)]
        r = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat())
        self.assertEqual(r['fonte_da_carga_pct']['Bike'], {'hr_load': 40.0, 'power_load': 60.0})
        self.assertEqual(r['fonte_da_carga_pct']['Run'], {'outra': 16.7, 'pace_load': 83.3})
        self.assertEqual(r['fonte_da_carga_pct']['Row'], {})

    def test_consistente_com_a_carga_por_modalidade_do_modelo_ir(self):
        ats = base() + [ativ('WeightTraining', 4, 900), ativ('Run', 5, None), ativ('Ride', 0, 100, aid='dup'),
                        ativ('Ride', 0, 100, aid='dup')]
        ag = dc.agregar(ats)
        ref, _ = mis.carga_por_modalidade(ats)
        for m in dc.MODALIDADES:
            self.assertEqual(sorted(ag['carga'][m].items()), sorted((k, v) for k, v in ref[m].items()), m)


def ativ2(tipo, dia, tl, trimp, **extra):
    """Atividade com os dois metodos (valores distintos de proposito: nao sao a mesma escala)."""
    return ativ(tipo, dia, tl, trimp=trimp, **extra)


def base2():
    """Mesmo desenho de base(), com TRIMP em escala propria."""
    return [ativ2('Ride', 0, 100, 80), ativ2('Row', 2, 50, 100), ativ2('Run', 4, 30, 60),
            ativ2('NordicSki', 6, 20, 40), ativ2('Ride', 8, 100, 80), ativ2('Run', 9, 10, 20)]


def pct(r, mod, i=-1):
    return r['serie']['pct'][mod][i]


class MetodoTrimp(unittest.TestCase):
    def test_distribuicao_training_load_e_trimp_corretas_e_somam_100(self):
        tl = dc.distribuicao(base2(), janela=7, metodo='training_load')
        tr = dc.distribuicao(base2(), janela=7, metodo='trimp')
        self.assertEqual(tl['metodo'], 'training_load')
        self.assertEqual(tr['metodo'], 'trimp')
        # janela [3, 9]: TL Bike 100, Run 30+10, Ski 20 = 160 ; TRIMP Bike 80, Run 60+20, Ski 40 = 200
        self.assertAlmostEqual(pct(tl, 'Bike'), 62.5, places=3)
        self.assertAlmostEqual(pct(tl, 'Run'), 25.0, places=3)
        self.assertAlmostEqual(pct(tl, 'Ski'), 12.5, places=3)
        self.assertAlmostEqual(pct(tr, 'Bike'), 40.0, places=3)
        self.assertAlmostEqual(pct(tr, 'Run'), 40.0, places=3)
        self.assertAlmostEqual(pct(tr, 'Ski'), 20.0, places=3)
        self.assertEqual(pct(tr, 'Row'), 0.0)
        self.assertEqual(tl['serie']['total'][-1], 160)
        self.assertEqual(tr['serie']['total'][-1], 200)
        for r in (tl, tr):
            for k, e in enumerate(r['serie']['estado']):
                if e.startswith('ok'):
                    self.assertAlmostEqual(sum(r['serie']['pct'][m][k] for m in dc.MODALIDADES), 100.0, places=2)

    def test_default_e_training_load_e_metodo_invalido_levanta(self):
        self.assertEqual(dc.distribuicao(base2(), janela=7)['serie'], dc.distribuicao(base2(), janela=7, metodo='training_load')['serie'])
        for m in ('TRIMP', 'trimp ', '', None, 'srpe', 3):
            with self.assertRaises(ValueError):
                dc.distribuicao(base2(), janela=7, metodo=m)

    def test_mesmas_janelas_e_datas_nos_dois_metodos(self):
        tl = dc.distribuicao(base2(), janela=7, metodo='training_load')
        tr = dc.distribuicao(base2(), janela=7, metodo='trimp')
        self.assertEqual(tl['serie']['datas'], tr['serie']['datas'])
        self.assertEqual(tl['comparacao']['datas'], tr['comparacao']['datas'])
        self.assertEqual(tl['comparacao'], tr['comparacao'])            # a comparacao nao depende do metodo escolhido

    def test_musculacao_fora_dos_dois_metodos_mesmo_com_carga_e_trimp_enormes(self):
        ext = ([ativ2('WeightTraining', d, 10 ** 6, 10 ** 6) for d in range(10)]
               + [ativ2('Workout', 5, 10 ** 7, 10 ** 7), ativ2('Crossfit', 6, 10 ** 7, 10 ** 7),
                  ativ2('WeightTraining', 400, 5, 5)])
        for metodo in dc.METODOS:
            a = dc.distribuicao(base2(), janela=7, metodo=metodo)
            b = dc.distribuicao(base2() + ext, janela=7, metodo=metodo)
            for k in ('serie', 'comparacao', 'cobertura', 'cobertura_periodo', 'data_ini', 'data_fim',
                      'ultima_atividade', 'parametros_trimp'):
                self.assertEqual(a[k], b[k], (metodo, k))
            self.assertEqual(b['info']['n_fora_das_quatro_modalidades'], 13)
            self.assertNotRegex(repr(b['serie']) + repr(b['comparacao']) + repr(b['cobertura']), r'(?i)weight|workout|crossfit')

    def test_so_musculacao_com_trimp_nao_gera_serie_em_nenhum_metodo(self):
        ats = [ativ2('WeightTraining', d, 100, 100) for d in range(40)]
        for m in dc.METODOS:
            self.assertEqual(dc.distribuicao(ats, janela=28, metodo=m)['estado'], 'sem_atividades')

    def test_valores_de_trimp_invalidos_ou_em_falta_ficam_fora_e_com_motivo(self):
        ats = base2() + [ativ('Run', 8, 50),                                     # sem campo trimp
                         ativ('Run', 8, 50, trimp=None), ativ('Row', 8, 50, trimp=0), ativ('Ski', 8, 50, trimp=-3),
                         ativ('Bike', 8, 50, trimp=float('nan')), ativ('Ride', 8, 50, trimp='abc'),
                         ativ('Ride', 8, 50, trimp=True), ativ('Ride', 8, 50, trimp=80, icu_ignore_hr=True),
                         ativ('Ride', 8, 50, trimp=80, icu_ignore_hr='true')]
        tr = dc.distribuicao(ats, janela=7, metodo='trimp')
        cob = tr['cobertura']
        self.assertEqual(cob['total']['elegiveis'], 4 + 9)                         # 4 da base na janela [3,9] (dias 4,6,8,9) + 9 do dia 8
        self.assertEqual(tr['serie']['carga']['Row'][-1], 0)                      # trimp 0 nunca entra
        self.assertEqual(tr['serie']['carga']['Ski'][-1], 40)                     # trimp negativo nunca entra
        self.assertEqual(tr['serie']['carga']['Bike'][-1], 80)                    # nan, texto, bool, FC ignorada: fora
        self.assertEqual(tr['serie']['carga']['Run'][-1], 80)                     # None e ausente: fora
        sd = cob['total']['sem_dados']
        self.assertEqual(sd['fc_ignorada'], 2)
        self.assertEqual(sd['sem_valor'], 2)                                      # Run sem campo e Run None
        self.assertEqual(sd['valor_invalido'], 5)                                 # 0, -3, nan, 'abc', True
        self.assertEqual(tr['serie']['estado'][-1], 'ok_com_ausentes')

    def test_cobertura_incompleta_conta_elegiveis_validas_e_excluidas(self):
        ats = [ativ2('Ride', 0, 10, 10), ativ('Ride', 1, 10), ativ('Row', 2, 10, trimp=5, icu_ignore_hr=True),
               ativ2('Run', 3, 10, 7), ativ('Ski', 4, 10, trimp=0), ativ2('Run', 5, 10, 3)]
        tr = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat(), metodo='trimp')
        c = tr['cobertura']
        self.assertEqual((c['total']['elegiveis'], c['total']['validas']), (6, 3))
        self.assertEqual(c['Bike'], {'elegiveis': 2, 'validas': 1, 'sem_dados': {'sem_valor': 1, 'valor_invalido': 0, 'fc_ignorada': 0}})
        self.assertEqual(c['Row']['sem_dados']['fc_ignorada'], 1)
        self.assertEqual(c['Ski']['sem_dados']['valor_invalido'], 1)
        self.assertEqual(tr['serie']['n_elegiveis_janela'][-1], 6)
        self.assertEqual(tr['serie']['n_validas_janela'][-1], 3)
        self.assertEqual(tr['serie']['n_ausentes_janela'][-1], 3)
        self.assertEqual(tr['serie']['estado'][-1], 'ok_com_ausentes')           # distribuicao so das atividades com TRIMP
        tl = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat(), metodo='training_load')
        self.assertEqual(tl['cobertura']['total']['validas'], 6)                 # a mesma janela tem cobertura total em TL
        self.assertEqual(tl['serie']['estado'][-1], 'ok')

    def test_denominador_trimp_zero_nao_inventa_percentagens_e_distingue_causas(self):
        ats = [ativ('Ride', 0, 100), ativ('Run', 1, 100, trimp=50, icu_ignore_hr=True), ativ('Row', 20, 10, trimp=5)]
        tr = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat(), metodo='trimp')
        self.assertEqual(tr['serie']['estado'][0], 'carga_ausente')
        for m in dc.MODALIDADES:
            self.assertIsNone(tr['serie']['pct'][m][0])
        self.assertEqual(tr['serie']['total'][0], 0)
        tl = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat(), metodo='training_load')
        self.assertEqual(tl['serie']['estado'][0], 'ok')                          # o outro metodo continua a ter dados
        self.assertIsNone(tr['comparacao']['dif_pp']['Bike'][0])                  # sem diferenca onde um lado nao existe
        # dia sem treino
        r = dc.distribuicao(ats, janela=7, metodo='trimp', dias=5)
        self.assertEqual(r['serie']['estado'][-1], 'ok')

    def test_fronteira_da_janela_com_trimp(self):
        ats = [ativ2('Ride', 0, 1, 100), ativ2('Run', 1, 1, 100)]
        r = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=7)).isoformat(), metodo='trimp')
        d = r['serie']['datas']
        i6, i7 = d.index((D0 + timedelta(days=6)).isoformat()), d.index((D0 + timedelta(days=7)).isoformat())
        self.assertEqual(r['serie']['pct']['Bike'][i6], 50.0)
        self.assertEqual(r['serie']['carga']['Bike'][i7], 0)
        self.assertEqual(r['serie']['pct']['Run'][i7], 100.0)
        self.assertEqual(r['serie']['n_elegiveis_janela'][i6], 2)
        self.assertEqual(r['serie']['n_elegiveis_janela'][i7], 1)

    def test_varias_atividades_no_mesmo_dia_e_duplicados_no_trimp(self):
        ats = [ativ2('Ride', 0, 1, 30, aid='a'), ativ2('Ride', 0, 1, 30, aid='a'), ativ2('Ride', 0, 1, 70, aid='b'),
               ativ2('Run', 0, 1, 100, aid='c')]
        r = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat(), metodo='trimp')
        self.assertEqual(r['serie']['carga']['Bike'][0], 100)
        self.assertEqual(r['serie']['pct']['Bike'][0], 50.0)
        self.assertEqual(r['info']['n_duplicadas'], 1)
        self.assertEqual(r['cobertura']['total']['elegiveis'], 3)

    def test_nao_mistura_trimp_e_training_load(self):
        a = base2()
        b = copy.deepcopy(a)
        for x in b:
            x['icu_training_load'] = (x['icu_training_load'] or 0) * 1000 + 7       # so o Training Load muda
        c = copy.deepcopy(a)
        for x in c:
            x['trimp'] = x['trimp'] * 1000 + 7                                      # so o TRIMP muda
        tr_a, tr_b = dc.distribuicao(a, janela=7, metodo='trimp'), dc.distribuicao(b, janela=7, metodo='trimp')
        sem_dif = lambda r: {k: v for k, v in r['serie'].items() if k != 'dif_max_pp_entre_metodos'}   # noqa: E731
        self.assertEqual(sem_dif(tr_a), sem_dif(tr_b))      # so a diferenca entre metodos depende dos dois
        tl_a, tl_c = dc.distribuicao(a, janela=7, metodo='training_load'), dc.distribuicao(c, janela=7, metodo='training_load')
        self.assertEqual(sem_dif(tl_a), sem_dif(tl_c))
        # uma atividade sem TRIMP nunca recebe o Training Load como substituto
        so_tl = [ativ('Ride', 0, 500), ativ2('Run', 0, 1, 10)]
        r = dc.distribuicao(so_tl, janela=7, ate=(D0 + timedelta(days=6)).isoformat(), metodo='trimp')
        self.assertEqual(r['serie']['carga']['Bike'][0], 0)
        self.assertEqual(r['serie']['pct']['Run'][0], 100.0)
        # e vice-versa: sem Training Load valido, o TRIMP nao o substitui
        so_trimp = [ativ('Ride', 0, None, trimp=500), ativ2('Run', 0, 10, 1)]
        r = dc.distribuicao(so_trimp, janela=7, ate=(D0 + timedelta(days=6)).isoformat(), metodo='training_load')
        self.assertEqual(r['serie']['carga']['Bike'][0], 0)

    def test_valores_absolutos_com_unidades_distintas_declaradas(self):
        tr = dc.distribuicao(base2(), janela=7, metodo='trimp')
        self.assertIn('TRIMP', tr['unidade'])
        self.assertIn('icu_training_load', tr['unidades']['training_load'])
        self.assertIn('Banister', tr['definicao_metodo'])
        self.assertIn('FC MEDIA', tr['definicao_metodo'])
        self.assertIn('nao sao diretamente comparaveis', tr['comparacao']['aviso'])
        self.assertIn('conta atividades com TRIMP valido', tr['aviso'])

    def test_parametros_de_trimp_fc_max_por_modalidade(self):
        ats = [ativ2('Ride', 0, 1, 10, athlete_max_hr=190), ativ2('Ride', 1, 1, 10, athlete_max_hr=190),
               ativ2('Row', 2, 1, 10, athlete_max_hr=188), ativ2('Run', 3, 1, 10), ativ2('Ski', 4, 1, 10, athlete_max_hr=182)]
        r = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat(), metodo='trimp')
        p = r['parametros_trimp']['fc_max_usada_por_modalidade']
        self.assertEqual(p, {'Bike': {'190': 2}, 'Row': {'188': 1}, 'Run': {}, 'Ski': {'182': 1}})


class ComparacaoEntreMetodos(unittest.TestCase):
    def test_diferenca_em_pontos_percentuais_trimp_menos_tl(self):
        r = dc.distribuicao(base2(), janela=7)
        c = r['comparacao']
        for k in range(len(c['datas'])):
            if c['dif_pp']['Bike'][k] is None:
                continue
            for m in dc.MODALIDADES:
                esperado = c['pct']['trimp'][m][k] - c['pct']['training_load'][m][k]
                self.assertAlmostEqual(c['dif_pp'][m][k], esperado, places=2)
            self.assertAlmostEqual(sum(c['dif_pp'][m][k] for m in dc.MODALIDADES), 0.0, places=2)   # ambas somam 100
            self.assertAlmostEqual(c['dif_max_pp'][k], max(abs(c['dif_pp'][m][k]) for m in dc.MODALIDADES), places=1)
        # ultimo dia: Bike 40 - 62,5 ; Row 0 - 0 ; Run 40 - 25 ; Ski 20 - 12,5
        self.assertAlmostEqual(c['dif_pp']['Bike'][-1], -22.5, places=2)
        self.assertAlmostEqual(c['dif_pp']['Run'][-1], 15.0, places=2)
        self.assertAlmostEqual(c['dif_pp']['Ski'][-1], 7.5, places=2)
        self.assertAlmostEqual(c['dif_max_pp'][-1], 22.5, places=1)

    def test_metricas_proporcionais_dao_diferenca_zero(self):
        ats = [ativ2('Ride', d, 10 + d, 2 * (10 + d)) if d % 2 else ativ2('Run', d, 10 + d, 2 * (10 + d)) for d in range(20)]
        c = dc.distribuicao(ats, janela=7)['comparacao']
        self.assertTrue(all(x == 0 for x in c['dif_max_pp']))
        self.assertTrue(all(x == 0 for x in c['controlo_mesmas_atividades']['dif_max_pp']))

    def test_valores_absolutos_acumulados_de_cada_metrica(self):
        c = dc.distribuicao(base2(), janela=7)['comparacao']
        self.assertEqual(c['total_absoluto']['training_load'][-1], 160)
        self.assertEqual(c['total_absoluto']['trimp'][-1], 200)
        self.assertEqual(c['carga_absoluta']['trimp']['Run'][-1], 80)
        self.assertEqual(c['carga_absoluta']['training_load']['Run'][-1], 40)

    def test_cobertura_nos_dois_metodos_e_controlo_das_mesmas_atividades(self):
        # a Run de 20 so tem Training Load: a cobertura difere e o controlo retira essa atividade dos dois lados
        ats = [ativ2('Ride', 0, 50, 50), ativ2('Row', 1, 50, 50), ativ('Run', 2, 100)]
        r = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat())
        c = r['comparacao']
        self.assertEqual(c['n_validas_janela']['training_load'][0], 3)
        self.assertEqual(c['n_validas_janela']['trimp'][0], 2)
        self.assertEqual(c['n_validas_ambos_janela'][0], 2)
        self.assertEqual(c['dif_pp']['Run'][0], -50.0)                           # 0 (TRIMP) - 50 (TL)
        self.assertEqual(c['controlo_mesmas_atividades']['dif_pp']['Run'][0], 0.0)  # mesmo subconjunto: sem diferenca
        self.assertEqual(c['controlo_mesmas_atividades']['dif_max_pp'][0], 0.0)
        ul = c['cobertura_ultima_janela']
        self.assertEqual(ul['training_load']['total']['validas'], 3)
        self.assertEqual(ul['trimp']['total']['validas'], 2)
        self.assertEqual(ul['trimp']['Run']['sem_dados']['sem_valor'], 1)

    def test_nao_declara_vencedor_nem_indice(self):
        r = dc.distribuicao(base2(), janela=7)
        self.assertNotRegex(repr({k: v for k, v in r.items() if k in ('aviso', 'comparacao', 'leitura')})
                            .replace('nao ha vencedor', ''), r'(?i)vencedor|melhor metodo|indice de qualidade|score')
        self.assertNotIn('indice', ' '.join(r['comparacao']))

    def test_comparacao_com_um_lado_sem_denominador_nao_inventa_diferenca(self):
        ats = [ativ('Ride', 0, 100), ativ('Run', 1, 100)]                           # nenhuma atividade tem TRIMP
        r = dc.distribuicao(ats, janela=7, ate=(D0 + timedelta(days=6)).isoformat())
        c = r['comparacao']
        self.assertEqual(c['estado']['trimp'][0], 'carga_ausente')
        self.assertEqual(c['estado']['training_load'][0], 'ok')
        self.assertTrue(all(c['dif_pp'][m][0] is None for m in dc.MODALIDADES))
        self.assertIsNone(c['dif_max_pp'][0])

    def test_serie_selecionada_nao_depende_do_outro_metodo_mas_traz_a_diferenca(self):
        r = dc.distribuicao(base2(), janela=7, metodo='trimp')
        self.assertEqual(r['serie']['dif_max_pp_entre_metodos'], r['comparacao']['dif_max_pp'])


# ---------------------------------------------------------------- endpoint e guardas (BD temporaria)
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import test_mmp_deteccao_automatica as T  # noqa: E402

db, APP = T.db, T.APP
URL = '/api/carga/distribuicao'


class Endpoint(T.BaseDB):
    def setUp(self):
        super().setUp()
        self.ativ = base() + [ativ('WeightTraining', d, 10 ** 5) for d in range(10)]

    def get(self, q=''):
        r = self.c.get(URL + q)
        return r.status_code, r.get_json()

    def test_resposta_basica_sem_musculacao(self):
        st, j = self.get('?janela=7')
        self.assertEqual(st, 200, j)
        self.assertEqual(j['status'], 'OK')
        self.assertEqual(j['janela_dias'], 7)
        self.assertEqual(set(j['serie']['pct']), {'Bike', 'Row', 'Run', 'Ski'})
        self.assertAlmostEqual(sum(j['serie']['pct'][m][-1] for m in dc.MODALIDADES), 100.0, places=2)
        self.assertNotIn('WeightTraining', repr(j))
        self.assertEqual(j['serie']['total'][-1], 160)

    def test_metodo_trimp_e_comparacao_pelo_endpoint(self):
        self.ativ[:] = base2() + [ativ2('WeightTraining', d, 10 ** 6, 10 ** 6) for d in range(10)]
        st, tl = self.get('?janela=7&metodo=training_load')
        st2, tr = self.get('?janela=7&metodo=trimp')
        self.assertEqual((st, st2), (200, 200))
        self.assertEqual((tl['metodo'], tr['metodo']), ('training_load', 'trimp'))
        self.assertAlmostEqual(tl['serie']['pct']['Bike'][-1], 62.5, places=3)
        self.assertAlmostEqual(tr['serie']['pct']['Bike'][-1], 40.0, places=3)
        self.assertEqual(tl['comparacao'], tr['comparacao'])
        self.assertAlmostEqual(tl['comparacao']['dif_pp']['Bike'][-1], -22.5, places=2)
        self.assertNotRegex(repr(tl) + repr(tr), r'(?i)weight|crossfit|workout')
        # sem metodo = training_load (comportamento anterior preservado)
        self.assertEqual(self.get('?janela=7')[1]['serie'], tl['serie'])

    def test_metodo_invalido_da_400(self):
        for q in ('?metodo=srpe', '?metodo=TRIMP', '?metodo=', '?metodo=trimp%20'):
            st, j = self.get(q)
            self.assertEqual(st, 400, q)
            self.assertEqual(j.get('campo'), 'metodo')

    def test_padrao_28_dias_e_dados_curtos_nao_inventam(self):
        st, j = self.get()
        self.assertEqual(st, 200)
        self.assertEqual(j['janela_dias'], 28)
        self.assertEqual(j['estado'], 'janela_incompleta')
        self.assertNotIn('serie', j)

    def test_parametros_invalidos_dao_400(self):
        for q in ('?janela=5', '?janela=x', '?dias=0', '?dias=99999', '?dias=a', '?ate=2026-99-01', '?ate=hoje'):
            st, j = self.get(q)
            self.assertEqual(st, 400, q)
            self.assertEqual(j['status'], 'erro')

    def test_sem_base_de_dados_503(self):
        with unittest.mock.patch.object(db, 'ENABLED', False):
            st, _ = self.get('?janela=7')
        self.assertEqual(st, 503)

    def test_ate_explicito_estende_a_serie_com_dias_sem_treino(self):
        st, j = self.get('?janela=7&ate=' + (D0 + timedelta(days=30)).isoformat())
        self.assertEqual(st, 200)
        self.assertTrue(j['fim_explicito'])
        self.assertEqual(j['serie']['estado'][-1], 'sem_treino')
        self.assertIsNone(j['serie']['pct']['Bike'][-1])

    def test_pedidos_nao_alteram_nenhuma_tabela(self):
        db._exec('SELECT 1', fetch='one')
        antes = db._conn.total_changes
        for q in ('', '?janela=7', '?janela=90', '?janela=7&dias=3', '?janela=2', '?ate=x', '?metodo=trimp', '?metodo=trimp&janela=42',
                  '?metodo=training_load&janela=7', '?metodo=x'):
            self.get(q)
        self.assertEqual(db._conn.total_changes, antes)

    def test_nao_usa_a_api_externa(self):
        with unittest.mock.patch('requests.sessions.Session.request', side_effect=AssertionError('rede')):
            st, _ = self.get('?janela=7')
        self.assertEqual(st, 200)


class Guardas(unittest.TestCase):
    def _fonte(self, f):
        return open(os.path.join(T.ROOT, f), encoding='utf-8').read()

    def test_a_rota_e_so_leitura(self):
        src = self._fonte('app.py')
        i = src.index('def api_carga_distribuicao(')
        j = src.find('@app.route(', i)
        j2 = src.find("if __name__ == '__main__'", i)
        corpo = src[i:min(x for x in (j, j2) if x > 0)]
        self.assertNotRegex(corpo, r'_exec\(|INSERT|UPDATE|DELETE|inserir_|upsert|recalibrar|requests|sync\.|fetch_')
        self.assertIn("'/api/carga/distribuicao')", src[src.rindex('@app.route(', 0, i):i])
        self.assertNotIn('methods=', src[src.rindex('@app.route(', 0, i):i])

    def test_o_modulo_nao_importa_bd_rede_nem_app(self):
        src = self._fonte('distribuicao_carga.py')
        self.assertNotRegex(src, r'^\s*(import|from)\s+(db|app|sync|requests|sqlite3)\b', )
        self.assertNotRegex(src, r'_exec\(|INSERT|UPDATE|DELETE')

    def test_nenhum_calculo_de_producao_conhece_o_modulo_novo(self):
        for f in ('pmc.py', 'calibracao.py', 'ftlm.py', 'modelo_ir.py', 'modelo_ir_global.py',
                  'modelo_ir_servico.py', 'modelo_estimador.py', 'modelo_testes.py', 'modelo_validacao.py',
                  'observacoes_auto.py', 'sync.py', 'db.py', 'config.py'):
            self.assertNotIn('distribuicao_carga', self._fonte(f), f)

    def test_filtro_usa_a_lista_central_sem_correspondencia_parcial(self):
        src = self._fonte('distribuicao_carga.py')
        self.assertIn('config.TYPE_MAP.get(tipo_cru)', src)
        self.assertNotRegex(src, r"\bin\s+tipo|tipo\w*\.(lower|startswith|endswith)|'weight|\"weight|re\.(search|match)")


if __name__ == '__main__':
    unittest.main()
