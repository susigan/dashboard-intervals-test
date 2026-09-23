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

    Cinco faixas, verificadas nesta ordem exacta (overshoot e "sem
    recuperação" primeiro, para nunca caírem por engano nas faixas
    intermédias):
      fraccao > 1          -> OVERSHOOT — passou do que tinha antes do
                              work, também informação real, não um erro
      fraccao < 0          -> SEM RECUPERAÇÃO / CONTINUAÇÃO — a variável
                              continuou a mudar na mesma direcção do
                              work, em vez de reverter
      0 <= fraccao < 0.4   -> RECUPERAÇÃO MÍNIMA
      0.4 <= fraccao < 0.75 -> RECUPERAÇÃO PARCIAL
      0.75 <= fraccao <= 1  -> RECUPERAÇÃO (quase) COMPLETA
    """
    if fraccao is None:
        return 'DADOS INSUFICIENTES'
    if fraccao > 1:
        return 'OVERSHOOT'
    if fraccao < 0:
        return 'SEM RECUPERAÇÃO / CONTINUAÇÃO'
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
            'excluidos': resto[7:],  # nao classificados em nenhum BP --
            # so' exposto para auditoria (secao 5 do pedido); a regra de
            # corte em si (resto[:4]/resto[4:7]) nao foi tocada aqui
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


def comparar_recovery(dia1_recovery, dia2_works_metricas, dia2_recuperacoes_bloco,
                      dia2_recuperacoes_1min=None):
    """Compara o padrao de recuperacao entre os dois dias -- rotulos
    proprios (CONVERGENTE/DIVERGENTE/INDETERMINADA), diferentes dos
    usados para BP (CONSISTENTE/DIVERGENTE).

    dia1_recovery: por_canal de UM metricas_recuperacao() do Dia 1.
    dia2_works_metricas: lista de metricas_intervalo() dos WORKS deste
    bloco no Dia 2 (bp1_m ou bp2_m) -- para calcular a fraccao de cada
    recovery em relacao ao work que a precedeu.
    dia2_recuperacoes_bloco: lista de metricas_recuperacao() das
    recuperacoes DENTRO do bloco -- len(works)-1 itens. Usada para a
    TRAJECTORIA inteira (fraccoes, padrao do bloco) -- nunca cortada.
    dia2_recuperacoes_1min: opcional -- a MESMA lista, mas cada item
    calculado so' sobre os primeiros ~60s de cada recovery (o Dia 1
    e' uma rampa de transicoes de ~1min; comparar o recovery inteiro
    do Dia 2, que pode durar varios minutos, contra uma transicao de
    1min do Dia 1 nao seria uma janela temporal comparavel). Se dado,
    e' esta lista que decide CONVERGENTE/DIVERGENTE por metrica -- a
    trajectoria continua a usar sempre o recovery completo.

    Preserva TODAS as recuperacoes individuais (nao resume so' a
    ultima); o padrao do bloco (ESTAVEL/PIOR/MELHOR/INCONSISTENTE) vem
    de recovery_padrao_bloco sobre a sequencia inteira de fraccoes. A
    comparacao Dia1xDia2 usa esse padrao, nao um valor isolado.
    """
    lista_para_comparacao = dia2_recuperacoes_1min or dia2_recuperacoes_bloco
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

        # o ESTADO usado na comparacao Dia1xDia2 ve' da janela
        # comparavel (1min, se dada) -- a trajectoria acima (fracoes,
        # padrao_bloco) usa sempre o recovery completo, nunca cortado
        individuais_1min = [
            (r.get('por_canal') or {}).get(canal) if r and r.get('ok') else None
            for r in lista_para_comparacao]
        c2_ultima = next((c for c in reversed(individuais_1min) if c), None)

        # fracção calculada NA MESMA janela usada para a comparação
        # (0-60s quando dia2_recuperacoes_1min é dado) -- ao lado da
        # fracção do recovery completo (fracoes[-1]), nunca no lugar
        # dela, para se poder ver directamente se um OVERSHOOT aparece
        # numa janela e nao na outra (pedido explícito)
        idx_ultima = len(lista_para_comparacao) - 1
        work_ultima = dia2_works_metricas[idx_ultima] if 0 <= idx_ultima < len(dia2_works_metricas) else None
        fracao_comparavel = recovery_fraction(
            (work_ultima or {}).get(canal), c2_ultima) if (work_ultima and c2_ultima) else None
        completude_comparavel = classificar_recovery_completeness(fracao_comparavel)
        duracao_comparavel = (lista_para_comparacao[idx_ultima] or {}).get('duracao_s') \
            if 0 <= idx_ultima < len(lista_para_comparacao) else None
        duracao_completa = (dia2_recuperacoes_bloco[idx_ultima] or {}).get('duracao_s') \
            if 0 <= idx_ultima < len(dia2_recuperacoes_bloco) else None

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
                           'delta': c2_ultima.get('delta'),
                           'fracao': fracao_comparavel,
                           'completude': completude_comparavel,
                           'duracao_s': duracao_comparavel},
            'dia2_ultima_completa': {'fracao': fracoes[idx_ultima] if 0 <= idx_ultima < len(fracoes) else None,
                                     'completude': classificar_recovery_completeness(
                                         fracoes[idx_ultima] if 0 <= idx_ultima < len(fracoes) else None),
                                     'duracao_s': duracao_completa},
            'janela_comparacao': '1min' if dia2_recuperacoes_1min else 'completa',
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


def comparar_rpe(dia1_rpe_base, dia1_rpe_alvo, dia2_rpes_bloco):
    """RPE — evidência PERCEPTIVA complementar, nunca decide BP sozinha
    (nunca entra nos pesos 'principal'/'complementar' de comparar_bp,
    nunca entra na fracção de convergência que decide o status global).

    dia1_rpe_base: RPE do primeiro bloco gravado no Dia 1 (referência de
    "antes"), já existente em moxy_rpe.
    dia1_rpe_alvo: RPE do bloco do Dia 1 mais próximo do alvo de watts
    deste BP -- o MESMO bloco (b1_bp1/b1_bp2) já usado para a
    comparação fisiológica, não um novo emparelhamento.
    dia2_rpes_bloco: lista ordenada de RPEs dos WORKs do Dia 2 que
    compõem este BP (ex.: os 4 WORKs de BP1) -- já gravados em
    moxy_rpe para a sessão VST.

    Direcção: mesma lógica conceptual já usada (↑/↓/→ pelo sinal do
    delta, limiar de 2% adaptado aqui para RPE inteiro: qualquer
    mudança >=1 ponto conta como direcção real, 0 é estável).
    """
    tem_d1 = dia1_rpe_base is not None and dia1_rpe_alvo is not None
    validos_d2 = [r for r in (dia2_rpes_bloco or []) if r is not None]
    tem_d2 = len(validos_d2) >= 1

    if not tem_d1 or not tem_d2:
        return {'status': 'DADOS INSUFICIENTES',
               'motivo': 'RPE ausente no Dia 1 ou no Dia 2 para este BP',
               'dia1': None, 'dia2': None}

    d1_inicial, d1_final = dia1_rpe_base, dia1_rpe_alvo
    d2_inicial, d2_final = validos_d2[0], validos_d2[-1]
    delta1, delta2 = d1_final - d1_inicial, d2_final - d2_inicial

    def _dir(delta):
        if delta >= 1:
            return '↑'
        if delta <= -1:
            return '↓'
        return '→'
    dir1, dir2 = _dir(delta1), _dir(delta2)

    if dir1 == dir2:
        status = 'CONSISTENTE'
        motivo = (f'A percepção de esforço mudou na mesma direcção nos '
                  f'dois dias (RPE {dir1}).')
    elif dir1 == '→' or dir2 == '→':
        status = 'PARCIAL'
        motivo = 'A percepção de esforço mudou claramente só num dos dias.'
    else:
        status = 'DIVERGENTE'
        motivo = 'A percepção de esforço mudou em direcções opostas nos dois dias.'

    # timing dentro do Dia 2: em que WORK (indice, 1-based) ocorre o
    # maior salto de RPE entre WORKs consecutivos -- so' informativo,
    # nao um novo teste estatistico
    maior_salto = None
    if len(validos_d2) >= 2:
        saltos = [abs(validos_d2[i+1]-validos_d2[i]) for i in range(len(validos_d2)-1)]
        idx = saltos.index(max(saltos))
        maior_salto = {'de_work': idx+1, 'para_work': idx+2, 'delta': saltos[idx]}

    return {
        'status': status, 'motivo': motivo,
        'dia1': {'inicial': d1_inicial, 'final': d1_final, 'delta': delta1, 'direccao': dir1},
        'dia2': {'inicial': d2_inicial, 'final': d2_final, 'delta': delta2, 'direccao': dir2,
                'valores': validos_d2, 'maior_salto': maior_salto},
    }


def profilage_estrutura_works(estrutura, canais, tempo):
    """PROFILAGE — auditoria + ENTRY/EXIT por WORK, a partir da estrutura
    já classificada por estruturar_protocolo() -- NÃO reclassifica nada,
    só percorre aquecimento/bp1/bp2/excluidos (nesta ordem temporal) e
    reaproveita metricas_intervalo() (já existente, usada em toda a
    comparação Dia1×Dia2) para cada bloco.

    ENTRY = 'inicial' de cada canal (já calculado por metricas_intervalo,
    via _metricas_variavel — janela terminal no INÍCIO do intervalo).
    EXIT  = 'final' do mesmo canal (janela terminal no FIM).
    Δ (EXIT-ENTRY) = 'delta', também já calculado ali — não recalculado
    aqui, só relido do mesmo dict.

    Devolve {'linhas': [...]}, uma linha por WORK/aquecimento, cada uma
    já com auditoria + entry + exit + delta prontos para tabela.
    """
    linhas = []
    ordem = 1

    def _linha(bloco, tipo, bp):
        nonlocal ordem
        m = metricas_intervalo(canais, tempo, bloco['t0'], bloco['t1'],
                               bloco.get('watts_medio_da_api'))
        canais_fis = ('hr', 'respiracao', 'smo2', 'thb', 'dfa1')
        entry, exit_, delta, delta_pct = {}, {}, {}, {}
        for c in canais_fis:
            v = m.get(c) or {}
            entry[c] = v.get('inicial') if v.get('ok') else None
            exit_[c] = v.get('final') if v.get('ok') else None
            delta[c] = v.get('delta') if v.get('ok') else None
            delta_pct[c] = v.get('delta_pct') if v.get('ok') else None
        linha = {
            'ordem': ordem, 'tipo': tipo, 'bp': bp,
            't0': bloco['t0'], 't1': bloco['t1'],
            'duracao_s': round(bloco['t1'] - bloco['t0'], 1),
            'duracao_curta': bool(bloco.get('duracao_curta')),
            'potencia_media': (m.get('potencia') or {}).get('media')
                              if (m.get('potencia') or {}).get('ok') else None,
            'entry': entry, 'exit': exit_, 'delta': delta,
            'delta_pct': delta_pct,
        }
        linhas.append(linha)
        ordem += 1
        return linha

    if estrutura.get('aquecimento'):
        _linha(estrutura['aquecimento'], 'AQUECIMENTO', None)
    for b in estrutura.get('bp1') or []:
        _linha(b, 'WORK', 'BP1')
    for b in estrutura.get('bp2') or []:
        _linha(b, 'WORK', 'BP2')
    for b in estrutura.get('excluidos') or []:
        _linha(b, 'WORK', None)  # nao classificado -- ver aviso da estrutura

    return {'linhas': linhas, 'aviso_estrutura': estrutura.get('aviso'),
           'n_excluidos': len(estrutura.get('excluidos') or [])}


def profilage_drift_intra_work(linhas):
    """DRIFT INTRA-WORK — direcção (↑/↓/→) da resposta dentro de cada WORK
    já classificado, a partir das linhas já produzidas por
    profilage_estrutura_works() -- NÃO recalcula ENTRY/EXIT/Δ, só lê
    'delta_pct' (já calculado por metricas_intervalo via
    _metricas_variavel) e classifica com _direccao(), a MESMA função e
    o MESMO limiar de 2% já usados em comparar_bp() para estes mesmos
    canais.

    Cada canal fica separado -- nunca combinado num score ou índice.
    Só WORKs reais (tipo='WORK') entram aqui; aquecimento fica de fora
    (não é um WORK sustentado do protocolo).
    """
    canais_fis = (('hr', 'HR'), ('respiracao', 'RF'), ('smo2', 'SmO2'),
                 ('thb', 'THb'), ('dfa1', 'DFA-α1'))
    works = [l for l in linhas if l.get('tipo') == 'WORK']

    linhas_drift = []
    for l in works:
        direccoes = {}
        for chave, _nome in canais_fis:
            dp = (l.get('delta_pct') or {}).get(chave)
            direccoes[chave] = _direccao(dp) if dp is not None else \
                ('dados insuficientes' if l.get('entry', {}).get(chave) is None
                 or l.get('exit', {}).get(chave) is None else '→')
        linhas_drift.append({
            'ordem': l['ordem'], 'bp': l.get('bp'),
            'potencia_media': l.get('potencia_media'),
            'entry': l.get('entry'), 'exit': l.get('exit'),
            'delta': l.get('delta'), 'delta_pct': l.get('delta_pct'),
            'direccao': direccoes,
        })

    # sintese descritiva por bloco -- so' descreve o que se ve nas
    # direccoes de cada canal ao longo dos WORKs desse bloco, NUNCA um
    # score. "predominante" = a direccao que aparece em mais WORKs
    # desse bloco para aquele canal; empate fica "misto".
    def _sintese_bloco(bp_nome):
        do_bloco = [l for l in linhas_drift if l['bp'] == bp_nome]
        if not do_bloco:
            return None
        fora = {'bp': bp_nome, 'n_works': len(do_bloco), 'por_canal': {}}
        for chave, nome in canais_fis:
            dirs = [l['direccao'][chave] for l in do_bloco]
            validas = [d for d in dirs if d in ('↑', '↓', '→')]
            if not validas:
                fora['por_canal'][chave] = {'nome': nome,
                                            'predominante': 'dados insuficientes'}
                continue
            contagem = {d: validas.count(d) for d in set(validas)}
            maxc = max(contagem.values())
            empatados = [d for d, n in contagem.items() if n == maxc]
            predominante = empatados[0] if len(empatados) == 1 else 'misto'
            fora['por_canal'][chave] = {
                'nome': nome, 'predominante': predominante,
                'contagem': contagem, 'n_dados_insuficientes':
                    sum(1 for d in dirs if d not in ('↑', '↓', '→'))}
        return fora

    return {
        'linhas': linhas_drift,
        'sintese_bp1': _sintese_bloco('BP1'),
        'sintese_bp2': _sintese_bloco('BP2'),
    }


# ACCUMULATION -- limiar ABSOLUTO por canal, NAO percentual. Motivo
# (explicito no pedido): a mesma alteracao absoluta produz percentuais
# diferentes consoante o valor de entrada, o que fazia BP2 (que parte
# de valores mais altos) parecer "estavel" quando na verdade mudou tanto
# quanto BP1. Valores escolhidos ao nivel do ruido tipico de cada canal
# -- nao vem de nenhum artigo, e' um criterio novo, proprio desta etapa,
# documentado aqui para poder ser revisto.
LIMIAR_ABSOLUTO_ACCUMULATION = {
    'hr': 2.0,          # bpm
    'respiracao': 1.0,  # respiracoes/min
    'smo2': 1.0,        # % (unidades do proprio sinal)
    'thb': 0.1,         # g/dL
    'dfa1': 0.05,       # unidade DFA-alfa1
}


def _classificar_progressao(valores, limiar_absoluto):
    """valores: lista ORDENADA por WORK (ex.: ENTRY de W1..W4 de um BP).

    Classifica por CONSISTENCIA DE SINAL dos deltas consecutivos, usando
    um limiar ABSOLUTO (nunca percentual -- ver nota acima de
    LIMIAR_ABSOLUTO_ACCUMULATION). Um delta so' conta como "com sinal"
    se |delta| >= limiar; deltas abaixo disso sao tratados como parte
    do ruido de medicao, nem a favor nem contra uma progressao.
    """
    validos = [v for v in valores if v is not None]
    if len(validos) < 2 or len(validos) != len(valores):
        return {'classificacao': 'dados insuficientes', 'deltas_consecutivos': [],
               'delta_total': None, 'valores': valores}

    deltas = [round(validos[i + 1] - validos[i], 3) for i in range(len(validos) - 1)]
    delta_total = round(validos[-1] - validos[0], 3)
    sinais = [1 if d >= limiar_absoluto else (-1 if d <= -limiar_absoluto else 0)
             for d in deltas]
    com_sinal = [s for s in sinais if s != 0]

    if not com_sinal:
        classificacao = 'estável'
    elif all(s == com_sinal[0] for s in com_sinal) and len(com_sinal) == len(deltas):
        classificacao = 'progressão consistente'
    elif all(s == com_sinal[0] for s in com_sinal):
        # todos os passos COM sinal concordam, mas ha' passos "planos"
        # misturados -- ainda e' consistente na direccao, so' nao em
        # todos os passos
        classificacao = 'progressão consistente' if len(com_sinal) >= len(deltas) - 1 \
            else 'progressão parcial'
    else:
        maioria = max(com_sinal.count(1), com_sinal.count(-1))
        classificacao = 'progressão parcial' if maioria > len(com_sinal) / 2 \
            else 'sem progressão consistente'

    return {'classificacao': classificacao, 'deltas_consecutivos': deltas,
           'delta_total': delta_total, 'valores': validos}


def profilage_accumulation(linhas):
    """ACCUMULATION / DRIFT ENTRE WORKs -- progressao de ENTRY e EXIT ao
    longo dos WORKs de cada bloco (BP1 e BP2 SEPARADOS -- nunca comparados
    directamente entre si, cargas diferentes). So' le' as linhas ja
    produzidas por profilage_estrutura_works() (entry/exit/potencia_media
    por WORK); nao recalcula nada disso, nao toca em bp1/bp2/drift.
    """
    canais_fis = (('hr', 'HR'), ('respiracao', 'RF'), ('smo2', 'SmO2'),
                 ('thb', 'THb'), ('dfa1', 'DFA-α1'))

    def _bloco(bp_nome):
        works = sorted([l for l in linhas if l.get('tipo') == 'WORK'
                       and l.get('bp') == bp_nome], key=lambda l: l['ordem'])
        if not works:
            return None
        fora = {'bp': bp_nome, 'n_works': len(works),
               'works': [{'ordem': w['ordem'], 'potencia_media': w.get('potencia_media')}
                        for w in works],
               'por_canal': {}}
        for chave, nome in canais_fis:
            limiar = LIMIAR_ABSOLUTO_ACCUMULATION[chave]
            entry_vals = [w['entry'].get(chave) for w in works]
            exit_vals = [w['exit'].get(chave) for w in works]
            fora['por_canal'][chave] = {
                'nome': nome, 'limiar_absoluto': limiar,
                'entry': _classificar_progressao(entry_vals, limiar),
                'exit': _classificar_progressao(exit_vals, limiar),
            }
        return fora

    bp1 = _bloco('BP1')
    bp2 = _bloco('BP2')

    # convergencia -- so' descreve quantos canais mostram progressao na
    # MESMA direccao (nunca um score); usa o EXIT (estado de saida de
    # cada WORK) como referencia, por ser o que mais se aproxima do
    # "estado acumulado" ao fim de cada WORK
    def _convergencia(bloco):
        if not bloco:
            return None
        em_progressao = []
        for chave, info in bloco['por_canal'].items():
            c = info['exit']['classificacao']
            if c in ('progressão consistente', 'progressão parcial'):
                sinal = None
                dts = info['exit']['deltas_consecutivos']
                if dts:
                    positivos = sum(1 for d in dts if d > 0)
                    negativos = sum(1 for d in dts if d < 0)
                    sinal = '↑' if positivos >= negativos else '↓'
                em_progressao.append({'canal': info['nome'], 'direccao': sinal,
                                      'classificacao': c})
        return {
            'n_canais_em_progressao': len(em_progressao),
            'canais': em_progressao,
            'nota': ('múltiplas métricas apresentam alteração progressiva'
                     if len(em_progressao) >= 2 else
                     ('uma métrica apresenta alteração progressiva'
                      if len(em_progressao) == 1 else
                      'nenhuma métrica com progressão consistente detectada')),
        }

    return {
        'bp1': bp1, 'bp2': bp2,
        'convergencia_bp1': _convergencia(bp1),
        'convergencia_bp2': _convergencia(bp2),
    }


# PRIMEIRA DIVERGÊNCIA TEMPORAL -- usa os MESMOS dados temporais ja
# usados pelos graficos Power x HR/RF/SmO2/THb/DFA1 (canais/tempo, os
# streams reais da sessao, ja passados a metricas_intervalo em toda a
# tab). Reaproveita _serie_na_janela() para extrair a serie real dentro
# de cada WORK, e a MESMA metodologia de baseline de _metricas_variavel
# (media do primeiro quarto de pontos -- nao o primeiro ponto isolado).
#
# Sustentacao: uma divergencia so' conta se, a partir do ponto onde o
# desvio ultrapassa o limiar (os MESMOS limiares absolutos ja definidos
# em LIMIAR_ABSOLUTO_ACCUMULATION -- nao um criterio novo), pelo menos
# 70% dos pontos restantes do WORK ficam do mesmo lado. Isto e' o
# criterio que evita um pico isolado: um unico ponto fora nunca basta,
# porque teria de "arrastar" a maioria dos pontos seguintes consigo.

FRACAO_SUSTENTACAO_DIVERGENCIA = 0.70
MIN_PONTOS_DIVERGENCIA = 6  # abaixo disto nao ha' dados para provar sustentacao


def _primeira_divergencia_canal(serie, baseline, limiar_absoluto):
    """serie: [(t,v), ...] real, ja sem None (de _serie_na_janela).
    baseline: valor de referencia (mesma janela inicial de
    _metricas_variavel -- passada por fora, nao recalculada aqui).
    Devolve o primeiro (t, v) cujo desvio sustenta-se por >=70% dos
    pontos seguintes, ou None se nao houver.
    """
    if len(serie) < MIN_PONTOS_DIVERGENCIA:
        return {'status': 'DADOS INSUFICIENTES'}
    for i, (t_i, v_i) in enumerate(serie):
        desvio = v_i - baseline
        if abs(desvio) < limiar_absoluto:
            continue
        sinal = 1 if desvio > 0 else -1
        resto = serie[i:]
        if len(resto) < 3:
            continue
        concordam = sum(1 for _, v in resto if (v - baseline) * sinal > 0)
        if concordam / len(resto) >= FRACAO_SUSTENTACAO_DIVERGENCIA:
            return {'status': 'ok', 't': round(t_i, 1), 'valor': round(v_i, 2),
                   'baseline': round(baseline, 2),
                   'direccao': '↑' if sinal > 0 else '↓',
                   'n_pontos_apos': len(resto)}
    return {'status': 'sem divergência sustentada detectada'}


def profilage_primeira_divergencia(estrutura, canais, tempo):
    """Para cada WORK de BP1/BP2 (nunca o residual), para cada canal
    fisiologico, encontra o primeiro momento de divergencia sustentada
    dentro do proprio WORK, ordenado por tempo. Reaproveita
    metricas_intervalo() so' para obter o 'inicial' (baseline), e
    _serie_na_janela() para a serie real -- nenhuma nova aquisicao de
    dados, nenhum novo calculo de baseline.
    """
    canais_fis = (('hr', 'HR', 'heartrate'), ('respiracao', 'RF', 'respiration'),
                 ('smo2', 'SmO2', 'smo2'), ('thb', 'THb', 'thb'),
                 ('dfa1', 'DFA-α1', 'dfa_a1'))

    def _works_do_bloco(bp_nome):
        return sorted([b for b in (estrutura.get(bp_nome.lower()) or [])],
                     key=lambda b: b['t0'])

    def _analisar_work(bloco, numero, bp_nome):
        t0, t1 = bloco['t0'], bloco['t1']
        m = metricas_intervalo(canais, tempo, t0, t1, bloco.get('watts_medio_da_api'))
        resultados = []
        for chave, nome, chave_canal in canais_fis:
            info = m.get(chave) or {}
            if not info.get('ok'):
                resultados.append({'metrica': nome, 'status': 'DADOS INSUFICIENTES'})
                continue
            baseline = info['inicial']
            serie = _serie_na_janela(tempo, canais.get(chave_canal) or [], t0, t1)
            # excluir a JANELA de baseline da busca -- a mesma fatia
            # exacta que _metricas_variavel usa para calcular 'inicial'
            # (primeiro quarto de pontos). Sem isto, um ponto DENTRO do
            # proprio baseline podia "divergir" da media desse baseline
            # e aparecer como t=0s -- que e' o bug reportado: primeira
            # divergencia tem de ser DEPOIS do periodo usado para
            # estabelecer o estado inicial, nunca dentro dele.
            n_baseline = max(1, len(serie) // 4)
            serie_apos_baseline = serie[n_baseline:]
            limiar = LIMIAR_ABSOLUTO_ACCUMULATION[chave]
            div = _primeira_divergencia_canal(serie_apos_baseline, baseline, limiar)
            if div['status'] == 'ok':
                resultados.append({'metrica': nome, 'status': 'ok',
                                   't_relativo': round(div['t'] - t0, 1),
                                   'valor_inicial': baseline, 'valor_no_momento': div['valor'],
                                   'direccao': div['direccao']})
            else:
                resultados.append({'metrica': nome, 'status': div['status']})
        # ordenar: as que tem 'ok' primeiro, por t_relativo crescente;
        # as sem divergencia/insuficientes vao depois, nessa ordem
        resultados.sort(key=lambda r: (r['status'] != 'ok',
                                       r.get('t_relativo', float('inf'))))
        return {
            'numero': numero, 'bp': bp_nome,
            'potencia_media': (m.get('potencia') or {}).get('media')
                              if (m.get('potencia') or {}).get('ok') else None,
            'duracao_s': round(t1 - t0, 1),
            'metricas_ordenadas': resultados,
            'nota_power': ('WORK a potência sustentada (não em rampa) — a '
                          'divergência encontrada não é explicada por uma '
                          'mudança de carga dentro do próprio WORK; se a '
                          'potência real não for constante, este contexto '
                          'não permite separar isso automaticamente.'),
        }

    def _bloco_completo(bp_nome):
        works = _works_do_bloco(bp_nome)
        if not works:
            return None
        analisados = [_analisar_work(b, i + 1, bp_nome) for i, b in enumerate(works)]
        # sintese: qual metrica aparece mais vezes em 1o lugar (com status 'ok')
        primeiras = [w['metricas_ordenadas'][0]['metrica'] for w in analisados
                    if w['metricas_ordenadas'] and w['metricas_ordenadas'][0]['status'] == 'ok']
        sintese = 'dados insuficientes'
        if primeiras:
            contagem = {m: primeiras.count(m) for m in set(primeiras)}
            maxc = max(contagem.values())
            top = [m for m, n in contagem.items() if n == maxc]
            sintese = (f'{top[0]} apareceu primeiro em {maxc} de {len(analisados)} WORKs'
                      if len(top) == 1 else 'empate / padrão misto')
        return {'bp': bp_nome, 'works': analisados,
               'sintese': ('métrica que mais frequentemente apresentou '
                          'primeira divergência: ' + sintese)}

    return {'bp1': _bloco_completo('BP1'), 'bp2': _bloco_completo('BP2')}


# CONVERGÊNCIA TEMPORAL -- so' le' os resultados JA calculados por
# profilage_primeira_divergencia() (cada WORK ja tem, por metrica,
# t_relativo/direccao/status, ja ordenados por tempo); nao recalcula
# divergencia com outra metodologia.
#
# Janela de proximidade: proporcional a duracao do WORK, mesmo estilo
# ja usado em _terminal() (janela = fracao da duracao, com piso e tecto
# absolutos, nunca um numero fixo arbitrario de segundos cravado). Nao
# existia nenhuma convencao de "proximidade temporal" no codigo antes
# desta etapa -- este e' o criterio novo, documentado aqui.
def _janela_proximidade(duracao_work_s, fracao=0.15, minimo_s=5, maximo_s=45):
    return max(minimo_s, min(maximo_s, duracao_work_s * fracao))


def profilage_convergencia_temporal(divergencia):
    """divergencia: o dict ja devolvido por profilage_primeira_divergencia()
    (com 'bp1'/'bp2', cada um com 'works', cada WORK com
    'metricas_ordenadas' ja calculadas e ja ordenadas por tempo).
    """
    def _analisar_work(w):
        janela = _janela_proximidade(w['duracao_s'])
        com_div = [m for m in w['metricas_ordenadas'] if m['status'] == 'ok']
        sem_div = [m for m in w['metricas_ordenadas'] if m['status'] != 'ok']

        if len(com_div) < 2:
            return {'numero': w['numero'], 'bp': w['bp'],
                   'potencia_media': w.get('potencia_media'),
                   'duracao_s': w['duracao_s'], 'janela_proximidade_s': round(janela, 1),
                   'classificacao': 'DADOS INSUFICIENTES',
                   'primeira': com_div[0] if com_div else None,
                   'grupo_proximo': [], 'tardias': com_div[1:] if com_div else [],
                   'sem_divergencia': [m['metrica'] for m in sem_div],
                   'resumo': ('menos de duas métricas com divergência sustentada — '
                             'não é possível avaliar agrupamento temporal.')}

        primeira = com_div[0]  # ja' vem ordenado por t_relativo (profilage_primeira_divergencia)
        grupo, tardias = [primeira], []
        for m in com_div[1:]:
            if abs(m['t_relativo'] - primeira['t_relativo']) <= janela:
                grupo.append(m)
            else:
                tardias.append(m)

        # matriz temporal simetrica (so' entre as que TEM divergencia)
        nomes = [m['metrica'] for m in com_div]
        tempos = {m['metrica']: m['t_relativo'] for m in com_div}
        matriz = {a: {b: (abs(tempos[a] - tempos[b]) <= janela if a != b else None)
                     for b in nomes} for a in nomes}

        if len(grupo) == len(com_div) or (len(com_div) >= 3 and len(tardias) <= 1):
            classificacao = 'CONVERGÊNCIA TEMPORAL'
        elif len(grupo) >= 2:
            classificacao = 'CONVERGÊNCIA PARCIAL'
        else:
            classificacao = 'RESPOSTAS DISPERSAS'

        partes = []
        if len(grupo) > 1:
            partes.append(classificacao.replace('_', ' ').title() + ' entre '
                         + '/'.join(m['metrica'] for m in grupo))
        else:
            partes.append('sem agrupamento temporal claro')
        if tardias:
            partes.append((tardias[0]['metrica'] if len(tardias) == 1 else
                          '/'.join(m['metrica'] for m in tardias))
                         + ' apresentou resposta tardia')
        if sem_div:
            sem_div_txt = []
            for grupo_status in set(m['status'] for m in sem_div):
                nomes_grupo = [m['metrica'] for m in sem_div if m['status'] == grupo_status]
                rotulo = ('sem divergência sustentada' if grupo_status ==
                         'sem divergência sustentada detectada' else grupo_status.lower())
                sem_div_txt.append('/'.join(nomes_grupo) + ' — ' + rotulo)
            partes.append('; '.join(sem_div_txt))

        return {'numero': w['numero'], 'bp': w['bp'],
               'potencia_media': w.get('potencia_media'), 'duracao_s': w['duracao_s'],
               'janela_proximidade_s': round(janela, 1), 'classificacao': classificacao,
               'primeira': primeira, 'grupo_proximo': grupo, 'tardias': tardias,
               'sem_divergencia': [m['metrica'] for m in sem_div],
               'matriz': matriz, 'resumo': '; '.join(partes) + '.'}

    def _sintese_bloco(bp_nome, works_analisados):
        do_bloco = [w for w in works_analisados if w['bp'] == bp_nome]
        if not do_bloco:
            return None
        contagem_class = {}
        for w in do_bloco:
            contagem_class[w['classificacao']] = contagem_class.get(w['classificacao'], 0) + 1
        # frequencia de participacao no grupo proximo, por metrica
        freq = {}
        for w in do_bloco:
            for m in w.get('grupo_proximo') or []:
                freq[m['metrica']] = freq.get(m['metrica'], 0) + 1
        n = len(do_bloco)
        participacao = {nome: f'{cont}/{n}' for nome, cont in freq.items()}
        predominante = max(contagem_class.items(), key=lambda kv: kv[1])[0] \
            if contagem_class else 'DADOS INSUFICIENTES'
        return {'bp': bp_nome, 'n_works': n, 'contagem_classificacao': contagem_class,
               'padrao_predominante': predominante, 'participacao_por_metrica': participacao}

    works_analisados = []
    for w in (divergencia.get('bp1') or {}).get('works') or []:
        works_analisados.append(_analisar_work(w))
    for w in (divergencia.get('bp2') or {}).get('works') or []:
        works_analisados.append(_analisar_work(w))

    sintese_bp1 = _sintese_bloco('BP1', works_analisados)
    sintese_bp2 = _sintese_bloco('BP2', works_analisados)

    # comparacao BP1 x BP2 -- so' descreve se o padrao PREDOMINANTE (a
    # classificacao mais frequente em cada bloco) e' igual, parcial ou
    # diferente; nunca atribui causa
    comparacao = 'dados insuficientes'
    if sintese_bp1 and sintese_bp2:
        p1, p2 = sintese_bp1['padrao_predominante'], sintese_bp2['padrao_predominante']
        ordem = ['CONVERGÊNCIA TEMPORAL', 'CONVERGÊNCIA PARCIAL',
                'RESPOSTAS DISPERSAS', 'DADOS INSUFICIENTES']
        if 'DADOS INSUFICIENTES' in (p1, p2):
            comparacao = 'dados insuficientes'
        elif p1 == p2:
            comparacao = 'semelhante'
        elif abs(ordem.index(p1) - ordem.index(p2)) == 1:
            comparacao = 'parcialmente semelhante'
        else:
            comparacao = 'diferente'

    return {
        'works': works_analisados,
        'sintese_bp1': sintese_bp1, 'sintese_bp2': sintese_bp2,
        'comparacao_bp1_bp2': comparacao,
        'nota_metodologia': ('proximidade avalia so' + "'" + ' o TEMPO entre as '
                             'primeiras divergências, nunca o valor ou magnitude da '
                             'métrica; janela = 15% da duração do WORK (mínimo 5s, '
                             'máximo 45s).'),
    }


# ============================================================
# LIMITER / PADRÃO FISIOLÓGICO -- camada de integração pura.
# Consome DRIFT, ACCUMULATION, PRIMEIRA DIVERGÊNCIA, CONVERGÊNCIA
# TEMPORAL, RECOVERY e RPE já calculados; não recalcula nenhum sinal
# fisiológico, não introduz score, não escolhe "vencedor".
# ============================================================

GRUPOS_FISIOLOGICOS = {
    'cardiorrespiratorio': ('HR', 'RF'),
    'periferico': ('SmO2',),        # THb entra so' como contexto, nunca sozinho
    'autonomico': ('DFA-α1',),      # complementar -- nunca sozinho decide
}


def _estado_metrica_no_bloco(metrica_nome, works_bloco_divergencia, works_bloco_convergencia):
    """Estado textual de UMA metrica ao longo de todos os WORKs de um
    bloco (BP1 ou BP2), combinando:
    - repeticao (quantos WORKs tiveram divergencia sustentada);
    - participacao no grupo proximo da convergencia temporal (vs tardia).
    Devolve um dos 6 estados do item 18: consistente/parcial/tardio/
    ausente/insuficiente. ('contextual' e' aplicado por fora, so' para
    THb, nao aqui.)
    """
    n = len(works_bloco_divergencia)
    if n == 0:
        return {'estado': 'insuficiente', 'n_com_divergencia': 0, 'n_total': 0,
               'n_no_grupo': 0, 'fraccao_divergencia': None}

    resultados = []
    for wd in works_bloco_divergencia:
        m = next((x for x in wd['metricas_ordenadas'] if x['metrica'] == metrica_nome), None)
        resultados.append(m)

    n_dados_insuf = sum(1 for m in resultados if m and m['status'] == 'DADOS INSUFICIENTES')
    com_div = [m for m in resultados if m and m['status'] == 'ok']
    n_com_div = len(com_div)

    if n_com_div == 0:
        estado = 'insuficiente' if n_dados_insuf > n / 2 else 'ausente'
        return {'estado': estado, 'n_com_divergencia': 0, 'n_total': n,
               'n_no_grupo': 0, 'fraccao_divergencia': round(n_com_div / n, 2)}

    # participacao no grupo proximo (convergencia temporal), por WORK
    n_no_grupo = 0
    for wc in works_bloco_convergencia:
        if any(g['metrica'] == metrica_nome for g in (wc.get('grupo_proximo') or [])):
            n_no_grupo += 1

    fraccao_div = n_com_div / n
    fraccao_grupo = n_no_grupo / n_com_div if n_com_div else 0

    if fraccao_div >= 0.66 and fraccao_grupo >= 0.66:
        estado = 'consistente'
    elif fraccao_div < 0.34:
        estado = 'parcial'
    elif fraccao_grupo < 0.34:
        estado = 'tardio'
    else:
        estado = 'parcial'

    return {'estado': estado, 'n_com_divergencia': n_com_div, 'n_total': n,
           'n_no_grupo': n_no_grupo, 'fraccao_divergencia': round(fraccao_div, 2)}


def _avaliar_grupo_cardiorrespiratorio(estado_hr, estado_rf):
    fortes = {'consistente'}
    hr_forte, rf_forte = estado_hr['estado'] in fortes, estado_rf['estado'] in fortes
    hr_algo = estado_hr['estado'] in ('consistente', 'parcial', 'tardio')
    rf_algo = estado_rf['estado'] in ('consistente', 'parcial', 'tardio')
    if hr_forte and rf_forte:
        return {'nivel': 'forte', 'nota': 'HR e RF respondem conjuntamente'}
    if hr_algo and rf_algo:
        return {'nivel': 'parcial', 'nota': 'HR e RF respondem, mas não ambos de forma consistente'}
    if hr_algo and not rf_algo:
        return {'nivel': 'isolado', 'nota': 'evidência cardíaca isolada (HR sem RF correspondente)'}
    if rf_algo and not hr_algo:
        return {'nivel': 'isolado', 'nota': 'evidência ventilatória isolada (RF sem HR correspondente)'}
    return {'nivel': 'ausente', 'nota': 'sem evidência cardiorrespiratória'}


def _avaliar_grupo_simples(estado, nome_metrica):
    if estado['estado'] == 'consistente':
        return {'nivel': 'forte', 'nota': f'{nome_metrica} consistente ao longo dos WORKs'}
    if estado['estado'] in ('parcial', 'tardio'):
        return {'nivel': 'parcial', 'nota': f'{nome_metrica} presente, mas parcial/tardio'}
    return {'nivel': 'ausente', 'nota': f'sem evidência de {nome_metrica}'}


def profilage_limiter(bp_nome, divergencia_bloco, convergencia_works, convergencia_sintese,
                      accumulation_bloco, comp_recovery, comp_rpe):
    """Camada de integração -- so' LÊ resultados já calculados:
    divergencia_bloco: divergencia['bp1'] ou ['bp2'] (profilage_primeira_divergencia)
    convergencia_works: [w for w in convergencia['works'] if w['bp']==bp_nome]
    convergencia_sintese: convergencia['sintese_bp1'] ou ['sintese_bp2']
    accumulation_bloco: accumulation['bp1'] ou ['bp2'] (profilage_accumulation)
    comp_recovery: comparacao_recovery_bp1/bp2 (comparar_recovery, já existente)
    comp_rpe: comparacao_rpe_bp1/bp2 (comparar_rpe, já existente) ou None
    """
    works_div = (divergencia_bloco or {}).get('works') or []
    if not works_div:
        return {'bp': bp_nome, 'padrao': 'EVIDÊNCIA INSUFICIENTE',
               'motivo': 'sem WORKs analisados neste bloco', 'evidencia': {},
               'recovery_coerencia': None, 'rpe_nota': 'RPE: não disponível'}

    canais_tabela = ('HR', 'RF', 'SmO2', 'THb', 'DFA-α1')
    estados = {c: _estado_metrica_no_bloco(c, works_div, convergencia_works) for c in canais_tabela}

    cardio = _avaliar_grupo_cardiorrespiratorio(estados['HR'], estados['RF'])
    perif = _avaliar_grupo_simples(estados['SmO2'], 'SmO2')
    auton = _avaliar_grupo_simples(estados['DFA-α1'], 'DFA-α1')
    # THb nunca decide sozinho -- so' contextual, mesmo que o estado interno seja forte
    thb_contexto = ('presente como contexto' if estados['THb']['estado'] in
                    ('consistente', 'parcial', 'tardio') else 'sem divergência sustentada')
    conv_predominante = (convergencia_sintese or {}).get('padrao_predominante')

    # recovery como evidencia complementar -- so' aumenta/reduz a
    # COERENCIA descritiva, nunca decide a classificacao sozinho
    recovery_estado = (comp_recovery or {}).get('status') if comp_recovery else None
    recovery_coerencia = {
        'CONVERGENTE': 'compatível', 'PARCIAL': 'parcial',
        'DIVERGENTE': 'divergente',
    }.get(recovery_estado, 'insuficiente')

    rpe_estado = (comp_rpe or {}).get('status') if comp_rpe else None
    rpe_nota = ('RPE: não disponível' if not comp_rpe or rpe_estado == 'DADOS INSUFICIENTES'
               else f'RPE: {rpe_estado.lower()}')

    # Só cardiorrespiratório e periférico contam como EIXOS PRIMÁRIOS
    # para decidir MISTO/MULTISSISTÊMICA/DISSOCIADAS. DFA-α1 é sempre
    # complementar (item 3/12) -- mesmo quando forte e temporalmente
    # agrupado com o eixo principal, entra só como nota, nunca eleva a
    # classificação sozinho nem conta para "quantos grupos responderam".
    def _grupo_so_tardio(nivel_info, estados_grupo):
        return nivel_info['nivel'] == 'parcial' and \
            all(e['estado'] in ('tardio', 'parcial') for e in estados_grupo) and \
            not any(e['estado'] == 'consistente' for e in estados_grupo)

    avaliacoes = {'cardiorrespiratorio': (cardio, [estados['HR'], estados['RF']]),
                 'periferico': (perif, [estados['SmO2']]),
                 'autonomico': (auton, [estados['DFA-α1']])}
    primarios = ('cardiorrespiratorio', 'periferico')
    primarios_fortes = [g for g in primarios if avaliacoes[g][0]['nivel'] == 'forte']
    primarios_com_evidencia = [g for g in primarios if avaliacoes[g][0]['nivel'] in ('forte', 'parcial')]
    # quantos dos TRÊS grupos (incluindo autonómico) ficam isolados no
    # tempo -- um único grupo tardio ao lado de um eixo forte não basta
    # para dissociação; dois ou mais grupos dispersos, sim
    n_tardio_isolado = sum(1 for g in ('cardiorrespiratorio', 'periferico', 'autonomico')
                           if _grupo_so_tardio(*avaliacoes[g]))
    nomes_grupos = {'cardiorrespiratorio': 'cardiorrespiratório',
                    'periferico': 'periférico', 'autonomico': 'autonômico'}

    def _nota_complementar(grupo_principal=None):
        partes = []
        if auton['nivel'] != 'ausente':
            partes.append('participação autonômica ' +
                          ('complementar' if auton['nivel'] == 'forte' else 'parcial'))
        if (perif['nivel'] != 'ausente' and 'periferico' not in primarios_fortes
                and grupo_principal != 'periferico'):
            partes.append('resposta periférica ' +
                          ('tardia' if avaliacoes['periferico'][0]['nivel'] == 'parcial'
                           and _grupo_so_tardio(*avaliacoes['periferico']) else 'parcial'))
        return (', com ' + ' e '.join(partes) + '.') if partes else '.'

    # ---- decisao (arvore descritiva, sem score) ----
    if not primarios_com_evidencia and auton['nivel'] == 'ausente':
        padrao = 'EVIDÊNCIA INSUFICIENTE'
        motivo = 'nenhum grupo fisiológico apresenta evidência consistente ou parcial.'
    elif not primarios_com_evidencia:
        # so' DFA-α1 tem evidencia -- sozinho nunca decide (item 12/teste G)
        padrao = 'EVIDÊNCIA INSUFICIENTE'
        motivo = ('apenas DFA-α1 (autonômico, complementar) apresenta evidência — '
                  'isoladamente não é suficiente para um padrão predominante.')
    elif len(primarios_fortes) == 2:
        padrao = 'RESPOSTA MULTISSISTÊMICA'
        motivo = ('componentes cardiorrespiratório e periférico apresentam respostas '
                  'temporalmente relacionadas' + _nota_complementar())
    elif len(primarios_fortes) == 1:
        grupo = primarios_fortes[0]
        if n_tardio_isolado >= 2:
            padrao = 'RESPOSTAS DISSOCIADAS'
            motivo = ('existe um eixo com resposta consistente ('
                      + nomes_grupos[grupo] + '), mas os demais grupos aparecem '
                      'dispersos no tempo, sem formar um segundo agrupamento claro.')
        else:
            padrao = ('PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE' if grupo == 'cardiorrespiratorio'
                      else 'PADRÃO PERIFÉRICO PREDOMINANTE')
            base_nota = cardio['nota'] if grupo == 'cardiorrespiratorio' else perif['nota']
            motivo = base_nota + _nota_complementar(grupo)
    else:
        # nenhum eixo primario "forte", mas ha' evidencia parcial em
        # 1 ou 2 -- olha para a convergencia do bloco ja' calculada
        if n_tardio_isolado >= 2 or conv_predominante == 'RESPOSTAS DISPERSAS':
            padrao = 'RESPOSTAS DISSOCIADAS'
            motivo = ('os grupos com evidência aparecem dispersos no tempo, sem um '
                      'agrupamento temporal predominante.')
        elif len(primarios_com_evidencia) == 1:
            grupo = primarios_com_evidencia[0]
            padrao = ('PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE' if grupo == 'cardiorrespiratorio'
                      else 'PADRÃO PERIFÉRICO PREDOMINANTE')
            base_nota = cardio['nota'] if grupo == 'cardiorrespiratorio' else perif['nota']
            motivo = base_nota + _nota_complementar(grupo) + ' (evidência parcial, não consistente).'
        else:
            padrao = 'PADRÃO MISTO'
            motivo = ('componentes cardiorrespiratório e periférico apresentam evidência, '
                      'mas nenhum de forma consistente o suficiente para predominar'
                      + _nota_complementar())

    return {
        'bp': bp_nome, 'padrao': padrao, 'motivo': motivo,
        'evidencia': {
            'cardiorrespiratorio': {'hr': estados['HR'], 'rf': estados['RF'], **cardio},
            'periferico': {'smo2': estados['SmO2'], 'thb_contexto': thb_contexto, **perif},
            'autonomico': {'dfa1': estados['DFA-α1'], **auton},
        },
        'convergencia_predominante': conv_predominante,
        'recovery_coerencia': recovery_coerencia,
        'rpe_nota': rpe_nota,
    }


def profilage_limiter_sintese(resultado_bp1, resultado_bp2):
    """Síntese descritiva da sessão -- so' concatena os dois resultados
    já produzidos por profilage_limiter(), nunca compara BP1 e BP2 numa
    escala nem atribui causa à transição."""
    def _frase(r):
        if not r:
            return None
        return f"{r['bp']} apresenta {r['padrao'].lower()}"
    return {
        'bp1': _frase(resultado_bp1), 'bp2': _frase(resultado_bp2),
        'nota': ('Esta análise identifica padrões de resposta fisiológica associados '
                 'ao WORK. Ela não demonstra causalmente qual sistema limita o '
                 'desempenho. HR, RF, SmO2, THb e DFA-α1 são marcadores '
                 'complementares; a convergência entre eles aumenta a coerência '
                 'do padrão, mas não estabelece causalidade.'),
    }


# ============================================================
# HIPÓTESE DE INTERVENÇÃO / TREINO -- camada de integração pura.
# Consome LIMITER, PRIMEIRA DIVERGÊNCIA, DRIFT, ACCUMULATION, RECOVERY
# e RPE já calculados; não recalcula nenhum sinal fisiológico, não
# prescreve protocolo fechado, não afirma causalidade.
# ============================================================

CONTEXTO_MODALIDADE = {
    'Bike': ('carga externa predominantemente ciclística; a musculatura '
            'monitorizada pode representar apenas parte da demanda periférica '
            'total do gesto.'),
    'Row': ('padrão mecânico diferente da Bike, com maior participação de '
           'massa muscular e ciclo respiratório/mecânico próprio; a '
           'recuperação pode envolver parada completa. Não transportar '
           'automaticamente a interpretação de outra modalidade.'),
    'Ski': ('padrão de recrutamento e demanda periférica próprios; a SmO2 '
           'deve ser interpretada no contexto da musculatura efectivamente '
           'monitorizada, sem assumir equivalência com Bike ou Row.'),
    'Run': ('impacto e mecânica próprios da corrida; a resposta '
           'cardiorrespiratória e periférica deve ser interpretada dentro '
           'do padrão específico deste gesto, não transposta de outra '
           'modalidade.'),
}


def _metricas_alteradas(evidencia):
    """So' as metricas que REALMENTE mostraram alteracao nesta sessao
    (estado != ausente/insuficiente) entram como alvo -- nunca a lista
    inteira por omissao."""
    hr = (evidencia.get('cardiorrespiratorio') or {}).get('hr') or {}
    rf = (evidencia.get('cardiorrespiratorio') or {}).get('rf') or {}
    smo2 = (evidencia.get('periferico') or {}).get('smo2') or {}
    dfa1 = (evidencia.get('autonomico') or {}).get('dfa1') or {}
    fora = []
    if hr.get('estado') in ('consistente', 'parcial', 'tardio'):
        fora.append('HR')
    if rf.get('estado') in ('consistente', 'parcial', 'tardio'):
        fora.append('RF')
    if smo2.get('estado') in ('consistente', 'parcial', 'tardio'):
        fora.append('SmO2')
    if dfa1.get('estado') in ('consistente', 'parcial', 'tardio'):
        fora.append('DFA-α1 (complementar)')
    return fora


def _timings_do_bloco(divergencia_bloco, metricas_interesse):
    """Timing real da primeira divergência (já calculado), so' para as
    métricas de interesse desta hipótese -- nunca recalculado."""
    works = (divergencia_bloco or {}).get('works') or []
    fora = {}
    for chave in metricas_interesse:
        tempos = []
        for w in works:
            m = next((x for x in w['metricas_ordenadas'] if x['metrica'] == chave), None)
            if m and m['status'] == 'ok':
                tempos.append(m['t_relativo'])
        if tempos:
            fora[chave] = {'tempos': tempos, 'media_s': round(sum(tempos) / len(tempos), 1)}
    return fora


def profilage_hipotese_intervencao(bp_nome, modalidade, limiter_result,
                                   divergencia_bloco, comp_recovery, comp_rpe,
                                   historico_padroes=None):
    """Camada de integração -- so' LÊ resultados já calculados.
    limiter_result: saída de profilage_limiter() para este BP.
    divergencia_bloco: divergencia['bp1'] ou ['bp2'] (para timing/potência).
    comp_recovery / comp_rpe: os mesmos já usados no LIMITER.
    historico_padroes: lista opcional de padrões (strings) de sessões
    anteriores comparáveis (mesma modalidade+BP); None/[] = sem histórico.
    """
    padrao = (limiter_result or {}).get('padrao') or 'EVIDÊNCIA INSUFICIENTE'
    evidencia = (limiter_result or {}).get('evidencia') or {}
    works = (divergencia_bloco or {}).get('works') or []
    potencias = [w.get('potencia_media') for w in works if w.get('potencia_media') is not None]
    nota_modalidade = CONTEXTO_MODALIDADE.get(modalidade,
        'modalidade não reconhecida — sem contexto específico disponível; '
        'interpretação genérica, com cautela adicional.')

    # estados internos de cada canal (já calculados pelo LIMITER) --
    # usados pelos ramos para escolher a métrica primária correcta
    _ev = evidencia
    estados_ev = {
        'HR':    ((_ev.get('cardiorrespiratorio') or {}).get('hr') or {}).get('estado','ausente'),
        'RF':    ((_ev.get('cardiorrespiratorio') or {}).get('rf') or {}).get('estado','ausente'),
        'SmO2':  ((_ev.get('periferico') or {}).get('smo2') or {}).get('estado','ausente'),
        'DFA-α1':((_ev.get('autonomico') or {}).get('dfa1') or {}).get('estado','ausente'),
    }

    base = {
        'bp': bp_nome, 'modalidade': modalidade, 'padrao_observado': padrao,
        'evidencias': limiter_result.get('motivo') if limiter_result else None,
        'potencia_w': potencias, 'nota_modalidade': nota_modalidade,
        'recovery_coerencia': (limiter_result or {}).get('recovery_coerencia'),
        'rpe_nota': (limiter_result or {}).get('rpe_nota'),
        'status': 'HIPÓTESE — REQUER NOVA VERIFICAÇÃO',
        'aviso': ('Esta é uma hipótese para teste. O padrão observado não demonstra '
                 'causalidade nem identifica isoladamente um limitante de desempenho.'),
    }

    if padrao == 'PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE':
        metricas_alvo = [m for m in _metricas_alteradas(evidencia) if m in ('HR', 'RF')]
        timings = _timings_do_bloco(divergencia_bloco, ['HR', 'RF'])
        metrica_prim = 'RF' if estados_ev.get('RF','ausente') in ('consistente','parcial') else 'HR'
        secundarias = ([m for m in ['HR','RF'] if m != metrica_prim]
                       + [] + (['RPE'] if comp_rpe else []) + ['Recovery', 'DFA-α1 (complementar)'])
        secundarias = [s for s in secundarias if s]
        base.update({
            'hipotese': ('A principal resposta progressiva observada neste WORK foi '
                        'cardiorrespiratória, com resposta periférica mais tardia ou ausente.'),
            'alvo_potencial': 'estabilidade cardiorrespiratória durante esforço sustentado nesta intensidade.',
            'metricas_alvo': metricas_alvo, 'timings_referencia': timings,
            'metrica_primaria': metrica_prim,
            'metricas_secundarias': secundarias,
            'estimulo_candidato': {
                'tipo': 'estímulo cujo objectivo seja testar a tolerância cardiorrespiratória '
                       'sustentada próxima desta intensidade',
                'objetivo': 'reduzir a progressão de HR/RF dentro do WORK, ou adiar o momento '
                           'da primeira divergência sustentada',
                'porque': 'HR e/ou RF mostraram resposta progressiva consistente nesta sessão',
                'metrica_alvo': '/'.join(metricas_alvo) if metricas_alvo else 'HR/RF',
                'criterio_resposta': 'menor drift intra-WORK, divergência mais tardia, ou '
                                    'recuperação mais rápida em sessão de verificação futura',
                'proxima_verificacao': 'repetir sessão comparável (mesma modalidade, BP e '
                                      'potência semelhante) e comparar DRIFT/timing/recovery',
            },
            'resposta_esperada': ['menor progressão de RF', 'menor drift de HR/RF',
                                  'divergência mais tardia', 'ou recuperação mais rápida'],
            'estrutura_futura': {
                'atividade_baseline': None, 'bp': bp_nome, 'modalidade': modalidade,
                'padrao': padrao, 'metrica_primaria': metrica_prim,
                'metricas_secundarias': secundarias,
                'estimulo': 'tolerância cardiorrespiratória sustentada',
                'criterio': 'menor drift HR/RF, divergência mais tardia ou recovery igual/melhor',
                'resultado_nova_vst': None,
            },
        })
    elif padrao == 'PADRÃO PERIFÉRICO PREDOMINANTE':
        timings = _timings_do_bloco(divergencia_bloco, ['SmO2'])
        base.update({
            'hipotese': 'Resposta periférica predominante durante o WORK, sem resposta '
                       'cardiorrespiratória equivalente.',
            'alvo_potencial': 'comportamento da SmO2 (oxigenação muscular monitorizada) '
                              'durante esforço sustentado — sem afirmar mecanismo específico.',
            'metricas_alvo': ['SmO2'], 'timings_referencia': timings,
            'metrica_primaria': 'SmO2',
            'metricas_secundarias': ['HR', 'RF'] + (['RPE'] if comp_rpe else []) + ['Recovery'],
            'estimulo_candidato': {
                'tipo': 'estímulo cujo objectivo seja testar a tolerância periférica ao '
                       'esforço sustentado nesta intensidade',
                'objetivo': 'reduzir a magnitude ou adiar o momento da divergência de SmO2',
                'porque': 'SmO2 mostrou resposta consistente e temporalmente relevante nesta sessão',
                'metrica_alvo': 'SmO2',
                'criterio_resposta': 'menor drift de SmO2, divergência mais tardia, ou '
                                    'recuperação de SmO2 mais rápida em sessão futura',
                'proxima_verificacao': 'repetir sessão comparável e comparar timing/magnitude '
                                      'de SmO2 e a recuperação periférica',
            },
            'resposta_esperada': ['menor drift de SmO2', 'divergência mais tardia',
                                  'ou recuperação periférica mais rápida'],
            'estrutura_futura': {
                'atividade_baseline': None, 'bp': bp_nome, 'modalidade': modalidade,
                'padrao': padrao, 'metrica_primaria': 'SmO2',
                'metricas_secundarias': ['HR', 'RF', 'Recovery'],
                'estimulo': 'tolerância periférica sustentada',
                'criterio': 'menor drift SmO2, divergência mais tardia ou recovery igual/melhor',
                'resultado_nova_vst': None,
            },
        })
    elif padrao == 'RESPOSTA MULTISSISTÊMICA':
        timings = _timings_do_bloco(divergencia_bloco, ['HR', 'RF', 'SmO2'])
        met_alt = _metricas_alteradas(evidencia)
        base.update({
            'hipotese': 'Resposta multissistêmica durante o WORK — cardiorrespiratório e '
                       'periférico apresentam respostas temporalmente relacionadas.',
            'alvo_potencial': 'capacidade de sustentar a carga sem progressão excessiva '
                              'simultânea de múltiplos sinais.',
            'metricas_alvo': met_alt, 'timings_referencia': timings,
            'metrica_primaria': 'HR/RF/SmO2',
            'metricas_secundarias': [] + (['RPE'] if comp_rpe else []) + ['Recovery', 'DFA-α1 (complementar)'],
            'estimulo_candidato': {
                'tipo': 'estímulo cujo objectivo seja testar a tolerância combinada a esta '
                       'intensidade, acompanhando vários sinais ao mesmo tempo',
                'objetivo': 'reduzir a progressão conjunta de HR/RF/SmO2 dentro do WORK',
                'porque': 'múltiplos sistemas mostraram respostas temporalmente relacionadas nesta sessão',
                'metrica_alvo': 'HR, RF e SmO2 em conjunto',
                'criterio_resposta': 'estabilidade maior entre ENTRY e EXIT nos vários sinais, '
                                    'ou recovery mais coerente, em sessão futura',
                'proxima_verificacao': 'repetir sessão comparável e comparar DRIFT/timing/'
                                      'recovery dos três sinais em conjunto',
            },
            'resposta_esperada': ['maior estabilidade entre ENTRY e EXIT', 'divergências mais '
                                  'tardias em conjunto', 'ou recovery mais coerente'],
            'estrutura_futura': {
                'atividade_baseline': None, 'bp': bp_nome, 'modalidade': modalidade,
                'padrao': padrao, 'metrica_primaria': 'HR/RF/SmO2',
                'metricas_secundarias': ['Recovery', 'RPE'],
                'estimulo': 'tolerância combinada sustentada (múltiplos sistemas)',
                'criterio': 'menor progressão conjunta HR/RF/SmO2 ou recovery igual/melhor',
                'resultado_nova_vst': None,
            },
        })
    elif padrao == 'RESPOSTAS DISSOCIADAS':
        primeira = None
        if works:
            todos = []
            for w in works:
                for mtc in w['metricas_ordenadas']:
                    if mtc['status'] == 'ok':
                        todos.append(mtc)
            if todos:
                primeira = min(todos, key=lambda x: x['t_relativo'])['metrica']
        # recorrencia decide se ha' consistencia suficiente para uma hipotese
        n_hist = len(historico_padroes or [])
        n_igual = sum(1 for p in (historico_padroes or []) if p == padrao)
        if n_hist == 0 or n_igual < n_hist:
            base.update({
                'hipotese': 'Respostas fisiológicas dissociadas durante o WORK — os sinais '
                           'aparecem temporalmente separados, sem um agrupamento predominante.',
                'alvo_potencial': None,
                'metricas_alvo': [], 'timings_referencia': {},
                'metrica_primaria': primeira,
                'metricas_secundarias': [],
                'estimulo_candidato': None,
                'resposta_esperada': None,
                'nota_dissociacao': ('primeira métrica a divergir nesta sessão: ' + primeira
                                     if primeira else 'sem divergência clara'),
                'motivo_sem_estimulo': ('Evidência insuficiente para selecionar um alvo de '
                                        'intervenção — a dissociação não se mostrou consistente '
                                        'entre sessões comparáveis (ou não há histórico '
                                        'suficiente para avaliar isso ainda).'),
                'estrutura_futura': {
                    'atividade_baseline': None, 'bp': bp_nome, 'modalidade': modalidade,
                    'padrao': padrao, 'metrica_primaria': primeira,
                    'metricas_secundarias': [],
                    'estimulo': None,
                    'criterio': 'verificar se a dissociação se repete e se emerge um agrupamento',
                    'resultado_nova_vst': None,
                },
            })
        else:
            base.update({
                'hipotese': 'Respostas fisiológicas dissociadas, de forma recorrente entre '
                           'sessões comparáveis.',
                'alvo_potencial': ('padrão de dissociação recorrente, com '
                                  + (primeira or 'a primeira métrica') + ' tipicamente '
                                  'divergindo primeiro.'),
                'metricas_alvo': [primeira] if primeira else [],
                'timings_referencia': _timings_do_bloco(divergencia_bloco, [primeira] if primeira else []),
                'metrica_primaria': primeira,
                'metricas_secundarias': [],
                'estimulo_candidato': {
                    'tipo': 'estímulo cujo objectivo seja investigar por que a dissociação se '
                           'repete — não um treino fechado, apenas uma verificação dirigida',
                    'objetivo': f'observar se {primeira or "a métrica inicial"} continua a '
                               'divergir sistematicamente antes das demais',
                    'porque': 'o padrão de dissociação repetiu-se em sessões comparáveis',
                    'metrica_alvo': primeira or '—',
                    'criterio_resposta': 'verificar se o agrupamento temporal muda com a '
                                        'repetição do estímulo',
                    'proxima_verificacao': 'repetir sessão comparável e reavaliar a '
                                          'convergência temporal',
                },
                'resposta_esperada': ['maior agrupamento temporal entre os sinais em sessão futura'],
                'estrutura_futura': {
                    'atividade_baseline': None, 'bp': bp_nome, 'modalidade': modalidade,
                    'padrao': padrao, 'metrica_primaria': primeira,
                    'metricas_secundarias': [],
                    'estimulo': 'investigação dirigida (dissociação recorrente)',
                    'criterio': 'verificar se emerge agrupamento temporal em sessão futura',
                    'resultado_nova_vst': None,
                },
            })
    else:  # EVIDÊNCIA INSUFICIENTE
        base.update({
            'hipotese': None, 'alvo_potencial': None, 'metricas_alvo': [],
            'timings_referencia': {}, 'estimulo_candidato': None, 'resposta_esperada': None,
            'metrica_primaria': None, 'metricas_secundarias': [],
            'motivo_sem_estimulo': ('Os dados disponíveis não permitem selecionar uma hipótese '
                                    'de intervenção fisiológica com segurança.'),
            'estrutura_futura': {
                'atividade_baseline': None, 'bp': bp_nome, 'modalidade': modalidade,
                'padrao': padrao, 'metrica_primaria': None, 'metricas_secundarias': [],
                'estimulo': None, 'criterio': 'repetir sessão com melhor qualidade de dados',
                'resultado_nova_vst': None,
            },
        })

    # recorrencia -- so' informativa, nunca um score
    n_hist = len(historico_padroes or [])
    n_igual = sum(1 for p in (historico_padroes or []) if p == padrao)
    if n_hist == 0:
        recorrencia = 'OBSERVADO UMA VEZ'
    elif n_igual >= 3:
        recorrencia = 'PADRÃO RECORRENTE'
    elif n_igual >= 2:
        recorrencia = 'OBSERVADO EM MÚLTIPLAS SESSÕES'
    else:
        recorrencia = 'OBSERVADO UMA VEZ'
    base['recorrencia'] = {'estado': recorrencia, 'n_sessoes_comparadas': n_hist,
                           'n_com_mesmo_padrao': n_igual}
    return base


# ============================================================
# BIBLIOTECA DE ESTILOS DE TREINO
# Cada entrada: nome, objetivo, estrutura conceptual, o que observar,
# resposta esperada por padrao (chave = padrao do LIMITER).
# NÃO contem watts, duracao, series, recuperacao -- esses parametros
# sao uma camada posterior. Apenas o ESTILO/FAMILIA e a logica de uso.
# ============================================================
ESTILOS_DE_TREINO = {
    'continuo_sustentado': {
        'nome': 'Contínuo sustentado',
        'objetivo': ('Avaliar estabilidade fisiológica durante esforço contínuo na '
                     'mesma faixa de intensidade do protocolo VST.'),
        'estrutura': 'entrada progressiva → bloco sustentado → recuperação passiva',
        'o_que_observar': ['HR', 'RF', 'SmO2', 'DFA-α1', 'RPE', 'Recovery'],
        'resposta_esperada': ('menor drift de HR/RF no final do bloco; '
                              'menor progressão de SmO2 se comparado à VST de referência.'),
        'padroes_indicados': ['PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE',
                              'PADRÃO PERIFÉRICO PREDOMINANTE'],
    },
    'intervalado_recuperacao_completa': {
        'nome': 'Intervalado com recuperação completa',
        'objetivo': ('Separar a capacidade de sustentar a intensidade da capacidade '
                     'de recuperar completamente entre esforços.'),
        'estrutura': 'WORK → recuperação suficiente → WORK → recuperação → WORK',
        'o_que_observar': ['entrada de cada WORK', 'drift intra-WORK',
                           'primeira divergência', 'recovery', 'RPE'],
        'resposta_esperada': ('ENTRY semelhante entre WORKs; menor drift ao longo de '
                              'cada WORK; divergência mais tardia.'),
        'padroes_indicados': ['PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE',
                              'RESPOSTA MULTISSISTÊMICA'],
    },
    'intervalado_recuperacao_incompleta': {
        'nome': 'Intervalado com recuperação incompleta',
        'objetivo': ('Testar tolerância à acumulação fisiológica — observar se o '
                     'ENTRY de cada WORK progressivamente se altera.'),
        'estrutura': 'WORK → recuperação curta → WORK → recuperação curta → ...',
        'o_que_observar': ['ENTRY de cada WORK', 'ACCUMULATION HR/RF/SmO2',
                           'Recovery', 'RPE'],
        'resposta_esperada': ('menor elevação do ENTRY entre WORKs consecutivos; '
                              'recovery mais coerente; RPE não aumentando desproporcionalmente.'),
        'padroes_indicados': ['PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE',
                              'RESPOSTA MULTISSISTÊMICA'],
    },
    'over_under': {
        'nome': 'Over/Under',
        'objetivo': ('Testar capacidade de alternar entre intensidades próximas, '
                     'observando recuperação parcial e estabilidade fisiológica.'),
        'estrutura': 'over (intensidade superior) → under (intensidade inferior) → repetição',
        'o_que_observar': ['HR', 'RF', 'SmO2', 'RPE', 'recuperação entre fases',
                           'acumulação entre repetições'],
        'resposta_esperada': ('menor progressão de HR/RF durante os blocos over; '
                              'recuperação parcial mais completa durante os blocos under.'),
        'padroes_indicados': ['PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE',
                              'RESPOSTA MULTISSISTÊMICA'],
    },
    'bloco_progressivo': {
        'nome': 'Bloco progressivo',
        'objetivo': ('Observar o ponto em que ocorre a primeira divergência fisiológica '
                     'e verificar se ele pode ser deslocado.'),
        'estrutura': 'intensidade crescente em blocos, monitorando a primeira divergência '
                     'de cada sinal',
        'o_que_observar': ['primeira divergência', 'convergência temporal',
                           'DRIFT', 'RPE'],
        'resposta_esperada': ('divergência ocorrendo a uma intensidade maior ou mais tarde '
                              'dentro de cada bloco, em comparação com a VST de referência.'),
        'padroes_indicados': ['PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE',
                              'PADRÃO PERIFÉRICO PREDOMINANTE',
                              'RESPOSTA MULTISSISTÊMICA'],
    },
    'foco_periferico': {
        'nome': 'Intervalado com foco periférico',
        'objetivo': ('Testar estabilidade de SmO2 sob esforço repetido — '
                     'sugerido somente quando houver evidência recorrente de padrão periférico.'),
        'estrutura': 'WORKs com recuperação suficiente para SmO2 recuperar parcialmente, '
                     'monitorizando a progressão entre WORKs',
        'o_que_observar': ['SmO2', 'THb (contextual)', 'HR/RF como secundários',
                           'Recovery periférico', 'RPE'],
        'resposta_esperada': ('SmO2 mais estável dentro de cada WORK; '
                              'menor deterioração entre WORKs consecutivos; '
                              'recovery periférico mais consistente.'),
        'padroes_indicados': ['PADRÃO PERIFÉRICO PREDOMINANTE'],
    },
    'estimulo_multissistemico': {
        'nome': 'Estímulo multissistêmico',
        'objetivo': ('Testar tolerância integrada quando múltiplos sistemas respondem '
                     'temporalmente juntos — sugerido somente com padrão multissistêmico '
                     'recorrente.'),
        'estrutura': 'WORKs na intensidade do protocolo VST, monitorizando HR, RF e SmO2 '
                     'em conjunto (não separadamente)',
        'o_que_observar': ['HR', 'RF', 'SmO2', 'DFA-α1 (complementar)',
                           'convergência temporal', 'Recovery', 'RPE'],
        'resposta_esperada': ('maior estabilidade conjunta dos sinais; divergências mais tardias; '
                              'menor acumulação entre WORKs; recovery igual ou melhor.'),
        'padroes_indicados': ['RESPOSTA MULTISSISTÊMICA'],
    },
    # ---- novos estilos (item 6 do pedido) ----
    'progressivo': {
        'nome': 'Progressivo',
        'tipo': 'Progressivo',
        'intensidade': 'entrada controlada → aumento gradual → sustentação próxima de BP2',
        'recuperacao': 'passiva no final (se intervalado) ou contínuo',
        'objetivo': ('Verificar em que ponto os sinais começam a divergir e '
                     'se esse ponto pode ser deslocado com o treino.'),
        'estrutura': 'entrada em intensidade abaixo de BP1 → progressão gradual '
                     '→ chegar e manter próximo de BP2 → observar DRIFT e primeira divergência',
        'ancoras': ['BP1', 'BP2', 'entre BP1 e BP2'],
        'o_que_observar': ['primeira divergência', 'convergência temporal', 'DRIFT', 'RPE'],
        'resposta_esperada': ('divergência ocorrendo a uma intensidade maior, '
                              'ou mais tarde em cada bloco, comparado à VST de referência.'),
        'padroes_indicados': ['PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE',
                              'PADRÃO PERIFÉRICO PREDOMINANTE',
                              'RESPOSTA MULTISSISTÊMICA'],
    },
    'inicio_forte': {
        'nome': 'Início forte → sustentação',
        'tipo': 'Intervalado',
        'intensidade': 'entrada acima de BP2 → sustentação entre BP1 e BP2',
        'recuperacao': 'completa entre repetições',
        'objetivo': ('Testar tolerância após entrada forte e capacidade de '
                     'estabilização fisiológica subsequente.'),
        'estrutura': 'início acima de BP2 por período curto → redução para entre BP1 e BP2 '
                     '→ sustentar → recuperação completa → repetir',
        'ancoras': ['acima de BP2', 'entre BP1 e BP2'],
        'o_que_observar': ['HR', 'RF', 'SmO2', 'primeira divergência após a transição',
                           'Recovery', 'RPE'],
        'resposta_esperada': ('estabilização mais rápida dos sinais após a entrada forte; '
                              'DRIFT menor na fase de sustentação.'),
        'padroes_indicados': ['PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE',
                              'RESPOSTA MULTISSISTÊMICA'],
    },
    'acumulacao_progressiva': {
        'nome': 'Acumulação progressiva',
        'tipo': 'Volume incremental',
        'intensidade': 'intensidade similar à do protocolo VST (BP1/BP2)',
        'recuperacao': 'moderada entre sessões/blocos; não testado num único esforço',
        'objetivo': ('Testar se o atleta consegue acumular mais tempo total '
                     'antes da primeira divergência ou antes de exceder o DRIFT tolerável.'),
        'estrutura': 'manter intensidade próxima de BP1 → aumentar progressivamente '
                     'a duração total → observar quando divergência aparece',
        'ancoras': ['próximo de BP1', 'entre BP1 e BP2'],
        'o_que_observar': ['ACCUMULATION', 'DRIFT', 'primeira divergência',
                           'ENTRY de cada WORK', 'Recovery', 'RPE'],
        'resposta_esperada': ('tempo até primeira divergência aumentando; '
                              'menor progressão de HR/RF/SmO2 por unidade de tempo acumulado.'),
        'padroes_indicados': ['PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE',
                              'PADRÃO PERIFÉRICO PREDOMINANTE',
                              'RESPOSTA MULTISSISTÊMICA'],
    },
    'tiros_curtos': {
        'nome': 'Tiros curtos / intervalos curtos',
        'tipo': 'Intervalado curto',
        'intensidade': 'acima de BP2',
        'recuperacao': 'curta a moderada entre repetições',
        'objetivo': ('Testar tolerância a esforços de maior intensidade '
                     'e a qualidade da recuperação entre repetições.'),
        'estrutura': 'esforço curto acima de BP2 → recuperação curta → repetir; '
                     'NÃO assumir automaticamente adequação a qualquer LIMITER — '
                     'sugerido somente quando há padrão de acumulação claro',
        'ancoras': ['acima de BP2'],
        'o_que_observar': ['Recovery', 'ENTRY de cada repetição',
                           'HR', 'RF', 'SmO2', 'RPE'],
        'resposta_esperada': ('ENTRY das repetições seguintes mais estável; '
                              'recovery mais consistente entre repetições.'),
        'padroes_indicados': ['RESPOSTAS DISSOCIADAS'],  # candidato de investigacao
    },
}

# mapeamento LIMITER VST → chave em utils/intervencoes.py (aba Intervenções MOXY)
# usado para verificar concordância/discordância (item 4/13 do pedido)
_LIMITER_PARA_INTERVENCAO = {
    'PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE': ['entrega', 'ventilacao', 'cardiaco'],
    'PADRÃO PERIFÉRICO PREDOMINANTE': ['utilizacao'],
    'RESPOSTA MULTISSISTÊMICA': ['entrega', 'utilizacao', 'ventilacao'],
    'RESPOSTAS DISSOCIADAS': [],
    'EVIDÊNCIA INSUFICIENTE': [],
}

# Mapeamento padrao → estilos primários e secundários.
# Primários: mais directamente coerentes com a hipótese.
# Secundários: também compatíveis, mas não os primeiros a testar.
_ESTILOS_POR_PADRAO = {
    'PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE': {
        'primarios': ['over_under', 'intervalado_recuperacao_completa'],
        'secundarios': ['continuo_sustentado', 'bloco_progressivo', 'progressivo',
                        'intervalado_recuperacao_incompleta', 'acumulacao_progressiva'],
    },
    'PADRÃO PERIFÉRICO PREDOMINANTE': {
        'primarios': ['foco_periferico', 'continuo_sustentado'],
        'secundarios': ['bloco_progressivo', 'progressivo',
                        'acumulacao_progressiva', 'intervalado_recuperacao_completa'],
    },
    'RESPOSTA MULTISSISTÊMICA': {
        'primarios': ['estimulo_multissistemico', 'intervalado_recuperacao_completa'],
        'secundarios': ['over_under', 'continuo_sustentado', 'progressivo',
                        'inicio_forte', 'intervalado_recuperacao_incompleta'],
    },
    'RESPOSTAS DISSOCIADAS': {
        'primarios': [],  # nao prescrever automaticamente sem recorrencia
        'secundarios': [],
    },
    'EVIDÊNCIA INSUFICIENTE': {
        'primarios': [],
        'secundarios': [],
    },
}


def concordancia_moxy_vst(padrao_vst, limitador_moxy):
    """Verifica se o padrão encontrado pela VST é concordante com o
    limitador identificado pela aba Intervenções MOXY.
    limitador_moxy: string com a chave (ex: 'entrega', 'utilizacao',
    'ventilacao', 'cardiaco') ou None quando não disponível.
    Devolve um dict com status ('concordante'/'discordante'/'parcial'/
    'sem_dados') e uma nota descritiva.
    """
    if not limitador_moxy:
        return {'status': 'sem_dados',
                'nota': 'Sem evidência complementar disponível no MOXY/Intervenções.'}
    chaves_esperadas = _LIMITER_PARA_INTERVENCAO.get(padrao_vst, [])
    if not chaves_esperadas:
        return {'status': 'sem_dados',
                'nota': f'Padrão VST "{padrao_vst}" não tem correspondência '
                        'directa com a classificação de limitador do MOXY.'}
    lim = limitador_moxy.lower().strip()
    if lim in chaves_esperadas:
        return {'status': 'concordante',
                'nota': f'Evidência complementar encontrada no MOXY/Intervenções '
                        f'— achado "{limitador_moxy}" é compatível com o padrão VST.'}
    if any(c in lim for c in chaves_esperadas) or any(lim in c for c in chaves_esperadas):
        return {'status': 'parcial',
                'nota': f'Achado MOXY "{limitador_moxy}" é parcialmente compatível '
                        'com o padrão VST — manter hipótese aberta.'}
    return {'status': 'discordante',
            'nota': f'Achado VST e achado MOXY/Intervenções ("{limitador_moxy}") '
                    'não são concordantes — manter hipótese aberta, '
                    'considerar nova verificação.'}


def _estilos_para_padrao(padrao, recorrencia_estado, n_modalidades):
    """Selecciona os estilos coerentes com o padrao, ajustando pela
    recorrencia -- estilos secundarios so' sao incluidos quando ha'
    evidencia recorrente em pelo menos 1 modalidade. Para DISSOCIADAS
    sem recorrencia, nao devolve nenhum estilo (item 9 do pedido).
    """
    conf = _ESTILOS_POR_PADRAO.get(padrao) or {'primarios': [], 'secundarios': []}
    resultado = []
    for chave in conf['primarios']:
        e = ESTILOS_DE_TREINO[chave]
        resultado.append({'chave': chave, 'prioridade': 'primário', **e})
    if recorrencia_estado in ('RECORRENTE NA MESMA MODALIDADE',
                              'RECORRENTE EM MÚLTIPLAS MODALIDADES',
                              'OBSERVADO EM MÚLTIPLAS SESSÕES'):
        for chave in conf['secundarios']:
            e = ESTILOS_DE_TREINO[chave]
            resultado.append({'chave': chave, 'prioridade': 'secundário', **e})
    return resultado


def profilage_historico_estilos(entradas, limitador_moxy=None):
    """Consome a lista de verificacoes salvas e devolve:
    - padroes recorrentes globais e por modalidade;
    - biblioteca de estilos para o historico;
    - concordância com achado MOXY (quando disponível).
    limitador_moxy: string da chave do limitador da aba Intervenções,
    ex: 'entrega', 'utilizacao', 'ventilacao', None quando sem dados.
    """
    from collections import Counter

    n_total = len(entradas)
    if n_total == 0:
        return {
            'n_verificacoes': 0,
            'recorrencia_global': [], 'recorrencia_por_modalidade': {},
            'estilos_historico': [],
            'nota': 'sem verificações salvas disponíveis.',
        }

    # -------- padroes globais (BP1 e BP2 separados) --------
    def _acumular(bp_chave):
        contador = Counter()
        por_modal = {}
        datas_por_padrao = {}
        for e in entradas:
            p = e.get(bp_chave)
            if not p or p == 'EVIDÊNCIA INSUFICIENTE':
                continue
            mod = e.get('modalidade') or 'desconhecida'
            data = e.get('analisado_em') or ''
            contador[p] += 1
            por_modal.setdefault(mod, Counter())[p] += 1
            datas_por_padrao.setdefault(p, []).append(data)
        return contador, por_modal, datas_por_padrao

    cnt1, mod1, datas1 = _acumular('padrao_bp1')
    cnt2, mod2, datas2 = _acumular('padrao_bp2')

    def _recorrencia(padrao, contagem, n_modal, datas):
        n = contagem.get(padrao, 0)
        datasP = sorted(datas.get(padrao) or [])
        primeira = datasP[0] if datasP else None
        ultima = datasP[-1] if datasP else None
        if n == 0:
            return 'OBSERVADO UMA VEZ', 0, n_modal, primeira, ultima
        if n >= 3 and n_modal >= 2:
            estado = 'RECORRENTE EM MÚLTIPLAS MODALIDADES'
        elif n >= 3:
            estado = 'RECORRENTE NA MESMA MODALIDADE'
        elif n >= 2:
            estado = 'OBSERVADO EM MÚLTIPLAS SESSÕES'
        else:
            estado = 'OBSERVADO UMA VEZ'
        return estado, n, n_modal, primeira, ultima

    rec_global = []
    todos_padroes = set(list(cnt1.keys()) + list(cnt2.keys()))
    for p in todos_padroes:
        n1 = cnt1.get(p, 0)
        n2 = cnt2.get(p, 0)
        n_total_p = n1 + n2
        mods_p = set(list(mod1.keys() if n1 else []) + list(mod2.keys() if n2 else []))
        datas_p = (datas1.get(p) or []) + (datas2.get(p) or [])
        datas_p = sorted(datas_p)
        est, _, _, prim, ult = _recorrencia(p, Counter({p: n_total_p}), len(mods_p),
                                             {p: datas_p})
        rec_global.append({
            'padrao': p, 'n_total': n_total_p, 'n_bp1': n1, 'n_bp2': n2,
            'n_modalidades': len(mods_p), 'modalidades': sorted(mods_p),
            'recorrencia': est, 'primeira_ocorrencia': prim, 'ultima_ocorrencia': ult,
        })
    rec_global.sort(key=lambda x: -x['n_total'])

    # -------- padroes por modalidade --------
    rec_por_modal = {}
    todas_mods = set(list(mod1.keys()) + list(mod2.keys()))
    for mod in todas_mods:
        c1 = mod1.get(mod, Counter())
        c2 = mod2.get(mod, Counter())
        n_sess_modal = sum(1 for e in entradas if e.get('modalidade') == mod)
        padroes_modal = []
        for p in set(list(c1.keys()) + list(c2.keys())):
            padroes_modal.append({
                'padrao': p, 'n_bp1': c1.get(p, 0), 'n_bp2': c2.get(p, 0),
                'n_total': c1.get(p, 0) + c2.get(p, 0),
                'n_sessoes_modalidade': n_sess_modal,
            })
        padroes_modal.sort(key=lambda x: -x['n_total'])
        rec_por_modal[mod] = padroes_modal

    # -------- estilos coerentes com o historico --------
    estilos_hist = []
    vistos = set()
    for r in rec_global:
        p = r['padrao']
        estilos_p = _estilos_para_padrao(p, r['recorrencia'], r['n_modalidades'])
        for e in estilos_p:
            chave = e['chave']
            if chave not in vistos:
                vistos.add(chave)
                estilos_hist.append({
                    **e,
                    'motivado_por': p,
                    'recorrencia': r['recorrencia'],
                    'n_sessoes': r['n_total'],
                    'n_modalidades': r['n_modalidades'],
                })

    # concordância com a aba Intervenções MOXY (item 4/13 do pedido)
    concordancia_por_padrao = {}
    for r in rec_global:
        concordancia_por_padrao[r['padrao']] = concordancia_moxy_vst(
            r['padrao'], limitador_moxy)

    return {
        'n_verificacoes': n_total,
        'recorrencia_global': rec_global,
        'recorrencia_por_modalidade': rec_por_modal,
        'estilos_historico': estilos_hist,
        'concordancia_moxy': concordancia_por_padrao,
        'nota_metodologica': ('Frequência de padrão não demonstra causalidade. '
                              'Padrão recorrente aumenta a justificativa para testar '
                              'a hipótese — nunca comprova o mecanismo fisiológico.'),
    }


def estilos_para_hipotese(padrao, recorrencia_estado, n_modalidades):
    """Ponto de entrada público: dado um padrão do LIMITER e o estado
    de recorrência, devolve os estilos coerentes (sem histórico global).
    """
    return _estilos_para_padrao(padrao, recorrencia_estado, n_modalidades)


# ============================================================
# ESTILOS PARAMETRIZADOS — calcula faixas de watts a partir de
# BP1/BP2/CP reais da modalidade actual; nunca inventa números.
# ============================================================

# Mapeamento dos limitadores MOXY → grupos fisiológicos VST.
# Chaves vindas de MX_ULT_US / MX_ULT_PC (tab_moxy.py).
_MOXY_PARA_GRUPO = {
    'Utilização':    'periferico',
    'Fornecimento':  'cardiorrespiratorio',
    'Cardíaco':      'cardiorrespiratorio',
    'Pulmonar':      'cardiorrespiratorio',
    'Ventilatório':  'cardiorrespiratorio',
}
_PADRAO_PARA_GRUPO = {
    'PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE': 'cardiorrespiratorio',
    'PADRÃO PERIFÉRICO PREDOMINANTE':          'periferico',
    'RESPOSTA MULTISSISTÊMICA':               'multisistemico',
    'PADRÃO MISTO':                           'multisistemico',
    'RESPOSTAS DISSOCIADAS':                  'dissociado',
    'EVIDÊNCIA INSUFICIENTE':                 'insuficiente',
}

# Explicações fisiológicas por estilo — o "por quê" que liga cada
# formato ao mecanismo observado.  Chave = chave do ESTILOS_DE_TREINO.
_EXPLICACAO_FISIOLOGICA = {
    'over_under': {
        'cardiorrespiratorio': (
            'O atleta apresenta progressão de RF/HR e/ou dificuldade de '
            'recuperação entre esforços. O Over/Under cria repetidamente '
            'uma fase de alta demanda seguida de menor intensidade, '
            'trabalhando a capacidade de sustentar e recuperar a resposta '
            'cardiorrespiratória DURANTE o exercício, sem pausa completa.'),
        'multisistemico': (
            'Múltiplos sistemas respondem progressivamente durante o WORK. '
            'O Over/Under expõe o atleta a variações de demanda e permite '
            'observar se a estabilidade conjunta de HR/RF/SmO2 melhora '
            'com o estímulo repetido de alternância.'),
        'periferico': (
            'SmO2 mostra deterioração sustentada. O Over/Under permite '
            'acumular tempo em intensidade relevante intercalado com '
            'períodos de menor demanda, testando a capacidade periférica '
            'de recuperação parcial sem pausa completa.'),
    },
    'intervalado_recuperacao_completa': {
        'cardiorrespiratorio': (
            'Quando a recuperação é suficiente, é possível separar a '
            'capacidade de produzir o esforço da capacidade de recuperar '
            'entre esforços. Este formato revela se o ENTRY de cada '
            'WORK melhora progressivamente, sinalizando melhora na '
            'recuperação cardiorrespiratória.'),
        'multisistemico': (
            'Permite isolar cada WORK e observar se os múltiplos sistemas '
            'iniciam cada esforço num estado semelhante ao anterior, '
            'controlando a acumulação entre WORKs.'),
        'periferico': (
            'Recuperação completa entre WORKs permite que SmO2 retorne '
            'próximo ao valor inicial, separando a resposta dentro de '
            'cada WORK da acumulação entre WORKs.'),
    },
    'continuo_sustentado': {
        'cardiorrespiratorio': (
            'Esforço contínuo na faixa fisiológica relevante avalia a '
            'estabilidade de HR/RF durante exposição prolongada. Um menor '
            'drift ao longo do tempo é o indicador de resposta ao estímulo.'),
        'multisistemico': (
            'Permite observar a evolução conjunta de HR/RF/SmO2 durante '
            'esforço contínuo, sem a variabilidade introduzida pela '
            'recuperação entre intervalos.'),
        'periferico': (
            'Expõe SmO2 a uma demanda sustentada na faixa relevante, '
            'permitindo avaliar a estabilidade periférica durante tempo '
            'prolongado de exercício.'),
    },
    'intervalado_recuperacao_incompleta': {
        'cardiorrespiratorio': (
            'Quando a recuperação é curta, a acumulação fisiológica entre '
            'WORKs fica visível. Este formato testa a tolerância a essa '
            'acumulação — o ENTRY de cada WORK progressivamente mais alto '
            'é um indicador de acumulação fisiológica.'),
        'multisistemico': (
            'Permite observar como a acumulação de múltiplos sistemas '
            'evolui quando a recuperação é insuficiente para restaurar '
            'completamente o estado fisiológico.'),
        'periferico': (
            'Recuperação incompleta entre WORKs permite avaliar se SmO2 '
            'se deteriora progressivamente com o acúmulo de estímulos '
            'com recuperação curta.'),
    },
    'progressivo': {
        'cardiorrespiratorio': (
            'Permite observar em que ponto HR/RF divergem e se esse ponto '
            'pode ser deslocado com o treino. A progressão gradual evita '
            'a antecipação da divergência por esforço súbito.'),
        'multisistemico': (
            'Permite monitorar qual sistema responde primeiro ao aumento '
            'progressivo de carga, fornecendo informação sobre o padrão '
            'de divergência temporal.'),
        'periferico': (
            'Permite identificar o ponto em que SmO2 diverge durante '
            'a progressão de carga, sem que o esforço súbito '
            'antecipe a resposta periférica.'),
    },
    'inicio_forte': {
        'cardiorrespiratorio': (
            'Testa se o atleta consegue estabilizar HR/RF após uma entrada '
            'de alta demanda. Relevant quando o DRIFT é maior no início '
            'do WORK do que na parte sustentada.'),
        'multisistemico': (
            'Permite observar se múltiplos sistemas se estabilizam após '
            'a entrada forte, ou se a perturbação inicial mantém '
            'progressão até ao final do WORK.'),
        'periferico': (
            'Testa a capacidade de SmO2 se estabilizar após entrada '
            'de alta demanda periférica.'),
    },
    'acumulacao_progressiva': {
        'cardiorrespiratorio': (
            'Aumentar progressivamente a duração total na faixa relevante '
            'testa se HR/RF conseguem manter estabilidade por períodos '
            'mais longos do que os observados na VST de referência.'),
        'multisistemico': (
            'Permite acumular exposição mantendo múltiplos sistemas '
            'monitorados, verificando se a progressão conjunta dos '
            'sinais diminui com o aumento do volume acumulado.'),
        'periferico': (
            'Acumular tempo na faixa relevante expõe SmO2 a uma '
            'demanda crescente de duração, testando a tolerância '
            'periférica sustentada.'),
    },
    'foco_periferico': {
        'periferico': (
            'SmO2 apresenta deterioração consistente durante o WORK. '
            'Este formato permite acumular trabalho na faixa periférica '
            'com recuperação suficiente para SmO2 recuperar parcialmente '
            'entre WORKs, separando a resposta intra-WORK da acumulação '
            'entre WORKs.'),
    },
    'estimulo_multissistemico': {
        'multisistemico': (
            'Múltiplos sistemas — cardiorrespiratório e periférico — '
            'responderam de forma temporalmente relacionada. Este formato '
            'mantém todos os sistemas monitorados simultaneamente, testando '
            'a tolerância integrada sem privilegiar um único sistema.'),
    },
    'bloco_progressivo': {
        'cardiorrespiratorio': (
            'Aumentar gradualmente a demanda ao longo de blocos permite '
            'verificar em que ponto ocorre a primeira divergência de '
            'HR/RF e se esse ponto pode ser deslocado.'),
        'multisistemico': (
            'Permite observar a ordem em que os sistemas divergem '
            'durante a progressão de carga, fornecendo informação '
            'complementar ao padrão de convergência temporal.'),
        'periferico': (
            'Permite identificar a intensidade de início de deterioração '
            'de SmO2, informação complementar ao timing observado na VST.'),
    },
}


def _faixas_watts(bp1_w, bp2_w, cp_w, padrao):
    """Calcula faixas de watts para cada zona de intensidade a partir
    dos valores reais da modalidade actual. Nunca inventa números.
    Devolve um dict com as faixas textuais e numéricas disponíveis.
    Se não houver dados suficientes, indica explicitamente.
    """
    if bp1_w is None and bp2_w is None and cp_w is None:
        return {'disponivel': False,
                'nota': 'Faixa de potência não determinada — sem BP1/BP2/CP '
                        'disponíveis para esta modalidade e sessão.'}

    def _fmt(v): return f'{round(v)} W' if v is not None else '—'
    def _faixa(lo, hi):
        if lo is not None and hi is not None:
            return f'{round(lo)}–{round(hi)} W'
        if lo is not None:
            return f'≥ {round(lo)} W'
        if hi is not None:
            return f'≤ {round(hi)} W'
        return '—'

    ref_principal = bp2_w or cp_w or bp1_w

    zonas = {
        'abaixo_bp1': _faixa(None, bp1_w),
        'bp1':        _fmt(bp1_w),
        'entre_bp1_bp2': _faixa(bp1_w, bp2_w) if bp1_w and bp2_w else '—',
        'bp2':        _fmt(bp2_w),
        'proximo_bp2': (f'{round(ref_principal * 0.97)}–{round(ref_principal)} W'
                        if ref_principal else '—'),
        'acima_bp2':  (f'> {round(bp2_w)} W' if bp2_w else
                       f'> {round(cp_w)} W' if cp_w else '—'),
        'cp':         _fmt(cp_w),
    }
    # faixas específicas por estilo (derivadas, nunca fixas)
    if padrao == 'over_under':
        zonas['over'] = (f'{round(bp2_w * 1.02)}–{round(bp2_w * 1.07)} W'
                         if bp2_w else zonas['acima_bp2'])
        zonas['under'] = zonas['entre_bp1_bp2'] or zonas['bp1']
    elif padrao == 'inicio_forte':
        zonas['fase_forte'] = zonas['acima_bp2']
        zonas['fase_sustentacao'] = zonas['entre_bp1_bp2'] or zonas['bp2']

    return {'disponivel': True, 'zonas': zonas,
            'bp1_w': bp1_w, 'bp2_w': bp2_w, 'cp_w': cp_w}


# Estrutura WORK/RECOVERY por estilo
_WORK_RECOVERY = {
    'continuo_sustentado': {
        'work': 'contínuo (sem intervalos)', 'recovery': 'não aplicável',
        'acumulo': '20–60 min de exposição total na faixa-alvo',
    },
    'intervalado_recuperacao_completa': {
        'work': '4–12 min', 'recovery': 'recuperação completa (1:1 a 1:2)',
        'acumulo': '20–50 min totais de WORK acumulado',
    },
    'intervalado_recuperacao_incompleta': {
        'work': '3–8 min', 'recovery': 'recuperação curta (1:0.5 a 1:1)',
        'acumulo': '15–40 min totais de WORK acumulado',
    },
    'over_under': {
        'work': 'alternância over (1–4 min) → under (1–4 min), repetido',
        'recovery': 'a recuperação ocorre no próprio bloco under; pausa completa no final',
        'acumulo': '20–45 min de alternância total',
    },
    'bloco_progressivo': {
        'work': 'blocos de 3–8 min em intensidade crescente',
        'recovery': 'pausa curta entre blocos (1–3 min)',
        'acumulo': '20–45 min totais',
    },
    'progressivo': {
        'work': 'rampa contínua de intensidade', 'recovery': 'não aplicável',
        'acumulo': '15–30 min de progressão total',
    },
    'inicio_forte': {
        'work': 'fase forte: 30–90 s | fase sustentação: 4–10 min',
        'recovery': 'completa entre repetições',
        'acumulo': '3–5 repetições (decisão do utilizador/treinador)',
    },
    'acumulacao_progressiva': {
        'work': 'duração progressivamente maior a cada sessão',
        'recovery': 'entre sessões; não é um único esforço',
        'acumulo': 'aumentar progressivamente o tempo total na faixa-alvo',
    },
    'foco_periferico': {
        'work': '3–8 min', 'recovery': 'suficiente para SmO2 recuperar parcialmente (1:1 a 1:2)',
        'acumulo': '20–40 min totais de WORK acumulado',
    },
    'estimulo_multissistemico': {
        'work': '4–12 min', 'recovery': 'completa a moderada (1:1 a 1:2)',
        'acumulo': '20–50 min totais',
    },
    'tiros_curtos': {
        'work': '20–60 s', 'recovery': 'curta a moderada (1:3 a 1:5)',
        'acumulo': '10–20 min de WORK acumulado',
    },
}


def profilage_estilos_parametrizados(padrao_vst, limitador_moxy_us,
                                     limitador_moxy_pc, bp1_w, bp2_w,
                                     cp_w, recorrencia_estado,
                                     n_modalidades_historico, modalidade):
    """Camada de interpretação + parametrização de estilos de treino.
    Consome APENAS resultados já calculados; não faz nenhum cálculo
    fisiológico novo.

    Devolve os estilos mais relevantes com:
    - explicação fisiológica específica para o padrão observado;
    - faixas de watts derivadas de BP1/BP2/CP reais;
    - WORK/RECOVERY por estilo;
    - concordância VST × MOXY;
    - prioridade (principal/secundário/alternativa).
    """
    grupo = _PADRAO_PARA_GRUPO.get(padrao_vst, 'insuficiente')

    # ── concordância VST × MOXY ──
    grupo_moxy_us = _MOXY_PARA_GRUPO.get(limitador_moxy_us or '') if limitador_moxy_us else None
    grupo_moxy_pc = _MOXY_PARA_GRUPO.get(limitador_moxy_pc or '') if limitador_moxy_pc else None
    grupos_moxy = {g for g in [grupo_moxy_us, grupo_moxy_pc] if g}

    if not grupos_moxy:
        concordancia = {'status': 'sem_dados',
                        'nota': 'Sem limitador identificado na aba MOXY/Intervenções.'}
    elif grupo in grupos_moxy:
        concordancia = {'status': 'concordante',
                        'nota': (f'VST ({padrao_vst}) e MOXY '
                                 f'({limitador_moxy_us or ""} / {limitador_moxy_pc or ""})'
                                 ' apontam para componentes fisiológicos compatíveis — '
                                 'aumenta a coerência da hipótese.')}
    elif grupo == 'multisistemico' and grupos_moxy:
        concordancia = {'status': 'parcial',
                        'nota': (f'VST (multissistêmico) e MOXY '
                                 f'({limitador_moxy_us or ""} / {limitador_moxy_pc or ""})'
                                 ' — sobreposição parcial: componente MOXY presente '
                                 'no padrão VST.')}
    else:
        concordancia = {'status': 'discordante',
                        'nota': (f'VST ({padrao_vst}) e MOXY '
                                 f'({limitador_moxy_us or ""} / {limitador_moxy_pc or ""})'
                                 ' apontam para componentes diferentes — '
                                 'manter múltiplas hipóteses; não forçar conclusão.')}

    # ── selecção de estilos ──
    conf = _ESTILOS_POR_PADRAO.get(padrao_vst) or {'primarios': [], 'secundarios': []}
    chaves_prim = conf['primarios']
    chaves_sec  = conf['secundarios'] if recorrencia_estado in (
        'RECORRENTE NA MESMA MODALIDADE',
        'RECORRENTE EM MÚLTIPLAS MODALIDADES',
        'OBSERVADO EM MÚLTIPLAS SESSÕES') else conf['secundarios'][:2]

    def _montar(chave, prioridade):
        if chave not in ESTILOS_DE_TREINO:
            return None
        base = ESTILOS_DE_TREINO[chave]
        expl_grupo = _EXPLICACAO_FISIOLOGICA.get(chave, {})
        explicacao = (expl_grupo.get(grupo)
                      or expl_grupo.get('cardiorrespiratorio')
                      or base.get('objetivo', ''))
        faixas = _faixas_watts(bp1_w, bp2_w, cp_w, chave)
        wr = _WORK_RECOVERY.get(chave, {})
        # extrair durações numéricas da string de WORK para calcular distâncias
        import re as _re
        _nums = [int(x) for x in _re.findall(r'\d+', wr.get('work','')) if int(x) < 200]
        _t_min = (min(_nums) * 60) if _nums else None
        _t_max = (max(_nums) * 60) if _nums else None
        pace_dist = faixas_pace_e_distancia(bp1_w, bp2_w, _t_min, _t_max, modalidade)
        return {
            'chave': chave,
            'nome': base['nome'],
            'prioridade': prioridade,
            'limitador_relacionado': padrao_vst,
            'objetivo': base['objetivo'],
            'por_que': explicacao,
            'faixas_watts': faixas,
            'work': wr.get('work', '—'),
            'recovery': wr.get('recovery', '—'),
            'acumulo': wr.get('acumulo', '—'),
            'metricas_principais': base.get('o_que_observar', [])[:4],
            'metricas_complementares': base.get('o_que_observar', [])[4:],
            'resposta_esperada': base.get('resposta_esperada', ''),
            'modalidade': modalidade,
            'nota_modalidade': ('Parâmetros de intensidade (watts) específicos '
                                f'da modalidade {modalidade} — não transferir '
                                'para outra modalidade.'),
            'instrucoes': _instrucoes_operacionais(chave, faixas),
            'pace_e_distancia': pace_dist,
        }

    estilos = []
    prioridades = ['principal'] + ['secundário'] * max(0, len(chaves_prim) - 1)
    for i, c in enumerate(chaves_prim):
        r = _montar(c, prioridades[i] if i < len(prioridades) else 'principal')
        if r:
            estilos.append(r)
    for i, c in enumerate(chaves_sec):
        prio = 'secundário' if i == 0 else 'alternativa'
        r = _montar(c, prio)
        if r:
            estilos.append(r)

    if not estilos and grupo in ('dissociado', 'insuficiente'):
        return {
            'grupo': grupo, 'concordancia': concordancia,
            'estilos': [], 'modalidade': modalidade,
            'nota': ('Dados insuficientes/dissociados para atribuir um estilo '
                     'de treino específico a um limitador. '
                     'Considerar nova verificação com melhor qualidade de dados.'),
        }

    return {
        'grupo': grupo, 'concordancia': concordancia,
        'estilos': estilos[:7],   # máximo 7, nunca lista interminável
        'modalidade': modalidade,
        'n_modalidades_historico': n_modalidades_historico,
        'recorrencia': recorrencia_estado,
        'nota': None,
    }


# ============================================================
# CAMADA OPERACIONAL — traduz termos analíticos em instruções
# simples de execução por estilo. Estas regras são qualitativas
# (nunca inventam limites numéricos individuais sem dados reais).
# Os limites numéricos são acrescentados dinamicamente por
# _instrucoes_operacionais() a partir dos dados da sessão.
# ============================================================
_COMO_CONTROLAR = {
    'continuo_sustentado': {
        'watts': 'faixa entre BP1 e BP2 (referência: próximo de BP2)',
        'work_instrucao': 'bloco contínuo sem interrupção',
        'recovery_instrucao': 'não aplicável — esforço sem pausa',
        'rpe_instrucao': 'RPE moderado a moderado-alto; evite progressão acentuada antes do final do bloco',
        'durante_work': [
            'mantenha HR dentro do comportamento observado nesta intensidade',
            'se RF começar a subir progressivamente sem aumento de potência, o drift está aumentando',
            'não aumente a potência se houver progressão contínua de HR/RF',
        ],
        'entre_works': None,  # sem intervalo
        'continuar_se': [
            'HR/RF permanecem relativamente estáveis ao longo do bloco',
            'a progressão de RF é gradual e controlada',
            'RPE permanece dentro do esperado',
        ],
        'nao_aumentar_se': [
            'HR/RF apresentam progressão acentuada antes do final do bloco',
            'DRIFT ultrapassa a tolerância individual observada na VST',
            'RPE aumenta desproporcionalmente antes da conclusão do acúmulo',
        ],
        'nota_entry': None,  # sem intervalos, sem ENTRY relevante
    },
    'intervalado_recuperacao_completa': {
        'watts': 'faixa entre BP1 e BP2 (ou próximo de BP2)',
        'work_instrucao': 'cada WORK de forma independente, com recuperação suficiente antes do próximo',
        'recovery_instrucao': 'aguarde HR/RF retornarem próximos à faixa de recuperação antes do próximo WORK',
        'rpe_instrucao': 'RPE deve permanecer aproximadamente estável entre os primeiros WORKs',
        'durante_work': [
            'observe se HR sobe de forma controlada e estabiliza antes do final do WORK',
            'RF deve permanecer dentro do comportamento observado na VST para esta intensidade',
            'não aumente a potência se houver progressão contínua de HR/RF antes do final do WORK',
        ],
        'entre_works': [
            'antes do próximo WORK, observe se HR/RF estão próximos da faixa esperada',
            'se cada repetição começa com HR/RF progressivamente mais altos, não aumente o volume',
            'o objetivo da recuperação completa é iniciar cada WORK em estado semelhante ao anterior',
        ],
        'continuar_se': [
            'cada WORK começa com HR/RF aproximadamente no mesmo nível',
            'drift intra-WORK permanece dentro do comportamento esperado',
            'RPE permanece controlado e estável entre repetições',
            'recuperação permanece compatível com o objetivo do treino',
        ],
        'nao_aumentar_se': [
            'ENTRY (início do WORK) apresenta progressão crescente entre repetições',
            'drift intra-WORK aumenta progressivamente a cada repetição',
            'RPE sobe desproporcionalmente antes do final do acúmulo',
            'recovery não retorna ao nível esperado antes do próximo WORK',
        ],
        'nota_entry': (
            'ENTRY: antes de cada WORK, observe se HR/RF iniciam próximos do mesmo nível. '
            'Se cada repetição começar progressivamente mais alto, há acumulação fisiológica '
            '— não aumente potência nem volume enquanto isso ocorrer.'),
    },
    'intervalado_recuperacao_incompleta': {
        'watts': 'faixa entre BP1 e BP2',
        'work_instrucao': 'WORKs com recuperação curta; alguma elevação do ENTRY é esperada e faz parte do estímulo',
        'recovery_instrucao': 'não é necessário recuperar completamente; inicie o próximo WORK ainda parcialmente elevado',
        'rpe_instrucao': 'RPE pode progredir ligeiramente entre repetições; interrompa o acúmulo se RPE subir abruptamente',
        'durante_work': [
            'observe a progressão de HR/RF dentro de cada WORK',
            'algum aumento entre WORKs é esperado — o objetivo é controlar essa acumulação, não eliminá-la',
            'não aumente a potência se a progressão entre WORKs for excessiva',
        ],
        'entre_works': [
            'alguma elevação do ENTRY é esperada neste estilo',
            'o sinal de alerta é uma progressão EXCESSIVA: HR/RF começando cada vez mais alto a cada repetição',
            'se isso ocorrer, encerre o acúmulo — não aumente potência nem volume',
        ],
        'continuar_se': [
            'a progressão entre WORKs é gradual e controlada',
            'HR/RF dentro de cada WORK permanecem relativamente estáveis',
            'RPE não aumenta abruptamente entre repetições',
        ],
        'nao_aumentar_se': [
            'ENTRY apresenta progressão excessiva — cada repetição começa muito mais alto',
            'drift intra-WORK piora progressivamente',
            'RPE sobe abruptamente antes do final do acúmulo planejado',
            'SmO2 acompanha a acumulação com queda progressiva entre WORKs',
        ],
        'nota_entry': (
            'Neste estilo, alguma elevação do ENTRY é esperada e intencional. '
            'O sinal de alerta é progressão EXCESSIVA entre repetições — '
            'não aumente volume nem intensidade se isso ocorrer.'),
    },
    'over_under': {
        'watts': 'fase OVER: acima de BP2 | fase UNDER: entre BP1 e BP2',
        'work_instrucao': 'alterne fase OVER (maior intensidade) e fase UNDER (menor intensidade) sem pausa entre elas',
        'recovery_instrucao': 'a recuperação parcial ocorre no próprio bloco UNDER; pausa completa somente ao final de toda a alternância',
        'rpe_instrucao': 'RPE deve cair ligeiramente durante a fase UNDER; se não cair, a intensidade OVER pode estar excessiva',
        'durante_work': [
            'durante a fase OVER: observe se HR/RF sobem de forma controlada',
            'durante a fase UNDER: observe se HR/RF descem parcialmente antes do próximo OVER',
            'se HR/RF não descerem durante a fase UNDER, a recuperação parcial não está ocorrendo como esperado',
            'não aumente a intensidade da fase OVER se o UNDER não promover recuperação parcial suficiente',
        ],
        'entre_works': [
            'entre blocos completos de alternância, aguarde recuperação mais completa antes de repetir',
            'se cada bloco começa com HR/RF mais altos que o bloco anterior, não aumente o volume',
        ],
        'continuar_se': [
            'a fase UNDER promove recuperação parcial visível de HR/RF',
            'RPE cai durante a fase UNDER e não progride abruptamente entre blocos',
            'HR/RF não apresentam tendência crescente de bloco para bloco',
        ],
        'nao_aumentar_se': [
            'HR/RF não descem durante a fase UNDER (ausência de recuperação parcial)',
            'progressão crescente de HR/RF de bloco para bloco',
            'RPE permanece elevado na fase UNDER e sobe na fase OVER além do esperado',
            'SmO2 continua caindo mesmo durante a fase UNDER',
        ],
        'nota_entry': (
            'OVER/UNDER: o sinal de controle principal é o comportamento de HR/RF '
            'durante a fase UNDER. Se não houver descida parcial, o estímulo não '
            'está produzindo o efeito esperado — reveja a intensidade das fases.'),
    },
    'progressivo': {
        'watts': 'inicie abaixo de BP1, aumente gradualmente até próximo de BP2',
        'work_instrucao': 'aumente a intensidade de forma gradual; não avance se os sinais divergirem prematuramente',
        'recovery_instrucao': 'não aplicável no bloco progressivo; pausa após o bloco completo',
        'rpe_instrucao': 'RPE deve aumentar proporcionalmente à progressão de carga; divergência abrupta de RPE indica progressão excessiva',
        'durante_work': [
            'observe em qual intensidade HR/RF começam a subir de forma mais acentuada',
            'se múltiplos sinais (HR, RF, SmO2) divergirem ao mesmo tempo, isso é relevante',
            'não continue aumentando a intensidade se os sinais divergirem antes de atingir a faixa-alvo',
        ],
        'entre_works': None,
        'continuar_se': [
            'HR/RF sobem de forma proporcional ao aumento de carga',
            'a divergência não ocorre prematuramente (antes da faixa-alvo)',
            'RPE progride de forma controlada e proporcional',
        ],
        'nao_aumentar_se': [
            'HR/RF divergem antes de atingir a faixa-alvo',
            'RPE sobe abruptamente em uma faixa inferior à esperada',
            'múltiplos sinais divergem simultaneamente em intensidade mais baixa que o esperado',
        ],
        'nota_entry': None,
    },
    'inicio_forte': {
        'watts': 'fase inicial: acima de BP2 | fase de sustentação: entre BP1 e BP2',
        'work_instrucao': 'inicie com intensidade elevada por curto período; reduza para a faixa de sustentação e mantenha',
        'recovery_instrucao': 'recuperação completa entre repetições',
        'rpe_instrucao': 'RPE elevado na fase inicial, deve reduzir e estabilizar durante a sustentação',
        'durante_work': [
            'fase inicial: aceite HR/RF elevados por curto período',
            'fase de sustentação: observe se HR/RF estabilizam após a redução de potência',
            'se HR/RF continuarem subindo durante a sustentação, o início foi excessivamente intenso',
            'o objetivo é estabilizar — não sustentar a intensidade inicial',
        ],
        'entre_works': [
            'aguarde recuperação suficiente antes do próximo início forte',
            'se HR/RF não retornam próximo à linha de base na recuperação, a carga total pode ser excessiva',
        ],
        'continuar_se': [
            'HR/RF estabilizam durante a fase de sustentação',
            'RPE reduz após a fase inicial e permanece controlado na sustentação',
            'recuperação entre repetições é suficiente',
        ],
        'nao_aumentar_se': [
            'HR/RF continuam subindo durante a fase de sustentação (não estabilizam)',
            'RPE permanece elevado e não reduz após o início forte',
            'recovery entre repetições é insuficiente — cada repetição começa mais elevada',
        ],
        'nota_entry': (
            'O sinal principal de controle é a ESTABILIZAÇÃO durante a fase de sustentação. '
            'Se os sinais não estabilizarem após reduzir a intensidade, '
            'reveja a intensidade da fase inicial.'),
    },
    'bloco_progressivo': {
        'watts': 'inicie abaixo de BP1, progrida bloco a bloco até próximo de BP2',
        'work_instrucao': 'blocos de duração fixa com aumento gradual de intensidade a cada bloco',
        'recovery_instrucao': 'pausa curta entre blocos (1–3 min)',
        'rpe_instrucao': 'RPE deve aumentar proporcionalmente; divergência abrupta indica bloco excessivo',
        'durante_work': [
            'observe em qual bloco HR/RF começam a subir de forma mais acentuada',
            'esse ponto é a referência individual da sessão — registre-o',
        ],
        'entre_works': [
            'na pausa curta entre blocos, observe se HR/RF descem parcialmente',
            'se não descerem, a pausa pode ser curta demais para o nível de intensidade',
        ],
        'continuar_se': [
            'cada bloco é executável com progressão proporcional de HR/RF e RPE',
            'HR/RF sobem mas não divergem prematuramente dentro de cada bloco',
        ],
        'nao_aumentar_se': [
            'HR/RF divergem antes do final do bloco atual',
            'RPE sobe abruptamente antes de atingir a intensidade-alvo',
        ],
        'nota_entry': None,
    },
    'acumulacao_progressiva': {
        'watts': 'próximo de BP1 (intensidade sustentável)',
        'work_instrucao': 'mantenha a mesma intensidade e aumente progressivamente a duração total a cada sessão',
        'recovery_instrucao': 'recuperação entre sessões (não é um único esforço)',
        'rpe_instrucao': 'RPE deve permanecer estável a cada sessão; aumento de RPE para a mesma carga indica acumulação entre sessões',
        'durante_work': [
            'observe se HR/RF permanecem estáveis pelo tempo total do bloco',
            'aumento progressivo de HR/RF ao longo do bloco (drift) é o sinal de que a duração pode estar no limite',
        ],
        'entre_works': None,
        'continuar_se': [
            'HR/RF permanecem estáveis ao longo de todo o bloco',
            'RPE permanece controlado para a mesma potência',
            'o drift não aumenta progressivamente ao longo das sessões',
        ],
        'nao_aumentar_se': [
            'drift aumenta progressivamente ao longo do bloco',
            'RPE aumenta para a mesma potência em sessões consecutivas',
        ],
        'nota_entry': None,
    },
    'foco_periferico': {
        'watts': 'faixa entre BP1 e BP2',
        'work_instrucao': 'cada WORK focando na observação de SmO2; recuperação suficiente para SmO2 recuperar parcialmente',
        'recovery_instrucao': 'aguarde SmO2 recuperar parcialmente antes do próximo WORK — não precisa retornar ao valor inicial',
        'rpe_instrucao': 'RPE como controle secundário; não há faixa individual determinada sem dados suficientes',
        'durante_work': [
            'observe se SmO2 apresenta queda controlada durante o WORK',
            'HR/RF como referência secundária (não devem dominar a resposta)',
            'se SmO2 cair abruptamente junto com HR/RF elevados, há resposta multissistêmica relevante',
        ],
        'entre_works': [
            'observe se SmO2 recupera parcialmente durante a pausa',
            'se SmO2 não recupera entre WORKs, a recuperação pode ser curta ou a carga elevada demais',
        ],
        'continuar_se': [
            'SmO2 recupera parcialmente entre WORKs',
            'HR/RF permanecem dentro do comportamento esperado',
        ],
        'nao_aumentar_se': [
            'SmO2 não recupera entre WORKs e continua caindo progressivamente',
            'HR/RF apresentam acumulação excessiva entre repetições',
        ],
        'nota_entry': (
            'Observe SmO2 no início de cada WORK: se começar cada vez mais baixo, '
            'a recuperação entre WORKs não está sendo suficiente.'),
    },
    'estimulo_multissistemico': {
        'watts': 'faixa entre BP1 e BP2 (próximo à intensidade do protocolo VST)',
        'work_instrucao': 'monitor HR, RF e SmO2 em conjunto; não priorize um único sinal',
        'recovery_instrucao': 'recuperação completa a moderada antes do próximo WORK',
        'rpe_instrucao': 'RPE como indicador de carga total; deve permanecer controlado e estável entre repetições',
        'durante_work': [
            'observe HR, RF e SmO2 simultaneamente',
            'se vários sinais divergem ao mesmo tempo, isso é o principal sinal fisiológico do estímulo',
            'não tente controlar um único sinal — o objetivo é estabilidade conjunta',
        ],
        'entre_works': [
            'observe se HR, RF e SmO2 retornam próximos ao estado inicial antes do próximo WORK',
            'se vários sinais permanecem elevados ao mesmo tempo no início do próximo WORK, não aumente o volume',
        ],
        'continuar_se': [
            'HR, RF e SmO2 permanecem dentro do comportamento esperado durante e entre WORKs',
            'RPE estável entre repetições',
        ],
        'nao_aumentar_se': [
            'vários sinais (HR + RF + SmO2) divergem simultaneamente antes do final do WORK',
            'RPE aumenta desproporcionalmente',
            'ENTRY de múltiplos sinais sobe progressivamente entre repetições',
        ],
        'nota_entry': (
            'Observe HR, RF e SmO2 no início de cada WORK. '
            'Se múltiplos sinais começam progressivamente mais altos, '
            'há acumulação multissistêmica — encerre o acúmulo.'),
    },
    'tiros_curtos': {
        'watts': 'acima de BP2',
        'work_instrucao': 'esforços curtos acima de BP2; recuperação suficiente para que HR/RF recuperem parcialmente',
        'recovery_instrucao': 'recuperação moderada a completa; se HR/RF não recuperam entre tiros, a carga total está excessiva',
        'rpe_instrucao': 'RPE elevado durante o tiro; deve reduzir claramente durante a recuperação',
        'durante_work': [
            'esforço curto e controlado — não tente sustentar por mais tempo que o planejado',
            'observe se HR/RF atingem o nível esperado para a intensidade',
        ],
        'entre_works': [
            'observe se HR/RF descem durante a recuperação',
            'se HR/RF permanecem elevados durante toda a recuperação, reduza a quantidade de tiros',
        ],
        'continuar_se': [
            'HR/RF recuperam parcialmente entre tiros',
            'RPE reduz claramente durante a recuperação',
            'o início de cada tiro não está progressivamente mais elevado',
        ],
        'nao_aumentar_se': [
            'HR/RF não recuperam entre tiros',
            'o início de cada tiro está progressivamente mais elevado',
            'RPE permanece elevado mesmo durante a recuperação',
        ],
        'nota_entry': (
            'O sinal de alerta principal é HR/RF no início de cada tiro. '
            'Se cada tiro começa mais elevado que o anterior, encerre o acúmulo.'),
    },
}


def _instrucoes_operacionais(chave, faixas_watts, limiter_result=None,
                             comp_recovery=None, comp_rpe=None):
    """Combina as instruções qualitativas do _COMO_CONTROLAR com os
    valores numéricos reais da sessão (quando disponíveis).
    Nunca inventa limites numéricos sem dados.
    Devolve um dict com as chaves que o frontend usa directamente.
    """
    base = _COMO_CONTROLAR.get(chave, {})
    z = (faixas_watts or {}).get('zonas', {})
    fw_disp = (faixas_watts or {}).get('disponivel', False)

    # faixa de watts operacional
    if fw_disp:
        if chave == 'over_under':
            watts_label = f"OVER: {z.get('over','—')} | UNDER: {z.get('under','—')}"
        elif chave in ('inicio_forte',):
            watts_label = (f"Fase forte: {z.get('fase_forte','—')} | "
                           f"Sustentação: {z.get('fase_sustentacao','—')}")
        elif chave == 'progressivo':
            bp1s = z.get('bp1','—'); bp2s = z.get('bp2','—')
            watts_label = f"{bp1s} → {bp2s}"
        elif chave == 'acumulacao_progressiva':
            watts_label = z.get('bp1', z.get('abaixo_bp1', '—'))
        else:
            faixa = (z.get('entre_bp1_bp2')
                     if z.get('entre_bp1_bp2') and z['entre_bp1_bp2'] != '—'
                     else z.get('proximo_bp2', z.get('bp2', '—')))
            watts_label = faixa
    else:
        watts_label = (faixas_watts or {}).get(
            'nota', 'Faixa de potência não determinada — BP1/BP2/CP insuficientes.')

    # notas de recovery da sessão (qualitativas, sem inventar números)
    recovery_nota = ''
    if comp_recovery:
        status_rec = (comp_recovery or {}).get('status', '')
        if status_rec in ('DETERIORANDO', 'AUSENTE'):
            recovery_nota = ('⚠ Recovery deteriorando na sessão de referência — '
                             'priorize recuperação mais completa.')
        elif status_rec == 'CONVERGENTE':
            recovery_nota = '✓ Recovery convergente na sessão de referência.'

    # RPE da sessão (qualitativo)
    rpe_nota = ''
    if comp_rpe:
        rpe_status = (comp_rpe or {}).get('status', '')
        if rpe_status == 'CONSISTENTE':
            rpe_nota = 'RPE consistente na sessão de referência — use como controle secundário.'
        elif rpe_status in ('ALTO', 'AUMENTANDO'):
            rpe_nota = ('⚠ RPE elevado/crescente na sessão de referência — '
                        'monitore de perto durante o acúmulo.')

    return {
        'watts_label': watts_label,
        'work_instrucao': base.get('work_instrucao', '—'),
        'recovery_instrucao': base.get('recovery_instrucao', '—'),
        'rpe_instrucao': base.get('rpe_instrucao',
                                  'Limite individual não determinado — use RPE como controle complementar.'),
        'durante_work': base.get('durante_work', []),
        'entre_works': base.get('entre_works'),
        'continuar_se': base.get('continuar_se', []),
        'nao_aumentar_se': base.get('nao_aumentar_se', []),
        'nota_entry': base.get('nota_entry'),
        'recovery_nota': recovery_nota,
        'rpe_nota': rpe_nota,
    }


# ============================================================
# PACE / 500 m — Fórmula oficial Concept2
# Derivada empiricamente dos dados publicados em:
#   https://www.concept2.com/training/watts-calculator
# Relação: watts = C / pace_500m_s^3
#   onde C = 2×10^8 e pace_500m_s = pace em segundos por 500 m
# Verificado: 100W↔2:06, 200W↔1:40, 250W↔1:33, 300W↔1:27, 400W↔1:19
# Aplicável a Row e SkiErg (Concept2). Não usar para Bike nem Run.
# ============================================================
_C2_CONSTANTE = 2e8   # watts * pace_500m_s^3 = 2×10^8

def watts_para_pace_500m(watts):
    """Devolve o pace /500 m em segundos para uma dada potência (W).
    Usa a fórmula Concept2: pace_s = (2×10^8 / watts)^(1/3).
    Devolve None se watts ≤ 0.
    """
    if not watts or watts <= 0:
        return None
    return (_C2_CONSTANTE / watts) ** (1 / 3)


def _fmt_pace(pace_500m_s):
    """Converte segundos/500m em string 'M:SS' (ex: 110.4 → '1:50')."""
    if pace_500m_s is None:
        return '—'
    mins = int(pace_500m_s // 60)
    segs = int(round(pace_500m_s % 60))
    if segs == 60:
        mins += 1; segs = 0
    return f"{mins}:{segs:02d}"


def _distancia_metros(duracao_s, watts):
    """Distância percorrida (m) dado duração em segundos e potência.
    Usa a fórmula Concept2: dist = duracao_s / (pace_500m_s / 500).
    """
    if not duracao_s or not watts or watts <= 0:
        return None
    pace_500m_s = (_C2_CONSTANTE / watts) ** (1 / 3)
    pace_s_per_m = pace_500m_s / 500
    return round(duracao_s / pace_s_per_m)


def faixas_pace_e_distancia(bp1_w, bp2_w, work_min_s, work_max_s, modalidade):
    """Para Row e SkiErg, calcula pace/500m e distâncias a partir das faixas
    de watts e das durações de série. Devolve dict com faixas textuais.
    Para Bike e Run, devolve None (pace/500m não se aplica).
    """
    if modalidade not in ('Row', 'Ski'):
        return None
    # pace: W maior → pace mais rápido (menor número)
    p_lento = watts_para_pace_500m(bp1_w) if bp1_w else None
    p_rapido = watts_para_pace_500m(bp2_w) if bp2_w else None

    def _dist_faixa(w, t_min, t_max):
        d_min = _distancia_metros(t_min, w) if t_min else None
        d_max = _distancia_metros(t_max, w) if t_max else None
        return d_min, d_max

    # distância mínima = W_max (ritmo rápido) × duração mínima
    # distância máxima = W_min (ritmo lento) × duração máxima
    if bp2_w and work_min_s:
        d_min = _distancia_metros(work_min_s, bp2_w)
    else:
        d_min = None
    if bp1_w and work_max_s:
        d_max = _distancia_metros(work_max_s, bp1_w)
    else:
        d_max = None

    pace_str = None
    if p_lento and p_rapido:
        pace_str = f"{_fmt_pace(p_rapido)}–{_fmt_pace(p_lento)} /500m"
    elif p_rapido:
        pace_str = f"≈ {_fmt_pace(p_rapido)} /500m"
    elif p_lento:
        pace_str = f"≈ {_fmt_pace(p_lento)} /500m"

    dist_str = None
    if d_min and d_max and d_min != d_max:
        dist_str = f"≈ {d_min:,}–{d_max:,} m".replace(',', '.')
    elif d_min:
        dist_str = f"≈ {d_min:,} m".replace(',', '.')
    elif d_max:
        dist_str = f"≈ {d_max:,} m".replace(',', '.')

    return {
        'pace_500m': pace_str,
        'distancia_por_serie': dist_str,
        'pace_min_s': p_rapido,  # numérico para cálculos
        'pace_max_s': p_lento,
    }
