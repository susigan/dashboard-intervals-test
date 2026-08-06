"""Persistencia — Postgres (Railway) com fallback SQLite.

A app funciona sem base de dados: se nao houver nenhuma, api_client vai
directo a API como antes. A BD e uma cache persistente, nao um requisito.

Tabelas
  activities  1 linha por sessao; colunas indexadas + JSON completo em 'raw'
  streams     1 linha por (actividade, stream); dados comprimidos com zlib
  sync_log    historico de sincronizacoes
"""

import os
import json
import zlib
from datetime import datetime, date

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SQLITE_PATH = os.getenv("SQLITE_PATH", "/tmp/intervals.db").strip()

DRIVER = None      # 'postgres' | 'sqlite' | None
ENABLED = False
_conn = None

# Colunas da tabela activities pela ordem do INSERT
COLS = ['id', 'athlete_id', 'date', 'start_local', 'type_raw', 'type', 'name',
        'elapsed_time', 'moving_time', 'distance_m', 'kj', 'kj_acima_ftp',
        'z1_kj', 'z2_kj', 'z3_kj', 'z1_sec', 'z2_sec', 'z3_sec',
        'training_load', 'rpe', 'xss', 'aerobic', 'glycolytic', 'sprint',
        'epoc', 'elevation', 'avg_hr', 'max_hr', 'avg_watts', 'ftp',
        'source', 'icu_sync_date', 'analyzed', 'raw']


def _connect():
    global _conn, DRIVER, ENABLED
    if _conn is not None:
        return _conn

    if DATABASE_URL:
        try:
            import psycopg
            url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            _conn = psycopg.connect(url, autocommit=True)
            DRIVER, ENABLED = 'postgres', True
            print("DB: Postgres ligado")
            return _conn
        except Exception as e:
            print(f"DB: Postgres indisponivel ({e})")

    try:
        import sqlite3
        _conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        DRIVER, ENABLED = 'sqlite', True
        print(f"DB: SQLite em {SQLITE_PATH}")
        return _conn
    except Exception as e:
        print(f"DB: sem persistencia ({e}) — a usar a API directamente")
        DRIVER, ENABLED = None, False
        return None


_connect()   # determina DRIVER/ENABLED no arranque


def _q(sql):
    """? -> %s quando o driver e Postgres."""
    return sql.replace('?', '%s') if DRIVER == 'postgres' else sql


def _exec(sql, params=None, fetch=None, many=None):
    conn = _connect()
    if conn is None:
        return None
    cur = conn.cursor()
    try:
        if many is not None:
            cur.executemany(_q(sql), many)
        else:
            cur.execute(_q(sql), params or ())
        out = None
        if fetch == 'one':
            out = cur.fetchone()
        elif fetch == 'all':
            out = cur.fetchall()
        if DRIVER == 'sqlite':
            conn.commit()
        return out
    finally:
        cur.close()


def init_schema():
    if _connect() is None:
        return False
    pg = DRIVER == 'postgres'
    blob = 'BYTEA' if pg else 'BLOB'
    ts = 'TIMESTAMP' if pg else 'TEXT'
    jsn = 'JSONB' if pg else 'TEXT'
    serial = 'SERIAL PRIMARY KEY' if pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'

    _exec(f"""CREATE TABLE IF NOT EXISTS activities (
        id             TEXT PRIMARY KEY,
        athlete_id     TEXT,
        date           DATE,
        start_local    {ts},
        type_raw       TEXT,
        type           TEXT,
        name           TEXT,
        elapsed_time   INTEGER,
        moving_time    INTEGER,
        distance_m     DOUBLE PRECISION,
        kj             DOUBLE PRECISION,
        kj_acima_ftp   DOUBLE PRECISION,
        z1_kj          DOUBLE PRECISION,
        z2_kj          DOUBLE PRECISION,
        z3_kj          DOUBLE PRECISION,
        z1_sec         DOUBLE PRECISION,
        z2_sec         DOUBLE PRECISION,
        z3_sec         DOUBLE PRECISION,
        training_load  DOUBLE PRECISION,
        rpe            DOUBLE PRECISION,
        xss            DOUBLE PRECISION,
        aerobic        DOUBLE PRECISION,
        glycolytic     DOUBLE PRECISION,
        sprint         DOUBLE PRECISION,
        epoc           DOUBLE PRECISION,
        elevation      DOUBLE PRECISION,
        avg_hr         DOUBLE PRECISION,
        max_hr         DOUBLE PRECISION,
        avg_watts      DOUBLE PRECISION,
        ftp            DOUBLE PRECISION,
        source         TEXT,
        icu_sync_date  {ts},
        analyzed       {ts},
        raw            {jsn},
        updated_at     {ts}
    )""")
    _exec("CREATE INDEX IF NOT EXISTS ix_act_date ON activities(date)")
    _exec("CREATE INDEX IF NOT EXISTS ix_act_type ON activities(type)")
    _exec("CREATE INDEX IF NOT EXISTS ix_act_type_date ON activities(type, date)")

    _exec(f"""CREATE TABLE IF NOT EXISTS streams (
        activity_id  TEXT,
        skey         TEXT,
        stype        TEXT,
        sensor_name  TEXT,
        is_custom    BOOLEAN,
        points       INTEGER,
        data         {blob},
        updated_at   {ts},
        PRIMARY KEY (activity_id, skey)
    )""")
    _exec("CREATE INDEX IF NOT EXISTS ix_str_type ON streams(stype)")

    _exec(f"""CREATE TABLE IF NOT EXISTS sync_log (
        id            {serial},
        modo          TEXT,
        oldest        DATE,
        recebidas     INTEGER,
        inseridas     INTEGER,
        actualizadas  INTEGER,
        segundos      DOUBLE PRECISION,
        erro          TEXT,
        criado_em     {ts}
    )""")
    return True


