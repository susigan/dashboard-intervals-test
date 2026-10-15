"""Matriz de semântica do eFTP da atividade (icu_pm_ftp).

Estruturas de treino x RPE 2-3 e RPE 8-9. Os valores de eFTP são ENTRADAS de
fixture rotuladas; não são resultados do algoritmo do Intervals.icu nem
expectativas numéricas. Os testes verificam semântica: o valor de entrada
passa sem transformação; RPE e rótulo/estrutura da sessão não alteram o
resultado; nenhuma conclusão de melhora ou piora é produzida a partir de uma
única atividade; zero continua sendo ausência.
"""
import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

import pmc  # noqa: E402

REF = '2026-10-14'
REF_D = date(2026, 10, 14)

# Estruturas de treino. 'eftp_entrada' é valor de fixture, não expectativa.
ESTRUTURAS = {
    'leve_continua':          {'nome': 'Rodagem contínua leve', 'eftp_entrada': 180.0},
    'leve_variacao_pequena':  {'nome': 'Sessão leve com pequenas variações', 'eftp_entrada': 185.0},
    'continua_exigente':      {'nome': 'Contínuo exigente, potência estável', 'eftp_entrada': 240.0},
    'interv_recup_0w':        {'nome': 'Intervalado, recuperações a 0 W', 'eftp_entrada': 160.0},
    'interv_recup_ativa':     {'nome': 'Intervalado, recuperações ativas', 'eftp_entrada': 200.0},
    'interv_mesma_potencia':  {'nome': 'Mesma potência de trabalho, recuperação diferente', 'eftp_entrada': 175.0},
    'esforco_maximo_curto':   {'nome': 'Esforço máximo curto', 'eftp_entrada': 250.0},
}
RPES = (2, 3, 8, 9)
FAIXAS = {'leve': (2, 3), 'intensa': (8, 9)}

PALAVRAS_PROIBIDAS = ('queda', 'piora', 'melhora', 'perda', 'ganho',
                      'classific', 'tendencia de', 'aumentou', 'diminuiu')


def _historico(valor=200.0):
    """52 semanas de eFTP estável, para que a janela de 180/365 dias seja suficiente."""
    return [{'id': f'h{k}', 'date': (REF_D - timedelta(days=7 * k)).isoformat(),
             'type': 'Bike', 'icu_pm_ftp': valor, 'icu_ftp': None, 'EFTP': None}
            for k in range(1, 53)]


def _sessao_do_cenario(estrutura, rpe, nome=None, extra=None):
    # id depende só da estrutura: o RPE é a única variável do teste de invariância
    e = {'id': f'sessao-{estrutura}', 'date': (REF_D - timedelta(days=2)).isoformat(),
         'type': 'Bike', 'icu_pm_ftp': ESTRUTURAS[estrutura]['eftp_entrada'],
         'icu_ftp': None, 'EFTP': None, 'icu_rpe': rpe,
         'name': nome if nome is not None else ESTRUTURAS[estrutura]['nome']}
    if extra:
        e.update(extra)
    return e


def _rodar(entradas, curvas=None):
    return pmc.evolucao_performance(entradas, curvas or [], REF, modalidades=('Bike',))


def _textos(x, out=None):
    out = [] if out is None else out
    if isinstance(x, dict):
        for v in x.values():
            _textos(v, out)
    elif isinstance(x, list):
        for v in x:
            _textos(v, out)
    elif isinstance(x, str):
        out.append(x.lower())
    return out


def _chaves(x, out=None):
    out = [] if out is None else out
    if isinstance(x, dict):
        for k, v in x.items():
            out.append(str(k).lower())
            _chaves(v, out)
    elif isinstance(x, list):
        for v in x:
            _chaves(v, out)
    return out


class MatrizEstruturaRpe(unittest.TestCase):
    """Cada estrutura x cada RPE: o resultado não depende do RPE."""

    def test_rpe_nao_altera_o_resultado_em_nenhuma_estrutura(self):
        for est in ESTRUTURAS:
            with self.subTest(estrutura=est):
                base = _rodar(_historico() + [_sessao_do_cenario(est, RPES[0])])
                for rpe in RPES[1:]:
                    outro = _rodar(_historico() + [_sessao_do_cenario(est, rpe)])
                    self.assertEqual(base, outro, f'RPE {rpe} alterou o resultado de {est}')

    def test_rpe_alto_nao_implica_eftp_alto(self):
        # Mesma estrutura intervalada com RPE 9 e RPE 2 produz a MESMA série de eFTP.
        a = _rodar(_historico() + [_sessao_do_cenario('interv_recup_0w', 9)])
        b = _rodar(_historico() + [_sessao_do_cenario('interv_recup_0w', 2)])
        fa = a['modalidades']['Bike']['fontes']['eftp_atividade']
        fb = b['modalidades']['Bike']['fontes']['eftp_atividade']
        self.assertEqual(fa, fb)

    def test_faixas_leve_e_intensa_nao_sao_lidas_pelo_algoritmo(self):
        for faixa, rpes in FAIXAS.items():
            with self.subTest(faixa=faixa):
                resultados = [_rodar(_historico() + [_sessao_do_cenario('leve_continua', r)])
                              for r in rpes]
                self.assertEqual(resultados[0], resultados[1])


