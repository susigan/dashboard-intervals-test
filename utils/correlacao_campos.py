"""utils/correlacao_campos.py — o que anda junto com o quê.

Correlaciona os campos da Intervals.icu entre si, sessão a sessão, para
ver quais se movem juntos ao longo do tempo.

PORQUE SPEARMAN E NAO PEARSON

O Pearson mede relacao LINEAR e e' sensivel a outliers. Aqui nem uma coisa
nem outra serve:

  - as relacoes fisiologicas raramente sao lineares na gama toda. O AeT e
    o MSS podem subir juntos ate' certo ponto e depois descolar.
  - uma sessao com artefacto ou com o sensor mal posto produz um valor
    absurdo que arrasta o Pearson sozinho.

O Spearman correlaciona as ORDENS, nao os valores. Responde a "quando este
campo esteve alto, o outro tambem esteve?" -- que e' a pergunta util -- e
um outlier vale so' o seu lugar na fila, nao a sua magnitude.

CORRECCAO PARA COMPARACOES MULTIPLAS

Com 15 campos sao 105 pares. A 5%, esperam-se cinco correlacoes "fortes"
so' por acaso. Aplica-se Benjamini-Hochberg sobre todos os p antes de
decidir o que se mostra. Sem isso, a tabela enche-se de ruido bem
apresentado.

O QUE ISTO NAO E'

Correlacao entre campos NAO diz que um causa o outro, nem que sao
medidas independentes. Dois campos calculados a partir do mesmo sinal --
o Aet e o AeTwkg, por exemplo, que so' diferem pelo peso -- vao dar r=1
e isso nao e' um achado, e' aritmetica. Esses ficam assinalados.
"""

import math

N_MINIMO = 8          # sessoes em comum para um par valer a pena
ALFA = 0.05
R_FORTE = 0.70

# Pares que sao a mesma medida em unidades diferentes, ou um derivado
# directo do outro. Aparecem com r perto de 1 e nao sao achado nenhum.
TRIVIAIS = [
    ({'aet', 'aetwkg'}, 'o mesmo valor, um por quilo'),
    ({'mss', 'mssrel'}, 'o mesmo valor, um por quilo'),
    ({'pvo2max', 'pvo2maxwkg'}, 'o mesmo valor, um por quilo'),
    ({'hrvt1', 'hrvt1plus'}, 'os dois extremos da mesma banda'),
]


def _postos(vs):
    """Postos com media nos empates, como o Spearman exige."""
    ordenado = sorted(range(len(vs)), key=lambda i: vs[i])
    postos = [0.0] * len(vs)
    i = 0
    while i < len(ordenado):
        j = i
        while j + 1 < len(ordenado) and vs[ordenado[j + 1]] == vs[ordenado[i]]:
            j += 1
        media = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            postos[ordenado[k]] = media
        i = j + 1
    return postos


def spearman(a, b):
    """rho de Spearman e p bilateral."""
    n = len(a)
    if n < 4:
        return None
    ra, rb = _postos(a), _postos(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    sa = sum((x - ma) ** 2 for x in ra)
    sb = sum((x - mb) ** 2 for x in rb)
    if sa <= 0 or sb <= 0:
        return None
    rho = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n)) / (sa * sb) ** 0.5
    rho = max(-1.0, min(1.0, rho))
    if abs(rho) >= 1.0 or n <= 2:
        return {'rho': round(rho, 3), 'n': n, 'p': 0.0 if abs(rho) >= 1 else 1.0}
    t = rho * math.sqrt((n - 2) / (1 - rho * rho))
    try:
        from scipy.stats import t as _td
        p = float(2 * (1 - _td.cdf(abs(t), n - 2)))
    except ImportError:
        # aproximacao normal: com n>=8 e' aceitavel, e vai dito
        z = abs(t)
        p = 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
    return {'rho': round(rho, 3), 'n': n, 'p': round(p, 5),
            't': round(t, 2)}


