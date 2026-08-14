"""tab_metabol_enhanced.py — Extensões metabol com DFA-α1, pace e gráficos dual-axis."""

import numpy as np
import sqlite3
import plotly.graph_objects as go
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from utils.dfa_artifacts_analyzer import DFAArtifactAnalyzer
from utils.pace_watts_converter import PaceWattsConverter

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import drive_db_fisiologia as ddf


def _conn():
    conn = ddf.get_conn()
    conn.row_factory = sqlite3.Row
    return conn


class MetabolicProfileEnhanced:
    """Perfil metabólico com DFA-α1, pace e gráficos dual Y-axis."""
    
    def __init__(self, modalidade: str = 'Row'):
        self.modalidade = modalidade
        self.dfa_analyzer = DFAArtifactAnalyzer(modalidade=modalidade)
        self.pace_converter = PaceWattsConverter()
    
    def gerar_perfil_com_pace(self, 
                             modalidade: str,
                             min_n_total: int = 15,
                             n_faixas: int = 10) -> Dict:
        """Gerar perfil metabólico com coluna pace (Row/Ski)."""
        conn = _conn()
        
        linhas = conn.execute("""
            SELECT watts_medio, data, activity_id,
                   hr_plateau_work, smo2_plateau_work, thb_plateau_work,
                   resp_plateau_work, dfa1_plateau_work,
                   hr_extremo, smo2_extremo, thb_extremo, resp_extremo, dfa1_extremo,
                   lag_hr_50, lag_smo2_50, lag_thb_50, lag_resp_50, lag_dfa1_50,
                   rec_hr_50, rec_smo2_50, rec_thb_50, rec_resp_50, rec_dfa1_50
            FROM fisiologia_intervalos
            WHERE modalidade = ? AND valido = 1 AND watts_medio IS NOT NULL
            ORDER BY watts_medio
        """, (modalidade,)).fetchall()
        
        if len(linhas) < min_n_total:
            return {'status': 'dados_insuficientes', 'n': len(linhas)}
        
        watts_array = np.array([l['watts_medio'] for l in linhas])
        watts_min, watts_max = np.min(watts_array), np.max(watts_array)
        bin_width = max(15, min(60, (watts_max - watts_min) / n_faixas))
        
        bins_saida = []
        for bin_start in np.arange(watts_min, watts_max + bin_width, bin_width):
            bin_end = bin_start + bin_width
            
            linhas_bin = [l for l in linhas 
                         if bin_start <= l['watts_medio'] < bin_end]
            
            if len(linhas_bin) < 3:
                continue
            
            bin_resultado = {
                'watts_min': round(float(bin_start), 1),
                'watts_max': round(float(bin_end), 1),
                'watts_medio': round(float(np.mean([l['watts_medio'] for l in linhas_bin])), 1),
                'n': len(linhas_bin),
            }
            
            metricas = ['hr_plateau_work', 'smo2_plateau_work', 'thb_plateau_work',
                       'resp_plateau_work', 'dfa1_plateau_work',
                       'lag_hr_50', 'rec_hr_50']
            
            for metrica in metricas:
                valores = [l[metrica] for l in linhas_bin if l[metrica] is not None]
                if len(valores) >= 3:
                    vs = np.array(valores, dtype=float)
                    vs = vs[np.isfinite(vs)]
                    if len(vs) > 0:
                        bin_resultado[metrica] = {
                            'p50': round(float(np.percentile(vs, 50)), 2),
                            'p25': round(float(np.percentile(vs, 25)), 2),
                            'p75': round(float(np.percentile(vs, 75)), 2),
                        }
            
            # Adicionar pace para Row/Ski
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
    
    def grafico_perfil_dual_axis(self, perfil: Dict) -> go.Figure:
        """Gráfico com dual Y-axis: watts (esquerda) e pace (direita) para Row/Ski."""
        bins = perfil.get('bins', [])
        if not bins:
            return go.Figure().add_annotation(text='Sem dados')
        
        watts_medio = [b['watts_medio'] for b in bins]
        hr_p50 = []
        pace_labels = []
        
        for b in bins:
            if 'hr_plateau_work' in b:
                hr_p50.append(b['hr_plateau_work'].get('p50'))
            if 'pace_medio' in b:
                pace_labels.append(b['pace_medio'])
        
        fig = go.Figure()
        
        # Y1 (esquerda): HR em função de watts
        if hr_p50 and len(hr_p50) == len(watts_medio):
            fig.add_trace(go.Scatter(
                x=watts_medio,
                y=hr_p50,
                name='HR (bpm)',
                mode='lines+markers',
                line=dict(color='blue', width=2),
                yaxis='y1',
            ))
        
        # Se Row/Ski, Y2 (direita): Pace
        if perfil.get('modalidade') in ['Row', 'Ski'] and pace_labels:
            # Converter pace strings para segundos para plotar
            pace_seg = []
            for pace_str in pace_labels:
                try:
                    m, s = map(int, pace_str.split(':'))
                    pace_seg.append(m * 60 + s)
                except:
                    pace_seg.append(0)
            
            if len(pace_seg) == len(watts_medio):
                fig.add_trace(go.Scatter(
                    x=watts_medio,
                    y=pace_seg,
                    name='Pace (seg/500m)',
                    mode='lines+markers',
                    line=dict(color='red', width=2, dash='dash'),
                    yaxis='y2',
                ))
        
        # Layout com dual Y-axis
        fig.update_layout(
            title=f'Perfil Metabólico — {perfil.get("modalidade", "?")} (Dual Axis)',
            xaxis=dict(
                title='Potência (watts)',
                gridcolor='lightgray',
            ),
            yaxis=dict(
                title='HR (bpm)',
                titlefont=dict(color='blue'),
                tickfont=dict(color='blue'),
                side='left',
            ),
            yaxis2=dict(
                title='Pace (seg/500m)' if perfil.get('modalidade') in ['Row', 'Ski'] else None,
                titlefont=dict(color='red'),
                tickfont=dict(color='red'),
                overlaying='y',
                side='right',
            ),
            hovermode='x unified',
            legend=dict(x=0.02, y=0.98),
            height=500,
            template='plotly_white',
        )
        
        return fig


