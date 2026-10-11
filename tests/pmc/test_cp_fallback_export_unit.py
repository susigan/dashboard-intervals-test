"""Exportacao cp_fallback.csv: campos brutos de activities.raw.

Sem rede e sem base real: o db e' substituido por um falso com a mesma
interface (load_activities, cp_por_sessao).
"""
import csv
import io
import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import export  # noqa: E402

CAMPOS = ['id', 'start_date_local', 'icu_pm_cp', 'icu_rolling_ftp',
          'icu_pm_ftp', 'icu_pm_w_prime']


class FakeDB:
    ENABLED = True

    def __init__(self, acts, curva=None, erro_curva=False, sem_base=False):
        self.acts = None if sem_base else acts
        self.curva = curva or []
        self.erro_curva = erro_curva

    def load_activities(self, desde=None):
        return self.acts

    def cp_por_sessao(self, tipo=None, **kw):
        if self.erro_curva:
            raise RuntimeError('tabela power_curves ausente')
        return self.curva


def _act(i, data='2023-01-02', **campos):
    a = {'id': i, 'start_date_local': data}
    a.update(campos)
    return a


ACTS = [
    _act('i1', icu_pm_cp=0, icu_rolling_ftp=None, icu_pm_ftp=250),
    _act('i2', icu_pm_cp=1.0),
    _act('i3', icu_pm_cp=25.5, icu_pm_w_prime=0),
    _act('i4', icu_pm_cp=0, icu_rolling_ftp=0, icu_pm_ftp=None),
    _act('i5', icu_pm_cp='abc'),
    _act('i6', icu_pm_cp=300.0),                    # na curva, r2 0.9
    _act('i7', icu_pm_cp=280.0),                    # curva com r2 0.5
    _act('i8', data=None, icu_pm_cp=200.0),         # sem data
    _act('i9', data='2023', icu_pm_cp=200.0),       # data curta
    _act('i10', icu_pm_cp=None, icu_rolling_ftp=200),
]
CURVA = [
    {'activity_id': 'i6', 'cp': 300.0, 'w_prime': 15000.0, 'r2': 0.9},
    {'activity_id': 'i7', 'cp': 280.0, 'w_prime': 12000.0, 'r2': 0.5},
]


def _csv_lido(texto):
    return list(csv.DictReader(io.StringIO(texto)))


class CpFallbackSelecao(unittest.TestCase):

    def setUp(self):
        self.texto, self.resumo = export.cp_fallback(
            FakeDB(ACTS, curva=CURVA))
        self.linhas = _csv_lido(self.texto)

    def test_cabecalho_e_colunas_exatas(self):
        self.assertEqual(self.texto.splitlines()[0], ','.join(CAMPOS))

    def test_ids_selecionados_seguem_criterio_do_pmc(self):
        # i1 (pm_ftp), i2 (1.0 W), i3 (w' 0), i7 (curva com r2 < 0.80),
        # i10 (rolling). Excluidos: sem CP, zero/texto, na curva, sem data.
        ids = [r['id'] for r in self.linhas]
        self.assertEqual(ids, ['i1', 'i2', 'i3', 'i7', 'i10'])

    def test_valores_brutos_preservados(self):
        por_id = {r['id']: r for r in self.linhas}
        self.assertEqual(por_id['i1']['icu_pm_cp'], '0')        # zero, nao vazio
        self.assertEqual(por_id['i1']['icu_rolling_ftp'], '')    # nulo
        self.assertEqual(por_id['i1']['icu_pm_ftp'], '250')
        self.assertEqual(por_id['i2']['icu_pm_cp'], '1.0')       # baixo, intacto
        self.assertEqual(por_id['i3']['icu_pm_w_prime'], '0')    # zero preservado
        self.assertEqual(por_id['i10']['icu_pm_cp'], '')
        self.assertEqual(por_id['i10']['icu_rolling_ftp'], '200')

    def test_data_completa_nao_truncada(self):
        por_id = {r['id']: r for r in self.linhas}
        self.assertEqual(por_id['i1']['start_date_local'], '2023-01-02')

    def test_resumo_contagens(self):
        self.assertEqual(self.resumo['registros'], 5)
        self.assertEqual(self.resumo['ids_distintos'], 5)
        p = self.resumo['preenchidos']
        self.assertEqual(p['icu_pm_cp'], 4)        # i1, i2, i3, i7
        self.assertEqual(p['icu_rolling_ftp'], 1)  # i10
        self.assertEqual(p['icu_pm_ftp'], 1)       # i1
        self.assertEqual(p['icu_pm_w_prime'], 1)   # i3 (zero conta como preenchido)

    def test_ids_duplicados_sao_reportados(self):
        acts = ACTS + [_act('i2', icu_pm_cp=1.0)]
        _, resumo = export.cp_fallback(FakeDB(acts, curva=CURVA))
        self.assertEqual(resumo['registros'], 6)
        self.assertEqual(resumo['ids_distintos'], 5)


class CpFallbackErros(unittest.TestCase):

    def test_base_sem_actividades_recusa_sem_estimar(self):
        with self.assertRaises(export.SemDados):
            export.cp_fallback(FakeDB([], curva=CURVA))

    def test_base_desligada_recusa(self):
        with self.assertRaises(export.SemDados):
            export.cp_fallback(FakeDB(None, sem_base=True))

    def test_curva_indisponivel_recusa_em_vez_de_ignorar(self):
        # /api/pmc ignora erro de cp_por_sessao; a exportacao nao pode, senao
        # a selecao sairia diferente da do endpoint sem aviso.
        with self.assertRaises(export.SemDados) as ctx:
            export.cp_fallback(FakeDB(ACTS, erro_curva=True))
        self.assertIn('curva de CP indisponivel', str(ctx.exception))


class CpFallbackRota(unittest.TestCase):
    """Rota /api/export/cp_fallback.csv com o db substituido."""

    @classmethod
    def setUpClass(cls):
        try:
            import app as app_mod
        except BaseException as e:  # config.py faz sys.exit sem chave da API
            raise unittest.SkipTest(f'app nao importa: {type(e).__name__}: {e}')
        cls.app_mod = app_mod
        cls.client = app_mod.app.test_client()

    def _com_db(self, fake):
        original = self.app_mod.db
        self.app_mod.db = fake
        self.addCleanup(setattr, self.app_mod, 'db', original)

    def test_rota_devolve_csv_e_resumo(self):
        self._com_db(FakeDB(ACTS, curva=CURVA))
        r = self.client.get('/api/export/cp_fallback.csv')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.mimetype == 'text/csv')
        self.assertIn('attachment', r.headers['Content-Disposition'])
        resumo = json.loads(r.headers['X-Resumo'])
        self.assertEqual(resumo['registros'], 5)
        self.assertEqual(len(_csv_lido(r.get_data(as_text=True))), 5)

    def test_rota_sem_base_responde_erro_explicito_503(self):
        self._com_db(FakeDB(None, sem_base=True))
        r = self.client.get('/api/export/cp_fallback.csv')
        self.assertEqual(r.status_code, 503)
        self.assertIn('erro', r.get_json())

    def test_rota_json_nao_existe(self):
        self._com_db(FakeDB(ACTS, curva=CURVA))
        r = self.client.get('/api/export/cp_fallback.json')
        self.assertEqual(r.status_code, 404)


if __name__ == '__main__':
    unittest.main()
