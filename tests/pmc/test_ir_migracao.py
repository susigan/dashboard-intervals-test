"""Migracao preparada (modelo_ir_confianca_migracao): aditiva, idempotente, com checksum.

SQLite: ficheiro temporario. PostgreSQL: IR_TESTE_PG_DSN apontando para uma base *_tmp descartavel
(ex.: IR_TESTE_PG_DSN="host=127.0.0.1 port=55432 user=postgres dbname=sombra_tmp"). Sem a variavel: saltado.
"""
import os, re, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')
import modelo_ir_sombra as sombra  # noqa: E402
import modelo_ir_confianca_migracao as mig  # noqa: E402

PG_DSN = os.environ.get('IR_TESTE_PG_DSN')
AGORA = '2026-10-23T10:00:00'


class Migracao:
    """Mixin. Subclasses: loja() -> LojaSombra com a base de teste e limpeza(loja)."""

    def setUp(self):
        self.loja = self.loja_teste()
        self.limpar()

    def executor(self):
        return self.loja._exec

    def contar(self, tabela):
        return self.loja._exec(f'SELECT COUNT(*) FROM {tabela}', (), 'one')[0]

    def test_estado_inicial_nao_aplicada(self):
        self.assertEqual(mig.estado(self.executor(), self.loja.dialeto), 'nao_aplicada')

    def test_aplicar_cria_tabela_e_registo(self):
        self.assertEqual(mig.aplicar(self.executor(), self.loja.dialeto, AGORA), 'aplicada')
        self.assertTrue(self.loja.tabela_existe())
        self.assertEqual(mig.estado(self.executor(), self.loja.dialeto), 'aplicada')

    def test_segunda_aplicacao_e_ja_aplicada_e_nao_altera_nada(self):
        mig.aplicar(self.executor(), self.loja.dialeto, AGORA)
        self.loja.inserir(self.linha('Ski', 300, 'x1'))
        antes = self.loja._exec(f'SELECT * FROM {sombra.NOME_TABELA}', (), 'all')
        self.assertEqual(mig.aplicar(self.executor(), self.loja.dialeto, '2026-10-24T10:00:00'), 'ja_aplicada')
        self.assertEqual(self.loja._exec(f'SELECT * FROM {sombra.NOME_TABELA}', (), 'all'), antes)
        self.assertEqual(self.contar(mig.REGISTO), 1)

    def test_checksum_diferente_recusa_sem_alterar(self):
        mig.aplicar(self.executor(), self.loja.dialeto, AGORA)
        self.loja._exec(self.loja._q(f'UPDATE {mig.REGISTO} SET checksum = ? WHERE id = ?'),
                        ('0' * 64, mig.MIGRACAO_ID), None)
        self.assertEqual(mig.estado(self.executor(), self.loja.dialeto)[0], 'alterada')
        with self.assertRaises(mig.MigracaoAlterada):
            mig.aplicar(self.executor(), self.loja.dialeto, AGORA)
        self.assertEqual(self.contar(mig.REGISTO), 1)

    def test_linhas_existentes_sao_preservadas(self):
        mig.aplicar(self.executor(), self.loja.dialeto, AGORA)
        self.loja.inserir(self.linha('Ski', 300, 'pre'))
        self.loja.inserir(self.linha('Bike', 180, 'pre2'))
        mig.aplicar(self.executor(), self.loja.dialeto, AGORA)
        self.assertEqual(self.contar(sombra.NOME_TABELA), 2)

    def test_tabela_preexistente_nao_e_recriada_nem_perde_dados(self):
        self.loja.criar()  # simula corrida: outro processo ja criou a tabela
        self.loja.inserir(self.linha('Ski', 300, 'antes'))
        self.assertEqual(mig.aplicar(self.executor(), self.loja.dialeto, AGORA), 'aplicada')
        self.assertEqual(self.contar(sombra.NOME_TABELA), 1)

    def test_erro_de_corrida_tolerado_se_tabela_existe(self):
        self.loja.criar()
        real = self.executor()
        chamadas = []

        def exec_com_corrida(sql, params, fetch):
            if sql.startswith('CREATE INDEX') and not chamadas:
                chamadas.append(sql)
                raise RuntimeError('simulada: indice criado por outro processo')
            return real(sql, params, fetch)
        self.assertEqual(mig.aplicar(exec_com_corrida, self.loja.dialeto, AGORA), 'aplicada')
        self.assertEqual(len(chamadas), 1)

    def test_instrucoes_sao_aditivas(self):
        for sql in mig.instrucoes():
            self.assertFalse(re.search(r'\b(DROP|DELETE|UPDATE|TRUNCATE|RENAME)\b', sql, re.I), sql[:60])
            self.assertFalse(re.search(r'\bALTER\s+TABLE\b', sql, re.I), sql[:60])

    def test_nao_toca_nas_tabelas_operacionais(self):
        for sql in mig.instrucoes():
            self.assertNotIn('modelo_ir_ajustes', sql)
            self.assertNotIn('ultimo_ajuste_ir', sql)

    def test_sql_exportado_igual_as_instrucoes(self):
        d = tempfile.TemporaryDirectory()
        try:
            caminho = mig.exportar_sql(os.path.join(d.name, 'm.sql'))
            texto = open(caminho, encoding='utf-8').read()
            self.assertIn('NAO aplicada em producao', texto)
            for sql in mig.instrucoes():
                self.assertIn(sql, texto)
            self.assertEqual(mig.estado(self.executor(), self.loja.dialeto), 'nao_aplicada')  # exportar nao aplica
        finally:
            d.cleanup()

    def test_migracao_revista_no_repositorio_tem_o_checksum_atual(self):
        caminho = os.path.join(ROOT, 'migracoes', '2026-10-23_ir_confianca_001_tentativas.sql')
        self.assertTrue(os.path.exists(caminho), 'exportar a migracao revista antes de fechar a etapa')
        self.assertIn(mig.checksum()[:16], open(caminho, encoding='utf-8').read())

    def linha(self, mod, dur, sig):
        import json
        base = {c: None for c in sombra.COLUNAS}
        base.update({'modalidade': mod, 'duracao_s': dur, 'assinatura_dados': sig, 'configuracao': 'c',
                     'politica_hash': 'h', 'tentado_em': AGORA, 'origem': 'teste', 'esquema': 'e', 'nivel': 'C',
                     'estado': 'provisorio', 'validada': 0, 'confianca_parametros': 'nao_medido',
                     'confianca_previsao': 'nula', 'motivos': '[]', 'limitacoes': '[]', 'teria_promovido': 0,
                     'acao_proposta': 'manter', 'motivo_acao': 'm', 'resultado': 'registada_sem_promocao',
                     'decisao': json.dumps({})})
        return base


