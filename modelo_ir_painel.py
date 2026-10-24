"""Etapa 15: vista do IR sombra para o painel (funcoes PURAS, sem base de dados).

Entrada: a resposta JSON de GET /api/modelo/ir/validacao, obtida para UMA selecao (modalidade, duracao, modo).
Saida: estados explicitos, natureza da avaliacao, contagens, rejeicoes e lacunas, series SEPARADAS (previsao
experimental da sombra e referencia observada) e metricas so quando ha alvos avaliaveis.

Regras:
  * nada aqui escreve, recalibra, sincroniza ou promove um candidato;
  * a sombra e experimental: a previsao 'ir' so aparece se o walk-forward a calculou;
  * o modo retrospectivo usa valores ATUAIS e nunca recebe rotulo de validacao temporal;
  * o IR operacional (180 s, MMP3) NAO tem validacao temporal neste endpoint (so 60/300/1200 s): bloqueio explicito;
  * a curva operacional (% do P0) e a previsao da sombra (W) nunca partilham a mesma serie.
"""
import modelo_ir_contrato as mct

MODALIDADES = ('Bike', 'Row', 'Ski', 'Run')
DURACOES_VISIVEIS = (60, 300, 1200)          # o que o endpoint de validacao aceita (protocolo.TESTES)
DURACAO_OPERACIONAL = 180                    # IR operacional: MMP3 de 180 s (modelo_mmp3.DURACAO_S)
MODOS = ('retrospectivo', 'conhecido_na_epoca')

# Estados canonicos da API (iguais aos de modelo_ir_contrato).
ESTADO_DISPONIVEL = mct.ESTADO_DISPONIVEL
ESTADO_INCOMPLETO = mct.ESTADO_INCOMPLETO
ESTADO_VAZIO = mct.ESTADO_VAZIO
ESTADO_INDISPONIVEL = mct.ESTADO_INDISPONIVEL
ESTADOS = (ESTADO_DISPONIVEL, ESTADO_INCOMPLETO, ESTADO_VAZIO, ESTADO_INDISPONIVEL)

# Constantes de suficiencia de modelo_validacao (copiadas; test_ir_painel_sombra verifica que coincidem com V.EST_*).
SUF_SEM_ALVOS = 'inconclusivo_sem_alvos'
SUF_DESCRITIVO = 'descritivo'
SUF_AVALIAVEL = 'avaliavel'

ROTULO_MODO = {
    'retrospectivo': 'Ajuste retrospectivo (valores atuais)',
    'conhecido_na_epoca': 'Previsao temporal (so com informacao conhecida na epoca)',
}
ROTULO_NATUREZA = {
    mct.NATUREZA_RETROSPECTIVA: 'Retrospectiva com valores atuais: nao e validacao temporal',
    mct.NATUREZA_VALIDADA: 'Temporal validada',
    mct.NATUREZA_INCOMPLETA: 'Temporal incompleta: ha alvos sem informacao conhecida na epoca',
    mct.NATUREZA_BLOQUEADA: 'Bloqueada: historico obrigatorio insuficiente, nada foi avaliado',
}
ROTULO_ESTADO = {
    ESTADO_DISPONIVEL: 'disponivel',
    ESTADO_INCOMPLETO: 'incompleto',
    ESTADO_VAZIO: 'vazio',
    ESTADO_INDISPONIVEL: 'indisponivel',
}
ROTULO_SUFICIENCIA = {
    SUF_SEM_ALVOS: 'sem alvos para avaliar',
    SUF_DESCRITIVO: 'amostra abaixo do minimo: apenas descritivo',
    SUF_AVALIAVEL: 'amostra suficiente para avaliar',
}

AVISO_OPERACIONAL = ('O IR operacional usa o MMP3 de 180 s. Esta validacao temporal so cobre 60/300/1200 s, por isso '
                     'o IR operacional NAO tem validacao temporal aqui. Bloqueio documentado; nao foi contornado.')
