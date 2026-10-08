"""Helpers comuns de segurança para os bancos SQLite sincronizados com o Drive.

Usado por drive_db_perfil.py e drive_db_moxy_vst.py. Regras:

- Nada aqui apaga dados: ficheiros problemáticos são MOVIDOS para um sufixo
  de arquivo (.corrompido-<ts>, .orfao-<ts>), nunca removidos.
- Upload só é permitido se o ficheiro local vier do Drive (origem 'drive'),
  tiver o mesmo modifiedTime que o Drive e não tiver menos linhas que a base.
- Um ficheiro local vazio criado após falha de download (origem 'vazio_local')
  nunca é enviado.
"""
import datetime
import json
import os
import shutil
import sqlite3
import tempfile

_SUFIXOS_WAL = ('-wal', '-shm')


def _ts():
    return datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')


def integridade(caminho):
    """Devolve (estado, detalhe). estado ∈ {'ok', 'corrompido', 'transitorio'}.

    Verifica uma CÓPIA (principal + -wal + -shm). Abrir o original com um
    -wal ao lado de um principal inválido faz o SQLite APAGAR o -wal, o que
    destruiria dados ainda não consolidados. Por isso o original nunca é aberto aqui.

    'corrompido' só para erros de conteúdo (malformed / not a database).
    Erros transitórios (lock, I/O) NÃO são corrupção.
    """
    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp(prefix='integ_')
        alvo = os.path.join(tmpdir, os.path.basename(caminho))
        shutil.copyfile(caminho, alvo)
        for suf in _SUFIXOS_WAL:
            if os.path.exists(caminho + suf):
                shutil.copyfile(caminho + suf, alvo + suf)
        cn = sqlite3.connect(alvo)
        try:
            r = cn.execute("PRAGMA integrity_check").fetchone()
        finally:
            cn.close()
        if r and r[0] == 'ok':
            return 'ok', 'ok'
        return 'corrompido', str(r[0]) if r else 'integrity_check sem resposta'
    except sqlite3.OperationalError as e:
        return 'transitorio', f'OperationalError: {e}'
    except sqlite3.DatabaseError as e:
        msg = str(e).lower()
        if 'malformed' in msg or 'not a database' in msg:
            return 'corrompido', str(e)
        return 'transitorio', f'{type(e).__name__}: {e}'
    except Exception as e:
        return 'transitorio', f'{type(e).__name__}: {e}'
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def consolidar_wal(caminho):
    """Checkpoint TRUNCATE: move o conteúdo do -wal para o ficheiro principal.

    Levanta RuntimeError se o checkpoint não conseguiu consolidar tudo
    (ex.: outra conexão com transação aberta). Quem chama NÃO deve subir.
    """
    if not os.path.exists(caminho):
        raise RuntimeError('ficheiro local inexistente')
    cn = sqlite3.connect(caminho)
    try:
        busy, _log, _ckpt = cn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise RuntimeError('checkpoint WAL bloqueado (transação aberta)')
    finally:
        cn.close()


def arquivar_grupo(caminho, rotulo):
    """Move caminho + -wal + -shm para sufixo .<rotulo>-<ts>. Nunca apaga."""
    ts = _ts()
    movidos = []
    for p in (caminho,) + tuple(caminho + s for s in _SUFIXOS_WAL):
        if os.path.exists(p):
            os.replace(p, f'{p}.{rotulo}-{ts}')
            movidos.append(p)
    return movidos


def arquivar_wal_orfao(caminho):
    """Se o principal não existe mas há -wal/-shm, são órfãos: mover-os.

    Um WAL órfão reaplicado a um principal novo corromperia o banco.
    """
    if os.path.exists(caminho):
        return []
    ts = _ts()
    movidos = []
    for s in _SUFIXOS_WAL:
        p = caminho + s
        if os.path.exists(p):
            os.replace(p, f'{p}.orfao-{ts}')
            movidos.append(p)
    return movidos


def _sidecar(caminho):
    return caminho + '.origem.json'


def ler_origem(caminho):
    try:
        with open(_sidecar(caminho), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def gravar_origem(caminho, dados):
    tmp = _sidecar(caminho) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(dados, f)
    os.replace(tmp, _sidecar(caminho))


def contagens(caminho, consultas):
    """consultas: dict nome -> SQL que devolve um escalar. Tabela ausente = omitida."""
    out = {}
    cn = sqlite3.connect(caminho)
    try:
        for nome, sql in consultas.items():
            try:
                out[nome] = cn.execute(sql).fetchone()[0] or 0
            except sqlite3.OperationalError:
                pass
    finally:
        cn.close()
    return out


def origem_drive(origem):
    """(ok, motivo). Verificação LOCAL, feita antes de qualquer chamada ao Drive."""
    if not origem:
        return False, 'origem do banco local desconhecida — upload recusado'
    if origem.get('origem') != 'drive':
        return False, (f"banco local com origem '{origem.get('origem')}' "
                       f"(não veio do Drive) — upload recusado")
    return True, None


def validar_envio(origem, modified_drive, cont_local):
    """(ok, motivo). Só permite upload de base do Drive, sem regressão."""
    ok_o, motivo_o = origem_drive(origem)
    if not ok_o:
        return False, motivo_o
    if not modified_drive or modified_drive != origem.get('modified_time'):
        return False, ('Drive foi alterado desde o último download/upload '
                       'deste processo — upload recusado para não substituir '
                       'dados de outra origem')
    for nome, n_base in (origem.get('contagens') or {}).items():
        n_loc = cont_local.get(nome)
        if n_loc is None or n_loc < n_base:
            return False, (f"'{nome}' local ({n_loc}) menor que a base do "
                           f"Drive ({n_base}) — upload recusado")
    return True, None
