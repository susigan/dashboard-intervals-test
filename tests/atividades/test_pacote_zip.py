"""Pacote ZIP (atividades + streams + manifesto). Dados sinteticos, sem rede.

O banco e um SQLite temporario (SQLITE_PATH definido ANTES de importar db),
para nao tocar no banco local nem no de producao.
"""
import csv, hashlib, io, json, os, shutil, subprocess, sys, tempfile, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
_TMP = tempfile.mkdtemp(prefix='pacote_test_')
os.environ['SQLITE_PATH'] = os.path.join(_TMP, 'teste.db')
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)

import socket                                   # noqa: E402
import requests                                  # noqa: E402
from flask import Flask                          # noqa: E402
import export                                    # noqa: E402
import db                                        # noqa: E402
import api_client                                # noqa: E402
import tabs.tab_atividades as tab                # noqa: E402
from fixtures_atividades import atividades_fixture  # noqa: E402

# Bloqueia qualquer chamada externa durante os testes.
def _proibido(*a, **k):
    raise AssertionError('chamada externa proibida nos testes')
api_client.icu_get = _proibido
requests.get = _proibido
requests.post = _proibido
socket.socket.connect = _proibido  # noqa

def ler_zip(dados):
    z = zipfile.ZipFile(io.BytesIO(dados))
    return z, {n: z.read(n) for n in z.namelist()}

def csv_linhas(b):
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'), newline='')))

ACTS = [
    {'id': 'a1', 'start_date_local': '2024-03-10T07:00:00', 'type': 'Run', 'name': 'Treino',
     'trimp': 0, 'icu_training_load': 0, 'tags': None, '_kj': 9.9, '_type_norm': 'Run',
     'oauth_client_id': 'cliente-fic', 'access_token': 'SEGREDO-FICTICIO', 'CPR': 123},
    {'id': 'a2', 'start_date_local': '2024-03-11T06:00:00', 'type': 'Ride', 'name': 'Bike',
     'trimp': 47.39868, 'icu_training_load': 44},
    {'id': 'a3', 'start_date_local': '2024-03-12T06:00:00', 'type': 'Ride', 'name': 'Sem streams',
     'trimp': None},
]

def fake_streams(mapa):
    def f(aid):
        if aid not in mapa:
            return None, None
        return mapa[aid]
    return f


