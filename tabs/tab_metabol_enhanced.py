"""tab_metabol_enhanced.py — Extensões para tab_metabol com DFA-α1 artefatos + pace/watts dual-axis.

ADICIONA AO tab_metabol EXISTENTE:
1. Análise de DFA-α1 com correção de artefatos
2. Conversão watts ↔ pace para Row/Ski
3. Gráficos dual-axis (eixo Y1=valor, eixo Y2=pace para Row/Ski)
4. Validação de intervalos por qualidade de sinal

COMO USAR:
Copiar este arquivo para o mesmo diretório de tab_metabol.py
depois importar as funções novas nos endpoints HTTP.
"""

import numpy as np
import sqlite3
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# Importar os módulos novos (estão em utils/)
from utils.dfa_artifacts_analyzer import DFAArtifactAnalyzer
from utils.pace_watts_converter import PaceWattsConverter, PaceWattsValidator

# Importar drive_db_fisiologia da raiz
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import drive_db_fisiologia as ddf


def _conn():
    conn = ddf.get_conn()
    conn.row_factory = sqlite3.Row
    return conn


class MetabolicProfileEnhanced:
    """Perfil metabólico com DFA-α1 artefatos e conversão pace."""
    
    def __init__(self, modalidade: str = 'Row'):
        self.modalidade = modalidade
        self.dfa_analyzer = DFAArtifactAnalyzer(modalidade=modalidade)
        self.pace_converter = PaceWattsConverter()
        self._calibrar_dfa()
    
    def _calibrar_dfa(self):
        """Calibrar DFA-α1 analyzer com histórico pessoal."""
        conn = _conn()
        historico = conn.execute("""
            SELECT dfa1_plateau_work, modalidade, COALESCE(artifact_percent, 2.5) as artifact_percent
            FROM fisiologia_intervalos
            WHERE modalidade = ? AND valido = 1 AND dfa1_plateau_work IS NOT NULL
            LIMIT 365
        """, (self.modalidade,)).fetchall()
        
        if len(historico) >= 10:
            dfa1_list = [h['dfa1_plateau_work'] for h in historico]
            mod_list = [h['modalidade'] for h in historico]
            art_list = [h['artifact_percent'] for h in historico]
            
            self.dfa_analyzer.calibrar_com_historico(dfa1_list, mod_list, art_list)
    
    def adicionar_campos_pace(self, perfil: Dict) -> Dict:
        """Adicionar coluna pace aos bins de watts (para Row/Ski).
        
        Para cada bin de watts, calcula o pace correspondente e adiciona
        como coluna PACE_MEDIO no perfil.
        """
        if self.modalidade not in ['Row', 'Ski']:
            return perfil  # sem pace para Bike/Run
        
        bins = perfil.get('bins', [])
        for bin_dict in bins:
            watts_medio = bin_dict.get('watts_medio')
            if watts_medio is not None and watts_medio > 0:
                pace_str = self.pace_converter.watts_para_pace_string(watts_medio, self.modalidade)
                bin_dict['pace_medio'] = pace_str
                
                # Também adicionar em segundos para cálculos
                parts = pace_str.split(':')
                pace_seg = int(parts[0]) * 60 + int(parts[1])
                bin_dict['pace_medio_segundos'] = pace_seg
        
        return perfil
    
    def adicionar_validacao_dfa(self, intervalo_dict: Dict) -> Dict:
        """Validar DFA-α1 de um intervalo e adicionar flags."""
        dfa1 = intervalo_dict.get('dfa1_plateau_work')
        if dfa1 is None:
            return intervalo_dict
        
        resultado = self.dfa_analyzer.analisar_intervalo(
            dfa1=dfa1,
            hr_medio=intervalo_dict.get('hr_plateau_work', 100),
            hr_max=intervalo_dict.get('hr_extremo', 110),
            artifact_percent=intervalo_dict.get('artifact_percent'),
            watts_medio=intervalo_dict.get('watts_medio')
        )
        
        intervalo_dict['dfa1_validacao'] = {
            'original': resultado.dfa1_original,
            'normalizado': resultado.dfa1_normalizado,
            'esta_valido': resultado.esta_valido,
            'confidence': resultado.confidence,
            'motivo': resultado.motivo,
            'flags': resultado.flags,
        }
        
        return intervalo_dict
    
    def gerar_perfil_com_pace(self, 
                             modalidade: str,
                             min_n_total: int = 15,
                             n_faixas: int = 10) -> Dict:
        """Gerar perfil metabólico com coluna pace para Row/Ski."""
        conn = _conn()
        
        # Buscar dados brutos
        linhas = conn.execute("""
            SELECT watts_medio, data, activity_id,
                   hr_plateau_work, smo2_plateau_work, thb_plateau_work,
                   resp_plateau_work, dfa1_plateau_work,
                   hr_extremo, smo2_extremo, thb_extremo, resp_extremo, dfa1_extremo,
                   lag_hr_50, lag_smo2_50, lag_thb_50, lag_resp_50, lag_dfa1_50,
                   rec_hr_50, rec_smo2_50, rec_thb_50, rec_resp_50, rec_dfa1_50,
                   artifact_percent
            FROM fisiologia_intervalos
            WHERE modalidade = ? AND valido = 1 AND watts_medio IS NOT NULL
            ORDER BY watts_medio
        """, (modalidade,)).fetchall()
        
        if len(linhas) < min_n_total:
            return {'status': 'dados_insuficientes', 'n': len(linhas)}
        
        # Calcular range de watts
        watts_array = np.array([l['watts_medio'] for l in linhas])
        watts_min, watts_max = np.min(watts_array), np.max(watts_array)
        bin_width = max(15, min(60, (watts_max - watts_min) / n_faixas))
        
        # Criar bins e agregar
        bins_saida = []
        for bin_start in np.arange(watts_min, watts_max + bin_width, bin_width):
            bin_end = bin_start + bin_width
            
            linhas_bin = [l for l in linhas 
                         if bin_start <= l['watts_medio'] < bin_end]
            
            if len(linhas_bin) < 3:  # MIN_POR_FAIXA
                continue
            
            # Aggregar cada métrica
            bin_resultado = {
                'watts_min': round(bin_start, 1),
                'watts_max': round(bin_end, 1),
                'watts_medio': round(np.mean([l['watts_medio'] for l in linhas_bin]), 1),
                'n': len(linhas_bin),
            }
            
            # Adicionar quartis para cada métrica
            metricas = ['hr_plateau_work', 'smo2_plateau_work', 'thb_plateau_work',
                       'resp_plateau_work', 'dfa1_plateau_work',
                       'lag_hr_50', 'rec_hr_50']
            
            for metrica in metricas:
                valores = [l[metrica] for l in linhas_bin if l[metrica] is not None]
                if len(valores) >= 3:
                    vs = np.array(valores, dtype=float)
                    vs = vs[np.isfinite(vs)]
                    bin_resultado[metrica] = {
                        'p50': round(float(np.percentile(vs, 50)), 2),
                        'p25': round(float(np.percentile(vs, 25)), 2),
                        'p75': round(float(np.percentile(vs, 75)), 2),
                    }
            
            # Adicionar pace se Row/Ski
            if modalidade in ['Row', 'Ski']:
                pace_str = self.pace_converter.watts_para_pace_string(
                    bin_resultado['watts_medio'], modalidade
                )
                bin_resultado['pace_medio'] = pace_str
            
            bins_saida.append(bin_resultado)
        
        return {
            'status': 'ok',
            'modalidade': modalidade,
            'n_bins': len(bins_saida),
            'bins': bins_saida,
        }


