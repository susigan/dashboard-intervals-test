# ══════════════════════════════════════════════════════════════════════════
# utils/cp_model.py — ATHELTICA (Flask / Railway)
#
# Modelos de Critical Power. Os ajustes sao os mesmos do cp_model.py do repo
# Streamlit -- M1, M2, M3, 2p e 3p hiperbolicos, Ward-Smith, OM3CP, OMExp,
# power-law, SEE%, Veloclinic e grid search do melhor subconjunto de MMP.
#
# DIFERENCA IMPORTANTE face ao original: la' os MMP vinham de colunas da
# Google Sheet no formato "Yes - 618w". Aqui vem da tabela power_curves,
# sincronizada da API da Intervals.icu. E' a mesma fonte que ja' alimenta o
# perfil metabolico, portanto o CP e o MLSS passam a assentar exactamente
# nos mesmos numeros -- que era o ponto de os calcular no mesmo sitio.
# ══════════════════════════════════════════════════════════════════════════

import numpy as np
from scipy.optimize import minimize, differential_evolution
from scipy.stats import linregress
from itertools import combinations

# Durações canónicas dos MMP (segundos) — usado no parsing
MMP_DURACOES = {'mmp1': 60, 'mmp3': 180, 'mmp5': 300,
                'mmp12': 720, 'mmp20': 1200, 'mmp60': 3600}

# Duração máxima considerada para o CP (segundos) — usada nos fits OM3CP/OMExp
TCP_MAX = 1800.0


def make_w(t_obs, mode):
    t = np.array(t_obs, dtype=float)
    if mode == "1/t":   return 1.0/t
    if mode == "1/t²":  return 1.0/t**2
    return np.ones_like(t)

def fit_m1(tests, w):
    """M1: P = W′·(1/t) + CP  — WLS no espaço P"""
    x = np.array([1/t for _,t in tests])
    y = np.array([p   for p,_ in tests])
    W = np.diag(w); X = np.column_stack([x, np.ones_like(x)])
    try:
        b = np.linalg.lstsq(W@X, W@y, rcond=None)[0]
        wp, cp = float(b[0]), float(b[1])
    except Exception:
        sl,ic,_,_,_ = linregress(x,y); wp,cp = float(sl),float(ic)
    pp = [wp/t+cp for _,t in tests]
    ss_res = float(np.sum(w*(y-np.array([wp/t+cp for _,t in tests]))**2))
    ss_tot = float(np.sum(w*(y-np.average(y,weights=w))**2))
    r2 = max(0.0,1-ss_res/ss_tot) if ss_tot>0 else 0.0
    return float(cp), float(wp), None, pp, r2, 2

def fit_m2(tests, w):
    """M2: W = CP·t + W′  — WLS no espaço W"""
    x = np.array([t   for _,t in tests])
    y = np.array([p*t for p,t in tests])
    W = np.diag(w); X = np.column_stack([x, np.ones_like(x)])
    try:
        b = np.linalg.lstsq(W@X, W@y, rcond=None)[0]
        cp, wp = float(b[0]), float(b[1])
    except Exception:
        sl,ic,_,_,_ = linregress(x,y); cp,wp = float(sl),float(ic)
    pp = [cp+wp/t for _,t in tests]
    ss_res = float(np.sum(w*(y-np.array([cp*t+wp for _,t in tests]))**2))
    ss_tot = float(np.sum(w*(y-np.average(y,weights=w))**2))
    r2 = max(0.0,1-ss_res/ss_tot) if ss_tot>0 else 0.0
    return float(cp), float(wp), None, pp, r2, 2

def fit_m3(tests, w):
    """M3: t = W′/(P-CP)  — minimiza erro em TEMPO"""
    p_obs = np.array([p for p,_ in tests])
    t_obs = np.array([t for _,t in tests])
    cp_max = float(min(p_obs))*0.99
    def _loss(params):
        cp,wp = params
        if wp<=0 or cp>=cp_max or cp<=0: return 1e12
        t_pred = wp/(p_obs-cp)
        return float(np.sum(w*(t_obs-t_pred)**2))
    best = None
    for cp0 in np.linspace(float(min(p_obs))*0.50, float(min(p_obs))*0.94, 8):
        wp0 = float(np.mean(t_obs))*float(min(p_obs)-cp0)*0.5
        if wp0<=0: continue
        try:
            r = minimize(_loss,[cp0,wp0],bounds=[(1,cp_max),(1,1e7)],method="L-BFGS-B")
            if best is None or r.fun < best.fun: best = r
        except Exception: pass
    if best is None or best.fun>1e10: return None,None,None,None,None,2
    cp,wp = float(best.x[0]),float(best.x[1])
    pp = [wp/t+cp for _,t in tests]
    ss_res = float(np.sum(w*(t_obs-wp/(p_obs-cp))**2))
    ss_tot = float(np.sum(w*(t_obs-np.average(t_obs,weights=w))**2))
    r2 = max(0.0,1-ss_res/ss_tot) if ss_tot>0 else 0.0
    return cp,wp,None,pp,r2,2

def fit_m4(tests, w):
    """M4: t = W′/(P-CP)·(1-(P-CP)/(Pmax-CP))  — 3 parâmetros"""
    p_obs = np.array([p for p,_ in tests])
    t_obs = np.array([t for _,t in tests])
    cp_max  = float(min(p_obs))*0.99
    pmax_lb = float(max(p_obs))*1.01
    def _t3(p,cp,wp,pmax):
        d = p-cp
        if np.any(d<=0) or np.any(p>=pmax): return np.full_like(p,1e9)
        return (wp/d)*(1-d/(pmax-cp))
    def _loss3(params):
        cp,wp,pmax = params
        if wp<=0 or cp<=0 or cp>=cp_max or pmax<=float(max(p_obs)): return 1e12
        t_pred = _t3(p_obs,cp,wp,pmax)
        if np.any(t_pred<=0): return 1e12
        return float(np.sum(w*(t_obs-t_pred)**2))
    best = None
    for cp0 in np.linspace(float(min(p_obs))*0.50,float(min(p_obs))*0.92,4):
        for pm0 in [float(max(p_obs))*f for f in [1.05,1.10,1.20]]:
            wp0 = float(np.mean(t_obs))*float(min(p_obs)-cp0)*0.4
            if wp0<=0: continue
            try:
                r = minimize(_loss3,[cp0,wp0,pm0],
                             bounds=[(1,cp_max),(1,1e7),(pmax_lb,pmax_lb*3)],
                             method="L-BFGS-B")
                if best is None or r.fun<best.fun: best=r
            except Exception: pass
    if best is None or best.fun>1e10: return None,None,None,None,None,3
    cp,wp,pmax = [float(x) for x in best.x]
    pp = [wp/t+cp for _,t in tests]
    ss_res = float(np.sum(w*(t_obs-_t3(p_obs,cp,wp,pmax))**2))
    ss_tot = float(np.sum(w*(t_obs-np.average(t_obs,weights=w))**2))
    r2 = max(0.0,1-ss_res/ss_tot) if ss_tot>0 else 0.0
    return cp,wp,pmax,pp,r2,3

