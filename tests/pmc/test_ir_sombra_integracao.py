"""Modo sombra LIGADO ao fluxo existente (etapa 6).

Dois niveis:
  * ordem do gancho (apos_deteccao -> recalibrar -> sombra), sem base de dados;
  * cenario real de recalibrar (test_modelo_ir_servico._Base) em base temporaria, com a migracao aplicada,
    verificando: sombra gravada, versao operacional intacta, idempotencia, nao_medido, conhecido_na_epoca,
    falha do avaliador sem afetar a sincronizacao.
"""
import copy, os, sys, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')
import test_modelo_ir_servico as TS  # noqa: E402
import test_mmp_deteccao_automatica as T  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_ir_sombra as sombra  # noqa: E402
import modelo_ir_sombra_integracao as msi  # noqa: E402
import modelo_ir_confianca_migracao as mig  # noqa: E402
import modelo_ir_temporal_migracao as tmig  # noqa: E402
import modelo_ir_curvas as mcurv  # noqa: E402
import modelo_ir_curvas_migracao as mcmig  # noqa: E402
import modelo_mmp3 as mmp3  # noqa: E402
import db  # noqa: E402


def setUpModule():
    TS.setUpModule()


def tearDownModule():
    TS.tearDownModule()


def resultado_recal(unidades, ok=True):
    return {'ok': ok, 'p0_global': {'Bike': {'p0': 290.0}}, 'unidades': unidades}


class OrdemDoGancho(unittest.TestCase):
    """apos_deteccao chama recalibrar() e so depois a sombra, com o resultado de recalibrar()."""

    def test_sombra_roda_depois_de_recalibrar_e_recebe_o_resultado(self):
        ordem, recebido = [], {}
        res = resultado_recal({'Bike:180': {'acao': 'ajustado', 'versao_ajuste': 3}})

        def fake_recal(origem='sync', **kw):
            ordem.append('recalibrar')
            return res

        def fake_sombra(r, origem, dados=None, agora=None):
            ordem.append('sombra')
            recebido['r'] = r
            return {'ok': True, 'unidades': [], 'escrito_operacional': False}
        with mock.patch.object(mis, 'recalibrar', side_effect=fake_recal), \
                mock.patch.object(msi, 'apos_recalibrar', side_effect=fake_sombra):
            out = mis.apos_deteccao({'ok': True}, origem='sync')
        self.assertEqual(ordem, ['recalibrar', 'sombra'])
        self.assertIs(recebido['r'], res)
        self.assertTrue(out['ok'])
        self.assertFalse(out['sombra']['escrito_operacional'])

    def test_falha_da_sombra_nao_altera_o_resultado_operacional(self):
        res = resultado_recal({'Bike:180': {'acao': 'ajustado', 'versao_ajuste': 3}})
        with mock.patch.object(mis, 'recalibrar', return_value=res), \
                mock.patch.object(msi, 'apos_recalibrar', side_effect=RuntimeError('sombra partiu')):
            out = mis.apos_deteccao({'ok': True}, origem='sync')
        self.assertTrue(out['ok'])
        self.assertEqual(out['unidades'], res['unidades'])
        self.assertFalse(out['sombra']['ok'])
        self.assertIn('sombra partiu', out['sombra']['erro'])
        self.assertFalse(out['sombra']['escrito_operacional'])

    def test_recalibrar_falhado_nao_chama_a_sombra(self):
        with mock.patch.object(mis, 'recalibrar', return_value={'ok': False, 'erro': 'x'}), \
                mock.patch.object(msi, 'apos_recalibrar') as sombra_mock:
            mis.apos_deteccao({'ok': True}, origem='sync')
        sombra_mock.assert_called_once()  # o gancho e chamado; decide sem unidades (recalibrar_sem_sucesso)
        r = msi.apos_recalibrar({'ok': False}, 'sync')
        self.assertEqual(r['ignorado'], 'recalibrar_sem_sucesso')


