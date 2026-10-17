"""Revisao de candidatos MMP: versao do valor (opcao A), historico append-only, estimador.

    python -m pytest tests/pmc/test_revisao_candidatos_mmp.py

Offline: SQLite temporaria, curvas/atividades simuladas. Dados SINTETICOS: verificam o
comportamento do codigo, nao sao evidencia sobre o atleta real.
"""
import copy
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fase1_util as U  # noqa: E402
from test_fase1_registo_api import Base, BIKE, ROW  # noqa: E402

AID, DUR = 'Bike11', 1200
W0 = 311.0          # watts de Bike11 aos 1200 s (base 300 + 11)


class Versao(Base):
    def setUp(self):
        super().setUp()
        self.curvs = copy.deepcopy(BIKE + ROW)
        m = mock.patch.object(self.db, 'load_power_curves',
                              side_effect=lambda tipo=None, desde=None: [
                                  c for c in self.curvs if not tipo or c['type'] == tipo])
        m.start()
        self.addCleanup(m.stop)

    # ---- helpers
    def muda(self, w, aid=AID, dur=DUR):
        for c in self.curvs:
            if c['activity_id'] == aid:
                c['watts'][c['secs'].index(dur)] = w
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})

    def c1(self, aid=AID):
        for c in self.cand()['candidatos']:
            if c['activity_id'] == aid and c['duracao_s'] == DUR:
                return c
        return None

    def decide(self, dec, **kw):
        r = self.post(dec=dec, **kw)
        return r

    def revs(self):
        return self.db.revisoes_testes_modelo()

    def estimador(self):
        import modelo_estimador as E
        return E.calcular_estado(self.revs(), self.ativ, self.curvs, '2026-12-31')

    def usadas(self):
        it = [i for i in self.estimador()['itens'] if i['modalidade'] == 'Bike' and i['duracao_s'] == DUR]
        return [] if not it else [o['revisao'] for o in it[0]['sequencia_usada']]

    # ---- 1
    def test_01_novo_candidato_pendente(self):
        c = self.c1()
        self.assertEqual(c['estado_registo'], 'pendente')
        self.assertFalse(c['valor_atualizado'])
        self.assertIsNone(c['decisao_anterior'])
        self.assertEqual(self.n(), 0)

    # ---- 2
    def test_02_mesmo_valor_nao_repete_pendencia(self):
        a = self.cand()['resumo_pendencias']
        b = self.cand()['resumo_pendencias']
        self.assertEqual(a, b)
        self.assertEqual(self.post().status_code, 200)
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})
        c = self.c1()
        self.assertEqual(c['estado_registo'], 'confirmado')
        self.assertEqual(self.cand()['resumo_pendencias']['pendentes_valor_atualizado'], 0)

    # ---- 3
    def test_03_aceitar_mantem_historico_e_tira_pendente(self):
        antes = self.cand()['resumo_pendencias']['pendentes']
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.n(), 1)
        self.assertEqual(self.cand()['resumo_pendencias']['pendentes'], antes - 1)
        self.assertEqual(self.usadas(), [1])

    # ---- 4
    def test_04_rejeitar_mantem_historico_e_bloqueia_modelo(self):
        self.assertEqual(self.post(dec='rejeitar').status_code, 200)
        self.assertEqual(self.c1()['estado_registo'], 'rejeitado')
        self.assertEqual(self.n(), 1)
        self.assertEqual(self.usadas(), [])

    # ---- 5 e 6
    def test_05_06_220_225_nova_pendencia_e_confirmacao_preserva(self):
        self.assertEqual(self.post().status_code, 200)
        r1 = copy.deepcopy(self.revs()[0])
        self.muda(W0 + 5)
        c = self.c1()
        self.assertEqual(c['estado_registo'], 'pendente')
        self.assertTrue(c['valor_atualizado'])
        self.assertEqual(c['decisao_anterior']['estado'], 'confirmado')
        self.assertEqual(c['decisao_anterior']['watts'], W0)
        self.assertEqual(self.revs()[0], r1)                     # revisao anterior intacta
        self.assertEqual(self.usadas(), [])                      # valor antigo nao vale
        self.assertEqual(self.post(watts_vistos=W0 + 5).status_code, 200)
        rs = self.revs()
        self.assertEqual(len(rs), 2)
        self.assertEqual(rs[0], r1)
        self.assertEqual((rs[1]['revisao'], rs[1]['estado'], rs[1]['watts']), (2, 'confirmado', W0 + 5))
        self.assertEqual(self.usadas(), [2])
        self.assertEqual(self.c1()['estado_registo'], 'confirmado')

    # ---- 7
    def test_07_rejeitar_novo_valor_preserva_e_nao_usa(self):
        self.assertEqual(self.post().status_code, 200)
        self.muda(W0 + 5)
        self.assertEqual(self.post(dec='rejeitar').status_code, 200)
        rs = self.revs()
        self.assertEqual([r['estado'] for r in rs], ['confirmado', 'rejeitado'])
        self.assertEqual(self.usadas(), [])
        self.assertEqual(self.c1()['estado_registo'], 'rejeitado')

    # ---- 8
    def test_08_ressincronizar_mesmo_valor_nao_cria_pendencia(self):
        self.post()
        self.muda(W0 + 5)
        self.post()
        self.muda(W0 + 5)            # resync com o mesmo valor
        self.assertEqual(self.c1()['estado_registo'], 'confirmado')
        self.assertEqual(self.cand()['resumo_pendencias']['pendentes_valor_atualizado'], 0)
        self.assertEqual(self.n(), 2)

    # ---- 9
    def test_09_valor_mudado_depois_de_rejeicao_cria_pendencia(self):
        self.post(dec='rejeitar')
        self.muda(W0 + 5)
        c = self.c1()
        self.assertEqual(c['estado_registo'], 'pendente')
        self.assertEqual(c['decisao_anterior']['estado'], 'rejeitado')
        self.assertEqual(self.post(watts_vistos=W0 + 5).status_code, 200)
        self.assertEqual(self.revs()[1]['estado'], 'confirmado')
        self.assertEqual(self.revs()[0]['estado'], 'rejeitado')

    # ---- 10
    def test_10_icu_ignore_power_continua_excluido(self):
        self.ativ = U.atividades(**{c['activity_id']: (c['activity_id'] == AID) for c in self.curvs})
        c = self.c1()
        self.assertEqual(c['elegibilidade'], 'inelegivel')
        self.assertFalse(c['confirmavel'])
        self.assertFalse(c['elegivel_para_estimador'])
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(self.n(), 0)

    # ---- 11
    def test_11_ignore_parts_nao_associavel_mantem_regra(self):
        self.ativ = U.atividades(**{c['activity_id']: False for c in self.curvs})
        for a in self.ativ:
            if a['id'] == AID:
                a['ignore_parts'] = [{'start_time': 0, 'end_time': 60}]
        c = self.c1()
        self.assertEqual(c['elegibilidade_estimador'], 'desconhecida')
        self.assertFalse(c['elegivel_para_estimador'])
        self.assertIn('ignore_parts', c['motivo_elegibilidade_estimador'])
        # regra do registo inalterada (so icu_ignore_power bloqueia); o estimador continua a excluir
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.usadas(), [])
        exc = [e['motivo'] for i in self.estimador()['itens'] for e in i['excluidas']
               if e['test_id'].endswith(AID)]
        self.assertEqual(exc, ['elegibilidade_desconhecida:ignore_parts_nao_associavel_ao_trecho'])

    # ---- 12
    def test_12_pendente_nunca_entra_no_estimador(self):
        self.assertEqual(self.revs(), [])
        self.assertEqual(self.usadas(), [])
        self.post()
        self.muda(W0 + 5)            # pendente de novo
        self.assertEqual(self.usadas(), [])
        exc = [e for i in self.estimador()['itens'] for e in i['excluidas'] if e['test_id'].endswith(AID)]
        self.assertEqual([e['motivo'] for e in exc], ['dados_divergente'])

    # ---- 13
    def test_13_curva_removida_ou_incompativel_nao_usa_revisao_antiga(self):
        self.post()
        self.assertEqual(self.usadas(), [1])
        self.curvs = [c for c in self.curvs if c['activity_id'] != AID]
        self.assertEqual(self.usadas(), [])
        self.assertIsNone(self.c1())
        exc = [e['motivo'] for i in self.estimador()['itens'] for e in i['excluidas']
               if e['test_id'].endswith(AID)]
        self.assertEqual(exc, ['dados_sem_curva'])

    # ---- extra: guarda de versao e compatibilidade
    def test_watts_vistos_desatualizado_da_409_sem_gravar(self):
        self.muda(W0 + 5)
        r = self.post(watts_vistos=W0)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.n(), 0)

    def test_watts_vistos_invalido_da_400(self):
        self.assertEqual(self.post(watts_vistos='x').status_code, 400)
        self.assertEqual(self.post(watts_vistos=-1).status_code, 400)

    def test_historico_ambiguo_nao_ocorre_com_estados_repetidos_de_versoes_distintas(self):
        self.post()
        self.muda(W0 + 5)
        self.post()
        self.muda(W0 + 9)
        self.post()
        self.assertEqual(self.usadas(), [3])
        self.assertEqual(self.c.get('/api/protocolo/testes').status_code, 200)

    def test_mesma_decisao_no_mesmo_valor_continua_idempotente(self):
        self.post()
        self.post()
        self.assertEqual(self.n(), 1)

    def test_14_json_antigo_compativel(self):
        j = self.cand()
        for k in ('status', 'n', 'candidatos', 'resumo_registo', 'persistencia'):
            self.assertIn(k, j)
        for k in ('modalidade', 'duracao_s', 'activity_id', 'data', 'watts', 'estado_registo',
                  'elegibilidade', 'confirmavel', 'revisao'):
            self.assertIn(k, j['candidatos'][0])
        self.assertEqual(set(j['resumo_pendencias']),
                         {'pendentes', 'pendentes_sem_decisao', 'pendentes_valor_atualizado'})

    def test_filtros_nao_fabricam_candidatos(self):
        n_all = self.cand()['n']
        n_bike = self.cand(tipo='Bike')['n']
        self.assertLessEqual(n_bike, n_all)
        for c in self.cand(tipo='Row')['candidatos']:
            self.assertEqual(c['modalidade'], 'Row')

    # ---- retorno a um valor anterior (contrato: versao nova, historico preservado)
    def test_retorno_a_valor_anterior_cria_nova_revisao_sem_apagar(self):
        self.assertEqual(self.post().status_code, 200)               # rev 1 @ W0
        self.muda(W0 + 5)
        self.assertEqual(self.post(watts_vistos=W0 + 5).status_code, 200)   # rev 2 @ W0+5
        r12 = copy.deepcopy(self.revs())
        self.muda(W0)                                                 # volta ao valor da rev 1
        c = self.c1()
        self.assertEqual(c['estado_registo'], 'pendente')             # ainda precisa de decisao
        self.assertTrue(c['valor_atualizado'])
        self.assertEqual(c['valor_igual_a_revisao'], 1)               # informativo
        self.assertEqual(self.usadas(), [])                           # rev 2 desatualizada, nao usada
        self.assertEqual(self.post(watts_vistos=W0).status_code, 200)
        rs = self.revs()
        self.assertEqual(rs[:2], r12)                                 # historico intacto
        self.assertEqual((rs[2]['revisao'], rs[2]['watts']), (3, W0))
        self.assertEqual(self.usadas(), [3])

    # ---- validacao usa as mesmas observacoes que o estimador
    def _alvos_validacao(self):
        import modelo_validacao as V
        res = V.avaliar(self.revs(), self.ativ, self.curvs, 'retrospectivo', tipo='Bike', duracao=DUR)
        it = [i for i in res['itens'] if i['modalidade'] == 'Bike' and i['duracao_s'] == DUR]
        return it[0]['n_observacoes_usaveis'] if it else 0

    def test_validacao_nao_usa_pendente_rejeitado_nem_versao_desatualizada(self):
        import modelo_estimador as E
        def alvos():
            atuais = E.estado_atual_por_teste(self.revs())
            sel = E.selecionar_observacoes(atuais, self.ativ, E.curvas_por_atividade(self.curvs))
            return [o['test_id'] for o in sel.get(('Bike', DUR), {}).get('usadas', [])]
        self.assertEqual(alvos(), [])                                 # pendente
        self.assertEqual(self._alvos_validacao(), 0)
        self.post(dec='rejeitar')
        self.assertEqual(alvos(), [])                                 # rejeitado
        self.muda(W0 + 5)
        self.post(watts_vistos=W0 + 5)
        self.assertEqual(len(alvos()), 1)                             # confirmado e atual
        self.assertEqual(self._alvos_validacao(), 1)
        self.muda(W0 + 9)
        self.assertEqual(alvos(), [])                                 # versao desatualizada
        self.assertEqual(self._alvos_validacao(), 0)                  # sem HistoricoAmbiguo


if __name__ == '__main__':
    unittest.main()
