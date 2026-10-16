"""Rota POST /api/atividades/export.csv, regressao do JSON e paridade filtros-UI x CSV. Dados ficticios."""
import csv, io, json, os, shutil, subprocess, sys, tempfile, unittest

import tempfile  # noqa: E402
os.environ.setdefault('SQLITE_PATH', os.path.join(tempfile.mkdtemp(prefix='rota_test_'), 'teste.db'))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
from flask import Flask                      # noqa: E402
import tabs.tab_atividades as tab            # noqa: E402
from fixtures_atividades import atividades_fixture  # noqa: E402
from config import TYPE_MAP                   # noqa: E402

FIXTURE = atividades_fixture()

def app_teste():
    app = Flask(__name__)
    app.add_url_rule('/api/atividades', 'api_atividades', tab.api_data)
    app.add_url_rule('/api/atividades/export.csv', 'api_atividades_export_csv',
                     tab.api_export_csv, methods=['POST'])
    return app

def parse(corpo):
    return list(csv.DictReader(io.StringIO(corpo.decode('utf-8-sig'), newline='')))

class RotaCsv(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig = tab.fetch_activities
        tab.fetch_activities = lambda force=False: FIXTURE
        cls.c = app_teste().test_client()

    @classmethod
    def tearDownClass(cls):
        tab.fetch_activities = cls._orig

    def post(self, corpo):
        return self.c.post('/api/atividades/export.csv', json=corpo)

    def test_01_pedido_invalido_400(self):
        self.assertEqual(self.post({'ids': 'i1'}).status_code, 400)
        self.assertEqual(self.post({'ids': [True]}).status_code, 400)
        self.assertEqual(self.c.post('/api/atividades/export.csv', data='x').status_code, 400)

    def test_02_lista_vazia_404_com_mensagem(self):
        r = self.post({'ids': []})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()['status'], 'vazio')
        self.assertIn('filtros', r.get_json()['mensagem'])

    def test_03_ids_inexistentes_404(self):
        r = self.post({'ids': ['nao-existe']})
        self.assertEqual(r.status_code, 404)

    def test_04_csv_com_nome_do_intervalo_e_cabecalhos(self):
        r = self.post({'ids': ['i100001', 'i100005']})
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r.headers['Content-Type'])
        self.assertEqual(r.headers['Content-Disposition'],
                         'attachment; filename="atividades_2024-03-10_a_2025-06-30.csv"')
        self.assertEqual(r.headers['X-Linhas'], '2')
        self.assertEqual(r.headers['X-Ids-Ausentes'], '0')

    def test_05_nome_com_data_unica(self):
        r = self.post({'ids': ['i100002']})
        self.assertEqual(r.headers['Content-Disposition'],
                         'attachment; filename="atividades_2024-03-11.csv"')

    def test_06_nao_inclui_todos_quando_pedido_so_alguns(self):
        r = self.post({'ids': ['i100003']})
        self.assertEqual([l['id'] for l in parse(r.data)], ['i100003'])

    def test_07_regressao_json_inalterado(self):
        j = self.c.get('/api/atividades').get_json()
        self.assertEqual(j['status'], 'OK')
        self.assertEqual(j['total'], len(FIXTURE))
        self.assertEqual(len(j['activities']), len(FIXTURE))
        chaves = set(j['activities'][0].keys())
        self.assertEqual(chaves, {'id', 'date', 'name', 'type', 'type_raw', 'duration_min', 'distance_km',
                                  'kj', 'training_load', 'avg_watts', 'ftp', 'rpe', 'xss', 'avg_hr',
                                  'max_hr', 'source', 'tags'})
        self.assertNotIn('trimp', chaves)            # resumo continua igual: trimp so no CSV

    def test_08_pagina_html_tem_botao_e_link_json(self):
        with app_teste().test_request_context('/atividades'):
            html = tab.render()
        if not isinstance(html, str):
            html = html.get_data(as_text=True)
        self.assertIn('Exportar CSV', html)
        self.assertIn('id="btnCsv"', html)
        self.assertIn('href="/api/atividades" target="_blank">JSON</a>', html)

    def test_09_sem_credenciais_nas_respostas(self):
        r = self.post({'ids': ['i100001']})
        self.assertNotIn(b'SEGREDO-FICTICIO', r.data)
        self.assertNotIn(b'cliente-fic', r.data)

    def test_10_rota_registrada_no_app(self):
        with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn("@app.route('/api/atividades/export.csv', methods=['POST'])", src)


