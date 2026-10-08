"""moxy_vst_schema.py — Schema do moxy_vst_historico.db.

Princípio: CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN.
Nunca DROP, nunca DELETE, nunca recria tabelas existentes.
Cada chamada a aplicar_schema() é idempotente.
"""

SCHEMA_VERSION = 4  # v4: rpe REAL — aceita qualquer decimal

_TABELAS = [

    # ──────────────────────────────────────────────────────────────
    # Metadados do próprio banco
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS db_metadata (
        key         TEXT PRIMARY KEY,
        value       TEXT,
        updated_at  TEXT
    )""",

    # ──────────────────────────────────────────────────────────────
    # Atividades MOXY
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS moxy_activities (
        activity_id         TEXT PRIMARY KEY,
        activity_name       TEXT,
        activity_date       TEXT,
        sport               TEXT,
        tag                 TEXT,
        source              TEXT DEFAULT 'intervals_icu',
        raw_metadata_json   TEXT,
        created_at          TEXT,
        updated_at          TEXT
    )""",

    # ──────────────────────────────────────────────────────────────
    # Atividades VST
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS vst_activities (
        activity_id         TEXT PRIMARY KEY,
        activity_name       TEXT,
        activity_date       TEXT,
        sport               TEXT,
        tag                 TEXT,
        raw_metadata_json   TEXT,
        created_at          TEXT,
        updated_at          TEXT
    )""",

    # ──────────────────────────────────────────────────────────────
    # Streams brutos (uma linha por stream por atividade)
    # stream_name: 'heartrate', 'watts', 'smo2', 'thb', 'dfa1',
    #              'respiration', 'velocity', 'cadence', etc.
    # stream_json: lista JSON [[t0, v0], [t1, v1], ...]
    #              ou {"time":[...], "data":[...]}
    # Arquitetura extensível: novos streams → nova linha, zero DDL.
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS activity_streams (
        activity_id     TEXT    NOT NULL,
        stream_name     TEXT    NOT NULL,
        stream_json     TEXT    NOT NULL,
        updated_at      TEXT,
        PRIMARY KEY (activity_id, stream_name)
    )""",

    # ──────────────────────────────────────────────────────────────
    # RPE por intervalo — fonte canônica e permanente
    # rpe=NULL + source='deleted' → apagado explicitamente pelo utilizador
    # rpe=1..10 + source='manual' → valor real gravado
    # Nunca 0 como fallback. Nunca apagar fisicamente.
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS activity_interval_rpe (
        activity_id     TEXT    NOT NULL,
        start_time      REAL    NOT NULL,
        end_time        REAL,
        interval_index  INTEGER,
        interval_type   TEXT,
        elapsed_time    REAL,
        rpe             REAL,
        rpe_status      TEXT    DEFAULT 'recorded',
        source          TEXT    DEFAULT 'manual',
        created_at      TEXT,
        updated_at      TEXT,
        PRIMARY KEY (activity_id, start_time)
    )""",

    # ──────────────────────────────────────────────────────────────
    # Intervalos VST (blocos de trabalho BP1/BP2)
    # raw_interval_json preserva o bloco original completo
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS vst_intervals (
        activity_id         TEXT    NOT NULL,
        interval_index      INTEGER NOT NULL,
        grupo               TEXT,
        start_time          REAL,
        end_time            REAL,
        duration_s          REAL,
        power_w             REAL,
        heart_rate_bpm      REAL,
        respiration         REAL,
        smo2_pct            REAL,
        thb_gdl             REAL,
        dfa1                REAL,
        raw_interval_json   TEXT,
        updated_at          TEXT,
        PRIMARY KEY (activity_id, interval_index)
    )""",

    # ──────────────────────────────────────────────────────────────
    # Conjuntos MOXY × VST
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS vst_conjuntos (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        moxy_activity_id        TEXT    NOT NULL,
        vst_activity_id         TEXT    NOT NULL,
        bp1_status              TEXT,
        bp2_status              TEXT,
        dia1_bp1_w              REAL,
        dia2_bp1_w              REAL,
        dia1_bp2_w              REAL,
        dia2_bp2_w              REAL,
        recovery_bp1_status     TEXT,
        recovery_bp2_status     TEXT,
        modalidade              TEXT,
        resultado_json          TEXT,
        validacao_fisiologica_json TEXT,
        bpm_vst_validacao_json  TEXT,
        analisado_em            TEXT,
        criado_em               TEXT,
        actualizado_em          TEXT,
        UNIQUE (moxy_activity_id, vst_activity_id)
    )""",

    # ──────────────────────────────────────────────────────────────
    # Resultados de verificação (um registo por versão de análise)
    # Nunca apagar versões antigas — só adicionar novas linhas.
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS vst_results (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        vst_conjunto_id             INTEGER NOT NULL
                                    REFERENCES vst_conjuntos(id),
        version                     INTEGER NOT NULL DEFAULT 1,
        analysis_version            TEXT,
        analysis_hash               TEXT,
        analyzed_at                 TEXT,

        -- JSONs completos (fonte de verdade)
        resultado_json              TEXT,
        validacao_fisiologica_json  TEXT,
        bpm_vst_validacao_json      TEXT,

        -- Campos explícitos de BP (redundância deliberada para queries simples)
        bp1_w                       REAL,
        bp2_w                       REAL,
        bp1_bpm                     REAL,
        bp2_bpm                     REAL,
        bp1_json                    TEXT,
        bp2_json                    TEXT,

        -- Campos de análise cruzada
        comparacao_rpe_bp1          TEXT,
        comparacao_rpe_bp2          TEXT,
        limiter_sintese             TEXT,
        recuperacao_final_dia2      TEXT,

        -- Flags de qualidade de dados
        rpe_bp1_disponivel          INTEGER DEFAULT 0,
        rpe_bp2_disponivel          INTEGER DEFAULT 0,

        created_at                  TEXT
    )""",

    # ──────────────────────────────────────────────────────────────
    # Análises MOXY (bp1_w, bp2_w, limiares calculados)
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS moxy_analyses (
        activity_id         TEXT    NOT NULL,
        version             INTEGER NOT NULL DEFAULT 1,
        bp1_w               REAL,
        bp1_bpm             REAL,
        bp2_w               REAL,
        bp2_bpm             REAL,
        json_completo       TEXT,
        analysis_version    TEXT,
        analyzed_at         TEXT,
        PRIMARY KEY (activity_id, version)
    )""",

    # ──────────────────────────────────────────────────────────────
    # Snapshot da atividade (botão "Salvar dados desta atividade",
    # rota api_activity_salvar_snapshot em app.py). Uma linha por
    # activity_id; o UPSERT da rota não apaga campos existentes.
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS activity_snapshot (
        activity_id         TEXT    PRIMARY KEY,
        nome                TEXT,
        data                TEXT,
        modalidade          TEXT,
        elapsed_time        REAL,
        avg_watts           REAL,
        avg_hr              REAL,
        rpe_sessao          REAL,
        z1_sec              REAL,
        z2_sec              REAL,
        z3_sec              REAL,
        icu_intervals_json  TEXT,
        rpe_intervalos_json TEXT,
        gravado_em          TEXT
    )""",
]

