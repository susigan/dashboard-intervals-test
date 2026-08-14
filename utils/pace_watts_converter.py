"""pace_watts_converter.py — Conversão bidirecional watts ↔ pace (Concept2)."""

import numpy as np
from typing import Tuple, Optional, List, Dict


class PaceWattsConverter:
    """Conversor watts ↔ pace para Concept2 Rowing/Ski (fórmula Pépin & Beaver 1992)."""
    
    # Fórmula: watts = CONCEPT2_FACTOR × (500 / pace_segundos)³
    CONCEPT2_FACTOR = 2.8
    
    # Limites fisiológicos por modalidade
    LIMITS = {
        'Row': {'pace_min_s': 60, 'pace_max_s': 180, 'watts_min': 30, 'watts_max': 600},
        'Ski': {'pace_min_s': 55, 'pace_max_s': 200, 'watts_min': 20, 'watts_max': 500},
        'Bike': {'pace_min_s': 40, 'pace_max_s': 300, 'watts_min': 30, 'watts_max': 800},
    }
    
    def __init__(self):
        pass
    
    def watts_para_pace(self, watts: float, modalidade: str = 'Row') -> Tuple[int, int]:
        """Converter watts para pace (min, seg).
        
        Returns: (minutos, segundos)
        """
        if not self._validar_watts(watts, modalidade):
            return (0, 0)
        
        # pace = 500 / (watts / 2.8)^(1/3)
        pace_segundos = 500.0 / ((watts / self.CONCEPT2_FACTOR) ** (1/3))
        
        minutos = int(pace_segundos // 60)
        segundos = int(pace_segundos % 60)
        
        return (minutos, segundos)
    
    def pace_para_watts(self, minutos: int, segundos: int, modalidade: str = 'Row') -> float:
        """Converter pace (min, seg) para watts."""
        pace_segundos = minutos * 60 + segundos
        
        if not self._validar_pace(pace_segundos, modalidade):
            return 0.0
        
        # watts = 2.8 × (500 / pace)³
        watts = self.CONCEPT2_FACTOR * ((500.0 / pace_segundos) ** 3)
        
        return round(watts, 1)
    
    def watts_para_pace_string(self, watts: float, modalidade: str = 'Row') -> str:
        """Converter watts para string "M:SS"."""
        if not self._validar_watts(watts, modalidade):
            return ''
        
        minutos, segundos = self.watts_para_pace(watts, modalidade)
        return f'{minutos}:{segundos:02d}'
    
    def pace_string_para_watts(self, pace_str: str, modalidade: str = 'Row') -> float:
        """Converter string "M:SS" para watts."""
        try:
            partes = pace_str.split(':')
            minutos = int(partes[0])
            segundos = int(partes[1])
            return self.pace_para_watts(minutos, segundos, modalidade)
        except (ValueError, IndexError):
            return 0.0
    
    def serie_watts_para_pace(self, 
                             array_watts: List[float], 
                             modalidade: str = 'Row') -> Tuple[List[float], List[str]]:
        """Converter array de watts para paces (segundos e strings)."""
        pace_seg = []
        pace_str = []
        
        for w in array_watts:
            m, s = self.watts_para_pace(w, modalidade)
            pace_seg.append(m * 60.0 + s)
            pace_str.append(f'{m}:{s:02d}')
        
        return (pace_seg, pace_str)
    
    def normalizar_axis_pace(self, array_seg: List[float]) -> Tuple[List[float], List[str]]:
        """Converter array de segundos em labels "M:SS" para gráfico."""
        labels = []
        for seg in array_seg:
            m = int(seg // 60)
            s = int(seg % 60)
            labels.append(f'{m}:{s:02d}')
        return (array_seg, labels)
    
    def _validar_watts(self, watts: float, modalidade: str) -> bool:
        """Validar se watts está dentro dos limites."""
        if modalidade not in self.LIMITS:
            return False
        
        limites = self.LIMITS[modalidade]
        return limites['watts_min'] <= watts <= limites['watts_max']
    
    def _validar_pace(self, pace_segundos: float, modalidade: str) -> bool:
        """Validar se pace está dentro dos limites."""
        if modalidade not in self.LIMITS:
            return False
        
        limites = self.LIMITS[modalidade]
        return limites['pace_min_s'] <= pace_segundos <= limites['pace_max_s']


class PaceWattsValidator:
    """Validador de conversões pace ↔ watts."""
    
    def __init__(self, modalidade: str = 'Row'):
        self.modalidade = modalidade
        self.converter = PaceWattsConverter()
    
    def validar_redondo(self, watts: float, tolerancia: float = 2.0) -> Dict:
        """Validar que conversão watts → pace → watts é consistente."""
        m, s = self.converter.watts_para_pace(watts, self.modalidade)
        watts_volta = self.converter.pace_para_watts(m, s, self.modalidade)
        
        diferenca = abs(watts - watts_volta)
        valido = diferenca <= tolerancia
        
        return {
            'watts_original': round(watts, 1),
            'pace': f'{m}:{s:02d}',
            'watts_volta': round(watts_volta, 1),
            'diferenca': round(diferenca, 1),
            'valido': valido,
        }
