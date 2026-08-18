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

    return {
        "status": "ok",
        "modalidade": modalidade,
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


def melhores_mmp(linhas, modalidade, pmax_max=None):
    """Melhor MMP de cada duracao necessaria, e o pico de 1 s.

    linhas: [{'date':..., 'secs':..., 'watts':...}] ja filtradas pela season
    e pela modalidade. Devolve tambem a DATA de cada maximo, para se ver se
    os valores sao contemporaneos -- um Pmax de ha dois anos com um MMP12
    recente produz um VLamax sem significado.
    """
    duracoes = DURACOES_MMP.get(modalidade, [])
    melhor = {d: (None, None) for d in duracoes}
    pico1s = (None, None)
    tecto = pmax_max or PMAX_PLAUSIVEL.get(modalidade, 2000)

    for l in linhas:
        secs, watts = _descomprimir(l.get('secs'), l.get('watts'))
        if not secs or not watts or len(secs) != len(watts):
            continue
        data = str(l.get('date'))[:10] if l.get('date') else None
        indice = {int(s): w for s, w in zip(secs, watts) if w is not None}

        for d in duracoes:
            w = indice.get(d)
            if w is None:                      # duracao exacta em falta
                proximos = [k for k in indice if abs(k - d) <= max(5, d * 0.05)]
                w = max((indice[k] for k in proximos), default=None)
            if w is not None and (melhor[d][0] is None or w > melhor[d][0]):
                melhor[d] = (float(w), data)

        w1 = indice.get(1)
        if w1 is not None and w1 <= tecto and (pico1s[0] is None or w1 > pico1s[0]):
            pico1s = (float(w1), data)

    return {"mmp": {d: melhor[d][0] for d in duracoes},
            "datas": {d: melhor[d][1] for d in duracoes},
            "pmax_w": pico1s[0], "pmax_data": pico1s[1],
            "pmax_tecto_aplicado": tecto}
