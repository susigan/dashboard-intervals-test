"""vst_verificacao.py — Verificação de BP1/BP2 a partir do protocolo VST.

Princípio central (repetido de propósito, porque é fácil de esquecer a meio
do código): NADA de watts, duração, recuperação ou nº de intervalos é fixo
aqui. Tudo vem dos blocos REAIS da sessão — o mesmo `blocos_de_laps` que já
alimenta o resto da tab Moxy. Este módulo só reorganiza esses blocos em
aquecimento/BP1/BP2 e calcula a evolução fisiológica dentro de cada troço.

Estrutura do protocolo, pelos blocos WORK na ordem em que aparecem:
    bloco 0            → aquecimento (nunca entra em BP1)
    blocos 1..4         → BP1 (até 4, o que houver)
    blocos 5..7         → BP2 (até 3, o que houver)

Se houver menos blocos WORK do que 8, usa-se o que existir e avisa-se —
nunca se inventa um bloco que não aconteceu.
"""
import re

PADRAO_VST = re.compile(r'^\s*#?\s*vst\s*$', re.IGNORECASE)


def tem_tag_vst(tags):
    """tags: lista de strings (já extraídas por api_moxy._tags)."""
    return any(PADRAO_VST.match(t) for t in (tags or []))


# ─────────────────────────────────────────────────────────────────────────
# Métricas por intervalo — tudo derivado da série real dentro de [t0, t1]
# ─────────────────────────────────────────────────────────────────────────

def _serie_na_janela(tempo, valores, t0, t1):
    """Sub-série (tempo, valor) dentro de [t0, t1], sem None."""
    out = []
    n = min(len(tempo), len(valores))
    for i in range(n):
        if t0 <= tempo[i] <= t1 and valores[i] is not None:
            out.append((tempo[i], valores[i]))
    return out


def _terminal(serie, fracao=0.25, minimo_s=8, maximo_s=60):
    """Janela terminal proporcional ao próprio intervalo — não um número
    fixo de segundos. Entre minimo_s e maximo_s, sempre que a duração do
    intervalo permitir; se for mais curto que 2×minimo_s, usa metade."""
    if not serie:
        return []
    dur = serie[-1][0] - serie[0][0]
    if dur <= 0:
        return serie
    janela = max(minimo_s, min(maximo_s, dur * fracao))
    if janela * 2 > dur:
        janela = dur / 2
    corte = serie[-1][0] - janela
    return [p for p in serie if p[0] >= corte]


