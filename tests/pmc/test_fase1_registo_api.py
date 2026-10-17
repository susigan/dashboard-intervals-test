"""Fase 1 — registo de testes de performance: rotas, persistencia e contrato.

    python -m pytest tests/pmc/test_fase1_registo_api.py

Offline e deterministico: base SQLite temporaria, curvas e atividades simuladas,
nenhuma chamada a Intervals.icu/Railway. Confirmar um teste so grava o registo.
"""
import copy
import json
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
HERE = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fase1_util as U  # noqa: E402

BIKE = U.curvas('Bike', 12)
ROW = U.curvas('Row', 8)
CURVAS = BIKE + ROW


def _por_tipo(tipo=None, desde=None):
    return [c for c in CURVAS if not tipo or c['type'] == tipo]


class Base(unittest.TestCase):
    ATIV = None          # lista de atividades devolvida por load_activities

    @classmethod
    def setUpClass(cls):
        try:
            cls.app = U.importar_app()
        except BaseException as e:
            raise unittest.SkipTest(f'app nao importa: {type(e).__name__}: {e}')
        cls.db = cls.app.db
        import modelo_testes
        cls.mt = modelo_testes

    def setUp(self):
        self.ativ = U.todas(CURVAS, False) if self.ATIV is None else self.ATIV
        self._ctx = U.base_temporaria(self.db)
        self.caminho = self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})
        self.addCleanup(self.app._cache_ignore_power.update, {'t': None, 'mapa': None})
        self.c = self.app.app.test_client()
        for alvo, kw in ((self.db, dict(attribute='load_power_curves', side_effect=_por_tipo)),
                         (self.db, dict(attribute='load_activities', side_effect=lambda **k: self.ativ))):
            m = mock.patch.object(alvo, kw.pop('attribute'), **kw)
            m.start()
            self.addCleanup(m.stop)

    # ---- helpers -------------------------------------------------------
    def corpo(self, mod='Bike', dur=1200, aid='Bike11', dec='confirmar', **extra):
        d = {'modalidade': mod, 'duracao_s': dur, 'activity_id': aid, 'decisao': dec,
             'motivo': 'teste padronizado, aquecimento feito', 'confirmacao_explicita': True,
             'origem_registo': 'api'}
        d.update(extra)
        return d

    def post(self, **kw):
        return self.c.post('/api/protocolo/testes/decisao', json=self.corpo(**kw))

    def n(self):
        return U.contar(self.db)

    def cand(self, **q):
        url = '/api/protocolo/candidatos' + ('?' + '&'.join(f'{k}={v}' for k, v in q.items()) if q else '')
        return self.c.get(url).get_json()


class Candidatos(Base):
    def test_detetado_nao_e_confirmado_automaticamente(self):
        j = self.cand()
        self.assertEqual(j['status'], 'OK')
        self.assertGreater(j['n'], 0)
        self.assertTrue(all(c['estado_registo'] == 'pendente' for c in j['candidatos']))
        # ler candidatos, testes e historico nao grava nada
        self.c.get('/api/protocolo/testes')
        self.c.get('/api/protocolo/testes/historico')
        self.c.get('/api/protocolo')
        self.assertEqual(self.n(), 0)
        self.assertEqual(j['resumo_registo'], {})

    def test_modalidade_e_duracao_explicitas_nos_candidatos(self):
        j = self.cand()
        self.assertEqual({c['modalidade'] for c in j['candidatos']}, {'Bike', 'Row'})
        self.assertEqual({c['duracao_s'] for c in j['candidatos']}, {60, 300, 1200})
        c = next(c for c in j['candidatos'] if c['test_id'] == 'Bike:1200:Bike11')
        self.assertEqual((c['modalidade'], c['duracao_s'], c['activity_id'], c['watts']),
                         ('Bike', 1200, 'Bike11', 311.0))

    def test_sem_candidatos(self):
        with mock.patch.object(self.db, 'load_power_curves', return_value=[]):
            j = self.cand()
        self.assertEqual((j['status'], j['n'], j['candidatos']), ('OK', 0, []))
        self.assertEqual(self.c.get('/api/protocolo/testes').get_json()['testes'], [])
        self.assertEqual(self.c.get('/api/protocolo/testes/historico').get_json()['revisoes'], [])

    def test_filtros_invalidos_dao_400(self):
        for q in ('tipo=Foo', 'duracao=999', 'duracao=abc', 'estado=outro'):
            r = self.c.get('/api/protocolo/candidatos?' + q)
            self.assertEqual(r.status_code, 400, q)
            self.assertEqual(r.get_json()['status'], 'erro')
        # 'pendente' nao existe no registo gravado
        self.assertEqual(self.c.get('/api/protocolo/testes?estado=pendente').status_code, 400)


