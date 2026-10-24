"""Contrato temporal da API /api/modelo/ir/validacao (Etapa 14). Funcoes PURAS: nao leem a base de dados.

Quem decide o que e 'validacao temporal' e este modulo, a partir dos estados dos historicos e das contagens de
rejeicao. O endpoint so o chama; nao reinterpreta os numeros.

NATUREZA POR MODO (campo `natureza_avaliacao`):
  retrospectivo                -> 'retrospectiva_valores_atuais'   (usa y e estado ATUAIS; NAO e validacao temporal)
  conhecido_na_epoca, bloqueado -> 'bloqueada_por_historico_insuficiente' (nada foi avaliado: historico obrigatorio
                                   indisponivel ou vazio; o endpoint nao chama o avaliador)
  conhecido_na_epoca, avaliado, sem rejeicoes por falta de historico -> 'temporal_validada'
  conhecido_na_epoca, avaliado, com alvos rejeitados por falta de historico -> 'temporal_incompleta'

ESTADOS DOS HISTORICOS (campos `historico_temporal_estado` e `historico_curvas_estado`):
  disponivel   tabelas existem, com registos, e nenhum alvo foi rejeitado por falta desse historico.
  incompleto   tabelas existem com registos, mas ha alvos rejeitados por falta de historico anterior ao alvo.
  vazio        tabelas existem mas sem nenhum registo. Bloqueia a avaliacao temporal.
  indisponivel tabelas ausentes, base indisponivel ou erro de leitura. Bloqueia a avaliacao temporal.

CATEGORIAS DE REJEICAO POR HISTORICO:
  temporal: marcos, marcos_indisponiveis, yes, discordancia (ver modelo_ir_servico.CATEGORIAS_HISTORICAS).
  curvas:   curvas (alvo anterior ao primeiro instante do historico de curvas, ou sem versao conhecida antes do alvo).
  versao_alvo (historico de deteccao) NAO conta como historico temporal: e uma rejeicao propria.
"""

NATUREZA_RETROSPECTIVA = 'retrospectiva_valores_atuais'
NATUREZA_VALIDADA = 'temporal_validada'
NATUREZA_INCOMPLETA = 'temporal_incompleta'
NATUREZA_BLOQUEADA = 'bloqueada_por_historico_insuficiente'

ESTADO_DISPONIVEL = 'disponivel'
ESTADO_INCOMPLETO = 'incompleto'
ESTADO_VAZIO = 'vazio'
ESTADO_INDISPONIVEL = 'indisponivel'

CATEGORIAS_TEMPORAIS = ('marcos', 'marcos_indisponiveis', 'yes', 'discordancia')
CATEGORIA_CURVAS = 'curvas'

DEFINICOES_ESTADO = {
    ESTADO_DISPONIVEL: 'Historico com registos; nenhum alvo rejeitado por falta deste historico antes do alvo.',
    ESTADO_INCOMPLETO: 'Historico com registos, mas ha alvos rejeitados por falta de informacao conhecida antes deles.',
    ESTADO_VAZIO: 'Tabelas existem sem nenhum registo. A avaliacao temporal fica bloqueada.',
    ESTADO_INDISPONIVEL: 'Tabelas ausentes, base indisponivel ou erro de leitura. A avaliacao temporal fica bloqueada.',
}

LIMITACAO_RETROSPECTIVO = ('Modo retrospectivo: usa y e estado ATUAIS. Nao e validacao historica e nao prova '
                           'capacidade preditiva no momento do esforco.')
LIMITACAO_DIA = ('Instantes de conhecimento com granularidade de dia: versoes do mesmo dia nao se distinguem.')
LIMITACAO_SEM_MIGRACAO = ('Sem a migracao temporal ou de curvas aplicada nao ha registo de conhecimento; '
                          'nenhum resultado temporal e produzido.')


def motivo_bloqueio(leitor_estado, contagem_temporal, curvas_estado, contagem_curvas):
    """None se os dois historicos obrigatorios estao disponiveis e tem registos; senao o motivo explicito.
    contagens: (n_snapshots_marcos, n_transicoes_yes) e (n_atividades, n_versoes); None se nao lidas."""
    if leitor_estado != 'ok' or contagem_temporal is None:
        return 'historico_temporal_indisponivel'
    if tuple(contagem_temporal) == (0, 0):
        return 'historico_temporal_vazio'
    if curvas_estado != 'ok' or contagem_curvas is None:
        return 'historico_curvas_indisponivel'
    if int(contagem_curvas[1]) == 0:
        return 'historico_curvas_vazio'
    return None


def estado_historico_temporal(leitor_estado, contagem_temporal, rejeicoes_temporais):
    """Estado canonico do historico temporal (marcos + YES) para o conjunto de alvos avaliado."""
    if leitor_estado != 'ok' or contagem_temporal is None:
        return ESTADO_INDISPONIVEL
    if tuple(contagem_temporal) == (0, 0):
        return ESTADO_VAZIO
    return ESTADO_INCOMPLETO if rejeicoes_temporais > 0 else ESTADO_DISPONIVEL


