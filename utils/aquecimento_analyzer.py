"""
AQUECIMENTO_ANALYZER.PY — Deteccao do aquecimento por protocolo fixo

O aquecimento e' uma escada de watts ALVO, sempre a mesma por modalidade,
com blocos de ~5 min separados por ~1 min de recuperacao:

    Row   140 - 160 - 180 W                (3 blocos)
    Ski   120 - 140 - 160 W                (3 blocos)
    Bike   80 - 100 - 120 - 140 - 160 W    (5 blocos)

Tolerancia diferente por modalidade: Row e Ski nao tem erg-mode, o atleta
persegue o alvo a' mao e os watts oscilam; a Bike tem erg-mode e fica
praticamente colada ao alvo.

Se a atividade nao bater no protocolo, e' IGNORADA (detectado=False) --
nem todas as sessoes comecam com este aquecimento.
"""

import numpy as np

PROTOCOLOS = {
    "Row":  {"watts": [140, 160, 180],          "tol": 15, "min_blocos": 3},
    "Ski":  {"watts": [120, 140, 160],          "tol": 15, "min_blocos": 3},
    # A Bike tem erg-mode, mas o numero de degraus pode variar entre 4 e 5.
    # min_blocos=4 aceita 80-100-140-160 e 80-100-120-140-160.
    "Bike": {"watts": [80, 100, 120, 140, 160], "tol": 12, "min_blocos": 4},
}

# Duracao aceite para blocos de trabalho e de recuperacao (segundos).
WORK_SEG = (210, 420)   # 5 min, com folga
REST_SEG = (30, 150)    # 1 min, com folga

# O aquecimento comeca SEMPRE no primeiro intervalo da atividade.
# Permitir folga aqui so criaria falsos positivos (degraus parecidos a meio
# do treino a serem lidos como aquecimento).
MAX_OFFSET = 0

# Nem todas as colunas *_60s foram preenchidas pelo pipeline original.
# Para cada metrica/agregacao tentamos varias colunas, pela ordem indicada,
# e usamos a primeira que tenha valor.
FALLBACKS = {
    ("hr", "avg"):   ["hr_avg_60s", "hr_medio_work", "hr_plateau_work"],
    ("hr", "min"):   ["hr_min_60s", "hr_baseline"],
    ("hr", "max"):   ["hr_max_60s", "hr_extremo"],
    ("smo2", "avg"): ["smo2_avg_60s", "smo2_medio_work", "smo2_plateau_work"],
    ("smo2", "min"): ["smo2_min_60s", "smo2_extremo", "smo2_baseline"],
    ("smo2", "max"): ["smo2_max_60s", "smo2_baseline"],
    ("resp", "avg"): ["resp_avg_60s", "resp_medio_work", "resp_plateau_work"],
    ("resp", "min"): ["resp_min_60s", "resp_baseline"],
    ("resp", "max"): ["resp_max_60s", "resp_extremo"],
    ("dfa1", "avg"): ["dfa1_avg_60s", "dfa1_clean", "dfa1_medio_work",
                      "dfa1_plateau_work"],
    ("dfa1", "min"): ["dfa1_min_60s", "dfa1_extremo"],
    ("dfa1", "max"): ["dfa1_max_60s", "dfa1_baseline"],
}

COLUNAS_METRICA = sorted({c for v in FALLBACKS.values() for c in v})


