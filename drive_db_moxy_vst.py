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

def diagnostico():
    """Estado completo do moxy_vst_historico.db. Não altera nada."""
    import os
    info = {
        'db_name':              _DB_NAME,
        'local_path':           _LOCAL_DB,
        'local_existe':         os.path.exists(_LOCAL_DB),
        'local_bytes':          os.path.getsize(_LOCAL_DB) if os.path.exists(_LOCAL_DB) else None,
        'folder_id':            _FOLDER_ID,
        'service_account':      _email_sa(),
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
            aplicar_schema(conn)
            def _count(t):
                return conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            info['contagens'] = {
                'moxy_activities':      _count('moxy_activities'),
                'vst_activities':       _count('vst_activities'),
                'activity_interval_rpe': _count('activity_interval_rpe'),
                'vst_intervals':        _count('vst_intervals'),
                'vst_conjuntos':        _count('vst_conjuntos'),
                'vst_results':          _count('vst_results'),
                'moxy_analyses':        _count('moxy_analyses'),
                'activity_streams':     _count('activity_streams'),
            }
            meta = {r[0]: r[1] for r in conn.execute(
                "SELECT key, value FROM db_metadata").fetchall()}
            info['schema_version']            = meta.get('schema_version')
            info['last_successful_upload_at'] = meta.get('last_successful_upload_at')
            info['db_created_at']             = meta.get('db_created_at')
            conn.close()
        except Exception as e:
            info['erro_leitura'] = f"{type(e).__name__}: {e}"
    return info
