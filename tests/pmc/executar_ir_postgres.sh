#!/usr/bin/env bash
# Executa os testes PostgreSQL do IR num processo PROPRIO (db.py fixa o driver na importacao).
# Requer um PostgreSQL de teste e uma base descartavel *_tmp num socket local absoluto. Nunca usa producao.
#   IR_PG_SOCKET=/var/tmp/pg_etapa5 IR_PG_PORT=55432 IR_PG_DB=ir_tmp ./tests/pmc/executar_ir_postgres.sh
set -euo pipefail
SOCK="${IR_PG_SOCKET:?defina IR_PG_SOCKET (diretorio do socket local absoluto)}"
PORTA="${IR_PG_PORT:-55432}"
BASE="${IR_PG_DB:-ir_tmp}"
case "$BASE" in *_tmp) ;; *) echo "recusado: a base tem de terminar em _tmp" >&2; exit 2;; esac
export IR_PG_TEST_URL="postgresql://postgres@/${BASE}?host=${SOCK}&port=${PORTA}"
export IR_TESTE_PG_DSN="host=${SOCK} port=${PORTA} user=postgres dbname=${BASE}"
cd "$(dirname "$0")/../.."
python -m pytest tests/pmc/test_ir_postgres.py tests/pmc/test_ir_migracao.py tests/pmc/test_ir_sombra.py -q -rs -p no:warnings
