"""Observacoes MMP detetadas automaticamente (experimental, local).

O que este modulo FAZ
---------------------
Deteta, a partir das curvas de potencia por atividade (power_curves), esforcos
que chegam perto de um maximo relevante, e planeia o registo versionado dessas
observacoes na tabela `modelo_observacoes_auto` (so INSERT, como o registo de
revisoes). Nao confirma nada a mao: a deteccao automatica e a elegibilidade
cientifica sao coisas distintas, e nenhuma delas diz que o modelo esta validado.

Tres referencias (limiar unico, o ja existente: protocolo.LIMIAR_ESFORCO = 0.92)
--------------------------------------------------------------------------------
  recente   melhor dos ultimos protocolo.JANELA_MELHOR dias ANTERIORES ao esforco, com a
            regra existente de >= 3 pontos na janela contando o esforco (isto e, >= 2
            anteriores). Da o mesmo conjunto de deteccoes que protocolo.detectar_testes
            (ha um teste de paridade).
  season    melhor da season da atividade, so entre pontos ANTERIORES (causal).
  all_time  melhor de TODAS as curvas sincronizadas ANTERIORES (causal).
            Limitado a janela historica sincronizada (config.ANOS_HISTORICO).
NENHUMA referencia inclui o proprio esforco: ele nunca se classifica contra si mesmo.
Um esforco e detetado se atinge >= 0.92 de PELO MENOS UMA referencia. Um novo maximo
da season abaixo do all-time continua detetado, mas so se houver um ponto anterior da
season (pct > 100% do maximo anterior da season). O primeiro esforco de uma season, sem
pontos anteriores nela, so se qualifica pelo all-time (ou recente) se os atingir.
Sem NENHUM ponto anterior (primeiro ponto valido da serie) nao ha referencia: grava-se
com estado 'sem_referencia_anterior' (referencia_tipo NULL) e o estimador/validacao
excluem-no ('sem_referencia_anterior'). Isto nao e um limiar novo.
O 0.92 aplicado a season e a all-time e EXPERIMENTAL (autorizado para esta
implementacao); o 0.92 do "recente" e o existente. Mantem-se a regra de amostra ja
existente (>= 5 pontos por modalidade e duracao); nao se introduz nenhum minimo novo,
por isso `n_pontos` (pontos ANTERIORES usados) de cada referencia fica registado para auditoria.

Regras de integridade
---------------------
  * icu_ignore_power=true: a atividade NAO entra no calculo de nenhuma referencia.
    O seu proprio esforco pode ser detetado e mostrado, mas fica excluido
    cientificamente (elegibilidade 'inelegivel').
  * Potencia valida = numero real, finito, positivo (nao bool, nao NaN/inf, > 0).
  * Referencias CAUSAIS: so pontos anteriores (ou o proprio) na ordem (data, activity_id).
  * Nao existe curva de season nem all-time guardada: ambas sao derivadas das curvas por
    atividade. As fronteiras de season vem do calendario (SEASON_START) ou, na falta
    deles, de SEASON_INICIO_MES; a fonte fica registada.

Versionamento (INSERT-only)
---------------------------
Identidade = test_id (modalidade:duracao_s:activity_id, a mesma do registo manual).
Cada versao e uma linha (test_id, versao). Mesmo valor e mesma data -> nada se grava.
Valor ou data diferentes -> nova linha (substitui_versao = anterior); a anterior fica
guardada e passa a 'substituida' (estado derivado da cadeia, nao atualizado em linha).
Se o esforco deixa de ser detetado grava-se uma linha 'retirada'. A observacao atual e a
ultima linha, se for 'detetada'.

`detetado_em` e o momento REAL em que a observacao foi gravada. Nao ha preenchimento
retroativo: para o modo `conhecido_na_epoca` uma versao so e "conhecida" desde a sua
primeira prova verificavel (detetado_em, ou o `revisado_em` de uma revisao manual do mesmo
valor).

Modulo puro: sem base de dados, sem Flask, sem rede, sem relogio.
"""
import math
import re
from datetime import datetime, timedelta

