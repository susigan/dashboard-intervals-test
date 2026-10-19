"""Assento de teste: duracao operacional do IR = 1200 s.

Os testes do MOTOR do IR (recalibracao, historico, P0, curvas) foram escritos com alvos de 1200 s. O motor e generico na
duracao (modelo_mmp3.DURACAO_S / modelo_ir_servico.DURACAO_IR); em producao e 180 s (MMP3). Estes modulos fixam 1200 s para
continuar a verificar o mesmo motor com os mesmos dados; o comportamento de 180 s esta em test_ir_mmp3*.py.
"""
from unittest import mock

import modelo_ir_servico as mis
import modelo_mmp3 as mmp3
import observacoes_auto as oa

_P = []


def iniciar(duracao=1200):
    for alvo, nome in ((mmp3, 'DURACAO_S'), (mis, 'DURACAO_IR')):
        p = mock.patch.object(alvo, nome, duracao)
        p.start()
        _P.append(p)
    p = mock.patch.object(oa, 'obter_marcos', return_value=(None, oa.FONTE_SEASON_MES))   # sem rede nos testes
    p.start()
    _P.append(p)


def parar():
    while _P:
        _P.pop().stop()
