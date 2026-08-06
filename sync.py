"""Sincronizacao Intervals.icu -> Postgres."""

import time
from datetime import datetime, timedelta

import db
from config import ATHLETE_ID, ANOS_HISTORICO
from api_client import icu_get, norm_tipo, num, kj_da_atividade, parse_streams


def _ts(v):
    """ISO-8601 da API -> datetime, tolerante a sufixos."""
    if not v or not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def to_row(a):
    """Actividade da API -> linha da tabela. O JSON completo vai para 'raw',
    por isso nada se perde: as colunas sao so para indexar e agregar."""
    d = (a.get('start_date_local') or '')[:10]
    if len(d) != 10:
        return None
    return {
        'id': a.get('id'),
        'athlete_id': a.get('icu_athlete_id'),
        'date': d,
        'start_local': _ts(a.get('start_date_local')),
        'type_raw': a.get('type'),
        'type': norm_tipo(a.get('type')),
        'name': a.get('name'),
        'elapsed_time': int(num(a.get('elapsed_time'))) or None,
        'moving_time': int(num(a.get('moving_time'))) or None,
        'distance_m': num(a.get('icu_distance')) or num(a.get('distance')),
        'kj': kj_da_atividade(a),
        'kj_acima_ftp': num(a.get('icu_joules_above_ftp')) / 1000.0,
        'z1_kj': num(a.get('Z1KJ')), 'z2_kj': num(a.get('Z2KJ')), 'z3_kj': num(a.get('Z3KJ')),
        'z1_sec': num(a.get('Z1sec')), 'z2_sec': num(a.get('Z2sec')), 'z3_sec': num(a.get('Z3sec')),
        'training_load': num(a.get('icu_training_load')),
        'rpe': num(a.get('icu_rpe')) or None,
        'xss': num(a.get('SS')),
        'aerobic': num(a.get('Aerobic')),
        'glycolytic': num(a.get('Glycolytic')),
        'sprint': num(a.get('Pmax')),
        'epoc': num(a.get('EPOC')),
        'elevation': num(a.get('total_elevation_gain')),
        'avg_hr': num(a.get('average_heartrate')),
        'max_hr': num(a.get('max_heartrate')),
        'avg_watts': num(a.get('icu_weighted_avg_watts')) or num(a.get('icu_average_watts')),
        'ftp': num(a.get('icu_pm_ftp')) or num(a.get('icu_ftp')),
        'source': a.get('source'),
        'icu_sync_date': _ts(a.get('icu_sync_date')),
        'analyzed': _ts(a.get('analyzed')),
        'raw': a,
    }


def sync_activities(modo='incremental', dias_recuo=21):
    """
    full        — puxa ANOS_HISTORICO anos e reescreve tudo
    incremental — puxa desde (data mais recente na BD - dias_recuo)

    O recuo existe porque a Intervals.icu recalcula actividades antigas
    quando mudas FTP ou zonas: icu_pm_ftp, Z1/Z2/Z3 KJ e icu_training_load
    sao derivados e mudam retroactivamente. Sem recuo, ficarias com valores
    obsoletos na base.
    """
    if not db.ENABLED:
        return {'ok': False, 'erro': 'DATABASE_URL nao configurada'}

    t0 = time.time()
    hoje = datetime.now()

    if modo == 'full':
        oldest = (hoje - timedelta(days=int(365.25 * ANOS_HISTORICO))).date()
    else:
        ult = db.ultima_data()
        oldest = ((ult - timedelta(days=dias_recuo)) if ult
                  else (hoje - timedelta(days=int(365.25 * ANOS_HISTORICO))).date())

    data, err = icu_get(f"/athlete/{ATHLETE_ID}/activities",
                        {"oldest": oldest.strftime("%Y-%m-%d")}, timeout=120)
    if err:
        db.log_sync(modo, oldest, 0, 0, 0, time.time() - t0, err)
        return {'ok': False, 'erro': err}

    acts = data if isinstance(data, list) else data.get("data", [])
    rows = [r for r in (to_row(a) for a in acts) if r]
    ins, upd = db.upsert_activities(rows)
    secs = round(time.time() - t0, 2)
    db.log_sync(modo, oldest, len(acts), ins, upd, secs)

    return {'ok': True, 'modo': modo, 'oldest': oldest.isoformat(),
            'recebidas': len(acts), 'inseridas': ins, 'actualizadas': upd,
            'segundos': secs}


def sync_streams(activity_id):
    """Guarda os streams de UMA actividade. Chamado quando abres o detalhe
    (lazy loading): nunca fazemos bulk de milhares de requests."""
    if not db.ENABLED:
        return None
    sdata, err = icu_get(f"/activity/{activity_id}/streams",
                         {"includeDefaults": "true"}, timeout=60)
    if err or not sdata:
        return None
    streams, meta, _ = parse_streams(sdata)
    if streams:
        db.upsert_streams(activity_id, meta, streams)
    return streams, meta
