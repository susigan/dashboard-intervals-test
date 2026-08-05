"""
app.py — Script CORRIGIDO para testar API Intervals.icu
Baseado na análise dos forums
"""
import requests
import os
import json

def main():
    """Função principal"""
    
    print("\n" + "="*70)
    print("🏃 INTERVALS.ICU API TEST — VERSÃO CORRIGIDA")
    print("="*70 + "\n")
    
    # Carregar variáveis de ambiente
    API_KEY = os.getenv("INTERVALS_ICU_API_KEY", "").strip()
    ATHLETE_ID = os.getenv("ATHLETE_ID", "me").strip()
    
    # Verificar API key
    if not API_KEY:
        print("❌ ERRO: INTERVALS_ICU_API_KEY não está configurada!")
        print("   Railway → Variables → INTERVALS_ICU_API_KEY")
        print("   Ou ficheiro .env: INTERVALS_ICU_API_KEY=sua_chave_aqui\n")
        return
    
    print(f"✅ API Key: {API_KEY[:20]}...{API_KEY[-10:]}")
    print(f"✅ Athlete ID: {ATHLETE_ID}")
    print(f"✅ Tamanho da chave: {len(API_KEY)} caracteres\n")
    
    # ──────────────────────────────────────────────────────────────
    # HEADERS CORRECTOS (baseado nos forums)
    # ──────────────────────────────────────────────────────────────
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    print("HEADERS SENDO USADO:")
    print(f"  Authorization: Bearer {API_KEY[:20]}...")
    print(f"  Content-Type: application/json\n")
    
    # ──────────────────────────────────────────────────────────────
    # TESTE 1: Verificar autenticação (Perfil do Atleta)
    # ──────────────────────────────────────────────────────────────
    
    print("="*70)
    print("1️⃣  TESTANDO AUTENTICAÇÃO (GET /athlete/{id})")
    print("="*70)
    
    try:
        url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}"
        print(f"URL: {url}\n")
        
        response = requests.get(url, headers=headers, timeout=10)
        
        print(f"Status: {response.status_code}")
        print(f"Reason: {response.reason}\n")
        
        if response.status_code == 200:
            profile = response.json()
            print("✅ AUTENTICAÇÃO BEM-SUCEDIDA!\n")
            print(f"Nome: {profile.get('name', 'N/A')}")
            print(f"ID: {profile.get('id', 'N/A')}")
            print(f"Peso: {profile.get('weight', 'N/A')}")
            print(f"Email: {profile.get('email', 'N/A')}\n")
        else:
            print(f"❌ ERRO {response.status_code}: {response.reason}")
            print(f"Resposta: {response.text}\n")
            return
    
    except Exception as e:
        print(f"❌ ERRO DE CONEXÃO: {e}\n")
        return
    
    # ──────────────────────────────────────────────────────────────
    # TESTE 2: Verificar conexões com devices/platforms (novo endpoint)
    # ──────────────────────────────────────────────────────────────
    
    print("="*70)
    print("2️⃣  TESTANDO CONEXÕES COM DEVICES (GET /athlete/{id}/connections)")
    print("="*70)
    
    try:
        url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/connections"
        print(f"URL: {url}\n")
        
        response = requests.get(url, headers=headers, timeout=10)
        
        print(f"Status: {response.status_code}\n")
        
        if response.status_code == 200:
            connections = response.json()
            print("✅ CONEXÕES ENCONTRADAS:\n")
            
            for device, connected in connections.items():
                if device != 'id':
                    status = "✅ Conectado" if connected else "❌ Não conectado"
                    print(f"  {device}: {status}")
            print()
        else:
            print(f"⚠️  AVISO {response.status_code}: {response.text}\n")
    
    except Exception as e:
        print(f"⚠️  AVISO: {e}\n")
    
    # ──────────────────────────────────────────────────────────────
    # TESTE 3: Buscar atividades
    # ──────────────────────────────────────────────────────────────
    
    print("="*70)
    print("3️⃣  BUSCANDO ATIVIDADES (GET /athlete/{id}/activities)")
    print("="*70)
    
    try:
        url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities"
        params = {"page": 1, "limit": 10}
        
        print(f"URL: {url}")
        print(f"Params: {params}\n")
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        print(f"Status: {response.status_code}\n")
        
        if response.status_code == 200:
            data = response.json()
            activities = data.get("data", [])
            
            print(f"✅ ENCONTRADAS {len(activities)} ATIVIDADES:\n")
            
            if activities:
                for i, activity in enumerate(activities[:5], 1):
                    print(f"{i}. {activity.get('start_date_local', 'N/A')} | "
                          f"{activity.get('type', 'N/A').upper():6} | "
                          f"{activity.get('distance', 0):6.1f}km | "
                          f"RPE: {activity.get('rpe', 'N/A')}")
                print()
                
                # ──────────────────────────────────────────────────────────────
                # TESTE 4: Pegar detalhes de 1 atividade
                # ──────────────────────────────────────────────────────────────
                
                first_activity_id = activities[0].get('id')
                
                print("="*70)
                print(f"4️⃣  DETALHES DA ATIVIDADE {first_activity_id}")
                print("="*70)
                
                url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities/{first_activity_id}"
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
                    
                    # Streams
                    print("="*70)
                    print("5️⃣  STREAMS (Série Temporal)")
                    print("="*70)
                    
                    stream_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities/{first_activity_id}/streams"
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
                        if stream_response.status_code == 403:
                            print("  (Pode ser restrição de Strava ou token propagation issue)")
                        print(f"  Resposta: {stream_response.text}\n")
                    
                    # JSON Completo
                    print("="*70)
                    print("📋 DADOS COMPLETOS (JSON)")
                    print("="*70)
                    print(json.dumps(activity, indent=2, ensure_ascii=False))
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
📌 NOTAS IMPORTANTES (baseado nos forums):

1. Bearer Token Format:
   Authorization: Bearer YOUR_API_KEY

2. Para acesso próprio:
   ✅ Usa API key simples (o que estás a usar)
   ❌ NÃO precisa OAuth

3. Scopes (se usasses OAuth, o que NÃO é o caso):
   ACTIVITY:READ, WELLNESS:WRITE, CALENDAR:READ, etc.

4. Athlete ID:
   ✅ Usa "me" para tua própria conta
   ✅ Ou usa o número do atleta

5. Se receberes 403 em streams:
   ⚠️  Pode ser token propagation issue
   ✅ Solução: Criar nova API key em Intervals.icu

6. Se receberes 401:
   ❌ API key inválida, expirada ou com espaços
   ✅ Verifica em: https://intervals.icu/settings/api

""")

if __name__ == "__main__":
    main()
