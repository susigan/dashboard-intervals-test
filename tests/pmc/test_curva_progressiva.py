"""Curva de desempenho progressiva (Etapa 28) - nucleo puro, sintetico e offline.

    python3 -m unittest tests.pmc.test_curva_progressiva -v

Cobre: grelhas de duracao diferentes por modalidade, modalidade ausente, novos treinos, valores suspeitos,
exclusoes com motivo, janela movel contra historico completo, empate, atualizacao repetida (idempotencia),
evolucao sem interpolacao e a separacao do IR (o modulo nao importa nenhum modulo do IR).
"""
import copy
import os
import sys
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import modelo_curva_progressiva as cp  # noqa: E402

ATE = '2026-10-24'


def curva(aid, mod, dia, pares):
    """pares: {duracao_s: watts}"""
    secs = sorted(pares)
    return {'activity_id': aid, 'type': mod, 'date': dia, 'weight': 70.0,
            'secs': secs, 'watts': [pares[s] for s in secs]}


def fixture():
    """Bike: grelha 60/300/1200. Row: so 60 e 1200 (grelha propria). Ski: ausente. Run: 300 apenas."""
    return [
        curva('b1', 'Bike', '2026-08-01', {60: 400, 300: 320, 1200: 250}),
        curva('b2', 'Bike', '2026-10-10', {60: 390, 300: 335, 1200: 240}),   # 300 s novo melhor, na janela
        curva('r1', 'Row', '2026-09-20', {60: 300, 1200: 200}),
        curva('u1', 'Run', '2026-09-01', {300: 300}),
    ]


class Grelhas(unittest.TestCase):
    def test_cada_modalidade_usa_so_as_suas_duracoes(self):
        r = cp.curva(fixture(), ATE)
        self.assertEqual(r['modalidades']['Bike']['duracoes_disponiveis'], [60, 300, 1200])
        self.assertEqual(r['modalidades']['Row']['duracoes_disponiveis'], [60, 1200])
        self.assertEqual(r['modalidades']['Run']['duracoes_disponiveis'], [300])

    def test_duracao_em_falta_fica_listada_e_nao_e_interpolada(self):
        r = cp.curva(fixture(), ATE, janela_dias=None)
        self.assertNotIn(300, [p['duracao_s'] for p in r['modalidades']['Row']['pontos']])
        self.assertEqual(r['modalidades']['Row']['em_falta'], [])  # Row nao tem 300 nas grelhas dela
        ev = cp.evolucao(fixture(), ATE, '2026-08-01', passo_dias=30, modalidades=['Row'])
        self.assertNotIn('300', ev['modalidades']['Row'])          # nem sequer aparece na serie

    def test_duracao_pedida_sem_valor_vai_para_em_falta(self):
        r = cp.curva(fixture(), ATE, duracoes=[60, 300, 1200], janela_dias=None)
        self.assertEqual(r['modalidades']['Row']['em_falta'], [])
        self.assertEqual(r['modalidades']['Bike']['em_falta'], [])
        r2 = cp.curva(fixture(), '2026-08-05', duracoes=[60, 300], janela_dias=2)
        self.assertIn(300, r2['modalidades']['Bike']['em_falta'])   # so ha 60 s nessa janela

    def test_modalidade_ausente_nao_tem_pontos_nem_quebra(self):
        r = cp.curva(fixture(), ATE)
        self.assertEqual(r['modalidades']['Ski'], {'estado': 'sem_dados', 'duracoes_disponiveis': [],
                                                   'pontos': [], 'em_falta': [],
                                                   'n_valores_usados': 0, 'n_valores_excluidos': 0})

    def test_modalidade_fora_do_conjunto_e_ignorada(self):
        dados = fixture() + [curva('o1', 'Other', '2026-09-01', {60: 999})]
        r = cp.curva(dados, ATE)
        self.assertNotIn('Other', r['modalidades'])
        self.assertEqual(r['resumo']['n_outra_modalidade'], 1)


