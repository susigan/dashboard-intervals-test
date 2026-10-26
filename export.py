"""Exportacao dos dados em bruto, para analise fora do dashboard.

Devolve exactamente o que o dashboard usa — as mesmas colunas, os mesmos
filtros — para que qualquer analise feita por fora seja comparavel com a
que corre aqui.
"""

import csv
import io
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path


def _csv(linhas, colunas):
    """Lista de dicts -> texto CSV. Colunas em falta ficam vazias."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=colunas, extrasaction='ignore',
                       lineterminator='\n')
    w.writeheader()
    for r in linhas:
        w.writerow({c: r.get(c) for c in colunas})
    return buf.getvalue()


COLUNAS = {
    'atividades': ['id', 'date', 'type', 'type_raw', 'name', 'elapsed_time',
                   'moving_time', 'distance_m', 'kj', 'kj_acima_ftp',
                   'z1_kj', 'z2_kj', 'z3_kj', 'z1_sec', 'z2_sec', 'z3_sec',
                   'training_load', 'rpe', 'xss', 'aerobic', 'glycolytic',
                   'sprint', 'epoc', 'elevation', 'avg_hr', 'max_hr',
                   'avg_watts', 'ftp', 'source'],
    # hrv/hf_power vem do formulario (HRV4Training); os campos com sufixo
    # _icu vem da Intervals.icu, que sincroniza do relogio. Sao medicoes
    # diferentes e por isso ficam em colunas separadas — nunca fundidas.
    'wellness': ['date', 'hrv', 'rhr', 'sleep_hours', 'sleep_quality',
                 'stress', 'fatiga', 'humor', 'soreness', 'peso', 'fat',
                 'hf_power', 'doente', 'performance',
                 'hrv_icu', 'hrvSDNN_icu', 'rhr_icu', 'readiness_icu',
                 'sleep_secs_icu', 'sleep_score_icu', 'avg_sleeping_hr_icu',
                 'respiration_icu', 'spo2_icu', 'steps_icu',
                 'kcal_icu', 'carb_icu', 'protein_icu', 'fat_icu'],
    'corporal': ['date', 'peso', 'bf', 'calorias', 'carb', 'fat', 'ptn',
                 'net', 'carb_perc', 'fat_perc', 'ptn_perc'],
    'curvas': ['activity_id', 'date', 'type', 'weight', 'secs', 'watts'],
    'testes': ['modalidade', 'duracao_s', 'nome', 'date', 'activity_id',
               'watts', 'pct_do_melhor', 'melhor_recente'],
    'cp': ['activity_id', 'date', 'type', 'cp', 'w_prime', 'r2', 'n_pontos'],
    'serie_diaria': ['date', 'load', 'ctl', 'atl', 'tsb', 'ramp'],
}


def atividades(db):
    return _csv(db.actividades_processadas(), COLUNAS['atividades'])


# Campos do wellness da Intervals.icu -> nome na exportacao
MAPA_ICU = {
    'hrv': 'hrv_icu', 'hrvSDNN': 'hrvSDNN_icu', 'restingHR': 'rhr_icu',
    'readiness': 'readiness_icu', 'sleepSecs': 'sleep_secs_icu',
    'sleepScore': 'sleep_score_icu', 'avgSleepingHR': 'avg_sleeping_hr_icu',
    'respiration': 'respiration_icu', 'spO2': 'spo2_icu', 'steps': 'steps_icu',
    'kcalConsumed': 'kcal_icu', 'carbohydrates': 'carb_icu',
    'protein': 'protein_icu', 'fatTotal': 'fat_icu',
}


def wellness_icu(oldest='2021-01-01'):
    """Wellness da Intervals.icu, que sincroniza do Garmin.

    Traz campos que o formulario nao tem — hrvSDNN, sleepScore, readiness —
    e que sao medicoes independentes. Fica em colunas com sufixo _icu para
    nunca serem confundidas com as do formulario.
    """
    from api_client import icu_get, athlete_id_real
    from datetime import datetime as _dt
    try:
        aid = athlete_id_real()
        dados, err = icu_get(f'/athlete/{aid}/wellness',
                             params={'oldest': oldest,
                                     'newest': _dt.now().strftime('%Y-%m-%d')})
        if err or not isinstance(dados, list):
            return {}
    except Exception:
        return {}
    out = {}
    for d in dados:
        data = d.get('id')
        if not data:
            continue
        linha = {}
        for orig, novo in MAPA_ICU.items():
            v = d.get(orig)
            if isinstance(v, (int, float)):
                linha[novo] = v
        if linha:
            out[data] = linha
    return out


def wellness(sheets):
    """Formulario + Intervals.icu, lado a lado.

    Nao se funde nada: cada fonte fica na sua coluna, para que a analise
    possa escolher e comparar.
    """
    # sheets.carregar() usa a cache; carregar_wellness() iria sempre a rede
    w, _c, _e = sheets.carregar()
    w = list(w or [])
    icu = wellness_icu()
    if icu:
        por_data = {r['date']: r for r in w}
        for data, extra in icu.items():
            if data in por_data:
                por_data[data].update(extra)
            else:
                por_data[data] = {'date': data, **extra}
        w = [por_data[k] for k in sorted(por_data)]
    return _csv(w, COLUNAS['wellness'])


def corporal(sheets):
    _w, c, _e = sheets.carregar()
    return _csv(c or [], COLUNAS['corporal'])


def curvas(db, tipo=None, formato='longo'):
    """Curvas de potencia.

    formato='longo'  uma linha por (sessao, duracao) — melhor para pandas
    formato='json'   secs e watts como listas, como estao na base
    """
    cs = db.load_power_curves(tipo) or []
    if formato == 'json':
        return json.dumps(cs, ensure_ascii=False, indent=1)
    linhas = []
    for c in cs:
        for s, w in zip(c.get('secs') or [], c.get('watts') or []):
            if isinstance(w, (int, float)) and w > 0:
                linhas.append({'activity_id': c['activity_id'],
                               'date': c['date'], 'type': c['type'],
                               'weight': c.get('weight'),
                               'secs': s, 'watts': w})
    return _csv(linhas, COLUNAS['curvas'])


def testes_maximos(db, protocolo):
    """Esforcos maximos detectados — a ancora de performance."""
    det = protocolo.detectar_testes(db.load_power_curves() or [])
    linhas = []
    for mod, durs in det.items():
        for secs, d in durs.items():
            for t in d.get('testes', []):
                linhas.append({'modalidade': mod, 'duracao_s': secs,
                               'nome': d['nome'], **t})
    linhas.sort(key=lambda r: (r['modalidade'], r['duracao_s'], r['date']))
    return _csv(linhas, COLUNAS['testes'])


def cp_ajustado(db):
    """CP e W' por sessao, do ajuste P(t)=W'/t+CP."""
    return _csv(db.cp_por_sessao() or [], COLUNAS['cp'])


CAMPOS_CP_FALLBACK = ['id', 'start_date_local', 'icu_pm_cp',
                      'icu_rolling_ftp', 'icu_pm_ftp', 'icu_pm_w_prime']


class SemDados(Exception):
    """Sem fonte bruta acessivel. Recusa-se a exportacao em vez de estimar."""


def cp_fallback_linhas(acts, curva_ids):
    """Sessoes sem curva aceita, com os campos brutos da cadeia ANTIGA.

    Export de auditoria: reproduz a cadeia anterior a 7fee86e (icu_rolling_ftp
    incluido), para inspeccionar as sessoes que deixaram de ter CP no PMC.
    NAO e a cadeia atual de tabs/tab_pmc.api_data, que usa so icu_pm_cp e
    icu_pm_ftp (cp_bruto_da_atividade).

    Data com 10 caracteres,
    cp = num(icu_pm_cp) or num(icu_rolling_ftp) or num(icu_pm_ftp) (truthy),
    e id fora de curva_ids (curvas com r2 >= 0.80). Os valores sao devolvidos
    tal como estao no raw: None continua None, 0 continua 0.
    """
    from api_client import num
    linhas = []
    for a in acts:
        if len((a.get('start_date_local') or '')[:10]) != 10:
            continue
        cp = (num(a.get('icu_pm_cp')) or num(a.get('icu_rolling_ftp'))
              or num(a.get('icu_pm_ftp')) or None)
        if not cp or a.get('id') in curva_ids:
            continue
        linhas.append({c: a.get(c) for c in CAMPOS_CP_FALLBACK})
    return linhas


def cp_fallback(db):
    """(texto_csv, resumo) das sessoes com CP de fallback, a partir de activities.raw.

    Levanta SemDados se a base estiver desligada, vazia, ou se a curva de CP
    nao puder ser lida. Nao chama a API externa.
    """
    acts = db.load_activities()
    if not acts:
        raise SemDados('base de dados indisponivel ou sem actividades; '
                       'cp_fallback.csv precisa de activities.raw')
    try:
        curva_ids = {r['activity_id'] for r in (db.cp_por_sessao() or [])
                     if r['r2'] >= 0.80}
    except Exception as e:
        raise SemDados(f'curva de CP indisponivel: {type(e).__name__}: {e}')
    linhas = cp_fallback_linhas(acts, curva_ids)
    resumo = {
        'registros': len(linhas),
        'ids_distintos': len({r['id'] for r in linhas}),
        'preenchidos': {c: sum(1 for r in linhas if r[c] not in (None, ''))
                        for c in CAMPOS_CP_FALLBACK},
    }
    return _csv(linhas, CAMPOS_CP_FALLBACK), resumo


# Mesmo limiar que tabs/tab_pmc.api_data usa para aceitar a curva de CP.
LIMIAR_R2_CURVA = 0.80

# Campos brutos do Intervals.icu lidos de activities.raw, com as chaves
# originais (EFTP em maiusculas, como vem da API).
CAMPOS_CP_ACEITAS_BRUTOS = ['icu_pm_cp', 'icu_rolling_ftp', 'icu_pm_ftp',
                            'icu_pm_ftp_watts', 'icu_pm_ftp_secs',
                            'icu_pm_w_prime', 'icu_ftp', 'EFTP']
CAMPOS_CP_ACEITAS = (['id', 'date', 'type_raw', 'curva_cp', 'curva_w_prime',
                      'curva_r2', 'curva_n_pontos'] + CAMPOS_CP_ACEITAS_BRUTOS)


def cp_aceitas_linhas(acts, curvas):
    """Uma linha por sessao com curva de CP aceita (r2 >= 0.80).

    acts: dicts originais de activities.raw; curvas: saida de db.cp_por_sessao.
    Devolve (linhas, sem_atividade): sessoes com curva aceita mas sem registo
    em activities nao geram linha; ficam so contadas em sem_atividade.
    Campos ausentes no raw ficam None (vazios no CSV), nunca zero.
    """
    raw_por_id = {}
    for a in acts or []:
        i = a.get('id')
        if i and i not in raw_por_id:
            raw_por_id[i] = a

    linhas, sem_atividade, vistos = [], [], set()
    for c in curvas or []:
        aid = c.get('activity_id')
        r2 = c.get('r2')
        if aid in vistos or r2 is None or r2 < LIMIAR_R2_CURVA:
            continue
        vistos.add(aid)
        a = raw_por_id.get(aid)
        if a is None:
            sem_atividade.append(aid)
            continue
        linha = {
            'id': aid,
            'date': (a.get('start_date_local') or '')[:10] or c.get('date'),
            'type_raw': a.get('type'),
            'curva_cp': c.get('cp'),
            'curva_w_prime': c.get('w_prime'),
            'curva_r2': r2,
            'curva_n_pontos': c.get('n_pontos'),
        }
        for campo in CAMPOS_CP_ACEITAS_BRUTOS:
            linha[campo] = a.get(campo)
        linhas.append(linha)
    return linhas, sem_atividade


def cp_aceitas(db):
    """(texto_csv, resumo) das sessoes com curva de CP aceita, com campos brutos.

    Reutiliza db.cp_por_sessao (o mesmo calculo do PMC) e db.load_activities.
    Nao chama a API externa e nao recalcula a regressao.
    Levanta SemDados se a base estiver desligada, vazia, ou se a curva de CP
    nao puder ser lida.
    """
    acts = db.load_activities()
    if not acts:
        raise SemDados('base de dados indisponivel ou sem actividades; '
                       'cp_aceitas.csv precisa de activities.raw')
    try:
        curvas = db.cp_por_sessao() or []
    except Exception as e:
        raise SemDados(f'curva de CP indisponivel: {type(e).__name__}: {e}')

    aceitas = {c.get('activity_id') for c in curvas
               if c.get('r2') is not None and c['r2'] >= LIMIAR_R2_CURVA}
    linhas, sem_atividade = cp_aceitas_linhas(acts, curvas)
    resumo = {
        'curvas_aceitas': len(aceitas),
        'linhas': len(linhas),
        'ids_unicos': len({r['id'] for r in linhas}),
        'sem_atividade': len(sem_atividade),
        'campos_brutos_ausentes': {c: sum(1 for r in linhas if r[c] is None)
                                   for c in CAMPOS_CP_ACEITAS_BRUTOS},
    }
    return _csv(linhas, CAMPOS_CP_ACEITAS), resumo


def serie_diaria(pmc, db, sheets):
    """Serie diaria com CTL, ATL e TSB — o que alimenta os graficos."""
    from api_client import fetch_activities, norm_tipo, num
    acts = fetch_activities() or []
    ses = []
    for a in acts:
        d = (a.get('start_date_local') or '')[:10]
        if len(d) == 10:
            ses.append({'date': d, 'type': norm_tipo(a.get('type')),
                        'tl': num(a.get('icu_training_load'))})
    return _csv(pmc.calcular(ses, 'tl'), COLUNAS['serie_diaria'])


def indice():
    """Que exportacoes existem, para quem chega ao endpoint sem saber."""
    return {
        'formatos': ['csv', 'json'],
        'ficheiros': {
            'atividades': 'uma linha por sessao, com carga, kJ, zonas, HR',
            'wellness': 'formulario (HRV4Training) + Intervals.icu lado a lado; colunas _icu vem do relogio',
            'corporal': 'peso, gordura, calorias, macros (Google Sheets)',
            'curvas': 'curvas de potencia; ?formato=json para listas',
            'testes': 'esforcos maximos detectados — a ancora de performance',
            'cp': "CP e W' por sessao, do ajuste P(t)=W'/t+CP",
            'serie_diaria': 'serie diaria com CTL, ATL, TSB',
        },
        'exemplos': [
            '/api/export/atividades.csv',
            '/api/export/curvas.csv?tipo=Bike',
            '/api/export/curvas.json',
            '/api/export/testes.csv',
        ],
        'tudo': '/api/export/tudo.json — todos num so ficheiro',
    }


# ── exportacao completa das atividades (JSON bruto, uma linha por atividade) ──
#
# Diferente de atividades(): le o JSON original de cada atividade (a coluna
# 'raw' do banco ou o dict da API), para que trimp, icu_training_load, RPE,
# tags e campos personalizados cheguem ao CSV com o nome original.

# Excluidos por regra, nunca gravados: campo fora de escopo do projeto (CAMPOS_EXCLUIDOS),
# derivados gravados pelo app no proprio dict (_kj, _type_norm), metadados de
# cliente OAuth e qualquer chave com nome de segredo.
CAMPOS_EXCLUIDOS = frozenset({'CPR'})
PREFIXOS_EXCLUIDOS = ('_', 'oauth_')
SUBSTRINGS_SEGREDO = ('token', 'password', 'senha', 'secret', 'apikey', 'api_key',
                      'authorization', 'cookie')

# Colunas que aparecem primeiro, se existirem; o resto segue ordem alfabetica.
COLUNAS_INICIAIS = ['id', 'start_date_local', 'start_date', 'type', 'name', 'source',
                    'moving_time', 'elapsed_time', 'distance', 'icu_distance',
                    'icu_training_load', 'trimp', 'icu_rpe', 'icu_joules',
                    'icu_joules_above_ftp', 'average_heartrate', 'max_heartrate',
                    'tags', 'icu_ftp', 'icu_pm_ftp', 'icu_athlete_id']

_NUMERO = re.compile(r'^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$')


def campo_permitido(nome):
    """True se a chave original pode ir para o CSV."""
    if not isinstance(nome, str) or not nome:
        return False
    if nome in CAMPOS_EXCLUIDOS or nome.startswith(PREFIXOS_EXCLUIDOS):
        return False
    baixo = nome.lower()
    return not any(s in baixo for s in SUBSTRINGS_SEGREDO)


def _protege_formula(s):
    """Texto que o Excel leria como formula recebe uma aspa simples na frente.
    Numeros (inclusive com sinal) ficam como estao."""
    cabeca = s.lstrip()
    if cabeca[:1] in ('=', '+', '-', '@', '\t', '\r') and not _NUMERO.match(s.strip()):
        return "'" + s
    return s


def celula(v):
    """Valor JSON -> texto. None (ausente ou nulo) vira vazio; 0 continua '0'."""
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (list, dict)):
        # estruturas: JSON compacto e estavel, por campo (nunca o registro inteiro)
        return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return _protege_formula(str(v))


def _colunas(registros):
    vistas = set()
    for r in registros:
        vistas.update(k for k in r if campo_permitido(k))
    ini = [c for c in COLUNAS_INICIAIS if c in vistas]
    resto = sorted((c for c in vistas if c not in ini), key=lambda c: c.casefold())
    return ini + resto


def atividades_csv(acts, ids=None):
    """(bytes_csv, info) a partir dos dicts originais das atividades.

    ids: lista de IDs (texto) a exportar, ou None para todas. A ordem das linhas
    segue a ordem de 'acts'. IDs pedidos e nao encontrados vao para info['ids_ausentes'].
    Vazio (sem linhas) devolve bytes vazios e info['n'] == 0; quem chama decide a resposta.
    """
    if ids is None:
        escolhidos = list(acts)
        ausentes = []
    else:
        pedidos = [str(i) for i in ids]
        conjunto = set(pedidos)
        escolhidos = [a for a in acts if str(a.get('id')) in conjunto]
        achados = {str(a.get('id')) for a in escolhidos}
        ausentes = [i for i in pedidos if i not in achados]
    colunas = _colunas(escolhidos)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\r\n', quoting=csv.QUOTE_MINIMAL)
    w.writerow(colunas)
    datas = []
    for a in escolhidos:
        w.writerow([celula(a.get(c)) if c in a else '' for c in colunas])
        d = str(a.get('start_date_local') or '')[:10]
        if len(d) == 10:
            datas.append(d)
    info = {'n': len(escolhidos), 'colunas': colunas, 'ids_ausentes': ausentes,
            'de': min(datas) if datas else None, 'ate': max(datas) if datas else None}
    if not escolhidos:
        return b'', info
    # BOM UTF-8: o Excel em portugues abre acentos corretamente
    return buf.getvalue().encode('utf-8-sig'), info


# ── pacote completo: atividades + streams (ZIP) ───────────────────────────
# Streams sao lidos SO do banco local (db.get_streams). Nunca chamam a API:
# a pagina de detalhe chama, mas so por atividade e sob clique. Aqui um pacote
# teria dezenas ou centenas de chamadas, e isso nao foi autorizado.

import hashlib
import zipfile

VERSAO_FORMATO_PACOTE = '1.0'
LIMITE_ATIVIDADES_PACOTE = 300          # cada atividade pode trazer milhares de linhas de stream
CAMPOS_DERIVADOS_APP = ('_kj', '_type_norm')   # gravados por tabs/tab_detalhe.py nos dicts em cache
COLUNAS_STREAMS = ['activity_id', 'stream_key', 'stream_type', 'sensor_name',
                   'sample_index', 'time_seconds', 'timestamp', 'value', 'unit',
                   'pontos_originais', 'reduzido']
FILTROS_PERMITIDOS = ('search', 'typeFilter', 'tagFilter', 'dtDe', 'dtAte')


class LimitePacote(Exception):
    """Pedido com mais atividades do que cabe num pacote. Nao trunca: recusa."""

    def __init__(self, n):
        super().__init__(n)
        self.n = n


def objeto_original(a):
    """Objeto da API sem as chaves que o app acrescenta (comprovadas em tab_detalhe.py)
    e sem campos fora da politica de exportacao (segredos, CPR)."""
    return {k: v for k, v in a.items()
            if k not in CAMPOS_DERIVADOS_APP and campo_permitido(k)}


def _csv_bytes(linhas, colunas):
    """CSV UTF-8 com BOM e CRLF, como o da aba. Celulas ja vem em texto (celula())."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=colunas, extrasaction='ignore',
                       lineterminator='\r\n')
    w.writeheader()
    for r in linhas:
        w.writerow({c: r.get(c, '') for c in colunas})
    return ('﻿' + buf.getvalue()).encode('utf-8')


