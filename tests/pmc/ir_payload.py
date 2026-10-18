"""Respostas REAIS de /api/modelo/ir/influencia (Flask test client + SQLite temporaria + dados
SINTETICOS) para o harness da UI: a interface e testada contra o contrato real do servidor."""
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

XSS = '<img src=x onerror=alert(1)>'
URL = '/api/modelo/ir/influencia?tipo=%s&duracao=%s'


def gerar():
    import test_modelo_ir_servico as S
    import modelo_ir as ir

    class Cenario(S._Base):
        def runTest(self):
            pass

    c = Cenario()
    c.setUp()
    try:
        for i, a in enumerate(c.ativ):                   # um nome com HTML: a UI tem de o escapar
            a['name'] = XSS if i == 40 else f'Treino {i}'
        c.prepara()
        c.rec()
        out = {'xss_id': c.ativ[40]['id']}
        get = lambda u: c.c.get(u).get_json()                                  # noqa: E731
        out['bike1200'] = get(URL % ('Bike', 1200))
        meio = out['bike1200']['atividades'][len(out['bike1200']['atividades']) // 2]
        out['ativ_id'] = meio['id']
        out['bike1200_ativ'] = get(URL % ('Bike', 1200) + '&atividade=' + meio['id'])
        out['bike300'] = get(URL % ('Bike', 300))
        out['row300_sem_ajuste'] = get(URL % ('Row', 300))
        real = S.db.ultimo_ajuste_ir('Bike', 1200)
        with mock.patch.object(S.db, 'ultimo_ajuste_ir', return_value={
                **real, 'estado': ir.EST_NAO_IDENTIFICAVEL,
                'identificabilidade': {'avisos': ['parametro_no_limite:t1']}}):
            out['bike1200_nao_ident'] = get(URL % ('Bike', 1200))
        with mock.patch.object(S.db, 'ultimo_ajuste_ir', return_value={
                **real, 'estado': ir.EST_INSUFICIENTE, 'versao_ajuste': real['versao_ajuste'] + 1,
                'p0': None, 'k1': None, 'k2': None, 't1': None, 't2': None}):
            out['bike1200_insuficiente'] = get(URL % ('Bike', 1200))
        with mock.patch.object(S.db, 'ultimo_ajuste_ir', return_value={
                **real, 'estado': ir.EST_SEM_CONVERGENCIA, 'versao_ajuste': real['versao_ajuste'] + 1,
                'convergiu': False, 'p0': None, 'k1': None, 'k2': None, 't1': None, 't2': None}):
            out['bike1200_sem_convergencia'] = get(URL % ('Bike', 1200))
        out['xss'] = get(URL % ('Bike', 1200) + '&atividade=' + out['xss_id'])
        return out
    finally:
        c.doCleanups()
