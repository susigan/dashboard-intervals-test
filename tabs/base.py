"""CSS, navegacao e helpers de layout partilhados por todas as tabs."""

CSS = r"""
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:#0e1117; color:#e6e6e6;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
h1 { font-size:22px; margin:0 0 4px; font-weight:600; }
h2 { font-size:15px; margin:30px 0 10px; font-weight:600; color:#c9d1d9;
  border-bottom:1px solid #21262d; padding-bottom:6px; }
h3 { font-size:12px; margin:14px 0 6px; font-weight:600; color:#8b949e;
  text-transform:uppercase; letter-spacing:.6px; }
.sub { color:#8b949e; font-size:13px; margin-bottom:20px; }
.nav { display:flex; gap:4px; margin-bottom:18px; border-bottom:1px solid #21262d;
  flex-wrap:wrap; }
.nav a { padding:9px 16px; font-size:13px; color:#8b949e; border-bottom:2px solid transparent;
  text-decoration:none; font-weight:500; }
.nav a:hover { color:#c9d1d9; text-decoration:none; }
.nav a.on { color:#5DADE2; border-bottom-color:#5DADE2; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:20px; }
.card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px 14px; }
.card .label { font-size:10px; text-transform:uppercase; letter-spacing:.5px; color:#8b949e; }
.card .value { font-size:21px; font-weight:600; color:#5DADE2; margin-top:3px; }
.controls { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:14px; align-items:center; }
input, select { background:#161b22; border:1px solid #30363d; color:#e6e6e6;
  padding:8px 10px; border-radius:6px; font-size:13px; }
input { min-width:220px; }
label.sel { font-size:12px; color:#8b949e; display:flex; align-items:center; gap:6px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; padding:9px 8px; background:#161b22; border-bottom:1px solid #30363d;
  color:#8b949e; font-weight:500; font-size:11px; text-transform:uppercase;
  cursor:pointer; user-select:none; position:sticky; top:0; }
th:hover { color:#5DADE2; }
td { padding:8px; border-bottom:1px solid #21262d; }
tbody tr.clickable { cursor:pointer; }
tbody tr:hover td { background:#1c2331; }
.num { text-align:right; font-variant-numeric:tabular-nums; }
.wrap { max-height:70vh; overflow:auto; border:1px solid #30363d; border-radius:8px; }
.loading { color:#8b949e; padding:40px; text-align:center; }
.count { color:#8b949e; font-size:12px; margin-bottom:8px; }
a { color:#5DADE2; text-decoration:none; font-size:13px; }
a:hover { text-decoration:underline; }
.chartbox { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px; margin-bottom:14px; }
canvas { width:100%; display:block; }
.legend { display:flex; gap:14px; font-size:12px; color:#8b949e; margin-bottom:8px; flex-wrap:wrap; }
.legend span i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; }
.kv { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:0 22px; font-size:13px; }
.kv div { padding:5px 0; border-bottom:1px solid #21262d; display:flex; justify-content:space-between; gap:10px; }
.kv .k { color:#8b949e; }
.kv .v { font-variant-numeric:tabular-nums; text-align:right; }
.kv .v.cf { color:#5DADE2; }
.toggles { display:flex; gap:12px; flex-wrap:wrap; font-size:12px; margin-bottom:10px; }
.toggles label { cursor:pointer; color:#8b949e; user-select:none; }
.toggles label.custom { color:#48C9B0; }
.pill { display:inline-block; background:#1c2331; border:1px solid #30363d; border-radius:12px;
  padding:3px 10px; font-size:11px; margin:2px 4px 2px 0; color:#5DADE2; }
.pill.custom { color:#48C9B0; border-color:#2d5a52; }
.zbar { display:flex; height:26px; border-radius:5px; overflow:hidden; margin:6px 0 4px; }
.zbar div { display:flex; align-items:center; justify-content:center; font-size:10px; color:#0e1117; font-weight:600; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
@media (max-width:800px){ .grid2 { grid-template-columns:1fr; } }
.err { color:#E74C3C; font-size:12px; }
.tip { position:fixed; z-index:999; pointer-events:none; display:none;
  background:#0d1117; border:1px solid #30363d; border-radius:6px;
  padding:8px 10px; font-size:12px; color:#e6e6e6; box-shadow:0 4px 14px rgba(0,0,0,.5);
  max-width:260px; line-height:1.5; }
.tip .th { color:#8b949e; font-size:11px; margin-bottom:4px; }
.tip .tr { display:flex; justify-content:space-between; gap:12px; }
.tip .tr i { display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:6px; }
.chartbox { position:relative; }
.legend span.tog { cursor:pointer; user-select:none; padding:2px 6px; border-radius:4px; }
.legend span.tog:hover { background:#1c2331; }
.legend span.tog.off { opacity:.35; }
.legend span.tog.off i { background:#484f58 !important; }
.frescura { display:inline-flex; align-items:center; gap:8px; font-size:12px;
  padding:5px 10px; border-radius:6px; border:1px solid #30363d; background:#161b22; }
.frescura .dot { width:8px; height:8px; border-radius:50%; }
.frescura button { background:#1c2331; border:1px solid #30363d; color:#5DADE2;
  padding:4px 10px; border-radius:5px; font-size:12px; cursor:pointer; }
.frescura button:hover { background:#22304a; }
.frescura button:disabled { opacity:.5; cursor:default; }
"""

