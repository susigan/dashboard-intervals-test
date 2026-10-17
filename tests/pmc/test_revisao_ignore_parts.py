"""Candidatos MMP com ignore_parts: visiveis e decidiveis; fora do estimador/validacao.
Tambem: potencia invalida nunca vira candidato e a sincronizacao repetida e idempotente.

    python -m pytest tests/pmc/test_revisao_ignore_parts.py

Offline: SQLite temporaria; curvas gravadas com db.upsert_power_curves (o mesmo caminho da
sincronizacao) e lidas com db.load_power_curves. Dados SINTETICOS: testam o codigo, nao o atleta.
"""
import copy
import math
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fase1_util as U  # noqa: E402
from test_fase1_registo_api import Base, BIKE, ROW  # noqa: E402

AID, DUR, W0 = 'Bike11', 1200, 311.0
FORMAS = [[{'start_time': 0, 'end_time': 60}], [1, 2], 'x', {'a': 1}, True]
MOTIVO_IP = 'ignore_parts_nao_associavel_ao_trecho'


class IgnorePartsBase(Base):
    def setUp(self):                      # sem mock de curvas: caminho real upsert -> load
        self.curvs = copy.deepcopy(BIKE + ROW)
        self.ativ = U.todas(self.curvs, False)
        for a in self.ativ:
            if a['id'] == AID:
                a['ignore_parts'] = FORMAS[0]
        self._ctx = U.base_temporaria(self.db)
        self.caminho = self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})
        self.addCleanup(self.app._cache_ignore_power.update, {'t': None, 'mapa': None})
        self.c = self.app.app.test_client()
        m = mock.patch.object(self.db, 'load_activities', side_effect=lambda **k: self.ativ)
        m.start()
        self.addCleanup(m.stop)
        self.sync()

    def sync(self):
        """Equivale a uma sincronizacao de curvas: upsert de todas as linhas."""
        self.db.upsert_power_curves(copy.deepcopy(self.curvs))
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})

    def muda(self, w, aid=AID, dur=DUR):
        for c in self.curvs:
            if c['activity_id'] == aid:
                c['watts'][c['secs'].index(dur)] = w
        self.sync()

    def forma(self, f):
        for a in self.ativ:
            if a['id'] == AID:
                a['ignore_parts'] = f
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})

    def c1(self, aid=AID):
        return next((c for c in self.cand()['candidatos']
                     if c['activity_id'] == aid and c['duracao_s'] == DUR), None)

    def revs(self):
        return self.db.revisoes_testes_modelo()

    def _itens(self):
        import modelo_estimador as E
        return E.calcular_estado(self.revs(), self.ativ, self.curvs, '2026-12-31')['itens']

    def usadas(self):
        it = [i for i in self._itens() if i['modalidade'] == 'Bike' and i['duracao_s'] == DUR]
        return [] if not it else [o['revisao'] for o in it[0]['sequencia_usada']]


class VisivelEDecidivel(IgnorePartsBase):
    def test_aparece_para_revisao_em_todas_as_formas_de_ignore_parts(self):
        for f in FORMAS:
            with self.subTest(forma=repr(f)):
                self.forma(f)
                c = self.c1()
                self.assertIsNotNone(c, 'ignore_parts nao pode esconder o candidato')
                self.assertEqual(c['estado_registo'], 'pendente')
                self.assertTrue(c['confirmavel'])
                self.assertFalse(c['requer_aceitar_elegibilidade_desconhecida'])
                self.assertEqual(c['elegibilidade'], 'elegivel')          # regra do registo
                self.assertEqual(c['elegibilidade_estimador'], 'desconhecida')
                self.assertEqual(c['motivo_elegibilidade_estimador'], MOTIVO_IP)
                self.assertFalse(c['elegivel_para_estimador'])

    def test_confirmar_individualmente_nao_toca_nos_outros(self):
        antes = {c['test_id']: c['estado_registo'] for c in self.cand()['candidatos']}
        self.assertEqual(self.post().status_code, 200)
        depois = {c['test_id']: c['estado_registo'] for c in self.cand()['candidatos']}
        mudou = [k for k in antes if antes[k] != depois[k]]
        self.assertEqual(mudou, [f'Bike:{DUR}:{AID}'])
        self.assertEqual(depois[f'Bike:{DUR}:{AID}'], 'confirmado')
        self.assertEqual(self.n(), 1)

    def test_rejeitar_individualmente(self):
        self.assertEqual(self.post(dec='rejeitar').status_code, 200)
        self.assertEqual(self.c1()['estado_registo'], 'rejeitado')
        self.assertEqual(self.n(), 1)

    def test_historico_e_decisoes_preservados(self):
        self.assertEqual(self.post().status_code, 200)
        r1 = copy.deepcopy(self.revs()[0])
        self.assertEqual(self.post(dec='rejeitar').status_code, 200)
        rs = self.revs()
        self.assertEqual([r['estado'] for r in rs], ['confirmado', 'rejeitado'])
        self.assertEqual(rs[0], r1)                                       # a primeira nao mudou
        self.assertEqual(self.post().status_code, 200)                    # reconfirmar
        self.assertEqual([r['revisao'] for r in self.revs()], [1, 2, 3])

    def test_valor_220_para_225_cria_nova_revisao_pendente(self):
        self.assertEqual(self.post().status_code, 200)
        r1 = copy.deepcopy(self.revs()[0])
        self.muda(W0 + 5)
        c = self.c1()
        self.assertEqual(c['estado_registo'], 'pendente')
        self.assertTrue(c['valor_atualizado'])
        self.assertEqual(c['decisao_anterior']['estado'], 'confirmado')
        self.assertEqual(c['decisao_anterior']['watts'], W0)
        self.assertEqual(self.revs(), [r1])                               # nada foi reescrito
        self.assertEqual(self.post(watts_vistos=W0 + 5).status_code, 200)
        rs = self.revs()
        self.assertEqual(rs[0], r1)
        self.assertEqual((rs[1]['revisao'], rs[1]['watts']), (2, W0 + 5))

    def test_409_com_watts_vistos_desatualizado_nao_regista(self):
        self.muda(W0 + 5)
        r = self.post(watts_vistos=W0)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json().get('campo'), 'watts_vistos')
        self.assertEqual(self.n(), 0)


