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

    _exec(f"""CREATE TABLE IF NOT EXISTS power_curves (
        activity_id  TEXT PRIMARY KEY,
        type         TEXT,
        date         DATE,
        weight       DOUBLE PRECISION,
        secs         TEXT,
        watts        TEXT,
        updated_at   {ts}
    )""")
    _exec("CREATE INDEX IF NOT EXISTS ix_pc_type_date ON power_curves(type, date)")

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


# ── curvas de potencia e recordes ──────────────────────────────────────────

# Duracoes canonicas. Incluem as dos custom fields MMP (60s, 180, 300, 720,
# 1200, 3600) mais o intervalo curto que interessa ao W'.
DURACOES = [1, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600,
            720, 900, 1200, 1800, 2400, 3600, 5400]


def diagnostico_curvas(limite=3):
    """Estado real da tabela de curvas: contagens, amostra crua e parsing.

    Serve para perceber porque e que a pagina fica a zero apesar de o sync
    dizer que gravou.
    """
    if not ENABLED:
        return {'enabled': False}
    out = {'driver': DRIVER, 'colunas': colunas_de('power_curves')}

    tot = _exec("SELECT COUNT(*) FROM power_curves", fetch='one')
    out['linhas'] = tot[0] if tot else 0

    por_tipo = _exec("""SELECT type, COUNT(*) FROM power_curves
                        GROUP BY type ORDER BY COUNT(*) DESC""", fetch='all') or []
    out['por_tipo'] = [{'type': t, 'n': n} for t, n in por_tipo]

    nulos = _exec("""SELECT
                       SUM(CASE WHEN secs IS NULL THEN 1 ELSE 0 END),
                       SUM(CASE WHEN watts IS NULL THEN 1 ELSE 0 END),
                       SUM(CASE WHEN date IS NULL THEN 1 ELSE 0 END)
                     FROM power_curves""", fetch='one')
    if nulos:
        out['nulos'] = {'secs': nulos[0], 'watts': nulos[1], 'date': nulos[2]}

    rows = _exec(f"""SELECT activity_id, type, date, weight, secs, watts
                     FROM power_curves LIMIT {int(limite)}""", fetch='all') or []
    amostra = []
    for aid, tp, dt, w, secs, watts in rows:
        item = {'activity_id': aid, 'type': tp, 'date': str(dt),
                'tipo_python_secs': type(secs).__name__,
                'tipo_python_watts': type(watts).__name__,
                'secs_cru': str(secs)[:80], 'watts_cru': str(watts)[:80]}
        try:
            item['secs_parsed'] = _lista(secs)[:5]
            item['watts_parsed'] = _lista(watts)[:5]
            item['parse'] = 'ok'
        except Exception as e:
            item['parse'] = f'{type(e).__name__}: {e}'
        amostra.append(item)
    out['amostra'] = amostra
    out['load_power_curves'] = len(load_power_curves() or [])
    return out


def recriar_power_curves():
    """Apaga e recria a tabela de curvas.

    CREATE TABLE IF NOT EXISTS nao altera uma tabela que ja exista, por isso
    se o esquema mudou e preciso deitar abaixo. Nao se perde nada: as curvas
    vem todas da API em 4 pedidos (/api/sync/curvas).
    """
    if not ENABLED:
        return {'ok': False, 'erro': 'sem base de dados'}
    antes = colunas_de('power_curves')
    _exec("DROP TABLE IF EXISTS power_curves")
    init_schema()
    return {'ok': True, 'colunas_antes': antes,
            'colunas_agora': colunas_de('power_curves'),
            'nota': 'corre /api/sync/curvas para voltar a preencher'}


def colunas_de(tabela):
    """Colunas de uma tabela, para diagnostico."""
    if not ENABLED:
        return []
    if DRIVER == 'postgres':
        rows = _exec("""SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_name = ? ORDER BY ordinal_position""",
                     (tabela,), fetch='all')
    else:
        rows = _exec(f"PRAGMA table_info({tabela})", fetch='all')
        rows = [(r[1], r[2]) for r in (rows or [])]
    return [{'nome': r[0], 'tipo': r[1]} for r in (rows or [])]


