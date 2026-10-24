"""Tab Curva progressiva (Etapa 28): envelope MMP por modalidade e duracao, janela movel, grafico conjunto.

Fina camada sobre modelo_curva_progressiva (nucleo puro). Le power_curves e a elegibilidade das atividades
(modelo_estimador.elegibilidade_aprendizagem). Nao grava, nao sincroniza e nao toca no IR.
"""

import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import jsonify, request

import db
import modelo_curva_progressiva as mcp
from tabs.base import page

SLUG = 'curva'
ROUTE = '/curva-progressiva'

# Fuso da data de referencia por defeito (o relogio do servidor pode estar em UTC e trocar o dia).
FUSO = os.environ.get('CURVA_FUSO', 'America/Sao_Paulo')


def _hoje():
    return datetime.now(ZoneInfo(FUSO)).date()


def _outras_modalidades(curvas, ate):
    """Atividades com modalidade fora de Bike/Row/Ski/Run (ex.: Other), ate `ate`. Nao entram no grafico."""
    ate_d = date.fromisoformat(ate)
    out = []
    for c in curvas:
        d = mcp._dia(c.get('date'))
        if c.get('type') not in mcp.MODALIDADES and d is not None and d <= ate_d:
            out.append({'activity_id': c.get('activity_id'), 'modalidade': c.get('type'),
                        'data': d.isoformat(), 'motivo': 'modalidade_fora_do_grafico'})
    return sorted(out, key=lambda x: (x['modalidade'], x['data']))


def _sinalizar_iqr(out, curvas, estados, ate):
    """Marca, por ponto, se o valor esta fora do IQR (k=3) da sua modalidade+duracao no historico ate `ate`.
    Informativo: nunca remove nem altera o ponto. Reutiliza a regra de pmc._evol_outliers."""
    import pmc
    pts, _ = mcp.pontos(curvas, ate, estados, False)
    obs = {}
    for p in pts:
        if p['estado'] == 'usado':
            obs.setdefault((p['modalidade'], p['duracao_s']), []).append((p['data'], p['watts'], p['activity_id']))
    for m, bloco in out['modalidades'].items():
        for pt in bloco['pontos']:
            serie = obs.get((m, pt['duracao_s']), [])
            flags = pmc._evol_outliers(serie)
            ids = {f['id'] for f in flags}
            pt['outlier_iqr'] = pt['activity_id'] in ids
            pt['outlier_iqr_motivo'] = ('fora do IQR x3 do historico desta modalidade e duracao; '
                                        'sinalizado, mantido') if pt['outlier_iqr'] else None


def _lista(v):
    return [x.strip() for x in v.split(',') if x.strip()] if v else None


def _erro(msg, codigo=400):
    return jsonify({'ok': False, 'erro': msg}), codigo


def _carregar_estados(curvas, atividades):
    """{activity_id: (estado, motivo)} so para as atividades que tem curva. Sem a atividade ou sem a lista,
    a elegibilidade e 'desconhecida' e a curva fica excluida com esse motivo (nunca assumida como valida)."""
    import modelo_estimador as E
    out = {}
    for aid in {c.get('activity_id') for c in curvas}:
        r = E.elegibilidade_aprendizagem(aid, atividades)
        out[aid] = (r['estado'], r['motivo'])
    return out


