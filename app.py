"""
app.py — Script FINAL com parsing correcto
"""
import requests
import os
import json
from datetime import datetime, timedelta

def main():
    """Função principal"""
    
    print("\n" + "="*70)
    print("🏃 INTERVALS.ICU API TEST — FINAL VERSION")
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
            
            # ⚠️ IMPORTANTE: A resposta pode ser lista ou dicionário!
            if isinstance(result, list):
                # Resposta é lista directa
                activities = result
                print(f"📌 Nota: API retornou lista directa (não dicionário com 'data')\n")
            else:
                # Resposta é dicionário com 'data'
                activities = result.get("data", [])
            
            print(f"✅ ENCONTRADAS {len(activities)} ATIVIDADES:\n")
            
            if activities:
                # Mostrar primeiras 10
                for i, activity in enumerate(activities[:10], 1):
                    print(f"{i:2}. {activity.get('start_date_local', 'N/A')} | "
                          f"{activity.get('type', 'N/A').upper():6} | "
                          f"{activity.get('distance', 0):7.1f}km | "
                          f"RPE: {activity.get('rpe', 'N/A')} | "
                          f"ID: {activity.get('id', 'N/A')}")
                
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
                    
                    print("📊 DADOS PRINCIPAIS:")
                    print(f"  Data: {activity.get('start_date_local', 'N/A')}")
                    print(f"  Tipo: {activity.get('type', 'N/A')}")
                    print(f"  Duração: {activity.get('duration', 0) // 60} min")
                    print(f"  Distância: {activity.get('distance', 0):.1f} km")
                    print(f"  RPE: {activity.get('rpe', 'N/A')}/10\n")
                    
                    print("❤️  HEART RATE:")
                    print(f"  Média: {activity.get('heart_rate_avg', 'N/A')} bpm")
                    print(f"  Máximo: {activity.get('heart_rate_max', 'N/A')} bpm\n")
                    
                    print("⚡ POWER:")
                    print(f"  Média: {activity.get('power_avg', 'N/A')} W")
                    print(f"  Máximo: {activity.get('power_max', 'N/A')} W\n")
                    
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
                            minutes = seconds / 60
                            print(f"  {zone}: {minutes:.0f} min")
                        print()
                    
                    # JSON resumido
                    print("="*70)
                    print("📋 DADOS JSON (primeiras 30 linhas)")
                    print("="*70)
                    json_str = json.dumps(activity, indent=2, ensure_ascii=False)
                    json_lines = json_str.split('\n')
                    for line in json_lines[:30]:
                        print(line)
                    if len(json_lines) > 30:
                        print(f"... ({len(json_lines) - 30} linhas omitidas)\n")
                
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
