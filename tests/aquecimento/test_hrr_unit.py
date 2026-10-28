"""HRR60 / HRR120 por degrau de Aquecimento (utils/aquecimento_hrr.py).

SINTETICO: todos os sinais sao gerados nestes testes, com bordas reais
conhecidas. Nao usa a API, a base de dados nem dados pessoais. A validacao
com streams reais e' feita a' parte (ver relatorio).

Tolerancia: a borda de fim do esforco e' detetada com 1-2 s de atraso
(medido), o que com queda de 0,5 bpm/s da' cerca de 1 bpm de erro.
"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'utils'))

import aquecimento_hrr as hrr  # noqa: E402

PROTO = {'Teste': {'watts': [120, 140, 160], 'tol': 15, 'min_blocos': 3}}
TOL_BPM = 1.5


def sessao(alvos, work_s=300, rec_s=(150, 150, 150), rec_w=60, decl=(0.5, 0.5, 0.5),
           P=150.0, passo=None, rec_fim_w=None):
    """Gera (time, watts, hr, tends) com bordas reais.

    decl[n]: queda de FC por segundo na recuperacao do degrau n (negativo = sobe).
    passo: lista de passos de tempo (s) ciclica; por omissao 1 s.
    Devolve tambem os instantes reais de fim de esforco de cada degrau.
    """
    segs = []
    for n, a in enumerate(alvos):
        segs.append(('work', n, a, work_s))
        if n < len(alvos) - 1 or rec_fim_w is not None:
            segs.append(('rec', n, rec_w, rec_s[n]))
    t_ini, t_end_real, tau_ini = [], [], 0.0
    for kind, n, _, dur in segs:
        if kind == 'work':
            t_ini.append(tau_ini)
        tau_ini += dur
        if kind == 'work':
            t_end_real.append(tau_ini)
    # amostragem
    tempo, watts, hr = [], [], []
    tau, k = 0.0, 0
    passo = passo or [1.0]
    fim_total = sum(s[3] for s in segs)
    while tau < fim_total:
        # segmento atual
        acc, seg = 0.0, None
        for s in segs:
            if tau < acc + s[3]:
                seg = s
                break
            acc += s[3]
        kind, n, val, dur = seg
        if kind == 'work':
            w, h = val, P
        else:
            w = rec_w
            h = P - decl[n] * (tau - t_end_real[n])
        tempo.append(tau)
        watts.append(float(w))
        hr.append(float(h))
        tau += passo[k % len(passo)]
        k += 1
    return tempo, watts, hr, t_end_real


def streams_de(tempo, watts, hr):
    return {'time': tempo, 'watts': watts, 'heartrate': hr}


class CalculoSintetico(unittest.TestCase):

    def test_queda_linear_da_hrr60_e_hrr120(self):
        t, w, h, ends = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        self.assertEqual(r['status'], 'ok')
        for d in r['degraus'][:2]:
            self.assertAlmostEqual(d['hrr60'], 30.0, delta=TOL_BPM)
            self.assertAlmostEqual(d['hrr120'], 60.0, delta=TOL_BPM)
            self.assertIsNone(d['hrr60_motivo'])
            self.assertIsNone(d['hrr120_motivo'])

    def test_ultimo_degrau_sem_recuperacao_seguinte(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150))
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        ult = r['degraus'][-1]
        self.assertIsNone(ult['hrr60'])
        self.assertEqual(ult['hrr60_motivo'], 'sem_fim_de_recuperacao')
        self.assertEqual(ult['hrr120_motivo'], 'sem_fim_de_recuperacao')

    def test_negativos_sao_preservados(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(-0.2, 0.5, 0.5))
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        d1 = r['degraus'][0]
        self.assertIsNotNone(d1['hrr60'])
        self.assertLess(d1['hrr60'], 0)
        self.assertAlmostEqual(d1['hrr60'], -12.0, delta=TOL_BPM)

    def test_associacao_cada_recuperacao_ao_degrau_anterior(self):
        # degrau 1 cai 0,5 bpm/s; degrau 2 sobe 0,2 bpm/s. O resultado do
        # degrau 1 nao pode ser afetado pelas amostras do degrau 2.
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, -0.2, 0.5))
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        self.assertAlmostEqual(r['degraus'][0]['hrr60'], 30.0, delta=TOL_BPM)
        self.assertAlmostEqual(r['degraus'][1]['hrr60'], -12.0, delta=TOL_BPM)

    def test_recuperacao_curta_nao_extrapola_hrr120(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(90, 150, 150), decl=(0.5, 0.5, 0.5))
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        d1 = r['degraus'][0]
        self.assertAlmostEqual(d1['hrr60'], 30.0, delta=TOL_BPM)
        self.assertIsNone(d1['hrr120'])
        self.assertEqual(d1['hrr120_motivo'], 'rec_curta')

    def test_pausa_longa_nao_e_recuperacao(self):
        # Pausa de 600 s entre degraus. Nao ha limite de pausa implementado no
        # detetor: este teste so' garante que a recuperacao longa nao produz
        # HRR com dados de pausa indevidos (o degrau fica sem HRR se faltar
        # o degrau seguinte). Nao valida o limite de 200 s, que nao existe.
        proto1 = {'Teste': {'watts': [120, 140, 160], 'tol': 15, 'min_blocos': 1}}
        t, w, h, _ = sessao([120, 140, 160], rec_s=(600, 600, 600), decl=(0.5, 0.5, 0.5))
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', proto1, duracao_s=t[-1])
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(len(r['degraus']), 1)
        self.assertIsNone(r['degraus'][0]['hrr60'])
        self.assertEqual(r['degraus'][0]['hrr60_motivo'], 'sem_fim_de_recuperacao')


class AusenciaEZeros(unittest.TestCase):

    def test_hr_nula_nao_vira_zero(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        h2 = list(h)
        for k, ti in enumerate(t):
            if 360 <= ti <= 370:  # dentro da janela de HR_60 do degrau 1
                h2[k] = None
        r = hrr.hrr_por_degrau(streams_de(t, w, h2), 'Teste', PROTO, duracao_s=t[-1])
        d1 = r['degraus'][0]
        self.assertIsNone(d1['hrr60'])
        self.assertNotEqual(d1['hrr60'], 0)
        self.assertEqual(d1['hrr60_motivo'], 'dados_insuficientes')

    def test_lacuna_temporal_e_identificada(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        keep = [k for k, ti in enumerate(t) if not (340 <= ti <= 365)]
        t2 = [t[k] for k in keep]; w2 = [w[k] for k in keep]; h2 = [h[k] for k in keep]
        r = hrr.hrr_por_degrau(streams_de(t2, w2, h2), 'Teste', PROTO, duracao_s=t2[-1])
        d1 = r['degraus'][0]
        self.assertIsNone(d1['hrr60'])
        self.assertEqual(d1['hrr60_motivo'], 'lacuna_sem_dados')
        self.assertGreater(r['n_lacunas'], 0)

    def test_zeros_de_fc_nao_contaminam(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        h2 = list(h)
        n_zeros = 0
        for k, ti in enumerate(t):
            if 356 <= ti <= 358:
                h2[k] = 0.0
                n_zeros += 1
        r = hrr.hrr_por_degrau(streams_de(t, w, h2), 'Teste', PROTO, duracao_s=t[-1])
        d1 = r['degraus'][0]
        self.assertEqual(d1['hr_60']['n_zeros'], n_zeros)
        self.assertAlmostEqual(d1['hrr60'], 30.0, delta=TOL_BPM)

    def test_potencia_zero_na_recuperacao_nao_e_exigida(self):
        # recuperacao a 60 W (nao zero): o calculo tem de funcionar igual
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), rec_w=60, decl=(0.5, 0.5, 0.5))
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        d1 = r['degraus'][0]
        self.assertAlmostEqual(d1['hrr60'], 30.0, delta=TOL_BPM)
        self.assertTrue(d1['rec']['fase_confirmada'])


class Tempo(unittest.TestCase):

    def test_amostragem_irregular_usa_tempo_nao_indice(self):
        passo = [1.0, 2.0, 1.0, 3.0]
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5),
                            passo=passo)
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        d1 = r['degraus'][0]
        self.assertIsNotNone(d1['hrr60'])
        self.assertAlmostEqual(d1['hrr60'], 30.0, delta=TOL_BPM)
        self.assertAlmostEqual(d1['hrr120'], 60.0, delta=TOL_BPM)

    def test_sem_timestamps_falha_explicitamente(self):
        t, w, h, _ = sessao([120, 140, 160])
        r = hrr.hrr_por_degrau({'watts': w, 'heartrate': h}, 'Teste', PROTO)
        self.assertEqual(r['status'], 'sem_timestamps')
        self.assertEqual(r['degraus'], [])

    def test_timestamps_nao_crescentes_falham(self):
        t, w, h, _ = sessao([120, 140, 160])
        t[100], t[101] = t[101], t[100]
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO)
        self.assertEqual(r['status'], 'timestamps_invalidos')

    def test_tamanhos_diferentes_falham(self):
        t, w, h, _ = sessao([120, 140, 160])
        r = hrr.hrr_por_degrau(streams_de(t, w[:-5], h), 'Teste', PROTO)
        self.assertEqual(r['status'], 'erro_streams')

    def test_sem_hr_e_sem_watts_falham_explicitamente(self):
        t, w, h, _ = sessao([120, 140, 160])
        self.assertEqual(hrr.hrr_por_degrau({'time': t, 'watts': w}, 'Teste', PROTO)['status'], 'sem_hr')
        self.assertEqual(hrr.hrr_por_degrau({'time': t, 'heartrate': h}, 'Teste', PROTO)['status'], 'sem_watts')

    def test_sem_degraus_e_sem_protocolo(self):
        t = [float(i) for i in range(600)]
        r = hrr.hrr_por_degrau(streams_de(t, [100.0] * 600, [120.0] * 600), 'Teste', PROTO)
        self.assertEqual(r['status'], 'sem_degraus')
        self.assertEqual(hrr.hrr_por_degrau(streams_de(t, [100.0] * 600, [120.0] * 600),
                                            'Outra', PROTO)['status'], 'sem_protocolo')


if __name__ == '__main__':
    unittest.main()


def _serie_por_tempo(t, fim_work, v_work, v_rec):
    """Serie constante v_work ate' ao fim do primeiro WORK, depois v_rec."""
    return [v_work if ti <= fim_work else v_rec for ti in t]


