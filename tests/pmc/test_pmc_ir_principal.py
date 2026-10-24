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
        self.assertIn('function irxPintar(cvId,its,legId,vazio){', self.src)
        self.assertIn("irxPintar('chIrxMain',its,'irxLegMain'", self.src)
        self.assertEqual(self.src.count('id="chIrxMain"'), 1)
        self.assertNotIn('id="chIrx"', self.src)      # sem segundo canvas na secao tecnica
        self.assertNotIn('irxDur', self.src)           # sem seletor de duracao concorrente

    def test_secao_tecnica_unica_recolhida(self):
        i = self.src.index('id="irTecnico"')
        j = self.src.index('</details>', self.src.index('id="irSombra"')) + len('</details>')
        bloco = self.src[i:j]
        self.assertEqual(self.src.count('id="irTecnico"'), 1)
        for ident in ('id="irExp"', 'id="irExploratorio"', 'id="irSombra"'):
            self.assertIn(ident, bloco)                # os tres diagnosticos vivem dentro da secao tecnica
        self.assertNotIn('ontoggle="abrirIR(this)"', self.src[:i])   # nenhum fica fora, a concorrer com o principal
        self.assertLess(self.src.index('id="irPrincipal"'), i)

    def test_mensagem_curta_por_estado(self):
        # executa a funcao real do ficheiro com estados de exemplo (sem alterar o codigo)
        import re, subprocess, json as _json
        m = re.search(r'function irxMensagemCurta\(s\)\{.*?\n\}', self.src, re.S)
        self.assertIsNotNone(m)
        casos = {
            'dados_insuficientes': {'estado': 'dados_insuficientes', 'motivo': 'menos de 15 observacoes'},
            'sem_observacoes': {'estado': 'sem_observacoes', 'motivo': 'nenhuma observacao elegivel'},
            'limites': {'estado': 'sem_ajuste_identificavel', 'motivo': 'ajuste nao identificavel: parametro_no_limite:t1:superior, mal_condicionado'},
            'mal': {'estado': 'sem_ajuste_identificavel', 'motivo': 'ajuste nao identificavel: mal_condicionado'},
        }
        js = 'var out={};var casos=' + _json.dumps(casos) + ';for(var k in casos){out[k]=irxMensagemCurta(casos[k]);}console.log(JSON.stringify(out));'
        js = m.group(0) + '\n' + js
        r = subprocess.run(['node', '-e', js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = _json.loads(r.stdout)
        self.assertEqual(out['dados_insuficientes'], 'sem curva: observações insuficientes (mínimo de 15 por duração)')
        self.assertEqual(out['sem_observacoes'], 'sem esforços elegíveis nesta modalidade')
        self.assertIn('parâmetros nos limites', out['limites'])
        self.assertIn('mal condicionado', out['mal'])
        self.assertTrue(all(len(v) < 90 for v in out.values()))

    def test_carregamento_automatico_na_abertura(self):
        self.assertIn('carregarIRExploratorio();\n"""', self.src)

    def test_estado_de_erro_nao_fabrica_curva(self):
        self.assertIn("Modo exploratório indisponível", self.src)
        self.assertIn("Nenhuma modalidade com curva identificável", self.src)


if __name__ == '__main__':
    unittest.main()


class ErroNaoDesenha(unittest.TestCase):
    """Regressao: no caminho de erro a secao principal nao chama o desenho do canvas."""

    def test_caminho_de_erro_nao_chama_desenho(self):
        with open(TAB, encoding='utf-8') as f:
            src = f.read()
        ini = src.index('function irxRenderMain(){')
        fim = src.index('function irxRender(erro){')
        ramo_erro = src[ini:src.index('if(aviso)aviso.textContent=\'Exploratório, não validado', ini)]
        self.assertNotIn('irxDesenhar(', ramo_erro)
        self.assertNotIn('irxPintar(', ramo_erro)
        self.assertIn("lg.innerHTML=''", ramo_erro)
        self.assertLess(ini, fim)
