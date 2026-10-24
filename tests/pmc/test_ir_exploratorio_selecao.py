"""Selecao automatica do modo IR EXPLORATORIO: uma curva por modalidade, durações diferentes,
identificabilidade antes de N, limiar causal 0,92 inalterado, global so com duracao comum valida e
estados sem dados. Dados sinteticos controlados; nada e gravado nem lido de producao."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

import modelo_ir as ir  # noqa: E402
import modelo_ir_exploratorio as mex  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_observacoes_auto as moa  # noqa: E402
import modelo_testes as mt  # noqa: E402
import protocolo  # noqa: E402
import observacoes_auto as oa  # noqa: E402


def item(mod, dur, estado=ir.EST_IDENTIFICAVEL, r2=0.9, n=20, datas=15, dias=200, convergiu=True,
         avisos=(), curva=True, motivo=None):
    return {'modalidade': mod, 'duracao_s': dur, 'estado': estado, 'n_obs': n, 'n_datas_distintas': datas,
            'data_ini': '2026-01-01', 'data_fim': '2026-01-01' if dias == 0 else
            ('2026-07-20' if dias == 200 else '2026-03-01'),
            'metricas': {'r2': r2}, 'convergencia': {'convergiu': convergiu}, 'avisos': list(avisos),
            'curva': {'datas': []} if curva else None, 'motivo': motivo}


class SelecaoPorModalidade(unittest.TestCase):
    def test_duracoes_diferentes_por_modalidade(self):
        itens = [item('Bike', 180, r2=0.95), item('Bike', 300, r2=0.80),
                 item('Row', 300, r2=0.90), item('Row', 60, r2=0.70),
                 item('Ski', 60, estado=ir.EST_INSUFICIENTE, curva=False, motivo='menos de 15 obs')]
        sel = mex.escolher_todas(itens)
        self.assertEqual(sel['Bike']['selecionada']['duracao_s'], 180)
        self.assertEqual(sel['Row']['selecionada']['duracao_s'], 300)
        self.assertIsNone(sel['Ski']['selecionada'])

    def test_nao_escolhe_pelo_maior_n(self):
        itens = [item('Bike', 60, n=60, r2=0.50), item('Bike', 300, n=20, r2=0.92)]
        self.assertEqual(mex.escolher_curva(itens)['selecionada']['duracao_s'], 300)

    def test_identificabilidade_vem_antes_de_n(self):
        itens = [item('Bike', 60, n=80, estado=ir.EST_NAO_IDENTIFICAVEL, avisos=['parametro_no_limite:k1'],
                      r2=0.99),
                 item('Bike', 1200, n=16, r2=0.60)]
        r = mex.escolher_curva(itens)
        self.assertEqual(r['selecionada']['duracao_s'], 1200)
        self.assertEqual(r['estado'], 'identificavel')

    def test_desempate_por_cobertura_depois_qualidade(self):
        a = item('Bike', 180, r2=0.90, dias=200)
        b = item('Bike', 240, r2=0.90, dias=0)
        self.assertEqual(mex.escolher_curva([b, a])['selecionada']['duracao_s'], 180)


class IdentificabilidadeEstados(unittest.TestCase):
    def test_convergencia_falhada_nao_e_identificavel(self):
        self.assertIsNone(mex.escolher_curva([item('Bike', 300, convergiu=False)])['selecionada'])

    def test_aviso_de_identificabilidade_nao_e_identificavel(self):
        r = mex.escolher_curva([item('Bike', 300, avisos=['solucoes_equivalentes_divergentes'])])
        self.assertIsNone(r['selecionada'])
        self.assertEqual(r['estado'], mex.ESTADO_SEM_CURVA)
        self.assertIn('solucoes_equivalentes_divergentes', r['motivo'])

    def test_dados_existem_mas_ajuste_nao_identificavel_nao_desenha(self):
        r = mex.escolher_curva([item('Row', 300, estado=ir.EST_NAO_IDENTIFICAVEL, avisos=['parametro_no_limite:t2'])])
        self.assertIsNone(r['selecionada'])
        self.assertEqual(r['n_identificaveis'], 0)

    def test_dados_insuficientes_sem_curva(self):
        r = mex.escolher_curva([item('Ski', d, estado=ir.EST_INSUFICIENTE, curva=False) for d in (60, 300)])
        self.assertIsNone(r['selecionada'])
        self.assertEqual(r['estado'], 'dados_insuficientes')

    def test_sem_observacoes_e_sem_itens(self):
        self.assertEqual(mex.escolher_curva([])['estado'], 'sem_observacoes')
        self.assertIsNone(mex.escolher_curva([])['selecionada'])

    def test_sem_convergencia_e_causa(self):
        r = mex.escolher_curva([item('Run', 300, estado=ir.EST_SEM_CONVERGENCIA, curva=False)])
        self.assertEqual(r['estado'], 'sem_convergencia')


class DuracaoGlobal(unittest.TestCase):
    def test_duracao_comum_com_duas_modalidades_identificaveis(self):
        itens = [item('Bike', 300), item('Row', 300), item('Bike', 180), item('Row', 60, estado=ir.EST_NAO_IDENTIFICAVEL)]
        self.assertEqual(mex.escolher_duracao_global(itens), (300, None))

    def test_sem_duracao_comum_nao_ha_global(self):
        itens = [item('Bike', 180), item('Row', 300)]          # cada duracao so tem uma modalidade
        d, motivo = mex.escolher_duracao_global(itens)
        self.assertIsNone(d)
        self.assertIn('2 modalidades', motivo)

    def test_nenhuma_identificavel_nao_ha_global(self):
        d, motivo = mex.escolher_duracao_global([item('Bike', 300, estado=ir.EST_NAO_IDENTIFICAVEL)])
        self.assertIsNone(d)
        self.assertIn('nao ha duracao comum', motivo)


class LimiarCausal092(unittest.TestCase):
    """O detetor e o limiar 0,92 sao os EXISTENTES: testa-se so que as novas durações os usam."""

    def _curvas(self, secs, valores, tipo='Bike'):
        out = []
        for i, w in enumerate(valores):
            out.append({'activity_id': f'a{i}', 'type': tipo, 'date': f'2026-03-{i + 1:02d}',
                        'secs': [secs], 'watts': [float(w)]})
        return out

    def test_limiar_inalterado_e_aplicado_a_duracao_nova(self):
        self.assertEqual(protocolo.LIMIAR_ESFORCO, 0.92)
        self.assertEqual(moa.LIMIAR_PROXIMIDADE, 0.92)
        # 180 s (fora de 60/300/1200): 5 pontos anteriores a 1000 W, depois 920 W (92 %) e 910 W (91 %)
        curvas = self._curvas(180, [1000, 1000, 1000, 1000, 1000, 920, 910])
        cands = moa.detetar(curvas, testes={180: {}})
        pesos = sorted(c['watts'] for c in cands)
        self.assertIn(920.0, pesos)          # 92 % qualifica (>= 0,92)
        self.assertNotIn(910.0, pesos)       # 91 % nao qualifica
        self.assertTrue(all(c['duracao_s'] == 180 for c in cands))

    def test_duracoes_diferentes_no_mesmo_detetor(self):
        curvas = self._curvas(180, [1000] * 5 + [950])
        curvas2 = [{**c, 'secs': [180, 300], 'watts': [c['watts'][0], c['watts'][0] * 0.5]} for c in curvas]
        cands = moa.detetar(curvas2, testes={180: {}, 300: {}})
        # cada duracao e detetada na sua propria serie, com a mesma regra causal
        self.assertEqual({c['duracao_s'] for c in cands}, {180, 300})
        self.assertEqual(mt.DURACOES, (60, 300, 1200))            # o protocolo continua igual

    def test_mt_duracoes_e_operacional_nao_mudam(self):
        self.assertEqual(mt.DURACAO_MMP3, 180)
        self.assertEqual(mex.DURACOES_HISTORICAS, (60, 300, 1200))
        self.assertIn(180, mex.DURACOES_EXPLORATORIAS)
        self.assertEqual(sorted(mex.DURACOES_EXPLORATORIAS), list(mex.DURACOES_EXPLORATORIAS))


class CandidatosExploratorios(unittest.TestCase):
    def test_candidatos_so_para_as_duracoes_pedidas_e_sem_gravar(self):
        curvas = [{'activity_id': f'a{i}', 'type': 'Row', 'date': f'2026-02-{i + 1:02d}',
                   'secs': [180, 300], 'watts': [1000.0, 500.0]} for i in range(6)]
        with mock.patch.object(oa.db, 'inserir_observacao_auto', side_effect=AssertionError('nao grava')):
            cands = oa.candidatos_exploratorios(curvas, [], None, (180,))
        self.assertTrue(cands)
        self.assertEqual({c['duracao_s'] for c in cands}, {180})


class ServicoEstadosSemDados(unittest.TestCase):
    def test_sem_base_e_sem_dados_devolve_erro_sem_curva(self):
        with mock.patch.object(mis.db, 'ENABLED', False):
            r = mis.analise_exploratoria()
        self.assertEqual(r['status'], 'ERRO')
        self.assertNotIn('selecao_automatica', r)

    def test_candidatos_vazios_todas_modalidades_sem_curva_e_sem_global(self):
        r = mis.analise_exploratoria(duracoes=(300,), candidatos=[], atividades=[], curvas=[],
                                     referencia='2026-10-24', marcos=[])
        self.assertEqual(r['status'], 'OK')
        for m in mt.MODALIDADES:
            self.assertIsNone(r['selecao_automatica'][m]['selecionada'])
        self.assertEqual(r['global']['estado'], 'indisponivel')
        self.assertIsNone(r['global']['duracao_s'])
        self.assertFalse(any(it.get('curva') for it in r['itens']))

    def test_criterios_operacionais_nao_mudam(self):
        import modelo_ir as ir_op
        self.assertEqual(ir_op.CONFIG_PADRAO['min_obs'], 15)
        self.assertEqual(ir_op.CONFIG_PADRAO['min_datas_distintas'], 12)
        self.assertEqual(ir_op.CONFIG_PADRAO['min_dias_cobertura'], 180)
        self.assertEqual(ir_op.EST_IDENTIFICAVEL, 'ajustado_identificavel')
        self.assertNotIn(180, mex.DURACOES_HISTORICAS)           # o exploratorio nao altera as historicas

    def test_nunca_persiste_e_mantem_flag(self):
        r = mis.analise_exploratoria(duracoes=(300,), candidatos=[], atividades=[], curvas=[],
                                     referencia='2026-10-24', marcos=[])
        self.assertIs(r['persistido'], False)
        self.assertTrue(r['experimental'])


class GlobalUsaSelecaoSemReler(unittest.TestCase):
    def test_analise_global_com_selecao_nao_le_a_base(self):
        with mock.patch.object(mis.db, 'observacoes_auto_historico', side_effect=AssertionError('base')):
            with mock.patch.object(mis.db, 'ultimo_ajuste_ir', side_effect=AssertionError('base')):
                r = mis.analise_global(300, selecao={}, atividades=[])
        self.assertEqual(r['estado'], 'global_dados_insuficientes')


if __name__ == '__main__':
    unittest.main()
