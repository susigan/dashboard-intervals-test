"""Diagnostico da carga do IR (so leitura, nao altera a serie usada pelo solver).

A serie numerica continua a ser a de `modelo_ir_servico.carga_por_modalidade`. Este modulo CLASSIFICA cada
atividade com as mesmas regras, para tornar visivel a diferenca entre:

  * dia sem atividade                      -> carga 0 por construcao (cargas_diarias);
  * atividade sem `icu_training_load`      -> nao entra na serie (conta como sem carga);
  * atividade com carga valida igual a 0   -> entra como 0 (carga valida);
  * carga negativa ou nao numerica         -> nao entra na serie; NAO e tratada como descanso real;
  * atividade ignorada por mapeamento/regras (tipo fora de TYPE_MAP, sem data, duplicada por id).

Nao existe corte de 2023: dias sem carga numerica ja sao zero em qualquer ano (cargas_diarias).
"""
import math
from collections import Counter

import config
import modelo_testes as mt

CAT_POSITIVA = 'carga_positiva'
CAT_ZERO_VALIDA = 'carga_zero_valida'
CAT_SEM_CAMPO = 'sem_icu_training_load'
CAT_NEGATIVA = 'carga_negativa'
CAT_NAO_NUMERICA = 'carga_nao_numerica'
CAT_SEM_DATA = 'sem_data'
CAT_DUPLICADA = 'duplicada'
CAT_TIPO_NAO_MAPEADO = 'tipo_nao_mapeado'
CAT_NAO_DICIONARIO = 'nao_dicionario'

# categorias que entram na serie de carga (contribuem numericamente)
ENTRAM_NA_SERIE = (CAT_POSITIVA, CAT_ZERO_VALIDA)
# categorias de atividade que nao entram na serie (mas que sao contadas como 'sem carga' na serie)
SEM_CARGA_NA_SERIE = (CAT_SEM_CAMPO, CAT_NEGATIVA, CAT_NAO_NUMERICA)


def _valor(v):
    """(valor float, categoria) para o campo icu_training_load. Numerico finito >= 0 entra."""
    if v is None:
        return None, CAT_SEM_CAMPO
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None, CAT_NAO_NUMERICA
    f = float(v)
    if not math.isfinite(f):          # NaN e +inf: nao entram (espelha carga_por_modalidade corrigida)
        return None, CAT_NAO_NUMERICA
    if f < 0:
        return None, CAT_NEGATIVA
    if f == 0:
        return 0.0, CAT_ZERO_VALIDA
    return f, CAT_POSITIVA


def classificar(a, vistos):
    """Classifica uma atividade. Devolve (modalidade|None, data|None, valor|None, categoria).

    `vistos` (set) acumula os ids ja vistos: a mesma regra de deduplicacao por id de carga_por_modalidade.
    A ordem das verificacoes e a mesma da serie: dicionario -> tipo -> id -> data -> carga."""
    if not isinstance(a, dict):
        return None, None, None, CAT_NAO_DICIONARIO
    mod = config.TYPE_MAP.get(a.get('type'))
    if mod not in mt.MODALIDADES:
        return None, None, None, CAT_TIPO_NAO_MAPEADO
    aid = a.get('id')
    if aid is not None:
        if aid in vistos:
            return mod, None, None, CAT_DUPLICADA
        vistos.add(aid)
    d = str(a.get('start_date_local') or '')[:10]
    if len(d) != 10:
        return mod, None, None, CAT_SEM_DATA
    v, cat = _valor(a.get('icu_training_load'))
    return mod, d, v, cat


def diagnostico_carga(atividades):
    """Contagens por modalidade e categoria, e a serie que o solver usa (a mesma de carga_por_modalidade).

    Devolve {'por_modalidade': {mod: {categoria: n}}, 'fora_do_mapeamento': n, 'serie': {mod: {data: carga}},
             'dias_com_atividade': {mod: n_dias_com_alguma_atividade_contada}}.
    Invariante (testada): para cada modalidade, a soma das categorias ENTRAM_NA_SERIE == n_atividades_com_carga
    e a soma de SEM_CARGA_NA_SERIE == n_atividades_sem_carga de carga_por_modalidade."""
    vistos = set()
    por_mod = {m: Counter() for m in mt.MODALIDADES}
    serie = {m: {} for m in mt.MODALIDADES}
    fora = 0
    for a in atividades or []:
        mod, d, v, cat = classificar(a, vistos)
        if mod is None:
            fora += 1
            continue
        por_mod[mod][cat] += 1
        if cat in ENTRAM_NA_SERIE:
            serie[mod][d] = serie[mod].get(d, 0.0) + v
    return {'por_modalidade': {m: dict(sorted(c.items())) for m, c in por_mod.items()},
            'fora_do_mapeamento': fora,
            'serie': serie}


def cobertura_carga(serie_mod):
    """Datas da carga numerica de uma modalidade: primeira, ultima e numero de dias com carga registada.

    Dias sem registo NAO sao lacunas determinaveis: um dia sem atividade e indistinguivel de repouso."""
    datas = sorted(serie_mod or {})
    if not datas:
        return {'data_ini': None, 'data_fim': None, 'dias_com_carga_registada': 0}
    return {'data_ini': datas[0], 'data_fim': datas[-1], 'dias_com_carga_registada': len(datas)}
