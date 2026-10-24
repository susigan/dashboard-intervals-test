"""Migracao PREPARADA (Etapa 16): modelo_ir_sombra_previsoes e modelo_ir_sombra_resultados.

Mesmo mecanismo das migracoes anteriores: aditiva (so CREATE ... IF NOT EXISTS), com registo e checksum em
schema_migracoes_ir. Nao e aplicada por nenhum caminho automatico. Nunca toca em modelo_ir_ajustes.
"""
import hashlib

import modelo_ir_previsoes as mp
from modelo_ir_confianca_migracao import DDL_REGISTO, REGISTO, registo_existe
from modelo_ir_sombra import sql_para

MIGRACAO_ID = '2026-10-24_ir_sombra_previsoes_001'


class MigracaoAlterada(RuntimeError):
    pass


def instrucoes():
    return [DDL_REGISTO, mp.DDL_PREV, mp.DDL_RES, mp.INDICE_PREV]


def checksum():
    return hashlib.sha256('\n;\n'.join(instrucoes()).encode('utf-8')).hexdigest()


def exportar_sql(caminho):
    cab = (f'-- Migracao {MIGRACAO_ID} (checksum {checksum()[:16]}...)\n'
           f'-- Aditiva e idempotente. NAO aplicada em producao. Revisar antes de executar.\n\n')
    with open(caminho, 'w', encoding='utf-8') as f:
        f.write(cab + ';\n\n'.join(instrucoes()) + ';\n')
    return caminho


def estado(executor, dialeto):
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
    """'aplicada' | 'ja_aplicada'. Checksum diferente -> MigracaoAlterada (nada e alterado)."""
    executor(DDL_REGISTO, (), None)
    e = estado(executor, dialeto)
    if e == 'aplicada':
        return 'ja_aplicada'
    if isinstance(e, tuple):
        raise MigracaoAlterada(f'{MIGRACAO_ID} ja aplicada com checksum {e[1][:16]}... diferente do atual')
    for sql in instrucoes()[1:]:
        executor(sql, (), None)
    executor(sql_para(dialeto, f'INSERT INTO {REGISTO} (id, checksum, aplicada_em) VALUES (?, ?, ?) '
                               'ON CONFLICT (id) DO NOTHING'), (MIGRACAO_ID, checksum(), agora), None)
    return 'aplicada'
