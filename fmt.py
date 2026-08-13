"""FMT — Functional Multidimensional Tensor (Della Mattia, 2019).

Referencia: FMT_Transformers_ENG.html, §02 Definicao 1.

    F(d) = (1/L) . SUM_{t=d-L+1}^{d}  dx(t) (x) dx(t)^T   em R^{FxF}

onde dx(t) e a variacao diaria do vector de estado fisiologico e (x) e o
produto externo. A traco k = tr(F) e a "curvatura escalar" do estado: alta
quando varias dimensoes mudam de forma abrupta e simultanea. Os valores
proprios dizem se o stress e focal (l1 domina) ou multissistemico (lj
aproximadamente iguais).

SOBRE OS CANAIS DE ATENCAO
O paper (§08) descreve quatro canais que EMERGEM de um Transformer treinado
numa coorte de 30 atletas. Nao temos esse modelo. O que esta aqui sao kernels
explicitos que reproduzem o comportamento descrito para cada canal — uteis
para ler a janela de 28 dias, mas nao sao pesos aprendidos. A interface diz
isso.
"""

import numpy as np

# Ordem fixa das dimensoes, como na Figura 1 do paper
DIMS = ['Load', 'HRV', "W'", 'Sleep', 'WEED']

# O QUE E DERIVADO DOS TEUS DADOS E O QUE E FIXO
#
# Desde a introducao do modulo calibracao.py, os parametros dos canais sao
# estimados por correlacao cruzada nas tuas proprias series. Os numeros
# abaixo so entram quando os teus dados nao chegam — e nesse caso a resposta
# diz fonte='referencia'.
#
# ANTES (mantido so como recurso):
#
# Derivado (individual):
#   - normalizacao de cada dimensao pela sua propria media e desvio
#   - a matriz de covariacao inteira, e portanto kappa e os eigenvalues
#   - os limiares focal/multissistemico (percentis 70/30 do teu historico)
#   - a modulacao dos canais pela variancia real de cada dimensao
#
# Fixo, vindo do paper (nao dos teus dados):
#   - janela de 28 dias (L=28, §03)
#   - tau=6.5 no canal 1, calibrado para dar os 68% em d-1..d-5 que o §08 reporta
#   - centro em d-17 e largura 3.5 no canal 3, da janela d-14..d-21 do §08
#   - decaimentos de 10 e 8 dias nos canais 2 e 4
#
# Estes ultimos sao constantes de forma, nao valores fisiologicos. Se quiseres
# que saiam dos teus dados, o caminho e treinar o modelo — o que exige alvos
# rotulados que nao temos.

CANAIS = {
    'load': {
        'nome': 'Canal 1 · Acumulacao de carga',
        'desc': 'Decaimento exponencial para o passado. Os dias mais recentes '
                'dominam o impulso de carga.',
        'cor': '#E74C3C',
    },
    'hrv': {
        'nome': 'Canal 2 · Recuperacao autonomica',
        'desc': 'Foca a dimensao HRV do tensor. Deteta recuperacao '
                'inter-sessao incompleta.',
        'cor': '#5DADE2',
    },
    'super': {
        'nome': 'Canal 3 · Supercompensacao',
        'desc': 'Foca d-14 a d-21. Procura kappa baixo com valores proprios '
                'equilibrados depois de um bloco de carga.',
        'cor': '#F4D03F',
    },
    'risco': {
        'nome': 'Canal 4 · Sinal de risco',
        'desc': 'Configuracao multidimensional de overreaching: kappa alto '
                'com o valor proprio do HRV a subir.',
        'cor': '#8E44AD',
    },
    'similar': {
        'nome': 'Similaridade entre tensores',
        'desc': 'Atencao no sentido literal de (4): softmax da similaridade '
                'entre vec(F) de cada dia e o dia consultado. Sem projeccoes '
                'aprendidas — e a geometria dos proprios tensores.',
        'cor': '#48C9B0',
    },
}


def _z(a):
    a = np.asarray(a, dtype=np.float64)
    if not np.isfinite(a).any():
        return a
    with np.errstate(all='ignore'):
        mu, sd = np.nanmean(a), np.nanstd(a)
    if not np.isfinite(mu):
        return a
    return (a - mu) / sd if sd > 1e-9 else a - mu


