"""
AQUECIMENTO_STREAMS.PY — Deteccao do aquecimento a partir dos streams

Porque nao usar a fisiologia_intervalos: essa tabela so tem ~57 atividades
e nao existe, neste projecto, codigo que insira linhas novas nela. Recria-la
exigiria replicar a cinetica (lag_*, rec_*, plateau, baseline) e as linhas
novas nao seriam comparaveis com as antigas.

Os streams, esses, estao todos no Postgres (db.get_streams). E como sabemos
exactamente que escada procurar e que ela comeca no inicio da atividade,
detectar directamente no stream de watts e' mais fiavel do que uma deteccao
generica de intervalos -- e nao mexe em nada do que ja funciona.

Metricas em falta nao invalidam nada: se o SmO2 nao existir, ou o sensor cair
a meio, os blocos afectados ficam a NULL e a sessao conta na mesma.
"""

import statistics

# Nomes possiveis de cada stream (a API varia consoante o sensor).
STREAM_KEYS = {
    "watts": ["watts", "power"],
    "hr":    ["heartrate", "hr"],
    "smo2":  ["smo2", "SmO2", "smo2_2"],
    "resp":  ["respiration", "resp", "breathing_rate"],
    "dfa1":  ["dfa_a1", "dfa1", "DFA_a1"],
}

SUAVIZA_S = 15      # media movel: os watts por stroke oscilam muito
MIN_BLOCO_S = 210   # um degrau tem de durar pelo menos isto (5 min com folga)
MAX_BLOCO_S = 420
MAX_RAMPA_S = 90    # transicao tolerada entre degraus
REC_S = (20, 160)   # o "1" do 5-1-5: recuperacao entre degraus

# A recuperacao NAO e' paragem. Na Bike costuma ser rodada a ~60 W, e no
# Row/Ski pode ficar com potencia residual. Por isso nao se exige que caia a
# zero -- so que saia da janela do degrau. Os laps tambem sao ignorados de
# proposito: o sinal manda, mesmo que um "rest" esteja marcado como WORK.


def _serie(streams, nome):
    """Primeira variante encontrada de um stream, como lista de floats."""
    for k in STREAM_KEYS.get(nome, []):
        v = streams.get(k)
        if isinstance(v, list) and v:
            return [float(x) if isinstance(x, (int, float)) else None for x in v]
    return None


def _suavizar(serie, janela=SUAVIZA_S):
    if not serie:
        return serie
    out, buf = [], []
    for v in serie:
        buf.append(v if v is not None else 0.0)
        if len(buf) > janela:
            buf.pop(0)
        out.append(sum(buf) / len(buf))
    return out


def _resumo(serie, i0, i1):
    """(avg, min, max) de uma janela, ignorando buracos."""
    if not serie:
        return None, None, None
    vals = [v for v in serie[i0:i1] if v is not None]
    if not vals:
        return None, None, None
    return (round(statistics.fmean(vals), 3), round(min(vals), 3),
            round(max(vals), 3))


def _tol_segura(alvos, tol, cap_frac=0.5):
    """Limita a tolerancia em funcao da distancia entre alvos.

    Na Bike os alvos estao a 20 W: com +-12 W a janela do 80 tocava na do
    100. Com cap_frac=0.5 as janelas nunca se tocam. Em modo assistido
    sobe-se o cap: pode haver sobreposicao nominal, mas a escada e' lida
    por ordem crescente, por isso cada degrau so pode vir depois do anterior.
    """
    if len(alvos) < 2:
        return tol
    menor_gap = min(b - a for a, b in zip(alvos, alvos[1:]))
    return min(tol, max(4, int(menor_gap * cap_frac)))


def detectar_escada(watts, alvos, tol, min_blocos, cap_frac=0.5):
    """Procura os degraus no stream de watts, a comecar no inicio.

    Devolve [(i0, i1, alvo), ...] ou None. Cada degrau tem de manter-se
    dentro de +-tol durante pelo menos MIN_BLOCO_S segundos.
    """
    if not watts:
        return None
    tol = _tol_segura(alvos, tol, cap_frac)
    suave = _suavizar(watts)
    n = len(suave)
    i = 0
    achados = []
    fim_anterior = None

    for alvo in alvos:
        # saltar a rampa/transicao ate entrar no alvo
        inicio = None
        limite = min(n, i + MAX_RAMPA_S + MAX_BLOCO_S)
        while i < limite:
            if abs(suave[i] - alvo) <= tol:
                inicio = i
                break
            i += 1
        if inicio is None:
            break

        # quanto tempo se aguenta dentro do alvo (com folga para picos curtos)
        fim, fora = inicio, 0
        while fim < n and fora < 10:
            if abs(suave[fim] - alvo) <= tol:
                fora = 0
            else:
                fora += 1
            fim += 1
        fim -= fora

        dur = fim - inicio
        if dur < MIN_BLOCO_S:
            break
        if dur > MAX_BLOCO_S:
            fim = inicio + MAX_BLOCO_S

        # o "1" do 5-1-5: a pausa entre degraus tem de ser curta
        if fim_anterior is not None:
            gap = inicio - fim_anterior
            if not (REC_S[0] <= gap <= REC_S[1]):
                break

        achados.append((inicio, fim, alvo))
        fim_anterior = fim
        i = fim

    if len(achados) < min_blocos:
        return None
    return achados


