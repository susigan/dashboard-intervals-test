"""Pagina de detalhe de uma atividade — streams, NIRS, curvas e custom fields."""

from flask import jsonify
import db
from api_client import (icu_get, norm_tipo, num, kj_da_atividade,
                        kj_do_stream, parse_streams)
from config import STD_FIELDS, ATHLETE_ID
from tabs.base import page

SLUG = 'atividades'


def api_full(activity_id):
    """Atividade + streams + power-vs-hr + curvas + histogramas."""
    act, err = icu_get(f"/activity/{activity_id}")
    if err:
        return jsonify({'error': err}), 502

    custom_fields = {k: v for k, v in act.items() if k not in STD_FIELDS}
    act['_type_norm'] = norm_tipo(act.get('type'))
    act['_kj'] = round(kj_da_atividade(act), 2)

    # Streams: da base de dados se ja foram carregados uma vez (lazy loading).
    streams, stream_meta = db.get_streams(activity_id)
    fonte_streams = 'db'
    if not streams:
        sdata, _ = icu_get(f"/activity/{activity_id}/streams", {"includeDefaults": "true"})
        streams, stream_meta, _w = parse_streams(sdata)
        fonte_streams = 'api'
        if db.ENABLED and streams:
            try:
                db.upsert_streams(activity_id, stream_meta, streams)
            except Exception as e:
                print(f"Nao consegui guardar streams: {e}")
    watts_raw = streams.get('watts') if streams else None

    # kJ recalculado a partir do stream, para validar icu_joules.
    # n_pontos vem dos metadados porque a serie foi reduzida para o grafico.
    dt = num(act.get('icu_median_time_delta'), 1.0) or 1.0
    n_watts = next((m.get('points') for m in (stream_meta or [])
                    if m.get('type') == 'watts'), None)
    kj_stream = kj_do_stream(watts_raw, dt, n_watts)

    pvh, _ = icu_get(f"/activity/{activity_id}/power-vs-hr")
    ivs, _ = icu_get(f"/activity/{activity_id}/intervals")
    pcurve, _ = icu_get(f"/activity/{activity_id}/power-curve")
    phist, _ = icu_get(f"/activity/{activity_id}/power-histogram")
    hhist, _ = icu_get(f"/activity/{activity_id}/hr-histogram")

    if isinstance(pcurve, dict):
        pcurve = {k: pcurve.get(k) for k in
                  ('secs', 'watts', 'values', 'watts_per_kg', 'weight',
                   'vo2max_5m', 'compound_score_5m')}

    icu_kj = num(act.get('icu_joules')) / 1000.0
    return jsonify({
        'status': 'OK',
        'activity': act,
        'custom_fields': custom_fields,
        'streams': streams,
        'stream_meta': stream_meta,
        'streams_fonte': fonte_streams,
        'kj_stream': kj_stream,
        'fonte_streams': fonte_streams,
        'kj_icu_joules': round(icu_kj, 2),
        'kj_delta_pct': (round((kj_stream - icu_kj) / icu_kj * 100, 2)
                         if kj_stream and icu_kj else None),
        'power_vs_hr': pvh or {},
        'intervals': ivs or {},
        'power_curve': pcurve or {},
        'power_histogram': phist or [],
        'hr_histogram': hhist or [],
        'meta': {
            'custom_field_count': len(custom_fields),
            'stream_count': len(stream_meta),
            'has_nirs': any(m['nirs'] for m in stream_meta),
            'null_fields': sorted([k for k, v in act.items() if v is None]),
        },
    })