def upsert_power_curves(rows):
    """rows: lista de {activity_id, type, date, weight, secs, watts}."""
    if not ENABLED or not rows:
        return 0
    now = _now()
    params = [(r['activity_id'], r['type'], r['date'], r.get('weight'),
               json.dumps(r['secs']), json.dumps(r['watts']), now) for r in rows]
    _exec("""INSERT INTO power_curves
             (activity_id, type, date, weight, secs, watts, updated_at)
             VALUES (?,?,?,?,?,?,?)
             ON CONFLICT (activity_id) DO UPDATE SET
               type=EXCLUDED.type, date=EXCLUDED.date, weight=EXCLUDED.weight,
               secs=EXCLUDED.secs, watts=EXCLUDED.watts,
               updated_at=EXCLUDED.updated_at""", many=params)
    return len(params)


def load_power_curves(tipo=None, desde=None):
    """Curvas ordenadas por data (a ordem importa para calcular progressao)."""
    if not ENABLED:
        return []
    cond, params = [], []
    if tipo:
        cond.append("type = ?")
        params.append(tipo)
    if desde:
        cond.append("date >= ?")
        params.append(desde)
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    rows = _exec(f"""SELECT activity_id, type, date, weight, secs, watts
                     FROM power_curves {where} ORDER BY date, activity_id""",
                 tuple(params), fetch='all') or []
    out, falhas = [], []
    for aid, tp, dt, w, secs, watts in rows:
        try:
            out.append({'activity_id': aid, 'type': tp, 'date': str(dt)[:10],
                        'weight': float(w) if w is not None else None,
                        'secs': _lista(secs), 'watts': _lista(watts)})
        except Exception as e:
            falhas.append(f"{aid}: {type(e).__name__}: {e}")
    if falhas:
        # nao engolir em silencio: sem isto a pagina fica a zero sem explicacao
        print(f"load_power_curves: {len(falhas)} linhas ilegiveis, ex: {falhas[:3]}")
    return out


