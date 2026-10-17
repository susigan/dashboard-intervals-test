"""Utilitarios dos testes da Fase 1 (registo de testes). Sem rede, sem base real.

A base e um ficheiro SQLite temporario: db._conn/DRIVER/ENABLED/SQLITE_PATH sao
substituidos e repostos no fim, por isso nunca se toca em /tmp/intervals.db nem
numa base de producao.
"""
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, timedelta
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MODS = ('Bike', 'Row', 'Ski', 'Run')


def curvas(tipo, n, ini=date(2026, 1, 5), passo=7, base=300.0):
    """n curvas semanais com 60/300/1200 s; a potencia sobe devagar, por isso
    os esforcos mais recentes ficam perto do melhor recente (candidatos)."""
    out = []
    for i in range(n):
        out.append({'activity_id': f'{tipo}{i}', 'type': tipo,
                    'date': (ini + timedelta(days=passo * i)).isoformat(),
                    'weight': 70.0, 'secs': [60, 300, 1200],
                    'watts': [base * 1.8 + i, base * 1.3 + i, base + i]})
    return out


def atividades(**estados):
    """{id: True|False|None|'ausente'} -> lista de atividades da API.
    'ausente' = atividade sem o campo icu_ignore_power. Ids nao citados nao existem."""
    out = []
    for aid, v in estados.items():
        a = {'id': aid, 'start_date_local': '2026-03-01T07:30:00'}
        if v != 'ausente':
            a['icu_ignore_power'] = v
        out.append(a)
    return out


def todas(curvs, valor=False):
    return atividades(**{c['activity_id']: valor for c in curvs})


@contextmanager
def base_temporaria(db, caminho=None):
    """Aponta db para um SQLite novo (ficheiro temporario) com o esquema criado."""
    dono = None
    if caminho is None:
        dono = tempfile.TemporaryDirectory()
        caminho = os.path.join(dono.name, 'teste.db')
    antes = (db._conn, db.DRIVER, db.ENABLED, db.SQLITE_PATH, db.DATABASE_URL)
    db.DATABASE_URL = ''
    db.SQLITE_PATH = caminho
    db._conn = None
    db._connect()
    db.init_schema()
    try:
        yield caminho
    finally:
        try:
            if db._conn is not None:
                db._conn.close()
        except Exception:
            pass
        db._conn, db.DRIVER, db.ENABLED, db.SQLITE_PATH, db.DATABASE_URL = antes
        if dono is not None:
            dono.cleanup()


def contar(db, tabela='modelo_testes_revisoes'):
    return db._exec(f'SELECT COUNT(*) FROM {tabela}', fetch='one')[0]


def importar_app():
    with mock.patch.dict(os.environ, {'INTERVALS_ICU_API_KEY': 'valor-ficticio-de-teste'}):
        import app as appmod
    return appmod
