"""Distribuicao temporal da CARGA OBSERVADA entre Bike, Row, Run e Ski. Nucleo puro (sem BD, sem rede).

O que e: a proporcao da carga registada atribuida a cada modalidade ciclica numa janela movel, por um de
dois metodos independentes (Training Load ou TRIMP) e a comparacao entre eles.
O que NAO e: influencia fisiologica, equilibrio ideal, nem prova de causalidade sobre o desempenho.
So ha informacao observada; nao ha modelo nem parametros estimados.

Metodos (nunca se misturam no mesmo numerador ou denominador; nada substitui o outro quando falta)
--------------------------------------------------------------------------------------------------
 * 'training_load': campo `icu_training_load` da atividade (a variavel que o PMC e o IR ja usam). Valida =
   numero finito e >= 0. O Intervals.icu calcula-a por atividade a partir de potencia, FC ou ritmo.
 * 'trimp': campo `trimp` da atividade. NAO e calculado neste projeto: vem do Intervals.icu. Verificado
   contra um export local de atividades Bike/Row/Run/Ski (razao 1,000, CV 0,000) que e o TRIMP de
   Banister com coeficientes masculinos, TRIMP = duracao_min * x * 0,64 * exp(1,92 * x), com
   x = (FC_media - FC_repouso) / (FC_max - FC_repouso), duracao = moving_time, FC media da atividade (nao
   por amostra), FC_max do desporto e FC_repouso do dia (campos athlete_max_hr e icu_resting_hr). Valido =
   finito, > 0 e FC nao marcada para ignorar (`icu_ignore_hr`, mesma convencao de helpers.py). O TRIMP
   sessao-RPE de `pmc._trimp_e_kj` e outra variavel (exige kJ e RPE, serve o modelo CTL vs kJ): nao e
   usado aqui.

Definicoes comuns
-----------------
 * Modalidades elegiveis: `config.CICLICOS` (Bike, Row, Run, Ski), a MESMA lista que o PMC por modalidade
   e o modelo IR usam. Uma atividade entra se e so se `config.TYPE_MAP[tipo_cru]` (procura exata no
   dicionario, sem texto parcial) for uma delas. Musculacao (WeightTraining, Workout, Crossfit mapeiam
   para 'WeightTraining') e qualquer tipo nao mapeado ficam fora ANTES de qualquer soma, para os dois
   metodos: nao entram no numerador, no denominador, nas series, nas coberturas nem nos totais. Nenhum
   registo e alterado ou apagado.
 * Varias atividades da mesma modalidade no mesmo dia somam-se; a mesma atividade (mesmo id) conta uma so
   vez. Um dia sem nenhuma atividade elegivel e treino NULO (0); um dia com atividade elegivel mas sem
   valor valido para o metodo e dado AUSENTE (nunca zero).
 * Janela de W dias que termina no dia de referencia t e o inclui: [t-W+1, t]. Numeradores e denominador
   usam exatamente os mesmos dias, e as duas metricas usam as mesmas janelas e as mesmas datas.
        Q_m(t) = 100 * L_m(t) / (L_Bike + L_Row + L_Run + L_Ski)(t).
   Com denominador > 0 as quatro quotas somam 100. Com denominador 0 NAO se inventa percentagem
   ('sem_treino' se nao houve atividade elegivel sem valores em falta, 'carga_ausente' se houve
   atividades sem valor valido). So se mostram janelas completas e a serie acaba na ultima atividade
   elegivel (os dias a seguir nao sao assumidos como descanso; so com `ate` explicito).
 * Cobertura: por janela, atividades elegiveis e atividades com valor valido em cada metodo. Uma
   distribuicao com cobertura incompleta representa so as atividades com valor valido.
 * Comparacao: as duas distribuicoes nas mesmas janelas, a diferenca em pontos percentuais (TRIMP menos
   Training Load) e, como controlo, a mesma diferenca calculada so com as atividades que tem os dois
   valores validos (separa o efeito da metrica do efeito da cobertura). Os valores absolutos de TRIMP e
   Training Load NAO sao diretamente comparaveis e nao ha conversao. Nao se atribui um metodo vencedor.
"""
import math
from datetime import date, timedelta

