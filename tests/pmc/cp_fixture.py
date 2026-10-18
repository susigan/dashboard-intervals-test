"""Instantaneos SINTETICOS do CP Model (linhas de cp_resultados) para testar a ligacao CP -> IR.

Dados inventados: testam o codigo, nao o atleta. Nunca tocam na base de perfil real.
"""
import json
from unittest import mock

import modelo_ir_servico as mis

# P0 = CP: o CP gravado e o P0 de TODAS as duracoes da modalidade (290 W = o P0 dos cenarios sinteticos do IR).
# W' e os pontos sao so informacao do resultado do CP (a mesma hiperbole CP + W'/t com CP 290, W' 24000).
CP_PADRAO, WP_PADRAO = 290.0, 24000.0
# duracoes classicas reais do CP Model: Bike/Run 300-720-1200 s; Row/Ski 60-300-720 s (utils/cp_model.DURACOES_CLASSICAS)
PONTOS_PADRAO = ((300, 370.0), (720, 323.3), (1200, 310.0))


def instantaneo(modalidade='Bike', cp=CP_PADRAO, wp=WP_PADRAO, modelo='M1 (WLS-P)', pontos=PONTOS_PADRAO,
                id=1, data_referencia='2025-01-01', data_gravacao='2025-01-02 10:00:00', see_pct=1.0,
                outros=None, season='2025', origem='manual', modelos_json=None):
    """Uma linha de cp_resultados como dict. `outros` = {nome: {...}} acrescenta modelos ao modelos_json."""
    mj = modelos_json
    if mj is None:
        mj = {modelo: {'cp': None if cp is None else round(cp, 1), 'wp': None if wp is None else round(wp),
                       'see_pct': see_pct, 'classico': True,
                       'pontos_usados': [{'t': t, 'w': w} for t, w in pontos]}}
        for n, x in (outros or {}).items():
            mj[n] = x
    return {'id': id, 'data_referencia': data_referencia, 'data_gravacao': data_gravacao,
            'modalidade': modalidade, 'season': season, 'modelo_escolhido': modelo, 'cp_w': cp, 'wp_j': wp,
            'see_pct': see_pct, 'n_pts': len(pontos), 'k_params': 2,
            'modelos_json': json.dumps(mj) if not isinstance(mj, str) else mj, 'origem': origem}


def instantaneos_padrao(**kw):
    """Bike e Run com pontos 300-1200 s; Row e Ski 60-720 s (as duracoes classicas reais do CP Model)."""
    longos = PONTOS_PADRAO
    curtos = ((60, 690.0), (300, 370.0), (720, 323.3))
    return [instantaneo('Bike', pontos=longos, id=1, **kw), instantaneo('Run', pontos=longos, id=2, **kw),
            instantaneo('Row', pontos=curtos, id=3, **kw), instantaneo('Ski', pontos=curtos, id=4, **kw)]


def patch_loader(lista=None, erro=None):
    """Substitui o leitor do CP Model no servico do IR por uma lista sintetica (nunca le a base de perfil)."""
    lista = instantaneos_padrao() if lista is None else lista
    return mock.patch.object(mis, 'carregar_instantaneos_cp', side_effect=lambda: (list(lista), erro))


def referencia_p0(modalidade, duracao, valor=290.0, conhecida_desde='2020-01-01T00:00:00', versao=1, assinatura=None):
    """Linha do historico de referencias de P0 do IR (para o walk-forward), conhecida desde `conhecida_desde`."""
    return {'modalidade': modalidade, 'duracao_s': duracao, 'versao_referencia': versao,
            'conhecida_desde': conhecida_desde, 'valor_p0': valor,
            'assinatura': assinatura or f'sig{versao}'}
