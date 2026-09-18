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


def recovery_fraction(metricas_work, metricas_recovery_canal):
    """Fracção do que mudou durante o WORK que foi revertido no
    RECOVERY -- uma so' formula para todas as metricas, porque a
    direccao esperada ja' vem embutida no PROPRIO sinal do delta do
    WORK (nao e' preciso saber se e' HR ou SmO2 para a formula
    funcionar, so' para a interpretar):

        fraccao = -delta_recovery / delta_work

    Se o recovery se moveu na direccao OPOSTA ao work (o esperado --
    ex.: HR subiu no work, desceu no recovery), a fraccao sai positiva:
    1.0 = reverteu tudo, 0.5 = reverteu metade, 0.0 = nao reverteu nada.
    Se o recovery continuou na MESMA direccao do work (piorou em vez de
    recuperar), a fraccao sai negativa -- e isso e' informacao real, nao
    um erro a esconder.

    Devolve None (nao um numero fabricado) se o work nao teve uma
    mudanca real para reverter (delta_work perto de zero), porque a
    fraccao nao faz sentido matematico nesse caso -- dividir por
    "quase nada" so' amplificaria ruido.
    """
    if not metricas_work or not metricas_work.get('ok'):
        return None
    if not metricas_recovery_canal or metricas_recovery_canal.get('estado') == 'sem_dados':
        return None
    delta_work = metricas_work.get('delta')
    delta_rec = metricas_recovery_canal.get('delta')
    if delta_work is None or delta_rec is None:
        return None
    # limiar RELATIVO à propria escala da metrica, nao absoluto -- 1e-6
    # bpm de mudanca no HR e' "nada" na pratica, mas 1e-6 em DFA1 (que
    # anda a' volta de 1.0) tambem seria "nada" -- um limiar absoluto so'
    # funciona bem para uma das escalas. Exige que o work tenha mudado
    # pelo menos 2% do seu proprio valor inicial antes de dividir por ele.
    inicial_work = metricas_work.get('inicial')
    limiar = max(1e-6, abs(inicial_work) * 0.02) if inicial_work else 1e-6
    if abs(delta_work) < limiar:
        return None
    return round(-delta_rec / delta_work, 3)


def classificar_recovery_completeness(fraccao):
    """CLASSIFICAÇÃO OPERACIONAL da fracção de recovery — não é uma
    validação fisiológica. Os limiares 0.75/0.4 são os mesmos já usados
    em verificar_bloco (CONFIRMADO/PARCIALMENTE/NÃO CONFIRMADO),
    reutilizados aqui por consistência com o resto do projecto, não
    porque "75% de recuperação" seja um valor fisiologicamente
    validado — não é. É só onde a metodologia actual traça a linha
    entre "recuperou bastante" e "recuperou pouco".

    Quatro faixas, nenhuma delas escondida ou achatada:
      fraccao < 0        -> a variável continuou a mudar na mesma
                            direcção do work, em vez de reverter
                            (ex.: HR continuou a subir no recovery)
      0 <= fraccao < 0.4  -> RECUPERAÇÃO MÍNIMA
      0.4 <= fraccao < 0.75 -> RECUPERAÇÃO PARCIAL
      fraccao >= 0.75     -> RECUPERAÇÃO (quase) COMPLETA
      fraccao > 1         -> passou do que tinha antes do work
                            (overshoot) -- tambem informação real,
                            não um erro de cálculo
    """
    if fraccao is None:
        return 'DADOS INSUFICIENTES'
    if fraccao < 0:
        return 'SEM RECUPERAÇÃO / CONTINUAÇÃO'
    if fraccao > 1:
        return 'OVERSHOOT'
    if fraccao >= 0.75:
        return 'RECUPERAÇÃO COMPLETA'
    if fraccao >= 0.4:
        return 'RECUPERAÇÃO PARCIAL'
    return 'RECUPERAÇÃO MÍNIMA'


