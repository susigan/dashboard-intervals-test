-- Migracao 2026-10-23_ir_temporal_001_marcos_yes (checksum b0dc5dc7e6ef027f...)
-- Aditiva e idempotente. NAO aplicada em producao. Revisar antes de executar.

CREATE TABLE IF NOT EXISTS schema_migracoes_ir (id TEXT PRIMARY KEY, checksum TEXT NOT NULL, aplicada_em TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS ir_marcos_snapshots (conhecido_em TEXT NOT NULL PRIMARY KEY, hash TEXT NOT NULL, fonte TEXT NOT NULL, marcos TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS ir_yes_transicoes (test_id TEXT NOT NULL, conhecido_em TEXT NOT NULL, estado TEXT NOT NULL, modalidade TEXT NOT NULL, activity_id TEXT, watts DOUBLE PRECISION, regra_versao INTEGER, origem TEXT, revisao INTEGER, PRIMARY KEY (test_id, conhecido_em));

CREATE INDEX IF NOT EXISTS ix_ir_yes_trans_test ON ir_yes_transicoes (test_id, conhecido_em);
