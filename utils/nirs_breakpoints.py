"""utils/nirs_breakpoints.py — limiares a partir do SmO2.

Tres coisas, todas para qualquer modalidade:

  BREAKPOINTS   regressao segmentada de dois trocos continuos sobre a
                relacao SmO2 x intensidade, com verificacao de plato.

  CER           Critical Energetic Rate: o analogo do CP feito com a taxa
                de queda do SmO2 em vez da potencia.

  HIPOCAPNIA    respiracao rapida e superficial imita limitacao de
                utilizacao. Sem isto, a 5-1-5 chama "utilizacao" ao que
                pode ser tecnica respiratoria.

FIABILIDADE POR MODALIDADE
    Calcula-se em todas, mas o resultado vem etiquetado. As referencias:

    Bike  Feldmann 2022 (n=10): concordancia moderada com VT1/VT2, com
          SUBESTIMACAO sistematica -- 15.4 +/- 7.4 bpm no BP1. E SmO2min
          correlaciona com VO2pico (R2=0.85).
    Run   Feldmann 2022: BP1 concorda bem (1.8 +/- 5.6 bpm), mas SmO2min
          nao serve para VO2pico (R2=0.27). Tres de dez testes so'
          mostraram um breakpoint.
    Row   Possamai 2024 (n=14, remo, especifico): "poor agreement (...)
          these thresholds should not be considered interchangeable"
          com MLSS e CP. Calcula-se, mas nao se usa para prescrever.
    Ski   sem literatura encontrada. Trata-se como o remo.

    Nao inventar confianca que os dados nao suportam e' mais util do que
    um numero limpo.
"""

FIABILIDADE = {
    'Bike': {'nivel': 'moderada', 'vies': 'subestima os limiares',
             'fonte': 'Feldmann 2022 (n=10, ciclismo)',
             'usar_para_prescrever': True},
    'Run': {'nivel': 'moderada', 'vies': 'BP1 concorda bem; SmO2min não '
            'reflecte VO2pico na corrida (R²=0,27)',
            'fonte': 'Feldmann 2022 (n=10, corrida)',
            'usar_para_prescrever': True},
    'Row': {'nivel': 'baixa', 'vies': 'concordância fraca com MLSS e CP; os '
            'autores dizem explicitamente que não são intercambiáveis',
            'fonte': 'Possamai 2024 (n=14, remo ergómetro)',
            'usar_para_prescrever': False},
    'Ski': {'nivel': 'desconhecida', 'vies': 'sem literatura específica; '
            'tratado como o remo por semelhança de gesto',
            'fonte': None, 'usar_para_prescrever': False},
}

# Kowalski 2025: janelas de 30 s e corte de 5 unidades para declarar
# plato. O proprio artigo avisa que 10 unidades, ou cortes relativos de
# 5-10%, geram falsos positivos.
PLATO_JANELA_S = 30
PLATO_CORTE = 5.0


def _fit(xs, ys, a, b):
    n = b - a
    if n < 2:
        return None
    mx = sum(xs[a:b]) / n
    my = sum(ys[a:b]) / n
    sxx = sum((x - mx) ** 2 for x in xs[a:b])
    if sxx <= 0:
        return None
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(a, b))
    m = sxy / sxx
    return m, my - m * mx


def _rss(xs, ys, m, b, a, z):
    return sum((m * xs[i] + b - ys[i]) ** 2 for i in range(a, z))


def dois_segmentos(xs, ys, min_pontos=2):
    """Ajuste continuo de dois trocos; devolve o tau e os dois declives.

    Continuo: o segundo troco arranca onde o primeiro acaba, em vez de
    serem duas rectas independentes que se cruzam onde calha. E' a forma
    que a revisao descreve e a que faz sentido fisico -- o sinal nao salta
    no limiar, muda de inclinacao.
    """
    n = len(xs)
    if n < 2 * min_pontos:
        return {'ok': False,
                'motivo': f'só {n} pontos; são precisos {2 * min_pontos}'}
    melhor = None
    for k in range(min_pontos, n - min_pontos + 1):
        p1 = _fit(xs, ys, 0, k)
        if not p1:
            continue
        m1, b1 = p1
        # o segundo troco parte do fim do primeiro: y(tau) fixo
        tau = xs[k - 1]
        y_tau = m1 * tau + b1
        num = sum((xs[i] - tau) * (ys[i] - y_tau) for i in range(k, n))
        den = sum((xs[i] - tau) ** 2 for i in range(k, n))
        if den <= 0:
            continue
        m2 = num / den
        b2 = y_tau - m2 * tau
        r = _rss(xs, ys, m1, b1, 0, k) + _rss(xs, ys, m2, b2, k, n)
        if melhor is None or r < melhor[0]:
            melhor = (r, k, m1, b1, m2, b2, tau)
    if melhor is None:
        return {'ok': False, 'motivo': 'nenhuma divisão ajustável'}
    r, k, m1, b1, m2, b2, tau = melhor

    # quanto e' que o ajuste de dois trocos melhora sobre uma recta so'?
    p0 = _fit(xs, ys, 0, len(xs))
    rss0 = _rss(xs, ys, p0[0], p0[1], 0, len(xs)) if p0 else None
    ganho = (1 - r / rss0) if rss0 and rss0 > 0 else None

    return {'ok': True, 'tau': round(tau, 1), 'indice': k,
            'declive_1': round(m1, 5), 'declive_2': round(m2, 5),
            'razao_declives': (round(m2 / m1, 2) if m1 else None),
            'rss': round(r, 3),
            'ganho_sobre_recta': (round(ganho, 3) if ganho is not None
                                  else None),
            'y_no_tau': round(m1 * tau + b1, 2),
            'n_pontos': len(xs)}


def plato(xs, ys, janela=PLATO_JANELA_S, corte=PLATO_CORTE, hz=1.0):
    """Primeiro ponto em que |ΔSmO2| fica abaixo do corte numa janela.

    Criterio de Kowalski 2025. Devolve tambem quantas janelas seguidas
    ficaram planas, porque uma janela isolada nao e' plato.
    """
    passo = max(1, int(janela * hz))
    if len(ys) < 2 * passo:
        return {'ok': False, 'motivo': 'série curta para a janela'}
    planas, inicio = 0, None
    for i in range(0, len(ys) - passo, passo):
        d = abs(ys[i + passo] - ys[i])
        if d <= corte:
            if inicio is None:
                inicio = i
            planas += 1
        else:
            inicio, planas = None, 0
        if planas >= 2:
            return {'ok': True, 'x': round(xs[inicio], 1),
                    'indice': inicio, 'janelas_planas': planas,
                    'janela_s': janela, 'corte': corte,
                    'nota': ('duas janelas seguidas abaixo do corte; '
                             'uma só não é platô')}
    return {'ok': False, 'motivo': f'sem platô com corte de {corte} em '
                                   f'janelas de {janela} s'}


def tres_segmentos(xs, ys, min_pontos=3, restringir=True):
    """Dois breakpoints de uma vez, por procura nos dois em simultaneo.

    Bhambhani descreve TRES fases no SmO2 durante exercicio incremental:
    plato inicial, queda continua, e nivelamento ou segunda queda. Ajustar
    dois trocos e depois voltar a partir o de cima nao encontra o mesmo:
    o primeiro ajuste poe a quebra onde ela e' mais forte, que costuma ser
    a segunda, e o BP1 sai errado.
    """
    n = len(xs)
    if n < 3 * min_pontos:
        return {'ok': False,
                'motivo': f'só {n} pontos; são precisos {3 * min_pontos}'}
    # RESTRICAO FISIOLOGICA, nao estatistica.
    #
    # Fernandez-Jarillo 2026 aponta directamente o erro deste metodo:
    #   "This misplacement is often driven by breakpoint-detection
    #    approaches based on curve fitting (e.g., maximal distance,
    #    segmented linear regression) which OPTIMISE STATISTICAL FIT and
    #    can therefore select a slope change WITHIN Phase B rather than
    #    the physiological Phase B-Phase C transition."
    #
    # As tres fases de Bhambhani sao: A estavel, B queda linear, C
    # estabiliza. Portanto os declives tem de obedecer a
    #
    #     |m1| pequeno  ->  m2 claramente negativo  ->  |m3| < |m2|
    #
    # O ajuste livre escolhia frequentemente o contrario: partia a fase B
    # em duas, com a segunda mais inclinada, porque isso reduz mais o
    # erro. Estatisticamente melhor, fisiologicamente errado.
    melhor = None
    for k1 in range(min_pontos, n - 2 * min_pontos + 1):
        p1 = _fit(xs, ys, 0, k1)
        if not p1:
            continue
        m1, b1 = p1
        tau1 = xs[k1 - 1]
        y1 = m1 * tau1 + b1
        for k2 in range(k1 + min_pontos, n - min_pontos + 1):
            num = sum((xs[i] - tau1) * (ys[i] - y1) for i in range(k1, k2))
            den = sum((xs[i] - tau1) ** 2 for i in range(k1, k2))
            if den <= 0:
                continue
            m2 = num / den
            b2 = y1 - m2 * tau1
            tau2 = xs[k2 - 1]
            y2 = m2 * tau2 + b2
            num3 = sum((xs[i] - tau2) * (ys[i] - y2) for i in range(k2, n))
            den3 = sum((xs[i] - tau2) ** 2 for i in range(k2, n))
            if den3 <= 0:
                continue
            m3 = num3 / den3
            b3 = y2 - m3 * tau2
            if restringir:
                # fase B desce, fase C abranda face a B
                if not (m2 < 0 and abs(m3) < abs(m2)):
                    continue
                # fase A e' mais plana do que a fase B
                if abs(m1) >= abs(m2):
                    continue
            r = (_rss(xs, ys, m1, b1, 0, k1)
                 + _rss(xs, ys, m2, b2, k1, k2)
                 + _rss(xs, ys, m3, b3, k2, n))
            if melhor is None or r < melhor[0]:
                melhor = (r, tau1, tau2, m1, m2, m3, k1, k2, y1, y2)
    if melhor is None:
        return {'ok': False,
                'motivo': ('nenhuma divisão respeita as três fases: fase A '
                           'mais plana, fase B a descer, fase C a abrandar. '
                           'Um ajuste que minimize só o erro partiria a fase '
                           'B em duas, e isso não é um breakpoint '
                           'fisiológico')}
    r, tau1, tau2, m1, m2, m3, k1, k2, y1, y2 = melhor
    p0 = _fit(xs, ys, 0, n)
    rss0 = _rss(xs, ys, p0[0], p0[1], 0, n) if p0 else None
    return {'ok': True,
            'bp1': round(tau1, 1), 'bp2': round(tau2, 1),
            'smo2_bp1': round(y1, 1), 'smo2_bp2': round(y2, 1),
            'declives': [round(m1, 5), round(m2, 5), round(m3, 5)],
            'fases': {'A': 'estável', 'B': 'queda linear', 'C': 'estabiliza'},
            'restringido': restringir,
            'restricao': (('|m1| < |m2|, m2 < 0, |m3| < |m2| — as três fases '
                           'de Bhambhani, impostas antes de minimizar o erro')
                          if restringir else
                          'nenhuma: só minimiza o erro'),
            'indices': [k1, k2], 'rss': round(r, 3),
            'ganho_sobre_recta': (round(1 - r / rss0, 3)
                                  if rss0 and rss0 > 0 else None),
            'n_pontos': n}