class Decisao(Base):
    def test_confirmacao_manual_explicita(self):
        r = self.post()
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        t = j['teste']
        self.assertTrue(j['alterado'])
        self.assertEqual((t['estado'], t['evento'], t['revisao'], t['estado_anterior']),
                         ('confirmado', 'confirmacao', 1, None))
        self.assertEqual((t['modalidade'], t['duracao_s'], t['activity_id'], t['watts'], t['data']),
                         ('Bike', 1200, 'Bike11', 311.0, '2026-03-23'))
        self.assertEqual((t['metodo_confirmacao'], t['origem_registo'], t['elegibilidade']),
                         ('manual', 'api', 'elegivel'))
        self.assertEqual(t['hora_local'], '2026-03-01T07:30:00')
        self.assertIs(t['icu_ignore_power'], False)
        self.assertEqual(t['deteccao'], {'limiar_esforco': 0.92, 'janela_dias': 180})
        self.assertTrue(t['registado_em'] and t['revisado_em'] and t['confirmado_em'])
        self.assertIn('Nao calibra', j['aviso'])
        self.assertEqual(self.n(), 1)

    def test_sem_confirmacao_explicita_nao_regista(self):
        for extra in ({'confirmacao_explicita': False}, {'confirmacao_explicita': 'true'},
                      {'confirmacao_explicita': None}):
            self.assertEqual(self.post(**extra).status_code, 400, extra)
        corpo = self.corpo()
        del corpo['confirmacao_explicita']
        self.assertEqual(self.c.post('/api/protocolo/testes/decisao', json=corpo).status_code, 400)
        self.assertEqual(self.n(), 0)

    def test_corpo_e_content_type(self):
        r = self.c.post('/api/protocolo/testes/decisao', data=json.dumps(self.corpo()),
                        content_type='text/plain')
        self.assertEqual(r.status_code, 415)
        self.assertEqual(self.c.post('/api/protocolo/testes/decisao', json=[1]).status_code, 400)
        self.assertEqual(self.n(), 0)

    def test_campos_invalidos(self):
        for extra in ({'modalidade': 'Swim'}, {'duracao_s': 600}, {'duracao_s': True},
                      {'duracao_s': '1200'}, {'decisao': 'talvez'}, {'motivo': ''},
                      {'motivo': '   '}, {'motivo': None}, {'motivo': 'x' * 501},
                      {'metodo_confirmacao': 'outro'}, {'origem_registo': 'x'},
                      {'aceita_elegibilidade_desconhecida': 'sim'}, {'responsavel': 5},
                      {'activity_id': ''}):
            r = self.post(**extra)
            self.assertEqual(r.status_code, 400, extra)
        self.assertEqual(self.n(), 0)

    def test_protocolo_verificavel_nao_existe(self):
        r = self.post(metodo_confirmacao='protocolo')
        self.assertEqual(r.status_code, 400)
        self.assertIn('protocolo', r.get_json()['mensagem'])
        self.assertEqual(self.n(), 0)

    def test_rejeicao_exige_motivo_e_fica_registada(self):
        self.assertEqual(self.post(dec='rejeitar', motivo='').status_code, 400)
        r = self.post(dec='rejeitar', motivo='competicao, nao e teste maximo padronizado')
        t = r.get_json()['teste']
        self.assertEqual((t['estado'], t['evento'], t['confirmado_em']), ('rejeitado', 'rejeicao', None))
        self.assertEqual(t['motivo'], 'competicao, nao e teste maximo padronizado')

    def test_candidato_inexistente_404(self):
        for aid in ('naoexiste', 'Bike0'):                  # Bike0: sem janela => nao detetado
            r = self.post(aid=aid)
            self.assertEqual(r.status_code, 404, aid)
        r = self.post(mod='Ski', aid='Ski3')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.n(), 0)

    def test_duplicacao_do_mesmo_teste_e_idempotente(self):
        a = self.post().get_json()
        b = self.post().get_json()
        self.assertTrue(a['alterado'])
        self.assertFalse(b['alterado'])
        self.assertEqual(b['teste']['revisao'], 1)
        self.assertEqual(self.n(), 1)
        self.post(dec='rejeitar')
        c = self.post(dec='rejeitar').get_json()
        self.assertFalse(c['alterado'])
        self.assertEqual(self.n(), 2)

    def test_id_distingue_modalidade_duracao_e_origem(self):
        self.post(mod='Bike', dur=1200, aid='Bike11')
        self.post(mod='Bike', dur=300, aid='Bike11')
        self.post(mod='Bike', dur=1200, aid='Bike10')
        self.post(mod='Row', dur=1200, aid='Row7')
        ids = {r['test_id'] for r in self.db.testes_modelo_atuais()}
        self.assertEqual(ids, {'Bike:1200:Bike11', 'Bike:300:Bike11', 'Bike:1200:Bike10', 'Row:1200:Row7'})

    def test_correcao_e_invalidacao_preservam_o_historico(self):
        self.post()
        rev1 = copy.deepcopy(self.db.ultima_revisao_teste('Bike:1200:Bike11'))
        self.post(dec='rejeitar', motivo='aquecimento incompleto, invalidado')
        self.post(dec='confirmar', motivo='reavaliado: repetido com aquecimento correto')
        hist = self.c.get('/api/protocolo/testes/historico?test_id=Bike:1200:Bike11').get_json()['revisoes']
        self.assertEqual([(r['revisao'], r['evento'], r['estado'], r['estado_anterior']) for r in hist],
                         [(1, 'confirmacao', 'confirmado', None),
                          (2, 'invalidacao', 'rejeitado', 'confirmado'),
                          (3, 'reconfirmacao', 'confirmado', 'rejeitado')])
        self.assertEqual(hist[0], rev1)                       # a revisao 1 nunca muda
        self.assertEqual({r['registado_em'] for r in hist}, {rev1['registado_em']})
        self.assertEqual(len({r['motivo'] for r in hist}), 3)
        atual = self.c.get('/api/protocolo/testes').get_json()['testes']
        self.assertEqual([(t['revisao'], t['estado']) for t in atual], [(3, 'confirmado')])

    def test_conflito_de_revisao_nao_sobrescreve(self):
        self.post()
        rev = copy.deepcopy(self.db.ultima_revisao_teste('Bike:1200:Bike11'))
        rev['motivo'] = 'tentativa de sobrescrever'
        with self.assertRaises(self.db.ConflitoRevisao):
            self.db.inserir_revisao_teste(rev)
        self.assertEqual(self.db.ultima_revisao_teste('Bike:1200:Bike11')['motivo'],
                         'teste padronizado, aquecimento feito')
        self.assertEqual(self.n(), 1)

    def test_sem_base_de_dados(self):
        with mock.patch.object(self.db, 'ENABLED', False):
            r = self.post()
            self.assertEqual(r.status_code, 503)
            j = self.cand()
        self.assertFalse(j['persistencia'])
        self.assertEqual(self.n(), 0)


