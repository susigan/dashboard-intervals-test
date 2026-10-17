"""Fase 1 — UI do registo de testes: o JS real, com DOM e fetch simulados (node), sem rede.

    python -m pytest tests/pmc/test_fase1_registo_ui.py
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
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class UiRegisto(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
                import tabs.tab_pmc as tp
        except BaseException as e:
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        cls.js, cls.body = tp.JS, tp.BODY
        cls.res = None
        if shutil.which('node') is not None:
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, 'pmc_js.js')
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(cls.js)
                r = subprocess.run(['node', os.path.join(HERE, 'js', 'harness_registo.js'), p],
                                   capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise AssertionError(f'harness falhou: {r.stdout[-300:]} {r.stderr[-300:]}')
            cls.res = json.loads(r.stdout)

    def need_node(self):
        if self.res is None:
            self.skipTest('node nao disponivel')

    # ---- estrutura estatica (sem node) ---------------------------------
    def test_painel_existe_e_nao_e_aberto_por_omissao(self):
        m = re.search(r'<details[^>]*id="registoTestes"[^>]*>', self.body)
        self.assertIsNotNone(m)
        self.assertNotIn(' open', m.group(0))
        self.assertIn('ontoggle="abrirRegisto(this)"', m.group(0))
        self.assertIn('id="registoTestesCorpo"', self.body)

    def test_nada_e_pedido_nem_enviado_ao_carregar_a_pagina(self):
        topo = [l for l in self.js.split('\n') if re.match(r'^(await )?(carregarRegisto|abrirRegisto|regEnviar)\(', l)]
        self.assertEqual(topo, [])
        self.assertEqual(len(re.findall(r"/api/protocolo/testes/decisao", self.js)), 1)
        # o unico POST vive dentro de regEnviar
        corpo = self.js[self.js.index('async function regEnviar'):self.js.index('// <</registo-testes>>')]
        self.assertIn("method:'POST'", corpo)
        self.assertEqual(len(re.findall(r"method:'POST'", self.js)), len(re.findall(r"method:'POST'", corpo)) +
                         len(re.findall(r"method:'POST'", self.js[:self.js.index('// <<registo-testes>>')])))

    def test_bloco_de_sugestoes_da_fase_a_intacto(self):
        i = self.js.index('Testes sugeridos (')
        trecho = self.js[i:i + 900]
        self.assertIn('nao ha nada para marcar', trecho)
        self.assertIn('nao atualizam os parametros do', trecho)
        self.assertIn('<div id="sugestoesTeste"></div>', self.body)

    def test_nenhuma_mensagem_diz_que_o_modelo_foi_calibrado_ou_atualizado(self):
        bloco = self.js[self.js.index('// <<registo-testes>>'):self.js.index('// <</registo-testes>>')]
        self.assertNotRegex(bloco, r'(?i)modelo (foi|esta|ficou|passou)')
        self.assertNotRegex(bloco, r'(?i)\b(calibrado|atualizado|actualizado)\b')
        self.assertIn('nao calibra nem atualiza o modelo', bloco)

    # ---- comportamento (node) ------------------------------------------
    def test_nao_pede_nada_ao_definir_nem_com_o_painel_fechado(self):
        self.need_node()
        self.assertEqual(self.res['definir_nao_pede_nada']['chamadas'], 0)
        self.assertEqual(self.res['fechado_nao_pede']['chamadas'], 0)

    def test_abrir_pede_so_um_get_e_nunca_um_post(self):
        self.need_node()
        self.assertEqual(self.res['abrir_pede_so_get_uma_vez']['chamadas'], ['GET /api/protocolo/candidatos'])

    def test_candidatos_estados_e_elegibilidade_distinguiveis(self):
        self.need_node()
        h = self.res['html']['html']
        for txt in ('detetado, sem decisao', 'confirmado', 'rejeitado', 'elegivel',
                    'inelegivel', 'elegibilidade desconhecida'):
            self.assertIn(txt, h, txt)
        # cores distintas para os tres estados de elegibilidade
        self.assertEqual(len({m for m in re.findall(r'border:1px solid (#[0-9A-Fa-f]{6})', h)}) >= 4, True)

    def test_inelegivel_nao_tem_botao_confirmar_ativo(self):
        self.need_node()
        h = self.res['html']['html']
        linha = h[h.index('i3'):]
        linha = linha[:linha.index('</tr>')]
        self.assertRegex(linha, r'<button type="button" disabled title="icu_ignore_power=true[^"]*" onclick="regAbrir\(2,\'confirmar\'\)">Confirmar')
        self.assertRegex(linha, r"regAbrir\(2,'rejeitar'\)\">Rejeitar")
        self.assertNotRegex(linha, r"regAbrir\(2,'rejeitar'\)\"\s+disabled")

    def test_texto_esclarece_que_nao_calibra(self):
        self.need_node()
        self.assertIn('confirmar um teste nao calibra nem atualiza o modelo', self.res['html']['html'])
        self.assertIn('nunca e tratada como elegivel', self.res['html']['html'])

    def test_html_das_strings_do_servidor_e_escapado(self):
        self.need_node()
        h = self.res['html']['html']
        self.assertNotIn('<img src=x', h)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', h)

    def test_sem_motivo_ou_ao_cancelar_nada_e_enviado(self):
        self.need_node()
        self.assertEqual(self.res['sem_motivo_nao_envia']['posts'], 0)
        self.assertIn('motivo', self.res['sem_motivo_nao_envia']['msg'])
        self.assertEqual(self.res['cancelar_nao_envia']['posts'], 0)

    def test_confirmar_envia_um_unico_post_explicito(self):
        self.need_node()
        r = self.res['confirmar_envia_um_post_explicito']
        self.assertEqual(len(r['posts']), 1)
        c = r['posts'][0]['corpo']
        self.assertIs(c['confirmacao_explicita'], True)
        self.assertEqual((c['decisao'], c['motivo'], c['origem_registo'], c['metodo_confirmacao'],
                          c['aceita_elegibilidade_desconhecida']),
                         ('confirmar', 'teste padronizado', 'ui', 'manual', False))
        self.assertNotIn('watts', c)                        # nunca vem do cliente
        self.assertEqual(r['gets'], 2)                      # recarrega a lista depois
        self.assertIn('nao calibra nem atualiza o modelo', r['msg'])

    def test_opt_in_so_para_desconhecida_e_so_se_marcado(self):
        self.need_node()
        r = self.res['desconhecida_envia_opt_in_so_se_marcado']
        self.assertTrue(r['form_tem_checkbox'])
        self.assertIs(r['post']['aceita_elegibilidade_desconhecida'], True)
        self.assertFalse(self.res['elegivel_sem_checkbox']['tem'])

    def test_erros_e_falhas_de_rede_sao_mostrados_sem_afirmar_sucesso(self):
        self.need_node()
        self.assertIn('so pode ser rejeitado', self.res['erro_do_servidor_e_mostrado']['msg'])
        self.assertEqual(self.res['falha_de_rede_no_post']['msg'], 'Falha de rede: nada foi registado.')
        self.assertIn('Nao foi possivel carregar', self.res['falha_de_rede_no_get']['html'])

    def test_payloads_invalidos_vazios_e_sem_bd(self):
        self.need_node()
        p = self.res['payloads_estranhos']
        self.assertIn('Nao foi possivel carregar', p['nulo'])
        self.assertIn('Nao foi possivel carregar', p['erro'])
        self.assertIn('Sem base de dados', p['sem_bd'])
        self.assertIn('Sem candidatos', p['vazio'])

    def test_filtros(self):
        self.need_node()
        f = self.res['filtros']
        self.assertEqual((f['row_tem_bike'], f['row_tem_row']), (False, True))
        self.assertEqual(f['conf_linhas'], 1)

    def test_revisao_mmp_estados_motivos_e_acoes(self):
        self.need_node()
        r = self.res['revisao_mmp']
        h = r['html']
        # valor mudado: pendente, decisao anterior e valores antigo/novo visiveis
        self.assertIn('pendente: valor mudou', h)
        self.assertRegex(h, r'decisao anterior: confirmado \(revisao 1\) sobre 220 W')
        self.assertIn('agora 225 W', h)
        # elegibilidade do estimador e motivo de exclusao
        self.assertIn('estimador: aceitaria', h)
        self.assertIn('estimador: nao usaria', h)
        self.assertIn('ignore_parts presente', h)
        # resumo sem confirmacao em lote
        self.assertRegex(h, r'Pendentes: 2 \(1 sem decisao, 1 com valor mudado\)')
        self.assertIn('Nada e confirmado em lote', h)
        self.assertNotRegex(h, r'(?i)confirmar tudo|confirmar todos')
        # acoes explicitas disponiveis
        self.assertIn('>Confirmar</button>', h)
        self.assertIn('>Rejeitar</button>', h)
        self.assertIn('O valor mudou', r['form0'])
        self.assertIn('continua excluida do estimador e da validacao', r['form1'])   # texto de ignore_parts (C)
        # POST: um so, com a versao vista, sem enviar watts como valor a gravar
        self.assertEqual(r['post']['watts_vistos'], 225)
        self.assertNotIn('watts', r['post'])
        # filtros por data
        self.assertTrue(r['dep_i2'] and not r['dep_i1'])
        self.assertTrue(r['ate_i1'] and not r['ate_i2'])
        self.assertEqual(r['pend_linhas'], 2)

    def test_409_valor_mudou_nao_diz_sucesso_e_recarrega(self):
        self.need_node()
        r = self.res['conflito_409_valor_mudou']
        self.assertIn('curva foi atualizada', r['msg'])
        self.assertIn('nada foi registado', r['msg'])
        self.assertNotRegex(r['msg'], r'(?i)^registado')
        self.assertEqual((r['posts'], r['gets']), (1, 2))   # recarrega a lista
        self.assertEqual(r['watts'], 305)                    # valor atual apresentado

    def test_ignore_parts_texto_explicito_sobre_registo_e_estimador(self):
        self.need_node()
        r = self.res['ignore_parts_texto']
        f = r['formIp']
        self.assertIn('Pode ser revista e confirmada ou rejeitada manualmente', f)
        self.assertIn('ignore_parts nao impede o registo', f)
        self.assertRegex(f, r'continua excluida do estimador e da validacao')
        self.assertIn('elegibilidade cientifica', f)
        self.assertIn('pode registar a decisao; fora do estimador e da validacao', r['lista'])
        # outro motivo de exclusao mantem o aviso generico (nao herda o texto de ignore_parts)
        self.assertIn('Confirmar so regista a decisao', r['formOutro'])
        self.assertEqual(f.count('Esta atividade tem ignore_parts'), 1)
        self.assertEqual(r['formOutro'].count('Esta atividade tem ignore_parts'), 0)   # so a ignore_parts


if __name__ == '__main__':
    unittest.main()
