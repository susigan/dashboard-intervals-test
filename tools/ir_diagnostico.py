"""Diagnostico de identificabilidade do modelo IR (FERRAMENTA OFFLINE, so leitura; nao faz parte da app).

Nao altera o modelo, os limites, a funcao objetivo nem nenhum dado: usa `modelo_ir.py` tal como esta e le uma
COPIA local da base. Nunca acede a Intervals.icu, a producao nem ao CP Model.

    python tools/ir_diagnostico.py excel
    python tools/ir_diagnostico.py real  --db copia_local.db --mod Bike --dur 1200 [--lag 1] [--p0-fixo 150]
    python tools/ir_diagnostico.py simular [--db copia_local.db --mod Bike] [--n 60 --amplitude 15 --ruido 5]

  excel    reproduz a folha EstimarConstantes (tests/pmc/dados/ir_excel_referencia.json): formulas, SSE (L10),
           otimo sob as restricoes do Solver da folha e peso do teste do dia 113 no SSE.
  real     perfil da funcao objetivo em (T1,T2) (grelha), minimos, planura, Jacobiano (SVD, correlacoes),
           multi-arranque e solucoes equivalentes, para o MESMO conjunto (observacoes, carga, P0, lag) de um ajuste
           guardado. `--lag 0|1` compara lags mantendo tudo o resto constante.
  simular  recuperacao de parametros conhecidos com ruido/n/layout (carga real da copia, ou sintetica sem --db).

Resultados sinteticos NAO provam que os dados reais chegam: ver docs/modelo_ir_identificabilidade.md.
"""
import argparse
import json
import math
import os
import sys
from datetime import date, timedelta

import numpy as np
from scipy.optimize import lsq_linear

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import modelo_ir as ir  # noqa: E402

NOMES = ir.NOMES


# ── entradas ───────────────────────────────────────────────────────────────────────────────────

def montar_caso(obs, carga_por_dia):
    """(cargas, idx, y, datas) exatamente como modelo_ir.ajustar_observacoes alinha carga e testes."""
    obs = sorted(obs, key=lambda o: (str(o['data'])[:10], str(o.get('test_id'))))
    d0 = min([str(o['data'])[:10] for o in obs] + [k[:10] for k in carga_por_dia])
    d1 = max(str(o['data'])[:10] for o in obs)
    cargas = ir.cargas_diarias({k: v for k, v in carga_por_dia.items() if k[:10] <= d1}, d0, d1)
    idx = np.array([(date.fromisoformat(str(o['data'])[:10]) - date.fromisoformat(d0)).days for o in obs])
    y = np.array([float(o['watts']) for o in obs])
    return dict(cargas=cargas, idx=idx, y=y, datas=[str(o['data'])[:10] for o in obs], d0=d0)


def caso_da_copia(db_path, modalidade, duracao):
    """Caso do ULTIMO ajuste guardado numa copia local da base (leitura): as observacoes do ajuste
    (entradas.observacoes) e a carga diaria da modalidade."""
    os.environ['SQLITE_PATH'] = db_path
    os.environ.setdefault('INTERVALS_ICU_API_KEY', 'ficticia-diagnostico')
    import db
    import modelo_ir_servico as mis
    aj = db.ultimo_ajuste_ir(modalidade, duracao)
    if aj is None:
        raise SystemExit(f'sem ajuste guardado para {modalidade} {duracao}s em {db_path}')
    carga, _ = mis.carga_por_modalidade(db.load_activities())
    return montar_caso(aj['entradas']['observacoes'], carga[modalidade]), aj


def resolver(caso, lag=1, p0_fixo=None, **cfg):
    c = {'lag_dias': lag, **cfg}
    return ir.ajustar(caso['cargas'], caso['idx'], caso['y'], c, None, caso['datas'], p0_fixo=p0_fixo)


# ── perfil da funcao objetivo ─────────────────────────────────────────────────────────────────────

