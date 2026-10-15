"""Graficos da evolucao: pontos por atividade, medianas de janela, sinalizacoes,
populacoes e renderizacao deterministica. Dados sinteticos com resposta conhecida;
nao sao dados reais e nao sao validacao das fontes cruas.
"""
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta


def _des(reg):
    """Pontos desenhados por duracao no grafico comparativo (ev_norm)."""
    return (reg.get('__ultimo_norm') or {}).get('desenhados', {})

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

import pmc  # noqa: E402

REF = '2026-10-14'
REF_D = date(2026, 10, 14)
SECS = (60, 300, 720, 1200)
ESTADOS_OK = {'valido', 'ausente', 'zero', 'negativo'}
SINAIS_OK = {'sequencia', 'suspeito', 'extremo', 'excluido'}


def _cenario():
    """100 sessoes semanais (693 dias). Casos conhecidos:
    k=3 zero em 60 s; k=4 sem 60 s (ausente); k=5 60 s negativo; k=6 extremo (900 W);
    k=20..29 sequencia de 10 valores iguais (250 W) em 60 s.
    Eftp: 105 atividades; 5 sem curva (k=100..104)."""
    entradas, curvas = [], []
    for k in range(105):
        d = (REF_D - timedelta(days=7 * k)).isoformat()
        entradas.append({'id': f'a{k}', 'date': d, 'type': 'Bike',
                         'icu_pm_ftp': 200.0 + (k % 5), 'icu_ftp': 250, 'EFTP': None})
        if k >= 100:
            continue
        w60 = 300 + (k % 7)
        if k == 3:
            w60 = 0
        if k == 5:
            w60 = -1
        if k == 6:
            w60 = 900
        if 20 <= k <= 29:
            w60 = 250
        secs = [s for s in SECS if not (k == 4 and s == 60)]
        watts = [{60: w60, 300: 280, 720: 260, 1200: 240}[s] for s in secs]
        curvas.append({'activity_id': f'a{k}', 'type': 'Bike', 'date': d,
                       'secs': secs, 'watts': watts})
    return entradas, curvas


def _gerar(entradas=None, curvas=None, mods=('Bike',), confirmados=None):
    if entradas is None:
        entradas, curvas = _cenario()
    return pmc.evolucao_performance(entradas, curvas or [], REF, modalidades=mods,
                                    padroes_confirmados=confirmados)


def _serie60(res):
    return res['modalidades']['Bike']['melhores_esforcos']['60']


class PontosBackend(unittest.TestCase):

    def setUp(self):
        self.res = _gerar()
        self.p60 = _serie60(self.res)['pontos']

    def test_um_ponto_por_atividade_e_duracao(self):
        ids = [p['id'] for p in self.p60]
        self.assertEqual(len(ids), 100)
        self.assertEqual(len(set(ids)), 100)

    def test_valor_exato_sem_interpolacao(self):
        p = {x['id']: x for x in self.p60}
        self.assertEqual(p['a0']['valor'], 300.0)
        self.assertEqual(p['a1']['valor'], 301.0)

    def test_ausencia_zero_e_negativo_nao_viram_valor(self):
        p = {x['id']: x for x in self.p60}
        self.assertEqual(p['a4']['estado'], 'ausente')
        self.assertIsNone(p['a4']['valor'])
        self.assertEqual(p['a3']['estado'], 'zero')
        self.assertIsNone(p['a3']['valor'])
        self.assertEqual(p['a5']['estado'], 'negativo')
        self.assertIsNone(p['a5']['valor'])

    def test_extremo_e_sinalizado_mas_preservado(self):
        p = {x['id']: x for x in self.p60}
        self.assertEqual(p['a6']['valor'], 900.0)
        self.assertIn('extremo', p['a6']['sinalizacoes'])
        self.assertEqual(p['a6']['estado'], 'valido')

    def test_sequencia_sinalizada_e_mantida_na_tendencia(self):
        p = {x['id']: x for x in self.p60}
        for k in range(20, 30):
            self.assertIn('sequencia', p[f'a{k}']['sinalizacoes'])
            self.assertEqual(p[f'a{k}']['valor'], 250.0)
        self.assertEqual(_serie60(self.res)['qualidade']['n_excluidos_tendencia'], 0)

    def test_estados_e_sinalizacoes_restritos(self):
        for serie in list(self.res['modalidades']['Bike']['fontes'].values()) + \
                     list(self.res['modalidades']['Bike']['melhores_esforcos'].values()):
            for p in serie.get('pontos', []):
                self.assertIn(p['estado'], ESTADOS_OK)
                self.assertTrue(set(p['sinalizacoes']) <= SINAIS_OK)

    def test_pontos_dentro_de_730_dias(self):
        lim = (REF_D - timedelta(days=730)).isoformat()
        for p in self.p60:
            self.assertGreaterEqual(p['data'], lim)
            self.assertLessEqual(p['data'], REF)

    def test_ultimo_valor_e_o_ultimo_valido_por_data(self):
        validos = sorted([p for p in self.p60 if p['estado'] == 'valido'], key=lambda p: p['data'])
        self.assertEqual(_serie60(self.res)['ultimo_valor'], validos[-1]['valor'])


