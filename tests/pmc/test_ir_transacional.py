"""PR #42 — consistencia transacional, reconciliacao direcionada, divergencias de YES e deduplicacao (regra 5).

    python -m pytest tests/pmc/test_ir_transacional.py

Offline e sem API: base SQLite temporaria, dados sinteticos gravados pela propria camada de base de dados, e
sync.icu_get substituido por mock (com a camada de rede bloqueada para garantir que nao ha chamadas reais).
"""
import importlib
import os
import sys
import unittest
from datetime import date
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fase1_util as U  # noqa: E402
import test_ir_mmp3 as M  # noqa: E402


def _ativ(aid, dia, tags=None, ignore=False):
    return {'id': aid, 'start_date_local': f'{dia}T07:30:00', 'icu_ignore_power': ignore, 'tags': tags or []}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.app = U.importar_app()
        except BaseException as e:
            raise unittest.SkipTest(f'app nao importa: {type(e).__name__}: {e}')
        cls.db = cls.app.db
        cls.sync = importlib.import_module('sync')
        cls.mis = importlib.import_module('modelo_ir_servico')
        cls.mt = importlib.import_module('modelo_testes')
        cls.mm = importlib.import_module('modelo_mmp3')

    def setUp(self):
        self._ctx = U.base_temporaria(self.db)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        p = mock.patch('observacoes_auto.obter_marcos', return_value=(None, 'teste'))
        p.start()
        self.addCleanup(p.stop)
        self.c = self.app.app.test_client()

    # helpers -------------------------------------------------------------
    def seed(self, vals, dia0=(2026, 1), tags=None, prefixo='e'):
        """Uma atividade por valor de MMP3 (180 s), um mes por atividade a partir de dia0."""
        cv, at = [], []
        for i, w in enumerate(vals):
            aid = f'{prefixo}{i}'
            dia = f'{dia0[0]}-{dia0[1] + i:02d}-10'
            cv.append(M.c180(aid, 'Bike', dia, w))
            at.append(_ativ(aid, dia, (tags or {}).get(i)))
        self.db.upsert_activities([self.sync.to_row(a) for a in at])
        self.db.upsert_power_curves(cv)
        return cv, at

    def tid(self, aid):
        return f'Bike:180:{aid}'

    def decidir(self, aid, watts, decisao='confirmar'):
        return self.c.post('/api/protocolo/testes/decisao', json={
            'modalidade': 'Bike', 'duracao_s': 180, 'activity_id': aid, 'decisao': decisao,
            'motivo': 'teste padronizado, aquecimento feito', 'confirmacao_explicita': True,
            'origem_registo': 'api', 'watts_vistos': watts})

    def n_revisoes(self, aid):
        return self.db._exec('SELECT COUNT(*) FROM modelo_testes_revisoes WHERE test_id = ?',
                             (self.tid(aid),), fetch='one')[0]

    def n_yes_rev(self, aid):
        return self.db._exec('SELECT COUNT(*) FROM modelo_mmp3_yes_revisoes WHERE test_id = ?',
                             (self.tid(aid),), fetch='one')[0]

    def yes_vigente(self, aid):
        for r in self.db.yes_historico():
            if r['test_id'] == self.tid(aid):
                return r
        return None

    def candidatos(self):
        return self.mis.candidatos_mmp3(self.db.load_power_curves(), self.db.load_activities() or [],
                                        date.today().isoformat(), None, self.db.revisoes_testes_modelo(), 'Bike')