def perfil_grelha(caso, lag=1, p0=None, g=48, limites=None):
    """SSE(T1,T2) numa grelha log; (P0,K1,K2) [ou (K1,K2) com P0 fixo] por minimos quadrados limitados
    (a mesma projeccao de variavel do modelo). `limites` permite uma grelha ESTENDIDA so para diagnostico."""
    L = {k: list(v) for k, v in ir.CONFIG_PADRAO['limites'].items()}
    L.update(limites or {})
    y = caso['y']
    lo, hi = ir._limites_vetor(y, ir._cfg({'lag_dias': lag, 'limites': L}))
    t1s = np.exp(np.linspace(math.log(L['t1'][0]), math.log(L['t1'][1]), g))
    t2s = np.exp(np.linspace(math.log(L['t2'][0]), math.log(L['t2'][1]), g))
    E = [ir._desloca(ir._decai(caso['cargas'], t), lag)[caso['idx']] for t in t1s]
    G = [ir._desloca(ir._decai(caso['cargas'], t), lag)[caso['idx']] for t in t2s]
    sse = np.full((g, g), np.inf)
    coef = np.zeros((g, g, 3))
    um = np.ones(len(y))
    for i in range(g):
        for j in range(g):
            if p0 is None:
                r = lsq_linear(np.column_stack([um, E[i], -G[j]]), y, bounds=(lo[:3], hi[:3]), method='bvls')
                coef[i, j] = r.x
            else:
                r = lsq_linear(np.column_stack([E[i], -G[j]]), y - p0, bounds=(lo[1:3], hi[1:3]), method='bvls')
                coef[i, j] = [p0, *r.x]
            sse[i, j] = float(r.fun @ r.fun)
    return t1s, t2s, sse, coef


def planura(t1s, t2s, sse, tolerancias=(0.02, 0.05, 0.10)):
    """Regiao da grelha com SSE <= (1+tol) x minimo: fracao de celulas e extensao em T1 e T2."""
    m = sse.min()
    out = {}
    for t in tolerancias:
        msk = sse <= m * (1 + t)
        i, j = np.where(msk)
        out[f'{int(round(t * 100))}%'] = dict(
            frac_celulas=float(msk.mean()), t1=[float(t1s[i].min()), float(t1s[i].max())],
            t2=[float(t2s[j].min()), float(t2s[j].max())])
    return out


def jacobiano(caso, x, lag, p0_fixo=None):
    """Jacobiano numerico (diferencas centrais) dos residuos no otimo: SVD, condicionamento e correlacao entre
    colunas normalizadas. Coluna nula = parametro sem efeito (ex.: K=0 anula T). Descritivo."""
    c, idx = caso['cargas'], caso['idx']

    def prev(p):
        e = ir._desloca(ir._decai(c, p[3]), lag)[idx]
        g = ir._desloca(ir._decai(c, p[4]), lag)[idx]
        return p[0] + p[1] * e - p[2] * g
    livres = [k for k in range(5) if not (p0_fixo is not None and k == 0)]
    x = np.array(x, float)
    J = np.zeros((len(idx), len(livres)))
    for a, k in enumerate(livres):
        h = 1e-5 * max(1.0, abs(x[k]))
        xp, xm = x.copy(), x.copy()
        xp[k] += h
        xm[k] -= h
        J[:, a] = (prev(xp) - prev(xm)) / (2 * h)
    nrm = np.linalg.norm(J, axis=0)
    ok = [a for a, n in enumerate(nrm) if n >= 1e-12]
    nulas = [NOMES[livres[a]] for a, n in enumerate(nrm) if n < 1e-12]
    if len(ok) < 2:
        return dict(livres=[NOMES[k] for k in livres], colunas_nulas=nulas, cond=None, corr={})
    Jn = J[:, ok] / nrm[ok]
    sv = np.linalg.svd(Jn, compute_uv=False)
    C = Jn.T @ Jn
    d = np.sqrt(np.diag(C))
    R = C / np.outer(d, d)
    nm = [NOMES[livres[a]] for a in ok]
    return dict(livres=[NOMES[k] for k in livres], colunas_nulas=nulas,
                cond=float(sv[0] / sv[-1]) if sv[-1] > 0 else float('inf'),
                corr={f'{nm[a]}~{nm[b]}': float(R[a, b]) for a in range(len(nm)) for b in range(a + 1, len(nm))})


