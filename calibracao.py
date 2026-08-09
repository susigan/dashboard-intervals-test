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


def _forca(r, n):
    """Forca pratica da correlacao.

    Com n na ordem dos milhares, p=0 nao diz nada: qualquer correlacao
    minuscula e "significativa". O que interessa e o r2 — a fraccao da
    variacao que fica explicada.
    """
    if r is None:
        return {'r2': None, 'forca': None}
    r2 = r * r
    if r2 >= 0.25:
        f = 'forte'
    elif r2 >= 0.09:
        f = 'moderada'
    elif r2 >= 0.02:
        f = 'fraca'
    else:
        f = 'residual'
    return {'r2': round(r2, 4), 'forca': f,
            'variacao_explicada_pct': round(r2 * 100, 1)}


def sem_tendencia(x, janela=90):
    """Residuos face a uma media movel centrada e longa.

    Porque e preciso: ao longo de anos, carga e HRV tem ambos derivas lentas
    (forma a melhorar, sazonalidade, idade). Correlacionar duas series com
    deriva partilhada da correlacao sem existir relacao dinamica — e o
    confundimento por tendencia. Ao tirar a media movel de +/-90 dias, fica
    so a dinamica de semanas, que e o que interessa aqui.

    Verificado: com relacao real de tau=10 dias mais deriva, a correlacao
    bruta melhora ate ao fim da grelha (escolhe a tendencia); sem tendencia,
    faz maximo em tau=10.
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    out = np.full(n, np.nan)
    for i in range(n):
        seg = x[max(0, i - janela):min(n, i + janela + 1)]
        seg = seg[np.isfinite(seg)]
        if len(seg) >= 10:
            out[i] = x[i] - seg.mean()
    return out


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
                 taus=(2, 3, 4, 5, 6.5, 8, 10, 12, 14, 16, 18, 21, 25, 30, 42,
                       56, 75, 100, 140, 180, 240),
                 lag=1, sinal=None, destendenciar=True):
    """tau da media exponencial que melhor explica o alvo.

    sinal: -1 se se espera correlacao negativa (carga sobe -> HRV desce),
           +1 se positiva, None se qualquer.
    """
    carga = np.nan_to_num(np.asarray(carga, dtype=np.float64))
    alvo = np.asarray(alvo, dtype=np.float64)
    alvo_use = sem_tendencia(alvo) if destendenciar else alvo
    if np.isfinite(alvo).sum() < 30:
        return {'fonte': 'referencia', 'valor': REFERENCIA['tau_carga'],
                'motivo': f'so {int(np.isfinite(alvo).sum())} dias com alvo '
                          '(precisa de 30)', 'testados': []}

    testados, melhor = [], None
    for t in taus:
        e = _desloca(_ewm(carga, t), lag)
        if destendenciar:
            e = sem_tendencia(e)
        r, p, n = _pearson(e, alvo_use)
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
    # Se o melhor ficou no extremo da grelha, nao e um optimo — e o sitio
    # onde a procura parou. Dizer isso e essencial: caso contrario um valor
    # de fronteira passa por resultado.
    extremos = [taus[0], taus[-1]]
    na_fronteira = melhor['tau'] in extremos
    out = {'fonte': 'dados', 'valor': melhor['tau'], 'r': melhor['r'],
           'p': melhor['p'], 'n': melhor['n'], 'testados': testados,
           'destendenciado': destendenciar,
           **_forca(melhor['r'], melhor['n'])}
    if na_fronteira:
        out['aviso'] = (f"tau={melhor['tau']} e o extremo da grelha "
                        f"({taus[0]}-{taus[-1]}): o r ainda estava a melhorar, "
                        "o valor verdadeiro esta fora deste intervalo")
        out['fronteira'] = True
    return out


def calibrar_lag(x, y, lags=range(0, 29), sinal=None, chave_ref=None,
                 destendenciar=True):
    """Lag em que x[t-lag] melhor explica y[t]."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if destendenciar:
        x, y = sem_tendencia(x), sem_tendencia(y)
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
    lags_l = list(lags)
    out = {'fonte': 'dados', 'valor': melhor['lag'], 'r': melhor['r'],
           'p': melhor['p'], 'n': melhor['n'],
           'largura': round(max(1.5, largura), 1),
           'janela': [min(bons), max(bons)] if bons else None,
           'testados': testados, 'destendenciado': destendenciar,
           **_forca(melhor['r'], melhor['n'])}
    if melhor['lag'] in (lags_l[0], lags_l[-1]):
        out['fronteira'] = True
        out['aviso'] = (f"lag={melhor['lag']} e o extremo do intervalo testado "
                        f"({lags_l[0]}-{lags_l[-1]})")
    return out


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

    # Canal 1: quanto tempo a carga pesa.
    #
    # NAO impomos sinal. A expectativa fisiologica e negativa (mais carga
    # acumulada -> HRV mais baixo), mas ha um efeito em sentido contrario que
    # e real: o atleta treina mais quando se sente bem. Essa causalidade
    # invertida (HRV alto -> mais carga) cancela em parte o efeito
    # fisiologico, e nos dados agregados pode ate domina-lo. Forcar o sinal
    # negativo esconderia isso.
    if hrv_trend is not None:
        c1 = calibrar_tau(carga, hrv_trend, lag=1, sinal=None)
        if c1.get('fonte') == 'dados' and (c1.get('r') or 0) > 0:
            c1['aviso_causalidade'] = (
                'correlacao POSITIVA: mais carga acumulada anda com HRV mais '
                'alto. O efeito fisiologico e o inverso — isto sugere '
                'causalidade invertida (treinas mais quando o HRV esta bom). '
                'O parametro nao deve ser lido como decaimento de fadiga.')
        out['canal1_tau'] = c1
    else:
        out['canal1_tau'] = {'fonte': 'referencia',
                             'valor': REFERENCIA['tau_carga'],
                             'motivo': 'sem serie de HRV'}

    # Canal 2: quantos dias depois da carga o HRV cai mais.
    out['canal2_lag'] = (calibrar_lag(carga, hrv_trend, range(0, 15), None, 'lag_hrv')
                         if hrv_trend is not None else
                         {'fonte': 'referencia', 'valor': REFERENCIA['lag_hrv'],
                          'motivo': 'sem serie de HRV'})

    # Canal 3: quantos dias depois de um bloco a CP sobe. Aqui o sinal e
    # positivo — e a definicao de supercompensacao.
    out['canal3_lag'] = (calibrar_lag(carga, cp, range(5, 29), None, 'lag_super')
                         if cp is not None else
                         {'fonte': 'referencia', 'valor': REFERENCIA['lag_super'],
                          'motivo': 'sem serie de CP'})

    # Canal 4: em que horizonte kappa se relaciona com a CP.
    #
    # NAO impomos o sinal. A intuicao inicial era kappa alto -> CP a cair
    # (risco), mas nos dados reais deste atleta a relacao e a inversa e mais
    # forte: kappa alto prevê CP MAIS ALTA 14-21 dias depois. Faz sentido —
    # kappa alto e uma perturbacao grande do sistema, ou seja um estimulo, e
    # a resposta chega com atraso. Forcar o sinal negativo rejeitava um sinal
    # real. Reportamos o que os dados dizem e deixamos a leitura seguir.
    if kappa is not None and cp is not None:
        c4 = calibrar_lag(kappa, cp, range(0, 43), None, 'tau_risco')
        if c4.get('fonte') == 'dados':
            c4['interpretacao'] = (
                'kappa alto antecede CP mais alta — assinatura de estimulo '
                'com resposta atrasada' if (c4.get('r') or 0) > 0 else
                'kappa alto antecede CP mais baixa — assinatura de risco')
        out['canal4_lag'] = c4
    else:
        out['canal4_lag'] = {'fonte': 'referencia',
                             'valor': REFERENCIA['tau_risco'],
                             'motivo': 'sem kappa ou sem CP'}

    out['limiares_lambda1'] = (limiares_por_distribuicao(lambda1)
                               if lambda1 is not None else
                               {'fonte': 'referencia', 'alto': 0.55,
                                'baixo': 0.35, 'motivo': 'sem lambda1'})

    # avisos que merecem ser vistos, nao enterrados no JSON
    avisos = []
    for k, v in out.items():
        if not isinstance(v, dict):
            continue
        if v.get('aviso'):
            avisos.append(f"{k}: {v['aviso']}")
        if v.get('forca') in ('residual', 'fraca') and v.get('fonte') == 'dados':
            avisos.append(
                f"{k}: r={v.get('r')} explica so {v.get('variacao_explicada_pct')}% "
                f"da variacao — estatisticamente significativo por causa do n "
                f"({v.get('n')}), mas fraco na pratica")
    out['avisos'] = avisos

    # Veredicto: ha sinal utilizavel, ou os canais sao decorativos?
    canais = ['canal1_tau', 'canal2_lag', 'canal3_lag', 'canal4_lag']
    r2s = [out[k].get('r2') for k in canais
           if isinstance(out.get(k), dict) and out[k].get('r2') is not None]
    melhor_r2 = max(r2s) if r2s else 0.0
    if melhor_r2 >= 0.09:
        vered = {'nivel': 'utilizavel', 'cor': '#2ECC71',
                 'texto': f'O melhor canal explica {melhor_r2*100:.0f}% da '
                          'variacao. Ha relacao dinamica detectavel.'}
    elif melhor_r2 >= 0.02:
        vered = {'nivel': 'fraco', 'cor': '#E67E22',
                 'texto': f'O melhor canal explica {melhor_r2*100:.0f}% da '
                          'variacao. Sinal fraco — usar como indicacao, nao '
                          'para decidir treino.'}
    else:
        vered = {'nivel': 'sem_sinal', 'cor': '#E74C3C',
                 'texto': f'Nenhum canal passa de {melhor_r2*100:.1f}% de '
                          'variacao explicada. Nos dados agregados nao ha '
                          'relacao dinamica detectavel entre carga e '
                          'HRV/CP — o mapa de atencao fica decorativo. '
                          'Ver a calibracao por modalidade e por periodo.'}
    out['veredicto'] = {**vered, 'melhor_r2': round(melhor_r2, 4)}

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