class Envelope(unittest.TestCase):
    def test_janela_42_dias_inclui_novo_treino_e_exclui_o_antigo(self):
        r = cp.curva(fixture(), ATE, janela_dias=42)
        bike = {p['duracao_s']: p for p in r['modalidades']['Bike']['pontos']}
        self.assertEqual(bike[300]['watts'], 335)             # b2 (10-10) dentro de 42 dias
        self.assertEqual(bike[300]['activity_id'], 'b2')
        self.assertEqual(bike[60]['watts'], 390)              # b1 400 W ha 84 dias: fora da janela
        self.assertEqual(bike[60]['activity_id'], 'b2')

    def test_historico_completo_mantem_o_recorde_antigo(self):
        r = cp.curva(fixture(), ATE, janela_dias=None)
        bike = {p['duracao_s']: p for p in r['modalidades']['Bike']['pontos']}
        self.assertEqual(bike[60]['watts'], 400)
        self.assertEqual(bike[60]['activity_id'], 'b1')
        self.assertEqual(r['serie'], 'envelope_historico_completo')

    def test_janela_limite_exato(self):
        # janela de 42 dias termina em ate: 2026-09-12 cai na janela (ate - 42 dias = 2026-09-12, exclusivo)
        d = [curva('x1', 'Bike', '2026-09-12', {60: 500}), curva('x2', 'Bike', '2026-09-13', {60: 480})]
        r = cp.curva(d, ATE, janela_dias=42)
        self.assertEqual(r['modalidades']['Bike']['pontos'][0]['activity_id'], 'x2')

    def test_empate_fica_o_mais_antigo(self):
        d = [curva('e2', 'Bike', '2026-10-02', {60: 400}), curva('e1', 'Bike', '2026-10-01', {60: 400})]
        r = cp.curva(d, ATE, janela_dias=42)
        self.assertEqual(r['modalidades']['Bike']['pontos'][0]['activity_id'], 'e1')

    def test_treino_futuro_nao_entra(self):
        d = fixture() + [curva('f1', 'Bike', '2026-11-01', {60: 900})]
        self.assertEqual(cp.curva(d, ATE)['modalidades']['Bike']['pontos'][0]['watts'], 390)

    def test_valores_zero_nulos_e_negativos_nao_sao_pontos(self):
        d = [curva('z1', 'Bike', '2026-10-01', {60: 0, 300: None, 1200: -5, 1: 800})]
        r = cp.curva(d, ATE)
        self.assertEqual([p['duracao_s'] for p in r['modalidades']['Bike']['pontos']], [1])
        self.assertEqual(r['resumo']['n_valores_em_falta'], 3)


class Qualidade(unittest.TestCase):
    def test_monotonia_sinaliza_e_mantem_por_defeito(self):
        d = [curva('m1', 'Bike', '2026-10-01', {60: 300, 300: 350})]      # 300 s acima de 60 s: impossivel
        r = cp.curva(d, ATE)
        self.assertEqual(len(r['suspeitos']), 1)
        self.assertEqual(r['suspeitos'][0]['duracao_s'], 300)
        pt = {p['duracao_s']: p for p in r['modalidades']['Bike']['pontos']}
        self.assertEqual(pt[300]['watts'], 350)                                   # continua no envelope
        self.assertEqual(r['resumo']['n_suspeitos'], 1)

    def test_monotonia_excluir_suspeitos_mostra_motivo(self):
        d = [curva('m1', 'Bike', '2026-10-01', {60: 300, 300: 350})]
        r = cp.curva(d, ATE, excluir_suspeitos=True)
        self.assertEqual([p['duracao_s'] for p in r['modalidades']['Bike']['pontos']], [60])
        self.assertEqual(r['exclusoes'][0]['motivo'], cp.MOTIVO_SUSPEITO)

    def test_tolerancia_de_arredondamento_nao_sinaliza(self):
        d = [curva('m2', 'Bike', '2026-10-01', {60: 300, 300: 300.3})]
        self.assertEqual(cp.curva(d, ATE)['suspeitos'], [])

    def test_inelegivel_e_desconhecida_sao_excluidas_com_motivo(self):
        d = [curva('i1', 'Bike', '2026-10-01', {60: 500}), curva('k1', 'Bike', '2026-10-02', {60: 450})]
        est = {'i1': ('inelegivel', None), 'k1': ('desconhecida', 'ignore_parts_nao_associavel_ao_trecho')}
        r = cp.curva(d, ATE, estados=est)
        self.assertEqual(r['modalidades']['Bike']['pontos'], [])
        motivos = {x['activity_id']: x['motivo'] for x in r['exclusoes']}
        self.assertEqual(motivos['i1'], cp.MOTIVO_IGNORADA)
        self.assertEqual(motivos['k1'], 'elegibilidade_desconhecida:ignore_parts_nao_associavel_ao_trecho')
        self.assertEqual(r['modalidades']['Bike']['n_valores_excluidos'], 2)

    def test_elegivel_passa(self):
        d = [curva('e1', 'Bike', '2026-10-01', {60: 500})]
        r = cp.curva(d, ATE, estados={'e1': ('elegivel', None)})
        self.assertEqual(r['modalidades']['Bike']['pontos'][0]['watts'], 500)
        self.assertEqual(r['exclusoes'], [])


