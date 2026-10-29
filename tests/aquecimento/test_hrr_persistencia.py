"""HRR guardado por sessao: conversao, base de dados, comparativo e reprocessamento.

SINTETICO: API simulada (mock de api_client.icu_get), SQLite em memoria com o
esquema real. Nao consulta a API nem o Drive, e nao altera a base de producao.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
RAIZ = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(RAIZ, 'utils'))
sys.path.insert(0, RAIZ)

import aquecimento_db as aqdb  # noqa: E402
import aquecimento_hrr as hrr  # noqa: E402
import aquecimento_comparativo as cmp  # noqa: E402
from test_hrr_unit import sessao  # noqa: E402
from test_hrr_rota_painel import PROTO_BIKE, _streams_api  # noqa: E402


def _con():
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    con.executescript(aqdb._SCHEMA)
    return con


def _r_ok(n=1, hrr60=30.0, fc1=140.0):
    return {'status': 'ok', 'degraus': [{
        'n': n, 'alvo_w': 160, 'watts_work_medio': 158.0,
        'hr_end': {'valor': 150.0, 'motivo': None},
        'hr_60': {'valor': 120.0, 'motivo': None},
        'hr_120': {'valor': None, 'motivo': 'janela_vazia'},
        'hrr60': hrr60, 'hrr60_motivo': None,
        'hrr120': None, 'hrr120_motivo': 'janela_vazia',
        'fc_media_rec_1min': {'valor': fc1, 'motivo': None},
    }]}


class Conversao(unittest.TestCase):

    def test_ok_mantem_motivos_e_nao_inventa_valores(self):
        s, d = hrr.registo_para_bd('i1', 'Bike', '2026-10-20', _r_ok())
        self.assertEqual(s['status'], 'ok')
        self.assertEqual(s['n_degraus'], 1)
        self.assertEqual(len(d), 1)
        self.assertIsNone(d[0]['hr_120'])
        self.assertEqual(d[0]['hr_120_motivo'], 'janela_vazia')
        self.assertEqual(d[0]['hrr60'], 30.0)

    def test_sem_hr_guarda_so_o_status_e_os_nomes(self):
        r = {'status': 'sem_hr', 'tipos_recebidos': ['watts', 'time']}
        s, d = hrr.registo_para_bd('i2', 'Row', '2026-10-12', r)
        self.assertEqual(s['status'], 'sem_hr')
        self.assertEqual(s['detalhe'], 'watts,time')
        self.assertEqual(d, [])

    def test_erro_de_sessao_fica_registado_com_o_estado(self):
        s, d = hrr.registo_para_bd('i3', 'Bike', None, {'status': 'sem_watts'})
        self.assertEqual((s['status'], s['n_degraus'], d), ('sem_watts', 0, []))


class BaseDeDados(unittest.TestCase):

    def setUp(self):
        self.con = _con()
        self.p = mock.patch.object(aqdb, 'get_conn', return_value=self.con)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.con.close()

    def _guardar(self, aid, r, versao=hrr.VERSAO_HRR, mod='Bike'):
        s, d = hrr.registo_para_bd(aid, mod, '2026-10-20', r)
        return aqdb.salvar_hrr(s, d, versao)

    def test_processada_so_com_a_versao_atual(self):
        self._guardar('i1', _r_ok())
        self.assertTrue(aqdb.hrr_processada('i1', hrr.VERSAO_HRR))
        self.assertFalse(aqdb.hrr_processada('i1', hrr.VERSAO_HRR + 1))
        self.assertFalse(aqdb.hrr_processada('naoexiste', hrr.VERSAO_HRR))

    def test_gravar_duas_vezes_substitui_nao_duplica(self):
        self._guardar('i1', _r_ok(hrr60=30.0))
        self._guardar('i1', _r_ok(hrr60=28.0))
        n = self.con.execute('SELECT COUNT(*) FROM aquecimento_hrr').fetchone()[0]
        self.assertEqual(n, 1)
        self.assertEqual(aqdb.listar_hrr('Bike', hrr.VERSAO_HRR)[0]['hrr60_avg'], 28.0)

    def test_listar_exclui_sem_hr_e_outra_versao(self):
        self._guardar('i1', _r_ok())
        self._guardar('i2', {'status': 'sem_hr', 'tipos_recebidos': []})
        self._guardar('i3', _r_ok(), versao=hrr.VERSAO_HRR - 1 if hrr.VERSAO_HRR > 1 else 0)
        ids = {x['activity_id'] for x in aqdb.listar_hrr('Bike', hrr.VERSAO_HRR)}
        self.assertEqual(ids, {'i1'})

    def test_aceites_vem_dos_blocos(self):
        self.con.execute("INSERT INTO aquecimento_blocos (activity_id,bloco_num,modalidade,data,watts_alvo) "
                         "VALUES ('i9',1,'Bike','2026-10-01',80)")
        self.con.execute("INSERT INTO aquecimento_blocos (activity_id,bloco_num,modalidade,data,watts_alvo) "
                         "VALUES ('i8',1,'Row','2026-10-01',80)")
        self.assertEqual([a for a, _ in aqdb.aceites_para_hrr('Bike')], ['i9'])


class ComparativoHRR(unittest.TestCase):

    def test_hrr60_agrega_por_mes_e_escalao(self):
        linhas = [
            {'activity_id': 'a', 'data': '2026-10-02', 'bloco_num': 1, 'interval_num': 1,
             'watts_alvo': 160, 'watts_real': None, 'hrr60_avg': 30.0},
            {'activity_id': 'b', 'data': '2026-10-09', 'bloco_num': 1, 'interval_num': 1,
             'watts_alvo': 160, 'watts_real': None, 'hrr60_avg': 26.0},
            {'activity_id': 'c', 'data': '2026-10-15', 'bloco_num': 1, 'interval_num': 1,
             'watts_alvo': 160, 'watts_real': None, 'hrr60_avg': None},
        ]
        import datetime as dt
        res = cmp.comparativo(linhas, 'Bike', 'hrr60', hoje=dt.date(2026, 10, 29),
                              dias=60, agregacao='mes', min_n=2)
        self.assertEqual(res['status'], 'ok')
        self.assertEqual(res['sessoes']['incluidas'], 2)
        self.assertEqual(res['vista2']['periodos'][0]['escaloes'][0]['media'], 28.0)

    def test_metricas_hrr_conhecidas(self):
        for m in cmp.METRICAS_HRR:
            self.assertIn(m, cmp.METRICAS)


class ReprocessarRota(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('INTERVALS_ICU_API_KEY', 'dummy-teste')
        try:
            import app as app_mod
        except Exception as e:                      # pragma: no cover
            raise unittest.SkipTest(f'app nao importa em teste: {type(e).__name__}: {e}')
        cls.app_mod = app_mod
        cls.client = app_mod.app.test_client()

    def setUp(self):
        self.con = _con()
        for aid, mod, data in [('i1', 'Bike', '2026-10-20'), ('i2', 'Bike', '2026-10-21'),
                               ('i3', 'Bike', '2026-10-22')]:
            self.con.execute("INSERT INTO aquecimento_blocos (activity_id,bloco_num,modalidade,data,watts_alvo) "
                             "VALUES (?,1,?,?,80)", (aid, mod, data))
        self.con.commit()
        t, w, h, _ = sessao(PROTO_BIKE, rec_s=(150,) * 5, decl=(0.5,) * 5)
        self.com_fc = _streams_api(t, w, h)
        self.sem_fc = [{'type': 'time', 'data': t}, {'type': 'watts', 'data': w}]
        self.patches = [
            mock.patch.object(aqdb, 'get_conn', return_value=self.con),
            mock.patch.object(aqdb, 'sincronizar', return_value=True),
            mock.patch.object(self.app_mod, '_duracao_atividade', return_value=None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.con.close()

    def _run(self, respostas, qs='modalidade=Bike&max_api=25'):
        def icu(path, params=None, timeout=60):
            aid = path.split('/')[2]
            return respostas[aid]
        with mock.patch('api_client.icu_get', side_effect=icu):
            r = self.client.get('/api/aquecimento/hrr/reprocessar?' + qs)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        return r.get_json()

    def test_processa_sem_fc_e_com_erro_sem_inventar(self):
        j = self._run({'i1': (self.com_fc, None),
                       'i2': (self.sem_fc, None),
                       'i3': (None, 'sem acesso')})
        self.assertEqual(j['processadas_nesta_chamada'], 2)
        self.assertEqual(j['erros_api'], 1)
        self.assertEqual(j['estados'].get('sem_hr'), 1)
        self.assertEqual(j['pendentes'], 1)
        st = dict(self.con.execute('SELECT activity_id, status FROM aquecimento_hrr_sessoes').fetchall())
        self.assertEqual(st.get('i2'), 'sem_hr')
        self.assertNotIn('i3', st)

    def test_segunda_chamada_salta_as_ja_feitas(self):
        self._run({'i1': (self.com_fc, None), 'i2': (self.sem_fc, None), 'i3': (None, 'x')})
        j = self._run({'i1': (self.com_fc, None), 'i2': (self.sem_fc, None), 'i3': (None, 'x')})
        self.assertEqual(j['ja_processadas'], 2)
        self.assertEqual(j['processadas_nesta_chamada'], 0)

    def test_max_api_limita_os_pedidos(self):
        j = self._run({'i1': (self.com_fc, None), 'i2': (self.sem_fc, None), 'i3': (self.sem_fc, None)},
                      qs='modalidade=Bike&max_api=1')
        self.assertEqual(j['processadas_nesta_chamada'], 1)
        self.assertEqual(j['pendentes'], 2)

    def test_sem_degraus_devolve_diagnostico_com_o_id(self):
        # sessao aceite (tem blocos) cujo stream nao tem escada: watts constantes
        self.con.execute("INSERT INTO aquecimento_blocos (activity_id,bloco_num,modalidade,data,watts_alvo) "
                         "VALUES ('i4',1,'Bike','2026-10-23',80)")
        self.con.commit()
        t = self.com_fc[0]['data']
        plano = [{'type': 'time', 'data': t},
                 {'type': 'watts', 'data': [100] * len(t)},
                 {'type': 'heartrate', 'data': [120] * len(t)}]
        j = self._run({'i1': (self.com_fc, None), 'i2': (self.sem_fc, None),
                       'i3': (None, 'x'), 'i4': (plano, None)})
        self.assertEqual(j['estados'].get('sem_degraus'), 1)
        diag = j['diagnostico_sem_degraus']
        self.assertEqual(len(diag), 1)
        self.assertEqual(diag[0]['activity_id'], 'i4')
        self.assertIn(diag[0]['veredicto'], ('aceite', 'rejeitada'))
        self.assertIsInstance(diag[0]['passos'], list)

    def test_sem_modalidade_400(self):
        self.assertEqual(self.client.get('/api/aquecimento/hrr/reprocessar').status_code, 400)


if __name__ == '__main__':
    unittest.main()
