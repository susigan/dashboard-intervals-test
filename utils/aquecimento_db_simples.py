"""
AQUECIMENTO_DB_SIMPLES.PY — Módulo de BD para Aquecimento

Igual ao drive_db_fisiologia.py:
1. Descarrega aquecimento.db do Google Drive
2. Trabalha localmente em /tmp/
3. Salva de volta ao Drive automaticamente
"""

import sqlite3
import os
from datetime import datetime

# Usar o mesmo padrão que drive_db_fisiologia.py
FOLDER_ID = "11oXQPkFrG6ZBCsvjDqb8RAiE_VfwBSfV"
DB_NAME = "aquecimento.db"

_conn = None

def get_conn():
    """Retorna conexão à BD de aquecimento."""
    global _conn
    
    if _conn is not None:
        return _conn
    
    db_path = f"/tmp/{DB_NAME}"
    
    # Tentar descarregar do Drive
    try:
        _download_from_drive(db_path)
    except Exception as e:
        print(f"[AQUECIMENTO] Não foi possível descarregar do Drive: {e}")
        # Continuar com ficheiro local se existir, ou criar novo
    
    # Conectar
    _conn = sqlite3.connect(db_path)
    
    # Garantir tabela
    _criar_tabelas()
    
    return _conn

def _download_from_drive(db_path):
    """Descarrega aquecimento.db do Google Drive."""
    try:
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request
        from google.oauth2.service_account import Credentials
        import io
        
        # Autenticar
        credentials = Credentials.from_service_account_file(
            'service_account.json',
            scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=credentials)
        
        # Procurar ficheiro
        query = f"name='{DB_NAME}' and '{FOLDER_ID}' in parents and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        files = results.get('files', [])
        
        if files:
            file_id = files[0]['id']
            # Descarregar
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = request.download(fh)
            
            # Salvar localmente
            with open(db_path, 'wb') as f:
                f.write(fh.getvalue())
            
            print(f"[AQUECIMENTO] BD descarregada do Drive: {len(fh.getvalue())} bytes")
    
    except Exception as e:
        raise e

def _upload_to_drive(db_path):
    """Envia aquecimento.db ao Google Drive."""
    try:
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request
        from google.oauth2.service_account import Credentials
        from googleapiclient.http import MediaFileUpload
        
        # Autenticar
        credentials = Credentials.from_service_account_file(
            'service_account.json',
            scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=credentials)
        
        # Procurar ficheiro existente
        query = f"name='{DB_NAME}' and '{FOLDER_ID}' in parents and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        files = results.get('files', [])
        
        file_metadata = {'name': DB_NAME}
        media = MediaFileUpload(db_path, mimetype='application/octet-stream')
        
        if files:
            # Update
            file_id = files[0]['id']
            service.files().update(fileId=file_id, media_body=media).execute()
            print(f"[AQUECIMENTO] BD actualizada no Drive")
        else:
            # Create
            file_metadata['parents'] = [FOLDER_ID]
            service.files().create(body=file_metadata, media_body=media).execute()
            print(f"[AQUECIMENTO] BD criada no Drive")
    
    except Exception as e:
        print(f"[AQUECIMENTO] Erro ao enviar para Drive: {e}")

def _criar_tabelas():
    """Cria tabelas se não existirem."""
    sql = """
    CREATE TABLE IF NOT EXISTS aquecimento_sessoes (
        activity_id TEXT PRIMARY KEY,
        modalidade TEXT,
        data TEXT,
        padrao_detectado TEXT,
        n_blocos INTEGER,
        
        hr_avg REAL,
        hr_min REAL,
        hr_max REAL,
        
        smo2_avg REAL,
        smo2_min REAL,
        smo2_max REAL,
        
        resp_avg REAL,
        resp_min REAL,
        resp_max REAL,
        
        dfa1_avg REAL,
        dfa1_min REAL,
        dfa1_max REAL,
        
        tempo_aquecimento_seg INTEGER,
        n_intervalos_analisados INTEGER,
        data_criacao TEXT,
        data_atualizacao TEXT
    )
    """
    _conn.execute(sql)
    _conn.commit()

def salvar_sessao(activity_id, dados):
    """Salva dados de aquecimento e sincroniza."""
    conn = get_conn()
    
    sql = """
    INSERT OR REPLACE INTO aquecimento_sessoes (
        activity_id, modalidade, data, padrao_detectado, n_blocos,
        hr_avg, hr_min, hr_max,
        smo2_avg, smo2_min, smo2_max,
        resp_avg, resp_min, resp_max,
        dfa1_avg, dfa1_min, dfa1_max,
        tempo_aquecimento_seg, n_intervalos_analisados,
        data_criacao, data_atualizacao
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    valores = (
        activity_id,
        dados.get('modalidade'),
        dados.get('data'),
        dados.get('padrao_detectado'),
        dados.get('n_blocos'),
        dados.get('hr_avg'),
        dados.get('hr_min'),
        dados.get('hr_max'),
        dados.get('smo2_avg'),
        dados.get('smo2_min'),
        dados.get('smo2_max'),
        dados.get('resp_avg'),
        dados.get('resp_min'),
        dados.get('resp_max'),
        dados.get('dfa1_avg'),
        dados.get('dfa1_min'),
        dados.get('dfa1_max'),
        dados.get('tempo_aquecimento_seg'),
        dados.get('n_intervalos_analisados'),
        datetime.now().isoformat(),
        datetime.now().isoformat()
    )
    
    conn.execute(sql, valores)
    conn.commit()
    
    # Sincronizar com Drive
    db_path = f"/tmp/{DB_NAME}"
    _upload_to_drive(db_path)

def obter_sessao(activity_id):
    """Obtém dados de aquecimento de uma atividade."""
    conn = get_conn()
    
    sql = "SELECT * FROM aquecimento_sessoes WHERE activity_id = ?"
    cursor = conn.execute(sql, (activity_id,))
    resultado = cursor.fetchone()
    
    if resultado:
        colunas = [desc[0] for desc in cursor.description]
        return dict(zip(colunas, resultado))
    return None

def listar_todas():
    """Lista todas as sessões."""
    conn = get_conn()
    
    sql = "SELECT * FROM aquecimento_sessoes ORDER BY data DESC"
    cursor = conn.execute(sql)
    colunas = [desc[0] for desc in cursor.description]
    resultados = []
    for row in cursor.fetchall():
        resultados.append(dict(zip(colunas, row)))
    return resultados

def fechar():
    """Fecha a conexão e sincroniza."""
    global _conn
    if _conn:
        _conn.close()
        _conn = None
    
    db_path = f"/tmp/{DB_NAME}"
    if os.path.exists(db_path):
        _upload_to_drive(db_path)
