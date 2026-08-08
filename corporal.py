"""Composicao corporal e nutricao.

Regras portadas do tab_corporal.py do dashboard:

  BF: a fonte primaria e o FAT do formulario de wellness; o BF do
      Consolidado_Comida so entra onde o formulario nao tem valor.
      Atencao: 'Fat' no sheet de comida sao GRAMAS de gordura alimentar,
      nao percentagem corporal — sao coisas diferentes.

  Cada coluna e cortada no seu ultimo registo valido, para nao arrastar
  linhas futuras vazias.

  Peso e BF actuais = mediana da media movel de 7 dias das ultimas 2 semanas.
  A media movel remove o ruido diario de agua e glicogenio.

  Bandas de variacao esperada, sobre o valor do periodo anterior:
      Peso  +/-0.30% a +/-0.70%
      BF    +/-0.25% a +/-0.65%

  Lag calorico: testa 0,3,5,7,10,14,21 dias e fica com o que der a
  correlacao de Spearman mais forte com p < 0.10.
"""

from datetime import datetime, timedelta

NUM_COLS = ['peso', 'bf', 'calorias', 'carb', 'fat', 'ptn', 'net']

BANDAS = {
    'peso': [(0.0070, '+max (+0.70%)', '#27ae60'),
             (0.0030, '+min (+0.30%)', '#82e0aa'),
             (-0.0030, '-min (-0.30%)', '#f1948a'),
             (-0.0070, '-max (-0.70%)', '#e74c3c')],
    'bf':   [(0.0065, '+max (+0.65%)', '#f39c12'),
             (0.0025, '+min (+0.25%)', '#fad7a0'),
             (-0.0025, '-min (-0.25%)', '#aed6f1'),
             (-0.0065, '-max (-0.65%)', '#2980b9')],
}

# kcal por grama, para a repartição energética dos macros
KCAL = {'carb': 4.0, 'ptn': 4.0, 'fat': 9.0}


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


def preparar(corporal, wellness):
    """Junta as duas fontes e limpa, seguindo as regras do dashboard."""
    if not corporal:
        return []

    # BF do formulario de wellness tem prioridade
    bf_well = {}
    for w in (wellness or []):
        v = _num(w.get('fat'))
        if v is not None:
            bf_well[w['date']] = v

    linhas = []
    for r in corporal:
        d = dict(r)
        if d['date'] in bf_well:
            d['bf'] = bf_well[d['date']]
        linhas.append(d)

    # tambem entram dias que so existem no wellness
    existentes = {r['date'] for r in linhas}
    for data, v in bf_well.items():
        if data not in existentes:
            linhas.append({'date': data, 'bf': v})
    linhas.sort(key=lambda r: r['date'])

    # cortar cada coluna no seu ultimo registo valido
    for col in NUM_COLS:
        ultimo = None
        for r in linhas:
            if _num(r.get(col)) is not None:
                ultimo = r['date']
        if ultimo:
            for r in linhas:
                if r['date'] > ultimo:
                    r[col] = None

    return [r for r in linhas
            if any(_num(r.get(c)) is not None for c in NUM_COLS)]


def media_movel(linhas, campo, janela=7, minimo=3):
    """Media movel diaria. Preenche os dias em falta para nao distorcer."""
    if not linhas:
        return []
    d0 = datetime.strptime(linhas[0]['date'], '%Y-%m-%d')
    d1 = datetime.strptime(linhas[-1]['date'], '%Y-%m-%d')
    idx = {r['date']: _num(r.get(campo)) for r in linhas}

    dias, atual = [], d0
    while atual <= d1:
        dias.append(atual.strftime('%Y-%m-%d'))
        atual += timedelta(days=1)

    out = []
    for i, d in enumerate(dias):
        seg = [idx.get(x) for x in dias[max(0, i - janela + 1):i + 1]]
        seg = [v for v in seg if v is not None]
        out.append({'date': d,
                    'valor': round(sum(seg) / len(seg), 2) if len(seg) >= minimo else None})
    return out


def valor_actual(serie_r7, dias=14):
    """Mediana da media movel nas ultimas 2 semanas — mais robusta que o
    ultimo valor, que pode ser um dia atipico."""
    vals = [r['valor'] for r in serie_r7[-dias:] if r['valor'] is not None]
    if len(vals) < 3:
        vals = [r['valor'] for r in serie_r7 if r['valor'] is not None][-dias:]
    if not vals:
        return None
    vals = sorted(vals)
    m = len(vals) // 2
    return round(vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2, 2)


def agrupar(linhas, periodo='W'):
    """Media por semana, mes ou trimestre."""
    def chave(d):
        dt = datetime.strptime(d, '%Y-%m-%d')
        if periodo == 'M':
            return f"{dt.year}-{dt.month:02d}"
        if periodo == 'Q':
            return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    grupos = {}
    for r in linhas:
        k = chave(r['date'])
        g = grupos.setdefault(k, {'periodo': k, 'de': r['date'], 'ate': r['date'],
                                  '_soma': {}, '_n': {}})
        g['de'] = min(g['de'], r['date'])
        g['ate'] = max(g['ate'], r['date'])
        for c in NUM_COLS:
            v = _num(r.get(c))
            if v is None:
                continue
            g['_soma'][c] = g['_soma'].get(c, 0.0) + v
            g['_n'][c] = g['_n'].get(c, 0) + 1

    out = []
    for k in sorted(grupos):
        g = grupos[k]
        linha = {'periodo': k, 'de': g['de'], 'ate': g['ate']}
        for c in NUM_COLS:
            linha[c] = (round(g['_soma'][c] / g['_n'][c], 2)
                        if g['_n'].get(c) else None)
        out.append(linha)
    return out


