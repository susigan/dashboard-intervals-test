"""dfa_artifacts_analyzer.py — Análise de DFA-α1 com correção de artefatos."""

import numpy as np
from typing import Tuple, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class DFAValidityResult:
    dfa1_original: float
    dfa1_normalizado: float
    artifact_percent: float
    esta_valido: bool
    motivo: str
    confidence: float
    flags: List[str]


class DFAArtifactAnalyzer:
    """Analisador de DFA-α1 com detecção de artefatos via HR/potência."""
    
    def __init__(self, 
                 modalidade: str = 'Row',
                 artifact_threshold_invalido: float = 10.0,
                 artifact_threshold_duvidoso: float = 5.0,
                 dfa_range_valido: Tuple[float, float] = (0.5, 2.0)):
        self.modalidade = modalidade
        self.artifact_threshold_invalido = artifact_threshold_invalido
        self.artifact_threshold_duvidoso = artifact_threshold_duvidoso
        self.dfa_range_valido = dfa_range_valido
        self._baseline_media = None
        self._baseline_sd = None
        self._n_amostras_baseline = 0
    
    def calibrar_com_historico(self, 
                               dfa1_historico: List[float],
                               modalidade_historico: List[str],
                               artifact_historico: List[float]) -> Dict:
        """Calibrar baseline pessoal a partir de histórico válido."""
        validos = [
            dfa1 for dfa1, mod, art in zip(
                dfa1_historico, modalidade_historico, artifact_historico
            )
            if (mod == self.modalidade and 
                art < self.artifact_threshold_duvidoso and
                self.dfa_range_valido[0] <= dfa1 <= self.dfa_range_valido[1])
        ]
        
        if len(validos) < 10:
            return {
                'status': 'amostras_insuficientes',
                'n': len(validos),
            }
        
        self._baseline_media = float(np.mean(validos))
        self._baseline_sd = float(np.std(validos, ddof=1))
        self._n_amostras_baseline = len(validos)
        
        return {
            'status': 'calibrado',
            'media': round(self._baseline_media, 3),
            'sd': round(self._baseline_sd, 3),
            'n': self._n_amostras_baseline,
        }
    
    def analisar_intervalo(self,
                          dfa1: float,
                          hr_medio: float,
                          hr_max: float,
                          artifact_percent: Optional[float] = None,
                          watts_medio: Optional[float] = None) -> DFAValidityResult:
        """Analisar validade de um intervalo DFA-α1."""
        flags = []
        dfa1_norm = dfa1
        confidence = 1.0
        
        if artifact_percent is None:
            artifact_percent = self._estimar_artifacts_de_hr_dfa1(
                dfa1, hr_medio, hr_max, watts_medio
            )
        
        # Classificação por artefatos
        if artifact_percent > self.artifact_threshold_invalido:
            flags.append(f'dropout={artifact_percent:.1f}%')
            esta_valido = False
            motivo = f'Artefatos > {self.artifact_threshold_invalido}%'
            confidence *= 0.3
        elif artifact_percent > self.artifact_threshold_duvidoso:
            flags.append(f'dropout={artifact_percent:.1f}% (duvidoso)')
            esta_valido = False
            motivo = f'Artefatos {artifact_percent:.1f}% — duvidoso'
            confidence *= 0.6
        else:
            esta_valido = True
            motivo = 'Sinal com qualidade aceitável'
        
        # Classificação por range DFA
        if not (self.dfa_range_valido[0] <= dfa1 <= self.dfa_range_valido[1]):
            flags.append(f'dfa1={dfa1:.2f} fora range {self.dfa_range_valido}')
            esta_valido = False
            motivo = f'DFA-α1 implausível ({dfa1:.2f})'
            confidence *= 0.2
        
        # Comparação com baseline pessoal
        if self._baseline_media is not None and esta_valido:
            z_score = (dfa1 - self._baseline_media) / (self._baseline_sd + 1e-6)
            if abs(z_score) > 2.0:
                flags.append(f'z-score={z_score:.1f} (outlier)')
                confidence *= 0.75
        
        return DFAValidityResult(
            dfa1_original=round(float(dfa1), 4),
            dfa1_normalizado=round(float(dfa1_norm), 4),
            artifact_percent=round(float(artifact_percent), 1),
            esta_valido=esta_valido,
            motivo=motivo,
            confidence=round(min(confidence, 1.0), 2),
            flags=flags
        )
    
    def _estimar_artifacts_de_hr_dfa1(self,
                                      dfa1: float,
                                      hr_medio: float,
                                      hr_max: float,
                                      watts_medio: Optional[float] = None) -> float:
        """Estimar % artefatos via inconsistência HR ↔ DFA-α1 (heurística)."""
        artifact_pct = 0.0
        hr_baseline = {'Row': 50, 'Bike': 50, 'Ski': 48, 'Run': 50}.get(self.modalidade, 50)
        
        # HR elevado + DFA-α1 elevado (inconsistência)
        if hr_medio > hr_baseline + 20 and dfa1 > 1.2:
            artifact_pct += 5.0
        
        # HR baixo + DFA-α1 baixo (inconsistência)
        if hr_medio < hr_baseline + 5 and dfa1 < 0.7:
            artifact_pct += 3.0
        
        # DFA-α1 fora limites plausíveis
        if dfa1 < 0.5 or dfa1 > 2.0:
            artifact_pct += 4.0
        
        # Potência muito elevada + DFA-α1 elevado
        if watts_medio is not None and watts_medio > 250 and dfa1 > 1.3:
            artifact_pct += 3.0
        
        return min(artifact_pct, 25.0)
    
    def resumo_validacao(self, 
                        resultados: List[DFAValidityResult]) -> Dict:
        """Resumir estatísticas de validação."""
        if not resultados:
            return {'n': 0}
        
        n_validos = sum(1 for r in resultados if r.esta_valido)
        n_invalidos = len(resultados) - n_validos
        
        return {
            'n_total': len(resultados),
            'n_validos': n_validos,
            'n_invalidos': n_invalidos,
            'pct_validos': round(100.0 * n_validos / len(resultados), 1),
            'confidence_media': round(float(np.mean([r.confidence for r in resultados])), 2),
            'artifact_medio': round(float(np.mean([r.artifact_percent for r in resultados])), 1),
        }
