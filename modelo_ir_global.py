"""Modelo IR GLOBAL de referencia: analise SEPARADA dos modelos por modalidade. Nucleo puro.

Nao substitui nem altera nenhum ajuste por modalidade (modelo_ir.py); tem as suas proprias
estimativas, metricas e limitacoes. Nao e persistido nem validado preditivamente.

Definicao (e porque e defensavel apenas COM os testes abaixo)
-------------------------------------------------------------
 * Grupo = UMA duracao (60, 300 ou 1200 s), varias modalidades. Nunca se misturam duracoes.
 * Nao se somam nem se comparam watts de modalidades diferentes: o erro de cada unidade e
   relativo (y/mediana da unidade) e K e relativo ao nivel de base da propria unidade.
 * Carga da unidade u (modalidade m): c_u(t) = carga_m(t) + LAMBDA * carga_outras_modalidades(t),
   com icu_training_load (a mesma carga do modelo por modalidade; TRIMP/hrTSS nao se misturam).
   LAMBDA em [0,1] e o COEFICIENTE DE TRANSFERENCIA ENTRE MODALIDADES, estimado: 0 = as outras
   modalidades nao influenciam esta (modelos independentes); 1 = a carga conta por igual
   (carga global somada). Os dados dizem onde estao, ou que nao distinguem.
 * Equacao por unidade: y_u(t) = P0_u * (1 + K1*E(c_u; T1) - K2*G(c_u; T2)) (mesmos E, G, T e lag
   do modelo por modalidade). K1 e K2 sao FRACOES DO NIVEL DE BASE P0_u por unidade de carga, por
   isso partilhaveis entre unidades de escalas diferentes sem depender de uma normalizacao
   amostral; o P0_u (em watts, proprio de cada unidade) e estimado em fecho (minimos quadrados,
   sem limites artificiais). K em W por carga da unidade u = K * P0_u. Partilhados: K1, K2, T1, T2
   e LAMBDA. O alvo so e dividido pela mediana da unidade como PESO do erro (erro relativo igual
   nas unidades), nao entra no modelo.
 * Pressuposto TESTAVEL e testado: dinamica partilhada (K, T iguais em todas as unidades). Teste F
   aninhado contra ajustes por unidade (com a mesma carga efetiva). Se for rejeitado, o estado e
   'global_heterogeneo': o modelo global nao descreve estas unidades como uma so e NAO se
   interpretam os parametros partilhados.
 * Mesmos limites de T e mesmo lag (1 dia) do modelo por modalidade, mesmos criterios de cobertura
   temporal. K relativo em [0, 0.03] por unidade de carga: a P0 >= 200 W cobre o teto de 6 W por
   unidade de carga do modelo por modalidade (e o K2 = 2,33 do exemplo a 274 W = 0,0085).

Limites assumidos (nao removiveis por calculo): erros independentes e homocedasticos nos testes F
(os alvos sao 'perto do melhor anterior' e as series tem autocorrelacao: os p-valores sao
indicativos); observacoes de duracoes/modalidades no mesmo dia estao correlacionadas, por isso a
informacao independente e menor do que n sugere.

Leitura dos estados (auditoria): 'nao rejeitada' NAO e prova de homogeneidade nem de equivalencia (o
teste tem pouco poder com poucas observacoes por unidade). Por isso o estado so e 'identificavel' se
(i) TODAS as unidades entraram no teste de homogeneidade (>= min_obs_homogeneidade observacoes e ajuste
proprio convergido; caso contrario 'homogeneidade_parcial' e a dinamica partilhada fica assumida para
as restantes) e (ii) o p-valor e conclusivo (com bootstrap, replicas validas suficientes para p poder
ficar abaixo de alfa; senao 'bootstrap_inconclusivo'). Os testes de lambda = 0 e lambda = 1 usam F(1, gl)
com a hipotese na fronteira de [0, 1]: sao indicativos e, nessa fronteira, conservadores. Nao ha
intervalos de confianca para os parametros: so avisos de identificabilidade (limite, condicao do
jacobiano, solucoes equivalentes divergentes).
"""
import math
from datetime import date

