"""Servico das observacoes MMP automaticas: le dados locais, deteta e regista versoes.

Liga a logica pura (modelo_observacoes_auto) a base de dados. Nao faz NENHUMA chamada
externa por si: as fronteiras de season (eventos SEASON_START) so sao pedidas por
`obter_marcos()`, que quem chama pode substituir/injetar; sem elas usa-se
config.SEASON_INICIO_MES e a fonte fica registada.

`atualizar()` e idempotente e so faz INSERT em modelo_observacoes_auto. Nunca toca em
power_curves, activities nem no registo manual de revisoes.
"""
import config
import db
import modelo_observacoes_auto as moa
import modelo_testes as mt
import protocolo

FONTE_SEASON_CALENDARIO = 'calendario_SEASON_START'
FONTE_SEASON_MES = 'fallback_SEASON_INICIO_MES'

LIMITACOES = (
    'A season e o all-time sao DERIVADOS das curvas por atividade sincronizadas; nao ha curva '
    'de season nem all-time guardada na Intervals.icu.',
    'O all-time cobre so a janela historica sincronizada (ANOS_HISTORICO anos): um recorde '
    'anterior nao e visto.',
    'As fronteiras de season vem dos eventos SEASON_START do calendario ou, na falta deles, '
    'de SEASON_INICIO_MES (a fonte usada fica registada em cada observacao).',
    'O limiar 0,92 aplicado a season e ao all-time e experimental; o do melhor recente e o '
    'existente. A amostra minima e a ja existente (>= 5 pontos por modalidade e duracao): '
    'cada referencia regista o seu n_pontos (pontos ANTERIORES; o proprio esforco nunca entra). '
    'O primeiro ponto de cada serie nao tem referencia e nao entra no estimador.',
    'Um maximo de curva (melhor valor da duracao em toda a atividade) nao e um teste '
    'laboratorial nem padronizado. Existirem observacoes nao valida o modelo.',
)


def obter_marcos():
    """(marcos, fonte). Chama o calendario da Intervals.icu (cache de 1 h); falhar nao e
    erro: cai em SEASON_INICIO_MES."""
    try:
        import api_client
        marcos = api_client.seasons_do_atleta()
        if marcos:
            return marcos, FONTE_SEASON_CALENDARIO
    except Exception:
        import traceback
        traceback.print_exc()
    return None, FONTE_SEASON_MES


def _ignorados_da_referencia(mapa, eleg, elegivel):
    """Atividades que NAO podem servir de referencia no detetor.

    Mesma regra do seletor (modelo_estimador.selecionar_observacoes_auto): so uma atividade
    'elegivel' pode ser referencia. Sai da pool: icu_ignore_power=true (mapa), e tambem toda a
    atividade com elegibilidade desconhecida (ex.: ignore_parts nao vazio). Antes so o primeiro
    caso era retirado: o detetor aceitava referencias que o seletor excluia como observacoes.
    """
    ign = {aid for aid, v in (mapa or {}).items() if v is True}
    ign |= {aid for aid, e in (eleg or {}).items() if (e or {}).get('estado') != elegivel}
    return ign


def _grupos(atividades):
    g = {}
    for a in atividades or []:
        if isinstance(a, dict) and a.get('id') is not None:
            g.setdefault(a['id'], []).append(a)
    return g


def criterio(fonte_season):
    return {'versao_criterio': moa.VERSAO_CRITERIO,
            'limiar_proximidade': moa.LIMIAR_PROXIMIDADE,
            'limiar_origem': {'recente': 'existente (protocolo.LIMIAR_ESFORCO)',
                              'season': 'experimental (mesmo valor)',
                              'all_time': 'experimental (mesmo valor)'},
            'janela_recente_dias': moa.JANELA_RECENTE,
            'min_pontos_serie': moa.MIN_PONTOS_SERIE,
            'min_pontos_janela_recente': moa.MIN_PONTOS_JANELA,
            'anos_historico': config.ANOS_HISTORICO,
            'season_fonte': fonte_season,
            'referencias_excluem_icu_ignore_power': True,
            'referencia_exclui_proprio_esforco': True,
            'min_pontos_anteriores_janela_recente': moa.MIN_ANTERIORES_JANELA,
            'ordem_mesmo_dia': 'hora local real (start_date_local); sem hora ou hora igual: ordem desconhecida'}