def estado_historico_curvas(curvas_estado, contagem_curvas, rejeicoes_curvas):
    """Estado canonico do historico de curvas para o conjunto de alvos avaliado."""
    if curvas_estado != 'ok' or contagem_curvas is None:
        return ESTADO_INDISPONIVEL
    if int(contagem_curvas[1]) == 0:
        return ESTADO_VAZIO
    return ESTADO_INCOMPLETO if rejeicoes_curvas > 0 else ESTADO_DISPONIVEL


def _somar_dicts(dicts):
    out = {}
    for d in dicts:
        for k, v in (d or {}).items():
            out[k] = out.get(k, 0) + v
    return {k: v for k, v in sorted(out.items()) if v}


def resumo_de_itens(itens):
    """Totais de um modo a partir dos itens de validar_preditivo. Previstos = IR produziu previsao;
    avaliaveis = previsao com baselines calculados; rejeitados = alvos fora da avaliacao (com motivo)."""
    previstos = sum(int(i.get('n_alvos_previstos', i.get('n_alvos_com_previsao_ir', 0))) for i in itens)
    avaliaveis = sum(int(i.get('n_alvos_avaliaveis', 0)) for i in itens)
    rejeitados = sum(int(i.get('n_alvos_rejeitados', 0)) for i in itens)
    return {
        'n_alvos_previstos': previstos,
        'n_alvos_avaliaveis': avaliaveis,
        'n_alvos_rejeitados': rejeitados,
        'rejeicoes_por_categoria': _somar_dicts(i.get('rejeicoes_por_categoria') for i in itens),
        'rejeicoes_por_motivo': _somar_dicts(i.get('alvos_sem_previsao_ir') for i in itens),
    }


def natureza_do_modo(modo, bloqueado, rejeicoes_historicas):
    if modo == 'retrospectivo':
        return NATUREZA_RETROSPECTIVA
    if bloqueado:
        return NATUREZA_BLOQUEADA
    return NATUREZA_INCOMPLETA if rejeicoes_historicas > 0 else NATUREZA_VALIDADA


def montar(modos, itens_por_modo, motivos_bloqueio, leitor_estado, contagem_temporal,
           curvas_estado, contagem_curvas):
    """Contrato completo. modos: os pedidos (ex.: ('retrospectivo', 'conhecido_na_epoca')).
    itens_por_modo: {modo: [itens]}. motivos_bloqueio: {modo: motivo} para os modos nao avaliados."""
    itens_por_modo = itens_por_modo or {}
    motivos_bloqueio = motivos_bloqueio or {}
    limitacoes = []
    resultado = {}
    rejeicoes_temporais_total = 0
    rejeicoes_curvas_total = 0
    for modo in modos:
        bloqueado = modo in motivos_bloqueio
        itens = [] if bloqueado else itens_por_modo.get(modo, [])
        resumo = resumo_de_itens(itens)
        cat = resumo['rejeicoes_por_categoria']
        rej_temp = sum(cat.get(c, 0) for c in CATEGORIAS_TEMPORAIS)
        rej_curv = cat.get(CATEGORIA_CURVAS, 0)
        if modo == 'conhecido_na_epoca' and not bloqueado:
            rejeicoes_temporais_total += rej_temp
            rejeicoes_curvas_total += rej_curv
        nat = natureza_do_modo(modo, bloqueado, rej_temp + rej_curv)
        resultado[modo] = {
            'natureza_avaliacao': nat,
            'validacao_temporal': nat in (NATUREZA_VALIDADA, NATUREZA_INCOMPLETA),
            'bloqueado': bloqueado,
            'motivo_bloqueio': motivos_bloqueio.get(modo),
            **resumo,
        }
    temporal_usado = 'conhecido_na_epoca' in resultado and not resultado['conhecido_na_epoca']['bloqueado']
    if temporal_usado:
        estado_t = estado_historico_temporal(leitor_estado, contagem_temporal, rejeicoes_temporais_total)
        estado_c = estado_historico_curvas(curvas_estado, contagem_curvas, rejeicoes_curvas_total)
    else:   # nada avaliado temporalmente: o estado descreve so o historico (sem rejeicoes atribuiveis)
        estado_t = estado_historico_temporal(leitor_estado, contagem_temporal, 0)
        estado_c = estado_historico_curvas(curvas_estado, contagem_curvas, 0)
    if 'retrospectivo' in resultado:
        limitacoes.append(LIMITACAO_RETROSPECTIVO)
    if 'conhecido_na_epoca' in resultado:
        limitacoes.append(LIMITACAO_DIA)
        if resultado['conhecido_na_epoca']['bloqueado']:
            limitacoes.append(LIMITACAO_SEM_MIGRACAO)
    return {
        'resultado_por_modo': resultado,
        'natureza_avaliacao': {m: r['natureza_avaliacao'] for m, r in resultado.items()},
        'historico_temporal_estado': estado_t,
        'historico_curvas_estado': estado_c,
        'definicoes_estado': DEFINICOES_ESTADO,
        'limitacoes': limitacoes,
    }