import numpy as np
from scipy.optimize import least_squares, lsq_linear
from scipy.stats import f as _f

import modelo_ir as ir

VERSAO_GLOBAL = 1
EST_IDENTIFICAVEL = 'global_ajustado_identificavel'
EST_NAO_IDENTIFICAVEL = 'global_ajustado_nao_identificavel'
EST_HETEROGENEO = 'global_heterogeneo'
EST_INSUFICIENTE = 'global_dados_insuficientes'
EST_SEM_CONVERGENCIA = 'global_sem_convergencia'

CONFIG_GLOBAL = {
    'lag_dias': 1,
    'limites': {'k1': [0.0, 0.03], 'k2': [0.0, 0.03], 't1': [20.0, 120.0], 't2': [1.0, 15.0],
                'lambda': [0.0, 1.0]},
    'min_obs_unidade': 4,           # uma unidade com menos entra so como excluida (o P0_u precisa de dados)
    'min_obs_por_parametro': 3,     # n_total >= 3 x (5 partilhados + n_unidades)
    'min_dias_cobertura': 180,
    'min_datas_distintas': 12,
    'grelha_t': 12,
    'grelha_lambda': [0.0, 0.25, 0.5, 0.75, 1.0],
    'n_polimentos': 5,
    'alfa': 0.05,
    'bootstrap_B': 39,              # p minimo = 1/40 = 0,025 < alfa; 0 desliga (so F indicativo)
    'triagem_p': 0.20,              # so se corre o bootstrap quando o F indicativo da p < 0,20 (o F e anti-conservador)
    'min_obs_homogeneidade': 15,    # unidade entra no teste de homogeneidade (ajuste proprio de 5 parametros)
    'tol_sse_alternativas': 0.02,
    'tol_param_rel': 0.25,
    'tol_lambda': 0.5,
    'cond_max': 1.0e5,
    'margem_limite': 0.001,
}

LIMITES = (
    'Analise global de REFERENCIA, separada dos modelos por modalidade: nao os substitui nem os '
    'altera. Ajuste historico (dentro da amostra): nao e previsao validada.',
    'Um grupo = uma duracao. K e relativo ao nivel de base P0 da propria unidade: nao ha soma nem '
    'comparacao direta de watts entre modalidades.',
    'Dinamica (K1, K2, T1, T2) PARTILHADA entre modalidades: pressuposto testado (teste F contra '
    'ajustes por unidade); se rejeitado o estado e global_heterogeneo.',
    'Transferencia de carga entre modalidades (LAMBDA) estimada; se os dados nao distinguem 0 de 1 '
    'isso e dito. Carga = icu_training_load das modalidades ciclicas (TYPE_MAP); musculacao, TRIMP '
    'e hrTSS nao entram.',
    'Os testes F assumem erros independentes e homocedasticos; os alvos sao esforcos detetados '
    'perto do melhor anterior e ha autocorrelacao: os p-valores sao indicativos.',
)


def _cfg(c=None):
    out = {k: (dict(v) if isinstance(v, dict) else (list(v) if isinstance(v, list) else v))
           for k, v in CONFIG_GLOBAL.items()}
    for k, v in (c or {}).items():
        out[k] = v
    return out


def normalizar(y):
    """(y_pct, escala): y_pct = 100*y/mediana(y) (escala = mediana, em watts)."""
    y = np.asarray(y, dtype=float)
    s = float(np.median(y))
    return 100.0 * y / s, s


def _eixo(unidades, carga_mod):
    ini = [min(u['datas']) for u in unidades if u['datas']] + [min(c) for c in carga_mod.values() if c]
    fim = [max(u['datas']) for u in unidades if u['datas']]
    return min(ini), max(fim)


