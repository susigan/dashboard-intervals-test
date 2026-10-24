"""Curva de desempenho progressiva (Etapa 28). NUCLEO PURO: sem base, sem rede, sem escrita.

Envelope de melhores esforcos (MMP) por modalidade e duracao, numa janela movel (por defeito 42 dias) ou no
historico completo. Cada modalidade usa so as duracoes que tem valores medidos; nada e interpolado, nada e
preenchido, e as duracoes sem valor na janela ficam listadas em `em_falta`.

Lugar na arquitetura (NAO e o IR):
  * le apenas valores medidos (`power_curves`), sem modelo, sem P0, sem ajuste;
  * nao usa a regra YES, o minimo de 15 observacoes nem o 180 s operacional;
  * nao e curva de influencia fisiologica nem IR validado: o rotulo `origem` diz sempre que o valor e medido;
  * nao grava nada: e recalculada a partir de `power_curves`, que a sincronizacao ja atualiza.

Qualidade (nada e apagado nem corrigido em silencio; cada exclusao tem motivo visivel):
  * atividade com `icu_ignore_power=true` -> excluida (motivo `icu_ignore_power=true`);
  * elegibilidade desconhecida (ex.: `ignore_parts` nao associavel ao trecho) -> excluida, com o motivo;
  * monotonia: a potencia media maxima nunca cresce com a duracao dentro da mesma atividade. Um valor de duracao
    mais longa acima do maior valor de duracao mais curta (tolerancia de arredondamento) e SINALIZADO como
    `suspeito`. Por defeito continua no envelope; `excluir_suspeitos=True` tira-o, com motivo `valor_suspeito`.
"""
import bisect
import math
from datetime import date, timedelta

VERSAO = 1
MODALIDADES = ('Bike', 'Row', 'Ski', 'Run')
# PROVISORIO: 42 dias e o valor pedido na Etapa 28. A comparacao com o historico completo esta no relatorio;
# a escolha do predefinido e decisao do utilizador.
JANELA_PADRAO_DIAS = 42
ORIGEM = 'mmp_medido'           # cada ponto e um valor medido numa atividade; nunca estimado nem interpolado
ESTIMATIVAS_AJUSTADAS = 'nao_produzidas'
TOL_W = 0.5                     # tolerancia de arredondamento na regra de monotonia (W)
MOTIVO_IGNORADA = 'icu_ignore_power=true'
MOTIVO_DESCONHECIDA = 'elegibilidade_desconhecida'
MOTIVO_SUSPEITO = 'valor_suspeito'
ELEG_INELEGIVEL = 'inelegivel'        # mesmas constantes de modelo_estimador (ELEG_*); repetidas para o nucleo puro
ELEG_DESCONHECIDA = 'desconhecida'