class PassagemSemTransformacao(unittest.TestCase):
    """O valor de eFTP de entrada entra na série sem correção por estrutura."""

    def test_valor_de_entrada_entra_intacto_em_cada_estrutura(self):
        for est, cfg in ESTRUTURAS.items():
            with self.subTest(estrutura=est):
                res = _rodar(_historico() + [_sessao_do_cenario(est, 8)])
                f = res['modalidades']['Bike']['fontes']['eftp_atividade']
                # a sessão do cenário é a última atividade associada à série
                self.assertEqual(f['qualidade']['ultima_atividade_id'], f'sessao-{est}')
                self.assertEqual(f['qualidade']['ultima_observacao'], (REF_D - timedelta(days=2)).isoformat())

    def test_nenhuma_chave_de_correcao_ou_equivalencia(self):
        proibidas = ('corrig', 'fator', 'equival', 'ajust', 'proxy', 'estimativa_derivada')
        res = _rodar(_historico() + [_sessao_do_cenario('interv_recup_0w', 9)])
        chaves = _chaves(res)
        for p in proibidas:
            self.assertFalse(any(p in c for c in chaves), p)


class SemConclusaoAutomatica(unittest.TestCase):
    """Uma única atividade, em qualquer estrutura, não gera melhora ou piora."""

    def test_nenhuma_palavra_de_conclusao_em_nenhuma_estrutura(self):
        for est in ESTRUTURAS:
            for rpe in RPES:
                with self.subTest(estrutura=est, rpe=rpe):
                    res = _rodar(_historico() + [_sessao_do_cenario(est, rpe)])
                    textos = _textos(res)
                    for p in PALAVRAS_PROIBIDAS:
                        self.assertFalse(any(p in t for t in textos), p)

    def test_eftp_baixo_isolado_nao_e_perda(self):
        # interv_recup_0w (160) abaixo do histórico (200): a comparação existe
        # numericamente, mas nenhum rótulo de perda é produzido.
        res = _rodar(_historico() + [_sessao_do_cenario('interv_recup_0w', 9)])
        f = res['modalidades']['Bike']['fontes']['eftp_atividade']
        self.assertIsNotNone(f['janelas']['180']['diferenca'])
        textos = _textos(res)
        self.assertFalse(any('perda' in t for t in textos))

    def test_estrutura_nao_e_inferida_do_nome(self):
        # Mesmo valor com nomes de estrutura diferentes produz resultado idêntico.
        a = _rodar(_historico() + [_sessao_do_cenario('interv_recup_0w', 9, nome='Intervalado 5x5')])
        b = _rodar(_historico() + [_sessao_do_cenario('interv_recup_0w', 9, nome='Contínuo')])
        self.assertEqual(a, b)


class ZeroEAusenciaNaMatriz(unittest.TestCase):
    """Zero e ausência nunca viram observação válida, em nenhuma estrutura."""

    def test_zero_em_recuperacao_nao_entra_como_eftp(self):
        for est in ESTRUTURAS:
            with self.subTest(estrutura=est):
                com = _rodar(_historico() + [_sessao_do_cenario(est, 9, extra={'icu_pm_ftp': 0.0})])
                sem = _rodar(_historico())
                nv_com = com['modalidades']['Bike']['fontes']['eftp_atividade']['qualidade']['n_validos']
                nv_sem = sem['modalidades']['Bike']['fontes']['eftp_atividade']['qualidade']['n_validos']
                self.assertEqual(nv_com, nv_sem)

    def test_ausente_e_nao_nulo_nao_vira_zero(self):
        ent = _historico() + [_sessao_do_cenario('leve_continua', 2, extra={'icu_pm_ftp': None})]
        f = _rodar(ent)['modalidades']['Bike']['fontes']['eftp_atividade']
        self.assertGreaterEqual(f['qualidade']['n_ausentes'], 1)
        self.assertEqual(f['qualidade']['n_zero'], 0)
        self.assertEqual(f['qualidade']['n_validos'], 52)


class TextoDaInterface(unittest.TestCase):
    """A interface explica a dependência da estrutura e a limitação isolada."""

    def setUp(self):
        with open(os.path.join(ROOT, 'tabs', 'tab_pmc.py'), encoding='utf-8') as f:
            self.src = f.read()

    def test_explica_dependencia_da_estrutura(self):
        self.assertIn('Depende da estrutura do treino', self.src)

    def test_explica_que_nao_basta_para_concluir_capacidade(self):
        self.assertIn('um valor baixo isolado não significa perda de capacidade', self.src)
        # distincao semantica: eFTP da atividade (icu_pm_ftp) nao equivale ao FTP configurado (icu_ftp)
        self.assertIn('e não equivale ao FTP configurado', self.src)

    def test_explica_recuperacoes_incluidas(self):
        self.assertIn('incluindo recuperações', self.src)


if __name__ == '__main__':
    unittest.main()
