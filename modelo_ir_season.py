"""Analise EXPERIMENTAL por SEASON do IR exploratorio. Separada do all-time; nunca o altera nem o mistura.

Regras:
  * fronteiras reais: as seasons vem dos marcos SEASON_START do calendario da Intervals.icu (mesmos marcos que
    config.season_de usa). Sem marcos NAO se usam anos civis: o resultado fica 'fronteiras_nao_confirmadas'
    e nenhum ajuste e feito;
  * candidatas: observacoes do conjunto JA selecionado (mesmas exclusoes de elegibilidade, icu_ignore_power,
    ignore_parts, etc.) cuja referencia SEASON qualifica (>= limiar, calculada so com esforcos da mesma season);
  * uma observacao so entra na season a que pertence pela data; rotulo inconsistente ou fora de qualquer
    fronteira e excluido com motivo explicito;
  * os gates atuais (15 observacoes, 12 datas, 180 dias) sao aplicados pelo mesmo solver; nada e relaxado;
  * a classificacao 'candidata_calibracao' so surge com estado identificavel, convergencia e nenhum parametro no
    limite. Caso contrario fica 'apenas_validacao', 'insuficiente' ou 'nao_identificavel', e os parametros
    nao sao apresentados como validos;
  * a carga usada e a serie completa (a EWM precisa do historico anterior a season), nao a da season.
"""
from datetime import date, timedelta

import modelo_ir as ir
import modelo_ir_exploratorio as mex
from modelo_testes import MODALIDADES

MODO = 'season_experimental_nao_validado'
ROTULO = 'Season — experimental, separada do all-time'
AVISO = ('Analise por season EXPERIMENTAL e nao validada. Separada do resultado all-time, que nao foi alterado. '
         'Nao combina observacoes de season com all-time. Nao e calibracao guardada.')
FONTE_FRONTEIRAS = 'calendario_SEASON_START'
FONTE_AUSENTE = 'sem_marcos_season_start'

CLASS_CANDIDATA = 'candidata_calibracao'
CLASS_VALIDACAO = 'apenas_validacao'
CLASS_INSUFICIENTE = 'insuficiente'
CLASS_NAO_IDENTIFICAVEL = 'nao_identificavel'
CLASS_NAO_CALCULADO = 'nao_calculado'


def fronteiras(marcos, hoje):
    """[{season, inicio, fim, aberta, fonte}] a partir dos marcos SEASON_START [(data_inicio, nome)].

    fim = dia anterior ao marco seguinte; a ultima season fica aberta ate `hoje`. Sem marcos: lista vazia."""
    ms = sorted((str(d)[:10], str(n)) for d, n in (marcos or []) if d)
    out = []
    for i, (d, nome) in enumerate(ms):
        ultima = i + 1 == len(ms)
        fim = hoje if ultima else (date.fromisoformat(ms[i + 1][0]) - timedelta(days=1)).isoformat()
        out.append({'season': nome, 'inicio': d, 'fim': fim, 'aberta': ultima, 'fonte': FONTE_FRONTEIRAS})
    return out


def _fronteira_de(data, fr):
    for f in fr:
        if f['inicio'] <= data <= f['fim']:
            return f
    return None


def candidatas(sel_pre, atuais):
    """{(modalidade, duracao): [obs]} das observacoes JA SELECIONADAS cuja referencia season qualifica.

    `sel_pre` = selecionar() ANTES do filtro all-time (nao e alterado). `atuais` = moa.atuais(historico), para
    ler `referencias` e o rotulo `season` gravado. Cada obs traz a proveniencia completa da referencia season."""
    por_tid = {o.get('test_id'): o for o in atuais or []}
    out = {}
    for k, v in (sel_pre or {}).items():
        lst = []
        for o in v.get('usadas') or []:
            fonte = por_tid.get(o.get('test_id')) or {}
            refs = fonte.get('referencias') or {}
            s = refs.get('season') or {}
            if not s.get('qualifica'):
                continue
            lst.append({'test_id': o.get('test_id'), 'data': str(o.get('data') or '')[:10],
                        'watts': o.get('watts'), 'atividade_esforco_id': o.get('activity_id'),
                        'season': fonte.get('season'),
                        'referencia_tipo': 'season', 'referencia_watts': s.get('referencia_watts'),
                        'referencia_activity_id': s.get('referencia_activity_id'),
                        'referencia_data': s.get('referencia_data'), 'pct_da_referencia': s.get('pct'),
                        'tambem_all_time': bool((refs.get('all_time') or {}).get('qualifica'))})
        out[k] = lst
    return out


def _classificar(r):
    estado = r.get('estado')
    if estado in (ir.EST_INSUFICIENTE, 'sem_observacoes'):
        return CLASS_INSUFICIENTE, r.get('motivo') or estado
    if estado == ir.EST_NAO_IDENTIFICAVEL:
        return CLASS_NAO_IDENTIFICAVEL, 'solver nao identificou os parametros: valores de fronteira, nao validos'
    if estado == ir.EST_IDENTIFICAVEL:
        ident = r.get('identificabilidade') or {}
        conv = r.get('convergencia') or {}
        if ident.get('parametros_no_limite'):
            return CLASS_VALIDACAO, 'parametros no limite: %s' % ','.join(ident['parametros_no_limite'])
        if conv.get('convergiu') is False:
            return CLASS_VALIDACAO, 'solver nao convergiu'
        return CLASS_CANDIDATA, 'identificavel, convergiu e sem parametros no limite'
    return CLASS_VALIDACAO, str(estado)


