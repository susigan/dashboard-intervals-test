"""drive_db_perfil.py — persiste perfil_historico.db no Google Drive.

Segue o mesmo padrão de drive_db.py (Streamlit) mas sem dependência de
streamlit: credenciais vêm de GCP_SERVICE_ACCOUNT (variável de ambiente
com o JSON da service account), igual ao resto do dashboard-intervals.

Uso:
    import drive_db_perfil as ddp

    conn = ddp.get_conn()        # download (se preciso) + abre conexao local
    ... ler/escrever ...
    ddp.upload()                 # sobe o .db actualizado para o Drive

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

from perfil_schema import aplicar_schema
import drive_db_seguro as _dbseg

_DB_NAME = "perfil_historico.db"
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


# Contagens-base usadas para impedir upload que encolha o banco do Drive.
_CONTAGENS_BASE = {
    'moxy_activities':       'SELECT COUNT(*) FROM moxy_activities',
    'vst_conjuntos':         'SELECT COUNT(*) FROM vst_conjuntos',
    'vst_results':           'SELECT COUNT(*) FROM vst_results',
    'moxy_analyses':         'SELECT COUNT(*) FROM moxy_analyses',
    'activity_interval_rpe': 'SELECT COUNT(*) FROM activity_interval_rpe',
    'rpe_preenchido':        'SELECT COUNT(rpe) FROM activity_interval_rpe',
}


def download():
    """(ok, detalhe). Traz o .db do Drive para /tmp.

    Descarrega para um ficheiro TEMPORÁRIO e só o promove a _LOCAL_DB
    depois de confirmar integridade. Um ficheiro local existente nunca é
    sobrescrito: é movido para arquivo. Regista a origem ('drive') para
    o guarda de upload.
    """
    try:
        from googleapiclient.http import MediaIoBaseDownload
        svc = _drive_svc()
        file_id, erro = _find_db_id(svc)
        if erro:
            return False, f"falha a procurar o ficheiro: {erro}"
        if not file_id:
            return False, "ficheiro nao existe ainda no Drive (normal na 1a vez)"
        # modifiedTime lido ANTES do download (ver drive_db_moxy_vst.download)
        meta = svc.files().get(fileId=file_id, fields="modifiedTime",
                               supportsAllDrives=True).execute()
        req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        tmp_path = _LOCAL_DB + ".partial"
        with open(tmp_path, "wb") as f:
            dl = MediaIoBaseDownload(f, req)
            done = False
            while not done:
                _, done = dl.next_chunk()

        ok_integ, detalhe_integ = _verificar_integridade(tmp_path)
        if not ok_integ:
            try:
                os.remove(tmp_path)   # cópia parcial do Drive, não dados locais
            except OSError:
                pass
            return False, f"download terminou mas o ficheiro está corrompido " \
                          f"({detalhe_integ}) — não promovido, nada foi tocado " \
                          f"no .db anterior (se existir)"
        if os.path.exists(_LOCAL_DB):
            _dbseg.arquivar_grupo(_LOCAL_DB, "substituido")
        else:
            _dbseg.arquivar_wal_orfao(_LOCAL_DB)
        os.replace(tmp_path, _LOCAL_DB)
        _dbseg.gravar_origem(_LOCAL_DB, {
            "origem": "drive",
            "file_id": file_id,
            "modified_time": meta.get("modifiedTime"),
            "contagens": _dbseg.contagens(_LOCAL_DB, _CONTAGENS_BASE),
        })
        return True, f"descarregado (file_id={file_id})"
    except Exception as e:
        detalhe = f"{type(e).__name__}: {e}"
        print(f"[drive_db_perfil] download falhou: {detalhe}")
        return False, detalhe


def _verificar_integridade(caminho):
    """(ok, detalhe). PRAGMA integrity_check — nunca escreve nada."""
    estado, det = _dbseg.integridade(caminho)
    return estado == "ok", det


def upload():
    """(ok, detalhe). Sobe o .db actualizado para o Drive.

    Ordem: checkpoint do WAL → integridade → guarda (origem Drive,
    modifiedTime igual, sem encolher) → update. Nunca cria ficheiro novo.
    """
    if not os.path.exists(_LOCAL_DB):
        return False, "sem ficheiro local para subir"
    try:
        _dbseg.consolidar_wal(_LOCAL_DB)
    except Exception as e:
        detalhe = f"consolidação do WAL falhou ({e}) — upload recusado"
        print(f"[drive_db_perfil] upload recusado: {detalhe}")
        return False, detalhe
    estado, detalhe_integ = _dbseg.integridade(_LOCAL_DB)
    if estado != "ok":
        detalhe = (f"ficheiro local {estado} ({detalhe_integ}) — upload recusado "
                   f"para não substituir a cópia boa no Drive")
        print(f"[drive_db_perfil] upload recusado: {detalhe}")
        return False, detalhe
    ok_o, motivo_o = _dbseg.origem_drive(_dbseg.ler_origem(_LOCAL_DB))
    if not ok_o:
        print(f"[drive_db_perfil] upload recusado: {motivo_o}")
        return False, motivo_o
    try:
        from googleapiclient.http import MediaFileUpload
        svc = _drive_svc()
        file_id, erro = _find_db_id(svc)
        if erro:
            return False, f"falha a procurar o ficheiro antes de subir: {erro}"
        if not file_id:
            return False, (f"{_DB_NAME} nao existe na pasta do Drive — upload "
                           f"recusado; nao e criado automaticamente")
        meta = svc.files().get(fileId=file_id, fields="modifiedTime",
                               supportsAllDrives=True).execute()
        cont = _dbseg.contagens(_LOCAL_DB, _CONTAGENS_BASE)
        ok_v, motivo = _dbseg.validar_envio(
            _dbseg.ler_origem(_LOCAL_DB), meta.get("modifiedTime"), cont)
        if not ok_v:
            print(f"[drive_db_perfil] upload recusado: {motivo}")
            return False, motivo
        media = MediaFileUpload(_LOCAL_DB, mimetype="application/x-sqlite3",
                                resumable=False)
        res = svc.files().update(fileId=file_id, media_body=media,
                                 supportsAllDrives=True,
                                 fields="modifiedTime").execute()
        _dbseg.gravar_origem(_LOCAL_DB, {
            "origem": "drive",
            "file_id": file_id,
            "modified_time": res.get("modifiedTime"),
            "contagens": cont,
        })
        return True, f"actualizado (file_id={file_id})"
    except Exception as e:
        detalhe = f"{type(e).__name__}: {e}"
        print(f"[drive_db_perfil] upload falhou: {detalhe}")
        return False, detalhe


def get_conn():
    """Conexao sqlite3 pronta a usar.

    - Corrupção real (malformed/not a database) → ficheiro MOVIDO para arquivo.
    - Erro transitório de integridade → ficheiro local mantido.
    - Sem ficheiro após falha de download → banco vazio com origem 'vazio_local'
      (não é enviado ao Drive).
    """
    if os.path.exists(_LOCAL_DB):
        estado, det = _dbseg.integridade(_LOCAL_DB)
        if estado == "corrompido":
            movidos = _dbseg.arquivar_grupo(_LOCAL_DB, "corrompido")
            print(f"[drive_db_perfil] {_LOCAL_DB} corrompido ({det}) — arquivado: {movidos}")
        elif estado == "transitorio":
            print(f"[drive_db_perfil] integridade nao verificavel agora ({det}) — ficheiro local mantido")

    if not os.path.exists(_LOCAL_DB):
        _dbseg.arquivar_wal_orfao(_LOCAL_DB)
        ok_dl, det_dl = download()
        if not ok_dl:
            print(f"[drive_db_perfil] download falhou ({det_dl}) — banco local vazio "
                  f"(origem 'vazio_local': nao sera enviado ao Drive)")

    criado_agora = not os.path.exists(_LOCAL_DB)
    conn = sqlite3.connect(_LOCAL_DB)
    aplicar_schema(conn)
    if criado_agora:
        _dbseg.gravar_origem(_LOCAL_DB, {
            "origem": "vazio_local",
            "motivo": "download falhou ou ficheiro inexistente no Drive",
        })
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
            info['linhas_no_db_local'] = {}
            for tabela in ('cp_resultados', 'perfil_snapshots',
                           'limiares_snapshots'):
                info['linhas_no_db_local'][tabela] = conn.execute(
                    f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
            info['datas_gravadas'] = [
                {'modalidade': r[0], 'season': r[1], 'data': r[2]}
                for r in conn.execute(
                    """SELECT modalidade, season, data_referencia
                         FROM perfil_snapshots
                        ORDER BY data_referencia DESC LIMIT 20""").fetchall()]
            conn.close()
        except Exception as e:
            info['erro_leitura_db_local'] = f"{type(e).__name__}: {e}"

    return info
