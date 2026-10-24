"""Modo IR EXPLORATORIO (historico, nao validado). Nucleo PURO: sem base de dados, sem rede, sem escrita.

Recupera a leitura historica do IR (commit 83cc6cc): por modalidade e DURACAO (60, 300 e 1200 s, as que o
codigo historico usava em `mt.DURACOES`), um ajuste com P0 LIVRE estimado por unidade, sobre as observacoes
do detetor historico (`observacoes_auto_historico`, criterio de proximidade de 0,92 sem exigir YES).

Regras deste modo (separadas do IR calibrado de 180 s com P0 fixo):
  * nao usa o P0 do IR calibrado nem o YES mais recente: o P0 e estimado por unidade;
  * nao e persistido: nada grava em `modelo_ir_ajustes`, `modelo_ir_p0_referencias` nem noutra tabela;
  * nao substitui nem le ajustes guardados; nao partilha parametros, observacoes nem estados com o IR calibrado;
  * o resultado e rotulado "Exploratorio - nao validado" e nunca apresentado como recalibracao.

O Solver e o mesmo de `modelo_ir.ajustar_observacoes` (sem duplicacao); a curva e `modelo_ir.curvas_influencia`.
Uma unidade sem observacoes, ou com dados insuficientes (gate de 15 observacoes, 12 datas, 180 dias), NAO tem curva:
o motivo fica registado e nenhuma curva artificial e desenhada.
"""
import math
from datetime import date, timedelta

import modelo_ir as ir
from modelo_testes import MODALIDADES

MODO = 'exploratorio_nao_validado'
ROTULO = 'Exploratorio — nao validado'
DURACOES_HISTORICAS = (60, 300, 1200)      # mt.DURACOES do codigo historico (83cc6cc); NAO converter para 180 s
AVISO = ('Exploratorio — nao validado: P0 livre estimado por unidade, observacoes do detetor historico (sem '
         'exigir YES). Ajuste dentro da amostra. Nao e calibracao valida, nao e guardado e nao alimenta o IR calibrado.')
ESTADOS_COM_CURVA = (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL)


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _observacoes(usadas):
    """Observacoes no formato do Solver: {data, watts, test_id, versao}. Ignora entradas sem data/potencia."""
    out = []
    for o in usadas or []:
        w = _num(o.get('watts'))
        d = str(o.get('data') or '')[:10]
        if w is None or len(d) != 10:
            continue
        out.append({'test_id': o.get('test_id'), 'versao': 1, 'data': d, 'watts': w})
    return out


def _parametros(r):
    p = r.get('parametros') or {}
    if not p:
        return None
    return {k: _num(p.get(k)) for k in ('p0', 'k1', 'k2', 't1', 't2')}


def _convergencia(r):
    """Bloco de convergencia TAL COMO o solver o devolve no nivel superior (modelo_ir.ajustar_observacoes:
    'convergencia' = {convergiu, status, mensagem, nfev, origem_melhor_partida}). None se o solver nao correu
    (dados insuficientes) -- nunca se inventa convergencia."""
    c = r.get('convergencia')
    if not isinstance(c, dict):
        return None
    return {'convergiu': c.get('convergiu'), 'status': c.get('status'), 'mensagem': c.get('mensagem'),
            'nfev': c.get('nfev'), 'origem_melhor_partida': c.get('origem_melhor_partida')}


def _curva(carga_dia, r, params, referencia, cfg):
    """Serie diaria em W desde a origem do ajuste ate `referencia`: positiva (K1 E), negativa (K2 G) e liquida."""
    d0 = r.get('serie_ini')
    if not d0 or not params or any(v is None for v in params.values()):
        return None
    cargas = ir.cargas_diarias(carga_dia, d0, referencia)
    comp = ir.curvas_influencia(cargas, params, lag_dias=cfg['lag_dias'])
    n = len(cargas)
    a = date.fromisoformat(d0)
    datas = [(a + timedelta(days=i)).isoformat() for i in range(n)]
    return {'datas': datas,
            'positiva': [round(float(v), 4) for v in comp['positiva']],
            'negativa': [round(float(v), 4) for v in comp['negativa']],
            'liquida': [round(float(v), 4) for v in comp['liquida']]}


def _dias(a, b):
    try:
        return (date.fromisoformat(a) - date.fromisoformat(b)).days
    except (TypeError, ValueError):
        return None


def _cobertura_historico(base, serie_ini, carga_dia, referencia, anos_historico):
    """Cobertura da SERIE DE CARGA, separada da cobertura das observacoes (gate de 180 dias, em `base`).

    * janela_solver: do inicio da serie (serie_ini = min(1.a observacao, 1.a carga)) ate a ultima observacao
      (a janela que o solver ajusta: cargas_diarias(d0, d1));
    * janela_curva: do mesmo inicio ate a data de referencia (so para desenhar a curva);
    * dias_historico_antes_primeiro_esforco: quantos dias de carga registada antecedem o 1.o esforco;
    * carga_cobre_primeiro_esforco: a carga registada comeca no ou antes do 1.o esforco;
    * truncamento_sincronizacao: a 1.a carga cai na fronteira da janela de sincronizacao (ANOS_HISTORICO),
      o que sugere historico cortado (dias anteriores ficam como repouso). 'nao_avaliado' sem anos_historico;
    * lacunas: NAO determinavel com estes dados (dia sem atividade = repouso indistinguivel de falta de dado).
    """
    datas = sorted(carga_dia or {})
    ini_c = datas[0] if datas else None
    fim_c = datas[-1] if datas else None
    obs_ini, obs_fim = base.get('data_ini'), base.get('data_fim')
    if anos_historico is None:
        trunc = 'nao_avaliado'
    elif ini_c is None or not referencia:
        trunc = 'nao_avaliado'
    else:
        fronteira = (date.fromisoformat(referencia) - timedelta(days=round(365.25 * float(anos_historico)))).isoformat()
        trunc = bool(abs(_dias(ini_c, fronteira) or 0) <= 7)
    return {'carga_data_ini': ini_c, 'carga_data_fim': fim_c,
            'dias_com_carga_registada': len(datas),
            'esforco_data_ini': obs_ini, 'esforco_data_fim': obs_fim,
            'serie_ini': serie_ini,
            'dias_historico_antes_primeiro_esforco': _dias(obs_ini, serie_ini) if obs_ini and serie_ini else None,
            'carga_cobre_primeiro_esforco': bool(ini_c and obs_ini and ini_c <= obs_ini),
            'janela_solver': {'ini': serie_ini, 'fim': obs_fim},
            'janela_curva': {'ini': serie_ini, 'fim': referencia},
            'truncamento_sincronizacao': trunc,
            'lacunas': 'nao_determinavel: dia sem atividade e indistinguivel de repouso'}