class AquecimentoAnalyzer:
    def __init__(self, conn=None):
        """conn: ligacao a' BD fisiologia (tabela fisiologia_intervalos)."""
        self.conn = conn

    # ── protocolo ─────────────────────────────────────────────────────────

    def protocolo(self, modalidade):
        return PROTOCOLOS.get(modalidade)

    # ── leitura dos intervalos ────────────────────────────────────────────

    def _colunas_existentes(self):
        try:
            return {r[1] for r in self.conn.execute(
                "PRAGMA table_info(fisiologia_intervalos)")}
        except Exception:
            return set()

    def _carregar_intervalos(self, activity_id):
        """Lista de dicts por ordem de interval_num.

        So pede colunas que existem mesmo -- o schema foi crescendo e nem
        todas as instalacoes tem as mesmas.
        """
        existentes = self._colunas_existentes()
        base = ["interval_num", "watts_medio"]
        opcionais = ["dur_work_s", "dur_rec_s"] + COLUNAS_METRICA
        faltam = [c for c in base if c not in existentes]
        if faltam:
            raise RuntimeError(f"colunas em falta na BD: {faltam}")
        cols = base + [c for c in opcionais if c in existentes]

        linhas = self.conn.execute(
            f"""SELECT {', '.join(cols)} FROM fisiologia_intervalos
                WHERE activity_id = ? AND valido = 1
                ORDER BY interval_num""", (activity_id,)).fetchall()
        return [dict(zip(cols, l)) for l in linhas]

    @staticmethod
    def _duracao(iv):
        return float(iv["dur_work_s"]) if iv.get("dur_work_s") else None

    @staticmethod
    def _duracao_rec(iv):
        return float(iv["dur_rec_s"]) if iv.get("dur_rec_s") else None

    # ── deteccao ──────────────────────────────────────────────────────────

    def _bate_alvo(self, watts, alvo, tol):
        return watts is not None and abs(watts - alvo) <= tol

    def _duracao_ok(self, dur, janela):
        """Duracao desconhecida nao invalida -- so nao confirma."""
        return dur is None or janela[0] <= dur <= janela[1]

    def _procurar_escada(self, intervalos, alvos, tol, min_blocos):
        """Procura linhas CONSECUTIVAS que sigam a escada de alvos.

        Aceita um PREFIXO da escada: basta bater em min_blocos degraus
        seguidos, a comecar no primeiro alvo. Assim uma Bike de 4 degraus
        e uma de 5 sao ambas aceites.

        Nao olha para metricas: uma sessao sem SmO2, ou com o sensor a cair
        a meio, e' aceite na mesma -- as metricas em falta ficam a NULL.
        """
        melhor = None
        for n in range(len(alvos), min_blocos - 1, -1):   # tenta a mais longa
            sub = alvos[:n]
            for off in range(0, MAX_OFFSET + 1):
                if off + n > len(intervalos):
                    break
                janela = intervalos[off:off + n]
                ok = True
                for pos, iv in enumerate(janela):
                    if not self._bate_alvo(iv.get("watts_medio"), sub[pos], tol):
                        ok = False
                        break
                    if not self._duracao_ok(self._duracao(iv), WORK_SEG):
                        ok = False
                        break
                    if pos < n - 1 and not self._duracao_ok(
                            self._duracao_rec(iv), REST_SEG):
                        ok = False
                        break
                if ok:
                    melhor = (list(range(off, off + n)), sub)
                    break
            if melhor:
                break
        return melhor

    # ── metricas ──────────────────────────────────────────────────────────

    @staticmethod
    def _primeiro_valor(iv, candidatas):
        for c in candidatas:
            v = iv.get(c)
            if v is not None:
                return float(v), c
        return None, None

    def _metricas_do_bloco(self, iv):
        """Valor de cada metrica/agregacao, com fallback de coluna.

        Guarda tambem que coluna foi usada, para se poder diagnosticar
        depois porque e' que uma metrica veio vazia.
        """
        out, origem = {}, {}
        for base in ("hr", "smo2", "resp", "dfa1"):
            for agreg in ("avg", "min", "max"):
                v, col = self._primeiro_valor(
                    iv, FALLBACKS.get((base, agreg), []))
                out[f"{base}_{agreg}"] = v
                if col:
                    origem[f"{base}_{agreg}"] = col
        self.ultima_origem = origem
        return out

    # ── API ───────────────────────────────────────────────────────────────

    def analisar_atividade(self, activity_id, modalidade):
        """Analisa uma atividade. Se 'detectado', devolve 'blocos' com uma
        entrada por bloco de trabalho da escada."""
        proto = self.protocolo(modalidade)
        if not proto:
            return {"detectado": False,
                    "motivo": f"modalidade sem protocolo: {modalidade}"}

        try:
            intervalos = self._carregar_intervalos(activity_id)
        except Exception as e:
            return {"detectado": False,
                    "motivo": f"erro BD: {type(e).__name__}: {e}"}

        if not intervalos:
            return {"detectado": False, "motivo": "sem intervalos validos"}

        alvos, tol = proto["watts"], proto["tol"]
        if len(intervalos) < proto.get("min_blocos", len(alvos)):
            return {"detectado": False, "motivo": "intervalos a menos para a escada"}

        min_blocos = proto.get("min_blocos", len(alvos))
        achado = self._procurar_escada(intervalos, alvos, tol, min_blocos)
        if achado is None:
            watts = [iv.get("watts_medio") for iv in intervalos[:8]]
            return {"detectado": False, "motivo": "nao bate no protocolo",
                    "watts_vistos": [round(w) if w else None for w in watts]}
        idx_work, alvos = achado

        blocos = []
        for n, i in enumerate(idx_work, start=1):
            iv = intervalos[i]
            dur = self._duracao(iv)
            w = iv.get("watts_medio")
            blocos.append({
                "bloco_num": n,
                "watts_alvo": alvos[n - 1],
                "watts_real": float(w) if w is not None else None,
                "interval_num": iv.get("interval_num"),
                "tempo_seg": int(dur) if dur else None,
                **self._metricas_do_bloco(iv),
            })

        return {
            "detectado": True,
            "modalidade": modalidade,
            "padrao": "-".join(str(a) for a in alvos),
            "n_blocos": len(blocos),
            "blocos": blocos,
            "tempo_aquecimento_seg": sum(b["tempo_seg"] or 0 for b in blocos),
        }


