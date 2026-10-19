"""MMP3 (180 s) como referencia operacional do IR: referencias por season, elegibilidade do esforco e estado do teste.

Modulo PURO (sem base, sem rede, sem relogio). O P0 GLOBAL do IR e o MMP3 do esforco `YES` (estado elegivel) MAIS
RECENTE entre as quatro modalidades (`p0_global`): nao e o melhor historico nem o PR, e nao vem do CP Model. Nada
aqui altera o CP Model.

Reutilizado (nenhum segundo calculo de potencia media)
------------------------------------------------------
  * A potencia de 180 s e a que o Intervals.icu devolve na curva de potencia da atividade (`power_curves`, duracao
    exata 180 s: faz parte de db.DURACOES e e a mesma coluna MMP3 que o CP Model le). Aqui nao se recalcula a media.
  * A deteccao e a de `modelo_observacoes_auto.detetar` (referencias CAUSAIS season / all-time / recente, o proprio
    esforco nunca entra na sua referencia, ordem no mesmo dia so pela hora real), chamada com a duracao 180 s.
  * O criterio de proximidade e o EXISTENTE: protocolo.LIMIAR_ESFORCO = 0,92 (configuravel por argumento) e a amostra
    minima existente (>= 5 pontos validos por modalidade). Nao se introduz nenhum limiar novo.
  * A elegibilidade da potencia e a do projeto (`modelo_estimador.elegibilidade_aprendizagem`: icu_ignore_power).

Duas referencias por modalidade (nada de campos redundantes: derivam-se das curvas, deterministicas e causais)
--------------------------------------------------------------------------------------------------------------
  mmp3_inicial_temporada  melhor MMP3 valido ANTERIOR ao inicio da season da data. Depende so de dados anteriores ao
                          inicio da season: um PR posterior nunca a altera (esta "congelada" por construcao).
                          Sem dados validos anteriores: indisponivel (nao se inventa).
  mmp3_pr_atual           melhor MMP3 valido conhecido ate a data (inclui o inicial); evolui ao longo da season.

Estado do teste (separado do recorde): `elegivel` nao exige PR; um esforco pode ser elegivel abaixo do melhor.
  'Yes — 318 W' | 'No (PR: 318 W)' | 'Sem teste elegível' | 'Dados insuficientes'
"""
from datetime import date

import modelo_observacoes_auto as moa
import modelo_testes as mt
import protocolo

DURACAO_S = 180
_INFO = {'nome': '3 min max (MMP3)', 'mede': 'esforco maximo de 3 min usado como referencia operacional do IR',
         'intervalo_dias': 21}


def testes_mmp3():
    """{duracao: info} no formato de protocolo.TESTES, so com a duracao operacional (lida na altura da chamada)."""
    return {DURACAO_S: dict(_INFO)}


LIMIAR = protocolo.LIMIAR_ESFORCO                      # 0,92 (existente)

E_ELEGIVEL = 'elegivel'
E_NAO_ELEGIVEL = 'nao_elegivel'
E_INSUFICIENTE = 'dados_insuficientes'
E_SEM_TESTE = 'sem_teste'

M_IGNORE_POWER = 'icu_ignore_power=true'
M_ELEG_DESCONHECIDA = 'elegibilidade_da_potencia_desconhecida'
M_SEM_REFERENCIA = 'sem_referencia_anterior'
M_AMOSTRA = 'amostra_minima_nao_atingida'
M_ABAIXO = 'abaixo_do_limiar_de_todas_as_referencias'
M_REJEITADO = 'rejeitado_manualmente'

ORIGEM_P0 = 'mmp3_ultimo_yes'
VERSAO_REGRA_P0 = 1


def inicio_season(data, marcos=None):
    """Data ISO de inicio da season que contem `data`: ultimo marco SEASON_START <= data; sem marcos, o inicio
    derivado de SEASON_INICIO_MES (a mesma regra de config.season_por_mes). None se `data` for invalida."""
    import config
    s = str(data)[:10] if data else ''
    try:
        date.fromisoformat(s)
    except ValueError:
        return None
    escolhido = None
    for ini, _nome in sorted(marcos or []):
        if s >= str(ini)[:10]:
            escolhido = str(ini)[:10]
    if escolhido:
        return escolhido
    ano, mes = int(s[:4]), int(s[5:7])
    m = config.SEASON_INICIO_MES
    return f'{ano if mes >= m else ano - 1}-{m:02d}-01'


def _pontos_validos(curvas, modalidade, ate, ignorar):
    """Pontos de 180 s validos e nao ignorados da modalidade com data <= ate: [{date, watts, activity_id}]."""
    out = []
    for c in moa.limpar_curvas(curvas, testes_mmp3()):
        if c['type'] != modalidade or c['date'] > ate or c['activity_id'] in ignorar:
            continue
        for s, w in zip(c['secs'], c['watts']):
            if s == DURACAO_S:
                out.append({'date': c['date'], 'watts': w, 'activity_id': c['activity_id']})
                break
    return out


