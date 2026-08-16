#!/usr/bin/env python3
"""Intervals.icu Dashboard — servidor Flask.

Estrutura:
  app.py          rotas
  config.py       constantes (TYPE_MAP, cores, campos)
  api_client.py   cliente da API + cache + normalizacao
  helpers.py      ActivityProcessor
  tabs/           uma tab por ficheiro
"""

import os
import sys
import logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'utils'))

AQUECIMENTO_ERRO = None
try:
    import aquecimento_db as aq_db
    from aquecimento_analyzer import AquecimentoAnalyzer
    AQUECIMENTO_ENABLED = True
except Exception as e:
    aq_db = None
    AquecimentoAnalyzer = None
    AQUECIMENTO_ENABLED = False
    AQUECIMENTO_ERRO = f"{type(e).__name__}: {e}"
    print(f"[AQUECIMENTO] indisponivel -> {AQUECIMENTO_ERRO}")
from flask import jsonify, request, Response
from dotenv import load_dotenv

load_dotenv()

from config import API_KEY, ATHLETE_ID, ANOS_HISTORICO

if not API_KEY:
    print("ERRO: INTERVALS_ICU_API_KEY nao configurada")
    sys.exit(1)

print(f"Config carregada | ATHLETE_ID: {ATHLETE_ID} | historico: {ANOS_HISTORICO} anos")

from flask import Flask
import db
import sync
from datetime import datetime, timedelta
from api_client import (fetch_activities, cache_info, invalidar_cache,
                        fetch_da_api)
from tabs import (tab_volume, tab_atividades, tab_detalhe,
                  tab_recordes, tab_pmc, tab_corporal, tab_metabol)

if db.ENABLED:
    db.init_schema()
    print(f"Fonte de dados: {db.DRIVER} (com a API como fallback)")
else:
    print("Fonte de dados: API Intervals.icu (DATABASE_URL nao definida)")

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
logging.getLogger("werkzeug").setLevel(logging.WARNING)


# ── Paginas ───────────────────────────────────────────────────────────────

@app.route('/')
def page_volume():
    return tab_volume.render()


@app.route('/pmc')
def page_pmc():
    return tab_pmc.render()


@app.route('/corporal')
def page_corporal():
    return tab_corporal.render()


@app.route('/metabol')
def page_metabol():
    return tab_metabol.render()


@app.route('/atividades')
def page_atividades():
    return tab_atividades.render()


@app.route('/activity/<activity_id>')
def page_detalhe(activity_id):
    return tab_detalhe.render(activity_id)


# ── API por tab ───────────────────────────────────────────────────────────

@app.route('/api/volume')
def api_volume():
    return tab_volume.api_data()


@app.route('/api/pmc')
def api_pmc():
    return tab_pmc.api_data()


@app.route('/api/corporal')
def api_corporal():
    return tab_corporal.api_data()


@app.route('/api/metabol')
def api_metabol():
    return tab_metabol.api_data()


@app.route('/api/debug/sheets')
def api_debug_sheets():
    """Estado da ligacao aos Google Sheets e colunas reconhecidas."""
    return tab_pmc.api_sheets_debug()


@app.route('/api/atividades')
def api_atividades():
    return tab_atividades.api_data()


@app.route('/api/activity/<activity_id>/full')
def api_activity_full(activity_id):
    return tab_detalhe.api_full(activity_id)


# ── Debug e servico ───────────────────────────────────────────────────────

@app.route('/api/activity/<activity_id>/debug')
def api_activity_debug(activity_id):
    return tab_detalhe.api_debug(activity_id)


# ── Perfil fisiológico (lag/recovery SmO2/tHb/HR/respiração) ────────────────
#
# Imports feitos DENTRO das funções (lazy) e envolvidos em try/except: se
# fisiologia_worker.py tiver algum problema, só estas 2 rotas falham
# (devolvem erro 500 em JSON) — o resto do dashboard continua de pé.