# Colunas a adicionar em tabelas existentes (migrações não destrutivas)
# Garante que bancos criados com schemas anteriores ganham as novas colunas.
_MIGRATIONS = [
    # vst_conjuntos — colunas originais que podem faltar em bancos migrados
    ("vst_conjuntos", "modalidade",               "TEXT", "NULL"),
    ("vst_conjuntos", "resultado_json",            "TEXT", "NULL"),
    ("vst_conjuntos", "validacao_fisiologica_json","TEXT", "NULL"),
    ("vst_conjuntos", "bpm_vst_validacao_json",    "TEXT", "NULL"),
    ("vst_conjuntos", "analisado_em",              "TEXT", "NULL"),
    ("vst_conjuntos", "criado_em",                 "TEXT", "NULL"),
    ("vst_conjuntos", "actualizado_em",            "TEXT", "NULL"),
]

# Migrações de dados (executadas em aplicar_schema, idempotentes)
def _migrar_rpe_real(conn):
    """v4: converte rpe INTEGER -> REAL no banco existente.

    SQLite aceita REAL mesmo em coluna declarada INTEGER (duck typing),
    mas este UPDATE garante que o tipo de armazenamento interno seja REAL.
    UPDATE ... * 1.0 nao altera valores: 6 -> 6.0, 4.2 -> 4.2.
    Idempotente: pode ser re-executada sem danos.
    """
    try:
        conn.execute(
            "UPDATE activity_interval_rpe "
            "SET rpe = rpe * 1.0 WHERE rpe IS NOT NULL")
        conn.commit()
    except Exception:
        pass  # tabela pode nao existir ainda


def aplicar_schema(conn):
    """Aplica todas as tabelas e migrações. Idempotente e não-destrutivo."""
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")

    for ddl in _TABELAS:
        cur.execute(ddl)

    # Migrações: ALTER TABLE ADD COLUMN (ignora se já existe)
    for tabela, coluna, tipo, default in _MIGRATIONS:
        try:
            cur.execute(
                f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo} DEFAULT {default}"
            )
        except Exception:
            pass  # coluna já existe

    # Migracoes de dados (idempotentes)
    _migrar_rpe_real(conn)

    # Atualizar metadados do schema
    import datetime
    agora = datetime.datetime.now().isoformat(timespec='seconds')
    cur.execute(
        "INSERT INTO db_metadata(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        ('schema_version', str(SCHEMA_VERSION), agora)
    )
    cur.execute(
        "INSERT INTO db_metadata(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET updated_at=excluded.updated_at",
        ('db_created_at', agora, agora)
    )
    conn.commit()
