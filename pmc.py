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
    fit_rec = {'motivo': 'sem serie de HRV suficiente', 'aceite': False}
    hrv_tendencia = np.full(n, np.nan)
    if int(np.isfinite(hrv_ln).sum()) >= 21:
        hrv_tendencia = ftlm.hrv_trend(hrv_ln, window=7)
        fit_rec = ftlm.fit_gamma(cargas, hrv_tendencia, lag=1, max_lag=max_lag)
        gamma_rec = fit_rec['gamma']
        r2_rec, n_rec = fit_rec['r2'], fit_rec['n']

    # ── gamma de performance, global e por modalidade ─────────────────────
    cp = _serie_por_dia(sessoes, datas, 'cp', 'mean')
    gamma_perf, r2_perf, n_perf = ftlm.GAMMA_DEFAULT, 0.0, 0
    fit_perf = {'motivo': 'sem pontos de CP suficientes', 'aceite': False}
    if np.isfinite(cp).sum() >= 10:
        fit_perf = ftlm.fit_gamma(cargas, cp, lag=0, max_lag=max_lag,
                                  suavizar=3)
        gamma_perf = fit_perf['gamma']
        r2_perf, n_perf = fit_perf['r2'], fit_perf['n']

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
        fit_m = {'gamma_encontrado': None, 'aceite': False,
                 'na_fronteira': False, 'p_permutacao': None,
                 'motivo': 'sem pontos de CP suficientes'}
        if np.isfinite(cp_mod).sum() >= 5:
            fit_m = ftlm.fit_gamma(carga_mod, cp_mod, lag=0,
                                   max_lag=max_lag, suavizar=3)
            g_m, r2_m, n_m = fit_m['gamma'], fit_m['r2'], fit_m['n']
        serie_mod = ftlm.ftlm_fractional(carga_mod, g_m, max_lag)
        ctlg_mod[mod] = serie_mod

        f_mod = ftlm.detect_phases(serie_mod, hrv_tendencia, weed_z,
                                   _dias_sem_treino(carga_mod))
        fases_mod[mod] = f_mod['fase'][-1]

        por_mod[mod] = {
            'gamma': g_m, 'r2': r2_m, 'n': n_m,
            'gamma_fit': {k: fit_m.get(k) for k in
                          ('gamma_encontrado', 'aceite', 'na_fronteira',
                           'p_permutacao', 'motivo')},
            # CTLgamma normalizado ao proprio maximo: e o unico numero
            # comparavel entre modalidades, ja que gammas diferentes dao
            # ordens de grandeza diferentes
            'ctlg_pct': (round(float(serie_mod[-1] / serie_mod.max() * 100), 1)
                         if serie_mod.max() > 0 else None),
            'n_sessoes': len(ses_mod),
            'ctlg_actual': round(float(serie_mod[-1]), 2),
            'fase': f_mod['fase'][-1],
            'serie': [{'date': datas[i], 'ctlg': round(float(serie_mod[i]), 2)}
                      for i in range(n)],
        }

    # ── fases: overall e global ponderada pelo CTLgamma de cada modalidade ─
    sem_treino = _dias_sem_treino(cargas)
    # Duas nocoes de "global", propositadamente diferentes:
    #
    #  agregada  — soma a carga de TODAS as modalidades num unico sinal e
    #              deteta a fase sobre ele. E o estado do corpo, que nao
    #              distingue de onde veio a carga.
    #
    #  ponderada — deteta a fase de cada modalidade separadamente e escolhe a
    #              moda pesada pelo CTLgamma de cada uma. E o estado do
    #              treino, dominado pela modalidade que mais pesa.
    #
    # Divergirem e informacao: significa que o corpo esta num estado que
    # nenhuma modalidade isolada explica.
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
            'perf': {'gamma': gamma_perf, 'r2': r2_perf, 'n': n_perf,
                     **{k: (fit_perf or {}).get(k) for k in
                        ('gamma_encontrado', 'aceite', 'na_fronteira',
                         'p_permutacao', 'motivo')}},
            'rec': {'gamma': gamma_rec, 'r2': r2_rec, 'n': n_rec,
                    **{k: (fit_rec or {}).get(k) for k in
                       ('gamma_encontrado', 'aceite', 'na_fronteira',
                        'p_permutacao', 'motivo')}},
        },
        'por_modalidade': por_mod,
        'fase_actual': {
            'codigo': fase_actual,
            'base': 'carga agregada de todas as modalidades',
            'modalidades_incluidas': sorted(set(
                s.get('type') for s in sessoes if s.get('type'))),
            'dias': int(f_overall['dias_na_fase'][-1]) + 1,
            'dctlg': _f(f_overall['dctlg'][-1]),
            'hrv_z': _f(f_overall['hrv_z'][-1]),
            **ftlm.FASES[fase_actual],
        },
        'fase_global': ({'codigo': fase_global, 'contribuicoes': contrib,
                         'base': 'moda das fases por modalidade, pesada pelo CTLgamma',
                         'fases_por_modalidade': fases_mod,
                         **ftlm.FASES[fase_global]} if fase_global else None),
        'fmt': {
            'dimensoes': nomes_dim,
            'kappa': _f(kappa[-1]) if len(kappa) else None,
            'lambda1': _f(lam1[-1]) if len(lam1) else None,
        },
        'fases_legenda': ftlm.FASES,
    }


