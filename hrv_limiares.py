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


def _isotonica(ys):
    """Torna a serie nao-crescente pelo pool adjacent violators.

    O a1 tem de descer com a intensidade -- e' o pressuposto do metodo. O
    ruido faz bins individuais subirem; a isotonica impoe a monotonia sem
    assumir forma nenhuma para a curva, ao contrario de uma recta.
    """
    blocos = [[y, 1] for y in ys]
    i = 0
    while i < len(blocos) - 1:
        if blocos[i][0] < blocos[i + 1][0]:
            soma = blocos[i][0] * blocos[i][1] + blocos[i + 1][0] * blocos[i + 1][1]
            peso = blocos[i][1] + blocos[i + 1][1]
            blocos[i] = [soma / peso, peso]
            del blocos[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1
    out = []
    for v, w in blocos:
        out.extend([v] * w)
    return out


def limiar_por_curva(pares, alvo, largura, rotulo):
    """Intensidade a que o a1 atinge o alvo, lida na propria curva.

    NAO por regressao linear. A relacao a1 x intensidade nao e' uma recta:
    numa sessao deste atleta o a1 fica em ~1.2 ate' aos 205 W e cai para
    ~0.42 aos 215 W. E' um degrau. Ajustar-lhe uma recta deu um limiar de
    68 W com r2 de 0.099 -- o r2 baixo era a curva a dizer que a recta nao
    servia, e eu a usar o valor na mesma.

    Aqui os bins sao tornados nao-crescentes e o cruzamento e' lido por
    interpolacao entre os dois escaloes que rodeiam o alvo. Devolve-se a
    largura desse degrau: se o a1 cai de 1.2 para 0.4 entre dois escaloes,
    o limiar esta algures nesses 10 W e a precisao real e' essa, nao a do
    numero interpolado.
    """
    bins = binar(pares, largura)
    if len(bins) < 4:
        return {'ok': False, 'motivo': f'so {len(bins)} escaloes de {rotulo}'}

    xs = [b['centro'] for b in bins]
    ys_bruto = [b['a1'] for b in bins]
    ys = _isotonica(ys_bruto)
    lo, hi = xs[0], xs[-1]

    if ys[0] < alvo:
        return {'ok': False, 'motivo': f'a1 ja abaixo de {alvo} no escalao mais '
                                       f'baixo ({round(lo)} {rotulo})',
                'intervalo_coberto': [round(lo), round(hi)]}
    if ys[-1] > alvo:
        return {'ok': False, 'motivo': f'a1 nunca desce a {alvo} nesta sessao '
                                       f'(minimo {round(ys[-1], 3)})',
                'intervalo_coberto': [round(lo), round(hi)],
                'a1_minimo': round(ys[-1], 3)}

    for k in range(1, len(xs)):
        if ys[k - 1] >= alvo > ys[k]:
            dy = ys[k - 1] - ys[k]
            frac = (ys[k - 1] - alvo) / dy if dy > 0 else 0.5
            valor = xs[k - 1] + frac * (xs[k] - xs[k - 1])
            return {
                'ok': True, 'valor': round(valor, 1), 'unidade': rotulo,
                'n_escaloes': len(bins),
                'intervalo_coberto': [round(lo), round(hi)],
                'degrau': [round(xs[k - 1]), round(xs[k])],
                'a1_no_degrau': [round(ys[k - 1], 3), round(ys[k], 3)],
                'queda_no_degrau': round(dy, 3),
                'n_pontos_no_degrau': bins[k - 1]['n'] + bins[k]['n'],
                'bins': [{'centro': xs[m], 'a1': round(ys_bruto[m], 3),
                          'a1_iso': round(ys[m], 3), 'n': bins[m]['n']}
                         for m in range(len(xs))],
            }
    return {'ok': False, 'motivo': 'sem cruzamento apos monotonizar'}


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
            'watts': limiar_por_curva(pares_w, alvo, 10.0, 'W'),
            'heartrate': limiar_por_curva(pares_hr, alvo, 5.0, 'bpm'),
        }

    # a sessao serve para isto? precisa de percorrer intensidades
    ws = [w for w, _ in pares_w]
    amplitude = (max(ws) - min(ws)) if ws else 0
    pct_artefacto = (descartes['artefacto'] / n * 100) if n else 0
    tem = [nome for nome, l in limiares.items()
           if (l['watts'].get('ok') or l['heartrate'].get('ok'))]
    adequada = (amplitude >= 80 and pct_artefacto < 30 and len(tem) >= 1)

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
        'pct_descartado_por_artefacto': round(pct_artefacto, 1),
        'limiares_obtidos': tem,
        'sessao_adequada': adequada,
        'limiares': limiares,
        'nota': ('HRVT1s (a1=0.75) e a convencao classica e sobrestima o '
                 'limiar aerobio (Rogers et al. 2024). HRVT1c usa o ponto '
                 'medio entre o teu maximo de a1 no inicio e 0.50 -- '
                 'individualizado em vez de constante.'),
        'nota_qualidade': (
            'sessao utilizavel' if adequada else
            'sessao nao utilizavel: ' + '; '.join(filter(None, [
                f'amplitude de so {round(amplitude)} W' if amplitude < 80 else None,
                (f'{round(pct_artefacto)}% dos pontos descartados por '
                 'artefacto na FC') if pct_artefacto >= 30 else None,
                'nenhum limiar atingido' if not tem else None]))),
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