@app.route('/api/fisiologia/debug/<activity_id>')
def api_fisiologia_debug(activity_id):
    """Mostra, para 1 atividade, como os intervalos foram interpretados:
    resposta bruta da API, classificação WORK/REC calculada pela potência
    (ignora o campo 'type' da API, que pode estar errado), pares
    WORK->REC, e uma simulação do processamento completo (lags/recovery)
    SEM gravar nada no .db.

    Exemplo: /api/fisiologia/debug/i174526190
    """
    try:
        import fisiologia_worker as fw
        return jsonify(fw.debug_dict(activity_id))
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/fisiologia/processar')
def api_fisiologia_processar():
    """Processa um lote de atividades (mais recentes -> mais antigas, até
    2024-01-01) e grava em fisiologia_perfil.db no Drive.

    Query params: ?n=5 (max LOTE_WEB_MAX por pedido, evita timeout Railway)
    Exemplo: /api/fisiologia/processar?n=5
    """
    try:
        import fisiologia_worker as fw
        n = int(request.args.get('n', 5))
        n = min(n, fw.LOTE_WEB_MAX)
        resumo = fw.processar_lote(n, retornar_resumo=True)
        return jsonify(resumo)
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/fisiologia/status')
def api_fisiologia_status():
    """Quantos intervalos válidos há já, por modalidade — para saber se
    já vale a pena pedir o perfil de cada uma.
    """
    try:
        from tabs import tab_metabol as tm
        return jsonify({'status': 'ok', 'modalidades': tm.modalidades_disponiveis()})
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/fisiologia/diagnostico')
def api_fisiologia_diagnostico():
    """Estado da persistência no Google Drive: credenciais, pasta, se o
    ficheiro existe lá, quantas linhas tem o .db local agora mesmo.

    Usa isto quando /api/fisiologia/processar disser "ok" mas a tab
    Metabolismo continuar vazia — revela se o upload para o Drive está
    mesmo a funcionar ou a falhar silenciosamente.
    """
    try:
        import drive_db_fisiologia as ddf
        return jsonify({'status': 'ok', 'diagnostico': ddf.diagnostico()})
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/fisiologia/perfil')
def api_fisiologia_perfil():
    """Curva watts -> métrica esperada (HR/SmO2/tHb/respiração/DFA1),
    valor e tempo de resposta/recuperação, por faixas de potência
    calculadas dinamicamente (não fixas) a partir dos dados reais.

    Query params:
      ?modalidade=Row      (obrigatório: Bike, Row, Ski ou Run)
      ?min_n=20             (mínimo de intervalos para calcular, default 20)
      ?n_faixas=4            (quantas faixas de watts, default 4)

    Exemplo: /api/fisiologia/perfil?modalidade=Row
    """
    try:
        from tabs import tab_metabol as tm
        modalidade = request.args.get('modalidade')
        if not modalidade:
            return jsonify({'erro': 'falta o parametro ?modalidade='}), 400
        min_n = int(request.args.get('min_n', 20))
        n_faixas = int(request.args.get('n_faixas', 4))
        resultado = tm.perfil_por_modalidade(modalidade, min_n_total=min_n, n_faixas=n_faixas)
        return jsonify(resultado)
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/fisiologia/perfil_grafico')
def api_fisiologia_perfil_grafico():
    """Igual a /api/fisiologia/perfil, mas devolve HTML Plotly pronto a
    embeber: 1 subplot por métrica (HR/SmO2/tHb/Respiração/DFA1), X =
    faixa de watts, linha+banda p25-p75 no esforço, tracejado cinza de
    referência em repouso.

    Query params: iguais a /api/fisiologia/perfil (?modalidade=, ?min_n=,
    ?n_faixas=)

    Exemplo: /api/fisiologia/perfil_grafico?modalidade=Row
    """
    try:
        from tabs import tab_metabol as tm
        modalidade = request.args.get('modalidade')
        if not modalidade:
            return jsonify({'erro': 'falta o parametro ?modalidade='}), 400
        min_n = int(request.args.get('min_n', 20))
        n_faixas = int(request.args.get('n_faixas', 4))

        perfil = tm.perfil_por_modalidade(modalidade, min_n_total=min_n, n_faixas=n_faixas)
        if perfil.get('status') != 'ok':
            return jsonify(perfil), 200  # dados insuficientes -- devolve o motivo, sem gráfico

        fig = tm.grafico_perfil_metabolico(perfil)
        html = fig.to_html(include_plotlyjs='cdn', div_id='perfil-metabolico-chart',
                           config={'responsive': True, 'displayModeBar': False})
        return jsonify({'status': 'ok', 'html': html, 'modalidade': modalidade,
                        'n_intervalos_total': perfil['n_intervalos_total']}), 200
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/fisiologia/evolucao')
def api_fisiologia_evolucao():
    """Deriva longitudinal de uma métrica, numa faixa de watts fixa
    (ao contrário do /perfil, aqui a faixa é a que tu pedires, para
    comparares sempre "a mesma pergunta" ao longo do tempo).

    Query params:
      ?modalidade=Row
      ?campo=smo2_medio_work   (ver tab_metabol.TODOS_CAMPOS para a lista)
      ?watts_min=250&watts_max=320
      ?agregacao=mes           (ou 'semana')

    Exemplo:
      /api/fisiologia/evolucao?modalidade=Row&campo=smo2_medio_work&watts_min=250&watts_max=320
    """
    try:
        from tabs import tab_metabol as tm
        modalidade = request.args.get('modalidade')
        campo = request.args.get('campo')
        if not modalidade or not campo:
            return jsonify({'erro': 'faltam parametros ?modalidade= e ?campo='}), 400
        watts_min = request.args.get('watts_min', type=float)
        watts_max = request.args.get('watts_max', type=float)
        agregacao = request.args.get('agregacao', 'mes')
        resultado = tm.evolucao_temporal(modalidade, campo, watts_min, watts_max, agregacao)
        return jsonify(resultado)
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/debug/athlete')
def api_debug_athlete():
    return tab_detalhe.api_debug_athlete()


@app.route('/api/cache')
def api_cache():
    return jsonify(cache_info())


@app.route('/api/cache/refresh')
def api_cache_refresh():
    acts = fetch_activities(force=True)
    return jsonify({'status': 'OK', 'count': len(acts or [])})


