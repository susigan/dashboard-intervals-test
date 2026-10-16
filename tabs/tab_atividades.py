"""Tab Atividades — lista completa, ordenavel e filtravel."""

from flask import Response, jsonify, request, send_file
from api_client import fetch_activities, cache_info, norm_tipo, num, kj_da_atividade
import os
import re
import export
import exportacao_completa as xc
import db as _db

_DATA_NOME = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _intervalo_nome(de, ate):
    """Parte do nome do arquivo: so datas validas AAAA-MM-DD entram no nome."""
    if not (de and ate and _DATA_NOME.match(de) and _DATA_NOME.match(ate)):
        return 'sem_data'
    return de if de == ate else f'{de}_a_{ate}'
from config import CORES_MOD
from tabs.base import page

SLUG = 'atividades'
ROUTE = '/atividades'


def api_data():
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'Fetch failed'}), 500

    out = []
    for a in acts:
        secs = num(a.get('elapsed_time')) or num(a.get('moving_time'))
        dist = num(a.get('icu_distance')) or num(a.get('distance'))
        raw = a.get('type')
        out.append({
            'id': a.get('id'), 'date': (a.get('start_date_local') or '')[:10],
            'name': a.get('name') or 'Sem nome',
            'type': norm_tipo(raw), 'type_raw': raw,
            'duration_min': round(secs / 60.0, 1),
            'distance_km': round(dist / 1000.0, 1),
            'kj': round(kj_da_atividade(a), 0),
            'training_load': num(a.get('icu_training_load')),
            'avg_watts': num(a.get('icu_weighted_avg_watts')) or num(a.get('icu_average_watts')),
            'ftp': num(a.get('icu_pm_ftp')) or num(a.get('icu_ftp')),
            'rpe': a.get('icu_rpe'), 'xss': round(num(a.get('SS')), 0),
            'avg_hr': num(a.get('average_heartrate')), 'max_hr': num(a.get('max_heartrate')),
            'source': a.get('source'),
            # tags vem null quando nao ha nenhuma, e lista de strings quando ha
            'tags': [t for t in (a.get('tags') or []) if t],
        })

    limit = request.args.get('limit', type=int)
    atype = request.args.get('type', type=str)
    if atype:
        out = [r for r in out if r['type'] == atype]
    if limit:
        out = out[:limit]
    todas_tags = sorted({t for r in out for t in r['tags']})
    return jsonify({'status': 'OK', 'total': len(out), 'cores': CORES_MOD,
                    'tags': todas_tags, 'activities': out})


LIMITE_IDS_CSV = 20000


def api_export_csv():
    """CSV das atividades pedidas pela aba: POST {"ids": [...]}.

    Os IDs chegam ja filtrados pela propria tabela (funcao filtrados() da pagina),
    por isso a exportacao usa exatamente os mesmos criterios, sem segunda copia.
    """
    corpo = request.get_json(silent=True)
    ids = corpo.get('ids') if isinstance(corpo, dict) else None
    if not isinstance(ids, list) or len(ids) > LIMITE_IDS_CSV or \
            not all(isinstance(i, (str, int)) and not isinstance(i, bool) for i in ids):
        return jsonify({'erro': 'pedido invalido: esperado {"ids": [...]}'}), 400
    if not ids:
        return jsonify({'status': 'vazio',
                        'mensagem': 'Nenhuma atividade corresponde aos filtros.'}), 404
    acts = fetch_activities()
    if not acts:
        return jsonify({'erro': 'sem atividades disponiveis'}), 503
    conteudo, info = export.atividades_csv(acts, ids)
    if info['n'] == 0:
        return jsonify({'status': 'vazio',
                        'mensagem': 'Nenhuma atividade corresponde aos filtros.'}), 404
    nome = f"atividades_{_intervalo_nome(info['de'], info['ate'])}.csv"
    cab = {'Content-Disposition': f'attachment; filename="{nome}"',
           'X-Linhas': str(info['n']),
           'X-Ids-Ausentes': str(len(info['ids_ausentes']))}
    return Response(conteudo, mimetype='text/csv; charset=utf-8', headers=cab)