# ══════════════════════════════════════════════════════════════════════════
# METODO RRa1 POR QUEBRA DE DECLIVE (Inigo Tolosa / alphaHRV)
#
# Diferente de tudo o que esta acima, e melhor: nao usa limiar de a1
# nenhum. Nem 0.75, nem 0.50, nem o ponto medio do Rogers.
#
#   y = RRa1 = respiracao (Hz) / a1        x = frequencia cardiaca
#
# O RRa1 sobe com a intensidade e a subida acelera nos limiares. Ajustam-se
# duas rectas a curva e o VT1 e' onde se cruzam. Depois parte-se o segmento
# superior outra vez em dois e o VT2 e' a segunda interseccao.
#
# O limiar sai da FORMA da curva, nao de uma constante emprestada -- que e'
# o principio seguido no resto deste projecto. E o ajuste de duas rectas
# contra o de tres da duas estimativas independentes do mesmo VT1: a media
# e' o valor e o desvio entre elas e' a incerteza, calculada e nao assumida.
# ══════════════════════════════════════════════════════════════════════════


def _lin_fit(xs, ys, ini, fim):
    """Minimos quadrados em [ini, fim). Devolve (declive, intercepto)."""
    n = fim - ini
    if n < 2:
        return None
    sx = sy = sxx = sxy = 0.0
    for i in range(ini, fim):
        sx += xs[i]
        sy += ys[i]
        sxx += xs[i] * xs[i]
        sxy += xs[i] * ys[i]
    det = sxx * n - sx * sx
    if det == 0:
        return None
    return ((sxy * n - sx * sy) / det, (sxx * sy - sxy * sx) / det)


def _fit_potencia_vs_fc(xs, ys):
    """Recta potencia x FC, pela janela central que melhor se ajusta.

    Reproduz a procura do script: alarga uma janela em torno do meio e fica
    com a que minimiza o residuo normalizado. Os extremos da curva -- o
    arranque e os sprints -- sao onde a relacao potencia/FC mais se afasta
    da linearidade, e esta procura evita-os sem os cortar a mao.
    """
    n = len(xs)
    if n < 22:
        p = _lin_fit(xs, ys, 0, n)
        return (p[0], p[1], None) if p else (None, None, None)
    meio = n // 2
    melhor, m_b, dmin = None, None, float('inf')
    for i in range(10, meio):
        p = _lin_fit(xs, ys, meio - i, meio + i)
        if not p:
            continue
        di = 0.0
        for j in range(meio - i, meio + i):
            d = p[0] * xs[j] + p[1] - ys[j]
            di += d * d / (i * i)
        if di < dmin:
            dmin, melhor, m_b = di, p[0], p[1]
    return melhor, m_b, round(dmin, 4) if dmin < float('inf') else None


