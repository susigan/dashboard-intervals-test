-- Migracao 2026-10-24_ir_sombra_previsoes_001 (checksum 81d37d5c597a80ad...)
-- Aditiva e idempotente. NAO aplicada em producao. Revisar antes de executar.

CREATE TABLE IF NOT EXISTS schema_migracoes_ir (id TEXT PRIMARY KEY, checksum TEXT NOT NULL, aplicada_em TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS modelo_ir_sombra_previsoes (previsao_id TEXT NOT NULL PRIMARY KEY, calculado_em TEXT NOT NULL, data_alvo TEXT NOT NULL, modalidade TEXT NOT NULL, duracao_s INTEGER NOT NULL, modo TEXT NOT NULL, versao_modelo TEXT NOT NULL, lag_dias INTEGER NOT NULL, previsao_ir DOUBLE PRECISION, parametros TEXT, entradas_sig TEXT NOT NULL, estado_temporal TEXT NOT NULL, motivo_bloqueio TEXT);

CREATE TABLE IF NOT EXISTS modelo_ir_sombra_resultados (previsao_id TEXT NOT NULL, versao INTEGER NOT NULL, resultado_sig TEXT NOT NULL, test_id TEXT NOT NULL, y_observado DOUBLE PRECISION NOT NULL, observado_em TEXT NOT NULL, registado_em TEXT NOT NULL, PRIMARY KEY (previsao_id, versao), UNIQUE (previsao_id, resultado_sig));

CREATE INDEX IF NOT EXISTS ix_ir_sombra_prev_alvo ON modelo_ir_sombra_previsoes (modalidade, duracao_s, data_alvo, calculado_em);