class MedianasReproduzidas(unittest.TestCase):

    def test_medianas_saem_dos_pontos_no_periodo(self):
        res = _gerar()
        s = _serie60(res)
        for W in (90, 180, 365):
            with self.subTest(W=W):
                jan = s['janelas'][str(W)]
                ini_rec, ini_ant = REF_D - timedelta(days=W), REF_D - timedelta(days=2 * W)
                rec = [p['valor'] for p in s['pontos'] if p['estado'] == 'valido'
                       and ini_rec < date.fromisoformat(p['data']) <= REF_D]
                ant = [p['valor'] for p in s['pontos'] if p['estado'] == 'valido'
                       and ini_ant < date.fromisoformat(p['data']) <= ini_rec]
                if jan['status'] == 'ok':
                    self.assertEqual(jan['mediana_recente'], round(statistics.median(rec), 2))
                    self.assertEqual(jan['mediana_anterior'], round(statistics.median(ant), 2))

    def test_periodos_das_janelas_sao_inclusivos_e_contiguos(self):
        s = _serie60(_gerar())
        j = s['janelas']['180']
        self.assertEqual(j['periodo_recente'], [(REF_D - timedelta(days=179)).isoformat(), REF])
        self.assertEqual(j['periodo_anterior'], [(REF_D - timedelta(days=359)).isoformat(),
                                                 (REF_D - timedelta(days=180)).isoformat()])


class PontosSomenteOndeHaGrafico(unittest.TestCase):
    """FTP configurado e EFTP nao confirmado nao tem grafico: sem a serie de pontos no
    payload, mas com qualidade, janelas e ultimo_valor preservados."""

    def test_blocos_sem_grafico_nao_tem_pontos(self):
        res = _gerar()
        for chave in ('ftp_configurado', 'eftp_nao_confirmado'):
            with self.subTest(fonte=chave):
                f = res['modalidades']['Bike']['fontes'][chave]
                self.assertNotIn('pontos', f)
                self.assertIn('qualidade', f)
                self.assertIn('janelas', f)
                self.assertIn('ultimo_valor', f)

    def test_blocos_de_grafico_mantem_pontos(self):
        res = _gerar()
        self.assertIn('pontos', res['modalidades']['Bike']['fontes']['eftp_atividade'])
        for secs in ('60', '300', '720', '1200'):
            self.assertIn('pontos', res['modalidades']['Bike']['melhores_esforcos'][secs])

    def test_qualidade_e_janelas_iguais_com_ou_sem_pontos(self):
        # Mesmas entradas, com e sem a serie de pontos: so a chave 'pontos' pode diferir.
        brutos = [(d, v, f'x{k}') for k, (d, v) in enumerate(
            [((REF_D - timedelta(days=7 * k)).isoformat(), 200.0 + k % 6) for k in range(60)])]
        com = pmc._evol_serie(brutos, REF, 'eFTP', 'icu_pm_ftp', eftp=True, com_pontos=True)
        sem = pmc._evol_serie(brutos, REF, 'eFTP', 'icu_pm_ftp', eftp=True, com_pontos=False)
        self.assertIn('pontos', com)
        self.assertNotIn('pontos', sem)
        self.assertEqual(json.dumps({k: v for k, v in com.items() if k != 'pontos'}, sort_keys=True),
                         json.dumps(sem, sort_keys=True))