# ══════════════════════════════════════════════════════════════════════════
# Modelo homeostatico e indice alostatico
# ══════════════════════════════════════════════════════════════════════════

def modelo_homeostatico(serie_classica, sessoes, p0=None,
                        tau_sugerido=None, lag_hrv_sugerido=None):
    """Reserva de performance p̂(t) = p₀ + K₁·EWM(carga,T₁) − K₂·EWM(carga,T₂).

    O PMC classico fixa τ em 42 e 7 dias. Aqui T₁ e T₂ sao ajustados aos
    dados deste atleta: procuramos a combinacao (K₁,K₂,T₁,T₂) que melhor
    explica a serie de CP observada.

    Sem testes de performance suficientes devolve os valores por defeito e
    diz que sao insuficientes — em vez de fingir um ajuste.
    """
    import numpy as np
    import ftlm

    if not serie_classica:
        return None

    datas = [d['date'] for d in serie_classica]
    cargas = np.array([d['load'] for d in serie_classica], dtype=np.float64)
    n = len(datas)

    alvo = _serie_por_dia(sessoes, datas, 'cp', 'mean')
    n_testes = int(np.isfinite(alvo).sum())

    if p0 is None:
        p0 = float(np.nanmedian(alvo)) if n_testes else 200.0

    melhor = {'k1': 2.0, 'k2': 3.0, 't1': 42.0, 't2': 7.0, 'r2': 0.0}
    ajustado = False
    tentativas, rejeitados = 0, 0
    melhor_rejeitado = {'k1': None, 'k2': None, 't1': None, 't2': None, 'r2': -9e9}

    # Se a calibracao encontrou um tau para a carga, a grelha do T1 e
    # centrada nele — a mesma constante de tempo que explica o HRV tem de
    # explicar tambem a componente de fitness.
    grelha_t1 = (25, 30, 35, 40, 45, 50, 60)
    grelha_t2 = (4, 5, 6, 7, 9, 11, 14)
    if tau_sugerido:
        t = float(tau_sugerido)
        grelha_t1 = tuple(sorted({max(7, round(t * f))
                                  for f in (0.6, 0.8, 1.0, 1.3, 1.8, 2.5, 3.5)}))
    if lag_hrv_sugerido:
        L = max(2.0, float(lag_hrv_sugerido))
        grelha_t2 = tuple(sorted({max(2, round(L * f))
                                  for f in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0)}))

    if n_testes >= 20:
        m = np.isfinite(alvo)
        y = alvo[m]
        for t1 in grelha_t1:
            e1 = ftlm.ewm(cargas, t1)[m]
            for t2 in grelha_t2:
                e2 = ftlm.ewm(cargas, t2)[m]
                # K1 e K2 por minimos quadrados, dados T1 e T2
                A = np.column_stack([np.ones(len(y)), e1, -e2])
                try:
                    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
                except Exception:
                    continue
                tentativas += 1
                if coef[1] <= 0 or coef[2] <= 0:
                    # K negativos nao tem sentido fisico (Banister): o fitness
                    # tem de somar e a fadiga tem de subtrair.
                    # Guardamos o melhor rejeitado so para diagnostico.
                    rejeitados += 1
                    prev_r = A @ coef
                    sr = float(((y - prev_r) ** 2).sum())
                    st = float(((y - y.mean()) ** 2).sum())
                    if st > 0:
                        r2r = 1 - sr / st
                        if r2r > melhor_rejeitado['r2']:
                            melhor_rejeitado = {
                                'k1': round(float(coef[1]), 3),
                                'k2': round(float(coef[2]), 3),
                                't1': float(t1), 't2': float(t2),
                                'r2': round(r2r, 4)}
                    continue
                prev = A @ coef
                ss_res = float(((y - prev) ** 2).sum())
                ss_tot = float(((y - y.mean()) ** 2).sum())
                if ss_tot <= 0:
                    continue
                r2 = 1 - ss_res / ss_tot
                if r2 > melhor['r2']:
                    melhor = {'k1': float(coef[1]), 'k2': float(coef[2]),
                              't1': float(t1), 't2': float(t2), 'r2': r2}
                    p0 = float(coef[0])
                    ajustado = True

    fit = ftlm.ewm(cargas, melhor['t1'])
    fad = ftlm.ewm(cargas, melhor['t2'])
    p_hat = p0 + melhor['k1'] * fit - melhor['k2'] * fad
    suave = _savgol(p_hat, 21, 3)
    sd = _banda_sd(p_hat, 14)

    # porque e que falhou, em detalhe — para se poder comparar modalidades
    if ajustado:
        motivo = 'ok'
    elif n_testes < 20:
        motivo = 'poucos_pontos_cp'
    elif tentativas == 0:
        motivo = 'sem_tentativas'
    elif rejeitados == tentativas:
        motivo = 'k_negativo'
    else:
        motivo = 'r2_nao_positivo'

    return {
        'ajustado': ajustado,
        'motivo': motivo,
        'tentativas': tentativas,
        'rejeitados_k_negativo': rejeitados,
        'n_testes': n_testes,
        'p0': round(p0, 1),
        'k1': round(melhor['k1'], 3), 'k2': round(melhor['k2'], 3),
        't1': round(melhor['t1'], 1), 't2': round(melhor['t2'], 1),
        'grelha_t1': list(grelha_t1), 'grelha_t2': list(grelha_t2),
        'grelha_calibrada': bool(tau_sugerido or lag_hrv_sugerido),
        'r2': round(melhor['r2'], 4),
        'melhor_rejeitado': (melhor_rejeitado
                             if melhor_rejeitado['k1'] is not None else None),
        'nota': _nota_homeo(ajustado, n_testes, tentativas, rejeitados,
                            melhor['r2']),
        'serie': [{'date': datas[i],
                   'p_hat': round(float(p_hat[i]), 1),
                   'p_hat_suave': round(float(suave[i]), 1),
                   'banda_sup': round(float(suave[i] + sd[i]), 1),
                   'banda_inf': round(float(suave[i] - sd[i]), 1),
                   'fitness': round(float(fit[i]), 1),
                   'fadiga': round(float(fad[i]), 1)} for i in range(n)],
    }


