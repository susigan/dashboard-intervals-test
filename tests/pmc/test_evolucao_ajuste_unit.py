"""Tendencia (regressao local linear) e banda (+/-1 DP local dos residuos) do grafico comparativo.

    python -m unittest discover -s tests/pmc -p 'test_evolucao_ajuste_unit.py'

O metodo e recalculado aqui em numpy, de forma independente da pagina, e comparado com o
que o JS de tabs/tab_pmc.py calcula. Pula se node nao estiver disponivel.
"""
import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'dummy-teste-local')

HARNESS = os.path.join(HERE, 'js', 'render_grafico_evol.js')
NODE = '/opt/node22/bin/node' if os.path.exists('/opt/node22/bin/node') else 'node'
DURACOES = ('60', '300', '720', '1200')
H, NMIN, PASSO = 90.0, 8, 7
EPOCH = date(1970, 1, 1)


def _dia(iso):
    """Dias desde 1970-01-01 (mesma escala de evDia no JS)."""
    y, m, d = map(int, iso[:10].split('-'))
    return (date(y, m, d) - EPOCH).days


def _tricubo(d):
    u = np.abs(d) / H
    return np.where(u < 1, (1 - u ** 3) ** 3, 0.0)


def _ll(t0, T, Y):
    """Regressao local linear ponderada (tricubo) em t0. None se menos de NMIN obs no raio."""
    w = _tricubo(T - t0)
    m = w > 0
    if m.sum() < NMIN:
        return None
    X = np.column_stack([np.ones(m.sum()), T[m] - t0])
    W = w[m]
    A = X.T @ (X * W[:, None])
    b = X.T @ (W * Y[m])
    if abs(np.linalg.det(A)) < 1e-12:
        return None
    return float(np.linalg.solve(A, b)[0])


def _ajuste_ref(T, Y):
    """Referencia independente: grade semanal (ini..fim) + banda local dos residuos."""
    res = []
    R = [None] * len(T)
    for i, t in enumerate(T):
        f = _ll(t, T, Y)
        R[i] = None if f is None else Y[i] - f
    for g in range(int(math.ceil(T.min())), int(T.max()) + 1, PASSO):
        f = _ll(float(g), T, Y)
        if f is None:
            res.append((g, None, None))
            continue
        idx = [i for i in range(len(T)) if R[i] is not None and _tricubo(np.array([T[i] - g]))[0] > 0]
        sd = None
        if len(idx) >= NMIN:
            w = _tricubo(T[idx] - g)
            r = np.array([R[i] for i in idx])
            sd = math.sqrt(float((w * r * r).sum() / w.sum()))
        res.append((g, f, sd))
    return res


