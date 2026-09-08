"""utils/hrv_recovery.py — prescrição a partir do HRV diário.

Reescrito a partir do dashboard antigo (utils/hrv_guided.py), com quatro
mudanças que estão marcadas no sítio onde acontecem. O que ficou igual
ficou igual porque estava certo, não por inércia.

O QUE MEDE O QUÊ — e porque isto importa antes de qualquer cálculo
──────────────────────────────────────────────────────────────────
    LnRMSSD  →  Modelo β · Altini/Plews · Javaloyes · PSlope
    HF power →  Kiviniemi
    folha    →  sono, RHR, stress, soreness

Os quatro primeiros saem TODOS do mesmo número: o rMSSD da manhã.
Concordarem entre si não confirma nada — é o mesmo sinal contado quatro
vezes, com janelas diferentes. Só o Kiviniemi (banda HF) e o wellness
subjectivo trazem informação que o LnRMSSD não tem.

É o mesmo problema do AeT no perfil metabólico: dois cálculos alimentados
pela mesma fonte não se validam um ao outro. Por isso o consenso conta
FAMÍLIAS, não modelos.
"""

import math

# Dias mínimos de dados reais para cada cálculo ter sentido. O código
# antigo usava min_periods=4 ou 5 — com 5 pontos um desvio-padrão não
# significa nada, mas o número saía na mesma e parecia uma prescrição.
MINIMOS = {
    'lnrmssd': 14,      # baseline de 7d precisa de história atrás
    'swc': 21,          # média e SD de 28d, com alguma folga
    'javaloyes': 28,
    'kiviniemi': 15,    # janela de 10d + margem
    'pslope': 21,
    'beta': 28,
}


def _ln(v):
    try:
        v = float(v)
        return math.log(v) if v > 0 else None
    except (TypeError, ValueError):
        return None


def _media(vs):
    vs = [v for v in vs if v is not None]
    return sum(vs) / len(vs) if vs else None


def _sd(vs):
    vs = [v for v in vs if v is not None]
    if len(vs) < 2:
        return None
    m = sum(vs) / len(vs)
    return (sum((v - m) ** 2 for v in vs) / (len(vs) - 1)) ** 0.5


