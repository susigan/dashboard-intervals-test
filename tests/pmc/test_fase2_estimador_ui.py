"""Fase 2 — painel de diagnostico experimental: JS real em node, sem rede.

    python -m pytest tests/pmc/test_fase2_estimador_ui.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
HERE = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class UiEstimador(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import tabs.tab_pmc as tp
        except BaseException as e:
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        cls.js, cls.body = tp.JS, tp.BODY
        cls.res = None
        if shutil.which('node') is not None:
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, 'pmc_js.js')
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(cls.js)
                r = subprocess.run(['node', os.path.join(HERE, 'js', 'harness_estimador.js'), p],
                                   capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise AssertionError(f'harness falhou: {r.stdout[-300:]} {r.stderr[-300:]}')
            cls.res = json.loads(r.stdout)

    def need_node(self):
        if self.res is None:
            self.skipTest('node nao disponivel')

    def test_painel_fechado_por_omissao_e_lazy(self):
        m = re.search(r'<details[^>]*id="estimadorExp"[^>]*>', self.body)
        self.assertIsNotNone(m)
        self.assertNotIn(' open', m.group(0))
        self.assertIn('ontoggle="abrirEstimador(this)"', m.group(0))
        topo = [l for l in self.js.split('\n') if re.match(r'^(await )?(carregarEstimador|abrirEstimador)\(', l)]
        self.assertEqual(topo, [])

    def test_so_leitura_no_codigo(self):
        bloco = self.js[self.js.index('// <<estimador-experimental>>'):self.js.index('// <</estimador-experimental>>')]
        self.assertNotIn("method:'POST'", bloco)
        self.assertNotIn('POST', bloco)
        self.assertEqual(len(re.findall(r'fetch\(', bloco)), 1)

    def test_nao_toca_nos_graficos_nem_indicadores_de_producao(self):
        bloco = self.js[self.js.index('// <<estimador-experimental>>'):self.js.index('// <</estimador-experimental>>')]
        for termo in ('Plotly', 'getContext', 'ctl', 'atl', 'tsb', 'carregarPMC', 'drawChart'):
            self.assertNotIn(termo, bloco, termo)

    def test_aviso_experimental_sem_afirmar_validacao(self):
        self.need_node()
        h = self.res['html']
        self.assertIn('Experimental e nao validado preditivamente', h)
        self.assertNotRegex(h.lower(), r'validado (com sucesso|estatisticamente)|prev[eê] melhor')

    def test_pedido_so_ao_abrir_e_uma_vez(self):
        self.need_node()
        self.assertEqual(self.res['sem_pedido_ao_definir'], 0)
        self.assertEqual(self.res['fechado_nao_pede'], 0)
        self.assertEqual(self.res['pedidos'], ['GET /api/modelo/estimador'])

    def test_mostra_os_campos_pedidos(self):
        self.need_node()
        h = self.res['html']
        for txt in ('Modalidade', 'Duracao', 'Confirmados', 'Usados', 'Excluidos', 'Estimativa',
                    'Incerteza', 'Ultimo teste usado', 'nao calibrado', 'experimental', '310.3 W',
                    '2026-02-10', 'elegibilidade_desconhecida:icu_ignore_power_ausente (1)',
                    '1 teste (sem incerteza)', 'IC95'):
            self.assertIn(txt, h, txt)

    def test_texto_de_servidor_e_escapado(self):
        self.need_node()
        self.assertNotIn('<img', self.res['html'])
        self.assertIn('&lt;img', self.res['html'])

    def test_erros_sao_mostrados_sem_rebentar(self):
        self.need_node()
        self.assertIn('historico ambiguo', self.res['erro'])
        self.assertIn('Erro ao carregar', self.res['rede'])


if __name__ == '__main__':
    unittest.main()