def api_debug(activity_id):
    """Sonda todos os endpoints disponiveis para esta atividade."""
    out = {'activity_id': activity_id, 'endpoints': {}}
    probes = [
        ('activity',        f"/activity/{activity_id}", {"intervals": "true"}),
        ('streams',         f"/activity/{activity_id}/streams", {"includeDefaults": "true"}),
        ('intervals',       f"/activity/{activity_id}/intervals", None),
        ('power_vs_hr',     f"/activity/{activity_id}/power-vs-hr", None),
        ('power_curve',     f"/activity/{activity_id}/power-curve", None),
        ('hr_curve',        f"/activity/{activity_id}/hr-curve", None),
        ('power_histogram', f"/activity/{activity_id}/power-histogram", None),
        ('hr_histogram',    f"/activity/{activity_id}/hr-histogram", None),
        ('time_at_hr',      f"/activity/{activity_id}/time-at-hr", None),
        ('best_efforts_w',  f"/activity/{activity_id}/best-efforts", {"stream": "watts"}),
        ('best_efforts_hr', f"/activity/{activity_id}/best-efforts", {"stream": "heartrate"}),
        ('interval_stats',  f"/activity/{activity_id}/interval-stats",
                            {"start_index": 0, "end_index": 60}),
        ('weather_summary', f"/activity/{activity_id}/weather-summary", None),
        ('segments',        f"/activity/{activity_id}/segments", None),
        ('hr_load_model',   f"/activity/{activity_id}/hr-load-model", None),
        ('power_spike',     f"/activity/{activity_id}/power-spike-model", None),
    ]
    for name, path, params in probes:
        data, err = icu_get(path, params)
        if err:
            out['endpoints'][name] = {'ok': False, 'error': err}
            continue
        info = {'ok': True, 'type': type(data).__name__}
        if isinstance(data, list):
            info['count'] = len(data)
            if name == 'streams':
                info['streams'] = [{
                    'type': s.get('type'), 'name': s.get('name'),
                    'custom': s.get('custom'), 'allNull': s.get('allNull'),
                    'points': len(s.get('data') or []) if isinstance(s.get('data'), list) else None,
                    'sample': (s.get('data') or [])[:5] if isinstance(s.get('data'), list) else None,
                } for s in data]
            else:
                info['sample'] = data[:3]
        elif isinstance(data, dict):
            info['keys'] = sorted(data.keys())
            info['null_keys'] = sorted([k for k, v in data.items() if v is None])
            if name == 'activity':
                info['custom_fields'] = {k: v for k, v in data.items()
                                         if k not in STD_FIELDS and not isinstance(v, (list, dict))}
                info['standard_fields'] = {k: v for k, v in data.items()
                                           if k in STD_FIELDS and not isinstance(v, (list, dict))}
            else:
                info['sample'] = {k: (str(v)[:120] if isinstance(v, (list, dict)) else v)
                                  for k, v in list(data.items())[:40]}
        out['endpoints'][name] = info
    return jsonify(out)


def api_debug_athlete():
    """Perfil, custom fields e definicoes do atleta."""
    out = {}
    for name, path in [
        ('athlete',        f"/athlete/{ATHLETE_ID}"),
        ('custom_item',    f"/athlete/{ATHLETE_ID}/custom-item"),
        ('activity_tags',  f"/athlete/{ATHLETE_ID}/activity-tags"),
        ('sport_settings', f"/athlete/{ATHLETE_ID}/sport-settings"),
        ('training_plan',  f"/athlete/{ATHLETE_ID}/training-plan"),
    ]:
        data, err = icu_get(path)
        if err:
            out[name] = {'ok': False, 'error': err}
        elif isinstance(data, dict):
            out[name] = {'ok': True, 'keys': sorted(data.keys()),
                         'values': {k: v for k, v in data.items()
                                    if not isinstance(v, (list, dict))}}
        elif name == 'custom_item':
            out[name] = {'ok': True, 'count': len(data), 'items': [{
                'id': it.get('id'), 'name': it.get('name'), 'type': it.get('type'),
                'code': (it.get('content') or {}).get('code'),
                'value_type': (it.get('content') or {}).get('type'),
                'units': (it.get('content') or {}).get('units'),
                'description': it.get('description'),
            } for it in data]}
        else:
            out[name] = {'ok': True, 'count': len(data), 'sample': data[:5]}
    return jsonify(out)


def api_reservas(activity_id):
    """W' e M' balance de uma actividade.

    O W' precisa so' de potencia, CP e W' -- funciona em QUALQUER sessao.
    O M' precisa de SmO2 e so' existe onde houve Moxy.

    O CP e o W' vem do perfil metabolico da modalidade, e ficam em cache
    por modalidade: sao os mesmos para todas as actividades dessa
    modalidade, e ir busca-los a cada actividade aberta tornava a
    navegacao lenta sem necessidade.
    """
    import os as _o
    import sys as _s
    _s.path.insert(0, _o.path.join(
        _o.path.dirname(_o.path.abspath(__file__)), '..', 'utils'))
    _s.path.insert(0, _o.path.join(
        _o.path.dirname(_o.path.abspath(__file__)), 'utils'))
    try:
        import balance as _bal
    except ImportError:
        return jsonify({'status': 'erro',
                        'mensagem': 'utils/balance.py não encontrado'}), 200

    act, err = icu_get(f"/activity/{activity_id}")
    if err:
        return jsonify({'status': 'erro', 'mensagem': err}), 200
    mod = norm_tipo(act.get('type'))

    raw, err2 = icu_get(f"/activity/{activity_id}/streams")
    if err2:
        return jsonify({'status': 'erro', 'mensagem': err2}), 200
    lista = raw
    if isinstance(lista, dict):
        lista = lista.get('streams') or lista.get('content') or []
    streams = {}
    for st in (lista or []):
        if isinstance(st, dict) and (st.get('type') or st.get('name')):
            streams[st.get('type') or st.get('name')] = st.get('data') or []

    def _achar(nomes):
        norm = lambda x: ''.join(c for c in str(x).lower() if c.isalnum())
        alvos = [norm(a) for a in nomes]
        for k in streams:
            if norm(k) in alvos:
                return k
        for k in streams:
            if any(a and a in norm(k) for a in alvos):
                return k
        return None

    kw = _achar(['watts', 'power'])
    ks = _achar(['smo2'])
    n = len(streams.get(kw) or streams.get(ks) or [])
    tempo = streams.get('time') or list(range(n))

    cp, wp = _cp_wp_da_modalidade(mod)
    reservas = {}

    if kw and cp and wp:
        reservas['wprime'] = _bal.wprime_balance(tempo, streams[kw], cp, wp)
    elif kw:
        reservas['wprime'] = {
            'ok': False,
            'motivo': (f'sem CP e W′ para {mod} — corre e grava na tab '
                       'CP-Model')}
    else:
        reservas['wprime'] = {'ok': False, 'motivo': 'sessão sem potência'}

    if ks:
        reservas['mprime'] = {
            'ok': False,
            'motivo': ('o M′ precisa do CER, que só é válido com ensaios de '
                       'durações diferentes até à exaustão. Calcula-o na tab '
                       'Moxy e, se for válido, aparece aqui')}
    else:
        reservas['mprime'] = {'ok': False,
                              'motivo': 'sessão sem SmO2 — não há Moxy'}

    return jsonify({'status': 'ok', 'modalidade': mod,
                    'cp': cp, 'w_prime': wp, 'reservas': reservas,
                    'nota': ("o W′ funciona em qualquer sessão com potência; "
                             "o M′ só onde houve Moxy E o CER for válido")})