def evolucao_temporal_com_pace(modalidade: str,
                              campo: str,
                              watts_min: Optional[float] = None,
                              watts_max: Optional[float] = None,
                              agregacao: str = 'mes') -> Dict:
    """Evolução temporal de uma métrica, com pace se Row/Ski."""
    conn = _conn()
    
    cond = ["modalidade = ?", "valido = 1", f"{campo} IS NOT NULL"]
    params = [modalidade]
    
    if watts_min is not None:
        cond.append("watts_medio >= ?")
        params.append(watts_min)
    if watts_max is not None:
        cond.append("watts_medio <= ?")
        params.append(watts_max)
    
    linhas = conn.execute(
        f"""SELECT data, {campo} as valor, watts_medio
           FROM fisiologia_intervalos
           WHERE {' AND '.join(cond)} ORDER BY data""",
        tuple(params)
    ).fetchall()
    
    if not linhas:
        return {'status': 'dados_insuficientes', 'n_disponivel': 0}
    
    # Agrupar por período
    def _periodo(data_str):
        if agregacao == 'semana':
            dt = datetime.strptime(data_str, '%Y-%m-%d')
            ano, semana, _ = dt.isocalendar()
            return f'{ano}-W{semana:02d}'
        return data_str[:7]  # YYYY-MM
    
    grupos = {}
    for l in linhas:
        p = _periodo(l['data'])
        grupos.setdefault(p, []).append({
            'valor': l['valor'],
            'watts_medio': l['watts_medio']
        })
    
    saida = []
    converter = PaceWattsConverter()
    
    for periodo in sorted(grupos.keys()):
        valores = [g['valor'] for g in grupos[periodo]]
        q = {
            'periodo': periodo,
            'n': len(valores),
            'p50': round(float(np.percentile(valores, 50)), 2),
            'p25': round(float(np.percentile(valores, 25)), 2),
            'p75': round(float(np.percentile(valores, 75)), 2),
        }
        
        # Se campo é watts e modalidade é Row/Ski, calcular pace também
        if 'watts' in campo.lower() and modalidade in ['Row', 'Ski']:
            pace_str = converter.watts_para_pace_string(q['p50'], modalidade)
            if pace_str:
                q['pace_p50'] = pace_str
        
        saida.append(q)
    
    return {
        'status': 'ok',
        'modalidade': modalidade,
        'campo': campo,
        'watts_min': watts_min,
        'watts_max': watts_max,
        'n_periodos': len(saida),
        'evolucao': saida,
    }


