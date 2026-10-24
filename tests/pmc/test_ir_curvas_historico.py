"""Etapa 13: historico verificavel das curvas (ir_curvas_versoes), migracao preparada e y conhecido do alvo.

TODOS os dados sao SINTETICOS. Os instantes de conhecimento sao RELOGIO INJETADO nos testes (nao sao datas de
producao). Cobre: mesmo conteudo nao grava; conteudo alterado grava nova versao e preserva a anterior; reconstrucao
so com versoes conhecidas antes do alvo; alteracao futura nao altera o passado; curvas sem versao anterior ao
alvo ficam inelegiveis; migracao idempotente com checksum; SQL exportado sem DDL destrutivo; sem migracao nada
e gravado e nada levanta; escrita so na propria tabela; y do alvo = versao conhecida ate ao dia do esforco.
SQLite sempre. PostgreSQL (psycopg v3, IR_TESTE_PG_DSN com base *_tmp): mesma historia, sem dados de producao.
"""
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

import test_modelo_ir_servico as TS  # noqa: E402
import db  # noqa: E402
import modelo_ir_curvas as mcurv  # noqa: E402
import modelo_ir_curvas_migracao as mcmig  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_observacoes_auto as moa  # noqa: E402

PG_DSN = os.environ.get('IR_TESTE_PG_DSN')
T0 = '2026-01-10T10:00:00'     # relogio injetado: primeira sincronizacao observada
T1 = '2026-03-10T10:00:00'     # relogio injetado: alteracao observada depois
CURVA = {'activity_id': 'a1', 'type': 'Bike', 'date': '2025-12-01', 'weight': 70,
         'secs': [60, 300, 1200], 'watts': [400, 320, 280]}


def setUpModule():
    TS.setUpModule()


def tearDownModule():
    TS.tearDownModule()


def com_watts(c, watts):
    return dict(c, watts=list(watts))


class _ComMigracao(TS._Base):
    """Base SQLite de teste com a migracao de curvas aplicada."""

    def setUp(self):
        super().setUp()
        self.assertEqual(mcmig.aplicar(db._exec, db.DRIVER, '2026-10-23T10:00:00'), 'aplicada')
        self.hc = mcurv.HistoricoCurvas(db._exec, db.DRIVER)


