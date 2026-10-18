"""Servico do modelo IR: liga modelo_ir (puro) as observacoes MMP, as atividades e a base.

  * recalibrar(): para cada modalidade x duracao, ajusta P0, K1, K2, T1, T2 aos MMP elegiveis
    (os mesmos que o estimador e a validacao usam) e a carga da MESMA modalidade
    (icu_training_load). So grava quando as ENTRADAS mudaram (assinatura) e nunca substitui o
    historico: cada ajuste e uma linha nova de modelo_ir_ajustes, com o motivo.
  * vista(): ultimo ajuste por unidade (e, se pedido, o historico). So leitura.
  * validar_preditivo(): walk-forward (treino estritamente anterior) contra M0/M1/M2, separado
    do erro de ajuste historico. So leitura; nada e gravado.

Nao chama a Intervals.icu. Nao toca em PMC, CTL/ATL/TSB, homeostatico, estimador nem validacao.
"""
from datetime import date, timedelta

import numpy as np

import config
import db
import modelo_estimador as E
import modelo_ir as ir
import modelo_ir_global as G
import modelo_observacoes_auto as moa
import modelo_testes as mt
import modelo_validacao as V

VERSAO_SERVICO = 1

MOTIVO_PRIMEIRO = 'primeiro_ajuste'
MOTIVO_NOVAS = 'novas_observacoes'
MOTIVO_ALTERADAS = 'observacoes_removidas_ou_alteradas'
MOTIVO_CARGA = 'carga_alterada'
MOTIVO_P0 = 'p0_inicial_alterado'
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


def _cfg_efetiva(config_ir=None):
    c = ir._cfg(config_ir)
    return {'ir': c, 'versao_modelo': ir.VERSAO_IR, 'versao_servico': VERSAO_SERVICO,
            'carga': 'icu_training_load das atividades da mesma modalidade, somada por dia',
            'alvo': 'melhor valor da duracao em toda a atividade (nao teste padronizado)'}


def _obs_min(usadas):
    return [{'test_id': o['test_id'], 'versao': o.get('revisao'), 'data': str(o['data'])[:10],
             'watts': float(o['watts'])} for o in sorted(usadas, key=lambda x: (x['data'], x['test_id']))]


