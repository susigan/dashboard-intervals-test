"""utils/hrv_qualidade.py — filtro de qualidade e inflexao no aquecimento.

Duas coisas que os dados das 85 sessoes de Bike tornaram necessarias.

FILTRO POR MODALIDADE
    A percentagem de pontos descartados por artefacto na FC varia de 0% a
    97% nas sessoes deste atleta, e sessoes com 70% ou 97% estavam a entrar
    nas medianas dos campos externos sem ninguem saber. O limiar nao pode
    ser o mesmo em todas as modalidades: a cinta comporta-se de forma
    diferente no remo, onde o tronco se move, do que no rolo. Mas tambem
    nao se inventam limiares por modalidade -- calculam-se da distribuicao
    do proprio atleta em cada uma.

INFLEXAO NO AQUECIMENTO
    Nas sessoes livres, o ajuste de duas rectas colocava o VT1 a 80-105 bpm
    em 16% dos casos, porque ha muito tempo de FC baixa e a quebra assenta
    ai. O aquecimento resolve isso pela estrutura: e' uma escada de blocos
    de watts crescentes, iguais entre sessoes. Cada bloco da um par
    (watts, dfa1) ja' medio, sem ruido de segundo a segundo, e o primeiro
    ponto onde o a1 deixa de estar plano e' o candidato a VT1 -- lido numa
    rampa controlada em vez de numa sessao qualquer.
"""


def _mediana(vs):
    vs = sorted(v for v in vs if v is not None)
    if not vs:
        return None
    n = len(vs)
    return vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2


def _quartis(vs):
    vs = sorted(v for v in vs if v is not None)
    if not vs:
        return None
    n = len(vs)

    def _p(q):
        if n == 1:
            return vs[0]
        pos = q * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        return vs[lo] + (vs[hi] - vs[lo]) * (pos - lo)

    return {'n': n, 'min': round(vs[0], 1), 'p25': round(_p(.25), 1),
            'p50': round(_p(.50), 1), 'p75': round(_p(.75), 1),
            'p90': round(_p(.90), 1), 'max': round(vs[-1], 1)}


# Tecto absoluto: acima disto a serie de RR tem buracos a mais para
# qualquer coisa derivada dela significar seja o que for, em qualquer
# modalidade. Nao e' uma constante fisiologica, e' o ponto em que menos de
# metade dos batimentos sobrevive.
ARTEFACTO_TECTO = 50.0

# Fraccao das sessoes que se quer conservar quando a modalidade e' toda
# ruidosa. Exigir 5% no remo deixaria zero sessoes; exigir nada deixaria
# entrar as de 97%.
FRACCAO_MINIMA_CONSERVADA = 0.60


def limiar_por_modalidade(pcts, tecto=ARTEFACTO_TECTO,
                          fraccao=FRACCAO_MINIMA_CONSERVADA):
    """Limiar de artefacto calibrado na distribuicao da propria modalidade.

    A regra: manter as sessoes melhores ate' cobrir 'fraccao' delas, mas
    nunca aceitar acima do tecto. Numa modalidade limpa o limiar cai
    naturalmente para valores baixos; numa ruidosa sobe ate' ao tecto e
    para ai, e o numero de sessoes conservadas diz o resto.

    Devolve o limiar e a distribuicao, para se poder discordar do numero
    olhando para os dados em vez de para o criterio.
    """
    vs = sorted(p for p in pcts if p is not None)
    if not vs:
        return {'limiar': tecto, 'n': 0, 'motivo': 'sem sessoes'}
    alvo = vs[min(len(vs) - 1, int(len(vs) * fraccao))]
    limiar = min(alvo, tecto)
    conservadas = sum(1 for v in vs if v <= limiar)
    return {
        'limiar': round(limiar, 1),
        'tecto_absoluto': tecto,
        'n_sessoes': len(vs),
        'n_conservadas': conservadas,
        'pct_conservadas': round(conservadas / len(vs) * 100, 1),
        'distribuicao': _quartis(vs),
        'limitado_pelo_tecto': alvo > tecto,
        'nota': ('o limiar sai da distribuicao desta modalidade, nao de um '
                 'numero fixo. Se limitado_pelo_tecto for verdadeiro, a '
                 'modalidade e ruidosa ao ponto de nem 60% das sessoes '
                 'serem aproveitaveis'),
    }


# ══════════════════════════════════════════════════════════════════════════
# INFLEXAO DO DFA-a1 NA ESCADA DO AQUECIMENTO
# ══════════════════════════════════════════════════════════════════════════

