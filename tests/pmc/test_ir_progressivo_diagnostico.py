"""Diagnostico do IR Exploratorio e do estado do solver (IR progressivo, etapa 8).

Cobre: convergencia exposta como o solver a devolve (2.1); exclusoes all_time tornadas visiveis sem alterar
`usadas` (2.2); requisitos em falta pelos gates reais (2.3); preservacao do ultimo ajuste identificavel (2.4).
Nao altera gates, limiares, solver nem estados: so diagnostico. Dados sinteticos; sem base de dados.
"""
import copy
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import modelo_ir as ir  # noqa: E402
import modelo_ir_exploratorio as mex  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import db  # noqa: E402


def _obs(n, datas=None, w=300.0):
    datas = datas or [f'2026-01-{(i % 28) + 1:02d}' for i in range(n)]
    return [{'test_id': f'Bike:300:x{i}', 'versao': 1, 'data': d, 'watts': w + i} for i, d in enumerate(datas)]


def _solver_fake(estado, convergiu=True):
    conv = {'convergiu': convergiu, 'status': 1 if convergiu else 0, 'mensagem': 'ok-fake', 'nfev': 42,
            'origem_melhor_partida': 'ponto_video'}
    return {'estado': estado, 'motivo': None,
            'parametros': {'p0': 300.0, 'k1': 1.0, 'k2': 1.0, 't1': 40.0, 't2': 7.0},
            'metricas': {'r2': 0.5, 'rmse': 2.0, 'sse': 9.0, 'mae': 1.5},
            'convergencia': conv,
            'identificabilidade': {'avisos': []},
            'serie_ini': None, 'p0_inicial': {'origem': 'livre'}}


class ConvergenciaExposta(unittest.TestCase):
    """2.1 -- o solver devolve 'convergencia' no nivel superior; o Exploratorio tem de o expor."""

    def test_01_bloco_do_solver_chega_ao_resultado_da_unidade(self):
        raw = _solver_fake(ir.EST_NAO_IDENTIFICAVEL, convergiu=True)
        with mock.patch.object(ir, 'ajustar_observacoes', return_value=copy.deepcopy(raw)):
            u = mex.ajustar_unidade('Bike', 300, _obs(20), {}, '2026-10-22', {'lag_dias': 1})
        esperado = {k: raw['convergencia'][k] for k in ('convergiu', 'status', 'mensagem', 'nfev',
                                                         'origem_melhor_partida')}
        self.assertEqual(u['convergencia'], esperado)                 # mesma informacao que o bruto
        self.assertIs(u['convergencia']['convergiu'], True)
        self.assertIs(u['metricas']['convergencia'], True)            # forma antiga preservada

    def test_02_convergencia_falsa_nao_vira_identificavel(self):
        raw = _solver_fake(ir.EST_SEM_CONVERGENCIA, convergiu=False)
        with mock.patch.object(ir, 'ajustar_observacoes', return_value=copy.deepcopy(raw)):
            u = mex.ajustar_unidade('Bike', 300, _obs(20), {}, '2026-10-22', {'lag_dias': 1})
        self.assertIs(u['convergencia']['convergiu'], False)
        self.assertIs(u['metricas']['convergencia'], False)
        self.assertNotEqual(u['estado'], ir.EST_IDENTIFICAVEL)

    def test_03_dados_insuficientes_sem_convergencia_inventada(self):
        u = mex.ajustar_unidade('Bike', 300, _obs(3), {}, '2026-10-22', {'lag_dias': 1})
        self.assertEqual(u['estado'], ir.EST_INSUFICIENTE)
        self.assertIsNone(u['convergencia'])
        self.assertIsNone(u['metricas']['convergencia'])

    def test_04_json_do_resultado_serializa_com_a_mesma_convergencia(self):
        raw = _solver_fake(ir.EST_IDENTIFICAVEL, convergiu=True)
        with mock.patch.object(ir, 'ajustar_observacoes', return_value=copy.deepcopy(raw)):
            u = mex.ajustar_unidade('Bike', 300, _obs(20), {}, '2026-10-22', {'lag_dias': 1})
        dump = json.loads(json.dumps(u))
        self.assertEqual(dump['convergencia'], u['convergencia'])
        self.assertEqual(dump['convergencia']['origem_melhor_partida'], raw['convergencia']['origem_melhor_partida'])