class Elegibilidade(Base):
    ATIV = U.atividades(Bike11=False, Bike10=True, Bike9='ausente', Bike8=None, Bike6=False)
    # Bike7 nao esta nas atividades: atividade nao encontrada

    def elig(self, aid):
        c = next(c for c in self.cand()['candidatos']
                 if c['test_id'] == f'Bike:1200:{aid}')
        return c['elegibilidade'], c['motivo_elegibilidade']

    def test_estados_true_false_ausente_none_e_inexistente(self):
        self.assertEqual(self.elig('Bike11'), ('elegivel', None))
        self.assertEqual(self.elig('Bike10'), ('inelegivel', 'icu_ignore_power'))
        self.assertEqual(self.elig('Bike9'), ('desconhecida', 'campo_ausente'))
        self.assertEqual(self.elig('Bike8'), ('desconhecida', 'campo_ausente'))
        self.assertEqual(self.elig('Bike7'), ('desconhecida', 'atividade_nao_encontrada'))

    def test_elegivel_confirma_sem_opt_in(self):
        t = self.post(aid='Bike11').get_json()['teste']
        self.assertEqual((t['elegibilidade'], t['aceita_elegibilidade_desconhecida']), ('elegivel', False))

    def test_inelegivel_nao_pode_ser_confirmado_mas_pode_ser_rejeitado(self):
        r = self.post(aid='Bike10', aceita_elegibilidade_desconhecida=True)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.n(), 0)
        t = self.post(aid='Bike10', dec='rejeitar', motivo='potencia ignorada').get_json()['teste']
        self.assertEqual((t['estado'], t['elegibilidade'], t['icu_ignore_power']),
                         ('rejeitado', 'inelegivel', True))
        c = next(c for c in self.cand()['candidatos'] if c['test_id'] == 'Bike:1200:Bike10')
        self.assertFalse(c['confirmavel'])
        self.assertIn('icu_ignore_power=true', c['motivo_nao_confirmavel'])

    def test_desconhecida_exige_opt_in_explicito_e_continua_desconhecida(self):
        for aid in ('Bike9', 'Bike8', 'Bike7'):
            r = self.post(aid=aid)
            self.assertEqual(r.status_code, 409, aid)
            self.assertEqual(r.get_json()['campo'], 'aceita_elegibilidade_desconhecida')
        self.assertEqual(self.n(), 0)
        t = self.post(aid='Bike9', aceita_elegibilidade_desconhecida=True).get_json()['teste']
        self.assertEqual((t['estado'], t['elegibilidade'], t['motivo_elegibilidade']),
                         ('confirmado', 'desconhecida', 'campo_ausente'))
        self.assertIsNone(t['icu_ignore_power'])              # nunca convertido em false
        self.assertTrue(t['aceita_elegibilidade_desconhecida'])

    def test_opt_in_nao_e_gravado_quando_nao_se_aplica(self):
        t = self.post(aid='Bike11', aceita_elegibilidade_desconhecida=True).get_json()['teste']
        self.assertFalse(t['aceita_elegibilidade_desconhecida'])

    def test_rejeitar_desconhecida_nao_precisa_de_opt_in(self):
        t = self.post(aid='Bike8', dec='rejeitar', motivo='nao sei se foi maximo').get_json()['teste']
        self.assertEqual((t['estado'], t['elegibilidade']), ('rejeitado', 'desconhecida'))

    def test_atividades_indisponiveis(self):
        self.ativ = None
        j = self.cand()
        self.assertTrue(all(c['elegibilidade'] == 'desconhecida' and
                            c['motivo_elegibilidade'] == 'atividades_indisponiveis'
                            for c in j['candidatos']))
        # a deteccao e a listagem continuam disponiveis
        self.assertGreater(j['n'], 0)
        self.assertEqual(self.post().status_code, 409)
        t = self.post(aceita_elegibilidade_desconhecida=True).get_json()['teste']
        self.assertEqual((t['elegibilidade'], t['hora_local']), ('desconhecida', None))

    def test_falha_a_ler_atividades_nao_derruba_a_decisao(self):
        with mock.patch.object(self.db, 'load_activities', side_effect=RuntimeError('boom')):
            r = self.post()
            self.assertEqual(r.status_code, 409)
            t = self.post(aceita_elegibilidade_desconhecida=True).get_json()['teste']
        self.assertEqual((t['elegibilidade'], t['motivo_elegibilidade']),
                         ('desconhecida', 'atividades_indisponiveis'))

    def test_decisao_usa_a_elegibilidade_do_momento_e_nao_a_cache(self):
        self.cand()                                           # aquece a cache (Bike11 elegivel)
        self.ativ = U.atividades(Bike11=True)                 # muda depois
        self.assertEqual(self.post().status_code, 409)


