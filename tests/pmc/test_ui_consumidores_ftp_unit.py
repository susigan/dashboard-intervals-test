"""UI real dos consumidores FTP: tab_detalhe (cards) e tab_atividades (linhas/colunas).

- tab_detalhe: o array `const cards=[...]` do JS é executado no Node com uma
  atividade de fixture; verifica-se o texto e o valor de cada card.
- tab_atividades: api_data() é chamada com fetch_activities substituído por
  fixture; verifica-se o conteúdo das linhas.
- Consistência: as chaves das colunas JS existem nas linhas Python.

Pula (SKIP) se node não estiver disponível. Não usa rede nem credenciais.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)


def _ler(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


def _cards_js_detalhe():
    """Extrai o literal `const cards=[...]` de tabs/tab_detalhe.py (sem DOM)."""
    src = _ler('tabs/tab_detalhe.py')
    m = re.search(r'const cards=(\[.*?\]\]);', src, re.S)
    if not m:
        raise AssertionError('literal const cards nao encontrado em tab_detalhe.py')
    return m.group(1)


# Script Node: avalia o literal de cards com `a` e `d` vindos do stdin.
_NODE = r"""
const vm = require('vm');
let entrada = '';
process.stdin.on('data', c => entrada += c);
process.stdin.on('end', () => {
  const {literal, a, d} = JSON.parse(entrada);
  const ctx = vm.createContext({a, d});
  const cards = vm.runInContext('(' + literal + ')', ctx);
  process.stdout.write(JSON.stringify(cards));
});
"""


def _rodar_cards(a, d=None):
    if shutil.which('node') is None:
        raise unittest.SkipTest('node nao disponivel')
    entrada = json.dumps({'literal': _cards_js_detalhe(), 'a': a, 'd': d or {}})
    r = subprocess.run(['node', '-e', _NODE], input=entrada, capture_output=True,
                       text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError('node falhou: ' + r.stderr[-400:])
    return json.loads(r.stdout)


def _valor(cards, rotulo):
    achados = [v for (lab, v) in cards if lab == rotulo]
    return achados


class CardsDetalheFtp(unittest.TestCase):

    def test_eftp_e_ftp_configurado_mostrados_separadamente(self):
        cards = _rodar_cards({'icu_pm_ftp': 227, 'icu_ftp': 250})
        self.assertEqual(_valor(cards, 'eFTP da atividade'), ['227 W'])
        self.assertEqual(_valor(cards, 'FTP configurado'), ['250 W'])

    def test_nao_existe_card_ftp_que_misture_as_fontes(self):
        cards = _rodar_cards({'icu_pm_ftp': 227, 'icu_ftp': 250})
        self.assertEqual(_valor(cards, 'FTP'), [])

    def test_so_ftp_configurado_nao_aparece_como_eftp(self):
        # Caso que a mistura antiga exibia como "FTP": icu_pm_ftp ausente.
        cards = _rodar_cards({'icu_ftp': 250})
        self.assertEqual(_valor(cards, 'eFTP da atividade'), ['—'])
        self.assertEqual(_valor(cards, 'FTP configurado'), ['250 W'])

    def test_so_eftp_nao_aparece_como_ftp_configurado(self):
        cards = _rodar_cards({'icu_pm_ftp': 227})
        self.assertEqual(_valor(cards, 'eFTP da atividade'), ['227 W'])
        self.assertEqual(_valor(cards, 'FTP configurado'), ['—'])

    def test_ausencia_mostra_traco_e_nao_zero(self):
        cards = _rodar_cards({})
        self.assertEqual(_valor(cards, 'eFTP da atividade'), ['—'])
        self.assertEqual(_valor(cards, 'FTP configurado'), ['—'])

    def test_zero_e_ausencia_nos_cards(self):
        # Regra do projeto: zero não é valor. eFTP 0 vira traço, não "0 W".
        cards = _rodar_cards({'icu_pm_ftp': 0, 'icu_ftp': 0})
        self.assertEqual(_valor(cards, 'eFTP da atividade'), ['—'])
        self.assertEqual(_valor(cards, 'FTP configurado'), ['—'])

    def test_kj_acima_do_limiar_com_referencia_explicita(self):
        cards = _rodar_cards({'icu_joules_above_ftp': 15000})
        rotulos = [lab for lab, _ in cards]
        self.assertNotIn('kJ > FTP', rotulos)
        self.assertIn('kJ acima do limiar (ref. Intervals.icu, não confirmada)', rotulos)
        self.assertEqual(_valor(cards, 'kJ acima do limiar (ref. Intervals.icu, não confirmada)'), [15])


class LinhasAtividadesFtp(unittest.TestCase):

    def _api(self, atividade):
        import tabs.tab_atividades as TA
        from app import app
        original = TA.fetch_activities
        TA.fetch_activities = lambda *a, **k: [atividade]
        try:
            with app.test_request_context('/api/atividades'):
                resp = TA.api_data()
                data = resp.get_json() if hasattr(resp, 'get_json') else resp[0].get_json()
        finally:
            TA.fetch_activities = original
        return data['activities'][0]

    def _base(self, **campos):
        a = {'id': 'x1', 'start_date_local': '2026-10-01T07:00:00', 'type': 'Bike',
             'name': 'Teste', 'elapsed_time': 3600, 'distance': 30000,
             'icu_distance': 30000, 'icu_training_load': 60}
        a.update(campos)
        return a

    def test_linha_tem_chaves_separadas_e_nao_a_mista(self):
        row = self._api(self._base(icu_pm_ftp=227, icu_ftp=250))
        self.assertEqual(row['eftp_atividade'], 227)
        self.assertEqual(row['ftp_configurado'], 250)
        self.assertNotIn('ftp', row)

    def test_eftp_ausente_nao_preenche_com_ftp_configurado(self):
        row = self._api(self._base(icu_ftp=250))
        self.assertIsNone(row['eftp_atividade'])
        self.assertEqual(row['ftp_configurado'], 250)

    def test_zero_nas_linhas_e_ausencia(self):
        row = self._api(self._base(icu_pm_ftp=0, icu_ftp=0))
        self.assertIsNone(row['eftp_atividade'])
        self.assertIsNone(row['ftp_configurado'])

    def test_ftp_configurado_ausente_nao_preenche_com_eftp(self):
        row = self._api(self._base(icu_pm_ftp=227))
        self.assertEqual(row['eftp_atividade'], 227)
        self.assertIsNone(row['ftp_configurado'])


class ColunasConsistentes(unittest.TestCase):

    def test_colunas_js_usam_as_chaves_das_linhas_python(self):
        src = _ler('tabs/tab_atividades.py')
        colunas = re.findall(r"\['(eftp_atividade|ftp_configurado)','[^']+',1\]", src)
        self.assertEqual(sorted(colunas), ['eftp_atividade', 'ftp_configurado'])
        for chave in colunas:
            self.assertIn(f"'{chave}':", src)

    def test_coluna_ftp_antiga_removida_do_js(self):
        src = _ler('tabs/tab_atividades.py')
        self.assertNotIn("['ftp','FTP',1]", src)


if __name__ == '__main__':
    unittest.main()