class IndicadoresFisiologicos(unittest.TestCase):
    """SmO2 / outras variaveis: referencia no fim do esforco e recuperacao."""

    def test_diferenca_referencia_menos_recuperacao(self):
        t, w, h, ends = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        smo2 = _serie_por_tempo(t, ends[0], 60.0, 40.0)
        s = streams_de(t, w, h)
        s['smo2'] = smo2
        r = hrr.hrr_por_degrau(s, 'Teste', PROTO, duracao_s=t[-1])
        f = r['degraus'][0]['fisio']['smo2']
        self.assertEqual(f['nome'], 'SmO₂')
        # Com o fim efetivo pela potencia bruta, a janela de 30 s fica so' com WORK.
        self.assertAlmostEqual(f['ref_end'], 60.0, places=3)
        self.assertAlmostEqual(f['rec_60'], 40.0, places=3)
        self.assertAlmostEqual(f['rec_120'], 40.0, places=3)
        self.assertAlmostEqual(f['dif_60'], 20.0, places=3)
        self.assertAlmostEqual(f['dif_120'], 20.0, places=3)

    def test_variavel_ausente_nao_aparece(self):
        t, w, h, _ = sessao([120, 140, 160])
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        self.assertEqual(r['degraus'][0]['fisio'], {})

    def test_zeros_de_variavel_excluidos_do_valor(self):
        t, w, h, ends = sessao([120, 140, 160])
        smo2 = _serie_por_tempo(t, ends[0], 60.0, 40.0)
        for k, ti in enumerate(t):
            if 356 <= ti <= 358:
                smo2[k] = 0.0
        s = streams_de(t, w, h)
        s['smo2'] = smo2
        r = hrr.hrr_por_degrau(s, 'Teste', PROTO, duracao_s=t[-1])
        f = r['degraus'][0]['fisio']['smo2']
        self.assertAlmostEqual(f['rec_60'], 40.0, places=3)
        self.assertIsNone(f['dif_60'] if f['rec_60'] is None else None)

    def test_variavel_sem_valor_mostra_motivo_nao_zero(self):
        t, w, h, ends = sessao([120, 140, 160])
        resp = [None] * len(t)
        s = streams_de(t, w, h)
        s['respiration'] = resp
        r = hrr.hrr_por_degrau(s, 'Teste', PROTO, duracao_s=t[-1])
        f = r['degraus'][0]['fisio']['resp']
        self.assertIsNone(f['ref_end'])
        self.assertIsNone(f['dif_60'])
        self.assertIsNotNone(f['motivo_end'])


