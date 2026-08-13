"""fisiologia_worker.py — Perfil metabólico por lag/recovery.

O QUE FAZ
---------
Para cada atividade cíclica (Bike/Row/Ski/Run) com potência:
  1. Lê os streams JÁ GUARDADOS no Postgres (watts, heartrate, smo2, thb,
     respiration) via db.get_streams() — não volta a pedir à API.
  2. Pede à API do Intervals.icu só os INTERVALOS/LAPS da atividade
     (endpoint leve, não é stream) — para saber onde começa/acaba cada
     WORK e REC.
  3. Para cada intervalo WORK: mede quanto tempo (s) cada métrica leva a
     percorrer 50/75/90% da excursão entre a baseline (antes do WORK) e o
     patamar estável (fim do WORK).
  4. Para o REC a seguir (se existir): mede quanto tempo leva a voltar
     50/75% do caminho até à baseline original.
  5. Grava 1 linha por intervalo em fisiologia_perfil.db (SQLite, Drive).

NÃO faz (por agora):
  DFA1 — exige RR intervals brutos do FIT, não vem nos streams da API.
         Fica para uma extensão futura (reaproveitando lógica tipo
         tab_fit_analise.py do Streamlit, que já sabe extrair RR de FIT).

SEM ZONAS FIXAS: watts_medio/min/max ficam como valor contínuo. Os
quartis de potência são calculados depois, na leitura (tab_metabol.py),
a partir da distribuição real de cada modalidade.

COMO CORRER
-----------
Processamento incremental, por lotes, do mais recente para o mais antigo,
até à data de corte (2024-01-01 por omissão):

    python fisiologia_worker.py                  # 1 lote de 10 atividades
    python fisiologia_worker.py --n 20            # lote maior
    python fisiologia_worker.py --debug ACTIVITY_ID   # ver resposta bruta
                                                        # da API /intervals
                                                        # para 1 atividade

Pensado para correr como CRON JOB separado no Railway (não dentro do
processo web do app.py) — evita bloquear pedidos HTTP durante o
processamento e evita repetir o erro anterior de mexer no app.py.
Ver INSTRUCOES no fim do ficheiro para configurar o cron no Railway.

CONFIGURAÇÃO QUE PODE PRECISAR DE AJUSTE
-----------------------------------------
O schema exato do endpoint /activity/{id}/intervals não foi confirmado
ao vivo. Os nomes de campos candidatos estão nas constantes CHAVES_* no
topo do ficheiro. Corra primeiro:

    python fisiologia_worker.py --debug <activity_id>

e confirme que os campos batem certo. Se a Intervals.icu usar nomes
diferentes, ajuste só as constantes — o resto do código não muda.
"""

import os
import sys
import json
from datetime import datetime, date

import numpy as np

import db
from api_client import icu_get, norm_tipo
import drive_db_fisiologia as ddf

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ══════════════════════════════════════════════════════════════════════════

DATA_CORTE = os.getenv("FISIOLOGIA_DATA_CORTE", "2024-01-01")
LOTE_PADRAO = int(os.getenv("FISIOLOGIA_LOTE", "10"))

DUR_MIN_WORK_S = 20     # intervalos mais curtos que isto são ignorados (ruído)
DUR_MIN_REC_S = 15
JANELA_BASELINE_S = 8   # segundos antes do WORK usados como baseline
FRAC_PLATEAU = 0.25     # fração final do intervalo usada como "patamar estável"

# Nomes de campos candidatos na resposta de /activity/{id}/intervals.
# A Intervals.icu pode devolver a lista directamente ou dentro de uma chave.
CHAVES_LISTA_INTERVALOS = ['icu_intervals', 'intervals', None]  # None = resposta já é lista
CHAVES_TIPO = ['type', 'label', 'name']
CHAVES_INICIO_S = ['start_time', 'start_index', 'start']
CHAVES_FIM_S = ['end_time', 'end_index', 'end']
CHAVES_WATTS_MEDIO = ['average_watts', 'avg_watts', 'icu_average_watts']

# Tipos que contam como WORK (case-insensitive, substring match)
TIPOS_WORK = ('work', 'interval', 'active', 'on')
# Tipos que contam como RECOVERY (substring match) — o resto é ignorado
# (warmup/cooldown/ramp não entram na análise de lag)
TIPOS_RECOVERY = ('recovery', 'rest', 'off')
TIPOS_IGNORAR = ('warmup', 'cooldown', 'ramp')

