"""perfil_schema.py — schema do perfil_historico.db.

Guarda instantaneos do CP e do perfil metabolico ao longo do tempo, para se
poder ver se os valores e os intervalos mudaram. Tres tabelas:

  cp_resultados        um instantaneo por gravacao: todos os modelos
                       corridos, mais qual foi escolhido
  perfil_snapshots     um instantaneo do perfil metabolico
  limiares_snapshots   os quartis dos campos externos nessa data, para se
                       ver o intervalo a mover-se e nao so' a mediana

A data e' dada por quem grava (data_referencia), nao pelo relogio: um
instantaneo pode dizer respeito a uma season passada e ser gravado hoje.
data_gravacao guarda quando foi de facto escrito, para se distinguirem os
dois. A chave unica e' (tipo/modalidade, season, data_referencia), com
REPLACE, para que voltar a gravar o mesmo dia corrija em vez de duplicar.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS cp_resultados (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    data_referencia     TEXT NOT NULL,
    data_gravacao       TEXT NOT NULL,
    modalidade          TEXT NOT NULL,
    season              TEXT,
    modelo_escolhido    TEXT,
    cp_w                REAL,
    wp_j                REAL,
    see_pct             REAL,
    n_pts               INTEGER,
    k_params            INTEGER,
    pmax_w              REAL,
    mmp60_validacao_w   REAL,
    mmp_pts_json        TEXT,
    modelos_json        TEXT,
    veloclinic_json     TEXT,
    origem              TEXT,
    nota                TEXT,
    UNIQUE (modalidade, season, data_referencia)
);

CREATE TABLE IF NOT EXISTS perfil_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    data_referencia     TEXT NOT NULL,
    data_gravacao       TEXT NOT NULL,
    modalidade          TEXT NOT NULL,
    season              TEXT,
    vo2max              REAL,
    vlamax              REAL,
    lt1_w               REAL,
    lt1_convencao_w     REAL,
    lt2_w               REAL,
    mlss_w              REAL,
    fatmax_w            REAL,
    pvo2max_w           REAL,
    frac_utilizacao_pct REAL,
    cp_w                REAL,
    wp_j                REAL,
    peso_kg             REAL,
    bf_pct              REAL,
    mmp_json            TEXT,
    zonas_json          TEXT,
    entradas_json       TEXT,
    avisos              TEXT,
    origem              TEXT,
    UNIQUE (modalidade, season, data_referencia)
);

CREATE TABLE IF NOT EXISTS limiares_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    data_referencia     TEXT NOT NULL,
    data_gravacao       TEXT NOT NULL,
    modalidade          TEXT NOT NULL,
    season              TEXT,
    campo               TEXT NOT NULL,
    grupo               TEXT,
    unidade             TEXT,
    n                   INTEGER,
    p25                 REAL,
    p50                 REAL,
    p75                 REAL,
    minimo              REAL,
    maximo              REAL,
    watts_equivalente   REAL,
    hr_equivalente      REAL,
    constante           INTEGER,
    UNIQUE (modalidade, season, data_referencia, campo)
);

CREATE TABLE IF NOT EXISTS moxy_cortes (
    activity_id     TEXT PRIMARY KEY,
    modalidade      TEXT,
    data            TEXT,
    inicio_s        REAL,
    fim_s           REAL,
    origem          TEXT,
    proposto_s      REAL,
    nota            TEXT,
    data_gravacao   TEXT
);

CREATE TABLE IF NOT EXISTS moxy_analises (
    activity_id     TEXT PRIMARY KEY,
    modalidade      TEXT,
    data            TEXT,
    -- limiares
    perfil          TEXT,
    bp1_w           REAL,
    bp1_bpm         REAL,
    bp2_w           REAL,
    bp2_bpm         REAL,
    bp2_origem      TEXT,
    smo2max         REAL,
    smo2min         REAL,
    n_degraus       INTEGER,
    -- 5-1-5
    us_score        REAL,
    us_limitador    TEXT,
    pc_score        REAL,
    pc_limitador    TEXT,
    -- rede causal
    rede_limitador  TEXT,
    rede_pct        TEXT,
    -- qualidade e proveniencia
    pct_artefacto   REAL,
    corte_inicio_s  REAL,
    corte_fim_s     REAL,
    -- limiares pelo protocolo de degraus (Yogev/Rogers): as duas
    -- transicoes do mesmo teste, medidas opticamente
    lt1_reox_w      REAL,
    lt1_reox_de     REAL,
    lt1_reox_ate    REAL,
    mlss_dessat_w   REAL,
    mlss_dessat_de  REAL,
    mlss_dessat_ate REAL,
    versao_analise  TEXT,
    json_completo   TEXT,
    data_gravacao   TEXT
);

CREATE INDEX IF NOT EXISTS ix_moxy_mod_data
    ON moxy_analises (modalidade, data);

CREATE INDEX IF NOT EXISTS ix_cp_mod_data
    ON cp_resultados (modalidade, data_referencia);
CREATE INDEX IF NOT EXISTS ix_perfil_mod_data
    ON perfil_snapshots (modalidade, data_referencia);
CREATE INDEX IF NOT EXISTS ix_lim_mod_campo_data
    ON limiares_snapshots (modalidade, campo, data_referencia);
"""


def aplicar_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
