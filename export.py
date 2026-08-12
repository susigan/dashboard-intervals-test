"""Exportacao dos dados em bruto, para analise fora do dashboard.

Devolve exactamente o que o dashboard usa — as mesmas colunas, os mesmos
filtros — para que qualquer analise feita por fora seja comparavel com a
que corre aqui.
"""

import csv
import io
import json
from datetime import datetime


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
