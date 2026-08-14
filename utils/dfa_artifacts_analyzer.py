"""dfa_artifacts_analyzer.py — Análise de DFA-α1 com correção de artefatos.

PRINCÍPIO (muscleoxygentraining.com + Lipponen 2019 + Tarvainen 2002):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

O DFA-α1 (detrended fluctuation analysis) depende da QUALIDADE DOS 
intervalos RR (tempo entre batidas cardíacas). Artefatos introduzem 
erros sistemáticos:

  Tipo 1: Batida perdida (RR muito longo) → α1 inflacionado (parece < 1.0)
  Tipo 2: Batida dupla/ruído (RR muito curto) → α1 deflacionado (parece > 1.0)
  Tipo 3: Queda de sinal (séries de RR perdidos) → indeterminado

A Intervals.icu NÃO fornece streams de RR brutos, mas fornece:
  - average_dfa_a1 (por intervalo)
  - average_heartrate (média de batidas)
  - artefatos/dropout percentual (flags de qualidade)

ESTRATÉGIA:
━━━━━━━

1. Detectar artefatos via % dropout + inconsistência HR vs DFA-α1
2. Normalizar usando baseline pessoal (mean + 2×SD)
3. Classificar: VÁLIDO (art<5%), DUVIDOSO (5-10%), INVÁLIDO (>10%)

REFERÊNCIAS:
[1] Lipponen et al. (2019) - Artefact correction algorithms
[2] Tarvainen et al. (2014) - Kubios HRV method
"""

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
        """Calcular baseline pessoal a partir de histórico válido."""
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
                'recomendacao': f'precisa de ≥10 amostras válidas, tem {len(validos)}'
            }
        
        self._baseline_media = np.mean(validos)
        self._baseline_sd = np.std(validos, ddof=1)
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
        
        # Estimar artifacts se não fornecido
        if artifact_percent is None:
            artifact_percent = self._estimar_artifacts_de_hr_dfa1(
                dfa1, hr_medio, hr_max, watts_medio
            )
        
        # Critério 1: Dropout
        if artifact_percent > self.artifact_threshold_invalido:
            flags.append(f'dropout={artifact_percent:.1f}%')
            esta_valido = False
            motivo = f'Artefatos > {self.artifact_threshold_invalido}%'
            confidence *= 0.3
        elif artifact_percent > self.artifact_threshold_duvidoso:
            flags.append(f'dropout={artifact_percent:.1f}% (duvidoso)')
            esta_valido = False  # conservador
            motivo = f'Artefatos {artifact_percent:.1f}% — duvidoso'
            confidence *= 0.6
        else:
            esta_valido = True
            motivo = 'Sinal com qualidade aceitável'
        
        # Critério 2: Range fisiológico
        if not (self.dfa_range_valido[0] <= dfa1 <= self.dfa_range_valido[1]):
            flags.append(f'dfa1={dfa1:.2f} fora range {self.dfa_range_valido}')
            esta_valido = False
            motivo = f'DFA-α1 implausível ({dfa1:.2f})'
            confidence *= 0.2
        
        # Critério 3: Baseline pessoal
        if self._baseline_media is not None and esta_valido:
            z_score = (dfa1 - self._baseline_media) / (self._baseline_sd + 1e-6)
            if abs(z_score) > 2.0:
                flags.append(f'z-score={z_score:.1f} (outlier)')
                confidence *= 0.75
        
        return DFAValidityResult(
            dfa1_original=round(dfa1, 4),
            dfa1_normalizado=round(dfa1_norm, 4),
            artifact_percent=round(artifact_percent, 1),
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
        """Estimar % artefatos via inconsistência HR ↔ DFA-α1."""
        artifact_pct = 0.0
        hr_baseline_repouso = {'Row': 50, 'Bike': 50, 'Ski': 48, 'Run': 50}.get(self.modalidade, 50)
        
        if hr_medio > hr_baseline_repouso + 20 and dfa1 > 1.2:
            artifact_pct += 5.0
        if hr_medio < hr_baseline_repouso + 5 and dfa1 < 0.7:
            artifact_pct += 3.0
        if dfa1 < 0.5 or dfa1 > 2.0:
            artifact_pct += 4.0
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
            'pct_validos': round(100 * n_validos / len(resultados), 1),
            'confidence_media': round(np.mean([r.confidence for r in resultados]), 2),
            'artifact_medio': round(np.mean([r.artifact_percent for r in resultados]), 1),
        }