class PacoteUnit(unittest.TestCase):

    def gerar(self, mapa, ids=('a1', 'a2', 'a3'), acts=None, **kw):
        return export.pacote_zip(acts or ACTS, list(ids), fake_streams(mapa),
                                 fonte_atividades='db', agora='2026-10-15T00:00:00', **kw)

    def test_01_estrutura_e_hashes(self):
        dados, _ = self.gerar({})
        z, arq = ler_zip(dados)
        self.assertIsNone(z.testzip())
        self.assertEqual(set(arq), {'atividades.csv', 'atividades.jsonl', 'streams.csv', 'manifest.json'})
        man = json.loads(arq['manifest.json'])
        for entrada in man['arquivos']:
            self.assertEqual(hashlib.sha256(arq[entrada['nome']]).hexdigest(), entrada['sha256'])
            self.assertEqual(len(arq[entrada['nome']]), entrada['bytes'])

    def test_02_jsonl_objeto_original_sem_derivados_nem_segredos(self):
        dados, _ = self.gerar({})
        _, arq = ler_zip(dados)
        linhas = [json.loads(l) for l in arq['atividades.jsonl'].decode('utf-8').splitlines()]
        self.assertEqual([l['id'] for l in linhas], ['a1', 'a2', 'a3'])
        self.assertNotIn('_kj', linhas[0])
        self.assertNotIn('_type_norm', linhas[0])
        self.assertNotIn('access_token', linhas[0])
        self.assertNotIn('oauth_client_id', linhas[0])
        self.assertNotIn('CPR', linhas[0])
        self.assertEqual(linhas[0]['trimp'], 0)                 # zero explicito preservado
        self.assertIsNone(linhas[2]['trimp'])                   # nulo preservado
        self.assertNotIn('icu_rpe', linhas[1])                  # ausente continua ausente

    def test_03_atividades_csv_trimp_e_zero_preservados(self):
        dados, _ = self.gerar({})
        _, arq = ler_zip(dados)
        rows = {r['id']: r for r in csv_linhas(arq['atividades.csv'])}
        self.assertEqual(rows['a1']['trimp'], '0')
        self.assertEqual(rows['a2']['trimp'], '47.39868')       # valor original, sem recalculo
        self.assertEqual(rows['a3']['trimp'], '')               # nulo vira vazio

    def test_04_streams_contagem_valores_e_nulos(self):
        s = {'watts': [1, 0, None, 250], 'heartrate': [120.5, 121]}
        meta = [{'key': 'watts', 'type': 'watts', 'points': 4},
                {'key': 'heartrate', 'type': 'heartrate', 'points': 2}]
        dados, _ = self.gerar({'a1': (s, meta)})
        _, arq = ler_zip(dados)
        rows = csv_linhas(arq['streams.csv'])
        self.assertEqual(len(rows), 6)
        w = [r for r in rows if r['stream_key'] == 'watts']
        self.assertEqual([r['sample_index'] for r in w], ['0', '1', '2', '3'])
        self.assertEqual([r['value'] for r in w], ['1', '0', '', '250'])   # 0 mantido, None vazio
        self.assertTrue(all(r['activity_id'] == 'a1' for r in rows))

    def test_05_comprimentos_diferentes_sem_alinhamento_por_posicao(self):
        longo = list(range(7))
        curto = [9, 8, 7]
        s = {'a_longo': longo, 'b_curto': curto}
        meta = [{'key': 'a_longo', 'type': 'x', 'points': 7}, {'key': 'b_curto', 'type': 'y', 'points': 3}]
        dados, _ = self.gerar({'a2': (s, meta)})
        _, arq = ler_zip(dados)
        rows = csv_linhas(arq['streams.csv'])
        self.assertEqual(sum(1 for r in rows if r['stream_key'] == 'a_longo'), 7)
        self.assertEqual(sum(1 for r in rows if r['stream_key'] == 'b_curto'), 3)
        self.assertEqual([r['value'] for r in rows if r['stream_key'] == 'b_curto'], ['9', '8', '7'])

    def test_06_streams_sem_timestamp_nem_unidade_ficam_vazios(self):
        s = {'watts': [10, 20]}
        meta = [{'key': 'watts', 'type': 'watts', 'points': 2}]
        dados, _ = self.gerar({'a1': (s, meta)})
        _, arq = ler_zip(dados)
        r = csv_linhas(arq['streams.csv'])
        self.assertTrue(all(x['time_seconds'] == '' and x['timestamp'] == '' and x['unit'] == '' for x in r))
        man = json.loads(ler_zip(dados)[1]['manifest.json'])
        self.assertTrue(any('time' in l for l in man['limitacoes']))

    def test_07_stream_time_e_unidade_quando_fornecidos(self):
        s = {'time': [0, 1, 2], 'watts': [100, 110, 120]}
        meta = [{'key': 'time', 'type': 'time', 'points': 3, 'unit': 's'},
                {'key': 'watts', 'type': 'watts', 'points': 3, 'unit': 'W'}]
        dados, _ = self.gerar({'a1': (s, meta)})
        _, arq = ler_zip(dados)
        w = [r for r in csv_linhas(arq['streams.csv']) if r['stream_key'] == 'watts']
        self.assertEqual([r['time_seconds'] for r in w], ['0', '1', '2'])
        self.assertEqual({r['unit'] for r in w}, {'W'})

    def test_08_reduzido_flag_pelos_pontos_originais(self):
        reduzido = [1.0] * 1500
        igual = [2.0] * 3
        meta = [{'key': 'a', 'type': 'a', 'points': 2000},
                {'key': 'b', 'type': 'b', 'points': 3}]
        dados, _ = self.gerar({'a1': ({'a': reduzido, 'b': igual}, meta)})
        _, arq = ler_zip(dados)
        rows = csv_linhas(arq['streams.csv'])
        self.assertTrue(all(r['reduzido'] == 'true' for r in rows if r['stream_key'] == 'a'))
        self.assertTrue(all(r['reduzido'] == 'false' for r in rows if r['stream_key'] == 'b'))
        self.assertEqual(rows[0]['pontos_originais'], '2000')

    def test_09_atividade_sem_streams_registrada_no_manifesto(self):
        dados, _ = self.gerar({'a1': ({'watts': [1]}, [{'key': 'watts', 'type': 'watts', 'points': 1}])})
        _, arq = ler_zip(dados)
        self.assertNotIn('a2', {r['activity_id'] for r in csv_linhas(arq['streams.csv'])})
        man = json.loads(arq['manifest.json'])
        por = {p['activity_id']: p['status'] for p in man['por_atividade']}
        self.assertEqual(por, {'a1': 'exportado', 'a2': 'sem_streams_locais', 'a3': 'sem_streams_locais'})
        self.assertEqual(man['atividades']['sem_streams_locais'], 2)

    def test_10_erro_de_leitura_nao_e_silencioso(self):
        def f(aid):
            if aid == 'a2':
                raise RuntimeError('disco indisponivel')
            return ({'watts': [1]}, [{'key': 'watts', 'type': 'watts', 'points': 1}]) if aid == 'a1' else (None, None)
        dados, _ = export.pacote_zip(ACTS, ['a1', 'a2', 'a3'], f, fonte_atividades='db')
        man = json.loads(ler_zip(dados)[1]['manifest.json'])
        por = {p['activity_id']: p for p in man['por_atividade']}
        self.assertEqual(por['a2']['status'], 'erro_leitura')
        self.assertIn('disco', por['a2']['erro'])
        self.assertEqual(por['a1']['status'], 'exportado')
        self.assertEqual(man['atividades']['com_erro_leitura'], 1)

    def test_11_ids_duplicados_e_desconhecidos(self):
        dados, _ = self.gerar({}, ids=('a1', 'a1', 'nao-existe', 'a2'))
        _, arq = ler_zip(dados)
        man = json.loads(arq['manifest.json'])
        self.assertEqual(man['atividades']['ids_duplicados'], 1)
        self.assertEqual(man['atividades']['ids_ausentes'], ['nao-existe'])
        self.assertEqual([r['id'] for r in csv_linhas(arq['atividades.csv'])], ['a1', 'a2'])
        self.assertTrue(any('nao existem' in l for l in man['limitacoes']))

    def test_12_nenhum_id_valido_levanta_erro(self):
        with self.assertRaises(ValueError):
            self.gerar({}, ids=('x', 'y'))

    def test_13_stream_estruturado_vai_para_json(self):
        s = {'latlng': [[1.0, 2.0], [1.1, 2.1]]}
        meta = [{'key': 'latlng', 'type': 'latlng', 'points': 2}]
        dados, _ = self.gerar({'a1': (s, meta)})
        z, arq = ler_zip(dados)
        self.assertIn('streams_estruturados.json', z.namelist())
        self.assertEqual(json.loads(arq['streams_estruturados.json'])['a1']['latlng'], [[1.0, 2.0], [1.1, 2.1]])
        self.assertEqual([r for r in csv_linhas(arq['streams.csv']) if r['stream_key'] == 'latlng'], [])

    def test_14_limite_recusa_em_vez_de_truncar(self):
        ids = [f'x{i}' for i in range(export.LIMITE_ATIVIDADES_PACOTE + 1)]
        with self.assertRaises(export.LimitePacote):
            export.pacote_zip(ACTS, ids, fake_streams({}), fonte_atividades='db')

    def test_15_formula_protegida_em_metadados_de_stream(self):
        s = {'watts': [1]}
        meta = [{'key': 'watts', 'type': 'watts', 'sensor_name': '=HYPERLINK("x")', 'points': 1}]
        dados, _ = self.gerar({'a1': (s, meta)})
        _, arq = ler_zip(dados)
        self.assertEqual(csv_linhas(arq['streams.csv'])[0]['sensor_name'], "'=HYPERLINK(\"x\")")

    def test_16_csv_utf8_bom_e_crlf(self):
        dados, _ = self.gerar({'a1': ({'watts': [1]}, [{'key': 'watts', 'type': 'watts', 'sensor_name': 'Potência ção', 'points': 1}])})
        _, arq = ler_zip(dados)
        self.assertTrue(arq['streams.csv'].startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'\r\n', arq['streams.csv'])
        self.assertIn('Potência ção'.encode('utf-8'), arq['streams.csv'])

    def test_17_nao_chama_rede(self):
        # _proibido levantaria AssertionError; se a funcao tentasse rede, o teste falharia
        dados, _ = self.gerar({'a1': ({'watts': [1]}, [{'key': 'watts', 'type': 'watts', 'points': 1}])})
        self.assertTrue(dados)


