"""Etapa 17: produtor prospetivo D+1 da sombra IR (180 s) e associacao posterior dos resultados.

Fluxo (todo LEITURA das entradas; a unica escrita e em modelo_ir_sombra_previsoes / _resultados):
  1. Entradas conhecidas no instante de calculo `agora` (hoje): observacoes de 180 s ate hoje, cargas ate hoje
     (a carga de hoje e a que se conhece agora; fica marcada como 'carga_dia_aberto'), P0 pelo resolver PURO.
  2. Previsao de amanha com a MESMA ferramenta da validacao: ajustar_observacoes + prever, lag >= 1.
  3. Registo append-only com o instante de calculo; nunca altera uma previsao.
  4. Associacao: um resultado so se associa a previsao quando existe EXATAMENTE UM teste atual (modalidade, duracao,
     data = dia-alvo). Zero -> 'sem_resultado'. Dois ou mais -> 'ambiguo' (nada associado).
     O instante do resultado e `detetado_em` da versao atual (o momento em que o sistema o conheceu).

Regras:
  * Nunca usa observacoes, cargas, curvas ou revisoes com data posterior ao instante de calculo.
  * Nunca chama garantir_referencia_p0 (escreve). Usa resolver_p0 (puro) e le o YES historico.
  * Nunca escreve em modelo_ir_ajustes nem em ultimo_ajuste_ir. Nunca promove. Nunca levanta para o sync.
"""
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

import modelo_ir as ir
import modelo_ir_p0 as p0m
import modelo_ir_previsoes as mp
import modelo_ir_previsoes_migracao as mpm
import modelo_observacoes_auto as moa

DURACAO_PROSPETIVA = 180          # o IR operacional (MMP3). Ver a limitacao sobre 60/300/1200 no relatorio.
MOTIVO_SEM_OBS = 'sem_observacoes_conhecidas'
MOTIVO_SEM_P0 = 'sem_p0_valido'


def _hoje(agora):
    if agora.tzinfo is not None:
        agora = agora.astimezone(timezone.utc).replace(tzinfo=None)
    return agora.date()


