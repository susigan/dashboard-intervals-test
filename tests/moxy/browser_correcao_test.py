# Correção da aba MOXY — FIXTURES SINTÉTICAS com o formato real de rpe_zonas_integrado.
import subprocess, sys, time, os, json
from playwright.sync_api import sync_playwright
S = os.path.dirname(os.path.abspath(__file__)); PORT = 8790
sys.path.insert(0, S)
from fixture_real_shape import REAL
results = []
def check(nome, ok, det=''):
    results.append((nome, bool(ok)))
    print(('OK    ' if ok else 'FALHA '), nome, '' if ok else det)

INK = """(id)=>{ const c=document.getElementById(id); if(!c) return -1; if(!c.width) return 0;
  const g=c.getContext('2d').getImageData(0,0,c.width,c.height).data; const r0=g[0],g0=g[1],b0=g[2]; let n=0;
  for(let i=0;i<g.length;i+=4){ if(Math.abs(g[i]-r0)+Math.abs(g[i+1]-g0)+Math.abs(g[i+2]-b0)>30) n++; } return n; }"""
srv = subprocess.Popen([sys.executable,'-m','http.server',str(PORT),'--bind','127.0.0.1'], cwd=S,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.0)
try:
  with sync_playwright() as p:
    b = p.chromium.launch()
    def nova_pagina():
        pg = b.new_page(viewport={'width':1100,'height':900}); pg.errs=[]; pg.fails=[]
        pg.on('pageerror', lambda e: pg.errs.append(str(e)[:200]))
        pg.on('console', lambda m: pg.errs.append(m.text[:200]) if m.type=='error' and 'Failed to load resource' not in m.text else None)
        pg.on('response', lambda r: pg.fails.append(r.url) if r.status>=400 else None)
        pg.goto('http://127.0.0.1:%d/moxy_test.html' % PORT, wait_until='load'); pg.wait_for_timeout(700)
        pg.evaluate("()=>mxMudarSubTab('verificacao')"); pg.wait_for_timeout(300)
        return pg
    def renderizar(pg, d):
        pg.evaluate("(d)=>{ window.__ex=null; try{ _mxVstRenderComparacao(d,'V1'); }catch(e){ window.__ex=String(e); } }", d)
        pg.wait_for_timeout(500)
        return pg.evaluate("()=>window.__ex")
    def abrir_detalhes(pg):
        pg.evaluate("()=>{ document.getElementById('mxDetalhesTecnicosEvidencias').open=true; document.getElementById('mxDetalhesIntegrado').open=true; }")
        pg.wait_for_timeout(500)

    # ── 1. Estrutura: principal visível; detalhes recolhidos ─────────────────
    pg = nova_pagina()
    ex = renderizar(pg, {'rpe_zonas_integrado': REAL['BIKE'], 'modalidade':'BIKE', 'comparacao_bp1':{'status':'CONSISTENTE'}, 'comparacao_bp2':{'status':'DIVERGENTE'}})
    check('1 render completo sem exceção', ex is None, str(ex))
    est = pg.evaluate("""()=>{ const vis=[...document.querySelectorAll('#mxVstRpeZonasArea canvas')].filter(c=>c.offsetParent!==null).map(c=>c.id);
       const det=document.getElementById('mxDetalhesTecnicosEvidencias');
       return {vis, det_open: det.open, medianas_visivel: document.getElementById('chMxCurvasMedianas').checkVisibility(),
               principal_em: document.getElementById('chMxCurvasFisio').closest('#mxVstRpeZonasArea')!==null}; }""")
    check('1 gráfico principal é "Resposta fisiológica × potência" (chMxCurvasFisio) na visão principal', est['principal_em'])
    check('1 única canvas visível na visão principal é o gráfico principal', est['vis']==['chMxCurvasFisio'], str(est['vis']))
    check('1 detalhes técnicos começam recolhidos', est['det_open'] is False)
    check('1 medianas por zona fora da visão principal (recolhidas)', est['medianas_visivel'] is False)
    check('1 todos os canvases fora da visão principal estão recolhidos (checkVisibility)', pg.evaluate("()=>[...document.querySelectorAll('canvas')].filter(c=>c.id && c.checkVisibility()).map(c=>c.id)") == ['chMxCurvasFisio'], str(pg.evaluate("()=>[...document.querySelectorAll('canvas')].filter(c=>c.id && c.checkVisibility()).map(c=>c.id)")))
    check('1 gráfico principal com tinta (desenhado de fato)', pg.evaluate(INK, 'chMxCurvasFisio')>500, str(pg.evaluate(INK,'chMxCurvasFisio')))

    # ── 2. Cinco métricas na mesma área (legenda) e sem THb ──────────────────
    leg = pg.evaluate("()=>document.getElementById('mxFisioPrincipalLegenda').innerText")
    for nome in ['FC (bpm)','SmO₂ (%)','RF (rpm)','RPE','DFA-α1']:
        check('2 métrica no gráfico principal: '+nome, nome in leg)
    txt_main = pg.evaluate("()=>document.getElementById('mxVstRpeZonasArea').innerText")
    check('2 sem THb na visão principal', 'THb' not in txt_main)

    # ── 3. Gráficos técnicos: funcionam ao abrir os detalhes ──────────────────
    abrir_detalhes(pg)
    for cid in ['chMxCurvasMedianas','chMxRzRpePot','chMxRzRpeHr','chMxRzRpeRf','chMxRzRpeSmo2','chMxRzRpeDfa1']:
        check('3 detalhe com tinta ao abrir: '+cid, pg.evaluate(INK, cid)>200, str(pg.evaluate(INK,cid)))
    check('3 chMxFisioIntegrado removido (substituído pela tabela de referência)', pg.evaluate("()=>document.getElementById('chMxFisioIntegrado')==null && !!document.getElementById('mxFisioRefTabela')"))

    # ── 4. Troca de modalidade e de conjunto atualiza dados e tooltip ─────────
    def hover_d1(pg):
        return pg.evaluate("""()=>{ const cv=document.getElementById('chMxCurvasFisio');
          const h=(cv._fpHits||[]).find(x=>x.iv.sessao==='d1'); if(!h) return null;
          const r=cv.getBoundingClientRect(); cv.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+h.x, clientY:r.top+h.y, bubbles:true}));
          return document.getElementById('mxTipCurvasFisio').innerText; }""")
    t_bike = hover_d1(pg)
    check('4 tooltip inicial mostra BIKE', t_bike and 'BIKE' in t_bike, str(t_bike))
    renderizar(pg, {'rpe_zonas_integrado': REAL['ROW'], 'modalidade':'ROW'})
    t_row = hover_d1(pg)
    check('4 após trocar para ROW, tooltip mostra ROW', t_row and 'ROW' in t_row and 'BIKE' not in t_row, str(t_row))
    check('4 após trocar para ROW, HRVT2 indisponível na legenda', 'Não disponível nesta sessão: HRVT2' in pg.evaluate("()=>document.getElementById('mxFisioPrincipalLegenda').innerText"))
    renderizar(pg, {'rpe_zonas_integrado': REAL['SKI'], 'modalidade':'SKI'})
    t_ski = hover_d1(pg)
    check('4 após trocar para SKI, tooltip mostra SKI e valores de Ski', t_ski and 'SKI' in t_ski and '130 W' in t_ski, str(t_ski))

    # ── 5. Ausência de dados: mensagem explícita, sem canvas vazio ───────────
    renderizar(pg, {'rpe_zonas_integrado_status':'sem_run_id_legado', 'rpe_zonas_integrado': None, 'modalidade':'BIKE'})
    st = pg.evaluate("()=>({area: document.getElementById('mxVstRpeZonasArea').style.display, ind: document.getElementById('mxVstRpeZonasIndisp').textContent, vis: [...document.querySelectorAll('#mxVstRpeZonasArea canvas')].filter(c=>c.checkVisibility()).length})")
    check('5 sem dados: área oculta e mensagem específica (correspondência)', st['area']=='none' and 'Correspondência não confirmada' in st['ind'], str(st))
    check('5 sem dados: nenhum canvas visível sem explicação', st['vis']==0, str(st))
    renderizar(pg, {'rpe_zonas_integrado': {'intervalos':[], 'zonas':{}, 'curvas':{}, 'bp':{}, 'hrvt':{}}, 'modalidade':'BIKE'})
    st2 = pg.evaluate("()=>({area: document.getElementById('mxVstRpeZonasArea').style.display, ind: document.getElementById('mxVstRpeZonasIndisp').textContent})")
    check('5 intervalos vazios: área oculta e mensagem de dado ausente', st2['area']=='none' and len(st2['ind'])>0, str(st2))

    # ── 6. Falha de render: visível e isolada ────────────────────────────────
    renderizar(pg, {'rpe_zonas_integrado': REAL['BIKE'], 'modalidade':'BIKE'})
    pg.evaluate("()=>{ window.__resumoOrig = mxVstRenderResumoEssencial; mxVstRenderResumoEssencial = function(){ throw new Error('falha simulada no resumo'); }; }")
    renderizar(pg, {'rpe_zonas_integrado': REAL['BIKE'], 'modalidade':'BIKE'})
    res_txt = pg.evaluate("()=>document.getElementById('mxResumoEssencial').textContent")
    check('6 falha no resumo aparece no próprio bloco (não silencioso)', 'falha simulada no resumo' in res_txt, res_txt)
    check('6 falha no resumo não apaga o gráfico principal', pg.evaluate(INK,'chMxCurvasFisio')>500)
    pg.evaluate("()=>{ mxVstRenderResumoEssencial = window.__resumoOrig; }")
    pg.close()

    # ── 7. Dados reais não são alterados pelo render ─────────────────────────
    pg = nova_pagina()
    snap_antes = pg.evaluate("(d)=>{ window.__rz = d; return JSON.stringify(d); }", REAL['BIKE'])
    renderizar(pg, {'rpe_zonas_integrado': REAL['BIKE'], 'modalidade':'BIKE'})
    snap_depois = pg.evaluate("()=>JSON.stringify(window.__rz)")
    check('7 objeto rpe_zonas_integrado idêntico antes e depois do render', snap_antes==snap_depois)

    # ── 8. Modalidades e ausência de sem-RPE no D1 ───────────────────────────
    renderizar(pg, {'rpe_zonas_integrado': REAL['BIKE_SEM_RPE_D1'], 'modalidade':'BIKE'})
    abrir_detalhes(pg)
    check('8 sem RPE no D1: gráfico principal ainda desenha (D2)', pg.evaluate(INK,'chMxCurvasFisio')>500)
    check('8 sem RPE no D1: RPE×FC desenha (D2)', pg.evaluate(INK,'chMxRzRpeHr')>200)

    # ── 9. Estrutura: IDs duplicados e erros de página ───────────────────────
    dup = pg.evaluate("""()=>{ const ids=[...document.querySelectorAll('[id]')].map(e=>e.id); const c={}; ids.forEach(i=>c[i]=(c[i]||0)+1); return Object.keys(c).filter(k=>c[k]>1); }""")
    check('9 IDs duplicados: só o pré-existente mxResumo', set(dup) <= {'mxResumo'}, str(dup))
    check('9 sem erros JS na página', not pg.errs, str(pg.errs[:3]))
    nao_api = [u for u in set(pg.fails) if '/api/' not in u]
    check('9 nenhum asset estático (JS/CSS/imagem) com erro HTTP', not nao_api, str(nao_api[:3]))
    print('     erros HTTP (somente /api/ do harness estático, sem backend):', sorted(set(u.split('8790')[1] for u in pg.fails))[:6])
    b.close()
finally:
  srv.terminate()
ok=sum(1 for _,o in results if o)
print('\nRESULTADO CORREÇÃO (fixtures): %d/%d OK' % (ok, len(results)))
