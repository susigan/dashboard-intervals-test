"""Etapa 7: modo sombra nos CAMINHOS REAIS de sincronizacao e de recalibracao manual.

Sincronizacao: sync.sync_power_curves() real (payload falso da API) -> observacoes_auto -> apos_deteccao ->
recalibrar -> sombra. Entradas de elegibilidade (MMP3) substituidas por duplo SINTETICO (ver test_ir_validacao_p0).
A confirmacao manual esta em test_ir_confirmacao_e2e.py (IdentificacaoEConfirmacao.test_5).
"""
import copy, os, sys, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')
import test_modelo_ir_servico as TS  # noqa: E402
import test_mmp_deteccao_automatica as T  # noqa: E402
import test_ir_validacao_p0 as V  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_ir_sombra as sombra  # noqa: E402
import modelo_ir_sombra_integracao as msi  # noqa: E402
import modelo_ir_confianca_migracao as mig  # noqa: E402
import modelo_ir_temporal_migracao as tmig  # noqa: E402
import modelo_ir_curvas as mcurv  # noqa: E402
import modelo_ir_curvas_migracao as mcmig  # noqa: E402
import observacoes_auto as oa  # noqa: E402
import db  # noqa: E402


def setUpModule():
    TS.setUpModule()


def tearDownModule():
    TS.tearDownModule()