def _melhor(pontos):
    if not pontos:
        return None
    p = max(pontos, key=lambda q: (q['watts'], q['date'], str(q['activity_id'])))
    return {'watts': p['watts'], 'data': p['date'], 'activity_id': p['activity_id']}


def referencias(curvas, modalidade, data, ignorar_ids=(), marcos=None):
    """{'mmp3_inicial_temporada', 'mmp3_pr_atual', 'inicio_season', 'season'} para a modalidade na `data`."""
    import config
    data = str(data)[:10]
    ignorar = set(ignorar_ids or ())
    ini = inicio_season(data, marcos)
    inicial = _melhor([p for p in _pontos_validos(curvas, modalidade, data, ignorar) if ini and p['date'] < ini])
    atual = _melhor(_pontos_validos(curvas, modalidade, data, ignorar))
    return {'modalidade': modalidade, 'duracao_s': DURACAO_S, 'data': data, 'inicio_season': ini,
            'season': config.season_de(data, marcos or None),
            'mmp3_inicial_temporada': inicial,
            'mmp3_inicial_indisponivel': None if inicial else 'sem_dados_validos_anteriores_ao_inicio_da_season',
            'mmp3_pr_atual': atual,
            'pr_atual_na_season_atual': bool(atual and ini and atual['data'] >= ini)}


def avaliar_esforcos(curvas, modalidade, ate, ignorar_ids=(), elegibilidade_fn=None, marcos=None, horas=None,
                     limiar=LIMIAR):
    """Todos os esforcos de 180 s da modalidade com data <= ate, avaliados so com dados ate essa data (sem futuro).

    elegibilidade_fn(activity_id) -> 'elegivel' | 'inelegivel' | 'desconhecida'. Sem funcao, a elegibilidade e
    'desconhecida' (nunca se presume elegivel). Cada esforco: {test_id, data, watts, activity_id, estado, motivo,
    pr, pct_do_melhor, melhor_anterior_watts, qualifica}.
    """
    import config
    ate = str(ate)[:10]
    ignorar = set(ignorar_ids or ())
    cur = [c for c in curvas or [] if isinstance(c, dict) and c.get('type') == modalidade
           and str(c.get('date'))[:10] <= ate]
    brutos = {}
    for c in moa.limpar_curvas(cur, testes_mmp3()):
        for s, w in zip(c['secs'], c['watts']):
            if s == DURACAO_S:
                brutos[c['activity_id']] = {'date': c['date'], 'watts': w}
                break
    cands = {c['activity_id']: c for c in moa.detetar(
        cur, ignorar, lambda d: config.season_de(d, marcos or None), limiar=0.0, horas=horas or {},
        testes=testes_mmp3()) if c['modalidade'] == modalidade}
    out = []
    for aid, b in sorted(brutos.items(), key=lambda kv: (kv[1]['date'], str(kv[0]))):
        hora = (horas or {}).get(aid)
        base = {'test_id': mt.id_teste(modalidade, DURACAO_S, aid), 'data': b['date'], 'watts': b['watts'],
                'modalidade': modalidade, 'activity_id': aid,
                'inicio': hora if isinstance(hora, str) and len(hora) >= 16 and hora[:10] == b['date'] else None,
                'pr': False, 'pct_do_melhor': None, 'melhor_anterior_watts': None,
                'qualifica': None, 'motivo': None}
        eleg = (elegibilidade_fn(aid) if elegibilidade_fn else None) or 'desconhecida'
        c = cands.get(aid)
        if c is not None and c.get('referencias'):
            todas = c['referencias']
            melhor = todas['all_time']['referencia_watts']
            base['melhor_anterior_watts'] = melhor
            base['pct_do_melhor'] = round(100.0 * b['watts'] / melhor, 1)
            base['pr'] = b['watts'] >= melhor
            base['qualifica'] = any(b['watts'] / r['referencia_watts'] >= limiar for r in todas.values())
        if eleg == 'inelegivel':
            out.append({**base, 'estado': E_NAO_ELEGIVEL, 'motivo': M_IGNORE_POWER})
        elif eleg != 'elegivel':
            out.append({**base, 'estado': E_INSUFICIENTE, 'motivo': M_ELEG_DESCONHECIDA})
        elif c is None:
            out.append({**base, 'estado': E_INSUFICIENTE, 'motivo': M_AMOSTRA})
        elif not c.get('referencias'):
            out.append({**base, 'estado': E_INSUFICIENTE, 'motivo': M_SEM_REFERENCIA})
        elif base['qualifica']:
            out.append({**base, 'estado': E_ELEGIVEL})
        else:
            out.append({**base, 'estado': E_NAO_ELEGIVEL, 'motivo': M_ABAIXO})
    return out


def observacoes_ir(esforcos):
    """Alvos do IR = esforcos ELEGIVEIS (com ou sem PR). Formato das observacoes do servico do IR."""
    return [{'test_id': e['test_id'], 'versao': 1, 'data': e['data'], 'watts': float(e['watts'])}
            for e in esforcos if e['estado'] == E_ELEGIVEL]