def multi_arranque(caso, lag=1, p0_fixo=None, n=200, semente=0):
    """n arranques aleatorios polidos com o otimizador do modelo; devolve as solucoes ordenadas por SSE e a
    diferenca maxima de previsao (em W) entre as 'equivalentes' (SSE <= +2 %, a tolerancia do proprio modelo)."""
    rng = np.random.default_rng(semente)
    y = caso['y']
    c = ir._cfg({'lag_dias': lag})
    lo, hi = ir._limites_vetor(y, c)
    if p0_fixo is not None:
        lo[0] = hi[0] = p0_fixo
    L = c['limites']
    sols = []
    for _ in range(n):
        x0 = np.array([np.median(y), rng.uniform(*L['k1']), rng.uniform(*L['k2']),
                       math.exp(rng.uniform(math.log(L['t1'][0]), math.log(L['t1'][1]))),
                       math.exp(rng.uniform(math.log(L['t2'][0]), math.log(L['t2'][1])))])
        if p0_fixo is not None:
            x0[0] = p0_fixo
        try:
            r = ir._polir(x0, caso['cargas'], caso['idx'], y, lo, hi, lag, p0_fixo)
        except Exception:
            continue
        xs = r.x if p0_fixo is None else np.concatenate([[p0_fixo], r.x])
        sols.append((float(2 * r.cost), xs))
    sols.sort(key=lambda s: s[0])
    if not sols:
        return dict(n=0)
    eq = [s for s in sols if s[0] <= sols[0][0] * 1.02]

    def serie(xs):
        e, g = ir.componentes(caso['cargas'], xs[3], xs[4], lag)
        return xs[0] + xs[1] * e - xs[2] * g
    pb = serie(sols[0][1])
    return dict(n=len(sols), melhor_sse=sols[0][0], n_equivalentes=len(eq),
                faixa={k: [float(min(s[1][i] for s in eq)), float(max(s[1][i] for s in eq))]
                       for i, k in enumerate(NOMES)},
                dif_max_serie_w=max(float(np.abs(serie(s[1]) - pb).max()) for s in eq),
                dif_max_obs_w=max(float(np.abs(serie(s[1])[caso['idx']] - pb[caso['idx']]).max()) for s in eq))


def diagnosticar(caso, lag=1, p0_fixo=None, g=48):
    r = resolver(caso, lag, p0_fixo)
    out = dict(lag=lag, p0_fixo=p0_fixo, estado=r['estado'], n=len(caso['y']))
    if not r.get('parametros'):
        out['motivo'] = r.get('motivo')
        return out
    P, m, idn = r['parametros'], r['metricas'], r['identificabilidade']
    t1s, t2s, sse, co = perfil_grelha(caso, lag, p0_fixo, g)
    gi = np.unravel_index(np.argmin(sse), sse.shape)
    out.update(parametros=P, sse=m['sse'], rmse=m['rmse'], r2=m['r2'], gl=m['gl'],
               avisos=idn['avisos'], condicionamento_modelo=idn['condicionamento'],
               ic_perfil=idn['ic_perfil'],
               grelha=dict(sse_min=float(sse.min()), sse_min_sobre_solver=float(sse.min() / m['sse']),
                           t1=float(t1s[gi[0]]), t2=float(t2s[gi[1]]), planura=planura(t1s, t2s, sse)),
               jacobiano=jacobiano(caso, [P[k] for k in NOMES], lag, p0_fixo),
               multi_arranque=multi_arranque(caso, lag, p0_fixo))
    return out


# ── simulacao ─────────────────────────────────────────────────────────────────────────────────────

VERDADES = {'A': dict(t1=45.0, t2=7.0, razao_k=2.0), 'B': dict(t1=75.0, t2=10.0, razao_k=2.5)}


