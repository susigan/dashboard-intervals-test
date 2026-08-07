"""PMC — Performance Management Chart.

Duas camadas:
  classica   CTL/ATL/TSB por media exponencial (42/7 dias)
  FTLM       CTLgamma fraccionario Della Mattia (2025), gamma ajustado aos
             dados do proprio atleta, fases de treino e tensor FMT


CTL/ATL/TSB a partir do icu_training_load, com a mesma logica do dashboard
original (tab_pmc.py):

  CTL = media exponencial a 42 dias da carga diaria
  ATL = media exponencial a 7 dias
  TSB = CTL - ATL   (calculado com os valores de ONTEM, ver abaixo)

Sem pandas: as series sao listas de dicts, calculadas em Python puro.
"""

from datetime import datetime, timedelta

CTL_DIAS = 42
ATL_DIAS = 7


def _ewm(valores, span):
    """Media exponencial, equivalente a pandas.ewm(span=N, adjust=False)."""
    alpha = 2.0 / (span + 1.0)
    out, anterior = [], None
    for v in valores:
        anterior = v if anterior is None else alpha * v + (1 - alpha) * anterior
        out.append(anterior)
    return out


def serie_diaria(sessoes, campo='tl', ate=None, desde=None):
    """Soma por dia, com os dias sem treino a zero.

    Os dias vazios contam: e o descanso que faz o ATL cair mais depressa
    que o CTL, e e dai que vem a forma.
    """
    if not sessoes:
        return []
    por_dia = {}
    for s in sessoes:
        d = (s.get('date') or '')[:10]
        if len(d) != 10:
            continue
        por_dia[d] = por_dia.get(d, 0.0) + float(s.get(campo) or 0)

    d0 = desde or min(por_dia)
    d1 = ate or datetime.now().strftime('%Y-%m-%d')
    ini = datetime.strptime(d0, '%Y-%m-%d')
    fim = datetime.strptime(d1, '%Y-%m-%d')
    dias = []
    while ini <= fim:
        k = ini.strftime('%Y-%m-%d')
        dias.append({'date': k, 'load': round(por_dia.get(k, 0.0), 1)})
        ini += timedelta(days=1)
    return dias


def calcular(sessoes, campo='tl', ate=None, desde=None):
    """CTL, ATL, TSB e ramp rate, dia a dia."""
    dias = serie_diaria(sessoes, campo, ate, desde)
    if not dias:
        return []
    cargas = [d['load'] for d in dias]
    ctl = _ewm(cargas, CTL_DIAS)
    atl = _ewm(cargas, ATL_DIAS)

    for i, d in enumerate(dias):
        d['ctl'] = round(ctl[i], 1)
        d['atl'] = round(atl[i], 1)
        # TSB de hoje usa os valores de ontem: a forma que trazes para o treino
        # de hoje nao pode incluir o treino de hoje.
        d['tsb'] = round((ctl[i - 1] - atl[i - 1]) if i else 0.0, 1)
        # ramp rate: quanto o CTL subiu nos ultimos 7 dias
        d['ramp'] = round(ctl[i] - ctl[i - 7], 1) if i >= 7 else 0.0
    return dias


def por_modalidade(sessoes, modalidades, campo='tl', ate=None, desde=None):
    """CTL por modalidade — para ver de onde vem a carga."""
    out = {}
    for m in modalidades:
        sub = [s for s in sessoes if s.get('type') == m]
        if not sub:
            continue
        serie = calcular(sub, campo, ate, desde)
        out[m] = [{'date': d['date'], 'ctl': d['ctl'], 'atl': d['atl']}
                  for d in serie]
    return out


