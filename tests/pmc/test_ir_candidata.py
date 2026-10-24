"""Candidata Ski-300 mediana5|D_T1fixo: registo selado, imutavel e avaliacao prospetiva sem reajuste."""
import copy, json, os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')
import modelo_ir_candidata as cand  # noqa: E402
import modelo_ir_confianca as mic  # noqa: E402

REG_PATH = os.path.join(HERE, 'fixtures', 'candidata_ski300_mediana5_D_T1fixo.json')


def _reg():
    return json.load(open(REG_PATH, encoding='utf-8'))


def _cortes_do_registo(reg):
    """Transcricao explicita dos campos de desenvolvimento para o formato de corte (mae_ult -> mae_ultimo)."""
    out = []
    for m in reg['metricas_desenvolvimento']:
        out.append({'corte': m['corte'], 'n_treino': m['n_treino'], 'n_teste': m['n_teste'],
                    'mae_ir': m['mae_ir'], 'rmse_ir': m['rmse_ir'], 'mae_media': m['mae_media'],
                    'rmse_media': m['rmse_media'], 'mae_ultimo': m['mae_ult'], 'rmse_ultimo': m['rmse_ult'],
                    'identificavel': m['identificavel'], 'no_limite': bool(m['no_limite']),
                    'cond': m['cond'], 'corr_max': m['corr_max'], 'parametros': m['parametros_estimados']})
    return out


class TestRegistoCongelado(unittest.TestCase):

    def test_hash_confere_e_estado_nao_operacional(self):
        reg = _reg()
        self.assertTrue(cand.verificar(reg))
        self.assertFalse(reg['promocao_operacional'])
        self.assertEqual(reg['estado'], 'candidata_experimental_congelada')

    def test_qualquer_alteracao_e_detetada(self):
        reg = _reg(); reg['configuracao']['fixos']['t1'] = 'outro'
        self.assertFalse(cand.verificar(reg))

    def test_contem_identidade_dos_dados_codigo_politica_e_datas(self):
        reg = _reg()
        self.assertEqual(reg['modalidade'], 'Ski'); self.assertEqual(reg['duracao_s'], 300)
        self.assertEqual(reg['dados_desenvolvimento']['sha256'], '2250b2ee2d59eb5010fd8d6e9eddf65698534924bc1adbac17070846f90c61c5')
        self.assertEqual(reg['codigo']['commit_base'], '24ddf4a')
        self.assertTrue(reg['codigo']['modelo_ir_py_sha256'])
        self.assertEqual(reg['politica']['versao'], mic.VERSAO_POLITICA)
        self.assertEqual(reg['data_ultima_observacao_desenvolvimento'], '2026-10-21')
        self.assertEqual(reg['data_congelamento'], '2026-10-23')

    def test_T1_por_corte_e_parametros_livres_registados(self):
        reg = _reg()
        self.assertEqual(set(reg['T1_escolhido_por_corte_desenvolvimento']), set(reg['cortes_desenvolvimento']))
        self.assertEqual(reg['configuracao']['parametros_livres'], ['p0', 'k1', 'k2', 't2'])

    def test_reclassificacao_do_desenvolvimento_reproduz_B(self):
        reg = _reg()
        av = {'esquema': 'cortes_treino_teste', 'configuracao': reg['configuracao'], 'n_observacoes': 25,
              'falha_ajuste': False, 'suspeita_fuga': False, 'validacao_causal_verificada': True,
              'cortes': _cortes_do_registo(reg)}
        d = mic.classificar(av)
        self.assertEqual(d['nivel'], 'B')
        self.assertTrue(d['validada'])