def _now():
    return datetime.utcnow() if DRIVER == 'postgres' else datetime.utcnow().isoformat()


def _dt(v):
    """datetime -> valor aceite pelo driver."""
    if v is None:
        return None
    if DRIVER == 'postgres':
        return v
    return v.isoformat() if hasattr(v, 'isoformat') else str(v)


# ── actividades ───────────────────────────────────────────────────────────

def upsert_activities(rows):
    """Grava/actualiza. Devolve (inseridas, actualizadas)."""
    if not ENABLED or not rows:
        return 0, 0

    existentes = ids_existentes()
    novos = sum(1 for r in rows if r['id'] not in existentes)

    now = _now()
    params = []
    for r in rows:
        raw = r.get('raw')
        raw_val = json.dumps(raw, ensure_ascii=False) if raw is not None else None
        params.append(tuple(
            [r.get('id'), r.get('athlete_id'), r.get('date'),
             _dt(r.get('start_local')), r.get('type_raw'), r.get('type'), r.get('name'),
             r.get('elapsed_time'), r.get('moving_time'), r.get('distance_m'),
             r.get('kj'), r.get('kj_acima_ftp'),
             r.get('z1_kj'), r.get('z2_kj'), r.get('z3_kj'),
             r.get('z1_sec'), r.get('z2_sec'), r.get('z3_sec'),
             r.get('training_load'), r.get('rpe'), r.get('xss'),
             r.get('aerobic'), r.get('glycolytic'), r.get('sprint'),
             r.get('epoc'), r.get('elevation'), r.get('avg_hr'), r.get('max_hr'),
             r.get('avg_watts'), r.get('ftp'), r.get('source'),
             _dt(r.get('icu_sync_date')), _dt(r.get('analyzed')), raw_val, now]))

    placeholders = ','.join(['?'] * (len(COLS) + 1))
    updates = ','.join(f"{c}=EXCLUDED.{c}" for c in COLS if c != 'id')
    _exec(f"""INSERT INTO activities ({','.join(COLS)}, updated_at)
              VALUES ({placeholders})
              ON CONFLICT (id) DO UPDATE SET {updates}, updated_at=EXCLUDED.updated_at""",
          many=params)

    return novos, len(rows) - novos


def ids_existentes():
    rows = _exec("SELECT id FROM activities", fetch='all') or []
    return {r[0] for r in rows}


def ultima_data():
    """Data mais recente na base, como date. None se vazia."""
    row = _exec("SELECT MAX(date) FROM activities", fetch='one')
    if not row or not row[0]:
        return None
    v = row[0]
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except Exception:
        return None


def load_activities(desde=None):
    """Dicts originais das actividades (coluna raw). None se a BD nao servir."""
    if not ENABLED:
        return None
    if desde:
        rows = _exec("SELECT raw FROM activities WHERE date >= ? ORDER BY date DESC",
                     (desde,), fetch='all')
    else:
        rows = _exec("SELECT raw FROM activities ORDER BY date DESC", fetch='all')
    if not rows:
        return None
    out = []
    for (raw,) in rows:
        if raw is None:
            continue
        try:
            out.append(raw if isinstance(raw, dict) else json.loads(raw))
        except Exception:
            continue
    return out


# ── streams ───────────────────────────────────────────────────────────────

