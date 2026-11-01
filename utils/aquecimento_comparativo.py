"""Analise comparativa de aquecimentos por periodo e escalao de watts.

Funcoes puras (sem Flask nem SQLite). Usa apenas colunas ja guardadas em
aquecimento_blocos: <metrica>_avg, watts_alvo, watts_real, data, activity_id.

Nao usa min/max (origem desconhecida), nem cadencia, torque ou elevacao.
ORIGEM DAS MEDIAS: por confirmar. Zeros sao duvidosos: ficam fora das
escalas e das estatisticas, e nunca sao tratados como ausencia nem como zero.
Ausencia nunca vira zero.
"""
import calendar
import math
import statistics
from datetime import date, timedelta

METRICAS = {
    'hr':   {'coluna': 'hr_avg',   'nome': 'HR',         'unidade': 'bpm', 'casas': 0},
    'smo2': {'coluna': 'smo2_avg', 'nome': 'SmO₂',       'unidade': '%',   'casas': 0},
    'resp': {'coluna': 'resp_avg', 'nome': 'Respiração', 'unidade': 'rpm', 'casas': 0},
    'dfa1': {'coluna': 'dfa1_avg', 'nome': 'DFA-α1',     'unidade': '',    'casas': 2},
    # HRR por degrau (vem de aquecimento_hrr, nao de aquecimento_blocos).
    # Descida da FC em 60 s e 120 s apos o fim do esforco, e FC media do 1.o minuto.
    'hrr60':      {'coluna': 'hrr60_avg',      'nome': 'HRR60',         'unidade': 'bpm', 'casas': 0},
    'hrr120':     {'coluna': 'hrr120_avg',     'nome': 'HRR120',        'unidade': 'bpm', 'casas': 0},
    'fc_rec1min': {'coluna': 'fc_rec1min_avg', 'nome': 'FC rec. 1 min', 'unidade': 'bpm', 'casas': 0},
}
METRICAS_HRR = ('hrr60', 'hrr120', 'fc_rec1min')
DIAS_RAPIDOS = (30, 60, 90)
AGREGACOES = ('mes', 'semana')
MOTIVO_SEM_DADOS = 'sem valor válido nesta métrica'
MOTIVO_SO_ZEROS = 'só zeros registados (duvidoso)'

AVISOS = [
    'Origem das médias por confirmar: podem vir da média das amostras do bloco '
    'ou de colunas de plateau/trabalho do pipeline antigo.',
    'Zeros registados são duvidosos: ficam fora das escalas e das estatísticas, '
    'sem serem tratados como ausência nem como zero.',
    'THb não está guardada na base de dados, por isso não é apresentada.',
]


def _float_valido(v):
    """Devolve float finito e diferente de zero; caso contrario None."""
    if v is None or v == '':
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f == 0:
        return None
    return f


