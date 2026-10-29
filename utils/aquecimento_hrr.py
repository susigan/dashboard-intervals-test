"""HRR60 e HRR120 por degrau de Aquecimento (funcao pura, sem persistencia).

Segmentacao: reutiliza aquecimento_streams (detectar_escada, _com_escala,
passo_temporal, _serie) com a mesma chamada que analisar_streams. Os degraus
sao i0/i1 em INDICES do array bruto. A recuperacao de cada degrau e' o
intervalo entre o fim do degrau (i1) e o inicio do degrau seguinte (i0 do
proximo). A potencia so' serve de verificacao complementar: nao exige zero.

Tempo: todas as janelas sao medidas com o array 'time' dos streams, nunca
com o indice. Sem 'time' o calculo nao e' feito.

Limites: HR_end usa [t_end-30, t_end): a amostra em t_end NAO entra, porque e'
a primeira da recuperacao. As demais janelas mantem a regra inclusiva ja'
validada analiticamente (excluir o fim retiraria meia amostra e enviesaria
HR_60 em cerca de 0,25 bpm no sinal linear de teste).

Indicadores (segundos apos o fim do WORK, t_end):
  HR_end  media ponderada no tempo em [t_end-30, t_end); >= 20 s validos.
  HR_60   media em [t_end+55, t_end+65]
  HR_120  media em [t_end+115, t_end+125]
  HRR60  = HR_end - HR_60     HRR120 = HR_end - HR_120
  FC_rec_1min  media da FC no inicio da recuperacao, em
          [t_end, min(t_end+60, t_next-15)]. Descritiva: NAO e' HRR60.
          Requer janela >= 30 s. Devolve tambem cobertura e n de amostras.
Cada janela exige cobertura >= 60% do tempo e tem de caber inteira dentro da
recuperacao associada. Zeros de FC sao invalidos. Ausencia nunca vira zero.
Valores negativos de HRR sao preservados.
"""
import math
import statistics

import aquecimento_streams as aqs

GAP_MAX_S = 10.0          # intervalo entre amostras acima disto = lacuna
JANELA_END = (30.0, 0.0)  # HR_end: ultimos 30 s do WORK
MIN_VALIDO_END_S = 20.0   # HR_end: segundos validos minimos
JANELA_60 = (55.0, 65.0)
JANELA_120 = (115.0, 125.0)
MIN_COBERTURA = 0.6       # fracao do tempo da janela com FC valida
FATOR_FASE = 0.7          # complemento: potencia media na REC < 0.7 x alvo
# A borda de INICIO do degrau seguinte e' detetada com atraso (media movel de
# 15 s): medido em sinal sintetico, ~13 s. A janela tem de terminar antes de
# t_next - MARGEM, para nunca incluir o inicio do esforço seguinte.
MARGEM_INICIO_SEGUINTE_S = 15.0
# FC media do inicio da recuperacao (descritiva, nao e' HRR60)
JANELA_REC_1MIN_S = 60.0  # ate' 60 s apos o fim do esforco...
MIN_JANELA_REC_1MIN_S = 30.0  # ...mas nao calcula com menos de 30 s de janela


# Variaveis fisiologicas extra, so' calculadas se o stream existir. Nomes de THb
# sao candidatos: a presenca no stream real NAO esta' verificada.
FISIO_KEYS = {
    "smo2": ["smo2", "SmO2", "smo2_2"],
    "resp": ["respiration", "resp", "breathing_rate"],
    "dfa1": ["dfa_a1", "dfa1", "DFA_a1"],
    "thb":  ["thb", "tHb", "THb", "total_haemoglobin"],
}
FISIO_NOME = {"smo2": "SmO₂", "resp": "Respiração", "dfa1": "DFA-α1", "thb": "THb"}


def _serie_fisio(streams, nome, n):
    for k in FISIO_KEYS[nome]:
        v = streams.get(k)
        if isinstance(v, list) and len(v) == n:
            return [float(x) if _finito(x) else None for x in v]
    return None