def api_data():
    """Curva progressiva. Parametros (todos opcionais):
      modalidades=Bike,Row,Ski,Run   duracoes=60,300,1200   janela=42 | historico
      ate=AAAA-MM-DD (por defeito hoje)   excluir_suspeitos=1
      evolucao=1 (+ desde=AAAA-MM-DD, passo=7 dias)
    """
    if not db.ENABLED:
        return jsonify({'ok': False, 'erro': 'sem base de dados configurada', 'modalidades': {}})
    try:
        mods = _lista(request.args.get('modalidades'))
        if mods and any(m not in mcp.MODALIDADES for m in mods):
            return _erro('modalidade invalida; validas: ' + ', '.join(mcp.MODALIDADES))
        duracoes = _lista(request.args.get('duracoes'))
        if duracoes:
            try:
                duracoes = [int(x) for x in duracoes]
            except ValueError:
                return _erro('duracoes tem de ser inteiros em segundos')
            if any(d <= 0 or d > 86400 for d in duracoes):
                return _erro('duracao fora do intervalo 1..86400 s')
        jan = request.args.get('janela', str(mcp.JANELA_PADRAO_DIAS))
        if jan == 'historico':
            janela = None
        else:
            try:
                janela = int(jan)
            except ValueError:
                return _erro('janela tem de ser um numero de dias ou "historico"')
            if not 1 <= janela <= 3650:
                return _erro('janela fora do intervalo 1..3650 dias')
        ate_pedida = request.args.get('ate')
        ate = ate_pedida or _hoje().isoformat()
        try:
            date.fromisoformat(ate)
        except ValueError:
            return _erro('ate tem de ser AAAA-MM-DD')
        excluir = request.args.get('excluir_suspeitos') in ('1', 'true')
        com_evolucao = request.args.get('evolucao') in ('1', 'true')
        desde = request.args.get('desde') or (date.fromisoformat(ate) - timedelta(days=365)).isoformat()
        try:
            passo = int(request.args.get('passo', '7'))
        except ValueError:
            return _erro('passo tem de ser um numero de dias')
        if not 1 <= passo <= 90:
            return _erro('passo fora do intervalo 1..90 dias')

        curvas = db.load_power_curves() or []
        atividades = db.load_activities()
        estados = _carregar_estados(curvas, atividades)
        out = mcp.curva(curvas, ate, modalidades=mods, duracoes=duracoes, janela_dias=janela,
                        estados=estados, excluir_suspeitos=excluir)
        _sinalizar_iqr(out, curvas, estados, ate)
        out['outras_modalidades'] = _outras_modalidades(curvas, ate)
        out['data_referencia'] = {'ate': ate, 'origem': 'parametro' if ate_pedida else 'padrao',
                                  'fuso': FUSO if not ate_pedida else None}
        if com_evolucao:
            out['evolucao'] = mcp.evolucao(curvas, ate, desde, passo_dias=passo, modalidades=mods,
                                           duracoes=duracoes, janela_dias=janela, estados=estados,
                                           excluir_suspeitos=excluir)
        out.update({'ok': True, 'versao': mcp.VERSAO, 'n_curvas_lidas': len(curvas),
                    'atividades_disponiveis': atividades is not None,
                    'nota': ('Valores MEDIDOS (MMP). Envelope = maximo na janela. Sem interpolacao. '
                             'Nao e curva de influencia fisiologica nem IR validado.')})
        return jsonify(out)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'erro': f'{type(e).__name__}: {e}'}), 500


def render():
    return page('Curva progressiva', SLUG, BODY, JS)


BODY = r"""
<style>.ctl{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;font-size:13px;margin:10px 0}.ctl label{display:inline-flex;align-items:center;gap:5px;font-size:13px;justify-content:flex-start}.ctl input[type=checkbox]{margin:0;width:auto}.legenda{font-size:13px;margin:6px 0}h2{font-size:16px;margin:18px 0 6px}canvas{display:block;height:380px}#ev{height:280px}</style>
<div class="curva-pagina">
<h1>Curva progressiva</h1>
<p class="sub" id="sub">Envelope de melhores esforcos (MMP), por modalidade e duracao. Valores medidos; nada e interpolado.</p>
<div class="ctl">
 <span>Modalidades:</span>
 <label><input type="checkbox" class="mod" value="Bike" checked> Bike</label>
 <label><input type="checkbox" class="mod" value="Row" checked> Row</label>
 <label><input type="checkbox" class="mod" value="Ski" checked> Ski</label>
 <label><input type="checkbox" class="mod" value="Run" checked> Run</label>
 <span>Janela:</span>
 <select id="janela"><option value="42" selected>42 dias</option><option value="90">90 dias</option>
  <option value="180">180 dias</option><option value="historico">Historico completo</option></select>
 <span>Ate:</span><input type="date" id="ate">
 <span>Duracoes (s):</span><input type="text" id="dur" placeholder="todas" size="18">
 <label><input type="checkbox" id="susp"> excluir valores suspeitos</label>
 <button id="ok">Atualizar</button>
</div>
<div id="msg" class="loading"></div>
<h2>Grafico conjunto (duracoes diferentes por modalidade)</h2>
<canvas id="cv" height="380"></canvas>
<div id="legenda" class="legenda"></div>
<div id="estados" class="legenda"></div>
<h2>Evolucao do envelope</h2>
<div class="ctl"><span>Duracao:</span><select id="durev"></select></div>
<canvas id="ev" height="280"></canvas>
<h2>Pontos da curva</h2>
<div style="overflow:auto"><table id="tbl"><thead><tr><th>Modalidade</th><th>Duracao</th><th>W</th><th>Data</th><th>Atividade</th><th>Origem</th><th>Serie</th></tr></thead><tbody></tbody></table></div>
<h2>Fora do grafico conjunto</h2>
<div id="outras"></div>
<h2>Em falta e exclusoes</h2>
<div id="falta"></div>
<h3>Atividades excluidas (com motivo)</h3>
<div id="excl"></div>
<h3>Valores suspeitos (sinalizados, nao removidos)</h3>
<div id="susp"></div>
<h3>Avisos IQR (sinalizados, mantidos; nao sao removidos)</h3>
<div id="iqr"></div>
</div>
"""

