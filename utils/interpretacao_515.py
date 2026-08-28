"""utils/interpretacao_515.py — 5-1-5 Interpretation Tool, automatizado.

Portado do 515_Interpretation_tool_v2_2.xlsm. A ferramenta original faz 13
perguntas ao utilizador sobre o que ele ve nos graficos; aqui as respostas
sao medidas dos blocos que ja' detectamos, e continuam editaveis.

Dois eixos independentes:

  U/S   Utilization vs Supply
        SmO2 de trabalho alto (>60%) = o musculo nao esta a extrair ->
        limitacao de UTILIZACAO. SmO2 a cair abaixo de 20% = extraccao
        maxima -> limitacao de FORNECIMENTO.

  P/C   Pulmonary vs Cardiac
        THb e SmO2 de repouso a subir, atraso THb->SmO2, FC de repouso a
        subir -> PULMONAR. THb a descer -> CARDIACO.

DUAS CORRECCOES face ao ficheiro original:

1. As linhas 29-32 do motor de calculo (perguntas 10 a 13, frequencia
   cardiaca) referenciam as linhas erradas: K29 usa I27, K30 usa I28, e
   assim por diante. A pergunta 10 soma a pontuacao da 8B, a 11 a da 9. O
   eixo P/C fica deslocado duas linhas em toda a seccao de FC. Aqui esta
   como pretendido.

2. O maximo de U/S esta fixo em 11, mas 11 so' e' atingivel com dois
   sensores: 7+1+1 no musculo principal e 1+1 no secundario. Com um
   sensor, o denominador correcto e' 9 -- senao o score nunca passa de
   82%. Segue-se a logica da coluna AB do proprio ficheiro, que ja'
   anula o segundo musculo quando nao existe.

O que a ferramenta NAO faz, e continua a nao fazer: dizer se os dados
prestam. Se a sessao tiver artefactos ou blocos a mais, o resultado sai
com aviso e sem classificacao.
"""

# Cortes de tendencia, em percentagem da amplitude do canal na sessao.
# O ficheiro original nao os define -- pede ao utilizador que olhe e
# decida "Clear" ou "Slight". Aqui ficam explicitos e ajustaveis, porque
# um criterio invisivel e' pior do que um criterio discutivel.
CORTE_CLARO = 0.10
CORTE_LIGEIRO = 0.03

# Fraccao final da sessao que conta como "later part of the assessment".
# O ficheiro tambem nao define. Metade dos blocos e' o que sobra quando
# se tira o aquecimento e as primeiras cargas.
FRACCAO_FINAL = 0.5

# Segundos finais de cada recuperacao que contam como "repouso". O inicio da
# recuperacao ainda esta a subir; o patamar e' o que interessa.
REPOUSO_SEG = 30

# Minimo de blocos para uma tendencia. Com dois pontos qualquer recta passa
# por eles e o declive nao significa nada. Era isto que fazia 3A, 4A, 6A,
# 7A, 10 e 11 sairem sem resposta em sessoes de 4 ou 5 blocos: a fraccao
# final dava k=2 e o ajuste recusava.
MIN_BLOCOS_TENDENCIA = 3

# Segundos finais de cada bloco usados como valor representativo.
#
# A media do bloco inteiro mistura a transicao com o patamar: num bloco de
# recuperacao inclui a subida do SmO2 desde o fundo, e o "valor de repouso"
# sai mais baixo do que o repouso realmente atingido. Os ultimos segundos
# sao o estado a que o bloco chegou, que e' o que as perguntas do 5-1-5
# pedem.
CAUDA_S = 30

NIVEIS = ['Clear increase', 'Slight increase', 'Steady',
          'Slight decrease', 'Clear decrease']

