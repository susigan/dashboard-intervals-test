"""Assento de teste: duracao operacional do IR = 1200 s e P0 global injetado.

Os testes do MOTOR do IR (recalibracao, historico, versoes de referencia, curvas) foram escritos com alvos de 1200 s e um
P0 conhecido. O motor e generico na duracao (modelo_mmp3.DURACAO_S / modelo_ir_servico.DURACAO_IR); em producao e 180 s
(MMP3) e o P0 e o MMP3 do ultimo YES (`modelo_mmp3.p0_global`). Estes modulos fixam 1200 s e injetam o registo YES de
origem do P0 (`definir_p0`) para verificar o mesmo motor com os mesmos dados; a regra real do P0 global (derivada dos
esforcos) esta em test_ir_p0_global.py e test_ir_mmp3.py.
"""
from unittest import mock

import modelo_ir_servico as mis
import modelo_mmp3 as mmp3
import observacoes_auto as oa

P0_PADRAO = 290.0
_P = []
_ESTADO = {'valor': P0_PADRAO, 'test_id': None, 'estado': 'valida'}


def definir_p0(valor=P0_PADRAO, test_id=None):
    """Registo YES de origem injetado (muda a assinatura da referencia se mudar o valor ou o test_id)."""
    _ESTADO.update(valor=valor, test_id=test_id, estado='valida')


def sem_p0(estado='ausente'):
    _ESTADO.update(valor=None, estado=estado)


def _falso_global(entradas):
    if _ESTADO['estado'] != 'valida':
        return {'origem': mmp3.ORIGEM_P0, 'duracao_s': mmp3.DURACAO_S, 'versao_regra': mmp3.VERSAO_REGRA_P0, 'n_yes': 0,
                'n_esforcos': 0, 'n_dados_insuficientes': 0, 'estado': _ESTADO['estado'], 'registo': None}
    tid = _ESTADO['test_id'] or f"Bike:{mmp3.DURACAO_S}:seam-{_ESTADO['valor']}"
    return {'origem': mmp3.ORIGEM_P0, 'duracao_s': mmp3.DURACAO_S, 'versao_regra': mmp3.VERSAO_REGRA_P0, 'n_yes': 1,
            'n_esforcos': 1, 'n_dados_insuficientes': 0, 'estado': 'valida',
            'registo': {'test_id': tid, 'activity_id': 'seam', 'modalidade': 'Bike', 'data': '2025-01-01',
                        'inicio': '2025-01-01T07:00:00', 'watts': _ESTADO['valor'], 'pr': False, 'pct_do_melhor': None}}


def iniciar(duracao=1200, p0=P0_PADRAO):
    definir_p0(p0)
    for alvo, nome, val in ((mmp3, 'DURACAO_S', duracao), (mis, 'DURACAO_IR', duracao),
                            (mis, 'p0_global_das_entradas', _falso_global),
                            (mmp3, 'tem_tag_3min', lambda tags: True)):   # a regra da tag tem os seus testes proprios
        pt = mock.patch.object(alvo, nome, val)
        pt.start()
        _P.append(pt)
    pt = mock.patch.object(oa, 'obter_marcos', return_value=(None, oa.FONTE_SEASON_MES))   # sem rede nos testes
    pt.start()
    _P.append(pt)


def parar():
    while _P:
        _P.pop().stop()