import modelo_testes as mt
import protocolo

VERSAO_CRITERIO = 2                                   # 2: referencias SEM o proprio esforco (1: circular)
LIMIAR_PROXIMIDADE = protocolo.LIMIAR_ESFORCO        # 0.92: existente (recente); experimental (season, all_time)
JANELA_RECENTE = protocolo.JANELA_MELHOR              # 180 dias
MIN_PONTOS_SERIE = 5                                  # regra existente de protocolo.detectar_testes
MIN_PONTOS_JANELA = 3                                 # idem, so para a referencia 'recente' (conta o esforco avaliado)
MIN_ANTERIORES_JANELA = MIN_PONTOS_JANELA - 1         # = 2 pontos ANTERIORES na janela: a mesma regra, sem contar o proprio
SEM_REFERENCIA = 'sem_referencia_anterior'            # estado: nenhum ponto anterior com que comparar
COM_REFERENCIA = 'com_referencia'

REF_RECENTE = 'recente'
REF_SEASON = 'season'
REF_ALL_TIME = 'all_time'
REFERENCIAS = (REF_ALL_TIME, REF_SEASON, REF_RECENTE)  # prioridade da referencia "usada"

EVENTO_DETETADA = 'detetada'
EVENTO_RETIRADA = 'retirada'
EVENTOS = (EVENTO_DETETADA, EVENTO_RETIRADA)

ESTADO_ATUAL = 'atual'
ESTADO_SUBSTITUIDA = 'substituida'
ESTADO_RETIRADA = 'retirada'

ORIGENS = ('sync', 'manual', 'script')

COLUNAS = ['test_id', 'versao', 'evento', 'substitui_versao', 'modalidade', 'duracao_s',
           'activity_id', 'data', 'hora_local', 'watts', 'referencia_tipo', 'referencia_watts',
           'pct_da_referencia', 'referencias', 'season', 'elegibilidade', 'motivo_exclusao',
           'icu_ignore_power', 'ignore_parts_presente', 'tags', 'criterio', 'origem',
           'motivo_retirada', 'versao_contrato', 'detetado_em']


# ── potencia e datas ───────────────────────────────────────────────────────

def potencia_valida(v):
    """Numero real, finito e positivo. bool, NaN, infinitos, zero e negativos nao."""
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and v > 0)


def _dia(v):
    return str(v)[:10] if v is not None else None


def hora_valida(valor, data):
    """'HH:MM:SS' (hora local REAL da atividade) ou None.

    So aceita um timestamp ISO com hora cujo dia coincide com `data` (o dia da curva). Sem
    hora, com texto invalido ou de outro dia -> None: a ordem dentro do dia nao se conhece.
    """
    if not isinstance(valor, str) or len(valor) < 16 or valor[10] not in 'T ':
        return None
    if valor[:10] != str(data)[:10]:
        return None
    try:
        h = datetime.strptime(valor[11:19] if len(valor) >= 19 else valor[11:16] + ':00', '%H:%M:%S')
    except ValueError:
        return None
    return h.strftime('%H:%M:%S')


def _data(s):
    try:
        return datetime.strptime(str(s)[:10], '%Y-%m-%d')
    except (TypeError, ValueError):
        return None


# ── tags do atleta (tempo ou distancia) ────────────────────────────────────

_RE_TAG = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(km|min|seg|sec|s|m)\s*$", re.I)


def analisar_tag(texto):
    """Interpreta uma tag como tempo ('5min', '20 min', '90s') ou distancia ('2km',
    '5km', '500m'). Devolve None se nao for uma tag desse tipo. Nao inventa nada:
    uma distancia NAO e convertida em duracao (nao ha ritmo nem velocidade aqui)."""
    if not isinstance(texto, str):
        return None
    m = _RE_TAG.match(texto)
    if not m:
        return None
    try:
        valor = float(m.group(1).replace(',', '.'))
    except ValueError:
        return None
    if not (math.isfinite(valor) and valor > 0):
        return None
    un = m.group(2).lower()
    if un == 'min':
        return {'texto': texto.strip(), 'tipo': 'tempo', 'valor': valor, 'unidade': 'min',
                'segundos': valor * 60.0}
    if un in ('s', 'seg', 'sec'):
        return {'texto': texto.strip(), 'tipo': 'tempo', 'valor': valor, 'unidade': 's',
                'segundos': valor}
    metros = valor * 1000.0 if un == 'km' else valor
    return {'texto': texto.strip(), 'tipo': 'distancia', 'valor': valor,
            'unidade': 'km' if un == 'km' else 'm', 'metros': metros}


