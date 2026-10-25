"""Frontend da vista principal do IR: seletor de periodo (90 dias por defeito) e legenda clicavel.

Apresentacao apenas: o periodo so escolhe um trecho da serie ja calculada no servidor; ocultar uma curva
nao pede dados nem recalcula. Testes de logica pura executam as funcoes reais do ficheiro com Node.
"""
import datetime as dt
import json
import os
import re
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
TAB = os.path.join(ROOT, 'tabs', 'tab_pmc.py')


def _bloco_js(src):
    i = src.index('var IRP_PER=')
    j = src.index('function irpDesenhar(j){')
    return src[i:j]


class PeriodoPorDefeito(unittest.TestCase):
    def setUp(self):
        with open(TAB, encoding='utf-8') as f:
            self.src = f.read()

    def test_padrao_e_90_dias(self):
        self.assertIn("var IRP_PER='90'", self.src)

    def test_seis_opcoes_na_ordem_pedida(self):
        m = re.search(r"var IRP_PERIODOS=(\[.*?\]);", self.src, re.S)
        self.assertIsNotNone(m)
        opcoes = json.loads(m.group(1).replace("'", '"'))
        self.assertEqual([o[0] for o in opcoes], ['90', '180', '3m', '6m', '1a', 'tudo'])
        self.assertEqual([o[1] for o in opcoes], ['90 dias', '180 dias', '3 meses', '6 meses', '1 ano', 'Tudo'])

    def test_seletor_so_redesenha_e_nao_pede_dados(self):
        m = re.search(r'function irpPeriodoSel\(v\)\{.*?\}', self.src, re.S)
        self.assertIsNotNone(m)
        self.assertNotIn('fetch', m.group(0))
        self.assertNotIn('carregar', m.group(0))


class LogicaDeDatas(unittest.TestCase):
    """Executa irpInicio/irpIndiceInicio reais com Node e compara com datas calculadas em Python."""

    ULTIMA = '2026-10-25'

    @classmethod
    def setUpClass(cls):
        with open(TAB, encoding='utf-8') as f:
            cls.bloco = _bloco_js(f.read())

    def _node(self, codigo):
        r = subprocess.run(['node', '-e', self.bloco + '\n' + codigo], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_inicio_de_cada_periodo(self):
        ultima = dt.date(2026, 10, 25)
        esperado = {
            '90': (ultima - dt.timedelta(days=89)).isoformat(),    # 90 dias corridos, inclusive
            '180': (ultima - dt.timedelta(days=179)).isoformat(),
            '3m': '2026-07-25',                                     # calendario retroativo
            '6m': '2026-04-25',
            '1a': '2025-10-25',
            'tudo': None,
        }
        res = self._node("var out={};['90','180','3m','6m','1a','tudo'].forEach(function(p){"
                         "out[p]=irpInicio('%s',p);});console.log(JSON.stringify(out));" % self.ULTIMA)
        self.assertEqual(res, esperado)

    def test_90_dias_mantem_exatamente_90_datas_diarias(self):
        ini = dt.date(2025, 1, 1)
        datas = [(ini + dt.timedelta(days=i)).isoformat() for i in range(500)]
        res = self._node("var d=%s;console.log(JSON.stringify({a:irpIndiceInicio(d,'90'),n:d.length}));"
                         % json.dumps(datas))
        self.assertEqual(res['n'] - res['a'], 90)

    def test_tudo_nao_corta_e_periodo_maior_que_historico_nao_inventa_dados(self):
        datas = ['2025-06-01', '2025-06-02', '2025-06-03']
        res = self._node("var d=%s;console.log(JSON.stringify({tudo:irpIndiceInicio(d,'tudo'),"
                         "um_ano:irpIndiceInicio(d,'1a')}));" % json.dumps(datas))
        self.assertEqual(res['tudo'], 0)
        self.assertEqual(res['um_ano'], 0)   # historico curto: mostra o que existe, sem datas futuras


class LegendaInterativa(unittest.TestCase):
    def setUp(self):
        with open(TAB, encoding='utf-8') as f:
            self.src = f.read()

    def test_legenda_e_botoes_com_estado_aria(self):
        self.assertIn('onclick="irpToggle(', self.src)
        self.assertIn('aria-pressed="\'+(!off)+\'"', self.src)

    def test_alternar_curva_so_redesenha(self):
        m = re.search(r'function irpToggle\(k\)\{.*?\n\}', self.src, re.S)
        self.assertIsNotNone(m)
        corpo = m.group(0)
        self.assertNotIn('fetch', corpo)
        self.assertNotIn('carregar', corpo)
        self.assertIn('irpDesenhar(IRP_J)', corpo)

    def test_desenho_nao_altera_os_dados_recebidos(self):
        bloco = self.src[self.src.index('function irpDesenhar(j){'):self.src.index('// <</ir-principal>>')]
        self.assertIn('datasAll.slice(a0)', bloco)
        self.assertIn('s.v=s.v.slice(a0)', bloco)
        self.assertNotIn('j.datas=', bloco)
        self.assertNotIn('j.overall=', bloco)
        self.assertNotIn('j.modalidades=', bloco)

    def test_curvas_ocultas_nao_entram_no_eixo_y(self):
        bloco = self.src[self.src.index('function irpDesenhar(j){'):self.src.index('// <</ir-principal>>')]
        self.assertIn('var vis=series.filter(', bloco)
        self.assertIn('vis.forEach(function(s){s.v.forEach', bloco)

    def test_mensagem_quando_todas_ocultas(self):
        self.assertIn('Todas as curvas estão ocultas', self.src)


if __name__ == '__main__':
    unittest.main()
