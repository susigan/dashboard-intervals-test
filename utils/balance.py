"""utils/balance.py — quanto da reserva já foi gasta, a cada momento.

DUAS RESERVAS, DUAS COBERTURAS DIFERENTES

  W′ balance   precisa só de POTÊNCIA, CP e W′.
               Funciona em TODAS as sessões — as 717.

  M′ balance   precisa de SmO2. É o análogo do W′ feito com a taxa de
               dessaturação em vez da potência.
               Só nas sessões com Moxy — 3 no Bike, 5 no Row, 5 no Ski.

Não há como calcular o M′ sem SmO2: é a definição dele.

QUAL MODELO, E PORQUÊ ESTE

Há duas famílias para o W′ balance:

  INTEGRAL (Skiba 2012) — a recuperação segue uma exponencial com uma
  constante de tempo τ que depende da diferença entre CP e a potência de
  recuperação. Ajusta melhor os dados originais, mas o τ tem de ser
  calibrado com testes específicos que este atleta não fez. Usar o τ da
  publicação seria importar a fisiologia de outra pessoa.

  DIFERENCIAL (Froncioni–Clarke–Skiba) — a recuperação é proporcional ao
  que falta encher:

      acima do CP:   dW′/dt = −(P − CP)
      abaixo do CP:  dW′/dt = +(W′ − W′bal) · (CP − P) / W′

  Não tem parâmetro livre. É o que se usa aqui, por isso: um modelo sem
  constante emprestada vale mais do que um modelo melhor com uma
  constante que não é do atleta.

A diferença prática: o diferencial recupera mais depressa em recuperações
longas e mais devagar em curtas. Fica dito no resultado.
"""


def _serie_limpa(vs):
    return [float(v) if v is not None else None for v in (vs or [])]


def wprime_balance(tempo, watts, cp, w_prime):
    """W′ restante a cada instante, pelo modelo diferencial.

    Devolve a série, o mínimo atingido e quantas vezes chegou a zero.
    """
    if not cp or not w_prime or w_prime <= 0:
        return {'ok': False, 'motivo': 'sem CP ou W′'}
    ws = _serie_limpa(watts)
    ts = _serie_limpa(tempo) or list(range(len(ws)))
    if len(ws) < 30:
        return {'ok': False, 'motivo': f'só {len(ws)} pontos de potência'}

    bal = float(w_prime)
    serie, minimo, i_min = [], float(w_prime), 0
    esgotou = 0
    estava_a_zero = False
    ultimo_t = ts[0] if ts else 0

    for i, p in enumerate(ws):
        dt = (ts[i] - ultimo_t) if i and ts[i] is not None else 1.0
        dt = dt if 0 < dt <= 10 else 1.0
        ultimo_t = ts[i] if ts[i] is not None else ultimo_t + dt
        if p is None:
            serie.append(round(bal))
            continue
        if p > cp:
            bal -= (p - cp) * dt
        else:
            # recuperacao proporcional ao que falta encher
            bal += (w_prime - bal) * (cp - p) / w_prime * dt
        bal = max(0.0, min(float(w_prime), bal))
        serie.append(round(bal))
        if bal < minimo:
            minimo, i_min = bal, i
        if bal <= 0 and not estava_a_zero:
            esgotou += 1
            estava_a_zero = True
        elif bal > w_prime * 0.05:
            estava_a_zero = False

    return {
        'ok': True,
        'serie': serie,
        'w_prime': round(float(w_prime)),
        'cp': round(float(cp), 1),
        'minimo_j': round(minimo),
        'minimo_pct': round(minimo / w_prime * 100, 1),
        'instante_do_minimo_s': (round(ts[i_min])
                                 if i_min < len(ts) and ts[i_min] is not None
                                 else None),
        'vezes_esgotado': esgotou,
        'modelo': 'diferencial (Froncioni–Clarke–Skiba), sem τ calibrado',
        'leitura': _ler_balance(minimo / w_prime, esgotou, 'W′'),
        'nota': ('recuperação proporcional ao que falta encher, sem '
                 'constante de tempo. O modelo integral de Skiba ajusta '
                 'melhor mas exige um τ calibrado com testes que não '
                 'existem — usar o da publicação seria importar a '
                 'fisiologia de outra pessoa'),
    }


