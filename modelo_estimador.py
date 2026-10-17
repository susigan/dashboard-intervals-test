"""Fase 2 do modelo individual progressivo: estimador experimental de nivel de potencia.

EXPERIMENTAL. Nao validado preditivamente (isso e a Fase 3) e nao e lido por
nenhum calculo de producao (PMC, CTL, ATL, TSB, CTLgamma, FMT, homeostatico,
ancora). Este modulo e puro: sem base de dados, sem Flask, sem rede, sem relogio.

O que estima
------------
Um nivel de potencia (W) INDEPENDENTE por modalidade x duracao (Bike/Row/Ski/Run x
60/300/1200 s). Um teste de 300 s so atualiza P300 da sua modalidade. CP e W' nao
sao estimados.

O que uma observacao representa (verificado no codigo, Fase 2)
--------------------------------------------------------------
A potencia de um candidato/teste e o MELHOR VALOR DA DURACAO EM TODA A ATIVIDADE
(power_curves <- /activity-power-curves). Nao ha indices de stream nem inicio/fim
do trecho. icu_ignore_power / icu_ignore_hr sao campos de NIVEL DA ATIVIDADE;
nenhum codigo le um flag por trecho. `ignore_parts` e sincronizado mas nao e usado
e o seu formato nao foi verificado: se vier nao vazio, nao e associavel ao trecho
e a elegibilidade fica 'desconhecida'.

Modelo (filtro de nivel local, causal)
--------------------------------------
    y_k = mu_k + e_k,          e_k  ~ N(0, s2)              [W]
    mu_k = mu_{k-1} + h_k,     h_k  ~ N(0, q * s2 * dt_k)   [dt_k em dias]

Todas as variancias sao relativas a s2 (a variancia do ruido, DESCONHECIDA):
  P*  = P / s2.  Primeira observacao: mu = y_1, P* = 1 (prior difuso: nenhum
  prior fisiologico, nenhum valor de referencia).
  Passo k>=2:  P*_pred = P*_{k-1} + q*dt;  F* = P*_pred + 1;  v = y_k - mu_{k-1};
               K = P*_pred / F*;  mu_k = mu_{k-1} + K v;  P*_k = P*_pred / F*.
  s2 estimado dos dados:  s2_hat = (1/m) * soma(v^2 / F*),  m = n-1 inovacoes.
  Nivel (em `ate`, dias depois da ultima observacao):
      sd = sqrt(s2_hat) * sqrt(P*_k + q*dias),   IC95 = mu +- t_{0.975, m} * sd.

Hipoteses: ruido gaussiano independente; variacao real do nivel aproximada por
passeio aleatorio; razao sinal/ruido `q` (por dia) FIXA. `q` e uma convencao de
suavizacao, NAO um parametro individual nem fisiologico: nao foi validada nem
estimada (Fase 3). s2_hat mistura ruido de medida e variacao real do nivel.

Incerteza: 0 obs -> 'nao_calibrado' (sem nivel nem incerteza); 1 obs ->
'estimativa_unica' (incerteza NAO estimavel: nao ha variabilidade observada);
>=2 obs -> 'experimental'. Se as observacoes forem identicas (s2_hat = 0) a
incerteza fica indeterminada em vez de ~0. E a incerteza do NIVEL sob o modelo,
nao um intervalo de previsao de um novo teste, e nao inclui a incerteza de `q`.

Mesmo dia: dt = 0 -> repeticoes da mesma medida; o resultado nao depende da ordem.
Ordem: (data, hora_local, activity_id, test_id). Nenhuma observacao posterior a
`ate` e usada (sem fuga de informacao futura; filtro causal, nao suavizador).
"""
import hashlib
import json
import math
from datetime import date, datetime

import modelo_testes as mt

VERSAO_ESTIMADOR = 1
Q_DIA = 0.01                       # razao sinal/ruido por dia (convencao, nao validada)
LIMITE_ATIPICA = 3.0               # |z| > 3 sinaliza (diagnostico; nao exclui)
MIN_INOVACOES_ATIPICA = 3

EST_NAO_CALIBRADO = 'nao_calibrado'
EST_UNICA = 'estimativa_unica'
EST_EXPERIMENTAL = 'experimental'

ELEG_ELEGIVEL = 'elegivel'
ELEG_INELEGIVEL = 'inelegivel'
ELEG_DESCONHECIDA = 'desconhecida'

