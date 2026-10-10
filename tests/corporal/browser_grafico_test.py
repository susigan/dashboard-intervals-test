"""Testes de navegador da visao temporal da aba Corporal (dados sinteticos).

O payload vem da propria api_data (rota /api/corporal) alimentada por tabelas
sinteticas; a rede do Sheets e substituida. A agregacao do JS e comparada com
uma implementacao independente em Python.

Executar a partir da raiz do repositorio:
    python3 tests/corporal/browser_grafico_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from statistics import median

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'teste-local')

PORT = 8799
MIN_REG = {'W': 3, 'M': 7, 'Q': 14, 'S': 30, 'A': 60}
RESULTADOS = []


def check(nome, ok, detalhe=''):
    RESULTADOS.append((nome, bool(ok)))
    print(('OK   ' if ok else 'FALHA') + ' ' + nome + ((' | ' + detalhe) if detalhe and not ok else ''))


# ── dados sinteticos e payload real da api_data ───────────────────────────────

def gerar_payload():
    import sheets_client as sheets
    from flask import Flask
    import tabs.tab_corporal as tc

    d0, d1 = date(2025, 11, 3), date(2026, 2, 20)
    dias = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    W, C = [['Data', 'Peso', 'FAT']], [['Data', 'Peso', 'BF', 'Calorias', 'Carb', 'Fat', 'Ptn', 'Net']]
    for i, d in enumerate(dias):
        if date(2025, 12, 8) <= d <= date(2025, 12, 14):
            continue                      # semana sem nenhum registo (nem diario, nem consolidado)
        sem_peso = i % 11 == 5
        peso = '' if sem_peso else '%.2f' % (80.0 - 0.02 * i + 0.4 * ((i * 7) % 5 - 2) / 4)
        fat = '%.1f' % (18.5 - 0.01 * i) if i % 3 == 0 else ''
        W.append([d.isoformat(), peso, fat])
        sem_cal = date(2026, 1, 19) <= d <= date(2026, 1, 25)
        if not sem_cal:
            cal = str(2000 + (i * 37) % 600)
            C.append([d.isoformat(), '', '', cal, '250', '70', '150', str(-200 + (i * 53) % 400 - 200)])

    def fake(url, aba):
        return (W if 'Respostas' in aba else C), None
    sheets._ler_aba = fake
    sheets._cache.update({'wellness': None, 'corporal': None, 'time': None})
    app = Flask(__name__)
    with app.test_request_context('/api/corporal?periodo=W'):
        return tc.api_data().get_json()


# ── implementacao de referencia em Python (independente do JS) ────────────────

def periodo_py(d, g):
    if g == 'W':
        iso = d.isocalendar()
        return '%d-W%02d' % (iso[0], iso[1])
    if g == 'M':
        return '%d-%02d' % (d.year, d.month)
    if g == 'Q':
        return '%d-Q%d' % (d.year, (d.month - 1) // 3 + 1)
    if g == 'S':
        return '%d-S%d' % (d.year, 1 if d.month <= 6 else 2)
    return str(d.year)


def ref_agregar(linhas, g, hoje=None):
    hoje = hoje or date.today()
    rows = [r for r in linhas if r.get('date') and date.fromisoformat(r['date']) <= hoje]
    if not rows:
        return []
    rows.sort(key=lambda r: r['date'])
    ds = [date.fromisoformat(r['date']) for r in rows]
    por = {}
    for r in rows:
        por.setdefault(periodo_py(date.fromisoformat(r['date']), g), []).append(r)
    ordem, vistos, d = [], set(), ds[0]
    while d <= ds[-1]:
        k = periodo_py(d, g)
        if k not in vistos:
            vistos.add(k)
            ordem.append(k)
        d += timedelta(days=1)
    out = {}
    for k in ordem:
        dd = por.get(k, [])
        item = {'dias': len(dd), 'n': {}, 'v': {}}
        for s, stat in (('peso', 'mediana'), ('bf', 'mediana'), ('calorias', 'media'), ('net', 'media')):
            vals = [r[s] for r in dd if isinstance(r.get(s), (int, float))]
            item['n'][s] = len(vals)
            item['v'][s] = (median(vals) if stat == 'mediana' else sum(vals) / len(vals)) if vals else None
        out[k] = item
    return out


def ref_roll(linhas, campo, N, minimo=3):
    """Media movel de N dias de calendario, sem arredondar; so dias com minimo de registos."""
    val = {date.fromisoformat(r['date']): r[campo] for r in linhas
           if isinstance(r.get(campo), (int, float))}
    datas = [date.fromisoformat(r['date']) for r in linhas]
    if not val or not datas:
        return {}
    a, b = min(datas), max(datas)
    out, d = {}, a
    while d <= b:
        seg = [val[x] for x in (d - timedelta(days=k) for k in range(N)) if x in val]
        if len(seg) >= minimo:
            out[d.isoformat()] = (sum(seg) / len(seg), len(seg))
        d += timedelta(days=1)
    return out


# ── navegador ─────────────────────────────────────────────────────────────────

def main():
    from playwright.sync_api import sync_playwright
    import tabs.tab_corporal as tc

    payload = gerar_payload()
    SHOTS = os.environ.get('CORP_SHOTS') or tempfile.mkdtemp(prefix='corp_shots_')
    os.makedirs(SHOTS, exist_ok=True)
    print('capturas em', SHOTS)
    tmp = tempfile.mkdtemp(prefix='corp_grafico_')
    with open(os.path.join(tmp, 'corp.html'), 'w', encoding='utf-8') as f:
        f.write(tc.render())
    srv = subprocess.Popen([sys.executable, '-m', 'http.server', str(PORT), '--bind', '127.0.0.1'],
                           cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()

            def pagina(dados=payload, largura=1100):
                pg = b.new_page(viewport={'width': largura, 'height': 900})
                pg.errs, pg.reqs = [], []
                pg.on('pageerror', lambda e: pg.errs.append(str(e)[:200]))
                pg.on('request', lambda r: pg.reqs.append(r.url) if '/api/corporal' in r.url else None)

                def resp(route):
                    route.fulfill(body=json.dumps(dados), content_type='application/json')
                pg.route('**/api/corporal**', resp)
                pg.goto('http://127.0.0.1:%d/corp.html' % PORT, wait_until='load')
                pg.wait_for_timeout(900)
                return pg

            pg = pagina()
            check('pagina carrega sem erros de JavaScript', not pg.errs, str(pg.errs))
            check('granularidade padrao e Semana', pg.evaluate("document.getElementById('granul').value") == 'W')

            # 1. limites, n e valores por granularidade, contra a referencia Python
            pg.evaluate("()=>{document.getElementById('janela').value='0';document.getElementById('janela').onchange();}")
            pg.wait_for_timeout(200)
            for g in ['W', 'M', 'Q', 'S', 'A']:
                js = pg.evaluate("g=>agregarTemporal(janelaDias(C.linhas),g)", g)
                ref = ref_agregar(payload['linhas'], g)
                chaves_js = [x['k'] for x in js]
                check('%s: periodos cobertos sem buracos (%d)' % (g, len(js)),
                      chaves_js == list(ref.keys()), '%s vs %s' % (chaves_js[:4], list(ref)[:4]))
                ok_vals, ok_n, ok_dias = True, True, True
                for x in js:
                    rf = ref[x['k']]
                    ok_dias &= x['dias'] == rf['dias']
                    for s in ('peso', 'bf', 'calorias', 'net'):
                        ok_n &= x['n'][s] == rf['n'][s]
                        a, c = x['v'][s], rf['v'][s]
                        ok_vals &= (a is None and c is None) or (a is not None and c is not None and abs(a - c) < 1e-9)
                check('%s: contagem de dias por periodo' % g, ok_dias)
                check('%s: n observacoes por serie' % g, ok_n)
                check('%s: mediana (peso, bf) e media (calorias, net) corretas' % g, ok_vals)

            # 2. virada de ano ISO e limites de periodo
            w = pg.evaluate("agregarTemporal(janelaDias(C.linhas),'W')")
            w01 = [x for x in w if x['k'] == '2026-W01']
            check('ISO: 2026-W01 comeca na segunda 2025-12-29 e termina em 2026-01-04',
                  bool(w01) and w01[0]['ini'] == '2025-12-29' and w01[0]['fim'] == '2026-01-04',
                  str(w01[:1]))
            check('ISO: 2025 nao tem semana 53 indevida (W52 existe, sem W53)',
                  any(x['k'] == '2025-W52' for x in w) and not any(x['k'] == '2025-W53' for x in w))
            m = pg.evaluate("agregarTemporal(janelaDias(C.linhas),'M').map(x=>[x.k,x.ini,x.fim])")
            check('mes: limites de calendario (fev/2026 termina em 28)',
                  any(k == '2026-02' and fim == '2026-02-28' for k, _, fim in m))
            q = pg.evaluate("agregarTemporal(janelaDias(C.linhas),'Q').map(x=>x.k)")
            check('trimestre: 2025-Q4 e 2026-Q1 presentes', '2025-Q4' in q and '2026-Q1' in q)
            s_ = pg.evaluate("agregarTemporal(janelaDias(C.linhas),'S').map(x=>x.k)")
            check('semestre: 2025-S2 e 2026-S1', '2025-S2' in s_ and '2026-S1' in s_)

            # 3. semanas sem observacoes: faixa e sem valor inventado
            vazias = [x for x in w if x['dias'] == 0]
            check('semana vazia existe no fixture (2025-W50, semana sem registos)', any(x['k'] == '2025-W50' for x in vazias))
            check('semanas vazias tem valores nulos (nada inventado)',
                  all(all(v is None for v in x['v'].values()) for x in vazias))

            # 4. tooltip de periodo vazio e de periodo com dados
            pg.evaluate("()=>{document.getElementById('granul').value='W';document.getElementById('granul').onchange();}")
            pg.wait_for_timeout(200)
            geo = "const geo=(W)=>{const OFF=W<560?56:72;const PL=58,w=W-PL-(10+2*OFF);return {PL:PL,w:w};};"
            info = pg.evaluate("()=>{" + geo + "const W=document.getElementById('chMain').clientWidth;"
                               "const per=agregarTemporal(janelaDias(C.linhas),'W');"
                               "const idx=per.findIndex(x=>x.k==='2025-W50');"
                               "const G=geo(W);const x=G.PL+G.w*(idx/(per.length-1));"
                               "return TIPS.chMain(x,10,W,340);}")
            check('tooltip de periodo vazio diz "sem registos no periodo"', 'sem registos no periodo' in info, info[:160])
            info2 = pg.evaluate("()=>{" + geo + "const W=document.getElementById('chMain').clientWidth;"
                                "const per=agregarTemporal(janelaDias(C.linhas),'W');"
                                "const idx=per.findIndex(x=>x.dias>0);"
                                "const G=geo(W);const x=G.PL+G.w*(idx/(per.length-1));"
                                "return TIPS.chMain(x,10,W,340);}")
            check('tooltip traz periodo, intervalo, unidade e n',
                  'kg' in info2 and 'n=' in info2 and 'registo' in info2 and '/' in info2, info2[:260])

            # 5. Janela aplicada antes da agregacao
            pg.evaluate("()=>{document.getElementById('janela').value='90';document.getElementById('janela').onchange();}")
            pg.wait_for_timeout(200)
            jan30 = pg.evaluate("janelaDias(C.linhas).length")
            fim = max(r['date'] for r in payload['linhas'])
            corte = (date.fromisoformat(fim) - timedelta(days=89)).isoformat()
            esperado = len([r for r in payload['linhas'] if r['date'] >= corte])
            check('Janela 90 dias: recorte por calendario antes da agregacao', jan30 == esperado, '%d vs %d' % (jan30, esperado))
            soma = sum(x['dias'] for x in pg.evaluate("agregarTemporal(janelaDias(C.linhas),'M')"))
            check('Janela: agregacao so conta linhas dentro da janela', soma == esperado, '%d vs %d' % (soma, esperado))
            pg.evaluate("()=>{document.getElementById('janela').value='0';document.getElementById('janela').onchange();}")
            pg.wait_for_timeout(150)

            # 6. troca de granularidade: sem consulta ao Sheets, dados originais intactos
            antes = json.dumps(pg.evaluate("C.linhas"), sort_keys=True)
            n_req = len(pg.reqs)
            for g in ['M', 'Q', 'S', 'A', 'W']:
                pg.select_option('#granul', g)
                pg.wait_for_timeout(120)
            check('trocar granularidade nao faz nova consulta a /api/corporal', len(pg.reqs) == n_req,
                  str(pg.reqs[n_req:]))
            depois = json.dumps(pg.evaluate("C.linhas"), sort_keys=True)
            check('dados diarios originais inalterados apos trocar granularidade', antes == depois)

            # 7. legenda: alterna camadas dentro de cada figura; a ultima camada visivel resiste
            DEFAULTS = {'main_peso_pontos': True, 'main_peso_rolling': True, 'main_peso_periodo': True,
                        'main_bf_pontos': True, 'main_bf_rolling': True, 'main_bf_periodo': True,
                        'main_calorias_pontos': True, 'main_calorias_rolling': True, 'main_calorias_periodo': False,
                        'cn_calorias_pontos': True, 'cn_calorias_rolling': True, 'cn_calorias_periodo': False,
                        'cn_net_rolling': True}
            pg.select_option('#granul', 'W'); pg.wait_for_timeout(120)
            txt = pg.inner_text('#lgMain')
            check('legenda da figura principal identifica metrica e tipo de serie',
                  all(t in txt for t in ['Peso — registros', 'Peso — rolling 7 dias', 'Peso — resumo semanal',
                                         'Gordura corporal — registros', 'Calorias — registros']), txt[:200])
            pg.click('#lgCalNet [data-serie="cn_net_rolling"]'); pg.wait_for_timeout(120)
            check('legenda desativa a media movel de Net', pg.evaluate("ATIVAS.cn_net_rolling") is False)
            pg.click('#lgCalNet [data-serie="cn_calorias_rolling"]'); pg.wait_for_timeout(120)
            check('legenda desativa a media movel de calorias (registos continuam)',
                  pg.evaluate("[ATIVAS.cn_calorias_rolling,ATIVAS.cn_calorias_pontos]") == [False, True])
            pg.click('#lgCalNet [data-serie="cn_calorias_pontos"]'); pg.wait_for_timeout(120)
            check('figura Calorias + Net nao fica sem camadas (ultima camada resiste)',
                  pg.evaluate("ATIVAS.cn_calorias_pontos") is True)
            pg.click('#lgMain [data-serie="main_bf_rolling"]'); pg.wait_for_timeout(120)
            check('legenda da figura principal desativa a media movel de gordura',
                  pg.evaluate("ATIVAS.main_bf_rolling") is False)
            pg.click('#lgMain [data-serie="main_calorias_periodo"]'); pg.wait_for_timeout(120)
            check('legenda liga o resumo de calorias na figura principal',
                  pg.evaluate("ATIVAS.main_calorias_periodo") is True)
            for sid in ['cn_net_rolling', 'cn_calorias_rolling', 'main_bf_rolling', 'main_calorias_periodo']:
                leg = 'lgCalNet' if sid.startswith('cn_') else 'lgMain'
                if pg.evaluate("ATIVAS['%s']" % sid) != DEFAULTS[sid]:
                    pg.click('#%s [data-serie="%s"]' % (leg, sid)); pg.wait_for_timeout(120)
            check('legenda volta ao estado padrao das camadas', pg.evaluate("ATIVAS") == DEFAULTS)

            # 7b. media movel (rolling): mesma regra dos KPIs, lacunas, granularidade, originais
            pg.evaluate("()=>{document.getElementById('janela').value='0';document.getElementById('janela').onchange();}")
            pg.wait_for_timeout(150)
            for campo in ['peso', 'bf', 'calorias', 'net']:
                js = pg.evaluate("c=>{const full=C.linhas.slice().sort((a,b)=>a.date<b.date?-1:a.date>b.date?1:0);"
                                 "const m=mediaMovel(full,c,7);return Object.keys(m).map(k=>[diaStr(+k),m[k].v,m[k].n]);}",
                                 campo)
                jsd = {x[0]: x[1] for x in js}
                bk = {x['date']: x['valor'] for x in payload['r7'][campo] if x['valor'] is not None}
                ok_set = set(jsd) == set(bk)
                ok_val = ok_set and all(abs(jsd[k] - bk[k]) <= 0.006 for k in bk)
                check('rolling 7 dias de %s igual ao r7 do backend (KPIs)' % campo, ok_val,
                      'dias js=%d backend=%d' % (len(jsd), len(bk)))
            for N in [7, 14, 28]:
                js = pg.evaluate("N=>{const full=C.linhas.slice().sort((a,b)=>a.date<b.date?-1:a.date>b.date?1:0);"
                                 "const m=mediaMovel(full,'peso',N);return Object.keys(m).map(k=>[diaStr(+k),m[k].v,m[k].n]);}", N)
                ref = ref_roll(payload['linhas'], 'peso', N)
                ok = set(x[0] for x in js) == set(ref) and all(abs(x[1] - ref[x[0]][0]) < 1e-9 and x[2] == ref[x[0]][1] for x in js)
                check('rolling peso %d dias igual a referencia independente' % N, ok)
            nulos = pg.evaluate("()=>{const full=C.linhas.slice().sort((a,b)=>a.date<b.date?-1:a.date>b.date?1:0);"
                                "const m=mediaMovel(full,'peso',7);const a=diaNum(full[0].date),b=diaNum(full[full.length-1].date);"
                                "let c=0;for(let d=a;d<=b;d++) if(!m[d]) c++;return c;}")
            check('lacunas: dias sem media (menos de 3 registos na janela) ficam nulos, sem preencher', nulos > 0, str(nulos))

            # granularidade e janela de rolling nao mudam a camada de media movel
            pg.select_option('#granul', 'W'); pg.wait_for_timeout(120)
            rolW = pg.evaluate("JSON.stringify(TEMP_ULT.figuras.chMain.dados.peso.rolPts)")
            for g in ['M', 'Q', 'S', 'A']:
                pg.select_option('#granul', g); pg.wait_for_timeout(120)
                rol = pg.evaluate("JSON.stringify(TEMP_ULT.figuras.chMain.dados.peso.rolPts)")
                check('media movel identica com granularidade %s' % g, rol == rolW)
            pg.select_option('#granul', 'W'); pg.wait_for_timeout(120)
            antes_linhas = json.dumps(pg.evaluate("C.linhas"), sort_keys=True)
            kpi_antes = pg.evaluate("document.getElementById('kpis').textContent")
            n_req = len(pg.reqs)
            pg.select_option('#rollJan', '14'); pg.wait_for_timeout(150)
            check('janela rolling 14 aplicada', pg.evaluate("TEMP_ULT.N") == 14)
            rol14 = pg.evaluate("JSON.stringify(TEMP_ULT.figuras.chMain.dados.peso.rolPts.map(q=>q.v))")
            ref14 = ref_roll(payload['linhas'], 'peso', 14)
            cont = pg.evaluate("TEMP_ULT.figuras.chMain.dados.peso.rolPts.map(q=>[q.t,q.v])")
            def dia_de(t):
                return (date(1970, 1, 1) + timedelta(days=t)).isoformat()
            okr = all(((v is None) == (dia_de(t) not in ref14)) and (v is None or abs(v - ref14[dia_de(t)][0]) < 1e-9)
                      for t, v in cont)
            check('janela rolling 14 dias igual a referencia (grafico)', okr)
            pg.select_option('#rollJan', '7'); pg.wait_for_timeout(120)
            check('trocar janela rolling nao faz consulta ao Sheets', len(pg.reqs) == n_req, str(pg.reqs[n_req:]))
            check('dados originais (C.linhas) intactos apos granularidade e rolling',
                  antes_linhas == json.dumps(pg.evaluate("C.linhas"), sort_keys=True))
            check('KPIs inalterados por granularidade e janela rolling',
                  kpi_antes == pg.evaluate("document.getElementById('kpis').textContent"))

            # tooltip mostra a media movel (rolling) no dia, na figura principal
            t_com = [q for q in pg.evaluate("TEMP_ULT.figuras.chMain.dados.peso.rolPts") if q['v'] is not None]
            alvo = t_com[len(t_com) // 2]['t']
            tip = pg.evaluate("(t)=>{const W=document.getElementById('chMain').clientWidth;const T=TEMP_ULT;"
                              "const OFF=W<560?56:72;const PL=58,w=W-PL-(10+2*OFF);"
                              "const x=PL+w*((t-T.t0)/(T.t1-T.t0));return TIPS.chMain(x,10,W,340);}", alvo)
            check('tooltip do dia mostra a rolling e os dias validos na janela',
                  'rolling 7 dias' in tip and 'dias validos na janela' in tip, tip[:200])

            # 11. estrutura: uma figura com peso, gordura e calorias; eixos Y independentes
            pg.evaluate("()=>{document.getElementById('janela').value='0';document.getElementById('janela').onchange();}")
            pg.wait_for_timeout(150)
            est = pg.evaluate("()=>({m:TEMP_ULT.figuras.chMain.metricas, lim:TEMP_ULT.figuras.chMain.lim,"
                              "ids:[...document.querySelectorAll('canvas')].map(c=>c.id)})")
            check('figura principal: peso, gordura e calorias na mesma figura', est['m'] == ['peso', 'bf', 'calorias'], str(est['m']))
            escalas = list(est['lim'].values())
            check('figura principal: tres eixos Y, cada um com escala propria',
                  len(escalas) == 3 and all(e is not None for e in escalas) and len(set(map(tuple, escalas))) == 3, str(escalas))
            check('figuras temporais: dois canvases, sem os quatro paineis antigos',
                  'chMain' in est['ids'] and 'chCalNet' in est['ids']
                  and not any(i in est['ids'] for i in ['chPeso', 'chBF', 'chCal', 'chNet']), str(est['ids']))
            cn = pg.evaluate("()=>({m:TEMP_ULT.figuras.chCalNet.metricas, lim:TEMP_ULT.figuras.chCalNet.lim})")
            check('figura Calorias + Net: duas metricas em dois eixos', cn['m'] == ['calorias', 'net'], str(cn['m']))
            check('eixo do Net inclui a referencia 0', cn['lim']['net'][0] <= 0 <= cn['lim']['net'][1], str(cn['lim']['net']))
            n_pontos = len(pg.evaluate("TEMP_ULT.figuras.chMain.dados.peso.pontos"))
            n_orig = sum(1 for r in payload['linhas'] if r.get('peso') is not None)
            check('pontos de peso = registos originais (a media movel nao os substitui)', n_pontos == n_orig,
                  '%d vs %d' % (n_pontos, n_orig))
            rp = pg.evaluate("TEMP_ULT.figuras.chCalNet.dados.net.rolPts.map(q=>[q.t,q.v])")
            refn = ref_roll(payload['linhas'], 'net', 7)
            okn = all(((v is None) == (dia_de(t) not in refn)) and (v is None or abs(v - refn[dia_de(t)][0]) < 1e-9)
                      for t, v in rp)
            check('Net: media movel igual a referencia; dias sem valor ficam nulos (nao viram zero)', okn)

            # Net ausente: sem zero inventado; sem Net nem calorias: figura nao desenha (mensagem de ausencia)
            sem_net = json.loads(json.dumps(payload))
            for r_ in sem_net['linhas']:
                r_['net'] = None
            pg3 = pagina(sem_net)
            check('sem Net: sem escala de Net, sem ponto e sem zero inventado',
                  pg3.evaluate("TEMP_ULT.figuras.chCalNet.lim.net") is None
                  and all(q['v'] is None for q in pg3.evaluate("TEMP_ULT.figuras.chCalNet.dados.net.rolPts")))
            check('sem Net: calorias continuam na figura Calorias + Net',
                  pg3.evaluate("TEMP_ULT.figuras.chCalNet.lim.calorias") is not None)
            check('sem Net: nenhum erro de JavaScript', not pg3.errs, str(pg3.errs))
            pg3.close()
            sem_nada = json.loads(json.dumps(payload))
            for r_ in sem_nada['linhas']:
                r_['net'] = None
                r_['calorias'] = None
            pg4 = pagina(sem_nada)
            check('sem Net nem calorias: figura Calorias + Net mostra a ausencia (nao desenha)',
                  pg4.evaluate("TEMP_ULT.figuras.chCalNet") is None)
            check('sem calorias: figura principal segue com peso e gordura',
                  pg4.evaluate("TEMP_ULT.figuras.chMain.lim.peso") is not None
                  and pg4.evaluate("TEMP_ULT.figuras.chMain.lim.calorias") is None)
            check('sem Net nem calorias: nenhum erro de JavaScript', not pg4.errs, str(pg4.errs))
            pg4.close()

            # 8. escala Y nao exagera pequenas oscilacoes
            lim = pg.evaluate("limitesY([80.0,80.2],SERIES_TEMP.peso.minSpan)")
            check('escala de peso tem amplitude minima (>= 2 kg)', lim[1] - lim[0] >= 2.0, str(lim))
            for lo_, hi_ in [(77.7, 80.1), (1930, 2750), (-450, 300), (18.2, 18.5), (79.9, 80.0)]:
                r = pg.evaluate("([a,b])=>niceLim(a,b)", [lo_, hi_])
                passo = (r[1] - r[0]) / 4
                redondo = abs(r[0] / passo - round(r[0] / passo)) < 1e-9
                cobre = r[0] <= lo_ + 1e-9 and r[1] >= hi_ - 1e-9
                check('escala com passo redondo e cobre os dados (%s..%s)' % (lo_, hi_), redondo and cobre, str(r))

            # 9. regressao: cartoes, variacao, macros, lag
            kpi = pg.evaluate("document.getElementById('kpis').textContent")
            check('cartoes de KPI renderizam (Peso, Gordura, Calorias, Net)',
                  all(t in kpi for t in ['Peso', 'Gordura', 'Calorias', 'Net']))
            tinta = pg.evaluate("""()=>{const out={};['chVar','chMacro'].forEach(id=>{const c=document.getElementById(id);
              const g=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;
              for(let i=3;i<g.length;i+=4) if(g[i]>0) n++; out[id]=n;});return out;}""")
            check('grafico de variacao desenha', tinta.get('chVar', 0) > 0, str(tinta))
            check('grafico de macros desenha', tinta.get('chMacro', 0) > 0, str(tinta))
            check('tabela de lag preenchida', pg.evaluate("document.getElementById('lagBody').rows.length") > 0)
            check('nenhum erro de JavaScript durante os testes', not pg.errs, str(pg.errs))
            pg.close()

            # 9b. capturas e larguras de tela (sinteticos)
            pg6 = pagina(payload)
            pg6.locator('#chMain').locator('..').screenshot(path=os.path.join(SHOTS, 'main_semana_1100.png'))
            pg6.select_option('#granul', 'M'); pg6.wait_for_timeout(150)
            pg6.locator('#chMain').locator('..').screenshot(path=os.path.join(SHOTS, 'main_mes_1100.png'))
            pg6.locator('#chCalNet').locator('..').screenshot(path=os.path.join(SHOTS, 'calorias_net_1100.png'))
            check('capturas do desktop geradas sem erros de JavaScript', not pg6.errs, str(pg6.errs))
            pg6.close()
            pg7 = pagina(payload, largura=390)
            check('390 px: figuras desenhadas sem erros de JavaScript',
                  not pg7.errs and pg7.evaluate("!!TEMP_ULT.figuras.chMain && !!TEMP_ULT.figuras.chCalNet"), str(pg7.errs))
            check('390 px: a area de desenho da figura principal tem largura util (>= 120 px)',
                  pg7.evaluate("(()=>{const W=document.getElementById('chMain').clientWidth;"
                               "const OFF=W<560?56:72;return W-58-(10+2*OFF);})()") >= 120)
            pg7.locator('#chMain').locator('..').screenshot(path=os.path.join(SHOTS, 'main_390.png'))
            pg7.close()

            # 10. sem dados: mensagem, sem erro
            vazio = {'status': 'OK', 'sheets_ok': True, 'erros_sheets': None, 'linhas': [],
                     'resumo': {'n_dias': 0, 'de': '-', 'ate': '-', 'cobertura': {}, 'actual': {}, 'tendencia_28d': {}},
                     'r7': {}, 'agrupado': [], 'periodo': 'W', 'variacao': {'peso': [], 'bf': []},
                     'bandas': {'peso': [], 'bf': []}, 'macros': [], 'lag': {'peso': {'melhor': {'r': None}, 'todos': []},
                                                                          'bf': {'melhor': {'r': None}, 'todos': []}}}
            pg2 = pagina(vazio)
            check('sem linhas: pagina nao quebra', not pg2.errs, str(pg2.errs))
            pg2.close()
            b.close()
    finally:
        srv.terminate()
    total = len(RESULTADOS)
    ok = sum(1 for _, v in RESULTADOS if v)
    print('\nRESULTADO VISAO TEMPORAL CORPORAL (sinteticos): %d/%d OK' % (ok, total))
    return 0 if ok == total else 1


if __name__ == '__main__':
    sys.exit(main())
