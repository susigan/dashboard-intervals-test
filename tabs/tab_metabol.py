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

AGREGACOES = ['min', 'avg', 'max']
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
  <button class="tab-btn" data-tab="aquecimento">Aquecimento</button>
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
<div id="aquecimento" class="tab-content" style="display:none;">
  <div class="tabs" id="aqModTabs" style="border-bottom:1px solid #21262d; margin-bottom:16px;"></div>
  <div class="controls">
    <label class="sel">Métrica
      <select id="aqMetrica">
        <option value="hr">HR (bpm)</option>
        <option value="smo2">SmO&#8322; (%)</option>
        <option value="resp">Respiração (rpm)</option>
        <option value="dfa1">DFA-&#945;1</option>
      </select></label>
    <label class="sel">Agregação
      <select id="aqAgregacao">
        <option value="avg" selected>Méd</option>
        <option value="min">Mín</option>
        <option value="max">Máx</option>
      </select></label>
    <label class="sel">Rolling (sessões)
      <select id="aqRolling"></select></label>
    <label class="sel" style="cursor:pointer;">
      <input type="checkbox" id="aqMDC" checked> Banda MDC&#8329;&#8325;</label>
    <label class="sel" style="cursor:pointer;">
      <input type="checkbox" id="aqTrend" checked> Tendência</label>
  </div>
  <h2 id="aqTitulo">Aquecimento por escalão de watts</h2>
  <div class="legend" id="aqLegenda"></div>
  <div class="chartbox"><canvas id="chAquecimento" height="320"></canvas></div>
  <h2>Fiabilidade por escalão</h2>
  <div id="aqTabela" style="overflow-x:auto;"></div>
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
  if(tabName === 'aquecimento'){ aqDraw(); aqTabela(); }
 });
});
// ═════ AQUECIMENTO ═════
let AQ_MOD = null, AQ_DADOS = null, AQ_MODS = [], aqLoading = false;
const AQ_LABELS = {hr:'HR (bpm)', smo2:'SmO\u2082 (%)', resp:'Respira\u00e7\u00e3o (rpm)', dfa1:'DFA-\u03b11'};
const AQ_CORES_W = ['#58A6FF','#3FB950','#F0883E','#DB6D28','#F85149'];

function aqInit(){
 const sel = document.getElementById('aqRolling');
 if(sel && !sel.options.length){
  for(let i=1;i<=12;i++){
   const o = document.createElement('option');
   o.value = i; o.textContent = (i===1 ? 'sem' : i);
   sel.appendChild(o);
  }
 }
 ['aqMetrica','aqAgregacao','aqRolling'].forEach(function(id){
  const el = document.getElementById(id);
  if(el) el.addEventListener('change', aqCarregar);
 });
 ['aqMDC','aqTrend'].forEach(function(id){
  const el = document.getElementById(id);
  if(el) el.addEventListener('change', aqDraw);
 });
 fetch('/api/aquecimento/estado').then(r=>r.json()).then(function(d){
  const box = document.getElementById('aqModTabs');
  if(!box) return;
  AQ_MODS = (d.modalidades||[]).filter(m=>m.modalidade);
  if(!AQ_MODS.length){
   box.innerHTML = '<span style="color:#8b949e;padding:10px 0;">Nenhum aquecimento detectado. Corre /api/aquecimento/calibrar?modalidade=Row</span>';
   return;
  }
  box.innerHTML = '';
  AQ_MODS.forEach(function(m, i){
   const b = document.createElement('button');
   b.className = 'tab-btn' + (i===0 ? ' active' : '');
   b.textContent = m.modalidade + ' (' + m.n_sessoes + ')';
   b.onclick = function(){
    box.querySelectorAll('.tab-btn').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    AQ_MOD = m.modalidade;
    aqCarregar();
   };
   box.appendChild(b);
  });
  AQ_MOD = AQ_MODS[0].modalidade;
  aqCarregar();
 }).catch(function(e){ console.error('[aqInit]', e); });
}

