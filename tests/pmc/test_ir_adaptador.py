"""Adaptadores explicitos (cortes da ronda e walk-forward) -> classificador. Dados sinteticos."""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')
import modelo_ir_adaptador as ad  # noqa: E402
import modelo_ir_confianca as mic  # noqa: E402


def alvo(data, y, ir, m0, m1, tid=None):
    return {'test_id': tid or f'{data}-{y}', 'data': data, 'y': y, 'ir': ir, 'M0': m0, 'M1': m1, 'M2': y,
            'n_treino': 20}


def item(previsoes, **extra):
    base = {'modalidade': 'Ski', 'duracao_s': 300, 'modo': 'retrospectivo',
            'n_alvos': len(previsoes), 'n_alvos_com_previsao_ir': len(previsoes),
            'previsoes': previsoes, 'n_previsoes_com_avisos_identificabilidade': 0, 'veredicto': None}
    base.update(extra)
    return base


class TestWalkForward(unittest.TestCase):

    def test_agrupa_por_trimestre_e_calcula_mae_rmse_definidos(self):
        ps = [alvo(f'2025-01-{d:02d}', 100.0, 110.0, 105.0, 90.0) for d in range(1, 6)]  # erros IR=+10
        ps += [alvo(f'2025-04-{d:02d}', 100.0, 100.0, 120.0, 130.0) for d in range(1, 6)]
        av = ad.de_validacao_preditiva(item(ps))
        self.assertEqual(av['esquema'], 'walk_forward_blocos')
        self.assertEqual([c['corte'] for c in av['cortes']], ['2025T1', '2025T2'])
        t1 = av['cortes'][0]
        self.assertAlmostEqual(t1['mae_ir'], 10.0)
        self.assertAlmostEqual(t1['rmse_ir'], 10.0)
        self.assertAlmostEqual(t1['mae_media'], 5.0)   # |105-100|
        self.assertAlmostEqual(t1['mae_ultimo'], 10.0)  # |90-100|
        self.assertIsNone(t1['identificavel'])

    def test_metadados_declaram_que_avisos_por_alvo_nao_existem_e_veredicto_nao_e_usado(self):
        av = ad.de_validacao_preditiva(item([alvo('2025-01-02', 1.0, 2.0, 1.0, 1.0)], veredicto='x'))
        self.assertFalse(av['metadados']['avisos_identificabilidade_por_alvo_disponiveis'])
        self.assertEqual(av['metadados']['veredicto_do_item_nao_usado'], 'x')

    def test_campo_ausente_falha_em_vez_de_adivinhar(self):
        p = alvo('2025-01-02', 1.0, 2.0, 1.0, 1.0); del p['M1']
        with self.assertRaises(ad.FormatoInesperado):
            ad.de_validacao_preditiva(item([p]))
        with self.assertRaises(ad.FormatoInesperado):
            ad.de_validacao_preditiva({'modalidade': 'Ski'})

    def test_populacao_comum_recusa_alvo_sem_baseline(self):
        with self.assertRaises(ad.FormatoInesperado):
            ad.de_validacao_preditiva(item([alvo('2025-01-02', 1.0, None, 1.0, 1.0)]))

    def test_classificador_aceita_o_resultado_do_adaptador(self):
        ps = []
        for q, (mes) in enumerate(['01', '04', '07', '10']):
            ps += [alvo(f'2025-{mes}-{d:02d}', 100.0, 101.0, 130.0, 130.0) for d in range(1, 7)]
        d = mic.classificar(ad.de_validacao_preditiva(item(ps)))
        self.assertEqual(d['esquema'], 'walk_forward_blocos')
        self.assertEqual(d['nivel'], 'B')  # previsao validada; parametros nao medidos
        self.assertIn('parametros_nao_medidos_neste_esquema', d['limitacoes'])

    def test_causalidade_declarada_pelo_chamador(self):
        av = ad.de_validacao_preditiva(item([alvo('2025-01-02', 1.0, 2.0, 1.0, 1.0)]), causal_por_construcao=False)
        self.assertFalse(av['validacao_causal_verificada'])


class TestCortesDaRonda(unittest.TestCase):

    def entrada(self, ronda=4, **over):
        diag = {'params': {'p0': 200.0, 'k1': 0.5, 't1': 60.0, 'k2': 0.0, 't2': 5.0}, 'identificavel': False,
                'no_limite': ['k2'], 'cond': 1e6, 'corr_max': 0.99, 'n': 25}
        e = {'sel': {'t1': 60.0}, 'diag': diag, 'n_teste': 27, 'mae_ir': 21.0, 'rmse_ir': 27.0,
             'mae_media': 25.0, 'rmse_media': 33.0, 'mae_ult': 46.0, 'rmse_ult': 58.0, 'win': True}
        e.update(over)
        return e

    def test_mapeamento_explicito_e_fixos_removidos_dos_parametros(self):
        c = ad.de_cortes_ronda(self.entrada(), '2025-01-01', 4, 'x', 'regularizado', {'t1': 'por_corte'}, True)
        self.assertEqual(c['mae_ultimo'], 46.0)  # mae_ult -> mae_ultimo
        self.assertTrue(c['no_limite'])
        self.assertNotIn('t1', c['parametros'])
        self.assertIn('k1', c['parametros'])

    def test_diagnostico_ausente_falha(self):
        e = self.entrada(); del e['diag']['identificavel']
        with self.assertRaises(ad.FormatoInesperado):
            ad.de_cortes_ronda(e, 'c', 4, 'x', 'completo', {}, True)

    def test_ronda3_usa_diag_treino(self):
        e = self.entrada(ronda=3); e['diag_treino'] = e.pop('diag'); e['n_treino'] = 25
        c = ad.de_cortes_ronda(e, 'c', 3, 'x', 'completo', {}, True)
        self.assertEqual(c['n_treino'], 25)


class TestDefinicoesComoAValidacao(unittest.TestCase):
    """Confirma que MAE/RMSE do adaptador coincidem com modelo_validacao.metricas (mesma definicao)."""

    def test_mae_rmse_iguais_a_modelo_validacao(self):
        try:
            import modelo_validacao as V
        except Exception as e:  # pragma: no cover
            self.skipTest(f'modelo_validacao indisponivel neste ambiente: {e}')
        pares = [(105.0, 100.0), (92.0, 100.0), (130.0, 101.0)]
        erros = [p - o for p, o in pares]
        m = V.metricas(erros)
        mae, rmse = ad._erros(pares)
        self.assertAlmostEqual(mae, m['mae'])
        self.assertAlmostEqual(rmse, m['rmse'])


if __name__ == '__main__':
    unittest.main()