class NomeDoArquivo(unittest.TestCase):

    def test_24_data_malformada_nao_entra_no_cabecalho(self):
        self.assertEqual(tab._intervalo_nome('2024-03-01"; x=1', '2024-03-02'), 'sem_data')
        self.assertEqual(tab._intervalo_nome('2024-03-01', '2024-03-05'), '2024-03-01_a_2024-03-05')
        self.assertEqual(tab._intervalo_nome('2024-03-01', '2024-03-01'), '2024-03-01')
        self.assertEqual(tab._intervalo_nome(None, None), 'sem_data')


class DbRoundtrip(unittest.TestCase):
    """Streams gravados com db.upsert_streams e lidos com db.get_streams (SQLite temporario)."""

    @classmethod
    def setUpClass(cls):
        db.init_schema()

    def test_18_roundtrip_e_reduzido(self):
        grande = [float(i % 7) for i in range(3000)]
        vazio_meio = [1.0, None, 3.0]
        meta = [{'key': 'watts', 'type': 'watts', 'points': 3000, 'custom': False},
                {'key': 'heartrate', 'type': 'heartrate', 'points': 3}]
        db.upsert_streams('dbt1', meta, {'watts': grande[:1500], 'heartrate': vazio_meio})
        s, m = db.get_streams('dbt1')
        self.assertEqual(s['heartrate'], vazio_meio)               # None preservado
        dados, _ = export.pacote_zip(
            [{'id': 'dbt1', 'start_date_local': '2025-01-01T00:00:00', 'type': 'Ride'}],
            ['dbt1'], db.get_streams, fonte_atividades='db')
        _, arq = ler_zip(dados)
        rows = csv_linhas(arq['streams.csv'])
        self.assertEqual(len([r for r in rows if r['stream_key'] == 'watts']), 1500)
        self.assertTrue(all(r['reduzido'] == 'true' for r in rows if r['stream_key'] == 'watts'))
        hr = [r for r in rows if r['stream_key'] == 'heartrate']
        self.assertEqual([r['value'] for r in hr], ['1.0', '', '3.0'])   # repr do float, sem perda


