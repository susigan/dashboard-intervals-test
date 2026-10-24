"""Classificador experimental A/B/C do IR com DOIS EIXOS separados. NUCLEO PURO (sem base de dados, rede ou modelo_ir).

Eixo 1 - confianca nos PARAMETROS (interpretabilidade, nao implica previsao boa):
  identificabilidade por corte, parametros no limite, condicionamento/correlacao, estabilidade (CV) dos
  parametros livres entre cortes, e proporcao de parametros fixados por escolha explicita.
  Nivel: alta | media | baixa | restrito (ha parametros fixados) | nao_medido (esquema sem diagnostico por corte).

Eixo 2 - confianca PREDITIVA (validacao fora da amostra):
  MAE/RMSE fora da amostra face a media de treino (M0) e ao ultimo valor (M1), vitorias por corte, CV do MAE entre
  cortes, cobertura (observacoes e cortes) e validade causal / sinais de fuga.
  Nivel: alta | media | baixa | nula. 'validada' = criterios de previsao cumpridos.

Estados operacionais (confianca OPERACIONAL, nao afirmam certeza fisiologica):
  A  previsao validada E parametros nao fixados e identificaveis (fracao >= frac_identificavel_A, sem limites, CV ok).
  B  previsao validada, mas parametros restritos, nao identificaveis, no limite ou nao medidos. As limitacoes
     ficam explicitas em 'limitacoes'. A falta de identificabilidade NAO invalida a previsao validada.
  C  previsao nao demonstrada (sem vantagem, instavel), dados insuficientes, cobertura insuficiente, falha de ajuste
     ou validacao nao causal / com suspeita de fuga. Mantem referencia simples e nao e promovido.

Politica 2.0 (esta). A politica 1.0 (identificabilidade como porta para C) esta congelada em modelo_ir_confianca_v1.py
para comparacao sobre a mesma fixture. Limiares sao POLITICA configuravel, nao constantes fisiologicas.
Cortes sobrepostos nao sao amostras independentes; o numero de cortes indica cobertura, nao prova.
"""
import hashlib
import json
import math
import statistics as st

VERSAO_POLITICA = '2.0-eixos-separados'
ESQUEMAS = ('cortes_treino_teste', 'walk_forward_blocos')

POLITICA = {
    'min_observacoes': 15,
    'min_cortes_avaliaveis': 3,
    'min_obs_teste_por_corte': 5,
    'min_vitorias': 3,
    'cv_max': 0.5,                  # estabilidade preditiva entre cortes (CV do MAE)
    'frac_identificavel_A': 0.75,   # A: parametros livres identificaveis em >= 75% dos cortes com diagnostico
    'frac_identificavel_media': 0.5,
    'cv_parametros_max': 0.5,       # estabilidade dos parametros livres entre cortes identificaveis
    'n_parametros': 5,              # p0, k1, k2, t1, t2
    'margem_limite_rel': 0.001,
}


def _hash_politica(pol):
    return hashlib.sha256(json.dumps(pol, sort_keys=True).encode('utf-8')).hexdigest()[:12]


def _cv(valores):
    valores = [v for v in valores if v is not None]
    if len(valores) < 2:
        return None
    m = st.mean(valores)
    return None if m == 0 else st.pstdev(valores) / m


def _cortes_validos(cortes, pol):
    return [c for c in (cortes or []) if (c.get('n_teste') or 0) >= pol['min_obs_teste_por_corte']
            and c.get('mae_ir') is not None]


def _win(c):
    return (c['mae_ir'] < min(c['mae_media'], c['mae_ultimo'])
            and c['rmse_ir'] < min(c['rmse_media'], c['rmse_ultimo']))


