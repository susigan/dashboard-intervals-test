"""Cliente da API Intervals.icu + normalizacao de dados."""

from datetime import datetime, timedelta
import requests

from config import (API_KEY, ATHLETE_ID, BASE, ANOS_HISTORICO, CACHE_TTL,
                    TYPE_MAP, NIRS_TYPES)

AUTH = ("API_KEY", API_KEY)

_cache = {'activities': None, 'time': None, 'oldest': None}


def icu_get(path, params=None, timeout=60):
    """GET generico. Devolve (data, erro)."""
    try:
        r = requests.get(f"{BASE}{path}", auth=AUTH, params=params or {}, timeout=timeout)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:200]}"
        return r.json(), None
    except Exception as e:
        return None, str(e)


def fetch_activities(force=False):
    """Lista de atividades dos ultimos ANOS_HISTORICO anos, com cache."""
    now = datetime.now()
    if not force and _cache['activities'] and _cache['time']:
        if (now - _cache['time']).total_seconds() < CACHE_TTL:
            return _cache['activities']

    oldest = (now - timedelta(days=int(365.25 * ANOS_HISTORICO))).strftime("%Y-%m-%d")
    data, err = icu_get(f"/athlete/{ATHLETE_ID}/activities", {"oldest": oldest})
    if err:
        print(f"Fetch error: {err}")
        return _cache['activities']  # devolve cache antiga se existir

    acts = data if isinstance(data, list) else data.get("data", [])
    _cache.update({'activities': acts, 'time': now, 'oldest': oldest})
    print(f"Fetched {len(acts)} atividades desde {oldest}")
    return acts


def cache_info():
    return {
        'cached': _cache['activities'] is not None,
        'count': len(_cache['activities'] or []),
        'fetched_at': _cache['time'].isoformat() if _cache['time'] else None,
        'oldest': _cache['oldest'],
        'anos_historico': ANOS_HISTORICO,
        'ttl_segundos': CACHE_TTL,
    }


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


def kj_do_stream(watts, dt=1.0):
    """kJ integrando o stream de potencia. So usado na pagina de detalhe,
    onde os streams ja foram carregados, para validar contra icu_joules."""
    if not watts:
        return None
    total = sum(v for v in watts if isinstance(v, (int, float)))
    return round(total * dt / 1000.0, 2)


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