class Transacao(Base):
    """1 e 2: falha simulada entre etapas, repeticao e recuperacao."""

    def test_falha_entre_etapas_nao_deixa_estado_parcial(self):
        self.seed([300.0] * 5 + [330.0])
        # trigger que aborta a 2a etapa (linha YES) depois de a revisao ter sido inserida na mesma transacao
        self.db._exec("CREATE TRIGGER falha_yes BEFORE INSERT ON modelo_mmp3_yes_revisoes "
                      "BEGIN SELECT RAISE(ABORT, 'falha simulada'); END")
        self.addCleanup(lambda: self.db._exec('DROP TRIGGER IF EXISTS falha_yes'))
        r = self.decidir('e5', 330.0)
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(self.n_revisoes('e5'), 0, 'a revisao nao pode ficar gravada sem o YES')
        self.assertEqual(self.n_yes_rev('e5'), 0)
        self.assertIsNone(self.yes_vigente('e5'))

    def test_recuperacao_apos_falha_sem_duplicar(self):
        self.seed([300.0] * 5 + [330.0])
        self.db._exec("CREATE TRIGGER falha_yes BEFORE INSERT ON modelo_mmp3_yes_revisoes "
                      "BEGIN SELECT RAISE(ABORT, 'falha simulada'); END")
        self.decidir('e5', 330.0)
        self.db._exec('DROP TRIGGER falha_yes')
        r = self.decidir('e5', 330.0)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()['alterado'])
        self.assertEqual((self.n_revisoes('e5'), self.n_yes_rev('e5')), (1, 1))

    def test_repeticao_do_pedido_e_idempotente(self):
        self.seed([300.0] * 5 + [330.0])
        self.assertTrue(self.decidir('e5', 330.0).get_json()['alterado'])
        r = self.decidir('e5', 330.0)
        self.assertFalse(r.get_json()['alterado'])
        self.assertEqual((self.n_revisoes('e5'), self.n_yes_rev('e5')), (1, 1))

    def test_alteracao_concorrente_do_valor_e_recusada_sem_escrever(self):
        cv, _ = self.seed([300.0] * 5 + [330.0])
        # o cliente viu 330 W; entretanto a fonte passou a 335 W
        self.db.upsert_power_curves([M.c180('e5', 'Bike', '2026-06-10', 335.0)])
        r = self.decidir('e5', 330.0)
        self.assertEqual(r.status_code, 409, r.get_json())
        self.assertEqual((self.n_revisoes('e5'), self.n_yes_rev('e5')), (0, 0))

    def test_recuperacao_repara_yes_em_falta_sem_nova_revisao(self):
        self.seed([300.0] * 5 + [330.0])
        self.assertTrue(self.decidir('e5', 330.0).get_json()['alterado'])
        self.db._exec('DELETE FROM modelo_mmp3_yes_revisoes WHERE test_id = ?', (self.tid('e5'),))  # perda simulada
        r = self.decidir('e5', 330.0)
        self.assertFalse(r.get_json()['alterado'])
        self.assertEqual(r.get_json()['yes_reparado'], 1)
        self.assertEqual((self.n_revisoes('e5'), self.n_yes_rev('e5')), (1, 1))
        self.assertEqual(self.decidir('e5', 330.0).get_json()['yes_reparado'], 0)


class Divergencia(Base):
    """3: YES confirmado cuja fonte muda. Historico preservado, divergencia visivel, nova decisao explicita."""

    def test_divergencia_visivel_sem_recalibracao_silenciosa(self):
        self.seed([300.0] * 5 + [330.0])
        self.decidir('e5', 330.0)
        self.db.upsert_power_curves([M.c180('e5', 'Bike', '2026-06-10', 335.0)])
        self.assertEqual(self.yes_vigente('e5')['watts'], 330.0)          # P0 nao muda sem decisao
        div = self.c.get('/api/protocolo/divergencias').get_json()['divergencias']
        self.assertEqual(len(div), 1)
        d = div[0]
        self.assertEqual((d['watts_confirmado'], d['watts_atual'], d['diferenca_w']), (330.0, 335.0, 5.0))
        self.assertEqual(d['motivo'], self.mm.M_VALOR_MUDOU)
        self.assertEqual(d['estado_revisao'], 'pendente_de_nova_decisao')
        self.assertEqual(len(d['historico_revisoes']), 1)

    def test_nova_decisao_explicita_mantem_historico_original(self):
        self.seed([300.0] * 5 + [330.0])
        self.decidir('e5', 330.0)
        self.db.upsert_power_curves([M.c180('e5', 'Bike', '2026-06-10', 335.0)])
        r = self.decidir('e5', 335.0)                                       # nova decisao com o valor atual
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.n_yes_rev('e5'), 2)                           # original (330) + nova (335)
        self.assertEqual(self.yes_vigente('e5')['watts'], 335.0)            # a revisao mais recente prevalece
        self.assertEqual(self.c.get('/api/protocolo/divergencias').get_json()['divergencias'], [])
        revs = self.db.revisoes_testes_modelo(test_id=self.tid('e5'))
        self.assertEqual([x['watts'] for x in revs], [330.0, 335.0])

    def test_migracao_preserva_yes_antigo_e_cria_tabela_nova(self):
        self.seed([300.0] * 5 + [330.0])
        self.db._exec('DROP TABLE modelo_mmp3_yes_revisoes')                # base anterior ao PR
        self.db.gravar_yes_historico([{'test_id': 'Bike:180:antigo', 'regra_versao': 5, 'activity_id': 'antigo',
                                       'modalidade': 'Bike', 'data': '2025-06-10', 'watts': 280.0}],
                                     '2025-06-10T08:00:00')
        self.db.init_schema()
        self.db.init_schema()                                               # idempotente
        antigo = [r for r in self.db.yes_historico() if r['test_id'] == 'Bike:180:antigo']
        self.assertEqual((len(antigo), antigo[0]['watts'], antigo[0]['revisao']), (1, 280.0, 0))
        self.assertEqual(self.db._exec('SELECT COUNT(*) FROM modelo_mmp3_yes_revisoes', fetch='one')[0], 0)