def construir(dimensoes, janela=28):
    """Sequencia de tensores FMT, um por dia.

    dimensoes: dict {nome: serie}. Cada serie e normalizada antes, para que
    escalas diferentes contribuam de forma comparavel.
    Devolve (tensores, kappa, eigenvalues, nomes).
    """
    nomes = [d for d in DIMS if d in dimensoes] + \
            [d for d in dimensoes if d not in DIMS]
    series = [np.asarray(dimensoes[n], dtype=np.float64) for n in nomes]
    if not series:
        return None, None, None, []

    n, F = len(series[0]), len(series)
    X = np.full((n, F), np.nan)
    for j, s in enumerate(series):
        X[:, j] = _z(s)

    dX = np.full_like(X, np.nan)
    dX[1:] = X[1:] - X[:-1]

    tensores = np.full((n, F, F), np.nan)
    kappa = np.full(n, np.nan)
    eig = np.full((n, F), np.nan)

    for t in range(janela, n):
        w = dX[t - janela:t]
        val = np.all(np.isfinite(w), axis=1)
        if val.sum() < max(10, F + 2):
            continue
        d = w[val]
        # F(d) = (1/L) SUM dx (x) dx^T  — momento de segunda ordem
        Ft = (d.T @ d) / len(d)
        tensores[t] = Ft
        kappa[t] = float(np.trace(Ft))
        try:
            ev = np.sort(np.linalg.eigvalsh(Ft))[::-1]
            eig[t] = ev
        except Exception:
            pass
    return tensores, kappa, eig, nomes


def _softmax(x, temp=1.0):
    x = np.asarray(x, dtype=np.float64)
    m = np.isfinite(x)
    out = np.zeros_like(x)
    if not m.any():
        return out
    v = x[m] / max(temp, 1e-9)
    v = v - v.max()
    e = np.exp(v)
    out[m] = e / e.sum()
    return out


def atencao(tensores, kappa, eig, nomes, dia, canal='load', janela=28,
            params=None):
    """Pesos de atencao dos 28 dias anteriores sobre o dia consultado.

    Cada canal e um kernel explicito que reproduz o comportamento descrito
    no §08 do paper. Devolve pesos que somam 1.
    """
    n = len(kappa)
    if dia < janela or dia >= n:
        return None
    ini = dia - janela + 1
    idx = np.arange(ini, dia + 1)
    lag = dia - idx                     # 0 = hoje, 27 = ha 27 dias
    F = len(nomes)

    # Parametros: preferencia aos calibrados nos dados do atleta.
    P = params or {}
    tau_carga = P.get('tau_carga', 6.5)
    lag_hrv = P.get('lag_hrv', 1)
    lag_super = P.get('lag_super', 17)
    largura_super = P.get('largura_super', 3.5)
    tau_risco = P.get('tau_risco', 8)

    def dim(nome):
        return nomes.index(nome) if nome in nomes else None

    if canal == 'load':
        # decaimento exponencial; ponderado pela variancia da dimensao Load
        base = np.exp(-lag / max(tau_carga, 0.5))
        j = dim('Load')
        if j is not None:
            var = np.array([tensores[i][j, j] if np.isfinite(tensores[i][j, j])
                            else 0.0 for i in idx])
            base = base * (1.0 + _z(var) * 0.3 + 0.5)
        pesos = base

    elif canal == 'hrv':
        # variancia da dimensao HRV, com decaimento suave
        j = dim('HRV')
        if j is None:
            return None
        var = np.array([tensores[i][j, j] if np.isfinite(tensores[i][j, j])
                        else 0.0 for i in idx])
        # centrado no lag em que a carga mais deprime o HRV neste atleta
        pesos = np.clip(var, 0, None) * np.exp(-np.abs(lag - lag_hrv) / 6.0)

    elif canal == 'super':
        # janela gaussiana centrada em d-17, entre d-14 e d-21;
        # premeia kappa baixo com valores proprios equilibrados
        base = np.exp(-((lag - float(lag_super)) ** 2) /
                      (2 * max(float(largura_super), 1.0) ** 2))
        k = np.array([kappa[i] if np.isfinite(kappa[i]) else np.nan for i in idx])
        eq = np.zeros(len(idx))
        for p, i in enumerate(idx):
            ev = eig[i]
            ev = ev[np.isfinite(ev) & (ev > 0)]
            if len(ev) >= 2:
                eq[p] = 1.0 - ev[0] / ev.sum()      # alto = equilibrado
        kz = _z(k)
        pesos = base * (1.0 + eq) * np.exp(-np.nan_to_num(kz) * 0.5)

    elif canal == 'risco':
        # kappa alto + valor proprio do HRV a subir, nos dias recentes
        k = np.array([kappa[i] if np.isfinite(kappa[i]) else np.nan for i in idx])
        j = dim('HRV')
        hv = np.zeros(len(idx))
        if j is not None:
            hv = np.array([tensores[i][j, j] if np.isfinite(tensores[i][j, j])
                           else 0.0 for i in idx])
        kz = np.nan_to_num(_z(k))
        hz = np.nan_to_num(_z(hv))
        pesos = np.exp(-lag / max(float(tau_risco), 1.0)) * \
            np.clip(1.0 + kz + hz, 0.05, None)

    elif canal == 'similar':
        # atencao no sentido de (4), sem projeccoes aprendidas:
        # produto interno normalizado entre vec(F) de cada dia e o dia d
        q = tensores[dia].reshape(-1)
        if not np.isfinite(q).all():
            return None
        qn = np.linalg.norm(q) or 1.0
        sim = np.full(len(idx), -np.inf)
        for p, i in enumerate(idx):
            v = tensores[i].reshape(-1)
            if not np.isfinite(v).all():
                continue
            vn = np.linalg.norm(v) or 1.0
            sim[p] = float(q @ v) / (qn * vn) * np.sqrt(F)   # escala 1/sqrt(d)
        return {'lag': lag.tolist(), 'idx': idx.tolist(),
                'pesos': _softmax(sim, temp=0.15).tolist()}
    else:
        return None

    pesos = np.clip(np.nan_to_num(pesos), 0, None)
    s = pesos.sum()
    pesos = pesos / s if s > 0 else np.full(len(pesos), 1.0 / len(pesos))
    return {'lag': lag.tolist(), 'idx': idx.tolist(), 'pesos': pesos.tolist()}


