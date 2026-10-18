"""Respostas REAIS de /api/carga/distribuicao (Flask test client + SQLite temporaria + dados SINTETICOS)
para o harness da UI: a interface e testada contra o contrato real do servidor."""
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

D0 = date(2026, 1, 1)
URL = '/api/carga/distribuicao?metodo=%s&janela=%s&dias=%s'
FATOR = {'Ride': 0.9, 'Row': 1.4, 'Run': 1.2, 'NordicSki': 1.25}      # escalas diferentes de proposito
FCMAX = {'Ride': 190, 'Row': 188, 'Run': 192, 'NordicSki': 182}   # valores sinteticos
CICLO = ('Ride', 'Row', 'Run', 'NordicSki')


def atividades(com_musculacao=True):
    out, n = [], [0]

    def add(tipo, dia, carga, **kw):
        n[0] += 1
        a = {'id': f'a{n[0]}', 'type': tipo, 'start_date_local': (D0 + timedelta(days=dia)).isoformat() + 'T08:00:00',
             'icu_training_load': carga}
        a.update(kw)
        out.append(a)

    for d in list(range(0, 60)) + list(range(101, 150)):
        tipo, tl = CICLO[d % 4], 40.0 + (d * 7) % 31
        extra = {'power_load': tl, 'athlete_max_hr': FCMAX[tipo], 'icu_resting_hr': 55}
        if d % 11 != 0:                                  # 1 em cada 11 sem TRIMP (sem FC)
            extra['trimp'] = round(tl * FATOR[tipo], 1)
        if d % 17 == 0:                                  # FC marcada para ignorar: TRIMP nao conta
            extra['icu_ignore_hr'] = True
            extra['trimp'] = 50.0
        add(tipo, d, tl, **extra)
        if com_musculacao and d % 2 == 0:
            add('WeightTraining', d, 100000.0, trimp=100000.0)
    for d in range(90, 96):                       # so atividades sem carga valida
        add('Run', d, None)
    add('Row', 120, None)                        # uma atividade sem carga numa janela com carga
    return out


def gerar():
    import test_mmp_deteccao_automatica as T

    class Cenario(T.BaseDB):
        def runTest(self):
            pass

    c = Cenario()
    c.setUp()
    try:
        get = lambda u: c.c.get(u).get_json()                                  # noqa: E731
        out = {}
        c.ativ[:] = atividades(True)
        for met, k in (('training_load', 'tl'), ('trimp', 'tr')):
            out[f'{k}28_365'] = get(URL % (met, 28, 365))
            out[f'{k}7_365'] = get(URL % (met, 7, 365))
            out[f'{k}28_90'] = get(URL % (met, 28, 90))
        c.ativ[:] = atividades(False)
        out['tl28_365_sem_musculacao_nos_dados'] = get(URL % ('training_load', 28, 365))
        out['tr28_365_sem_musculacao_nos_dados'] = get(URL % ('trimp', 28, 365))
        c.ativ[:] = [a for a in atividades(True)][:6]
        out['incompleta'] = get(URL % ('training_load', 28, 365))
        c.ativ[:] = []
        out['vazia'] = get(URL % ('training_load', 28, 365))
        return out
    finally:
        c.doCleanups()