def fit_ompd(tests, pmax_ext=None):
    """
    M5: OmPD — Omni-Domain Power-Duration (Puchowicz, Baker & Clarke 2020)

    Para t ≤ TCPmax (1800s):
        P(t) = W′/t × (1 - exp(-t×(Pmax-CP)/W′)) + CP

    Para t > TCPmax:
        P(t) = mesma equação - A × ln(t/TCPmax)

    Parâmetros: CP, W′, Pmax (fixo de p_max da sheet), A (se t>TCPmax disponível)

    Wʼeff(t) = W′ × (1 - exp(-t×(Pmax-CP)/W′))  → plateia ~110s → consistente com
    interpretação de capacidade anaeróbica fixa (diferença vs OmExp/Om3CP).

    Se pmax_ext=None → inferido como max(p_obs)*1.15 (estimativa conservadora).
    Se não há ponto t>TCPmax → A=0 (modelo reduz a 3 parâmetros para curtas durações).
    """
    from scipy.optimize import minimize as _minimize

    p_obs_arr = np.array([p for p, _ in tests])
    t_obs_arr = np.array([t for _, t in tests])

    # Pmax: usar valor externo (da sheet) se disponível, senão estimar
    if pmax_ext is not None and pmax_ext > float(max(p_obs_arr)):
        pmax = float(pmax_ext)
    else:
        pmax = float(max(p_obs_arr)) * 1.15

    # Separar testes curtos (≤TCPmax) e longos (>TCPmax)
    mask_long  = t_obs_arr > TCP_MAX
    has_long   = bool(np.any(mask_long))

    # Função OmPD P(t) com ou sem extensão longa
    def _ompd_p(t_arr, cp, wp, A=0.0):
        tau  = wp / max(pmax - cp, 1.0)
        base = wp / t_arr * (1 - np.exp(-t_arr / tau)) + cp
        if A > 0:
            decay = np.where(
                t_arr > TCP_MAX,
                A * np.log(t_arr / TCP_MAX),
                0.0
            )
            return base - decay
        return base

    # Loss: minimiza erro quadrático ponderado em potência
    # Peso 1/t → mais peso em esforços curtos (onde o modelo é mais sensível)
    def _loss(params):
        if has_long:
            cp, wp, A = params
            if A < 0: return 1e12
        else:
            cp, wp = params; A = 0.0
        if wp <= 0 or cp <= 0 or cp >= float(min(p_obs_arr)) * 0.99: return 1e12
        if cp >= pmax: return 1e12
        p_pred = _ompd_p(t_obs_arr, cp, wp, A)
        w_vec  = 1.0 / t_obs_arr  # peso 1/t
        return float(np.sum(w_vec * (p_obs_arr - p_pred) ** 2))

    best = None
    cp_max = float(min(p_obs_arr)) * 0.99
    # Grid de arranques
    for cp0 in np.linspace(float(min(p_obs_arr)) * 0.50,
                           float(min(p_obs_arr)) * 0.93, 6):
        wp0 = float(np.mean(t_obs_arr)) * (float(min(p_obs_arr)) - cp0) * 0.5
        if wp0 <= 0: continue
        try:
            if has_long:
                x0     = [cp0, wp0, 30.0]
                bounds = [(1, cp_max), (1, 1e7), (0, 500)]
            else:
                x0     = [cp0, wp0]
                bounds = [(1, cp_max), (1, 1e7)]
            r = _minimize(_loss, x0, bounds=bounds, method='L-BFGS-B')
            if best is None or r.fun < best.fun:
                best = r
        except Exception:
            pass

    if best is None or best.fun > 1e10:
        return None, None, None, None, None, None, None

    if has_long:
        cp, wp, A = float(best.x[0]), float(best.x[1]), float(best.x[2])
    else:
        cp, wp = float(best.x[0]), float(best.x[1]); A = 0.0

    p_pred_arr = _ompd_p(t_obs_arr, cp, wp, A)
    pp         = list(p_pred_arr)

    # R² em potência
    ss_res = float(np.sum((p_obs_arr - p_pred_arr) ** 2))
    ss_tot = float(np.sum((p_obs_arr - float(np.mean(p_obs_arr))) ** 2))
    r2     = max(0.0, 1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    # Wʼeff(120s) — verificar que atinge plateia (paper: ~110s)
    tau_fit   = wp / max(pmax - cp, 1.0)
    weff_120  = wp * (1 - np.exp(-120.0 / tau_fit))
    weff_pct  = weff_120 / wp * 100  # deve ser ≈ 99%

    return cp, wp, pmax, A, pp, r2, weff_pct

def calc_see(p_obs, pp, k=2):
    n = len(p_obs)
    if n<=k: return None,None
    sse  = float(np.sum((np.array(p_obs)-np.array(pp))**2))
    see  = float(np.sqrt(sse/max(n-k,1)))
    seep = see/float(np.mean(p_obs))*100
    return round(see,2),round(seep,2)

def veloclinic_points(tests, cp):
    """
    Veloclinic: scatter P vs W′_point = t*(P-CP).
    SEM curva teórica — seria W′_point = W′ (linha horizontal trivial).
    O diagnóstico está na distribuição dos pontos reais.
    """
    p_pts  = [p for p,_ in tests]
    wp_pts = [t*(p-cp) for p,t in tests]
    return p_pts, wp_pts

def vc_metrics(tests, cp, wp):
    wp_pts = [t*(p-cp) for p,t in tests if p>cp]
    if not wp_pts: return {"std":0,"cv":0,"mean":0,"slope":0}
    std_w  = float(np.std(wp_pts))
    mean_w = float(np.mean(wp_pts))
    cv_w   = std_w/mean_w*100 if mean_w>0 else 0.0
    p_pts  = [p for p,t in tests if p>cp]
    sl = 0.0
    # Proteger contra valores idênticos (linregress falha com std=0)
    if len(p_pts) >= 2 and len(set(p_pts)) > 1:
        try:
            sl,_,_,_,_ = linregress(p_pts, wp_pts)
        except Exception:
            sl = 0.0
    amp = (max(p_pts) - min(p_pts)) if len(p_pts) >= 2 else 0.0
    efeito = (abs(sl) * amp / mean_w * 100) if mean_w else 0.0
    return {"std":round(std_w,1),"cv":round(cv_w,1),
            "mean":round(mean_w,0),"slope":round(float(sl),4),
            "amplitude_p":round(amp,1),
            "efeito_declive_pct":round(efeito,1)}

def classify_fatigue(vm):
    """Classificacao a partir da dispersao do W\' ponto a ponto.

    O criterio original comparava o declive absoluto com 1, o que nao tem
    escala: um declive de -8 J/W sobre um intervalo de 64 W desloca o W\' em
    512 J, meio por cento de uma reserva de 13 kJ, e mesmo assim caia em
    "dados inconsistentes". Aqui o declive e' normalizado -- quanto do W\'
    medio e' explicado pela tendencia ao longo do intervalo de potencias
    observado -- para que a classificacao nao dependa das unidades nem do
    numero de pontos.
    """
    cv = vm.get("cv", 0)
    media = vm.get("mean", 0) or 0
    amplitude = vm.get("amplitude_p", 0) or 0
    efeito = (abs(vm.get("slope", 0)) * amplitude / media * 100) if media else 0

    if cv < 10 and efeito < 15:
        return "✅ Bom fit — W′ consistente"
    if cv > 30:
        return "🔵 Fadiga central (variabilidade)"
    if media and vm.get("std", 0) and media < vm["std"] * 2:
        return "🔴 Fadiga periférica (W′ reduzido)"
    if efeito >= 30:
        return "🟠 W′ depende da potência — modelo hiperbólico não serve aqui"
    if cv > 15:
        return "🟠 Fadiga sistémica"
    return "🟡 Fit aceitável com reservas"


def fit_2p_hyperbolic(tests):
    """2P Hiperbólico: P = W′/t + CP  (trabalho-tempo linear)
    Janela recomendada: 2min – 60min. Mínimo: 2 pontos."""
    from scipy.stats import linregress
    if len(tests) < 2: return None, None, None, None
    x = np.array([1.0/t for _, t in tests])
    y = np.array([p for p, _ in tests])
    slope, intercept, r, _, _ = linregress(x, y)
    cp = float(intercept); wp = float(slope)
    if cp <= 0 or wp <= 0: return None, None, None, None
    pp = [wp/t + cp for _, t in tests]
    return cp, wp, None, pp

def fit_3p_hyperbolic(tests, pmax_ext=None):
    """3P Hiperbólico: P(t) = (Pmax·W′) / (W′ + (Pmax-CP)·t)
    Se pmax_ext disponível → Pmax FIXO (apenas 2 parâmetros livres: CP, W′).
    Sem pmax_ext → Pmax como 3º parâmetro livre (precisa ponto curto <30s)."""
    from scipy.optimize import minimize as _min
    if len(tests) < 2: return None, None, None, None
    p_obs = np.array([p for p, _ in tests])
    t_obs = np.array([t for _, t in tests])

    # Usar Pmax externo fixo se disponível → reduz a 2 parâmetros, muito mais estável
    if pmax_ext and float(pmax_ext) > float(max(p_obs)):
        pmax_fixed = float(pmax_ext)
        def _p3f(t, cp, wp):
            return (pmax_fixed * wp) / (wp + (pmax_fixed - cp) * t)
        def _loss2(params):
            cp, wp = params
            if cp <= 0 or wp <= 0 or cp >= min(p_obs)*0.99 or cp >= pmax_fixed: return 1e12
            pred = _p3f(t_obs, cp, wp)
            return float(np.sum((p_obs - pred)**2))
        best = None
        for cp0 in np.linspace(float(min(p_obs))*0.50, float(min(p_obs))*0.93, 8):
            wp0 = float(np.mean(t_obs))*(float(min(p_obs))-cp0)*0.5
            if wp0 <= 0: continue
            try:
                r = _min(_loss2, [cp0, max(wp0,1)],
                         bounds=[(1, float(min(p_obs))*0.98), (1, 1e7)],
                         method='L-BFGS-B')
                if best is None or r.fun < best.fun: best = r
            except Exception: pass
        if best is None or best.fun > 1e10: return None, None, None, None
        cp, wp = float(best.x[0]), float(best.x[1])
        pp = [float(_p3f(np.array([t]), cp, wp)[0]) for _, t in tests]
        return cp, wp, pmax_fixed, pp

    # Sem Pmax externo → optimizar os 3 parâmetros (precisa ponto curto para Pmax)
    def _p3(t, cp, wp, pmax):
        return (pmax * wp) / (wp + (pmax - cp) * t)
    def _loss3(params):
        cp, wp, pmax = params
        if cp<=0 or wp<=0 or pmax<=max(p_obs) or cp>=min(p_obs)*0.99: return 1e12
        pred = _p3(t_obs, cp, wp, pmax)
        return float(np.sum((p_obs - pred)**2))
    best = None
    for cp0 in np.linspace(float(min(p_obs))*0.5, float(min(p_obs))*0.92, 5):
        for pm0 in [float(max(p_obs))*f for f in [1.05,1.10,1.20,1.50,2.0]]:
            wp0 = float(np.mean(t_obs))*(float(min(p_obs))-cp0)*0.5
            if wp0 <= 0: continue
            try:
                r = _min(_loss3, [cp0, max(wp0,1), pm0],
                         bounds=[(1, float(min(p_obs))*0.98), (1, 1e7),
                                 (float(max(p_obs))*1.01, float(max(p_obs))*3)],
                         method='L-BFGS-B')
                if best is None or r.fun < best.fun: best = r
            except Exception: pass
    if best is None or best.fun > 1e10: return None, None, None, None
    cp, wp, pmax = float(best.x[0]), float(best.x[1]), float(best.x[2])
    pp = [float(_p3(np.array([t]), cp, wp, pmax)[0]) for _, t in tests]
    return cp, wp, pmax, pp

def fit_ward_smith(tests, pmax_ext=None):
    """Ward-Smith (1999): extensão 3P com decaimento fisiológico.
    P(t) = CP + (Pmax-CP)·exp(-t·(Pmax-CP)/W′)
    Requer Pmax externo; sem ele usa estimativa conservadora."""
    from scipy.optimize import minimize as _min
    if len(tests) < 3: return None, None, None, None
    p_obs = np.array([p for p, _ in tests])
    t_obs = np.array([t for _, t in tests])
    pmax  = float(pmax_ext) if pmax_ext and pmax_ext > max(p_obs) else float(max(p_obs)) * 1.2

    def _pws(t, cp, wp):
        return cp + (pmax - cp) * np.exp(-t * (pmax - cp) / max(wp, 1.0))

    def _loss(params):
        cp, wp = params
        if cp <= 0 or wp <= 0 or cp >= min(p_obs)*0.99: return 1e12
        return float(np.sum((p_obs - _pws(t_obs, cp, wp))**2))

    best = None
    for cp0 in np.linspace(float(min(p_obs))*0.5, float(min(p_obs))*0.92, 6):
        wp0 = float(np.mean(t_obs)) * (float(min(p_obs)) - cp0) * 0.5
        try:
            r = _min(_loss, [cp0, max(wp0, 1)],
                     bounds=[(1, float(min(p_obs))*0.98), (1, 1e7)],
                     method='L-BFGS-B')
            if best is None or r.fun < best.fun: best = r
        except Exception: pass
    if best is None or best.fun > 1e10: return None, None, None, None
    cp, wp = float(best.x[0]), float(best.x[1])
    pp = [float(_pws(np.array([t]), cp, wp)[0]) for _, t in tests]
    return cp, wp, pmax, pp

def fit_om3cp(tests, pmax_ext=None):
    """Om3CP (Omni-3CP): OmPD com 3P base em vez de 2P.
    P(t) = W′/t × f(t,Pmax,CP) + CP, âncora em τ de 3P Pmax."""
    from scipy.optimize import minimize as _min
    if len(tests) < 2: return None, None, None, None
    p_obs = np.array([p for p, _ in tests])
    t_obs = np.array([t for _, t in tests])
    pmax  = float(pmax_ext) if pmax_ext and pmax_ext > max(p_obs) else float(max(p_obs)) * 1.15

    def _pom3(t, cp, wp, A_om=0.0):
        tau  = wp / max(pmax - cp, 1.0)
        base = wp / t * (1 - np.exp(-t / tau)) + cp
        if A_om > 0:
            decay = np.where(t > TCP_MAX, A_om * np.log(t / TCP_MAX), 0.0)
            return base - decay
        return base

    mask_long = t_obs > TCP_MAX
    has_long  = bool(np.any(mask_long))

    def _loss(params):
        cp, wp = params[0], params[1]
        A_om   = params[2] if has_long else 0.0
        if cp <= 0 or wp <= 0 or cp >= min(p_obs)*0.99 or cp >= pmax: return 1e12
        pred = _pom3(t_obs, cp, wp, A_om)
        return float(np.sum((1.0/t_obs) * (p_obs - pred)**2))

    best = None
    for cp0 in np.linspace(float(min(p_obs))*0.50, float(min(p_obs))*0.93, 6):
        wp0 = float(np.mean(t_obs)) * (float(min(p_obs)) - cp0) * 0.5
        if wp0 <= 0: continue
        try:
            x0 = [cp0, wp0, 30.0] if has_long else [cp0, wp0]
            bd = [(1, float(min(p_obs))*0.98), (1, 1e7)]
            if has_long: bd.append((0, 500))
            r = _min(_loss, x0, bounds=bd, method='L-BFGS-B')
            if best is None or r.fun < best.fun: best = r
        except Exception: pass
    if best is None or best.fun > 1e10: return None, None, None, None
    cp, wp = float(best.x[0]), float(best.x[1])
    A_om   = float(best.x[2]) if has_long else 0.0
    pp = [float(_pom3(np.array([t]), cp, wp, A_om)[0]) for _, t in tests]
    return cp, wp, pmax, pp

def fit_omexp(tests, pmax_ext=None):
    """OmExp: variante OmPD com decaimento exponencial para t > TCPmax.
    P(t) = OmPD_base(t) para t≤TCPmax
    P(t) = OmPD_base(t) × exp(-A_e × (t-TCPmax)/TCPmax) para t>TCPmax"""
    from scipy.optimize import minimize as _min
    if len(tests) < 2: return None, None, None, None
    p_obs = np.array([p for p, _ in tests])
    t_obs = np.array([t for _, t in tests])
    pmax  = float(pmax_ext) if pmax_ext and pmax_ext > max(p_obs) else float(max(p_obs)) * 1.15

    def _pomexp(t, cp, wp, A_e=0.0):
        tau  = wp / max(pmax - cp, 1.0)
        base = wp / t * (1 - np.exp(-t / tau)) + cp
        if A_e > 0:
            decay = np.where(t > TCP_MAX,
                             (1 - np.exp(-A_e * (t - TCP_MAX) / TCP_MAX)),
                             0.0)
            return base * (1 - decay * 0.15)
        return base

    mask_long = t_obs > TCP_MAX
    has_long  = bool(np.any(mask_long))

    def _loss(params):
        cp, wp = params[0], params[1]
        A_e = params[2] if has_long else 0.0
        if cp <= 0 or wp <= 0 or cp >= min(p_obs)*0.99 or cp >= pmax: return 1e12
        pred = _pomexp(t_obs, cp, wp, A_e)
        return float(np.sum((1.0/t_obs) * (p_obs - pred)**2))

    best = None
    for cp0 in np.linspace(float(min(p_obs))*0.50, float(min(p_obs))*0.93, 6):
        wp0 = float(np.mean(t_obs)) * (float(min(p_obs)) - cp0) * 0.5
        if wp0 <= 0: continue
        try:
            x0 = [cp0, wp0, 1.0] if has_long else [cp0, wp0]
            bd = [(1, float(min(p_obs))*0.98), (1, 1e7)]
            if has_long: bd.append((0, 10))
            r = _min(_loss, x0, bounds=bd, method='L-BFGS-B')
            if best is None or r.fun < best.fun: best = r
        except Exception: pass
    if best is None or best.fun > 1e10: return None, None, None, None
    cp, wp = float(best.x[0]), float(best.x[1])
    A_e = float(best.x[2]) if has_long else 0.0
    pp = [float(_pomexp(np.array([t]), cp, wp, A_e)[0]) for _, t in tests]
    return cp, wp, pmax, pp

def fit_power_law(tests):
    """Power Law: P = a × t^(-b). Sem CP explícito.
    log(P) = log(a) - b×log(t) — regressão linear no espaço log-log."""
    from scipy.stats import linregress
    if len(tests) < 2: return None, None, None, None
    x = np.log([t for _, t in tests])
    y = np.log([p for p, _ in tests])
    slope, intercept, r, _, _ = linregress(x, y)
    b = -float(slope); a = float(np.exp(intercept))
    if a <= 0 or b <= 0: return None, None, None, None
    pp = [a * t**(-b) for _, t in tests]
    # CP implícito ~ P(3600s)
    cp_impl = a * 3600.0**(-b)
    return cp_impl, a, b, pp  # (cp_proxy, a, b, pp)

def _extrair_pp(res, n):
    """Potencias previstas de dentro do tuplo devolvido por um fit.

    Os fits nao devolvem todos o mesmo formato: M1/M2/M3 devolvem
    (cp, wp, None, pp, r2, k) -- seis elementos, com o pp na posicao 3 e um
    inteiro no fim -- enquanto os de 3 parametros terminam no pp. O grid
    search original lia sempre res[-1], o que para o M1, M2 e M3 lhe dava
    o inteiro 2 em vez das previsoes e produzia um SEE% constante e sem
    sentido (131.70 em todos eles, independentemente dos dados). Procura-se
    a ultima sequencia com o comprimento certo, em vez de assumir posicao.
    """
    for item in reversed(res):
        if isinstance(item, (list, tuple, np.ndarray)) and len(item) == n:
            try:
                return [float(v) for v in item]
            except (TypeError, ValueError):
                continue
    return None


def _grid_search_model(fit_fn, all_mmp_pts, min_pts, pmax_ext=None, k_params=2):
    """
    Testa todas as combinações de N pontos (N >= min_pts) dos MMPs disponíveis.
    Retorna a combinação com menor SEE%.
    fit_fn(tests, pmax_ext=None) → (cp, wp, pmax_or_extra, pp)
    """
    from itertools import combinations
    if len(all_mmp_pts) < min_pts:
        return None
    best = {'see_pct': 999, 'result': None, 'combo': None}
    for combo in combinations(range(len(all_mmp_pts)), min_pts):
        pts = [all_mmp_pts[i] for i in combo]
        try:
            if pmax_ext is not None:
                res = fit_fn(pts, pmax_ext=pmax_ext)
            else:
                res = fit_fn(pts)
            if res is None or res[0] is None: continue
            pp = _extrair_pp(res, len(pts))
            if pp is None: continue
            cp = res[0]
            p_obs  = [p for p, _ in pts]
            _, see_pct = calc_see(p_obs, pp, k=k_params)
            if see_pct is not None and see_pct < best['see_pct']:
                best = {'see_pct': see_pct, 'result': res, 'combo': pts,
                        'n_pts': len(pts), 'cp': cp}
        except Exception:
            pass
    # Também testar com todos os pontos
    try:
        if pmax_ext is not None:
            res = fit_fn(all_mmp_pts, pmax_ext=pmax_ext)
        else:
            res = fit_fn(all_mmp_pts)
        pp_all = _extrair_pp(res, len(all_mmp_pts)) if res else None
        if res and res[0] is not None and pp_all is not None:
            p_obs = [p for p, _ in all_mmp_pts]
            _, see_pct = calc_see(p_obs, pp_all, k=k_params)
            if see_pct is not None and see_pct < best['see_pct']:
                best = {'see_pct': see_pct, 'result': res, 'combo': all_mmp_pts,
                        'n_pts': len(all_mmp_pts), 'cp': res[0]}
    except Exception:
        pass
    return best if best['result'] is not None else None


# ══════════════════════════════════════════════════════════════════════════
# PONTOS MMP — a partir das power_curves da Intervals.icu
# ══════════════════════════════════════════════════════════════════════════

MMP_COLS = {'MMP1': 60, 'MMP3': 180, 'MMP5': 300,
            'MMP12': 720, 'MMP20': 1200, 'MMP60': 3600}


def marcar_duplicados(pontos, tolerancia=0.005):
    """Duracoes diferentes com praticamente a mesma potencia.

    Acontece quando um esforco longo e constante e' o melhor de varias
    duracoes ao mesmo tempo: 20 minutos a 242 W fazem com que o melhor de
    12 minutos dentro deles seja tambem 242 W. O ponto mais curto nao traz
    informacao nova -- pior, entra no ajuste como se fosse uma observacao
    independente e obriga a curva a ser plana onde devia decair, o que
    empurra o CP para cima.

    Devolve (pontos_limpos, descartados). Fica o mais longo de cada par,
    que e' o que mais informa sobre o CP.
    """
    ordenados = sorted(pontos, key=lambda x: x[1])
    manter, fora = [], []
    for i, (w, t) in enumerate(ordenados):
        dup = False
        for w2, t2 in ordenados[i + 1:]:
            if w2 > 0 and abs(w - w2) / max(w, w2) <= tolerancia:
                dup = True
                fora.append({'t': int(t), 'w': round(w, 1),
                             'igual_a_t': int(t2)})
                break
        if not dup:
            manter.append((w, t))
    return manter, fora


def pontos_de_curvas(registos, modalidade, season_activa=None, limiar_max=None,
                     excluir_duplicados=True):
    """MMP1..MMP60 a partir das power_curves, com a mesma regra da season.

    Reutiliza o melhores_mmp do perfil metabolico -- melhor da season activa,
    com recuo para a anterior quando nao houve esforco maximo, e reparacao
    da monotonia. Assim o CP e o MLSS assentam nos mesmos MMP e deixa de ser
    possivel o CP dizer uma coisa e o modelo de Mader outra por causa da
    fonte dos numeros.

    Regras de duracao por modalidade, iguais as do repo Streamlit:
      Row / Ski   classicos  60, 300, 720      nao-classicos 180, 300, 720, 1200
      Bike / Run  todas as duracoes em ambos
    O MMP60 nunca entra no fit -- fica de fora para validacao.
    """
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import perfil_metabolico as pmet

    duracoes = sorted(MMP_COLS.values())
    guardado = pmet.DURACOES_MMP.get(modalidade)
    try:
        pmet.DURACOES_MMP[modalidade] = duracoes
        extraido = pmet.melhores_mmp(registos, modalidade,
                                     season_activa=season_activa,
                                     limiar_max=limiar_max)
    finally:
        if guardado is not None:
            pmet.DURACOES_MMP[modalidade] = guardado

    mmp = extraido['mmp']
    classicos, completos = [], []
    for dur in duracoes:
        v = mmp.get(dur)
        if v is None or dur == 3600:
            continue
        d = float(dur)
        if modalidade in ('Row', 'Ski'):
            if d in (60.0, 300.0, 720.0):
                classicos.append((float(v), d))
            if d in (180.0, 300.0, 720.0, 1200.0):
                completos.append((float(v), d))
        else:
            classicos.append((float(v), d))
            completos.append((float(v), d))

    classicos = sorted(set(classicos), key=lambda x: x[1])
    completos = sorted(set(completos), key=lambda x: x[1])
    dup_cl, dup_full = [], []
    if excluir_duplicados:
        classicos, dup_cl = marcar_duplicados(classicos)
        completos, dup_full = marcar_duplicados(completos)

    return {
        'all_mmp_pts': classicos,
        'all_mmp_pts_full': completos,
        'duplicados_excluidos': dup_cl or dup_full,
        'mmp60_val': mmp.get(3600),
        'pmax': extraido.get('pmax_w'),
        'datas': {str(k): v for k, v in extraido['datas'].items()},
        'seasons': {str(k): v for k, v in extraido['seasons'].items()},
        'recuou': {str(k): bool(v) for k, v in extraido['recuou'].items()},
        'ajustado_por_coerencia': {
            str(k): bool(v) for k, v in extraido['ajustado_por_coerencia'].items()},
        'seasons_disponiveis': extraido['seasons_disponiveis'],
    }


# ══════════════════════════════════════════════════════════════════════════
# ORQUESTRADORA
# ══════════════════════════════════════════════════════════════════════════

# Janela de duracoes em que o modelo hiperbolico e' valido. Abaixo de ~2 min
# domina a componente neuromuscular e a reserva finita unica deixa de
# descrever o esforco; acima de ~20 min entram a deriva e o substrato. Nao e'
# uma convencao de tabela sem base: neste atleta, incluir o ponto de 60 s
# faz o W' calculado ponto a ponto variar 14.8%, e exclui-lo baixa para 3.3%.
JANELA_CP = (120.0, 1200.0)

# W' plausivel em Joules. Fora disto o ajuste convergiu para uma solucao
# matematicamente valida e fisiologicamente impossivel -- acontece sempre
# que ha parametros a mais para os pontos disponiveis.
WPRIME_PLAUSIVEL = (5000.0, 30000.0)


def validar(res, dados, janela=JANELA_CP):
    """Verificacoes que nao dependem do ajuste, so' de fisiologia.

    O SEE% diz se a curva passa perto dos pontos. Nao diz nada sobre se os
    pontos sao esforcos maximos, nem se o CP resultante e' possivel. Estas
    verificacoes dizem.
    """
    avisos = []
    melhor = res.get('melhor') or {}
    cp = melhor.get('cp')
    mmp60 = dados.get('mmp60_val')
    pts = dados.get('all_mmp_pts_full') or dados.get('all_mmp_pts') or []

    if cp and mmp60 and cp > mmp60:
        avisos.append({
            'gravidade': 'alto', 'chave': 'cp_acima_do_mmp60',
            'texto': (f'CP de {round(cp)} W acima do melhor de 60 min '
                      f'({round(mmp60)} W). Por definicao o CP e sustentavel '
                      'mais de uma hora, portanto ou o esforco de 60 min nao '
                      'foi maximo -- o caso habitual, e uma saida longa em '
                      'endurance nao e um teste -- ou os MMP curtos estao a '
                      'puxar o CP para cima. Enquanto o MMP60 for so o melhor '
                      'de uma saida qualquer, esta verificacao nao invalida o '
                      'CP; deixa de a poder usar como validacao.')})
    elif cp and mmp60:
        avisos.append({
            'gravidade': 'ok', 'chave': 'cp_abaixo_do_mmp60',
            'texto': (f'CP de {round(cp)} W abaixo do melhor de 60 min '
                      f'({round(mmp60)} W), como tem de ser.')})

    # graus de liberdade
    if melhor.get('n_pts') and melhor.get('k_params'):
        df = melhor['n_pts'] - melhor['k_params']
        if df <= 1:
            avisos.append({
                'gravidade': 'alto', 'chave': 'sem_graus_de_liberdade',
                'texto': (f"O modelo escolhido usa {melhor['n_pts']} pontos "
                          f"para {melhor['k_params']} parametros: {df} grau de "
                          'liberdade. Com tao poucos, a curva passa quase '
                          'exactamente pelos pontos e o SEE% baixo nao mede '
                          'qualidade nenhuma -- mede so que ha parametros a '
                          'mais. Nao usar o SEE% para escolher entre modelos '
                          'nestas condicoes.')})

    # W' plausivel
    wp = melhor.get('wp')
    if wp and not (WPRIME_PLAUSIVEL[0] <= wp <= WPRIME_PLAUSIVEL[1]):
        avisos.append({
            'gravidade': 'alto', 'chave': 'wprime_implausivel',
            'texto': (f"W' de {round(wp/1000, 1)} kJ fora do intervalo "
                      f'plausivel ({WPRIME_PLAUSIVEL[0]/1000:.0f}-'
                      f'{WPRIME_PLAUSIVEL[1]/1000:.0f} kJ). O ajuste '
                      'convergiu para uma solucao matematicamente valida e '
                      'fisiologicamente impossivel. Rejeitar este modelo.')})

    # pontos fora da janela de validade
    fora = [p for p in pts if not (janela[0] <= p[1] <= janela[1])]
    if fora:
        det = ', '.join(f'{int(t)}s' for _w, t in sorted(fora, key=lambda x: x[1]))
        avisos.append({
            'gravidade': 'medio', 'chave': 'fora_da_janela',
            'texto': (f'Pontos fora da janela de {int(janela[0])}-'
                      f'{int(janela[1])} s onde o modelo hiperbolico e valido '
                      f'({det}). Abaixo de 2 min domina a componente '
                      'neuromuscular e uma reserva finita unica deixa de '
                      'descrever o esforco.')})

    dups = dados.get('duplicados_excluidos') or []
    if dups:
        det = ', '.join(f"{d['t']}s = {d['igual_a_t']}s ({d['w']} W)" for d in dups)
        avisos.append({
            'gravidade': 'medio', 'chave': 'duracoes_duplicadas',
            'texto': (f'Duracoes com potencia praticamente igual ({det}): '
                      'vem do mesmo esforco longo e constante, onde o melhor '
                      'de uma duracao curta e simplesmente um pedaco da longa. '
                      'Foram excluidas do ajuste por nao serem observacoes '
                      'independentes.')})

    # Veloclinic: comparar dentro e fora da janela diz se o desalinhamento
    # e' do modelo ou so' dos pontos curtos
    v = res.get('veloclinic') or {}
    m = v.get('metricas') or {}
    vj = res.get('veloclinic_janela') or {}
    mj = vj.get('metricas') or {}
    if m.get('cv') is not None and mj.get('cv') is not None and fora:
        if mj['cv'] < m['cv'] * 0.6:
            avisos.append({
                'gravidade': 'ok', 'chave': 'wprime_consistente_na_janela',
                'texto': (f"W' varia {m['cv']}% em todos os pontos mas so "
                          f"{mj['cv']}% dentro da janela de "
                          f'{int(janela[0])}-{int(janela[1])} s. O modelo '
                          'descreve bem o atleta no intervalo onde e valido; '
                          'a inconsistencia vinha dos pontos curtos, que nao '
                          'deviam pesar no ajuste.')})
    if (mj.get('cv') if mj else m.get('cv', 0)) > 15:
        avisos.append({
            'gravidade': 'medio', 'chave': 'wprime_inconsistente',
            'texto': (f"W' calculado ponto a ponto varia {m['cv']}% "
                      f"(declive {m.get('slope')} contra a potencia). Se o "
                      'modelo hiperbolico descrevesse este atleta, o W\' seria '
                      'constante em todos os pontos. Um declive marcado '
                      'significa que uma reserva finita unica nao chega para '
                      'descrever o intervalo de duracoes usado.')})

    if dados.get('pmax') and cp and res.get('usou_pmax'):
        avisos.append({
            'gravidade': 'baixo', 'chave': 'pmax_ancora',
            'texto': (f"Pmax de {round(dados['pmax'])} W (pico de 1 s) e usado "
                      'como ancora nos modelos de tres parametros. E um valor '
                      'neuromuscular, de um dominio diferente do que o CP '
                      'descreve; forcar a curva a passar por la distorce o '
                      'resto. Comparar com ?usar_pmax=0 antes de escolher um '
                      'modelo de tres parametros.')})
    return avisos


def calcular_cp_completo(dados, modalidade, min_pts=3, usar_pmax=True):
    """Corre todos os modelos e ordena-os por SEE%.

    'dados' e' o dict devolvido por pontos_de_curvas.

    O SEE% e' o erro padrao do ajuste em percentagem da potencia media. Nao
    diz que o CP esta certo -- diz que a curva passa perto dos pontos. Um
    modelo de 3 parametros ajusta quase sempre melhor do que um de 2 por
    ter mais liberdade, e por isso o k entra no denominador do SEE. Mesmo
    assim, comparar SEE% entre modelos com numeros de parametros diferentes
    e' comparacao enviesada: por isso se devolve tudo e a escolha e' do
    utilizador, em vez de se impor o menor SEE%.
    """
    pts = dados.get('all_mmp_pts') or []
    pts_full = dados.get('all_mmp_pts_full') or []
    pmax = dados.get('pmax')

    if len(pts) < min_pts:
        return {'ok': False, 'modalidade': modalidade, 'n_mmp': len(pts),
                'motivo': f'MMP insuficiente ({len(pts)} < {min_pts})',
                'mmp_pts': pts, 'pmax': pmax,
                'mmp60_val': dados.get('mmp60_val'),
                'modelos': {}, 'melhor': None}

    definicoes = [
        ('M1 (WLS-P)',     lambda t, **k: fit_m1(t, make_w([x for _, x in t], 'log')), pts,      2),
        ('M2 (WLS-1/t)',   lambda t, **k: fit_m2(t, make_w([x for _, x in t], 'log')), pts,      2),
        ('M3 (NL-2p)',     lambda t, **k: fit_m3(t, make_w([x for _, x in t], 'log')), pts,      2),
        ('2p hiperbolico', lambda t, **k: fit_2p_hyperbolic(t),                        pts,      2),
        ('3p hiperbolico', lambda t, pmax_ext=None: fit_3p_hyperbolic(t, pmax_ext),    pts_full, 3),
        ('Ward-Smith',     lambda t, pmax_ext=None: fit_ward_smith(t, pmax_ext),       pts_full, 3),
        ('OM3CP',          lambda t, pmax_ext=None: fit_om3cp(t, pmax_ext),            pts_full, 3),
        ('OMExp',          lambda t, pmax_ext=None: fit_omexp(t, pmax_ext),            pts_full, 3),
    ]

    modelos = {}
    for nome, fit_fn, base, kp in definicoes:
        if len(base) < min_pts:
            continue
        try:
            best = _grid_search_model(
                fit_fn, base, min_pts=min(min_pts, len(base)),
                pmax_ext=(pmax if (kp == 3 and usar_pmax) else None),
                k_params=kp)
            if best and best.get('result'):
                res = best['result']
                cp = float(res[0]) if res[0] is not None else None
                wp = float(res[1]) if len(res) > 1 and res[1] is not None else None
                if cp is None or not (0 < cp < 2000):
                    continue
                modelos[nome] = {
                    'cp': round(cp, 1),
                    'wp': round(wp) if wp else None,
                    'wp_kj': round(wp / 1000.0, 2) if wp else None,
                    'see_pct': best['see_pct'],
                    'n_pts': best['n_pts'],
                    'k_params': kp,
                    'pontos_usados': [{'w': round(p, 1), 't': int(t)}
                                      for p, t in (best.get('combo') or [])],
                }
        except Exception:
            pass

    melhor = None
    if modelos:
        nome = min(modelos, key=lambda n: modelos[n]['see_pct'])
        melhor = {'nome': nome, **modelos[nome]}

    # diagnostico Veloclinic com o melhor CP, em todos os pontos e so' na
    # janela de validade -- a diferenca entre os dois e' o diagnostico util
    veloclinic = veloclinic_janela = None
    base_v = pts_full or pts
    if melhor and melhor['cp']:
        def _velo(conjunto):
            if len(conjunto) < 2:
                return None
            vm = vc_metrics(conjunto, melhor['cp'], melhor.get('wp') or 0)
            p_pts, wp_pts = veloclinic_points(conjunto, melhor['cp'])
            return {'metricas': vm, 'classificacao': classify_fatigue(vm),
                    'n': len(conjunto),
                    'pontos': [{'p': round(a, 1), 'wp': round(b)}
                               for a, b in zip(p_pts, wp_pts)]}
        veloclinic = _velo(base_v)
        na_janela = [p for p in base_v
                     if JANELA_CP[0] <= p[1] <= JANELA_CP[1]]
        if len(na_janela) < len(base_v):
            veloclinic_janela = _velo(na_janela)

    saida = {'ok': len(modelos) > 0, 'modalidade': modalidade,
            'n_mmp': len(pts), 'min_pts': min_pts, 'usou_pmax': usar_pmax,
            'duplicados_excluidos': dados.get('duplicados_excluidos') or [],
            'mmp_pts': [{'w': round(p, 1), 't': int(t)} for p, t in pts],
            'mmp_pts_full': [{'w': round(p, 1), 't': int(t)} for p, t in pts_full],
            'pmax': pmax, 'mmp60_val': dados.get('mmp60_val'),
            'modelos': modelos, 'melhor': melhor, 'veloclinic': veloclinic,
            'veloclinic_janela': veloclinic_janela,
            'janela_cp': list(JANELA_CP)}
    saida['validacao'] = validar(saida, dados)
    return saida


def curva_do_modelo(cp, wp, t_min=30, t_max=3600, n=120):
    """P(t) = CP + W'/t, para desenhar a hiperbole."""
    if not cp or not wp:
        return []
    passo = (np.log(t_max) - np.log(t_min)) / (n - 1)
    out = []
    for i in range(n):
        t = float(np.exp(np.log(t_min) + passo * i))
        out.append({'t': round(t, 1), 'p': round(cp + wp / t, 1)})
    return out


def tempo_ate_exaustao(cp, wp, potencia):
    """t = W' / (P - CP). So' faz sentido acima do CP."""
    if not cp or not wp or not potencia or potencia <= cp:
        return None
    return round(wp / (potencia - cp), 1)


# ══════════════════════════════════════════════════════════════════════════
# CALCULADORA CONCEPT2 — so' Row e Ski
# ══════════════════════════════════════════════════════════════════════════

# Percentagens do 2 km usadas como referencia de perfil. Vem da tabela de
# equivalencias do ergometro, nao dos dados deste atleta -- por isso e' um
# alvo generico, util para ver a forma do perfil (velocista vs diesel) e
# nao para prescrever.
PCT_C2 = {"Power Peak": 173, "60seg": 153, "2km": 100, "6km": 85, "60min": 76}

MODALIDADES_C2 = ('Row', 'Ski')


def split_de_watts(w):
    """Watts -> segundos por 500 m (formula do Concept2: P = 2.8 / pace^3)."""
    if not w or w <= 0:
        return None
    return ((2.8 / float(w)) ** (1.0 / 3.0)) * 500.0


def formatar_split(seg):
    if seg is None:
        return None
    m = int(seg // 60)
    return f"{m}:{seg - m * 60:05.2f}"


def watts_de_split(texto):
    """'MM:SS.ss' -> Watts."""
    try:
        partes = str(texto).strip().replace(',', '.').split(':')
        if len(partes) != 2:
            return None
        seg = float(partes[0]) * 60 + float(partes[1])
        if seg <= 0:
            return None
        return round(2.8 / ((seg / 500.0) ** 3), 1)
    except Exception:
        return None


def tabela_c2(watts_2k, medidos=None):
    """Tabela de alvos a partir do 2 km, com splits e % actual."""
    medidos = medidos or {}
    if not watts_2k or watts_2k <= 0:
        return []
    linhas = []
    for teste, pct in PCT_C2.items():
        real = medidos.get(teste)
        real = float(real) if real else None
        objectivo = watts_2k * pct / 100.0
        linhas.append({
            'teste': teste,
            'watts_real': round(real, 1) if real else None,
            'split_real': formatar_split(split_de_watts(real)) if real else None,
            'pct_actual': round(real / watts_2k * 100, 1) if real else None,
            'pct_ideal': pct,
            'watts_objectivo': round(objectivo, 1),
            'split_objectivo': formatar_split(split_de_watts(objectivo)),
            'delta_w': round(real - objectivo, 1) if real else None,
        })
    return linhas
