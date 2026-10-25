import re
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
        for ident in ('id="irPrincipal"', 'id="chIrxMain"', 'id="irxLegMain"',
                      'id="irPrincipalAviso"', 'id="irPrincipalEstados"', 'id="irPrincipalGlobal"'):
            self.assertEqual(self.src.count(ident), 1, ident)

    def test_secao_principal_vem_antes_do_painel_tecnico_fechado(self):
        self.assertLess(self.src.index('id="irPrincipal"'), self.src.index('id="registoTestes"'))

    def test_secao_principal_nao_tem_tabelas(self):
        ini = self.src.index('id="irPrincipal"')
        fim = self.src.index('id="registoTestes"')
        self.assertNotIn('<table', self.src[ini:fim])
        # a area principal nao tem seletor de duracao: as curvas sao automaticas por modalidade
        self.assertNotIn('irxDurMain', self.src[ini:fim])

    def test_grafico_unico_na_area_principal(self):
        # um so desenho de curvas: irxPintar, chamado pela area principal. Sem funcao duplicada de comparacao.
        self.assertNotIn('function irxDesenhar(', self.src)
        self.assertIn('function irpDesenhar(j){', self.src)
        self.assertIn("ctx('chIrxMain',300)", self.src)
        self.assertNotIn('function irxPintar(', self.src)
        self.assertEqual(self.src.count('id="chIrxMain"'), 1)
        self.assertNotIn('id="chIrx"', self.src)      # sem segundo canvas na secao tecnica
        self.assertNotIn('irxDur', self.src)           # sem seletor de duracao concorrente

    def test_painel_tecnico_removido_e_dropdown_unico(self):
        self.assertNotIn('id="irTecnico"', self.src)
        self.assertNotIn('id="irExp"', self.src)
        self.assertNotIn('id="irExploratorio"', self.src)
        # um so dropdown de evolucao detalhada, recolhido, reutilizando o IR sombra
        self.assertEqual(self.src.count('id="irSombra"'), 1)
        self.assertIn('Evolução detalhada do IR — observações e testes', self.src)
        self.assertIn('ontoggle="abrirIRSombra(this)"', self.src)
        self.assertNotIn(' open', re.search(r'<details[^>]*id="irSombra"[^>]*>', self.src).group(0))
        # manutencao do protocolo: recolhida e com o painel MMP dentro
        man = re.search(r'<details[^>]*id="manProtocolo"[^>]*>', self.src).group(0)
        self.assertNotIn(' open', man)
        self.assertLess(self.src.index('id="manProtocolo"'), self.src.index('id="registoTestes"'))
        self.assertLess(self.src.index('id="irPrincipal"'), self.src.index('id="manProtocolo"'))

    def test_carregamento_automatico_na_abertura(self):
        self.assertIn('carregarIRPrincipal();\n"""', self.src)
        self.assertNotIn('\ncarregarIRExploratorio();\n', self.src)    # sem pedido de nivel superior ao abrir

    def test_estado_de_erro_nao_fabrica_curva(self):
        self.assertIn("Vista indisponível", self.src)
        self.assertIn("Curva não calculável com os dados disponíveis", self.src)


if __name__ == '__main__':
    unittest.main()


class ErroNaoDesenha(unittest.TestCase):
    """Regressao: no caminho de erro a vista principal nao chama o desenho do canvas."""

    def test_caminho_de_erro_nao_chama_desenho(self):
        with open(TAB, encoding='utf-8') as f:
            src = f.read()
        ini = src.index('function irpRender(erro){')
        fim = src.index('if(sel)sel.innerHTML', ini)
        ramo_erro = src[ini:fim]
        self.assertIn("if(!j||j.status!=='OK'){", ramo_erro)
        self.assertNotIn('irpDesenhar(', ramo_erro)
        self.assertIn('return;', ramo_erro)
        self.assertLess(src.index('function irpRender(erro){'), src.index('function irpDesenhar(j){'))
