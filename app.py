"""
app.py — Script FINAL v2 com fix para None values
"""
import requests
import os
import json
from datetime import datetime, timedelta

def main():
    """Função principal"""
    
    print("\n" + "="*70)
    print("🏃 INTERVALS.ICU API TEST — FINAL v2")
    print("="*70 + "\n")
    
    # Carregar variáveis de ambiente
    API_KEY = os.getenv("INTERVALS_ICU_API_KEY", "").strip()
    ATHLETE_ID = os.getenv("ATHLETE_ID", "0").strip()
    
    # Verificar API key
    if not API_KEY:
        print("❌ ERRO: INTERVALS_ICU_API_KEY não está configurada!\n")
        return
    
    print(f"✅ API Key: {API_KEY[:20]}...{API_KEY[-10:]}")
    print(f"✅ Athlete ID: {ATHLETE_ID}\n")
    
    # ──────────────────────────────────────────────────────────────
    # TESTE 1: Verificar autenticação
    # ──────────────────────────────────────────────────────────────
    
    print("="*70)
    print("1️⃣  TESTANDO AUTENTICAÇÃO")
    print("="*70)
    
    try:
        # URL com Basic Auth INLINE
        url = f"https://API_KEY:{API_KEY}@intervals.icu/api/v1/athlete/{ATHLETE_ID}"
        print(f"URL: https://API_KEY:***@intervals.icu/api/v1/athlete/{ATHLETE_ID}\n")
        
        response = requests.get(url, timeout=10)
        
        print(f"Status: {response.status_code}\n")
        
        if response.status_code == 200:
            profile = response.json()
            print("✅ AUTENTICAÇÃO BEM-SUCEDIDA!\n")
            print(f"Nome: {profile.get('name', 'N/A')}")
            print(f"ID: {profile.get('id', 'N/A')}\n")
            
            actual_athlete_id = profile.get('id', ATHLETE_ID)
        else:
            print(f"❌ ERRO {response.status_code}: {response.text}\n")
            return
    
    except Exception as e:
        print(f"❌ ERRO: {e}\n")
        return
    
    # ──────────────────────────────────────────────────────────────
    # TESTE 2: Buscar atividades
    # ──────────────────────────────────────────────────────────────
    
    print("="*70)
    print("2️⃣  BUSCANDO ATIVIDADES")
    print("="*70)
    
    try:
        oldest_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        
        url = f"https://API_KEY:{API_KEY}@intervals.icu/api/v1/athlete/{actual_athlete_id}/activities"
        params = {"oldest": oldest_date}
        
        print(f"URL: https://API_KEY:***@intervals.icu/api/v1/athlete/{actual_athlete_id}/activities")
        print(f"Params: {params}\n")
        
        response = requests.get(url, params=params, timeout=10)
        
        print(f"Status: {response.status_code}\n")
        
        if response.status_code == 200:
            result = response.json()
            
            # Detectar se é lista ou dicionário
            if isinstance(result, list):
                activities = result
                print(f"📌 Nota: API retornou lista directa\n")
            else:
                activities = result.get("data", [])
            
            print(f"✅ ENCONTRADAS {len(activities)} ATIVIDADES:\n")
            
            if activities:
                # Mostrar primeiras 10
                for i, activity in enumerate(activities[:10], 1):
                    # ⚠️ IMPORTANTE: Converter None para 0!
                    distance = activity.get('distance') or 0
                    rpe = activity.get('rpe') or 'N/A'
                    
                    print(f"{i:2}. {activity.get('start_date_local', 'N/A')} | "
                          f"{activity.get('type', 'N/A').upper():12} | "
                          f"{distance:7.1f}km | "
                          f"RPE: {rpe}")
                
                print(f"\n📊 Total: {len(activities)} atividades")
                print(f"   Mais antigas: {activities[-1].get('start_date_local', 'N/A')}")
                print(f"   Mais recentes: {activities[0].get('start_date_local', 'N/A')}\n")
                
                # ──────────────────────────────────────────────────────────────
                # TESTE 3: Pegar detalhes de 1 atividade
                # ──────────────────────────────────────────────────────────────
                
                first_activity_id = activities[0].get('id')
                
                print("="*70)
                print(f"3️⃣  DETALHES DA ATIVIDADE {first_activity_id}")
                print("="*70)
                
                url = f"https://API_KEY:{API_KEY}@intervals.icu/api/v1/athlete/{actual_athlete_id}/activities/{first_activity_id}"
                print(f"URL: https://API_KEY:***@intervals.icu/api/v1/athlete/{actual_athlete_id}/activities/{first_activity_id}\n")
                
                response = requests.get(url, timeout=10)
                
                print(f"Status: {response.status_code}\n")
                
                if response.status_code == 200:
                    activity = response.json()
                    
                    print("✅ ATIVIDADE CARREGADA:\n")
                    
                    # Converter None para 0 ou valor padrão
                    duration = activity.get('duration') or 0
                    distance = activity.get('distance') or 0
                    hr_avg = activity.get('heart_rate_avg') or 0
                    hr_max = activity.get('heart_rate_max') or 0
                    power_avg = activity.get('power_avg') or 0
                    power_max = activity.get('power_max') or 0
                    rpe = activity.get('rpe') or 'N/A'
                    
                    print("📊 DADOS PRINCIPAIS:")
                    print(f"  Data: {activity.get('start_date_local', 'N/A')}")
                    print(f"  Tipo: {activity.get('type', 'N/A')}")
                    print(f"  Duração: {duration // 60} min")
                    print(f"  Distância: {distance:.1f} km")
                    print(f"  RPE: {rpe}/10\n")
                    
                    print("❤️  HEART RATE:")
                    print(f"  Média: {hr_avg} bpm")
                    print(f"  Máximo: {hr_max} bpm\n")
                    
                    print("⚡ POWER:")
                    print(f"  Média: {power_avg} W")
                    print(f"  Máximo: {power_max} W\n")
                    
                    # Custom Fields
                    custom_fields = activity.get('custom_fields', {})
                    if custom_fields:
                        print("📝 CUSTOM FIELDS:")
                        for key, value in custom_fields.items():
                            print(f"  {key}: {value}")
                        print()
                    
                    # Zones
                    zones = activity.get('zones', {})
                    if zones:
                        print("🎯 ZONAS:")
                        for zone, seconds in zones.items():
                            minutes = seconds / 60 if seconds else 0
                            print(f"  {zone}: {minutes:.0f} min")
                        print()
                    
                    # JSON resumido
                    print("="*70)
                    print("📋 DADOS JSON (primeiras 40 linhas)")
                    print("="*70)
                    json_str = json.dumps(activity, indent=2, ensure_ascii=False)
                    json_lines = json_str.split('\n')
                    for line in json_lines[:40]:
                        print(line)
                    if len(json_lines) > 40:
                        print(f"... ({len(json_lines) - 40} linhas omitidas)\n")
                
                else:
                    print(f"❌ ERRO {response.status_code}: {response.text}\n")
            
            else:
                print("⚠️  Nenhuma atividade encontrada\n")
        
        else:
            print(f"❌ ERRO {response.status_code}: {response.text}\n")
    
    except Exception as e:
        print(f"❌ ERRO: {e}\n")
        import traceback
        traceback.print_exc()
    
    print("="*70)
    print("✅ TESTE CONCLUÍDO!")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()