# ── estatistica: SEM / MDC ────────────────────────────────────────────────

def sem_por_pares(valores_datados, dias_max=10, min_pares=5):
    """SEM estimado a partir de sessoes proximas no tempo.

    valores_datados: lista de (data_iso, valor) do MESMO escalao de watts e
    da MESMA modalidade. Pares separados por <= dias_max assumem-se sem
    adaptacao verdadeira, logo a diferenca e' ruido de medicao.

        SEM   = sd(diferencas) / sqrt(2)
        MDC95 = SEM * 1.96 * sqrt(2)

    Nao usar o SD de todas as sessoes do ano: isso mete variacao biologica
    real dentro do termo de erro e inflaciona o MDC, escondendo justamente
    as mudancas que se quer detectar.

    Devolve sem=None se nao houver pares suficientes -- melhor nao mostrar
    banda nenhuma do que mostrar uma banda inventada.
    """
    from datetime import datetime as _dt

    pts = []
    for d, v in valores_datados:
        if v is None:
            continue
        try:
            pts.append((_dt.fromisoformat(str(d)[:19]), float(v)))
        except Exception:
            continue
    pts.sort(key=lambda p: p[0])

    difs = []
    for i in range(len(pts) - 1):
        delta = (pts[i + 1][0] - pts[i][0]).days
        if 0 <= delta <= dias_max:
            difs.append(pts[i + 1][1] - pts[i][1])

    if len(difs) < min_pares:
        return {"sem": None, "mdc95": None, "n_pares": len(difs),
                "fiavel": False,
                "nota": f"pares a menos para estimar o SEM (minimo {min_pares})"}

    sem = float(np.std(difs, ddof=1) / np.sqrt(2))
    return {"sem": sem,
            "mdc95": float(sem * 1.96 * np.sqrt(2)),
            "n_pares": len(difs),
            "fiavel": len(difs) >= 10,
            "nota": None if len(difs) >= 10 else "poucos pares; banda indicativa"}


