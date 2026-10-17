"""Fase 3 do modelo individual progressivo: validacao temporal (walk-forward) do estimador.

AVALIACAO, nao promocao. O estimador da Fase 2 continua experimental; este modulo nao
altera q, parametros, criterios de elegibilidade nem nenhum calculo de producao. Puro:
sem base de dados, Flask, rede nem relogio. Nao e lido por nenhum modulo de producao.

Pergunta
--------
Por modalidade x duracao (12 unidades independentes, sem misturar nem extrapolar), o
estimador (M2, q=0.01) preve a proxima observacao melhor do que baselines simples?
  M0  media historica expansiva das observacoes anteriores
  M1  ultimo valor observado antes da previsao. Se o ultimo dia do treino tiver varias
      observacoes sem ordem provada pela hora local real, usa-se a MEDIA desse dia: um
      fallback metodologico para nao simular uma sequencia (nao e o ultimo valor literal).
  M2  filtro de nivel local da Fase 2 (previsao pontual = nivel; q predefinido)

O alvo e a potencia da proxima observacao elegivel da mesma modalidade e duracao, tal
como o estimador a define: o MELHOR VALOR DA DURACAO EM TODA A ATIVIDADE (power_curves),
sem trecho identificado. NAO sao testes padronizados.

Walk-forward
------------
Para cada observacao-alvo (por ordem temporal) a informacao usada e so a das observacoes
com data ESTRITAMENTE anterior a data do alvo (filtro defensivo dentro de
`previsoes_metodos`, independente do chamador). Alvos na mesma data partilham o mesmo
conjunto de treino. Os tres metodos correm nas mesmas origens e nos mesmos alvos; uma
origem em que algum metodo nao preve fica fora de todos e e contada com o motivo.

Dois modos (a diferenca importa)
--------------------------------
  retrospectivo       usa o estado ATUAL do registo (revisoes atuais) e ordena pela data
                      da observacao. E uma APROXIMACAO: a decisao de confirmar um teste foi
                      tomada depois, com conhecimento posterior (vies de selecao), e a
                      elegibilidade (icu_ignore_power) e a atual, nao a da epoca.
  conhecido_na_epoca  so conta revisoes com `revisado_em` anterior a data do alvo
                      (reconstroi o historico append-only da Fase 1). Se os testes foram
                      confirmados muito depois, quase nao ha treino: e a consequencia
                      honesta. Mesmo assim os ALVOS sao definidos em retrospetiva e a
                      elegibilidade e a atual (nao ha historico dela).
Nenhum dos modos reconstitui o que o atleta sabia na epoca sobre o `icu_ignore_power`.

Metricas e incerteza
--------------------
erro = previsao - observado (positivo = sobrestima). MAE, RMSE, erro medio com sinal.
Diferencas M2-Mx: dMAE = media(|e2|-|ex|), dRMSE = RMSE2-RMSEx (negativo favorece M2).
IC95 das diferencas por bootstrap em BLOCOS moveis (dependencia temporal), bloco =
round(n^(1/3)), B=2000, semente fixa; so com amostra `avaliavel`. Reporta-se tambem a
autocorrelacao lag-1 das diferencas absolutas (n>=10). O IC do NIVEL da Fase 2 nao e um
intervalo de previsao: a calibracao usa um intervalo preditivo DERIVADO do mesmo modelo
(sd^2 do nivel + sigma^2 do ruido, t com m g.l.), rotulado exploratorio e nao validado.

Suficiencia (fixada antes de ver dados)
---------------------------------------
  sem alvos                              -> inconclusivo_sem_alvos
  < 20 alvos, < 15 datas ou < 90 dias    -> descritivo (so metricas descritivas)
  caso contrario                         -> avaliavel (IC e veredicto)
Veredicto (so em `avaliavel`): "m2_melhor_que_ambos_baselines" exige, face a M0 E a M1,
IC95 de dMAE < 0, melhoria relativa de MAE >= 5% e dRMSE < 0. O simetrico da
"m2_pior_que_algum_baseline"; senao "diferenca_nao_demonstrada". A margem de 5% e uma
convencao declarada, nao um valor derivado dos dados.

q
-
Predefinido (0.01) avaliado a parte. Grelha (0.001, 0.003, 0.01, 0.03, 0.1): sensibilidade
EXPLORATORIA nos mesmos alvos, sem selecao. Selecao ANINHADA exploratoria: em cada origem
escolhe-se q so com erros walk-forward DENTRO do treino (>= 6 observacoes) e preve-se o
alvo; relata-se a parte e nunca entra no veredicto. Nada altera o q de producao.
"""
import hashlib
import json
import math
import random
from datetime import date, datetime