class ExclusoesAllTime(unittest.TestCase):
    """2.2 -- o filtro all_time continua igual em `usadas`; as exclusoes passam a aparecer com motivo."""

    def _sel_e_atuais(self):
        def ob(tid, tipo, w, ref_w, ref_id, pct):
            refs = {tipo: {'tipo': tipo, 'referencia_watts': ref_w, 'referencia_activity_id': ref_id,
                           'referencia_data': '2026-02-01', 'pct': pct, 'qualifica': True, 'n_pontos': 3},
                    'all_time': {'tipo': 'all_time', 'referencia_watts': 286.0,
                                 'referencia_activity_id': 'i2977223', 'referencia_data': '2022-01-03',
                                 'pct': 91.6, 'qualifica': False, 'n_pontos': 9}}
            return {'test_id': tid, 'modalidade': 'Ski', 'duracao_s': 300, 'activity_id': tid.split(':')[-1],
                    'data': '2026-02-20', 'watts': w, 'referencia_tipo': tipo, 'pct_da_referencia': pct,
                    'referencias': refs, 'versao': 1}
        a = ob('Ski:300:i126571067', 'season', 262.0, 206.0, 'i123125359', 127.2)
        b = ob('Ski:300:i999', 'all_time', 280.0, 286.0, 'i2977223', 97.9)
        c = ob('Ski:300:i888', 'recente', 250.0, 255.0, 'i777', 98.0)
        ex = {'test_id': 'Ski:300:i000', 'modalidade': 'Ski', 'duracao_s': 300, 'activity_id': 'i000',
              'data': '2026-01-01', 'watts': 120.0, 'referencia_tipo': None, 'motivo': 'sem_referencia_anterior'}
        sel = {('Ski', 300): {'n_confirmados': 0, 'n_detetadas': 4,
                              'usadas': [dict(a), dict(b), dict(c)],
                              'excluidas': [ex]}}
        return sel, [a, b, c]

    def test_05_usadas_igual_ao_filtro_anterior(self):
        sel, atuais = self._sel_e_atuais()
        antigo = [o for o in sel[('Ski', 300)]['usadas'] if o.get('referencia_tipo') == 'all_time']
        novo = mis.separar_nao_all_time(sel, atuais)[('Ski', 300)]['usadas']
        self.assertEqual(novo, antigo)                       # exatamente o mesmo conjunto e ordem

    def test_06_motivos_e_contagens_sem_dupla_contagem(self):
        sel, atuais = self._sel_e_atuais()
        out = mis.separar_nao_all_time(sel, atuais)
        excl = out[('Ski', 300)]['excluidas']
        motivos = sorted(e['motivo'] for e in excl)
        self.assertEqual(motivos, ['referencia_nao_all_time:recente', 'referencia_nao_all_time:season',
                                   'sem_referencia_anterior'])    # o motivo antigo fica intacto
        ids = [e['test_id'] for e in excl] + [o['test_id'] for o in out[('Ski', 300)]['usadas']]
        self.assertEqual(len(ids), len(set(ids)))              # nenhuma observacao em duas categorias
        res = mis.resumo_exclusoes(out)
        self.assertEqual(res['por_motivo'], {'referencia_nao_all_time:recente': 1,
                                             'referencia_nao_all_time:season': 1,
                                             'sem_referencia_anterior': 1})
        self.assertEqual(res['por_unidade']['Ski:300']['referencia_nao_all_time:season'], 1)

    def test_07_proveniencia_e_percentual_da_referencia_escolhida(self):
        sel, atuais = self._sel_e_atuais()
        excl = {e['test_id']: e for e in mis.separar_nao_all_time(sel, atuais)[('Ski', 300)]['excluidas']}
        e = excl['Ski:300:i126571067']
        self.assertEqual(e['referencia_watts'], 206.0)
        self.assertEqual(e['referencia_activity_id'], 'i123125359')
        self.assertEqual(e['pct_da_referencia'], 127.2)
        self.assertEqual(e['referencias']['all_time']['pct'], 91.6)   # a alternativa tambem fica visivel

    def test_08_analise_exploratoria_usa_o_mesmo_conjunto_e_mostra_exclusoes(self):
        sel, atuais = self._sel_e_atuais()
        with mock.patch.object(mis, 'selecionar', return_value=sel), \
                mock.patch.object(mis, 'carga_por_modalidade', return_value=({}, {})):
            res = mis.analise_exploratoria(duracoes=(300,), historico_obs=atuais, revisoes=[], atividades=[],
                                           curvas=[], referencia='2026-10-22')
        self.assertEqual(res['status'], 'OK')
        ski = next(i for i in res['itens'] if i['modalidade'] == 'Ski' and i['duracao_s'] == 300)
        self.assertEqual(ski['n_obs'], 1)                       # so a observacao all_time
        self.assertEqual(res['exclusoes']['por_motivo']['referencia_nao_all_time:season'], 1)