@app.route('/api/db')
def api_db():
    return jsonify(db.stats())


@app.route('/api/sync')
def api_sync():
    """Sync incremental: actividades + curvas de potencia.

    As curvas vivem numa tabela propria e nao se actualizam sozinhas quando
    chegam sessoes novas, por isso vao no mesmo passo. Sao 4 pedidos (um por
    modalidade), nao um por sessao. Com ?curvas=0 ficam de fora.
    """
    res = sync.sync_activities('incremental')
    invalidar_cache()
    if res.get('ok') and request.args.get('curvas') != '0':
        try:
            res['curvas'] = sync.sync_power_curves()
        except Exception as e:
            res['curvas'] = {'ok': False, 'erro': str(e)}
        try:
            # le do JSON ja guardado, nao gasta pedidos
            res['zonas'] = db.extrair_zone_times()
        except Exception as e:
            res['zonas'] = {'ok': False, 'erro': str(e)}
    return jsonify(res)


@app.route('/api/sync/full')
def api_sync_full():
    """Sync completo: puxa ANOS_HISTORICO anos. Correr uma vez no inicio."""
    res = sync.sync_activities('full')
    invalidar_cache()
    return jsonify(res)


@app.route('/api/export')
@app.route('/api/export/')
def api_export_indice():
    """Que exportacoes existem."""
    import export
    return jsonify(export.indice())


@app.route('/api/export/<nome>')
def api_export(nome):
    """Descarregar os dados em bruto.

    /api/export/atividades.csv   /api/export/curvas.json?tipo=Bike
    /api/export/tudo.json        todos num so ficheiro
    """
    import export
    import protocolo
    import sheets_client as sheets
    import pmc as _pmc

    base, _, ext = nome.rpartition('.')
    base = base or nome
    ext = (ext or 'csv').lower()
    tipo = request.args.get('tipo')

    try:
        if base == 'tudo':
            import csv as _csv_mod
            import io as _io

            def _linhas(txt):
                return list(_csv_mod.DictReader(_io.StringIO(txt)))

            # O wellness tem de passar pelo export.wellness(), que junta o
            # formulario com os campos da Intervals.icu (hrvSDNN_icu,
            # readiness_icu, etc). Usar sheets.carregar() directamente
            # trazia so o formulario, e o hrvSDNN nunca chegava a analise.
            wl, cp_, _erros = sheets.carregar()
            try:
                wl = _linhas(export.wellness(sheets))
            except Exception as e:
                print(f'wellness completo falhou, a usar so o formulario: {e}')
                wl = wl or []
            return jsonify({
                'gerado_em': datetime.now().isoformat(),
                'atividades': db.actividades_processadas(),
                'wellness': wl or [],
                'corporal': cp_ or [],
                'curvas': db.load_power_curves(tipo) or [],
                'cp_ajustado': db.cp_por_sessao() or [],
                'testes_maximos': _linhas(
                    export.testes_maximos(db, protocolo)),
            })

        if base == 'curvas':
            texto = export.curvas(db, tipo,
                                  'json' if ext == 'json' else 'longo')
        elif base == 'atividades':
            texto = export.atividades(db)
        elif base == 'wellness':
            texto = export.wellness(sheets)
        elif base == 'corporal':
            texto = export.corporal(sheets)
        elif base == 'testes':
            texto = export.testes_maximos(db, protocolo)
        elif base == 'cp':
            texto = export.cp_ajustado(db)
        elif base == 'serie_diaria':
            texto = export.serie_diaria(_pmc, db, sheets)
        else:
            return jsonify({'erro': f'"{base}" nao existe',
                            **export.indice()}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'erro': f'{type(e).__name__}: {e}',
                        'traceback': traceback.format_exc()[-1200:]}), 500

    mime = 'application/json' if ext == 'json' else 'text/csv'
    hoje = datetime.now().strftime('%Y%m%d')
    return Response(texto, mimetype=f'{mime}; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="{base}_{hoje}.{ext}"'})