import modelo_estimador as E

VERSAO_VALIDACAO = 1
Q_PADRAO = E.Q_DIA
GRELHA_Q = (0.001, 0.003, 0.01, 0.03, 0.1)
MODOS = ('retrospectivo', 'conhecido_na_epoca')
DESCRICAO_MODO = {
    'retrospectivo': ('Usa todas as observacoes atuais, incluindo as detetadas depois da data de cada alvo '
                      '(conhecimento retrospetivo).'),
    'conhecido_na_epoca': ('So usa observacoes ja detetadas (detetado_em) antes da data de cada alvo; o '
                           'historico anterior a ativacao da deteccao automatica nao pode ser reconstruido '
                           'e nao entra.')}

MIN_ALVOS_AVALIAVEL = 20
MIN_DATAS_AVALIAVEL = 15
MIN_DIAS_AVALIAVEL = 90
MARGEM_RELATIVA = 0.05
MIN_TREINO_Q = 6
MIN_ERROS_Q = 3
MIN_INTERVALOS_CALIBRACAO = 10
MIN_N_ACF = 10
N_BOOT = 2000
SEMENTE = 20261017
NIVEL_NOMINAL = 0.95

EST_SEM_ALVOS = 'inconclusivo_sem_alvos'
EST_DESCRITIVO = 'descritivo'
EST_AVALIAVEL = 'avaliavel'


# ── metricas ────────────────────────────────────────────────────────────────

def metricas(erros):
    """erros: lista de (previsao - observado). Vazio -> valores None."""
    n = len(erros)
    if n == 0:
        return {'n': 0, 'mae': None, 'rmse': None, 'erro_medio': None}
    return {'n': n,
            'mae': sum(abs(e) for e in erros) / n,
            'rmse': math.sqrt(sum(e * e for e in erros) / n),
            'erro_medio': sum(erros) / n}


def autocorrelacao_lag1(xs):
    n = len(xs)
    if n < MIN_N_ACF:
        return None
    m = sum(xs) / n
    den = sum((x - m) ** 2 for x in xs)
    if not den > 0:
        return None
    return sum((xs[i] - m) * (xs[i + 1] - m) for i in range(n - 1)) / den


def comprimento_bloco(n):
    return max(1, int(round(n ** (1.0 / 3.0))))


def _rmse_idx(e, idx):
    return math.sqrt(sum(e[i] * e[i] for i in idx) / len(idx))