def _prepara(unidades, carga_mod, cfg):
    """Por unidade: indices das observacoes, alvo %, escala e cargas propria/outras (vetores diarios)."""
    d0, d1 = _eixo(unidades, carga_mod)
    cargas = {m: ir.cargas_diarias({k: v for k, v in c.items() if k[:10] <= d1}, d0, d1)
              for m, c in carga_mod.items()}
    n_dias = len(next(iter(cargas.values()))) if cargas else (date.fromisoformat(d1) - date.fromisoformat(d0)).days + 1
    total = sum(cargas.values()) if cargas else np.zeros(n_dias)
    out = []
    for u in unidades:
        ordem = sorted(range(len(u['datas'])), key=lambda i: (u['datas'][i], i))
        datas = [u['datas'][i] for i in ordem]
        y = np.array([float(u['y'][i]) for i in ordem])
        yp, esc = normalizar(y)
        idx = np.array([(date.fromisoformat(d) - date.fromisoformat(d0)).days for d in datas])
        propria = cargas.get(u['modalidade'], np.zeros(n_dias))
        out.append({'id': u['id'], 'modalidade': u['modalidade'], 'datas': datas, 'y_w': y, 'y': yp,
                    'escala': esc, 'idx': idx, 'propria': propria, 'outras': total - propria})
    return out, d0, d1


def _componentes_obs(un, t, lag):
    """(E,G)-estado a t dias, nos dias das observacoes, separados em carga propria e das outras."""
    return {
        'p': ir._desloca(ir._decai(un['propria'], t), lag)[un['idx']],
        'o': ir._desloca(ir._decai(un['outras'], t), lag)[un['idx']],
    }


def _centra(a, grupos):
    out = np.array(a, dtype=float)
    for sl in grupos:
        out[sl] = out[sl] - out[sl].mean()
    return out


def _grupos(unds):
    g, i = [], 0
    for u in unds:
        n = len(u['idx'])
        g.append(slice(i, i + n))
        i += n
    return g


def _f_x(unds, x, lag):
    """f_u(t) = K1*E - K2*G nos dias das observacoes, por unidade, para x = (k1,k2,t1,t2,lambda)."""
    k1, k2, t1, t2, lam = x
    out = []
    for u in unds:
        c = u['propria'] + lam * u['outras']
        e = ir._desloca(ir._decai(c, t1), lag)[u['idx']]
        g = ir._desloca(ir._decai(c, t2), lag)[u['idx']]
        out.append(k1 * e - k2 * g)
    return out


def _p0_fecho(y, f):
    """P0 (em % do nivel tipico) que minimiza sum (y - P0*(1+f))^2: fecho exato."""
    z = 1.0 + f
    den = float(np.dot(z, z))
    return float(np.dot(y, z) / den) if den > 0 else float('nan')


def _residuos(unds, x, lag, grupos, yc=None):
    """Residuos relativos empilhados com P0_u em fecho (y em % do nivel tipico da unidade)."""
    res = []
    for u, f in zip(unds, _f_x(unds, x, lag)):
        p0 = _p0_fecho(u['y'], f)
        res.append(u['y'] - p0 * (1.0 + f))
    return np.concatenate(res)


def _superficie(unds, cfg, grupos, yc):
    L = cfg['limites']
    g = int(cfg['grelha_t'])
    lam_g = list(cfg['grelha_lambda'])
    t1s = np.exp(np.linspace(math.log(L['t1'][0]), math.log(L['t1'][1]), g))
    t2s = np.exp(np.linspace(math.log(L['t2'][0]), math.log(L['t2'][1]), g))
    lag = int(cfg['lag_dias'])
    CE = [[_componentes_obs(u, t, lag) for u in unds] for t in t1s]
    CG = [[_componentes_obs(u, t, lag) for u in unds] for t in t2s]

    def col(C, lam):
        return np.concatenate([c['p'] + lam * c['o'] for c in C])

    # so serve para escolher pontos de partida: superficie do modelo ADITIVO centrado por unidade
    # (P0_u eliminado por centragem), com K sem teto (os limites aplicam-se no polimento)
    sse = np.full((len(lam_g), g, g), np.inf)
    coef = np.zeros((len(lam_g), g, g, 2))
    lo = [0.0, 0.0]
    hi = [np.inf, np.inf]
    for a, lam in enumerate(lam_g):
        Es = [_centra(col(CE[i], lam), grupos) for i in range(g)]
        Gs = [_centra(col(CG[j], lam), grupos) for j in range(g)]
        for i in range(g):
            for j in range(g):
                A = np.column_stack([Es[i], -Gs[j]])
                r = lsq_linear(A, yc, bounds=(lo, hi), method='bvls')
                sse[a, i, j] = float(np.dot(r.fun, r.fun))
                coef[a, i, j] = r.x
    return lam_g, t1s, t2s, sse, coef


