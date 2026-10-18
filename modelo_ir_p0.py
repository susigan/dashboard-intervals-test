"""P0 do modelo IR = CP do resultado SELECIONADO e GUARDADO no CP Model (modulo PURO: sem base nem rede).

Regra (decisao final)
---------------------
    P0 = CP

O P0 de uma modalidade e EXATAMENTE o valor de CP (`cp_w`, em W) do resultado que o CP Model guardou
(tabela `cp_resultados`, o mesmo "instantaneo gravado" que /api/cp/actual/<modalidade> devolve como
confirmado). Nao e calculado nem estimado: nada de CP + W'/d (interpretacao rejeitada), MMP, mediana das
observacoes, outro modelo escolhido automaticamente ou parametro livre do Solver.

  * A DURACAO do IR (60/300/1200 s) NAO entra: identifica o alvo e o ajuste, nunca o P0. O mesmo CP e o P0
    de todas as duracoes da modalidade.
  * W' NAO entra: uma alteracao so de W' (mesmo CP, mesmo modelo, mesma data de referencia) nao cria
    referencia nova nem recalibra. W' e a sua plausibilidade ficam registados so como informacao.
  * O modelo de menor SEE% nunca e escolhido aqui: usa-se o `modelo_escolhido` gravado. O CP Model nao
    distingue "escolha explicita" de "default gravado"; esta limitacao mantem-se e o IR nao inventa uma
    confirmacao (`coincide_com_menor_see_classico` e so informacao).
  * Sem resultado guardado valido nao ha P0: o chamador nao cria ajuste e diz o motivo. Nunca ha P0 de
    substituicao.

O que bloqueia (so o que a matematica do proprio resultado justifica)
---------------------------------------------------------------------
  * nao ha resultado gravado para a modalidade;
  * o resultado nao identifica o modelo;
  * CP ausente, nao finito ou nao positivo;
  * CP da linha diferente do CP que o `modelos_json` guarda para o mesmo modelo (integridade da gravacao);
  * graus de liberdade do ajuste do CP <= 0 (n_pts <= k_params): o CP seria uma interpolacao exata, sem erro
    residual nem SEE%, nao uma estimativa.
Avisos (nao bloqueiam; ficam na referencia): modelo de 3 parametros, W' fora de WPRIME_PLAUSIVEL do CP Model,
modelo ausente em `modelos_json`, graus de liberdade nao verificaveis.

Nao ha limiar de SEE%: o CP Model mostra o SEE% para o utilizador decidir e nao define um corte; escolher um
aqui seria arbitrario. O SEE% fica registado na referencia.
"""
import math
import os
import sys

import modelo_ir as ir

VERSAO_P0 = 2
FORMULA_ID = 'p0_igual_cp'
EQUACAO = 'P0 = CP  [CP do resultado selecionado e guardado no CP Model, em W]'

# Modelos de 3 parametros do CP Model: o CP e o do modelo gravado; o aviso lembra que e outro modelo.
MODELOS_3P = ('3p hiperbolico', 'Ward-Smith', 'OM3CP', 'OMExp')

# motivos de bloqueio (estaveis: a UI e os testes usam-nos)
M_SEM_INSTANTANEO = 'sem_resultado_cp_confirmado'
M_SEM_MODELO = 'modelo_nao_registado'
M_CP = 'cp_invalido'
M_INCONSISTENTE = 'cp_inconsistente_com_modelos'
M_GL = 'graus_de_liberdade_cp_insuficientes'
M_P0 = 'p0_invalido'

TEXTOS = {
    M_SEM_INSTANTANEO: 'nao ha resultado do CP Model gravado (confirmado) para esta modalidade; o calculo '
                       '"agora" do CP Model nao e confirmacao e o modelo de menor SEE% nao e usado',
    M_SEM_MODELO: 'o resultado gravado nao identifica o modelo escolhido',
    M_CP: 'CP do resultado gravado ausente, nao finito ou nao positivo',
    M_INCONSISTENTE: 'o CP do resultado difere do CP guardado para o mesmo modelo',
    M_GL: 'o ajuste do CP Model nao tem graus de liberdade (n_pts <= k_params): o CP seria uma interpolacao '
          'exata, nao uma estimativa',
    M_P0: 'o P0 (= CP) nao e um numero finito positivo',
}


def _cp_model():
    """Constantes do CP Model (so leitura). Importado sem alterar nada nele."""
    u = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'utils')
    if u not in sys.path:
        sys.path.insert(0, u)
    import cp_model as cpm
    return cpm


def _num(v):
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and v > 0)


def _json(v):
    if isinstance(v, str):
        import json
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def instantaneo_em_vigor(modalidade, instantaneos):
    """O instantaneo gravado mais recente da modalidade com CP (a mesma ordem do /api/cp/actual:
    data_referencia DESC; desempate pela data de gravacao e pelo id). None se nao houver."""
    cand = [r for r in instantaneos or [] if isinstance(r, dict) and r.get('modalidade') == modalidade
            and r.get('cp_w') is not None]
    if not cand:
        return None
    return max(cand, key=lambda r: (str(r.get('data_referencia') or ''), str(r.get('data_gravacao') or ''),
                                    int(r.get('id') or 0)))


def _ref_instantaneo(r):
    return {'id': r.get('id'), 'modalidade': r.get('modalidade'), 'season': r.get('season'),
            'data_referencia': str(r.get('data_referencia'))[:10] if r.get('data_referencia') else None,
            'data_gravacao': str(r.get('data_gravacao')) if r.get('data_gravacao') else None,
            'modelo': r.get('modelo_escolhido'), 'origem': r.get('origem')}


