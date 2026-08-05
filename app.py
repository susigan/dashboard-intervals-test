#!/usr/bin/env python3
"""
🚀 API Intervals.icu — Version 3 (Logging Otimizado)
=====================================================

Sem logging excessivo que causa Railway rate limit.
Apenas 10-15 linhas de output crítico.

Mudanças:
  - Remove loop field-by-field
  - Adiciona sample processing (primeira atividade processada)
  - Output é estruturado, não linha por linha
"""

import os
import sys
import json
import logging
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from dotenv import load_dotenv

load_dotenv()

# ================== CONFIG ==================

API_KEY = os.getenv("INTERVALS_ICU_API_KEY", "").strip()
ATHLETE_ID = os.getenv("ATHLETE_ID", "0").strip()

if not API_KEY:
    print("❌ INTERVALS_ICU_API_KEY não configurada. Saindo.")
    sys.exit(1)

print(f"✅ API_KEY carregada ({len(API_KEY)} chars)")
print(f"✅ ATHLETE_ID: {ATHLETE_ID}")

# ================== LOGGING ==================

# Suprimir logs verbose do urllib3
logging.getLogger("urllib3").setLevel(logging.WARNING)

# ================== FUNCTIONS ==================

def fetch_activities(days_back: int = 365) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch activities from Intervals.icu API.
    
    Returns:
        Lista de atividades ou None se erro.
    """
    oldest_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    url = f"https://API_KEY:{API_KEY}@intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities"
    
    print(f"\n🔍 Fetching activities...")
    print(f"   URL: https://API_KEY:***@intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities")
    print(f"   Params: oldest={oldest_date}")
    
    try:
        response = requests.get(url, params={"oldest": oldest_date}, timeout=15)
        response.raise_for_status()
        
        result = response.json()
        
        # Verificar tipo de resposta
        if isinstance(result, list):
            activities = result
        elif isinstance(result, dict) and "data" in result:
            activities = result["data"]
        else:
            print(f"❌ Resposta inesperada: {type(result).__name__}")
            return None
        
        return activities
    
    except requests.exceptions.Timeout:
        print("❌ Timeout: Servidor demorou demais")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP Error: {e.response.status_code} — {e.response.text[:200]}")
        return None
    except json.JSONDecodeError:
        print(f"❌ JSON decode error")
        return None
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        return None


def process_activity(activity: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrai campos críticos de uma atividade.
    Tratamento seguro de None.
    """
    return {
        'id': activity.get('id', ''),
        'date': activity.get('start_date_local', '')[:10],
        'name': activity.get('name', 'Unknown'),
        'type': activity.get('type', 'Unknown'),
        
        'duration_sec': activity.get('elapsed_time') or activity.get('moving_time') or 0,
        'distance_km': activity.get('icu_distance') or activity.get('distance') or 0.0,
        
        'ftp': activity.get('icu_pm_ftp') or activity.get('icu_ftp') or 0,
        'avg_watts': activity.get('icu_weighted_avg_watts') or activity.get('icu_average_watts') or 0,
        'joules': activity.get('icu_joules') or 0,
        'training_load': activity.get('icu_training_load') or 0,
        
        'avg_hr': activity.get('average_heartrate') or 0,
        'max_hr': activity.get('max_heartrate') or 0,
        'has_hr': activity.get('has_heartrate', False),
        'ignore_hr': activity.get('icu_ignore_hr', False),
        
        'source': activity.get('source', 'UNKNOWN'),
        'trainer': activity.get('trainer', False),
    }


def analyze_sample(activities: List[Dict[str, Any]]) -> None:
    """
    Analisa e printa detalhes da primeira atividade.
    (SAMPLE apenas, sem loop)
    """
    if not activities:
        print("⚠️  Nenhuma atividade encontrada")
        return
    
    first = activities[0]
    processed = process_activity(first)
    
    print("\n" + "="*60)
    print("📊 AMOSTRA: Primeira Atividade")
    print("="*60)
    print(json.dumps(processed, indent=2))
    print("="*60)
    
    # Verificar campos que podem ser None
    none_fields = [k for k in first.keys() if first[k] is None]
    if none_fields:
        print(f"\n⚠️  Campos NULL: {', '.join(none_fields[:5])}...")
    else:
        print("\n✅ Nenhum campo NULL na primeira atividade")


def validate_activities(activities: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Validação rápida da lista de atividades.
    """
    if not activities:
        return {'valid': False, 'reason': 'Lista vazia'}
    
    # Verificar campos obrigatórios
    required_fields = ['id', 'start_date_local', 'type', 'icu_training_load']
    missing = []
    
    for field in required_fields:
        if field not in activities[0]:
            missing.append(field)
    
    if missing:
        return {
            'valid': False,
            'reason': f"Campos faltando: {', '.join(missing)}"
        }
    
    # Contagem de atividades com dados críticos
    with_power = sum(1 for a in activities if a.get('icu_pm_ftp'))
    with_hr = sum(1 for a in activities if a.get('average_heartrate'))
    
    return {
        'valid': True,
        'total': len(activities),
        'with_power': with_power,
        'with_hr': with_hr,
        'power_coverage': f"{with_power/len(activities)*100:.1f}%",
        'hr_coverage': f"{with_hr/len(activities)*100:.1f}%",
    }


# ================== MAIN ==================

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🚀 INTERVALS.ICU API — Debug (Logging Otimizado)")
    print("="*60)
    
    # 1. Fetch
    activities = fetch_activities(days_back=365)
    if not activities:
        print("❌ Falha ao fetch activities")
        sys.exit(1)
    
    # 2. Validar
    print(f"\n✅ Status: 200 OK")
    print(f"✅ Tipo de resposta: {type(activities).__name__}")
    print(f"✅ Total atividades: {len(activities)}")
    
    validation = validate_activities(activities)
    print(f"\n📋 Validação:")
    for k, v in validation.items():
        print(f"   {k}: {v}")
    
    # 3. Analisar sample
    analyze_sample(activities)
    
    # 4. Estatísticas rápidas
    print("\n📈 Estatísticas Rápidas:")
    durations = [a.get('elapsed_time', 0) for a in activities if a.get('elapsed_time')]
    distances = [a.get('icu_distance', 0) for a in activities if a.get('icu_distance')]
    tl_values = [a.get('icu_training_load', 0) for a in activities if a.get('icu_training_load')]
    
    if durations:
        avg_duration = sum(durations) / len(durations) / 60
        print(f"   Duração média: {avg_duration:.1f} min")
    
    if distances:
        avg_distance = sum(distances) / len(distances)
        print(f"   Distância média: {avg_distance:.1f} km")
    
    if tl_values:
        avg_tl = sum(tl_values) / len(tl_values)
        print(f"   TL médio: {avg_tl:.1f}")
    
    print("\n✅ OK — Sem erros de parsing ou NoneType")
    print("="*60)