# Pontuacoes, tal como no ficheiro (com as correccoes acima)
ESCALA_US = {
    '2A': {'>60%': 7, '50-60%': 5, '40-50%': 3,
           '30-40%': 2, '20%-30%': 1, '<20%': 0},
    '4A': {'Clear increase': 1, 'Slight increase': 0.75, 'Steady': 0.5,
           'Slight decrease': 0.25, 'Clear decrease': 0},
    '5A': {'Clear increase': 1, 'Slight increase': 0.75, 'Steady': 0.5,
           'Slight decrease': 0.25, 'Clear decrease': 0},
    '4B': {'Clear increase': 1, 'Slight increase': 0.75, 'Steady': 0.5,
           'Slight decrease': 0.25, 'Clear decrease': 0},
    '5B': {'Clear increase': 1, 'Slight increase': 0.75, 'Steady': 0.5,
           'Slight decrease': 0.25, 'Clear decrease': 0},
}

ESCALA_PC = {
    '3A': {'Clear increase': 6, 'Slight increase': 3, 'Steady': 0,
           'Slight decrease': -4, 'Clear decrease': -8},
    '6A': {'Clear increase': 8, 'Slight increase': 4, 'Steady': 0,
           'Slight decrease': -5.3, 'Clear decrease': -10.7},
    '7A': {'Clear increase': 6, 'Slight increase': 3, 'Steady': 0,
           'Slight decrease': -4, 'Clear decrease': -8},
    '8A': {'Clear increase': 4, 'Slight increase': 2, 'Steady': 0,
           'Slight decrease': -2.7, 'Clear decrease': -5.3},
    '9':  {'>3 seconds': 4, '1-3 seconds': 2, 'No Delay': 0},
    '10': {'Clear increase': 2, 'Slight increase': 1, 'Steady': 0,
           'Slight decrease': 0, 'Clear decrease': 0},
    '11': {'Clear increase': 0, 'Slight increase': 0, 'Steady': 0,
           'Slight decrease': 1, 'Clear decrease': 2},
    '12': {'Clear increase': 0, 'Slight increase': 0, 'Steady': 0,
           'Slight decrease': 1, 'Clear decrease': 2},
    '13': {'Clear increase': 2, 'Slight increase': 1, 'Steady': 0,
           'Slight decrease': 0, 'Clear decrease': 0},
}

PERGUNTAS = {
    '1A': 'SmO2 de repouso (valor)',
    '2A': 'SmO2 mínimo de trabalho nos últimos blocos',
    '3A': 'Tendência do SmO2 de repouso',
    '4A': 'Tendência do SmO2 mínimo entre blocos',
    '5A': 'Tendência do SmO2 dentro de cada bloco',
    '6A': 'Tendência do THb de repouso',
    '7A': 'Tendência do THb de trabalho entre blocos',
    '8A': 'THb em carga repetida',
    '9':  'Atraso entre THb e SmO2 no início da recuperação',
    '10': 'Tendência da FC de repouso',
    '11': 'Tendência da FC máxima de trabalho',
    '12': 'Tendência da FC dentro da carga',
    '13': 'FC em carga repetida',
}


def _media(vs):
    vs = [v for v in vs if v is not None]
    return sum(vs) / len(vs) if vs else None


def _declive(ys):
    """Declive por indice, por minimos quadrados."""
    ys = [y for y in ys if y is not None]
    n = len(ys)
    if n < 3:
        return None
    mx = (n - 1) / 2
    my = sum(ys) / n
    sxx = sum((i - mx) ** 2 for i in range(n))
    if sxx <= 0:
        return None
    return sum((i - mx) * (ys[i] - my) for i in range(n)) / sxx


def classificar(valores, amplitude, corte_claro=CORTE_CLARO,
                corte_ligeiro=CORTE_LIGEIRO):
    """Serie de valores -> um dos cinco niveis.

    O declive e' medido por bloco e comparado com a AMPLITUDE do canal na
    sessao, nao com uma constante: 2% de queda por bloco significa coisas
    diferentes num SmO2 que oscila 40 pontos e num que oscila 5.
    """
    m = _declive(valores)
    if m is None or not amplitude:
        return None, None
    rel = m * max(1, len(valores) - 1) / amplitude
    if rel >= corte_claro:
        n = 'Clear increase'
    elif rel >= corte_ligeiro:
        n = 'Slight increase'
    elif rel > -corte_ligeiro:
        n = 'Steady'
    elif rel > -corte_claro:
        n = 'Slight decrease'
    else:
        n = 'Clear decrease'
    return n, round(rel * 100, 1)


