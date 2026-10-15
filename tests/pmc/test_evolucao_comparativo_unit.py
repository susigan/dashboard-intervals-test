"""Grafico comparativo dos melhores esforcos (variacao % vs mediana anterior) e eFTP separado.

    python -m unittest discover -s tests/pmc -p 'test_evolucao_comparativo_unit.py'

Executa o JS real de tabs/tab_pmc.py via tests/pmc/js/render_grafico_evol.js (node + canvas
registrado). Pula (SKIP) se node nao estiver disponivel. Nao altera calculos do backend:
a normalizacao e feita pela propria pagina a partir das medianas que o backend entrega.
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'dummy-teste-local')

HARNESS = os.path.join(HERE, 'js', 'render_grafico_evol.js')
NODE = '/opt/node22/bin/node' if os.path.exists('/opt/node22/bin/node') else 'node'
DURACOES = ('60', '300', '720', '1200')
SINAL_EXTREMO = 'extremo'


def _cenario_payload():
    """Payload real (formato de pmc.evolucao_performance) do cenario de teste visual."""
    from test_evolucao_visual_unit import _cenario, _gerar  # reutiliza o cenario existente
    e, c = _cenario()
    return _gerar(e, c, mods=('Bike',))


class ComparativoMelhoresEsforcos(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if shutil.which(NODE) is None and not os.path.exists(NODE):
            raise unittest.SkipTest('node nao disponivel')
        try:
            import tabs.tab_pmc as T
        except BaseException as e:  # config.py encerra sem chave da API
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        html = T.render()
        js = '\n'.join(re.findall(r'<script>(.*?)</script>', html, re.S))
        cls.tmp = tempfile.mkdtemp(prefix='evol_cmp_')
        cls.pjs = os.path.join(cls.tmp, 'pmc.js')
        with open(cls.pjs, 'w', encoding='utf-8') as f:
            f.write(js)
        cls.base = _cenario_payload()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- utilitarios ----
    def _rodar(self, payload, mod='Bike', jan='180', fonte='todas', ocultas=''):
        pay = os.path.join(self.tmp, 'payload.json')
        with open(pay, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        r = subprocess.run([NODE, HARNESS, self.pjs, pay, mod, jan, fonte, ocultas],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        return json.loads(r.stdout)

    @staticmethod
    def _norm(reg, k):
        return next(n['n'] for n in reg['__norm'] if n['k'] == k)

    def _mod(self, payload):
        return payload['modalidades']['Bike']

    # ---- testes ----
    def test_renderizacao_conjunta_das_quatro_duracoes(self):
        reg = self._rodar(self.base)
        self.assertIn('ev_norm', reg)
        self.assertNotIn('ev_m60', reg)                       # graficos antigos fora
        self.assertEqual(set(reg['__ultimo_norm']['desenhados']), set(DURACOES))
        self.assertEqual(reg['__ultimo_norm']['ocultas'], [])

    def test_formula_delta_percentual_contra_mediana_anterior(self):
        reg = self._rodar(self.base, jan='180')
        mods = self._mod(self.base)
        for k in DURACOES:
            with self.subTest(duracao=k):
                med = mods['melhores_esforcos'][k]['janelas']['180']['mediana_anterior']
                n = self._norm(reg, k)
                self.assertTrue(n['ok'])
                self.assertEqual(n['med'], med)
                for p in n['pontos']:
                    if p['valor'] is not None and p['estado'] == 'valido' and p['delta'] is not None:
                        esperado = 100 * (p['valor'] / med - 1)
                        self.assertAlmostEqual(p['delta'], esperado, places=9)

    def test_medianas_indisponiveis_nao_plotam_e_informam_motivo(self):
        casos = [(None, 'mediana anterior ausente'), (0, 'mediana anterior igual a zero'),
                 (-5.0, 'mediana anterior negativa')]
        for valor, motivo in casos:
            with self.subTest(mediana=valor):
                p = copy.deepcopy(self.base)
                p['modalidades']['Bike']['melhores_esforcos']['60']['janelas']['180']['mediana_anterior'] = valor
                reg = self._rodar(p)
                n = self._norm(reg, '60')
                self.assertFalse(n['ok'])
                self.assertIn(motivo, n['motivo'])
                self.assertTrue(all(q['delta'] is None for q in n['pontos']))
                self.assertNotIn('60', reg['__ultimo_norm']['desenhados'])
                self.assertIn('comparação indisponível: ', reg['__html'])
                # as demais duracoes seguem normais
                self.assertTrue(self._norm(reg, '300')['ok'])
                self.assertIn('300', reg['__ultimo_norm']['desenhados'])

    def test_contagens_diferentes_por_duracao_sao_respeitadas(self):
        reg = self._rodar(self.base)
        des = reg['__ultimo_norm']['desenhados']
        for k in DURACOES:
            with self.subTest(duracao=k):
                n = self._norm(reg, k)
                self.assertEqual(len(n['pontos']), len(self._mod(self.base)['melhores_esforcos'][k]['pontos']))
                self.assertEqual(des[k], sum(1 for q in n['pontos'] if q['delta'] is not None))
        self.assertGreater(len(set(des.values())), 1, 'cenario deve ter contagens desenhadas diferentes')

    def test_ausente_zero_negativo_nao_plotados_e_sinalizado_preservado(self):
        p = copy.deepcopy(self.base)
        pts = p['modalidades']['Bike']['melhores_esforcos']['300']['pontos']
        pts[0].update({'valor': None, 'estado': 'ausente'})
        pts[1].update({'valor': 0, 'estado': 'zero'})
        pts[2].update({'valor': -3, 'estado': 'negativo'})
        pts[3]['sinalizacoes'] = sorted(set(pts[3].get('sinalizacoes', []) + [SINAL_EXTREMO]))
        reg = self._rodar(p)
        n = self._norm(reg, '300')
        por_id = {q['id']: q for q in n['pontos']}
        for i in (0, 1, 2):
            self.assertIsNone(por_id[pts[i]['id']]['delta'], f'ponto {i} nao deve ser plotado')
        self.assertIsNotNone(por_id[pts[3]['id']]['delta'])
        self.assertIn(SINAL_EXTREMO, por_id[pts[3]['id']]['sinal'])
        # nada interpolado: desenhados = pontos com delta; zeros/ausentes nao entram
        self.assertEqual(reg['__ultimo_norm']['desenhados']['300'],
                         sum(1 for q in n['pontos'] if q['delta'] is not None))

    def test_sinalizados_nao_sao_excluidos_do_grafico(self):
        reg = self._rodar(self.base)
        n = self._norm(reg, '60')
        sinalizados = [q for q in n['pontos'] if q['sinal']]
        # serie de repeticao (sequencia) do cenario precisa continuar plotada
        self.assertTrue(sinalizados)
        self.assertTrue(all(q['delta'] is not None for q in sinalizados if q['estado'] == 'valido'))

    def test_selecao_90_180_365_muda_medianas_e_mantem_historico(self):
        mods = self._mod(self.base)
        datas_ref = None
        for jan in ('90', '180', '365'):
            with self.subTest(janela=jan):
                reg = self._rodar(self.base, jan=jan)
                for k in DURACOES:
                    med = mods['melhores_esforcos'][k]['janelas'][jan]['mediana_anterior']
                    self.assertEqual(self._norm(reg, k)['med'], med)
                datas = [q['data'] for q in self._norm(reg, '60')['pontos']]
                if datas_ref is None:
                    datas_ref = datas
                self.assertEqual(datas, datas_ref, 'historico visual deve ficar em 730 dias')
                self.assertIn(f'janela de {jan} dias', reg['__html'])

    def test_valores_em_w_e_medianas_do_backend_preservados(self):
        reg = self._rodar(self.base, jan='180')
        mods = self._mod(self.base)
        for k in DURACOES:
            with self.subTest(duracao=k):
                origem = {q['id']: q for q in mods['melhores_esforcos'][k]['pontos']}
                for q in self._norm(reg, k)['pontos']:
                    if q['valor'] is not None:
                        self.assertEqual(q['valor'], origem[q['id']]['valor'])
                med = mods['melhores_esforcos'][k]['janelas']['180']['mediana_anterior']
                self.assertIn(f'{med:.1f} → ', reg['__html'])  # mediana anterior do backend no resumo

    def test_ocultar_e_reexibir_serie_pela_legenda(self):
        oculta = self._rodar(self.base, ocultas='60,720')
        self.assertEqual(sorted(oculta['__ultimo_norm']['ocultas']), ['60', '720'])
        self.assertNotIn('60', oculta['__ultimo_norm']['desenhados'])
        self.assertNotIn('720', oculta['__ultimo_norm']['desenhados'])
        self.assertIn('300', oculta['__ultimo_norm']['desenhados'])
        self.assertIn('aria-pressed="false"', oculta['__html'])
        volta = self._rodar(self.base)
        self.assertEqual(set(volta['__ultimo_norm']['desenhados']), set(DURACOES))

    def test_eftp_atividade_continua_em_grafico_separado(self):
        reg = self._rodar(self.base)
        self.assertIn('eFTP da atividade (icu_pm_ftp)', reg['__html'])
        eftp = self._mod(self.base)['fontes']['eftp_atividade']['pontos']
        validos = sum(1 for q in eftp if q.get('valor') is not None)
        # a tabela de pontos (recolhida) lista cada valor valido; so ela tem 'W' por celula
        self.assertEqual(reg['__tabela_eftp'].count(' W</td>'), validos)
        self.assertIn('eFTP da atividade', reg['__html'])
        # a distincao vive no bloco superior de explicacao (estatico); o grafico nao repete o paragrafo
        src = open(os.path.join(ROOT, 'tabs', 'tab_pmc.py'), encoding='utf-8').read()
        self.assertIn('e não equivale ao FTP configurado', src)
        self.assertNotIn('não equivale ao FTP configurado', reg['__html'])

    def test_payload_incompleto_nao_lanca_e_mantem_demais_duracoes(self):
        p = copy.deepcopy(self.base)
        del p['modalidades']['Bike']['melhores_esforcos']['1200']
        reg = self._rodar(p)
        self.assertEqual(set(reg['__ultimo_norm']['desenhados']), {'60', '300', '720'})
        self.assertIn('ev_norm', reg)

    def test_sem_dados_de_melhores_esforcos_mostra_indisponivel_sem_lancar(self):
        p = copy.deepcopy(self.base)
        p['modalidades']['Bike']['melhores_esforcos'] = {}
        reg = self._rodar(p)
        self.assertEqual(reg['__ultimo_norm']['desenhados'], {})
        self.assertIn('eFTP da atividade (icu_pm_ftp)', reg['__html'])


if __name__ == '__main__':
    unittest.main()