def ic_bloco(e2, ex, semente, b=N_BOOT):
    """IC95 (percentis) de dMAE e dRMSE por bootstrap em blocos moveis. Determinista."""
    n = len(e2)
    L = comprimento_bloco(n)
    rnd = random.Random(semente)
    d_abs = [abs(a) - abs(b_) for a, b_ in zip(e2, ex)]
    dm, dr = [], []
    nb = -(-n // L)
    for _ in range(b):
        idx = []
        for _ in range(nb):
            s = rnd.randrange(0, n - L + 1)
            idx.extend(range(s, s + L))
        idx = idx[:n]
        dm.append(sum(d_abs[i] for i in idx) / n)
        dr.append(_rmse_idx(e2, idx) - _rmse_idx(ex, idx))
    dm.sort()
    dr.sort()
    lo, hi = int(math.floor(0.025 * b)), int(math.ceil(0.975 * b)) - 1
    return {'bloco': L, 'replicas': b, 'ic95_d_mae': [dm[lo], dm[hi]],
            'ic95_d_rmse': [dr[lo], dr[hi]]}


def diferenca(e2, ex, semente, com_ic):
    """M2 - Mx nos mesmos alvos. Negativo favorece M2."""
    if not e2:
        return None
    d_abs = [abs(a) - abs(b) for a, b in zip(e2, ex)]
    mae_x = sum(abs(b) for b in ex) / len(ex)
    out = {'d_mae': sum(d_abs) / len(d_abs),
           'd_rmse': metricas(e2)['rmse'] - metricas(ex)['rmse'],
           'd_mae_relativo': (sum(d_abs) / len(d_abs)) / mae_x if mae_x > 0 else None,
           'autocorrelacao_lag1_d_abs': autocorrelacao_lag1(d_abs),
           'ic95_d_mae': None, 'ic95_d_rmse': None, 'bloco': None,
           'motivo_sem_ic': None}
    if com_ic:
        out.update(ic_bloco(e2, ex, semente))
    else:
        out['motivo_sem_ic'] = 'amostra_insuficiente'
    return out


# ── previsao num alvo ───────────────────────────────────────────────────────

def previsoes_metodos(treino, alvo_data, q=Q_PADRAO):
    """(previsoes, None) ou (None, motivo). Usa SO observacoes com data < alvo_data."""
    t = sorted([o for o in treino if o['data'] < alvo_data], key=E.chave_ordem)
    if not t:
        return None, 'sem_historico'
    ser = E.estimar_serie(t, alvo_data, q)
    nivel = ser['resumo']['nivel_w']
    if nivel is None:
        return None, 'estimador_sem_previsao'
    w = [o['watts'] for o in t]
    inc = ser['resumo']['incerteza']
    ic = None
    if inc is not None:
        sd = math.sqrt(inc['sd_w'] ** 2 + inc['sigma_ruido_w'] ** 2)
        tq = E.t975(inc['graus_liberdade'])
        ic = [nivel - tq * sd, nivel + tq * sd]
    m1, ambiguo = ultimo_valor(t)
    return {'M0': sum(w) / len(w), 'M1': m1, 'M2': nivel, 'n_treino': len(t),
            'm1_ordem_ambigua': ambiguo, 'ic95_predito_m2': ic}, None


def _hora_real(o):
    """Hora local REAL ('YYYY-MM-DDTHH:MM:SS') de uma observacao, ou None.

    So conta um timestamp ISO com hora cujo dia coincide com o da observacao: sem hora, com
    texto invalido ou de outro dia, a ordem dentro do dia nao se conhece.
    """
    v = o.get('hora_local')
    if not isinstance(v, str) or len(v) < 16 or v[10] not in 'T ' or v[:10] != str(o['data'])[:10]:
        return None
    hms = v[11:19] if len(v) >= 19 else v[11:16] + ':00'
    try:
        datetime.strptime(hms, '%H:%M:%S')              # horas impossiveis (25:61) nao provam nada
    except ValueError:
        return None
    return v[:10] + 'T' + hms


def ultimo_valor(t):
    """(valor do baseline M1, ordem_ambigua). `t`: treino ordenado (data < alvo).

    M1 = ultimo valor observado. Se o ULTIMO DIA do treino tiver varias observacoes, so a hora
    local real as ordena; sem ela (ou com horas iguais) a ordem e desconhecida e o activity_id
    NAO a decide (seria simular uma sequencia que os dados nao provam): M1 passa a ser a media
    das observacoes desse dia. Com ordem provada, e o valor da mais tardia.
    """
    ult = [o for o in t if o['data'] == t[-1]['data']]
    if len(ult) == 1:
        return t[-1]['watts'], False
    horas = [_hora_real(o) for o in ult]
    if all(horas) and len(set(horas)) == len(horas):
        return max(zip(horas, (o['watts'] for o in ult)))[1], False
    return sum(o['watts'] for o in ult) / len(ult), True


def _sse_walk_forward(t, q):
    """Soma dos erros^2 de prever cada observacao de `t` (ordenada) com as de data
    estritamente anterior, num so varrimento (O(n)). Equivale a estimar_serie por origem."""
    st = E.estado_inicial()
    sse, cnt, i = 0.0, 0, 0
    while i < len(t):
        j = i
        while j < len(t) and t[j]['data'] == t[i]['data']:
            j += 1
        if st['n'] > 0:
            for o in t[i:j]:
                sse += (st['mu'] - o['watts']) ** 2
                cnt += 1
        for o in t[i:j]:
            st, _ = E.passo(st, o, q)
        i = j
    return sse, cnt


def escolher_q(treino, alvo_data, grelha=GRELHA_Q, q_padrao=Q_PADRAO):
    """Selecao ANINHADA: usa so erros walk-forward dentro do treino (data < alvo_data).

    (q, True) ou (q_padrao, False). Empates (tolerancia relativa 1e-9): o primeiro da grelha.
    """
    t = sorted([o for o in treino if o['data'] < alvo_data], key=E.chave_ordem)
    if len(t) < MIN_TREINO_Q:
        return q_padrao, False
    melhor, melhor_sse = None, None
    for q in grelha:
        sse, cnt = _sse_walk_forward(t, q)
        if cnt < MIN_ERROS_Q:
            return q_padrao, False
        if melhor_sse is None or sse < melhor_sse * (1 - 1e-9):
            melhor, melhor_sse = q, sse
    return melhor, True


# ── suficiencia e veredicto ─────────────────────────────────────────────────

def suficiencia(alvos):
    datas = sorted({a['data'] for a in alvos})
    dias = ((date.fromisoformat(datas[-1]) - date.fromisoformat(datas[0])).days
            if datas else 0)
    crit = {'min_alvos': MIN_ALVOS_AVALIAVEL, 'min_datas_distintas': MIN_DATAS_AVALIAVEL,
            'min_dias_cobertura': MIN_DIAS_AVALIAVEL}
    obs = {'n_alvos': len(alvos), 'n_datas_distintas': len(datas), 'dias_cobertura': dias,
           'primeira': datas[0] if datas else None, 'ultima': datas[-1] if datas else None}
    if not alvos:
        est = EST_SEM_ALVOS
    elif (len(alvos) < MIN_ALVOS_AVALIAVEL or len(datas) < MIN_DATAS_AVALIAVEL
          or dias < MIN_DIAS_AVALIAVEL):
        est = EST_DESCRITIVO
    else:
        est = EST_AVALIAVEL
    return est, {**obs, 'criterios': crit}


def veredicto(dif):
    """Veredicto so para amostra avaliavel; dif = {'M0': diferenca, 'M1': diferenca}."""
    pior = melhor = 0
    for d in dif.values():
        if d is None or d.get('ic95_d_mae') is None or d['d_mae_relativo'] is None:
            return 'diferenca_nao_demonstrada'
        lo, hi = d['ic95_d_mae']
        if hi < 0 and d['d_mae_relativo'] <= -MARGEM_RELATIVA and d['d_rmse'] < 0:
            melhor += 1
        if lo > 0 and d['d_mae_relativo'] >= MARGEM_RELATIVA and d['d_rmse'] > 0:
            pior += 1
    if pior:
        return 'm2_pior_que_algum_baseline'
    if melhor == len(dif):
        return 'm2_melhor_que_ambos_baselines'
    return 'diferenca_nao_demonstrada'


# ── orientacao de dados por unidade ────────────────────────────────────────

SIT_SEM_ALVOS = 'sem_alvos'
SIT_INSUFICIENTE = 'amostra_insuficiente'
SIT_SEM_DIFERENCA = 'avaliavel_diferenca_nao_demonstrada'
SIT_M2_MELHOR = 'avaliavel_m2_melhor'
SIT_M2_PIOR = 'avaliavel_m2_pior'

_RESUMO_SITUACAO = {
    SIT_SEM_ALVOS: 'Sem alvos avaliaveis: nada a concluir sobre o estimador nesta unidade (provisorio).',
    SIT_INSUFICIENTE: ('Amostra insuficiente: so metricas descritivas. A conclusao e provisoria; '
                       'nao valida nem rejeita o estimador.'),
    SIT_SEM_DIFERENCA: ('Amostra suficiente para avaliar, mas sem diferenca demonstrada face aos '
                        'baselines. Nao prova equivalencia: continua a acumular observacoes.'),
    SIT_M2_MELHOR: ('Nesta modalidade e duracao o estimador foi melhor que M0 e M1 (margem e incerteza '
                    'cumpridas). Nao se generaliza as outras unidades.'),
    SIT_M2_PIOR: ('Nesta modalidade e duracao o estimador foi pior que pelo menos um baseline, com a '
                  'incerteza a suportar. Nao se generaliza as outras unidades.'),
}


def situacao_da_unidade(item):
    """Uma de: sem_alvos | amostra_insuficiente | avaliavel_* (so le campos do proprio item)."""
    est = item.get('estado_suficiencia')
    if est == EST_SEM_ALVOS:
        return SIT_SEM_ALVOS
    if est != EST_AVALIAVEL:                 # descritivo, ou item incompleto: nunca se presume amostra suficiente
        return SIT_INSUFICIENTE
    v = item.get('veredicto')
    if v == 'm2_melhor_que_ambos_baselines':
        return SIT_M2_MELHOR
    if v == 'm2_pior_que_algum_baseline':
        return SIT_M2_PIOR
    return SIT_SEM_DIFERENCA


def orientacao_dados(item, testes=None, limiar=None):
    """Que dados faltam a uma unidade (modalidade x duracao) e que esforco ajudaria.

    Derivada so dos campos do item de `avaliar` e dos criterios de suficiencia JA existentes
    (nao cria criterios nem altera nenhum). `testes`: {duracao_s: {'nome','mede'}} (protocolo
    existente); `limiar`: fracao da referencia que define um esforco maximo (so para o texto).
    Sem amostra suficiente descreve o que falta e sugere UM esforco util; com amostra suficiente
    nao sugere mais testes so para aumentar o numero. Um esforco e uma OPORTUNIDADE de recolha
    de dados, nunca uma condicao para a aplicacao funcionar.
    """
    sit = situacao_da_unidade(item)
    crit = item.get('criterios') or {}
    n, d, dias = item.get('n_alvos') or 0, item.get('n_datas_distintas') or 0, item.get('dias_cobertura') or 0
    out = {'situacao': sit,
           'conclusao': 'provisoria' if sit in (SIT_SEM_ALVOS, SIT_INSUFICIENTE) else 'avaliada_exploratoria',
           'resumo': _RESUMO_SITUACAO[sit], 'faltam': None, 'observacoes_a_acrescentar': 0,
           'teste_util': None, 'motivo_sem_sugestao': None,
           'excluidas_por_elegibilidade': sum(
               v for k, v in (item.get('motivos_exclusao_observacoes') or {}).items()
               if str(k).startswith(('elegibilidade_desconhecida', 'inelegivel'))),
           'como_contribui': ('Cada esforco novo, detetado e elegivel, passa a ser um alvo futuro e a '
                              'entrar no treino das previsoes seguintes; nunca altera previsoes passadas.')}
    if sit not in (SIT_SEM_ALVOS, SIT_INSUFICIENTE):
        out['motivo_sem_sugestao'] = 'amostra_suficiente_para_avaliar'
        return out
    f = {'alvos': max(0, crit.get('min_alvos', MIN_ALVOS_AVALIAVEL) - n),
         'datas_distintas': max(0, crit.get('min_datas_distintas', MIN_DATAS_AVALIAVEL) - d),
         'dias_cobertura': max(0, crit.get('min_dias_cobertura', MIN_DIAS_AVALIAVEL) - dias)}
    out['faltam'] = f
    # cada esforco novo, numa data nova, acrescenta 1 alvo e 1 data
    out['observacoes_a_acrescentar'] = max(f['alvos'], f['datas_distintas'])
    info = (testes or {}).get(item.get('duracao_s'))
    if not info:
        out['motivo_sem_sugestao'] = 'duracao_sem_protocolo_definido'
        return out
    pct = f' (>= {round(limiar * 100)}% de uma referencia anterior)' if limiar else ''
    out['teste_util'] = {
        'modalidade': item.get('modalidade'), 'duracao_s': item.get('duracao_s'),
        'nome': info.get('nome'), 'mede': info.get('mede'),
        'condicao': ('Conta se for um esforco maximo nesta duracao' + pct + ', numa atividade com '
                     'icu_ignore_power=false explicito e sem ignore_parts. Nao e um teste padronizado '
                     'nem obrigatorio; uma prova, um bloco duro ou uma saida forte servem.')}
    return out


# ── orquestracao ────────────────────────────────────────────────────────────

def _dia(v):
    if v is None:
        return None
    s = v.isoformat() if hasattr(v, 'isoformat') else str(v)
    return s[:10] if len(s) >= 10 else None


def _semente(modo, mod, dur, metodo):
    return f'{SEMENTE}:{modo}:{mod}:{dur}:{metodo}'


def _avaliar_chave(modo, mod, dur, alvos, treino_de, n_conf, motivos_sel, q, grelha):
    """alvos: observacoes usaveis (estado atual). treino_de(alvo) -> observacoes conhecidas."""
    excl, prev = {}, []
    perdido_epoca = 0
    m1_ambiguo = 0
    for a in alvos:
        treino = treino_de(a)
        base_retro = [o for o in alvos if o['data'] < a['data']]
        perdido_epoca += max(0, len(base_retro) - len([o for o in treino if o['data'] < a['data']]))
        p, motivo = previsoes_metodos(treino, a['data'], q)
        if p is None:
            excl[motivo] = excl.get(motivo, 0) + 1
            continue
        m1_ambiguo += 1 if p['m1_ordem_ambigua'] else 0
        prev.append({'test_id': a['test_id'], 'data': a['data'], 'y': a['watts'],
                     'treino': [o for o in treino if o['data'] < a['data']], **p})
    er = {m: [r[m] - r['y'] for r in prev] for m in ('M0', 'M1', 'M2')}
    est, cob = suficiencia([{'data': r['data']} for r in prev])
    com_ic = est == EST_AVALIAVEL
    dif = {b: diferenca(er['M2'], er[b], _semente(modo, mod, dur, b), com_ic) for b in ('M0', 'M1')}

    # calibracao exploratoria do intervalo preditivo derivado do modelo
    cob_ic = [r for r in prev if r['ic95_predito_m2'] is not None]
    cobertos = sum(1 for r in cob_ic if r['ic95_predito_m2'][0] <= r['y'] <= r['ic95_predito_m2'][1])
    calib = {'tipo': 'intervalo_preditivo_derivado_do_modelo_nao_validado',
             'nivel_nominal': NIVEL_NOMINAL, 'n_intervalos': len(cob_ic),
             'n_sem_intervalo': len(prev) - len(cob_ic),
             'cobertura_observada': (cobertos / len(cob_ic)
                                     if len(cob_ic) >= MIN_INTERVALOS_CALIBRACAO else None),
             'motivo_sem_cobertura': (None if len(cob_ic) >= MIN_INTERVALOS_CALIBRACAO
                                      else 'amostra_insuficiente')}

    # sensibilidade a q (exploratoria, sem selecao) nos MESMOS alvos
    sens = []
    for qq in sorted(set(grelha) | {q}):
        e = []
        for r in prev:
            pq = E.estimar_serie(r['treino'], r['data'], qq)['resumo']['nivel_w']
            e.append(pq - r['y'])
        sens.append({'q': qq, 'predefinido': qq == q, **metricas(e)})

    # selecao aninhada (exploratoria): q escolhido so com o treino de cada origem
    ee_sel, ee_pad, escolhas = [], [], {}
    for r in prev:
        qs, ok = escolher_q(r['treino'], r['data'], grelha, q)
        if not ok:
            continue
        escolhas[str(qs)] = escolhas.get(str(qs), 0) + 1
        ee_sel.append(E.estimar_serie(r['treino'], r['data'], qs)['resumo']['nivel_w'] - r['y'])
        ee_pad.append(r['M2'] - r['y'])
    aninhada = {'exploratoria': True, 'n_origens_com_selecao': len(ee_sel),
                'n_origens_sem_selecao': len(prev) - len(ee_sel),
                'min_treino': MIN_TREINO_Q, 'q_escolhido_contagem': dict(sorted(escolhas.items())),
                'm2_q_aninhado': metricas(ee_sel), 'm2_q_predefinido_mesmo_subconjunto': metricas(ee_pad)}

    return {
        'modalidade': mod, 'duracao_s': dur,
        'estado_suficiencia': est, **cob,
        'n_confirmados': n_conf, 'n_observacoes_usaveis': len(alvos),
        'motivos_exclusao_observacoes': motivos_sel,
        'origens_excluidas': dict(sorted(excl.items())),
        'n_origens_excluidas': sum(excl.values()),
        'n_origens_m1_ordem_ambigua': m1_ambiguo,
        'treino_perdido_por_modo': perdido_epoca if modo == 'conhecido_na_epoca' else 0,
        'metodos': {m: metricas(er[m]) for m in ('M0', 'M1', 'M2')},
        'diferencas_m2_menos_baseline': dif,
        'veredicto': veredicto(dif) if est == EST_AVALIAVEL else None,
        'calibracao_intervalo_preditivo': calib,
        'sensibilidade_q': sens,
        'selecao_q_aninhada': aninhada,
        'previsoes': [{k: r[k] for k in ('test_id', 'data', 'y', 'n_treino', 'M0', 'M1', 'M2',
                                         'm1_ordem_ambigua', 'ic95_predito_m2')} for r in prev],
    }


def avaliar(historico, atividades, curvas, modo='retrospectivo', q=Q_PADRAO, grelha_q=GRELHA_Q,
            tipo=None, duracao=None, auto=None):
    """Validacao walk-forward de um modo. Levanta HistoricoAmbiguo / ValueError.

    auto: None -> observacoes = revisoes confirmadas (comportamento anterior). Com `auto`
    (modelo_observacoes_auto.FonteAuto) as observacoes sao as detetadas automaticamente e
    elegiveis, sem confirmacao manual; as revisoes manuais so excluem (sobreposicao opcional).
    No modo conhecido_na_epoca uma observacao so conta desde a sua primeira prova
    verificavel (`detetado_em`, ou o `revisado_em` de uma revisao manual do mesmo valor);
    nao ha preenchimento retroativo, por isso o treino pode ser vazio para datas antigas.
    """
    if modo not in MODOS:
        raise ValueError(f'modo invalido: {modo}')
    atuais = E.estado_atual_por_teste(historico)
    cpa = E.curvas_por_atividade(curvas)
    if auto is None:
        atual = E.selecionar_observacoes(atuais, atividades, cpa)
    else:
        atual = E.selecionar_observacoes_auto(auto.atuais(), atuais, atividades, cpa)
    cache = {}

    def na_epoca(corte):
        if corte not in cache:
            parcial = [r for r in (historico or []) if (_dia(r.get('revisado_em')) or '9999') < corte]
            ep = E.estado_atual_por_teste(parcial)
            if auto is None:
                cache[corte] = E.selecionar_observacoes(ep, atividades, cpa)
            else:
                cache[corte] = E.selecionar_observacoes_auto(auto.conhecidas_antes(corte), ep,
                                                             atividades, cpa)
        return cache[corte]

    itens = []
    for (m, d) in sorted(atual):
        if (tipo and m != tipo) or (duracao is not None and d != duracao):
            continue
        s = atual[(m, d)]
        alvos = s['usadas']
        if modo == 'retrospectivo':
            treino_de = lambda a, alvos=alvos: [o for o in alvos if o['data'] < a['data']]
        else:
            treino_de = lambda a, m=m, d=d: na_epoca(a['data'])[(m, d)]['usadas']
        motivos = {}
        for e in s['excluidas']:
            motivos[e['motivo']] = motivos.get(e['motivo'], 0) + 1
        item = _avaliar_chave(modo, m, d, alvos, treino_de, s['n_confirmados'],
                              dict(sorted(motivos.items())), q, grelha_q)
        item['n_detetadas'] = s.get('n_detetadas')
        if auto is not None and modo == 'conhecido_na_epoca':
            item['historico_temporal'] = auto.resumo_temporal([o['test_id'] for o in alvos])
        itens.append(item)
    res = {'modo': modo, 'itens': itens,
           'fonte_observacoes': 'automatica' if auto is not None else 'confirmacao_manual'}
    if auto is not None:
        res['descricao_modo'] = DESCRICAO_MODO[modo]
    return res


def validar(historico, atividades, curvas, modos=MODOS, q=Q_PADRAO, grelha_q=GRELHA_Q,
            tipo=None, duracao=None, auto=None):
    """Resultado completo (um ou mais modos) com configuracao e assinatura."""
    res = {m: avaliar(historico, atividades, curvas, m, q, grelha_q, tipo, duracao, auto)
           for m in modos}
    cfg = {'versao_validacao': VERSAO_VALIDACAO, 'versao_estimador': E.VERSAO_ESTIMADOR,
           'q_predefinido': q, 'grelha_q': list(grelha_q), 'n_bootstrap': N_BOOT,
           'semente_base': SEMENTE, 'margem_relativa': MARGEM_RELATIVA,
           'criterios_suficiencia': {'min_alvos': MIN_ALVOS_AVALIAVEL,
                                     'min_datas_distintas': MIN_DATAS_AVALIAVEL,
                                     'min_dias_cobertura': MIN_DIAS_AVALIAVEL}}
    assin = hashlib.sha256(json.dumps({'cfg': cfg, 'res': res}, sort_keys=True,
                                      default=str).encode()).hexdigest()
    return {'experimental': True, 'tipo_analise': 'validacao_temporal_exploratoria',
            'promocao': False, 'alvo': 'melhor valor da duracao em toda a atividade '
            '(power_curves), nao teste padronizado', 'configuracao': cfg,
            'assinatura': assin, 'modos': res}
