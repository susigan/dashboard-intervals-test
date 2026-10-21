"""Servico do modelo IR: liga modelo_ir (puro) as observacoes MMP, as atividades e a base.

  * recalibrar(): para cada modalidade (unica duracao operacional: MMP3, 180 s), ajusta K1, K2, T1, T2 aos esforcos
    MMP3 `YES` e a carga da MESMA modalidade (icu_training_load), com P0 FIXO = MMP3 do `YES` mais recente
    (global, igual nas quatro modalidades; nunca o CP). So grava quando as ENTRADAS mudaram (assinatura) e nunca substitui o
    historico: cada ajuste e uma linha nova de modelo_ir_ajustes, com o motivo.
  * vista(): ultimo ajuste por unidade (e, se pedido, o historico). So leitura.
  * validar_preditivo(): walk-forward (treino estritamente anterior) contra M0/M1/M2, separado
    do erro de ajuste historico. So leitura; nada e gravado.

Nao chama a Intervals.icu. Nao toca em PMC, CTL/ATL/TSB, homeostatico, estimador nem validacao.
"""
import math
from datetime import date, timedelta

import numpy as np

import config
import db
import modelo_estimador as E
import modelo_ir as ir
import modelo_ir_exploratorio as mex
import modelo_ir_global as G
import modelo_ir_p0 as p0m
import modelo_mmp3 as mmp3
import modelo_mmp3_intervalos as mi
import modelo_observacoes_auto as moa
import modelo_testes as mt
import modelo_validacao as V

VERSAO_SERVICO = 5

# UM ajuste por modalidade, sobre o MMP3 (180 s). As duracoes antigas (60/300/1200 s) deixam de ser unidades
# operacionais do IR: os ajustes e referencias antigos ficam na base (so leitura), nao se recalibram.
DURACAO_IR = mmp3.DURACAO_S

MOTIVO_PRIMEIRO = 'primeiro_ajuste'
MOTIVO_NOVAS = 'novas_observacoes'
MOTIVO_ALTERADAS = 'observacoes_removidas_ou_alteradas'
MOTIVO_CARGA = 'carga_alterada'
MOTIVO_REF_P0 = 'referencia_p0_alterada'      # novo registo YES de origem com o MESMO valor de P0
MOTIVO_CP = 'p0_alterado'                      # o valor de P0 (MMP3 do ultimo YES) mudou
ACAO_BLOQUEADO = 'bloqueado_sem_p0'      # sem registo MMP3 YES: sem P0, sem ajuste novo
ACAO_FALHOU = 'solver_falhou'
MOTIVO_CONFIG = 'configuracao_ou_versao_alterada'
MOTIVO_FORCADO = 'forcado'

AVISO = ('Experimental. Ajuste HISTORICO (dentro da amostra) do modelo Impulse-Response aos '
         'melhores esforcos detetados por modalidade e duracao; nao e previsao validada, nao e '
         'um teste padronizado e nao altera PMC, CTL, ATL, TSB, estimador nem validacao. '
         'Parametros nao identificaveis ou com dados insuficientes nao devem ser lidos como '
         'fisiologia confirmada.')


# ── entradas ──────────────────────────────────────────────────────────────

def carga_por_modalidade(atividades):
    """{modalidade: {data: carga}} e contagens. Carga = icu_training_load (>= 0) por dia, somando
    as atividades do mesmo dia; uma atividade (mesmo id) conta uma vez; sem carga numerica conta
    em 'sem_carga' e nao entra. Nao mistura modalidades (TYPE_MAP)."""
    out = {m: {} for m in mt.MODALIDADES}
    vistos, sem_carga, n = set(), {m: 0 for m in mt.MODALIDADES}, {m: 0 for m in mt.MODALIDADES}
    for a in atividades or []:
        if not isinstance(a, dict):
            continue
        mod = config.TYPE_MAP.get(a.get('type'))
        if mod not in out:
            continue
        aid = a.get('id')
        if aid is not None:
            if aid in vistos:
                continue
            vistos.add(aid)
        d = str(a.get('start_date_local') or '')[:10]
        if len(d) != 10:
            continue
        v = a.get('icu_training_load')
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not (v == v) or v < 0:
            sem_carga[mod] += 1
            continue
        out[mod][d] = out[mod].get(d, 0.0) + float(v)
        n[mod] += 1
    return out, {'n_atividades_com_carga': n, 'n_atividades_sem_carga': sem_carga}


def selecionar(historico_obs, revisoes, atividades, curvas):
    """Observacoes MMP elegiveis ATUAIS por (modalidade, duracao): a mesma selecao do estimador e
    da validacao (icu_ignore_power, ignore_parts, exclusao manual, dados divergentes...)."""
    cpa = E.curvas_por_atividade(curvas or [])
    return E.selecionar_observacoes_auto(moa.atuais(historico_obs),
                                         E.estado_atual_por_teste(revisoes or []), atividades, cpa)


def _ignorar_e_horas(atividades):
    """({activity_id com icu_ignore_power}, {activity_id: start_date_local com hora}) das atividades."""
    import protocolo
    mapa = protocolo.mapa_ignore_power(atividades) or {}
    ignorar = {aid for aid, v in mapa.items() if v is True}
    horas = {}
    for a in atividades or []:
        v = a.get('start_date_local') if isinstance(a, dict) else None
        if isinstance(v, str) and len(v) >= 16 and a.get('id') is not None:
            horas.setdefault(a['id'], v)
    return ignorar, horas


def _tags_por_atividade(atividades):
    """{activity_id: [tags]} (uniao por id) das atividades originais da API."""
    out = {}
    for a in atividades or []:
        if isinstance(a, dict) and a.get('id') is not None:
            out.setdefault(a['id'], set()).update(moa.tags_da_atividade(a))
    return {k: sorted(v) for k, v in out.items()}


def _atividades_por_id(atividades):
    """{activity_id: atividade} (a primeira de cada id): so a leitura dos intervalos da PROPRIA atividade."""
    out = {}
    for a in atividades or []:
        if isinstance(a, dict) and a.get('id') is not None:
            out.setdefault(a['id'], a)
    return out


def entradas_mmp3(curvas, atividades, ate, marcos=None, revisoes=None):
    """{modalidade: (observacoes_para_o_IR, esforcos_avaliados)} a partir das curvas (`power_curves`) e das
    atividades, so com dados ate `ate` (sem futuro). Usa o MMP3 canonico de `avaliar_esforcos`.

    Revisoes manuais (estado ATUAL por teste):
      * REJEITADO: retira a elegibilidade de YES e de A_VERIFICAR (motivo `rejeitado_manualmente`). A potencia
        continua na referencia fisica (nao e excluida das referencias de deteccao).
      * CONFIRMADO: um candidato A_VERIFICAR confirmado entra como YES (origem `confirmacao_manual`), desde que o
        MMP3 atual seja o MESMO valor que foi visto na confirmacao. Se o valor mudou (correcao na origem), o candidato
        volta a ficar A_VERIFICAR (motivo `valor_mudou_desde_a_confirmacao`) e precisa de nova decisao.
    O mesmo conjunto alimenta o P0 global e as observacoes do IR."""
    ignorar, horas = _ignorar_e_horas(atividades)
    tags = _tags_por_atividade(atividades)
    por_id = _atividades_por_id(atividades)
    cache = {}

    def eleg(aid):
        if aid not in cache:
            cache[aid] = E.elegibilidade_aprendizagem(aid, atividades)['estado']
        return cache[aid]
    atuais = E.estado_atual_por_teste(revisoes or [])
    rejeitados = {t for t, r in atuais.items() if r.get('estado') == mt.ESTADO_REJEITADO}
    confirmados = {t: r for t, r in atuais.items() if r.get('estado') == mt.ESTADO_CONFIRMADO}
    out = {}
    for mod in mt.MODALIDADES:
        esf = []
        for e in mmp3.avaliar_esforcos(curvas, mod, ate, ignorar, eleg, marcos, horas, tags=tags,
                                       atividades_por_id=por_id):
            t = e['test_id']
            if t in rejeitados and e['estado'] in (mmp3.E_ELEGIVEL, mmp3.E_A_VERIFICAR):
                e = {**e, 'estado': mmp3.E_NAO_ELEGIVEL, 'motivo': mmp3.M_REJEITADO, 'a_verificar': False}
            elif e['estado'] == mmp3.E_A_VERIFICAR and t in confirmados:
                r = confirmados[t]
                if mt.mesmo_valor(r.get('watts'), e['watts']):
                    e = {**e, 'estado': mmp3.E_ELEGIVEL, 'a_verificar': False, 'motivo': 'confirmado_manualmente',
                         'origem_classificacao': mmp3.ORIGEM_CONFIRMACAO, 'revisao_confirmada': r.get('revisao'),
                         'motivo_decisao': 'confirmado manualmente (revisao %s)' % r.get('revisao')}
                else:
                    e = {**e, 'motivo': mmp3.M_VALOR_MUDOU,
                         'motivo_decisao': 'a confirmacao (revisao %s) era de %s W: valor atual %s W, nova decisao '
                                           'necessaria' % (r.get('revisao'), r.get('watts'), e['watts'])}
            esf.append(e)
        out[mod] = (mmp3.observacoes_ir(esf), esf)
    return out


def candidatos_mmp3(curvas, atividades, ate, marcos=None, revisoes=None, modalidade=None):
    """Candidatos MMP3 de 180 s para o registo de decisoes (mesma forma de `modelo_testes.candidatos`).
    Identificacao = mesma de entradas_mmp3: os A_VERIFICAR e os YES ja identificados. Sem nenhuma logica nova."""
    out = []
    for mod, (_obs, esf) in entradas_mmp3(curvas, atividades, ate, marcos, revisoes).items():
        if modalidade and mod != modalidade:
            continue
        for e in esf:
            if e['estado'] not in (mmp3.E_ELEGIVEL, mmp3.E_A_VERIFICAR):
                continue
            out.append({'test_id': e['test_id'], 'modalidade': mod, 'duracao_s': DURACAO_IR,
                        'activity_id': e['activity_id'], 'data': e['data'], 'watts': e['watts'],
                        'pct_do_melhor': e.get('pct_do_melhor'), 'melhor_recente': e.get('melhor_anterior_watts'),
                        'referencia_tipo': 'all_time', 'elegibilidade': 'elegivel',
                        'motivo_elegibilidade': None})
    return out


def rejeitados_manualmente(revisoes):
    """test_ids cuja revisao manual atual e REJEITADO (mesma regra que `entradas_mmp3`)."""
    return {t for t, r in E.estado_atual_por_teste(revisoes or []).items() if r.get('estado') == mt.ESTADO_REJEITADO}


