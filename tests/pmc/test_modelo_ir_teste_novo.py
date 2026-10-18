"""Fluxo "teste novo": uma observacao nova entra no IR na sua data real, sem tocar no CP confirmado nem no P0.

    python -m pytest tests/pmc/test_modelo_ir_teste_novo.py

Sem producao: base temporaria e base de perfil temporaria (as da cadeia CP -> P0 -> IR). So audita o que ja existe;
nao cria nenhuma ponte teste -> CP Model.
"""
import copy
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import modelo_ir_servico as mis  # noqa: E402
import test_modelo_ir_p0_integracao as I  # noqa: E402
import test_modelo_ir_servico as S  # noqa: E402

CENARIO_ORIGINAL = S.cenario
db = S.db


class TesteNovo(I._Cadeia):
    def setUp(self):
        curvas, ativ = CENARIO_ORIGINAL()
        self.nova_curva = copy.deepcopy(curvas[-1])
        self.nova_ativ = [copy.deepcopy(a) for a in ativ if a['id'] == self.nova_curva['activity_id']]
        parcial = ([c for c in curvas if c['activity_id'] != self.nova_curva['activity_id']],
                   [a for a in ativ if a['id'] != self.nova_curva['activity_id']])
        p = mock.patch.object(S, 'cenario', side_effect=lambda *a, **k: parcial)
        p.start()
        self.addCleanup(p.stop)
        super().setUp()

    def _chega_o_teste_novo(self):
        self.curvs.append(copy.deepcopy(self.nova_curva))
        self.ativ.extend(copy.deepcopy(self.nova_ativ))
        self.sync()
        r = self.atualiza()
        self.assertTrue(r['ok'], r)
        return mis.apos_deteccao(r, origem='sync')

    def test_o_teste_novo_entra_na_data_real_e_nao_toca_no_cp_nem_no_p0(self):
        self.prepara()
        self.guardar()
        cp_antes, refs_antes, aj_antes = self.cp_linhas(), copy.deepcopy(self.refs()), copy.deepcopy(self.aj())
        n_antes = aj_antes[-1]['n_obs']
        data_real = self.nova_curva['date'][:10]
        with mock.patch.object(self.cpm, 'calcular_cp_completo', side_effect=AssertionError('o IR nao recalcula o CP')):
            out = self._chega_o_teste_novo()
        self.assertTrue(out['ok'], out)
        self.assertEqual(self.cp_linhas(), cp_antes)                                  # CP confirmado intacto
        self.assertEqual(self.refs(), refs_antes)                                     # nenhuma referencia de P0 nova
        aj = self.aj()
        self.assertEqual(len(aj), len(aj_antes) + 1)                                  # nova versao de ajuste
        self.assertEqual(aj[:len(aj_antes)], aj_antes)                                # as anteriores nao mudam
        novo = aj[-1]
        self.assertEqual(novo['n_obs'], n_antes + 1)
        self.assertEqual(str(novo['data_fim'])[:10], data_real)                       # data real, sem deslocamento
        self.assertEqual(novo['p0'], 270.0)                                           # P0 == CP, exatamente
        self.assertEqual(novo['entradas']['p0_ref']['versao_referencia'],
                         aj_antes[-1]['entradas']['p0_ref']['versao_referencia'])
        datas = [str(o['data'])[:10] for o in novo['entradas']['observacoes']]
        self.assertEqual(datas.count(data_real), 1)
        self.assertEqual(max(datas), data_real)

    def test_o_ajuste_anterior_nao_usa_o_teste_futuro(self):
        self.prepara()
        self.guardar()
        (l,) = self.aj()
        datas = [str(o['data'])[:10] for o in l['entradas']['observacoes']]
        self.assertLess(max(datas), self.nova_curva['date'][:10])


if __name__ == '__main__':
    unittest.main()