def _lista(v):
    """secs/watts podem vir como lista (JSONB), string JSON (TEXT) ou memoryview."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, (bytes, bytearray, memoryview)):
        v = bytes(v).decode()
    if isinstance(v, str):
        return json.loads(v)
    return list(v)


def _nomes_actividades(ids):
    if not ids or not ENABLED:
        return {}
    marcas = ','.join(['?'] * len(ids))
    rows = _exec(f"SELECT id, name, type FROM activities WHERE id IN ({marcas})",
                 tuple(ids), fetch='all') or []
    return {r[0]: {'name': r[1], 'type': r[2]} for r in rows}


def curvas_por_periodo(tipo=None, marcos=None, por='season'):
    """Melhor curva de cada periodo — season (do calendario) ou ano civil.

    por='ano'    -> agrupa por ano civil, sempre
    por='season' -> usa os SEASON_START; sem eles cai no ano civil
    """
    from config import season_de, season_por_mes

    curvas = load_power_curves(tipo)
    if not curvas:
        return {'periodos': [], 'por_periodo': {}, 'duracoes': []}

    def etiqueta(d):
        if por == 'ano':
            return str(d)[:4]
        return season_de(d, marcos)

    grupos, duracoes = {}, set()
    for c in curvas:
        s = etiqueta(c['date'])
        if not s:
            continue
        alvo = grupos.setdefault(s, {'melhores': {}, 'n_sessoes': 0,
                                     'de': c['date'], 'ate': c['date']})
        alvo['n_sessoes'] += 1
        alvo['de'] = min(alvo['de'], c['date'])
        alvo['ate'] = max(alvo['ate'], c['date'])
        for secs, w in zip(c['secs'], c['watts']):
            if not isinstance(w, (int, float)) or w <= 0:
                continue
            duracoes.add(secs)
            m = alvo['melhores'].get(secs)
            if m is None or w > m['watts']:
                alvo['melhores'][secs] = {'watts': w, 'date': c['date'],
                                          'activity_id': c['activity_id']}

    ids = {m['activity_id'] for v in grupos.values() for m in v['melhores'].values()}
    nomes = _nomes_actividades(list(ids))
    for v in grupos.values():
        for m in v['melhores'].values():
            m['name'] = (nomes.get(m['activity_id']) or {}).get('name')

    ordem = sorted(grupos, key=lambda s: grupos[s]['de'], reverse=True)
    return {'periodos': ordem, 'por_periodo': grupos,
            'duracoes': sorted(duracoes)}


def curvas_por_season(tipo=None, marcos=None):
    """Melhor curva de cada season.

    Para cada season e cada duracao, o melhor watt de todas as sessoes dessa
    season — e a sessao onde aconteceu. E o que permite sobrepor a epoca
    actual com as anteriores.
    """
    from config import season_de

    curvas = load_power_curves(tipo)
    if not curvas:
        return {'seasons': [], 'por_season': {}, 'duracoes': []}

    por_season, duracoes = {}, set()
    for c in curvas:
        s = season_de(c['date'], marcos)
        if not s:
            continue
        alvo = por_season.setdefault(s, {'melhores': {}, 'n_sessoes': 0,
                                         'de': c['date'], 'ate': c['date']})
        alvo['n_sessoes'] += 1
        alvo['de'] = min(alvo['de'], c['date'])
        alvo['ate'] = max(alvo['ate'], c['date'])
        for secs, w in zip(c['secs'], c['watts']):
            if not isinstance(w, (int, float)) or w <= 0:
                continue
            duracoes.add(secs)
            m = alvo['melhores'].get(secs)
            if m is None or w > m['watts']:
                alvo['melhores'][secs] = {'watts': w, 'date': c['date'],
                                          'activity_id': c['activity_id']}

    ids = {m['activity_id'] for v in por_season.values()
           for m in v['melhores'].values()}
    nomes = _nomes_actividades(list(ids))
    for v in por_season.values():
        for m in v['melhores'].values():
            m['name'] = (nomes.get(m['activity_id']) or {}).get('name')

    # ordenar pela data de inicio de cada season, nao pelo nome
    ordem = sorted(por_season, key=lambda s: por_season[s]['de'], reverse=True)
    return {'seasons': ordem, 'por_season': por_season,
            'duracoes': sorted(duracoes)}


def calcular_recordes(tipo=None, desde=None, ate=None):
    """Recordes por duracao dentro de uma janela.

    Sem 'desde', devolve o melhor de sempre — util para saber do que ja foste
    capaz, mas enganador como retrato da forma actual: um esforco de 2022 fica
    a marcar o recorde para sempre. Por isso a janela existe: com desde/ate
    vemos o melhor DESSE periodo, e comparamos com o de sempre.
    """
    curvas = load_power_curves(tipo, desde)
    if ate:
        curvas = [c for c in curvas if c['date'] < ate]
    if not curvas:
        return {'duracoes': [], 'progressao': {}, 'melhores': {}, 'n_sessoes': 0}

    melhores = {}      # secs -> {watts, date, activity_id, anterior_*}
    progressao = {}    # secs -> [{date, watts, activity_id, delta}]
    prs_por_act = {}   # activity_id -> [secs...]

    for c in curvas:
        for s, w in zip(c['secs'], c['watts']):
            if not isinstance(w, (int, float)) or w <= 0:
                continue
            m = melhores.get(s)
            if m is None or w > m['watts']:
                anterior = dict(m) if m else None
                melhores[s] = {
                    'watts': w, 'date': c['date'], 'activity_id': c['activity_id'],
                    'anterior_watts': anterior['watts'] if anterior else None,
                    'anterior_date': anterior['date'] if anterior else None,
                    'anterior_activity_id': anterior['activity_id'] if anterior else None,
                }
                progressao.setdefault(s, []).append({
                    'date': c['date'], 'watts': w, 'activity_id': c['activity_id'],
                    'delta': round(w - anterior['watts'], 1) if anterior else None,
                })
                prs_por_act.setdefault(c['activity_id'], []).append(s)

    ids = {v['activity_id'] for v in melhores.values()}
    nomes = _nomes_actividades(list(ids))
    for v in melhores.values():
        v['name'] = (nomes.get(v['activity_id']) or {}).get('name')

    out = {
        'duracoes': sorted(melhores),
        'melhores': melhores,
        'progressao': progressao,
        'prs_por_actividade': prs_por_act,
        'n_sessoes': len(curvas),
        'periodo': {'de': curvas[0]['date'], 'ate': curvas[-1]['date']},
        'janela': {'desde': desde, 'ate': ate},
    }

    # Referencia de sempre, para o periodo poder ser lido em contexto:
    # 260 W aos 20min so diz alguma coisa se souberes que o teu melhor e 285.
    if desde or ate:
        todas = load_power_curves(tipo)
        sempre = {}
        for c in todas:
            for s, w in zip(c['secs'], c['watts']):
                if not isinstance(w, (int, float)) or w <= 0:
                    continue
                m = sempre.get(s)
                if m is None or w > m['watts']:
                    sempre[s] = {'watts': w, 'date': c['date'],
                                 'activity_id': c['activity_id']}
        nomes_s = _nomes_actividades([v['activity_id'] for v in sempre.values()])
        for v in sempre.values():
            v['name'] = (nomes_s.get(v['activity_id']) or {}).get('name')
        out['sempre'] = sempre
        out['n_sessoes_sempre'] = len(todas)
    else:
        out['sempre'] = melhores
        out['n_sessoes_sempre'] = len(curvas)

    return out


def prs_da_actividade(activity_id):
    """Que duracoes esta sessao bateu, comparando so com as ANTERIORES.

    Um PR so conta contra o que ja tinha acontecido nessa data — comparar com
    o historico completo diria que quase nada foi recorde.
    """
    if not ENABLED:
        return None
    row = _exec("""SELECT type, date, secs, watts FROM power_curves
                   WHERE activity_id = ?""", (activity_id,), fetch='one')
    if not row:
        return None
    tipo, data, secs, watts = row
    secs = secs if isinstance(secs, list) else json.loads(secs)
    watts = watts if isinstance(watts, list) else json.loads(watts)
    data = str(data)[:10]

    anteriores = load_power_curves(tipo)
    melhor_antes = {}
    for c in anteriores:
        if c['date'] >= data and c['activity_id'] != activity_id:
            continue
        if c['activity_id'] == activity_id:
            continue
        for s, w in zip(c['secs'], c['watts']):
            if isinstance(w, (int, float)) and w > melhor_antes.get(s, 0):
                melhor_antes[s] = w

    out = []
    for s, w in zip(secs, watts):
        if not isinstance(w, (int, float)) or w <= 0:
            continue
        antes = melhor_antes.get(s)
        out.append({
            'secs': s, 'watts': w,
            'melhor_anterior': antes,
            'pr': antes is None or w > antes,
            'delta': round(w - antes, 1) if antes else None,
            'pct_do_melhor': round(w / antes * 100, 1) if antes else None,
        })
    return {'activity_id': activity_id, 'type': tipo, 'date': data, 'duracoes': out}


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
    pc = _exec("SELECT COUNT(*), COUNT(DISTINCT type) FROM power_curves",
               fetch='one') or (0, 0)
    ult = _exec("""SELECT modo, oldest, recebidas, inseridas, actualizadas,
                          segundos, erro, criado_em
                   FROM sync_log ORDER BY id DESC LIMIT 5""", fetch='all') or []
    return {
        'enabled': True, 'driver': DRIVER,
        'actividades': a[0], 'date_min': str(a[1]) if a[1] else None,
        'date_max': str(a[2]) if a[2] else None, 'modalidades': a[3],
        'por_tipo': [{'type': t, 'n': n, 'kj': float(k or 0)} for t, n, k in por_tipo],
        'streams': {'actividades': s[0], 'series': s[1], 'pontos': int(s[2] or 0)},
        'power_curves': {'actividades': pc[0], 'modalidades': pc[1]},
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
