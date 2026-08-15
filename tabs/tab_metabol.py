"""tab_metabol.py — CORRIGIDO: apenas campos que existem na BD."""
from flask import jsonify, request
import numpy as np
import sqlite3
from datetime import datetime
import drive_db_fisiologia as ddf
from tabs.base import page
SLUG = 'metabol'
# APENAS os campos que REALMENTE existem na BD:
# hr_max_60s, hr_avg_60s (sem hr_min!)
# resp_avg_60s (sem resp_min, resp_max!)
# smo2_min_60s (sem smo2_max, smo2_avg!)
# dfa1_clean (apenas um valor)
METRICAS_BASE = ['hr', 'resp', 'smo2', 'dfa1']
# MAP REAL: só os que existem!
CAMPOS_DB = {
    'hr': {
        'max': 'hr_max_60s',
        'avg': 'hr_avg_60s',
        # 'min' NÃO EXISTE
    },
    'resp': {
        'avg': 'resp_avg_60s',
        # 'min', 'max' NÃO EXISTEM
    },
    'smo2': {
        'min': 'smo2_min_60s',
        # 'max', 'avg' NÃO EXISTEM
    },
    'dfa1': {
        'avg': 'dfa1_clean',
        # 'min', 'max' NÃO EXISTEM (é um único valor)
    },
}
# AGREGAÇÕES DISPONÍVEIS por métrica
AGREGACOES_VALIDAS = {
    'hr': ['max', 'avg'],
    'resp': ['avg'],
    'smo2': ['min'],
    'dfa1': ['avg'],
}
CORES_METAB = {
    'hr': '#E74C3C',
    'resp': '#1ABC9C',
    'smo2': '#F39C12',
    'dfa1': '#9B59B6',
}
LABELS_METAB = {
    'hr': 'HR (bpm)',
    'resp': 'Respiração (rpm)',
    'smo2': 'SmO₂ (%)',
    'dfa1': 'DFA-α1 (clean)',
}

CAMPOS_DB = {
    m: {a: f'{m}_{a}_60s' for a in AGREGACOES} for m in METRICAS_BASE
}

AGREGACOES_VALIDAS = {m: list(AGREGACOES) for m in METRICAS_BASE}

CORES_METAB = {
    'hr': '#E74C3C',
    'resp': '#1ABC9C',
    'smo2': '#F39C12',
    'thb': '#3498DB',
    'dfa1': '#9B59B6',
}
LABELS_AGREGACAO = {
    'min': 'Mín',
    'max': 'Máx',
    'avg': 'Méd',
}
def _conn():
    conn = ddf.get_conn()
    conn.row_factory = sqlite3.Row
    return conn
def _fmt_pace(segundos):
    """Segundos -> 'm:ss'. None se o valor nao fizer sentido."""
    if segundos is None:
        return None
    try:
        segundos = float(segundos)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(segundos) or segundos <= 0 or segundos > 3600:
        return None
    return f'{int(segundos // 60)}:{int(segundos % 60):02d}'
def _pace_da_faixa_concept2(watts_medio, modalidade):
    """Calcula pace usando FÓRMULA Concepto2 baseada em WATTS.
    
    Isto é mais fiável que API porque watts vêm do sensor directamente.
    Concepto2: pace(seg/500m) = 500 / ((watts/2.8)^(1/3))
    """
    if watts_medio is None or watts_medio <= 0:
        return None
    
    try:
        watts = float(watts_medio)
        if watts <= 0:
            return None
        # Fórmula Concepto2 para segundos por 500m
        pace_500m = 500.0 / ((watts / 2.8) ** (1.0/3.0))
        
        if not np.isfinite(pace_500m) or pace_500m <= 0 or pace_500m > 3600:
            return None
        
        txt = _fmt_pace(pace_500m)
        return f'{txt} /500m' if txt else None
    except (ValueError, ZeroDivisionError):
        return None
def _pace_da_faixa(pace_s_km_mediano, modalidade):
    """Formata o pace medido para a unidade convencional da modalidade.
    
    Row/Ski: usam FÓRMULA Concepto2 (watts → pace)
    Run: usa dados reais (API), com filtro de credibilidade
    Bike: sem pace
    """
    if modalidade in ('Row', 'Ski'):
        return None  # Será calculado via _pace_da_faixa_concept2() baseado em watts
    
    if modalidade == 'Run':
        if pace_s_km_mediano is None:
            return None
        try:
            segundos = float(pace_s_km_mediano)
        except (TypeError, ValueError):
            return None
        
        # Filtro: pace >= 120s/km (2:00) é válido; < 120s é error
        if not np.isfinite(segundos) or segundos < 120 or segundos > 3600:
            return None
        
        txt = _fmt_pace(segundos)
        return f'{txt} /km' if txt else None
    
    return None
