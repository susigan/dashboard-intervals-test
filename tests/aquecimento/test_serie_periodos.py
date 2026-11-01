"""Serie agregada por periodo: media de todas as sessoes por escalao (sem N)."""
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
RAIZ = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(RAIZ, 'utils'))
sys.path.insert(0, RAIZ)

import aquecimento_comparativo as cmp  # noqa: E402


def b(aid, data, w, hr, num=1):
    return {'activity_id': aid, 'data': data, 'watts_alvo': w, 'watts_real': float(w),
            'bloco_num': num, 'interval_num': num, 'hr_avg': hr}


BLOCOS = [
    b('a', '2026-09-05', 160, 140.0),
    b('b', '2026-09-20', 160, 150.0),
    b('c', '2026-10-03', 160, 160.0),
    b('c', '2026-10-03', 140, 130.0, num=2),
    b('d', '2026-10-10', 140, None),        # sem valor: nao conta
    b('e', '2026-10-11', 100, 0.0),         # zero registado: duvidoso, fica fora
]


class SeriePorPeriodo(unittest.TestCase):

    def test_mes_media_de_todas_as_sessoes_do_periodo(self):
        r = cmp.serie_por_periodo(BLOCOS, 'hr', 'mes', '2026-01-01', '2026-12-31')
        por = {s['periodo']: {p['watts_alvo']: p['valor'] for p in s['pontos']}
               for s in r['series']}
        self.assertEqual(por['2026-09'][160], 145.0)     # (140+150)/2
        self.assertEqual(por['2026-10'][160], 160.0)
        self.assertEqual(por['2026-10'][140], 130.0)

    def test_nao_expoe_n_nem_sessoes_por_linha(self):
        r = cmp.serie_por_periodo(BLOCOS, 'hr', 'mes', '2026-01-01', '2026-12-31')
        for s in r['series']:
            for p in s['pontos']:
                self.assertEqual(set(p.keys()), {'watts_alvo', 'valor'})

    def test_todo_historico_e_uma_unica_linha(self):
        r = cmp.serie_por_periodo(BLOCOS, 'hr', 'todo', '2026-01-01', '2026-12-31')
        self.assertEqual([s['periodo'] for s in r['series']], ['todo'])
        por = {p['watts_alvo']: p['valor'] for p in r['series'][0]['pontos']}
        self.assertEqual(por[160], 150.0)                 # (140+150+160)/3
        self.assertEqual(por[140], 130.0)

    def test_rolling_junta_os_periodos_da_janela(self):
        r = cmp.serie_por_periodo(BLOCOS, 'hr', 'mes', '2026-01-01', '2026-12-31', rolling=2)
        por = {s['periodo']: {p['watts_alvo']: p['valor'] for p in s['pontos']}
               for s in r['series']}
        self.assertEqual(por['2026-10'][160], 150.0)      # pool 140,150,160

    def test_trimestre_semestre_e_ano(self):
        self.assertEqual(cmp.chave_periodo_amplo('2026-05-02', 'trimestre'), '2026-T2')
        self.assertEqual(cmp.chave_periodo_amplo('2026-05-02', 'semestre'), '2026-S1')
        self.assertEqual(cmp.chave_periodo_amplo('2026-11-02', 'semestre'), '2026-S2')
        self.assertEqual(cmp.chave_periodo_amplo('2026-11-02', 'ano'), '2026')

    def test_escalao_sem_dados_nao_aparece(self):
        r = cmp.serie_por_periodo(BLOCOS, 'hr', 'mes', '2026-01-01', '2026-12-31')
        self.assertNotIn(100, r['escaloes'])              # so tinha zero

    def test_periodo_ou_rolling_invalidos(self):
        with self.assertRaises(ValueError):
            cmp.serie_por_periodo(BLOCOS, 'hr', 'dia', '2026-01-01', '2026-12-31')
        with self.assertRaises(ValueError):
            cmp.serie_por_periodo(BLOCOS, 'hr', 'mes', '2026-01-01', '2026-12-31', rolling=0)

    def test_intervalo_dos_dados(self):
        self.assertEqual(cmp.intervalo_dados(BLOCOS), ('2026-09-05', '2026-10-11'))
        self.assertEqual(cmp.intervalo_dados([]), (None, None))


if __name__ == '__main__':
    unittest.main()


class PainelTendenciaPorPeriodo(unittest.TestCase):
    """Painel unico de comparacao: periodos em janelas, sem N, sem data, endpoint certo."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(RAIZ, 'tabs', 'tab_metabol.py'), encoding='utf-8') as f:
            cls.src = f.read()
        cls.bloco = cls.src.split('/* AQCMP_BEGIN */')[1].split('/* AQCMP_END */')[0]

    def test_marcadores_e_endpoint(self):
        self.assertNotIn('AQPT_BEGIN', self.src)
        self.assertIn('/api/aquecimento/tendencia_periodos', self.bloco)
        self.assertNotIn('/api/aquecimento/comparativo', self.bloco)

    def test_grafico_nao_mostra_n(self):
        import re
        self.assertIsNone(re.search(r'\.n\b', self.bloco))
        self.assertNotIn('n=', self.bloco)

    def test_periodos_em_janelas_oferecidos(self):
        for m in ('3', '6', '12', '24', '36', 'todo'):
            self.assertIn(f'data-meses="{m}"', self.src)
        for antigo in ('mes', 'trimestre', 'semestre'):
            self.assertNotIn(f'value="{antigo}"', self.bloco)

    def test_metricas_padrao_hr_smo2_resp(self):
        import re
        m = re.search(r"const AQC_LIG = \{([^}]*)\}", self.bloco)
        self.assertIsNotNone(m)
        ligadas = set(re.findall(r"(\w+):true", m.group(1)))
        self.assertEqual(ligadas, {'hr', 'smo2', 'resp'})
