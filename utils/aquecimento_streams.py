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

# Sobe sempre que a logica de deteccao muda. As sessoes rejeitadas guardam a
# versao com que foram avaliadas: quando esta sobe, sao reanalisadas
# automaticamente, em vez de ficarem presas a um veredicto de um algoritmo
# que entretanto foi corrigido.
VERSAO_DETECTOR = 5

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
# O "1" do 5-1-5. So se valida o MAXIMO: a suavizacao borra a transicao e
# absorve parte da recuperacao no fim do bloco anterior, por isso o gap
# medido pode colapsar para zero mesmo havendo pausa real. Exigir um minimo
# rejeitava sessoes validas; o maximo e' que impede juntar degraus separados
# por uma pausa longa (isso ja e' outro treino).
REC_S = (0, 200)

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


def _com_escala(dt, fn):
    """Corre fn() com os limiares convertidos de segundos para AMOSTRAS.

    Todos os limites do modulo estao escritos em segundos (mais legiveis);
    aqui sao divididos pelo passo temporal antes da deteccao.
    """
    global SUAVIZA_S, MIN_BLOCO_S, MAX_BLOCO_S, MAX_RAMPA_S, REC_S
    orig = (SUAVIZA_S, MIN_BLOCO_S, MAX_BLOCO_S, MAX_RAMPA_S, REC_S)
    try:
        SUAVIZA_S = max(3, int(SUAVIZA_S / dt))
        MIN_BLOCO_S = max(8, int(MIN_BLOCO_S / dt))
        MAX_BLOCO_S = max(MIN_BLOCO_S + 4, int(MAX_BLOCO_S / dt))
        MAX_RAMPA_S = max(4, int(MAX_RAMPA_S / dt))
        REC_S = (max(2, int(REC_S[0] / dt)), max(4, int(REC_S[1] / dt)))
        return fn()
    finally:
        SUAVIZA_S, MIN_BLOCO_S, MAX_BLOCO_S, MAX_RAMPA_S, REC_S = orig


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

        # a pausa entre degraus tem de ser curta (ver nota em REC_S)
        if fim_anterior is not None and (inicio - fim_anterior) > REC_S[1]:
            break

        achados.append((inicio, fim, alvo))
        fim_anterior = fim
        i = fim

    if len(achados) < min_blocos:
        return None
    return achados


def passo_temporal(streams, duracao_s=None):
    """Segundos por amostra do stream.

    Os streams da Intervals.icu NAO sao 1 Hz: consoante o dispositivo, uma
    amostra pode valer 2 s, 4 s ou mais. Assumir 1 s fazia um bloco de 5 min
    parecer ter 80 s e ser rejeitado por ser curto de mais.

    Sem a duracao da atividade nao ha como saber, e devolve-se 1.0.
    """
    watts = _serie(streams, "watts")
    if not watts or not duracao_s:
        return 1.0
    dt = float(duracao_s) / len(watts)
    return dt if 0.2 <= dt <= 30 else 1.0


def analisar_streams(streams, modalidade, protocolos, duracao_s=None):
    """Analisa uma atividade a partir dos streams ja carregados."""
    proto = protocolos.get(modalidade)
    if not proto:
        return {"detectado": False, "motivo": f"sem protocolo para {modalidade}"}

    watts = _serie(streams, "watts")
    if not watts:
        return {"detectado": False, "motivo": "sem stream de watts"}

    dt = passo_temporal(streams, duracao_s)
    achados = _com_escala(dt, lambda: detectar_escada(
        watts, proto["watts"], proto["tol"],
        proto.get("min_blocos", len(proto["watts"]))))
    if not achados:
        suave = _suavizar(watts)
        passo = max(1, int(120 / dt))
        amostra = [round(suave[k]) for k in range(0, min(len(suave), passo * 10), passo)]
        return {"detectado": False, "motivo": "escada nao encontrada no stream",
                "passo_temporal_s": round(dt, 2),
                "watts_a_cada_2min": amostra}

    r = _montar(streams, watts, achados, modalidade, proto, dt)
    r["passo_temporal_s"] = round(dt, 2)
    return r


# ── modo assistido: datas que o utilizador garante terem aquecimento ──────

