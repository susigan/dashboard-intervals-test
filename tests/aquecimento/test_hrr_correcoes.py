"""Correcoes HRR apos auditoria: sem_hr, HR_end, FC media do inicio da recuperacao.

SINTETICO: sinais gerados nestes testes, com instantes reais conhecidos.
Nao usa a API nem a base de dados. Os valores de FC sao controlados (constantes
por fase), para que a media esperada seja exata e qualquer mistura de amostras
de intervalos diferentes apareça como erro.

Convencao do sinal (sessao com alvos [120,140,160], work_s=300, rec_s=(R0,R1,R2)):
  WORK 0: [0, 300)            fim real 300
  REC 0 : [300, 300+R0)
  WORK 1: [300+R0, 600+R0)    fim real 600+R0
  REC 1 : ...
"""
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)  # test_hrr_unit (sessao) fica nesta pasta

from test_hrr_unit import PROTO, sessao, streams_de  # noqa: E402

import aquecimento_hrr as hrr  # noqa: E402  (utils/ ja' no sys.path via test_hrr_unit)


def _override(t, h, intervalos):
    """Substitui FC por valor constante em intervalos [a, b) de tempo."""
    out = list(h)
    for k, ti in enumerate(t):
        for a, b, v in intervalos:
            if a <= ti < b:
                out[k] = v
    return out


def _remove(t, w, h, a, b):
    """Remove amostras (tempo, watts, FC) com t em [a, b)."""
    keep = [k for k, ti in enumerate(t) if not (a <= ti < b)]
    return ([t[k] for k in keep], [w[k] for k in keep], [h[k] for k in keep])


def _fim_real(r0):
    return [300.0, 600.0 + r0]


def _inicio_real(r0):
    return [0.0, 300.0 + r0]


class FcMediaRecuperacao1Min(unittest.TestCase):
    """Item 3 e 5: FC media do inicio da recuperacao, sem misturar intervalos."""

    def _calc(self, r0, fc_rec=120.0, fc_next=170.0, rec_s=None, passo=None):
        rec_s = rec_s or (r0, 150, 150)
        t, w, h, _ = sessao([120, 140, 160], rec_s=rec_s, decl=(0.5, 0.5, 0.5), passo=passo)
        fim0, fim1 = _fim_real(r0)[0], _fim_real(r0)[1]
        ini1 = _inicio_real(r0)[1]
        h = _override(t, h, [(fim0, ini1, fc_rec), (ini1, fim1, fc_next)])
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        return r

    def test_recuperacao_60s_media_so_da_recuperacao(self):
        r = self._calc(60)
        self.assertEqual(r['status'], 'ok')
        d = r['degraus'][0]
        f = d['fc_media_rec_1min']
        t_next = d['rec']['t_fim_s']                   # inicio DETETADO do degrau seguinte
        self.assertEqual(f['valor'], 120.0)            # so' recuperacao; o WORK seguinte (170) nao entra
        self.assertEqual(f['janela'][0], 300.0)
        # formula: fim = min(t_end + 60, t_next - 15); e' sempre antes do inicio REAL (360)
        self.assertAlmostEqual(f['janela'][1], min(360.0, t_next - hrr.MARGEM_INICIO_SEGUINTE_S), places=1)
        self.assertLess(f['janela'][1], 360.0)
        self.assertIsNone(f['motivo'])
        self.assertGreaterEqual(f['cobertura'], 0.6)
        self.assertEqual(f['n_validas'], int(f['janela'][1]) - 300 + 1)  # 1 amostra/s, inclusive

    def test_nao_e_hrr60(self):
        r = self._calc(60)
        f = r['degraus'][0]['fc_media_rec_1min']
        self.assertNotIn('hrr', f)
        self.assertIn('fc_media_rec_1min', r['degraus'][0])
        self.assertNotEqual(r['degraus'][0]['hrr60'], f['valor'])

    def test_recuperacao_50s_com_janela_util_calcula(self):
        r = self._calc(50)
        d = r['degraus'][0]
        f = d['fc_media_rec_1min']
        self.assertEqual(f['valor'], 120.0)
        self.assertAlmostEqual(f['duracao_janela_s'], f['janela'][1] - 300.0, places=1)
        self.assertGreaterEqual(f['duracao_janela_s'], hrr.MIN_JANELA_REC_1MIN_S)
        self.assertLess(f['janela'][1], 350.0)         # nunca chega ao inicio real (360)

    def test_recuperacao_40s_respeita_minimo_de_janela(self):
        r = self._calc(40)
        self.assertEqual(r['status'], 'ok')
        d = r['degraus'][0]
        f = d['fc_media_rec_1min']
        t_next = d['rec']['t_fim_s']
        dur = min(60.0, t_next - hrr.MARGEM_INICIO_SEGUINTE_S) - 0.0
        if dur < hrr.MIN_JANELA_REC_1MIN_S:
            self.assertIsNone(f['valor'])
            self.assertEqual(f['motivo'], 'rec_curta')
        else:
            # janela util: o valor usa apenas a recuperacao (120), nunca o seguinte (170)
            self.assertEqual(f['valor'], 120.0)
        self.assertLess(f['janela'][1], 340.0 + 1e-6)  # nunca passa o inicio real do seguinte (340)

    def test_amostragem_irregular_usa_tempo(self):
        r = self._calc(90, passo=[1.0, 2.0, 3.0])
        self.assertEqual(r['status'], 'ok')
        f = r['degraus'][0]['fc_media_rec_1min']
        self.assertEqual(f['valor'], 120.0)
        self.assertGreater(f['n_validas'], 0)
        self.assertGreater(f['cobertura'], 0.6)

    def test_ultimo_intervalo_sem_recuperacao_seguinte(self):
        r = self._calc(60)
        f = r['degraus'][-1]['fc_media_rec_1min']
        self.assertIsNone(f['valor'])
        self.assertEqual(f['motivo'], 'sem_fim_de_recuperacao')
        self.assertIsNone(r['degraus'][-1]['hrr60'])
        self.assertEqual(r['degraus'][-1]['hrr60_motivo'], 'sem_fim_de_recuperacao')


