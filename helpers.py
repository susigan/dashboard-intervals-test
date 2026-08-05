"""
🛠️ HELPERS DE TRATAMENTO SEGURO — API Intervals.icu
====================================================

Funções para lidar com None/null na resposta da API.
Baseado em OpenAPI spec v1.0.0 + análise dos logs.

Uso:
    from safe_api_helpers import ActivityProcessor
    
    processor = ActivityProcessor()
    activities = fetch_from_api()
    
    for act in activities:
        ftp = processor.get_ftp(act)
        distance = processor.get_distance(act)
        training_load = processor.get_training_load(act)
"""

import pandas as pd
from typing import Optional, Union, Dict, Any


class ActivityProcessor:
    """
    Processa atividades da API Intervals.icu com tratamento seguro de None.
    """
    
    def __init__(self, default_ftp: int = 200):
        """
        Args:
            default_ftp: FTP padrão se nenhuma métrica estiver disponível
        """
        self.default_ftp = default_ftp
    
    # ================== POWER METRICS ==================
    
    def get_ftp(self, activity: Dict[str, Any]) -> int:
        """
        Retorna FTP da atividade.
        
        Ordem de prioridade:
        1. icu_pm_ftp (Morton 3P, mais confiável)
        2. icu_ftp (rolling/profile)
        3. default_ftp
        """
        ftp = activity.get('icu_pm_ftp')
        if ftp is not None and ftp > 0:
            return int(ftp)
        
        ftp = activity.get('icu_ftp')
        if ftp is not None and ftp > 0:
            return int(ftp)
        
        return self.default_ftp
    
    def get_w_prime(self, activity: Dict[str, Any]) -> int:
        """Retorna W′ em Joules (Morton 3P)."""
        w_prime = activity.get('icu_pm_w_prime')
        return int(w_prime) if w_prime is not None else 20000
    
    def get_joules(self, activity: Dict[str, Any]) -> int:
        """Retorna energia total (Joules). CONFIÁVEL."""
        joules = activity.get('icu_joules')
        return int(joules) if joules is not None else 0
    
    def get_avg_watts(self, activity: Dict[str, Any]) -> int:
        """Retorna potência média normalizada."""
        watts = activity.get('icu_weighted_avg_watts')
        if watts is not None:
            return int(watts)
        
        watts = activity.get('icu_average_watts')
        return int(watts) if watts is not None else 0
    
    def get_intensity_factor(self, activity: Dict[str, Any]) -> float:
        """Retorna IF (Intensity Factor, 0-2)."""
        intensity = activity.get('icu_intensity')
        return float(intensity) if intensity is not None else 0.0
    
    def get_rolling_cp(self, activity: Dict[str, Any]) -> int:
        """
        Retorna Critical Power rolling.
        
        ⚠️ Pode retornar null da API.
        Retorna 0 se None.
        """
        cp = activity.get('icu_rolling_cp')
        return int(cp) if cp is not None else 0
    
    def get_training_load(self, activity: Dict[str, Any]) -> int:
        """Retorna TL (Training Load, TSS-equivalent). CONFIÁVEL."""
        tl = activity.get('icu_training_load')
        return int(tl) if tl is not None else 0
    
    # ================== DURATION ==================
    
    def get_duration_seconds(self, activity: Dict[str, Any]) -> int:
        """
        Retorna duração total em segundos.
        
        Preferência: elapsed_time (com pauses) > moving_time (pedal only)
        """
        elapsed = activity.get('elapsed_time')
        if elapsed is not None and elapsed > 0:
            return int(elapsed)
        
        moving = activity.get('moving_time')
        return int(moving) if moving is not None else 0
    
    def get_duration_minutes(self, activity: Dict[str, Any]) -> float:
        """Retorna duração em minutos (com decimais)."""
        seconds = self.get_duration_seconds(activity)
        return seconds / 60.0
    
    # ================== DISTANCE ==================
    
    def get_distance_km(self, activity: Dict[str, Any]) -> float:
        """
        Retorna distância em km.
        
        Usa icu_distance (standardizado) se disponível.
        Fallback: distance (pode estar em m se Zwift).
        """
        dist = activity.get('icu_distance')
        if dist is not None and dist > 0:
            return float(dist) / 1000.0 if dist > 1000 else float(dist)
        
        dist = activity.get('distance')
        if dist is not None and dist > 0:
            # Se Zwift (trainer=true) e distance < 50, é em metros
            if activity.get('trainer') and dist < 50:
                return dist / 1000.0
            return float(dist)
        
        return 0.0
    
    def get_avg_speed_kmh(self, activity: Dict[str, Any]) -> float:
        """Retorna velocidade média em km/h."""
        speed = activity.get('average_speed')
        return float(speed) if speed is not None else 0.0
    
    # ================== HEART RATE ==================
    
    def get_avg_hr(self, activity: Dict[str, Any]) -> int:
        """
        Retorna HR médio.
        
        Retorna 0 se:
        - icu_ignore_hr = True (dados indoor ruins)
        - HR não disponível
        """
        if activity.get('icu_ignore_hr'):
            return 0
        
        hr = activity.get('average_heartrate')
        return int(hr) if hr is not None else 0
    
    def get_max_hr(self, activity: Dict[str, Any]) -> int:
        """Retorna HR máximo."""
        if activity.get('icu_ignore_hr'):
            return 0
        
        hr = activity.get('max_heartrate')
        return int(hr) if hr is not None else 0
    
    def has_hr_data(self, activity: Dict[str, Any]) -> bool:
        """Verifica se atividade tem dados HR válidos."""
        if activity.get('icu_ignore_hr'):
            return False
        
        return activity.get('has_heartrate', False)
    
    # ================== ELEVATION ==================
    
    def get_elevation_gain_m(self, activity: Dict[str, Any]) -> float:
        """Retorna ganho de elevação em metros."""
        elev = activity.get('total_elevation_gain')
        return float(elev) if elev is not None else 0.0
    
    def get_elevation_loss_m(self, activity: Dict[str, Any]) -> float:
        """Retorna perda de elevação em metros."""
        elev = activity.get('total_elevation_loss')
        return float(elev) if elev is not None else 0.0
    
    # ================== METADATA ==================
    
    def get_activity_id(self, activity: Dict[str, Any]) -> str:
        """Retorna ID da atividade."""
        return str(activity.get('id', ''))
    
    def get_activity_name(self, activity: Dict[str, Any]) -> str:
        """Retorna nome da atividade."""
        return str(activity.get('name', 'Untitled'))
    
    def get_activity_type(self, activity: Dict[str, Any]) -> str:
        """Retorna tipo (Ride, Run, Row, Swim, VirtualRide, etc.)."""
        return str(activity.get('type', 'Unknown'))
    
    def get_start_date_local(self, activity: Dict[str, Any]) -> str:
        """Retorna data/hora local (ISO-8601): '2026-08-04T16:25:31'."""
        return str(activity.get('start_date_local', ''))
    
    def get_source(self, activity: Dict[str, Any]) -> str:
        """Retorna origem (STRAVA, UPLOAD, GARMIN_CONNECT, ZWIFT, etc.)."""
        return str(activity.get('source', 'UNKNOWN'))
    
    def is_trainer(self, activity: Dict[str, Any]) -> bool:
        """Retorna True se atividade foi em trainer indoor (Zwift, etc.)."""
        return activity.get('trainer', False)
    
    def is_commute(self, activity: Dict[str, Any]) -> bool:
        """Retorna True se tagged como commute."""
        return activity.get('commute', False)
    
    def is_race(self, activity: Dict[str, Any]) -> bool:
        """Retorna True se tagged como race/event."""
        return activity.get('race', False)
    
    # ================== TEMPERATURE ==================
    
    def get_avg_temperature_c(self, activity: Dict[str, Any]) -> Optional[float]:
        """
        Retorna temperatura média em °C.
        
        ⚠️ Pode ser None para atividades indoor.
        """
        temp = activity.get('average_temp')
        if temp is not None:
            return float(temp)
        
        # Fallback: usar weather data se disponível
        temp = activity.get('average_weather_temp')
        return float(temp) if temp is not None else None
    
    # ================== SAFETY FLAGS ==================
    
    def is_reliable(self, activity: Dict[str, Any]) -> bool:
        """
        Verifica se atividade tem dados confiáveis.
        
        Retorna False se:
        - Sem moving_time
        - icu_ignore_time = True
        """
        if activity.get('icu_ignore_time'):
            return False
        
        if not self.get_duration_seconds(activity):
            return False
        
        return True
    
    # ================== BATCH PROCESSING ==================
    
    def process_activities_to_dataframe(
        self,
        activities: list,
        include_fields: Optional[list] = None
    ) -> pd.DataFrame:
        """
        Converte lista de atividades em DataFrame com tratamento seguro.
        
        Args:
            activities: Lista de dicts da API
            include_fields: Campos a incluir. Se None, inclui defaults.
        
        Returns:
            pd.DataFrame com colunas processadas
        """
        if include_fields is None:
            include_fields = [
                'id', 'start_date_local', 'name', 'type', 'source',
                'duration_seconds', 'distance_km', 'training_load',
                'avg_watts', 'avg_hr', 'ftp', 'intensity_factor'
            ]
        
        rows = []
        for act in activities:
            row = {}
            
            # Mapear field aliases para métodos
            field_mapping = {
                'id': lambda: self.get_activity_id(act),
                'start_date_local': lambda: self.get_start_date_local(act),
                'name': lambda: self.get_activity_name(act),
                'type': lambda: self.get_activity_type(act),
                'source': lambda: self.get_source(act),
                'duration_seconds': lambda: self.get_duration_seconds(act),
                'duration_minutes': lambda: self.get_duration_minutes(act),
                'distance_km': lambda: self.get_distance_km(act),
                'training_load': lambda: self.get_training_load(act),
                'avg_watts': lambda: self.get_avg_watts(act),
                'avg_hr': lambda: self.get_avg_hr(act),
                'max_hr': lambda: self.get_max_hr(act),
                'ftp': lambda: self.get_ftp(act),
                'intensity_factor': lambda: self.get_intensity_factor(act),
                'joules': lambda: self.get_joules(act),
                'elevation_gain_m': lambda: self.get_elevation_gain_m(act),
                'is_trainer': lambda: self.is_trainer(act),
                'is_reliable': lambda: self.is_reliable(act),
            }
            
            for field in include_fields:
                if field in field_mapping:
                    row[field] = field_mapping[field]()
                else:
                    # Fallback: tenta pegar direto
                    row[field] = act.get(field)
            
            rows.append(row)
        
        return pd.DataFrame(rows)