def _rolling(serie, janela, fn, minimo=None):
    """Aplica fn à janela dos últimos `janela` valores, terminando em cada i."""
    minimo = minimo or max(2, janela // 2)
    fora = []
    for i in range(len(serie)):
        jan = [v for v in serie[max(0, i - janela + 1):i + 1] if v is not None]
        fora.append(fn(jan) if len(jan) >= minimo else None)
    return fora


def _declive(vs):
    """Declive de uma regressão simples sobre a janela."""
    pts = [(i, v) for i, v in enumerate(vs) if v is not None]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    den = sum((p[0] - mx) ** 2 for p in pts)
    if den <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / den


def _cdf_normal(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def preparar(dias):
    """dias: [{'data': 'YYYY-MM-DD', 'hrv':…, 'hf_power':…, 'rhr':…, …}]

    Devolve a série ordenada e com os dias em falta preenchidos a None —
    sem isso, uma janela de "7 dias" podia abranger três semanas.
    """
    if not dias:
        return {'ok': False, 'motivo': 'sem dados'}
    from datetime import date, timedelta

    por_data = {}
    for d in dias:
        s = str(d.get('data') or d.get('date') or '')[:10]
        if len(s) == 10:
            por_data[s] = d

    if not por_data:
        return {'ok': False, 'motivo': 'nenhuma data válida'}

    datas = sorted(por_data)
    d0 = date.fromisoformat(datas[0])
    d1 = date.fromisoformat(datas[-1])
    seq, n = [], (d1 - d0).days + 1
    for k in range(n):
        dia = (d0 + timedelta(days=k)).isoformat()
        seq.append({'data': dia, **(por_data.get(dia) or {})})

    hrv = [d.get('hrv') for d in seq]
    ln = [_ln(v) for v in hrv]
    n_reais = sum(1 for v in ln if v is not None)
    return {
        'ok': True, 'dias': seq, 'lnrmssd': ln,
        'n_dias': n, 'n_com_hrv': n_reais,
        'cobertura_pct': round(n_reais / n * 100, 1) if n else 0,
        'de': datas[0], 'ate': datas[-1],
    }


# ══════════════════════════════════════════════════════════════════════════
# BASELINE E SWC (Altini / Plews)
#
# LnRMSSD, média móvel de 7 dias, e a banda de mudança minimamente
# relevante: média₂₈ ± 0.5 × SD₂₈.
# ══════════════════════════════════════════════════════════════════════════

def baseline_swc(ln, janela_base=7, janela_swc=28, k_swc=0.5):
    ln7 = _rolling(ln, janela_base, _media, minimo=4)
    m28 = _rolling(ln, janela_swc, _media, minimo=14)
    s28 = _rolling(ln, janela_swc, _sd, minimo=14)
    sup = [(m + k_swc * s) if (m is not None and s is not None) else None
           for m, s in zip(m28, s28)]
    inf = [(m - k_swc * s) if (m is not None and s is not None) else None
           for m, s in zip(m28, s28)]
    estado = []
    for v, lo, hi in zip(ln7, inf, sup):
        if v is None or lo is None:
            estado.append(None)
        elif v > hi:
            estado.append('acima')
        elif v < lo:
            estado.append('abaixo')
        else:
            estado.append('dentro')
    return {'ln7': ln7, 'media28': m28, 'sd28': s28,
            'swc_sup': sup, 'swc_inf': inf, 'estado': estado,
            'k_swc': k_swc,
            'metodo': 'Altini/Plews — LnRMSSD 7d vs SWC de 28d (±0.5 SD)'}


# ══════════════════════════════════════════════════════════════════════════
# MÁQUINA DE ESTADOS (Fig. 1, comum a Javaloyes e Kiviniemi)
#
# Os dois artigos usam a MESMA máquina. O que muda é a métrica e a regra
# do sinal — por isso há uma só implementação, e não duas a divergir.
#
#   max_high        tecto de HIGH consecutivos   (Javaloyes 2, Kiviniemi —)
#   max_dias_treino dias seguidos até REST forçado (Kiviniemi 9)
# ══════════════════════════════════════════════════════════════════════════

def maquina_de_estados(valores, sinal_fn, max_high=2, max_dias_treino=None):
    pres, sinais = [], []
    estado, n_high, n_rest, n_treino = 'START', 0, 0, 0
    ant = ant2 = None
    for i, v in enumerate(valores):
        s = sinal_fn(i, v, ant, ant2)
        if v is None or s == '·':
            p, estado, n_high, n_rest, n_treino = 'LOW', 'LOW', 0, 0, 0
        elif estado == 'START':
            p, estado, n_high, n_rest, n_treino = 'LOW', 'LOW', 0, 0, 1
        elif max_dias_treino is not None and n_treino >= max_dias_treino:
            p, estado, n_rest, n_high, n_treino = 'REST', 'REST', 1, 0, 0
        elif estado == 'HIGH':
            if s.endswith('+'):
                if max_high is not None and n_high >= max_high:
                    p, estado, n_high = 'LOW', 'LOW', 0
                    n_treino += 1
                else:
                    p, estado = 'HIGH', 'HIGH'
                    n_high += 1
                    n_treino += 1
            else:
                p, estado, n_high = 'LOW', 'LOW', 0
                n_treino += 1
            n_rest = 0
        elif estado == 'LOW':
            if s.endswith('+'):
                p, estado, n_high, n_rest = 'HIGH', 'HIGH', 1, 0
                n_treino += 1
            else:
                p, estado, n_rest, n_treino = 'REST', 'REST', 1, 0
        elif estado == 'REST':
            if n_rest >= 2 or s.endswith('+'):
                p, estado, n_rest, n_treino = 'LOW', 'LOW', 0, 1
            else:
                p, estado, n_treino = 'REST', 'REST', 0
                n_rest += 1
            n_high = 0
        else:
            p, estado, n_treino = 'LOW', 'LOW', 1
        pres.append(p)
        sinais.append(s)
        if v is not None:
            ant2, ant = ant, v
    return pres, sinais


def javaloyes(ln, dias_baseline=60, k_swc=0.5):
    """LnRMSSD 7d contra a banda SWC, com tecto de 2 HIGH.

    dias_baseline=60 e não os 14 do artigo: com 14 dias o SD tem muito
    ruído de amostragem, e a largura da banda variava cerca de 3× mais de
    semana para semana. É uma escolha nossa, registada aqui para não
    passar por fidelidade ao artigo.

    A baseline de cada dia usa só dias ANTERIORES — o dia avaliado nunca
    contribui para o seu próprio limiar.
    """
    ln7 = _rolling(ln, 7, _media, minimo=4)
    # baseline com atraso de um dia: exclui o próprio dia
    m = [None] * len(ln)
    s = [None] * len(ln)
    for i in range(len(ln)):
        jan = [v for v in ln[max(0, i - dias_baseline):i] if v is not None]
        if len(jan) >= 14:
            m[i] = _media(jan)
            s[i] = _sd(jan)
    sup = [(a + k_swc * b) if a is not None and b else None
           for a, b in zip(m, s)]
    inf = [(a - k_swc * b) if a is not None and b else None
           for a, b in zip(m, s)]

    def _sinal(i, v, ant, ant2):
        if v is None or inf[i] is None:
            return '·'
        return 'HRV+' if inf[i] <= v <= sup[i] else 'HRV−'

    pres, sinais = maquina_de_estados(ln7, _sinal, max_high=2,
                                      max_dias_treino=None)
    return {'ln7': ln7, 'swc_sup': sup, 'swc_inf': inf,
            'prescricao': pres, 'sinal': sinais,
            'dias_baseline': dias_baseline,
            'metodo': ('Javaloyes — LnRMSSD 7d vs banda SWC, máquina de '
                       'estados com tecto de 2 HIGH'),
            'nota_baseline': (
                f'baseline de {dias_baseline} dias anteriores (o artigo usa '
                '14; com 14 a banda oscila cerca de 3× mais). O dia '
                'avaliado nunca entra na sua própria baseline')}


def kiviniemi(hf, usar_log=True):
    """HF power contra média de 10d menos 1 SD, sem tecto de HIGH.

    usar_log: o artigo NÃO descreve transformação logarítmica — trabalha
    na escala original. Fica como parâmetro explícito, com o valor por
    omissão a True porque o HF em ms² é muito assimétrico; mas isso é
    escolha nossa e não do Kiviniemi et al. 2007.
    """
    vs = [(_ln(v) if usar_log else (float(v) if v is not None else None))
          for v in hf]
    n_reais = sum(1 for v in vs if v is not None)
    if n_reais < MINIMOS['kiviniemi']:
        return {'ok': False,
                'motivo': (f'só {n_reais} dias com HF power; são precisos '
                           f"{MINIMOS['kiviniemi']}"),
                'n_dias': n_reais}
    m10 = _rolling(vs, 10, _media, minimo=5)
    s10 = _rolling(vs, 10, _sd, minimo=5)
    ref = [(a - b) if a is not None and b is not None else None
           for a, b in zip(m10, s10)]

    def _sinal(i, v, ant, ant2):
        if v is None or ref[i] is None:
            return '·'
        abaixo = v < ref[i]
        # tendência decrescente de dois dias, na escala transformada
        desce = (ant is not None and ant2 is not None
                 and (ant2 - ant) > 0.1 and (ant - v) > 0.1)
        return 'HF−' if (abaixo or desce) else 'HF+'

    pres, sinais = maquina_de_estados(vs, _sinal, max_high=None,
                                      max_dias_treino=9)
    return {'ok': True, 'hf': vs, 'referencia': ref,
            'prescricao': pres, 'sinal': sinais, 'usou_log': usar_log,
            'metodo': ('Kiviniemi — HF power vs média de 10d − 1 SD, sem '
                       'tecto de HIGH, REST forçado ao 9.º dia de treino'),
            'nota_log': ('o artigo não descreve log nenhum. Aplicámo-lo '
                         'porque o HF em ms² é muito assimétrico, mas é '
                         'escolha nossa' if usar_log else
                         'escala original, como no artigo')}


# ══════════════════════════════════════════════════════════════════════════
# PSLOPE — declive de 7 dias e PERSISTÊNCIA do estado
#
# É o único que responde a "há quanto tempo estou assim", e isso muda a
# leitura de tudo o resto: fadiga há um dia é o efeito de um treino;
# fadiga há cinco dias é outra coisa.
# ══════════════════════════════════════════════════════════════════════════

ZONAS_SLOPE = [
    ('Supercompensação', 1.0, None),
    ('Recuperação', 0.5, 1.0),
    ('Estável', -0.5, 0.5),
    ('Declínio leve', -1.0, -0.5),
    ('Fadiga', -2.0, -1.0),
    ('NFOR crítico', None, -2.0),
]


def pslope(ln, janela=7):
    """Declive do LnRMSSD e há quantos dias se está na mesma zona."""
    dec = _rolling(ln, janela, _declive, minimo=4)
    reais = [v for v in dec if v is not None]
    if len(reais) < MINIMOS['pslope']:
        return {'ok': False,
                'motivo': (f'só {len(reais)} dias com declive; são precisos '
                           f"{MINIMOS['pslope']}")}
    m, s = _media(reais), _sd(reais)
    if not s:
        return {'ok': False, 'motivo': 'declive sem variação'}

    def _zona(v):
        if v is None:
            return None
        z = (v - m) / s
        for nome, lo, hi in ZONAS_SLOPE:
            if (lo is None or z >= lo) and (hi is None or z < hi):
                return nome
        return 'Estável'

    zonas = [_zona(v) for v in dec]
    actual = next((z for z in reversed(zonas) if z), None)

    # há quantos dias na zona actual
    dias_na_zona = 0
    for z in reversed(zonas):
        if z is None:
            continue
        if z == actual:
            dias_na_zona += 1
        else:
            break

    # histórico de permanência em cada zona
    seqs, ant, n = [], None, 0
    for z in zonas:
        if z is None:
            continue
        if z == ant:
            n += 1
        else:
            if ant:
                seqs.append((ant, n))
            ant, n = z, 1
    if ant:
        seqs.append((ant, n))

    hist = {}
    for nome, dur in seqs:
        hist.setdefault(nome, []).append(dur)
    stats = {k: {'media': round(sum(v) / len(v), 1), 'max': max(v),
                 'n_vezes': len(v)} for k, v in hist.items()}

    st = stats.get(actual) or {}
    razao = (round(dias_na_zona / st['media'], 1)
             if st.get('media') else None)
    return {
        'ok': True, 'declive': dec, 'zonas': zonas,
        'zona_actual': actual, 'dias_na_zona': dias_na_zona,
        'declive_actual': round(reais[-1], 5),
        'media': round(m, 5), 'sd': round(s, 5),
        'historico': stats,
        'vs_media_historica': razao,
        'metodo': f'declive do LnRMSSD em {janela} dias, zonas por z-score',
        'leitura': _ler_pslope(actual, dias_na_zona, st, razao),
    }


def _ler_pslope(zona, dias, st, razao):
    """O que o estado significa. A decisão fica para quem treina."""
    if not zona:
        return None
    base = {
        'Supercompensação': 'o HRV está a subir mais do que é habitual em ti',
        'Recuperação': 'o HRV está a subir',
        'Estável': 'o HRV está dentro da tua variação normal',
        'Declínio leve': 'o HRV está a descer, ainda dentro do comum',
        'Fadiga': 'o HRV está a descer de forma acentuada',
        'NFOR crítico': ('o HRV está a descer muito abaixo do que é normal '
                         'em ti'),
    }.get(zona, '')
    ctx = f'{dias} dia(s) nesta zona'
    if st.get('media'):
        ctx += f", contra uma média histórica de {st['media']}"
    if st.get('max'):
        ctx += f" e um máximo de {st['max']}"
    aviso = None
    if razao and razao >= 2 and zona in ('Fadiga', 'NFOR crítico',
                                         'Declínio leve'):
        aviso = (f'está nesta zona há {razao}× o habitual. A duração é o '
                 'que distingue o efeito de um treino de uma tendência')
    return {'significa': base, 'contexto': ctx, 'aviso': aviso}


# ══════════════════════════════════════════════════════════════════════════
# MODELO β — três horizontes do mesmo sinal
#
# MUDANÇA face ao dashboard antigo: o modo 'multi' fundia HRV, sono, RHR e
# carga com pesos 40/20/20/20. Esses pesos não vêm de nenhum artigo, e a
# fusão esconde qual dos quatro mudou — um β a cair podia ser HRV, sono ou
# treino de ontem, e o número não distinguia.
#
# Aqui o β é só do LnRMSSD, e os outros canais aparecem SEPARADOS.
# ══════════════════════════════════════════════════════════════════════════

def modelo_beta(ln):
    m28 = _rolling(ln, 28, _media, minimo=14)
    s28 = _rolling(ln, 28, _sd, minimo=14)
    beta = []
    for v, m, s in zip(ln, m28, s28):
        if v is None or m is None or not s:
            beta.append(None)
        else:
            beta.append(round(_cdf_normal((v - m) / s) * 100, 1))
    b3 = _rolling(beta, 3, _media, minimo=2)
    b7 = _rolling(beta, 7, _media, minimo=4)
    b28 = _rolling(beta, 28, _media, minimo=14)
    agudo = [(a - b) if a is not None and b is not None else None
             for a, b in zip(b3, b7)]
    cronico = [(a - b) if a is not None and b is not None else None
               for a, b in zip(b7, b28)]
    reais = [v for v in beta if v is not None]
    if len(reais) < MINIMOS['beta']:
        return {'ok': False,
                'motivo': f'só {len(reais)} dias; são precisos '
                          f"{MINIMOS['beta']}"}
    return {
        'ok': True, 'beta': beta, 'agudo': agudo, 'cronico': cronico,
        'beta_hoje': next((v for v in reversed(beta) if v is not None), None),
        'agudo_hoje': next((v for v in reversed(agudo) if v is not None),
                           None),
        'cronico_hoje': next((v for v in reversed(cronico)
                              if v is not None), None),
        'metodo': ('β = percentil do LnRMSSD de hoje na distribuição dos '
                   '28 dias. Agudo = 3d − 7d; crónico = 7d − 28d'),
        'nota': ('só do LnRMSSD. O dashboard antigo tinha um modo que '
                 'fundia HRV, sono, RHR e carga com pesos fixos — os pesos '
                 'não vinham de nenhum artigo, e a fusão escondia qual dos '
                 'quatro tinha mudado'),
    }


# ══════════════════════════════════════════════════════════════════════════
# SÍNTESE — por FAMÍLIAS, não por modelos
#
# Ver a nota do topo: quatro dos cinco modelos saem do mesmo LnRMSSD. Se
# cada um votasse por si, a família do LnRMSSD teria quatro votos contra um
# do HF — e o "consenso" seria simplesmente o LnRMSSD a repetir-se.
#
# Por isso a família do LnRMSSD vota UMA vez, com o seu próprio consenso
# interno, e a divergência DENTRO da família é informação à parte: quando
# o β e o Javaloyes discordam olhando para o mesmo número, o que discorda
# é a janela temporal, não a fisiologia.
# ══════════════════════════════════════════════════════════════════════════

FAMILIAS = {
    'lnrmssd': {'nome': 'LnRMSSD (rMSSD da manhã)',
                'modelos': ['swc', 'javaloyes', 'pslope', 'beta']},
    'hf': {'nome': 'HF power', 'modelos': ['kiviniemi']},
    'subjectivo': {'nome': 'Wellness (folha)', 'modelos': ['wellness']},
}

# Voto de cada estado: +1 pronto para carga, 0 neutro, −1 recuar.
_VOTO = {
    'HIGH': 1, 'LOW': 0, 'REST': -1,
    'acima': 1, 'dentro': 0, 'abaixo': -1,
    'Supercompensação': 1, 'Recuperação': 1, 'Estável': 0,
    'Declínio leve': 0, 'Fadiga': -1, 'NFOR crítico': -1,
}


def _voto_beta(b, agudo, cronico):
    """Regra de convergência do β: ≥2 dos 3 na mesma direcção."""
    if b is None:
        return None
    sinais = []
    sinais.append(1 if b >= 60 else (-1 if b <= 40 else 0))
    if agudo is not None:
        sinais.append(1 if agudo > 2 else (-1 if agudo < -2 else 0))
    if cronico is not None:
        sinais.append(1 if cronico > 2 else (-1 if cronico < -2 else 0))
    pos = sum(1 for s in sinais if s > 0)
    neg = sum(1 for s in sinais if s < 0)
    if pos >= 2:
        return 1
    if neg >= 2:
        return -1
    return 0


def sintetizar(swc=None, jav=None, kiv=None, ps=None, beta=None,
               wellness=None, pesos=None):
    """Junta tudo por famílias e devolve uma leitura, não uma ordem."""
    pesos = pesos or PESOS_POR_OMISSAO
    votos_ln, detalhe = [], []

    if swc:
        v = _VOTO.get(swc)
        votos_ln.append(v)
        detalhe.append({'modelo': 'SWC (Altini/Plews)', 'familia': 'lnrmssd',
                        'estado': swc, 'voto': v})
    if jav:
        v = _VOTO.get(jav)
        votos_ln.append(v)
        detalhe.append({'modelo': 'Javaloyes', 'familia': 'lnrmssd',
                        'estado': jav, 'voto': v})
    if ps:
        v = _VOTO.get(ps)
        votos_ln.append(v)
        detalhe.append({'modelo': 'PSlope', 'familia': 'lnrmssd',
                        'estado': ps, 'voto': v})
    if beta:
        v = _voto_beta(beta.get('beta'), beta.get('agudo'),
                       beta.get('cronico'))
        votos_ln.append(v)
        detalhe.append({'modelo': 'Modelo β', 'familia': 'lnrmssd',
                        'estado': f"β={beta.get('beta')}", 'voto': v})

    votos_ln = [v for v in votos_ln if v is not None]
    fam = {}
    if votos_ln:
        m = sum(votos_ln) / len(votos_ln)
        fam['lnrmssd'] = {
            'voto': m, 'n_modelos': len(votos_ln),
            'unanime': len(set(votos_ln)) == 1,
            'dispersao': (max(votos_ln) - min(votos_ln)),
        }
    if kiv:
        v = _VOTO.get(kiv)
        if v is not None:
            fam['hf'] = {'voto': v, 'n_modelos': 1, 'unanime': True,
                         'dispersao': 0}
            detalhe.append({'modelo': 'Kiviniemi', 'familia': 'hf',
                            'estado': kiv, 'voto': v})
    if wellness is not None:
        fam['subjectivo'] = {'voto': wellness, 'n_modelos': 1,
                             'unanime': True, 'dispersao': 0}
        detalhe.append({'modelo': 'Wellness', 'familia': 'subjectivo',
                        'estado': f'{wellness:+.0f}', 'voto': wellness})

    if not fam:
        return {'ok': False, 'motivo': 'nenhuma família com dados'}

    num = sum(fam[k]['voto'] * pesos.get(k, 0) for k in fam)
    den = sum(pesos.get(k, 0) for k in fam)
    score = num / den if den else 0

    avisos = []
    f_ln = fam.get('lnrmssd')
    if f_ln and f_ln['dispersao'] >= 2:
        avisos.append(
            'os modelos do LnRMSSD discordam entre si. Como olham todos '
            'para o mesmo número, o que difere é a JANELA: um vê os '
            'últimos 3 dias, outro os últimos 28. Discordarem costuma '
            'significar que a tendência mudou há pouco')
    if 'hf' in fam and f_ln and (fam['hf']['voto'] - _sinal_de(f_ln['voto'])) \
            not in (0,):
        if abs(fam['hf']['voto'] - f_ln['voto']) >= 1.5:
            avisos.append(
                'o HF power e o LnRMSSD discordam. Estes SÃO independentes, '
                'por isso a discordância é real e não redundância — vale '
                'mais do que dois modelos do LnRMSSD a concordar')

    estado = ('carga' if score >= 0.5 else
              'moderado' if score >= -0.2 else 'recuar')
    return {
        'ok': True,
        'score': round(score, 2),
        'estado': estado,
        'leitura': {
            'carga': 'os sinais estão a favor de treino intenso hoje',
            'moderado': ('sem sinal claro em nenhuma direcção: treino '
                         'moderado, e reavaliar amanhã'),
            'recuar': 'os sinais apontam para recuar hoje',
        }[estado],
        'familias': fam,
        'detalhe': detalhe,
        'pesos_usados': {k: pesos.get(k) for k in fam},
        'avisos': avisos,
        'nota': (
            'o peso é por FAMÍLIA e não por modelo. Quatro modelos do '
            'LnRMSSD a concordar não são quatro confirmações — são o mesmo '
            'número visto com quatro janelas'),
    }


def _sinal_de(v):
    return 1 if v > 0.3 else (-1 if v < -0.3 else 0)


# Ver a discussão dos pesos no fim do ficheiro.
PESOS_POR_OMISSAO = {
    'lnrmssd': 0.55,
    'hf': 0.25,
    'subjectivo': 0.20,
}


# ══════════════════════════════════════════════════════════════════════════
# PESOS AJUSTADOS AOS DADOS
#
# Os 0.55/0.25/0.20 são uma escolha sem base — nenhum artigo compara estes
# métodos entre si. Em vez de os arbitrar para sempre, medem-se.
#
# O PROBLEMA: não há rótulo. Não sabemos se o dia correu bem, portanto não
# se pode medir "acerto".
#
# O QUE SE PODE MEDIR: se cada família antecipa o LnRMSSD de AMANHÃ. Uma
# família que diga "recuar" hoje e veja o HRV cair amanhã antecipou
# alguma coisa; uma que diga "recuar" e o HRV suba, não.
#
# ISTO É CIRCULAR PARA A FAMÍLIA DO LnRMSSD — está a prever-se a si
# própria, e ganharia sempre. Por isso o peso do LnRMSSD fica FIXO, como
# referência, e só o HF e o wellness são ajustados: são precisamente
# aqueles cujo valor não conhecemos.
#
# Janela móvel de 180 dias, recalculada a cada dia: os pesos acompanham
# mudanças de forma e de hábitos de medição.
# ══════════════════════════════════════════════════════════════════════════

PESO_FIXO_LNRMSSD = 0.55   # referência, não se auto-avalia
PESO_MIN = 0.05            # nenhuma família desaparece por completo
PESO_MAX = 0.45


def _correlacao(xs, ys):
    pares = [(a, b) for a, b in zip(xs, ys)
             if a is not None and b is not None]
    if len(pares) < 20:
        return None, len(pares)
    n = len(pares)
    mx = sum(p[0] for p in pares) / n
    my = sum(p[1] for p in pares) / n
    sx = sum((p[0] - mx) ** 2 for p in pares) ** 0.5
    sy = sum((p[1] - my) ** 2 for p in pares) ** 0.5
    if sx <= 0 or sy <= 0:
        return None, n
    r = sum((p[0] - mx) * (p[1] - my) for p in pares) / (sx * sy)
    return r, n


def pesos_ajustados(ln, votos_hf=None, votos_wellness=None, janela=180):
    """Peso de cada família pelo que antecipa da variação de amanhã.

    ln            série de LnRMSSD
    votos_hf      voto diário da família HF (−1..+1), alinhado com ln
    votos_wellness idem para o wellness
    """
    n = len(ln)
    ini = max(0, n - janela)
    # variação do LnRMSSD de amanhã face a hoje
    delta = [None] * n
    for i in range(n - 1):
        if ln[i] is not None and ln[i + 1] is not None:
            delta[i] = ln[i + 1] - ln[i]

    fora = {'lnrmssd': {'peso': PESO_FIXO_LNRMSSD, 'fixo': True,
                        'porque': ('é a referência contra a qual as outras '
                                   'são medidas — avaliá-la contra si '
                                   'própria seria circular')}}
    brutos = {}
    for nome, votos in (('hf', votos_hf), ('subjectivo', votos_wellness)):
        if not votos:
            continue
        r, n_pares = _correlacao(votos[ini:n], delta[ini:n])
        if r is None:
            fora[nome] = {'peso': PESO_MIN, 'r': None, 'n': n_pares,
                          'porque': f'só {n_pares} dias em comum; são '
                                    'precisos 20 para medir'}
            continue
        # só a correlação POSITIVA conta: prever ao contrário não é mérito
        brutos[nome] = max(0.0, r)
        fora[nome] = {'r': round(r, 3), 'n': n_pares}

    # repartir o que sobra (1 − peso do LnRMSSD) na proporção do r
    resto = 1.0 - PESO_FIXO_LNRMSSD
    soma = sum(brutos.values())
    for nome in ('hf', 'subjectivo'):
        if nome not in fora:
            continue
        if 'peso' in fora[nome]:
            continue
        if soma <= 0:
            p = resto / max(1, len(brutos)) if brutos else PESO_MIN
        else:
            p = resto * brutos[nome] / soma
        fora[nome]['peso'] = round(max(PESO_MIN, min(PESO_MAX, p)), 3)
        r = fora[nome]['r']
        fora[nome]['porque'] = (
            f'r={r} entre o voto de hoje e a variação do LnRMSSD de amanhã, '
            f"em {fora[nome]['n']} dias"
            + ('. Correlação negativa não conta — prever ao contrário não é '
               'mérito' if r is not None and r < 0 else ''))

    return {
        'pesos': {k: v['peso'] for k, v in fora.items()},
        'detalhe': fora,
        'janela_dias': janela,
        'metodo': ('cada família é medida pelo que antecipa da variação do '
                   'LnRMSSD do dia seguinte; o peso é proporcional a essa '
                   'correlação'),
        'limite': (
            'isto mede antecipação, não acerto. Sem saber se o dia correu '
            'bem, não há forma de medir acerto — e antecipar o HRV de '
            'amanhã não é o mesmo que prescrever bem hoje. É melhor do que '
            'pesos inventados, e menos do que uma validação a sério'),
    }