class RequisitosEmFalta(unittest.TestCase):
    """2.3 -- os tres gates reais, calculados independentemente, sem novos requisitos."""

    def setUp(self):
        self.cfg = ir._cfg(None)

    def test_09_gates_efetivos_e_faltas(self):
        self.assertEqual((self.cfg['min_obs'], self.cfg['min_datas_distintas'], self.cfg['min_dias_cobertura']),
                         (15, 12, 180))
        r = ir.requisitos({'n': 5, 'n_datas_distintas': 3, 'dias_cobertura': 40}, self.cfg)
        self.assertEqual(r['em_falta'], {'observacoes': 10, 'datas_distintas': 9, 'dias_cobertura': 140})
        self.assertFalse(r['gates_cumpridos'])

    def test_10_gates_cumpridos_nao_mostra_falta(self):
        r = ir.requisitos({'n': 20, 'n_datas_distintas': 12, 'dias_cobertura': 200}, self.cfg)
        self.assertEqual(r['em_falta'], {'observacoes': 0, 'datas_distintas': 0, 'dias_cobertura': 0})
        self.assertTrue(r['gates_cumpridos'])

    def test_11_resultado_insuficiente_do_solver_traz_os_requisitos(self):
        r = ir.ajustar_observacoes(_obs(5), {}, '2026-10-22', self.cfg, p0_fixo=None)
        self.assertEqual(r['estado'], ir.EST_INSUFICIENTE)
        self.assertEqual(r['requisitos']['em_falta']['observacoes'], 10)
        self.assertEqual(r['requisitos']['atuais']['observacoes'], 5)
        self.assertFalse(r['requisitos']['gates_cumpridos'])

    def test_12_estado_solver_expoe_os_requisitos(self):
        j = {'estado': ir.EST_INSUFICIENTE, 'motivo': 'menos_de_15_observacoes',
             'requisitos': ir.requisitos({'n': 5, 'n_datas_distintas': 3, 'dias_cobertura': 40}, self.cfg)}
        s = mis.estado_solver(j, None, 5, None)
        self.assertEqual(s['estado'], 'dados_insuficientes')
        self.assertEqual(s['requisitos']['em_falta']['datas_distintas'], 9)


