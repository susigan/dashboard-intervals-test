#!/usr/bin/env python3
"""
🚀 API Intervals.icu — Flask Web Server
=========================================

Servidor HTTP que serve dados da API Intervals.icu.
Integra com helpers.py (ActivityProcessor).

Endpoints:
  GET /                       → Status
  GET /api/activities         → JSON com todas as atividades processadas
  GET /api/activities/<id>    → Detalhes de 1 atividade
  GET /api/stats              → Estatísticas rápidas
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from flask import Flask, jsonify, request
from dotenv import load_dotenv

# Importar helpers
try:
    from helpers import ActivityProcessor
except ImportError:
    print("❌ Erro: helpers.py não encontrado na pasta main")
    sys.exit(1)

# Config
load_dotenv()
API_KEY = os.getenv("INTERVALS_ICU_API_KEY", "").strip()
ATHLETE_ID = os.getenv("ATHLETE_ID", "0").strip()

# Verificar config
if not API_KEY:
    print("❌ INTERVALS_ICU_API_KEY não definida")
    sys.exit(1)

# Flask app
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Suprimir logs verbose
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ================== CACHE ==================

_activities_cache = None
_cache_time = None
CACHE_TTL = 300  # 5 minutos

# ================== FUNCTIONS ==================

def fetch_and_cache_activities():
    """Fetch activities e cache por 5 min."""
    global _activities_cache, _cache_time
    
    import requests
    
    # Verificar cache
    if _activities_cache and _cache_time:
        elapsed = (datetime.now() - _cache_time).total_seconds()
        if elapsed < CACHE_TTL:
            return _activities_cache
    
    # Fetch
    oldest_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    url = f"https://API_KEY:{API_KEY}@intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities"
    
    try:
        response = requests.get(
            url,
            params={"oldest": oldest_date},
            timeout=15
        )
        response.raise_for_status()
        
        result = response.json()
        
        # Garantir que é lista
        if isinstance(result, list):
            activities = result
        elif isinstance(result, dict) and "data" in result:
            activities = result["data"]
        else:
            return None
        
        # Cache
        _activities_cache = activities
        _cache_time = datetime.now()
        
        return activities
    
    except Exception as e:
        print(f"❌ Erro ao fetch: {e}")
        return None


# ================== ROUTES ==================

@app.route('/', methods=['GET'])
def index():
    """Status page."""
    return jsonify({
        'status': 'OK',
        'service': 'Intervals.icu API Proxy',
        'endpoints': {
            'GET /': 'This page',
            'GET /api/activities': 'All activities (JSON)',
            'GET /api/activities/<id>': 'Single activity details',
            'GET /api/stats': 'Quick statistics',
            'GET /health': 'Health check',
        },
        'timestamp': datetime.now().isoformat(),
    })


@app.route('/health', methods=['GET'])
def health():
    """Health check — Railway use this."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
    }), 200


@app.route('/api/activities', methods=['GET'])
def activities():
    """
    Retorna todas as atividades processadas.
    
    Query params:
        ?limit=10       → Primeiras 10
        ?offset=5       → Saltar 5
        ?type=Ride      → Filtrar por tipo
    """
    acts = fetch_and_cache_activities()
    if not acts:
        return jsonify({
            'error': 'Falha ao fetch activities',
            'timestamp': datetime.now().isoformat(),
        }), 500
    
    processor = ActivityProcessor()
    
    # Processar todas
    processed = []
    for act in acts:
        try:
            processed.append({
                'id': processor.get_activity_id(act),
                'date': processor.get_start_date_local(act)[:10],
                'name': processor.get_activity_name(act),
                'type': processor.get_activity_type(act),
                'duration_min': round(processor.get_duration_minutes(act), 1),
                'distance_km': round(processor.get_distance_km(act), 1),
                'ftp': processor.get_ftp(act),
                'avg_watts': processor.get_avg_watts(act),
                'joules': processor.get_joules(act),
                'training_load': processor.get_training_load(act),
                'avg_hr': processor.get_avg_hr(act),
                'max_hr': processor.get_max_hr(act),
                'has_hr': processor.has_hr_data(act),
                'source': processor.get_source(act),
                'trainer': processor.is_trainer(act),
            })
        except Exception as e:
            print(f"⚠️  Erro ao processar {act.get('id')}: {e}")
            continue
    
    # Filtros query
    limit = request.args.get('limit', type=int)
    offset = request.args.get('offset', default=0, type=int)
    activity_type = request.args.get('type', type=str)
    
    if activity_type:
        processed = [a for a in processed if a['type'] == activity_type]
    
    if offset:
        processed = processed[offset:]
    
    if limit:
        processed = processed[:limit]
    
    return jsonify({
        'status': 'OK',
        'total': len(acts),
        'returned': len(processed),
        'timestamp': datetime.now().isoformat(),
        'activities': processed,
    }), 200