def breakpoints(blocos, modalidade=None, canal='smo2_min'):
    """BP1 e BP2 do SmO2 contra a intensidade, pelos blocos de trabalho.

    Usa o MINIMO de SmO2 de cada bloco contra a potencia media do bloco.
    O minimo e nao a media porque e' o que representa a extraccao maxima
    naquela carga -- e' a mesma escolha da pergunta 4A da 5-1-5.
    """
    pts = sorted(
        ((b.get('watts_medio'), b.get(canal)) for b in blocos
         if b.get('watts_medio') is not None and b.get(canal) is not None),
        key=lambda p: p[0])
    if len(pts) < 4:
        # A fiabilidade era preenchida so' no fim, e esta saida antecipada
        # devolvia "0 degraus" quando havia 5. Preenche-se aqui tambem.
        f0 = dict(FIABILIDADE.get(modalidade or '', {}) or
                  {'nivel': 'desconhecida'})
        f0['n_degraus'] = len(pts)
        f0['aviso_n'] = (f'{len(pts)} degraus: são precisos 4 para um '
                         'breakpoint e 9 para dois')
        return {'ok': False,
                'motivo': (f'só {len(pts)} degraus com potência e SmO2; '
                           'são precisos 4 para um breakpoint e 9 para os '
                           'dois'),
                'n_blocos': len(pts), 'fiabilidade': f0,
                'pontos': [{'watts': round(x, 1), 'smo2': round(y, 1)}
                           for x, y in pts]}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    # tres trocos primeiro: e' o que corresponde as tres fases descritas
    # na literatura. Se nao houver pontos para tres, cai em dois.
    # Dois ajustes: com e sem a restricao fisiologica. Quando divergem, e'
    # sinal de que a fase B nao e' linear -- tem uma parte final mais
    # inclinada -- e nesse caso nem tres segmentos a representam. O artigo
    # que aponta este problema recomenda inspeccao visual informada pelas
    # fases; mostrar os dois e deixar decidir e' o mais honesto que se pode
    # fazer sem ser o utilizador a olhar.
    tres = tres_segmentos(xs, ys)
    tres_livre = tres_segmentos(xs, ys, restringir=False)
    out = {'pontos': [{'watts': round(x, 1), 'smo2': round(y, 1)}
                      for x, y in pts]}
    if tres_livre.get('ok'):
        out['ajuste_livre'] = {
            'bp1': tres_livre['bp1'], 'bp2': tres_livre['bp2'],
            'declives': tres_livre['declives'],
            'nota': ('ajuste que só minimiza o erro, sem a restrição das '
                     'três fases. Mostrado para comparação')}
        if tres.get('ok'):
            d1 = abs(tres_livre['bp1'] - tres['bp1'])
            d2 = abs(tres_livre['bp2'] - tres['bp2'])
            m = tres_livre['declives']
            partiu_b = abs(m[2]) > abs(m[1])
            if partiu_b or d1 > 15 or d2 > 15:
                out['divergencia'] = {
                    'bp1_difere': round(d1, 1), 'bp2_difere': round(d2, 1),
                    'livre_partiu_fase_b': partiu_b,
                    'leitura': (
                        'os dois ajustes discordam. O livre '
                        + ('parte a fase B em duas, com a segunda mais '
                           'inclinada — é o erro que Fernández-Jarillo 2026 '
                           'descreve, e estatisticamente ele ganha sempre'
                           if partiu_b else
                           'coloca os pontos noutro sítio')
                        + '. Quando isto acontece, a fase B não é linear e '
                          'nem três segmentos a representam: o artigo '
                          'recomenda inspecção visual guiada pelas fases. '
                          'Olha o gráfico antes de aceitar qualquer um')}

    if tres.get('ok'):
        out['ok'] = True
        out['metodo'] = 'três segmentos contínuos, com restrição das fases'
        out['bp1'] = {'ok': True, 'tau': tres['bp1'], 'smo2': tres['smo2_bp1']}
        out['bp2'] = {'ok': True, 'tau': tres['bp2'], 'smo2': tres['smo2_bp2']}
        out['declives'] = tres['declives']
        out['ganho_sobre_recta'] = tres['ganho_sobre_recta']
    else:
        d = dois_segmentos(xs, ys)
        out['ok'] = d.get('ok')
        out['metodo'] = 'dois segmentos contínuos'
        out['motivo_sem_tres'] = tres.get('motivo')
        out['bp1'] = d
        out['bp2'] = {'ok': False,
                      'motivo': 'sem pontos para um segundo breakpoint'}
        out['ganho_sobre_recta'] = d.get('ganho_sobre_recta')
    f = dict(FIABILIDADE.get(modalidade or '', {}) or
             {'nivel': 'desconhecida'})
    # Numero de degraus: testado com escadas de breakpoints conhecidos,
    # 12 acertam o BP1 no valor exacto e o BP2 a um degrau; 9 erram 30 W
    # no BP1 e 55 no BP2. Nao e' um detalhe -- e' a diferenca entre um
    # limiar util e um numero.
    n = len(pts)
    if n < 9:
        f['aviso_n'] = (f'{n} degraus: com menos de 9 o ajuste de três '
                        'troços não é possível e o de dois encontra a quebra '
                        'dominante, não a primeira')
    elif n < 12:
        f['aviso_n'] = (f'{n} degraus: o BP1 pode errar por um a dois '
                        'degraus. Testado com escadas de breakpoint '
                        'conhecido, 12 degraus acertam, 9 erram 30 W')
    else:
        f['aviso_n'] = f'{n} degraus: suficientes para o ajuste'
    f['n_degraus'] = n
    out['fiabilidade'] = f
    # Teste F contra a recta unica, em vez de olhar so' para o ganho.
    #
    # Numa descida perfeitamente linear com ruido, o ganho chega a 0.27 --
    # dois breakpoints trazem quatro parametros extra e ajustam ruido de
    # graca. Comparar o ganho com um corte fixo aceitava essa curva como
    # tendo dois limiares. O teste F desconta a complexidade: pergunta se
    # a reducao do erro compensa os parametros gastos.
    p0 = _fit(xs, ys, 0, len(xs))
    if p0 and out.get('ok'):
        rss0 = _rss(xs, ys, p0[0], p0[1], 0, len(xs))
        k_extra = 4 if out['metodo'].startswith('três') else 2
        k_seg = 2 + k_extra
        gl = len(xs) - k_seg
        rss1 = tres.get('rss') if tres.get('ok') else out['bp1'].get('rss')
        if gl > 0 and rss1 and rss1 > 0:
            f_stat = ((rss0 - rss1) / k_extra) / (rss1 / gl)
            out['f_vs_recta'] = round(f_stat, 2)
            try:
                from scipy.stats import f as _fd
                out['p_vs_recta'] = round(float(1 - _fd.cdf(f_stat, k_extra, gl)), 4)
            except ImportError:
                out['p_vs_recta'] = None
            # F critico aproximado a 5% para poucos graus de liberdade
            critico = 4.0 if gl < 10 else 3.0
            if f_stat < critico:
                out['ok'] = False
                out['motivo'] = (
                    f'o ajuste segmentado não melhora o suficiente sobre uma '
                    f'recta única (F={round(f_stat, 1)}, abaixo de {critico}): '
                    'a curva não tem quebra, desce de forma contínua')
    out['nota'] = (
        'BP1 marca a saída do domínio moderado, BP2 a entrada no severo. '
        'ganho_sobre_recta diz quanto o ajuste de dois troços melhora '
        'sobre uma recta única: abaixo de 0,10 não há quebra que valha, '
        'só ruído ajustado.')
    return out