def evolucao_temporal_com_pace(modalidade: str,
                              campo: str,
                              watts_min: Optional[float] = None,
                              watts_max: Optional[float] = None,
                              agregacao: str = 'mes') -> Dict:
    """Evolução temporal com pace dual-axis para Row/Ski."""
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
    
    def _periodo(data_str):
        if agregacao == 'semana':
            try:
                dt = datetime.strptime(data_str, '%Y-%m-%d')
                ano, semana, _ = dt.isocalendar()
                return f'{ano}-W{semana:02d}'
            except:
                return data_str[:7]
        return data_str[:7]
    
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
        watts_vals = [g['watts_medio'] for g in grupos[periodo]]
        
        q = {
            'periodo': periodo,
            'n': len(valores),
            'p50': round(float(np.percentile(valores, 50)), 2),
            'p25': round(float(np.percentile(valores, 25)), 2),
            'p75': round(float(np.percentile(valores, 75)), 2),
        }
        
        # Adicionar pace se for modalidade Row/Ski e campo for watts
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


def grafico_evolucao_dual_axis(resultado: Dict) -> go.Figure:
    """Gráfico evolução temporal com dual Y-axis (watts + pace)."""
    evolucao = resultado.get('evolucao', [])
    if not evolucao:
        return go.Figure().add_annotation(text='Sem dados')
    
    periodos = [e['periodo'] for e in evolucao]
    p50_vals = [e['p50'] for e in evolucao]
    
    fig = go.Figure()
    
    # Y1: Métrica principal
    fig.add_trace(go.Scatter(
        x=periodos,
        y=p50_vals,
        name=resultado.get('campo', 'Valor'),
        mode='lines+markers',
        line=dict(color='blue', width=2),
        yaxis='y1',
    ))
    
    # Y2: Pace se Row/Ski
    if resultado.get('modalidade') in ['Row', 'Ski']:
        pace_seg = []
        for e in evolucao:
            if 'pace_p50' in e:
                try:
                    m, s = map(int, e['pace_p50'].split(':'))
                    pace_seg.append(m * 60 + s)
                except:
                    pace_seg.append(0)
            else:
                pace_seg.append(0)
        
        if any(pace_seg):
            fig.add_trace(go.Scatter(
                x=periodos,
                y=pace_seg,
                name='Pace (seg/500m)',
                mode='lines+markers',
                line=dict(color='red', width=2, dash='dash'),
                yaxis='y2',
            ))
    
    fig.update_layout(
        title=f'Evolução {resultado.get("campo")} — {resultado.get("modalidade")}',
        xaxis=dict(title='Período'),
        yaxis=dict(
            title=resultado.get('campo', 'Valor'),
            titlefont=dict(color='blue'),
            tickfont=dict(color='blue'),
        ),
        yaxis2=dict(
            title='Pace (seg/500m)' if resultado.get('modalidade') in ['Row', 'Ski'] else None,
            titlefont=dict(color='red'),
            tickfont=dict(color='red'),
            overlaying='y',
            side='right',
        ),
        hovermode='x unified',
        legend=dict(x=0.02, y=0.98),
        height=500,
        template='plotly_white',
    )
    
    return fig


def validacao_lote_dfa(modalidade: str) -> Dict:
    """Validar DFA-α1 de uma modalidade."""
    analyzer = DFAArtifactAnalyzer(modalidade=modalidade)
    
    conn = _conn()
    intervalos = conn.execute("""
        SELECT dfa1_plateau_work, hr_plateau_work, hr_extremo, watts_medio
        FROM fisiologia_intervalos
        WHERE modalidade = ? AND valido = 1 AND dfa1_plateau_work IS NOT NULL
        LIMIT 500
    """, (modalidade,)).fetchall()
    
    if not intervalos:
        return {
            'modalidade': modalidade,
            'resumo': {'n_total': 0},
        }
    
    resultados = []
    for iv in intervalos:
        resultado = analyzer.analisar_intervalo(
            dfa1=float(iv['dfa1_plateau_work']) if iv['dfa1_plateau_work'] else 0.0,
            hr_medio=float(iv['hr_plateau_work']) if iv['hr_plateau_work'] else 100.0,
            hr_max=float(iv['hr_extremo']) if iv['hr_extremo'] else 110.0,
            artifact_percent=None,
            watts_medio=float(iv['watts_medio']) if iv['watts_medio'] else 0.0
        )
        resultados.append(resultado)
    
    resumo = analyzer.resumo_validacao(resultados)
    
    return {
        'modalidade': modalidade,
        'resumo': resumo,
        'detalhes_primeiros_10': [
            {
                'dfa1': r.dfa1_original,
                'valido': r.esta_valido,
                'confidence': r.confidence,
                'motivo': r.motivo,
            }
            for r in resultados[:10]
        ]
    }
