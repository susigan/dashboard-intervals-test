"""Cliente da API Intervals.icu + normalizacao de dados."""

from datetime import datetime, timedelta
import requests

from config import (API_KEY, ATHLETE_ID, BASE, ANOS_HISTORICO, CACHE_TTL,
                    TYPE_MAP, NIRS_TYPES)

AUTH = ("API_KEY", API_KEY)

_cache = {'activities': None, 'time': None, 'oldest': None, 'fonte': None}

try:
    import db
except Exception as _e:      # a app funciona sem persistencia
    print(f"DB indisponivel: {_e}")
    db = None


def icu_get(path, params=None, timeout=60):
    """GET generico. Devolve (data, erro)."""
    try:
        r = requests.get(f"{BASE}{path}", auth=AUTH, params=params or {}, timeout=timeout)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:200]}"
        return r.json(), None
    except Exception as e:
        return None, str(e)


def icu_get_many(pedidos, max_workers=6):
    """Varios GET em paralelo. pedidos = {nome: (path, params)}.

    A pagina de detalhe precisa de 6 endpoints; em serie sao 6 idas e voltas
    a Intervals.icu e o browser desiste antes do fim.
    """
    from concurrent.futures import ThreadPoolExecutor
    out = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {nome: ex.submit(icu_get, path, params)
                for nome, (path, params) in pedidos.items()}
        for nome, f in futs.items():
            try:
                out[nome] = f.result(timeout=45)
            except Exception as e:
                out[nome] = (None, str(e))
    return out


_athlete = {'id': None}


def athlete_id_real():
    """Id numerico do atleta.

    ATHLETE_ID pode ser "0" ("eu proprio"), mas nem todos os endpoints
    resolvem esse atalho: alguns tentam mesmo o atleta 0 e devolvem 403.
    Resolvemos uma vez e reutilizamos.
    """
    if _athlete['id']:
        return _athlete['id']

    if ATHLETE_ID and ATHLETE_ID not in ('0', 'i0', ''):
        _athlete['id'] = ATHLETE_ID
        return _athlete['id']

    perfil, err = icu_get(f"/athlete/{ATHLETE_ID}")
    if not err and isinstance(perfil, dict) and perfil.get('id'):
        _athlete['id'] = str(perfil['id'])
        print(f"Athlete id resolvido: {_athlete['id']}")
        return _athlete['id']

    if db is not None and db.ENABLED:
        linha = db._exec("""SELECT athlete_id FROM activities
                            WHERE athlete_id IS NOT NULL LIMIT 1""", fetch='one')
        if linha and linha[0]:
            _athlete['id'] = str(linha[0])
            print(f"Athlete id vindo da base: {_athlete['id']}")
            return _athlete['id']

    return ATHLETE_ID


def _data_oldest():
    return (datetime.now() - timedelta(days=int(365.25 * ANOS_HISTORICO))).strftime("%Y-%m-%d")


def fetch_da_api(oldest=None):
    """Vai a API buscar atividades. Devolve (lista, erro)."""
    oldest = oldest or _data_oldest()
    data, err = icu_get(f"/athlete/{ATHLETE_ID}/activities", {"oldest": oldest})
    if err:
        return None, err
    return (data if isinstance(data, list) else data.get("data", [])), None


def invalidar_cache():
    """Forca o proximo fetch a reler da fonte."""
    _cache.update({'activities': None, 'time': None})


def fetch_activities(force=False):
    """Actividades: cache em memoria -> base de dados -> API.

    A base de dados e transparente. Se nao existir, o comportamento e
    exactamente o mesmo de antes: vai a API e guarda em memoria.
    """
    now = datetime.now()
    if not force and _cache['activities'] and _cache['time']:
        if (now - _cache['time']).total_seconds() < CACHE_TTL:
            return _cache['activities']

    if db is not None and db.ENABLED:
        acts = db.load_activities()
        if acts:
            _cache.update({'activities': acts, 'time': now,
                           'oldest': None, 'fonte': 'db'})
            return acts

    oldest = _data_oldest()
    acts, err = fetch_da_api(oldest)
    if err:
        print(f"Fetch error: {err}")
        return _cache['activities']

    _cache.update({'activities': acts, 'time': now,
                   'oldest': oldest, 'fonte': 'api'})
    print(f"Fetched {len(acts)} actividades desde {oldest}")
    return acts


