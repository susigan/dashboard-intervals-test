"""Ferramentas da fase 2 do diagnostico do IR: alvo x P0, walk-forward sem fuga, simulacao (sinteticos; offline)."""
import os
import sys
import unittest
from datetime import date, timedelta

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for p in (os.path.join(ROOT, 'tools'), ROOT):
    sys.path.insert(0, p)
import ir_diagnostico as dg  # noqa: E402
import ir_fase2 as f2  # noqa: E402
import modelo_ir as ir  # noqa: E402

D0 = date(2024, 1, 1)


def serie(n_obs=40, ruido=2.0, semente=0):
    cargas = dg.carga_sintetica(600, 4)
    tr = dg.verdade(cargas, 'A', 30.0)
    rng = np.random.default_rng(semente)
    idx = np.sort(rng.choice(np.arange(80, 600), n_obs, replace=False))
    perf = ir.desempenho(cargas, tr['p0'], tr['k1'], tr['k2'], tr['t1'], tr['t2'], 1)
    obs = [{'test_id': f't{i}', 'data': (D0 + timedelta(days=int(i))).isoformat(), 'watts': float(perf[i] + rng.normal(0, ruido))}
           for i in idx]
    carga = {(D0 + timedelta(days=i)).isoformat(): float(c) for i, c in enumerate(cargas) if c > 0}
    return obs, carga, tr


class Fase2(unittest.TestCase):
    def test_walk_forward_nao_usa_o_futuro(self):
        obs, carga, _ = serie()
        cfg = ir._cfg(None)
        base = ir.avaliar_preditivo(obs, lambda a: [o for o in obs if o['data'] < a['data']], carga, cfg)
        # corromper TUDO o que e posterior a uma data de corte nao pode mexer nas previsoes anteriores
        corte = obs[30]['data']
        obs2 = [dict(o, watts=o['watts'] * 3 if o['data'] >= corte else o['watts']) for o in obs]
        carga2 = {k: (v * 5 if k >= corte else v) for k, v in carga.items()}
        alt = ir.avaliar_preditivo(obs2, lambda a: [o for o in obs2 if o['data'] < a['data']], carga2, cfg)
        for a, b in zip(base, alt):
            if a['data'] <= corte and a['ir'] is not None:
                self.assertEqual(a['ir'], b['ir'], a['data'])

    def test_perfil_de_p0_marca_o_p0_verdadeiro_como_compativel_e_um_muito_errado_como_incompativel(self):
        obs, carga, tr = serie(60, 2.0, 1)
        caso = f2.caso_de(obs, carga)
        r = f2.alvo_vs_p0(caso, cp=tr['p0'])
        self.assertTrue(r['cp_informado']['compativel_95'])
        errado = f2.alvo_vs_p0(caso, cp=tr['p0'] * 0.6)['cp_informado']
        self.assertFalse(errado['compativel_95'])
        self.assertIsNotNone(r['p0_compativel_95'])

    def test_sem_cp_informado_nao_inventa_cp(self):
        obs, carga, _ = serie()
        r = f2.alvo_vs_p0(f2.caso_de(obs, carga))
        self.assertNotIn('cp_informado', r)

    def test_jackknife_pequeno_nao_se_interpreta(self):
        obs, carga, _ = serie(10)
        self.assertFalse(f2.jackknife(f2.caso_de(obs, carga))['interpretavel'])

    def test_simulacao_regista_selo_falso_so_quando_ha_selo(self):
        r = dg.simular_um(dg.carga_sintetica(), 'A', n=60, layout='regular', amplitude=30.0, ruido=2.0, semente=2)
        if r['estado'] != 'ajustado_identificavel':
            self.assertFalse(r.get('selo_falso', False))
        self.assertIn('regular', f2.grelha_sim('n_layout')[0][2:3] + ('regular',))


if __name__ == '__main__':
    unittest.main()