def p0_global_das_entradas(entradas, historico_yes=None, rejeitados=None):
    """P0 global (modelo_mmp3.p0_global) a partir de `entradas_mmp3`, com o historico de YES gravados e as
    rejeicoes manuais. Sem historico nem rejeicoes, o resultado e o mesmo de antes."""
    return mmp3.p0_global({m: v[1] for m, v in entradas.items()}, historico_yes=historico_yes, rejeitados=rejeitados)


def _cfg_efetiva(config_ir=None):
    c = ir._cfg(config_ir)
    return {'ir': c, 'versao_modelo': ir.VERSAO_IR, 'versao_servico': VERSAO_SERVICO,
            'duracao_alvo_s': DURACAO_IR,
            'carga': 'icu_training_load das atividades da mesma modalidade, somada por dia',
            'alvo': 'MMP3 (180 s) de esforcos YES: atividade com tag 3 min ou intervalo isolado de 3 min detetado, e potencia >= %.2f x o melhor MMP3 valido '
                    'anterior (regra %d)' % (mmp3.LIMIAR, mmp3.VERSAO_REGRA_P0),
            'regra_yes': {'versao': mmp3.VERSAO_REGRA_P0, 'tag_tempo_s': mmp3.DURACAO_S, 'limiar': mmp3.LIMIAR,
                          'referencia': 'melhor_mmp3_valido_anterior',
                          'sinais': [mi.ORIGEM_TAG, mi.ORIGEM_INTERVALO],
                          'janela_intervalo_3min_s': list(mi.JANELA_3MIN)},
            # P0 nao e estimado: e EXATAMENTE o MMP3 do esforco YES mais recente (global) e fica FIXO em cada
            # ajuste. Faz parte da configuracao para que um ajuste antigo (P0 livre, ou P0 = CP) nao conte
            # como atual.
            'p0': {'modo': 'mmp3_ultimo_yes_global', 'formulacao': p0m.FORMULA_ID,
                   'versao': p0m.VERSAO_P0, 'parametros_estimados': ['k1', 'k2', 't1', 't2']}}


# ── P0 global de referencia (MMP3 do ultimo YES); a referencia fica no historico do IR ──


def garantir_referencia_p0(res, agora, origem='sync'):
    """Persiste (so INSERT, idempotente) a referencia de P0 valida `res` da unidade. Devolve (ref, criada).

    Se a referencia mais recente da unidade tem a mesma assinatura (mesma regra, mesmo registo YES de origem,
    mesma data/hora e valor) nao grava nada. Um registo de origem novo cria a versao seguinte da unidade com
    `conhecida_desde = agora`; as anteriores nunca sao reescritas. A referencia e global: as quatro unidades
    recebem o mesmo registo de origem e a mesma assinatura (cada uma com a sua versao_referencia)."""
    mod, dur = res['modalidade'], res['duracao_s']
    ult = db.ultima_referencia_p0(mod, dur)
    if ult is not None and ult['assinatura'] == res['assinatura']:
        return ult, False
    reg = res['registo']
    ref = {'modalidade': mod, 'duracao_s': dur,
           'versao_referencia': (ult['versao_referencia'] + 1) if ult else 1,
           'conhecida_desde': agora, 'valor_p0': res['valor_p0'], 'formulacao': res['formulacao'],
           # colunas herdadas do desenho com CP: aqui guardam a proveniencia MMP3 (ver modelo_ir_p0)
           'modelo_cp': res['origem'], 'cp_w': res['valor_p0'], 'wp_j': None,
           'cp_data_referencia': reg['data'], 'cp_data_gravacao': reg.get('inicio') or reg['data'],
           'cp_resultado_ref': dict(reg),
           'parametros': {'equacao': res['equacao'], 'origem': res['origem'], 'versao_regra': res['versao_regra'],
                          'duracao_s': mmp3.DURACAO_S, 'n_yes': res['n_yes'], 'registo': dict(reg)},
           'assinatura': res['assinatura'], 'versao_modulo': p0m.VERSAO_P0, 'origem': origem}
    try:
        db.inserir_referencia_p0(ref)
    except db.ConflitoReferenciaP0:
        return db.ultima_referencia_p0(mod, dur), False
    return db.ultima_referencia_p0(mod, dur), True


def _ref_resumo(r):
    """Campos da referencia que acompanham um ajuste (e a UI)."""
    if not r:
        return None
    reg = (r.get('parametros') or {}).get('registo') or r.get('cp_resultado_ref') or {}
    return {'versao_referencia': r.get('versao_referencia'), 'valor_p0': r.get('valor_p0'),
            'formulacao': r.get('formulacao'), 'origem': (r.get('parametros') or {}).get('origem'),
            'conhecida_desde': r.get('conhecida_desde'), 'assinatura': r.get('assinatura'),
            'registo': {k: reg.get(k) for k in ('test_id', 'activity_id', 'modalidade', 'data', 'inicio', 'watts')}
            if reg else None}


def _obs_min(usadas):
    return [{'test_id': o['test_id'], 'versao': o.get('revisao'), 'data': str(o['data'])[:10],
             'watts': float(o['watts'])} for o in sorted(usadas, key=lambda x: (x['data'], x['test_id']))]


def _entradas(obs, carga_dia, referencia, p0_ini, p0_ref=None):
    """Resumo JSON das entradas de UM ajuste (serve a assinatura e a auditoria).
    p0_ref: resumo da referencia de P0 global (MMP3 do ultimo YES) usada, ligada ao ajuste."""
    ate = max((o['data'] for o in obs), default=None)
    cargas = sorted((d, round(v, 3)) for d, v in carga_dia.items() if ate and d <= ate and v > 0)
    return {'observacoes': obs, 'carga_ate': ate,
            'carga_dias_com_treino': len(cargas),
            'carga_total': round(sum(v for _, v in cargas), 3),
            'carga_digest': ir.assinatura(cargas),
            'p0_inicial': None if p0_ini is None else round(p0_ini, 3),
            'p0_ref': p0_ref,
            'referencia': referencia}


def _motivo(prev, ent, cfg_assin, forcar):
    if prev is None:
        return MOTIVO_PRIMEIRO
    if forcar:
        return MOTIVO_FORCADO
    pe = prev.get('entradas') or {}
    if prev.get('versao_modelo') != ir.VERSAO_IR or ir.assinatura(prev.get('configuracao')) != cfg_assin:
        return MOTIVO_CONFIG
    ant = {(o['test_id'], o.get('versao'), o['data'], o['watts']) for o in pe.get('observacoes') or []}
    nov = {(o['test_id'], o.get('versao'), o['data'], o['watts']) for o in ent['observacoes']}
    if nov - ant and not (ant - nov):
        return MOTIVO_NOVAS
    if ant != nov:
        return MOTIVO_ALTERADAS
    if pe.get('carga_digest') != ent['carga_digest']:
        return MOTIVO_CARGA
    # o que sobra e a referencia de P0: valor diferente (p0_alterado) ou so novo registo YES com o mesmo valor
    # (referencia_p0_alterada)
    ant_p0 = (pe.get('p0_ref') or {}).get('valor_p0')
    nov_p0 = (ent.get('p0_ref') or {}).get('valor_p0')
    return MOTIVO_CP if (ant_p0 is None or nov_p0 is None or ant_p0 != nov_p0) else MOTIVO_REF_P0


# ── recalibracao ──────────────────────────────────────────────────────────