def _nota_homeo(ajustado, n_testes, tentativas, rejeitados, r2):
    if ajustado:
        return f'K e tau ajustados aos teus dados de CP (R² {r2:.3f})'
    if n_testes < 20:
        return (f'so {n_testes} pontos de CP (precisa de 20) — '
                'a usar tau 42/7 do PMC classico')
    if tentativas and rejeitados == tentativas:
        return ('nenhuma combinacao deu K₁ e K₂ positivos: a CP nao segue o '
                'padrao fitness-menos-fadiga neste periodo — a usar tau 42/7')
    return 'sem ajuste com R² positivo — a usar tau 42/7 do PMC classico'


def _savgol(y, janela=21, grau=3):
    """Savitzky-Golay: ajusta um polinomio local por minimos quadrados.

    Ao contrario da media movel, preserva a amplitude dos picos — e por isso
    que o dashboard o usa para a reserva de performance.
    """
    import numpy as np
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    if n < grau + 2:
        return y.copy()
    j = min(janela, n if n % 2 == 1 else n - 1)
    if j % 2 == 0:
        j -= 1
    j = max(j, grau + 2 if (grau + 2) % 2 == 1 else grau + 3)
    if j > n:
        return y.copy()
    meio = j // 2

    # coeficientes do filtro: linha central da pseudo-inversa de Vandermonde
    x = np.arange(-meio, meio + 1, dtype=np.float64)
    A = np.vander(x, grau + 1, increasing=True)
    coef = np.linalg.pinv(A)[0]

    ext = np.concatenate([np.full(meio, y[0]), y, np.full(meio, y[-1])])
    return np.array([float(np.dot(coef, ext[i:i + j])) for i in range(n)])


def _banda_sd(y, janela=14):
    """Desvio padrao movel centrado, para a banda +/-1 SD."""
    import numpy as np
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    out = np.zeros(n)
    meio = janela // 2
    for i in range(n):
        seg = y[max(0, i - meio):min(n, i + meio + 1)]
        seg = seg[np.isfinite(seg)]
        if len(seg) >= 3:
            out[i] = float(seg.std())
    return out


def homeostatico_por_modalidade(serie_classica, sessoes, modalidades):
    """Reserva de performance calculada por modalidade.

    Cada desporto tem a sua CP e a sua carga, por isso os K e os tau saem
    diferentes: o Ski absorve e dissipa a outro ritmo que o Bike. Sobrepor
    as curvas mostra qual das modalidades esta a puxar a reserva global.
    """
    out = {}
    for mod in modalidades:
        ses = [s for s in sessoes if s.get('type') == mod]
        if len(ses) < 30:
            continue
        # a serie diaria tem de cobrir o mesmo intervalo que a global,
        # senao as curvas nao alinham no grafico
        datas = [d['date'] for d in serie_classica]
        por_dia = {}
        for s in ses:
            por_dia[s['date']] = por_dia.get(s['date'], 0.0) + float(s.get('tl') or 0)
        serie_mod = [{'date': d, 'load': round(por_dia.get(d, 0.0), 1)}
                     for d in datas]
        r = modelo_homeostatico(serie_mod, ses)
        if r:
            out[mod] = r
    return out


def _media_periodo(linhas, campo, ini, fim, detalhe=None):
    """Media de um campo num intervalo. Se detalhe for um dict, escreve la
    quantos dias entraram e de que datas — para se poder auditar diferencas
    entre implementacoes."""
    import numpy as np
    sel = [r for r in linhas
           if r.get(campo) is not None and ini <= r['date'] <= fim]
    vals = [r[campo] for r in sel]
    if detalhe is not None:
        detalhe['n'] = len(vals)
        detalhe['primeiro'] = sel[0]['date'] if sel else None
        detalhe['ultimo'] = sel[-1]['date'] if sel else None
    return float(np.mean(vals)) if vals else float('nan')