def atualizar(marcos=None, fonte_season=None, agora=None, origem='sync',
              curvas=None, atividades=None):
    """Deteta e regista as observacoes. Devolve um resumo (nunca levanta por dados maus).

    Sem atividades legiveis nao se regista nada (nao se pode aplicar icu_ignore_power).
    """
    import modelo_estimador as me
    if not db.ENABLED:
        return {'ok': False, 'erro': 'sem base de dados'}
    obtido_aqui = marcos is None and fonte_season is None
    if obtido_aqui:
        marcos, fonte_season = obter_marcos()
    fonte_season = fonte_season or (FONTE_SEASON_CALENDARIO if marcos else FONTE_SEASON_MES)
    curvas = db.load_power_curves() if curvas is None else curvas
    atividades = db.load_activities() if atividades is None else atividades
    if atividades is None:
        return {'ok': False, 'erro': 'atividades_indisponiveis',
                'detalhe': 'sem activities nao se aplica icu_ignore_power: nada foi registado'}
    agora = agora or mt.agora_utc()
    if obtido_aqui:
        import modelo_ir_temporal as tmp
        tmp.registar_marcos_seguro(marcos, fonte_season, agora)   # instante real da obtencao; nunca levanta

    mapa = protocolo.mapa_ignore_power(atividades)
    grupos = _grupos(atividades)
    eleg = {aid: me.elegibilidade_aprendizagem(aid, g) for aid, g in grupos.items()}
    ignorar = _ignorados_da_referencia(mapa, eleg, me.ELEG_ELEGIVEL)
    tags = {aid: sorted({t for a in g for t in moa.tags_da_atividade(a)}) for aid, g in grupos.items()}
    hora = {}                       # hora local REAL de activities.start_date_local (nunca a das curvas: so tem o dia)
    for aid, g in grupos.items():
        for a in g:
            v = a.get('start_date_local')
            if isinstance(v, str) and len(v) >= 16:
                hora[aid] = v
                break

    candidatos = moa.detetar(curvas, ignorar,
                             lambda d: config.season_de(d, marcos), horas=hora)
    historico = db.observacoes_auto_historico()
    problemas = moa.verificar_cadeia(historico)
    if problemas:
        return {'ok': False, 'erro': 'cadeia_de_versoes_incoerente', 'problemas': problemas[:5]}
    novas = moa.planear(
        candidatos, historico, agora, moa.limpar_curvas(curvas),
        elegibilidade_fn=lambda aid: eleg.get(aid) or me.elegibilidade_aprendizagem(aid, []),
        tags_fn=lambda aid: tags.get(aid, []), hora_local_fn=lambda aid: hora.get(aid),
        criterio=criterio(fonte_season), origem=origem)

    gravadas, conflitos = [], 0
    for linha in novas:
        try:
            db.inserir_observacao_auto(linha)
            gravadas.append(linha)
        except db.ConflitoObservacao:
            conflitos += 1
    por_chave = {}
    for c in candidatos:
        k = f"{c['modalidade']}:{c['duracao_s']}"
        por_chave[k] = por_chave.get(k, 0) + 1
    return {'ok': True, 'detetados': len(candidatos),
            'novas_versoes': sum(1 for l in gravadas if l['evento'] == moa.EVENTO_DETETADA),
            'primeiras_deteccoes': sum(1 for l in gravadas
                                       if l['evento'] == moa.EVENTO_DETETADA and l['versao'] == 1),
            'versoes_substituidas': sum(1 for l in gravadas
                                        if l['evento'] == moa.EVENTO_DETETADA and l['versao'] > 1),
            'retiradas': sum(1 for l in gravadas if l['evento'] == moa.EVENTO_RETIRADA),
            'conflitos_ignorados': conflitos,
            'sem_referencia_anterior': sum(1 for c in candidatos if c['referencia_tipo'] is None),
            'detetados_por_modalidade_duracao': dict(sorted(por_chave.items())),
            'season_fonte': fonte_season, 'anos_historico': config.ANOS_HISTORICO,
            'atividades_ignoradas_nas_referencias': len(ignorar)}


def fonte(revisoes=None, historico=None):
    """FonteAuto com o historico gravado (ou o dado) e as revisoes manuais."""
    return moa.FonteAuto(db.observacoes_auto_historico() if historico is None else historico,
                         db.revisoes_testes_modelo() if revisoes is None else revisoes)