class AjusteComparativo(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if shutil.which(NODE) is None and not os.path.exists(NODE):
            raise unittest.SkipTest('node nao disponivel')
        try:
            import tabs.tab_pmc as T
            from test_evolucao_visual_unit import _cenario, _gerar
        except BaseException as e:  # config.py encerra sem chave da API
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        html = T.render()
        js = '\n'.join(re.findall(r'<script>(.*?)</script>', html, re.S))
        cls.tmp = tempfile.mkdtemp(prefix='evol_aj_')
        cls.pjs = os.path.join(cls.tmp, 'pmc.js')
        with open(cls.pjs, 'w', encoding='utf-8') as f:
            f.write(js)
        e, c = _cenario()
        cls.base = _gerar(e, c, mods=('Bike',))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, payload, mod='Bike', jan='180', ocultas=''):
        pay = os.path.join(self.tmp, 'payload.json')
        with open(pay, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        r = subprocess.run([NODE, HARNESS, self.pjs, pay, mod, jan, 'todas', ocultas],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        return json.loads(r.stdout)

    def _obs_ref(self, payload, k, jan):
        """Observacoes de referencia: valido, valor > 0, sem 'excluido', Δ% pela mediana do backend."""
        mod = payload['modalidades']['Bike']['melhores_esforcos'][k]
        med = mod['janelas'][jan]['mediana_anterior']
        T, Y, ids = [], [], []
        for p in mod['pontos']:
            if p.get('estado') != 'valido' or not isinstance(p.get('valor'), (int, float)) or p['valor'] <= 0:
                continue
            if 'excluido' in (p.get('sinalizacoes') or []):
                continue
            T.append(_dia(p['data']))
            Y.append(100 * (p['valor'] / med - 1))
            ids.append(p['id'])
        return np.array(T, dtype=float), np.array(Y, dtype=float), ids

    # ---- 1. ajuste independente por duracao, recalculado em referencia ----
    def test_ajuste_independente_por_duracao_bate_com_referencia(self):
        for jan in ('90', '180', '365'):
            reg = self._run(self.base, jan=jan)
            for k in DURACOES:
                with self.subTest(janela=jan, duracao=k):
                    T, Y, _ = self._obs_ref(self.base, k, jan)
                    ref = _ajuste_ref(T, Y)
                    got = reg['__ultimo_norm']['ajuste'][k]['grade']
                    self.assertEqual(len(got), len(ref))
                    for (g, f, _sd), q in zip(ref, got):
                        self.assertEqual(q['t'], g)
                        if f is None:
                            self.assertIsNone(q['y'])
                        else:
                            self.assertAlmostEqual(q['y'], f, places=6)

    # ---- 2. banda = +/-1 DP local dos residuos (nao IC) ----
    def test_banda_e_desvio_local_dos_residuos(self):
        reg = self._run(self.base, jan='180')
        for k in DURACOES:
            with self.subTest(duracao=k):
                T, Y, _ = self._obs_ref(self.base, k, '180')
                ref = _ajuste_ref(T, Y)
                got = reg['__ultimo_norm']['ajuste'][k]['grade']
                for (g, f, sd), q in zip(ref, got):
                    if sd is None:
                        self.assertIsNone(q['sd'])
                    else:
                        self.assertAlmostEqual(q['sd'], sd, places=6)
                        self.assertGreaterEqual(q['sd'], 0)

    # ---- 3. sem curva/banda sem dados suficientes ----
    def test_sem_curva_quando_dados_insuficientes(self):
        p = copy.deepcopy(self.base)
        pts = p['modalidades']['Bike']['melhores_esforcos']['300']['pontos']
        for q in pts[5:]:  # so 5 observacoes validas restam
            q['estado'], q['valor'] = 'ausente', None
        reg = self._run(p)
        aj = reg['__ultimo_norm']['ajuste']['300']
        self.assertFalse(aj['ok'])
        self.assertEqual(aj['curva'], 0)
        self.assertEqual(aj['banda'], 0)
        self.assertNotIn('300', reg['__ultimo_norm']['curvas'])
        self.assertNotIn('300', reg['__ultimo_norm']['bandas'])
        self.assertIn('sem tendência', reg['__html'])
        self.assertTrue(reg['__ultimo_norm']['ajuste']['60']['ok'])  # demais seguem

    # ---- 4. nao extrapola ----
    def test_nao_extrapola_alem_das_observacoes(self):
        reg = self._run(self.base, jan='180')
        for k in DURACOES:
            with self.subTest(duracao=k):
                T, _, _ = self._obs_ref(self.base, k, '180')
                suportadas = [q['t'] for q in reg['__ultimo_norm']['ajuste'][k]['grade'] if q['y'] is not None]
                self.assertTrue(suportadas)
                self.assertGreaterEqual(min(suportadas), T.min())
                self.assertLessEqual(max(suportadas), T.max())
                for t in suportadas:  # cada ponto com suporte tem >= NMIN obs no raio
                    self.assertGreaterEqual(int((np.abs(T - t) < H).sum()), NMIN)

    # ---- 5. excluido sai do ajuste; sinalizados ficam ----
    def test_excluido_fica_fora_do_ajuste_e_sinalizados_ficam(self):
        base_n = self._run(self.base)['__ultimo_norm']['ajuste']['60']['n_obs']
        p = copy.deepcopy(self.base)
        pts = p['modalidades']['Bike']['melhores_esforcos']['60']['pontos']
        alvo = next(q for q in pts if q.get('estado') == 'valido')
        alvo['sinalizacoes'] = sorted(set((alvo.get('sinalizacoes') or []) + ['excluido']))
        self.assertEqual(self._run(p)['__ultimo_norm']['ajuste']['60']['n_obs'], base_n - 1)
        q = copy.deepcopy(self.base)
        pts = q['modalidades']['Bike']['melhores_esforcos']['60']['pontos']
        alvo = next(x for x in pts if x.get('estado') == 'valido')
        alvo['sinalizacoes'] = sorted(set((alvo.get('sinalizacoes') or []) + ['extremo']))
        self.assertEqual(self._run(q)['__ultimo_norm']['ajuste']['60']['n_obs'], base_n)

    # ---- 6. ausente/zero/negativo nao entram ----
    def test_ausente_zero_negativo_nao_entram_no_ajuste(self):
        p = copy.deepcopy(self.base)
        pts = p['modalidades']['Bike']['melhores_esforcos']['720']['pontos']
        pts[0].update({'estado': 'ausente', 'valor': None})
        pts[1].update({'estado': 'zero', 'valor': 0})
        pts[2].update({'estado': 'negativo', 'valor': -4})
        reg = self._run(p)
        T, _, _ = self._obs_ref(p, '720', '180')
        self.assertEqual(reg['__ultimo_norm']['ajuste']['720']['n_obs'], len(T))

    # ---- 7. selecao 90/180/365 mantem a grade temporal (so o denominador muda) ----
    def test_selecao_mantem_grade_temporal(self):
        grades = {}
        for jan in ('90', '180', '365'):
            reg = self._run(self.base, jan=jan)
            grades[jan] = [q['t'] for q in reg['__ultimo_norm']['ajuste']['60']['grade']]
        self.assertEqual(grades['90'], grades['180'])
        self.assertEqual(grades['180'], grades['365'])

    # ---- 8. observados e medianas do backend preservados ----
    def test_observados_e_medianas_inalterados(self):
        reg = self._run(self.base, jan='180')
        mod = self.base['modalidades']['Bike']['melhores_esforcos']
        for k in DURACOES:
            self.assertEqual(reg['__ultimo_norm']['ajuste'][k]['n_obs'],
                             len(self._obs_ref(self.base, k, '180')[0]))
            self.assertIn(f"{mod[k]['janelas']['180']['mediana_anterior']:.1f} → ", reg['__html'])

    # ---- 9. ocultar/reexibir remove curva e banda ----
    def test_ocultar_remove_curva_e_banda(self):
        oc = self._run(self.base, ocultas='60,720')
        self.assertNotIn('60', oc['__ultimo_norm']['curvas'])
        self.assertNotIn('720', oc['__ultimo_norm']['bandas'])
        self.assertIn('300', oc['__ultimo_norm']['curvas'])
        self.assertEqual(sorted(oc['__ultimo_norm']['ocultas']), ['60', '720'])
        volta = self._run(self.base)
        self.assertEqual(set(volta['__ultimo_norm']['curvas']), set(DURACOES))

    # ---- 10. texto: observado x estimado, metodo da banda explicito ----
    def test_textos_separam_observado_de_tendencia_e_banda(self):
        reg = self._run(self.base)
        self.assertIn('não intervalo de confiança', reg['__html'])
        self.assertIn('regressão local linear', reg['__html'])
        tip = reg['__tooltip']
        self.assertIn(' W · ', tip)
        self.assertIn('tendência estimada', tip)
        self.assertIn('não observação', tip)

    # ---- 11. regressao: eFTP fora do ajuste ----
    def test_eftp_nao_entra_no_ajuste(self):
        reg = self._run(self.base)
        self.assertNotIn('eftp_atividade', reg['__ultimo_norm']['ajuste'])
        self.assertIn('ev_eftp', reg)


if __name__ == '__main__':
    unittest.main()