def inflexao_na_escada(blocos, chave_x='watts_alvo', chave_y='dfa1_avg',
                       min_blocos=4):
    """Primeiro ponto onde o a1 deixa de estar plano, numa escada de blocos.

    Cada bloco do aquecimento e' um patamar de watts com um a1 medio ja'
    calculado. Sao poucos pontos, ordenados, e sem o ruido segundo a
    segundo que fazia o ajuste falhar nas sessoes livres.

    Procura-se a divisao que melhor separa a escada em duas rectas, com a
    segunda a descer mais do que a primeira. Ao contrario do metodo sobre a
    sessao inteira, aqui nao ha aquecimento nem volta a calma a puxar a
    quebra para o inicio: a escada JA E' o aquecimento, do principio ao fim.

    Devolve tambem a queda relativa, porque um 'ponto de quebra' entre duas
    rectas quase paralelas nao e' quebra nenhuma.
    """
    pts = [(b.get(chave_x), b.get(chave_y)) for b in blocos]
    pts = [(float(x), float(y)) for x, y in pts
           if x is not None and y is not None and y > 0]
    pts.sort()
    if len(pts) < min_blocos:
        return {'ok': False, 'motivo': f'so {len(pts)} blocos com {chave_y}'}

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    def _fit(a, b):
        n = b - a
        if n < 2:
            return None
        mx = sum(xs[a:b]) / n
        my = sum(ys[a:b]) / n
        sxx = sum((v - mx) ** 2 for v in xs[a:b])
        if sxx <= 0:
            return None
        sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(a, b))
        m = sxy / sxx
        return m, my - m * mx

    melhor = None
    for k in range(2, len(pts) - 1):
        p1, p2 = _fit(0, k), _fit(k, len(pts))
        if not p1 or not p2:
            continue
        if p2[0] >= p1[0]:          # o segundo troco tem de descer mais
            continue
        res = (sum((p1[0] * xs[i] + p1[1] - ys[i]) ** 2 for i in range(k))
               + sum((p2[0] * xs[i] + p2[1] - ys[i]) ** 2
                     for i in range(k, len(pts))))
        if melhor is None or res < melhor[0]:
            melhor = (res, k, p1, p2)
    if melhor is None:
        return {'ok': False, 'motivo': 'a1 nao muda de declive nesta escada',
                'pontos': [{'x': x, 'y': round(y, 3)} for x, y in pts]}

    res, k, (m1, b1), (m2, b2) = melhor
    if m1 == m2:
        return {'ok': False, 'motivo': 'rectas paralelas'}
    x_inf = (b2 - b1) / (m1 - m2)
    if not (xs[0] <= x_inf <= xs[-1]):
        return {'ok': False, 'motivo': 'inflexao fora do intervalo da escada',
                'x_calculado': round(x_inf, 1),
                'intervalo': [xs[0], xs[-1]]}

    return {
        'ok': True,
        'inflexao': round(x_inf, 1),
        'a1_na_inflexao': round(m1 * x_inf + b1, 3),
        'declive_antes': round(m1, 6),
        'declive_depois': round(m2, 6),
        'razao_declives': (round(m2 / m1, 2) if m1 else None),
        'n_blocos': len(pts),
        'intervalo': [xs[0], xs[-1]],
        'bloco_da_quebra': k,
        'pontos': [{'x': x, 'y': round(y, 3)} for x, y in pts],
        'nota': ('razao_declives grande significa quebra nitida; perto de 1 '
                 'as duas rectas sao quase paralelas e a inflexao nao tem '
                 'significado, por muito bem que o ajuste corra'),
    }


def resumir_inflexoes(por_sessao, chave='inflexao'):
    """Mediana e quartis das inflexoes, com a dispersao entre sessoes.

    A dispersao aqui e' o numero que interessa: se aquecimentos iguais
    derem inflexoes proximas, o metodo e' repetivel e o valor serve de
    ancora. Se derem valores espalhados, nao serve -- e isso e' resposta,
    nao falha.
    """
    vs = [s.get(chave) for s in por_sessao if s.get(chave) is not None]
    q = _quartis(vs)
    if not q:
        return {'ok': False, 'n': 0}
    iqr = q['p75'] - q['p25']
    return {
        'ok': True, 'n': q['n'], 'mediana': q['p50'],
        'p25': q['p25'], 'p75': q['p75'], 'iqr': round(iqr, 1),
        'min': q['min'], 'max': q['max'],
        'iqr_relativo_pct': (round(iqr / q['p50'] * 100, 1)
                             if q['p50'] else None),
        'repetivel': bool(q['p50'] and iqr / q['p50'] < 0.15),
    }