def api_export_pacote():
    """ZIP completo: atividades (CSV e JSONL), streams e manifesto.

    POST {"ids": [...], "filtros": {...}}. Streams vem so do banco local; ver export.pacote_zip.
    """
    corpo = request.get_json(silent=True)
    ids = corpo.get('ids') if isinstance(corpo, dict) else None
    if not isinstance(ids, list) or len(ids) > LIMITE_IDS_CSV or \
            not all(isinstance(i, (str, int)) and not isinstance(i, bool) for i in ids):
        return jsonify({'erro': 'pedido invalido: esperado {"ids": [...]}'}), 400
    if not ids:
        return jsonify({'status': 'vazio',
                        'mensagem': 'Nenhuma atividade corresponde aos filtros.'}), 404
    bruto = corpo.get('filtros') if isinstance(corpo.get('filtros'), dict) else {}
    filtros = {k: str(bruto.get(k, ''))[:200] for k in export.FILTROS_PERMITIDOS}
    acts = fetch_activities()
    if not acts:
        return jsonify({'erro': 'sem atividades disponiveis'}), 503
    try:
        conteudo, res = export.pacote_zip(
            acts, ids, _db.get_streams, filtros=filtros,
            fonte_atividades=cache_info().get('fonte'), banco_ativo=_db.ENABLED)
    except export.LimitePacote as e:
        return jsonify({'status': 'limite',
                        'mensagem': (f'Pacote limitado a {export.LIMITE_ATIVIDADES_PACOTE} '
                                     f'atividades por arquivo ({e.n} selecionadas). '
                                     'Restrinja os filtros, por exemplo por datas.')}), 413
    except ValueError:
        return jsonify({'status': 'vazio',
                        'mensagem': 'Nenhuma atividade corresponde aos filtros.'}), 404
    intervalo = _intervalo_nome(res['de'], res['ate'])
    cab = {'Content-Disposition': f'attachment; filename="pacote_atividades_{intervalo}.zip"',
           'X-Atividades': str(res['n']),
           'X-Streams-Amostras': str(res['streams_amostras']),
           'X-Sem-Streams': str(res['sem_streams']),
           'X-Erros-Leitura': str(res['erros']),
           'X-Ids-Ausentes': str(len(res['ids_ausentes']))}
    return Response(conteudo, mimetype='application/zip', headers=cab)


MAX_IDS_EXPORTACAO_COMPLETA = 5000


def api_exportacao_iniciar():
    """Inicia a exportacao completa. So aqui a API de streams e chamada, por pedido explicito.

    POST {"ids": [...], "filtros": {...}}. Os IDs tem de estar no conjunto que a aba carregou.
    """
    corpo = request.get_json(silent=True)
    ids = corpo.get('ids') if isinstance(corpo, dict) else None
    if not isinstance(ids, list) or not ids or len(ids) > MAX_IDS_EXPORTACAO_COMPLETA or \
            not all(isinstance(i, (str, int)) and not isinstance(i, bool) for i in ids):
        return jsonify({'erro': 'pedido invalido: esperado {"ids": [...]} com 1 a 5000 IDs'}), 400
    acts = fetch_activities()
    if not acts:
        return jsonify({'erro': 'sem atividades disponiveis'}), 503
    autorizados = {str(a.get('id')) for a in acts if a.get('id') is not None}
    fora = [str(i) for i in ids if str(i) not in autorizados]
    if fora:
        return jsonify({'erro': 'IDs fora do conjunto autorizado', 'total_fora': len(fora),
                        'exemplos': fora[:5]}), 400
    bruto = corpo.get('filtros') if isinstance(corpo.get('filtros'), dict) else {}
    filtros = {k: str(bruto.get(k, ''))[:200] for k in export.FILTROS_PERMITIDOS}
    try:
        job = xc.novo_job(acts, ids, filtros)
    except xc.JobEmAndamento as e:
        return jsonify({'erro': 'ja existe uma exportacao em andamento',
                        'job_id': str(e)}), 409
    return jsonify({'job_id': job.id, 'total': len(job.ordem)}), 202