def limiares_rra1(streams, artefacto_max=5.0, min_bins=12):
    """VT1 e VT2 pela quebra de declive do RRa1 contra a FC."""
    a1 = streams.get('dfa_a1') or []
    resp = streams.get('respiration') or streams.get('RespirationRateAlphaHRV') or []
    hr = streams.get('heartrate') or []
    pwr = streams.get('watts') or []
    art = streams.get('artifacts') or []
    if not a1 or not resp or not hr:
        return {'ok': False, 'motivo': 'faltam streams de a1, respiracao ou FC'}

    n = min(len(a1), len(resp), len(hr))
    soma_r = {}
    cont_r = {}
    soma_p = {}
    cont_p = {}
    descartados = 0
    for i in range(n):
        if art and i < len(art) and art[i] is not None and art[i] > artefacto_max:
            descartados += 1
            continue
        v, r, h = a1[i], resp[i], hr[i]
        if v is None or r is None or h is None or v <= 0 or not (30 < h < 221):
            continue
        b = int(round(h))
        soma_r[b] = soma_r.get(b, 0.0) + r / (60.0 * v)
        cont_r[b] = cont_r.get(b, 0) + 1
        if i < len(pwr) and pwr[i] and pwr[i] > 0:
            soma_p[b] = soma_p.get(b, 0.0) + pwr[i]
            cont_p[b] = cont_p.get(b, 0) + 1

    xs = sorted(soma_r)
    ys = [soma_r[b] / cont_r[b] for b in xs]
    if len(xs) < min_bins:
        return {'ok': False, 'motivo': f'so {len(xs)} escaloes de FC (min {min_bins})',
                'descartados_por_artefacto': descartados}

    nP = len(xs)
    # ── VT1: duas rectas ──────────────────────────────────────────────────
    dmin, m1, b1, m2, b2, th1 = float('inf'), None, None, None, None, None
    for i in range(3, nP - 3):
        p1 = _lin_fit(xs, ys, 0, i + 1)
        p2 = _lin_fit(xs, ys, i + 1, nP)
        if not p1 or not p2:
            continue
        di = 0.0
        for j in range(0, i + 1):
            d = p1[0] * xs[j] + p1[1] - ys[j]
            di += d * d
        for j in range(i + 1, nP - 1):
            d = p2[0] * xs[j] + p2[1] - ys[j]
            di += d * d
        # so' aceitar se o declive de cima for mais inclinado e a
        # interseccao cair dentro dos dados: sem isto, duas rectas quase
        # paralelas dao um cruzamento em qualquer sitio
        if p1[0] >= p2[0]:
            continue
        cruz = (p2[1] - p1[1]) / (p1[0] - p2[0])
        if cruz <= xs[0] or cruz >= xs[-1]:
            continue
        if di < dmin:
            dmin, m1, b1, m2, b2, th1 = di, p1[0], p1[1], p2[0], p2[1], i

    if m1 is None:
        return {'ok': False, 'motivo': 'nao ha quebra de declive nesta sessao',
                'n_escaloes_fc': nP, 'descartados_por_artefacto': descartados}

    vt1 = (b2 - b1) / (m1 - m2)

    # ── VT2: partir o segmento de cima outra vez ──────────────────────────
    vt2 = vt1p = vt2p = None
    dmin2, m2p, b2p, m3, b3 = float('inf'), None, None, None, None
    for i in range(th1 + 15, nP - 3):
        p1 = _lin_fit(xs, ys, th1, i + 1)
        p2 = _lin_fit(xs, ys, i + 1, nP)
        if not p1 or not p2:
            continue
        di = 0.0
        for j in range(th1, i + 1):
            d = p1[0] * xs[j] + p1[1] - ys[j]
            di += d * d
        for j in range(i + 1, nP - 1):
            d = p2[0] * xs[j] + p2[1] - ys[j]
            di += d * d
        if di < dmin2:
            dmin2, m2p, b2p, m3, b3 = di, p1[0], p1[1], p2[0], p2[1]

    if m3 is not None and m2p is not None and m3 > m2p and m1 != m2p and m2 != m3:
        vt2 = (b3 - b2) / (m2 - m3)
        vt1p = (b2p - b1) / (m1 - m2p)
        vt2p = (b3 - b2p) / (m2p - m3)

    # ── potencia a cada limiar ────────────────────────────────────────────
    xp = sorted(soma_p)
    yp = [soma_p[b] / cont_p[b] for b in xp]
    mP, bP, res_p = _fit_potencia_vs_fc(xp, yp) if len(xp) >= 6 else (None, None, None)

    def _w(v):
        if v is None or mP is None:
            return None
        return round(mP * v + bP, 1)

    # BUG do script original corrigido aqui: la' o VT1, que esta em bpm, era
    # validado contra a gama de POTENCIA da sessao. Num rolo com 300 W de
    # pico um VT1 de 140 bpm passava por acaso; numa sessao mais fraca o
    # mesmo valor era anulado. A validacao tem de ser contra a gama de FC.
    def _valido(v):
        return v is not None and xs[0] <= v <= xs[-1]

    saida = {'ok': True,
             'metodo': 'RRa1 x FC, quebra de declive (Tolosa/alphaHRV)',
             'n_escaloes_fc': nP,
             'gama_fc': [xs[0], xs[-1]],
             'descartados_por_artefacto': descartados,
             'residuo_2_rectas': round(dmin, 5),
             'declives': {'m1': round(m1, 5), 'm2': round(m2, 5),
                          'm2_linha3': round(m2p, 5) if m2p else None,
                          'm3': round(m3, 5) if m3 else None},
             'curva_rra1': [{'fc': xs[i], 'rra1': round(ys[i], 4),
                             'n': cont_r[xs[i]]} for i in range(nP)],
             'potencia_vs_fc': ({'declive_w_por_bpm': round(mP, 2),
                                 'intercepto': round(bP, 1),
                                 'residuo': res_p, 'n_escaloes': len(xp)}
                                if mP is not None else None)}

    estimativas = {'VT1': [], 'VT2': []}
    if _valido(vt1):
        estimativas['VT1'].append(round(vt1, 1))
    if _valido(vt1p):
        estimativas['VT1'].append(round(vt1p, 1))
    if _valido(vt2):
        estimativas['VT2'].append(round(vt2, 1))
    if _valido(vt2p):
        estimativas['VT2'].append(round(vt2p, 1))

    for nome, vs in estimativas.items():
        if not vs:
            saida[nome] = None
            continue
        media = sum(vs) / len(vs)
        dp = (sum((v - media) ** 2 for v in vs) / len(vs)) ** 0.5
        saida[nome] = {
            'fc_bpm': round(media, 1),
            'fc_sd': round(dp, 1),
            'watts': _w(media),
            'watts_sd': round(abs(mP) * dp, 1) if mP else None,
            'estimativas_bpm': vs,
            'n_ajustes': len(vs),
        }
    saida['nota'] = (
        'Sem limiar de a1: o VT1 e a interseccao de duas rectas ajustadas ao '
        'RRa1 contra a FC, e o VT2 vem de partir o segmento superior outra '
        'vez. O sd e a diferenca entre o ajuste de 2 rectas e o de 3 -- duas '
        'estimativas independentes do mesmo ponto. Um sd grande diz que a '
        'quebra nao esta bem definida nesta sessao.')
    return saida


