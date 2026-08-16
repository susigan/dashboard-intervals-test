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

import math
import statistics

STREAM_KEYS = {
    "watts": ["watts", "power"],
    "hr":    ["heartrate", "hr"],
    "smo2":  ["smo2", "SmO2", "smo2_2"],
    "thb":   ["thb", "tHb"],
    "resp":  ["respiration", "resp", "breathing_rate"],
    "dfa1":  ["dfa_a1", "dfa1", "DFA_a1"],
    # RRa1: alpha1 calculado a partir dos RR pelo proprio dispositivo
    "rra1":  ["RRa1", "rra1", "rr_a1"],
    # percentagem de artefactos nos RR, para validar o DFA-a1
    "artefactos": ["artifacts", "artefacts", "artifact_percent"],
}

# Limites de sanidade fisica por modalidade. Blocos fora disto sao lixo de
# medicao -- no Run os watts sao ESTIMADOS e podem disparar para milhares
# quando o GPS ou a cadencia falham, e um unico bloco desses distorce todo
# o perfil por watts.
WATTS_PLAUSIVEIS = {
    "Bike": (30, 900),
    "Row":  (30, 700),
    "Ski":  (30, 700),
    "Run":  (30, 700),
}

# Cada metrica tem a sua constante de tempo. Quando a potencia cai, a
# resposta NAO para: o DFA-a1 continua a subir durante ~1 min, a SmO2
# reoxigena em ~30-60 s, a HR desce depressa mas nao instantaneamente.
# Medir so ate ao fim do bloco corta a resposta a meio -- e' por isso que a
# janela de analise se estende para dentro da recuperacao, por metrica.
ATRASO_RESPOSTA_S = {
    "hr":   30,
    "smo2": 45,
    "resp": 30,
    "dfa1": 75,   # a mais lenta das quatro
    "thb":  30,
    "rra1": 60,
}

# Artefactos de RR: acima disto o DFA-a1 do bloco nao e' de confianca.
# Mesmos limiares do dfa_artifacts_analyzer do projecto.
ARTEFACTOS_INVALIDO = 10.0
ARTEFACTOS_DUVIDOSO = 5.0

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