@app.route('/api/debug/wellness-icu')
def api_debug_wellness_icu():
    """O wellness da Intervals.icu traz o HRV nocturno do Garmin?

    A Intervals.icu sincroniza do Garmin Connect e guarda os campos em
    /athlete/{id}/wellness. Se o teu relogio grava HRV nocturno e a
    sincronizacao esta ligada, o campo `hrv` vem de la — e nesse caso nao
    e preciso ir buscar nada a mao.

    Este endpoint compara: quantos dias tem hrv, desde quando, e como se
    relaciona com o que tens no formulario.
    """
    from api_client import icu_get, athlete_id_real
    import sheets_client

    aid = athlete_id_real()
    oldest = request.args.get('oldest', '2023-01-01')
    dados, err = icu_get(f'/athlete/{aid}/wellness',
                         params={'oldest': oldest,
                                 'newest': datetime.now().strftime('%Y-%m-%d')})
    if err:
        return jsonify({'erro': err}), 502
    if not isinstance(dados, list):
        return jsonify({'erro': 'resposta inesperada',
                        'tipo': str(type(dados))}), 502

    campos = ['hrv', 'hrvSDNN', 'restingHR', 'sleepSecs', 'sleepScore',
              'sleepQuality', 'avgSleepingHR', 'readiness', 'respiration',
              'spO2', 'baevskySI', 'steps', 'weight', 'bodyFat',
              'kcalConsumed', 'carbohydrates', 'protein', 'fatTotal']
    resumo = {}
    for c in campos:
        vals = [(d.get('id'), d.get(c)) for d in dados
                if isinstance(d.get(c), (int, float))]
        if not vals:
            resumo[c] = {'n': 0}
            continue
        datas = sorted(v[0] for v in vals)
        nums = [v[1] for v in vals]
        resumo[c] = {
            'n': len(vals), 'primeiro': datas[0], 'ultimo': datas[-1],
            'media': round(sum(nums) / len(nums), 2),
            'min': round(min(nums), 2), 'max': round(max(nums), 2)}

    # cruzar com o formulario, para ver se sao a mesma medicao
    comparacao = None
    try:
        w, _c, _e = sheets_client.carregar()
        form = {x['date']: x.get('hrv') for x in (w or [])
                if isinstance(x.get('hrv'), (int, float))}
        pares = [(d.get('hrv'), form.get(d.get('id'))) for d in dados
                 if isinstance(d.get('hrv'), (int, float))
                 and isinstance(form.get(d.get('id')), (int, float))]
        if len(pares) >= 20:
            import statistics as st
            xs = [p[0] for p in pares]
            ys = [p[1] for p in pares]
            mx, my = st.mean(xs), st.mean(ys)
            num = sum((a - mx) * (b - my) for a, b in pares)
            den = (sum((a - mx) ** 2 for a in xs)
                   * sum((b - my) ** 2 for b in ys)) ** 0.5
            r = num / den if den else None
            comparacao = {
                'dias_em_comum': len(pares),
                'media_intervals': round(mx, 2),
                'media_formulario': round(my, 2),
                'diferenca': round(mx - my, 2),
                'r': round(r, 4) if r else None,
                'leitura': (
                    'praticamente a mesma medicao — o teu formulario e a '
                    'fonte' if r and r > 0.95 else
                    'muito parecidas mas nao identicas' if r and r > 0.85 else
                    'medicoes DIFERENTES — o hrv da Intervals.icu pode vir do '
                    'Garmin, o que seria exactamente o que procuramos')}
    except Exception as e:
        comparacao = {'erro': str(e)}

    return jsonify({
        'status': 'OK', 'athlete': aid,
        'registos': len(dados),
        'periodo': {'de': oldest, 'ate': datetime.now().strftime('%Y-%m-%d')},
        'campos': resumo,
        'comparacao_com_formulario': comparacao,
        'como_ler': (
            'Se `hrv` tiver muitos dias E a comparacao disser "medicoes '
            'diferentes", entao a Intervals.icu ja traz o HRV do Garmin e '
            'podemos usa-lo directamente, sem o script do Colab. Se `hrv` '
            'vier vazio, e preciso ligar a sincronizacao Garmin -> '
            'Intervals.icu nas definicoes da Intervals.icu.'),
    })


@app.route('/api/protocolo')
def api_protocolo():
    """Testes maximos detectados e quais estao em atraso.

    ?tipo=Bike   filtra por modalidade
    """
    import protocolo
    curvas = db.load_power_curves(request.args.get('tipo') or None)
    det = protocolo.detectar_testes(curvas or [])
    return jsonify({
        'status': 'OK',
        'cobertura': protocolo.cobertura(det),
        'sugestoes': protocolo.sugerir(det),
        'robustez': protocolo.robustez(det),
        # so os recentes na resposta: a lista completa pode ter centenas
        'detectados': {m: {s: {k: v for k, v in d.items() if k != 'testes'}
                           for s, d in durs.items()}
                       for m, durs in det.items()},
        'como_funciona': (
            f'Uma sessao conta como teste quando o esforco numa duracao chega '
            f'a {int(protocolo.LIMIAR_ESFORCO*100)}% do teu melhor dos ultimos '
            f'{protocolo.JANELA_MELHOR} dias. Nao e preciso marcar nada.'),
    })


@app.route('/api/calibracao')
def api_calibracao():
    """Parametros calibrados nos dados do atleta, com a evidencia.

    E o mesmo calculo que alimenta o FMT — aqui exposto sozinho, para se
    poder ver e auditar os valores sem abrir a tab.
    """
    return _seguro(tab_pmc.api_calibracao_dados)


@app.route('/api/sync/curvas')
def api_sync_curvas():
    """Curvas de potencia por sessao — base dos recordes.

    Uma chamada por modalidade, nao uma por sessao.
    """
    return jsonify(sync.sync_power_curves())


@app.route('/api/recordes')
def api_recordes():
    return tab_recordes.api_data()


@app.route('/recordes')
def page_recordes():
    return tab_recordes.render()


@app.route('/api/recordes/seasons')
def api_recordes_seasons():
    """Melhor curva por periodo. ?por=season (default) ou ?por=ano"""
    return tab_recordes.api_seasons()


