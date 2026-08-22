"""utils/hrv_limiares.py — HRVT1, HRVT2 e HRVT1c a partir dos streams.

O alphaHRV grava o DFA-a1 e a frequencia respiratoria no FIT, e a
Intervals.icu expoe-os como streams. Isso permite calcular os limiares aqui,
em vez de depender do script privado que devolve o HRVT1/HRVT2 sem se saber
o que faz.

Tres limiares:

  HRVT1s   a1 = 0.75. E' a convencao classica. Rogers et al. (2024) mostram
           que SOBRESTIMA o limiar aerobio, e o protocolo do forum diz o
           mesmo -- "will likely overestimate the threshold; treat with
           caution". Calcula-se para comparacao, nao para usar.

  HRVT2    a1 = 0.50. Corresponde ao ponto de compensacao respiratoria.

  HRVT1c   ponto medio entre o MAXIMO de a1 no inicio do esforco e 0.50.
           E' a correccao do Rogers: em vez de uma constante igual para
           todos, o alvo sai do proprio individuo. Encaixa no principio
           que este projecto segue em tudo o resto.

Nada aqui usa constantes de populacao a nao ser o 0.75 e o 0.50, que sao
definicoes do metodo e vao identificadas como tal.
"""

# Segundos iniciais que contam como "early ramp" para o maximo de a1.
# O paper e' vago entre 5 e 10 minutos; o protocolo do forum usa 300 s.
EARLY_RAMP_S = 300

A1_HRVT1_CLASSICO = 0.75
A1_HRVT2 = 0.50

# Um a1 acima disto e' quase de certeza artefacto: em repouso ronda 1.0-1.5
A1_MAX_PLAUSIVEL = 2.0


def _mediana(vs):
    vs = sorted(v for v in vs if v is not None)
    if not vs:
        return None
    n = len(vs)
    return vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2


def _suavizar(serie, janela=15):
    """Mediana movel. Mediana e nao media: o a1 tem picos isolados de
    artefacto e a media arrasta-os para os vizinhos."""
    n = len(serie)
    if n < janela:
        return list(serie)
    metade = janela // 2
    out = []
    for i in range(n):
        a, b = max(0, i - metade), min(n, i + metade + 1)
        vs = [v for v in serie[a:b] if v is not None]
        out.append(_mediana(vs) if vs else None)
    return out


def maximo_inicial(a1, early_s=EARLY_RAMP_S, hz=1.0):
    """Maximo de a1 no inicio, com filtro de 3 desvios-padrao.

    O filtro existe porque um unico ponto de artefacto no arranque -- e o
    arranque e' onde ha mais, com a cinta a assentar -- define sozinho o
    HRVT1c de toda a sessao se nao for removido.
    """
    n = int(early_s * hz)
    ini = [v for v in a1[:n] if v is not None and 0 < v <= A1_MAX_PLAUSIVEL]
    if len(ini) < 10:
        return None, {'motivo': f'so {len(ini)} pontos validos no inicio'}
    m = sum(ini) / len(ini)
    dp = (sum((v - m) ** 2 for v in ini) / len(ini)) ** 0.5
    limpos = [v for v in ini if abs(v - m) <= 3 * dp] or ini
    return max(limpos), {'n_pontos': len(ini), 'n_apos_filtro': len(limpos),
                         'media': round(m, 3), 'desvio': round(dp, 3),
                         'max_bruto': round(max(ini), 3)}


def perfil_a1_vs_intensidade(a1, canal, artefactos=None, n_bins=12,
                             art_max_pct=5.0):
    """Mediana de a1 por escalao de intensidade.

    Substitui a procura do cruzamento no TEMPO, que se mostrou errada nos
    dados reais. Numa sessao que nao e' uma rampa, o a1 sobe e desce dezenas
    de vezes e cruza o alvo a toda a hora -- apanhar "a ultima descida" da'
    um ponto arbitrario, e numa sessao apanhou a volta a calma: 236 W a
    91 bpm, que nao existe.

    Contra a intensidade o problema desaparece: nao interessa QUANDO o a1
    esteve baixo, interessa a QUE potencia. Cada escalao junta todos os
    momentos passados nessa potencia, esteja onde estiver na sessao.
    """
    pares = []
    for i in range(min(len(a1), len(canal))):
        v, p = a1[i], canal[i]
        if v is None or p is None or v <= 0 or v > A1_MAX_PLAUSIVEL or p <= 0:
            continue
        if artefactos is not None and i < len(artefactos):
            art = artefactos[i]
            if art is not None and art > art_max_pct:
                continue
        pares.append((float(p), float(v)))
    if len(pares) < 60:
        return None

    ps = sorted(p for p, _ in pares)
    lo, hi = ps[int(len(ps) * 0.05)], ps[int(len(ps) * 0.95)]
    if hi <= lo:
        return None
    largura = (hi - lo) / n_bins
    bins = {}
    for p, v in pares:
        if p < lo or p > hi:
            continue
        k = min(int((p - lo) / largura), n_bins - 1)
        bins.setdefault(k, []).append(v)

    escaloes = []
    for k in sorted(bins):
        vs = bins[k]
        if len(vs) < 15:
            continue
        escaloes.append({
            'centro': round(lo + largura * (k + 0.5), 1),
            'n': len(vs),
            'a1_mediana': round(_mediana(vs), 3),
        })
    return escaloes if len(escaloes) >= 4 else None


