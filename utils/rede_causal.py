"""utils/rede_causal.py — rede causal entre canais fisiológicos.

Adaptado do PhysioNexus (Evan Peikon). O metodo e' o mesmo: correlacao para
pre-seleccionar pares, Granger para lhes dar direccao, e uma rede onde os
nos com mais saidas sao causas e os com mais entradas sao efeitos.

TRES DIFERENCAS face ao original, todas por causa dos dados deste atleta:

1. DIFERENCIACAO
   O Granger pressupoe series estacionarias. Numa sessao com carga
   crescente, tudo sobe com o tempo e qualquer par passa no teste por
   tendencia comum. Testa-se a estacionariedade e diferencia-se o que
   falhar, uma vez, antes de qualquer teste.

2. CONDICIONAMENTO AOS WATTS
   O protocolo e' causa comum de tudo. Os watts sobem por decisao do
   atleta, e o SmO2, a FC e a respiracao respondem todos a isso. Um teste
   entre SmO2 e FC encontraria ligacao, mas quem a produz e' o protocolo.
   A pergunta util e' outra: o SmO2 acrescenta poder preditivo sobre a FC
   PARA ALEM do que os watts ja' explicam? E' isso que se testa.

3. CORRECCAO PARA COMPARACOES MULTIPLAS
   Com 11 canais sao 110 pares dirigidos. A 5%, esperam-se cinco arestas
   falsas so' por acaso. Aplica-se Benjamini-Hochberg sobre todos os p
   antes de decidir o que fica na rede.

O resultado continua a nao ser causalidade no sentido forte. Granger mede
precedencia preditiva: se A ajuda a prever B, A precede B. Num sistema
fisiologico com um controlador comum, isso e' uma pista, nao uma prova.
"""

# Canais MECANICOS: sao a entrada do sistema, escolhida pelo atleta. Que os
# watts causem alteracoes fisiologicas nao e' descoberta -- e' o protocolo.
# Ficam de fora da rede como nos e entram so' como variavel de controlo.
#
# O que interessa e' o que se passa ENTRE as fisiologicas depois de a
# entrada estar descontada.
MECANICOS = ('watts', 'velocity_smooth', 'torque', 'cadence', 'distance',
             'Speed', 'power')

# A que sistema pertence cada canal. Serve para somar o F que sai de cada
# sistema e ver qual esta a comandar a resposta.
SISTEMAS = {
    'smo2': 'periferico', 'thb': 'periferico',
    'o2hb': 'periferico', 'hhb': 'periferico',
    'heartrate': 'cardiaco',
    'respiration': 'respiratorio',
    'dfa_a1': 'autonomico',
}

# Canais MECANICOS: sao decisao do atleta ou consequencia directa dela, nao
# resposta fisiologica. Entram como CONTROLO -- todos, nao so' os watts --
# e nunca como origem nem destino de uma aresta.
#
# A razao e' simples: "a potencia precede a subida da FC" nao e' um achado,
# e' a definicao de treinar. O que interessa e o que acontece ENTRE as
# respostas fisiologicas, depois de descontado o que a mecanica ja' explica.
MECANICOS = ('watts', 'velocity_smooth', 'cadence', 'torque', 'speed',
             'distance', 'altitude')

# Canais FISIOLOGICOS, por sistema. Serve para classificar o limitador:
# se as arestas partem sobretudo do sistema periferico, a limitacao esta na
# extraccao muscular; se partem do central, no transporte.
SISTEMAS = {
    'smo2': 'periferico', 'thb': 'periferico',
    'o2hb': 'periferico', 'hhb': 'periferico',
    'heartrate': 'cardiaco',
    'respiration': 'respiratorio',
    'dfa_a1': 'autonomico',
}

MAX_LAG = 5

# Quantas vezes o F de um sentido tem de superar o do inverso para se
# considerar que a direccao ficou decidida. Nao e' um valor de literatura:
# e' um criterio de decisao, e por isso e' parametro.
RACIO_DOMINANTE = 3.0
CORR_MINIMA = 0.30
P_MAXIMO = 0.05


def _diferencas(v):
    return [v[i] - v[i - 1] for i in range(1, len(v))]