def indice_alostatico(serie_classica, homeostatico, wellness,
                      p_ant=None, p_rec=None):
    """Adaptacao vs sobrecarga alostatica, em 6 dimensoes.

    Compara dois periodos. Cada dimensao da um score entre -1 e +1:
      score = sinal · clip(variacao% / 50, -1, +1)
    onde o sinal e -1 nas dimensoes em que subir e mau (HR de repouso).
    """
    import numpy as np
    from datetime import datetime, timedelta

    if not serie_classica:
        return None

    datas = [d['date'] for d in serie_classica]
    fim = datas[-1]
    if not p_rec:
        ini_rec = (datetime.strptime(fim, '%Y-%m-%d') - timedelta(days=59)
                   ).strftime('%Y-%m-%d')
        p_rec = (ini_rec, fim)
    if not p_ant:
        f_ant = (datetime.strptime(p_rec[0], '%Y-%m-%d') - timedelta(days=1)
                 ).strftime('%Y-%m-%d')
        i_ant = (datetime.strptime(f_ant, '%Y-%m-%d') - timedelta(days=59)
                 ).strftime('%Y-%m-%d')
        p_ant = (i_ant, f_ant)

    ph = ((homeostatico or {}).get('serie')) or []
    w = wellness or []

    # Escala de referencia do TSB: o desvio-padrao do proprio atleta, em vez
    # de um numero fixo. Uma variacao de 1 SD passa a valer o mesmo para
    # qualquer pessoa, seja o TSB dela estavel ou muito oscilante.
    tsbs = [r['tsb'] for r in serie_classica if r.get('tsb') is not None]
    ref_tsb = float(np.std(tsbs)) if len(tsbs) >= 30 else 25.0
    ref_tsb = max(ref_tsb, 1.0)
    ref_tsb_fonte = 'desvio do atleta' if len(tsbs) >= 30 else 'referencia 25 au'

    dims = []
    # 'ref' != None -> a dimensao usa diferenca absoluta em vez de percentagem.
    # O TSB oscila em torno de zero: dividir por uma base proxima de zero faz
    # a percentagem explodir. Um TSB de 3.4 -> 1.2 e uma variacao de 2 pontos,
    # mas da -65% e satura o score, enquanto -47 -> -50 (variacao maior) da -6%.
    # Escala de referencia 25 au: e a largura tipica das bandas de forma.
    for nome, uni, bom, fonte, campo, ref in [
            ('Reserva pico', 'u.a.', True, ph, 'p_hat', None),
            ('CTL fitness', 'au', True, serie_classica, 'ctl', None),
            ('Recovery TSB', 'au', True, serie_classica, 'tsb', ref_tsb),
            ('HRV matinal', 'ms', True, w, 'hrv', None),
            ('HR repouso', 'bpm', False, w, 'rhr', None),
            ('Sono', '/5', True, w, 'sleep_quality', None)]:
        da, dr = {}, {}
        va = _media_periodo(fonte, campo, p_ant[0], p_ant[1], da)
        vr = _media_periodo(fonte, campo, p_rec[0], p_rec[1], dr)
        dims.append((nome, uni, bom, va, vr, (da, dr), ref))

    linhas, scores = [], []
    for nome, uni, bom_positivo, ant, rec, det, ref in dims:
        if not np.isfinite(ant) or not np.isfinite(rec):
            linhas.append({'dim': nome, 'unidade': uni, 'ant': None,
                           'rec': None, 'delta_pct': None, 'score': None,
                           'n_ant': det[0].get('n', 0), 'n_rec': det[1].get('n', 0),
                           'motivo': 'sem dados'})
            continue

        delta = rec - ant
        if ref is not None:
            # diferenca absoluta escalada: imune a base proxima de zero
            dp = delta / ref * 100
            base_metodo = f'diferenca absoluta / {ref:.1f}'
        elif abs(ant) < 0.001:
            linhas.append({'dim': nome, 'unidade': uni, 'ant': round(ant, 2),
                           'rec': round(rec, 2), 'delta_pct': None, 'score': None,
                           'n_ant': det[0].get('n', 0), 'n_rec': det[1].get('n', 0),
                           'motivo': 'base proxima de zero'})
            continue
        else:
            dp = delta / abs(ant) * 100
            base_metodo = 'variacao percentual'
        sc = (1 if bom_positivo else -1) * float(np.clip(dp / 50.0, -1.0, 1.0))
        scores.append(sc)
        linhas.append({'dim': nome, 'unidade': uni,
                       'ant': round(ant, 2), 'rec': round(rec, 2),
                       'delta_pct': round(dp, 2), 'score': round(sc, 4),
                       'bom_positivo': bom_positivo,
                       'delta_abs': round(delta, 2), 'metodo': base_metodo,
                       # quantos dias entraram em cada media — a causa mais
                       # comum de duas implementacoes darem numeros diferentes
                       'n_ant': det[0].get('n', 0), 'n_rec': det[1].get('n', 0),
                       'datas_ant': [det[0].get('primeiro'), det[0].get('ultimo')],
                       'datas_rec': [det[1].get('primeiro'), det[1].get('ultimo')],
                       'saturado': abs(dp) >= 50})

    total = float(np.clip(np.mean(scores), -1, 1)) if scores else 0.0
    if total > 0.20:
        estado = {'label': 'BOA ADAPTACAO', 'cor': '#27ae60',
                  'desc': 'O corpo responde positivamente a carga'}
    elif total > -0.10:
        estado = {'label': 'ESTAVEL', 'cor': '#f39c12',
                  'desc': 'Sistema em equilibrio — sem adaptacao clara nem sobrecarga'}
    else:
        estado = {'label': 'SOBRECARGA', 'cor': '#e74c3c',
                  'desc': 'O corpo nao esta a compensar a carga'}

    return {'total': round(total, 4), 'n_dims': len(scores),
            'estado': estado, 'dimensoes': linhas,
            'periodo_anterior': list(p_ant), 'periodo_recente': list(p_rec),
            'formula': 'score = sinal * clip(delta_pct / 50, -1, +1); '
                       'total = media dos scores com dados',
            'ref_tsb': round(ref_tsb, 2), 'ref_tsb_fonte': ref_tsb_fonte,
            'scores': [round(s, 4) for s in scores],
            'p_hat_disponivel': len(ph),
            'wellness_disponivel': len(w)}


