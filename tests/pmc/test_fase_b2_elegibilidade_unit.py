"""Fase B2, passo 5 — elegibilidade para calibracao (criterio icu_ignore_power).

A deteccao nao muda: so se anota cada esforco detetado. Elegibilidade:
  icu_ignore_power True  -> 'inelegivel' (motivo icu_ignore_power), fora da serie de calibracao
  icu_ignore_power False -> 'elegivel'
  campo ausente/None/nao booleano, atividade nao encontrada ou atividades
  indisponiveis -> 'desconhecida': fica no historico e nos relatorios, mas
  NAO entra na serie de calibracao ("nao validada para este uso", nao "invalida").
"""
import copy
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import protocolo as P  # noqa: E402

HOJE = datetime(2026, 10, 16)


class _Relogio(datetime):
    @classmethod
    def now(cls, tz=None):
        return HOJE


def _data(dias_atras):
    return (HOJE - timedelta(days=dias_atras)).strftime('%Y-%m-%d')


def _curva(aid, tipo, dias_atras, w1200=None):
    return {'activity_id': aid, 'type': tipo, 'date': _data(dias_atras),
            'secs': [60, 300, 1200], 'watts': [None, None, w1200]}


def _n_curvas(tipo, n, rec=40, passo=5, w=300):
    return [_curva(f'{tipo}{i}', tipo, rec + passo * (n - 1 - i), w1200=w) for i in range(n)]


def _det(curvas):
    with mock.patch.object(P, 'datetime', _Relogio):
        return P.detectar_testes(curvas)


def _atividades(**por_id):
    """_atividades(Bike3=True, Bike4=False, Bike5=None, Bike6='ausente')"""
    out = []
    for k, v in por_id.items():
        a = {'id': k, 'name': 'x'}
        if v != 'ausente':
            a['icu_ignore_power'] = v
        out.append(a)
    return out


class MapaIgnorePower(unittest.TestCase):
    def test_estados_do_campo(self):
        m = P.mapa_ignore_power(_atividades(a=True, b=False, c=None, d='ausente') +
                                [{'id': 'e', 'icu_ignore_power': 'true'}, {'id': 'f', 'icu_ignore_power': 1}])
        self.assertEqual(m, {'a': True, 'b': False, 'c': None, 'd': None, 'e': None, 'f': None})

    def test_atividades_indisponiveis_devolve_none_e_lista_vazia_devolve_mapa_vazio(self):
        self.assertIsNone(P.mapa_ignore_power(None))
        self.assertEqual(P.mapa_ignore_power([]), {})

    def test_ignora_entradas_invalidas_e_true_prevalece_em_ids_repetidos(self):
        m = P.mapa_ignore_power([None, 'x', {'name': 'sem id'},
                                 {'id': 'a', 'icu_ignore_power': True},
                                 {'id': 'a', 'icu_ignore_power': False},
                                 {'id': 'b', 'icu_ignore_power': False},
                                 {'id': 'b', 'icu_ignore_power': True}])
        self.assertEqual(m, {'a': True, 'b': True})


