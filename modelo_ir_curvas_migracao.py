"""Migracao PREPARADA (Etapa 13) do historico de curvas: tabela ir_curvas_versoes.

NAO aplicada por nenhum caminho automatico. O registo so ESCREVE se a tabela existir (tabela_existe()).
Sem a migracao: a sincronizacao segue normalmente e o estado e 'tabelas_ausentes' (nada e gravado).

Propriedades (as mesmas das migracoes anteriores):
  * aditiva: so CREATE ... IF NOT EXISTS. Nunca DROP, ALTER, UPDATE ou DELETE.
  * idempotente: registo com checksum. Mesmo checksum -> 'ja_aplicada'. Checksum diferente -> MigracaoAlterada.
  * mesmo DDL em SQLite e PostgreSQL (TEXT, DOUBLE PRECISION). Chave primaria (activity_id, conhecido_em).
  * usa o registo schema_migracoes_ir e nao toca em power_curves nem em nenhuma tabela operacional.
"""
import hashlib

import modelo_ir_curvas as mc
from modelo_ir_confianca_migracao import DDL_REGISTO, REGISTO, registo_existe
from modelo_ir_sombra import sql_para

MIGRACAO_ID = '2026-10-23_ir_curvas_001_versoes'


class MigracaoAlterada(RuntimeError):
    pass


def instrucoes():
    """Sequencia fixa da migracao (identica nos dois motores)."""
    return [DDL_REGISTO, mc.DDL, mc.INDICE]


def checksum():
    return hashlib.sha256('\n;\n'.join(instrucoes()).encode('utf-8')).hexdigest()


def exportar_sql(caminho):
    """Escreve o SQL para revisao humana (sem executar)."""
    cab = (f'-- Migracao {MIGRACAO_ID} (checksum {checksum()[:16]}...)\n'
           f'-- Aditiva e idempotente. NAO aplicada em producao. Revisar antes de executar.\n\n')
    with open(caminho, 'w', encoding='utf-8') as f:
        f.write(cab + ';\n\n'.join(instrucoes()) + ';\n')
    return caminho


def estado(executor, dialeto):
    """'nao_aplicada' | 'aplicada' | ('alterada', checksum_registado). Nao escreve nada."""
    if not registo_existe(executor, dialeto):   # catalogo primeiro: sem registo nao ha SELECT que possa falhar
        return 'nao_aplicada'
    try:
        row = executor(sql_para(dialeto, f'SELECT checksum FROM {REGISTO} WHERE id = ?'), (MIGRACAO_ID,), 'one')
    except Exception:  # noqa: BLE001
        return 'nao_aplicada'
    if row is None:
        return 'nao_aplicada'
    return 'aplicada' if row[0] == checksum() else ('alterada', row[0])


def aplicar(executor, dialeto, agora):
    """Aplica se ainda nao estiver aplicada. Devolve 'aplicada' | 'ja_aplicada'.
    Levanta MigracaoAlterada se o registo tiver outro checksum (nada e alterado nesse caso)."""
    executor(DDL_REGISTO, (), None)
    e = estado(executor, dialeto)
    if e == 'aplicada':
        return 'ja_aplicada'
    if isinstance(e, tuple):
        raise MigracaoAlterada(f'{MIGRACAO_ID} ja aplicada com checksum {e[1][:16]}... diferente do atual')
    for sql in instrucoes()[1:]:
        executor(sql, (), None)   # DDL idempotente: repetir numa corrida com outro processo e inofensivo
    executor(sql_para(dialeto, f'INSERT INTO {REGISTO} (id, checksum, aplicada_em) VALUES (?, ?, ?) '
                               'ON CONFLICT (id) DO NOTHING'), (MIGRACAO_ID, checksum(), agora), None)
    return 'aplicada'