# quantil 0.975 da t de Student (valores tabelados standard). Acima de 30 g.l. usa-se
# o valor do limite inferior do intervalo (ligeiramente conservador).
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
         9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
         16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074,
         23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045,
         30: 2.042}


def t975(gl):
    if gl < 1:
        raise ValueError('graus de liberdade < 1')
    if gl <= 30:
        return _T975[gl]
    if gl < 40:
        return 2.042
    if gl < 60:
        return 2.021
    if gl < 120:
        return 2.000
    return 1.980


class HistoricoAmbiguo(Exception):
    """O historico nao permite determinar sem ambiguidade a revisao atual."""

    def __init__(self, problemas):
        super().__init__('; '.join(problemas[:5]))
        self.problemas = problemas


# ── historico -> revisao atual ──────────────────────────────────────────────

def estado_atual_por_teste(historico):
    """{test_id: revisao atual} a partir do historico COMPLETO (todas as revisoes).

    Valida, por test_id: revisoes 1..n sem lacunas nem repeticoes; a primeira sem
    estado anterior; cada estado_anterior = estado da revisao anterior; estado
    diferente do anterior; evento coerente com a transicao; test_id coerente com
    modalidade/duracao/atividade. Qualquer falha -> HistoricoAmbiguo (nao se adivinha).
    """
    por_id, problemas = {}, []
    for r in historico or []:
        por_id.setdefault(r.get('test_id'), []).append(r)
    atuais = {}
    for tid, revs in por_id.items():
        nums = [r.get('revisao') for r in revs]
        if any(isinstance(n, bool) or not isinstance(n, int) for n in nums):
            problemas.append(f'{tid}: numero de revisao invalido')
            continue
        if sorted(nums) != list(range(1, len(nums) + 1)):
            problemas.append(f'{tid}: revisoes {sorted(nums)} nao sao 1..{len(nums)} (lacuna ou repeticao)')
            continue
        revs = sorted(revs, key=lambda r: r['revisao'])
        anterior, anterior_watts = None, None
        for r in revs:
            if r.get('estado') not in mt.ESTADOS_GRAVADOS:
                problemas.append(f'{tid}#{r["revisao"]}: estado invalido {r.get("estado")!r}')
                break
            if r.get('estado_anterior') != anterior:
                problemas.append(f'{tid}#{r["revisao"]}: estado_anterior incoerente')
                break
            # mesmo estado so e legitimo como decisao sobre uma NOVA versao do valor
            # (watts diferentes da revisao anterior); com o mesmo valor e uma repeticao
            if r['estado'] == anterior and not mt.valor_mudou(anterior_watts, r.get('watts')):
                problemas.append(f'{tid}#{r["revisao"]}: estado repetido')
                break
            if r.get('evento') != mt._evento(anterior, r['estado']):
                problemas.append(f'{tid}#{r["revisao"]}: evento incoerente com a transicao')
                break
            anterior, anterior_watts = r['estado'], r.get('watts')
        else:
            ultima = revs[-1]
            try:
                esperado = mt.id_teste(ultima['modalidade'], ultima['duracao_s'], ultima['activity_id'])
            except (KeyError, TypeError, ValueError):
                esperado = None
            if esperado != tid:
                problemas.append(f'{tid}: test_id incoerente com modalidade/duracao/atividade')
                continue
            atuais[tid] = ultima
    if problemas:
        raise HistoricoAmbiguo(problemas)
    return atuais


# ── elegibilidade para aprendizagem (granularidade: ATIVIDADE) ───────────────

def _nao_vazio(v):
    if v is None or v is False:
        return False
    if isinstance(v, (list, tuple, dict, str)):
        return len(v) > 0
    return True