# Valores criticos do teste de Dickey-Fuller aumentado, especificacao com
# constante ("c"). Sao constantes da distribuicao do proprio teste, nao
# normas de populacao: a estatistica nao segue uma t normal porque sob a
# hipotese nula a serie tem raiz unitaria.
#
# Aproximacao de MacKinnon (1994) para amostra grande, que e' o caso aqui --
# uma sessao a 1 Hz tem milhares de pontos.
ADF_CRITICOS = {0.01: -3.43, 0.05: -2.86, 0.10: -2.57}


def adf_estacionaria(v, alfa=0.05, lags=None):
    """Teste ADF com constante, implementado directamente.

    Regride Dy_t sobre y_{t-1}, uma constante e p desfasamentos de Dy. Se o
    coeficiente de y_{t-1} for suficientemente negativo, rejeita-se a raiz
    unitaria e a serie e' considerada estacionaria.

    Escrito aqui em vez de importado: a statsmodels rebentava com
    "deprecate_kwarg() missing 1 required positional argument" por conflito
    de versoes, e era a unica coisa que se lhe pedia. O Granger deste
    modulo ja' era proprio.
    """
    vs = [float(x) for x in v if x is not None]
    n = len(vs)
    if n < 30:
        return {'ok': False, 'motivo': f'serie com {n} pontos'}
    try:
        import numpy as np
    except ImportError:
        return {'ok': False, 'motivo': 'numpy indisponivel'}

    y = np.asarray(vs)
    dy = np.diff(y)
    if lags is None:
        # regra de Schwert, o valor por omissao da maioria das
        # implementacoes
        lags = int(min(12 * (n / 100.0) ** 0.25, n // 4, 24))
    lags = max(0, int(lags))

    m = len(dy) - lags
    if m < 20:
        return {'ok': False, 'motivo': 'pontos insuficientes apos desfasar'}

    cols = [y[lags:lags + m], np.ones(m)]
    for k in range(1, lags + 1):
        cols.append(dy[lags - k:lags - k + m])
    A = np.column_stack(cols)
    alvo = dy[lags:lags + m]
    try:
        beta, *_ = np.linalg.lstsq(A, alvo, rcond=None)
    except Exception as e:
        return {'ok': False, 'motivo': f'{type(e).__name__}: {e}'}

    resid = alvo - A @ beta
    gl = m - A.shape[1]
    if gl <= 0:
        return {'ok': False, 'motivo': 'graus de liberdade insuficientes'}
    s2 = float(resid @ resid) / gl
    try:
        cov = s2 * np.linalg.pinv(A.T @ A)
        se = float(np.sqrt(max(cov[0, 0], 1e-30)))
    except Exception:
        return {'ok': False, 'motivo': 'matriz singular'}
    if se <= 0:
        return {'ok': False, 'motivo': 'erro padrao nulo'}

    t = float(beta[0]) / se
    critico = ADF_CRITICOS.get(alfa, ADF_CRITICOS[0.05])
    return {'ok': True, 'estatistica': round(t, 3), 'critico': critico,
            'lags': lags, 'n': n,
            'estacionaria': t < critico, 'metodo': 'ADF (proprio)'}


def preparar(canais, diferenciar=True):
    """Alinha comprimentos, remove constantes e diferencia o que precisa."""
    nomes = [k for k, v in canais.items() if v]
    if not nomes:
        return {}, {}
    n = min(len(canais[k]) for k in nomes)
    if n < 30:
        return {}, {'erro': f'series com so {n} pontos'}

    # Duas versoes de cada serie: a original, para a correlacao que
    # pre-selecciona os pares, e a diferenciada, para o Granger. Filtrar
    # pares pela correlacao da serie JA diferenciada nao funciona -- a
    # diferenciacao remove exactamente a componente lenta que produz a
    # correlacao, e num teste com causalidade conhecida nao sobrou um unico
    # par para testar.
    saida, originais, diag = {}, {}, {}
    for k in nomes:
        v = list(canais[k])[:n]
        # buracos interpolados: o Granger nao aceita None
        validos = [i for i, x in enumerate(v) if x is not None]
        if len(validos) < n * 0.5:
            diag[k] = {'excluido': 'mais de metade em falta'}
            continue
        for i in range(n):
            if v[i] is None:
                ant = max([j for j in validos if j < i], default=None)
                seg = min([j for j in validos if j > i], default=None)
                if ant is None:
                    v[i] = v[seg]
                elif seg is None:
                    v[i] = v[ant]
                else:
                    v[i] = v[ant] + (v[seg] - v[ant]) * (i - ant) / (seg - ant)
        if max(v) == min(v):
            diag[k] = {'excluido': 'constante'}
            continue
        originais[k] = list(v)
        est = adf_estacionaria(v)
        diag[k] = {'estacionaria_original': est.get('estacionaria'),
                   'teste': est.get('metodo'), 'p_adf': est.get('p')}
        saida[k] = v

    # Ou se diferenciam TODAS ou nenhuma. Misturar series diferenciadas com
    # nao diferenciadas no mesmo teste compara coisas em unidades
    # diferentes -- uma em nivel, outra em variacao por segundo.
    precisam = [k for k in saida
                if diag[k].get('estacionaria_original') is False]
    if diferenciar and precisam:
        for k in list(saida):
            saida[k] = _diferencas(saida[k])
            diag[k]['diferenciada'] = True
            diag[k]['estacionaria_apos'] = \
                adf_estacionaria(saida[k]).get('estacionaria')
        diag['_todas_diferenciadas'] = {
            'motivo': (f'{len(precisam)} de {len(saida)} series nao eram '
                       'estacionarias; diferenciadas todas para ficarem '
                       'comparaveis')}
    else:
        for k in saida:
            diag[k]['diferenciada'] = False

    if saida:
        m = min(len(v) for v in saida.values())
        saida = {k: v[-m:] for k, v in saida.items()}
        originais = {k: v[-m:] for k, v in originais.items() if k in saida}
    return saida, originais, diag


def correlacao(a, b):
    n = min(len(a), len(b))
    if n < 3:
        return None
    ma = sum(a[:n]) / n
    mb = sum(b[:n]) / n
    sa = sum((x - ma) ** 2 for x in a[:n])
    sb = sum((x - mb) ** 2 for x in b[:n])
    if sa <= 0 or sb <= 0:
        return None
    sab = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return sab / (sa * sb) ** 0.5


def granger(x, y, max_lag=MAX_LAG, controlo=None):
    """x Granger-causa y? Com 'controlo', condicionado a essa serie.

    Sem controlo, compara o modelo de y sobre o proprio passado com o
    modelo de y sobre o proprio passado mais o de x. Com controlo, ambos
    os modelos incluem o passado do controlo, e a pergunta passa a ser se
    x acrescenta alguma coisa alem dele.
    """
    try:
        import numpy as np
    except ImportError:
        return {'ok': False, 'motivo': 'numpy indisponivel'}

    # controlo pode ser uma serie ou varias
    ctrls = []
    if controlo is not None:
        ctrls = controlo if isinstance(controlo, (list, tuple)) and \
            controlo and isinstance(controlo[0], (list, tuple)) else [controlo]
    n = min([len(x), len(y)] + [len(c) for c in ctrls])
    if n < 40:
        return {'ok': False, 'motivo': f'so {n} pontos'}
    x, y = np.asarray(x[:n], float), np.asarray(y[:n], float)
    cs = [np.asarray(c[:n], float) for c in ctrls]

    melhor = None
    for lag in range(1, max_lag + 1):
        m = n - lag
        if m < 4 * (2 * lag + 2):
            break
        alvo = y[lag:]
        cols_r = [y[lag - k:n - k] for k in range(1, lag + 1)]
        for c in cs:
            cols_r += [c[lag - k:n - k] for k in range(1, lag + 1)]
        cols_c = cols_r + [x[lag - k:n - k] for k in range(1, lag + 1)]

        def _rss(cols):
            A = np.column_stack(cols + [np.ones(m)])
            try:
                beta, *_ = np.linalg.lstsq(A, alvo, rcond=None)
            except Exception:
                return None
            r = alvo - A @ beta
            return float(r @ r), A.shape[1]

        r_res = _rss(cols_r)
        r_com = _rss(cols_c)
        if not r_res or not r_com:
            continue
        rss_r, k_r = r_res
        rss_c, k_c = r_com
        gl = k_c - k_r
        den = m - k_c
        if gl <= 0 or den <= 0 or rss_c <= 0:
            continue
        f = ((rss_r - rss_c) / gl) / (rss_c / den)
        try:
            from scipy.stats import f as fdist
            p = float(1 - fdist.cdf(f, gl, den))
        except ImportError:
            p = 1.0 if f < 1 else 0.5 / max(f, 1)
        if melhor is None or f > melhor['f']:
            melhor = {'f': round(float(f), 2), 'p': round(p, 5), 'lag': lag}
    if melhor is None:
        return {'ok': False, 'motivo': 'nenhum lag ajustavel'}
    return {'ok': True, **melhor, 'n_controlos': len(cs)}


def benjamini_hochberg(ps, alfa=P_MAXIMO):
    """Limiar de p corrigido para comparacoes multiplas."""
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


def rede(canais, controlo='watts', max_lag=MAX_LAG, corr_minima=CORR_MINIMA,
         alfa=P_MAXIMO, diferenciar=True, condicionar=True):
    """Rede causal entre os canais.

    Devolve as arestas com F, p, p corrigido e lag, e o grau de cada no.
    """
    dados, originais, diag = preparar(canais, diferenciar=diferenciar)
    if len(dados) < 2:
        return {'ok': False, 'motivo': 'menos de dois canais utilizaveis',
                'diagnostico': diag}

    # Todos os mecanicos entram como controlo, nao so' os watts. Controlar
    # so' a potencia deixava a cadencia e o torque a explicar sozinhos
    # relacoes que sao da mecanica -- sobretudo no remo, onde a potencia
    # por si nao descreve o gesto.
    mecanicos = [k for k in dados if k in MECANICOS]
    if controlo and controlo in dados and controlo not in mecanicos:
        mecanicos.append(controlo)
    ctrl = [dados[k] for k in mecanicos] if (condicionar and mecanicos) else None
    nomes = [k for k in dados if k not in mecanicos]
    excluidos_mecanicos = mecanicos
    if len(nomes) < 2:
        return {'ok': False,
                'motivo': (f'so {len(nomes)} canais fisiologicos utilizaveis '
                           f'({", ".join(nomes) or "nenhum"}); a rede precisa '
                           'de pelo menos dois'),
                'mecanicos_excluidos': mecanicos, 'diagnostico': diag}

    pares, ps = [], []
    for a in nomes:
        for b in nomes:
            if a == b:
                continue
            # correlacao na serie ORIGINAL, Granger na diferenciada
            r = correlacao(originais.get(a, dados[a]),
                           originais.get(b, dados[b]))
            if r is None or abs(r) < corr_minima:
                continue
            g = granger(dados[a], dados[b], max_lag=max_lag, controlo=ctrl)
            if not g.get('ok'):
                continue
            pares.append({'de': a, 'para': b, 'correlacao': round(r, 3),
                          'sinal': '+' if r > 0 else '-',
                          'sistema_de': SISTEMAS.get(a, 'outro'),
                          'sistema_para': SISTEMAS.get(b, 'outro'),
                          'f': g['f'], 'p': g['p'], 'lag': g['lag']})
            ps.append(g['p'])

    corte, aceites = benjamini_hochberg(ps, alfa)
    for i, e in enumerate(pares):
        e['significativa'] = aceites[i]

    arestas = [e for e in pares if e['significativa']]

    # Quando os dois sentidos passam, o Granger nao decidiu a direccao:
    # significa que cada serie ajuda a prever a outra, o que acontece com
    # autocorrelacao residual ou com um terceiro factor nao controlado.
    # Chamar-lhe causalidade nos dois sentidos seria inventar. Marca-se
    # como indecisa e fica fora do calculo de fontes e sumidouros -- mas
    # continua visivel, porque a indecisao tambem e' informacao.
    chaves = {(e['de'], e['para']) for e in arestas}
    for e in arestas:
        e['bidireccional'] = (e['para'], e['de']) in chaves
        if e['bidireccional']:
            gemea = next((x for x in arestas
                          if x['de'] == e['para'] and x['para'] == e['de']), None)
            e['f_inverso'] = gemea['f'] if gemea else None
            e['racio_f'] = (round(e['f'] / gemea['f'], 2)
                            if gemea and gemea['f'] else None)

    # Quando os dois sentidos passam mas um domina claramente -- F pelo
    # menos RACIO_DOMINANTE vezes maior -- trata-se o dominante como
    # dirigido. Na simulacao com causalidade conhecida, smo2 -> respiration
    # tinha racio 9.0 e era a relacao verdadeira; o sentido inverso passava
    # so' por autocorrelacao residual. Ficar-se pela indecisao em todos os
    # casos deitava fora informacao que os dados tem.
    dirigidas, indecisas = [], []
    for e in arestas:
        if not e['bidireccional']:
            e['direccao'] = 'unica'
            dirigidas.append(e)
        elif (e.get('racio_f') or 0) >= RACIO_DOMINANTE:
            e['direccao'] = 'dominante'
            dirigidas.append(e)
        elif (e.get('racio_f') or 1) <= 1 / RACIO_DOMINANTE:
            e['direccao'] = 'dominada'
            indecisas.append(e)
        else:
            e['direccao'] = 'ambigua'
            indecisas.append(e)

    graus = {}
    for k in nomes:
        graus[k] = {'saidas': sum(1 for e in dirigidas if e['de'] == k),
                    'entradas': sum(1 for e in dirigidas if e['para'] == k),
                    'indecisas': sum(1 for e in indecisas
                                     if k in (e['de'], e['para']))}
    ordenado = sorted(graus.items(),
                      key=lambda kv: kv[1]['saidas'] - kv[1]['entradas'],
                      reverse=True)

    # ── razao de controlo fisiologico ────────────────────────────────
    # Que fraccao do F total sai de cada sistema. Um sistema que so' recebe
    # esta a compensar; o que emite esta a comandar. Nao e' uma medida
    # validada -- e' uma leitura da rede, e depende de que canais existem
    # na sessao. Sem DFA-a1 gravado, o autonomico da 0% por ausencia, nao
    # por inactividade.
    por_sistema = {}
    for e in dirigidas:
        sis = SISTEMAS.get(e['de'])
        if not sis:
            continue
        d2 = por_sistema.setdefault(sis, {'f_saida': 0.0, 'f_entrada': 0.0,
                                          'arestas': 0})
        d2['f_saida'] += e['f']
        d2['arestas'] += 1
    for e in dirigidas:
        sis = SISTEMAS.get(e['para'])
        if sis:
            por_sistema.setdefault(sis, {'f_saida': 0.0, 'f_entrada': 0.0,
                                         'arestas': 0})['f_entrada'] += e['f']
    total = sum(v['f_saida'] for v in por_sistema.values())
    pcr = {}
    for sis in ('cardiaco', 'periferico', 'respiratorio', 'autonomico'):
        v = por_sistema.get(sis)
        presente = any(SISTEMAS.get(k) == sis for k in nomes)
        pcr[sis] = {
            'pct': (round(v['f_saida'] / total * 100, 1)
                    if v and total else 0.0),
            'f_saida': round(v['f_saida'], 1) if v else 0.0,
            'f_entrada': round(v['f_entrada'], 1) if v else 0.0,
            'canais_presentes': presente,
            'nota': (None if presente
                     else 'nenhum canal deste sistema na sessao'),
        }

    dominante = max(pcr, key=lambda k: pcr[k]['pct']) if total else None
    limitador = None
    if dominante and pcr[dominante]['pct'] > 0:
        top = max(dirigidas, key=lambda e: e['f']) if dirigidas else None
        rotulos = {
            'periferico': ('Periférico / fornecimento',
                           'a extracção muscular precede a resposta '
                           'cardíaca: o músculo comanda, o coração compensa'),
            'cardiaco': ('Central / débito',
                         'a resposta cardíaca precede a muscular: o '
                         'fornecimento comanda'),
            'respiratorio': ('Ventilatório',
                             'a ventilação precede as outras respostas'),
            'autonomico': ('Autonómico',
                           'a modulação autonómica precede as outras'),
        }
        nome, expl = rotulos.get(dominante, (dominante, ''))
        limitador = {
            'sistema': dominante, 'rotulo': nome, 'leitura': expl,
            'pct': pcr[dominante]['pct'],
            'aresta_dominante': (f"{top['de']} → {top['para']} "
                                 f"(F={top['f']}, lag {top['lag']}s)"
                                 if top else None),
            'confianca': ('alta' if pcr[dominante]['pct'] >= 60 else
                          'media' if pcr[dominante]['pct'] >= 40 else 'baixa'),
        }

    # ── quem controla quem ────────────────────────────────────────────
    # Peso de cada sistema pelo F das arestas que dele PARTEM, descontando
    # as que nele CHEGAM. Um sistema que so' recebe esta a responder; um que
    # so' emite esta a impor o ritmo.
    #
    # Usa-se o F e nao a contagem: uma aresta com F=169 e outra com F=17
    # nao valem o mesmo, e contar arestas trataria as duas por igual.
    peso = {}
    for e in dirigidas:
        sa = SISTEMAS.get(e['de'], 'outro')
        sb = SISTEMAS.get(e['para'], 'outro')
        peso.setdefault(sa, {'emite': 0.0, 'recebe': 0.0})['emite'] += e['f']
        peso.setdefault(sb, {'emite': 0.0, 'recebe': 0.0})['recebe'] += e['f']
    total = sum(v['emite'] for v in peso.values()) or 1.0
    controlo_pct = {k: round(v['emite'] / total * 100, 1)
                    for k, v in peso.items()}
    liquido = {k: round(v['emite'] - v['recebe'], 1) for k, v in peso.items()}

    dominante = max(liquido, key=liquido.get) if liquido else None
    if dominante and liquido[dominante] <= 0:
        dominante = None
    leitura = {
        'periferico': ('a extraccao muscular precede o resto: e o musculo '
                       'que impoe o ritmo e o cardiovascular que compensa'),
        'cardiaco': ('o transporte precede o resto: a limitacao esta na '
                     'entrega de oxigenio, nao na extraccao'),
        'respiratorio': ('a ventilacao precede o resto, o que e invulgar e '
                         'merece confirmacao antes de se acreditar'),
        'autonomico': ('o sinal autonomico precede o resto -- provavel '
                       'artefacto da cinta, ver a percentagem de artefacto'),
    }
    limitador = {
        'sistema': dominante,
        'leitura': leitura.get(dominante) if dominante else
                   ('nenhum sistema domina: as arestas equilibram-se, ou nao '
                    'ha arestas dirigidas suficientes'),
        'controlo_pct': controlo_pct,
        'liquido_f': liquido,
        'aviso': ('isto e uma leitura de UMA sessao, com Granger, que mede '
                  'precedencia e nao causalidade. Repetir em varias sessoes '
                  'antes de mudar treino por causa disto'),
    }

    return {
        'ok': True,
        'limitador': limitador,
        'canais_usados': nomes,
        'excluidos_por_serem_entrada': excluidos_mecanicos,
        'pcr': pcr,
        'limitador': limitador,
        'controlo': mecanicos if ctrl is not None else None,
        'mecanicos_excluidos': excluidos_mecanicos,
        'diferenciacao': diferenciar,
        'max_lag': max_lag,
        'corr_minima': corr_minima,
        'n_pares_testados': len(pares),
        'p_corte_bh': corte,
        'n_arestas': len(arestas),
        'n_dirigidas': len(dirigidas),
        'n_indecisas': len([e for e in indecisas
                            if e['direccao'] == 'ambigua']) // 2,
        'racio_dominante': RACIO_DOMINANTE,
        'arestas': sorted(dirigidas, key=lambda e: -e['f']),
        'indecisas': sorted(indecisas, key=lambda e: -e['f']),
        'todos_os_pares': sorted(pares, key=lambda e: -e['f']),
        'graus': graus,
        'fontes': [k for k, v in ordenado if v['saidas'] > v['entradas']],
        'sumidouros': [k for k, v in ordenado if v['entradas'] > v['saidas']],
        'diagnostico': diag,
        'nota': (
            'Granger mede precedencia preditiva, nao causalidade. Numa '
            'sessao de intervalos o protocolo e causa comum de tudo, por '
            'isso os testes sao condicionados aos watts: a pergunta e se '
            'um canal acrescenta poder preditivo sobre outro ALEM do que a '
            'potencia ja explica. Series nao estacionarias sao '
            'diferenciadas antes do teste, e os p sao corrigidos por '
            'Benjamini-Hochberg sobre todos os pares. Pares em que ambos '
            'os sentidos passam ficam marcados indecisos: o teste nao '
            'distinguiu a direccao, e isso e informacao, nao ruido a '
            'esconder'),
    }