class TestMigracaoSqlite(Migracao, unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = os.path.join(self._tmp.name, 'mig.db')
        super().setUp()

    def tearDown(self):
        self._tmp.cleanup()

    def loja_teste(self):
        return sombra.loja_sqlite(self.caminho)

    def limpar(self):
        pass


@unittest.skipUnless(PG_DSN and re.search(r'dbname=\S*_tmp\b', PG_DSN),
                     'IR_TESTE_PG_DSN com base *_tmp nao definido: PostgreSQL nao testado')
class TestMigracaoPostgres(Migracao, unittest.TestCase):

    def loja_teste(self):
        return sombra.loja_postgres(PG_DSN)

    def limpar(self):
        for t in (sombra.NOME_TABELA, mig.REGISTO):
            self.loja._exec(f'DROP TABLE IF EXISTS {t}', (), None)



def _executor_psycopg3(dsn):
    """Driver de PRODUCAO (psycopg v3, autocommit), igual ao db._exec: '?' -> '%s', fetch one/all/None."""
    import psycopg

    def executor(sql, params, fetch):
        sql = sombra.sql_para('postgres', sql)
        with psycopg.connect(dsn, autocommit=True) as con:
            with con.cursor() as cur:
                cur.execute(sql, params)
                if fetch == 'one':
                    return cur.fetchone()
                if fetch == 'all':
                    return cur.fetchall()
                return None
    return sombra.LojaSombra(executor, 'postgres')


@unittest.skipUnless(PG_DSN and re.search(r'dbname=\S*_tmp\b', PG_DSN),
                     'IR_TESTE_PG_DSN com base *_tmp nao definido: PostgreSQL nao testado')
class TestMigracaoPostgresPsycopg3(Migracao, unittest.TestCase):
    """Mesmo contrato, com o driver de producao (psycopg v3). Garante que o SQL da migracao e da sombra e
    aceite por psycopg v3 (nao so por psycopg2)."""

    def loja_teste(self):
        try:
            import psycopg  # noqa: F401
        except ImportError:
            self.skipTest('psycopg (v3) nao instalado')
        return _executor_psycopg3(PG_DSN)

    def limpar(self):
        for t in (sombra.NOME_TABELA, mig.REGISTO):
            self.loja._exec(f'DROP TABLE IF EXISTS {t}', (), None)


if __name__ == '__main__':
    unittest.main()
