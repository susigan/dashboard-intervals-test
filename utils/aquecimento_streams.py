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


def _tol_segura(alvos, tol):
    """Impede que as janelas de dois degraus vizinhos se sobreponham.

    Na Bike os alvos estao a 20 W de distancia: com +-12 W a janela do 80
    tocava na do 100 e um degrau podia ser atribuido ao alvo errado.
    """
    if len(alvos) < 2:
        return tol
    menor_gap = min(b - a for a, b in zip(alvos, alvos[1:]))
    return min(tol, max(4, (menor_gap - 2) // 2))


def detectar_escada(watts, alvos, tol, min_blocos):
    """Procura os degraus no stream de watts, a comecar no inicio.

    Devolve [(i0, i1, alvo), ...] ou None. Cada degrau tem de manter-se
    dentro de +-tol durante pelo menos MIN_BLOCO_S segundos.
    """
    if not watts:
        return None
    tol = _tol_segura(alvos, tol)
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
