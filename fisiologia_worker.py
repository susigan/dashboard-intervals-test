"""fisiologia_worker.py — Perfil metabólico por lag/recovery + patamar.

O QUE FAZ
---------
Para cada atividade cíclica (Bike/Row/Ski/Run) com potência, calcula DUAS
dimensões, de fontes diferentes e independentes uma da outra:

  DIMENSÃO TEMPO (lag_*/rec_*) — precisa de streams já guardados no
  Postgres (watts, heartrate, smo2, thb, respiration) via db.get_streams():
    1. Pede à API do Intervals.icu só os INTERVALOS/LAPS da atividade
       (endpoint leve, não é stream) — para saber onde começa/acaba cada
       WORK e REC.
    2. Para cada intervalo WORK: mede quanto tempo (s) cada métrica leva a
       percorrer 50/75/90% da excursão entre a baseline (antes do WORK) e
       o patamar estável (fim do WORK).
    3. Para o REC a seguir (se existir): mede quanto tempo leva a voltar
       50/75% do caminho até à baseline original.

  DIMENSÃO VALOR/PATAMAR (*_medio_work/*_medio_rec) — NÃO precisa de
  streams nenhuns, só da mesma resposta de /intervals: a Intervals.icu já
  calcula e devolve, por lap, a média de HR, SmO2, tHb, respiração E DFA1
  (average_dfa_a1) — usados directamente. É esta dimensão que dá a curva
  "a X watts, o esperado é Y" que serve de base ao perfil metabólico.

Por serem independentes: uma atividade sem streams carregados (ainda não
passou por /api/sync/streams) fica com lag_*/rec_* a None mas continua a
gravar os valores de patamar. Corre o sync de streams e reprocessa depois
para completar a dimensão tempo.

Grava 1 linha por intervalo em fisiologia_perfil.db (SQLite, Drive).

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

CONFIGURAÇÃO — CAMPOS CONFIRMADOS
-----------------------------------------
Os nomes de campos abaixo foram confirmados directamente no código já
existente em tabs/tab_detalhe.py (a página /activity/<id> já usa isto):

    d.intervals.icu_intervals = [
        {label, type, start_time, elapsed_time, distance,
         average_watts, max_watts, weighted_average_watts,

         average_heartrate, max_heartrate, average_cadence,
         intensity, joules, decoupling}, ...
    ]

IMPORTANTE: 'elapsed_time' aqui é a DURAÇÃO do intervalo (não um
timestamp absoluto) — bate com a coluna "DurW" que já vês na tua tabela
de calibração (ex.: intervalo 1 = 304s). t_fim = start_time + elapsed_time.

O campo 'type' da API NEM SEMPRE é fiável — confirmaste que às vezes
marca REST como WORK. Por isso este worker NÃO usa 'type' para decidir
o que é WORK/REC: classifica pela POTÊNCIA MÉDIA de cada intervalo
(maior "salto" na distribuição ordenada de average_watts separa os dois
grupos). 'type'/'label' só ficam guardados para debug/logging.
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
LOTE_WEB_MAX = int(os.getenv("FISIOLOGIA_LOTE_WEB_MAX", "8"))  # limite p/ pedido HTTP (timeout Railway)

DUR_MIN_WORK_S = 20     # intervalos mais curtos que isto são ignorados (ruído)
DUR_MIN_REC_S = 15
JANELA_BASELINE_S = 8   # segundos antes do WORK usados como baseline
FRAC_PLATEAU = 0.25     # fração final do intervalo usada como "patamar estável"

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
# INTERVALOS (laps) — busca à API + parsing com campos confirmados
# ══════════════════════════════════════════════════════════════════════════

def buscar_intervalos_api(activity_id):
    """(lista_intervalos, erro). Cada item: {tipo, label, t_ini, t_fim,
    watts_medio_api, hr_medio_api, smo2_medio_api, thb_medio_api,
    resp_medio_api, dfa1_medio_api}.

    Os *_medio_api vêm TODOS directos da resposta da API por intervalo —
    a Intervals.icu já calcula estas médias por lap, incluindo o DFA1
    (average_dfa_a1), sem precisar de processar nenhum stream.
    """
    data, erro = icu_get(f"/activity/{activity_id}/intervals")
    if erro:
        return None, erro

    if isinstance(data, dict) and isinstance(data.get('icu_intervals'), list):
        bruto = data['icu_intervals']
    elif isinstance(data, list):
        bruto = data
    elif isinstance(data, dict) and isinstance(data.get('intervals'), list):
        bruto = data['intervals']
    else:
        return None, f"schema inesperado: {str(data)[:200]}"

    out = []
    for item in bruto:
        if not isinstance(item, dict):
            continue
        t_ini = item.get('start_time')
        duracao = item.get('elapsed_time')
        if t_ini is None or duracao is None:
            continue
        t_ini = float(t_ini)
        t_fim = t_ini + float(duracao)
        out.append({
            'tipo': str(item.get('type') or '').lower(),
            'label': item.get('label'),
            't_ini': t_ini,
            't_fim': t_fim,
            'watts_medio_api': item.get('average_watts'),
            'hr_medio_api': item.get('average_heartrate'),
            'smo2_medio_api': item.get('average_smo2'),
            'thb_medio_api': item.get('average_thb'),
            'resp_medio_api': item.get('average_respiration'),
            'dfa1_medio_api': item.get('average_dfa_a1'),
        })
    return out, None



LIMIAR_QUEDA_REC = float(os.getenv("FISIOLOGIA_LIMIAR_QUEDA", "0.30"))  # 30%


def _limiar_gap(potencias):
    """Maior 'salto' na distribuição ordenada de potências — separa o
    grupo de baixa potência (REC/warmup/cooldown) do de alta (WORK).
    Robusto a contagens desiguais de WORK vs REC, ao contrário de usar
    simplesmente a mediana.
    """
    vs = sorted(p for p in potencias if p is not None)
    if len(vs) < 2:
        return (vs[0] - 1) if vs else 0.0
    gaps = [(vs[i + 1] - vs[i], i) for i in range(len(vs) - 1)]
    _, i = max(gaps)
    return (vs[i] + vs[i + 1]) / 2.0


def classificar_por_potencia(intervalos):
    """Marca cada intervalo com iv['_classe'] = 'work' | 'recovery' | 'ignorar'.

    NÃO usa o campo 'type' da API (pode estar errado — já visto REST
    marcado como WORK, e no caso de Row, TODOS os laps marcados "WORK"
    mesmo quando a potência é 0). Combina três sinais:

      1. SEM POTÊNCIA (average_watts = None): não é "sem dados a
         ignorar" — é o sinal MAIS FORTE de descanso que existe. Em
         Row/Ski, quando não há remada/patinada nenhuma no lap, a API
         nem calcula uma média (fica null) em vez de devolver 0. Isto
         classifica sempre como 'recovery'.

      2. GLOBAL: potência abaixo do maior "salto" na distribuição de
         toda a atividade -> candidato a REC. Bom para descansos longos
         e estáveis com ALGUMA potência residual.

      3. QUEDA RELATIVA (face ao intervalo ANTERIOR): se a potência cai
         >= LIMIAR_QUEDA_REC (30% por omissão) em relação ao intervalo
         imediatamente antes, classifica como REC mesmo que ainda tenha
         watts residuais.

    Um intervalo fica REC se qualquer um dos três sinais disparar.
    """
    potencias = [iv.get('watts_medio_api') for iv in intervalos
                if iv.get('watts_medio_api') is not None]
    limiar_global = _limiar_gap(potencias) if len(potencias) >= 2 else None

    anterior_watts = None
    for iv in intervalos:
        w = iv.get('watts_medio_api')

        if w is None:
            # Esforço nulo (nenhuma remada/pedalada no lap) -> REC quase
            # certo, independentemente de tudo o resto.
            iv['_classe'] = 'recovery'
            anterior_watts = 0.0
            continue

        baixo_global = (limiar_global is not None and w < limiar_global)

        queda_relativa = False
        if anterior_watts is not None and anterior_watts > 0:
            queda = (anterior_watts - w) / anterior_watts
            queda_relativa = queda >= LIMIAR_QUEDA_REC

        iv['_classe'] = 'recovery' if (baixo_global or queda_relativa) else 'work'
        anterior_watts = w

    return intervalos


def emparelhar_work_rec(intervalos):
    """[(work, rec_ou_None), ...] — rec é o intervalo seguinte SE for recovery.

    Espera que classificar_por_potencia() já tenha corrido (usa iv['_classe']).
    """
    pares = []
    i = 0
    n = len(intervalos)
    while i < n:
        if intervalos[i].get('_classe') == 'work':
            work = intervalos[i]
            rec = None
            if i + 1 < n and intervalos[i + 1].get('_classe') == 'recovery':
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
    """Retorna (n_intervalos_gravados, motivo_se_pulada).

    Duas fontes de dados INDEPENDENTES:
      - streams do Postgres -> lag_*/rec_* (timing). Se não existirem
        (ainda não foi feito /api/sync/streams para esta atividade), fica
        tudo None nesses campos, mas NÃO impede o resto.
      - médias por intervalo da API -> *_medio_work/*_medio_rec (patamar).
        Não depende de streams nenhuns, só do /activity/{id}/intervals.

    Assim dá para começar já a construir o perfil watts->valor mesmo em
    atividades sem streams carregados, e completar com lag/recovery mais
    tarde (correndo /api/sync/streams e reprocessando).
    """
    activity_id = str(activity.get('id'))
    modalidade = norm_tipo(activity.get('type'))
    data_str = str(activity.get('start_date_local', ''))[:10]

    duracao_total_s = activity.get('moving_time') or activity.get('elapsed_time')
    if not duracao_total_s:
        return 0, 'sem duracao'

    intervalos, erro = buscar_intervalos_api(activity_id)
    if erro:
        return 0, f'erro API intervals: {erro}'
    if not intervalos:
        return 0, 'sem intervalos/laps marcados'

    classificar_por_potencia(intervalos)
    pares = emparelhar_work_rec(intervalos)
    if not pares:
        return 0, 'nenhum WORK identificado'

    streams, meta = db.get_streams(activity_id)
    tem_streams = bool(streams) and STREAM_WATTS in (streams or {})

    # tempos + valores por métrica (uma vez por atividade, reaproveitados
    # para todos os intervalos) — só se houver streams; senão ficam vazios
    # e lag_*/rec_* saem None (mas *_medio_work/*_medio_rec da API continuam
    # a ser gravados na mesma).
    t_watts = _tempos_do_stream(streams.get(STREAM_WATTS), duracao_total_s) if tem_streams else np.array([])
    v_watts = _valores_float(streams.get(STREAM_WATTS)) if tem_streams else np.array([])

    tem_hr = tem_streams and STREAM_HR in streams
    t_hr = _tempos_do_stream(streams.get(STREAM_HR), duracao_total_s) if tem_hr else None
    v_hr = _valores_float(streams.get(STREAM_HR)) if tem_hr else None

    tem_smo2 = tem_streams and STREAM_SMO2 in streams
    t_smo2 = _tempos_do_stream(streams.get(STREAM_SMO2), duracao_total_s) if tem_smo2 else None
    v_smo2 = _valores_float(streams.get(STREAM_SMO2)) if tem_smo2 else None

    tem_thb = tem_streams and STREAM_THB in streams
    t_thb = _tempos_do_stream(streams.get(STREAM_THB), duracao_total_s) if tem_thb else None
    v_thb = _valores_float(streams.get(STREAM_THB)) if tem_thb else None

    resp_key = next((k for k in STREAMS_RESP_CANDIDATOS if tem_streams and k in streams), None)
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
            'tem_dfa1': int(work.get('dfa1_medio_api') is not None),
            'valido': 1, 'motivo_invalido': None, 'criado_em': now,
            # Valor/patamar: direto da API, sem processar streams — é a
            # base da curva "a X watts, esperar Y" que vais construir depois.
            'hr_medio_work': work.get('hr_medio_api'),
            'hr_medio_rec': rec.get('hr_medio_api') if rec else None,
            'smo2_medio_work': work.get('smo2_medio_api'),
            'smo2_medio_rec': rec.get('smo2_medio_api') if rec else None,
            'thb_medio_work': work.get('thb_medio_api'),
            'thb_medio_rec': rec.get('thb_medio_api') if rec else None,
            'resp_medio_work': work.get('resp_medio_api'),
            'resp_medio_rec': rec.get('resp_medio_api') if rec else None,
            'dfa1_medio_work': work.get('dfa1_medio_api'),
            'dfa1_medio_rec': rec.get('dfa1_medio_api') if rec else None,
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

        tem_algum_valor_api = any(
            linha[k] is not None for k in
            ('hr_medio_work', 'smo2_medio_work', 'thb_medio_work',
             'resp_medio_work', 'dfa1_medio_work'))

        if not qualidade_ok and not tem_algum_valor_api:
            linha['valido'] = 0
            linha['motivo_invalido'] = (
                'sem lag calculavel pelos streams E sem valores medios da API')

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

def processar_lote(n=LOTE_PADRAO, retornar_resumo=False):
    conn = ddf.get_conn()
    lote = proximo_lote(conn, n)

    if not lote:
        conn.execute("""UPDATE fisiologia_progresso SET
                        concluido = 1, ultima_execucao = ? WHERE id = 1""",
                    (datetime.now().isoformat(timespec='seconds'),))
        conn.commit()
        ddf.upload()
        resumo = {'status': 'concluido',
                 'mensagem': 'Nada para processar — historico completo ate a data de corte.'}
        if retornar_resumo:
            return resumo
        print(resumo['mensagem'])
        return

    processadas = puladas = erros = 0
    detalhes = []
    for activity in lote:
        aid = str(activity.get('id'))
        try:
            n_gravados, motivo = processar_atividade(activity, conn)
            if motivo:
                puladas += 1
                detalhes.append({'activity_id': aid, 'status': 'pulada', 'motivo': motivo})
                if not retornar_resumo:
                    print(f"  [PULADA] {aid}: {motivo}")
            else:
                processadas += 1
                detalhes.append({'activity_id': aid, 'status': 'ok', 'intervalos_gravados': n_gravados})
                if not retornar_resumo:
                    print(f"  [OK] {aid}: {n_gravados} intervalos gravados")
        except Exception as e:
            erros += 1
            detalhes.append({'activity_id': aid, 'status': 'erro',
                             'erro': f'{type(e).__name__}: {e}'})
            if not retornar_resumo:
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

    resumo = {
        'status': 'lote_concluido',
        'processadas': processadas, 'puladas': puladas, 'erros': erros,
        'total_no_lote': len(lote),
        'detalhes': detalhes,
    }
    if retornar_resumo:
        return resumo

    print(f"\nLote concluido: {processadas} processadas, {puladas} puladas, "
          f"{erros} erros. {len(lote)} atividades no lote.")


def _amostra_bruta(bruto, n=3):
    try:
        if isinstance(bruto, dict) and isinstance(bruto.get('icu_intervals'), list):
            return bruto['icu_intervals'][:n]
        if isinstance(bruto, list):
            return bruto[:n]
        return str(bruto)[:500]
    except Exception:
        return None


def debug_dict(activity_id):
    """Versão JSON-friendly do debug — para expor via endpoint HTTP.

    Mostra: resposta bruta (amostra), o que o parser extraiu, a
    classificação WORK/REC por potência (SEM usar o campo 'type' da API),
    e os pares WORK->REC resultantes. Não grava nada no .db.
    """
    bruto, erro_api = icu_get(f"/activity/{activity_id}/intervals")
    intervalos, erro_parse = buscar_intervalos_api(activity_id)

    resultado = {
        'activity_id': activity_id,
        'erro_api': erro_api,
        'erro_parsing': erro_parse,
        'n_intervalos_parseados': len(intervalos) if intervalos else 0,
        'bruto_amostra': _amostra_bruta(bruto),
    }

    if intervalos:
        classificar_por_potencia(intervalos)
        anterior_watts = None
        intervalos_debug = []
        for iv in intervalos:
            w = iv.get('watts_medio_api')
            queda_pct = None
            if w is not None and anterior_watts is not None and anterior_watts > 0:
                queda_pct = round((anterior_watts - w) / anterior_watts * 100, 1)
            intervalos_debug.append({
                'tipo_api': iv['tipo'], 'label_api': iv.get('label'),
                'classe_calculada': iv.get('_classe'),
                't_ini_s': round(iv['t_ini'], 1), 't_fim_s': round(iv['t_fim'], 1),
                'dur_s': round(iv['t_fim'] - iv['t_ini'], 1),
                'watts_medio_api': w,
                'queda_pct_vs_anterior': queda_pct,
                'hr_medio_api': iv.get('hr_medio_api'),
            })
            anterior_watts = 0.0 if w is None else w
        resultado['intervalos'] = intervalos_debug
        pares = emparelhar_work_rec(intervalos)
        resultado['n_pares_work_rec'] = len(pares)
        resultado['pares'] = [
            {
                'work_dur_s': round(w['t_fim'] - w['t_ini'], 1),
                'work_watts': w.get('watts_medio_api'),
                'rec_dur_s': (round(r['t_fim'] - r['t_ini'], 1) if r else None),
                'rec_watts': (r.get('watts_medio_api') if r else None),
            }
            for w, r in pares
        ]

    # também tenta correr o processamento completo (sem gravar) para o
    # utilizador ver os lags calculados de verdade nesta atividade
    try:
        act, err_act = icu_get(f"/activity/{activity_id}")
        if act and not err_act:
            act['id'] = activity_id
            conn_temp = None
            import sqlite3
            conn_temp = sqlite3.connect(':memory:')
            from fisiologia_schema import aplicar_schema
            aplicar_schema(conn_temp)
            n_gravados, motivo = processar_atividade(act, conn_temp)
            if motivo:
                resultado['simulacao_processamento'] = {'status': 'pulada', 'motivo': motivo}
            else:
                linhas = conn_temp.execute(
                    "SELECT * FROM fisiologia_intervalos WHERE activity_id = ?",
                    (activity_id,)).fetchall()
                cols = [d[0] for d in conn_temp.execute(
                    "SELECT * FROM fisiologia_intervalos LIMIT 0").description]
                resultado['simulacao_processamento'] = {
                    'status': 'ok', 'n_linhas': len(linhas),
                    'linhas': [dict(zip(cols, l)) for l in linhas],
                }
    except Exception as e:
        resultado['simulacao_processamento'] = {'status': 'erro', 'erro': str(e)}

    return resultado


if __name__ == '__main__':
    if '--debug' in sys.argv:
        idx = sys.argv.index('--debug')
        activity_id = sys.argv[idx + 1]
        print(json.dumps(debug_dict(activity_id), indent=2, ensure_ascii=False, default=str))
    else:
        n = LOTE_PADRAO
        if '--n' in sys.argv:
            idx = sys.argv.index('--n')
            n = int(sys.argv[idx + 1])
        processar_lote(n)


# ══════════════════════════════════════════════════════════════════════════
# INSTRUÇÕES — Railway Cron Job (opcional, além do HTTP manual)
# ══════════════════════════════════════════════════════════════════════════
#
# Este ficheiro NÃO altera app.py por si só. Para o usar via HTTP (sem
# terminal, só browser), ver app_fisiologia_rotas_ADICIONAR.py — são 2
# rotas pequenas para colar no teu app.py via GitHub web.
#
# Para automatizar (correr sozinho todos os dias, sem precisares de abrir
# a URL manualmente), o Railway permite criar um "Cron Job" TAMBÉM pela
# interface web (não precisa de CLI): no projecto Railway, "New" ->
# "Cron Job" -> escolher o mesmo repo -> comando:
#
#     python fisiologia_worker.py --n 10
#
# agendamento (todos os dias às 04:00):
#
#     0 4 * * *
#
# Variáveis de ambiente — as MESMAS do serviço web já existente:
#   INTERVALS_ICU_API_KEY, ATHLETE_ID, DATABASE_URL, GCP_SERVICE_ACCOUNT
#
# Opcional:
#   FISIOLOGIA_LOTE        (default 10)
#   FISIOLOGIA_DATA_CORTE  (default 2024-01-01)
#   GDRIVE_FOLDER_ID       (default = mesma pasta de correlacoes.db)