# ══════════════════════════════════════════════════════════════════════════
# METODO RRa1 x FC — ponto de quebra, sem constante de a1
#
# Porta do activity chart do forum da Intervals.icu. E' melhor do que o
# metodo do a1 fixo por quatro razoes, todas visiveis nos dados deste
# atleta:
#
#   1. Nao usa constante nenhuma. O limiar e' o ponto onde a relacao
#      RRa1 x FC muda de declive. O 0.75 e o 0.50 desaparecem.
#   2. Usa RRa1 = respiracao / (60 x a1). No limiar as duas variaveis
#      movem-se no mesmo sentido, portanto o sinal e' maior do que em
#      qualquer uma sozinha.
#   3. O limiar sai em bpm, onde a dispersao entre sessoes era de 17 bpm
#      entre quartis. A potencia vem depois, por um ajuste separado
#      potencia x FC -- e' isto que evita a dispersao de 2.5x que o meu
#      metodo tinha ao binar a1 contra a potencia directamente.
#   4. Devolve VT1 como media de duas estimativas independentes, com
#      desvio-padrao. Da' a incerteza em vez de um numero seco.
#
# Uma correccao face ao original: la' o teste de sanidade compara VT1, que
# esta em bpm, com o minimo e o maximo da POTENCIA. Compara batimentos com
# watts. Aqui cada grandeza e' verificada na sua unidade.
# ══════════════════════════════════════════════════════════════════════════

