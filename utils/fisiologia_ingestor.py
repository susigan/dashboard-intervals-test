"""
FISIOLOGIA_INGESTOR.PY — Cria linhas em fisiologia_intervalos a partir dos streams

Porque existe: neste repositorio nao ha (nem nunca houve, em nenhum commit)
codigo que faca INSERT em fisiologia_intervalos. O worker so faz UPDATE das
linhas que la estao, por isso o Perfil por Watts ficou parado nas ~57
atividades que alguem inseriu por fora.

O que faz: le os streams do Postgres, deteta os blocos de esforco, calcula as
metricas e insere. Idempotente por atividade (apaga e reescreve).

Sobre a comparabilidade com as linhas antigas: as colunas *_60s sao calculadas
como "os ultimos 60 s do bloco de trabalho", que e' a leitura natural do nome
e coincide com o valor estabilizado. Se as linhas antigas usarem outra
convencao, os valores nao serao identicos -- por isso cada linha inserida fica
marcada com origem='ingestor' para se poder distinguir.
"""

import statistics

STREAM_KEYS = {
    "watts": ["watts", "power"],
    "hr":    ["heartrate", "hr"],
    "smo2":  ["smo2", "SmO2", "smo2_2"],
    "thb":   ["thb", "tHb"],
    "resp":  ["respiration", "resp", "breathing_rate"],
    "dfa1":  ["dfa_a1", "dfa1", "DFA_a1"],
}

SUAVIZA_S = 10
MIN_WORK_S = 60          # blocos mais curtos nao servem ao perfil por watts
MAX_WORK_S = 1800
MIN_REC_S = 10
JANELA_FINAL_S = 60      # as colunas *_60s
JANELA_BASELINE_S = 20   # estado imediatamente antes da transicao
ENTRADA_REC_S = 30       # o "extremo" pode cair ja no descanso


def _serie(streams, nome):
    for k in STREAM_KEYS.get(nome, []):
        v = streams.get(k)
        if isinstance(v, list) and v:
            return [float(x) if isinstance(x, (int, float)) else None for x in v]
    return None


def _suavizar(serie, janela):
    if not serie or janela < 2:
        return serie
    out, buf = [], []
    for v in serie:
        buf.append(v if v is not None else 0.0)
        if len(buf) > janela:
            buf.pop(0)
        out.append(sum(buf) / len(buf))
    return out


def _stats(serie, i0, i1):
    """(avg, min, max) ignorando buracos. (None, None, None) se vazio."""
    if not serie:
        return None, None, None
    vals = [v for v in serie[max(0, i0):max(0, i1)] if v is not None]
    if not vals:
        return None, None, None
    return statistics.fmean(vals), min(vals), max(vals)


def passo_temporal(watts, duracao_s):
    """Segundos por amostra. Os streams da Intervals.icu nao sao 1 Hz."""
    if not watts or not duracao_s:
        return 1.0
    dt = float(duracao_s) / len(watts)
    return dt if 0.2 <= dt <= 30 else 1.0


def detectar_blocos(watts, dt, limiar_frac=0.55):
    """Blocos de esforco: [(i0_work, i1_work, i1_rec), ...] em amostras.

    O limiar e' relativo ao proprio treino (fraccao do percentil 90 dos
    watts), para funcionar tanto num aquecimento a 80 W como numa sessao a
    250 W, sem numeros magicos por modalidade.
    """
    if not watts:
        return []
    suave = _suavizar(watts, max(2, int(SUAVIZA_S / dt)))
    validos = sorted(v for v in suave if v is not None and v > 0)
    if len(validos) < 10:
        return []
    p90 = validos[int(len(validos) * 0.9)]
    limiar = p90 * limiar_frac

    min_w = max(3, int(MIN_WORK_S / dt))
    max_w = int(MAX_WORK_S / dt)
    min_r = max(2, int(MIN_REC_S / dt))

    blocos, i, n = [], 0, len(suave)
    while i < n:
        while i < n and (suave[i] is None or suave[i] < limiar):
            i += 1
        if i >= n:
            break
        i0 = i
        while i < n and suave[i] is not None and suave[i] >= limiar:
            i += 1
        i1 = i
        if not (min_w <= i1 - i0 <= max_w):
            continue
        j = i1
        while j < n and (suave[j] is None or suave[j] < limiar):
            j += 1
        rec_fim = j if (j - i1) >= min_r else None
        blocos.append((i0, i1, rec_fim))
    return blocos