class ExclusaoDoSeguinte(unittest.TestCase):
    """Item 8: amostras do intervalo seguinte nunca entram nas janelas de recuperacao."""

    def test_pico_no_work_seguinte_nao_altera_fc_media(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(60, 150, 150), decl=(0.5, 0.5, 0.5))
        h = _override(t, h, [(300.0, 360.0, 120.0), (360.0, 660.0, 250.0)])
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        f = r['degraus'][0]['fc_media_rec_1min']
        self.assertEqual(f['valor'], 120.0)            # 250 nao entra
        self.assertLess(f['janela'][1], 360.0)         # antes do inicio REAL do seguinte

    def test_hr60_nao_usa_work_seguinte_quando_recuperacao_longa(self):
        # recuperacao 80 s: HR_60 em [355,365] cabe antes do WORK seguinte (380).
        # Pico no WORK seguinte nao pode aparecer no HR_60.
        t, w, h, _ = sessao([120, 140, 160], rec_s=(80, 150, 150), decl=(0.5, 0.5, 0.5))
        fim0, ini1 = 300.0, 380.0
        h = _override(t, h, [(fim0, ini1, 120.0), (ini1, 700.0, 250.0)])
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        d = r['degraus'][0]
        self.assertEqual(d['hr_60']['valor'], 120.0)
        self.assertLessEqual(d['hr_60']['janela'][1], ini1 - hrr.MARGEM_INICIO_SEGUINTE_S)


class LimiteDoEsforco(unittest.TestCase):
    """Item 2 e 4: a primeira amostra da recuperacao nao entra em HR_end."""

    def test_pico_exatamente_no_inicio_da_recuperacao_nao_altera_hr_end(self):
        t, w, h, ends = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        self.assertIn(300.0, t)                       # ha' amostra exatamente em t_end
        base = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        h2 = _override(t, h, [(300.0, 301.0, 220.0)])  # so' a primeira amostra da recuperacao
        r = hrr.hrr_por_degrau(streams_de(t, w, h2), 'Teste', PROTO, duracao_s=t[-1])
        d0, d1 = base['degraus'][0], r['degraus'][0]
        self.assertEqual(d1['work']['t_fim_s'], 300.0)
        self.assertEqual(d1['hr_end']['valor'], d0['hr_end']['valor'])
        self.assertEqual(d1['hr_end']['valor'], 150.0)  # FC do trabalho, sem o pico
        self.assertEqual(d1['hr_end']['n_validas'], 30)

    def test_controlo_pico_dentro_do_work_altera_hr_end(self):
        # prova de sensibilidade: o mesmo pico, 1 s ANTES do fim, entra em HR_end
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        h2 = _override(t, h, [(299.0, 300.0, 220.0)])
        r = hrr.hrr_por_degrau(streams_de(t, w, h2), 'Teste', PROTO, duracao_s=t[-1])
        self.assertGreater(r['degraus'][0]['hr_end']['valor'], 150.0)