def validacao_lote_dfa(modalidade: str) -> Dict:
    """Validar todos os DFA-α1 de uma modalidade e retornar resumo."""
    analyzer = DFAArtifactAnalyzer(modalidade=modalidade)
    analyzer._calibrar_dfa()  # carregar baseline
    
    conn = _conn()
    intervalos = conn.execute("""
        SELECT dfa1_plateau_work, hr_plateau_work, hr_extremo, watts_medio,
               artifact_percent
        FROM fisiologia_intervalos
        WHERE modalidade = ? AND valido = 1 AND dfa1_plateau_work IS NOT NULL
        LIMIT 500
    """, (modalidade,)).fetchall()
    
    resultados = []
    for iv in intervalos:
        resultado = analyzer.analisar_intervalo(
            dfa1=iv['dfa1_plateau_work'],
            hr_medio=iv['hr_plateau_work'] or 100,
            hr_max=iv['hr_extremo'] or 110,
            artifact_percent=iv['artifact_percent'],
            watts_medio=iv['watts_medio']
        )
        resultados.append(resultado)
    
    resumo = analyzer.resumo_validacao(resultados)
    
    return {
        'modalidade': modalidade,
        'resumo': resumo,
        'detalhes_primeiros_10': [
            {
                'dfa1': r.dfa1_original,
                'valid': r.esta_valido,
                'confidence': r.confidence,
                'motivo': r.motivo,
            }
            for r in resultados[:10]
        ]
    }


# ════════════════════════════════════════════════════════════════════════════
# EXEMPLO: Como integrar ao app.py
# ════════════════════════════════════════════════════════════════════════════

"""
Em app.py, adicionar estas rotas:

@app.route('/api/fisiologia/perfil_enhanced/<modalidade>')
def api_perfil_enhanced(modalidade):
    enh = MetabolicProfileEnhanced(modalidade)
    perfil = enh.gerar_perfil_com_pace(modalidade)
    return jsonify(perfil)

@app.route('/api/fisiologia/evolucao_com_pace')
def api_evolucao_com_pace():
    modalidade = request.args.get('modalidade', 'Row')
    campo = request.args.get('campo', 'watts_medio')
    watts_min = request.args.get('watts_min', type=float)
    watts_max = request.args.get('watts_max', type=float)
    resultado = evolucao_temporal_com_pace(modalidade, campo, watts_min, watts_max)
    return jsonify(resultado)

@app.route('/api/fisiologia/validacao_dfa/<modalidade>')
def api_validacao_dfa(modalidade):
    resultado = validacao_lote_dfa(modalidade)
    return jsonify(resultado)
"""