@app.route('/api/activity/<activity_id>/prs')
def api_activity_prs(activity_id):
    return jsonify(db.prs_da_actividade(activity_id) or {'erro': 'sem curva guardada'})


@app.route('/api/frescura')
def api_frescura():
    """Ha quanto tempo a base foi actualizada e se ha sessoes novas na API.

    Compara a data mais recente na base com a data mais recente na
    Intervals.icu, sem gravar nada. Serve para o aviso no topo das paginas.
    """
    if not db.ENABLED:
        return jsonify({'db': False, 'nota': 'sem base de dados; le sempre da API'})

    ult = db.ultima_data()
    info = {'db': True,
            'ultima_na_base': ult.isoformat() if ult else None,
            'last_sync': None, 'novas': None}

    linha = db._exec("""SELECT criado_em FROM sync_log
                        WHERE erro IS NULL ORDER BY id DESC LIMIT 1""", fetch='one')
    if linha and linha[0]:
        info['last_sync'] = str(linha[0])

    if request.args.get('verificar') in ('1', 'true'):
        desde = ((ult - timedelta(days=1)).strftime("%Y-%m-%d") if ult
                 else datetime.now().strftime("%Y-%m-%d"))
        acts, err = fetch_da_api(desde)
        if err:
            info['erro'] = err
        else:
            ids = db.ids_existentes()
            novas = [a for a in (acts or []) if a.get('id') not in ids]
            info['novas'] = len(novas)
            info['novas_detalhe'] = [{
                'id': a.get('id'), 'date': (a.get('start_date_local') or '')[:10],
                'name': a.get('name'), 'type': a.get('type')} for a in novas[:10]]
    return jsonify(info)


@app.route('/api/db/schema')
def api_db_schema():
    """Colunas de cada tabela — para perceber erros 500 de SQL."""
    return jsonify({t: db.colunas_de(t) for t in
                    ('activities', 'power_curves', 'streams', 'sync_log')})


@app.route('/api/db/recriar-curvas')
def api_recriar_curvas():
    """Recria a tabela de curvas quando o esquema mudou."""
    res = db.recriar_power_curves()
    if res.get('ok'):
        try:
            res['sync'] = sync.sync_power_curves()
        except Exception as e:
            res['sync'] = {'ok': False, 'erro': str(e)}
    return jsonify(res)


@app.route('/api/debug/curvas')
def api_debug_curvas():
    """Testa variantes do endpoint de curvas para perceber o 403.

    Compara: id "0" vs id real, com e sem extensao .json, e o endpoint por
    actividade (que sabemos funcionar). Assim vemos qual das diferencas conta.
    """
    from api_client import icu_get, athlete_id_real
    from datetime import datetime as _dt

    real = athlete_id_real()
    oldest = (_dt.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    newest = _dt.now().strftime("%Y-%m-%d")
    base_p = {"oldest": oldest, "newest": newest, "type": "Ride"}

    uma = db._exec("""SELECT id FROM activities WHERE type = 'Bike'
                      ORDER BY date DESC LIMIT 1""", fetch='one') if db.ENABLED else None
    aid = uma[0] if uma else None

    testes = [
        ('activities (controlo)', f"/athlete/{ATHLETE_ID}/activities",
         {"oldest": oldest}),
        ('perfil id=0', f"/athlete/{ATHLETE_ID}", None),
        ('perfil id real', f"/athlete/{real}", None),
        ('curvas id=0', f"/athlete/{ATHLETE_ID}/activity-power-curves", base_p),
        ('curvas id real', f"/athlete/{real}/activity-power-curves", base_p),
        ('curvas id real .json', f"/athlete/{real}/activity-power-curves.json", base_p),
        ('power-curves id real', f"/athlete/{real}/power-curves",
         {"type": "Ride", "curves": "42d"}),
    ]
    if aid:
        testes.append((f'power-curve da sessao {aid}', f"/activity/{aid}/power-curve", None))

    out = {'athlete_id_configurado': ATHLETE_ID, 'athlete_id_resolvido': real,
           'testes': {}}
    for nome, path, params in testes:
        data, err = icu_get(path, params, timeout=60)
        if err:
            out['testes'][nome] = {'ok': False, 'erro': err[:160]}
        elif isinstance(data, dict):
            out['testes'][nome] = {'ok': True, 'chaves': sorted(data.keys())[:12],
                                   'n_curvas': len(data.get('curves') or [])}
        elif isinstance(data, list):
            out['testes'][nome] = {'ok': True, 'n': len(data)}
        else:
            out['testes'][nome] = {'ok': True, 'tipo': type(data).__name__}
    return jsonify(out)


@app.route('/api/db/curvas')
def api_db_curvas():
    """Diagnostico da tabela de curvas."""
    return jsonify(db.diagnostico_curvas())


@app.route('/api/debug/zonas')
def api_debug_zonas():
    """Que custom_zones existem por modalidade, e onde faltam."""
    return jsonify(db.diagnostico_zonas())


@app.route('/api/sync/zonas')
def api_sync_zonas():
    """Extrai o tempo por zona do JSON ja guardado. Nao gasta pedidos a API."""
    return jsonify(db.extrair_zone_times())


@app.route('/api/zonas')
def api_zonas():
    """Tempo por zona, para os graficos.

    ?tipo=Bike  ?kind=power|hr|pace  ?desde=YYYY-MM-DD
    """
    return jsonify({
        'status': 'OK',
        'disponiveis': db.zonas_disponiveis(),
        'sessoes': db.tempo_por_zona(
            request.args.get('tipo') or None,
            request.args.get('kind') or 'power',
            request.args.get('desde') or None),
    })


def _seguro(fn, *args, **kwargs):
    """Corre fn e devolve o erro em JSON em vez de 500.

    Um endpoint de diagnostico que rebenta com 500 nao diz nada; a mensagem
    de erro e precisamente a informacao util.
    """
    try:
        return jsonify(fn(*args, **kwargs))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'erro': f'{type(e).__name__}: {e}',
                        'traceback': traceback.format_exc()[-1200:]}), 500


