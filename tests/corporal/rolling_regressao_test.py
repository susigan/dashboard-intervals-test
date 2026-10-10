"""Regressao da media movel por periodo da aba Corporal (dados sinteticos).

Regra (como na referencia do Streamlit, mas com lacunas respeitadas):
  - media movel dos ultimos N periodos (N = seletor "Media movel": 2, 3, 4, 6, 8, 12);
  - cada periodo tem o resumo da propria granularidade (mediana ou media);
  - o valor so existe quando os N periodos da janela tem valor: nada e preenchido;
  - a linha so liga pontos com valor; uma lacuna interrompe a linha.

Compara, com as mesmas entradas:
  - rolPeriodo() do JavaScript (usado pelo grafico);
  - ref_rolp() em Python, implementacao independente;
  - a linha desenhada (segmentos realmente desenhados no canvas) com as corridas de valores.

Executar a partir da raiz do repositorio:
    python3 tests/corporal/rolling_regressao_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'teste-local')

from browser_grafico_test import check, RESULTADOS, gerar_payload  # noqa: E402

PORT = 8803
INICIO = date(2025, 6, 6)  # uma segunda-feira: cada periodo semanal comeca na segunda
CAMPOS = ['peso', 'bf', 'calorias', 'net']
INSTRUMENTA = """
(()=>{
 const P=CanvasRenderingContext2D.prototype;
 if(P.__rollInst) return; P.__rollInst=true;
 const ob={beginPath:P.beginPath,moveTo:P.moveTo,lineTo:P.lineTo,stroke:P.stroke};
 P.beginPath=function(){this.__cur=[];return ob.beginPath.call(this);};
 P.moveTo=function(x,y){(this.__cur=this.__cur||[]).push(['M',x,y]);return ob.moveTo.call(this,x,y);};
 P.lineTo=function(x,y){(this.__cur=this.__cur||[]).push(['L',x,y]);return ob.lineTo.call(this,x,y);};
 P.stroke=function(){
   if(this.strokeStyle==='#0d1117' && this.lineWidth===5){
     window.__halo=(window.__halo||[]).concat([(this.__cur||[]).slice()]);
   }
   return ob.stroke.call(this);};
})();
"""


def periodos_sinteticos(valores, campo_por_serie=None):
    """valores: lista de dicts {campo: valor ou None} uma por semana (segunda a domingo)."""
    out = []
    for i, v in enumerate(valores):
        ini = INICIO + timedelta(days=7 * i)
        out.append({'k': ini.isocalendar()[1], 'ini': ini.isoformat(),
                    'fim': (ini + timedelta(days=6)).isoformat(), 'v': v})
    return out


def ref_rolp(per, campo, N):
    """Referencia independente: media dos ultimos N periodos; None se algum falta."""
    out = []
    for i in range(len(per)):
        if i < N - 1:
            out.append(None)
            continue
        vals = [per[j]['v'].get(campo) for j in range(i - N + 1, i + 1)]
        out.append(sum(vals) / N if all(x is not None for x in vals) else None)
    return out


def js_rolp(pg, per, campo, N):
    return pg.evaluate("([p,c,N])=>rolPeriodo(p,c,N).map(q=>q.v)", [per, campo, N])


def semanas(*linhas):
    """Cada argumento: lista de valores de cada semana para o campo."""
    return list(linhas)


def cenarios():
    """Doze cenarios de regressao (sinteticos, semanas). Cada um devolve (nome, per, campo, N, esperado_extra)."""
    def mk(vals_campo, campo='peso'):
        return periodos_sinteticos([{campo: v} for v in vals_campo])
    c = []
    c.append(('1. quatro semanas seguidas com valor (N=4)', mk([80, 81, 82, 83, 84], 'peso'), 'peso', 4))
    c.append(('2. semana isolada sem valor (semana 3)', mk([80, 81, None, 83, 84, 85, 86], 'peso'), 'peso', 4))
    c.append(('3. tres semanas seguidas sem valor', mk([80, 81, None, None, None, 84, 85, 86, 87], 'peso'), 'peso', 4))
    c.append(('4. lacuna maior que a janela (6 semanas sem valor)',
              mk([80, 81, 82, 83] + [None] * 6 + [84, 85, 86, 87, 88, 89], 'peso'), 'peso', 4))
    c.append(('5. BF com frequencia diferente do peso (BF so em semanas pares)',
              periodos_sinteticos([{'peso': 80 + i, 'bf': (18.0 - 0.1 * i) if i % 2 == 0 else None}
                                   for i in range(10)]), 'bf', 4))
    c.append(('6. calorias com semanas ausentes', mk([2300, None, 2350, 2400, 2380, None, 2420, 2410], 'calorias'),
              'calorias', 3))
    c.append(('7a. Net ausente uma semana', mk([200, 210, None, 230, 240, 250], 'net'), 'net', 4))
    c.append(('7b. Net ausente um mes inteiro (4 semanas)',
              mk([200, 210, 220, 230, None, None, None, None, 260, 270, 280, 290, 300], 'net'), 'net', 4))
    c.append(('8. null no meio (semana sem valor valido no resumo)',
              periodos_sinteticos([{'peso': v} for v in [80, None, 81, 82, 83, 84, 85]]), 'peso', 4))
    c.append(('9. troca N = 2, 4 e 12 na mesma serie', mk([80 + 0.5 * i for i in range(14)], 'peso'), 'peso', 2))
    c.append(('10. granularidade: periodos mensais diferentes produzem outra serie', None, None, None))
    c.append(('11. comparacao com referencia Python (N=3, serie irregular)',
              mk([80, 79.5, None, 81, 80.2, 80.8, None, 82, 81.5], 'peso'), 'peso', 3))
    c.append(('12. nenhum valor sem N periodos completos (verificacao geral)', None, None, None))
    return c


def main():
    from playwright.sync_api import sync_playwright
    import tabs.tab_corporal as tc

    tmp = tempfile.mkdtemp(prefix='rolp_')
    open(os.path.join(tmp, 'corp.html'), 'w', encoding='utf-8').write(tc.render())
    srv = subprocess.Popen([sys.executable, '-m', 'http.server', str(PORT), '--bind', '127.0.0.1'],
                           cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1100, 'height': 900})
            erros = []
            pg.on('pageerror', lambda e: erros.append(str(e)[:200]))
            pg.route('**/api/corporal**', lambda r: r.fulfill(body='{"error":"sem dados (teste de funcao isolada)"}',
                                                              content_type='application/json'))
            pg.goto('http://127.0.0.1:%d/corp.html' % PORT, wait_until='load')
            pg.wait_for_timeout(700)
            print('Cenarios (periodos semanais; a referencia e a media dos N ultimos, com None se faltar algum):')
            for nome, per, campo, N in cenarios():
                if per is None:
                    continue
                esp = ref_rolp(per, campo, N)
                obt = js_rolp(pg, per, campo, N)
                ok = all((e is None) == (o is None) and (e is None or abs(e - o) < 1e-9) for e, o in zip(esp, obt))
                check(nome, ok and len(esp) == len(obt),
                      'esperado=%s obtido=%s' % ([None if x is None else round(x, 3) for x in esp],
                                                 [None if x is None else round(x, 3) for x in obt]))
            # 10. granularidade: mensal e semanal produzem series diferentes (pela mesma funcao, periodos distintos)
            semanal = periodos_sinteticos([{'peso': 80 + 0.2 * i} for i in range(12)])
            mensal = [{'k': m, 'ini': '2025-%02d-01' % m, 'fim': '2025-%02d-28' % m,
                       'v': {'peso': 80 + 0.5 * m}} for m in range(1, 13)]
            a = js_rolp(pg, semanal, 'peso', 4)
            bb = js_rolp(pg, mensal, 'peso', 4)
            check('10. granularidade muda a serie da media movel (semana x mes)', a != bb,
                  '%d vs %d periodos' % (len(a), len(bb)))
            # 11 (cenario a parte): confere a regra de janela completa na mesma entrada
            # 12. nenhum valor com menos de N periodos: todo valor tem N valores validos na janela
            todos = []
            for nome, per, campo, N in cenarios():
                if per is None: continue
                for i, v in enumerate(js_rolp(pg, per, campo, N)):
                    if v is not None:
                        janela = [per[j]['v'].get(campo) for j in range(i - N + 1, i + 1)]
                        todos.append(all(x is not None for x in janela) and i >= N - 1)
            check('12. nenhum valor sem N periodos completos na janela', all(todos) and len(todos) > 0,
                  '%d valores verificados' % len(todos))
            check('nenhum erro de JavaScript nos cenarios', not erros, str(erros))
            verificar_desenho(b, PORT)
            b.close()
    finally:
        srv.terminate()
    ok = sum(1 for _, v in RESULTADOS if v)
    total = len(RESULTADOS)
    print('\nRESULTADO MEDIA MOVEL POR PERIODO (regressao, sinteticos): %d/%d OK' % (ok, total))
    return 0 if ok == total else 1


def verificar_desenho(b, port_tmp):
    """Segmentos realmente desenhados (subcaminhos com lineTo, cor de halo) = corridas de valores."""
    pg = b.new_page(viewport={'width': 1100, 'height': 900})
    erros = []
    pg.on('pageerror', lambda e: erros.append(str(e)[:200]))
    dados = gerar_payload()
    pg.route('**/api/corporal**', lambda r: r.fulfill(body=json.dumps(dados), content_type='application/json'))
    pg.add_init_script(INSTRUMENTA)
    pg.goto('http://127.0.0.1:%d/corp.html' % port_tmp, wait_until='load')
    pg.wait_for_timeout(1200)
    series = [('chMain', 'peso'), ('chMain', 'bf'), ('chMain', 'calorias'),
              ('chCalNet', 'calorias'), ('chCalNet', 'net')]
    esperados = []
    for fig, m in series:
        rp = pg.evaluate("TEMP_ULT.figuras.%s.dados.%s.rolPts.map(q=>q.v)" % (fig, m))
        atual = 0
        for v in rp + [None]:
            if v is not None:
                atual += 1
            else:
                if atual >= 2: esperados.append(atual)
                atual = 0
    halo = pg.evaluate("window.__halo || []")
    ult = halo[-len(series):] if len(halo) >= len(series) else halo
    desenhados = []
    for caminho in ult:
        cont = 0
        for c in caminho:
            if c[0] == 'M':
                if cont >= 2: desenhados.append(cont)
                cont = 1
            else:
                cont += 1
        if cont >= 2: desenhados.append(cont)
    check('desenho: numero de trilhas = series com media movel (%d)' % len(series),
          len(ult) == len(series), 'trilhas=%d' % len(ult))
    check('desenho: segmentos desenhados = corridas de valores (lacunas nao sao ligadas)',
          sorted(desenhados) == sorted(esperados),
          'desenhados=%d esperados=%d' % (len(desenhados), len(esperados)))
    check('desenho: nenhum erro de JavaScript', not erros, str(erros))
    pg.close()


if __name__ == '__main__':
    sys.exit(main())
