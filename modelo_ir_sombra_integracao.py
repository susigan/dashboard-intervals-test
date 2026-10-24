"""Ligacao do modo sombra ao fluxo EXISTENTE. Chamado por modelo_ir_servico.apos_deteccao DEPOIS de recalibrar().

Nao cria pipeline novo: as entradas sao lidas com os mesmos leitores de recalibrar() e as funcoes de selecao
(entradas_mmp3, carga_por_modalidade, _entradas) sao reutilizadas. A avaliacao e validar_preditivo em
'conhecido_na_epoca' com temporal= (registo prospetivo de modelo_ir_temporal). Sem esse registo (migracao nao
aplicada ou base indisponivel) NAO ha avaliacao: devolve 'historico_temporal_indisponivel' e nao escreve nada.
Escreve SO em modelo_ir_confianca_tentativas. Nunca escreve em modelo_ir_ajustes nem em referencias P0.
Nunca levanta: qualquer falha volta como {'ok': False, ...} e nao afeta a sincronizacao.
"""
import hashlib
import json

import db
import modelo_ir as ir
import modelo_ir_adaptador as ad
import modelo_ir_servico as mis
import modelo_ir_sombra as sombra
import modelo_ir_temporal as mtemp
import modelo_ir_temporal_migracao as mtmig
import modelo_ir_curvas as mcurv
import modelo_ir_curvas_migracao as mcmig
import modelo_testes as mt
import observacoes_auto as oa

MODO_AVALIACAO = 'conhecido_na_epoca'
MIGRACAO_ID = '2026-10-23_ir_confianca_001_tentativas'
MIGRACAO_TEMPORAL_ID = mtmig.MIGRACAO_ID


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode('utf-8')).hexdigest()


def _exec_base(sql, params, fetch):
    return db._exec(sql, params, fetch)


def loja_da_base():
    return sombra.LojaSombra(_exec_base, db.DRIVER)


def carregar_entradas(dados=None):
    """(historico, revisoes, atividades, curvas, marcos) - mesmas fontes de recalibrar(). Testes passam 'dados'."""
    if dados is not None:
        return dados
    atividades = db.load_activities()
    if atividades is None:
        return None
    historico = db.observacoes_auto_historico()
    revisoes = db.revisoes_testes_modelo()
    curvas = db.load_power_curves() or []
    marcos, _fonte = oa.obter_marcos()
    return historico, revisoes, atividades, curvas, marcos


def avaliacao_walk_forward(historico, revisoes, atividades, curvas, mod, dur, temporal, historico_curvas=None):
    """validar_preditivo em conhecido_na_epoca com registo temporal, so para a unidade pedida, pelo adaptador.
    Marcos, YES e valores de P0 vem SO do registo temporal (nunca dos atuais). Sem `temporal` levanta.
    Curvas do P0: so as versoes conhecidas antes do alvo (historico_curvas). Sem ele, os alvos sao rejeitados."""
    if temporal is None:
        raise ValueError('historico_temporal_indisponivel')
    res = mis.validar_preditivo(historico, revisoes, atividades, curvas, modos=(MODO_AVALIACAO,), tipo=mod,
                                duracao=dur, temporal=temporal, historico_curvas=historico_curvas)
    itens = [i for i in res.get('itens', [])
             if i.get('modalidade') == mod and i.get('duracao_s') == dur and i.get('modo') == MODO_AVALIACAO]
    if not itens:  # sem alvos para esta unidade: dados insuficientes (classificador -> C)
        return {'esquema': 'walk_forward_blocos',
                'configuracao': {'id': f'{mod}-{dur}-{MODO_AVALIACAO}', 'familia': 'completo', 'fixos': {}},
                'n_observacoes': 0, 'falha_ajuste': False, 'suspeita_fuga': False,
                'validacao_causal_verificada': True, 'cortes': [],
                'rejeicoes_por_categoria': {}, 'natureza': 'sem_alvos', 'historico_temporal': 'registo_prospetivo'}
    item = itens[0]
    out = ad.de_validacao_preditiva(item, causal_por_construcao=True)
    out['rejeicoes_por_categoria'] = item.get('rejeicoes_por_categoria') or {}
    out['natureza'] = mis.natureza_avaliacao(item, temporal_usado=True)
    out['historico_temporal'] = res.get('historico_temporal')
    return out