# CP e W' por modalidade, calculados uma vez. Sao os mesmos para todas as
# actividades da modalidade -- ir busca-los a cada actividade aberta
# tornava a navegacao lenta sem nada em troca.
_CACHE_CP = {}


def _cp_wp_da_modalidade(mod):
    if mod in _CACHE_CP:
        return _CACHE_CP[mod]
    cp = wp = None
    try:
        from app import perfil_metabolico_dados as _pmd
        pm, _ = _pmd(mod, {}, com_ancoras=False)
        pm = pm or {}
        lim = pm.get('limiares') or {}
        cp = pm.get('cp_w') or lim.get('cp_w')
        wp = pm.get('w_prime_j') or lim.get('w_prime_j')
    except Exception:
        pass
    _CACHE_CP[mod] = (cp, wp)
    return cp, wp


BODY = r"""<a href="/">&larr; Voltar a lista</a>
<h1 id="title">A carregar...</h1>
<div class="sub" id="subtitle"></div>
<div class="cards" id="cards"></div>

<h2>Series temporais</h2>
<div class="toggles" id="toggles"></div>
<div class="controls" style="margin:4px 0;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
  <span class="sub">Suavizacao:</span>
  <input type="range" id="rollSlider" min="0" max="4" step="1" value="0"
         style="width:160px" oninput="setRoll()">
  <span id="rollTxt" class="sub">sem suavizacao</span>
  <span class="sub" style="opacity:.7">| W' e M' nao sao suavizados: ja' sao um integral</span>
</div>
<div class="chartbox">
  <div class="legend" id="legend"></div>
  <canvas id="chart" height="360"></canvas>
</div>

<div id="nirsSection" style="display:none">
<h2>NIRS &middot; SmO<sub>2</sub> / THb</h2>
<div class="toggles" id="nirsToggles"></div>
<div class="controls" style="margin:4px 0;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
  <span class="sub">Suavizacao NIRS:</span>
  <input type="range" id="rollNirs" min="0" max="4" step="1" value="0"
         style="width:160px" oninput="setRollNirs()">
  <span id="rollNirsTxt" class="sub">sem suavizacao</span>
</div>
<div class="chartbox">
  <div class="legend" id="nirsLegend"></div>
  <canvas id="nirs" height="260"></canvas>
</div>
<div class="cards" id="nirsCards"></div>
</div>

<h2>Power vs HR &middot; decoupling</h2>
<div class="chartbox">
  <div class="legend" id="pvhLegend"></div>
  <canvas id="pvh" height="240"></canvas>
</div>

<h2>Curva de potencia</h2>
<div class="chartbox">
  <div class="legend" id="pcLegend"></div>
  <canvas id="pc" height="240"></canvas>
</div>

<h2>Distribuicao</h2>
<div class="grid2">
  <div class="chartbox"><div class="legend"><span><i style="background:#5DADE2"></i>Tempo por potencia</span></div>
    <canvas id="phist" height="200"></canvas></div>
  <div class="chartbox"><div class="legend"><span><i style="background:#E74C3C"></i>Tempo por HR</span></div>
    <canvas id="hhist" height="200"></canvas></div>
</div>

<h2>Tempo em zonas</h2>
<div id="zones"></div>

<h2>Custom fields do atleta</h2>
<div class="sub" id="cfCount"></div>
<div class="kv" id="customkv"></div>

<h2>Streams disponiveis</h2>
<div id="streamPills"></div>

<h2>Intervalos</h2>
<div class="wrap" style="max-height:360px"><table>
  <thead><tr id="ivHead"></tr></thead><tbody id="ivBody"></tbody></table></div>

<h2>Campos standard (Intervals.icu)</h2>
<div class="kv" id="rawkv"></div>

<div class="sub" style="margin-top:24px">
  <a href="/api/activity/__AID__/full" target="_blank">JSON completo</a> &middot;
  <a href="/api/activity/__AID__/debug" target="_blank">Debug de todos os endpoints</a>
</div>"""