def intensidade_no_alvo(escaloes, alvo):
    """Interpola a intensidade a que o a1 mediano atinge o alvo.

    Percorre os escaloes por ordem de intensidade e apanha a passagem de
    cima para baixo. Se o a1 ja' esta abaixo do alvo no escalao mais baixo,
    o limiar fica abaixo do que foi treinado e nao ha nada a interpolar --
    devolve-se isso em vez de extrapolar.
    """
    if not escaloes or len(escaloes) < 2:
        return None
    if escaloes[0]['a1_mediana'] < alvo:
        return {'fora_do_intervalo': 'abaixo',
                'nota': (f"a1 ja' abaixo de {alvo} no escalao mais baixo "
                         f"({escaloes[0]['centro']}); o limiar esta abaixo "
                         'da intensidade minima desta sessao')}
    if escaloes[-1]['a1_mediana'] > alvo:
        return {'fora_do_intervalo': 'acima',
                'nota': (f"a1 nunca desceu abaixo de {alvo} (minimo "
                         f"{escaloes[-1]['a1_mediana']} a "
                         f"{escaloes[-1]['centro']}); a sessao nao chegou "
                         'ao limiar')}
    for i in range(1, len(escaloes)):
        a, b = escaloes[i - 1], escaloes[i]
        if a['a1_mediana'] >= alvo > b['a1_mediana']:
            f = ((a['a1_mediana'] - alvo)
                 / (a['a1_mediana'] - b['a1_mediana'] or 1))
            return {'intensidade': round(a['centro']
                                         + f * (b['centro'] - a['centro']), 1),
                    'entre': [a['centro'], b['centro']],
                    'n_pontos': a['n'] + b['n']}
    return None


def e_rampa(canal, n_bins=6):
    """A intensidade sobe ao longo da sessao?

    O HRVT1c precisa de um arranque facil seguido de subida: e' do maximo
    de a1 no inicio que sai o alvo. Numa sessao que comeca dura, esse
    maximo fica baixo e o alvo sai errado -- e' o "Unrealistic threshold"
    que o protocolo do forum avisa.
    """
    vs = [v for v in canal if v is not None and v > 0]
    if len(vs) < 120:
        return None
    tam = len(vs) // n_bins
    medias = [sum(vs[i * tam:(i + 1) * tam]) / tam for i in range(n_bins)]
    subidas = sum(1 for i in range(1, n_bins) if medias[i] > medias[i - 1])
    return {
        'medias_por_sexto': [round(m) for m in medias],
        'sextos_a_subir': subidas,
        'de': n_bins - 1,
        'primeiro_vs_ultimo_pct': round((medias[-1] / medias[0] - 1) * 100, 1)
                                  if medias[0] else None,
        'parece_rampa': subidas >= n_bins - 2 and medias[-1] > medias[0] * 1.25,
    }


def cruzamento(a1, alvo, canais, hz=1.0, suavizar=True):
    """DEPRECADO. Onde o a1 desce abaixo do alvo, ao longo do TEMPO.

    Nao usar. Falha em qualquer sessao que nao seja uma rampa limpa, e as
    sessoes reais nao sao. Nos dados deste atleta produziu HRVT1c, HRVT1s e
    HRVT2 separados por 36 segundos no fim de uma sessao de 24 minutos --
    o mesmo instante detectado tres vezes -- e, noutra, 236 W a 86 bpm, que
    e' a volta a calma.

    A causa e' conceptual, nao um afinamento: o limiar e' uma propriedade da
    INTENSIDADE, nao um instante. Ver limiar_por_regressao.
    """
    serie = _suavizar(a1) if suavizar else list(a1)
    idx = None
    for i in range(1, len(serie)):
        a, b = serie[i - 1], serie[i]
        if a is None or b is None:
            continue
        if a >= alvo > b:
            idx = i
    if idx is None:
        return None
    out = {'indice': idx, 'segundo': round(idx / hz, 1), 'a1_alvo': alvo}
    for nome, dados in (canais or {}).items():
        if not dados or idx >= len(dados):
            continue
        a = max(0, idx - int(30 * hz))
        vs = [v for v in dados[a:idx + 1] if v is not None]
        if vs:
            out[nome] = round(sum(vs) / len(vs), 1)
    return out


