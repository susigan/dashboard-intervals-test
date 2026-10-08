"""drive_db_moxy_vst.py — persiste moxy_vst_historico.db no Google Drive.

Fonte canónica e permanente para todos os dados MOXY/VST:
  - activity_interval_rpe (RPE dos intervalos)
  - moxy_activities / vst_activities
  - activity_streams (dados brutos)
  - vst_intervals / vst_conjuntos / vst_results
  - moxy_analyses

Princípio de não-destruição:
  Nenhuma função aqui faz DROP, DELETE ou recria tabelas.
  Apenas CREATE TABLE IF NOT EXISTS, ALTER TABLE ADD COLUMN,
  INSERT OR REPLACE / INSERT OR IGNORE / UPDATE seletivo.

Fluxo de vida do DB:
  1. Utilizador baixa moxy_vst_historico.db via /api/moxy/vst/db/download
  2. Utilizador faz upload manual para a pasta do Google Drive
  3. container reinicia → get_moxy_vst_conn() → download() do Drive
  4. Todos os dados históricos voltam intactos

Regra de upload:
  Chamar upload_moxy_vst() após cada commit() importante.
  upload() nunca é silencioso — sempre loga ok/falha.
"""

import os
import json
import sqlite3
import datetime

from moxy_vst_schema import aplicar_schema

_DB_NAME   = "moxy_vst_historico.db"
_LOCAL_DB  = f"/tmp/{_DB_NAME}"

# Mesma pasta do Drive onde vivem os outros .db do projecto.
# Service account precisa de acesso de Editor a esta pasta.
_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "11oXQPkFrG6ZBCsvjDqb8RAiE_VfwBSfV")

_SCOPES = ["https://www.googleapis.com/auth/drive"]

_TAG = "[MOXY_VST_DB]"


# ─── Credenciais / Drive ──────────────────────────────────────────────────────

def _credenciais():
    from google.oauth2.service_account import Credentials
    raw = os.getenv("GCP_SERVICE_ACCOUNT", "").strip()
    if not raw:
        raise RuntimeError("GCP_SERVICE_ACCOUNT nao configurada")
    return Credentials.from_service_account_info(json.loads(raw), scopes=_SCOPES)


def _email_sa():
    try:
        return json.loads(os.getenv("GCP_SERVICE_ACCOUNT", "{}")).get("client_email")
    except Exception:
        return None


def _drive_svc():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_credenciais())


