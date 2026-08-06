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
from flask import jsonify
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
from api_client import fetch_activities, cache_info, invalidar_cache
from tabs import tab_volume, tab_atividades, tab_detalhe

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
    """Sync incremental: so o que mudou desde o ultimo, com recuo de 21 dias."""
    res = sync.sync_activities('incremental')
    invalidar_cache()
    return jsonify(res)


@app.route('/api/sync/full')
def api_sync_full():
    """Sync completo: puxa ANOS_HISTORICO anos. Correr uma vez no inicio."""
    res = sync.sync_activities('full')
    invalidar_cache()
    return jsonify(res)


@app.route('/health')
def health():
    return jsonify({'status': 'healthy'}), 200


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