class PreservacaoUltimoIdentificavel(unittest.TestCase):
    """2.4 -- a calibracao VALIDA e' sempre o ultimo ajuste IDENTIFICAVEL. Uma tentativa nao identificavel ou
    sem dados fica registada e visivel como `tentativa_mais_recente`, nunca substitui a referencia valida."""

    def _aj(self, v, estado):
        p = {'p0': 300.0, 'k1': 1.0, 'k2': 0.5, 't1': 40.0, 't2': 7.0}
        return {'versao_ajuste': v, 'estado': estado, 'ajustado_em': f'2026-10-{v:02d}', 'motivo': None,
                'metricas': {}, 'n_obs': 20, 'data_ini': '2026-01-01', 'data_fim': '2026-06-01',
                'funcao_objetivo': 'sse', 'rmse': 2.0, 'r2': 0.5, 'convergiu': True,
                'p0_origem': 'mmp3_ultimo_yes', 'identificabilidade': {'avisos': []}, 'versao_modelo': 1,
                'configuracao': None, 'entradas': {'observacoes': [{'data': '2026-01-01'}],
                                                    'p0_ref': {'versao_referencia': 1, 'assinatura': 'a'}},
                **p}

    def _resposta(self, estados, ordem_invertida=False):
        """curva_influencia sobre um historico de ajustes (o ultimo e' o mais recente)."""
        hist = [self._aj(i + 1, e) for i, e in enumerate(estados)]
        entrega = list(reversed(hist)) if ordem_invertida else hist
        with mock.patch.object(db, 'ultimo_ajuste_ir', return_value=hist[-1]), \
                mock.patch.object(db, 'ajustes_ir_historico', return_value=entrega), \
                mock.patch.object(db, 'ultima_referencia_p0', return_value=None):
            return mis.curva_influencia('Bike', 180)

    def test_13_nao_identificavel_depois_de_identificavel_nao_substitui_a_referencia(self):
        r = self._resposta([ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL])
        self.assertEqual(r['versao_ajuste'], 1)                          # a referencia continua a ser v1
        self.assertEqual(r['estado'], ir.EST_IDENTIFICAVEL)
        self.assertFalse(r['sem_calibracao_valida'])
        self.assertTrue(r['ajuste_apresentado_e_antigo'])
        self.assertEqual(r['tentativa_mais_recente']['versao_ajuste'], 2)
        self.assertEqual(r['tentativa_mais_recente']['estado'], ir.EST_NAO_IDENTIFICAVEL)

    def test_14_insuficiente_depois_de_identificavel_preserva_o_identificavel(self):
        r = self._resposta([ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL, ir.EST_INSUFICIENTE])
        self.assertEqual(r['versao_ajuste'], 1)
        self.assertEqual(r['ajuste_valido_anterior']['versao_ajuste'], 1)
        self.assertEqual(r['ajuste_valido_anterior']['estado'], ir.EST_IDENTIFICAVEL)
        self.assertEqual(r['tentativa_mais_recente']['versao_ajuste'], 3)
        self.assertEqual(r['tentativa_mais_recente']['estado'], ir.EST_INSUFICIENTE)

    def test_15_sequencia_completa_identificavel_nao_insuficiente_identificavel(self):
        """identificavel -> nao identificavel -> dados insuficientes -> identificavel. A referencia valida fica
        em v1 ate ser substituida pelo ultimo identificavel (v4); nada intermedio a substitui."""
        estados = [ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL, ir.EST_INSUFICIENTE, ir.EST_IDENTIFICAVEL]
        esperado = [(1, None), (1, 2), (1, 3), (4, None)]     # (referencia valida, tentativa mais recente)
        for n, (ref_v, tent_v) in enumerate(esperado, start=1):
            r = self._resposta(estados[:n])
            self.assertEqual(r['versao_ajuste'], ref_v, f'passo {n}')
            if tent_v is None:
                self.assertIsNone(r['tentativa_mais_recente'], f'passo {n}')
                self.assertFalse(r['ajuste_apresentado_e_antigo'], f'passo {n}')
            else:
                self.assertEqual(r['tentativa_mais_recente']['versao_ajuste'], tent_v, f'passo {n}')
                self.assertTrue(r['ajuste_apresentado_e_antigo'], f'passo {n}')
        self.assertEqual(r['ajuste_valido_anterior']['versao_ajuste'], 1)   # o identificavel anterior fica no historico

    def test_16_sem_nenhum_identificavel_diz_que_nao_ha_calibracao_valida(self):
        r = self._resposta([ir.EST_INSUFICIENTE])
        self.assertEqual(r['estado'], mis.EST_SEM_CALIBRACAO)
        self.assertIsNone(r['parametros'])
        self.assertIsNone(r['curvas'])
        self.assertEqual(r['tentativa_mais_recente']['estado'], ir.EST_INSUFICIENTE)
        self.assertEqual(mis.estado_ir(r, None), mis.EI_SEM_CALIBRACAO)
        self.assertEqual(mis._estado_conjunto(r), 'dados_insuficientes')

    def test_17_so_nao_identificavel_e_desenhado_marcado_como_nao_valido(self):
        r = self._resposta([ir.EST_NAO_IDENTIFICAVEL])
        self.assertEqual(r['versao_ajuste'], 1)
        self.assertTrue(r['sem_calibracao_valida'])
        self.assertIsNone(r['tentativa_mais_recente'])
        self.assertEqual(mis.estado_ir(r, None), mis.EI_NAO_IDENT)


