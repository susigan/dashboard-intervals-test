"""Registo temporal prospetivo (Etapa 10): modelo_ir_temporal + migracao + ganchos reais.

Dados: TODOS sinteticos (valores, marcos e YES construidos aqui). Nada aqui e validacao historica real.
PostgreSQL: IR_TESTE_PG_DSN com base *_tmp descartavel. Sem a variavel: saltado (e reportado como saltado).
"""
import datetime as dt
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

import db  # noqa: E402
import fase1_util as U  # noqa: E402
import modelo_ir_sombra as sombra  # noqa: E402
import modelo_ir_temporal as tmp  # noqa: E402
import modelo_ir_temporal_migracao as mig  # noqa: E402
import modelo_mmp3 as mmp3  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import observacoes_auto as oa  # noqa: E402
from test_ir_migracao import _executor_psycopg3  # noqa: E402

PG_DSN = os.environ.get('IR_TESTE_PG_DSN')
AGORA = '2026-10-23T10:00:00.000000'
MARCOS = [('2025-01-01', 'S25'), ('2026-01-01', 'S26')]


def yes(tid, watts, act='a1', origem='automatico', revisao=None, mod='Bike'):
    return {'modalidade': mod, 'activity_id': act, 'watts': watts, 'regra_versao': 1,
            'origem': origem, 'revisao': revisao}


