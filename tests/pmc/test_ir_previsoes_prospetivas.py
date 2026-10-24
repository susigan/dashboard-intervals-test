"""Etapa 16: registo prospetivo append-only das previsoes da sombra (SINTETICO: valores de teste, nao producao).

Cobre: idempotencia, imutabilidade da previsao, resultados em versoes, rejeicao de informacao futura,
consultas as-of, bloqueio explicito, migracao (checksum, sem DROP/ALTER/UPDATE/DELETE) e nenhuma escrita em
modelo_ir_ajustes. SQLite sempre; PostgreSQL so se IR_TESTE_PG_DSN apontar para uma base *_tmp (senao SKIP explicito).
"""
import math
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)

import modelo_ir_previsoes as mp  # noqa: E402
import modelo_ir_previsoes_migracao as mm  # noqa: E402

SQL_ESCRITA_PROIBIDA = re.compile(r'\b(DROP|ALTER|UPDATE|DELETE|TRUNCATE)\b', re.I)
TABELAS_PERMITIDAS = {mp.TAB_PREV, mp.TAB_RES, 'schema_migracoes_ir'}


def _prever(loja, **over):
    base = dict(calculado_em='2026-10-24T10:00:00', data_alvo='2026-10-25', modalidade='Bike', duracao_s=1200,
                versao_modelo='ir-v1', lag_dias=1, entradas_sig='entradas-A', estado_temporal='disponivel',
                previsao_ir=300.5, parametros={'p0': 300.0, 'k1': 0.1})
    base.update(over)
    return mp.registar_previsao(loja, **base)


class _Base(unittest.TestCase):
    def _loja(self):
        self.dir = tempfile.mkdtemp()
        self.caminho = os.path.join(self.dir, 'teste.db')
        self.log = []
        loja = mp.loja_sqlite(self.caminho)
        loja.criar()
        inner = loja._exec

        def espiao(sql, params, fetch):
            self.log.append(sql)
            return inner(sql, params, fetch)
        loja._exec = espiao
        return loja


class PrevisaoProspetiva(_Base):
    def setUp(self):
        self.loja = self._loja()

    def test_registo_e_idempotente(self):
        a = _prever(self.loja)
        b = _prever(self.loja)
        self.assertEqual(a['acao'], 'registada')
        self.assertEqual(b['acao'], 'ja_registada')
        self.assertEqual(a['previsao_id'], b['previsao_id'])
        self.assertEqual(len(self.loja.previsoes_do_alvo('Bike', 1200, '2026-10-25', '2026-10-30T00:00:00')), 1)

    def test_entradas_novas_geram_linha_nova_e_a_antiga_fica_intacta(self):
        antiga = _prever(self.loja)
        nova = _prever(self.loja, entradas_sig='entradas-B', previsao_ir=305.0, calculado_em='2026-10-24T11:00:00')
        self.assertEqual(nova['acao'], 'registada')
        self.assertNotEqual(antiga['previsao_id'], nova['previsao_id'])
        velha = self.loja.previsao(antiga['previsao_id'])
        self.assertEqual(velha['previsao_ir'], 300.5)           # a previsao antiga nunca e substituida
        self.assertEqual(velha['calculado_em'], '2026-10-24T10:00:00.000000')

    def test_alvo_nao_prospectivo_e_rejeitado_e_nada_e_gravado(self):
        for alvo in ('2026-10-24', '2026-10-23'):               # mesmo dia ou anterior: o alvo ja estaria nas entradas
            r = _prever(self.loja, data_alvo=alvo)
            self.assertEqual((r['acao'], r['motivo']), ('rejeitada', 'alvo_nao_prospectivo'))
        self.assertEqual(self.loja.previsoes_do_alvo('Bike', 1200, '2026-10-24', '2026-12-31T00:00:00'), [])

    def test_previsao_nao_finita_e_rejeitada(self):
        for v in (float('nan'), float('inf'), None):
            r = _prever(self.loja, previsao_ir=v)
            self.assertEqual((r['acao'], r['motivo']), ('rejeitada', 'previsao_ir_invalida'))

    def test_bloqueio_fica_explicito_sem_valor(self):
        r = _prever(self.loja, estado_temporal='indisponivel', previsao_ir=None, parametros=None,
                    motivo_bloqueio='historico_temporal_indisponivel')
        self.assertEqual(r['acao'], 'registada')
        linha = self.loja.previsao(r['previsao_id'])
        self.assertIsNone(linha['previsao_ir'])
        self.assertEqual(linha['motivo_bloqueio'], 'historico_temporal_indisponivel')

    def test_estado_sem_previsao_exige_motivo(self):
        r = _prever(self.loja, estado_temporal='vazio', previsao_ir=None, parametros=None)
        self.assertEqual((r['acao'], r['motivo']), ('rejeitada', 'bloqueio_sem_motivo'))

    def test_bloqueio_com_valor_em_simultaneo_e_rejeitado(self):
        r = _prever(self.loja, estado_temporal='indisponivel', motivo_bloqueio='x', previsao_ir=300.0)
        self.assertEqual((r['acao'], r['motivo']), ('rejeitada', 'previsao_e_bloqueio_em_simultaneo'))

    def test_consulta_as_of_so_ve_previsoes_ja_calculadas(self):
        _prever(self.loja)
        self.assertEqual(self.loja.previsoes_do_alvo('Bike', 1200, '2026-10-25', '2026-10-24T09:59:59'), [])
        self.assertEqual(len(self.loja.previsoes_do_alvo('Bike', 1200, '2026-10-25', '2026-10-24T10:00:00')), 1)