# Nomes dos streams tal como guardados por parse_streams() (chave = 'type'
# devolvido pela API). Se houver 2º sensor, a chave fica com sufixo _2 —
# aqui só usamos o principal.
STREAM_HR = 'heartrate'
STREAM_WATTS = 'watts'
STREAM_SMO2 = 'smo2'
STREAM_THB = 'thb'
STREAMS_RESP_CANDIDATOS = ['respiration', 'breathing_rate', 'resp_rate']


# ══════════════════════════════════════════════════════════════════════════
# ALINHAMENTO TEMPORAL (streams vêm downsampled, precisam de dt próprio)
# ══════════════════════════════════════════════════════════════════════════

def _tempos_do_stream(stream, duracao_total_s):
    """Vector de tempos (s) alinhado a um stream possivelmente downsampled.

    downsample() em api_client.py reduz para no máx. 1500 pontos, uniforme.
    dt = duracao_total / n_pontos (aprox — assume amostragem original ~1Hz).
    """
    n = len(stream) if stream else 0
    if n == 0 or not duracao_total_s:
        return np.array([])
    dt = duracao_total_s / n
    return np.arange(n) * dt


def _valores_float(stream):
    return np.array([v if isinstance(v, (int, float)) else np.nan for v in (stream or [])],
                    dtype=np.float64)


# ══════════════════════════════════════════════════════════════════════════
# INTERVALOS (laps) — busca à API + parsing defensivo
# ══════════════════════════════════════════════════════════════════════════

def buscar_intervalos_api(activity_id):
    """(lista_intervalos, erro). Cada item: {tipo, t_ini, t_fim, watts_medio}."""
    data, erro = icu_get(f"/activity/{activity_id}/intervals")
    if erro:
        return None, erro

    bruto = None
    for chave in CHAVES_LISTA_INTERVALOS:
        if chave is None and isinstance(data, list):
            bruto = data
            break
        if chave and isinstance(data, dict) and chave in data:
            bruto = data[chave]
            break
    if bruto is None:
        return None, f"schema inesperado: {str(data)[:200]}"

    out = []
    for item in bruto:
        if not isinstance(item, dict):
            continue
        tipo = None
        for k in CHAVES_TIPO:
            if item.get(k):
                tipo = str(item[k]).lower()
                break
        t_ini = None
        for k in CHAVES_INICIO_S:
            if item.get(k) is not None:
                t_ini = float(item[k])
                break
        t_fim = None
        for k in CHAVES_FIM_S:
            if item.get(k) is not None:
                t_fim = float(item[k])
                break
        watts_medio = None
        for k in CHAVES_WATTS_MEDIO:
            if item.get(k) is not None:
                watts_medio = float(item[k])
                break
        if tipo is None or t_ini is None or t_fim is None:
            continue
        out.append({'tipo': tipo, 't_ini': t_ini, 't_fim': t_fim,
                    'watts_medio_api': watts_medio})
    return out, None


def _classificar(tipo):
    t = (tipo or '').lower()
    if any(p in t for p in TIPOS_IGNORAR):
        return 'ignorar'
    if any(p in t for p in TIPOS_WORK):
        return 'work'
    if any(p in t for p in TIPOS_RECOVERY):
        return 'recovery'
    return 'ignorar'


def emparelhar_work_rec(intervalos):
    """[(work, rec_ou_None), ...] — rec é o intervalo seguinte SE for recovery."""
    pares = []
    i = 0
    n = len(intervalos)
    while i < n:
        cls = _classificar(intervalos[i]['tipo'])
        if cls == 'work':
            work = intervalos[i]
            rec = None
            if i + 1 < n and _classificar(intervalos[i + 1]['tipo']) == 'recovery':
                rec = intervalos[i + 1]
            pares.append((work, rec))
        i += 1
    return pares


# ══════════════════════════════════════════════════════════════════════════
# LAG / RECOVERY — núcleo do cálculo
# ══════════════════════════════════════════════════════════════════════════

def _media_janela(tempos, valores, t_ini, t_fim):
    mask = (tempos >= t_ini) & (tempos <= t_fim)
    vs = valores[mask]
    vs = vs[np.isfinite(vs)]
    return float(np.mean(vs)) if len(vs) else None


