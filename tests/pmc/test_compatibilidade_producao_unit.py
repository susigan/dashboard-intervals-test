"""Compatibilidade do payload com a producao (commit 3c3585fa, origin/main).

Sem fixture de producao: nenhum dado real e versionado. A comparacao usa:
  (1) AST de tabs/tab_pmc.py: chaves de topo de api_data e a propria funcao api_data;
  (2) pmc.py de producao lido de `git show 3c3585fa:pmc.py` e executado sobre o mesmo
      cenario sintetico de test_evolucao_visual_unit.

Limitacao declarada: o payload completo (valores reais das 22 chaves originais) nao e
reproduzido aqui; a verificacao de valores reais exige a captura de producao, que nao
pode ser versionada. Se o commit 3c3585fa nao existir no clone, os testes que dependem
dele sao pulados com o motivo.
"""
import ast
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import pmc  # noqa: E402
from test_evolucao_visual_unit import _cenario, REF  # noqa: E402

COMMIT_PRODUCAO = '3c3585fa'
CHAVES_ORIGINAIS = {  # nomes das 22 chaves de api_data antes da evolucao (sem valores)
    'actual', 'alertas', 'alostatico', 'ciclicos', 'cores', 'corporal', 'cp_fonte',
    'dtrimp_dkj', 'eficiencia_kj', 'erro_ftlm', 'erros_sheets', 'escala_1a5', 'fmt',
    'ftlm', 'homeostatico', 'homeostatico_mod', 'por_modalidade', 'serie', 'sessoes',
    'sheets_ok', 'status', 'wellness'}
CHAVES_ADITIVAS_PERMITIDAS = {'pontos', 'ultimo_valor', 'periodo_recente', 'periodo_anterior'}
SERIES_COM_PONTOS = {'eftp_atividade', 'melhor_60', 'melhor_300', 'melhor_720', 'melhor_1200'}
SERIES_SEM_PONTOS = {'ftp_configurado', 'eftp_nao_confirmado'}