def preparar_unidade(mod, dur, entradas, referencia, resultado_recalibrar, temporal, historico_curvas=None):
    historico, revisoes, atividades, curvas, marcos = entradas
    # obs e a chave de identidade da tentativa (calendario de HOJE); a AVALIACAO nao usa estes marcos.
    ent = mis.entradas_mmp3(curvas, atividades, referencia, marcos, revisoes)
    obs = sorted(ent[mod][0], key=lambda x: (x['data'], x['test_id']))
    cargas, _info = mis.carga_por_modalidade(atividades)
    carga_digest = mis._entradas(obs, cargas[mod], referencia, None, None)['carga_digest']
    # Entradas auxiliares de validar_preditivo (historico de deteccao, revisoes, registo temporal): na assinatura.
    # Se o registo temporal mudar, a avaliacao anterior deixa de ser reutilizada.
    carga_sig = _sha({'carga': carga_digest, 'historico': historico, 'revisoes': revisoes,
                      'temporal': temporal.assinatura_estado(),
                      'curvas_historico': historico_curvas.assinatura_estado() if historico_curvas else None})
    p0_sig = ir.assinatura(resultado_recalibrar.get('p0_global') or {})
    info = (resultado_recalibrar.get('unidades') or {}).get(f'{mod}:{dur}') or {}
    cfg_id = f'{mod}:{dur}:{MODO_AVALIACAO}:v{ir.VERSAO_IR}'
    return {'obs': obs, 'carga_sig': carga_sig, 'p0_ref_sig': p0_sig, 'configuracao_id': cfg_id,
            'avaliar': lambda: avaliacao_walk_forward(historico, revisoes, atividades, curvas, mod, dur, temporal,
                                                      historico_curvas=historico_curvas),
            'versao_operacional_ref': f"{mod}:{dur}:v{info.get('versao_ajuste')}"}


def apos_recalibrar(resultado_recalibrar, origem, dados=None, agora=None):
    """Chamado apos recalibrar(). Devolve sumario. Nunca levanta. Nunca escreve versao operacional."""
    try:
        if not isinstance(resultado_recalibrar, dict) or not resultado_recalibrar.get('ok'):
            return {'ok': False, 'ignorado': 'recalibrar_sem_sucesso', 'escrito_operacional': False}
        if dados is None and not db.ENABLED:
            return {'ok': False, 'ignorado': 'sem_base_de_dados', 'escrito_operacional': False}
        unidades = sombra.unidades_alteradas(resultado_recalibrar)
        if not unidades:
            return {'ok': True, 'unidades': [], 'ignorado': 'sem_unidades_operacionais',
                    'escrito_operacional': False}
        loja = loja_da_base() if dados is None else dados_loja(dados)
        if not loja.tabela_existe():
            return {'ok': False, 'ignorado': 'tabela_inexistente', 'migracao_pendente': MIGRACAO_ID,
                    'escrito_operacional': False}
        # Sem historico temporal provado NAO ha avaliacao (nunca a versao atual apresentada como temporal).
        temporal, estado_t = _leitor_temporal(dados)
        if temporal is None:
            return {'ok': False, 'ignorado': 'historico_temporal_indisponivel', 'estado_temporal': estado_t,
                    'migracao_pendente': MIGRACAO_TEMPORAL_ID, 'escrito_operacional': False}
        # Historico de curvas (Etapa 13): sem ele o P0 de todos os alvos seria rejeitado. Nao se avalia nada.
        historico_curvas, estado_c = _leitor_curvas(dados)
        if historico_curvas is None:
            return {'ok': False, 'ignorado': 'historico_curvas_indisponivel', 'estado_curvas': estado_c,
                    'migracao_pendente': mcmig.MIGRACAO_ID, 'escrito_operacional': False}
        entradas = carregar_entradas(None if dados is None else dados.get('entradas'))
        if entradas is None:
            return {'ok': False, 'ignorado': 'entradas_indisponiveis', 'escrito_operacional': False}
        referencia = (agora or mt.agora_utc()).date().isoformat()

        def preparar(mod, dur):
            return preparar_unidade(mod, dur, entradas, referencia, resultado_recalibrar, temporal,
                                    historico_curvas=historico_curvas)
        return sombra.apos_deteccao_sombra(loja, {'unidades_alteradas': unidades}, origem, preparar)
    except Exception as e:
        return {'ok': False, 'erro': f'{type(e).__name__}: {e}', 'escrito_operacional': False}


def _leitor_temporal(dados):
    """(leitor | None, estado). Testes injetam dados['temporal']; producao usa o leitor real."""
    if dados is None:
        return mtemp.leitor_producao()
    t = dados.get('temporal')
    if t is None:
        return None, 'nao_injetado'
    return (t, 'ok') if t.tabelas_existem() else (None, 'tabelas_ausentes')


def _leitor_curvas(dados):
    """(HistoricoCurvas | None, estado). Testes injetam dados['historico_curvas']; producao usa o leitor real."""
    if dados is None:
        return mcurv.leitor_producao()
    h = dados.get('historico_curvas')
    if h is None:
        return None, 'nao_injetado'
    return (h, 'ok') if h.tabela_existe() else (None, 'tabelas_ausentes')


def dados_loja(dados):
    """Testes: loja injetada (mesma interface)."""
    return dados['loja']
