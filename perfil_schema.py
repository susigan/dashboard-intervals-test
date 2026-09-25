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

-- RPE (esforço percebido, 1-10) por bloco de TRABALHO de uma sessão.
--
-- Um bloco pode ser regravado: o atleta engana-se a escrever o RPE, ou
-- quer corrigir depois de reflectir. Por isso a chave e' (activity_id,
-- bloco_indice), nao um id proprio -- gravar outra vez SUBSTITUI a
-- entrada anterior desse bloco, nunca acumula duplicados.
CREATE TABLE IF NOT EXISTS moxy_rpe (
    activity_id     TEXT NOT NULL,
    bloco_indice    INTEGER NOT NULL,
    watts_medio     REAL,
    t0_s            REAL,
    t1_s            REAL,
    rpe             INTEGER NOT NULL,
    gravado_em      TEXT,
    PRIMARY KEY (activity_id, bloco_indice)
);
"""


# Colunas acrescentadas depois de a tabela ja' existir em producao.
#
# O "CREATE TABLE IF NOT EXISTS" nao toca numa tabela que ja' existe: se o
# ficheiro no Drive foi criado antes destas colunas, elas nunca aparecem e
# o INSERT rebenta com "table moxy_analises has no column named ...".
#
# Formato: (tabela, coluna, tipo). Correr ALTER TABLE para cada uma que
# falte e' barato e idempotente.
MIGRACOES = [
    ('moxy_analises', 'lt1_reox_w', 'REAL'),
    ('moxy_analises', 'lt1_reox_de', 'REAL'),
    ('moxy_analises', 'lt1_reox_ate', 'REAL'),
    ('moxy_analises', 'mlss_dessat_w', 'REAL'),
    ('moxy_analises', 'mlss_dessat_de', 'REAL'),
    ('moxy_analises', 'mlss_dessat_ate', 'REAL'),
    # VO2max previsto pela regressão SmO2×FC (Peikon, NNOXX) — para
    # cruzar no Perfil Metabólico com o VO2max do modelo de Hawley.
    ('moxy_analises', 'vo2max_previsto', 'REAL'),
    ('moxy_analises', 'vo2max_plausivel', 'INTEGER'),
    # Snapshot do ultimo resultado da comparacao Dia1xDia2, para nao
    # ter de recalcular so' para mostrar a lista "verificacoes salvas".
    # Os valores estruturados sao os que ja vem de comparar_bp/
    # comparar_recovery -- nunca recalculados aqui, so' guardados.
    ('vst_conjuntos', 'bp1_status', 'TEXT'),
    ('vst_conjuntos', 'bp2_status', 'TEXT'),
    ('vst_conjuntos', 'recovery_bp1_status', 'TEXT'),
    ('vst_conjuntos', 'recovery_bp2_status', 'TEXT'),
    ('vst_conjuntos', 'dia1_bp1_w', 'REAL'),
    ('vst_conjuntos', 'dia2_bp1_w', 'REAL'),
    ('vst_conjuntos', 'dia1_bp2_w', 'REAL'),
    ('vst_conjuntos', 'dia2_bp2_w', 'REAL'),
    ('vst_conjuntos', 'resultado_json', 'TEXT'),
    ('vst_conjuntos', 'analisado_em', 'TEXT'),
]

# Escolha do atleta: usar os blocos WORK/RECOVERY da Intervals.icu
# (icu_intervals) ou a detecção automática nossa, quando a API falha ou
# dá blocos que o atleta não confia. Por omissão ('automatico' quando
# não há registo) mantém-se o comportamento actual: tenta icu_intervals,
# cai para detecção automática só se aquele falhar.

# RPE manual por intervalo de qualquer actividade.
# Fonte de verdade universal: uma anotação por (activity_id, start_time).
# start_time = icu_interval.start_time = bloco.t0 (via blocos_de_laps).
# rpe=0   → apagado explicitamente; NÃO fazer fallback para moxy_rpe.
# rpe=1..10 → valor real.
# Sem linha → sem anotação nova; fallback para moxy_rpe (legado).
SCHEMA_ACTIVITY_INTERVAL_RPE = """
CREATE TABLE IF NOT EXISTS activity_interval_rpe (
    activity_id     TEXT    NOT NULL,
    start_time      REAL    NOT NULL,
    interval_type   TEXT,
    elapsed_time    REAL,
    rpe             INTEGER NOT NULL CHECK (rpe BETWEEN 0 AND 10),
    source          TEXT    NOT NULL DEFAULT 'manual',
    updated_at      TEXT    NOT NULL,
    PRIMARY KEY (activity_id, start_time)
);
"""

SCHEMA_MODO_BLOCOS = """
CREATE TABLE IF NOT EXISTS moxy_modo_blocos (
    activity_id   TEXT PRIMARY KEY,
    modo          TEXT NOT NULL,   -- 'automatico' | 'sincronizado'
    gravado_em    TEXT NOT NULL
)
"""

# Vinculo persistente entre uma sessao VST e a sessao correspondente da
# tab Moxy -- um "conjunto de verificacao". Chave primaria e' o proprio
# id da sessao VST (uma sessao VST pertence no maximo a um conjunto de
# cada vez; escolher outra Moxy para a mesma VST substitui o vinculo,
# nunca acumula). moxy_activity_id tem indice proprio para a procura
# inversa (abrir pela sessao Moxy).
SCHEMA_VST_CONJUNTO = """
CREATE TABLE IF NOT EXISTS vst_conjuntos (
    vst_activity_id    TEXT PRIMARY KEY,
    moxy_activity_id   TEXT NOT NULL,
    criado_em          TEXT NOT NULL,
    actualizado_em     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_vst_conjuntos_moxy
    ON vst_conjuntos(moxy_activity_id)
"""


def migrar(conn):
    """Acrescenta colunas em falta a tabelas que ja' existem."""
    feitas, erros = [], []
    for tabela, coluna, tipo in MIGRACOES:
        try:
            existe = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name=?", (tabela,)).fetchone()
            if not existe:
                continue
            cols = {r[1] for r in
                    conn.execute(f"PRAGMA table_info({tabela})")}
            if coluna in cols:
                continue
            conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
            feitas.append(f'{tabela}.{coluna}')
        except Exception as e:
            erros.append(f'{tabela}.{coluna}: {e}')
    if feitas:
        conn.commit()
    return {'acrescentadas': feitas, 'erros': erros}


def aplicar_schema(conn):
    conn.executescript(SCHEMA)
    conn.execute(SCHEMA_MODO_BLOCOS)
    conn.executescript(SCHEMA_VST_CONJUNTO)
    conn.executescript(SCHEMA_ACTIVITY_INTERVAL_RPE)
    conn.commit()
    migrar(conn)
    return conn