class ListaExclusoes(unittest.TestCase):
    """As contagens e a lista de exclusoes do Exploratorio saem da MESMA fonte: nunca contam duas vezes."""

    def test_18_lista_e_contagens_saem_da_mesma_fonte(self):
        sel = {('Ski', 300): {'usadas': [], 'excluidas': [
            {'test_id': 'a', 'data': '2026-02-20', 'watts': 262.0, 'motivo': 'referencia_nao_all_time:season',
             'referencia_tipo': 'season', 'referencias': {'season': {'pct': 91.6, 'referencia_watts': 286.0}},
             'referencia_watts': 286.0, 'referencia_activity_id': 'i1', 'referencia_data': '2026-02-20'},
            {'test_id': 'b', 'data': '2026-01-01', 'watts': 200.0, 'motivo': 'sem_referencia_anterior'}]}}
        r = mis.resumo_exclusoes(sel)
        self.assertEqual(r['total'], 2)
        self.assertEqual(len(r['lista']), 2)
        self.assertEqual(sum(r['por_motivo'].values()), r['total'])
        self.assertEqual(sum(r['por_origem'].values()), r['total'])
        self.assertEqual(sum(sum(u.values()) for u in r['por_unidade'].values()), r['total'])
        a = next(x for x in r['lista'] if x['test_id'] == 'a')
        self.assertEqual((a['pct_da_referencia'], a['referencia_tipo'], a['referencia_activity_id']),
                         (91.6, 'season', 'i1'))
        b = next(x for x in r['lista'] if x['test_id'] == 'b')
        self.assertIsNone(b['referencia_activity_id'])        # ausente: None, nada inventado

    def test_19_sem_exclusoes_devolve_vazio(self):
        r = mis.resumo_exclusoes({})
        self.assertEqual((r['total'], r['lista'], r['por_motivo'], r['por_unidade']), (0, [], {}, {}))

class OrdemDoHistorico(PreservacaoUltimoIdentificavel):
    """A escolha da referencia nao depende da ordem em que o historico chega (versao_ajuste explicita)."""

    def test_20_ordem_de_entrada_nao_altera_o_resultado(self):
        estados = [ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL, ir.EST_INSUFICIENTE, ir.EST_IDENTIFICAVEL,
                   ir.EST_NAO_IDENTIFICAVEL]
        direto, invertido = self._resposta(estados), self._resposta(estados, ordem_invertida=True)
        def chave(r):
            t = r['tentativa_mais_recente'] or {}
            v = r['ajuste_valido_anterior'] or {}
            return (r['versao_ajuste'], r['estado'], t.get('versao_ajuste'), v.get('versao_ajuste'),
                    r['ajuste_apresentado_e_antigo'])
        self.assertEqual(chave(direto), chave(invertido))
        self.assertEqual(direto['versao_ajuste'], 4)                  # o ultimo identificavel
        self.assertEqual(direto['tentativa_mais_recente']['versao_ajuste'], 5)  # a ultima tentativa, a parte
        # Com tentativa, 'ajuste_valido_anterior' e' a calibracao valida que continua em vigor (v4), nao v1.
        self.assertEqual(direto['ajuste_valido_anterior']['versao_ajuste'], 4)
        self.assertNotEqual(direto['versao_ajuste'], 1)                       # v1 nunca e' a referencia