@app.route('/api/activities/<activity_id>', methods=['GET'])
def activity_detail(activity_id: str):
    """Detalhes de uma atividade específica."""
    acts = fetch_and_cache_activities()
    if not acts:
        return jsonify({'error': 'Falha ao fetch'}), 500
    
    # Procurar
    act = next((a for a in acts if a.get('id') == activity_id), None)
    if not act:
        return jsonify({
            'error': f'Atividade {activity_id} não encontrada',
            'timestamp': datetime.now().isoformat(),
        }), 404
    
    processor = ActivityProcessor()
    
    return jsonify({
        'status': 'OK',
        'timestamp': datetime.now().isoformat(),
        'activity': {
            'id': processor.get_activity_id(act),
            'date': processor.get_start_date_local(act),
            'name': processor.get_activity_name(act),
            'type': processor.get_activity_type(act),
            'duration_sec': processor.get_duration_seconds(act),
            'distance_km': round(processor.get_distance_km(act), 2),
            'avg_speed': round(processor.get_avg_speed_kmh(act), 2),
            'ftp': processor.get_ftp(act),
            'avg_watts': processor.get_avg_watts(act),
            'max_watts': act.get('max_speed', 0),  # Power metrics
            'joules': processor.get_joules(act),
            'training_load': processor.get_training_load(act),
            'intensity_factor': round(processor.get_intensity_factor(act), 2),
            'avg_hr': processor.get_avg_hr(act),
            'max_hr': processor.get_max_hr(act),
            'has_hr': processor.has_hr_data(act),
            'elevation_gain': round(processor.get_elevation_gain_m(act), 0),
            'source': processor.get_source(act),
            'trainer': processor.is_trainer(act),
            'commute': processor.is_commute(act),
            'race': processor.is_race(act),
        }
    }), 200


@app.route('/api/stats', methods=['GET'])
def stats():
    """Estatísticas rápidas."""
    acts = fetch_and_cache_activities()
    if not acts:
        return jsonify({'error': 'Falha ao fetch'}), 500
    
    processor = ActivityProcessor()
    
    # Calcular stats
    durations = [processor.get_duration_seconds(a) for a in acts]
    distances = [processor.get_distance_km(a) for a in acts]
    tls = [processor.get_training_load(a) for a in acts]
    hrs = [processor.get_avg_hr(a) for a in acts if processor.has_hr_data(a)]
    watts = [processor.get_avg_watts(a) for a in acts]
    joules_list = [processor.get_joules(a) for a in acts]
    
    return jsonify({
        'status': 'OK',
        'timestamp': datetime.now().isoformat(),
        'total_activities': len(acts),
        'duration': {
            'avg_minutes': round(sum(durations) / len(durations) / 60, 1) if durations else 0,
            'total_hours': round(sum(durations) / 3600, 1) if durations else 0,
        },
        'distance': {
            'avg_km': round(sum(distances) / len(distances), 1) if distances else 0,
            'total_km': round(sum(distances), 1) if distances else 0,
        },
        'training': {
            'avg_tl': round(sum(tls) / len(tls), 1) if tls else 0,
            'total_tl': sum(tls),
            'avg_watts': round(sum(watts) / len(watts), 0) if watts else 0,
            'total_joules': sum(joules_list),
        },
        'heart_rate': {
            'activities_with_hr': len(hrs),
            'avg_hr': round(sum(hrs) / len(hrs), 0) if hrs else 0,
        },
        'coverage': {
            'with_power': sum(1 for a in acts if processor.get_ftp(a) > 0),
            'with_hr': len(hrs),
            'power_pct': round(sum(1 for a in acts if processor.get_ftp(a) > 0) / len(acts) * 100, 1),
            'hr_pct': round(len(hrs) / len(acts) * 100, 1),
        }
    }), 200


# ================== ERROR HANDLING ==================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'error': 'Endpoint not found',
        'path': request.path,
        'timestamp': datetime.now().isoformat(),
    }), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        'error': 'Internal server error',
        'timestamp': datetime.now().isoformat(),
    }), 500


# ================== MAIN ==================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    print(f"\n✅ Starting Intervals.icu API Proxy")
    print(f"   Athlete ID: {ATHLETE_ID}")
    print(f"   API Key: {API_KEY[:10]}...")
    print(f"   Listening on port {port}")
    print(f"\n   http://localhost:{port}/")
    print(f"   http://localhost:{port}/api/activities")
    print(f"   http://localhost:{port}/api/stats\n")
    
    app.run(host='0.0.0.0', port=port, debug=False)