JS = r"""
const COR={Bike:'#1f77b4',Row:'#2ca02c',Ski:'#9467bd',Run:'#d62728'};
let D=null, pontosCV=[];

function parametros(){
  const mods=[...document.querySelectorAll('.mod:checked')].map(c=>c.value);
  const p=new URLSearchParams();
  if(mods.length) p.set('modalidades',mods.join(','));
  p.set('janela',document.getElementById('janela').value);
  const ate=document.getElementById('ate').value; if(ate) p.set('ate',ate);
  const dur=document.getElementById('dur').value.trim(); if(dur) p.set('duracoes',dur);
  if(document.getElementById('susp').checked) p.set('excluir_suspeitos','1');
  p.set('evolucao','1');
  return p;
}

async function carregar(){
  const msg=document.getElementById('msg');
  msg.textContent='A calcular...';
  try{
    const r=await fetch('/api/curva-progressiva?'+parametros().toString());
    D=await r.json();
  }catch(e){ msg.textContent='Nao consegui carregar: '+e.message; return; }
  if(!D.ok){ msg.textContent=D.erro||'Erro'; return; }
  msg.textContent='';
  const p=D.serie==='envelope_historico_completo'?'historico completo':'janela de '+D.janela_dias+' dias (ate '+D.ate+' inclusive)';
  const ref=D.data_referencia||{};
  const origem=ref.origem==='padrao'?('data de referencia por defeito, fuso '+ref.fuso):'data escolhida';
  document.getElementById('sub').textContent='Ate '+D.ate+' ('+origem+') — envelope MMP, '+p+'. '+D.nota;
  preencherDuracoesEvolucao();
  desenharConjunto();
  desenharEvolucao();
  tabela();
  faltaExclusoes();
}

function preencherDuracoesEvolucao(){
  const sel=document.getElementById('durev'); const antes=sel.value;
  const durs=new Set();
  const ev=D.evolucao||{modalidades:{}};
  for(const m in ev.modalidades) for(const d in ev.modalidades[m]) durs.add(Number(d));
  const lst=[...durs].sort((a,b)=>a-b);
  sel.innerHTML=lst.map(d=>'<option value="'+d+'">'+d+' s</option>').join('');
  if(antes && lst.includes(Number(antes))) sel.value=antes;
  else if(lst.includes(300)) sel.value='300';
}

function escalaX(d,min,max,w,pad){ return pad+(Math.log10(d)-min)/(max-min||1)*(w-2*pad); }

function desenharConjunto(){
  const cv=document.getElementById('cv'); const W=cv.clientWidth||800; cv.width=W; const H=cv.height;
  const g=cv.getContext('2d'); g.clearRect(0,0,W,H);
  pontosCV=[];
  const todos=[];
  for(const m in D.modalidades) for(const p of D.modalidades[m].pontos){ todos.push({m,...p}); }
  if(!todos.length){ g.fillStyle='#888'; g.fillText('Sem valores medidos para estas modalidades nesta janela.',20,30); document.getElementById('legenda').textContent=''; document.getElementById('estados').textContent=''; return; }
  const ds=todos.map(p=>p.duracao_s), ws=todos.map(p=>p.watts);
  const lmin=Math.log10(Math.min(...ds)), lmax=Math.log10(Math.max(...ds));
  const wmax=Math.max(...ws)*1.08, pad=48;
  g.strokeStyle='#ccc'; g.fillStyle='#555'; g.font='11px sans-serif';
  for(let k=0;k<=4;k++){ const y=H-pad-(k/4)*(H-2*pad); g.beginPath(); g.moveTo(pad,y); g.lineTo(W-pad,y); g.stroke(); g.fillText(Math.round(wmax*k/4)+' W',4,y+4); }
  for(const d of [...new Set(ds)].sort((a,b)=>a-b)){ const x=escalaX(d,lmin,lmax,W,pad); g.fillText(d+' s',x-12,H-pad+16); }
  const leg=[];
  const estados=[];
  for(const m in D.modalidades){
    const pts=D.modalidades[m].pontos.slice().sort((a,b)=>a.duracao_s-b.duracao_s);
    const b=D.modalidades[m];
    estados.push('<div><b style="color:'+COR[m]+'">'+m+'</b>: '+(b.estado==='sem_dados'?'sem dados nesta janela':
      (pts.length?pts.length+' duracao(oes) com valor':'sem valor nas duracoes pedidas'))+
      (b.em_falta.length?' · em falta na janela: '+b.em_falta.join(', ')+' s':'')+'</div>');
    if(!pts.length) continue;
    g.strokeStyle=COR[m]; g.fillStyle=COR[m]; g.setLineDash([4,4]); g.beginPath();
    pts.forEach((p,i)=>{ const x=escalaX(p.duracao_s,lmin,lmax,W,pad), y=H-pad-(p.watts/wmax)*(H-2*pad); i?g.lineTo(x,y):g.moveTo(x,y); });
    g.stroke(); g.setLineDash([]);
    for(const p of pts){ const x=escalaX(p.duracao_s,lmin,lmax,W,pad), y=H-pad-(p.watts/wmax)*(H-2*pad);
      g.beginPath(); g.arc(x,y,4,0,Math.PI*2); g.fill(); pontosCV.push({x,y,m,p});
      if(p.outlier_iqr){ g.strokeStyle='#b00020'; g.lineWidth=2; g.beginPath(); g.arc(x,y,8,0,Math.PI*2); g.stroke(); g.lineWidth=1; g.strokeStyle=COR[m]; } }
    leg.push('<span style="color:'+COR[m]+'">● '+m+'</span>');
  }
  document.getElementById('legenda').innerHTML=leg.join(' &nbsp; ')+' <small>(linhas tracejadas: guia visual entre valores medidos; nao sao interpolacao; anel vermelho: aviso IQR, valor mantido)</small>';
  document.getElementById('estados').innerHTML=estados.join('');
  cv.onmousemove=function(ev){
    const r=cv.getBoundingClientRect(), mx=ev.clientX-r.left, my=ev.clientY-r.top;
    let best=null, bd=144;
    for(const q of pontosCV){ const dd=(q.x-mx)**2+(q.y-my)**2; if(dd<bd){bd=dd;best=q;} }
    cv.title = best ? best.m+' '+best.p.duracao_s+' s: '+best.p.watts+' W, '+best.p.data+', atividade '+best.p.activity_id+', '+best.p.origem+' ('+best.p.serie+')'+(best.p.outlier_iqr?' — AVISO IQR: '+best.p.outlier_iqr_motivo:'') : '';
  };
}

function desenharEvolucao(){
  const cv=document.getElementById('ev'); const W=cv.clientWidth||800; cv.width=W; const H=cv.height;
  const g=cv.getContext('2d'); g.clearRect(0,0,W,H);
  const ev=D.evolucao; const dur=document.getElementById('durev').value;
  if(!ev||!ev.as_of||!ev.as_of.length||!dur){ g.fillText('Sem serie de evolucao.',20,30); return; }
  const n=ev.as_of.length, pad=48;
  let wmax=0; for(const m in ev.modalidades){ const s=ev.modalidades[m][dur]||[]; for(const v of s) if(v!=null&&v>wmax) wmax=v; }
  if(!wmax){ g.fillText('Sem valores para '+dur+' s nesta janela.',20,30); return; }
  wmax*=1.08;
  g.strokeStyle='#ccc'; g.fillStyle='#555'; g.font='11px sans-serif';
  for(let k=0;k<=4;k++){ const y=H-pad-(k/4)*(H-2*pad); g.beginPath(); g.moveTo(pad,y); g.lineTo(W-pad,y); g.stroke(); g.fillText(Math.round(wmax*k/4)+' W',4,y+4); }
  g.fillText(ev.as_of[0],pad,H-12); g.fillText(ev.as_of[n-1],W-pad-70,H-12);
  for(const m in ev.modalidades){
    const s=ev.modalidades[m][dur]; if(!s) continue;
    g.strokeStyle=COR[m]; g.beginPath(); let aberto=false;
    s.forEach((v,i)=>{
      if(v==null){ aberto=false; return; }   // sem valor: quebra a linha, nunca interpola
      const x=pad+(i/(n-1||1))*(W-2*pad), y=H-pad-(v/wmax)*(H-2*pad);
      aberto?g.lineTo(x,y):g.moveTo(x,y); aberto=true; });
    g.stroke();
  }
}

function tabela(){
  const linhas=[];
  for(const m in D.modalidades) for(const p of D.modalidades[m].pontos)
    linhas.push('<tr><td>'+esc(m)+'</td><td>'+esc(p.duracao_s)+' s</td><td>'+esc(p.watts)+(p.outlier_iqr?' <span style="color:#b00020" title="'+esc(p.outlier_iqr_motivo)+'">⚠ IQR</span>':'')+'</td><td>'+esc(p.data)+'</td><td>'+esc(p.activity_id)+'</td><td>'+esc(p.origem)+'</td><td>'+esc(p.serie)+'</td></tr>');
  document.querySelector('#tbl tbody').innerHTML=linhas.join('')||'<tr><td colspan="7">Sem pontos nesta janela</td></tr>';
}

function esc(s){ return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function faltaExclusoes(){
  const f=[];
  for(const m in D.modalidades){ const b=D.modalidades[m];
    f.push('<div><b>'+m+'</b> '+(b.pontos.length?'':'<i>sem valores nesta janela</i> ')+'(duracoes no historico: '+b.duracoes_disponiveis.join(', ')+')'+
      ' · sem valor na janela: '+(b.em_falta.join(', ')||'nenhuma')+
      ' · usados: '+b.n_valores_usados+' · excluidos: '+b.n_valores_excluidos+'</div>'); }
  document.getElementById('falta').innerHTML=f.join('');
  const ex=(D.exclusoes||[]).map(x=>'<div>'+esc(x.modalidade)+' '+esc(x.data)+' · atividade '+esc(x.activity_id)+' · '+esc(x.motivo)+'</div>');
  document.getElementById('excl').innerHTML=ex.join('')||'<div>Nenhuma.</div>';
  const su=(D.suspeitos||[]).map(x=>'<div>'+esc(x.modalidade)+' '+esc(x.duracao_s)+' s · '+esc(x.watts)+' W · '+esc(x.data)+' · atividade '+esc(x.activity_id)+' · '+esc(x.motivo_suspeita)+'</div>');
  document.getElementById('susp').innerHTML=su.join('')||'<div>Nenhum.</div>';
  const iq=[];
  for(const m in D.modalidades) for(const p of D.modalidades[m].pontos) if(p.outlier_iqr)
    iq.push('<div>'+esc(m)+' '+esc(p.duracao_s)+' s · '+esc(p.watts)+' W · '+esc(p.data)+' · atividade '+esc(p.activity_id)+' · '+esc(p.outlier_iqr_motivo)+'</div>');
  document.getElementById('iqr').innerHTML=iq.join('')||'<div>Nenhum aviso IQR nesta janela.</div>';
  const ou=(D.outras_modalidades||[]).map(x=>'<div>'+esc(x.modalidade)+' '+esc(x.data)+' · atividade '+esc(x.activity_id)+' · '+esc(x.motivo)+'</div>');
  document.getElementById('outras').innerHTML=ou.length?'<div>Modalidades fora de Bike/Row/Ski/Run (nao entram no grafico):</div>'+ou.join(''):'<div>Nenhuma.</div>';
}

document.getElementById('ok').addEventListener('click',carregar);
document.getElementById('durev').addEventListener('change',()=>desenharEvolucao());
/* data de referencia: vem da API (fuso do servidor); o campo fica vazio por defeito */
carregar();
"""
