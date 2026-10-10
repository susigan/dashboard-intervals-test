"""Peso da aba corporal vem da aba diaria (Respostas ao formulario 1, coluna Peso).

Dados sinteticos. A camada de rede (sheets_client._ler_aba) e substituida,
entao nada toca o Google Sheets.

Executar a partir da raiz do repositorio:
    python3 -m unittest tests/corporal/test_peso_fonte.py -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

import corporal  # noqa: E402
import sheets_client as sheets  # noqa: E402


def dia(k):
    return (date.today() - timedelta(days=k)).isoformat()


def dia_br(k):
    return (date.today() - timedelta(days=k)).strftime('%d/%m/%Y')


CABECALHO_W = ['Carimbo de data/hora', 'Data', 'Peso', 'FAT', 'HRV']
CABECALHO_C = ['Data', 'Peso', 'BF', 'Calorias', 'Carb', 'Fat', 'Ptn', 'Net']


class TabelasFalsas:
    """Substitui sheets_client._ler_aba por tabelas em memoria."""

    def __init__(self, wellness_rows, food_rows):
        self.w, self.c = wellness_rows, food_rows
        self.chamadas = []

    def __call__(self, url, aba):
        self.chamadas.append(aba)
        if 'Respostas' in aba:
            return self.w, None
        return self.c, None


class _Base(unittest.TestCase):
    def setUp(self):
        self._orig = sheets._ler_aba
        sheets._cache.update({'wellness': None, 'corporal': None, 'time': None})

    def tearDown(self):
        sheets._ler_aba = self._orig

    def usar(self, wellness_rows, food_rows):
        f = TabelasFalsas(wellness_rows, food_rows)
        sheets._ler_aba = f
        return f


class TestFontePeso(_Base):

    def test_1_peso_diario_prevalece_sobre_consolidado(self):
        """Pesos diferentes nas duas fontes: usa o da aba diaria."""
        self.usar([CABECALHO_W, [dia_br(2), dia_br(2), '81,4', '', '60']],
                  [CABECALHO_C, [dia_br(2), '80,0', '', '2.100', '250', '70', '150', '-300']])
        w, _ = sheets.carregar_wellness()
        c, _ = sheets.carregar_corporal()
        linhas = corporal.preparar(c, w)
        dia2 = [r for r in linhas if r['date'] == dia(2)][0]
        self.assertEqual(dia2['peso'], 81.4)

    def test_2_coluna_peso_reconhecida_e_convertida(self):
        """Cabecalho 'Peso' e formato PT-BR ('81,4') sao lidos corretamente."""
        self.usar([CABECALHO_W,
                   [dia_br(3), dia_br(3), '80,9', '', ''],
                   [dia_br(2), dia_br(2), '80.2', '', ''],       # ponto decimal
                   [dia_br(1), dia_br(1), '999', '', ''],        # fora da faixa 30-200
                   [dia_br(0), dia_br(0), '', '', ''],           # vazio
                   ['', '', '77,0', '', '']],                    # sem data
                  [CABECALHO_C])
        w, _ = sheets.carregar_wellness()
        por_data = {r['date']: r.get('peso') for r in w}
        self.assertEqual(por_data[dia(3)], 80.9)
        self.assertEqual(por_data[dia(2)], 80.2)
        self.assertIsNone(por_data[dia(1)])
        self.assertIsNone(por_data[dia(0)])
        self.assertNotIn(None, por_data.keys())               # linha sem data descartada
        self.assertEqual(len(w), 4)

    def test_3_bf_mantem_prioridade_do_formulario(self):
        """BF: formulario (FAT) prevalece sobre o consolidado; dias so no formulario entram."""
        self.usar([CABECALHO_W,
                   [dia_br(2), dia_br(2), '81,4', '18,5', ''],
                   [dia_br(1), dia_br(1), '80,9', '', ''],       # sem FAT
                   [dia_br(0), dia_br(0), '', '17,9', '']],      # so FAT
                  [CABECALHO_C,
                   [dia_br(2), '80,0', '20,0', '2.100', '250', '70', '150', '-300']])
        w, _ = sheets.carregar_wellness()
        c, _ = sheets.carregar_corporal()
        por_data = {r['date']: r for r in corporal.preparar(c, w)}
        self.assertEqual(por_data[dia(2)]['bf'], 18.5)        # prevalece o FAT, nao 20.0
        self.assertEqual(por_data[dia(0)]['bf'], 17.9)        # dia so no formulario
        self.assertIsNone(por_data.get(dia(1), {}).get('bf'))  # sem FAT: nao inventa

    def test_4_calorias_net_macros_continuam_do_consolidado(self):
        """Calorias, net e macros vem so de Consolidado_Comida, inclusive com a aba diaria presente."""
        self.usar([CABECALHO_W,
                   [dia_br(2), dia_br(2), '81,4', '18,5', ''],
                   [dia_br(0), dia_br(0), '', '17,9', '']],     # dia 0 so existe no formulario
                  [CABECALHO_C,
                   [dia_br(2), '80,0', '', '2.100', '250', '70', '150', '-300'],
                   [dia_br(1), '79,5', '', '2.300', '260', '80', '160', '-100']])
        w, _ = sheets.carregar_wellness()
        c, _ = sheets.carregar_corporal()
        por_data = {r['date']: r for r in corporal.preparar(c, w)}
        self.assertEqual(por_data[dia(2)]['calorias'], 2100.0)
        self.assertEqual(por_data[dia(2)]['net'], -300.0)
        self.assertEqual(por_data[dia(2)]['carb'], 250.0)
        self.assertEqual(por_data[dia(1)]['calorias'], 2300.0)
        # dia so no formulario: BF sim, calorias e macros nao
        self.assertEqual(por_data[dia(0)]['bf'], 17.9)
        for campo in ('calorias', 'net', 'carb', 'fat', 'ptn'):
            self.assertIsNone(por_data[dia(0)].get(campo), campo)

    def test_5_sem_invencao_de_dados(self):
        """Sem peso diario = sem peso; nada de interpolacao, fallback ou datas futuras."""
        self.usar([CABECALHO_W,
                   [dia_br(4), dia_br(4), '81,0', '', ''],
                   [dia_br(0), dia_br(0), '', '', ''],
                   ['', '', '', '', ''],
                   [(date.today() + timedelta(days=3)).strftime('%d/%m/%Y'),
                    (date.today() + timedelta(days=3)).strftime('%d/%m/%Y'), '120,0', '', '']],
                  [CABECALHO_C,
                   [dia_br(4), '80,0', '', '2.000', '250', '70', '150', '-200'],
                   [dia_br(3), '79,5', '', '2.000', '250', '70', '150', '-200'],
                   [dia_br(2), '79,0', '', '2.000', '250', '70', '150', '-200']])
        w, _ = sheets.carregar_wellness()
        c, _ = sheets.carregar_corporal()
        linhas = corporal.preparar(c, w)
        por_data = {r['date']: r for r in linhas}
        self.assertEqual(por_data[dia(4)]['peso'], 81.0)      # diario, nao o 80.0 do consolidado
        self.assertIsNone(por_data[dia(3)]['peso'])           # sem peso diario: nao usa 79.5
        self.assertIsNone(por_data[dia(2)]['peso'])           # idem 79.0
        self.assertNotIn((date.today() + timedelta(days=3)).isoformat(), por_data)  # futuro fora
        # calorias de dias sem peso continuam (fonte separada)
        self.assertEqual(por_data[dia(3)]['calorias'], 2000.0)

    def test_5b_aba_diaria_vazia_nao_recebe_peso_do_consolidado(self):
        self.usar([CABECALHO_W], [CABECALHO_C,
                  [dia_br(2), '80,0', '', '2.000', '250', '70', '150', '-200']])
        w, _ = sheets.carregar_wellness()
        c, _ = sheets.carregar_corporal()
        linhas = corporal.preparar(c, w)
        self.assertTrue(linhas)
        self.assertTrue(all(r['peso'] is None for r in linhas))


class TestPayloadApi(_Base):
    """Formato de /api/corporal mantido (chaves iguais ao esperado)."""

    CHAVES_TOPO = {'agrupado', 'bandas', 'erros_sheets', 'lag', 'linhas', 'macros',
                   'periodo', 'r7', 'resumo', 'sheets_ok', 'status', 'variacao'}
    CHAVES_LINHA = {'date', 'peso', 'bf', 'calorias', 'carb', 'fat', 'ptn', 'net'}

    def test_6_payload_compativel(self):
        from flask import Flask
        import tabs.tab_corporal as tc
        self.usar([CABECALHO_W, [dia_br(2), dia_br(2), '81,4', '18,5', '']],
                  [CABECALHO_C,
                   [dia_br(2), '80,0', '', '2.100', '250', '70', '150', '-300'],
                   [dia_br(1), '79,5', '', '2.300', '260', '80', '160', '-100'],
                   [dia_br(0), '79,0', '', '2.000', '240', '60', '140', '-400']])
        app = Flask(__name__)
        with app.test_request_context('/api/corporal?periodo=W'):
            resp = tc.api_data()
            dados = resp.get_json()
        self.assertEqual(set(dados.keys()), self.CHAVES_TOPO)
        self.assertEqual(dados['status'], 'OK')
        for linha in dados['linhas']:
            self.assertTrue(self.CHAVES_LINHA.issubset(linha.keys()))
        self.assertEqual({r['date']: r['peso'] for r in dados['linhas']}[dia(2)], 81.4)


class TestConsumidorApp(unittest.TestCase):
    """app.py: o peso do perfil metabolico (corp_media) vem da aba diaria."""

    def test_7_perfil_usa_peso_diario(self):
        try:
            import sqlite3 as _s
            origem = '/tmp/intervals.db'
            if not os.path.exists(origem):
                self.skipTest('banco de origem ausente (/tmp/intervals.db)')
        except ImportError:
            self.skipTest('sqlite3 indisponivel')
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'teste.db')
            src = sqlite3.connect(f'file:{origem}?mode=ro', uri=True)
            dst = sqlite3.connect(db)
            for (sql,) in src.execute("select sql from sqlite_master where type='table' and sql is not null"):
                if 'sqlite_sequence' in sql or 'sqlite_' in sql[:20]:
                    continue
                dst.execute(sql)
            for i, k in enumerate([60, 30, 10]):
                dst.execute("insert into power_curves(activity_id,type,date,weight,secs,watts,updated_at)"
                            " values (?,?,?,?,?,?,?)",
                            (f'syn{i}', 'Bike', dia(k), 80.0, json.dumps([5, 60, 300, 600, 1200, 1800, 3600]),
                             json.dumps([300 + i, 260, 235, 225, 210, 200, 190]), '2026-10-10'))
            dst.commit(); dst.close(); src.close()
            script = os.path.join(tmp, 'probe.py')
            with open(script, 'w', encoding='utf-8') as f:
                f.write(PROBE_APP)
            env = dict(os.environ, SQLITE_PATH=db, INTERVALS_ICU_API_KEY='teste-local')
            env.pop('GCP_SERVICE_ACCOUNT', None)
            out = subprocess.run([sys.executable, '-I', script, ROOT], env=env,
                                 capture_output=True, text=True, timeout=300)
            ultima = [l for l in out.stdout.splitlines() if l.startswith('RESULTADO ')]
            self.assertTrue(ultima, out.stderr[-800:])
            res = json.loads(ultima[-1][len('RESULTADO '):])
            self.assertEqual(res['peso_corp_media'], 80.83)     # media dos pesos diarios (81.4, 80.9, 80.2)
            self.assertNotEqual(res['peso_corp_media'], 79.33)  # nao a media do consolidado
            self.assertEqual(res['n_peso'], 3)
            self.assertEqual(res['bf_corp_media'], 18.13)       # BF inalterado


class TestConsumidorSemPeso(unittest.TestCase):
    """Item 8: sem peso diario e sem peso nas atividades, o perfil nao quebra nem inventa valores."""

    def test_8_perfil_sem_peso_trata_ausencia(self):
        origem = '/tmp/intervals.db'
        if not os.path.exists(origem):
            self.skipTest('banco de origem ausente (/tmp/intervals.db)')
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'teste.db')
            src = sqlite3.connect(f'file:{origem}?mode=ro', uri=True)
            dst = sqlite3.connect(db)
            for (sql,) in src.execute("select sql from sqlite_master where type='table' and sql is not null"):
                if 'sqlite_sequence' in sql or 'sqlite_' in sql[:20]:
                    continue
                dst.execute(sql)
            for i, k in enumerate([60, 30, 10]):
                dst.execute("insert into power_curves(activity_id,type,date,weight,secs,watts,updated_at)"
                            " values (?,?,?,?,?,?,?)",
                            (f'syn{i}', 'Bike', dia(k), None, json.dumps([5, 60, 300, 600, 1200, 1800, 3600]),
                             json.dumps([300 + i, 260, 235, 225, 210, 200, 190]), '2026-10-10'))
            dst.commit(); dst.close(); src.close()
            script = os.path.join(tmp, 'probe_sem_peso.py')
            with open(script, 'w', encoding='utf-8') as f:
                f.write(PROBE_SEM_PESO)
            env = dict(os.environ, SQLITE_PATH=db, INTERVALS_ICU_API_KEY='teste-local')
            out = subprocess.run([sys.executable, '-I', script, ROOT], env=env,
                                 capture_output=True, text=True, timeout=300)
            linhas = [l for l in out.stdout.splitlines() if l.startswith('RESULTADO ')]
            self.assertTrue(linhas, out.stderr[-800:])
            res = json.loads(linhas[-1][len('RESULTADO '):])
            self.assertNotEqual(res['status_http'], 500)
            self.assertEqual(res['status_http'], 400)              # 'sem peso' explicito
            self.assertIn('peso', (res['mensagem'] or '').lower())
            self.assertIsNone(res['peso_corp_media'])              # nenhum peso inventado
            self.assertNotIn('nan', json.dumps(res).lower())


PROBE_SEM_PESO = r'''
import sys, json, os
repo = sys.argv[1]; sys.path.insert(0, repo); os.chdir(repo)
from datetime import date, timedelta
import sheets_client as s
hoje = date.today()
def d(k): return (hoje - timedelta(days=k)).strftime('%d/%m/%Y')
W = [['Data','Peso','FAT'],[d(10),'','18,5']]
C = [['Data','Peso','BF','Calorias','Carb','Fat','Ptn','Net']] + \
    [[d(k),'80,0','','2.000','250','70','150','-200'] for k in (10,9,8,5)]
s._ler_aba = lambda url, aba: ((W if 'Respostas' in aba else C), None)
import app as A
r = A.app.test_client().get('/api/metabol/perfil_metabolico/Bike')
j = r.get_json() or {}
ct = j.get('corporal_trimestre') or {}
print('RESULTADO ' + json.dumps({'status_http': r.status_code, 'mensagem': j.get('mensagem'),
                                 'peso_corp_media': ct.get('peso')}))
'''


PROBE_APP = r'''
import sys, json, os
repo = sys.argv[1]; sys.path.insert(0, repo); os.chdir(repo)
from datetime import date, timedelta
import sheets_client as s
hoje = date.today()
def d(k): return (hoje - timedelta(days=k)).strftime('%d/%m/%Y')
W = [['Data','Peso','FAT'],[d(10),'81,4','18,5'],[d(8),'80,9',''],[d(4),'80,2','18,0'],[d(0),'','17,9']]
C = [['Data','Peso','BF','Calorias','Carb','Fat','Ptn','Net']] + \
    [[d(k),p,'','2.000','250','70','150','-200'] for k,p in [(10,'80,0'),(9,''),(8,'79,5'),(5,'79,0'),(3,''),(1,'78,8')]]
s._ler_aba = lambda url, aba: ((W if 'Respostas' in aba else C), None)
import app as A
r = A.app.test_client().get('/api/metabol/perfil_metabolico/Bike')
j = r.get_json() or {}
ct = j.get('corporal_trimestre') or {}
print('RESULTADO ' + json.dumps({'peso_corp_media': ct.get('peso'), 'n_peso': ct.get('n_peso'),
                                 'bf_corp_media': ct.get('bf')}))
'''

if __name__ == '__main__':
    unittest.main()
