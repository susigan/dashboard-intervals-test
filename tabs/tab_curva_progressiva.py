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


# Duas categorias distintas, com nomes distintos na API (suspeitos / outlier_iqr):
DEFINICOES_AVISOS = {
    'suspeitos': ('incoerencia de monotonia dentro da mesma atividade: uma duracao mais longa tem mais W '
                  'do que uma mais curta da mesma atividade. Calculado sobre todo o historico ate a data de '
                  'referencia (resumo.n_suspeitos). Por defeito mantido; excluir_suspeitos=1 retira-o.'),
    'outlier_iqr': ('valor fora de 3 x IQR da distribuicao historica (ate a data de referencia) da mesma '
                    'modalidade e duracao. Informativo: pode ser um esforco real e extremo (ex.: sprint). '
                    'Nunca e removido nem altera o envelope; nao implica incoerencia.'),
}


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
    """Por ponto: outlier_iqr (informativo, 3 x IQR do historico da modalidade+duracao) e
    suspeito_monotonia (a regra de monotonia, a mesma de `suspeitos`). Nunca remove nem altera valores."""
    import pmc
    pts, _ = mcp.pontos(curvas, ate, estados, False)
    obs = {}
    susp = set()
    for p in pts:
        if p['estado'] == 'usado':
            obs.setdefault((p['modalidade'], p['duracao_s']), []).append((p['data'], p['watts'], p['activity_id']))
        if p['suspeito']:
            susp.add((p['modalidade'], p['duracao_s'], p['activity_id']))
    for m, bloco in out['modalidades'].items():
        for pt in bloco['pontos']:
            flags = pmc._evol_outliers(obs.get((m, pt['duracao_s']), []))
            pt['outlier_iqr'] = pt['activity_id'] in {f['id'] for f in flags}
            pt['outlier_iqr_motivo'] = ('fora do IQR x3 do historico desta modalidade e duracao; sinalizado, mantido'
                                        if pt['outlier_iqr'] else None)
            pt['suspeito_monotonia'] = (m, pt['duracao_s'], pt['activity_id']) in susp


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
        out['definicoes_avisos'] = DEFINICOES_AVISOS
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
<style>
.curva-pagina{max-width:1100px}
.curva-pagina h1{margin-bottom:4px}
.ctl{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;font-size:14px;margin:12px 0}
.ctl label{display:inline-flex;align-items:center;gap:5px;margin:0}
.ctl input[type=checkbox]{margin:0;width:auto;min-width:0}
.ctl select,.ctl input[type=date]{font-size:14px}
.bloco{margin:18px 0}
.bloco h2{font-size:15px;margin:0 0 6px}
.legenda{font-size:13px;margin:6px 0;display:flex;flex-wrap:wrap;gap:6px 16px}
.estado{font-size:12px;color:#666}
.aviso{font-size:12.5px;color:#555;margin:8px 0;line-height:1.5}
.vazio{font-size:14px;color:#777;padding:24px 0}
canvas{display:block;width:100%;height:360px}
#ev{height:260px}
</style>
<div class="curva-pagina">
<h1>Curva progressiva</h1>
<p class="sub" id="sub">Melhores esforcos medidos (MMP) por modalidade e duracao.</p>

<div class="ctl">
 <label><input type="checkbox" class="mod" value="Bike" checked> Bike</label>
 <label><input type="checkbox" class="mod" value="Row" checked> Row</label>
 <label><input type="checkbox" class="mod" value="Ski" checked> Ski</label>
 <label><input type="checkbox" class="mod" value="Run" checked> Run</label>
 <span>Janela</span>
 <select id="janela">
  <option value="42" selected>42 dias</option>
  <option value="90">90 dias</option>
  <option value="180">180 dias</option>
  <option value="historico">Historico completo</option>
 </select>
 <span>Ate</span><input type="date" id="ate" title="Vazio = data de hoje (America/Sao_Paulo)">
 <label title="Retira da curva apenas os pontos marcados por incoerencia de monotonia">
  <input type="checkbox" id="susp"> excluir pontos com incoerencia</label>
 <button id="ok">Atualizar</button>
</div>

<div id="msg" class="loading"></div>

<div class="bloco">
 <h2>Curva MMP por modalidade</h2>
 <div class="legenda" id="legenda"></div>
 <canvas id="cv" height="360"></canvas>
</div>

<div class="bloco">
 <h2>Evolucao do melhor esforco</h2>
 <div class="ctl" style="margin:4px 0"><span>Duracao</span><select id="durev"></select></div>
 <canvas id="ev" height="260"></canvas>
</div>

<div class="bloco aviso" id="avisos"></div>
</div>
"""

JS = r"""
const COR={Bike:'#1f77b4',Row:'#2ca02c',Ski:'#9467bd',Run:'#d62728'};
const NOMES={Bike:'Bike',Row:'Row',Ski:'Ski',Run:'Run'};
let D=null, pontosCV=[];

function esc(s){ return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function parametros(){
  const mods=[...document.querySelectorAll('.mod:checked')].map(c=>c.value);
  const p=new URLSearchParams();
  p.set('modalidades',mods.join(','));
  p.set('janela',document.getElementById('janela').value);
  const ate=document.getElementById('ate').value; if(ate) p.set('ate',ate);
  if(document.getElementById('susp').checked) p.set('excluir_suspeitos','1');
  p.set('evolucao','1');
  return p;
}

async function carregar(){
  const msg=document.getElementById('msg');
  if(!document.querySelectorAll('.mod:checked').length){ msg.textContent='Escolha pelo menos uma modalidade.'; return; }
  msg.textContent='A calcular...';
  try{
    const r=await fetch('/api/curva-progressiva?'+parametros().toString());
    D=await r.json();
  }catch(e){ msg.textContent='Nao foi possivel carregar os dados: '+e.message; return; }
  if(!D.ok){ msg.textContent=D.erro||'Erro ao calcular.'; return; }
  msg.textContent='';
  const janela=D.serie==='envelope_historico_completo'?'historico completo':'ultimos '+D.janela_dias+' dias';
  const ref=(D.data_referencia&&D.data_referencia.origem==='padrao')?'hoje':'data escolhida';
  document.getElementById('sub').textContent='Janela: '+janela+' · ate '+D.ate+' ('+ref+') · valores medidos, sem interpolacao.';
  preencherDuracoesEvolucao();
  desenharConjunto();
  desenharEvolucao();
  avisos();
}

function preencherDuracoesEvolucao(){
  const sel=document.getElementById('durev'); const antes=sel.value;
  const durs=new Set();
  const ev=D.evolucao||{modalidades:{}};
  for(const m in ev.modalidades) for(const d in ev.modalidades[m]) durs.add(Number(d));
  const lst=[...durs].sort((a,b)=>a-b);
  sel.innerHTML=lst.map(d=>'<option value="'+d+'">'+rotuloDuracao(d)+'</option>').join('');
  if(antes && lst.includes(Number(antes))) sel.value=antes;
  else if(lst.includes(300)) sel.value='300';
}

function rotuloDuracao(s){
  if(s<60) return s+' s';
  if(s<3600) return (s/60)+' min';
  return (s/3600)+' h';
}

function escalaX(d,min,max,w,pad){ return pad+(Math.log10(d)-min)/(max-min||1)*(w-2*pad); }

function desenharConjunto(){
  const cv=document.getElementById('cv'); const W=cv.clientWidth||800; cv.width=W; const H=cv.height;
  const g=cv.getContext('2d'); g.clearRect(0,0,W,H);
  pontosCV=[];
  const leg=[];
  const todos=[];
  for(const m in D.modalidades) for(const p of D.modalidades[m].pontos){ todos.push({m,...p}); }
  for(const m in D.modalidades){
    const b=D.modalidades[m];
    const n=b.pontos.length;
    leg.push('<span><b style="color:'+COR[m]+'">● '+NOMES[m]+'</b> '+
      (n?'<span class="estado">'+n+' duracoes</span>':'<span class="estado">sem dados nesta janela</span>')+'</span>');
  }
  document.getElementById('legenda').innerHTML=leg.join('');
  if(!todos.length){
    g.fillStyle='#777'; g.font='14px sans-serif';
    g.fillText('Sem valores medidos para as modalidades e a janela escolhidas.',24,40);
    return;
  }
  const ds=todos.map(p=>p.duracao_s), ws=todos.map(p=>p.watts);
  const lmin=Math.log10(Math.min(...ds)), lmax=Math.log10(Math.max(...ds));
  const wmax=Math.max(...ws)*1.08, pad=48;
  g.strokeStyle='#e3e3e3'; g.fillStyle='#555'; g.font='12px sans-serif';
  for(let k=0;k<=4;k++){ const y=H-pad-(k/4)*(H-2*pad); g.beginPath(); g.moveTo(pad,y); g.lineTo(W-pad,y); g.stroke(); g.fillText(Math.round(wmax*k/4)+' W',4,y+4); }
  const dsU=[...new Set(ds)].sort((a,b)=>a-b), fim=[-Infinity,-Infinity];
  dsU.forEach(d=>{ const x=escalaX(d,lmin,lmax,W,pad), r=rotuloDuracao(d), lw=g.measureText(r).width, ini=x-lw/2;
    const k=fim.findIndex(f=>ini>f+8); if(k>=0){ g.fillText(r,ini,H-pad+16+k*14); fim[k]=ini+lw; } });
  for(const m in D.modalidades){
    const pts=D.modalidades[m].pontos.slice().sort((a,b)=>a.duracao_s-b.duracao_s);
    if(!pts.length) continue;
    g.strokeStyle=COR[m]; g.fillStyle=COR[m]; g.setLineDash([4,4]); g.lineWidth=1.5; g.beginPath();
    pts.forEach((p,i)=>{ const x=escalaX(p.duracao_s,lmin,lmax,W,pad), y=H-pad-(p.watts/wmax)*(H-2*pad); i?g.lineTo(x,y):g.moveTo(x,y); });
    g.stroke(); g.setLineDash([]);
    for(const p of pts){
      const x=escalaX(p.duracao_s,lmin,lmax,W,pad), y=H-pad-(p.watts/wmax)*(H-2*pad);
      g.beginPath(); g.arc(x,y,4,0,Math.PI*2); g.fill(); pontosCV.push({x,y,m,p});
      if(p.suspeito_monotonia){ g.strokeStyle='#e07b00'; g.lineWidth=2; g.beginPath(); g.arc(x,y,8,0,Math.PI*2); g.stroke(); g.lineWidth=1.5; g.strokeStyle=COR[m]; }
      if(p.outlier_iqr){ g.strokeStyle='#b00020'; g.lineWidth=2; g.beginPath(); g.arc(x,y,11,0,Math.PI*2); g.stroke(); g.lineWidth=1.5; g.strokeStyle=COR[m]; }
    }
  }
  cv.onmousemove=function(ev){
    const r=cv.getBoundingClientRect(), mx=ev.clientX-r.left, my=ev.clientY-r.top;
    let best=null, bd=144;
    for(const q of pontosCV){ const dd=(q.x-mx)**2+(q.y-my)**2; if(dd<bd){bd=dd;best=q;} }
    cv.title = best ? textoPonto(best.m,best.p) : '';
  };
}

function textoPonto(m,p){
  let t=NOMES[m]+' · '+rotuloDuracao(p.duracao_s)+': '+p.watts+' W · '+p.data+' · atividade '+p.activity_id;
  if(p.suspeito_monotonia) t+=' — incoerencia de monotonia (valor mantido na curva, salvo exclusao escolhida)';
  if(p.outlier_iqr) t+=' — valor fora da distribuicao historica (aviso informativo, mantido)';
  return t;
}

function desenharEvolucao(){
  const cv=document.getElementById('ev'); const W=cv.clientWidth||800; cv.width=W; const H=cv.height;
  const g=cv.getContext('2d'); g.clearRect(0,0,W,H);
  const ev=D.evolucao; const dur=document.getElementById('durev').value;
  if(!ev||!ev.as_of||!ev.as_of.length||!dur){ g.fillStyle='#777'; g.font='14px sans-serif'; g.fillText('Sem serie de evolucao.',24,40); return; }
  const n=ev.as_of.length, pad=48;
  let wmax=0; for(const m in ev.modalidades){ const s=ev.modalidades[m][dur]||[]; for(const v of s) if(v!=null&&v>wmax) wmax=v; }
  if(!wmax){ g.fillStyle='#777'; g.font='14px sans-serif'; g.fillText('Sem valores para '+rotuloDuracao(Number(dur))+' nesta janela.',24,40); return; }
  wmax*=1.08;
  g.strokeStyle='#e3e3e3'; g.fillStyle='#555'; g.font='12px sans-serif';
  for(let k=0;k<=4;k++){ const y=H-pad-(k/4)*(H-2*pad); g.beginPath(); g.moveTo(pad,y); g.lineTo(W-pad,y); g.stroke(); g.fillText(Math.round(wmax*k/4)+' W',4,y+4); }
  g.fillText(ev.as_of[0],pad,H-12); g.fillText(ev.as_of[n-1],W-pad-70,H-12);
  for(const m in ev.modalidades){
    const s=ev.modalidades[m][dur]; if(!s) continue;
    g.strokeStyle=COR[m]; g.lineWidth=2; g.beginPath(); let aberto=false;
    s.forEach((v,i)=>{
      if(v==null){ aberto=false; return; }   // sem valor: quebra a linha, nunca interpola
      const x=pad+(i/(n-1||1))*(W-2*pad), y=H-pad-(v/wmax)*(H-2*pad);
      aberto?g.lineTo(x,y):g.moveTo(x,y); aberto=true; });
    g.stroke();
  }
  g.lineWidth=1;
}

function avisos(){
  const el=document.getElementById('avisos');
  let nSusp=0, nIqr=0;
  for(const m in D.modalidades) for(const p of D.modalidades[m].pontos){ if(p.suspeito_monotonia) nSusp++; if(p.outlier_iqr) nIqr++; }
  const partes=[];
  if(nSusp) partes.push('<span style="color:#e07b00">●</span> '+nSusp+' valor(es) com incoerencia de monotonia (um esforco mais longo acima de um mais curto da mesma atividade)');
  if(nIqr) partes.push('<span style="color:#b00020">○</span> '+nIqr+' valor(es) fora da distribuicao historica (informativo)');
  const exM=(D.resumo&&D.resumo.n_excluidos_monotonia)||0, exA=(D.resumo&&D.resumo.n_excluidos_atividade)||0;
  if(document.getElementById('susp').checked && exM) partes.push(exM+' ponto(s) excluidos por incoerencia de monotonia, por escolha sua');
  if(exA) partes.push(exA+' ponto(s) de atividades inelegiveis excluidos (independente da escolha de monotonia).');
  const outras=(D.outras_modalidades||[]).length;
  if(outras) partes.push('Atividades de outra modalidade ('+outras+') nao entram no grafico.');
  el.innerHTML=partes.length?partes.map(x=>'<div>'+x+'</div>').join(''):'Sem avisos nesta janela. Os valores sao medidos, sem interpolacao.';
}

document.getElementById('ok').addEventListener('click',carregar);
document.getElementById('durev').addEventListener('change',()=>desenharEvolucao());
carregar();
"""