def _bloqueio(motivo, modalidade, duracao_s, inst=None, extra=None):
    d = {'valida': False, 'motivo': motivo, 'detalhe': TEXTOS.get(motivo, motivo),
         'modalidade': modalidade, 'duracao_s': duracao_s, 'valor_p0': None}
    if inst is not None:
        d['instantaneo'] = _ref_instantaneo(inst)
    if extra:
        d.update(extra)
    return d


def assinatura_referencia(modalidade, modelo, cp, data_referencia):
    """Assinatura da REFERENCIA de P0 (o que a identifica): modalidade, modelo selecionado, CP e a data de
    referencia do resultado. Nao entram W', SEE%, pontos nem a duracao do IR. Uma gravacao nova (nova data de
    referencia, ex. a revisao anual) e uma referencia nova mesmo com o mesmo CP; repetir a mesma gravacao
    ou mudar so W' (mesma data) nao o e."""
    return ir.assinatura({'v': VERSAO_P0, 'f': FORMULA_ID, 'mod': modalidade, 'modelo': modelo,
                          'cp': float(cp), 'data_ref': str(data_referencia)[:10] if data_referencia else None})


def resolver_p0(modalidade, duracao_s, instantaneos):
    """P0 (= CP) da modalidade ou o motivo do bloqueio. Puro e deterministico.

    duracao_s so identifica a unidade do IR (e devolve-se tal e qual): NAO altera o P0 nem a validade.
    instantaneos: linhas de cp_resultados como dicts (id, modalidade, season, data_referencia,
    data_gravacao, modelo_escolhido, cp_w, wp_j, see_pct, n_pts, k_params, modelos_json, origem, ...).
    """
    inst = instantaneo_em_vigor(modalidade, instantaneos)
    if inst is None:
        return _bloqueio(M_SEM_INSTANTANEO, modalidade, duracao_s)
    modelo = inst.get('modelo_escolhido')
    if not modelo or not isinstance(modelo, str):
        return _bloqueio(M_SEM_MODELO, modalidade, duracao_s, inst)
    cp = inst.get('cp_w')
    if not _num(cp):
        return _bloqueio(M_CP, modalidade, duracao_s, inst, {'modelo_cp': modelo})
    avisos = []
    modelos = _json(inst.get('modelos_json')) or {}
    m = modelos.get(modelo) if isinstance(modelos, dict) else None
    if isinstance(m, dict) and m.get('cp') is not None:
        try:
            # modelos_json guarda o cp com 1 casa decimal (arredondado): tolerancia de arredondamento
            if abs(float(m['cp']) - float(cp)) > 0.051:
                return _bloqueio(M_INCONSISTENTE, modalidade, duracao_s, inst, {'modelo_cp': modelo})
        except (TypeError, ValueError):
            return _bloqueio(M_INCONSISTENTE, modalidade, duracao_s, inst, {'modelo_cp': modelo})
    else:
        avisos.append('modelo_ausente_em_modelos_json')
    n_pts, k = inst.get('n_pts'), inst.get('k_params')
    if isinstance(n_pts, (int, float)) and isinstance(k, (int, float)) and not isinstance(n_pts, bool) \
            and not isinstance(k, bool):
        if n_pts <= k:
            return _bloqueio(M_GL, modalidade, duracao_s, inst,
                             {'modelo_cp': modelo, 'n_pts': n_pts, 'k_params': k})
    else:
        avisos.append('graus_de_liberdade_cp_nao_verificaveis')
    if modelo in MODELOS_3P:
        avisos.append('modelo_de_3_parametros')
    wp = inst.get('wp_j')
    lo_w, hi_w = _cp_model().WPRIME_PLAUSIVEL
    if not (_num(wp) and lo_w <= float(wp) <= hi_w):
        avisos.append('wprime_ausente_ou_fora_do_intervalo_plausivel')
    p0 = float(cp)
    if not _num(p0):
        return _bloqueio(M_P0, modalidade, duracao_s, inst, {'modelo_cp': modelo})
    classicos = {n: x for n, x in modelos.items()
                 if isinstance(x, dict) and x.get('classico') and isinstance(x.get('see_pct'), (int, float))}
    menor = min(classicos, key=lambda n: classicos[n]['see_pct']) if classicos else None
    usados = (m or {}).get('pontos_usados') if isinstance(m, dict) else None
    base = {'modalidade': modalidade, 'duracao_s': duracao_s, 'formulacao': FORMULA_ID, 'equacao': EQUACAO,
            'modelo_cp': modelo, 'cp_w': p0, 'wp_j': float(wp) if _num(wp) else None,
            'see_pct': inst.get('see_pct'), 'n_pts': n_pts, 'k_params': k,
            'pontos_usados': [{'t': p.get('t'), 'w': p.get('w')} for p in usados or [] if isinstance(p, dict)],
            'avisos_cp': avisos,
            'instantaneo': _ref_instantaneo(inst),
            'coincide_com_menor_see_classico': (menor == modelo) if menor else None,
            'valor_p0': p0}              # EXATAMENTE o CP: sem arredondar nem somar nada
    base['assinatura'] = assinatura_referencia(modalidade, modelo, p0, inst.get('data_referencia'))
    base['valida'] = True
    base['motivo'] = None
    return base
