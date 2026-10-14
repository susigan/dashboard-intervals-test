"""Evolucao da performance (pmc.evolucao_performance): regras de qualidade e suficiencia.

    python -m unittest tests.pmc.test_evolucao_performance_unit -v

Funcao pura: sem banco, sem API. Cada teste monta uma fixture explicita e verifica
uma regra da especificacao. Ausencia e None; zero nunca e observacao valida.
"""
import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

import pmc  # noqa: E402

REF = '2026-10-14'
REF_D = date(2026, 10, 14)


def _iso(d):
    return d.isoformat()


def _sessao(d, tipo='Bike', **campos):
    e = {'date': _iso(d), 'type': tipo, 'icu_pm_ftp': None, 'icu_ftp': None, 'EFTP': None}
    e.update(campos)
    return e


def _semanal(offsets_dias, valor_fn, tipo='Bike', campo='icu_pm_ftp'):
    """Uma observacao por offset (dias antes da referencia)."""
    out = []
    for k in offsets_dias:
        out.append(_sessao(REF_D - timedelta(days=k), tipo, **{campo: valor_fn(k)}))
    return out


def _curva(d, tipo='Bike', pontos=None):
    pontos = pontos or {}
    return {'activity_id': f'c{d}', 'type': tipo, 'date': _iso(d),
            'secs': sorted(pontos), 'watts': [pontos[s] for s in sorted(pontos)]}


def _serie(entradas, mod='Bike', curvas=None):
    return pmc.evolucao_performance(entradas, curvas or [], REF, modalidades=(mod,))


def _ftp(res, mod='Bike', fonte='eftp_atividade'):
    return res['modalidades'][mod]['fontes'][fonte]


class ZerosEAusencia(unittest.TestCase):

    def test_zero_nao_e_observacao_valida(self):
        ent = [_sessao(REF_D - timedelta(days=i), icu_pm_ftp=0.0) for i in range(0, 60, 3)]
        s = _ftp(_serie(ent))
        q = s['qualidade']
        self.assertEqual(q['n_zero'], 20)
        self.assertEqual(q['n_validos'], 0)
        self.assertIsNone(s['janelas']['180']['mediana_recente'])

    def test_ausente_e_none_e_nao_vira_zero(self):
        ent = [_sessao(REF_D - timedelta(days=i)) for i in range(0, 60, 3)]
        q = _ftp(_serie(ent))['qualidade']
        self.assertEqual(q['n_ausentes'], 20)
        self.assertEqual(q['n_zero'], 0)
        self.assertEqual(q['n_validos'], 0)

    def test_negativo_e_bool_e_texto_nao_sao_valores(self):
        ent = [_sessao(REF_D - timedelta(days=1), icu_pm_ftp=-5.0),
               _sessao(REF_D - timedelta(days=2), icu_pm_ftp=True),
               _sessao(REF_D - timedelta(days=3), icu_pm_ftp='250')]
        q = _ftp(_serie(ent))['qualidade']
        self.assertEqual((q['n_negativos'], q['n_validos']), (1, 0))
        self.assertEqual(q['n_ausentes'], 2)


class FontesSeparadas(unittest.TestCase):

    def test_eftp_atividade_so_le_icu_pm_ftp(self):
        ent = [_sessao(REF_D - timedelta(days=5), icu_ftp=200.0)]
        self.assertEqual(_ftp(_serie(ent))['qualidade']['n_validos'], 0)
        self.assertEqual(_ftp(_serie(ent), fonte='ftp_configurado')['qualidade']['n_validos'], 1)

    def test_eftp_separado_de_ftp_registrado(self):
        ent = [_sessao(REF_D - timedelta(days=5), EFTP=255.0)]
        self.assertEqual(_ftp(_serie(ent))['qualidade']['n_validos'], 0)
        self.assertEqual(_ftp(_serie(ent), fonte='eftp_nao_confirmado')['qualidade']['n_validos'], 1)

    def test_campo_legado_ftp_nao_e_atribuido_a_nenhuma_fonte(self):
        ent = [_sessao(REF_D - timedelta(days=5), ftp=250.0)]
        for f in ('eftp_atividade', 'ftp_configurado', 'eftp_nao_confirmado'):
            self.assertEqual(_ftp(_serie(ent), fonte=f)['qualidade']['n_validos'], 0, f)

    def test_origem_identificada_no_resultado(self):
        s = _ftp(_serie([]))
        self.assertEqual(s['origem'], 'icu_pm_ftp')
        self.assertEqual(_ftp(_serie([]), fonte='eftp_nao_confirmado')['origem'], 'EFTP')


