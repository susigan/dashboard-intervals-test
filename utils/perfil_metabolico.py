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
#
# Sao duas grandezas com o mesmo nome e magnitudes muito diferentes:
#   VOL_REL      = 0.45  -- fraccao de massa muscular activa, na curva de Mader
#   vol_rel_vlamax ~ 2-3 -- carga relativa (W/kg x fator), so' na formula do
#                           VLamax. Um valor na casa dos 2.4 e' o esperado,
#                           nao um erro. Confirmado contra o tab_cp_model.py
#                           do repo Streamlit, que usa exactamente
#                           _mb_volrel_vla = (MMP3 + MMP5) / 3 / peso.

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

    # Potencia aerobia maxima (MAP / Pvo2max): a potencia que corresponde a
    # consumir o VO2max, sem o acrescimo glicolitico. E' esta que se compara
    # com o campo Pvo2max da Intervals.icu e que serve de denominador a
    # utilizacao fraccional (MLSS como % do MAP).
    w_vo2max = vo2max * peso / WATT_O2

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
        "pvo2max_w": round(w_vo2max),
        "fractional_utilization_pct": (round(w_at / w_vo2max * 100, 1)
                                       if w_vo2max else None),
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
                                mader.get("mlss_at_w") if mader else None,
                                mader.get("fatmax_w") if mader else None)

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


MODOS_CONJUNTO = ('recuo', 'season', 'coerente')


