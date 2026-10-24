"""Classificador A/B/C com dois eixos (politica 2.0). Dados sinteticos; sem base de dados nem rede.

Mudanca face a politica 1.0 (ver modelo_ir_confianca_v1.py): a falta de identificabilidade dos parametros
NAO invalida uma previsao validada; leva a B com limitacoes explicitas. C fica so para previsao nao demonstrada,
dados insuficientes, falha de validacao ou validacao nao causal.
Executar: python -m pytest tests/pmc/test_ir_confianca.py
"""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')
import modelo_ir_confianca as mic  # noqa: E402


def corte(mae_ir=5.0, mae_media=9.0, mae_ultimo=10.0, ident=True, no_limite=False, n_teste=10, nome=None,
          parametros=None, cond=None, corr=None):
    return {'corte': nome or f'c{mae_ir}', 'n_treino': 100, 'n_teste': n_teste,
            'mae_ir': mae_ir, 'rmse_ir': mae_ir * 1.2,
            'mae_media': mae_media, 'rmse_media': mae_media * 1.3,
            'mae_ultimo': mae_ultimo, 'rmse_ultimo': mae_ultimo * 1.3,
            'identificavel': ident, 'no_limite': no_limite, 'parametros': parametros or {},
            'cond': cond, 'corr_max': corr}


def avaliacao(cortes=None, n=100, cfg_id='completo', familia='completo', fixos=None, falha=False, fuga=False,
              causal=True, esquema='cortes_treino_teste'):
    if cortes is None:
        cortes = [corte(5.0, nome='a'), corte(5.1, nome='b'), corte(4.9, nome='c'), corte(5.0, nome='d')]
    return {'esquema': esquema, 'configuracao': {'id': cfg_id, 'familia': familia, 'fixos': fixos or {}},
            'n_observacoes': n, 'falha_ajuste': falha, 'suspeita_fuga': fuga,
            'validacao_causal_verificada': causal, 'cortes': cortes}


class TestEixoParametros(unittest.TestCase):

    def test_parametros_identificaveis_e_estaveis_dao_alta(self):
        cs = [corte(5.0, parametros={'k1': 1.0 + 0.01 * i, 't2': 5.0}) for i in range(4)]
        d = mic.classificar(avaliacao(cortes=cs))
        self.assertEqual(d['confianca_parametros'], 'alta')
        self.assertEqual(d['detalhe_parametros']['fracao_identificavel'], 1.0)

    def test_parametros_instaveis_entre_cortes_baixam_o_nivel_mas_nao_invalidam_previsao(self):
        cs = [corte(5.0, parametros={'k1': v}) for v in (0.1, 2.0, 0.2, 3.0)]
        d = mic.classificar(avaliacao(cortes=cs))
        self.assertGreater(d['detalhe_parametros']['cv_parametros_max'], 0.5)
        self.assertNotEqual(d['confianca_parametros'], 'alta')
        self.assertTrue(d['validada'])
        self.assertEqual(d['nivel'], 'B')

    def test_parametros_no_limite_nao_contam_como_identificaveis(self):
        cs = [corte(5.0, no_limite=True), corte(5.0, no_limite=True), corte(5.0, no_limite=True), corte(5.0)]
        d = mic.classificar(avaliacao(cortes=cs))
        self.assertEqual(d['detalhe_parametros']['cortes_identificaveis'], 1)
        self.assertEqual(d['detalhe_parametros']['cortes_no_limite'], 3)
        self.assertTrue(any(l.startswith('parametros_no_limite_em') for l in d['limitacoes']))

    def test_parametros_fixos_sao_contados_e_declarados(self):
        d = mic.classificar(avaliacao(cfg_id='reg', familia='regularizado', fixos={'t1': 'por_corte', 'k2': 0.0}))
        self.assertEqual(d['confianca_parametros'], 'restrito')
        self.assertEqual(d['detalhe_parametros']['n_fixos'], 2)
        self.assertEqual(d['detalhe_parametros']['fracao_fixados'], 0.4)

    def test_sem_diagnostico_por_corte_e_nao_medido_e_limitacao(self):
        cs = [{**corte(5.0, nome=str(i)), 'identificavel': None} for i in range(4)]
        d = mic.classificar(avaliacao(cortes=cs, esquema='walk_forward_blocos'))
        self.assertEqual(d['confianca_parametros'], 'nao_medido')
        self.assertEqual(d['nivel'], 'B')
        self.assertIn('parametros_nao_medidos_neste_esquema', d['limitacoes'])