class Armazem:
    """Mixin para SQLite e PostgreSQL. Subclasses: loja() e limpar()."""

    def setUp(self):
        self.loja = self.loja_teste()
        self.limpar()
        self.reg = tmp.RegistoTemporal(self.loja._exec, self.loja.dialeto)
        self.assertEqual(mig.aplicar(self.loja._exec, self.loja.dialeto, AGORA), 'aplicada')

    def contar(self, tabela):
        return self.loja._exec(f'SELECT COUNT(*) FROM {tabela}', (), 'one')[0]

    # marcos ---------------------------------------------------------------
    def test_marcos_primeira_gravacao_e_repeticao_idempotente(self):
        self.assertEqual(self.reg.registar_marcos(MARCOS, 'cal', '2026-10-23T10:00:00'), 'gravado')
        self.assertEqual(self.reg.registar_marcos(MARCOS, 'cal', '2026-10-24T10:00:00'), 'sem_alteracao')
        self.assertEqual(self.contar(tmp.TAB_MARCOS), 1)

    def test_marcos_alterados_criam_nova_linha_e_preservam_a_anterior(self):
        self.reg.registar_marcos(MARCOS, 'cal', '2026-10-23T10:00:00')
        antes = self.loja._exec(f'SELECT * FROM {tmp.TAB_MARCOS}', (), 'all')
        self.assertEqual(self.reg.registar_marcos(MARCOS + [('2027-01-01', 'S27')], 'cal',
                                                  '2026-10-25T10:00:00'), 'gravado')
        depois = self.loja._exec(f'SELECT * FROM {tmp.TAB_MARCOS} ORDER BY conhecido_em', (), 'all')
        self.assertEqual(len(depois), 2)
        self.assertIn(antes[0], depois)

    def test_marcos_indisponiveis_sao_um_estado_real_e_nao_apagam_o_historico(self):
        self.reg.registar_marcos(MARCOS, 'cal', '2026-10-23T10:00:00')
        self.assertEqual(self.reg.registar_marcos(None, 'mes', '2026-10-24T10:00:00'), 'gravado')
        self.assertEqual(self.reg.marcos_conhecidos_em('2026-10-30')['marcos'], None)
        self.assertEqual(self.reg.marcos_conhecidos_em('2026-10-24')['marcos'], [['2025-01-01', 'S25'], ['2026-01-01', 'S26']])

    def test_marcos_sem_fuga_so_os_conhecidos_antes_do_alvo(self):
        self.reg.registar_marcos(MARCOS, 'cal', '2026-10-23T10:00:00')
        self.reg.registar_marcos(MARCOS + [('2027-01-01', 'S27')], 'cal', '2026-10-25T10:00:00')
        # alvo a 24: o registo de 25 ainda nao era conhecido
        ant = self.reg.marcos_conhecidos_em('2026-10-24')
        self.assertEqual(len(ant['marcos']), 2)
        # alvo a 26: ja pode usar o de 25, nunca um posterior
        pos = self.reg.marcos_conhecidos_em('2026-10-26')
        self.assertEqual(len(pos['marcos']), 3)
        # antes de qualquer registo: rejeicao explicita (None), nunca o estado atual
        self.assertIsNone(self.reg.marcos_conhecidos_em('2026-10-01'))

    def test_marcos_registo_no_mesmo_dia_do_alvo_nao_conta_para_esse_alvo(self):
        self.reg.registar_marcos(MARCOS, 'cal', '2026-10-24T09:00:00')
        self.assertIsNone(self.reg.marcos_conhecidos_em('2026-10-24'))
        self.assertIsNotNone(self.reg.marcos_conhecidos_em('2026-10-25'))

    # YES --------------------------------------------------------------------
    def test_yes_aparece_muda_sai_e_reaparece_sem_duplicar(self):
        self.assertEqual(self.reg.registar_transicoes_yes({'Bike:180:a1': yes('Bike:180:a1', 300.0)},
                                                          '2026-10-23T10:00:00'), 1)
        # repeticao: nada
        self.assertEqual(self.reg.registar_transicoes_yes({'Bike:180:a1': yes('Bike:180:a1', 300.0)},
                                                          '2026-10-23T11:00:00'), 0)
        # valor muda (correcao): uma transicao nova; a anterior fica
        self.assertEqual(self.reg.registar_transicoes_yes({'Bike:180:a1': yes('Bike:180:a1', 310.0, act='a2')},
                                                          '2026-10-24T10:00:00'), 1)
        # deixa de ser YES: uma linha de saida, so uma vez
        self.assertEqual(self.reg.registar_transicoes_yes({}, '2026-10-25T10:00:00'), 1)
        self.assertEqual(self.reg.registar_transicoes_yes({}, '2026-10-26T10:00:00'), 0)
        # volta a ser YES: nova linha
        self.assertEqual(self.reg.registar_transicoes_yes({'Bike:180:a1': yes('Bike:180:a1', 310.0, act='a2')},
                                                          '2026-10-27T10:00:00'), 1)
        self.assertEqual(self.contar(tmp.TAB_YES), 4)

    def test_yes_reconstrucao_antes_do_alvo_ignora_mudancas_posteriores(self):
        self.reg.registar_transicoes_yes({'Bike:180:a1': yes('Bike:180:a1', 300.0)}, '2026-10-23T10:00:00')
        self.reg.registar_transicoes_yes({'Bike:180:a1': yes('Bike:180:a1', 350.0, act='a9')},
                                         '2026-10-25T10:00:00')
        self.assertEqual(self.reg.yes_conhecidos_em('2026-10-24')['Bike:180:a1']['watts'], 300.0)
        self.assertEqual(self.reg.yes_conhecidos_em('2026-10-26')['Bike:180:a1']['watts'], 350.0)
        self.assertEqual(self.reg.yes_conhecidos_em('2026-10-20'), {})

    def test_yes_revisao_manual_fica_identificada(self):
        self.reg.registar_transicoes_yes({'Bike:180:a1': yes('Bike:180:a1', 300.0, origem='confirmacao', revisao=2)},
                                         '2026-10-23T10:00:00')
        r = self.reg.yes_conhecidos_em('2026-10-24')['Bike:180:a1']
        self.assertEqual((r['origem'], r['revisao']), ('confirmacao', 2))

    def test_linhas_anteriores_nunca_mudam(self):
        self.reg.registar_transicoes_yes({'Bike:180:a1': yes('Bike:180:a1', 300.0)}, '2026-10-23T10:00:00')
        antes = self.loja._exec(f'SELECT * FROM {tmp.TAB_YES} ORDER BY conhecido_em', (), 'all')
        self.reg.registar_transicoes_yes({'Bike:180:a1': yes('Bike:180:a1', 999.0, act='x')}, '2026-10-24T10:00:00')
        self.assertEqual(self.loja._exec(f'SELECT * FROM {tmp.TAB_YES} ORDER BY conhecido_em', (), 'all')[:1], antes)

    # migracao ---------------------------------------------------------------
    def test_migracao_segunda_aplicacao_nao_altera_dados(self):
        self.reg.registar_marcos(MARCOS, 'cal', '2026-10-23T10:00:00')
        self.reg.registar_transicoes_yes({'Bike:180:a1': yes('Bike:180:a1', 300.0)}, '2026-10-23T10:00:00')
        antes = (self.loja._exec(f'SELECT * FROM {tmp.TAB_MARCOS}', (), 'all'),
                 self.loja._exec(f'SELECT * FROM {tmp.TAB_YES}', (), 'all'))
        self.assertEqual(mig.aplicar(self.loja._exec, self.loja.dialeto, '2026-10-24T10:00:00'), 'ja_aplicada')
        depois = (self.loja._exec(f'SELECT * FROM {tmp.TAB_MARCOS}', (), 'all'),
                  self.loja._exec(f'SELECT * FROM {tmp.TAB_YES}', (), 'all'))
        self.assertEqual(antes, depois)

    def test_migracao_checksum_alterado_recusa_sem_alterar(self):
        self.loja._exec(sombra.sql_para(self.loja.dialeto, f'UPDATE {mig.REGISTO} SET checksum = ? WHERE id = ?'),
                        ('0' * 64, mig.MIGRACAO_ID), None)
        with self.assertRaises(mig.MigracaoAlterada):
            mig.aplicar(self.loja._exec, self.loja.dialeto, AGORA)

    def test_sql_da_migracao_e_aditivo(self):
        texto = ';'.join(mig.instrucoes()).upper()
        for proibido in ('DROP ', 'DELETE ', 'UPDATE ', 'ALTER ', 'TRUNCATE '):
            self.assertNotIn(proibido, texto, proibido)

    def test_migracao_nao_toca_nas_tabelas_operacionais(self):
        nomes = ' '.join(mig.instrucoes())
        for operacional in ('modelo_observacoes_auto', 'modelo_mmp3_yes_historico', 'modelo_ir_ajustes',
                            'ultimo_ajuste_ir', 'activities'):
            self.assertNotIn(operacional, nomes)

    def test_sql_exportado_coincide_com_as_instrucoes(self):
        with tempfile.TemporaryDirectory() as d:
            caminho = mig.exportar_sql(os.path.join(d, 'm.sql'))
            texto = open(caminho, encoding='utf-8').read()
        for sql in mig.instrucoes():
            self.assertIn(sql, texto)


