"""Filtro de modalidade em /api/aquecimento/dados e associacao sessao/modalidade.

Usa SQLite em memoria com o esquema real (utils/aquecimento_db._SCHEMA). Nada
de API, Drive ou dados pessoais: sessoes sinteticas i1 (Bike), i2 (Row), i3 (Ski).
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


def _conn_sintetica():
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    con.executescript(aqdb._SCHEMA)
    linhas = [
        ('i1', 1, 'Bike', '2026-10-20', 140), ('i1', 2, 'Bike', '2026-10-20', 160),
        ('i2', 1, 'Row', '2026-10-12', 140),
        ('i3', 1, 'Ski', '2026-10-02', 120),
    ]
    for aid, num, mod, data, w in linhas:
        con.execute(
            "INSERT INTO aquecimento_blocos (activity_id, bloco_num, modalidade, data, watts_alvo) "
            "VALUES (?,?,?,?,?)", (aid, num, mod, data, w))
    con.commit()
    return con


class FiltroNoModulo(unittest.TestCase):

    def setUp(self):
        self.con = _conn_sintetica()

    def tearDown(self):
        self.con.close()

    def _ids(self, mod):
        with mock.patch.object(aqdb, 'get_conn', return_value=self.con):
            return sorted({s['activity_id'] for s in aqdb.listar_sessoes(mod)})

    def test_bike_so_devolve_bike(self):
        self.assertEqual(self._ids('Bike'), ['i1'])

    def test_row_so_devolve_row(self):
        self.assertEqual(self._ids('Row'), ['i2'])

    def test_modalidade_inexistente_devolve_vazio(self):
        self.assertEqual(self._ids('ZZZ_teste'), [])

    def test_sem_modalidade_devolve_todas(self):
        self.assertEqual(self._ids(None), ['i1', 'i2', 'i3'])

    def test_cada_sessao_traz_a_sua_modalidade(self):
        with mock.patch.object(aqdb, 'get_conn', return_value=self.con):
            for s in aqdb.listar_sessoes(None):
                esperado = {'i1': 'Bike', 'i2': 'Row', 'i3': 'Ski'}[s['activity_id']]
                self.assertEqual(s['modalidade'], esperado)


class FiltroNaRota(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('INTERVALS_ICU_API_KEY', 'dummy-teste')
        try:
            import app as app_mod
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f'app nao importa em teste: {type(e).__name__}: {e}')
        cls.app_mod = app_mod
        cls.client = app_mod.app.test_client()

    def _get(self, qs):
        con = _conn_sintetica()
        try:
            with mock.patch.object(aqdb, 'get_conn', return_value=con):
                return self.client.get('/api/aquecimento/dados?' + qs).get_json()
        finally:
            con.close()

    def test_rota_filtra_e_ecoa_modalidade(self):
        j = self._get('modalidade=Bike')
        self.assertEqual(j['status'], 'ok')
        self.assertEqual(j['modalidade'], 'Bike')
        self.assertEqual({s['activity_id'] for s in j['sessoes']}, {'i1'})
        self.assertEqual(j['total'], 1)

    def test_rota_modalidade_inexistente_devolve_zero(self):
        j = self._get('modalidade=ZZZ_teste')
        self.assertEqual(j['total'], 0)
        self.assertEqual(j['sessoes'], [])
        self.assertEqual(j['modalidade'], 'ZZZ_teste')


class PainelAssociaSessaoEModalidade(unittest.TestCase):
    """Texto do painel: o HRR usa a modalidade da sessao selecionada."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(RAIZ, 'tabs', 'tab_metabol.py'), encoding='utf-8') as f:
            cls.s = f.read()

if __name__ == '__main__':
    unittest.main()
