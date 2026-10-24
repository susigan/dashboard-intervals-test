"""Politica experimental de classificacao (niveis A/B/C) e decisao de atualizacao do IR. NUCLEO PURO.

Nao chama base de dados, rede nem o modelo_ir. Recebe resultados de validacao temporal ja calculados e devolve
uma decisao determinstica e reproduzivel. Nao substitui recalibrar() de modelo_ir_servico nem altera a producao.

Estados operacionais (decisao, nao rotulos):
  A  individualizado : K1, K2, T1, T2 estimados e identificaveis; previsao melhor que as baselines de forma estavel.
  B  regularizado    : versao reduzida (parametros FIXOS explicitos, nunca apresentados como estimados); previsao
                       melhor que as baselines de forma estavel. So e escolhida se a reducao foi selecionada no treino.
  C  provisorio      : sem vantagem suficiente, dados insuficientes ou falha. Mantem a referencia simples
                       (media do treino ou ultimo valor, a que tiver menor MAE medio) e continua a avaliar.

Confianca em dois eixos distintos:
  confianca_parametros : estabilidade e interpretabilidade de K e T (nao implica boa previsao).
  confianca_previsao   : consistencia do erro fora da amostra face as baselines.

Limiares em POLITICA: valores iniciais CONFIGURAVEIS, documentados e sujeitos a validacao. Nao sao constantes fisiologicas.
Cortes sobrepostos NAO sao amostras independentes: o numero de cortes e tratado como indicador de cobertura, nao de prova.
"""
import hashlib
import json
import math
import statistics as st

VERSAO_POLITICA = '1.0-experimental'

POLITICA = {
    'min_observacoes': 15,              # gate do solver (CONFIG_PADRAO['min_obs'])
    'min_cortes_avaliaveis': 3,         # cobertura temporal minima para decidir
    'min_obs_teste_por_corte': 5,       # um corte com menos observacoes de teste nao conta
    'min_vitorias': 3,                  # cortes em que o IR bate media e ultimo em MAE e RMSE
    'min_cortes_identificaveis': 3,     # cortes com parametros estimados identificaveis
    'cv_max': 0.5,                      # estabilidade do MAE entre cortes (desvio/media)
    'margem_limite_rel': 0.001,         # parametro a menos de 0,1% da amplitude do limite = "no limite"
}


def _hash_politica(pol):
    return hashlib.sha256(json.dumps(pol, sort_keys=True).encode('utf-8')).hexdigest()[:12]


def _cv(valores):
    if len(valores) < 2:
        return None
    m = st.mean(valores)
    return None if m == 0 else st.pstdev(valores) / m


def _cortes_validos(cortes, pol):
    return [c for c in (cortes or []) if (c.get('n_teste') or 0) >= pol['min_obs_teste_por_corte']
            and c.get('mae_ir') is not None]


def resumo_validacao(avaliacao, pol=POLITICA):
    """Agrega os cortes validos. Nao decide nada: so resume."""
    cs = _cortes_validos(avaliacao.get('cortes'), pol)
    n = len(cs)
    if n == 0:
        return {'n_cortes': 0, 'vitorias': 0, 'cv_mae': None, 'mae_ir': None, 'rmse_ir': None,
                'mae_media': None, 'mae_ultimo': None, 'cortes_identificaveis': 0}

    def win(c):
        return (c['mae_ir'] < min(c['mae_media'], c['mae_ultimo'])
                and c['rmse_ir'] < min(c['rmse_media'], c['rmse_ultimo']))
    maes = [c['mae_ir'] for c in cs]
    return {'n_cortes': n,
            'vitorias': sum(1 for c in cs if win(c)),
            'cv_mae': _cv(maes),
            'mae_ir': round(st.mean(maes), 3),
            'rmse_ir': round(st.mean(c['rmse_ir'] for c in cs), 3),
            'mae_media': round(st.mean(c['mae_media'] for c in cs), 3),
            'mae_ultimo': round(st.mean(c['mae_ultimo'] for c in cs), 3),
            'cortes_identificaveis': sum(1 for c in cs if c.get('identificavel') and not c.get('no_limite')),
            }


