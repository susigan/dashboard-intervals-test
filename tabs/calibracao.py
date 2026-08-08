"""Calibracao dos parametros do FMT a partir dos dados do proprio atleta.

Porque existe este modulo
--------------------------
No paper do FMT, os canais de atencao emergem de um Transformer treinado.
Nao ha constantes: os pesos sao aprendidos. Sem esse treino, a tentacao e
copiar numeros do paper — mas os numeros do paper descrevem OUTROS atletas.
O "68% em d-1..d-5" que o §08 reporta e de um atleta especifico em fase de
Build; nao ha razao para ser o teu.

Este modulo estima cada parametro a partir das tuas proprias series, por
correlacao cruzada, e diz sempre a forca da evidencia. Onde nao ha dados
suficientes, devolve o valor de referencia E marca-o como tal — nunca
apresenta um valor de referencia como se fosse teu.

Metodo
------
Cada canal responde a uma pergunta empirica:

  Canal 1  Ao fim de quantos dias e que a carga deixa de pesar no teu HRV?
           -> tau que maximiza |r| entre EWM(carga, tau) e a tendencia do HRV

  Canal 2  Quantos dias depois de uma carga e que o teu HRV cai mais?
           -> lag com correlacao mais negativa entre carga e HRV

  Canal 3  Quantos dias depois de um bloco e que a tua CP sobe mais?
           -> lag com correlacao mais positiva entre carga e CP

  Canal 4  Em que horizonte e que kappa antecipa quedas de CP?
           -> lag com correlacao mais negativa entre kappa e CP futura

Limiares de lambda1: media +/- 1 desvio do teu proprio historico. "Focal"
passa a significar "mais concentrado do que o teu normal", com um criterio
estatistico em vez de um percentil escolhido a mao.
"""

import numpy as np

# Valores de referencia. So sao usados quando os teus dados nao chegam, e
# nesse caso a resposta diz fonte='referencia'.
REFERENCIA = {
    'tau_carga': 6.5,        # reproduz os 68% em d-1..d-5 do §08 (outro atleta)
    'lag_hrv': 1,            # a carga de ontem explica o HRV de hoje
    'lag_super': 17,         # centro de d-14..d-21 (§08)
    'largura_super': 3.5,    # escolha de forma, sem base no paper
    'tau_risco': 8,          # sem base no paper
}

P_MINIMO = 0.10              # acima disto a correlacao nao sustenta nada


def _limpar(x, y):
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    m = np.isfinite(x) & np.isfinite(y)
    return x[m], y[m]


def _pearson(x, y):
    """r e p aproximado (t de Student). None se nao houver dados."""
    import math
    x, y = _limpar(x, y)
    n = len(x)
    if n < 10 or np.std(x) < 1e-9 or np.std(y) < 1e-9:
        return None, None, n
    r = float(np.corrcoef(x, y)[0, 1])
    if not np.isfinite(r):
        return None, None, n
    if abs(r) >= 1:
        return r, 0.0, n
    t = r * math.sqrt((n - 2) / (1 - r * r))
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return round(r, 4), round(p, 5), n


def _ewm(v, span):
    a = 2.0 / (span + 1.0)
    out, prev = np.empty(len(v)), None
    for i, x in enumerate(v):
        prev = x if prev is None else a * x + (1 - a) * prev
        out[i] = prev
    return out


def _desloca(serie, lag):
    """serie[t-lag] alinhado com t. Os primeiros lag dias ficam NaN."""
    s = np.asarray(serie, dtype=np.float64)
    if lag <= 0:
        return s
    out = np.full(len(s), np.nan)
    out[lag:] = s[:-lag]
    return out


def calibrar_tau(carga, alvo,
                 taus=(2, 3, 4, 5, 6.5, 8, 10, 12, 14, 16, 18, 21, 25, 30, 42),
                 lag=1, sinal=None):
    """tau da media exponencial que melhor explica o alvo.

    sinal: -1 se se espera correlacao negativa (carga sobe -> HRV desce),
           +1 se positiva, None se qualquer.
    """
    carga = np.nan_to_num(np.asarray(carga, dtype=np.float64))
    alvo = np.asarray(alvo, dtype=np.float64)
    if np.isfinite(alvo).sum() < 30:
        return {'fonte': 'referencia', 'valor': REFERENCIA['tau_carga'],
                'motivo': f'so {int(np.isfinite(alvo).sum())} dias com alvo '
                          '(precisa de 30)', 'testados': []}

    testados, melhor = [], None
    for t in taus:
        e = _desloca(_ewm(carga, t), lag)
        r, p, n = _pearson(e, alvo)
        testados.append({'tau': t, 'r': r, 'p': p, 'n': n})
        if r is None or p is None or p > P_MINIMO:
            continue
        if sinal is not None and np.sign(r) != sinal:
            continue
        if melhor is None or abs(r) > abs(melhor['r']):
            melhor = {'tau': t, 'r': r, 'p': p, 'n': n}

    if melhor is None:
        return {'fonte': 'referencia', 'valor': REFERENCIA['tau_carga'],
                'motivo': f'nenhum tau deu correlacao com p < {P_MINIMO}'
                          + (f' e sinal {sinal:+d}' if sinal else ''),
                'testados': testados}
    return {'fonte': 'dados', 'valor': melhor['tau'], 'r': melhor['r'],
            'p': melhor['p'], 'n': melhor['n'], 'testados': testados}


