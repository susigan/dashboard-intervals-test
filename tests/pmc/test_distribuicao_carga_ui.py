"""UI do painel 'Distribuicao da carga' (Training Load / TRIMP / comparacao): JS real do bloco
<<distribuicao-carga>> executado em node contra RESPOSTAS REAIS do servidor (dist_payload).
Dados sinteticos: testam o codigo, nao o atleta nem a validade fisiologica."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

U = '/api/carga/distribuicao?metodo=%s&janela=%s&dias=%s'
MODS = ['Bike', 'Row', 'Run', 'Ski']


class PainelDistribuicao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import tabs.tab_pmc as tp
        except BaseException as e:
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        cls.js, cls.body = tp.JS, tp.BODY
        import dist_payload
        cls.real = dist_payload.gerar()
        cls.res = None
        if shutil.which('node') is not None:
            with tempfile.TemporaryDirectory() as d:
                a, b = os.path.join(d, 'pmc_js.js'), os.path.join(d, 'real.json')
                with open(a, 'w', encoding='utf-8') as f:
                    f.write(cls.js)
                with open(b, 'w', encoding='utf-8') as f:
                    json.dump(cls.real, f)
                r = subprocess.run(['node', os.path.join(HERE, 'js', 'harness_dist.js'), a, b],
                                   capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise AssertionError(f'harness falhou: {r.stdout[-300:]} {r.stderr[-300:]}')
            cls.res = json.loads(r.stdout)

    def need_node(self):
        if self.res is None:
            self.skipTest('node nao disponivel')

    def bloco(self):
        return self.js[self.js.index('// <<distribuicao-carga>>'):self.js.index('// <</distribuicao-carga>>')]

    # ---- estrutura estatica
    def test_painel_integrado_fechado_por_omissao_e_independente_do_ir(self):
        m = re.search(r'<details[^>]*id="distCargaExp"[^>]*>', self.body)
        self.assertIsNotNone(m)
        self.assertNotIn(' open', m.group(0))
        self.assertIn('ontoggle="abrirDistCarga(this)"', m.group(0))
        self.assertIn('class="expl"', m.group(0))
        self.assertIn('id="distCargaExpCorpo"', self.body)
        self.assertIn('id="irExp"', self.body)                               # o painel IR continua la
        self.assertNotIn('<canvas', self.body.split('id="distCargaExp"')[1].split('</details>')[0])

    def test_nada_e_pedido_ao_carregar_e_so_ha_um_get_de_leitura(self):
        topo = [l for l in self.js.split('\n') if re.match(r'^(await )?(carregarDistCarga|abrirDistCarga|dcShell)\(', l)]
        self.assertEqual(topo, [])
        b = self.bloco()
        self.assertNotIn("method:'POST'", b)
        self.assertNotRegex(b, r'recalibrar|/api/modelo|localStorage|XMLHttpRequest')
        self.assertEqual(re.findall(r"fetch\('([^']+)'", b), ['/api/carga/distribuicao?metodo='])

    def test_reutiliza_helpers_existentes(self):
        b = self.bloco()
        for h in ('ctx(', 'noData(', 'registarTip(', 'linhaTip('):
            self.assertIn(h, b)
        self.assertIn('class="chartbox"', b)
        self.assertIn('class="legend"', b)

    def test_nenhuma_referencia_a_musculacao_nem_a_residuo_na_ui(self):
        b = self.bloco()
        self.assertNotRegex(b, r'(?i)weight|crossfit|workout|outros|outras modalidades|residual')
        # a unica mencao admitida a musculacao e a frase que declara a exclusao
        self.assertEqual([l.strip()[:60] for l in re.findall(r'^(?!\s*//)[^\n]*[Mm]uscula[^\n]*', b, re.M)],
                         ["'efeito no desempenho. Musculacao e restantes tipos nao entram em nenhum "[:60]])
        self.assertEqual(re.search(r"DC_MODS=\[([^\]]*)\]", b).group(1), "'Bike','Row','Run','Ski'")

    def test_texto_nao_afirma_influencia_equilibrio_causalidade_nem_vencedor(self):
        b = self.bloco()
        self.assertIn('Nao mede influencia fisiologica', b)
        self.assertIn('nao define equilibrio ideal', b)
        self.assertNotRegex(b, r'(?i)(melhora|piora|causa|ideal para|recomend|deve(s)? treinar|vencedor|melhor metodo|mais adequad)')

    def test_nao_ha_conversao_entre_metricas_no_codigo_da_ui(self):
        b = self.bloco()
        self.assertNotRegex(b, r'(?i)fator\s*[:=]|\*\s*0\.\d+\s*;.*trimp|trimp\s*[*/]\s*[\d(]|training_load\s*[*/]\s*[\d(]')
        self.assertIn('nenhum fator de conversao', b)

    # ---- contrato com o servidor
    def test_servidor_devolve_as_chaves_que_a_ui_le(self):
        for k in ('tl28_365', 'tr28_365'):
            j = self.real[k]
            for c in ('estado', 'serie', 'metodo', 'rotulo_metodo', 'janela_dias', 'unidade', 'definicao_metodo', 'formula',
                      'semantica_janela', 'exclusoes', 'aviso', 'cores', 'modalidades', 'fonte_da_carga_pct', 'cobertura',
                      'parametros_trimp', 'comparacao'):
                self.assertIn(c, j, (k, c))
            S = j['serie']
            n = len(S['datas'])
            for c in ('total', 'estado', 'n_ausentes_janela', 'n_elegiveis_janela', 'n_validas_janela', 'dif_max_pp_entre_metodos'):
                self.assertEqual(len(S[c]), n, c)
            for m in MODS:
                self.assertEqual(len(S['pct'][m]), n)
                self.assertEqual(len(S['carga'][m]), n)
            self.assertEqual(set(S['pct']), set(MODS))
            C = j['comparacao']
            for c in ('datas', 'pct', 'carga_absoluta', 'total_absoluto', 'estado', 'n_elegiveis_janela', 'n_validas_janela',
                      'n_validas_ambos_janela', 'dif_pp', 'dif_max_pp', 'controlo_mesmas_atividades', 'cobertura_ultima_janela',
                      'cobertura_periodo', 'aviso'):
                self.assertIn(c, C, c)
            self.assertEqual(len(C['datas']), n)
            for m in MODS:
                self.assertEqual(len(C['dif_pp'][m]), n)

    def test_payload_real_sem_musculacao_apesar_de_dados_com_musculacao_enorme(self):
        for a, b in (('tl28_365', 'tl28_365_sem_musculacao_nos_dados'), ('tr28_365', 'tr28_365_sem_musculacao_nos_dados')):
            x, y = self.real[a], self.real[b]
            self.assertEqual(x['serie'], y['serie'])
            self.assertEqual(x['comparacao'], y['comparacao'])
            self.assertEqual(x['cobertura'], y['cobertura'])
            self.assertNotRegex(json.dumps(x), r'(?i)weight|crossfit|workout')

    def test_o_metodo_escolhido_muda_a_serie_e_a_comparacao_e_a_mesma(self):
        a, b = self.real['tl28_365'], self.real['tr28_365']
        self.assertEqual(a['serie']['datas'], b['serie']['datas'])
        self.assertNotEqual(a['serie']['pct'], b['serie']['pct'])
        self.assertEqual(a['comparacao'], b['comparacao'])
        self.assertEqual(a['comparacao']['pct']['training_load'], a['serie']['pct'])
        self.assertEqual(a['comparacao']['pct']['trimp'], b['serie']['pct'])

    # ---- comportamento
    def test_nao_pede_nada_ao_definir_nem_fechado(self):
        self.need_node()
        self.assertEqual(self.res['definir_nao_pede_nada']['chamadas'], 0)
        self.assertEqual(self.res['fechado_nao_pede']['chamadas'], 0)

    def test_abrir_pede_um_get_com_os_defaults_training_load_e_nao_repete(self):
        self.need_node()
        self.assertEqual(self.res['abrir_pede_um_get_uma_vez']['chamadas'], [U % ('training_load', 28, 365)])

    def test_render_training_load_mostra_janela_unidade_percentagens_e_soma_100(self):
        self.need_node()
        r, j = self.res['render_training_load'], self.real['tl28_365']
        self.assertIn('Training Load', r['info'])
        self.assertIn('28 dias', r['info'])
        self.assertIn('icu_training_load', r['info'])
        k = len(j['serie']['datas']) - 1
        for m in MODS:
            self.assertIn('%.1f%%' % j['serie']['pct'][m][k], r['info'])
        self.assertIn('<b>100%</b>', r['info'])
        self.assertAlmostEqual(sum(j['serie']['pct'][m][k] for m in MODS), 100.0, places=2)
        self.assertEqual(r['semData'], [])
        self.assertIn('Fonte da carga no periodo', r['notas'])

    def test_render_trimp_usa_a_serie_trimp_e_declara_a_definicao_e_a_fc_max(self):
        self.need_node()
        r, j = self.res['render_trimp'], self.real['tr28_365']
        self.assertEqual(r['J'], 'trimp')
        self.assertIn('TRIMP', r['info'])
        k = len(j['serie']['datas']) - 1
        for m in MODS:
            self.assertIn('%.1f%%' % j['serie']['pct'][m][k], r['info'])
        self.assertIn('Banister', r['notas'])
        self.assertIn('FC MEDIA', r['notas'])
        for fc in ('190 bpm', '188 bpm', '192 bpm', '182 bpm'):
            self.assertIn(fc, r['notas'])                                    # FC max por modalidade visivel
        self.assertNotIn('Fonte da carga no periodo', r['notas'])            # a composicao por fonte e do Training Load
        self.assertIn('Nao foi aplicado nenhum fator de conversao', r['notas'])

    def test_trocar_metodo_janela_e_periodo_pede_o_get_certo_e_mantem_o_metodo(self):
        self.need_node()
        r = self.res['trocar_metodo_janela_e_periodo']
        self.assertEqual(r['a'], [U % ('training_load', 28, 365), U % ('trimp', 28, 365)])
        self.assertEqual(r['b'][-1], U % ('trimp', 7, 365))
        self.assertEqual(r['chamadas'][-1], U % ('trimp', 28, 90))

    def test_legenda_so_tem_as_quatro_modalidades_e_os_estados(self):
        self.need_node()
        leg = self.res['render_training_load']['leg']
        for m in MODS:
            self.assertIn(m, leg)
            self.assertIn(self.real['tl28_365']['cores'][m], leg)
        self.assertIn('sem treino na janela', leg)
        self.assertIn('carga em falta', leg)
        self.assertNotRegex(leg, r'(?i)weight|muscula|outros')
        self.assertIn('sem TRIMP valido', self.res['estados_trimp_sem_treino_e_sem_trimp_valido']['leg'])

    def test_barras_cores_so_das_quatro_modalidades_e_100_por_cento_da_altura(self):
        self.need_node()
        for r, j in ((self.res['render_training_load'], self.real['tl28_365']), (self.res['render_trimp'], self.real['tr28_365'])):
            cores = set(j['cores'].values())
            S = j['serie']
            por_x = {}
            for cor, x, y, w, h, alfa in r['rects']:
                if cor in cores:
                    por_x[round(x, 3)] = por_x.get(round(x, 3), 0) + h
            self.assertEqual(len(por_x), sum(1 for e in S['estado'] if e.startswith('ok')))
            for h in por_x.values():
                self.assertAlmostEqual(h, 216.0, places=3)
            self.assertTrue({c for c, *_ in r['rects']} <= cores | {'#d29922', '#484f58'})

    def test_tooltip_mostra_percentagem_carga_absoluta_e_cobertura(self):
        self.need_node()
        r, j = self.res['render_training_load'], self.real['tl28_365']
        k = len(j['serie']['datas']) - 1
        for m in MODS:
            self.assertIn('%.1f%%' % j['serie']['pct'][m][k], r['tipUltimo'])
            self.assertIn('%.0f' % j['serie']['carga'][m][k], r['tipUltimo'])
        self.assertIn('Total da janela', r['tipUltimo'])
        self.assertIn('Training Load', r['tipUltimo'])
        self.assertIn('Atividades validas/elegiveis', r['tipUltimo'])
        self.assertEqual(r['tipFora'], '')

    def test_cobertura_incompleta_do_trimp_e_avisada_e_a_do_training_load_nao(self):
        self.need_node()
        r = self.res['cobertura_incompleta_aviso']
        j = self.real['tr28_365']
        t = j['cobertura']['total']
        self.assertLess(t['validas'], t['elegiveis'])                         # o cenario tem atividades sem TRIMP valido
        self.assertIn('Cobertura incompleta', r['tr'])
        self.assertIn('representa apenas as %d de %d atividades com TRIMP valido' % (t['validas'], t['elegiveis']), r['tr'])
        self.assertIn('Nao e a distribuicao completa da carga real', r['tr'])
        self.assertIn('excluidas por falta de dados', r['tr'])
        self.assertIn('FC marcada para ignorar', r['tr'])
        self.assertNotIn('Cobertura incompleta', r['tl'])

    def test_comparacao_mostra_pct_dos_dois_metodos_diferenca_absolutos_e_cobertura(self):
        self.need_node()
        c, j = self.res['render_training_load']['comp'], self.real['tl28_365']['comparacao']
        k = len(j['datas']) - 1
        for m in MODS:
            self.assertIn('%.1f%%' % j['pct']['training_load'][m][k], c)
            self.assertIn('%.1f%%' % j['pct']['trimp'][m][k], c)
            self.assertIn(('+' if j['dif_pp'][m][k] > 0 else '') + '%.1f' % j['dif_pp'][m][k], c)
            self.assertIn('%.0f' % j['carga_absoluta']['training_load'][m][k], c)
            self.assertIn('%.0f' % j['carga_absoluta']['trimp'][m][k], c)
        self.assertIn('Training Load (unid. icu)', c)
        self.assertIn('TRIMP (unid. TRIMP)', c)
        self.assertIn('TRIMP menos Training Load', c)
        self.assertIn('Cobertura na janela', c)
        self.assertIn('com os dois valores validos', c)
        self.assertIn('mesmas atividades', c)
        self.assertIn('nao sao diretamente comparaveis', c)
        self.assertNotRegex(c, r'(?i)vencedor|melhor metodo|mais adequad')

    def test_comparacao_e_a_mesma_nos_dois_metodos_e_o_grafico_de_diferencas_e_desenhado(self):
        self.need_node()
        a, b = self.res['render_training_load'], self.res['render_trimp']
        self.assertEqual(a['comp'], b['comp'])
        self.assertGreater(a['dif']['linhas'], 100)
        self.assertEqual(set(a['dif']['cores']) & set(self.real['tl28_365']['cores'].values()), set(self.real['tl28_365']['cores'].values()))
        self.assertIn('TRIMP &minus; Training Load', a['legDif'])
        for m in MODS:
            self.assertIn(m, a['legDif'])
        j = self.real['tl28_365']['comparacao']
        k = len(j['datas']) - 1
        for m in MODS:
            self.assertIn(('+' if j['dif_pp'][m][k] > 0 else '') + '%.1f pp' % j['dif_pp'][m][k], a['tipDif'])
        self.assertIn('Validas TL / TRIMP / ambos', a['tipDif'])

    def test_estados_sem_treino_e_sem_trimp_valido_ficam_distintos_do_zero(self):
        self.need_node()
        r = self.res['estados_trimp_sem_treino_e_sem_trimp_valido']
        self.assertTrue({'sem_treino', 'carga_ausente', 'ok_com_ausentes'} <= set(r['estados']))
        self.assertIn('Sem treino na janela', r['tipSem'])
        self.assertIn('#484f58', r['tipSem'])
        self.assertIn('Sem TRIMP valido', r['tipAus'])
        self.assertIn('#d29922', r['tipAus'])
        self.assertNotIn('%', r['tipSem'] + r['tipAus'])
        self.assertIn('fora da soma', r['tipOkAus'])
        cores = {c for c, *_ in r['rects']}
        self.assertIn('#484f58', cores)
        self.assertIn('#d29922', cores)

    def test_janela_incompleta_e_vazia_sao_ditas_sem_grafico_nem_comparacao_inventados(self):
        self.need_node()
        r = self.res['janela_incompleta']
        self.assertIn('nao cobrem uma janela completa', r['info'])
        self.assertEqual((r['notas'], r['comp']), ('', ''))
        self.assertEqual(r['rects'], 0)
        self.assertTrue(r['semData'])
        v = self.res['vazia']
        self.assertIn('nao ha atividades', v['info'])
        self.assertEqual(v['comp'], '')

    def test_falha_de_rede_nao_quebra(self):
        self.need_node()
        r = self.res['falha_de_rede']
        self.assertIn('Nao foi possivel calcular', r['info'])
        self.assertEqual(r['comp'], '')
        self.assertTrue(r['semData'])

    def test_resposta_antiga_nao_sobrepoe_a_nova(self):
        self.need_node()
        self.assertEqual(self.res['resposta_antiga_nao_sobrepoe_a_nova']['J'], 'trimp')

    def test_series_pura(self):
        self.need_node()
        s = self.res['series']
        self.assertEqual(s['s']['mods'], MODS)
        self.assertEqual(s['t']['mods'], MODS)
        self.assertNotEqual(s['s']['pct'], s['t']['pct'])
        self.assertIsNone(s['vazio'])
        self.assertIsNone(s['nulo'])


if __name__ == '__main__':
    unittest.main()