class Evolucao(unittest.TestCase):
    def test_serie_aumenta_quando_chega_treino_novo(self):
        ev = cp.evolucao(fixture(), ATE, '2026-08-01', passo_dias=1, modalidades=['Bike'],
                         duracoes=[300], janela_dias=None)
        serie = ev['modalidades']['Bike']['300']
        self.assertEqual(serie[ev['as_of'].index('2026-09-30')], 320)
        self.assertEqual(serie[ev['as_of'].index('2026-10-10')], 335)

    def test_janela_faz_valor_cair_quando_sai_da_janela(self):
        ev = cp.evolucao(fixture(), ATE, '2026-08-01', passo_dias=1, modalidades=['Bike'],
                         duracoes=[60], janela_dias=42)
        serie = ev['modalidades']['Bike']['60']
        self.assertEqual(serie[ev['as_of'].index('2026-09-01')], 400)     # b1 (08-01) ainda na janela
        self.assertIsNone(serie[ev['as_of'].index('2026-09-20')])         # b1 saiu, b2 ainda nao existe
        self.assertEqual(serie[ev['as_of'].index('2026-10-10')], 390)     # so b2 na janela

    def test_sem_valor_e_none_nunca_interpolado(self):
        ev = cp.evolucao(fixture(), ATE, '2026-08-01', passo_dias=1, modalidades=['Run'], janela_dias=42)
        serie = ev['modalidades']['Run']['300']
        self.assertIsNone(serie[ev['as_of'].index('2026-08-15')])      # antes de existir a atividade
        self.assertEqual(serie[ev['as_of'].index('2026-09-01')], 300)

    def test_intervalo_invalido_devolve_erro(self):
        self.assertIn('erro', cp.evolucao(fixture(), ATE, '2026-12-01'))
        self.assertIn('erro', cp.evolucao(fixture(), ATE, '2026-08-01', passo_dias=0))


class Repeticao(unittest.TestCase):
    def test_calculo_repetido_e_identico_e_nao_altera_entrada(self):
        dados = fixture()
        antes = copy.deepcopy(dados)
        a = cp.curva(dados, ATE, janela_dias=42)
        b = cp.curva(dados, ATE, janela_dias=42)
        self.assertEqual(a, b)
        self.assertEqual(dados, antes)

    def test_sincronizacao_repetida_sem_novos_dados_nao_muda_nada(self):
        a = cp.evolucao(fixture(), ATE, '2026-08-01', passo_dias=7)
        b = cp.evolucao(list(fixture()), ATE, '2026-08-01', passo_dias=7)
        self.assertEqual(a, b)


class Apresentacao(unittest.TestCase):
    def test_grafico_conjunto_separa_series_e_rotula_origem(self):
        r = cp.curva(fixture(), ATE)
        self.assertEqual(r['origem'], cp.ORIGEM)
        self.assertEqual(r['estimativas_ajustadas'], cp.ESTIMATIVAS_AJUSTADAS)
        for mod, bloco in r['modalidades'].items():
            for p in bloco['pontos']:
                self.assertEqual(p['origem'], cp.ORIGEM)
                self.assertTrue(p['serie'].startswith('envelope_'))
                self.assertIn('data', p)
                self.assertIn('activity_id', p)

    def test_parcial_sem_modalidades_nao_quebra(self):
        r = cp.curva([], ATE)
        self.assertEqual({m: b['estado'] for m, b in r['modalidades'].items()},
                         {'Bike': 'sem_dados', 'Row': 'sem_dados', 'Ski': 'sem_dados', 'Run': 'sem_dados'})
        self.assertEqual(cp.evolucao([], ATE, '2026-08-01')['modalidades']['Bike'], {})


class IsolamentoDoIR(unittest.TestCase):
    def test_modulo_nao_importa_nada_do_ir_nem_da_base(self):
        import ast
        with open(cp.__file__, encoding='utf-8') as fh:
            arvore = ast.parse(fh.read())
        importados = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados.update(a.name.split('.')[0] for a in no.names)
            elif isinstance(no, ast.ImportFrom) and no.module:
                importados.add(no.module.split('.')[0])
        proibidos = {'db', 'modelo_ir', 'modelo_ir_servico', 'modelo_mmp3', 'modelo_estimador', 'app', 'api_cp'}
        self.assertEqual(importados & proibidos, set())
        self.assertLessEqual(importados, {'bisect', 'math', 'datetime'})


if __name__ == '__main__':
    unittest.main()