# ================== EXEMPLOS DE USO ==================

if __name__ == '__main__':
    # Exemplo 1: Processar uma atividade
    activity = {
        'id': 'i172528054',
        'start_date_local': '2026-08-04T16:25:31',
        'name': 'Afternoon Virtual Ride',
        'type': 'VirtualRide',
        'icu_pm_ftp': 182,
        'icu_training_load': 84,
        'icu_joules': 672991,
        'distance': 25980.0,
        'icu_distance': 25980.0,
        'moving_time': 4500,
        'icu_weighted_avg_watts': 188,
        'average_heartrate': 125,
        'icu_rolling_cp': None,  # ⚠️ Null — será tratado como 0
        'trainer': True,
        'icu_ignore_hr': False,
    }
    
    processor = ActivityProcessor(default_ftp=200)
    
    print("📊 Processamento de Atividade:")
    print(f"  ID: {processor.get_activity_id(activity)}")
    print(f"  Nome: {processor.get_activity_name(activity)}")
    print(f"  Duração: {processor.get_duration_minutes(activity):.1f} min")
    print(f"  Distância: {processor.get_distance_km(activity):.1f} km")
    print(f"  TL: {processor.get_training_load(activity)}")
    print(f"  FTP: {processor.get_ftp(activity)} W")
    print(f"  Avg Watts: {processor.get_avg_watts(activity)} W")
    print(f"  Avg HR: {processor.get_avg_hr(activity)} bpm")
    print(f"  Rolling CP: {processor.get_rolling_cp(activity)} W (era None)")
    print(f"  Confiável? {processor.is_reliable(activity)}")
    
    print("\n✅ Sem NoneType errors!")