# ── tendencia por janelas temporais ───────────────────────────────────────

# Direccao que representa melhoria. None = ambiguo, e nesse caso NAO se
# rotula como "melhor" ou "pior" -- so se diz para onde foi.
DIRECAO_BOA = {
    "hr":   -1,    # menos batimentos para a mesma potencia
    "hrw":  -1,    # menos batimentos por watt
    "resp": -1,    # menos ventilacao para a mesma potencia
    "dfa1": +1,    # mais alto = menos stress autonomico aquela intensidade
    "smo2": None,  # ambiguo: pode subir por melhor entrega OU menor extraccao
}

JANELAS = [("60 dias", 60), ("90 dias", 90), ("1 ano", 365),
           ("2 anos", 730), ("3 anos", 1095)]


def tendencia(pontos, metrica=None, mdc=None, min_n=6):
    """Tendencia por janelas temporais, com o MDC como limiar.

    pontos: [(data_iso, valor), ...] do MESMO escalao de watts.

    A mudanca estimada e' o declive da recta multiplicado pelo intervalo
    realmente coberto pelos dados dessa janela -- nao pela janela nominal,
    senao extrapolava-se para alem do que existe.

    Uma mudanca menor que o MDC e' classificada como estavel: nao e'
    distinguivel do ruido de medicao, por muito bonita que a recta seja.
    """
    from datetime import datetime as _dt

    pts = []
    for d, v in pontos:
        if v is None:
            continue
        try:
            pts.append((_dt.fromisoformat(str(d)[:19]), float(v)))
        except Exception:
            continue
    if not pts:
        return []
    pts.sort(key=lambda p: p[0])
    fim = pts[-1][0]

    saida = []
    for etiqueta, dias in JANELAS:
        janela = [(t, v) for t, v in pts if (fim - t).days <= dias]
        if len(janela) < min_n:
            if janela:
                saida.append({"janela": etiqueta, "n": len(janela),
                              "estado": "dados insuficientes",
                              "nota": f"minimo {min_n} sessoes"})
            continue

        x = [(t - janela[0][0]).days for t, _ in janela]
        y = [v for _, v in janela]
        n = len(x)
        mx, my = sum(x) / n, sum(y) / n
        den = sum((xi - mx) ** 2 for xi in x)
        if den == 0:
            continue
        declive = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / den
        cobertura = x[-1] - x[0]
        mudanca = declive * cobertura

        # r2, para se saber se a recta descreve mesmo os pontos
        ss_tot = sum((yi - my) ** 2 for yi in y)
        ss_res = sum((yi - (my + declive * (xi - mx))) ** 2
                     for xi, yi in zip(x, y))
        r2 = (1 - ss_res / ss_tot) if ss_tot else None

        if mdc and abs(mudanca) < mdc:
            estado, sentido = "estavel", 0
        else:
            sentido = 1 if mudanca > 0 else -1
            estado = "a subir" if sentido > 0 else "a descer"

        item = {"janela": etiqueta, "n": n,
                "dias_cobertos": cobertura,
                "mudanca": round(mudanca, 3),
                "por_30_dias": round(declive * 30, 4),
                "r2": round(r2, 2) if r2 is not None else None,
                "estado": estado,
                "primeiro": round(y[0], 2), "ultimo": round(y[-1], 2)}

        boa = DIRECAO_BOA.get(metrica)
        if estado == "estavel":
            item["leitura"] = "sem mudanca alem do ruido"
        elif boa is None:
            item["leitura"] = "sem direccao clara para esta metrica"
        else:
            item["leitura"] = "melhoria" if sentido == boa else "piora"
        if mdc is None:
            item["aviso"] = "sem MDC: mudanca nao comparada com o ruido"
        elif r2 is not None and r2 < 0.15 and estado != "estavel":
            item["aviso"] = "recta explica pouco (r2 baixo); dispersao alta"
        saida.append(item)

    return saida