def _digest_cargas(cargas):
    return hashlib.sha256(json.dumps(sorted((k[:10], round(float(v), 6)) for k, v in cargas.items()),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def calcular_previsao_d1(agora, mod, obs, cargas, p0_valor, cfg=None, p0_motivo=None, dur=DURACAO_PROSPETIVA):
    """Previsao para amanha com entradas conhecidas ate hoje. Pura (nao le nem escreve nada).
    Devolve kwargs para mp.registar_previsao (com previsao_ir=None e motivo_bloqueio quando bloqueada)."""
    agora = agora.replace(tzinfo=None) if agora.tzinfo is not None else agora
    hoje = _hoje(agora).isoformat()
    alvo = (_hoje(agora) + timedelta(days=1)).isoformat()
    cfg_ir = ir._cfg(cfg)
    lag = int(cfg_ir['lag_dias'])
    base = dict(calculado_em=agora, data_alvo=alvo, modalidade=mod, duracao_s=int(dur),
                versao_modelo=f'ir-v{ir.VERSAO_IR}', lag_dias=lag)

    def bloqueada(estado, motivo, carga_ate=None):
        sig = mp.entradas_assinatura(modelo='bloqueio', motivo=motivo, hoje=hoje, carga_ate=carga_ate,
                                     cfg=ir.assinatura(cfg_ir), versao=ir.VERSAO_IR)
        return {**base, 'entradas_sig': sig, 'estado_temporal': estado, 'previsao_ir': None,
                'parametros': None, 'motivo_bloqueio': motivo}

    if lag < 1:
        return bloqueada('indisponivel', 'lag_invalido')
    obs_c = [o for o in (obs or []) if str(o['data'])[:10] <= hoje and math.isfinite(float(o['watts']))]
    cargas_c = {k[:10]: float(v) for k, v in (cargas or {}).items()
                if k[:10] <= hoje and v is not None and math.isfinite(float(v))}
    carga_ate = max(cargas_c) if cargas_c else None
    if not obs_c:
        return bloqueada('vazio', MOTIVO_SEM_OBS, carga_ate)
    if p0_valor is None or not math.isfinite(float(p0_valor)):
        return bloqueada('indisponivel', p0_motivo or MOTIVO_SEM_P0, carga_ate)
    r = ir.ajustar_observacoes(obs_c, cargas_c, hoje, cfg_ir, p0_fixo=float(p0_valor))
    if r.get('parametros') is None:
        return bloqueada('indisponivel', r.get('motivo') or r.get('estado') or 'ajuste_sem_parametros', carga_ate)
    previsao = ir.prever(cargas_c, r['parametros'], alvo, lag)
    if previsao is None or not math.isfinite(previsao):
        return bloqueada('indisponivel', 'sem_carga_para_previsao', carga_ate)
    entradas = sorted((str(o.get('test_id')), str(o['data'])[:10], round(float(o['watts']), 6)) for o in obs_c)
    sig = mp.entradas_assinatura(obs=entradas, carga=_digest_cargas(cargas_c), p0=float(p0_valor),
                                 cfg=ir.assinatura(cfg_ir), versao=ir.VERSAO_IR, lag=lag, hoje=hoje,
                                 carga_ate=carga_ate)
    detalhe = {'parametros': r['parametros'], 'p0': float(p0_valor), 'n_observacoes': len(obs_c),
               'carga_ate': carga_ate, 'carga_dia_aberto': carga_ate == hoje}
    return {**base, 'entradas_sig': sig, 'estado_temporal': 'disponivel', 'previsao_ir': float(previsao),
            'parametros': detalhe, 'motivo_bloqueio': None}


def preparar_entradas(historico, revisoes, atividades, curvas, marcos, agora, historico_yes=None):
    """Entradas do produtor, so leitura: MMP3 ate hoje, cargas e P0 pelo resolver puro. Nada e escrito."""
    import modelo_ir_servico as mis
    hoje = _hoje(agora).isoformat()
    entradas = mis.entradas_mmp3(curvas, atividades, hoje, marcos, revisoes)
    rejeitados = mis.rejeitados_manualmente(revisoes)
    global_ = mis.p0_global_das_entradas(entradas, historico_yes, rejeitados)
    cargas, _info = mis.carga_por_modalidade(atividades)
    out = {}
    for mod in ('Bike', 'Row', 'Ski', 'Run'):
        res = p0m.resolver_p0(mod, DURACAO_PROSPETIVA, global_)
        out[mod] = {'obs': sorted(entradas[mod][0], key=lambda x: (x['data'], x['test_id'])),
                    'cargas': cargas.get(mod, {}),
                    'p0_valor': res['valor_p0'] if res.get('valida') else None,
                    'p0_motivo': None if res.get('valida') else res.get('motivo')}
    return out


def produzir(loja, agora, entradas_por_mod, cfg=None):
    """Calcula e regista a previsao D+1 de cada modalidade. Nunca altera previsoes ja registadas."""
    saida = []
    for mod, e in sorted(entradas_por_mod.items()):
        kw = calcular_previsao_d1(agora, mod, e['obs'], e['cargas'], e['p0_valor'], cfg=cfg,
                                  p0_motivo=e.get('p0_motivo'))
        r = mp.registar_previsao(loja, **kw)
        saida.append({'modalidade': mod, 'duracao_s': DURACAO_PROSPETIVA, 'estado': kw['estado_temporal'],
                      'motivo_bloqueio': kw['motivo_bloqueio'], **r})
    return saida


def candidato_do_alvo(historico, mod, dur, data_alvo):
    """Regra de associacao. ('unico', observacao) | ('sem_resultado', None) | ('ambiguo', None).
    Atuais = ultima versao de cada teste, se 'detetada'. So conta a mesma modalidade, duracao e dia."""
    d = str(data_alvo)[:10]
    atuais = [o for o in moa.atuais(historico or [])
              if o.get('modalidade') == mod and int(o.get('duracao_s') or 0) == int(dur)
              and str(o.get('data'))[:10] == d]
    if not atuais:
        return 'sem_resultado', None
    if len(atuais) > 1:
        return 'ambiguo', None
    return 'unico', atuais[0]


def associar_pendentes(loja, historico, agora):
    """Associa resultados a previsoes cujo dia-alvo ja chegou. Revisoes geram versoes novas (a previsao fica)."""
    hoje = _hoje(agora).isoformat()
    saida = []
    for p in loja.previsoes_ate(hoje):
        if p['previsao_ir'] is None:
            continue                       # bloqueada: nao ha previsao a associar
        estado, obs = candidato_do_alvo(historico, p['modalidade'], p['duracao_s'], p['data_alvo'])
        if estado != 'unico':
            saida.append({'previsao_id': p['previsao_id'], 'acao': estado})
            continue
        if not obs.get('detetado_em'):
            saida.append({'previsao_id': p['previsao_id'], 'acao': 'rejeitado',
                          'motivo': 'observacao_sem_instante_de_conhecimento'})
            continue
        r = mp.associar_resultado(loja, p['previsao_id'], test_id=obs['test_id'], y_observado=obs['watts'],
                                  observado_em=obs['detetado_em'], registado_em=agora.isoformat())
        saida.append({'previsao_id': p['previsao_id'], **r})
    return saida


def estado_registo(loja, historico, agora):
    """Vista de leitura do registo para o painel. Nunca escreve e nunca inventa previsoes ou datas."""
    if not loja.tabela_existe():
        return {'estado': 'migracao_pendente', 'migracao': mpm.MIGRACAO_ID, 'previsoes': 0}
    rows = loja.todas()
    hoje = _hoje(agora).isoformat()
    bloqueadas = [p for p in rows if p['previsao_ir'] is None]
    calculadas = [p for p in rows if p['previsao_ir'] is not None]
    com_resultado, pendentes, vencidas, revisoes = [], [], [], 0
    pares = []
    for p in calculadas:
        vs = loja.versoes(p['previsao_id'])
        if vs:
            com_resultado.append(p['previsao_id'])
            revisoes += max(0, len(vs) - 1)
            ult = vs[-1]
            pares.append((p['data_alvo'], p['previsao_ir'], ult['y_observado']))
        else:
            pendentes.append(p['previsao_id'])
            if p['data_alvo'] <= hoje:
                vencidas.append(p['previsao_id'])
    n = len(pares)
    if n:
        erros = [pi - y for _, pi, y in pares]
        datas = sorted({d for d, _, _ in pares})
        dias = (datetime.fromisoformat(datas[-1]) - datetime.fromisoformat(datas[0])).days
        suficiente = n >= 20 and len(datas) >= 15 and dias >= 90
        metricas = ({'n': n, 'mae': round(sum(abs(e) for e in erros) / n, 4),
                     'erro_medio': round(sum(erros) / n, 4), 'periodo': [datas[0], datas[-1]]}
                    if suficiente else None)
        suf = {'n': n, 'datas_distintas': len(datas), 'dias_cobertura': dias, 'suficiente': suficiente,
               'criterios': {'min_alvos': 20, 'min_datas': 15, 'min_dias': 90}}
    else:
        metricas, suf = None, {'n': 0, 'suficiente': False, 'criterios': {'min_alvos': 20, 'min_datas': 15,
                                                                         'min_dias': 90}}
    if not rows:
        estado = 'sem_previsoes'
    elif not calculadas:
        estado = 'so_bloqueadas'
    elif com_resultado:
        estado = 'com_resultados'
    else:
        estado = 'pendentes'
    return {'estado': estado, 'previsoes': len(rows), 'calculadas': len(calculadas), 'bloqueadas': len(bloqueadas),
            'motivos_bloqueio': _contar([p['motivo_bloqueio'] for p in bloqueadas]),
            'pendentes_de_resultado': len(pendentes), 'vencidas_sem_resultado': len(vencidas),
            'com_resultado': len(com_resultado),
            'revisoes_de_resultado': revisoes, 'suficiencia_metricas': suf, 'metricas': metricas,
            'ultimo_calculo': max((p['calculado_em'] for p in rows), default=None),
            'retroativa': False}


def listar(loja, limite=20):
    """Ultimas previsoes registadas com o estado de cada uma (so leitura)."""
    rows = loja.todas()[-int(limite):]
    out = []
    for p in rows:
        vs = loja.versoes(p['previsao_id']) if p['previsao_ir'] is not None else []
        out.append({'previsao_id': p['previsao_id'], 'calculado_em': p['calculado_em'], 'data_alvo': p['data_alvo'],
                    'modalidade': p['modalidade'], 'duracao_s': p['duracao_s'], 'previsao_ir': p['previsao_ir'],
                    'estado_temporal': p['estado_temporal'], 'motivo_bloqueio': p['motivo_bloqueio'],
                    'situacao': ('bloqueada' if p['previsao_ir'] is None else
                                 'com_resultado' if vs else 'pendente'),
                    'resultado': ({'versao': vs[-1]['versao'], 'y_observado': vs[-1]['y_observado'],
                                   'observado_em': vs[-1]['observado_em'], 'versoes': len(vs)} if vs else None)})
    return out


def _contar(itens):
    out = {}
    for i in itens:
        out[i or 'sem_motivo'] = out.get(i or 'sem_motivo', 0) + 1
    return dict(sorted(out.items()))


def integrar_apos_sync(resultado, origem='sync', agora=None):
    """Ponto de integracao no sync. NUNCA levanta. Sem migracao aplicada NAO escreve nada."""
    try:
        import db
        import modelo_ir_servico as mis
        import modelo_ir_sombra_integracao as msi
        if not db.ENABLED:
            return {'ok': False, 'ignorado': 'sem_base_de_dados', 'escrito_operacional': False}
        loja = mp.loja_da_base()
        if not loja.tabela_existe():
            return {'ok': False, 'ignorado': 'migracao_pendente', 'migracao_pendente': mpm.MIGRACAO_ID,
                    'escrito_operacional': False}
        agora = agora or datetime.now(timezone.utc).replace(tzinfo=None)
        historico, revisoes, atividades, curvas, marcos = msi.carregar_entradas(None)
        entradas = preparar_entradas(historico, revisoes, atividades, curvas, marcos, agora,
                                     historico_yes=db.yes_historico())
        cfg = mis._cfg_efetiva(None)['ir']
        previsoes = produzir(loja, agora, entradas, cfg=cfg)
        associacoes = associar_pendentes(loja, historico, agora)
        return {'ok': True, 'previsoes': previsoes, 'associacoes': associacoes, 'escrito_operacional': False}
    except Exception as e:  # noqa: BLE001 - a sombra prospetiva nunca derruba o sync
        return {'ok': False, 'erro': f'{type(e).__name__}: {e}', 'escrito_operacional': False}