def _polir(x0, unds, cfg, grupos, yc, lam_fixo=None):
    L = cfg['limites']
    lag = int(cfg['lag_dias'])
    lo = np.array([L['k1'][0], L['k2'][0], L['t1'][0], L['t2'][0], L['lambda'][0]])
    hi = np.array([L['k1'][1], L['k2'][1], L['t1'][1], L['t2'][1], L['lambda'][1]])
    if lam_fixo is not None:
        lo[4] = hi[4] = float(lam_fixo)
        x0 = list(x0)
        x0[4] = float(lam_fixo)
        # least_squares nao aceita lo == hi: otimiza so 4 variaveis
        def res4(v):
            return _residuos(unds, [v[0], v[1], v[2], v[3], float(lam_fixo)], lag, grupos, yc)
        r = least_squares(res4, np.minimum(np.maximum(np.array(x0[:4], float), lo[:4]), hi[:4]),
                          bounds=(lo[:4], hi[:4]), x_scale=np.array([1e-4, 1e-4, 10.0, 3.0]),
                          method='trf', max_nfev=300)
        x = np.append(r.x, float(lam_fixo))
        return x, float(np.dot(r.fun, r.fun)), r
    x0 = np.minimum(np.maximum(np.asarray(x0, dtype=float), lo), hi)
    r = least_squares(lambda x: _residuos(unds, x, lag, grupos, yc), x0, bounds=(lo, hi),
                      x_scale=np.array([1e-4, 1e-4, 10.0, 3.0, 0.3]), method='trf', max_nfev=400)
    return r.x, float(np.dot(r.fun, r.fun)), r


def _escolhe(sse, k):
    """k celulas (lambda,t1,t2) de menor SSE, distintas em (t1,t2) ou lambda (afastadas)."""
    ordem = np.dstack(np.unravel_index(np.argsort(sse, axis=None), sse.shape))[0]
    esc = []
    for a, i, j in ordem:
        if not np.isfinite(sse[a, i, j]):
            continue
        if all(abs(a - a2) >= 2 or max(abs(i - i2), abs(j - j2)) >= 2 for a2, i2, j2 in esc):
            esc.append((int(a), int(i), int(j)))
        if len(esc) >= k:
            break
    return esc


def _p_f(sse_restr, sse_livre, df_extra, df_res):
    if df_extra <= 0 or df_res <= 0 or sse_livre <= 0:
        return None
    F = max(0.0, (sse_restr - sse_livre) / df_extra) / (sse_livre / df_res)
    return float(1.0 - _f.cdf(F, df_extra, df_res))


def _ajusta_pool(unds, cfg, arranque_extra=None):
    """Ajuste pooled multi-inicio. Devolve (x, sse, resultado_otimizador, [(sse,x,res)], arranques)."""
    L = cfg['limites']
    grupos = _grupos(unds)
    yc = _centra(np.concatenate([u['y'] for u in unds]), grupos)       # so para a superficie de partida
    lam_g, t1s, t2s, sse, coef = _superficie(unds, cfg, grupos, yc)
    celulas = _escolhe(sse, int(cfg['n_polimentos']))
    kmax = L['k1'][1]
    arranques = []
    for a, i, j in celulas:
        # K relativo de partida = K aditivo (em % do nivel) da celula / 100 (P0 ~ 100 %)
        k1c, k2c = (min(max(v / 100.0, 1e-7), kmax) for v in coef[a, i, j])
        arranques.append([k1c, k2c, float(t1s[i]), float(t2s[j]), float(lam_g[a])])
    arranques.append([3e-5, 1e-4, 35.0, 7.0, 1.0])      # ordem de grandeza tipica (K ~ 0,01-0,03 W por carga a P0 ~ 300 W)
    fits = []
    for x0 in arranques:
        try:
            x, s_, r = _polir(x0, unds, cfg, grupos, yc)
            fits.append((s_, x, r))
        except Exception:
            continue
    if not fits:
        return None
    fits.sort(key=lambda t: t[0])
    return fits[0][1], fits[0][0], fits[0][2], fits, arranques


