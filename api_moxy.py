"""api_moxy.py — sessoes com sensor NIRS (Moxy).

Encontra as sessoes marcadas como Moxy e devolve os streams de SmO2 e THb
ja' limpos pelo pipeline do utils/mnirs.py.

A marca e' procurada no nome, na descricao e nos campos de texto do JSON da
actividade, aceitando 'moxy', '#moxy', 'Moxy' e variantes. Nao se assume
um campo de tags: a Intervals.icu nao expoe um consistentemente, e ja'
custou caro neste projecto assumir nomes de campos.

Registado com:  import api_moxy; api_moxy.registar(app)
"""

import json
import re
import traceback
from datetime import datetime, timedelta

from flask import jsonify, request

# So' a tag conta. Antes procurava-se tambem no nome e na descricao, e
# aceitavam-se sessoes com Smo2 no sumario mesmo sem tag -- isso trazia
# actividades que nada tinham a ver, porque qualquer sessao com o sensor
# ligado por acaso entrava. A tag e' uma decisao explicita do atleta; o
# nome nao e'.
MX_SESSOES_CACHE = {}

PADRAO_MOXY = re.compile(r'^\s*#?\s*moxy\s*$', re.IGNORECASE)

# Nomes possiveis dos streams NIRS. A Intervals.icu expoe smo2/thb, mas
# ficheiros com dois sensores acrescentam sufixos.
CANAIS = {
    'smo2': ['smo2', 'SmO2', 'smo2_1', 'Smo2'],
    'thb': ['thb', 'THb', 'thb_1'],
    'o2hb': ['O2Hb', 'o2hb'],
    'hhb': ['HHb', 'hhb', 'DiffHb'],
}


def _tags(j):
    """Lista de tags da actividade. Vem null quando nao ha nenhuma."""
    t = (j or {}).get('tags')
    if isinstance(t, str):
        return [x.strip() for x in t.split(',') if x.strip()]
    return [str(x).strip() for x in (t or []) if x]


def _tem_tag_moxy(j):
    return any(PADRAO_MOXY.match(t) for t in _tags(j))


def _remover_orfa(aid):
    """Apaga das tabelas locais uma actividade que a API ja' nao tem."""
    import db as _db
    fora = {}
    for tabela, coluna in (('activities', 'id'),
                           ('power_curves', 'activity_id'),
                           ('zone_times', 'activity_id')):
        try:
            _db._exec(f"DELETE FROM {tabela} WHERE {coluna} = ?", (str(aid),))
            fora[tabela] = 'apagada'
        except Exception as e:
            fora[tabela] = f'{type(e).__name__}: {e}'
    return fora


def _consenso_limiares(mlss, bp_mx, bp_livre, bp_taxa, perfil,
                       bp_hhb=None, lt1_reox=None, blocos=None):
    """Junta as estimativas nos dois limiares e assinala incoerencias.

    Antes havia um painel por metodo -- quatro numeros soltos, sem dizer
    qual respondia a que pergunta. E o BP1 do metodo Moxy nao entrava em
    grupo nenhum, o que deixava passar um candidato a PRIMEIRO limiar
    acima de um candidato a SEGUNDO sem ninguem dar por isso.
    """
    p1, p2 = [], []

    def _add(lista, metodo, w, rota, bpm=None, fc_origem=None):
        if w is not None:
            lista.append({'metodo': metodo, 'watts': round(float(w), 1),
                          'bpm': bpm, 'rota': rota,
                          'fc_origem': fc_origem})

    # ── primeiro limiar: LT1 / VT1 / FatMax ──────────────────────────
    if (lt1_reox or {}).get('ok'):
        _add(p1, 'Transição da reoxigenação (Yogev)',
             lt1_reox['lt1_estimado'],
             'onde o SmO2 deixa de subir dentro do bloco',
             (lt1_reox.get('fc') or {}).get('bpm'),
             (lt1_reox.get('fc') or {}).get('nota'))
    if perfil.get('ok') and perfil.get('bp1_watts') is not None:
        _add(p1, 'Topo da parábola (SmO2max)', perfil['bp1_watts'],
             'média do último minuto por degrau')
    if bp_mx.get('bp1_w') is not None:
        _add(p1, 'BP1 da curva SmO2 × potência', bp_mx['bp1_w'],
             'regressão por troços, 2 degraus por troço',
