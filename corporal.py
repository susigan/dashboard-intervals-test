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

import statistics
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
    """Junta as duas fontes e limpa, seguindo as regras do dashboard.

    Peso: so a aba diaria (Respostas ao formulario 1, coluna Peso). O peso
    do Consolidado_Comida NAO e usado, e nao ha substituicao automatica:
    dia sem peso diario fica sem peso.
    BF: formulario de wellness tem prioridade (inalterado).
    Calorias, net e macros: Consolidado_Comida (inalterado).
    """
    if not corporal:
        return []

    hoje = datetime.now().strftime('%Y-%m-%d')

    # BF do formulario de wellness tem prioridade
    bf_well = {}
    # peso diario; datas futuras ficam de fora
    peso_well = {}
    for w in (wellness or []):
        v = _num(w.get('fat'))
        if v is not None:
            bf_well[w['date']] = v
        p = _num(w.get('peso'))
        if p is not None and w['date'] <= hoje:
            peso_well[w['date']] = p

    linhas = []
    for r in corporal:
        d = dict(r)
        d['peso'] = peso_well.get(d['date'])
        if d['date'] in bf_well:
            d['bf'] = bf_well[d['date']]
        linhas.append(d)

    # tambem entram dias que so existem no wellness (BF ou peso)
    existentes = {r['date'] for r in linhas}
    for data in sorted(set(bf_well) | set(peso_well)):
        if data not in existentes:
            linhas.append({'date': data, 'peso': peso_well.get(data),
                           'bf': bf_well.get(data)})
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
    """Correlacao de Spearman e p-valor aproximado (normal sobre a estatistica t)."""
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


def lag_calorico(cal_r7, var_r7, lags_semanas=(0, 1, 2, 3)):
    """Ao fim de quantas semanas as calorias se reflectem no peso ou no BF.

    Um ponto por semana: calorias = media semanal, peso/BF = mediana semanal.
    Testa lags de 0 a 3 semanas; p ajustado por Bonferroni (n de lags).
    Fica com o lag mais forte com p ajustado < 0.10.
    """
    def semanal(serie, agr):
        por = {}
        for r in serie:
            v = _num(r.get('valor'))
            if v is not None:
                por.setdefault(_segunda(r['date']), []).append(v)
        return {s: agr(v) for s, v in por.items()}

    cal_s = semanal(cal_r7, statistics.mean)
    var_s = semanal(var_r7, statistics.median)

    resultados, melhor = [], {'lag': None, 'r': None, 'p': None, 'n': None}
    for L in lags_semanas:
        xs, ys = [], []
        for s, y in var_s.items():
            ref = (datetime.strptime(s, '%Y-%m-%d') - timedelta(weeks=L)).strftime('%Y-%m-%d')
            if ref in cal_s:
                xs.append(cal_s[ref])
                ys.append(y)
        item = {'lag': 7 * L, 'n': len(xs), 'r': None, 'p': None}
        if len(xs) >= MIN_PARES_KJ:
            r, p = _spearman(xs, ys)
            if r is not None:
                item.update(r=r, p=round(min(1.0, p * len(lags_semanas)), 4))
        resultados.append(item)
        if item['p'] is not None and item['p'] < 0.10:
            if melhor['r'] is None or abs(item['r']) > abs(melhor['r']):
                melhor = dict(item)
    return {'melhor': melhor, 'todos': resultados}


def kj_diario(pares, ate=None):
    """kJ de trabalho por dia (ver soma_diaria)."""
    return soma_diaria(pares, ate, 'kj')


KJ_LAG_SEMANAS = (0, 1, 2, 3, 4)
MIN_PARES_KJ = 12


def _segunda(d):
    """Segunda-feira da semana de d (YYYY-MM-DD)."""
    dt = datetime.strptime(d, '%Y-%m-%d')
    return (dt - timedelta(days=dt.weekday())).strftime('%Y-%m-%d')