class AnotarElegibilidade(unittest.TestCase):
    def setUp(self):
        self.det = _det(_n_curvas('Bike', 10))             # testes Bike2..Bike9 (8)

    def _estados(self, mapa):
        a = P.anotar_elegibilidade(self.det, mapa)
        return {t['activity_id']: (t['elegibilidade_calibracao'], t['motivo_elegibilidade'])
                for t in a['Bike'][1200]['testes']}

    def test_cinco_casos(self):
        mapa = P.mapa_ignore_power(_atividades(Bike2=True, Bike3=False, Bike4=None, Bike5='ausente'))
        e = self._estados(mapa)
        self.assertEqual(e['Bike2'], ('inelegivel', 'icu_ignore_power'))
        self.assertEqual(e['Bike3'], ('elegivel', None))
        self.assertEqual(e['Bike4'], ('desconhecida', 'campo_ausente'))
        self.assertEqual(e['Bike5'], ('desconhecida', 'campo_ausente'))
        self.assertEqual(e['Bike6'], ('desconhecida', 'atividade_nao_encontrada'))

    def test_atividade_indisponivel(self):
        e = self._estados(None)
        self.assertEqual({v for v in e.values()}, {('desconhecida', 'atividades_indisponiveis')})

    def test_contagens_somam_n_testes(self):
        mapa = P.mapa_ignore_power(_atividades(Bike2=True, Bike3=True, Bike4=False, Bike5=False, Bike6=False))
        d = P.anotar_elegibilidade(self.det, mapa)['Bike'][1200]
        self.assertEqual((d['n_elegiveis_calibracao'], d['n_inelegiveis_calibracao'],
                          d['n_desconhecidos_calibracao']), (3, 2, 3))
        self.assertEqual(d['n_testes'], 8)

    def test_nao_exclui_nem_altera_a_deteccao(self):
        antes = copy.deepcopy(self.det)
        a = P.anotar_elegibilidade(self.det, P.mapa_ignore_power(_atividades(Bike2=True, Bike9=True)))
        self.assertEqual(self.det, antes)                     # objeto recebido intacto
        for chave in ('nome', 'mede', 'n_testes', 'dias_desde_ultimo', 'intervalo_recomendado', 'em_atraso'):
            self.assertEqual(a['Bike'][1200][chave], antes['Bike'][1200][chave], chave)
        sem = lambda ts: [{k: v for k, v in t.items()
                           if k not in ('elegibilidade_calibracao', 'motivo_elegibilidade')} for t in ts]
        self.assertEqual(sem(a['Bike'][1200]['testes']), antes['Bike'][1200]['testes'])
        self.assertEqual(sem([a['Bike'][1200]['ultimo']])[0], antes['Bike'][1200]['ultimo'])
        self.assertEqual(sem(a['Bike'][1200]['recentes']), antes['Bike'][1200]['recentes'])

    def test_ultimo_e_recentes_anotados_de_forma_consistente(self):
        a = P.anotar_elegibilidade(self.det, P.mapa_ignore_power(_atividades(Bike9=True)))['Bike'][1200]
        self.assertEqual(a['ultimo']['elegibilidade_calibracao'], 'inelegivel')
        self.assertEqual(a['recentes'][-1]['elegibilidade_calibracao'], 'inelegivel')
        self.assertEqual(a['testes'][-1]['elegibilidade_calibracao'], 'inelegivel')

    def test_sugestoes_cobertura_robustez_inalteradas(self):
        # estas funcoes recebem a deteccao original; a anotacao nao as afeta
        com = P.anotar_elegibilidade(self.det, P.mapa_ignore_power(_atividades(Bike9=True, Bike2=True)))
        with mock.patch.object(P, 'datetime', _Relogio):
            self.assertEqual(P.sugerir(com), P.sugerir(self.det))
            self.assertEqual(P.cobertura(com), P.cobertura(self.det))
            self.assertEqual(P.robustez(com), P.robustez(self.det))


class SerieAncoraEligivel(unittest.TestCase):
    def setUp(self):
        det = _det(_n_curvas('Bike', 10))
        mapa = P.mapa_ignore_power(_atividades(Bike2=True, Bike3=False, Bike4=None))
        self.det = P.anotar_elegibilidade(det, mapa)

    def test_por_omissao_inclui_tudo(self):
        self.assertEqual(len(P.serie_ancora(self.det, 'Bike', 1200)), 8)

    def test_somente_elegiveis_so_deixa_entrar_false_explicito(self):
        ids = [x['activity_id'] for x in P.serie_ancora(self.det, 'Bike', 1200, somente_elegiveis=True)]
        self.assertEqual(ids, ['Bike3'])          # Bike2 true, Bike4 None, Bike5.. nao encontradas

    def test_sem_anotacao_nao_e_elegivel(self):
        det = _det(_n_curvas('Bike', 10))
        self.assertEqual(len(P.serie_ancora(det, 'Bike', 1200)), 8)
        self.assertEqual(P.serie_ancora(det, 'Bike', 1200, somente_elegiveis=True), [])


