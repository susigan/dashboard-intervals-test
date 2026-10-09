import json, sys, subprocess, time, os
from playwright.sync_api import sync_playwright
S = os.path.dirname(os.path.abspath(__file__))
PORT = 8768
results = []
def check(nome, ok, det=''):
    results.append((nome, bool(ok)))
    print(('OK   ' if ok else 'FALHA'), nome, '' if ok else det)

def rz(label, b1, b2, h1c, h1s, h2):
    iv = []
    for i,(w,rpe,hr,rf,sm,df) in enumerate([(b1-25,4,146.5,30,60,0.95),(b1,6,154.8,31,59,0.85),(b2-5,8,159.3,33,57,0.7)]):
        iv.append({'sessao':'d1','intervalo':i+1,'zona':'Z2','potencia':w,'rpe':rpe,'hr':hr,'respiracao':rf,
                   'smo2':sm,'thb':None,'dfa1':df,'t0':100+i*300,'grupo':'moxy'})
    for i,(w,rpe,hr,rf,sm,df) in enumerate([(b1+1.5,5,150.1,30.5,59.5,0.9),(b1+2.5,6,155.2,31.2,58.8,0.8),(b2,9,166.0,34,55,0.6)]):
        iv.append({'sessao':'d2','intervalo':i+1,'zona':'Z2' if i<2 else 'Z3','potencia':w,'rpe':rpe,'hr':hr,
                   'respiracao':rf,'smo2':sm,'thb':None,'dfa1':df,'t0':1000+i*300,'grupo':'bp1' if i<2 else 'bp2'})
    return {'intervalos':iv,'zonas':{},'curvas':{},'bp':{'bp1':{'watts':b1,'hr_interpolado':151.2},
                                                     'bp2':{'watts':b2,'hr_interpolado':163.0}},
            'hrvt':{'HRVT1c':{'watts':h1c,'heartrate':149.1,'alpha_label':'individualizado'},
                    'HRVT1s':{'watts':h1s,'heartrate':154.7,'alpha_label':'0.75'},
                    'HRVT2':{'watts':h2,'heartrate':169.1,'alpha_label':'0.50'}},
            'comparacao_bp_hrvt':{},'day1_vs_day2':{},
            'meta':{'n_d1':3,'n_d2':3,'n_total':6,'bp1_alvo_w':b1,'bp2_alvo_w':b2},'limitacoes':['teste '+label]}

FIX = {'BIKE': rz('BIKE', 188.6, 235.5, 196.0, 205.0, 240.0),
       'ROW':  rz('ROW', 150.0, 200.0, 158.0, 166.0, None),     # HRVT2 indisponível
       'SKI':  rz('SKI', 170.0, 215.0, 180.0, 190.0, 220.0)}

