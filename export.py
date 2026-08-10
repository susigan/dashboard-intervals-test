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
    'wellness': ['date', 'hrv', 'rhr', 'sleep_hours', 'sleep_quality',
                 'stress', 'fatiga', 'humor', 'soreness', 'peso', 'fat',
                 'hf_power', 'doente', 'performance'],
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


def wellness(sheets):
    # sheets.carregar() usa a cache; carregar_wellness() iria sempre a rede
    w, _c, _e = sheets.carregar()
    return _csv(w or [], COLUNAS['wellness'])


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
            'wellness': 'HRV, RHR, sono, stress, cansaco, dores (Google Sheets)',
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