# ══════════════════════════════════════════════════════════════════════════
# CRITICAL ENERGETIC RATE
#
#     Tempo ate a exaustao = M' / (ΔSmO2 - CER)
#
# Mesma hiperbole do CP, com a taxa de queda do SmO2 no lugar da potencia.
# Ajusta-se ΔSmO2 contra 1/duracao: o declive e' M' e a ordenada na origem
# e' o CER.
#
# AVISO QUE NAO SE PODE OMITIR: isto exige ensaios ATE A EXAUSTAO. Blocos
# de 5 minutos que acabam porque o relogio tocou nao sao pontos validos --
# a duracao nao e' a duracao maxima naquela taxa. Calcular na mesma da um
# numero, mas um numero de outra coisa.
# ══════════════════════════════════════════════════════════════════════════

def cer(ensaios, ate_exaustao=False):
    """ensaios: [(delta_smo2_por_s, duracao_s)]"""
    pts = [(float(d), float(t)) for d, t in ensaios
           if d is not None and t and t > 0]
    if len(pts) < 3:
        return {'ok': False, 'motivo': f'só {len(pts)} ensaios; são precisos 3'}
    xs = [1.0 / t for _d, t in pts]
    ys = [d for d, _t in pts]
    p = _fit(xs, ys, 0, len(xs))
    if not p:
        durs = sorted({round(t) for _d, t in pts})
        return {'ok': False,
                'motivo': (
                    'ajuste impossível: o CER regride ΔSmO2 contra 1/duração, '
                    'e todos os ensaios têm a mesma duração ('
                    + ', '.join(f'{d} s' for d in durs[:4])
                    + '), portanto não há variação em x. Não é uma falha do '
                      'cálculo — é impossível por construção. O CER precisa '
                      'de ensaios de DURAÇÕES DIFERENTES até à exaustão, '
                      'como o CP precisa de esforços de durações diferentes'),
                'duracoes_encontradas': durs}
    m_linha, cer_v = p
    my = sum(ys) / len(ys)
    sst = sum((y - my) ** 2 for y in ys)
    sse = _rss(xs, ys, m_linha, cer_v, 0, len(xs))
    r2 = (1 - sse / sst) if sst > 0 else None

    return {
        'ok': True,
        'cer_pct_por_s': round(cer_v, 4),
        'm_linha': round(m_linha, 2),
        'r2': round(r2, 3) if r2 is not None else None,
        'n_ensaios': len(pts),
        'ensaios': [{'delta_smo2': round(d, 4), 'duracao_s': round(t)}
                    for d, t in pts],
        'valido': bool(ate_exaustao),
        'aviso': (None if ate_exaustao else
                  'os blocos não terminaram por exaustão: a duração é a do '
                  'protocolo, não a duração máxima sustentável nessa taxa. '
                  'O número sai, mas não é o CER — para o obter são '
                  'precisos 3 ensaios até à falha'),
        'nota': ('CER é a taxa de queda de SmO2 que se sustenta '
                 'indefinidamente. M\\u2032 é a reserva, o análogo do W\\u2032. '
                 'Tempo até à exaustão = M\\u2032 / (ΔSmO2 − CER)'),
    }


def delta_smo2_do_bloco(tempo, smo2, t0, t1):
    """Taxa de queda de SmO2 dentro de um bloco, em % por segundo."""
    pts = [(tempo[i], smo2[i]) for i in range(min(len(tempo), len(smo2)))
           if t0 <= tempo[i] <= t1 and smo2[i] is not None]
    if len(pts) < 10:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    p = _fit(xs, ys, 0, len(xs))
    return round(p[0], 5) if p else None


# ══════════════════════════════════════════════════════════════════════════
# HIPOCAPNIA
#
# Do documento oficial da 5-1-5: "Poor breathing technique, such as rapid
# shallow breathing, that causes Hypocapnia shows up as a muscle oxidative
# capacity limitation and is not what we are referring to here as a
# Pulmonary Limitation."
#
# Mecanismo: CO2 baixo alcaliniza o sangue, o que AUMENTA a afinidade da
# hemoglobina pelo oxigenio -- ela larga-o com mais dificuldade no
# musculo. O SmO2 fica alto e parece que o musculo nao extrai, quando o
# problema e' que o oxigenio nao se solta.
#
# Nao medimos CO2. O que se pode medir e' o padrao: frequencia
# respiratoria alta para a FC observada. Sem volume corrente nao se
# distingue "rapida e superficial" de "rapida e profunda", e isso vai
# dito.
# ══════════════════════════════════════════════════════════════════════════

