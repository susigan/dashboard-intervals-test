"""Fim efetivo do esforco para HRR (utils/aquecimento_hrr.py).

SINTETICO: todos os sinais sao gerados aqui. Nao usa API, base de dados nem
credenciais. A validacao com streams reais esta' pendente (ver relatorio).

Objetivo: a referencia temporal do HRR vem da POTENCIA BRUTA dentro do
intervalo ja' detetado, e nao da potencia suavizada da deteção.
"""
import os
import random
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'utils'))
sys.path.insert(0, HERE)

import aquecimento_analyzer as aa  # noqa: E402
import aquecimento_hrr as hrr  # noqa: E402
import aquecimento_streams as aqs  # noqa: E402
from test_hrr_unit import sessao, streams_de  # noqa: E402

ALVOS = [80, 100, 120, 140, 160]
PROTO = aa.PROTOCOLOS['Bike']          # watts 80..160, tol 12, min_blocos 4
TOL_S = 1.0                            # tolerancia de tempo (amostras de 1 s)


def _sinal(decl=0.5, ruido=0.0, seed=1, passo=None, rec_fim=True):
    t, w, h, ends = sessao(ALVOS, rec_s=(150,) * 5, decl=(decl,) * 5, passo=passo,
                           rec_fim_w=60 if rec_fim else None)
    if ruido:
        rng = random.Random(seed)
        w = [x + rng.uniform(-ruido, ruido) for x in w]
    return t, w, h, ends


def _detetado(w):
    """Intervalos (i0, i1, alvo) da deteção atual, sem alterar nada."""
    return aqs._com_escala(1.0, lambda: aqs.detectar_escada(
        w, PROTO['watts'], PROTO['tol'], PROTO['min_blocos']))


class FimEfetivoSinalLimpo(unittest.TestCase):

    def test_sem_atraso_nos_degraus_80_a_140(self):
        t, w, h, ends = _sinal()
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Bike', aa.PROTOCOLOS, duracao_s=t[-1])
        for d, real in zip(r['degraus'][:4], ends[:4]):
            f = d['work']['fim_efetivo']
            self.assertIsNone(f['motivo'])
            self.assertAlmostEqual(f['t_fim_s'], real, delta=TOL_S, msg=f"alvo {d['alvo_w']}")

    def test_degrau_160_tem_fim_efetivo_mas_sem_hrr_por_ser_o_ultimo(self):
        # O ultimo degrau nao tem degrau seguinte: a recuperacao nao esta' definida
        # pela segmentacao, por isso nao ha HRR. O fim efetivo continua a existir.
        t, w, h, ends = _sinal()
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Bike', aa.PROTOCOLOS, duracao_s=t[-1])
        d = r['degraus'][4]
        self.assertAlmostEqual(d['work']['fim_efetivo']['t_fim_s'], ends[4], delta=TOL_S)
        self.assertIsNone(d['hrr60'])
        self.assertEqual(d['hrr60_motivo'], 'sem_fim_de_recuperacao')

    def test_deslocamento_detetado_e_registado(self):
        t, w, h, ends = _sinal()
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Bike', aa.PROTOCOLOS, duracao_s=t[-1])
        f = r['degraus'][0]['work']['fim_efetivo']
        self.assertEqual(f['fonte'], 'potencia_bruta_mediana')
        # deslocamento = fim efetivo - fim detetado
        self.assertAlmostEqual(f['t_fim_s'] - f['t_fim_detetado_s'], f['deslocamento_s'], delta=0.01)


class HRRAnaliticoConhecido(unittest.TestCase):

    def test_hrr60_e_hrr120_analiticos_para_decl_05(self):
        t, w, h, _ = _sinal(decl=0.5)
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Bike', aa.PROTOCOLOS, duracao_s=t[-1])
        for d in r['degraus'][:4]:
            self.assertAlmostEqual(d['hrr60'], 30.0, delta=0.01, msg=f"alvo {d['alvo_w']}")
            self.assertAlmostEqual(d['hrr120'], 60.0, delta=0.01, msg=f"alvo {d['alvo_w']}")

    def test_hrr_analitico_para_decl_08(self):
        t, w, h, _ = _sinal(decl=0.8)
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Bike', aa.PROTOCOLOS, duracao_s=t[-1])
        for d in r['degraus'][:4]:
            self.assertAlmostEqual(d['hrr60'], 48.0, delta=0.01)
            self.assertAlmostEqual(d['hrr120'], 96.0, delta=0.01)