@app.route('/api/sync/streams')
def api_sync_streams():
    """Carrega streams em bloco.

    ?limite=60          quantas sessoes por chamada (1 pedido cada)
    ?tipos=Bike,Row     so estas modalidades
    ?desde=YYYY-MM-DD   so a partir desta data
    Repetir ate 'faltam' chegar a zero.
    """
    tipos = request.args.get('tipos')
    return _seguro(sync.sync_streams_bloco,
                   limite=request.args.get('limite', default=60, type=int),
                   tipos=[t.strip() for t in tipos.split(',')] if tipos else None,
                   desde=request.args.get('desde'))


@app.route('/api/db/streams')
def api_db_streams():
    """Cobertura dos streams guardados."""
    return _seguro(db.streams_stats)


@app.route('/fisiologia/perfil_grafico_enhanced/<modalidade>')
def page_perfil_grafico_enhanced(modalidade):
    """Página HTML com gráfico dual-axis (watts + pace) do perfil."""
    try:
        from tabs import tab_metabol_enhanced as tme
        min_n = int(request.args.get('min_n', 20))
        n_faixas = int(request.args.get('n_faixas', 10))
        enh = tme.MetabolicProfileEnhanced(modalidade)
        perfil = enh.gerar_perfil_com_pace(modalidade, min_n_total=min_n, n_faixas=n_faixas)
        if perfil.get('status') != 'ok':
            return f"<h1>Perfil — {modalidade}</h1><p>Dados insuficientes</p>"
        fig = enh.grafico_perfil_dual_axis(perfil)
        html = fig.to_html(include_plotlyjs='cdn', div_id=f'perfil-{modalidade}',
                          config={'responsive': True, 'displayModeBar': True})
        return f"""<html><head><title>Perfil {modalidade}</title><meta charset="utf-8"></head><body>
                <h1>Perfil Metabólico — {modalidade} (Dual Axis: Watts + Pace)</h1>{html}
                <p><a href="/">← Voltar</a></p></body></html>"""
    except Exception as e:
        import traceback
        return f"<h1>Erro</h1><pre>{traceback.format_exc()}</pre>", 500


@app.route('/fisiologia/evolucao_grafico/<modalidade>/<campo>')
def page_evolucao_grafico(modalidade, campo):
    """Página HTML com gráfico evolução temporal (watts + pace)."""
    try:
        from tabs import tab_metabol_enhanced as tme
        watts_min = request.args.get('watts_min', type=float)
        watts_max = request.args.get('watts_max', type=float)
        agregacao = request.args.get('agregacao', 'mes')
        resultado = tme.evolucao_temporal_com_pace(modalidade, campo, watts_min, watts_max, agregacao)
        if resultado.get('status') != 'ok':
            return f"<h1>Evolução — {modalidade}</h1><p>Dados insuficientes</p>"
        fig = tme.grafico_evolucao_dual_axis(resultado)
        html = fig.to_html(include_plotlyjs='cdn', div_id=f'evolucao-{modalidade}',
                          config={'responsive': True, 'displayModeBar': True})
        return f"""<html><head><title>Evolução {modalidade}</title><meta charset="utf-8"></head><body>
                <h1>Evolução {campo} — {modalidade} (Dual Axis: Watts + Pace)</h1>{html}
                <p><a href="/">← Voltar</a></p></body></html>"""
    except Exception as e:
        import traceback
        return f"<h1>Erro</h1><pre>{traceback.format_exc()}</pre>", 500


# ── Novas rotas: DFA-α1 + Pace/Watts ──────────────────────────────────────