def _eh_zero(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f == 0


def _watts(w):
    """Normaliza watts: 120.0 -> 120 (inteiro quando nao ha parte decimal)."""
    if w is None:
        return None
    f = float(w)
    return int(f) if f.is_integer() else f


def intervalo_periodo(hoje, dias=None, inicio=None, fim=None):
    """Devolve (inicio, fim) como date, ambos inclusivos.

    dias: 30, 60 ou 90, contados ate hoje (inclusive).
    inicio/fim: datas para o periodo personalizado.
    """
    if dias is not None:
        if dias not in DIAS_RAPIDOS:
            raise ValueError('dias deve ser 30, 60 ou 90')
        return hoje - timedelta(days=dias - 1), hoje
    if inicio is None or fim is None:
        raise ValueError('periodo personalizado requer inicio e fim')
    if inicio > fim:
        raise ValueError('inicio posterior ao fim')
    return inicio, fim


def sessoes_no_periodo(blocos, metrica, ini_iso, fim_iso):
    """Agrupa os blocos por sessao dentro do periodo.

    Devolve (incluidas, excluidas, zeros_excluidos).
    - incluidas: sessoes com pelo menos um valor valido na metrica. Cada
      ponto e um escalao de watts_alvo com o valor da metrica.
    - excluidas: sessoes do periodo sem nenhum valor valido, com motivo.
    - zeros_excluidos: total de blocos com zero registado (fora das estatisticas).
    """
    col = METRICAS[metrica]['coluna']
    por_sessao = {}
    for b in blocos:  # o chamador ja filtra por modalidade
        d = str(b.get('data') or '')[:10]
        if not (ini_iso <= d <= fim_iso):
            continue
        s = por_sessao.setdefault(b['activity_id'], {'data': d, 'blocos': []})
        s['blocos'].append(b)

    incluidas, excluidas, zeros_total = [], [], 0
    ordem = sorted(por_sessao.items(), key=lambda kv: (kv[1]['data'], str(kv[0])))
    for aid, s in ordem:
        pontos, vistos, zeros = [], set(), 0
        blocos_ord = sorted(s['blocos'], key=lambda b: (b.get('interval_num') or 0,
                                                       b.get('bloco_num') or 0))
        for b in blocos_ord:
            raw = b.get(col)
            if _eh_zero(raw):
                zeros += 1
                continue
            v = _float_valido(raw)
            w = _watts(b.get('watts_alvo'))
            if v is None or w is None or w in vistos:
                continue
            vistos.add(w)
            pontos.append({'watts_alvo': w, 'valor': v,
                           'watts_real': b.get('watts_real'),
                           'bloco_num': b.get('bloco_num')})
        zeros_total += zeros
        if not pontos:
            motivo = MOTIVO_SO_ZEROS if zeros else MOTIVO_SEM_DADOS
            excluidas.append({'activity_id': aid, 'data': s['data'], 'motivo': motivo})
        else:
            incluidas.append({'activity_id': aid, 'data': s['data'],
                              'pontos': sorted(pontos, key=lambda p: p['watts_alvo']),
                              'zeros': zeros})
    return incluidas, excluidas, zeros_total


def escaloes(incluidas):
    """Escalões de watts presentes nas sessoes incluidas, ordenados."""
    return sorted({p['watts_alvo'] for s in incluidas for p in s['pontos']})


def chave_periodo(data_iso, agregacao):
    d = date.fromisoformat(data_iso)
    if agregacao == 'mes':
        return f'{d.year:04d}-{d.month:02d}'
    if agregacao == 'semana':
        ano, semana, _ = d.isocalendar()
        return f'{ano:04d}-S{semana:02d}'
    raise ValueError('agregacao deve ser mes ou semana')


def estatisticas(valores):
    """Media e desvio-padrao amostral (n-1). SD so existe com n >= 2."""
    n = len(valores)
    media = sum(valores) / n
    if n >= 2:
        sd = math.sqrt(sum((v - media) ** 2 for v in valores) / (n - 1))
        sem = sd / math.sqrt(n)
    else:
        sd = sem = None
    return {'n': n, 'media': media, 'sd': sd, 'sem': sem}


def agregar_periodos(incluidas, agregacao='mes', min_n=3):
    """Vista 2: por periodo e escalao, media das sessoes com n e dispersao.

    Cada ponto e uma media de sessoes, nao uma medicao individual.
    'suficiente' indica se n >= min_n; abaixo disso o ponto aparece mas sem
    ser ligado a linha nem com barra de dispersao.
    """
    grupos = {}
    for s in incluidas:
        p = chave_periodo(s['data'], agregacao)
        for pt in s['pontos']:
            grupos.setdefault((p, pt['watts_alvo']), []).append((pt['valor'], s['activity_id']))

    periodos = sorted({p for p, _ in grupos})
    saida = []
    for p in periodos:
        linhas = []
        for w in sorted({w for pp, w in grupos if pp == p}):
            vals = grupos[(p, w)]
            est = estatisticas([v for v, _ in vals])
            linhas.append({'watts_alvo': w, **est,
                           'suficiente': est['n'] >= min_n,
                           'sessoes': [a for _, a in vals]})
        saida.append({'periodo': p, 'escaloes': linhas})
    return saida


PERIODOS_SERIE = ('mes', 'trimestre', 'semestre', 'ano', 'todo')


def chave_periodo_amplo(data_iso, periodo):
    """Rotulo do periodo para a serie agregada (mes, trimestre, semestre, ano, todo)."""
    d = date.fromisoformat(data_iso)
    if periodo == 'mes':
        return f'{d.year:04d}-{d.month:02d}'
    if periodo == 'trimestre':
        return f'{d.year:04d}-T{(d.month - 1) // 3 + 1}'
    if periodo == 'semestre':
        return f'{d.year:04d}-S{1 if d.month <= 6 else 2}'
    if periodo == 'ano':
        return f'{d.year:04d}'
    if periodo == 'todo':
        return 'todo'
    raise ValueError(f'periodo deve ser um de {", ".join(PERIODOS_SERIE)}')


def intervalo_dados(blocos):
    """(primeiro, ultimo) dia AAAA-MM-DD dos dados, ou (None, None) se nao ha."""
    datas = sorted(str(b.get('data') or '')[:10] for b in blocos if b.get('data'))
    return (datas[0], datas[-1]) if datas else (None, None)


def serie_por_periodo(blocos, metrica, periodo, ini_iso, fim_iso, rolling=1):
    """Linha agregada por periodo: para cada escalao de watts, a MEDIA de todas
    as sessoes do periodo (sem N, sem uma linha por sessao).

    rolling=1 e' a media simples do periodo. rolling=k e' uma media movel que
    junta os ultimos k periodos existentes (pool de todas as sessoes dessa janela).
    Ausencia continua a ser ausencia: um escalao sem dados no periodo nao aparece.
    """
    if metrica not in METRICAS:
        raise ValueError(f'metrica desconhecida: {metrica}')
    if periodo not in PERIODOS_SERIE:
        raise ValueError(f'periodo deve ser um de {", ".join(PERIODOS_SERIE)}')
    if rolling < 1:
        raise ValueError('rolling deve ser >= 1')
    casas = METRICAS[metrica]['casas']

    incl, _excl, _zeros = sessoes_no_periodo(blocos, metrica, ini_iso, fim_iso)
    grupos = {}  # (periodo, escalao) -> valores de todas as sessoes
    for sess in incl:
        p = chave_periodo_amplo(sess['data'], periodo)
        for pt in sess['pontos']:
            grupos.setdefault((p, pt['watts_alvo']), []).append(pt['valor'])

    periodos = sorted({p for p, _ in grupos})
    escaloes = sorted({w for _, w in grupos})
    series = []
    for i, p in enumerate(periodos):
        janela = periodos[max(0, i - rolling + 1): i + 1]
        pontos = []
        for w in escaloes:
            vals = [v for q in janela for v in grupos.get((q, w), [])]
            if vals:
                pontos.append({'watts_alvo': w,
                               'valor': round(sum(vals) / len(vals), casas)})
        if pontos:
            series.append({'periodo': p, 'pontos': pontos})

    m = METRICAS[metrica]
    return {'metrica': metrica, 'nome': m['nome'], 'unidade': m['unidade'],
            'casas': casas, 'periodo': periodo, 'rolling': rolling,
            'escaloes': escaloes, 'series': series, 'sessoes': len(incl)}


def deltas(periodos, escalao_lista):
    """Diferenca entre o primeiro e o ultimo periodo suficiente, por escalao."""
    out = []
    for w in escalao_lista:
        suf = [(p['periodo'], e) for p in periodos for e in p['escaloes']
               if e['watts_alvo'] == w and e['suficiente']]
        if len(suf) >= 2:
            (p0, e0), (p1, e1) = suf[0], suf[-1]
            out.append({'watts_alvo': w, 'de': p0, 'para': p1,
                        'n_de': e0['n'], 'n_para': e1['n'],
                        'media_de': e0['media'], 'media_para': e1['media'],
                        'delta': e1['media'] - e0['media']})
    return out


def comparativo(blocos, modalidade, metrica, hoje, dias=None, inicio=None,
                fim=None, agregacao='mes', min_n=3):
    """Monta o resultado completo para as duas vistas."""
    if metrica not in METRICAS:
        raise ValueError(f'metrica desconhecida: {metrica}')
    if agregacao not in AGREGACOES:
        raise ValueError('agregacao deve ser mes ou semana')
    if min_n < 1:
        raise ValueError('min_n deve ser >= 1')

    ini, fim_d = intervalo_periodo(hoje, dias=dias, inicio=inicio, fim=fim)
    incl, excl, zeros = sessoes_no_periodo(blocos, metrica, ini.isoformat(), fim_d.isoformat())
    esc = escaloes(incl)
    for s in incl:
        presentes = {p['watts_alvo'] for p in s['pontos']}
        s['em_falta'] = [w for w in esc if w not in presentes]
    periodos = agregar_periodos(incl, agregacao, min_n)

    m = METRICAS[metrica]
    return {
        'status': 'ok',
        'modalidade': modalidade,
        'metrica': metrica,
        'nome': m['nome'],
        'unidade': m['unidade'],
        'casas': m['casas'],
        'agregacao': agregacao,
        'min_n': min_n,
        'periodo': {'inicio': ini.isoformat(), 'fim': fim_d.isoformat(),
                    'dias': dias},
        'sessoes': {
            'incluidas': len(incl),
            'excluidas': len(excl),
            'excluidas_lista': excl,
            'incompletas': sum(1 for s in incl if s['em_falta']),
            'zeros_excluidos': zeros,
        },
        'escaloes': esc,
        'vista1': incl,
        'vista2': {'periodos': periodos, 'deltas': deltas(periodos, esc)},
        'avisos': AVISOS,
    }


# ---------------------------------------------------------------------------
# Tendencia ao longo do tempo (uma metrica, um periodo)
# ---------------------------------------------------------------------------

# Sentido "melhor" de cada metrica, ao mesmo escalao de watts.
# None = sem sentido fixo; apenas se diz subida / descida / sem mudanca.
SENTIDO_MELHOR = {
    'hr': 'baixo',          # FC mais baixa ao mesmo esforco
    'fc_rec1min': 'baixo',  # FC mais baixa nos primeiros 60 s apos o esforco
    'smo2': 'alto',
    'hrr60': 'alto',        # descida maior da FC
    'hrr120': 'alto',
    'resp': None,
    'dfa1': None,
}

# Janelas de tempo oferecidas no painel: (rotulo, meses ou None para todo o periodo).
JANELAS_MESES = (('3', 3), ('6', 6), ('12', 12), ('24', 24), ('36', 36), ('todo', None))
MIN_SESSOES_TENDENCIA = 8
ALFA_TENDENCIA = 0.05


def menos_meses(d, meses):
    """Data d recuada em 'meses' meses calendario (dia limitado ao mes de destino)."""
    total = d.year * 12 + (d.month - 1) - meses
    ano, mes0 = divmod(total, 12)
    mes = mes0 + 1
    dia = min(d.day, calendar.monthrange(ano, mes)[1])
    return date(ano, mes, dia)


def tendencia(incl, metrica, min_sessoes=MIN_SESSOES_TENDENCIA):
    """Tendencia da metrica ao longo do tempo, dentro do periodo.

    Uma observacao por SESSAO (nao por bloco), para nao contar o mesmo dia
    varias vezes. Cada sessao vale a media dos seus desvios face a mediana do
    mesmo escalao de watts no periodo: assim uma sessao mais forte ou mais
    fraca nao parece uma melhora ou piora.

    Teste: tau de Kendall entre o tempo e o valor (equivalente ao Mann-Kendall,
    sem assumir normalidade). Declive: Theil-Sen (mediana das inclinacoes), em
    unidades da metrica por ano, com IC 95%.
    Com uma so pessoa, sessoes proximas no tempo nao sao independentes:
    o resultado e um indicio, nao uma prova.
    """
    from scipy import stats

    por_escalao = {}
    for s in incl:
        for p in s['pontos']:
            por_escalao.setdefault(p['watts_alvo'], []).append(p['valor'])
    medianas = {w: statistics.median(v) for w, v in por_escalao.items() if len(v) >= 3}
    if not medianas:
        return {'status': 'insuficiente'}

    obs = []
    for s in incl:
        desvios = [p['valor'] - medianas[p['watts_alvo']]
                   for p in s['pontos'] if p['watts_alvo'] in medianas]
        if desvios:
            obs.append((date.fromisoformat(s['data']), sum(desvios) / len(desvios)))
    obs.sort(key=lambda o: o[0])
    if len(obs) < min_sessoes:
        return {'status': 'insuficiente', 'minimo_sessoes': min_sessoes}

    d0 = obs[0][0]
    t = [(d - d0).days / 365.25 for d, _ in obs]
    y = [v for _, v in obs]
    if max(t) <= 0:
        return {'status': 'insuficiente', 'minimo_sessoes': min_sessoes}

    tau, p = stats.kendalltau(t, y)
    if p is None or math.isnan(p):
        # valores todos iguais: nao ha variacao, logo nao ha tendencia
        tau, p = 0.0, 1.0
    declive, _icpt, lo, hi = stats.theilslopes(y, t)

    if p < ALFA_TENDENCIA and declive > 0:
        direcao = 'subida'
    elif p < ALFA_TENDENCIA and declive < 0:
        direcao = 'descida'
    else:
        direcao = 'sem_mudanca'

    sentido = SENTIDO_MELHOR.get(metrica)
    avaliacao = None
    if sentido is not None and direcao != 'sem_mudanca':
        sobe_bom = (sentido == 'alto')
        avaliacao = 'melhorou' if (direcao == 'subida') == sobe_bom else 'piorou'
    elif sentido is not None:
        avaliacao = 'sem mudança clara'

    return {'status': 'ok', 'direcao': direcao, 'tau': round(float(tau), 3),
            'p': round(float(p), 4), 'declive_ano': round(float(declive), 3),
            'ic95': [round(float(lo), 3), round(float(hi), 3)],
            'avaliacao': avaliacao, 'desde': d0.isoformat(), 'ate': obs[-1][0].isoformat()}


def resumo_janela(blocos, metrica, ini_iso, fim_iso):
    """Media por escalao de watts (todas as sessoes da janela) + tendencia."""
    casas = METRICAS[metrica]['casas']
    incl, _excl, _zeros = sessoes_no_periodo(blocos, metrica, ini_iso, fim_iso)
    por_escalao = {}
    for s in incl:
        for p in s['pontos']:
            por_escalao.setdefault(p['watts_alvo'], []).append(p['valor'])
    pontos = [{'watts_alvo': w, 'valor': round(sum(v) / len(v), casas)}
              for w, v in sorted(por_escalao.items())]
    return {'ini': ini_iso, 'fim': fim_iso, 'escaloes': sorted(por_escalao),
            'pontos': pontos, 'tendencia': tendencia(incl, metrica)}
