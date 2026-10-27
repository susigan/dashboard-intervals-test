"""Testes da logica JavaScript do painel ordinal de blocos (Aquecimento).

Extrai do proprio tabs/tab_metabol.py as funcoes puras do painel e executa-as
com node. Cobre: ordenacao dos intervalos, ausencia nunca convertida em zero,
zeros assinalados como duvidosos e fora das estatisticas, ausencia de
cadencia/torque/elevacao, e ligacoes do painel a navegacao.

Isto e' teste unitario da logica. NAO e' validacao visual: o desenho no
canvas nao e' coberto aqui.
"""
import json
import pathlib
import shutil
import subprocess
import unittest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
TAB = RAIZ / "tabs" / "tab_metabol.py"


def _fonte():
    return TAB.read_text(encoding="utf-8")


def _bloco_logica():
    """Desde AQB_METRICAS ate a funcao de desenho (so logica, sem canvas)."""
    s = _fonte()
    ini = s.index("const AQB_METRICAS")
    fim = s.index("function aqBlocosDraw(s){")
    return s[ini:fim]


NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node nao disponivel")
class LogicaPainelJS(unittest.TestCase):

    def _correr(self, expressao):
        """Executa uma expressao JS no contexto da logica extraida."""
        script = _bloco_logica() + "\nprocess.stdout.write(JSON.stringify(" + expressao + "));"
        out = subprocess.run([NODE, "-e", script], capture_output=True,
                             text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_ordenacao_por_interval_num_e_bloco_num(self):
        expr = ("aqBlocosOrdenados({blocos:["
                "{interval_num:3,bloco_num:3},"
                "{interval_num:1,bloco_num:2},"
                "{interval_num:1,bloco_num:1}]}).map(b=>b.bloco_num)")
        self.assertEqual(self._correr(expr), [1, 2, 3])

    def test_ordenacao_sem_sessao_devolve_lista_vazia(self):
        self.assertEqual(self._correr("aqBlocosOrdenados(null)"), [])

    def test_ausencia_nunca_vira_zero(self):
        expr = ("[aqBlocosValor({hr_avg:null},'hr'),"
                "aqBlocosValor({},'hr'),"
                "aqBlocosValor({hr_avg:undefined},'hr')]")
        self.assertEqual(self._correr(expr), [None, None, None])

    def test_ausencia_nao_e_duvidosa(self):
        expr = "[aqBlocosDuvidoso({hr_avg:null},'hr'), aqBlocosDuvidoso({},'hr')]"
        self.assertEqual(self._correr(expr), [False, False])

    def test_zero_e_duvidoso_e_fica_fora_do_valor(self):
        # zero em qualquer metrica: nao entra em escalas nem estatisticas
        for m in ("hr", "smo2", "resp", "dfa1"):
            with self.subTest(metrica=m):
                expr = (f"[aqBlocosValor({{{m}_avg:0}},'{m}'),"
                        f"aqBlocosDuvidoso({{{m}_avg:0}},'{m}')]")
                self.assertEqual(self._correr(expr), [None, True])

    def test_valor_real_e_preservado(self):
        expr = ("[aqBlocosValor({dfa1_avg:0.91},'dfa1'),"
                "aqBlocosValor({hr_avg:131.2},'hr'),"
                "aqBlocosDuvidoso({dfa1_avg:0.91},'dfa1')]")
        self.assertEqual(self._correr(expr), [0.91, 131.2, False])

    def test_metricas_do_painel_sao_as_quatro(self):
        nomes = self._correr("AQB_METRICAS.map(m=>m.k)")
        self.assertEqual(nomes, ["hr", "dfa1", "smo2", "resp"])

    def test_sem_cadencia_torque_elevacao(self):
        bloco = _bloco_logica().lower()
        for proibido in ("cadence", "cadência", "cadencia", "torque", "elevation",
                         "elevação", "elevacao", "aqb_cad", "aqb_tor", "aqb_ele"):
            with self.subTest(termo=proibido):
                self.assertNotIn(proibido, bloco)

    def test_painel_ligado_a_navegacao(self):
        s = _fonte()
        self.assertIn("aqBlocosCarregar(); }", s, "carregar ao abrir a sub-aba")
        self.assertIn("aqContexto(met, agr); aqBlocosCarregar();", s,
                      "recarregar ao mudar metrica/agregacao")
        for id_ in ("aqBlocosSessao", "chBlocos", "aqBlocosTip", "aqBlocosEstado"):
            with self.subTest(id=id_):
                self.assertIn(f'id="{id_}"', s)

    def test_nota_de_origem_das_medias_presente(self):
        self.assertIn("origem por confirmar", _fonte())


class Estrutura(unittest.TestCase):
    """Verificacoes que nao precisam de node."""

    def test_js_do_painel_existe_uma_vez(self):
        s = _fonte()
        self.assertEqual(s.count("function aqBlocosDraw(s){"), 1)
        self.assertEqual(s.count("const AQB_METRICAS"), 1)


if __name__ == "__main__":
    unittest.main()