def hipocapnia(blocos, tempo, canais, limiar_desvio=1.5):
    """Sinal de respiração desproporcionada para a FC observada.

    Ajusta-se a relacao FR x FC nos blocos de intensidade BAIXA da propria
    sessao -- onde a respiracao ainda e' espontanea -- e ve-se quanto os
    blocos altos se desviam dela. Calibrado no proprio atleta, nao numa
    norma de populacao.
    """
    resp = canais.get('respiration') or []
    hr = canais.get('heartrate') or []
    smo2 = canais.get('smo2') or []
    if not resp or not hr:
        return {'ok': False, 'motivo': 'sem frequência respiratória ou FC'}

    def _med(serie, t0, t1):
        vs = [serie[i] for i in range(min(len(tempo), len(serie)))
              if t0 <= tempo[i] <= t1 and serie[i] is not None]
        return sum(vs) / len(vs) if vs else None

    ons = sorted((b for b in blocos if b.get('on')),
                 key=lambda b: b.get('watts_medio') or 0)
    dados = []
    for b in ons:
        r = _med(resp, b['t0'], b['t1'])
        h = _med(hr, b['t0'], b['t1'])
        s = _med(smo2, b['t0'], b['t1']) if smo2 else None
        if r and h:
            dados.append({'watts': b.get('watts_medio'), 'fr': r,
                          'fc': h, 'smo2': s})
    if len(dados) < 4:
        return {'ok': False, 'motivo': f'só {len(dados)} blocos utilizáveis'}

    metade = max(2, len(dados) // 2)
    base = dados[:metade]
    xs = [d['fc'] for d in base]
    ys = [d['fr'] for d in base]
    p = _fit(xs, ys, 0, len(xs))
    if not p:
        return {'ok': False, 'motivo': 'sem variação de FC nos blocos baixos'}
    m, b0 = p
    resid = [d['fr'] - (m * d['fc'] + b0) for d in base]
    dp = (sum(x * x for x in resid) / len(resid)) ** 0.5 or 1.0

    altos = dados[metade:]
    for d in altos:
        d['fr_esperada'] = round(m * d['fc'] + b0, 1)
        d['excesso_fr'] = round(d['fr'] - d['fr_esperada'], 1)
        d['z'] = round(d['excesso_fr'] / dp, 2)

    z_max = max((d['z'] for d in altos), default=0)
    smo2_alto = all((d['smo2'] or 0) > 55 for d in altos if d['smo2'])
    suspeita = z_max >= limiar_desvio and smo2_alto

    return {
        'ok': True,
        'suspeita': suspeita,
        'z_maximo': round(z_max, 2),
        'limiar_z': limiar_desvio,
        'smo2_alto_nos_blocos_duros': smo2_alto,
        'recta_base': {'declive_fr_por_bpm': round(m, 4),
                       'intercepto': round(b0, 2), 'desvio': round(dp, 2),
                       'n_blocos_base': len(base)},
        'blocos': dados,
        'leitura': (
            'a frequência respiratória sobe muito acima do que a FC prevê, '
            'e o SmO2 mantém-se alto nos blocos duros. Isso é compatível '
            'com hipocapnia por respiração rápida e superficial — que '
            'IMITA uma limitação de utilização. Antes de treinar extracção, '
            'vale a pena confirmar a técnica respiratória'
            if suspeita else
            'a respiração acompanha a FC dentro do esperado: a leitura de '
            'utilização não parece ser artefacto respiratório'),
        'limite': ('sem volume corrente nem capnometria não se distingue '
                   '"rápida e superficial" de "rápida e profunda". Isto é '
                   'um sinal de alerta, não um diagnóstico'),
    }


# ══════════════════════════════════════════════════════════════════════════
# MLSS POR PADRAO DE DESSATURACAO — o metodo para blocos, nao para rampa
#
# Bruce Rogers (muscleoxygentraining.com) descreve exactamente o protocolo
# de blocos de 5 min, e o criterio NAO e' um breakpoint numa curva:
#
#   "What we are going to look for is an O2 desaturation pattern that
#    continuously downslopes over a 5 minute constant power interval."
#
#   Abaixo do MLSS: o SmO2 desce e ESTABILIZA dentro do bloco.
#   Acima do MLSS:  o SmO2 desce CONTINUAMENTE ate' ao fim.
#
# O contra-exemplo dele e' o que importa: um bloco a 268 W descia, mas
# estabilizava aos 4 minutos -- "This would NOT be considered a valid
# marker of exceeding MLSS". Nao basta o declive medio ser negativo; tem
# de continuar negativo ate' ao fim.
#
# E' por isto que os breakpoints por regressao davam F=0.1 nas sessoes
# deste atleta: o metodo da regressao segmentada e' para RAMPA continua,
# onde a queda acelera. Num protocolo de blocos com descanso, cada bloco
# parte de um SmO2 diferente e a informacao esta na FORMA de cada bloco,
# nao na envolvente dos minimos.
#
# Funciona em qualquer modalidade. Rogers mostra ciclismo, corrida e
# esqui, usando FC quando nao ha potencia.
# ══════════════════════════════════════════════════════════════════════════

# Fraccao inicial do bloco ignorada: o SmO2 cai sempre no arranque por
# transiente, e isso nao diz nada sobre sustentabilidade.
TRANSIENTE = 0.35

# Declive, em % de SmO2 por minuto, abaixo do qual se considera estavel.
# Nao vem de nenhum artigo -- e' um criterio de decisao, e por isso e'
# parametro. Rogers decide a olho; aqui tem de haver um numero.
ESTAVEL_POR_MIN = 0.5


def _declive_por_min(tempo, serie, t0, t1):
    pts = [(tempo[i], serie[i]) for i in range(min(len(tempo), len(serie)))
           if t0 <= tempo[i] <= t1 and serie[i] is not None]
    if len(pts) < 10:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    p = _fit(xs, ys, 0, len(xs))
    return p[0] * 60.0 if p else None


# Duracao a partir da qual o criterio do Rogers e' valido. Ele exige 5
# minutos: abaixo disso o SmO2 ainda esta no transiente de arranque e nao
# houve tempo de mostrar se assenta.
DUR_IDEAL = 300
# Piso absoluto: abaixo disto nao ha sinal nenhum para medir declive.
DUR_MINIMA = 90


def mlss_por_dessaturacao(tempo, smo2, blocos, transiente=TRANSIENTE,
                          estavel=ESTAVEL_POR_MIN, dur_minima=DUR_MINIMA):
    """MLSS entre o último bloco estável e o primeiro que desce até ao fim.

    A duracao minima era 240 s, o que rejeitava TODOS os blocos numa
    sessao de 2 min por degrau -- e' isso que produzia "0 blocos". Passou
    a 90 s, com aviso quando ficam abaixo dos 5 minutos que o metodo pede.
    """
    ons = sorted((b for b in blocos if b.get('on')),
                 key=lambda b: b.get('watts_medio') or 0)
    linhas = []
    for b in ons:
        dur = b['t1'] - b['t0']
        if dur < dur_minima:
            linhas.append({'watts': b.get('watts_medio'),
                           'duracao_s': round(dur),
                           'motivo': f'bloco de {round(dur)} s; são precisos '
                                     f'{dur_minima}'})
            continue
        ini = b['t0'] + dur * transiente
        meio = ini + (b['t1'] - ini) / 2
        d_total = _declive_por_min(tempo, smo2, ini, b['t1'])
        d_fim = _declive_por_min(tempo, smo2, meio, b['t1'])
        if d_total is None or d_fim is None:
            linhas.append({'watts': b.get('watts_medio'),
                           'duracao_s': round(dur),
                           'motivo': 'poucos pontos de SmO2'})
            continue
        # continua a descer no fim = acima do MLSS
        continua = d_fim <= -estavel
        estabiliza = abs(d_fim) < estavel
        linhas.append({
            'watts': b.get('watts_medio'),
            'duracao_s': round(dur),
            'declive_total': round(d_total, 2),
            'declive_2a_metade': round(d_fim, 2),
            'padrao': ('desce até ao fim' if continua else
                       'estabiliza' if estabiliza else 'sobe'),
            'acima_do_mlss': continua,
        })

    curtos = [x for x in linhas
              if x.get('duracao_s') and x['duracao_s'] < DUR_IDEAL]
    aviso_dur = None
    if curtos:
        med = sorted(x['duracao_s'] for x in curtos)[len(curtos) // 2]
        aviso_dur = (
            f'{len(curtos)} de {len(linhas)} blocos têm menos de '
            f'{DUR_IDEAL} s (mediana {med} s). O critério pede 5 minutos por '
            'carga: com blocos curtos o SmO2 ainda está no transiente de '
            'arranque e não houve tempo de mostrar se assenta. O resultado '
            'sai, mas tende a marcar como "desce até ao fim" cargas que na '
            'verdade estabilizariam se o bloco durasse mais')

    validos = [x for x in linhas if 'padrao' in x and x['watts'] is not None]
    if len(validos) < 2:
        return {'ok': False,
                'motivo': (f'só {len(validos)} blocos com duração e SmO2 '
                           f'suficientes (mínimo {dur_minima} s por bloco)'),
                'blocos': linhas, 'aviso_duracao': aviso_dur}

    # A travessia e' a ULTIMA, nao a primeira.
    #
    # Procurava-se o primeiro bloco que descia ate' ao fim e o ultimo
    # estavel antes dele. Numa sequencia real isso falha: numa sessao com
    # 155(sobe) 173(desce) 193(desce) 213(ESTABILIZA) 229(desce) 251(desce)
    # o metodo antigo dava MLSS=164 W -- mas 213 W estabiliza, e se essa
    # carga e' sustentavel o MLSS nao pode estar 50 W abaixo.
    #
    # O criterio certo e': o ultimo bloco que estabiliza tal que TUDO acima
    # dele desce. Le'-se de cima para baixo, como se le' a olho.
    ultimo_estavel = None
    primeiro_acima = None
    for i in range(len(validos) - 1, -1, -1):
        if validos[i]['acima_do_mlss']:
            continue
        # este estabiliza: tudo acima dele desce?
        if all(x['acima_do_mlss'] for x in validos[i + 1:]) and \
                i + 1 < len(validos):
            ultimo_estavel = validos[i]
            primeiro_acima = validos[i + 1]
            break
    # nenhum estavel com tudo acima a descer: cai no criterio antigo, e
    # avisa que a sequencia nao e' limpa
    sequencia_irregular = False
    if ultimo_estavel is None:
        for x in validos:
            if x['acima_do_mlss']:
                if primeiro_acima is None:
                    primeiro_acima = x
            elif primeiro_acima is None:
                ultimo_estavel = x
        sequencia_irregular = ultimo_estavel is not None

    if primeiro_acima is None:
        return {'ok': False,
                'motivo': ('nenhum bloco desce continuamente até ao fim: o '
                           'MLSS está ACIMA da carga mais alta testada'),
                'blocos': linhas, 'limite_inferior': validos[-1]['watts']}
    if ultimo_estavel is None:
        return {'ok': False,
                'motivo': ('todos os blocos descem até ao fim: o MLSS está '
                           'ABAIXO da carga mais baixa testada'),
                'blocos': linhas, 'limite_superior': validos[0]['watts']}

    a, b2 = ultimo_estavel['watts'], primeiro_acima['watts']
    for x in validos:
        if x['watts'] < a and x['acima_do_mlss']:
            x['contradiz'] = ('desce até ao fim mas está abaixo do MLSS '
                              'estimado')
        elif x['watts'] > b2 and not x['acima_do_mlss']:
            x['contradiz'] = ('estabiliza mas está acima do MLSS estimado')
    return {
        'ok': True,
        'mlss_entre': [round(a, 1), round(b2, 1)],
        'mlss_estimado': round((a + b2) / 2, 1),
        'incerteza': round((b2 - a) / 2, 1),
        'ultimo_estavel': ultimo_estavel,
        'primeiro_acima': primeiro_acima,
        'sequencia_irregular': sequencia_irregular,
        'aviso_sequencia': (
            'nenhum bloco estabiliza com todos os acima a descer: a '
            'sequência alterna. O valor sai da primeira travessia, que é '
            'menos fiável — olha para a coluna do padrão e confirma a olho'
            if sequencia_irregular else None),
        'blocos': linhas,
        'aviso_duracao': aviso_dur,
        'criterio': {'transiente_ignorado_pct': round(transiente * 100),
                     'duracao_ideal_s': DUR_IDEAL,
                     'duracao_minima_s': dur_minima,
                     'estavel_abaixo_de': estavel,
                     'unidade': '% de SmO2 por minuto'},
        'nota': ('o MLSS fica entre o último bloco que estabiliza e o '
                 'primeiro que desce até ao fim. A incerteza é metade do '
                 'intervalo entre eles: degraus mais próximos dão uma '
                 'estimativa mais fina, e é a única forma de a melhorar'),
        'metodo': ('padrão de dessaturação em blocos de carga constante '
                   '(Rogers, muscleoxygentraining.com)'),
    }


# ══════════════════════════════════════════════════════════════════════════
# BREAKPOINT PELA TAXA DE DESSATURACAO
#
# Rogers, sobre o recto femoral numa escada de degraus:
#
#   "The rectus femoris has a gradual desaturation with increasing effort
#    then has an acceleration at the RCP. With the RATE OF CHANGE BETWEEN
#    STAGES showing a shift at high power outputs corresponding to the RCP"
#
# E' o metodo que faltava, e o que serve a este protocolo. Em vez de olhar
# para o SmO2 minimo de cada degrau -- que depende de onde o degrau
# comecou -- olha-se para a VELOCIDADE a que o SmO2 cai dentro de cada
# degrau, e procura-se onde essa velocidade acelera.
#
# Duas vantagens sobre os outros metodos aqui:
#   - funciona com blocos curtos: a taxa mede-se em 2 minutos, o patamar
#     precisa de 5
#   - funciona com poucos degraus: bastam 4 para um breakpoint
# ══════════════════════════════════════════════════════════════════════════

def bp_por_taxa(tempo, smo2, blocos, transiente=TRANSIENTE, min_pontos=2):
    """Breakpoint na taxa de dessaturação (%/min) contra a intensidade."""
    ons = sorted((b for b in blocos if b.get('on')),
                 key=lambda b: b.get('watts_medio') or 0)
    pts = []
    for b in ons:
        w = b.get('watts_medio')
        if w is None:
            continue
        dur = b['t1'] - b['t0']
        ini = b['t0'] + dur * transiente
        d = _declive_por_min(tempo, smo2, ini, b['t1'])
        if d is None:
            continue
        pts.append({'watts': round(w, 1), 'taxa': round(d, 2),
                    'duracao_s': round(dur)})
    if len(pts) < 2 * min_pontos:
        return {'ok': False,
                'motivo': (f'só {len(pts)} degraus com taxa medível; são '
                           f'precisos {2 * min_pontos}'),
                'degraus': pts}

    xs = [p['watts'] for p in pts]
    ys = [p['taxa'] for p in pts]
    d2 = dois_segmentos(xs, ys, min_pontos=min_pontos)
    out = {'degraus': pts, 'n_degraus': len(pts)}
    if not d2.get('ok'):
        return {'ok': False, 'motivo': d2.get('motivo'), **out}

    # DOIS padroes, nao um. Rogers descreve-os no mesmo artigo:
    #
    #   "The RECTUS FEMORIS has a gradual desaturation with increasing
    #    effort then has an ACCELERATION at the RCP."
    #
    #   "The VASTUS LATERALIS progressively de-saturates (almost in a
    #    linear fashion), until there is a point where it PLATEAUS with
    #    no further change."
    #
    # Eu so' aceitava o primeiro. O segundo -- a taxa aumenta e depois
    # estabiliza -- e' igualmente um breakpoint, e e' o padrao que aparece
    # nas sessoes deste atleta. Rejeita-lo era descartar o resultado certo
    # por ter a forma do outro musculo.
    m1, m2 = d2['declive_1'], d2['declive_2']
    acelera = m2 < m1
    # patamar: o segundo troco fica quase plano depois de o primeiro
    # descer. "Quase plano" e' relativo ao primeiro declive, nao absoluto.
    plateia = (m1 < 0 and abs(m2) < abs(m1) * 0.35)
    padrao = ('aceleração (tipo recto femoral)' if acelera else
              'patamar (tipo vasto lateral)' if plateia else None)
    p0 = _fit(xs, ys, 0, len(xs))
    f_stat = None
    if p0:
        rss0 = _rss(xs, ys, p0[0], p0[1], 0, len(xs))
        gl = len(xs) - 4
        if gl > 0 and d2['rss'] > 0:
            f_stat = round(((rss0 - d2['rss']) / 2) / (d2['rss'] / gl), 2)

    critico = 4.0 if len(xs) < 10 else 3.0
    ok = padrao is not None and (f_stat is None or f_stat >= critico)
    return {
        'ok': ok,
        'bp_watts': d2['tau'],
        'taxa_no_bp': d2['y_no_tau'],
        'taxa_antes': d2['declive_1'],
        'taxa_depois': d2['declive_2'],
        'acelera': acelera,
        'plateia': plateia,
        'padrao': padrao,
        'f_vs_recta': f_stat,
        'ganho_sobre_recta': d2['ganho_sobre_recta'],
        'motivo': (None if ok else
                   ('a taxa nem acelera nem estabiliza: continua a mudar ao '
                    'mesmo ritmo, sem quebra' if padrao is None else
                    f'a quebra não se distingue de uma recta (F={f_stat}, '
                    f'abaixo de {critico})')),
        **out,
        'metodo': 'quebra na taxa de dessaturação por degrau (Rogers)',
        'nota': ('a taxa mede-se depois do transiente de arranque, em % de '
                 'SmO2 por minuto. Ao contrário do mínimo de cada degrau, '
                 'não depende do ponto de partida — e por isso funciona com '
                 'blocos curtos e com poucos degraus'),
        'nota_padrao': (
            'dois padrões são válidos: ACELERAÇÃO, típica do recto femoral, '
            'em que a queda se agrava acima do ponto; e PATAMAR, típico do '
            'vasto lateral, em que a queda deixa de se agravar porque a '
            'extracção chegou ao limite. O músculo onde tens o sensor '
            'determina qual esperas ver'),
    }


# ══════════════════════════════════════════════════════════════════════════
# METODO DE ARNOLD — o canonico para o protocolo 5-1
#
# Jem Arnold, sparecycles.blog, no MESMO protocolo de degraus com pausa:
#
#   "The mean of the LAST MINUTE of each workload is taken as a single
#    data point for each stage."
#
# Nao o minimo do bloco nem a taxa de queda: a media do ULTIMO MINUTO. E'
# o SmO2 de estado estavel naquela carga, quando a entrega e o consumo ja'
# se equilibraram. Foi o metodo que ele usou no estudo publicado.
#
# E ha DOIS PERFIS de resposta, que mudam a interpretacao toda:
#
#   PARABOLICO  o SmO2 SOBE nas cargas baixas ate' um maximo e so' depois
#               desce. O topo da parabola aproxima o FatMax / LT1.
#               Tipico de: menos treinado, prega cutanea mais espessa,
#               musculo menor, fenotipo mais oxidativo.
#
#   MONOTONICO  o SmO2 desce sempre. Nao ha topo, e o SmO2max e' o
#               primeiro degrau. Tipico de: mais treinado, mais magro,
#               musculo maior, fenotipo mais glicolitico.
#
# Isto e' decisivo, e nas palavras dele:
#
#   "an SmO2 signal that may be interpreted as associated with, say LT1
#    in one response profile, may not correspond to the same intensity --
#    OR MAY NOT EXIST AT ALL -- in the other response profile."
#
# Ou seja: num atleta monotonico, procurar o primeiro limiar no SmO2 e'
# procurar uma coisa que nao esta la'.
#
# QUAL LIMIAR E' QUAL
#   SmO2max (topo da parabola)  ~  FatMax / LT1 / VT1   -> PRIMEIRO
#   deoxy-BP (quebra na queda)  ~  RCP / VT2 / MLSS     -> SEGUNDO
#
# E o aviso dele sobre a concordancia individual:
#   "at an individual level this association broke down (...) the
#    variability was anywhere within +/- ~100 W"
# ══════════════════════════════════════════════════════════════════════════

CAUDA_S = 60          # ultimo minuto de cada degrau
SUBIDA_MINIMA = 1.5   # % de SmO2 para contar como subida real


def perfil_de_resposta(tempo, smo2, blocos, cauda=CAUDA_S,
                       subida_minima=SUBIDA_MINIMA):
    """Perfil parabólico ou monotónico, pelo método de Arnold."""
    ons = sorted((b for b in blocos if b.get('on')),
                 key=lambda b: b.get('watts_medio') or 0)
    degraus = []
    for b in ons:
        w = b.get('watts_medio')
        if w is None:
            continue
        t0 = max(b['t0'], b['t1'] - cauda)
        vs = [smo2[i] for i in range(min(len(tempo), len(smo2)))
              if t0 <= tempo[i] <= b['t1'] and smo2[i] is not None]
        if not vs:
            continue
        degraus.append({'watts': round(w, 1),
                        'smo2_fim': round(sum(vs) / len(vs), 1),
                        'n_amostras': len(vs),
                        'duracao_s': round(b['t1'] - b['t0'])})
    if len(degraus) < 3:
        return {'ok': False,
                'motivo': f'só {len(degraus)} degraus com SmO2 no último minuto'}

    ys = [d['smo2_fim'] for d in degraus]
    i_max = ys.index(max(ys))
    subida = ys[i_max] - ys[0]

    # parabolico: o maximo NAO esta no primeiro degrau e a subida ate' la'
    # e' real, nao ruido
    parabolico = i_max > 0 and subida >= subida_minima
    perfil = 'parabólico' if parabolico else 'monotónico'

    out = {
        'ok': True,
        'perfil': perfil,
        'degraus': degraus,
        'smo2max': ys[i_max],
        'smo2max_watts': degraus[i_max]['watts'],
        'smo2min': min(ys),
        'smo2min_watts': degraus[ys.index(min(ys))]['watts'],
        'amplitude': round(max(ys) - min(ys), 1),
        'subida_ate_ao_max': round(subida, 1),
        'cauda_s': cauda,
        'metodo': ('média do último minuto de cada degrau '
                   '(Arnold, sparecycles.blog)'),
    }
    if parabolico:
        out['bp1_watts'] = degraus[i_max]['watts']
        out['bp1_leitura'] = (
            f"o SmO2 sobe até {degraus[i_max]['watts']} W e só depois desce. "
            'O topo da parábola aproxima o FatMax e o LT1 — o PRIMEIRO '
            'limiar. Arnold avisa que a associação existe mas não está '
            'robustamente validada')
        out['fenotipo'] = (
            'perfil parabólico associa-se a menos treino, prega cutânea mais '
            'espessa sobre o músculo, massa muscular menor e fenótipo mais '
            'oxidativo. Não é um julgamento: é o que muda o sinal ótico')
    else:
        out['bp1_watts'] = None
        out['bp1_leitura'] = (
            'perfil monotónico: o SmO2 desce desde o primeiro degrau, sem '
            'topo. Neste perfil o primeiro limiar NÃO É OBSERVÁVEL no SmO2 — '
            'Arnold é explícito que o sinal associado ao LT1 "may not exist '
            'at all" neste perfil. Procurá-lo aqui é procurar o que não está')
        out['fenotipo'] = (
            'perfil monotónico associa-se a mais treino, menor prega cutânea, '
            'massa muscular maior e fenótipo mais glicolítico')
    return out


def fc_na_carga(blocos, tempo, hr, watts_alvo, tolerancia=25):
    """FC média do bloco de trabalho mais próximo de uma dada carga.

    Interpola entre os dois blocos vizinhos quando o alvo cai entre eles;
    sem isso, um BP a meio caminho entre dois degraus herdava a FC de um
    deles e a comparacao entre sessoes ficava com degraus a mais.
    """
    if watts_alvo is None or not hr:
        return None
    pts = []
    for b in blocos:
        if not b.get('on') or b.get('watts_medio') is None:
            continue
        vs = [hr[i] for i in range(min(len(tempo), len(hr)))
              if b['t0'] <= tempo[i] <= b['t1'] and hr[i] is not None]
        if vs:
            pts.append((b['watts_medio'], sum(vs) / len(vs)))
    if not pts:
        return None
    pts.sort()
    abaixo = [p for p in pts if p[0] <= watts_alvo]
    acima = [p for p in pts if p[0] > watts_alvo]
    if abaixo and acima:
        (w1, h1), (w2, h2) = abaixo[-1], acima[0]
        f = (watts_alvo - w1) / (w2 - w1) if w2 > w1 else 0
        return round(h1 + (h2 - h1) * f)
    perto = min(pts, key=lambda p: abs(p[0] - watts_alvo))
    if abs(perto[0] - watts_alvo) > tolerancia:
        return None
    return round(perto[1])


# Como se le' cada forma de curva. E' o que o Arnold descreve, posto em
# linguagem de prescricao -- e com o que cada forma NAO permite concluir,
# que e' a parte que costuma faltar.
LEITURA_DO_PERFIL = {
    'parabólico': {
        'o_que_mostra': ('a entrega de oxigénio sobe mais depressa que o '
                         'consumo nas cargas baixas, e só acima do topo da '
                         'parábola é que o consumo passa à frente'),
        'o_que_permite': ('o topo da parábola dá um candidato a FatMax / LT1 '
                          '— o primeiro limiar é observável neste perfil'),
        'o_que_nao_permite': ('o SmO2 mínimo só é atingido à exaustão, por '
                              'isso não serve para marcar o topo do domínio '
                              'severo'),
        'prescricao': ('treino de base na zona onde o SmO2 se mantém perto '
                       'do máximo: é onde o fluxo e a entrega de substrato '
                       'estão altos'),
    },
    'monotónico': {
        'o_que_mostra': ('o consumo excede a entrega desde a primeira carga: '
                         'não há zona em que a oxigenação melhore com o '
                         'esforço'),
        'o_que_permite': ('o SmO2 aproxima-se do mínimo fisiológico pouco '
                          'acima do CP, o que dá um alvo para intervalos '
                          'longos no domínio severo'),
        'o_que_nao_permite': ('o primeiro limiar NÃO é observável: procurar '
                              'um LT1 no SmO2 deste perfil é procurar o que '
                              'não existe. Usar potência, FC e sensação'),
        'prescricao': ('intervalos que cheguem ao SmO2 quase mínimo, com '
                       'duração longa em vez de intensidade alta — a '
                       'intensidade que lá chega não é muito acima do CP'),
    },
}


# ══════════════════════════════════════════════════════════════════════════
# METODO DO SCRIPT OFICIAL DA MOXY (MoxyBreakPoint v0.8)
#
# Adaptado do script que a Moxy disponibiliza para a Intervals.icu. O que
# ele faz e que os outros metodos aqui nao faziam:
#
#   1. SmO2 = MEDIA de cada intervalo WORK (average_smo2), nao o minimo
#      nem a media do ultimo minuto.
#   2. Ordena por potencia.
#   3. INTERPOLA 10 pontos entre cada par consecutivo.
#   4. Regressao por trocos com DOIS breakpoints.
#
# O passo 3 e' o que faltava. Com 6 degraus eu ajustava tres trocos em 6
# pontos e nao dava; interpolados, sao 51 pontos e o ajuste corre, com o
# breakpoint a cair numa grelha fina em vez de so' nos degraus medidos.
#
# UMA CORRECCAO AO SCRIPT DELES
#
# Interpolar nao cria informacao. Os 51 pontos sao 6 medicoes e 45 valores
# calculados por recta entre elas. O script deles nao faz teste de
# significancia, o que evita o problema; mas se se fizer sobre os pontos
# interpolados, o n esta inflacionado 10x e o F da' significativo quase
# sempre -- seria matematica, nao fisiologia.
#
# Aqui: a interpolacao LOCALIZA o breakpoint na grelha fina, e o teste de
# significancia corre sobre os pontos ORIGINAIS.
# ══════════════════════════════════════════════════════════════════════════

N_FINO = 10


def _interpolar(xs, ys, n_fino=N_FINO):
    fx, fy = [], []
    for i in range(len(xs) - 1):
        for j in range(n_fino):
            f = j / n_fino
            fx.append(xs[i] + (xs[i + 1] - xs[i]) * f)
            fy.append(ys[i] + (ys[i + 1] - ys[i]) * f)
    fx.append(xs[-1])
    fy.append(ys[-1])
    return fx, fy


def bp_moxy(blocos, tempo=None, smo2=None, hr=None, n_fino=N_FINO,
            modalidade=None, degraus_por_troco=2):
    """BP1 e BP2 pelo método do script oficial da Moxy, com teste F honesto.

    blocos: lista com watts_medio e, ou smo2_medio já calculado, ou t0/t1
    para o extrair dos streams.
    """
    pts = []
    for b in blocos:
        if not b.get('on'):
            continue
        w = b.get('watts_medio')
        if w is None:
            continue
        s = b.get('smo2_medio')
        if s is None and tempo and smo2:
            vs = [smo2[i] for i in range(min(len(tempo), len(smo2)))
                  if b['t0'] <= tempo[i] <= b['t1'] and smo2[i] is not None]
            s = sum(vs) / len(vs) if vs else None
        if s is None:
            continue
        h = b.get('hr_medio')
        if h is None and tempo and hr:
            vs = [hr[i] for i in range(min(len(tempo), len(hr)))
                  if b['t0'] <= tempo[i] <= b['t1'] and hr[i] is not None]
            h = sum(vs) / len(vs) if vs else None
        pts.append({'watts': float(w), 'smo2': float(s),
                    'hr': round(h) if h is not None else None})

    if len(pts) < 3:
        return {'ok': False,
                'motivo': (f'só {len(pts)} intervalos de trabalho com SmO2; '
                           'o método precisa de 3'),
                'n_intervalos': len(pts)}

    pts.sort(key=lambda p: p['watts'])
    xs = [p['watts'] for p in pts]
    ys = [p['smo2'] for p in pts]

    fx, fy = _interpolar(xs, ys, n_fino)
    # min_pontos na grelha fina = 2 degraus MEDIDOS de cada lado.
    #
    # Usar 5 (meio degrau) parecia razoavel mas empurrava os breakpoints:
    # numa curva com quebras verdadeiras em 195 e 240 W, davam 227 e 241 --
    # o primeiro troco ficava com pontos a menos e o ajuste compensava
    # deslocando a quebra. O minimo tem de contar degraus reais, nao
    # pontos interpolados.
    # degraus_por_troco=2 e' o meu criterio; =1 reproduz o script deles,
    # que nao impoe minimo e por isso deixa o breakpoint cair entre dois
    # degraus quaisquer. Com 5 degraus a diferenca e' grande: 209 W com o
    # meu criterio, 197 W com o deles.
    min_f = max(2, int(degraus_por_troco * n_fino))
    tres = tres_segmentos(fx, fy, min_pontos=min_f)
    if not tres.get('ok'):
        d2 = dois_segmentos(fx, fy, min_pontos=min_f)
        if not d2.get('ok'):
            return {'ok': False, 'motivo': d2.get('motivo'),
                    'pontos': pts, 'n_intervalos': len(pts)}
        tres = {'ok': True, 'bp1': d2['tau'], 'bp2': None,
                'smo2_bp1': d2['y_no_tau'], 'smo2_bp2': None,
                'declives': [d2['declive_1'], d2['declive_2']],
                'so_um': True}

    # ── significancia nos pontos ORIGINAIS ────────────────────────────
    # E' aqui que este difere do script da Moxy: eles nao testam, e testar
    # nos interpolados daria significativo sempre.
    # RSS do modelo segmentado nos pontos ORIGINAIS. Reconstroi-se a
    # funcao continua a partir dos breakpoints e reavalia-se nos x medidos.
    # A versao anterior tentava fazer isto a mao e enganava-se no ponto de
    # ancoragem de cada troco, o que dava RSS errado e F impossivel.
    bps = sorted(b for b in (tres.get('bp1'), tres.get('bp2'))
                 if b is not None)
    d = tres.get('declives') or []

    def _modelo(x):
        # reconstroi por integracao dos declives, partindo do primeiro
        # ponto ajustado
        y = fy[0]
        ant = fx[0]
        for k, m in enumerate(d):
            fim = bps[k] if k < len(bps) else x
            if x <= fim:
                return y + m * (x - ant)
            y += m * (fim - ant)
            ant = fim
        return y + d[-1] * (x - ant)

    p0 = _fit(xs, ys, 0, len(xs))
    f_stat = p_val = None
    if p0:
        rss0 = _rss(xs, ys, p0[0], p0[1], 0, len(xs))
        rss1 = sum((_modelo(x) - y) ** 2 for x, y in zip(xs, ys))
        k_extra = len(bps) * 2
        gl = len(xs) - (2 + k_extra)
        if gl > 0 and rss1 > 0:
            f_stat = round(((rss0 - rss1) / k_extra) / (rss1 / gl), 2)
            try:
                from scipy.stats import f as _fd
                p_val = round(float(1 - _fd.cdf(f_stat, k_extra, gl)), 4)
            except ImportError:
                p_val = None
        elif gl <= 0:
            f_stat = None
            p_val = None

    out = {
        'ok': True,
        'bp1_w': round(tres['bp1'], 1) if tres.get('bp1') is not None else None,
        'bp2_w': round(tres['bp2'], 1) if tres.get('bp2') is not None else None,
        'smo2_bp1': tres.get('smo2_bp1'),
        'smo2_bp2': tres.get('smo2_bp2'),
        'declives': tres.get('declives'),
        'n_intervalos': len(pts),
        'n_pontos_interpolados': len(fx),
        'pontos': pts,
        'f_vs_recta': f_stat,
        'p_vs_recta': p_val,
        'metodo': ('MoxyBreakPoint v0.8 adaptado: média de SmO2 por '
                   'intervalo, interpolação 10x, regressão por troços'),
        'degraus_por_troco': degraus_por_troco,
        'nota_interpolacao': (
            f'{len(fx)} pontos ajustados vêm de {len(pts)} medições. A '
            'interpolação serve para o breakpoint cair numa grelha fina, '
            'não para acrescentar informação — por isso o teste F corre '
            f'sobre os {len(pts)} pontos originais, não sobre os '
            f'{len(fx)} interpolados'),
    }
    out['bp1_bpm'] = _hr_interp(pts, out['bp1_w'])
    out['bp2_bpm'] = _hr_interp(pts, out['bp2_w'])

    critico = 4.0 if len(xs) < 10 else 3.0
    if f_stat is None:
        # graus de liberdade a menos: com 2 breakpoints sao 6 parametros,
        # e com 6 degraus sobram zero. Cai-se para um breakpoint so'.
        out['aviso_gl'] = (
            f'{len(xs)} degraus não chegam para testar dois breakpoints '
            '(6 parâmetros, 0 graus de liberdade). O BP2 sai do ajuste mas '
            'não é testável — são precisos 8 degraus para o testar')
    elif f_stat < critico:
        out['ok'] = False
        out['motivo'] = (f'a quebra não se distingue de uma recta nos pontos '
                         f'medidos (F={f_stat}, abaixo de {critico}). O '
                         'script original não faz este teste, por isso '
                         'devolveria estes breakpoints à mesma')
    f = dict(FIABILIDADE.get(modalidade or '', {}) or {'nivel': 'desconhecida'})
    f['n_degraus'] = len(pts)
    out['fiabilidade'] = f
    return out


def _hr_interp(pts, alvo):
    """FC interpolada na carga alvo, a partir dos pontos por degrau."""
    if alvo is None:
        return None
    com = [(p['watts'], p['hr']) for p in pts if p.get('hr') is not None]
    if not com:
        return None
    com.sort()
    ab = [p for p in com if p[0] <= alvo]
    ac = [p for p in com if p[0] > alvo]
    if ab and ac:
        (w1, h1), (w2, h2) = ab[-1], ac[0]
        f = (alvo - w1) / (w2 - w1) if w2 > w1 else 0
        return round(h1 + (h2 - h1) * f)
    return round(min(com, key=lambda p: abs(p[0] - alvo))[1])


# ══════════════════════════════════════════════════════════════════════════
# COERENCIA DO BP1 E BP2 COM AS OUTRAS METRICAS
#
# Um breakpoint sozinho nao diz se e' de confianca. Confronta-se com o que
# ja' sabemos do atleta por outras vias -- CP, MLSS, campos da
# Intervals.icu -- e com o que a fisiologia obriga: BP1 abaixo de BP2,
# ambos abaixo do Pvo2max, e cada um dentro da gama que a literatura
# reporta para a sua fraccao do CP.
#
# As fraccoes vem dos estudos que confrontaram breakpoints de SmO2 com
# limiares medidos. Nao sao normas de populacao para prescrever: sao
# limites de plausibilidade para levantar a bandeira quando um numero cai
# fora do que qualquer atleta apresentaria.
FRACCOES_DO_CP = {
    'bp1': (0.55, 0.85),   # LT1/VT1 ronda 60-80% do CP
    'bp2': (0.85, 1.10),   # LT2/VT2/RCP ronda perto do CP
}


def coerencia(bp1_w=None, bp2_w=None, cp=None, mlss=None, pvo2max=None,
              lt1_campos=None, lt2_campos=None, zonas=None):
    """Verifica se os breakpoints batem certo com o resto do perfil."""
    testes, avisos = [], []

    def _t(nome, ok, detalhe):
        testes.append({'teste': nome, 'ok': ok, 'detalhe': detalhe})
        if not ok:
            avisos.append(detalhe)

    # ── ordem fisiologica ────────────────────────────────────────────
    if bp1_w is not None and bp2_w is not None:
        _t('BP1 abaixo do BP2', bp1_w < bp2_w,
           (f'BP1 {round(bp1_w)} W e BP2 {round(bp2_w)} W: '
            + ('ordem correcta' if bp1_w < bp2_w else
               'INVERTIDOS. O primeiro limiar não pode estar acima do '
               'segundo — os métodos encontraram a mesma quebra duas vezes, '
               'ou uma delas é ruído')))
        sep = (bp2_w - bp1_w) / bp1_w * 100 if bp1_w else None
        if sep is not None:
            _t('separação entre limiares', 5 <= sep <= 60,
               (f'{round(sep)}% entre BP1 e BP2. '
                + ('plausível' if 5 <= sep <= 60 else
                   'menos de 5%: os dois breakpoints estão praticamente no '
                   'mesmo sítio, provavelmente é a mesma quebra'
                   if sep < 5 else
                   'mais de 60%: separação grande demais, um dos dois pode '
                   'não ser um limiar')))

    if pvo2max is not None:
        for nome, v in (('BP1', bp1_w), ('BP2', bp2_w)):
            if v is None:
                continue
            _t(f'{nome} abaixo do Pvo2max', v < pvo2max,
               (f'{nome} {round(v)} W vs Pvo2max {round(pvo2max)} W: '
                + ('abaixo, como tem de ser' if v < pvo2max else
                   'ACIMA do Pvo2max, o que é impossível — um limiar '
                   'sustentável não pode exceder a potência máxima aeróbia')))

    # ── fraccao do CP ────────────────────────────────────────────────
    if cp:
        for chave, v in (('bp1', bp1_w), ('bp2', bp2_w)):
            if v is None:
                continue
            lo, hi = FRACCOES_DO_CP[chave]
            f = v / cp
            _t(f'{chave.upper()} como fracção do CP', lo <= f <= hi,
               (f'{chave.upper()} está a {round(f * 100)}% do CP '
                f'({round(cp)} W). Esperado {round(lo * 100)}–'
                f'{round(hi * 100)}%'
                + ('' if lo <= f <= hi else
                   '. Fora da gama que a literatura reporta — não invalida '
                   'o valor, mas obriga a confirmar antes de o usar')))

    # ── concordancia com os campos medidos ───────────────────────────
    for nome, v, campos in (('BP1', bp1_w, lt1_campos),
                            ('BP2', bp2_w, lt2_campos)):
        if v is None or not campos:
            continue
        vs = [x for x in campos if x is not None]
        if not vs:
            continue
        lo, hi = min(vs), max(vs)
        med = sorted(vs)[len(vs) // 2]
        dif = (v - med) / med * 100 if med else None
        dentro = lo * 0.85 <= v <= hi * 1.15
        _t(f'{nome} concorda com os campos',
           dentro,
           (f'{nome} {round(v)} W vs campos {round(lo)}–{round(hi)} W '
            f'(mediana {round(med)})'
            + (f', {"+" if dif > 0 else ""}{round(dif)}%' if dif else '')
            + ('. Dentro do intervalo dos campos' if dentro else
               '. FORA do intervalo dos campos por mais de 15% — duas vias '
               'independentes a discordar assim é sinal de que uma delas '
               'está a medir outra coisa')))

    if mlss and bp2_w:
        d = abs(bp2_w - mlss) / mlss * 100
        _t('BP2 concorda com o MLSS', d <= 15,
           (f'BP2 {round(bp2_w)} W vs MLSS {round(mlss)} W: {round(d)}% de '
            'diferença'
            + ('. Concordam' if d <= 15 else
               '. Discordam. São dois caminhos para o mesmo limiar, e uma '
               'diferença desta ordem significa que pelo menos um deles '
               'não está bem determinado')))

    # ── zona em que cada breakpoint cai ──────────────────────────────
    zona_de = {}
    if zonas:
        for nome, v in (('BP1', bp1_w), ('BP2', bp2_w)):
            if v is None:
                continue
            z = next((z for z in zonas
                      if z.get('de_w') is not None
                      and z['de_w'] <= v < (z.get('ate_w') or 1e9)), None)
            if z:
                zona_de[nome] = {'zona': z.get('zona'),
                                 'de_w': z.get('de_w'), 'ate_w': z.get('ate_w')}

    n_ok = sum(1 for t in testes if t['ok'])
    return {
        'ok': True,
        'n_testes': len(testes), 'n_passou': n_ok,
        'testes': testes, 'avisos': avisos,
        'zona_de': zona_de,
        'veredicto': (
            'coerente com o resto do perfil' if not avisos else
            f'{len(avisos)} incoerência(s): usar com reserva'),
        'nota': ('as fracções do CP são limites de plausibilidade tirados da '
                 'literatura, não normas para prescrever. Servem para '
                 'levantar a bandeira quando um número cai fora do que '
                 'qualquer atleta apresentaria — não para dizer que o '
                 'atleta devia estar noutro sítio'),
    }


# ══════════════════════════════════════════════════════════════════════════
# PRIMEIRO LIMIAR PELA REOXIGENACAO (Yogev)
#
# Assaf Yogev, no MESMO protocolo que este atleta faz -- incremental com
# 1 min de recuperacao entre degraus. Confirmado tambem pelas figuras da
# apresentacao (video 22:37), onde se veem os tres padroes lado a lado:
#
#   BAIXA INTENSIDADE   o SmO2 cai no arranque e depois SOBE dentro do
#                       proprio bloco -- reoxigenacao. Na figura (a), a
#                       linha azul desce e recupera durante o esforco.
#
#   MEDIA               cai e ESTABILIZA. Na figura (b) a linha assenta e
#                       fica plana. "supply and demand being matched".
#
#   ALTA                cai CONTINUAMENTE ate' ao fim. Figura (c), queda
#                       sustentada sem recuperacao.
#
# E o criterio, nas palavras dele:
#
#   "after they reach the peak of this reoxygenation response, we see a
#    plateau in the signal (...) THIS TRANSITION is what we're looking for
#    when we're trying to set the FIRST AEROBIC THRESHOLD"
#
# Ou seja: o primeiro limiar esta' entre o ULTIMO degrau que reoxigena e o
# PRIMEIRO que apenas estabiliza.
#
# PORQUE E' QUE ISTO IMPORTA AQUI
#
# O mlss_por_dessaturacao ja' distingue "estabiliza" de "desce ate' ao
# fim" -- e' o SEGUNDO limiar. O "sobe" era tratado como um caso a parte e
# nao era usado para nada. Mas e' precisamente o sinal do PRIMEIRO limiar,
# que e' onde este atleta tem menos medicoes independentes.
#
# Nos dados dele (Row, 2026-01-16):
#   155 W  declive da 2a metade  +5.35  -> reoxigena
#   173 W  declive da 2a metade  -1.35  -> ja' nao
# Logo o primeiro limiar fica entre 155 e 173 W.
# ══════════════════════════════════════════════════════════════════════════

# Declive, em % de SmO2 por minuto, acima do qual se considera que o sinal
# REOXIGENA dentro do bloco. Usa-se o mesmo valor de "estavel" mas com o
# sinal ao contrario: acima de +0.5 sobe, abaixo de -0.5 desce, entre os
# dois esta' estavel.
REOX_MINIMA = 0.5


def lt1_por_reoxigenacao(tempo, smo2, blocos, transiente=TRANSIENTE,
                         reox_minima=REOX_MINIMA, dur_minima=DUR_MINIMA):
    """Primeiro limiar: onde a reoxigenação dentro do bloco deixa de ocorrer."""
    ons = sorted((b for b in blocos if b.get('on')),
                 key=lambda b: b.get('watts_medio') or 0)
    linhas = []
    for b in ons:
        dur = b['t1'] - b['t0']
        w = b.get('watts_medio')
        if dur < dur_minima or w is None:
            linhas.append({'watts': w, 'duracao_s': round(dur),
                           'motivo': f'bloco de {round(dur)} s'})
            continue
        ini = b['t0'] + dur * transiente
        meio = ini + (b['t1'] - ini) / 2
        d_fim = _declive_por_min(tempo, smo2, meio, b['t1'])
        if d_fim is None:
            linhas.append({'watts': w, 'duracao_s': round(dur),
                           'motivo': 'poucos pontos'})
            continue
        if d_fim >= reox_minima:
            padrao, fase = 'reoxigena', 'baixa'
        elif d_fim <= -reox_minima:
            padrao, fase = 'desce até ao fim', 'alta'
        else:
            padrao, fase = 'estabiliza', 'média'
        linhas.append({'watts': round(w, 1), 'duracao_s': round(dur),
                       'declive_2a_metade': round(d_fim, 2),
                       'padrao': padrao, 'dominio': fase,
                       'reoxigena': padrao == 'reoxigena'})

    validos = [x for x in linhas if 'padrao' in x]
    if len(validos) < 2:
        return {'ok': False,
                'motivo': f'só {len(validos)} blocos utilizáveis',
                'blocos': linhas}

    # ultimo que reoxigena, com nenhum a reoxigenar acima dele
    ultimo_reox = primeiro_sem = None
    for i in range(len(validos) - 1, -1, -1):
        if not validos[i]['reoxigena']:
            continue
        if not any(x['reoxigena'] for x in validos[i + 1:]) and \
                i + 1 < len(validos):
            ultimo_reox, primeiro_sem = validos[i], validos[i + 1]
            break

    if ultimo_reox is None:
        if validos[0]['reoxigena']:
            return {'ok': False,
                    'motivo': ('todos os blocos reoxigenam: o primeiro '
                               'limiar está ACIMA da carga mais alta '
                               'testada'),
                    'limite_inferior': validos[-1]['watts'],
                    'blocos': linhas}
        return {'ok': False,
                'motivo': ('nenhum bloco reoxigena: o primeiro limiar está '
                           'ABAIXO da carga mais baixa testada, ou a sessão '
                           'começou já aquecida'),
                'limite_superior': validos[0]['watts'],
                'blocos': linhas}

    a, b2 = ultimo_reox['watts'], primeiro_sem['watts']
    return {
        'ok': True,
        'lt1_entre': [round(a, 1), round(b2, 1)],
        'lt1_estimado': round((a + b2) / 2, 1),
        'incerteza': round((b2 - a) / 2, 1),
        'ultimo_a_reoxigenar': ultimo_reox,
        'primeiro_sem_reoxigenar': primeiro_sem,
        'blocos': linhas,
        'criterio': {'reox_acima_de': reox_minima,
                     'transiente_ignorado_pct': round(transiente * 100),
                     'unidade': '% de SmO2 por minuto na 2.ª metade'},
        'metodo': 'transição da reoxigenação (Yogev)',
        'leitura': (
            f'até {round(a)} W o SmO2 ainda SOBE dentro do bloco depois da '
            f'queda inicial — a entrega supera o consumo. A partir de '
            f'{round(b2)} W deixa de subir. É essa transição que marca o '
            'primeiro limiar aeróbio'),
        'nota': ('mede uma coisa diferente do MLSS: este é onde a '
                 'reoxigenação DENTRO do bloco desaparece; o MLSS é onde a '
                 'queda deixa de estabilizar. São o primeiro e o segundo '
                 'limiar, pela mesma via'),
        'independente': (
            'vem do sensor óptico, não da curva de potência. É uma medição '
            'independente do modelo — ao contrário do AeT'),
    }
