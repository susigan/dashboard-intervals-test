"""drive_db_fisiologia.py — persiste fisiologia_perfil.db no Google Drive.

Segue o mesmo padrão de drive_db.py (Streamlit) mas sem dependência de
streamlit: credenciais vêm de GCP_SERVICE_ACCOUNT (variável de ambiente
com o JSON da service account), igual ao resto do dashboard-intervals.

Uso:
    import drive_db_fisiologia as ddf

    conn = ddf.get_conn()        # download (se preciso) + abre conexao local
    ... ler/escrever ...
    ddf.upload()                 # sobe o .db actualizado para o Drive

O .db fica em /tmp (efémero no Railway — reinicia sempre que o container
reinicia) — por isso upload() deve ser chamado no fim de cada lote de
processamento, não só no fim do dia.

IMPORTANTE: download()/upload()/_find_db_id() devolvem (ok, detalhe) —
nunca engolem o erro silenciosamente. Usar diagnostico() para veres
exactamente o que se passa (pasta certa? credenciais certas? ficheiro
lá? quantas linhas tem agora?), sem teres de ir aos logs do Railway.
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


def _email_service_account():
    """Email da service account em uso — útil para confirmar que a pasta
    do Drive está partilhada com ESTA conta especificamente (pode ser
    diferente da usada no projecto Streamlit)."""
    try:
        raw = os.getenv("GCP_SERVICE_ACCOUNT", "").strip()
        info = json.loads(raw)
        return info.get("client_email")
    except Exception:
        return None


def _drive_svc():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_credenciais())


def _find_db_id(svc):
    """(file_id_ou_None, erro_ou_None)."""
    try:
        r = svc.files().list(
            q=f"name='{_DB_NAME}' and '{_FOLDER_ID}' in parents and trashed=false",
            fields="files(id, size, modifiedTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = r.get("files", [])
        if files:
            return files[0]["id"], None
        return None, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def download():
    """(ok, detalhe). Traz o .db do Drive para /tmp."""
    try:
        from googleapiclient.http import MediaIoBaseDownload
        svc = _drive_svc()
        file_id, erro = _find_db_id(svc)
        if erro:
            return False, f"falha a procurar o ficheiro: {erro}"
        if not file_id:
            return False, "ficheiro nao existe ainda no Drive (normal na 1a vez)"
        req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        with open(_LOCAL_DB, "wb") as f:
            dl = MediaIoBaseDownload(f, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
        return True, f"descarregado (file_id={file_id})"
    except Exception as e:
        detalhe = f"{type(e).__name__}: {e}"
        print(f"[drive_db_fisiologia] download falhou: {detalhe}")
        return False, detalhe


def upload():
    """(ok, detalhe). Sobe o .db actualizado para o Drive."""
    if not os.path.exists(_LOCAL_DB):
        return False, "sem ficheiro local para subir"
    try:
        from googleapiclient.http import MediaFileUpload
        svc = _drive_svc()
        file_id, erro = _find_db_id(svc)
        if erro:
            return False, f"falha a procurar o ficheiro antes de subir: {erro}"
        media = MediaFileUpload(_LOCAL_DB, mimetype="application/x-sqlite3",
                                resumable=False)
        if file_id:
            svc.files().update(fileId=file_id, media_body=media,
                               supportsAllDrives=True).execute()
            return True, f"actualizado (file_id={file_id})"
        else:
            res = svc.files().create(
                body={"name": _DB_NAME, "parents": [_FOLDER_ID]},
                media_body=media, supportsAllDrives=True, fields="id",
            ).execute()
            return True, f"criado (file_id={res.get('id')})"
    except Exception as e:
        detalhe = f"{type(e).__name__}: {e}"
        print(f"[drive_db_fisiologia] upload falhou: {detalhe}")
        return False, detalhe


def get_conn():
    """Conexao sqlite3 pronta a usar. Faz download na primeira chamada do
    processo (container) — chamadas seguintes reaproveitam o /tmp local,
    sem voltar a descarregar."""
    if not os.path.exists(_LOCAL_DB):
        download()
    conn = sqlite3.connect(_LOCAL_DB)
    aplicar_schema(conn)
    return conn


def diagnostico():
    """Estado completo da persistência — para veres exactamente o que se
    passa sem ires aos logs do Railway. Não altera nada."""
    info = {
        'pasta_id_usada': _FOLDER_ID,
        'service_account_email': _email_service_account(),
        'ficheiro_local_existe': os.path.exists(_LOCAL_DB),
        'ficheiro_local_tamanho_bytes': (os.path.getsize(_LOCAL_DB)
                                         if os.path.exists(_LOCAL_DB) else None),
    }

    try:
        svc = _drive_svc()
        info['credenciais_ok'] = True
    except Exception as e:
        info['credenciais_ok'] = False
        info['erro_credenciais'] = f"{type(e).__name__}: {e}"
        return info

    file_id, erro = _find_db_id(svc)
    info['ficheiro_encontrado_no_drive'] = file_id is not None
    info['erro_procura_drive'] = erro
    info['file_id_no_drive'] = file_id

    # contar linhas no .db local actual (o que está em /tmp agora mesmo)
    if os.path.exists(_LOCAL_DB):
        try:
            conn = sqlite3.connect(_LOCAL_DB)
            aplicar_schema(conn)
            n = conn.execute("SELECT COUNT(*) FROM fisiologia_intervalos").fetchone()[0]
            ids = [r[0] for r in conn.execute(
                "SELECT DISTINCT activity_id FROM fisiologia_intervalos").fetchall()]
            info['linhas_no_db_local'] = n
            info['activity_ids_no_db_local'] = ids
            conn.close()
        except Exception as e:
            info['erro_leitura_db_local'] = f"{type(e).__name__}: {e}"

    return info