def recovery_progressivo(lista_recuperacoes, canal):
    """A recuperacao muda conforme os WORKs se sucedem dentro de um
    bloco (BP1 ou BP2)? Reutiliza _tendencia (o MESMO teste de
    permutacao exacto de verificar_bloco), agora sobre os deltas de
    recovery em vez dos deltas de work -- nao e' uma segunda
    implementacao estatistica, e' a mesma funcao aplicada a outra
    serie."""
    deltas = []
    for r in lista_recuperacoes:
        if not r or not r.get('ok'):
            continue
        c = (r.get('por_canal') or {}).get(canal)
        if c and c.get('estado') != 'sem_dados' and c.get('delta') is not None:
            deltas.append(c['delta'])
    if len(deltas) < 2:
        return {'ok': False, 'motivo': f'só {len(deltas)} recovery(s) com '
               f'dados válidos para {canal} -- precisa de pelo menos 2 '
               'para ver tendência'}
    tend, slope, p = _tendencia(deltas)
    return {'ok': True, 'tendencia': tend, 'slope': slope, 'p_permutacao': p,
           'significativo': p is not None and p <= 0.34, 'valores': deltas}


def estruturar_protocolo(blocos):
    """blocos: lista COMPLETA (WORK + RECOVERY), no formato que
    blocos_de_laps já produz.

    Devolve {'aquecimento': bloco|None, 'bp1': [blocos], 'bp2': [blocos],
    'duracao_curta': [...], 'aviso': str|None, 'estrutura_confirmada': bool}.

    Duas coisas que só apareceram ao testar com sessões reais:

    1. O aquecimento pode NÃO vir tipado como WORK. Num caso real, a
       Intervals.icu tipou o aquecimento (17min, ~114W) como RECOVERY —
       provavelmente por ser uma rampa gradual, não um degrau constante.
       Se se assumisse sempre "o primeiro bloco WORK é o aquecimento",
       o primeiro esforço real do BP1 era roubado ao bloco errado.
       Por isso: primeiro procura-se um bloco ANTES do primeiro WORK —
       seja ele WORK ou não — cuja duração seja claramente maior do que
       as recuperações normais da sessão (>=180s e >=1.5x a mediana das
       recuperações); só se não existir esse candidato é que se cai
       para trás no comportamento antigo (primeiro WORK = aquecimento).

    2. Um WORK com duração muito abaixo dos restantes (ex.: 1 segundo,
       exactamente onde a gravação parou) NÃO é excluído — fica na
       contagem (a potência da API continua válida), só marcado em
       'duracao_curta' para quem for mostrar a fisiologia saber que ali
       não há dados suficientes para uma análise fiável. Excluir por
       duração já causou o erro oposto: um caso real tinha o BP2#3
       verdadeiro (251W) com duração de 1s por um problema de fronteira
       do lap do lado da Intervals.icu — exclui-lo tirava-o da análise.
    """
    todos = sorted([b for b in blocos if b.get('t0') is not None],
                   key=lambda b: b['t0'])
    ons = [b for b in todos if b.get('on')]
    if not ons:
        return {'aquecimento': None, 'bp1': [], 'bp2': [],
                'duracao_curta': [], 'aviso': 'nenhum intervalo de '
                'trabalho encontrado nesta sessão',
                'estrutura_confirmada': False, 'n_total_encontrados': 0}

    # marcar duracao curta, sem excluir -- ver ponto 2 do docstring
    duracoes = [b['t1'] - b['t0'] for b in ons if b['t1'] > b['t0']]
    mediana_dur = sorted(duracoes)[len(duracoes) // 2] if duracoes else 0
    limiar_dur = max(15.0, mediana_dur * 0.10)
    for b in ons:
        b['duracao_curta'] = (b['t1'] - b['t0']) < limiar_dur

    # candidato a aquecimento: o bloco imediatamente antes do primeiro
    # WORK, na lista COMPLETA (pode ser RECOVERY) -- ver ponto 1 do
    # docstring. So' conta como aquecimento separado se durar claramente
    # mais do que as recuperacoes normais da sessao.
    idx_primeiro_on = todos.index(ons[0])
    aquecimento = None
    veio_de_ons = False
    if idx_primeiro_on > 0:
        candidato = todos[idx_primeiro_on - 1]
        recs = [b['t1'] - b['t0'] for b in todos
               if not b.get('on') and b is not candidato]
        mediana_rec = sorted(recs)[len(recs) // 2] if recs else 0
        dur_candidato = candidato['t1'] - candidato['t0']
        if dur_candidato >= max(180.0, mediana_rec * 1.5):
            aquecimento = candidato
    if aquecimento is None:
        aquecimento = ons[0]
        resto = ons[1:]
        veio_de_ons = True
    else:
        resto = ons

    bp1 = resto[:4]
    bp2 = resto[4:7]

    # confirmar que ha' RECOVERY genuino entre cada par consecutivo --
    # nao basta a posicao na lista filtrada a WORK.
    sequencia = ([aquecimento] if veio_de_ons else []) + bp1 + bp2
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
    if len(resto) < 7:
        aviso = (f'DADOS INSUFICIENTES PARA A ESTRUTURA COMPLETA DO VST — '
                 f'{len(resto)} intervalos de trabalho encontrados depois '
                 f'do aquecimento (esperados 7: 4 BP1 + 3 BP2). A analisar '
                 f'só o que existe: {len(bp1)} para BP1, {len(bp2)} para '
                 f'BP2.')
    elif len(resto) > 7:
        aviso = (f'{len(resto)} intervalos de trabalho encontrados depois '
                 f'do aquecimento, mais do que os 7 esperados — a usar os '
                 f'primeiros 4 como BP1 e os 3 seguintes como BP2; os '
                 f'restantes {len(resto) - 7} não entram na análise.')
    if pares_sem_recovery:
        aviso_rec = (f'{len(pares_sem_recovery)} par(es) de intervalos de '
                    f'trabalho consecutivos sem um RECOVERY genuíno entre '
                    f'eles — a estrutura desta sessão pode diferir do '
                    f'protocolo VST esperado.')
        aviso = (aviso + ' ' + aviso_rec) if aviso else aviso_rec
    curtos = [b for b in sequencia if b.get('duracao_curta')]
    if curtos:
        aviso_curto = (f'{len(curtos)} intervalo(s) com duração muito '
                      f'abaixo dos restantes (< {limiar_dur:.0f}s) — a '
                      f'potência é usada na mesma (vem da API, não do '
                      f'stream), mas a fisiologia (SmO2/HR/RF/DFA-α1) fica '
                      f'sem dados suficientes para uma análise fiável '
                      f'nesse intervalo especificamente.')
        aviso = (aviso + ' ' + aviso_curto) if aviso else aviso_curto

    return {'aquecimento': aquecimento, 'bp1': bp1, 'bp2': bp2,
            'duracao_curta': [{'t0': b['t0'], 't1': b['t1'],
                              'duracao_s': round(b['t1'] - b['t0'], 1),
                              'watts_medio_da_api': b.get('watts_medio_da_api'),
                              'watts_medio': b.get('watts_medio')}
                             for b in curtos],
            'aviso': aviso, 'estrutura_confirmada': not pares_sem_recovery,
            'n_total_encontrados': len(ons) + (0 if veio_de_ons else 1)}


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


# ═══════════════════════════════════════════════════════════════════════
# COMPARAÇÃO DIA 1 × DIA 2 — reproduz o BP1/BP2 identificado no Dia 1
# (sessão Moxy comum, threshold por SmO2) através dos esforços
# sustentados do Dia 2 (protocolo VST, estruturado). Nunca exige
# igualdade de valores -- compara DIRECÇÃO, MAGNITUDE e se o padrão
# converge entre varias metricas, nao um numero so'.
# ═══════════════════════════════════════════════════════════════════════

_METRICAS_COMPARACAO = [
    # (chave, nome para mostrar, unidade)
    ('potencia', 'Potência', 'W'),
    ('respiracao', 'Respiração (RF)', ''),
    ('smo2', 'SmO2', '%'),
    ('thb', 'THb', ''),
    ('hr', 'HR', 'bpm'),
    ('dfa1', 'DFA1-α1', ''),
]

# ponte para os sinais que verificar_bloco ja' testa por permutacao no
# Dia 2 -- reaproveitado aqui, nao duplicado (pedido explicito: nao
# reimplementar uma segunda vez o mesmo principio estatistico)
_CHAVE_PARA_SINAL = {
    'respiracao': 'rf_drift', 'hr': 'hr_drift', 'smo2': 'smo2_queda',
    'thb': 'thb_mudanca', 'dfa1': 'dfa1_queda',
}

# DFA1 fica com peso reduzido -- evidencia autonomica complementar, nao
# ao mesmo nivel de RF/HR/SmO2 (a estacionariedade que o metodo exige
# e' questionavel numa janela onde a potencia ainda esta a subir).
_METRICAS_PESO_COMPLEMENTAR = {'dfa1'}


def _direccao(delta_pct, limiar=2.0):
    """↑/↓/→ a partir do delta percentual -- limiar de 2% para nao
    chamar "estavel" ruido de medicao de direccao nenhuma, nem chamar
    "mudanca" a uma oscilacao de 0.3%."""
    if delta_pct is None:
        return None
    if delta_pct > limiar:
        return '↑'
    if delta_pct < -limiar:
        return '↓'
    return '→'


def _consistencia_metrica(m1, m2):
    """Compara UMA metrica entre os dois dias -- direccao primeiro
    (o que a spec pede: nao exigir igualdade), so' depois olha para a
    magnitude como informacao extra, nunca como criterio unico."""
    if not m1 or not m1.get('ok') or not m2 or not m2.get('ok'):
        return {'consistencia': 'SEM DADOS', 'dia1': None, 'dia2': None,
               'direccao_dia1': None, 'direccao_dia2': None}

    d1, d2 = m1.get('delta_pct'), m2.get('delta_pct')
    dir1, dir2 = _direccao(d1), _direccao(d2)

    if dir1 is None or dir2 is None:
        consist = 'SEM DADOS'
    elif dir1 == dir2:
        consist = 'CONSISTENTE'
    elif dir1 == '→' or dir2 == '→':
        # um dos dois "estavel" e o outro claramente subiu/desceu --
        # nem concorda nem discorda de vez, fica parcial
        consist = 'PARCIAL'
    else:
        consist = 'DIVERGENTE'  # um sobe, o outro desce

    return {'consistencia': consist,
           'dia1': {'inicial': m1.get('inicial'), 'final': m1.get('final'),
                    'delta_pct': d1},
           'dia2': {'inicial': m2.get('inicial'), 'final': m2.get('final'),
                    'delta_pct': d2},
           'direccao_dia1': dir1, 'direccao_dia2': dir2}


def _timing_dia1(dia1_metricas, dia1_vizinhos):
    """Timing no Dia 1: o bloco escolhido (mais proximo do BP) mostra
    MAIS mudanca do que os blocos vizinhos que nao sao o BP? Se sim, a
    mudanca esta mesmo concentrada ali -- se os vizinhos mudam tanto ou
    mais, o "breakpoint" pode nao estar localizado onde se pensa.
    dia1_vizinhos: lista de dicts de metricas_intervalo() dos OUTROS
    blocos WORK desta sessao (antes/depois do escolhido), para servir
    de referencia -- nunca usados para nada alem desta comparacao.
    """
    if not dia1_metricas or not dia1_vizinhos:
        return None
    fora = {}
    for chave, _, _ in _METRICAS_COMPARACAO:
        if chave == 'potencia':
            continue
        alvo = (dia1_metricas.get(chave) or {})
        if not alvo.get('ok') or alvo.get('delta_pct') is None:
            continue
        viz_deltas = [abs((v.get(chave) or {}).get('delta_pct'))
                     for v in dia1_vizinhos
                     if (v.get(chave) or {}).get('ok')
                     and (v.get(chave) or {}).get('delta_pct') is not None]
        if not viz_deltas:
            continue
        media_viz = sum(viz_deltas) / len(viz_deltas)
        fora[chave] = {
            'delta_no_alvo': abs(alvo['delta_pct']),
            'media_delta_vizinhos': round(media_viz, 1),
            'concentrado_no_alvo': abs(alvo['delta_pct']) > media_viz,
        }
    return fora or None


def _timing_dia2(chave, verificacao_dia2):
    """Timing no Dia 2: reaproveita o proprio resultado de
    verificar_bloco (evidencias[].tendencia) -- se o sinal sobe/desce ao
    longo do bloco (tendencia != 0) com significancia (p_permutacao
    baixo), a mudanca esta concentrada no FIM do bloco, que e' onde o
    BP se espera. Nao se recalcula nada, so' se le o que ja existe.
    """
    sinal = _CHAVE_PARA_SINAL.get(chave)
    if not sinal or not verificacao_dia2:
        return None
    ev = next((e for e in (verificacao_dia2.get('evidencias') or [])
              if e['sinal'] == sinal), None)
    if not ev:
        return None
    return {'tendencia_no_bloco': ev['tendencia'],
           'p_permutacao': ev.get('p_permutacao'),
           'concentrado_no_fim': ev.get('tendencia', 0) != 0
                                and (ev.get('p_permutacao') or 1) <= 0.34}


def comparar_bp(dia1_metricas, dia2_metricas_lista, dia1_vizinhos=None,
                verificacao_dia2=None):
    """dia1_metricas: dict de metricas_intervalo() para O bloco do Dia 1
    mais proximo do BP em causa (BP1 ou BP2).
    dia2_metricas_lista: lista de dicts de metricas_intervalo(), os
    intervalos do Dia 2 que compoem esse mesmo BP (bp1 ou bp2 do VST).
    dia1_vizinhos: outros blocos WORK do Dia 1 (para o timing).
    verificacao_dia2: o resultado de verificar_bloco() sobre este mesmo
    bloco do Dia 2 -- reaproveitado para robustez e timing, nunca
    recalculado aqui.

    IMPORTANTE (pedido explicito, seccao 1 do pedido): "CONSISTENTE" NAO
    significa "BP confirmado com certeza estatistica". Significa que o
    PADRAO FISIOLOGICO observado no Dia 1 foi reproduzido de forma
    semelhante no Dia 2 -- direccao, timing e robustez sao tres pistas,
    nunca uma prova.

    O Dia 2 e' resumido pelo SEU ULTIMO intervalo -- e' onde a carga
    sustentada ja teve tempo de produzir o efeito fisiologico completo.
    """
    if not dia2_metricas_lista:
        return {'status': 'DADOS INSUFICIENTES',
               'motivo': 'sem intervalos do Dia 2 para este BP',
               'metricas': {}, 'potencia': None}

    dia2_final = dia2_metricas_lista[-1]
    timing1 = _timing_dia1(dia1_metricas, dia1_vizinhos)

    pot1 = (dia1_metricas or {}).get('potencia') or {}
    pot2 = dia2_final.get('potencia') or {}
    potencia = None
    if pot1.get('ok') and pot2.get('ok'):
        p1, p2 = pot1['media'], pot2['media']
        potencia = {'dia1_w': p1, 'dia2_w': p2,
                   'diferenca_w': round(p2 - p1, 1),
                   'diferenca_pct': round((p2 - p1) / p1 * 100, 1) if p1 else None}

    metricas = {}
    for chave, nome, unidade in _METRICAS_COMPARACAO:
        if chave == 'potencia':
            continue
        m = _consistencia_metrica(
            (dia1_metricas or {}).get(chave), dia2_final.get(chave))
        m['nome'] = nome
        m['unidade'] = unidade
        m['peso'] = 'complementar' if chave in _METRICAS_PESO_COMPLEMENTAR else 'principal'
        m['timing_dia1'] = (timing1 or {}).get(chave)
        m['timing_dia2'] = _timing_dia2(chave, verificacao_dia2)
        metricas[chave] = m

    # convergencia: so' as metricas de peso PRINCIPAL entram na fraccao
    # que decide o status -- DFA1 fica visivel mas nao pesa a decisao,
    # tal como pedido (evidencia complementar, nunca prova isolada)
    principais = {k: m for k, m in metricas.items()
                 if m['peso'] == 'principal'}
    validas = [m for m in principais.values() if m['consistencia'] != 'SEM DADOS']
    convergentes = [m for m in validas if m['consistencia'] == 'CONSISTENTE']
    divergentes = [k for k, m in principais.items() if m['consistencia'] == 'DIVERGENTE']
    sem_dados_lista = [k for k, m in metricas.items() if m['consistencia'] == 'SEM DADOS']

    # robustez: quantos dos sinais principais tem tendencia significativa
    # no proprio Dia 2 (reaproveitado de verificar_bloco, nao recalculado)
    robustos = [k for k, m in principais.items()
               if (m['timing_dia2'] or {}).get('concentrado_no_fim')]
    n_com_p = sum(1 for m in principais.values()
                 if (m['timing_dia2'] or {}).get('p_permutacao') is not None)

    if not validas:
        status = 'DADOS INSUFICIENTES'
    else:
        frac = len(convergentes) / len(validas)
        if frac >= 0.75:
            status = 'CONSISTENTE'
        elif frac >= 0.4:
            status = 'PARCIALMENTE CONSISTENTE'
        else:
            status = 'DIVERGENTE'

    aviso_n = None
    if len(dia2_metricas_lista) < 3:
        aviso_n = (f'apenas {len(dia2_metricas_lista)} intervalo(s) no '
                  f'Dia 2 para este bloco — dados insuficientes para um '
                  f'teste estatístico robusto; a robustez abaixo deve '
                  f'ser lida com essa reserva.')

    return {
        'status': status,
        'motivo': (f'{len(convergentes)} de {len(validas)} respostas '
                  f'fisiológicas principais mostram a mesma direcção '
                  f'nos dois dias — isto indica REPRODUTIBILIDADE do '
                  f'padrão observado, não uma confirmação estatística '
                  f'do breakpoint em si'
                  if validas else 'sem métricas com dados nos dois dias '
                  'para comparar'),
        'potencia': potencia,
        'metricas': metricas,
        'n_convergentes': len(convergentes), 'n_validas': len(validas),
        'divergentes': divergentes, 'sem_dados': sem_dados_lista,
        'robustez': {'n_sinais_com_permutacao': n_com_p,
                    'n_sinais_concentrados_no_fim': len(robustos),
                    'aviso_poucos_pontos': aviso_n,
                    'nota': 'a robustez usa o mesmo teste de permutação '
                    'já aplicado dentro do Dia 2 (verificar_bloco) — '
                    'avalia se a concordância observada é maior do que '
                    'seria esperada por acaso, nunca "prova" o BP'},
    }


def recovery_padrao_bloco(fracoes):
    """Padrao do bloco INTEIRO (todas as recuperacoes de BP1, ou todas
    as de BP2) -- nunca reduzido a' ultima sozinha. Usa as FRACCOES
    (recovery_fraction), ja normalizadas por direccao, por isso um so
    _tendencia serve para qualquer metrica sem precisar saber se e' HR
    ou SmO2: fraccao a subir ao longo do bloco = recovery a melhorar;
    a descer = recovery a piorar. Mesmo teste de permutacao de sempre,
    aplicado a esta serie.
    """
    validas = [f for f in fracoes if f is not None]
    if len(validas) < 2:
        return {'padrao': 'DADOS INSUFICIENTES',
               'motivo': f'só {len(validas)} fracção(ões) válida(s) — '
                        'precisa de pelo menos 2 para ver tendência',
               'fracoes': validas}

    tend, slope, p = _tendencia(validas)
    significativo = p is not None and p <= 0.34
    if not significativo:
        amplitude = max(validas) - min(validas)
        # sem tendencia detectavel: "estavel" se as fraccoes andam
        # proximas umas das outras, "inconsistente" se saltam (ex.:
        # boa, minima, boa) sem um padrao continuo
        padrao = 'ESTÁVEL' if amplitude < 0.3 else 'INCONSISTENTE'
    elif tend > 0:
        padrao = 'PROGRESSIVAMENTE MELHOR'
    else:
        padrao = 'PROGRESSIVAMENTE PIOR'

    return {'padrao': padrao, 'tendencia': tend, 'slope': slope,
           'p_permutacao': p, 'significativo': significativo,
           'fracoes': validas}


def comparar_recovery(dia1_recovery, dia2_works_metricas, dia2_recuperacoes_bloco):
    """Compara o padrao de recuperacao entre os dois dias -- rotulos
    proprios (CONVERGENTE/DIVERGENTE/INDETERMINADA), diferentes dos
    usados para BP (CONSISTENTE/DIVERGENTE).

    dia1_recovery: por_canal de UM metricas_recuperacao() do Dia 1.
    dia2_works_metricas: lista de metricas_intervalo() dos WORKS deste
    bloco no Dia 2 (bp1_m ou bp2_m) -- para calcular a fraccao de cada
    recovery em relacao ao work que a precedeu.
    dia2_recuperacoes_bloco: lista de metricas_recuperacao() das
    recuperacoes DENTRO do bloco -- len(works)-1 itens.

    Preserva TODAS as recuperacoes individuais (nao resume so' a
    ultima); o padrao do bloco (ESTAVEL/PIOR/MELHOR/INCONSISTENTE) vem
    de recovery_padrao_bloco sobre a sequencia inteira de fraccoes. A
    comparacao Dia1xDia2 usa esse padrao, nao um valor isolado.
    """
    validas_dia2 = [r for r in dia2_recuperacoes_bloco if r and r.get('ok')]
    if not dia1_recovery or not validas_dia2:
        return {'status': 'DADOS INSUFICIENTES',
               'motivo': 'sem recuperação válida num dos dois dias',
               'metricas': {}}

    metricas = {}
    for canal, nome, unidade in _METRICAS_COMPARACAO:
        if canal == 'potencia':
            continue
        c1 = (dia1_recovery or {}).get(canal)

        # todas as recuperacoes individuais deste canal, preservadas
        individuais = []
        fracoes = []
        for i, r in enumerate(dia2_recuperacoes_bloco):
            if not r or not r.get('ok'):
                individuais.append(None)
                fracoes.append(None)
                continue
            c2 = (r.get('por_canal') or {}).get(canal)
            individuais.append(c2)
            work_antes = dia2_works_metricas[i] if i < len(dia2_works_metricas) else None
            fracao = recovery_fraction(
                (work_antes or {}).get(canal), c2) if work_antes else None
            fracoes.append(fracao)

        padrao_bloco = recovery_padrao_bloco(fracoes)

        c2_ultima = next((c for c in reversed(individuais) if c), None)
        if not c1 or c1.get('estado') == 'sem_dados' or not c2_ultima or \
           c2_ultima.get('estado') == 'sem_dados':
            metricas[canal] = {
                'nome': nome, 'unidade': unidade,
                'peso': 'complementar' if canal in _METRICAS_PESO_COMPLEMENTAR else 'principal',
                'consistencia': 'INDETERMINADA',
                'dia1': None, 'dia2_recuperacoes_individuais': individuais,
                'dia2_fracoes': fracoes, 'dia2_padrao_bloco': padrao_bloco,
            }
            continue

        # a comparacao de estado usa a ULTIMA recuperacao valida (o
        # estado final do bloco), mas agora ao lado do padrao inteiro,
        # nunca no lugar dele
        igual = c1.get('estado') == c2_ultima.get('estado')
        metricas[canal] = {
            'nome': nome, 'unidade': unidade,
            'peso': 'complementar' if canal in _METRICAS_PESO_COMPLEMENTAR else 'principal',
            'consistencia': 'CONVERGENTE' if igual else 'DIVERGENTE',
            'dia1': {'estado': c1.get('estado'), 'inicial': c1.get('inicial'),
                    'final': c1.get('final'), 'delta': c1.get('delta')},
            'dia2_recuperacoes_individuais': individuais,
            'dia2_fracoes': fracoes,
            'dia2_padrao_bloco': padrao_bloco,
            'dia2_ultima': {'estado': c2_ultima.get('estado'),
                           'inicial': c2_ultima.get('inicial'),
                           'final': c2_ultima.get('final'),
                           'delta': c2_ultima.get('delta')},
        }

    principais = {k: m for k, m in metricas.items() if m['peso'] == 'principal'}
    validas = [m for m in principais.values() if m['consistencia'] != 'INDETERMINADA']
    convergentes = [m for m in validas if m['consistencia'] == 'CONVERGENTE']

    if not validas:
        status = 'DADOS INSUFICIENTES'
    else:
        frac = len(convergentes) / len(validas)
        status = 'CONVERGENTE' if frac >= 0.75 else (
            'PARCIALMENTE CONVERGENTE' if frac >= 0.4 else 'DIVERGENTE')

    return {
        'status': status, 'metricas': metricas,
        'n_convergentes': len(convergentes), 'n_validas': len(validas),
        'motivo': (f'{len(convergentes)} de {len(validas)} respostas de '
                  f'recuperação principais têm o mesmo sentido '
                  f'(recuperou/não recuperou) nos dois dias'
                  if validas else 'sem métricas de recuperação com dados '
                  'nos dois dias'),
    }
