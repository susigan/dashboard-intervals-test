"""
app.py — Script com FIX para erro 422 (parâmetro 'oldest' faltando)
"""
import requests
import os
import json
import base64
from datetime import datetime, timedelta

def main():
    """Função principal"""
    
    print("\n" + "="*70)
    print("🏃 INTERVALS.ICU API TEST — BASIC AUTH + FIX 422")
    print("="*70 + "\n")
    
    # Carregar variáveis de ambiente
    API_KEY = os.getenv("INTERVALS_ICU_API_KEY", "").strip()
    ATHLETE_ID = os.getenv("ATHLETE_ID", "0").strip()
    
    # Verificar API key
    if not API_KEY:
        print("❌ ERRO: INTERVALS_ICU_API_KEY não está configurada!\n")
        return
    
    print(f"✅ API Key: {API_KEY[:20]}...{API_KEY[-10:]}")
    print(f"✅ Athlete ID: {ATHLETE_ID}")
    print(f"✅ Tamanho da chave: {len(API_KEY)} caracteres\n")
    
    # ──────────────────────────────────────────────────────────────
    # CRIAR BASIC AUTH HEADER
    # ──────────────────────────────────────────────────────────────
    
    credentials = f"API_KEY:{API_KEY}"
    credentials_base64 = base64.b64encode(credentials.encode()).decode()
    
    headers = {
        "Authorization": f"Basic {credentials_base64}",
        "Content-Type": "application/json"
    }
    
    print("AUTENTICAÇÃO: Basic Authentication")
    print(f"  Username: API_KEY (literal)")
    print(f"  Password: {API_KEY[:20]}...\n")
    
    # ──────────────────────────────────────────────────────────────
    # TESTE 1: Verificar autenticação
    # ──────────────────────────────────────────────────────────────
    
    print("="*70)
    print("1️⃣  TESTANDO AUTENTICAÇÃO")
    print("="*70)
    
    try:
        url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}"
        print(f"URL: {url}\n")
        
        response = requests.get(url, headers=headers, timeout=10)
        
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
    # TESTE 2: Buscar atividades COM parâmetro 'oldest'
    # ──────────────────────────────────────────────────────────────
    
    print("="*70)
    print("2️⃣  BUSCANDO ATIVIDADES (com parâmetro 'oldest')")
    print("="*70)
    
    try:
        # Calcular data 'oldest' = 1 ano atrás
        oldest_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        
        url = f"https://intervals.icu/api/v1/athlete/{actual_athlete_id}/activities"
        params = {"oldest": oldest_date}
        
        print(f"URL: {url}")
        print(f"Params: {params}\n")
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        print(f"Status: {response.status_code}\n")
        
        if response.status_code == 200:
            data = response.json()
            activities = data.get("data", [])
            
            print(f"✅ ENCONTRADAS {len(activities)} ATIVIDADES:\n")
            
            if activities:
                # Mostrar primeiras 10
                for i, activity in enumerate(activities[:10], 1):
                    print(f"{i:2}. {activity.get('start_date_local', 'N/A')} | "
                          f"{activity.get('type', 'N/A').upper():6} | "
                          f"{activity.get('distance', 0):7.1f}km | "
                          f"RPE: {activity.get('rpe', 'N/A')} | "
                          f"ID: {activity.get('id', 'N/A')}")
                
                print(f"\n📊 Total de atividades: {len(activities)}")
                print(f"   Mais antigas: {activities[-1].get('start_date_local', 'N/A')}")
                print(f"   Mais recentes: {activities[0].get('start_date_local', 'N/A')}\n")
                
                # ──────────────────────────────────────────────────────────────
                # TESTE 3: Pegar detalhes de 1 atividade
                # ──────────────────────────────────────────────────────────────
                
                first_activity_id = activities[0].get('id')
                
                print("="*70)
                print(f"3️⃣  DETALHES DA ATIVIDADE {first_activity_id}")
                print("="*70)
                
                url = f"https://intervals.icu/api/v1/athlete/{actual_athlete_id}/activities/{first_activity_id}"
                print(f"URL: {url}\n")
                
                response = requests.get(url, headers=headers, timeout=10)
                
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
                    print(f"  Máximo: {activity.get('power_max', 'N/A')} W")
                    print(f"  Normalizado: {activity.get('power_normalized', 'N/A')} W\n")
                    
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
                        total_time = 0
                        for zone, seconds in zones.items():
                            minutes = seconds / 60
                            print(f"  {zone}: {minutes:.0f} min")
                            total_time += seconds
                        print()
                    
                    # Streams
                    print("="*70)
                    print("5️⃣  STREAMS (Série Temporal)")
                    print("="*70)
                    
                    stream_url = f"https://intervals.icu/api/v1/athlete/{actual_athlete_id}/activities/{first_activity_id}/streams"
                    print(f"URL: {stream_url}\n")
                    
                    stream_response = requests.get(stream_url, headers=headers, timeout=10)
                    
                    print(f"Status: {stream_response.status_code}\n")
                    
                    if stream_response.status_code == 200:
                        streams = stream_response.json()
                        print("✅ STREAMS DISPONÍVEIS:")
                        
                        if "data" in streams:
                            for stream_type in streams["data"]:
                                print(f"  - {stream_type}")
                        print()
                    else:
                        print(f"⚠️  AVISO {stream_response.status_code}")
                        print(f"  Resposta: {stream_response.text}\n")
                    
                    # JSON Completo (primeiras 50 linhas)
                    print("="*70)
                    print("📋 DADOS COMPLETOS (JSON - primeiras 50 linhas)")
                    print("="*70)
                    json_str = json.dumps(activity, indent=2, ensure_ascii=False)
                    json_lines = json_str.split('\n')
                    for line in json_lines[:50]:
                        print(line)
                    if len(json_lines) > 50:
                        print(f"... ({len(json_lines) - 50} linhas omitidas)")
                    print()
                
                else:
                    print(f"❌ ERRO {response.status_code}: {response.text}\n")
            
            else:
                print("⚠️  Nenhuma atividade encontrada\n")
        
        else:
            print(f"❌ ERRO {response.status_code}: {response.text}\n")
    
    except Exception as e:
        print(f"❌ ERRO: {e}\n")
    
    print("="*70)
    print("✅ TESTE CONCLUÍDO!")
    print("="*70 + "\n")
    
    print("""
📌 NOTAS IMPORTANTES:

1. Parâmetro 'oldest':
   ✅ Obrigatório para listar atividades
   ✅ Formato: YYYY-MM-DD
   ✅ Retorna atividades entre 'oldest' e agora

2. Basic Auth funciona! ✅
   Username: "API_KEY" (literal)
   Password: Tua API key
   
3. Próximos passos:
   ✅ Guardar atividades em DB
   ✅ Integrar com dashboard Streamlit
   ✅ Processar custom fields

""")

if __name__ == "__main__":
    main()
