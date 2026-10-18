"""UI das curvas de influencia do modelo IR: o JS real, com DOM/canvas/fetch simulados (node).

As respostas do servidor sao as REAIS (tests/pmc/ir_payload.py), nao escritas a mao. Offline.

    python -m pytest tests/pmc/test_modelo_ir_ui.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
HERE = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

U = '/api/modelo/ir/influencia?tipo=%s&duracao=%s'


class UiCurvasIR(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import tabs.tab_pmc as tp
        except BaseException as e:
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        cls.js, cls.body = tp.JS, tp.BODY
        import ir_payload
        cls.real = ir_payload.gerar()
        cls.res = None
        if shutil.which('node') is not None:
            with tempfile.TemporaryDirectory() as d:
                a, b = os.path.join(d, 'pmc_js.js'), os.path.join(d, 'real.json')
                with open(a, 'w', encoding='utf-8') as f:
                    f.write(cls.js)
                with open(b, 'w', encoding='utf-8') as f:
                    json.dump(cls.real, f)
                r = subprocess.run(['node', os.path.join(HERE, 'js', 'harness_ir.js'), a, b],
                                   capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise AssertionError(f'harness falhou: {r.stdout[-300:]} {r.stderr[-300:]}')
            cls.res = json.loads(r.stdout)

    def need_node(self):
        if self.res is None:
            self.skipTest('node nao disponivel')

    def bloco(self):
        return self.js[self.js.index('// <<modelo-ir>>'):self.js.index('// <</modelo-ir>>')]

    # ---- estrutura estatica ------------------------------------------------
    def test_painel_integrado_na_pagina_pmc_fechado_por_omissao(self):
        m = re.search(r'<details[^>]*id="irExp"[^>]*>', self.body)
        self.assertIsNotNone(m)
        self.assertNotIn(' open', m.group(0))
        self.assertIn('ontoggle="abrirIR(this)"', m.group(0))
        self.assertIn('class="expl"', m.group(0))                         # o componente existente
        self.assertIn('id="irExpCorpo"', self.body)

    def test_nada_e_pedido_ao_carregar_a_pagina_e_so_ha_leitura(self):
        topo = [l for l in self.js.split('\n') if re.match(r'^(await )?(carregarIR|abrirIR|irShell)\(', l)]
        self.assertEqual(topo, [])
        b = self.bloco()
        self.assertNotIn("method:'POST'", b)
        self.assertNotIn('recalibrar', b)
        self.assertEqual(re.findall(r"fetch\('([^']+)'", b), ['/api/modelo/ir/influencia?tipo='])

    def test_reutiliza_helpers_e_componentes_existentes_sem_pagina_paralela(self):
        b = self.bloco()
        for h in ("ctx(canvasId,240)", 'noData(', 'registarTip(', 'linhaTip('):
            self.assertIn(h, b)
        self.assertIn('class="chartbox"', b)
        self.assertIn('class="legend"', b)
        self.assertNotIn('<canvas', self.body.split('id="irExp"')[1].split('</details>')[0])   # canvases so no shell JS

    def test_texto_nao_afirma_validacao_nem_fisiologia_confirmada(self):
        b = self.bloco()
        self.assertIn('nao validado preditivamente', b)
        sem_negacao = b.replace('nao confirmado', '')       # unica forma admitida: a negacao
        self.assertNotRegex(sem_negacao, r'(?i)\b(confirmad[oa]s?|comprovad[oa]s?)\b')
        self.assertNotRegex(b, r'(?i)constantes fisiologicas')

    # ---- contrato com o servidor ------------------------------------------
    def test_o_servidor_devolve_as_chaves_que_a_ui_le(self):
        j = self.real['bike1200_ativ']
        for k in ('estado', 'parametros', 'parametros_identificados', 'aviso_parametros', 'leitura', 'aviso', 'n_obs',
                  'data_ini', 'data_fim', 'funcao_objetivo', 'rmse', 'r2', 'convergiu', 'versao_ajuste', 'ajustado_em',
                  'motivo_ajuste', 'p0_origem', 'avisos_identificabilidade', 'ajuste_desatualizado',
                  'tratamento_da_carga', 'verificacao_aditividade', 'curvas', 'observacoes', 'atividades',
                  'contribuicao_atividade'):
            self.assertIn(k, j, k)
        for k in ('datas', 'positiva', 'negativa', 'liquida', 'carga_dia', 'fora_do_ajuste_desde'):
            self.assertIn(k, j['curvas'], k)
        n = len(j['curvas']['datas'])
        for k in ('positiva', 'negativa', 'liquida', 'carga_dia'):
            self.assertEqual(len(j['curvas'][k]), n)
        for k in ('positiva', 'negativa', 'liquida'):
            self.assertEqual(len(j['contribuicao_atividade'][k]), n)

    # ---- comportamento (node) ----------------------------------------------
    def test_nao_pede_nada_ao_definir_nem_com_o_painel_fechado(self):
        self.need_node()
        self.assertEqual(self.res['definir_nao_pede_nada']['chamadas'], 0)
        self.assertEqual(self.res['fechado_nao_pede']['chamadas'], 0)

    def test_abrir_pede_um_unico_get_com_os_defaults_e_nao_repete(self):
        self.need_node()
        self.assertEqual(self.res['abrir_pede_um_get_uma_vez']['chamadas'], [U % ('Bike', 1200)])

    def test_mostra_parametros_dados_do_ajuste_erro_convergencia_e_avisos(self):
        self.need_node()
        r, j = self.res['render_completo'], self.real['bike1200']
        info = r['info']
        for k, nd in (('p0', 1), ('t1', 1), ('t2', 1)):
            self.assertIn(f'{j["parametros"][k]:.{nd}f}', info)
        self.assertIn(f'{j["parametros"]["k1"]:.4f}', info)
        self.assertIn(f'<b>{j["n_obs"]}</b> observacoes', info)
        self.assertIn(j['data_ini'], info)
        self.assertIn(j['data_fim'], info)
        self.assertIn('Erro dentro da amostra', info)
        self.assertIn('Convergencia: sim', info)
        self.assertIn(f'Ajuste v{j["versao_ajuste"]}', info)
        self.assertIn('o esforco nao explica o proprio valor', info)
        self.assertIn('Dias sem treino: carga 0', info)
        self.assertIn(f'Soma das contribuicoes de {j["verificacao_aditividade"]["n_atividades"]} atividades', info)
        self.assertIn('Nao e previsao validada', info)

    def test_desenha_os_dois_graficos_com_componentes_liquida_e_observacoes(self):
        self.need_node()
        r, j = self.res['render_completo'], self.real['bike1200']
        for leg in ('Positiva', 'Negativa', 'Resposta liquida'):
            self.assertIn(leg, r['leg1'])
        self.assertIn('Melhor valor da duracao (observado)', r['leg2'])
        a, b = r['canvases']['chIrComp'], r['canvases']['chIrLiq']
        self.assertEqual((a['stroke'], b['stroke']), (9, 6))                 # 5 grelha + 1 zero + 3 series ; 5 + 1
        self.assertGreater(a['lineTo'], 3 * (len(j['curvas']['datas']) - 2))
        self.assertEqual(b['arc'], len(j['observacoes']))                    # um ponto por observacao usada
        self.assertIn('fora do ajuste \u25b8', a['texts'])
        self.assertEqual(r['semData'], [])
        self.assertIn('Carga do dia', r['tip'])
        self.assertEqual(r['tipFora'], '')

    def test_seletores_pedem_a_combinacao_certa_e_nao_misturam(self):
        self.need_node()
        self.assertEqual(self.res['trocar_modalidade_e_duracao']['a'],
                         [U % ('Bike', 1200), U % ('Bike', 300)])
        self.assertEqual(self.res['trocar_modalidade_e_duracao']['chamadas'][-1], U % ('Row', 300))
        self.assertIn('ainda nao ha ajuste', self.res['trocar_modalidade_e_duracao']['infoRow'])

    def test_escolher_atividade_pede_a_contribuicao_e_mostra_a_curva_da_atividade(self):
        self.need_node()
        r = self.res['escolher_atividade']
        self.assertEqual(r['chamadas'][-1], U % ('Bike', 1200) + '&atividade=' + self.real['ativ_id'])
        self.assertIn('Atividade ', r['leg'])
        self.assertEqual(r['ativ'], self.real['ativ_id'])
        d = self.real['bike1200_ativ']['contribuicao_atividade']['atividade']['data']
        self.assertIn(d, r['leg'])

    def test_mudar_modalidade_ou_duracao_limpa_a_atividade(self):
        self.need_node()
        r = self.res['mudar_modalidade_limpa_atividade']
        self.assertEqual(r['ativ'], '')
        self.assertNotIn('atividade=', r['chamadas'][-1])

    def test_sem_ajuste_nao_desenha_curva_e_diz_que_nada_foi_calculado(self):
        self.need_node()
        r = self.res['sem_ajuste']
        self.assertIn('nada foi calculado agora', r['info'])
        self.assertEqual(r['comLinhas'], 0)
        self.assertTrue(r['semData'])

    def test_nao_identificavel_vem_marcado_como_nao_confirmado(self):
        self.need_node()
        info = self.res['nao_identificavel']['info']
        self.assertIn('Atencao', info)
        self.assertIn('NAO identificaveis', info)
        self.assertEqual(info.count('(nao confirmado)'), 5)               # os 5 parametros
        self.assertIn('parametro_no_limite:t1', info)
        self.assertIn('#d29922', info)

    def test_identificavel_nao_traz_marca_de_nao_confirmado(self):
        self.need_node()
        self.assertNotIn('(nao confirmado)', self.res['render_completo']['info'])

    def test_dados_insuficientes_nao_mostram_parametros_nem_curva(self):
        self.need_node()
        r = self.res['insuficiente']
        self.assertNotIn('<table', r['info'].split('Ajuste v')[0])
        self.assertIn('sem parametros utilizaveis', r['info'])
        self.assertEqual(r['linhas'], 0)
        self.assertTrue(r['semData'])

    def test_cada_estado_sem_parametros_mostra_o_seu_motivo(self):
        # auditoria: 'insuficiente' e 'nao convergiu' mostravam a mesma frase generica e o
        # utilizador nao sabia qual era o caso
        self.need_node()
        ins, sem = self.res['insuficiente']['info'], self.res['sem_convergencia']['info']
        self.assertIn('dados insuficientes', ins)
        self.assertNotIn('nao convergiu', ins)
        self.assertIn('nao convergiu', sem)
        self.assertNotIn('dados insuficientes', sem)
        for r in (self.res['sem_convergencia'],):
            self.assertNotIn('<table', r['info'].split('Ajuste v')[0])
            self.assertEqual(r['linhas'], 0)
            self.assertTrue(r['semData'])

    def test_series_desenhadas_sao_as_do_servidor_com_o_sinal_certo(self):
        self.need_node()
        r, j = self.res['series'], self.real['bike1200_ativ']
        pos, neg, liq, ativ = r['s']['comp']
        cu = j['curvas']
        self.assertEqual(pos['vals'], cu['positiva'])
        self.assertTrue(all(v <= 0 for v in neg['vals']))                         # negativa desenhada abaixo de zero
        self.assertEqual([-v for v in neg['vals']], cu['negativa'])
        for a, b, c in zip(pos['vals'], neg['vals'], liq['vals']):                 # liquida (sem P0) = positiva + negativa
            self.assertAlmostEqual(a + b, c, delta=2e-3)
        self.assertEqual(ativ['vals'], j['contribuicao_atividade']['liquida'])
        self.assertTrue(ativ['tracejado'])
        self.assertEqual(r['s']['liq'][0]['vals'], cu['liquida'])                 # absoluta (inclui P0)
        self.assertAlmostEqual(r['s']['liq'][0]['vals'][0] - r['P0'], liq['vals'][0], delta=2e-3)
        self.assertEqual(len(r['s']['pts']), j['n_obs'])
        for p in r['s']['pts']:
            self.assertEqual(p['y'], next(o['watts'] for o in j['observacoes'] if o['data'] == cu['datas'][p['i']]))

    def test_sem_curva_nao_ha_series(self):
        self.need_node()
        self.assertIsNone(self.res['series']['vazio'])
        self.assertIsNone(self.res['series']['sem'])

    def test_falha_de_rede_mostra_mensagem(self):
        self.need_node()
        self.assertIn('Nao foi possivel calcular', self.res['falha_de_rede']['info'])

    def test_resposta_antiga_nao_sobrepoe_a_selecao_nova(self):
        self.need_node()
        r = self.res['resposta_antiga_nao_sobrepoe_a_nova']
        self.assertEqual(r['J'], 300)
        self.assertTrue(r['info'])

    def test_texto_da_base_de_dados_e_escapado(self):
        self.need_node()
        r = self.res['xss_escapado']
        for h in (r['opcoes'], r['info'], r['leg']):
            self.assertNotIn('<img', h)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', r['opcoes'])


if __name__ == '__main__':
    unittest.main()
