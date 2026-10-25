"""Vista principal do IR: influencia GLOBAL (Overall) e por modalidade, a 300 s. Funcoes PURAS.

Nao le a base de dados, nao persiste, nao altera nenhum ajuste operacional nem o modo exploratorio.
Porta, para o codigo de producao, a definicao verificada independentemente no prototipo (Fase 4.1):

  * Duracao unica: 300 s. Um valor por dia (o maior). Esforcos de treino: datas < CORTE.
  * Normalizacao (Overall e modalidades na MESMA unidade, z): por modalidade, z = (w - media) / desvio-padrao
    (ddof=1) dos esforcos de TREINO a 300 s. Assim watts de modalidades diferentes nunca se somam.
  * Carga global do Overall: soma diaria de icu_training_load das 3 modalidades (Bike, Row, Ski), por data
    de calendario. Nao soma watts. Carga de cada modalidade: a sua propria.
  * Equacao: E = recursao de T1, G = recursao de T2, com lag 1 (equations de modelo_ir.componentes).
    Ajuste por grelha T1 x T2 (so treino), P0 livre (limites 0,25..1,5 da mediana/maximo), K1,K2 >= 0 (<= 6).
    Celula escolhida: menor SSE de treino. Celulas equivalentes: SSE <= (1+0,02) x SSE minimo.
  * Criterios (inalterados): c1 K1,K2 > 0,01 e fora dos limites; c2 celula escolhida bate a media no teste;
    c3 >= 80% das celulas batem a media; c4 p5 do bootstrap de K1 > 0,01 (B=200, semente 42).
    Todos cumpridos -> 'estimativa_validada'; calculavel mas nao cumpre -> 'previsto_nao_validado';
    sem ajuste finito / sem observacoes de treino suficientes -> 'curva_nao_calculavel'.
  * Overall e exploratorio: nunca e apresentado como validado.

Suavizacao: APENAS a representacao visual. Media movel centrada de JANELA_SUAV dias (com janela encolhida
nas extremidades, sem valores artificiais). As series de calculo ficam intactas em `_serie_bruta`.
"""
import math
from datetime import date, timedelta

import numpy as np
from scipy.optimize import least_squares

import modelo_ir as ir

CORTE = '2025-01-01'
DURACAO_S = 300
MODALIDADES = ('Bike', 'Row', 'Ski', 'Run')
MODALIDADES_OVERALL = ('Bike', 'Row', 'Ski')
D0_MINIMO = '2021-01-01'
GRELHA_T1 = (20, 35, 42, 50, 80, 120)
GRELHA_T2 = (2, 4, 6.6, 8, 11, 15)
TOL_SSE = 0.02
MIN_TREINO = 15
MIN_DATAS = 12
MIN_TESTE = 5
LIM_K = 0.01
B_BOOTSTRAP = 200
SEMENTE = 42
JANELA_SUAVIZACAO = 7          # dias, mediana/media centrada; so visual
INICIO_VISTA = '2023-01-01'    # janela visivel; calculo usa o calendario completo

ESTADO_EXPLORATORIO = 'exploratorio_nao_validado'
ESTADO_PREVISTO = 'previsto_nao_validado'
ESTADO_VALIDADA = 'estimativa_validada'
ESTADO_NAO_CALCULAVEL = 'curva_nao_calculavel'

ROTULO_OVERALL = 'Exploratório — ainda não validado; são necessários mais testes'
ROTULO_MODALIDADE = 'Curva exploratória — não validada; são necessários mais testes'
ROTULO_NAO_CALCULAVEL = 'Curva não calculável com os dados disponíveis'

FALTAS = {
    'c1': 'parâmetros interiores (K1 e K2 acima de 0,01 e fora dos limites)',
    'c2': 'célula escolhida superar a média no teste',
    'c3': 'estabilidade entre células (pelo menos 80% acima da média)',
    'c4': 'bootstrap de K1 acima de 0,01',
}


def _data(x):
    return str(x or '')[:10]


def _obs_300(curvas, mod):
    """[(data, watts)] com um valor por dia (o maior), so duracao DURACAO_S e watts finitos e > 0."""
    melhor = {}
    for c in curvas or []:
        if not isinstance(c, dict) or c.get('type') != mod:
            continue
        secs = c.get('secs') or []
        watts = c.get('watts') or []
        valor = dict(zip(secs, watts)).get(DURACAO_S)
        if valor is None or not isinstance(valor, (int, float)) or isinstance(valor, bool):
            continue
        if not math.isfinite(valor) or valor <= 0:
            continue
        d = _data(c.get('date'))
        if len(d) != 10:
            continue
        melhor[d] = max(float(valor), melhor.get(d, 0.0))
    return sorted(melhor.items())


