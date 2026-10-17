"""Fase 1 do modelo individual progressivo: contrato de testes e registo auditavel.

O que esta fase FAZ
-------------------
Regista, de forma explicita e auditavel, quais esforcos detetados o utilizador
confirma ou rejeita como testes de performance. Nao estima nada: nenhum
parametro, nenhuma serie e nenhum resultado do PMC (CTL, ATL, TSB, CTLgamma,
FMT, homeostatico, ancora) le este registo. Confirmar um teste NAO calibra nem
atualiza o modelo.

Estados
-------
  detetado     esforco encontrado por protocolo.detectar_testes. Nao e gravado.
  elegibilidade 'elegivel' | 'inelegivel' | 'desconhecida' (regra icu_ignore_power da
               Fase B2; desconhecida = informacao ausente, nunca assumida como false).
  pendente     detetado e sem decisao do utilizador. Derivado, nao gravado:
               nada e confirmado nem rejeitado por omissao.
  confirmado   decisao explicita (confirmar) -> entra no registo.
  rejeitado    decisao explicita (rejeitar), sempre com motivo.

Regras
------
  * Um candidato 'inelegivel' (icu_ignore_power=true) nao pode ser confirmado.
  * Um candidato 'desconhecida' so se confirma com
    aceita_elegibilidade_desconhecida=true e motivo; a elegibilidade fica
    'desconhecida' no registo (nao e convertida em 'elegivel').
  * So existe metodo de confirmacao 'manual': nao ha protocolo de teste
    verificavel no projeto. 'protocolo' esta reservado e e recusado.
  * O servidor volta a derivar o candidato dos dados atuais; os watts nunca
    vem do cliente.
  * O registo e append-only: cada decisao e uma nova revisao do mesmo test_id.
    Corrigir ou invalidar = nova revisao; nada se apaga nem se sobrescreve.

Este modulo e puro (sem base de dados, sem Flask, sem rede).
"""
import math
from datetime import datetime, timezone

import protocolo

VERSAO_CONTRATO = 1

MODALIDADES = ('Bike', 'Row', 'Ski', 'Run')
DURACOES = tuple(sorted(protocolo.TESTES))          # 60, 300, 1200

ESTADO_CONFIRMADO = 'confirmado'
ESTADO_REJEITADO = 'rejeitado'
ESTADO_PENDENTE = 'pendente'                        # derivado, nunca gravado
ESTADOS_GRAVADOS = (ESTADO_CONFIRMADO, ESTADO_REJEITADO)
ESTADOS_CONSULTA = ESTADOS_GRAVADOS + (ESTADO_PENDENTE,)

DECISOES = {'confirmar': ESTADO_CONFIRMADO, 'rejeitar': ESTADO_REJEITADO}

METODO_MANUAL = 'manual'
METODO_PROTOCOLO = 'protocolo'                      # reservado: sem protocolo verificavel
METODOS = (METODO_MANUAL, METODO_PROTOCOLO)
METODOS_ACEITES = (METODO_MANUAL,)

ORIGENS_REGISTO = ('api', 'ui', 'script')

EVENTO_CONFIRMACAO = 'confirmacao'
EVENTO_REJEICAO = 'rejeicao'
EVENTO_INVALIDACAO = 'invalidacao'                  # confirmado -> rejeitado
EVENTO_RECONFIRMACAO = 'reconfirmacao'              # rejeitado -> confirmado
EVENTOS = (EVENTO_CONFIRMACAO, EVENTO_REJEICAO, EVENTO_INVALIDACAO, EVENTO_RECONFIRMACAO)

MAX_MOTIVO = 500
MAX_RESPONSAVEL = 80

ELEG = (protocolo.ELEGIBILIDADE_ELEGIVEL, protocolo.ELEGIBILIDADE_INELEGIVEL,
        protocolo.ELEGIBILIDADE_DESCONHECIDA)


class ErroContrato(Exception):
    """Pedido recusado pelo contrato. `status` e o codigo HTTP sugerido."""

    def __init__(self, status, mensagem, campo=None):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem
        self.campo = campo