def compatibilidade_p0_alvo(obs, p0):
    """Diagnostico DESCRITIVO (sem limiar) de como P0 (= CP) se relaciona com os alvos usados: o alvo do IR e
    o melhor valor da duracao numa atividade (nao um teste maximo), por isso pode ficar muito abaixo de um P0
    que e a potencia sustentavel. Nao decide nada; fica no ajuste para quem interpreta as curvas."""
    w = sorted(float(o['watts']) for o in obs if _p0_valido(o.get('watts')))
    if not w or not _p0_valido(p0):
        return None
    n = len(w)
    med = w[n // 2] if n % 2 else 0.5 * (w[n // 2 - 1] + w[n // 2])
    return {'n': n, 'p0': float(p0), 'mediana_alvos': round(med, 3), 'mediana_alvos_sobre_p0': round(med / p0, 4),
            'pct_alvos_acima_p0': round(100.0 * sum(1 for x in w if x > p0) / n, 2),
            'alvo_max_sobre_p0': round(w[-1] / p0, 4)}


def _linha_yes(e, mod, revisao=None):
    """Linha de YES (mesma forma em ambas as tabelas). revisao None = automatica (tabela antiga)."""
    l = {'test_id': e['test_id'], 'regra_versao': mmp3.VERSAO_REGRA_P0,
         'activity_id': str(e['activity_id']), 'modalidade': mod, 'data': e['data'],
         'inicio': e.get('inicio'), 'watts': e['watts'], 'season': e.get('season'),
         'origem_classificacao': e.get('origem_classificacao'),
         'classe_intervalo': e.get('classe_intervalo'), 'motivo_decisao': e.get('motivo_decisao'),
         'referencia_watts': e.get('melhor_anterior_watts')}
    if revisao is not None:
        l['revisao'] = revisao
    return l


def _gravar_yes_vivos(entradas, agora):
    """Acrescenta ao historico de YES os efeitos ELEGIVEIS de hoje (append-only; repetidos sao ignorados).
    Automaticos -> tabela antiga (revisao 0). Confirmacoes manuais -> modelo_mmp3_yes_revisoes (sua revisao).
    Um efeito so entra aqui se for YES pela regra vigente; nada e alterado nem apagado."""
    automaticas, manuais = [], []
    for mod, (_obs, esf) in entradas.items():
        for e in esf:
            if e['estado'] != mmp3.E_ELEGIVEL:
                continue
            if e.get('origem_classificacao') == mmp3.ORIGEM_CONFIRMACAO and e.get('revisao_confirmada'):
                manuais.append(_linha_yes(e, mod, revisao=e['revisao_confirmada']))
            else:
                automaticas.append(_linha_yes(e, mod))
    agora_iso = agora.isoformat(timespec='seconds')
    n = db.gravar_yes_historico(automaticas, agora_iso)
    if manuais:
        db.gravar_yes_revisao([dict(l, registado_em=agora_iso) for l in manuais], agora_iso)
    return n


def linhas_yes_confirmacao(rev, curvas, atividades, ate, marcos=None, revisoes=None):
    """Linha YES desta confirmacao, CALCULADA antes de qualquer escrita (so leitura e calculo).

    `rev` e a revisao nova (ainda nao gravada). Recalcula as entradas com ela e devolve a linha do proprio teste
    se ficar ELEGIVEL por confirmacao manual; lista vazia se o valor ja nao corresponder."""
    if rev.get('estado') != mt.ESTADO_CONFIRMADO:
        return []
    ent = entradas_mmp3(curvas, atividades, ate, marcos, list(revisoes or []) + [rev])
    for e in ent.get(rev['modalidade'], ([], []))[1]:
        if e['test_id'] == rev['test_id'] and e['estado'] == mmp3.E_ELEGIVEL \
                and e.get('origem_classificacao') == mmp3.ORIGEM_CONFIRMACAO:
            return [_linha_yes(e, rev['modalidade'], revisao=rev['revisao'])]
    return []


def divergencias_yes(curvas, atividades, ate, marcos=None, revisoes=None):
    """Confirmacoes manuais cujo MMP3 atual ja nao e o valor confirmado. So leitura: nao recalibra, nao grava.

    Cada item traz o valor confirmado (revisao vigente), o MMP3 atual, a diferenca, o motivo e o historico de
    revisoes do teste. O YES anterior mantem-se no P0 ate haver nova decisao explicita (confirmar com o valor
    atual, ou rejeitar)."""
    revs = list(revisoes or [])
    atuais = E.estado_atual_por_teste(revs)
    ent = entradas_mmp3(curvas, atividades, ate, marcos, revs)
    por_teste = {e['test_id']: e for mod in ent for e in ent[mod][1]}
    out = []
    for tid, r in atuais.items():
        if r.get('estado') != mt.ESTADO_CONFIRMADO:
            continue
        e = por_teste.get(tid)
        if e is None or e.get('motivo') != mmp3.M_VALOR_MUDOU:
            continue
        w0, w1 = r.get('watts'), e['watts']
        historico = [{'revisao': h.get('revisao'), 'estado': h.get('estado'), 'watts': h.get('watts'),
                      'motivo': h.get('motivo'), 'registado_em': str(h.get('registado_em'))}
                     for h in db.revisoes_testes_modelo(test_id=tid)]
        out.append({'test_id': tid, 'modalidade': r.get('modalidade'), 'activity_id': r.get('activity_id'),
                    'revisao_confirmada': r.get('revisao'), 'watts_confirmado': w0, 'watts_atual': w1,
                    'diferenca_w': round(w1 - w0, 2), 'diferenca_pct': round(100.0 * (w1 - w0) / w0, 2) if w0 else None,
                    'motivo': e.get('motivo'), 'motivo_decisao': e.get('motivo_decisao'),
                    'estado_revisao': 'pendente_de_nova_decisao', 'historico_revisoes': historico})
    return out


def garantir_yes_confirmacao(tid, curvas, atividades, ate, marcos=None, revisoes=None):
    """Reparacao idempotente: se a ultima revisao CONFIRMADA ja e YES mas a sua linha falta, grava-a.
    Nunca escreve revisao nem altera outras linhas. Devolve o numero de linhas enviadas (0 se nada a fazer)."""
    ult = db.ultima_revisao_teste(tid)
    if not ult or ult.get('estado') != mt.ESTADO_CONFIRMADO:
        return 0
    linhas = linhas_yes_confirmacao(ult, curvas, atividades, ate, marcos, [r for r in (revisoes or [])
                                                                          if r.get('test_id') != tid or
                                                                          r.get('revisao') != ult.get('revisao')])
    # so conta o que falta de facto: uma linha ja presente nao e reparacao
    faltam = [l for l in linhas if not db._exec(
        'SELECT 1 FROM modelo_mmp3_yes_revisoes WHERE test_id = ? AND revisao = ? AND regra_versao = ?',
        (l['test_id'], l['revisao'], l['regra_versao']), fetch='one')]
    if faltam:
        db.gravar_yes_revisao(faltam, mt.agora_utc().isoformat(timespec='seconds'))
    return len(faltam)


def recalibrar(origem='sync', agora=None, forcar=False, config_ir=None, tipo=None, duracao=None,
               dados=None, marcos=None):
    """Ajusta cada unidade cujas entradas mudaram. Idempotente: sem mudancas nao grava nada.

    P0 GLOBAL = MMP3 do esforco `YES` mais recente entre as quatro modalidades (modelo_ir_p0): fixo no ajuste;
    so K1, K2, T1, T2 sao estimados. Sem nenhum `YES` NAO se cria ajuste (acao 'bloqueado_sem_p0'): os ultimos
    ajustes validos ficam intactos e nao ha P0 de substituicao (nem CP, nem FTP/eFTP, nem outra duracao). Um
    registo `YES` mais recente cria uma referencia nova em cada modalidade e, porque a assinatura do ajuste
    inclui a referencia, o Solver volta a correr nas quatro: K1, K2, T1, T2 sao reestimados com o P0 novo e
    gravados como versao nova. Sem mudanca de P0, de observacoes ou de carga nao se grava nada. Se o Solver
    falhar numa modalidade (excecao) grava-se a tentativa falhada dessa modalidade (estado sem_convergencia,
    `acao: solver_falhou`): o ajuste anterior fica intacto na historia mas deixa de ser o atual e nunca se
    apresenta como recalibrado; as outras modalidades nao sao afetadas. Volta a tentar na chamada seguinte.

    Devolve {'ok', 'unidades': {'Bike:300': {...}}, 'n_ajustadas', 'n_inalteradas', 'n_bloqueadas', ...}.
    Nunca levanta por dados maus: falhar o IR nao pode estragar a sincronizacao.
    dados: (historico_obs, revisoes, atividades, curvas) para testes; por omissao le da base.
    """
    if not db.ENABLED:
        return {'ok': False, 'erro': 'sem base de dados'}
    agora = agora or mt.agora_utc()
    referencia = agora.date().isoformat()
    if dados is None:
        atividades = db.load_activities()
        if atividades is None:
            return {'ok': False, 'erro': 'atividades_indisponiveis',
                    'detalhe': 'sem activities nao ha carga: nada foi ajustado'}
        historico_obs = db.observacoes_auto_historico()
        revisoes = db.revisoes_testes_modelo()
        curvas = db.load_power_curves() or []
    else:
        historico_obs, revisoes, atividades, curvas = dados
    if duracao is not None and duracao != DURACAO_IR:
        return {'ok': False, 'erro': 'duracao_nao_operacional',
                'detalhe': f'o IR so tem um ajuste por modalidade, sobre o MMP3 ({DURACAO_IR} s)'}
    if marcos is None and dados is None:
        import observacoes_auto as oa            # calendario de seasons (cache); falhar cai no ano civil
        marcos, _fonte = oa.obter_marcos()
    try:
        entradas = entradas_mmp3(curvas, atividades, referencia, marcos, revisoes)
    except E.HistoricoAmbiguo as e:
        return {'ok': False, 'erro': 'historico_de_testes_ambiguo', 'detalhe': str(e)}
    # YES historicos gravados (producao) + rejeicoes atuais: um YES gravado nao sai do P0 por mudar a temporada
    historico = db.yes_historico() if dados is None else None
    rejeitados = rejeitados_manualmente(revisoes)
    global_ = p0_global_das_entradas(entradas, historico, rejeitados)   # UM P0 para as quatro modalidades
    if dados is None:
        _gravar_yes_vivos(entradas, agora)
    cargas, info_carga = carga_por_modalidade(atividades)
    cfg = _cfg_efetiva(config_ir)
    cfg_assin = ir.assinatura(cfg)
    unidades, n_aj, n_in, n_bl, n_fa = {}, 0, 0, 0, 0
    for mod in mt.MODALIDADES:
        dur = DURACAO_IR
        if tipo and mod != tipo:
            continue
        chave = f'{mod}:{dur}'
        obs = sorted(entradas[mod][0], key=lambda x: (x['data'], x['test_id']))
        prev = db.ultimo_ajuste_ir(mod, dur)
        if not obs and prev is None:
            unidades[chave] = {'acao': 'sem_observacoes'}
            continue
        res_p0 = p0m.resolver_p0(mod, dur, global_)
        if not res_p0['valida']:
            # sem P0 valido: nenhuma versao nova, nenhum P0 de substituicao; o ultimo ajuste fica como esta
            unidades[chave] = {'acao': ACAO_BLOQUEADO, 'motivo': res_p0['motivo'],
                               'detalhe': res_p0['detalhe'], 'estado_referencia': global_['estado'],
                               'ultimo_ajuste': None if prev is None else {
                                   'versao_ajuste': prev['versao_ajuste'], 'estado': prev['estado'],
                                   'p0': prev['p0'], 'p0_origem': prev['p0_origem']}}
            n_bl += 1
            continue
        ref, _criada = garantir_referencia_p0(res_p0, agora, origem)
        p0v = float(ref['valor_p0'])
        p0o = f"{p0m.ORIGEM}:{res_p0['registo']['test_id']}"
        ent = _entradas(obs, cargas[mod], referencia, p0v, _ref_resumo(ref))
        sig = ir.assinatura({'cfg': cfg_assin, 'obs': [(o['test_id'], o['versao'], o['data'], o['watts'])
                                                       for o in obs],
                             'carga': ent['carga_digest'], 'p0_inicial': ent['p0_inicial'],
                             'p0_ref': ref['assinatura']})
        if (prev is not None and prev['assinatura'] == sig and not forcar
                and not (prev.get('metricas') or {}).get('excecao')):
            unidades[chave] = {'acao': 'inalterado', 'versao_ajuste': prev['versao_ajuste'],
                               'estado': prev['estado'], 'versao_referencia_p0': ref['versao_referencia']}
            n_in += 1
            continue
        falha = None
        try:
            r = ir.ajustar_observacoes(obs, cargas[mod], referencia, cfg['ir'], p0_fixo=p0v)
        except Exception as e:       # um erro numerico numa unidade nao pode parar as outras
            import traceback
            traceback.print_exc()
            falha = f'{type(e).__name__}: {e}'
            # tentativa FALHADA registada como nova versao sem parametros: o ultimo ajuste valido fica
            # intacto (versao anterior), mas deixa de ser o "atual" e nao se desenha como se estivesse
            # recalibrado com o P0 novo
            r = {'estado': ir.EST_SEM_CONVERGENCIA, 'parametros': None, 'metricas': {}, 'obs': {},
                 'convergencia': {'convergiu': False, 'detalhe': falha}, 'limites': None,
                 'identificabilidade': None, 'motivo': 'excecao_no_solver', 'p0_inicial': {}}
        p = r.get('parametros') or {}
        m = r.get('metricas') or {}
        o = r.get('obs') or {}
        linha = {
            'modalidade': mod, 'duracao_s': dur,
            'versao_ajuste': (prev['versao_ajuste'] + 1) if prev else 1,
            'ajustado_em': agora, 'origem': origem,
            'motivo': _motivo(prev, ent, cfg_assin, forcar), 'estado': r['estado'],
            'p0': p.get('p0'), 'k1': p.get('k1'), 'k2': p.get('k2'), 't1': p.get('t1'),
            't2': p.get('t2'), 'n_obs': len(obs),
            'data_ini': o.get('data_ini'), 'data_fim': o.get('data_fim'),
            'funcao_objetivo': m.get('sse'), 'r2': m.get('r2'), 'rmse': m.get('rmse'),
            'convergiu': (r.get('convergencia') or {}).get('convergiu'),
            'p0_inicial': p0v, 'p0_origem': p0o,
            'assinatura': sig, 'versao_modelo': ir.VERSAO_IR, 'configuracao': cfg,
            'metricas': {**(m or {}), 'motivo_insuficiente': r.get('motivo'),
                         'convergencia': r.get('convergencia'), 'limites': r.get('limites'),
                         'obs': o, 'p0_inicial_detalhe': (r.get('p0_inicial') or {}).get('detalhe'),
                         'compatibilidade_p0_alvo': compatibilidade_p0_alvo(obs, p0v),
                         'excecao': falha},
            'identificabilidade': r.get('identificabilidade'),
            'entradas': ent}
        try:
            db.inserir_ajuste_ir(linha)
        except db.ConflitoAjusteIR:
            unidades[chave] = {'acao': 'conflito_ignorado'}
            continue
        if falha:
            n_fa += 1
            unidades[chave] = {'acao': ACAO_FALHOU, 'erro': falha, 'versao_ajuste': linha['versao_ajuste'],
                               'estado': r['estado'], 'versao_referencia_p0': ref['versao_referencia'],
                               'p0': p0v, 'motivo': linha['motivo']}
            continue
        n_aj += 1
        unidades[chave] = {'acao': 'ajustado', 'versao_ajuste': linha['versao_ajuste'],
                           'estado': r['estado'], 'motivo': linha['motivo'], 'n_obs': len(obs),
                           'versao_referencia_p0': ref['versao_referencia'], 'p0': p0v}
    return {'ok': True, 'p0_global': {k: v for k, v in global_.items()},
            'unidades': unidades, 'n_ajustadas': n_aj, 'n_inalteradas': n_in,
            'n_bloqueadas': n_bl, 'n_falhadas': n_fa, 'carga': info_carga, 'versao_modelo': ir.VERSAO_IR}


# ── leitura ───────────────────────────────────────────────────────────────

def vista(tipo=None, duracao=None, historico=False):
    """Ultimo ajuste por unidade (e todo o historico se pedido). So leitura. Por omissao so a unidade operacional
    (MMP3, 180 s); os ajustes das duracoes antigas continuam consultaveis com `duracao` explicita (historico)."""
    duracao = DURACAO_IR if duracao is None else duracao
    todos = db.ajustes_ir_historico(tipo, duracao)
    por_unidade = {}
    for r in todos:
        por_unidade.setdefault((r['modalidade'], r['duracao_s']), []).append(r)
    itens = []
    for (m, d), lst in sorted(por_unidade.items()):
        ult = lst[-1]
        itens.append({**ult, 'n_ajustes': len(lst),
                      'leitura': _leitura(ult)})
    out = {'experimental': True, 'tipo_analise': 'ajuste_historico_ir', 'aviso': AVISO,
           'limitacoes': list(ir.LIMITES), 'itens': itens}
    if historico:
        out['historico'] = [{k: r[k] for k in ('modalidade', 'duracao_s', 'versao_ajuste',
                                               'ajustado_em', 'origem', 'motivo', 'estado', 'p0',
                                               'k1', 'k2', 't1', 't2', 'n_obs', 'data_ini',
                                               'data_fim', 'funcao_objetivo', 'r2', 'rmse',
                                               'versao_modelo')} for r in todos]
    return out


def _leitura(aj):
    e = aj['estado']
    if e == ir.EST_INSUFICIENTE:
        motivo = (aj.get('metricas') or {}).get('motivo_insuficiente')
        return 'dados insuficientes' + (f': {motivo}' if motivo else '') + ' — parametros nao estimados'
    if e == ir.EST_SEM_CONVERGENCIA:
        return 'o otimizador nao convergiu: parametros nao devem ser usados'
    if e == ir.EST_NAO_IDENTIFICAVEL:
        return ('ajuste historico com parametros NAO identificaveis (' +
                ', '.join((aj.get('identificabilidade') or {}).get('avisos') or []) +
                '): nao e fisiologia confirmada')
    return ('ajuste historico com parametros identificaveis nesta amostra; continua a ser '
            'ajuste dentro da amostra, nao previsao validada')


# ── curvas de influencia (so leitura do ultimo ajuste; nunca recalibra) ─────────────

AVISO_INFLUENCIA = ('Curvas descritivas do ULTIMO AJUSTE guardado (dentro da amostra): mostram como a '
                    'equacao ajustada reparte a carga em componente positiva (K1*E) e negativa (K2*G). '
                    'Nao e previsao validada; K, T e P0 so tem leitura fisiologica se o ajuste for '
                    'identificavel e, mesmo assim, sao parametros de um modelo, nao constantes medidas. Os valores estao '
                    'em W do alvo desta modalidade e duracao, por unidade da carga (icu_training_load) desta '
                    'modalidade: nao sao comparaveis em valor absoluto entre modalidades nem medem equilibrio '
                    'entre elas (cada modalidade tem o seu proprio modelo, sem influencia cruzada).')
MAX_ATIVIDADES_LISTA = 300
_ARR = ('positiva', 'negativa', 'liquida')


def _atividades_da_modalidade(atividades, mod):
    """Atividades com carga > 0 da modalidade (mesmas regras de carga_por_modalidade: dedup por id,
    TYPE_MAP, icu_training_load >= 0). Devolve [{id, data, nome, carga}]."""
    out, vistos = [], set()
    for a in atividades or []:
        if not isinstance(a, dict) or config.TYPE_MAP.get(a.get('type')) != mod:
            continue
        aid = a.get('id')
        if aid is not None:
            if aid in vistos:
                continue
            vistos.add(aid)
        d = str(a.get('start_date_local') or '')[:10]
        v = a.get('icu_training_load')
        if len(d) != 10 or not isinstance(v, (int, float)) or isinstance(v, bool) or not (v == v) or v <= 0:
            continue
        out.append({'id': None if aid is None else str(aid), 'data': d,
                    'nome': str(a.get('name') or a.get('type') or '')[:80], 'carga': float(v)})
    return sorted(out, key=lambda x: (x['data'], str(x['id'])))


def _arr(x, nd=3):
    return [round(float(v), nd) for v in x]


def curva_influencia(tipo, duracao, atividade_id=None, ate=None, atividades=None):
    """Decomposicao do ultimo ajuste guardado de (tipo, duracao). SO LEITURA: nao recalibra, nao
    grava. Usa o modelo e os parametros ja ajustados (modelo_ir.curvas_influencia/
    contribuicao_atividade, a mesma equacao de `desempenho`).

    Serie diaria desde a mesma origem do ajuste (estado 0 no 1.o dia com carga ou observacao) ate
    `ate` (por omissao a ultima carga/observacao). Dias depois da ultima observacao usada marcam-se
    'fora_do_ajuste'. A carga do dia t so entra no estado `lag_dias` dias depois (por omissao 1:
    o esforco do dia nao explica o proprio valor); dias sem treino tem carga 0.
    """
    base = {'experimental': True, 'tipo_analise': 'curva_influencia_ir', 'aviso': AVISO_INFLUENCIA,
            'modalidade': tipo, 'duracao_s': duracao}
    aj = db.ultimo_ajuste_ir(tipo, duracao)
    if aj is None:
        return {**base, 'estado': 'sem_ajuste',
                'leitura': 'ainda nao ha ajuste IR para esta modalidade e duracao (corre-se com a '
                           'deteccao/sync ou em /api/modelo/ir/recalibrar); nada foi calculado agora'}
    anterior = None
    validos = (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL)
    tentativa = None
    if aj['estado'] not in validos:
        # a tentativa mais recente falhou/nao convergiu/ficou sem dados: o ultimo ajuste VALIDO fica preservado e e
        # apresentado como DESATUALIZADO (estimado com um P0 mais antigo), nunca como se fosse o ajuste novo
        ultimo_valido = next((r for r in reversed(db.ajustes_ir_historico(tipo, duracao)) if r['estado'] in validos), None)
        if ultimo_valido is not None:
            tentativa = {'versao_ajuste': aj['versao_ajuste'], 'estado': aj['estado'], 'ajustado_em': aj['ajustado_em'],
                         'motivo': aj['motivo'], 'excecao': (aj.get('metricas') or {}).get('excecao')}
            aj = ultimo_valido
    for r in reversed(db.ajustes_ir_historico(tipo, duracao)):
        if r['estado'] in validos and r['versao_ajuste'] != aj['versao_ajuste']:
            anterior = {'versao_ajuste': r['versao_ajuste'], 'estado': r['estado'],
                        'ajustado_em': r['ajustado_em'], 'p0': r['p0'],
                        'versao_referencia_p0': ((r.get('entradas') or {}).get('p0_ref') or {}).get(
                            'versao_referencia')}
            break
    if tentativa is not None:                    # o ajuste valido preservado e o apresentado: identifica-se a si proprio
        anterior = {'versao_ajuste': aj['versao_ajuste'], 'estado': aj['estado'], 'ajustado_em': aj['ajustado_em'],
                    'p0': aj['p0'],
                    'versao_referencia_p0': ((aj.get('entradas') or {}).get('p0_ref') or {}).get('versao_referencia')}
    ref_atual = db.ultima_referencia_p0(tipo, duracao)
    ref_aj = (aj.get('entradas') or {}).get('p0_ref')
    # o ajuste mostrado foi estimado com uma referencia de P0 mais antiga que a ultima registada (ex.: o Solver
    # falhou ou o CP ficou invalido depois): os parametros NAO foram recalibrados com o P0 novo
    p0_desatualizado = bool(ref_atual is not None and (ref_aj or {}).get('assinatura') != ref_atual['assinatura'])
    # a tentativa mais recente (com o P0 novo) falhou/nao convergiu: os parametros validos que existem sao de um
    # ajuste anterior, estimado com outro P0; nada esta recalibrado com a referencia mais recente
    if anterior is not None and aj['estado'] not in (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL):
        p0_desatualizado = True
    if tentativa is not None:
        p0_desatualizado = True
    datas_obs = sorted({str(o['data'])[:10] for o in (aj.get('entradas') or {}).get('observacoes') or []})
    maior_vazio = None
    for a_, b_ in zip(datas_obs, datas_obs[1:]):
        dd = (date.fromisoformat(b_) - date.fromisoformat(a_)).days
        if maior_vazio is None or dd > maior_vazio['dias']:
            maior_vazio = {'de': a_, 'ate': b_, 'dias': dd}
    meta = {'versao_ajuste': aj['versao_ajuste'], 'ajustado_em': aj['ajustado_em'],
            'estado': aj['estado'], 'motivo_ajuste': aj['motivo'], 'leitura': _leitura(aj),
            'n_obs': aj['n_obs'], 'data_ini': aj['data_ini'], 'data_fim': aj['data_fim'],
            'funcao_objetivo': aj['funcao_objetivo'], 'rmse': aj['rmse'], 'r2': aj['r2'],
            'convergiu': aj['convergiu'], 'p0_origem': aj['p0_origem'],
            'avisos_identificabilidade': (aj.get('identificabilidade') or {}).get('avisos') or [],
            'ajuste_valido_anterior': anterior, 'versao_modelo': aj['versao_modelo'],
            # referencia de P0 (MMP3 do ultimo YES) que este ajuste usou; None num ajuste antigo de P0 livre
            'p0_referencia': ref_aj, 'p0_desatualizado': p0_desatualizado,
            'compatibilidade_p0_alvo': (aj.get('metricas') or {}).get('compatibilidade_p0_alvo'),
            'intervalo_maximo_sem_observacoes': maior_vazio,
            'excecao_solver': (tentativa or {}).get('excecao') or (aj.get('metricas') or {}).get('excecao'),
            'tentativa_mais_recente': tentativa,
            'ajuste_apresentado_e_antigo': tentativa is not None}
    if not str(aj.get('p0_origem') or '').startswith(p0m.ORIGEM):
        # ajuste de outra regra de P0 (P0 livre, P0 = CP): incompativel, nunca se desenha como se fosse atual
        return {**base, **meta, 'parametros': None, 'curvas': None, 'p0_incompativel': True,
                'aviso_parametros': 'ajuste de uma regra de P0 anterior (nao e o MMP3 do ultimo YES): incompativel, '
                                    'nao se desenha curva'}
    if aj['estado'] not in (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL):
        return {**base, **meta, 'parametros': None, 'curvas': None,
                'aviso_parametros': 'sem parametros utilizaveis neste ajuste: nao se desenha curva'}
    params = {k: aj[k] for k in ir.NOMES}
    cfg = ((aj.get('configuracao') or {}).get('ir')) or {}
    lag = int(cfg.get('lag_dias', ir.CONFIG_PADRAO['lag_dias']))
    if atividades is None:
        atividades = db.load_activities()
    if atividades is None:
        return {**base, **meta, 'parametros': params, 'curvas': None,
                'aviso_parametros': 'sem atividades na base: nao ha carga para desenhar'}
    lista = _atividades_da_modalidade(atividades, tipo)
    carga_dia = {}
    for a in lista:
        carga_dia[a['data']] = carga_dia.get(a['data'], 0.0) + a['carga']
    obs = (aj.get('entradas') or {}).get('observacoes') or []
    ent_ref = aj.get('entradas') or {}
    atual = _entradas(obs, carga_dia, ent_ref.get('referencia'), ent_ref.get('p0_inicial'))
    desatualizado = atual['carga_digest'] != ent_ref.get('carga_digest')
    d0 = min([o['data'] for o in obs] + list(carga_dia))       # a mesma origem do ajuste
    ultimo = max([o['data'] for o in obs] + list(carga_dia))
    fim = max(ate, d0) if ate else ultimo
    n = (date.fromisoformat(fim) - date.fromisoformat(d0)).days + 1
    cargas = ir.cargas_diarias(carga_dia, d0, fim)
    ag = ir.curvas_influencia(cargas, params, lag)
    dias = [(date.fromisoformat(d0) + timedelta(days=i)).isoformat() for i in range(n)]
    idx = {d: i for i, d in enumerate(dias)}
    # soma das contribuicoes individuais == resposta agregada (menos P0), pela mesma equacao
    soma = {k: np.zeros(n) for k in _ARR}
    for a in lista:
        i = idx.get(a['data'])
        if i is None:
            continue
        c = ir.contribuicao_atividade(n, i, a['carga'], params, lag)
        for k in _ARR:
            soma[k] += c[k]
    verif = {'max_abs_dif_positiva': float(np.max(np.abs(soma['positiva'] - ag['positiva']))) if n else 0.0,
             'max_abs_dif_negativa': float(np.max(np.abs(soma['negativa'] - ag['negativa']))) if n else 0.0,
             'max_abs_dif_liquida': float(np.max(np.abs(soma['liquida'] - (ag['liquida'] - params['p0'])))) if n else 0.0,
             'n_atividades': len([a for a in lista if a['data'] in idx])}
    sel = None
    if atividade_id is not None:
        sel = next((a for a in lista if a['id'] == str(atividade_id)), None)
        if sel is None or sel['data'] not in idx:
            return {**base, **meta, 'erro': 'atividade_nao_encontrada',
                    'leitura_atividade': 'a atividade nao tem carga positiva nesta modalidade/intervalo'}
    contrib = None
    if sel is not None:
        c = ir.contribuicao_atividade(n, idx[sel['data']], sel['carga'], params, lag)
        contrib = {'atividade': sel, 'inicio_do_efeito': dias[idx[sel['data']] + lag] if idx[sel['data']] + lag < n else None,
                   **{k: _arr(c[k]) for k in _ARR}}
    recentes = sorted(lista, key=lambda x: (x['data'], str(x['id'])), reverse=True)
    return {**base, **meta, 'parametros': params,
            'parametros_identificados': aj['estado'] == ir.EST_IDENTIFICAVEL,
            'aviso_parametros': None if aj['estado'] == ir.EST_IDENTIFICAVEL else
            'parametros NAO identificaveis: a curva descreve este ajuste, nao fisiologia confirmada',
            'lag_dias': lag, 'ajuste_desatualizado': bool(desatualizado),
            'tratamento_da_carga': {
                'proprio_dia': ('a carga do dia t entra no estado so em t+%d: o esforco nao explica o '
                                'proprio valor' % lag) if lag >= 1 else
                'lag 0: a carga do proprio dia ja conta no estado (igual a folha; com circularidade)',
                'dias_sem_treino': 'carga 0 (os estados continuam a decair)',
                'estado_inicial': 'E = G = 0 no primeiro dia com carga ou observacao (como no ajuste)'},
            'curvas': {'datas': dias, 'positiva': _arr(ag['positiva']), 'negativa': _arr(ag['negativa']),
                       'liquida': _arr(ag['liquida']), 'carga_dia': _arr(cargas),
                       'fora_do_ajuste_desde': next((d for d in dias if aj['data_fim'] and d > aj['data_fim']), None)},
            'observacoes': [{'data': o['data'], 'watts': o['watts'], 'test_id': o['test_id']} for o in obs],
            'verificacao_aditividade': verif, 'contribuicao_atividade': contrib,
            'atividades': recentes[:MAX_ATIVIDADES_LISTA],
            'atividades_truncadas': len(recentes) > MAX_ATIVIDADES_LISTA}



# ── MMP3 e CP Model: contexto e impacto (SO LEITURA; nada e gravado, o CP Model nao e alterado) ────────────

def _cp_model():
    """Constantes/funcoes do CP Model (so leitura). Importado sem alterar nada nele."""
    import os
    import sys
    u = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'utils')
    if u not in sys.path:
        sys.path.insert(0, u)
    import cp_model as cpm
    return cpm


