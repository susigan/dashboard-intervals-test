"""FTLM fraccionario — Della Mattia (2025).

Porta do repo dashboard (data.py + phase_detector.py) para esta app, sem
pandas nem scipy. Usa numpy, que ja e preciso para a covariancia do FMT.

Peças:
  ftlm_fractional   integral fraccionario Riemann-Liouville da carga
  hrv_trend         tendencia local do LnRMSSD por regressao movel 7d
  fit_gamma_*       procura o gamma que maximiza R² (performance / recuperacao)
  detect_phases     Build / Fatigue / Overreach / Recovery / Peak / Transition
  kappa_fmt         tensor metrico de fadiga: trace da covariancia movel
"""

import math
import numpy as np

MODS = ['Bike', 'Row', 'Ski', 'Run']
GAMMA_DEFAULT = 0.35
GAP_TREINO = 10          # dias sem treino a partir dos quais BUILD/FATIGUE nao fazem sentido

FASES = {
    'BUILD':      {'label': 'Build',      'cor': '#2980b9',
                   'desc': 'Acumulacao de carga — fitness a crescer'},
    'FATIGUE':    {'label': 'Fatigue',    'cor': '#e74c3c',
                   'desc': 'Carga alta com recuperacao autonomica comprometida'},
    'OVERREACH':  {'label': 'Overreach',  'cor': '#8e44ad',
                   'desc': 'HRV muito baixo + stress elevado + carga alta'},
    'RECOVERY':   {'label': 'Recovery',   'cor': '#27ae60',
                   'desc': 'Carga a reduzir, sistema autonomico a recuperar'},
    'PEAK':       {'label': 'Peak',       'cor': '#f39c12',
                   'desc': 'Carga estavel, HRV alto — forma potencial'},
    'TRANSITION': {'label': 'Transition', 'cor': '#95a5a6',
                   'desc': 'Estado intermedio — sem padrao claro'},
}


# ── kernel fraccionario ───────────────────────────────────────────────────

def ftlm_fractional(load, gamma_val, max_lag=None):
    """CTLγ(t) = Σ_k Load(t-k) · k^(γ-1) / Γ(γ)

    Memoria em lei de potencia: ao contrario do EWM, os treinos antigos nunca
    desaparecem por completo — so pesam cada vez menos.
    """
    load = np.asarray(load, dtype=np.float64)
    n = len(load)
    if n == 0:
        return np.zeros(0)
    ml = n if max_lag is None else min(n, max_lag)
    k = np.arange(1, ml + 1, dtype=np.float64)
    w = np.power(k, gamma_val - 1.0) / math.gamma(gamma_val)
    ctl = np.zeros(n)
    for t in range(1, n):
        lag = min(t, ml)
        seg = load[max(0, t - ml):t][::-1]     # mais recente primeiro
        ctl[t] = float(np.dot(seg, w[:lag]))
    return ctl


def _zscore(arr):
    arr = np.asarray(arr, dtype=np.float64)
    if not np.isfinite(arr).any():     # serie toda vazia: nada a normalizar
        return arr
    with np.errstate(all='ignore'):
        mu, sd = np.nanmean(arr), np.nanstd(arr)
    if not np.isfinite(mu):
        return arr
    return (arr - mu) / sd if sd > 1e-9 else arr - mu