class TestEixoPreditivo(unittest.TestCase):

    def test_previsao_estavel_com_parametros_instaveis_e_B_com_limitacao(self):
        cs = [corte(5.0 + 0.1 * i, ident=False) for i in range(4)]
        d = mic.classificar(avaliacao(cortes=cs))
        self.assertEqual(d['nivel'], 'B')
        self.assertEqual(d['confianca_previsao'], 'alta')
        self.assertTrue(any(l.startswith('parametros_nao_identificaveis') for l in d['limitacoes']))

    def test_previsao_instavel_entre_cortes_e_C(self):
        cs = [corte(2.0, mae_media=15, mae_ultimo=15), corte(10.0, mae_media=15, mae_ultimo=15),
              corte(3.0, mae_media=15, mae_ultimo=15), corte(12.0, mae_media=15, mae_ultimo=15)]
        d = mic.classificar(avaliacao(cortes=cs))
        self.assertEqual(d['nivel'], 'C')
        self.assertFalse(d['validada'])
        self.assertTrue(any(m.startswith('instavel_entre_cortes') for m in d['motivos']))

    def test_candidato_regularizado_com_parametros_fixos_explicitos_e_B(self):
        d = mic.classificar(avaliacao(cfg_id='reg', familia='regularizado', fixos={'t2': 5}))
        self.assertEqual(d['nivel'], 'B')
        self.assertIn('parametros_fixos_explicitos: t2', d['limitacoes'])

    def test_A_exige_completo_sem_fixos_e_parametros_altos(self):
        d = mic.classificar(avaliacao(cfg_id='reg', familia='regularizado', fixos={}))
        self.assertEqual(d['nivel'], 'B')  # familia regularizada nunca e A
        self.assertEqual(mic.classificar(avaliacao())['nivel'], 'A')  # completo, identificavel, estavel
        cs = [{**corte(5.0, nome=str(i)), 'identificavel': None} for i in range(4)]
        self.assertEqual(mic.classificar(avaliacao(cortes=cs))['nivel'], 'B')  # sem diagnostico nao e A


class TestDadosEBaselines(unittest.TestCase):

    def test_dados_insuficientes_sao_C(self):
        d = mic.classificar(avaliacao(n=10))
        self.assertEqual(d['nivel'], 'C')
        self.assertTrue(any(m.startswith('dados_insuficientes') for m in d['motivos']))

    def test_cobertura_insuficiente_e_C(self):
        cs = [corte(5.0, n_teste=2), corte(5.0, n_teste=3), corte(5.0), corte(5.0)]
        d = mic.classificar(avaliacao(cortes=cs))
        self.assertEqual(d['nivel'], 'C')
        self.assertTrue(any(m.startswith('cobertura_insuficiente') for m in d['motivos']))

    def test_baselines_superiores_sao_C_com_referencia_simples(self):
        cs = [corte(12.0, mae_media=8.0, mae_ultimo=9.0, nome=str(i)) for i in range(4)]
        d = mic.classificar(avaliacao(cortes=cs))
        self.assertEqual(d['nivel'], 'C')
        self.assertEqual(d['resumo']['vitorias'], 0)
        self.assertEqual(d['referencia_operacional'], 'media_treino')

    def test_referencia_ultimo_valor_quando_e_melhor_baseline(self):
        cs = [corte(12.0, mae_media=20.0, mae_ultimo=9.0, nome=str(i)) for i in range(4)]
        self.assertEqual(mic.classificar(avaliacao(cortes=cs))['referencia_operacional'], 'ultimo_valor')

    def test_falha_de_ajuste_suspeita_de_fuga_e_nao_causal_sao_C(self):
        for kw in ({'falha': True}, {'fuga': True}, {'causal': False}):
            d = mic.classificar(avaliacao(**kw))
            self.assertEqual(d['nivel'], 'C', kw)
            self.assertFalse(d['validada'])

    def test_esquema_obrigatorio(self):
        a = avaliacao(); del a['esquema']
        with self.assertRaises(ValueError):
            mic.classificar(a)


