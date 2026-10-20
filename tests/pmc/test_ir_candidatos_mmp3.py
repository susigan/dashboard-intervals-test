"""PR #42 — identificacao de candidatos MMP3 sem exigir tag de 3 min (regra 5).

    python -m pytest tests/pmc/test_ir_candidatos_mmp3.py

Offline, dados SINTETICOS. Sem Intervals.icu, sem base real. Cobre as areas de classificacao, comparacao com o P0,
dados insuficientes, dados futuros, alteracao retroativa e isolamento por modalidade. Sincronizacao e confirmacao
persistida ficam fora deste ficheiro (ver o relatorio: precisam de base temporaria propria).
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_ir_mmp3 as M  # noqa: E402
import modelo_mmp3 as mm  # noqa: E402

ELEG = M.ELEG


def linha(mod, vals, tags=None, ano='2025', pre='a', ate_dia=None):
    """Um 180 s por mes; tags: indices com tag 3min (None = todos)."""
    cv = [M.c180(f'{mod}-{pre}{i}', mod, f'{ano}-{(i % 12) + 1:02d}-10', w) for i, w in enumerate(vals)]
    tg = {f'{mod}-{pre}{i}': (['3min'] if tags is None or i in tags else []) for i in range(len(vals))}
    return cv, tg


def aval(cv, tg, mod='Bike', ate='2026-12-31'):
    return {e['activity_id']: e for e in mm.avaliar_esforcos(cv, mod, ate, (), ELEG, tags=tg)}


class ClassificacaoSemTag(unittest.TestCase):
    """Areas 1-4: sem tag nao ha YES automatico; MMP3 valido sem sinal fica A_VERIFICAR."""

    def test_1_com_tag_3min_e_yes(self):
        # 300 W com tag 3min = sinal de teste; referencia historica igual -> ELEGIVEL
        cv, tg = linha('Bike', [300.0] * 5 + [300.0], tags={5})
        e = aval(cv, tg)['Bike-a5']
        self.assertEqual(e['estado'], mm.E_ELEGIVEL)

    def test_2_sessao_longa_sem_tag_nunca_yes(self):
        # 5-10 min sem tag: o MMP3 (180 s) qualifica, mas nao ha sinal -> A_VERIFICAR, nunca YES
        cv, tg = linha('Bike', [300.0] * 5 + [330.0], tags=set())
        e = aval(cv, tg)['Bike-a5']
        self.assertEqual((e['estado'], e['a_verificar']), (mm.E_A_VERIFICAR, True))

    def test_3_repeticoes_sem_tag_nao_sao_yes(self):
        # varias repeticoes sem tag: todas candidatos, nenhuma YES automatica
        cv, tg = linha('Bike', [300.0] * 5 + [300.0, 301.0, 302.0], tags=set())
        r = aval(cv, tg)
        self.assertFalse(any(e['estado'] == mm.E_ELEGIVEL for e in r.values()))

    def test_4_mmp3_valido_sem_sinal_e_a_verificar(self):
        cv, tg = linha('Bike', [300.0] * 5 + [300.0], tags=set())
        e = aval(cv, tg)['Bike-a5']
        self.assertEqual((e['estado'], e['motivo']), (mm.E_A_VERIFICAR, mm.M_SEM_SINAL))


class ComparacaoP0(unittest.TestCase):
    """Areas 5-7: comparacao explicita com o P0 (abaixo / igualado / superado, W e %)."""

    def test_5_abaixo(self):
        c = mm.comparar_p0(270.0, 300.0)
        self.assertEqual((c['estado'], c['diferenca_w']), ('abaixo', -30.0))
        self.assertAlmostEqual(c['diferenca_pct'], -10.0)

    def test_6_igualado(self):
        c = mm.comparar_p0(300.0, 300.0)
        self.assertEqual((c['estado'], c['diferenca_w'], c['diferenca_pct']), ('igualado', 0.0, 0.0))

    def test_7_superado(self):
        c = mm.comparar_p0(330.0, 300.0)
        self.assertEqual(c['estado'], 'superado')
        self.assertAlmostEqual(c['diferenca_pct'], 10.0)

    def test_7b_sem_p0(self):
        self.assertEqual(mm.comparar_p0(300.0, None)['estado'], 'sem_p0')


class DadosInsuficientesEFuturos(unittest.TestCase):
    """Areas 8 e 10: dados insuficientes com motivo explicito; dados futuros excluidos."""

    def test_8_amostra_curta_e_insuficiente_nao_no(self):
        cv, tg = linha('Bike', [300.0] * 2, tags=set())
        e = aval(cv, tg)['Bike-a1']
        self.assertEqual(e['estado'], mm.E_INSUFICIENTE)

    def test_10_dados_posteriores_a_data_limite_sao_excluidos(self):
        cv, tg = linha('Bike', [300.0] * 5 + [330.0], tags={5})
        r = aval(cv, tg, ate='2025-06-01')          # a5 e de 2025-06-10: fora da janela
        self.assertNotIn('Bike-a5', r)


class RetroativoEModalidades(unittest.TestCase):
    """Areas 12 e 14: alteracao retroativa da potencia reflete-se na avaliacao; modalidades isoladas."""

    def test_12_alteracao_retroativa_reflete_se(self):
        cv, tg = linha('Bike', [300.0] * 5 + [330.0], tags={5})
        antes = aval(cv, tg)['Bike-a5']['estado']
        cv[5]['watts'][cv[5]['secs'].index(180)] = 250.0   # a potencia mudou na fonte
        depois = aval(cv, tg)['Bike-a5']['estado']
        self.assertEqual((antes, depois), (mm.E_ELEGIVEL, mm.E_NAO_ELEGIVEL))

    def test_14_modalidades_isoladas(self):
        cvb, tgb = linha('Bike', [300.0] * 5 + [330.0], tags={5})
        cvr, tgr = linha('Row', [200.0] * 6, tags=set(), pre='r')
        bike = mm.avaliar_esforcos(cvb + cvr, 'Bike', '2026-12-31', (), ELEG, tags={**tgb, **tgr})
        self.assertTrue(all(e['modalidade'] == 'Bike' or e.get('modalidade') in (None, 'Bike') for e in bike))
        self.assertTrue(all('Row' not in e['activity_id'] for e in bike))


if __name__ == '__main__':
    unittest.main()