AVISO_EXPERIMENTAL = ('Sombra experimental: a previsao nao substitui o IR operacional, nao recalibra nada e nao '
                      'promove nenhum candidato.')
AVISO_RETROSPECTIVO = 'Modo retrospectivo: usa y e estado ATUAIS. Nao e validacao historica.'


def _lista(x):
    return x if isinstance(x, list) else []


def _dict(x):
    return x if isinstance(x, dict) else {}


def _rejeicoes(item):
    """Lacunas: alvos sem previsao avaliavel, com data e motivo. Agrupadas por motivo."""
    lacunas = []
    for r in _lista(item.get('alvos_rejeitados')):
        if isinstance(r, dict):
            lacunas.append({'test_id': r.get('test_id'), 'data': r.get('data'), 'motivo': r.get('motivo')})
    por_motivo = {}
    for r in lacunas:
        k = r['motivo'] or 'sem_motivo'
        por_motivo[k] = por_motivo.get(k, 0) + 1
    return lacunas, dict(sorted(por_motivo.items()))


def _pontos(previsoes):
    """Duas series SEPARADAS. A sombra so tem pontos onde a previsao foi calculada; a referencia so onde y existe.
    Nunca se interpola, e os baselines (M0/M1) nao entram nestas series."""
    sombra, referencia = [], []
    for p in sorted((x for x in _lista(previsoes) if isinstance(x, dict)), key=lambda x: str(x.get('data'))):
        if p.get('ir') is not None:
            sombra.append({'data': p.get('data'), 'valor': p.get('ir'), 'test_id': p.get('test_id')})
        if p.get('y') is not None:
            referencia.append({'data': p.get('data'), 'valor': p.get('y'), 'test_id': p.get('test_id')})
    return sombra, referencia


def _periodo(previsoes):
    datas = sorted(str(p.get('data')) for p in _lista(previsoes) if isinstance(p, dict) and p.get('data'))
    return [datas[0], datas[-1]] if datas else None


def _metricas(item, n_avaliaveis):
    """MAE, RMSE e vies SO com alvos avaliaveis. Compara a sombra com M0 e M1 (referencias simples).
    Sem intervalos de confianca: nao ha metodo validado para os mostrar nesta vista."""
    if n_avaliaveis <= 0:
        return None
    m = _dict(item.get('metricas'))
    if not all(k in m for k in ('IR', 'M0', 'M1')):
        return None
    comp = {}
    ir_mae = _dict(m.get('IR')).get('mae')
    for base in ('M0', 'M1'):
        b_mae = _dict(m.get(base)).get('mae')
        comp[base] = round(ir_mae - b_mae, 4) if ir_mae is not None and b_mae is not None else None
    return {'por_modelo': {k: _dict(m.get(k)) for k in ('IR', 'M0', 'M1', 'M2') if k in m},
            'delta_mae_ir_menos_referencia': comp,
            'n_alvos': n_avaliaveis}


def _suficiencia(item):
    suf = _dict(item.get('suficiencia'))
    est = item.get('estado_suficiencia')
    return {'estado': est, 'rotulo': ROTULO_SUFICIENCIA.get(est, 'desconhecido'),
            'criterios': _dict(suf.get('criterios')),
            'observado': {k: suf.get(k) for k in ('n_alvos', 'n_datas_distintas', 'dias_cobertura',
                                                   'primeira', 'ultima')}}


def _evidencia_para_revisao(natureza, suficiencia, veredicto):
    """Verdadeiro so se a avaliacao e TEMPORAL (nunca retrospectiva), suficiente e favoravel.
    Nunca promove: indica apenas que os dados justificam analisar uma futura revisao do candidato."""
    ok = (natureza == mct.NATUREZA_VALIDADA and suficiencia.get('estado') == SUF_AVALIAVEL
          and veredicto == 'ir_melhor_que_ambos_baselines')
    return {'existe': ok,
            'texto': ('Dados suficientes para analisar uma futura revisao do candidato. Nao promovido: '
                      'a decisao cabe a revisao humana.') if ok else
                     'Sem evidencia suficiente para uma futura revisao do candidato.'}