class FcExistenteSemAmostrasValidas(unittest.TestCase):
    """Item 3 do teste obrigatorio: FC presente mas sem valores validos."""

    def _run(self, valor):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        h = [valor] * len(t)
        return hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])

    def test_todas_nulas_nao_e_sem_hr(self):
        r = self._run(None)
        self.assertEqual(r['status'], 'ok')
        d = r['degraus'][0]
        self.assertIsNone(d['hrr60'])
        self.assertEqual(d['hr_end']['motivo'], 'sem_amostras_validas')
        self.assertEqual(d['hrr60_motivo'], 'hr_end_sem_amostras_validas')
        self.assertIsNone(d['fc_media_rec_1min']['valor'])
        self.assertEqual(d['fc_media_rec_1min']['motivo'], 'sem_amostras_validas')

    def test_todas_zeros_sao_invalidas_nao_zero(self):
        r = self._run(0.0)
        self.assertEqual(r['status'], 'ok')
        d = r['degraus'][0]
        self.assertIsNone(d['hr_end']['valor'])
        self.assertGreater(d['hr_end']['n_zeros'], 0)
        self.assertIsNone(d['fc_media_rec_1min']['valor'])


class CoberturaInsuficiente(unittest.TestCase):
    """Item 7: HRR60/HRR120 n/d quando a janela nao tem cobertura suficiente."""

    def _base(self):
        return sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))

    def test_lacuna_total_na_janela_do_hr60(self):
        t, w, h, _ = self._base()
        t, w, h = _remove(t, w, h, 352.0, 372.0)      # 20 s sem dados, cobre [355,365]
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        d = r['degraus'][0]
        self.assertIsNone(d['hrr60'])
        self.assertEqual(d['hr_60']['motivo'], 'lacuna_sem_dados')
        self.assertEqual(d['hrr60_motivo'], 'lacuna_sem_dados')

    def test_cobertura_parcial_abaixo_de_60_por_cento(self):
        t, w, h, _ = self._base()
        t, w, h = _remove(t, w, h, 355.0, 362.0)      # 7 s de 11 em falta: cobertura ~0,4
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        d = r['degraus'][0]
        self.assertIsNone(d['hrr60'])
        self.assertIsNotNone(d['hr_60']['cobertura'])
        self.assertLess(d['hr_60']['cobertura'], 0.6)
        self.assertEqual(d['hr_60']['motivo'], 'dados_insuficientes')

    def test_hrr120_continua_valido_quando_so_hrr60_falha(self):
        t, w, h, _ = self._base()
        t, w, h = _remove(t, w, h, 352.0, 372.0)
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        d = r['degraus'][0]
        self.assertIsNone(d['hrr60'])
        self.assertIsNotNone(d['hrr120'])              # janela 415-425 nao foi tocada