class Consultas(Base):
    def setUp(self):
        super().setUp()
        self.post(mod='Bike', dur=1200, aid='Bike11')
        self.post(mod='Bike', dur=300, aid='Bike11', dec='rejeitar', motivo='nao foi maximo')
        self.post(mod='Bike', dur=1200, aid='Bike10', dec='rejeitar', motivo='lesao')
        self.post(mod='Row', dur=1200, aid='Row7')
        self.post(mod='Row', dur=300, aid='Row6')

    def ids(self, url, chave):
        j = self.c.get(url).get_json()
        return sorted(x['test_id'] for x in j[chave])

    def test_por_modalidade_duracao_e_estado(self):
        self.assertEqual(self.ids('/api/protocolo/testes?tipo=Row', 'testes'),
                         ['Row:1200:Row7', 'Row:300:Row6'])
        self.assertEqual(self.ids('/api/protocolo/testes?tipo=Bike&duracao=1200', 'testes'),
                         ['Bike:1200:Bike10', 'Bike:1200:Bike11'])
        self.assertEqual(self.ids('/api/protocolo/testes?estado=rejeitado', 'testes'),
                         ['Bike:1200:Bike10', 'Bike:300:Bike11'])
        self.assertEqual(self.ids('/api/protocolo/testes?tipo=Bike&duracao=1200&estado=confirmado',
                                  'testes'), ['Bike:1200:Bike11'])
        self.assertEqual(self.ids('/api/protocolo/testes?tipo=Ski', 'testes'), [])

    def test_historico_por_modalidade_e_duracao(self):
        self.assertEqual(self.ids('/api/protocolo/testes/historico?tipo=Row&duracao=300', 'revisoes'),
                         ['Row:300:Row6'])
        self.assertEqual(self.ids('/api/protocolo/testes/historico?tipo=Bike', 'revisoes'),
                         ['Bike:1200:Bike10', 'Bike:1200:Bike11', 'Bike:300:Bike11'])

    def test_isolamento_entre_modalidades(self):
        antes = self.c.get('/api/protocolo/testes?tipo=Row').get_json()['testes']
        self.post(mod='Bike', dur=1200, aid='Bike11', dec='rejeitar', motivo='invalidado')
        depois = self.c.get('/api/protocolo/testes?tipo=Row').get_json()['testes']
        self.assertEqual(antes, depois)
        cands = {c['test_id']: c['estado_registo'] for c in self.cand(tipo='Row')['candidatos']}
        self.assertEqual(cands['Row:1200:Row7'], 'confirmado')
        self.assertEqual(cands['Row:1200:Row6'], 'pendente')
        self.assertNotIn('Bike:1200:Bike11', cands)

    def test_estado_nos_candidatos(self):
        cands = {c['test_id']: c for c in self.cand()['candidatos']}
        self.assertEqual(cands['Bike:1200:Bike11']['estado_registo'], 'confirmado')
        self.assertEqual(cands['Bike:300:Bike11']['estado_registo'], 'rejeitado')
        self.assertEqual(cands['Bike:1200:Bike9']['estado_registo'], 'pendente')
        pend = self.cand(estado='pendente', tipo='Bike', duracao=1200)['candidatos']
        self.assertTrue(pend and all(c['estado_registo'] == 'pendente' for c in pend))
        resumo = self.cand()['resumo_registo']
        self.assertEqual(resumo['Bike']['1200'], {'confirmado': 1, 'rejeitado': 1})

    def test_persistencia_apos_fechar_e_reabrir_a_ligacao(self):
        antes = self.db.testes_modelo_atuais()
        hist = self.db.revisoes_testes_modelo()
        self.assertEqual(len(antes), 5)
        self.db._conn.close()
        self.db._conn = None                                   # forca nova ligacao ao mesmo ficheiro
        self.assertEqual(self.db.testes_modelo_atuais(), antes)
        self.assertEqual(self.db.revisoes_testes_modelo(), hist)
        self.assertEqual(self.db.SQLITE_PATH, self.caminho)

    def test_resync_nao_reescreve_testes_confirmados(self):
        antes = self.db.testes_modelo_atuais()
        # a curva da atividade muda num re-sync, e as atividades sao reescritas
        alteradas = copy.deepcopy(CURVAS)
        for c in alteradas:
            if c['activity_id'] == 'Bike11':
                c['watts'] = [w + 40 for w in c['watts']]
        with mock.patch.object(self.db, 'load_power_curves',
                               side_effect=lambda tipo=None, desde=None:
                               [c for c in alteradas if not tipo or c['type'] == tipo]):
            testes = self.c.get('/api/protocolo/testes').get_json()['testes']
        self.assertEqual(self.db.testes_modelo_atuais(), antes)            # registo intacto
        t = next(t for t in testes if t['test_id'] == 'Bike:1200:Bike11')
        self.assertEqual((t['watts'], t['situacao_dados']), (311.0, 'divergente'))
        outro = next(t for t in testes if t['test_id'] == 'Row:1200:Row7')
        self.assertEqual(outro['situacao_dados'], 'conforme')
        with mock.patch.object(self.db, 'load_power_curves', return_value=[]):
            t = self.c.get('/api/protocolo/testes').get_json()['testes'][0]
        self.assertEqual(t['situacao_dados'], 'sem_curva')