def calibrar_lag(x, y, lags=range(0, 29), sinal=None, chave_ref=None):
    """Lag em que x[t-lag] melhor explica y[t]."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if np.isfinite(y).sum() < 30:
        return {'fonte': 'referencia', 'valor': REFERENCIA.get(chave_ref),
                'motivo': f'so {int(np.isfinite(y).sum())} dias com alvo '
                          '(precisa de 30)', 'testados': []}

    testados, melhor = [], None
    for L in lags:
        r, p, n = _pearson(_desloca(x, L), y)
        testados.append({'lag': int(L), 'r': r, 'p': p, 'n': n})
        if r is None or p is None or p > P_MINIMO:
            continue
        if sinal is not None and np.sign(r) != sinal:
            continue
        if melhor is None or abs(r) > abs(melhor['r']):
            melhor = {'lag': int(L), 'r': r, 'p': p, 'n': n}

    if melhor is None:
        return {'fonte': 'referencia', 'valor': REFERENCIA.get(chave_ref),
                'motivo': f'nenhum lag deu correlacao com p < {P_MINIMO}'
                          + (f' e sinal {sinal:+d}' if sinal else ''),
                'testados': testados}

    # largura: quantos lags a volta mantem pelo menos 70% do r maximo
    bons = [t['lag'] for t in testados
            if t['r'] is not None and abs(t['r']) >= 0.7 * abs(melhor['r'])]
    largura = (max(bons) - min(bons)) / 2.0 if len(bons) > 1 else 3.5
    return {'fonte': 'dados', 'valor': melhor['lag'], 'r': melhor['r'],
            'p': melhor['p'], 'n': melhor['n'],
            'largura': round(max(1.5, largura), 1),
            'janela': [min(bons), max(bons)] if bons else None,
            'testados': testados}


def limiares_por_distribuicao(serie, minimo=60):
    """Media +/- 1 desvio do proprio historico.

    Criterio estatistico em vez de percentil escolhido a mao: 'focal' passa a
    ser 'mais concentrado do que o habitual para ti'. Numa distribuicao
    normal, +/-1 SD cobre ~68% dos dias como 'normal'.
    """
    v = np.asarray([x for x in serie if x is not None and np.isfinite(x)],
                   dtype=np.float64)
    if len(v) < minimo:
        return {'fonte': 'referencia', 'alto': 0.55, 'baixo': 0.35,
                'motivo': f'so {len(v)} dias (precisa de {minimo})',
                'n': len(v)}
    mu, sd = float(v.mean()), float(v.std())
    return {'fonte': 'dados', 'alto': round(mu + sd, 4),
            'baixo': round(mu - sd, 4), 'media': round(mu, 4),
            'desvio': round(sd, 4), 'n': len(v),
            'p70': round(float(np.quantile(v, 0.70)), 4),
            'p30': round(float(np.quantile(v, 0.30)), 4)}


def calibrar_tudo(carga, hrv_trend=None, cp=None, kappa=None, lambda1=None):
    """Calibra os quatro canais e os limiares. Devolve valores e evidencia."""
    out = {'p_minimo': P_MINIMO, 'referencia': REFERENCIA}

    # Canal 1: quanto tempo a carga pesa. Mais carga acumulada -> HRV mais baixo.
    out['canal1_tau'] = (calibrar_tau(carga, hrv_trend, lag=1, sinal=-1)
                         if hrv_trend is not None else
                         {'fonte': 'referencia', 'valor': REFERENCIA['tau_carga'],
                          'motivo': 'sem serie de HRV'})

    # Canal 2: quantos dias depois da carga o HRV cai mais.
    out['canal2_lag'] = (calibrar_lag(carga, hrv_trend, range(0, 15), -1, 'lag_hrv')
                         if hrv_trend is not None else
                         {'fonte': 'referencia', 'valor': REFERENCIA['lag_hrv'],
                          'motivo': 'sem serie de HRV'})

    # Canal 3: quantos dias depois de um bloco a CP sobe. Aqui o sinal e
    # positivo — e a definicao de supercompensacao.
    out['canal3_lag'] = (calibrar_lag(carga, cp, range(5, 29), +1, 'lag_super')
                         if cp is not None else
                         {'fonte': 'referencia', 'valor': REFERENCIA['lag_super'],
                          'motivo': 'sem serie de CP'})

    # Canal 4: em que horizonte kappa antecipa quedas de CP.
    out['canal4_lag'] = (calibrar_lag(kappa, cp, range(0, 22), -1, 'tau_risco')
                         if (kappa is not None and cp is not None) else
                         {'fonte': 'referencia', 'valor': REFERENCIA['tau_risco'],
                          'motivo': 'sem kappa ou sem CP'})

    out['limiares_lambda1'] = (limiares_por_distribuicao(lambda1)
                               if lambda1 is not None else
                               {'fonte': 'referencia', 'alto': 0.55,
                                'baixo': 0.35, 'motivo': 'sem lambda1'})

    n_dados = sum(1 for k, v in out.items()
                  if isinstance(v, dict) and v.get('fonte') == 'dados')
    out['resumo'] = {
        'derivados_dos_dados': n_dados,
        'total': 5,
        'nota': ('Parametros com fonte "dados" saem das tuas series por '
                 'correlacao cruzada. Os de fonte "referencia" vem do paper '
                 'ou de escolha de forma — descrevem outros atletas, nao ti.'),
    }
    return out