class TestDeterminismoEComparacao(unittest.TestCase):

    def test_classificacao_e_deterministica_e_nao_muta_entrada(self):
        a = avaliacao(cfg_id='reg', familia='regularizado', fixos={'t2': 5})
        antes = copy.deepcopy(a)
        x = mic.classificar(a)
        self.assertEqual(a, antes)
        self.assertEqual(mic.assinatura(x), mic.assinatura(mic.classificar(copy.deepcopy(a))))

    def test_politica_hash_muda_com_limiares(self):
        pol2 = dict(mic.POLITICA, cv_max=0.1)
        self.assertNotEqual(mic.classificar(avaliacao())['politica_hash'],
                            mic.classificar(avaliacao(), pol=pol2)['politica_hash'])

    def test_politica_1_0_fica_disponivel_para_comparacao(self):
        import modelo_ir_confianca_v1 as v1
        cs = [corte(5.0, ident=False) for _ in range(4)]
        self.assertEqual(v1.classificar(avaliacao(cortes=cs))['nivel'], 'C')
        self.assertEqual(mic.classificar(avaliacao(cortes=cs))['nivel'], 'B')


class TestPrimeiraVersaoEAtualizacao(unittest.TestCase):

    def setUp(self):
        self.A = mic.classificar(avaliacao(cfg_id='A', cortes=[corte(5.0, parametros={'k1': 1.0}) for _ in range(4)]))
        self.B = mic.classificar(avaliacao(cfg_id='B', cortes=[corte(5.0, ident=False) for _ in range(4)]))
        self.C = mic.classificar(avaliacao(n=10))

    def test_primeira_versao_B_e_promovida_C_nao(self):
        self.assertEqual(mic.decidir_atualizacao(None, self.B)['acao'], 'promover')
        self.assertEqual(mic.decidir_atualizacao(None, self.C)['acao'], 'manter')

    def test_repeticao_sem_mudanca_e_idempotente(self):
        r = mic.decidir_atualizacao(self.B, mic.classificar(avaliacao(cfg_id='B', cortes=[corte(5.0, ident=False) for _ in range(4)])))
        self.assertEqual(r['acao'], 'sem_alteracao')

    def test_candidato_C_preserva_versao_valida(self):
        self.assertEqual(mic.decidir_atualizacao(self.B, self.C)['acao'], 'manter')

    def test_B_nao_substitui_A(self):
        self.assertEqual(mic.decidir_atualizacao(self.A, self.B)['acao'], 'manter')

    def test_esquemas_diferentes_nao_sao_comparados(self):
        wf = mic.classificar(avaliacao(cfg_id='W', esquema='walk_forward_blocos', n=100,
                                       cortes=[corte(5.0, nome=f'q{i}') for i in range(4)]))
        r = mic.decidir_atualizacao(self.B, wf)
        self.assertEqual(r['acao'], 'manter')
        self.assertIn('nao comparaveis', r['motivo'])

    def test_registo_versao_tem_campos_de_auditoria(self):
        comp = mic.decidir_atualizacao(None, self.B)
        reg = mic.registo_versao('Ski', 300, self.B, comp, 'teste')
        for chave in ('esquema', 'nivel', 'limitacoes', 'assinatura', 'politica_hash', 'acao', 'versao_codigo'):
            self.assertIn(chave, reg)


if __name__ == '__main__':
    unittest.main()
