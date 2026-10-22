"""Modelo Impulse-Response (Banister / Clarke & Skiba) com ajuste automatico (EXPERIMENTAL).

Modulo PURO: so numpy/scipy, sem base de dados nem rede. A ligacao aos dados (observacoes MMP,
atividades, persistencia) esta em modelo_ir_servico.py.

Estrutura (confirmada nas formulas do EstimarConstantes.xlsx):

    E_t = E_(t-1) * exp(-1/T1) + carga_t          (componente positiva, "fitness")
    G_t = G_(t-1) * exp(-1/T2) + carga_t          (componente negativa, "fadiga")
    P_t = P0 + K1 * E_t - K2 * G_t                (desempenho previsto, em watts)

  * estados iniciais E = G = 0; dia sem carga = 0 (so ha decaimento); varias atividades no
    mesmo dia somam-se (carga diaria);
  * T1 e T2 sao constantes de tempo exponenciais, em DIAS (nao o "span" do EWM do PMC);
  * K1, K2 >= 0: a componente positiva soma e a negativa subtrai (convencao de sinais unica);
  * erro = previsto - observado; objetivo = soma dos quadrados dos erros nos dias com teste.

Diferencas deliberadas em relacao a folha (ver LIMITES):
  * `lag_dias`: a folha (lag 0) inclui a carga do proprio dia do teste no previsor. Aqui o
    padrao e lag 1 (estado ate ao dia ANTERIOR): o esforco medido nao pode explicar-se a si
    proprio. `lag_dias=0` reproduz a folha exatamente (testado).
  * P0, K1, K2, T1 e T2 sao todos estimados (a folha fixa P0=274 e K1=1 por restricao do Solver).
  * os limites sao os deste modelo, nao os do exemplo (a solucao da folha estava encostada a
    T1=25 e T2=10, os seus proprios limites).

Ajuste historico NAO e previsao validada: as metricas aqui descrevem o erro DENTRO da amostra.
A avaliacao preditiva (treino estritamente anterior) esta em avaliar_preditivo().
"""
import hashlib
import json
import math
from datetime import date, timedelta

import numpy as np
from scipy.optimize import least_squares, lsq_linear
from scipy.signal import lfilter
from scipy.stats import f as _f

VERSAO_IR = 1

NOMES = ('p0', 'k1', 'k2', 't1', 't2')

# valores citados para o exemplo do video: SO inicializacao (um dos pontos de partida)
INICIAL_VIDEO = {'k1': 1.0, 'k2': 2.0, 't1': 35.0, 't2': 7.0}

EST_IDENTIFICAVEL = 'ajustado_identificavel'
EST_NAO_IDENTIFICAVEL = 'ajustado_nao_identificavel'
EST_INSUFICIENTE = 'dados_insuficientes'
EST_SEM_CONVERGENCIA = 'sem_convergencia'

# Limites e criterios (configuraveis; ficam gravados em cada ajuste).
#  t2 in [1,15] e t1 in [20,120]: intervalos que NAO se sobrepoem, por isso T1 (lenta) e T2
#  (rapida) ficam identificadas por construcao (sem troca de papeis); cobrem as faixas usuais
#  da literatura (fadiga de dias a ~2 semanas; adaptacao de ~3 semanas a ~4 meses) e incluem os
#  valores do exemplo (25/10) sem os usar como teto.
#  k in [0,6] W por unidade de carga: inclui os do exemplo (K2=2,33), os de referencia TP (1) e
#  os da nota tecnica do utilizador (~2,9 e ~4,1); K>=0 mantem a convencao de sinais.
#  p0 in [0,25 x mediana(y) ; 1,5 x max(y)]: nivel de base plausivel para a mesma duracao.
CONFIG_PADRAO = {
    'lag_dias': 1,
    'limites': {'k1': [0.0, 6.0], 'k2': [0.0, 6.0], 't1': [20.0, 120.0], 't2': [1.0, 15.0]},
    'p0_limites_rel': [0.25, 1.5],
    'min_obs': 15,                 # 3 observacoes por parametro livre (gl >= 10)
    'min_dias_cobertura': 180,     # >= ~1,5 x o T1 minimo + folga: ha tempo para a lenta se ver
    'min_datas_distintas': 12,
    'grelha_t': 16,
    'n_polimentos': 6,
    'tol_sse_alternativas': 0.02,  # solucoes com SSE ate +2% do melhor sao "equivalentes"
    'tol_param_rel': 0.25,         # ... e divergem se T1 ou T2 diferem mais de 25%
    'cond_max': 1.0e5,
    'perfil': True,
    'perfil_nivel': 0.95,
    'margem_limite': 0.001,        # a <0,1% da amplitude do limite = "no limite"
}

