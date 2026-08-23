"""utils/mnirs.py — limpeza e filtragem de sinais NIRS (SmO2, THb).

Portado do pacote mnirs de Jem Arnold (jemarnold.github.io/mnirs). A ordem
das operacoes e' a do vignette e nao e' arbitraria:

    resample -> replace (invalidos e outliers) -> filter -> shift/rescale

Filtrar antes de remover outliers espalha o outlier pelos vizinhos; remover
outliers antes de regularizar a amostragem faz a janela movel abranger
periodos de tempo diferentes conforme a densidade de amostras.

Nota do proprio autor que vale a pena reter: o SmO2 nao e' medido numa
escala absoluta. Comparar valores entre sessoes, entre musculos ou entre
pessoas exige normalizar primeiro, e a normalizacao escolhida decide o que
se pode concluir. Deslocar duas pernas para a mesma base assume que a base
representa a mesma condicao nas duas.
"""

import math


def _mediana(vs):
    vs = sorted(v for v in vs if v is not None)
    if not vs:
        return None
    n = len(vs)
    return vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2


def resample(tempo, valores, hz=1.0, metodo='linear'):
    """Grelha regular de tempo, com interpolacao linear ou LOCF.

    Os streams da Intervals.icu vem tipicamente a 1 Hz mas com amostras
    repetidas ou em falta quando o sensor perde ligacao. Sem regularizar,
    uma janela de 15 amostras cobre 15 s numa parte do ficheiro e 40 s
    noutra.
    """
    pares = [(float(t), v) for t, v in zip(tempo, valores) if t is not None]
    if len(pares) < 2:
        return list(tempo), list(valores)
    pares.sort()
    t0, t1 = pares[0][0], pares[-1][0]
    passo = 1.0 / hz
    n = int((t1 - t0) / passo) + 1
    saida_t = [t0 + i * passo for i in range(n)]
    saida_v, j = [], 0
    for t in saida_t:
        while j + 1 < len(pares) and pares[j + 1][0] <= t:
            j += 1
        if pares[j][0] == t or j + 1 >= len(pares):
            saida_v.append(pares[j][1])
            continue
        ta, va = pares[j]
        tb, vb = pares[j + 1]
        if va is None or vb is None or metodo == 'locf':
            saida_v.append(va)
        else:
            f = (t - ta) / (tb - ta) if tb > ta else 0
            saida_v.append(va + (vb - va) * f)
    return saida_t, saida_v


