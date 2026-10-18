"""Fase 2 do diagnostico do IR (FERRAMENTA OFFLINE, so leitura; nao faz parte da app).

Orquestra funcoes que JA existem (`tools/ir_diagnostico.py`, `modelo_ir.avaliar_preditivo`, `modelo_validacao`,
`modelo_ir_servico._avaliar_unidade`); nao reimplementa a matematica do IR. Nunca acede a Intervals.icu nem a producao,
nunca grava na base, nunca toca no CP Model. Usa uma COPIA local da base (--db).

    python tools/ir_fase2.py alvo  --db copia.db [--cp Bike=250 --cp Row=180] --out saida/
    python tools/ir_fase2.py walk  --db copia.db [--cenario livre|p0_085|p0_100|cp] [--cp Bike=250] --out saida/
    python tools/ir_fase2.py jack  --db copia.db --out saida/
    python tools/ir_fase2.py sim   --grupo n_layout|amp_ruido|p0_erro --db copia.db --out saida/

CP: a copia local pode NAO ter nenhum CP confirmado (cp_resultados vive na base de perfil). Esta ferramenta nunca
inventa um CP: sem `--cp` mede a compatibilidade para uma GRELHA de valores de P0 e devolve o intervalo de P0 (F 95 %)
compativel com o alvo; com `--cp Mod=W` avalia tambem esse valor (que o utilizador confirmou) e diz se cai nesse intervalo.
Cenarios `p0_085`/`p0_100` do walk-forward usam k x mediana dos alvos ANTERIORES: sao PROXIES rotulados, nao CP.
"""
import argparse
import itertools
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(AQUI, '..'))
for _p in (ROOT, AQUI):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ir_diagnostico as dg  # noqa: E402
import modelo_ir as ir  # noqa: E402

GRELHA_F = tuple(np.linspace(0.2, 1.3, 56))     # P0 = f x mediana dos alvos (so uma grelha de varrimento)


def abrir(db_path):
    """Liga os modulos do projeto a COPIA local da base e devolve (atual, cargas_por_modalidade, atividades)."""
    os.environ['SQLITE_PATH'] = db_path
    os.environ.setdefault('INTERVALS_ICU_API_KEY', 'ficticia-diagnostico')
    import db
    import modelo_estimador as E
    import modelo_ir_servico as mis
    import modelo_observacoes_auto as moa
    hist, rev, atv = db.observacoes_auto_historico(), db.revisoes_testes_modelo(), db.load_activities()
    cur = db.load_power_curves() or []
    atual = E.selecionar_observacoes_auto(moa.FonteAuto(hist, rev).atuais(), E.estado_atual_por_teste(rev or []),
                                          atv, E.curvas_por_atividade(cur))
    cargas, _ = mis.carga_por_modalidade(atv)
    return atual, cargas, atv


def caso_de(alvos, carga_mod):
    return dg.montar_caso([{'data': o['data'], 'watts': o['watts'], 'test_id': o.get('test_id')} for o in alvos], carga_mod)


def unidades(atual, cargas):
    for (m, d) in sorted(atual):
        alvos = atual[(m, d)]['usadas']
        if alvos:
            yield m, d, alvos, caso_de(alvos, cargas[m])


# ── 1. alvo x P0 ──────────────────────────────────────────────────────────────────────────────────

def descricao_alvo(caso):
    y, c = caso['y'], caso['cargas']
    datas = sorted(set(caso['datas']))
    gaps = np.diff([np.datetime64(x) for x in datas]).astype(int) if len(datas) > 1 else np.array([0])
    z, mx, cur = (c == 0), 0, 0
    for v in z:
        cur = cur + 1 if v else 0
        mx = max(mx, cur)
    q = np.percentile(y, [0, 10, 25, 50, 75, 90, 100])
    return dict(n=len(y), datas_distintas=len(datas), data_ini=datas[0], data_fim=datas[-1],
                quantis=dict(zip(['min', 'p10', 'p25', 'med', 'p75', 'p90', 'max'], map(float, q))),
                amplitude=float(q[-1] - q[0]), cv=float(y.std(ddof=1) / y.mean()),
                gap_obs_med=float(np.median(gaps)), gap_obs_max=int(gaps.max()),
                dias_serie=len(c), frac_dias_com_carga=float((c > 0).mean()), maior_bloco_sem_carga=int(mx))