def extrair_intervalos(streams, duracao_s=None):
    """Uma entrada por bloco, pronta a inserir em fisiologia_intervalos."""
    watts = _serie(streams, "watts")
    if not watts:
        return []
    dt = passo_temporal(watts, duracao_s)
    blocos = detectar_blocos(watts, dt)
    if not blocos:
        return []

    series = {m: _serie(streams, m) for m in ("hr", "smo2", "thb", "resp", "dfa1")}
    n60 = max(2, int(JANELA_FINAL_S / dt))
    nbase = max(2, int(JANELA_BASELINE_S / dt))
    nrec = max(1, int(ENTRADA_REC_S / dt))

    saida = []
    for num, (i0, i1, rec_fim) in enumerate(blocos, start=1):
        wa, wmin, wmax = _stats(watts, i0, i1)
        linha = {
            "interval_num": num,
            "watts_medio": round(wa, 1) if wa is not None else None,
            "watts_min": round(wmin, 1) if wmin is not None else None,
            "watts_max": round(wmax, 1) if wmax is not None else None,
            "dur_work_s": int((i1 - i0) * dt),
            "dur_rec_s": int((rec_fim - i1) * dt) if rec_fim else None,
        }

        for m, serie in series.items():
            if not serie:
                continue
            # *_60s: ultimos 60 s do esforco (estado ja estabilizado)
            avg, mn, mx = _stats(serie, max(i0, i1 - n60), i1)
            if avg is not None:
                linha[f"{m}_avg_60s"] = round(avg, 3)
                linha[f"{m}_min_60s"] = round(mn, 3)
                linha[f"{m}_max_60s"] = round(mx, 3)

            # media de todo o bloco
            avg_w, _, _ = _stats(serie, i0, i1)
            if avg_w is not None:
                linha[f"{m}_medio_work"] = round(avg_w, 3)

            # plateau: mesma janela final, medida do fim para tras
            if avg is not None:
                linha[f"{m}_plateau_work"] = round(avg, 3)

            # baseline: estado imediatamente ANTES da transicao
            base, _, _ = _stats(serie, max(0, i0 - nbase), i0)
            if base is not None:
                linha[f"{m}_baseline"] = round(base, 3)

            # extremo: valor mais afastado da baseline numa janela que entra
            # no descanso (capta picos com inercia, como resp. e DFA1)
            fim_janela = (rec_fim or i1) if rec_fim else i1
            fim_janela = min(len(serie), max(i1, min(i1 + nrec, fim_janela)))
            a2, mn2, mx2 = _stats(serie, i0, fim_janela)
            if a2 is not None and base is not None:
                extremo = mx2 if abs(mx2 - base) >= abs(mn2 - base) else mn2
                linha[f"{m}_extremo"] = round(extremo, 3)
                alvo = serie[i0:fim_janela]
                try:
                    pos = next(k for k, v in enumerate(alvo)
                               if v is not None and abs(v - extremo) < 1e-9)
                    linha[f"{m}_t_extremo"] = round(pos * dt, 1)
                except StopIteration:
                    pass
            elif a2 is not None:
                linha[f"{m}_extremo"] = round(mx2, 3)

            if m == "dfa1":
                linha["tem_dfa1"] = 1
                linha["tem_dfa1_stream"] = 1

        saida.append(linha)
    return saida


# ── escrita ───────────────────────────────────────────────────────────────

def _colunas(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(fisiologia_intervalos)")}


def gravar(conn, activity_id, data, modalidade, linhas):
    """Substitui as linhas dessa atividade. Ignora colunas inexistentes."""
    if not linhas:
        return 0
    existentes = _colunas(conn)
    conn.execute("DELETE FROM fisiologia_intervalos WHERE activity_id = ?",
                 (str(activity_id),))
    gravadas = 0
    for l in linhas:
        campos = {"activity_id": str(activity_id), "data": data,
                  "modalidade": modalidade, "valido": 1}
        campos.update({k: v for k, v in l.items() if k in existentes})
        if "origem" in existentes:
            campos["origem"] = "ingestor"
        cols = [c for c in campos if c in existentes or c in
                ("activity_id", "data", "modalidade", "valido")]
        marcas = ",".join("?" * len(cols))
        conn.execute(
            f"INSERT INTO fisiologia_intervalos ({','.join(cols)}) VALUES ({marcas})",
            tuple(campos[c] for c in cols))
        gravadas += 1
    conn.commit()
    return gravadas