class RuidoEOscilacoes(unittest.TestCase):

    def test_transicao_com_ruido_de_4_w(self):
        t, w, h, ends = _sinal(ruido=4.0)
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Bike', aa.PROTOCOLOS, duracao_s=t[-1])
        for d, real in zip(r['degraus'][:4], ends[:4]):
            f = d['work']['fim_efetivo']
            self.assertIsNotNone(f['t_fim_s'], f"alvo {d['alvo_w']}")
            self.assertAlmostEqual(f['t_fim_s'], real, delta=TOL_S)

    def test_oscilacao_breve_de_3_s_nao_desloca_o_fim(self):
        t, w, h, ends = _sinal()
        for k, ti in enumerate(t):
            if 100 <= ti < 103:
                w[k] = 40.0
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Bike', aa.PROTOCOLOS, duracao_s=t[-1])
        self.assertAlmostEqual(r['degraus'][0]['work']['fim_efetivo']['t_fim_s'], ends[0], delta=TOL_S)

    def test_descida_de_6_s_dentro_do_esforco_e_aceite(self):
        # LIMITACAO documentada: uma descida sustentada >= 5 s dentro do esforco
        # e' tomada como fim. Nao ha' como distinguir de uma pausa real.
        n = 300
        t = [float(i) for i in range(n)]
        w = [120.0] * n
        for k in range(100, 106):
            w[k] = 60.0
        f = hrr.fim_efetivo_esforco(t, w, 0, n, 120, 12)
        self.assertEqual(f['t_fim_s'], 100.0)


class AtrasoDaDeteccao(unittest.TestCase):

    def test_fim_efetivo_independe_do_fim_detetado_dentro_da_busca(self):
        t, w, h, ends = _sinal()
        i0, i1, alvo = _detetado(w)[0]
        real = ends[0]
        for deslocamento in (-1, 0, 2, 5):
            j1 = min(len(t), max(i0 + 1, i1 + deslocamento))
            f = hrr.fim_efetivo_esforco(t, w, i0, j1, alvo, PROTO['tol'])
            self.assertIsNotNone(f['t_fim_s'], f"i1 {deslocamento:+d}")
            self.assertAlmostEqual(f['t_fim_s'], real, delta=TOL_S, msg=f"i1 {deslocamento:+d}")

    def test_amostragem_irregular_dentro_de_2_s(self):
        t, w, h, ends = _sinal(passo=[1.0, 2.0, 1.0, 3.0])
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Bike', aa.PROTOCOLOS, duracao_s=t[-1])
        for d, real in zip(r['degraus'][:4], ends[:4]):
            f = d['work']['fim_efetivo']
            self.assertIsNotNone(f['t_fim_s'])
            self.assertLessEqual(abs(f['t_fim_s'] - real), 2.0, msg=f"alvo {d['alvo_w']}")


class CasosLimite(unittest.TestCase):

    def test_sem_queda_nao_inventa_timestamp(self):
        n = 200
        t = [float(i) for i in range(n)]
        f = hrr.fim_efetivo_esforco(t, [120.0] * n, 0, n, 120, 12)
        self.assertIsNone(f['t_fim_s'])
        self.assertEqual(f['motivo'], 'sem_queda_confirmada')

    def test_queda_no_inicio_e_recusada(self):
        n = 200
        t = [float(i) for i in range(n)]
        f = hrr.fim_efetivo_esforco(t, [60.0] * n, 0, 50, 120, 12)
        self.assertIsNone(f['t_fim_s'])
        self.assertEqual(f['motivo'], 'queda_no_inicio_do_esforco')

    def test_potencia_ausente_a_partir_da_queda(self):
        # Sem a queda observavel (potencia ausente a partir de 290 s), nao ha' fim.
        t, w, h, _ = _sinal()
        i0, i1, alvo = _detetado(w)[0]
        w2 = list(w)
        for k in range(len(w2)):
            if t[k] >= 290:
                w2[k] = None
        f = hrr.fim_efetivo_esforco(t, w2, i0, i1, alvo, PROTO['tol'])
        self.assertIsNone(f['t_fim_s'])
        self.assertIsNotNone(f['motivo'])

    def test_fim_indisponivel_propaga_motivo_sem_zero(self):
        t, w, h, _ = _sinal(rec_fim=False)   # ultimo degrau sem recuperacao
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Bike', aa.PROTOCOLOS, duracao_s=t[-1])
        d = r['degraus'][4]
        self.assertIsNone(d['hrr60'])
        self.assertIsNone(d['hrr120'])
        self.assertEqual(d['hrr60_motivo'], 'sem_fim_de_recuperacao')


class SobreposicaoProximoDegrau(unittest.TestCase):

    def test_hr_do_degrau_seguinte_nao_altera_hrr(self):
        t, w, h, ends = _sinal()
        base = hrr.hrr_por_degrau(streams_de(t, w, h), 'Bike', aa.PROTOCOLOS, duracao_s=t[-1])
        h2 = list(h)
        for k, ti in enumerate(t):
            if ti >= ends[0] + 140:   # apos a janela de HR_120 (125 s) do degrau 1
                h2[k] = 999.0
        alt = hrr.hrr_por_degrau(streams_de(t, w, h2), 'Bike', aa.PROTOCOLOS, duracao_s=t[-1])
        self.assertEqual(base['degraus'][0]['hrr60'], alt['degraus'][0]['hrr60'])
        self.assertEqual(base['degraus'][0]['hrr120'], alt['degraus'][0]['hrr120'])


if __name__ == '__main__':
    unittest.main()