def replace(valores, invalidos=(0,), acima=None, abaixo=None,
            corte_outlier=3.0, largura=7, metodo='linear'):
    """Marca invalidos e outliers locais, e interpola por cima.

    O corte de 3 corresponde a regra de Pearson -- 3 desvios em torno da
    MEDIANA local, nao da media. A mediana e' usada de proposito: com a
    media, um pico isolado desloca o proprio centro contra o qual esta a ser
    julgado e escapa a deteccao.
    """
    vs = list(valores)
    n = len(vs)
    fora = [False] * n
    n_invalidos = n_outliers = 0

    for i, v in enumerate(vs):
        if v is None:
            fora[i] = True
            continue
        if invalidos and v in invalidos:
            fora[i] = True
            n_invalidos += 1
        elif acima is not None and v > acima:
            fora[i] = True
            n_invalidos += 1
        elif abaixo is not None and v < abaixo:
            fora[i] = True
            n_invalidos += 1

    if corte_outlier:
        metade = max(1, largura // 2)
        for i in range(n):
            if fora[i]:
                continue
            jan = [vs[k] for k in range(max(0, i - metade),
                                        min(n, i + metade + 1))
                   if vs[k] is not None and not fora[k]]
            if len(jan) < 3:
                continue
            med = _mediana(jan)
            desvios = sorted(abs(x - med) for x in jan)
            mad = desvios[len(desvios) // 2]
            if mad <= 0:
                continue
            if abs(vs[i] - med) > corte_outlier * 1.4826 * mad:
                fora[i] = True
                n_outliers += 1

    limpos = [None if fora[i] else vs[i] for i in range(n)]
    if metodo == 'none':
        return limpos, {'invalidos': n_invalidos, 'outliers': n_outliers}

    # interpolar sobre os buracos
    validos = [i for i, v in enumerate(limpos) if v is not None]
    if not validos:
        return limpos, {'invalidos': n_invalidos, 'outliers': n_outliers,
                        'erro': 'nenhum ponto valido'}
    for i in range(n):
        if limpos[i] is not None:
            continue
        ant = max([k for k in validos if k < i], default=None)
        seg = min([k for k in validos if k > i], default=None)
        if ant is None:
            limpos[i] = limpos[seg]
        elif seg is None or metodo == 'locf':
            limpos[i] = limpos[ant]
        else:
            f = (i - ant) / (seg - ant)
            limpos[i] = limpos[ant] + (limpos[seg] - limpos[ant]) * f
    return limpos, {'invalidos': n_invalidos, 'outliers': n_outliers,
                    'pct_substituido': round((n_invalidos + n_outliers)
                                             / n * 100, 1) if n else 0}


def media_movel(valores, largura=15):
    """Media movel centrada. O filtro mais simples e o mais previsivel."""
    n = len(valores)
    metade = max(1, largura // 2)
    out = []
    for i in range(n):
        jan = [v for v in valores[max(0, i - metade):min(n, i + metade + 1)]
               if v is not None]
        out.append(sum(jan) / len(jan) if jan else None)
    return out


def butterworth(valores, hz=1.0, fc=0.02, ordem=2):
    """Passa-baixo de Butterworth, ida e volta (sem desfasamento).

    E' o filtro mais usado na literatura de mNIRS. Recorre ao scipy quando
    existe; sem ele, cai na media movel com largura equivalente, e diz que
    o fez -- em vez de devolver silenciosamente outra coisa.
    """
    try:
        from scipy.signal import butter, filtfilt
        import numpy as np
        vs = np.array([v if v is not None else np.nan for v in valores],
                      dtype=float)
        if np.isnan(vs).any():
            idx = np.arange(len(vs))
            bons = ~np.isnan(vs)
            if bons.sum() < 4:
                return list(valores), {'metodo': 'nenhum',
                                       'motivo': 'poucos pontos validos'}
            vs = np.interp(idx, idx[bons], vs[bons])
        wn = min(0.99, max(1e-4, fc / (hz / 2.0)))
        b, a = butter(ordem, wn, btype='low')
        pad = max(3 * max(len(a), len(b)), 12)
        if len(vs) <= pad:
            return media_movel(list(vs), 15), {
                'metodo': 'media movel', 'motivo': 'serie curta demais'}
        return [float(x) for x in filtfilt(b, a, vs)], {
            'metodo': 'butterworth', 'ordem': ordem, 'fc_hz': fc, 'wn': wn}
    except ImportError:
        largura = max(3, int(round(hz / max(fc, 1e-4) / 4)))
        return media_movel(valores, largura), {
            'metodo': 'media movel', 'largura': largura,
            'motivo': 'scipy indisponivel'}


def deslocar(valores, para=0.0, primeiros=None, posicao='first'):
    """Desloca a serie para que a referencia fique em 'para'.

    posicao: 'first' usa a media dos primeiros N pontos, 'min' o minimo,
    'max' o maximo. Preserva a amplitude -- so' muda o nivel.
    """
    vs = [v for v in valores if v is not None]
    if not vs:
        return list(valores), None
    if posicao == 'min':
        ref = min(vs)
    elif posicao == 'max':
        ref = max(vs)
    else:
        n = primeiros or min(len(vs), 60)
        ref = sum(vs[:n]) / n
    d = para - ref
    return [None if v is None else v + d for v in valores], round(ref, 2)


def reescalar(valores, minimo=0.0, maximo=100.0):
    """Reescala para um novo intervalo dinamico.

    Assume que o minimo e o maximo observados representam a capacidade
    funcional do tecido nesta sessao. E' uma suposicao forte: perde-se a
    diferenca de amplitude entre musculos, ganha-se a comparacao da forma
    da resposta.
    """
    vs = [v for v in valores if v is not None]
    if len(vs) < 2:
        return list(valores), None
    lo, hi = min(vs), max(vs)
    if hi == lo:
        return list(valores), None
    f = (maximo - minimo) / (hi - lo)
    return ([None if v is None else minimo + (v - lo) * f for v in valores],
            {'min_original': round(lo, 2), 'max_original': round(hi, 2),
             'amplitude_original': round(hi - lo, 2)})


def processar(tempo, canais, hz=1.0, acima=None, corte_outlier=3.0,
              largura=7, fc=0.02, ordem=2, normalizar=None):
    """Pipeline completo: resample -> replace -> filter -> (shift|rescale).

    canais: {'smo2': [...], 'thb': [...]}
    normalizar: None | 'deslocar' | 'reescalar'
    """
    saida, diag = {}, {}
    t_ref = None
    for nome, serie in (canais or {}).items():
        if not serie:
            continue
        t, v = resample(tempo, serie, hz=hz)
        t_ref = t_ref or t
        v, d_rep = replace(v, acima=acima, corte_outlier=corte_outlier,
                           largura=largura)
        v, d_filt = butterworth(v, hz=hz, fc=fc, ordem=ordem)
        d = {'resample_hz': hz, **d_rep, 'filtro': d_filt,
             'n_pontos': len(v)}
        if normalizar == 'deslocar':
            v, ref = deslocar(v, para=0.0, primeiros=int(60 * hz))
            d['deslocado_de'] = ref
        elif normalizar == 'reescalar':
            v, esc = reescalar(v)
            d['escala_original'] = esc
        saida[nome] = [round(x, 2) if x is not None else None for x in v]
        diag[nome] = d
    return {'tempo': [round(x, 1) for x in (t_ref or [])],
            'canais': saida, 'diagnostico': diag,
            'nota': ('ordem do pipeline: resample, substituir invalidos e '
                     'outliers, filtrar, normalizar. Filtrar antes de '
                     'remover outliers espalha-os pelos vizinhos')}