def _linregress(x, y):
    """Declive e ordenada na origem. Substitui scipy.stats.linregress."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = len(x)
    if n < 2:
        return 0.0, float(y[0]) if n else 0.0
    mx, my = x.mean(), y.mean()
    den = float(((x - mx) ** 2).sum())
    if den < 1e-12:
        return 0.0, float(my)
    a = float(((x - mx) * (y - my)).sum() / den)
    return a, float(my - a * mx)


def hrv_trend(hrv, window=7):
    """Tendencia local do HRV por regressao movel.

    Devolve b_z + w·a_z, onde b e o nivel e a o declive da janela. O peso w
    e std(b)/std(a) — sai dos dados, nao e escolhido a mao.
    """
    hrv = np.asarray(hrv, dtype=np.float64)
    n = len(hrv)
    tb = np.full(n, np.nan)
    ta = np.full(n, np.nan)
    x = np.arange(window, dtype=np.float64)
    for t in range(window - 1, n):
        y = hrv[t - window + 1:t + 1]
        m = ~np.isnan(y)
        if m.sum() < 4:
            continue
        a, b = _linregress(x[m], y[m])
        ta[t], tb[t] = a, b

    b_z, a_z = _zscore(tb), _zscore(ta)
    sb, sa = np.nanstd(tb), np.nanstd(ta)
    w = float(np.clip(sb / sa, 0.1, 5.0)) if sa > 1e-9 else 1.0
    return np.where(np.isfinite(b_z) & np.isfinite(a_z), b_z + w * a_z, b_z)


def _r2(x, y):
    """R² da regressao linear entre duas series, ignorando NaN."""
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 5:
        return 0.0
    xv, yv = x[m], y[m]
    if np.std(xv) < 1e-12 or np.std(yv) < 1e-12:
        return 0.0
    r = float(np.corrcoef(xv, yv)[0, 1])
    return 0.0 if not np.isfinite(r) else r * r


def fit_gamma(load, alvo, lag=0, gama_min=0.10, gama_max=0.90, passo=0.05,
              max_lag=365, suavizar=0, r2_minimo=0.02, permutacoes=150):
    """Procura o γ que maximiza o R² entre CTLγ e a serie alvo.

    lag=0 para performance (CP no proprio dia), lag=1 para HRV (a carga de
    ontem explica o HRV de hoje).
    """
    load = np.asarray(load, dtype=np.float64)
    alvo = np.asarray(alvo, dtype=np.float64)
    if np.isfinite(alvo).sum() < 5:
        return {'gamma': GAMMA_DEFAULT, 'gamma_encontrado': None, 'r2': 0.0,
                'n': 0, 'aceite': False, 'na_fronteira': False,
                'p_permutacao': None, 'motivo': 'menos de 5 pontos de alvo'}

    if suavizar > 1:
        alvo = _media_movel(alvo, suavizar)

    melhor_g, melhor_r2 = GAMMA_DEFAULT, 0.0
    g = gama_min
    while g <= gama_max + 1e-9:
        ctl = ftlm_fractional(load, g, max_lag)
        if lag:
            r2 = _r2(ctl[:-lag], alvo[lag:])
        else:
            r2 = _r2(ctl, alvo)
        if r2 > melhor_r2:
            melhor_g, melhor_r2 = g, r2
        g += passo
    n = int(np.isfinite(alvo).sum())

    # O mesmo rigor que se aplica aos canais tem de valer para o proprio
    # gamma. Sem isto, um gamma escolhido no ruido muda o CTLgamma em duas
    # ordens de grandeza: com gamma=0.9 o expoente e -0.1 e a soma quase nao
    # decai (dezenas de milhar); com gamma=0.1 converge (dezenas).
    na_fronteira = abs(melhor_g - gama_min) < 1e-9 or abs(melhor_g - gama_max) < 1e-9

    p_perm = None
    if permutacoes and n >= 60:
        curvas = []
        for g in np.arange(gama_min, gama_max + 1e-9, passo):
            c = ftlm_fractional(load, float(g), max_lag)
            curvas.append(c[:-lag] if lag else c)
        P = np.vstack(curvas)
        y = alvo[lag:] if lag else alvo
        p_perm = _p_perm_matriz(P, y, permutacoes)

    aceite = (melhor_r2 >= r2_minimo
              and not na_fronteira
              and (p_perm is None or p_perm < 0.05))

    return {
        'gamma': round(melhor_g, 3) if aceite else GAMMA_DEFAULT,
        'gamma_encontrado': round(melhor_g, 3),
        'r2': round(melhor_r2, 4), 'n': n,
        'aceite': aceite, 'na_fronteira': na_fronteira,
        'p_permutacao': p_perm,
        'motivo': (None if aceite else
                   ('gamma no extremo da grelha — nao e um optimo'
                    if na_fronteira else
                    (f'R2 de {melhor_r2:.4f} abaixo do minimo {r2_minimo}'
                     if melhor_r2 < r2_minimo else
                     f'p de permutacao {p_perm} — dentro do acaso'))),
    }


def _p_perm_matriz(P, y, n_perm=150, semente=0):
    """p por permutacao circular sobre uma matriz de candidatos."""
    P = np.asarray(P, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if P.ndim != 2 or P.shape[1] != len(y):
        return None
    mask = np.isfinite(y) & np.all(np.isfinite(P), axis=0)
    if mask.sum() < 60:
        return None
    Xm, ym = P[:, mask], y[mask]
    n = len(ym)
    Xc = Xm - Xm.mean(axis=1, keepdims=True)
    Xn = np.sqrt((Xc ** 2).sum(axis=1))
    ok = Xn > 1e-9
    if not ok.any():
        return None
    Xc, Xn = Xc[ok], Xn[ok]

    def melhor(v):
        vc = v - v.mean()
        vn = np.sqrt((vc ** 2).sum())
        return 0.0 if vn < 1e-9 else float(np.abs(Xc @ vc / (Xn * vn)).max())

    obs = melhor(ym)
    rng = np.random.default_rng(semente)
    margem = max(30, n // 10)
    if n - 2 * margem <= 1:
        return None
    ks = rng.integers(margem, n - margem, size=n_perm)
    nulos = np.array([melhor(np.roll(ym, int(k))) for k in ks])
    return round(float((1 + int((nulos >= obs).sum())) / (1 + len(nulos))), 4)


def _media_movel(arr, janela):
    arr = np.asarray(arr, dtype=np.float64)
    out = np.full(len(arr), np.nan)
    for i in range(len(arr)):
        seg = arr[max(0, i - janela + 1):i + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg):
            out[i] = seg.mean()
    return out


# ── percentis e declives moveis ───────────────────────────────────────────

def declive_movel(serie, janela=14):
    serie = np.asarray(serie, dtype=np.float64)
    n = len(serie)
    out = np.full(n, np.nan)
    x = np.arange(janela, dtype=np.float64)
    for t in range(janela - 1, n):
        y = serie[t - janela + 1:t + 1]
        m = np.isfinite(y)
        if m.sum() < max(3, janela // 2):
            continue
        a, _ = _linregress(x[m], y[m])
        out[t] = a
    return out


def percentis_moveis(serie, qs, janela=60, minimo=10):
    """Varios percentis moveis de uma so vez.

    A deteccao de fases precisa de 7 percentis das MESMAS janelas. Calcular
    cada um por separado repete o trabalho caro — ordenar. Aqui ordenamos as
    janelas uma vez e lemos todos os percentis dessa ordenacao.
    """
    serie = np.asarray(serie, dtype=np.float64)
    n = len(serie)
    saida = {q: np.full(n, np.nan) for q in qs}
    if n == 0:
        return saida

    ext = np.concatenate([np.full(janela - 1, np.nan), serie])
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        M = sliding_window_view(ext, janela)
    except Exception:
        for q in qs:
            for t in range(n):
                seg = serie[max(0, t - janela + 1):t + 1]
                seg = seg[np.isfinite(seg)]
                if len(seg) >= minimo:
                    saida[q][t] = float(np.quantile(seg, q))
        return saida

    k = np.isfinite(M).sum(axis=1)          # validos por janela
    tem = k >= minimo
    if not tem.any():
        return saida

    # ordenar uma vez: os NaN vao para o fim
    S = np.sort(M[tem], axis=1)
    kk = k[tem].astype(np.float64)
    linhas = np.arange(S.shape[0])

    for q in qs:
        # interpolacao linear, igual ao metodo por defeito do numpy
        pos = q * (kk - 1)
        lo = np.floor(pos).astype(int)
        hi = np.minimum(lo + 1, (kk - 1).astype(int))
        frac = pos - lo
        vals = S[linhas, lo] * (1 - frac) + S[linhas, hi] * frac
        saida[q][tem] = vals
    return saida


def percentil_movel(serie, q, janela=60, minimo=10):
    """Um so percentil. Atalho para percentis_moveis."""
    return percentis_moveis(serie, [q], janela, minimo)[q]

def zscore_movel(serie, janela=60):
    serie = np.asarray(serie, dtype=np.float64)
    n = len(serie)
    out = np.full(n, np.nan)
    for t in range(n):
        seg = serie[max(0, t - janela + 1):t + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg) >= 10 and np.std(seg) > 1e-9:
            out[t] = (serie[t] - seg.mean()) / seg.std()
    return out


def _moda_movel_3(fases):
    """Suaviza a serie de fases com a moda de 3 dias, para nao piscar."""
    n = len(fases)
    out = list(fases)
    for t in range(1, n - 1):
        janela = [fases[t - 1], fases[t], fases[t + 1]]
        for f in set(janela):
            if janela.count(f) >= 2:
                out[t] = f
                break
    return np.array(out, dtype=object)


# ── FMT: tensor metrico de fadiga ─────────────────────────────────────────

def explicar_kappa(k, k_media=None, k_desvio=None):
    """Interpretação simples de κ para o utilizador.
    
    κ = trace(matriz covariância) = soma das variâncias das 5 dimensões
    
    Alto κ = múltiplos sistemas instáveis (carga, HRV, W', sono, WEED)
    Baixo κ = sistemas estáveis, previsíveis
    
    Args:
        k: valor de κ hoje
        k_media: κ histórico médio (baseline)
        k_desvio: κ histórico desvio padrão
    
    Returns:
        dict com 'nivel', 'texto', 'cor'
    """
    if k is None or not np.isfinite(k):
        return {'nivel': '—', 'texto': 'κ não disponível', 'cor': '#8b949e', 'valor': None}
    
    if k_media is None:
        # Sem baseline, interpretação absoluta
        if k > 3.0:
            return {
                'nivel': 'Alto',
                'texto': f'κ={k:.2f} — múltiplos sistemas instáveis. Recuperação imprevisível.',
                'cor': '#E74C3C',
                'valor': k,
            }
        elif k > 1.5:
            return {
                'nivel': 'Moderado',
                'texto': f'κ={k:.2f} — alguns sistemas variáveis. Ajusta treino conforme estado.',
                'cor': '#F39C12',
                'valor': k,
            }
        else:
            return {
                'nivel': 'Baixo',
                'texto': f'κ={k:.2f} — sistemas estáveis, previsíveis. Seguro treinar.',
                'cor': '#2ECC71',
                'valor': k,
            }
    else:
        # Com baseline, comparar percentis
        z = (k - k_media) / (k_desvio or 0.5)
        if z > 1.5:
            nivel = 'Alto'
            cor = '#E74C3C'
        elif z > 0.5:
            nivel = 'Moderado'
            cor = '#F39C12'
        else:
            nivel = 'Baixo'
            cor = '#2ECC71'
        
        return {
            'nivel': nivel,
            'texto': f'κ={k:.2f} ({z:+.2f}σ vs seu baseline {k_media:.2f}±{k_desvio:.2f}). {nivel}: instabilidade.',
            'cor': cor,
            'valor': k,
            'z_score': z,
        }


def kappa_fmt(series, janela=28, suavizar=7):
    """κ(t) = trace(cov(Δx)) numa janela movel, e λ₁ = peso do 1º eigenvalor.

    Cada serie e normalizada antes, para que modalidades com escalas
    diferentes contribuam de forma comparavel. κ alto = sistema instavel.
    """
    series = [s for s in series if s is not None and len(s)]
    if not series:
        return np.zeros(0), np.zeros(0)
    n, d = len(series[0]), len(series)
    mat = np.full((n, d), np.nan)
    for j, s in enumerate(series):
        mat[:, j] = _zscore(np.asarray(s, dtype=np.float64))

    delta = np.full_like(mat, np.nan)
    delta[1:] = mat[1:] - mat[:-1]

    kappa = np.full(n, np.nan)
    lam1 = np.full(n, np.nan)
    for t in range(janela, n):
        wd = delta[t - janela:t]
        val = np.all(np.isfinite(wd), axis=1)
        if val.sum() < max(10, d + 2):
            continue
        try:
            F = np.cov(wd[val].T)
            if F.ndim == 0:
                F = F.reshape(1, 1)
            kappa[t] = float(np.trace(F))
            if d >= 2:
                eig = np.sort(np.linalg.eigvalsh(F))[::-1]
                pos = eig[eig > 0]
                if len(pos):
                    lam1[t] = float(pos[0] / pos.sum())
        except Exception:
            pass
    return _ewm_nan(kappa, suavizar), _ewm_nan(lam1, suavizar)


def _ewm_nan(arr, span, min_periods=3, adjust=True):
    """Media exponencial ignorando NaN, igual ao pandas.ewm.

    adjust=True e o defeito do pandas (e o que o dashboard usa para suavizar
    o kappa); adjust=False e o usado no CTL/ATL. Em ambos o peso do passado
    decai com a distancia real desde a ultima observacao valida, que e o
    comportamento de ignore_na=False.
    """
    arr = np.asarray(arr, dtype=np.float64)
    alpha = 2.0 / (span + 1.0)
    um_menos = 1.0 - alpha
    out = np.full(len(arr), np.nan)
    num = den = 0.0
    prev = None
    vistos, desde = 0, 0

    for i, v in enumerate(arr):
        if np.isfinite(v):
            peso = um_menos ** (desde + 1)
            if adjust:
                num = v + peso * num
                den = 1.0 + peso * den
                prev = num / den
            else:
                prev = v if prev is None else peso * prev + (1 - peso) * v
            vistos += 1
            desde = 0
        else:
            desde += 1
        if prev is not None and vistos >= min_periods:
            out[i] = prev
    return out


def ewm(valores, span):
    alpha = 2.0 / (span + 1.0)
    out, prev = [], None
    for v in valores:
        prev = v if prev is None else alpha * v + (1 - alpha) * prev
        out.append(prev)
    return np.array(out)


# ── deteccao de fases ─────────────────────────────────────────────────────

def detect_phases(ctlg, hrv_rel, weed_z, dias_sem_treino,
                  janela_declive=14, janela_pct=60):
    """Classifica cada dia numa fase de treino.

    A ordem das regras importa: OVERREACH antes de FATIGUE, FATIGUE antes de
    BUILD. Os limiares sao percentis moveis de 60 dias, por isso adaptam-se
    ao atleta em vez de serem numeros fixos.
    """
    n = len(ctlg)
    dctl = declive_movel(ctlg, janela_declive)
    hrv_z = np.asarray(hrv_rel, dtype=np.float64)
    weed = _zscore(np.asarray(weed_z, dtype=np.float64)) if weed_z is not None \
        else np.full(n, np.nan)

    # todos os percentis das mesmas janelas numa so passagem
    pd_ = percentis_moveis(dctl, [0.30, 0.50, 0.70], janela_pct)
    ph_ = percentis_moveis(hrv_z, [0.10, 0.20, 0.30, 0.50, 0.60], janela_pct)
    pw_ = percentis_moveis(weed, [0.90], janela_pct)
    d70, d50, d30 = pd_[0.70], pd_[0.50], pd_[0.30]
    h60, h50 = ph_[0.60], ph_[0.50]
    h30, h20, h10 = ph_[0.30], ph_[0.20], ph_[0.10]
    w90 = pw_[0.90]

    fases = np.array(['TRANSITION'] * n, dtype=object)
    for t in range(n):
        dc, hv, wd = dctl[t], hrv_z[t], weed[t]
        if not np.isfinite(dc) or not (np.isfinite(d70[t]) and np.isfinite(d30[t])):
            continue

        hrv_ok = np.isfinite(hv) and np.isfinite(h20[t])
        weed_ok = np.isfinite(wd) and np.isfinite(w90[t])

        # Sem carga recente, o declive pode estar positivo so por inercia.
        if dias_sem_treino is not None and dias_sem_treino[t] > GAP_TREINO:
            if hrv_ok and np.isfinite(h50[t]) and hv > h50[t]:
                fases[t] = 'RECOVERY'
            continue

        if hrv_ok and weed_ok and hv < h10[t] and wd > w90[t] and dc > d50[t]:
            fases[t] = 'OVERREACH'
        elif hrv_ok and np.isfinite(d50[t]) and dc > d50[t] and hv < h20[t]:
            fases[t] = 'FATIGUE'
        elif hrv_ok and dc > d70[t] and hv > h30[t]:
            fases[t] = 'BUILD'
        elif (hrv_ok and np.isfinite(h60[t]) and np.isfinite(d30[t])
              and d30[t] <= dc <= d70[t] and hv > h60[t]):
            fases[t] = 'PEAK'
        elif hrv_ok and dc < d30[t] and hv > h50[t]:
            fases[t] = 'RECOVERY'

    suave = _moda_movel_3(fases)

    # ha quantos dias estamos nesta fase
    dias_na_fase = np.zeros(n, dtype=int)
    for t in range(1, n):
        dias_na_fase[t] = 0 if suave[t] != suave[t - 1] else dias_na_fase[t - 1] + 1

    return {'fase': suave, 'dctlg': dctl, 'hrv_z': hrv_z,
            'weed_z': weed, 'dias_na_fase': dias_na_fase}


def fase_global_ponderada(fases_por_mod, ctlg_por_mod):
    """Fase global = moda das fases modais, ponderada pelo CTLγ de cada uma.

    Uma modalidade com CTLγ alto pesa mais: se o Bike domina a carga, e o
    estado do Bike que define o estado global.
    """
    if not fases_por_mod:
        return None, {}
    total = sum(ctlg_por_mod.get(m, 0.0) for m in fases_por_mod)
    if total <= 0:
        return None, {}
    pesos = {}
    for mod, fase in fases_por_mod.items():
        pesos[fase] = pesos.get(fase, 0.0) + ctlg_por_mod.get(mod, 0.0)
    global_fase = max(pesos, key=pesos.get)
    contrib = {m: round(ctlg_por_mod.get(m, 0.0) / total, 3) for m in fases_por_mod}
    return global_fase, contrib