def resumo_validacao(avaliacao, pol=POLITICA):
    """Eixo preditivo: agrega cortes validos. Nao decide nada."""
    cs = _cortes_validos(avaliacao.get('cortes'), pol)
    if not cs:
        return {'n_cortes': 0, 'vitorias': 0, 'cv_mae': None, 'mae_ir': None, 'rmse_ir': None,
                'mae_media': None, 'mae_ultimo': None, 'rmse_media': None, 'rmse_ultimo': None,
                'n_obs_teste': 0}
    maes = [c['mae_ir'] for c in cs]
    return {'n_cortes': len(cs),
            'vitorias': sum(1 for c in cs if _win(c)),
            'cv_mae': _cv(maes),
            'mae_ir': round(st.mean(maes), 3),
            'rmse_ir': round(st.mean(c['rmse_ir'] for c in cs), 3),
            'mae_media': round(st.mean(c['mae_media'] for c in cs), 3),
            'rmse_media': round(st.mean(c['rmse_media'] for c in cs), 3),
            'mae_ultimo': round(st.mean(c['mae_ultimo'] for c in cs), 3),
            'rmse_ultimo': round(st.mean(c['rmse_ultimo'] for c in cs), 3),
            'n_obs_teste': sum(c['n_teste'] for c in cs)}


def avaliar_parametros(cfg, cortes, pol=POLITICA):
    """Eixo dos parametros. cortes: cortes validos. Campos por corte usados (todos opcionais exceto identificavel):
    identificavel (bool|None), no_limite (bool), cond, corr_max, parametros ({nome: valor}, livres)."""
    fixos = [k for k in (cfg.get('fixos') or {}) if k != 'nota']
    n_fixos = len(fixos)
    com_diag = [c for c in cortes if c.get('identificavel') is not None]
    base = {'n_fixos': n_fixos, 'fracao_fixados': round(n_fixos / pol['n_parametros'], 3),
            'parametros_fixos': sorted(fixos), 'cortes_com_diagnostico': len(com_diag),
            'cortes_identificaveis': 0, 'cortes_no_limite': 0, 'fracao_identificavel': None,
            'cond_max': None, 'corr_max': None, 'cv_parametros_max': None}
    if not com_diag:
        return {**base, 'nivel': 'nao_medido'}
    ident = sum(1 for c in com_diag if c['identificavel'] and not c.get('no_limite'))
    no_lim = sum(1 for c in com_diag if c.get('no_limite'))
    conds = [c['cond'] for c in com_diag if c.get('cond') is not None and math.isfinite(c['cond'])]
    corrs = [c['corr_max'] for c in com_diag if c.get('corr_max') is not None]
    livres = sorted({k for c in com_diag for k in (c.get('parametros') or {}) if k not in fixos})
    cvs = []
    for nome in livres:
        vals = [c['parametros'][nome] for c in com_diag if c['identificavel'] and nome in (c.get('parametros') or {})]
        cv = _cv(vals)
        if cv is not None:
            cvs.append(cv)
    cv_p = max(cvs) if cvs else None
    frac = ident / len(com_diag)
    base.update({'cortes_identificaveis': ident, 'cortes_no_limite': no_lim,
                 'fracao_identificavel': round(frac, 3),
                 'cond_max': max(conds) if conds else None, 'corr_max': max(corrs) if corrs else None,
                 'cv_parametros_max': None if cv_p is None else round(cv_p, 3)})
    estavel = cv_p is None or cv_p <= pol['cv_parametros_max']
    if n_fixos > 0:
        nivel = 'restrito'
    elif frac >= pol['frac_identificavel_A'] and no_lim == 0 and estavel:
        nivel = 'alta'
    elif frac >= pol['frac_identificavel_media'] and no_lim == 0:
        nivel = 'media'
    else:
        nivel = 'baixa'
    return {**base, 'nivel': nivel}


def avaliar_previsao(resumo, pol=POLITICA):
    """Eixo preditivo: nivel e validade. 'validada' so depende de vitorias, estabilidade e cobertura."""
    n, v = resumo['n_cortes'], resumo['vitorias']
    estavel = resumo['cv_mae'] is not None and resumo['cv_mae'] <= pol['cv_max']
    if n == 0:
        nivel = 'nula'
    elif v >= pol['min_vitorias'] and estavel:
        nivel = 'alta' if v == n else 'media'
    elif v >= 2:
        nivel = 'baixa'
    else:
        nivel = 'nula'
    return {'nivel': nivel, 'vitorias': v, 'n_cortes': n, 'estavel': estavel}


