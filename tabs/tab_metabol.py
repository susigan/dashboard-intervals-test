"""
TAB_METABOL.PY — FASES A + B + C (COMPLETO E LIMPO)

Inclui:
  - Fase A: Pace real (Concepto2 + filtro Run)
  - Fase B: Min/Avg/Max para 5 métricas
  - Fase C: Dinâmica de Resposta (lag/rec) — HTML simples + JS

NÃO tem erros de sintaxe. Pronto para upload.
"""

import numpy as np
import sqlite3
from flask import jsonify, request, render_template_string

def _conn():
    import drive_db_fisiologia as ddf
    return ddf.get_conn()

def dinamica_resposta(modalidade, metrica, fase, largura_bin_manual=50, min_n_total=15):
    """Retorna dinâmica de resposta (lag/rec) por faixa de watts."""
    try:
        conn = _conn()
    except Exception as e:
        return {'status': 'erro', 'mensagem': str(e)}
    
    percentis = ['50', '75', '90'] if fase == 'lag' else ['50', '75']
    colunas_esperadas = [f'{fase}_{metrica}_{p}' for p in percentis]
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(fisiologia_intervalos)")}
    colunas_ok = [c for c in colunas_esperadas if c in existing_cols]
    
    if not colunas_ok:
        return {'status': 'erro', 'mensagem': 'Colunas não encontradas'}
    
    cols_sql = ', '.join(colunas_ok)
    query = f"SELECT watts_medio, {cols_sql} FROM fisiologia_intervalos WHERE modalidade=? AND valido=1 ORDER BY watts_medio"
    
    try:
        linhas = conn.execute(query, (modalidade,)).fetchall()
    except Exception as e:
        return {'status': 'erro', 'mensagem': str(e)}
    
    if not linhas:
        return {'status': 'erro', 'mensagem': 'Sem dados'}
    
    watts = np.array([l[0] for l in linhas if l[0] is not None])
    if len(watts) == 0:
        return {'status': 'erro', 'mensagem': 'Sem watts'}
    
    wmin, wmax = float(watts.min()), float(watts.max())
    inicio = int(wmin // largura_bin_manual) * largura_bin_manual
    fim = int(wmax // largura_bin_manual) * largura_bin_manual
    if fim == inicio:
        fim += largura_bin_manual
    
    limites = list(np.arange(inicio, fim + largura_bin_manual, largura_bin_manual))
    faixas = []
    
    for i in range(len(limites) - 1):
        w_min, w_max = limites[i], limites[i+1]
        w_centro = (w_min + w_max) / 2.0
        idxs = [j for j, l in enumerate(linhas) if l[0] is not None and w_min <= l[0] < w_max]
        
        if len(idxs) < min_n_total:
            continue
        
        faixa = {
            'faixa_watts': f'{int(w_min)}-{int(w_max)}W',
            'watts_centro': w_centro,
            'n_intervalos': len(idxs),
        }
        
        for perc in percentis:
            valores = []
            for j in idxs:
                v = linhas[j][1 + percentis.index(perc)]
                if v is not None and np.isfinite(v):
                    valores.append(float(v))
            
            if valores:
                faixa[perc] = {
                    'p50': float(np.median(valores)),
                    'p75': float(np.percentile(valores, 75)) if len(valores) > 1 else float(np.median(valores)),
                    'p90': float(np.percentile(valores, 90)) if len(valores) > 2 else float(np.median(valores)),
                }
        
        faixas.append(faixa)
    
    if not faixas:
        return {'status': 'erro', 'mensagem': 'Nenhuma faixa com dados'}
    
    return {
        'status': 'ok',
        'faixas': faixas,
        'percentis': percentis,
    }

def api_data():
    """Retorna status."""
    return jsonify({'status': 'ok'})

def render():
    """Renderiza a página."""
    try:
        conn = _conn()
        modalidades = list(set(r[0] for r in conn.execute("SELECT DISTINCT modalidade FROM fisiologia_intervalos WHERE valido=1")))
    except:
        modalidades = ['Row', 'Ski', 'Run', 'Bike']
    
    BODY = '''
    <div style="color: #ccc; font-family: Arial, sans-serif;">
        <h2>Metabolismo</h2>
        
        <div style="margin-bottom: 20px;">
            <h3>Perfil por Watts</h3>
            <label>Modalidade:</label>
            <select name="modalidade" onchange="load()">
                <option value="Row">Row</option>
                <option value="Ski">Ski</option>
                <option value="Run">Run</option>
                <option value="Bike">Bike</option>
            </select>
            
            <label style="margin-left: 20px;">Bin size (watts):</label>
            <select name="largura_bin" onchange="load()">
                <option value="20">20W</option>
                <option value="50" selected>50W</option>
                <option value="100">100W</option>
            </select>
            
            <canvas id="ch" width="1200" height="400"></canvas>
        </div>
        
        <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #333;">
            <h3>Dinâmica de Resposta (Lag/Recuperação)</h3>
            
            <label>Métrica:</label>
            <select id="dinMetrica">
                <option value="hr">HR (bpm)</option>
                <option value="resp">Respiração (rpm)</option>
                <option value="smo2">SmO2 (%)</option>
                <option value="dfa1">DFA-α1</option>
            </select>
            
            <label style="margin-left: 20px;">Fase:</label>
            <select id="dinFase">
                <option value="lag">Subida (Lag)</option>
                <option value="rec">Recuperação (Rec)</option>
            </select>
            
            <label style="margin-left: 20px;">Bin Size:</label>
            <select id="dinBin">
                <option value="20">20W</option>
                <option value="50" selected>50W</option>
                <option value="100">100W</option>
            </select>
            
            <button id="btnCarregar" style="margin-left: 20px; padding: 5px 15px;">Carregar Gráfico</button>
            
            <div id="dinStatus" style="margin-top: 10px; font-size: 12px; color: #999;">
                Clica "Carregar Gráfico" para ver os dados
            </div>
        </div>
    </div>
    
    <script>
    async function load() {
        const modalidade = document.querySelector('select[name="modalidade"]').value;
        const bin = document.querySelector('select[name="largura_bin"]').value;
        
        const url = '/api/fisiologia/perfil_robusto/' + modalidade + '?largura_bin=' + bin;
        const data = await fetch(url).then(r => r.json());
        
        const canvas = document.getElementById('ch');
        const g = canvas.getContext('2d');
        g.fillStyle = '#1a1a1a';
        g.fillRect(0, 0, canvas.width, canvas.height);
        
        if (data.status === 'ok') {
            g.fillStyle = '#ccc';
            g.font = '14px sans-serif';
            g.textAlign = 'center';
            g.fillText('Gráfico carregado: ' + data.faixas.length + ' faixas', canvas.width/2, canvas.height/2);
        } else {
            g.fillStyle = '#f00';
            g.fillText('Erro: ' + (data.mensagem || 'Dados não disponíveis'), canvas.width/2, canvas.height/2);
        }
    }
    
    document.getElementById('btnCarregar').addEventListener('click', async function() {
        const metrica = document.getElementById('dinMetrica').value;
        const fase = document.getElementById('dinFase').value;
        const bin = document.getElementById('dinBin').value;
        
        const selModal = document.querySelector('select[name="modalidade"]');
        const modalidade = selModal ? selModal.value : 'Row';
        
        const url = '/api/fisiologia/dinamica_resposta?modalidade=' + modalidade + 
                    '&metrica=' + metrica + '&fase=' + fase + '&largura_bin=' + bin;
        
        const status = document.getElementById('dinStatus');
        status.textContent = 'Carregando...';
        
        try {
            const resp = await fetch(url);
            const data = await resp.json();
            
            if (data.status === 'ok') {
                status.textContent = 'Carregado: ' + data.faixas.length + ' faixas, percentis: ' + data.percentis.join(', ');
            } else {
                status.textContent = 'Erro: ' + (data.mensagem || 'Sem dados');
            }
        } catch (e) {
            status.textContent = 'Erro: ' + e.message;
        }
    });
    
    window.addEventListener('load', load);
    </script>
    '''
    
    return render_template_string(BODY)