def vista(tipo=None, duracao=None, incluida=None):
    """Observacoes ATUAIS para a UI, com estado de deteccao, elegibilidade cientifica
    (recalculada sobre as atividades de agora) e exclusao final. So leitura."""
    import modelo_estimador as me
    revisoes = db.revisoes_testes_modelo()
    historico = db.observacoes_auto_historico()
    atividades = db.load_activities()
    cpa = me.curvas_por_atividade(db.load_power_curves() or [])
    atuais_manuais = me.estado_atual_por_teste(revisoes)
    atuais = moa.atuais(historico)
    sel = me.selecionar_observacoes_auto(atuais, atuais_manuais, atividades, cpa)
    estado = {}
    for v in sel.values():
        for o in v['usadas']:
            estado[o['test_id']] = (True, None, o)
        for o in v['excluidas']:
            estado[o['test_id']] = (False, o['motivo'], o)
    cadeias = moa.por_teste(historico)
    itens = []
    for o in sorted(atuais, key=lambda r: (r['modalidade'], r['duracao_s'], r['data'], r['test_id'])):
        if tipo and o['modalidade'] != tipo:
            continue
        if duracao is not None and o['duracao_s'] != duracao:
            continue
        inc, motivo, info = estado.get(o['test_id'], (False, 'sem_estado', {}))
        if incluida == 'sim' and not inc:
            continue
        if incluida == 'nao' and inc:
            continue
        el = me.elegibilidade_aprendizagem(o['activity_id'], atividades)
        rev = atuais_manuais.get(o['test_id'])
        manual = None
        if rev is not None and rev['estado'] == mt.ESTADO_REJEITADO:
            manual = {'revisao': rev['revisao'], 'watts': rev.get('watts'),
                      'valor_diferente': mt.valor_mudou(rev.get('watts'), o['watts']),
                      'motivo': rev.get('motivo'), 'revisado_em': rev.get('revisado_em')}
        itens.append({
            'test_id': o['test_id'], 'modalidade': o['modalidade'], 'duracao_s': o['duracao_s'],
            'activity_id': o['activity_id'], 'data': o['data'], 'watts': o['watts'],
            'referencia_tipo': o['referencia_tipo'], 'referencia_watts': o['referencia_watts'],
            'pct_da_referencia': o['pct_da_referencia'], 'referencias': o['referencias'],
            'season': o['season'], 'versao': o['versao'],
            'estado_versao': moa.ESTADO_ATUAL,
            'estado_deteccao': ('sem_referencia_anterior' if o['referencia_tipo'] is None else 'detetada'),
            'detetado_em': o['detetado_em'], 'origem': o['origem'],
            'elegibilidade_cientifica': el['estado'], 'motivo_elegibilidade': el['motivo'],
            'ignore_parts_presente': el['ignore_parts_presente'],
            'incluida_no_estimador': inc, 'motivo_exclusao': motivo,
            'exclusao_manual': manual,
            'tags': o.get('tags'),
            'ordem_mesmo_dia': (o.get('criterio') or {}).get('ordem_mesmo_dia'),
            'versoes_anteriores': [{'versao': r['versao'], 'evento': r['evento'],
                                    'watts': r['watts'], 'data': r['data'],
                                    'detetado_em': r['detetado_em'],
                                    'estado_versao': moa.estado_versao(r, historico)}
                                   for r in cadeias[o['test_id']][:-1]],
        })
    resumo = {}
    for i in itens:
        d = resumo.setdefault(i['modalidade'], {}).setdefault(str(i['duracao_s']), {
            'detetadas': 0, 'incluidas': 0, 'excluidas': 0, 'motivos': {}})
        d['detetadas'] += 1
        if i['incluida_no_estimador']:
            d['incluidas'] += 1
        else:
            d['excluidas'] += 1
            d['motivos'][i['motivo_exclusao']] = d['motivos'].get(i['motivo_exclusao'], 0) + 1
    return {'itens': itens, 'resumo': resumo,
            'historico_temporal': moa.FonteAuto(historico, revisoes).resumo_temporal(
                [i['test_id'] for i in itens]),
            'n_versoes_substituidas': sum(1 for r in historico
                                          if moa.estado_versao(r, historico) == moa.ESTADO_SUBSTITUIDA),
            'n_retiradas': sum(1 for r in historico if r['evento'] == moa.EVENTO_RETIRADA),
            'ultima_atualizacao': max((r['detetado_em'] for r in historico), default=None),
            'criterio_ultimo': (max(historico, key=lambda r: r['detetado_em']).get('criterio') if historico else None)}
