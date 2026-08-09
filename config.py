"""Constantes partilhadas do dashboard."""

import os

API_KEY = os.getenv("INTERVALS_ICU_API_KEY", "").strip()
ATHLETE_ID = os.getenv("ATHLETE_ID", "0").strip()
BASE = "https://intervals.icu/api/v1"

# Quantos anos de historico puxar. Configuravel no Railway via ANOS_HISTORICO.
ANOS_HISTORICO = float(os.getenv("ANOS_HISTORICO", "5"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "900"))  # 15 min

# ── Normalizacao de modalidades (igual a config.py do repo dashboard) ──
TYPE_MAP = {
    'VirtualSki': 'Ski', 'AlpineSki': 'Ski', 'Ski': 'Ski', 'NordicSki': 'Ski',
    'BackcountrySki': 'Ski', 'RollerSki': 'Ski', 'Snowboard': 'Ski',
    'VirtualRow': 'Row', 'Rowing': 'Row', 'Row': 'Row', 'Kayaking': 'Row',
    'Canoeing': 'Row', 'StandUpPaddling': 'Row',
    'VirtualRide': 'Bike', 'Cycling': 'Bike', 'Ride': 'Bike', 'Bike': 'Bike',
    'MountainBike': 'Bike', 'MountainBikeRide': 'Bike', 'GravelRide': 'Bike',
    'EBikeRide': 'Bike', 'Handcycle': 'Bike', 'Velomobile': 'Bike',
    'VirtualRun': 'Run', 'Running': 'Run', 'Run': 'Run', 'TrailRun': 'Run',
    'Treadmill': 'Run', 'Walk': 'Run', 'Hike': 'Run',
    'WeightTraining': 'WeightTraining', 'Workout': 'WeightTraining',
    'Crossfit': 'WeightTraining',
}
CICLICOS = ['Bike', 'Row', 'Run', 'Ski']
VALID_TYPES = CICLICOS + ['WeightTraining']

CORES_MOD = {'Bike': '#E74C3C', 'Row': '#3498DB', 'Run': '#2ECC71',
             'Ski': '#9B59B6', 'WeightTraining': '#F39C12', 'Other': '#7F8C8D'}

NIRS_TYPES = ('smo2', 'thb', 'O2Hb', 'HHb', 'DiffHb')

