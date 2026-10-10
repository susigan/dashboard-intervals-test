"""Calorias estimadas (regressao com atraso sobre peso e BF) - dados sinteticos.

Regra testada (funcoes JavaScript da aba Corporal):
  - para X = peso e BF, e atraso L = 0..4 periodos, ajusta calorias(i) = a + b * X(i+L);
  - o atraso de maior R2 (com pelo menos 8 pares) fica para cada X;
  - estimativa = media dos modelos disponiveis, ponderada pelo R2;
  - so existe onde nao ha calorias medidas e ha X no periodo do atraso;
  - a media movel de calorias usa a estimativa nas lacunas e marca os pontos estimados.

Caso de controle: calorias = 2000 + 40 * peso(i+2), exatamente. O atraso 2 deve vencer
com R2 = 1 e as estimativas nas lacunas devem ser exatas.

Executar a partir da raiz do repositorio:
    python3 tests/corporal/calorias_estimadas_test.py
"""
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

from browser_grafico_test import check, RESULTADOS  # noqa: E402

PORT = 8804
INICIO = date(2025, 1, 6)


def periodos(valores):
    """valores: lista de dicts por semana (peso, bf, calorias)."""
    out = []
    for i, v in enumerate(valores):
        ini = INICIO + timedelta(days=7 * i)
        out.append({'k': 'S%02d' % i, 'ini': ini.isoformat(), 'fim': (ini + timedelta(days=6)).isoformat(),
                    'v': v, 'n': {}, 'dias': 7})
    return out


def ols(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    b = sxy / sxx
    return {'a': my - b * mx, 'b': b, 'r2': sxy * sxy / (sxx * syy)}


def ref_modelos(per):
    """Referencia Python: melhor atraso por X (R2 maximo com >= 8 pares)."""
    out = {}
    for X in ['peso', 'bf']:
        melhor = None
        for L in range(5):
            xs, ys = [], []
            for i in range(len(per) - L):
                y, x = per[i]['v'].get('calorias'), per[i + L]['v'].get(X)
                if y is not None and x is not None:
                    xs.append(x)
                    ys.append(y)
            if len(xs) < 8:
                continue
            m = ols(xs, ys)
            if m and (melhor is None or m['r2'] > melhor['r2']):
                melhor = dict(m, L=L)
        if melhor and melhor['r2'] > 0:
            out[X] = melhor
    return out


def ref_estimar(per):
    mods = ref_modelos(per)
    out = []
    for i, q in enumerate(per):
        if q['v'].get('calorias') is not None or not mods:
            out.append(None)
            continue
        s = w = 0.0
        for X, m in mods.items():
            j = i + m['L']
            if j >= len(per) or per[j]['v'].get(X) is None:
                continue
            s += m['r2'] * (m['a'] + m['b'] * per[j]['v'][X])
            w += m['r2']
        out.append(s / w if w > 0 else None)
    return out


def dados_controle():
    """Peso com tendencia e oscilacao; calorias = 2000 + 40 * peso(i+2) exatamente, com lacunas."""
    n = 30
    peso = [80 + 0.15 * i + 0.8 * ((i * 7) % 5 - 2) for i in range(n)]
    cal = []
    for i in range(n):
        exato = 2000 + 40 * peso[i + 2] if i + 2 < n else None
        cal.append(None if (i % 6 == 4 or i in (10, 11, 12)) else exato)
    vals = [{'peso': peso[i], 'bf': None, 'calorias': cal[i]} for i in range(n)]
    return vals


def main():
    from playwright.sync_api import sync_playwright
    import tabs.tab_corporal as tc

    tmp = tempfile.mkdtemp(prefix='estim_')
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
            pg.route('**/api/corporal**', lambda r: r.fulfill(body='{"error":"sem dados (teste isolado)"}',
                                                              content_type='application/json'))
            pg.goto('http://127.0.0.1:%d/corp.html' % PORT, wait_until='load')
            pg.wait_for_timeout(700)

            per = periodos(dados_controle())
            mods_js = pg.evaluate("p=>modelosCalorias(p)", per)
            mods_py = ref_modelos(per)
            check('controle: peso escolhe atraso 2 semanas (relacao exata)',
                  mods_js.get('peso', {}).get('L') == 2, str(mods_js.get('peso')))
            check('controle: R2 do peso = 1 (relacao exata)',
                  abs(mods_js.get('peso', {}).get('r2', 0) - 1.0) < 1e-9, str(mods_js.get('peso', {}).get('r2')))
            check('controle: coeficientes a=2000 e b=40 (exatos)',
                  abs(mods_js['peso']['a'] - 2000) < 1e-6 and abs(mods_js['peso']['b'] - 40) < 1e-6,
                  str((mods_js['peso']['a'], mods_js['peso']['b'])))
            check('controle: modelos JS = referencia Python (atraso e R2)',
                  all(mods_js[X]['L'] == mods_py[X]['L'] and abs(mods_js[X]['r2'] - mods_py[X]['r2']) < 1e-9
                      for X in mods_py if X in mods_js) and set(mods_js) == set(mods_py))

            est_js = pg.evaluate("p=>estimarCalorias(p).map(q=>q.v)", per)
            est_py = ref_estimar(per)
            ok_est = all((a is None) == (b_ is None) and (a is None or abs(a - b_) < 1e-6)
                         for a, b_ in zip(est_js, est_py))
            check('estimativas JS = referencia Python em todos os periodos', ok_est)
            medidos = [q['v']['calorias'] is not None for q in per]
            check('estimativas so onde nao ha calorias medidas',
                  all(est_js[i] is None for i in range(len(per)) if medidos[i]))
            exatos = [i for i in range(len(per)) if not medidos[i] and i + 2 < len(per)]
            check('lacunas com peso futuro sao estimadas com o valor exato (2000 + 40 * peso)',
                  all(est_js[i] is not None and abs(est_js[i] - (2000 + 40 * per[i + 2]['v']['peso'])) < 1e-6
                      for i in exatos) and len(exatos) > 0, '%d lacunas' % len(exatos))
            check('ultimas semanas sem peso futuro nao recebem estimativa',
                  est_js[-1] is None and est_js[-2] is None)

            # media movel mista: janela com lacuna estimada e marcada; janela so medida nao
            roll = pg.evaluate("p=>rolCaloriasMisto(p,estimarCalorias(p),4)", per)
            com_est = [i for i, r in enumerate(roll) if r['est']]
            so_medido = [i for i, r in enumerate(roll) if r['v'] is not None and not r['est']]
            check('media movel mista: ha janelas marcadas como estimadas', len(com_est) > 0, str(len(com_est)))
            check('media movel mista: janelas so com medicao nao sao marcadas',
                  all(not roll[i]['est'] for i in so_medido) and len(so_medido) > 0)
            i_teste = com_est[0]
            janela = [per[k]['v']['calorias'] if per[k]['v']['calorias'] is not None else est_py[k]
                      for k in range(i_teste - 3, i_teste + 1)]
            check('media movel mista: valor = media dos 4 periodos (medido ou estimado)',
                  abs(roll[i_teste]['v'] - sum(janela) / 4) < 1e-6, '%.3f' % roll[i_teste]['v'])
            check('nenhum erro de JavaScript', not erros, str(erros))
            b.close()
    finally:
        srv.terminate()
    ok = sum(1 for _, v in RESULTADOS if v)
    total = len(RESULTADOS)
    print('\nRESULTADO CALORIAS ESTIMADAS (regressao, sinteticos): %d/%d OK' % (ok, total))
    return 0 if ok == total else 1


if __name__ == '__main__':
    sys.exit(main())