class SequenciasIdenticas(unittest.TestCase):

    def test_sequencia_longa_e_sinalizada_mas_mantida_sem_evidencia(self):
        # 12 valores iguais espacados de 7 dias = 77 dias. Sem evidencia explicita, NAO exclui.
        ent = [_sessao(REF_D - timedelta(days=7 * k), icu_pm_ftp=200.0) for k in range(12)]
        s = _ftp(_serie(ent))
        self.assertEqual(len(s['qualidade']['sequencias_identicas']), 1)
        bloco = s['qualidade']['sequencias_identicas'][0]
        self.assertEqual(bloco['classificacao'], 'suspeita')
        self.assertFalse(bloco['excluida_da_tendencia'])
        self.assertEqual(s['qualidade']['n_excluidos_tendencia'], 0)
        self.assertEqual(s['qualidade']['n_sequencias_sinalizadas'], 1)
        self.assertEqual(s['qualidade']['n_registros_em_sequencia'], 12)
        self.assertNotIn('indices', bloco)

    def test_sequencia_confirmada_como_padrao_e_excluida_com_motivo(self):
        ent = [_sessao(REF_D - timedelta(days=7 * k), icu_pm_ftp=200.0) for k in range(12)]
        ini, fim = _iso(REF_D - timedelta(days=77)), _iso(REF_D)
        res = pmc.evolucao_performance(ent, [], REF, modalidades=('Bike',),
                                       padroes_confirmados={(ini, fim, 200.0)})
        s = res['modalidades']['Bike']['fontes']['eftp_atividade']
        bloco = s['qualidade']['sequencias_identicas'][0]
        self.assertEqual(bloco['classificacao'], 'padrao_confirmado')
        self.assertTrue(bloco['excluida_da_tendencia'])
        self.assertIn('evidencia', bloco['motivo'])
        self.assertEqual(s['qualidade']['n_excluidos_tendencia'], 12)

    def test_duracao_nao_decide_exclusao(self):
        # 10 valores em 630 dias (espacados): longa no tempo, mas sem evidencia -> mantida
        ent = [_sessao(REF_D - timedelta(days=70 * k), icu_pm_ftp=348.0) for k in range(10)]
        s = _ftp(_serie(ent))
        bloco = s['qualidade']['sequencias_identicas'][0]
        self.assertGreaterEqual(bloco['dias'], 60)
        self.assertFalse(bloco['excluida_da_tendencia'])

    def test_sequencia_curta_no_tempo_e_sinalizada_mas_mantida(self):
        # 10 valores iguais em 9 dias: sinaliza, nao exclui
        ent = [_sessao(REF_D - timedelta(days=k), icu_pm_ftp=250.0) for k in range(10)]
        s = _ftp(_serie(ent))
        self.assertEqual(len(s['qualidade']['sequencias_identicas']), 1)
        self.assertFalse(s['qualidade']['sequencias_identicas'][0]['excluida_da_tendencia'])
        self.assertEqual(s['qualidade']['n_excluidos_tendencia'], 0)

    def test_sequencia_menor_que_limiar_nao_e_marcada(self):
        ent = [_sessao(REF_D - timedelta(days=7 * k), icu_pm_ftp=200.0) for k in range(9)]
        self.assertEqual(_ftp(_serie(ent))['qualidade']['sequencias_identicas'], [])

    def test_valores_repetidos_nao_sao_removidos_quando_nao_consecutivos(self):
        ent = []
        for k in range(20):
            ent.append(_sessao(REF_D - timedelta(days=7 * k), icu_pm_ftp=200.0 + (k % 2)))
        q = _ftp(_serie(ent))['qualidade']
        self.assertEqual(q['sequencias_identicas'], [])
        self.assertEqual(q['n_excluidos_tendencia'], 0)


