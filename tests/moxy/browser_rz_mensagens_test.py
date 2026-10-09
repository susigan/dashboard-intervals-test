import subprocess, sys, time, os, json
from playwright.sync_api import sync_playwright
S=os.path.dirname(os.path.abspath(__file__)); PORT=8772
RZ={'intervalos':[{'sessao':'d1','intervalo':1,'zona':'Z2','potencia':190,'rpe':5,'hr':150,'respiracao':30,'smo2':59,'thb':None,'dfa1':0.9,'t0':100,'grupo':'moxy'}],
    'zonas':{},'curvas':{},'bp':{'bp1':{'watts':188.6,'hr_interpolado':None},'bp2':{'watts':235.5,'hr_interpolado':None}},
    'hrvt':{},'comparacao_bp_hrvt':{},'day1_vs_day2':{},'meta':{'modalidade':'BIKE'},'limitacoes':[]}
res=[]
def check(n,ok,d=''):
    res.append(bool(ok)); print(('OK   ' if ok else 'FALHA'),n,'' if ok else d)
srv=subprocess.Popen([sys.executable,'-m','http.server',str(PORT),'--bind','127.0.0.1'],cwd=S,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(1)
try:
 with sync_playwright() as p:
  b=p.chromium.launch(); pg=b.new_page(viewport={'width':1280,'height':900})
  errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)[:160]))
  def novo():
    pg.goto('http://127.0.0.1:%d/moxy_test.html'%PORT, wait_until='load'); pg.wait_for_timeout(400)
    pg.evaluate("()=>mxMudarSubTab('verificacao')"); pg.wait_for_timeout(150)
  def nota(d):
    pg.evaluate("(d)=>mxVstRenderRpeZonas(d)", d); pg.wait_for_timeout(120)
    return pg.evaluate("()=>({txt:document.getElementById('mxVstRpeZonasIndisp').textContent, ind:document.getElementById('mxVstRpeZonasIndisp').style.display, area:document.getElementById('mxVstRpeZonasArea').style.display})")
  novo()
  casos=[('sem_run_id_legado','Correspondência não confirmada: este conjunto foi salvo sem'),
         ('run_id_divergente','pertence a outra execução'),
         ('sem_canonico','Dado ausente: não há resultado canónico'),
         ('sem_analise_canonica','não contém análise integrada'),
         ('erro_leitura','falha ao ler o banco canónico')]
  for st,frag in casos:
    r=nota({'status':'ok','rpe_zonas_integrado_status':st})
    check(f'nota especifica para {st}', frag in r['txt'] and r['ind']=='' and r['area']=='none', json.dumps(r,ensure_ascii=False)[:200])
  r=nota({'status':'ok'})
  check('resposta sem o campo de status: nao fica vazia', 'Dado ausente: a resposta da API' in r['txt'] and r['ind']=='', json.dumps(r,ensure_ascii=False)[:200])
  r=nota({'status':'ok','rpe_zonas_integrado':RZ,'rpe_zonas_integrado_status':'ok'})
  check('com dados: nota escondida e area visivel', r['ind']=='none' and r['area']=='', json.dumps(r,ensure_ascii=False)[:200])
  # falha de renderização: forçar exceção no renderizador e chamar a orquestração real
  novo()
  pg.evaluate("()=>{window.mxVstRenderRpeZonas=function(){throw new Error('falha-forcada');}}")
  pg.evaluate("(d)=>_mxVstRenderComparacao(d,'V1')", {'status':'ok','rpe_zonas_integrado':RZ,'rpe_zonas_integrado_status':'ok'})
  pg.wait_for_timeout(200)
  r=pg.evaluate("()=>({txt:document.getElementById('mxVstRpeZonasIndisp').textContent, ind:document.getElementById('mxVstRpeZonasIndisp').style.display, area:document.getElementById('mxVstRpeZonasArea').style.display})")
  check('falha de renderizacao vira explicacao visivel', 'falha de renderização' in r['txt'] and r['ind']=='' and r['area']=='none', json.dumps(r,ensure_ascii=False)[:200])
  # erro de API ao ler o conjunto salvo
  novo()
  pg.evaluate("()=>{window.mxFetchJson=function(){return Promise.reject(new Error('HTTP 500'));}}")
  pg.evaluate("()=>mxVstGarantirOpcao(document.getElementById('mxVstSelect'),'V1','VST V1')")
  pg.evaluate("()=>mxVstCarregarConjunto('V1')"); pg.wait_for_timeout(300)
  r=pg.evaluate("()=>({txt:document.getElementById('mxVstRpeZonasIndisp').textContent, ind:document.getElementById('mxVstRpeZonasIndisp').style.display, area:document.getElementById('mxVstRpeZonasArea').style.display})")
  check('erro de API mostra explicacao na area do grafico', 'erro de API' in r['txt'] and r['ind']=='' and r['area']=='none', json.dumps(r,ensure_ascii=False)[:200])
  check('sem erros JS nao tratados', not errs, '; '.join(errs[:3]))
  b.close()
finally: srv.terminate()
print(f'\nRESULTADO MENSAGENS RZ NO NAVEGADOR: {sum(res)}/{len(res)} OK')
