"""
app.py — Script simples para testar API Intervals.icu
SEM Streamlit, apenas Python puro
"""
import requests
import os
import json
from config import INTERVALS_API_KEY, ATHLETE_ID

def main():
    """Função principal"""
    
    print("\n" + "="*60)
    print("🏃 INTERVALS.ICU API TEST")
    print("="*60 + "\n")
    
    # Verificar API key
    if not INTERVALS_API_KEY:
        print("❌ ERRO: INTERVALS_ICU_API_KEY não está configurada!")
        print("Railway → Variables → INTERVALS_ICU_API_KEY")
        return
    
    print(f"✅ API Key encontrada")
    print(f"✅ Athlete ID: {ATHLETE_ID}\n")
    
    # ──────────────────────────────────────────────────────────────
    # 1. TESTAR AUTENTICAÇÃO
    # ──────────────────────────────────────────────────────────────
    
    print("1️⃣  TESTANDO AUTENTICAÇÃO...")
    print("-" * 60)
    
    headers = {
        "Authorization": f"Bearer {INTERVALS_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(
            f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            profile = response.json()
            print("✅ AUTENTICAÇÃO BEM-SUCEDIDA!")
            print(f"   Nome: {profile.get('name', 'N/A')}")
            print(f"   ID: {profile.get('id', 'N/A')}\n")
        else:
            print(f"❌ ERRO: Status {response.status_code}")
            print(f"   {response.text}\n")
            return
    
    except Exception as e:
        print(f"❌ ERRO: {e}\n")
        return
    
    # ──────────────────────────────────────────────────────────────
    # 2. BUSCAR ATIVIDADES
    # ──────────────────────────────────────────────────────────────
    
    print("2️⃣  BUSCANDO ATIVIDADES...")
    print("-" * 60)
    
    try:
        response = requests.get(
            f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities",
            headers=headers,
            params={"page": 1, "limit": 10},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            activities = data.get("data", [])
            
            print(f"✅ ENCONTRADAS {len(activities)} ATIVIDADES")
            print()
            
            if activities:
                # Mostrar primeiras 5
                for i, activity in enumerate(activities[:5], 1):
                    print(f"{i}. {activity.get('start_date_local', 'N/A')} | "
                          f"{activity.get('type', 'N/A').upper()} | "
                          f"{activity.get('distance', 0):.1f}km | "
                          f"ID: {activity.get('id', 'N/A')}")
                
                print()
                
                # ──────────────────────────────────────────────────────────────
                # 3. PEGAR DETALHES DE 1 ATIVIDADE
                # ──────────────────────────────────────────────────────────────
                
                first_activity_id = activities[0].get('id')
                print(f"3️⃣  BUSCANDO DETALHES DA ATIVIDADE {first_activity_id}...")
                print("-" * 60)
                
                response = requests.get(
                    f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities/{first_activity_id}",
                    headers=headers,
                    timeout=10
                )
                
                if response.status_code == 200:
                    activity = response.json()
                    
                    print("✅ ATIVIDADE CARREGADA COM SUCESSO!\n")
                    
                    # Mostrar dados principais
                    print("📊 DADOS PRINCIPAIS:")
                    print(f"   Data: {activity.get('start_date_local', 'N/A')}")
                    print(f"   Tipo: {activity.get('type', 'N/A')}")
                    print(f"   Duração: {activity.get('duration', 0) // 60} min")
                    print(f"   Distância: {activity.get('distance', 0):.1f} km")
                    print(f"   RPE: {activity.get('rpe', 'N/A')}/10")
                    print()
                    
                    # Heart Rate
                    print("❤️  HEART RATE:")
                    print(f"   Média: {activity.get('heart_rate_avg', 'N/A')} bpm")
                    print(f"   Máximo: {activity.get('heart_rate_max', 'N/A')} bpm")
                    print()
                    
                    # Power
                    print("⚡ POWER:")
                    print(f"   Média: {activity.get('power_avg', 'N/A')} W")
                    print(f"   Máximo: {activity.get('power_max', 'N/A')} W")
                    print(f"   Normalizado: {activity.get('power_normalized', 'N/A')} W")
                    print()
                    
                    # Custom Fields
                    custom_fields = activity.get('custom_fields', {})
                    if custom_fields:
                        print("📝 CUSTOM FIELDS:")
                        for key, value in custom_fields.items():
                            print(f"   {key}: {value}")
                        print()
                    
                    # Todos os dados em JSON
                    print("📋 DADOS COMPLETOS (JSON):")
                    print("-" * 60)
                    print(json.dumps(activity, indent=2, ensure_ascii=False))
                    print()
                
                else:
                    print(f"❌ ERRO: Status {response.status_code}")
                    print(f"   {response.text}\n")
            
            else:
                print("⚠️  Nenhuma atividade encontrada\n")
        
        else:
            print(f"❌ ERRO: Status {response.status_code}")
            print(f"   {response.text}\n")
    
    except Exception as e:
        print(f"❌ ERRO: {e}\n")
    
    print("="*60)
    print("✅ TESTE CONCLUÍDO!")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