# ── identidade ──────────────────────────────────────────────────────────────

def id_teste(modalidade, duracao_s, activity_id):
    """Identificador estavel e determinístico: o mesmo teste da mesma origem
    tem sempre o mesmo id, por isso nao ha duplicados."""
    return f'{modalidade}:{int(duracao_s)}:{activity_id}'


# ── candidatos ─────────────────────────────────────────────────────────────

def _icu_ignore_power_de(elegibilidade):
    """Valor observado de icu_ignore_power, deduzido SO da elegibilidade
    anotada: elegivel -> False, inelegivel -> True, desconhecida -> None."""
    if elegibilidade == protocolo.ELEGIBILIDADE_ELEGIVEL:
        return False
    if elegibilidade == protocolo.ELEGIBILIDADE_INELEGIVEL:
        return True
    return None


def candidatos(deteccao_anotada):
    """Esforcos detetados, achatados, com elegibilidade (sem estado de registo).

    deteccao_anotada: saida de protocolo.anotar_elegibilidade(detectar_testes(..)).
    Sem anotacao, a elegibilidade e 'desconhecida' (nunca se presume elegivel).
    """
    out = []
    for mod, durs in (deteccao_anotada or {}).items():
        for secs, d in (durs or {}).items():
            for t in d.get('testes') or []:
                eleg = t.get('elegibilidade_calibracao') or protocolo.ELEGIBILIDADE_DESCONHECIDA
                motivo = t.get('motivo_elegibilidade')
                if 'elegibilidade_calibracao' not in t:
                    motivo = 'sem_anotacao'
                out.append({
                    'test_id': id_teste(mod, secs, t['activity_id']),
                    'modalidade': mod, 'duracao_s': int(secs),
                    'activity_id': t['activity_id'], 'data': t['date'],
                    'watts': t['watts'],
                    'pct_do_melhor': t.get('pct_do_melhor'),
                    'melhor_recente': t.get('melhor_recente'),
                    'elegibilidade': eleg, 'motivo_elegibilidade': motivo,
                })
    out.sort(key=lambda c: (c['modalidade'], c['duracao_s'], c['data'], c['activity_id']))
    return out


def _confirmabilidade(c):
    """(confirmavel, requer_opt_in, motivo_se_nao) para um candidato."""
    if c['elegibilidade'] == protocolo.ELEGIBILIDADE_INELEGIVEL:
        return False, False, ('icu_ignore_power=true: a Intervals.icu ignora a potencia '
                              'desta atividade; so pode ser rejeitado')
    if c['elegibilidade'] == protocolo.ELEGIBILIDADE_DESCONHECIDA:
        return True, True, ('elegibilidade desconhecida (icu_ignore_power ausente ou '
                            'atividade nao encontrada): a confirmacao exige aceitar '
                            'explicitamente essa incerteza')
    return True, False, None