class SerieParaGrafico(unittest.TestCase):

    def test_sem_tempo_devolve_none(self):
        self.assertIsNone(hrr.serie_grafico({'watts': [1.0], 'heartrate': [1.0]}))

    def test_limita_pontos_e_inclui_ultimo(self):
        t = [float(i) for i in range(10001)]
        s = {'time': t, 'watts': [100.0] * len(t), 'heartrate': [120.0] * len(t)}
        g = hrr.serie_grafico(s, max_pontos=1500)
        self.assertLessEqual(len(g['t']), 1502)
        self.assertEqual(g['t'][-1], t[-1])
        self.assertEqual(len(g['hr']), len(g['t']))

    def test_zero_de_fc_vira_none_e_zero_de_watts_fica(self):
        t = [float(i) for i in range(10)]
        s = {'time': t, 'watts': [0.0] * 10, 'heartrate': [0.0] * 10}
        g = hrr.serie_grafico(s)
        self.assertTrue(all(v is None for v in g['hr']))
        self.assertTrue(all(v == 0.0 for v in g['watts']))

    def test_serie_reduzida_nao_altera_valores_calculados(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        s = streams_de(t, w, h)
        antes = hrr.hrr_por_degrau(s, 'Teste', PROTO, duracao_s=t[-1])
        hrr.serie_grafico(s, max_pontos=50)
        depois = hrr.hrr_por_degrau(s, 'Teste', PROTO, duracao_s=t[-1])
        self.assertEqual(antes, depois)