class Deduplicacao(Base):
    """4: tres atividades consecutivas com o mesmo MMP3; reexecucao; atualizacao; confirmacao de uma."""

    def test_tres_iguais_sao_candidatos_distintos_sem_duplicados(self):
        self.seed([300.0] * 8)                                              # e1..e7 iguais a referencia
        ids = [c['test_id'] for c in self.candidatos()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({self.tid('e5'), self.tid('e6'), self.tid('e7')} <= set(ids))

    def test_reexecucao_e_atualizacao_nao_criam_candidatos_nem_revisoes(self):
        cv, at = self.seed([300.0] * 8)
        antes = self.candidatos()
        self.assertEqual(self.candidatos(), antes)                          # reexecucao da deteccao
        self.db.upsert_activities([self.sync.to_row(at[6])])                # atualizacao da mesma atividade
        self.db.upsert_power_curves([cv[6]])
        ids = [c['test_id'] for c in self.candidatos()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.n_revisoes('e6'), 0)

    def test_confirmar_uma_so_promove_essa_e_nao_muda_as_outras(self):
        self.seed([300.0] * 8)
        self.assertTrue(self.decidir('e6', 300.0).get_json()['alterado'])   # igualdade confirmada, sem superioridade
        self.assertEqual(self.yes_vigente('e6')['watts'], 300.0)
        self.assertIsNone(self.yes_vigente('e5'))
        self.assertIsNone(self.yes_vigente('e7'))
        self.assertEqual(self.n_revisoes('e5'), 0)
        self.assertEqual(self.n_revisoes('e7'), 0)


class Reconciliacao(Base):
    """2: reconciliacao direcionada de atividades antigas (1 pedido, sem API real, idempotente)."""

    def setUp(self):
        super().setUp()
        # 5 atividades ANTERIORES a janela (amostra minima do IR) + 3 DENTRO da janela de reconciliacao
        # (2026-06-22 a 2026-09-29, com hoje = 2026-10-20)
        self.seed([300.0] * 5, dia0=(2026, 1), prefixo='a')
        self.seed([300.0, 300.0, 300.0], dia0=(2026, 7))
        self.p_icu = mock.patch.object(self.sync, 'icu_get')
        self.icu = self.p_icu.start()
        self.addCleanup(self.p_icu.stop)
        self.rede = mock.patch('api_client.requests.get', side_effect=AssertionError('chamada real a API'))
        self.rede.start()
        self.addCleanup(self.rede.stop)

    def _api(self, tags_e1=None):
        return ([_ativ('e0', '2026-07-10'), _ativ('e1', '2026-08-10', tags=tags_e1),
                 _ativ('e2', '2026-09-10')], None)

    def test_altera_so_a_atividade_mudada_e_faz_um_pedido(self):
        self.icu.return_value = self._api(tags_e1=['3min'])
        r = self.sync.reconciliar_antigas(21, 120)
        self.assertEqual(self.icu.call_count, 1)
        params = self.icu.call_args.args[1] if len(self.icu.call_args.args) > 1 else self.icu.call_args.kwargs['params']
        self.assertIn('oldest', params)
        self.assertIn('newest', params)
        self.assertEqual(r['alteradas'], ['e1'])
        self.assertEqual(r['novas'], [])
        self.assertEqual(r['pedidos'], 1)
        tags = {a['id']: a.get('tags') for a in self.db.load_activities()}
        self.assertEqual(tags['e1'], ['3min'])

    def test_reexecucao_e_idempotente(self):
        self.icu.return_value = self._api(tags_e1=['3min'])
        self.sync.reconciliar_antigas(21, 120)
        r = self.sync.reconciliar_antigas(21, 120)
        self.assertEqual((r['alteradas'], r['novas'], r['actualizadas']), ([], [], 0))

    def test_confirmacao_manual_e_preservada_apos_reconciliacao(self):
        self.db.upsert_power_curves([M.c180('e1', 'Bike', '2026-08-10', 330.0)])
        self.assertTrue(self.decidir('e1', 330.0).get_json()['alterado'])
        self.icu.return_value = self._api(tags_e1=['3min'])
        self.sync.reconciliar_antigas(21, 120)
        self.assertEqual(self.n_revisoes('e1'), 1)
        self.assertEqual(self.yes_vigente('e1')['watts'], 330.0)

    def test_nao_toca_nas_curvas_de_potencia(self):
        antes = self.db._exec('SELECT COUNT(*) FROM power_curves', fetch='one')[0]
        self.icu.return_value = self._api(tags_e1=['3min'])
        self.sync.reconciliar_antigas(21, 120)
        self.assertEqual(self.db._exec('SELECT COUNT(*) FROM power_curves', fetch='one')[0], antes)

    def test_janela_invalida_nao_chama_api(self):
        r = self.sync.reconciliar_antigas(120, 21)
        self.assertFalse(r['ok'])
        self.icu.assert_not_called()

    def test_janela_aplicada_localmente_e_sem_duplicar(self):
        # a API devolve uma atividade FORA da janela e um id REPETIDO: nada fora da janela e escrito e
        # a escrita e por id (uma linha por atividade), independentemente de a API honrar `newest`.
        dados = [_ativ('e0', '2026-07-10'), _ativ('e1', '2026-08-10', tags=['3min']),
                 _ativ('e1', '2026-08-10', tags=['3min']), _ativ('fora', '2026-10-15')]
        self.icu.return_value = (dados, None)
        r = self.sync.reconciliar_antigas(21, 120)
        self.assertEqual(r['fora_da_janela'], 1)
        self.assertEqual(r['alteradas'], ['e1'])
        ids = [a['id'] for a in self.db.load_activities()]
        self.assertNotIn('fora', ids)
        self.assertEqual(ids.count('e1'), 1)
        self.assertEqual(self.db._exec('SELECT COUNT(*) FROM activities WHERE id = ?', ('e1',), fetch='one')[0], 1)

    def test_interval_summary_alterado_chega_a_base(self):
        base = _ativ('e1', '2026-08-10')
        self.icu.return_value = ([_ativ('e0', '2026-07-10'), dict(base, interval_summary=['x']),
                                  _ativ('e2', '2026-09-10')], None)
        r = self.sync.reconciliar_antigas(21, 120)
        self.assertEqual(r['alteradas'], ['e1'])
        ativ = {a['id']: a for a in self.db.load_activities()}
        self.assertEqual(ativ['e1'].get('interval_summary'), ['x'])

    def test_rota_reconciliar_faz_um_pedido_e_reexecuta_a_deteccao(self):
        self.icu.return_value = ([_ativ('e0', '2026-07-10'), _ativ('e1', '2026-08-10', tags=['3min']),
                                  _ativ('e2', '2026-09-10')], None)
        with mock.patch('observacoes_auto.atualizar', return_value={'ok': True}) as obs, \
                mock.patch.object(self.mis, 'apos_deteccao', return_value={'ok': True}) as ir:
            r = self.c.get('/api/sync/reconciliar?dias_inicio=21&dias_fim=120').get_json()
        self.assertTrue(r['ok'])
        self.assertEqual(r['alteradas'], ['e1'])
        self.assertEqual(self.icu.call_count, 1)
        obs.assert_called_once()
        ir.assert_called_once()


if __name__ == '__main__':
    unittest.main()