def _tempo_ate_percentual(tempos, valores, t_ini, t_fim, baseline, alvo, pct):
    """Segundos desde t_ini até valor cruzar baseline + pct*(alvo-baseline).

    None se não houver dados suficientes ou o alvo nunca for atingido
    dentro da janela.
    """
    if baseline is None or alvo is None:
        return None
    delta = alvo - baseline
    if abs(delta) < 1e-6:
        return None
    limiar = baseline + pct * delta
    crescente = delta > 0

    mask = (tempos >= t_ini) & (tempos <= t_fim)
    idx = np.where(mask)[0]
    for i in idx:
        v = valores[i]
        if not np.isfinite(v):
            continue
        if (crescente and v >= limiar) or ((not crescente) and v <= limiar):
            return float(tempos[i] - t_ini)
    return None


def _lags_metrica(tempos, valores, t_work_ini, t_work_fim, t_rec_fim=None):
    """Lag de resposta (50/75/90%) + recovery (50/75%), para 1 métrica.

    baseline  = média nos JANELA_BASELINE_S antes do WORK
    plateau   = média no último FRAC_PLATEAU do WORK (patamar estável)
    recovery  = tempo (desde início do REC) até voltar 50/75% do caminho
                de volta à baseline original
    """
    baseline = _media_janela(tempos, valores, max(0, t_work_ini - JANELA_BASELINE_S), t_work_ini)
    janela_plateau = max(3.0, (t_work_fim - t_work_ini) * FRAC_PLATEAU)
    plateau = _media_janela(tempos, valores, t_work_fim - janela_plateau, t_work_fim)

    lags = {}
    for pct, nome in [(0.5, '50'), (0.75, '75'), (0.9, '90')]:
        lags[f'lag_{nome}'] = _tempo_ate_percentual(
            tempos, valores, t_work_ini, t_work_fim, baseline, plateau, pct)

    rec = {'rec_50': None, 'rec_75': None}
    if t_rec_fim is not None and baseline is not None and plateau is not None:
        # recovery: parte de "plateau" (extremo atingido no WORK) e mede o
        # regresso em direcção à baseline original
        for pct, nome in [(0.5, '50'), (0.75, '75')]:
            rec[f'rec_{nome}'] = _tempo_ate_percentual(
                tempos, valores, t_work_fim, t_rec_fim, plateau, baseline, pct)

    return lags, rec, baseline is not None and plateau is not None


# ══════════════════════════════════════════════════════════════════════════
# PROCESSAMENTO DE 1 ATIVIDADE
# ══════════════════════════════════════════════════════════════════════════