def tags_da_atividade(atividade):
    """Lista de tags (texto) de uma atividade da API. Aceita lista ou texto separado por
    virgulas; vazio/ausente -> []."""
    if not isinstance(atividade, dict):
        return []
    tt = atividade.get('tags')
    if isinstance(tt, str):
        itens = [x.strip() for x in tt.split(',')]
    elif isinstance(tt, (list, tuple)):
        itens = [str(x).strip() for x in tt if x is not None]
    else:
        itens = []
    return [x for x in itens if x]


def tags_de_teste(tags):
    """So as tags que descrevem tempo ou distancia, ja interpretadas."""
    return [p for p in (analisar_tag(t) for t in (tags or [])) if p]


def tag_coincide_duracao(tags_teste, duracao_s):
    """True se alguma tag de TEMPO igual a duracao do teste (ex.: '5min' e 300 s). Uma tag
    de distancia nunca coincide (nao e convertida). Informativo: nao muda a deteccao nem a
    elegibilidade."""
    return any(t['tipo'] == 'tempo' and abs(t['segundos'] - duracao_s) < 1e-6
               for t in (tags_teste or []))


# ── curvas ─────────────────────────────────────────────────────────────────

def limpar_curvas(curvas):
    """Copia das curvas so com as duracoes do protocolo e potencias validas.
    Curvas sem data valida ou sem id sao descartadas. Nao altera o argumento."""
    out = []
    for c in curvas or []:
        if not isinstance(c, dict) or c.get('activity_id') is None or _data(c.get('date')) is None:
            continue
        secs, watts = [], []
        for s, w in zip(c.get('secs') or [], c.get('watts') or []):
            if s in protocolo.TESTES and potencia_valida(w):
                secs.append(s)
                watts.append(float(w))
        out.append({'activity_id': c['activity_id'], 'type': c.get('type'),
                    'date': str(c['date'])[:10], 'secs': secs, 'watts': watts})
    return out


def _ref(tipo, melhor, n, w, extra=None):
    """melhor: o melhor ponto ANTERIOR; n: quantos pontos anteriores formam a referencia.
    'e_maximo' = o esforco iguala ou supera o maximo anterior."""
    pct = w / melhor['watts'] if melhor else None
    d = {'tipo': tipo, 'referencia_watts': melhor['watts'] if melhor else None,
         'referencia_activity_id': melhor['activity_id'] if melhor else None,
         'referencia_data': melhor['date'] if melhor else None,
         'pct': round(pct * 100, 1) if pct is not None else None,
         'qualifica': bool(pct is not None and pct >= LIMIAR_PROXIMIDADE),
         'e_maximo': bool(pct is not None and pct >= 1.0),
         'n_pontos': n}
    if extra:
        d.update(extra)
    return d


def _sem_referencia(mod, secs, p, n_serie, ambiguos=0):
    """Esforco sem nenhum ponto anterior valido: nao ha com que o comparar."""
    return {'test_id': mt.id_teste(mod, secs, p['activity_id']),
            'modalidade': mod, 'duracao_s': int(secs),
            'activity_id': p['activity_id'], 'data': p['date'], 'watts': p['watts'],
            'referencia_tipo': None, 'referencia_watts': None, 'pct_da_referencia': None,
            'referencias': {}, 'season': p['season'],
            'ignorada_na_referencia': p['ignorada'],
            'estado_referencia': SEM_REFERENCIA, 'ambiguos_mesmo_dia': ambiguos,
            'hora_local_real': p['hora'], 'n_pontos_serie': n_serie}


