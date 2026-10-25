"""Contrato do topo do payload de api_data (tabs/tab_pmc.py).

Garante que as 22 chaves originais continuam no payload. A secção «Evolução da
performance» foi removida; os testes que comparavam com o commit de produção
3c3585f (que já continha essa secção) e com o código de pmc.py desse commit
foram retirados, porque deixaram de ter objeto.
"""
import ast
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

CHAVES_ORIGINAIS = {  # nomes das 22 chaves de api_data (sem valores)
    'actual', 'alertas', 'alostatico', 'ciclicos', 'cores', 'corporal', 'cp_fonte',
    'dtrimp_dkj', 'eficiencia_kj', 'erro_ftlm', 'erros_sheets', 'escala_1a5', 'fmt',
    'ftlm', 'homeostatico', 'homeostatico_mod', 'por_modalidade', 'serie', 'sessoes',
    'sheets_ok', 'status', 'wellness'}


def _chaves_api_data(src):
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == 'api_data':
            for r in ast.walk(n):
                if (isinstance(r, ast.Return) and isinstance(r.value, ast.Call)
                        and getattr(r.value.func, 'id', None) == 'jsonify'
                        and r.value.args and isinstance(r.value.args[0], ast.Dict)):
                    return {k.value for k in r.value.args[0].keys if isinstance(k, ast.Constant)}
    return None


class ContratoDeTopoDoPayload(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'tabs', 'tab_pmc.py'), encoding='utf-8') as f:
            cls.atual = f.read()

    def test_vinte_e_duas_chaves_originais_continuam_no_topo(self):
        chaves = _chaves_api_data(self.atual)
        self.assertIsNotNone(chaves)
        self.assertTrue(CHAVES_ORIGINAIS <= chaves, CHAVES_ORIGINAIS - chaves)


if __name__ == '__main__':
    unittest.main()
