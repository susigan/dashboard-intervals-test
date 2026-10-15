"""Graficos do PMC: eFTP em resumo textual (sem recalculo), suavizacao homeostatica
com retrospectiva por defeito e causal opcional, e texto neutro sobre a curva estimada.
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


class EftpResumo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _html()
        cls.js = _js(cls.html)

    def test_grafico_eftp_removido(self):
        self.assertNotIn('id="ev_eftp"', self.html)
        self.assertNotIn('drawEvolChart', self.js)
        self.assertNotIn('evBlocoGrafico', self.js)

    def test_resumo_so_le_campos_do_backend(self):
        corpo = _funcao(self.js, 'evResumoEftp')
        self.assertIn('janelas', corpo)
        # nao recalcula medianas, diferencas ou variacoes no frontend
        for proibido in ('median(', 'statistics.', '.reduce(', '.sort(', 'Math.abs(', 'variacao_pct ='):
            self.assertNotIn(proibido, corpo, proibido)

    def test_status_nao_ok_mostra_dados_insuficientes(self):
        corpo = _funcao(self.js, 'evResumoEftp')
        self.assertIn("w.status!=='ok'", corpo)
        self.assertIn('Dados insuficientes para comparar', corpo)

    def test_tabela_de_pontos_recolhida_e_lazy(self):
        self.assertIn('<details class="sub" id="evEftpTab">', self.html)
        self.assertIn('<div id="evEftpTabBody"></div>', self.html)
        self.assertIn('etab.ontoggle=renderEt', self.js)
        self.assertIn("etab.open?evTabelaEftp(F.eftp_atividade):''", self.js)


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


if __name__ == '__main__':
    unittest.main()