def detetar(curvas, ignorar_ids=(), season_de=None, limiar=None, janela=None, horas=None):
    """Esforcos detetados, com as tres referencias, por modalidade e duracao do protocolo.

    curvas: db.load_power_curves(). ignorar_ids: activity_id com icu_ignore_power=true
    (nao entram em nenhuma referencia). season_de: callable(data) -> etiqueta da season
    (por omissao, ano civil). Devolve lista de candidatos ordenada; cada um com
    'referencias' (as tres), 'referencia_tipo' (a mais exigente que qualifica, por ordem
    all_time > season > recente) e 'pct_da_referencia'. Cada referencia usa so pontos
    ANTERIORES ao esforco.

    Ordem: so se ordena o que os dados provam. Dias diferentes -> pela data. Mesmo dia -> so
    se AMBAS as atividades tem hora local real (horas: {activity_id: 'YYYY-MM-DDTHH:MM:SS',
    de activities.start_date_local}) e as horas diferem; o mais cedo e anterior. Sem hora
    numa delas, ou horas iguais, a ordem e desconhecida: nenhum dos dois serve de referencia
    ao outro (activity_id NAO desempata). 'ambiguos_mesmo_dia' conta os pontos assim postos de parte. Um esforco sem nenhum ponto anterior sai com
    'estado_referencia' = 'sem_referencia_anterior' e referencia_tipo None.
    """
    limiar = LIMIAR_PROXIMIDADE if limiar is None else limiar
    janela = JANELA_RECENTE if janela is None else janela
    ignorar = set(ignorar_ids or ())
    season_de = season_de or (lambda d: str(d)[:4])
    horas = horas or {}

    por_mod = {}
    for c in limpar_curvas(curvas):
        por_mod.setdefault(c['type'], []).append(c)

    out = []
    for mod, lista in por_mod.items():
        for secs, info in protocolo.TESTES.items():
            pontos = []
            for c in lista:
                for s, w in zip(c['secs'], c['watts']):
                    if s == secs:
                        pontos.append({'date': c['date'], 'watts': w,
                                       'activity_id': c['activity_id'],
                                       'hora': hora_valida(horas.get(c['activity_id']), c['date']),
                                       'ignorada': c['activity_id'] in ignorar})
                        break
            pontos.sort(key=lambda p: (p['date'], p['hora'] or '', str(p['activity_id'])))   # so determinismo da saida
            validos = [p for p in pontos if not p['ignorada']]
            if len(validos) < MIN_PONTOS_SERIE:
                continue
            for p in pontos:
                p['season'] = season_de(p['date'])
            inicio_dia = {}
            for i, p in enumerate(pontos):
                inicio_dia.setdefault(p['date'], i)
            for i, p in enumerate(pontos):
                # referencias causais: so pontos NAO ignorados e PROVADAMENTE anteriores:
                # de dias anteriores, ou do mesmo dia com hora real estritamente mais cedo.
                # O proprio esforco nunca entra na sua referencia, nem um do mesmo dia de
                # ordem desconhecida.
                ini = inicio_dia[p['date']]
                mesmo_dia = [q for q in pontos[ini:] if q['date'] == p['date'] and q is not p
                             and not q['ignorada']]
                anteriores_dia = [q for q in mesmo_dia if p['hora'] and q['hora'] and q['hora'] < p['hora']]
                ambiguos = sum(1 for q in mesmo_dia if not (p['hora'] and q['hora'])
                               or q['hora'] == p['hora'])
                R = [q for q in pontos[:ini] if not q['ignorada']] + anteriores_dia
                if not R:
                    out.append(_sem_referencia(mod, secs, p, len(validos), ambiguos))
                    continue
                limite = (_data(p['date']) - timedelta(days=janela)).strftime('%Y-%m-%d')
                na_janela = [q for q in R if q['date'] >= limite]
                refs = {}
                if len(na_janela) >= MIN_ANTERIORES_JANELA:
                    refs[REF_RECENTE] = _ref(REF_RECENTE, max(na_janela, key=lambda q: q['watts']),
                                             len(na_janela), p['watts'], {'janela_dias': janela})
                da_season = [q for q in R if q['season'] == p['season']]
                if da_season:
                    refs[REF_SEASON] = _ref(REF_SEASON, max(da_season, key=lambda q: q['watts']),
                                            len(da_season), p['watts'], {'season': p['season']})
                refs[REF_ALL_TIME] = _ref(REF_ALL_TIME, max(R, key=lambda q: q['watts']),
                                          len(R), p['watts'])
                for r in refs.values():
                    r['qualifica'] = bool(r['pct'] is not None and
                                          p['watts'] / r['referencia_watts'] >= limiar)
                escolhida = next((refs[t] for t in REFERENCIAS
                                  if t in refs and refs[t]['qualifica']), None)
                if escolhida is None:
                    continue
                out.append({
                    'test_id': mt.id_teste(mod, secs, p['activity_id']),
                    'modalidade': mod, 'duracao_s': int(secs),
                    'activity_id': p['activity_id'], 'data': p['date'], 'watts': p['watts'],
                    'referencia_tipo': escolhida['tipo'],
                    'referencia_watts': escolhida['referencia_watts'],
                    'pct_da_referencia': escolhida['pct'],
                    'referencias': refs, 'season': p['season'],
                    'ignorada_na_referencia': p['ignorada'],
                    'estado_referencia': COM_REFERENCIA, 'ambiguos_mesmo_dia': ambiguos,
                    'hora_local_real': p['hora'],
                    'n_pontos_serie': len(validos),
                })
    out.sort(key=lambda c: (c['modalidade'], c['duracao_s'], c['data'], c['hora_local_real'] or '', str(c['activity_id'])))
    return out