def soma_diaria(pares, ate=None, chave='kj'):
    """Soma diaria de atividade. pares: [(data, valor)], uma entrada por atividade.

    Os dias entre a primeira atividade e 'ate' (hoje por omissao) sem
    atividade contam como 0: pressupoe que todas as sessoes estao na base.
    """
    soma = {}
    for d, kj in pares:
        if d and kj:
            soma[d] = soma.get(d, 0.0) + kj
    if not soma:
        return []
    fim = datetime.strptime(ate or datetime.now().strftime('%Y-%m-%d'), '%Y-%m-%d').date()
    dia = datetime.strptime(min(soma), '%Y-%m-%d').date()
    out = []
    while dia <= fim:
        k = dia.isoformat()
        out.append({'date': k, chave: round(soma.get(k, 0.0), 1)})
        dia += timedelta(days=1)
    return out


def lag_kj(kj_dia, linhas, lags=KJ_LAG_SEMANAS):
    """Spearman entre o kJ semanal (defasado 0 a 4 semanas) e o peso e a
    gordura semanais (mediana). Um ponto por semana, para nao usar a
    autocorrelacao diaria. p ajustado por Bonferroni (n de lags testados).
    """
    kj_sem = {}
    for r in kj_dia:
        s = _segunda(r['date'])
        kj_sem[s] = kj_sem.get(s, 0.0) + r['kj']

    out = {}
    for campo in ('peso', 'bf'):
        por_sem = {}
        for r in linhas:
            v = _num(r.get(campo))
            if v is not None:
                por_sem.setdefault(_segunda(r['date']), []).append(v)
        alvo = {s: statistics.median(v) for s, v in por_sem.items()}

        resultados = []
        for L in lags:
            xs, ys = [], []
            for s, y in alvo.items():
                kj_ref = (datetime.strptime(s, '%Y-%m-%d') - timedelta(weeks=L)).strftime('%Y-%m-%d')
                if kj_ref in kj_sem:
                    xs.append(kj_sem[kj_ref])
                    ys.append(y)
            item = {'lag_semanas': L, 'lag_dias': 7 * L, 'n': len(xs),
                    'r': None, 'p': None, 'p_ajust': None}
            if len(xs) >= MIN_PARES_KJ:
                r, p = _spearman(xs, ys)
                if r is not None:
                    item.update(r=r, p=p, p_ajust=round(min(1.0, p * len(lags)), 4))
            resultados.append(item)
        out[campo] = resultados
    return out


CRUZ_LAGS_SEM = (0, 1, 2, 3, 4)
CRUZ_N_PERM = 499
CRUZ_N_REFINO = 4999  # so para pares com p <= 0.05: resolucao necessaria para Bonferroni
CRUZ_MIN_PARES = 30
_CRUZ_CACHE = {}


def _rank_medio(a):
    """Postos com empates resolvidos pela media (1-based)."""
    import numpy as np
    a = np.asarray(a, float)
    _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    medio = np.cumsum(cnt) - (cnt - 1) / 2.0
    return medio[inv]


def _corr(a, b):
    """Pearson sobre arrays (aqui sobre postos = Spearman). None se constante."""
    sa, sb = a.std(), b.std()
    if sa == 0 or sb == 0:
        return None
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))


def _p_permutacao(xs, ys, r_obs, rng, n_perm):
    """p por deslocamento circular de y: preserva a autocorrelacao de cada serie."""
    import numpy as np
    rx, ry = _rank_medio(xs), _rank_medio(ys)
    n, cont = len(xs), 0
    for _ in range(n_perm):
        k = int(rng.integers(8, n - 8))
        r = _corr(rx, np.roll(ry, k))
        if r is not None and abs(r) >= abs(r_obs) - 1e-12:
            cont += 1
    return (cont + 1) / (n_perm + 1)


def _serie_semanal(pares, agr):
    """{segunda: valor agregado} a partir de pares (data, valor)."""
    por = {}
    for d, v in pares:
        if d and v is not None:
            por.setdefault(_segunda(d), []).append(v)
    return {s: agr(vs) for s, vs in por.items()}