def _vetor_diario(mapa, d0, n):
    v = np.zeros(n)
    for k, val in (mapa or {}).items():
        i = (date.fromisoformat(_data(k)) - d0).days
        if 0 <= i < n and val is not None and math.isfinite(float(val)):
            v[i] += max(0.0, float(val))
    return v


def _ajustar(E, G, idx, y):
    """Minimos quadrados limitados para y = P0 + K1*E - K2*G (so treino). None se nao houver solucao finita."""
    if len(y) < 3:
        return None
    med, mx = float(np.median(y)), float(np.max(y))
    lo = np.array([0.25 * med, 0.0, 0.0])
    hi = np.array([1.5 * mx, 6.0, 6.0])

    def res(p):
        return p[0] + p[1] * E[idx] - p[2] * G[idx] - y

    melhor = None
    for x0 in ([med, 0.5, 0.5], [med, 0.05, 0.05]):
        try:
            r = least_squares(res, np.clip(x0, lo + 1e-9, hi - 1e-9), bounds=(lo, hi),
                              method='trf', max_nfev=3000)
        except (ValueError, np.linalg.LinAlgError):
            continue
        if not np.all(np.isfinite(r.x)):
            continue
        if melhor is None or r.cost < melhor.cost:
            melhor = r
    if melhor is None:
        return None
    p = melhor.x
    sse = float(np.sum(melhor.fun ** 2))
    no_limite = bool(np.any(np.isclose(p[1:], 0.0, atol=1e-6)) or np.any(np.isclose(p[1:], 6.0, atol=1e-6))
                     or np.isclose(p[0], lo[0], atol=1e-6) or np.isclose(p[0], hi[0], atol=1e-6))
    return {'p0': float(p[0]), 'k1': float(p[1]), 'k2': float(p[2]), 'sse': sse, 'no_limite': no_limite}


def _grelha(carga, idx_tr, y_tr, idx_te, y_te):
    """Todas as celulas T1 x T2 (so treino para escolher). Ordenadas por SSE de treino."""
    celulas = []
    for t1 in GRELHA_T1:
        for t2 in GRELHA_T2:
            E, G = ir.componentes(carga, t1, t2, lag_dias=1)
            a = _ajustar(E, G, idx_tr, y_tr)
            if a is None:
                continue
            pred = a['p0'] + a['k1'] * E[idx_te] - a['k2'] * G[idx_te]
            rmse = float(np.sqrt(np.mean((y_te - pred) ** 2)))
            celulas.append({'t1': t1, 't2': t2, **a, 'rmse_teste': rmse})
    celulas.sort(key=lambda z: z['sse'])
    return celulas


def _bootstrap_k1(carga, t1, t2, idx_tr, y_tr, rng):
    E, G = ir.componentes(carga, t1, t2, lag_dias=1)
    ks = []
    n = len(y_tr)
    for _ in range(B_BOOTSTRAP):
        j = rng.integers(0, n, n)
        a = _ajustar(E, G, idx_tr[j], y_tr[j])
        if a is not None:
            ks.append(a['k1'])
    if not ks:
        return None
    return [float(np.percentile(ks, 5)), float(np.percentile(ks, 95))]


def _suavizar(x, janela=JANELA_SUAVIZACAO):
    """Media movel centrada, janela encolhida nas extremidades. Apenas para apresentacao."""
    x = np.asarray(x, dtype=float)
    if x.size < janela:
        return x.copy()
    kernel = np.ones(janela)
    soma = np.convolve(x, kernel, mode='same')
    cont = np.convolve(np.ones_like(x), kernel, mode='same')   # nas extremidades conta so os dias existentes
    return soma / np.maximum(cont, 1.0)


def _calendario(cargas, curvas):
    datas = []
    for m in MODALIDADES:
        datas += [_data(k) for k in (cargas.get(m) or {})]
    datas += [_data(c.get('date')) for c in curvas or [] if isinstance(c, dict)]
    datas = [d for d in datas if len(d) == 10]
    if not datas:
        return None, 0
    d0 = min(date.fromisoformat(min(datas)), date.fromisoformat(D0_MINIMO))
    fim = date.fromisoformat(max(datas))
    return d0, (fim - d0).days + 1