def carga_sintetica(n_dias=1500, semente=3):
    """Carga diaria sintetica (60 % dos dias com treino, 30-120 u.): so quando nao ha copia local."""
    rng = np.random.default_rng(semente)
    return np.where(rng.random(n_dias) < 0.6, rng.uniform(30, 120, n_dias), 0.0)


def verdade(cargas, chave, amplitude, p0=250.0, lag=1, burn=60):
    """Parametros verdadeiros com K1,K2 escolhidos para que std(K1*E - K2*G) = amplitude (W): a 'escala da carga'
    so conta pela relacao amplitude/ruido (carga x c com K / c da as mesmas previsoes)."""
    t = VERDADES[chave]
    e, g = ir.componentes(cargas, t['t1'], t['t2'], lag)
    k1 = amplitude / (e - t['razao_k'] * g)[burn:].std()
    return dict(p0=p0, k1=float(k1), k2=float(t['razao_k'] * k1), t1=t['t1'], t2=t['t2'])


def escolher_dias(n, layout, n_dias, rng, burn=60):
    dias = np.arange(burn, n_dias)
    if layout == 'uniforme':
        return np.sort(rng.choice(dias, n, replace=False))
    if layout == 'agrupado':                  # 3 blocos densos separados por vazios longos
        cs = np.sort(rng.choice(dias[:-120], 3, replace=False))
        sel = []
        for c in cs:
            sel += list(c + rng.choice(np.arange(120), min(max(1, n // 3), 120), replace=False))
        return np.unique(np.clip(sel, burn, n_dias - 1))
    if layout == 'regular':                   # espacamento (quase) constante
        return np.unique(np.linspace(burn, n_dias - 1, n).astype(int))
    if layout == 'lacuna12m':                 # sem nenhuma observacao num bloco continuo de 365 dias
        mid = n_dias // 2
        msk = (dias < mid - 182) | (dias > mid + 182)
        return np.sort(rng.choice(dias[msk], min(n, int(msk.sum())), replace=False))
    raise ValueError(layout)


def simular_um(cargas, chave='A', n=60, layout='uniforme', amplitude=15.0, ruido=5.0, semente=0, lag=1,
               erro_p0=None):
    """Um ajuste sobre dados sinteticos. erro_p0=None: P0 livre; senao P0 FIXO = P0 verdadeiro x (1+erro_p0)."""
    rng = np.random.default_rng(semente)
    tr = verdade(cargas, chave, amplitude, lag=lag)
    idx = escolher_dias(n, layout, len(cargas), rng)
    y = ir.desempenho(cargas, tr['p0'], tr['k1'], tr['k2'], tr['t1'], tr['t2'], lag)[idx] + rng.normal(0, ruido, len(idx))
    caso = dict(cargas=cargas, idx=idx, y=y, d0='2000-01-01',
                datas=[(date(2000, 1, 1) + timedelta(days=int(i))).isoformat() for i in idx])
    r = resolver(caso, lag, None if erro_p0 is None else tr['p0'] * (1 + erro_p0))
    out = dict(verdade=tr, estado=r['estado'], n=len(idx))
    if r.get('parametros'):
        P = r['parametros']
        out.update(parametros=P, rel={k: abs(P[k] - tr[k]) / tr[k] for k in ('k1', 'k2', 't1', 't2')},
                   r2=r['metricas']['r2'],
                   t_no_limite=any(d['parametro'] in ('t1', 't2') for d in r['identificabilidade']['parametros_no_limite']))
        # 'falso selo': o modelo diz identificavel mas T1 ou T2 estao a mais de 25 % da verdade (so vale em sintetico)
        out['selo_falso'] = bool(r['estado'] == 'ajustado_identificavel' and max(out['rel']['t1'], out['rel']['t2']) > 0.25)
    return out


# ── folha Excel ───────────────────────────────────────────────────────────────────────────────────

def folha():
    with open(os.path.join(ROOT, 'tests', 'pmc', 'dados', 'ir_excel_referencia.json'), encoding='utf-8') as f:
        return json.load(f)


def otimo_sob_restricoes_da_folha(xl=None):
    """Otimo de (K2,T1,T2) com P0=274 e K1=1 fixos e as caixas do Solver da folha (K2 em [2,3], T1 em [25,50],
    T2 em [4,10]; lag 0): K2 por forma fechada limitada, (T1,T2) em grelha. A restricao R>=0.75 da folha nao entra."""
    xl = xl or folha()
    tss = np.array(xl['tss'], float)
    idx = np.array(sorted(int(k) for k in xl['testes']))
    y = np.array([xl['testes'][str(i)] for i in idx], float)
    p0, k1 = xl['parametros']['p0'], xl['parametros']['k1']
    melhor = None
    for t1 in np.linspace(25, 50, 51):
        for t2 in np.linspace(4, 10, 61):
            e, g = ir.componentes(tss, t1, t2, 0)
            e0 = (p0 + k1 * e)[idx] - y
            gi = g[idx]
            k2 = float(np.clip((e0 @ gi) / (gi @ gi), 2.0, 3.0))
            s = float(((e0 - k2 * gi) ** 2).sum())
            if melhor is None or s < melhor['sse']:
                melhor = dict(sse=s, k2=k2, t1=float(t1), t2=float(t2))
    return melhor


def peso_do_teste(xl, dia):
    """Fracao do SSE da folha que vem do teste do dia `dia` (indice a partir de 0)."""
    p = xl['parametros']
    idx = sorted(int(k) for k in xl['testes'])
    y = [xl['testes'][str(i)] for i in idx]
    e = ir.erros(xl['tss'], idx, y, p, 0)
    return float(e[idx.index(dia)] ** 2 / (e @ e))


# ── linha de comando ──────────────────────────────────────────────────────────────────────────────

def _main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('excel')
    r = sub.add_parser('real')
    r.add_argument('--db', required=True)
    r.add_argument('--mod', required=True)
    r.add_argument('--dur', type=int, required=True)
    r.add_argument('--lag', type=int, default=1)
    r.add_argument('--p0-fixo', type=float, default=None)
    s = sub.add_parser('simular')
    s.add_argument('--db')
    s.add_argument('--mod', default='Bike')
    s.add_argument('--dur', type=int, default=1200)
    for k, d in (('n', 60), ('amplitude', 15.0), ('ruido', 5.0), ('repeticoes', 20)):
        s.add_argument('--' + k, type=type(d), default=d)
    s.add_argument('--layout', default='uniforme')
    s.add_argument('--verdade', default='A')
    a = ap.parse_args(argv)
    if a.cmd == 'excel':
        xl = folha()
        print(json.dumps(dict(otimo_sob_restricoes=otimo_sob_restricoes_da_folha(xl),
                              sse_folha=xl['soma_quadrados_excel'], peso_teste_dia_113=peso_do_teste(xl, 113)), indent=1))
    elif a.cmd == 'real':
        caso, aj = caso_da_copia(a.db, a.mod, a.dur)
        print(json.dumps(diagnosticar(caso, a.lag, a.p0_fixo), indent=1, default=float))
    else:
        cargas = carga_sintetica()
        if a.db:
            cargas = caso_da_copia(a.db, a.mod, a.dur)[0]['cargas']
        res = [simular_um(cargas, a.verdade, a.n, a.layout, a.amplitude, a.ruido, semente=k) for k in range(a.repeticoes)]
        ok = [x for x in res if 'rel' in x]
        print(json.dumps(dict(n=len(res), com_ajuste=len(ok),
                              erro_rel_mediano={k: float(np.median([x['rel'][k] for x in ok])) for k in ('k1', 'k2', 't1', 't2')} if ok else None,
                              frac_identificavel=sum(x['estado'] == 'ajustado_identificavel' for x in ok) / max(1, len(ok)),
                              frac_t_no_limite=sum(x['t_no_limite'] for x in ok) / max(1, len(ok))), indent=1))


if __name__ == '__main__':
    _main()