def melhores_mmp(linhas, modalidade, pmax_max=None, season_activa=None,
                 limiar_max=None, modo='coerente', duracoes=None):
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

    Ha tres modos, porque nenhum serve sempre:

      'season'    so' a season activa. Deixa duracoes por preencher quando
                  nao houve esforco maximo, mas o que der descreve um
                  momento real.
      'coerente'  (por omissao) todas as duracoes da MESMA season -- a
                  activa se tiver o conjunto completo, senao a mais
                  recente que tenha. Descreve um atleta que existiu.
      'recuo'     cada duracao recua por si. Preenche sempre tudo, mas
                  pode montar uma curva com pedacos de anos diferentes.

    O 'recuo' era o comportamento unico e mostrou-se perigoso: no Row deste
    atleta produziu um conjunto com datas de Outubro de 2023 a Janeiro de
    2026 -- 831 dias -- porque a season activa tinha esforcos mais fracos
    do que os de 2024. O criterio dos 85% confundiu "nao houve esforco
    maximo nesta season" com "o atleta esta menos forte do que ja' esteve",
    que sao coisas diferentes e pedem respostas diferentes.
    """
    # 'duracoes' permite pedir outro conjunto sem tocar no DURACOES_MMP.
    # Antes o cp_model alterava esse dicionario global durante o pedido e
    # repunha-o no fim; como o perfil metabolico le o mesmo dicionario e as
    # duas rotas correm no mesmo processo, o perfil apanhava as duracoes do
    # CP a meio -- era por isso que o Bike aparecia com o valor de 60 s em
    # vez do de 3 min. Estado global mutavel por pedido nao e' seguro aqui.
    duracoes = sorted(duracoes) if duracoes else DURACOES_MMP.get(modalidade, [])
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

    # conjunto completo de cada season, para se poder escolher um bloco
    # inteiro em vez de remendar duracao a duracao
    conjuntos = {}
    for sea in ordem:
        conjuntos[sea] = {
            'valores': {str(d): (round(por_season[d][sea][0], 1)
                                 if por_season[d].get(sea) else None)
                        for d in duracoes},
            'datas': {str(d): (por_season[d][sea][1]
                               if por_season[d].get(sea) else None)
                      for d in duracoes},
            'n_duracoes': sum(1 for d in duracoes if por_season[d].get(sea)),
        }

    modo = modo if modo in MODOS_CONJUNTO else 'coerente'
    season_do_conjunto = None
    if modo == 'coerente':
        # a activa se tiver tudo; senao a mais recente com o conjunto
        # completo; senao a que tiver mais duracoes
        completas = [s2 for s2 in ordem
                     if conjuntos[s2]['n_duracoes'] == len(duracoes)]
        if season_activa and conjuntos.get(season_activa, {}).get('n_duracoes') \
                == len(duracoes):
            season_do_conjunto = season_activa
        elif completas:
            season_do_conjunto = completas[0]
        elif ordem:
            season_do_conjunto = max(
                ordem, key=lambda s2: (conjuntos[s2]['n_duracoes'],
                                       -ordem.index(s2)))

    mmp, datas, seasons, recuou = {}, {}, {}, {}
    qualidade = {}
    for d in duracoes:
        if modo == 'season':
            v = por_season[d].get(season_activa)
            w, dt, sea, fb = (v[0], v[1], season_activa, False) if v \
                else (None, None, None, False)
            rc = None
            hist = max((x[0] for x in por_season[d].values()), default=None)
            if v and hist:
                rc = v[0] / hist
        elif modo == 'coerente':
            v = por_season[d].get(season_do_conjunto)
            w, dt, sea = (v[0], v[1], season_do_conjunto) if v else (None, None, None)
            fb = bool(season_activa and season_do_conjunto != season_activa and v)
            hist = max((x[0] for x in por_season[d].values()), default=None)
            rc = (v[0] / hist) if (v and hist) else None
        else:
            w, dt, sea, fb, rc, hist = _escolher(por_season[d])
        mmp[d], datas[d], seasons[d], recuou[d] = w, dt, sea, fb
        qualidade[d] = {'racio_na_season': round(rc, 3) if rc is not None else None,
                        'melhor_historico_w': round(hist) if hist else None,
                        'melhor_na_season_w': (round(por_season[d][season_activa][0])
                                               if por_season[d].get(season_activa)
                                               else None)}

    if modo in ('season', 'coerente'):
        alvo = season_activa if modo == 'season' else season_do_conjunto
        v = pico_por_season.get(alvo)
        pw, pdt, psea = (v[0], v[1], alvo) if v else (None, None, None)
        pfb = bool(v and season_activa and alvo != season_activa)
        _h = max((x[0] for x in pico_por_season.values()), default=None)
        prc = (v[0] / _h) if (v and _h) else None
    else:
        pw, pdt, psea, pfb, prc, phist = _escolher(pico_por_season)

    # ── coerencia da curva ────────────────────────────────────────────────
    # Escolher cada duracao de forma independente entre seasons pode produzir
    # um MMP5 acima do MMP3 -- o que nenhum atleta faz. No repo Streamlit
    # (tab_cp_model.py) o problema nao existe porque o valor vem de uma
    # coluna unica com o PR corrente, coerente por construcao; aqui, como se
    # recua duracao a duracao, ha que impor a monotonia explicitamente.
    # Percorre-se da duracao mais curta para a mais longa e, sempre que uma
    # excede a anterior, re-escolhe-se o melhor valor que ja' nao a exceda,
    # dando prioridade a season activa.
    ajustado = {d: False for d in duracoes}
    ordenadas = sorted(duracoes)
    for k in range(1, len(ordenadas)):
        d, ant = ordenadas[k], ordenadas[k - 1]
        if mmp[d] is None or mmp[ant] is None or mmp[d] <= mmp[ant]:
            continue
        cands = []
        if season_activa and por_season[d].get(season_activa):
            w0, dt0 = por_season[d][season_activa]
            cands.append((season_activa, w0, dt0))
        for sea in ordem:
            if sea != season_activa and por_season[d].get(sea):
                w0, dt0 = por_season[d][sea]
                cands.append((sea, w0, dt0))
        viaveis = [c for c in cands if c[1] <= mmp[ant]]
        escolha = (max(viaveis, key=lambda c: c[1]) if viaveis
                   else (min(cands, key=lambda c: c[1]) if cands else None))
        if escolha is None:
            continue
        sea, w0, dt0 = escolha
        mmp[d], datas[d], seasons[d] = w0, dt0, sea
        recuou[d] = (season_activa is not None and sea != season_activa)
        ajustado[d] = True

    # o pico de 1 s tambem nao pode ficar abaixo da duracao mais curta
    if pw is not None and ordenadas and mmp.get(ordenadas[0]) is not None \
            and pw < mmp[ordenadas[0]]:
        pw = None
        pdt = psea = None

    return {"mmp": mmp, "datas": datas,
            "seasons": seasons, "recuou": recuou,
            "qualidade": qualidade,
            "ajustado_por_coerencia": ajustado,
            "modo": modo,
            "season_do_conjunto": season_do_conjunto,
            "conjuntos_por_season": conjuntos,
            "dispersao_datas_dias": _dispersao(datas),
            "limiar_esforco_maximo": limiar,
            "pmax_w": pw, "pmax_data": pdt,
            "pmax_season": psea, "pmax_recuou": pfb,
            "pmax_racio_na_season": round(prc, 3) if prc is not None else None,
            "pmax_tecto_aplicado": tecto,
            "seasons_disponiveis": ordem,
            "curvas_lidas": lidas, "curvas_ignoradas": ignoradas}


# ── campos externos da Intervals.icu ─────────────────────────────────────
# Estimativas das MESMAS grandezas que o modelo calcula, mas por outro
# caminho (custom fields e scripts do atleta). Servem de controlo externo:
# o modelo vem de potencias maximas, estes vem dos streams da sessao. Se
# divergirem sistematicamente, e' o modelo que nao descreve o atleta.
#
# 'compara_com' aponta para a chave equivalente no resultado de calcular().
# None = nao ha equivalente no modelo, e' informacao complementar.
# Nomes reais dos custom fields deste atleta, apanhados pelo
# campos_por_reconhecer do endpoint. Nao se inventam nomes: o que nao
# estiver aqui aparece nessa lista e acrescenta-se depois.
#
# 'grupo'       agrupa por aquilo que a grandeza estima, para se poder ver
#               se estimativas independentes do mesmo limiar concordam
# 'eixo'        W | wkg | bpm | None -- serve para converter tudo para
#               watts e comparar num so' grafico
# 'compara_com' chave equivalente no resultado de calcular()
# So' os campos que estimam limiares. Ficaram deliberadamente de fora:
#   PCr, ss_p_max, ss_cp, Pmax   -- valores degenerados neste atleta (p50 = 0)
#   icu_pm_ftp, Cp, EFTP,
#   ThresholdPower               -- FTP configurada ou derivada, nao um
#                                   limiar medido. O CP passa a vir da tab
#                                   CP-Model, com SE% e escolha de modelo
#   Peak5m                       -- pico de 5 min da sessao, nao uma
#                                   estimativa de potencia a VO2max
#   Smo2, RespirationRateAvg,
#   MeanRRA1, HRVREC, RecoveryHR,
#   CardiacDrift, CompoundScore  -- sinais de sessao, nao limiares
#   Cp1min..Cp20min, Wprime,
#   MaxPwr, Best1minpower        -- curva de potencia, pertence a tab CP-Model
#
# 'grupo'       agrupa por aquilo que a grandeza estima, para se ver se
#               estimativas independentes do mesmo limiar concordam
# 'eixo'        W | wkg | bpm | None -- para converter tudo para watts
# ── FONTE de cada campo: modelo ou medicao ───────────────────────────────
#
# O autor dos campos da Intervals.icu confirmou-o directamente:
#
#   "AeT is based on the POWER CURVE, along with some additional
#    calculations. (...) It's an ESTIMATE that can be verified in
#    the field."
#
# Isso muda o que a tabela de validacao externa significa. O nosso modelo
# de Mader parte dos MMP; a curva de potencia sao os mesmos MMP. Comparar
# os dois nao valida nada:
#
#   - se concordarem, so' diz que duas formulas alimentadas pelos MESMOS
#     dados chegam ao mesmo sitio
#   - se discordarem, so' diz que as formulas sao diferentes
#
# Validacao externa a serio exige uma fonte INDEPENDENTE. Nos campos
# disponiveis, so' a familia HRV (que vem dos streams de DFA-a1) e o
# LTHRdetected (que vem do stream de FC) cumprem isso.
#
# Nao se apagam os campos de modelo -- continuam uteis como segunda
# opiniao. Mas deixam de contar para o consenso, porque um consenso entre
# duas versoes do mesmo calculo e' um consenso consigo proprio.
FONTE_DO_CAMPO = {
    # da curva de potencia -- MESMA origem que o nosso modelo
    'aet': 'modelo', 'aetwkg': 'modelo', 'mss': 'modelo', 'pbp': 'modelo',
    'pvo2max': 'modelo', 'fractionalutilizationusing6mpower': 'modelo',
    'ebp': 'modelo',
    # definicao do perfil, nao medicao (varia 3 bpm em 244 sessoes)
    'aethr': 'perfil',
    # dos streams -- INDEPENDENTES do modelo
    'hrvt1': 'medido', 'hrvt1plus': 'medido', 'hrvt2': 'medido',
    'hrvtmss': 'medido', 'lthrdetected': 'medido',
    'a1_inflexao_w': 'medido', 'a1_inflexao_bpm': 'medido',
    # limiares opticos: sensor no musculo, independentes do modelo
    'bp1_smo2': 'medido', 'bp2_smo2': 'medido',
    'lt1_reox': 'medido', 'mlss_dessat': 'medido',
}

EXPLICA_FONTE = {
    'modelo': ('vem da curva de potência — a mesma origem que o nosso '
               'modelo. Serve de segunda opinião, não de validação'),
    'perfil': 'é uma definição do perfil, não uma medição',
    'medido': 'vem dos streams da sessão — independente do modelo',
}


def fonte_do_campo(chave):
    return FONTE_DO_CAMPO.get(str(chave).lower(), 'desconhecida')


# 'compara_com' chave equivalente no resultado de calcular()
CAMPOS_EXTERNOS = [
    # ── limiar aerobio (LT1 / VT1) ───────────────────────────────────────
    {"chave": "Aet", "unidade": "W", "eixo": "W", "grupo": "aerobio",
     "compara_com": "lt1_w", "aliases": ["aet", "aet_w", "aerobicthreshold"],
     "descricao": "Limiar aerobio em potencia"},
    {"chave": "AeTwkg", "unidade": "W/kg", "eixo": "wkg", "grupo": "aerobio",
     "compara_com": "lt1_w", "aliases": ["aetwkg", "aet_wkg", "aetwattskg"],
     "descricao": "Limiar aerobio em watts por quilo"},
    {"chave": "AeTHR", "unidade": "bpm", "eixo": "bpm", "grupo": "aerobio",
     "compara_com": "lt1_w", "aliases": ["aethr", "aethr_bpm"],
     "descricao": "Frequencia cardiaca no limiar aerobio"},
    {"chave": "HRVT1", "unidade": "bpm", "eixo": "bpm", "grupo": "aerobio",
     "compara_com": "lt1_w", "aliases": ["hrvt1", "hrvt1_bpm"],
     "descricao": "Primeiro limiar por DFA-a1 (a1 = 0.75)"},
    {"chave": "HRVT1PLUS", "unidade": "bpm", "eixo": "bpm", "grupo": "aerobio",
     "compara_com": "lt1_w",
     # nao por "hrvt1+": a normalizacao remove o '+' e ficaria igual a
     # "hrvt1", roubando o campo HRVT1 no indice de aliases
     "aliases": ["hrvt1plus", "hrvt1_plus", "hrvt1mais"],
     "descricao": "HRVT1 no limite superior da banda de DFA-a1"},

    # ── limiar / MLSS (LT2 / VT2) ────────────────────────────────────────
    {"chave": "MSS", "unidade": "W", "eixo": "W", "grupo": "limiar",
     "compara_com": "mlss_at_w", "aliases": ["mss", "maximalsteadystate"],
     "descricao": "Maximal steady state abaixo do eFTP (controlmetrics.es)"},
    {"chave": "HRVTMSS", "unidade": "bpm", "eixo": "bpm", "grupo": "limiar",
     "compara_com": "mlss_at_w", "aliases": ["hrvtmss", "hrvt_mss"],
     "descricao": "FC do maximal steady state por DFA-a1"},
    {"chave": "PBP", "unidade": "W", "eixo": "W", "grupo": "limiar",
     "compara_com": "lt2_w", "aliases": ["pbp", "pbp_w", "poweratbreakingpoint"],
     "descricao": "Power at Breaking Point"},
    {"chave": "EBP", "unidade": "W", "eixo": "W", "grupo": "limiar",
     "compara_com": "lt2_w",
     "aliases": ["ebp", "estimatedbreakingpoint", "breakingpoint"],
     "descricao": "Estimated Power at Breaking Point (entre LT1 e LT2)"},
    {"chave": "HRVT2", "unidade": "bpm", "eixo": "bpm", "grupo": "limiar",
     "compara_com": "lt2_w", "aliases": ["hrvt2", "hrvt2_bpm"],
     "descricao": "Segundo limiar por DFA-a1 (a1 = 0.50)"},
    {"chave": "LTHRdetected", "unidade": "bpm", "eixo": "bpm", "grupo": "limiar",
     "compara_com": "lt2_w", "aliases": ["lthrdetected", "lthr_detected", "lthr"],
     "descricao": "LTHR detectada pela Intervals.icu"},

    # ── DFA-a1 individualizado ───────────────────────────────────────────
    # Nao sao campos da Intervals.icu: sao calculados dos streams pelo
    # /api/hrv/aquecimento_inflexao e injectados aqui. Aparecem na tabela
    # ao lado dos outros para se comparar o limiar que sai das escadas de
    # aquecimento com o que sai dos scripts.
    {"chave": "a1_inflexao_W", "unidade": "W", "eixo": "W", "grupo": "aerobio",
     "compara_com": "lt1_w", "aliases": ["a1inflexaow", "a1_inflexao_w"],
     "descricao": "Potencia no primeiro ponto de inflexao do DFA-a1, "
                  "mediana das escadas de aquecimento deste atleta"},
    {"chave": "a1_inflexao_bpm", "unidade": "bpm", "eixo": "bpm",
     "grupo": "aerobio", "compara_com": "lt1_w",
     "aliases": ["a1inflexaobpm", "a1_inflexao_bpm"],
     "descricao": "FC no primeiro ponto de inflexao do DFA-a1, mediana das "
                  "escadas de aquecimento"},
    {"chave": "a1_no_ponto", "unidade": "—", "eixo": None, "grupo": "aerobio",
     "compara_com": None, "aliases": ["a1noponto", "a1_no_ponto"],
     "descricao": "Valor de DFA-a1 na inflexao, medido neste atleta. E a "
                  "alternativa individualizada ao 0.75 da literatura: se for "
                  "repetivel entre aquecimentos, e o alvo dele e nao o de "
                  "toda a gente"},

    # ── VO2max ───────────────────────────────────────────────────────────
    {"chave": "Pvo2max", "unidade": "W", "eixo": "W", "grupo": "vo2max",
     "compara_com": "pvo2max_w", "aliases": ["pvo2max", "pvo2max_w", "pvo2"],
     "descricao": "Estimated Power at VO2max"},
    {"chave": "FractionalUtilizationusing6mPower", "unidade": "%", "eixo": None,
     "grupo": "vo2max", "compara_com": "fractional_utilization_pct",
     "aliases": ["fractionalutilizationusing6mpower",
                 "fractionalutilization6minpower", "fu6min",
                 "fractionalutilisationusing6mpower", "fractionalutilization"],
     "descricao": "FTP como % da potencia de 6 min de 42 dias (proxy de "
                  "VO2max). Muda pouco por construcao -- tendencia, nao "
                  "valor pontual. 75-85% e' o intervalo habitual; abaixo de "
                  "75% trabalhar limiar, acima de 85% trabalhar VO2max"},
]

# Que valor do modelo serve de referencia a cada grupo
REFERENCIA_DO_GRUPO = {
    "aerobio": "lt1_w",
    "limiar":  "mlss_at_w",
    "vo2max":  "pvo2max_w",
}

ORDEM_GRUPOS = ["aerobio", "limiar", "vo2max"]

ROTULO_GRUPO = {
    "aerobio": "Limiar aerobio (LT1 / VT1)",
    "limiar":  "Limiar / MLSS (LT2 / VT2)",
    "vo2max":  "VO2max",
}


def _normaliza(nome):
    return ''.join(c for c in str(nome).lower() if c.isalnum())


def mapear_campos_externos(nomes_presentes, definicoes=None):
    """Nome real no JSON -> definicao, sem duplicar a mesma definicao.

    Cada atleta escreve o custom field como quer ('Fractional Utilization',
    'FractionalUtilization', 'fractional_utilization'), por isso compara-se
    sem maiusculas, espacos nem underscores. Mas se dois nomes diferentes
    caem na mesma definicao -- por exemplo um custom field 'Pcr' e o campo
    standard 'ss_p_max' -- so um pode ganhar, senao a mesma grandeza aparece
    duas vezes na tabela como se fossem medidas independentes. Ganha o que
    corresponde a chave exacta; os outros ficam registados como duplicados,
    a vista, para se decidir o que fazer com eles.
    """
    defs = definicoes if definicoes is not None else CAMPOS_EXTERNOS
    idx = {}
    for definicao in defs:
        for a in [definicao["chave"]] + definicao["aliases"]:
            idx[_normaliza(a)] = definicao

    candidatos = {}
    for nome in nomes_presentes:
        definicao = idx.get(_normaliza(nome))
        if definicao is None:
            continue
        candidatos.setdefault(definicao["chave"], []).append(nome)

    encontrados, duplicados = {}, {}
    for chave, nomes in candidatos.items():
        definicao = next(d for d in defs if d["chave"] == chave)
        exacto = next((n for n in nomes if _normaliza(n) == _normaliza(chave)),
                      None)
        vencedor = exacto or sorted(nomes)[0]
        encontrados[vencedor] = definicao
        resto = [n for n in nomes if n != vencedor]
        if resto:
            duplicados[chave] = resto
    return encontrados, duplicados


HR_PLAUSIVEL = (60.0, 220.0)     # bpm num patamar de esforco
W_PLAUSIVEL = (20.0, 2000.0)     # W num patamar de esforco

# r2 minimo para a recta HR<->Watts poder ser usada em conversoes.
#
# Abaixo disto a recta nao tem informacao suficiente e converter amplifica
# ruido. No Ski deste atleta o r2 e' 0.079 -- a potencia explica 8% da
# variacao da FC -- e a conversao transformava 11 bpm de diferenca entre o
# HRVT1 e o HRVT1PLUS em 80 W. O intervalo do LT1 aparecia como 103-218 W
# com 86.6% de amplitude, quando os dois campos medidos em watts concordavam
# a 1% entre si. A dispersao era toda fabricada pela conversao.
#
# Sem recta fiavel, cada campo fica no eixo em que foi medido. Perde-se a
# comparacao directa entre watts e bpm, que e' o correcto: ela nao existia.
R2_MINIMO_CONVERSAO = 0.50


def regressao_hr_watts(pontos):
    """Recta HR = a x Watts + b a partir dos pares do proprio atleta.

    Serve para pousar num mesmo grafico as grandezas em watts e as em bpm:
    sem uma relacao medida, comparar EBP (W) com HRVT2 (bpm) e' impossivel.
    A recta e' do atleta, nao de tabela -- e devolve-se o r2 e o n para se
    ver se e' de confianca antes de se olhar para as conversoes.

    Limite conhecido: a relacao potencia-FC nao e' linear em todo o
    dominio (achata perto do maximo) nem estavel ao longo do ano (deriva
    com a forma e com o calor). Serve para situar pontos proximos uns dos
    outros, nao para converter extremos.
    """
    # Filtro de plausibilidade: aparecem patamares com FC de 7 ou 41 bpm
    # (sensor a falhar no arranque do intervalo). Um punhado destes puxa a
    # recta em todo o dominio e estraga todas as conversoes.
    pts = [(float(w), float(h)) for w, h in pontos
           if w is not None and h is not None
           and HR_PLAUSIVEL[0] <= float(h) <= HR_PLAUSIVEL[1]
           and W_PLAUSIVEL[0] <= float(w) <= W_PLAUSIVEL[1]]
    descartados = len([1 for w, h in pontos
                       if w is not None and h is not None]) - len(pts)
    n = len(pts)
    if n < 8:
        return {"n": n, "suficiente": False,
                "nota": "menos de 8 pares validos; sem recta"}
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    if sxx <= 0:
        return {"n": n, "suficiente": False, "nota": "sem variacao em watts"}
    a = sxy / sxx
    b = my - a * mx
    syy = sum((p[1] - my) ** 2 for p in pts)
    r2 = (sxy ** 2 / (sxx * syy)) if syy > 0 else 0.0
    return {"n": n, "suficiente": True, "descartados": descartados,
            "fiavel": r2 >= R2_MINIMO_CONVERSAO,
            "r2_minimo": R2_MINIMO_CONVERSAO,
            "declive_bpm_por_w": round(a, 4),
            "intercepto_bpm": round(b, 1),
            "r2": round(r2, 3),
            "watts_min": round(min(p[0] for p in pts)),
            "watts_max": round(max(p[0] for p in pts)),
            "pontos": [{"w": round(w, 1), "hr": round(h, 1)} for w, h in pts]}


def regressao_pace_watts(pontos):
    """Recta velocidade = a x Watts + b, dos pares do proprio atleta.

    Para a corrida, watts sozinhos nao dizem nada a quem treina: o que se
    reconhece e' o pace. A conversao nao pode vir de formula generica --
    depende da economia de corrida de cada um, que e' precisamente o que
    varia entre atletas. Por isso ajusta-se aos pares (potencia media,
    velocidade media) das proprias sessoes.

    'pontos' e' [(watts, metros, segundos)]. Devolve-se sempre o r2 e o n:
    se a recta for fraca, o pace mostrado ao lado dos watts e uma
    ilustracao, nao uma prescricao.
    """
    pts = []
    for w, m, seg in pontos:
        if not w or not m or not seg or seg <= 0:
            continue
        v = float(m) / float(seg)          # m/s
        if not (1.0 <= v <= 8.0):          # 16:40 a 2:05 por km
            continue
        if not (50.0 <= float(w) <= 800.0):
            continue
        pts.append((float(w), v))
    n = len(pts)
    if n < 8:
        return {"n": n, "suficiente": False,
                "nota": "menos de 8 sessoes com potencia e distancia"}
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx <= 0:
        return {"n": n, "suficiente": False, "nota": "sem variacao em watts"}
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    a = sxy / sxx
    b = my - a * mx
    syy = sum((p[1] - my) ** 2 for p in pts)
    r2 = (sxy ** 2 / (sxx * syy)) if syy > 0 else 0.0
    return {"n": n, "suficiente": True,
            "declive_ms_por_w": round(a, 6),
            "intercepto_ms": round(b, 4),
            "r2": round(r2, 3),
            "watts_min": round(min(p[0] for p in pts)),
            "watts_max": round(max(p[0] for p in pts)),
            "pontos": [{"w": round(w, 1), "v": round(v, 3)} for w, v in pts]}


def pace_de_watts(rel, watts):
    """-> segundos por km, ou None."""
    if not rel or not rel.get("suficiente") or not watts:
        return None
    v = rel["declive_ms_por_w"] * watts + rel["intercepto_ms"]
    if v <= 0.3:
        return None
    return round(1000.0 / v, 1)


def formatar_pace(seg_km):
    if seg_km is None:
        return None
    m = int(seg_km // 60)
    return f"{m}:{int(round(seg_km - m * 60)):02d}/km"


def hr_de_watts(rel, watts):
    """None quando a recta nao e' fiavel: melhor nao converter do que
    converter mal e apresentar o resultado como se fosse uma medicao."""
    if not rel or not rel.get("suficiente") or not rel.get("fiavel") \
            or watts is None:
        return None
    return round(rel["declive_bpm_por_w"] * watts + rel["intercepto_bpm"], 1)


def watts_de_hr(rel, hr):
    if not rel or not rel.get("suficiente") or not rel.get("fiavel") \
            or hr is None:
        return None
    a = rel["declive_bpm_por_w"]
    if not a:
        return None
    return round((hr - rel["intercepto_bpm"]) / a, 1)


def quartis(valores):
    """p25/p50/p75 sem numpy, por interpolacao linear (metodo 7 do R)."""
    vs = sorted(v for v in valores if v is not None)
    if not vs:
        return None
    def _p(q):
        if len(vs) == 1:
            return vs[0]
        pos = q * (len(vs) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(vs) - 1)
        return vs[lo] + (vs[hi] - vs[lo]) * (pos - lo)
    return {"n": len(vs),
            "min": round(vs[0], 2), "p25": round(_p(0.25), 2),
            "p50": round(_p(0.50), 2), "p75": round(_p(0.75), 2),
            "max": round(vs[-1], 2)}


def e_constante(q):
    """O campo muda de sessao para sessao ou e' uma definicao fixa?

    O AeTHR, por exemplo, vem igual em todas as sessoes e em todas as
    seasons: e' um valor que o atleta configurou no perfil, nao uma
    medicao. Confundir os dois leva a tratar uma definicao como se fosse
    evidencia independente -- que e' o contrario do que esta tabela serve
    para fazer.
    """
    if not q or q.get("min") is None or q.get("max") is None:
        return None
    if q["max"] == q["min"]:
        return True
    escala = abs(q["p50"]) or 1
    return (q["max"] - q["min"]) / escala < 0.01


# Abaixo deste n uma estimativa nao entra no consenso. Nao e' um numero
# magico: e' o ponto em que uma mediana deixa de ser mediana e passa a ser
# duas ou tres observacoes. As estimativas fracas continuam visiveis na
# tabela, assinaladas.
N_MINIMO_CONSENSO = 10

# Salto relativo acima do qual se assume que os estimadores nao medem o
# mesmo ponto, mas os dois extremos da mesma transicao.
#
# A razao vem dos proprios dados. No Bike, em watts: 104.5, 105.5, 160,
# 164.3, 176.2. O salto de 105.5 para 160 e' de 52%; os restantes sao de
# 1% a 7%. Tratar isto como um consenso unico com um "discrepante" e'
# forcar uma media entre duas coisas diferentes. Em bpm o proprio nome dos
# campos diz o mesmo -- HRVT1 e HRVT1PLUS sao o inicio e o fim da mesma
# banda, nao duas tentativas de medir o mesmo ponto.
#
# Um limiar nao e' um numero, e' uma transicao com largura. Os metodos
# discordam porque uns marcam onde ela comeca e outros onde acaba.
SALTO_TRANSICAO = 0.20


def separar_transicao(valores, salto=SALTO_TRANSICAO):
    """Parte a lista no maior salto relativo, se ele for grande demais.

    Devolve (inicio, fim) ou (todos, None) quando nao ha salto que
    justifique separar.
    """
    vs = sorted(valores, key=lambda t: t[1])
    if len(vs) < 2:
        return vs, None
    maior, k = 0.0, None
    for i in range(1, len(vs)):
        a, b = vs[i - 1][1], vs[i][1]
        if a <= 0:
            continue
        rel = (b - a) / a
        if rel > maior:
            maior, k = rel, i
    if k is None or maior < salto:
        return vs, None
    return vs[:k], vs[k:]


def coerencia_por_grupo(campos, modelo):
    """Estimativas independentes do mesmo limiar concordam entre si?

    Separado POR UNIDADE. Juntar campos medidos em watts com campos medidos
    em bpm convertidos para watts mistura medicoes com estimativas de
    segunda mao, e a dispersao que aparece e' a da recta de conversao, nao
    a dos metodos. Se a recta nao for fiavel a conversao nem sequer existe,
    e cada bloco fica com o que foi medido nas suas proprias unidades.

    Campos constantes ficam de fora: uma definicao do perfil nao e' uma
    estimativa independente.
    """
    out = {}
    for grupo, ref in REFERENCIA_DO_GRUPO.items():
        membros = [c for c in campos
                   if c.get("grupo") == grupo and not c.get("constante")]
        if not membros:
            continue
        bloco = {"rotulo": ROTULO_GRUPO.get(grupo, grupo),
                 "referencia_do_modelo": ref,
                 "modelo_w": modelo.get(ref)}

        for unidade, chave, campo_valor in (
                ("W", "em_watts", "watts_medido"),
                ("bpm", "em_bpm", "hr_medido")):
            # a FONTE viaja com o valor. Estava a ser procurada depois,
            # com um next() sobre 'campos' -- que nesse ponto ja' e' outra
            # lista, e por isso devolvia sempre 'desconhecida'.
            vals = [(c["rotulo"], c[campo_valor],
                     c.get("fonte") or fonte_do_campo(c.get("chave")))
                    for c in membros if c.get(campo_valor) is not None]
            if len(vals) < 1:
                continue
            ws = sorted(v for _n, v, _f in vals)
            mediana = quartis(ws)["p50"]
            bloco[chave] = {
                "unidade": unidade,
                "n_estimativas": len(ws),
                "min": round(min(ws), 1), "max": round(max(ws), 1),
                "mediana": round(mediana, 1),
                "amplitude": round(max(ws) - min(ws), 1),
                "amplitude_pct": (round((max(ws) - min(ws)) / mediana * 100, 1)
                                  if mediana else None),
                # a fonte vai no detalhe para a tabela poder marcar quais
                # estimativas contam e quais sao so' segunda opiniao
                "detalhe": sorted(
                    ({"campo": n, "valor": round(v, 1), "fonte": f}
                     for n, v, f in vals),
                    key=lambda d: d["valor"]),
            }
        # ── consenso ────────────────────────────────────────────────
        # A mediana de todos os metodos que medem o mesmo limiar na mesma
        # unidade. Metodos com poucas sessoes entram assinalados mas nao
        # pesam: uma estimativa de 2 aquecimentos nao pode contar o mesmo
        # que uma de 350 sessoes. E quando um metodo se afasta mais de 20%
        # da mediana dos outros, e' marcado como discrepante em vez de ser
        # silenciosamente absorvido.
        for chave, campo_valor in (("em_watts", "watts_medido"),
                                   ("em_bpm", "hr_medido")):
            b = bloco.get(chave)
            if not b:
                continue
            membros = [c for c in campos
                       if c.get("grupo") == grupo and not c.get("constante")
                       and c.get(campo_valor) is not None]

            # Campos que vem da CURVA DE POTENCIA saem do consenso.
            #
            # O autor confirmou que o AeT -- e por extensao o MSS, o PBP e
            # o Pvo2max -- e' calculado da curva de potencia. O nosso
            # modelo de Mader parte dos MMP, que sao a mesma coisa. Incluir
            # esses campos no consenso e' fazer o modelo concordar consigo
            # proprio por outra via, e chamar a isso validacao.
            #
            # Ficam na tabela, marcados, porque sao uma segunda opiniao
            # util -- mas nao pesam no numero que ancora as zonas.
            for c in membros:
                c["fonte"] = fonte_do_campo(c.get("chave"))
            independentes = [c for c in membros if c.get("fonte") == "medido"]
            do_modelo = [c for c in membros if c.get("fonte") == "modelo"]

            candidatos = independentes or membros
            solidos = [c for c in candidatos
                       if (c.get("quartis") or {}).get("n", 0)
                       >= N_MINIMO_CONSENSO]
            base = solidos or candidatos
            pares = [(c["rotulo"], c[campo_valor]) for c in base]
            ini, fim = separar_transicao(pares)

            def _bloco(grupo_pares):
                if not grupo_pares:
                    return None
                vals = [v for _n, v in grupo_pares]
                m = quartis(vals)["p50"]
                return {"valor": round(m, 1),
                        "n_metodos": len(grupo_pares),
                        "metodos": [n for n, _v in grupo_pares],
                        "intervalo": [round(min(vals), 1), round(max(vals), 1)]}

            b["consenso"] = {
                **(_bloco(ini + (fim or [])) or {}),
                "n_metodos_fracos_excluidos": len(candidatos) - len(base),
                "usou_fracos": not solidos and bool(candidatos),
                "so_independentes": bool(independentes),
                "n_do_modelo_excluidos": len(do_modelo),
                "metodos_do_modelo": [c["rotulo"] for c in do_modelo],
                "porque_excluidos": (
                    (", ".join(c["rotulo"] for c in do_modelo)
                     + " vêm da curva de potência, a mesma origem que o "
                     "nosso modelo. Contá-los no consenso seria o modelo a "
                     "concordar consigo próprio. Ficam na tabela como "
                     "segunda opinião")
                    if do_modelo and independentes else
                    ("não há nenhum campo independente do modelo neste "
                     "limiar: o consenso usa os campos da curva de potência "
                     "por falta de alternativa, e não confirma nada"
                     if do_modelo and not independentes else None)),
            }
            if fim:
                # Nao ha um valor: ha uma transicao com principio e fim.
                b["transicao"] = {
                    "inicio": _bloco(ini),
                    "fim": _bloco(fim),
                    "largura": round(_bloco(fim)["valor"] - _bloco(ini)["valor"], 1),
                    "largura_pct": round(
                        (_bloco(fim)["valor"] - _bloco(ini)["valor"])
                        / _bloco(ini)["valor"] * 100, 1),
                }
                b["consenso"]["valor"] = None   # nao colapsar numa media

        if bloco.get("em_watts") or bloco.get("em_bpm"):
            out[grupo] = bloco
    return out


def valores_do_modelo(res):
    """Achata o resultado de calcular() nas chaves usadas em 'compara_com'."""
    if not res or res.get("status") != "ok":
        return {}
    lim = res.get("limiares") or {}
    mad = res.get("mader") or {}
    return {
        "lt1_w": lim.get("lt1_w"),
        "lt2_w": lim.get("lt2_w"),
        "mlss_at_w": mad.get("mlss_at_w"),
        "fatmax_w": mad.get("fatmax_w"),
        "pvo2max_w": mad.get("pvo2max_w"),
        "fractional_utilization_pct": mad.get("fractional_utilization_pct"),
        "vo2max": res.get("vo2max"),
        "vlamax": res.get("vlamax"),
    }


# ── limiares de lactato a partir da curva do modelo ──────────────────────

def _dispersao(datas):
    """Dias entre a data mais antiga e a mais recente do conjunto."""
    ds = sorted(v for v in datas.values()
                if v and len(str(v)) >= 10 and str(v)[:4].isdigit())
    if len(ds) < 2:
        return 0
    from datetime import date as _d
    a = _d(*map(int, str(ds[0])[:10].split('-')))
    b = _d(*map(int, str(ds[-1])[:10].split('-')))
    return (b - a).days


def _breakpoint_2seg(xs, ys):
    """Ponto de quebra por dois segmentos de recta unidos (minimos quadrados).

    Ajusta duas rectas que se encontram num no' e devolve o no' que minimiza
    o erro total. E' a mesma familia de metodo que ja' se usa para os
    breakpoints de SmO2 (Feldmann): sai da geometria da propria curva, sem
    constante emprestada de tabela.
    """
    n = len(xs)
    if n < 8:
        return None, None
    melhor, melhor_erro = None, None
    for k in range(3, n - 3):
        erro = 0.0
        for seg_x, seg_y in ((xs[:k + 1], ys[:k + 1]), (xs[k:], ys[k:])):
            m = len(seg_x)
            mx = sum(seg_x) / m
            my = sum(seg_y) / m
            sxx = sum((v - mx) ** 2 for v in seg_x)
            if sxx <= 0:
                continue
            sxy = sum((seg_x[t] - mx) * (seg_y[t] - my) for t in range(m))
            a = sxy / sxx
            b = my - a * mx
            erro += sum((seg_y[t] - (a * seg_x[t] + b)) ** 2 for t in range(m))
        if melhor_erro is None or erro < melhor_erro:
            melhor_erro, melhor = erro, k
    if melhor is None:
        return None, None
    return xs[melhor], melhor_erro


def limiares_lactato(curva, mlss_w=None, fatmax_w=None):
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

    # ── LT1 ──────────────────────────────────────────────────────────────
    # A convencao classica "+0.5 mmol/L acima da linha de base" foi feita
    # para um teste incremental, onde a base e' o lactato medido em repouso
    # ou no aquecimento (tipicamente 0.8-1.2 mmol/L) e as etapas sao poucas.
    # Aqui a curva e' varrida desde VO2 = 0.5, praticamente repouso, com um
    # lactato modelado de ~0.2 mmol/L. Somar 0.5 a esse minimo dispara
    # muito cedo -- foi o que produziu um LT1 de 103 W neste atleta, abaixo
    # do MSS (170 W) e do PBP (197 W), o que nao e' defensavel.
    #
    # Passa a usar-se o ponto de quebra da curva, pelo mesmo criterio
    # geometrico do LT2: onde a subida do lactato deixa de ser plana e
    # comeca a acelerar. Fica restrito a zona abaixo do LT2, senao os dois
    # metodos convergiriam para o mesmo ponto. A convencao antiga continua
    # a ser devolvida em lt1_convencao_w, para comparacao.
    base = min(la)
    lt1_conv = None
    for w, v in zip(ws, la):
        if v >= base + 0.5:
            lt1_conv = w
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

    # LT1 pelo ponto de quebra, abaixo do LT2
    corte = lt2 if lt2 else ws[-1]
    idx = [k for k in range(len(ws)) if ws[k] <= corte]
    lt1, lt1_metodo = None, None
    if len(idx) >= 8:
        lt1, _erro = _breakpoint_2seg([ws[k] for k in idx],
                                      [la[k] for k in idx])
        lt1_metodo = 'ponto de quebra (dois segmentos) abaixo do LT2'
    if lt1 is None:
        lt1, lt1_metodo = lt1_conv, 'convencao +0.5 mmol/L (sem pontos para o ajuste)'

    def _la_em(w):
        if w is None:
            return None
        k = min(range(len(ws)), key=lambda i: abs(ws[i] - w))
        return round(la[k], 2)

    # ── coerencia do LT1 com o FatMax ────────────────────────────────
    # O FatMax tem de ficar ABAIXO do LT1: e' a intensidade de maxima
    # oxidacao de gordura, e essa acontece dentro do dominio moderado.
    #
    # Neste atleta:
    #   LT1 por quebra      174 W  ->  FatMax 143 W fica a 82% dele. OK.
    #   LT1 por convencao   104 W  ->  FatMax 143 W fica a 138%. Impossivel.
    #
    # E' este teste que decide entre os dois metodos, e nao o facto de a
    # convencao concordar melhor com os campos da Intervals.icu. Um LT1
    # abaixo do FatMax nao e' um limiar aerobio -- e' um numero.
    coer_fatmax = None
    if fatmax_w and lt1:
        rel = fatmax_w / lt1
        coer_fatmax = {
            "fatmax_w": round(fatmax_w),
            "fatmax_pct_do_lt1": round(rel * 100),
            "coerente": rel < 1.0,
            "leitura": (
                f"FatMax {round(fatmax_w)} W fica a {round(rel * 100)}% do "
                f"LT1 {round(lt1)} W — abaixo, como tem de ser"
                if rel < 1.0 else
                f"FatMax {round(fatmax_w)} W fica ACIMA do LT1 "
                f"{round(lt1)} W ({round(rel * 100)}%). Isso é impossível: "
                "a máxima oxidação de gordura acontece dentro do domínio "
                "moderado, não acima dele. O LT1 está subestimado"),
        }
        if lt1_conv:
            rel_c = fatmax_w / lt1_conv
            coer_fatmax["convencao"] = {
                "lt1_w": round(lt1_conv),
                "fatmax_pct_do_lt1": round(rel_c * 100),
                "coerente": rel_c < 1.0,
            }

    return {
        "coerencia_fatmax": coer_fatmax,
        "lt1_w": round(lt1) if lt1 else None,
        "lt1_lactato": _la_em(lt1),
        "lt1_metodo": lt1_metodo,
        "lt1_convencao_w": round(lt1_conv) if lt1_conv else None,
        "lt1_convencao_lactato": _la_em(lt1_conv),
        "lt2_w": round(lt2) if lt2 else None,
        "lt2_lactato": _la_em(lt2),
        "mlss_w": mlss_w,
        "lt2_vs_mlss_w": (round(lt2 - mlss_w) if (lt2 and mlss_w) else None),
        "nota": ("LT1 pelo ponto de quebra da curva, nao pelo '+0.5 mmol/L "
                 "acima do minimo' -- essa convencao pressupoe um lactato de "
                 "repouso medido, e num varrimento que comeca perto do zero "
                 "dispara cedo demais. LT2 pela maxima curvatura, nao pelo "
                 "4 mmol/L fixo. LT2 e MLSS devem ficar proximos; grande "
                 "divergencia indica que o modelo nao descreve este atleta."),
    }


# ══════════════════════════════════════════════════════════════════════════
# DIAGNOSTICO PELA FORMA DA CURVA (Gordo Byrn / Hellemans)
#
# A ideia: antes de decidir onde treinar, ler o FORMATO da curva. Cada
# perfil pede uma prescricao diferente, e treinar a zona errada para o
# perfil gera fadiga sem ganho.
#
# A classificacao sai de duas medidas geometricas da curva de lactato que o
# modelo ja' produz -- nenhuma constante nova:
#
#   base_relativa  quanto do dominio de potencia fica abaixo do LT1. Uma
#                  base longa e' o perfil ingreme; curta e' o linear.
#   subida_topo    quao depressa o lactato sobe depois do LT2. Topo que
#                  dispara e' normal/ingreme; topo que nao sobe e' o
#                  achatado, o perfil de fadiga.
# ══════════════════════════════════════════════════════════════════════════

PERFIS_CURVA = {
    "linear": {
        "nome": "Linear",
        "descricao": "Sobe desde o inicio, sem zona facil",
        "prescricao": "So verde + forca. Zero Z3+.",
        "cor": "#F85149",
    },
    "normal": {
        "nome": "Normal",
        "descricao": "Base plana ate ao LT1, sobe, e dispara no LT2",
        "prescricao": "Estavel. Pode usar Z3/Z4/Z5.",
        "cor": "#3FB950",
    },
    "ingreme": {
        "nome": "Ingreme (elite/aerobio)",
        "descricao": "Base longa, topo estreito",
        "prescricao": "Unico caso para bloco de Z5.",
        "cor": "#58A6FF",
    },
    "achatada": {
        "nome": "Achatada (fadiga)",
        "descricao": "Parece treinada, mas sem topo -- 'apaga'",
        "prescricao": "NAO treinar vermelho. Descansar.",
        "cor": "#F0883E",
    },
}


def diagnostico_curva(curva, lt1_w, lt2_w, pvo2max_w=None, medido=False):
    """Classifica a forma da curva de lactato num dos quatro perfis."""
    pts = [(p.get("watts"), p.get("lactato")) for p in (curva or [])
           if p.get("watts") is not None and p.get("lactato") is not None]
    pts = [(float(w), float(l)) for w, l in pts]
    pts.sort()
    if len(pts) < 10 or not lt1_w or not lt2_w:
        return {"ok": False, "motivo": "curva ou limiares em falta"}

    w_max = pts[-1][0]
    def _la(w):
        melhor, d = None, 1e18
        for x, l in pts:
            if abs(x - w) < d:
                d, melhor = abs(x - w), l
        return melhor

    la_base = min(l for _w, l in pts)
    la_lt1, la_lt2 = _la(lt1_w), _la(lt2_w)
    la_topo = pts[-1][1]

    base_relativa = lt1_w / w_max if w_max else 0
    # subida no topo, por watt, relativa a subida entre LT1 e LT2
    tramo_meio = ((la_lt2 - la_lt1) / (lt2_w - lt1_w)) if lt2_w > lt1_w else 0
    tramo_topo = ((la_topo - la_lt2) / (w_max - lt2_w)) if w_max > lt2_w else 0
    aceleracao = (tramo_topo / tramo_meio) if tramo_meio > 0 else 0
    # Delta ABSOLUTO em mmol/L, nao percentagem. A curva modelada arranca
    # em ~0.2 mmol/L, muito abaixo de qualquer lactato de repouso real, e
    # uma subida de 1.1 mmol/L -- perfeitamente normal -- aparecia como
    # +550% e classificava tudo como "linear". E o mesmo erro de escala que
    # ja' tinha estragado a convencao do LT1.
    subida_ate_lt1 = la_lt1 - la_base

    # LIMITE CONHECIDO, verificado nos dados deste atleta:
    #
    # A curva do modelo de Mader nao muda de FORMA. Testada com quatro
    # conjuntos de MMP muito diferentes -- incluindo um sem base e outro
    # com o topo achatado -- a base relativa ficou sempre entre 78.6% e
    # 80.5%, a razao LT1/LT2 entre 0.799 e 0.816, e o VLamax entre 0.438 e
    # 0.465. A forma e' determinada pela estrutura da equacao, nao pelo
    # atleta: o que muda sao as potencias em que os pontos caem, nao o
    # desenho entre eles.
    #
    # Classificar nos quatro perfis do metodo exige lactato MEDIDO, com
    # pontos reais em varias intensidades. Com a curva modelada, qualquer
    # classificador devolveria sempre a mesma resposta -- e uma resposta
    # constante apresentada como diagnostico e' pior do que nenhuma.
    #
    # As medidas ficam devolvidas na mesma: se um dia houver pontos de
    # lactato reais, a classificacao passa a ter base e o codigo ja' esta.
    if not medido:
        return {
            "ok": False,
            "motivo": ("a forma da curva modelada e quase invariante -- "
                       "classificar os quatro perfis exige lactato medido"),
            "base_relativa_pct": round(base_relativa * 100, 1),
            "aceleracao_no_topo": round(aceleracao, 2),
            "subida_ate_lt1_mmol": round(subida_ate_lt1, 2),
            "lactato": {"base": round(la_base, 2), "lt1": round(la_lt1, 2),
                        "lt2": round(la_lt2, 2), "topo": round(la_topo, 2)},
            "perfis_possiveis": {k: v["nome"] for k, v in PERFIS_CURVA.items()},
            "o_que_falta": ("pontos de lactato medidos em pelo menos 4 "
                            "intensidades, ou uma rampa com recolha"),
        }

    if subida_ate_lt1 > 1.5 or base_relativa < 0.30:
        perfil = "linear"
    elif aceleracao < 1.2:
        perfil = "achatada"
    elif base_relativa >= 0.55 and aceleracao >= 2.5:
        perfil = "ingreme"
    else:
        perfil = "normal"

    return {
        "ok": True, "perfil": perfil, **PERFIS_CURVA[perfil],
        "base_relativa_pct": round(base_relativa * 100, 1),
        "aceleracao_no_topo": round(aceleracao, 2),
        "subida_ate_lt1_mmol": round(subida_ate_lt1, 2),
        "lactato": {"base": round(la_base, 2), "lt1": round(la_lt1, 2),
                    "lt2": round(la_lt2, 2), "topo": round(la_topo, 2)},
        "nota": ("base_relativa e a fraccao do dominio de potencia que fica "
                 "abaixo do LT1; aceleracao_no_topo compara a subida do "
                 "lactato acima do LT2 com a subida entre LT1 e LT2. Sao "
                 "medidas da curva deste atleta, nao cortes de tabela"),
    }


# Zonas ao estilo do semaforo (verde / tempo / vermelho), ancoradas nos
# limiares medidos e nao numa FTP. As sensacoes e a respiracao vem do
# metodo; as potencias e as FC saem dos teus dados.
ZONAS_SEMAFORO = [
    ("Z1 Recuperacao / Easy", 0.00, 0.85, "Facil; canta musica",
     "Igual ao repouso", "So limita a duracao", "parte do verde"),
    ("Z2 Steady / Endurance", 0.85, 1.00, "Confortavel com trabalho",
     "Aprofundamento subtil; frases com pausa", "Horas", "~80% (verde)"),
    ("Z3 Tempo / T-", 1.00, 1.15, "'Happy hard', controlado",
     "Ouve a respiracao, sem ofegar", "60-90 min", "~0-10%"),
    ("Z4 Threshold", 1.15, 1.30, "Dificil; queimacao",
     "Palavras isoladas; ofegante", "10-60 min", "~10-20%"),
    ("Z5 VO2max", 1.30, 1.45, "Muito dificil",
     "Grunhidos; sem fala", "6-10 min", "blocos curtos"),
]


def zonas_semaforo(lt1_w, lt2_w, hr_lt1=None, hr_lt2=None, pvo2max_w=None):
    """Cinco zonas ancoradas no LT1 (verde) e no LT2 (vermelho).

    Z1/Z2 sao fraccoes do LT1; Z3 em diante sao fraccoes do LT1 ate ao LT2
    e acima. Sem FTP: os limites sao os limiares medidos.
    """
    if not lt1_w or not lt2_w:
        return []
    out = []
    for nome, a, b, sens, resp, dur, pct in ZONAS_SEMAFORO:
        de = lt1_w * a if a < 1.0 else lt1_w + (lt2_w - lt1_w) * (a - 1.0) / 0.30
        ate = lt1_w * b if b <= 1.0 else lt1_w + (lt2_w - lt1_w) * (b - 1.0) / 0.30
        linha = {"zona": nome, "de_w": round(de), "ate_w": round(ate),
                 "sensacao": sens, "respiracao": resp,
                 "duracao": dur, "pct_treino": pct,
                 "cor": ("#3FB950" if a < 1.0 else
                         "#F0883E" if a < 1.15 else "#F85149")}
        linha["de_bpm"] = _bpm_da_zona(de, lt1_w, lt2_w, hr_lt1, hr_lt2)
        linha["ate_bpm"] = _bpm_da_zona(ate, lt1_w, lt2_w, hr_lt1, hr_lt2)
        out.append(linha)
    return out



# FC plausivel em treino. A recta LT1->LT2 so' e' valida entre esses dois
# pontos; extrapolada para Z1 dava -252 bpm e para Z5 dava 481 bpm. Um
# valor impossivel numa tabela de zonas e' pior do que uma celula vazia.
FC_MIN_ZONA, FC_MAX_ZONA = 70, 205


def _bpm_da_zona(w, lt1_w, lt2_w, hr_lt1, hr_lt2):
    """FC correspondente a uma potencia, ou None fora do plausivel."""
    if not (hr_lt1 and hr_lt2 and lt2_w and lt1_w and lt2_w > lt1_w):
        return None
    f = (hr_lt2 - hr_lt1) / (lt2_w - lt1_w)
    v = hr_lt1 + (w - lt1_w) * f
    return round(v) if FC_MIN_ZONA <= v <= FC_MAX_ZONA else None


def zonas_tres(lt1_w, lt2_w, hr_lt1=None, hr_lt2=None, w_max=None):
    """As tres zonas do semaforo: verde, tempo, vermelho.

    E' a leitura que interessa para decidir o dia a dia. As cinco zonas
    servem para prescrever a sessao; estas tres servem para decidir se hoje
    se constroi, se se afina ou se se quebra.
    """
    if not lt1_w or not lt2_w:
        return []
    tecto = w_max or lt2_w * 1.35
    blocos = [
        ("Zona Verde (Z1+Z2)", 0, lt1_w, "#3FB950",
         "Constroi. Anabolico. Base do treino o ano todo.", "~80%"),
        ("Tempo (Z3)", lt1_w, lt2_w, "#F0883E",
         "Constroi se estas fresco, quebra se estas cansado.", "0-10%"),
        ("Zona Vermelha (Z4+Z5)", lt2_w, tecto, "#F85149",
         "Catabolico. So adapta com nutricao e recuperacao.", "10-20%"),
    ]
    out = []
    for nome, de, ate, cor, o_que_faz, pct in blocos:
        linha = {"zona": nome, "de_w": round(de), "ate_w": round(ate),
                 "cor": cor, "o_que_faz": o_que_faz, "pct_treino": pct}
        linha["de_bpm"] = _bpm_da_zona(de, lt1_w, lt2_w, hr_lt1, hr_lt2)
        linha["ate_bpm"] = _bpm_da_zona(ate, lt1_w, lt2_w, hr_lt1, hr_lt2)
        out.append(linha)
    return out


def ancoras_do_consenso(coerencia):
    """LT1 e LT2 em W e bpm, tirados do consenso da validacao externa.

    As zonas passam a assentar no que os campos medem, nao no que o modelo
    calcula. Quando ha transicao, usa-se o FIM dela como ancora: e' o fim
    do dominio moderado que separa o verde do tempo, nao o inicio.
    """
    def _pega(grupo, unidade):
        b = (coerencia.get(grupo) or {}).get(unidade)
        if not b:
            return None
        if b.get("transicao"):
            return b["transicao"]["fim"]["valor"]
        c = b.get("consenso") or {}
        return c.get("valor")

    return {
        "lt1_w": _pega("aerobio", "em_watts"),
        "lt2_w": _pega("limiar", "em_watts"),
        "lt1_bpm": _pega("aerobio", "em_bpm"),
        "lt2_bpm": _pega("limiar", "em_bpm"),
    }
