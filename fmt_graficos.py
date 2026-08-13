"""Visualizações FMT Tensor com Plotly para Flask.

Exporta 3 gráficos principais (retorna HTML renderizado):
  1. grafico_kappa_timeline() — κ + Δκ/14d com regime backgrounds
  2. mapa_regimes_scatter() — κ vs TSB, 2D regime map com quadrants
  3. grafico_lambda1_dimensoes() — λ₁ timeline + 5 dimensões (Load/HRV/W'/Sleep/WEED)

Baseado em tab_fmt_tensor.py (Streamlit dashboard).
Adaptado para retornar HTML/Plotly que Flask possa servir.
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO DE REGIMES (cores, labels)
# ══════════════════════════════════════════════════════════════════════════════

REGIME_CFG = {
    'build': {'label': 'BUILD', 'color': '#F39C12', 'desc': 'Acumulação'},
    'peak': {'label': 'PEAK', 'color': '#2980B9', 'desc': 'Pico'},
    'fatigue': {'label': 'FATIGUE', 'color': '#E74C3C', 'desc': 'Fadiga'},
    'recovery': {'label': 'RECOVERY', 'color': '#27AE60', 'desc': 'Recuperação'},
    'overreach': {'label': 'OVERREACH', 'color': '#8E44AD', 'desc': 'Sobrecarga'},
    'transition': {'label': 'TRANSITION', 'color': '#95A5A6', 'desc': 'Transição'},
}

CORES = {
    'azul': '#2980B9',
    'verde': '#27AE60',
    'vermelho': '#E74C3C',
    'laranja': '#F39C12',
    'cinza': '#95A5A6',
}


# ══════════════════════════════════════════════════════════════════════════════
# 1. GRÁFICO: κ Timeline + Δκ/14d
# ══════════════════════════════════════════════════════════════════════════════

def grafico_kappa_timeline(datas, kappa, regimes, tsb=None):
    """κ timeline com bandas de regime background + Δκ/14d slope.
    
    Args:
        datas: array de datas (datetime ou str YYYY-MM-DD)
        kappa: array de κ valores
        regimes: array de regime labels (build, peak, fatigue, etc)
        tsb: array de TSB (opcional, para contexto)
    
    Returns:
        go.Figure (Plotly chart object)
    """
    datas = pd.Series(pd.to_datetime(datas)).astype(str)
    kappa = np.asarray(kappa, dtype=float)
    regimes = np.asarray(regimes, dtype=str)
    
    # Calcular percentis
    kappa_valid = kappa[np.isfinite(kappa)]
    q75 = np.percentile(kappa_valid, 75) if len(kappa_valid) else 1.0
    q50 = np.percentile(kappa_valid, 50) if len(kappa_valid) else 0.5
    q25 = np.percentile(kappa_valid, 25) if len(kappa_valid) else 0.0
    
    # Calcular slope Δκ/14d
    slope = np.full_like(kappa, np.nan)
    for i in range(14, len(kappa)):
        if np.isfinite(kappa[i]) and np.isfinite(kappa[i-14]):
            slope[i] = (kappa[i] - kappa[i-14]) / 14.0
    
    # Criar figura com 2 subplots
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.65, 0.35],
        vertical_spacing=0.04,
        subplot_titles=('κ — Curvatura do Estado', 'Δκ/14d — Slope')
    )
    
    # ── Row 1: Regime background bands
    prev_regime = None
    band_start = None
    prev_color = '#888780'
    
    for i, (dt, rg) in enumerate(zip(datas, regimes)):
        if rg != prev_regime:
            if band_start is not None:
                # Converter hex to rgba
                hex_color = prev_color.lstrip('#')
                r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
                fig.add_shape(
                    type='rect',
                    x0=band_start, x1=dt,
                    y0=0, y1=1,
                    xref='x', yref='paper',
                    fillcolor=f'rgba({r},{g},{b},0.12)',
                    line_width=0,
                    layer='below',
                    row=1, col=1
                )
            band_start = dt
            prev_regime = rg
            prev_color = REGIME_CFG.get(rg, {}).get('color', '#888780')
    
    # Fechar última banda
    if band_start is not None:
        hex_color = prev_color.lstrip('#')
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        fig.add_shape(
            type='rect',
            x0=band_start, x1=datas.iloc[-1],
            y0=0, y1=1,
            xref='x', yref='paper',
            fillcolor=f'rgba({r},{g},{b},0.12)',
            line_width=0,
            layer='below',
            row=1, col=1
        )
    
    # ── Row 1: κ line
    fig.add_trace(
        go.Scatter(
            x=datas, y=kappa,
            name='κ',
            line=dict(color=CORES['azul'], width=2),
            hovertemplate='κ: %{y:.3f}<extra></extra>'
        ),
        row=1, col=1
    )
    
    # ── Row 1: Percentil reference lines
    for qv, label, color in [(q75, 'p75', CORES['vermelho']), (q50, 'p50', CORES['cinza']), (q25, 'p25', CORES['verde'])]:
        fig.add_hline(
            y=qv,
            line_dash='dot',
            line_color=color,
            line_width=1,
            annotation_text=f'  {label}={qv:.2f}',
            annotation_font_size=10,
            row=1, col=1
        )
    
    # ── Row 2: Δκ/14d slope bars
    slope_colors = [CORES['vermelho'] if s > 0 else CORES['verde'] if np.isfinite(s) else '#ccc'
                    for s in slope]
    fig.add_trace(
        go.Bar(
            x=datas, y=slope,
            name='Δκ/14d',
            marker_color=slope_colors,
            hovertemplate='Δκ/d: %{y:.4f}<extra></extra>',
            showlegend=False
        ),
        row=2, col=1
    )
    
    fig.add_hline(y=0, line_color='#666', line_width=0.8, row=2, col=1)
    
    # ── Layout
    fig.update_layout(
        title=dict(text='FMT Tensor — Evolução de κ e Regimes', font=dict(size=14, color='#222')),
        paper_bgcolor='white',
        plot_bgcolor='white',
        height=480,
        margin=dict(t=40, b=60, l=70, r=20),
        hovermode='x unified',
        legend=dict(orientation='h', y=-0.20, font=dict(color='#222', size=11),
                    bgcolor='rgba(255,255,255,0.85)', borderwidth=0),
        font=dict(color='#222', size=11)
    )
    
    fig.update_xaxes(tickfont=dict(size=10, color='#333'), linecolor='#555', tickcolor='#555', tickangle=-30)
    fig.update_yaxes(tickfont=dict(size=10, color='#333'), linecolor='#555', tickcolor='#555', gridcolor='rgba(0,0,0,0.06)')
    fig.update_yaxes(title_text='κ', row=1, col=1, title_font=dict(size=12, color='#333'))
    fig.update_yaxes(title_text='Δκ/14d', row=2, col=1, title_font=dict(size=12, color='#333'))
    
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# 2. GRÁFICO: Mapa de Regimes — κ vs TSB (2D scatter)
# ══════════════════════════════════════════════════════════════════════════════

def mapa_regimes_scatter(tsb, kappa, regimes, datas, kappa_now=None, tsb_now=None):
    """Scatter plot 2D: TSB (eixo X) vs κ (eixo Y), colorido por regime.
    
    Mostra quadrantes anotados:
    - Fadiga silenciosa (TSB alto, κ alto)
    - Acumulação intensa (TSB baixo, κ alto)
    - Supercompensação (TSB alto, κ baixo)
    
    Args:
        tsb: array de TSB
        kappa: array de κ
        regimes: array de regime labels
        datas: array de datas (para hover)
        kappa_now: κ hoje (marcador star)
        tsb_now: TSB hoje (marcador star)
    
    Returns:
        go.Figure (Plotly scatter)
    """
    tsb = np.asarray(tsb, dtype=float)
    kappa = np.asarray(kappa, dtype=float)
    regimes = np.asarray(regimes, dtype=str)
    datas = pd.Series(pd.to_datetime(datas)).astype(str)
    
    # Percentis κ
    kappa_valid = kappa[np.isfinite(kappa)]
    q75 = np.percentile(kappa_valid, 75) if len(kappa_valid) else 1.0
    q25 = np.percentile(kappa_valid, 25) if len(kappa_valid) else 0.0
    
    fig = go.Figure()
    
    # ── Scatter por regime
    for regime_key, regime_cfg in REGIME_CFG.items():
        mask = regimes == regime_key
        if not mask.any():
            continue
        
        tsb_regime = tsb[mask]
        kappa_regime = kappa[mask]
        datas_regime = datas[mask]
        
        fig.add_trace(go.Scatter(
            x=tsb_regime, y=kappa_regime,
            mode='markers',
            name=regime_cfg['label'],
            marker=dict(color=regime_cfg['color'], size=7, opacity=0.7),
            text=datas_regime,
            hovertemplate='%{text}<br>TSB: %{x:.1f} | κ: %{y:.3f}<extra></extra>'
        ))
    
    # ── Marker "Hoje"
    if kappa_now is not None and tsb_now is not None:
        fig.add_trace(go.Scatter(
            x=[tsb_now], y=[kappa_now],
            mode='markers',
            name='Hoje',
            marker=dict(color='black', size=14, symbol='star', line=dict(color='white', width=1.5)),
            hovertemplate=f'Hoje: TSB={tsb_now:.1f} | κ={kappa_now:.3f}<extra></extra>'
        ))
    
    # ── Quadrant reference lines
    fig.add_vline(x=0, line_color='#999', line_width=0.8, line_dash='dot')
    fig.add_hline(y=q75, line_color=CORES['vermelho'], line_width=0.8, line_dash='dot')
    fig.add_hline(y=q25, line_color=CORES['verde'], line_width=0.8, line_dash='dot')
    
    # ── Quadrant annotations
    tsb_min, tsb_max = float(tsb[np.isfinite(tsb)].min()), float(tsb[np.isfinite(tsb)].max())
    annotations = [
        (tsb_max * 0.95, q75 + 0.1, 'Fadiga\nsilenciosa', CORES['vermelho']),
        (tsb_min * 0.95, q75 + 0.1, 'Acumulação\nintensa', CORES['laranja']),
        (tsb_max * 0.95, q25 - 0.15, 'Supercompensação', CORES['verde']),
    ]
    
    for x, y, text, color in annotations:
        fig.add_annotation(
            x=x, y=y, text=text,
            font=dict(size=11, color=color),
            showarrow=False,
            xanchor='right' if x > 0 else 'left'
        )
    
    # ── Layout
    fig.update_layout(
        title=dict(text='FMT Tensor — Mapa de Regimes (κ vs TSB)', font=dict(size=14, color='#222')),
        paper_bgcolor='white',
        plot_bgcolor='white',
        height=400,
        margin=dict(t=40, b=60, l=70, r=20),
        xaxis=dict(title='TSB (Forma)', tickfont=dict(size=11, color='#333'), linecolor='#555', gridcolor='rgba(0,0,0,0.06)'),
        yaxis=dict(title='κ (Instabilidade)', tickfont=dict(size=11, color='#333'), linecolor='#555', gridcolor='rgba(0,0,0,0.06)'),
        legend=dict(orientation='h', y=-0.22, font=dict(color='#222', size=10),
                    bgcolor='rgba(255,255,255,0.85)', borderwidth=0),
        font=dict(color='#222', size=11)
    )
    
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# 3. GRÁFICO: λ₁ + Dimensões (5 linhas)
# ══════════════════════════════════════════════════════════════════════════════

def grafico_lambda1_dimensoes(datas, lambda1, load_z, hrv_z, wprime_z, sleep_z, weed_z, lambda1_threshold=None):
    """λ₁ timeline com 5 dimensões (Load, HRV, W', Sleep, WEED) como linhas.
    
    λ₁ alto = stress focal (concentrado numa dimensão)
    λ₁ baixo = stress multissistémico (distribuído)
    
    Args:
        datas: array de datas
        lambda1: array de λ₁ (0-1)
        load_z, hrv_z, wprime_z, sleep_z, weed_z: arrays de z-scores das 5 dimensões
        lambda1_threshold: (focal, multi) — valores para destacar treshold focal/multi
    
    Returns:
        go.Figure com 2 subplots (λ₁ e 5 dimensões)
    """
    datas = pd.Series(pd.to_datetime(datas)).astype(str)
    lambda1 = np.asarray(lambda1, dtype=float)
    
    dimensoes = {
        'Load': np.asarray(load_z, dtype=float),
        'HRV': np.asarray(hrv_z, dtype=float),
        "W'": np.asarray(wprime_z, dtype=float),
        'Sleep': np.asarray(sleep_z, dtype=float),
        'WEED': np.asarray(weed_z, dtype=float),
    }
    
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.4, 0.6],
        vertical_spacing=0.08,
        subplot_titles=('λ₁ — Focal vs Multissistemico', '5 Dimensões (z-score)')
    )
    
    # ── Row 1: λ₁
    fig.add_trace(go.Scatter(
        x=datas, y=lambda1,
        name='λ₁',
        line=dict(color=CORES['azul'], width=2),
        hovertemplate='λ₁: %{y:.3f}<extra></extra>'
    ), row=1, col=1)
    
    # ── Row 1: Thresholds focal/multi
    if lambda1_threshold:
        focal, multi = lambda1_threshold
        fig.add_hline(y=focal, line_dash='dot', line_color=CORES['vermelho'], line_width=1, row=1, col=1)
        fig.add_hline(y=multi, line_dash='dot', line_color=CORES['verde'], line_width=1, row=1, col=1)
    
    # ── Row 2: 5 dimensões
    cores_dim = [CORES['laranja'], CORES['azul'], '#9B59B6', '#1ABC9C', CORES['cinza']]
    for (dim_name, dim_data), cor in zip(dimensoes.items(), cores_dim):
        fig.add_trace(go.Scatter(
            x=datas, y=dim_data,
            name=dim_name,
            line=dict(color=cor, width=1.5),
            hovertemplate='%{fullData.name}: %{y:.2f}<extra></extra>'
        ), row=2, col=1)
    
    fig.add_hline(y=0, line_color='#ccc', line_width=0.8, row=2, col=1)
    
    # ── Layout
    fig.update_layout(
        title=dict(text='FMT Tensor — λ₁ & Dimensões', font=dict(size=14, color='#222')),
        paper_bgcolor='white',
        plot_bgcolor='white',
        height=500,
        margin=dict(t=40, b=60, l=70, r=20),
        hovermode='x unified',
        legend=dict(orientation='h', y=-0.15, font=dict(color='#222', size=10),
                    bgcolor='rgba(255,255,255,0.85)', borderwidth=0),
        font=dict(color='#222', size=11)
    )
    
    fig.update_xaxes(tickfont=dict(size=10, color='#333'), linecolor='#555', tickcolor='#555', tickangle=-30)
    fig.update_yaxes(tickfont=dict(size=10, color='#333'), linecolor='#555', tickcolor='#555', gridcolor='rgba(0,0,0,0.06)')
    fig.update_yaxes(title_text='λ₁', row=1, col=1, title_font=dict(size=11, color='#333'))
    fig.update_yaxes(title_text='z-score', row=2, col=1, title_font=dict(size=11, color='#333'))
    
    return fig


if __name__ == '__main__':
    print("fmt_graficos.py carregado")
    print("Funções disponíveis:")
    print("  - grafico_kappa_timeline(datas, kappa, regimes, tsb)")
    print("  - mapa_regimes_scatter(tsb, kappa, regimes, datas, kappa_now, tsb_now)")
    print("  - grafico_lambda1_dimensoes(datas, lambda1, load_z, hrv_z, wprime_z, sleep_z, weed_z)")