class CalibracaoSoComElegiveis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import pmc
                import calibracao
                import db
        except BaseException as e:
            raise unittest.SkipTest(f'pmc nao importa: {type(e).__name__}: {e}')
        cls.pmc, cls.cal, cls.db = pmc, calibracao, db

    @staticmethod
    def _serie(n=400):
        ini = datetime(2025, 1, 1)
        return [{'date': (ini + timedelta(days=i)).strftime('%Y-%m-%d'), 'load': 50.0 + (i % 7)}
                for i in range(n)]

    @staticmethod
    def _curvas(tipo, n, ini=datetime(2025, 1, 1)):
        return [{'activity_id': f'{tipo}{i}', 'type': tipo,
                 'date': (ini + timedelta(days=5 + 10 * i)).strftime('%Y-%m-%d'),
                 'secs': [60, 300, 1200], 'watts': [None, None, 300.0]} for i in range(n)]

    def _correr(self, tipo, n, atividades, resposta=None):
        resposta = resposta or {'fonte': 'referencia', 'valor': 17, 'motivo': 'x', 'testados': []}
        alvos = []

        def lag(x, y, *a, **k):
            alvos.append(np.asarray(y, dtype=float).copy())
            return resposta
        with mock.patch.object(self.db, 'load_power_curves',
                               side_effect=lambda t=None, desde=None: self._curvas(tipo, n) if t == tipo else []), \
                mock.patch.object(self.cal, 'calibrar_lag', side_effect=lag):
            r = self.pmc.calibrar_com_ancora(self._serie(), [tipo], atividades=atividades)
        return r, r['por_modalidade'][tipo], (alvos[0] if alvos else None)

    @staticmethod
    def _tudo_false(tipo, n, **excepcoes):
        return [{'id': f'{tipo}{i}', 'icu_ignore_power': excepcoes.get(f'{tipo}{i}', False)}
                for i in range(n)]

    # ---- true excluido, false incluido -------------------------------
    def test_true_e_excluido_false_e_incluido(self):
        # 32 curvas -> 30 testes (Bike2..Bike31); Bike10 e Bike11 com icu_ignore_power=true
        atv = self._tudo_false('Bike', 32, Bike10=True, Bike11=True)
        r, e, alvo = self._correr('Bike', 32, atv)
        self.assertEqual((e['n_testes_detetados'], e['n_testes_elegiveis'], e['n_testes_inelegiveis'],
                          e['n_testes_desconhecidos']), (30, 28, 2, 0))
        self.assertEqual(e['n_testes'], 28)
        self.assertEqual(int(np.isfinite(alvo).sum()), 28)
        for i in (10, 11):
            self.assertTrue(np.isnan(alvo[5 + 10 * i]), i)       # excluidos: sem alvo
        self.assertTrue(np.isfinite(alvo[5 + 10 * 12]))          # false explicito: incluido
        self.assertIn('icu_ignore_power', e['efeito_elegibilidade'])
        self.assertIs(e['elegibilidade_validada'], True)
        self.assertEqual(r['elegibilidade'], {'criterio': 'icu_ignore_power', 'inelegiveis_excluidos': 2,
                                              'desconhecidos_excluidos': 0, 'validada_em_todas': True})

    def test_so_false_explicito_resultado_igual_ao_de_antes(self):
        r, e, alvo = self._correr('Bike', 32, self._tudo_false('Bike', 32))
        self.assertEqual((e['n_testes'], e['n_testes_detetados'], e['n_testes_elegiveis']), (30, 30, 30))
        self.assertEqual((e['n_testes_inelegiveis'], e['n_testes_desconhecidos']), (0, 0))
        self.assertIsNone(e['efeito_elegibilidade'])
        self.assertEqual(int(np.isfinite(alvo).sum()), 30)
        self.assertNotIn('desconhecida', r['leitura'])

    # ---- desconhecidos excluidos (campo ausente, None, nao encontrada, sem atividades)
    def test_desconhecidos_de_todos_os_tipos_sao_excluidos_e_contados(self):
        # 40 curvas -> 38 testes (Bike2..Bike39): false 2..29 (28), ausente 30-32, None 33-35, nao listadas 36-39
        atv = ([{'id': f'Bike{i}', 'icu_ignore_power': False} for i in range(0, 30)]
               + [{'id': f'Bike{i}'} for i in range(30, 33)]
               + [{'id': f'Bike{i}', 'icu_ignore_power': None} for i in range(33, 36)])
        r, e, alvo = self._correr('Bike', 40, atv)
        self.assertEqual((e['n_testes_detetados'], e['n_testes_elegiveis'], e['n_testes_inelegiveis'],
                          e['n_testes_desconhecidos']), (38, 28, 0, 10))
        self.assertEqual(e['n_testes'], 28)
        self.assertEqual(int(np.isfinite(alvo).sum()), 28)
        for i in list(range(30, 40)):
            self.assertTrue(np.isnan(alvo[5 + 10 * i]), i)       # nenhum desconhecido entra no alvo
        self.assertIs(e['elegibilidade_validada'], False)
        self.assertIn('nao validada para este uso', e['efeito_elegibilidade'])
        self.assertIn('excluidos da calibracao', e['efeito_elegibilidade'])
        self.assertEqual(r['elegibilidade']['desconhecidos_excluidos'], 10)
        self.assertIs(r['elegibilidade']['validada_em_todas'], False)
        self.assertIn('Elegibilidade desconhecida em 10 esforco(s): excluidos da calibracao '
                      '(nao validados para este uso).', r['leitura'])
        self.assertNotIn('invalid', r['leitura'].lower())        # nao e dito que sejam fisiologicamente invalidos

    def test_atividades_indisponiveis_nenhuma_observacao_entra(self):
        r, e, alvo = self._correr('Bike', 32, None)
        self.assertIsNone(alvo)                                  # calibrar_lag nao foi chamado
        self.assertEqual((e['n_testes'], e['n_testes_detetados'], e['n_testes_desconhecidos'],
                          e['n_testes_elegiveis']), (0, 30, 30, 0))
        self.assertEqual(r['elegibilidade']['desconhecidos_excluidos'], 30)

    def test_campo_ausente_none_e_atividade_inexistente(self):
        for nome, atv in (
                ('ausente', [{'id': f'Bike{i}'} for i in range(32)]),
                ('none', [{'id': f'Bike{i}', 'icu_ignore_power': None} for i in range(32)]),
                ('inexistente', [{'id': 'outra', 'icu_ignore_power': False}]),
                ('lista vazia', [])):
            _, e, alvo = self._correr('Bike', 32, atv)
            self.assertIsNone(alvo, nome)
            self.assertEqual((e['n_testes'], e['n_testes_desconhecidos'], e['n_testes_detetados']),
                             (0, 30, 30), nome)
            self.assertEqual(e['fonte'], 'insuficiente', nome)

    # ---- amostra insuficiente: nunca simula calibracao ----------------
    def test_amostra_insuficiente_apos_exclusao_nao_apresenta_lag_de_referencia_como_calibrado(self):
        # mesmo que calibrar_lag devolvesse algo, nem e chamado
        r, e, alvo = self._correr('Bike', 32, None, resposta={'fonte': 'dados', 'valor': 14, 'r': 0.5,
                                                              'r2': 0.25, 'p_permutacao': 0.001})
        self.assertIsNone(alvo)
        self.assertEqual((e['fonte'], e['calibrado'], e['lag_referencia']), ('insuficiente', False, None))
        self.assertNotIn('lag', e)
        self.assertNotIn('p_permutacao', e)
        self.assertIn('so 0 testes elegiveis (precisa de 25)', e['motivo'])
        self.assertIn('30 com elegibilidade desconhecida', e['motivo'])
        self.assertEqual((r['sobrevivem'], r['calibradas'], r['em_referencia']), ([], [], []))

    def test_exclusao_de_inelegiveis_pode_deixar_abaixo_do_minimo(self):
        # 27 curvas -> 25 testes (no minimo); 1 true -> 24 < 25
        _, e, alvo = self._correr('Bike', 27, self._tudo_false('Bike', 27, Bike10=True))
        self.assertIsNone(alvo)
        self.assertEqual((e['fonte'], e['calibrado'], e['n_testes']), ('insuficiente', False, 24))
        self.assertEqual((e['n_testes_detetados'], e['n_testes_inelegiveis']), (25, 1))
        self.assertIn('precisa de 25', e['motivo'])
        self.assertIn('icu_ignore_power', e['motivo'])

    # ---- distincao fonte / calibrado / sobrevive com observacoes elegiveis
    def test_fonte_calibrado_sobrevive_continuam_distintos(self):
        atv = self._tudo_false('Bike', 32)
        dados = lambda p: {'fonte': 'dados', 'valor': 14, 'r': 0.42, 'r2': 0.1764, 'p_permutacao': p,
                           'forca': 'moderada', 'distribuicao_nula': {}, 'testados': []}
        ref = {'fonte': 'referencia', 'valor': 17, 'motivo': 'nenhum lag deu correlacao com p < 0.1',
               'testados': []}
        a = self._correr('Bike', 32, atv, dados(0.298))[1]
        b = self._correr('Bike', 32, atv, dados(0.01))[1]
        c = self._correr('Bike', 32, atv, ref)[1]
        self.assertEqual([(x['fonte'], x['calibrado'], x['sobrevive']) for x in (a, b, c)],
                         [('dados', True, False), ('dados', True, True), ('referencia', False, False)])
        self.assertEqual((c['lag'], c['lag_referencia']), (17, 17))     # contrato de lag preservado

    def test_contratos_de_ancora_testes(self):
        atv = self._tudo_false('Bike', 32)
        elegib = {'n_testes_detetados', 'n_testes_elegiveis', 'n_testes_inelegiveis',
                  'n_testes_desconhecidos', 'elegibilidade_validada'}
        r, e, _ = self._correr('Bike', 32, atv)
        self.assertEqual(set(r), {'duracao', 'por_modalidade', 'sobrevivem', 'calibradas', 'em_referencia',
                                  'leitura', 'elegibilidade'})
        self.assertEqual(set(e), elegib | {'n_testes', 'efeito_elegibilidade', 'de', 'ate', 'fonte', 'calibrado',
                                           'motivo', 'lag_referencia', 'lag', 'r', 'r2', 'forca',
                                           'p_permutacao', 'distribuicao_nula', 'sobrevive'})
        _, ins, _ = self._correr('Bike', 32, None)
        self.assertEqual(set(ins), elegib | {'n_testes', 'fonte', 'calibrado', 'lag_referencia', 'motivo'})

    def test_calibracao_nao_muda_o_algoritmo(self):
        antes = dict(self.cal.REFERENCIA)
        with mock.patch.object(self.db, 'load_power_curves',
                               side_effect=lambda t=None, desde=None: self._curvas('Bike', 32)), \
                mock.patch.object(self.cal, 'calibrar_lag',
                                  return_value={'fonte': 'referencia', 'valor': 17, 'motivo': 'x',
                                                'testados': []}) as lag:
            self.pmc.calibrar_com_ancora(self._serie(), ['Bike'], atividades=self._tudo_false('Bike', 32, Bike10=True))
        args = lag.call_args[0]
        self.assertEqual((list(args[2]), args[3], args[4]), (list(range(5, 29)), None, 'lag_super'))
        self.assertEqual(self.cal.REFERENCIA, antes)

    def test_a_deteccao_nao_depende_das_atividades(self):
        curvas = self._curvas('Bike', 32)
        base = _det(curvas)
        with mock.patch.object(self.db, 'load_power_curves', side_effect=lambda t=None, desde=None: curvas):
            for atv in (None, [], self._tudo_false('Bike', 32, Bike10=True)):
                self.pmc.calibrar_com_ancora(self._serie(), ['Bike'], atividades=atv)
                self.assertEqual(_det(curvas), base)

    def test_tab_pmc_passa_as_atividades(self):
        src = open(os.path.join(ROOT, 'tabs', 'tab_pmc.py'), encoding='utf-8').read()
        self.assertIn('pmc.calibrar_com_ancora(serie, CICLICOS, atividades=acts)', src)