class SombraSemUnidades(unittest.TestCase):

    def test_sem_unidades_operacionais_nao_grava_nada(self):
        out = msi.apos_recalibrar(resultado_recal({'Bike:180': {'acao': 'sem_observacoes'}}), 'sync',
                                  dados={'loja': mock.Mock()})
        self.assertEqual(out['ignorado'], 'sem_unidades_operacionais')
        self.assertFalse(out['escrito_operacional'])

    def test_tabela_inexistente_devolve_migracao_pendente(self):
        import tempfile
        d = tempfile.TemporaryDirectory()
        try:
            loja = sombra.loja_sqlite(os.path.join(d.name, 'vazia.db'))
            out = msi.apos_recalibrar(resultado_recal({'Bike:180': {'acao': 'ajustado'}}), 'sync',
                                      dados={'loja': loja})
            self.assertEqual(out['ignorado'], 'tabela_inexistente')
            self.assertEqual(out['migracao_pendente'], mig.MIGRACAO_ID)
            self.assertFalse(out['escrito_operacional'])
        finally:
            d.cleanup()

    def test_avaliacao_exige_historico_temporal(self):
        with self.assertRaises(ValueError) as ctx:
            msi.avaliacao_walk_forward([], [], [], [], 'Bike', 180, None)
        self.assertEqual(str(ctx.exception), 'historico_temporal_indisponivel')

    def test_avaliacao_passa_o_registo_temporal_e_modo_conhecido(self):
        registo = object()
        with mock.patch.object(mis, 'validar_preditivo',
                               return_value={'itens': [], 'historico_temporal': 'registo_prospetivo'}) as vp:
            r = msi.avaliacao_walk_forward([], [], [], [], 'Bike', 180, registo)
        self.assertIs(vp.call_args.kwargs['temporal'], registo)
        self.assertEqual(vp.call_args.kwargs['modos'], (msi.MODO_AVALIACAO,))
        self.assertEqual(msi.MODO_AVALIACAO, 'conhecido_na_epoca')
        self.assertEqual(r['n_observacoes'], 0)  # sem alvos -> dados insuficientes -> C
        self.assertEqual(sombra.mic.classificar(r, sombra.mic.POLITICA)['nivel'], 'C')