def correlacoes_cruzadas(kj_dia, horas_dia, cal_dia, linhas,
                         lags=CRUZ_LAGS_SEM, n_perm=CRUZ_N_PERM, n_refino=CRUZ_N_REFINO):
    """Correlacao cruzada semanal entre kJ, horas, calorias, peso e gordura.

    Cada variavel e agregada por semana (kJ e horas: soma; calorias: media;
    peso e gordura: mediana). Para cada par ordenado (causa -> efeito) e lag
    de 0 a 4 semanas: Spearman com p por permutacao em bloco e versao sem
    tendencia linear. p ajustado por Bonferroni sobre todos os testes.
    Determinista (semente fixa) e em cache: o mesmo dado da sempre o mesmo resultado.
    """
    import hashlib
    import json
    import numpy as np

    series = {
        'kJ': _serie_semanal([(r['date'], r.get('kj')) for r in kj_dia], sum),
        'Horas': _serie_semanal([(r['date'], r.get('horas')) for r in horas_dia], sum),
        'Calorias': _serie_semanal([(r['date'], _num(r.get('calorias'))) for r in cal_dia], statistics.mean),
        'Peso': _serie_semanal([(r['date'], _num(r.get('peso'))) for r in linhas], statistics.median),
        'Gordura': _serie_semanal([(r['date'], _num(r.get('bf'))) for r in linhas], statistics.median),
    }
    series = {k: {s: float(v) for s, v in x.items() if v is not None} for k, x in series.items()}

    chave = hashlib.md5(json.dumps([series, list(lags), n_perm, n_refino], sort_keys=True).encode()).hexdigest()
    if chave in _CRUZ_CACHE:
        return _CRUZ_CACHE[chave]

    testes = []
    for a, b in ((a, b) for a in series for b in series if a != b):
        for L in lags:
            pares = []
            for s, y in series[b].items():
                ref = (datetime.strptime(s, '%Y-%m-%d') - timedelta(weeks=L)).strftime('%Y-%m-%d')
                if ref in series[a]:
                    pares.append((series[a][ref], y, s))
            if len(pares) < CRUZ_MIN_PARES:
                continue
            pares.sort(key=lambda t: t[2])
            xs = np.array([p[0] for p in pares])
            ys = np.array([p[1] for p in pares])
            t = np.array([datetime.strptime(p[2], '%Y-%m-%d').toordinal() / 7.0 for p in pares])
            rng = np.random.default_rng(42)
            r = _corr(_rank_medio(xs), _rank_medio(ys))
            if r is None:
                continue
            p = _p_permutacao(xs, ys, r, rng, n_perm)
            if p <= 0.05:
                p = _p_permutacao(xs, ys, r, rng, n_refino)
            xd = xs - np.polyval(np.polyfit(t, xs, 1), t)
            yd = ys - np.polyval(np.polyfit(t, ys, 1), t)
            rd = _corr(_rank_medio(xd), _rank_medio(yd))
            pd_ = _p_permutacao(xd, yd, rd, rng, n_perm) if rd is not None else None
            if pd_ is not None and pd_ <= 0.05:
                pd_ = _p_permutacao(xd, yd, rd, rng, n_refino)
            testes.append({'causa': a, 'efeito': b, 'lag_sem': L, 'n': len(pares),
                           'r': round(r, 3), 'p': round(p, 4),
                           'r_sem_tend': round(rd, 3) if rd is not None else None,
                           'p_sem_tend': round(pd_, 4) if pd_ is not None else None})

    T = len(testes)
    for t_ in testes:
        t_['p_ajust'] = round(min(1.0, t_['p'] * T), 4)
        t_['p_sem_tend_ajust'] = (round(min(1.0, t_['p_sem_tend'] * T), 4)
                                  if t_['p_sem_tend'] is not None else None)
    testes.sort(key=lambda t_: t_['p'])
    out = {'n_testes': T, 'semanas': len({s for x in series.values() for s in x}),
           'cobertura': {k: len(x) for k, x in series.items()}, 'linhas': testes}
    if len(_CRUZ_CACHE) > 8:
        _CRUZ_CACHE.clear()
    _CRUZ_CACHE[chave] = out
    return out


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


def resumo(linhas):
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