class RegistoDeVersoes(_ComMigracao):

    def test_mesmo_conteudo_nao_grava_nova_versao(self):
        self.assertEqual(self.hc.registar([CURVA], T0), {'inseridas': 1, 'inalteradas': 0})
        self.assertEqual(self.hc.registar([CURVA], T1), {'inseridas': 0, 'inalteradas': 1})
        self.assertEqual(len(self.hc.versoes('a1')), 1)

    def test_conteudo_alterado_grava_nova_versao_e_preserva_a_anterior(self):
        self.hc.registar([CURVA], T0)
        antiga = self.hc.versoes('a1')[0]
        self.assertEqual(self.hc.registar([com_watts(CURVA, [410, 320, 280])], T1)['inseridas'], 1)
        v = self.hc.versoes('a1')
        self.assertEqual(len(v), 2)
        self.assertEqual(v[0], antiga)                       # versao antiga intacta
        self.assertEqual(v[1]['watts'], [410.0, 320.0, 280.0])

    def test_tipos_numericos_equivalentes_nao_contam_como_alteracao(self):
        self.hc.registar([CURVA], T0)
        equivalente = dict(CURVA, weight=70.0, watts=[400.0, 320.0, 280.0], secs=[60.0, 300.0, 1200.0])
        self.assertEqual(self.hc.registar([equivalente], T1)['inalteradas'], 1)
        self.assertEqual(len(self.hc.versoes('a1')), 1)

    def test_instante_repetido_nao_rebenta_nem_duplica(self):
        self.hc.registar([CURVA], T0)
        self.hc.registar([com_watts(CURVA, [1, 2, 3])], T0)  # mesma chave (activity_id, conhecido_em)
        self.assertEqual(len(self.hc.versoes('a1')), 1)

    def test_escrita_so_na_propria_tabela(self):
        with mock.patch.object(self.hc, '_exec', wraps=self.hc._exec) as spy:   # o executor e guardado no construtor
            self.hc.registar([CURVA], T0)
        escritas = [c.args[0] for c in spy.call_args_list
                    if c.args and str(c.args[0]).lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
        self.assertTrue(escritas)
        for sql in escritas:
            self.assertIn(mcurv.TAB, sql)
            self.assertNotIn('power_curves', sql)


class ReconstrucaoAntesDoAlvo(_ComMigracao):

    def test_alvo_ve_so_a_versao_conhecida_antes_do_inicio_do_dia(self):
        self.hc.registar([CURVA], T0)
        self.hc.registar([com_watts(CURVA, [999, 320, 280])], T1)
        antes = self.hc.curvas_conhecidas_antes('2026-02-01')
        depois = self.hc.curvas_conhecidas_antes('2026-04-01')
        self.assertEqual(antes[0]['watts'], [400, 320, 280])
        self.assertEqual(depois[0]['watts'], [999, 320, 280])

    def test_alteracao_futura_nao_altera_a_reconstrucao_passada(self):
        self.hc.registar([CURVA], T0)
        passado = self.hc.curvas_para_alvo('2026-02-01')
        self.hc.registar([com_watts(CURVA, [999, 1, 1])], T1)     # alteracao posterior ao alvo
        self.assertEqual(self.hc.curvas_para_alvo('2026-02-01'), passado)

    def test_curvas_para_alvo_sem_prova_antes_do_primeiro_instante(self):
        self.hc.registar([CURVA], T0)
        self.assertIsNone(self.hc.curvas_para_alvo('2026-01-10'))  # o proprio dia da primeira versao: nao provado
        self.assertIsNone(self.hc.curvas_para_alvo('2026-01-05'))
        self.assertEqual(len(self.hc.curvas_para_alvo('2026-01-11')), 1)

    def test_curva_cuja_primeira_versao_e_posterior_ao_alvo_nao_entra(self):
        self.hc.registar([CURVA], T0)
        novo = dict(CURVA, activity_id='a2', date='2025-12-02', watts=[500, 300, 200])
        self.hc.registar([novo], T1)                                # a2 so conhecida em T1
        conhecidas = self.hc.curvas_conhecidas_antes('2026-02-01')
        self.assertEqual({c['activity_id'] for c in conhecidas}, {'a1'})

    def test_sem_historico_nenhuma_curva_e_provada(self):
        self.assertIsNone(self.hc.curvas_para_alvo('2026-02-01'))
        self.assertIsNone(self.hc.inicio_historico())

    def test_forma_identica_a_load_power_curves(self):
        self.hc.registar([CURVA], T0)
        (c,) = self.hc.curvas_conhecidas_antes('2026-02-01')
        self.assertEqual(set(c), {'activity_id', 'type', 'date', 'weight', 'secs', 'watts'})
        self.assertEqual(c['date'], '2025-12-01')


class MigracaoDeCurvas(_ComMigracao):

    def test_aplicar_e_idempotente(self):
        self.assertEqual(mcmig.aplicar(db._exec, db.DRIVER, '2026-10-23T11:00:00'), 'ja_aplicada')
        self.assertEqual(mcmig.estado(db._exec, db.DRIVER), 'aplicada')

    def test_checksum_diferente_levanta_sem_alterar_nada(self):
        db._exec("UPDATE schema_migracoes_ir SET checksum = 'outro' WHERE id = ?", (mcmig.MIGRACAO_ID,), None)
        with self.assertRaises(mcmig.MigracaoAlterada):
            mcmig.aplicar(db._exec, db.DRIVER, '2026-10-23T11:00:00')
        self.assertEqual(db._exec("SELECT checksum FROM schema_migracoes_ir WHERE id = ?",
                                  (mcmig.MIGRACAO_ID,), 'one')[0], 'outro')

    def test_sql_exportado_e_aditivo(self):
        with tempfile.TemporaryDirectory() as d:
            caminho = mcmig.exportar_sql(os.path.join(d, 'curvas.sql'))
            with open(caminho, encoding='utf-8') as f:
                sql = f.read()
        self.assertIn(f'CREATE TABLE IF NOT EXISTS {mcurv.TAB}', sql)
        self.assertIn('PRIMARY KEY (activity_id, conhecido_em)', sql)
        self.assertIn(f'CREATE INDEX IF NOT EXISTS {mcurv.IX_TIPO}', sql)
        for proibido in (r'\bDROP\b', r'\bALTER\b', r'\bUPDATE\b', r'\bDELETE\b', r'\bTRUNCATE\b'):
            self.assertIsNone(re.search(proibido, sql, re.IGNORECASE), proibido)

    def test_checksum_muda_se_o_ddl_mudar(self):
        antes = mcmig.checksum()
        with mock.patch.object(mcurv, 'DDL', mcurv.DDL + ' '):
            self.assertNotEqual(mcmig.checksum(), antes)


class SemMigracaoNadaEGravado(TS._Base):

    def test_leitor_reporta_tabelas_ausentes_e_registo_nao_levanta(self):
        leitor, estado = mcurv.leitor_producao()
        self.assertIsNone(leitor)
        self.assertEqual(estado, 'tabelas_ausentes')
        self.assertEqual(mcurv.registar_seguro([CURVA], datetime(2026, 1, 10)), {'estado': 'tabelas_ausentes'})

    def test_sincronizacao_nao_falha_sem_a_tabela(self):
        with mock.patch.object(mcurv, 'leitor_producao', side_effect=RuntimeError('base partiu')):
            out = mcurv.registar_seguro([CURVA], datetime(2026, 1, 10))
        self.assertEqual(out['estado'], 'erro')
        self.assertIn('base partiu', out['erro'])


class YConhecidoDoAlvo(unittest.TestCase):
    """alvos_com_y_conhecido: o y do alvo e a versao conhecida ate ao dia do esforco. Versao posterior nao o altera."""

    def _auto(self, historico, revisoes=None):
        return moa.FonteAuto(historico, revisoes or [])

    def _versao(self, versao, watts, detetado_em, evento=None):
        return {'test_id': 'T1', 'versao': versao, 'evento': evento or moa.EVENTO_DETETADA, 'watts': watts,
                'detetado_em': detetado_em, 'data': '2026-01-10', 'modalidade': 'Bike', 'duracao_s': 1200,
                'activity_id': 'a1'}

    def test_alvo_usa_a_versao_conhecida_no_dia_do_esforco(self):
        auto = self._auto([self._versao(1, 300.0, '2026-01-10T12:00:00'),
                           self._versao(2, 310.0, '2026-03-01T12:00:00')])
        validos, sem = mis.alvos_com_y_conhecido([{'test_id': 'T1', 'data': '2026-01-10', 'watts': 310.0}], auto)
        self.assertEqual(sem, [])
        self.assertEqual(validos[0]['watts'], 300.0)           # versao 2 so e conhecida depois: nao altera este alvo

    def test_alvo_posterior_a_revisao_usa_a_versao_nova(self):
        auto = self._auto([self._versao(1, 300.0, '2026-01-10T12:00:00'),
                           self._versao(2, 310.0, '2026-03-01T12:00:00')])
        validos, _ = mis.alvos_com_y_conhecido([{'test_id': 'T1', 'data': '2026-03-02', 'watts': 300.0}], auto)
        self.assertEqual(validos[0]['watts'], 310.0)

    def test_sem_versao_conhecida_no_dia_vai_para_sem_versao(self):
        auto = self._auto([self._versao(1, 300.0, '2026-01-12T12:00:00')])     # so conhecida dois dias depois
        validos, sem = mis.alvos_com_y_conhecido([{'test_id': 'T1', 'data': '2026-01-10', 'watts': 300.0}], auto)
        self.assertEqual(validos, [])
        self.assertEqual(len(sem), 1)

    def test_ultima_versao_conhecida_retirada_nao_e_usada(self):
        auto = self._auto([self._versao(1, 300.0, '2026-01-10T12:00:00'),
                           self._versao(2, 300.0, '2026-01-10T13:00:00', evento=moa.EVENTO_RETIRADA)])
        validos, sem = mis.alvos_com_y_conhecido([{'test_id': 'T1', 'data': '2026-01-10', 'watts': 300.0}], auto)
        self.assertEqual(validos, [])
        self.assertEqual(len(sem), 1)


@unittest.skipUnless(PG_DSN and re.search(r'dbname=\S*_tmp\b', PG_DSN),
                     'IR_TESTE_PG_DSN com base *_tmp nao definido: PostgreSQL nao testado')
class HistoricoCurvasPostgres(unittest.TestCase):
    """Mesmas regras em PostgreSQL (psycopg v3). So toca na tabela de teste ir_curvas_versoes e no registo."""

    def setUp(self):
        from test_ir_migracao import _executor_psycopg3
        self.loja = _executor_psycopg3(PG_DSN)
        for t in (mcurv.TAB,):
            self.loja._exec(f'DROP TABLE IF EXISTS {t}', (), None)
        self.loja._exec(f'DELETE FROM schema_migracoes_ir WHERE id = %s', (mcmig.MIGRACAO_ID,), None)
        self.assertEqual(mcmig.aplicar(self.loja._exec, self.loja.dialeto, '2026-10-23T10:00:00'), 'aplicada')
        self.hc = mcurv.HistoricoCurvas(self.loja._exec, self.loja.dialeto)

    def test_versoes_idempotencia_e_reconstrucao(self):
        self.assertTrue(self.hc.tabela_existe())
        self.assertEqual(self.hc.registar([CURVA], T0)['inseridas'], 1)
        self.assertEqual(self.hc.registar([CURVA], T1)['inalteradas'], 1)
        self.hc.registar([com_watts(CURVA, [999, 1, 1])], T1)
        self.assertEqual(len(self.hc.versoes('a1')), 2)
        self.assertEqual(self.hc.curvas_conhecidas_antes('2026-02-01')[0]['watts'], [400, 320, 280])
        self.assertIsNone(self.hc.curvas_para_alvo('2026-01-10'))


if __name__ == '__main__':
    unittest.main()