def candidatos_com_estado(deteccao_anotada, atuais, filtros=None, elig_estimador=None,
                          historico=None):
    """Candidatos + estado no registo para a VERSAO ATUAL do valor.

    Identidade do candidato = test_id (modalidade:duracao_s:activity_id). Versao do
    valor = watts da curva atual. O candidato esta 'pendente' se nao ha revisao OU se
    os watts atuais diferem dos da ultima revisao (valor atualizado na Intervals.icu):
    a decisao anterior fica intacta no historico, e a nova versao espera decisao.
    Nenhum estado 'pendente' e gravado: calcula-se a cada pedido.

    atuais: {test_id: ultima revisao}. filtros: {'tipo','duracao','estado'}.
    elig_estimador: callable(activity_id) -> elegibilidade de aprendizagem que o
    ESTIMADOR aplica (injetada por quem chama: este modulo nao conhece o estimador);
    opcional, aditivo.
    historico: todas as revisoes (opcional) para assinalar o regresso a um valor anterior.
    """
    f = filtros or {}
    por_teste = {}
    for r in historico or []:
        por_teste.setdefault(r.get('test_id'), []).append(r)
    out = []
    for c in candidatos(deteccao_anotada):
        rev = (atuais or {}).get(c['test_id'])
        atualizado = rev is not None and not mesmo_valor(rev.get('watts'), c['watts'])
        estado = ESTADO_PENDENTE if (rev is None or atualizado) else rev['estado']
        confirmavel, opt_in, porque = _confirmabilidade(c)
        igual = None
        if atualizado:
            iguais = [r['revisao'] for r in por_teste.get(c['test_id'], [])
                      if mesmo_valor(r.get('watts'), c['watts'])]
            igual = max(iguais) if iguais else None
        c = {**c,
             'estado_registo': estado,
             'revisao': rev['revisao'] if rev else None,
             'confirmavel': confirmavel, 'requer_aceitar_elegibilidade_desconhecida': opt_in,
             'motivo_nao_confirmavel': None if confirmavel else porque,
             'aviso_elegibilidade': porque if confirmavel and opt_in else None,
             # aditivos (versao do valor)
             'valor_atualizado': bool(atualizado),
             'estado_ultima_revisao': rev['estado'] if rev else None,
             'decisao_anterior': ({'revisao': rev['revisao'], 'estado': rev['estado'],
                                   'watts': rev.get('watts'), 'data': rev.get('data'),
                                   'revisado_em': rev.get('revisado_em')} if rev else None),
             'valor_igual_a_revisao': igual}
        if elig_estimador is not None:
            e = elig_estimador(c['activity_id'])
            c['elegibilidade_estimador'] = e['estado']
            c['motivo_elegibilidade_estimador'] = e.get('motivo')
            c['elegivel_para_estimador'] = e['estado'] == 'elegivel'
        if f.get('tipo') and c['modalidade'] != f['tipo']:
            continue
        if f.get('duracao') is not None and c['duracao_s'] != f['duracao']:
            continue
        if f.get('estado') and c['estado_registo'] != f['estado']:
            continue
        out.append(c)
    return out


def resumo_pendencias(candidatos_estado):
    """Contagem aditiva sobre a lista devolvida por candidatos_com_estado."""
    pend = [c for c in candidatos_estado if c['estado_registo'] == ESTADO_PENDENTE]
    return {'pendentes': len(pend),
            'pendentes_sem_decisao': sum(1 for c in pend if not c.get('valor_atualizado')),
            'pendentes_valor_atualizado': sum(1 for c in pend if c.get('valor_atualizado'))}


# ── validacao do pedido de decisao ───────────────────────────────────────

def _texto(v, campo, obrigatorio, maximo):
    if v is None:
        if obrigatorio:
            raise ErroContrato(400, f'{campo} obrigatorio', campo)
        return None
    if not isinstance(v, str):
        raise ErroContrato(400, f'{campo} tem de ser texto', campo)
    v = v.strip()
    if not v:
        if obrigatorio:
            raise ErroContrato(400, f'{campo} obrigatorio', campo)
        return None
    if len(v) > maximo:
        raise ErroContrato(400, f'{campo} excede {maximo} caracteres', campo)
    return v