# Registo de tabs: (slug, url, label). A ordem define a barra de navegacao.
TABS = [
    ('volume',     '/',            'Volume'),
    ('recordes',   '/recordes',    'Recordes'),
    ('atividades', '/atividades',  'Atividades'),
]


def nav(active):
    links = ''.join(
        f'<a href="{url}" class="{"on" if slug == active else ""}">{label}</a>'
        for slug, url, label in TABS)
    return f'<div class="nav">{links}</div>'


# ── JS partilhado: periodos, pivots e grafico de barras empilhadas ──
CHART_JS = r"""
function periodoDe(dateStr,tipo){
 const d=new Date(dateStr+'T00:00:00');
 if(tipo==='ano')return String(d.getFullYear());
 if(tipo==='mes')return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0');
 const t=new Date(Date.UTC(d.getFullYear(),d.getMonth(),d.getDate()));
 const dn=t.getUTCDay()||7; t.setUTCDate(t.getUTCDate()+4-dn);
 const y0=new Date(Date.UTC(t.getUTCFullYear(),0,1));
 const wk=Math.ceil(((t-y0)/86400000+1)/7);
 return t.getUTCFullYear()+'-W'+String(wk).padStart(2,'0');
}
function pivot(rows,periodo,groupKey,valueKey,groups){
 const map={};
 rows.forEach(function(r){
  const p=periodoDe(r.date,periodo);
  const g=typeof groupKey==='function'?groupKey(r):r[groupKey];
  if(g==null||groups.indexOf(g)===-1)return;
  const v=typeof valueKey==='function'?valueKey(r):r[valueKey];
  if(!isFinite(v))return;
  map[p]=map[p]||{}; map[p][g]=(map[p][g]||0)+v;});
 return Object.keys(map).sort().map(p=>({periodo:p,vals:map[p]}));
}
function pivotCols(rows,periodo,cols){
 const map={};
 rows.forEach(function(r){
  const p=periodoDe(r.date,periodo);
  map[p]=map[p]||{};
  cols.forEach(function(c){const v=r[c];if(isFinite(v))map[p][c]=(map[p][c]||0)+v;});});
 return Object.keys(map).sort().map(p=>({periodo:p,vals:map[p]}));
}
function ctx(id,h){const c=document.getElementById(id);if(!c)return null;
 const dpr=window.devicePixelRatio||1;const W=c.clientWidth;
 c.width=W*dpr;c.height=h*dpr;const g=c.getContext('2d');
 g.scale(dpr,dpr);g.clearRect(0,0,W,h);return {g:g,W:W,H:h};}
function noData(g,W,H,msg){g.fillStyle='#8b949e';g.font='13px sans-serif';
 g.fillText(msg||'Sem dados',20,30);}
function fmtH(h){const H=Math.floor(h),M=Math.round((h-H)*60);return H+'h'+String(M).padStart(2,'0');}

// Series desligadas por clique na legenda, por grafico.
const OFF = {};
function ligado(canvasId,k){ return !(OFF[canvasId] && OFF[canvasId][k]); }
function alternar(canvasId,k){
 OFF[canvasId] = OFF[canvasId] || {};
 OFF[canvasId][k] = !OFF[canvasId][k];
 if (typeof redraw === 'function') redraw();
}

function drawStack(canvasId,legendId,data,groups,cores,opts){
 opts=opts||{};
 const o=ctx(canvasId,opts.height||260); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 if(legendId){
  document.getElementById(legendId).innerHTML=groups.map(function(k){
   const off = !ligado(canvasId,k);
   return '<span class="tog'+(off?' off':'')+'" data-c="'+canvasId+'" data-k="'+k+'">'+
    '<i style="background:'+(cores[k]||'#8b949e')+'"></i>'+
    (opts.labels&&opts.labels[k]||k)+'</span>';}).join('');
  document.querySelectorAll('#'+legendId+' span.tog').forEach(function(sp){
   sp.onclick=function(){ alternar(sp.dataset.c, sp.dataset.k); };});
 }
 // desenha so o que esta ligado; o empilhamento e as percentagens
 // recalculam-se sobre as series visiveis
 groups = groups.filter(k=>ligado(canvasId,k));
 if(!groups.length){noData(g,W,H,'Todas as series desligadas');return;}
 if(!data.length){noData(g,W,H);return;}
 const pct=opts.pct;
 const PL=54,PR=14,PT=12,PB=34,w=W-PL-PR,h=H-PT-PB;
 const totals=data.map(d=>groups.reduce((s,k)=>s+(d.vals[k]||0),0));
 const mx=pct?100:Math.max.apply(null,totals.concat([1]));
 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 const bw=w/data.length,pad=Math.min(4,bw*0.18);
 data.forEach(function(d,i){
  const tot=totals[i]||1;let acc=0;
  groups.forEach(function(k){
   let v=d.vals[k]||0; if(!v)return;
   if(pct)v=v/tot*100;
   const bh=h*v/mx;
   g.fillStyle=cores[k]||'#8b949e';
   g.fillRect(PL+i*bw+pad/2,PT+h-acc-bh,bw-pad,bh);
   acc+=bh;});});
 if(!pct&&totals.length){
  const avg=totals.reduce((s,x)=>s+x,0)/totals.length;
  const y=PT+h-h*avg/mx;
  g.strokeStyle='#8b949e';g.setLineDash([4,4]);g.beginPath();
  g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();g.setLineDash([]);
  g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='left';
  g.fillText('media '+avg.toFixed(opts.decimals||0)+(opts.unit||''),PL+4,y-4);}
 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++){const v=mx-mx*i/4;
  g.fillText(pct?Math.round(v)+'%':(v>=1000?Math.round(v/1000)+'k':v.toFixed(opts.decimals||0)),
   PL-6,PT+h*i/4+3);}
 g.textAlign='center';
 const step=Math.ceil(data.length/12);
 data.forEach(function(d,i){if(i%step!==0)return;
  g.save();g.translate(PL+i*bw+bw/2,H-8);
  if(data.length>16){g.rotate(-Math.PI/5);g.textAlign='right';}
  g.fillText(d.periodo,0,0);g.restore();});
 g.textAlign='left';

 // tooltip: barra sob o rato -> valores de cada serie visivel
 registarTip(canvasId,function(mx,my,rw){
  const esc=rw/W;                       // o canvas e escalado por CSS
  const x=mx/esc;
  const i=Math.floor((x-PL)/bw);
  if(i<0||i>=data.length||x<PL||x>PL+w) return '';
  const d=data[i], tot=groups.reduce((s,k)=>s+(d.vals[k]||0),0);
  const dec=opts.decimals||0;
  let html='<div class="th">'+d.periodo+'</div>';
  groups.forEach(function(k){
   const v=d.vals[k]||0; if(!v)return;
   const val=pct? (v/tot*100).toFixed(0)+'%'
                : v.toFixed(dec)+(opts.unit||'');
   html+=linhaTip(cores[k]||'#8b949e',(opts.labels&&opts.labels[k])||k,val);});
  if(groups.length>1)
   html+='<div class="tr" style="border-top:1px solid #30363d;margin-top:4px;padding-top:4px">'+
         '<span>Total</span><b>'+(pct?'100%':tot.toFixed(dec)+(opts.unit||''))+'</b></div>';
  return html;
 });
}

// ─── Tooltip partilhado ──────────────────────────────────────────────────
// Cada grafico regista como converter a posicao do rato em conteudo.
const TIPS={};
function _tipEl(){
 let t=document.getElementById('__tip');
 if(!t){ t=document.createElement('div'); t.id='__tip'; t.className='tip';
   document.body.appendChild(t); }
 return t;
}
function registarTip(canvasId,fn){
 const c=document.getElementById(canvasId); if(!c) return;
 TIPS[canvasId]=fn;
 if(c.dataset.tip) return;      // so ligar os eventos uma vez
 c.dataset.tip='1';
 c.style.cursor='crosshair';
 c.addEventListener('mousemove',function(ev){
  const f=TIPS[canvasId]; if(!f) return;
  const r=c.getBoundingClientRect();
  const html=f(ev.clientX-r.left, ev.clientY-r.top, r.width, r.height);
  const t=_tipEl();
  if(!html){ t.style.display='none'; return; }
  t.innerHTML=html; t.style.display='block';
  // manter dentro do ecra
  const tw=t.offsetWidth||200, th=t.offsetHeight||60;
  let x=ev.clientX+14, y=ev.clientY+14;
  if(x+tw>window.innerWidth-8) x=ev.clientX-tw-14;
  if(y+th>window.innerHeight-8) y=ev.clientY-th-14;
  t.style.left=x+'px'; t.style.top=y+'px';
 });
 c.addEventListener('mouseleave',function(){ _tipEl().style.display='none'; });
}
function linhaTip(cor,nome,valor){
 return '<div class="tr"><span><i style="background:'+cor+'"></i>'+nome+'</span>'+
        '<b>'+valor+'</b></div>';
}
function fmtD(s){
 s=Number(s);
 if(s<60) return s+'s';
 if(s<3600) return (s%60?(s/60).toFixed(1):s/60)+'min';
 return (s%3600?(s/3600).toFixed(1):s/3600)+'h';
}

function limparOutliers(rows,col,factor){
 factor=factor||3;
 const out=rows.map(r=>Object.assign({},r));
 const mods=Array.from(new Set(out.map(r=>r.type)));
 let n=0;
 mods.forEach(function(m){
  const vals=out.filter(r=>r.type===m&&isFinite(r[col])&&r[col]>0).map(r=>r[col]).sort((a,b)=>a-b);
  if(vals.length<8)return;
  const q=p=>vals[Math.floor(p*(vals.length-1))];
  const q1=q(0.25),q3=q(0.75),iqr=q3-q1;
  if(iqr===0)return;
  const lo=q1-factor*iqr,hi=q3+factor*iqr;
  out.forEach(function(r){if(r.type===m&&isFinite(r[col])&&(r[col]<lo||r[col]>hi)){r[col]=0;n++;}});});
 window.__NOUT__=n;
 return out;
}
"""


