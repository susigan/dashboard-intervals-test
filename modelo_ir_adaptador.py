"""Adaptadores EXPLICITOS entre os formatos de validacao e o classificador modelo_ir_confianca.

Nao ha correspondencia por semelhanca de nomes: cada campo lido esta listado abaixo e, se faltar, o adaptador
falha em vez de adivinhar. Dois esquemas, nunca misturados:

  cortes_treino_teste  (rondas 3-4): um ajuste por corte, treino data<corte, teste data>=corte. Baseline
                       M0 = media do treino; M1 = ultima observacao do treino (empates ordenados arbitrariamente).
                       Diagnostico de identificabilidade POR CORTE disponivel.
  walk_forward_blocos  (validar_preditivo, modelo_validacao / modelo_ir_servico._avaliar_unidade): um ajuste POR ALVO,
                       treino data<alvo. Baseline M0 = media do treino; M1 = ultimo valor (empates de dia sem
                       hora real -> media do dia, sinalizado por m1_ordem_ambigua, NAO exposto no item).
                       Os avisos de identificabilidade POR ALVO NAO estao no item devolvido: so existe o total
                       'n_previsoes_com_avisos_identificabilidade'. Logo o eixo dos parametros fica 'nao_medido'.

Definicoes (identicas nos dois esquemas, confirmadas em modelo_validacao.metricas):
  erro = previsao - observado; MAE = media |erro|; RMSE = raiz(media erro^2).
  vitoria por corte/bloco = MAE_IR < min(MAE_M0, MAE_M1) E RMSE_IR < min(RMSE_M0, RMSE_M1).
  CV entre cortes = desvio padrao populacional / media dos MAE_IR.
"""
import math
from collections import defaultdict

CAMPOS_RONDA = ('n_treino', 'n_teste', 'mae_ir', 'rmse_ir', 'mae_media', 'rmse_media', 'mae_ult', 'rmse_ult')
CAMPOS_DIAG = ('params', 'identificavel', 'no_limite', 'cond', 'corr_max')
CAMPOS_ALVO_WF = ('test_id', 'data', 'y', 'ir', 'M0', 'M1', 'M2', 'n_treino')
CAMPOS_ITEM_WF = ('modalidade', 'duracao_s', 'modo', 'n_alvos_com_previsao_ir', 'previsoes',
                  'n_previsoes_com_avisos_identificabilidade')


class FormatoInesperado(ValueError):
    pass


def _exigir(obj, campos, origem):
    faltam = [c for c in campos if c not in obj]
    if faltam:
        raise FormatoInesperado(f'{origem}: campos ausentes {faltam}')


def _erros(pares):
    """pares: [(previsao, observado)] -> (mae, rmse) ou (None, None)."""
    if not pares:
        return None, None
    e = [p - o for p, o in pares]
    return sum(abs(x) for x in e) / len(e), math.sqrt(sum(x * x for x in e) / len(e))


def _cut(nome, n_teste, pares_ir, pares_m0, pares_m1, identificavel, no_limite, cond=None, corr=None, params=None):
    mi, ri = _erros(pares_ir)
    m0, r0 = _erros(pares_m0)
    m1, r1 = _erros(pares_m1)
    return {'corte': nome, 'n_teste': n_teste, 'mae_ir': mi, 'rmse_ir': ri, 'mae_media': m0, 'rmse_media': r0,
            'mae_ultimo': m1, 'rmse_ultimo': r1, 'identificavel': identificavel, 'no_limite': no_limite,
            'cond': cond, 'corr_max': corr, 'parametros': params or {}}


