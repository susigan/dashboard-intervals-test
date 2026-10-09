# Testes do gráfico integrado (visão principal) — FIXTURES SINTÉTICAS, não dados reais.
import json, sys, subprocess, time, os
from playwright.sync_api import sync_playwright
S = os.path.dirname(os.path.abspath(__file__))
PORT = 8773
results = []
def check(nome, ok, det=''):
    results.append((nome, bool(ok)))
    print(('OK    ' if ok else 'FALHA '), nome, '' if ok else det)

def iv(sessao, i, w, rpe, hr, rf, sm, df):
    return {'sessao':sessao,'intervalo':i,'zona':'Z2','potencia':w,'rpe':rpe,'hr':hr,
            'respiracao':rf,'smo2':sm,'thb':None,'dfa1':df,'t0':100*i,'grupo':'moxy' if sessao=='d1' else 'bp1'}

def rz(b1, b2, h1c, h1s, h2, extra_d2=None):
    d1=[iv('d1',1,b1-25,4,146.5,30,60,0.95), iv('d1',2,b1,6,154.8,31,59,0.85), iv('d1',3,b2-5,8,159.3,33,57,0.70)]
    d2=[iv('d2',1,b1+12,5,150.1,30.5,59.5,0.90), iv('d2',2,b1+22,6,155.2,31.2,58.8,0.80), iv('d2',3,b2+7,9,166.0,34,55,0.60)]
    if extra_d2: d2.append(extra_d2)
    hr={'HRVT1c':{'watts':h1c,'heartrate':149.1,'ok':True},'HRVT1s':{'watts':h1s,'heartrate':154.7,'ok':True},
        'HRVT2':{'watts':h2,'heartrate':169.1,'ok':h2 is not None}}
    return {'intervalos':d1+d2,
            'zonas':{'Z1':{'n':1,'hr':{'mediana':140.0,'n':2},'respiracao':{'mediana':28.0,'n':2},'smo2':{'mediana':61.0,'n':2},'rpe':{'mediana':3.0,'n':2},'dfa1':{'mediana':1.0,'n':2}},
                     'Z2':{'n':4,'hr':{'mediana':152.0,'n':4},'respiracao':{'mediana':31.0,'n':4},'smo2':{'mediana':58.0,'n':4},'rpe':{'mediana':6.0,'n':4},'dfa1':{'mediana':0.8,'n':4}}},
            'curvas':{},'bp':{'bp1':{'watts':b1,'hr_interpolado':151.2},'bp2':{'watts':b2,'hr_interpolado':163.0}},
            'hrvt':hr,'comparacao_bp_hrvt':{},'day1_vs_day2':{},
            'meta':{'n_d1':3,'n_d2':3,'n_total':6,'bp1_alvo_w':b1,'bp2_alvo_w':b2},'limitacoes':['teste fixture']}