def _watts(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return float(v)


def _dia(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _mapa_duracoes(c):
    """{duracao_s: watts} de uma curva. Pares (s, w) desalinhados ou ilegiveis nao contam."""
    out = {}
    for s, w in zip(c.get('secs') or [], c.get('watts') or []):
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s):
            continue
        out[int(round(s))] = w
    return out


def pontos(curvas, ate, estados=None, excluir_suspeitos=False):
    """Um ponto por (atividade, duracao) com potencia medida > 0 e data <= ate. Nada e apagado.

    estados: {activity_id: (estado, motivo)} de modelo_estimador.elegibilidade_aprendizagem.
    Devolve (lista_de_pontos, resumo). Cada ponto: modalidade, duracao_s, watts, data, activity_id,
    estado ('usado' | 'excluido'), motivo (None se usado), suspeito (bool), motivo_suspeita.
    """
    ate_d = _dia(ate)
    estados = estados or {}
    out = []
    resumo = {'n_curvas': 0, 'n_fora_da_data': 0, 'n_outra_modalidade': 0, 'n_valores_em_falta': 0,
              'n_atividades_excluidas': 0, 'n_suspeitos': 0, 'n_pontos_excluidos': 0,
              'n_excluidos_monotonia': 0, 'n_excluidos_atividade': 0}
    for c in curvas or []:
        mod = c.get('type')
        aid = c.get('activity_id')
        d = _dia(c.get('date'))
        if mod not in MODALIDADES:
            resumo['n_outra_modalidade'] += 1
            continue
        if d is None or (ate_d is not None and d > ate_d):
            resumo['n_fora_da_data'] += 1
            continue
        resumo['n_curvas'] += 1
        mapa = {}
        for s, w in _mapa_duracoes(c).items():
            v = _watts(w)
            if v is None:
                resumo['n_valores_em_falta'] += 1
                continue
            mapa[s] = v
        motivo_atividade = None
        est = estados.get(aid)
        if est is not None:
            if est[0] == ELEG_INELEGIVEL:
                motivo_atividade = MOTIVO_IGNORADA
            elif est[0] == ELEG_DESCONHECIDA:
                motivo_atividade = f"{MOTIVO_DESCONHECIDA}:{est[1] or 'sem_detalhe'}"
        if motivo_atividade:
            resumo['n_atividades_excluidas'] += 1
        # monotonia (intra-atividade): P(t) suspeito se existir s < t com P(t) > P(s) + TOL_W.
        # Equivale a comparar com o MINIMO das duracoes ja vistas (mais curtas), em ordem crescente.
        minimo_curto = None
        for s in sorted(mapa):
            w = mapa[s]
            suspeito = minimo_curto is not None and w > minimo_curto + TOL_W
            motivo_susp = (f'mais_longa_{s}s_acima_de_mais_curta_{round(minimo_curto, 1)}W' if suspeito else None)
            if suspeito:
                resumo['n_suspeitos'] += 1
            minimo_curto = w if minimo_curto is None else min(minimo_curto, w)
            motivo = motivo_atividade
            if motivo is None and suspeito and excluir_suspeitos:
                motivo = MOTIVO_SUSPEITO
            if motivo is not None:
                resumo['n_pontos_excluidos'] += 1
                resumo['n_excluidos_atividade' if motivo_atividade else 'n_excluidos_monotonia'] += 1
            out.append({'modalidade': mod, 'duracao_s': s, 'watts': w, 'data': d.isoformat(),
                        'activity_id': aid, 'estado': 'excluido' if motivo else 'usado', 'motivo': motivo,
                        'suspeito': bool(suspeito), 'motivo_suspeita': motivo_susp})
    return out, resumo


def envelope(pts_ordenados, datas, ate, janela_dias=JANELA_PADRAO_DIAS):
    """{(modalidade, duracao_s): ponto} com o maior valor 'usado' em (ate - janela, ate].

    pts_ordenados/datas: pontos ordenados por data e a lista das suas datas (ISO), para pesquisa binaria.
    janela_dias None = historico completo (todas as datas <= ate). Empate: fica o mais antigo (como
    calcular_recordes, que so substitui com '>' estrito)."""
    ate_d = _dia(ate)
    if ate_d is None:
        return {}
    fim = bisect.bisect_right(datas, ate_d.isoformat())
    if janela_dias is None:
        ini = 0
    else:
        inicio = (ate_d - timedelta(days=int(janela_dias))).isoformat()
        ini = bisect.bisect_right(datas, inicio)          # estritamente depois de ate - janela
    best = {}
    for p in pts_ordenados[ini:fim]:
        if p['estado'] != 'usado':
            continue
        k = (p['modalidade'], p['duracao_s'])
        m = best.get(k)
        if m is None or p['watts'] > m['watts']:
            best[k] = p
    return best


def _ordenar(pts):
    ordenados = sorted([p for p in pts], key=lambda p: (p['data'], str(p['activity_id'])))
    return ordenados, [p['data'] for p in ordenados]


def curva(curvas, ate, modalidades=None, duracoes=None, janela_dias=JANELA_PADRAO_DIAS,
          estados=None, excluir_suspeitos=False):
    """Envelope em `ate`, por modalidade e duracao. Sem interpolacao; ausencias explicitas."""
    pts, resumo = pontos(curvas, ate, estados, excluir_suspeitos)
    ordenados, datas = _ordenar(pts)
    env = envelope(ordenados, datas, ate, janela_dias)
    mods = [m for m in (modalidades or MODALIDADES) if m in MODALIDADES]
    pedidas = sorted({int(d) for d in duracoes}) if duracoes else None
    rotulo = 'envelope_historico_completo' if janela_dias is None else f'envelope_janela_{int(janela_dias)}d'
    por_modalidade = {}
    for m in mods:
        usados = [p for p in pts if p['modalidade'] == m]
        grade = sorted({p['duracao_s'] for p in usados})   # so as duracoes que ESTA modalidade tem
        alvo = [d for d in grade if pedidas is None or d in pedidas]
        pontos_m = []
        for d in alvo:
            p = env.get((m, d))
            if p is not None:
                pontos_m.append({'duracao_s': d, 'watts': p['watts'], 'data': p['data'],
                                 'activity_id': p['activity_id'], 'origem': ORIGEM, 'serie': rotulo})
        por_modalidade[m] = {
            'estado': 'com_dados' if usados else 'sem_dados',
            'duracoes_disponiveis': grade,
            'pontos': pontos_m,
            'em_falta': [d for d in alvo if (m, d) not in env],
            'n_valores_usados': sum(1 for p in usados if p['estado'] == 'usado'),
            'n_valores_excluidos': sum(1 for p in usados if p['estado'] != 'usado'),
        }
    return {'ate': _dia(ate).isoformat() if _dia(ate) else None, 'janela_dias': janela_dias,
            'serie': rotulo, 'origem': ORIGEM, 'estimativas_ajustadas': ESTIMATIVAS_AJUSTADAS,
            'modalidades': por_modalidade, 'resumo': resumo,
            'exclusoes': _exclusoes(pts), 'suspeitos': [
                {k: p[k] for k in ('modalidade', 'duracao_s', 'watts', 'data', 'activity_id', 'motivo_suspeita')}
                for p in pts if p['suspeito']]}


def _exclusoes(pts):
    """Uma linha por atividade com pelo menos um ponto excluido. Campos existentes inalterados.
    escopo='atividade' (inelegivel/desconhecida: todos os pontos da atividade) ou
    escopo='pontos' (monotonia: apenas os duracoes listadas em duracoes_excluidas; o resto continua)."""
    vistos = {}
    for p in pts:
        if p['estado'] != 'excluido':
            continue
        r = vistos.setdefault(p['activity_id'], {'activity_id': p['activity_id'], 'modalidade': p['modalidade'],
                                                 'data': p['data'], 'motivo': p['motivo'],
                                                 'escopo': None, 'duracoes_excluidas': []})
        r['duracoes_excluidas'].append(p['duracao_s'])
    for r in vistos.values():
        r['escopo'] = 'pontos' if r['motivo'] == MOTIVO_SUSPEITO else 'atividade'
        r['duracoes_excluidas'] = sorted(r['duracoes_excluidas'])
    return sorted(vistos.values(), key=lambda x: (x['modalidade'], x['data']))


def evolucao(curvas, ate, desde, passo_dias=7, modalidades=None, duracoes=None,
             janela_dias=JANELA_PADRAO_DIAS, estados=None, excluir_suspeitos=False):
    """Serie temporal do envelope: em cada data T (passo_dias), o envelope com dados ate T.

    Devolve {'as_of': [...], 'modalidades': {mod: {duracao_s: [watts ou None por T]}}}.
    None = sem valor medido na janela de T (nunca interpolado). Historico = power_curves, sem perda."""
    ate_d, ini_d = _dia(ate), _dia(desde)
    if ate_d is None or ini_d is None or ini_d > ate_d or passo_dias < 1:
        return {'as_of': [], 'modalidades': {}, 'erro': 'intervalo_ou_passo_invalido'}
    pts, _resumo = pontos(curvas, ate, estados, excluir_suspeitos)
    ordenados, datas = _ordenar(pts)
    dts = []
    t = ini_d
    while t <= ate_d:
        dts.append(t)
        t += timedelta(days=passo_dias)
    if dts[-1] != ate_d:
        dts.append(ate_d)
    mods = [m for m in (modalidades or MODALIDADES) if m in MODALIDADES]
    pedidas = sorted({int(d) for d in duracoes}) if duracoes else None
    grade = {m: sorted({p['duracao_s'] for p in pts if p['modalidade'] == m
                        and (pedidas is None or p['duracao_s'] in pedidas)}) for m in mods}
    out = {m: {d: [] for d in grade[m]} for m in mods}
    for T in dts:
        env = envelope(ordenados, datas, T.isoformat(), janela_dias)
        for m in mods:
            for d in grade[m]:
                p = env.get((m, d))
                out[m][d].append(p['watts'] if p else None)
    return {'as_of': [T.isoformat() for T in dts], 'janela_dias': janela_dias,
            'serie': 'envelope_historico_completo' if janela_dias is None else f'envelope_janela_{int(janela_dias)}d',
            'origem': ORIGEM, 'estimativas_ajustadas': ESTIMATIVAS_AJUSTADAS, 'passo_dias': passo_dias,
            'modalidades': {m: {str(d): v for d, v in out[m].items()} for m in mods}}