def _linhas_streams(aid, streams, meta):
    """Uma linha por amostra. Cada linha leva a chave do proprio stream: nada e
    alinhado por posicao entre streams diferentes.

    Tempo: so ha time_seconds quando existe um stream 'time' de mesmo comprimento
    (associacao por indice dentro do MESMO stream e evidente nesse caso). Sem ele,
    time_seconds fica vazio. Nunca se inventa tempo.
    Devolve (linhas, estruturados): estruturados sao streams que nao cabem em CSV.
    """
    por_chave = {m.get('key'): m for m in (meta or []) if isinstance(m, dict)}
    tempo = streams.get('time') if isinstance(streams.get('time'), list) else None
    linhas, estruturados = [], {}
    for chave, arr in streams.items():
        m = por_chave.get(chave, {})
        if not isinstance(arr, list) or any(isinstance(v, (list, dict)) for v in arr):
            estruturados[chave] = arr
            continue
        pontos = m.get('points')
        if isinstance(pontos, int) and not isinstance(pontos, bool):
            reduzido = 'true' if pontos > len(arr) else 'false'
        else:
            reduzido = ''
        usa_tempo = tempo is not None and len(tempo) == len(arr)
        for i, v in enumerate(arr):
            linhas.append({
                'activity_id': celula(aid),
                'stream_key': celula(chave),
                'stream_type': celula(m.get('type') or chave),
                'sensor_name': celula(m.get('sensor_name')),
                'sample_index': i,
                'time_seconds': celula(tempo[i]) if usa_tempo else '',
                'timestamp': '',
                'value': celula(v),
                'unit': celula(m.get('unit')),
                'pontos_originais': celula(pontos),
                'reduzido': reduzido,
            })
    return linhas, estruturados