class Rota(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._orig = tab.fetch_activities
        tab.fetch_activities = lambda force=False: ACTS
        cls.app = Flask(__name__)
        cls.app.add_url_rule('/api/atividades/export.zip', 'z', tab.api_export_pacote, methods=['POST'])
        cls.c = cls.app.test_client()
        cls._ci = tab.cache_info
        tab.cache_info = lambda: {'fonte': 'db'}
        db.upsert_streams('a1', [{'key': 'watts', 'type': 'watts', 'points': 2}], {'watts': [5, 6]})

    @classmethod
    def tearDownClass(cls):
        tab.fetch_activities = cls._orig
        tab.cache_info = cls._ci

    def test_19_rota_200_com_cabecalhos_e_zip_integro(self):
        r = self.c.post('/api/atividades/export.zip', json={'ids': ['a1', 'a2'], 'filtros': {'typeFilter': 'Ride'}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers['Content-Type'], 'application/zip')
        self.assertEqual(r.headers['X-Atividades'], '2')
        self.assertEqual(r.headers['X-Sem-Streams'], '1')
        self.assertEqual(r.headers['X-Erros-Leitura'], '0')
        z, arq = ler_zip(r.data)
        self.assertIsNone(z.testzip())
        man = json.loads(arq['manifest.json'])
        self.assertEqual(man['filtros']['typeFilter'], 'Ride')
        self.assertTrue(man['sem_chamadas_externas'])

    def test_20_rota_recusa_acima_do_limite_com_413(self):
        ids = [f'q{i}' for i in range(export.LIMITE_ATIVIDADES_PACOTE + 1)]
        r = self.c.post('/api/atividades/export.zip', json={'ids': ids})
        self.assertEqual(r.status_code, 413)
        self.assertIn('Restrinja', r.get_json()['mensagem'])

    def test_21_rota_vazia_e_invalida(self):
        self.assertEqual(self.c.post('/api/atividades/export.zip', json={'ids': []}).status_code, 404)
        self.assertEqual(self.c.post('/api/atividades/export.zip', json={'ids': 'a1'}).status_code, 400)
        self.assertEqual(self.c.post('/api/atividades/export.zip', json={'ids': ['zz']}).status_code, 404)

    def test_22_filtros_desconhecidos_ignorados(self):
        r = self.c.post('/api/atividades/export.zip', json={'ids': ['a1'], 'filtros': {'hack': 'x', 'search': 'a'}})
        man = json.loads(ler_zip(r.data)[1]['manifest.json'])
        self.assertEqual(set(man['filtros']), set(export.FILTROS_PERMITIDOS))
        self.assertNotIn('hack', man['filtros'])


@unittest.skipUnless(shutil.which('node'), 'node nao disponivel')
class ParidadeComFiltrados(unittest.TestCase):
    """Os IDs de cada filtro (funcao filtrados() real) chegam ao pacote sem alteracao."""

    def test_23_pacote_usa_os_mesmos_ids_da_interface(self):
        fx = atividades_fixture()
        orig = tab.fetch_activities
        tab.fetch_activities = lambda force=False: fx
        try:
            app = Flask(__name__)
            app.add_url_rule('/api/atividades', 'a', tab.api_data)
            app.add_url_rule('/api/atividades/export.zip', 'z', tab.api_export_pacote, methods=['POST'])
            c = app.test_client()
            resposta = c.get('/api/atividades').get_json()
        finally:
            tab.fetch_activities = orig
        tmp = tempfile.mkdtemp()
        try:
            cenarios = [{'nome': 'tipo_grande', 'filtros': {'typeFilter': 'Bike'}},
                        {'nome': 'tag_lactato', 'filtros': {'tagFilter': 'Lactato'}},
                        {'nome': 'datas', 'filtros': {'dtDe': '2024-03-11', 'dtAte': '2024-05-02'}}]
            ent = os.path.join(tmp, 'e.json')
            with open(ent, 'w', encoding='utf-8') as f:
                json.dump({'js': tab.JS, 'resposta_api': resposta, 'cenarios': cenarios}, f, ensure_ascii=False)
            p = subprocess.run(['node', os.path.join(HERE, 'js', 'harness_filtros.js'), ent],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            ui = json.loads(p.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        tab.fetch_activities = lambda force=False: fx
        tab.cache_info = lambda: {'fonte': 'db'}
        try:
            for c_ in cenarios:
                ids = ui[c_['nome']]
                if not ids:
                    continue
                r = app.test_client().post('/api/atividades/export.zip', json={'ids': ids})
                if len(ids) > export.LIMITE_ATIVIDADES_PACOTE:
                    self.assertEqual(r.status_code, 413, c_['nome'])   # recusa, nao trunca
                    continue
                self.assertEqual(r.status_code, 200, c_['nome'])
                _, arq = ler_zip(r.data)
                exportados = [x['id'] for x in csv_linhas(arq['atividades.csv'])]
                self.assertEqual(set(exportados), set(ids), c_['nome'])
                self.assertEqual(len(exportados), len(ids), c_['nome'])
        finally:
            tab.fetch_activities = orig


if __name__ == '__main__':
    try:
        unittest.main(exit=False, verbosity=2)
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