def _chave_tempo(e):
    """Ordem cronologica efetiva: data/hora real de inicio da atividade quando conhecida (a mesma que a deteccao
    existente usa dentro do dia), depois activity_id (desempate deterministico)."""
    return (e['data'], (e.get('inicio') or '')[11:19], str(e['activity_id']))


def p0_global(esforcos_por_modalidade):
    """P0 GLOBAL = MMP3 do esforco `YES` mais recente (por data/hora efetiva) entre TODAS as modalidades.

    esforcos_por_modalidade: {modalidade: [esforcos de avaliar_esforcos]} (ja so com dados ate a data de analise).
    Devolve {'estado': 'valida'|'ausente'|'dados_insuficientes', 'registo': esforco|None, 'n_yes', ...}.
    NAO e o melhor valor, nem o PR, nem o CP: sem nenhum `YES` nao ha P0 (sem fallback para outra fonte)."""
    todos = [e for lst in esforcos_por_modalidade.values() for e in lst]
    yes = [e for e in todos if e['estado'] == E_ELEGIVEL]
    diag = {'origem': ORIGEM_P0, 'duracao_s': DURACAO_S, 'versao_regra': VERSAO_REGRA_P0, 'n_yes': len(yes),
            'n_esforcos': len(todos),
            'n_dados_insuficientes': sum(1 for e in todos if e['estado'] == E_INSUFICIENTE)}
    if not yes:
        return {**diag, 'estado': 'dados_insuficientes' if diag['n_dados_insuficientes'] else 'ausente',
                'registo': None}
    r = max(yes, key=_chave_tempo)
    return {**diag, 'estado': 'valida', 'registo': {k: r.get(k) for k in (
        'test_id', 'activity_id', 'modalidade', 'data', 'inicio', 'watts', 'pr', 'pct_do_melhor')}}


def texto_estado(estado, watts=None, pr_watts=None):
    if estado == E_ELEGIVEL:
        return f'Yes — {watts:.0f} W'
    if estado == E_NAO_ELEGIVEL:
        return f'No (PR: {pr_watts:.0f} W)' if pr_watts else 'No'
    if estado == E_INSUFICIENTE:
        return 'Dados insuficientes'
    return 'Sem teste elegível'


def estado_no_dia(esforcos, data, pr_watts=None):
    """Estado do teste MMP3 numa data: elegivel (melhor entre os elegiveis do dia) > dados insuficientes >
    nao elegivel; sem esforco de 180 s nesse dia: 'Sem teste elegível'. O estado de elegibilidade e o de recorde
    (`pr`) sao campos separados."""
    data = str(data)[:10]
    do_dia = [e for e in esforcos if e['data'] == data]
    if not do_dia:
        return {'estado': E_SEM_TESTE, 'texto': texto_estado(E_SEM_TESTE), 'data': data, 'watts': None, 'pr': False,
                'motivo': None, 'n_esforcos_no_dia': 0}
    for alvo in (E_ELEGIVEL, E_INSUFICIENTE, E_NAO_ELEGIVEL):
        sel = [e for e in do_dia if e['estado'] == alvo]
        if sel:
            e = max(sel, key=lambda x: x['watts'])
            return {'estado': alvo, 'texto': texto_estado(alvo, e['watts'], pr_watts), 'data': data,
                    'watts': e['watts'], 'pr': e['pr'], 'motivo': e['motivo'], 'activity_id': e['activity_id'],
                    'test_id': e['test_id'], 'pct_do_melhor': e['pct_do_melhor'], 'n_esforcos_no_dia': len(do_dia)}
    return {'estado': E_SEM_TESTE, 'texto': texto_estado(E_SEM_TESTE), 'data': data, 'watts': None, 'pr': False,
            'motivo': None, 'n_esforcos_no_dia': len(do_dia)}


def contexto(curvas, modalidade, ate, ignorar_ids=(), elegibilidade_fn=None, marcos=None, horas=None, limiar=LIMIAR,
             esforcos=None):
    """Contexto MMP3 de uma modalidade para a UI: referencias, ultimo esforco de 180 s e o seu estado.
    `esforcos`: avaliacao ja feita (a mesma que alimenta o P0 global e o ajuste); por omissao avalia-se aqui."""
    esf = esforcos if esforcos is not None else avaliar_esforcos(curvas, modalidade, ate, ignorar_ids,
                                                                  elegibilidade_fn, marcos, horas, limiar)
    ref = referencias(curvas, modalidade, ate, ignorar_ids, marcos)
    pr_w = (ref['mmp3_pr_atual'] or {}).get('watts')
    ult = esf[-1]['data'] if esf else None
    est = estado_no_dia(esf, ult, pr_w) if ult else estado_no_dia(esf, ate, pr_w)
    return {**ref, 'limiar': limiar, 'n_esforcos': len(esf), 'n_elegiveis': sum(1 for e in esf if e['estado'] == E_ELEGIVEL),
            'ultimo_esforco': est}