class TestArmazemSqlite(Armazem, unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.caminho = os.path.join(self._tmp.name, 'temporal.db')
        super().setUp()

    def loja_teste(self):
        return sombra.loja_sqlite(self.caminho)

    def limpar(self):
        pass


@unittest.skipUnless(PG_DSN and re.search(r'dbname=\S*_tmp\b', PG_DSN),
                     'IR_TESTE_PG_DSN com base *_tmp nao definido: PostgreSQL nao testado')
class TestArmazemPostgresPsycopg3(Armazem, unittest.TestCase):
    """Mesmo contrato com o driver de producao (psycopg v3)."""

    def loja_teste(self):
        return _executor_psycopg3(PG_DSN)

    def limpar(self):
        for t in (tmp.TAB_MARCOS, tmp.TAB_YES, mig.REGISTO):
            self.loja._exec(f'DROP TABLE IF EXISTS {t}', (), None)


class HooksProducao(unittest.TestCase):
    """Ganchos reais numa base SQLite temporaria (db.py): recalibrar, atualizar e falhas de persistencia."""

    def setUp(self):
        self._ctx = U.base_temporaria(db)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.assertEqual(mig.aplicar(db._exec, db.DRIVER, AGORA), 'aplicada')

    def contar(self, t):
        return db._exec(f'SELECT COUNT(*) FROM {t}', (), 'one')[0]

    def test_registo_de_marcos_pelo_gancho_real_e_idempotente(self):
        with mock.patch('observacoes_auto.obter_marcos', return_value=(MARCOS, 'teste')):
            self.assertEqual(tmp.registar_marcos_seguro(MARCOS, 'teste', AGORA)['estado'], 'gravado')
            self.assertEqual(tmp.registar_marcos_seguro(MARCOS, 'teste', AGORA)['estado'], 'sem_alteracao')
        self.assertEqual(self.contar(tmp.TAB_MARCOS), 1)

    def test_sem_migracao_o_gancho_nao_escreve_e_nao_levanta(self):
        db._exec(f'DROP TABLE {tmp.TAB_MARCOS}', (), None)
        db._exec(f'DROP TABLE {tmp.TAB_YES}', (), None)
        self.assertEqual(tmp.registar_marcos_seguro(MARCOS, 'teste', AGORA)['estado'], 'tabelas_ausentes')
        self.assertEqual(tmp.registar_yes_seguro({}, AGORA)['estado'], 'tabelas_ausentes')

    def test_falha_de_persistencia_nao_levanta(self):
        def falha(*a, **k):
            raise RuntimeError('disco cheio (simulado)')
        with mock.patch.object(tmp.RegistoTemporal, 'registar_marcos', side_effect=falha):
            r = tmp.registar_marcos_seguro(MARCOS, 'teste', AGORA)
        self.assertEqual(r['estado'], 'erro')
        self.assertIn('disco cheio', r['erro'])

    def test_efetivos_yes_de_hoje_separa_automatico_de_confirmacao(self):
        ent = {'Bike': ([], [
            {'test_id': 'Bike:180:a1', 'activity_id': 'a1', 'watts': 300.0, 'estado': mmp3.E_ELEGIVEL,
             'origem_classificacao': None},
            {'test_id': 'Bike:180:a2', 'activity_id': 'a2', 'watts': 320.0, 'estado': mmp3.E_ELEGIVEL,
             'origem_classificacao': mmp3.ORIGEM_CONFIRMACAO, 'revisao_confirmada': 3},
            {'test_id': 'Bike:180:a3', 'activity_id': 'a3', 'watts': 1.0, 'estado': 'inelegivel',
             'origem_classificacao': None},
        ])}
        out = mis.efetivos_yes_de_hoje(ent)
        self.assertEqual(set(out), {'Bike:180:a1', 'Bike:180:a2'})
        self.assertEqual(out['Bike:180:a1']['origem'], 'automatico')
        self.assertEqual((out['Bike:180:a2']['origem'], out['Bike:180:a2']['revisao']), ('confirmacao', 3))

    def test_recalibrar_real_regista_marcos_e_nao_quebra_se_persistencia_falhar(self):
        curvas = [{'activity_id': 'a1', 'type': 'Bike', 'date': '2026-03-01', 'secs': [180], 'watts': [300.0],
                   'weight': [1.0]}]
        ativ = [{'id': 'a1', 'start_date_local': '2026-03-01T07:30:00', 'icu_ignore_power': False, 'tags': []}]
        with mock.patch.object(db, 'load_power_curves', side_effect=lambda tipo=None, desde=None: list(curvas)), \
                mock.patch.object(db, 'load_activities', side_effect=lambda desde=None: list(ativ)), \
                mock.patch('observacoes_auto.obter_marcos', return_value=(MARCOS, 'teste')):
            res = mis.recalibrar(origem='teste', agora=dt.datetime(2026, 10, 23, 10, 0, 0))
            self.assertIsInstance(res, dict)
            self.assertEqual(self.contar(tmp.TAB_MARCOS), 1)
            # repetir a mesma recalibracao: nada novo
            mis.recalibrar(origem='teste', agora=dt.datetime(2026, 10, 23, 11, 0, 0))
            self.assertEqual(self.contar(tmp.TAB_MARCOS), 1)
            # persistencia a falhar: o IR continua a responder, sem excecao
            with mock.patch.object(tmp.RegistoTemporal, 'registar_marcos', side_effect=RuntimeError('x')), \
                    mock.patch.object(tmp.RegistoTemporal, 'registar_transicoes_yes', side_effect=RuntimeError('y')):
                res2 = mis.recalibrar(origem='teste', agora=dt.datetime(2026, 10, 24, 10, 0, 0))
            self.assertIsInstance(res2, dict)

    def test_observacao_nova_e_alteracao_preservam_versoes_e_instante(self):
        base = {c: None for c in db._COLS_OBS_AUTO}
        base.update({'test_id': 'Bike:180:a1', 'evento': 'detetada', 'modalidade': 'Bike', 'duracao_s': 180,
                     'activity_id': 'a1', 'data': '2026-03-01', 'hora_local': '07:30', 'watts': 300.0,
                     'elegibilidade': 'elegivel', 'origem': 'automatico', 'versao_contrato': 1,
                     'detetado_em': dt.datetime(2026, 3, 2, 9, 0, 0), 'versao': 1})
        db.inserir_observacao_auto(dict(base))
        v2 = dict(base, versao=2, watts=310.0, detetado_em=dt.datetime(2026, 10, 23, 9, 0, 0),
                  substitui_versao=1)
        db.inserir_observacao_auto(v2)
        hist = [h for h in db.observacoes_auto_historico() if h['test_id'] == 'Bike:180:a1']
        self.assertEqual({(h['versao'], h['watts']) for h in hist}, {(1, 300.0), (2, 310.0)})
        self.assertEqual(len({str(h['detetado_em']) for h in hist}), 2)
        with self.assertRaises(db.ConflitoObservacao):
            db.inserir_observacao_auto(dict(base, watts=999.0))   # mesma versao: imutavel

    def test_log_de_sincronizacao_existe_e_e_do_fluxo_real(self):
        db.log_sync('incremental', '2026-10-01', 3, 2, 1, 1.25, None)
        db.log_sync('incremental', '2026-10-01', 0, 0, 0, 0.5, 'falhou')
        linhas = db._exec('SELECT recebidas, erro FROM sync_log ORDER BY id', (), 'all')
        self.assertEqual(linhas, [(3, None), (0, 'falhou')])


if __name__ == '__main__':
    unittest.main()