LIMITES = (
    'Ajuste historico (dentro da amostra): nao e previsao validada.',
    'O alvo e o melhor valor da duracao em toda a atividade, nao um teste padronizado; so ha '
    'observacoes quando o esforco ficou perto do melhor anterior, por isso o modelo descreve '
    'esforcos detetados, nao a capacidade do atleta.',
    'Carga = icu_training_load das atividades da mesma modalidade (unidades da Intervals.icu); '
    'os K dependem dessa escala e nao sao comparaveis com os do exemplo em TSS.',
    'P0 e constante no periodo ajustado: um deslocamento de base entre anos nao e modelado.',
)


# ── nucleo: componentes e desempenho (formulas da folha) ────────────────────

def _decai(cargas, tau):
    return lfilter([1.0], [1.0, -math.exp(-1.0 / float(tau))], np.asarray(cargas, dtype=float))


def _desloca(a, lag):
    if not lag:
        return a
    out = np.zeros_like(a)
    if lag < len(a):
        out[lag:] = a[:-lag]
    return out


def componentes(cargas, t1, t2, lag_dias=0):
    """(E, G) por dia. lag_dias=0: inclui a carga do proprio dia (como a folha)."""
    return _desloca(_decai(cargas, t1), lag_dias), _desloca(_decai(cargas, t2), lag_dias)


def desempenho(cargas, p0, k1, k2, t1, t2, lag_dias=0):
    """Serie diaria P = P0 + K1*E - K2*G (mesmas unidades do alvo)."""
    e, g = componentes(cargas, t1, t2, lag_dias)
    return p0 + k1 * e - k2 * g


def curvas_influencia(cargas, params, lag_dias=0):
    """Decomposicao diaria da resposta agregada: positiva = K1*E, negativa = K2*G (ambas >= 0) e
    liquida = P0 + positiva - negativa. E exatamente `desempenho` (mesma equacao)."""
    e, g = componentes(cargas, params['t1'], params['t2'], lag_dias)
    pos, neg = params['k1'] * e, params['k2'] * g
    return {'positiva': pos, 'negativa': neg, 'liquida': params['p0'] + pos - neg}


def contribuicao_atividade(n_dias, i_dia, carga, params, lag_dias=0):
    """Contribuicao de UMA atividade (carga `carga` no dia de indice `i_dia`) para a resposta
    diaria, de forma fechada: com u = t - lag - i_dia >= 0,
        positiva_t = carga*K1*exp(-u/T1),  negativa_t = carga*K2*exp(-u/T2),  liquida = pos - neg.
    Sem P0 (P0 e da resposta agregada, nao de uma atividade). Antes de i_dia+lag e zero: a carga de
    um dia so entra no estado `lag_dias` dias depois (sem informacao futura nem do proprio dia
    quando lag_dias >= 1). Linearidade: a soma sobre todas as atividades e `curvas_influencia`
    menos P0."""
    pos, neg = np.zeros(n_dias), np.zeros(n_dias)
    t0 = i_dia + int(lag_dias)
    if carga and 0 <= i_dia and t0 < n_dias:
        u = np.arange(0, n_dias - t0, dtype=float)
        pos[t0:] = carga * params['k1'] * np.exp(-u / float(params['t1']))
        neg[t0:] = carga * params['k2'] * np.exp(-u / float(params['t2']))
    return {'positiva': pos, 'negativa': neg, 'liquida': pos - neg}


def cargas_diarias(carga_por_dia, d_ini, d_fim):
    """Vetor diario [d_ini..d_fim] (datas ISO); dia sem carga = 0; cargas do mesmo dia somadas
    por quem preenche o dicionario (aqui apenas lidas)."""
    a, b = date.fromisoformat(d_ini), date.fromisoformat(d_fim)
    n = (b - a).days + 1
    if n <= 0:
        return np.zeros(0)
    out = np.zeros(n)
    for k, v in carga_por_dia.items():
        i = (date.fromisoformat(k[:10]) - a).days
        if 0 <= i < n and v is not None and math.isfinite(float(v)):
            out[i] += max(0.0, float(v))
    return out


def somar_por_dia(registos, campo_data='data', campo_carga='carga'):
    """{data: soma} a partir de registos (varias atividades no mesmo dia somam-se)."""
    out = {}
    for r in registos:
        d = str(r.get(campo_data) or '')[:10]
        v = r.get(campo_carga)
        if len(d) != 10 or not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            continue
        out[d] = out.get(d, 0.0) + float(v)
    return out