class CaminhoSync(TS._Base):

    def setUp(self):
        super().setUp()
        self._efeitos = [V.yes(f'Bike:1200:{c["activity_id"]}', c['date'], float(c['watts'][-1])) for c in self.CURVAS]
        p = mock.patch.object(mis, 'entradas_mmp3', side_effect=self._entradas)
        p.start(); self.addCleanup(p.stop)
        p0 = mock.patch.object(mis, 'p0_global_das_entradas', new=V.P0_REAL)   # regra real (sem P0 fixo do assento)
        p0.start(); self.addCleanup(p0.stop)
        mig.aplicar(db._exec, db.DRIVER, '2026-10-23T10:00:00')
        tmig.aplicar(db._exec, db.DRIVER, '2026-10-23T10:00:00')
        mcmig.aplicar(db._exec, db.DRIVER, '2026-10-23T10:00:00')   # historico de curvas: a sincronizacao real grava
        db._exec('DELETE FROM power_curves', (), None)
        self.payload = {'secs': [60, 300, 1200],
                        'curves': [{'id': c['activity_id'], 'start_date_local': c['date'] + 'T07:00:00',
                                    'weight': 70, 'watts': c['watts']} for c in copy.deepcopy(self.CURVAS)]}

    def _entradas(self, curvas, atividades, ate, marcos=None, revisoes=None):
        # SINTETICO: observacoes e esforcos derivados das mesmas curvas, com o mesmo corte temporal (data <= ate)
        efeitos = [copy.deepcopy(e) for e in self._efeitos if e['data'] <= ate]
        obs = [{'test_id': e['test_id'], 'versao': None, 'data': e['data'], 'watts': e['watts']} for e in efeitos]
        return {'Bike': (obs, efeitos), 'Row': ([], []), 'Ski': ([], []), 'Run': ([], [])}

    def sincronizar(self):
        import sync
        def falso_get(url, params=None, **k):
            return (self.payload if 'activity-power-curves' in url else []), None
        with mock.patch.object(sync, 'icu_get', side_effect=falso_get), \
                mock.patch.object(sync, 'tipos_reais', return_value={'Bike': ['Ride']}), \
                mock.patch.object(sync, 'athlete_id_real', return_value='0'), \
                mock.patch.object(oa, 'obter_marcos', return_value=(None, 'teste')):
            return sync.sync_power_curves()

    def ajustes(self):
        return db._exec('SELECT * FROM modelo_ir_ajustes', (), 'all') or []

    def tentativas(self):
        return db._exec(f'SELECT COUNT(*) FROM {sombra.NOME_TABELA}', (), 'one')[0]

    def test_sync_real_regista_sombra_sem_tocar_no_operacional(self):
        antes = self.ajustes()
        r = self.sincronizar()
        self.assertTrue(r['ok'], r)
        sh = r['modelo_ir']['sombra']
        self.assertTrue(sh['ok'], sh)
        self.assertFalse(sh['escrito_operacional'])
        self.assertTrue(sh.get('unidades'), 'o caminho real deve avaliar pelo menos uma unidade')
        self.assertEqual(len(self.ajustes()) - len(antes), r['modelo_ir'].get('n_ajustadas', 0))

    def test_sync_real_grava_historico_de_curvas_idempotente(self):
        """Etapa 13: a sincronizacao real regista UMA versao por curva; repetir sem alteracoes nao acrescenta nada."""
        r = self.sincronizar()
        self.assertTrue(r['ok'], r)
        n_curvas = len(db.load_power_curves() or [])
        self.assertGreater(n_curvas, 0)
        self.assertEqual(db._exec(f'SELECT COUNT(DISTINCT activity_id) FROM {mcurv.TAB}', (), 'one')[0], n_curvas)
        antes = db._exec(f'SELECT COUNT(*) FROM {mcurv.TAB}', (), 'one')[0]
        self.sincronizar()
        self.assertEqual(db._exec(f'SELECT COUNT(*) FROM {mcurv.TAB}', (), 'one')[0], antes)

    def test_repetir_mesma_sincronizacao_nao_duplica_tentativas(self):
        self.sincronizar()
        n1 = self.tentativas()
        with mock.patch.object(msi, 'avaliacao_walk_forward', wraps=msi.avaliacao_walk_forward) as av:
            r2 = self.sincronizar()
        self.assertEqual(av.call_count, 0, 'mesma versao de dados: nao reavaliar')
        self.assertEqual(self.tentativas(), n1)
        for u in r2['modelo_ir']['sombra'].get('unidades', []):
            self.assertIn(u['acao_sombra'], ('sem_trabalho', 'sem_entradas'))

    def test_atividade_alterada_gera_nova_tentativa(self):
        self.sincronizar()
        n1 = self.tentativas()
        self.payload['curves'][0]['watts'] = [w * 1.01 for w in self.payload['curves'][0]['watts']]
        for e in self._efeitos:                                   # a mesma alteracao chega ao duplo de elegibilidade
            if e['test_id'].endswith(self.payload['curves'][0]['id']):
                e['watts'] = e['watts'] * 1.01
        r2 = self.sincronizar()
        self.assertTrue(any(u.get('acao_sombra') == 'avaliado' for u in r2['modelo_ir']['sombra'].get('unidades', [])))
        self.assertGreater(self.tentativas(), n1)

    def test_tabela_da_sombra_inexistente_nao_para_a_sincronizacao(self):
        db._exec(f'DROP TABLE IF EXISTS {sombra.NOME_TABELA}', (), None)
        r = self.sincronizar()
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['modelo_ir']['sombra'].get('ignorado'), 'tabela_inexistente')
        self.assertFalse(r['modelo_ir']['sombra']['escrito_operacional'])

    def test_falha_da_avaliacao_sombra_nao_altera_o_operacional(self):
        with mock.patch.object(msi, 'avaliacao_walk_forward', side_effect=RuntimeError('ajuste divergiu')):
            antes = self.ajustes()
            r = self.sincronizar()
        self.assertTrue(r['ok'], r)
        self.assertTrue(r['modelo_ir']['ok'])
        for u in r['modelo_ir']['sombra'].get('unidades', []):
            if u.get('acao_sombra') == 'avaliado':
                self.assertEqual(u['nivel'], 'C')
                self.assertFalse(u['teria_promovido'])
        self.assertEqual(len(self.ajustes()) - len(antes), r['modelo_ir'].get('n_ajustadas', 0))

    def test_recalibracao_manual_regista_sombra(self):
        with mock.patch.object(mis, 'registar_sombra', wraps=mis.registar_sombra) as rs:
            resp = T.APP.app.test_client().post('/api/modelo/ir/recalibrar', json={})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        rs.assert_called_once()
        self.assertEqual(rs.call_args.args[1], 'manual')
        self.assertFalse(resp.get_json().get('sombra', {}).get('escrito_operacional', False))


if __name__ == '__main__':
    unittest.main()
