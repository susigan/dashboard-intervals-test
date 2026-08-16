"""
AQUECIMENTO_DB.PY — BD do aquecimento (SQLite no Google Drive)

Mesmo mecanismo de drive_db_fisiologia.py:
  - credenciais via env var GCP_SERVICE_ACCOUNT (NAO ficheiro local)
  - ficheiro aquecimento.db na pasta _FOLDER_ID do Drive
  - copia local em /tmp, sincronizada no arranque e apos cada escrita

Schema: UMA LINHA POR BLOCO de aquecimento (nao por sessao).
Obrigatorio para calcular SEM/MDC por escalao de watts -- uma linha por
sessao misturaria 140/160/180 W num unico numero.
"""

import os
import io
import json
import sqlite3
from datetime import datetime

_DB_NAME = "aquecimento.db"
_LOCAL_DB = f"/tmp/{_DB_NAME}"
_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "11oXQPkFrG6ZBCsvjDqb8RAiE_VfwBSfV")
_SCOPES = ["https://www.googleapis.com/auth/drive"]

_conn = None


# ── Google Drive ──────────────────────────────────────────────────────────

def _credenciais():
    from google.oauth2.service_account import Credentials
    raw = os.getenv("GCP_SERVICE_ACCOUNT", "").strip()
    if not raw:
        raise RuntimeError("GCP_SERVICE_ACCOUNT nao configurada")
    return Credentials.from_service_account_info(json.loads(raw), scopes=_SCOPES)


def _service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_credenciais(), cache_discovery=False)


def _procurar_ficheiro(svc):
    res = svc.files().list(
        q=f"name='{_DB_NAME}' and '{_FOLDER_ID}' in parents and trashed=false",
        spaces="drive", fields="files(id, name)").execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def _download():
    """Traz o .db do Drive para /tmp. Silencioso se ainda nao existir la."""
    from googleapiclient.http import MediaIoBaseDownload
    svc = _service()
    fid = _procurar_ficheiro(svc)
    if not fid:
        print(f"[AQUECIMENTO] {_DB_NAME} ainda nao existe no Drive; cria local")
        return False
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, svc.files().get_media(fileId=fid))
    done = False
    while not done:
        _, done = downloader.next_chunk()
    with open(_LOCAL_DB, "wb") as f:
        f.write(buf.getvalue())
    print(f"[AQUECIMENTO] BD descarregada do Drive ({len(buf.getvalue())} bytes)")
    return True


def sincronizar():
    """Envia a copia local para o Drive. Nunca levanta excepcao."""
    from googleapiclient.http import MediaFileUpload
    try:
        if not os.path.exists(_LOCAL_DB):
            return False
        svc = _service()
        fid = _procurar_ficheiro(svc)
        media = MediaFileUpload(_LOCAL_DB, mimetype="application/octet-stream",
                                resumable=False)
        if fid:
            svc.files().update(fileId=fid, media_body=media).execute()
        else:
            svc.files().create(body={"name": _DB_NAME, "parents": [_FOLDER_ID]},
                               media_body=media).execute()
        return True
    except Exception as e:
        print(f"[AQUECIMENTO] sync falhou: {type(e).__name__}: {e}")
        return False


# ── Conexao / schema ──────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS aquecimento_blocos (
    activity_id   TEXT    NOT NULL,
    bloco_num     INTEGER NOT NULL,
    modalidade    TEXT,
    data          TEXT,
    watts_alvo    INTEGER,
    watts_real    REAL,
    interval_num  INTEGER,
    tempo_seg     INTEGER,
    hr_avg REAL,   hr_min REAL,   hr_max REAL,
    smo2_avg REAL, smo2_min REAL, smo2_max REAL,
    resp_avg REAL, resp_min REAL, resp_max REAL,
    dfa1_avg REAL, dfa1_min REAL, dfa1_max REAL,
    data_registo  TEXT,
    PRIMARY KEY (activity_id, bloco_num)
);
CREATE INDEX IF NOT EXISTS idx_aq_mod_watts
    ON aquecimento_blocos (modalidade, watts_alvo, data);

