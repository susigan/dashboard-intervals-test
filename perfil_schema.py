"""perfil_schema.py — Schema do perfil_historico.db.

Princípio:
    CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN.
    Nunca DROP.
    Nunca DELETE.
    Nunca recria tabelas existentes.
    Cada chamada a aplicar_schema() é idempotente.

v6:
    Compatibilidade entre o schema normalizado e o contrato legado
    ainda utilizado pelo api_moxy.py para vst_conjuntos.

Importante:
    vst_results continua sendo a fonte de verdade dos resultados/versionamento.
    As colunas de compatibilidade em vst_conjuntos existem para manter
    compatibilidade com os endpoints atuais sem obrigar uma refatoração
    ampla do api_moxy.py nesta etapa.
"""

SCHEMA_VERSION = 6


_TABELAS = [

    # ──────────────────────────────────────────────────────────────
    # Metadados
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
    # Streams
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS activity_streams (
        activity_id     TEXT    NOT NULL,
        stream_name     TEXT    NOT NULL,
        stream_json     TEXT    NOT NULL,
        updated_at      TEXT,
        PRIMARY KEY (activity_id, stream_name)
    )""",

    # ──────────────────────────────────────────────────────────────
    # RPE por intervalo
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
    # Intervalos VST
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
    #
    # created_at / updated_at:
    #   schema normalizado.
    #
    # criado_em / actualizado_em:
    #   compatibilidade com api_moxy.py existente.
    #
    # resultado_json / analisado_em / validacao...:
    #   compatibilidade com os endpoints atuais.
    #
    # vst_results continua sendo o histórico versionado.
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS vst_conjuntos (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,

        moxy_activity_id            TEXT    NOT NULL,
        vst_activity_id             TEXT    NOT NULL,

        status                      TEXT    DEFAULT 'active',

        bp1_status                  TEXT,
        bp2_status                  TEXT,

        dia1_bp1_w                  REAL,
        dia2_bp1_w                  REAL,
        dia1_bp2_w                  REAL,
        dia2_bp2_w                  REAL,

        recovery_bp1_status         TEXT,
        recovery_bp2_status         TEXT,

        created_at                  TEXT,
        updated_at                  TEXT,

        -- Compatibilidade api_moxy.py
        criado_em                   TEXT,
        actualizado_em              TEXT,
        modalidade                  TEXT,

        resultado_json              TEXT,
        analisado_em                TEXT,

        validacao_fisiologica_json  TEXT,
        bpm_vst_validacao_json      TEXT,

        fisio_version               TEXT,
        fisio_data_hash             TEXT,

        UNIQUE (moxy_activity_id, vst_activity_id)
    )""",

    # ──────────────────────────────────────────────────────────────
    # Resultados versionados
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS vst_results (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,

        vst_conjunto_id             INTEGER NOT NULL
                                    REFERENCES vst_conjuntos(id),

        version                     INTEGER NOT NULL DEFAULT 1,

        analysis_version            TEXT,
        analysis_hash               TEXT,
        analyzed_at                TEXT,

        -- JSONs completos
        resultado_json              TEXT,
        validacao_fisiologica_json  TEXT,
        bpm_vst_validacao_json      TEXT,

        -- Campos explícitos BP
        bp1_w                       REAL,
        bp2_w                       REAL,
        bp1_bpm                     REAL,
        bp2_bpm                     REAL,

        bp1_json                    TEXT,
        bp2_json                    TEXT,

        -- Análise cruzada
        comparacao_rpe_bp1          TEXT,
        comparacao_rpe_bp2          TEXT,
        limiter_sintese             TEXT,
        recuperacao_final_dia2      TEXT,

        -- Qualidade RPE
        rpe_bp1_disponivel          INTEGER DEFAULT 0,
        rpe_bp2_disponivel          INTEGER DEFAULT 0,

        -- Análise integrada RPE × fisiologia
        rpe_fisiologia_json         TEXT,

        created_at                  TEXT
    )""",

    # ──────────────────────────────────────────────────────────────
    # Análises MOXY
    # ──────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS moxy_analyses (
        activity_id         TEXT    NOT NULL,
        version             INTEGER NOT NULL DEFAULT 1,
        bp1_w               REAL,
        bp1_bpm             REAL,
        bp2_w               REAL,
        bp2_bpm              REAL,
        json_completo       TEXT,
        analysis_version    TEXT,
        analyzed_at         TEXT,
        PRIMARY KEY (activity_id, version)
    )""",
]


# ──────────────────────────────────────────────────────────────────────────────
# Migrações de dados
# ──────────────────────────────────────────────────────────────────────────────

def _migrar_rpe_real(conn):
    """Garante armazenamento REAL do RPE.

    Idempotente.
    Não altera o valor numérico.
    """
    try:
        conn.execute(
            "UPDATE activity_interval_rpe "
            "SET rpe = rpe * 1.0 "
            "WHERE rpe IS NOT NULL"
        )
        conn.commit()
    except Exception:
        # A tabela pode ainda não existir numa base muito antiga.
        pass


def _adicionar_coluna_se_necessaria(conn, tabela, coluna, tipo):
    """ALTER TABLE ADD COLUMN idempotente.

    Não usa DROP, não recria tabela e não altera colunas existentes.
    """
    try:
        cols = {
            row[1]
            for row in conn.execute(
                f"PRAGMA table_info({tabela})"
            ).fetchall()
        }

        if coluna not in cols:
            conn.execute(
                f"ALTER TABLE {tabela} "
                f"ADD COLUMN {coluna} {tipo}"
            )
            conn.commit()

    except Exception:
        # Compatibilidade com bases extremamente antigas.
        # A chamada seguinte a aplicar_schema() poderá tentar novamente.
        pass