def pacote_zip(acts, ids, buscar_streams, filtros=None, fonte_atividades=None,
               banco_ativo=True, agora=None):
    """ZIP com atividades.csv, atividades.jsonl, streams.csv, manifest.json
    (+ streams_estruturados.json quando houver stream que nao cabe em CSV).

    acts: atividades da mesma fonte que a tabela. ids: IDs escolhidos pelos filtros.
    buscar_streams(aid) -> (streams, meta) ou (None, None). Leitura local apenas.
    Devolve (bytes_zip, resumo). Levanta LimitePacote acima do limite; ValueError
    quando nenhuma atividade pedida existe.
    """
    agora = agora or datetime.now().isoformat(timespec='seconds')
    por_id = {str(a.get('id')): a for a in acts
              if isinstance(a, dict) and a.get('id') is not None}

    vistos, unicos, duplicados = set(), [], 0
    for i in ids:
        s = str(i)
        if s in vistos:
            duplicados += 1
            continue
        vistos.add(s)
        unicos.append(s)
    if len(unicos) > LIMITE_ATIVIDADES_PACOTE:
        raise LimitePacote(len(unicos))

    ausentes = [s for s in unicos if s not in por_id]
    presentes = [s for s in unicos if s in por_id]
    if not presentes:
        raise ValueError('nenhuma atividade pedida existe na fonte local')
    selecionadas = [por_id[s] for s in presentes]

    csv_bytes, info_csv = atividades_csv(selecionadas, presentes)
    jsonl_bytes = ''.join(
        json.dumps(objeto_original(a), ensure_ascii=False, separators=(',', ':')) + '\n'
        for a in selecionadas).encode('utf-8')

    linhas, estruturados, por_atividade = [], {}, []
    for s in presentes:
        st = {'activity_id': s, 'status': None, 'erro': None,
              'stream_tipos': [], 'amostras': 0, 'estruturados': []}
        try:
            streams, meta = buscar_streams(s)
            if not streams:
                st['status'] = 'sem_streams_locais'
            else:
                l, est = _linhas_streams(s, streams, meta)
                linhas.extend(l)
                if est:
                    estruturados[s] = est
                st.update(status='exportado', stream_tipos=sorted(streams.keys()),
                          amostras=len(l), estruturados=sorted(est.keys()))
        except Exception as e:  # erro registrado no manifesto, nunca silencioso
            st.update(status='erro_leitura', erro=str(e)[:200])
        por_atividade.append(st)

    cobertura = {}
    for l in linhas:
        c = cobertura.setdefault(l['stream_type'], {'atividades': set(), 'amostras': 0})
        c['atividades'].add(l['activity_id'])
        c['amostras'] += 1
    cobertura = {k: {'atividades': len(v['atividades']), 'amostras': v['amostras']}
                 for k, v in sorted(cobertura.items())}

    n_sem = sum(1 for p in por_atividade if p['status'] == 'sem_streams_locais')
    n_erro = sum(1 for p in por_atividade if p['status'] == 'erro_leitura')
    n_ok = sum(1 for p in por_atividade if p['status'] == 'exportado')
    n_pares = sum(len(p['stream_tipos']) for p in por_atividade)

    limitacoes = [
        'Streams locais sao reduzidos a no maximo 1500 pontos por media '
        '(api_client.parse_streams). Coluna reduzido=true indica a serie reduzida; '
        'pontos_originais informa o comprimento original quando guardado.',
        'O stream de tempo nao e guardado localmente: time_seconds e timestamp ficam vazios '
        'salvo quando o pacote recebe um stream time com mesmo comprimento.',
        'Unidades nao sao guardadas localmente: unit fica vazio salvo quando informado.',
        'Streams sem valores numericos ou do tipo array nao sao guardados localmente; '
        'a ausencia nao significa que o stream nao existe na fonte.',
        f'Atividades sem streams locais: {n_sem}. Nao foram consultadas na API nesta exportacao.',
        'CPR excluido por politica do projeto.',
        'Campos _kj e _type_norm (derivados pelo app) removidos do JSON original.',
    ]
    if not banco_ativo:
        limitacoes.append('Banco local nao habilitado: nenhum stream foi lido.')
    if fonte_atividades != 'db':
        limitacoes.append(f'Atividades vieram da fonte "{fonte_atividades}", '
                          'nao do banco local: o pacote nao e apenas local.')
    if duplicados:
        limitacoes.append(f'{duplicados} IDs duplicados foram ignorados.')
    if ausentes:
        limitacoes.append(f'{len(ausentes)} IDs nao existem na fonte local e nao foram exportados.')

    arquivos = {
        'atividades.csv': csv_bytes,
        'atividades.jsonl': jsonl_bytes,
        'streams.csv': _csv_bytes(linhas, COLUNAS_STREAMS),
    }
    if estruturados:
        arquivos['streams_estruturados.json'] = json.dumps(
            estruturados, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    manifest = {
        'versao_formato': VERSAO_FORMATO_PACOTE,
        'gerado_em': agora,
        'filtros': filtros or {},
        'atividades': {
            'selecionadas': len(unicos), 'exportadas': len(presentes),
            'com_streams': n_ok, 'sem_streams_locais': n_sem, 'com_erro_leitura': n_erro,
            'ids_ausentes': ausentes, 'ids_duplicados': duplicados,
        },
        'streams': {
            'pares_atividade_stream': n_pares, 'amostras_exportadas': len(linhas),
            'cobertura_por_tipo': cobertura,
            'estruturados_em_json': sorted(estruturados.keys()),
        },
        'por_atividade': por_atividade,
        'fonte': {
            'atividades': f'fonte "{fonte_atividades}" (mesma da tabela)',
            'streams': 'banco local, tabela streams via db.get_streams; sem chamadas a API',
        },
        'sem_chamadas_externas': fonte_atividades == 'db',
        'limitacoes': limitacoes,
        'arquivos': [{'nome': n, 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()}
                     for n, b in arquivos.items()],
        'nota_integridade': 'manifest.json nao se inclui no proprio hash',
    }
    arquivos['manifest.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for nome in ['atividades.csv', 'atividades.jsonl', 'streams.csv',
                     'streams_estruturados.json', 'manifest.json']:
            if nome in arquivos:
                z.writestr(nome, arquivos[nome])

    resumo = {'n': len(presentes), 'de': info_csv['de'], 'ate': info_csv['ate'],
              'ids_ausentes': ausentes, 'streams_amostras': len(linhas),
              'sem_streams': n_sem, 'erros': n_erro}
    return buf.getvalue(), resumo


# ── Blocos de aquecimento (Metabolismo -> Aquecimento) ───────────────────
#
# Exporta apenas o que esta persistido em aquecimento_blocos. Nao chama a
# deteccao, nao le streams, nao sincroniza com o Drive e nao cria a base:
# a ligacao e' SQLite em modo somente leitura.

COLUNAS_AQUECIMENTO = [
    'activity_id', 'data', 'modalidade', 'bloco_num', 'interval_num',
    'watts_alvo', 'watts_real', 'tempo_seg',
    'hr_avg', 'hr_min', 'hr_max',
    'smo2_avg', 'smo2_min', 'smo2_max',
    'resp_avg', 'resp_min', 'resp_max',
    'dfa1_avg', 'dfa1_min', 'dfa1_max',
    'metricas_disponiveis', 'data_registo',
]
_METRICAS_AQUECIMENTO = ('hr', 'smo2', 'resp', 'dfa1')
_COLUNAS_SQL_AQUECIMENTO = [c for c in COLUNAS_AQUECIMENTO
                            if c != 'metricas_disponiveis']
_MODALIDADE = re.compile(r'[A-Za-z]{1,20}')


def abrir_leitura_aquecimento(caminho):
    """Ligacao SQLite somente leitura. Falha se o ficheiro nao existir."""
    uri = Path(caminho).resolve().as_uri() + '?mode=ro'
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def metricas_disponiveis_aquecimento(r):
    """Metricas com pelo menos um valor (avg, min ou max) guardado no bloco."""
    return '|'.join(
        m for m in _METRICAS_AQUECIMENTO
        if any(r.get(f'{m}_{a}') is not None for a in ('avg', 'min', 'max')))


def aquecimento_blocos_csv(caminho, modalidade=None):
    """(conteudo_bytes, info). Uma linha por bloco persistido.

    Sem base local: devolve so o cabecalho, com um aviso explicito (linha de
    comentario no inicio do CSV e info['aviso']). Nao cria nem descarrega nada.
    """
    if modalidade is not None and not _MODALIDADE.fullmatch(modalidade):
        raise ValueError('modalidade invalida')

    aviso, linhas = None, []
    if not os.path.exists(caminho):
        aviso = 'Base local de aquecimento inexistente: nenhum dado exportado.'
    else:
        try:
            con = abrir_leitura_aquecimento(caminho)
            try:
                sql = ("SELECT " + ', '.join(_COLUNAS_SQL_AQUECIMENTO)
                       + " FROM aquecimento_blocos")
                params = []
                if modalidade:
                    sql += " WHERE modalidade = ?"
                    params.append(modalidade)
                sql += " ORDER BY data, modalidade, activity_id, bloco_num"
                linhas = con.execute(sql, params).fetchall()
            finally:
                con.close()
        except sqlite3.Error as e:
            aviso = (f'Base local de aquecimento ilegivel '
                     f'({type(e).__name__}): {e}')

    buf = io.StringIO()
    if aviso:
        buf.write('# AVISO: ' + aviso + '\r\n')
    w = csv.writer(buf, delimiter=';', lineterminator='\r\n')
    w.writerow(COLUNAS_AQUECIMENTO)
    for r in linhas:
        d = dict(r)
        d['metricas_disponiveis'] = metricas_disponiveis_aquecimento(d)
        w.writerow([celula(d.get(c)) for c in COLUNAS_AQUECIMENTO])
    conteudo = ('\ufeff' + buf.getvalue()).encode('utf-8')
    return conteudo, {'n': len(linhas), 'aviso': aviso,
                      'modalidade': modalidade or None}