def elegibilidade_aprendizagem(activity_id, atividades):
    """Elegibilidade da POTENCIA de uma atividade para aprendizagem.

    Ambito verificado: atividade inteira. Devolve
    {'estado','motivo','icu_ignore_power','icu_ignore_hr','ignore_parts_presente','ambito'}.
      icu_ignore_power True            -> inelegivel
      ausente/None/nao booleano        -> desconhecida
      atividade ausente / sem lista    -> desconhecida
      False + ignore_parts nao vazio   -> desconhecida (nao associavel ao trecho)
      False (explicito)                -> elegivel (nao prova que seja um teste valido)
    icu_ignore_hr e so informativo: nunca exclui.
    """
    base = {'icu_ignore_power': None, 'icu_ignore_hr': None,
            'ignore_parts_presente': None, 'ambito': 'atividade'}
    if atividades is None:
        return {**base, 'estado': ELEG_DESCONHECIDA, 'motivo': 'atividades_indisponiveis'}
    alvo = [a for a in atividades if isinstance(a, dict) and a.get('id') == activity_id]
    if not alvo:
        return {**base, 'estado': ELEG_DESCONHECIDA, 'motivo': 'atividade_nao_encontrada'}
    pw = [a.get('icu_ignore_power') for a in alvo]
    pw = [v if isinstance(v, bool) else None for v in pw]
    hr = [a.get('icu_ignore_hr') for a in alvo]
    base['icu_ignore_hr'] = True if any(v is True for v in hr) else (
        False if all(v is False for v in hr) else None)
    partes = any(_nao_vazio(a.get('ignore_parts')) for a in alvo)
    base['ignore_parts_presente'] = partes
    if any(v is True for v in pw):
        base['icu_ignore_power'] = True
        return {**base, 'estado': ELEG_INELEGIVEL, 'motivo': 'icu_ignore_power=true'}
    if any(v is None for v in pw):
        return {**base, 'estado': ELEG_DESCONHECIDA, 'motivo': 'icu_ignore_power_ausente'}
    base['icu_ignore_power'] = False
    if partes:
        return {**base, 'estado': ELEG_DESCONHECIDA,
                'motivo': 'ignore_parts_nao_associavel_ao_trecho'}
    return {**base, 'estado': ELEG_ELEGIVEL, 'motivo': None}


# ── selecao de observacoes ──────────────────────────────────────────────────

