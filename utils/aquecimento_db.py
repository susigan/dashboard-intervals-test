"""
AQUECIMENTO_DB_GOOGLE_DRIVE.PY — Gestão de BD de Aquecimento no Google Drive

Guarda a BD directamente no Google Drive (igual ao drive_db_fisiologia.py)
"""

import sqlite3
import os
from datetime import datetime
from io import BytesIO
import pickle

class AquecimentoDBGoogleDrive:
    def __init__(self, folder_id="11oXQPkFrG6ZBCsvjDqb8RAiE_VfwBSfV", db_name="aquecimento.db"):
        """Inicializa BD de aquecimento no Google Drive.
        
        A BD é guardada em /tmp/ e sincronizada com Google Drive.
        """
        self.folder_id = folder_id
        self.db_name = db_name
        self.db_path = f"/tmp/{db_name}"
        self.conn = None
        
        # Descarregar DB do Drive se existir
        self._download_from_drive()
        
        # Conectar
        self.conn = sqlite3.connect(self.db_path)
        self._criar_tabelas()
    
    def _download_from_drive(self):
        """Descarrega DB do Google Drive (se existir)."""
        try:
            from googleapiclient.discovery import build
            from google.auth.transport.requests import Request
            from google.oauth2.service_account import Credentials
            
            # Tentar com service account
            credentials = Credentials.from_service_account_file(
                'service_account.json',
                scopes=['https://www.googleapis.com/auth/drive']
            )
            service = build('drive', 'v3', credentials=credentials)
            
            # Procurar ficheiro
            query = f"name='{self.db_name}' and '{self.folder_id}' in parents and trashed=false"
            results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
            
            files = results.get('files', [])
            if files:
                file_id = files[0]['id']
                # Descarregar
                request = service.files().get_media(fileId=file_id)
                fh = BytesIO()
                downloader = request.download(fh)
                
                with open(self.db_path, 'wb') as f:
                    f.write(fh.getvalue())
                
                print(f"[AQUECIMENTO] DB descarregada do Drive")
        except Exception as e:
            print(f"[AQUECIMENTO] Não foi possível descarregar do Drive: {e}")
            # Continuar com DB local (será criada nova)
    
    def _upload_to_drive(self):
        """Envia DB para Google Drive."""
        try:
            from googleapiclient.discovery import build
            from google.auth.transport.requests import Request
            from google.oauth2.service_account import Credentials
            from googleapiclient.http import MediaFileUpload
            
            credentials = Credentials.from_service_account_file(
                'service_account.json',
                scopes=['https://www.googleapis.com/auth/drive']
            )
            service = build('drive', 'v3', credentials=credentials)
            
            # Procurar ficheiro existente
            query = f"name='{self.db_name}' and '{self.folder_id}' in parents and trashed=false"
            results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
            files = results.get('files', [])
            
            file_metadata = {'name': self.db_name}
            media = MediaFileUpload(self.db_path, mimetype='application/octet-stream')
            
            if files:
                # Update
                file_id = files[0]['id']
                service.files().update(fileId=file_id, media_body=media).execute()
                print(f"[AQUECIMENTO] DB actualizada no Drive")
            else:
                # Create
                file_metadata['parents'] = [self.folder_id]
                service.files().create(body=file_metadata, media_body=media).execute()
                print(f"[AQUECIMENTO] DB criada no Drive")
        except Exception as e:
            print(f"[AQUECIMENTO] Erro ao enviar para Drive: {e}")
    
    def _criar_tabelas(self):
        """Cria as tabelas se não existirem."""
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
        self.conn.execute(sql)
        self.conn.commit()
    
    def salvar_sessao(self, activity_id, dados):
        """Salva dados de aquecimento e sincroniza com Drive."""
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
        
        self.conn.execute(sql, valores)
        self.conn.commit()
        
        # Sincronizar com Drive
        self._upload_to_drive()
    
    def obter_sessao(self, activity_id):
        """Obtém dados de aquecimento de uma atividade."""
        sql = "SELECT * FROM aquecimento_sessoes WHERE activity_id = ?"
        cursor = self.conn.execute(sql, (activity_id,))
        resultado = cursor.fetchone()
        
        if resultado:
            colunas = [desc[0] for desc in cursor.description]
            return dict(zip(colunas, resultado))
        return None
    
    def listar_todas(self):
        """Lista todas as sessões."""
        sql = "SELECT * FROM aquecimento_sessoes ORDER BY data DESC"
        cursor = self.conn.execute(sql)
        colunas = [desc[0] for desc in cursor.description]
        resultados = []
        for row in cursor.fetchall():
            resultados.append(dict(zip(colunas, row)))
        return resultados
    
    def fechar(self):
        """Fecha a conexão e sincroniza."""
        if self.conn:
            self.conn.close()
        self._upload_to_drive()

# Instância global
_db_instance = None

def get_db():
    global _db_instance
    if _db_instance is None:
        _db_instance = AquecimentoDBGoogleDrive()
    return _db_instance
