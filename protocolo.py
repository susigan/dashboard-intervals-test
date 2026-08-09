"""Protocolo de testes de validacao.

Porque existe
-------------
A calibracao nos dados de treino falha por tres razoes que a estatistica
nao resolve:

  1. Causalidade invertida — treinas mais quando te sentes bem, por isso
     carga e HRV correlacionam-se no sentido errado.
  2. A CP por sessao nao mede capacidade, mede o que fizeste nesse dia.
  3. Um ponto de HRV por dia da o nivel, nao a trajectoria de recuperacao.

Um teste maximo padronizado resolve os dois primeiros: o estimulo passa a
ser fixado pelo protocolo em vez de escolhido consoante o estado, e o
resultado mede capacidade. Sao os pontos-ancora que faltam.

Como se deteta um teste
-----------------------
Nao e preciso marcar nada a mao. Uma sessao conta como teste valido para
uma duracao se o esforco nessa duracao chegar perto do teu melhor recente:
e o que distingue um esforco maximo de um treino qualquer.
"""

from datetime import datetime, timedelta

# Duracoes que servem de teste, com o que cada uma mede
TESTES = {
    300:  {'nome': '5 min max', 'mede': 'potencia aerobia maxima / VO2max',
           'intervalo_dias': 21},
    1200: {'nome': '20 min max', 'mede': 'limiar funcional / CP',
           'intervalo_dias': 28},
    60:   {'nome': '1 min max', 'mede': 'capacidade anaerobia / W prime',
           'intervalo_dias': 28},
}

# Percentagem do melhor recente a partir da qual o esforco conta como maximo
# Nao e preciso um teste formal. Uma prova, um bloco de intervalos duro ou
# uma saida em grupo servem — o que conta e ter ido perto do maximo naquela
# duracao. 95% e exigente; 92% apanha mais esforcos genuinos sem deixar
# entrar treinos moderados.
LIMIAR_ESFORCO = 0.92
JANELA_MELHOR = 180          # dias para "melhor recente"


def detectar_testes(curvas, limiar=LIMIAR_ESFORCO, janela=JANELA_MELHOR):
    """Sessoes em que houve esforco maximo, por modalidade e duracao.

    curvas: saida de db.load_power_curves() — uma entrada por sessao com
    secs, watts, date e type.
    """
    if not curvas:
        return {}

    por_mod = {}
    for c in curvas:
        por_mod.setdefault(c['type'], []).append(c)

    out = {}
    for mod, lista in por_mod.items():
        lista = sorted(lista, key=lambda x: x['date'])
        resultado = {}
        for secs, info in TESTES.items():
            pontos = []
            for c in lista:
                w = None
                for s, v in zip(c['secs'], c['watts']):
                    if s == secs and isinstance(v, (int, float)) and v > 0:
                        w = float(v)
                        break
                if w is not None:
                    pontos.append({'date': c['date'], 'watts': w,
                                   'activity_id': c['activity_id']})
            if len(pontos) < 5:
                continue

            # melhor dos ultimos `janela` dias antes de cada sessao
            testes = []
            for i, p in enumerate(pontos):
                limite = (datetime.strptime(p['date'], '%Y-%m-%d')
                          - timedelta(days=janela)).strftime('%Y-%m-%d')
                anteriores = [q['watts'] for q in pontos[:i + 1]
                              if q['date'] >= limite]
                if len(anteriores) < 3:
                    continue
                melhor = max(anteriores)
                frac = p['watts'] / melhor if melhor > 0 else 0
                if frac >= limiar:
                    testes.append({**p, 'pct_do_melhor': round(frac * 100, 1),
                                   'melhor_recente': round(melhor, 1)})

            if testes:
                ultimo = testes[-1]
                dias = (datetime.now()
                        - datetime.strptime(ultimo['date'], '%Y-%m-%d')).days
                resultado[secs] = {
                    'nome': info['nome'], 'mede': info['mede'],
                    'n_testes': len(testes),
                    'ultimo': ultimo,
                    'dias_desde_ultimo': dias,
                    'intervalo_recomendado': info['intervalo_dias'],
                    'em_atraso': dias > info['intervalo_dias'],
                    # todos, para calibrar; os ultimos, para mostrar
                    'testes': testes,
                    'recentes': testes[-12:],
                }
        if resultado:
            out[mod] = resultado
    return out