CREATE TABLE IF NOT EXISTS aquecimento_rejeitadas (
    activity_id TEXT PRIMARY KEY,
    modalidade  TEXT,
    data        TEXT,
    motivo      TEXT,
    verificada  TEXT,
    versao      INTEGER DEFAULT 0
);
"""


def get_conn():
    """Conexao a /tmp/aquecimento.db, descarregando do Drive na 1a chamada."""
    global _conn
    if _conn is not None:
        return _conn
    if not os.path.exists(_LOCAL_DB):
        try:
            _download()
        except Exception as e:
            print(f"[AQUECIMENTO] download falhou: {type(e).__name__}: {e}")
    _conn = sqlite3.connect(_LOCAL_DB, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(_SCHEMA)
    _conn.commit()
    return _conn


# ── Escrita ───────────────────────────────────────────────────────────────

_COLS = ["hr_avg", "hr_min", "hr_max", "smo2_avg", "smo2_min", "smo2_max",
         "resp_avg", "resp_min", "resp_max", "dfa1_avg", "dfa1_min", "dfa1_max"]


def salvar_blocos(activity_id, modalidade, data, blocos, sync=True):
    """Grava os blocos de uma sessao. blocos = lista de dicts do analyzer.

    Substitui os blocos anteriores dessa atividade (reprocessar e' idempotente).
    """
    conn = get_conn()
    agora = datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM aquecimento_blocos WHERE activity_id = ?", (activity_id,))
    # se tinha sido rejeitada por uma versao anterior do detector, o registo
    # de rejeicao tem de sair -- senao a sessao aparece aceite E ignorada
    conn.execute("DELETE FROM aquecimento_rejeitadas WHERE activity_id = ?", (activity_id,))
    n_campos = 8 + len(_COLS) + 1
    for b in blocos:
        conn.execute(
            f"""INSERT INTO aquecimento_blocos
                (activity_id, bloco_num, modalidade, data, watts_alvo, watts_real,
                 interval_num, tempo_seg, {', '.join(_COLS)}, data_registo)
                VALUES ({','.join('?' * n_campos)})""",
            (activity_id, b["bloco_num"], modalidade, data,
             b.get("watts_alvo"), b.get("watts_real"),
             b.get("interval_num"), b.get("tempo_seg"),
             *[b.get(c) for c in _COLS], agora))
    conn.commit()
    if sync:
        sincronizar()
    return len(blocos)


def _garantir_coluna_versao(conn):
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(aquecimento_rejeitadas)")}
        if "versao" not in cols:
            conn.execute("ALTER TABLE aquecimento_rejeitadas ADD COLUMN versao INTEGER DEFAULT 0")
            conn.commit()
    except Exception as e:
        print(f"[AQUECIMENTO] migracao versao: {e}")


def marcar_rejeitada(activity_id, modalidade, data, motivo, sync=False, versao=0):
    """Regista que a atividade foi analisada e NAO segue o protocolo.

    Evita reanalisar a mesma atividade em cada passagem do worker.
    """
    conn = get_conn()
    _garantir_coluna_versao(conn)
    conn.execute(
        """INSERT OR REPLACE INTO aquecimento_rejeitadas
           (activity_id, modalidade, data, motivo, verificada, versao)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (activity_id, modalidade, data, motivo,
         datetime.now().isoformat(timespec="seconds"), versao))
    conn.commit()
    if sync:
        sincronizar()


def ja_analisada(activity_id, versao_atual=None):
    """True se ja foi aceite, ou rejeitada por um detector ATUAL.

    Uma rejeicao feita por uma versao antiga do detector nao conta: a
    atividade volta a ser analisada com a logica corrigida.
    """
    conn = get_conn()
    if conn.execute("SELECT 1 FROM aquecimento_blocos WHERE activity_id = ? LIMIT 1",
                    (activity_id,)).fetchone():
        return True
    _garantir_coluna_versao(conn)
    r = conn.execute(
        "SELECT COALESCE(versao, 0) FROM aquecimento_rejeitadas WHERE activity_id = ?",
        (activity_id,)).fetchone()
    if not r:
        return False
    if versao_atual is None:
        return True
    return int(r[0]) >= int(versao_atual)


# ── Leitura ───────────────────────────────────────────────────────────────

def listar_blocos(modalidade=None, watts_alvo=None):
    conn = get_conn()
    cond, params = [], []
    if modalidade:
        cond.append("modalidade = ?")
        params.append(modalidade)
    if watts_alvo:
        cond.append("watts_alvo = ?")
        params.append(watts_alvo)
    where = f"WHERE {' AND '.join(cond)}" if cond else ""
    linhas = conn.execute(
        f"SELECT * FROM aquecimento_blocos {where} ORDER BY data, bloco_num",
        tuple(params)).fetchall()
    return [dict(l) for l in linhas]


def listar_sessoes(modalidade=None):
    """Uma linha por sessao, para a tabela-resumo da tab."""
    conn = get_conn()
    where = "WHERE modalidade = ?" if modalidade else ""
    params = (modalidade,) if modalidade else ()
    linhas = conn.execute(
        f"""SELECT activity_id, modalidade, data,
                   COUNT(*)        AS n_blocos,
                   SUM(tempo_seg)  AS tempo_total_seg,
                   MIN(watts_alvo) AS watts_min,
                   MAX(watts_alvo) AS watts_max
            FROM aquecimento_blocos {where}
            GROUP BY activity_id ORDER BY data DESC""", params).fetchall()
    return [dict(l) for l in linhas]


def obter_sessao(activity_id):
    conn = get_conn()
    linhas = conn.execute(
        "SELECT * FROM aquecimento_blocos WHERE activity_id = ? ORDER BY bloco_num",
        (activity_id,)).fetchall()
    if not linhas:
        return None
    return {"activity_id": activity_id,
            "modalidade": linhas[0]["modalidade"],
            "data": linhas[0]["data"],
            "blocos": [dict(l) for l in linhas]}


def modalidades_disponiveis():
    conn = get_conn()
    linhas = conn.execute(
        """SELECT modalidade, COUNT(DISTINCT activity_id) AS n_sessoes,
                  COUNT(*) AS n_blocos
           FROM aquecimento_blocos GROUP BY modalidade ORDER BY modalidade"""
    ).fetchall()
    return [dict(l) for l in linhas]


def listar_todas():
    """Compatibilidade com a versao anterior da API."""
    return listar_sessoes()