# ══════════════════════════════════════════════════════════════════════════
# FMT 5x5 (Della Mattia 2019, §02) — tensor completo e mapa de atencao
# ══════════════════════════════════════════════════════════════════════════

def calcular_fmt(sessoes, wellness, serie_classica, janela=28, desde_hrv=None):
    """Sequencia de tensores FMT 5x5 e mapa de atencao sobre 28 dias.

    As cinco dimensoes sao as da Figura 1 do paper: Load, HRV, W', Sleep e
    WEED. Dimensoes sem dados suficientes ficam de fora e o tensor encolhe —
    e melhor do que enche-las com zeros, que criariam covariancias falsas.
    """
    import numpy as np
    import fmt as _fmt

    if not serie_classica:
        return None

    datas = [d['date'] for d in serie_classica]
    n = len(datas)
    dims = {'Load': np.array([d['load'] for d in serie_classica], dtype=np.float64)}

    # so o wellness dentro da janela util entra no tensor
    if desde_hrv:
        wellness = [w for w in (wellness or []) if w['date'] >= desde_hrv]
    idx = {w['date']: w for w in (wellness or [])}

    def do_wellness(campo):
        v = np.array([(idx.get(d) or {}).get(campo) if idx.get(d) else None
                      for d in datas], dtype=object)
        return np.array([x if isinstance(x, (int, float)) else np.nan
                         for x in v], dtype=np.float64)

    hrv = do_wellness('hrv')
    if np.isfinite(hrv).sum() >= janela + 10:
        with np.errstate(all='ignore'):
            dims['HRV'] = np.where(hrv > 0, np.log(hrv), np.nan)

    wp = _serie_por_dia(sessoes, datas, 'w_prime', 'mean')
    if np.isfinite(wp).sum() >= janela + 10:
        dims["W'"] = wp

    sono = do_wellness('sleep_quality')
    if np.isfinite(sono).sum() >= janela + 10:
        dims['Sleep'] = sono

    partes = []
    for campo in ('stress', 'soreness', 'fatiga'):
        v = do_wellness(campo)
        if np.isfinite(v).sum() >= janela + 10:
            partes.append(_zscore_rolling_28(v, datas))
    if partes:
        arr = np.array(partes)
        validos = np.isfinite(arr).any(axis=0)
        weed = np.full(arr.shape[1], np.nan)
        if validos.any():
            with np.errstate(all='ignore'):
                weed[validos] = np.nanmean(arr[:, validos], axis=0)
        if np.isfinite(weed).sum() >= janela + 10:
            dims['WEED'] = weed

    if len(dims) < 2:
        return {'erro': 'sao precisas pelo menos 2 dimensoes com dados',
                'dimensoes': list(dims)}

    # Interpolar SO buracos curtos. Um dia sem resposta ao formulario nao deve
    # apagar a janela de 28 dias; mas np.interp sobre a serie toda preenche
    # tambem lacunas de anos, e extrapola plano antes do primeiro valor.
    # Se o HRV so comeca em 2024, isso transformava 2021-2023 numa constante
    # inventada — 56% da serie — e a calibracao corria sobre dados falsos.
    MAX_LACUNA = 7
    cobertura = {}
    for k, v in dims.items():
        m = np.isfinite(v)
        cobertura[k] = {
            'dias_reais': int(m.sum()),
            'primeiro': datas[int(np.argmax(m))] if m.any() else None,
            'ultimo': datas[n - 1 - int(np.argmax(m[::-1]))] if m.any() else None,
        }
        if m.sum() < 2 or not (~m).any():
            continue
        idx = np.flatnonzero(m)
        preenchido = v.copy()
        for a, b in zip(idx[:-1], idx[1:]):
            if 1 < b - a <= MAX_LACUNA + 1:
                preenchido[a + 1:b] = np.interp(np.arange(a + 1, b), [a, b],
                                                [v[a], v[b]])
        dims[k] = preenchido
        cobertura[k]['apos_interpolacao'] = int(np.isfinite(preenchido).sum())

    # Janela util: onde TODAS as dimensoes tem dados. E aqui que o tensor
    # e a calibracao podem correr sem inventar nada.
    todas = np.ones(n, dtype=bool)
    for v in dims.values():
        todas &= np.isfinite(v)
    if todas.sum() < janela + 30:
        return {'erro': 'sem periodo em que todas as dimensoes tenham dados',
                'dimensoes': list(dims), 'cobertura': cobertura,
                'dias_com_todas': int(todas.sum())}
    ini_util = int(np.argmax(todas))
    fim_util = n - 1 - int(np.argmax(todas[::-1]))

    tensores, kappa, eig, nomes = _fmt.construir(dims, janela)
    if tensores is None:
        return None

    # ── calibracao nos dados deste atleta ────────────────────────────────
    import calibracao as _cal
    cp_serie = _serie_por_dia(sessoes, datas, 'cp', 'mean')
    # Calibrar contra o LnRMSSD directamente, nao contra a tendencia: a
    # tendencia e ja uma transformacao (nivel + declive em z-score) e destroi
    # a relacao directa entre carga acumulada e nivel de HRV. Testado: com
    # tau real de 14 dias, calibrar pela tendencia recupera 3; pelo LnRMSSD
    # recupera 14.
    hrv_para_calibrar = dims.get('HRV')
    if hrv_para_calibrar is not None and np.isfinite(hrv_para_calibrar).sum() < 30:
        hrv_para_calibrar = None
    l1_hist = []
    for linha in eig:
        v = linha[np.isfinite(linha)]
        v = v[v > 0]
        l1_hist.append(float(v[0] / v.sum()) if len(v) >= 2 else None)

    # A calibracao corre so no periodo em que os dados existem mesmo.
    sl = slice(ini_util, fim_util + 1)
    cal = _cal.calibrar_tudo(
        carga=dims['Load'][sl],
        hrv_trend=(hrv_para_calibrar[sl] if hrv_para_calibrar is not None
                   else None),
        cp=(cp_serie[sl] if np.isfinite(cp_serie[sl]).sum() >= 30 else None),
        kappa=kappa[sl],
        lambda1=l1_hist[ini_util:fim_util + 1])
    cal['periodo'] = {
        'de': datas[ini_util], 'ate': datas[fim_util],
        'dias': fim_util - ini_util + 1,
        'nota': 'periodo em que todas as dimensoes tem dados; fora dele nao '
                'se calibra para nao inventar valores',
    }
    cal['cobertura_por_dimensao'] = cobertura

    # Um parametro so e usado se medir o que diz medir.
    #
    # O canal 1 e um decaimento de FADIGA: exige que mais carga acumulada
    # ande com HRV mais baixo. Se a correlacao sai positiva, o que foi
    # medido e causalidade invertida (treina-se mais quando o HRV esta bom)
    # — usar esse tau como decaimento de fadiga seria pior do que usar o
    # valor de referencia, porque parece individualizado e nao e.
    import calibracao as _c
    rejeitados = {}

    def _usar(chave, defeito, exigir_negativo=False):
        v = cal.get(chave) or {}
        if v.get('fonte') != 'dados' or v.get('valor') is None:
            return defeito
        if exigir_negativo and (v.get('r') or 0) > 0:
            rejeitados[chave] = {
                'valor_encontrado': v.get('valor'), 'r': v.get('r'),
                'motivo': 'correlacao positiva — mede causalidade invertida, '
                          'nao decaimento de fadiga',
                'usado': defeito}
            return defeito
        pp = v.get('p_permutacao')
        if pp is not None and pp >= 0.05:
            nu = v.get('distribuicao_nula') or {}
            rejeitados[chave] = {
                'valor_encontrado': v.get('valor'), 'p_permutacao': pp,
                'motivo': f'p corrigido por permutacao = {pp}: o |r| obtido '
                          f'esta dentro do acaso (mediana nula '
                          f"{nu.get('nulo_mediana')})",
                'usado': defeito}
            return defeito
        if (v.get('r2') or 0) < 0.02:
            rejeitados[chave] = {
                'valor_encontrado': v.get('valor'), 'r2': v.get('r2'),
                'motivo': f"explica so {(v.get('r2') or 0)*100:.1f}% da "
                          'variacao — abaixo do minimo utilizavel',
                'usado': defeito}
            return defeito
        return v['valor']

    params = {
        'tau_carga': _usar('canal1_tau', _c.REFERENCIA['tau_carga'], True),
        'lag_hrv': _usar('canal2_lag', _c.REFERENCIA['lag_hrv'], True),
        'lag_super': _usar('canal3_lag', _c.REFERENCIA['lag_super']),
        'largura_super': (cal['canal3_lag'].get('largura', 3.5)
                          if cal.get('canal3_lag', {}).get('fonte') == 'dados'
                          else _c.REFERENCIA['largura_super']),
        'tau_risco': max(2.0, float(_usar('canal4_lag',
                                          _c.REFERENCIA['tau_risco']) or 8)),
    }
    cal['parametros_rejeitados'] = rejeitados

    ultimo = None
    for t in range(n - 1, -1, -1):
        if np.isfinite(kappa[t]):
            ultimo = t
            break
    if ultimo is None:
        return {'erro': 'sem janelas completas de 28 dias',
                'dimensoes': nomes}

    fonte_por_canal = {'load': 'canal1_tau', 'hrv': 'canal2_lag',
                       'super': 'canal3_lag', 'risco': 'canal4_lag'}
    canais = {}
    for c in _fmt.CANAIS:
        a = _fmt.atencao(tensores, kappa, eig, nomes, ultimo, c, janela, params)
        if a:
            info = cal.get(fonte_por_canal.get(c, ''), {})
            canais[c] = {**a, 'datas': [datas[i] for i in a['idx']],
                         **_fmt.CANAIS[c],
                         'calibracao': {k: info.get(k) for k in
                                        ('fonte', 'valor', 'r', 'p', 'n',
                                         'motivo', 'janela')}
                         if info else None}

    return {
        'dimensoes': nomes,
        'janela': janela,
        'dia': datas[ultimo],
        'dia_idx': ultimo,
        'resumo': _fmt.resumo_dia(tensores, kappa, eig, nomes, ultimo,
                                  cal.get('limiares_lambda1')),
        'calibracao': cal,
        'params_usados': params,
        'canais': canais,
        'serie': [{'date': datas[i],
                   'kappa': (round(float(kappa[i]), 4)
                             if np.isfinite(kappa[i]) else None),
                   'lambda1': (round(float(eig[i][0] / eig[i][eig[i] > 0].sum()), 4)
                               if np.isfinite(eig[i]).all() and (eig[i] > 0).any()
                               else None)}
                  for i in range(n)],
        'nota_atencao': ('Os canais do paper emergem de um Transformer treinado '
                         'em 30 atletas. Aqui sao kernels explicitos cujos '
                         'parametros sao estimados por correlacao cruzada nas '
                         'tuas series — ve a coluna "fonte" de cada canal. '
                         'Onde diz "referencia", o valor vem do paper e '
                         'descreve outros atletas, nao ti.'),
    }


