"""UI da deteccao automatica de observacoes MMP: o JS real, com DOM e fetch simulados (node).

Substitui a UI de Confirmar/Rejeitar por candidato (Fase 1): nao ha requisito de confirmacao
individual para detetar ou incluir uma observacao elegivel; a unica acao manual e uma exclusao
OPCIONAL. Sem rede.

    python -m pytest tests/pmc/test_mmp_auto_ui.py
"""
import html
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
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

NOME_REF = {'recente': 'melhor recente', 'season': 'season', 'all_time': 'all-time'}


def pct(v):
    return f'{round(v, 1):.1f}%'


class UiDeteccaoAutomatica(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import tabs.tab_pmc as tp
        except BaseException as e:
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        cls.js, cls.body = tp.JS, tp.BODY
        cls.res = None
        # respostas REAIS do servidor (Flask + SQLite temporaria + dados sinteticos), nao escritas a mao
        try:
            import mmp_auto_payload
            cls.real = mmp_auto_payload.gerar()
        except BaseException as e:
            raise AssertionError(f'nao foi possivel gerar as respostas reais do endpoint: {type(e).__name__}: {e}')
        cls.obs = {(o['modalidade'], o['duracao_s'], o['activity_id']): o for o in cls.real['get']['observacoes']}
        if shutil.which('node') is not None:
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, 'pmc_js.js')
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(cls.js)
                q = os.path.join(d, 'real.json')
                with open(q, 'w', encoding='utf-8') as f:
                    json.dump(cls.real, f)
                r = subprocess.run(['node', os.path.join(HERE, 'js', 'harness_mmp_auto.js'), p, q],
                                   capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise AssertionError(f'harness falhou: {r.stdout[-300:]} {r.stderr[-300:]}')
            cls.res = json.loads(r.stdout)

    def need_node(self):
        if self.res is None:
            self.skipTest('node nao disponivel')

    def bloco(self):
        return self.js[self.js.index('// <<registo-testes>>'):self.js.index('// <</registo-testes>>')]

    # ---- estrutura estatica (sem node) ---------------------------------
    def test_painel_existe_e_nao_e_aberto_por_omissao(self):
        m = re.search(r'<details[^>]*id="registoTestes"[^>]*>', self.body)
        self.assertIsNotNone(m)
        self.assertNotIn(' open', m.group(0))
        self.assertIn('ontoggle="abrirRegisto(this)"', m.group(0))
        self.assertIn('id="registoTestesCorpo"', self.body)
        self.assertIn('Deteccao automatica de observacoes MMP', self.body)

    def test_nada_e_pedido_nem_enviado_ao_carregar_a_pagina(self):
        topo = [l for l in self.js.split('\n')
                if re.match(r'^(await )?(carregarRegisto|abrirRegisto|regEnviar|regAtualizar)\(', l)]
        self.assertEqual(topo, [])

    def test_ja_nao_existe_o_fluxo_confirmar_rejeitar_por_candidato(self):
        b = self.bloco()
        self.assertNotIn('/api/protocolo/candidatos', b)
        self.assertNotIn('>Confirmar</button>', b)
        self.assertNotIn('>Rejeitar</button>', b)
        self.assertNotRegex(b, r'(?i)confirmar tudo|confirmar todos|Nada e confirmado')
        self.assertNotIn("'Confirmar'", b)

    def test_a_unica_acao_manual_e_de_exclusao_opcional(self):
        b = self.bloco()
        self.assertEqual(len(re.findall(r"method:'POST'", b)), 2)          # decisao + atualizar deteccao
        self.assertIn('Excluir (opcional)', b)
        self.assertIn('a inclusao nunca exige confirmacao', b)

    def test_bloco_de_sugestoes_da_fase_a_intacto(self):
        i = self.js.index('Testes sugeridos (')
        trecho = self.js[i:i + 900]
        self.assertIn('nao ha nada para marcar', trecho)
        self.assertIn('<div id="sugestoesTeste"></div>', self.body)

    def test_nenhuma_mensagem_diz_que_o_modelo_foi_calibrado_ou_validado(self):
        b = self.bloco()
        self.assertNotRegex(b, r'(?i)modelo (foi|esta|ficou|passou)')
        self.assertNotRegex(b, r'(?i)\b(calibrado|atualizado|actualizado|validado)\b')
        self.assertIn('Detetar nao e validar o modelo', b)

    # ---- contrato: a UI le exatamente o que o servidor devolve ---------------
    def test_respostas_reais_cobrem_os_estados_do_cenario(self):
        g = self.real['get']
        self.assertEqual(g['status'], 'OK')
        self.assertLessEqual(len(g['observacoes']), 40)       # REG_MAX: todas aparecem na tabela
        estados = {(o['estado_deteccao'], o['referencia_tipo'], o['elegibilidade_cientifica'],
                    o['incluida_no_estimador']) for o in g['observacoes']}
        for esperado in (('sem_referencia_anterior', None, 'elegivel', False),
                         ('detetada', 'season', 'elegivel', True), ('detetada', 'all_time', 'elegivel', True),
                         ('detetada', 'recente', 'elegivel', True), ('detetada', 'all_time', 'inelegivel', False),
                         ('detetada', 'all_time', 'desconhecida', False), ('detetada', 'season', 'elegivel', False)):
            self.assertIn(esperado, estados, esperado)
        self.assertEqual(self.real['post_409_status'], 409)
        self.assertEqual(self.real['post_atualizar_status'], 200)
        # chaves que a UI usa existem em TODAS as observacoes devolvidas
        for o in g['observacoes']:
            for k in ('modalidade', 'duracao_s', 'activity_id', 'data', 'watts', 'referencia_tipo', 'referencia_watts',
                      'pct_da_referencia', 'referencias', 'season', 'versao', 'estado_deteccao', 'detetado_em',
                      'elegibilidade_cientifica', 'motivo_elegibilidade', 'ignore_parts_presente',
                      'incluida_no_estimador', 'motivo_exclusao', 'exclusao_manual', 'tags', 'versoes_anteriores'):
                self.assertIn(k, o, k)

    # ---- comportamento (node) ------------------------------------------
    def test_nao_pede_nada_ao_definir_nem_com_o_painel_fechado(self):
        self.need_node()
        self.assertEqual(self.res['definir_nao_pede_nada']['chamadas'], 0)
        self.assertEqual(self.res['fechado_nao_pede']['chamadas'], 0)

    def test_abrir_pede_so_um_get_e_nunca_um_post(self):
        self.need_node()
        self.assertEqual(self.res['abrir_pede_so_get_uma_vez']['chamadas'],
                         ['GET /api/protocolo/observacoes-auto'])

    def test_cabecalhos_do_painel(self):
        self.need_node()
        h = self.res['html']['html']
        for cab in ('Esforco', 'Atividade e data', 'Potencia observada', 'Referencia usada',
                    'Estado de deteccao', 'Elegibilidade cientifica', 'Estimador e validacao',
                    'Exclusao manual'):
            self.assertIn(cab, h, cab)

    def test_cada_linha_mostra_os_valores_do_servidor(self):
        """Para TODAS as observacoes reais: modalidade/duracao, atividade/data, potencia,
        referencia usada com valor e %, n de pontos, as outras referencias com a sua %,
        versao e DATA DE DETECAO."""
        self.need_node()
        h = self.res['html']['html']
        for (mod, dur, aid), o in self.obs.items():
            partes = [p for p in h.split('<tr><td>')[1:] if p.startswith(f'{mod} {dur} s') and f'>{aid}</div>' in p]
            self.assertEqual(len(partes), 1, (mod, dur, aid))
            l = partes[0].split('</tr>')[0]
            self.assertIn(o['data'], l)
            self.assertRegex(l, rf'{round(o["watts"], 1):g} W')
            self.assertIn(f'v{o["versao"]}', l)
            self.assertIn(o['detetado_em'][:10], l, (mod, dur, aid))           # data de detecao
            if o['referencia_tipo'] is None:
                self.assertIn('sem referencia anterior', l)
                continue
            ref = o['referencias'][o['referencia_tipo']]
            self.assertIn(f'<b>{NOME_REF[o["referencia_tipo"]]}</b>', l)
            self.assertIn(f': {round(o["referencia_watts"], 1):g} W', l)
            self.assertIn(f'{pct(o["pct_da_referencia"])} desta referencia ({ref["n_pontos"]} pts na referencia)', l)
            for k, r in o['referencias'].items():                              # % e n de CADA referencia
                if k == o['referencia_tipo']:
                    continue
                esperado = f'{NOME_REF[k]} {pct(r["pct"])}{" (qualifica)" if r["qualifica"] else ""}, {r["n_pontos"]} pts'
                self.assertIn(esperado, l, (mod, dur, aid, k))

    def test_percentagem_por_referencia_exemplo_concreto(self):
        self.need_node()
        l = self.res['html']['linhas']['season']                               # b2: 280 W, season anterior 250 W
        self.assertIn('<b>season</b> (S26): 250 W', l)
        self.assertIn('112.0% desta referencia (1 pts na referencia)', l)
        # 280/340 sobre os 5 pontos anteriores ELEGIVEIS. a5 (ignore_parts: elegibilidade desconhecida) ja nao serve de
        # referencia: regra do seletor, provada em test_ir_referencia_ignore_parts. Antes contava como 6.o ponto;
        # o valor da referencia (340 W) nao muda, so a contagem.
        self.assertIn('all-time 82.4%, 5 pts', l)
        self.assertIn('2026-10-17', l)                                         # detetado_em
        l = self.res['html']['linhas']['all_time']                             # b4
        self.assertIn('<b>all-time</b>: 340 W', l)
        self.assertIn('92.6% desta referencia', l)

    def test_historico_temporal_sem_historico_no_primeiro_arranque(self):
        """Tudo foi detetado de uma vez (hoje): a UI diz que nao ha historico temporal e que nada e retroativo."""
        self.need_node()
        h = self.res['html']['html']
        t = self.real['get']['historico_temporal']
        self.assertEqual(t['estado'], 'sem_historico_temporal')
        self.assertIn('Historico temporal (sem_historico_temporal)', h)
        self.assertIn(f'{t["n_conhecidas_no_dia_ou_antes"]} de {t["n_observacoes"]} observacoes foram detetadas no dia do esforco', h)
        self.assertIn(f'{t["n_conhecidas_so_depois"]} so depois', h)
        self.assertIn('nao pode ser reconstruido nem se atribuem datas de deteccao retroativas', h)
        self.assertIn('A validacao retrospetiva usa todas as observacoes atuais', h)
        self.assertIn('conhecido na epoca', h)

    def test_historico_temporal_parcial_apos_deteccao_no_proprio_dia(self):
        self.need_node()
        t = self.real['get_parcial']['historico_temporal']
        self.assertEqual(t['estado'], 'parcial')
        self.assertGreater(t['n_conhecidas_no_dia_ou_antes'], 0)
        h = self.res['html_parcial']['html']
        self.assertIn('Historico temporal (parcial)', h)
        self.assertIn(f'{t["n_conhecidas_no_dia_ou_antes"]} de {t["n_observacoes"]} observacoes foram detetadas no dia do esforco', h)
        self.assertIn('So parte das observacoes foi detetada no dia do esforco', h)
        self.assertNotIn('Historico temporal (sem_historico_temporal)', h)

    def test_validacao_real_distingue_os_dois_modos(self):
        e, r = self.real['validacao_epoca']['modos']['conhecido_na_epoca'], self.real['validacao_retro']['modos']['retrospectivo']
        self.assertNotEqual(e['descricao_modo'], r['descricao_modo'])
        self.assertEqual(e['itens'][0]['historico_temporal']['estado'], 'sem_historico_temporal')
        self.assertNotIn('historico_temporal', r['itens'][0])
        self.assertEqual(e['itens'][0]['metodos']['M2']['n'], 0)
        self.assertGreater(r['itens'][0]['metodos']['M2']['n'], 0)

    def test_referencia_curta_so_quando_tem_menos_pontos_que_o_minimo_de_serie(self):
        self.need_node()
        m = self.real['get']['criterio']['min_pontos_serie']
        self.assertEqual(m, 5)
        h = self.res['html']['html']
        n_curtas = 0
        for (mod, dur, aid), o in self.obs.items():
            if o['referencia_tipo'] is None:
                continue
            l = [p for p in h.split('<tr><td>')[1:] if p.startswith(f'{mod} {dur} s') and f'>{aid}</div>' in p][0]
            n = o['referencias'][o['referencia_tipo']]['n_pontos']
            if n < m:
                n_curtas += 1
                self.assertIn(f'referencia curta: {n} pts', l, (mod, dur, aid))
            else:
                self.assertNotIn('referencia curta', l, (mod, dur, aid))
        self.assertGreater(n_curtas, 0)                       # o cenario tem referencias curtas e longas
        # o rotulo nao exclui nada: so informa
        self.assertIn('Nao e um criterio de exclusao', self.bloco())

    def test_ordem_do_mesmo_dia_sem_prova_e_assinalada(self):
        self.need_node()
        l = self.res['html']['linhas']['mesmo_dia']
        self.assertIn('1 esforco(s) do mesmo dia sem hora que prove a ordem ficaram fora da referencia', l)
        o = self.obs[('Bike', 1200, 's1')]
        self.assertEqual(o['ordem_mesmo_dia']['ambiguos'], 1)
        self.assertEqual(self.obs[('Bike', 1200, 'b2')]['ordem_mesmo_dia']['ambiguos'], 0)
        self.assertNotIn('mesmo dia', self.res['html']['linhas']['season'])
        # nenhum dos dois serve de referencia ao outro: ambos tem 335/336 contra o historico
        for aid in ('s1', 's2'):
            ref = self.obs[('Bike', 1200, aid)]['referencias']['all_time']
            self.assertEqual(ref['referencia_activity_id'] not in ('s1', 's2'), True, aid)

    def test_a_ui_nao_apresenta_a_deteccao_como_teste_padronizado_nem_validacao(self):
        b = self.bloco()
        self.assertIn('o maximo de uma curva nao e um teste laboratorial ou padronizado', b)
        self.assertIn('Detetar nao e validar o modelo', b)
        h = self.res['html']['html']
        self.assertNotRegex(h, r'(?i)teste padronizado confirmado|modelo validado')

    def test_primeiro_ponto_aparece_sem_referencia_e_fora_do_estimador(self):
        self.need_node()
        l = self.res['html']['linhas']['sem_ref']
        self.assertIn('sem referencia anterior', l)
        self.assertIn('sem referencia', l)
        self.assertIn('fora do estimador e da validacao', l)
        self.assertIn('sem referencia anterior (primeiro ponto da serie)', l)
        self.assertNotIn('entra no estimador e na validacao', l)
        self.assertNotIn('>detetada<', l)

    def test_elegibilidade_exclusao_e_exclusao_manual_distinguiveis(self):
        self.need_node()
        L = self.res['html']['linhas']
        self.assertIn('>elegivel<', L['season'])
        self.assertIn('entra no estimador e na validacao', L['season'])
        self.assertIn('>inelegivel<', L['ignore_power'])
        self.assertIn('icu_ignore_power=true (potencia ignorada)', L['ignore_power'])
        self.assertIn('excluida manualmente', L['excl_manual'])
        self.assertIn('o valor mudou desde a exclusao (a exclusao mantem-se)', L['excl_manual'])
        self.assertIn('>elegivel<', L['excl_manual'])                        # elegivel, mas excluida a mao
        self.assertIn('fora do estimador e da validacao', L['excl_manual'])
        self.assertIn('v2', L['excl_manual'])
        self.assertIn('1 versao(oes) anterior(es)', L['excl_manual'])
        self.assertNotIn('excluida manualmente', L['season'])
        h = self.res['html']['html']
        self.assertGreaterEqual(len({m for m in re.findall(r'border:1px solid (#[0-9A-Fa-f]{6})', h)}), 4)

    def test_ignore_parts_visivel_mas_fora_do_estimador(self):
        self.need_node()
        l = self.res['html']['linhas']['ignore_parts']
        self.assertIn('ignore_parts: detetado, nao elegivel', l)
        self.assertIn('ignore_parts presente: segmento valido nao identificado', l)
        self.assertIn('fora do estimador e da validacao', l)
        self.assertNotIn('entra no estimador', l)

    def test_tags_de_tempo_e_distancia_sao_mostradas_sem_converter(self):
        self.need_node()
        l = self.res['html']['linhas']['tags']
        self.assertIn('tag 20min', l)
        self.assertIn('tag 5km', l)
        self.assertIn('tag de distancia: informativa, nao convertida em duracao', l)
        self.assertIn('tag de tempo que coincide com a duracao', l)           # 20min == 1200 s
        self.assertNotIn('zona 2', l)                                          # nao e tempo nem distancia

    def test_limitacoes_e_criterio_vindos_do_servidor(self):
        self.need_node()
        h = self.res['html']['html']
        g = self.real['get']
        for t in g['limitacoes']:
            self.assertIn(html.escape(t), h, t[:50])                           # as do servidor, nao as da UI
        self.assertIn('O all-time cobre so a janela historica sincronizada', h)
        self.assertIn('Limiar de proximidade 0.92', h)
        self.assertIn('historico sincronizado 5 anos', h)
        self.assertIn('fronteiras de season: teste', h)
        n = len(g['observacoes'])
        inc = sum(1 for o in g['observacoes'] if o['incluida_no_estimador'])
        self.assertIn(f'{n} observacoes detetadas, {inc} entram no estimador e {n - inc} ficam de fora', h)

    def test_html_das_strings_do_servidor_e_escapado(self):
        self.need_node()
        l = self.res['html']['linhas']['excl_manual']        # o motivo manual e texto livre do utilizador
        self.assertNotIn('<img src=x', self.res['html']['html'])
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', l)

    def test_acoes_so_de_exclusao_opcional(self):
        self.need_node()
        h = self.res['html']['html']
        com_exclusao = [o for o in self.obs.values() if o['exclusao_manual']]
        self.assertEqual(len(com_exclusao), 1)
        self.assertEqual(h.count('Excluir (opcional)'), len(self.obs) - 1)   # todas, menos a que ja esta excluida
        self.assertEqual(h.count('Remover exclusao'), 1)       # so a observacao ja excluida e elegivel
        self.assertNotIn('>Confirmar<', h)
        self.assertNotIn('>Rejeitar<', h)

    def test_excluir_sem_motivo_ou_ao_cancelar_nada_e_enviado(self):
        self.need_node()
        self.assertEqual(self.res['excluir_sem_motivo_nao_envia']['posts'], 0)
        self.assertIn('motivo', self.res['excluir_sem_motivo_nao_envia']['msg'])
        self.assertEqual(self.res['cancelar_nao_envia']['posts'], 0)

    def test_excluir_envia_um_unico_post_explicito_com_a_versao_vista(self):
        self.need_node()
        r = self.res['excluir_envia_um_post_explicito']
        self.assertIn('Excluir manualmente', r['form'])
        self.assertIn('Opcional', r['form'])
        self.assertEqual(len(r['posts']), 1)
        c = r['posts'][0]['corpo']
        self.assertIs(c['confirmacao_explicita'], True)
        b2 = self.obs[('Bike', 1200, 'b2')]
        self.assertEqual((c['modalidade'], c['duracao_s'], c['activity_id']), ('Bike', 1200, 'b2'))
        self.assertEqual((c['decisao'], c['motivo'], c['origem_registo'], c['metodo_confirmacao'],
                          c['aceita_elegibilidade_desconhecida'], c['watts_vistos']),
                         ('rejeitar', 'esforco nao maximo', 'ui', 'manual', False, b2['watts']))
        self.assertNotIn('watts', c)
        self.assertEqual(r['gets'], 2)
        self.assertIn('nao valida o modelo', r['msg'])

    def test_o_corpo_do_post_e_aceite_pelo_endpoint_real(self):
        """O que a UI envia tem exatamente os campos que o servidor real aceitou."""
        self.need_node()
        self.assertEqual(self.real['post_decisao_ok_status'], 200)
        self.assertEqual(self.real['post_decisao_ok']['status'], 'OK')

    def test_remover_exclusao_so_levanta_a_exclusao(self):
        self.need_node()
        r = self.res['remover_exclusao_envia_confirmar']
        self.assertIn('Remover a exclusao manual de', r['form'])
        self.assertIn('continua sujeita a elegibilidade cientifica', r['form'])
        self.assertEqual(r['posts'][0]['corpo']['decisao'], 'confirmar')
        self.assertEqual(r['posts'][0]['corpo']['activity_id'], 'b3')

    def test_atualizar_deteccao_e_explicito_e_recarrega(self):
        self.need_node()
        r = self.res['atualizar_deteccao']
        self.assertEqual(r['chamadas'], ['GET /api/protocolo/observacoes-auto',
                                         'POST /api/protocolo/observacoes-auto/atualizar',
                                         'GET /api/protocolo/observacoes-auto'])
        n = self.real['post_atualizar']['novas_versoes']
        self.assertIn(f'{n} versao(oes) nova(s)', r['msg'])
        self.assertIn('Isto nao valida o modelo', r['msg'])

    def test_erros_e_falhas_de_rede_sao_mostrados_sem_afirmar_sucesso(self):
        self.need_node()
        self.assertEqual(self.real['post_erro_status'], 400)
        self.assertIn(self.real['post_erro']['mensagem'], self.res['erro_do_servidor_e_mostrado']['msg'])
        self.assertEqual(self.res['falha_de_rede_no_post']['msg'], 'Falha de rede: nada foi registado.')
        self.assertIn('Nao foi possivel carregar', self.res['falha_de_rede_no_get']['html'])

    def test_payloads_invalidos_e_vazios(self):
        self.need_node()
        p = self.res['payloads_estranhos']
        self.assertIn('Nao foi possivel carregar', p['nulo'])
        self.assertIn('Nao foi possivel carregar', p['erro'])
        self.assertIn('Nao foi possivel carregar', p['sem_lista'])
        self.assertIn('Ainda nao ha observacoes detetadas', p['vazio'])

    def test_filtros_batem_com_os_dados_do_servidor(self):
        self.need_node()
        f = self.res['filtros']
        self.assertEqual(f['todas'], f['total_servidor'])
        self.assertEqual((f['row_bike'], f['row_row']), (False, True))
        self.assertEqual(f['row_linhas'], f['row_servidor'])
        self.assertEqual(f['dentro_linhas'], f['incluidas_servidor'])
        self.assertEqual(f['fora_linhas'], f['total_servidor'] - f['incluidas_servidor'])
        self.assertEqual(f['at_linhas'], f['all_time_servidor'])
        self.assertEqual(f['season_linhas'], f['season_servidor'])
        self.assertGreater(f['season_linhas'], 0)

    def test_409_valor_atualizado_nao_diz_sucesso_e_recarrega(self):
        self.need_node()
        r = self.res['conflito_409_valor_mudou']
        self.assertEqual(self.real['post_409']['campo'], 'watts_vistos')
        self.assertIn('nada foi registado', r['msg'])
        self.assertIn('A lista foi recarregada com o valor atual', r['msg'])
        self.assertNotRegex(r['msg'], r'(?i)^registado')
        self.assertEqual((r['posts'], r['gets']), (1, 2))


if __name__ == '__main__':
    unittest.main()