class SuficienciaComElegibilidade(unittest.TestCase):
    def test_so_elegiveis_contam_para_o_minimo_mas_o_estado_e_da_deteccao(self):
        curvas = _n_curvas('Bike', 27)                                      # 25 testes
        atv = [{'id': f'Bike{i}', 'icu_ignore_power': i == 10} for i in range(27)]
        det = P.anotar_elegibilidade(_det(curvas), P.mapa_ignore_power(atv))
        e = P.suficiencia_por_modalidade(det, curvas)['Bike']
        self.assertEqual((e['n_candidatos'], e['n_utilizaveis_calibracao'], e['n_inelegiveis_calibracao'],
                          e['n_desconhecidos_calibracao']), (25, 24, 1, 0))
        self.assertIs(e['atinge_minimo_calibracao'], False)
        self.assertEqual(e['estado'], 'parcial')
        self.assertIn('1 excluido(s) da calibracao por icu_ignore_power', e['texto'])

    def test_desconhecidos_nao_contam_e_sao_assinalados(self):
        curvas = _n_curvas('Bike', 27)
        det = P.anotar_elegibilidade(_det(curvas), None)
        e = P.suficiencia_por_modalidade(det, curvas)['Bike']
        self.assertEqual((e['n_candidatos'], e['n_desconhecidos_calibracao'], e['n_utilizaveis_calibracao']),
                         (25, 25, 0))
        self.assertIs(e['atinge_minimo_calibracao'], False)
        self.assertEqual(e['estado'], 'parcial')                            # a deteccao nao mudou
        self.assertIn('25 com elegibilidade desconhecida (nao validada para este uso): '
                      'excluidos da calibracao', e['texto'])

    def test_elegiveis_explicitos_atingem_o_minimo(self):
        curvas = _n_curvas('Bike', 27)
        det = P.anotar_elegibilidade(
            _det(curvas), P.mapa_ignore_power([{'id': f'Bike{i}', 'icu_ignore_power': False} for i in range(27)]))
        e = P.suficiencia_por_modalidade(det, curvas)['Bike']
        self.assertEqual((e['n_utilizaveis_calibracao'], e['atinge_minimo_calibracao']), (25, True))

    def test_sem_anotacao_a_elegibilidade_nao_foi_avaliada(self):
        curvas = _n_curvas('Bike', 27)
        e = P.suficiencia_por_modalidade(_det(curvas), curvas)['Bike']
        self.assertEqual((e['n_utilizaveis_calibracao'], e['n_inelegiveis_calibracao'],
                          e['n_desconhecidos_calibracao']), (25, 0, None))
        self.assertIs(e['atinge_minimo_calibracao'], True)