def _referencia(resumo):
    if resumo.get('mae_media') is None:
        return None
    return 'media_treino' if resumo['mae_media'] <= resumo['mae_ultimo'] else 'ultimo_valor'


def classificar(avaliacao, pol=POLITICA):
    """Entrada: {'esquema', 'configuracao': {'id','familia','fixos'}, 'n_observacoes', 'falha_ajuste',
    'suspeita_fuga', 'validacao_causal_verificada', 'cortes': [...]}. Determinista."""
    esquema = avaliacao.get('esquema')
    if esquema not in ESQUEMAS:
        raise ValueError(f'esquema de validacao desconhecido ou ausente: {esquema!r}')
    cfg = avaliacao.get('configuracao') or {'id': 'nenhuma', 'familia': 'completo', 'fixos': {}}
    cortes = _cortes_validos(avaliacao.get('cortes'), pol)
    resumo = resumo_validacao(avaliacao, pol)
    par = avaliar_parametros(cfg, cortes, pol)
    prev = avaliar_previsao(resumo, pol)
    base = {'esquema': esquema, 'configuracao': cfg['id'], 'parametros_fixos': dict(cfg.get('fixos') or {}),
            'politica_versao': VERSAO_POLITICA, 'politica_hash': _hash_politica(pol),
            'n_observacoes': avaliacao.get('n_observacoes'), 'resumo': resumo,
            'confianca_parametros': par['nivel'], 'detalhe_parametros': par,
            'confianca_previsao': prev['nivel'], 'detalhe_previsao': prev}

    def c(motivos):
        return {**base, 'nivel': 'C', 'estado_operacional': 'provisorio', 'validada': False,
                'referencia_operacional': _referencia(resumo), 'motivos': motivos, 'limitacoes': []}

    if avaliacao.get('falha_ajuste'):
        return c(['falha_ajuste'])
    if avaliacao.get('suspeita_fuga') or not avaliacao.get('validacao_causal_verificada'):
        return c(['validacao_nao_causal_ou_suspeita_fuga'])
    n_obs = avaliacao.get('n_observacoes') or 0
    if n_obs < pol['min_observacoes']:
        return c([f"dados_insuficientes: {n_obs} < {pol['min_observacoes']} observacoes"])
    if resumo['n_cortes'] < pol['min_cortes_avaliaveis']:
        return c([f"cobertura_insuficiente: {resumo['n_cortes']} cortes validos < {pol['min_cortes_avaliaveis']}"])
    if prev['vitorias'] < pol['min_vitorias']:
        return c([f"sem_vantagem_preditiva: {prev['vitorias']}/{resumo['n_cortes']} cortes"])
    if not prev['estavel']:
        cv = resumo['cv_mae']
        return c([f"instavel_entre_cortes: CV={None if cv is None else round(cv, 3)}"])

    # Previsao validada. Os limites so decidem A versus B.
    lim = []
    if par['nivel'] == 'nao_medido':
        lim.append('parametros_nao_medidos_neste_esquema')
    if par['n_fixos']:
        lim.append(f"parametros_fixos_explicitos: {','.join(par['parametros_fixos'])}")
    if par['cortes_com_diagnostico'] and par['fracao_identificavel'] is not None \
            and par['fracao_identificavel'] < pol['frac_identificavel_A']:
        lim.append(f"parametros_nao_identificaveis_em_todos_os_cortes: "
                   f"{par['cortes_identificaveis']}/{par['cortes_com_diagnostico']}")
    if par['cortes_no_limite']:
        lim.append(f"parametros_no_limite_em: {par['cortes_no_limite']} cortes")
    if par['nivel'] == 'alta' and cfg.get('familia') == 'completo' and not par['n_fixos']:
        nivel, estado = 'A', 'individualizado'
    else:
        nivel, estado = 'B', 'regularizado_ou_com_limitacoes'
    return {**base, 'nivel': nivel, 'estado_operacional': estado, 'validada': True,
            'referencia_operacional': None, 'motivos': [], 'limitacoes': lim}


