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
from flask import jsonify, request
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
from tabs import tab_volume, tab_atividades, tab_detalhe, tab_recordes

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
    return jsonify(res)


@app.route('/api/sync/full')
def api_sync_full():
    """Sync completo: puxa ANOS_HISTORICO anos. Correr uma vez no inicio."""
    res = sync.sync_activities('full')
    invalidar_cache()
    return jsonify(res)


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


@app.route('/health')
def health():
    return jsonify({'status': 'healthy'}), 200


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