def _find_db_id(svc):
    """(file_id | None, erro | None)"""
    try:
        r = svc.files().list(
            q=f"name='{_DB_NAME}' and '{_FOLDER_ID}' in parents and trashed=false",
            fields="files(id, size, modifiedTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = r.get("files", [])
        return (files[0]["id"], None) if files else (None, None)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ─── Integridade ──────────────────────────────────────────────────────────────

def _verificar_integridade(caminho):
    """(ok, detalhe). Nunca escreve nada."""
    try:
        cn = sqlite3.connect(caminho)
        r  = cn.execute("PRAGMA integrity_check").fetchone()
        cn.close()
        if r and r[0] == "ok":
            return True, "ok"
        return False, str(r[0]) if r else "integrity_check sem resposta"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ─── Download ─────────────────────────────────────────────────────────────────

def download():
    """(ok, detalhe). Baixa moxy_vst_historico.db do Drive para /tmp.

    Descarrega para ficheiro temporário e só promove a _LOCAL_DB após
    confirmar integridade — evita .db parcial que bloqueia chamadas futuras.
    """
    try:
        from googleapiclient.http import MediaIoBaseDownload
        svc = _drive_svc()
        file_id, erro = _find_db_id(svc)
        if erro:
            return False, f"falha a procurar ficheiro: {erro}"
        if not file_id:
            return False, (
                f"{_DB_NAME} nao existe no Drive ainda. "
                f"Baixe o DB vazio em /api/moxy/vst/db/download, "
                f"faca upload manual para a pasta {_FOLDER_ID} e reinicie."
            )
        req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        tmp = _LOCAL_DB + ".partial"
        with open(tmp, "wb") as f:
            dl = MediaIoBaseDownload(f, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
        ok_i, det_i = _verificar_integridade(tmp)
        if not ok_i:
            try: os.remove(tmp)
            except OSError: pass
            return False, f"download corrompido ({det_i}) — nao promovido"
        os.replace(tmp, _LOCAL_DB)
        print(f"{_TAG} download concluido (file_id={file_id})")
        return True, f"descarregado (file_id={file_id})"
    except Exception as e:
        det = f"{type(e).__name__}: {e}"
        print(f"{_TAG} download falhou: {det}")
        return False, det


# ─── Upload ───────────────────────────────────────────────────────────────────

def upload():
    """(ok, detalhe). Sobe moxy_vst_historico.db para o Drive.

    Sempre loga o resultado — nunca silencioso.

    NOTA: service accounts nao podem criar ficheiros novos no Drive
    (sem quota propria). Se o ficheiro ainda nao existe, o utilizador
    precisa de fazer o upload manual inicial de um .db vazio.
    A partir dai, updates sao feitos por esta funcao sem quota.
    """
    if not os.path.exists(_LOCAL_DB):
        msg = "sem ficheiro local para subir"
        print(f"{_TAG} upload falhou: {msg}")
        return False, msg
    ok_i, det_i = _verificar_integridade(_LOCAL_DB)
    if not ok_i:
        msg = f"ficheiro local corrompido ({det_i}) — upload recusado"
        print(f"{_TAG} upload falhou: {msg}")
        return False, msg
    print(f"{_TAG} upload iniciado")
    try:
        from googleapiclient.http import MediaFileUpload
        svc = _drive_svc()
        file_id, erro = _find_db_id(svc)
        if erro:
            msg = f"falha a procurar ficheiro antes de subir: {erro}"
            print(f"{_TAG} upload falhou: {msg}")
            return False, msg
        media = MediaFileUpload(_LOCAL_DB, mimetype="application/x-sqlite3",
                                resumable=False)
        # Atualizar metadados de sync antes de subir
        try:
            agora = datetime.datetime.now().isoformat(timespec='seconds')
            cn = sqlite3.connect(_LOCAL_DB)
            cn.execute(
                "INSERT INTO db_metadata(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                ('last_successful_upload_at', agora, agora)
            )
            cn.commit(); cn.close()
        except Exception:
            pass
        if file_id:
            svc.files().update(fileId=file_id, media_body=media,
                               supportsAllDrives=True).execute()
            print(f"{_TAG} upload concluido (update file_id={file_id})")
            return True, f"actualizado (file_id={file_id})"
        else:
            # Tentativa de criar — pode falhar se service account sem quota
            res = svc.files().create(
                body={"name": _DB_NAME, "parents": [_FOLDER_ID]},
                media_body=media, supportsAllDrives=True, fields="id",
            ).execute()
            print(f"{_TAG} upload concluido (criado file_id={res.get('id')})")
            return True, f"criado (file_id={res.get('id')})"
    except Exception as e:
        det = f"{type(e).__name__}: {e}"
        if 'storageQuotaExceeded' in det or 'storage quota' in det.lower():
            det = (
                f"{_DB_NAME} nao existe no Drive ({_FOLDER_ID}) e a service account "
                f"({_email_sa()}) nao pode cria-lo (sem quota propria). "
                f"SOLUCAO: baixe o DB vazio em /api/moxy/vst/db/download e "
                f"faca upload manual para a pasta do Drive. Depois desta "
                f"primeira vez, uploads automaticos voltam a funcionar."
            )
        print(f"{_TAG} upload falhou: {det}")
        return False, det


# ─── Conexão principal ────────────────────────────────────────────────────────

def get_moxy_vst_conn():
    """Conexão sqlite3 pronta a usar ao moxy_vst_historico.db.

    Garante:
    1. Ficheiro existe localmente (download se preciso)
    2. Schema aplicado (CREATE IF NOT EXISTS, nunca DROP)
    3. Auto-recuperação de .db corrompido

    Retorna sqlite3.Connection ou lança RuntimeError se o DB
    nao existe nem no Drive (primeira execucao antes do upload manual).
    """
    # Auto-recuperação de .db corrompido
    if os.path.exists(_LOCAL_DB):
        ok_i, _ = _verificar_integridade(_LOCAL_DB)
        if not ok_i:
            print(f"{_TAG} {_LOCAL_DB} corrompido — a apagar e a re-descarregar")
            try: os.remove(_LOCAL_DB)
            except OSError: pass

    if not os.path.exists(_LOCAL_DB):
        ok_dl, det_dl = download()
        if not ok_dl:
            # DB nao existe no Drive: criar localmente para que a app
            # funcione, mas avisar que o utilizador precisa de fazer
            # o upload manual inicial.
            print(f"{_TAG} download falhou ({det_dl}) — criar DB local vazio. "
                  f"Baixe via /api/moxy/vst/db/download e faca upload para o Drive.")
            _criar_db_local_vazio()

    conn = sqlite3.connect(_LOCAL_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    aplicar_schema(conn)
    return conn


def _criar_db_local_vazio():
    """Cria o .db local com o schema completo mas sem dados."""
    conn = sqlite3.connect(_LOCAL_DB)
    aplicar_schema(conn)
    conn.close()
    print(f"{_TAG} DB local criado em {_LOCAL_DB} (vazio, aguarda upload manual para Drive)")


# ─── Utilitários de escrita (UPSERT seguro) ───────────────────────────────────

def upsert_rpe(conn, activity_id, start_time, rpe,
               end_time=None, interval_index=None,
               interval_type=None, elapsed_time=None,
               source='manual'):
    """Grava ou actualiza RPE. Nunca apaga. Nunca converte ausente em 0.

    rpe=None + source='deleted' → apagado explicitamente pelo utilizador
    rpe=1..10 → valor real
    """
    agora = datetime.datetime.now().isoformat(timespec='seconds')
    rpe_status = 'deleted' if (rpe is None and source == 'deleted') else \
                 'recorded' if rpe is not None else 'not_recorded'
    conn.execute(
        """INSERT INTO activity_interval_rpe
           (activity_id, start_time, end_time, interval_index,
            interval_type, elapsed_time, rpe, rpe_status, source,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(activity_id, start_time) DO UPDATE SET
             end_time      = COALESCE(excluded.end_time, end_time),
             interval_index= COALESCE(excluded.interval_index, interval_index),
             interval_type = COALESCE(excluded.interval_type, interval_type),
             elapsed_time  = COALESCE(excluded.elapsed_time, elapsed_time),
             rpe           = excluded.rpe,
             rpe_status    = excluded.rpe_status,
             source        = excluded.source,
             updated_at    = excluded.updated_at
        """,
        (str(activity_id), float(start_time), end_time, interval_index,
         interval_type, elapsed_time, rpe, rpe_status, source, agora, agora)
    )


def get_rpe(conn, activity_id, start_time):
    """Devolve (rpe, rpe_status, source) ou (None, 'not_found', None)."""
    row = conn.execute(
        "SELECT rpe, rpe_status, source FROM activity_interval_rpe "
        "WHERE activity_id=? AND start_time=?",
        (str(activity_id), float(start_time))
    ).fetchone()
    if row is None:
        return None, 'not_found', None
    return row[0], row[1], row[2]


def resolver_rpe(conn, activity_id, start_time):
    """Resolver canónico de RPE para o MOXY_VST_DB.

    Retorna (rpe_int | None, fonte_str):
      ('recorded', rpe=1..10) → (rpe, 'new')
      ('deleted')             → (None, 'deleted')
      ('not_found')           → (None, 'absent')
    """
    rpe, status, source = get_rpe(conn, activity_id, start_time)
    if status == 'not_found':
        return None, 'absent'
    if status == 'deleted' or rpe is None:
        return None, 'deleted'
    return rpe, 'new'


# ─── Diagnóstico ─────────────────────────────────────────────────────────────

def upsert_activity(conn, activity_id, kind, name=None, date=None,
                    sport=None, tag=None, raw_metadata=None,
                    intervals_available=True):
    """Grava ou actualiza uma atividade MOXY ou VST.

    kind: 'moxy' → moxy_activities | 'vst' → vst_activities
    intervals_available=False: atividade desapareceu da Intervals.icu mas
      o histórico é preservado — nunca apagado.
    UPSERT seguro: nunca apaga, nunca faz DELETE.
    """
    agora = datetime.datetime.now().isoformat(timespec='seconds')
    table = 'moxy_activities' if kind == 'moxy' else 'vst_activities'
    conn.execute(
        f"""INSERT INTO {table}
               (activity_id, activity_name, activity_date, sport, tag,
                raw_metadata_json, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(activity_id) DO UPDATE SET
             activity_name   = COALESCE(excluded.activity_name, activity_name),
             activity_date   = COALESCE(excluded.activity_date, activity_date),
             sport           = COALESCE(excluded.sport, sport),
             tag             = COALESCE(excluded.tag, tag),
             raw_metadata_json = COALESCE(excluded.raw_metadata_json, raw_metadata_json),
             updated_at      = excluded.updated_at
        """,
        (str(activity_id), name, date, sport, tag,
         raw_metadata if isinstance(raw_metadata, str)
         else (json.dumps(raw_metadata) if raw_metadata else None),
         agora, agora)
    )


def upsert_stream(conn, activity_id, stream_name, stream_data):
    """Grava ou actualiza UM stream de uma atividade.

    stream_name: chave do stream (ex: 'watts', 'heartrate', 'smo2', ...)
    stream_data: lista, dict ou qualquer valor serializável em JSON.
    Arquitetura dinâmica: nenhum DDL necessário para novos tipos de stream.
    UPSERT: se existir, actualiza; se não existir, insere.
    NUNCA apaga streams existentes.
    """
    agora = datetime.datetime.now().isoformat(timespec='seconds')
    raw = (stream_data if isinstance(stream_data, str)
           else json.dumps(stream_data, ensure_ascii=False))
    conn.execute(
        """INSERT INTO activity_streams (activity_id, stream_name, stream_json, updated_at)
           VALUES (?,?,?,?)
           ON CONFLICT(activity_id, stream_name) DO UPDATE SET
             stream_json = excluded.stream_json,
             updated_at  = excluded.updated_at
        """,
        (str(activity_id), str(stream_name), raw, agora)
    )


def _parse_streams_api(raw):
    """Converte a resposta bruta da API /activity/{id}/streams
    para {stream_name: data} independentemente do formato devolvido.

    A Intervals.icu pode devolver:
      A) Lista directa: [{type: 'heartrate', data: [...]}, ...]
      B) Wrapper dict:  {'streams': [{type:..., data:...}, ...], ...}
      C) Dict directo:  {'heartrate': [...], 'watts': [...]}   (improvável)

    Retorna sempre {stream_name: data_original_completo}.
    O 'data_original_completo' preserva o sub-dict completo do stream
    (não apenas o campo 'data') para não perder metadados futuros.
    Nunca filtra por nome — zero whitelist.
    """
    if not raw:
        return {}
    # Formato B: wrapper dict com chave 'streams' ou 'content'
    lista = raw
    if isinstance(raw, dict):
        if 'streams' in raw or 'content' in raw:
            lista = raw.get('streams') or raw.get('content') or []
        else:
            # Formato C: já é {name: data}
            return {k: v for k, v in raw.items() if v is not None}
    # Formato A (e B após extrair lista): lista de dicts com 'type'/'name' e 'data'
    result = {}
    for st in (lista or []):
        if not isinstance(st, dict):
            continue
        name = st.get('type') or st.get('name')
        if not name:
            continue
        # Guardar o sub-dict completo, não só st['data']
        # Assim: {'type':'heartrate','data':[...],'unit':'bpm',...} tudo preservado
        result[str(name)] = st
    return result


def upsert_all_streams(conn, activity_id, streams_raw):
    """Grava TODOS os streams de uma atividade a partir da resposta bruta da API.

    streams_raw: resposta directa de icu_get('/activity/{id}/streams')
      (lista, wrapper dict ou dict) — parseado internamente por _parse_streams_api.

    Regras:
      - Zero whitelist: qualquer chave retornada pela API é guardada.
      - Streams existentes → UPDATE.
      - Streams novos → INSERT.
      - Streams ausentes numa chamada → NÃO apagados (preservação histórica).

    Retorna (inserted, updated, preserved).
    """
    parsed = _parse_streams_api(streams_raw)
    existing = set(r[0] for r in conn.execute(
        "SELECT stream_name FROM activity_streams WHERE activity_id=?",
        (str(activity_id),)).fetchall())

    inserted = updated = 0
    for name, data in parsed.items():
        upsert_stream(conn, activity_id, name, data)
        if name in existing:
            updated += 1
        else:
            inserted += 1
    preserved = len(existing) - updated  # streams que já existiam mas não vieram agora
    return inserted, updated, preserved


def get_streams(conn, activity_id):
    """Devolve dict {stream_name: data_parsed} para uma atividade.
    Retorna o que está no DB — nunca vai à API.
    """
    rows = conn.execute(
        "SELECT stream_name, stream_json FROM activity_streams WHERE activity_id=?",
        (str(activity_id),)
    ).fetchall()
    result = {}
    for name, raw in rows:
        try:
            result[name] = json.loads(raw)
        except Exception:
            result[name] = raw
    return result


def upsert_vst_interval(conn, vst_activity_id, interval_index, grupo=None,
                         start_time=None, end_time=None, duration_s=None,
                         power_w=None, heart_rate_bpm=None, respiration=None,
                         smo2_pct=None, thb_gdl=None, dfa1=None,
                         raw_interval=None):
    """Grava ou actualiza um intervalo VST. UPSERT por (activity_id, interval_index)."""
    agora = datetime.datetime.now().isoformat(timespec='seconds')
    raw = (raw_interval if isinstance(raw_interval, str)
           else (json.dumps(raw_interval) if raw_interval else None))
    conn.execute(
        """INSERT INTO vst_intervals
               (activity_id, interval_index, grupo, start_time, end_time,
                duration_s, power_w, heart_rate_bpm, respiration,
                smo2_pct, thb_gdl, dfa1, raw_interval_json, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(activity_id, interval_index) DO UPDATE SET
             grupo          = COALESCE(excluded.grupo, grupo),
             start_time     = COALESCE(excluded.start_time, start_time),
             end_time       = COALESCE(excluded.end_time, end_time),
             duration_s     = COALESCE(excluded.duration_s, duration_s),
             power_w        = COALESCE(excluded.power_w, power_w),
             heart_rate_bpm = COALESCE(excluded.heart_rate_bpm, heart_rate_bpm),
             smo2_pct       = COALESCE(excluded.smo2_pct, smo2_pct),
             thb_gdl        = COALESCE(excluded.thb_gdl, thb_gdl),
             dfa1           = COALESCE(excluded.dfa1, dfa1),
             raw_interval_json = COALESCE(excluded.raw_interval_json, raw_interval_json),
             updated_at     = excluded.updated_at
        """,
        (str(vst_activity_id), interval_index, grupo, start_time, end_time,
         duration_s, power_w, heart_rate_bpm, respiration,
         smo2_pct, thb_gdl, dfa1, raw, agora)
    )


def upsert_conjunto(conn, moxy_activity_id, vst_activity_id,
                    bp1_status=None, bp2_status=None,
                    dia1_bp1_w=None, dia2_bp1_w=None,
                    dia1_bp2_w=None, dia2_bp2_w=None,
                    recovery_bp1_status=None, recovery_bp2_status=None):
    """Grava ou actualiza um conjunto MOXY × VST. Devolve o id do registo."""
    agora = datetime.datetime.now().isoformat(timespec='seconds')
    conn.execute(
        """INSERT INTO vst_conjuntos
               (moxy_activity_id, vst_activity_id,
                bp1_status, bp2_status,
                dia1_bp1_w, dia2_bp1_w, dia1_bp2_w, dia2_bp2_w,
                recovery_bp1_status, recovery_bp2_status,
                created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(moxy_activity_id, vst_activity_id) DO UPDATE SET
             bp1_status           = COALESCE(excluded.bp1_status, bp1_status),
             bp2_status           = COALESCE(excluded.bp2_status, bp2_status),
             dia1_bp1_w           = COALESCE(excluded.dia1_bp1_w, dia1_bp1_w),
             dia2_bp1_w           = COALESCE(excluded.dia2_bp1_w, dia2_bp1_w),
             dia1_bp2_w           = COALESCE(excluded.dia1_bp2_w, dia1_bp2_w),
             dia2_bp2_w           = COALESCE(excluded.dia2_bp2_w, dia2_bp2_w),
             recovery_bp1_status  = COALESCE(excluded.recovery_bp1_status, recovery_bp1_status),
             recovery_bp2_status  = COALESCE(excluded.recovery_bp2_status, recovery_bp2_status),
             updated_at           = excluded.updated_at
        """,
        (str(moxy_activity_id), str(vst_activity_id),
         bp1_status, bp2_status,
         dia1_bp1_w, dia2_bp1_w, dia1_bp2_w, dia2_bp2_w,
         recovery_bp1_status, recovery_bp2_status,
         agora, agora)
    )
    row = conn.execute(
        "SELECT id FROM vst_conjuntos WHERE moxy_activity_id=? AND vst_activity_id=?",
        (str(moxy_activity_id), str(vst_activity_id))
    ).fetchone()
    return row[0] if row else None


def insert_resultado(conn, conjunto_id, resultado_json=None,
                     validacao_fisiologica_json=None, bpm_vst_validacao_json=None,
                     bp1_w=None, bp2_w=None, bp1_bpm=None, bp2_bpm=None,
                     bp1_json=None, bp2_json=None,
                     comparacao_rpe_bp1=None, comparacao_rpe_bp2=None,
                     limiter_sintese=None, recuperacao_final_dia2=None,
                     analysis_version=None, analysis_hash=None,
                     rpe_fisiologia_json=None):
    """Insere uma NOVA versão do resultado — nunca sobrescreve versões anteriores.

    O número de versão é calculado automaticamente como MAX(version)+1.
    Regra anti-destruição: se algum campo for None mas o resultado anterior
    tinha dados, a nova versão mantém None (o histórico fica na versão anterior).
    """
    agora = datetime.datetime.now().isoformat(timespec='seconds')

    def _j(v):
        if v is None: return None
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)

    # Calcular próxima versão
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM vst_results WHERE vst_conjunto_id=?",
        (conjunto_id,)
    ).fetchone()
    next_version = (row[0] or 0) + 1

    rpe_bp1_ok = int(bool(comparacao_rpe_bp1 or
                     (resultado_json and 'rpe' in str(resultado_json))))
    rpe_bp2_ok = int(bool(comparacao_rpe_bp2))

    conn.execute(
        """INSERT INTO vst_results
               (vst_conjunto_id, version, analysis_version, analysis_hash,
                analyzed_at, resultado_json, validacao_fisiologica_json,
                bpm_vst_validacao_json, bp1_w, bp2_w, bp1_bpm, bp2_bpm,
                bp1_json, bp2_json, comparacao_rpe_bp1, comparacao_rpe_bp2,
                limiter_sintese, recuperacao_final_dia2,
                rpe_bp1_disponivel, rpe_bp2_disponivel,
                rpe_fisiologia_json, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (conjunto_id, next_version, analysis_version, analysis_hash,
         agora, _j(resultado_json), _j(validacao_fisiologica_json),
         _j(bpm_vst_validacao_json), bp1_w, bp2_w, bp1_bpm, bp2_bpm,
         _j(bp1_json), _j(bp2_json), _j(comparacao_rpe_bp1), _j(comparacao_rpe_bp2),
         _j(limiter_sintese), _j(recuperacao_final_dia2),
         rpe_bp1_ok, rpe_bp2_ok, _j(rpe_fisiologia_json), agora)
    )
    return next_version


def upsert_moxy_analysis(conn, activity_id, bp1_w=None, bp1_bpm=None,
                         bp2_w=None, bp2_bpm=None, json_completo=None,
                         analysis_version=None, analyzed_at=None):
    """Grava a análise MOXY canônica em moxy_analyses (única escrita viva).

    Versionamento: MAX(version)+1, mas só quando algo mudou em relação à
    versão mais recente (analysis_version, bps, bpm ou json_completo).
    Se nada mudou, não cria versão nova. Devolve (version, criou_nova).
    """
    def _j(v):
        if v is None: return None
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)

    atual = conn.execute(
        "SELECT version, bp1_w, bp1_bpm, bp2_w, bp2_bpm, json_completo, "
        "       analysis_version "
        "FROM moxy_analyses WHERE activity_id=? "
        "ORDER BY version DESC LIMIT 1",
        (str(activity_id),)).fetchone()

    novo = (bp1_w, bp1_bpm, bp2_w, bp2_bpm, _j(json_completo), analysis_version)
    if atual is not None and tuple(atual[1:]) == novo:
        return atual[0], False

    next_version = ((atual[0] if atual else 0) or 0) + 1
    conn.execute(
        """INSERT INTO moxy_analyses
               (activity_id, version, bp1_w, bp1_bpm, bp2_w, bp2_bpm,
                json_completo, analysis_version, analyzed_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (str(activity_id), next_version, bp1_w, bp1_bpm, bp2_w, bp2_bpm,
         _j(json_completo), analysis_version, analyzed_at))
    return next_version, True


def get_resultado_atual(conn, moxy_activity_id, vst_activity_id):
    """Devolve o resultado mais recente (maior version) para um conjunto.
    Retorna dict ou None.
    """
    row = conn.execute(
        """SELECT r.*
           FROM vst_results r
           JOIN vst_conjuntos c ON r.vst_conjunto_id = c.id
           WHERE c.moxy_activity_id=? AND c.vst_activity_id=?
           ORDER BY r.version DESC LIMIT 1
        """,
        (str(moxy_activity_id), str(vst_activity_id))
    ).fetchone()
    if not row:
        return None
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM vst_results LIMIT 0").description or []]
    if not cols:
        # fallback via cursor description
        conn.execute("SELECT * FROM vst_results LIMIT 0")
    # Usar sqlite3 row_factory
    try:
        return dict(row)
    except Exception:
        return None


def diagnostico():
    """Estado completo do moxy_vst_historico.db. Não altera nada."""
    import os
    info = {
        'db_name':    _DB_NAME,
        'local_path': _LOCAL_DB,
        'local_existe': os.path.exists(_LOCAL_DB),
        'local_bytes': os.path.getsize(_LOCAL_DB) if os.path.exists(_LOCAL_DB) else None,
        'folder_id':  _FOLDER_ID,
        'service_account': _email_sa(),
    }

    try:
        svc = _drive_svc()
        info['credenciais_ok'] = True
        file_id, erro = _find_db_id(svc)
        info['drive_encontrado'] = file_id is not None
        info['drive_file_id']    = file_id
        info['drive_erro']       = erro
    except Exception as e:
        info['credenciais_ok'] = False
        info['drive_erro']     = f"{type(e).__name__}: {e}"
        return info

    if os.path.exists(_LOCAL_DB):
        try:
            conn = sqlite3.connect(_LOCAL_DB)
            conn.row_factory = sqlite3.Row
            aplicar_schema(conn)

            def _count(t):
                return conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

            info['contagens'] = {
                'moxy_activities':       _count('moxy_activities'),
                'vst_activities':        _count('vst_activities'),
                'activity_interval_rpe': _count('activity_interval_rpe'),
                'vst_intervals':         _count('vst_intervals'),
                'vst_conjuntos':         _count('vst_conjuntos'),
                'vst_results':           _count('vst_results'),
                'moxy_analyses':         _count('moxy_analyses'),
                'activity_streams':      _count('activity_streams'),
            }

            # Stream keys únicas
            stream_keys = [r[0] for r in conn.execute(
                "SELECT DISTINCT stream_name FROM activity_streams ORDER BY stream_name"
            ).fetchall()]
            info['stream_keys'] = stream_keys
            info['activities_with_streams'] = conn.execute(
                "SELECT COUNT(DISTINCT activity_id) FROM activity_streams"
            ).fetchone()[0]

            # Metadados de sync
            meta = {r[0]: r[1] for r in conn.execute(
                "SELECT key, value FROM db_metadata"
            ).fetchall()}
            info['schema_version']              = meta.get('schema_version')
            info['last_successful_upload_at']   = meta.get('last_successful_upload_at')
            info['db_created_at']               = meta.get('db_created_at')
            info['last_rpe_update_at']          = conn.execute(
                "SELECT MAX(updated_at) FROM activity_interval_rpe"
            ).fetchone()[0]
            info['last_stream_sync_at'] = conn.execute(
                "SELECT MAX(updated_at) FROM activity_streams"
            ).fetchone()[0]

            # stream_sync_status — diagnóstico explícito
            _n_streams = info['contagens']['activity_streams']
            _last_sync  = info['last_stream_sync_at']
            _last_meta  = {r[0]: r[1] for r in conn.execute(
                "SELECT key, value FROM db_metadata WHERE key LIKE 'stream_sync%'"
            ).fetchall()}
            if _n_streams > 0:
                info['stream_sync_status'] = 'success'
                info['stream_sync_message'] = (
                    f"{_n_streams} streams em {info['activities_with_streams']} atividades. "
                    f"Última sync: {_last_sync}.")
            elif _last_meta.get('stream_sync_last_status') == 'api_empty':
                info['stream_sync_status'] = 'api_empty'
                info['stream_sync_message'] = 'API retornou 0 streams na última chamada.'
            elif _last_meta.get('stream_sync_last_status') == 'api_error':
                info['stream_sync_status'] = 'api_error'
                info['stream_sync_message'] = (
                    f"Erro na última chamada: {_last_meta.get('stream_sync_last_error','?')}")
            elif _last_meta.get('stream_sync_last_status') == 'persist_error':
                info['stream_sync_status'] = 'persist_error'
                info['stream_sync_message'] = (
                    f"Erro ao persistir: {_last_meta.get('stream_sync_last_error','?')}")
            else:
                info['stream_sync_status'] = 'never_run'
                info['stream_sync_message'] = (
                    'Sincronização de streams nunca executada. '
                    'Execute COMPARAR/SINCRONIZAR numa verificação MOXY×VST.')

            # Detalhe por atividade
            _acts_detail = conn.execute(
                """SELECT s.activity_id,
                          COUNT(*) as stream_count,
                          GROUP_CONCAT(s.stream_name, ',') as keys,
                          CASE WHEN m.activity_id IS NOT NULL THEN 'moxy'
                               WHEN v.activity_id IS NOT NULL THEN 'vst'
                               ELSE 'unknown' END as activity_type
                   FROM activity_streams s
                   LEFT JOIN moxy_activities m ON s.activity_id = m.activity_id
                   LEFT JOIN vst_activities  v ON s.activity_id = v.activity_id
                   GROUP BY s.activity_id"""
            ).fetchall()
            info['activities_with_streams_detail'] = [
                {'activity_id': r[0], 'stream_count': r[1],
                 'stream_keys': sorted(r[2].split(',')) if r[2] else [],
                 'activity_type': r[3]}
                for r in _acts_detail
            ]

            conn.close()
        except Exception as e:
            info['erro_leitura'] = f"{type(e).__name__}: {e}"
    return info
