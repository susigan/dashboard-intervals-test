"""Schema SQLite — fisiologia_perfil.db

Guarda, por INTERVALO (não por atividade), duas dimensões distintas:

  1. TEMPO — quanto demora cada métrica a responder (lag_*) e a recuperar
     (rec_*) face a uma mudança de potência.

  2. VALOR/PATAMAR — quanto vale cada métrica quando estabiliza naquele
     esforço (*_medio_work) e quando estabiliza em repouso (*_medio_rec).
     É esta segunda dimensão que permite a curva "a X watts, o HR/SmO2/
     tHb/respiração/DFA1 esperado é Y" — o objetivo de perfil metabólico.

Watts fica como valor contínuo — sem zonas fixas. Os quartis de potência
(e dentro deles, os quartis de cada métrica) são calculados NA LEITURA
(tab_metabol), a partir da distribuição real de watts de cada modalidade.

DFA1 ESTÁ incluído (correção: a API do Intervals.icu já devolve
average_dfa_a1 por intervalo — não precisa de RR bruto do FIT como se
pensou inicialmente). O valor médio por intervalo vem directo da API;
só o LAG de resposta do DFA1 continuaria a exigir a série temporal do
FIT bruto, e por isso lag_dfa1_* fica de fora nesta v1 (só o "patamar",
dfa1_medio_work/rec, está disponível).

Duas tabelas:
  fisiologia_intervalos   dados por intervalo (uma linha por par WORK+REC)
  fisiologia_progresso    metadados de execução (não é cursor obrigatório —
                          o "próximo a processar" é decidido por exclusão:
                          atividades que ainda não têm linhas aqui)

MIGRAÇÃO: aplicar_schema() é seguro correr sobre uma base de dados já
existente (com dados de sessões anteriores) — cria as tabelas se não
existirem, e adiciona só as colunas que ainda faltarem, sem apagar nada.
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

    -- ── TEMPO: Heart Rate ────────────────────────────────────────────
    lag_hr_50       REAL,
    lag_hr_75       REAL,
    lag_hr_90       REAL,
    rec_hr_50       REAL,
    rec_hr_75       REAL,

    -- ── TEMPO: SmO2 ──────────────────────────────────────────────────
    lag_smo2_50     REAL,
    lag_smo2_75     REAL,
    lag_smo2_90     REAL,
    rec_smo2_50     REAL,
    rec_smo2_75     REAL,

    -- ── TEMPO: tHb ───────────────────────────────────────────────────
    lag_thb_50      REAL,
    lag_thb_75      REAL,
    lag_thb_90      REAL,
    rec_thb_50      REAL,
    rec_thb_75      REAL,

    -- ── TEMPO: Respiração ────────────────────────────────────────────
    lag_resp_50     REAL,
    lag_resp_75     REAL,
    lag_resp_90     REAL,
    rec_resp_50     REAL,
    rec_resp_75     REAL,

    -- ── VALOR/PATAMAR: quanto vale cada métrica no esforço (WORK) e em
    --    repouso (REC) — direto da API por intervalo, sem processar
    --    streams. É isto que dá a curva watts -> métrica esperada.
    hr_medio_work   REAL,
    hr_medio_rec    REAL,
    smo2_medio_work REAL,
    smo2_medio_rec  REAL,
    thb_medio_work  REAL,
    thb_medio_rec   REAL,
    resp_medio_work REAL,
    resp_medio_rec  REAL,
    dfa1_medio_work REAL,
    dfa1_medio_rec  REAL,

    -- Flags de disponibilidade/qualidade
    tem_hr          INTEGER DEFAULT 0,
    tem_smo2        INTEGER DEFAULT 0,
    tem_thb         INTEGER DEFAULT 0,
    tem_resp        INTEGER DEFAULT 0,
    tem_dfa1        INTEGER DEFAULT 0,
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

# Colunas que podem faltar numa base de dados criada por uma versão
# anterior deste schema (ex.: as 2 atividades já processadas antes desta
# alteração). Migração aditiva só — nunca remove nem renomeia colunas.
COLUNAS_MIGRACAO = {
    'hr_medio_work':   'REAL',
    'hr_medio_rec':    'REAL',
    'smo2_medio_work': 'REAL',
    'smo2_medio_rec':  'REAL',
    'thb_medio_work':  'REAL',
    'thb_medio_rec':   'REAL',
    'resp_medio_work': 'REAL',
    'resp_medio_rec':  'REAL',
    'dfa1_medio_work': 'REAL',
    'dfa1_medio_rec':  'REAL',
    'tem_dfa1':        'INTEGER DEFAULT 0',
}


def _colunas_existentes(conn, tabela):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({tabela})").fetchall()}


def aplicar_schema(conn):
    """Aplica o schema numa conexao sqlite3 já aberta.

    Cria as tabelas se não existirem, e migra (ADD COLUMN) qualquer
    coluna em falta numa base de dados antiga — sem apagar dados.
    """
    conn.executescript(SCHEMA_SQL)
    conn.commit()

    existentes = _colunas_existentes(conn, 'fisiologia_intervalos')
    for coluna, tipo in COLUNAS_MIGRACAO.items():
        if coluna not in existentes:
            conn.execute(f"ALTER TABLE fisiologia_intervalos ADD COLUMN {coluna} {tipo}")
    conn.commit()