@app.route('/api/fisiologia/validacao_dfa/<modalidade>')
def api_validacao_dfa(modalidade):
    """Validar qualidade de DFA-α1 para uma modalidade."""
    try:
        from tabs import tab_metabol_enhanced as tme
        resultado = tme.validacao_lote_dfa(modalidade)
        return jsonify(resultado)
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/fisiologia/perfil_enhanced/<modalidade>')
def api_perfil_enhanced(modalidade):
    """Perfil metabólico com coluna pace adicionada (Row/Ski)."""
    try:
        from tabs import tab_metabol_enhanced as tme
        min_n = int(request.args.get('min_n', 20))
        n_faixas = int(request.args.get('n_faixas', 10))
        enh = tme.MetabolicProfileEnhanced(modalidade)
        resultado = enh.gerar_perfil_com_pace(modalidade, min_n_total=min_n, n_faixas=n_faixas)
        return jsonify(resultado)
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/fisiologia/evolucao_com_pace')
def api_evolucao_com_pace():
    """Evolução temporal com pace secundário (Row/Ski)."""
    try:
        from tabs import tab_metabol_enhanced as tme
        modalidade = request.args.get('modalidade')
        campo = request.args.get('campo')
        if not modalidade or not campo:
            return jsonify({'erro': 'faltam parametros ?modalidade= e ?campo='}), 400
        watts_min = request.args.get('watts_min', type=float)
        watts_max = request.args.get('watts_max', type=float)
        agregacao = request.args.get('agregacao', 'mes')
        resultado = tme.evolucao_temporal_com_pace(modalidade, campo, watts_min, watts_max, agregacao)
        return jsonify(resultado)
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/health')
def health():
    return jsonify({'status': 'healthy'}), 200









# ===== ROTAS QUE A TAB METABOLISMO CONSOME =====

@app.route('/api/fisiologia/perfil_robusto/<modalidade>')
def api_fisiologia_perfil_robusto(modalidade):
    """Perfil ponderado por faixas de watts. Consumido por carregarPerfil()."""
    try:
        from tabs import tab_metabol as tm
        largura = int(request.args.get('largura_bin', 50))
        min_n = int(request.args.get('min_n', 15))
        campos = {k: v for k, v in request.args.items()
                  if k not in ('largura_bin', 'min_n')}
        if not campos:
            campos = {'hr': 'max', 'resp': 'avg', 'smo2': 'min', 'dfa1': 'avg'}
        res = tm.perfil_por_modalidade(modalidade, campos,
                                       min_n_total=min_n,
                                       largura_bin_manual=largura)
        return jsonify(res), 200
    except Exception as e:
        import traceback
        return jsonify({'status': 'erro', 'mensagem': str(e),
                        'trace': traceback.format_exc()}), 200


@app.route('/api/fisiologia/evolucao_robusta')
def api_fisiologia_evolucao_robusta():
    """Evolucao temporal de uma metrica. Consumido por carregarEvolucao()."""
    try:
        from tabs import tab_metabol as tm
        modalidade = request.args.get('modalidade')
        metrica = request.args.get('metrica')
        agregacao = request.args.get('agregacao')
        if not (modalidade and metrica and agregacao):
            return jsonify({'status': 'erro',
                            'mensagem': 'faltam modalidade/metrica/agregacao'}), 200
        res = tm.evolucao_temporal(
            modalidade, metrica, agregacao,
            watts_min=request.args.get('watts_min', type=float),
            watts_max=request.args.get('watts_max', type=float))
        return jsonify(res), 200
    except Exception as e:
        import traceback
        return jsonify({'status': 'erro', 'mensagem': str(e),
                        'trace': traceback.format_exc()}), 200


# ===== ROTAS AQUECIMENTO =====

def _aq_indisponivel():
    return jsonify({'status': 'erro', 'mensagem': 'Aquecimento não disponível',
                    'causa': AQUECIMENTO_ERRO}), 503


@app.route('/api/aquecimento/estado')
def api_aquecimento_estado():
    """Diagnostico: modulos, BD e o que ja' foi analisado."""
    if not AQUECIMENTO_ENABLED:
        return _aq_indisponivel()
    try:
        conn = aq_db.get_conn()
        rej = conn.execute("SELECT COUNT(*) FROM aquecimento_rejeitadas").fetchone()[0]
        return jsonify({
            'status': 'ok',
            'modulos_carregados': True,
            'protocolos': __import__('aquecimento_analyzer').PROTOCOLOS,
            'modalidades': aq_db.modalidades_disponiveis(),
            'sessoes_detectadas': len(aq_db.listar_sessoes()),
            'atividades_rejeitadas': rej,
        })
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500


@app.route('/api/aquecimento/dados')
def api_aquecimento_dados():
    """Sessoes detectadas. ?modalidade=Row para filtrar."""
    if not AQUECIMENTO_ENABLED:
        return _aq_indisponivel()
    try:
        mod = request.args.get('modalidade')
        sessoes = aq_db.listar_sessoes(mod)
        return jsonify({'status': 'ok', 'modalidade': mod,
                        'sessoes': sessoes, 'total': len(sessoes)})
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500


@app.route('/api/aquecimento/blocos')
def api_aquecimento_blocos():
    """Blocos individuais. ?modalidade=Row&watts_alvo=160"""
    if not AQUECIMENTO_ENABLED:
        return _aq_indisponivel()
    try:
        blocos = aq_db.listar_blocos(request.args.get('modalidade'),
                                     request.args.get('watts_alvo', type=int))
        return jsonify({'status': 'ok', 'blocos': blocos, 'total': len(blocos)})
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500