function aqCarregar(){
 if(!AQ_MOD || aqLoading) return;
 aqLoading = true;
 const met = document.getElementById('aqMetrica').value;
 const agr = document.getElementById('aqAgregacao').value;
 const rol = document.getElementById('aqRolling').value;
 let url = '/api/aquecimento/serie?modalidade='+AQ_MOD+'&metrica='+met+'&agregacao='+agr;
 if(rol && rol !== '1') url += '&rolling='+rol;
 fetch(url).then(r=>r.json()).then(function(d){
  AQ_DADOS = d;
  document.getElementById('aqTitulo').textContent =
    AQ_LABELS[met] + ' \u2014 ' + AQ_MOD + ' por escal\u00e3o de watts';
  aqDraw(); aqTabela();
 }).catch(function(e){
  console.error('[aqCarregar]', e);
  AQ_DADOS = {status:'erro'}; aqDraw();
 }).finally(function(){ aqLoading = false; });
}

function aqRegressao(ys){
 const n = ys.length;
 if(n < 2) return null;
 let sx=0, sy=0, sxy=0, sxx=0;
 for(let i=0;i<n;i++){ sx+=i; sy+=ys[i]; sxy+=i*ys[i]; sxx+=i*i; }
 const den = n*sxx - sx*sx;
 if(!den) return null;
 const m = (n*sxy - sx*sy)/den;
 return {m:m, b:(sy - m*sx)/n};
}

function aqDraw(){
 const o = ctx('chAquecimento', 320);
 if(!o) return;
 const g = o.g, W = o.W, H = o.H;
 if(!AQ_DADOS || AQ_DADOS.status !== 'ok' || !(AQ_DADOS.series||[]).length){
  noData(g, W, H, AQ_DADOS && AQ_DADOS.status === 'sem_dados'
    ? 'Nenhum aquecimento detectado nesta modalidade' : 'Sem dados');
  document.getElementById('aqLegenda').innerHTML = '';
  return;
 }
 const series = AQ_DADOS.series.filter(s => s.valores && s.valores.length);
 if(!series.length){ noData(g, W, H, 'Sem dados'); return; }

 const mostrarMDC = document.getElementById('aqMDC').checked;
 const mostrarTrend = document.getElementById('aqTrend').checked;

 let vmin = Infinity, vmax = -Infinity, nmax = 0;
 series.forEach(function(s){
  s.valores.forEach(function(v){ if(v<vmin) vmin=v; if(v>vmax) vmax=v; });
  if(s.valores.length > nmax) nmax = s.valores.length;
  const mdc = s.reliability && s.reliability.mdc95;
  if(mostrarMDC && mdc){
   const ult = s.valores[s.valores.length-1];
   if(ult-mdc < vmin) vmin = ult-mdc;
   if(ult+mdc > vmax) vmax = ult+mdc;
  }
 });
 const marg = (vmax-vmin)*0.15 || 1;
 const va = vmin-marg, vb = vmax+marg;
 const PL=70, PR=90, PB=34, PT=16, w=W-PL-PR, h=H-PT-PB;
 const Y = v => PT + h - (v-va)/(vb-va)*h;
 const X = i => PL + w*i/((nmax-1)||1);

 g.strokeStyle = '#21262d'; g.lineWidth = 1;
 g.fillStyle = '#8b949e'; g.font = '11px sans-serif'; g.textAlign = 'right';
 for(let k=0;k<=4;k++){
  const y = PT + h*k/4;
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.fillText((vb - (vb-va)*k/4).toFixed(1), PL-8, y+4);
 }

 series.forEach(function(s, si){
  const cor = AQ_CORES_W[si % AQ_CORES_W.length];
  const vals = s.valores;
  const rel = s.reliability || {};

  // banda MDC em torno da ultima observacao: fora dela = mudanca real
  if(mostrarMDC && rel.mdc95){
   const ult = vals[vals.length-1];
   const r = parseInt(cor.substring(1,3),16), gg = parseInt(cor.substring(3,5),16), bb = parseInt(cor.substring(5,7),16);
   g.fillStyle = 'rgba('+r+','+gg+','+bb+',' + (rel.fiavel ? 0.10 : 0.05) + ')';
   g.fillRect(PL, Y(ult+rel.mdc95), w, Y(ult-rel.mdc95)-Y(ult+rel.mdc95));
   g.strokeStyle = 'rgba('+r+','+gg+','+bb+',0.35)';
   g.setLineDash([3,3]); g.lineWidth = 1;
   [ult+rel.mdc95, ult-rel.mdc95].forEach(function(v){
    g.beginPath(); g.moveTo(PL, Y(v)); g.lineTo(PL+w, Y(v)); g.stroke();
   });
   g.setLineDash([]);
  }

  g.strokeStyle = cor; g.lineWidth = 2; g.beginPath();
  vals.forEach(function(v,i){ i ? g.lineTo(X(i),Y(v)) : g.moveTo(X(i),Y(v)); });
  g.stroke();

  g.fillStyle = cor;
  vals.forEach(function(v,i){ g.beginPath(); g.arc(X(i),Y(v),2.5,0,6.2832); g.fill(); });

  if(mostrarTrend){
   const reg = aqRegressao(vals);
   if(reg){
    g.strokeStyle = cor; g.lineWidth = 1.5; g.setLineDash([6,4]);
    g.beginPath();
    g.moveTo(X(0), Y(reg.b));
    g.lineTo(X(vals.length-1), Y(reg.m*(vals.length-1)+reg.b));
    g.stroke(); g.setLineDash([]);
   }
  }

  g.fillStyle = cor; g.font = '11px sans-serif'; g.textAlign = 'left';
  g.fillText(s.watts_alvo + 'W', PL+w+8, Y(vals[vals.length-1])+4);
 });

 // datas nos extremos
 const s0 = series[0];
 if(s0.datas && s0.datas.length){
  g.fillStyle = '#8b949e'; g.font = '10px sans-serif';
  g.textAlign = 'left';  g.fillText(s0.datas[0], PL, H-12);
  g.textAlign = 'right'; g.fillText(s0.datas[s0.datas.length-1], PL+w, H-12);
 }

 document.getElementById('aqLegenda').innerHTML = series.map(function(s, si){
  const cor = AQ_CORES_W[si % AQ_CORES_W.length];
  return '<span style="margin-right:16px;color:'+cor+';">\u25CF ' + s.watts_alvo + 'W (n=' + s.n + ')</span>';
 }).join('');
}