def _estado_visivel(modo, bloqueado, hist_t, hist_c, n_linha, n_rejeitados):
    """Estado do grafico, com as mesmas quatro categorias da API.
    Regra unica para os DOIS modos: qualquer alvo rejeitado torna a vista 'incompleto' (as lacunas tem de aparecer,
    mesmo sem pontos). So o modo temporal depende dos historicos; o retrospectivo usa valores atuais."""
    if modo == 'conhecido_na_epoca':
        if ESTADO_INDISPONIVEL in (hist_t, hist_c):
            return ESTADO_INDISPONIVEL
        if bloqueado or ESTADO_VAZIO in (hist_t, hist_c):
            return ESTADO_VAZIO
    if n_linha == 0 and n_rejeitados == 0:       # nada calculado e nada rejeitado: nao ha o que mostrar
        return ESTADO_VAZIO
    if n_rejeitados > 0 or n_linha == 0:
        return ESTADO_INCOMPLETO
    if modo == 'conhecido_na_epoca' and ESTADO_INCOMPLETO in (hist_t, hist_c):
        return ESTADO_INCOMPLETO
    return ESTADO_DISPONIVEL


def _vista_indisponivel(modalidade, duracao, modo, mensagem, origem='pedido_falhou'):
    evid = _evidencia_para_revisao(None, {}, None)
    return {'estado': ESTADO_INDISPONIVEL, 'origem': origem, 'mensagem': mensagem,
            'modalidade': modalidade, 'duracao_s': duracao, 'modo': modo,
            'rotulo_modo': ROTULO_MODO.get(modo), 'natureza': None, 'natureza_rotulo': None,
            'nao_validacao_historica': modo == 'retrospectivo', 'validacao_temporal': False,
            'bloqueado': False, 'motivo_bloqueio': None,
            'historico_temporal': {'estado': ESTADO_INDISPONIVEL, 'rotulo': 'indisponivel'},
            'historico_curvas': {'estado': ESTADO_INDISPONIVEL, 'rotulo': 'indisponivel'},
            # null = nao avaliado (nada foi lido): nunca zero, que seria uma medicao
            'contagens': {'previstos': None, 'avaliaveis': None, 'rejeitados': None, 'previsto_nao_avaliavel': None},
            'rejeicoes_por_categoria': {}, 'rejeicoes_por_motivo': {}, 'lacunas': [],
            'serie': {'sombra': [], 'referencia_observada': []}, 'periodo': None,
            'metricas': None, 'suficiencia': None, 'veredicto': None, 'evidencia_para_revisao': evid,
            'limitacoes': [], 'avisos': [AVISO_EXPERIMENTAL, AVISO_OPERACIONAL]}