def mprime_balance(tempo, smo2, cer, m_prime, suavizar=15):
    """M′ restante a cada instante — o análogo do W′ com SmO2.

    cer: taxa crítica de dessaturação, em %/s (valor NEGATIVO ou o seu
    módulo; normaliza-se aqui).
    m_prime: reserva, na mesma unidade integrada (% · s).
    """
    if cer is None or not m_prime:
        return {'ok': False, 'motivo': 'sem CER ou M′'}
    vs = _serie_limpa(smo2)
    ts = _serie_limpa(tempo) or list(range(len(vs)))
    if len(vs) < 30:
        return {'ok': False, 'motivo': f'só {len(vs)} pontos de SmO2'}

    cer = abs(float(cer))
    m_prime = abs(float(m_prime))

    # taxa instantanea de queda, suavizada: a derivada ponto a ponto do
    # SmO2 e' dominada por ruido, e sem suavizar o balance oscila sem
    # significado
    taxa = []
    meia = max(1, suavizar // 2)
    for i in range(len(vs)):
        a = max(0, i - meia)
        b = min(len(vs) - 1, i + meia)
        if vs[a] is None or vs[b] is None or ts[b] == ts[a]:
            taxa.append(None)
            continue
        taxa.append((vs[b] - vs[a]) / (ts[b] - ts[a]))

    bal = m_prime
    serie, minimo, i_min = [], m_prime, 0
    esgotou = 0
    estava_a_zero = False
    ultimo_t = ts[0]

    for i, r in enumerate(taxa):
        dt = (ts[i] - ultimo_t) if i else 1.0
        dt = dt if 0 < dt <= 10 else 1.0
        ultimo_t = ts[i]
        if r is None:
            serie.append(round(bal, 1))
            continue
        queda = -r  # positivo quando o SmO2 desce
        if queda > cer:
            bal -= (queda - cer) * dt
        else:
            bal += (m_prime - bal) * (cer - queda) / m_prime * dt
        bal = max(0.0, min(m_prime, bal))
        serie.append(round(bal, 1))
        if bal < minimo:
            minimo, i_min = bal, i
        if bal <= 0 and not estava_a_zero:
            esgotou += 1
            estava_a_zero = True
        elif bal > m_prime * 0.05:
            estava_a_zero = False

    return {
        'ok': True,
        'serie': serie,
        'm_prime': round(m_prime, 1),
        'cer_pct_por_s': round(cer, 4),
        'minimo': round(minimo, 1),
        'minimo_pct': round(minimo / m_prime * 100, 1),
        'instante_do_minimo_s': round(ts[i_min]) if i_min < len(ts) else None,
        'vezes_esgotado': esgotou,
        'suavizacao_s': suavizar,
        'modelo': 'diferencial, análogo ao W′ balance',
        'leitura': _ler_balance(minimo / m_prime, esgotou, 'M′'),
        'limite': ('o M′ e o CER vêm de um ajuste que precisa de ensaios de '
                   'durações diferentes até à exaustão. Se esse ajuste não '
                   'for válido, esta série é aritmética correcta sobre '
                   'parâmetros errados'),
    }


def _ler_balance(fraccao_minima, esgotou, nome):
    if esgotou:
        return (f'a reserva {nome} chegou a zero {esgotou} vez(es): o '
                'esforço foi até ao limite do que o modelo prevê ser '
                'sustentável')
    if fraccao_minima < 0.15:
        return (f'restaram {round(fraccao_minima * 100)}% da reserva {nome} '
                'no pior momento — muito perto do limite')
    if fraccao_minima < 0.5:
        return (f'a reserva {nome} desceu a {round(fraccao_minima * 100)}%: '
                'esforço substancial, com margem')
    return (f'a reserva {nome} nunca desceu abaixo de '
            f'{round(fraccao_minima * 100)}%: a sessão ficou longe do limite')