# ── historico versionado ───────────────────────────────────────────────────

def por_teste(historico):
    """{test_id: [linhas por versao crescente]}."""
    d = {}
    for r in historico or []:
        d.setdefault(r.get('test_id'), []).append(r)
    for v in d.values():
        v.sort(key=lambda r: r['versao'])
    return d


def ultimas(historico):
    return {tid: v[-1] for tid, v in por_teste(historico).items()}


def atuais(historico):
    """Observacoes ATUAIS: ultima versao de cada test_id, se for 'detetada'.
    Versoes substituidas e retiradas nunca contam como observacao atual."""
    return [r for r in ultimas(historico).values() if r.get('evento') == EVENTO_DETETADA]


def estado_versao(linha, historico):
    """'atual' | 'substituida' | 'retirada' (derivado da cadeia; nada e reescrito)."""
    ult = ultimas(historico).get(linha['test_id'])
    if linha.get('evento') == EVENTO_RETIRADA:
        return ESTADO_RETIRADA
    if ult is not None and ult['versao'] == linha['versao']:
        return ESTADO_ATUAL
    return ESTADO_SUBSTITUIDA


def verificar_cadeia(historico):
    """Problemas de integridade da cadeia de versoes (lista vazia = ok)."""
    problemas = []
    for tid, v in por_teste(historico).items():
        nums = [r['versao'] for r in v]
        if nums != list(range(1, len(nums) + 1)):
            problemas.append(f'{tid}: versoes {nums} nao sao 1..{len(nums)}')
            continue
        for i, r in enumerate(v):
            esperado = None if i == 0 else v[i - 1]['versao']
            if r.get('substitui_versao') != esperado:
                problemas.append(f'{tid}#{r["versao"]}: substitui_versao incoerente')
            if r.get('evento') not in EVENTOS:
                problemas.append(f'{tid}#{r["versao"]}: evento invalido')
    return problemas


def _mesmo(a, b):
    return mt.mesmo_valor(a, b)


# ── planeamento do registo ─────────────────────────────────────────────────