def validar_pedido(corpo):
    """Normaliza e valida o corpo de POST /api/protocolo/testes/decisao.

    Levanta ErroContrato(400). Nao consulta dados: so a forma do pedido.
    """
    if not isinstance(corpo, dict):
        raise ErroContrato(400, 'corpo JSON (objeto) obrigatorio')
    if corpo.get('confirmacao_explicita') is not True:
        raise ErroContrato(400, 'confirmacao_explicita=true obrigatoria: a decisao '
                                'nunca e tomada por omissao', 'confirmacao_explicita')
    mod = corpo.get('modalidade')
    if mod not in MODALIDADES:
        raise ErroContrato(400, f'modalidade invalida (use {", ".join(MODALIDADES)})', 'modalidade')
    dur = corpo.get('duracao_s')
    if isinstance(dur, bool) or not isinstance(dur, int) or dur not in DURACOES:
        raise ErroContrato(400, f'duracao_s invalida (use {", ".join(map(str, DURACOES))})', 'duracao_s')
    aid = _texto(corpo.get('activity_id'), 'activity_id', True, 80)
    dec = corpo.get('decisao')
    if dec not in DECISOES:
        raise ErroContrato(400, 'decisao invalida (use confirmar ou rejeitar)', 'decisao')
    motivo = _texto(corpo.get('motivo'), 'motivo', True, MAX_MOTIVO)
    metodo = corpo.get('metodo_confirmacao', METODO_MANUAL)
    if metodo not in METODOS:
        raise ErroContrato(400, 'metodo_confirmacao invalido', 'metodo_confirmacao')
    if metodo not in METODOS_ACEITES:
        raise ErroContrato(400, 'nao existe protocolo de teste verificavel: use '
                                'metodo_confirmacao=manual', 'metodo_confirmacao')
    origem = corpo.get('origem_registo', 'api')
    if origem not in ORIGENS_REGISTO:
        raise ErroContrato(400, f'origem_registo invalida (use {", ".join(ORIGENS_REGISTO)})',
                           'origem_registo')
    optin = corpo.get('aceita_elegibilidade_desconhecida', False)
    if not isinstance(optin, bool):
        raise ErroContrato(400, 'aceita_elegibilidade_desconhecida tem de ser booleano',
                           'aceita_elegibilidade_desconhecida')
    resp = _texto(corpo.get('responsavel'), 'responsavel', False, MAX_RESPONSAVEL)
    vistos = corpo.get('watts_vistos')
    if vistos is not None and (isinstance(vistos, bool) or not isinstance(vistos, (int, float))
                               or not math.isfinite(vistos) or vistos <= 0):
        raise ErroContrato(400, 'watts_vistos tem de ser um numero positivo', 'watts_vistos')
    return {'watts_vistos': vistos, 'modalidade': mod, 'duracao_s': dur, 'activity_id': aid, 'decisao': dec,
            'motivo': motivo, 'metodo_confirmacao': metodo, 'origem_registo': origem,
            'aceita_elegibilidade_desconhecida': optin, 'responsavel': resp}


# ── decisao ──────────────────────────────────────────────────────────────

def _evento(anterior, novo):
    # anterior == novo so chega aqui quando o VALOR mudou (nova versao do valor):
    # e uma nova confirmacao/rejeicao desse valor; estado_anterior fica registado.
    if anterior is None or anterior == novo:
        return EVENTO_CONFIRMACAO if novo == ESTADO_CONFIRMADO else EVENTO_REJEICAO
    if anterior == ESTADO_CONFIRMADO and novo == ESTADO_REJEITADO:
        return EVENTO_INVALIDACAO
    return EVENTO_RECONFIRMACAO                     # rejeitado -> confirmado


def hora_local_da_atividade(atividades, activity_id):
    """start_date_local da atividade tal como a API a deu, ou None.
    Nada e inventado: sem atividade ou sem o campo, devolve None."""
    for a in atividades or []:
        if isinstance(a, dict) and a.get('id') == activity_id:
            v = a.get('start_date_local')
            return v if isinstance(v, str) and len(v) >= 16 else None
    return None