def erros(cargas, idx, y, params, lag_dias=0):
    """previsto - observado nos dias de teste (convencao da folha)."""
    p = desempenho(cargas, params['p0'], params['k1'], params['k2'], params['t1'], params['t2'], lag_dias)
    return p[np.asarray(idx, dtype=int)] - np.asarray(y, dtype=float)


def soma_quadrados(cargas, idx, y, params, lag_dias=0):
    e = erros(cargas, idx, y, params, lag_dias)
    return float(np.dot(e, e))


# ── inicializacao de P0 ───────────────────────────────────────────────────

def p0_inicial(observacoes, referencia):
    """P0 de partida do otimizador. Devolve (valor|None, origem, detalhe).

    1) 'ano_anterior': ultimo MMP elegivel CONHECIDO do ano civil anterior ao de `referencia`
       (mesma modalidade e duracao: quem chama so passa observacoes dessa unidade). Varias no
       ultimo dia: media (a ordem entre elas nao e conhecida).
    2) 'primeiras_observacoes': mediana das (ate) 3 observacoes mais antigas disponiveis.
    3) None: o otimizador usa a mediana dos alvos (so no ajuste historico, dentro da amostra).

    `observacoes` deve conter so o que era conhecido a data de `referencia` (o chamador aplica
    conhecida_desde): esta funcao nao filtra por instante de conhecimento, so por data do esforco.
    """
    obs = [o for o in observacoes or [] if o.get('data') and isinstance(o.get('watts'), (int, float))]
    if not obs:
        return None, None, {'motivo': 'sem_observacoes'}
    ano = int(str(referencia)[:4]) - 1
    do_ano = [o for o in obs if str(o['data'])[:4] == str(ano)]
    if do_ano:
        ult = max(str(o['data'])[:10] for o in do_ano)
        v = [float(o['watts']) for o in do_ano if str(o['data'])[:10] == ult]
        return (sum(v) / len(v), 'ano_anterior',
                {'ano': ano, 'data': ult, 'n_no_dia': len(v),
                 'test_ids': sorted(str(o.get('test_id')) for o in do_ano
                                    if str(o['data'])[:10] == ult)})
    ordenadas = sorted(obs, key=lambda o: (str(o['data'])[:10], str(o.get('test_id'))))[:3]
    w = sorted(float(o['watts']) for o in ordenadas)
    return (float(np.median(w)), 'primeiras_observacoes',
            {'n': len(w), 'ate': str(ordenadas[-1]['data'])[:10]})


# ── ajuste ────────────────────────────────────────────────────────────────

def _cfg(config):
    c = {**CONFIG_PADRAO, **(config or {})}
    c['limites'] = {**CONFIG_PADRAO['limites'], **((config or {}).get('limites') or {})}
    return c


def _limites_vetor(y, cfg):
    mediana, mx = float(np.median(y)), float(np.max(y))
    lo_rel, hi_rel = cfg['p0_limites_rel']
    L = cfg['limites']
    lo = np.array([lo_rel * mediana, L['k1'][0], L['k2'][0], L['t1'][0], L['t2'][0]])
    hi = np.array([hi_rel * mx, L['k1'][1], L['k2'][1], L['t1'][1], L['t2'][1]])
    return lo, hi


def _cobertura(idx, datas):
    ds = sorted(set(datas)) if datas is not None else None
    dias = (date.fromisoformat(ds[-1]) - date.fromisoformat(ds[0])).days if ds else \
        int(np.max(idx) - np.min(idx))
    return dias, (len(ds) if ds else int(len(set(np.asarray(idx).tolist()))))


def requisitos(obs_info, cfg):
    """Os TRES gates de identificacao (min_obs, min_datas_distintas, min_dias_cobertura) avaliados
    independentemente, para mostrar o que falta -- nao so o primeiro que falha. Nao altera nenhum gate.
    `gates_cumpridos` = os tres gates satisfeitos (nesse caso o motivo de dados vem de outra verificacao)."""
    o = obs_info or {}
    n = int(o.get('n') or 0)
    d = int(o.get('n_datas_distintas') or 0)
    c = int(o.get('dias_cobertura') or 0)
    minimo = {'observacoes': cfg['min_obs'], 'datas_distintas': cfg['min_datas_distintas'],
              'dias_cobertura': cfg['min_dias_cobertura']}
    atuais = {'observacoes': n, 'datas_distintas': d, 'dias_cobertura': c}
    em_falta = {k: max(0, minimo[k] - atuais[k]) for k in minimo}
    return {'minimo': minimo, 'atuais': atuais, 'em_falta': em_falta,
            'gates_cumpridos': not any(em_falta.values())}