def _entradas(obs, carga_dia, referencia, p0_ini):
    """Resumo JSON das entradas de UM ajuste (serve a assinatura e a auditoria)."""
    ate = max((o['data'] for o in obs), default=None)
    cargas = sorted((d, round(v, 3)) for d, v in carga_dia.items() if ate and d <= ate and v > 0)
    return {'observacoes': obs, 'carga_ate': ate,
            'carga_dias_com_treino': len(cargas),
            'carga_total': round(sum(v for _, v in cargas), 3),
            'carga_digest': ir.assinatura(cargas),
            'p0_inicial': None if p0_ini is None else round(p0_ini, 3),
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
    return MOTIVO_P0


# ── recalibracao ──────────────────────────────────────────────────────────

def recalibrar(origem='sync', agora=None, forcar=False, config_ir=None, tipo=None, duracao=None,
               dados=None):
    """Ajusta cada unidade cujas entradas mudaram. Idempotente: sem mudancas nao grava nada.

    Devolve {'ok', 'unidades': {'Bike:300': {...}}, 'n_ajustadas', 'n_inalteradas', ...}. Nunca
    levanta por dados maus: falhar o IR nao pode estragar a sincronizacao.
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
    try:
        sel = selecionar(historico_obs, revisoes, atividades, curvas)
    except E.HistoricoAmbiguo as e:
        return {'ok': False, 'erro': 'historico_de_testes_ambiguo', 'detalhe': str(e)}
    cargas, info_carga = carga_por_modalidade(atividades)
    cfg = _cfg_efetiva(config_ir)
    cfg_assin = ir.assinatura(cfg)
    unidades, n_aj, n_in = {}, 0, 0
    for (mod, dur) in sorted(sel):
        if (tipo and mod != tipo) or (duracao is not None and dur != duracao):
            continue
        chave = f'{mod}:{dur}'
        obs = _obs_min(sel[(mod, dur)]['usadas'])
        prev = db.ultimo_ajuste_ir(mod, dur)
        if not obs and prev is None:
            unidades[chave] = {'acao': 'sem_observacoes'}
            continue
        p0v, p0o, p0d = ir.p0_inicial(obs, referencia)
        ent = _entradas(obs, cargas[mod], referencia, p0v)
        sig = ir.assinatura({'cfg': cfg_assin, 'obs': [(o['test_id'], o['versao'], o['data'], o['watts'])
                                                       for o in obs],
                             'carga': ent['carga_digest'], 'p0_inicial': ent['p0_inicial']})
        if prev is not None and prev['assinatura'] == sig and not forcar:
            unidades[chave] = {'acao': 'inalterado', 'versao_ajuste': prev['versao_ajuste'],
                               'estado': prev['estado']}
            n_in += 1
            continue
        try:
            r = ir.ajustar_observacoes(obs, cargas[mod], referencia, cfg['ir'])
        except Exception as e:       # um erro numerico numa unidade nao pode parar as outras
            import traceback
            traceback.print_exc()
            unidades[chave] = {'acao': 'erro', 'erro': f'{type(e).__name__}: {e}'}
            continue
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
            'p0_inicial': p0v, 'p0_origem': p0o or 'mediana_dos_alvos',
            'assinatura': sig, 'versao_modelo': ir.VERSAO_IR, 'configuracao': cfg,
            'metricas': {**(m or {}), 'motivo_insuficiente': r.get('motivo'),
                         'convergencia': r.get('convergencia'), 'limites': r.get('limites'),
                         'obs': o, 'p0_inicial_detalhe': (r.get('p0_inicial') or {}).get('detalhe')},
            'identificabilidade': r.get('identificabilidade'),
            'entradas': ent}
        try:
            db.inserir_ajuste_ir(linha)
        except db.ConflitoAjusteIR:
            unidades[chave] = {'acao': 'conflito_ignorado'}
            continue
        n_aj += 1
        unidades[chave] = {'acao': 'ajustado', 'versao_ajuste': linha['versao_ajuste'],
                           'estado': r['estado'], 'motivo': linha['motivo'], 'n_obs': len(obs)}
    return {'ok': True, 'unidades': unidades, 'n_ajustadas': n_aj, 'n_inalteradas': n_in,
            'carga': info_carga, 'versao_modelo': ir.VERSAO_IR}


# ── leitura ───────────────────────────────────────────────────────────────

def vista(tipo=None, duracao=None, historico=False):
    """Ultimo ajuste por unidade (e todo o historico se pedido). So leitura."""
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
    for r in reversed(db.ajustes_ir_historico(tipo, duracao)):
        if r['estado'] in (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL) and r['versao_ajuste'] != aj['versao_ajuste']:
            anterior = {'versao_ajuste': r['versao_ajuste'], 'estado': r['estado'],
                        'ajustado_em': r['ajustado_em']}
            break
    meta = {'versao_ajuste': aj['versao_ajuste'], 'ajustado_em': aj['ajustado_em'],
            'estado': aj['estado'], 'motivo_ajuste': aj['motivo'], 'leitura': _leitura(aj),
            'n_obs': aj['n_obs'], 'data_ini': aj['data_ini'], 'data_fim': aj['data_fim'],
            'funcao_objetivo': aj['funcao_objetivo'], 'rmse': aj['rmse'], 'r2': aj['r2'],
            'convergiu': aj['convergiu'], 'p0_origem': aj['p0_origem'],
            'avisos_identificabilidade': (aj.get('identificabilidade') or {}).get('avisos') or [],
            'ajuste_valido_anterior': anterior, 'versao_modelo': aj['versao_modelo']}
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

def validar_preditivo(historico_obs, revisoes, atividades, curvas, modos=V.MODOS, tipo=None,
                      duracao=None, auto=None, config_ir=None):
    """Walk-forward do IR contra M0/M1/M2 nos MESMOS alvos, para cada unidade e modo.

    Para cada alvo: treino = so observacoes de data estritamente anterior (no modo
    conhecido_na_epoca, so as ja detetadas antes do alvo); cargas ate ao dia anterior; o IR e
    reajustado SO com isso e preve o alvo. O erro de ajuste historico nunca entra aqui.
    auto: FonteAuto (modelo_observacoes_auto); sem ele usa as observacoes atuais do historico.
    """
    auto = auto or moa.FonteAuto(historico_obs, revisoes)
    atuais = E.estado_atual_por_teste(revisoes or [])
    cpa = E.curvas_por_atividade(curvas or [])
    atual = E.selecionar_observacoes_auto(auto.atuais(), atuais, atividades, cpa)
    cargas, _ = carga_por_modalidade(atividades)
    cfg = ir._cfg(config_ir)
    cache = {}

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
            itens.append(_avaliar_unidade(modo, m, d, alvos, treino_de, cargas[m], cfg))
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


def _avaliar_unidade(modo, mod, dur, alvos, treino_de, carga_dia, cfg):
    prev_ir = ir.avaliar_preditivo(alvos, treino_de, carga_dia, cfg)
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
