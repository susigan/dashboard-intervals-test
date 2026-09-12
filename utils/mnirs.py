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
        # A média do bloco INTEIRO fica puxada para cima quando o atleta
        # empurra mais força no arranque para lá chegar à potência alvo
        # (comum sem modo ERG, onde a resistência não é controlada
        # electronicamente). Ignora-se o arranque -- até 20 s ou 15% do
        # bloco, o que for menor -- e usa-se a média do estado estável a
        # seguir, que é o que representa de facto o degrau.
        n = len(jan)
        corte = min(int(n * 0.15), 20) if n > 10 else 0
        jan_estavel = jan[corte:] if corte < n else jan
        b['watts_medio'] = (round(sum(jan_estavel) / len(jan_estavel), 1)
                            if jan_estavel else None)
        b['watts_medio_bloco_inteiro'] = (round(sum(jan) / len(jan), 1)
                                          if jan else None)

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

def blocos_de_laps(laps, tempo_inicial=0.0, watts_stream=None,
                   tempo_stream=None):
    """Blocos ON/OFF a partir dos laps. laps: lista da API.

    watts_medio vinha SEMPRE de lp.get('average_watts') — a média que a
    Intervals.icu já calculou para o lap inteiro. Sem modo ERG, o atleta
    empurra mais força no arranque do bloco para lá chegar à potência
    alvo, e essa ponta puxa a média do lap inteiro para cima. Quando
    watts_stream e tempo_stream são dados, recalcula-se a média em ESTADO
    ESTÁVEL a partir do sinal em bruto — ignorando os primeiros segundos
    do bloco — e usa-se essa em vez da da API. Sem os streams, mantém-se
    o valor da API como antes.
    """
    fora = []
    for lp in (laps or []):
        if not isinstance(lp, dict):
            continue
        t0 = lp.get('start_time')
        t1 = lp.get('end_time')
        if t0 is None or t1 is None:
            continue
        tipo = str(lp.get('type') or '').upper()
        watts_medio = lp.get('average_watts')
        watts_medio_bloco_inteiro = watts_medio
        if watts_stream and tempo_stream and tipo.startswith('WORK'):
            vs = [watts_stream[i] for i in range(min(len(tempo_stream),
                                                      len(watts_stream)))
                  if t0 <= tempo_stream[i] <= t1
                  and watts_stream[i] is not None]
            if len(vs) > 10:
                watts_medio_bloco_inteiro = round(sum(vs) / len(vs), 1)
                n = len(vs)
                corte = min(int(n * 0.15), 20)
                vs_estavel = vs[corte:] if corte < n else vs
                if vs_estavel:
                    watts_medio = round(sum(vs_estavel) / len(vs_estavel), 1)
        fora.append({
            'on': tipo.startswith('WORK'),
            'tipo': tipo or None,
            't0': float(t0) - tempo_inicial,
            't1': float(t1) - tempo_inicial,
            'duracao_s': round(float(t1) - float(t0), 1),
            'watts_medio': watts_medio,
            'watts_medio_bloco_inteiro': watts_medio_bloco_inteiro,
            'watts_medio_da_api': lp.get('average_watts'),
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

# ══════════════════════════════════════════════════════════════════════════
# CLASSIFICAR O TIPO DE SESSÃO — constantes
#
# Estas duas foram apagadas por engano numa limpeza anterior (a remover um
# bloco duplicado de propor_corte_laps, a faixa de linhas escolhida
# também levou estas duas constantes, que estavam no meio). Ficam aqui
# restauradas com o mesmo critério original.
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

    # 'min() iterable argument is empty': acontecia quando nao havia
    # blocos ON. Sai-se antes de calcular qualquer minimo.
    if not ons:
        return {'ok': True, 'tipo': 'sem blocos de trabalho',
                'n_blocos_trabalho': 0,
                'descricao': ('nenhum bloco acima do limiar de potência: '
                              'sessão só de recuperação, ou sem potência'),
                'serve_para_breakpoints': False,
                'blocos_fora_do_corte': fora_do_corte}

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
    # Uma escada tem degraus de DURACAO PARECIDA. Sem esta condicao, uma
    # sessao como "2x 5m 167w | 8x 4m 213w" passava por escada só porque a
    # potencia sobe uma vez -- e sao 8 repeticoes a carga constante, que e'
    # um intervalado. Exige-se tambem que a carga suba em mais de um
    # degrau, nao apenas do primeiro para o resto.
    degraus_distintos = len({round(w / 5) for w in ws})
    escada = (_monotona(ws) and subida is not None
              and subida >= SUBIDA_ESCADA
              and (cv_dur is None or cv_dur < 0.35)
              and degraus_distintos >= 3)

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
        # dur_off pode estar VAZIO: um treino sem blocos de recuperacao
        # identificados cai aqui e o min() rebentava com
        # "min() iterable argument is empty". E' o erro que aparecia em
        # tres sessoes reais.
        tipo = 'intervalado com descanso variável' if dur_off else 'blocos repetidos'
        desc = ((f'trabalho constante ({round(sum(dur_on) / len(dur_on))} s) '
                 f'mas recuperações de {round(min(dur_off))} a '
                 f'{round(max(dur_off))} s. O SmO2 de partida de cada bloco '
                 'não é comparável entre repetições') if dur_off else
                (f'{len(ons)} blocos de '
                 f'{round(sum(dur_on) / len(dur_on))} s à mesma carga, sem '
                 'recuperações identificadas entre eles'))
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


# ══════════════════════════════════════════════════════════════════════════
# INTERVAL_SUMMARY — classificar sem chamar a API
#
# A Intervals.icu guarda no sumario de cada actividade uma lista de strings
# legiveis, uma por grupo de intervalos:
#
#     ["1x 5m21s 72w", "1x 5m2s 99w", ..., "3x 10m1s 215w"]
#
# Formato: REPETICOESx DURACAO POTENCIA. Esta em TODAS as 244 actividades
# do atleta, o que permite classificar tudo sem uma unica chamada a API --
# ir buscar os laps a serio custaria 244 chamadas.
#
# O QUE NAO TEM, e importa saber:
#   - a marca WORK/RECOVERY
#   - a distancia de cada bloco
#
# Sem WORK/RECOVERY, as recuperacoes tem de ser deduzidas pela potencia.
# Sem distancia, nao se distingue um intervalado por distancia de um por
# tempo. Sao os dois limites deste atalho, e estao assinalados no
# resultado -- para quem ler nao pensar que a classificacao aqui vale o
# mesmo que a feita com os laps.
# ══════════════════════════════════════════════════════════════════════════

import re as _re

_RE_ITEM = _re.compile(
    r'^\s*(\d+)\s*x\s+'                       # repeticoes
    r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?'      # duracao
    r'\s*(?:([\d.]+)\s*w)?',                  # potencia
    _re.IGNORECASE)


def ler_interval_summary(itens):
    """['1x 5m2s 99w', ...] -> lista de blocos com repeticoes, duracao e W."""
    fora = []
    for it in (itens or []):
        m = _RE_ITEM.match(str(it))
        if not m:
            fora.append({'texto': str(it), 'lido': False})
            continue
        rep = int(m.group(1) or 1)
        h, mi, sg = (int(m.group(i) or 0) for i in (2, 3, 4))
        dur = h * 3600 + mi * 60 + sg
        w = float(m.group(5)) if m.group(5) else None
        if dur <= 0:
            fora.append({'texto': str(it), 'lido': False,
                         'motivo': 'sem duração'})
            continue
        fora.append({'texto': str(it), 'lido': True, 'repeticoes': rep,
                     'duracao_s': dur, 'watts': w})
    return fora


def blocos_de_summary(itens, limiar_recuperacao=0.55):
    """Blocos ON/OFF a partir do interval_summary.

    Sem a marca WORK/RECOVERY, deduz-se pela potencia: blocos abaixo de
    'limiar_recuperacao' vezes a potencia mediana dos blocos altos contam
    como recuperacao. E' inferencia, nao leitura -- e por isso o resultado
    diz que a fonte foi o sumario e nao os laps.
    """
    lidos = [x for x in ler_interval_summary(itens) if x.get('lido')]
    if not lidos:
        return {'ok': False, 'motivo': 'nenhuma linha legível no summary'}

    ws = sorted(x['watts'] for x in lidos if x.get('watts'))
    if not ws:
        return {'ok': False, 'motivo': 'sem potência no summary'}
    p75 = ws[int(0.75 * (len(ws) - 1))]
    limiar = p75 * limiar_recuperacao

    blocos, t = [], 0.0
    for x in lidos:
        for _ in range(x['repeticoes']):
            w = x.get('watts')
            blocos.append({
                'on': (w is not None and w >= limiar),
                'tipo': 'inferido pela potência',
                't0': t, 't1': t + x['duracao_s'],
                'duracao_s': x['duracao_s'],
                'watts_medio': w,
                'texto': x['texto'],
            })
            t += x['duracao_s']

    return {'ok': True, 'fonte': 'interval_summary (sem chamada à API)',
            'blocos': blocos,
            'n_on': sum(1 for b in blocos if b['on']),
            'n_off': sum(1 for b in blocos if not b['on']),
            'limiar_w': round(limiar, 1),
            'limites': [
                'sem marca WORK/RECOVERY: o tipo é inferido pela potência',
                'sem distância: não se distingue intervalado por distância '
                'de intervalado por tempo',
                'os tempos são sequenciais e não reais — servem para durações '
                'e ordem, não para cruzar com streams'],
            'nota': ('classificação a partir do sumário guardado. Serve para '
                     'saber o formato da sessão; para análise de SmO2 é '
                     'preciso ir buscar os laps verdadeiros')}


def separar_aquecimento_summary(blocos, tol_duracao=0.35, subida_min=0.10,
                                max_degraus=8):
    """Separa a escada de aquecimento inicial do treino propriamente dito.

    O aquecimento deste atleta tem 5 degraus na bicicleta e 3 no remo e no
    esqui, com a potencia a subir e as duracoes parecidas.

    DUAS COISAS QUE PARTIAM A DETECCAO E JA' NAO PARTEM:

    1. RECUPERACOES INTERCALADAS. Numa sessao real:

           74w | 99w | 3x 61s 64w | 119w | 138w | 158w | 1h 132w

       o "3x 61s 64w" sao pausas entre degraus. A versao anterior exigia
       degraus consecutivos a subir e parava ali, deixando tres degraus de
       aquecimento a contar como treino. Agora os blocos curtos e de baixa
       potencia sao SALTADOS sem interromper a escada.

    2. DEGRAUS REPETIDOS. No remo:

           1x 5m5s 136w | 2x 5m 167w | 8x 4m 213w

       o "2x 5m 167w" e' um degrau contado duas vezes. A escada continua
       enquanto a potencia nao DESCE, em vez de exigir que suba sempre --
       senao o aquecimento ficava truncado e os 8x4min a 213 W passavam
       por escada.
    """
    bl = [b for b in (blocos or []) if b.get('watts_medio') is not None]
    if len(bl) < 3:
        return {'aquecimento': [], 'treino': list(blocos or []),
                'motivo': 'poucos blocos para separar'}

    durs = [b['duracao_s'] for b in bl]
    ws = [b['watts_medio'] for b in bl]
    dur_ref = durs[0]
    # potencia abaixo da qual um bloco curto conta como pausa, nao degrau
    lim_pausa = ws[0] * 0.9

    fim = 0
    ultimo_w = ws[0]
    repetidos = 0
    saltados = []
    for i in range(1, min(len(bl), max_degraus * 3)):
        curto = durs[i] < dur_ref * 0.5
        baixo = ws[i] < lim_pausa
        if curto and baixo:
            # pausa entre degraus: salta sem quebrar a escada
            saltados.append(i)
            fim = i
            continue
        d_ok = abs(durs[i] - dur_ref) <= dur_ref * tol_duracao
        # A escada tem de SUBIR para continuar. Aceitar "nao desce" fazia o
        # aquecimento engolir 8 blocos iguais a 213 W -- o "2x 5m 167w"
        # repetia o degrau e os 8x4min a seguir eram absorvidos, dando
        # "escada" a uma sessao que e' claramente um intervalado.
        #
        # Repeticoes do MESMO degrau sao toleradas (o 2x), mas so' uma vez
        # seguida: duas repeticoes iguais consecutivas terminam a escada.
        sobe = ws[i] > ultimo_w * 1.03
        igual = abs(ws[i] - ultimo_w) <= ultimo_w * 0.03
        if d_ok and sobe:
            fim = i
            ultimo_w = ws[i]
            repetidos = 0
        elif d_ok and igual and repetidos == 0:
            fim = i
            repetidos = 1
        else:
            break

    # tirar pausas finais do aquecimento
    while fim > 0 and fim in saltados:
        fim -= 1

    degraus = [k for k in range(fim + 1) if k not in saltados]
    if len(degraus) < 2:
        return {'aquecimento': [], 'treino': list(blocos or []),
                'motivo': 'sem escada inicial reconhecível'}

    if fim >= len(bl) - 1:
        return {'aquecimento': [], 'treino': list(blocos or []),
                'motivo': ('a progressão vai até ao fim da sessão: é mesmo '
                           'um teste de degraus, não aquecimento')}

    w_ini, w_fim = ws[degraus[0]], ws[degraus[-1]]
    subida = (w_fim - w_ini) / w_ini if w_ini else 0
    if subida < subida_min:
        return {'aquecimento': [], 'treino': list(blocos or []),
                'motivo': f'a escada inicial sobe só {round(subida * 100)}%'}

    aq = bl[:fim + 1]
    tr = bl[fim + 1:]
    return {
        'aquecimento': aq,
        'treino': tr,
        'n_aquecimento': len(aq),
        'n_degraus': len(degraus),
        'n_pausas_saltadas': len(saltados),
        'duracao_aquecimento_s': round(sum(b['duracao_s'] for b in aq)),
        'watts_aquecimento': [round(ws[k]) for k in degraus],
        'subida_pct': round(subida * 100),
        'motivo': (f'{len(degraus)} degraus de ~{round(dur_ref / 60)} min '
                   f'com a potência a subir {round(subida * 100)}% '
                   f'({round(w_ini)}→{round(w_fim)} W)'
                   + (f', com {len(saltados)} pausa(s) pelo meio'
                      if saltados else '')),
    }


def classificar_de_summary(itens, separar_aquecimento=True):
    """interval_summary -> tipo de sessão, com o aquecimento separado."""
    b = blocos_de_summary(itens)
    if not b.get('ok'):
        return {'ok': False, 'motivo': b.get('motivo')}

    sep = ({'aquecimento': [], 'treino': b['blocos'], 'motivo': 'não pedido'}
           if not separar_aquecimento
           else separar_aquecimento_summary(b['blocos']))

    alvo = sep['treino'] or b['blocos']
    c = classificar_sessao(alvo)
    c['aquecimento'] = {
        'n_blocos': len(sep['aquecimento']),
        'duracao_s': sep.get('duracao_aquecimento_s'),
        'watts': sep.get('watts_aquecimento'),
        'motivo': sep.get('motivo'),
    }
    c['fonte'] = b['fonte']
    c['limites'] = b['limites']
    c['blocos_do_treino'] = [
        {'duracao_s': x['duracao_s'], 'watts': round(x['watts_medio'])
         if x.get('watts_medio') else None, 'texto': x.get('texto')}
        for x in alvo]
    return {'ok': True, **c}


# ══════════════════════════════════════════════════════════════════════════
# DEOXY-HEMOGLOBINA — o canal preferido para breakpoints
#
# O Moxy nao da' HHb directamente, mas ele sai do SmO2 e do THb:
#
#     HHb = ((100 - SmO2) / 100) x THb
#
# (Zurbuchen 2020, via Peikon.)
#
# PORQUE E' PREFERIDO, e nao mais um canal derivado como os outros:
#
# Ja' vimos que o O2Hb e o DiffHb ficam sobrepostos ao SmO2 quando o THb
# quase nao varia -- sao transformacoes lineares dele. O HHb NAO e' um
# deles. Repare-se:
#
#     O2Hb   = SmO2/100 x THb          sobe quando o SmO2 sobe
#     HHb    = (1 - SmO2/100) x THb    sobe quando o SmO2 DESCE
#
# O HHb mede a hemoglobina que JA' LARGOU o oxigenio, ou seja a
# extraccao. E' por isso que a literatura o prefere:
#
#   "we'll want to use the deoxy-hemoglobin measurements (...) since they
#    are LESS AFFECTED BY BLOOD VOLUME under the NIRS probe"
#
# Continua a depender do THb, portanto nao e' independente do SmO2 -- mas
# responde as duas coisas (saturacao e volume) na direccao que interessa
# para ler extraccao, e e' o canal em que os breakpoints da literatura
# foram descritos.
#
# E ha um achado que o torna especialmente util aqui:
#
#   "the deoxy-hemoglobin breakpoint and Fatmax (...) both occur at the
#    SAME PERCENTAGE of VO2peak"
#
# Ou seja: o breakpoint do HHb aproxima o FatMax, o que da' uma segunda
# via para o primeiro limiar.
# ══════════════════════════════════════════════════════════════════════════

def calcular_hhb(smo2, thb):
    """HHb = ((100 - SmO2)/100) x THb, ponto a ponto."""
    if not smo2 or not thb:
        return None
    n = min(len(smo2), len(thb))
    fora = []
    validos = 0
    for i in range(n):
        s, t = smo2[i], thb[i]
        if s is None or t is None:
            fora.append(None)
            continue
        fora.append(round((100.0 - float(s)) / 100.0 * float(t), 4))
        validos += 1
    if validos < n * 0.5:
        return None
    return fora


def hhb_disponivel(canais):
    """Acrescenta 'hhb_calc' quando ha SmO2 e THb e nao ha HHb medido."""
    if not canais:
        return None, 'sem canais'
    # se o sensor ja' deu HHb, usa-se esse
    for k in canais:
        if k.lower() in ('hhb', 'hhb_1', 'deoxyhb'):
            return k, 'canal do sensor'
    sm = next((k for k in canais if k.lower().startswith('smo2')), None)
    th = next((k for k in canais if k.lower().startswith('thb')), None)
    if not sm or not th:
        return None, 'sem SmO2 e THb para o calcular'
    v = calcular_hhb(canais[sm], canais[th])
    if v is None:
        return None, 'poucos pontos válidos'
    canais['hhb_calc'] = v
    return 'hhb_calc', 'calculado de SmO2 e THb'


# ══════════════════════════════════════════════════════════════════════════
# CANAIS CONGELADOS — quando o sensor trava
#
# Uma cinta que trava nao devolve buracos: devolve o MESMO valor repetido.
# A FC fica em 140 bpm durante meia hora, e todos os calculos a jusante
# aceitam isso como uma medicao valida.
#
# E arrasta os outros: a FC, o DFA-a1 e a frequencia respiratoria saem
# todos da MESMA serie de intervalos RR. Se a cinta congela, os tres
# congelam juntos -- mas o DFA-a1 e a respiracao continuam a produzir
# numeros com aspecto normal, porque sao derivados de uma serie constante.
#
# Detectar isto e' barato: um sinal fisiologico real nunca fica
# exactamente igual durante minutos. O que se faz a seguir e' que importa:
# em vez de "corrigir", APAGA-SE o troco. Um valor nulo diz "nao sei"; um
# valor inventado diz "sei, e e' isto" -- e essa e' a mentira que custa
# caro quando alimenta um limiar.
# ══════════════════════════════════════════════════════════════════════════

# Segundos com o valor exactamente igual a partir dos quais se considera
# congelado. A FC varia batimento a batimento; 45 s idênticos não é
# fisiologia, é o sensor preso.
CONGELADO_S = 45

# Canais que partilham a origem: todos vêm da série de RR da cinta. Se um
# congela, os outros herdam o problema.
FAMILIA_RR = ('heartrate', 'dfa_a1', 'respiration', 'rra1', 'hrv',
              'respirationratealphahrv', 'meanrra1')


def _troços_congelados(vs, tempo=None, minimo_s=CONGELADO_S):
    """Devolve [(i_inicio, i_fim)] dos troços com valor repetido."""
    if not vs:
        return []
    fora, i = [], 0
    n = len(vs)
    while i < n:
        if vs[i] is None:
            i += 1
            continue
        j = i
        while j + 1 < n and vs[j + 1] == vs[i]:
            j += 1
        dur = ((tempo[j] - tempo[i]) if tempo and j < len(tempo)
               else (j - i))
        if dur >= minimo_s and j > i:
            fora.append((i, j))
        i = j + 1
    return fora


def detectar_congelados(canais, tempo=None, minimo_s=CONGELADO_S,
                        apagar=True):
    """Apaga os troços congelados e propaga à família de RR.

    Não interpola nem corrige: põe a None. Um nulo é honesto, um valor
    inventado não.
    """
    if not canais:
        return {'ok': False, 'motivo': 'sem canais'}

    relatorio, afectados = {}, []
    for k, v in list(canais.items()):
        tr = _troços_congelados(v, tempo, minimo_s)
        if not tr:
            continue
        n_pontos = sum(j - i + 1 for i, j in tr)
        pct = round(n_pontos / max(1, len(v)) * 100, 1)
        relatorio[k] = {
            'n_trocos': len(tr),
            'n_pontos': n_pontos,
            'pct_da_serie': pct,
            'primeiro_troco_s': (round(tempo[tr[0][0]])
                                 if tempo and tr[0][0] < len(tempo) else None),
            'valor_preso': v[tr[0][0]],
        }
        afectados.append(k)

    # propagar à família: se a cinta travou, o DFA-a1 e a respiração
    # dessa janela não valem nada, mesmo que os números variem
    da_familia = [k for k in afectados if k.lower() in FAMILIA_RR]
    propagou = []
    if da_familia:
        janelas = []
        for k in da_familia:
            janelas += _troços_congelados(canais[k], tempo, minimo_s)
        for k in canais:
            if k.lower() in FAMILIA_RR and k not in da_familia:
                propagou.append(k)

    if apagar:
        for k in afectados:
            for i, j in _troços_congelados(canais[k], tempo, minimo_s):
                for x in range(i, j + 1):
                    canais[k][x] = None
        for k in propagou:
            for i, j in janelas:
                for x in range(i, min(j + 1, len(canais[k]))):
                    canais[k][x] = None

    return {
        'ok': True,
        'canais_congelados': relatorio,
        'propagado_para': propagou,
        'apagado': apagar,
        'limiar_s': minimo_s,
        'nota': (
            'os troços congelados foram APAGADOS, não corrigidos. Um valor '
            'nulo diz "não sei"; um valor interpolado diz "sei, e é isto" — '
            'e essa é a mentira que custa caro quando alimenta um limiar'
            if afectados else None),
        'porque_propaga': (
            'a FC, o DFA-a1 e a respiração saem todos da mesma série de '
            'RR. Se a cinta congelou, os três estão comprometidos nessa '
            'janela — mesmo que os números do DFA-a1 continuem a variar'
            if propagou else None),
    }


def fc_utilizavel(congelados, artefactos=None, limiar_pct=40):
    """A FC desta sessão serve para alguma coisa?

    Se a cinta esteve presa 85% do tempo, o bpm que sai de qualquer
    análise é ficção. Melhor não mostrar nada do que mostrar um número.

    Devolve o veredicto uma vez, para todos os cálculos o consultarem em
    vez de cada um decidir por si.
    """
    r = (congelados or {}).get('canais_congelados') or {}
    hr = r.get('heartrate') or r.get('fixed_heartrate') or {}
    pct_congelado = hr.get('pct_da_serie') or 0
    pct_art = (artefactos or {}).get('pct_acima_do_limiar') or 0

    mau = pct_congelado >= limiar_pct or pct_art > 30
    motivos = []
    if pct_congelado >= limiar_pct:
        motivos.append(
            f'a cinta esteve presa em {hr.get("valor_preso")} durante '
            f'{pct_congelado}% da sessão')
    if pct_art > 30:
        motivos.append(f'{round(pct_art)}% de artefacto na série de RR')

    return {
        'utilizavel': not mau,
        'pct_congelado': pct_congelado,
        'pct_artefacto': pct_art,
        'motivos': motivos,
        'afecta': (['heartrate', 'dfa_a1', 'respiration'] if mau else []),
        'consequencia': (
            'os resultados em bpm desta sessão são descartados. Os watts '
            'não são afectados — vêm do medidor de potência, não da cinta. '
            'A análise continua, só sem a componente de frequência cardíaca'
            if mau else None),
        'porque_todos': (
            'a FC, o DFA-a1 e a respiração saem todos da mesma série de RR. '
            'Não faz sentido descartar um e manter os outros'
            if mau else None),
    }


def canais_invalidos(res):
    """Que canais NÃO são de confiança nesta sessão, e o que isso invalida.

    Serve para o resto do pipeline saber o que não pode usar. Sem isto, o
    congelamento é detectado, os dados são apagados — e depois um limiar
    em bpm calculado antes da limpeza é gravado na mesma.
    """
    cg = (res or {}).get('congelados') or {}
    art = (res or {}).get('artefactos') or {}

    maus, motivos = set(), {}

    for k, v in (cg.get('canais_congelados') or {}).items():
        # abaixo de 20% da sessão o troço apaga-se e o resto ainda serve
        if (v.get('pct_da_serie') or 0) >= 20:
            maus.add(k.lower())
            motivos[k.lower()] = (
                f"preso em {v.get('valor_preso')} durante "
                f"{v.get('pct_da_serie')}% da sessão")
    for k in (cg.get('propagado_para') or []):
        if any(m in maus for m in ('heartrate', 'hr')):
            maus.add(k.lower())
            motivos.setdefault(k.lower(),
                               'deriva da mesma série de RR que congelou')

    pct_art = art.get('pct_acima_do_limiar')
    if pct_art is not None and pct_art > 30:
        for k in FAMILIA_RR:
            maus.add(k)
            motivos.setdefault(k, f'{pct_art}% de artefacto na cinta')

    tem_fc = any(m in maus for m in ('heartrate', 'hr'))
    return {
        'canais': sorted(maus),
        'motivos': motivos,
        'fc_invalida': tem_fc,
        'usar_apenas_watts': tem_fc,
        'consequencia': (
            'a FC desta sessão não é de confiança: os limiares em bpm não '
            'devem ser calculados, gravados nem comparados. Os limiares em '
            'watts não são afectados — o SmO2 e a potência vêm de sensores '
            'diferentes'
            if tem_fc else None),
        'nota': ('canais com menos de 20% da série congelada não entram '
                 'aqui: o troço apaga-se e o resto continua utilizável'),
    }