import config

MODALIDADES = tuple(config.CICLICOS)
JANELAS = (7, 28, 42, 90)
JANELA_PADRAO = 28
HORIZONTE_PADRAO = 365
HORIZONTE_MAX = 3650
METODOS = ('training_load', 'trimp')
METODO_PADRAO = 'training_load'
CAMPOS = {'training_load': 'icu_training_load', 'trimp': 'trimp'}
MOTIVOS = ('sem_valor', 'valor_invalido', 'fc_ignorada')
FONTES = ('power_load', 'hr_load', 'pace_load')
TIPO_ANALISE = 'distribuicao_observada_da_carga'
ROTULOS = {'training_load': 'Training Load', 'trimp': 'TRIMP'}
UNIDADES = {
    'training_load': 'icu_training_load (Intervals.icu): carga registada por atividade',
    'trimp': 'TRIMP de Banister (campo trimp do Intervals.icu, FC media da atividade): unidades de TRIMP',
}
DEFINICOES = {
    'training_load': ('icu_training_load: carga por atividade calculada pelo Intervals.icu a partir de potencia, '
                      'FC ou ritmo (cada uma com os limiares do respetivo desporto). Valida: finita e >= 0.'),
    'trimp': ('trimp: TRIMP de Banister (coeficientes masculinos 0,64*exp(1,92*x)) calculado pelo Intervals.icu com '
              'a FC MEDIA da atividade, x = (FC media - FC repouso)/(FC max - FC repouso), vezes a duracao em minutos. '
              'FC max e do desporto, FC repouso e do dia. Nao e recalculado aqui. Valido: finito, > 0 e FC nao '
              'marcada para ignorar. Atividades sem TRIMP valido ficam fora (nunca recebem o Training Load).'),
}
AVISOS = {
    'training_load': ('Distribuicao OBSERVADA da carga registada (icu_training_load): nao e influencia fisiologica, '
                      'nem equilibrio ideal, nem prova que mudar a distribuicao mude o desempenho. So entram Bike, '
                      'Row, Run e Ski. A carga mistura fontes (potencia, FC, ritmo) calibradas pelos limiares de cada '
                      'desporto, por isso a comparabilidade entre modalidades nao esta garantida: ver a comparacao '
                      'com TRIMP e a composicao por fonte. Dia sem treino (carga 0) e dado ausente (atividade sem '
                      'carga valida) sao coisas diferentes.'),
    'trimp': ('Distribuicao OBSERVADA do TRIMP registado: nao e influencia fisiologica, nem equilibrio ideal, nem prova '
              'que mudar a distribuicao mude o desempenho. So entram Bike, Row, Run e Ski. O TRIMP depende so da FC media: '
              'subestima sessoes de intensidade variavel, ignora a duracao relativa a zonas e usa a FC maxima de cada '
              'desporto (a mesma FC pesa de forma diferente entre modalidades). So conta atividades com TRIMP valido: '
              'com cobertura incompleta nao e a distribuicao completa da carga real. Dia sem treino (0) e atividade '
              'sem TRIMP valido (ausente) sao coisas diferentes.'),
}
AVISO = AVISOS['training_load']                 # compatibilidade
AVISO_COMPARACAO = ('Comparacao entre dois metodos de carga, nao entre qualidade: os valores absolutos de TRIMP e '
                    'Training Load nao sao diretamente comparaveis e nao ha conversao. Percentagens parecidas nao '
                    'provam que um metodo seja melhor; diferencas grandes mostram que a distribuicao depende da '
                    'metrica escolhida. O controlo "mesmas atividades" usa so atividades com os dois valores validos.')


def modalidade_elegivel(tipo_cru):
    """Modalidade (Bike/Row/Run/Ski) de um tipo cru de atividade, ou None. Procura exata no TYPE_MAP."""
    if not isinstance(tipo_cru, str):
        return None
    m = config.TYPE_MAP.get(tipo_cru)
    return m if m in MODALIDADES else None


