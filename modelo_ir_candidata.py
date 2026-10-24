"""Candidata experimental CONGELADA e avaliacao prospetiva. Nucleo puro. Nao promove nada operacionalmente.

Principio: a candidata e fixada ANTES de dados novos. O registo tem hash de conteudo (imutabilidade verificavel).
A avaliacao prospetiva so aceita observacoes com data > data_ultima_observacao_desenvolvimento e usa as mesmas
baselines, o mesmo esquema de cortes e a mesma politica (POLITICA da versao 2.0) definidos no registo.
Dados insuficientes -> 'em_observacao'. Nunca se reajusta a candidata aos cortes de desenvolvimento.
"""
import hashlib
import json
from datetime import date

import modelo_ir_confianca as mic

ESTADO_EM_OBSERVACAO = 'em_observacao'


def _hash_conteudo(reg):
    base = {k: v for k, v in reg.items() if k != 'hash_conteudo'}
    return hashlib.sha256(json.dumps(base, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def verificar(reg):
    """True se o conteudo corresponde ao hash gravado (imutabilidade). Falha = registo alterado."""
    return reg.get('hash_conteudo') == _hash_conteudo(reg)


def selar(reg):
    reg = dict(reg)
    reg['hash_conteudo'] = _hash_conteudo(reg)
    return reg


def avaliar_prospetiva(reg, observacoes, avaliador, pol=None):
    """observacoes: [{test_id, data (YYYY-MM-DD), watts}] NOVAS. avaliador(observacoes_validas, reg) -> lista de cortes
    no formato cortes_treino_teste (ver modelo_ir_adaptador) ou levanta excecao. Nao reajusta nada aos cortes antigos.
    """
    if not verificar(reg):
        raise ValueError('registo da candidata alterado: hash de conteudo nao confere')
    pol = pol or mic.POLITICA
    corte_dev = date.fromisoformat(reg['data_ultima_observacao_desenvolvimento'])
    congelamento = date.fromisoformat(reg['data_congelamento'])
    # prospetiva = posterior ao ultimo dado de desenvolvimento E nao anterior a data de congelamento
    validas = [o for o in observacoes
               if date.fromisoformat(str(o['data'])[:10]) > corte_dev
               and date.fromisoformat(str(o['data'])[:10]) >= congelamento]
    recusadas = len(observacoes) - len(validas)
    minimo = pol['min_obs_teste_por_corte'] * pol['min_cortes_avaliaveis']
    if len(validas) < minimo:
        return {'estado': ESTADO_EM_OBSERVACAO, 'motivo': f'observacoes_prospetivas {len(validas)} < {minimo}',
                'recusadas_nao_prospetivas': recusadas, 'promocao_operacional': False}
    try:
        cortes = avaliador(validas, reg)
    except Exception as e:
        return {'estado': 'falha_avaliacao', 'motivo': f'{type(e).__name__}: {e}',
                'recusadas_nao_prospetivas': recusadas, 'promocao_operacional': False}
    # Nao se reavalia com os cortes do desenvolvimento nem com cortes anteriores ao ultimo dado de desenvolvimento.
    usados = set(str(c) for c in reg.get('cortes_desenvolvimento') or [])
    reutilizados = sorted({str(c.get('corte'))[:10] for c in cortes
                           if str(c.get('corte'))[:10] in usados or str(c.get('corte'))[:10] <= reg[
                               'data_ultima_observacao_desenvolvimento']})
    if reutilizados:
        return {'estado': 'recusada_cortes_de_desenvolvimento',
                'motivo': 'cortes nao prospetivos (usados ou anteriores ao desenvolvimento): ' + ', '.join(reutilizados),
                'recusadas_nao_prospetivas': recusadas, 'promocao_operacional': False}
    av = {'esquema': 'cortes_treino_teste', 'configuracao': reg['configuracao'],
          'n_observacoes': len(validas), 'falha_ajuste': False, 'suspeita_fuga': False,
          'validacao_causal_verificada': True, 'cortes': cortes}
    d = mic.classificar(av, pol)
    return {'estado': 'avaliada_prospetivamente' if d['validada'] else ESTADO_EM_OBSERVACAO,
            'nivel': d['nivel'], 'validada': d['validada'], 'motivos': d['motivos'], 'limitacoes': d['limitacoes'],
            'resumo': d['resumo'], 'recusadas_nao_prospetivas': recusadas, 'promocao_operacional': False,
            'politica_hash': d['politica_hash']}