def _linha_p0(caso, p0, sse0, gl):
    r = dg.resolver(caso, 1, p0)
    if not r.get('parametros'):
        return dict(p0=float(p0), estado=r['estado'])
    P, y, idx = r['parametros'], caso['y'], caso['idx']
    e, g = ir.componentes(caso['cargas'], P['t1'], P['t2'], 1)
    sse = r['metricas']['sse']
    return dict(p0=float(p0), sse=sse, estado=r['estado'], k1=P['k1'], k2=P['k2'], t1=P['t1'], t2=P['t2'],
                media_k1e=float((P['k1'] * e)[idx].mean()), media_k2g=float((P['k2'] * g)[idx].mean()),
                frac_alvos_acima_p0=float((y > p0).mean()),
                F=float((sse - sse0) / (sse0 / gl)) if sse0 and gl else None)


def alvo_vs_p0(caso, cp=None):
    """Compatibilidade do alvo com um P0 FIXO: SSE e F (teste de 1 gl contra P0 livre) por valor de P0. Nao presume CP."""
    from scipy import stats
    out = dict(alvo=descricao_alvo(caso))
    livre = dg.resolver(caso, 1, None)
    out['livre'] = dict(estado=livre['estado'], params=livre.get('parametros'),
                        sse=(livre.get('metricas') or {}).get('sse'), gl=(livre.get('metricas') or {}).get('gl'),
                        r2=(livre.get('metricas') or {}).get('r2'))
    if not livre.get('parametros') or not out['livre']['gl']:
        out['motivo'] = livre.get('motivo') or livre['estado']
        return out
    sse0, gl, med = out['livre']['sse'], out['livre']['gl'], float(np.median(caso['y']))
    corte = float(stats.f.ppf(0.95, 1, gl))
    # a grelha inclui o P0 do ajuste livre (F = 0): o intervalo nunca fica vazio por resolucao da grelha
    pontos = sorted({*(med * f for f in GRELHA_F), float(livre['parametros']['p0'])})
    out['perfil_p0'] = [dict(f=float(p0 / med), **_linha_p0(caso, p0, sse0, gl)) for p0 in pontos]
    ok = [p['p0'] for p in out['perfil_p0'] if p.get('F') is not None and p['F'] <= corte]
    out['p0_compativel_95'] = [min(ok), max(ok)] if ok else None
    out['f_critico_95'] = corte
    out['mediana_alvo'] = med
    if cp is not None:
        lin = _linha_p0(caso, cp, sse0, gl)
        lin['compativel_95'] = bool(lin.get('F') is not None and lin['F'] <= corte)
        out['cp_informado'] = lin
    return out


# ── 2. validacao temporal ─────────────────────────────────────────────────────────────────────────

def _p0_de(cenario, alvos, cp):
    if cenario == 'livre':
        return None
    if cenario == 'cp':
        return lambda a: cp                                            # CP informado e constante (nao inferido)
    f = 0.85 if cenario == 'p0_085' else 1.0

    def p0(a):
        t = [o['watts'] for o in alvos if o['data'] < a['data']]
        return f * float(np.median(t)) if t else None
    return p0


