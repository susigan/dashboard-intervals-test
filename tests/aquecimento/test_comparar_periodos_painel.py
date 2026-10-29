"""Painel 'Comparar periodos': sem sessao escolhida, A e B por datas, com delta.

Verificacoes de texto do HTML/JS e sintaxe do JavaScript com Node (se existir).
Dados sinteticos; nao usa a API nem a base de dados.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _ler():
    with open(os.path.join(RAIZ, 'tabs', 'tab_metabol.py'), encoding='utf-8') as f:
        return f.read()


class ComparaPeriodos(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.s = _ler()

    def test_ids_do_html(self):
        for ident in ['id="aqPerAIni"', 'id="aqPerAFim"', 'id="aqPerBIni"', 'id="aqPerBFim"',
                      'id="aqPerMeta"', 'id="aqPerCont"']:
            self.assertIn(ident, self.s, ident)

    def test_seccao_blocos_da_sessao_removida(self):
        for proibido in ['aqBlocosSessao', 'aqBlocosCarregar', 'AQB_METRICAS', 'chBlocos']:
            self.assertNotIn(proibido, self.s, proibido)

    def test_pede_comparativo_por_datas_nos_dois_periodos(self):
        self.assertIn("'&inicio=' + per[p][0] + '&fim=' + per[p][1]", self.s)
        self.assertIn("['A', 'B'].forEach", self.s)

    def test_metricas_do_painel(self):
        for k in ["k: 'hr'", "k: 'smo2'", "k: 'resp'", "k: 'dfa1'", "k: 'fc_rec1min'"]:
            self.assertIn(k, self.s, k)

    def test_delta_e_b_menos_a(self):
        self.assertIn('Δ B − A', self.s)
        self.assertIn('b.media - a.media', self.s)

    def test_ausencia_mostra_travessao_nao_zero(self):
        self.assertIn("'<td style=\"padding:4px 8px;color:#8b949e;\">—</td>'", self.s)

    def test_escapa_texto_do_servidor(self):
        self.assertIn('aqcEsc(msg)', self.s)
        self.assertIn('aqcEsc(A.ini)', self.s)

    def test_sem_persistencia(self):
        i = self.s.index('function aqPerIso')
        j = self.s.index('function aqExportar')
        bloco = self.s[i:j]
        for proibido in ['localStorage', 'sessionStorage', "method: 'POST'"]:
            self.assertNotIn(proibido, bloco, proibido)

    def test_sintaxe_javascript_valida(self):
        if not shutil.which('node'):
            self.skipTest('node indisponivel')
        blocos = [m.group(1) for m in re.finditer(r'r"""(.*?)"""', self.s, re.S)
                  if 'function aqPerCarregar' in m.group(1)]
        self.assertEqual(len(blocos), 1)
        with tempfile.TemporaryDirectory() as d:
            fn = os.path.join(d, 'bloco.js')
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(blocos[0])
            r = subprocess.run(['node', '--check', fn], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[:400])


if __name__ == '__main__':
    unittest.main()
