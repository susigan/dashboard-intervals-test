"""
AQUECIMENTO_DB.PY — Gestão de BD de Aquecimento

BD SQLite: aquecimento.db (Google Drive)
Tabela: aquecimento_sessoes

Campos:
  - activity_id (TEXT PRIMARY KEY)
  - modalidade (TEXT) — Row, Ski, Bike
  - data (TEXT) — ISO date
  - padrao_detectado (TEXT) — "5-1-5-1-5" ou "5-1-5-1-5-1-5-1-5"
  - n_blocos (INTEGER) — número de blocos de 5min detectados
  
  — HR (bpm) —
  - hr_avg (REAL)
  - hr_min (REAL)
  - hr_max (REAL)
  
  — SmO2 (%) —
  - smo2_avg (REAL)
  - smo2_min (REAL)
  - smo2_max (REAL)
  
  — Respiração (rpm) —
  - resp_avg (REAL)
  - resp_min (REAL)
  - resp_max (REAL)
  
  — DFA-α1 —
  - dfa1_avg (REAL)
  - dfa1_min (REAL)
  - dfa1_max (REAL)
  
  — Metadata —
  - tempo_aquecimento_seg (INTEGER) — segundos totais
  - n_intervalos_analisados (INTEGER)
  - data_criacao (TEXT)
  - data_atualizacao (TEXT)
"""

import sqlite3
import os
from datetime import datetime
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
import io

class AquecimentoDB:
    def __init__(self, folder_id="11oXQPkFrG6ZBCsvjDqb8RAiE_VfwBSfV"):
        """Conecta à BD de aquecimento no Google Drive."""
        self.folder_id = folder_id
        self.db_name = "aquecimento.db"
        self.conn = None
        self._setup()
    
    def _get_drive_service(self):
        """Autentica com Google Drive."""
        # Usar credentials do environment ou arquivo
        try:
            credentials = Credentials.from_service_account_file(
                'service_account.json',
                scopes=['https://www.googleapis.com/auth/drive']
            )
        except:
            # Fallback: assumir que está autenticado via OAuth
            from google.auth.transport.requests import Request
            from google.auth import default
            credentials, _ = default(scopes=['https://www.googleapis.com/auth/drive'])
        
        return build('drive', 'v3', credentials=credentials)
    
    def _setup(self):
        """Descarrega DB do Drive ou cria nova."""
        # Por enquanto, usar SQLite local (pode ser expandido para Drive depois)
        self.db_path = f"/tmp/{self.db_name}"
        
        # Criar conexão
        self.conn = sqlite3.connect(self.db_path)
        self._criar_tabelas()
    
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
        """Salva dados de aquecimento para uma atividade."""
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
    
    def atualizar_sessao(self, activity_id, dados):
        """Atualiza uma sessão existente."""
        self.salvar_sessao(activity_id, dados)
    
    def fechar(self):
        """Fecha a conexão."""
        if self.conn:
            self.conn.close()

# Função global de acesso
_db_instance = None

def get_db():
    global _db_instance
    if _db_instance is None:
        _db_instance = AquecimentoDB()
    return _db_instance
