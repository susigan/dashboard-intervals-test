# Testes da aba PMC: inicialização, Coeficientes e Eficiência (CTL vs KJ).
# FIXTURES SINTÉTICAS — não validam dados reais de produção.
import json, os, sys, subprocess, tempfile, time, copy
from playwright.sync_api import sync_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
PORT = 8797
results = []

def check(nome, ok, det=''):
    results.append((nome, bool(ok)))
    print(('OK    ' if ok else 'FALHA '), nome, '' if ok else det)

def payload_base():
    base = {'year': 2026}
    import datetime as dt, math
    d0 = dt.date(2026, 8, 1)
    serie = [{'date': (d0 + dt.timedelta(days=i)).isoformat(), 'ctl': 40 + i * 0.3,
              'atl': 45 + math.sin(i / 5) * 8, 'tsb': -5 + math.cos(i / 7) * 4, 'tl': 60} for i in range(60)]
    fser = [{'date': s['date'], 'kappa': 1.0 + 0.3 * math.sin(i / 6), 'lambda1': 0.5 + 0.1 * math.cos(i / 9), 'fase': 'BUILD'}
            for i, s in enumerate(serie)]
    base.update({
        'serie': serie,
        'actual': {'ctl': 58.2, 'atl': 51.0, 'tsb': -3.1, 'ramp': 2.4, 'estado': {'label': 'Build', 'cor': '#5DADE2'}},
        'sessoes': [], 'alertas': [], 'cores': {'Bike': '#58a6ff'},
        'ciclicos': ['Bike', 'Row', 'Run', 'Ski'],
        'ftlm': {'serie': fser, 'fases_legenda': {'BUILD': {'label': 'Build', 'cor': '#5DADE2'}},
                 'fase_actual': {'codigo': 'BUILD', 'label': 'Build', 'desc': 'carga crescente', 'dias': 12,
                                 'dctlg': 0.0123, 'hrv_z': 0.4, 'cor': '#5DADE2'},
                 'fase_global': {'codigo': 'BUILD', 'label': 'Build', 'cor': '#5DADE2',
                                 'contribuicoes': {'Bike': 1.0}, 'fases_por_modalidade': {'Bike': 'BUILD'}},
                 'gammas': {'perf': {'gamma': 0.62, 'r2': 0.71}, 'rec': {'gamma': 0.48, 'r2': 0.55}},
                 'fmt': {'dimensoes': ['a', 'b']}, 'canais': {}, 'dia': serie[-1]['date'], 'janela': 28,
                 'nota_atencao': None},
        'fmt': {'dimensoes': ['a', 'b'], 'serie': [], 'canais': {}},
        'homeostatico': None, 'cp_fonte': None, 'erro_ftlm': None,
        'dtrimp_dkj': {'Bike': [
            {'tipo': 'todos', 'n': 70, 'dtrimp_dkj': 0.352, 'coef_densidade': 62.76, 'r2': 0.608, 'kj_medio': 637, 'trimp_medio': 429.2},
            {'tipo': 'base', 'n': 20, 'dtrimp_dkj': 0.281, 'coef_densidade': 41.5, 'r2': 0.512, 'kj_medio': 540, 'trimp_medio': 310.4}]},
        'eficiencia_kj': {'Bike': {'semanas': ['2026-09-07', '2026-09-14'], 'eff_semanal': [1.1, 1.2],
                                   'eff_roll': [1.1, 1.15], 'tendencia': 'fadiga', 'ultima_data': '2026-10-08',
                                   'dias_desde_ultima_sessao': 2, 'desactualizado': False, 'aviso': None,
                                   'eff_actual': 1.234, 'eff_historica': 1.1, 'n_sessoes': 70}},
    })
    return base

def gerar_pagina(dirpath):
    import tabs.tab_pmc as m
    html = m.render()
    with open(os.path.join(dirpath, 'pmc_test.html'), 'w', encoding='utf-8') as f:
        f.write(html)