def _numero(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _valido(v):
    return _numero(v) and v >= 0


def _dia(a):
    d = str(a.get('start_date_local') or '')[:10]
    try:
        date.fromisoformat(d)
    except ValueError:
        return None
    return d


def _fc_ignorada(a):
    v = a.get('icu_ignore_hr')
    return v is True or (isinstance(v, str) and v.strip().lower() == 'true')


def valor_do_metodo(a, metodo):
    """(valor, None) se a atividade tem valor valido para o metodo; (None, motivo) caso contrario."""
    if metodo == 'training_load':
        v = a.get('icu_training_load')
        if v is None:
            return None, 'sem_valor'
        return (float(v), None) if _valido(v) else (None, 'valor_invalido')
    if metodo == 'trimp':
        if _fc_ignorada(a):
            return None, 'fc_ignorada'
        v = a.get('trimp')
        if v is None:
            return None, 'sem_valor'
        return (float(v), None) if _numero(v) and v > 0 else (None, 'valor_invalido')
    raise ValueError(f'metodo invalido: {metodo!r} (use {METODOS})')


def classificar(atividades):
    """Atividades ELEGIVEIS (so as quatro modalidades), sem duplicados, com data valida. O filtro de
    modalidade acontece aqui, antes de qualquer soma. Devolve (registos, info); cada registo traz o
    valor e o motivo de ausencia para os DOIS metodos, de modo que ambos usam exatamente as mesmas atividades."""
    recs, vistos = [], set()
    info = {'n_duplicadas': 0, 'n_sem_data': 0, 'n_fora_das_quatro_modalidades': 0}
    for a in atividades or []:
        if not isinstance(a, dict):
            continue
        m = modalidade_elegivel(a.get('type'))
        if m is None:
            info['n_fora_das_quatro_modalidades'] += 1
            continue
        aid = a.get('id')
        if aid is not None:
            if aid in vistos:
                info['n_duplicadas'] += 1
                continue
            vistos.add(aid)
        d = _dia(a)
        if d is None:
            info['n_sem_data'] += 1
            continue
        r = {'mod': m, 'dia': d, 'fonte': None}
        for met in METODOS:
            r[met], r[met + '_motivo'] = valor_do_metodo(a, met)
        if r['training_load'] is not None:
            r['fonte'] = 'outra'
            for nome in FONTES:
                x = a.get(nome)
                if _valido(x) and abs(float(x) - r['training_load']) < 1e-6:
                    r['fonte'] = nome
                    break
        r['fc_max'] = a.get('athlete_max_hr') if _numero(a.get('athlete_max_hr')) else None
        recs.append(r)
    return recs, info


def agregar(atividades, metodo=METODO_PADRAO):
    """Vista agregada por metodo: {'carga': {mod: {data: soma}}, 'ausentes': {data: n}, 'treino': {data: n},
    'fontes': {mod: {data: {fonte: carga}}}, 'info': {...}}."""
    recs, info = classificar(atividades)
    carga = {m: {} for m in MODALIDADES}
    fontes = {m: {} for m in MODALIDADES}
    ausentes, treino = {}, {}
    info = {**info, 'n_validas': {m: 0 for m in MODALIDADES}, 'n_sem_carga': {m: 0 for m in MODALIDADES}}
    for r in recs:
        d, m = r['dia'], r['mod']
        treino[d] = treino.get(d, 0) + 1
        v = r[metodo]
        if v is None:
            info['n_sem_carga'][m] += 1
            ausentes[d] = ausentes.get(d, 0) + 1
            continue
        info['n_validas'][m] += 1
        carga[m][d] = carga[m].get(d, 0.0) + v
        if metodo == 'training_load':
            fm = fontes[m].setdefault(d, {})
            fm[r['fonte']] = fm.get(r['fonte'], 0.0) + v
    return {'carga': carga, 'ausentes': ausentes, 'treino': treino, 'fontes': fontes, 'info': info}


def _vetor(pares, d0, n):
    out = [0.0] * n
    for k, v in pares:
        i = (date.fromisoformat(k) - d0).days
        if 0 <= i < n:
            out[i] += v
    return out


def _acum(v):
    out, s = [0.0], 0.0
    for x in v:
        s += x
        out.append(s)
    return out


def _quotas(janela_por_mod):
    """{mod: L} -> {mod: Q} ou None se o denominador nao for positivo."""
    den = sum(janela_por_mod.values())
    if not (den > 0) or not math.isfinite(den):
        return None
    return {m: 100.0 * janela_por_mod[m] / den for m in janela_por_mod}


def _acumulados(recs, metodo, d0, n, so_ambos=False):
    """Somas acumuladas por dia (indice 0 = d0): carga por modalidade, elegiveis, validas, por motivo."""
    sel = [r for r in recs if not so_ambos or (r['training_load'] is not None and r['trimp'] is not None)]
    out = {'carga': {m: _acum(_vetor(((r['dia'], r[metodo]) for r in sel
                                      if r['mod'] == m and r[metodo] is not None), d0, n)) for m in MODALIDADES},
           'eleg': _acum(_vetor(((r['dia'], 1.0) for r in sel), d0, n)),
           'val': _acum(_vetor(((r['dia'], 1.0) for r in sel if r[metodo] is not None), d0, n))}
    out['motivo'] = {k: _acum(_vetor(((r['dia'], 1.0) for r in sel if r[metodo + '_motivo'] == k), d0, n))
                     for k in MOTIVOS}
    return out


def _janela(ac, i, janela):
    a, b = i + 1, i + 1 - janela
    L = {m: ac['carga'][m][a] - ac['carga'][m][b] for m in MODALIDADES}
    return L, int(round(ac['eleg'][a] - ac['eleg'][b])), int(round(ac['val'][a] - ac['val'][b]))


def _cobertura(recs, metodo, ini, fim):
    """Atividades elegiveis, validas e excluidas por motivo entre ini e fim (ISO, inclusivo), por modalidade."""
    def vazio():
        return {'elegiveis': 0, 'validas': 0, 'sem_dados': {k: 0 for k in MOTIVOS}}
    out = {m: vazio() for m in MODALIDADES}
    out['total'] = vazio()
    for r in recs:
        if not (ini <= r['dia'] <= fim):
            continue
        for alvo in (out[r['mod']], out['total']):
            alvo['elegiveis'] += 1
            if r[metodo] is not None:
                alvo['validas'] += 1
            else:
                alvo['sem_dados'][r[metodo + '_motivo']] += 1
    return out


def _parametros_trimp(recs, ini, fim):
    """FC maxima usada pelo Intervals.icu no TRIMP, por modalidade (so atividades com TRIMP valido)."""
    out = {}
    for m in MODALIDADES:
        c = {}
        for r in recs:
            if r['mod'] == m and r['trimp'] is not None and ini <= r['dia'] <= fim and r['fc_max'] is not None:
                c[int(r['fc_max'])] = c.get(int(r['fc_max']), 0) + 1
        out[m] = {str(k): v for k, v in sorted(c.items())}
    return out


def _estado(q, eleg, val):
    if q is None:
        return 'carga_ausente' if eleg > val else 'sem_treino'
    return 'ok_com_ausentes' if eleg > val else 'ok'


def distribuicao(atividades, janela=JANELA_PADRAO, ate=None, dias=HORIZONTE_PADRAO, metodo=METODO_PADRAO):
    """Serie diaria da distribuicao da carga na janela movel, pelo metodo escolhido, mais a comparacao
    entre os dois metodos nas mesmas janelas. Nunca escreve; nao le a BD.

    janela: 7, 28, 42 ou 90 dias. ate: ultimo dia de referencia (ISO); por omissao, a ultima atividade
    elegivel. dias: comprimento maximo da serie (os mais recentes). metodo: 'training_load' ou 'trimp'."""
    if janela not in JANELAS:
        raise ValueError(f'janela invalida: {janela} (use {JANELAS})')
    if metodo not in METODOS:
        raise ValueError(f'metodo invalido: {metodo!r} (use {METODOS})')
    if not isinstance(dias, int) or isinstance(dias, bool) or not (1 <= dias <= HORIZONTE_MAX):
        raise ValueError(f'dias invalido: {dias}')
    if ate is not None:
        date.fromisoformat(ate)
    recs, info = classificar(atividades)
    base = {'experimental': True, 'tipo_analise': TIPO_ANALISE, 'metodo': metodo, 'rotulo_metodo': ROTULOS[metodo],
            'metodos_disponiveis': list(METODOS), 'rotulos_metodos': dict(ROTULOS),
            'aviso': AVISOS[metodo], 'unidade': UNIDADES[metodo], 'definicao_metodo': DEFINICOES[metodo],
            'unidades': dict(UNIDADES), 'definicoes': dict(DEFINICOES),
            'modalidades': list(MODALIDADES), 'cores': {m: config.CORES_MOD[m] for m in MODALIDADES},
            'janela_dias': janela, 'janelas_disponiveis': list(JANELAS),
            'semantica_janela': ('[t-W+1, t]: inclui o dia t; numeradores e denominador usam os mesmos '
                                 'dias; so janelas completas; as duas metricas usam as mesmas janelas'),
            'formula': 'Q_m(t) = 100 * L_m(t) / (L_Bike + L_Row + L_Run + L_Ski)(t)',
            'exclusoes': ('Apenas Bike, Row, Run e Ski. Qualquer outra atividade (incluindo musculacao) '
                          'fica fora do numerador, do denominador, das coberturas e das series, em ambos os metodos.'),
            'info': {**info, 'n_elegiveis': len(recs),
                     'n_validas': {m: sum(1 for r in recs if r['mod'] == m and r[metodo] is not None)
                                   for m in MODALIDADES},
                     'n_sem_carga': {m: sum(1 for r in recs if r['mod'] == m and r[metodo] is None)
                                     for m in MODALIDADES}}}
    if not recs:
        return {**base, 'estado': 'sem_atividades',
                'leitura': 'nao ha atividades de Bike, Row, Run ou Ski com data na base'}
    datas_ativ = [r['dia'] for r in recs]
    primeira, ultima = min(datas_ativ), max(datas_ativ)
    fim = ate or ultima
    fim_d = date.fromisoformat(fim)
    primeira_janela = date.fromisoformat(primeira) + timedelta(days=janela - 1)
    if fim_d < primeira_janela:
        return {**base, 'estado': 'janela_incompleta', 'primeira_atividade': primeira,
                'ultima_atividade': ultima,
                'leitura': f'os dados disponiveis nao cobrem uma janela completa de {janela} dias'}
    ini_d = max(primeira_janela, fim_d - timedelta(days=dias - 1))
    d0 = ini_d - timedelta(days=janela - 1)
    n = (fim_d - d0).days + 1
    ac = {m: _acumulados(recs, m, d0, n) for m in METODOS}
    ac_amb = {m: _acumulados(recs, m, d0, n, so_ambos=True) for m in METODOS}

    datas = []
    S = {m: {'total': [], 'estado': [], 'eleg': [], 'val': [], 'carga': {k: [] for k in MODALIDADES},
             'pct': {k: [] for k in MODALIDADES}} for m in METODOS}
    dif = {k: [] for k in MODALIDADES}
    dif_amb = {k: [] for k in MODALIDADES}
    dif_max, dif_max_amb, val_amb = [], [], []
    for i in range(janela - 1, n):
        datas.append((d0 + timedelta(days=i)).isoformat())
        Q = {}
        for met in METODOS:
            L, eleg, val = _janela(ac[met], i, janela)
            q = _quotas(L)
            Q[met] = q
            s = S[met]
            s['total'].append(round(sum(L.values()), 3))
            s['eleg'].append(eleg)
            s['val'].append(val)
            s['estado'].append(_estado(q, eleg, val))
            for k in MODALIDADES:
                s['carga'][k].append(round(L[k], 3))
                s['pct'][k].append(None if q is None else round(q[k], 4))
        Qa = {}
        for met in METODOS:
            La, _, va = _janela(ac_amb[met], i, janela)
            Qa[met] = _quotas(La)
        val_amb.append(va)
        for k in MODALIDADES:
            dif[k].append(None if None in (Q['training_load'], Q['trimp'])
                          else round(Q['trimp'][k] - Q['training_load'][k], 3))
            dif_amb[k].append(None if None in (Qa['training_load'], Qa['trimp'])
                              else round(Qa['trimp'][k] - Qa['training_load'][k], 3))
        dif_max.append(None if dif['Bike'][-1] is None else round(max(abs(dif[k][-1]) for k in MODALIDADES), 2))
        dif_max_amb.append(None if dif_amb['Bike'][-1] is None
                           else round(max(abs(dif_amb[k][-1]) for k in MODALIDADES), 2))

    sel = S[metodo]
    serie = {'datas': datas, 'total': sel['total'], 'estado': sel['estado'], 'carga': sel['carga'], 'pct': sel['pct'],
             'n_elegiveis_janela': sel['eleg'], 'n_validas_janela': sel['val'],
             'n_ausentes_janela': [e - v for e, v in zip(sel['eleg'], sel['val'])],
             'dif_max_pp_entre_metodos': dif_max}
    ref_ini = (fim_d - timedelta(days=janela - 1)).isoformat()
    cob_ult = {m: _cobertura(recs, m, ref_ini, fim) for m in METODOS}
    cob_per = {m: _cobertura(recs, m, d0.isoformat(), fim) for m in METODOS}
    comparacao = {
        'aviso': AVISO_COMPARACAO, 'sentido_da_diferenca': 'TRIMP menos Training Load, em pontos percentuais',
        'datas': datas,
        'pct': {m: S[m]['pct'] for m in METODOS}, 'carga_absoluta': {m: S[m]['carga'] for m in METODOS},
        'total_absoluto': {m: S[m]['total'] for m in METODOS}, 'estado': {m: S[m]['estado'] for m in METODOS},
        'n_elegiveis_janela': S['training_load']['eleg'],
        'n_validas_janela': {m: S[m]['val'] for m in METODOS}, 'n_validas_ambos_janela': val_amb,
        'dif_pp': dif, 'dif_max_pp': dif_max,
        'controlo_mesmas_atividades': {
            'descricao': ('mesma diferenca calculada so com as atividades que tem Training Load e TRIMP validos: '
                          'separa o efeito da metrica do efeito da cobertura'),
            'dif_pp': dif_amb, 'dif_max_pp': dif_max_amb},
        'cobertura_ultima_janela': cob_ult, 'cobertura_periodo': cob_per,
        'periodo_cobertura': {'ini': d0.isoformat(), 'fim': fim, 'ultima_janela_ini': ref_ini}}
    fonte = {}
    for m in MODALIDADES:
        tot, por = 0.0, {}
        for r in recs:
            if r['mod'] == m and r['training_load'] is not None and d0.isoformat() <= r['dia'] <= fim:
                por[r['fonte']] = por.get(r['fonte'], 0.0) + r['training_load']
                tot += r['training_load']
        fonte[m] = {f: round(100.0 * v / tot, 1) for f, v in sorted(por.items())} if tot > 0 else {}
    return {**base, 'estado': 'ok', 'primeira_atividade': primeira, 'ultima_atividade': ultima,
            'data_ini': datas[0], 'data_fim': datas[-1], 'fim_explicito': ate is not None,
            'serie': serie, 'comparacao': comparacao,
            'cobertura': cob_ult[metodo], 'cobertura_periodo': cob_per[metodo],
            'fonte_da_carga_pct': fonte,
            'parametros_trimp': {'fc_max_usada_por_modalidade': _parametros_trimp(recs, d0.isoformat(), fim),
                                 'nota': ('FC max e FC repouso vem do Intervals.icu (FC max por desporto, repouso do dia); '
                                          'o TRIMP da mesma FC pesa de forma diferente entre modalidades se a FC max difere')}}
