"""Exportacao completa de streams originais. Respostas sinteticas, sem rede.

Formato simulado: lista de streams com type, name, data, unit, custom, valueTypeIsArray,
allNull, mais 'time' como stream proprio (formato inferido dos consumidores do codigo).
"""
import csv, hashlib, io, json, os, shutil, subprocess, sys, tempfile, threading, time, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
_TMP = tempfile.mkdtemp(prefix='xc_test_')
os.environ['SQLITE_PATH'] = os.path.join(_TMP, 'teste.db')
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)

import socket          # noqa: E402
import requests        # noqa: E402
from flask import Flask  # noqa: E402
import api_client      # noqa: E402
import exportacao_completa as xc  # noqa: E402
import tabs.tab_atividades as tab  # noqa: E402
import export          # noqa: E402
from fixtures_atividades import atividades_fixture  # noqa: E402

def _proibido(*a, **k):
    raise AssertionError('chamada externa proibida nos testes')
requests.get = _proibido
requests.post = _proibido
socket.socket.connect = _proibido  # noqa
api_client.icu_get = _proibido
api_client.parse_streams = _proibido   # a exportacao nao pode passar por aqui
api_client.downsample = _proibido

ACTS = [{'id': 'a', 'start_date_local': '2025-01-01T07:00:00', 'type': 'Ride', 'name': 'A', 'trimp': 0},
        {'id': 'b', 'start_date_local': '2025-01-02T07:00:00', 'type': 'Ride', 'name': 'B'},
        {'id': 'c', 'start_date_local': '2025-01-03T07:00:00', 'type': 'Run', 'name': 'C', 'trimp': None},
        {'id': 'd', 'start_date_local': '2025-01-04T07:00:00', 'type': 'Run', 'name': 'D'}]


def resp(status, corpo=None, retry=None):
    return {'status': status, 'retry_after': retry, 'corpo': corpo}


class FakeAPI:
    """Roteiro por atividade: lista de respostas ou excecoes; depois do roteiro, usa 'padrao'."""

    def __init__(self, roteiro=None, padrao=None, atraso=0.0):
        self.roteiro = {k: list(v) for k, v in (roteiro or {}).items()}
        self.padrao = padrao or {}
        self.chamadas = []
        self.atraso = atraso
        self._lock = threading.Lock()
        self.em_voo = 0
        self.pico = 0

    def __call__(self, aid):
        with self._lock:
            self.chamadas.append(aid)
            self.em_voo += 1
            self.pico = max(self.pico, self.em_voo)
        try:
            if self.atraso:
                time.sleep(self.atraso)
            fila = self.roteiro.get(aid)
            if fila:
                item = fila.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            corpo = self.padrao.get(aid, [])
            return resp(200, corpo)
        finally:
            with self._lock:
                self.em_voo -= 1


def ler_zip(caminho):
    z = zipfile.ZipFile(caminho)
    return z, {n: z.read(n) for n in z.namelist()}


def linhas_csv(b):
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'), newline='')))