class OutliersNaoRemovidos(unittest.TestCase):

    def test_extremo_e_sinalizado_mas_permanece_na_tendencia(self):
        valores = [250.0] * 4 + [251.0, 249.0, 252.0, 248.0, 250.5, 249.5] + [900.0]
        ent = [_sessao(REF_D - timedelta(days=3 * k), icu_pm_ftp=v) for k, v in enumerate(valores)]
        q = _ftp(_serie(ent))['qualidade']
        self.assertTrue(any(o['valor'] == 900.0 for o in q['outliers_sinalizados']))
        self.assertEqual(q['n_validos'], len(valores))
        self.assertEqual(q['n_excluidos_tendencia'], 0)


class MinimoPorJanela(unittest.TestCase):

    def _duas_janelas(self, n_rec, n_ant):
        # recente: (ref-90, ref] ; anterior: (ref-180, ref-90]
        # uma observacao por semana e valores distintos: nao ha sequencia identica
        ent = [_sessao(REF_D - timedelta(days=7 * k + 2), icu_pm_ftp=150.0 + 0.5 * k)
               for k in range(n_rec)]
        ent += [_sessao(REF_D - timedelta(days=92 + 7 * k), icu_pm_ftp=140.0 + 0.5 * k)
                for k in range(n_ant)]
        return _ftp(_serie(ent))['janelas']['90']

    def test_nove_observacoes_em_uma_janela_e_insuficiente(self):
        w = self._duas_janelas(n_rec=9, n_ant=20)
        self.assertEqual(w['status'], 'insuficiente')
        self.assertIn('observacoes_insuficientes', w['motivos'])
        self.assertIsNone(w['diferenca'])

    def test_dez_observacoes_nas_duas_janelas_e_suficiente(self):
        w = self._duas_janelas(n_rec=10, n_ant=10)
        self.assertEqual(w['status'], 'ok')
        # medianas de 150 + 0.5k (k<10) e 140 + 0.5k: 152.25 e 142.25
        self.assertEqual(w['mediana_recente'], 152.25)
        self.assertEqual(w['mediana_anterior'], 142.25)
        self.assertEqual(w['diferenca'], 10.0)

    def test_minimo_aplicado_a_janela_anterior_isoladamente(self):
        # recente com 13 observacoes (dentro de (ref-90, ref]); anterior com 9
        w = self._duas_janelas(n_rec=13, n_ant=9)
        self.assertEqual(w['status'], 'insuficiente')
        self.assertIn('observacoes_insuficientes', w['motivos'])


class CoberturaPorSemanas(unittest.TestCase):

    def _cobertura(self, janela, semanas_com_dado, obs_por_semana):
        W = int(janela)
        ent = []
        # valores distintos por observacao: evita a regra de sequencia identica
        n = 0
        for k in range(semanas_com_dado):
            for j in range(obs_por_semana):
                n += 1
                ent.append(_sessao(REF_D - timedelta(days=7 * k + j),
                                   icu_pm_ftp=150.0 + 0.1 * n))
        # janela anterior bem coberta (2 obs por semana, todas as semanas da janela),
        # para isolar a cobertura recente
        for k in range(round(W / 7)):
            for j in range(2):
                ent.append(_sessao(REF_D - timedelta(days=W + 1 + 7 * k + j),
                                   icu_pm_ftp=140.0 + 0.1 * (2 * k + j)))
        return _ftp(_serie(ent))['janelas'][janela]

    def test_denominadores_por_janela(self):
        for janela, semanas, exig in (('90', 13, 10), ('180', 26, 19), ('365', 52, 37)):
            w = self._cobertura(janela, semanas_com_dado=1, obs_por_semana=12)
            self.assertEqual(w['semanas_total'], semanas, janela)
            self.assertEqual(w['semanas_necessarias'], exig, janela)

    def test_90_dias_nove_semanas_insuficiente_dez_suficiente(self):
        self.assertEqual(self._cobertura('90', 9, 2)['status'], 'insuficiente')
        self.assertIn('cobertura_insuficiente', self._cobertura('90', 9, 2)['motivos'])
        self.assertEqual(self._cobertura('90', 10, 2)['status'], 'ok')

    def test_365_dias_36_semanas_insuficiente_37_suficiente(self):
        self.assertIn('cobertura_insuficiente', self._cobertura('365', 36, 2)['motivos'])
        self.assertEqual(self._cobertura('365', 37, 2)['status'], 'ok')

    def test_muitas_observacoes_em_poucas_semanas_nao_bastam(self):
        w = self._cobertura('180', semanas_com_dado=5, obs_por_semana=20)
        self.assertEqual(w['status'], 'insuficiente')
        self.assertIn('cobertura_insuficiente', w['motivos'])
        self.assertGreaterEqual(w['n_recente'], 10)


