"""Etapa 18 - regressao dos dois defeitos confirmados pela auditoria (sem base de dados; funcoes puras e mocks).

D1 (null como zero): quando o historico nao foi lido (indisponivel ou bloqueado) as contagens de alvos sao
    desconhecidas e devem ser null, nunca 0. Quando a validacao correu, zero continua a ser um zero medido.
D2 (P0 de um ajuste inexistente): sem nenhum ajuste nao ha P0 usado nem desatualizacao; a origem e 'sem_ajuste'.
    Ajustes antigos (P0 livre, sem referencia) mantem 'p0_livre_anterior'.
"""
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_ir_painel_sombra import _item, _resposta  # noqa: E402  (so funcoes; as classes ficam la)
import modelo_ir_painel as mip  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_ir_p0 as p0m  # noqa: E402
import db  # noqa: E402

GLOBAL = {'estado': 'valida', 'n_yes': 2,
          'registo': {'test_id': 'Bike:180:i199567369', 'activity_id': 'i199567369', 'modalidade': 'Bike',
                      'data': '2026-10-01', 'inicio': '2026-10-01T10:00:00', 'watts': 346.0}}
REF_PERSISTIDA = {'modalidade': 'Row', 'duracao_s': 180, 'versao_referencia': 1, 'assinatura': 'SIG_ANTIGA',
                  'valor_p0': 340.0}


class P0SemAjuste(unittest.TestCase):
    """D2: estado do P0 de uma modalidade sem ajuste."""

    def _estado(self, aj_ref, tem_ajuste, desatualizado=False, ref=REF_PERSISTIDA):
        with mock.patch.object(db, 'ultima_referencia_p0', lambda t, d: ref if t == 'Row' else None):
            return mis._estado_p0('Row', 180, GLOBAL, aj_ref, desatualizado, tem_ajuste=tem_ajuste)

    def test_sem_ajuste_com_referencia_persistida_nao_fica_desatualizado(self):
        out = self._estado(None, tem_ajuste=False)
        self.assertEqual(out['origem_ajuste'], 'sem_ajuste')
        self.assertFalse(out['desatualizado'])
        self.assertIsNone(out['usado_no_ajuste'])
        # o P0 atual continua a ser apresentado (e o global de hoje), so nao e tratado como usado por um ajuste
        self.assertTrue(out['atual']['valida'])
        self.assertEqual(out['atual']['valor_p0'], 346.0)

    def test_sem_ajuste_e_sem_referencia(self):
        out = self._estado(None, tem_ajuste=False, ref=None)
        self.assertEqual(out['origem_ajuste'], 'sem_ajuste')
        self.assertFalse(out['desatualizado'])

    def test_legado_sem_tem_ajuste_mantem_p0_livre_anterior(self):
        """Chamadores antigos (tem_ajuste=None) mantem o comportamento anterior."""
        out = self._estado(None, tem_ajuste=None)
        self.assertEqual(out['origem_ajuste'], 'p0_livre_anterior')

    def test_ajuste_existente_com_p0_antigo_continua_desatualizado(self):
        """Regressao: com ajuste real e P0 diferente, desatualizado continua verdadeiro."""
        aj = {'formulacao': p0m.FORMULA_ID, 'assinatura': 'SIG_ANTIGA', 'valor_p0': 340.0}
        out = self._estado(aj, tem_ajuste=True)
        self.assertTrue(out['desatualizado'])
        self.assertEqual(out['origem_ajuste'], p0m.ORIGEM)

    def test_ajuste_com_p0_igual_ao_atual_nao_e_desatualizado(self):
        assinatura_atual = mis.p0m.resolver_p0('Row', 180, GLOBAL)['assinatura']
        aj = {'formulacao': p0m.FORMULA_ID, 'assinatura': assinatura_atual, 'valor_p0': 346.0}
        ref = dict(REF_PERSISTIDA, assinatura=assinatura_atual)
        out = self._estado(aj, tem_ajuste=True, ref=ref)
        self.assertFalse(out['desatualizado'])


class ContagensNaoAvaliadas(unittest.TestCase):
    """D1: null quando nada foi lido; zero medido quando a validacao correu."""

    def test_vista_indisponivel_tem_contagens_null(self):
        v = mip._vista_indisponivel('Bike', 1200, 'conhecido_na_epoca', 'falhou', 'pedido_falhou')
        self.assertEqual(v['contagens'], {'previstos': None, 'avaliaveis': None, 'rejeitados': None,
                                          'previsto_nao_avaliavel': None})

    def test_bloqueado_por_historico_insuficiente_nao_mostra_zero(self):
        """Espelha o caso A da AWS: historico indisponivel, bloqueado, nada avaliado."""
        resp = _resposta(itens=[], natureza='bloqueada_por_historico_insuficiente', hist_t='indisponivel',
                         hist_c='indisponivel', bloqueado=True, motivo='historico_temporal_indisponivel')
        v = mip.montar(resp, 'Bike', 1200, 'conhecido_na_epoca')
        self.assertTrue(v['bloqueado'])
        self.assertEqual(v['contagens']['previstos'], None)
        self.assertEqual(v['contagens']['avaliaveis'], None)
        self.assertEqual(v['contagens']['rejeitados'], None)

    def test_validacao_disponivel_com_zero_continua_a_ser_zero_medido(self):
        """Regressao: se a validacao correu e nao houve alvos, o zero e uma medicao, nao null."""
        it = _item(previstos=0, avaliaveis=0, previsoes=[], rejeitados=0)
        resp = _resposta(itens=[it], natureza='validada', hist_t='disponivel', hist_c='disponivel')
        v = mip.montar(resp, 'Bike', 1200, 'conhecido_na_epoca')
        self.assertEqual(v['contagens']['previstos'], 0)
        self.assertEqual(v['contagens']['avaliaveis'], 0)

    def test_frontend_nao_converte_null_em_zero(self):
        """Verificacao estatica do bloco do painel: a contagem usa a funcao que devolve '—' para null."""
        src = open(os.path.join(ROOT, 'tabs', 'tab_pmc.py'), encoding='utf-8').read()
        bloco = src[src.index('function somCabecalho'):src.index('function somOperacional')]
        self.assertNotIn('(cn.previstos||0)', bloco)
        self.assertNotIn('(cn.avaliaveis||0)', bloco)
        self.assertNotIn('(cn.rejeitados||0)', bloco)
        self.assertIn("x==null?'—'", bloco)
        self.assertRegex(bloco, r"cv\(cn\.previstos\)")


if __name__ == '__main__':
    unittest.main()