class Base(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp(dir=_TMP, prefix='job_')
        self._cfg = dict(xc.CONFIG)
        xc.CONFIG.update(intervalo_s=0.0, tentativas=3, concorrencia=2)
        self._dir = xc.DIR_BASE
        xc.DIR_BASE = self.base

    def tearDown(self):
        xc.CONFIG.clear(); xc.CONFIG.update(self._cfg)
        xc.DIR_BASE = self._dir

    def rodar(self, api, ids=('a', 'b', 'c', 'd'), local=None, espera=None):
        job = xc.novo_job(ACTS, list(ids), {'typeFilter': ''}, sincrono=True, buscar=api,
                          espera=espera or (lambda s: None), local=local or (lambda a: None),
                          base=self.base)
        return job

    def zip_do(self, job):
        caminho, manifest = xc.montar_pacote(job)
        z, arq = ler_zip(caminho)
        return z, arq, manifest


class PreservacaoSemDownsample(Base):

    def test_01_todos_os_pontos_sem_downsample(self):
        pontos = [i % 997 for i in range(3000)]
        api = FakeAPI(padrao={'a': [{'type': 'watts', 'data': pontos, 'unit': 'W', 'custom': False}]})
        job = self.rodar(api, ids=('a',))
        _, arq, man = self.zip_do(job)
        rows = [r for r in linhas_csv(arq['streams.csv']) if r['stream_key'] == 'watts']
        self.assertEqual(len(rows), 3000)
        self.assertEqual([int(r['value']) for r in rows], pontos)
        self.assertTrue(all(r['reduzido'] == 'false' and r['origem'] == 'original_api' for r in rows))
        self.assertEqual(rows[0]['pontos_originais'], '3000')

    def test_02_tempo_preservado_e_alinhado_so_com_mesmo_comprimento(self):
        api = FakeAPI(padrao={'a': [
            {'type': 'time', 'data': [0, 1, 2, 3, 4]},
            {'type': 'watts', 'data': [10, 20, 30, 40, 50]},
            {'type': 'heartrate', 'data': [100, 101, 102, 103, 104, 105, 106]}]})
        job = self.rodar(api, ids=('a',))
        _, arq, _ = self.zip_do(job)
        rows = linhas_csv(arq['streams.csv'])
        w = [r for r in rows if r['stream_key'] == 'watts']
        self.assertEqual([r['time_seconds'] for r in w], ['0', '1', '2', '3', '4'])
        hr = [r for r in rows if r['stream_key'] == 'heartrate']
        self.assertTrue(all(r['time_seconds'] == '' for r in hr))   # comprimentos diferentes: sem alinhar

    def test_03_unidade_e_metadados_preservados(self):
        api = FakeAPI(padrao={'a': [{'type': 'watts', 'data': [1, 2], 'unit': 'W', 'custom': True,
                                     'name': 'Pm', 'sensor_name': 'PM-01'}]})
        job = self.rodar(api, ids=('a',))
        _, arq, _ = self.zip_do(job)
        r = [x for x in linhas_csv(arq['streams.csv']) if x['stream_key'] == 'watts'][0]
        self.assertEqual(r['unit'], 'W')
        self.assertEqual(r['custom'], 'true')
        meta = json.loads(arq['streams_metadados.json'])
        self.assertEqual(meta[0]['stream']['sensor_name'], 'PM-01')
        self.assertNotIn('data', meta[0]['stream'])

    def test_04_unidade_ausente_fica_vazia(self):
        api = FakeAPI(padrao={'a': [{'type': 'watts', 'data': [1]}]})
        job = self.rodar(api, ids=('a',))
        _, arq, _ = self.zip_do(job)
        r = linhas_csv(arq['streams.csv'])[0]
        self.assertEqual(r['unit'], '')

    def test_05_arrays_estruturados_e_nulos(self):
        api = FakeAPI(padrao={'a': [
            {'type': 'latlng', 'data': [[1.0, 2.0], [1.1, 2.1]], 'valueTypeIsArray': True},
            {'type': 'smo2', 'data': [60.0, None, 61.5], 'unit': '%'},
            {'type': 'dfa_a1', 'data': [0.9, 1.0], 'data2': [5, 6]}]})
        job = self.rodar(api, ids=('a',))
        _, arq, _ = self.zip_do(job)
        est = json.loads(arq['streams_estruturados.json'])
        tipos = sorted(x['stream']['type'] for x in est)
        self.assertEqual(tipos, ['dfa_a1', 'latlng'])
        latlng = [x for x in est if x['stream']['type'] == 'latlng'][0]
        self.assertEqual(latlng['stream']['data'], [[1.0, 2.0], [1.1, 2.1]])
        smo2 = [r for r in linhas_csv(arq['streams.csv']) if r['stream_key'] == 'smo2']
        self.assertEqual([r['value'] for r in smo2], ['60.0', '', '61.5'])

    def test_06_atividade_sem_streams_nao_e_erro(self):
        api = FakeAPI(padrao={'a': []})
        job = self.rodar(api, ids=('a',))
        _, arq, man = self.zip_do(job)
        self.assertEqual(job.estado['a']['status'], 'concluida_sem_streams')
        self.assertEqual(man['atividades']['concluidas'], 1)
        self.assertEqual(man['atividades']['erros'], 0)
        self.assertEqual(linhas_csv(arq['streams.csv']), [])

    def test_07_metadados_e_estruturados_sem_perda(self):
        api = FakeAPI(padrao={'a': [{'type': 'watts', 'data': [5], 'custom': True}]})
        job = self.rodar(api, ids=('a',))
        _, arq, _ = self.zip_do(job)
        self.assertEqual(json.loads(arq['streams_estruturados.json']), [])


class Falhas(Base):

    def test_08_erros_por_atividade_com_motivo(self):
        api = FakeAPI(roteiro={'a': [resp(500)] * 3, 'b': [TimeoutError('x')] * 3,
                               'c': [resp(404)]},
                      padrao={'d': [{'type': 'watts', 'data': [1]}]})
        job = self.rodar(api)
        est = job.estado
        self.assertEqual(est['a']['status'], 'erro_api'); self.assertEqual(est['a']['motivo'], 'HTTP 500')
        self.assertEqual(est['b']['status'], 'erro_api'); self.assertEqual(est['b']['motivo'], 'timeout')
        self.assertEqual(est['c']['status'], 'erro_api'); self.assertEqual(est['c']['motivo'], 'HTTP 404')
        self.assertEqual(api.chamadas.count('c'), 1)           # 404 nao e repetido
        _, _, man = self.zip_do(job)
        self.assertFalse(man['pacote_completo_original'])
        self.assertEqual({f['activity_id'] for f in man['falhas']}, {'a', 'b', 'c'})

    def test_09_http_429_respeita_retry_after(self):
        esperas = []
        api = FakeAPI(roteiro={'a': [resp(429, retry='7'), resp(200, [{'type': 'watts', 'data': [1]}])]})
        job = self.rodar(api, ids=('a',), espera=esperas.append)
        self.assertIn(7, esperas)
        self.assertEqual(job.estado['a']['status'], 'concluida')
        self.assertEqual(api.chamadas.count('a'), 2)

    def test_10_retry_after_em_data_http(self):
        self.assertEqual(xc._espera_retry_after(None), xc.RETRY_AFTER_PADRAO_S)
        self.assertEqual(xc._espera_retry_after('12'), 12)
        self.assertEqual(xc._espera_retry_after('lixo'), xc.RETRY_AFTER_PADRAO_S)
        self.assertLessEqual(xc._espera_retry_after('999999'), xc.RETRY_AFTER_MAX_S)

    def test_11_resposta_sem_lista_vira_erro_de_formato(self):
        api = FakeAPI(roteiro={'a': [resp(200, {'algo': 'inesperado'})]})
        job = self.rodar(api, ids=('a',))
        self.assertEqual(job.estado['a']['status'], 'erro_formato')

    def test_12_retomada_refaz_so_as_falhas(self):
        api = FakeAPI(roteiro={'b': [resp(500)] * 3}, padrao={'a': [{'type': 'watts', 'data': [1]}],
                                                              'b': [{'type': 'watts', 'data': [2]}]})
        job = self.rodar(api, ids=('a', 'b'))
        self.assertEqual(job.estado['b']['status'], 'erro_api')
        antes = api.chamadas.count('a')
        xc.retomar_job(job, sincrono=True)
        self.assertEqual(job.estado['b']['status'], 'concluida')
        self.assertEqual(api.chamadas.count('a'), antes)        # concluida nao foi refeita
        _, _, man = self.zip_do(job)
        self.assertTrue(man['pacote_completo_original'])

    def test_13_fallback_local_marcado_como_reduzido(self):
        api = FakeAPI(roteiro={'a': [resp(500)] * 3})
        local = lambda aid: [{'type': 'watts', 'name': None, 'custom': False, 'points': 2000,
                              'data': [1.0] * 1500}] if aid == 'a' else None
        job = self.rodar(api, ids=('a',), local=local)
        self.assertEqual(job.estado['a']['status'], 'erro_api')
        self.assertTrue(job.estado['a']['fallback_local'])
        _, arq, man = self.zip_do(job)
        rows = linhas_csv(arq['streams.csv'])
        self.assertTrue(all(r['origem'] == 'local_reduzido' and r['reduzido'] == 'true' for r in rows))
        self.assertEqual(rows[0]['pontos_originais'], '2000')
        self.assertFalse(man['pacote_completo_original'])


class Concorrencia(Base):

    def test_14_concorrencia_limitada(self):
        xc.CONFIG['concorrencia'] = 2
        api = FakeAPI(padrao={k: [{'type': 'watts', 'data': [1]}] for k in 'abcdefgh'}, atraso=0.02)
        job = self.rodar(api, ids=tuple('abcdefgh'))
        self.assertLessEqual(api.pico, 2)
        self.assertGreaterEqual(api.pico, 1)
        self.assertEqual(job.resumo()['concluidas'], 8)

    def test_15_limitador_respeita_intervalo(self):
        esperas = []
        lim = xc.Limitador(0.5, relogio=lambda: 0.0, espera=esperas.append)
        for _ in range(5):
            lim.aguardar()
        self.assertAlmostEqual(sum(esperas), 0.5 * (0 + 1 + 2 + 3 + 4))

    def test_16_padrao_abaixo_do_limite_da_api(self):
        self.assertLessEqual(60 / 0.5 * 1, 2500 / 15)   # 120/min <= 166/min


class ProtecaoEEstado(Base):

    def test_17_um_job_por_vez(self):
        sair = threading.Event()
        def bloqueado(aid):
            sair.wait(5)
            return resp(200, [])
        xc.novo_job(ACTS, ['a'], {}, sincrono=False, buscar=bloqueado, base=self.base)
        time.sleep(0.05)
        with self.assertRaises(xc.JobEmAndamento):
            xc.novo_job(ACTS, ['b'], {}, sincrono=True, buscar=lambda a: resp(200, []), base=self.base)
        sair.set()
        for _ in range(100):
            if not xc._ativo():
                break
            time.sleep(0.02)

    def test_18_job_id_invalido_nao_acessa_disco(self):
        for ruim in ['../../etc', 'ABC', '', None, 'a' * 24 + '/x']:
            with self.assertRaises(xc.JobInexistente):
                xc.obter_job(ruim, base=self.base)

    def test_19_limpeza_remove_so_antigos(self):
        velho = os.path.join(self.base, 'a' * 24); novo = os.path.join(self.base, 'b' * 24)
        os.makedirs(velho); os.makedirs(novo)
        antigo = time.time() - 3 * 86400
        os.utime(velho, (antigo, antigo))
        self.assertEqual(xc.limpar_antigos(horas=24, base=self.base), 1)
        self.assertFalse(os.path.exists(velho)); self.assertTrue(os.path.exists(novo))

    def test_20_estado_persistido_e_retomavel_apos_reinicio(self):
        api = FakeAPI(roteiro={'b': [resp(500)] * 3}, padrao={'a': [{'type': 'watts', 'data': [1]}]})
        job = self.rodar(api, ids=('a', 'b'))
        xc._REGISTRO.pop(job.id, None)                  # simula reinicio do processo
        recarregado = xc.obter_job(job.id, base=self.base)
        self.assertEqual(recarregado.estado['a']['status'], 'concluida')
        self.assertEqual(recarregado.estado['b']['status'], 'erro_api')

    def test_21_manifest_contagens_por_estado(self):
        api = FakeAPI(roteiro={'b': [resp(500)] * 3}, padrao={'a': [{'type': 'watts', 'data': [1]}], 'c': []})
        job = self.rodar(api, ids=('a', 'b', 'c'))
        _, _, man = self.zip_do(job)
        at = man['atividades']
        self.assertEqual((at['selecionadas'], at['concluidas'], at['sem_streams'], at['erros']), (3, 2, 1, 1))
        self.assertFalse(man['pacote_completo_original'])
        self.assertEqual(man['chamadas_api_nesta_exportacao'], api.chamadas.__len__())
        self.assertFalse(man['sem_chamadas_externas'])


class IntegridadeEZip(Base):

    def test_22_zip_e_hashes(self):
        api = FakeAPI(padrao={'a': [{'type': 'watts', 'data': [1, 2]}], 'b': []})
        job = self.rodar(api, ids=('a', 'b'))
        z, arq, man = self.zip_do(job)
        self.assertIsNone(z.testzip())
        self.assertEqual(set(arq), {'atividades.csv', 'atividades.jsonl', 'streams.csv',
                                    'streams_metadados.json', 'streams_estruturados.json', 'manifest.json'})
        for entrada in man['arquivos']:
            self.assertEqual(hashlib.sha256(arq[entrada['nome']]).hexdigest(), entrada['sha256'])
            self.assertEqual(len(arq[entrada['nome']]), entrada['bytes'])

    def test_23_jsonl_sem_derivados_nem_segredos(self):
        acts = [{'id': 'a', 'start_date_local': '2025-01-01', 'type': 'Ride', '_kj': 1.0,
                 'access_token': 'SEGREDO-FICTICIO', 'trimp': 0}]
        job = xc.novo_job(acts, ['a'], {}, sincrono=True, buscar=lambda a: resp(200, []),
                          base=self.base, espera=lambda s: None)
        _, arq, _ = self.zip_do(job)
        linha = json.loads(arq['atividades.jsonl'].decode().splitlines()[0])
        self.assertNotIn('_kj', linha); self.assertNotIn('access_token', linha)
        self.assertEqual(linha['trimp'], 0)

    def test_24_arquivos_temporarios_removidos(self):
        api = FakeAPI(padrao={'a': [{'type': 'watts', 'data': [1]}]})
        job = self.rodar(api, ids=('a',))
        self.zip_do(job)
        restos = [n for n in os.listdir(job.dir) if n.startswith('tmp_')]
        self.assertEqual(restos, [])


class SemDownsample(unittest.TestCase):

    def test_29_codigo_da_exportacao_nao_usa_parse_streams_nem_downsample(self):
        """Analise da arvore sintatica: referencias reais, nao texto de comentarios."""
        import ast
        arvore = ast.parse(open(os.path.join(ROOT, 'exportacao_completa.py'), encoding='utf-8').read())
        nomes = {n.id for n in ast.walk(arvore) if isinstance(n, ast.Name)}
        atributos = {n.attr for n in ast.walk(arvore) if isinstance(n, ast.Attribute)}
        self.assertNotIn('parse_streams', nomes | atributos)
        self.assertNotIn('downsample', nomes | atributos)
        # db.get_streams so pode aparecer no fallback local (quando a API falha)
        for no in ast.walk(arvore):
            if isinstance(no, ast.FunctionDef) and no.name != '_local_padrao':
                for sub in ast.walk(no):
                    if isinstance(sub, ast.Attribute) and sub.attr == 'get_streams':
                        self.fail(f'db.get_streams fora do fallback local: {no.name}')


class Rotas(Base):

    @classmethod
    def setUpClass(cls):
        cls._orig_fetch = tab.fetch_activities
        cls._orig_api = xc.buscar_streams_api
        tab.fetch_activities = lambda force=False: atividades_fixture()
        app = Flask(__name__)
        app.add_url_rule('/api/exportacao/iniciar', 'i', tab.api_exportacao_iniciar, methods=['POST'])
        app.add_url_rule('/api/exportacao/<job_id>/estado', 'e', tab.api_exportacao_estado)
        app.add_url_rule('/api/exportacao/<job_id>/retomar', 'r', tab.api_exportacao_retomar, methods=['POST'])
        app.add_url_rule('/api/exportacao/<job_id>/download', 'd', tab.api_exportacao_download)
        cls.c = app.test_client()

    @classmethod
    def tearDownClass(cls):
        tab.fetch_activities = cls._orig_fetch
        xc.buscar_streams_api = cls._orig_api

    def setUp(self):
        super().setUp()
        xc.buscar_streams_api = lambda aid: resp(200, [{'type': 'watts', 'data': [1, 2]}])

    def esperar(self, job_id):
        for _ in range(500):
            j = self.c.get(f'/api/exportacao/{job_id}/estado').get_json()
            if j['pronto'] or not j['resumo']['rodando']:
                return j
            time.sleep(0.02)
        self.fail('exportacao nao terminou')

    def test_25_ids_nao_autorizados_rejeitados(self):
        r = self.c.post('/api/exportacao/iniciar', json={'ids': ['nao-existe']})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.post('/api/exportacao/iniciar', json={'ids': []}).status_code, 400)
        self.assertEqual(self.c.post('/api/exportacao/iniciar', json={'ids': 'x'}).status_code, 400)

    def test_26_fluxo_completo_com_filtros_e_download(self):
        ids = ['i100001', 'i100002']
        r = self.c.post('/api/exportacao/iniciar', json={'ids': ids, 'filtros': {'typeFilter': 'Bike'}})
        self.assertEqual(r.status_code, 202)
        job_id = r.get_json()['job_id']
        estado = self.esperar(job_id)
        self.assertEqual(estado['resumo']['selecionadas'], 2)
        d = self.c.get(f'/api/exportacao/{job_id}/download')
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d.mimetype, 'application/zip')
        z, arq = ler_zip(io.BytesIO(d.data) if False else _grava(d.data))
        man = json.loads(arq['manifest.json'])
        self.assertEqual(man['filtros']['typeFilter'], 'Bike')
        self.assertEqual({x['id'] for x in linhas_csv(arq['atividades.csv'])}, set(ids))

    def test_27_job_id_malformado_404(self):
        self.assertEqual(self.c.get('/api/exportacao/..%2F..%2Fetc/estado').status_code, 404)
        self.assertEqual(self.c.get('/api/exportacao/abc/download').status_code, 404)

    def test_28_paridade_com_filtrados_real(self):
        if not shutil.which('node'):
            self.skipTest('node indisponivel')
        fx = atividades_fixture()
        tab.fetch_activities = lambda force=False: fx
        app = Flask(__name__)
        app.add_url_rule('/api/atividades', 'a', tab.api_data)
        resposta = app.test_client().get('/api/atividades').get_json()
        tmp = tempfile.mkdtemp()
        try:
            ent = os.path.join(tmp, 'e.json')
            cen = [{'nome': 'lactato', 'filtros': {'tagFilter': 'Lactato'}},
                   {'nome': 'datas', 'filtros': {'dtDe': '2024-03-11', 'dtAte': '2024-05-02'}}]
            with open(ent, 'w', encoding='utf-8') as f:
                json.dump({'js': tab.JS, 'resposta_api': resposta, 'cenarios': cen}, f, ensure_ascii=False)
            p = subprocess.run(['node', os.path.join(HERE, 'js', 'harness_filtros.js'), ent],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            ui = json.loads(p.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            tab.fetch_activities = lambda force=False: fx
        for c_ in cen:
            ids = ui[c_['nome']]
            r = self.c.post('/api/exportacao/iniciar', json={'ids': ids})
            self.assertEqual(r.status_code, 202, c_['nome'])
            job_id = r.get_json()['job_id']
            self.esperar(job_id)
            d = self.c.get(f'/api/exportacao/{job_id}/download')
            _, arq = ler_zip(_grava(d.data))
            self.assertEqual({x['id'] for x in linhas_csv(arq['atividades.csv'])}, set(ids), c_['nome'])


def _grava(dados):
    p = tempfile.mktemp(dir=_TMP, suffix='.zip')
    with open(p, 'wb') as f:
        f.write(dados)
    return p


if __name__ == '__main__':
    try:
        unittest.main(exit=False, verbosity=2)
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