class MotivosDeInsuficiencia(unittest.TestCase):

    def test_sem_dados_quando_nao_ha_observacoes(self):
        w = _ftp(_serie([]))['janelas']['90']
        self.assertEqual(w['motivos'], ['sem_dados'])
        self.assertEqual(w['status'], 'insuficiente')

    def test_sem_tendencia_quando_insuficiente(self):
        w = _ftp(_serie([]))['janelas']['180']
        self.assertIsNone(w['diferenca'])
        self.assertIsNone(w['variacao_pct'])
        self.assertIsNone(w['mediana_recente'])


class MelhoresEsforcos(unittest.TestCase):

    def _res(self, curvas):
        return pmc.evolucao_performance([], curvas, REF, modalidades=('Bike',))

    def test_duracoes_exatas_sao_lidas_da_grade(self):
        c = _curva(REF_D - timedelta(days=3), pontos={60: 300, 300: 250, 720: 220, 1200: 200})
        res = self._res([c])['modalidades']['Bike']['melhores_esforcos']
        self.assertEqual(res['60']['qualidade']['n_validos'], 1)
        self.assertEqual(res['300']['qualidade']['n_validos'], 1)
        self.assertEqual(res['720']['qualidade']['n_validos'], 1)
        self.assertEqual(res['1200']['qualidade']['n_validos'], 1)

    def test_duracao_ausente_nao_e_interpolada(self):
        # grade tem 600 e 900, mas nao 720: o valor de 720 deve ser ausencia
        c = _curva(REF_D - timedelta(days=3), pontos={600: 230, 900: 210})
        res = self._res([c])['modalidades']['Bike']['melhores_esforcos']
        self.assertEqual(res['720']['qualidade']['n_validos'], 0)
        self.assertEqual(res['720']['qualidade']['n_ausentes'], 1)

    def test_zero_em_curva_e_ausencia(self):
        c = _curva(REF_D - timedelta(days=3), pontos={1200: 0})
        q = self._res([c])['modalidades']['Bike']['melhores_esforcos']['1200']['qualidade']
        self.assertEqual((q['n_zero'], q['n_validos']), (1, 0))

    def test_curvas_de_outra_modalidade_sao_ignoradas(self):
        c = _curva(REF_D - timedelta(days=3), tipo='Row', pontos={1200: 180})
        q = self._res([c])['modalidades']['Bike']['melhores_esforcos']['1200']['qualidade']
        self.assertEqual(q['n_brutos'], 0)


class SemProxyNemEquivalencia(unittest.TestCase):

    def test_nenhuma_serie_de_proxy_ou_estimativa_derivada(self):
        ent = [_sessao(REF_D - timedelta(days=k), icu_pm_ftp=250.0) for k in range(0, 200, 7)]
        c = [_curva(REF_D - timedelta(days=k), pontos={1200: 300}) for k in range(0, 200, 7)]
        res = pmc.evolucao_performance(ent, c, REF, modalidades=('Bike',))
        texto = repr(res).lower()
        self.assertNotIn('proxy', texto)
        self.assertNotIn('0.95', texto)
        self.assertEqual(set(res['modalidades']['Bike']['fontes']), {'eftp_atividade', 'ftp_configurado', 'eftp_nao_confirmado'})

    def test_referencia_ausente_nao_produz_tendencia(self):
        res = pmc.evolucao_performance([], [], None)
        self.assertEqual(res['status'], 'sem_referencia')
        self.assertEqual(res['modalidades'], {})


class TendenciaRunContaminada(unittest.TestCase):

    def test_run_com_valor_repetido_longo_e_sinalizado_nao_excluido(self):
        # 98 valores iguais (200 W) de 2021 a 2023 e 8 medidos recentemente
        ent = [_sessao(REF_D - timedelta(days=900 - 8 * k), tipo='Run', icu_pm_ftp=200.0)
               for k in range(98)]
        ent += [_sessao(REF_D - timedelta(days=5 + 6 * k), tipo='Run', icu_pm_ftp=310.0 + k % 3)
                for k in range(8)]
        s = pmc.evolucao_performance(ent, [], REF, modalidades=('Run',))['modalidades']['Run']['fontes']['eftp_atividade']
        self.assertEqual(s['qualidade']['n_excluidos_tendencia'], 0)
        self.assertEqual(s['qualidade']['n_sequencias_sinalizadas'], 1)
        self.assertEqual(s['qualidade']['n_registros_em_sequencia'], 98)
        self.assertEqual(s['janelas']['90']['status'], 'insuficiente')


