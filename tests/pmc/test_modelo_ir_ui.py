"""UI das curvas de influencia IR conjuntas: o JS real, com DOM/canvas/fetch simulados (node).

As respostas do servidor sao as REAIS (tests/pmc/ir_payload.py), nao escritas a mao. Offline.
Testes de IMPLEMENTACAO da interface: nao validam o modelo nem a sua capacidade preditiva.

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

U = '/api/modelo/ir/conjunto'
MODS = ['Bike', 'Row', 'Run', 'Ski']


import ir_duracao_legado as _LEG  # noqa: E402


def setUpModule():
    _LEG.iniciar()


def tearDownModule():
    _LEG.parar()


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
        b = self.js[self.js.index('// <<modelo-ir>>'):self.js.index('// <</modelo-ir>>')]
        return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), b)   # \uXXXX == caractere literal

    # ---- estrutura estatica ------------------------------------------------
    def test_painel_integrado_na_pagina_pmc_fechado_por_omissao(self):
        m = re.search(r'<details[^>]*id="irExp"[^>]*>', self.body)
        self.assertIsNotNone(m)
        self.assertNotIn(' open', m.group(0))
        self.assertIn('ontoggle="abrirIR(this)"', m.group(0))
        self.assertIn('class="expl"', m.group(0))
        self.assertIn('id="irExpCorpo"', self.body)

    def test_os_outros_paineis_experimentais_continuam_na_pagina(self):
        for i in ('registoTestes', 'estimadorExp', 'validacaoExp', 'distCargaExp'):
            self.assertIn(f'id="{i}"', self.body, i)

    def test_nada_e_pedido_ao_carregar_a_pagina_e_so_ha_leitura(self):
        topo = [l for l in self.js.split('\n') if re.match(r'^(await )?(carregarIR|abrirIR|irShell)\(', l)]
        self.assertEqual(topo, [])
        b = self.bloco()
        self.assertNotIn("method:'POST'", b)
        self.assertNotIn('recalibrar', b)
        self.assertEqual(re.findall(r"fetch\('([^']+)'", b), ['/api/modelo/ir/conjunto'])

    def test_interface_simplificada_sem_seletores_antigos(self):
        b = self.bloco()
        for velho in ('irMod', 'irAtiv', 'chIrComp', 'chIrLiq', 'lgIrComp', 'lgIrLiq'):
            self.assertNotIn(velho, b, velho)
        for novo in ('chIrConj', 'irMmp3', 'lgIrConj', 'irTabela', 'irAvisos', 'irEscala', 'irOverall'):
            self.assertIn(novo, b, novo)

    def test_reutiliza_helpers_e_componentes_existentes(self):
        b = self.bloco()
        for h in ("ctx('chIrConj',300)", 'noData(', 'registarTip(', 'linhaTip('):
            self.assertIn(h, b)
        self.assertIn('class="chartbox"', b)
        self.assertIn('class="legend"', b)
        self.assertNotIn('<canvas', self.body.split('id="irExp"')[1].split('</details>')[0])

    def test_nenhuma_fase_de_treino_previsao_competicao_ou_taper(self):
        b = self.bloco()
        self.assertNotRegex(b, r'(?i)\b(build|peak|taper|competi\w+|fases? de treino|previs\w+ de (desempenho|forma))\b')

    def test_texto_nao_afirma_validacao_nem_fisiologia_confirmada(self):
        b = self.bloco()
        self.assertIn('nao validado preditivamente', b)
        self.assertIn('nem prova que as modalidades sejam fisiologicamente compar\u00e1veis', b)
        sem_negacao = b.replace('n\u00e3o constantes confirmadas', '').replace('n\u00e3o confirmad', '').replace('nao confirmado', '')\
            .replace('sem CP confirmado', '').replace('CP confirmado', '')    # o CP gravado no CP Model, nao fisiologia
        self.assertNotRegex(sem_negacao, r'(?i)\b(confirmad[oa]s?|comprovad[oa]s?)\b')

    # ---- contrato com o servidor ------------------------------------------
    def test_o_servidor_devolve_as_chaves_que_a_ui_le(self):
        j = self.real['normal']
        for k in ('status', 'estado', 'datas', 'modalidades_ordem', 'cores', 'modalidades', 'overall', 'convencao_temporal'):
            self.assertIn(k, j, k)
        for m in MODS:
            it = j['modalidades'][m]
            for k in ('tem_curva', 'estado', 'leitura', 'parametros', 'parametros_identificados', 'avisos_identificabilidade',
                      'no_limite', 'ajuste', 'lag_dias', 'fora_do_ajuste_desde', 'plausibilidade', 'w', 'pct', 'ajuste_desatualizado'):
                self.assertIn(k, it, (m, k))
            self.assertEqual(len(it['pct']['liquida']), len(j['datas']))
        for k in ('disponivel', 'motivo', 'falta'):
            self.assertIn(k, j['overall'])

    # ---- comportamento (node) ----------------------------------------------
    def test_nao_pede_nada_ao_definir_nem_com_o_painel_fechado(self):
        self.need_node()
        self.assertEqual(self.res['definir_nao_pede_nada']['chamadas'], 0)
        self.assertEqual(self.res['fechado_nao_pede']['chamadas'], 0)

    def test_abrir_pede_um_unico_get_com_o_default_1200_e_nao_repete(self):
        self.need_node()
        self.assertEqual(self.res['abrir_pede_um_get_uma_vez']['chamadas'], [{'url': U, 'metodo': 'GET'}])

    def test_quatro_modalidades_com_as_cores_do_projeto_num_unico_canvas(self):
        self.need_node()
        r = self.res['render_normal']
        self.assertEqual(r['canvases'], ['chIrConj'])
        cores = {s['mod']: s['cor'] for s in r['series']}
        self.assertEqual(cores, {'Bike': '#E74C3C', 'Row': '#3498DB', 'Run': '#2ECC71', 'Ski': '#9B59B6'})
        self.assertEqual(len(r['series']), 12)                                    # 4 modalidades x 3 componentes
        for m, c in cores.items():
            self.assertIn(f'background:{c}', r['leg'])
        self.assertEqual(r['semData'], [])
        self.assertTrue(all(s['n'] == len(self.real['normal']['datas']) for s in r['series']))

    def test_liquida_espessa_continua_e_componentes_finas_com_estilos_diferentes(self):
        self.need_node()
        s = self.res['render_normal']['series']
        bike = {x['comp']: x for x in s if x['mod'] == 'Bike'}
        self.assertGreater(bike['liquida']['lw'], 2 * bike['positiva']['lw'])
        self.assertEqual(bike['liquida']['dash'], '')                              # continua
        self.assertNotEqual(bike['positiva']['dash'], bike['negativa']['dash'])
        self.assertNotEqual(bike['positiva']['dash'], '')
        self.assertNotEqual(bike['negativa']['dash'], '')

    def test_nao_identificavel_tem_traco_distinto_e_aviso_visivel(self):
        self.need_node()
        r = self.res['render_normal']
        por = {(x['mod'], x['comp']): x for x in r['series']}
        self.assertFalse(por[('Bike', 'liquida')]['nid'])
        self.assertTrue(por[('Row', 'liquida')]['nid'])
        for c in ('liquida', 'positiva', 'negativa'):
            self.assertNotEqual(por[('Bike', c)]['dash'], por[('Row', c)]['dash'], c)
        self.assertNotEqual(por[('Row', 'liquida')]['dash'], '')                    # tracejada
        self.assertIn('Row ⚠', r['leg'])
        self.assertNotIn('Bike ⚠', r['leg'])
        self.assertIn('NÃO identificáveis', r['avisos'])

    def test_legenda_tem_modalidades_componentes_e_overall_desativado_com_motivo(self):
        self.need_node()
        r = self.res['render_normal']
        for m in MODS:
            self.assertIn(f"irAlternar('mod','{m}')", r['leg'])
        for c in ('liquida', 'positiva', 'negativa'):
            self.assertIn(f"irAlternar('comp','{c}')", r['leg'])
        ov = re.search(r'<span[^>]*data-mod="Overall"[^>]*>', r['leg']).group(0)
        self.assertIn('aria-disabled="true"', ov)
        self.assertNotIn('onclick', ov)
        self.assertIn('title="', ov)
        self.assertIn('indispon', r['leg'])
        self.assertIn(self.real['normal']['overall']['falta'][0][:20].replace('&', '&amp;'), r['overall'])
        self.assertIn('indispon', r['overall'])

    def test_legenda_so_redesenha_nao_pede_nada_ao_servidor(self):
        self.need_node()
        r = self.res['legenda_alterna_sem_pedir_nada']
        self.assertEqual(r['antes']['chamadas'], 1)
        self.assertEqual(r['depoisChamadas'], 1)                                    # nenhum pedido novo
        self.assertTrue(r['so_liquida0'])                                           # por omissao so a liquida (legibilidade)
        self.assertIn('class="tog off" data-comp="positiva"', r['legInicial'])      # mas positiva/negativa estao a um clique
        self.assertIn('class="tog off" data-comp="negativa"', r['legInicial'])
        self.assertGreater(r['s1'], r['s0'])                                        # ligar positiva+negativa acrescenta tracos
        self.assertLess(r['s2'], r['s1'])                                           # desligar a Bike remove os seus
        self.assertIn('class="tog off" data-mod="Bike"', r['semBike'])

    def test_todas_as_series_desligadas_diz_isso_em_vez_de_desenhar(self):
        self.need_node()
        self.assertEqual(self.res['todas_desligadas']['semData'], ['Todas as séries estão desligadas'])

    def test_tooltip_mostra_data_modalidade_componente_percentagem_e_watts(self):
        self.need_node()
        r, j = self.res['render_normal'], self.real['normal']
        self.assertIn('class="th">', r['tip'])
        for m in MODS:
            self.assertIn(m + ' · liquida', r['tip'])
        self.assertIn('% de P0', r['tip'])
        self.assertIn(' W', r['tip'])
        self.assertIn('data-cor="#E74C3C"', r['tip'])                               # cor da modalidade
        self.assertEqual(r['tipFora'], '')                                          # fora da area do grafico

    def test_tooltip_antes_da_origem_diz_ausente_e_nunca_zero(self):
        self.need_node()
        r, j = self.res['render_normal'], self.real['normal']
        self.assertEqual(j['datas'][0], j['modalidades']['Bike']['inicio_serie'])
        self.assertGreater(j['datas'].index(j['modalidades']['Ski']['inicio_serie']), 0)
        self.assertIn('Ski · liquida (não identific.):ainda sem série (antes da origem)', r['tipInicio'])
        self.assertIn('Bike · liquida:0.0 % de P0', r['tipInicio'])          # Bike existe e vale 0 na origem

    def test_fora_do_ajuste_marcado_na_dica_e_no_traco_esmaecido(self):
        self.need_node()
        m = self.res['misto']
        self.assertIn('(fora do ajuste)', m['tip'])
        self.assertTrue(any(t['a'] < 1 for t in m['tracos']))                       # troco esmaecido
        self.assertTrue(any(t['a'] == 1 for t in m['tracos']))
        self.assertIn('Trecho esmaecido = depois da última observação do ajuste', self.res['render_normal']['escala'])

    def test_magnitude_acima_de_100_pct_e_assinalada_na_dica_e_no_aviso_sem_cortar(self):
        self.need_node()
        m = self.res['misto']
        self.assertIn('⚠ &gt;100 % de P0', m['tip'])
        self.assertIn('magnitude acima de 100 % de P0 (sem corte)', m['avisos'])
        self.assertIn('Run</b>', m['avisos'])
        run = self.real['misto']['modalidades']['Run']
        self.assertLess(min(v for v in run['pct']['liquida'] if v is not None), -100)  # payload nao cortado
        self.assertIn('−100 % de P0', ' '.join(m['texts']))                    # guia, nao corte

    def test_sem_ajuste_e_dados_insuficientes_nao_geram_serie_nem_zeros(self):
        self.need_node()
        r = self.res['sem_ajuste_e_p0_invalido']
        self.assertEqual(sorted({s.split(':')[0] for s in r['series']}), ['Ski', 'Row'][::-1] and ['Row', 'Ski'])
        self.assertIn('(sem curva)', r['leg'])
        self.assertIn('Run</b>: ainda nao ha ajuste', r['avisos'])
        m = self.res['misto']
        self.assertIn('Ski</b>: dados insuficientes', m['avisos'])
        self.assertNotIn('Ski', {s['mod'] for s in m['series']})

    def test_p0_invalido_diz_que_a_normalizacao_e_impossivel_e_nao_desenha_percentagens(self):
        self.need_node()
        r = self.res['sem_ajuste_e_p0_invalido']
        self.assertIn('Normalização impossível', r['escala'])
        self.assertIn('Bike', r['escala'].split('Normalização impossível')[1])
        self.assertNotIn('Bike', {s.split(':')[0] for s in r['series']})

    def test_sem_nenhuma_curva_diz_que_nada_foi_calculado(self):
        self.need_node()
        r = self.res['sem_curvas']
        self.assertIn('Nenhuma modalidade tem curva', r['info'])
        self.assertEqual((r['tracos'], r['series']), (0, 0))
        self.assertTrue(r['semData'])
        self.assertEqual(r['semData'][-1], 'Sem ajuste válido: nenhuma curva desenhada')

    def test_tabela_de_parametros_compacta_com_marca_de_nao_confirmado(self):
        self.need_node()
        t, j = self.res['render_normal']['tabela'], self.real['normal']
        for h in ('P0 (W)', 'K1', 'K2', 'T1 (d)', 'T2 (d)', 'N obs.', 'No limite'):
            self.assertIn(h, t)
        self.assertIn('overflow-x:auto', t)                                          # nao alarga o ecra pequeno
        for m in MODS:
            self.assertIn(f'<b>{m}</b>', t)
        p = j['modalidades']['Bike']['parametros']
        self.assertIn(f'{p["p0"]:.1f}', t)
        self.assertIn(f'{p["k1"]:.4f}', t)
        self.assertEqual(j['modalidades']['Bike']['estado'], 'identificavel')
        self.assertIn('identificável</td>', t)
        self.assertIn('NÃO identificável', t)
        self.assertGreaterEqual(t.count('nao confirmado'), 3 * 5)                    # 3 modalidades x 5 parametros
        self.assertIn('Detalhes dos ajustes', t)
        self.assertIn('RMSE', t)

    def test_tabela_mostra_parametros_no_limite(self):
        self.need_node()
        self.assertIn('t1 (inferior)', self.res['misto']['tabela'])

    def test_tabela_mostra_a_origem_do_p0_fixo_vinda_do_ultimo_yes(self):
        self.need_node()
        t = self.res['render_normal']['tabela']
        self.assertIn('Origem do P0 (fixo)', t)
        self.assertIn('P0 = MMP3 do último YES (270.0 W)', t)     # P0 = MMP3 do ultimo YES (nao o CP)
        self.assertIn('Bike:1200:seam-270.0', t)                  # identificador do registo de origem
        self.assertIn('2025-01-01 07:00', t)                      # data/hora do registo
        self.assertIn('· v2', t)                                  # versao da referencia
        self.assertIn('P0 livre (ajuste anterior)', t)            # ajustes antigos nao se passam pela regra nova
        self.assertIn('P0 fixo, origem: mmp3_ultimo_yes:Bike:1200:seam-270.0', t)
        self.assertNotRegex(t, r'P0 = CP|CP confirmado|CP Model')

    def test_ausencia_de_referencia_mmp3_e_explicita_e_nao_ha_seletor_nem_chamada_ao_cp(self):
        self.need_node()
        a = self.res['render_normal']['avisos']
        self.assertNotIn('sem referência MMP3 válida', a)          # ha YES: nao se avisa
        s = self.res['sem_cp']
        self.assertGreaterEqual(s['avisos'].count('sem referência MMP3 válida'), 4)
        self.assertIn('nenhum registo YES', s['avisos'])
        self.assertIn('não se usa CP, FTP/eFTP nem outra duração', s['avisos'])
        self.assertIn('Mantém-se o último ajuste guardado; não foi criado ajuste novo', s['avisos'])
        for h in (self.res['render_normal']['tabela'], a, s['tabela']):
            self.assertNotIn('<select', h)                        # nenhum seletor de modelo
        self.assertNotRegex(self.bloco(), r'M1 \(WLS|M2 \(WLS|M3 \(NL|modelo_escolhido|/api/cp/')   # a UI do IR nao chama o CP

    def test_ajuste_com_referencia_antiga_e_falha_do_solver_sao_visiveis(self):
        self.need_node()
        a = self.res['desatualizado_e_falha']['avisos']
        self.assertIn('referência de P0 mais antiga (v1)', a)         # o ajuste mostrado usa v1 e ja existe a v2
        self.assertIn('do que a atual (v2)', a)
        self.assertIn('NÃO foram reestimados com o P0 mais recente', a)
        self.assertIn('o Solver falhou ao reestimar com o P0 novo', a)
        self.assertIn('RuntimeError: boom', a)
        self.assertIn('Não há ajuste válido anterior', a)
        self.assertNotIn('recalibrar', a)                              # so informa: a UI nao recalibra

    def test_nao_ha_seletor_de_duracao_e_o_pedido_nao_leva_duracao(self):
        self.assertNotIn('irDur', self.body + self.js)
        self.assertNotIn('IR_DURS', self.js)
        self.assertNotIn('duracao=', self.bloco())
        self.need_node()
        self.assertEqual([c['url'] for c in self.res['abrir_pede_um_get_uma_vez']['chamadas']], [U])

    def test_falha_de_rede_mostra_mensagem_e_nada_de_tabela(self):
        self.need_node()
        r = self.res['falha_de_rede']
        self.assertIn('Nao foi possivel calcular', r['info'])
        self.assertEqual(r['tabela'], '')

    def test_resposta_antiga_nao_sobrepoe_a_selecao_nova(self):
        self.need_node()
        # a resposta lenta (normal: 4 modalidades) chega depois da mais recente (cenario 'misto': 3) e e descartada
        self.assertEqual(self.res['resposta_antiga_nao_sobrepoe_a_nova']['n_mod'], 3)

    def test_texto_vindo_da_base_de_dados_e_escapado(self):
        self.need_node()
        r = self.res['xss_escapado']
        for h in (r['avisos'], r['tabela'], r['leg']):
            self.assertNotIn('<img', h)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', r['tabela'])


if __name__ == '__main__':
    unittest.main()