def cache_info():
    info = {
        'cached': _cache['activities'] is not None,
        'count': len(_cache['activities'] or []),
        'fonte': _cache['fonte'],
        'fetched_at': _cache['time'].isoformat() if _cache['time'] else None,
        'oldest': _cache['oldest'],
        'anos_historico': ANOS_HISTORICO,
        'ttl_segundos': CACHE_TTL,
    }
    info['db_enabled'] = bool(db is not None and db.ENABLED)
    return info


# ── Normalizacao ──────────────────────────────────────────────────────────

def norm_tipo(t):
    """AlpineSki/VirtualSki -> Ski, VirtualRide/Ride -> Bike, etc."""
    if not t:
        return 'Other'
    return TYPE_MAP.get(t, TYPE_MAP.get(str(t).strip(), 'Other'))


def num(v, default=0.0):
    return float(v) if isinstance(v, (int, float)) else default


def kj_da_atividade(a):
    """Trabalho total em kJ.

    icu_joules E o integral do stream de potencia (integral watts.dt) que a
    Intervals.icu calcula no upload. Verificado: AvgPower 149.55 W x 4500 s
    = 672 975 J vs icu_joules 672 991 J.

    Por isso e a fonte primaria: um campo ja calculado, sem precisar de um
    request de streams por atividade. Z1KJ+Z2KJ+Z3KJ so entra como fallback
    para sessoes antigas sem icu_joules.
    """
    j = a.get('icu_joules')
    if isinstance(j, (int, float)) and j > 0:
        return float(j) / 1000.0
    z = [a.get('Z1KJ'), a.get('Z2KJ'), a.get('Z3KJ')]
    if any(isinstance(v, (int, float)) for v in z):
        return float(sum(v for v in z if isinstance(v, (int, float))))
    return 0.0


def kj_do_stream(watts, dt=1.0, n_pontos=None):
    """kJ integrando o stream de potencia.

    Usa a media x numero de pontos ORIGINAIS, nao a soma da serie: os streams
    sao reduzidos a 1500 pontos para o grafico, e somar a serie reduzida daria
    o integral a dividir pelo factor de reducao. A media sobrevive a reducao
    (cada ponto e a media do seu bucket), o numero de pontos nao.

    So e chamado na pagina de detalhe, onde os streams ja estao carregados.
    """
    if not watts:
        return None
    vals = [v for v in watts if isinstance(v, (int, float))]
    if not vals:
        return None
    n = n_pontos if n_pontos else len(vals)
    media = sum(vals) / len(vals)
    return round(media * n * dt / 1000.0, 2)


def classificar_rpe(v):
    """Leve 1-4.9 | Moderado 5-6.9 | Pesado 7-10 (helpers.py:156 do dashboard)."""
    if not isinstance(v, (int, float)):
        return None
    v = float(v)
    if 1 <= v <= 4.9:
        return 'Leve'
    if 5 <= v <= 6.9:
        return 'Moderado'
    if 7 <= v <= 10:
        return 'Pesado'
    return None


def downsample(arr, target=1500):
    if not arr:
        return []
    n = len(arr)
    if n <= target:
        return arr
    step = n / target
    out = []
    for i in range(target):
        lo = int(i * step)
        hi = max(lo + 1, int((i + 1) * step))
        chunk = [v for v in arr[lo:hi] if isinstance(v, (int, float))]
        out.append(round(sum(chunk) / len(chunk), 2) if chunk else None)
    return out


def parse_streams(sdata):
    """Normaliza a resposta de /streams.

    Trata dois casos que a API traz: streams com 'name' (posicao do sensor
    Moxy) e varios streams com o mesmo 'type' (2 sensores -> smo2, smo2_2).
    Devolve (streams_plotaveis, metadados, watts_raw).
    """
    streams, meta, watts_raw = {}, [], None
    if not isinstance(sdata, list):
        return streams, meta, watts_raw

    for s in sdata:
        t = s.get('type')
        if not t or t == 'time' or s.get('allNull'):
            continue
        d = s.get('data')
        has_values = (isinstance(d, list) and d
                      and not s.get('valueTypeIsArray')
                      and any(isinstance(v, (int, float)) for v in d))
        key, n = t, 2
        while key in streams or any(m['key'] == key for m in meta):
            key = f"{t}_{n}"
            n += 1
        if t == 'watts' and has_values and watts_raw is None:
            watts_raw = d
        meta.append({
            'key': key, 'type': t, 'label': s.get('name') or t,
            'sensor_name': s.get('name'), 'custom': bool(s.get('custom')),
            'points': len(d) if isinstance(d, list) else None,
            'plotted': bool(has_values),
            'nirs': t in NIRS_TYPES,
        })
        if has_values:
            streams[key] = downsample(d)
    return streams, meta, watts_raw