def planear(candidatos, historico, agora, curvas_validas, elegibilidade_fn=None,
            tags_fn=None, hora_local_fn=None, criterio=None, origem='sync'):
    """Linhas novas a gravar (lista; vazia se nada mudou). Idempotente: o mesmo candidato
    com o mesmo valor e data nao gera nada.

    candidatos: saida de detetar(). historico: todas as linhas ja gravadas. agora: datetime
    UTC (e o `detetado_em`). curvas_validas: limpar_curvas(...) (para saber porque uma
    observacao deixou de ser detetada). elegibilidade_fn(activity_id) ->
    {'estado','motivo','icu_ignore_power','ignore_parts_presente'}.
    """
    ult = ultimas(historico)
    novas, vivos = [], set()
    tipo_da_atividade = {c['activity_id']: c['type'] for c in curvas_validas or []}
    pontos_validos = {(c['activity_id'], s) for c in curvas_validas or [] for s in c['secs']}

    for c in candidatos:
        if not (potencia_valida(c['watts']) and c['modalidade'] in mt.MODALIDADES
                and c['duracao_s'] in mt.DURACOES):
            continue
        tid = c['test_id']
        vivos.add(tid)
        ant = ult.get(tid)
        if (ant is not None and ant['evento'] == EVENTO_DETETADA
                and _mesmo(ant['watts'], c['watts']) and _dia(ant['data']) == c['data']
                and (ant.get('referencia_tipo') is None) == (c.get('referencia_tipo') is None)):
            continue
        eleg = (elegibilidade_fn(c['activity_id']) if elegibilidade_fn
                else {'estado': 'desconhecida', 'motivo': 'sem_elegibilidade',
                      'icu_ignore_power': None, 'ignore_parts_presente': None})
        tags = list(tags_fn(c['activity_id'])) if tags_fn else []
        tt = tags_de_teste(tags)
        novas.append({
            'test_id': tid, 'versao': (ant['versao'] + 1) if ant else 1,
            'evento': EVENTO_DETETADA,
            'substitui_versao': ant['versao'] if ant else None,
            'modalidade': c['modalidade'], 'duracao_s': c['duracao_s'],
            'activity_id': c['activity_id'], 'data': c['data'],
            'hora_local': hora_local_fn(c['activity_id']) if hora_local_fn else None,
            'watts': c['watts'], 'referencia_tipo': c['referencia_tipo'],
            'referencia_watts': c['referencia_watts'],
            'pct_da_referencia': c['pct_da_referencia'],
            'referencias': c['referencias'], 'season': c.get('season'),
            'elegibilidade': eleg['estado'],
            'motivo_exclusao': None if eleg['estado'] == 'elegivel' else eleg.get('motivo'),
            'icu_ignore_power': eleg.get('icu_ignore_power'),
            'ignore_parts_presente': eleg.get('ignore_parts_presente'),
            'tags': {'todas': tags, 'tempo_ou_distancia': tt,
                     'coincide_duracao': tag_coincide_duracao(tt, c['duracao_s'])},
            'criterio': {**dict(criterio or {}), 'ordem_mesmo_dia': {
                'ambiguos': c.get('ambiguos_mesmo_dia', 0), 'hora_local_real': c.get('hora_local_real'),
                'regra': 'so a hora local real ordena atividades do mesmo dia; sem hora ou com hora igual a ordem e desconhecida e nenhuma serve de referencia a outra'}},
            'origem': origem,
            'motivo_retirada': None, 'versao_contrato': VERSAO_CRITERIO,
            'detetado_em': agora,
        })

    for tid, ant in sorted(ult.items()):
        if ant['evento'] != EVENTO_DETETADA or tid in vivos:
            continue
        aid = ant['activity_id']
        if aid not in tipo_da_atividade:
            continue                       # curva ausente: nao se retira (pode ser sync parcial)
        if tipo_da_atividade[aid] != ant['modalidade']:
            motivo = 'modalidade_alterada'
        elif (aid, ant['duracao_s']) not in pontos_validos:
            motivo = 'potencia_invalida_ou_ausente'
        else:
            motivo = 'deixou_de_ser_detetada'
        novas.append({**{k: ant.get(k) for k in COLUNAS},
                      'versao': ant['versao'] + 1, 'evento': EVENTO_RETIRADA,
                      'substitui_versao': ant['versao'], 'origem': origem,
                      'motivo_retirada': motivo, 'detetado_em': agora})
    return novas


# ── "conhecido na epoca" ───────────────────────────────────────────────────

