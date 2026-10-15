"""Robustez da UI de Evolucao da performance: periodo_recente / periodo_anterior
ausentes, nulos ou incompletos nao podem lancar excecao nem impedir os demais graficos.

Usa o JS real da pagina (tabs/tab_pmc.py) com canvas registrado (tests/pmc/js/
render_grafico_evol.js) e payloads sinteticos derivados do cenario conhecido de
test_evolucao_visual_unit. Nao usa dados reais de producao.
"""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


def _des(reg):
    """Pontos desenhados por duracao no grafico comparativo (ev_norm)."""
    return (reg.get('__ultimo_norm') or {}).get('desenhados', {})

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_evolucao_visual_unit import _cenario, _gerar  # noqa: E402

GRAFICOS = ('ev_norm',)  # o eFTP agora e resumo textual (ver EFTP_RESUMO), nao canvas
EFTP_RESUMO = 'eFTP da atividade (icu_pm_ftp)'
NODE = '/opt/node22/bin/node' if os.path.exists('/opt/node22/bin/node') else 'node'
HARNESS = os.path.join(HERE, 'js', 'render_grafico_evol.js')


def _janelas(payload):
    """Todas as janelas de todas as series da modalidade Bike."""
    m = payload['modalidades']['Bike']
    series = [m['fontes']['eftp_atividade']] + list(m['melhores_esforcos'].values())
    for s in series:
        for w in s['janelas'].values():
            yield w


class RobustezEvolucaoUI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if shutil.which('node') is None and not os.path.exists(NODE):
            raise unittest.SkipTest('node nao disponivel')
        import tabs.tab_pmc as T
        html = T.render()
        js = '\n'.join(re.findall(r'<script>(.*?)</script>', html, re.S))
        cls.tmp = tempfile.mkdtemp(prefix='evol_robustez_')
        cls.pjs = os.path.join(cls.tmp, 'pmc.js')
        with open(cls.pjs, 'w', encoding='utf-8') as f:
            f.write(js)
        e, c = _cenario()
        cls.base = _gerar(e, c, mods=('Bike',))
        cls.html = html

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _rodar(self, payload, jan='180', fonte='todas'):
        """Executa mostrarEvolucao() com o payload. Retorna (rc, registro_dos_canvas)."""
        path = os.path.join(self.tmp, 'p.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        r = subprocess.run([NODE, HARNESS, self.pjs, path, 'Bike', jan, fonte],
                           capture_output=True, text=True, timeout=60)
        reg = json.loads(r.stdout) if r.returncode == 0 and r.stdout else {}
        return r.returncode, reg, r.stderr

    # --- comportamento normal permanece igual ---------------------------------

    def test_normal_renderiza_os_cinco_graficos_sem_aviso(self):
        rc, reg, err = self._rodar(copy.deepcopy(self.base))
        self.assertEqual(rc, 0, err[-400:])
        for g in GRAFICOS:
            with self.subTest(grafico=g):
                self.assertIn(g, reg)
        self.assertEqual(_des(reg).get('60'), 97)
        html = reg['__html']
        self.assertIn(EFTP_RESUMO, html)
        self.assertIn('Mediana anterior:', html)
        self.assertIn('Mediana recente:', html)
        self.assertNotIn('Dados insuficientes para comparar', html)
        self.assertIn('(2025-10-20 a 2026-04-17)', html)  # periodo anterior exibido
        self.assertIn('(2026-04-18 a 2026-10-14)', html)  # periodo recente exibido

    # --- periodo_anterior / periodo_recente ausentes ---------------------------

    def test_periodo_anterior_ausente_nao_lanca_e_mantem_pontos(self):
        p = copy.deepcopy(self.base)
        for w in _janelas(p):
            w.pop('periodo_anterior', None)
        rc, reg, err = self._rodar(p)
        self.assertEqual(rc, 0, err[-400:])
        for g in GRAFICOS:
            with self.subTest(grafico=g):
                self.assertIn(g, reg)
        self.assertEqual(_des(reg).get('60'), 97)  # pontos continuam desenhados
        html = reg['__html']
        self.assertNotIn('(2025-10-20 a 2026-04-17)', html)  # sem periodo, sem parenteses do periodo anterior
        self.assertIn('(2026-04-18 a 2026-10-14)', html)     # periodo recente intacto
        self.assertIn('Mediana anterior:', html)
        med = self.base['modalidades']['Bike']['melhores_esforcos']['60']['janelas']['180']['mediana_anterior']
        self.assertIn(f'{med:.1f} → ', html)  # o valor continua no resumo
        self.assertNotIn('()', html)                      # sem parenteses vazios

    def test_periodo_recente_ausente_nao_lanca_e_mantem_pontos(self):
        p = copy.deepcopy(self.base)
        for w in _janelas(p):
            w.pop('periodo_recente', None)
        rc, reg, err = self._rodar(p)
        self.assertEqual(rc, 0, err[-400:])
        self.assertEqual(_des(reg).get('60'), 97)
        html = reg['__html']
        self.assertNotIn('(2026-04-18 a 2026-10-14)', html)
        self.assertIn('(2025-10-20 a 2026-04-17)', html)

    def test_periodos_ambos_ausentes_mantem_todos_os_graficos(self):
        p = copy.deepcopy(self.base)
        for w in _janelas(p):
            w.pop('periodo_anterior', None)
            w.pop('periodo_recente', None)
        rc, reg, err = self._rodar(p)
        self.assertEqual(rc, 0, err[-400:])
        for g in GRAFICOS:
            with self.subTest(grafico=g):
                self.assertIn(g, reg)

    # --- estrutura incompleta ---------------------------------------------------

    def test_periodo_incompleto_ou_nulo_nao_lanca(self):
        formas = [None, [], ['2026-04-18'], [None, None], ['2026-04-18', None], 'texto', 5]
        for forma in formas:
            for campo in ('periodo_anterior', 'periodo_recente'):
                with self.subTest(campo=campo, forma=repr(forma)):
                    p = copy.deepcopy(self.base)
                    for w in _janelas(p):
                        w[campo] = forma
                    rc, reg, err = self._rodar(p)
                    self.assertEqual(rc, 0, err[-400:])
                    for g in GRAFICOS:
                        self.assertIn(g, reg)
                    self.assertIn(EFTP_RESUMO, reg['__html'])  # formas invalidas nao derrubam o resumo

    def test_janelas_ausentes_numa_serie_nao_impedem_as_outras(self):
        p = copy.deepcopy(self.base)
        p['modalidades']['Bike']['melhores_esforcos']['720']['janelas'] = None
        rc, reg, err = self._rodar(p)
        self.assertEqual(rc, 0, err[-400:])
        for g in GRAFICOS:
            with self.subTest(grafico=g):
                self.assertIn(g, reg)
        self.assertEqual(_des(reg).get('60'), 97)

    def test_pontos_nao_array_nao_impedem_os_outros_graficos(self):
        p = copy.deepcopy(self.base)
        p['modalidades']['Bike']['melhores_esforcos']['720']['pontos'] = {'x': 1}
        rc, reg, err = self._rodar(p)
        self.assertEqual(rc, 0, err[-400:])
        for g in GRAFICOS:
            with self.subTest(grafico=g):
                self.assertIn(g, reg)
        self.assertEqual(_des(reg).get('60'), 97)


