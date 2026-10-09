# Controles da visão principal (MOXY): FIXTURES SINTÉTICAS Bike/Row/Ski. Nunca dados de produção.
import subprocess, sys, time, os
from playwright.sync_api import sync_playwright
S = os.path.dirname(os.path.abspath(__file__)); PORT = 8791
sys.path.insert(0, S)
from fixture_real_shape import REAL
results = []
def check(nome, ok, det=''):
    results.append((nome, bool(ok)))
    print(('OK    ' if ok else 'FALHA '), nome, '' if ok else det)
srv = subprocess.Popen([sys.executable,'-m','http.server',str(PORT),'--bind','127.0.0.1'], cwd=S,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.0)
HITS = "()=>{const cv=document.getElementById('chMxCurvasFisio'); const h=cv._fpHits||[]; const c={}; h.forEach(x=>{c[x.k]=(c[x.k]||0)+1}); return {total:h.length, por:c};}"
try:
  with sync_playwright() as p:
    b = p.chromium.launch()
    def nova_pagina():
        pg = b.new_page(viewport={'width':1100,'height':900}); pg.errs=[]
        pg.on('pageerror', lambda e: pg.errs.append(str(e)[:200]))
        pg.on('console', lambda m: pg.errs.append(m.text[:200]) if m.type=='error' and 'Failed to load resource' not in m.text else None)
        pg.goto('http://127.0.0.1:%d/moxy_test.html' % PORT, wait_until='load'); pg.wait_for_timeout(700)
        pg.evaluate("()=>mxMudarSubTab('verificacao')"); pg.wait_for_timeout(300)
        return pg
    def renderizar(pg, d):
        pg.evaluate("(d)=>{ window.__ex=null; try{ _mxVstRenderComparacao(d,'V1'); }catch(e){ window.__ex=String(e); } }", d)
        pg.wait_for_timeout(500)
        return pg.evaluate("()=>window.__ex")
    def clicar(pg, k):
        pg.click("#mxFpControles [data-fp='%s']" % k); pg.wait_for_timeout(250)

    # ── A. Fixture Bike: controles e padrão ──────────────────────────────────
    pg = nova_pagina()
    ex = renderizar(pg, {'rpe_zonas_integrado': REAL['BIKE'], 'modalidade':'BIKE', 'vst_activity_id':'V1'})
    check('A render Bike sem exceção', ex is None, str(ex))
    check('A cinco botões de métrica', pg.evaluate("()=>document.querySelectorAll('#mxFpControles [data-fp]').length")==5)
    st = pg.evaluate("()=>({med:document.getElementById('mxFpMedianas').checked, rol:document.getElementById('mxFpRolling').checked, jan:document.getElementById('mxFpJanela').disabled, val:document.getElementById('mxFpJanela').value})")
    check('A padrão: medianas ligadas, média móvel desligada, janela desabilitada', st['med'] and not st['rol'] and st['jan'], str(st))
    base = pg.evaluate(HITS)
    check('A padrão: pontos das cinco métricas medidos', base['total']>0 and all(base['por'].get(k,0)>0 for k in ['hr','smo2','rf','rpe','dfa1']), str(base))
    base_ink = pg.evaluate("(id)=>{const c=document.getElementById(id);const g=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=0;i<g.length;i+=4){if(Math.abs(g[i]-g[0])+Math.abs(g[i+1]-g[1])+Math.abs(g[i+2]-g[2])>30)n++;}return n;}", 'chMxCurvasFisio')

    # ── B. Ocultar/reexibir cada métrica: só ela sai (pontos, medianas e média) ──
    clicar(pg,'hr')
    h1 = pg.evaluate(HITS)
    check('B ocultar FC remove pontos de FC', h1['por'].get('hr',0)==0, str(h1))
    check('B ocultar FC mantém as demais', all(h1['por'].get(k,0)>0 for k in ['smo2','rf','rpe','dfa1']), str(h1))
    check('B botão FC marcado como oculto', pg.evaluate("()=>document.querySelector(\"#mxFpControles [data-fp='hr']\").getAttribute('aria-pressed')")=='false')
    check('B legenda deixa de listar FC ativa', 'FC (bpm) ' not in pg.evaluate("()=>document.getElementById('mxFisioPrincipalLegenda').innerText"))
    clicar(pg,'hr')
    h2 = pg.evaluate(HITS)
    check('B reexibir FC restaura exatamente os pontos anteriores', h2==base, str(h2))
    # hover: FC oculta aparece como "(oculta)" quando não está nos pontos
    clicar(pg,'hr')
    pg.evaluate("()=>{const cv=document.getElementById('chMxCurvasFisio'); const h=(cv._fpHits||[]).find(x=>x.iv.sessao==='d1'); if(!h) return; const r=cv.getBoundingClientRect(); cv.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+h.x,clientY:r.top+h.y,bubbles:true}));}")
    pg.wait_for_timeout(150)
    tip = pg.evaluate("()=>document.getElementById('mxTipCurvasFisio').innerText")
    check('B tooltip indica FC oculta', 'FC: ' in tip and '(oculta)' in tip, tip)
    clicar(pg,'hr')

    # ── C. Medianas: separadas por sessão, sem misturar D1 e D2 ─────────────
    med_ok = pg.evaluate("""()=>{ const ivs=(MX_FP_RZ.intervalos||[]).filter(i=>i.potencia!=null);
        const out={};
        ['d1','d2'].forEach(s=>{ ['hr','smo2','rf','rpe','dfa1'].forEach(c=>{
          const bs=_mxFpMedianas(ivs,c,s);
          const soma=bs.reduce((a,b)=>a+b.n,0);
          const esperado=ivs.filter(i=>i.sessao===s && i[c]!=null && isFinite(i[c])).length;
          out[s+'.'+c]=(soma===esperado); }); });
        return out; }""")
    check('C medianas: soma de amostras por faixa = amostras da própria sessão (todas as métricas)', all(med_ok.values()), str(med_ok))
    check('C medianas com a mesma largura de faixa do código (20 W)', pg.evaluate("()=>MX_FP_FAIXA_W")==20)

    # ── D. Média móvel: desligada por padrão; por sessão; janela configurável ─
    pg.check('#mxFpRolling'); pg.wait_for_timeout(200)
    check('D média móvel ligada pelo controle', pg.evaluate("()=>MX_FP_ESTADO.rolling")==True)
    check('D janela habilitada quando média móvel ligada', pg.evaluate("()=>document.getElementById('mxFpJanela').disabled")==False)
    rol_ok = pg.evaluate("""()=>{ const ivs=(MX_FP_RZ.intervalos||[]).filter(i=>i.potencia!=null);
        const a=_mxFpRolling(ivs,'hr','d1',30);
        const b=_mxFpRolling(ivs.filter(i=>i.sessao==='d1'),'hr','d1',30);
        return JSON.stringify(a)===JSON.stringify(b); }""")
    check('D média móvel D1 não depende de amostras de D2 (não mistura sessões)', rol_ok)
    pg.select_option('#mxFpJanela','50'); pg.wait_for_timeout(200)
    check('D janela muda para 50 W', pg.evaluate("()=>MX_FP_ESTADO.janela")==50)
    check('D janela aplicada no redesenho (cv desenhado)', pg.evaluate(HITS)['total']>0)

    # ── E. Restaurar padrão ──────────────────────────────────────────────────
    pg.click('#mxFpReset'); pg.wait_for_timeout(250)
    rst = pg.evaluate("()=>({st:MX_FP_ESTADO, med:document.getElementById('mxFpMedianas').checked, rol:document.getElementById('mxFpRolling').checked, jan:document.getElementById('mxFpJanela').value})")
    check('E restaurar padrão: todas ativas, medianas ligadas, média desligada, janela 30 W',
          all(rst['st']['ativas'].values()) and rst['st']['medianas'] and not rst['st']['rolling'] and rst['st']['janela']==30 and rst['med'] and not rst['rol'] and rst['jan']=='30', str(rst))
    check('E restaurar padrão recupera o mesmo conjunto de pontos', pg.evaluate(HITS)==base, str(pg.evaluate(HITS)))

    # ── F. Sem nenhuma métrica ativa: mensagem, sem erro ─────────────────────
    for k in ['hr','smo2','rf','rpe','dfa1']: clicar(pg,k)
    # erro pré-existente do harness estático: mxVstDesenharRpePots lê /api (sem backend) e recebe HTML 404
    errs_F=[e for e in pg.errs if 'mxVstDesenharRpePots' not in e]
    check('F todas ocultas: nenhum ponto e sem erro JS (exceto o do harness, já existente)', pg.evaluate(HITS)['total']==0 and not errs_F, str(errs_F[:2]))
    pg.click('#mxFpReset'); pg.wait_for_timeout(200)

    # ── G. Modalidade: nunca inventada ───────────────────────────────────────
    pg.evaluate("()=>{ MX_VID=null; MX_VST_MODALIDADE_POR_VST={}; }")
    renderizar(pg, {'rpe_zonas_integrado': REAL['ROW'], 'vst_activity_id':'V9'})
    cv_tip = lambda: pg.evaluate("""()=>{const cv=document.getElementById('chMxCurvasFisio'); const h=(cv._fpHits||[])[0]; if(!h) return null;
        const r=cv.getBoundingClientRect(); cv.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+h.x,clientY:r.top+h.y,bubbles:true}));
        return document.getElementById('mxTipCurvasFisio').innerText;}""")
    pg.wait_for_timeout(100)
    t = cv_tip()
    check('G sem modalidade em lugar nenhum: rótulo honesto', t and 'modalidade não registrada' in t, str(t))
    check('G sem modalidade: nenhuma modalidade inventada', t and 'ROW' not in t and 'BIKE' not in t and 'SKI' not in t, str(t))
    pg.evaluate("()=>{ MX_VST_MODALIDADE_POR_VST={'V9':'ROW'}; }")
    renderizar(pg, {'rpe_zonas_integrado': REAL['ROW'], 'vst_activity_id':'V9'})
    t2 = cv_tip()
    check('G modalidade da lista de conjuntos (V9 = ROW) aparece no tooltip', t2 and 'ROW' in t2, str(t2))
    renderizar(pg, {'rpe_zonas_integrado': REAL['SKI'], 'modalidade':'SKI', 'vst_activity_id':'V9'})
    t3 = cv_tip()
    check('G modalidade do próprio resultado tem prioridade sobre a lista', t3 and 'SKI' in t3 and 'ROW' not in t3, str(t3))

    # ── H. Isolamento de erro por painel RPE×métricas (verificação estática do código) ──
    src_r = pg.evaluate("()=>mxVstRenderRpeZonas.toString()")
    check('H os 5 painéis RPE×métricas passam por _rzSeguro (erro isolado por painel)', src_r.count("_rzSeguro('chMxRzRpe")==5 and "_rzDesenharScatter('chMxRzRpe" not in src_r, str(src_r.count("_rzSeguro('chMxRzRpe")))

    # ── I. Fixtures Row e Ski: controles funcionam sem erro ──────────────────
    for nome, mod in [('ROW','ROW'),('SKI','SKI')]:
        pg2 = nova_pagina()
        renderizar(pg2, {'rpe_zonas_integrado': REAL[nome], 'modalidade':mod, 'vst_activity_id':'V1'})
        n0 = pg2.evaluate(HITS)['total']
        pg2.click("#mxFpControles [data-fp='smo2']"); pg2.wait_for_timeout(200)
        n1 = pg2.evaluate(HITS)['total']
        pg2.click('#mxFpReset'); pg2.wait_for_timeout(200)
        check('I %s: pontos existem e ocultar SmO₂ reduz o total, restaurar devolve' % nome, n0>0 and n1<n0 and pg2.evaluate(HITS)['total']==n0)
        errs_I=[e for e in pg2.errs if 'mxVstDesenharRpePots' not in e]
        check('I %s: sem erros JS (exceto o do harness, já existente)' % nome, not errs_I, str(errs_I[:2]))
        pg2.close()

    # ── K. Medianas: nota no gráfico com poucas amostras; desenhadas com amostras suficientes ──
    pgK = nova_pagina()
    renderizar(pgK, {'rpe_zonas_integrado': REAL['BIKE'], 'modalidade':'BIKE', 'vst_activity_id':'V1'})
    check('K Bike (4 amostras/sessão): nenhuma mediana desenhada, por falta de amostras', pgK.evaluate("()=>document.getElementById('chMxCurvasFisio')._fpMedDesenhadas")==0)
    # fixture SINTÉTICO denso (não é resultado real): 12 intervalos por sessão em 3 faixas de 20 W
    dense=dict(REAL['BIKE'])
    ivs=[]
    for sess in ['d1','d2']:
        for k,w in enumerate([150,153,156,161,164,168,172,175,180,186,190,197]):
            ivs.append({'sessao':sess,'intervalo':k+1,'potencia':w,'hr':130+k*2+(3 if sess=='d2' else 0),'smo2':70-k*0.5,'respiracao':20+k*0.3,'rpe':4+k*0.3,'dfa1':1.0-k*0.02,'zona':'Z1' if w<180 else 'Z2','t0':k*60,'grupo':'sint'})
    dense['intervalos']=ivs
    renderizar(pgK, {'rpe_zonas_integrado': dense, 'modalidade':'BIKE', 'vst_activity_id':'V1'})
    check('K denso sintético: medianas desenhadas quando há amostras suficientes', pgK.evaluate("()=>document.getElementById('chMxCurvasFisio')._fpMedDesenhadas")>0)
    pgK.check('#mxFpRolling'); pgK.wait_for_timeout(200)
    check('K média móvel ligada: não quebra o gráfico denso', pgK.evaluate(HITS)['total']>0 and not [e for e in pgK.errs if 'mxVstDesenharRpePots' not in e])
    pgK.close()

    # ── J. Gráfico principal segue sendo o único visível na visão principal ──
    pg3 = nova_pagina()
    renderizar(pg3, {'rpe_zonas_integrado': REAL['BIKE'], 'modalidade':'BIKE'})
    vis = pg3.evaluate("()=>[...document.querySelectorAll('#mxVstRpeZonasArea canvas')].filter(c=>c.checkVisibility()).map(c=>c.id)")
    check('J visão principal: só chMxCurvasFisio visível', vis==['chMxCurvasFisio'], str(vis))

    b.close()
finally:
    srv.terminate()
ok=sum(1 for _,o in results if o)
print('\nRESULTADO CONTROLES (fixtures Bike/Row/Ski): %d/%d OK' % (ok,len(results)))
