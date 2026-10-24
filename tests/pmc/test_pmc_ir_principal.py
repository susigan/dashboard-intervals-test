"""Regressao: curvas IR exploratorias na area principal da PMC (fora do <details> fechado)."""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TAB = os.path.abspath(os.path.join(HERE, '..', '..', 'tabs', 'tab_pmc.py'))


class IrPrincipalPMC(unittest.TestCase):
    def setUp(self):
        with open(TAB, encoding='utf-8') as f:
            self.src = f.read()

    def test_secao_principal_existe_com_os_elementos_esperados(self):
        for ident in ('id="irPrincipal"', 'id="chIrxMain"', 'id="irxDurMain"', 'id="irxLegMain"',
                      'id="irPrincipalAviso"', 'id="irPrincipalEstados"'):
            self.assertEqual(self.src.count(ident), 1, ident)

    def test_secao_principal_vem_antes_do_painel_tecnico_fechado(self):
        self.assertLess(self.src.index('id="irPrincipal"'), self.src.index('id="registoTestes"'))

    def test_secao_principal_nao_tem_tabelas(self):
        ini = self.src.index('id="irPrincipal"')
        fim = self.src.index('id="registoTestes"')
        self.assertNotIn('<table', self.src[ini:fim])

    def test_desenho_parametrizado_com_predefinicoes_antigas(self):
        self.assertIn('function irxDesenhar(cvId,selId,legId){', self.src)
        self.assertIn("ctx(cvId||'chIrx',300)", self.src)
        self.assertIn("document.getElementById(legId||'irxLeg')", self.src)

    def test_carregamento_automatico_na_abertura(self):
        self.assertIn('carregarIRExploratorio();\n"""', self.src)

    def test_estado_de_erro_nao_fabrica_curva(self):
        self.assertIn("Modo exploratorio indisponivel", self.src)
        self.assertIn("Sem unidades nesta duracao.", self.src)


if __name__ == '__main__':
    unittest.main()


class ErroNaoDesenha(unittest.TestCase):
    """Regressao: no caminho de erro a secao principal nao chama o desenho do canvas."""

    def test_caminho_de_erro_nao_chama_desenho(self):
        with open(TAB, encoding='utf-8') as f:
            src = f.read()
        ini = src.index('function irxRenderMain(){')
        fim = src.index('function irxRender(erro){')
        ramo_erro = src[ini:src.index('if(aviso)aviso.textContent=\'Exploratorio', ini)]
        self.assertNotIn('irxDesenhar(', ramo_erro)
        self.assertIn("lg.innerHTML=''", ramo_erro)
        self.assertLess(ini, fim)