def confianca_parametros(resumo, pol=POLITICA):
    """Alta: identificaveis na maioria dos cortes. Media: em alguns. Baixa/nula: quase nenhum ou nenhum."""
    n, k = resumo['n_cortes'], resumo['cortes_identificaveis']
    if n == 0 or k == 0:
        return 'nula'
    if k >= pol['min_cortes_identificaveis'] and k == n:
        return 'alta'
    if k >= pol['min_cortes_identificaveis']:
        return 'media'
    return 'baixa'


def confianca_previsao(resumo, pol=POLITICA):
    if resumo['n_cortes'] == 0:
        return 'nula'
    estavel = resumo['cv_mae'] is not None and resumo['cv_mae'] <= pol['cv_max']
    if resumo['vitorias'] >= pol['min_vitorias'] and estavel:
        return 'alta' if resumo['vitorias'] == resumo['n_cortes'] else 'media'
    if resumo['vitorias'] >= 2:
        return 'baixa'
    return 'nula'


def _baseline_operacional(resumo):
    if resumo['mae_media'] is None:
        return None
    return 'media_treino' if resumo['mae_media'] <= resumo['mae_ultimo'] else 'ultimo_valor'


def classificar(avaliacao, pol=POLITICA):
    """avaliacao = {
         'configuracao': {'id', 'familia' ('completo'|'regularizado'), 'fixos': {param: valor}},
         'n_observacoes': int, 'falha_ajuste': bool, 'suspeita_fuga': bool,
         'cortes': [{'corte','n_treino','n_teste','mae_ir','rmse_ir','mae_media','rmse_media',
                     'mae_ultimo','rmse_ultimo','identificavel','no_limite'}]}
    Devolve a decisao com motivos. Determinístico: mesma entrada, mesma saida."""
    cfg = avaliacao.get('configuracao') or {'id': 'nenhuma', 'familia': 'completo', 'fixos': {}}
    fixos = dict(cfg.get('fixos') or {})
    motivos = []
    resumo = resumo_validacao(avaliacao, pol)
    base = {'configuracao': cfg['id'], 'parametros_fixos': fixos, 'politica_versao': VERSAO_POLITICA,
            'politica_hash': _hash_politica(pol),
            'n_observacoes': avaliacao.get('n_observacoes'), 'resumo': resumo}

    def fallback(motivo_lista):
        ref = _baseline_operacional(resumo)
        return {**base, 'nivel': 'C', 'estado_operacional': 'provisorio',
                'confianca_parametros': confianca_parametros(resumo, pol),
                'confianca_previsao': confianca_previsao(resumo, pol),
                'referencia_operacional': ref, 'motivos': motivo_lista}

    if avaliacao.get('falha_ajuste'):
        return fallback(['falha_ajuste'])
    if avaliacao.get('suspeita_fuga'):
        return fallback(['suspeita_fuga_informacao: candidato rejeitado'])
    if (avaliacao.get('n_observacoes') or 0) < pol['min_observacoes']:
        return fallback([f"dados_insuficientes: {avaliacao.get('n_observacoes')} < {pol['min_observacoes']} observacoes"])
    if resumo['n_cortes'] < pol['min_cortes_avaliaveis']:
        return fallback([f"cobertura_insuficiente: {resumo['n_cortes']} cortes validos < {pol['min_cortes_avaliaveis']}"])

    if resumo['cortes_identificaveis'] < pol['min_cortes_identificaveis']:
        motivos.append(f"parametros_nao_identificaveis: {resumo['cortes_identificaveis']}/{resumo['n_cortes']} cortes")
    if resumo['vitorias'] < pol['min_vitorias']:
        motivos.append(f"sem_vantagem_preditiva: {resumo['vitorias']}/{resumo['n_cortes']} cortes")
    if resumo['cv_mae'] is None or resumo['cv_mae'] > pol['cv_max']:
        motivos.append(f"instavel_entre_cortes: CV={None if resumo['cv_mae'] is None else round(resumo['cv_mae'], 3)}")
    if motivos:
        return fallback(motivos)

    if cfg.get('familia') == 'completo' and not fixos:
        nivel, estado = 'A', 'individualizado'
    else:
        nivel, estado = 'B', 'regularizado'
    return {**base, 'nivel': nivel, 'estado_operacional': estado,
            'confianca_parametros': confianca_parametros(resumo, pol),
            'confianca_previsao': confianca_previsao(resumo, pol),
            'referencia_operacional': None, 'motivos': []}


