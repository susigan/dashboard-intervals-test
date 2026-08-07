"""PMC — Performance Management Chart.

CTL/ATL/TSB a partir do icu_training_load, com a mesma logica do dashboard
original (tab_pmc.py):

  CTL = media exponencial a 42 dias da carga diaria
  ATL = media exponencial a 7 dias
  TSB = CTL - ATL   (calculado com os valores de ONTEM, ver abaixo)

Sem pandas: as series sao listas de dicts, calculadas em Python puro.
"""

from datetime import datetime, timedelta

CTL_DIAS = 42
ATL_DIAS = 7


def _ewm(valores, span):
    """Media exponencial, equivalente a pandas.ewm(span=N, adjust=False)."""
    alpha = 2.0 / (span + 1.0)
    out, anterior = [], None
    for v in valores:
        anterior = v if anterior is None else alpha * v + (1 - alpha) * anterior
        out.append(anterior)
    return out


def serie_diaria(sessoes, campo='tl', ate=None, desde=None):
    """Soma por dia, com os dias sem treino a zero.

    Os dias vazios contam: e o descanso que faz o ATL cair mais depressa
    que o CTL, e e dai que vem a forma.
    """
    if not sessoes:
        return []
    por_dia = {}
    for s in sessoes:
        d = (s.get('date') or '')[:10]
        if len(d) != 10:
            continue
        por_dia[d] = por_dia.get(d, 0.0) + float(s.get(campo) or 0)

    d0 = desde or min(por_dia)
    d1 = ate or datetime.now().strftime('%Y-%m-%d')
    ini = datetime.strptime(d0, '%Y-%m-%d')
    fim = datetime.strptime(d1, '%Y-%m-%d')
    dias = []
    while ini <= fim:
        k = ini.strftime('%Y-%m-%d')
        dias.append({'date': k, 'load': round(por_dia.get(k, 0.0), 1)})
        ini += timedelta(days=1)
    return dias


def calcular(sessoes, campo='tl', ate=None, desde=None):
    """CTL, ATL, TSB e ramp rate, dia a dia."""
    dias = serie_diaria(sessoes, campo, ate, desde)
    if not dias:
        return []
    cargas = [d['load'] for d in dias]
    ctl = _ewm(cargas, CTL_DIAS)
    atl = _ewm(cargas, ATL_DIAS)

    for i, d in enumerate(dias):
        d['ctl'] = round(ctl[i], 1)
        d['atl'] = round(atl[i], 1)
        # TSB de hoje usa os valores de ontem: a forma que trazes para o treino
        # de hoje nao pode incluir o treino de hoje.
        d['tsb'] = round((ctl[i - 1] - atl[i - 1]) if i else 0.0, 1)
        # ramp rate: quanto o CTL subiu nos ultimos 7 dias
        d['ramp'] = round(ctl[i] - ctl[i - 7], 1) if i >= 7 else 0.0
    return dias


def por_modalidade(sessoes, modalidades, campo='tl', ate=None, desde=None):
    """CTL por modalidade — para ver de onde vem a carga."""
    out = {}
    for m in modalidades:
        sub = [s for s in sessoes if s.get('type') == m]
        if not sub:
            continue
        serie = calcular(sub, campo, ate, desde)
        out[m] = [{'date': d['date'], 'ctl': d['ctl'], 'atl': d['atl']}
                  for d in serie]
    return out


def estado_forma(tsb):
    """Interpretacao do TSB. Os limites sao convencao do TrainingPeaks,
    nao uma verdade fisiologica — servem de referencia, nao de regra."""
    if tsb is None:
        return {'label': '—', 'cor': '#8b949e', 'nota': ''}
    if tsb > 25:
        return {'label': 'Muito fresco', 'cor': '#5DADE2',
                'nota': 'forma alta, mas fitness a cair se durar'}
    if tsb > 5:
        return {'label': 'Fresco', 'cor': '#2ECC71', 'nota': 'pronto para competir'}
    if tsb > -10:
        return {'label': 'Neutro', 'cor': '#F4D03F', 'nota': 'treino sustentavel'}
    if tsb > -30:
        return {'label': 'Em carga', 'cor': '#E67E22', 'nota': 'bloco de trabalho'}
    return {'label': 'Muito carregado', 'cor': '#E74C3C',
            'nota': 'risco se se prolongar'}


def alertas(dias, wellness=None):
    """Sinais que merecem atencao. Descritivos, nao prescritivos."""
    if not dias:
        return []
    fim = dias[-1]
    out = []

    if fim['ramp'] > 8:
        out.append({'nivel': 'aviso',
                    'texto': f"CTL subiu {fim['ramp']} em 7 dias. "
                             "Acima de ~8/semana costuma ser dificil de aguentar."})
    if fim['tsb'] < -30:
        out.append({'nivel': 'aviso',
                    'texto': f"TSB em {fim['tsb']}. Carga acumulada alta."})
    if fim['tsb'] > 25 and fim['ctl'] > 0:
        out.append({'nivel': 'info',
                    'texto': f"TSB em {fim['tsb']} — muito fresco. "
                             "Bom para competir, mau para manter fitness."})

    # HRV abaixo da media dos 60 dias, tres dias seguidos
    if wellness:
        hrvs = [(w['date'], w.get('hrv')) for w in wellness
                if w.get('hrv') is not None]
        if len(hrvs) >= 10:
            vals = [v for _, v in hrvs[-60:]]
            media = sum(vals) / len(vals)
            desv = (sum((v - media) ** 2 for v in vals) / len(vals)) ** 0.5
            ultimos = [v for _, v in hrvs[-3:]]
            if len(ultimos) == 3 and all(v < media - desv for v in ultimos):
                out.append({'nivel': 'aviso',
                            'texto': f"HRV abaixo de {media - desv:.0f} ha 3 dias "
                                     f"(media 60d: {media:.0f})."})
    return out