def conhecida_desde(linha, revisoes_do_teste=()):
    """Primeiro dia (YYYY-MM-DD) em que ha prova verificavel desta versao: o seu
    `detetado_em` ou o `revisado_em` de uma revisao manual do MESMO valor. Nunca
    `updated_at` da curva (sobrescrito a cada sincronizacao). Sem nenhuma, None."""
    dias = [_dia(linha.get('detetado_em'))]
    for r in revisoes_do_teste or ():
        if _mesmo(r.get('watts'), linha.get('watts')):
            dias.append(_dia(r.get('revisado_em')))
    dias = [d for d in dias if d]
    return min(dias) if dias else None


class FonteAuto:
    """Observacoes automaticas (historico completo) + revisoes manuais (so para provar
    quando um valor ja era conhecido). Interface usada pelo estimador/validacao."""

    def __init__(self, historico_obs, revisoes=None):
        self.historico = list(historico_obs or [])
        self.revisoes = {}
        for r in revisoes or []:
            self.revisoes.setdefault(r.get('test_id'), []).append(r)

    def atuais(self):
        return atuais(self.historico)

    def conhecidas_antes(self, corte):
        """Observacoes cuja versao mais recente com prova verificavel ANTERIOR a `corte`
        (YYYY-MM-DD, estrito) era uma deteccao. Sem prova -> nunca conhecidas."""
        out = []
        for tid, versoes in por_teste(self.historico).items():
            escolhida = None
            for r in versoes:
                desde = conhecida_desde(r, self.revisoes.get(tid))
                if desde is not None and desde < corte:
                    escolhida = r
            if escolhida is not None and escolhida['evento'] == EVENTO_DETETADA:
                out.append(escolhida)
        return out

    def resumo_temporal(self, test_ids=None):
        """Quando cada observacao ATUAL passou a ser conhecida, face a data do seu esforco.

        'no_dia_ou_antes': detetada (ou provada por revisao manual do mesmo valor) no dia do
        esforco ou antes; 'depois': so conhecida mais tarde (nunca retroativa); 'sem_prova':
        sem data verificavel. Estado descritivo, sem limiares:
          sem_observacoes | sem_historico_temporal (nenhuma conhecida na epoca) | parcial | completo.
        """
        atuais_ = {o['test_id']: o for o in atuais(self.historico)}
        ids = sorted(atuais_ if test_ids is None else [t for t in test_ids if t in atuais_])
        no = depois = sem = 0
        datas = []
        for t in ids:
            o = atuais_[t]
            desde = conhecida_desde(o, self.revisoes.get(t))
            if desde is None:
                sem += 1
                continue
            datas.append(desde)
            if desde <= _dia(o['data']):
                no += 1
            else:
                depois += 1
        n = len(ids)
        if n == 0:
            estado = 'sem_observacoes'
        elif no == 0:
            estado = 'sem_historico_temporal'
        elif no == n:
            estado = 'completo'
        else:
            estado = 'parcial'
        return {'estado': estado, 'n_observacoes': n, 'n_conhecidas_no_dia_ou_antes': no,
                'n_conhecidas_so_depois': depois, 'n_sem_data_de_deteccao_verificavel': sem,
                'primeira_deteccao': min(datas) if datas else None,
                'ultima_deteccao': max(datas) if datas else None,
                'nota': NOTAS_TEMPORAIS[estado]}


NOTAS_TEMPORAIS = {
    'sem_observacoes': 'Sem observacoes automaticas.',
    'sem_historico_temporal': (
        'Nenhuma observacao foi detetada no dia do seu esforco: o historico anterior a ativacao da '
        'deteccao automatica nao pode ser reconstruido nem se atribuem datas de deteccao retroativas. '
        'A validacao "conhecido na epoca" ainda nao tem treino; so a retrospetiva tem.'),
    'parcial': (
        'So parte das observacoes foi detetada no dia do esforco. As restantes contam como conhecidas '
        'apenas desde a sua primeira deteccao (e as sem data verificavel, nunca).'),
    'completo': 'Todas as observacoes foram detetadas no dia do esforco ou antes.',
}