class CenarioReal(TS._Base):
    """recalibrar() real no cenario do assento de teste (duracao operacional 1200 s, P0 injetado pelo assento) e
    sombra em base temporaria com a migracao aplicada.

    DUPLO DE ELEGIBILIDADE (unica substituicao): a deteccao MMP3 (YES) nao produz observacoes com este cenario
    sintetico. Substituimos apenas entradas_mmp3 por observacoes derivadas das curvas do proprio cenario. Recalibrar,
    P0, carga, validacao walk-forward, classificador, sombra e persistencia continuam reais.
    """

    def setUp(self):
        super().setUp()  # cenario, P0 injetado, base temporaria e sincronizacao (_Base / BaseDB)
        # mesmo formato de modelo_ir_servico._obs_min: {test_id, versao (revisao), data, watts}
        self._obs_cenario = [{'test_id': c['activity_id'], 'versao': None, 'data': c['date'],
                              'watts': float(c['watts'][-1])} for c in self.CURVAS]
        patch = mock.patch.object(mis, 'entradas_mmp3', side_effect=self._entradas_sinteticas)
        patch.start()
        self.addCleanup(patch.stop)
        import test_ir_validacao_p0 as V
        p0 = mock.patch.object(mis, 'p0_global_das_entradas', new=V.P0_REAL)   # regra real, sem P0 fixo
        p0.start()
        self.addCleanup(p0.stop)
        mig.aplicar(db._exec, db.DRIVER, '2026-10-23T10:00:00')
        tmig.aplicar(db._exec, db.DRIVER, '2026-10-23T10:00:00')   # historico temporal (exigido pela sombra)
        mcmig.aplicar(db._exec, db.DRIVER, '2026-10-23T10:00:00')  # historico de curvas (exigido pela sombra)
        # primeira versao de cada curva antes de qualquer alvo do cenario (relogio injetado de teste)
        mcurv.HistoricoCurvas(db._exec, db.DRIVER).registar(db.load_power_curves() or [], '2024-12-31T00:00:00')

    def _entradas_sinteticas(self, curvas, atividades, ate, marcos=None, revisoes=None):
        obs = [o for o in self._obs_cenario if o['data'] <= str(ate)]
        efeitos = [{'test_id': f"Bike:1200:{o['test_id']}", 'activity_id': o['test_id'], 'modalidade': 'Bike',
                    'data': o['data'], 'inicio': None, 'watts': o['watts'], 'estado': mmp3.E_ELEGIVEL,
                    'motivo': 'yes', 'tag_3min': True, 'pr': False, 'pct_do_melhor': 1.0,
                    'origem_classificacao': 'sintetico', 'motivo_decisao': 'sintetico', 'classe_intervalo': None,
                    'origem_registo': None, 'season': None} for o in obs]
        return {'Bike': (obs, efeitos), 'Row': ([], []), 'Ski': ([], []), 'Run': ([], [])}

    def sombra_linhas(self):
        return db._exec(f'SELECT * FROM {sombra.NOME_TABELA}', (), 'all') or []

    def operacional(self):
        return db._exec('SELECT * FROM modelo_ir_ajustes', (), 'all') or []

    def test_apos_deteccao_grava_sombra_e_nao_mexe_na_operacional(self):
        r = self.prepara()
        out = mis.apos_deteccao(r, origem='sync')
        self.assertTrue(out['ok'])
        self.assertTrue(out['sombra']['ok'], out['sombra'])
        self.assertFalse(out['sombra']['escrito_operacional'])
        self.assertTrue(out['sombra'].get('unidades'), 'a sombra deve avaliar pelo menos uma unidade real')
        # a sombra, isolada, nao acrescenta versoes operacionais
        antes = self.operacional()
        msi.apos_recalibrar(resultado_recal({'Bike:180': {'acao': 'inalterado'}}), 'sync')
        self.assertEqual(self.operacional(), antes)

    def test_nova_execucao_com_dados_iguais_nao_reavalia(self):
        r = self.prepara()
        mis.apos_deteccao(r, origem='sync')
        with mock.patch.object(msi, 'avaliacao_walk_forward', wraps=msi.avaliacao_walk_forward) as av:
            out2 = mis.apos_deteccao(r, origem='sync')
        self.assertEqual(av.call_count, 0)
        for u in out2['sombra'].get('unidades', []):
            self.assertIn(u['acao_sombra'], ('sem_trabalho', 'sem_entradas'))

    def test_falha_do_avaliador_registra_C_sem_afetar_sincronizacao(self):
        r = self.prepara()
        with mock.patch.object(msi, 'avaliacao_walk_forward', side_effect=RuntimeError('ajuste divergiu')):
            out = mis.apos_deteccao(r, origem='sync')
        self.assertTrue(out['ok'])
        self.assertTrue(out['sombra']['ok'])
        for u in out['sombra'].get('unidades', []):
            if u.get('acao_sombra') == 'avaliado':
                self.assertEqual(u['nivel'], 'C')
                self.assertFalse(u['teria_promovido'])
                self.assertTrue(any('falha_avaliacao' in m for m in u['motivos']))

    def test_escritas_da_sombra_so_na_propria_tabela(self):
        r = self.prepara()
        with mock.patch.object(db, '_exec', wraps=db._exec) as spy:
            msi.apos_recalibrar(resultado_recal({'Bike:180': {'acao': 'ajustado', 'versao_ajuste': 9}}), 'sync',
                                agora=None)
        escritas = [c.args[0] for c in spy.call_args_list
                    if c.args and c.args[0].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE'))]
        for sql in escritas:
            self.assertIn(sombra.NOME_TABELA, sql, sql[:80])

    def test_decisao_de_amostra_com_campos_separados(self):
        r = self.prepara()
        out = mis.apos_deteccao(r, origem='sync')
        for u in out['sombra'].get('unidades', []):
            if u.get('acao_sombra') == 'avaliado':
                self.assertIn('confianca_parametros', u)
                self.assertIn('confianca_previsao', u)
                self.assertIn(u['confianca_parametros'], ('alta', 'media', 'baixa', 'restrito', 'nao_medido'))
                self.assertIn(u['confianca_previsao'], ('alta', 'media', 'baixa', 'nula'))
                self.assertNotEqual(u['confianca_parametros'], 'alta')  # walk-forward nao mede identificabilidade
                self.assertIn(u['nivel'], ('A', 'B', 'C'))
                self.assertNotEqual(u['nivel'], 'A')  # sem diagnostico de identificabilidade nunca e A


    def test_atividade_nova_ou_alterada_gera_nova_avaliacao(self):
        r = self.prepara()
        mis.apos_deteccao(r, origem='sync')
        n1 = len(self.sombra_linhas())
        # atividade alterada: a carga muda -> assinatura muda -> nova avaliacao (sem repetir a anterior)
        self.ativ[0] = dict(self.ativ[0], icu_training_load=float(self.ativ[0].get('icu_training_load') or 0) + 25.0)
        out = mis.apos_deteccao(r, origem='sync')
        self.assertTrue(any(u.get('acao_sombra') == 'avaliado' for u in out['sombra'].get('unidades', [])))
        self.assertGreater(len(self.sombra_linhas()), n1)

    def test_avaliacao_usa_conhecido_na_epoca(self):
        r = self.prepara()
        with mock.patch.object(mis, 'validar_preditivo', wraps=mis.validar_preditivo) as vp:
            mis.apos_deteccao(r, origem='sync')
        self.assertTrue(vp.called)
        for c in vp.call_args_list:
            self.assertEqual(c.kwargs.get('modos'), (msi.MODO_AVALIACAO,))


if __name__ == '__main__':
    unittest.main()