def analisar_streams(streams, modalidade, protocolos):
    """Analisa uma atividade a partir dos streams ja carregados."""
    proto = protocolos.get(modalidade)
    if not proto:
        return {"detectado": False, "motivo": f"sem protocolo para {modalidade}"}

    watts = _serie(streams, "watts")
    if not watts:
        return {"detectado": False, "motivo": "sem stream de watts"}

    achados = detectar_escada(watts, proto["watts"], proto["tol"],
                              proto.get("min_blocos", len(proto["watts"])))
    if not achados:
        suave = _suavizar(watts)
        amostra = [round(suave[k]) for k in range(0, min(len(suave), 1200), 120)]
        return {"detectado": False, "motivo": "escada nao encontrada no stream",
                "watts_a_cada_2min": amostra}

    series = {m: _serie(streams, m) for m in ("hr", "smo2", "resp", "dfa1")}

    blocos = []
    for num, (i0, i1, alvo) in enumerate(achados, start=1):
        wa, wmin, wmax = _resumo(watts, i0, i1)
        b = {"bloco_num": num, "watts_alvo": alvo, "watts_real": wa,
             "interval_num": num, "tempo_seg": i1 - i0}
        for m, serie in series.items():
            avg, mn, mx = _resumo(serie, i0, i1)
            b[f"{m}_avg"], b[f"{m}_min"], b[f"{m}_max"] = avg, mn, mx
        blocos.append(b)

    return {"detectado": True, "modalidade": modalidade,
            "padrao": "-".join(str(a) for a in proto["watts"][:len(blocos)]),
            "n_blocos": len(blocos), "blocos": blocos,
            "tempo_aquecimento_seg": sum(b["tempo_seg"] for b in blocos)}


# ── modo assistido: datas que o utilizador garante terem aquecimento ──────

NIVEIS = [
    # (etiqueta, mult. da tolerancia, min_bloco_s, folga do gap, cap_frac)
    ("normal",     1.0, MIN_BLOCO_S, 1.0, 0.50),
    ("tolerante",  1.6, 180,         1.6, 0.65),
    ("permissivo", 2.4, 120,         2.5, 0.80),
]


def analisar_assistido(streams, modalidade, protocolos):
    """Para as sessoes que o utilizador CONFIRMOU terem aquecimento.

    Tenta primeiro os criterios normais. Se falhar, vai relaxando a
    tolerancia de watts e a duracao minima do degrau, e devolve o nivel que
    resultou -- para se saber quao à-vontade foi a aceitacao.

    Nao inventa dados: se nem no nivel mais permissivo a escada aparecer, e'
    rejeitada com os watts observados, para se poder ver o que la esta.
    """
    global MIN_BLOCO_S, REC_S
    proto = protocolos.get(modalidade)
    if not proto:
        return {"detectado": False, "motivo": f"sem protocolo para {modalidade}"}

    watts = _serie(streams, "watts")
    if not watts:
        return {"detectado": False, "motivo": "sem stream de watts"}

    min_orig, rec_orig = MIN_BLOCO_S, REC_S
    try:
        for etiqueta, mult, min_bloco, folga, cap in NIVEIS:
            MIN_BLOCO_S = min_bloco
            REC_S = (max(5, int(rec_orig[0] / folga)), int(rec_orig[1] * folga))
            achados = detectar_escada(
                watts, proto["watts"], proto["tol"] * mult,
                proto.get("min_blocos", len(proto["watts"])), cap_frac=cap)
            if achados:
                r = _montar(streams, watts, achados, modalidade, proto)
                r["nivel_deteccao"] = etiqueta
                r["confianca"] = {"normal": "alta", "tolerante": "media",
                                  "permissivo": "baixa"}[etiqueta]
                return r
    finally:
        MIN_BLOCO_S, REC_S = min_orig, rec_orig

    suave = _suavizar(watts)
    return {"detectado": False,
            "motivo": "escada nao encontrada nem em modo permissivo",
            "duracao_s": len(watts),
            "watts_a_cada_2min": [round(suave[k])
                                  for k in range(0, min(len(suave), 1800), 120)]}


def _montar(streams, watts, achados, modalidade, proto):
    """Blocos + metricas. Cada metrica e' usada se existir; as que faltarem
    ficam a NULL sem invalidar o bloco nem a sessao."""
    series = {m: _serie(streams, m) for m in ("hr", "smo2", "resp", "dfa1")}
    blocos, metricas_usadas = [], set()
    for num, (i0, i1, alvo) in enumerate(achados, start=1):
        wa, _wmin, _wmax = _resumo(watts, i0, i1)
        b = {"bloco_num": num, "watts_alvo": alvo, "watts_real": wa,
             "interval_num": num, "tempo_seg": i1 - i0}
        for m, serie in series.items():
            avg, mn, mx = _resumo(serie, i0, i1)
            b[f"{m}_avg"], b[f"{m}_min"], b[f"{m}_max"] = avg, mn, mx
            if avg is not None:
                metricas_usadas.add(m)
        blocos.append(b)
    return {"detectado": True, "modalidade": modalidade,
            "padrao": "-".join(str(a) for a in proto["watts"][:len(blocos)]),
            "n_blocos": len(blocos), "blocos": blocos,
            "metricas_disponiveis": sorted(metricas_usadas),
            "tempo_aquecimento_seg": sum(b["tempo_seg"] for b in blocos)}