def _criterios(celula, rmse_media, frac_melhor, boot):
    c1 = celula['k1'] > LIM_K and celula['k2'] > LIM_K and not celula['no_limite']
    c2 = celula['rmse_teste'] < rmse_media
    c3 = frac_melhor >= 0.8
    c4 = bool(boot and boot[0] > LIM_K)
    falhas = [FALTAS[k] for k, ok in (('c1', c1), ('c2', c2), ('c3', c3), ('c4', c4)) if not ok]
    return {'c1': bool(c1), 'c2': bool(c2), 'c3': bool(c3), 'c4': bool(c4),
            'validada': bool(c1 and c2 and c3 and c4), 'faltas': falhas}


def _modalidade(carga, obs, d0, n, sd, rng, mod):
    """Ajuste condicional da modalidade (W) e serie de influencia em z (divisao pelo desvio-padrao de treino)."""
    datas = [d for d, _ in obs]
    idx = np.array([(date.fromisoformat(d) - d0).days for d in datas])
    y = np.array([w for _, w in obs])
    tr = np.array([d < CORTE for d in datas], dtype=bool)
    te = ~tr
    base = {'modalidade': mod, 'duracao_s': DURACAO_S, 'n_treino': int(tr.sum()), 'n_teste': int(te.sum())}
    if tr.sum() < MIN_TREINO or len(set(np.array(datas)[tr])) < MIN_DATAS or te.sum() < MIN_TESTE or sd is None:
        return {**base, 'estado': ESTADO_NAO_CALCULAVEL, 'motivo': f'observações insuficientes antes de {CORTE} '
                f'(mínimo {MIN_TREINO} observações e {MIN_DATAS} datas de treino; {int(tr.sum())} obs.)',
                'fitness': None, 'fadiga': None, 'liquida': None, 'parametros': None, 'criterios': None}
    rmse_media = float(np.sqrt(np.mean((y[te] - np.mean(y[tr])) ** 2)))
    celulas = _grelha(carga, idx[tr], y[tr], idx[te], y[te])
    if not celulas:
        return {**base, 'estado': ESTADO_NAO_CALCULAVEL, 'motivo': 'ajuste sem solução finita',
                'fitness': None, 'fadiga': None, 'liquida': None, 'parametros': None, 'criterios': None}
    melhor = celulas[0]
    frac = sum(1 for z in celulas if z['rmse_teste'] < rmse_media) / len(celulas)
    boot = _bootstrap_k1(carga, melhor['t1'], melhor['t2'], idx[tr], y[tr], rng)
    crit = _criterios(melhor, rmse_media, frac, boot)
    E, G = ir.componentes(carga, melhor['t1'], melhor['t2'], lag_dias=1)
    return {**base,
            'estado': ESTADO_VALIDADA if crit['validada'] else ESTADO_PREVISTO,
            'motivo': None,
            'parametros': {'t1': melhor['t1'], 't2': melhor['t2'], 'p0': melhor['p0'],
                           'k1': melhor['k1'], 'k2': melhor['k2']},
            'rmse_teste': melhor['rmse_teste'], 'rmse_media': rmse_media,
            'criterios': crit,
            'bootstrap_k1': boot,
            '_serie_bruta': {'d0': d0, 'n': n, 'E': E / sd, 'G': G / sd,
                             'pos': melhor['k1'] * E / sd, 'neg': -melhor['k2'] * G / sd,
                             'liq': (melhor['k1'] * E - melhor['k2'] * G) / sd}}


