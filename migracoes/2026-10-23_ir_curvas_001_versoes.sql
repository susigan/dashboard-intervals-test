-- Migracao 2026-10-23_ir_curvas_001_versoes (checksum 56e6cfce1ddf8e1c...)
-- Aditiva e idempotente. NAO aplicada em producao. Revisar antes de executar.

CREATE TABLE IF NOT EXISTS schema_migracoes_ir (id TEXT PRIMARY KEY, checksum TEXT NOT NULL, aplicada_em TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS ir_curvas_versoes (activity_id TEXT NOT NULL, conhecido_em TEXT NOT NULL, hash TEXT NOT NULL, tipo TEXT, data TEXT NOT NULL, peso DOUBLE PRECISION, secs TEXT NOT NULL, watts TEXT NOT NULL, PRIMARY KEY (activity_id, conhecido_em));

CREATE INDEX IF NOT EXISTS ix_ir_curvas_conhecido ON ir_curvas_versoes (conhecido_em);