def de_cortes_ronda(entrada, nome_corte, ronda, configuracao_id, familia, fixos, validacao_causal_verificada):
    """entrada: uma celula de ronda3/ronda4 (sel, diag/diag_treino, n_treino/n, n_teste, mae_ir, ..., mae_ult).
    Devolve o corte. Nao recalcula nada: so transcreve campos com nomes explicitos."""
    _exigir(entrada, ('mae_ir', 'rmse_ir', 'n_teste', 'mae_media', 'rmse_media', 'mae_ult', 'rmse_ult'),
            f'ronda{ronda}:{nome_corte}')
    diag = entrada['diag_treino'] if ronda == 3 else entrada['diag']
    _exigir(diag, CAMPOS_DIAG, f'ronda{ronda}:{nome_corte}:diag')
    n_teste = entrada['n_teste']
    # Erros reconstruidos a partir das metricas agregadas (ja calculadas nas rondas): guardam-se como medias.
    return {'corte': nome_corte, 'n_treino': entrada.get('n_treino', diag.get('n')), 'n_teste': n_teste,
            'mae_ir': entrada['mae_ir'], 'rmse_ir': entrada['rmse_ir'],
            'mae_media': entrada['mae_media'], 'rmse_media': entrada['rmse_media'],
            'mae_ultimo': entrada['mae_ult'], 'rmse_ultimo': entrada['rmse_ult'],
            'identificavel': bool(diag['identificavel']),
            'no_limite': len(diag.get('no_limite') or []) > 0,
            'cond': diag.get('cond'), 'corr_max': diag.get('corr_max'),
            'parametros': {k: v for k, v in (diag.get('params') or {}).items() if k not in (fixos or {})},
            '_esquema_fonte': 'cortes_treino_teste', '_validacao_causal': validacao_causal_verificada}


def avaliacao_de_cortes(cortes, configuracao_id, familia, fixos, n_observacoes, validacao_causal_verificada,
                        falha_ajuste=False, suspeita_fuga=False):
    return {'esquema': 'cortes_treino_teste',
            'configuracao': {'id': configuracao_id, 'familia': familia, 'fixos': dict(fixos or {})},
            'n_observacoes': n_observacoes, 'falha_ajuste': falha_ajuste, 'suspeita_fuga': suspeita_fuga,
            'validacao_causal_verificada': validacao_causal_verificada,
            'cortes': [{k: v for k, v in c.items() if not k.startswith('_')} for c in cortes]}


def de_validacao_preditiva(item, causal_por_construcao=True, bloco='trimestre'):
    """item: uma unidade (modalidade, duracao, modo) devolvida por modelo_ir_servico._avaliar_unidade.
    Agrupa os alvos (ja restritos a quem tem previsao IR, M0 e M1: mesma populacao) em blocos temporais.
    Devolve avaliacao no esquema walk_forward_blocos. Nao devolve veredicto nem o compara com cortes."""
    _exigir(item, CAMPOS_ITEM_WF, 'validar_preditivo:item')
    if bloco != 'trimestre':
        raise FormatoInesperado('unico agrupamento suportado: trimestre')
    grupos = defaultdict(list)
    for p in item['previsoes']:
        _exigir(p, CAMPOS_ALVO_WF, 'validar_preditivo:previsao')
        if p['ir'] is None or p['M0'] is None or p['M1'] is None:
            raise FormatoInesperado('previsao sem IR ou sem baseline: a populacao deve ser comum')
        ano, mes = int(str(p['data'])[:4]), int(str(p['data'])[5:7])
        grupos[f'{ano}T{(mes - 1) // 3 + 1}'].append(p)
    cortes = []
    for nome in sorted(grupos):
        ps = grupos[nome]
        cortes.append(_cut(nome, len(ps),
                           [(p['ir'], p['y']) for p in ps], [(p['M0'], p['y']) for p in ps],
                           [(p['M1'], p['y']) for p in ps],
                           identificavel=None, no_limite=False))
    return {'esquema': 'walk_forward_blocos',
            'configuracao': {'id': f"{item['modalidade']}-{item['duracao_s']}-{item['modo']}",
                             'familia': 'completo', 'fixos': {}},
            'n_observacoes': item['n_alvos_com_previsao_ir'],
            'falha_ajuste': False, 'suspeita_fuga': False,
            'validacao_causal_verificada': bool(causal_por_construcao),
            'cortes': cortes,
            'metadados': {'agrupamento': bloco, 'veredicto_do_item_nao_usado': item.get('veredicto'),
                          'avisos_identificabilidade_por_alvo_disponiveis': False,
                          'n_previsoes_com_avisos_total': item['n_previsoes_com_avisos_identificabilidade'],
                          'populacao': 'alvos com previsao IR, M0 e M1'}}
