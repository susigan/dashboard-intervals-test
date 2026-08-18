"""
PERFIL_METABOLICO.PY — VO2max, VLamax e modelo de Mader a partir dos MMP

Portado do tab_cp_model.py (Streamlit) do repositorio dashboard, mantendo as
formulas e as constantes, mas sem Streamlit e sem os modelos de CP -- esses
pertencem a tab Recordes.

Fonte dos dados: tabela power_curves (MMP por duracao), peso corporal e idade.

Duracoes de MMP por modalidade (definidas pelo atleta):
    Bike        3, 5, 12 min
    Row / Ski   1, 5, 12 min
    Run         5, 12, 20 min

Limites de validade que convem ter presentes:
  - A formula de Hawley para VO2max foi calibrada em CICLISMO. Aplicada ao
    remo, ski ou corrida devolve numeros, mas a relacao potencia/VO2 e'
    diferente -- por isso o resultado vem marcado como estimativa nessas
    modalidades.
  - O VLamax por konaendu/Mader e' uma estimativa a partir de potencias
    maximas, nao uma medicao de lactato. Serve para comparar o proprio
    atleta ao longo do tempo, nao para valores absolutos.
"""

import json
import math

# Valores por defeito, editaveis pelo utilizador na interface
ALTURA_CM_DEFEITO = 186
IDADE_DEFEITO = 40

# Tecto de potencia para o pico de 1 s, por modalidade. Na corrida os watts
# sao ESTIMADOS e o pico de 1 s dispara com falhas de GPS ou cadencia -- um
# Pmax absurdo empurra o termo de sprint e inflaciona o VLamax.
PMAX_PLAUSIVEL = {"Bike": 1800, "Row": 1200, "Ski": 1200, "Run": 900}

# Duracoes (segundos) usadas em cada modalidade, pela ordem em que entram
DURACOES_MMP = {
    "Bike": [180, 300, 720],
    "Row":  [60, 300, 720],
    "Ski":  [60, 300, 720],
    "Run":  [300, 720, 1200],
}

# Constantes do modelo (Mader 1986 / Hauser 2014 / konaendu)
KS1     = 0.25 ** 2      # 0.0625
KS2     = 1.1 ** 3       # 1.331
KEL     = 2.0            # Mader 1986
LAC_O2  = 0.01576
WATT_O2 = 12.5           # ml/min/W
VOL_REL = 0.45           # fraccao de massa muscular activa (notebook Mader)

# ATENCAO: o vol_rel usado na formula do VLamax NAO e' esta constante.
# E' derivado da carga: (MMP_curto + MMP_medio) / 3 / peso. Usar 0.45 aqui
# satura o VLamax no tecto (1.8) e destroi o calculo -- foi o que aconteceu
# na primeira versao deste modulo.

GLICOGENIO_POR_KG = {"elite": 17, "advanced": 15,
                     "intermediate": 14, "beginner": 13}


def vo2max_hawley(mmp_curto, mmp_medio, peso):
    """VO2max pela media de duas estimativas de Hawley.

    Na Bike sao MMP3 e MMP5; nas outras modalidades sao as duas primeiras
    duracoes definidas para essa modalidade.
    """
    if not (mmp_curto and mmp_medio and peso):
        return None
    v1 = mmp_curto / peso * 10.8 + 7
    v2 = mmp_medio / peso * 10.8 + 7
    return max(20.0, min(95.0, (v1 + v2) / 2))


def vol_rel_vlamax(mmp_curto, mmp_medio, peso):
    """vol_rel especifico da formula do VLamax: carga relativa, nao 0.45."""
    if not (mmp_curto and mmp_medio and peso):
        return None
    return ((mmp_curto + mmp_medio) / 3.0) / peso


def vlamax_konaendu(vo2max, pmax, peso, altura_cm, idade, vr=None):
    """VLamax estimado (mmol/L/s) — componente aerobia (Mader) + sprint."""
    if not (vo2max and peso and altura_cm and idade and vr):
        return None
    bmi = peso / ((altura_cm / 100.0) ** 2)
    mader = (0.02049 / vr * vo2max * (bmi / 22)
             * (1 + 0.000025 * idade - 0.0000001 * peso))
    sprint = (0.000004 / vr * (pmax or 0)
              * (1 + 0.0000001 * idade - 0.0000001 * peso))
    return max(0.05, min(1.8, mader + sprint))