def main():
    tmp = tempfile.mkdtemp(prefix='pmc_test_')
    gerar_pagina(tmp)
    srv = subprocess.Popen([sys.executable, '-m', 'http.server', str(PORT), '--bind', '127.0.0.1'], cwd=tmp,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()

            def pagina(payload, largura=1100):
                pg = b.new_page(viewport={'width': largura, 'height': 900})
                pg.errs = []
                pg.on('pageerror', lambda e: pg.errs.append(str(e)[:200]))
                pg.on('console', lambda m: pg.errs.append('console: ' + m.text[:200]) if m.type == 'error' and 'Failed to load' not in m.text else None)
                pg.route('http://127.0.0.1:%d/api/pmc' % PORT,
                         lambda r: r.fulfill(body=json.dumps(payload), content_type='application/json'))
                pg.goto('http://127.0.0.1:%d/pmc_test.html' % PORT, wait_until='load')
                pg.wait_for_timeout(1000)
                return pg

            def tinta_chFMT(pg):
                return pg.evaluate("""()=>{const c=document.getElementById('chFMT'); let n=0;
                  if(c&&c.width){const g=c.getContext('2d').getImageData(0,0,c.width,c.height).data; for(let i=3;i<g.length;i+=4) if(g[i]>0) n++;}
                  return n;}""")

            # ── 1. Inicialização sem F12 ─────────────────────────────────────────
            pg = pagina(payload_base())
            check('1a carga sem erros de página', not pg.errs, str(pg.errs))
            check('1b curvatura κ desenhada na carga (sem resize)', tinta_chFMT(pg) > 0, str(tinta_chFMT(pg)))
            check('1c cartão de fase preenchido após a carga', pg.evaluate("()=>document.getElementById('faseCard').innerHTML.length")>0)
            check('1d legenda de κ construída', pg.evaluate("()=>document.getElementById('lgFMT').innerHTML.length")>0)

            # ── 2. Coeficientes ─────────────────────────────────────────────────
            pg.click('#ctlkjBtnCoef'); pg.wait_for_timeout(200)
            check('2a clique em Coeficientes sem erro', not pg.errs, str(pg.errs))
            check('2b caixa de Coeficientes visível', pg.evaluate("()=>getComputedStyle(document.getElementById('ctlkjCoefBox')).display")!='none')
            check('2c cabeçalho com 8 colunas', pg.evaluate("()=>document.querySelectorAll('#ctlkjCoefHead th').length")==8)
            linhas = pg.evaluate("()=>Array.from(document.querySelectorAll('#ctlkjCoefBody tr')).map(r=>r.innerText)")
            check('2d duas linhas de dados (Bike: todos e base)', len([l for l in linhas if 'Bike' in l])==2, str(linhas))
            check('2e valores exibidos = payload (0.3520 e 0.2810)', any('0.3520' in l for l in linhas) and any('0.2810' in l for l in linhas), str(linhas))
            check('2f modalidades sem resultado nomeadas (Row, Run, Ski)', any('Row, Run, Ski' in l for l in linhas), str(linhas))
            check('2g tipo "base" com rótulo RPE ≤ 5', any('Base (RPE ≤ 5)' in l for l in linhas))

            # ── 3. Eficiência ───────────────────────────────────────────────────
            pg.click('#ctlkjBtnEf'); pg.wait_for_timeout(200)
            check('3a clique em Eficiência sem erro', not pg.errs, str(pg.errs))
            check('3b caixa de Eficiência visível', pg.evaluate("()=>getComputedStyle(document.getElementById('ctlkjEfBox')).display")!='none')
            check('3c caixa de Coeficientes oculta', pg.evaluate("()=>getComputedStyle(document.getElementById('ctlkjCoefBox')).display")=='none')
            cards = pg.evaluate("()=>Array.from(document.querySelectorAll('#ctlkjEfCards .card')).map(c=>c.innerText)")
            check('3d um cartão para Bike com valor do payload (1.234)', len(cards)==1 and '1.234' in cards[0], str(cards))
            check('3e cartão mostra mediana histórica e tendência', len(cards)==1 and '1.100' in cards[0] and 'fadiga' in cards[0], str(cards))
            check('3f modalidades sem resultado nomeadas', pg.evaluate("()=>document.getElementById('ctlkjEfCards').innerText").find('Row, Run, Ski')>=0)
            pg.click('#ctlkjBtnCoef'); pg.wait_for_timeout(150)
            check('3g voltar para Coeficientes restaura a caixa', pg.evaluate("()=>getComputedStyle(document.getElementById('ctlkjCoefBox')).display")!='none')

            # ── 4. Redimensionar ────────────────────────────────────────────────
            pg.evaluate("()=>window.dispatchEvent(new Event('resize'))"); pg.wait_for_timeout(300)
            check('4a κ continua desenhada após resize', tinta_chFMT(pg) > 0)
            check('4b bitmap de κ acompanha a largura CSS', pg.evaluate("()=>{const c=document.getElementById('chFMT');return c.width===c.clientWidth}"))
            check('4c sem erros após resize', not pg.errs, str(pg.errs))
            pg.close()

            # ── 5. Dados vazios: mensagem explícita, sem erro ───────────────────
            vazio = payload_base(); vazio['dtrimp_dkj'] = {}; vazio['eficiencia_kj'] = {}
            pg = pagina(vazio)
            check('5a sem erro com resultados vazios', not pg.errs, str(pg.errs))
            pg.click('#ctlkjBtnCoef'); pg.wait_for_timeout(150)
            txt = pg.evaluate("()=>document.getElementById('ctlkjCoefBody').innerText")
            check('5b Coeficientes: mensagem "Sem resultado" (sem valor inventado)', 'Sem resultado' in txt and 'amostras suficientes' in txt, txt)
            pg.click('#ctlkjBtnEf'); pg.wait_for_timeout(150)
            txt = pg.evaluate("()=>document.getElementById('ctlkjEfCards').innerText")
            check('5c Eficiência: mensagem "Sem resultado" (sem cartão)', 'Sem resultado' in txt and pg.evaluate("()=>document.querySelectorAll('#ctlkjEfCards .card').length")==0, txt)
            check('5d κ continua desenhada mesmo sem resultados CTL vs KJ', tinta_chFMT(pg) > 0)
            pg.close()

            # ── 6. Chave ausente no payload: mensagem distinta ──────────────────
            sem_chave = payload_base(); del sem_chave['dtrimp_dkj']; del sem_chave['eficiencia_kj']
            pg = pagina(sem_chave)
            check('6a sem erro com chaves ausentes', not pg.errs, str(pg.errs))
            pg.click('#ctlkjBtnCoef'); pg.wait_for_timeout(150)
            check('6b mensagem de dados ausentes (distinta de "sem resultado")', 'ausentes' in pg.evaluate("()=>document.getElementById('ctlkjCoefBody').innerText"))
            pg.close()

            # ── 7. Aviso de dados desatualizados (texto do backend) ─────────────
            desat = payload_base()
            desat['eficiencia_kj']['Bike'].update({'desactualizado': True, 'aviso': 'sem sessões há 20 dias — tendência da última vez'})
            pg = pagina(desat)
            pg.click('#ctlkjBtnEf'); pg.wait_for_timeout(150)
            check('7a aviso de desatualização exibido', 'sem sessões há 20 dias' in pg.evaluate("()=>document.getElementById('ctlkjEfCards').innerText"))
            pg.close()

            b.close()
    finally:
        srv.terminate()

    # ── 8. Verificação estática: sem try/catch genérico nas funções novas ───
    src = open(os.path.join(ROOT, 'tabs', 'tab_pmc.py'), encoding='utf-8').read()
    ini = src.index('// ── CTL vs KJ — coeficientes'); fim = src.index('function redesenhar(){')
    check('8a funções novas sem try/catch', 'try{' not in src[ini:fim] and 'catch' not in src[ini:fim])
    check('8b chamadas originais de load() preservadas (linha com mostrarDtrimpDkj)', 'mostrarDtrimpDkj(); mostrarEficienciaKj();' in src)

    print(f'\nRESULTADO PMC CTL vs KJ (fixtures sintéticas): {sum(1 for _,o in results if o)}/{len(results)} OK')
    return 0 if all(o for _, o in results) else 1

if __name__ == '__main__':
    sys.exit(main())