def impacto_cp_model(mod, curvas, ate, marcos=None, modo='coerente'):
    """INFORMATIVO: como o ponto MMP3 (180 s) entra no CP Model e que CP resultaria AGORA. Reutiliza
    `cp_model.pontos_de_curvas` e `calcular_cp_completo` (a mesma selecao dos MMP, season e duplicados da gravacao do
    CP), so com curvas ate `ate`. O 180 s e uma duracao EXTRA em todas as modalidades: so os modelos de 3
    parametros o consomem; M1/M2/M3 nao mudam e o ponto nao substitui um de 5, 12 ou 20 min. Os resultados sao
    PROVISORIOS e NAO influenciam o IR: o P0 vem do MMP3 do ultimo YES, nao do CP Model."""
    cpm = _cp_model()
    from config import season_de
    ate = str(ate)[:10]
    registos = [{'date': str(c['date'])[:10], 'secs': c.get('secs'), 'watts': c.get('watts'),
                 'season': season_de(str(c['date'])[:10], marcos or None)}
                for c in curvas or [] if isinstance(c, dict) and c.get('type') == mod and str(c.get('date'))[:10] <= ate]
    out = {'modalidade': mod, 'provisorio': True, 'ate': ate, 'duracao_mmp3_s': DURACAO_IR,
           'duracao_so_em_modelos_3p': DURACAO_IR not in cpm.DURACOES_CLASSICAS.get(mod, []),
           'ponto_mmp3': None, 'modelos_provisorios': {}, 'influencia_no_ir': False}
    try:
        dados = cpm.pontos_de_curvas(registos, mod, season_activa=season_de(ate, marcos or None), modo=modo)
        datas = dados.get('datas') or {}
        full = {int(t): w for w, t in (dados.get('all_mmp_pts_full') or [])}
        if DURACAO_IR in full:
            out['ponto_mmp3'] = {'watts': full[DURACAO_IR], 'data': datas.get(str(DURACAO_IR))}
        res = cpm.calcular_cp_completo(dados, mod)
        for nome, m in (res.get('modelos') or {}).items():
            out['modelos_provisorios'][nome] = {'cp': m.get('cp'), 'wp': m.get('wp'), 'see_pct': m.get('see_pct'),
                                                 'n_pts': m.get('n_pts'), 'classico': bool(m.get('classico'))}
        out['melhor_see_classico_provisorio'] = (res.get('melhor') or {}).get('nome')
    except Exception as e:                                   # nunca estraga a vista do IR
        out['erro'] = f'{type(e).__name__}: {e}'
    return out