class FonteQualidadeEEstruturaAusentes(unittest.TestCase):
    """Fonte selecionada, series, 'qualidade' e 'janelas' ausentes ou nulos.
    Nenhum destes casos pode lancar excecao; dados validos nao mudam (ver snapshot).
    Reusa o setup e o helper de RobustezEvolucaoUI sem reexecutar os testes dela."""
    setUpClass = classmethod(RobustezEvolucaoUI.setUpClass.__func__)
    tearDownClass = classmethod(RobustezEvolucaoUI.tearDownClass.__func__)
    _rodar = RobustezEvolucaoUI._rodar

    def _sem(self, caminho):
        p = copy.deepcopy(self.base)
        m = p['modalidades']['Bike']
        alvo = m
        for k in caminho[:-1]:
            alvo = alvo[k]
        alvo.pop(caminho[-1], None)
        return p

    def _com(self, caminho, valor):
        p = copy.deepcopy(self.base)
        alvo = p['modalidades']['Bike']
        for k in caminho[:-1]:
            alvo = alvo[k]
        alvo[caminho[-1]] = valor
        return p

    def test_dados_validos_mostram_contagens_reais(self):
        rc, reg, err = self._rodar(copy.deepcopy(self.base))
        self.assertEqual(rc, 0, err[-400:])
        self.assertIn('(105 atividades, 105 com valor válido)', reg['__tabelas']['populacao'])
        self.assertNotIn('indisponível', reg['__tabelas']['eftp'])

    def test_modalidades_fontes_ausente_nao_lanca_e_mostra_aviso(self):
        rc, reg, err = self._rodar(self._sem(['fontes']))
        self.assertEqual(rc, 0, err[-400:])
        self.assertIn('série indisponível nesta resposta', reg['__tabelas']['eftp'])
        self.assertIn('— atividades', reg['__tabelas']['populacao'])
        # os melhores esforcos seguem desenhados
        self.assertEqual(_des(reg).get('60'), 97)
        self.assertNotIn('Mediana anterior:', reg['__html'])

    def test_melhores_esforcos_ausente_nao_lanca_e_mantem_eftp(self):
        rc, reg, err = self._rodar(self._sem(['melhores_esforcos']))
        self.assertEqual(rc, 0, err[-400:])
        self.assertIn('série indisponível nesta resposta', reg['__tabelas']['melhores'])
        self.assertNotIn('60', _des(reg))
        self.assertIn(EFTP_RESUMO, reg['__html'])
        self.assertIn('Mediana anterior:', reg['__html'])

    def test_eftp_atividade_ausente_so_essa_serie_fica_indisponivel(self):
        p = self._sem(['fontes', 'eftp_atividade'])
        rc, reg, err = self._rodar(p)
        self.assertEqual(rc, 0, err[-400:])
        self.assertIn('série indisponível nesta resposta', reg['__tabelas']['eftp'])
        self.assertNotIn('Mediana anterior:', reg['__html'])
        self.assertEqual(_des(reg).get('60'), 97)
        self.assertIn('— atividades', reg['__tabelas']['populacao'])

    def test_series_nulas_nao_lancam(self):
        p = self._com(['fontes', 'eftp_atividade'], None)
        p['modalidades']['Bike']['melhores_esforcos']['720'] = None
        rc, reg, err = self._rodar(p)
        self.assertEqual(rc, 0, err[-400:])
        self.assertIn('série indisponível nesta resposta', reg['__tabelas']['melhores'])
        self.assertIn('série indisponível nesta resposta', reg['__tabelas']['eftp'])
        self.assertEqual(_des(reg).get('60'), 97)
        self.assertNotIn('720', _des(reg))

    def test_qualidade_ausente_mostra_aviso_e_mantem_pontos(self):
        p = copy.deepcopy(self.base)
        m = p['modalidades']['Bike']
        m['fontes']['eftp_atividade'].pop('qualidade', None)
        m['melhores_esforcos']['60'].pop('qualidade', None)
        rc, reg, err = self._rodar(p)
        self.assertEqual(rc, 0, err[-400:])
        self.assertIn('qualidade não informada nesta resposta', reg['__tabelas']['avisos'])
        self.assertIn('— atividades', reg['__tabelas']['populacao'])
        self.assertEqual(_des(reg).get('60'), 97)  # pontos continuam desenhados
        self.assertIn(EFTP_RESUMO, reg['__html'])

    def test_janelas_ausentes_numa_serie_mostram_traco_sem_lancar(self):
        p = self._com(['melhores_esforcos', '300', 'janelas'], None)
        rc, reg, err = self._rodar(p)
        self.assertEqual(rc, 0, err[-400:])
        self.assertIn('janelas não informadas nesta resposta', reg['__tabelas']['melhores'])
        self.assertEqual(_des(reg).get('60'), 97)

    def test_fonte_selecionada_ausente_nao_lanca(self):
        for fonte in ('ftp_configurado', 'eftp_nao_confirmado', 'eftp_atividade'):
            with self.subTest(fonte=fonte):
                p = self._sem(['fontes', fonte])
                rc, reg, err = self._rodar(p, fonte=fonte)
                self.assertEqual(rc, 0, err[-400:])
                self.assertIn('ev_norm', reg)  # melhores esforcos nao dependem da fonte

    def test_ftp_configurado_ausente_mostra_traco_na_referencia(self):
        p = self._sem(['fontes', 'ftp_configurado'])
        rc, reg, err = self._rodar(p, fonte='ftp_configurado')
        self.assertEqual(rc, 0, err[-400:])
        self.assertIn('FTP configurado (icu_ftp)', reg['__tabelas']['cfg'])
        self.assertIn('<td>—</td>', reg['__tabelas']['cfg'])

    def test_erro_real_do_backend_continua_visivel(self):
        p = copy.deepcopy(self.base)
        p['modalidades'] = None
        p['erro'] = 'falha de calculo'
        rc, reg, err = self._rodar(p)
        self.assertEqual(rc, 0, err[-400:])
        self.assertIn('Evolução indisponível: falha de calculo', reg['__tabelas']['sub'])


class ProtecaoDeCarga(unittest.TestCase):
    """A chamada de mostrarEvolucao em load() nao pode impedir mostrarAlos()."""

    def test_load_protege_mostrar_evolucao_e_mantem_mostrar_alos(self):
        import tabs.tab_pmc as T
        html = T.render()
        m = re.search(r'async function load\(\)\{(.*?)\n\}', html, re.S)
        self.assertIsNotNone(m, 'funcao load() nao encontrada')
        corpo = m.group(1)
        self.assertIn('try{mostrarEvolucao();}catch', corpo)
        self.assertLess(corpo.index('mostrarEvolucao'), corpo.index('mostrarAlos'))

    def test_periodo_helper_existe_e_valida_par(self):
        import tabs.tab_pmc as T
        js = T.render()
        self.assertIn('function evPeriodo(p)', js)
        self.assertIn('Array.isArray(p)&&p.length===2', js)


if __name__ == '__main__':
    unittest.main()