def variacao_com_bandas(agrupado, campo):
    """Variacao periodo a periodo e limites esperados.

    Os limites sao percentagens do valor do periodo ANTERIOR, nao valores
    fixos: perder 0.5 kg pesa diferente a 60 kg e a 90 kg.
    """
    out = []
    anterior = None
    for g in agrupado:
        v = g.get(campo)
        if v is None:
            continue
        if anterior is not None:
            linha = {'periodo': g['periodo'], 'valor': v,
                     'delta': round(v - anterior, 2), 'base': anterior}
            for pct, rotulo, cor in BANDAS.get(campo, []):
                linha[rotulo] = round(anterior * pct, 3)
            out.append(linha)
        anterior = v
    return out


def _spearman(x, y):
    """Correlacao de Spearman e p-valor aproximado (t de Student)."""
    import math
    n = len(x)
    if n < 5:
        return None, None

    def postos(v):
        ordem = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[ordem[j + 1]] == v[ordem[i]]:
                j += 1
            media = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[ordem[k]] = media
            i = j + 1
        return r

    rx, ry = postos(x), postos(y)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    if den == 0:
        return None, None
    r = num / den
    if abs(r) >= 1:
        return r, 0.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    # aproximacao normal ao p bilateral
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return round(r, 3), round(p, 4)


def lag_calorico(cal_r7, var_r7, lags=(0, 3, 5, 7, 10, 14, 21)):
    """Ao fim de quantos dias as calorias se reflectem no peso ou no BF.

    Testa varios desfasamentos e fica com o mais forte que seja
    estatisticamente defensavel (p < 0.10).
    """
    idx_cal = {r['date']: r['valor'] for r in cal_r7}
    idx_var = {r['date']: r['valor'] for r in var_r7}
    datas = sorted(set(idx_var))

    resultados, melhor = [], {'lag': 7, 'r': None, 'p': None}
    for lag in lags:
        pares = []
        for d in datas:
            dt = (datetime.strptime(d, '%Y-%m-%d') - timedelta(days=lag)
                  ).strftime('%Y-%m-%d')
            a, b = idx_cal.get(dt), idx_var.get(d)
            if a is not None and b is not None:
                pares.append((a, b))
        if len(pares) < 15:
            resultados.append({'lag': lag, 'r': None, 'p': None, 'n': len(pares)})
            continue
        r, p = _spearman([x for x, _ in pares], [y for _, y in pares])
        resultados.append({'lag': lag, 'r': r, 'p': p, 'n': len(pares)})
        if r is not None and p is not None and p < 0.10:
            if melhor['r'] is None or abs(r) > abs(melhor['r']):
                melhor = {'lag': lag, 'r': r, 'p': p, 'n': len(pares)}
    return {'melhor': melhor, 'todos': resultados}


def macros_percentagem(linhas):
    """Reparticao energetica dos macros. Calculada a partir das gramas,
    porque as colunas _perc do sheet nem sempre estao preenchidas."""
    out = []
    for r in linhas:
        g = {c: _num(r.get(c)) for c in ('carb', 'ptn', 'fat')}
        if not all(v is not None for v in g.values()):
            continue
        kcal = {c: g[c] * KCAL[c] for c in g}
        total = sum(kcal.values())
        if total <= 0:
            continue
        item = {'date': r['date'], 'kcal_macros': round(total)}
        for c in g:
            item[c + '_pct'] = round(kcal[c] / total * 100, 1)
            item[c + '_g'] = g[c]
        cal = _num(r.get('calorias'))
        if cal:
            # diferenca entre as calorias registadas e a soma dos macros:
            # se for grande, ha macros por preencher
            item['calorias'] = cal
            item['delta_kcal'] = round(total - cal)
        out.append(item)
    return out


def resumo(linhas, wellness=None):
    """Cobertura, valores actuais e tendencias."""
    if not linhas:
        return None
    datas = [r['date'] for r in linhas]
    r7 = {c: media_movel(linhas, c) for c in ('peso', 'bf', 'calorias', 'net')}

    def tendencia(serie, dias=28):
        vals = [x['valor'] for x in serie if x['valor'] is not None]
        if len(vals) < 8:
            return None
        recentes = vals[-dias:] if len(vals) > dias else vals
        metade = len(recentes) // 2
        if metade < 3:
            return None
        a = sum(recentes[:metade]) / metade
        b = sum(recentes[metade:]) / len(recentes[metade:])
        return round(b - a, 2)

    return {
        'de': datas[0], 'ate': datas[-1], 'n_dias': len(linhas),
        'cobertura': {c: sum(1 for r in linhas if _num(r.get(c)) is not None)
                      for c in NUM_COLS},
        'actual': {c: valor_actual(r7[c]) for c in r7},
        'tendencia_28d': {c: tendencia(r7[c]) for c in r7},
        'r7': r7,
    }