def _git_show(caminho):
    r = subprocess.run(['git', 'show', f'{COMMIT_PRODUCAO}:{caminho}'], cwd=ROOT,
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _chaves_api_data(src):
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == 'api_data':
            for r in ast.walk(n):
                if (isinstance(r, ast.Return) and isinstance(r.value, ast.Call)
                        and getattr(r.value.func, 'id', None) == 'jsonify'
                        and r.value.args and isinstance(r.value.args[0], ast.Dict)):
                    return {k.value for k in r.value.args[0].keys if isinstance(k, ast.Constant)}
    return None


def _funcao_api_data(src):
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == 'api_data':
            return ast.dump(n)
    return None


def _nos(obj, pref=()):
    """Todos os nos (contineres e folhas) como (caminho, valor). Contineres viram marcador."""
    if isinstance(obj, dict):
        yield pref, '<dict>'
        for k, v in obj.items():
            yield from _nos(v, pref + (k,))
    elif isinstance(obj, list):
        yield pref, '<list>'
        for i, v in enumerate(obj):
            yield from _nos(v, pref + (i,))
    else:
        yield pref, obj


def _raizes_novas(prod_nos, nova_nos):
    """Primeiro caminho novo de cada subarvore adicionada (o que a producao nao tem)."""
    raizes = set()
    for c in nova_nos - prod_nos:
        for i in range(1, len(c) + 1):
            if c[:i] not in prod_nos:
                raizes.add(c[:i])
                break
    return raizes


class ContratoDeTopoDoPayload(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.atual = open(os.path.join(ROOT, 'tabs', 'tab_pmc.py'), encoding='utf-8').read()
        cls.producao = _git_show('tabs/tab_pmc.py')
        if cls.producao is None:
            raise unittest.SkipTest(f'commit {COMMIT_PRODUCAO} indisponivel neste clone')

    def test_vinte_e_duas_chaves_originais_continuam_no_topo(self):
        chaves = _chaves_api_data(self.atual)
        self.assertIsNotNone(chaves)
        self.assertTrue(CHAVES_ORIGINAIS <= chaves, CHAVES_ORIGINAIS - chaves)

    def test_mesmo_conjunto_de_chaves_de_topo_que_producao(self):
        self.assertEqual(_chaves_api_data(self.atual), _chaves_api_data(self.producao))

    def test_evolucao_performance_e_a_unica_chave_fora_das_originais(self):
        extras = _chaves_api_data(self.atual) - CHAVES_ORIGINAIS
        self.assertEqual(extras, {'evolucao_performance'})

    def test_funcao_api_data_identica_a_producao(self):
        # Os valores das 22 chaves originais vem do mesmo codigo: a funcao nao mudou.
        self.assertEqual(_funcao_api_data(self.atual), _funcao_api_data(self.producao))


class EvolucaoAditivaEmRelacaoAProducao(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        src = _git_show('pmc.py')
        if src is None:
            raise unittest.SkipTest(f'commit {COMMIT_PRODUCAO} indisponivel neste clone')
        cls.tmp = tempfile.mkdtemp(prefix='compat_prod_')
        caminho = os.path.join(cls.tmp, 'pmc_producao.py')
        with open(caminho, 'w', encoding='utf-8') as f:
            f.write(src)
        spec = importlib.util.spec_from_file_location('pmc_producao', caminho)
        cls.prod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.prod)
        entradas, curvas = _cenario()
        cls.saida_prod = cls.prod.evolucao_performance(entradas, curvas, REF, modalidades=('Bike',))
        cls.saida_nova = pmc.evolucao_performance(entradas, curvas, REF, modalidades=('Bike',))

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_nenhum_valor_de_producao_foi_removido_ou_alterado(self):
        nova = dict(_nos(self.saida_nova))
        for caminho, valor in _nos(self.saida_prod):
            with self.subTest(caminho=caminho):
                self.assertIn(caminho, nova, 'caminho de producao removido')
                self.assertEqual(nova[caminho], valor)

    def test_unicas_adicoes_sao_os_campos_aditivos_previstos(self):
        prod = {c for c, _ in _nos(self.saida_prod)}
        nova = {c for c, _ in _nos(self.saida_nova)}
        raizes = _raizes_novas(prod, nova)
        self.assertTrue(raizes, 'a evolucao nova deveria acrescentar campos')
        nomes = {r[-1] for r in raizes}
        self.assertLessEqual(nomes, CHAVES_ADITIVAS_PERMITIDAS, nomes - CHAVES_ADITIVAS_PERMITIDAS)

    def test_pontos_so_nas_series_com_grafico(self):
        m = self.saida_nova['modalidades']['Bike']
        com = {'eftp_atividade': m['fontes']['eftp_atividade'],
               'melhor_60': m['melhores_esforcos']['60'], 'melhor_300': m['melhores_esforcos']['300'],
               'melhor_720': m['melhores_esforcos']['720'], 'melhor_1200': m['melhores_esforcos']['1200']}
        for nome, s in com.items():
            with self.subTest(serie=nome):
                self.assertIn('pontos', s)
                self.assertIsInstance(s['pontos'], list)

    def test_ftp_configurado_e_eftp_nao_confirmado_sem_pontos(self):
        m = self.saida_nova['modalidades']['Bike']
        for nome in SERIES_SEM_PONTOS:
            with self.subTest(serie=nome):
                self.assertNotIn('pontos', m['fontes'][nome])

    def test_pontos_dos_cinco_graficos_nao_vazios(self):
        m = self.saida_nova['modalidades']['Bike']
        series = [m['fontes']['eftp_atividade']] + [m['melhores_esforcos'][k] for k in ('60', '300', '720', '1200')]
        for s in series:
            with self.subTest(serie=s.get('rotulo', '')):
                self.assertTrue(any(p.get('estado') == 'valido' for p in s['pontos']))


if __name__ == '__main__':
    unittest.main()
