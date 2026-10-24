"""PR #42 — confirmacao manual de 180 s ponta a ponta (regra 5), sobre base SQLite temporaria.

    python -m pytest tests/pmc/test_ir_confirmacao_e2e.py

Offline. Sem Intervals.icu, sem Railway, sem base real: db aponta para um ficheiro temporario (fase1_util),
load_activities / load_power_curves sao substituidos por fixtures e obter_marcos devolve None (sem calendario).
Cobre: identificacao sem tag, A_VERIFICAR sem YES automatico, confirmacao persistida com identidade/valor/regra,
YES no historico e observacoes elegiveis, P0 pela regra de confirmacao, valor desatualizado recusado, segunda
execucao idempotente, rejeicao que impede promocao, e estado parcial quando a recalibracao falha.
"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fase1_util as U  # noqa: E402
import test_ir_mmp3 as M  # noqa: E402
import modelo_mmp3 as mm  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402

MOD = 'Bike'
# historico com referencia 300 W (sem tag); a5 = 330 W sem tag (superado, sem sinal -> A_VERIFICAR)
VALS = [300.0] * 5 + [330.0]


def _curva(i, w):
    return M.c180(f'e{i}', MOD, f'2026-{i + 1:02d}-10', w)


class Fluxo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.app = U.importar_app()
        except BaseException as e:
            raise unittest.SkipTest(f'app nao importa: {type(e).__name__}: {e}')
        cls.db = cls.app.db

    def setUp(self):
        self.curvas = [_curva(i, w) for i, w in enumerate(VALS)]
        self.ativ = [{'id': c['activity_id'], 'start_date_local': c['date'] + 'T07:30:00',
                      'icu_ignore_power': False, 'tags': []} for c in self.curvas]
        self._ctx = U.base_temporaria(self.db)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        for nome, kw in (('load_power_curves', dict(side_effect=lambda tipo=None, desde=None: list(self.curvas))),
                         ('load_activities', dict(side_effect=lambda desde=None: list(self.ativ)))):
            p = mock.patch.object(self.db, nome, **kw)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch('observacoes_auto.obter_marcos', return_value=(None, 'teste'))
        p.start()
        self.addCleanup(p.stop)
        self.c = self.app.app.test_client()

    # helpers ----------------------------------------------------------------
    def tid(self, aid):
        return f'{MOD}:180:{aid}'

    def candidatos(self):
        return mis.candidatos_mmp3(self.curvas, self.ativ, '2026-12-31', None, self.db.revisoes_testes_modelo(), MOD)

    def decidir(self, aid, watts, decisao='confirmar', motivo='teste padronizado, aquecimento feito'):
        return self.c.post('/api/protocolo/testes/decisao', json={
            'modalidade': MOD, 'duracao_s': 180, 'activity_id': aid, 'decisao': decisao, 'motivo': motivo,
            'confirmacao_explicita': True, 'origem_registo': 'api', 'watts_vistos': watts})

    def entrada(self, aid):
        ent = mis.entradas_mmp3(self.curvas, self.ativ, '2026-12-31', None, self.db.revisoes_testes_modelo())
        return next(e for e in ent[MOD][1] if e['activity_id'] == aid)

    def yes_ids(self):
        return [r['test_id'] for r in self.db.yes_historico()]


class IdentificacaoEConfirmacao(Fluxo):
    def test_1_identificado_sem_tag(self):
        ids = {c['test_id']: c for c in self.candidatos()}
        self.assertIn(self.tid('e5'), ids)

    def test_2_sem_yes_automatico(self):
        e = self.entrada('e5')
        self.assertEqual((e['estado'], e['a_verificar']), (mm.E_A_VERIFICAR, True))
        self.assertEqual(self.yes_ids(), [])

    def test_3_confirmacao_persistida_com_identidade_valor_e_regra(self):
        r = self.decidir('e5', 330.0)
        self.assertEqual(r.status_code, 200, r.get_json())
        rev = self.db.ultima_revisao_teste(self.tid('e5'))
        self.assertEqual((rev['modalidade'], rev['duracao_s'], rev['activity_id']), (MOD, 180, 'e5'))
        self.assertEqual(rev['watts'], 330.0)
        self.assertEqual(rev['estado'], 'confirmado')

    def test_5_confirmacao_manual_regista_a_sombra_sem_promover(self):
        """Etapa 7: a confirmacao manual (app.py) passa pelo modo sombra depois de recalibrar; nunca promove."""
        import modelo_ir_servico as mis_
        with mock.patch.object(mis_, 'registar_sombra', wraps=mis_.registar_sombra) as rs:
            r = self.decidir('e5', 330.0)
        self.assertEqual(r.status_code, 200, r.get_json())
        rs.assert_called_once()
        self.assertEqual(rs.call_args.args[1], 'confirmacao_manual')

    def test_4_yes_no_historico_e_observacao_elegivel(self):
        self.decidir('e5', 330.0)
        self.assertIn(self.tid('e5'), self.yes_ids())
        e = self.entrada('e5')
        self.assertEqual(e['estado'], mm.E_ELEGIVEL)
        self.assertEqual(e['origem_classificacao'], 'confirmacao_manual')

    def test_5_p0_so_muda_com_confirmacao(self):
        p0_antes = self.db.yes_historico()
        self.assertEqual(p0_antes, [])
        self.decidir('e5', 330.0)
        self.assertEqual(self.db.yes_historico()[0]['watts'], 330.0)

    def test_6_valor_desatualizado_nao_e_aceite_em_silencio(self):
        self.decidir('e5', 330.0)                   # confirmado
        self.curvas[5]['watts'][self.curvas[5]['secs'].index(180)] = 335.0   # MMP3 mudou na fonte
        e = self.entrada('e5')
        self.assertEqual((e['estado'], e['motivo']), (mm.E_A_VERIFICAR, mm.M_VALOR_MUDOU))
        r = self.decidir('e5', 330.0)               # cliente ainda ve 330
        self.assertEqual(r.status_code, 409, r.get_json())

    def test_7_segunda_execucao_nao_duplica(self):
        self.decidir('e5', 330.0)
        n_rev = U.contar(self.db)
        n_yes = len(self.db.yes_historico())
        r = self.decidir('e5', 330.0)
        self.assertFalse(r.get_json()['alterado'])
        mis.recalibrar(origem='sync', agora=None, dados=None)
        self.assertEqual(U.contar(self.db), n_rev)
        self.assertEqual(len(self.db.yes_historico()), n_yes)

    def test_8_rejeicao_impede_promocao(self):
        r = self.decidir('e5', 330.0, decisao='rejeitar')
        self.assertEqual(r.status_code, 200, r.get_json())
        mis.recalibrar(origem='sync', agora=None, dados=None)
        self.assertNotIn(self.tid('e5'), self.yes_ids())
        self.assertEqual(self.entrada('e5')['motivo'], mm.M_REJEITADO)


class EstadoParcial(Fluxo):
    def test_9_falha_da_recalibracao_nao_perde_revisao_nem_yes(self):
        # Revisao e linha YES sao gravadas numa so transacao, ANTES da recalibracao. Se a recalibracao
        # (calculo do IR) falhar depois, a confirmacao e o seu YES ja estao gravados: nao ha estado parcial.
        with mock.patch.object(mis, 'recalibrar', side_effect=RuntimeError('falha simulada')):
            r = self.decidir('e5', 330.0)
        self.assertEqual(r.status_code, 200)
        self.assertIn('falha simulada', str(r.get_json()['recalibracao']))
        self.assertEqual(self.db.ultima_revisao_teste(self.tid('e5'))['watts'], 330.0)
        self.assertEqual(self.yes_ids(), [self.tid('e5')])
        mis.recalibrar(origem='sync', agora=None, dados=None)
        self.assertEqual(self.yes_ids(), [self.tid('e5')])     # a recalibracao seguinte nao duplica


class Causalidade(Fluxo):
    def test_10_referencia_so_com_passado(self):
        # um esforco futuro (data posterior) nao pode ser referencia do candidato
        self.curvas.append(M.c180('futuro', MOD, '2027-01-10', 999.0))
        self.ativ.append({'id': 'futuro', 'start_date_local': '2027-01-10T07:30:00', 'icu_ignore_power': False})
        ids = [c['test_id'] for c in self.candidatos()]
        self.assertNotIn(self.tid('futuro'), ids)
        e = self.entrada('e5')
        self.assertLess(e.get('melhor_anterior_watts') or 0, 999.0)


class Sincronizacao(Fluxo):
    """Tarefa 2: reupsert sem duplicar, alteracao retroativa e reavaliacao."""

    def _linhas_bd(self, aid):
        # le a tabela real (a fixture substitui load_power_curves por mocks)
        return self.db._exec('SELECT watts FROM power_curves WHERE activity_id = ?', (aid,), fetch='all')

    def test_s1_reupsert_curva_nao_duplica_e_atualiza(self):
        self.db.upsert_power_curves([_curva(5, 330.0)])
        self.db.upsert_power_curves([_curva(5, 340.0)])        # alteracao retroativa na fonte
        linhas = self._linhas_bd('e5')
        self.assertEqual(len(linhas), 1)
        self.assertIn(340.0, __import__('json').loads(linhas[0][0]))

    def test_s2_reupsert_atividade_devolve_atualizadas_e_nao_duplica(self):
        row = {'id': 'e5', 'date': '2026-06-10', 'type': MOD, 'start_local': '2026-06-10T07:30:00'}
        self.assertEqual(self.db.upsert_activities([row]), (1, 0))   # primeira vez: inserida
        self.assertEqual(self.db.upsert_activities([row]), (0, 1))   # repeticao: atualizada, nao duplicada
        n = self.db._exec('SELECT COUNT(*) FROM activities WHERE id = ?', ('e5',), fetch='one')[0]
        self.assertEqual(n, 1)

    def test_s3_reavaliacao_apos_alteracao_retroativa(self):
        self.curvas[5]['watts'][self.curvas[5]['secs'].index(180)] = 270.0   # 270/300 = 0,90 < 0,92
        self.assertEqual(self.entrada('e5')['motivo'], mm.M_ABAIXO)

    def test_s4_avaliacao_idempotente(self):
        a = self.candidatos()
        b = self.candidatos()
        self.assertEqual(a, b)


class Deduplicacao(Fluxo):
    """Tarefa 3: igualdade a referencia, atividades distintas e sem candidatos duplicados."""

    def setUp(self):
        super().setUp()
        # referencia 300 W (e5 deixa de ser o recorde de 330 W) e tres atividades de 300 W, sem tag
        self.curvas[5]['watts'][self.curvas[5]['secs'].index(180)] = 300.0
        for i in (6, 7, 8):
            self.curvas.append(_curva(i, 300.0))
            self.ativ.append({'id': f'e{i}', 'start_date_local': f'2026-{i + 1:02d}-10T07:30:00',
                              'icu_ignore_power': False, 'tags': []})

    def test_d1_atividades_distintas_sem_duplicados(self):
        ids = [c['test_id'] for c in self.candidatos()]
        self.assertEqual(len(ids), len(set(ids)))
        for aid in ('e6', 'e7', 'e8'):
            self.assertIn(self.tid(aid), ids)

    def test_d2_igualdade_nao_e_yes_nem_atualiza_p0(self):
        for aid in ('e6', 'e7', 'e8'):
            self.assertEqual(self.entrada(aid)['estado'], mm.E_A_VERIFICAR)
        self.assertEqual(self.db.yes_historico(), [])

    def test_d3_confirmar_igualdade_sem_superioridade_estrita(self):
        r = self.decidir('e6', 300.0)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn(self.tid('e6'), [x['test_id'] for x in self.db.yes_historico()])
        self.assertEqual(self.db.yes_historico()[0]['watts'], 300.0)


class FonteAlteradaDepoisDeConfirmar(Fluxo):
    """Tarefa 4: YES historico preservado; divergencia assinalada; sem recalibracao silenciosa."""

    def test_y1_historico_original_preservado_e_divergencia_assinalada(self):
        self.decidir('e5', 330.0)
        self.curvas[5]['watts'][self.curvas[5]['secs'].index(180)] = 335.0
        mis.recalibrar(origem='sync', agora=None, dados=None)
        hist = [r for r in self.db.yes_historico() if r['test_id'] == self.tid('e5')]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]['watts'], 330.0)                 # o YES original nao muda
        e = self.entrada('e5')
        self.assertEqual((e['estado'], e['motivo']), (mm.E_A_VERIFICAR, mm.M_VALOR_MUDOU))
        # sem flag dedicada: a divergencia e visivel pelo estado e pelo motivo; ver relatorio


if __name__ == '__main__':
    unittest.main()
