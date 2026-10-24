"""Etapa 14: sequencia de aplicacao das migracoes do IR (confianca, temporal, curvas), SQLite e PostgreSQL.

Nada aqui toca em producao. Cada teste usa base temporaria (SQLite) ou a base *_tmp do IR_TESTE_PG_DSN (PostgreSQL).
Verifica: idempotencia com checksum, preservacao de linhas, indices, ausencia de SQL destrutivo, ficheiros exportados
iguais ao codigo, e comportamento de leitura antes de as tabelas existirem.
"""
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

import test_modelo_ir_servico as TS  # noqa: E402
import db  # noqa: E402
import modelo_ir_confianca_migracao as mcon  # noqa: E402
import modelo_ir_temporal as tmp  # noqa: E402
import modelo_ir_temporal_migracao as mtmp  # noqa: E402
import modelo_ir_curvas as mcurv  # noqa: E402
import modelo_ir_curvas_migracao as mcmig  # noqa: E402

PG_DSN = os.environ.get('IR_TESTE_PG_DSN')
AGORA = '2026-10-23T10:00:00'
# ordem recomendada (revisao humana). Cada migracao cria o registo se precisar: a ordem nao e funcional,
# mas e a ordem em que se revisa e aplica.
SEQUENCIA = [mcon, mtmp, mcmig]
TABELAS = (mcon.REGISTO, 'modelo_ir_confianca_tentativas', tmp.TAB_MARCOS, tmp.TAB_YES, mcurv.TAB)
DESTRUTIVO = re.compile(r'\b(DROP|ALTER|UPDATE|DELETE|TRUNCATE)\b', re.IGNORECASE)
MIGRACOES_DIR = os.path.join(ROOT, 'migracoes')


def setUpModule():
    TS.setUpModule()


def tearDownModule():
    TS.tearDownModule()


def aplicar_sequencia(executor, dialeto, agora=AGORA):
    return [m.aplicar(executor, dialeto, agora) for m in SEQUENCIA]


def tabelas_sqlite(executor):
    rows = executor("SELECT name FROM sqlite_master WHERE type = 'table'", (), 'all') or []
    return {r[0] for r in rows}


def indices_sqlite(executor):
    rows = executor("SELECT name FROM sqlite_master WHERE type = 'index'", (), 'all') or []
    return {r[0] for r in rows}


class SequenciaSqlite(TS._Base):

    def test_aplicacao_completa_e_idempotente(self):
        self.assertEqual(aplicar_sequencia(db._exec, db.DRIVER), ['aplicada'] * 3)
        self.assertEqual(aplicar_sequencia(db._exec, db.DRIVER), ['ja_aplicada'] * 3)
        for t in TABELAS:
            self.assertIn(t, tabelas_sqlite(db._exec), t)

    def test_indices_do_historico_e_do_temporal_existem(self):
        aplicar_sequencia(db._exec, db.DRIVER)
        ix = indices_sqlite(db._exec)
        self.assertIn('ix_ir_yes_trans_test', ix)
        self.assertIn(mcurv.IX_TIPO, ix)                       # ix_ir_curvas_conhecido
        # marcos: chave primaria em conhecido_em (indice implicito) -> a consulta marcos_conhecidos_em e coberta

    def test_linhas_existentes_sobrevivem_a_reaplicacao(self):
        aplicar_sequencia(db._exec, db.DRIVER)
        hc = mcurv.HistoricoCurvas(db._exec, db.DRIVER)
        hc.registar([{'activity_id': 'a1', 'type': 'Bike', 'date': '2025-12-01', 'weight': 70,
                      'secs': [60], 'watts': [400]}], '2026-01-10T10:00:00')
        aplicar_sequencia(db._exec, db.DRIVER, '2026-10-23T12:00:00')
        self.assertEqual(len(hc.versoes('a1')), 1)

    def test_checksum_alterado_numa_migracao_levanta_sem_tocar_nas_outras(self):
        aplicar_sequencia(db._exec, db.DRIVER)
        db._exec("UPDATE schema_migracoes_ir SET checksum = 'alterado' WHERE id = ?", (mtmp.MIGRACAO_ID,), None)
        with self.assertRaises(mtmp.MigracaoAlterada):
            mtmp.aplicar(db._exec, db.DRIVER, AGORA)
        self.assertEqual(mcmig.aplicar(db._exec, db.DRIVER, AGORA), 'ja_aplicada')

    def test_ordem_invertida_chega_ao_mesmo_esquema(self):
        for m in reversed(SEQUENCIA):
            m.aplicar(db._exec, db.DRIVER, AGORA)
        for t in TABELAS:
            self.assertIn(t, tabelas_sqlite(db._exec), t)

    def test_antes_da_migracao_os_leitores_nao_escrevem_nem_levantam(self):
        self.assertEqual(mcmig.estado(db._exec, db.DRIVER), 'nao_aplicada')
        self.assertEqual(mtmp.estado(db._exec, db.DRIVER), 'nao_aplicada')
        linha = {'activity_id': 'x', 'type': 'Bike', 'date': '2025-01-01', 'weight': None, 'secs': [60], 'watts': [1]}
        self.assertEqual(mcurv.registar_seguro([linha], datetime(2026, 1, 1)), {'estado': 'tabelas_ausentes'})
        self.assertNotIn(mcurv.TAB, tabelas_sqlite(db._exec))  # nada foi criado por ler

    def test_ficheiros_exportados_correspondem_ao_codigo(self):
        for m, nome in ((mcon, '2026-10-23_ir_confianca_001_tentativas.sql'),
                        (mtmp, '2026-10-23_ir_temporal_001_marcos_yes.sql'),
                        (mcmig, '2026-10-23_ir_curvas_001_versoes.sql')):
            caminho = os.path.join(MIGRACOES_DIR, nome)
            self.assertTrue(os.path.exists(caminho), caminho)
            with open(caminho, encoding='utf-8') as f:
                cab = f.readline()
            self.assertIn(m.checksum()[:16], cab, f'{nome} desatualizado: reexportar antes de rever')


