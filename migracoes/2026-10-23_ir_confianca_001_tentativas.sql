-- Migracao 2026-10-23_ir_confianca_001_tentativas (checksum 38ead21bc47938c3...)
-- Aditiva e idempotente. NAO aplicada em producao. Revisar antes de executar.

CREATE TABLE IF NOT EXISTS schema_migracoes_ir (id TEXT PRIMARY KEY, checksum TEXT NOT NULL, aplicada_em TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS modelo_ir_confianca_tentativas (
    modalidade TEXT NOT NULL,
    duracao_s INTEGER NOT NULL,
    assinatura_dados TEXT NOT NULL,
    configuracao TEXT NOT NULL,
    politica_hash TEXT NOT NULL,
    tentado_em TEXT NOT NULL,
    origem TEXT NOT NULL,
    esquema TEXT NOT NULL,
    nivel TEXT NOT NULL,
    estado TEXT NOT NULL,
    validada INTEGER NOT NULL,
    confianca_parametros TEXT NOT NULL,
    confianca_previsao TEXT NOT NULL,
    motivos TEXT NOT NULL,
    limitacoes TEXT NOT NULL,
    teria_promovido INTEGER NOT NULL,
    acao_proposta TEXT NOT NULL,
    motivo_acao TEXT NOT NULL,
    versao_operacional_ref TEXT,
    resultado TEXT NOT NULL,
    decisao TEXT NOT NULL,
    PRIMARY KEY (modalidade, duracao_s, assinatura_dados, configuracao, politica_hash)
);

CREATE INDEX IF NOT EXISTS ix_modelo_ir_conf_ult ON modelo_ir_confianca_tentativas (modalidade, duracao_s, teria_promovido, tentado_em);