def decidir(pedido, deteccao_anotada, ultima_revisao, agora, hora_local=None):
    """Nova revisao para o pedido, ou None se nao ha nada a mudar.

    pedido: saida de validar_pedido. deteccao_anotada: dados ATUAIS do servidor.
    ultima_revisao: ultima revisao gravada deste test_id (ou None).
    agora: datetime (UTC). Levanta ErroContrato (404 candidato inexistente,
    409 regra de elegibilidade).
    """
    tid = id_teste(pedido['modalidade'], pedido['duracao_s'], pedido['activity_id'])
    cand = next((c for c in candidatos(deteccao_anotada) if c['test_id'] == tid), None)
    if cand is None:
        raise ErroContrato(404, f'{tid} nao e um candidato detetado nos dados atuais')

    novo = DECISOES[pedido['decisao']]
    confirmavel, opt_in, porque = _confirmabilidade(cand)
    optin_dado = pedido['aceita_elegibilidade_desconhecida']
    if novo == ESTADO_CONFIRMADO:
        if not confirmavel:
            raise ErroContrato(409, porque, 'elegibilidade')
        if opt_in and not optin_dado:
            raise ErroContrato(409, porque + ' (aceita_elegibilidade_desconhecida=true)',
                               'aceita_elegibilidade_desconhecida')

    vistos = pedido.get('watts_vistos')
    if vistos is not None and not mesmo_valor(vistos, cand['watts']):
        raise ErroContrato(409, f'o valor mudou desde que o viu ({vistos} W visto, '
                                f'{cand["watts"]} W atual): recarregue e decida de novo',
                           'watts_vistos')

    anterior = ultima_revisao['estado'] if ultima_revisao else None
    if anterior == novo and mesmo_valor(ultima_revisao.get('watts'), cand['watts']):
        return None            # idempotente: mesmo estado E mesma versao do valor

    return {
        'test_id': tid,
        'revisao': (ultima_revisao['revisao'] + 1) if ultima_revisao else 1,
        'evento': _evento(anterior, novo),
        'estado': novo, 'estado_anterior': anterior,
        'modalidade': cand['modalidade'], 'duracao_s': cand['duracao_s'],
        'activity_id': cand['activity_id'], 'data': cand['data'],
        'hora_local': hora_local,
        'watts': cand['watts'], 'pct_do_melhor': cand['pct_do_melhor'],
        'melhor_recente': cand['melhor_recente'],
        'deteccao': {'limiar_esforco': protocolo.LIMIAR_ESFORCO,
                     'janela_dias': protocolo.JANELA_MELHOR},
        'elegibilidade': cand['elegibilidade'],
        'motivo_elegibilidade': cand['motivo_elegibilidade'],
        'icu_ignore_power': _icu_ignore_power_de(cand['elegibilidade']),
        'aceita_elegibilidade_desconhecida': bool(optin_dado and opt_in),
        'metodo_confirmacao': pedido['metodo_confirmacao'],
        'motivo': pedido['motivo'], 'responsavel': pedido['responsavel'],
        'origem_registo': pedido['origem_registo'],
        'versao_contrato': VERSAO_CONTRATO,
        'registado_em': ultima_revisao['registado_em'] if ultima_revisao else agora,
        'revisado_em': agora,
        'confirmado_em': agora if novo == ESTADO_CONFIRMADO else None,
    }


# ── consulta do registo ─────────────────────────────────────────────────

def mesmo_valor(a, b):
    """True se dois watts sao a MESMA versao do valor (mesma tolerancia de
    situacao_dados). Valor ausente ou nao numerico nunca e igual a nada."""
    if not (_numero(a) and _numero(b)):
        return False
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def _numero(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def valor_mudou(antes, depois):
    """True so se AMBOS os valores sao numeros e diferem (versao nova do valor)."""
    return _numero(antes) and _numero(depois) and not mesmo_valor(antes, depois)


def situacao_dados(revisao, curvas_por_atividade):
    """Compara os watts registados com a curva atual da atividade.

    'conforme' | 'divergente' | 'sem_curva'. So informa: nunca altera o registo
    (um re-sync nao reescreve um teste confirmado).
    """
    curva = (curvas_por_atividade or {}).get(revisao['activity_id'])
    if not curva:
        return 'sem_curva'
    for s, w in zip(curva.get('secs') or [], curva.get('watts') or []):
        if s == revisao['duracao_s'] and isinstance(w, (int, float)) and not isinstance(w, bool):
            return ('conforme' if math.isclose(w, revisao['watts'], rel_tol=1e-9, abs_tol=1e-6)
                    else 'divergente')
    return 'sem_curva'


def resumo_registo(atuais_lista):
    """Contagem do registo atual por modalidade, duracao e estado. So contagem."""
    out = {}
    for r in atuais_lista or []:
        d = out.setdefault(r['modalidade'], {}).setdefault(str(r['duracao_s']), {})
        d[r['estado']] = d.get(r['estado'], 0) + 1
    return out


def agora_utc():
    """UTC sem fuso (mesma convencao de db._now())."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
