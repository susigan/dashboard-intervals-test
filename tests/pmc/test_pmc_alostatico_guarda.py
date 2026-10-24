"""Regressao: a tabela alostatica da PMC nao chama .toFixed sobre delta_pct/score nulos.

pmc.py devolve linhas com 'ant' e 'rec' preenchidos e delta_pct/score = None quando a base e
proxima de zero ('base proxima de zero'). A interface so tratava ant == null.
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TAB = os.path.abspath(os.path.join(HERE, '..', '..', 'tabs', 'tab_pmc.py'))


class GuardaAlostatico(unittest.TestCase):
    def setUp(self):
        with open(TAB, encoding='utf-8') as f:
            self.src = f.read()

    def test_linha_sem_delta_tem_guarda_antes_do_toFixed(self):
        guarda = "if(d.delta_pct==null) return"
        self.assertEqual(self.src.count(guarda), 1)
        self.assertLess(self.src.index(guarda), self.src.index("d.delta_pct.toFixed(1)"))

    def test_guarda_mostra_motivo_do_backend(self):
        self.assertIn("(d.motivo||'sem variacao calculavel')", self.src)


if __name__ == '__main__':
    unittest.main()


class EstadoCanalVazio(unittest.TestCase):
    """Regressao: sem canais com dados, o dropdown mostra uma opcao explicita (nao fica vazio)."""

    def test_dropdown_canal_tem_opcao_quando_nao_ha_canais(self):
        with open(TAB, encoding='utf-8') as f:
            src = f.read()
        self.assertIn("sem canais com dados", src)
