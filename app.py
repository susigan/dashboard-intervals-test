"""
Script para DEBUG — Ver EXACTAMENTE o que a API retorna
"""
import requests
import os
import json
from datetime import datetime, timedelta

def main():
    API_KEY = os.getenv("INTERVALS_ICU_API_KEY", "").strip()
    ATHLETE_ID = "0"
    
    if not API_KEY:
        print("❌ ERRO: API_KEY não configurada!\n")
        return
    
    print("\n" + "="*70)
    print("🔍 DEBUG API — Ver EXACTAMENTE o que vem na resposta")
    print("="*70 + "\n")
    
    # ──────────────────────────────────────────────────────────────
    # TESTE: Buscar atividades E MOSTRAR JSON COMPLETO
    # ──────────────────────────────────────────────────────────────
    
    oldest_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    url = f"https://API_KEY:{API_KEY}@intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities"
    params = {"oldest": oldest_date}
    
    print(f"📌 URL: {url.replace(API_KEY, '***')}")
    print(f"📌 Params: {params}\n")
    
    try:
        response = requests.get(url, params=params, timeout=10)
        
        print(f"📌 Status: {response.status_code}\n")
        
        if response.status_code == 200:
            result = response.json()
            
            # ──────────────────────────────────────────────────────────────
            # MOSTRAR TIPO DA RESPOSTA
            # ──────────────────────────────────────────────────────────────
            
            print("="*70)
            print("1️⃣  TIPO DA RESPOSTA")
            print("="*70)
            print(f"Tipo: {type(result).__name__}")
            
            if isinstance(result, list):
                print(f"Comprimento: {len(result)} itens")
                print("É uma LISTA directa de atividades\n")
                activities = result
            elif isinstance(result, dict):
                print(f"Chaves: {list(result.keys())}")
                if "data" in result:
                    activities = result["data"]
                    print(f"Usando result['data'] = {len(activities)} atividades\n")
                else:
                    print("⚠️ Não há chave 'data'!\n")
                    activities = []
            else:
                print(f"❌ Tipo inesperado: {type(result)}\n")
                return
            
            # ──────────────────────────────────────────────────────────────
            # MOSTRAR 1ª ATIVIDADE COMPLETA (JSON)
            # ──────────────────────────────────────────────────────────────
            
            if activities:
                print("="*70)
                print("2️⃣  PRIMEIRA ATIVIDADE (JSON COMPLETO)")
                print("="*70)
                first_activity = activities[0]
                print(json.dumps(first_activity, indent=2, ensure_ascii=False))
                
                # ──────────────────────────────────────────────────────────────
                # MOSTRAR TIPOS DE CADA CAMPO
                # ──────────────────────────────────────────────────────────────
                
                print("\n" + "="*70)
                print("3️⃣  TIPOS DE DADOS DE CADA CAMPO")
                print("="*70)
                
                for key, value in first_activity.items():
                    value_type = type(value).__name__
                    value_repr = str(value)[:50] if value is not None else "None"
                    print(f"  {key:25} → {value_type:15} = {value_repr}")
                
                # ──────────────────────────────────────────────────────────────
                # TESTAR CONVERSÕES
                # ──────────────────────────────────────────────────────────────
                
                print("\n" + "="*70)
                print("4️⃣  TESTAR CONVERSÕES")
                print("="*70)
                
                distance = first_activity.get('distance')
                duration = first_activity.get('duration')
                
                print(f"\ndistance = {repr(distance)}")
                print(f"  type: {type(distance).__name__}")
                print(f"  distance or 0 = {distance or 0}")
                print(f"  Formatting: {(distance or 0):7.1f}km")
                
                print(f"\nduration = {repr(duration)}")
                print(f"  type: {type(duration).__name__}")
                print(f"  duration or 0 = {duration or 0}")
                print(f"  Formatting: {(duration or 0) // 60} min")
                
            else:
                print("⚠️ Nenhuma atividade encontrada!\n")
        
        else:
            print(f"❌ ERRO {response.status_code}: {response.text}\n")
    
    except Exception as e:
        print(f"❌ ERRO: {e}\n")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