def upsert_streams(activity_id, meta, streams):
    """Guarda os streams comprimidos (zlib nivel 6)."""
    if not ENABLED or not streams:
        return 0
    now = _now()
    by_key = {m['key']: m for m in (meta or [])}
    params = []
    for key, data in streams.items():
        m = by_key.get(key, {})
        blob = zlib.compress(json.dumps(data).encode(), 6)
        params.append((activity_id, key, m.get('type'), m.get('sensor_name'),
                       bool(m.get('custom')), m.get('points'), blob, now))
    _exec("""INSERT INTO streams
             (activity_id, skey, stype, sensor_name, is_custom, points, data, updated_at)
             VALUES (?,?,?,?,?,?,?,?)
             ON CONFLICT (activity_id, skey) DO UPDATE SET
               stype=EXCLUDED.stype, sensor_name=EXCLUDED.sensor_name,
               is_custom=EXCLUDED.is_custom, points=EXCLUDED.points,
               data=EXCLUDED.data, updated_at=EXCLUDED.updated_at""",
          many=params)
    return len(params)


def get_streams(activity_id):
    """(streams, meta) ou (None, None) se ainda nao foram guardados."""
    if not ENABLED:
        return None, None
    rows = _exec("""SELECT skey, stype, sensor_name, is_custom, points, data
                    FROM streams WHERE activity_id = ?""", (activity_id,), fetch='all')
    if not rows:
        return None, None
    streams, meta = {}, []
    for skey, stype, sensor, custom, points, blob in rows:
        try:
            streams[skey] = json.loads(zlib.decompress(bytes(blob)).decode())
        except Exception:
            continue
        meta.append({'key': skey, 'type': stype, 'label': sensor or stype,
                     'sensor_name': sensor, 'custom': bool(custom),
                     'points': points, 'plotted': True,
                     'nirs': stype in ('smo2', 'thb', 'O2Hb', 'HHb', 'DiffHb')})
    return streams, meta


# ── log e estado ──────────────────────────────────────────────────────────

def log_sync(modo, oldest, recebidas, inseridas, actualizadas, segundos, erro=None):
    if not ENABLED:
        return
    _exec("""INSERT INTO sync_log
             (modo, oldest, recebidas, inseridas, actualizadas, segundos, erro, criado_em)
             VALUES (?,?,?,?,?,?,?,?)""",
          (modo, oldest, recebidas, inseridas, actualizadas,
           round(segundos, 2), erro, _now()))


def stats():
    if not ENABLED:
        return {'enabled': False, 'driver': None,
                'nota': 'sem DATABASE_URL — a app usa a API directamente'}
    a = _exec("""SELECT COUNT(*), MIN(date), MAX(date), COUNT(DISTINCT type)
                 FROM activities""", fetch='one') or (0, None, None, 0)
    s = _exec("""SELECT COUNT(DISTINCT activity_id), COUNT(*), COALESCE(SUM(points),0)
                 FROM streams""", fetch='one') or (0, 0, 0)
    por_tipo = _exec("""SELECT type, COUNT(*), ROUND(SUM(kj)::numeric, 0)
                        FROM activities GROUP BY type ORDER BY COUNT(*) DESC"""
                     if DRIVER == 'postgres' else
                     """SELECT type, COUNT(*), ROUND(SUM(kj), 0)
                        FROM activities GROUP BY type ORDER BY COUNT(*) DESC""",
                     fetch='all') or []
    ult = _exec("""SELECT modo, oldest, recebidas, inseridas, actualizadas,
                          segundos, erro, criado_em
                   FROM sync_log ORDER BY id DESC LIMIT 5""", fetch='all') or []
    return {
        'enabled': True, 'driver': DRIVER,
        'actividades': a[0], 'date_min': str(a[1]) if a[1] else None,
        'date_max': str(a[2]) if a[2] else None, 'modalidades': a[3],
        'por_tipo': [{'type': t, 'n': n, 'kj': float(k or 0)} for t, n, k in por_tipo],
        'streams': {'actividades': s[0], 'series': s[1], 'pontos': int(s[2] or 0)},
        'ultimos_syncs': [{
            'modo': r[0], 'oldest': str(r[1]), 'recebidas': r[2],
            'inseridas': r[3], 'actualizadas': r[4], 'segundos': r[5],
            'erro': r[6], 'em': str(r[7])} for r in ult],
    }


# ── agregacao em SQL ──────────────────────────────────────────────────────

def volume_rows(desde=None):
    """So as colunas que o Volume usa — evita carregar 183 campos x N sessoes."""
    if not ENABLED:
        return None
    cols = """id, date, type, type_raw, elapsed_time, moving_time, distance_m,
              kj, kj_acima_ftp, z1_kj, z2_kj, z3_kj, z1_sec, z2_sec, z3_sec,
              training_load, rpe, xss, aerobic, glycolytic, sprint, epoc, elevation"""
    where = "WHERE date >= ?" if desde else ""
    rows = _exec(f"SELECT {cols} FROM activities {where} ORDER BY date",
                 (desde,) if desde else (), fetch='all')
    if rows is None:
        return None
    nomes = [c.strip() for c in cols.replace('\n', ' ').split(',')]
    return [dict(zip(nomes, r)) for r in rows]