# Campos do OpenAPI spec v1.0.0. O que nao esta aqui = custom field do atleta.
STD_FIELDS = {
 'analysis_issues','analyzed','athlete_max_hr','attachments','average_altitude','average_cadence',
 'average_clouds','average_feels_like','average_heartrate','average_impact_loading_rate','average_speed',
 'average_stance_time','average_stance_time_balance','average_stance_time_percent','average_step_length',
 'average_stride','average_temp','average_vertical_oscillation','average_vertical_ratio',
 'average_vertical_speed','average_weather_temp','average_wind_gust','average_wind_speed',
 'average_leg_spring_stiffness','avg_lr_balance','calories','carbs_ingested','carbs_used','coach_tick',
 'coasting_time','commute','compliance','crank_length','created','custom_zones','decoupling','description',
 'device_name','device_watts','distance','elapsed_time','external_id','feel','file_sport_index','file_type',
 'gap','gap_model','gap_zone_times','gear','group','has_heartrate','has_segments','has_weather',
 'headwind_percent','hr_load','hr_load_type','icu_achievements','icu_athlete_id','icu_atl',
 'icu_average_watts','icu_cadence_z2','icu_chat_id','icu_color','icu_cooldown_time','icu_ctl',
 'icu_distance','icu_efficiency_factor','icu_ftp','icu_hr_zone_times','icu_hr_zones','icu_hrr',
 'icu_ignore_hr','icu_ignore_power','icu_ignore_time','icu_intensity','icu_intervals','icu_groups',
 'icu_intervals_edited','icu_joules','icu_joules_above_ftp','icu_lap_count','icu_max_wbal_depletion',
 'icu_median_time_delta','icu_pm_cp','icu_pm_ftp','icu_pm_ftp_secs','icu_pm_ftp_watts','icu_pm_p_max',
 'icu_pm_w_prime','icu_power_hr','icu_power_hr_z2','icu_power_hr_z2_mins','icu_power_spike_threshold',
 'icu_power_zones','icu_recording_time','icu_resting_hr','icu_rolling_cp','icu_rolling_ftp',
 'icu_rolling_ftp_delta','icu_rolling_p_max','icu_rolling_w_prime','icu_rpe','icu_sweet_spot_max',
 'icu_sweet_spot_min','icu_sync_date','icu_sync_error','icu_training_load','icu_training_load_data',
 'icu_variability_index','icu_w_prime','icu_warmup_time','icu_weight','icu_weighted_avg_watts',
 'icu_zone_times','id','ignore_pace','ignore_parts','ignore_velocity','interval_summary','kg_lifted',
 'lengths','lock_intervals','lthr','max_altitude','max_feels_like','max_heartrate','max_rain','max_snow',
 'max_speed','max_temp','max_weather_temp','min_altitude','min_feels_like','min_temp','min_weather_temp',
 'moving_time','name','oauth_client_id','oauth_client_name','p30s_exponent','p_max','pace','pace_load',
 'pace_load_type','pace_zone_times','pace_zones','paired_event_id','perceived_exertion',
 'polarization_index','pool_length','power_field','power_field_names','power_load','power_meter',
 'power_meter_battery','power_meter_serial','prevailing_wind_deg','race','recording_stops','route_id',
 'session_rpe','skyline_chart_bytes','source','ss_cp','ss_p_max','ss_w_prime','start_date',
 'start_date_local','strain_score','strava_id','stream_types','sub_type','tags','tailwind_percent',
 'threshold_pace','timezone','tiz_order','total_elevation_gain','total_elevation_loss','trainer','trimp',
 'type','use_elevation_correction','use_gap_zone_times','workout_shift_secs',
}


# ── Seasons ───────────────────────────────────────────────────────────────
# As seasons vivem no calendario da Intervals.icu como eventos de categoria
# SEASON_START (ver api_client.seasons_do_atleta). Quando existem, sao essas
# que mandam. Este mes so serve de recurso se o calendario nao tiver nenhuma.
SEASON_INICIO_MES = int(os.getenv("SEASON_INICIO_MES", "1"))


def season_por_mes(d):
    """Recurso: etiqueta da season a partir de SEASON_INICIO_MES."""
    if not d:
        return None
    s = str(d)
    ano, mes = int(s[:4]), int(s[5:7])
    if SEASON_INICIO_MES == 1:
        return str(ano)
    ini = ano if mes >= SEASON_INICIO_MES else ano - 1
    return f"{ini}/{str(ini + 1)[-2:]}"


def season_de(d, marcos=None):
    """Data -> etiqueta da season.

    marcos: lista de (data_inicio, nome) vinda dos eventos SEASON_START,
    ordenada. Se vier vazia, cai no calculo por mes.
    """
    if not d:
        return None
    s = str(d)[:10]
    if not marcos:
        return season_por_mes(s)
    escolhido = None
    for inicio, nome in marcos:
        if s >= inicio:
            escolhido = nome
        else:
            break
    # antes da primeira season definida
    return escolhido or season_por_mes(s)


# ── Janela das analises que dependem de HRV ───────────────────────────────
# O HRV e sensivel a mudancas de dispositivo, de protocolo de medicao e de
# habito. Dados de ha 5 anos podem nao ser comparaveis com os de agora, e
# misturar tudo dilui relacoes que existem no periodo recente.
# As series de carga, CP e curvas de potencia continuam a usar o historico
# completo — essas nao tem o mesmo problema de comparabilidade.
ANOS_HRV = float(os.getenv("ANOS_HRV", "3"))


def limite_hrv(hoje=None):
    """Data a partir da qual as analises com HRV correm."""
    from datetime import datetime, timedelta
    hoje = hoje or datetime.now()
    return (hoje - timedelta(days=int(365.25 * ANOS_HRV))).strftime('%Y-%m-%d')