def _estado_de(job_id):
    job = xc.obter_job(job_id)
    res = job.resumo()
    pronto = (not job.rodando) and res['pendentes'] == 0
    return job, {'job_id': job.id, 'resumo': res, 'pronto': pronto,
                 'pacote_disponivel': os.path.exists(os.path.join(job.dir, 'pacote.zip')),
                 'falhas': job.falhas()[:200], 'falhas_total': len(job.falhas())}


def api_exportacao_estado(job_id):
    try:
        _, dados = _estado_de(job_id)
    except xc.JobInexistente:
        return jsonify({'erro': 'exportacao nao encontrada'}), 404
    return jsonify(dados)


def api_exportacao_retomar(job_id):
    """Refaz so as atividades com falha ou nao consultadas. As concluidas nao sao refeitas."""
    try:
        job = xc.obter_job(job_id)
    except xc.JobInexistente:
        return jsonify({'erro': 'exportacao nao encontrada'}), 404
    if job.rodando:
        return jsonify({'erro': 'exportacao ainda em andamento'}), 409
    try:
        xc.retomar_job(job)
    except xc.JobEmAndamento:
        return jsonify({'erro': 'exportacao ja em andamento'}), 409
    return jsonify({'job_id': job.id, 'retomando': True}), 202


def api_exportacao_download(job_id):
    try:
        job = xc.obter_job(job_id)
    except xc.JobInexistente:
        return jsonify({'erro': 'exportacao nao encontrada'}), 404
    if job.rodando:
        return jsonify({'erro': 'exportacao ainda em andamento'}), 409
    if job.resumo()['pendentes']:
        return jsonify({'erro': 'ha atividades nao consultadas; retome a exportacao primeiro'}), 409
    caminho = os.path.join(job.dir, 'pacote.zip')
    if not os.path.exists(caminho):
        try:
            caminho, _ = xc.montar_pacote(job)
        except xc.JobEmAndamento:
            return jsonify({'erro': 'exportacao ainda em andamento'}), 409
    nome = f'pacote_completo_{job.id[:8]}.zip'
    return send_file(caminho, mimetype='application/zip', as_attachment=True,
                     download_name=nome)


BODY = r"""
<h1>Atividades</h1>
<div class="sub" id="sub">A carregar...</div>
<div class="controls">
  <input id="search" placeholder="Procurar nome...">
  <select id="typeFilter"><option value="">Todos os tipos</option></select>
  <select id="tagFilter"><option value="">Todas as tags</option></select>
  <label class="sel">De <input id="dtDe" type="date" style="min-width:auto"></label>
  <label class="sel">Ate <input id="dtAte" type="date" style="min-width:auto"></label>
  <a href="/api/atividades" target="_blank">JSON</a>
  <button id="btnCsv" type="button">Exportar CSV</button>
  <button id="btnPacote" type="button" title="Streams do banco local: reduzidos a ate 1500 pontos quando aplicavel, sem stream de tempo e sem unidades. Ver manifest.json no ZIP.">Exportar dados completos</button>
  <span class="sub">Streams locais: reduzidos (até 1500 pontos), sem tempo e sem unidades</span>
  <span class="sub" id="msgCsv"></span>
  <span id="pacoteAcoes"></span>
</div>
<div class="count" id="count"></div>
<div class="wrap"><table>
  <thead><tr id="head"></tr></thead>
  <tbody id="body"><tr><td class="loading">A carregar...</td></tr></tbody>
</table></div>
"""

