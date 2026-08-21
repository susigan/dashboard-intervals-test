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


def cruzamento(a1, alvo, canais, hz=1.0, suavizar=True):
    """Onde o a1 desce abaixo do alvo, e o que os outros canais marcam ai.

    Usa-se a ULTIMA descida, nao a primeira: no inicio da sessao o a1
    oscila e cruza o alvo varias vezes antes de assentar. A ultima e' a que
    corresponde a passagem definitiva para acima do limiar.
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
        # media dos 30 s anteriores ao cruzamento: o valor instantaneo de
        # potencia oscila de mais para servir de limiar
        a = max(0, idx - int(30 * hz))
        vs = [v for v in dados[a:idx + 1] if v is not None]
        if vs:
            out[nome] = round(sum(vs) / len(vs), 1)
    return out


def calcular(streams, hz=1.0, early_s=EARLY_RAMP_S):
    """streams: {'dfa_a1': [...], 'respiration': [...], 'watts': [...],
                 'heartrate': [...]}"""
    a1 = streams.get('dfa_a1') or []
    if not a1:
        return {'ok': False, 'motivo': 'sem stream de DFA-a1'}

    canais = {k: streams.get(k) for k in ('watts', 'heartrate', 'respiration')
              if streams.get(k)}
    validos = [v for v in a1 if v is not None and 0 < v <= A1_MAX_PLAUSIVEL]
    if len(validos) < 60:
        return {'ok': False, 'motivo': f'so {len(validos)} pontos de a1 validos'}

    max_ini, det = maximo_inicial(a1, early_s, hz)
    hrvt1c_alvo = round((max_ini + A1_HRVT2) / 2, 3) if max_ini else None

    limiares = {}
    for nome, alvo in (('HRVT1s', A1_HRVT1_CLASSICO),
                       ('HRVT2', A1_HRVT2),
                       ('HRVT1c', hrvt1c_alvo)):
        if alvo is None:
            continue
        limiares[nome] = cruzamento(a1, alvo, canais, hz)

    return {
        'ok': True,
        'n_pontos': len(a1),
        'n_validos': len(validos),
        'duracao_s': round(len(a1) / hz),
        'a1_max_inicial': round(max_ini, 3) if max_ini else None,
        'a1_max_inicial_detalhe': det,
        'a1_alvo_hrvt1c': hrvt1c_alvo,
        'a1_mediana_sessao': round(_mediana(validos), 3),
        'limiares': limiares,
        'canais_disponiveis': sorted(canais),
        'nota': ('HRVT1s (a1=0.75) e a convencao classica e sobrestima o '
                 'limiar aerobio, segundo Rogers et al. 2024. HRVT1c usa o '
                 'ponto medio entre o teu maximo de a1 no inicio do esforco '
                 'e 0.50 -- individualizado em vez de constante. HRVT2 '
                 '(a1=0.50) corresponde ao ponto de compensacao '
                 'respiratoria.'),
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