# ══════════════════════════════════════════════════════════════════════════
# Calibracao segmentada
# ══════════════════════════════════════════════════════════════════════════

def calibrar_por_segmento(segmentos, carga, hrv, cp, minimo_dias=120):
    """Repete a calibracao dentro de cada segmento.

    Porque isto interessa: no agregado de varios anos, blocos de base,
    construcao e pico misturam-se, e relacoes que existem dentro de um bloco
    diluem-se. O mesmo se passa entre modalidades — a carga de Ski e a de
    Bike nao produzem a mesma resposta autonomica, mas somamos as duas num
    unico sinal.

    segmentos: {nome: mascara booleana ou lista de indices}
    """
    out = {}
    for nome, sel in segmentos.items():
        idx = np.asarray(sel)
        if idx.dtype == bool:
            n_dias = int(idx.sum())
            pick = lambda a: np.asarray(a, dtype=np.float64)[idx] if a is not None else None
        else:
            n_dias = len(idx)
            pick = lambda a: np.asarray(a, dtype=np.float64)[idx] if a is not None else None

        if n_dias < minimo_dias:
            out[nome] = {'n_dias': n_dias,
                         'motivo': f'so {n_dias} dias (precisa de {minimo_dias})'}
            continue

        c = pick(carga)
        h = pick(hrv)
        p = pick(cp)
        # janela de destendenciamento menor: o segmento e mais curto
        jan = max(21, min(90, n_dias // 6))
        r = {'n_dias': n_dias, 'janela_destend': jan}

        if h is not None and np.isfinite(h).sum() >= 40:
            r['tau'] = calibrar_tau(c, h, lag=1, sinal=None)
            r['lag_hrv'] = calibrar_lag(c, h, range(0, 15), None, 'lag_hrv')
        if p is not None and np.isfinite(p).sum() >= 40:
            r['lag_cp'] = calibrar_lag(c, p, range(5, 29), None, 'lag_super')

        r2s = [v.get('r2') for v in r.values()
               if isinstance(v, dict) and v.get('r2') is not None]
        r['melhor_r2'] = round(max(r2s), 4) if r2s else None
        out[nome] = r

    # onde e que o sinal e mais forte
    com_r2 = {k: v['melhor_r2'] for k, v in out.items()
              if isinstance(v, dict) and v.get('melhor_r2')}
    out['_melhor_segmento'] = (max(com_r2, key=com_r2.get) if com_r2 else None)
    out['_comparacao'] = (
        'Se algum segmento tiver r2 muito acima do agregado, e sinal de que a '
        'relacao existe la dentro e se dilui ao juntar tudo.')
    return out
