"""
config.py — Configurações globais
"""
import os
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv()

# API Intervals.icu
INTERVALS_API_KEY = os.getenv("INTERVALS_ICU_API_KEY")
INTERVALS_BASE_URL = "https://intervals.icu/api/v1"

# Athlete ID (exemplo)
ATHLETE_ID = os.getenv("ATHLETE_ID", "me")

# Cache TTL (segundos)
CACHE_TTL = 3600  # 1 hora

# Debug
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