def _observacoes_detalhe(usadas):
    """Proveniencia de cada observacao que ENTRA no solver: o id do esforco, o tipo e o valor da referencia."""
    out = []
    for o in usadas or []:
        if _num(o.get('watts')) is None or len(str(o.get('data') or '')[:10]) != 10:
            continue        # mesmo filtro de _observacoes: so o que entra no solver
        out.append({'test_id': o.get('test_id'), 'atividade_esforco_id': o.get('atividade_esforco_id', o.get('activity_id')),
                    'data': str(o.get('data') or '')[:10], 'watts': _num(o.get('watts')),
                    'referencia_tipo': o.get('referencia_tipo'),
                    'referencia_activity_id': o.get('referencia_activity_id'),
                    'referencia_data': o.get('referencia_data'), 'referencia_watts': _num(o.get('referencia_watts')),
                    'incluida_no_solver': True})
    return out


def ajustar_unidade(modalidade, duracao_s, usadas, carga_dia, referencia, cfg, anos_historico=None):
    """Um ajuste exploratorio (P0 livre). Nunca grava; devolve um dicionario descritivo da unidade."""
    obs = _observacoes(usadas)
    base = {'modalidade': modalidade, 'duracao_s': duracao_s, 'modo': MODO, 'rotulo': ROTULO,
            'persistido': False, 'n_obs': len(obs), 'n_datas_distintas': len({o['data'] for o in obs}),
            'data_ini': min((o['data'] for o in obs), default=None),
            'data_fim': max((o['data'] for o in obs), default=None),
            'p0_origem': 'livre_estimado'}
    if not obs:
        return {**base, 'estado': 'sem_observacoes', 'motivo': 'nenhuma observacao usada pelo detetor historico',
                'parametros': None, 'metricas': None, 'identificabilidade': None, 'avisos': [], 'curva': None,
                'convergencia': None, 'observacoes': [],
                'cobertura_historico': _cobertura_historico(base, None, carga_dia, referencia, anos_historico),
                'requisitos': ir.requisitos({'n': 0}, ir._cfg(cfg))}
    r = ir.ajustar_observacoes(obs, carga_dia, referencia, cfg, p0_fixo=None)
    estado = r.get('estado')
    metricas = r.get('metricas') or {}
    ident = r.get('identificabilidade') or {}
    avisos = list(ident.get('avisos') or [])
    params = _parametros(r)
    curva = _curva(carga_dia, r, params, referencia, cfg) if estado in ESTADOS_COM_CURVA else None
    conv = _convergencia(r)
    return {**base, 'estado': estado,
            'motivo': r.get('motivo') or metricas.get('motivo_insuficiente') or (None if curva is not None else estado),
            'parametros': params,
            # 'convergencia' no nivel superior = o bloco do solver; metricas.convergencia (bool) mantem a forma antiga
            'convergencia': conv,
            'metricas': {k: _num(metricas.get(k)) for k in ('r2', 'rmse', 'sse', 'mae')} | {
                'convergencia': conv.get('convergiu') if conv else None},
            'identificabilidade': {'parametros_no_limite': ident.get('parametros_no_limite') or [],
                                   'erros_padrao': ident.get('erros_padrao'),
                                   'condicionamento': _num(ident.get('condicionamento'))},
            'avisos': avisos, 'curva': curva,
            'requisitos': r.get('requisitos'),
            'p0_inicial': (r.get('p0_inicial') or {}).get('origem'), 'serie_ini': r.get('serie_ini'),
            'observacoes': _observacoes_detalhe(usadas),
            'cobertura_historico': _cobertura_historico(base, r.get('serie_ini'), carga_dia, referencia,
                                                        anos_historico)}


def analisar(selecao, cargas, referencia, cfg, duracoes=DURACOES_HISTORICAS, anos_historico=None):
    """{(modalidade, duracao): unidade} para as duracoes historicas. `selecao` = selecionar() do servico
    ({(mod, dur): {'usadas': [...]}}); `cargas` = {modalidade: {data: carga}}; `referencia` = data ISO de hoje."""
    itens = []
    for dur in duracoes:
        for mod in MODALIDADES:
            usadas = ((selecao or {}).get((mod, dur)) or {}).get('usadas') or []
            itens.append(ajustar_unidade(mod, dur, usadas, (cargas or {}).get(mod, {}), referencia, cfg,
                                         anos_historico=anos_historico))
    return itens