srv = subprocess.Popen([sys.executable,'-m','http.server',str(PORT),'--bind','127.0.0.1'], cwd=S,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.0)
try:
  with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width':1280,'height':1000})
    errors=[]; pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.goto('http://127.0.0.1:%d/moxy_test.html' % PORT, wait_until='load')
    pg.wait_for_timeout(800)
    pg.evaluate("() => mxMudarSubTab('verificacao')"); pg.wait_for_timeout(300)

    def render(mod):
        pg.evaluate("(a) => { mxVstRenderRpeZonas({rpe_zonas_integrado: a.rz, modalidade: a.mod}); }", {'rz':FIX[mod],'mod':mod})
        pg.wait_for_timeout(450)

    def estado():
        return pg.evaluate("""() => {
          const cv=document.getElementById('chMxFisioIntegrado');
          const d=document.getElementById('mxDetalhesFisioIntegrado');
          let px=0; if(cv && cv.width){ const g=cv.getContext('2d').getImageData(0,0,cv.width,cv.height).data; for(let i=3;i<g.length;i+=4){ if(g[i]>0) px++; } }
          return {open: !!(d&&d.open), px: px, tabela: (document.getElementById('mxFisioRefTabela')||{}).innerText||'',
                  hits: (cv&&cv._fisioHits)? cv._fisioHits.length : 0, mod: (cv&&cv._fisioModalidade)||null};
        }""")

    # 1. Render: a seção técnica começa RECOLHIDA (nova visão principal). O usuário a abre.
    render('BIKE')
    recolhida = pg.evaluate("() => { const d=document.getElementById('mxDetalhesTecnicosEvidencias'); return !!d && d.open===false; }")
    check('1 detalhes técnicos e evidências começam recolhidos', recolhida)
    pg.evaluate("() => { document.getElementById('mxDetalhesTecnicosEvidencias').open=true; }"); pg.wait_for_timeout(250)
    pg.evaluate("() => { document.getElementById('mxDetalhesIntegrado').open=true; }"); pg.wait_for_timeout(250)
    pg.evaluate("() => { document.getElementById('mxDetalhesFisioIntegrado').open=true; }"); pg.wait_for_timeout(350)
    e = estado()
    check('1 seção de evidência técnica (D1/D2) aberta pelo usuário', e['open'])
    check('1 gráfico desenhado (pixels > 0)', e['px'] > 0, str(e['px']))
    check('1 tabela mostra BP1 com BPM marcado como interpolado', 'mesma sessão' in e['tabela'] and 'BP1' in e['tabela'])
    linha_hrvt1 = [l for l in e['tabela'].split('\n') if 'HRVT1 indiv' in l]
    check('1 HRVT1 individualizado = 196 W e 149.1 bpm (tabela)', bool(linha_hrvt1) and '196' in linha_hrvt1[0] and '149.1' in linha_hrvt1[0], e['tabela'][:300])
    check('1 modalidade registrada no gráfico = BIKE', e['mod'] == 'BIKE', str(e['mod']))

    # 2. Tooltip: mesmo intervalo, D2 (VST) e D1 (MOXY)
    def hover_hit(idx):
        # o canvas pode estar abaixo da dobra: rolar até ele antes de mover o mouse
        pg.evaluate("() => document.getElementById('chMxFisioIntegrado').scrollIntoView({block:'center'})")
        pg.wait_for_timeout(150)
        r = pg.evaluate("(i) => { const cv=document.getElementById('chMxFisioIntegrado'); const r=cv.getBoundingClientRect(); const h=cv._fisioHits[i]; return {x:r.left+h.x, y:r.top+r.height/2, iv:h.iv}; }", idx)
        pg.mouse.move(r['x'], r['y']); pg.wait_for_timeout(150)
        return r
    hits = pg.evaluate("() => document.getElementById('chMxFisioIntegrado')._fisioHits.map(h=>({sessao:h.iv.sessao, w:h.iv.potencia, hr:h.iv.hr, rpe:h.iv.rpe}))")
    i_d2 = next(i for i,h in enumerate(hits) if h['sessao']=='d2')
    hover_hit(i_d2)
    tip = pg.evaluate("() => { const t=document.getElementById('mxTipFisioIntegrado'); return {vis: t.style.display, txt: t.innerText}; }")
    alvo = hits[i_d2]
    check('2 tooltip aparece ao passar sobre um intervalo', tip['vis']=='block', str(tip))
    check('2 tooltip mostra VST · Dia 2 e o watts/FC/RPE do MESMO intervalo',
          'VST · Dia 2' in tip['txt'] and ('%d W' % round(alvo['w'])) in tip['txt'] and ('%d bpm' % round(alvo['hr'])) in tip['txt']
          and ('RPE %s' % (('%.1f' % alvo['rpe']))) in tip['txt'], tip['txt'][:200])
    check('2 tooltip mostra modalidade', 'BIKE' in tip['txt'], tip['txt'][:60])
    i_d1 = next(i for i,h in enumerate(hits) if h['sessao']=='d1')
    hover_hit(i_d1)
    tip = pg.evaluate("() => document.getElementById('mxTipFisioIntegrado').innerText")
    check('2 tooltip de Dia 1 mostra MOXY · Dia 1', 'MOXY · Dia 1' in tip, tip[:80])

    # 3. Troca de modalidade: ROW (HRVT2 indisponível) sem restos da BIKE
    render('ROW'); e = estado()
    check('3 Row: HRVT2 aparece como indisponível (não zero, não estimado)', 'HRVT2' in e['tabela'] and 'indisponível' in e['tabela'], e['tabela'][-160:])
    check('3 Row: valores da Bike não permanecem (196 W e 240 não aparecem)', '196 W' not in e['tabela'] and '240 W' not in e['tabela'], e['tabela'][:200])
    check('3 Row: modalidade registrada = ROW', e['mod']=='ROW', str(e['mod']))
    check('3 Row: gráfico redesenhado (pixels > 0)', e['px']>0, str(e['px']))
    render('SKI'); e = estado()
    check('3 Ski: modalidade e valores de Ski (180 W, 220 W) sem restos de Row/Bike',
          e['mod']=='SKI' and '180 W' in e['tabela'] and '196 W' not in e['tabela'] and '158 W' not in e['tabela'], e['tabela'][:160])

    # 4. Fechar e reabrir a seção: redesenho
    pg.evaluate("() => { const d=document.getElementById('mxDetalhesFisioIntegrado'); d.open=false; }"); pg.wait_for_timeout(150)
    pg.evaluate("() => { const d=document.getElementById('mxDetalhesFisioIntegrado'); d.open=true; }"); pg.wait_for_timeout(500)
    e = estado()
    check('4 ao reabrir a seção o gráfico volta a ter pixels', e['open'] and e['px']>0, str(e['px']))
    # tooltip continua funcionando após reabrir
    hits = pg.evaluate("() => document.getElementById('chMxFisioIntegrado')._fisioHits.length")
    hover_hit(0); tip = pg.evaluate("() => document.getElementById('mxTipFisioIntegrado').style.display")
    check('4 tooltip funciona após reabrir', tip=='block' and hits>0, str(tip))

    check('sem erros JS na página', not errors, '; '.join(errors[:3]))
    b.close()
finally:
    srv.terminate()
print(f'\nRESULTADO BROWSER GRÁFICO: {sum(1 for _,v in results if v)}/{len(results)} OK')