def _insuficiente(motivo, n, obs_info, cfg, lo=None, hi=None):
    return {'estado': EST_INSUFICIENTE, 'motivo': motivo, 'parametros': None, 'metricas': None,
            'convergencia': None, 'identificabilidade': {'avisos': [motivo]},
            'obs': obs_info, 'limites': _limites_dict(cfg, lo, hi),
            'criterios': {k: cfg[k] for k in ('min_obs', 'min_dias_cobertura', 'min_datas_distintas')},
            'requisitos': requisitos(obs_info, cfg)}


def _limites_dict(cfg, lo=None, hi=None):
    d = {k: list(v) for k, v in cfg['limites'].items()}
    if lo is not None:
        d['p0'] = [float(lo[0]), float(hi[0])]
    return d


def _superficie(cargas, idx, y, lo, hi, cfg, p0=None):
    """SSE(T1,T2) com (P0,K1,K2) por minimos quadrados limitados (projecao variavel).
    p0 != None: P0 fixo (nao e estimado); so (K1,K2) sao resolvidos, sobre y - P0."""
    L = cfg['limites']
    g = int(cfg['grelha_t'])
    t1s = np.exp(np.linspace(math.log(L['t1'][0]), math.log(L['t1'][1]), g))
    t2s = np.exp(np.linspace(math.log(L['t2'][0]), math.log(L['t2'][1]), g))
    lag = int(cfg['lag_dias'])
    E = [_desloca(_decai(cargas, t), lag)[idx] for t in t1s]
    G = [_desloca(_decai(cargas, t), lag)[idx] for t in t2s]
    sse = np.full((g, g), np.inf)
    coef = {}
    ones = np.ones(len(idx))
    for i in range(g):
        for j in range(g):
            if p0 is None:
                A = np.column_stack([ones, E[i], -G[j]])
                r = lsq_linear(A, y, bounds=(lo[:3], hi[:3]), method='bvls')
                coef[(i, j)] = r.x
            else:
                A = np.column_stack([E[i], -G[j]])
                r = lsq_linear(A, y - p0, bounds=(lo[1:3], hi[1:3]), method='bvls')
                coef[(i, j)] = np.array([p0, r.x[0], r.x[1]])
            sse[i, j] = float(np.dot(r.fun, r.fun))
    return t1s, t2s, sse, coef


def _cruza(t_a, s_a, t_b, s_b, crit):
    """T onde o SSE de perfil cruza `crit`, por interpolacao linear entre dois pontos da grelha."""
    if s_b == s_a:
        return t_a
    f = (crit - s_a) / (s_b - s_a)
    return t_a + min(1.0, max(0.0, f)) * (t_b - t_a)