class EftpSuspeitoEInvalido(unittest.TestCase):

    def test_eftp_um_watt_e_sinalizado_e_mantido(self):
        ent = [_sessao(REF_D - timedelta(days=7 * k), tipo='Ski', EFTP=1.0 if k == 0 else 250.0)
               for k in range(12)]
        s = pmc.evolucao_performance(ent, [], REF, modalidades=('Ski',))['modalidades']['Ski']['fontes']['eftp_nao_confirmado']
        q = s['qualidade']
        self.assertEqual(len(q['valores_suspeitos']), 1)
        self.assertEqual(q['valores_suspeitos'][0]['valor'], 1.0)
        self.assertEqual(q['n_validos'], 12)            # mantido na tendencia
        self.assertEqual(q['n_excluidos_tendencia'], 0)

    def test_eftp_negativo_e_invalido_nao_suspeito(self):
        ent = [_sessao(REF_D - timedelta(days=3), tipo='Ski', EFTP=-5.0)]
        q = pmc.evolucao_performance(ent, [], REF, modalidades=('Ski',))['modalidades']['Ski']['fontes']['eftp_nao_confirmado']['qualidade']
        self.assertEqual(q['n_negativos'], 1)
        self.assertEqual(q['valores_suspeitos'], [])

    def test_eftp_atividade_nao_recebe_marca_de_escala_do_eftp(self):
        ent = [_sessao(REF_D - timedelta(days=3), icu_pm_ftp=1.0)]
        q = _ftp(_serie(ent))['qualidade']
        self.assertEqual(q['valores_suspeitos'], [])

    def test_semantica_declarada_por_serie(self):
        f = pmc.evolucao_performance([], [], REF, modalidades=('Bike',))['modalidades']['Bike']['fontes']
        self.assertIn('Intervals.icu', f['eftp_atividade']['semantica'])
        self.assertIn('atividade completa', f['eftp_atividade']['semantica'])
        self.assertIn('recuperacao', f['eftp_atividade']['semantica'])
        self.assertIn('configuracoes', f['ftp_configurado']['semantica'])
        self.assertIn('nao confirmada', f['eftp_nao_confirmado']['semantica'])


