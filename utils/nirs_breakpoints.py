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


def dois_segmentos(xs, ys, min_pontos=3):
    """Ajuste continuo de dois trocos; devolve o tau e os dois declives.

    Continuo: o segundo troco arranca onde o primeiro acaba, em vez de
    serem duas rectas independentes que se cruzam onde calha. E' a forma
    que a revisao descreve e a que faz sentido fisico -- o sinal nao salta
    no limiar, muda de inclinacao.
    """
    n = len(xs)
    if n < 2 * min_pontos:
        return {'ok': False, 'motivo': f'só {n} pontos'}
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


def tres_segmentos(xs, ys, min_pontos=3):
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
            r = (_rss(xs, ys, m1, b1, 0, k1)
                 + _rss(xs, ys, m2, b2, k1, k2)
                 + _rss(xs, ys, m3, b3, k2, n))
            if melhor is None or r < melhor[0]:
                melhor = (r, tau1, tau2, m1, m2, m3, k1, k2, y1, y2)
    if melhor is None:
        return {'ok': False, 'motivo': 'nenhuma divisão ajustável'}
    r, tau1, tau2, m1, m2, m3, k1, k2, y1, y2 = melhor
    p0 = _fit(xs, ys, 0, n)
    rss0 = _rss(xs, ys, p0[0], p0[1], 0, n) if p0 else None
    return {'ok': True,
            'bp1': round(tau1, 1), 'bp2': round(tau2, 1),
            'smo2_bp1': round(y1, 1), 'smo2_bp2': round(y2, 1),
            'declives': [round(m1, 5), round(m2, 5), round(m3, 5)],
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
    if len(pts) < 6:
        # A fiabilidade era preenchida so' no fim, e esta saida antecipada
        # devolvia "0 degraus" quando havia 5. Preenche-se aqui tambem.
        f0 = dict(FIABILIDADE.get(modalidade or '', {}) or
                  {'nivel': 'desconhecida'})
        f0['n_degraus'] = len(pts)
        f0['aviso_n'] = (f'{len(pts)} degraus: são precisos 6 para dois '
                         'breakpoints e 9 para o ajuste de três troços')
        return {'ok': False,
                'motivo': (f'só {len(pts)} blocos com potência e SmO2; '
                           'são precisos 6 para dois breakpoints e 9 para '
                           'o ajuste de três troços'),
                'n_blocos': len(pts), 'fiabilidade': f0,
                'pontos': [{'watts': round(x, 1), 'smo2': round(y, 1)}
                           for x, y in pts]}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    # tres trocos primeiro: e' o que corresponde as tres fases descritas
    # na literatura. Se nao houver pontos para tres, cai em dois.
    tres = tres_segmentos(xs, ys)
    out = {'pontos': [{'watts': round(x, 1), 'smo2': round(y, 1)}
                      for x, y in pts]}
    if tres.get('ok'):
        out['ok'] = True
        out['metodo'] = 'três segmentos contínuos'
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
        return {'ok': False, 'motivo': 'ajuste impossível'}
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

    ultimo_estavel = None
    primeiro_acima = None
    for x in validos:
        if x['acima_do_mlss']:
            if primeiro_acima is None:
                primeiro_acima = x
        elif primeiro_acima is None:
            ultimo_estavel = x

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
    return {
        'ok': True,
        'mlss_entre': [round(a, 1), round(b2, 1)],
        'mlss_estimado': round((a + b2) / 2, 1),
        'incerteza': round((b2 - a) / 2, 1),
        'ultimo_estavel': ultimo_estavel,
        'primeiro_acima': primeiro_acima,
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