class RotaProtocoloElegibilidade(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import app as appmod
        except BaseException as e:
            raise unittest.SkipTest(f'app nao importa: {type(e).__name__}: {e}')
        cls.app = appmod

    def setUp(self):
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})

    def _get(self, curvas, atividades, url='/api/protocolo'):
        efeito = atividades if isinstance(atividades, Exception) else None
        with mock.patch.object(self.app.db, 'load_power_curves', return_value=curvas), \
                mock.patch.object(self.app.db, 'load_activities',
                                  side_effect=efeito, return_value=None if efeito else atividades) as la, \
                mock.patch.object(P, 'datetime', _Relogio):
            r = self.app.app.test_client().get(url)
        return r, la

    def test_contagens_atrasos_e_sugestoes_iguais_com_e_sem_inelegiveis(self):
        curvas = _n_curvas('Bike', 32) + _n_curvas('Row', 10, rec=90)
        base, _ = self._get(curvas, None)
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})
        com, _ = self._get(curvas, _atividades(Bike10=True, Bike11=True, Row5=True, Bike12=False, Bike13=None))
        jb, jc = base.get_json(), com.get_json()
        self.assertEqual(jc['sugestoes'], jb['sugestoes'])
        self.assertEqual(jc['robustez'], jb['robustez'])
        sem_suf = lambda c: {k: v for k, v in c.items() if k != 'suficiencia'}
        self.assertEqual(sem_suf(jc['cobertura']), sem_suf(jb['cobertura']))
        for m in jb['detectados']:
            for s, d in jb['detectados'][m].items():
                n = jc['detectados'][m][s]
                for k in ('n_testes', 'dias_desde_ultimo', 'em_atraso', 'intervalo_recomendado'):
                    self.assertEqual(n[k], d[k], (m, s, k))
        self.assertEqual(set(jc), set(jb))

    def test_estados_de_elegibilidade_na_resposta(self):
        curvas = _n_curvas('Bike', 32)
        r, _ = self._get(curvas, _atividades(Bike10=True, Bike11=True, Bike12=False))
        d = r.get_json()['detectados']['Bike']['1200']
        self.assertEqual((d['n_testes'], d['n_inelegiveis_calibracao'], d['n_elegiveis_calibracao'],
                          d['n_desconhecidos_calibracao']), (30, 2, 1, 27))
        self.assertEqual(d['ultimo']['elegibilidade_calibracao'], 'desconhecida')
        self.assertEqual(d['ultimo']['motivo_elegibilidade'], 'atividade_nao_encontrada')
        suf = r.get_json()['cobertura']['suficiencia']['Bike']
        self.assertEqual((suf['n_candidatos'], suf['n_utilizaveis_calibracao']), (30, 1))

    def test_falha_a_carregar_atividades_nao_derruba_e_fica_desconhecido(self):
        r, _ = self._get(_n_curvas('Bike', 32, rec=40), RuntimeError('sem bd'))
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        d = j['detectados']['Bike']['1200']
        self.assertEqual((d['n_testes'], d['n_desconhecidos_calibracao']), (30, 30))
        self.assertEqual(d['ultimo']['motivo_elegibilidade'], 'atividades_indisponiveis')
        self.assertEqual(j['sugestoes'][0]['secs'], 1200)

    def test_atividades_none_tambem_e_desconhecido(self):
        r, _ = self._get(_n_curvas('Bike', 32), None)
        d = r.get_json()['detectados']['Bike']['1200']
        self.assertEqual(d['n_desconhecidos_calibracao'], 30)

    def test_cache_evita_reler_e_falhas_nao_ficam_em_cache(self):
        curvas = _n_curvas('Bike', 32)
        _, la = self._get(curvas, _atividades(Bike10=True))
        self.assertEqual(la.call_count, 1)
        _, la2 = self._get(curvas, _atividades(Bike10=True))
        self.assertEqual(la2.call_count, 0)                      # servido da cache
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})
        r, _ = self._get(curvas, RuntimeError('x'))
        self.assertEqual(self.app._cache_ignore_power['t'], None)  # falha nao fica em cache
        _, la3 = self._get(curvas, _atividades(Bike10=True))
        self.assertEqual(la3.call_count, 1)

    def test_resposta_serializa_em_json(self):
        r, _ = self._get(_n_curvas('Bike', 32), _atividades(Bike10=True))
        json.dumps(r.get_json())


if __name__ == '__main__':
    unittest.main()
