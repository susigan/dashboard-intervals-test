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


# ══════════════════════════════════════════════════════════════════════════
# DETECCAO DO INICIO DO PROTOCOLO
#
# Muitas sessoes com Moxy tem aquecimento antes do protocolo. O padrao do
# protocolo e' ON/OFF repetido; o aquecimento e' continuo ou irregular. A
# fronteira entre os dois e' a ultima pausa longa antes da sequencia
# comecar.
#
# Detecta-se pela POTENCIA, nao pelo campo 'type' dos intervalos: neste
# projecto ja' se estabeleceu que esse campo nao e' de confianca para
# distinguir trabalho de recuperacao.
# ══════════════════════════════════════════════════════════════════════════

def detectar_blocos(tempo, watts, hz=1.0, limiar_rel=0.35, min_dur=20):
    """Blocos ON/OFF a partir da potencia.

    limiar_rel: fraccao da potencia mediana dos blocos altos abaixo da qual
    se considera OFF. min_dur: segundos minimos para um bloco contar --
    abaixo disso e' oscilacao, nao um bloco.
    """
    vs = [(w if w is not None else 0.0) for w in watts]
    validos = [w for w in vs if w > 0]
    if len(validos) < 30:
        return {'ok': False, 'motivo': 'sem potencia suficiente'}
    validos.sort()
    p75 = validos[int(0.75 * (len(validos) - 1))]
    limiar = p75 * limiar_rel

    estados = [w >= limiar for w in vs]
    blocos, ini, actual = [], 0, estados[0]
    for i in range(1, len(estados)):
        if estados[i] != actual:
            blocos.append({'on': actual, 'i0': ini, 'i1': i - 1})
            ini, actual = i, estados[i]
    blocos.append({'on': actual, 'i0': ini, 'i1': len(estados) - 1})

    # fundir blocos curtos no vizinho: um segundo de queda nao e' um OFF
    min_n = int(min_dur * hz)
    fundidos = []
    for b in blocos:
        dur = b['i1'] - b['i0'] + 1
        if fundidos and dur < min_n:
            fundidos[-1]['i1'] = b['i1']
        else:
            fundidos.append(dict(b))

    for b in fundidos:
        b['t0'] = float(tempo[b['i0']]) if b['i0'] < len(tempo) else None
        b['t1'] = float(tempo[b['i1']]) if b['i1'] < len(tempo) else None
        b['duracao_s'] = round((b['t1'] - b['t0']) if b['t0'] is not None else 0, 1)
        jan = [vs[k] for k in range(b['i0'], b['i1'] + 1)]
        b['watts_medio'] = round(sum(jan) / len(jan), 1) if jan else None

    return {'ok': True, 'limiar_w': round(limiar, 1),
            'p75_watts': round(p75, 1), 'blocos': fundidos,
            'n_on': sum(1 for b in fundidos if b['on']),
            'n_off': sum(1 for b in fundidos if not b['on'])}


