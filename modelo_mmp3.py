"""MMP3 (180 s) como referencia operacional do IR: referencias por season, elegibilidade do esforco e estado do teste.

Modulo PURO (sem base, sem rede, sem relogio). O P0 GLOBAL do IR e o MMP3 do esforco `YES` (estado elegivel) MAIS
RECENTE entre as quatro modalidades (`p0_global`): nao e o melhor historico nem o PR, e nao vem do CP Model. Nada
aqui altera o CP Model.

Tres referencias SEPARADAS (regra 4)
-----------------------------------
  * DETECAO sazonal: o recorde da season (`ref_temporada_watts`) e o all-time causal (`melhor_anterior_watts`). Um
    esforco que supera o recorde da season sem recorde all-time qualificado fica `a_verificar` (motivo
    `recorde_de_temporada_sem_recorde_all_time` ou `primeiro_candidato_da_temporada`): nunca YES automatico.
  * HISTORICO DE YES: registos YES ja gravados (`modelo_mmp3_yes_historico`, append-only, uma linha por teste e versao da
    regra). Um YES gravado nao sai do P0 por mudar a temporada nem por deixar de ser elegivel nos esforcos vivos; so a
    rejeicao manual (`rejeitados`) o retira.
  * P0: o MMP3 do YES mais recente entre historico e vivos (`p0_global`). Nao e a referencia de deteccao nem o recorde
    sazonal da Power Curve. Sem nenhum YES: `ausente`; so com esforcos insuficientes: `dados_insuficientes`.
  * `a_verificar` e um ESTADO explicito (nao e NO com flag) e nunca conta para o P0 nem para as observacoes do IR.

Regra do YES (decisao do utilizador, 2026-10-19; substitui o OR recente/season/all-time)
------------------------------------------------------------------------------------
Atualizar a Power Curve NAO cria um teste: um esforco de 180 s e `YES` quando, cumulativamente,
  1. SINAL DE TESTE (regra 3, 2026-10-19): (a) a TAG de tempo de 3 min da atividade ('3min', '3 min', '180s', ...;
     `moa.tag_coincide_duracao`; origem `tag_manual`), OU (b) um intervalo isolado de 175-185 s da propria atividade
     com potencia media >= 0,92 x melhor MMP3 anterior e >= 0,92 x o pico de 180 s (`modelo_mmp3_intervalos`; origem
     `intervalo_detectado`). Repeticoes, intervalos de 5 min e picos noutro trecho ficam `a_verificar`, nunca YES. Um
     treino sem sinal nunca e YES;
  2. a potencia nao e ignorada (`icu_ignore_power`) e a elegibilidade e conhecida;
  3. existe referencia valida ANTERIOR e a amostra minima existente (>= 5 pontos validos de 180 s da modalidade);
  4. potencia_180s / melhor_MMP3_valido_ANTERIOR >= LIMIAR (0,92, inclusivo; direcao: esforco sobre a referencia).
A referencia e so o melhor MMP3 valido anterior (all-time causal, sem o proprio esforco, sem atividades ignoradas): as
referencias `recente` e `season` da deteccao existente deixam de qualificar, porque sao feitas de treinos submaximos
(253 -> 268 -> 290 -> 300 -> 318 W qualificava em cadeia contra a season, mesmo a 79 % do all-time de 401 W). Superar o
recorde (razao >= 1) e YES; ficar abaixo, ate 0,92 do recorde, tambem. Nao exige PR.

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

import modelo_mmp3_intervalos as mi
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
E_A_VERIFICAR = 'a_verificar'       # ESTADO EXPLICITO (nao e NO com flag): ha sinal de teste ou candidato sazonal
                                   # sem prova suficiente. Nunca conta para P0 nem para observacoes do IR.
M_RECORDE_SAZONAL = 'recorde_de_temporada_sem_recorde_all_time'   # sinal + melhor da temporada, abaixo do all-time
M_PRIMEIRO_DA_TEMPORADA = 'primeiro_candidato_da_temporada'        # sinal sem referencia sazonal anterior

M_IGNORE_POWER = 'icu_ignore_power=true'
M_ELEG_DESCONHECIDA = 'elegibilidade_da_potencia_desconhecida'
M_SEM_REFERENCIA = 'sem_referencia_anterior'
M_AMOSTRA = 'amostra_minima_nao_atingida'
M_ABAIXO = 'abaixo_do_limiar_de_todas_as_referencias'
M_REJEITADO = 'rejeitado_manualmente'
M_SEM_TAG = 'sem_tag_3min'                  # (legado, nao usado na classificacao desde a regra 5)
M_SEM_SINAL = 'mmp3_valido_sem_sinal_estrutural'   # candidato sem tag/intervalo: A_VERIFICAR (confirmacao manual)
M_VALOR_MUDOU = 'valor_mudou_desde_a_confirmacao'  # a confirmacao refere-se a outro MMP3: nova decisao necessaria
ORIGEM_CONFIRMACAO = 'confirmacao_manual'

ORIGEM_P0 = 'mmp3_ultimo_yes'
VERSAO_REGRA_P0 = 5        # 5: identificacao por MMP3 >= limiar (sem exigir tag), A_VERIFICAR sem sinal, confirmacao manual
                           #    -> YES, comparacao explicita com o P0. 4: 3 (YES = tag 3 min | intervalo 3 min) + estado A_VERIFICAR explicito e contexto
                           #    sazonal por efeito. O conjunto de YES e IGUAL ao da 3 (testado): so muda o rotulo
                           #    dos candidatos que nao sao YES. Migracao = novas linhas no registo (nunca reescrever).


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


def tem_tag_3min(tags):
    """True se alguma tag de TEMPO da atividade iguala DURACAO_S (ex.: '3min' = 180 s). Distancias nunca coincidem."""
    return moa.tag_coincide_duracao(moa.tags_de_teste(tags), DURACAO_S)


def avaliar_esforcos(curvas, modalidade, ate, ignorar_ids=(), elegibilidade_fn=None, marcos=None, horas=None,
                     limiar=LIMIAR, tags=None, atividades_por_id=None):
    """Todos os esforcos de 180 s da modalidade com data <= ate, avaliados so com dados ate essa data (sem futuro).

    elegibilidade_fn(activity_id) -> 'elegivel' | 'inelegivel' | 'desconhecida'. Sem funcao, a elegibilidade e
    'desconhecida' (nunca se presume elegivel). tags: {activity_id: [tags]}; sem tags conhecidas o esforco NAO e YES
    (nunca se presume teste). Cada esforco: {test_id, data, watts, activity_id, estado, motivo, pr, tag_3min,
    pct_do_melhor, melhor_anterior_watts, qualifica}. `pct_do_melhor` = 100 x watts / melhor MMP3 valido ANTERIOR.
    """
    import config
    ate = str(ate)[:10]
    ignorar = set(ignorar_ids or ())
    tags = {} if tags is None else tags
    atividades_por_id = atividades_por_id or {}
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
        ref_sz = ((c or {}).get('referencias') or {}).get('season') if c else None
        base = {'test_id': mt.id_teste(modalidade, DURACAO_S, aid), 'data': b['date'], 'watts': b['watts'],
                'modalidade': modalidade, 'activity_id': aid,
                'season': config.season_de(b['date'], marcos or None),
                'ref_temporada_watts': ref_sz['referencia_watts'] if ref_sz else None,
                'ref_temporada_data': ref_sz['referencia_data'] if ref_sz else None,
                'pct_da_temporada': None,
                'inicio': hora if isinstance(hora, str) and len(hora) >= 16 and hora[:10] == b['date'] else None,
                'tag_3min': tem_tag_3min(tags.get(aid)), 'pr': False, 'pct_do_melhor': None,
                'melhor_anterior_watts': None, 'melhor_anterior_data': None, 'limiar': limiar,
                'qualifica': None, 'motivo': None, 'origem_classificacao': None, 'classe_intervalo': None,
                'intervalo': None, 'a_verificar': False, 'motivo_decisao': None}
        eleg = (elegibilidade_fn(aid) if elegibilidade_fn else None) or 'desconhecida'
        c = cands.get(aid)
        ref = ((c or {}).get('referencias') or {}).get('all_time')       # SO o melhor anterior (all-time causal)
        if ref:
            razao = b['watts'] / ref['referencia_watts']
            base.update(melhor_anterior_watts=ref['referencia_watts'], melhor_anterior_data=ref['referencia_data'],
                        pct_do_melhor=round(100.0 * razao, 1), pr=razao >= 1.0, qualifica=razao >= limiar)
        if ref_sz:
            base['pct_da_temporada'] = round(100.0 * b['watts'] / ref_sz['referencia_watts'], 1)
        iv = mi.classificar(atividades_por_id.get(aid), b['watts'], ref['referencia_watts'] if ref else None, limiar)
        base.update(classe_intervalo=iv['classe'], intervalo=iv['intervalo'], a_verificar=iv['classe'] in mi.A_VERIFICAR,
                    n_grupos_intervalos=iv['n_grupos_potencia'], n_grupos_3min=iv['n_grupos_3min'],
                    n_grupos_3min_potencia_ok=iv['n_grupos_3min_potencia_ok'],
                    n_grupos_longos_potencia_ok=iv['n_grupos_longos_potencia_ok'])
        if base['tag_3min']:
            base.update(origem_classificacao=mi.ORIGEM_TAG, motivo_decisao='tag de 3 min na atividade')
        elif iv['classe'] == mi.C_AUTO:
            base.update(origem_classificacao=mi.ORIGEM_INTERVALO, motivo_decisao=(
                'intervalo isolado de %ds a %.0f W (>= %.2f x melhor anterior e x pico de 180 s)'
                % (iv['intervalo']['duracao_s'], iv['intervalo']['watts'], limiar)))
        sinal = base['origem_classificacao'] is not None
        classe = iv['classe']
        # IDENTIFICACAO: MMP3 valido >= LIMIAR da referencia all-time causal (com ou sem tag/intervalo).
        # CONFIRMACAO AUTOMATICA DE YES: so com sinal estrutural (tag de 3 min ou intervalo isolado 175-185 s).
        # Sem sinal estrutural um candidato fica A_VERIFICAR (confirmacao manual), nunca YES automatico.
        if eleg == 'inelegivel':
            out.append({**base, 'estado': E_NAO_ELEGIVEL, 'motivo': M_IGNORE_POWER})
        elif eleg != 'elegivel':
            out.append({**base, 'estado': E_INSUFICIENTE, 'motivo': M_ELEG_DESCONHECIDA})
        elif c is None:
            out.append({**base, 'estado': E_INSUFICIENTE, 'motivo': M_AMOSTRA})
        elif not ref:
            out.append({**base, 'estado': E_INSUFICIENTE, 'motivo': M_SEM_REFERENCIA})
        elif base['qualifica'] and sinal:
            out.append({**base, 'estado': E_ELEGIVEL})
        elif base['qualifica']:
            out.append({**base, 'estado': E_A_VERIFICAR, 'motivo': M_SEM_SINAL, 'a_verificar': True,
                        'motivo_decisao': ('MMP3 >= %.2f da referencia anterior sem tag de 3 min nem intervalo '
                                           'isolado (%s): candidato, confirmacao manual, nunca YES automatico'
                                           % (limiar, classe))})
        elif sinal and (ref_sz is None or b['watts'] >= limiar * ref_sz['referencia_watts']):
            # sinal de teste e o melhor da temporada, mas abaixo do all-time: contexto sazonal, A VERIFICAR
            motivo = M_PRIMEIRO_DA_TEMPORADA if ref_sz is None else M_RECORDE_SAZONAL
            out.append({**base, 'estado': E_A_VERIFICAR, 'motivo': motivo, 'a_verificar': True,
                        'motivo_decisao': 'sinal de teste sem recorde all-time (%s): revisao manual' % motivo})
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


def diagnostico_esforcos(esforcos_por_modalidade, limiar=LIMIAR):
    """Contagens (a partir dos MESMOS esforcos avaliados; nenhum calculo novo) que explicam porque existe ou nao um YES.

    Por modalidade e no total: n_180 (potencia valida de 180 s), n_tag_3min (tag de 3 min reconhecida), n_sem_tag,
    n_potencia_ignorada, n_elegibilidade_desconhecida, n_amostra_ou_referencia_insuficiente, n_proximidade_ok_sem_outros
    (razao >= limiar mas NAO YES: falta tag, potencia ignorada/desconhecida ou rejeicao manual), n_abaixo_limiar (com tag e
    elegibilidade ok mas razao < limiar), n_yes."""
    def conta(esf):
        return {'n_180': len(esf),
                'n_tag_3min': sum(1 for e in esf if e.get('tag_3min')),
                'n_sem_tag': sum(1 for e in esf if not e.get('tag_3min')),
                'n_potencia_ignorada': sum(1 for e in esf if e['motivo'] == M_IGNORE_POWER),
                'n_elegibilidade_desconhecida': sum(1 for e in esf if e['motivo'] == M_ELEG_DESCONHECIDA),
                'n_amostra_ou_referencia_insuficiente': sum(1 for e in esf if e['motivo'] in (M_AMOSTRA, M_SEM_REFERENCIA)),
                'n_proximidade_ok_sem_outros': sum(1 for e in esf if e['estado'] != E_ELEGIVEL and e.get('qualifica')
                                                   and e.get('pct_do_melhor') is not None
                                                   and e['pct_do_melhor'] >= round(100.0 * limiar, 1)),
                'n_abaixo_limiar': sum(1 for e in esf if e['motivo'] == M_ABAIXO),
                'n_intervalos_analisados': sum(e.get('n_grupos_intervalos') or 0 for e in esf),
                'n_atividades_com_intervalos': sum(1 for e in esf if e.get('n_grupos_intervalos')),
                'n_candidatos_duracao_3min': sum(1 for e in esf if e.get('n_grupos_3min')),
                'n_candidatos_potencia_3min': sum(1 for e in esf if e.get('n_grupos_3min_potencia_ok')),
                'n_candidatos_longos_potencia': sum(1 for e in esf if e.get('n_grupos_longos_potencia_ok')),
                'n_a_verificar': sum(1 for e in esf if e.get('a_verificar') and e['estado'] != E_ELEGIVEL),
                'n_estado_a_verificar': sum(1 for e in esf if e['estado'] == E_A_VERIFICAR),
                'n_recorde_sazonal': sum(1 for e in esf if e['motivo'] == M_RECORDE_SAZONAL),
                'n_confirmados_tag_manual': sum(1 for e in esf if e['estado'] == E_ELEGIVEL
                                                and e.get('origem_classificacao') == mi.ORIGEM_TAG),
                'n_elegiveis_automaticos': sum(1 for e in esf if e['estado'] == E_ELEGIVEL
                                               and e.get('origem_classificacao') == mi.ORIGEM_INTERVALO),
                'n_yes': sum(1 for e in esf if e['estado'] == E_ELEGIVEL)}
    por_mod = {m: conta(lst) for m, lst in esforcos_por_modalidade.items()}
    total = conta([e for lst in esforcos_por_modalidade.values() for e in lst])
    if total['n_yes']:
        motivo = None
    elif total['n_180'] == 0:
        motivo = 'sem_esforcos_180s'
    elif total['n_tag_3min'] == 0 and total['n_candidatos_potencia_3min'] == 0:
        motivo = 'nenhuma_atividade_com_tag_3min'
    else:
        motivo = 'nenhum_esforco_cumpre_todos_os_criterios'
    return {'total': total, 'por_modalidade': por_mod, 'motivo_sem_yes': motivo, 'limiar': limiar}


def p0_global(esforcos_por_modalidade, historico_yes=None, rejeitados=None):
    """P0 GLOBAL = MMP3 do esforco `YES` mais recente (por data/hora efetiva) entre TODAS as modalidades.

    esforcos_por_modalidade: {modalidade: [esforcos de avaliar_esforcos]} (ja so com dados ate a data de analise).
    historico_yes: registos YES JA GRAVADOS em registo_mmp3 (de temporadas anteriores, mesma forma de um esforco).
      Um YES gravado NAO sai do P0 por mudar a temporada, nem por deixar de estar nos esforcos vivos (ex.: tag ou
      potencia alterada depois): e registo historico. So sai por revisao manual: `rejeitados` (test_id).
    rejeitados: {test_id} rejeitados manualmente (revisao atual); exclui YES vivos e gravados.
    Devolve {'estado': 'valida'|'ausente'|'dados_insuficientes', 'registo': esforco|None, 'n_yes', ...}.
    NAO e o melhor valor, nem o PR, nem o CP: sem nenhum `YES` nao ha P0 (sem fallback para outra fonte)."""
    rejeitados = set(rejeitados or ())
    todos = [e for lst in esforcos_por_modalidade.values() for e in lst]
    vivos = [e for e in todos if e['estado'] == E_ELEGIVEL]
    ids_vivos = {e['test_id'] for e in vivos}
    gravados = [dict(h, origem_registo='historico') for h in (historico_yes or [])
                if h.get('test_id') not in ids_vivos]
    yes = [e for e in vivos + gravados if e.get('test_id') not in rejeitados]
    diag = {'origem': ORIGEM_P0, 'duracao_s': DURACAO_S, 'versao_regra': VERSAO_REGRA_P0, 'n_yes': len(yes),
            'n_yes_historicos': len(gravados), 'n_esforcos': len(todos),
            'n_dados_insuficientes': sum(1 for e in todos if e['estado'] == E_INSUFICIENTE),
            'diagnostico': diagnostico_esforcos(esforcos_por_modalidade)}
    if not yes:
        return {**diag, 'estado': 'dados_insuficientes' if diag['n_dados_insuficientes'] else 'ausente',
                'registo': None}
    r = max(yes, key=_chave_tempo)
    return {**diag, 'estado': 'valida', 'registo': {k: r.get(k) for k in (
        'test_id', 'activity_id', 'modalidade', 'data', 'inicio', 'watts', 'pr', 'pct_do_melhor',
        'origem_classificacao', 'motivo_decisao', 'classe_intervalo', 'origem_registo', 'season')}}


def comparar_p0(watts, p0_watts):
    """Comparacao EXPLICITA do MMP3 de um candidato com um P0 de referencia. So apresenta: nao altera nenhum P0.
    Sem P0 (ou sem watts) devolve 'sem_p0' / None: a falta de referencia nunca e tratada como valor baixo."""
    if not (isinstance(watts, (int, float)) and isinstance(p0_watts, (int, float))):
        return {'estado': 'sem_p0', 'diferenca_w': None, 'diferenca_pct': None}
    d = round(float(watts) - float(p0_watts), 1)
    pct = round(100.0 * d / float(p0_watts), 1) if p0_watts else None
    est = 'superado' if d > 0 else ('igualado' if d == 0 else 'abaixo')
    return {'estado': est, 'diferenca_w': d, 'diferenca_pct': pct}


def comparacoes_candidatos(esforcos_por_modalidade, historico_yes=None, rejeitados=None):
    """Para cada candidato (A_VERIFICAR ou ELEGIVEL), compara o MMP3 com o P0 ANTERIOR: o ultimo YES
    estritamente anterior ao candidato (causal: nunca um YES posterior), na mesma modalidade ou em qualquer uma
    (o P0 e global). YES rejeitados nao contam. Os YES do historico sao os gravados (mesma regra do p0_global)."""
    rejeitados = set(rejeitados or ())
    todos = [e for lst in esforcos_por_modalidade.values() for e in lst]
    yes = [e for e in todos if e['estado'] == E_ELEGIVEL and e.get('test_id') not in rejeitados]
    ids = {e['test_id'] for e in yes}
    yes += [dict(h, origem_registo='historico') for h in (historico_yes or [])
            if h.get('test_id') not in ids and h.get('test_id') not in rejeitados]
    out = []
    for e in sorted((x for x in todos if x['estado'] in (E_ELEGIVEL, E_A_VERIFICAR)), key=_chave_tempo):
        anteriores = [y for y in yes if y.get('test_id') != e['test_id'] and _chave_tempo(y) < _chave_tempo(e)]
        p0 = max(anteriores, key=_chave_tempo) if anteriores else None
        out.append({'test_id': e['test_id'], 'modalidade': e['modalidade'], 'data': e['data'],
                    'activity_id': e['activity_id'], 'watts': e['watts'], 'estado': e['estado'],
                    'p0_anterior': None if p0 is None else {'test_id': p0.get('test_id'), 'data': p0.get('data'),
                                                            'watts': p0.get('watts'), 'origem_registo':
                                                            p0.get('origem_registo', 'vivo')},
                    'comparacao': comparar_p0(e['watts'], p0.get('watts') if p0 else None)})
    return out


def texto_estado(estado, watts=None, pr_watts=None):
    if estado == E_A_VERIFICAR:
        return 'A verificar'
    if estado == E_ELEGIVEL:
        return f'Yes — {watts:.0f} W'
    if estado == E_NAO_ELEGIVEL:
        return f'No (PR: {pr_watts:.0f} W)' if pr_watts else 'No'
    if estado == E_INSUFICIENTE:
        return 'Dados insuficientes'
    return 'Sem teste elegível'


def estado_no_dia(esforcos, data, pr_watts=None):
    """Estado do teste MMP3 numa data: elegivel (melhor entre os elegiveis do dia) > dados insuficientes >
    a verificar (revisao manual) > nao elegivel; sem esforco de 180 s nesse dia: 'Sem teste elegível'. O estado de elegibilidade e o de recorde
    (`pr`) sao campos separados."""
    data = str(data)[:10]
    do_dia = [e for e in esforcos if e['data'] == data]
    if not do_dia:
        return {'estado': E_SEM_TESTE, 'texto': texto_estado(E_SEM_TESTE), 'data': data, 'watts': None, 'pr': False,
                'motivo': None, 'n_esforcos_no_dia': 0}
    for alvo in (E_ELEGIVEL, E_A_VERIFICAR, E_INSUFICIENTE, E_NAO_ELEGIVEL):
        sel = [e for e in do_dia if e['estado'] == alvo]
        if sel:
            e = max(sel, key=lambda x: x['watts'])
            return {'estado': alvo, 'texto': texto_estado(alvo, e['watts'], pr_watts), 'data': data,
                    'watts': e['watts'], 'pr': e['pr'], 'motivo': e['motivo'], 'activity_id': e['activity_id'],
                    'test_id': e['test_id'], 'pct_do_melhor': e['pct_do_melhor'], 'n_esforcos_no_dia': len(do_dia)}
    return {'estado': E_SEM_TESTE, 'texto': texto_estado(E_SEM_TESTE), 'data': data, 'watts': None, 'pr': False,
            'motivo': None, 'n_esforcos_no_dia': len(do_dia)}


def contexto(curvas, modalidade, ate, ignorar_ids=(), elegibilidade_fn=None, marcos=None, horas=None, limiar=LIMIAR,
             esforcos=None, tags=None, atividades_por_id=None):
    """Contexto MMP3 de uma modalidade para a UI: referencias, ultimo esforco de 180 s e o seu estado.
    `esforcos`: avaliacao ja feita (a mesma que alimenta o P0 global e o ajuste); por omissao avalia-se aqui."""
    esf = esforcos if esforcos is not None else avaliar_esforcos(curvas, modalidade, ate, ignorar_ids,
                                                                  elegibilidade_fn, marcos, horas, limiar, tags, atividades_por_id)
    ref = referencias(curvas, modalidade, ate, ignorar_ids, marcos)
    pr_w = (ref['mmp3_pr_atual'] or {}).get('watts')
    ult = esf[-1]['data'] if esf else None
    est = estado_no_dia(esf, ult, pr_w) if ult else estado_no_dia(esf, ate, pr_w)
    return {**ref, 'limiar': limiar, 'n_esforcos': len(esf), 'n_elegiveis': sum(1 for e in esf if e['estado'] == E_ELEGIVEL),
            'ultimo_esforco': est}