def _janela_hrv(datas, wellness, desde=None):
    """Primeiro e ultimo dia com HRV real, respeitando um limite opcional.

    As analises que dependem de HRV so devem correr onde ha HRV. Sem isto,
    anos inteiros sem medicoes entram como se tivessem dados.
    """
    idx = {w['date']: w for w in (wellness or [])}
    com = [d for d in datas
           if isinstance((idx.get(d) or {}).get('hrv'), (int, float))
           and (not desde or d >= desde)]
    if not com:
        return None, None, 0
    return com[0], com[-1], len(com)


def teste_eventos(sessoes, wellness, serie_classica, modalidades, desde=None):
    """Teste por eventos: HRV depois de dias duros vs dias leves.

    Corre no agregado e por modalidade. E mais sensivel que a correlacao —
    se nem aqui houver efeito, a limitacao esta nos dados, nao no metodo.
    """
    import numpy as np
    import calibracao as _cal

    if not serie_classica or not wellness:
        return None

    datas_todas = [d['date'] for d in serie_classica]
    de, ate, n_hrv = _janela_hrv(datas_todas, wellness, desde)
    if not de or n_hrv < 100:
        return {'erro': f'so {n_hrv} dias com HRV (precisa de 100)',
                'desde_pedido': desde}

    # cortar tudo para o periodo em que ha HRV
    sel = [i for i, d in enumerate(datas_todas) if de <= d <= ate]
    datas = [datas_todas[i] for i in sel]
    idx_w = {w['date']: w for w in wellness}
    hrv = np.array([(idx_w.get(d) or {}).get('hrv') or np.nan for d in datas],
                   dtype=np.float64)
    carga = np.array([serie_classica[i]['load'] for i in sel], dtype=np.float64)
    out = {'periodo': {'de': de, 'ate': ate, 'dias': len(datas),
                       'dias_com_hrv': int(np.isfinite(hrv).sum()),
                       'nota': 'restrito ao periodo com HRV real'},
           'agregado': _cal.teste_dias_duros(datas, carga, hrv),
           'por_modalidade': {}}

    for mod in modalidades:
        ses = [s for s in sessoes if s.get('type') == mod and de <= s['date'] <= ate]
        if len(ses) < 80:
            out['por_modalidade'][mod] = {
                'motivo': f'so {len(ses)} sessoes no periodo com HRV'}
            continue
        cm = np.nan_to_num(_serie_por_dia(ses, datas, 'tl', 'sum'))
        out['por_modalidade'][mod] = _cal.teste_dias_duros(datas, cm, hrv)

    efeitos = [('agregado', out['agregado'].get('maior_efeito'))]
    for m, v in out['por_modalidade'].items():
        efeitos.append((m, v.get('maior_efeito')))
    validos = [(k, v) for k, v in efeitos if v is not None]
    if validos:
        k, v = max(validos, key=lambda x: abs(x[1]))
        out['maior_efeito_global'] = {'onde': k, 'cohen_d': v}
    return out