_RANK = {'A': 3, 'B': 2, 'C': 1}


def assinatura(decisao):
    """Identidade reproduzivel de uma decisao (configuracao, parametros fixos, nivel, dados, politica)."""
    conteudo = json.dumps({'cfg': decisao.get('configuracao'), 'fixos': decisao.get('parametros_fixos'),
                           'nivel': decisao.get('nivel'), 'n': decisao.get('n_observacoes'),
                           'res': decisao.get('resumo'), 'pol': decisao.get('politica_versao'),
                           'pol_hash': decisao.get('politica_hash')},
                          sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(conteudo.encode('utf-8')).hexdigest()[:16]


def decidir_atualizacao(atual, candidato):
    """Compara a versao operacional atual com um candidato. Nunca substitui por pior ou instavel.

    Devolve {'acao': 'sem_alteracao'|'promover'|'manter', 'motivo', 'versao_anterior_assinatura', 'assinatura_candidato'}.
    'sem_alteracao' quando o candidato tem a mesma assinatura (repeticao idempotente, sem novas observacoes)."""
    ass_c = assinatura(candidato)
    ass_a = assinatura(atual) if atual else None
    if atual is not None and ass_a == ass_c:
        return {'acao': 'sem_alteracao', 'motivo': 'mesma assinatura: sem novas observacoes elegiveis nem alteracao',
                'versao_anterior_assinatura': ass_a, 'assinatura_candidato': ass_c}
    if atual is None and candidato['nivel'] == 'C':
        acao, motivo = 'manter', 'sem versao anterior valida: candidato provisorio (C) nao e promovido a versao'
    elif atual is None:
        acao, motivo = 'promover', 'primeira versao'
    elif candidato['nivel'] == 'C' and atual['nivel'] != 'C':
        acao, motivo = 'manter', 'candidato provisorio nao substitui versao operacional valida'
    elif _RANK[candidato['nivel']] > _RANK[atual['nivel']]:
        acao, motivo = 'promover', f"nivel {atual['nivel']} -> {candidato['nivel']}"
    elif _RANK[candidato['nivel']] < _RANK[atual['nivel']]:
        acao, motivo = 'manter', f"nivel {candidato['nivel']} inferior ao atual {atual['nivel']}"
    else:
        score_c = (candidato['resumo']['vitorias'], -(candidato['resumo']['cv_mae'] or 9))
        score_a = (atual['resumo']['vitorias'], -(atual['resumo']['cv_mae'] or 9))
        acao, motivo = ('promover', 'mesmo nivel e melhor estabilidade/vitorias') if score_c > score_a \
            else ('manter', 'mesmo nivel sem melhoria suficiente')
    return {'acao': acao, 'motivo': motivo,
            'versao_anterior_assinatura': ass_a, 'assinatura_candidato': ass_c}


def registo_versao(modalidade, duracao_s, decisao, decisao_atualizacao, versao_codigo):
    """Registo versionado (sem persistencia aqui). Nao apaga historico: quem grava acrescenta uma linha."""
    return {'modalidade': modalidade, 'duracao_s': duracao_s, 'nivel': decisao['nivel'],
            'estado_operacional': decisao['estado_operacional'], 'configuracao': decisao['configuracao'],
            'parametros_fixos': decisao['parametros_fixos'], 'n_observacoes': decisao['n_observacoes'],
            'confianca_parametros': decisao['confianca_parametros'], 'confianca_previsao': decisao['confianca_previsao'],
            'resumo_validacao': decisao['resumo'], 'referencia_operacional': decisao['referencia_operacional'],
            'motivos': decisao['motivos'], 'acao': decisao_atualizacao['acao'], 'motivo_acao': decisao_atualizacao['motivo'],
            'assinatura': decisao_atualizacao['assinatura_candidato'],
            'versao_anterior_assinatura': decisao_atualizacao['versao_anterior_assinatura'],
            'politica_versao': decisao['politica_versao'], 'politica_hash': decisao['politica_hash'],
            'versao_codigo': versao_codigo}