def benjamini_hochberg(ps, alfa=ALFA):
    vs = sorted((p, i) for i, p in enumerate(ps) if p is not None)
    m = len(vs)
    if not m:
        return None, [False] * len(ps)
    corte = None
    for pos, (p, _i) in enumerate(vs, start=1):
        if p <= alfa * pos / m:
            corte = p
    ok = [False] * len(ps)
    if corte is not None:
        for i, p in enumerate(ps):
            if p is not None and p <= corte:
                ok[i] = True
    return corte, ok


def _trivial(a, b):
    par = {a.lower(), b.lower()}
    for chaves, motivo in TRIVIAIS:
        if par == chaves:
            return motivo
    return None


def correlacionar(por_campo, n_minimo=N_MINIMO, alfa=ALFA):
    """por_campo: {campo: {data: valor}} -> pares correlacionados.

    A chave interna e' a DATA da sessao: so' se comparam campos medidos na
    mesma sessao. Comparar medianas de janelas diferentes dava correlacoes
    entre coisas que nunca coexistiram.
    """
    campos = [k for k, v in (por_campo or {}).items() if len(v) >= n_minimo]
    if len(campos) < 2:
        return {'ok': False,
                'motivo': (f'só {len(campos)} campos com {n_minimo}+ sessões; '
                           'são precisos 2')}

    pares, ps = [], []
    for i, a in enumerate(campos):
        for b in campos[i + 1:]:
            comuns = sorted(set(por_campo[a]) & set(por_campo[b]))
            if len(comuns) < n_minimo:
                continue
            va = [por_campo[a][d] for d in comuns]
            vb = [por_campo[b][d] for d in comuns]
            r = spearman(va, vb)
            if not r:
                continue
            pares.append({
                'a': a, 'b': b, **r,
                'trivial': _trivial(a, b),
                'primeira': comuns[0], 'ultima': comuns[-1],
            })
            ps.append(r['p'])

    corte, aceites = benjamini_hochberg(ps, alfa)
    for i, e in enumerate(pares):
        e['significativa'] = aceites[i]

    sig = [e for e in pares if e['significativa'] and not e['trivial']]
    sig.sort(key=lambda e: -abs(e['rho']))
    triviais = [e for e in pares if e['trivial']]

    # o que vale a pena dizer por palavras
    notas = []
    fortes = [e for e in sig if abs(e['rho']) >= R_FORTE]
    for e in fortes[:6]:
        sentido = 'sobem juntos' if e['rho'] > 0 else 'movem-se em sentidos opostos'
        notas.append(
            f"{e['a']} e {e['b']} {sentido} (rho={e['rho']}, n={e['n']} "
            f"sessões): quando um está alto o outro acompanha, o que sugere "
            f"que medem a mesma coisa por vias diferentes — ou que ambos "
            f"respondem ao mesmo estado de forma")
    negativas = [e for e in sig if e['rho'] <= -R_FORTE]
    if negativas:
        e = negativas[0]
        notas.append(
            f"{e['a']} sobe quando {e['b']} desce (rho={e['rho']}). Vale a "
            'pena perceber porquê: pode ser fisiologia, ou pode ser que um '
            'dos dois esteja a ser calculado ao contrário')

    return {
        'ok': True,
        'n_campos': len(campos), 'campos': campos,
        'n_pares_testados': len(pares),
        'p_corte_bh': corte,
        'n_significativos': len(sig),
        'pares': sig,
        'triviais': triviais,
        'todos': sorted(pares, key=lambda e: -abs(e['rho'])),
        'notas': notas,
        'n_minimo': n_minimo,
        'metodo': ('Spearman sobre as sessões em comum a cada par, com '
                   'correcção de Benjamini-Hochberg'),
        'aviso': (
            'correlação entre campos não é causalidade nem independência. '
            'Campos calculados do mesmo sinal dão rho perto de 1 sem isso '
            'ser um achado — esses aparecem em separado, marcados como '
            'triviais. E com '
            f'{len(pares)} pares testados, sem a correcção esperar-se-iam '
            f'{round(len(pares) * alfa)} correlações falsas só por acaso'),
    }