def _sse_unidades(unds, x, lag):
    out = {}
    for u, f in zip(unds, _f_x(unds, x, lag)):
        p0 = _p0_fecho(u['y'], f)
        e = p0 * (1.0 + f) - u['y']
        out[u['id']] = float(np.dot(e, e))
    return out


def _estat_homogeneidade(unds, x, cfg, lam):
    """Compara o global (dinamica partilhada) com ajustes por unidade (5 parametros proprios) com a
    mesma carga efetiva, nas unidades com observacoes suficientes. Devolve dict ou None."""
    lag = int(cfg['lag_dias'])
    ucfg = {**{k: v for k, v in ir.CONFIG_PADRAO.items()}, 'lag_dias': lag, 'perfil': False,
            'min_obs': int(cfg['min_obs_homogeneidade'])}
    sse_pool = _sse_unidades(unds, x, lag)
    soma_u, soma_pool, n_h, k_h, detalhe = 0.0, 0.0, 0, 0, []
    for u in unds:
        if len(u['idx']) < cfg['min_obs_homogeneidade']:
            continue
        try:
            ru = ir.ajustar(u['propria'] + lam * u['outras'], list(u['idx']), list(u['y']), ucfg, None,
                            list(u['datas']))
        except Exception:
            continue
        if ru['estado'] not in (ir.EST_IDENTIFICAVEL, ir.EST_NAO_IDENTIFICAVEL):
            continue
        s_u = ru['metricas']['sse']
        soma_u += s_u
        soma_pool += sse_pool[u['id']]
        n_h += len(u['idx'])
        k_h += 1
        detalhe.append({'id': u['id'], 'sse_proprio': s_u, 'sse_global': sse_pool[u['id']],
                        'k1': ru['parametros']['k1'], 'k2': ru['parametros']['k2'],
                        't1': ru['parametros']['t1'], 't2': ru['parametros']['t2']})
    if k_h < 2 or n_h - 5 * k_h <= 0:
        return None
    return {'sse_global': soma_pool, 'sse_por_unidade': soma_u, 'n_unidades': k_h, 'n_obs': n_h,
            'gl_extra': 4 * (k_h - 1), 'gl_residual': n_h - 5 * k_h, 'unidades': detalhe}


def _semente(unds):
    h = ir.assinatura([(u['id'], [str(d) for d in u['datas']], [round(float(v), 6) for v in u['y_w']])
                       for u in unds])
    return int(h[:8], 16)