NIVEIS = [
    # (etiqueta, mult. da tolerancia, min_bloco_s, folga do gap, cap_frac)
    ("normal",     1.0, MIN_BLOCO_S, 1.0, 0.50),
    ("tolerante",  1.6, 180,         1.6, 0.65),
    ("permissivo", 2.4, 120,         2.5, 0.80),
]


def analisar_assistido(streams, modalidade, protocolos, duracao_s=None):
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

    dt = passo_temporal(streams, duracao_s)
    min_orig, rec_orig = MIN_BLOCO_S, REC_S
    try:
        for etiqueta, mult, min_bloco, folga, cap in NIVEIS:
            MIN_BLOCO_S = min_bloco
            REC_S = (max(5, int(rec_orig[0] / folga)), int(rec_orig[1] * folga))
            achados = _com_escala(dt, lambda: detectar_escada(
                watts, proto["watts"], proto["tol"] * mult,
                proto.get("min_blocos", len(proto["watts"])), cap_frac=cap))
            if achados:
                r = _montar(streams, watts, achados, modalidade, proto, dt)
                r["passo_temporal_s"] = round(dt, 2)
                r["nivel_deteccao"] = etiqueta
                r["confianca"] = {"normal": "alta", "tolerante": "media",
                                  "permissivo": "baixa"}[etiqueta]
                return r
    finally:
        MIN_BLOCO_S, REC_S = min_orig, rec_orig

    suave = _suavizar(watts)
    return {"detectado": False,
            "motivo": "escada nao encontrada nem em modo permissivo",
            "passo_temporal_s": round(dt, 2),
            "duracao_s": int(len(watts) * dt),
            "watts_a_cada_2min": [round(suave[k])
                                  for k in range(0, min(len(suave), 1800), 120)]}


def _montar(streams, watts, achados, modalidade, proto, dt=1.0):
    """Blocos + metricas. Cada metrica e' usada se existir; as que faltarem
    ficam a NULL sem invalidar o bloco nem a sessao."""
    series = {m: _serie(streams, m) for m in ("hr", "smo2", "resp", "dfa1")}
    blocos, metricas_usadas = [], set()
    for num, (i0, i1, alvo) in enumerate(achados, start=1):
        wa, _wmin, _wmax = _resumo(watts, i0, i1)
        b = {"bloco_num": num, "watts_alvo": alvo, "watts_real": wa,
             "interval_num": num, "tempo_seg": int((i1 - i0) * dt)}
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


# ── engenharia inversa: descobrir a escada real de uma sessao ─────────────

def perfil_degraus(watts, min_dur=45, tol_patamar=12):
    """Segmenta o stream em patamares estaveis, SEM assumir protocolo.

    Serve para descobrir o que a sessao tem mesmo, quando a escada esperada
    nao aparece. Devolve [(inicio_s, duracao_s, watts_mediano), ...].
    """
    if not watts:
        return []
    suave = _suavizar(watts)
    segmentos, ini, buf = [], 0, []

    for i, v in enumerate(suave):
        if not buf:
            buf = [v]
            ini = i
            continue
        med = statistics.median(buf)
        if abs(v - med) <= tol_patamar:
            buf.append(v)
        else:
            if i - ini >= min_dur:
                segmentos.append((ini, i - ini, round(statistics.median(buf), 1)))
            buf, ini = [v], i

    if buf and len(suave) - ini >= min_dur:
        segmentos.append((ini, len(suave) - ini,
                          round(statistics.median(buf), 1)))
    return segmentos