FRESCURA_HTML = ('<div class="frescura" id="frescura">'
                 '<span class="dot" style="background:#484f58"></span>'
                 '<span id="frescuraTxt">a verificar...</span>'
                 '<button id="btSync">Actualizar</button></div>')

FRESCURA_JS = r"""
// Indicador de frescura: compara a data mais recente na base com a API.
async function verificarFrescura(){
 const txt=document.getElementById('frescuraTxt');
 const dot=document.querySelector('#frescura .dot');
 if(!txt) return;
 try{
  const f=await fetch('/api/frescura?verificar=1').then(r=>r.json());
  if(!f.db){ txt.textContent='sem base de dados - le sempre da API';
             dot.style.background='#5DADE2'; return; }
  const ult=f.ultima_na_base||'?';
  if(f.erro){ txt.textContent='ultima sessao '+ult+' - nao consegui verificar a API';
              dot.style.background='#E67E22'; return; }
  if(f.novas>0){
   const n=f.novas;
   txt.innerHTML='<b>'+n+(n===1?' sessao nova':' sessoes novas')+'</b> na Intervals.icu - '+
    'a base vai ate '+ult;
   dot.style.background='#F4D03F';
  } else {
   txt.textContent='actualizado - ultima sessao '+ult;
   dot.style.background='#2ECC71';
  }
 }catch(e){ txt.textContent='nao consegui verificar'; dot.style.background='#E74C3C'; }
}

async function sincronizar(){
 const bt=document.getElementById('btSync');
 const txt=document.getElementById('frescuraTxt');
 const dot=document.querySelector('#frescura .dot');
 bt.disabled=true; bt.textContent='a sincronizar...'; dot.style.background='#5DADE2';
 try{
  const r=await fetch('/api/sync').then(r=>r.json());
  if(!r.ok){ txt.textContent='erro: '+(r.erro||'?'); dot.style.background='#E74C3C'; }
  else {
   txt.textContent=r.inseridas+' novas, '+r.actualizadas+' actualizadas';
   dot.style.background='#2ECC71';
   setTimeout(()=>location.reload(),900);
  }
 }catch(e){ txt.textContent='erro a sincronizar'; dot.style.background='#E74C3C'; }
 bt.disabled=false; bt.textContent='Actualizar';
}
(function(){
 const bt=document.getElementById('btSync');
 if(bt){ bt.onclick=sincronizar; verificarFrescura(); }
})();
"""


def page(title, active, body, extra_js=""):
    """Monta uma pagina completa."""
    return f"""<!DOCTYPE html><html lang="pt"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{CSS}</style></head><body>
{nav(active)}
{body}
<div style="margin-top:26px">{FRESCURA_HTML}</div>
<script>{CHART_JS}
{extra_js}
{FRESCURA_JS}</script></body></html>"""