class TestAvaliacaoProspetiva(unittest.TestCase):

    def _novas(self, n, data_ini='2026-10-22'):
        from datetime import date, timedelta
        base = date.fromisoformat(data_ini)
        return [{'test_id': i, 'data': (base + timedelta(days=i)).isoformat(), 'watts': 200.0} for i in range(n)]

    def _cortes_bons(self, reg, obs):
        cs = []
        for i in range(3):
            cs.append({'corte': f'novo{i}', 'n_treino': 50, 'n_teste': 6, 'mae_ir': 10.0, 'rmse_ir': 12.0,
                       'mae_media': 20.0, 'rmse_media': 24.0, 'mae_ultimo': 25.0, 'rmse_ultimo': 30.0,
                       'identificavel': True, 'no_limite': False, 'parametros': {}})
        return cs

    def test_observacoes_do_desenvolvimento_sao_recusadas(self):
        reg = _reg()
        obs = self._novas(20, data_ini='2026-10-01')  # anteriores ou iguais ao corte de desenvolvimento
        r = cand.avaliar_prospetiva(reg, obs, lambda v, r: self._cortes_bons(r, v))
        self.assertEqual(r['estado'], cand.ESTADO_EM_OBSERVACAO)
        self.assertEqual(r['recusadas_nao_prospetivas'], 20)

    def test_fronteira_do_corte_e_recusada(self):
        reg = _reg()
        obs = [{'test_id': 1, 'data': '2026-10-21', 'watts': 200.0}]
        self.assertEqual(cand.avaliar_prospetiva(reg, obs, lambda v, r: [])['recusadas_nao_prospetivas'], 1)

    def test_corte_de_desenvolvimento_reutilizado_e_recusado(self):
        """Proibido reselecionar a candidata com os cortes usados no desenvolvimento (ex.: 2025-01-01)."""
        reg = _reg()
        r = cand.avaliar_prospetiva(reg, self._novas(20), lambda v, rg: [{'corte': '2025-01-01'}])
        self.assertEqual(r['estado'], 'recusada_cortes_de_desenvolvimento')
        self.assertFalse(r['promocao_operacional'])

    def test_corte_anterior_ao_fim_do_desenvolvimento_e_recusado(self):
        reg = _reg()
        r = cand.avaliar_prospetiva(reg, self._novas(20), lambda v, rg: [{'corte': '2026-10-01'}])
        self.assertEqual(r['estado'], 'recusada_cortes_de_desenvolvimento')
        # corte posterior ao fim do desenvolvimento NAO e recusado por esta regra
        r2 = cand.avaliar_prospetiva(reg, self._novas(20), lambda v, rg: [{'corte': '2026-10-22'}])
        self.assertNotEqual(r2['estado'], 'recusada_cortes_de_desenvolvimento')

    def test_entre_o_fim_do_desenvolvimento_e_o_congelamento_e_recusada(self):
        """Regressao etapa 6: 22/10 e posterior ao desenvolvimento mas anterior ao congelamento (23/10)."""
        reg = _reg()
        obs = [{'test_id': i, 'data': '2026-10-22', 'watts': 200.0} for i in range(20)]
        r = cand.avaliar_prospetiva(reg, obs, lambda v, rg: [])
        self.assertEqual(r['recusadas_nao_prospetivas'], 20)
        self.assertEqual(r['estado'], cand.ESTADO_EM_OBSERVACAO)

    def test_dados_no_dia_do_congelamento_sao_aceites(self):
        reg = _reg()
        obs = [{'test_id': i, 'data': '2026-10-23', 'watts': 200.0} for i in range(20)]
        r = cand.avaliar_prospetiva(reg, obs, lambda v, rg: [])
        self.assertEqual(r['recusadas_nao_prospetivas'], 0)

    def test_dados_insuficientes_mantem_em_observacao(self):
        r = cand.avaliar_prospetiva(_reg(), self._novas(5), lambda v, r: [])
        self.assertEqual(r['estado'], cand.ESTADO_EM_OBSERVACAO)
        self.assertFalse(r['promocao_operacional'])

    def test_dados_suficientes_sao_avaliados_sem_promocao(self):
        reg = _reg()
        r = cand.avaliar_prospetiva(reg, self._novas(20), lambda v, rg: self._cortes_bons(rg, v))
        self.assertEqual(r['estado'], 'avaliada_prospetivamente')
        self.assertEqual(r['nivel'], 'B')
        self.assertFalse(r['promocao_operacional'])

    def test_falha_do_avaliador_nao_promove(self):
        def falha(v, r):
            raise RuntimeError('otimizador divergiu')
        r = cand.avaliar_prospetiva(_reg(), self._novas(20), falha)
        self.assertEqual(r['estado'], 'falha_avaliacao')
        self.assertFalse(r['promocao_operacional'])

    def test_registo_alterado_nao_e_avaliado(self):
        reg = copy.deepcopy(_reg()); reg['modalidade'] = 'Bike'
        with self.assertRaises(ValueError):
            cand.avaliar_prospetiva(reg, self._novas(20), lambda v, r: [])


if __name__ == '__main__':
    unittest.main()