def analisar_resposta(serie, i0, i1, dt, atraso_s, nbase, janela_medida_s=30):
    """Cinetica da resposta de uma metrica a um degrau de potencia.

    O problema que isto resolve: quando a potencia sobe, a metrica nao muda
    de imediato. O DFA-a1 leva dezenas de segundos a descer. Num intervalo
    curto ele ainda esta a descer quando os watts caem -- nunca chegou ao
    valor que aquela potencia produziria. Se fizermos a media do bloco (ou
    mesmo dos ultimos 60 s fixos) misturamos o transitorio com o estado
    estabilizado, e o numero nao representa o efeito real da potencia.

    Devolve:
      lag_50/75/90   segundos ate percorrer 50/75/90% da amplitude
      atingiu_plateau  se a metrica chegou a estabilizar dentro do bloco
      valor_estabilizado  media da janela final ANTES de comecar a inverter
      inicio_plateau_s  quando estabilizou
      amplitude, baseline, extremo
    """
    if not serie:
        return {}

    base_vals = [v for v in serie[max(0, i0 - nbase):i0] if v is not None]
    if not base_vals:
        base_vals = [v for v in serie[i0:i0 + max(1, nbase)] if v is not None]
    if not base_vals:
        return {}
    baseline = statistics.fmean(base_vals)

    atraso = max(1, int(atraso_s / dt))
    fim_resp = min(len(serie), i1 + atraso)
    trecho = [(k, v) for k, v in enumerate(serie[i0:fim_resp], start=i0)
              if v is not None]
    if len(trecho) < 3:
        return {}

    # extremo = ponto mais afastado da baseline (a direccao vem dos dados,
    # nao de suposicoes: HR sobe, SmO2 desce, DFA-a1 desce)
    k_ext, v_ext = max(trecho, key=lambda p: abs(p[1] - baseline))
    amplitude = v_ext - baseline
    if abs(amplitude) < 1e-9:
        return {"baseline": round(baseline, 3)}

    out = {"baseline": round(baseline, 3),
           "extremo": round(v_ext, 3),
           "amplitude": round(amplitude, 3),
           "t_extremo": round((k_ext - i0) * dt, 1)}

    # lag: quando percorreu cada fraccao da amplitude
    for frac, nome in ((0.50, "50"), (0.75, "75"), (0.90, "90")):
        alvo = baseline + frac * amplitude
        for k, v in trecho:
            if (v >= alvo) if amplitude > 0 else (v <= alvo):
                out[f"lag_{nome}"] = round((k - i0) * dt, 1)
                break

    # Plateau: comparar a TAXA de variacao com a taxa maxima observada.
    #
    # Comparar com a amplitude nao serve: num bloco curto a amplitude
    # observada e' pequena e a curva perto do fim parece plana, dando um
    # falso plateau quando na verdade a metrica ainda estava a mudar.
    # A taxa e' o que distingue "estabilizou" de "ainda esta a caminho":
    # numa resposta exponencial, ao fim de 3 tau a taxa cai para ~5% da
    # inicial; ao fim de 1,5 tau ainda esta em ~22%.
    vals = [v for _, v in trecho]
    idxs = [k for k, _ in trecho]
    n_work = sum(1 for k in idxs if k < i1)

    # Estabilizou ou nao? E com que constante de tempo?
    #
    # A primeira versao estimava tau pela razao entre a taxa no fim e no
    # inicio (tau = T / -ln(razao)). Funcionava em curvas limpas e falhou
    # redondamente nos dados reais: o ruido NAO decai, a razao fica perto
    # de 1, o -ln aproxima-se de zero e o tau explodia -- deu 560 s para a
    # HR na bicicleta e 1178 s no remo, quando o valor fisiologico ronda os
    # 30-60 s.
    #
    # Agora ajusta-se mesmo a exponencial v(t) = vf + (v0-vf)*exp(-t/tau)
    # por minimos quadrados, varrendo tau numa grelha. O ruido dispersa-se
    # no residuo em vez de contaminar a estimativa.
    tau = None
    r2 = None
    if n_work >= 8:
        y = vals[:n_work]
        t = [(idxs[a] - i0) * dt for a in range(n_work)]
        v0 = statistics.fmean(y[:max(2, n_work // 10)])
        dur_w = t[-1] if t else 0
        media_y = statistics.fmean(y)
        ss_tot = sum((yi - media_y) ** 2 for yi in y)

        melhor = None
        for cand in (5, 8, 11, 15, 20, 26, 33, 40, 50, 60, 75, 90,
                     110, 135, 165, 200, 250, 300):
            if cand > max(dur_w * 3, 60):
                break
            # vf otimo para este tau (minimos quadrados fechado)
            pesos = [1 - math.exp(-ti / cand) for ti in t]
            den = sum(p * p for p in pesos)
            if den <= 1e-9:
                continue
            num = sum(p * (yi - v0) for p, yi in zip(pesos, y))
            delta = num / den
            ss = sum((yi - (v0 + delta * p)) ** 2 for p, yi in zip(pesos, y))
            if melhor is None or ss < melhor[0]:
                melhor = (ss, cand, v0 + delta)

        if melhor and ss_tot > 0:
            ss, tau, assintota = melhor
            r2 = 1 - ss / ss_tot
            # so se aceita o tau se a exponencial explicar mesmo a curva
            if r2 >= 0.5:
                out["tau_estimado_s"] = round(tau, 1)
                out["tau_r2"] = round(r2, 2)
                # Se o bloco durou menos de ~1,5 tau, so vimos o inicio da
                # curva: o tau e' extrapolacao e nao deve ser usado como
                # valor fisiologico, apenas para dizer "nao estabilizou".
                out["tau_fiavel"] = 1 if dur_w >= 1.5 * tau else 0
                out["assintota_estimada"] = round(assintota, 3)
                out["duracao_para_plateau_s"] = round(3 * tau, 1)
            else:
                tau = None
                out["tau_r2"] = round(r2, 2)

    # Antes de julgar o plateau: ha' sequer um patamar a atingir?
    #
    # Acima do limiar nao existe estado estacionario -- a SmO2 continua a
    # dessaturar e a HR continua a subir enquanto durar o esforco (componente
    # lenta). Nesses blocos "nao estabilizou" NAO quer dizer bloco curto:
    # quer dizer que aquela intensidade nao tem patamar nenhum.
    #
    # Distingue-se comparando o ajuste exponencial (que assume assintota)
    # com um ajuste LINEAR (deriva continua). Se a recta explica tanto ou
    # mais que a exponencial, a metrica esta em deriva, nao a convergir.
    if n_work >= 8:
        y = vals[:n_work]
        t = [(idxs[a] - i0) * dt for a in range(n_work)]
        n_ = len(t)
        mt, my2 = statistics.fmean(t), statistics.fmean(y)
        den_l = sum((ti - mt) ** 2 for ti in t)
        if den_l > 0:
            decl = sum((ti - mt) * (yi - my2) for ti, yi in zip(t, y)) / den_l
            ss_lin = sum((yi - (my2 + decl * (ti - mt))) ** 2
                         for ti, yi in zip(t, y))
            ss_tot2 = sum((yi - my2) ** 2 for yi in y)
            r2_lin = 1 - ss_lin / ss_tot2 if ss_tot2 else 0
            out["r2_linear"] = round(r2_lin, 2)
            out["deriva_por_min"] = round(decl * 60, 4)
            if r2 is not None and r2_lin >= r2 - 0.02 and r2_lin >= 0.5:
                out["regime"] = "deriva continua (sem patamar)"
                out["atingiu_plateau"] = 0
                out["sem_steady_state"] = 1
            elif r2 is not None:
                out["regime"] = "convergente"

    # Plateau: com tau fiavel, a regra e' duracao >= 3 tau (95% da resposta).
    # Sem tau fiavel nao se inventa: fica indeterminado (None), e quem le
    # sabe que nao pode contar com aquele bloco para julgar o efeito.
    inicio_plateau = None
    dur_work_s = (i1 - i0) * dt
    if out.get("sem_steady_state"):
        tau = None          # nao faz sentido falar de tau numa deriva
        out.pop("tau_estimado_s", None)
        out.pop("duracao_para_plateau_s", None)
    if tau:
        if dur_work_s >= 3 * tau:
            out["atingiu_plateau"] = 1
            for a in range(n_work):
                if (idxs[a] - i0) * dt >= 3 * tau:
                    inicio_plateau = idxs[a]
                    break
        else:
            out["atingiu_plateau"] = 0
            out["fraccao_da_resposta"] = round(1 - math.exp(-dur_work_s / tau), 2)

    if inicio_plateau is not None:
        out["inicio_plateau_s"] = round((inicio_plateau - i0) * dt, 1)

    # janela de medida: os ultimos N s ANTES de a metrica comecar a inverter.
    # Assim mede-se o efeito da potencia, nao a recuperacao que ja comecou.
    k_inv = k_ext
    if k_ext < idxs[-1]:
        for k, v in trecho:
            if k <= k_ext:
                continue
            # inverteu mais de 15% da amplitude -> ja esta a recuperar
            if abs(v - v_ext) > 0.15 * abs(amplitude):
                k_inv = k
                break
        else:
            k_inv = idxs[-1]
    fim_med = min(k_inv, i1 + atraso)
    ini_med = max(i0, fim_med - max(2, int(janela_medida_s / dt)))
    med = [v for k, v in trecho if ini_med <= k <= fim_med]
    if med:
        out["valor_estabilizado"] = round(statistics.fmean(med), 3)
        out["janela_medida_de_s"] = round((ini_med - i0) * dt, 1)
        out["janela_medida_ate_s"] = round((fim_med - i0) * dt, 1)
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


def extrair_intervalos(streams, duracao_s=None, modalidade=None):
    """Uma entrada por bloco, pronta a inserir em fisiologia_intervalos."""
    watts = _serie(streams, "watts")
    if not watts:
        return []
    lo, hi = WATTS_PLAUSIVEIS.get(modalidade, (0, 10000))
    dt = passo_temporal(watts, duracao_s)
    blocos = detectar_blocos(watts, dt)
    if not blocos:
        return []

    series = {m: _serie(streams, m)
              for m in ("hr", "smo2", "thb", "resp", "dfa1", "rra1")}
    artefactos = _serie(streams, "artefactos")
    n60 = max(2, int(JANELA_FINAL_S / dt))
    nbase = max(2, int(JANELA_BASELINE_S / dt))
    nrec = max(1, int(ENTRADA_REC_S / dt))

    saida, num = [], 0
    for (i0, i1, rec_fim) in blocos:
        wa, wmin, wmax = _stats(watts, i0, i1)
        # descartar blocos com potencia implausivel para a modalidade
        if wa is None or not (lo <= wa <= hi):
            continue
        num += 1
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

            # tempo de recuperacao: quanto demora a voltar a meio caminho
            # da baseline depois de a potencia cair (so se houver REC)
            if rec_fim and rec_fim > i1:
                base_r, _, _ = _stats(serie, max(0, i0 - nbase), i0)
                fim_v, _, _ = _stats(serie, max(i0, i1 - n60), i1)
                if base_r is not None and fim_v is not None and abs(fim_v - base_r) > 1e-6:
                    alvo50 = fim_v + 0.5 * (base_r - fim_v)
                    subida = base_r > fim_v
                    for k in range(i1, min(rec_fim, len(serie))):
                        v = serie[k]
                        if v is None:
                            continue
                        if (v >= alvo50) if subida else (v <= alvo50):
                            linha[f"rec_{m}_50"] = round((k - i1) * dt, 1)
                            break

            # baseline: estado imediatamente ANTES da transicao
            base, _, _ = _stats(serie, max(0, i0 - nbase), i0)
            if base is not None:
                linha[f"{m}_baseline"] = round(base, 3)

            # cinetica completa: lag de subida, plateau e janela de medida
            resp = analisar_resposta(serie, i0, i1, dt,
                                     ATRASO_RESPOSTA_S.get(m, 30), nbase)
            if resp:
                if "extremo" in resp:
                    linha[f"{m}_extremo"] = resp["extremo"]
                    linha[f"{m}_t_extremo"] = resp.get("t_extremo")
                    linha[f"{m}_amplitude"] = resp.get("amplitude")
                for frac in ("50", "75", "90"):
                    if f"lag_{frac}" in resp:
                        linha[f"lag_{m}_{frac}"] = resp[f"lag_{frac}"]
                if "atingiu_plateau" in resp:
                    linha[f"{m}_atingiu_plateau"] = resp["atingiu_plateau"]
                    linha["atingiu_plateau"] = resp["atingiu_plateau"]
                if "inicio_plateau_s" in resp:
                    linha[f"{m}_inicio_plateau_s"] = resp["inicio_plateau_s"]
                if "tau_estimado_s" in resp:
                    linha[f"{m}_tau_s"] = resp["tau_estimado_s"]
                if "tau_r2" in resp:
                    linha[f"{m}_tau_r2"] = resp["tau_r2"]
                if "tau_fiavel" in resp:
                    linha[f"{m}_tau_fiavel"] = resp["tau_fiavel"]
                if "fraccao_da_resposta" in resp:
                    linha[f"{m}_fraccao_resposta"] = resp["fraccao_da_resposta"]
                if "sem_steady_state" in resp:
                    linha[f"{m}_sem_steady_state"] = resp["sem_steady_state"]
                if "deriva_por_min" in resp:
                    linha[f"{m}_deriva_por_min"] = resp["deriva_por_min"]
                # ESTE e' o valor que representa o efeito da potencia:
                # medido na janela estabilizada, antes de comecar a inverter
                if "valor_estabilizado" in resp:
                    linha[f"{m}_estabilizado"] = resp["valor_estabilizado"]
                    linha[f"{m}_janela_ate_s"] = resp.get("janela_medida_ate_s")

            if m == "dfa1":
                linha["tem_dfa1"] = 1
                linha["tem_dfa1_stream"] = 1
                # qualidade: com muitos artefactos de RR o DFA-a1 e' ruido
                if artefactos:
                    art, _, _ = _stats(artefactos, i0, i1)
                    if art is not None:
                        linha["dfa1_artefactos_pct"] = round(art, 2)
                        if art > ARTEFACTOS_INVALIDO:
                            linha["dfa1_qualidade"] = "invalido"
                            # nao se apaga o valor: fica gravado e marcado,
                            # para se poder filtrar sem perder o historico
                        elif art > ARTEFACTOS_DUVIDOSO:
                            linha["dfa1_qualidade"] = "duvidoso"
                        else:
                            linha["dfa1_qualidade"] = "ok"

        saida.append(linha)
    return saida


# ── escrita ───────────────────────────────────────────────────────────────

def _colunas(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(fisiologia_intervalos)")}


def gravar(conn, activity_id, data, modalidade, linhas):
    """Substitui as linhas dessa atividade. Ignora colunas inexistentes."""
    if not linhas:
        return 0
    garantir_colunas(conn)
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


# ── migracao ──────────────────────────────────────────────────────────────

COLUNAS_NOVAS = {
    # cinetica: valor medido na janela ja estabilizada, e se chegou la
    **{f"{m}_estabilizado": "REAL" for m in
       ("hr", "smo2", "resp", "dfa1", "thb", "rra1")},
    **{f"{m}_atingiu_plateau": "INTEGER" for m in
       ("hr", "smo2", "resp", "dfa1", "thb", "rra1")},
    **{f"{m}_inicio_plateau_s": "REAL" for m in
       ("hr", "smo2", "resp", "dfa1", "thb", "rra1")},
    **{f"{m}_janela_ate_s": "REAL" for m in
       ("hr", "smo2", "resp", "dfa1", "thb", "rra1")},
    **{f"{m}_amplitude": "REAL" for m in
       ("hr", "smo2", "resp", "dfa1", "thb", "rra1")},
    **{f"{m}_tau_s": "REAL" for m in
       ("hr", "smo2", "resp", "dfa1", "thb", "rra1")},
    **{f"{m}_tau_r2": "REAL" for m in
       ("hr", "smo2", "resp", "dfa1", "thb", "rra1")},
    **{f"{m}_tau_fiavel": "INTEGER" for m in
       ("hr", "smo2", "resp", "dfa1", "thb", "rra1")},
    **{f"{m}_sem_steady_state": "INTEGER" for m in
       ("hr", "smo2", "resp", "dfa1", "thb", "rra1")},
    **{f"{m}_deriva_por_min": "REAL" for m in
       ("hr", "smo2", "resp", "dfa1", "thb", "rra1")},
    **{f"{m}_fraccao_resposta": "REAL" for m in
       ("hr", "smo2", "resp", "dfa1", "thb", "rra1")},
    **{f"lag_{m}_{f}": "REAL" for m in ("thb", "rra1") for f in ("50", "75", "90")},
    "rra1_avg_60s": "REAL", "rra1_min_60s": "REAL", "rra1_max_60s": "REAL",
    "rra1_medio_work": "REAL", "rra1_plateau_work": "REAL",
    "rra1_baseline": "REAL", "rra1_extremo": "REAL", "rra1_t_extremo": "REAL",
    "rec_rra1_50": "REAL",
    "dfa1_artefactos_pct": "REAL",
    "dfa1_qualidade": "TEXT",
    "origem": "TEXT",
}


def garantir_colunas(conn):
    """Cria as colunas novas se faltarem. Assim nao e' preciso substituir o
    .db a mao no Drive -- a migracao corre sozinha e e' idempotente."""
    try:
        existentes = _colunas(conn)
    except Exception:
        return []
    criadas = []
    for col, tipo in COLUNAS_NOVAS.items():
        if col not in existentes:
            try:
                conn.execute(f"ALTER TABLE fisiologia_intervalos ADD COLUMN {col} {tipo}")
                criadas.append(col)
            except Exception as e:
                print(f"[INGESTOR] nao criou {col}: {e}")
    # rec_* das restantes metricas, se o schema original nao as tiver
    for m in ("hr", "smo2", "resp", "dfa1", "thb"):
        col = f"rec_{m}_50"
        if col not in existentes and col not in criadas:
            try:
                conn.execute(f"ALTER TABLE fisiologia_intervalos ADD COLUMN {col} REAL")
                criadas.append(col)
            except Exception:
                pass
    if criadas:
        conn.commit()
    return criadas