class EstadoPorTentativa(PreservacaoUltimoIdentificavel):
    """O estado do IR e o do painel distinguem calibracao valida, tentativa e falha real do solver."""

    def test_21_identificavel_seguido_de_nao_identificavel_mantem_a_calibracao_marcada(self):
        r = self._resposta([ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL])
        self.assertEqual(mis.estado_ir(r, None), mis.EI_DESATUALIZADO)   # v1 com P0 antigo; v2 nao o substitui
        self.assertFalse(r['sem_calibracao_valida'])

    def test_22_identificavel_seguido_de_dados_insuficientes(self):
        r = self._resposta([ir.EST_IDENTIFICAVEL, ir.EST_INSUFICIENTE])
        self.assertEqual(mis.estado_ir(r, None), mis.EI_DESATUALIZADO)
        self.assertEqual(r['tentativa_mais_recente']['estado'], ir.EST_INSUFICIENTE)
        self.assertNotEqual(mis.estado_ir(r, None), mis.EI_ERRO_SOLVER)   # dados insuficientes nao e' erro

    def test_23_so_dados_insuficientes_nao_tem_parametros_nem_erro_de_solver(self):
        r = self._resposta([ir.EST_INSUFICIENTE])
        self.assertEqual(mis.estado_ir(r, None), mis.EI_SEM_CALIBRACAO)
        self.assertNotEqual(mis.estado_ir(r, None), mis.EI_ERRO_SOLVER)
        self.assertIsNone(r['parametros'])
        self.assertIsNone(r['curvas'])
        self.assertEqual(r['leitura'].startswith('dados insuficientes'), True)   # o motivo continua visivel

    def test_24_so_nao_identificavel_e_marcado_como_nao_valido(self):
        r = self._resposta([ir.EST_NAO_IDENTIFICAVEL])
        self.assertTrue(r['sem_calibracao_valida'])
        self.assertEqual(mis.estado_ir(r, None), mis.EI_NAO_IDENT)

    def test_25_falha_real_do_solver_continua_distinta_de_dados_insuficientes(self):
        r = self._resposta([ir.EST_IDENTIFICAVEL, ir.EST_SEM_CONVERGENCIA])
        self.assertEqual(mis.estado_ir(r, None), mis.EI_ERRO_SOLVER)
        self.assertNotEqual(mis._estado_conjunto(r), 'dados_insuficientes')
        ins = self._resposta([ir.EST_IDENTIFICAVEL, ir.EST_INSUFICIENTE])
        self.assertNotEqual(mis.estado_ir(ins, None), mis.EI_ERRO_SOLVER)

    def test_26_estado_do_painel_corresponde_ao_estado_da_api(self):
        r = self._resposta([ir.EST_INSUFICIENTE])
        self.assertEqual(mis._estado_conjunto(r), 'dados_insuficientes')   # o painel le este, nao "valido"
        self.assertNotIn(mis._estado_conjunto(r), ('identificavel', 'nao_identificavel'))


class ExclusoesComOrigem(unittest.TestCase):
    """A origem de cada exclusao e explicita; o motivo preexistente e preservado; nada e' contado duas vezes."""

    def test_27_filtro_nao_apaga_nem_duplica_as_exclusoes_preexistentes(self):
        a = {'test_id': 'a', 'data': '2026-01-01', 'watts': 300.0, 'referencia_tipo': 'all_time'}
        o = {'test_id': 'o', 'data': '2026-02-01', 'watts': 250.0, 'referencia_tipo': 'season'}
        sel = {('Ski', 300): {'usadas': [a, o],
                              'excluidas': [{'test_id': 'z', 'data': '2025-01-01', 'watts': 100.0,
                                             'motivo': 'inelegivel:x'}]}}
        r = mis.separar_nao_all_time(sel, [])
        usadas = r[('Ski', 300)]['usadas']
        excl = r[('Ski', 300)]['excluidas']
        self.assertEqual([x['test_id'] for x in usadas], ['a'])        # usadas = filtro anterior (all_time)
        ids = [x['test_id'] for x in excl]
        self.assertEqual(sorted(ids), ['o', 'z'])                      # sem duplicados entre usadas/excluidas
        self.assertEqual(len(ids), len(set(ids)))
        motivos = {x['test_id']: x['motivo'] for x in excl}
        self.assertEqual(motivos['z'], 'inelegivel:x')                 # motivo preexistente preservado
        self.assertEqual(motivos['o'], 'referencia_nao_all_time:season')

    def test_28_origem_e_contagens_separadas_sem_misturar(self):
        sel = {('Ski', 300): {'usadas': [], 'excluidas': [
            {'test_id': 'o', 'motivo': 'referencia_nao_all_time:season', 'referencia_tipo': 'season'},
            {'test_id': 'z', 'motivo': 'inelegivel:x'}]}}
        r = mis.resumo_exclusoes(sel)
        self.assertEqual(r['por_origem'], {'filtro_all_time': 1, 'selecao_anterior': 1})
        self.assertEqual(r['total'], sum(r['por_origem'].values()))
        origens = {x['test_id']: x['origem'] for x in r['lista']}
        self.assertEqual(origens, {'o': 'filtro_all_time', 'z': 'selecao_anterior'})


if __name__ == '__main__':
    unittest.main()
