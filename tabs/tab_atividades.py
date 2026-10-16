"""Tab Atividades — lista completa, ordenavel e filtravel."""

from flask import Response, jsonify, request
from api_client import fetch_activities, cache_info, norm_tipo, num, kj_da_atividade
import re
import export
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
            'eftp_atividade': num(a.get('icu_pm_ftp')) or None,
            'ftp_configurado': num(a.get('icu_ftp')) or None,
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
 ['avg_watts','W',1],['eftp_atividade','eFTP ativ.',1],['ftp_configurado','FTP conf.',1],['rpe','RPE',1],['xss','XSS',1],
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
function exportarPacote(){
 const msg=document.getElementById('msgCsv');
 const rows=filtrados();
 if(!rows.length){msg.textContent='Nenhuma atividade corresponde aos filtros.';return;}
 const filtros={search:document.getElementById('search').value,
   typeFilter:document.getElementById('typeFilter').value,
   tagFilter:document.getElementById('tagFilter').value,
   dtDe:document.getElementById('dtDe').value,dtAte:document.getElementById('dtAte').value};
 msg.textContent='A gerar pacote ('+rows.length+' atividades)...';
 fetch('/api/atividades/export.zip',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({ids:rows.map(r=>r.id),filtros:filtros})})
  .then(function(res){
   if(!res.ok)return res.json().then(function(j){throw new Error(j.mensagem||j.erro||('HTTP '+res.status));});
   const m=/filename="([^"]+)"/.exec(res.headers.get('Content-Disposition')||'');
   const nome=m?m[1]:'pacote_atividades.zip';
   const erros=res.headers.get('X-Erros-Leitura')||'0';
   const semS=res.headers.get('X-Sem-Streams')||'0';
   return res.blob().then(function(b){return {b:b,nome:nome,erros:erros,semS:semS};});
  })
  .then(function(x){
   const u=URL.createObjectURL(x.b);const a=document.createElement('a');
   a.href=u;a.download=x.nome;document.body.appendChild(a);a.click();a.remove();
   setTimeout(function(){URL.revokeObjectURL(u);},1000);
   msg.textContent=rows.length+' atividades exportadas; sem streams locais: '+x.semS+'; erros de leitura: '+x.erros+'. Streams locais estão reduzidos a até 1500 pontos, sem tempo e sem unidades (ver manifest.json).';})
  .catch(function(e){msg.textContent='Erro: '+e.message;});
}
document.getElementById('btnPacote').onclick=exportarPacote;
['search','dtDe','dtAte'].forEach(id=>document.getElementById(id).oninput=render);
['typeFilter','tagFilter'].forEach(id=>document.getElementById(id).onchange=render);
load();
"""


def render():
    return page('Atividades', SLUG, BODY, JS)