class PopulacoesEContagens(unittest.TestCase):

    def test_contagens_com_e_sem_curvas(self):
        e, c = _cenario()
        com = _gerar(e, c)
        sem = _gerar(e, [])
        self.assertEqual(com['modalidades']['Bike']['fontes']['eftp_atividade']['qualidade']['n_brutos'], 105)
        self.assertEqual(_serie60(com)['qualidade']['n_brutos'], 100)
        self.assertEqual(_serie60(sem)['qualidade']['n_brutos'], 0)
        self.assertEqual(_serie60(sem)['pontos'], [])
        self.assertEqual(_serie60(sem)['janelas']['180']['motivos'], ['sem_dados'])

    def test_eftp_nao_e_substituido_por_melhor_esforco(self):
        e, c = _cenario()
        res = _gerar(e, c)
        eftp = res['modalidades']['Bike']['fontes']['eftp_atividade']['pontos']
        self.assertEqual(len(eftp), 105)
        self.assertTrue(all(p['valor'] is None or p['valor'] < 210 for p in eftp))

    def test_ftp_configurado_tem_ultimo_valor_e_sem_pontos_de_tendencia_na_janela(self):
        res = _gerar()
        cfg = res['modalidades']['Bike']['fontes']['ftp_configurado']
        self.assertEqual(cfg['ultimo_valor'], 250.0)


class SelecaoEClassificacao(unittest.TestCase):

    def test_so_modalidade_pedida(self):
        res = _gerar(mods=('Run',))
        self.assertEqual(list(res['modalidades'].keys()), ['Run'])

    def test_sem_classificacao_automatica(self):
        res = _gerar()
        texto = json.dumps(res, ensure_ascii=False).lower()
        # 'classificacao' e o nome do campo de sequencia (status da sinalizacao), nao uma
        # conclusao de performance; por isso nao entra na lista de palavras proibidas.
        for palavra in ('melhora', 'piora', 'queda de performance', 'perda de performance',
                        'declinio'):
            self.assertNotIn(palavra, texto, palavra)

    def test_padrao_confirmado_sai_da_mediana_e_fica_marcado(self):
        e, c = _cenario()
        inicio, fim = (REF_D - timedelta(days=7 * 29)).isoformat(), (REF_D - timedelta(days=7 * 20)).isoformat()
        res = _gerar(e, c, confirmados={(inicio, fim, 250.0)})
        s = _serie60(res)
        excl = [p for p in s['pontos'] if 'excluido' in p['sinalizacoes']]
        self.assertEqual(len(excl), 10)
        self.assertEqual(s['qualidade']['n_excluidos_tendencia'], 10)

    def test_payload_mantem_chaves_existentes(self):
        res = _gerar()
        for chave in ('referencia', 'janelas', 'regras', 'modalidades', 'status'):
            self.assertIn(chave, res)
        fonte = res['modalidades']['Bike']['fontes']['eftp_atividade']
        for chave in ('origem', 'rotulo', 'semantica', 'qualidade', 'janelas'):
            self.assertIn(chave, fonte)
        j = fonte['janelas']['180']
        for chave in ('status', 'motivos', 'mediana_anterior', 'mediana_recente', 'diferenca',
                      'variacao_pct', 'n_anterior', 'n_recente', 'semanas_anterior',
                      'semanas_recente', 'semanas_necessarias', 'semanas_total'):
            self.assertIn(chave, j)