def _finito(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pesos(t):
    """Peso temporal de cada amostra (ate ao GAP_MAX_S); a ultima usa a mediana."""
    n = len(t)
    difs = [t[k + 1] - t[k] for k in range(n - 1) if t[k + 1] > t[k]]
    dt_med = statistics.median(difs) if difs else 1.0
    pesos = []
    for k in range(n):
        d = (t[k + 1] - t[k]) if k + 1 < n else dt_med
        pesos.append(min(max(d, 0.0), GAP_MAX_S))
    return pesos, dt_med


def _lacunas(t):
    out = []
    for k in range(len(t) - 1):
        d = t[k + 1] - t[k]
        if d > GAP_MAX_S:
            out.append((t[k], t[k + 1]))
    return out


def _janela_fc(t, hr, pesos, lacunas, a, b, fim_exclusivo=False):
    """Media ponderada no tempo de FC valida em [a, b], com diagnostico.

    fim_exclusivo=True usa [a, b): a amostra em 'b' nao entra. Usado so' em
    HR_end, cuja borda 'b' = t_end e' a primeira amostra da recuperacao.
    """
    if b > t[-1]:
        return {"valor": None, "motivo": "sem_stream_ate_janela",
                "cobertura": None, "n_validas": 0, "n_zeros": 0}
    esperado = b - a
    soma_v = soma_w = 0.0
    n_val = n_zero = 0
    for k, ti in enumerate(t):
        if ti < a or ti > b or (fim_exclusivo and ti == b):
            continue
        v = hr[k]
        if not _finito(v):
            continue
        if v == 0:
            n_zero += 1
            continue
        w = pesos[k]
        soma_v += v * w
        soma_w += w
        n_val += 1
    cobertura = min(soma_w, esperado) / esperado if esperado > 0 else 0.0
    # sobreposicao parcial ou total entre a lacuna (l0, l1) e a janela (a, b)
    tem_lacuna = any(l0 < b and l1 > a for l0, l1 in lacunas)
    if n_val == 0:
        motivo = "lacuna_sem_dados" if tem_lacuna else "sem_amostras_validas"
        return {"valor": None, "motivo": motivo, "cobertura": round(cobertura, 3),
                "n_validas": 0, "n_zeros": n_zero}
    if cobertura < MIN_COBERTURA:
        motivo = "lacuna_sem_dados" if tem_lacuna else "dados_insuficientes"
        return {"valor": None, "motivo": motivo, "cobertura": round(cobertura, 3),
                "n_validas": n_val, "n_zeros": n_zero}
    return {"valor": round(soma_v / soma_w, 3), "motivo": None,
            "cobertura": round(cobertura, 3), "n_validas": n_val, "n_zeros": n_zero}


def _janela_end(t, hr, pesos, lacunas, t_end, t_inicio_work):
    """HR_end: ultimos 30 s do WORK, com >= 20 s validos.

    Fim EXCLUSIVO em t_end: a amostra de t_end ja e' recuperacao.
    """
    a, b = t_end - JANELA_END[0], t_end
    if a < t_inicio_work:
        return {"valor": None, "motivo": "work_curto", "cobertura": None,
                "n_validas": 0, "n_zeros": 0}
    r = _janela_fc(t, hr, pesos, lacunas, a, b, fim_exclusivo=True)
    if r["valor"] is not None:
        # tempo valido real = cobertura x 30 s
        valido_s = (r["cobertura"] or 0.0) * (b - a)
        if valido_s < MIN_VALIDO_END_S:
            r = {**r, "valor": None, "motivo": "dados_insuficientes"}
    return r


def _janela_rec(t, hr, pesos, lacunas, t_end, janela, t_next):
    """HR_60 ou HR_120, so' se a janela couber inteira na recuperacao."""
    a, b = t_end + janela[0], t_end + janela[1]
    if t_next is None:
        return {"valor": None, "motivo": "sem_fim_de_recuperacao", "cobertura": None,
                "n_validas": 0, "n_zeros": 0, "janela": [round(a, 1), round(b, 1)]}
    if b > t_next - MARGEM_INICIO_SEGUINTE_S:
        return {"valor": None, "motivo": "rec_curta", "cobertura": None,
                "n_validas": 0, "n_zeros": 0, "janela": [round(a, 1), round(b, 1)]}
    r = _janela_fc(t, hr, pesos, lacunas, a, b)
    return {**r, "janela": [round(a, 1), round(b, 1)]}


def _fc_media_rec_1min(t, hr, pesos, lacunas, t_end, t_next):
    """FC media no inicio da recuperacao: [t_end, min(t_end+60, t_next-15)].

    Descritiva, separada de HRR60/HRR120. Nunca usa amostras do degrau seguinte
    (o limite superior fica antes de t_next - MARGEM). Sem interpolacao.
    """
    a = t_end
    if t_next is None:
        return {"valor": None, "motivo": "sem_fim_de_recuperacao", "cobertura": None,
                "n_validas": 0, "n_zeros": 0, "janela": None, "duracao_janela_s": None}
    b = min(t_end + JANELA_REC_1MIN_S, t_next - MARGEM_INICIO_SEGUINTE_S)
    dur = b - a
    janela = [round(a, 1), round(b, 1)]
    if dur < MIN_JANELA_REC_1MIN_S:
        return {"valor": None, "motivo": "rec_curta", "cobertura": None,
                "n_validas": 0, "n_zeros": 0, "janela": janela,
                "duracao_janela_s": round(dur, 1)}
    r = _janela_fc(t, hr, pesos, lacunas, a, b)
    return {**r, "janela": janela, "duracao_janela_s": round(dur, 1)}


# ---------------------------------------------------------------------------
# Fim efetivo do esforco (referencia temporal do HRR)
#
# A deteção do Aquecimento usa potencia SUAVIZADA (media movel de 15 s), que
# atrasa a borda de fim em degraus de queda pequena (ate' ~6 s medidos). A
# regra abaixo volta a medir a borda com a potencia BRUTA, dentro do intervalo
# ja' detetado. A deteção, os limites e a associação NAO mudam.
#
# Regra (explicita e testavel):
#  1. Mediana centrada de +/- FIM_MEDIANA_S segundos da potencia bruta. Uma
#     mediana centrada preserva degraus (nao introduz atraso) e ignora picos
#     isolados (ruido por amostra).
#  2. Uma amostra esta' "fora" se a mediana sair de [alvo - tol, alvo + tol],
#     a mesma faixa usada pela deteção.
#  3. Uma queda so' e' aceite se 'fora' se mantiver durante FIM_SUSTENTADO_S
#     segundos seguidos, sem lacuna > GAP_MAX_S. Oscilacoes mais curtas sao
#     ignoradas e nao deslocam o fim.
#  4. Fim efetivo = instante do primeiro inicio de queda aceite, procurado
#     dentro do intervalo detetado e ate' FIM_BUSCA_APOS_S segundos depois do
#     fim detetado (a deteção pode ser ligeiramente precoce).
#  5. Se nao houver queda aceite, ou o fim cair no inicio do esforco, devolve
#     um motivo e NAO inventa timestamp.
# Limitacao: uma descida sustentada de >= 5 s dentro do esforco (pausa real)
# seria tomada como fim. Nao ha' validacao com potencia real de remo nesta fase.
FIM_MEDIANA_S = 3.0
FIM_SUSTENTADO_S = 5.0
FIM_BUSCA_APOS_S = 5.0


def _mediana_local(t, w, k, meia):
    """Mediana dos valores finitos de w com |t - t[k]| <= meia (ou None)."""
    vals = []
    j = k
    while j >= 0 and t[k] - t[j] <= meia:
        if _finito(w[j]):
            vals.append(w[j])
        j -= 1
    j = k + 1
    while j < len(t) and t[j] - t[k] <= meia:
        if _finito(w[j]):
            vals.append(w[j])
        j += 1
    return statistics.median(vals) if vals else None


def fim_efetivo_esforco(t, watts, i0, i1, alvo, tol):
    """Instante efetivo do fim do esforco dentro do intervalo detetado [i0, i1).

    Devolve dict com t_fim_s (ou None), motivo, t_fim_detetado_s e
    deslocamento_s (efetivo - detetado; negativo = antes do detetado).
    Nao altera i0/i1: so' procura a queda na potencia bruta.
    """
    t_det = t[i1 - 1]
    base = {"t_fim_s": None, "motivo": None, "t_fim_detetado_s": round(t_det, 3),
            "deslocamento_s": None, "fonte": "potencia_bruta_mediana"}
    lim = t_det + FIM_BUSCA_APOS_S
    n = len(t)
    # Inicio de queda: so' ate' 'lim'. Confirmacao: precisa de amostras ate' lim + FIM_SUSTENTADO_S.
    fora = {}
    k_calc = i0
    while k_calc < n and t[k_calc] <= lim + FIM_SUSTENTADO_S:
        m = _mediana_local(t, watts, k_calc, FIM_MEDIANA_S)
        fora[k_calc] = None if m is None else not (alvo - tol <= m <= alvo + tol)
        k_calc += 1
    if not any(v is not None for v in fora.values()):
        return {**base, "motivo": "sem_amostras_de_potencia"}
    for j in range(i0, k_calc):
        if t[j] > lim:
            break
        if fora[j] is not True:
            continue
        if j > i0 and fora.get(j - 1) is True:
            continue  # so' o inicio de cada queda interessa
        confirmada = True
        k = j
        while k < k_calc and t[k] - t[j] < FIM_SUSTENTADO_S:
            if fora[k] is not True or (k > j and t[k] - t[k - 1] > GAP_MAX_S):
                confirmada = False
                break
            k += 1
        # precisa de uma amostra depois do intervalo de confirmacao (existe e foi calculada)
        if confirmada and k < k_calc:
            if j == i0:
                return {**base, "motivo": "queda_no_inicio_do_esforco"}
            return {**base, "t_fim_s": round(t[j], 3), "motivo": None,
                    "deslocamento_s": round(t[j] - t_det, 3)}
    return {**base, "motivo": "sem_queda_confirmada"}


def _media_potencia(t, watts, a, b):
    if watts is None:
        return None
    vals = [watts[k] for k, ti in enumerate(t)
            if a <= ti <= b and _finito(watts[k])]
    return round(statistics.fmean(vals), 1) if vals else None


TIPOS_MAX = 60


def _util(d):
    """Serie numerica utilizavel: lista nao vazia com pelo menos um numero."""
    return isinstance(d, list) and any(_finito(v) for v in d)


def montar_streams_brutos(lista):
    """Converte a lista de /streams em dict {tipo: serie} SEM reduzir os dados.

    Mesma regra de utils/api_client.parse_streams, sem downsample:
      - entradas 'allNull', sem dados numericos ou com valueTypeIsArray sao
        ignoradas e NUNCA sobrescrevem uma entrada util do mesmo tipo;
      - duplicados utiles: o primeiro fica com o nome do tipo e os seguintes
        ficam como tipo_2, tipo_3 (como em parse_streams).
    Devolve (streams, nomes_recebidos). nomes_recebidos inclui tambem as
    entradas ignoradas, so' com nomes (diagnostico).
    """
    streams, nomes = {}, set()
    for st in (lista or []):
        if not isinstance(st, dict):
            continue
        tipo = st.get('type') or st.get('name')
        if not tipo:
            continue
        nomes.add(str(tipo)[:40])
        if st.get('allNull') or st.get('valueTypeIsArray'):
            continue
        d = st.get('data')
        if not _util(d):
            continue
        chave, n = str(tipo), 2
        while chave in streams:
            chave = f"{tipo}_{n}"
            n += 1
        streams[chave] = d
    return streams, sorted(nomes)[:TIPOS_MAX]


def buscar_streams_brutos(icu_get, aid):
    """UM pedido /activity/{aid}/streams com includeDefaults=true, mesma montagem.

    icu_get: a funcao do api_client (injetada, para testes). Devolve
    (streams, nomes_recebidos, erro). Usado pelo HRR e pelo painel de blocos,
    para que os dois caminhos vejam exatamente a mesma resposta.
    """
    raw, err = icu_get(f'/activity/{aid}/streams', {'includeDefaults': 'true'})
    if err:
        return None, [], err
    lista = raw
    if isinstance(raw, dict):
        lista = raw.get('streams') or raw.get('content') or []
    streams, nomes = montar_streams_brutos(lista)
    return streams, nomes, None


def _tipos_recebidos(streams):
    """So' os NOMES dos streams recebidos (sem valores), para diagnostico."""
    if not isinstance(streams, dict):
        return []
    nomes = sorted(str(k)[:40] for k in streams.keys())
    return nomes[:TIPOS_MAX]


def hrr_por_degrau(streams, modalidade, protocolos, duracao_s=None):
    """Calcula HR_end, HR_60, HR_120, HRR60 e HRR120 por degrau de Aquecimento.

    streams: dict com 'time', 'watts' e 'heartrate' (listas do mesmo tamanho).
    Devolve sempre um dict com 'status' explicito; nunca levanta excecao por
    dados em falta.
    """
    base = {"modalidade": modalidade, "degraus": [], "gaps": []}
    proto = (protocolos or {}).get(modalidade)
    if not proto:
        return {**base, "status": "sem_protocolo"}

    watts = aqs._serie(streams, "watts")
    hr = aqs._serie(streams, "hr")
    t_raw = streams.get("time")
    if not watts:
        return {**base, "status": "sem_watts"}
    if not hr:
        # Lista de FC ausente ou vazia. Diagnostico: apenas os nomes recebidos.
        return {**base, "status": "sem_hr", "tipos_recebidos": _tipos_recebidos(streams)}
    if not isinstance(t_raw, list) or not t_raw:
        return {**base, "status": "sem_timestamps"}
    if not (len(t_raw) == len(watts) == len(hr)):
        return {**base, "status": "erro_streams",
                "detalhe": f"tamanhos: time={len(t_raw)} watts={len(watts)} hr={len(hr)}"}
    if not all(_finito(x) for x in t_raw):
        return {**base, "status": "timestamps_invalidos", "detalhe": "time com valores nao numericos"}
    t = [float(x) for x in t_raw]
    if any(t[k + 1] < t[k] for k in range(len(t) - 1)):
        return {**base, "status": "timestamps_invalidos", "detalhe": "time nao crescente"}

    # 1. Segmentacao do Aquecimento (mesma chamada que analisar_streams)
    dt = aqs.passo_temporal(streams, duracao_s)
    achados = aqs._com_escala(dt, lambda: aqs.detectar_escada(
        watts, proto["watts"], proto["tol"],
        proto.get("min_blocos", len(proto["watts"]))))
    if not achados:
        return {**base, "status": "sem_degraus"}

    pesos, dt_med = _pesos(t)
    lacunas = _lacunas(t)
    out_base = {**base, "status": "ok", "n_amostras": len(t),
                "dt_mediano_s": round(dt_med, 3),
                "gaps": [{"de_s": round(a, 1), "ate_s": round(b, 1)} for a, b in lacunas[:50]],
                "n_lacunas": len(lacunas)}

    # 2. Por degrau: WORK, RECOVERY associada e janelas de FC
    degraus = []
    for n, (i0, i1, alvo) in enumerate(achados):
        t_ini_work = t[i0]
        t_next = t[achados[n + 1][0]] if n + 1 < len(achados) else None
        w_work = [watts[k] for k in range(i0, i1) if _finito(watts[k])]
        fim = fim_efetivo_esforco(t, watts, i0, i1, alvo, proto["tol"])
        t_end = fim["t_fim_s"]
        if t_end is None:
            # sem fim efetivo confirmado: nao ha referencia temporal, nao se inventa
            falha = {"valor": None, "motivo": "fim_efetivo_" + fim["motivo"],
                     "cobertura": None, "n_validas": 0, "n_zeros": 0}
            hr_end = falha
            h60 = h120 = (dict(falha) if t_next is not None else
                          {"valor": None, "motivo": "sem_fim_de_recuperacao", "cobertura": None,
                           "n_validas": 0, "n_zeros": 0})
            fc_rec = dict(falha) if t_next is not None else {
                "valor": None, "motivo": "sem_fim_de_recuperacao", "cobertura": None,
                "n_validas": 0, "n_zeros": 0, "janela": None, "duracao_janela_s": None}
        else:
            hr_end = _janela_end(t, hr, pesos, lacunas, t_end, t_ini_work)
            h60 = _janela_rec(t, hr, pesos, lacunas, t_end, JANELA_60, t_next)
            h120 = _janela_rec(t, hr, pesos, lacunas, t_end, JANELA_120, t_next)
            fc_rec = _fc_media_rec_1min(t, hr, pesos, lacunas, t_end, t_next)

        def _hrr(ref, h):
            if h["valor"] is None and h["motivo"] == "sem_fim_de_recuperacao":
                return None, h["motivo"]  # ultimo degrau: sem recuperacao seguinte
            if ref["valor"] is None:
                return None, f"hr_end_{ref['motivo']}"
            if h["valor"] is None:
                return None, h["motivo"]
            return round(ref["valor"] - h["valor"], 3), None

        hrr60, m60 = _hrr(hr_end, h60)
        hrr120, m120 = _hrr(hr_end, h120)
        fisio = {}
        for nome in FISIO_KEYS:
            sv = _serie_fisio(streams, nome, len(t))
            if sv is None or t_end is None:
                continue
            f_end = _janela_end(t, sv, pesos, lacunas, t_end, t_ini_work)
            f60 = _janela_rec(t, sv, pesos, lacunas, t_end, JANELA_60, t_next)
            f120 = _janela_rec(t, sv, pesos, lacunas, t_end, JANELA_120, t_next)
            ref = f_end["valor"]
            fisio[nome] = {
                "nome": FISIO_NOME[nome],
                "ref_end": ref,
                "rec_60": f60["valor"], "rec_120": f120["valor"],
                "dif_60": round(ref - f60["valor"], 3) if ref is not None and f60["valor"] is not None else None,
                "dif_120": round(ref - f120["valor"], 3) if ref is not None and f120["valor"] is not None else None,
                "motivo_end": None if ref is not None else f_end["motivo"],
                "motivo_60": f60["motivo"], "motivo_120": f120["motivo"],
            }
        rec_fim = t_next
        pot_rec = _media_potencia(t, watts, t_end, rec_fim) if (rec_fim and t_end is not None) else None
        degraus.append({
            "n": n + 1,
            "alvo_w": alvo,
            "watts_work_medio": round(statistics.fmean(w_work), 1) if w_work else None,
            "work": {"t_inicio_s": round(t_ini_work, 1),
                     "t_fim_s": round(t_end, 1) if t_end is not None else None,
                     "dur_s": round(t_end - t_ini_work, 1) if t_end is not None else None,
                     "fim_efetivo": fim},
            "rec": {"t_inicio_s": round(t_end, 1) if t_end is not None else None,
                    "t_fim_s": round(rec_fim, 1) if rec_fim is not None else None,
                    "dur_s": round(rec_fim - t_end, 1) if (rec_fim is not None and t_end is not None) else None,
                    "fim_por": "inicio_degrau_seguinte" if rec_fim is not None else None,
                    "potencia_media_w": pot_rec,
                    "fase_confirmada": (pot_rec < FATOR_FASE * alvo) if pot_rec is not None else None},
            "hr_end": hr_end,
            "hr_60": h60,
            "hr_120": h120,
            "hrr60": hrr60, "hrr60_motivo": m60,
            "hrr120": hrr120, "hrr120_motivo": m120,
            # descritiva: FC media do inicio da recuperacao (NAO e' HRR60)
            "fc_media_rec_1min": fc_rec,
            "fisio": fisio,
        })
    return {**out_base, "margem_inicio_seguinte_s": MARGEM_INICIO_SEGUINTE_S,
            "degraus": degraus}


MAX_PONTOS_GRAFICO = 1500


def serie_grafico(streams, max_pontos=MAX_PONTOS_GRAFICO):
    """Serie reduzida por amostragem (nao por media) SO' para desenhar.

    Os valores de HRR/HR_end/HR_60/HR_120 nunca saem daqui: sao calculados
    sobre a serie original em hrr_por_degrau. Zeros de FC/SmO2/etc. viram None
    para a linha nao cair a zero no grafico.
    """
    t_raw = streams.get("time") if isinstance(streams, dict) else None
    if not isinstance(t_raw, list) or not t_raw or not all(_finito(x) for x in t_raw):
        return None
    t = [float(x) for x in t_raw]
    n = len(t)
    stride = max(1, -(-n // max_pontos))
    idx = list(range(0, n, stride))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    out = {"stride": stride, "t": [round(t[k], 1) for k in idx]}
    series = {"watts": aqs._serie(streams, "watts"), "hr": aqs._serie(streams, "hr")}
    for nome in FISIO_KEYS:
        series[nome] = _serie_fisio(streams, nome, n)
    for nome, sv in series.items():
        if sv is None or len(sv) != n:
            continue
        out[nome] = [None if (sv[k] is None or (nome != "watts" and sv[k] == 0))
                     else round(sv[k], 2) for k in idx]
    return out



# ── persistencia por sessao (uma linha por degrau) ───────────────────────

# Sobe sempre que a logica de HRR muda: as sessoes guardadas com outra versao
# sao recalculadas no proximo reprocessamento.
VERSAO_HRR = 1


def _val(d):
    """Valor de um bloco {valor, motivo, ...} ou None."""
    return d.get("valor") if isinstance(d, dict) else None


def _motivo(d):
    return d.get("motivo") if isinstance(d, dict) else None


def registo_para_bd(aid, modalidade, data, r):
    """Converte o resultado de hrr_por_degrau em (sessao, degraus) para a BD.

    Nunca inventa valores: um degrau sem FC util fica com valor None e o
    motivo. Sessoes sem FC ou sem degraus ficam so com o status.
    """
    status = r.get("status", "erro")
    detalhe = None
    if status == "sem_hr":
        detalhe = ",".join(r.get("tipos_recebidos") or [])[:500] or None
    sessao = {"activity_id": aid, "modalidade": modalidade, "data": data,
              "status": status, "detalhe": detalhe,
              "n_degraus": len(r.get("degraus") or []) if status == "ok" else 0}
    degraus = []
    if status == "ok":
        for d in r.get("degraus") or []:
            fc = d.get("fc_media_rec_1min") or {}
            degraus.append({
                "degrau_num": d.get("n"),
                "alvo_w": d.get("alvo_w"),
                "watts_work_medio": d.get("watts_work_medio"),
                "hr_end": _val(d.get("hr_end")),
                "hr_end_motivo": _motivo(d.get("hr_end")),
                "hr_60": _val(d.get("hr_60")),
                "hr_60_motivo": _motivo(d.get("hr_60")),
                "hr_120": _val(d.get("hr_120")),
                "hr_120_motivo": _motivo(d.get("hr_120")),
                "hrr60": d.get("hrr60"),
                "hrr60_motivo": d.get("hrr60_motivo"),
                "hrr120": d.get("hrr120"),
                "hrr120_motivo": d.get("hrr120_motivo"),
                "fc_rec1min": _val(fc),
                "fc_rec1min_motivo": _motivo(fc),
            })
    return sessao, degraus