def processar_atividade(activity, conn):
    """Retorna (n_intervalos_gravados, motivo_se_pulada)."""
    activity_id = str(activity.get('id'))
    modalidade = norm_tipo(activity.get('type'))
    data_str = str(activity.get('start_date_local', ''))[:10]

    duracao_total_s = activity.get('moving_time') or activity.get('elapsed_time')
    if not duracao_total_s:
        return 0, 'sem duracao'

    streams, meta = db.get_streams(activity_id)
    if not streams or STREAM_WATTS not in streams:
        return 0, 'sem streams de potencia guardados'

    intervalos, erro = buscar_intervalos_api(activity_id)
    if erro:
        return 0, f'erro API intervals: {erro}'
    if not intervalos:
        return 0, 'sem intervalos/laps marcados'

    pares = emparelhar_work_rec(intervalos)
    if not pares:
        return 0, 'nenhum WORK identificado'

    # tempos + valores por métrica (uma vez por atividade, reaproveitados
    # para todos os intervalos)
    t_watts = _tempos_do_stream(streams.get(STREAM_WATTS), duracao_total_s)
    v_watts = _valores_float(streams.get(STREAM_WATTS))

    tem_hr = STREAM_HR in streams
    t_hr = _tempos_do_stream(streams.get(STREAM_HR), duracao_total_s) if tem_hr else None
    v_hr = _valores_float(streams.get(STREAM_HR)) if tem_hr else None

    tem_smo2 = STREAM_SMO2 in streams
    t_smo2 = _tempos_do_stream(streams.get(STREAM_SMO2), duracao_total_s) if tem_smo2 else None
    v_smo2 = _valores_float(streams.get(STREAM_SMO2)) if tem_smo2 else None

    tem_thb = STREAM_THB in streams
    t_thb = _tempos_do_stream(streams.get(STREAM_THB), duracao_total_s) if tem_thb else None
    v_thb = _valores_float(streams.get(STREAM_THB)) if tem_thb else None

    resp_key = next((k for k in STREAMS_RESP_CANDIDATOS if k in streams), None)
    tem_resp = resp_key is not None
    t_resp = _tempos_do_stream(streams.get(resp_key), duracao_total_s) if tem_resp else None
    v_resp = _valores_float(streams.get(resp_key)) if tem_resp else None

    now = datetime.now().isoformat(timespec='seconds')
    gravados = 0

    for n, (work, rec) in enumerate(pares, start=1):
        t_ini, t_fim = work['t_ini'], work['t_fim']
        dur_work = t_fim - t_ini
        if dur_work < DUR_MIN_WORK_S:
            continue

        dur_rec = None
        t_rec_fim = None
        if rec is not None:
            dur_rec = rec['t_fim'] - rec['t_ini']
            if dur_rec >= DUR_MIN_REC_S:
                t_rec_fim = rec['t_fim']
            else:
                dur_rec = None

        # watts: preferir o que a API já dá (average_watts do intervalo);
        # min/max calculados a partir do stream, se disponível
        watts_medio = work.get('watts_medio_api')
        watts_min = watts_max = None
        if len(v_watts):
            mask = (t_watts >= t_ini) & (t_watts <= t_fim)
            vs = v_watts[mask]
            vs = vs[np.isfinite(vs)]
            if len(vs):
                if watts_medio is None:
                    watts_medio = float(np.mean(vs))
                watts_min = float(np.min(vs))
                watts_max = float(np.max(vs))
        if watts_medio is None:
            continue  # sem potência não há como caracterizar o intervalo

        linha = {
            'activity_id': activity_id, 'data': data_str, 'modalidade': modalidade,
            'interval_num': n, 'watts_medio': watts_medio,
            'watts_min': watts_min, 'watts_max': watts_max,
            'dur_work_s': int(dur_work), 'dur_rec_s': int(dur_rec) if dur_rec else None,
            'tem_hr': int(tem_hr), 'tem_smo2': int(tem_smo2),
            'tem_thb': int(tem_thb), 'tem_resp': int(tem_resp),
            'valido': 1, 'motivo_invalido': None, 'criado_em': now,
        }

        qualidade_ok = False

        def _preencher(prefixo, tem, t_arr, v_arr):
            nonlocal qualidade_ok
            if not tem or t_arr is None or not len(t_arr):
                for suf in ('50', '75', '90'):
                    linha[f'lag_{prefixo}_{suf}'] = None
                linha[f'rec_{prefixo}_50'] = None
                linha[f'rec_{prefixo}_75'] = None
                return
            lags, recv, ok = _lags_metrica(t_arr, v_arr, t_ini, t_fim, t_rec_fim)
            if ok:
                qualidade_ok = True
            linha[f'lag_{prefixo}_50'] = lags.get('lag_50')
            linha[f'lag_{prefixo}_75'] = lags.get('lag_75')
            linha[f'lag_{prefixo}_90'] = lags.get('lag_90')
            linha[f'rec_{prefixo}_50'] = recv.get('rec_50')
            linha[f'rec_{prefixo}_75'] = recv.get('rec_75')

        _preencher('hr', tem_hr, t_hr, v_hr)
        _preencher('smo2', tem_smo2, t_smo2, v_smo2)
        _preencher('thb', tem_thb, t_thb, v_thb)
        _preencher('resp', tem_resp, t_resp, v_resp)

        if not qualidade_ok:
            linha['valido'] = 0
            linha['motivo_invalido'] = 'nenhuma metrica com baseline/plateau calculavel'

        _gravar_linha(conn, linha)
        gravados += 1

    return gravados, None


def _gravar_linha(conn, linha):
    cols = ', '.join(linha.keys())
    placeholders = ', '.join(['?'] * len(linha))
    conn.execute(
        f"""INSERT OR REPLACE INTO fisiologia_intervalos ({cols})
            VALUES ({placeholders})""",
        tuple(linha.values())
    )


# ══════════════════════════════════════════════════════════════════════════
# SELEÇÃO DO LOTE (mais recente → mais antigo, até DATA_CORTE)
# ══════════════════════════════════════════════════════════════════════════

CICLICOS = ('Bike', 'Row', 'Ski', 'Run')