def modalidades_disponiveis():
    conn = _conn()
    resultado = conn.execute("""
        SELECT modalidade, COUNT(*) as n, COUNT(DISTINCT data) as n_dias, 
               COUNT(DISTINCT activity_id) as n_atividades
        FROM fisiologia_intervalos
        WHERE valido = 1 AND watts_medio IS NOT NULL
        GROUP BY modalidade
        ORDER BY modalidade
    """).fetchall()
    return [
        {'modalidade': r['modalidade'], 'n': r['n'], 'n_dias': r['n_dias'], 'n_atividades': r['n_atividades']}
        for r in resultado
    ]
def perfil_por_modalidade(modalidade, campos_selecionados, min_n_total=15, largura_bin_manual=50):
    """
    Perfil com PONDERAÇÃO.
    campos_selecionados: dict {metrica_base: agregacao}
    Ex: {'hr': 'max', 'resp': 'avg', 'smo2': 'min', 'dfa1': 'avg'}
    """
    conn = _conn()
    
    # VALIDAR que as agregações são válidas
    para_buscar = {}
    for metrica_base, agregacao in campos_selecionados.items():
        if agregacao in AGREGACOES_VALIDAS.get(metrica_base, []):
            coluna_db = CAMPOS_DB[metrica_base][agregacao]
            para_buscar[f'{metrica_base}_{agregacao}'] = coluna_db
        # Se agregação inválida, ignora (não inclui na query)
    
    if not para_buscar:
        return {'status': 'erro', 'mensagem': 'Nenhuma métrica válida selecionada'}
    
    todas_colunas = set(['watts_medio', 'data', 'activity_id', 'interval_num'])
    # pace_s_km é opcional — criada pelo worker mas pode não existir ainda
    try:
        existentes = {r[1] for r in conn.execute("PRAGMA table_info(fisiologia_intervalos)")}
        if 'pace_s_km' in existentes:
            todas_colunas.add('pace_s_km')
    except:
        pass
    todas_colunas.update(para_buscar.values())
    colunas_str = ", ".join(todas_colunas)
    
    linhas = conn.execute(
        f"""SELECT {colunas_str}
           FROM fisiologia_intervalos
           WHERE modalidade = ? AND valido = 1 AND watts_medio IS NOT NULL
           ORDER BY data DESC, activity_id DESC, interval_num DESC""",
        (modalidade,)
    ).fetchall()
    if len(linhas) < min_n_total:
        return {'status': 'dados_insuficientes', 'modalidade': modalidade, 'n_disponivel': len(linhas)}
    n_linhas = len(linhas)
    corte = int(n_linhas * 0.3)
    pesos = np.ones(n_linhas)
    pesos[:corte] = 1.5
    watts = np.array([l['watts_medio'] for l in linhas])
    wmin, wmax = float(watts.min()), float(watts.max())
    # Gerar bins — APENAS até a última faixa com dados
    # Isto evita espaço vazio à direita (problema do Run)
    inicio = int(wmin // largura_bin_manual) * largura_bin_manual
    fim = int(wmax // largura_bin_manual) * largura_bin_manual  # sem +1
    if fim == inicio:
        fim += largura_bin_manual
    limites = list(np.arange(inicio, fim + largura_bin_manual, largura_bin_manual))
    faixas_saida = []
    for i in range(len(limites) - 1):
        lo, hi = limites[i], limites[i + 1]
        ultima = (i == len(limites) - 2)
        mask = (watts >= lo) & ((watts <= hi) if ultima else (watts < hi))
        idxs = np.where(mask)[0]
        if len(idxs) == 0:
            continue
        watts_centro = round((lo + hi) / 2, 1)
        faixa = {
            'faixa_watts': f'{lo:.0f}-{hi:.0f}W',
            'watts_min': round(float(lo), 1),
            'watts_max': round(float(hi), 1),
            'watts_centro': watts_centro,
            'n_intervalos': len(idxs),
        }
        
        # FASE A — pace por modalidade
        # Row/Ski: FÓRMULA Concepto2 (watts → pace), mais confiável
        # Run: dados API com FILTRO (pace >= 2:00/km)
        if modalidade in ('Row', 'Ski'):
            # Concepto2: pace = 500 / ((watts/2.8)^(1/3))
            pace_txt = _pace_da_faixa_concept2(watts_centro, modalidade)
            if pace_txt:
                faixa['pace_medio'] = pace_txt
        elif modalidade == 'Run':
            # Run: usar dados reais da API, filtrar valores inválidos
            paces = []
            for j in idxs:
                try:
                    v = linhas[j].get('pace_s_km') if hasattr(linhas[j], 'get') else linhas[j]['pace_s_km']
                except (IndexError, KeyError, TypeError, AttributeError):
                    v = None
                if v is not None and np.isfinite(v):
                    # Filtro: apenas pace >= 120s (>= 2:00/km)
                    if float(v) >= 120:
                        paces.append(float(v))
            if paces:
                try:
                    pace_mediano = float(np.median(paces))
                    pace_txt = _pace_da_faixa(pace_mediano, modalidade)
                    if pace_txt:
                        faixa['pace_medio'] = pace_txt
                        faixa['n_pace'] = len(paces)
                except (ValueError, TypeError):
                    pass
        
        # Para cada métrica selecionada (VALIDADA)
        for chave_unica, coluna_db in para_buscar.items():
            valores = [linhas[j][coluna_db] for j in idxs]
            pesos_faixa = pesos[idxs]
            
            vs_validos = []
            ps_validos = []
            for v, p in zip(valores, pesos_faixa):
                if v is not None and np.isfinite(v):
                    vs_validos.append(v)
                    ps_validos.append(p)
            
            if len(vs_validos) > 0:
                vs_arr = np.array(vs_validos)
                ps_arr = np.array(ps_validos)
                
                vs_sorted_idx = np.argsort(vs_arr)
                vs_sorted = vs_arr[vs_sorted_idx]
                ps_sorted = ps_arr[vs_sorted_idx]
                
                ps_cum = np.cumsum(ps_sorted) / np.sum(ps_sorted)
                
                faixa[chave_unica] = {
                    'p10': round(float(vs_sorted[min(np.searchsorted(ps_cum, 0.10), len(vs_sorted)-1)]), 2),
                    'p25': round(float(vs_sorted[min(np.searchsorted(ps_cum, 0.25), len(vs_sorted)-1)]), 2),
                    'p50': round(float(vs_sorted[min(np.searchsorted(ps_cum, 0.50), len(vs_sorted)-1)]), 2),
                    'p75': round(float(vs_sorted[min(np.searchsorted(ps_cum, 0.75), len(vs_sorted)-1)]), 2),
                    'p90': round(float(vs_sorted[min(np.searchsorted(ps_cum, 0.90), len(vs_sorted)-1)]), 2),
                    'n': len(vs_validos),
                }
        
        faixas_saida.append(faixa)
    return {
        'status': 'ok',
        'modalidade': modalidade,
        'n_intervalos_total': len(linhas),
        'campos_selecionados': campos_selecionados,
        'faixas': faixas_saida,
    }
def evolucao_temporal(modalidade, metrica, agregacao, watts_min=None, watts_max=None, min_por_periodo=3):
    """Evolução temporal com agregação dinâmica."""
    
    # VALIDAR agregação
    if agregacao not in AGREGACOES_VALIDAS.get(metrica, []):
        return {'status': 'erro', 'mensagem': f'agregacao inválida: {metrica} não tem {agregacao}'}
    
    coluna_db = CAMPOS_DB[metrica][agregacao]
    
    conn = _conn()
    cond = ["modalidade = ?", "valido = 1", f"{coluna_db} IS NOT NULL"]
    params = [modalidade]
    if watts_min is not None:
        cond.append("watts_medio >= ?")
        params.append(watts_min)
    if watts_max is not None:
        cond.append("watts_medio <= ?")
        params.append(watts_max)
    linhas = conn.execute(
        f"""SELECT data, {coluna_db} as valor FROM fisiologia_intervalos
           WHERE {' AND '.join(cond)} ORDER BY data""",
        tuple(params)
    ).fetchall()
    if not linhas:
        return {'status': 'dados_insuficientes', 'n_disponivel': 0}
    grupos = {}
    for l in linhas:
        p = l['data'][:7]
        grupos.setdefault(p, []).append(l['valor'])
    saida = []
    for periodo in sorted(grupos.keys()):
        vs = [v for v in grupos[periodo] if v is not None and np.isfinite(v)]
        if len(vs) < min_por_periodo:
            continue
        
        vs_arr = np.array(vs)
        saida.append({
            'periodo': periodo,
            'p10': round(float(np.percentile(vs_arr, 10)), 2),
            'p25': round(float(np.percentile(vs_arr, 25)), 2),
            'p50': round(float(np.percentile(vs_arr, 50)), 2),
            'p75': round(float(np.percentile(vs_arr, 75)), 2),
            'p90': round(float(np.percentile(vs_arr, 90)), 2),
            'n': len(vs),
        })
    return {
        'status': 'ok',
        'metrica': metrica,
        'agregacao': agregacao,
        'periodos': saida,
    }
BODY = r"""
<h1>Metabolismo</h1>
<div class="tabs" style="border-bottom:1px solid #21262d; margin-bottom:20px;">
  <button class="tab-btn active" data-tab="perfil_watts">Perfil por Watts</button>
  <button class="tab-btn" data-tab="outras">Outras Análises</button>
</div>
<div id="perfil_watts" class="tab-content active">
<div class="controls">
  <label class="sel">Modalidade
    <select id="modalidade"></select></label>
  <label class="sel">Bin size (watts)
    <select id="larguraBin">
      <option value="20">20W</option>
      <option value="50" selected>50W</option>
      <option value="100">100W</option>
    </select></label>
</div>
<div class="controls" id="agregacaoControls"></div>
<h2>Perfil metabólico — ponderado (últimos 30% com 1.5x peso)</h2>
<div id="tooltip" style="position:absolute;background:#000;color:#fff;padding:8px;border-radius:3px;font-size:11px;display:none;z-index:1000;pointer-events:none;border:1px solid #666;white-space:nowrap;"></div>
<div class="legend" id="lgPerfil"></div>
<div class="chartbox">
  <canvas id="chPerfil" height="300"></canvas>
</div>
<h2>Evolução ao longo do tempo</h2>
<div class="controls">
  <label class="sel">Métrica
    <select id="metricaEvolucao"></select></label>
  <label class="sel">Agregação
    <select id="agregacaoEvolucao"></select></label>
  <label class="sel">Watts min <input type="number" id="wattsMin" value="200" style="width:70px"></label>
  <label class="sel">Watts max <input type="number" id="wattsMax" value="350" style="width:70px"></label>
  <button onclick="carregarEvolucao()">Actualizar</button>
</div>
<div class="chartbox">
  <canvas id="chEvolucao" height="240"></canvas>
</div>
</div>
<div id="outras" class="tab-content" style="display:none;">
  <p style="color:#8b949e;">Outras análises virão aqui...</p>
</div>
<style>
.tabs { display:flex; gap:20px; }
.tab-btn { background:none; border:none; color:#8b949e; padding:10px 0; cursor:pointer; font-size:14px; border-bottom:2px solid transparent; }
.tab-btn.active { color:#fff; border-bottom-color:#fff; }
.tab-content { display:none; }
.tab-content.active { display:block; }
</style>
"""
JS = r"""
let MODALIDADES = [];
let PERFIL = null;
let EVOLUCAO = null;
let isLoadingPerfil = false;
let isLoadingEvolucao = false;
const CORES_METAB = {
 hr:'#E74C3C', resp:'#1ABC9C', smo2:'#F39C12', dfa1:'#9B59B6',
};
const LABELS_METAB = {
 hr:'HR (bpm)', resp:'Respiração (rpm)', smo2:'SmO₂ (%)', dfa1:'DFA-α1 (clean)',
};
const LABELS_AGREGACAO = {
 min:'Mín', max:'Máx', avg:'Méd',
};
const METRICAS_BASE = ['hr', 'resp', 'smo2', 'dfa1'];
// AGREGAÇÕES REAIS (apenas as que existem na BD)
const AGREGACOES_VALIDAS = {
 hr: ['max', 'avg'],
 resp: ['avg'],
 smo2: ['min'],
 dfa1: ['avg'],
};
let chartState = {chPerfil: {}, chEvolucao: {}};
let camposSelecionados = {hr:'max', resp:'avg', smo2:'min', dfa1:'avg'};
function ctx(canvasId, h){
 const canvas = document.getElementById(canvasId);
 if(!canvas) return null;
 canvas.height = h;
 const rect = canvas.getBoundingClientRect();
 canvas.width = rect.width;
 const g = canvas.getContext('2d');
 return {g: g, W: canvas.width, H: canvas.height};
}
function noData(g, W, H, msg){
 g.fillStyle = '#555';
 g.font = '14px sans-serif';
 g.textAlign = 'center';
 g.fillText(msg, W/2, H/2);
}
function ligado(canvasId, k){
 if(!chartState[canvasId]) chartState[canvasId] = {};
 if(chartState[canvasId][k] === undefined) chartState[canvasId][k] = true;
 return chartState[canvasId][k];
}
function alternar(canvasId, k){
 if(!chartState[canvasId]) chartState[canvasId] = {};
 chartState[canvasId][k] = !chartState[canvasId][k];
}
function drawPerfil(){
 console.log('[drawPerfil] Começando. PERFIL:', PERFIL?.status);
 const o = ctx('chPerfil', 300);
 if(!o) return;
 const g = o.g, W = o.W, H = o.H;
 
 if(!PERFIL || PERFIL.status !== 'ok'){
  console.warn('[drawPerfil] Erro:', PERFIL);
  noData(g, W, H, PERFIL?.mensagem || 'Sem dados');
  return;
 }
 
 const faixas = PERFIL.faixas;
 if(!faixas || !faixas.length){
  noData(g, W, H, 'Sem faixas');
  return;
 }
 
 const disponiveis = Object.keys(camposSelecionados).filter(m => faixas.some(f => f[m+'_'+camposSelecionados[m]]));
 
 document.getElementById('lgPerfil').innerHTML = disponiveis.map(function(m){
  const off = !ligado('chPerfil', m);
  const label = LABELS_METAB[m] + ' (' + LABELS_AGREGACAO[camposSelecionados[m]] + ')';
  return '<span class="tog'+(off?' off':'')+'" data-c="chPerfil" data-k="'+m+'" style="cursor:pointer;margin-right:15px;"><i style="display:inline-block;width:10px;height:10px;background:'+CORES_METAB[m]+';margin-right:5px;"></i>'+label+'</span>';
 }).join('');
 document.querySelectorAll('#lgPerfil span.tog').forEach(function(sp){
  sp.onclick = function(){ alternar(sp.dataset.c, sp.dataset.k); drawPerfil(); };
 });
 
 const vis = disponiveis.filter(m => ligado('chPerfil', m));
 if(!vis.length){
  noData(g, W, H, 'Nenhuma métrica');
  return;
 }
 
 const temPace = faixas.some(f => f.pace_medio);
 const PL = 100, PR = 120, PB = 40, PT = temPace ? 46 : 25, w = W - PL - PR, h = H - PT - PB;
 const xs = faixas.map(f => f.watts_centro);
 const xmin = Math.min.apply(null, xs), xmax = Math.max.apply(null, xs);
 const X = v => xmax > xmin ? PL + w*(v-xmin)/(xmax-xmin) : PL + w/2;
 
 function hexRgba(hex, a){
  const h = hex.replace('#', '');
  return 'rgba('+parseInt(h.substring(0,3),16)+','+parseInt(h.substring(3,5),16)+','+parseInt(h.substring(5,7),16)+','+a+')';
 }
 
 const escalas = {};
 vis.forEach(function(m){
  const pts = faixas.filter(f => f[m+'_'+camposSelecionados[m]]);
  let a = Infinity, b = -Infinity;
  pts.forEach(function(f){
   const q = f[m+'_'+camposSelecionados[m]];
   if(q.p10 < a) a = q.p10;
   if(q.p90 > b) b = q.p90;
  });
  if(!isFinite(a)){ a = 0; b = 1; }
  const marg = (b-a)*0.15 || 1;
  a -= marg; b += marg;
  const Y = v => PT + h - (v-a)/(b-a)*h;
  escalas[m] = {a: a, b: b, Y: Y, pts: pts, range_vis: {vmin: a, vmax: b}};
 });
 
 g.strokeStyle = '#21262d';
 g.lineWidth = 1;
 for(let k = 0; k <= 2; k++){
  const y = PT + h*k/2;
  g.beginPath();
  g.moveTo(PL, y);
  g.lineTo(PL+w, y);
  g.stroke();
 }
 
 vis.forEach(function(m){
  const esc = escalas[m];
  const pts = esc.pts;
  const chave = m+'_'+camposSelecionados[m];
  
  g.fillStyle = hexRgba(CORES_METAB[m], 0.08);
  g.beginPath();
  pts.forEach(function(f, j){
   const y = esc.Y(f[chave].p75);
   if(j === 0) g.moveTo(X(f.watts_centro), y);
   else g.lineTo(X(f.watts_centro), y);
  });
  for(let j = pts.length-1; j >= 0; j--){
   g.lineTo(X(pts[j].watts_centro), esc.Y(pts[j][chave].p25));
  }
  g.closePath();
  g.fill();
  
  g.strokeStyle = CORES_METAB[m];
  g.lineWidth = 2.5;
  g.beginPath();
  pts.forEach(function(f, j){
   const y = esc.Y(f[chave].p50);
   if(j === 0) g.moveTo(X(f.watts_centro), y);
   else g.lineTo(X(f.watts_centro), y);
  });
  g.stroke();
  
  g.fillStyle = CORES_METAB[m];
  pts.forEach(function(f){
   g.beginPath();
   g.arc(X(f.watts_centro), esc.Y(f[chave].p50), 3.5, 0, 7);
   g.fill();
  });
 });
 
 g.fillStyle = '#8b949e';
 g.font = '10px sans-serif';
 g.textAlign = 'center';
 faixas.forEach(function(f){
  g.fillText(Math.round(f.watts_centro)+'W', X(f.watts_centro), H-20);
 });
 // FASE A — faixa de pace medido, por cima do eixo dos watts
 if(temPace){
  g.fillStyle = '#FF6B6B';
  g.font = 'bold 10px sans-serif';
  g.textAlign = 'left';
  g.fillText('PACE', 8, 16);
  g.font = '9px sans-serif';
  g.textAlign = 'center';
  const passo = faixas.length > 10 ? 2 : 1;
  faixas.forEach(function(f, i){
   if(i % passo !== 0 || !f.pace_medio) return;
   g.fillText(f.pace_medio, X(f.watts_centro), 16);
  });
 }
 
 g.font = '9px sans-serif';
 g.textAlign = 'right';
 vis.forEach(function(m, idx){
  const esc = escalas[m];
  const cor = CORES_METAB[m];
  for(let k = 0; k <= 2; k++){
   const val = (esc.range_vis.vmax - (esc.range_vis.vmax-esc.range_vis.vmin)*k/2).toFixed(1);
   const y = PT + h*k/2;
   g.fillStyle = cor;
   g.fillText(val, PL - 10 - idx*50, y+3);
  }
 });
 
 const tooltip = document.getElementById('tooltip');
 const canvas = document.getElementById('chPerfil');
 canvas.onmousemove = function(evt){
  const rect = canvas.getBoundingClientRect();
  const mx = evt.clientX - rect.left;
  const my = evt.clientY - rect.top;
  
  if(mx < PL || mx > PL+w || my < PT || my > PT+h){
   tooltip.style.display = 'none';
   return;
  }
  
  const watts = xmin + (mx-PL)/w*(xmax-xmin);
  const faixa = faixas.find(f => Math.abs(f.watts_centro - watts) < 30);
  
  if(faixa){
   let txt = '<b>'+faixa.faixa_watts+'</b><br/>'+faixa.n_intervalos+' int.<br/>';
   if(faixa.pace_medio) txt += '<span style="color:#FF6B6B">Pace: '+faixa.pace_medio+'</span><br/>';
   vis.forEach(function(m){
    const chave = m+'_'+camposSelecionados[m];
    if(faixa[chave]){
     txt += LABELS_METAB[m]+' ('+LABELS_AGREGACAO[camposSelecionados[m]]+'): '+faixa[chave].p50+'<br/>';
    }
   });
   tooltip.innerHTML = txt;
   tooltip.style.left = (evt.clientX + 10) + 'px';
   tooltip.style.top = (evt.clientY + 10) + 'px';
   tooltip.style.display = 'block';
  } else {
   tooltip.style.display = 'none';
  }
 };
}
function drawEvolucao(){
 const o = ctx('chEvolucao', 240);
 if(!o) return;
 const g = o.g, W = o.W, H = o.H;
 
 if(!EVOLUCAO || EVOLUCAO.status !== 'ok'){
  noData(g, W, H, 'Sem dados');
  return;
 }
 
 const periodos = EVOLUCAO.periodos || [];
 if(!periodos.length){
  noData(g, W, H, 'Sem dados');
  return;
 }
 
 const metrica = EVOLUCAO.metrica;
 const cor = CORES_METAB[metrica] || '#999';
 const valores = periodos.map(p => p.p50);
 const vmin = Math.min.apply(null, valores);
 const vmax = Math.max.apply(null, valores);
 const vmarg = (vmax - vmin) * 0.15 || 1;
 const va = vmin - vmarg;
 const vb = vmax + vmarg;
 
 const PL = 70, PR = 80, PB = 30, PT = 20, w = W - PL - PR, h = H - PT - PB;
 const Y = v => PT + h - (v - va)/(vb - va)*h;
 
 g.strokeStyle = '#21262d';
 g.lineWidth = 1;
 for(let k = 0; k <= 2; k++){
  const y = PT + h*k/2;
  g.beginPath();
  g.moveTo(PL, y);
  g.lineTo(PL+w, y);
  g.stroke();
 }
 
 g.fillStyle = 'rgba('+parseInt(cor.substring(1,3),16)+','+parseInt(cor.substring(3,5),16)+','+parseInt(cor.substring(5,7),16)+',0.12)';
 g.beginPath();
 periodos.forEach(function(p, i){
  const x = PL + w*i/(periodos.length-1||1);
  if(i === 0) g.moveTo(x, Y(p.p75));
  else g.lineTo(x, Y(p.p75));
 });
 for(let i = periodos.length-1; i >= 0; i--){
  const x = PL + w*i/(periodos.length-1||1);
  g.lineTo(x, Y(periodos[i].p25));
 }
 g.closePath();
 g.fill();
 
 g.strokeStyle = cor;
 g.lineWidth = 2.5;
 g.beginPath();
 periodos.forEach(function(p, i){
  const x = PL + w*i/(periodos.length-1||1);
  if(i === 0) g.moveTo(x, Y(p.p50));
  else g.lineTo(x, Y(p.p50));
 });
 g.stroke();
 
 g.fillStyle = cor;
 periodos.forEach(function(p, i){
  const x = PL + w*i/(periodos.length-1||1);
  g.beginPath();
  g.arc(x, Y(p.p50), 3, 0, 7);
  g.fill();
 });
 
 g.fillStyle = '#8b949e';
 g.font = '9px sans-serif';
 g.textAlign = 'center';
 const step = Math.max(1, Math.floor(periodos.length / 8));
 periodos.forEach(function(p, i){
  if(i % step !== 0) return;
  g.fillText(p.periodo, PL + w*i/(periodos.length-1||1), H-10);
 });
 
 g.fillStyle = cor;
 g.font = '9px sans-serif';
 g.textAlign = 'right';
 for(let k = 0; k <= 2; k++){
  const val = (vb - (vb-va)*k/2).toFixed(1);
  const y = PT + h*k/2;
  g.fillText(val, PL-5, y+3);
 }
}
async function carregarPerfil(){
 if(isLoadingPerfil) return;
 isLoadingPerfil = true;
 
 const modalidade = document.getElementById('modalidade').value;
 const largura = document.getElementById('larguraBin').value;
 const params = new URLSearchParams();
 params.append('largura_bin', largura);
 Object.entries(camposSelecionados).forEach(([m, a]) => params.append(m, a));
 
 const url = '/api/fisiologia/perfil_robusto/'+modalidade+'?'+params.toString();
 console.log('[carregarPerfil]', url);
 
 try{
  const d = await fetch(url).then(r => r.json());
  console.log('[carregarPerfil] OK:', d);
  PERFIL = d;
  drawPerfil();
 }catch(e){
  console.error('[carregarPerfil] ERRO:', e);
  PERFIL = {status: 'erro', mensagem: e.message};
  drawPerfil();
 }finally{
  isLoadingPerfil = false;
 }
}
async function carregarEvolucao(){
 if(isLoadingEvolucao) return;
 isLoadingEvolucao = true;
 
 const metrica = document.getElementById('metricaEvolucao').value;
 const agregacao = document.getElementById('agregacaoEvolucao').value;
 const modalidade = document.getElementById('modalidade').value;
 const wmin = document.getElementById('wattsMin').value || null;
 const wmax = document.getElementById('wattsMax').value || null;
 const url = '/api/fisiologia/evolucao_robusta?modalidade='+modalidade+'&metrica='+metrica+'&agregacao='+agregacao+(wmin?'&watts_min='+wmin:'')+(wmax?'&watts_max='+wmax:'');
 
 console.log('[carregarEvolucao]', url);
 
 try{
  const d = await fetch(url).then(r => r.json());
  console.log('[carregarEvolucao] OK:', d);
  EVOLUCAO = d;
  drawEvolucao();
 }catch(e){
  console.error('[carregarEvolucao] ERRO:', e);
  EVOLUCAO = {status: 'erro'};
  drawEvolucao();
 }finally{
  isLoadingEvolucao = false;
 }
}
async function load(){
 try{
  const d = await fetch('/api/metabol').then(r => r.json());
  MODALIDADES = d.modalidades || [];
  if(!MODALIDADES.length) return;
  
  const selMod = document.getElementById('modalidade');
  selMod.innerHTML = MODALIDADES.map(m => '<option value="'+m.modalidade+'">'+m.modalidade+' ('+m.n+')</option>').join('');
  selMod.onchange = function(){
   console.log('[selMod.onchange]', this.value);
   carregarPerfil();
   carregarEvolucao();
  };
  
  const agregControls = document.getElementById('agregacaoControls');
  agregControls.innerHTML = METRICAS_BASE.map(m => {
   const aggs = AGREGACOES_VALIDAS[m] || [];
   return '<label class="sel">'+LABELS_METAB[m]+': <select id="agr_'+m+'">'+
   aggs.map(a => '<option value="'+a+'"'+(camposSelecionados[m]===a?' selected':'')+'> '+LABELS_AGREGACAO[a]+'</option>').join('')+
   '</select></label>';
  }).join('');
  
  METRICAS_BASE.forEach(m => {
   const sel = document.getElementById('agr_'+m);
   if(sel){
    sel.onchange = function(){
     console.log('[agr_'+m+'].onchange', this.value);
     camposSelecionados[m] = this.value;
     carregarPerfil();
    };
   }
  });
  
  const selMetricaEvolucao = document.getElementById('metricaEvolucao');
  selMetricaEvolucao.innerHTML = METRICAS_BASE.map(m => '<option value="'+m+'">'+LABELS_METAB[m]+'</option>').join('');
  
  const selAgregacaoEvolucao = document.getElementById('agregacaoEvolucao');
  const primeiraMetrica = METRICAS_BASE[0];
  const primeiraAgregacao = AGREGACOES_VALIDAS[primeiraMetrica]?.[0] || 'avg';
  selAgregacaoEvolucao.innerHTML = (AGREGACOES_VALIDAS[primeiraMetrica] || []).map(a => '<option value="'+a+'">'+LABELS_AGREGACAO[a]+'</option>').join('');
  
  selMetricaEvolucao.onchange = function(){
   const aggs = AGREGACOES_VALIDAS[this.value] || [];
   selAgregacaoEvolucao.innerHTML = aggs.map(a => '<option value="'+a+'">'+LABELS_AGREGACAO[a]+'</option>').join('');
   carregarEvolucao();
  };
  selAgregacaoEvolucao.onchange = carregarEvolucao;
  
  const selBin = document.getElementById('larguraBin');
  selBin.onchange = function(){
   console.log('[selBin.onchange]', this.value);
   carregarPerfil();
  };
  
  carregarPerfil();
  carregarEvolucao();
 }catch(e){
  console.error('[load] ERRO:', e);
 }
}
document.querySelectorAll('.tab-btn').forEach(btn => {
 btn.addEventListener('click', function(){
  const tabName = this.dataset.tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  this.classList.add('active');
  document.getElementById(tabName).classList.add('active');
 });
});
load();
"""
def api_data():
    try:
        modalidades = modalidades_disponiveis()
    except Exception as e:
        return jsonify({'status': 'erro', 'modalidades': []})
    return jsonify({'status': 'ok', 'modalidades': modalidades, 'agregacoes_validas': AGREGACOES_VALIDAS})

def render():
    from flask import render_template_string
    return render_template_string(page(SLUG, 'Metabolismo', BODY, JS))