def calibrar_com_ancora(serie_classica, modalidades, secs=1200,
                        minimo_testes=25):
    """Calibra contra os dias de esforco maximo, nao contra a CP de todas as
    sessoes.

    A CP de uma sessao de Z2 tranquilo e baixa porque escolheste treinar
    suave, nao porque estas pior — isso e ruido a afogar o sinal. Nos dias
    de esforco maximo a CP mede capacidade, e o estimulo foi decidido pelo
    esforco e nao pelo estado, o que reduz a causalidade invertida.
    """
    import numpy as np
    import calibracao as _cal
    import protocolo as _prot
    import db

    if not serie_classica:
        return None

    datas = [d['date'] for d in serie_classica]
    idx = {d: i for i, d in enumerate(datas)}
    n = len(datas)
    carga_total = np.array([d['load'] for d in serie_classica], dtype=np.float64)

    out = {'duracao': secs, 'por_modalidade': {}}
    for mod in modalidades:
        curvas = db.load_power_curves(mod)
        if not curvas:
            continue
        det = _prot.detectar_testes(curvas)
        ancora = _prot.serie_ancora(det, mod, secs)
        if len(ancora) < minimo_testes:
            out['por_modalidade'][mod] = {
                'n_testes': len(ancora),
                'motivo': f'so {len(ancora)} testes (precisa de {minimo_testes})'}
            continue

        alvo = np.full(n, np.nan)
        for t in ancora:
            i = idx.get(t['date'])
            if i is not None:
                alvo[i] = t['valor']

        # carga da propria modalidade
        r = _cal.calibrar_lag(carga_total, alvo, range(5, 29), None, 'lag_super')
        primeiro = ancora[0]['date']
        ultimo = ancora[-1]['date']
        out['por_modalidade'][mod] = {
            'n_testes': len(ancora),
            'de': primeiro, 'ate': ultimo,
            'lag': r.get('valor'), 'r': r.get('r'), 'r2': r.get('r2'),
            'forca': r.get('forca'),
            'p_permutacao': r.get('p_permutacao'),
            'distribuicao_nula': r.get('distribuicao_nula'),
            'sobrevive': (r.get('p_permutacao') is not None
                          and r['p_permutacao'] < 0.05),
        }

    validos = [(m, v) for m, v in out['por_modalidade'].items()
               if v.get('sobrevive')]
    out['sobrevivem'] = [m for m, _ in validos]
    if validos:
        m, v = max(validos, key=lambda x: x[1].get('r2') or 0)
        out['leitura'] = (
            f"{m}: a CP nos dias de teste responde a carga {v['lag']} dias "
            f"antes (r={v['r']}, {v['r2']*100:.0f}% da variacao, "
            f"p permutacao {v['p_permutacao']}, {v['n_testes']} testes).")
    else:
        out['leitura'] = (
            'Nenhuma modalidade sobrevive a correccao por permutacao, mesmo '
            'usando so os dias de esforco maximo. Com esta ancora — que e a '
            'melhor disponivel — continua sem haver relacao detectavel entre '
            'carga e performance.')
    return out