def proximo_lote(conn, n=LOTE_PADRAO):
    ja_processadas = {r[0] for r in conn.execute(
        "SELECT DISTINCT activity_id FROM fisiologia_intervalos").fetchall()}

    atividades = db.load_activities(desde=DATA_CORTE) or []
    candidatas = []
    for a in atividades:
        aid = str(a.get('id'))
        if aid in ja_processadas:
            continue
        modalidade = norm_tipo(a.get('type'))
        if modalidade not in CICLICOS:
            continue
        if not (a.get('icu_average_watts') or 0) > 0:
            continue
        candidatas.append(a)

    # já vem ORDER BY date DESC do Postgres (load_activities)
    return candidatas[:n]


# ══════════════════════════════════════════════════════════════════════════
# LOOP PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════

def processar_lote(n=LOTE_PADRAO):
    conn = ddf.get_conn()
    lote = proximo_lote(conn, n)

    if not lote:
        conn.execute("""UPDATE fisiologia_progresso SET
                        concluido = 1, ultima_execucao = ? WHERE id = 1""",
                    (datetime.now().isoformat(timespec='seconds'),))
        conn.commit()
        ddf.upload()
        print("Nada para processar — historico completo ate a data de corte.")
        return

    processadas = puladas = erros = 0
    for activity in lote:
        aid = str(activity.get('id'))
        try:
            n_gravados, motivo = processar_atividade(activity, conn)
            if motivo:
                puladas += 1
                print(f"  [PULADA] {aid}: {motivo}")
            else:
                processadas += 1
                print(f"  [OK] {aid}: {n_gravados} intervalos gravados")
        except Exception as e:
            erros += 1
            print(f"  [ERRO] {aid}: {type(e).__name__}: {e}")

    ultima = lote[-1]
    conn.execute("""UPDATE fisiologia_progresso SET
                    total_processadas = total_processadas + ?,
                    total_puladas = total_puladas + ?,
                    total_erros = total_erros + ?,
                    ultima_activity_id = ?,
                    ultima_data = ?,
                    ultima_execucao = ?
                    WHERE id = 1""",
                (processadas, puladas, erros,
                 str(ultima.get('id')), str(ultima.get('start_date_local', ''))[:10],
                 datetime.now().isoformat(timespec='seconds')))
    conn.commit()
    ddf.upload()

    print(f"\nLote concluido: {processadas} processadas, {puladas} puladas, "
          f"{erros} erros. {len(lote)} atividades no lote.")


def depurar_intervalos(activity_id):
    """Mostra a resposta bruta de /intervals para confirmar o schema."""
    data, erro = icu_get(f"/activity/{activity_id}/intervals")
    print(f"Erro: {erro}")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])
    print("\n--- Parsing com as chaves configuradas ---")
    intervalos, erro2 = buscar_intervalos_api(activity_id)
    print(f"Erro parsing: {erro2}")
    if intervalos:
        for it in intervalos[:10]:
            print(it)


if __name__ == '__main__':
    if '--debug' in sys.argv:
        idx = sys.argv.index('--debug')
        activity_id = sys.argv[idx + 1]
        depurar_intervalos(activity_id)
    else:
        n = LOTE_PADRAO
        if '--n' in sys.argv:
            idx = sys.argv.index('--n')
            n = int(sys.argv[idx + 1])
        processar_lote(n)


# ══════════════════════════════════════════════════════════════════════════
# INSTRUÇÕES — Railway Cron Job
# ══════════════════════════════════════════════════════════════════════════
#
# Este ficheiro NÃO altera app.py nem corre dentro do processo web.
# No Railway, criar um novo serviço "Cron Job" (mesmo repo, mesmo build),
# com comando:
#
#     python fisiologia_worker.py --n 10
#
# e agendamento (ex.: todos os dias às 04:00):
#
#     0 4 * * *
#
# Variáveis de ambiente necessárias (as mesmas do serviço web):
#   INTERVALS_ICU_API_KEY, ATHLETE_ID, DATABASE_URL, GCP_SERVICE_ACCOUNT
#
# Opcional:
#   FISIOLOGIA_LOTE       (default 10)
#   FISIOLOGIA_DATA_CORTE (default 2024-01-01)
#   GDRIVE_FOLDER_ID       (default = mesma pasta de correlacoes.db)
#
# ANTES DA PRIMEIRA EXECUÇÃO EM MASSA:
#   python fisiologia_worker.py --debug <um_activity_id_recente>
# para confirmar que o parsing de /activity/{id}/intervals está a apanhar
# os campos certos. Se não bater certo, ajustar as constantes CHAVES_*
# no topo do ficheiro — o resto do código não precisa de mudar.
