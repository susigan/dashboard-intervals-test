"""Consumidores FTP/eFTP: rótulos e colunas não misturam icu_pm_ftp com icu_ftp.

Testes textuais sobre o código-fonte (não executam a UI) e comportamento de
helpers.get_ftp, que não foi alterado.
"""
import os
import unittest

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _ler(rel):
    with open(os.path.join(RAIZ, rel), encoding='utf-8') as f:
        return f.read()


class DetalheFtpSeparado(unittest.TestCase):
    def setUp(self):
        self.src = _ler('tabs/tab_detalhe.py')

    def test_nao_existe_card_ftp_misto(self):
        self.assertNotIn("['FTP',(a.icu_pm_ftp||a.icu_ftp", self.src)

    def test_cards_separados_com_rotulos_explicitos(self):
        self.assertIn("['eFTP da atividade',a.icu_pm_ftp", self.src)
        self.assertIn("['FTP configurado',a.icu_ftp", self.src)

    def test_card_ausente_mostra_traco_nao_zero(self):
        # ausencia deve aparecer como '—', nunca como '0 W'
        self.assertIn("'—'", self.src)
        self.assertNotIn("(a.icu_pm_ftp||a.icu_ftp||0)", self.src)

    def test_kj_acima_do_limiar_referencia_explicita(self):
        self.assertNotIn("['kJ > FTP'", self.src)
        self.assertIn('ref. Intervals.icu, não confirmada', self.src)


class AtividadesFtpSeparado(unittest.TestCase):
    def setUp(self):
        self.src = _ler('tabs/tab_atividades.py')

    def test_coluna_mista_removida(self):
        self.assertNotIn("'ftp': num(a.get('icu_pm_ftp'))", self.src)
        self.assertNotIn("['ftp','FTP',1]", self.src)

    def test_colunas_separadas(self):
        self.assertIn("'eftp_atividade': num(a.get('icu_pm_ftp'))", self.src)
        self.assertIn("'ftp_configurado': num(a.get('icu_ftp'))", self.src)
        self.assertIn("['eftp_atividade','eFTP ativ.',1]", self.src)
        self.assertIn("['ftp_configurado','FTP conf.',1]", self.src)

    def test_nenhum_valor_de_eftp_vira_ftp_configurado(self):
        # a chave de icu_ftp nunca lê icu_pm_ftp e vice-versa
        self.assertNotIn("num(a.get('icu_pm_ftp')) or num(a.get('icu_ftp'))", self.src)


class RecordesProxyRotulado(unittest.TestCase):
    def test_proxy_20min_nao_se_apresenta_como_ftp(self):
        src = _ler('tabs/tab_recordes.py')
        self.assertNotIn("['FTP 20min'", src)
        self.assertIn('Proxy FTP 20min', src)
        self.assertIn('estimativa', src)


class DocumentacaoFtp(unittest.TestCase):
    def test_helpers_get_ftp_docstring_nao_chama_rolling(self):
        src = _ler('helpers.py')
        self.assertNotIn('Morton 3P > rolling', src)
        self.assertIn('Não é FTP medido', src)

    def test_helpers_get_ftp_comportamento_inalterado(self):
        from helpers import ActivityProcessor
        p = ActivityProcessor(default_ftp=200)
        self.assertEqual(p.get_ftp({'icu_pm_ftp': 227, 'icu_ftp': 250}), 227)
        self.assertEqual(p.get_ftp({'icu_ftp': 250}), 250)
        self.assertEqual(p.get_ftp({'icu_pm_ftp': 0, 'icu_ftp': 0}), 200)
        self.assertEqual(p.get_ftp({}), 200)

    def test_eftp_da_atividade_docstring_sem_afirmacao_de_medido(self):
        src = _ler('tabs/tab_pmc.py')
        self.assertNotIn('eFTP da propria Intervals.icu', src)
        self.assertIn('semântica não confirmada', src)


class MutacaoReintroduzMistura(unittest.TestCase):
    """Prova que o teste detecta a regressão: uma cópia com a mistura falha."""

    def test_detector_pega_a_mistura_original(self):
        original = "['FTP',(a.icu_pm_ftp||a.icu_ftp||0)+' W'],"
        self.assertIn("['FTP',(a.icu_pm_ftp||a.icu_ftp", original)
        self.assertNotIn("['FTP',(a.icu_pm_ftp||a.icu_ftp", _ler('tabs/tab_detalhe.py'))


if __name__ == '__main__':
    unittest.main()