class EftpAtividadeSemantica(unittest.TestCase):
    """icu_pm_ftp = eFTP da atividade completa (Intervals.icu). icu_ftp = FTP configurado.
    EFTP e icu_rolling_ftp: semantica nao confirmada, nunca misturados."""

    def test_icu_pm_ftp_nunca_rotulado_como_medido(self):
        s = pmc.evolucao_performance([], [], REF, modalidades=('Bike',))['modalidades']['Bike']['fontes']['eftp_atividade']
        self.assertNotIn('medido', s['rotulo'].lower())
        self.assertNotIn('medido', s['semantica'].lower())
        self.assertIn('eFTP da atividade', s['rotulo'])

    def test_nao_mistura_icu_ftp_quando_icu_pm_ausente(self):
        ent = [_sessao(REF_D - timedelta(days=3), icu_pm_ftp=None, icu_ftp=210.0)]
        f = pmc.evolucao_performance(ent, [], REF, modalidades=('Bike',))['modalidades']['Bike']['fontes']
        self.assertEqual(f['eftp_atividade']['qualidade']['n_validos'], 0)
        self.assertEqual(f['ftp_configurado']['qualidade']['n_validos'], 1)

    def test_series_nao_se_combinam_em_uma_so(self):
        ent = [_sessao(REF_D - timedelta(days=7 * k), icu_pm_ftp=200.0 if k % 2 == 0 else None,
                       icu_ftp=210.0 if k % 2 else None, id=f'a{k}') for k in range(10)]
        f = pmc.evolucao_performance(ent, [], REF, modalidades=('Bike',))['modalidades']['Bike']['fontes']
        self.assertEqual(f['eftp_atividade']['qualidade']['n_validos'], 5)
        self.assertEqual(f['ftp_configurado']['qualidade']['n_validos'], 5)

    def test_associacao_com_atividade_data_e_modalidade_preservada(self):
        ent = [_sessao(REF_D - timedelta(days=7 * k), icu_pm_ftp=250.0 + (k % 3), id=f'a{k}')
               for k in range(1, 13)]
        ent.append(_sessao(REF_D, icu_pm_ftp=900.0, id='x9'))
        s = _ftp(_serie(ent), fonte='eftp_atividade')
        q = s['qualidade']
        self.assertEqual(q['outliers_sinalizados'][0]['id'], 'x9')
        self.assertEqual(q['outliers_sinalizados'][0]['data'], _iso(REF_D))
        self.assertEqual(q['ultima_atividade_id'], 'x9')
        self.assertEqual(q['ultima_observacao'], _iso(REF_D))

    def test_ids_da_sequencia_identificam_as_atividades(self):
        ent = [_sessao(REF_D - timedelta(days=7 * k), icu_pm_ftp=200.0, id=f'a{k}') for k in range(12)]
        b = _ftp(_serie(ent), fonte='eftp_atividade')['qualidade']['sequencias_identicas'][0]
        self.assertEqual(b['ids'], [f'a{k}' for k in range(11, -1, -1)])

    def test_exemplos_do_usuario_ficam_como_observacoes_separadas(self):
        # 10 min a 227 W -> eFTP 200; intervalos com recuperacao -> eFTP 160.
        # Cada atividade vira uma observacao propria; nenhuma e combinada com outra fonte.
        ent = [_sessao(REF_D - timedelta(days=7 * k), icu_pm_ftp=200.0 if k % 2 else 160.0,
                       icu_ftp=227.0, id=f'a{k}') for k in range(52)]
        res = pmc.evolucao_performance(ent, [], REF, modalidades=('Bike',))['modalidades']['Bike']['fontes']
        self.assertEqual(res['eftp_atividade']['qualidade']['n_validos'], 52)
        self.assertEqual(res['ftp_configurado']['qualidade']['n_validos'], 52)
        self.assertEqual(res['eftp_atividade']['janelas']['180']['status'], 'ok')

    def test_sessao_isolada_baixa_nao_gera_classificacao(self):
        ent = [_sessao(REF_D - timedelta(days=7 * k), icu_pm_ftp=200.0 + (k % 3), id=f'a{k}')
               for k in range(1, 53)]
        ent.append(_sessao(REF_D - timedelta(days=1), icu_pm_ftp=160.0, id='baixa'))
        res = pmc.evolucao_performance(ent, [], REF, modalidades=('Bike',))
        textos = []
        def walk(x):
            if isinstance(x, dict):
                for v in x.values(): walk(v)
            elif isinstance(x, list):
                for v in x: walk(v)
            elif isinstance(x, str):
                textos.append(x.lower())
        walk(res)
        for palavra in ('queda', 'piora', 'melhora', 'classific', 'tendencia de'):
            self.assertFalse(any(palavra in t for t in textos), palavra)
        j = res['modalidades']['Bike']['fontes']['eftp_atividade']['janelas']['180']
        self.assertEqual(j['status'], 'ok')
        self.assertIsNotNone(j['diferenca'])     # a comparacao numerica existe; nao ha rotulo

    def test_icu_rolling_ftp_nunca_e_lido(self):
        ent = [{'id': 'r1', 'date': _iso(REF_D - timedelta(days=3)), 'type': 'Ski',
                'icu_rolling_ftp': 195.0, 'icu_pm_ftp': None, 'icu_ftp': None, 'EFTP': None}]
        res = pmc.evolucao_performance(ent, [], REF, modalidades=('Ski',))['modalidades']['Ski']['fontes']
        for chave in ('eftp_atividade', 'ftp_configurado', 'eftp_nao_confirmado'):
            self.assertEqual(res[chave]['qualidade']['n_validos'], 0, chave)
        self.assertEqual(pmc.evolucao_performance([], [], REF)['regras']['campo_nao_lido'], 'icu_rolling_ftp')

    def test_eftp_nao_confirmado_separado_do_eftp_atividade(self):
        ent = [_sessao(REF_D - timedelta(days=3), icu_pm_ftp=200.0, EFTP=255.0, id='m1')]
        f = pmc.evolucao_performance(ent, [], REF, modalidades=('Bike',))['modalidades']['Bike']['fontes']
        self.assertEqual(f['eftp_atividade']['qualidade']['n_validos'], 1)
        self.assertEqual(f['eftp_nao_confirmado']['qualidade']['n_validos'], 1)
        self.assertEqual(f['ftp_configurado']['qualidade']['n_validos'], 0)