def contexto_mmp3(curvas, atividades, ate, marcos=None, revisoes=None, historico_yes=None):
    """({modalidade: contexto MMP3 + impacto informativo no CP Model}, p0_global), so leitura, so com dados ate `ate`.
    Os esforcos avaliados sao os mesmos que alimentam o ajuste e o P0 global (uma unica fonte)."""
    ignorar, _horas = _ignorar_e_horas(atividades)
    ent = entradas_mmp3(curvas, atividades, ate, marcos, revisoes)
    out = {}
    for mod in mt.MODALIDADES:
        c = mmp3.contexto(curvas, mod, ate, ignorar, None, marcos, esforcos=ent[mod][1])   # tags/intervalos ja nas entradas
        c['cp_model'] = impacto_cp_model(mod, curvas, ate, marcos)
        out[mod] = c
    return out, p0_global_das_entradas(ent, historico_yes, rejeitados_manualmente(revisoes))


# estados de apresentacao do ajuste do IR (um por modalidade)
EI_VALIDO = 'valido'
EI_DESATUALIZADO = 'desatualizado'
EI_NAO_IDENT = 'nao_identificavel'
EI_SEM_CP = 'sem_referencia_mmp3'      # nao existe nenhum registo MMP3 YES: sem P0, sem curva
EI_ERRO_SOLVER = 'erro_solver'
EI_SEM_AJUSTE = 'sem_ajuste'


