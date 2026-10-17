"""Sugestoes de teste da PMC (protocolo.py + /api/protocolo + cartoes em tab_pmc).

Fase A: fixa o comportamento ja existente (3 duracoes, intervalos, limiar 92 %,
janela 180 d, sem vazamento de futuro, ausente != 0) e a correcao da duplicacao
da interface. Sem rede e sem base real: as curvas sao sinteticas.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import protocolo as P  # noqa: E402

HOJE = datetime(2026, 10, 16)


class _Relogio(datetime):
    @classmethod
    def now(cls, tz=None):
        return HOJE


def _data(dias_atras):
    return (HOJE - timedelta(days=dias_atras)).strftime('%Y-%m-%d')


def _curva(aid, tipo, dias_atras, w60=None, w300=None, w1200=None):
    secs, watts = [], []
    for s, w in ((60, w60), (300, w300), (1200, w1200)):
        secs.append(s)
        watts.append(w)          # pode ser None: ausente
    return {'activity_id': aid, 'type': tipo, 'date': _data(dias_atras),
            'secs': secs, 'watts': watts}


def _serie(tipo, secs_idx, valores_dias, prefixo='a'):
    """valores_dias: [(dias_atras, watts)] para uma unica duracao."""
    out = []
    for i, (d, w) in enumerate(valores_dias):
        kw = {'w60': None, 'w300': None, 'w1200': None}
        kw[secs_idx] = w
        out.append(_curva(f'{prefixo}{tipo}{i}', tipo, d, **kw))
    return out


# 6 sessoes de 20 min, a mais recente ha 'rec' dias, todas a 300 W (= 100 %)
def _base1200(tipo='Bike', rec=40, w=300):
    return _serie(tipo, 'w1200', [(rec + 10 * k, w) for k in range(5, -1, -1)])


class ConfiguracaoPreservada(unittest.TestCase):
    def test_tres_duracoes_e_nomes(self):
        self.assertEqual(set(P.TESTES), {60, 300, 1200})
        self.assertEqual(P.TESTES[60]['nome'], '1 min max')
        self.assertEqual(P.TESTES[300]['nome'], '5 min max')
        self.assertEqual(P.TESTES[1200]['nome'], '20 min max')

    def test_intervalos_atuais(self):
        self.assertEqual(P.TESTES[60]['intervalo_dias'], 28)
        self.assertEqual(P.TESTES[300]['intervalo_dias'], 21)
        self.assertEqual(P.TESTES[1200]['intervalo_dias'], 28)

    def test_limiar_e_janela_atuais(self):
        self.assertEqual(P.LIMIAR_ESFORCO, 0.92)
        self.assertEqual(P.JANELA_MELHOR, 180)


class Deteccao(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(P, 'datetime', _Relogio)
        p.start()
        self.addCleanup(p.stop)

    def test_esforco_valido_reconhecido_e_modalidade_preservada(self):
        det = P.detectar_testes(_base1200('Row', rec=40, w=300))
        self.assertEqual(list(det), ['Row'])
        d = det['Row'][1200]
        self.assertEqual(d['nome'], '20 min max')
        self.assertEqual(d['ultimo']['date'], _data(40))
        self.assertEqual(d['dias_desde_ultimo'], 40)
        self.assertTrue(d['em_atraso'])             # 40 d > 28 d

    def test_modalidades_nao_se_misturam(self):
        cs = _base1200('Bike', 40, 300) + _base1200('Run', 10, 200)
        det = P.detectar_testes(cs)
        self.assertEqual(det['Bike'][1200]['ultimo']['watts'], 300.0)
        self.assertEqual(det['Run'][1200]['ultimo']['watts'], 200.0)

    def test_limiar_92_por_cento_do_melhor_na_janela(self):
        base = _base1200('Bike', 40, 300)
        for w, esperado in ((276, True), (275, False)):   # 92 % de 300 = 276 (>= inclusivo)
            cs = base + _serie('Bike', 'w1200', [(2, w)], 'x')
            d = P.detectar_testes(cs).get('Bike', {}).get(1200)
            ultimo = d['ultimo']['date']
            self.assertEqual(ultimo == _data(2), esperado, w)

    def test_melhor_so_conta_dentro_de_180_dias(self):
        # um pico de 400 W ha 200 d sai da janela; 300 W recentes sao 100 %
        cs = (_serie('Bike', 'w1200', [(200, 400)], 'p')
              + _serie('Bike', 'w1200', [(60, 300), (50, 300), (40, 300),
                                         (30, 300), (2, 300)]))
        d = P.detectar_testes(cs)['Bike'][1200]
        self.assertEqual(d['ultimo']['date'], _data(2))
        self.assertEqual(d['ultimo']['pct_do_melhor'], 100.0)

    def test_sugestao_atualiza_depois_de_resultado_valido(self):
        antes = P.sugerir(P.detectar_testes(_base1200('Bike', 40, 300)))
        self.assertEqual([(s['modalidade'], s['secs']) for s in antes],
                         [('Bike', 1200)])
        depois = P.sugerir(P.detectar_testes(
            _base1200('Bike', 40, 300) + _serie('Bike', 'w1200', [(1, 305)], 'n')))
        self.assertEqual(depois, [])

    def test_esforco_fraco_nao_apaga_a_sugestao(self):
        cs = _base1200('Bike', 40, 300) + _serie('Bike', 'w1200', [(1, 200)], 'f')
        sug = P.sugerir(P.detectar_testes(cs))
        self.assertEqual(len(sug), 1)
        self.assertEqual(sug[0]['ultima_data'], _data(40))

    def test_ausente_nao_vira_zero(self):
        cs = _base1200('Bike', 40, 300)
        # curvas sem a duracao de 1200 s, ou com None/NaN/0: nao contam
        cs += [_curva('n1', 'Bike', 1, w1200=None),
               _curva('n2', 'Bike', 1, w1200=float('nan')),
               _curva('n3', 'Bike', 1, w1200=0)]
        d = P.detectar_testes(cs)['Bike'][1200]
        self.assertEqual(d['ultimo']['date'], _data(40))
        self.assertEqual(d['n_testes'], 4)   # nenhum ponto novo; sem zeros a baixar o "melhor"

    def test_menos_de_5_pontos_nao_gera_deteccao(self):
        cs = _serie('Bike', 'w1200', [(30, 300), (20, 300), (10, 300), (1, 300)])
        self.assertEqual(P.detectar_testes(cs), {})

    def test_dados_posteriores_nao_alteram_deteccao_anterior(self):
        antigo = _base1200('Bike', 60, 300)
        futuro = _serie('Bike', 'w1200', [(5, 900), (3, 950)], 'fut')  # picos recentes
        a = P.detectar_testes(antigo)['Bike'][1200]['testes']
        b = P.detectar_testes(antigo + futuro)['Bike'][1200]['testes']
        datas_antigas = {t['date'] for t in a}
        self.assertEqual([t for t in b if t['date'] in datas_antigas], a)

    def test_serie_ancora_preserva_modalidade_e_duracao(self):
        det = P.detectar_testes(_base1200('Row', 40, 300) + _base1200('Bike', 30, 280))
        s = P.serie_ancora(det, 'Row', 1200)
        self.assertTrue(s and all(x['modalidade'] == 'Row' for x in s))
        self.assertEqual(P.serie_ancora(det, 'Run', 1200), [])

    def test_cobertura_e_robustez_contrato(self):
        det = P.detectar_testes(_base1200('Bike', 40, 300))
        cob = P.cobertura(det)
        self.assertEqual(set(cob), {'total', 'por_modalidade', 'veredicto'})
        self.assertIn('20 min max', cob['por_modalidade']['Bike'])
        self.assertIn('Bike', P.robustez(det))

    def test_sugerir_ordena_por_atraso_e_filtra_modalidade(self):
        cs = _base1200('Bike', 40, 300) + _base1200('Row', 90, 300)
        sug = P.sugerir(P.detectar_testes(cs))
        self.assertEqual([s['modalidade'] for s in sug], ['Row', 'Bike'])
        self.assertEqual(sug[0]['urgencia'], 'alta')      # atraso 62 > 28
        self.assertEqual(sug[1]['urgencia'], 'media')
        so_bike = P.sugerir(P.detectar_testes(cs), modalidades_activas=['Bike'])
        self.assertEqual([s['modalidade'] for s in so_bike], ['Bike'])


class RecomendacaoPorTsbCaracterizacao(unittest.TestCase):
    """A funcao continua sem chamadas na PMC (achado da Fase A); so se fixa o
    comportamento atual, sem a ligar a nada."""

    def test_faixas(self):
        self.assertEqual(P.recomendacao_teste_por_tsb(None)['recomendacao'], 'indeterminado')
        self.assertEqual(P.recomendacao_teste_por_tsb(-15)['recomendacao'], 'aguardar')
        self.assertEqual(P.recomendacao_teste_por_tsb(-5)['recomendacao'], 'moderado')
        self.assertEqual(P.recomendacao_teste_por_tsb(3)['recomendacao'], 'ideal')

    def test_continua_sem_chamadas_fora_do_proprio_modulo(self):
        achados = []
        for raiz, dirs, ficheiros in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'tests')]
            for f in ficheiros:
                if f.endswith(('.py', '.js', '.html')) and f != 'protocolo.py':
                    txt = open(os.path.join(raiz, f), encoding='utf-8', errors='ignore').read()
                    if 'recomendacao_teste_por_tsb' in txt:
                        achados.append(f)
        self.assertEqual(achados, [])


class InterfaceSemDuplicacao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import tabs.tab_pmc as tp
        except BaseException as e:   # config.py faz sys.exit sem chave da API
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        cls.tp = tp

    def test_um_unico_contentor(self):
        self.assertEqual(self.tp.BODY.count('id="sugestoesTeste"'), 1)

    def test_uma_definicao_de_cada_funcao_e_um_unico_pedido(self):
        # estatico: complementa os testes comportamentais abaixo
        js = self.tp.JS
        self.assertEqual(len(re.findall(r'function\s+carregarSugestoes\s*\(', js)), 1)
        self.assertEqual(len(re.findall(r'function\s+mostrarSugestoes\s*\(', js)), 1)
        self.assertEqual(js.count("fetch('/api/protocolo')"), 1)

    def test_chamadas_separam_pedido_e_apresentacao(self):
        js = self.tp.JS
        # pedido: uma unica chamada independente, no fim do script, depois de load()
        pedidos = re.findall(r'(?<![\w.])carregarSugestoes\s*\(\s*\)\s*;', js)
        self.assertEqual(len(pedidos), 1)
        self.assertTrue(js.rstrip().endswith('load();\ncarregarSugestoes();'))
        # apresentacao: load() so redesenha (sem novo pedido); mais a chamada dentro de carregarSugestoes
        ini = js.index('async function load()')
        fim = js.index('\nfunction ', ini)
        self.assertIn('mostrarSugestoes();', js[ini:fim])
        self.assertIn("try{ mostrarSugestoes(); }catch(e){ console.error('sugestoes:',e); }", js[ini:fim])
        # o isolamento e so' da chamada das sugestoes: load() nao fica dentro de um try geral
        self.assertEqual(js[ini:fim].count('try{'), 2)   # o fetch de /api/pmc (ja existia) + este
        self.assertNotIn('carregarSugestoes', js[ini:fim])

    def test_rotulo_nao_afirma_teste_formal(self):
        self.assertNotIn("' feitos'", self.tp.JS)
        self.assertIn('esforcos detetados', self.tp.JS)


class ComportamentoDoCarregamento(unittest.TestCase):
    """Executa o JS real da pagina PMC com node, DOM e fetch simulados (sem rede).
    Ignorado se node nao existir. Cenarios da revisao final da Fase A."""

    @classmethod
    def setUpClass(cls):
        if shutil.which('node') is None:
            raise unittest.SkipTest('node nao disponivel')
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import tabs.tab_pmc as tp
        except BaseException as e:
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        harness = os.path.join(os.path.dirname(__file__), 'js', 'harness_sugestoes.js')
        with tempfile.TemporaryDirectory() as tmp:
            caminho = os.path.join(tmp, 'pmc_js.js')
            with open(caminho, 'w', encoding='utf-8') as f:
                f.write(tp.JS)
            r = subprocess.run(['node', harness, caminho], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise AssertionError(r.stderr or r.stdout)
        cls.R = json.loads(r.stdout)

    def _final(self, nome):
        return self.R[nome]['instantaneos'][-1]

    def test_sem_excecoes_nao_tratadas(self):
        for nome, v in self.R.items():
            if nome == 'protocolo_malformado_pmc_lento':
                continue      # tem o seu proprio teste (excecao deliberada)
            self.assertEqual(v['erros'], [], nome)
            self.assertEqual(v['consoleErros'], [], nome)

    def test_desenhos_da_pmc_so_ocorrem_com_pmc_valida(self):
        self.assertGreaterEqual(self.R['sucesso']['instantaneos'][0]['desenhos'], 1)
        for nome in ('pmc_falha_rede', 'pmc_erro'):
            self.assertEqual(self._final(nome)['desenhos'], 0, nome)
        antes, depois = self.R['pmc_lento']['instantaneos']
        self.assertEqual(antes['desenhos'], 0)
        self.assertGreaterEqual(depois['desenhos'], 1)

    def test_payload_malformado_nao_impede_o_carregamento_da_pmc(self):
        # /api/protocolo responde primeiro com sugestoes="x" (nao e lista) e
        # /api/pmc demora: a apresentacao das sugestoes lanca excecao dentro de load()
        r = self.R['protocolo_malformado_pmc_lento']
        antes, depois = r['instantaneos']
        self.assertEqual((antes['pmc'], antes['prot']), (1, 1))
        self.assertFalse(antes['kpis'])
        # quando /api/pmc responde: KPIs e desenhos da PMC acontecem na mesma
        self.assertTrue(depois['kpis'])
        self.assertIn('2 dias', depois['sub'])
        self.assertGreaterEqual(depois['desenhos'], 1)
        # uma so consulta a /api/protocolo; nenhum cartao (nem duplicado)
        self.assertEqual(depois['prot'], 1)
        self.assertEqual((depois['sug'], depois['n_cartoes']), ('vazio', 0))
        # a falha do painel nao e engolida em silencio: fica registada na consola
        self.assertEqual(len(r['consoleErros']), 1)
        self.assertIn('sugestoes:', r['consoleErros'][0])
        self.assertIn('TypeError', r['consoleErros'][0])
        # unico erro nao tratado: a rejeicao assincrona de carregarSugestoes (comportamento
        # pre-existente na base, fora do ambito desta correcao)
        self.assertEqual(len(r['erros']), 1)
        self.assertIn('TypeError', r['erros'][0])

    def test_sucesso_um_pedido_a_cada_endpoint_e_cartao_colorido(self):
        i = self.R['sucesso']['instantaneos'][0]
        self.assertEqual((i['pmc'], i['prot']), (1, 1))
        self.assertTrue(i['kpis'])
        self.assertEqual((i['sug'], i['cor'], i['n_cartoes']), ('cartoes', 'D', 1))

    def test_pmc_falha_de_rede_sugestoes_continuam_visiveis(self):
        i = self._final('pmc_falha_rede')
        self.assertFalse(i['kpis'])
        self.assertIn('Nao consegui carregar', i['sub'])
        self.assertEqual((i['sug'], i['n_cartoes']), ('cartoes', 1))
        self.assertEqual(i['prot'], 1)

    def test_pmc_com_error_sugestoes_continuam_visiveis(self):
        i = self._final('pmc_erro')
        self.assertFalse(i['kpis'])
        self.assertIn('sem actividades', i['sub'])
        self.assertEqual((i['sug'], i['n_cartoes']), ('cartoes', 1))

    def test_pmc_lento_sugestoes_nao_esperam_e_recoloram_depois(self):
        antes, depois = self.R['pmc_lento']['instantaneos']
        self.assertFalse(antes['kpis'])
        self.assertEqual((antes['sug'], antes['cor']), ('cartoes', 'cinza'))
        self.assertTrue(depois['kpis'])
        self.assertEqual((depois['sug'], depois['cor'], depois['n_cartoes']), ('cartoes', 'D', 1))
        self.assertEqual(depois['prot'], 1)

    def test_protocolo_falha_ou_json_invalido_pagina_principal_intacta(self):
        # comportamento pre-existente (silencioso); so se documenta
        for nome in ('protocolo_falha', 'protocolo_json_invalido'):
            i = self._final(nome)
            self.assertTrue(i['kpis'], nome)
            self.assertEqual(i['sug'], 'vazio', nome)
            self.assertEqual(i['prot'], 1, nome)

    def test_protocolo_lento_aparece_ao_chegar(self):
        antes, depois = self.R['protocolo_lento']['instantaneos']
        self.assertEqual(antes['sug'], 'vazio')
        self.assertTrue(antes['kpis'])
        self.assertEqual((depois['sug'], depois['cor']), ('cartoes', 'D'))

    def test_um_unico_pedido_a_protocolo_por_carregamento(self):
        for nome, v in self.R.items():
            self.assertEqual(v['instantaneos'][-1]['prot'], 1, nome)

    def test_load_repetido_nao_repete_pedido_nem_duplica_cartoes(self):
        antes, depois = self.R['sucesso']['instantaneos']
        self.assertEqual(depois['pmc'], 2)            # load() refez o seu proprio pedido
        self.assertEqual(depois['prot'], 1)           # mas nao o de /api/protocolo
        self.assertEqual(depois['n_cartoes'], 1)      # sem cartoes duplicados
        self.assertEqual(depois['cor'], 'D')


class RotaProtocolo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}
        try:
            with mock.patch.dict(os.environ, env):
                import app as appmod
        except BaseException as e:
            raise unittest.SkipTest(f'app nao importa: {type(e).__name__}: {e}')
        cls.app = appmod

    def test_contrato_da_resposta(self):
        curvas = _base1200('Bike', 40, 300)
        with mock.patch.object(self.app.db, 'load_power_curves', return_value=curvas), \
                mock.patch.object(P, 'datetime', _Relogio):
            r = self.app.app.test_client().get('/api/protocolo')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j['status'], 'OK')
        self.assertEqual(set(j), {'status', 'cobertura', 'sugestoes', 'robustez',
                                  'detectados', 'como_funciona'})
        self.assertEqual(j['sugestoes'][0]['secs'], 1200)
        self.assertNotIn('testes', j['detectados']['Bike']['1200'])
        self.assertIn('92%', j['como_funciona'])
        self.assertIn('180', j['como_funciona'])

    def test_sem_curvas(self):
        with mock.patch.object(self.app.db, 'load_power_curves', return_value=[]):
            j = self.app.app.test_client().get('/api/protocolo').get_json()
        self.assertEqual(j['sugestoes'], [])
        self.assertEqual(j['detectados'], {})


if __name__ == '__main__':
    unittest.main()