function aqTabela(){
 const box = document.getElementById('aqTabela');
 if(!box) return;
 if(!AQ_DADOS || AQ_DADOS.status !== 'ok'){ box.innerHTML = ''; return; }
 let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
  + '<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
  + '<th style="padding:6px;">Watts</th><th>n sess\u00f5es</th><th>Primeiro</th><th>\u00daltimo</th>'
  + '<th>\u0394</th><th>SEM</th><th>MDC\u2089\u2085</th><th>Interpreta\u00e7\u00e3o</th></tr>';
 AQ_DADOS.series.forEach(function(s){
  const v = s.valores || [];
  if(!v.length) return;
  const ini = v[0], fim = v[v.length-1], d = fim - ini;
  const rel = s.reliability || {};
  let interp, cor;
  if(rel.mdc95 == null){
   interp = rel.nota || 'sem SEM'; cor = '#8b949e';
  } else if(Math.abs(d) >= rel.mdc95){
   interp = 'mudan\u00e7a real (> MDC)'; cor = d > 0 ? '#3FB950' : '#F85149';
  } else {
   interp = 'dentro do ru\u00eddo'; cor = '#8b949e';
  }
  if(rel.mdc95 != null && !rel.fiavel) interp += ' \u26A0';
  html += '<tr style="border-bottom:1px solid #161b22;">'
   + '<td style="padding:6px;">' + s.watts_alvo + 'W</td>'
   + '<td>' + s.n + '</td>'
   + '<td>' + ini.toFixed(1) + '</td>'
   + '<td>' + fim.toFixed(1) + '</td>'
   + '<td style="color:' + cor + ';">' + (d>=0?'+':'') + d.toFixed(1) + '</td>'
   + '<td>' + (rel.sem != null ? rel.sem.toFixed(2) : '\u2014') + '</td>'
   + '<td>' + (rel.mdc95 != null ? rel.mdc95.toFixed(2) : '\u2014') + '</td>'
   + '<td style="color:' + cor + ';">' + interp + '</td></tr>';
 });
 html += '</table>'
  + '<p style="color:#8b949e;font-size:11px;margin-top:8px;">'
  + 'SEM estimado a partir de sess\u00f5es separadas por \u226410 dias (ru\u00eddo de medi\u00e7\u00e3o, '
  + 'n\u00e3o adapta\u00e7\u00e3o). MDC\u2089\u2085 = SEM \u00d7 1,96 \u00d7 \u221a2. '
  + '\u26A0 = menos de 10 pares, banda indicativa.</p>';
 box.innerHTML = html;
}

aqInit();
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
