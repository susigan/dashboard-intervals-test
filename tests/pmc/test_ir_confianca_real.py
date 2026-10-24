"""Classificacao politica 2.0 sobre resultados REAIS das rondas 3 e 4 (fixture gerada pelo adaptador, sem credenciais).
Reprodutibilidade e comparacao com a politica 1.0 sobre a MESMA entrada. Nao recalcula ajustes."""
import json, os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')
import modelo_ir_confianca as v2  # noqa: E402
import modelo_ir_confianca_v1 as v1  # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'ir_confianca_ronda_real.json')


def _itens():
    return json.load(open(FIX, encoding='utf-8'))['itens']


def _chave(av):
    m = av['metadados']
    return (m['ronda'], m['celula'], m['chave'])


class TestClassificacaoReal(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.itens = _itens()
        cls.d2 = {_chave(a): v2.classificar(a) for a in cls.itens}
        cls.d1 = {_chave(a): v1.classificar({k: v for k, v in a.items() if k != 'metadados'}) for a in cls.itens}

    def test_numero_de_configuracoes_e_esquema(self):
        self.assertEqual(len(self.itens), 45)
        self.assertTrue(all(a['esquema'] == 'cortes_treino_teste' for a in self.itens))

    def test_distribuicao_de_niveis_politica_2(self):
        from collections import Counter
        cont = Counter(d['nivel'] for d in self.d2.values())
        self.assertEqual(cont, Counter({'C': 31, 'B': 14}))  # nenhuma A: nenhuma configuracao e fixa-livre-identificavel

    def test_politica_1_distribuicao_inalterada_para_reproducao(self):
        from collections import Counter
        self.assertEqual(Counter(d['nivel'] for d in self.d1.values()), Counter({'C': 44, 'B': 1}))

    def test_mudancas_v1_para_v2_sao_todas_C_para_B(self):
        mud = [(k, self.d1[k]['nivel'], self.d2[k]['nivel']) for k in self.d1 if self.d1[k]['nivel'] != self.d2[k]['nivel']]
        self.assertEqual(len(mud), 13)
        self.assertTrue(all(a == 'C' and b == 'B' for _, a, b in mud))
        self.assertFalse(any(self.d2[k]['nivel'] == 'C' and self.d1[k]['nivel'] == 'B' for k in self.d1))

    def test_candidata_ski_mediana5_D_T1fixo_e_B_nas_duas_politicas(self):
        k = (4, 'Ski-300', 'mediana5|D_T1fixo')
        self.assertEqual(self.d1[k]['nivel'], 'B')
        self.assertEqual(self.d2[k]['nivel'], 'B')
        self.assertEqual(self.d2[k]['confianca_parametros'], 'restrito')
        self.assertTrue(self.d2[k]['validada'])
        self.assertIn('parametros_fixos_explicitos: t1', self.d2[k]['limitacoes'])
        self.assertEqual(self.d2[k]['resumo']['vitorias'], 4)

    def test_ski_completo_4_de_4_fica_B_com_parametros_baixos_e_nao_A(self):
        d = self.d2[(3, 'Ski-300', 'ano_anterior|A_completo')]
        self.assertEqual(d['nivel'], 'B')
        self.assertEqual(d['confianca_parametros'], 'baixa')
        self.assertEqual(d['confianca_previsao'], 'alta')
        self.assertTrue(any(l.startswith('parametros_nao_identificaveis') for l in d['limitacoes']))

    def test_bike_300_todas_C(self):
        bike = [d for k, d in self.d2.items() if k[1] == 'Bike-300']
        self.assertTrue(bike)
        self.assertTrue(all(d['nivel'] == 'C' for d in bike))

    def test_reproduzivel(self):
        outra = {_chave(a): v2.assinatura(v2.classificar(a)) for a in _itens()}
        self.assertEqual({k: v2.assinatura(d) for k, d in self.d2.items()}, outra)


if __name__ == '__main__':
    unittest.main()