def _faixa_smo2(v):
    if v is None:
        return None
    if v > 60:
        return '>60%'
    if v >= 50:
        return '50-60%'
    if v >= 40:
        return '40-50%'
    if v >= 30:
        return '30-40%'
    if v >= 20:
        return '20%-30%'
    return '<20%'


def _recortar(serie, tempo, t0, t1, cauda=None):
    """Valores no intervalo. Com 'cauda', so' os ultimos N segundos."""
    if cauda:
        t0 = max(t0, t1 - cauda)
    return [serie[i] for i in range(min(len(serie), len(tempo)))
            if t0 <= tempo[i] <= t1 and serie[i] is not None]


def medir(tempo, canais, blocos, fraccao_final=FRACCAO_FINAL,
          corte_claro=CORTE_CLARO, corte_ligeiro=CORTE_LIGEIRO,
          cauda=CAUDA_S):
    """Mede as 13 perguntas a partir dos blocos ON/OFF."""
    smo2 = canais.get('smo2') or []
    thb = canais.get('thb') or []
    hr = canais.get('heartrate') or []
    if not smo2:
        return {'ok': False, 'motivo': 'sem SmO2'}

    ons = [b for b in blocos if b.get('on')]
    offs = [b for b in blocos if not b.get('on')]
    if len(ons) < 3:
        return {'ok': False,
                'motivo': f'so {len(ons)} blocos de trabalho; sao precisos 3'}

    k = max(MIN_BLOCOS_TENDENCIA, int(round(len(ons) * fraccao_final)))
    k = min(k, len(ons))
    ons_fim = ons[-k:]
    offs_fim = offs[-min(k, len(offs)):] if offs else []

    def amp(serie):
        vs = [v for v in serie if v is not None]
        return (max(vs) - min(vs)) if len(vs) > 1 else None

    a_smo2, a_thb, a_hr = amp(smo2), amp(thb), amp(hr)

    # por bloco
    smo2_min_on = [min(_recortar(smo2, tempo, b['t0'], b['t1']), default=None)
                   for b in ons_fim]
    # Repouso e trabalho medidos nos ultimos CAUDA_S de cada bloco: e o
    # estado a que o bloco chegou, nao a media da transicao com o patamar.
    smo2_rep = [_media(_recortar(smo2, tempo, b['t0'], b['t1'], cauda))
                for b in offs_fim]
    thb_rep = [_media(_recortar(thb, tempo, b['t0'], b['t1'], cauda))
               for b in offs_fim] if thb else []
    thb_on = [_media(_recortar(thb, tempo, b['t0'], b['t1'], cauda))
              for b in ons_fim] if thb else []
    hr_rep = [_media(_recortar(hr, tempo, b['t0'], b['t1'], cauda))
              for b in offs_fim] if hr else []
    hr_max_on = [max(_recortar(hr, tempo, b['t0'], b['t1']), default=None)
                 for b in ons_fim] if hr else []

    # dentro dos blocos: declive medio, normalizado
    def dentro(serie, amplitude):
        if not serie or not amplitude:
            return None, None
        rels = []
        for b in ons_fim:
            vs = _recortar(serie, tempo, b['t0'], b['t1'])
            m = _declive(vs)
            if m is not None:
                rels.append(m * max(1, len(vs) - 1) / amplitude)
        if not rels:
            return None, None
        r = sum(rels) / len(rels)
        if r >= corte_claro:
            n = 'Clear increase'
        elif r >= corte_ligeiro:
            n = 'Slight increase'
        elif r > -corte_ligeiro:
            n = 'Steady'
        elif r > -corte_claro:
            n = 'Slight decrease'
        else:
            n = 'Clear decrease'
        return n, round(r * 100, 1)

    r = {}
    r['1A'] = {'valor': round(_media(smo2_rep), 1) if smo2_rep else None,
               'unidade': '%'}
    minimo = min([v for v in smo2_min_on if v is not None], default=None)
    r['2A'] = {'resposta': _faixa_smo2(minimo),
               'valor': round(minimo, 1) if minimo is not None else None}
    for chave, serie, amplitude in (('3A', smo2_rep, a_smo2),
                                    ('4A', smo2_min_on, a_smo2),
                                    ('6A', thb_rep, a_thb),
                                    ('7A', thb_on, a_thb),
                                    ('10', hr_rep, a_hr),
                                    ('11', hr_max_on, a_hr)):
        n, rel = classificar(serie, amplitude, corte_claro, corte_ligeiro)
        r[chave] = {'resposta': n, 'declive_pct_da_amplitude': rel,
                    'n_blocos': len([v for v in serie if v is not None])}
    n, rel = dentro(smo2, a_smo2)
    r['5A'] = {'resposta': n, 'declive_pct_da_amplitude': rel}
    n, rel = dentro(hr, a_hr)
    r['12'] = {'resposta': n, 'declive_pct_da_amplitude': rel}

    # cargas repetidas: mesmo escalao de watts em blocos diferentes
    def repetida(valores_por_bloco, amplitude):
        pares = [(b.get('watts_medio'), v)
                 for b, v in zip(ons_fim, valores_por_bloco)
                 if b.get('watts_medio') is not None and v is not None]
        grupos = {}
        for w, v in pares:
            grupos.setdefault(round(w / 10) * 10, []).append(v)
        rep = [vs for vs in grupos.values() if len(vs) > 1]
        if not rep:
            return None, None, 0
        rels = []
        for vs in rep:
            m = _declive(vs)
            if m is not None and amplitude:
                rels.append(m * max(1, len(vs) - 1) / amplitude)
        if not rels:
            return None, None, len(rep)
        rr = sum(rels) / len(rels)
        if rr >= corte_claro:
            n2 = 'Clear increase'
        elif rr >= corte_ligeiro:
            n2 = 'Slight increase'
        elif rr > -corte_ligeiro:
            n2 = 'Steady'
        elif rr > -corte_claro:
            n2 = 'Slight decrease'
        else:
            n2 = 'Clear decrease'
        return n2, round(rr * 100, 1), len(rep)

    # Carga repetida: so' faz sentido em protocolos que repetem o mesmo
    # escalao. Num teste em escada crescente nao ha repeticoes, e a
    # pergunta fica sem base -- por isso pode ser desligada em vez de
    # contar zero e puxar o score para baixo.
    if True:
        n, rel, ng = repetida(thb_on, a_thb)
        r['8A'] = {'resposta': n, 'declive_pct_da_amplitude': rel,
                   'n_escaloes_repetidos': ng}
        hr_on = [_media(_recortar(hr, tempo, b['t0'], b['t1'], cauda))
                 for b in ons_fim] if hr else []
        n, rel, ng = repetida(hr_on, a_hr)
        r['13'] = {'resposta': n, 'declive_pct_da_amplitude': rel,
                   'n_escaloes_repetidos': ng}
        if not r['8A']['n_escaloes_repetidos'] and \
                not r['13']['n_escaloes_repetidos']:
            r['_sem_repeticoes'] = True

    # atraso THb -> SmO2 no inicio da recuperacao
    atrasos = []
    if thb:
        for b in offs_fim:
            jan_t = [i for i in range(min(len(tempo), len(smo2)))
                     if b['t0'] <= tempo[i] <= min(b['t1'], b['t0'] + 60)]
            if len(jan_t) < 10:
                continue
            s = [smo2[i] for i in jan_t]
            t_ = [thb[i] for i in jan_t if i < len(thb)]
            if len(t_) < 10:
                continue
            i_s = jan_t[s.index(min(s))]
            i_t = jan_t[t_.index(min(t_))] if len(t_) == len(jan_t) else None
            if i_t is not None:
                atrasos.append(tempo[i_s] - tempo[i_t])
    if atrasos:
        med = sorted(atrasos)[len(atrasos) // 2]
        resp = ('>3 seconds' if med > 3 else
                '1-3 seconds' if med >= 1 else 'No Delay')
        r['9'] = {'resposta': resp, 'atraso_mediano_s': round(med, 1),
                  'n_recuperacoes': len(atrasos)}
    else:
        r['9'] = {'resposta': None, 'motivo': 'sem THb ou recuperações curtas'}

    return {'ok': True, 'respostas': r,
            'repouso_seg': REPOUSO_SEG,
            'n_blocos_trabalho': len(ons),
            'n_blocos_usados': len(ons_fim),
            'n_blocos_repouso_usados': len(offs_fim),
            'cauda_s': cauda,
            'sem_repeticoes_de_carga': r.pop('_sem_repeticoes', False),
            'fraccao_final': fraccao_final,
            'amplitudes': {'smo2': round(a_smo2, 1) if a_smo2 else None,
                           'thb': round(a_thb, 2) if a_thb else None,
                           'heartrate': round(a_hr, 1) if a_hr else None},
            'tem_thb': bool(thb), 'tem_hr': bool(hr)}


def pontuar(respostas, tem_thb=True, tem_hr=True, tem_segundo_musculo=False,
            excluir_carga_repetida=None):
    """Pontos e score nos dois eixos, com o denominador ajustado."""
    det_us, det_pc = [], []
    pontos_us = pontos_pc = 0.0
    max_us = max_pc = 0.0

    for q, escala in ESCALA_US.items():
        if q in ('4B', '5B') and not tem_segundo_musculo:
            continue
        resp = (respostas.get(q) or {}).get('resposta')
        disponivel = max(escala.values())
        max_us += disponivel
        p = escala.get(resp)
        det_us.append({'pergunta': q, 'texto': PERGUNTAS.get(q, q),
                       'resposta': resp, 'pontos': p,
                       'max': disponivel})
        if p is not None:
            pontos_us += p

    for q, escala in ESCALA_PC.items():
        if q in ('6A', '7A', '8A', '9') and not tem_thb:
            continue
        if q in ('10', '11', '12', '13') and not tem_hr:
            continue
        # As perguntas de carga repetida so' fazem sentido em protocolos
        # que repetem o mesmo escalao. Neste atleta a escada e' sempre
        # crescente, e conta-las como zero baixava o score sem razao. Fora
        # do numerador E do denominador -- que e a diferenca entre "nao se
        # aplica" e "resposta zero".
        nao_ap = (respostas.get(q) or {}).get('nao_aplicavel')
        if q in ('8A', '13') and (
                nao_ap if excluir_carga_repetida is None
                else excluir_carga_repetida):
            det_pc.append({'pergunta': q, 'texto': PERGUNTAS.get(q, q),
                           'resposta': None, 'pontos': None,
                           'max': max(escala.values()),
                           'nao_aplicavel': True})
            continue
        resp = (respostas.get(q) or {}).get('resposta')
        disponivel = max(escala.values())
        max_pc += disponivel
        p = escala.get(resp)
        det_pc.append({'pergunta': q, 'texto': PERGUNTAS.get(q, q),
                       'resposta': resp, 'pontos': p,
                       'max': disponivel})
        if p is not None:
            pontos_pc += p

    score_us = (pontos_us / max_us) if max_us else None
    score_pc = (pontos_pc / max_pc) if max_pc else None

    sem_resposta = [d['pergunta'] for d in det_us + det_pc
                    if d['resposta'] is None and not d.get('nao_aplicavel')]
    nao_aplicaveis = [d['pergunta'] for d in det_us + det_pc
                      if d.get('nao_aplicavel')]

    return {
        'us': {'pontos': round(pontos_us, 2), 'max': round(max_us, 2),
               'score': round(score_us, 3) if score_us is not None else None,
               'detalhe': det_us},
        'pc': {'pontos': round(pontos_pc, 2), 'max': round(max_pc, 2),
               'score': round(score_pc, 3) if score_pc is not None else None,
               'detalhe': det_pc},
        'sem_resposta': sem_resposta,
        'nao_aplicaveis': nao_aplicaveis,
    }


def interpretar(score_us, score_pc, sem_resposta=None, avisos=None):
    """Traduz os dois scores em limitador, com as reservas devidas."""
    out = {'us': None, 'pc': None, 'resumo': None}
    if score_us is not None:
        if score_us >= 0.55:
            out['us'] = {
                'limitador': 'Utilização',
                'texto': ('o SmO2 de trabalho fica alto: o músculo não está '
                          'a extrair o oxigénio que lhe chega. O fornecimento '
                          'não é o travão')}
        elif score_us <= 0.30:
            out['us'] = {
                'limitador': 'Fornecimento',
                'texto': ('o SmO2 desce muito e continua a descer: a '
                          'extracção está no limite e falta entrega')}
        else:
            out['us'] = {'limitador': 'Misto',
                         'texto': 'sem predomínio claro entre os dois'}
    if score_pc is not None:
        if score_pc >= 0.35:
            out['pc'] = {
                'limitador': 'Pulmonar',
                'texto': ('THb e SmO2 de repouso a subir, com atraso na '
                          'resposta: o padrão aponta para o lado ventilatório')}
        elif score_pc <= -0.15:
            out['pc'] = {
                'limitador': 'Cardíaco',
                'texto': ('THb a descer ao longo da sessão: o volume de '
                          'sangue local não acompanha')}
        else:
            out['pc'] = {'limitador': 'Misto', 'texto': 'sem predomínio claro'}

    partes = [f"{v['limitador']} ({k.upper()})"
              for k, v in (('us', out['us']), ('pc', out['pc'])) if v]
    out['resumo'] = ' · '.join(partes) if partes else 'sem dados suficientes'
    out['reservas'] = []
    if sem_resposta:
        out['reservas'].append(
            f"{len(sem_resposta)} pergunta(s) sem resposta medível "
            f"({', '.join(sem_resposta)}): contam zero, o que empurra os "
            f"scores para baixo")
    for a in (avisos or []):
        out['reservas'].append(a)
    out['reservas'].append(
        'os cortes entre "clear" e "slight" são decisão de quem construiu '
        'isto, não uma constante: o ficheiro original pedia ao utilizador '
        'que olhasse e decidisse')
    return out


def avaliar(tempo, canais, blocos, pct_artefacto=None, **kw):
    """Medir, pontuar e interpretar, com recusa quando os dados não prestam."""
    avisos = []
    if pct_artefacto is not None and pct_artefacto > 30:
        avisos.append(f'{round(pct_artefacto)}% dos pontos com artefacto na '
                      'cinta: as respostas sobre FC não são de confiança')
    kw_medir = {k: v for k, v in kw.items()
                if k != 'excluir_carga_repetida'}
    m = medir(tempo, canais, blocos, **kw_medir)
    if not m.get('ok'):
        return {'ok': False, 'motivo': m.get('motivo'), 'avisos': avisos}
    # None = decidir pelos dados (n/a quando nao ha escaloes repetidos);
    # True/False = forcado pelo utilizador
    p = pontuar(m['respostas'], tem_thb=m['tem_thb'], tem_hr=m['tem_hr'],
                excluir_carga_repetida=kw.get('excluir_carga_repetida'))
    if p.get('nao_aplicaveis'):
        avisos.append(
            f"perguntas {', '.join(p['nao_aplicaveis'])} não se aplicam: a "
            'sessão não repete o mesmo escalão de carga. Ficaram fora do '
            'numerador e do denominador, não contadas como zero')
    i = interpretar(p['us']['score'], p['pc']['score'],
                    p['sem_resposta'], avisos)
    return {'ok': True, 'medicoes': m, 'pontuacao': p, 'interpretacao': i,
            'avisos': avisos,
            'cortes': {'claro_pct': CORTE_CLARO * 100,
                       'ligeiro_pct': CORTE_LIGEIRO * 100,
                       'fraccao_final': kw.get('fraccao_final', FRACCAO_FINAL)}}


# ══════════════════════════════════════════════════════════════════════════
# MINI-FIGURAS
#
# O ficheiro original tem, ao lado de cada pergunta, um desenho a mao do
# padrao que se procura. Sao 35 formas do Excel, nao imagens, por isso nao
# se extraem -- desenham-se aqui em SVG, o que sai mais nitido e acompanha
# o tema da pagina.
#
# Cada figura mostra o sinal ao longo de varios intervalos, com a linha de
# tendencia por cima, exactamente como no ficheiro.
# ══════════════════════════════════════════════════════════════════════════

def _dentes(n_int, base, delta_por_int, profundidade, largura=120,
            altura=34, invertido=False):
    """Serie em dentes: cada intervalo desce (ou sobe) e recupera."""
    pts = []
    passos = 14
    for k in range(n_int):
        b = base + delta_por_int * k
        for j in range(passos):
            f = j / (passos - 1)
            if f < 0.6:
                v = b - profundidade * (f / 0.6)
            else:
                v = b - profundidade * (1 - (f - 0.6) / 0.4)
            pts.append(v)
    n = len(pts)
    lo, hi = min(pts), max(pts)
    amp = (hi - lo) or 1
    xs = [4 + i * (largura - 8) / max(1, n - 1) for i in range(n)]
    ys = [altura - 4 - (v - lo) / amp * (altura - 10) for v in pts]
    if invertido:
        ys = [altura - y for y in ys]
    return xs, ys, pts, lo, amp


def figura(tipo, nivel, largura=120, altura=34):
    """SVG de uma das cinco tendencias, para o canal indicado.

    tipo: 'smo2_repouso' | 'smo2_min' | 'smo2_dentro' | 'thb' | 'hr'
    """
    cores = {'smo2_repouso': '#3FB950', 'smo2_min': '#3FB950',
             'smo2_dentro': '#3FB950', 'thb': '#F0883E', 'hr': '#E3B341'}
    cor = cores.get(tipo, '#8b949e')
    delta = {'Clear increase': 3.0, 'Slight increase': 1.2, 'Steady': 0.0,
             'Slight decrease': -1.2, 'Clear decrease': -3.0}.get(nivel, 0.0)

    if tipo == 'smo2_dentro':
        # a tendencia e' DENTRO de cada dente, nao entre eles
        pts = []
        for k in range(5):
            passos = 14
            for j in range(passos):
                f = j / (passos - 1)
                fundo = 10 + delta * 1.6
                v = 30 - (30 - (30 - fundo)) * 0 - fundo * min(1.0, f / 0.6) \
                    if f < 0.6 else 30 - fundo * (1 - (f - 0.6) / 0.4)
                pts.append(v)
        n = len(pts)
        lo, hi = min(pts), max(pts)
        amp = (hi - lo) or 1
        xs = [4 + i * (largura - 8) / max(1, n - 1) for i in range(n)]
        ys = [altura - 4 - (v - lo) / amp * (altura - 10) for v in pts]
    else:
        prof = 14 if tipo.startswith('smo2') else 6
        xs, ys, pts, lo, amp = _dentes(5, 30, delta, prof, largura, altura)

    d = 'M ' + ' L '.join(f'{x:.1f} {y:.1f}' for x, y in zip(xs, ys))

    # linha de tendencia: sobre os picos (repouso) ou os vales (minimo)
    n_int = 5
    passos = len(xs) // n_int
    if tipo in ('smo2_min',):
        idx = [k * passos + int(passos * 0.6) for k in range(n_int)]
    else:
        idx = [k * passos for k in range(n_int)]
    idx = [min(i, len(xs) - 1) for i in idx]
    x0, y0 = xs[idx[0]], ys[idx[0]]
    x1, y1 = xs[idx[-1]], ys[idx[-1]]

    return (
        f'<svg width="{largura}" height="{altura}" viewBox="0 0 {largura} '
        f'{altura}" xmlns="http://www.w3.org/2000/svg">'
        f'<path d="{d}" fill="none" stroke="{cor}" stroke-width="1.4"/>'
        f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
        f'stroke="#8b949e" stroke-width="1.2" stroke-dasharray="3,2"/>'
        f'</svg>')


TIPO_DA_PERGUNTA = {
    '3A': 'smo2_repouso', '4A': 'smo2_min', '5A': 'smo2_dentro',
    '6A': 'thb', '7A': 'thb', '8A': 'thb',
    '10': 'hr', '11': 'hr', '12': 'hr', '13': 'hr',
}


def figuras_das_perguntas(largura=120, altura=34):
    """{pergunta: {nivel: svg}} para as perguntas de tendencia."""
    out = {}
    for q, tipo in TIPO_DA_PERGUNTA.items():
        out[q] = {n: figura(tipo, n, largura, altura) for n in NIVEIS}
    return out
