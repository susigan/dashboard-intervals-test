"""
🛠️ HELPERS — ActivityProcessor para API Intervals.icu
======================================================

Processamento seguro de atividades com tratamento de None/null.

Uso:
    from helpers import ActivityProcessor
    processor = ActivityProcessor()
    ftp = processor.get_ftp(activity)
"""

from typing import Optional, Dict, Any


class ActivityProcessor:
    """Processa atividades da API Intervals.icu com tratamento seguro."""
    
    def __init__(self, default_ftp: int = 200):
        self.default_ftp = default_ftp
    
    # ==================== POWER ====================
    
    def get_ftp(self, activity: Dict[str, Any]) -> int:
        """FTP — Morton 3P > rolling > default."""
        ftp = activity.get('icu_pm_ftp')
        if ftp is not None and ftp > 0:
            return int(ftp)
        ftp = activity.get('icu_ftp')
        if ftp is not None and ftp > 0:
            return int(ftp)
        return self.default_ftp
    
    def get_w_prime(self, activity: Dict[str, Any]) -> int:
        """W′ em Joules."""
        w_prime = activity.get('icu_pm_w_prime')
        return int(w_prime) if w_prime is not None else 20000
    
    def get_joules(self, activity: Dict[str, Any]) -> int:
        """Energia total — CONFIÁVEL."""
        joules = activity.get('icu_joules')
        return int(joules) if joules is not None else 0
    
    def get_avg_watts(self, activity: Dict[str, Any]) -> int:
        """Potência média normalizada."""
        watts = activity.get('icu_weighted_avg_watts')
        if watts is not None:
            return int(watts)
        watts = activity.get('icu_average_watts')
        return int(watts) if watts is not None else 0
    
    def get_intensity_factor(self, activity: Dict[str, Any]) -> float:
        """IF (Intensity Factor)."""
        intensity = activity.get('icu_intensity')
        return float(intensity) if intensity is not None else 0.0
    
    def get_training_load(self, activity: Dict[str, Any]) -> int:
        """TL — CONFIÁVEL."""
        tl = activity.get('icu_training_load')
        return int(tl) if tl is not None else 0
    
    # ==================== DURATION ====================
    
    def get_duration_seconds(self, activity: Dict[str, Any]) -> int:
        """Duração em segundos."""
        elapsed = activity.get('elapsed_time')
        if elapsed is not None and elapsed > 0:
            return int(elapsed)
        moving = activity.get('moving_time')
        return int(moving) if moving is not None else 0
    
    def get_duration_minutes(self, activity: Dict[str, Any]) -> float:
        """Duração em minutos."""
        seconds = self.get_duration_seconds(activity)
        return seconds / 60.0 if seconds else 0.0
    
    # ==================== DISTANCE ====================
    
    def get_distance_km(self, activity: Dict[str, Any]) -> float:
        """Distância em km."""
        dist = activity.get('icu_distance')
        if dist is not None and dist > 0:
            return float(dist) / 1000.0 if dist > 1000 else float(dist)
        dist = activity.get('distance')
        if dist is not None and dist > 0:
            if activity.get('trainer') and dist < 50:
                return dist / 1000.0
            return float(dist)
        return 0.0
    
    def get_avg_speed_kmh(self, activity: Dict[str, Any]) -> float:
        """Velocidade média."""
        speed = activity.get('average_speed')
        return float(speed) if speed is not None else 0.0
    
    # ==================== HEART RATE ====================
    
    def get_avg_hr(self, activity: Dict[str, Any]) -> int:
        """HR médio — verifica ignore flag."""
        if activity.get('icu_ignore_hr'):
            return 0
        hr = activity.get('average_heartrate')
        return int(hr) if hr is not None else 0
    
    def get_max_hr(self, activity: Dict[str, Any]) -> int:
        """HR máximo."""
        if activity.get('icu_ignore_hr'):
            return 0
        hr = activity.get('max_heartrate')
        return int(hr) if hr is not None else 0
    
    def has_hr_data(self, activity: Dict[str, Any]) -> bool:
        """Tem dados HR válidos?"""
        if activity.get('icu_ignore_hr'):
            return False
        return activity.get('has_heartrate', False)
    
    # ==================== ELEVATION ====================
    
    def get_elevation_gain_m(self, activity: Dict[str, Any]) -> float:
        """Ganho de elevação em metros."""
        elev = activity.get('total_elevation_gain')
        return float(elev) if elev is not None else 0.0
    
    def get_elevation_loss_m(self, activity: Dict[str, Any]) -> float:
        """Perda de elevação em metros."""
        elev = activity.get('total_elevation_loss')
        return float(elev) if elev is not None else 0.0
    
    # ==================== METADATA ====================
    
    def get_activity_id(self, activity: Dict[str, Any]) -> str:
        """ID da atividade."""
        return str(activity.get('id', ''))
    
    def get_activity_name(self, activity: Dict[str, Any]) -> str:
        """Nome da atividade."""
        return str(activity.get('name', 'Untitled'))
    
    def get_activity_type(self, activity: Dict[str, Any]) -> str:
        """Tipo (Ride, Run, etc.)."""
        return str(activity.get('type', 'Unknown'))
    
    def get_start_date_local(self, activity: Dict[str, Any]) -> str:
        """Data/hora local ISO-8601."""
        return str(activity.get('start_date_local', ''))
    
    def get_source(self, activity: Dict[str, Any]) -> str:
        """Origem (STRAVA, UPLOAD, etc.)."""
        return str(activity.get('source', 'UNKNOWN'))
    
    def is_trainer(self, activity: Dict[str, Any]) -> bool:
        """É atividade em trainer?"""
        return activity.get('trainer', False)
    
    def is_commute(self, activity: Dict[str, Any]) -> bool:
        """É commute?"""
        return activity.get('commute', False)
    
    def is_race(self, activity: Dict[str, Any]) -> bool:
        """É race/evento?"""
        return activity.get('race', False)
    
    # ==================== TEMPERATURE ====================
    
    def get_avg_temperature_c(self, activity: Dict[str, Any]) -> Optional[float]:
        """Temperatura média em °C — pode ser None."""
        temp = activity.get('average_temp')
        if temp is not None:
            return float(temp)
        temp = activity.get('average_weather_temp')
        return float(temp) if temp is not None else None
    
    # ==================== SAFETY ====================
    
    def is_reliable(self, activity: Dict[str, Any]) -> bool:
        """Atividade tem dados confiáveis?"""
        if activity.get('icu_ignore_time'):
            return False
        if not self.get_duration_seconds(activity):
            return False
        return True