def _data_valida(s):
    if not isinstance(s, str) or len(s) < 10:
        return None
    try:
        return datetime.strptime(s[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _watts_validos(w):
    return (isinstance(w, (int, float)) and not isinstance(w, bool)
            and math.isfinite(w) and w > 0)


def _motivo_revisao_invalida(r):
    """Motivo de uma revisao CONFIRMADA nao ser utilizavel, ou None."""
    if r.get('modalidade') not in mt.MODALIDADES:
        return 'modalidade_invalida'
    d = r.get('duracao_s')
    if isinstance(d, bool) or d not in mt.DURACOES:
        return 'duracao_invalida'
    if not _watts_validos(r.get('watts')):
        return 'potencia_invalida'
    if _data_valida(r.get('data')) is None:
        return 'data_invalida'
    if r.get('confirmado_em') is None or r.get('metodo_confirmacao') not in mt.METODOS_ACEITES:
        return 'confirmacao_explicita_ausente'
    return None


def chave_ordem(o):
    return (o['data'], o.get('hora_local') or '', o['activity_id'], o['test_id'])


def selecionar_observacoes(atuais, atividades, curvas_por_atividade):
    """Separa, por (modalidade, duracao), as observacoes usaveis das excluidas.

    atuais: saida de estado_atual_por_teste. atividades: lista da API (ou None);
    curvas_por_atividade: {activity_id: {'secs','watts'}} (ou None). Nada vem do cliente.
    Devolve {(mod, dur): {'n_confirmados', 'usadas': [obs ordenadas], 'excluidas': [...]}}
    com as 12 combinacoes sempre presentes.
    """
    out = {(m, d): {'n_confirmados': 0, 'usadas': [], 'excluidas': []}
           for m in mt.MODALIDADES for d in mt.DURACOES}
    for tid in sorted(atuais):
        r = atuais[tid]
        k = (r.get('modalidade'), r.get('duracao_s'))
        if k not in out:                      # historico fora do dominio conhecido
            continue
        base = {'test_id': tid, 'revisao': r['revisao'], 'activity_id': r.get('activity_id'),
                'data': r.get('data'), 'watts': r.get('watts')}
        if r['estado'] != mt.ESTADO_CONFIRMADO:
            out[k]['excluidas'].append({**base, 'motivo': 'revisao_atual_rejeitada'})
            continue
        out[k]['n_confirmados'] += 1
        motivo = _motivo_revisao_invalida(r)
        eleg = None
        if motivo is None:
            eleg = elegibilidade_aprendizagem(r['activity_id'], atividades)
            if eleg['estado'] == ELEG_INELEGIVEL:
                motivo = 'inelegivel:' + eleg['motivo']
            elif eleg['estado'] == ELEG_DESCONHECIDA:
                motivo = 'elegibilidade_desconhecida:' + eleg['motivo']
        if motivo is None:
            sit = mt.situacao_dados(r, curvas_por_atividade)
            if sit != 'conforme':
                motivo = 'dados_' + sit        # dados_divergente | dados_sem_curva
        if motivo is not None:
            out[k]['excluidas'].append({**base, 'motivo': motivo})
            continue
        out[k]['usadas'].append({**base, 'hora_local': r.get('hora_local'),
                                 'elegibilidade_aprendizagem': eleg['estado'],
                                 'elegibilidade_registo': r.get('elegibilidade'),
                                 'aceita_elegibilidade_desconhecida':
                                     r.get('aceita_elegibilidade_desconhecida', False)})
    for v in out.values():
        v['usadas'].sort(key=chave_ordem)
        v['excluidas'].sort(key=lambda e: (e['motivo'], e['test_id']))
    return out


# ── nucleo do estimador (funcoes puras) ─────────────────────────────────────

def estado_inicial():
    return {'n': 0, 'm': 0, 'mu': None, 'p': None, 'ss': 0.0,
            'ultima_data': None, 'ids': ()}


def passo(estado, obs, q=Q_DIA):
    """(novo_estado, info). Observacao invalida -> devolve o MESMO estado, aceite=False."""
    w, d = obs.get('watts'), _data_valida(obs.get('data'))
    if not _watts_validos(w):
        return estado, {'aceite': False, 'motivo': 'potencia_invalida'}
    if d is None:
        return estado, {'aceite': False, 'motivo': 'data_invalida'}
    if estado['n'] == 0:
        novo = {'n': 1, 'm': 0, 'mu': float(w), 'p': 1.0, 'ss': 0.0,
                'ultima_data': d.isoformat(), 'ids': (obs.get('test_id'),)}
        return novo, {'aceite': True, 'inovacao': None, 'z': None, 'atipica': None}
    ultima = date.fromisoformat(estado['ultima_data'])
    if d < ultima:
        return estado, {'aceite': False, 'motivo': 'fora_de_ordem'}
    dt = (d - ultima).days
    p_pred = estado['p'] + q * dt
    f = p_pred + 1.0
    v = float(w) - estado['mu']
    z = atipica = None
    if estado['m'] >= MIN_INOVACOES_ATIPICA:
        s_prev = math.sqrt(estado['ss'] / estado['m'])
        if s_prev > 0:
            z = v / (s_prev * math.sqrt(f))
            atipica = abs(z) > LIMITE_ATIPICA
    novo = {'n': estado['n'] + 1, 'm': estado['m'] + 1,
            'mu': estado['mu'] + (p_pred / f) * v, 'p': p_pred / f,
            'ss': estado['ss'] + v * v / f,
            'ultima_data': d.isoformat(), 'ids': estado['ids'] + (obs.get('test_id'),)}
    return novo, {'aceite': True, 'inovacao': v, 'z': z, 'atipica': atipica}


def resumir(estado, ate=None, q=Q_DIA):
    """Estimativa e incerteza como campos distintos, ao dia `ate` (YYYY-MM-DD)."""
    n = estado['n']
    r = {'estado_calibracao': EST_NAO_CALIBRADO, 'n_observacoes': n,
         'nivel_w': None, 'incerteza': None, 'motivo_sem_incerteza': None,
         'ultima_observacao': estado['ultima_data'], 'dias_desde_ultima': None}
    if n == 0:
        r['motivo_sem_incerteza'] = 'sem_observacoes'
        return r
    dias = 0
    if ate is not None:
        da = _data_valida(ate)
        if da is not None:
            dias = max(0, (da - date.fromisoformat(estado['ultima_data'])).days)
    r['dias_desde_ultima'] = dias
    r['nivel_w'] = estado['mu']
    if n == 1:
        r['estado_calibracao'] = EST_UNICA
        r['motivo_sem_incerteza'] = 'incerteza_nao_estimavel_com_1_observacao'
        return r
    r['estado_calibracao'] = EST_EXPERIMENTAL
    s2 = estado['ss'] / estado['m']
    if not s2 > 0:
        r['motivo_sem_incerteza'] = 'variabilidade_observada_nula'
        return r
    sd = math.sqrt(s2) * math.sqrt(estado['p'] + q * dias)
    t = t975(estado['m'])
    r['incerteza'] = {'sd_w': sd, 'ic95_w': [estado['mu'] - t * sd, estado['mu'] + t * sd],
                      'graus_liberdade': estado['m'], 'sigma_ruido_w': math.sqrt(s2),
                      'tipo': 'nivel_sob_o_modelo'}
    return r


def estimar_serie(observacoes, ate=None, q=Q_DIA):
    """Dobra as observacoes (qualquer ordem de entrada) por ordem temporal.

    Observacoes com data > `ate` nao sao usadas. Devolve
    {'estado','resumo','trajetoria','descartadas'}; a trajetoria e causal (cada ponto
    so usa observacoes ate esse ponto).
    """
    lim = _data_valida(ate) if ate is not None else None
    st, traj, desc = estado_inicial(), [], []
    for o in sorted(observacoes, key=chave_ordem):
        d = _data_valida(o.get('data'))
        if lim is not None and d is not None and d > lim:
            desc.append({'test_id': o.get('test_id'), 'motivo': 'posterior_a_data_de_referencia'})
            continue
        st, info = passo(st, o, q)
        if not info['aceite']:
            desc.append({'test_id': o.get('test_id'), 'motivo': info['motivo']})
            continue
        res = resumir(st, o['data'][:10], q)
        traj.append({'test_id': o['test_id'], 'data': o['data'][:10], 'watts': o['watts'],
                     'nivel_w': res['nivel_w'], 'sd_w': (res['incerteza'] or {}).get('sd_w'),
                     'z': info['z'], 'atipica': info['atipica']})
    return {'estado': st, 'resumo': resumir(st, ate, q), 'trajetoria': traj, 'descartadas': desc}


# ── orquestracao ────────────────────────────────────────────────────────────

def _assinatura(usadas_por_chave, q, ate):
    seq = [[m, d, o['test_id'], o['revisao'], o['data'], o['watts']]
           for (m, d), v in sorted(usadas_por_chave.items()) for o in v]
    txt = json.dumps({'v': VERSAO_ESTIMADOR, 'q': q, 'ate': ate, 'seq': seq}, sort_keys=True)
    return hashlib.sha256(txt.encode()).hexdigest()


def curvas_por_atividade(curvas):
    """{activity_id: curva} a partir de db.load_power_curves()."""
    return {c['activity_id']: c for c in (curvas or []) if isinstance(c, dict) and 'activity_id' in c}


def calcular_estado(historico, atividades, curvas, ate, q=Q_DIA, tipo=None, duracao=None):
    """Estado experimental completo, refeito do historico (nada e persistido).

    historico: TODAS as revisoes (db.revisoes_testes_modelo()). `ate`: YYYY-MM-DD
    obrigatorio e explicito (a funcao nao le o relogio). Levanta HistoricoAmbiguo.
    """
    if _data_valida(ate) is None:
        raise ValueError('ate tem de ser uma data YYYY-MM-DD')
    atuais = estado_atual_por_teste(historico)
    sel = selecionar_observacoes(atuais, atividades, curvas_por_atividade(curvas))
    itens, usadas_para_hash = [], {}
    for (m, d) in sorted(sel):
        if (tipo and m != tipo) or (duracao is not None and d != duracao):
            continue
        s = sel[(m, d)]
        ser = estimar_serie(s['usadas'], ate, q)
        aceites = set(ser['estado']['ids'])
        excl = list(s['excluidas']) + [
            {**next((o for o in s['usadas'] if o['test_id'] == x['test_id']), {}),
             'motivo': x['motivo']} for x in ser['descartadas']]
        usadas = [o for o in s['usadas'] if o['test_id'] in aceites]
        usadas_para_hash[(m, d)] = usadas
        motivos = {}
        for e in excl:
            motivos[e['motivo']] = motivos.get(e['motivo'], 0) + 1
        itens.append({
            'modalidade': m, 'duracao_s': d, **ser['resumo'],
            'n_confirmados': s['n_confirmados'], 'n_usadas': len(usadas),
            'n_excluidas': len(excl), 'motivos_exclusao': dict(sorted(motivos.items())),
            'excluidas': excl,
            'sequencia_usada': [{'test_id': o['test_id'], 'revisao': o['revisao'],
                                 'activity_id': o['activity_id'], 'hora_local': o.get('hora_local'),
                                 'data': o['data'], 'watts': o['watts'],
                                 'elegibilidade_aprendizagem': o['elegibilidade_aprendizagem']}
                                for o in usadas],
            'trajetoria': ser['trajetoria'],
        })
    return {'experimental': True, 'validado_preditivamente': False,
            'versao_estimador': VERSAO_ESTIMADOR, 'q_dia': q, 'ate': ate,
            'assinatura': _assinatura(usadas_para_hash, q, ate), 'itens': itens}