def propor_corte(blocos_info, hz=1.0, off_minimo=None):
    """Onde comeca o protocolo: apos a ultima pausa longa antes da serie.

    O separador do aquecimento e' a pausa MAIS LONGA que ainda tenha pelo
    menos dois blocos de trabalho a seguir. Escolher simplesmente a ultima
    pausa longa nao serve: as recuperacoes entre repeticoes tambem sao
    longas, e a busca caia numa delas -- num teste com aquecimento de 12
    min, pausa de 3 min e 4x(3 ON / 2 OFF), a ultima pausa qualificada era
    uma recuperacao de 2 min e o corte cortava metade do protocolo.

    Exigir dois ON depois evita o caso simetrico: uma pausa longa seguida
    de um unico esforco e' o fim da sessao, nao o inicio do protocolo.
    """
    if not blocos_info.get('ok'):
        return {'ok': False, 'motivo': blocos_info.get('motivo')}
    bl = blocos_info['blocos']

    # O tempo de descanso NAO e' fixo, e nao pode ser: varia de sessao para
    # sessao e de protocolo para protocolo. Por isso o limite minimo sai da
    # propria sessao -- 1.5x a mediana das pausas dela. Numa sessao com
    # recuperacoes de 2 min, so' pausas acima de 3 min contam como
    # separador; numa com recuperacoes de 30 s, bastam 45 s.
    #
    # O valor absoluto so' entra como piso, para nao aceitar micro-pausas
    # numa sessao inteira sem interrupcoes.
    offs = sorted(b['duracao_s'] for b in bl if not b['on'])
    if off_minimo is None:
        if offs:
            med = offs[len(offs) // 2]
            off_minimo = max(45.0, med * 1.5)
        else:
            off_minimo = 90.0

    candidatos = []
    for k, b in enumerate(bl):
        if b['on'] or b['duracao_s'] < off_minimo:
            continue
        ons_depois = [x for x in bl[k + 1:] if x['on']]
        if len(ons_depois) >= 2:
            candidatos.append((b['duracao_s'], k, b, ons_depois))
    if candidatos:
        dur, k, b, ons_depois = max(candidatos, key=lambda c: c[0])
        outras = sorted(c[0] for c in candidatos if c[1] != k)
        return {'ok': True,
                'inicio_s': round(ons_depois[0]['t0'], 1),
                'fim_s': round(bl[-1]['t1'], 1),
                'pausa_antes_s': b['duracao_s'],
                'n_blocos_on': len(ons_depois),
                'outras_pausas_s': outras,
                'off_minimo_usado_s': round(off_minimo, 1),
                'mediana_das_pausas_s': (round(offs[len(offs) // 2], 1)
                                         if offs else None),
                'confianca': ('alta' if not outras or dur > max(outras) * 1.4
                              else 'baixa'),
                'motivo': (f'pausa mais longa ({round(dur)} s) seguida de '
                           f'{len(ons_depois)} blocos de trabalho'
                           + (f'; outras pausas de {outras} s'
                              if outras else ''))}
    ons = [x for x in bl if x['on']]
    if ons:
        return {'ok': True, 'inicio_s': round(ons[0]['t0'], 1),
                'fim_s': round(bl[-1]['t1'], 1),
                'n_blocos_on': len(ons),
                'motivo': ('sem pausa longa a separar aquecimento de '
                           'protocolo; proposto o primeiro bloco de trabalho')}
    return {'ok': False, 'motivo': 'nenhum bloco de trabalho detectado'}


def resumir_degraus(tempo, canais, blocos_info, so_on=True, aparar=30):
    """Media de cada canal em cada bloco de trabalho.

    'aparar' descarta os primeiros N segundos de cada bloco: o SmO2 leva
    tempo a responder a uma mudanca de carga, e incluir a transicao mistura
    o degrau novo com o anterior. Trinta segundos e' o compromisso habitual
    -- suficiente para a resposta assentar sem gastar metade de um bloco de
    tres minutos.
    """
    if not blocos_info.get('ok'):
        return []
    out = []
    n_on = 0
    for b in blocos_info['blocos']:
        if so_on and not b['on']:
            continue
        n_on += 1
        i0 = min(b['i0'] + int(aparar), b['i1'])
        if i0 >= b['i1']:
            i0 = b['i0']
        linha = {'degrau': n_on, 'i0': i0, 'i1': b['i1'],
                 't0': b['t0'], 't1': b['t1'],
                 'duracao_s': b['duracao_s'], 'on': b['on'],
                 'n_pontos': b['i1'] - i0 + 1}
        for nome, serie in (canais or {}).items():
            jan = [serie[k] for k in range(i0, min(b['i1'] + 1, len(serie)))
                   if serie[k] is not None]
            linha[nome] = round(sum(jan) / len(jan), 2) if jan else None
        out.append(linha)
    return out


def emparelhar_degraus(sessoes, tolerancia=15.0, campo='watts'):
    """Junta degraus de varias sessoes que estejam a potencias parecidas.

    Alinhar por TEMPO exigiria sincronizar sessoes com aquecimentos
    diferentes e transicoes em momentos diferentes. Alinhar por POTENCIA
    dispensa isso: o degrau de 200 W de uma sessao compara-se com o de
    200 W da outra, seja quando for que tenha acontecido.

    A tolerancia existe porque no remo e no ski nao ha ergmode: o mesmo
    degrau alvo sai com potencias ligeiramente diferentes de dia para dia.
    """
    todos = []
    for idx, degraus in enumerate(sessoes):
        for d in degraus:
            if d.get(campo) is not None:
                todos.append((d[campo], idx, d))
    if not todos:
        return []
    todos.sort()

    grupos, actual = [], [todos[0]]
    for item in todos[1:]:
        if abs(item[0] - actual[0][0]) <= tolerancia:
            actual.append(item)
        else:
            grupos.append(actual)
            actual = [item]
    grupos.append(actual)

    saida = []
    for gr in grupos:
        vals = [x[0] for x in gr]
        linha = {'watts_centro': round(sum(vals) / len(vals), 1),
                 'watts_min': round(min(vals), 1),
                 'watts_max': round(max(vals), 1),
                 'n_sessoes': len({x[1] for x in gr}),
                 'por_sessao': {}}
        for w, idx, d in gr:
            linha['por_sessao'][str(idx)] = d
        saida.append(linha)
    return saida


# ══════════════════════════════════════════════════════════════════════════
# BLOCOS A PARTIR DOS LAPS DA INTERVALS.ICU
#
# Muito mais fiavel do que deduzir os blocos da potencia: os laps sao a
# estrutura que o atleta marcou, com tipo WORK ou RECOVERY ja' atribuido.
#
# Com uma ressalva importante, observada nos dados deste atleta: um lap
# marcado como RECOVERY pode trazer potencia media alta -- 22m58s de
# aquecimento com 174 W de media aparece como Recovery. O tipo do lap e' de
# confianca; a potencia media dele nao e'. Por isso o tipo manda, e a
# potencia so' serve para descrever.
# ══════════════════════════════════════════════════════════════════════════

def blocos_de_laps(laps, tempo_inicial=0.0):
    """Blocos ON/OFF a partir dos laps. laps: lista da API."""
    fora = []
    for lp in (laps or []):
        if not isinstance(lp, dict):
            continue
        t0 = lp.get('start_time')
        t1 = lp.get('end_time')
        if t0 is None or t1 is None:
            continue
        tipo = str(lp.get('type') or '').upper()
        fora.append({
            'on': tipo.startswith('WORK'),
            'tipo': tipo or None,
            't0': float(t0) - tempo_inicial,
            't1': float(t1) - tempo_inicial,
            'duracao_s': round(float(t1) - float(t0), 1),
            'watts_medio': lp.get('average_watts'),
            'hr_medio': lp.get('average_heartrate'),
            'label': lp.get('label') or lp.get('name'),
        })
    fora.sort(key=lambda b: b['t0'])
    return {'ok': bool(fora), 'fonte': 'laps da Intervals.icu',
            'blocos': fora,
            'n_on': sum(1 for b in fora if b['on']),
            'n_off': sum(1 for b in fora if not b['on']),
            'motivo': None if fora else 'sem laps utilizaveis'}


def propor_corte_laps(blocos_info, min_work_depois=2):
    """Fim do aquecimento = ultima RECOVERY longa antes de uma serie.

    Longa em relacao as outras: usa-se a mediana das recuperacoes da
    propria sessao vezes 1.5, com piso de 45 s, pela mesma razao de sempre
    -- o descanso varia de protocolo para protocolo e um numero fixo falha
    metade das vezes.
    """
    if not blocos_info.get('ok'):
        return {'ok': False, 'motivo': blocos_info.get('motivo')}
    bl = blocos_info['blocos']
    offs = sorted(b['duracao_s'] for b in bl if not b['on'])
    minimo = max(45.0, (offs[len(offs) // 2] * 1.5) if offs else 90.0)

    candidatos = []
    for k, b in enumerate(bl):
        if b['on'] or b['duracao_s'] < minimo:
            continue
        works = [x for x in bl[k + 1:] if x['on']]
        if len(works) >= min_work_depois:
            candidatos.append((b['duracao_s'], k, b, works))
    if not candidatos:
        works = [b for b in bl if b['on']]
        if not works:
            return {'ok': False, 'motivo': 'nenhum lap de trabalho'}
        return {'ok': True, 'fonte': 'laps',
                'inicio_s': round(works[0]['t0'], 1),
                'fim_s': round(bl[-1]['t1'], 1),
                'n_blocos_on': len(works), 'confianca': 'baixa',
                'motivo': ('sem recuperacao longa a separar aquecimento; '
                           'proposto o primeiro lap de trabalho')}
    dur, k, b, works = max(candidatos, key=lambda c: c[0])
    outras = sorted(c[0] for c in candidatos if c[1] != k)
    return {
        'ok': True, 'fonte': 'laps',
        'inicio_s': round(works[0]['t0'], 1),
        'fim_s': round(bl[-1]['t1'], 1),
        'pausa_antes_s': dur,
        'watts_da_pausa': b.get('watts_medio'),
        'n_blocos_on': len(works),
        'off_minimo_usado_s': round(minimo, 1),
        'confianca': ('alta' if not outras or dur > max(outras) * 1.4
                      else 'baixa'),
        'motivo': (f'lap de recuperacao mais longo ({round(dur)} s'
                   + (f', {round(b["watts_medio"])} W medios'
                      if b.get('watts_medio') else '')
                   + f') seguido de {len(works)} laps de trabalho'),
        'aviso': ('a potencia media de um lap de recuperacao pode vir alta '
                  'por erro de gravacao; o que conta e o tipo do lap'),
    }


# ══════════════════════════════════════════════════════════════════════════
# BLOCOS A PARTIR DOS LAPS DA INTERVALS.ICU
#
# Melhor fonte do que o agrupamento por potencia: os laps ja' estao
# marcados como WORK ou RECOVERY, e o aquecimento aparece como um RECOVERY
# longo antes da sequencia alternada.
#
# NAO se usa a potencia para classificar. Nos dados deste atleta ha laps
# marcados RECOVERY com potencia media de 174 W -- erro de gravacao do
# Garmin. Se o tipo diz recuperacao, e' recuperacao; a potencia media
# desse lap serve para mostrar, nao para decidir.
# ══════════════════════════════════════════════════════════════════════════

TIPOS_TRABALHO = {'WORK', 'work', 'Work'}


def blocos_de_laps(laps, tempo=None):
    """Blocos ON/OFF a partir dos intervalos da Intervals.icu."""
    if not laps:
        return {'ok': False, 'motivo': 'sem laps na actividade'}
    blocos = []
    for lp in laps:
        if not isinstance(lp, dict):
            continue
        t0 = lp.get('start_time')
        t1 = lp.get('end_time')
        if t0 is None or t1 is None:
            continue
        tipo = str(lp.get('type') or '')
        blocos.append({
            'on': tipo in TIPOS_TRABALHO,
            'tipo': tipo or '?',
            't0': float(t0), 't1': float(t1),
            'duracao_s': round(float(t1) - float(t0), 1),
            'watts_medio': lp.get('average_watts'),
            'hr_medio': lp.get('average_heartrate'),
            'nome': lp.get('label') or lp.get('name'),
        })
    if not blocos:
        return {'ok': False, 'motivo': 'laps sem indices de tempo'}
    blocos.sort(key=lambda b: b['t0'])
    return {'ok': True, 'fonte': 'laps da Intervals.icu',
            'blocos': blocos,
            'n_on': sum(1 for b in blocos if b['on']),
            'n_off': sum(1 for b in blocos if not b['on']),
            'nota': ('tipo do lap decide o que e trabalho, nao a potencia: '
                     'ha laps marcados RECOVERY com 174 W de media por erro '
                     'de gravacao')}


def propor_corte_laps(blocos_info):
    """Inicio do protocolo: apos o RECOVERY longo que precede a sequencia.

    E' o padrao que o atleta descreve: aquecimento continuo, uma pausa
    grande, e depois ON/OFF a alternar. O aquecimento aparece como um
    unico RECOVERY muito mais longo do que as recuperacoes entre series.
    """
    if not blocos_info.get('ok'):
        return {'ok': False, 'motivo': blocos_info.get('motivo')}
    bl = blocos_info['blocos']
    offs = sorted(b['duracao_s'] for b in bl if not b['on'])
    if not offs:
        ons = [b for b in bl if b['on']]
        if not ons:
            return {'ok': False, 'motivo': 'nenhum lap de trabalho'}
        return {'ok': True, 'inicio_s': ons[0]['t0'], 'fim_s': bl[-1]['t1'],
                'n_blocos_on': len(ons), 'fonte': 'laps',
                'motivo': 'sem laps de recuperacao; primeiro lap de trabalho'}
    mediana = offs[len(offs) // 2]
    candidatos = []
    for k, b in enumerate(bl):
        if b['on'] or b['duracao_s'] < max(60.0, mediana * 1.5):
            continue
        ons_depois = [x for x in bl[k + 1:] if x['on']]
        if len(ons_depois) >= 2:
            candidatos.append((b['duracao_s'], k, b, ons_depois))
    if not candidatos:
        ons = [b for b in bl if b['on']]
        if not ons:
            return {'ok': False, 'motivo': 'nenhum lap de trabalho'}
        return {'ok': True, 'inicio_s': ons[0]['t0'], 'fim_s': bl[-1]['t1'],
                'n_blocos_on': len(ons), 'fonte': 'laps',
                'confianca': 'baixa',
                'motivo': ('nenhuma recuperacao se destaca das outras; '
                           'proposto o primeiro lap de trabalho')}
    dur, k, b, ons_depois = max(candidatos, key=lambda c: c[0])
    outras = sorted(c[0] for c in candidatos if c[1] != k)
    return {'ok': True, 'fonte': 'laps',
            'inicio_s': ons_depois[0]['t0'], 'fim_s': bl[-1]['t1'],
            'pausa_antes_s': dur, 'n_blocos_on': len(ons_depois),
            'outras_pausas_s': outras,
            'mediana_das_pausas_s': round(mediana, 1),
            'confianca': ('alta' if not outras or dur > max(outras) * 1.4
                          else 'baixa'),
            'motivo': (f'recuperacao de {round(dur)} s marcada nos laps, '
                       f'seguida de {len(ons_depois)} laps de trabalho')}


# ══════════════════════════════════════════════════════════════════════════
# QUE TIPO DE SESSAO FOI ESTA
#
# O metodo de analise depende do protocolo, e ate' agora era o utilizador
# que tinha de saber qual era. Isto le' os laps e diz.
#
# A classificacao corre SEMPRE depois do corte do aquecimento. Os degraus
# leves do inicio, em bicicleta e remo, sao aquecimento e nao fazem parte
# do protocolo -- inclui-los fazia uma escada de 5 degraus parecer uma de
# 8, com os tres primeiros a nao encaixar em padrao nenhum.
#
# O que distingue os tipos:
#
#   ESCADA          a carga sobe de forma monotona entre blocos. E' o
#                   5-1-5 e o teste de degraus. So' aqui fazem sentido
#                   os breakpoints.
#   INTERVALADO     a carga repete-se. Duracoes iguais = por tempo;
#                   distancias iguais = por distancia (comum no remo, no
#                   esqui e na corrida).
#   DESCANSO VARIAVEL  trabalho constante mas recuperacoes de duracoes
#                   muito diferentes -- 5/4:30 e depois 5/8. Nao invalida
#                   a analise, mas o SmO2 de partida de cada bloco deixa
#                   de ser comparavel.
#   CONTINUO        sem blocos, ou um bloco so'.
# ══════════════════════════════════════════════════════════════════════════

# Coeficiente de variacao abaixo do qual se considera "constante". Nao e'
# um valor de literatura: e' o ponto a partir do qual a variacao deixa de
# ser execucao e passa a ser intencao. 8% num bloco de 5 min sao 24 s.
CV_CONSTANTE = 0.08
# Subida minima de carga entre o primeiro e o ultimo bloco para ser escada.
SUBIDA_ESCADA = 0.15


def _cv(vs):
    vs = [v for v in vs if v is not None]
    if len(vs) < 2:
        return None
    m = sum(vs) / len(vs)
    if m == 0:
        return None
    dp = (sum((v - m) ** 2 for v in vs) / len(vs)) ** 0.5
    return dp / abs(m)


def _monotona(vs, tol=0.03):
    """A serie sobe de forma monotona, tolerando pequenos recuos?"""
    vs = [v for v in vs if v is not None]
    if len(vs) < 3:
        return False
    recuos = 0
    for i in range(1, len(vs)):
        if vs[i] < vs[i - 1] * (1 - tol):
            recuos += 1
    return recuos <= max(0, len(vs) // 5)


def classificar_sessao(blocos, corte=None, laps=None):
    """Tipo de protocolo, a partir dos blocos JÁ CORTADOS.

    corte: (inicio_s, fim_s) — se dado, só os blocos dentro entram.
    laps: laps originais, para ler distância quando existe.
    """
    bl = list(blocos or [])
    fora_do_corte = 0
    if corte:
        a, b = corte
        antes = len(bl)
        bl = [x for x in bl if x.get('t1', 0) >= a and x.get('t0', 0) <= b]
        fora_do_corte = antes - len(bl)

    ons = [x for x in bl if x.get('on')]
    offs = [x for x in bl if not x.get('on')]

    if len(ons) < 2:
        return {'ok': True, 'tipo': 'contínuo',
                'n_blocos_trabalho': len(ons),
                'descricao': ('menos de dois blocos de trabalho: é uma '
                              'sessão contínua ou só um esforço'),
                'serve_para_breakpoints': False,
                'blocos_fora_do_corte': fora_do_corte}

    dur_on = [x['t1'] - x['t0'] for x in ons]
    dur_off = [x['t1'] - x['t0'] for x in offs]
    watts = [x.get('watts_medio') for x in ons]
    dist = None
    if laps:
        d = [lp.get('distance') for lp in laps
             if isinstance(lp, dict) and str(lp.get('type', '')).upper() == 'WORK'
             and lp.get('distance')]
        if len(d) >= len(ons) * 0.8:
            dist = d

    cv_dur = _cv(dur_on)
    cv_off = _cv(dur_off)
    cv_w = _cv(watts)
    cv_dist = _cv(dist) if dist else None

    ws = [w for w in watts if w is not None]
    subida = ((ws[-1] - ws[0]) / ws[0]) if len(ws) >= 2 and ws[0] else None
    escada = (_monotona(ws) and subida is not None
              and subida >= SUBIDA_ESCADA)

    por_distancia = cv_dist is not None and cv_dist < CV_CONSTANTE
    dur_constante = cv_dur is not None and cv_dur < CV_CONSTANTE
    off_constante = cv_off is not None and cv_off < CV_CONSTANTE

    if escada:
        tipo = 'escada (teste de degraus)'
        desc = (f'a carga sobe {round(subida * 100)}% do primeiro ao último '
                f'bloco, de forma monótona. É o formato do 5-1-5 e do teste '
                'de degraus')
        serve = True
    elif por_distancia:
        media_m = sum(dist) / len(dist)
        tipo = 'intervalado por distância'
        desc = (f'{len(ons)} repetições de ~{round(media_m)} m. Formato '
                'comum no remo, esqui e corrida: a distância é fixa e a '
                'duração varia com o ritmo')
        serve = False
    elif dur_constante and off_constante:
        tipo = 'intervalado por tempo'
        desc = (f'{len(ons)} repetições de {round(sum(dur_on) / len(dur_on))} s '
                f'com {round(sum(dur_off) / len(dur_off))} s de recuperação, '
                'ambos constantes')
        serve = False
    elif dur_constante and not off_constante:
        tipo = 'intervalado com descanso variável'
        desc = (f'trabalho constante ({round(sum(dur_on) / len(dur_on))} s) '
                f'mas recuperações de {round(min(dur_off))} a '
                f'{round(max(dur_off))} s. O SmO2 de partida de cada bloco '
                'não é comparável entre repetições')
        serve = False
    else:
        tipo = 'intervalado irregular'
        desc = ('nem a duração do trabalho nem a da recuperação são '
                'constantes, e a carga não sobe de forma monótona')
        serve = False

    return {
        'ok': True,
        'tipo': tipo,
        'descricao': desc,
        'serve_para_breakpoints': serve,
        'porque': (
            'os breakpoints precisam de cargas diferentes para traçar a '
            'curva SmO2 × potência. Num intervalado a carga repete-se, e '
            'não há curva para ajustar — o que se pode ler é a resposta '
            'ao esforço repetido, não um limiar'
            if not serve else
            'a carga varia entre blocos, o que permite traçar a curva '
            'SmO2 × potência e procurar as quebras'),
        'n_blocos_trabalho': len(ons),
        'n_recuperacoes': len(offs),
        'blocos_fora_do_corte': fora_do_corte,
        'trabalho': {
            'duracao_media_s': round(sum(dur_on) / len(dur_on)),
            'duracao_min_s': round(min(dur_on)),
            'duracao_max_s': round(max(dur_on)),
            'cv': round(cv_dur, 3) if cv_dur is not None else None,
            'constante': dur_constante,
        },
        'recuperacao': ({
            'duracao_media_s': round(sum(dur_off) / len(dur_off)),
            'duracao_min_s': round(min(dur_off)),
            'duracao_max_s': round(max(dur_off)),
            'cv': round(cv_off, 3) if cv_off is not None else None,
            'constante': off_constante,
        } if dur_off else None),
        'carga': {
            'watts': [round(w) if w is not None else None for w in watts],
            'cv': round(cv_w, 3) if cv_w is not None else None,
            'subida_pct': round(subida * 100, 1) if subida is not None else None,
            'monotona': _monotona(ws),
        },
        'distancia': ({'media_m': round(sum(dist) / len(dist)),
                       'cv': round(cv_dist, 3)} if dist else None),
        'criterio': {'cv_constante': CV_CONSTANTE,
                     'subida_minima_escada': SUBIDA_ESCADA},
        'nota': ('classificado DEPOIS do corte do aquecimento. Incluir os '
                 'degraus leves do início faria uma escada de 5 degraus '
                 'parecer uma de 8, com os primeiros sem encaixar em '
                 'padrão nenhum'),
    }
