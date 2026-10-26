"""Exportacao CSV de Metabolismo -> Aquecimento (export.aquecimento_blocos_csv).

Base SQLite temporaria com o esquema real (aquecimento_db._SCHEMA). Sem
producao, sem Drive, sem deteccao. Os testes de rota so correm se a app
importar sem rede; caso contrario sao assinalados como ignorados.
"""
import csv
import hashlib
import inspect
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'utils'))

import export                     # noqa: E402
import aquecimento_db as aqdb     # noqa: E402
import aquecimento_streams as aqs  # noqa: E402

COLS = export.COLUNAS_AQUECIMENTO
INSERT = ("INSERT INTO aquecimento_blocos (activity_id, bloco_num, modalidade, data, "
          "watts_alvo, watts_real, interval_num, tempo_seg, hr_avg, hr_min, hr_max, "
          "smo2_avg, smo2_min, smo2_max, resp_avg, resp_min, resp_max, "
          "dfa1_avg, dfa1_min, dfa1_max, data_registo) "
          "VALUES (" + ",".join("?" * 21) + ")")


def bloco(aid='a1', num=1, mod='Row', data='2026-01-10', alvo=140, real=141.2,
          tempo=300, hr=(150.0, 140.0, 160.0), smo2=(None, None, None),
          resp=(None, None, None), dfa1=(None, None, None),
          reg='2026-01-10T10:00:00'):
    return (aid, num, mod, data, alvo, real, num, tempo,
            *hr, *smo2, *resp, *dfa1, reg)


def criar_base(caminho, linhas=()):
    con = sqlite3.connect(caminho)
    con.executescript(aqdb._SCHEMA)
    con.executemany(INSERT, list(linhas))
    con.commit()
    con.close()


def ler(conteudo):
    """(linhas_csv, aviso_ou_None). Valida BOM e separador antes de parsear."""
    texto = conteudo.decode('utf-8')
    assert texto.startswith('﻿'), 'falta BOM UTF-8'
    texto = texto[1:]
    aviso = None
    if texto.startswith('# AVISO: '):
        aviso, texto = texto.split('\r\n', 1)
        aviso = aviso[len('# AVISO: '):]
    linhas = list(csv.reader(io.StringIO(texto, newline=''), delimiter=';'))
    return linhas, aviso