class ContinuaForaDoEstimadorEDaValidacao(IgnorePartsBase):
    def test_confirmado_com_ignore_parts_nao_entra_no_estimador_nem_na_validacao(self):
        import modelo_validacao as V
        for f in FORMAS:
            with self.subTest(forma=repr(f)):
                self.forma(f)
                self.db._exec('DELETE FROM modelo_testes_revisoes')
                self.assertEqual(self.post().status_code, 200)
                it = [i for i in self._itens() if i['modalidade'] == 'Bike' and i['duracao_s'] == DUR][0]
                self.assertEqual(self.usadas(), [])
                self.assertEqual(it['n_usadas'], 0)
                self.assertEqual(it['motivos_exclusao'], {'elegibilidade_desconhecida:' + MOTIVO_IP: 1})
                v = V.avaliar(self.revs(), self.ativ, self.curvs, 'retrospectivo', tipo='Bike', duracao=DUR)
                self.assertEqual(v['itens'][0]['n_observacoes_usaveis'], 0)

    def test_sem_ignore_parts_a_mesma_confirmacao_entra(self):
        self.forma(None)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.usadas(), [1])                              # controlo: o filtro e o ignore_parts


class PotenciaInvalida(IgnorePartsBase):
    INVALIDAS = [True, False, float('nan'), float('inf'), float('-inf'), None, 0, 0.0, -5.0, '300']

    def test_candidatos_rejeita_potencia_invalida_e_mantem_a_valida(self):
        import modelo_testes as mt
        def det(w):
            return {'Bike': {1200: {'testes': [{'date': '2026-03-01', 'watts': w, 'activity_id': 'x',
                                               'pct_do_melhor': 100.0, 'melhor_recente': 300.0,
                                               'elegibilidade_calibracao': 'elegivel',
                                               'motivo_elegibilidade': None}]}}}
        for w in self.INVALIDAS:
            with self.subTest(watts=repr(w)):
                self.assertEqual(mt.candidatos(det(w)), [])
        for w in (300.0, 1, 0.5):
            with self.subTest(valida=w):
                self.assertEqual(len(mt.candidatos(det(w))), 1)

    def test_curva_com_potencia_invalida_nao_gera_candidato_nem_decisao(self):
        base = copy.deepcopy(self.curvs)
        for w in self.INVALIDAS:
            with self.subTest(watts=repr(w)):
                self.curvs = copy.deepcopy(base)
                novo = {'activity_id': 'BikeX', 'type': 'Bike', 'date': '2026-09-01', 'weight': 70.0,
                        'secs': [60, 300, 1200], 'watts': [500.0, 400.0, w]}
                self.curvs.append(novo)
                self.ativ = self.ativ + [{'id': 'BikeX', 'icu_ignore_power': False}]
                try:
                    self.db.upsert_power_curves([novo])
                except Exception:
                    continue          # a base recusar o valor (JSON) tambem e aceitavel
                self.app._cache_ignore_power.update({'t': None, 'mapa': None})
                self.assertIsNone(self.c1('BikeX'))
                self.assertGreaterEqual(self.post(aid='BikeX').status_code, 400)
                self.assertEqual(self.n(), 0)


class SincronizacaoRepetida(IgnorePartsBase):
    def test_sync_repetida_nao_duplica_candidatos_nem_perde_decisoes(self):
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post(aid='Bike10', dec='rejeitar').status_code, 200)
        antes = self.cand()
        revs = copy.deepcopy(self.revs())
        n_curvas = len(self.db.load_power_curves())
        for _ in range(3):
            self.sync()
        depois = self.cand()
        self.assertEqual(len(self.db.load_power_curves()), n_curvas)
        self.assertEqual([c['test_id'] for c in depois['candidatos']],
                         [c['test_id'] for c in antes['candidatos']])
        self.assertEqual(len({c['test_id'] for c in depois['candidatos']}), len(depois['candidatos']))
        self.assertEqual(self.revs(), revs)                               # nenhuma revisao nova/perdida
        self.assertEqual(depois['resumo_pendencias'], antes['resumo_pendencias'])
        self.assertEqual(self.c1()['estado_registo'], 'confirmado')
        self.assertEqual(self.c1('Bike10')['estado_registo'], 'rejeitado')

    def test_sync_com_valor_novo_cria_so_uma_pendencia_e_guarda_o_historico(self):
        self.post()
        r1 = copy.deepcopy(self.revs())
        self.muda(W0 + 5)
        self.sync()                                                       # repetir nao multiplica
        self.assertEqual(self.cand()['resumo_pendencias']['pendentes_valor_atualizado'], 1)
        self.assertEqual(self.revs(), r1)


if __name__ == '__main__':
    unittest.main()