def _metricas_variavel(tempo, valores, t0, t1):
    """Inicial/média/mín/máx/final/Δ/slope, tudo a partir da série real.

    'inicial' e 'final' vêm de médias de janelas terminais proporcionais
    (não do primeiro/último ponto isolado, que seria ruído puro), mas a
    janela usada é sempre uma fracção do próprio intervalo.
    """
    serie = _serie_na_janela(tempo, valores, t0, t1)
    if len(serie) < 3:
        return {'ok': False, 'motivo': f'só {len(serie)} pontos válidos'}

    vs = [v for _, v in serie]
    ini_janela = serie[:max(1, len(serie) // 4)]
    fim_janela = _terminal(serie)

    inicial = sum(v for _, v in ini_janela) / len(ini_janela)
    final = sum(v for _, v in fim_janela) / len(fim_janela)
    media = sum(vs) / len(vs)
    delta = final - inicial
    pct = (delta / inicial * 100) if inicial else None

    # slope por regressão linear simples (tempo relativo ao início)
    xs = [t - serie[0][0] for t, _ in serie]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(vs) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = (sum((x - mx) * (v - my) for x, v in zip(xs, vs)) / sxx
             if sxx > 1e-9 else None)

    return {
        'ok': True,
        'inicial': round(inicial, 2), 'media': round(media, 2),
        'minimo': round(min(vs), 2), 'maximo': round(max(vs), 2),
        'final': round(final, 2),
        'delta': round(delta, 2),
        'delta_pct': round(pct, 1) if pct is not None else None,
        'slope_por_s': round(slope, 4) if slope is not None else None,
        'n_pontos': len(serie),
    }


def _tempo_ate_minimo(tempo, valores, t0, t1):
    """Segundos desde t0 até ao valor mínimo dentro da janela — só faz
    sentido para SmO2 (procura o vale). Devolve None se não houver dados
    suficientes."""
    serie = _serie_na_janela(tempo, valores, t0, t1)
    if len(serie) < 3:
        return None
    t_min, _ = min(serie, key=lambda p: p[1])
    return round(t_min - t0, 1)


def metricas_intervalo(canais, tempo, t0, t1, watts_medio_api=None):
    """Todas as métricas de um intervalo — potência + cada canal
    disponível. Um canal em falta ou com poucos pontos não bloqueia os
    outros; aparece como {'ok': False, 'motivo': ...} nesse campo.
    """
    watts = canais.get('watts') or []
    fora = {'t0': t0, 't1': t1, 'duracao_s': round(t1 - t0, 1)}

    # potência: se o stream nao chega, usa-se o average_watts da API
    pm = _metricas_variavel(tempo, watts, t0, t1)
    if pm.get('ok'):
        fora['potencia'] = pm
    elif watts_medio_api is not None:
        fora['potencia'] = {'ok': True, 'media': watts_medio_api,
                            'inicial': None, 'final': None,
                            'minimo': None, 'maximo': None,
                            'delta': None, 'delta_pct': None,
                            'slope_por_s': None, 'n_pontos': 0,
                            'nota': 'só average_watts da API, sem stream'}
    else:
        fora['potencia'] = {'ok': False, 'motivo': 'sem potência'}

    for canal, nome in (('smo2', 'smo2'), ('thb', 'thb'),
                        ('respiration', 'respiracao'),
                        ('heartrate', 'hr'), ('dfa_a1', 'dfa1')):
        serie = canais.get(canal)
        if not serie:
            fora[nome] = {'ok': False, 'motivo': 'canal não disponível '
                          'nesta actividade'}
            continue
        m = _metricas_variavel(tempo, serie, t0, t1)
        if canal == 'smo2' and m.get('ok'):
            m['tempo_ate_minimo_s'] = _tempo_ate_minimo(tempo, serie, t0, t1)
        fora[nome] = m

    return fora


def metricas_recuperacao(canais, tempo, t_fim_anterior, t_inicio_seguinte):
    """Recuperação REAL entre dois blocos — usa o intervalo de tempo que
    existir, por mais curto ou longo que seja. 'completude' é descrita
    por canal, nunca um score inventado."""
    dur = t_inicio_seguinte - t_fim_anterior
    if dur <= 0:
        return {'ok': False, 'motivo': 'sem intervalo de recuperação '
                'entre os blocos (consecutivos ou sobrepostos)'}

    fora = {'ok': True, 'duracao_s': round(dur, 1)}
    por_canal = {}
    for canal, nome in (('heartrate', 'hr'), ('respiration', 'respiracao'),
                        ('smo2', 'smo2'), ('thb', 'thb'),
                        ('dfa_a1', 'dfa1')):
        serie = canais.get(canal)
        if not serie:
            por_canal[nome] = {'estado': 'sem_dados',
                               'motivo': 'canal não disponível'}
            continue
        m = _metricas_variavel(tempo, serie, t_fim_anterior, t_inicio_seguinte)
        if not m.get('ok'):
            por_canal[nome] = {'estado': 'sem_dados', 'motivo': m.get('motivo')}
            continue
        # direcção esperada: HR/RF/dfa1 descem em recuperacao normal
        # (dfa1 sobe de volta para valores de repouso, tratado à parte);
        # SmO2/THb costumam subir. Compara só a direcção do delta com o
        # que se espera, sem threshold nenhum de magnitude.
        desce_esperado = nome in ('hr', 'respiracao')
        moveu_na_direccao = (m['delta'] < 0) if desce_esperado else (m['delta'] > 0)
        por_canal[nome] = {
            'estado': ('recuperou' if moveu_na_direccao else 'nao_recuperou'),
            'inicial': m['inicial'], 'final': m['final'], 'delta': m['delta'],
        }
    fora['por_canal'] = por_canal
    return fora


# ─────────────────────────────────────────────────────────────────────────
# Estrutura do protocolo — separar os blocos WORK em aquecimento/BP1/BP2
# ─────────────────────────────────────────────────────────────────────────

def estruturar_protocolo(blocos):
    """blocos: lista COMPLETA (WORK + RECOVERY), no formato que
    blocos_de_laps já produz — a função filtra os WORK aqui dentro, mas
    também usa os RECOVERY para confirmar a estrutura.

    Devolve {'aquecimento': bloco|None, 'bp1': [blocos], 'bp2': [blocos],
    'aviso': str|None, 'estrutura_confirmada': bool} -- nunca inventa um
    bloco que não existe, e avisa quando dois WORK aparecem consecutivos
    sem nenhum RECOVERY genuíno entre eles (a estrutura do protocolo diz
    que devia haver sempre um).
    """
    todos = sorted([b for b in blocos if b.get('t0') is not None],
                   key=lambda b: b['t0'])
    ons = [b for b in todos if b.get('on')]
    if not ons:
        return {'aquecimento': None, 'bp1': [], 'bp2': [],
                'aviso': 'nenhum intervalo de trabalho encontrado nesta '
                        'sessão', 'estrutura_confirmada': False}

    aquecimento = ons[0]
    resto = ons[1:]
    bp1 = resto[:4]
    bp2 = resto[4:7]

    # confirmar que ha' RECOVERY genuino entre cada par consecutivo --
    # nao basta a posicao na lista filtrada a WORK. Um "buraco" (RECOVERY
    # entre t1 de um e t0 do seguinte) tem de existir de facto; dois WORK
    # colados sem nada entre eles nao deviam acontecer neste protocolo, e
    # se acontecer e' sinal de que a estrutura real e' diferente da
    # esperada -- vale mais avisar do que assumir calado.
    sequencia = [aquecimento] + bp1 + bp2
    pares_sem_recovery = []
    for i in range(len(sequencia) - 1):
        fim_anterior = sequencia[i]['t1']
        inicio_seguinte = sequencia[i + 1]['t0']
        tem_recovery = any(
            not b.get('on') and b['t0'] >= fim_anterior - 1e-6
            and b['t1'] <= inicio_seguinte + 1e-6
            for b in todos)
        if not tem_recovery and (inicio_seguinte - fim_anterior) < 1.0:
            pares_sem_recovery.append(i)

    aviso = None
    if len(ons) < 8:
        aviso = (f'DADOS INSUFICIENTES PARA A ESTRUTURA COMPLETA DO VST — '
                 f'{len(ons)} intervalos de trabalho encontrados (esperados '
                 f'8: 1 aquecimento + 4 BP1 + 3 BP2). A analisar só o que '
                 f'existe: {len(bp1)} para BP1, {len(bp2)} para BP2.')
    elif len(ons) > 8:
        aviso = (f'{len(ons)} intervalos de trabalho encontrados, mais do '
                 f'que os 8 esperados — a usar os primeiros 4 depois do '
                 f'aquecimento como BP1 e os 3 seguintes como BP2; os '
                 f'restantes {len(ons) - 8} não entram na análise.')
    if pares_sem_recovery:
        aviso_rec = (f'{len(pares_sem_recovery)} par(es) de intervalos de '
                    f'trabalho consecutivos sem um RECOVERY genuíno entre '
                    f'eles — a estrutura desta sessão pode diferir do '
                    f'protocolo VST esperado (aquecimento/RECOVERY/BP1×4/'
                    f'RECOVERY/BP2×3).')
        aviso = (aviso + ' ' + aviso_rec) if aviso else aviso_rec

    return {'aquecimento': aquecimento, 'bp1': bp1, 'bp2': bp2,
            'aviso': aviso, 'estrutura_confirmada': not pares_sem_recovery, 'n_total_encontrados': len(ons)}


# ─────────────────────────────────────────────────────────────────────────
# Convergência de evidências — NUNCA um threshold único numa métrica só
# ─────────────────────────────────────────────────────────────────────────

_SINAIS_ESPERADOS = {
    # nome do sinal: (canal, campo, direccao_do_aumento_de_carga)
    # direccao='sobe' significa que se espera o valor SUBIR ao longo dos
    # intervalos do bloco à medida que a carga fisiológica se acumula
    'rf_drift': ('respiracao', 'delta_pct', 'sobe'),
    'hr_drift': ('hr', 'delta_pct', 'sobe'),
    'smo2_queda': ('smo2', 'delta_pct', 'desce'),
    'thb_mudanca': ('thb', 'delta_pct', 'qualquer'),
    'dfa1_queda': ('dfa1', 'delta', 'desce'),
}


def _tendencia(valores):
    """+1 se sobe, -1 se desce, 0 se nao ha' tendencia -- E um p-valor
    exacto por permutacao, nao um teste de sinal ingenuo.

    Com so' 3-4 pontos por bloco (o que o protocolo VST tem), um simples
    "o declive e' positivo?" da' falso positivo por puro acaso quase
    metade das vezes -- testado directamente: dados 100% aleatorios,
    sem nenhuma deriva real, deram 4 de 5 sinais "favoraveis" so' por
    sorte. Com n<=4, todas as permutacoes da ordem cabem (24 para n=4,
    6 para n=3) -- calcula-se o p exacto comparando o declive OBSERVADO
    com o declive de TODAS as reordenacoes possiveis dos mesmos valores.
    """
    vals = [v for v in valores if v is not None]
    n = len(vals)
    if n < 2:
        return 0, None, None
    mx = (n - 1) / 2
    sxx = sum((i - mx) ** 2 for i in range(n))
    if sxx < 1e-9:
        return 0, 0.0, 1.0

    def declive(seq):
        my = sum(seq) / n
        return sum((i - mx) * (v - my) for i, v in enumerate(seq)) / sxx

    slope_obs = declive(vals)
    if n <= 8:
        # forca bruta: todas as permutacoes cabem (8!=40320 no pior caso
        # aqui, mas o protocolo VST nunca passa de 4) -- p exacto, nao
        # aproximado
        import itertools
        todos = [declive(list(p)) for p in itertools.permutations(vals)]
        mais_extremos = sum(1 for s in todos if abs(s) >= abs(slope_obs) - 1e-12)
        p = mais_extremos / len(todos)
    else:
        p = None  # nao deveria acontecer com o protocolo VST (max 4)

    tend = 1 if slope_obs > 0 else (-1 if slope_obs < 0 else 0)
    return tend, round(slope_obs, 4), (round(p, 3) if p is not None else None)


def verificar_bloco(intervalos_metricas):
    """intervalos_metricas: lista de dicts de metricas_intervalo(), na
    ordem real dos intervalos do bloco (BP1 ou BP2).

    Classifica por CONVERGÊNCIA: cada sinal disponível vota se a
    tendência ao longo do bloco é a esperada para uma transição de
    limiar (mais RF/HR drift, mais queda de SmO2, mais queda de dfa1).
    Não há um único threshold nem uma métrica que decida sozinha.
    """
    if len(intervalos_metricas) < 2:
        return {'status': 'DADOS INSUFICIENTES',
               'motivo': f'só {len(intervalos_metricas)} intervalo(s) '
                        'neste bloco — precisa de pelo menos 2 para ver '
                        'evolução'}

    evidencias, sem_dados = [], []
    for sinal, (canal, campo, direccao) in _SINAIS_ESPERADOS.items():
        vals = []
        for iv in intervalos_metricas:
            m = iv.get(canal) or {}
            vals.append(m.get(campo) if m.get('ok') else None)
        n_validos = sum(1 for v in vals if v is not None)
        if n_validos < 2:
            sem_dados.append(sinal)
            continue
        tend, slope, p = _tendencia(vals)
        if direccao == 'sobe':
            favoravel = tend > 0
        elif direccao == 'desce':
            favoravel = tend < 0
        else:
            favoravel = tend != 0
        # com tao poucos pontos (3-4), a direccao por si so' nao chega --
        # tem de ser distinguivel do acaso (testado: dados aleatorios
        # davam 4 de 5 "favoraveis" por sorte, sem isto). O limiar e'
        # 0.34: com n=3 (sempre o caso do BP2, que so' tem 3 intervalos),
        # o MELHOR p possivel -- mesmo com uma subida perfeitamente
        # monotona -- e' 0.333, confirmado directamente (so' 6
        # permutacoes cabem com 3 valores, e a monotona empata em
        # magnitude com o seu espelho). Um limiar mais apertado do que
        # isto tornaria IMPOSSIVEL o BP2 alguma vez ser confirmado, por
        # pura limitacao do tamanho da amostra -- nao por os dados
        # nao mostrarem nada.
        significativo = (p is not None and p <= 0.34)
        evidencias.append({'sinal': sinal, 'tendencia': tend,
                           'slope': slope, 'p_permutacao': p,
                           'favoravel': favoravel and significativo,
                           'direccao_certa_mas_fraca': favoravel and not significativo,
                           'valores': vals})

    if not evidencias:
        return {'status': 'DADOS INSUFICIENTES',
               'motivo': 'nenhum sinal fisiológico com dados suficientes '
                        'para ver evolução dentro do bloco',
               'sem_dados': sem_dados}

    n_fav = sum(1 for e in evidencias if e['favoravel'])
    n_total = len(evidencias)
    frac = n_fav / n_total

    if frac >= 0.75:
        status = 'CONFIRMADO'
    elif frac >= 0.4:
        status = 'PARCIALMENTE CONFIRMADO'
    else:
        status = 'NÃO CONFIRMADO'

    return {
        'status': status,
        'n_sinais_favoraveis': n_fav, 'n_sinais_testados': n_total,
        'fraccao_favoravel': round(frac, 2),
        'evidencias': evidencias,
        'sem_dados': sem_dados,
        'motivo': (f'{n_fav} de {n_total} sinais fisiológicos mostram a '
                  f'tendência esperada de uma transição de limiar ao '
                  f'longo do bloco'),
    }
