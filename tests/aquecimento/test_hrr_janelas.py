"""HRR a partir dos instantes dos WORK aceites (sem voltar a detetar a escada).

SINTETICO: stream construido aqui, SQLite em memoria com o esquema real.
Nao consulta a API nem o Drive.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
RAIZ = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(RAIZ, 'utils'))
sys.path.insert(0, RAIZ)

import aquecimento_db as aqdb  # noqa: E402
import aquecimento_hrr as hrr  # noqa: E402
import aquecimento_streams as aqs  # noqa: E402

PROTO = {'Bike': {'watts': [80, 100, 120, 140, 160], 'tol': 12, 'min_blocos': 4}}
ALVOS = [80, 100, 120, 140, 160]


def _stream(passos):
    """passos: [(alvo_w, dur_work_s, dur_rec_s)] -> streams 1 Hz com FC.
    FC sobe no WORK e desce no descanso, para o HRR ter forma real."""
    t, w, h = [], [], []
    seg = {}
    s = 0
    for k, (alvo, dw, dr) in enumerate(passos):
        ini = s
        for _ in range(dw):
            t.append(float(s)); w.append(float(alvo)); h.append(120.0 + 10 * k + (s - ini) * 0.05)
            s += 1
        fim_work = s - 1
        fc_fim = h[-1]
        for j in range(dr):
            t.append(float(s)); w.append(60.0); h.append(fc_fim - (j + 1) * 0.4)
            s += 1
        seg[k] = (float(ini), float(fim_work), alvo)
    return {'time': t, 'watts': w, 'heartrate': h}, [seg[k] for k in range(len(passos))]


class Janelas(unittest.TestCase):

    def test_indices_das_janelas(self):
        t = [float(x) for x in range(0, 100)]
        ach = hrr._achados_das_janelas(t, [(10.0, 20.0, 80), (50.0, 50.5, 100), (200.0, 210.0, 120)])
        # so a primeira tem 2+ amostras dentro do stream; a de 50.5 s tem 0-1 amostras
        self.assertEqual(ach, [(10, 21, 80)])

    def test_janela_sem_tempos_e_ignorada(self):
        t = [float(x) for x in range(0, 50)]
        self.assertEqual(hrr._achados_das_janelas(t, [(None, None, 80)]), [])

    def test_hrr_usa_as_janelas_e_nao_volta_a_detetar(self):
        passos = [(a, 300, 60) for a in ALVOS]
        streams, janelas = _stream(passos)
        # alvos errados de proposito: a deteccao nao acharia isto, as janelas sim
        janelas_erradas = [(j[0], j[1], a + 5) for j, a in zip(janelas, ALVOS)]
        with mock.patch.object(aqs, 'detectar_escada', side_effect=AssertionError('nao devia detetar')):
            r = hrr.hrr_por_degrau(streams, 'Bike', PROTO, janelas=janelas_erradas)
        self.assertEqual(r['status'], 'ok', r.get('status'))
        self.assertEqual(len(r['degraus']), 5)
        self.assertEqual(r['degraus'][0]['work']['t_inicio_s'], janelas[0][0])
        # FC no fim do WORK e na recuperacao de 1 min: valores fisiologicos, nao negativos
        d0 = r['degraus'][0]
        self.assertIsNotNone(d0['hr_end']['valor'])
        self.assertGreater(d0['fc_media_rec_1min']['valor'], 60)
        self.assertLess(d0['fc_media_rec_1min']['valor'], d0['hr_end']['valor'])

    def test_janelas_fora_do_stream_nao_inventam_degraus(self):
        streams, _ = _stream([(80, 300, 60)])
        r = hrr.hrr_por_degrau(streams, 'Bike', PROTO, janelas=[(9000.0, 9100.0, 80)])
        self.assertEqual(r['status'], 'janelas_fora_do_stream')
        self.assertEqual(r['degraus'], [])

    def test_montar_guarda_o_instante_do_work(self):
        t = [float(x) for x in range(0, 400)]
        w = [80.0] * 400
        streams = {'time': t, 'watts': w}
        r = aqs._montar(streams, w, [(20, 320, 80)], 'Bike', PROTO['Bike'], dt=1.0)
        b = r['blocos'][0]
        self.assertEqual(b['t_inicio_s'], 20.0)
        self.assertEqual(b['t_fim_s'], 319.0)


class BaseDeDados(unittest.TestCase):

    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        self.con.executescript(aqdb._SCHEMA)
        self.p = mock.patch.object(aqdb, 'get_conn', return_value=self.con)
        self.q = mock.patch.object(aqdb, 'sincronizar', return_value=True)
        self.p.start(); self.q.start()

    def tearDown(self):
        self.p.stop(); self.q.stop(); self.con.close()

    def test_guarda_e_le_as_janelas(self):
        blocos = [{'bloco_num': 1, 'watts_alvo': 80, 'watts_real': 80.0, 'tempo_seg': 300,
                   't_inicio_s': 20.0, 't_fim_s': 320.0},
                  {'bloco_num': 2, 'watts_alvo': 100, 'watts_real': 100.0, 'tempo_seg': 300,
                   't_inicio_s': 400.0, 't_fim_s': 700.0}]
        aqdb.salvar_blocos('i1', 'Bike', '2026-10-20', blocos, sync=False)
        self.assertEqual(aqdb.janelas_da_sessao('i1'), [(20.0, 320.0, 80), (400.0, 700.0, 100)])
        self.assertEqual(aqdb.contar_blocos('i1'), 2)

    def test_sem_instantes_devolve_none(self):
        self.con.execute("INSERT INTO aquecimento_blocos (activity_id,bloco_num,modalidade,data,watts_alvo) "
                         "VALUES ('i2',1,'Bike','2026-10-20',80)")
        self.con.commit()
        self.assertIsNone(aqdb.janelas_da_sessao('i2'))

    def test_preenche_instantes_sem_apagar_os_blocos(self):
        self.con.execute("INSERT INTO aquecimento_blocos (activity_id,bloco_num,modalidade,data,watts_alvo,tempo_seg) "
                         "VALUES ('i3',1,'Bike','2026-10-20',80,300)")
        self.con.commit()
        aqdb.definir_tempos_blocos('i3', [{'bloco_num': 1, 't_inicio_s': 5.0, 't_fim_s': 305.0}])
        self.assertEqual(aqdb.janelas_da_sessao('i3'), [(5.0, 305.0, 80)])
        self.assertEqual(aqdb.contar_blocos('i3'), 1)

    def test_migracao_acrescenta_colunas_a_base_antiga(self):
        con = sqlite3.connect(':memory:')
        con.execute("CREATE TABLE aquecimento_blocos (activity_id TEXT, bloco_num INTEGER, "
                    "watts_alvo INTEGER, PRIMARY KEY (activity_id, bloco_num))")
        aqdb._garantir_colunas_tempo(con)
        cols = {r[1] for r in con.execute("PRAGMA table_info(aquecimento_blocos)")}
        self.assertIn('t_inicio_s', cols)
        self.assertIn('t_fim_s', cols)
        con.close()


class RotaSemJanelas(unittest.TestCase):

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
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        self.con.executescript(aqdb._SCHEMA)
        self.con.execute("INSERT INTO aquecimento_blocos (activity_id,bloco_num,modalidade,data,watts_alvo) "
                         "VALUES ('i9',1,'Bike','2026-10-20',80)")
        self.con.commit()
        self.patches = [
            mock.patch.object(aqdb, 'get_conn', return_value=self.con),
            mock.patch.object(aqdb, 'sincronizar', return_value=True),
            mock.patch.object(self.app_mod, '_duracao_atividade', return_value=None),
            # sem copia reduzida nao ha onde ler os instantes dos WORK
            mock.patch('db.get_streams', return_value=(None, None)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.con.close()

    def test_sem_instantes_nem_copia_regista_sem_janelas_e_nao_chama_a_api(self):
        with mock.patch('api_client.icu_get', side_effect=AssertionError('nao devia chamar a API')):
            r = self.client.get('/api/aquecimento/hrr/reprocessar?modalidade=Bike&max_api=25')
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        j = r.get_json()
        self.assertEqual(j['estados'].get('sem_janelas'), 1)
        self.assertEqual(j['pendentes'], 0)
        st = dict(self.con.execute('SELECT activity_id, status FROM aquecimento_hrr_sessoes').fetchall())
        self.assertEqual(st.get('i9'), 'sem_janelas')


if __name__ == '__main__':
    unittest.main()