def walk_forward(mod, dur, alvos, carga_mod, cenario='livre', cp=None):
    """Walk-forward um passo: reutiliza `_avaliar_unidade` (IR + M0/M1/M2 nas MESMAS datas) e acrescenta a mediana
    movel de 5 e o erro dentro da amostra (ajuste com TODOS os alvos, avaliado nas mesmas datas: NAO e previsao)."""
    import modelo_estimador as E
    import modelo_ir_servico as mis
    cfg = ir._cfg(None)
    treino_de = lambda a: [o for o in alvos if o['data'] < a['data']]  # noqa: E731
    p0_de = _p0_de(cenario, alvos, cp)
    it = mis._avaliar_unidade('retrospectivo', mod, dur, alvos, treino_de, carga_mod, cfg, p0_de)
    for p in it['previsoes']:
        w = [o['watts'] for o in sorted(treino_de({'data': p['data']}), key=E.chave_ordem)][-5:]
        p['MM5'] = float(np.median(w))
    p0_in = None if cenario == 'livre' else (cp if cenario == 'cp' else (0.85 if cenario == 'p0_085' else 1.0)
                                             * float(np.median([o['watts'] for o in alvos])))
    r = ir.ajustar_observacoes(alvos, carga_mod, '9999-12-31', {**cfg, 'perfil': False}, p0_fixo=p0_in)
    it['insample'] = None
    if r['parametros'] and it['previsoes']:
        P = r['parametros']
        d0 = min([str(o['data'])[:10] for o in alvos] + [k[:10] for k in carga_mod])
        dm = max(str(o['data'])[:10] for o in alvos)
        s = ir.desempenho(ir.cargas_diarias({k: v for k, v in carga_mod.items() if k[:10] <= dm}, d0, dm),
                          P['p0'], P['k1'], P['k2'], P['t1'], P['t2'], 1)
        e = np.array([s[(date.fromisoformat(p['data']) - date.fromisoformat(d0)).days] - p['y'] for p in it['previsoes']])
        it['insample'] = dict(rmse=float(np.sqrt((e ** 2).mean())), mae=float(np.abs(e).mean()), estado=r['estado'])
    return it


def metricas_wf(it):
    """RMSE, MAE e vies por metodo nas datas em que o IR previu (mesmas datas para todos)."""
    P = it['previsoes']
    if not P:
        return None
    y = np.array([p['y'] for p in P])
    out = {}
    for k, ch in (('IR', 'ir'), ('M0', 'M0'), ('M1', 'M1'), ('M2', 'M2'), ('MM5', 'MM5')):
        e = np.array([p[ch] for p in P]) - y
        out[k] = dict(rmse=float(np.sqrt((e ** 2).mean())), mae=float(np.abs(e).mean()), vies=float(e.mean()))
    out['n'] = len(P)
    return out


# ── 3. jackknife ──────────────────────────────────────────────────────────────────────────────────

def jackknife(caso, n_min=15):
    """Leave-one-out (P0 livre, lag 1). Com n < n_min nao se interpreta (amostra pequena demais)."""
    y, idx, c = caso['y'], caso['idx'], caso['cargas']
    if len(y) < n_min:
        return dict(n=len(y), interpretavel=False)
    jk = {k: [] for k in ir.NOMES}
    est = []
    for i in range(len(y)):
        mk = np.ones(len(y), bool)
        mk[i] = False
        sub = dict(cargas=c, idx=idx[mk], y=y[mk], datas=[x for j, x in enumerate(caso['datas']) if mk[j]], d0=caso['d0'])
        r = dg.resolver(sub, 1, None)
        if r.get('parametros'):
            for k in jk:
                jk[k].append(r['parametros'][k])
            est.append(r['estado'])
    lim = lambda v, a, b: float(np.mean([(x <= a * 1.0001) or (x >= b * 0.9999) for x in v]))  # noqa: E731
    out = {k: [float(np.min(v)), float(np.median(v)), float(np.max(v))] for k, v in jk.items() if v}
    out.update(n=len(y), interpretavel=True, frac_identificavel=float(np.mean([s == 'ajustado_identificavel' for s in est])),
               frac_t1_limite=lim(jk['t1'], 20, 120), frac_t2_limite=lim(jk['t2'], 1, 15))
    return out


# ── 4. simulacoes (sinteticas: carga real, efeito e ruido ASSUMIDOS) ─────────────────────────────────

_CARGAS = {}