def estado_ir(j, p0_atual):
    """Estado do ajuste de uma modalidade a partir da resposta de `curva_influencia` e do P0 global atual."""
    if j.get('estado') == 'sem_ajuste':
        return EI_SEM_CP if (p0_atual is not None and p0_atual.get('valida') is False) else EI_SEM_AJUSTE
    if p0_atual is not None and p0_atual.get('valida') is False:
        return EI_SEM_CP                       # nao existe registo MMP3 YES: nenhum ajuste e "atual"
    if j.get('p0_incompativel'):
        return EI_DESATUALIZADO                # ajuste de outra regra de P0: nao foi recalibrado com a atual
    if j.get('excecao_solver') or j.get('tentativa_mais_recente'):
        return EI_ERRO_SOLVER
    if j.get('p0_desatualizado'):               # P0 mais antigo que a ultima referencia (a carga nova so e aviso)
        return EI_DESATUALIZADO
    if j.get('estado') in (ir.EST_INSUFICIENTE, ir.EST_SEM_CONVERGENCIA):
        return j['estado']
    return EI_VALIDO if j.get('estado') == ir.EST_IDENTIFICAVEL else EI_NAO_IDENT


# ── curvas de influencia CONJUNTAS (uma duracao, todas as modalidades; so leitura) ───────────
#
# Nao ha calculo novo do IR: cada modalidade vem de `curva_influencia` (o ULTIMO ajuste guardado,
# a mesma equacao e o mesmo lag). Aqui so se (1) alinham as series num eixo diario comum, (2) normaliza
# cada componente pelo P0 do proprio ajuste (apresentacao) e (3) assinala o que e implausivel.
AVISO_CONJUNTO = ('Curvas descritivas dos ULTIMOS AJUSTES guardados (dentro da amostra), uma por modalidade, para a '
                  'duracao escolhida. Cada modalidade tem o seu proprio modelo (sem influencia cruzada) e os seus '
                  'parametros: as componentes positiva (K1*E) e negativa (K2*G) sao componentes matematicas do '
                  'modelo, nao medicoes fisiologicas; a liquida e positiva menos negativa (a resposta completa e '
                  'P0 + liquida). Nao e desempenho observado nem previsao validada. A escala em % do P0 e so uma '
                  'transformacao de apresentacao: nao prova que as modalidades sejam fisiologicamente comparaveis.')
LIMITE_PLAUSIVEL_PCT = 100.0
ESCALA_PCT_P0 = 'pct_p0'


def _p0_valido(p0):
    return isinstance(p0, (int, float)) and not isinstance(p0, bool) and math.isfinite(p0) and p0 > 0


def _no_limite(avisos):
    """[{parametro, lado}] a partir dos avisos 'parametro_no_limite:<p>:<lado>' do ajuste."""
    out = []
    for a in avisos or []:
        p = str(a).split(':')
        if len(p) == 3 and p[0] == 'parametro_no_limite':
            out.append({'parametro': p[1], 'lado': p[2]})
    return out


def _estado_conjunto(j):
    """Estado de apresentacao de uma modalidade a partir da resposta de `curva_influencia`."""
    e = j.get('estado')
    if e == 'sem_ajuste':
        return 'sem_ajuste'
    if e == ir.EST_INSUFICIENTE:
        return 'dados_insuficientes'
    if e == ir.EST_SEM_CONVERGENCIA:
        return 'sem_convergencia'
    if not j.get('curvas'):
        return 'sem_atividades'
    return 'identificavel' if e == ir.EST_IDENTIFICAVEL else 'nao_identificavel'


def estado_solver(j, p0_atual, n_yes, cfg=None):
    """Estado do SOLVER de uma modalidade (so leitura), separado de P0 e de identificabilidade:
    nao_executado (sem P0 valido, ou sem ajuste gravado), dados_insuficientes (o Solver foi chamado e recusou por
    falta de observacoes/datas/cobertura), falha (nao convergiu ou excecao), executado_nao_identificavel,
    executado_ok. Nunca se diz que falhou quando nunca foi chamado. `faltam_observacoes` = min_obs - YES atuais."""
    c = ir._cfg(cfg)
    base = {'n_observacoes_yes': n_yes, 'min_obs': c['min_obs'], 'min_datas_distintas': c['min_datas_distintas'],
            'min_dias_cobertura': c['min_dias_cobertura'], 'faltam_observacoes': max(0, c['min_obs'] - n_yes)}
    e = j.get('estado')
    if p0_atual is not None and p0_atual.get('valida') is False:
        return {**base, 'estado': 'nao_executado', 'motivo': 'sem_p0'}
    if e == 'sem_ajuste':
        return {**base, 'estado': 'nao_executado', 'motivo': 'sem_observacoes' if not n_yes else 'sem_ajuste_gravado'}
    if e == ir.EST_INSUFICIENTE:
        return {**base, 'estado': 'dados_insuficientes', 'motivo': (j.get('motivo_insuficiente') or j.get('motivo'))}
    if e == ir.EST_SEM_CONVERGENCIA or j.get('excecao_solver'):
        return {**base, 'estado': 'falha', 'motivo': 'excecao_ou_sem_convergencia'}
    if e == ir.EST_IDENTIFICAVEL:
        return {**base, 'estado': 'executado_ok', 'motivo': None}
    return {**base, 'estado': 'executado_nao_identificavel', 'motivo': None}


def _plausibilidade(pct):
    """Magnitude maxima (em % do P0) por componente e dias acima de LIMITE_PLAUSIVEL_PCT. Nao corta nada."""
    out = {'limite_pct': LIMITE_PLAUSIVEL_PCT, 'max_abs_pct': {}, 'dias_acima': {}, 'excede': False}
    for k in ('positiva', 'negativa', 'liquida'):
        v = [abs(x) for x in pct[k] if x is not None]
        out['max_abs_pct'][k] = round(max(v), 2) if v else None
        out['dias_acima'][k] = sum(1 for x in v if x > LIMITE_PLAUSIVEL_PCT)
        out['excede'] = out['excede'] or out['dias_acima'][k] > 0
    return out


OVERALL_INDISPONIVEL = {
    'disponivel': False,
    'motivo': ('Sem definicao matematica valida nesta fase: os watts de Bike, Row, Run e Ski sao desempenhos '
               'diferentes (cada modalidade com o seu P0, K e T), por isso nao se somam nem se fazem medias; e o '
               'modelo global existente (/api/modelo/ir/global) devolve parametros partilhados e testes, mas '
               'nenhuma serie diaria.'),
    'falta': ['uma serie diaria definida pelo modelo global (hoje nao existe no resultado);',
              'carga efetiva comum (lambda ~ 1) com lambda estimado e intervalo de confianca, hoje so ha testes '
              'indicativos de lambda = 0 e lambda = 1;',
              'ajuste global identificavel e com homogeneidade nao rejeitada, persistido (hoje e recalculado em '
              'cada pedido e nao e guardado);',
              'validacao preditiva (walk-forward) do global.'],
    'referencia': '/api/modelo/ir/global?duracao=<s>'}


def _estado_p0(mod, duracao, global_, aj_ref, desatualizado=False):
    """Estado do P0 de uma modalidade para a UI: o que o ultimo ajuste usou e o P0 global de hoje (MMP3 do ultimo
    YES). So leitura. `desatualizado`: o ajuste mostrado usou outra referencia que a atual."""
    ult = db.ultima_referencia_p0(mod, duracao)
    out = {'usado_no_ajuste': aj_ref,
           'origem_ajuste': (p0m.ORIGEM if (aj_ref or {}).get('formulacao') == p0m.FORMULA_ID else
                             ('outra_regra_de_p0' if aj_ref else 'p0_livre_anterior')),
           'ultima_referencia': _ref_resumo(ult), 'atual': None}
    des = bool(desatualizado or (ult is not None and (aj_ref or {}).get('assinatura') != ult['assinatura']))
    r = p0m.resolver_p0(mod, duracao, global_)
    if r['valida']:
        out['atual'] = {'valida': True, 'valor_p0': r['valor_p0'], 'origem': r['origem'], 'registo': r['registo'],
                        'estado_referencia': r['estado_referencia'], 'assinatura': r['assinatura'],
                        'versao_regra': r['versao_regra']}
        des = des or bool(aj_ref and aj_ref.get('assinatura') != r['assinatura'])
    else:
        out['atual'] = {'valida': False, 'motivo': r['motivo'], 'detalhe': r['detalhe'],
                        'estado_referencia': r['estado_referencia']}
    out['desatualizado'] = des
    return out


