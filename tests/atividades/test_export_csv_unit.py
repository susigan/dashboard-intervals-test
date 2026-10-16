"""Testes unitarios da serializacao CSV (export.atividades_csv). Dados ficticios."""
import csv, io, os, sys, unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
import export                                    # noqa: E402
EXCLUIDOS = export.CAMPOS_EXCLUIDOS
from fixtures_atividades import atividades_fixture  # noqa: E402

def parse(conteudo):
    texto = conteudo.decode('utf-8-sig')
    return list(csv.DictReader(io.StringIO(texto, newline='')))

class ExportCsvUnit(unittest.TestCase):
    def setUp(self):
        self.acts = atividades_fixture()

    def test_01_todas_sem_filtro(self):
        conteudo, info = export.atividades_csv(self.acts)
        self.assertEqual(info['n'], len(self.acts))
        self.assertEqual(len(parse(conteudo)), len(self.acts))

    def test_02_subconjunto_por_ids_mantem_ordem_e_reporta_ausentes(self):
        conteudo, info = export.atividades_csv(self.acts, ['i100003', 'i100001', 'nao-existe'])
        linhas = parse(conteudo)
        self.assertEqual([l['id'] for l in linhas], ['i100001', 'i100003'])
        self.assertEqual(info['ids_ausentes'], ['nao-existe'])

    def test_03_intervalo_de_datas_informado(self):
        _, info = export.atividades_csv(self.acts, ['i100002', 'i100003'])
        self.assertEqual((info['de'], info['ate']), ('2024-03-11', '2024-05-02'))

    def test_04_sem_registros_devolve_vazio(self):
        conteudo, info = export.atividades_csv(self.acts, [])
        self.assertEqual(conteudo, b'')
        self.assertEqual(info['n'], 0)

    def test_05_todos_os_registros_independente_de_paginacao(self):
        conteudo, info = export.atividades_csv(self.acts)
        self.assertEqual(info['n'], 1205)
        self.assertEqual(len(parse(conteudo)), 1205)

    def test_06_trimp_tl_e_zeros_explicitos_preservados(self):
        l = {r['id']: r for r in parse(export.atividades_csv(self.acts)[0])}
        self.assertEqual(l['i100001']['trimp'], '0.0')            # zero explicito
        self.assertEqual(l['i100001']['icu_training_load'], '0')  # zero explicito
        self.assertEqual(l['i100002']['trimp'], '47.39868')       # valor original, nao recalculado
        self.assertEqual(l['i100002']['icu_training_load'], '44')
        self.assertEqual(l['i100001']['SS'], '0.0')               # campo personalizado com zero

    def test_07_campos_personalizados_com_nome_original(self):
        cab = parse(export.atividades_csv(self.acts)[0])[0].keys()
        self.assertIn('Aerobic', cab)
        self.assertIn('telefone_fic', cab)

    def test_08_ausente_vazio_nulo_vazio(self):
        l = {r['id']: r for r in parse(export.atividades_csv(self.acts)[0])}
        self.assertEqual(l['i100004']['trimp'], '')          # chave ausente
        self.assertEqual(l['i100004']['icu_rpe'], '')        # nulo
        self.assertEqual(l['i100002']['tags'], '')           # tags nulas
        self.assertEqual(l['i100002']['average_heartrate'], '')  # chave ausente

    def test_09_acentos_virgulas_aspas_quebras_preservados(self):
        l = {r['id']: r for r in parse(export.atividades_csv(self.acts)[0])}
        self.assertEqual(l['i100001']['name'], 'Treino "Limiar", série 2\nlinha dois')
        self.assertEqual(l['i100004']['name'], 'Ski "São Bento" 1º dia')
        self.assertEqual(l['i100005']['name'], 'Remo ☃ ok')

    def test_10_protecao_contra_formulas(self):
        l = {r['id']: r for r in parse(export.atividades_csv(self.acts)[0])}
        self.assertEqual(l['i100002']['name'], "'=HYPERLINK(\"http://exemplo.invalido\",\"clique\")")
        self.assertEqual(l['i100003']['name'], "'-- Força, ação")
        self.assertEqual(l['i100003']['telefone_fic'], "'+00 000 000 000")
        # numeros com sinal nao sao alterados
        self.assertEqual(export.celula('-5'), '-5')
        self.assertEqual(export.celula('+3.5'), '+3.5')

    def test_11_estruturas_como_json_por_campo(self):
        l = {r['id']: r for r in parse(export.atividades_csv(self.acts)[0])}
        self.assertEqual(l['i100001']['tags'], '["Lactato"]')
        self.assertEqual(l['i100003']['tags'], '["a|b","Teste"]')

    def test_12_sem_cpr_sem_derivados_sem_segredos(self):
        conteudo, _ = export.atividades_csv(self.acts)
        cab = parse(conteudo)[0].keys()
        self.assertFalse(any(k in cab for k in EXCLUIDOS))
        self.assertFalse(any(c.startswith('_') for c in cab))
        self.assertNotIn('oauth_client_id', cab)
        self.assertNotIn('access_token', cab)
        self.assertNotIn(b'SEGREDO-FICTICIO', conteudo)
        self.assertNotIn(b'cliente-fic', conteudo)
        for k in EXCLUIDOS:
            self.assertNotIn(k.encode(), conteudo.split(b'\r\n')[0])

    def test_13_utf8_com_bom_e_crlf(self):
        conteudo, _ = export.atividades_csv(self.acts, ['i100001'])
        self.assertTrue(conteudo.startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'\r\n', conteudo)

    def test_14_nenhum_derivado_de_trimp_criado(self):
        cab = parse(export.atividades_csv(self.acts)[0])[0].keys()
        self.assertEqual(sorted(c for c in cab if 'trimp' in c.lower()), ['trimp'])

if __name__ == '__main__':
    unittest.main()