class RenderizacaoDeterministica(unittest.TestCase):
    """Executa o JS real da pagina com canvas registrado. Pula sem node."""

    @classmethod
    def setUpClass(cls):
        if shutil.which('node') is None:
            raise unittest.SkipTest('node nao disponivel')
        import tabs.tab_pmc as T
        html = T.render()
        js = '\n'.join(re.findall(r'<script>(.*?)</script>', html, re.S))
        cls.tmp = tempfile.mkdtemp(prefix='evol_vis_')
        cls.pjs = os.path.join(cls.tmp, 'pmc.js')
        with open(cls.pjs, 'w', encoding='utf-8') as f:
            f.write(js)
        e, c = _cenario()
        cls.payload = os.path.join(cls.tmp, 'payload.json')
        with open(cls.payload, 'w', encoding='utf-8') as f:
            json.dump(_gerar(e, c, mods=('Bike',)), f, ensure_ascii=False)
        cls.harness = os.path.join(os.path.dirname(__file__), 'js', 'render_grafico_evol.js')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _rodar(self, mod='Bike', jan='180'):
        r = subprocess.run(['/opt/node22/bin/node' if os.path.exists('/opt/node22/bin/node') else 'node',
                            self.harness, self.pjs, self.payload, mod, jan],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        return json.loads(r.stdout)

    def test_determinismo(self):
        self.assertEqual(self._rodar(), self._rodar())

    def test_um_arco_por_ponto_valido(self):
        out = self._rodar()
        self.assertEqual(_des(out).get('60'), 97)  # 100 - zero - ausente - negativo
        self.assertEqual(_des(out).get('300'), 100)  # 300 s presente em todas as 100 curvas
        self.assertIn('eFTP da atividade (icu_pm_ftp)', out['__html'])

    def test_rotulos_de_ausencia_e_mediana_aparecem(self):
        out = self._rodar(jan='180')
        self.assertIn('Mediana recente:', out['__html'])
        self.assertIn('Mediana anterior:', out['__html'])

    def test_rotulo_de_mediana_so_quando_janela_suficiente(self):
        payload = json.load(open(self.payload, encoding='utf-8'))
        for jan in ('90', '180', '365'):
            with self.subTest(jan=jan):
                ok = payload['modalidades']['Bike']['fontes']['eftp_atividade']['janelas'][jan]['status'] == 'ok'
                html = self._rodar(jan=jan)['__html']
                self.assertEqual('Mediana recente:' in html, ok)
                self.assertEqual('Dados insuficientes para comparar' in html, not ok)
                if ok:
                    w = payload['modalidades']['Bike']['fontes']['eftp_atividade']['janelas'][jan]
                    self.assertIn(f"Mediana recente: <b>{w['mediana_recente']:.1f} W</b>", html)


class TextosEIdsDaInterface(unittest.TestCase):
    """Populacoes explicadas, FTP configurado separado da tendencia, ids presentes."""

    @classmethod
    def setUpClass(cls):
        import tabs.tab_pmc as T
        html = T.render()
        cls.html = html
        cls.js = '\n'.join(re.findall(r'<script>(.*?)</script>', html, re.S))

    def test_populacoes_explicadas(self):
        self.assertIn('Populações distintas', self.js)
        self.assertIn('As contagens podem diferir', self.js)

    def test_ids_das_secoes_visuais_existem(self):
        for i in ('evolPopulacao', 'evolCfgBody', 'evolGraficos', 'evolBody', 'evolMeBody'):
            self.assertIn(f'id="{i}"', self.html, i)

    def test_ftp_configurado_fora_da_tabela_de_tendencia(self):
        self.assertIn("cfgBody.innerHTML=mostraCfg", self.js)
        self.assertIn('Não compõe a tendência: sem Δ', self.js)
        self.assertNotIn("fontes=[['eftp_atividade','eFTP da atividade (icu_pm_ftp)'],['ftp_configurado'", self.js)

    def test_cores_validadas_pelo_validador(self):
        self.assertIn("ponto:'#3a78b5'", self.js)
        self.assertIn("sinal:'#c4691f'", self.js)


if __name__ == '__main__':
    unittest.main()
