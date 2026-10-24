"""Migracao PREPARADA para revisao: tabela de tentativas do modo sombra + registo de migracoes.

Nao e aplicada por nenhum caminho automatico. Aplicar exige chamada explicita (aplicar()) revista.
Propriedades:
  * aditiva: so CREATE ... IF NOT EXISTS. Nunca DROP, ALTER destrutivo, UPDATE ou DELETE.
  * idempotente: registo com checksum. Mesmo checksum -> 'ja_aplicada' (nada executado).
    Checksum diferente -> MigracaoAlterada (nunca sobrescreve uma migracao ja aplicada).
  * mesmo DDL em SQLite e PostgreSQL (tipos TEXT/INTEGER; sem tipos especificos de motor).
  * concorrencia: dois processos podem tentar criar a mesma tabela; o erro de 'ja existe' e tolerado
    se a tabela existir depois. Nao ha transacao unica sobre todas as instrucoes (DDL idempotente basta).
Ficheiros para revisao: migracoes/ (gerados por exportar_sql()).
"""
import hashlib

import modelo_ir_sombra as sombra
from modelo_ir_sombra import sql_para

MIGRACAO_ID = '2026-10-23_ir_confianca_001_tentativas'
REGISTO = 'schema_migracoes_ir'

DDL_REGISTO = (f"CREATE TABLE IF NOT EXISTS {REGISTO} ("
               f"id TEXT PRIMARY KEY, checksum TEXT NOT NULL, aplicada_em TEXT NOT NULL)")
INDICE_ULTIMA = ("CREATE INDEX IF NOT EXISTS ix_modelo_ir_conf_ult "
                 f"ON {sombra.NOME_TABELA} (modalidade, duracao_s, teria_promovido, tentado_em)")


class MigracaoAlterada(RuntimeError):
    pass


def instrucoes():
    """Sequencia da migracao (ordem fixa). Identica para os dois motores."""
    return [DDL_REGISTO, sombra.DDL, INDICE_ULTIMA]


def checksum():
    return hashlib.sha256('\n;\n'.join(instrucoes()).encode('utf-8')).hexdigest()


def exportar_sql(caminho):
    """Escreve o SQL para revisao humana (sem executar)."""
    cab = (f'-- Migracao {MIGRACAO_ID} (checksum {checksum()[:16]}...)\n'
           f'-- Aditiva e idempotente. NAO aplicada em producao. Revisar antes de executar.\n\n')
    open(caminho, 'w', encoding='utf-8').write(cab + ';\n\n'.join(instrucoes()) + ';\n')
    return caminho


def registo_existe(executor, dialeto):
    """Consulta ao CATALOGO: nunca executa um SELECT que possa falhar (um erro faz o db reconectar-se)."""
    if dialeto == 'sqlite':
        sql, params = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (REGISTO,)
    else:
        sql, params = "SELECT table_name FROM information_schema.tables WHERE table_name = %s", (REGISTO,)
    try:
        return bool(executor(sql, params, 'all'))
    except Exception:  # noqa: BLE001
        return False


def estado(executor, dialeto):
    """'nao_aplicada' | 'aplicada' | ('alterada', checksum_registado). Nao escreve nada."""
    if not registo_existe(executor, dialeto):
        return 'nao_aplicada'
    try:
        row = executor(sql_para(dialeto, f'SELECT checksum FROM {REGISTO} WHERE id = ?'), (MIGRACAO_ID,), 'one')
    except Exception:
        return 'nao_aplicada'
    if row is None:
        return 'nao_aplicada'
    return 'aplicada' if row[0] == checksum() else ('alterada', row[0])


def aplicar(executor, dialeto, agora):
    """Aplica a migracao se ainda nao estiver aplicada. Devolve 'aplicada' | 'ja_aplicada'.
    Levanta MigracaoAlterada se o registo tiver outro checksum (nada e alterado nesse caso)."""
    executor(DDL_REGISTO, (), None)
    e = estado(executor, dialeto)
    if e == 'aplicada':
        return 'ja_aplicada'
    if isinstance(e, tuple):
        raise MigracaoAlterada(f'{MIGRACAO_ID} ja aplicada com checksum {e[1][:16]}... diferente do atual')
    for sql in instrucoes()[1:]:
        try:
            executor(sql, (), None)
        except Exception:
            # corrida com outro processo: se a tabela ja existe, a instrucao idempotente basta
            if not sombra.LojaSombra(executor, dialeto).tabela_existe():
                raise
    executor(sql_para(dialeto, f'INSERT INTO {REGISTO} (id, checksum, aplicada_em) VALUES (?, ?, ?) '
                               'ON CONFLICT (id) DO NOTHING'), (MIGRACAO_ID, checksum(), agora), None)
    return 'aplicada'