@unittest.skipUnless(shutil.which('node'), 'node nao disponivel')
class ParidadeFiltrosUi(unittest.TestCase):
    """Os IDs filtrados pela propria tabela (funcao filtrados() real) = os IDs exportados no CSV."""

    @classmethod
    def setUpClass(cls):
        cls._orig = tab.fetch_activities
        tab.fetch_activities = lambda force=False: FIXTURE
        with app_teste().test_request_context('/'):
            resp = tab.api_data()
        cls.resumo = resp.get_json()
        cls.c = app_teste().test_client()

    @classmethod
    def tearDownClass(cls):
        tab.fetch_activities = cls._orig

    def cenarios(self):
        return [
            {'nome': 'sem_filtros', 'filtros': {}},
            {'nome': 'tipo_bike', 'filtros': {'typeFilter': 'Bike'}},
            {'nome': 'tag_lactato', 'filtros': {'tagFilter': 'Lactato'}},
            {'nome': 'datas', 'filtros': {'dtDe': '2024-03-11', 'dtAte': '2024-05-02'}},
            {'nome': 'nome_treino', 'filtros': {'search': 'treino'}},
            {'nome': 'combinado', 'filtros': {'typeFilter': 'Run', 'dtAte': '2024-12-31'}},
            {'nome': 'vazio', 'filtros': {'search': 'nao-existe-xyz'}},
        ]

    def test_ids_da_interface_igual_ids_do_csv(self):
        tmp = tempfile.mkdtemp()
        try:
            entrada = os.path.join(tmp, 'entrada.json')
            cen = self.cenarios()
            with open(entrada, 'w', encoding='utf-8') as f:
                json.dump({'js': tab.JS, 'resposta_api': self.resumo, 'cenarios': cen}, f, ensure_ascii=False)
            harness = os.path.join(HERE, 'js', 'harness_filtros.js')
            p = subprocess.run(['node', harness, entrada], capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            ui = json.loads(p.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        # conjuntos esperados, calculados a mao a partir das fixtures
        self.assertEqual(set(ui['tag_lactato']), {'i100001'})
        self.assertEqual(set(ui['datas']), {'i100002', 'i100003'})
        bulk = {a['id'] for a in FIXTURE if a['id'].startswith('i2')}
        self.assertEqual(set(ui['nome_treino']), {'i100001'} | bulk)
        self.assertEqual(set(ui['sem_filtros']), {a['id'] for a in FIXTURE})
        self.assertEqual(ui['vazio'], [])
        esperado_run = {a['id'] for a in FIXTURE if TYPE_MAP.get(a['type']) == 'Run'
                        and a['start_date_local'][:10] <= '2024-12-31'}
        self.assertEqual(set(ui['combinado']), esperado_run)

        for c in cen:
            ids = ui[c['nome']]
            r = self.c.post('/api/atividades/export.csv', json={'ids': ids})
            if not ids:
                self.assertEqual(r.status_code, 404, c['nome'])
                continue
            self.assertEqual(r.status_code, 200, c['nome'])
            exportados = [l['id'] for l in parse(r.data)]
            self.assertEqual(set(exportados), set(ids), c['nome'])
            self.assertEqual(len(exportados), len(ids), c['nome'])


if __name__ == '__main__':
    unittest.main()
