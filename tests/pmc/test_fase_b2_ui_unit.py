"""Fase B2, passo 3 — texto da interface sobre o uso dos esforcos detetados.

O diagnostico `ancora_testes` nao ajusta nenhum parametro do modelo; a frase
da UI nao pode afirmar que os esforcos "alimentam a calibracao".
"""
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TextoDasSugestoes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import tabs.tab_pmc as tp
        except BaseException as e:
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        cls.js = tp.JS

    def test_nao_afirma_que_alimentam_a_calibracao(self):
        self.assertNotIn('alimentam a calibracao', self.js)
        self.assertNotRegex(self.js, r'Estes esforcos alimentam')

    def test_explica_que_e_um_diagnostico_e_nao_atualiza_o_modelo(self):
        i = self.js.index('Testes sugeridos (')
        trecho = self.js[i:i + 900]
        self.assertIn('produzem hoje um diagnostico', trecho)
        self.assertIn('nao atualizam os parametros do', trecho)
        self.assertIn('modelo', trecho)

    def test_mantem_a_mensagem_de_que_nao_ha_nada_para_marcar(self):
        i = self.js.index('Testes sugeridos (')
        trecho = self.js[i:i + 900]
        self.assertIn('nao ha nada para marcar', trecho)
        self.assertIn('A sugestao desaparece sozinha', trecho)

    def test_sintaxe_do_bloco_intacta(self):
        # o texto esta dentro de uma string JS entre aspas simples: sem aspas soltas
        i = self.js.index('Estes esforcos produzem')
        linha = self.js[self.js.rfind('\n', 0, i) + 1:self.js.index('\n', i)]
        self.assertEqual(len(re.findall(r"(?<!\\)'", linha)), 2)


if __name__ == '__main__':
    unittest.main()