def sugerir(deteccao, modalidades_activas=None):
    """Que testes fazer a seguir, por ordem de urgencia."""
    sug = []
    for mod, durs in (deteccao or {}).items():
        if modalidades_activas and mod not in modalidades_activas:
            continue
        for secs, d in durs.items():
            if not d['em_atraso']:
                continue
            atraso = d['dias_desde_ultimo'] - d['intervalo_recomendado']
            sug.append({
                'modalidade': mod, 'secs': secs, 'nome': d['nome'],
                'mede': d['mede'],
                'dias_desde_ultimo': d['dias_desde_ultimo'],
                'atraso_dias': atraso,
                'ultimo_valor': d['ultimo']['watts'],
                'ultima_data': d['ultimo']['date'],
                'urgencia': ('alta' if atraso > d['intervalo_recomendado']
                             else 'media'),
            })
    sug.sort(key=lambda x: -x['atraso_dias'])
    return sug


def serie_ancora(deteccao, mod=None, secs=1200):
    """Serie de performance so com dias de teste.

    E esta que deve alimentar a calibracao em vez da CP de todas as sessoes:
    poucos pontos, mas cada um mede capacidade em vez de escolha de treino.
    """
    out = []
    for m, durs in (deteccao or {}).items():
        if mod and m != mod:
            continue
        d = durs.get(secs)
        if not d:
            continue
        for t in d['testes']:
            out.append({'date': t['date'], 'valor': t['watts'],
                        'modalidade': m, 'activity_id': t['activity_id']})
    out.sort(key=lambda x: x['date'])
    return out


def robustez(deteccao, secs=1200):
    """Como o sistema se degrada quando faltam testes.

    Um teste falhado nao parte nada: perde-se um ponto-ancora. O que importa
    e quantos pontos restam e ha quanto tempo foi o ultimo. A resposta diz
    exactamente isso, por modalidade.
    """
    out = {}
    for mod, durs in (deteccao or {}).items():
        d = durs.get(secs)
        if not d:
            continue
        testes = d.get('testes') or []
        if len(testes) < 2:
            out[mod] = {'n': len(testes), 'estado': 'insuficiente'}
            continue

        # intervalo tipico entre testes, nos dados reais
        dias = []
        for a, b in zip(testes[:-1], testes[1:]):
            da = datetime.strptime(a['date'], '%Y-%m-%d')
            db_ = datetime.strptime(b['date'], '%Y-%m-%d')
            dias.append((db_ - da).days)
        dias_ord = sorted(dias)
        mediana = dias_ord[len(dias_ord) // 2]

        n = len(testes)
        if n >= 30:
            estado, nota = 'bom', 'da para calibrar contra performance real'
        elif n >= 12:
            estado, nota = ('parcial',
                            f'{n} pontos — da para tendencia, ainda pouco para '
                            'calibrar (~30)')
        else:
            estado, nota = 'insuficiente', f'so {n} pontos'

        out[mod] = {
            'n': n, 'estado': estado, 'nota': nota,
            'intervalo_mediano_dias': mediana,
            'primeiro': testes[0]['date'], 'ultimo': testes[-1]['date'],
            'dias_desde_ultimo': d['dias_desde_ultimo'],
            'perder_um_teste': (
                f'sem consequencia imediata: com {n} pontos e mediana de '
                f'{mediana}d entre eles, falhar um adia a actualizacao mas '
                'nao invalida nada'),
        }
    return out


def cobertura(deteccao):
    """Ha testes suficientes para calibrar? Quantos e com que frequencia."""
    total = 0
    detalhe = {}
    for mod, durs in (deteccao or {}).items():
        for secs, d in durs.items():
            total += d['n_testes']
            detalhe.setdefault(mod, {})[d['nome']] = {
                'n': d['n_testes'],
                'ultimo': d['ultimo']['date'],
                'dias': d['dias_desde_ultimo'],
                'em_atraso': d['em_atraso'],
            }
    if total >= 30:
        veredicto = ('ha testes suficientes para calibrar contra performance '
                     'real em vez da CP de cada sessao')
    elif total >= 10:
        veredicto = (f'{total} testes — da para ver tendencia, ainda pouco '
                     'para calibrar (precisa de ~30)')
    else:
        veredicto = (f'so {total} testes detectados. Sem esforcos maximos '
                     'regulares nao ha ancora de performance; e por isso que '
                     'a calibracao contra CP falha.')
    return {'total': total, 'por_modalidade': detalhe, 'veredicto': veredicto}
