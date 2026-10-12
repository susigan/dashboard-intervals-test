"""Exportacao cp_aceitas.csv: sessoes com curva de CP aceita (r2 >= 0.80).

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

CAMPOS_OBRIGATORIOS = [
    'id', 'date', 'type_raw', 'curva_cp', 'curva_w_prime', 'curva_r2',
    'curva_n_pontos', 'icu_pm_cp', 'icu_rolling_ftp', 'icu_pm_ftp',
    'icu_pm_ftp_watts', 'icu_pm_ftp_secs', 'icu_pm_w_prime', 'icu_ftp',
    'EFTP',
]


class FakeDB:
    ENABLED = True

    def __init__(self, acts, curvas=None, sem_base=False, erro_curva=False):
        self.acts = None if sem_base else acts
        self.curvas = curvas or []
        self.erro_curva = erro_curva

    def load_activities(self, desde=None):
        return self.acts

    def cp_por_sessao(self, tipo=None, **kw):
        if self.erro_curva:
            raise RuntimeError('tabela power_curves ausente')
        return self.curvas


def _act(i, data='2024-03-10', **campos):
    a = {'id': i, 'start_date_local': data + 'T07:00:00', 'type': 'Ride'}
    a.update(campos)
    return a


def _curva(aid, r2, cp=250.0, wp=15000.0, n=9, data='2024-03-10'):
    return {'activity_id': aid, 'date': data, 'type': 'Bike', 'cp': cp,
            'w_prime': wp, 'r2': r2, 'n_pontos': n}


ACTS = [
    _act('i1', icu_pm_cp=260, icu_rolling_ftp=250, icu_pm_ftp=248,
         icu_pm_ftp_watts=240, icu_pm_ftp_secs=1200, icu_pm_w_prime=14000,
         icu_ftp=251, EFTP=255),
    _act('i2', icu_pm_cp=0, icu_rolling_ftp=None),       # zero real, vazio
    _act('i3', icu_pm_cp=None),                          # ausentes
    _act('i4', icu_pm_cp=300, icu_ftp=230),              # r2 abaixo de 0.80
    _act('i5', icu_pm_cp=310, EFTP=0),                   # r2 exatamente 0.80
]
CURVAS = [
    _curva('i1', 0.9651),
    _curva('i2', 0.85, cp=200.0),
    _curva('i3', 0.8000),                 # limite inclusivo: aceite
    _curva('i4', 0.7999),                 # logo abaixo: fora
    _curva('i5', 0.80),
    _curva('i9', 0.92),                   # curva sem atividade no banco
]


def _csv_lido(texto):
    return list(csv.DictReader(io.StringIO(texto)))


class CpAceitasSelecao(unittest.TestCase):

    def test_so_r2_maior_ou_igual_a_080_entra(self):
        texto, resumo = export.cp_aceitas(FakeDB(ACTS, CURVAS))
        ids = {r['id'] for r in _csv_lido(texto)}
        self.assertEqual(ids, {'i1', 'i2', 'i3', 'i5'})
        self.assertNotIn('i4', ids)

    def test_r2_abaixo_do_limiar_nao_aparece(self):
        texto, _ = export.cp_aceitas(FakeDB(ACTS, [_curva('i4', 0.79)]))
        self.assertEqual(_csv_lido(texto), [])

    def test_colunas_obrigatorias_presentes_na_ordem(self):
        texto, _ = export.cp_aceitas(FakeDB(ACTS, CURVAS))
        cab = texto.splitlines()[0].split(',')
        self.assertEqual(cab, CAMPOS_OBRIGATORIOS)

    def test_campos_brutos_lidos_das_chaves_originais(self):
        texto, _ = export.cp_aceitas(FakeDB(ACTS, CURVAS))
        l1 = next(r for r in _csv_lido(texto) if r['id'] == 'i1')
        self.assertEqual(l1['icu_pm_cp'], '260')
        self.assertEqual(l1['icu_rolling_ftp'], '250')
        self.assertEqual(l1['icu_pm_ftp_watts'], '240')
        self.assertEqual(l1['icu_pm_ftp_secs'], '1200')
        self.assertEqual(l1['icu_pm_w_prime'], '14000')
        self.assertEqual(l1['icu_ftp'], '251')
        self.assertEqual(l1['curva_cp'], '250.0')
        self.assertEqual(l1['curva_r2'], '0.9651')
        self.assertEqual(l1['type_raw'], 'Ride')
        self.assertEqual(l1['date'], '2024-03-10')

    def test_eftp_preserva_capitalizacao_original(self):
        texto, _ = export.cp_aceitas(FakeDB(ACTS, CURVAS))
        self.assertIn('EFTP', texto.splitlines()[0].split(','))
        self.assertNotIn('eftp', texto.splitlines()[0].split(','))
        l1 = next(r for r in _csv_lido(texto) if r['id'] == 'i1')
        self.assertEqual(l1['EFTP'], '255')

    def test_ausente_fica_vazio_e_zero_fica_zero(self):
        texto, resumo = export.cp_aceitas(FakeDB(ACTS, CURVAS))
        linhas = {r['id']: r for r in _csv_lido(texto)}
        # i2: icu_pm_cp=0 e icu_rolling_ftp=None
        self.assertEqual(linhas['i2']['icu_pm_cp'], '0')
        self.assertEqual(linhas['i2']['icu_rolling_ftp'], '')
        # i3: icu_pm_cp=None e os demais campos ausentes no raw
        self.assertEqual(linhas['i3']['icu_pm_cp'], '')
        self.assertEqual(linhas['i3']['icu_ftp'], '')
        self.assertEqual(linhas['i3']['EFTP'], '')
        # i5: EFTP=0 real preservado como zero
        self.assertEqual(linhas['i5']['EFTP'], '0')
        self.assertGreaterEqual(
            resumo['campos_brutos_ausentes']['icu_ftp'], 2)

    def test_cada_id_aparece_uma_unica_vez(self):
        acts = ACTS + [_act('i1', icu_pm_cp=999)]        # atividade duplicada
        curvas = CURVAS + [_curva('i1', 0.99)]            # curva duplicada
        texto, resumo = export.cp_aceitas(FakeDB(acts, curvas))
        ids = [r['id'] for r in _csv_lido(texto)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count('i1'), 1)
        l1 = next(r for r in _csv_lido(texto) if r['id'] == 'i1')
        self.assertEqual(l1['icu_pm_cp'], '260')          # primeira ocorrencia
        self.assertEqual(resumo['ids_unicos'], resumo['linhas'])

    def test_curva_sem_atividade_nao_gera_linha_falsa(self):
        texto, resumo = export.cp_aceitas(FakeDB(ACTS, CURVAS))
        ids = {r['id'] for r in _csv_lido(texto)}
        self.assertNotIn('i9', ids)
        self.assertEqual(resumo['sem_atividade'], 1)
        self.assertEqual(resumo['curvas_aceitas'], 5)     # i1,i2,i3,i5,i9
        self.assertEqual(resumo['linhas'], 4)

    def test_resumo_controles_consistentes(self):
        texto, resumo = export.cp_aceitas(FakeDB(ACTS, CURVAS))
        self.assertEqual(resumo['linhas'], len(_csv_lido(texto)))
        self.assertEqual(resumo['linhas'] + resumo['sem_atividade'],
                         resumo['curvas_aceitas'])
        self.assertEqual(set(resumo['campos_brutos_ausentes']),
                         set(export.CAMPOS_CP_ACEITAS_BRUTOS))

    def test_base_sem_actividades_levanta_semdados(self):
        with self.assertRaises(export.SemDados):
            export.cp_aceitas(FakeDB(None, sem_base=True))
        with self.assertRaises(export.SemDados):
            export.cp_aceitas(FakeDB([], CURVAS))

    def test_erro_na_curva_levanta_semdados(self):
        with self.assertRaises(export.SemDados):
            export.cp_aceitas(FakeDB(ACTS, erro_curva=True))


class CpAceitasRota(unittest.TestCase):
    """Rotas /api/export/cp_aceitas.csv e cp_fallback.csv com o db substituido."""

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

    def test_rota_devolve_csv_content_type_e_resumo(self):
        self._com_db(FakeDB(ACTS, CURVAS))
        r = self.client.get('/api/export/cp_aceitas.csv')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, 'text/csv')
        self.assertIn('attachment', r.headers['Content-Disposition'])
        resumo = json.loads(r.headers['X-Resumo'])
        self.assertEqual(resumo['linhas'], 4)
        self.assertEqual(len(_csv_lido(r.get_data(as_text=True))), 4)

    def test_rota_sem_base_responde_503(self):
        self._com_db(FakeDB(None, sem_base=True))
        r = self.client.get('/api/export/cp_aceitas.csv')
        self.assertEqual(r.status_code, 503)
        self.assertIn('erro', r.get_json())

    def test_cp_fallback_rota_continua_igual(self):
        acts = [_act('f1', icu_pm_cp=None, icu_rolling_ftp=200)]
        self._com_db(FakeDB(acts, [_curva('f2', 0.95)]))
        r = self.client.get('/api/export/cp_fallback.csv')
        self.assertEqual(r.status_code, 200)
        cab = r.get_data(as_text=True).splitlines()[0].split(',')
        self.assertEqual(cab, ['id', 'start_date_local', 'icu_pm_cp',
                               'icu_rolling_ftp', 'icu_pm_ftp',
                               'icu_pm_w_prime'])
        self.assertEqual(json.loads(r.headers['X-Resumo'])['registros'], 1)


if __name__ == '__main__':
    unittest.main()