def resumir_inicio(streams, minutos=30, duracao_s=None, protocolo=None):
    """Retrato do inicio da sessao: degraus reais, ja em segundos.

    Depois do ultimo degrau do aquecimento nao interessa o que vem a seguir
    (e' o treino), por isso a listagem para ai.
    """
    watts = _serie(streams, "watts")
    if not watts:
        return {"erro": "sem stream de watts"}
    dt = passo_temporal(streams, duracao_s)
    corte = watts[:int(minutos * 60 / dt)]
    suave = _com_escala(dt, lambda: _suavizar(corte))
    degraus = _com_escala(dt, lambda: perfil_degraus(
        corte, min_dur=max(6, int(45 / dt))))
    # cortar assim que o ultimo alvo do protocolo for atingido
    if protocolo and protocolo.get("watts"):
        alvos = protocolo["watts"]
        ultimo = alvos[-1]
        # tolerancia APERTADA: com a folga antiga, um degrau de 160 W caia
        # dentro de 180+-22 e a listagem parava antes do ultimo degrau real
        tol = min(protocolo.get("tol", 12), (ultimo - alvos[-2]) / 2 if len(alvos) > 1 else 12)
        vistos = 0
        for k, (a, b, wv) in enumerate(degraus):
            if b < max(6, int(150 / dt)):
                continue
            vistos += 1
            # so pode ser o fim se ja passamos por degraus suficientes
            if abs(wv - ultimo) <= tol and vistos >= len(alvos):
                degraus = degraus[:k + 1]
                break

    passo30 = max(1, int(30 / dt))
    return {
        "passo_temporal_s": round(dt, 2),
        "amostras": len(watts),
        "duracao_total_s": int(len(watts) * dt),
        "degraus": [{"inicio_s": int(a * dt), "duracao_s": int(b * dt),
                     "watts": c} for a, b, c in degraus[:12]],
        "termina_no_ultimo_degrau": bool(protocolo),
        "watts_cada_30s": [round(suave[k]) for k in range(0, len(suave), passo30)],
        "streams_presentes": sorted(streams.keys()),
    }


def diagnosticar_escada(streams, modalidade, protocolos, duracao_s=None):
    """Percorre a escada alvo a alvo e diz onde e' que parou.

    Sem isto, "escada nao encontrada" nao distingue entre um degrau com os
    watts errados, um degrau curto de mais, ou uma pausa longa a meio.
    """
    proto = protocolos.get(modalidade)
    watts = _serie(streams, "watts")
    if not proto or not watts:
        return {"erro": "sem protocolo ou sem stream de watts"}

    dt = passo_temporal(streams, duracao_s)
    alvos = proto["watts"]

    def correr():
        tol = _tol_segura(alvos, proto["tol"])
        suave = _suavizar(watts)
        n = len(suave)
        i, fim_ant, passos = 0, None, []
        for alvo in alvos:
            ini, limite = None, min(n, i + MAX_RAMPA_S + MAX_BLOCO_S)
            while i < limite:
                if abs(suave[i] - alvo) <= tol:
                    ini = i
                    break
                i += 1
            if ini is None:
                janela = suave[min(i, n - 1):min(i + 60, n)]
                passos.append({
                    "alvo_W": alvo, "estado": "nao encontrado",
                    "procurou_ate_s": int(limite * dt),
                    "watts_por_ali": round(statistics.fmean(janela), 1) if janela else None,
                    "tolerancia_W": tol})
                break
            fim, fora = ini, 0
            while fim < n and fora < 10:
                fora = 0 if abs(suave[fim] - alvo) <= tol else fora + 1
                fim += 1
            fim -= fora
            dur, gap = fim - ini, (ini - fim_ant) if fim_ant is not None else None
            p = {"alvo_W": alvo, "inicio_s": int(ini * dt),
                 "duracao_s": int(dur * dt), "watts_medidos": round(
                     statistics.fmean(suave[ini:fim]), 1) if fim > ini else None,
                 "pausa_antes_s": int(gap * dt) if gap is not None else None,
                 "tolerancia_W": tol}
            if dur < MIN_BLOCO_S:
                p["estado"] = f"curto de mais (minimo {int(MIN_BLOCO_S * dt)}s)"
                passos.append(p)
                break
            if gap is not None and gap > REC_S[1]:
                p["estado"] = f"pausa longa antes (maximo {int(REC_S[1] * dt)}s)"
                passos.append(p)
                break
            p["estado"] = "ok"
            passos.append(p)
            fim_ant, i = fim, fim
        return passos

    passos = _com_escala(dt, correr)
    ok = sum(1 for p in passos if p.get("estado") == "ok")
    return {"passo_temporal_s": round(dt, 2),
            "min_blocos_exigido": proto.get("min_blocos", len(alvos)),
            "degraus_ok": ok, "passos": passos,
            "veredicto": ("aceite" if ok >= proto.get("min_blocos", len(alvos))
                          else "rejeitada")}