def _perfil(cargas, idx, y, lo, hi, cfg, x_melhor, k, pontos=41, p0=None):
    """SSE de perfil de T1 (k=3) ou T2 (k=4): T fixo numa grelha fina (inclui o otimo), a outra
    constante de tempo otimizada numa grelha (inclui a otima) e (P0,K1,K2) exatos por minimos
    quadrados limitados. Devolve (valores_de_T, sse_de_perfil)."""
    L = cfg['limites']
    nome, outro = ('t1', 't2') if k == 3 else ('t2', 't1')
    k_o = 7 - k
    lag = int(cfg['lag_dias'])
    ts = np.exp(np.linspace(math.log(L[nome][0]), math.log(L[nome][1]), pontos))
    ts = np.sort(np.append(ts, x_melhor[k]))
    os_ = np.exp(np.linspace(math.log(L[outro][0]), math.log(L[outro][1]), 3 * int(cfg['grelha_t']) // 2))
    os_ = np.sort(np.append(os_, x_melhor[k_o]))
    comp_o = [_desloca(_decai(cargas, t), lag)[idx] for t in os_]
    ones = np.ones(len(idx))
    prof = np.empty(len(ts))
    for a, t in enumerate(ts):
        c_t = _desloca(_decai(cargas, t), lag)[idx]
        melhor = np.inf
        for c_o in comp_o:
            e, g = (c_t, c_o) if k == 3 else (c_o, c_t)
            if p0 is None:
                A = np.column_stack([ones, e, -g])
                r = lsq_linear(A, y, bounds=(lo[:3], hi[:3]), method='bvls')
            else:
                A = np.column_stack([e, -g])
                r = lsq_linear(A, y - p0, bounds=(lo[1:3], hi[1:3]), method='bvls')
            s = float(np.dot(r.fun, r.fun))
            if s < melhor:
                melhor = s
        prof[a] = melhor
    return ts, prof


def _escolhe_celulas(sse, k):
    """k celulas de menor SSE, afastadas entre si (>=2 passos), para varios pontos de partida."""
    g = sse.shape[0]
    ordem = np.dstack(np.unravel_index(np.argsort(sse, axis=None), sse.shape))[0]
    escolhidas = []
    for i, j in ordem:
        if not np.isfinite(sse[i, j]):
            continue
        if all(max(abs(i - a), abs(j - b)) >= 2 for a, b in escolhidas):
            escolhidas.append((int(i), int(j)))
        if len(escolhidas) >= k:
            break
    return escolhidas


def _polir(x0, cargas, idx, y, lo, hi, lag, p0=None):
    if p0 is not None:
        # P0 fixo: so (K1,K2,T1,T2) variam; x0 vem com 5 entradas (a 1.a e o P0 e e ignorada)
        escala4 = np.array([0.5, 0.5, 10.0, 3.0])

        def res4(x):
            e = _desloca(_decai(cargas, x[2]), lag)[idx]
            g = _desloca(_decai(cargas, x[3]), lag)[idx]
            return p0 + x[0] * e - x[1] * g - y

        x04 = np.minimum(np.maximum(np.asarray(x0, dtype=float)[1:], lo[1:]), hi[1:])
        return least_squares(res4, x04, bounds=(lo[1:], hi[1:]), x_scale=escala4, method='trf', max_nfev=400)
    ref = float(np.median(y))
    escala = np.array([0.1 * ref, 0.5, 0.5, 10.0, 3.0])

    def res(x):
        e = _desloca(_decai(cargas, x[3]), lag)[idx]
        g = _desloca(_decai(cargas, x[4]), lag)[idx]
        return x[0] + x[1] * e - x[2] * g - y

    x0 = np.minimum(np.maximum(np.asarray(x0, dtype=float), lo), hi)
    r = least_squares(res, x0, bounds=(lo, hi), x_scale=escala, method='trf', max_nfev=400)
    return r


def ajustar(cargas, idx, y, config=None, p0_ini=None, datas=None, p0_fixo=None):
    """Estima P0, K1, K2, T1, T2 minimizando a soma dos quadrados dos erros.

    cargas: carga diaria (vetor, dia 0 = primeiro dia). idx: indice do dia de cada observacao.
    y: watts observados. datas: datas ISO das observacoes (so para cobertura/relatorio).
    p0_ini: P0 de partida (ver p0_inicial); sem ele usa-se a mediana de y.
    p0_fixo: P0 FIXO (referencia do CP Model): nao e estimado nem pode ser alterado pelo otimizador;
    so K1, K2, T1 e T2 sao estimados (4 parametros livres, gl = n - 4). Sem inicializacao
    alternativa: nao ha mediana nem ano anterior neste modo.

    Estrategia: (1) superficie SSE(T1,T2) com os parametros lineares resolvidos exatamente;
    (2) polimento nao linear dos 5 parametros a partir das melhores celulas separadas, do ponto
    do video e do ponto de P0 inicial; (3) diagnostico de identificabilidade (limites, solucoes
    equivalentes divergentes, condicionamento, intervalo de perfil de T1/T2).
    """
    cfg = _cfg(config)
    if p0_fixo is not None:
        if isinstance(p0_fixo, bool) or not isinstance(p0_fixo, (int, float)) or not math.isfinite(p0_fixo) or p0_fixo <= 0:
            raise ValueError('p0_fixo tem de ser um numero finito > 0')
        p0_fixo = float(p0_fixo)
    n_livres = 5 if p0_fixo is None else 4
    cargas = np.asarray(cargas, dtype=float)
    idx = np.asarray(idx, dtype=int)
    y = np.asarray(y, dtype=float)
    n = len(y)
    lag = int(cfg['lag_dias'])
    obs_info = {'n': n}
    if n:
        dias, ndist = _cobertura(idx, datas)
        obs_info.update({'dias_cobertura': dias, 'n_datas_distintas': ndist})
        if datas:
            obs_info.update({'data_ini': sorted(datas)[0], 'data_fim': sorted(datas)[-1]})
    if n < cfg['min_obs']:
        return _insuficiente('menos_de_%d_observacoes' % cfg['min_obs'], n, obs_info, cfg)
    if obs_info['n_datas_distintas'] < cfg['min_datas_distintas']:
        return _insuficiente('menos_de_%d_datas_distintas' % cfg['min_datas_distintas'], n, obs_info, cfg)
    if obs_info['dias_cobertura'] < cfg['min_dias_cobertura']:
        return _insuficiente('cobertura_inferior_a_%d_dias' % cfg['min_dias_cobertura'], n, obs_info, cfg)
    if idx.min() < 0 or idx.max() >= len(cargas):
        raise ValueError('indice de observacao fora da serie de cargas')
    if float(np.sum(cargas[:idx.max() + 1])) <= 0:
        return _insuficiente('sem_carga_no_periodo', n, obs_info, cfg)
    if float(np.std(y)) <= 1e-9:
        return _insuficiente('alvo_sem_variacao', n, obs_info, cfg)

    lo, hi = _limites_vetor(y, cfg)
    if p0_fixo is not None:
        lo[0] = hi[0] = p0_fixo          # so para relatorio: P0 nao e variavel
    t1s, t2s, sse_g, coef_g = _superficie(cargas, idx, y, lo, hi, cfg, p0_fixo)
    ref = float(np.median(y))
    p0_part = p0_fixo if p0_fixo is not None else (float(p0_ini) if p0_ini is not None else ref)

    partidas = []
    for (i, j) in _escolhe_celulas(sse_g, int(cfg['n_polimentos'])):
        c = coef_g[(i, j)]
        partidas.append(('grelha', [c[0], c[1], c[2], t1s[i], t2s[j]]))
    partidas.append(('video', [p0_part, INICIAL_VIDEO['k1'], INICIAL_VIDEO['k2'],
                               INICIAL_VIDEO['t1'], INICIAL_VIDEO['t2']]))
    ib = np.unravel_index(np.argmin(sse_g), sse_g.shape)
    cb = coef_g[(int(ib[0]), int(ib[1]))]
    partidas.append(('p0_inicial', [p0_part, cb[1], cb[2], t1s[ib[0]], t2s[ib[1]]]))

    solucoes = []
    for origem, x0 in partidas:
        try:
            r = _polir(x0, cargas, idx, y, lo, hi, lag, p0_fixo)
        except Exception:
            continue
        if not np.all(np.isfinite(r.x)):
            continue
        xs = r.x if p0_fixo is None else np.concatenate([[p0_fixo], r.x])
        solucoes.append({'origem': origem, 'x': xs, 'sse': float(2.0 * r.cost), 'res': r})
    if not solucoes:
        return {**_insuficiente('sem_solucao_numerica', n, obs_info, cfg, lo, hi),
                'estado': EST_SEM_CONVERGENCIA}
    solucoes.sort(key=lambda s: s['sse'])
    melhor = solucoes[0]
    x = melhor['x']
    sse = melhor['sse']
    gl = n - n_livres
    sst = float(np.sum((y - y.mean()) ** 2))
    metricas = {
        'sse': sse, 'rmse': math.sqrt(sse / n), 'n': n, 'gl': gl,
        'r2': (1.0 - sse / sst) if sst > 0 else None,
        'r2_ajustado': (1.0 - (sse / gl) / (sst / (n - 1))) if sst > 0 and gl > 0 else None,
        'mae': float(np.mean(np.abs(_desloca(_decai(cargas, x[3]), lag)[idx] * x[1]
                                    - _desloca(_decai(cargas, x[4]), lag)[idx] * x[2] + x[0] - y))),
        'tipo': 'ajuste_historico_dentro_da_amostra'}

    avisos = []
    # (a) parametros encostados a um limite
    amp = hi - lo
    no_limite = []
    for k, nome in enumerate(NOMES):
        if amp[k] <= 0:            # inclui o P0 fixo (amplitude 0): nao se estima, nao pode estar "no limite"
            continue
        m = cfg['margem_limite'] * amp[k]
        if x[k] <= lo[k] + m:
            no_limite.append({'parametro': nome, 'limite': 'inferior', 'valor': float(x[k])})
        elif x[k] >= hi[k] - m:
            no_limite.append({'parametro': nome, 'limite': 'superior', 'valor': float(x[k])})
    for d in no_limite:
        avisos.append(f"parametro_no_limite:{d['parametro']}:{d['limite']}")

    # (b) solucoes equivalentes que divergem
    alt = [s for s in solucoes if s['sse'] <= sse * (1 + cfg['tol_sse_alternativas']) + 1e-12]
    diverge = False
    for k in (3, 4):
        v = [s['x'][k] for s in alt]
        if (max(v) - min(v)) > cfg['tol_param_rel'] * float(np.median(v)):
            diverge = True
    if diverge:
        avisos.append('solucoes_equivalentes_divergentes')

    # (c) condicionamento do jacobiano (colunas na escala da amplitude do limite)
    cond, erros_padrao = None, None
    try:
        J = melhor['res'].jac * (amp if p0_fixo is None else amp[1:])[None, :]
        sv = np.linalg.svd(J, compute_uv=False)
        cond = float(sv[0] / sv[-1]) if sv[-1] > 0 else float('inf')
        if not math.isfinite(cond) or cond > cfg['cond_max']:
            avisos.append('mal_condicionado')
        elif gl > 0:
            cov = (sse / gl) * np.linalg.inv(melhor['res'].jac.T @ melhor['res'].jac)
            nomes_livres = NOMES if p0_fixo is None else NOMES[1:]
            erros_padrao = {nome: float(math.sqrt(max(cov[k, k], 0.0))) for k, nome in enumerate(nomes_livres)}
            if p0_fixo is not None:
                erros_padrao = {'p0': None, **erros_padrao}
    except Exception:
        avisos.append('mal_condicionado')

    # (d) intervalo de perfil (F) para T1 e T2, sobre a superficie ja calculada
    ic_perfil = None
    if cfg['perfil'] and gl > 0:
        crit = sse * (1.0 + _f.ppf(cfg['perfil_nivel'], 1, gl) / gl)
        ic_perfil = {}
        for nome, k in (('t1', 3), ('t2', 4)):
            ts, prof = _perfil(cargas, idx, y, lo, hi, cfg, x, k, p0=p0_fixo)
            ok = np.where(prof <= crit)[0]
            if ok.size == 0:
                ic_perfil[nome] = [float(x[k]), float(x[k])]
                continue
            a, b = int(ok[0]), int(ok[-1])
            esq = ts[0] if a == 0 else _cruza(ts[a - 1], prof[a - 1], ts[a], prof[a], crit)
            dir_ = ts[-1] if b == len(ts) - 1 else _cruza(ts[b], prof[b], ts[b + 1], prof[b + 1], crit)
            ic_perfil[nome] = [float(esq), float(dir_)]
            if a == 0 or b == len(ts) - 1:
                avisos.append(f'ic_perfil_toca_limite:{nome}')
    ident = {
        'avisos': avisos, 'parametros_no_limite': no_limite, 'condicionamento': cond,
        'erros_padrao': erros_padrao, 'ic_perfil': ic_perfil,
        'n_solucoes_equivalentes': len(alt),
        'solucoes_equivalentes': [{'origem': s['origem'], 'sse': s['sse'],
                                   **{nome: float(s['x'][k]) for k, nome in enumerate(NOMES)}}
                                  for s in alt[:8]],
        'n_partidas': len(solucoes), 'p0_fixo': p0_fixo is not None,
        'parametros_livres': [nm for nm in NOMES if not (p0_fixo is not None and nm == 'p0')]}
    convergiu = bool(melhor['res'].status > 0)
    if not convergiu:
        estado = EST_SEM_CONVERGENCIA
        avisos.append('sem_convergencia')
    elif avisos:
        estado = EST_NAO_IDENTIFICAVEL
    else:
        estado = EST_IDENTIFICAVEL
    return {'estado': estado, 'motivo': None,
            'parametros': {nome: float(x[k]) for k, nome in enumerate(NOMES)},
            'metricas': metricas,
            'convergencia': {'convergiu': convergiu, 'status': int(melhor['res'].status),
                             'mensagem': str(melhor['res'].message), 'nfev': int(melhor['res'].nfev),
                             'origem_melhor_partida': melhor['origem']},
            'identificabilidade': ident, 'obs': obs_info,
            'limites': _limites_dict(cfg, lo, hi), 'p0_inicial_usado': p0_part,
            'p0_fixo': p0_fixo, 'lag_dias': lag}


# ── previsao e avaliacao preditiva (sem fuga temporal) ───────────────────────

def prever(carga_por_dia, params, data_alvo, lag_dias=1):
    """Previsao para `data_alvo` usando SO cargas de dias estritamente anteriores a ela.

    Constroi o vetor de cargas ate data_alvo-1 (as posteriores nem chegam a funcao).
    """
    if not carga_por_dia:
        return None
    ate = (date.fromisoformat(data_alvo) - timedelta(days=1)).isoformat()
    ini = min(k[:10] for k in carga_por_dia)
    if ini > ate:
        return None
    c = cargas_diarias({k: v for k, v in carga_por_dia.items() if k[:10] <= ate}, ini, ate)
    # o dia alvo e o ultimo+1: com lag 1 o previsor e o estado ao fim do dia anterior
    c = np.concatenate([c, [0.0]])
    p = desempenho(c, params['p0'], params['k1'], params['k2'], params['t1'], params['t2'], lag_dias)
    return float(p[-1])


def ajustar_observacoes(observacoes, carga_por_dia, referencia, config=None, p0_obs=None, p0_fixo=None):
    """Ajuste a partir de observacoes {data, watts, test_id} e carga {data: carga}.

    p0_obs: observacoes usadas para escolher o P0 de partida (por defeito as proprias, o que so
    e correto para o ajuste historico). `referencia` (YYYY-MM-DD) define o 'ano anterior'.
    p0_fixo: P0 de referencia (CP Model), fixo neste ajuste. Com ele NAO se chama p0_inicial: nem o
    ano anterior nem as primeiras observacoes nem a mediana podem substituir a referencia.
    """
    obs = sorted(observacoes, key=lambda o: (str(o['data'])[:10], str(o.get('test_id'))))
    if not obs:
        return ajustar(np.zeros(1), [], [], config)
    d0 = min([str(o['data'])[:10] for o in obs] + [k[:10] for k in carga_por_dia])
    d1 = max(str(o['data'])[:10] for o in obs)
    cargas = cargas_diarias({k: v for k, v in carga_por_dia.items() if k[:10] <= d1}, d0, d1)
    idx = [(date.fromisoformat(str(o['data'])[:10]) - date.fromisoformat(d0)).days for o in obs]
    y = [float(o['watts']) for o in obs]
    datas = [str(o['data'])[:10] for o in obs]
    if p0_fixo is not None:
        r = ajustar(cargas, idx, y, config, None, datas, p0_fixo=p0_fixo)
        r['p0_inicial'] = {'valor': float(p0_fixo), 'origem': 'p0_fixo_referencia_cp', 'detalhe': None,
                           'referencia': referencia}
        r['serie_ini'] = d0
        return r
    v, origem, det = p0_inicial(obs if p0_obs is None else p0_obs, referencia)
    r = ajustar(cargas, idx, y, config, v, datas)
    r['p0_inicial'] = {'valor': v, 'origem': origem or 'mediana_dos_alvos', 'detalhe': det,
                       'referencia': referencia}
    r['serie_ini'] = d0
    return r


def avaliar_preditivo(alvos, treino_de, carga_por_dia, config=None, metodos_base=None, p0_de=None):
    """Walk-forward: para cada alvo recalibra SO com o que era conhecido antes dele e preve.

    alvos: [{test_id, data, watts}]; treino_de(alvo) -> observacoes conhecidas (o chamador
    aplica 'conhecida_desde'); aqui so se aceitam as de data estritamente anterior. As cargas
    usadas vao ate ao dia anterior ao alvo. O previsor do dia alvo e P(alvo) com lag>=1: a
    carga do proprio dia nunca entra.

    Devolve previsoes por alvo {y, ir, ...}, SEM metricas agregadas (ver o servico, que compara
    com M0/M1/M2 nos mesmos alvos). O erro dentro da amostra NAO e usado aqui.

    p0_de(alvo) -> P0 de referencia JA CONHECIDO antes do alvo (ou None). Com p0_de, um alvo sem
    referencia nao e previsto ('sem_referencia_p0_conhecida'): nunca se usa uma referencia futura nem
    se cai para um P0 livre. Sem p0_de mantem-se o comportamento anterior (P0 livre).
    """
    cfg = _cfg(config)
    if int(cfg['lag_dias']) < 1:
        raise ValueError('a avaliacao preditiva exige lag_dias >= 1 (a carga do dia alvo nao e conhecida)')
    saida = []
    for a in sorted(alvos, key=lambda o: (str(o['data'])[:10], str(o.get('test_id')))):
        d = str(a['data'])[:10]
        treino = [o for o in treino_de(a) if str(o['data'])[:10] < d]
        reg = {'test_id': a.get('test_id'), 'data': d, 'y': float(a['watts']),
               'n_treino': len(treino), 'ir': None, 'estado_ajuste': None, 'motivo': None}
        if not treino:
            reg['motivo'] = 'sem_historico'
            saida.append(reg)
            continue
        cargas_conhecidas = {k: v for k, v in carga_por_dia.items() if k[:10] < d}
        p0_alvo = None
        if p0_de is not None:
            p0_alvo = p0_de(a)
            if p0_alvo is None:
                reg['motivo'] = 'sem_referencia_p0_conhecida'
                saida.append(reg)
                continue
        r = ajustar_observacoes(treino, cargas_conhecidas, d, {**cfg, 'perfil': False}, p0_fixo=p0_alvo)
        reg['estado_ajuste'] = r['estado']
        reg['p0_origem'] = (r.get('p0_inicial') or {}).get('origem')
        if r['parametros'] is None:
            reg['motivo'] = r.get('motivo') or r['estado']
            saida.append(reg)
            continue
        reg['ir'] = prever(cargas_conhecidas, r['parametros'], d, int(cfg['lag_dias']))
        reg['avisos_identificabilidade'] = r['identificabilidade']['avisos']
        reg['parametros'] = r['parametros']
        saida.append(reg)
    return saida


def assinatura(obj):
    """sha256 estavel de uma estrutura JSON (entradas/configuracao de um ajuste)."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str,
                                     separators=(',', ':')).encode()).hexdigest()