def curvas_conjuntas(duracao=None, ate=None, atividades=None, curvas=None, marcos=None, revisoes=None):
    """Curvas de influencia das quatro modalidades para UMA duracao, num eixo diario comum.

    SO LEITURA: reutiliza `curva_influencia` (ultimo ajuste guardado de cada modalidade), nunca recalibra
    nem grava. Antes da origem de uma modalidade os valores sao None (nao ha serie, nao e zero); sem ajuste
    ou com dados insuficientes nao ha curva. Escala: % do P0 do proprio ajuste (so se P0 for finito e > 0).
    Nada e cortado: a plausibilidade (> 100 % do P0) apenas se assinala."""
    duracao = DURACAO_IR if duracao is None else duracao
    mods = list(config.CICLICOS)
    base = {'experimental': True, 'tipo_analise': 'curvas_influencia_ir_conjunto', 'aviso': AVISO_CONJUNTO,
            'duracao_s': duracao, 'modalidades_ordem': mods,
            'cores': {m: config.CORES_MOD[m] for m in mods},
            'escala': {'tipo': ESCALA_PCT_P0,
                       'descricao': 'pct = 100 x componente / P0 do proprio ajuste (negativa com sinal menos); '
                                    'w = valor original em watts do alvo da modalidade (negativa >= 0, como no modelo)'},
            'overall': dict(OVERALL_INDISPONIVEL)}
    if atividades is None:
        atividades = db.load_activities()
    js = {m: curva_influencia(m, duracao, ate=ate, atividades=atividades) for m in mods}
    hoje = (ate or mt.agora_utc().date().isoformat())
    if curvas is None:
        curvas = db.load_power_curves() or []
    ctx3, global_ = contexto_mmp3(curvas, atividades, hoje, marcos, revisoes,
                                  historico_yes=db.yes_historico())
    series = {m: j['curvas'] for m, j in js.items() if j.get('curvas')}
    datas = []
    if series:
        ini = min(c['datas'][0] for c in series.values())
        fim = ate if ate else max(c['datas'][-1] for c in series.values())
        fim = max(fim, ini)
        for m in list(series):                       # estender (carga 0, so decaimento) ate ao fim comum
            if series[m]['datas'][-1] < fim:
                js[m] = curva_influencia(m, duracao, ate=fim, atividades=atividades)
                series[m] = js[m]['curvas']
        n = (date.fromisoformat(fim) - date.fromisoformat(ini)).days + 1
        datas = [(date.fromisoformat(ini) + timedelta(days=i)).isoformat() for i in range(n)]
    idx = {d: i for i, d in enumerate(datas)}
    itens, lags = {}, set()
    for m in mods:
        j = js[m]
        estado = _estado_conjunto(j)
        P = j.get('parametros')
        item = {'modalidade': m, 'cor': config.CORES_MOD[m], 'estado': estado, 'estado_ajuste': j.get('estado'),
                'leitura': j.get('leitura') or j.get('aviso_parametros'),
                'parametros': ({k: P[k] for k in ir.NOMES} if P else None),
                'parametros_identificados': (j.get('estado') == ir.EST_IDENTIFICAVEL) if P else None,
                'avisos_identificabilidade': j.get('avisos_identificabilidade') or [],
                'no_limite': _no_limite(j.get('avisos_identificabilidade')),
                'ajuste': {k: j.get(k) for k in ('versao_ajuste', 'ajustado_em', 'n_obs', 'data_ini', 'data_fim',
                                                 'funcao_objetivo', 'rmse', 'r2', 'convergiu', 'p0_origem')},
                'ajuste_valido_anterior': j.get('ajuste_valido_anterior'),
                'ajuste_desatualizado': j.get('ajuste_desatualizado'),
                'p0_desatualizado': j.get('p0_desatualizado'),
                'compatibilidade_p0_alvo': j.get('compatibilidade_p0_alvo'),
                'intervalo_maximo_sem_observacoes': j.get('intervalo_maximo_sem_observacoes'),
                'excecao_solver': j.get('excecao_solver'),
                'periodo_observado': {'ini': j.get('data_ini'), 'fim': j.get('data_fim')},
                'extrapolacao_antes_das_observacoes': None,
                'lag_dias': j.get('lag_dias'), 'tem_curva': False, 'normalizacao': None,
                'motivo_normalizacao': None, 'inicio_serie': None, 'fora_do_ajuste_desde': None,
                'plausibilidade': None, 'w': None, 'pct': None,
                'p0': _estado_p0(m, duracao, global_, j.get('p0_referencia'), bool(j.get('p0_desatualizado'))),
                'mmp3': ctx3[m], 'tentativa_mais_recente': j.get('tentativa_mais_recente'),
                'ajuste_apresentado_e_antigo': bool(j.get('ajuste_apresentado_e_antigo'))}
        item['estado_ir'] = estado_ir({**j, 'p0_desatualizado': item['p0']['desatualizado']}, item['p0']['atual'])
        item['solver'] = estado_solver(j, item['p0']['atual'], (ctx3[m] or {}).get('n_elegiveis') or 0)
        c = series.get(m)
        if c and datas:
            p0 = P['p0']
            lags.add(j.get('lag_dias'))
            w = {k: [None] * len(datas) for k in ('positiva', 'negativa', 'liquida')}
            pc = {k: [None] * len(datas) for k in ('positiva', 'negativa', 'liquida')}
            norm = _p0_valido(p0)
            for k, d in enumerate(c['datas']):
                i = idx.get(d)
                if i is None:
                    continue
                pos, neg = c['positiva'][k], c['negativa'][k]
                w['positiva'][i], w['negativa'][i], w['liquida'][i] = pos, neg, round(pos - neg, 3)
                if norm:
                    pc['positiva'][i] = round(100.0 * pos / p0, 2)
                    pc['negativa'][i] = round(-100.0 * neg / p0, 2)
                    pc['liquida'][i] = round(100.0 * (pos - neg) / p0, 2)
            item['extrapolacao_antes_das_observacoes'] = (
                {'de': c['datas'][0], 'ate': j.get('data_ini'),
                 'dias': (date.fromisoformat(j['data_ini']) - date.fromisoformat(c['datas'][0])).days}
                if j.get('data_ini') and c['datas'][0] < j['data_ini'] else None)
            item.update({'tem_curva': True, 'w': w, 'inicio_serie': c['datas'][0],
                         'fora_do_ajuste_desde': c.get('fora_do_ajuste_desde'),
                         'normalizacao': ESCALA_PCT_P0 if norm else 'indisponivel',
                         'motivo_normalizacao': None if norm else 'p0_invalido_ou_nao_positivo'})
            if norm:
                item['pct'] = pc
                item['plausibilidade'] = _plausibilidade(pc)
        itens[m] = item
    com_curva = [m for m in mods if itens[m]['tem_curva']]
    lags.discard(None)
    return {**base, 'p0_global': global_, 'estado': 'ok' if com_curva else 'sem_curvas', 'datas': datas,
            'data_ini': datas[0] if datas else None, 'data_fim': datas[-1] if datas else None,
            'modalidades_com_curva': com_curva,
            'modalidades_sem_curva': [m for m in mods if m not in com_curva],
            'convencao_temporal': {
                'lag_dias': sorted(lags), 'lags_diferentes': len(lags) > 1,
                'eixo': 'diario e comum; cada serie comeca na origem do seu ajuste (E = G = 0)',
                'antes_da_origem': 'valores ausentes (null): nao ha serie, nao e contribuicao nula',
                'dias_sem_treino': 'carga 0: so decaimento exponencial dos estados',
                'depois_do_ultimo_ajuste': 'marcado em fora_do_ajuste_desde (por modalidade)'},
            'modalidades': itens}


# ── modelo IR global de referencia (analise SEPARADA; so leitura, nao persistida) ────────────

AVISO_GLOBAL = ('Analise GLOBAL de referencia, separada dos modelos por modalidade (que nao substitui '
                'nem altera): um grupo = uma duracao, dinamica (K1, K2, T1, T2) partilhada e coeficiente '
                'de transferencia de carga entre modalidades estimado. Ajuste historico (dentro da amostra): '
                'nao e previsao validada, nao e persistida e nao atualiza nada. Calculada de novo em cada '
                'pedido (alguns segundos; ate ~30 s quando o teste de homogeneidade da sinal e corre o '
                'bootstrap; ?rapido=1 salta o bootstrap).')


def _leitura_global(r):
    e = r['estado']
    if e == G.EST_INSUFICIENTE:
        return 'dados insuficientes para o global (' + str(r.get('motivo')) + '): parametros nao estimados'
    if e == G.EST_SEM_CONVERGENCIA:
        return 'o otimizador nao convergiu: parametros globais nao devem ser usados'
    if e == G.EST_HETEROGENEO:
        return ('o teste de homogeneidade REJEITA a dinamica partilhada entre as unidades: o global nao '
                'descreve estas modalidades como uma so; nao interpretar os parametros partilhados')
    if e == G.EST_NAO_IDENTIFICAVEL:
        return ('ajuste global com parametros NAO identificaveis (' +
                ', '.join(r['identificabilidade']['avisos']) + '): nao e fisiologia confirmada')
    return ('ajuste global com parametros partilhados identificaveis nesta amostra e homogeneidade nao '
            'rejeitada; continua a ser ajuste dentro da amostra, nao previsao validada')


def analise_global(duracao, historico_obs=None, revisoes=None, atividades=None, curvas=None, config=None):
    """Ajuste global (modelo_ir_global) para UMA duracao, juntando as modalidades com observacoes
    elegiveis (as mesmas do modelo por modalidade) e as cargas de TODAS as modalidades. So leitura:
    nao grava, nao altera os ajustes por modalidade. Sem argumentos de dados le da base."""
    base = {'experimental': True, 'tipo_analise': 'modelo_ir_global_referencia', 'aviso': AVISO_GLOBAL,
            'limitacoes': list(G.LIMITES), 'grupo': {'duracao_s': duracao}}
    if historico_obs is None:
        historico_obs = db.observacoes_auto_historico()
        revisoes = db.revisoes_testes_modelo()
        atividades = db.load_activities()
        curvas = db.load_power_curves() or []
    if atividades is None:
        return {**base, 'estado': G.EST_INSUFICIENTE, 'motivo': 'atividades_indisponiveis',
                'parametros': None, 'leitura': 'sem atividades na base: nao ha carga'}
    sel = selecionar(historico_obs, revisoes, atividades, curvas)
    cargas, info = carga_por_modalidade(atividades)
    unidades, sem = [], []
    for m in mt.MODALIDADES:
        usadas = (sel.get((m, duracao)) or {}).get('usadas') or []
        if not usadas:
            sem.append(m)
            continue
        obs = _obs_min(usadas)
        unidades.append({'id': f'{m}:{duracao}', 'modalidade': m, 'datas': [o['data'] for o in obs],
                         'y': [o['watts'] for o in obs]})
    base['grupo'].update({'unidades': [{'id': u['id'], 'n_obs': len(u['datas'])} for u in unidades],
                          'modalidades_sem_observacoes': sem})
    if not unidades:
        return {**base, 'estado': G.EST_INSUFICIENTE, 'motivo': 'sem_observacoes_elegiveis',
                'parametros': None, 'leitura': 'nao ha observacoes elegiveis para esta duracao'}
    r = G.ajustar_global(unidades, cargas, config)
    por_mod = {}
    for m in mt.MODALIDADES:
        a = db.ultimo_ajuste_ir(m, duracao)
        if a is not None:
            por_mod[m] = {'versao_ajuste': a['versao_ajuste'], 'estado': a['estado'],
                          'parametros': {k: a[k] for k in ir.NOMES}, 'n_obs': a['n_obs']}
    return {**base, **r, 'leitura': _leitura_global(r), 'carga': info,
            'modelos_por_modalidade_para_comparar': por_mod,
            'nota_comparacao': 'so informativo: os parametros por modalidade estao em W por unidade de carga '
                               'propria e os do global em fracao do nivel de base com carga efetiva '
                               '(propria + lambda x outras); K_w do global por unidade em por_unidade'}


# ── avaliacao preditiva (separada do ajuste) ─────────────────────────────

def p0_conhecido_antes(referencias, data):
    """P0 (float) da referencia MAIS RECENTE cuja `conhecida_desde` e estritamente anterior a `data`
    (AAAA-MM-DD), ou None. Uma referencia so passa a existir para o IR quando foi gravada: nunca se usa uma
    referencia futura, nem a data de referencia do instantaneo de CP (escolhida pelo utilizador e cujo
    conteudo pode incluir dados posteriores)."""
    d = str(data)[:10]
    cand = [r for r in referencias or [] if r.get('conhecida_desde') and str(r['conhecida_desde'])[:10] < d]
    if not cand:
        return None
    return float(max(cand, key=lambda r: r['versao_referencia'])['valor_p0'])


