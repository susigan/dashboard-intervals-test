"""Etapa 14: contrato temporal do endpoint /api/modelo/ir/validacao e regra de selecao temporal de y.

Dados SINTETICOS (cenario de test_ir_walkforward_temporal). Cobre:
  * funcoes puras do contrato (modelo_ir_contrato): estados, naturezas, bloqueio, limitacoes;
  * endpoint: historico completo, incompleto, vazio (temporal e curvas), indisponivel (curvas);
  * o avaliador NAO e chamado no modo temporal quando os historicos obrigatorios faltam;
  * compatibilidade: chaves antigas mantidas (valores legados em *_legado quando o valor mudou);
  * revisao posterior de y nao altera a previsao nem o y de alvos anteriores (ou do proprio alvo).
"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

import test_modelo_ir_servico as TS  # noqa: E402
import test_ir_walkforward_temporal as W  # noqa: E402
from test_ir_walkforward_temporal import dia_mais  # noqa: E402
import db  # noqa: E402
import modelo_ir_contrato as mct  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402


def setUpModule():
    TS.setUpModule()


def tearDownModule():
    TS.tearDownModule()


# ── contrato puro (sem base de dados) ───────────────────────────────────────────

class ContratoPuro(unittest.TestCase):

    def test_bloqueio_explicito_por_cada_historico(self):
        self.assertEqual(mct.motivo_bloqueio('erro', None, 'erro', None), 'historico_temporal_indisponivel')
        self.assertEqual(mct.motivo_bloqueio('tabelas_ausentes', None, 'ok', (1, 1)), 'historico_temporal_indisponivel')
        self.assertEqual(mct.motivo_bloqueio('ok', (0, 0), 'ok', (1, 1)), 'historico_temporal_vazio')
        self.assertEqual(mct.motivo_bloqueio('ok', (1, 1), 'tabelas_ausentes', None), 'historico_curvas_indisponivel')
        self.assertEqual(mct.motivo_bloqueio('ok', (1, 1), 'ok', (0, 0)), 'historico_curvas_vazio')
        self.assertIsNone(mct.motivo_bloqueio('ok', (1, 1), 'ok', (5, 9)))

    def test_estado_temporal_disponivel_incompleto_vazio_indisponivel(self):
        self.assertEqual(mct.estado_historico_temporal('ok', (3, 4), 0), mct.ESTADO_DISPONIVEL)
        self.assertEqual(mct.estado_historico_temporal('ok', (3, 4), 2), mct.ESTADO_INCOMPLETO)
        self.assertEqual(mct.estado_historico_temporal('ok', (0, 0), 0), mct.ESTADO_VAZIO)
        self.assertEqual(mct.estado_historico_temporal('erro', None, 0), mct.ESTADO_INDISPONIVEL)

    def test_estado_curvas_disponivel_incompleto_vazio_indisponivel(self):
        self.assertEqual(mct.estado_historico_curvas('ok', (4, 9), 0), mct.ESTADO_DISPONIVEL)
        self.assertEqual(mct.estado_historico_curvas('ok', (4, 9), 3), mct.ESTADO_INCOMPLETO)
        self.assertEqual(mct.estado_historico_curvas('ok', (0, 0), 0), mct.ESTADO_VAZIO)
        self.assertEqual(mct.estado_historico_curvas('tabelas_ausentes', None, 0), mct.ESTADO_INDISPONIVEL)

    def _item(self, ir=3, av=3, rej=0, cat=None, motivos=None):
        return {'n_alvos_previstos': ir, 'n_alvos_avaliaveis': av, 'n_alvos_rejeitados': rej,
                'rejeicoes_por_categoria': cat or {}, 'alvos_sem_previsao_ir': motivos or {}}

    def test_retrospectivo_nunca_e_validacao_temporal(self):
        c = mct.montar(('retrospectivo',), {'retrospectivo': [self._item()]}, {}, 'ok', (1, 1), 'ok', (1, 1))
        r = c['resultado_por_modo']['retrospectivo']
        self.assertEqual(c['natureza_avaliacao']['retrospectivo'], mct.NATUREZA_RETROSPECTIVA)
        self.assertFalse(r['validacao_temporal'])
        self.assertTrue(any('nao e validacao historica' in t.lower() for t in c['limitacoes']))

    def test_temporal_validada_sem_rejeicoes_historicas(self):
        c = mct.montar(('conhecido_na_epoca',), {'conhecido_na_epoca': [self._item(rej=1, cat={'amostra': 1})]},
                       {}, 'ok', (1, 1), 'ok', (1, 1))
        self.assertEqual(c['natureza_avaliacao']['conhecido_na_epoca'], mct.NATUREZA_VALIDADA)
        self.assertEqual(c['historico_temporal_estado'], mct.ESTADO_DISPONIVEL)
        self.assertEqual(c['historico_curvas_estado'], mct.ESTADO_DISPONIVEL)

    def test_rejeicao_historica_torna_temporal_incompleta(self):
        c = mct.montar(('conhecido_na_epoca',), {'conhecido_na_epoca': [self._item(rej=2, cat={'marcos': 2})]},
                       {}, 'ok', (1, 1), 'ok', (1, 1))
        self.assertEqual(c['natureza_avaliacao']['conhecido_na_epoca'], mct.NATUREZA_INCOMPLETA)
        self.assertEqual(c['historico_temporal_estado'], mct.ESTADO_INCOMPLETO)

    def test_modo_bloqueado_nao_tem_itens_nem_validacao(self):
        c = mct.montar(('conhecido_na_epoca',), {}, {'conhecido_na_epoca': 'historico_curvas_vazio'},
                       'ok', (1, 1), 'ok', (0, 0))
        r = c['resultado_por_modo']['conhecido_na_epoca']
        self.assertTrue(r['bloqueado'])
        self.assertEqual(r['n_alvos_previstos'], 0)
        self.assertEqual(c['natureza_avaliacao']['conhecido_na_epoca'], mct.NATUREZA_BLOQUEADA)
        self.assertFalse(r['validacao_temporal'])
        self.assertEqual(c['historico_curvas_estado'], mct.ESTADO_VAZIO)

    def test_totais_somam_os_itens(self):
        itens = [self._item(ir=5, av=4, rej=1, cat={'yes': 1}, motivos={'yes_sem_registo_antes_do_alvo': 1}),
                 self._item(ir=2, av=2, rej=0)]
        r = mct.montar(('conhecido_na_epoca',), {'conhecido_na_epoca': itens}, {}, 'ok', (1, 1), 'ok', (1, 1))
        res = r['resultado_por_modo']['conhecido_na_epoca']
        self.assertEqual((res['n_alvos_previstos'], res['n_alvos_avaliaveis'], res['n_alvos_rejeitados']), (7, 6, 1))
        self.assertEqual(res['rejeicoes_por_categoria'], {'yes': 1})
        self.assertEqual(res['rejeicoes_por_motivo'], {'yes_sem_registo_antes_do_alvo': 1})

    def test_definicoes_cobrem_os_quatro_estados(self):
        self.assertEqual(set(mct.DEFINICOES_ESTADO), {'disponivel', 'incompleto', 'vazio', 'indisponivel'})


# ── endpoint ─────────────────────────────────────────────────────────────────────

class ContratoEndpoint(W.CenarioTemporal):

    def _preparar_completo(self):
        self.preparar_historico()
        primeiro = min(e['data'] for e in self._efeitos)
        self.registar_marcos(dia_mais(primeiro, 60))
        self.registar_yes_dia_a_dia()

    def _get(self, modo='ambos'):
        return self.c.get(f'/api/modelo/ir/validacao?tipo=Bike&duracao=1200&modo={modo}').get_json()

    def test_historico_completo_mas_com_alvos_anteriores_e_incompleto(self):
        self._preparar_completo()
        j = self._get('conhecido_na_epoca')
        self.assertEqual(j['historico_temporal_estado'], 'incompleto')
        self.assertEqual(j['historico_curvas_estado'], 'disponivel')
        self.assertIn(j['natureza_avaliacao']['conhecido_na_epoca'], ('temporal_incompleta', 'temporal_validada'))
        self.assertFalse(j['resultado_por_modo']['conhecido_na_epoca']['bloqueado'])

    def test_registo_temporal_vazio_bloqueia_sem_avaliar(self):
        self.preparar_historico()                     # migracoes aplicadas, mas sem marcos nem YES
        with mock.patch.object(mis, 'validar_preditivo', wraps=mis.validar_preditivo) as vp:
            j = self._get('conhecido_na_epoca')
        modos_chamados = [c.kwargs.get('modos') for c in vp.call_args_list]
        self.assertNotIn(('conhecido_na_epoca',), modos_chamados)
        self.assertEqual(j['historico_temporal_estado'], 'vazio')
        self.assertEqual(j['resultado_por_modo']['conhecido_na_epoca']['motivo_bloqueio'], 'historico_temporal_vazio')
        self.assertEqual(j['itens'], [])

    def test_curvas_vazias_bloqueiam(self):
        self._preparar_completo()
        db._exec('DELETE FROM ir_curvas_versoes', (), None)
        j = self._get('conhecido_na_epoca')
        self.assertEqual(j['historico_curvas_estado'], 'vazio')
        self.assertEqual(j['resultado_por_modo']['conhecido_na_epoca']['motivo_bloqueio'], 'historico_curvas_vazio')
        self.assertEqual(j['natureza_avaliacao']['conhecido_na_epoca'], 'bloqueada_por_historico_insuficiente')

    def test_curvas_ausentes_sao_indisponiveis_e_bloqueiam(self):
        self._preparar_completo()
        db._exec('DROP TABLE IF EXISTS ir_curvas_versoes', (), None)
        j = self._get('conhecido_na_epoca')
        self.assertEqual(j['historico_curvas_estado'], 'indisponivel')
        self.assertEqual(j['resultado_por_modo']['conhecido_na_epoca']['motivo_bloqueio'],
                         'historico_curvas_indisponivel')
        self.assertEqual(j['itens'], [])

    def test_ambos_os_modos_separa_retrospectivo_de_temporal(self):
        self._preparar_completo()
        j = self._get('ambos')
        self.assertEqual(j['natureza_avaliacao']['retrospectivo'], 'retrospectiva_valores_atuais')
        self.assertFalse(j['resultado_por_modo']['retrospectivo']['validacao_temporal'])
        self.assertTrue(any('nao e validacao historica' in t.lower() for t in j['limitacoes']))

    def test_compatibilidade_das_chaves_existentes(self):
        self._preparar_completo()
        j = self._get('ambos')
        for chave in ('status', 'itens', 'historico_temporal', 'historico_temporal_estado',
                      'natureza_por_modo', 'legado', 'aviso_legado', 'natureza_avaliacao', 'resultado_por_modo',
                      'historico_curvas_estado', 'limitacoes', 'definicoes_estado'):
            self.assertIn(chave, j, chave)
        self.assertTrue(j['legado'])
        self.assertEqual(j['status'], 'OK')
        # valor cujo significado mudou: o legado continua disponivel
        self.assertEqual(j['historico_temporal_estado_legado'], 'ok')

    def test_leitura_do_endpoint_nao_promove_nem_escreve(self):
        """Ausencia de promocao operacional: o GET nao escreve em nenhuma tabela (nem ajustes nem sombra)."""
        self._preparar_completo()
        # ultimo_ajuste_ir() le modelo_ir_ajustes (nao e tabela): conta-se a tabela real e as de referencia/sombra
        existentes = {r[0] for r in db._exec("SELECT name FROM sqlite_master WHERE type = 'table'", (), 'all')}
        tabelas = [t for t in ('modelo_ir_ajustes', 'modelo_ir_p0_referencias', 'modelo_ir_confianca_tentativas')
                   if t in existentes]
        self.assertIn('modelo_ir_ajustes', tabelas)
        antes = {t: db._exec(f'SELECT COUNT(*) FROM {t}', (), 'one')[0] for t in tabelas}
        with mock.patch.object(db, '_exec', wraps=db._exec) as spy:
            self._get('ambos')
        escritas = [c.args[0] for c in spy.call_args_list
                    if c.args and str(c.args[0]).lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'DROP'))]
        self.assertEqual(escritas, [])
        depois = {t: db._exec(f'SELECT COUNT(*) FROM {t}', (), 'one')[0] for t in antes}
        self.assertEqual(depois, antes)

    def test_item_mantem_natureza_e_campos_de_contagem(self):
        self._preparar_completo()
        j = self._get('conhecido_na_epoca')
        for it in j['itens']:
            for campo in ('natureza', 'n_alvos_previstos', 'n_alvos_avaliaveis', 'n_alvos_rejeitados',
                          'rejeicoes_por_categoria', 'alvos_sem_previsao_ir', 'previsoes'):
                self.assertIn(campo, it, campo)


# ── y: revisao posterior nao altera a avaliacao anterior ──────────────────────────

class RevisaoFuturaDeY(W.CenarioTemporal):

    def test_revisao_de_y_posterior_ao_alvo_nao_altera_previsao_nem_y_do_alvo(self):
        self.preparar_historico()
        primeiro = min(e['data'] for e in self._efeitos)
        self.registar_marcos(dia_mais(primeiro, 60))
        self.registar_yes_dia_a_dia()
        antes = self.val()['itens'][0]
        self.assertGreater(len(antes['previsoes']), 3, 'cenario sem alvos suficientes')
        alvo = antes['previsoes'][len(antes['previsoes']) // 2]
        # nova versao do y deste alvo, DETETADA 30 dias depois do esforco (conhecida so depois do alvo)
        obs = [h for h in db.observacoes_auto_historico()
               if h['test_id'] == alvo['test_id'] and h['modalidade'] == 'Bike' and h['duracao_s'] == 1200]
        self.assertTrue(obs, 'alvo sem observacao historica')
        atual = max(obs, key=lambda h: h['versao'])
        db.inserir_observacao_auto(dict(atual, versao=atual['versao'] + 1, watts=float(atual['watts']) + 40.0,
                                        detetado_em=dia_mais(alvo['data'], 30) + 'T09:00:00',
                                        substitui_versao=atual['versao']))
        depois = self.val()['itens'][0]
        previsoes_antes = {p['test_id']: p for p in antes['previsoes']}
        previsoes_depois = {p['test_id']: p for p in depois['previsoes']}
        # o y do alvo e o conhecido ate ao dia do esforco: NUNCA o valor revisto (watts + 40).
        # Se o alvo continuar avaliado, o y e o antigo. Pode sair da avaliacao: a membership em
        # selecionar_observacoes_auto confere contra as curvas atuais (limitacao registada na Etapa 14).
        novo_y = float(atual['watts']) + 40.0
        if alvo['test_id'] in previsoes_depois:
            self.assertEqual(previsoes_depois[alvo['test_id']]['y'], previsoes_antes[alvo['test_id']]['y'])
        self.assertNotIn(novo_y, [p['y'] for p in depois['previsoes'] if p['test_id'] == alvo['test_id']])
        for tid, p in previsoes_antes.items():
            if p['data'] < alvo['data']:
                self.assertEqual(previsoes_depois.get(tid), p, tid)


if __name__ == '__main__':
    unittest.main()
