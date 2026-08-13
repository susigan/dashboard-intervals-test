"""drive_db_fisiologia.py — persiste fisiologia_perfil.db no Google Drive.

Segue o mesmo padrão de drive_db.py (Streamlit) mas sem dependência de
streamlit: credenciais vêm de GCP_SERVICE_ACCOUNT (variável de ambiente
com o JSON da service account), igual ao resto do dashboard-intervals.

Uso:
    import drive_db_fisiologia as ddf

    conn = ddf.get_conn()        # download (se preciso) + abre conexao local
    ... ler/escrever ...
    ddf.upload()                 # sobe o .db actualizado para o Drive

O .db fica em /tmp (efémero no Railway) — por isso upload() deve ser
chamado no fim de cada lote de processamento, não só no fim do dia.
"""

import os
import json
import sqlite3

from fisiologia_schema import aplicar_schema

_DB_NAME = "fisiologia_perfil.db"
_LOCAL_DB = f"/tmp/{_DB_NAME}"

# Mesma pasta partilhada onde já vivem correlacoes.db e hrv_analyzer.db.
# Se este ficheiro for para outra pasta, mudar via env var GDRIVE_FOLDER_ID.
_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "11oXQPkFrG6ZBCsvjDqb8RAiE_VfwBSfV")

_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


def _credenciais():
    from google.oauth2.service_account import Credentials
    raw = os.getenv("GCP_SERVICE_ACCOUNT", "").strip()
    if not raw:
        raise RuntimeError("GCP_SERVICE_ACCOUNT nao configurada")
    info = json.loads(raw)
    return Credentials.from_service_account_info(info, scopes=_SCOPES)


def _drive_svc():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_credenciais())


def _find_db_id(svc):
    try:
        r = svc.files().list(
            q=f"name='{_DB_NAME}' and '{_FOLDER_ID}' in parents and trashed=false",
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = r.get("files", [])
        return files[0]["id"] if files else None
    except Exception:
        return None


def download():
    """Traz o .db do Drive para /tmp. Se não existir no Drive, fica só local."""
    try:
        from googleapiclient.http import MediaIoBaseDownload
        svc = _drive_svc()
        file_id = _find_db_id(svc)
        if file_id:
            req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
            with open(_LOCAL_DB, "wb") as f:
                dl = MediaIoBaseDownload(f, req)
                done = False
                while not done:
                    _, done = dl.next_chunk()
            return True
    except Exception as e:
        print(f"[drive_db_fisiologia] download falhou (a continuar so com local): {e}")
    return False


def upload():
    """Sobe o .db actualizado para o Drive. Não rebenta o worker se falhar."""
    if not os.path.exists(_LOCAL_DB):
        return False
    try:
        from googleapiclient.http import MediaFileUpload
        svc = _drive_svc()
        file_id = _find_db_id(svc)
        media = MediaFileUpload(_LOCAL_DB, mimetype="application/x-sqlite3",
                                resumable=False)
        if file_id:
            svc.files().update(fileId=file_id, media_body=media,
                               supportsAllDrives=True).execute()
        else:
            svc.files().create(
                body={"name": _DB_NAME, "parents": [_FOLDER_ID]},
                media_body=media, supportsAllDrives=True, fields="id",
            ).execute()
        return True
    except Exception as e:
        print(f"[drive_db_fisiologia] upload falhou: {e}")
        return False


def get_conn():
    """Conexao sqlite3 pronta a usar. Faz download na primeira chamada do processo."""
    if not os.path.exists(_LOCAL_DB):
        download()
    conn = sqlite3.connect(_LOCAL_DB)
    aplicar_schema(conn)
    return conn