def estado_forma(tsb):
    """Interpretacao do TSB. Os limites sao convencao do TrainingPeaks,
    nao uma verdade fisiologica — servem de referencia, nao de regra."""
    if tsb is None:
        return {'label': '—', 'cor': '#8b949e', 'nota': ''}
    if tsb > 25:
        return {'label': 'Muito fresco', 'cor': '#5DADE2',
                'nota': 'forma alta, mas fitness a cair se durar'}
    if tsb > 5:
        return {'label': 'Fresco', 'cor': '#2ECC71', 'nota': 'pronto para competir'}
    if tsb > -10:
        return {'label': 'Neutro', 'cor': '#F4D03F', 'nota': 'treino sustentavel'}
    if tsb > -30:
        return {'label': 'Em carga', 'cor': '#E67E22', 'nota': 'bloco de trabalho'}
    return {'label': 'Muito carregado', 'cor': '#E74C3C',
            'nota': 'risco se se prolongar'}


def alertas(dias, wellness=None):
    """Sinais que merecem atencao. Descritivos, nao prescritivos."""
    if not dias:
        return []
    fim = dias[-1]
    out = []

    if fim['ramp'] > 8:
        out.append({'nivel': 'aviso',
                    'texto': f"CTL subiu {fim['ramp']} em 7 dias. "
                             "Acima de ~8/semana costuma ser dificil de aguentar."})
    if fim['tsb'] < -30:
        out.append({'nivel': 'aviso',
                    'texto': f"TSB em {fim['tsb']}. Carga acumulada alta."})
    if fim['tsb'] > 25 and fim['ctl'] > 0:
        out.append({'nivel': 'info',
                    'texto': f"TSB em {fim['tsb']} — muito fresco. "
                             "Bom para competir, mau para manter fitness."})

    # HRV abaixo da media dos 60 dias, tres dias seguidos
    if wellness:
        hrvs = [(w['date'], w.get('hrv')) for w in wellness
                if w.get('hrv') is not None]
        if len(hrvs) >= 10:
            vals = [v for _, v in hrvs[-60:]]
            media = sum(vals) / len(vals)
            desv = (sum((v - media) ** 2 for v in vals) / len(vals)) ** 0.5
            ultimos = [v for _, v in hrvs[-3:]]
            if len(ultimos) == 3 and all(v < media - desv for v in ultimos):
                out.append({'nivel': 'aviso',
                            'texto': f"HRV abaixo de {media - desv:.0f} ha 3 dias "
                                     f"(media 60d: {media:.0f})."})
    return out


# ══════════════════════════════════════════════════════════════════════════
# FTLM fraccionario, fases e FMT — sobre a camada classica acima
# ══════════════════════════════════════════════════════════════════════════

def _serie_por_dia(sessoes, datas, campo, agregacao='sum'):
    """Valor diario alinhado com a lista de datas. NaN onde nao ha sessao."""
    import numpy as np
    por_dia = {}
    for s in sessoes:
        v = s.get(campo)
        if v is None:
            continue
        por_dia.setdefault(s['date'], []).append(float(v))
    out = np.full(len(datas), np.nan)
    for i, d in enumerate(datas):
        vals = por_dia.get(d)
        if vals:
            out[i] = sum(vals) if agregacao == 'sum' else sum(vals) / len(vals)
    return out


def _dias_sem_treino(cargas):
    import numpy as np
    n = len(cargas)
    out = np.zeros(n, dtype=int)
    contador = 0
    for i, c in enumerate(cargas):
        contador = 0 if c > 0 else contador + 1
        out[i] = contador
    return out


def _zscore_rolling_28(valores, datas, minimo=7):
    """z-score de cada dia contra a sua propria linha de base de 28 dias.

    E assim que o dashboard trata as escalas 1-5: em vez de comparar com um
    valor absoluto, compara com o teu normal recente. Fica invariante a escala.
    """
    import numpy as np
    v = np.asarray(valores, dtype=np.float64)
    n = len(v)
    out = np.full(n, np.nan)
    for t in range(n):
        seg = v[max(0, t - 27):t + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg) >= minimo and np.std(seg) > 1e-9:
            out[t] = (v[t] - seg.mean()) / seg.std()
    return out