@app.route('/api/aquecimento/sessao/<activity_id>')
def api_aquecimento_sessao(activity_id):
    if not AQUECIMENTO_ENABLED:
        return _aq_indisponivel()
    try:
        s = aq_db.obter_sessao(activity_id)
        if not s:
            return jsonify({'status': 'erro', 'mensagem': 'sessao nao encontrada'}), 404
        return jsonify({'status': 'ok', 'sessao': s})
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500


@app.route('/api/aquecimento/serie')
def api_aquecimento_serie():
    """Serie temporal por escalao de watts, com SEM/MDC.

    ?modalidade=Row&metrica=hr&agregacao=avg[&rolling=3]
    """
    if not AQUECIMENTO_ENABLED:
        return _aq_indisponivel()
    try:
        from aquecimento_analyzer import sem_por_pares
        mod = request.args.get('modalidade')
        metrica = request.args.get('metrica', 'hr')
        agreg = request.args.get('agregacao', 'avg')
        rolling = request.args.get('rolling', type=int)
        if not mod:
            return jsonify({'status': 'erro', 'mensagem': 'falta ?modalidade='}), 400

        campo = f'{metrica}_{agreg}'
        blocos = aq_db.listar_blocos(mod)
        if not blocos:
            return jsonify({'status': 'sem_dados', 'modalidade': mod,
                            'mensagem': 'nenhum aquecimento detectado ainda'}), 200

        por_watts = {}
        for b in blocos:
            por_watts.setdefault(b['watts_alvo'], []).append((b['data'], b.get(campo)))

        saida = []
        for w in sorted(por_watts):
            pts = [(d, v) for d, v in por_watts[w] if v is not None]
            pts.sort(key=lambda p: p[0])
            valores = [v for _, v in pts]
            if rolling and rolling > 1 and valores:
                suav, jan = [], max(1, min(rolling, 12))
                for i in range(len(valores)):
                    ini = max(0, i - jan + 1)
                    suav.append(sum(valores[ini:i + 1]) / len(valores[ini:i + 1]))
                valores = suav
            saida.append({
                'watts_alvo': w,
                'n': len(pts),
                'datas': [d for d, _ in pts],
                'valores': valores,
                'reliability': sem_por_pares(pts),
            })

        return jsonify({'status': 'ok', 'modalidade': mod, 'metrica': metrica,
                        'agregacao': agreg, 'rolling': rolling, 'series': saida})
    except Exception as e:
        import traceback
        return jsonify({'status': 'erro', 'mensagem': str(e),
                        'trace': traceback.format_exc()}), 500


@app.route('/api/aquecimento/calibrar', methods=['GET', 'POST'])
def api_aquecimento_calibrar():
    """Varre atividades historicas a procura do protocolo.

    ?modalidade=Row[&desde=2024-01-01][&limite=500]
    Sem ?desde=, comeca na atividade mais antiga da modalidade.
    """
    if not AQUECIMENTO_ENABLED:
        return _aq_indisponivel()
    try:
        import drive_db_fisiologia as ddf
        if request.method == 'POST' and request.get_json(silent=True):
            body = request.get_json()
            mod = body.get('modalidade')
            desde = body.get('desde')
            limite = int(body.get('limite', 500))
        else:
            mod = request.args.get('modalidade')
            desde = request.args.get('desde')
            limite = request.args.get('limite', default=500, type=int)

        conn = ddf.get_conn()
        cond, params = ['valido = 1'], []
        if mod:
            cond.append('modalidade = ?')
            params.append(mod)
        if desde:
            cond.append('data >= ?')
            params.append(desde)
        params.append(limite)

        atividades = conn.execute(
            f"""SELECT activity_id, modalidade, MAX(data) AS data
                FROM fisiologia_intervalos
                WHERE {' AND '.join(cond)}
                GROUP BY activity_id, modalidade
                ORDER BY data ASC LIMIT ?""", tuple(params)).fetchall()

        analyzer = AquecimentoAnalyzer(conn)
        detectados, rejeitados, saltados, motivos = 0, 0, 0, {}

        for row in atividades:
            aid, modalidade, data = row[0], row[1], row[2]
            if modalidade not in ('Row', 'Ski', 'Bike'):
                continue
            if aq_db.ja_analisada(aid):
                saltados += 1
                continue
            r = analyzer.analisar_atividade(aid, modalidade)
            if r.get('detectado'):
                aq_db.salvar_blocos(aid, modalidade, data, r['blocos'], sync=False)
                detectados += 1
            else:
                motivo = r.get('motivo', 'desconhecido')
                aq_db.marcar_rejeitada(aid, modalidade, data, motivo)
                motivos[motivo] = motivos.get(motivo, 0) + 1
                rejeitados += 1

        aq_db.sincronizar()
        return jsonify({'status': 'ok', 'modalidade': mod, 'desde': desde,
                        'analisadas': len(atividades), 'detectados': detectados,
                        'rejeitados': rejeitados, 'ja_analisadas': saltados,
                        'motivos': motivos})
    except Exception as e:
        import traceback
        return jsonify({'status': 'erro', 'mensagem': str(e),
                        'trace': traceback.format_exc()}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