def sha(caminho):
    with open(caminho, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


class ExportAquecimentoCsv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = os.path.join(self._tmp.name, 'aquecimento.db')

    def tearDown(self):
        self._tmp.cleanup()

    def exportar(self, mod=None):
        return export.aquecimento_blocos_csv(self.caminho, mod)

    # 1. cabecalho e ordem das colunas
    def test_01_cabecalho_e_ordem(self):
        criar_base(self.caminho, [bloco()])
        linhas, aviso = ler(self.exportar()[0])
        self.assertIsNone(aviso)
        self.assertEqual(linhas[0], COLS)
        self.assertEqual(COLS[0], 'activity_id')
        self.assertEqual(COLS[-2:], ['metricas_disponiveis', 'data_registo'])

    # 2. exporta os blocos existentes, uma linha por bloco
    def test_02_uma_linha_por_bloco(self):
        criar_base(self.caminho, [bloco(num=1), bloco(num=2, alvo=160),
                                  bloco(num=3, alvo=180)])
        info = self.exportar()[1]
        linhas, _ = ler(self.exportar()[0])
        self.assertEqual(info['n'], 3)
        self.assertEqual(len(linhas) - 1, 3)

    # 3. associacao entre atividade, modalidade e bloco
    def test_03_associacao_atividade_modalidade_bloco(self):
        criar_base(self.caminho, [
            bloco(aid='A', num=1, mod='Row', alvo=140),
            bloco(aid='A', num=2, mod='Row', alvo=160),
            bloco(aid='B', num=1, mod='Bike', alvo=80, data='2026-02-01'),
        ])
        linhas, _ = ler(self.exportar()[0])
        dados = [dict(zip(COLS, l)) for l in linhas[1:]]
        self.assertEqual([(d['activity_id'], d['modalidade'], d['bloco_num'],
                           d['watts_alvo']) for d in dados],
                         [('A', 'Row', '1', '140'), ('A', 'Row', '2', '160'),
                          ('B', 'Bike', '1', '80')])

    # 4. todas as modalidades por padrao e filtro opcional
    def test_04_todas_as_modalidades_e_filtro(self):
        criar_base(self.caminho, [bloco(aid='R', mod='Row'),
                                  bloco(aid='B', mod='Bike', alvo=80),
                                  bloco(aid='S', mod='Ski', alvo=120)])
        linhas, _ = ler(self.exportar()[0])
        mods = {l[COLS.index('modalidade')] for l in linhas[1:]}
        self.assertEqual(mods, {'Row', 'Bike', 'Ski'})
        linhas_bike, _ = ler(self.exportar('Bike')[0])
        self.assertEqual([l[COLS.index('modalidade')] for l in linhas_bike[1:]], ['Bike'])

    def test_04b_modalidade_invalida_recusada(self):
        criar_base(self.caminho, [bloco()])
        for ruim in ('Bi;ke', 'Row\r\nX', 'x' * 21, '../etc'):
            with self.assertRaises(ValueError, msg=ruim):
                self.exportar(ruim)

    # 5. nulos vazios e zeros preservados
    def test_05_nulos_vazios_zeros_preservados(self):
        criar_base(self.caminho, [bloco(hr=(0.0, None, 160.0),
                                        smo2=(None, None, None), tempo=0)])
        linhas, _ = ler(self.exportar()[0])
        d = dict(zip(COLS, linhas[1]))
        self.assertEqual(d['hr_avg'], '0.0')          # zero continua zero
        self.assertEqual(d['hr_min'], '')             # NULL fica vazio
        self.assertEqual(d['smo2_avg'], '')
        self.assertEqual(d['tempo_seg'], '0')

    # 6. metricas_disponiveis
    def test_06_metricas_disponiveis(self):
        criar_base(self.caminho, [
            bloco(aid='P', num=1, hr=(150.0, 140.0, 160.0)),
            bloco(aid='Q', num=1, hr=(150.0, 140.0, 160.0),
                  smo2=(60.0, 55.0, 65.0)),
            bloco(aid='R', num=1, hr=(None, None, None), resp=(None, 12.0, None)),
            bloco(aid='S', num=1, hr=(None, None, None)),
        ])
        linhas, _ = ler(self.exportar()[0])
        mapa = {l[0]: l[COLS.index('metricas_disponiveis')] for l in linhas[1:]}
        self.assertEqual(mapa, {'P': 'hr', 'Q': 'hr|smo2', 'R': 'resp', 'S': ''})

    # 7. BOM, UTF-8, separador ';' e CRLF
    def test_07_bom_separador_crlf(self):
        criar_base(self.caminho, [bloco()])
        conteudo = self.exportar()[0]
        self.assertTrue(conteudo.startswith(b'\xef\xbb\xbf'))
        texto = conteudo.decode('utf-8')
        cabecalho = texto[1:].split('\r\n', 1)[0]
        self.assertIn(';', cabecalho)
        self.assertNotIn(',', cabecalho)
        self.assertIn('\r\n', texto)
        self.assertNotRegex(texto.replace('\r\n', ''), r'\n')

    def test_07b_acentos_utf8(self):
        criar_base(self.caminho, [bloco(aid='a-ção', mod='Row')])
        linhas, _ = ler(self.exportar()[0])
        self.assertEqual(linhas[1][0], 'a-ção')

    # 8. protecao contra formulas
    def test_08_protecao_contra_formulas(self):
        criar_base(self.caminho, [
            bloco(aid='=HYPERLINK("http://x","y")', num=1),
            bloco(aid='+cmd', num=2),
            bloco(aid='@SUM(A1)', num=3),
            bloco(aid='-1+1', num=4),
        ])
        linhas, _ = ler(self.exportar()[0])
        # A ordenacao e por activity_id; compara-se por valor, nao por posicao.
        ids = {l[0] for l in linhas[1:]}
        self.assertEqual(ids, {"'=HYPERLINK(\"http://x\",\"y\")", "'+cmd",
                               "'@SUM(A1)", "'-1+1"})

    # 9. base vazia
    def test_09_base_vazia(self):
        criar_base(self.caminho, [])
        conteudo, info = self.exportar()
        linhas, aviso = ler(conteudo)
        self.assertIsNone(aviso)
        self.assertEqual(info['n'], 0)
        self.assertEqual(linhas, [COLS])

    # 10. base local inexistente: aviso explicito, sem criar nem descarregar
    def test_10_base_inexistente_aviso_sem_criar(self):
        self.assertFalse(os.path.exists(self.caminho))
        with mock.patch.object(aqdb, '_download') as dl, \
             mock.patch.object(aqdb, 'sincronizar') as sinc:
            conteudo, info = self.exportar()
        self.assertFalse(os.path.exists(self.caminho), 'a base foi criada')
        self.assertFalse(dl.called, 'houve descarga do Drive')
        self.assertFalse(sinc.called, 'houve sincronizacao')
        linhas, aviso = ler(conteudo)
        self.assertIsNotNone(aviso)
        self.assertIn('inexistente', aviso)
        self.assertEqual(linhas, [COLS])
        self.assertIsNotNone(info['aviso'])
        self.assertEqual(info['n'], 0)

    def test_10b_base_sem_tabela_gera_aviso(self):
        con = sqlite3.connect(self.caminho)
        con.execute('CREATE TABLE outra (x INTEGER)')
        con.commit(); con.close()
        conteudo, info = self.exportar()
        self.assertIsNotNone(info['aviso'])
        self.assertIn('ilegivel', info['aviso'])

    # 11. ausencia de escritas
    def test_11_sem_escritas_na_base(self):
        criar_base(self.caminho, [bloco(num=1), bloco(num=2, alvo=160)])
        antes = sha(self.caminho)
        self.exportar()
        self.exportar('Row')
        self.assertEqual(sha(self.caminho), antes)

    def test_11b_ligacao_e_somente_leitura(self):
        criar_base(self.caminho, [bloco()])
        con = export.abrir_leitura_aquecimento(self.caminho)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('DELETE FROM aquecimento_blocos')
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('INSERT INTO aquecimento_rejeitadas '
                            '(activity_id) VALUES (?)', ('x',))
        finally:
            con.close()
        self.assertEqual(len(ler(self.exportar()[0])[0]), 2)

    # 12. ausencia de deteccao, streams e sincronizacao
    def test_12_sem_deteccao_streams_nem_sincronizacao(self):
        criar_base(self.caminho, [bloco()])
        with mock.patch.object(aqs, 'analisar_streams') as an, \
             mock.patch.object(aqs, 'analisar_assistido') as asst, \
             mock.patch.object(aqdb, 'sincronizar') as sinc, \
             mock.patch.object(aqdb, 'get_conn') as gc, \
             mock.patch.object(aqdb, '_download') as dl:
            self.exportar()
            self.exportar('Row')
        for m in (an, asst, sinc, gc, dl):
            self.assertFalse(m.called)

    def test_12b_codigo_da_exportacao_nao_chama_deteccao_nem_escrita(self):
        fontes = ''.join(inspect.getsource(f) for f in (
            export.aquecimento_blocos_csv, export.abrir_leitura_aquecimento,
            export.metricas_disponiveis_aquecimento))
        for proibido in ('analisar_', 'get_streams', 'sincronizar', 'get_conn',
                         'executescript', '_download', 'upsert', 'INSERT ',
                         'UPDATE ', 'DELETE ', 'commit('):
            self.assertNotIn(proibido, fontes, proibido)

    # 13. regressao: controlos existentes intactos, botao so em Aquecimento
    def test_13_interface_preservada_e_botao_isolado(self):
        path = os.path.join(ROOT, 'tabs', 'tab_metabol.py')
        with open(path, encoding='utf-8') as f:
            src = f.read()
        for id_existente in ('aqBtnScan', 'aqBtnLst', 'aqMetrica', 'aqAgregacao',
                             'chRange', 'chAquecimento', 'aqContexto', 'aqTabela',
                             'aqTendencia', 'aqLegenda'):
            self.assertEqual(src.count(f'id="{id_existente}"'), 1, id_existente)
        ini = src.index('<div id="aquecimento" class="tab-content">')
        fim = src.index('<style>', ini)
        secao = src[ini:fim]
        self.assertEqual(src.count('id="aqBtnCsv"'), 1)
        self.assertIn('id="aqBtnCsv"', secao)
        self.assertIn('Exportar aquecimentos (CSV)', secao)
        self.assertEqual(src.count('function aqExportar('), 1)
        self.assertIn('/api/aquecimento/export.csv', src)


class RotaExportAquecimento(unittest.TestCase):
    """Rota GET /api/aquecimento/export.csv. Corre apenas se a app importar."""

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
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = os.path.join(self._tmp.name, 'aquecimento.db')
        self.patch = mock.patch.object(self.app_mod.aq_db, '_LOCAL_DB', self.caminho)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self._tmp.cleanup()

    def test_rota_com_base(self):
        criar_base(self.caminho, [bloco(aid='X', mod='Row')])
        r = self.client.get('/api/aquecimento/export.csv?modalidade=Row')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.mimetype == 'text/csv')
        self.assertIn('attachment', r.headers['Content-Disposition'])
        self.assertEqual(r.headers['X-Registos'], '1')
        self.assertNotIn('X-Aviso', r.headers)
        linhas, _ = ler(r.data)
        self.assertEqual(linhas[1][0], 'X')

    def test_rota_sem_base_avisa(self):
        r = self.client.get('/api/aquecimento/export.csv')
        self.assertEqual(r.status_code, 200)
        self.assertIn('inexistente', r.headers['X-Aviso'])
        self.assertFalse(os.path.exists(self.caminho))

    def test_rota_modalidade_invalida_400(self):
        r = self.client.get('/api/aquecimento/export.csv?modalidade=Bi%3Bke')
        self.assertEqual(r.status_code, 400)


if __name__ == '__main__':
    unittest.main()
