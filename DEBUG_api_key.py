"""
debug_api_key.py — Script para debugar erro 401
Testa diferentes formas de autenticação
"""
import requests
import os

# API key (edita aqui ou adiciona .env)
API_KEY = os.getenv("INTERVALS_ICU_API_KEY", "")

if not API_KEY:
    print("❌ ERRO: INTERVALS_ICU_API_KEY não está configurada!")
    print("Adiciona a API key num ficheiro .env ou variável de ambiente")
    exit(1)

print("\n" + "="*60)
print("🔍 DEBUG API KEY INTERVALS.ICU")
print("="*60 + "\n")

print(f"API Key: {API_KEY[:20]}...{API_KEY[-10:]}")
print(f"Tamanho: {len(API_KEY)} caracteres\n")

# ──────────────────────────────────────────────────────────────
# TESTE 1: Bearer Token (formato padrão)
# ──────────────────────────────────────────────────────────────

print("TESTE 1️⃣  — Bearer Token (padrão)")
print("-" * 60)

headers1 = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

try:
    response = requests.get(
        "https://intervals.icu/api/v1/athlete/me",
        headers=headers1,
        timeout=10
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        print("✅ SUCESSO COM BEARER TOKEN!")
        print(f"Resposta: {response.json()}\n")
    else:
        print(f"❌ ERRO: {response.text}\n")
except Exception as e:
    print(f"❌ ERRO: {e}\n")

# ──────────────────────────────────────────────────────────────
# TESTE 2: API Key em Header customizado
# ──────────────────────────────────────────────────────────────

print("TESTE 2️⃣  — Custom Header (X-API-Key)")
print("-" * 60)

headers2 = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

try:
    response = requests.get(
        "https://intervals.icu/api/v1/athlete/me",
        headers=headers2,
        timeout=10
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        print("✅ SUCESSO COM X-API-KEY!")
        print(f"Resposta: {response.json()}\n")
    else:
        print(f"❌ ERRO: {response.text}\n")
except Exception as e:
    print(f"❌ ERRO: {e}\n")

# ──────────────────────────────────────────────────────────────
# TESTE 3: Verificar se a chave tem espaços/caracteres estranhos
# ──────────────────────────────────────────────────────────────

print("TESTE 3️⃣  — Verificar formato da chave")
print("-" * 60)

print(f"Primeiro caractere: '{API_KEY[0]}'")
print(f"Último caractere: '{API_KEY[-1]}'")
print(f"Contém espaços: {'SIM ❌' if ' ' in API_KEY else 'NÃO ✅'}")
print(f"Contém quebras de linha: {'SIM ❌' if '\\n' in API_KEY else 'NÃO ✅'}")
print()

# ──────────────────────────────────────────────────────────────
# TESTE 4: Verificar se é apenas a chave (sem "Bearer " prefixo)
# ──────────────────────────────────────────────────────────────

print("TESTE 4️⃣  — Remover prefixo se existir")
print("-" * 60)

api_key_clean = API_KEY.replace("Bearer ", "").strip()

if api_key_clean != API_KEY:
    print(f"⚠️  Chave tinha prefixo 'Bearer', removido")
    print(f"Chave original: {API_KEY[:30]}...")
    print(f"Chave limpa: {api_key_clean[:30]}...\n")
    
    headers_clean = {
        "Authorization": f"Bearer {api_key_clean}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(
            "https://intervals.icu/api/v1/athlete/me",
            headers=headers_clean,
            timeout=10
        )
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            print("✅ SUCESSO COM CHAVE LIMPA!")
            print(f"Resposta: {response.json()}\n")
        else:
            print(f"❌ ERRO: {response.text}\n")
    except Exception as e:
        print(f"❌ ERRO: {e}\n")
else:
    print("ℹ️  Chave parece estar limpa (sem prefixos)\n")

# ──────────────────────────────────────────────────────────────
# TESTE 5: Verificar documentação da API
# ──────────────────────────────────────────────────────────────

print("TESTE 5️⃣  — Informações importantes")
print("-" * 60)
print("""
✓ Documentação: https://intervals.icu/api-docs.html
✓ Settings: https://intervals.icu/settings/api
✓ Forum: https://forum.intervals.icu

Se ainda dá 401:
1. Verifica se a API key está ativa em Intervals.icu
2. Verifica se tem permissões correctas
3. Tenta criar uma NOVA API key
4. Se não funcionar, contacta suporte Intervals.icu
""")

print("="*60)
print("FIM DO DEBUG")
print("="*60 + "\n")