def calcular(cargas, curvas, cores=None):
    """cargas: {modalidade: {data: icu_training_load}} (de modelo_ir_servico.carga_por_modalidade).
    curvas: [{type, date, secs, watts}]. Devolve a vista (dict JSON) sem persistir nada.
    Nunca altera as entradas."""
    rng = np.random.default_rng(SEMENTE)
    cargas = cargas or {}
    d0, n = _calendario(cargas, curvas)
    if d0 is None:
        return {'status': 'ERRO', 'mensagem': 'sem dados de carga ou de curvas', 'persistido': False}

    obs = {m: _obs_300(curvas, m) for m in MODALIDADES}
    sd = {}
    media = {}
    for m in MODALIDADES:
        tr_vals = np.array([w for d, w in obs[m] if d < CORTE])
        if len(tr_vals) >= 2 and np.std(tr_vals, ddof=1) > 0:
            media[m] = float(np.mean(tr_vals))
            sd[m] = float(np.std(tr_vals, ddof=1))
        else:
            sd[m] = None
            media[m] = None

    # carga por modalidade (vetor diario comum)
    vetor = {m: _vetor_diario(cargas.get(m), d0, n) for m in MODALIDADES}
    carga_overall = sum(vetor[m] for m in MODALIDADES_OVERALL)

    # --- Overall: z pooled das 3 modalidades (treino), carga global somada
    zs, idxs, trs, ys = [], [], [], []
    for m in MODALIDADES_OVERALL:
        if sd[m] is None:
            continue
        for d, w in obs[m]:
            zs.append((w - media[m]) / sd[m])
            idxs.append((date.fromisoformat(d) - d0).days)
            trs.append(d < CORTE)
    zs = np.array(zs)
    idxs = np.array(idxs)
    trs = np.array(trs, dtype=bool)
    overall = None
    if zs.size and trs.sum() >= MIN_TREINO and (~trs).sum() >= MIN_TESTE:
        celulas = _grelha(carga_overall, idxs[trs], zs[trs], idxs[~trs], zs[~trs])
        if celulas:
            m0 = celulas[0]
            E, G = ir.componentes(carga_overall, m0['t1'], m0['t2'], lag_dias=1)
            overall = {'estado': ESTADO_EXPLORATORIO,
                       'parametros': {'t1': m0['t1'], 't2': m0['t2'], 'p0': m0['p0'],
                                      'k1': m0['k1'], 'k2': m0['k2']},
                       'rmse_teste': m0['rmse_teste'],
                       'rmse_media': float(np.sqrt(np.mean((zs[~trs] - zs[trs].mean()) ** 2))),
                       'pos': m0['k1'] * E, 'neg': -m0['k2'] * G, 'liq': m0['k1'] * E - m0['k2'] * G}

    # --- por modalidade (W, depois z)
    mods = {}
    for m in MODALIDADES:
        mods[m] = _modalidade(vetor[m], obs[m], d0, n, sd[m], rng, m)

    # --- serie visivel (suavizada) e datas
    i0 = (date.fromisoformat(INICIO_VISTA) - d0).days
    i0 = max(0, i0)
    datas_vista = [(d0 + timedelta(days=i)).isoformat() for i in range(i0, n)]

    def pronto(serie):
        return [round(float(v), 5) for v in _suavizar(serie)[i0:]]

    out_overall = None
    if overall:
        out_overall = {'estado': overall['estado'], 'parametros': overall['parametros'],
                       'rmse_teste': overall['rmse_teste'], 'rmse_media': overall['rmse_media'],
                       'fitness': pronto(overall['pos']), 'fadiga': pronto(overall['neg']),
                       'liquida': pronto(overall['liq'])}

    out_mods = {}
    for m in MODALIDADES:
        r = mods[m]
        item = {k: v for k, v in r.items() if k != '_serie_bruta'}
        if r['estado'] != ESTADO_NAO_CALCULAVEL and '_serie_bruta' in r:
            s = r['_serie_bruta']
            item['fitness'] = pronto(s['pos'])
            item['fadiga'] = pronto(s['neg'])
            item['liquida'] = pronto(s['liq'])
        out_mods[m] = item

    return {'status': 'OK', 'experimental': True, 'persistido': False,
            'rotulo': ROTULO_OVERALL, 'corte': CORTE, 'duracao_s': DURACAO_S,
            'janela_suavizacao_dias': JANELA_SUAVIZACAO,
            'normalizacao': ('z-score por modalidade (média e desvio-padrão dos esforços de treino a 300 s); '
                             'carga global = soma diária de icu_training_load de Bike, Row e Ski'),
            'carga': 'icu_training_load', 'unidade': 'z',
            'datas': datas_vista, 'overall': out_overall, 'modalidades': out_mods,
            'cores': cores or {}, 'rotulos': {'overall': ROTULO_OVERALL, 'modalidade': ROTULO_MODALIDADE,
                                             'nao_calculavel': ROTULO_NAO_CALCULAVEL},
            'validade_modalidades': {k: v.get('estado') for k, v in out_mods.items()}}