class TemposEFormatos(unittest.TestCase):
    """Item 9: amostragem irregular e timestamps invalidos/ausentes."""

    def test_timestamps_ausentes_falham_explicitamente(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        r = hrr.hrr_por_degrau({'watts': w, 'heartrate': h}, 'Teste', PROTO)
        self.assertEqual(r['status'], 'sem_timestamps')

    def test_timestamps_nao_numericos_falham(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        tt = list(t)
        tt[10] = 'x'
        r = hrr.hrr_por_degrau(streams_de(tt, w, h), 'Teste', PROTO)
        self.assertEqual(r['status'], 'timestamps_invalidos')

    def test_irregular_nao_altera_hrr_analitico(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5),
                            passo=[1.0, 2.0, 3.0])
        r = hrr.hrr_por_degrau(streams_de(t, w, h), 'Teste', PROTO, duracao_s=t[-1])
        self.assertEqual(r['status'], 'ok')
        self.assertIsNotNone(r['degraus'][0]['hrr60'])
        self.assertAlmostEqual(r['degraus'][0]['hrr60'], 30.0, delta=1.0)


class DiagnosticoSemHr(unittest.TestCase):
    """Item 1 e 2: sem FC, so' os nomes dos tipos recebidos; sem valores."""

    def test_sem_chave_de_fc_devolve_apenas_nomes(self):
        t, w, _, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        s = {'time': t, 'watts': w, 'smo2': [777.5] * len(t)}
        r = hrr.hrr_por_degrau(s, 'Teste', PROTO, duracao_s=t[-1])
        self.assertEqual(r['status'], 'sem_hr')
        self.assertEqual(r['tipos_recebidos'], ['smo2', 'time', 'watts'])
        self.assertNotIn('777.5', repr(r))             # nenhum valor da serie

    def test_lista_de_fc_vazia_e_sem_hr_e_mostra_o_nome(self):
        t, w, _, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        s = {'time': t, 'watts': w, 'heartrate': []}
        r = hrr.hrr_por_degrau(s, 'Teste', PROTO, duracao_s=t[-1])
        self.assertEqual(r['status'], 'sem_hr')
        self.assertIn('heartrate', r['tipos_recebidos'])

    def test_nomes_sao_limitados(self):
        s = {'time': [0.0], 'watts': [1.0]}
        for k in range(200):
            s[f'x{k:03d}'] = [1.0]
        r = hrr.hrr_por_degrau(s, 'Teste', PROTO)
        self.assertEqual(r['status'], 'sem_hr')
        self.assertLessEqual(len(r['tipos_recebidos']), hrr.TIPOS_MAX)


class MontagemDeStreams(unittest.TestCase):
    """Duplicados de FC: entradas inuteis nunca sobrescrevem uma util (causa de sem_hr)."""

    def test_allnull_depois_de_fc_valida_nao_sobrescreve(self):
        lista = [{'type': 'heartrate', 'data': [150.0, 151.0]},
                 {'type': 'heartrate', 'data': None, 'allNull': True}]
        s, nomes = hrr.montar_streams_brutos(lista)
        self.assertEqual(s['heartrate'], [150.0, 151.0])
        self.assertIn('heartrate', nomes)

    def test_allnull_antes_de_fc_valida_nao_bloqueia(self):
        lista = [{'type': 'heartrate', 'data': None, 'allNull': True},
                 {'type': 'heartrate', 'data': [150.0, 151.0]}]
        s, _ = hrr.montar_streams_brutos(lista)
        self.assertEqual(s['heartrate'], [150.0, 151.0])

    def test_dois_validos_ficam_com_sufixo_sem_perder_nenhum(self):
        lista = [{'type': 'heartrate', 'data': [150.0]},
                 {'type': 'heartrate', 'data': [160.0]}]
        s, _ = hrr.montar_streams_brutos(lista)
        self.assertEqual(s['heartrate'], [150.0])
        self.assertEqual(s['heartrate_2'], [160.0])

    def test_sem_dados_uteis_nao_guarda_e_lista_o_nome(self):
        lista = [{'type': 'heartrate', 'data': []},
                 {'type': 'watts', 'data': [200, 200]}]
        s, nomes = hrr.montar_streams_brutos(lista)
        self.assertNotIn('heartrate', s)
        self.assertEqual(nomes, ['heartrate', 'watts'])

    def test_valores_nao_sao_reduzidos(self):
        d = [float(k) for k in range(5000)]
        s, _ = hrr.montar_streams_brutos([{'type': 'heartrate', 'data': d}])
        self.assertEqual(len(s['heartrate']), 5000)

    def test_hrr_calcula_com_fc_depois_de_entrada_allnull(self):
        t, w, h, _ = sessao([120, 140, 160], rec_s=(150, 150, 150), decl=(0.5, 0.5, 0.5))
        lista = [{'type': 'time', 'data': t}, {'type': 'watts', 'data': w},
                 {'type': 'heartrate', 'data': h},
                 {'type': 'heartrate', 'data': None, 'allNull': True}]
        s, _ = hrr.montar_streams_brutos(lista)
        r = hrr.hrr_por_degrau(s, 'Teste', PROTO, duracao_s=t[-1])
        self.assertEqual(r['status'], 'ok')
        self.assertIsNotNone(r['degraus'][0]['hrr60'])


if __name__ == '__main__':
    unittest.main()