def _migrar_v6_compatibilidade_vst(conn):
    """v6 — alinha vst_conjuntos com o contrato atualmente utilizado
    pelo api_moxy.py.

    Problema corrigido:
        api_moxy.py ainda lê/grava:
            criado_em
            actualizado_em
            modalidade
            resultado_json
            analisado_em
            validacao_fisiologica_json
            bpm_vst_validacao_json
            fisio_version
            fisio_data_hash

        O schema normalizado possuía apenas:
            created_at
            updated_at
            e os campos estruturais do conjunto.

    A solução é aditiva:
        - adiciona somente as colunas ausentes;
        - mantém created_at/updated_at;
        - não apaga nenhum resultado;
        - recupera resultados existentes de vst_results quando possível.
    """

    compat = {
        'criado_em':                  'TEXT',
        'actualizado_em':             'TEXT',
        'modalidade':                 'TEXT',
        'resultado_json':             'TEXT',
        'analisado_em':               'TEXT',
        'validacao_fisiologica_json': 'TEXT',
        'bpm_vst_validacao_json':     'TEXT',
        'fisio_version':              'TEXT',
        'fisio_data_hash':            'TEXT',
    }

    for coluna, tipo in compat.items():
        _adicionar_coluna_se_necessaria(
            conn,
            'vst_conjuntos',
            coluna,
            tipo
        )

    # Garantir que vst_results antigo também tenha o JSON integrado.
    _adicionar_coluna_se_necessaria(
        conn,
        'vst_results',
        'rpe_fisiologia_json',
        'TEXT'
    )

    # ──────────────────────────────────────────────────────────────
    # Sincronizar nomes de data antigos com o schema normalizado.
    # ──────────────────────────────────────────────────────────────

    try:
        conn.execute(
            """
            UPDATE vst_conjuntos
               SET criado_em = COALESCE(criado_em, created_at)
             WHERE criado_em IS NULL
                OR criado_em = ''
            """
        )

        conn.execute(
            """
            UPDATE vst_conjuntos
               SET actualizado_em = COALESCE(actualizado_em, updated_at)
             WHERE actualizado_em IS NULL
                OR actualizado_em = ''
            """
        )

        conn.commit()

    except Exception:
        pass

    # ──────────────────────────────────────────────────────────────
    # Recuperar o último resultado versionado para os campos de
    # compatibilidade usados pelo api_moxy.py.
    #
    # IMPORTANTE:
    #   não cria uma nova versão;
    #   não apaga versões;
    #   somente copia o resultado mais recente para o snapshot
    #   compatível com os endpoints antigos.
    # ──────────────────────────────────────────────────────────────

    try:
        conn.execute(
            """
            UPDATE vst_conjuntos AS c
               SET resultado_json = COALESCE(
                       c.resultado_json,
                       (
                           SELECT r.resultado_json
                             FROM vst_results AS r
                            WHERE r.vst_conjunto_id = c.id
                            ORDER BY r.version DESC
                            LIMIT 1
                       )
                   ),
                   analisado_em = COALESCE(
                       c.analisado_em,
                       (
                           SELECT r.analyzed_at
                             FROM vst_results AS r
                            WHERE r.vst_conjunto_id = c.id
                            ORDER BY r.version DESC
                            LIMIT 1
                       )
                   ),
                   validacao_fisiologica_json = COALESCE(
                       c.validacao_fisiologica_json,
                       (
                           SELECT r.validacao_fisiologica_json
                             FROM vst_results AS r
                            WHERE r.vst_conjunto_id = c.id
                            ORDER BY r.version DESC
                            LIMIT 1
                       )
                   ),
                   bpm_vst_validacao_json = COALESCE(
                       c.bpm_vst_validacao_json,
                       (
                           SELECT r.bpm_vst_validacao_json
                             FROM vst_results AS r
                            WHERE r.vst_conjunto_id = c.id
                            ORDER BY r.version DESC
                            LIMIT 1
                       )
                   )
            """
        )

        conn.commit()

    except Exception:
        pass

    # ──────────────────────────────────────────────────────────────
    # Se resultado_json já existir mas rpe_fisiologia_json estiver
    # vazio, não fazemos parsing de JSON aqui.
    #
    # A análise RPE × fisiologia será gerada novamente pelo fluxo
    # Comparar / Sincronizar, que é a fonte correta para essa análise.
    # ──────────────────────────────────────────────────────────────


def aplicar_schema(conn):
    """Aplica todas as tabelas e migrações.

    Idempotente e não-destrutivo.
    """

    cur = conn.cursor()

    cur.execute("PRAGMA foreign_keys=ON")

    # Criar somente tabelas inexistentes.
    for ddl in _TABELAS:
        cur.execute(ddl)

    # Migrações de dados.
    _migrar_rpe_real(conn)
    _migrar_v6_compatibilidade_vst(conn)

    # ──────────────────────────────────────────────────────────────
    # Metadados
    # ──────────────────────────────────────────────────────────────

    import datetime

    agora = datetime.datetime.now().isoformat(
        timespec='seconds'
    )

    cur.execute(
        """
        INSERT INTO db_metadata(key, value, updated_at)
        VALUES(?,?,?)
        ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,
            updated_at=excluded.updated_at
        """,
        (
            'schema_version',
            str(SCHEMA_VERSION),
            agora
        )
    )

    cur.execute(
        """
        INSERT INTO db_metadata(key, value, updated_at)
        VALUES(?,?,?)
        ON CONFLICT(key) DO UPDATE SET
            updated_at=excluded.updated_at
        """,
        (
            'db_created_at',
            agora,
            agora
        )
    )

    conn.commit()