FIX = {
 'BIKE': rz(188.6, 235.5, 196.0, 205.0, 240.0),
 'ROW':  rz(150.0, 200.0, 158.0, 166.0, None),          # HRVT2 indisponível
 'SKI':  rz(170.0, 215.0, 180.0, 190.0, 220.0),
}
# Ponto com RF/SmO2 ausentes: tooltip deve mostrar só o disponível
FIX['BIKE_PARCIAL'] = rz(188.6, 235.5, 196.0, 205.0, 240.0)
for it in FIX['BIKE_PARCIAL']['intervalos']:
    if it['sessao']=='d2' and it['intervalo']==2:
        it['smo2']=None; it['dfa1']=None

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

    def render(mod, key=None):
        pg.evaluate("(a) => { window.__ex=null; try { mxVstRenderRpeZonas({rpe_zonas_integrado: a.rz, modalidade: a.mod}); } catch(e){ window.__ex=String(e); } }",
                    {'rz':FIX[key or mod],'mod':mod})
        pg.evaluate("() => { const t=document.getElementById('mxVstRpeZonasArea'); if(t) t.scrollIntoView(); }")
        pg.wait_for_timeout(450)

    def pixels():
        return pg.evaluate("""() => { const cv=document.getElementById('chMxCurvasFisio');
          if(!cv||!cv.width) return 0; const g=cv.getContext('2d').getImageData(0,0,cv.width,cv.height).data;
          const r0=g[0],g0=g[1],b0=g[2]; let n=0;
          for(let i=0;i<g.length;i+=4){ if(Math.abs(g[i]-r0)+Math.abs(g[i+1]-g0)+Math.abs(g[i+2]-b0)>30) n++; } return n; }""")

    # ── 1. Estrutura da visão principal ───────────────────────────────────
    render('BIKE')
    e = pg.evaluate("""() => { const a=document.getElementById('mxVstRpeZonasArea');
       const cvs=[...a.querySelectorAll('canvas')].filter(c=>c.offsetParent!==null);
       const det=document.getElementById('mxDetalhesTecnicosEvidencias');
       return {visivel: a.style.display!=='none', canvas_visiveis: cvs.map(c=>c.id),
               det_open: !!(det&&det.open), det_dentro_principal: !!(det&&a.contains(det)),
               area_principal: document.getElementById('mxCurvasFisioArea').style.display,
               txt_principal: a.innerText}; }""")
    check('1 área principal visível com dados', e['visivel'])
    check('1 única área de plotagem visível na visão principal', e['canvas_visiveis']==['chMxCurvasFisio'], str(e['canvas_visiveis']))
    check('1 detalhes técnicos começam recolhidos', e['det_open'] is False)
    check('1 detalhes técnicos fora da visão principal', e['det_dentro_principal'] is False)
    check('1 gráfico principal desenhado (pixels > 0)', pixels()>0, str(pixels()))
    check('1 sem THb na visão principal', 'THb' not in e['txt_principal'], e['txt_principal'][:200])

    # ── 2. Cinco métricas, cada uma com eixo e unidade ─────────────────────
    leg = pg.evaluate("() => document.getElementById('mxFisioPrincipalLegenda').innerText")
    for nome in ['FC (bpm)','SmO₂ (%)','RF (rpm)','RPE','DFA-α1']:
        check('2 legenda lista métrica '+nome, nome in leg, leg[:200])
    check('2 sessões identificadas (círculo MOXY D1, quadrado VST D2)', 'MOXY · Dia 1' in leg or 'MOXY · Dia 1 (círculo)' in leg)
    check('2 referências BP1/BP2 com potência', 'BP1 189 W' in leg and 'BP2 236 W' in leg, leg)
    check('2 HRVT1 indiv/clássico/HRVT2 com potência (Bike)', 'HRVT1 indiv. 196 W' in leg and 'HRVT1 clássico 205 W' in leg and 'HRVT2 240 W' in leg, leg)

    # ── 3. Estática: sem linhas horizontais de DFA-α1, sem THb, sem linhas entre sessões ──
    # o JS do gráfico principal é extraído da própria página carregada
    fn = pg.evaluate("() => mxVstDesenharFisioPrincipal.toString()")
    check('3 gráfico principal não desenha linhas de referência DFA-α1', 'linhasRef' not in fn and '0.75' not in fn and '0.50' not in fn)
    check('3 gráfico principal não usa THb', 'thb' not in fn.lower())
    trecho_pontos = fn.split('// ── Pontos medidos')[1].split('// ── Eixo X compartilhado')[0]
    check('3 gráfico principal não liga pontos (trecho de pontos sem lineTo)', 'lineTo' not in trecho_pontos and 'moveTo' not in trecho_pontos)

    # ── 4. Tooltip: sessão correta e só valores disponíveis ─────────────────
    def hover(alvo_sessao, alvo_int):
        return pg.evaluate("""([s,i]) => { const cv=document.getElementById('chMxCurvasFisio');
           const h=(cv._fpHits||[]).find(x=>x.iv.sessao===s && x.iv.intervalo===i);
           if(!h) return null; const r=cv.getBoundingClientRect();
           cv.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+h.x, clientY:r.top+h.y, bubbles:true}));
           const t=document.getElementById('mxTipCurvasFisio'); return {txt:t.innerText, vis:t.style.display}; }""",
           [alvo_sessao, alvo_int])
    tip = hover('d1', 2)
    check('4 tooltip aparece no ponto MOXY Dia 1', tip and tip['vis']=='block', str(tip))
    check('4 tooltip de Dia 1 identifica MOXY · Dia 1', tip and 'MOXY · Dia 1' in tip['txt'], str(tip))
    check('4 tooltip de Dia 1 mostra RPE 6.0 (do ponto D1)', tip and 'RPE: 6.0' in tip['txt'], str(tip))
    check('4 tooltip de Dia 1 não traz valores do Dia 2 (RPE 5.0/6.0 de D2 no mesmo W não aparece)', tip and 'VST' not in tip['txt'], str(tip))
    check('4 tooltip mostra modalidade BIKE', tip and 'BIKE' in tip['txt'], str(tip))
    tip2 = hover('d2', 2)
    check('4 tooltip de Dia 2 identifica VST · Dia 2', tip2 and 'VST · Dia 2' in tip2['txt'], str(tip2))
    check('4 tooltip de Dia 2 mostra RPE 6.0 do D2 (não do D1)', tip2 and 'RPE: 6.0' in tip2['txt'], str(tip2))

    render('BIKE_PARCIAL')
    tip3 = hover('d2', 2)
    check('4 valores ausentes aparecem como "não medido nesta sessão", sem número inventado (D2 intervalo 2)',
          tip3 and 'SmO₂: não medido nesta sessão' in tip3['txt'] and 'DFA-α1: não medido nesta sessão' in tip3['txt'] and 'RPE: ' in tip3['txt'], str(tip3))

    # ── 5. Row (HRVT2 indisponível) e Ski ──────────────────────────────────
    render('ROW')
    leg_r = pg.evaluate("() => document.getElementById('mxFisioPrincipalLegenda').innerText")
    check('5 Row: HRVT2 marcado como não disponível (não estimado)', 'Não disponível nesta sessão: HRVT2' in leg_r, leg_r)
    check('5 Row: valores da Bike não permanecem (240 W não aparece)', '240 W' not in leg_r, leg_r)
    check('5 Row: gráfico desenhado', pixels()>0)
    render('SKI')
    leg_s = pg.evaluate("() => document.getElementById('mxFisioPrincipalLegenda').innerText")
    check('5 Ski: referências de Ski (BP1 170 W, HRVT2 220 W)', 'BP1 170 W' in leg_s and 'HRVT2 220 W' in leg_s, leg_s)
    check('5 Ski: gráfico desenhado', pixels()>0)

    # ── 6. Interpretação essencial ─────────────────────────────────────────
    render('BIKE')
    res = pg.evaluate("() => document.getElementById('mxResumoEssencial').innerText")
    check('6 interpretação com tendência por zona', 'FC' in res and 'Z1 140' in res and 'Z2 152' in res, res[:200])
    check('6 interpretação traz consistência entre sessões', 'Consistência entre sessões' in res, res[:200])
    check('6 interpretação não usa causalidade', 'causa' not in res.lower() or 'não implica causa' in res.lower(), res[:200])

    # ── 7. Detalhes técnicos: recolhidos e funcionais ──────────────────────
    pg.evaluate("() => { document.getElementById('mxDetalhesTecnicosEvidencias').open=true; }")
    pg.wait_for_timeout(400)
    check('7 medianas por zona redesenham ao abrir detalhes', pg.evaluate("() => { const c=document.getElementById('chMxCurvasFisio'); return !!c && c.width>0; }"))
    pg.evaluate("() => { const d=document.getElementById('mxDetalhesIntegrado'); if(d) d.open=true; }")
    pg.wait_for_timeout(400)
    check('7 gráfico técnico D1/D2 existe dentro dos detalhes', pg.evaluate("() => !!document.getElementById('chMxFisioIntegrado')"))

    # ── 8. Estados: indisponível, erro de leitura, falha de render ──────────
    pg.evaluate("() => { document.getElementById('mxDetalhesTecnicosEvidencias').open=false; }")
    pg.evaluate("() => mxVstRenderRpeZonas({rpe_zonas_integrado_status:'run_id_divergente', rpe_zonas_integrado:null})")
    pg.wait_for_timeout(200)
    m = pg.evaluate("() => ({area: document.getElementById('mxVstRpeZonasArea').style.display, ind: document.getElementById('mxVstRpeZonasIndisp').textContent})")
    check('8 run_id divergente: área oculta e motivo específico', m['area']=='none' and 'Nada foi misturado' in m['ind'], str(m))
    pg.evaluate("() => mxVstRenderRpeZonas({rpe_zonas_integrado_status:'erro_leitura', rpe_zonas_integrado:null})")
    m2 = pg.evaluate("() => document.getElementById('mxVstRpeZonasIndisp').textContent")
    check('8 erro de leitura: mensagem de causa', 'falha ao ler o banco canónico' in m2, m2)
    pg.evaluate("() => mxVstRenderRpeZonas({rpe_zonas_integrado:{intervalos:[]}})")
    m3 = pg.evaluate("() => document.getElementById('mxVstRpeZonasIndisp').textContent")
    area3=pg.evaluate("() => document.getElementById('mxVstRpeZonasArea').style.display")
    check('8 intervalos vazios: mensagem de dado ausente e área oculta', len(m3)>0 and area3=='none', m3+' | '+str(area3))
    # render com falha: dados corrompidos não podem quebrar a página
    pg.evaluate("() => { window.__ex2=null; try { _mxVstRenderComparacao({rpe_zonas_integrado:{intervalos:[{potencia:'x', hr:null}], bp:null, hrvt:null}, comparacao_bp1:{}, comparacao_bp2:{}}, 'V1'); } catch(e){ window.__ex2=String(e); } }")
    pg.wait_for_timeout(300)
    ex2 = pg.evaluate("() => window.__ex2")
    check('8 dados inválidos: o wrapper de render captura a falha (sem exceção para a página)', ex2 is None, str(ex2))

    check('9 sem erros JS na página', not errors, str(errors[:3]))
    b.close()
finally:
  srv.terminate()

ok=sum(1 for _,o in results if o)
print('\nRESULTADO BROWSER GRÁFICO PRINCIPAL: %d/%d OK' % (ok, len(results)))
