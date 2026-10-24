"""Etapa 17: produtor prospetivo D+1 da sombra e associacao posterior dos resultados.

DADOS SINTETICOS (tests/pmc/ir_sintetico.py) e registos em SQLite temporario. Nada aqui e resultado real.
Cobre: calculo D+1 com entradas conhecidas, exclusao de informacao futura, idempotencia, imutabilidade, associacao
inequivoca e rejeicao de ambiguas, revisao em versao nova, falha isolada do produtor no sync, ausencia de escrita
operacional, estado do painel e a rota de leitura.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

import ir_sintetico as sint  # noqa: E402
import modelo_ir as ir  # noqa: E402
import modelo_ir_previsoes as mp  # noqa: E402
import modelo_ir_produtor_previsoes as prod  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402

AGORA = datetime(2025, 7, 25, 10, 0, 0)
P0 = 600.0


def _cenario():
    carga, _sel, atuais, _cfg = sint.cenario(semente=3)
    obs = [{'test_id': o['test_id'], 'data': o['data'], 'watts': o['watts']} for o in atuais]
    return carga, obs


class ProdutorPuro(unittest.TestCase):
    def setUp(self):
        self.carga, self.obs = _cenario()

    def test_previsao_d1_com_entradas_conhecidas(self):
        r = prod.calcular_previsao_d1(AGORA, 'Bike', self.obs, self.carga, P0)
        self.assertEqual(r['estado_temporal'], 'disponivel')
        self.assertEqual(r['data_alvo'], '2025-07-26')            # posterior ao dia do calculo
        self.assertGreaterEqual(r['lag_dias'], 1)
        self.assertIsInstance(r['previsao_ir'], float)
        self.assertIn('parametros', r['parametros'])
        self.assertEqual(r['parametros']['p0'], P0)
        self.assertTrue(r['entradas_sig'])                          # assinatura das entradas registada

    def test_exclui_observacoes_e_cargas_futuras(self):
        base = prod.calcular_previsao_d1(AGORA, 'Bike', self.obs, self.carga, P0)
        futuro_obs = self.obs + [{'test_id': 'Bike:180:futuro', 'data': '2025-08-10', 'watts': 9999.0}]
        futuro_carga = {**self.carga, '2025-08-01': 9e6, '2025-07-26': 9e6}
        com_futuro = prod.calcular_previsao_d1(AGORA, 'Bike', futuro_obs, futuro_carga, P0)
        self.assertEqual(com_futuro['previsao_ir'], base['previsao_ir'])
        self.assertEqual(com_futuro['parametros']['n_observacoes'], base['parametros']['n_observacoes'])
        self.assertEqual(com_futuro['entradas_sig'], base['entradas_sig'])

    def test_lag_invalido_bloqueia(self):
        r = prod.calcular_previsao_d1(AGORA, 'Bike', self.obs, self.carga, P0, cfg={'lag_dias': 0})
        self.assertEqual(r['motivo_bloqueio'], 'lag_invalido')
        self.assertIsNone(r['previsao_ir'])

    def test_sem_observacoes_e_vazio(self):
        r = prod.calcular_previsao_d1(AGORA, 'Bike', [], self.carga, P0)
        self.assertEqual((r['estado_temporal'], r['motivo_bloqueio']), ('vazio', prod.MOTIVO_SEM_OBS))

    def test_sem_p0_valido_bloqueia_com_motivo_explicito(self):
        r = prod.calcular_previsao_d1(AGORA, 'Bike', self.obs, self.carga, None, p0_motivo='sem_yes_valido')
        self.assertEqual((r['estado_temporal'], r['motivo_bloqueio']), ('indisponivel', 'sem_yes_valido'))

    def test_mesmas_entradas_mesma_assinatura_e_entradas_alteradas_mudam(self):
        a = prod.calcular_previsao_d1(AGORA, 'Bike', self.obs, self.carga, P0)
        b = prod.calcular_previsao_d1(AGORA + timedelta(hours=3), 'Bike', self.obs, self.carga, P0)
        self.assertEqual(a['entradas_sig'], b['entradas_sig'])     # mesmo dia, mesmas entradas: idempotente
        alterada = [dict(o) for o in self.obs]
        alterada[-1]['watts'] = alterada[-1]['watts'] + 25.0
        c = prod.calcular_previsao_d1(AGORA, 'Bike', alterada, self.carga, P0)
        self.assertNotEqual(a['entradas_sig'], c['entradas_sig'])

    def test_carga_do_dia_aberto_fica_marcada(self):
        carga = {**self.carga, '2025-07-25': 80.0}
        r = prod.calcular_previsao_d1(AGORA, 'Bike', self.obs, carga, P0)
        self.assertTrue(r['parametros']['carga_dia_aberto'])


class _ComRegisto(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.loja = mp.loja_sqlite(os.path.join(self.dir, 'r.db'))
        self.loja.criar()
        self.log = []
        inner = self.loja._exec

        def espiao(sql, params, fetch):
            self.log.append(sql)
            return inner(sql, params, fetch)
        self.loja._exec = espiao


class Registo(_ComRegisto):
    def setUp(self):
        super().setUp()
        self.carga, self.obs = _cenario()

    def _entradas(self, obs=None, carga=None, p0=P0):
        base = {m: {'obs': [], 'cargas': {}, 'p0_valor': None, 'p0_motivo': 'sem_yes_valido'} for m in
                ('Bike', 'Row', 'Ski', 'Run')}
        base['Bike'] = {'obs': obs if obs is not None else self.obs, 'cargas': carga or self.carga,
                        'p0_valor': p0, 'p0_motivo': None}
        return base

    def test_produzir_regista_e_e_idempotente(self):
        a = prod.produzir(self.loja, AGORA, self._entradas())
        b = prod.produzir(self.loja, AGORA + timedelta(hours=1), self._entradas())
        bike_a = next(x for x in a if x['modalidade'] == 'Bike')
        bike_b = next(x for x in b if x['modalidade'] == 'Bike')
        self.assertEqual((bike_a['acao'], bike_b['acao']), ('registada', 'ja_registada'))
        self.assertEqual(len(self.loja.todas()), 4)                  # uma por modalidade, sem duplicar a Bike

    def test_modalidades_sem_entradas_ficam_bloqueadas_explicitas(self):
        out = prod.produzir(self.loja, AGORA, self._entradas())
        row_row = next(x for x in out if x['modalidade'] == 'Row')
        self.assertEqual((row_row['estado'], row_row['motivo_bloqueio']), ('vazio', prod.MOTIVO_SEM_OBS))
        self.assertTrue(row_row['acao'] in ('registada', 'ja_registada'))

    def test_nova_entrada_gera_linha_e_a_antiga_fica_imutavel(self):
        prod.produzir(self.loja, AGORA, self._entradas())
        antes = [p for p in self.loja.todas() if p['modalidade'] == 'Bike'][0]
        alterada = [dict(o) for o in self.obs]
        alterada[-1]['watts'] += 40.0
        prod.produzir(self.loja, AGORA + timedelta(days=1), self._entradas(obs=alterada,
                                                                            carga=self.carga))
        bikes = [p for p in self.loja.todas() if p['modalidade'] == 'Bike']
        self.assertEqual(len(bikes), 2)
        depois = self.loja.previsao(antes['previsao_id'])
        self.assertEqual(depois, antes)                             # a previsao original nao mudou

    def test_escritas_so_nas_tabelas_do_registo(self):
        prod.produzir(self.loja, AGORA, self._entradas())
        self.assertTrue(self.log)
        for sql in self.log:
            low = sql.lower()
            self.assertNotIn('modelo_ir_ajustes', low)
            self.assertNotIn('ultimo_ajuste_ir', low)
            self.assertNotIn('referencia', low)                     # nunca grava referencia de P0

    def test_produtor_nao_chama_escrita_de_referencia_p0(self):
        with mock.patch.object(mis, 'garantir_referencia_p0', side_effect=AssertionError('nao deve ser chamada')):
            prod.produzir(self.loja, AGORA, self._entradas())
        import ast
        arvore = ast.parse(open(os.path.join(ROOT, 'modelo_ir_produtor_previsoes.py'), encoding='utf-8').read())
        nomes = {n.id for n in ast.walk(arvore) if isinstance(n, ast.Name)} | \
                {n.attr for n in ast.walk(arvore) if isinstance(n, ast.Attribute)} | \
                {n.value for n in ast.walk(arvore) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertFalse(nomes & {'garantir_referencia_p0', 'ultimo_ajuste_ir'}, nomes & {'garantir_referencia_p0'})


def _obs(tid, data, watts, detetado_em, mod='Bike', dur=180, versao=1, evento='detetada'):
    return {'test_id': tid, 'versao': versao, 'evento': evento, 'modalidade': mod, 'duracao_s': dur, 'data': data,
            'watts': watts, 'detetado_em': detetado_em}


class Associacao(_ComRegisto):
    def setUp(self):
        super().setUp()
        self.calc = '2025-07-25T10:00:00'
        self.pid = self._previsao('2025-07-26')

    def _previsao(self, alvo, mod='Bike', dur=180, sig='s1'):
        r = mp.registar_previsao(self.loja, calculado_em=self.calc, data_alvo=alvo, modalidade=mod, duracao_s=dur,
                                 versao_modelo='ir-v1', lag_dias=1, entradas_sig=sig, estado_temporal='disponivel',
                                 previsao_ir=300.0, parametros={'p0': 300.0})
        return r['previsao_id']

    def test_associacao_unica_e_inequivoca(self):
        h = [_obs('Bike:180:a1', '2025-07-26', 305.0, '2025-07-26T18:00:00')]
        out = prod.associar_pendentes(self.loja, h, datetime(2025, 7, 27, 9))
        self.assertEqual(out[0]['acao'], 'registado')
        vs = self.loja.versoes(self.pid)
        self.assertEqual((len(vs), vs[0]['test_id'], vs[0]['observado_em']),
                         (1, 'Bike:180:a1', '2025-07-26T18:00:00.000000'))

    def test_sem_resultado_nao_associa(self):
        out = prod.associar_pendentes(self.loja, [], datetime(2025, 7, 27, 9))
        self.assertEqual(out[0]['acao'], 'sem_resultado')
        self.assertEqual(self.loja.versoes(self.pid), [])

    def test_dois_testes_no_mesmo_dia_sao_ambiguos_e_nada_se_associa(self):
        h = [_obs('Bike:180:a1', '2025-07-26', 305.0, '2025-07-26T18:00:00'),
             _obs('Bike:180:a2', '2025-07-26', 310.0, '2025-07-26T19:00:00')]
        out = prod.associar_pendentes(self.loja, h, datetime(2025, 7, 27, 9))
        self.assertEqual(out[0]['acao'], 'ambiguo')
        self.assertEqual(self.loja.versoes(self.pid), [])

    def test_outra_modalidade_ou_duracao_nao_se_associa(self):
        h = [_obs('Row:180:r1', '2025-07-26', 200.0, '2025-07-26T18:00:00', mod='Row'),
             _obs('Bike:300:b5', '2025-07-26', 320.0, '2025-07-26T18:00:00', dur=300)]
        out = prod.associar_pendentes(self.loja, h, datetime(2025, 7, 27, 9))
        self.assertEqual(out[0]['acao'], 'sem_resultado')

    def test_outro_dia_nao_se_associa(self):
        h = [_obs('Bike:180:a9', '2025-07-27', 305.0, '2025-07-27T18:00:00')]
        out = prod.associar_pendentes(self.loja, h, datetime(2025, 7, 28, 9))
        self.assertEqual(out[0]['acao'], 'sem_resultado')

    def test_resultado_conhecido_antes_do_calculo_e_rejeitado(self):
        h = [_obs('Bike:180:a1', '2025-07-26', 305.0, '2025-07-25T08:00:00')]
        out = prod.associar_pendentes(self.loja, h, datetime(2025, 7, 27, 9))
        self.assertEqual((out[0]['acao'], out[0]['motivo']), ('rejeitado', 'resultado_anterior_a_previsao'))

    def test_observacao_sem_instante_e_rejeitada(self):
        h = [_obs('Bike:180:a1', '2025-07-26', 305.0, None)]
        out = prod.associar_pendentes(self.loja, h, datetime(2025, 7, 27, 9))
        self.assertEqual(out[0]['motivo'], 'observacao_sem_instante_de_conhecimento')

    def test_retirada_nao_conta_como_resultado_atual(self):
        h = [_obs('Bike:180:a1', '2025-07-26', 305.0, '2025-07-26T18:00:00'),
             _obs('Bike:180:a1', '2025-07-26', 305.0, '2025-07-26T20:00:00', versao=2, evento='retirada')]
        out = prod.associar_pendentes(self.loja, h, datetime(2025, 7, 27, 9))
        self.assertEqual(out[0]['acao'], 'sem_resultado')

    def test_revisao_do_resultado_cria_versao_e_preserva_a_previsao(self):
        h1 = [_obs('Bike:180:a1', '2025-07-26', 305.0, '2025-07-26T18:00:00')]
        prod.associar_pendentes(self.loja, h1, datetime(2025, 7, 27, 9))
        antes = self.loja.previsao(self.pid)
        h2 = [_obs('Bike:180:a1', '2025-07-26', 309.0, '2025-07-28T09:00:00', versao=2)]
        out = prod.associar_pendentes(self.loja, h2, datetime(2025, 7, 29, 9))
        self.assertEqual((out[0]['acao'], out[0]['versao']), ('registado', 2))
        self.assertEqual(self.loja.previsao(self.pid), antes)
        self.assertEqual([v['y_observado'] for v in self.loja.versoes(self.pid)], [305.0, 309.0])

    def test_previsao_futura_nao_e_associada(self):
        futura = self._previsao('2025-08-30', sig='s2')
        h = [_obs('Bike:180:f1', '2025-08-30', 300.0, '2025-08-30T18:00:00')]
        prod.associar_pendentes(self.loja, h, datetime(2025, 7, 27, 9))
        self.assertEqual(self.loja.versoes(futura), [])            # o dia-alvo ainda nao chegou


class Integracao(_ComRegisto):
    def test_sem_migracao_nao_escreve_e_nao_levanta(self):
        loja_sem = mp.LojaPrevisoes(self.loja._exec, 'sqlite')
        self.loja._exec('DROP TABLE modelo_ir_sombra_resultados', (), None)
        self.loja._exec('DROP TABLE modelo_ir_sombra_previsoes', (), None)
        with mock.patch.object(mp, 'loja_da_base', return_value=loja_sem), mock.patch('db.ENABLED', True):
            r = prod.integrar_apos_sync({}, 'sync')
        self.assertEqual(r['ignorado'], 'migracao_pendente')
        self.assertFalse(r['escrito_operacional'])

    def test_integracao_com_migracao_so_escreve_no_registo(self):
        """Caminho completo com a migracao aplicada: entradas sinteticas, sem recalibrar nem escrever operacao."""
        carga, obs = _cenario()
        entradas = {m: {'obs': [], 'cargas': {}, 'p0_valor': None, 'p0_motivo': 'sem_yes_valido'}
                    for m in ('Bike', 'Row', 'Ski', 'Run')}
        entradas['Bike'] = {'obs': obs, 'cargas': carga, 'p0_valor': P0, 'p0_motivo': None}
        historico_hoje = [_obs('Bike:180:h1', '2025-07-24', 300.0, '2025-07-24T18:00:00')]
        with mock.patch.object(mp, 'loja_da_base', return_value=self.loja), mock.patch('db.ENABLED', True), \
                mock.patch('db.yes_historico', return_value=[]), \
                mock.patch.object(prod, 'preparar_entradas', return_value=entradas), \
                mock.patch('modelo_ir_sombra_integracao.carregar_entradas',
                           return_value=(historico_hoje, [], [], [], [])), \
                mock.patch.object(mis, 'recalibrar', side_effect=AssertionError('nao deve recalibrar')):
            r = prod.integrar_apos_sync({}, 'sync', agora=AGORA)
        self.assertTrue(r['ok'])
        self.assertFalse(r['escrito_operacional'])
        bike = [p for p in r['previsoes'] if p['modalidade'] == 'Bike'][0]
        self.assertEqual(bike['acao'], 'registada')
        escritas = [s for s in self.log if s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
        self.assertTrue(escritas)
        for sql in escritas:
            self.assertTrue(any(t in sql for t in (mp.TAB_PREV, mp.TAB_RES)), sql)
            self.assertNotIn('modelo_ir_ajustes', sql)
            self.assertNotIn('ultimo_ajuste_ir', sql)

    def test_falha_do_produtor_nao_derruba_o_sync(self):
        base = {'ok': True, 'unidades': {'Bike:180': {'acao': 'ajustado', 'versao_ajuste': 3}}}
        with mock.patch.object(mis, 'recalibrar', return_value=base), \
                mock.patch.object(mis, '_sombra_apos_recalibrar', lambda r, o: None), \
                mock.patch.object(prod, 'integrar_apos_sync', side_effect=RuntimeError('boom')):
            r = mis.apos_deteccao({'ok': True}, origem='sync')
        self.assertTrue(r['ok'])
        self.assertEqual(r['unidades'], base['unidades'])            # resultado operacional intacto
        self.assertFalse(r['previsoes_prospetivas']['ok'])
        self.assertIn('boom', r['previsoes_prospetivas']['erro'])

    def test_erro_interno_do_integrador_nao_levanta(self):
        with mock.patch.object(mp, 'loja_da_base', side_effect=ValueError('sem base')), \
                mock.patch('db.ENABLED', True):
            r = prod.integrar_apos_sync({}, 'sync')
        self.assertFalse(r['ok'])
        self.assertFalse(r['escrito_operacional'])

    def test_sem_base_nao_faz_nada(self):
        with mock.patch('db.ENABLED', False):
            r = prod.integrar_apos_sync({}, 'sync')
        self.assertEqual(r['ignorado'], 'sem_base_de_dados')


class EstadoPainel(_ComRegisto):
    def test_estados_do_painel_passo_a_passo(self):
        agora = datetime(2025, 7, 25, 10)
        self.assertEqual(prod.estado_registo(self.loja, [], agora)['estado'], 'sem_previsoes')
        mp.registar_previsao(self.loja, calculado_em='2025-07-25T10:00:00', data_alvo='2025-07-26', modalidade='Bike',
                             duracao_s=180, versao_modelo='ir-v1', lag_dias=1, entradas_sig='b1',
                             estado_temporal='vazio', motivo_bloqueio=prod.MOTIVO_SEM_OBS)
        e = prod.estado_registo(self.loja, [], agora)
        self.assertEqual((e['estado'], e['bloqueadas'], e['calculadas']), ('so_bloqueadas', 1, 0))
        self.assertEqual(e['motivos_bloqueio'], {prod.MOTIVO_SEM_OBS: 1})
        mp.registar_previsao(self.loja, calculado_em='2025-07-25T10:00:00', data_alvo='2025-07-26', modalidade='Row',
                             duracao_s=180, versao_modelo='ir-v1', lag_dias=1, entradas_sig='r1',
                             estado_temporal='disponivel', previsao_ir=200.0, parametros={'p0': 1})
        e = prod.estado_registo(self.loja, [], agora)
        self.assertEqual(e['estado'], 'pendentes')
        self.assertEqual(e['pendentes_de_resultado'], 1)
        self.assertEqual(e['vencidas_sem_resultado'], 0)
        self.assertEqual(e['metricas'], None)
        self.assertFalse(e['suficiencia_metricas']['suficiente'])
        pid = [p['previsao_id'] for p in self.loja.todas() if p['modalidade'] == 'Row'][0]
        mp.associar_resultado(self.loja, pid, test_id='Row:180:x', y_observado=198.0,
                              observado_em='2025-07-26T19:00:00')
        e = prod.estado_registo(self.loja, [], datetime(2025, 7, 27, 10))
        self.assertEqual(e['estado'], 'com_resultados')
        self.assertEqual(e['com_resultado'], 1)

    def test_metricas_so_com_amostra_suficiente_sinteticas(self):
        # DADOS SINTETICOS: 25 previsoes em 25 dias distintos cobrindo 96 dias, so para testar as regras de suficiencia.
        d0 = datetime(2025, 1, 1)
        for i in range(25):
            alvo = (d0 + timedelta(days=4 * i + 1)).date().isoformat()
            calc = (d0 + timedelta(days=4 * i)).date().isoformat() + 'T10:00:00'
            pid = mp.registar_previsao(self.loja, calculado_em=calc, data_alvo=alvo, modalidade='Bike', duracao_s=180,
                                       versao_modelo='ir-v1', lag_dias=1, entradas_sig=f'syn{i}',
                                       estado_temporal='disponivel', previsao_ir=300.0 + i,
                                       parametros={'p0': 1})['previsao_id']
            mp.associar_resultado(self.loja, pid, test_id=f'Bike:180:s{i}', y_observado=300.0,
                                  observado_em=alvo + 'T19:00:00')
        e = prod.estado_registo(self.loja, [], datetime(2025, 6, 1, 10))
        self.assertTrue(e['suficiencia_metricas']['suficiente'])
        self.assertEqual(e['metricas']['n'], 25)
        self.assertAlmostEqual(e['metricas']['erro_medio'], 12.0, places=3)

    def test_listar_mostra_situacao_por_previsao(self):
        mp.registar_previsao(self.loja, calculado_em='2025-07-25T10:00:00', data_alvo='2025-07-26', modalidade='Bike',
                             duracao_s=180, versao_modelo='ir-v1', lag_dias=1, entradas_sig='l1',
                             estado_temporal='vazio', motivo_bloqueio=prod.MOTIVO_SEM_OBS)
        rows = prod.listar(self.loja)
        self.assertEqual(rows[0]['situacao'], 'bloqueada')

    def test_sem_tabela_e_migracao_pendente(self):
        self.loja._exec('DROP TABLE modelo_ir_sombra_resultados', (), None)
        self.loja._exec('DROP TABLE modelo_ir_sombra_previsoes', (), None)
        self.assertEqual(prod.estado_registo(self.loja, [], AGORA)['estado'], 'migracao_pendente')


class RotaProspetiva(_ComRegisto):
    def test_rota_le_o_registo_sem_escrever(self):
        import app as appmod
        mp.registar_previsao(self.loja, calculado_em='2025-07-25T10:00:00', data_alvo='2025-07-26', modalidade='Bike',
                             duracao_s=180, versao_modelo='ir-v1', lag_dias=1, entradas_sig='rt1',
                             estado_temporal='disponivel', previsao_ir=300.0, parametros={'p0': 1})
        antes = len(self.log)
        c = appmod.app.test_client()
        with mock.patch('db.ENABLED', True), \
                mock.patch.object(mp, 'loja_da_base', return_value=self.loja), \
                mock.patch('db.observacoes_auto_historico', return_value=[]):
            r = c.get('/api/modelo/ir/sombra/prospetiva')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j['status'], 'OK')
        self.assertEqual(j['registo']['estado'], 'pendentes')
        self.assertFalse(j['retroativa'])
        self.assertEqual(len(j['recentes']), 1)
        self.assertEqual(j['recentes'][0]['situacao'], 'pendente')
        durante = self.log[antes:]
        self.assertFalse([s for s in durante if s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])

    def test_rota_sem_base_responde_503(self):
        import app as appmod
        with mock.patch('db.ENABLED', False):
            r = appmod.app.test_client().get('/api/modelo/ir/sombra/prospetiva')
        self.assertEqual(r.status_code, 503)


if __name__ == '__main__':
    unittest.main()


class PainelProspetivoJS(unittest.TestCase):
    """Executa o JS REAL do bloco <<ir-sombra>> (prospCarregar/prospRender) com respostas do registo. Sem rede."""

    @classmethod
    def setUpClass(cls):
        import shutil
        import subprocess
        if shutil.which('node') is None:
            raise unittest.SkipTest('node nao instalado: JS do painel prospetivo nao executado')
        cls.node = shutil.which('node')
        cls.tab = os.path.join(ROOT, 'tabs', 'tab_pmc.py')
        cls.harness = os.path.join(HERE, 'js', 'harness_ir_sombra.js')
        cls._sub = subprocess

    def _rodar(self, nome, corpo, falha=False):
        import json
        import tempfile as tf
        resp = {'falha': True} if falha else {'corpo': corpo}
        cen = {nome: {'respostas': [resp], 'acoes': [{'tipo': 'abrir'}, {'tipo': 'prospetiva'}]}}
        with tf.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(cen, f, ensure_ascii=False)
            caminho = f.name
        try:
            out = self._sub.run([self.node, self.harness, self.tab, caminho], capture_output=True, text=True,
                                timeout=120)
        finally:
            os.unlink(caminho)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)[nome]

    def _registo(self, **kw):
        base = {'estado': 'pendentes', 'previsoes': 3, 'calculadas': 2, 'bloqueadas': 1,
                'motivos_bloqueio': {'sem_observacoes_conhecidas': 1}, 'pendentes_de_resultado': 2,
                'vencidas_sem_resultado': 1, 'com_resultado': 0, 'revisoes_de_resultado': 0,
                'suficiencia_metricas': {'n': 0, 'suficiente': False}, 'metricas': None, 'retroativa': False}
        base.update(kw)
        return {'status': 'OK', 'registo': base, 'recentes': [], 'retroativa': False}

    def test_mostra_pendentes_bloqueadas_e_metricas_insuficientes(self):
        out = self._rodar('pend', self._registo())
        self.assertEqual(out['urls'], ['/api/modelo/ir/sombra/prospetiva'])
        self.assertIn('existem previsões pendentes de resultado', out['prosp_html'])
        self.assertIn('bloqueadas: 1', out['prosp_html'])
        self.assertIn('Motivos de bloqueio', out['prosp_html'])
        self.assertIn('dados insuficientes', out['prosp_html'])
        self.assertIn('não é retroativo', out['prosp_html'])

    def test_mostra_metricas_quando_a_amostra_e_suficiente(self):
        reg = self._registo(estado='com_resultados', com_resultado=25,
                            metricas={'n': 25, 'mae': 12.5, 'erro_medio': -3.0, 'periodo': ['2025-01-02', '2025-06-01']},
                            suficiencia_metricas={'n': 25, 'suficiente': True})
        out = self._rodar('ok', reg)
        self.assertIn('existem previsões com resultado associado', out['prosp_html'])
        self.assertIn('MAE 12.5 W', out['prosp_html'])
        self.assertIn('n=25', out['prosp_html'])

    def test_migracao_pendente_e_dito_sem_inventar_previsoes(self):
        out = self._rodar('mig', {'status': 'OK', 'registo': {'estado': 'migracao_pendente', 'previsoes': 0},
                                  'recentes': [], 'retroativa': False})
        self.assertIn('migração pendente', out['prosp_html'])
        self.assertNotIn('Previsões:', out['prosp_html'])

    def test_falha_do_pedido_mostra_aviso_e_nao_desenha_nada(self):
        out = self._rodar('falha', None, falha=True)
        self.assertIn('indisponível', out['prosp_html'])

    def test_erro_do_servidor_mostra_a_mensagem_real(self):
        out = self._rodar('erro', {'status': 'erro', 'mensagem': 'sem base de dados'})
        self.assertIn('sem base de dados', out['prosp_html'])

    def _registo_json(self, reg):
        return {'status': 'OK', 'registo': reg, 'recentes': [], 'retroativa': False}