JS = r"""
const COLS=[['date','Data',0],['name','Nome',0],['type','Tipo',0],['type_raw','Tipo API',0],
 ['duration_min','Min',1],['distance_km','km',1],['kj','kJ',1],['training_load','TL',1],
 ['avg_watts','W',1],['ftp','FTP',1],['rpe','RPE',1],['xss','XSS',1],
 ['avg_hr','HR',1],['max_hr','HR max',1],['tags','Tags',0],['source','Fonte',0]];
let data=[],CORES={},sortKey='date',sortAsc=false;

function fmt(v,num){if(v===null||v===undefined||v==='')return '-';
 if(num&&typeof v==='number')return v.toLocaleString('pt-PT');return v;}

function filtrados(){
 const q=document.getElementById('search').value.toLowerCase();
 const t=document.getElementById('typeFilter').value;
 const de=document.getElementById('dtDe').value;
 const ate=document.getElementById('dtAte').value;
 const tag=document.getElementById('tagFilter').value;
 return data.filter(r=>(!q||(r.name||'').toLowerCase().indexOf(q)!==-1)
  &&(!t||r.type===t)&&(!de||r.date>=de)&&(!ate||r.date<=ate)
  &&(!tag||(r.tags||[]).indexOf(tag)!==-1));
}

function render(){
 let rows=filtrados();
 rows.sort(function(a,b){var x=a[sortKey],y=b[sortKey];
  if(Array.isArray(x))x=x.join(',');
  if(Array.isArray(y))y=y.join(',');
  if(typeof x==='number')return sortAsc?x-y:y-x;
  x=String(x||'');y=String(y||'');return sortAsc?x.localeCompare(y):y.localeCompare(x);});
 document.getElementById('count').textContent=rows.length+' de '+data.length+' atividades';
 document.getElementById('body').innerHTML=rows.map(r=>
  '<tr class="clickable" onclick="location.href=\'/activity/'+r.id+'\'">'+
  COLS.map(function(c){
   let v;
   if(c[0]==='tags'){
    v=(r.tags&&r.tags.length)
      ? r.tags.map(t=>'<span class="pill">'+t+'</span>').join('') : '-';
   } else {
    v=fmt(r[c[0]],c[2]);
    if(c[0]==='type')v='<span style="color:'+(CORES[r.type]||'#8b949e')+'">'+v+'</span>';
   }
   return '<td class="'+(c[2]?'num':'')+'">'+v+'</td>';}).join('')+'</tr>').join('');
}

function buildHead(){
 document.getElementById('head').innerHTML=COLS.map(c=>
  '<th class="'+(c[2]?'num':'')+'" data-k="'+c[0]+'">'+c[1]+'</th>').join('');
 document.querySelectorAll('th').forEach(th=>th.onclick=function(){
  var k=th.dataset.k;if(sortKey===k)sortAsc=!sortAsc;else{sortKey=k;sortAsc=false;}render();});
}

async function load(){
 buildHead();
 const d=await fetch('/api/atividades').then(r=>r.json());
 if(d.error){document.getElementById('sub').textContent='Erro: '+d.error;return;}
 data=d.activities||[];CORES=d.cores||{};
 const datas=data.map(r=>r.date).filter(Boolean).sort();
 document.getElementById('sub').textContent=
  data.length+' sessoes de '+(datas[0]||'?')+' a '+(datas[datas.length-1]||'?')+
  ' - clica numa linha para ver o detalhe';
 const types=Array.from(new Set(data.map(r=>r.type))).sort();
 document.getElementById('typeFilter').innerHTML='<option value="">Todos os tipos</option>'+
  types.map(t=>'<option>'+t+'</option>').join('');
 const tags=d.tags||[];
 const selTag=document.getElementById('tagFilter');
 if(tags.length){
  selTag.innerHTML='<option value="">Todas as tags ('+tags.length+')</option>'+
   tags.map(t=>'<option>'+t+'</option>').join('');
 } else {
  selTag.innerHTML='<option value="">Sem tags</option>';
  selTag.disabled=true;
 }
 render();
}
function exportarCsv(){
 const msg=document.getElementById('msgCsv');
 const rows=filtrados();
 if(!rows.length){msg.textContent='Nenhuma atividade corresponde aos filtros.';return;}
 msg.textContent='A gerar CSV ('+rows.length+')...';
 fetch('/api/atividades/export.csv',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({ids:rows.map(r=>r.id)})})
  .then(function(res){
   if(!res.ok)return res.json().then(function(j){throw new Error(j.mensagem||j.erro||('HTTP '+res.status));});
   const m=/filename="([^"]+)"/.exec(res.headers.get('Content-Disposition')||'');
   const nome=m?m[1]:'atividades.csv';
   return res.blob().then(function(b){return {b:b,nome:nome};});
  })
  .then(function(x){
   const u=URL.createObjectURL(x.b);const a=document.createElement('a');
   a.href=u;a.download=x.nome;document.body.appendChild(a);a.click();a.remove();
   setTimeout(function(){URL.revokeObjectURL(u);},1000);
   msg.textContent=rows.length+' atividades exportadas';})
  .catch(function(e){msg.textContent='Erro: '+e.message;});
}
document.getElementById('btnCsv').onclick=exportarCsv;
let pacoteJob=null, pacoteTimer=null;
function pacoteMsg(txt){document.getElementById('msgCsv').textContent=txt;}
function pacoteAcoes(){
 const box=document.getElementById('pacoteAcoes'); box.innerHTML='';
 return box;
}
function exportarPacote(){
 const rows=filtrados();
 if(!rows.length){pacoteMsg('Nenhuma atividade corresponde aos filtros.');return;}
 const filtros={search:document.getElementById('search').value,
   typeFilter:document.getElementById('typeFilter').value,
   tagFilter:document.getElementById('tagFilter').value,
   dtDe:document.getElementById('dtDe').value,dtAte:document.getElementById('dtAte').value};
 pacoteAcoes();
 document.getElementById('btnPacote').disabled=true;
 pacoteMsg('A iniciar exportação de '+rows.length+' atividades (chamadas à API, uma por atividade)...');
 fetch('/api/exportacao/iniciar',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({ids:rows.map(r=>r.id),filtros:filtros})})
  .then(function(res){return res.json().then(function(j){
     if(!res.ok)throw new Error(j.erro||('HTTP '+res.status));return j;});})
  .then(function(j){pacoteJob=j.job_id;acompanharPacote();})
  .catch(function(e){pacoteMsg('Erro: '+e.message);document.getElementById('btnPacote').disabled=false;});
}
function acompanharPacote(){
 if(!pacoteJob)return;
 fetch('/api/exportacao/'+pacoteJob+'/estado').then(function(r){return r.json();}).then(function(j){
   const r=j.resumo;
   pacoteMsg('Selecionadas: '+r.selecionadas+' · concluídas: '+r.concluidas+
     ' (sem streams na fonte: '+r.sem_streams+') · com erro: '+r.erros+
     ' · pendentes: '+r.pendentes+' · com fallback local (reduzido): '+r.com_fallback_local+
     (r.rodando?' · em andamento...':''));
   if(r.rodando){pacoteTimer=setTimeout(acompanharPacote,2000);return;}
   document.getElementById('btnPacote').disabled=false;
   const box=pacoteAcoes();
   if(r.pendentes===0){
     const a=document.createElement('a');
     a.href='/api/exportacao/'+pacoteJob+'/download';a.textContent='Baixar pacote ZIP';
     box.appendChild(a);
   }
   if(r.erros||r.pendentes){
     const b=document.createElement('button');b.type='button';b.textContent='Retomar falhas ('+(r.erros+r.pendentes)+')';
     b.onclick=function(){
       fetch('/api/exportacao/'+pacoteJob+'/retomar',{method:'POST'})
        .then(function(res){return res.json().then(function(x){if(!res.ok)throw new Error(x.erro);return x;});})
        .then(function(){document.getElementById('btnPacote').disabled=true;acompanharPacote();})
        .catch(function(e){pacoteMsg('Erro: '+e.message);});
     };
     box.appendChild(b);
   }
   pacoteMsg(pacoteMsgFinal(r));
 }).catch(function(e){pacoteMsg('Erro ao consultar estado: '+e.message);});
}
function pacoteMsgFinal(r){
 const base='Selecionadas: '+r.selecionadas+' · concluídas: '+r.concluidas+' · com erro: '+r.erros+' · pendentes: '+r.pendentes;
 return r.completo_original
   ? base+' · pacote com streams originais completos das atividades selecionadas.'
   : base+' · pacote NÃO completo: há falhas, pendências ou streams reduzidos locais.';
}
document.getElementById('btnPacote').onclick=exportarPacote;
['search','dtDe','dtAte'].forEach(id=>document.getElementById(id).oninput=render);
['typeFilter','tagFilter'].forEach(id=>document.getElementById(id).onchange=render);
load();
"""


def render():
    return page('Atividades', SLUG, BODY, JS)