def _textos(x):
    out = []
    if isinstance(x, dict):
        for v in x.values():
            out += _textos(v)
    elif isinstance(x, list):
        for v in x:
            out += _textos(v)
    elif isinstance(x, str):
        out.append(x.lower())
    return out


def _chaves(x):
    out = []
    if isinstance(x, dict):
        for k, v in x.items():
            out.append(str(k))
            out += _chaves(v)
    elif isinstance(x, list):
        for v in x:
            out += _chaves(v)
    return out


class IntervalosComRecuperacao(unittest.TestCase):
    """Sessao intervalada: recuperacoes a 0 W fazem parte da atividade completa.
    icu_pm_ftp e o eFTP da atividade; nao e o maximo comprovado nem base de conclusao."""

    def test_zero_w_de_recuperacao_na_curva_nao_vira_melhor_esforco(self):
        c = [_curva(REF_D - timedelta(days=2), pontos={60: 0, 300: 210, 720: 0, 1200: 160})]
        res = pmc.evolucao_performance([], c, REF, modalidades=('Bike',))['modalidades']['Bike']['melhores_esforcos']
        self.assertEqual(res['60']['qualidade']['n_zero'], 1)
        self.assertEqual(res['60']['qualidade']['n_validos'], 0)
        self.assertEqual(res['720']['qualidade']['n_zero'], 1)
        self.assertEqual(res['300']['qualidade']['n_validos'], 1)

    def test_eftp_intervalado_fica_como_eftp_da_atividade_sem_conclusao_de_perda(self):
        ent = [_sessao(REF_D - timedelta(days=7 * k), icu_pm_ftp=200.0, id=f'a{k}') for k in range(1, 53)]
        ent.append(_sessao(REF_D - timedelta(days=1), icu_pm_ftp=160.0, id='interv'))
        c = [_curva(REF_D - timedelta(days=1), pontos={60: 0, 300: 220, 720: 0, 1200: 160})]
        res = pmc.evolucao_performance(ent, c, REF, modalidades=('Bike',))['modalidades']['Bike']
        eft = res['fontes']['eftp_atividade']
        self.assertEqual(eft['qualidade']['ultima_atividade_id'], 'interv')
        self.assertEqual(eft['qualidade']['n_validos'], 53)
        self.assertEqual(eft['rotulo'], 'eFTP da atividade (icu_pm_ftp)')
        textos = _textos(res)
        for palavra in ('queda', 'piora', 'melhora', 'classific', 'perda', 'declinio'):
            self.assertFalse(any(palavra in t for t in textos), palavra)
        # nenhum campo se apresenta como maximo comprovado
        self.assertFalse(any('max' in k.lower() or 'comprovad' in k.lower() for k in _chaves(res)))

    def test_recuperacoes_nao_entram_como_eftp_zero(self):
        ent = [_sessao(REF_D - timedelta(days=3), icu_pm_ftp=0.0, id='rec')]
        q = pmc.evolucao_performance(ent, [], REF, modalidades=('Bike',))['modalidades']['Bike']['fontes']['eftp_atividade']['qualidade']
        self.assertEqual((q['n_zero'], q['n_validos']), (1, 0))


class SequenciaInconclusivaPreservada(unittest.TestCase):

    def test_sequencia_inconclusiva_sinalizada_e_valores_seguem_na_janela(self):
        ent = [_sessao(REF_D - timedelta(days=7 * k), icu_pm_ftp=200.0, id=f'a{k}') for k in range(1, 13)]
        s = _ftp(_serie(ent), fonte='eftp_atividade')
        q = s['qualidade']
        self.assertEqual(q['sequencias_identicas'][0]['classificacao'], 'suspeita')
        self.assertEqual(q['n_sequencias_sinalizadas'], 1)
        self.assertEqual(q['n_registros_em_sequencia'], 12)
        self.assertEqual(q['n_excluidos_tendencia'], 0)
        self.assertEqual(s['janelas']['90']['n_recente'], 12)


if __name__ == '__main__':
    unittest.main()
