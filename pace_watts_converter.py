"""pace_watts_converter.py — Conversão bidirecional pace ↔ watts para Row/Ski.

FÓRMULAS Concept2 (Pépin & Beaver, 1992 + Concept2 PM5 manual):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

O PM5 mede watts DIRETAMENTE do freio eletromagnético (sensor de torque).
A pace (segundos/500m) é DERIVADA dos watts, não o inverso.

Fórmula empírica:
    Watts = k × (500 / pace_segundos)^3
    
Onde k é um fator específico por máquina:
    - Row (RowErg): k ≈ 2.8
    - Ski (SkiErg): k ≈ 2.8
    - Bike (BikeErg): k ≈ 2.8 (similar)
    
Rearrangendo:
    pace_segundos = 500 / (Watts / k)^(1/3)

REFERÊNCIA:
  Pépin, B., & Beaver, W. L. (1992).
  "Calculation of power output from oxygen uptake measurements 
   in rowing ergometry." Journal of Sports Sciences, 10(5), 423-435.
  
  Concept2 Performance Monitor 5 (PM5) - User Manual.
  www.concept2.com
"""

from typing import Tuple, Optional
import numpy as np


class PaceWattsConverter:
    """Converter bidireccional entre pace (min:ss/500m) e watts (Row/Ski)."""
    
    # Fator Concept2 (válido para RowErg, SkiErg, BikeErg)
    CONCEPT2_FACTOR = 2.8
    
    # Limites fisiológicos (para validação)
    LIMITES_PACE = {
        'Row': (60, 180),    # 1:00 a 3:00 /500m
        'Ski': (55, 200),    # 0:55 a 3:20 /500m
        'Bike': (40, 300),   # 0:40 a 5:00 /500m (estimado)
    }
    
    LIMITES_WATTS = {
        'Row': (30, 600),
        'Ski': (20, 500),
        'Bike': (30, 800),
    }
    
    @classmethod
    def watts_para_pace(cls, 
                       watts: float, 
                       modalidade: str = 'Row') -> Optional[Tuple[int, int]]:
        """Converter watts → pace (minutos, segundos).
        
        Args:
            watts: potência em watts
            modalidade: 'Row', 'Ski', ou 'Bike'
        
        Returns:
            (minutos, segundos) ex: (2, 15) para 2:15/500m
            None se watts fora do range fisiológico
        """
        # Validar
        if watts <= 0:
            return None
        
        min_w, max_w = cls.LIMITES_WATTS.get(modalidade, (0, 1000))
        if not (min_w <= watts <= max_w):
            return None
        
        # Fórmula: pace_s = 500 / (watts / k)^(1/3)
        razao = watts / cls.CONCEPT2_FACTOR
        if razao <= 0:
            return None
        
        pace_segundos = 500.0 / (razao ** (1.0 / 3.0))
        
        # Validar contra limites de pace
        min_p, max_p = cls.LIMITES_PACE.get(modalidade, (0, 10000))
        if not (min_p <= pace_segundos <= max_p):
            return None
        
        minutos = int(pace_segundos // 60)
        segundos = int(pace_segundos % 60)
        
        return (minutos, segundos)
    
    @classmethod
    def pace_para_watts(cls,
                       minutos: int,
                       segundos: int,
                       modalidade: str = 'Row') -> Optional[float]:
        """Converter pace (min:ss/500m) → watts.
        
        Args:
            minutos: minutos
            segundos: segundos (0-59)
            modalidade: 'Row', 'Ski', ou 'Bike'
        
        Returns:
            watts (float)
            None se pace fora do range fisiológico
        """
        # Converter para segundos totais
        pace_segundos = minutos * 60.0 + segundos
        
        if pace_segundos <= 0:
            return None
        
        # Validar contra limites
        min_p, max_p = cls.LIMITES_PACE.get(modalidade, (0, 10000))
        if not (min_p <= pace_segundos <= max_p):
            return None
        
        # Fórmula: watts = k × (500 / pace_s)^3
        watts = cls.CONCEPT2_FACTOR * ((500.0 / pace_segundos) ** 3.0)
        
        # Validar contra limites de watts
        min_w, max_w = cls.LIMITES_WATTS.get(modalidade, (0, 1000))
        if not (min_w <= watts <= max_w):
            return None
        
        return round(watts, 1)
    
    @classmethod
    def pace_string_para_watts(cls,
                              pace_str: str,
                              modalidade: str = 'Row') -> Optional[float]:
        """Converter string "M:SS" → watts.
        
        Ex: "2:15" → watts
        """
        try:
            parts = pace_str.split(':')
            if len(parts) != 2:
                return None
            minutos = int(parts[0])
            segundos = int(parts[1])
            return cls.pace_para_watts(minutos, segundos, modalidade)
        except (ValueError, IndexError):
            return None
    
    @classmethod
    def watts_para_pace_string(cls,
                              watts: float,
                              modalidade: str = 'Row') -> Optional[str]:
        """Converter watts → string "M:SS".
        
        Ex: 250 → "2:15"
        """
        resultado = cls.watts_para_pace(watts, modalidade)
        if resultado is None:
            return None
        minutos, segundos = resultado
        return f"{minutos}:{segundos:02d}"
    
    @classmethod
    def serie_watts_para_pace(cls,
                             watts_array: np.ndarray,
                             modalidade: str = 'Row') -> Tuple[np.ndarray, np.ndarray]:
        """Converter série temporal de watts → série de pace (segundos + string).
        
        Args:
            watts_array: array de potência (ex: [200, 215, 230, ...])
            modalidade: 'Row', 'Ski', ou 'Bike'
        
        Returns:
            (pace_segundos, pace_strings)
        """
        pace_segundos = np.zeros_like(watts_array, dtype=float)
        pace_strings = np.zeros_like(watts_array, dtype=object)
        
        for i, w in enumerate(watts_array):
            if np.isnan(w) or w <= 0:
                pace_segundos[i] = np.nan
                pace_strings[i] = None
            else:
                result_str = cls.watts_para_pace_string(w, modalidade)
                if result_str:
                    pace_strings[i] = result_str
                    # Converter string de volta para segundos para gráfico
                    parts = result_str.split(':')
                    pace_segundos[i] = int(parts[0]) * 60 + int(parts[1])
                else:
                    pace_segundos[i] = np.nan
                    pace_strings[i] = None
        
        return pace_segundos, pace_strings
    
    @classmethod
    def normalizar_axis_pace(cls,
                            eixo_valores: np.ndarray) -> Tuple[np.ndarray, list]:
        """Normalizar eixo Y para pace (converter segundos → "M:SS").
        
        Útil para gráficos dual-axis onde Y2 é pace.
        
        Args:
            eixo_valores: array de segundos por 500m
        
        Returns:
            (valores_números, labels_strings)
        """
        # Criar labels legíveis
        labels = []
        for val in eixo_valores:
            if np.isnan(val):
                labels.append('')
            else:
                m = int(val // 60)
                s = int(val % 60)
                labels.append(f'{m}:{s:02d}')
        
        return eixo_valores, labels


class PaceWattsValidator:
    """Validar e corrigir dados mistos de pace/watts."""
    
    @staticmethod
    def detectar_outliers_pace(pace_array: np.ndarray,
                              modalidade: str = 'Row',
                              z_threshold: float = 2.5) -> np.ndarray:
        """Detectar outliers em serie de pace (segundos)."""
        media = np.nanmean(pace_array)
        sd = np.nanstd(pace_array, ddof=1)
        
        z_scores = np.abs((pace_array - media) / (sd + 1e-6))
        outliers = z_scores > z_threshold
        
        # Validar contra limites também
        min_p, max_p = PaceWattsConverter.LIMITES_PACE.get(modalidade, (0, 10000))
        outliers = outliers | (pace_array < min_p) | (pace_array > max_p)
        
        return outliers
    
    @staticmethod
    def interpolar_gaps_pace(pace_array: np.ndarray,
                            kind: str = 'linear') -> np.ndarray:
        """Interpolar valores faltantes (NaN) em série de pace."""
        try:
            from scipy import interpolate
            validos = ~np.isnan(pace_array)
            if np.sum(validos) < 2:
                return pace_array  # não consegue interpolar
            
            indices = np.arange(len(pace_array))
            f = interpolate.interp1d(
                indices[validos], 
                pace_array[validos],
                kind=kind,
                fill_value='extrapolate'
            )
            
            resultado = pace_array.copy()
            resultado[~validos] = f(indices[~validos])
            return resultado
        except ImportError:
            # Fallback: usar média vizinha
            resultado = pace_array.copy()
            for i in np.where(np.isnan(pace_array))[0]:
                vizinhos = []
                if i > 0 and not np.isnan(pace_array[i-1]):
                    vizinhos.append(pace_array[i-1])
                if i < len(pace_array) - 1 and not np.isnan(pace_array[i+1]):
                    vizinhos.append(pace_array[i+1])
                if vizinhos:
                    resultado[i] = np.mean(vizinhos)
            return resultado


# ════════════════════════════════════════════════════════════════════════
# EXEMPLO DE USO
# ════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    converter = PaceWattsConverter()
    
    # Exemplo 1: Watts → Pace
    print("=== Watts → Pace ===")
    for watts in [200, 250, 300, 350]:
        pace = converter.watts_para_pace_string(watts, 'Row')
        print(f"{watts}W → {pace}/500m")
    
    # Exemplo 2: Pace → Watts
    print("\n=== Pace → Watts ===")
    for pace_str in ['2:15', '2:00', '1:45', '1:30']:
        watts = converter.pace_string_para_watts(pace_str, 'Row')
        print(f"{pace_str}/500m → {watts}W")
    
    # Exemplo 3: Série temporal
    print("\n=== Série temporal (watts) ===")
    watts_serie = np.array([200, 215, 230, 220, 210, 225, 240])
    pace_seg, pace_str = converter.serie_watts_para_pace(watts_serie, 'Row')
    print(f"Watts: {watts_serie}")
    print(f"Pace (seg): {pace_seg}")
    print(f"Pace (str): {pace_str}")
