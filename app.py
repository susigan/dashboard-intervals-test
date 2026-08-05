#!/usr/bin/env python3
"""
🚀 Intervals.icu API Proxy — Flask Server
===========================================
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from flask import Flask, jsonify, request
from dotenv import load_dotenv

# Carregar env
load_dotenv()

# Config
API_KEY = os.getenv("INTERVALS_ICU_API_KEY", "").strip()
ATHLETE_ID = os.getenv("ATHLETE_ID", "0").strip()

if not API_KEY:
    print("❌ INTERVALS_ICU_API_KEY não configurada")
    sys.exit(1)

print(f"✅ Config carregada")
print(f"   API_KEY: {API_KEY[:5]}...")
print(f"   ATHLETE_ID: {ATHLETE_ID}")

# Import helpers
try:
    from helpers import ActivityProcessor
    print("✅ ActivityProcessor importado com sucesso")
except Exception as e:
    print(f"❌ ERRO ao importar ActivityProcessor: {e}")
    print(f"   Verifica se helpers.py está na pasta main")
    sys.exit(1)

# Flask
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# Cache
_cache = {'activities': None, 'time': None}


def fetch_activities():
    """Fetch e cache por 5 min."""
    import requests
    
    now = datetime.now()
    if _cache['activities'] and _cache['time']:
        elapsed = (now - _cache['time']).total_seconds()
        if elapsed < 300:
            return _cache['activities']
    
    oldest = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    url = f"https://API_KEY:{API_KEY}@intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities"
    
    try:
        resp = requests.get(url, params={"oldest": oldest}, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        
        if isinstance(result, list):
            acts = result
        elif isinstance(result, dict) and "data" in result:
            acts = result["data"]
        else:
            return None
        
        _cache['activities'] = acts
        _cache['time'] = now
        return acts
    except Exception as e:
        print(f"❌ Fetch error: {e}")
        return None


# ==================== ROUTES ====================

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'status': 'OK',
        'service': 'Intervals.icu API Proxy',
        'endpoints': {
            'GET /api/activities': 'All activities',
            'GET /api/activities/<id>': 'Single activity',
            'GET /api/stats': 'Statistics',
        }
    })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200


@app.route('/api/activities', methods=['GET'])
def activities():
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'Fetch failed'}), 500
    
    processor = ActivityProcessor()
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
                'source': processor.get_source(act),
            })
        except Exception as e:
            print(f"⚠️ Error processing {act.get('id')}: {e}")
            continue
    
    # Filters
    limit = request.args.get('limit', type=int)
    offset = request.args.get('offset', default=0, type=int)
    
    if offset:
        processed = processed[offset:]
    if limit:
        processed = processed[:limit]
    
    return jsonify({
        'status': 'OK',
        'total': len(acts),
        'returned': len(processed),
        'activities': processed,
    })


@app.route('/api/activities/<activity_id>', methods=['GET'])
def activity_detail(activity_id: str):
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'Fetch failed'}), 500
    
    act = next((a for a in acts if a.get('id') == activity_id), None)
    if not act:
        return jsonify({'error': 'Not found'}), 404
    
    processor = ActivityProcessor()
    
    return jsonify({
        'status': 'OK',
        'activity': {
            'id': processor.get_activity_id(act),
            'date': processor.get_start_date_local(act),
            'name': processor.get_activity_name(act),
            'type': processor.get_activity_type(act),
            'duration_sec': processor.get_duration_seconds(act),
            'distance_km': round(processor.get_distance_km(act), 2),
            'ftp': processor.get_ftp(act),
            'avg_watts': processor.get_avg_watts(act),
            'joules': processor.get_joules(act),
            'training_load': processor.get_training_load(act),
            'avg_hr': processor.get_avg_hr(act),
            'max_hr': processor.get_max_hr(act),
            'elevation_gain': round(processor.get_elevation_gain_m(act), 0),
        }
    })


@app.route('/api/stats', methods=['GET'])
def stats():
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'Fetch failed'}), 500
    
    processor = ActivityProcessor()
    
    durations = [processor.get_duration_seconds(a) for a in acts]
    distances = [processor.get_distance_km(a) for a in acts]
    tls = [processor.get_training_load(a) for a in acts]
    hrs = [processor.get_avg_hr(a) for a in acts if processor.has_hr_data(a)]
    watts = [processor.get_avg_watts(a) for a in acts]
    joules_list = [processor.get_joules(a) for a in acts]
    
    return jsonify({
        'status': 'OK',
        'total_activities': len(acts),
        'duration_avg_min': round(sum(durations) / len(durations) / 60, 1) if durations else 0,
        'distance_total_km': round(sum(distances), 1),
        'distance_avg_km': round(sum(distances) / len(distances), 1) if distances else 0,
        'training_total_tl': sum(tls),
        'training_avg_tl': round(sum(tls) / len(tls), 1) if tls else 0,
        'training_avg_watts': round(sum(watts) / len(watts)) if watts else 0,
        'training_total_joules': sum(joules_list),
        'hr_avg': round(sum(hrs) / len(hrs)) if hrs else 0,
        'coverage_with_hr': len(hrs),
        'coverage_with_power': sum(1 for a in acts if processor.get_ftp(a) > 0),
    })


# ==================== MAIN ====================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    print(f"\n✅ Starting server on port {port}")
    print(f"   http://localhost:{port}/")
    print(f"   http://localhost:{port}/api/activities")
    print(f"   http://localhost:{port}/api/stats\n")
    app.run(host='0.0.0.0', port=port, debug=False)
