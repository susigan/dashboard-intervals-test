"""DADOS SINTETICOS (rotulados) para os testes do IR exploratorio e season. Nunca sao dados reais.

Gera cargas diarias e observacoes de potencia a partir de parametros conhecidos, com ruido determinista (semente
fixa), para testar comportamento controlado. Nao serve para concluir que parametros reais sao validos.
"""
from datetime import date, timedelta

import numpy as np

import modelo_ir as ir

PARAMETROS_VERDADEIROS = dict(p0=600.0, k1=1.2, k2=2.5, t1=40.0, t2=6.0)


def cenario(semente=1, n_dias=900, inicio='2023-01-01', passo=9):
    """Retorna (carga_dia, sel_pre, atuais, cfg) para uma modalidade (Bike, 300 s), com duas seasons.

    carga_dia: {data: carga} numerica (70% dos dias tem atividade). sel_pre: estrutura de selecionar()
    (usadas com referencia season). atuais: observacoes com 'referencias' e 'season' como as do detetor."""
    rng = np.random.default_rng(semente)
    d0 = date.fromisoformat(inicio)
    carga = {}
    for i in range(n_dias):
        if rng.random() < 0.7:
            carga[(d0 + timedelta(days=i)).isoformat()] = float(rng.uniform(30, 120))
    fim = (d0 + timedelta(days=n_dias - 1)).isoformat()
    c = ir.cargas_diarias(carga, d0.isoformat(), fim)
    P = ir.desempenho(np.concatenate([c, [0.0]]), PARAMETROS_VERDADEIROS['p0'], PARAMETROS_VERDADEIROS['k1'],
                      PARAMETROS_VERDADEIROS['k2'], PARAMETROS_VERDADEIROS['t1'], PARAMETROS_VERDADEIROS['t2'], 1)
    sel, atuais = {}, []
    for i in range(0, n_dias, passo):
        dd = (d0 + timedelta(days=i)).isoformat()
        w = float(P[i] + rng.normal(0, 5))
        tid = f'Bike:300:x{i}'
        season = '2023/24' if dd < '2024-07-01' else '2024/25'
        refs = {'season': {'qualifica': True, 'referencia_watts': w * 1.02, 'referencia_activity_id': f'r{i}',
                           'referencia_data': dd, 'pct': 98.0},
                'all_time': {'qualifica': False, 'referencia_watts': w * 1.2, 'referencia_activity_id': 'rA',
                             'referencia_data': inicio, 'pct': 83.0}}
        atuais.append({'test_id': tid, 'modalidade': 'Bike', 'duracao_s': 300, 'data': dd, 'watts': w,
                       'activity_id': f'a{i}', 'season': season, 'referencias': refs})
        sel.setdefault(('Bike', 300), {'usadas': [], 'excluidas': []})['usadas'].append(
            {'test_id': tid, 'data': dd, 'watts': w, 'activity_id': f'a{i}', 'referencia_tipo': 'season',
             'pct_da_referencia': 98.0})
    return carga, sel, atuais, ir._cfg(None)