def classificar_perfil(vlamax):
    if vlamax is None:
        return None
    if vlamax < 0.30:
        return "Endurance puro"
    if vlamax < 0.50:
        return "Endurance / Speed"
    if vlamax < 0.80:
        return "Speed / Power"
    return "Sprint / Anaerobio"


def modelo_mader(vo2max, vlamax, peso, bf_pct=None, passo=0.01):
    """Curva metabolica completa: MLSS/AT, FatMax, substratos, lactato.

    Varre o VO2 em estado estacionario de 0.5 ate ao VO2max e, para cada
    ponto, calcula a producao e a combustao de lactato. O AT (MLSS) e' onde
    as duas se igualam; o FatMax e' onde a oxidacao de gordura e' maxima.
    """
    if not (vo2max and vlamax and peso):
        return None

    vo2ss, adp, vlass, lacomb, vlanet = [], [], [], [], []
    v = 0.5
    while v < vo2max - 0.05:
        vo2ss.append(v)
        den = vo2max - v
        a = math.sqrt(max(0.0, (KS1 * v) / den)) if den > 0 else 0.0
        adp.append(a)
        vl = 60 * vlamax / (1 + (KS2 / max(a ** 3, 1e-12)))
        vlass.append(vl)
        lc = (LAC_O2 / VOL_REL) * v
        lacomb.append(lc)
        vlanet.append(vl - lc)
        v += passo

    if not vo2ss:
        return None

    # AT / MLSS: producao = combustao
    i_at = min(range(len(vlanet)), key=lambda k: abs(vlanet[k]))

    overall = [(vl * (VOL_REL * peso) * ((1 / 4.3) * 22.4) / peso) + v
               for vl, v in zip(vlass, vo2ss)]
    watts = [max(0.0, o * peso / WATT_O2) for o in overall]

    # FatMax: maxima oxidacao de gordura, abaixo do AT
    fat = [max(0.0, (-vn) * VOL_REL / LAC_O2 * peso * 60 * 4.65 / 9.5 / 1000)
           for vn in vlanet[:i_at]] if i_at > 0 else []
    i_fm = max(range(len(fat)), key=lambda k: fat[k]) if fat else 0

    n_cho = min(i_at + 400, len(watts))
    cho = [vl * (peso * VOL_REL) * 60 / 1000 / 2 * 162.14
           for vl in vlass[:n_cho]]

    # lactato estacionario abaixo do AT
    lactato = []
    for k in range(i_at):
        den_in = max(vo2max - vo2ss[k], 0.01)
        termo = (KS2 / max((KS1 * vo2ss[k]) / den_in, 1e-9)) ** 1.5
        den = (LAC_O2 / VOL_REL) * vo2ss[k] * (1 + termo) - vlamax * 60
        lactato.append(math.sqrt(max(0.0, (vlamax * KEL * 60) / den))
                       if den > 0 else None)

    # glicogenio
    glicogenio = None
    if bf_pct is not None:
        lean = peso - peso * bf_pct / 100.0
        musculo = lean * 0.70
        if vo2max >= 65 and vlamax <= 0.5:
            nivel = "elite"
        elif vo2max >= 50 and vlamax <= 0.7:
            nivel = "advanced"
        elif vo2max >= 40 and vlamax <= 0.9:
            nivel = "intermediate"
        else:
            nivel = "beginner"
        glicogenio = {"nivel": nivel,
                      "musculo_kg": round(musculo, 1),
                      "total_g": round(90 + musculo * GLICOGENIO_POR_KG[nivel])}

    w_at = watts[i_at]
    w_fm = watts[i_fm] if i_fm < len(watts) else None

    # curva para grafico (amostrada, para nao mandar milhares de pontos)
    passo_g = max(1, len(watts) // 120)
    curva = [{"watts": round(watts[k], 1),
              "vo2": round(vo2ss[k], 2),
              "fat_g_h": round(fat[k], 1) if k < len(fat) else None,
              "cho_g_h": round(cho[k], 1) if k < len(cho) else None,
              "lactato": (round(lactato[k], 2)
                          if k < len(lactato) and lactato[k] is not None else None)}
             for k in range(0, n_cho, passo_g)]

    return {
        "mlss_at_w": round(w_at),
        "fatmax_w": round(w_fm) if w_fm else None,
        "pct_vo2max_at": round(vo2ss[i_at] / vo2max * 100, 1),
        "pct_vo2max_fatmax": round(vo2ss[i_fm] / vo2max * 100, 1) if fat else None,
        "fat_no_fatmax_g_h": round(fat[i_fm], 1) if fat else None,
        "cho_no_at_g_h": round(cho[i_at - 1], 1) if i_at > 0 and cho else None,
        "fatmax_pct_mlss": round(w_fm / w_at * 100, 1) if (w_fm and w_at) else None,
        "glicogenio": glicogenio,
        "curva": curva,
    }


def zonas_a_partir_do_at(w_at, vo2max_w=None):
    """Zonas de intensidade ancoradas no MLSS/AT, nao numa FTP arbitraria."""
    if not w_at:
        return []
    z = [("Z1 recuperacao", 0.00, 0.55),
         ("Z2 endurance",   0.55, 0.75),
         ("Z3 tempo",       0.75, 0.90),
         ("Z4 limiar",      0.90, 1.00),
         ("Z5 VO2max",      1.00, 1.15),
         ("Z6 anaerobio",   1.15, 1.50)]
    return [{"zona": n, "de_w": round(w_at * a), "ate_w": round(w_at * b),
             "pct_at": f"{int(a*100)}-{int(b*100)}%"} for n, a, b in z]


def calcular(modalidade, mmps, peso, altura_cm, idade, pmax=None, bf_pct=None):
    """Ponto de entrada. mmps = {duracao_s: watts} com as duracoes da modalidade."""
    duracoes = DURACOES_MMP.get(modalidade)
    if not duracoes:
        return {"status": "erro", "mensagem": f"modalidade sem MMP definidos: {modalidade}"}

    valores = [mmps.get(d) for d in duracoes]
    em_falta = [d for d, v in zip(duracoes, valores) if not v]
    if em_falta:
        return {"status": "dados_insuficientes",
                "mensagem": "faltam MMP",
                "duracoes_em_falta_s": em_falta,
                "duracoes_necessarias_s": duracoes}

    vo2max = vo2max_hawley(valores[0], valores[1], peso)
    vr = vol_rel_vlamax(valores[0], valores[1], peso)
    vlamax = vlamax_konaendu(vo2max, pmax, peso, altura_cm, idade, vr=vr)
    mader = modelo_mader(vo2max, vlamax, peso, bf_pct)

    limiares = limiares_lactato(mader.get("curva") if mader else None,
                                mader.get("mlss_at_w") if mader else None)

    return {
        "status": "ok",
        "modalidade": modalidade,
        "limiares": limiares,
        "duracoes_usadas_s": duracoes,
        "mmp_usados": {str(d): v for d, v in zip(duracoes, valores)},
        "pmax_w": pmax,
        "vo2max": round(vo2max, 1) if vo2max else None,
        "vo2max_validade": ("calibrado (ciclismo)" if modalidade == "Bike"
                            else "estimativa: formula de Hawley e' de ciclismo"),
        "vlamax": round(vlamax, 3) if vlamax else None,
        "vlamax_saturado": bool(vlamax and (vlamax >= 1.79 or vlamax <= 0.051)),
        "vol_rel_vlamax": round(vr, 3) if vr else None,
        "perfil": classificar_perfil(vlamax),
        "mader": mader,
        "zonas": zonas_a_partir_do_at(mader.get("mlss_at_w") if mader else None),
    }


# ── extraccao dos MMP a partir da tabela power_curves ─────────────────────

def _descomprimir(secs, watts):
    """As curvas vem guardadas como texto (JSON ou separado por virgulas)."""
    def _lista(v):
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return list(v)
        txt = str(v).strip()
        if not txt:
            return []
        try:
            d = json.loads(txt)
            return list(d) if isinstance(d, (list, tuple)) else []
        except Exception:
            pass
        sep = ',' if ',' in txt else None
        try:
            return [float(x) for x in (txt.split(sep) if sep else txt.split())]
        except Exception:
            return []
    return _lista(secs), _lista(watts)


# Fraccao do melhor historico de cada duracao abaixo da qual se considera
# que NAO houve esforco maximo dessa duracao na season. Nao e' uma constante
# fisiologica -- e' um criterio de decisao, e por isso vai a jusante como
# parametro (?limiar_max=) e o racio observado e' sempre devolvido, para se
# poder discutir o numero em vez de o aceitar.
LIMIAR_ESFORCO_MAXIMO = 0.85


def _indice_curva(linha):
    """(data, season, {duracao: watts}) de uma linha de power_curves.

    A API devolve o array 'watts' TRUNCADO no comprimento da sessao: uma
    sessao de 1h nao tem ponto de 5400s. O 'secs' guardado e' a lista
    completa que foi pedida, por isso exigir len(secs) == len(watts)
    descartava todas as sessoes curtas -- que sao exactamente aquelas onde
    se fazem os esforcos maximos de 3 e 5 minutos. Os arrays sao alinhados
    desde o inicio e ordenados, portanto trunca-se pelo mais curto.
    """
    secs, watts = _descomprimir(linha.get('secs'), linha.get('watts'))
    n = min(len(secs), len(watts))
    if n == 0:
        return None, None, None
    data = str(linha.get('date'))[:10] if linha.get('date') else None
    indice = {int(s): w for s, w in zip(secs[:n], watts[:n]) if w is not None}
    return data, linha.get('season'), indice


def _watts_em(indice, d):
    """Watts a duracao d, aceitando a duracao mais proxima dentro de 5%."""
    w = indice.get(d)
    if w is None:
        proximos = [k for k in indice if abs(k - d) <= max(5, d * 0.05)]
        w = max((indice[k] for k in proximos), default=None)
    return w


def melhores_mmp(linhas, modalidade, pmax_max=None, season_activa=None,
                 limiar_max=None):
    """Melhor MMP de cada duracao necessaria, e o pico de 1 s.

    linhas: [{'date':..., 'secs':..., 'watts':..., 'season':...}] filtradas
    pela modalidade mas NAO pela season -- o filtro e' feito aqui, para se
    poder recuar quando falta.

    Regra: o valor de cada duracao e' o melhor da season activa, desde que
    seja mesmo um esforco maximo. O criterio e' o racio contra o melhor
    historico do proprio atleta nessa duracao: se a season so tem rolos
    constantes, o "melhor" de 3 min fica a 65% do que ja se fez e nao
    descreve capacidade nenhuma -- alimentar o modelo de Mader com isso
    produz um VO2max e um MLSS sem significado. Nesse caso recua para a
    season anterior mais recente que tenha um esforco a serio.

    Cada duracao recua de forma independente, por isso devolve-se a season
    e a data de cada uma: um MMP3 desta epoca com um MMP12 de ha dois anos
    descreve um atleta que nunca existiu, e isso tem de ficar visivel.
    """
    duracoes = DURACOES_MMP.get(modalidade, [])
    tecto = pmax_max or PMAX_PLAUSIVEL.get(modalidade, 2000)
    limiar = LIMIAR_ESFORCO_MAXIMO if limiar_max is None else float(limiar_max)

    # duracao -> season -> (watts, data)
    por_season = {d: {} for d in duracoes}
    pico_por_season = {}
    ultima_data = {}          # season -> data mais recente vista
    lidas, ignoradas = 0, 0

    for l in linhas:
        data, sea, indice = _indice_curva(l)
        if not indice:
            ignoradas += 1
            continue
        lidas += 1
        if data and (sea not in ultima_data or data > ultima_data[sea]):
            ultima_data[sea] = data

        for d in duracoes:
            w = _watts_em(indice, d)
            if w is None:
                continue
            actual = por_season[d].get(sea)
            if actual is None or w > actual[0]:
                por_season[d][sea] = (float(w), data)

        w1 = indice.get(1)
        if w1 is not None and w1 <= tecto:
            actual = pico_por_season.get(sea)
            if actual is None or w1 > actual[0]:
                pico_por_season[sea] = (float(w1), data)

    # seasons por ordem de recencia real (data mais recente), nao por nome:
    # etiquetas como "January 2026" ou "2025/26" nao ordenam alfabeticamente
    ordem = sorted(ultima_data, key=lambda s: ultima_data[s], reverse=True)

    def _escolher(mapa):
        """-> (watts, data, season, recuou, racio_na_season, melhor_historico)"""
        if not mapa:
            return None, None, None, False, None, None
        historico = max(v[0] for v in mapa.values())

        if season_activa is None:              # sem filtro: melhor de sempre
            sea = max(mapa, key=lambda s: mapa[s][0])
            return mapa[sea][0], mapa[sea][1], sea, False, None, historico

        na_season = mapa.get(season_activa)
        racio = (na_season[0] / historico) if (na_season and historico) else None
        if na_season and racio is not None and racio >= limiar:
            return na_season[0], na_season[1], season_activa, False, racio, historico

        # recua: season anterior mais recente COM esforco maximo a serio
        for s in ordem:
            if s == season_activa:
                continue
            v = mapa.get(s)
            if v and historico and v[0] / historico >= limiar:
                return v[0], v[1], s, True, racio, historico
        # nenhuma qualifica: fica o melhor historico, seja de onde for
        sea = max(mapa, key=lambda s: mapa[s][0])
        return (mapa[sea][0], mapa[sea][1], sea,
                sea != season_activa, racio, historico)

    mmp, datas, seasons, recuou = {}, {}, {}, {}
    qualidade = {}
    for d in duracoes:
        w, dt, sea, fb, rc, hist = _escolher(por_season[d])
        mmp[d], datas[d], seasons[d], recuou[d] = w, dt, sea, fb
        qualidade[d] = {'racio_na_season': round(rc, 3) if rc is not None else None,
                        'melhor_historico_w': round(hist) if hist else None,
                        'melhor_na_season_w': (round(por_season[d][season_activa][0])
                                               if por_season[d].get(season_activa)
                                               else None)}

    pw, pdt, psea, pfb, prc, phist = _escolher(pico_por_season)

    return {"mmp": mmp, "datas": datas,
            "seasons": seasons, "recuou": recuou,
            "qualidade": qualidade,
            "limiar_esforco_maximo": limiar,
            "pmax_w": pw, "pmax_data": pdt,
            "pmax_season": psea, "pmax_recuou": pfb,
            "pmax_racio_na_season": round(prc, 3) if prc is not None else None,
            "pmax_tecto_aplicado": tecto,
            "seasons_disponiveis": ordem,
            "curvas_lidas": lidas, "curvas_ignoradas": ignoradas}


# ── limiares de lactato a partir da curva do modelo ──────────────────────

def limiares_lactato(curva, mlss_w=None):
    """LT1 e LT2 a partir da curva de lactato estacionario do modelo.

    LT1 (limiar aerobio): primeiro aumento sustentado acima da linha de
        base -- convencao +0.5 mmol/L sobre o minimo observado.
    LT2 (limiar anaerobio): maxima curvatura da curva de lactato, que e' a
        definicao geometrica do ponto de inflexao. Nao se usa o "4 mmol/L"
        fixo: esse valor e' uma convencao de laboratorio que varia muito
        entre atletas, e num modelo derivado de potencias maximas seria uma
        falsa precisao.

    Devolve tambem mlss_w para comparacao -- no modelo de Mader o MLSS ja e'
    onde producao e combustao se igualam, por isso LT2 e MLSS devem estar
    proximos. Se divergirem muito, o modelo nao esta a descrever bem o atleta.
    """
    pts = [(p["watts"], p["lactato"]) for p in (curva or [])
           if p.get("lactato") is not None and p.get("watts")]
    if len(pts) < 8:
        return None
    pts.sort()
    ws = [p[0] for p in pts]
    la = [p[1] for p in pts]

    base = min(la)
    lt1 = None
    for w, v in zip(ws, la):
        if v >= base + 0.5:
            lt1 = w
            break

    # LT2: maxima segunda derivada (curvatura) da curva de lactato
    lt2 = None
    if len(ws) >= 5:
        curv = []
        for k in range(1, len(ws) - 1):
            h1, h2 = ws[k] - ws[k - 1], ws[k + 1] - ws[k]
            if h1 <= 0 or h2 <= 0:
                continue
            d2 = 2 * (la[k - 1] / (h1 * (h1 + h2))
                      - la[k] / (h1 * h2)
                      + la[k + 1] / (h2 * (h1 + h2)))
            curv.append((d2, ws[k], la[k]))
        if curv:
            _, lt2, _ = max(curv, key=lambda c: c[0])

    def _la_em(w):
        if w is None:
            return None
        k = min(range(len(ws)), key=lambda i: abs(ws[i] - w))
        return round(la[k], 2)

    return {
        "lt1_w": round(lt1) if lt1 else None,
        "lt1_lactato": _la_em(lt1),
        "lt2_w": round(lt2) if lt2 else None,
        "lt2_lactato": _la_em(lt2),
        "mlss_w": mlss_w,
        "lt2_vs_mlss_w": (round(lt2 - mlss_w) if (lt2 and mlss_w) else None),
        "nota": ("LT2 pela maxima curvatura, nao pelo 4 mmol/L fixo. "
                 "LT2 e MLSS devem ficar proximos; grande divergencia indica "
                 "que o modelo nao descreve bem este atleta."),
    }
