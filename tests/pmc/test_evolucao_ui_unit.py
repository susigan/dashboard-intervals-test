"""Interface da secao "Evolucao da performance" e retirada do homeostatico da visao principal.

    python -m unittest tests.pmc.test_evolucao_ui_unit -v

Executa o JS real de tabs/tab_pmc.py com DOM simulado (node). Pula (SKIP, nao PASS)
se node ou a chave da API (exigida para importar tabs.tab_pmc) nao estiverem disponiveis.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'dummy-teste-local')

HARNESS = os.path.join(os.path.dirname(__file__), 'js', 'harness_evolucao_performance.js')


def _payload_fixture():
    """Payload gerado pela propria funcao (formato real), com casos de qualidade:
    zeros, sequencia identica longa, extremo e uma modalidade sem dados."""
    import pmc
    ref = date(2026, 10, 14)
    ent = []
    for k in range(0, 200, 7):
        ent.append({'date': (ref - timedelta(days=k)).isoformat(), 'type': 'Bike',
                    'icu_pm_ftp': 150.0 + (k % 9) * 0.5 if k % 21 else 0.0,
                    'icu_ftp': None, 'EFTP': 1.0 if k == 7 else (255.0 if k % 14 == 0 else None)})
    # sequencia identica longa (2021): 12 valores iguais espacados de 7 dias
    for k in range(12):
        ent.append({'date': (ref - timedelta(days=400 + 7 * k)).isoformat(), 'type': 'Bike',
                    'icu_pm_ftp': 240.0, 'icu_ftp': None, 'EFTP': None})
    # extremo isolado
    ent.append({'date': (ref - timedelta(days=3)).isoformat(), 'type': 'Bike',
                'icu_pm_ftp': 900.0, 'icu_ftp': None, 'EFTP': None})
    curvas = [{'activity_id': f'c{k}', 'type': 'Bike',
               'date': (ref - timedelta(days=k)).isoformat(),
               'secs': [60, 300, 720, 1200], 'watts': [300 - k % 5, 250, 220, 200]}
              for k in range(0, 200, 7)]
    res = pmc.evolucao_performance(ent, curvas, ref.isoformat())
    return res


def _rodar_harness(payload, js_codigo):
    with tempfile.TemporaryDirectory() as tmp:
        pjs = os.path.join(tmp, 'pmc_js.js')
        pjson = os.path.join(tmp, 'evol.json')
        with open(pjs, 'w', encoding='utf-8') as f:
            f.write(js_codigo)
        with open(pjson, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
        r = subprocess.run(['node', HARNESS, pjs, pjson],
                           capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise AssertionError(r.stderr or r.stdout)
    return json.loads(r.stdout)


class EvolucaoInterface(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if shutil.which('node') is None:
            raise unittest.SkipTest('node nao disponivel')
        try:
            import tabs.tab_pmc as tp
        except BaseException as e:  # config.py encerra sem chave da API
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        cls.tp = tp
        cls.js = tp.JS
        cls.body = tp.BODY

    def test_harness_evolucao_todas_as_checagens(self):
        resultado = _rodar_harness(_payload_fixture(), self.js)
        self.assertGreater(len(resultado), 0, 'harness nao produziu checagens')
        falhas = sorted(k for k, v in resultado.items() if v is not True)
        self.assertEqual(falhas, [], f'checagens reprovadas: {falhas}')

    def test_secao_evolucao_no_html_visivel_e_homeo_oculto(self):
        corpo = self.body
        self.assertIn('<h2>Evolução da performance</h2>', corpo)
        ini = corpo.index('<div id="homeoLegado" hidden>')
        fim = corpo.index('<h2>Indice alostatico</h2>')
        bloco_oculto = corpo[ini:fim]
        self.assertIn('Modelo homeostatico', bloco_oculto)
        self.assertIn('id="chHomeo"', bloco_oculto)      # IDs mantidos para o harness
        antes = corpo[:ini]
        self.assertNotIn('Modelo homeostatico', antes)   # fora da visao principal

    def test_fluxo_principal_nao_chama_desenho_homeostatico(self):
        js = self.js
        m = re.search(r'async function load\(\)\{.*?\n\}\n', js, re.S)
        self.assertIsNotNone(m, 'load() nao encontrada')
        self.assertNotIn('mostrarHomeo()', m.group(0))
        self.assertIn('mostrarEvolucao()', m.group(0))
        r = re.search(r'function redesenhar\(\)\{.*?\}\s*\ndocument', js, re.S)
        self.assertIsNotNone(r, 'redesenhar() nao encontrada')
        self.assertNotIn('drawHomeo', r.group(0))

    def test_funcoes_homeostaticas_preservadas(self):
        for nome in ('function drawHomeo(', 'function mostrarHomeo(', 'function tabelaHomeo(',
                     'function homeoValido(', 'function homeoCapacidade('):
            self.assertIn(nome, self.js, nome)

    def test_explicacao_da_interface_sobre_eftp_da_atividade(self):
        corpo = self.body
        self.assertIn('id="evolExplica"', corpo)
        ini = corpo.index('id="evolExplica"')
        bloco = corpo[ini:ini + 1200]
        self.assertIn('estimativa do Intervals.icu', bloco)
        self.assertIn('atividade completa', bloco)
        self.assertIn('incluindo os períodos de recuperação', bloco)
        self.assertIn('Uma sessão isolada com eFTP baixo não demonstra perda real de performance', bloco)
        self.assertIn('Nenhuma classificação automática de melhora ou piora', bloco)

    def test_icu_pm_ftp_nunca_apresentado_como_ftp_medido(self):
        ini = self.body.index('<h2>Evolução da performance</h2>')
        fim = self.body.index('<h2>Indice alostatico</h2>') if '<h2>Indice alostatico</h2>' in self.body[ini:] else len(self.body)
        secao = self.body[ini:fim]
        self.assertNotIn('FTP medido', secao)
        self.assertNotIn('FTP medido', self.js)
        self.assertNotIn("'ftp_medido'", self.js)
        self.assertIn('eFTP da atividade', secao)

    def test_opcoes_de_fonte_refletem_a_semantica(self):
        self.assertIn('<option value="eftp_atividade">', self.body)
        self.assertIn('<option value="ftp_configurado">', self.body)
        self.assertIn('<option value="eftp_nao_confirmado">', self.body)
        self.assertNotIn('value="ftp_medido"', self.body)
        self.assertNotIn('value="eftp"', self.body)

    def test_js_usa_o_payload_fontes_e_nao_o_antigo(self):
        self.assertIn('M.fontes[', self.js)
        self.assertNotIn('M.ftp[', self.js)


    def test_rotulos_e_chaves_coerentes_entre_python_js_e_html(self):
        import pmc as P
        chaves = [c[0] for c in P.EVOL_SERIES]
        self.assertEqual(chaves, ['eftp_atividade', 'ftp_configurado', 'eftp_nao_confirmado'])
        for chave in chaves:
            self.assertIn(f"['{chave}'", self.js, chave)
            self.assertIn(f'value="{chave}"', self.body, chave)
        self.assertIn("eFTP da atividade (icu_pm_ftp)", self.js)
        self.assertIn("FTP configurado (icu_ftp)", self.js)

    def test_js_da_evolucao_sem_classificacao_automatica_de_melhora_ou_piora(self):
        ini = self.js.index('const EV_MOTIVOS')
        fim = self.js.index('function redesenhar()')
        trecho = self.js[ini:fim].lower()
        for palavra in ('queda', 'piora', 'melhora', 'declínio', 'declinio', 'perda de'):
            self.assertNotIn(palavra, trecho, palavra)

    def test_ids_das_novas_secoes_existem_no_html(self):
        for i in ('evolMod', 'evolJanela', 'evolFonte', 'evolSub', 'evolAvisos',
                  'evolHead', 'evolBody', 'evolMeHead', 'evolMeBody'):
            self.assertIn(f'id="{i}"', self.body, i)


if __name__ == '__main__':
    unittest.main()