def limiares_lambda1(eig, minimo=60):
    """Limiares focal/multissistemico a partir do proprio historico.

    Em vez de numeros fixos, usamos os percentis 70 e 30 da distribuicao de
    lambda1 deste atleta: "focal" passa a significar "mais concentrado do que
    o teu normal", que e o que interessa. Se nao houver historico suficiente,
    cai para 0.55/0.35 — e ai diz-se que sao valores de referencia.
    """
    serie = []
    for linha in eig:
        v = linha[np.isfinite(linha)]
        v = v[v > 0]
        if len(v) >= 2:
            serie.append(float(v[0] / v.sum()))
    if len(serie) < minimo:
        return 0.55, 0.35, 'referencia', len(serie)
    return (float(np.quantile(serie, 0.70)),
            float(np.quantile(serie, 0.30)), 'historico', len(serie))


def resumo_dia(tensores, kappa, eig, nomes, dia, limiares=None):
    """Matriz, kappa, valores proprios e leitura focal/multissistemica."""
    if dia is None or dia >= len(kappa) or not np.isfinite(kappa[dia]):
        return None
    Ft = tensores[dia]
    ev = eig[dia]
    ev = ev[np.isfinite(ev)]
    pos = ev[ev > 0]
    l1 = float(pos[0] / pos.sum()) if len(pos) else None

    if limiares:
        alto = limiares.get('alto', 0.55)
        baixo = limiares.get('baixo', 0.35)
        fonte_lim = limiares.get('fonte', 'referencia')
        n_hist = limiares.get('n', limiares.get('n_historico', 0))
    else:
        alto, baixo, fonte_lim, n_hist = limiares_lambda1(eig)

    if l1 is None:
        leitura = None
    elif l1 > alto:
        j = int(np.nanargmax(np.diag(Ft)))
        leitura = {'tipo': 'focal', 'cor': '#E67E22',
                   'texto': f'Stress focal (λ₁={l1*100:.0f}% > limiar {alto*100:.0f}%) — '
                            f'variabilidade concentrada em uma direção ({nomes[j]}). '
                            f'Risco: pouca adaptabilidade.'}
    elif l1 < baixo:
        leitura = {'tipo': 'multissistemico', 'cor': '#5DADE2',
                   'texto': f'Stress multissistemico (λ₁={l1*100:.0f}% < limiar {baixo*100:.0f}%) — '
                            f'variabilidade distribuída; vários sistemas instáveis juntos. '
                            f'Risco: colapso coordenado.'}
    else:
        leitura = {'tipo': 'intermedio', 'cor': '#8b949e',
                   'texto': f'Distribuição intermédia (λ₁={l1*100:.0f}%, entre {baixo*100:.0f}% e {alto*100:.0f}%).'}


    return {
        'matriz': [[round(float(v), 4) if np.isfinite(v) else None
                    for v in linha] for linha in Ft],
        'nomes': nomes,
        'kappa': round(float(kappa[dia]), 4),
        'eigen': [round(float(v), 4) for v in ev],
        'lambda1_frac': round(l1, 4) if l1 is not None else None,
        'leitura': leitura,
        'limiares': {'focal_acima': round(alto, 4), 'multi_abaixo': round(baixo, 4),
                     'fonte': fonte_lim, 'n_historico': n_hist},
    }
