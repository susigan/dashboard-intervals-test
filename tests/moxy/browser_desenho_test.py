# Verificação de DESENHO EFETIVO (pixels) do gráfico principal. Fixtures sintéticos Bike (+ denso sintético).
import subprocess, sys, time, os, hashlib, json
from playwright.sync_api import sync_playwright
S=os.path.dirname(os.path.abspath(__file__)); PORT=8797; sys.path.insert(0,S)
from fixture_real_shape import REAL
results=[]
def check(n,ok,d=''):
    results.append((n,bool(ok))); print(('OK    ' if ok else 'FALHA '), n, '' if ok else d)
CORES={'hr':(255,123,114),'smo2':(63,185,80),'rf':(210,168,255),'rpe':(230,237,243),'dfa1':(242,204,96)}
CONTA_COR="""(args)=>{ const [id,rgb]=args; const c=document.getElementById(id); const g=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
  let n=0; for(let i=0;i<g.length;i+=4){ if(Math.abs(g[i]-rgb[0])<=12 && Math.abs(g[i+1]-rgb[1])<=12 && Math.abs(g[i+2]-rgb[2])<=12) n++; } return n; }"""
ASSINATURA="""(id)=>{ const c=document.getElementById(id); const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
  let h=0; for(let i=0;i<d.length;i+=4){ h=(h*31 + d[i]*3 + d[i+1]*5 + d[i+2]*7)>>>0; } return h; }"""
srv=subprocess.Popen([sys.executable,'-m','http.server',str(PORT),'--bind','127.0.0.1'],cwd=S,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(1)
def dense_rz():
    d=dict(REAL['BIKE']); ivs=[]
    for sess in ['d1','d2']:
        for k,w in enumerate([150,153,156,161,164,168,172,175,180,186,190,197]):
            ivs.append({'sessao':sess,'intervalo':k+1,'potencia':w,'hr':130+k*2+(3 if sess=='d2' else 0),'smo2':70-k*0.5,'respiracao':20+k*0.3,'rpe':4+k*0.3,'dfa1':1.0-k*0.02,'zona':'Z1' if w<180 else 'Z2','t0':k*60,'grupo':'sint'})
    d['intervalos']=ivs; return d
try:
  with sync_playwright() as p:
    b=p.chromium.launch()
    def pagina(rz):
        pg=b.new_page(viewport={'width':1100,'height':900}); pg.errs=[]
        pg.on('pageerror', lambda e: pg.errs.append(str(e)[:150]))
        pg.goto('http://127.0.0.1:%d/moxy_test.html'%PORT,wait_until='load'); pg.wait_for_timeout(500)
        pg.evaluate("()=>mxMudarSubTab('verificacao')"); pg.wait_for_timeout(200)
        pg.evaluate("(d)=>_mxVstRenderComparacao(d,'V1')",rz); pg.wait_for_timeout(700)
        return pg
    def clicar(pg,k): pg.click("#mxFpControles [data-fp='%s']"%k); pg.wait_for_timeout(250)
    def cor(pg,k): return pg.evaluate(CONTA_COR,['chMxCurvasFisio',list(CORES[k])])
    def sig(pg): return pg.evaluate(ASSINATURA,'chMxCurvasFisio')

    pg=pagina({'rpe_zonas_integrado':REAL['BIKE'],'modalidade':'BIKE','vst_activity_id':'V1'})
    s0=sig(pg); c0={k:cor(pg,k) for k in CORES}
    check('D padrão: todas as cores de métrica aparecem no desenho', all(c0[k]>0 for k in CORES), str(c0))
    clicar(pg,'hr')
    c1={k:cor(pg,k) for k in CORES}
    check('D ocultar FC: a cor de FC some do desenho (0 pixels)', c1['hr']==0, str(c1))
    check('D ocultar FC: as outras cores permanecem no desenho', all(c1[k]>0 for k in CORES if k!='hr'), str(c1))
    check('D ocultar FC: o desenho muda (assinatura diferente)', sig(pg)!=s0)
    clicar(pg,'hr')
    check('D reexibir FC: desenho volta exatamente ao padrão (mesma assinatura)', sig(pg)==s0, '')
    for k in ['smo2','rf','rpe','dfa1']:
        clicar(pg,k); ck=cor(pg,k)
        check('D ocultar %s: cor some do desenho' % k, ck==0, str(ck))
        clicar(pg,k)
    pg.close()

    pg=pagina({'rpe_zonas_integrado':dense_rz(),'modalidade':'BIKE','vst_activity_id':'V1'})
    sm=sig(pg)
    pg.uncheck('#mxFpMedianas'); pg.wait_for_timeout(250)
    sem_med=sig(pg)
    check('D densa: medianas ligadas alteram o desenho em relação a desligadas', sm!=sem_med)
    pg.check('#mxFpMedianas'); pg.wait_for_timeout(250)
    pg.check('#mxFpRolling'); pg.wait_for_timeout(250)
    sr=sig(pg)
    check('D densa: média móvel ligada altera o desenho (com medianas)', sr!=sm)
    pg.select_option('#mxFpJanela','10'); pg.wait_for_timeout(250)
    check('D densa: janela diferente altera o desenho', sig(pg)!=sr)
    pg.click('#mxFpReset'); pg.wait_for_timeout(250)
    check('D densa: restaurar padrão devolve exatamente o desenho padrão', sig(pg)==sm)
    check('D sem erros JS', not pg.errs, str(pg.errs[:2]))
    pg.close()

    pg=pagina({'rpe_zonas_integrado':REAL['BIKE'],'modalidade':'BIKE','vst_activity_id':'V1'})
    pg.set_viewport_size({'width':380,'height':900}); pg.wait_for_timeout(500)
    pg.evaluate("()=>{ const cv=document.getElementById('chMxCurvasFisio'); cv._fpHits=cv._fpHits; }")
    pg.evaluate("(d)=>_mxVstRenderComparacao(d,'V1')",{'rpe_zonas_integrado':REAL['BIKE'],'modalidade':'BIKE','vst_activity_id':'V1'}); pg.wait_for_timeout(600)
    check('D 380 px: cores de todas as métricas aparecem no desenho', all(cor(pg,k)>0 for k in CORES), str({k:cor(pg,k) for k in CORES}))
    pg.close()
    b.close()
finally: srv.terminate()
print('\nRESULTADO DESENHO EFETIVO (pixels): %d/%d OK' % (sum(o for _,o in results),len(results)))