def _regressao(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    a = sxy / sxx
    b = my - a * mx
    syy = sum((y - my) ** 2 for y in ys)
    r2 = (sxy ** 2 / (sxx * syy)) if syy > 0 else 0.0
    return {'declive': a, 'intercepto': b, 'r2': r2, 'n': n}


def binar(pares, largura):
    """Mediana de a1 por escalao de intensidade.

    Sem isto, uma sessao passada quase toda a 200 W tem 90% dos pontos
    nessa potencia, e a regressao descreve o tempo passado em cada zona em
    vez da relacao entre intensidade e a1. Binar da o mesmo peso a cada
    nivel de intensidade, que e' o que interessa para um limiar. A mediana
    dentro do bin absorve os artefactos.
    """
    baldes = {}
    for x, y in pares:
        k = int(x // largura)
        baldes.setdefault(k, []).append(y)
    out = []
    for k in sorted(baldes):
        vs = baldes[k]
        if len(vs) < 5:            # bins com meia duzia de pontos sao ruido
            continue
        out.append({'centro': (k + 0.5) * largura, 'a1': _mediana(vs),
                    'n': len(vs)})
    return out


def limiar_por_regressao(pares, alvo, largura, rotulo):
    """Intensidade a que o a1 atinge o alvo, por regressao sobre os bins.

    O limiar sai da relacao a1 x intensidade, nao de um instante da sessao.
    Devolve sempre o r2 e o intervalo coberto: se o alvo cair fora do
    intervalo de intensidades que a sessao percorreu, o valor e'
    extrapolacao e vem marcado como tal.
    """
    bins = binar(pares, largura)
    if len(bins) < 3:
        return {'ok': False, 'motivo': f'so {len(bins)} escaloes de {rotulo}'}
    reg = _regressao([b['centro'] for b in bins], [b['a1'] for b in bins])
    if not reg or reg['declive'] >= 0:
        return {'ok': False,
                'motivo': ('a1 nao desce com a intensidade nesta sessao'
                           if reg else 'regressao impossivel'),
                'r2': round(reg['r2'], 3) if reg else None}
    valor = (alvo - reg['intercepto']) / reg['declive']
    lo = min(b['centro'] for b in bins)
    hi = max(b['centro'] for b in bins)
    return {'ok': True, 'valor': round(valor, 1), 'unidade': rotulo,
            'r2': round(reg['r2'], 3), 'n_escaloes': len(bins),
            'intervalo_coberto': [round(lo), round(hi)],
            'extrapolado': not (lo <= valor <= hi),
            'declive_a1_por_unidade': round(reg['declive'], 6),
            'bins': bins}


def calcular(streams, hz=1.0, early_s=EARLY_RAMP_S, artefacto_max=5.0):
    """streams: {'dfa_a1', 'respiration', 'watts', 'heartrate', 'artifacts'}

    Os limiares saem da relacao a1 x intensidade, por regressao sobre bins
    de potencia e de frequencia cardiaca. Nao ha nenhum instante da sessao
    envolvido: um limiar e' uma intensidade, e detecta-lo no tempo faz o
    resultado depender de como a sessao foi estruturada.
    """
    a1 = streams.get('dfa_a1') or []
    if not a1:
        return {'ok': False, 'motivo': 'sem stream de DFA-a1'}

    watts = streams.get('watts') or []
    hr = streams.get('heartrate') or []
    resp = streams.get('respiration') or []
    art = streams.get('artifacts') or []

    n = len(a1)
    pares_w, pares_hr, descartes = [], [], {
        'a1_invalido': 0, 'artefacto': 0, 'sem_potencia': 0, 'sem_fc': 0}

    for i in range(n):
        v = a1[i]
        if v is None or not (0 < v <= A1_MAX_PLAUSIVEL):
            descartes['a1_invalido'] += 1
            continue
        if art and i < len(art) and art[i] is not None and art[i] > artefacto_max:
            descartes['artefacto'] += 1
            continue
        w = watts[i] if i < len(watts) else None
        if w is not None and w > 0:
            pares_w.append((float(w), v))
        else:
            descartes['sem_potencia'] += 1
        h = hr[i] if i < len(hr) else None
        if h is not None and h > 0:
            pares_hr.append((float(h), v))
        else:
            descartes['sem_fc'] += 1

    max_ini, det = maximo_inicial(a1, early_s, hz)
    hrvt1c_alvo = round((max_ini + A1_HRVT2) / 2, 3) if max_ini else None

    limiares = {}
    for nome, alvo in (('HRVT1c', hrvt1c_alvo),
                       ('HRVT1s', A1_HRVT1_CLASSICO),
                       ('HRVT2', A1_HRVT2)):
        if alvo is None:
            continue
        limiares[nome] = {
            'a1_alvo': alvo,
            'watts': limiar_por_regressao(pares_w, alvo, 10.0, 'W'),
            'heartrate': limiar_por_regressao(pares_hr, alvo, 5.0, 'bpm'),
        }

    # a sessao serve para isto? precisa de percorrer intensidades
    ws = [w for w, _ in pares_w]
    amplitude = (max(ws) - min(ws)) if ws else 0
    r2s = [l['watts'].get('r2') for l in limiares.values()
           if l['watts'].get('r2') is not None]
    adequada = amplitude >= 80 and (max(r2s) if r2s else 0) >= 0.4

    return {
        'ok': True,
        'metodo': 'regressao a1 x intensidade sobre bins',
        'n_pontos': n,
        'n_pares_potencia': len(pares_w),
        'n_pares_fc': len(pares_hr),
        'descartes': descartes,
        'duracao_s': round(n / hz),
        'a1_max_inicial': round(max_ini, 3) if max_ini else None,
        'a1_max_inicial_detalhe': det,
        'a1_alvo_hrvt1c': hrvt1c_alvo,
        'a1_mediana_sessao': round(_mediana(
            [v for v in a1 if v and 0 < v <= A1_MAX_PLAUSIVEL]) or 0, 3),
        'amplitude_potencia_w': round(amplitude),
        'sessao_adequada': adequada,
        'limiares': limiares,
        'nota': ('HRVT1s (a1=0.75) e a convencao classica e sobrestima o '
                 'limiar aerobio (Rogers et al. 2024). HRVT1c usa o ponto '
                 'medio entre o teu maximo de a1 no inicio e 0.50 -- '
                 'individualizado em vez de constante.'),
        'nota_qualidade': (
            'sessao adequada: percorre intensidade suficiente e o a1 desce '
            'de forma consistente com ela'
            if adequada else
            f'sessao pouco adequada: amplitude de {round(amplitude)} W e r2 '
            f'maximo de {round(max(r2s), 2) if r2s else 0}. Um limiar so tem '
            'significado se a sessao passar por intensidades acima e abaixo '
            'dele; uma rampa ou progressiva da isto, um rolo a potencia '
            'constante nao. Marcado "extrapolado" quando o alvo cai fora do '
            'intervalo percorrido.'),
    }


def replicar_meanrra1(streams, hz=1.0):
    """Reproduz o custom field MeanRRa1 e mostra de onde vem o valor.

    A formula e' sum(resp/a1)/(60*n) sobre a SESSAO INTEIRA, com o unico
    filtro de a1 > 0. Duas consequencias que nao sao obvias:

    1. Como o a1 esta no denominador, os momentos de intensidade alta (a1
       baixo) dominam a soma. Numa sessao simulada, 60 s com a1=0.15 --
       2.7% do tempo -- valiam 19% do total.
    2. Inclui aquecimento e volta a calma, onde o a1 e' alto e a respiracao
       baixa. Nao e' um limiar; e' uma media de um racio sobre tudo.

    Serve para confirmar que estamos a ler os streams certos: se o valor
    bater com o custom field, a leitura esta correcta.
    """
    a1 = streams.get('dfa_a1') or []
    resp = streams.get('respiration') or streams.get('RespirationRateAlphaHRV') or []
    n = min(len(a1), len(resp))
    if not n:
        return {'ok': False, 'motivo': 'faltam streams de a1 ou respiracao'}

    soma, usados, contrib = 0.0, 0, []
    for i in range(n):
        v, r = a1[i], resp[i]
        if v is None or r is None or v <= 0:
            continue
        soma += r / v
        usados += 1
        contrib.append((r / v, i))
    if not usados:
        return {'ok': False, 'motivo': 'nenhum ponto com a1 > 0'}

    valor = soma / (60 * usados)
    contrib.sort(reverse=True)
    top = contrib[:max(1, usados // 20)]          # os 5% que mais pesam
    return {
        'ok': True, 'meanrra1': round(valor, 6),
        'n_usados': usados, 'n_pontos': n,
        'peso_dos_5pct_mais_altos': round(sum(c for c, _ in top) / soma * 100, 1),
        'nota': ('os 5% de pontos com maior racio valem esta percentagem da '
                 'soma total; quanto mais alto, mais o numero e um retrato '
                 'dos momentos duros e menos da sessao'),
    }