def _um(a):
    m, n, lay, amp, rui, erro, sem = a
    r = dg.simular_um(_CARGAS[m], 'A', n=n, layout=lay, amplitude=amp, ruido=rui, semente=sem, erro_p0=erro)
    return dict(m=m, n=n, lay=lay, amp=amp, rui=rui, erro=erro, sem=sem, estado=r['estado'], rel=r.get('rel'),
                selo=r['estado'] == 'ajustado_identificavel', selo_falso=r.get('selo_falso', False), tlim=r.get('t_no_limite'))


def grelha_sim(grupo, reps=24):
    G = []
    if grupo == 'n_layout':
        for m, n, lay in itertools.product(('Bike', 'Row'), (15, 30, 60, 120), ('uniforme', 'regular', 'agrupado', 'lacuna12m')):
            G += [(m, n, lay, 15.0, 5.0, None, s) for s in range(reps)]
    elif grupo == 'amp_ruido':
        for m, amp, rui in itertools.product(('Bike', 'Row'), (5.0, 15.0, 30.0, 60.0), (2.0, 5.0, 10.0, 20.0)):
            G += [(m, 60, 'uniforme', amp, rui, None, s) for s in range(reps)]
    elif grupo == 'p0_erro':
        for m, erro in itertools.product(('Bike', 'Row'), (-0.3, -0.15, -0.05, 0.0, 0.05, 0.15, 0.3)):
            G += [(m, 60, 'uniforme', 15.0, 5.0, erro, s) for s in range(reps)]
    else:
        raise SystemExit('grupo desconhecido: ' + grupo)
    return G


def simular(grupo, cargas_mod, reps=24, workers=2):
    _CARGAS.update(cargas_mod)
    with ProcessPoolExecutor(workers) as ex:
        return list(ex.map(_um, grelha_sim(grupo, reps), chunksize=8))


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────────

def _cargas_para_sim(atual, cargas):
    out = {}
    for m in ('Bike', 'Row'):
        a = atual.get((m, 1200), {}).get('usadas')
        if a:
            out[m] = dg.montar_caso([a[0], a[-1]], cargas[m])['cargas']
    return out


def _cp(args):
    return {k: float(v) for k, v in (x.split('=') for x in args.cp or [])}


def _main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['alvo', 'walk', 'jack', 'sim'])
    ap.add_argument('--db', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--cp', action='append', help='Modalidade=W (CP confirmado pelo utilizador); repetir por modalidade')
    ap.add_argument('--cenario', default='livre', choices=['livre', 'p0_085', 'p0_100', 'cp'])
    ap.add_argument('--grupo', default='n_layout')
    ap.add_argument('--reps', type=int, default=24)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    atual, cargas, _ = abrir(a.db)
    cp = _cp(a)
    if a.cmd == 'sim':
        res = simular(a.grupo, _cargas_para_sim(atual, cargas), a.reps)
    else:
        res = []
        for m, d, alvos, caso in unidades(atual, cargas):
            if a.cmd == 'alvo':
                res.append(dict(mod=m, dur=d, **alvo_vs_p0(caso, cp.get(m))))
            elif a.cmd == 'walk':
                if a.cenario == 'cp' and m not in cp:
                    res.append(dict(mod=m, dur=d, motivo='sem_cp_informado'))
                    continue
                it = walk_forward(m, d, alvos, cargas[m], a.cenario, cp.get(m))
                res.append(dict(mod=m, dur=d, metricas=metricas_wf(it), insample=it['insample'], item=it))
            else:
                res.append(dict(mod=m, dur=d, jackknife=jackknife(caso)))
            print(m, d, flush=True)
    nome = os.path.join(a.out, f'{a.cmd}_{a.cenario if a.cmd == "walk" else a.grupo if a.cmd == "sim" else "real"}.json')
    with open(nome, 'w', encoding='utf-8') as f:
        json.dump(res, f, default=float, indent=1)
    print('escrito', nome)


if __name__ == '__main__':
    _main()