def _unidade_season(mod, dur, f, obs, carga, hoje, cfg, anos_historico, excl):
    ref = min(f['fim'], hoje)
    usadas = [{'test_id': o['test_id'], 'data': o['data'], 'watts': o['watts'],
               'atividade_esforco_id': o['atividade_esforco_id'], 'referencia_tipo': 'season',
               'referencia_activity_id': o['referencia_activity_id'], 'referencia_data': o['referencia_data'],
               'referencia_watts': o['referencia_watts']} for o in obs]
    u = mex.ajustar_unidade(mod, dur, usadas, carga, ref, cfg, anos_historico=anos_historico)
    classe, motivo = _classificar(u)
    ident = u.get('identificabilidade') or {}
    valido = classe == CLASS_CANDIDATA
    cobertura_dias = None
    if u.get('data_ini') and u.get('data_fim'):
        cobertura_dias = (date.fromisoformat(u['data_fim']) - date.fromisoformat(u['data_ini'])).days
    # o parametro so e apresentado como valor quando a classe e candidata; caso contrario fica registado como nao valido
    return {**u, 'season': f['season'], 'inicio': f['inicio'], 'fim': f['fim'], 'aberta': f['aberta'],
            'fonte_fronteiras': f['fonte'], 'modo': MODO, 'rotulo': ROTULO, 'persistido': False,
            'classificacao': classe, 'motivo_classificacao': motivo, 'valido_para_calibracao': valido,
            'cobertura_dias': cobertura_dias,
            'parametros': u.get('parametros') if classe != CLASS_INSUFICIENTE else None,
            'curva': u.get('curva') if classe in (CLASS_CANDIDATA, CLASS_VALIDACAO) else None,
            'ids_esforcos': [o['atividade_esforco_id'] for o in obs],
            'tipos_referencia': sorted({o['referencia_tipo'] for o in obs}),
            'n_tambem_all_time': sum(1 for o in obs if o['tambem_all_time']),
            'exclusoes': excl, 'identificabilidade': ident}


def analisar(sel_pre, atuais, cargas, hoje, marcos, cfg, duracoes=mex.DURACOES_HISTORICAS, anos_historico=None):
    """Resultado season: {'status', 'modo', 'aviso', 'fonte_fronteiras', 'fronteiras', 'itens'}.

    `cargas` = {modalidade: {data: carga}} (serie completa). Nao altera `sel_pre`, `atuais` nem o all-time."""
    base = {'modo': MODO, 'rotulo': ROTULO, 'aviso': AVISO, 'persistido': False, 'experimental': True,
            'duracoes': list(duracoes)}
    fr = fronteiras(marcos, hoje)
    if not fr:
        itens = []
        cand = candidatas(sel_pre, atuais)
        for dur in duracoes:
            for mod in MODALIDADES:
                n = len(cand.get((mod, dur), []))
                itens.append({'modalidade': mod, 'duracao_s': dur, 'season': None, 'estado': 'nao_calculado',
                              'classificacao': CLASS_NAO_CALCULADO, 'valido_para_calibracao': False,
                              'motivo_classificacao': 'sem marcos SEASON_START: fronteiras reais indisponiveis',
                              'n_candidatas_season': n, 'parametros': None, 'curva': None, 'observacoes': [],
                              'exclusoes': [], 'modo': MODO, 'persistido': False})
        return {**base, 'status': 'fronteiras_nao_confirmadas', 'fonte_fronteiras': FONTE_AUSENTE,
                'fronteiras': [], 'itens': itens}
    cand = candidatas(sel_pre, atuais)
    itens = []
    for dur in duracoes:
        for mod in MODALIDADES:
            grupos = {f['season']: [] for f in fr}
            excl_por = {f['season']: [] for f in fr}
            vistos = set()
            fora = []
            for o in cand.get((mod, dur), []):
                f = _fronteira_de(o['data'], fr)
                if f is None:
                    fora.append({**_exc(o), 'motivo': 'fora_das_fronteiras_season'})
                    continue
                if o['season'] is not None and o['season'] != f['season']:
                    excl_por[f['season']].append({**_exc(o), 'motivo': 'rotulo_season_inconsistente'})
                    continue
                if o['test_id'] in vistos:
                    excl_por[f['season']].append({**_exc(o), 'motivo': 'duplicada'})
                    continue
                vistos.add(o['test_id'])
                grupos[f['season']].append(o)
            for f in fr:
                obs = grupos[f['season']]
                if not obs and not excl_por[f['season']]:
                    continue        # season sem candidatas nesta unidade: nao se lista (nada a avaliar)
                carga = (cargas or {}).get(mod, {})
                it = _unidade_season(mod, dur, f, obs, carga, hoje, cfg, anos_historico, excl_por[f['season']])
                itens.append(it)
            if fora:
                itens.append({'modalidade': mod, 'duracao_s': dur, 'season': None, 'estado': 'nao_calculado',
                              'classificacao': CLASS_NAO_CALCULADO, 'valido_para_calibracao': False,
                              'motivo_classificacao': 'observacoes fora de qualquer fronteira season',
                              'parametros': None, 'curva': None, 'observacoes': [], 'exclusoes': fora,
                              'modo': MODO, 'persistido': False})
    return {**base, 'status': 'OK', 'fonte_fronteiras': FONTE_FRONTEIRAS, 'fronteiras': fr, 'itens': itens}


def _exc(o):
    return {'test_id': o['test_id'], 'atividade_esforco_id': o['atividade_esforco_id'], 'data': o['data'],
            'watts': o['watts'], 'referencia_tipo': 'season', 'referencia_activity_id': o['referencia_activity_id'],
            'referencia_data': o['referencia_data'], 'referencia_watts': o['referencia_watts'],
            'tambem_all_time': o['tambem_all_time']}