JS = r"""
const AID="__AID__";
const COLORS={watts:'#5DADE2',heartrate:'#E74C3C',cadence:'#F4D03F',altitude:'#58D68D',
 velocity_smooth:'#AF7AC5',temp:'#E67E22',Temperature:'#D68910',smo2:'#48C9B0',smo2_2:'#1ABC9C',
 thb:'#EC7063',thb_2:'#CD6155',O2Hb:'#F39C12',HHb:'#9B59B6',DiffHb:'#E59866',torque:'#85929E',
 respiration:'#7FB3D5',dfa_a1:'#F1948A',RRa1:'#82E0AA',distance:'#566573',Speed:'#A569BD',
 RespirationRateAlphaHRV:'#D7BDE2',hrv:'#F5B041',artifacts:'#5D6D7E',
 GarminDistanceperStroke:'#7DCEA0',WorkperStrokeEstimated:'#BB8FCE',
 wprime:'#E3B341',mprime:'#F85149'};
const NIRS=['smo2','thb','O2Hb','HHb','DiffHb'];
let STREAMS={},META=[],ACTIVE={},NACTIVE={},DATA=null;
// STREAMS_ORIG guarda as series como vieram. O rolling e' sempre aplicado
// a partir daqui e nunca em cima do ja' suavizado -- suavizar duas vezes
// tira o pico duas vezes.
let STREAMS_ORIG={};
const ROLL_JANELAS=[0,10,30,60,120];
// As reservas ja' sao um integral do esforco. Suavizar um integral e'
// suavizar duas vezes, e o minimo -- que e' o numero que interessa --
// deixaria de ser o minimo real.
const ROLL_EXCEPCOES=['wprime','mprime'];

function rollingSerie(vs,janela){
 if(!janela||janela<=0) return vs.slice();
 const n=vs.length, metade=Math.floor(janela/2), out=[];
 for(let i=0;i<n;i++){
  const a=Math.max(0,i-metade), b=Math.min(n,i+metade+1);
  let soma=0,c=0;
  for(let k=a;k<b;k++){ const v=vs[k]; if(typeof v==='number'){soma+=v;c++;} }
  out.push(c?soma/c:null);
 }
 return out;
}

// O NIRS tem slider proprio: o SmO2 e' muito mais lento que a potencia, e
// a janela que serve para um raramente serve para o outro. Ligar os dois
// ao mesmo slider obrigava a escolher entre ver o detalhe da potencia ou a
// forma do SmO2.
let ROLL_NIRS = 0;

function setRollNirs(){
 const idx=parseInt(document.getElementById('rollNirs').value,10)||0;
 ROLL_NIRS=ROLL_JANELAS[idx];
 document.getElementById('rollNirsTxt').textContent =
  ROLL_NIRS ? 'media movel de '
    +(ROLL_NIRS<60?ROLL_NIRS+'s':(ROLL_NIRS/60)+'min')+' (centrada)'
    : 'sem suavizacao';
 drawNirs();
}

function setRoll(){
 const idx=parseInt(document.getElementById('rollSlider').value,10)||0;
 const j=ROLL_JANELAS[idx];
 document.getElementById('rollTxt').textContent =
  j ? 'media movel de '+(j<60?j+'s':(j/60)+'min')+' (centrada)'
    : 'sem suavizacao';
 Object.keys(STREAMS_ORIG).forEach(function(k){
  STREAMS[k] = ROLL_EXCEPCOES.indexOf(k)>=0
    ? STREAMS_ORIG[k].slice()
    : rollingSerie(STREAMS_ORIG[k], j);
 });
 drawChart();
 if(typeof drawNirs==='function') drawNirs();
}
function color(k){return COLORS[k]||'#8b949e';}
function metaOf(k){for(var i=0;i<META.length;i++)if(META[i].key===k)return META[i];return {key:k,label:k,type:k};}


function drawSeries(canvasId,height,keys){
 const o=ctx(canvasId,height),g=o.g,W=o.W,H=o.H;
 const PL=46,PR=46,PT=10,PB=26,w=W-PL-PR,h=H-PT-PB;
 keys=keys.filter(k=>STREAMS[k]&&STREAMS[k].length);
 if(!keys.length){noData(g,W,H,'Seleciona pelo menos uma serie');return;}
 const n=Math.max.apply(null,keys.map(k=>STREAMS[k].length));
 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 keys.forEach(function(k,idx){
  const s=STREAMS[k],vals=s.filter(v=>typeof v==='number');
  if(!vals.length)return;
  let mn=Math.min.apply(null,vals),mx=Math.max.apply(null,vals);
  if(mx===mn)mx=mn+1;
  g.strokeStyle=color(k);g.lineWidth=1.3;g.beginPath();let st=false;
  for(let i=0;i<s.length;i++){const v=s[i];if(typeof v!=='number'){st=false;continue;}
   const x=PL+w*i/(n-1),y=PT+h-(v-mn)/(mx-mn)*h;
   if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);}
  g.stroke();
  if(idx<2){const right=idx===1;g.fillStyle=color(k);g.font='10px sans-serif';
   g.textAlign=right?'left':'right';
   for(let i=0;i<=4;i++){const val=mx-(mx-mn)*i/4,y=PT+h*i/4;
    g.fillText(Math.round(val*10)/10,right?PL+w+6:PL-6,y+3);}
   g.textAlign='left';}
 });
 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='center';
 const el=window.__ELAPSED__||0;
 for(let i=0;i<=6;i++){const x=PL+w*i/6;g.fillText(Math.round(i/6*el/60)+'m',x,H-8);}
 g.textAlign='left';
}
function drawChart(){drawSeries('chart',360,Object.keys(ACTIVE).filter(k=>ACTIVE[k]));}
function drawNirs(){
 const keys=Object.keys(NACTIVE).filter(k=>NACTIVE[k]);
 if(!keys.length){const o=ctx('nirs',260);noData(o.g,o.W,o.H,'Sem canais NIRS selecionados');return;}
 // aplica a janela do NIRS sobre os ORIGINAIS, sem mexer no STREAMS que o
 // grafico principal usa -- os dois sliders sao independentes
 const guardado={};
 keys.forEach(function(k){
  if(!STREAMS_ORIG[k]) return;
  guardado[k]=STREAMS[k];
  STREAMS[k] = ROLL_EXCEPCOES.indexOf(k)>=0
    ? STREAMS_ORIG[k].slice()
    : rollingSerie(STREAMS_ORIG[k], ROLL_NIRS);
 });
 drawSeries('nirs',260,keys);
 Object.keys(guardado).forEach(function(k){ STREAMS[k]=guardado[k]; });
}

function drawPvH(pvh){
 const o=ctx('pvh',240),g=o.g,W=o.W,H=o.H;
 const series=(pvh&&pvh.series)||[];
 if(!series.length){noData(g,W,H,'Sem power vs HR');return;}
 const PL=46,PR=46,PT=10,PB=26,w=W-PL-PR,h=H-PT-PB;
 const P=series.map(b=>b.watts),Hr=series.map(b=>b.hr);
 const pv=P.filter(v=>typeof v==='number'),hv=Hr.filter(v=>typeof v==='number');
 if(!pv.length||!hv.length){noData(g,W,H,'Sem power vs HR');return;}
 const pmn=Math.min.apply(null,pv),pmx=Math.max.apply(null,pv);
 const hmn=Math.min.apply(null,hv),hmx=Math.max.apply(null,hv);
 g.strokeStyle='#21262d';
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 function line(arr,mn,mx,col){g.strokeStyle=col;g.lineWidth=1.6;g.beginPath();let st=false;
  for(let i=0;i<arr.length;i++){const v=arr[i];if(typeof v!=='number'){st=false;continue;}
   const x=PL+w*i/(arr.length-1),y=PT+h-(v-mn)/((mx-mn)||1)*h;
   if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);}g.stroke();}
 line(P,pmn,pmx,'#5DADE2');line(Hr,hmn,hmx,'#E74C3C');
 g.font='10px sans-serif';g.textAlign='right';g.fillStyle='#5DADE2';
 for(let i=0;i<=4;i++)g.fillText(Math.round(pmx-(pmx-pmn)*i/4),PL-6,PT+h*i/4+3);
 g.textAlign='left';g.fillStyle='#E74C3C';
 for(let i=0;i<=4;i++)g.fillText(Math.round(hmx-(hmx-hmn)*i/4),PL+w+6,PT+h*i/4+3);
}

function drawPowerCurve(pc){
 const o=ctx('pc',240),g=o.g,W=o.W,H=o.H;
 const secs=pc&&pc.secs,watts=pc&&(pc.watts||pc.values);
 if(!secs||!watts||!secs.length){noData(g,W,H,'Sem curva de potencia');return;}
 const PL=46,PR=20,PT=10,PB=26,w=W-PL-PR,h=H-PT-PB;
 const lmin=Math.log10(Math.max(1,secs[0])),lmax=Math.log10(secs[secs.length-1]);
 const wv=watts.filter(v=>typeof v==='number');
 const mx=Math.max.apply(null,wv),mn=0;
 g.strokeStyle='#21262d';
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 g.strokeStyle='#5DADE2';g.lineWidth=1.6;g.beginPath();let st=false;
 for(let i=0;i<secs.length;i++){const v=watts[i];if(typeof v!=='number'){st=false;continue;}
  const x=PL+w*(Math.log10(Math.max(1,secs[i]))-lmin)/(lmax-lmin||1),y=PT+h-(v-mn)/(mx-mn||1)*h;
  if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);}
 g.stroke();
 g.fillStyle='#5DADE2';g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++)g.fillText(Math.round(mx-(mx-mn)*i/4)+'W',PL-6,PT+h*i/4+3);
 g.fillStyle='#8b949e';g.textAlign='center';
 [1,5,15,60,300,1200,3600].forEach(function(s){
  if(s<secs[0]||s>secs[secs.length-1])return;
  const x=PL+w*(Math.log10(s)-lmin)/(lmax-lmin||1);
  g.fillText(s<60?s+'s':(s/60)+'m',x,H-8);
  g.strokeStyle='#21262d';g.beginPath();g.moveTo(x,PT);g.lineTo(x,PT+h);g.stroke();});
 g.textAlign='left';
}

function drawHist(id,bins,col,unit){
 const o=ctx(id,200),g=o.g,W=o.W,H=o.H;
 if(!bins||!bins.length){noData(g,W,H,'Sem histograma');return;}
 const PL=44,PR=12,PT=10,PB=26,w=W-PL-PR,h=H-PT-PB;
 const mx=Math.max.apply(null,bins.map(b=>b.secs||0));
 const bw=w/bins.length;
 bins.forEach(function(b,i){
  const bh=h*(b.secs||0)/(mx||1);
  g.fillStyle=col;g.globalAlpha=.75;
  g.fillRect(PL+i*bw+1,PT+h-bh,bw-2,bh);g.globalAlpha=1;});
 g.fillStyle='#8b949e';g.font='9px sans-serif';g.textAlign='center';
 bins.forEach(function(b,i){ if(i%Math.ceil(bins.length/8)!==0)return;
  g.fillText(b.min,PL+i*bw+bw/2,H-8);});
 g.textAlign='right';
 for(let i=0;i<=3;i++)g.fillText(Math.round((mx-mx*i/3)/60)+'m',PL-6,PT+h*i/3+3);
 g.textAlign='left';
}

function drawZones(a){
 const el=document.getElementById('zones');
 let html='';
 const PZ=['#58D68D','#5DADE2','#F4D03F','#E67E22','#E74C3C','#C0392B','#8E44AD'];
 function bar(title,items){
  const tot=items.reduce((s,x)=>s+x.secs,0)||1;
  return '<h3>'+title+'</h3><div class="zbar">'+items.map(function(x,i){
   const pct=x.secs/tot*100;
   return '<div style="width:'+pct+'%;background:'+PZ[i%PZ.length]+'" title="'+x.label+'">'+
    (pct>6?Math.round(pct)+'%':'')+'</div>';}).join('')+'</div>'+
   '<div class="sub" style="margin:0">'+items.map(x=>x.label+' '+Math.round(x.secs/60)+'m').join(' · ')+'</div>';
 }
 if(Array.isArray(a.icu_zone_times)&&a.icu_zone_times.length)
  html+=bar('Potencia',a.icu_zone_times.map((z,i)=>({label:z.id||('Z'+(i+1)),secs:z.secs||z.seconds||0})));
 if(Array.isArray(a.icu_hr_zone_times)&&a.icu_hr_zone_times.length)
  html+=bar('Frequencia cardiaca',a.icu_hr_zone_times.map((s,i)=>({label:'Z'+(i+1),secs:s||0})));
 el.innerHTML=html||'<div class="sub">Sem dados de zonas</div>';
}

function fmtv(v){
 if(v===null||v===undefined)return '<span style="color:#484f58">null</span>';
 if(typeof v==='object')return '<span style="color:#8b949e">'+JSON.stringify(v).slice(0,70)+'</span>';
 if(typeof v==='number')return (Math.round(v*1000)/1000).toLocaleString('pt-PT');
 return String(v);
}

async function load(){
 const d=await fetch('/api/activity/'+AID+'/full').then(r=>r.json());
 if(d.error){document.getElementById('title').textContent='Erro: '+d.error;return;}
 DATA=d;
 const a=d.activity||{},cf=d.custom_fields||{};
 window.__ELAPSED__=a.elapsed_time||a.moving_time||0;
 document.getElementById('title').textContent=a.name||AID;
 document.getElementById('subtitle').textContent=
  (a.start_date_local||'')+'  ·  '+(a.type||'')+'  ·  '+(a.device_name||a.source||'');

 const cards=[['Duracao',Math.round((a.elapsed_time||0)/60)+' min'],
  ['Distancia',((a.icu_distance||a.distance||0)/1000).toFixed(1)+' km'],
  ['TL',a.icu_training_load],['NP',(a.icu_weighted_avg_watts||0)+' W'],
  ['FTP',(a.icu_pm_ftp||a.icu_ftp||0)+' W'],
  ['IF',a.icu_intensity!=null?(a.icu_intensity>3?a.icu_intensity.toFixed(0)+'%':a.icu_intensity.toFixed(2)):'-'],
  ['HR med',(a.average_heartrate||0)+' bpm'],['HR max',(a.max_heartrate||0)+' bpm'],
  ['kJ',Math.round((a.icu_joules||0)/1000)],
  ['kJ do stream',d.kj_stream!=null?Math.round(d.kj_stream):'-'],['W prime',a.icu_pm_w_prime||0],
  ['W bal min',a.icu_max_wbal_depletion||0],
  ['Decoupling',(a.decoupling!=null?a.decoupling.toFixed(2):'-')+'%'],
  ['PI',a.polarization_index!=null?a.polarization_index.toFixed(2):'-'],
  ['VI',a.icu_variability_index!=null?a.icu_variability_index.toFixed(2):'-'],
  ['EF',a.icu_efficiency_factor!=null?a.icu_efficiency_factor.toFixed(2):'-'],
  ['kJ > FTP',Math.round((a.icu_joules_above_ftp||0)/1000)]];
 document.getElementById('cards').innerHTML=cards.map(c=>
  '<div class="card"><div class="label">'+c[0]+'</div><div class="value">'+(c[1]==null?'-':c[1])+'</div></div>').join('');

 STREAMS=d.streams||{};
 META=d.stream_meta||[];
 // reservas W' e M': o W' precisa so' de potencia, CP e W'; o M' precisa
 // de SmO2 e so' existe nas sessoes com Moxy
 try{
  const rb=await fetch('/api/activity/'+AID+'/reservas').then(r=>r.json());
  if(rb && rb.status==='ok'){
   ['wprime','mprime'].forEach(function(k){
    const r=(rb.reservas||{})[k];
    if(r && r.ok && (r.serie||[]).length){
     STREAMS[k]=r.serie;
     META.push({key:k, label:k, type:k, plotted:true,
                sensor_name:(k==='wprime'?"W' restante (J)":"M' restante"),
                points:r.serie.length});
    }
   });
   window.__RESERVAS__=rb.reservas||{};
  }
 }catch(e){ window.__RESERVAS_ERRO__=String(e); }
 STREAMS_ORIG={};
 Object.keys(STREAMS).forEach(k=>{STREAMS_ORIG[k]=STREAMS[k].slice();});
 const names=Object.keys(STREAMS);
 const nirsKeys=META.filter(m=>NIRS.indexOf(m.type)!==-1&&m.plotted).map(m=>m.key);
 // Os canais NIRS deixam de ser excluídos do gráfico principal: têm
 // secção própria, mas poder pô-los ao lado dos watts e da FC é
 // precisamente o que permite ver a resposta ao esforço no mesmo eixo
 // de tempo. Continuam a aparecer também na secção NIRS.
 const mainKeys=names.slice();

 document.getElementById('streamPills').innerHTML=META.map(function(m){
  const t=m.sensor_name?(m.type+' - '+m.sensor_name):m.key;
  return '<span class="pill'+(m.custom?' custom':'')+'"'+(m.plotted?'':' style="opacity:.45"')+
   ' title="'+(m.points||0)+' pontos">'+t+'</span>';}).join('')||'<span class="sub">nenhum</span>';

 // grafico principal
 mainKeys.forEach(k=>{ACTIVE[k]=(k==='watts'||k==='heartrate');});
 document.getElementById('toggles').innerHTML=mainKeys.map(function(k){
  const m=metaOf(k);
  return '<label class="'+(m.custom?'custom':'')+'"><input type="checkbox" data-k="'+k+'" '+
   (ACTIVE[k]?'checked':'')+'> '+(m.sensor_name||k)+'</label>';}).join('');
 document.querySelectorAll('#toggles input').forEach(cb=>cb.onchange=function(){
  ACTIVE[cb.dataset.k]=cb.checked;updLegend();drawChart();});
 function updLegend(){document.getElementById('legend').innerHTML=mainKeys.filter(k=>ACTIVE[k])
  .map(k=>'<span><i style="background:'+color(k)+'"></i>'+(metaOf(k).sensor_name||k)+'</span>').join('');}
 updLegend();drawChart();

 // grafico NIRS
 if(nirsKeys.length){
  document.getElementById('nirsSection').style.display='';
  nirsKeys.forEach(k=>{NACTIVE[k]=(metaOf(k).type==='smo2'||metaOf(k).type==='thb');});
  document.getElementById('nirsToggles').innerHTML=nirsKeys.map(function(k){
   const m=metaOf(k);
   return '<label class="'+(m.custom?'custom':'')+'"><input type="checkbox" data-k="'+k+'" '+
    (NACTIVE[k]?'checked':'')+'> '+(m.sensor_name||k)+'</label>';}).join('');
  document.querySelectorAll('#nirsToggles input').forEach(cb=>cb.onchange=function(){
   NACTIVE[cb.dataset.k]=cb.checked;updNirsLegend();drawNirs();});
  function updNirsLegend(){document.getElementById('nirsLegend').innerHTML=nirsKeys.filter(k=>NACTIVE[k])
   .map(k=>'<span><i style="background:'+color(k)+'"></i>'+(metaOf(k).sensor_name||k)+'</span>').join('');}
  updNirsLegend();drawNirs();
  // estatisticas por canal
  document.getElementById('nirsCards').innerHTML=nirsKeys.map(function(k){
   const v=STREAMS[k].filter(x=>typeof x==='number');
   if(!v.length)return '';
   const mn=Math.min.apply(null,v),mx=Math.max.apply(null,v);
   const avg=v.reduce((s,x)=>s+x,0)/v.length;
   const m=metaOf(k);
   return '<div class="card"><div class="label">'+(m.sensor_name||k)+'</div>'+
    '<div class="value">'+avg.toFixed(1)+'</div>'+
    '<div class="label" style="margin-top:4px">min '+mn.toFixed(1)+' · max '+mx.toFixed(1)+
    ' · amp '+(mx-mn).toFixed(1)+'</div></div>';}).join('');
 }

 const pvh=d.power_vs_hr||{};
 document.getElementById('pvhLegend').innerHTML=
  '<span><i style="background:#5DADE2"></i>Power</span><span><i style="background:#E74C3C"></i>HR</span>'+
  (pvh.decoupling!=null?'<span>Decoupling '+pvh.decoupling.toFixed(2)+'%</span>':'')+
  (pvh.powerHr!=null?'<span>Power/HR '+pvh.powerHr.toFixed(3)+'</span>':'')+
  (pvh.powerHrFirst!=null?'<span>1a metade '+pvh.powerHrFirst.toFixed(3)+'</span>':'')+
  (pvh.powerHrSecond!=null?'<span>2a metade '+pvh.powerHrSecond.toFixed(3)+'</span>':'')+
  (pvh.hrLag!=null?'<span>HR lag '+pvh.hrLag+'s</span>':'');
 drawPvH(pvh);

 const pc=d.power_curve||{};
 document.getElementById('pcLegend').innerHTML='<span><i style="background:#5DADE2"></i>MMP (escala log)</span>'+
  (pc.vo2max_5m!=null?'<span>VO2max 5m '+pc.vo2max_5m.toFixed(1)+'</span>':'')+
  (pc.compound_score_5m!=null?'<span>Compound score '+Math.round(pc.compound_score_5m)+'</span>':'');
 drawPowerCurve(pc);

 drawHist('phist',d.power_histogram,'#5DADE2');
 drawHist('hhist',d.hr_histogram,'#E74C3C');
 drawZones(a);

 const cfKeys=Object.keys(cf).sort();
 document.getElementById('cfCount').textContent=cfKeys.length+' custom fields definidos por ti';
 document.getElementById('customkv').innerHTML=cfKeys.map(k=>
  '<div><span class="k">'+k+'</span><span class="v cf">'+fmtv(cf[k])+'</span></div>').join('');

 const ivs=(d.intervals&&(d.intervals.icu_intervals||d.intervals))||[];
 if(Array.isArray(ivs)&&ivs.length){
  const cols=['label','type','start_time','elapsed_time','distance','average_watts','max_watts',
   'weighted_average_watts','average_heartrate','max_heartrate','average_cadence','intensity','joules','decoupling'];
  document.getElementById('ivHead').innerHTML=cols.map(c=>'<th>'+c+'</th>').join('');
  document.getElementById('ivBody').innerHTML=ivs.map(iv=>'<tr>'+cols.map(function(c){
   var v=iv[c];return '<td class="num">'+(v==null?'-':(typeof v==='number'?Math.round(v*10)/10:v))+'</td>';
  }).join('')+'</tr>').join('');
 } else document.getElementById('ivBody').innerHTML='<tr><td class="loading">Sem intervalos</td></tr>';

 document.getElementById('rawkv').innerHTML=Object.keys(a).sort()
  .filter(k=>!(k in cf)).map(k=>'<div><span class="k">'+k+'</span><span class="v">'+fmtv(a[k])+'</span></div>').join('');
}
window.addEventListener('resize',function(){
 if(!DATA)return;drawChart();
 if(Object.keys(NACTIVE).length)drawNirs();
 drawPvH(DATA.power_vs_hr||{});drawPowerCurve(DATA.power_curve||{});
 drawHist('phist',DATA.power_histogram,'#5DADE2');drawHist('hhist',DATA.hr_histogram,'#E74C3C');});
load();
"""


def render(activity_id):
    return page(f'Atividade {activity_id}', SLUG,
                BODY.replace('__AID__', activity_id),
                JS.replace('__AID__', activity_id))