def validar_preditivo(historico_obs, revisoes, atividades, curvas, modos=V.MODOS, tipo=None,
                      duracao=None, auto=None, config_ir=None, referencias_p0=None):
    """Walk-forward do IR contra M0/M1/M2 nos MESMOS alvos, para cada unidade e modo.

    Para cada alvo: treino = so observacoes de data estritamente anterior (no modo
    conhecido_na_epoca, so as ja detetadas antes do alvo); cargas ate ao dia anterior; o IR e
    reajustado SO com isso e preve o alvo. O erro de ajuste historico nunca entra aqui.
    auto: FonteAuto (modelo_observacoes_auto); sem ele usa as observacoes atuais do historico.
    LEGADO (60/300/1200 s): nao valida o IR baseado em MMP3 (180 s) nem o alimenta.
    P0: fixo e igual a referencia de P0 mais recente JA CONHECIDA do IR antes da data do alvo
    (`conhecida_desde` estritamente anterior), nos dois modos; sem ela o alvo nao e previsto
    ('sem_referencia_p0_conhecida'). referencias_p0: historico de referencias (testes); omisso = base.
    """
    auto = auto or moa.FonteAuto(historico_obs, revisoes)
    atuais = E.estado_atual_por_teste(revisoes or [])
    cpa = E.curvas_por_atividade(curvas or [])
    atual = E.selecionar_observacoes_auto(auto.atuais(), atuais, atividades, cpa)
    cargas, _ = carga_por_modalidade(atividades)
    cfg = ir._cfg(config_ir)
    cache = {}
    if referencias_p0 is None:
        referencias_p0 = db.referencias_p0_historico(tipo, duracao)

    def na_epoca(corte):
        if corte not in cache:
            parcial = [r for r in (revisoes or []) if (V._dia(r.get('revisado_em')) or '9999') < corte]
            cache[corte] = E.selecionar_observacoes_auto(
                auto.conhecidas_antes(corte), E.estado_atual_por_teste(parcial), atividades, cpa)
        return cache[corte]

    itens = []
    for modo in modos:
        for (m, d) in sorted(atual):
            if (tipo and m != tipo) or (duracao is not None and d != duracao):
                continue
            alvos = atual[(m, d)]['usadas']
            if modo == 'retrospectivo':
                treino_de = lambda a, alvos=alvos: [o for o in alvos if o['data'] < a['data']]  # noqa: E731
            else:
                treino_de = lambda a, m=m, d=d: na_epoca(a['data'])[(m, d)]['usadas']  # noqa: E731
            refs = [r for r in referencias_p0 if r['modalidade'] == m and r['duracao_s'] == d]
            p0_de = lambda a, refs=refs: p0_conhecido_antes(refs, a['data'])  # noqa: E731
            itens.append(_avaliar_unidade(modo, m, d, alvos, treino_de, cargas[m], cfg, p0_de))
    cfg_rec = {'ir': cfg, 'versao_modelo': ir.VERSAO_IR, 'versao_validacao': V.VERSAO_VALIDACAO,
               'criterios_suficiencia': {'min_alvos': V.MIN_ALVOS_AVALIAVEL,
                                         'min_datas_distintas': V.MIN_DATAS_AVALIAVEL,
                                         'min_dias_cobertura': V.MIN_DIAS_AVALIAVEL}}
    return {'experimental': True, 'tipo_analise': 'validacao_preditiva_ir_walk_forward',
            'promocao': False, 'configuracao': cfg_rec, 'itens': itens,
            'aviso': ('Previsao um passo a frente com treino estritamente anterior. So esforcos '
                      'detetados (perto do melhor anterior): mede o proximo esforco detetado, nao a '
                      'capacidade. O erro de ajuste historico (modelo_ir_ajustes) nao entra aqui. '
                      'Comparacao feita so nos alvos em que o IR chegou a prever.')}


def _avaliar_unidade(modo, mod, dur, alvos, treino_de, carga_dia, cfg, p0_de=None):
    prev_ir = ir.avaliar_preditivo(alvos, treino_de, carga_dia, cfg, p0_de=p0_de)
    por_id = {a['test_id']: a for a in alvos}
    linhas, excl = [], {}
    for p in prev_ir:
        a = por_id[p['test_id']]
        if p['ir'] is None:
            excl[p['motivo']] = excl.get(p['motivo'], 0) + 1
            continue
        treino = [o for o in treino_de(a) if o['data'] < a['data']]
        base, motivo = V.previsoes_metodos(treino, a['data'])
        if base is None:
            excl[motivo] = excl.get(motivo, 0) + 1
            continue
        linhas.append({**p, **{k: base[k] for k in ('M0', 'M1', 'M2')},
                       'm1_ordem_ambigua': base['m1_ordem_ambigua']})
    est, cob = V.suficiencia([{'data': r['data']} for r in linhas])
    er = {k: [r[k] - r['y'] for r in linhas] for k in ('M0', 'M1', 'M2')}
    er['IR'] = [r['ir'] - r['y'] for r in linhas]
    com_ic = est == V.EST_AVALIAVEL
    dif = {b: V.diferenca(er['IR'], er[b], V._semente(modo, mod, dur, 'IR-' + b), com_ic)
           for b in ('M0', 'M1')}
    item = {
        'modalidade': mod, 'duracao_s': dur, 'modo': modo,
        'n_alvos': len(alvos), 'n_alvos_com_previsao_ir': len(linhas),
        'alvos_sem_previsao_ir': dict(sorted(excl.items())),
        'estado_suficiencia': est, 'suficiencia': cob,
        'metricas': {k: V.metricas(v) for k, v in er.items()},
        'diferenca_ir_menos_baseline': dif,
        'cobertura_estados_ajuste': _contar(prev_ir, 'estado_ajuste'),
        'n_previsoes_com_avisos_identificabilidade': sum(
            1 for p in prev_ir if p.get('avisos_identificabilidade')),
        'previsoes': [{'test_id': r['test_id'], 'data': r['data'], 'y': r['y'], 'ir': r['ir'],
                       'M0': r['M0'], 'M1': r['M1'], 'M2': r['M2'], 'n_treino': r['n_treino']}
                      for r in linhas],
        'tipo': 'previsao_um_passo_walk_forward (nao e ajuste historico)',
    }
    if est == V.EST_AVALIAVEL:
        v = V.veredicto(dif)
        item['veredicto'] = {'m2_melhor_que_ambos_baselines': 'ir_melhor_que_ambos_baselines',
                             'm2_pior_que_algum_baseline': 'ir_pior_que_algum_baseline'}.get(
                                 v, 'diferenca_nao_demonstrada')
    else:
        item['veredicto'] = None
        item['motivo_sem_veredicto'] = ('sem_alvos_com_previsao_ir' if est == V.EST_SEM_ALVOS
                                        else 'amostra_insuficiente_para_avaliar')
    return item


def _contar(prev, campo):
    out = {}
    for p in prev:
        k = p.get(campo) or 'sem_ajuste'
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def apos_cp_gravado(resultado_cp, modalidade, origem='cp_model'):
    """Mantido por compatibilidade com o gancho de api_cp (/api/perfil/guardar): o P0 do IR NAO depende do CP
    Model (e o MMP3 do ultimo YES), por isso gravar um CP nao recalibra nada nem toca no IR."""
    return {'ok': False, 'ignorado': 'p0_do_ir_nao_depende_do_cp'}


def apos_deteccao(resultado_deteccao, origem='sync'):
    """Gancho para quem acabou de correr a deteccao MMP (sincronizacao ou atualizacao manual):
    recalibra so o que mudou. Nunca levanta: falhar o IR nao pode estragar a sincronizacao nem
    a deteccao. Se a deteccao falhou nao se faz nada (as entradas nao mudaram por la)."""
    if not isinstance(resultado_deteccao, dict) or not resultado_deteccao.get('ok'):
        return {'ok': False, 'ignorado': 'deteccao_sem_sucesso'}
    try:
        return recalibrar(origem=origem)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'ok': False, 'erro': f'{type(e).__name__}: {e}'}


# ── modo exploratorio (historico, nao validado) ────────────────────────────────────────────────

def analise_exploratoria(duracoes=None, historico_obs=None, revisoes=None, atividades=None, curvas=None,
                         referencia=None, config_ir=None):
    """Curvas exploratorias historicas por modalidade e duracao (60/300/1200 s), com P0 livre por unidade.

    SO LEITURA e NAO PERSISTIDO: le o historico do detetor e as atividades, nunca grava em modelo_ir_ajustes
    nem em modelo_ir_p0_referencias, nao le nem substitui os ajustes calibrados e nao usa o P0 do IR de 180 s.
    Sem argumentos de dados le da base. Devolve status ERRO com a mensagem (nunca uma curva artificial) se faltar
    a base ou as atividades."""
    duracoes = tuple(duracoes) if duracoes else mex.DURACOES_HISTORICAS
    base = {'experimental': True, 'modo': mex.MODO, 'rotulo': mex.ROTULO, 'persistido': False,
            'tipo_analise': 'ir_exploratorio_historico', 'aviso': mex.AVISO,
            'duracoes': list(duracoes), 'limitacoes': list(ir.LIMITES),
            'cores': {m: config.CORES_MOD[m] for m in config.CICLICOS}}
    if historico_obs is None:
        if not db.ENABLED:
            return {**base, 'status': 'ERRO', 'mensagem': 'sem base de dados'}
        historico_obs = db.observacoes_auto_historico() or []
        revisoes = db.revisoes_testes_modelo()
        atividades = db.load_activities()
        curvas = db.load_power_curves() or []
    if atividades is None:
        return {**base, 'status': 'ERRO', 'mensagem': 'atividades indisponiveis: sem carga, nada foi calculado'}
    hoje = referencia or mt.agora_utc().date().isoformat()
    sel = selecionar(historico_obs, revisoes, atividades, curvas)
    # mesma identificacao de candidatos do calibrado: so o maximo causal ALL-TIME >= limiar (sem exigir tag/intervalo)
    sel = {k: {**v, 'usadas': [o for o in v.get('usadas') or [] if o.get('referencia_tipo') == 'all_time']}
           for k, v in sel.items()}
    cargas, info = carga_por_modalidade(atividades)
    itens = mex.analisar(sel, cargas, hoje, ir._cfg(config_ir), duracoes)
    return {**base, 'status': 'OK', 'referencia': hoje, 'itens': itens, 'carga': info}

