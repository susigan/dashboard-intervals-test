# Sequência de aplicação das migrações do IR (para revisão humana)

**Não aplicada.** Nenhum destes passos foi executado em produção. Cada passo é aditivo e idempotente.

## Ordem

1. `2026-10-23_ir_confianca_001_tentativas.sql` — cria o registo `schema_migracoes_ir` e a tabela da sombra.
2. `2026-10-23_ir_temporal_001_marcos_yes.sql` — tabelas `ir_marcos_snapshots` e `ir_yes_transicoes`.
3. `2026-10-23_ir_curvas_001_versoes.sql` — tabela `ir_curvas_versoes` e índice `ix_ir_curvas_conhecido`.
4. `2026-10-24_ir_sombra_previsoes_001.sql` — tabelas `modelo_ir_sombra_previsoes` e `modelo_ir_sombra_resultados` (registo prospetivo, Etapa 16). Integrada no sync de forma não bloqueante: sem esta migração o produtor não escreve nada (`migracao_pendente`).

Cada migração cria o registo se ainda não existir, por isso a ordem não é funcional. A ordem
acima é a que se revê e aplica.

## Checksums (SHA-256 das instruções, 16 primeiros caracteres)

| Migração | Checksum |
|---|---|
| confianca_001_tentativas | 38ead21bc47938c3 |
| temporal_001_marcos_yes | b0dc5dc7e6ef027f |
| curvas_001_versoes | 56e6cfce1ddf8e1c |
| sombra_previsoes_001 | 81d37d5c597a80ad |

O teste `test_ficheiros_exportados_correspondem_ao_codigo` falha se um ficheiro não corresponder ao código.

## Propriedades verificadas (SQLite e PostgreSQL/psycopg v3)

- Só `CREATE TABLE IF NOT EXISTS` e `CREATE INDEX IF NOT EXISTS`. Sem DROP, ALTER, UPDATE, DELETE.
- Reaplicar devolve `ja_aplicada` e não altera dados.
- Checksum diferente levanta `MigracaoAlterada` e não altera nada.
- Linhas existentes sobrevivem à reaplicação.
- Antes da aplicação, os leitores consultam o **catálogo** e não executam SELECT que possa falhar.
  Sem tabelas, o sistema não escreve e não avalia.

## Índices e consultas de reconstrução

- `ir_marcos_snapshots`: chave primária em `conhecido_em` cobre `marcos_conhecidos_em`.
- `ir_yes_transicoes`: `(test_id, conhecido_em)` cobre o último YES por teste.
- `ir_curvas_versoes`: PK `(activity_id, conhecido_em)` cobre a última versão por atividade;
  `ix_ir_curvas_conhecido (conhecido_em)` cobre o filtro de corte. (Na Etapa 13 o índice era
  `(tipo, conhecido_em)`, que não servia estas consultas; corrigido antes da aplicação.)

## Antes de aplicar (revisão)

1. Confirmar que o registo `schema_migracoes_ir` não tem os três IDs, ou que os checksums coincidem.
2. Confirmar que as tabelas-alvo não existem, ou que têm a forma acima.
3. Fazer cópia de segurança da base antes de qualquer DDL.
4. Aplicar uma migração de cada vez, verificando `SELECT id, checksum FROM schema_migracoes_ir`.

## Depois de aplicar

- Os registos ficam vazios. O estado do contrato passa a `vazio`, e a avaliação temporal continua bloqueada
  até existir um registo de marcos e de YES, e uma versão de curvas.
- As curvas só ganham versão na primeira sincronização posterior à migração. Os alvos anteriores são rejeitados.

## Reversão

Não existe reversão automática. Se for necessária, deve ser revista caso a caso. As tabelas são novas e
vazias até à primeira escrita, por isso a remoção manual é a única forma de desfazer.
