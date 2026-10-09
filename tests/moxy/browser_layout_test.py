# Layout da visão principal em várias larguras. FIXTURE SINTÉTICO (Bike); sem dados reais.
import subprocess, sys, time, os
from playwright.sync_api import sync_playwright
S=os.path.dirname(os.path.abspath(__file__)); PORT=8794; sys.path.insert(0,S)
from fixture_real_shape import REAL
results=[]
def check(n,ok,d=''):
    results.append((n,bool(ok))); print(('OK    ' if ok else 'FALHA '), n, '' if ok else d)
ANC = """()=>{ const leg=document.getElementById('mxFisioPrincipalLegenda'); const out=[];
 let el=leg.parentElement; while(el && el!==document.body){ const cs=getComputedStyle(el);
   if(cs.overflow!=='visible' && (cs.overflowY!=='visible'||cs.overflowX!=='visible')) out.push(el.id||el.className||el.tagName);
   el=el.parentElement; } return out; }"""
srv=subprocess.Popen([sys.executable,'-m','http.server',str(PORT),'--bind','127.0.0.1'],cwd=S,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(1)
try:
  with sync_playwright() as p:
    b=p.chromium.launch()
    for W in [380,768,1100]:
        pg=b.new_page(viewport={'width':W,'height':900}); errs=[]
        pg.on('pageerror', lambda e: errs.append(str(e)[:150]))
        pg.goto('http://127.0.0.1:%d/moxy_test.html'%PORT,wait_until='load'); pg.wait_for_timeout(600)
        pg.evaluate("()=>mxMudarSubTab('verificacao')"); pg.wait_for_timeout(300)
        pg.evaluate("(d)=>_mxVstRenderComparacao(d,'V1')",{'rpe_zonas_integrado':REAL['BIKE'],'modalidade':'BIKE','vst_activity_id':'V1'})
        pg.wait_for_timeout(800)
        check('%dpx sem rolagem horizontal da página' % W, pg.evaluate("()=>document.documentElement.scrollWidth<=window.innerWidth+1"), pg.evaluate("()=>[document.documentElement.scrollWidth,window.innerWidth]"))
        check('%dpx gráfico desenhado (não "largura insuficiente")' % W, pg.evaluate("()=>(document.getElementById('chMxCurvasFisio')._fpHits||[]).length")>0)
        check('%dpx tinta no gráfico' % W, pg.evaluate("(id)=>{const c=document.getElementById(id);const g=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=0;i<g.length;i+=4){if(Math.abs(g[i]-g[0])+Math.abs(g[i+1]-g[1])+Math.abs(g[i+2]-g[2])>30)n++;}return n;}",'chMxCurvasFisio')>500)
        anc=pg.evaluate(ANC)
        check('%dpx legenda sem ancestral com corte (overflow)' % W, not anc, str(anc))
        ok_leg=pg.evaluate("()=>{const r=document.getElementById('mxFisioPrincipalLegenda').getBoundingClientRect(); return r.height>0 && r.bottom<=document.documentElement.scrollHeight && r.right<=window.innerWidth+1;}")
        check('%dpx legenda inteira dentro da página' % W, ok_leg)
        check('%dpx sem erros JS' % W, not [e for e in errs], str(errs[:2]))
        pg.locator('#mxVstRpeZonasArea').screenshot(path=os.path.join(S,'shot_layout_%dpx.png'%W))
        pg.close()
    b.close()
finally: srv.terminate()
print('\nRESULTADO LAYOUT: %d/%d OK' % (sum(o for _,o in results), len(results)))