class SemSqlDestrutivo(unittest.TestCase):

    def test_sql_exportado_so_tem_criacao_idempotente(self):
        for m in SEQUENCIA:
            with tempfile.TemporaryDirectory() as d:
                caminho = m.exportar_sql(os.path.join(d, 'm.sql'))
                with open(caminho, encoding='utf-8') as f:
                    corpo = '\n'.join(l for l in f if not l.startswith('--'))
            self.assertIsNone(DESTRUTIVO.search(corpo), m.MIGRACAO_ID)
            self.assertIn('IF NOT EXISTS', corpo)


@unittest.skipUnless(PG_DSN and re.search(r'dbname=\S*_tmp\b', PG_DSN),
                     'IR_TESTE_PG_DSN com base *_tmp nao definido: PostgreSQL nao testado')
class SequenciaPostgres(unittest.TestCase):
    """Mesma sequencia em PostgreSQL (psycopg v3, autocommit). Base *_tmp; tabelas e registos de migracao apagados."""

    def setUp(self):
        from test_ir_migracao import _executor_psycopg3
        self.loja = _executor_psycopg3(PG_DSN)
        for t in TABELAS:
            self.loja._exec(f'DROP TABLE IF EXISTS {t}', (), None)
        self.loja._exec(mcon.DDL_REGISTO, (), None)   # o registo tem de existir antes de se apagar a linha de teste
        for mid in (mcon.MIGRACAO_ID, mtmp.MIGRACAO_ID, mcmig.MIGRACAO_ID):
            self.loja._exec('DELETE FROM schema_migracoes_ir WHERE id = %s', (mid,), None)

    def _tabelas_pg(self):
        rows = self.loja._exec("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()",
                               (), 'all') or []
        return {r[0] for r in rows}

    def test_sequencia_idempotente_e_com_indices(self):
        self.assertEqual([m.aplicar(self.loja._exec, self.loja.dialeto, AGORA) for m in SEQUENCIA], ['aplicada'] * 3)
        self.assertEqual([m.aplicar(self.loja._exec, self.loja.dialeto, AGORA) for m in SEQUENCIA], ['ja_aplicada'] * 3)
        for t in TABELAS:
            self.assertIn(t, self._tabelas_pg(), t)
        ix = {r[0] for r in (self.loja._exec("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()",
                                             (), 'all') or [])}
        self.assertIn(mcurv.IX_TIPO, ix)
        self.assertIn('ix_ir_yes_trans_test', ix)

    def test_historico_de_curvas_em_postgres_preserva_versoes(self):
        for m in SEQUENCIA:
            m.aplicar(self.loja._exec, self.loja.dialeto, AGORA)
        hc = mcurv.HistoricoCurvas(self.loja._exec, self.loja.dialeto)
        c = {'activity_id': 'p1', 'type': 'Bike', 'date': '2025-12-01', 'weight': 70,
             'secs': [60, 300], 'watts': [400, 320]}
        hc.registar([c], '2026-01-10T10:00:00')
        hc.registar([dict(c, watts=[410, 320])], '2026-03-10T10:00:00')
        for m in SEQUENCIA:
            m.aplicar(self.loja._exec, self.loja.dialeto, AGORA)
        self.assertEqual(len(hc.versoes('p1')), 2)
        self.assertEqual(hc.curvas_conhecidas_antes('2026-02-01')[0]['watts'], [400, 320])


if __name__ == '__main__':
    unittest.main()