def calcular_ftlm(sessoes, wellness, serie_classica, modalidades):
    """CTLgamma, gammas ajustados, fases e FMT.

    Devolve um dict pronto a serializar para JSON.
    """
    import numpy as np
    import ftlm

    if not serie_classica:
        return None

    datas = [d['date'] for d in serie_classica]
    cargas = np.array([d['load'] for d in serie_classica], dtype=np.float64)
    n = len(datas)
    max_lag = min(365, n)

    # ── sinal de recuperacao: LnRMSSD, WEED e sono ────────────────────────
    hrv_ln = np.full(n, np.nan)
    weed_z = None
    sleep_z = None
    if wellness:
        idx = {w['date']: w for w in wellness}
        bruto = np.array([(idx.get(d) or {}).get('hrv') or np.nan for d in datas],
                         dtype=np.float64)
        hrv_ln = np.where(bruto > 0, np.log(bruto), np.nan)

        # WEED: stress, dores e cansaco. Escala 1-5 em que 5 = melhor nos tres,
        # por isso nao ha nada a inverter.
        partes = []
        for campo in ('stress', 'soreness', 'fatiga'):
            vals = [(idx.get(d) or {}).get(campo) for d in datas]
            vals = np.array([v if v is not None else np.nan for v in vals],
                            dtype=np.float64)
            if np.isfinite(vals).sum() >= 10:
                partes.append(_zscore_rolling_28(vals, datas))
        if partes:
            arr = np.array(partes)
            # dias em que nenhuma das componentes tem valor ficam NaN, sem aviso
            validos = np.isfinite(arr).any(axis=0)
            weed_z = np.full(arr.shape[1], np.nan)
            if validos.any():
                with np.errstate(all='ignore'):
                    weed_z[validos] = np.nanmean(arr[:, validos], axis=0)

        sq = [(idx.get(d) or {}).get('sleep_quality') for d in datas]
        sq = np.array([v if v is not None else np.nan for v in sq], dtype=np.float64)
        if np.isfinite(sq).sum() >= 5:
            sleep_z = _zscore_rolling_28(sq, datas)

    # ── gamma de recuperacao: carga de ontem contra HRV de hoje (lag=1) ────
    gamma_rec, r2_rec, n_rec = ftlm.GAMMA_DEFAULT, 0.0, 0
    hrv_tendencia = np.full(n, np.nan)
    if int(np.isfinite(hrv_ln).sum()) >= 21:
        hrv_tendencia = ftlm.hrv_trend(hrv_ln, window=7)
        gamma_rec, r2_rec, n_rec = ftlm.fit_gamma(
            cargas, hrv_tendencia, lag=1, max_lag=max_lag)

    # ── gamma de performance, global e por modalidade ─────────────────────
    cp = _serie_por_dia(sessoes, datas, 'cp', 'mean')
    gamma_perf, r2_perf, n_perf = ftlm.GAMMA_DEFAULT, 0.0, 0
    if np.isfinite(cp).sum() >= 10:
        gamma_perf, r2_perf, n_perf = ftlm.fit_gamma(
            cargas, cp, lag=0, max_lag=max_lag, suavizar=3)

    ctlg_perf = ftlm.ftlm_fractional(cargas, gamma_perf, max_lag)
    ctlg_rec = ftlm.ftlm_fractional(cargas, gamma_rec, max_lag)

    por_mod, ctlg_mod, fases_mod = {}, {}, {}
    for mod in modalidades:
        ses_mod = [s for s in sessoes if s.get('type') == mod]
        if len(ses_mod) < 5:
            continue
        carga_mod = _serie_por_dia(ses_mod, datas, 'tl', 'sum')
        carga_mod = np.nan_to_num(carga_mod)
        cp_mod = _serie_por_dia(ses_mod, datas, 'cp', 'mean')

        g_m, r2_m, n_m = ftlm.GAMMA_DEFAULT, 0.0, 0
        if np.isfinite(cp_mod).sum() >= 5:
            g_m, r2_m, n_m = ftlm.fit_gamma(carga_mod, cp_mod, lag=0,
                                            max_lag=max_lag, suavizar=3)
        serie_mod = ftlm.ftlm_fractional(carga_mod, g_m, max_lag)
        ctlg_mod[mod] = serie_mod

        f_mod = ftlm.detect_phases(serie_mod, hrv_tendencia, weed_z,
                                   _dias_sem_treino(carga_mod))
        fases_mod[mod] = f_mod['fase'][-1]

        por_mod[mod] = {
            'gamma': g_m, 'r2': r2_m, 'n': n_m,
            'n_sessoes': len(ses_mod),
            'ctlg_actual': round(float(serie_mod[-1]), 2),
            'fase': f_mod['fase'][-1],
            'serie': [{'date': datas[i], 'ctlg': round(float(serie_mod[i]), 2)}
                      for i in range(n)],
        }

    # ── fases: overall e global ponderada pelo CTLgamma de cada modalidade ─
    sem_treino = _dias_sem_treino(cargas)
    f_overall = ftlm.detect_phases(ctlg_perf, hrv_tendencia, weed_z, sem_treino)
    ctlg_actual_mod = {m: float(s[-1]) for m, s in ctlg_mod.items()}
    fase_global, contrib = ftlm.fase_global_ponderada(fases_mod, ctlg_actual_mod)

    # ── FMT: quanto o sistema esta a oscilar ──────────────────────────────
    dimensoes = [ctlg_perf, ctlg_rec]
    nomes_dim = ['CTLg_perf', 'CTLg_rec']
    if np.isfinite(hrv_tendencia).sum() >= 30:
        dimensoes.append(hrv_tendencia)
        nomes_dim.append('HRV_trend')
    if weed_z is not None and np.isfinite(weed_z).sum() >= 30:
        dimensoes.append(weed_z)
        nomes_dim.append('WEED')
    if sleep_z is not None and np.isfinite(sleep_z).sum() >= 30:
        dimensoes.append(sleep_z)
        nomes_dim.append('Sono')
    wp = _serie_por_dia(sessoes, datas, 'w_prime', 'mean')
    if np.isfinite(wp).sum() >= 30:
        dimensoes.append(wp)
        nomes_dim.append("W'")

    kappa, lam1 = ftlm.kappa_fmt(dimensoes)

    def _f(v):
        return round(float(v), 4) if np.isfinite(v) else None

    fase_actual = f_overall['fase'][-1]
    serie = [{
        'date': datas[i],
        'ctlg_perf': round(float(ctlg_perf[i]), 2),
        'ctlg_rec': round(float(ctlg_rec[i]), 2),
        'dctlg': _f(f_overall['dctlg'][i]),
        'hrv_z': _f(f_overall['hrv_z'][i]),
        'weed_z': _f(f_overall['weed_z'][i]),
        'kappa': _f(kappa[i]) if i < len(kappa) else None,
        'lambda1': _f(lam1[i]) if i < len(lam1) else None,
        'fase': f_overall['fase'][i],
    } for i in range(n)]

    return {
        'serie': serie,
        'gammas': {
            'perf': {'gamma': gamma_perf, 'r2': r2_perf, 'n': n_perf},
            'rec': {'gamma': gamma_rec, 'r2': r2_rec, 'n': n_rec},
        },
        'por_modalidade': por_mod,
        'fase_actual': {
            'codigo': fase_actual,
            'dias': int(f_overall['dias_na_fase'][-1]) + 1,
            'dctlg': _f(f_overall['dctlg'][-1]),
            'hrv_z': _f(f_overall['hrv_z'][-1]),
            **ftlm.FASES[fase_actual],
        },
        'fase_global': ({'codigo': fase_global, 'contribuicoes': contrib,
                         **ftlm.FASES[fase_global]} if fase_global else None),
        'fmt': {
            'dimensoes': nomes_dim,
            'kappa': _f(kappa[-1]) if len(kappa) else None,
            'lambda1': _f(lam1[-1]) if len(lam1) else None,
        },
        'fases_legenda': ftlm.FASES,
    }