class ResultadoObservado(_Base):
    def setUp(self):
        self.loja = self._loja()
        self.pid = _prever(self.loja)['previsao_id']

    def test_associa_resultado_sem_alterar_a_previsao(self):
        antes = self.loja.previsao(self.pid)
        r = mp.associar_resultado(self.loja, self.pid, test_id='t1', y_observado=298.0,
                                  observado_em='2026-10-25T18:00:00', registado_em='2026-10-25T18:05:00')
        self.assertEqual((r['acao'], r['versao']), ('registado', 1))
        self.assertEqual(self.loja.previsao(self.pid), antes)

    def test_revisao_do_resultado_e_nova_versao_e_a_anterior_fica(self):
        mp.associar_resultado(self.loja, self.pid, test_id='t1', y_observado=298.0,
                              observado_em='2026-10-25T18:00:00', registado_em='2026-10-25T18:05:00')
        r = mp.associar_resultado(self.loja, self.pid, test_id='t1', y_observado=301.0,
                                  observado_em='2026-10-26T09:00:00', registado_em='2026-10-26T09:10:00')
        self.assertEqual((r['acao'], r['versao']), ('registado', 2))
        vs = self.loja.versoes(self.pid)
        self.assertEqual([v['y_observado'] for v in vs], [298.0, 301.0])

    def test_mesmo_resultado_nao_duplica_versao(self):
        for _ in range(2):
            r = mp.associar_resultado(self.loja, self.pid, test_id='t1', y_observado=298.0,
                                      observado_em='2026-10-25T18:00:00', registado_em='2026-10-25T18:05:00')
        self.assertEqual(r['acao'], 'ja_registado')
        self.assertEqual(len(self.loja.versoes(self.pid)), 1)

    def test_resultado_anterior_ao_calculo_e_rejeitado(self):
        r = mp.associar_resultado(self.loja, self.pid, test_id='t1', y_observado=298.0,
                                  observado_em='2026-10-24T09:00:00')
        self.assertEqual((r['acao'], r['motivo']), ('rejeitado', 'resultado_anterior_a_previsao'))
        self.assertEqual(self.loja.versoes(self.pid), [])

    def test_resultado_antes_do_dia_do_alvo_e_rejeitado(self):
        r = mp.associar_resultado(self.loja, self.pid, test_id='t1', y_observado=298.0,
                                  observado_em='2026-10-24T23:00:00')
        self.assertEqual((r['acao'], r['motivo']), ('rejeitado', 'resultado_antes_do_alvo'))

    def test_previsao_bloqueada_nao_recebe_resultado(self):
        pid_b = _prever(self.loja, data_alvo='2026-10-26', estado_temporal='indisponivel', previsao_ir=None,
                        parametros=None, motivo_bloqueio='historico_temporal_indisponivel')['previsao_id']
        r = mp.associar_resultado(self.loja, pid_b, test_id='t9', y_observado=1.0,
                                  observado_em='2026-10-26T10:00:00')
        self.assertEqual((r['acao'], r['motivo']), ('rejeitado', 'previsao_bloqueada_sem_valor'))

    def test_previsao_inexistente_e_rejeitada(self):
        r = mp.associar_resultado(self.loja, 'nao-existe', test_id='t1', y_observado=1.0,
                                  observado_em='2026-10-25T10:00:00')
        self.assertEqual(r['motivo'], 'previsao_inexistente')

    def test_resultado_as_of_so_ve_versoes_ja_registadas(self):
        mp.associar_resultado(self.loja, self.pid, test_id='t1', y_observado=298.0,
                              observado_em='2026-10-25T18:00:00', registado_em='2026-10-25T18:05:00')
        mp.associar_resultado(self.loja, self.pid, test_id='t1', y_observado=301.0,
                              observado_em='2026-10-26T09:00:00', registado_em='2026-10-26T09:10:00')
        self.assertIsNone(mp.resultado_conhecido_em(self.loja, self.pid, '2026-10-25T12:00:00'))
        self.assertEqual(mp.resultado_conhecido_em(self.loja, self.pid, '2026-10-25T20:00:00')['y_observado'], 298.0)
        self.assertEqual(mp.resultado_conhecido_em(self.loja, self.pid, '2026-10-27T00:00:00')['y_observado'], 301.0)