FC_MAX_BIN = 221          # tamanho dos vectores de acumulacao, como no original
SEPARACAO_MINIMA_VT = 15  # bpm entre VT1 e VT2, como no original


def _lin_fit(xs, ys, ini, fim):
    """Minimos quadrados em xs[ini:fim]. Igual ao linFit do original."""
    n = fim - ini
    if n < 2:
        return None
    sx = sum(xs[ini:fim])
    sx2 = sum(v * v for v in xs[ini:fim])
    sy = sum(ys[ini:fim])
    sxy = sum(xs[i] * ys[i] for i in range(ini, fim))
    det = sx2 * n - sx * sx
    if det == 0:
        return None
    return ((sxy * n - sx * sy) / det, (sx2 * sy - sxy * sx) / det)


def _residuo(xs, ys, m, b, a, z):
    return sum((m * xs[j] + b - ys[j]) ** 2 for j in range(a, min(z + 1, len(xs))))


def limiares_rra1(streams, suavizar_resp=1):
    """VT1 e VT2 pelo ponto de quebra de RRa1 contra a FC."""
    a1 = streams.get('dfa_a1') or []
    resp = (streams.get('respiration')
            or streams.get('RespirationRateAlphaHRV') or [])
    hr = streams.get('heartrate') or []
    pwr = streams.get('watts') or []
    if not a1 or not resp or not hr:
        return {'ok': False, 'motivo': 'faltam streams de a1, respiracao ou FC'}

    if suavizar_resp > 1:
        bp = (suavizar_resp - 1) // 2
        suav = list(resp)
        for i in range(bp, len(resp) - bp):
            vs = [resp[j] for j in range(i - bp, i + bp + 1) if resp[j] is not None]
            if vs:
                suav[i] = sum(vs) / len(vs)
        resp = suav

    n_rra1 = [0] * FC_MAX_BIN
    s_rra1 = [0.0] * FC_MAX_BIN
    n_pwr = [0] * FC_MAX_BIN
    s_pwr = [0.0] * FC_MAX_BIN

    for i in range(min(len(a1), len(resp), len(hr))):
        v, r, h = a1[i], resp[i], hr[i]
        if v is None or r is None or h is None:
            continue
        b = int(h)
        if not (0 < b < FC_MAX_BIN):
            continue
        if v > 0:
            s_rra1[b] += r / (60.0 * v)
            n_rra1[b] += 1
        if i < len(pwr) and pwr[i] is not None and pwr[i] > 0:
            s_pwr[b] += pwr[i]
            n_pwr[b] += 1

    xs = [i for i in range(FC_MAX_BIN) if n_rra1[i] > 0]
    ys = [s_rra1[i] / n_rra1[i] for i in xs]
    if len(xs) < 10:
        return {'ok': False, 'motivo': f'so {len(xs)} batimentos com dados'}

    # ── recta potencia x FC, pela janela que melhor se ajusta ──
    xp = [i for i in range(FC_MAX_BIN) if n_pwr[i] > 0]
    yp = [s_pwr[i] / n_pwr[i] for i in xp]
    m_pwr = b_pwr = None
    if len(xp) >= 20:
        meio = len(xp) // 2
        melhor = None
        for i in range(10, meio):
            p = _lin_fit(xp, yp, meio - i, meio + i)
            if not p:
                continue
            d = _residuo(xp, yp, p[0], p[1], meio - i, meio + i) / (i * i)
            if melhor is None or d < melhor[0]:
                melhor = (d, p[0], p[1])
        if melhor:
            _, m_pwr, b_pwr = melhor

    def _para_watts(bpm):
        if bpm is None or m_pwr is None:
            return None
        return round(m_pwr * bpm + b_pwr)

    nP = len(xs)
    # ── quebra de dois segmentos ──
    melhor, th1 = None, None
    for i in range(3, nP - 3):
        p1 = _lin_fit(xs, ys, 0, i)
        p2 = _lin_fit(xs, ys, i + 1, nP - 1)
        if not p1 or not p2 or p1[0] >= p2[0]:
            continue
        if p1[0] == p2[0]:
            continue
        inter = (p2[1] - p1[1]) / (p1[0] - p2[0])
        if inter <= xs[0]:
            continue
        d = (_residuo(xs, ys, p1[0], p1[1], 0, i)
             + _residuo(xs, ys, p2[0], p2[1], i + 1, nP - 2))
        if melhor is None or d < melhor[0]:
            melhor, th1 = (d, p1, p2), i
    if melhor is None:
        return {'ok': False, 'motivo': 'nenhuma quebra valida em RRa1 x FC',
                'n_batimentos': nP}
    res2, (m1, b1), (m2, b2) = melhor

    # ── quebra do segmento de cima, para o VT2 ──
    melhor3, th2 = None, None
    for i in range(th1 + SEPARACAO_MINIMA_VT, nP - 3):
        p1 = _lin_fit(xs, ys, th1, i)
        p2 = _lin_fit(xs, ys, i + 1, nP - 1)
        if not p1 or not p2:
            continue
        d = (_residuo(xs, ys, p1[0], p1[1], th1, i)
             + _residuo(xs, ys, p2[0], p2[1], i + 1, nP - 2))
        if melhor3 is None or d < melhor3[0]:
            melhor3, th2 = (d, p1, p2), i

    def _cruz(ma, ba, mb, bb):
        return round((bb - ba) / (ma - mb)) if ma != mb else None

    vt1 = _cruz(m1, b1, m2, b2)
    vt2 = vt1p = vt2p = None
    if melhor3:
        _, (m2p, b2p), (m3, b3) = melhor3
        if m3 > m2p:
            vt2 = _cruz(m2, b2, m3, b3)
            vt1p = _cruz(m1, b1, m2p, b2p)
            vt2p = _cruz(m2p, b2p, m3, b3)

    # sanidade NA UNIDADE CERTA -- o original comparava bpm com watts
    fc_lo, fc_hi = xs[0], xs[-1]
    def _valida(v):
        return v if (v is not None and fc_lo <= v <= fc_hi) else None
    vt1, vt2, vt1p, vt2p = map(_valida, (vt1, vt2, vt1p, vt2p))

    def _media_dp(a, b):
        vs = [v for v in (a, b) if v is not None]
        if not vs:
            return None, None
        m = sum(vs) / len(vs)
        dp = (sum((v - m) ** 2 for v in vs) / len(vs)) ** 0.5
        return round(m, 1), round(dp, 1)

    vt1_m, vt1_dp = _media_dp(vt1, vt1p)
    vt2_m, vt2_dp = _media_dp(vt2, vt2p)
    pt1, pt1_dp = _media_dp(_para_watts(vt1), _para_watts(vt1p))
    pt2, pt2_dp = _media_dp(_para_watts(vt2), _para_watts(vt2p))

    ws = [w for w in pwr if w and w > 0]
    if ws and pt1 is not None and not (min(ws) <= pt1 <= max(ws)):
        pt1 = pt1_dp = None
    if ws and pt2 is not None and not (min(ws) <= pt2 <= max(ws)):
        pt2 = pt2_dp = None

    return {
        'ok': vt1_m is not None,
        'metodo': 'ponto de quebra de RRa1 x FC (sem constante de a1)',
        'VT1_bpm': vt1_m, 'VT1_dp': vt1_dp,
        'VT2_bpm': vt2_m, 'VT2_dp': vt2_dp,
        'PT1_w': pt1, 'PT1_dp': pt1_dp,
        'PT2_w': pt2, 'PT2_dp': pt2_dp,
        'estimativas': {'VT1_2rectas': vt1, 'VT1_3rectas': vt1p,
                        'VT2_2rectas': vt2, 'VT2_3rectas': vt2p},
        'declives': {'m1': round(m1, 5), 'm2': round(m2, 5)},
        'n_batimentos': nP,
        'intervalo_fc': [fc_lo, fc_hi],
        'recta_potencia_fc': ({'declive_w_por_bpm': round(m_pwr, 2),
                               'intercepto': round(b_pwr, 1)}
                              if m_pwr is not None else None),
        'residuo_2seg': round(res2, 4),
        'nota': ('o desvio-padrao vem de duas estimativas independentes -- '
                 'ajuste de duas e de tres rectas. Nao e um intervalo de '
                 'confianca; e a distancia entre dois metodos que deviam '
                 'concordar. Grande, nao confiar no valor'),
    }
