"""Schema SQLite — fisiologia_perfil.db

Guarda, por INTERVALO (não por atividade), a resposta e recuperação de
HR, SmO2, tHb e respiração face a mudanças de potência. Watts fica como
valor contínuo — sem zonas fixas. Os quartis de potência (e dentro deles,
os quartis de cada métrica) são calculados NA LEITURA (tab_metabol), a
partir da distribuição real de watts de cada modalidade. Isto significa
que os "buckets" nunca ficam presos a uma escolha antiga.

DFA1 NÃO está incluído nesta v1: exige extração de intervalos RR a partir
do FIT bruto (pipeline pesado, tipo tab_fit_analise.py no Streamlit), e
os streams já guardados no Postgres (via db.get_streams) não têm RR bruto
— só as séries que a API do Intervals.icu expõe (watts, heartrate, smo2,
thb, respiration quando o sensor grava). Fica como extensão futura.

Duas tabelas:
  fisiologia_intervalos   dados por intervalo (uma linha por par WORK+REC)
  fisiologia_progresso    metadados de execução (não é cursor obrigatório —
                          o "próximo a processar" é decidido por exclusão:
                          atividades que ainda não têm linhas aqui)
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fisiologia_intervalos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id     TEXT NOT NULL,
    data            TEXT NOT NULL,          -- YYYY-MM-DD
    modalidade      TEXT NOT NULL,          -- Bike, Row, Ski, Run

    interval_num    INTEGER NOT NULL,       -- ordem dentro da atividade (1, 2, 3...)
    watts_medio     REAL,
    watts_min       REAL,
    watts_max       REAL,
    dur_work_s      INTEGER,
    dur_rec_s       INTEGER,                -- NULL se não houver REC a seguir

    -- Heart Rate: lag de resposta (s desde início do WORK) e recuperação
    -- (s desde início do REC, tempo até % de volta à baseline)
    lag_hr_50       REAL,
    lag_hr_75       REAL,
    lag_hr_90       REAL,
    rec_hr_50       REAL,
    rec_hr_75       REAL,

    -- SmO2
    lag_smo2_50     REAL,
    lag_smo2_75     REAL,
    lag_smo2_90     REAL,
    rec_smo2_50     REAL,
    rec_smo2_75     REAL,

    -- tHb
    lag_thb_50      REAL,
    lag_thb_75      REAL,
    lag_thb_90      REAL,
    rec_thb_50      REAL,
    rec_thb_75      REAL,

    -- Respiração (quando o sensor grava; nem toda atividade tem)
    lag_resp_50     REAL,
    lag_resp_75     REAL,
    lag_resp_90     REAL,
    rec_resp_50     REAL,
    rec_resp_75     REAL,

    -- Flags de disponibilidade/qualidade
    tem_hr          INTEGER DEFAULT 0,
    tem_smo2        INTEGER DEFAULT 0,
    tem_thb         INTEGER DEFAULT 0,
    tem_resp        INTEGER DEFAULT 0,
    valido          INTEGER DEFAULT 1,      -- 0 = intervalo curto demais ou dados incompletos
    motivo_invalido TEXT,

    criado_em       TEXT NOT NULL,

    UNIQUE(activity_id, interval_num)
);

CREATE INDEX IF NOT EXISTS idx_fisio_modalidade_watts
    ON fisiologia_intervalos(modalidade, watts_medio);

CREATE INDEX IF NOT EXISTS idx_fisio_activity
    ON fisiologia_intervalos(activity_id);

CREATE INDEX IF NOT EXISTS idx_fisio_data
    ON fisiologia_intervalos(data);


CREATE TABLE IF NOT EXISTS fisiologia_progresso (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    total_processadas    INTEGER DEFAULT 0,
    total_puladas        INTEGER DEFAULT 0,
    total_erros          INTEGER DEFAULT 0,
    ultima_activity_id   TEXT,
    ultima_data          TEXT,
    ultima_execucao      TEXT,
    concluido            INTEGER DEFAULT 0,   -- 1 quando chegou ao corte (2024-01-01)
    corte_data           TEXT DEFAULT '2024-01-01'
);

INSERT OR IGNORE INTO fisiologia_progresso (id) VALUES (1);
"""


def aplicar_schema(conn):
    """Aplica o schema numa conexao sqlite3 já aberta."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()