def montar(resposta, modalidade, duracao, modo):
    """Vista de UMA selecao. resposta: JSON de /api/modelo/ir/validacao (ou None se o pedido falhou).
    Nunca levanta por campo ausente: o que falta vira 'indisponivel' ou fica vazio, nunca inventado."""
    if modalidade not in MODALIDADES or modo not in MODOS:
        return _vista_indisponivel(modalidade, duracao, modo, 'Seleccao invalida.', 'parametros')
    if duracao not in DURACOES_VISIVEIS:
        return _vista_indisponivel(modalidade, duracao, modo,
                                   'Duracao sem validacao temporal neste endpoint (apenas 60, 300 ou 1200 s).', 'parametros')
    r = _dict(resposta)
    if r.get('status') != 'OK':
        msg = r.get('mensagem') or 'Pedido de validacao indisponivel.'
        return _vista_indisponivel(modalidade, duracao, modo, str(msg), 'pedido_falhou')

    rm = _dict(_dict(r.get('resultado_por_modo')).get(modo))
    if not rm:
        return _vista_indisponivel(modalidade, duracao, modo, 'A resposta nao contem o modo pedido.', 'resposta')
    itens = [i for i in _lista(r.get('itens')) if isinstance(i, dict)
             and i.get('modalidade') == modalidade and i.get('duracao_s') == duracao
             and i.get('modo', modo) == modo]
    if len(itens) > 1:   # uma unidade tem no maximo um item por modo: mais do que um e contradicao
        return _vista_indisponivel(modalidade, duracao, modo,
                                   'Resposta ambigua: varios itens para a mesma selecao. Nada foi mostrado.', 'resposta')
    item = itens[0] if itens else {}

    natureza = _dict(r.get('natureza_avaliacao')).get(modo) or rm.get('natureza_avaliacao')
    bloqueado = bool(rm.get('bloqueado'))
    hist_t = r.get('historico_temporal_estado') or ESTADO_INDISPONIVEL
    hist_c = r.get('historico_curvas_estado') or ESTADO_INDISPONIVEL
    previsoes = _lista(item.get('previsoes'))
    n_linha = len(previsoes)
    n_prev = int(item.get('n_alvos_previstos', rm.get('n_alvos_previstos', 0)) or 0)
    n_aval = int(item.get('n_alvos_avaliaveis', n_linha) or 0)
    n_rej = int(item.get('n_alvos_rejeitados', 0) or 0)
    lacunas, por_motivo = _rejeicoes(item)
    estado = _estado_visivel(modo, bloqueado, hist_t, hist_c, n_linha, n_rej)
    sombra, referencia = _pontos(previsoes)
    suf = _suficiencia(item) if item else {}
    veredicto = item.get('veredicto') if suf.get('estado') == SUF_AVALIAVEL else None
    avisos = [AVISO_EXPERIMENTAL, AVISO_OPERACIONAL]
    if modo == 'retrospectivo':
        avisos.insert(0, AVISO_RETROSPECTIVO)

    origem = 'historico' if estado == ESTADO_INDISPONIVEL else None
    return {
        'estado': estado,
        'origem': origem,
        'mensagem': None,
        'modalidade': modalidade, 'duracao_s': duracao, 'modo': modo,
        'rotulo_modo': ROTULO_MODO[modo],
        'natureza': natureza,
        'natureza_rotulo': ROTULO_NATUREZA.get(natureza, 'Natureza desconhecida'),
        'nao_validacao_historica': modo == 'retrospectivo',
        'validacao_temporal': natureza in (mct.NATUREZA_VALIDADA, mct.NATUREZA_INCOMPLETA),
        'bloqueado': bloqueado,
        'motivo_bloqueio': rm.get('motivo_bloqueio'),
        'historico_temporal': {'estado': hist_t, 'rotulo': ROTULO_ESTADO.get(hist_t, hist_t)},
        'historico_curvas': {'estado': hist_c, 'rotulo': ROTULO_ESTADO.get(hist_c, hist_c)},
        # Sem validacao executada (histerico indisponivel ou bloqueado) as contagens sao desconhecidas: null, nunca 0.
        'contagens': ({'previstos': None, 'avaliaveis': None, 'rejeitados': None, 'previsto_nao_avaliavel': None}
                      if (estado == ESTADO_INDISPONIVEL or bloqueado) else
                      {'previstos': n_prev, 'avaliaveis': n_aval, 'rejeitados': n_rej,
                       'previsto_nao_avaliavel': max(n_prev - n_aval, 0)}),
        'rejeicoes_por_categoria': _dict(item.get('rejeicoes_por_categoria')),
        'rejeicoes_por_motivo': por_motivo,
        'lacunas': lacunas,
        'serie': {'sombra': sombra, 'referencia_observada': referencia},
        'periodo': _periodo(previsoes),
        'metricas': _metricas(item, n_aval),
        'suficiencia': suf or None,
        'veredicto': veredicto,
        'evidencia_para_revisao': _evidencia_para_revisao(natureza, suf, veredicto),
        'limitacoes': [t for t in _lista(r.get('limitacoes')) if isinstance(t, str)],
        'avisos': avisos,
    }