_RANK = {'A': 3, 'B': 2, 'C': 1}


def assinatura(decisao):
    """Identidade reproduzivel: configuracao, fixos, nivel, dados, resumo, politica e esquema."""
    conteudo = json.dumps({'cfg': decisao.get('configuracao'), 'fixos': decisao.get('parametros_fixos'),
                           'nivel': decisao.get('nivel'), 'n': decisao.get('n_observacoes'),
                           'res': decisao.get('resumo'), 'esq': decisao.get('esquema'),
                           'pol': decisao.get('politica_versao'), 'pol_hash': decisao.get('politica_hash')},
                          sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(conteudo.encode('utf-8')).hexdigest()[:16]


def decidir_atualizacao(atual, candidato):
    """Compara com a versao operacional atual. Nunca substitui por pior, por provisorio ou por instavel.
    Devolve {'acao': 'sem_alteracao'|'promover'|'manter', 'motivo', 'versao_anterior_assinatura', 'assinatura_candidato'}."""
    ass_c = assinatura(candidato)
    ass_a = assinatura(atual) if atual else None
    if atual is not None and ass_a == ass_c:
        return {'acao': 'sem_alteracao', 'motivo': 'mesma assinatura: sem alteracao', 'versao_anterior_assinatura': ass_a,
                'assinatura_candidato': ass_c}
    if atual is not None and atual.get('esquema') != candidato.get('esquema'):
        return {'acao': 'manter', 'motivo': 'esquemas de validacao diferentes: nao comparaveis',
                'versao_anterior_assinatura': ass_a, 'assinatura_candidato': ass_c}
    if atual is None and candidato['nivel'] == 'C':
        acao, motivo = 'manter', 'sem versao anterior valida: candidato C nao e promovido'
    elif atual is None:
        acao, motivo = 'promover', 'primeira versao'
    elif candidato['nivel'] == 'C' and atual['nivel'] != 'C':
        acao, motivo = 'manter', 'candidato provisorio nao substitui versao operacional valida'
    elif _RANK[candidato['nivel']] > _RANK[atual['nivel']]:
        acao, motivo = 'promover', f"nivel {atual['nivel']} -> {candidato['nivel']}"
    elif _RANK[candidato['nivel']] < _RANK[atual['nivel']]:
        acao, motivo = 'manter', f"nivel {candidato['nivel']} inferior ao atual {atual['nivel']}"
    else:
        sc = (candidato['resumo']['vitorias'], -(candidato['resumo']['cv_mae'] or 9))
        sa = (atual['resumo']['vitorias'], -(atual['resumo']['cv_mae'] or 9))
        acao, motivo = ('promover', 'mesmo nivel e melhor estabilidade/vitorias') if sc > sa \
            else ('manter', 'mesmo nivel sem melhoria suficiente')
    return {'acao': acao, 'motivo': motivo, 'versao_anterior_assinatura': ass_a, 'assinatura_candidato': ass_c}


def registo_versao(modalidade, duracao_s, decisao, comparacao, versao_codigo):
    return {'modalidade': modalidade, 'duracao_s': duracao_s, 'esquema': decisao['esquema'],
            'nivel': decisao['nivel'], 'estado_operacional': decisao['estado_operacional'],
            'configuracao': decisao['configuracao'], 'parametros_fixos': decisao['parametros_fixos'],
            'n_observacoes': decisao['n_observacoes'], 'confianca_parametros': decisao['confianca_parametros'],
            'confianca_previsao': decisao['confianca_previsao'], 'resumo_validacao': decisao['resumo'],
            'limitacoes': decisao['limitacoes'], 'motivos': decisao['motivos'],
            'referencia_operacional': decisao['referencia_operacional'],
            'acao': comparacao['acao'], 'motivo_acao': comparacao['motivo'],
            'assinatura': comparacao['assinatura_candidato'],
            'versao_anterior_assinatura': comparacao['versao_anterior_assinatura'],
            'politica_versao': decisao['politica_versao'], 'politica_hash': decisao['politica_hash'],
            'versao_codigo': versao_codigo}