def _bootstrap_homogeneidade(unds, x, cfg, estat_obs):
    """P-valor por bootstrap parametrico sob H0 (dinamica partilhada). O F classico e
    ANTI-CONSERVADOR aqui: os ajustes por unidade sao nao lineares e multi-inicio (escolhem o melhor
    de varios minimos), logo a melhora sob H0 excede a do F. Simula-se de novo sob o ajuste global
    (ruido gaussiano com o desvio de cada unidade), repete-se o MESMO procedimento (global +
    por unidade) e compara-se a estatistica S = (SSE_global - SSE_unidades) / SSE_unidades.
    Para cedo quando a hipotese ja nao pode ser rejeitada ao nivel alfa."""
    lag = int(cfg['lag_dias'])
    B = int(cfg['bootstrap_B'])
    alfa = cfg['alfa']
    rng = np.random.default_rng(_semente(unds))
    s_obs = (estat_obs['sse_global'] - estat_obs['sse_por_unidade']) / estat_obs['sse_por_unidade']
    medias, sig = [], []
    for u, f in zip(unds, _f_x(unds, x, lag)):
        p0 = _p0_fecho(u['y'], f)
        medias.append(p0 * (1.0 + f))
        e = u['y'] - p0 * (1.0 + f)
        sig.append(float(math.sqrt(np.dot(e, e) / max(1, len(e) - 1))))
    exced, feitas, parou_cedo = 0, 0, False
    for b in range(B):
        ub = []
        for u, m_, sg in zip(unds, medias, sig):
            v = dict(u)
            v['y'] = m_ + rng.normal(0.0, sg, len(m_))
            ub.append(v)
        try:
            r = _ajusta_pool(ub, cfg)
            if r is None:
                continue
            xb = r[0]
            eb = _estat_homogeneidade(ub, xb, cfg, float(xb[4]))
        except Exception:
            continue
        if eb is None:
            continue
        feitas += 1
        sb = (eb['sse_global'] - eb['sse_por_unidade']) / eb['sse_por_unidade']
        if sb >= s_obs:
            exced += 1
        if (exced + 1) / (B + 1) > alfa:            # ja nao ha rejeicao possivel: para
            parou_cedo = True
            break
    p = (exced + 1) / (feitas + 1) if feitas else None
    # Conclusivo = a nao-rejeicao e genuina (parou porque ja nao podia rejeitar) ou, se percorreu
    # tudo, as replicas feitas chegam para o p poder ficar abaixo de alfa. Com replicas falhadas a
    # menos, 'p >= alfa' e so falta de poder, nao evidencia de homogeneidade. Se parou cedo, 'p' e
    # apenas um limite inferior (> alfa), nao um p-valor exato.
    conclusivo = bool(feitas and (parou_cedo or 1.0 / (feitas + 1) < alfa))
    return {'p': p, 'B_pedido': B, 'B_feito': feitas, 'excedencias': exced, 'estatistica': s_obs,
            'parou_cedo': parou_cedo, 'conclusivo': conclusivo,
            'metodo': 'bootstrap_parametrico_sob_H0'}