class MigracaoEAuditoria(unittest.TestCase):
    def test_sql_exportado_corresponde_ao_ficheiro_versionado(self):
        ficheiro = os.path.join(ROOT, 'migracoes', f'{mm.MIGRACAO_ID}.sql')
        with tempfile.TemporaryDirectory() as d:
            gerado = mm.exportar_sql(os.path.join(d, 'x.sql'))
            with open(gerado, encoding='utf-8') as a, open(ficheiro, encoding='utf-8') as b:
                self.assertEqual(a.read(), b.read())

    def test_instrucoes_sao_so_criacao_aditiva(self):
        for sql in mm.instrucoes():
            self.assertFalse(SQL_ESCRITA_PROIBIDA.search(sql), sql)
            self.assertIn('IF NOT EXISTS', sql)

    def test_aplicar_e_idempotente_e_checksum_alterado_nao_altera_nada(self):
        d = tempfile.mkdtemp()
        loja = mp.loja_sqlite(os.path.join(d, 'm.db'))
        ex = loja._exec
        self.assertEqual(mm.aplicar(ex, 'sqlite', '2026-10-24T00:00:00'), 'aplicada')
        self.assertEqual(mm.aplicar(ex, 'sqlite', '2026-10-24T00:00:00'), 'ja_aplicada')
        ex('UPDATE schema_migracoes_ir SET checksum = ? WHERE id = ?', ('outro', mm.MIGRACAO_ID), None)
        with self.assertRaises(mm.MigracaoAlterada):
            mm.aplicar(ex, 'sqlite', '2026-10-24T00:00:00')
        self.assertEqual(ex('SELECT checksum FROM schema_migracoes_ir WHERE id = ?', (mm.MIGRACAO_ID,), 'one')[0],
                         'outro')

    def test_nenhuma_escrita_toca_em_ajustes_nem_na_versao_operacional(self):
        base = _Base()
        loja = base._loja()
        _prever(loja)
        mp.associar_resultado(loja, mp.previsao_id('Bike', 1200, '2026-10-25', 'ir-v1', 'entradas-A'),
                              test_id='t1', y_observado=298.0, observado_em='2026-10-25T18:00:00')
        self.assertTrue(base.log, 'o espiao tem de ter visto as operacoes')
        for sql in base.log:
            self.assertIsNone(SQL_ESCRITA_PROIBIDA.search(sql), sql)     # so INSERT e SELECT
            self.assertNotIn('modelo_ir_ajustes', sql)
            self.assertNotIn('ultimo_ajuste_ir', sql)
            for t in re.findall(r'(?:INTO|FROM)\s+(\w+)', sql, re.I):
                self.assertIn(t, TABELAS_PERMITIDAS, sql)

DSN_PG = os.environ.get('IR_TESTE_PG_DSN', '')
_BASE_PG = re.search(r'dbname=(\S+)', DSN_PG)


@unittest.skipUnless(_BASE_PG and _BASE_PG.group(1).endswith('_tmp'),
                     'PostgreSQL: defina IR_TESTE_PG_DSN com uma base *_tmp (ver tests/pmc/executar_ir_postgres.sh)')
class PrevisaoPostgres(unittest.TestCase):
    """O mesmo contrato no PostgreSQL. Apaga SO as tabelas deste modulo, e so numa base *_tmp."""

    def setUp(self):
        self.loja = mp.loja_postgres(DSN_PG)
        for t in (mp.TAB_RES, mp.TAB_PREV):
            self.loja._exec(f'DROP TABLE IF EXISTS {t}', (), None)
        self.loja.criar()

    def test_idempotencia_imutabilidade_e_resultado_em_versoes(self):
        a = _prever(self.loja)
        b = _prever(self.loja)
        self.assertEqual((a['acao'], b['acao']), ('registada', 'ja_registada'))
        pid = a['previsao_id']
        r1 = mp.associar_resultado(self.loja, pid, test_id='t1', y_observado=298.0,
                                   observado_em='2026-10-25T18:00:00')
        r2 = mp.associar_resultado(self.loja, pid, test_id='t1', y_observado=301.0,
                                   observado_em='2026-10-26T09:00:00')
        self.assertEqual((r1['versao'], r2['versao']), (1, 2))
        self.assertEqual(self.loja.previsao(pid)['previsao_ir'], 300.5)
        self.assertEqual(len(self.loja.versoes(pid)), 2)

    def test_alvo_nao_prospectivo_e_rejeitado_no_postgres(self):
        r = _prever(self.loja, data_alvo='2026-10-24')
        self.assertEqual(r['motivo'], 'alvo_nao_prospectivo')

    def test_consulta_as_of_no_postgres(self):
        _prever(self.loja)
        self.assertEqual(self.loja.previsoes_do_alvo('Bike', 1200, '2026-10-25', '2026-10-24T09:00:00'), [])
        self.assertEqual(len(self.loja.previsoes_do_alvo('Bike', 1200, '2026-10-25', '2026-10-24T12:00:00')), 1)


if __name__ == '__main__':
    unittest.main()
