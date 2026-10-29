"""Estrutura do painel comparativo de Aquecimento (tabs/tab_metabol.py + app.py).

Verificacoes de texto, sem browser nem base de dados. A logica numerica esta
em test_comparativo_unit.py; o desenho foi validado visualmente com dados
reais do CSV (ver relatorio), nao por estes testes.
"""
import os
import re
import unittest

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _ler(rel):
    with open(os.path.join(RAIZ, rel), encoding='utf-8') as f:
        return f.read()


def _bloco_js():
    s = _ler('tabs/tab_metabol.py')
    i = s.index('/* AQCMP_BEGIN */')
    j = s.index('/* AQCMP_END */')
    return s[i:j]


class PainelComparativo(unittest.TestCase):

    def test_ids_do_html_existem_uma_vez(self):
        s = _ler('tabs/tab_metabol.py')
        for id_ in ('aqCmpMeta', 'aqCmpLegenda', 'aqCmpPeriodos', 'aqCmpDelta',
                    'chCmp1', 'chCmp2', 'aqCmpTip1', 'aqCmpTip2', 'aqCmpMet',
                    'aqCmpAgr', 'aqCmpMin', 'aqCmpCustom', 'aqCmpIni', 'aqCmpFim',
                    'aqCmpAplicar'):
            with self.subTest(id=id_):
                self.assertEqual(s.count(f'id="{id_}"'), 1)

    def test_botoes_de_periodo_30_60_90_e_personalizado(self):
        s = _ler('tabs/tab_metabol.py')
        for dias in ('30', '60', '90', 'custom'):
            with self.subTest(dias=dias):
                self.assertIn(f'data-dias="{dias}"', s)

    def test_chama_o_endpoint_comparativo(self):
        js = _bloco_js()
        self.assertIn("'/api/aquecimento/comparativo?'", js)
        self.assertIn('min_n=', js)

    def test_endpoint_existe_no_app_uma_vez(self):
        s = _ler('app.py')
        self.assertEqual(s.count("@app.route('/api/aquecimento/comparativo')"), 1)
        self.assertIn('aquecimento_comparativo as cmp', s)

    def test_bloco_js_sem_cadencia_torque_elevacao(self):
        js = _bloco_js().lower()
        for proibido in ('cadence', 'cadência', 'cadencia', 'torque', 'elevation', 'elevação', 'elevacao'):
            with self.subTest(termo=proibido):
                self.assertNotIn(proibido, js)

    def test_bloco_js_nao_usa_min_max_nem_thb(self):
        js = _bloco_js()
        self.assertIsNone(re.search(r"_(min|max)['\"]", js), 'nao usar colunas _min/_max')
        self.assertIsNone(re.search(r'\bthb\b', js, re.IGNORECASE), 'THb nao esta guardada')

    def test_ausencia_nunca_vira_zero_no_painel(self):
        js = _bloco_js().replace(' ', '')
        # aqcFmt devolve travessao para valor em falta, nunca zero
        self.assertRegex(js, r"if\(v===null\|\|v===undefined\|\|!isFinite\(v\)\)return'(\\u2014|\u2014)'")
        self.assertNotIn("v||0", js)
        self.assertNotIn("??0", js)

    def test_hooks_de_carregamento(self):
        s = _ler('tabs/tab_metabol.py')
        self.assertIn("aqPerDefaults(); aqCmpCarregar(); aqTendLigar(); aqTendCarregar(); }", s)

    def test_marcadores_do_bloco_presentes(self):
        s = _ler('tabs/tab_metabol.py')
        self.assertEqual(s.count('/* AQCMP_BEGIN */'), 1)
        self.assertEqual(s.count('/* AQCMP_END */'), 1)


if __name__ == '__main__':
    unittest.main()