def calibrar_segmentado(sessoes, wellness, serie_classica, modalidades,
                        desde=None):
    """Calibracao por modalidade e por ano, para ver onde o sinal existe.

    No agregado de anos e desportos, relacoes reais diluem-se. Isto separa
    para se poder comparar: se um segmento tiver r2 muito acima do agregado,
    a relacao existe la e some ao juntar tudo.
    """
    import numpy as np
    import calibracao as _cal

    if not serie_classica:
        return None

    datas_todas = [d['date'] for d in serie_classica]
    de, ate, n_hrv = _janela_hrv(datas_todas, wellness, desde)
    if de:
        sel = [i for i, d in enumerate(datas_todas) if de <= d <= ate]
    else:
        sel = list(range(len(datas_todas)))
    datas = [datas_todas[i] for i in sel]
    n = len(datas)
    carga_total = np.array([serie_classica[i]['load'] for i in sel],
                           dtype=np.float64)

    idx_w = {w['date']: w for w in (wellness or [])}
    hrv = np.array([(idx_w.get(d) or {}).get('hrv') or np.nan for d in datas],
                   dtype=np.float64)
    with np.errstate(all='ignore'):
        hrv = np.where(hrv > 0, np.log(hrv), np.nan)
    cp = _serie_por_dia(sessoes, datas, 'cp', 'mean')

    out = {}

    # ── por modalidade: carga so dessa modalidade, HRV e CP dessa modalidade ─
    por_mod = {}
    for mod in modalidades:
        ses = [s for s in sessoes if s.get('type') == mod]
        if len(ses) < 60:
            continue
        carga_mod = np.nan_to_num(_serie_por_dia(ses, datas, 'tl', 'sum'))
        cp_mod = _serie_por_dia(ses, datas, 'cp', 'mean')
        # dias com actividade desta modalidade, mais os 30 dias seguintes
        activo = carga_mod > 0
        janela = activo.copy()
        for k in range(1, 31):
            janela[k:] |= activo[:-k]
        seg = _cal.calibrar_por_segmento({mod: janela}, carga_mod, hrv, cp_mod)
        por_mod[mod] = seg.get(mod)
    out['por_modalidade'] = por_mod

    # ── por ano civil ────────────────────────────────────────────────────
    anos = {}
    for d in datas:
        anos.setdefault(d[:4], []).append(d)
    segs = {}
    for ano, ds in anos.items():
        if len(ds) < 120:
            continue
        mask = np.array([d[:4] == ano for d in datas])
        segs[ano] = mask
    out['por_ano'] = (_cal.calibrar_por_segmento(segs, carga_total, hrv, cp)
                      if segs else {})

    # ── agregado, para comparar ──────────────────────────────────────────
    tudo = np.ones(n, dtype=bool)
    ag = _cal.calibrar_por_segmento({'agregado': tudo}, carga_total, hrv, cp)
    out['agregado'] = ag.get('agregado')
    # tambem restringimos os segmentos por ano ao periodo com HRV, ja feito
    # acima ao cortar `datas`

    base = (out['agregado'] or {}).get('melhor_r2') or 0.0
    melhores = []
    for grupo, dic in (('modalidade', por_mod), ('ano', out['por_ano'])):
        for k, v in (dic or {}).items():
            if k.startswith('_') or not isinstance(v, dict):
                continue
            r2 = v.get('melhor_r2')
            if r2 and r2 > max(base * 2, 0.02):
                melhores.append({'grupo': grupo, 'nome': k, 'r2': r2,
                                 'n_dias': v.get('n_dias')})
    melhores.sort(key=lambda x: -x['r2'])
    out['destaques'] = melhores[:5]
    out['r2_agregado'] = round(base, 4)
    out['periodo'] = {'de': de, 'ate': ate, 'dias': n,
                      'dias_com_hrv': n_hrv,
                      'nota': ('restrito ao periodo com HRV real'
                               if de else 'sem HRV — so as series de carga/CP')}
    out['leitura'] = (
        f"Segmentos com sinal claramente acima do agregado ({base*100:.1f}%): "
        + (', '.join(f"{m['nome']} ({m['r2']*100:.0f}%)" for m in melhores[:3])
           if melhores else 'nenhum — a ausencia de sinal nao vem da mistura '
                           'de fases ou modalidades'))
    return out