def ajustar_global(unidades, carga_mod, config=None):
    """Ajusta o modelo global a um grupo (uma duracao).

    unidades: [{'id','modalidade','datas':[ISO],'y':[watts]}]; carga_mod: {modalidade: {data: carga}}
    (TODAS as modalidades ciclicas, mesmo as sem unidade: contam como 'outras').
    """
    cfg = _cfg(config)
    L = cfg['limites']
    lag = int(cfg['lag_dias'])
    excluidas = [u['id'] for u in unidades if len(u['datas']) < cfg['min_obs_unidade']]
    unidades = [u for u in unidades if len(u['datas']) >= cfg['min_obs_unidade']]
    base = {'versao_global': VERSAO_GLOBAL, 'tipo': 'ajuste_historico_global_dentro_da_amostra',
            'unidades_excluidas': excluidas, 'config': cfg, 'validade_preditiva': 'nao_avaliada'}
    n = sum(len(u['datas']) for u in unidades)
    if len(unidades) < 2:
        return {**base, 'estado': EST_INSUFICIENTE, 'motivo': 'menos_de_2_unidades_com_dados',
                'parametros': None, 'n_obs': n}
    datas_todas = sorted({d for u in unidades for d in u['datas']})
    dias = (date.fromisoformat(datas_todas[-1]) - date.fromisoformat(datas_todas[0])).days
    n_min = int(cfg['min_obs_por_parametro']) * (5 + len(unidades))
    motivo = None
    if n < n_min:
        motivo = f'menos_de_{n_min}_observacoes_no_total'
    elif len(datas_todas) < cfg['min_datas_distintas']:
        motivo = f"menos_de_{cfg['min_datas_distintas']}_datas_distintas"
    elif dias < cfg['min_dias_cobertura']:
        motivo = f"cobertura_inferior_a_{cfg['min_dias_cobertura']}_dias"
    elif all(np.ptp(np.asarray(u['y'], float)) == 0 for u in unidades):
        motivo = 'alvo_constante'
    if motivo:
        return {**base, 'estado': EST_INSUFICIENTE, 'motivo': motivo, 'parametros': None, 'n_obs': n,
                'n_unidades': len(unidades), 'data_ini': datas_todas[0], 'data_fim': datas_todas[-1]}

    unds, d0, d1 = _prepara(unidades, carga_mod, cfg)
    if not any(np.any(u['propria'] > 0) or np.any(u['outras'] > 0) for u in unds):
        return {**base, 'estado': EST_INSUFICIENTE, 'motivo': 'sem_carga', 'parametros': None, 'n_obs': n}
    grupos = _grupos(unds)
    yc = _centra(np.concatenate([u['y'] for u in unds]), grupos)
    core = _ajusta_pool(unds, cfg)
    if core is None:
        return {**base, 'estado': EST_SEM_CONVERGENCIA, 'parametros': None, 'n_obs': n}
    x, s_liv, r, fits, arranques = core
    k1, k2, t1, t2, lam = (float(v) for v in x)

    n_par_livre = 5 + len(unds)                          # 4 dinamicos + lambda + P0_u
    gl = n - n_par_livre
    f_u = _f_x(unds, x, lag)
    por_unidade, sse_u = [], {}
    for u, f in zip(unds, f_u):
        p0_pct = _p0_fecho(u['y'], f)
        e = p0_pct * (1.0 + f) - u['y']
        p0_w = p0_pct * u['escala'] / 100.0
        sse_u[u['id']] = float(np.dot(e, e))
        por_unidade.append({
            'id': u['id'], 'modalidade': u['modalidade'], 'n_obs': len(u['idx']), 'escala_w': u['escala'],
            'p0_w': p0_w, 'k1_w_por_carga': k1 * p0_w, 'k2_w_por_carga': k2 * p0_w,
            'sse_pct': sse_u[u['id']], 'rmse_pct': float(math.sqrt(sse_u[u['id']] / len(u['idx']))),
            'rmse_w': float(math.sqrt(np.mean((e * u['escala'] / 100.0) ** 2)))})

    avisos = []
    for nome, v in (('k1', k1), ('k2', k2), ('t1', t1), ('t2', t2)):
        lo_, hi_ = L[nome]
        marg = cfg['margem_limite'] * (hi_ - lo_)
        if v <= lo_ + marg or v >= hi_ - marg:
            avisos.append(f'parametro_no_limite:{nome}')
    J = np.asarray(r.jac, float)
    esc = np.array([1e-4, 1e-4, 10.0, 3.0, 0.3])
    try:
        cond = float(np.linalg.cond(J * esc))
    except Exception:
        cond = float('inf')
    if not math.isfinite(cond) or cond > cfg['cond_max']:
        avisos.append('jacobiano_mal_condicionado')
    for s2, x2, _ in fits[1:]:
        if s2 <= s_liv * (1 + cfg['tol_sse_alternativas']):
            if (abs(x2[2] - t1) / t1 > cfg['tol_param_rel'] or abs(x2[3] - t2) / t2 > cfg['tol_param_rel']
                    or abs(x2[4] - lam) > cfg['tol_lambda']):
                avisos.append('solucoes_equivalentes_divergentes')
                break

    # transferencia: lambda = 0 (independentes) e lambda = 1 (carga global somada) vs livre
    def melhor_fixo(valor):
        res = []
        for x0 in arranques[: int(cfg['n_polimentos'])]:
            try:
                _, sf, _ = _polir(x0, unds, cfg, grupos, yc, lam_fixo=valor)
                res.append(sf)
            except Exception:
                continue
        return min(res) if res else None

    sse0, sse1 = melhor_fixo(0.0), melhor_fixo(1.0)
    p0_f = None if sse0 is None else _p_f(sse0, s_liv, 1, gl)
    p1_f = None if sse1 is None else _p_f(sse1, s_liv, 1, gl)
    rej0 = p0_f is not None and p0_f < cfg['alfa']
    rej1 = p1_f is not None and p1_f < cfg['alfa']
    if p0_f is None or p1_f is None:
        leit = 'transferencia nao testavel'
    elif rej0 and rej1:
        leit = 'transferencia parcial: nem 0 (independentes) nem 1 (carga global somada) descrevem os dados'
    elif rej0:
        leit = 'ha transferencia entre modalidades: lambda = 0 (modelos independentes) e rejeitado; lambda = 1 nao'
    elif rej1:
        leit = 'sem transferencia clara: lambda = 1 (carga global somada) e rejeitado; lambda = 0 nao'
    else:
        leit = 'os dados nao distinguem transferencia nula de total (nem lambda = 0 nem lambda = 1 e rejeitado)'
        avisos.append('transferencia_nao_identificada')
    transf = {'lambda': lam, 'sse_livre': s_liv, 'sse_lambda_0': sse0, 'sse_lambda_1': sse1,
              'p_lambda_0': p0_f, 'p_lambda_1': p1_f, 'metodo_p': 'F_indicativo', 'leitura': leit}

    # homogeneidade da dinamica
    est = _estat_homogeneidade(unds, x, cfg, lam)
    homog = {'testavel': False}
    if est is None:
        avisos.append('homogeneidade_nao_testavel')
    else:
        p_f = _p_f(est['sse_global'], est['sse_por_unidade'], est['gl_extra'], est['gl_residual'])
        testadas = {d['id'] for d in est['unidades']}
        nao_testadas = [u['id'] for u in unds if u['id'] not in testadas]
        homog = {'testavel': p_f is not None, **{k: v for k, v in est.items() if k != 'unidades'},
                 'unidades': est['unidades'], 'unidades_nao_testadas': nao_testadas,
                 'p_F_indicativo': p_f, 'p': p_f, 'metodo_p': 'F_indicativo', 'conclusiva': True}
        if nao_testadas:
            # a dinamica partilhada e ASSUMIDA para estas unidades (poucas observacoes ou ajuste
            # proprio sem convergir): nao se pode dizer que a homogeneidade foi verificada
            avisos.append('homogeneidade_parcial')
        if p_f is not None and p_f < max(cfg['alfa'], cfg['triagem_p']) and cfg['bootstrap_B'] > 0:
            bs = _bootstrap_homogeneidade(unds, x, cfg, est)
            homog['bootstrap'] = bs
            if bs['conclusivo']:
                homog.update({'p': bs['p'], 'metodo_p': bs['metodo']})
            else:
                homog['conclusiva'] = False
                avisos.append('bootstrap_inconclusivo')
        homog['rejeitada'] = bool(homog['conclusiva'] and homog['p'] is not None and homog['p'] < cfg['alfa'])

    if r.status <= 0 and not r.success:
        estado = EST_SEM_CONVERGENCIA
    elif homog.get('rejeitada'):
        estado = EST_HETEROGENEO
        avisos.append('dinamica_nao_partilhada')
    elif avisos:
        estado = EST_NAO_IDENTIFICAVEL
    else:
        estado = EST_IDENTIFICAVEL
    ssr = float(sum(np.dot(u['y'] - u['y'].mean(), u['y'] - u['y'].mean()) for u in unds))
    return {**base, 'estado': estado, 'motivo': None,
            'parametros': {'k1_rel': k1, 'k2_rel': k2, 't1': t1, 't2': t2, 'lambda': lam},
            'limites': {k: list(v) for k, v in L.items()},
            'metricas': {'sse_pct': s_liv, 'rmse_pct': float(math.sqrt(s_liv / n)),
                         'r2_dentro_da_unidade': (1.0 - s_liv / ssr) if ssr > 0 else None, 'gl_residual': gl,
                         'n_parametros': n_par_livre, 'tipo': 'ajuste_historico_dentro_da_amostra'},
            'por_unidade': por_unidade, 'transferencia': transf, 'homogeneidade': homog,
            'identificabilidade': {'avisos': sorted(set(avisos)), 'condicao_jacobiano': cond},
            'convergencia': {'convergiu': bool(r.success), 'estado_otimizador': int(r.status),
                             'n_inicios': len(fits)},
            'n_obs': n, 'n_unidades': len(unds), 'data_ini': datas_todas[0], 'data_fim': datas_todas[-1],
            'dias_cobertura': dias, 'serie_ini': d0}