class Compatibilidade(Base):
    def test_api_protocolo_inalterada_pelo_registo(self):
        antes = self.c.get('/api/protocolo').get_json()
        self.assertEqual(self.post().status_code, 200)
        self.post(mod='Row', dur=300, aid='Row6', dec='rejeitar', motivo='x')
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})
        depois = self.c.get('/api/protocolo').get_json()
        self.assertEqual(antes, depois)
        self.assertEqual(set(depois), {'status', 'cobertura', 'sugestoes', 'robustez',
                                       'detectados', 'como_funciona'})

    def test_registo_nao_muda_deteccao_sugestoes_nem_elegibilidade(self):
        import protocolo
        det0 = protocolo.detectar_testes(CURVAS)
        mapa = protocolo.mapa_ignore_power(self.ativ)
        a0 = protocolo.anotar_elegibilidade(det0, mapa)
        self.post()
        det1 = protocolo.detectar_testes(CURVAS)
        self.assertEqual(det0, det1)
        self.assertEqual(a0, protocolo.anotar_elegibilidade(det1, mapa))
        self.assertEqual(protocolo.sugerir(det0), protocolo.sugerir(det1))


class ConfirmarNaoAtualizaOModelo(Base):
    """Confirmar um teste so grava o registo: nenhum resultado do modelo muda."""

    def _resultados(self):
        import pmc
        import ftlm
        from datetime import datetime, timedelta
        ini = datetime(2025, 1, 1)
        serie = [{'date': (ini + timedelta(days=i)).strftime('%Y-%m-%d'), 'load': 50.0 + (i % 7)}
                 for i in range(420)]
        sessoes = [{'id': f's{i}', 'date': serie[i]['date'], 'type': 'Bike', 'tl': 50.0,
                    'cp': 250.0 + (i % 11)} for i in range(0, 420, 3)]
        curvas = U.curvas('Bike', 40, ini=ini.date(), passo=9)
        ativ = U.todas(curvas, False)
        with mock.patch.object(self.db, 'load_power_curves',
                               side_effect=lambda tipo=None, desde=None: curvas):
            ancora = pmc.calibrar_com_ancora(serie, ['Bike'], atividades=ativ)
        import numpy as np
        cargas = np.array([s['load'] for s in serie])
        cp = pmc._serie_por_dia(sessoes, [s['date'] for s in serie], 'cp', 'mean')
        return {'ancora': ancora,
                'homeo': pmc.modelo_homeostatico(serie, sessoes),
                'gamma': ftlm.fit_gamma(cargas, cp, lag=0, suavizar=3),
                'protocolo': self.c.get('/api/protocolo').get_json()}

    def test_resultados_do_modelo_identicos_antes_e_depois_de_confirmar(self):
        antes = self._resultados()
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post(mod='Row', dur=1200, aid='Row7').status_code, 200)
        self.post(mod='Bike', dur=300, aid='Bike10', dec='rejeitar', motivo='x')
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})
        depois = self._resultados()
        self.assertEqual(antes, depois)

    def test_nenhum_modulo_de_calculo_le_o_registo(self):
        for f in ('pmc.py', 'calibracao.py', 'ftlm.py', 'fmt.py', 'protocolo.py', 'export.py'):
            src = open(os.path.join(ROOT, f), encoding='utf-8').read()
            self.assertNotIn('modelo_testes', src, f)
            self.assertNotIn('testes_modelo_atuais', src, f)

    def test_nao_ha_tabelas_de_versoes_nem_ponteiro_de_producao(self):
        nomes = {r[0] for r in self.db._exec(
            "SELECT name FROM sqlite_master WHERE type='table'", fetch='all')}
        self.assertIn('modelo_testes_revisoes', nomes)
        # a deteccao automatica acrescenta UMA tabela (so INSERT); nao ha tabelas de versoes do
        # registo manual nem ponteiros de producao
        self.assertEqual({n for n in nomes if n.startswith('modelo')}, {'modelo_observacoes_auto', 'modelo_testes_revisoes'})

    def test_registo_e_append_only_no_codigo(self):
        src = open(os.path.join(ROOT, 'db.py'), encoding='utf-8').read()
        self.assertIsNone(re.search(r'(UPDATE|DELETE\s+FROM)\s+modelo_testes', src, re.I))
        self.assertIsNone(re.search(r'DROP\s+TABLE[^\n]*modelo_testes', src, re.I))
        # recriar_power_curves so apaga power_curves
        antes = self.post().get_json()['teste']
        self.db.recriar_power_curves()
        self.assertEqual(self.db.ultima_revisao_teste('Bike:1200:Bike11'), antes)


if __name__ == '__main__':
    unittest.main()
