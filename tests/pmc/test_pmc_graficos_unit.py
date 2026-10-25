"""Graficos do PMC: suavizacao homeostatica com retrospectiva por defeito e causal
opcional, e texto neutro sobre a curva estimada. (Resumo eFTP removido com a secção de evolução.)
Verificacoes de fonte/renderizacao; nao alteram calculos nem payload."""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)


def _html():
    import tabs.tab_pmc as T
    return T.render()


def _js(html):
    return '\n'.join(re.findall(r'<script>(.*?)</script>', html, re.S))


def _funcao(js, nome):
    """Corpo de 'function NOME(' ate o proximo topo de bloco."""
    a = js.index('function ' + nome + '(')
    m = re.compile(r'\n(function |const |let |// )').search(js, a + 10)
    return js[a:(m.start() if m else len(js))]


class AusenciaDoGraficoRemovido(unittest.TestCase):
    """Guarda negativa: o grafico da secao «Evolucao da performance» nao deve voltar.
    Verifica apenas os identificadores exclusivos desse grafico, com fronteira de palavra
    (para nao casar com nomes parecidos). Nao depende de elementos partilhados."""
    PADROES_EXCLUSIVOS = (r'\bev_eftp\b', r'\bdrawEvolChart\b', r'\bevBlocoGrafico\b')

    @classmethod
    def setUpClass(cls):
        cls.html = _html()
        cls.js = _js(cls.html)

    def test_identificadores_exclusivos_do_grafico_removido_nao_existem(self):
        for padrao in self.PADROES_EXCLUSIVOS:
            self.assertIsNone(re.search(padrao, self.html), 'HTML contem ' + padrao)
            self.assertIsNone(re.search(padrao, self.js), 'JS contem ' + padrao)


class SuavizacaoHomeostatica(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _html()
        cls.js = _js(cls.html)

    def test_retrospectiva_e_o_padrao(self):
        self.assertIn('<option value="retro" selected>', self.html)
        self.assertNotIn('<option value="causal" selected>', self.html)

    def test_causal_usa_campos_existentes_no_payload(self):
        corpo = _funcao(self.js, 'homeoCampos')
        for campo in ('p_hat_suave_causal', 'banda_sup_causal', 'banda_inf_causal'):
            self.assertIn(campo, corpo)
        for campo in ('p_hat_suave', 'banda_sup', 'banda_inf'):
            self.assertIn(campo, corpo)

    def test_seletor_dispara_redesenho(self):
        self.assertIn("'homeoSuav'", self.js)
        self.assertIn('id="homeoSuav"', self.html)

    def test_sem_calculo_novo_no_frontend(self):
        corpo = _funcao(self.js, 'drawHomeo')
        for proibido in ('savgol', 'Savitzky', 'stddev', 'EWM', 'ewm('):
            self.assertNotIn(proibido, corpo, proibido)


class TextoNeutroHomeostatico(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _js(_html())

    def test_sem_interpretacao_fisiologica(self):
        for proibido in ('Melhor adaptacao', 'carga sem compensacao', 'fase de acumulacao',
                         'adaptação na fase recente', 'fadiga confirmada'):
            self.assertNotIn(proibido, self.js, proibido)

    def test_descreve_curva_faixa_e_periodos(self):
        corpo = _funcao(self.js, 'drawHomeo')
        self.assertIn('curva estimada pelo modelo', corpo)
        self.assertIn('desvio-padrão móvel de 14 dias', corpo)
        self.assertIn('não é intervalo de confiança nem previsão', corpo)
        self.assertIn('desenhados separadamente, sem ligação entre eles', corpo)
        self.assertNotIn('intervalo de confiança', corpo.replace('não é intervalo de confiança', ''))


class RenderizacaoPMCSemRotuloIndevido(unittest.TestCase):
    """eFTP da atividade nunca apresentado como FTP medido, no HTML e no JS efetivamente renderizados.
    Limitacao: verificacao por texto exato; um rotulo reformulado (outro espacamento ou texto) nao seria detetado."""

    @classmethod
    def setUpClass(cls):
        cls.html = _html()
        cls.js = _js(cls.html)

    def test_sem_rotulo_ftp_medido_no_html_nem_no_js(self):
        self.assertNotIn('FTP medido', self.html)
        self.assertNotIn('FTP medido', self.js)

    def test_sem_campo_ftp_medido_no_js(self):
        self.assertNotIn("'ftp_medido'", self.js)


class BlocoHomeostaticoOculto(unittest.TestCase):
    """Decisao existente (ja no origin/main): o bloco do modelo homeostatico fica oculto na visao principal.
    O teste protege o bloco, os controlos e o canvas que drawHomeo usa, sem depender da ordem dos atributos."""

    @classmethod
    def setUpClass(cls):
        cls.html = _html()

    def test_bloco_homeostatico_oculto_com_modelo_controlos_e_canvas(self):
        m = re.search(r'<div id="homeoLegado"[^>]*\bhidden\b[^>]*>', self.html)
        self.assertIsNotNone(m, 'bloco homeoLegado deixou de estar oculto')
        fim = self.html.index('<h2>Indice alostatico</h2>')
        bloco = self.html[m.start():fim]
        for trecho in ('Modelo homeostatico', 'id="chHomeo"', 'id="homeoMod"', 'id="homeoVista"', 'id="homeoSuav"'):
            self.assertIn(trecho, bloco, trecho)
        self.assertNotIn('Modelo homeostatico', self.html[:m.start()])


class CarregamentoChamaMostrarAlos(unittest.TestCase):
    """load() continua a chamar mostrarAlos como instrucao (nao em comentario) apos a remocao da secao."""

    @classmethod
    def setUpClass(cls):
        cls.js = _js(_html())

    def test_load_chama_mostrar_alos(self):
        corpo = _funcao(self.js, 'load')
        self.assertRegex(corpo, r'(?m)^\s*mostrarAlos\(\);')


if __name__ == '__main__':
    unittest.main()
