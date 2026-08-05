"""
utils/intervals_client.py — Cliente da API Intervals.icu
"""
import requests
import pandas as pd
import streamlit as st
from config import INTERVALS_API_KEY, INTERVALS_BASE_URL, ATHLETE_ID, DEBUG


class IntervalsClient:
    """Cliente para API Intervals.icu"""
    
    def __init__(self, api_key, base_url=INTERVALS_BASE_URL):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def _make_request(self, method, endpoint, params=None):
        """Faz request à API com tratamento de erros"""
        url = f"{self.base_url}/{endpoint}"
        
        try:
            if method == "GET":
                response = requests.get(url, headers=self.headers, params=params, timeout=30)
            else:
                response = requests.post(url, headers=self.headers, json=params, timeout=30)
            
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.RequestException as e:
            if DEBUG:
                print(f"❌ API Error: {e}")
            raise Exception(f"API Error: {str(e)}")
    
    @st.cache_data(ttl=3600)
    def get_activities(self, athlete_id=None, before=None, after=None, _limit=1000):
        """Pega todas as atividades"""
        if athlete_id is None:
            athlete_id = ATHLETE_ID
        
        all_activities = []
        page = 1
        
        while True:
            params = {
                "page": page,
                "limit": _limit
            }
            
            if before:
                params["before"] = before
            if after:
                params["after"] = after
            
            try:
                data = self._make_request("GET", f"athlete/{athlete_id}/activities", params=params)
                activities = data.get("data", [])
                
                if not activities:
                    break
                
                all_activities.extend(activities)
                page += 1
                
                if DEBUG:
                    print(f"✅ Fetched page {page-1}: {len(activities)} activities")
            
            except Exception as e:
                if DEBUG:
                    print(f"❌ Error fetching page {page}: {e}")
                break
        
        return all_activities
    
    @st.cache_data(ttl=3600)
    def get_activity_details(self, athlete_id=None, activity_id=None):
        """Pega detalhes de 1 atividade"""
        if athlete_id is None:
            athlete_id = ATHLETE_ID
        
        if activity_id is None:
            raise ValueError("activity_id é obrigatório")
        
        try:
            data = self._make_request("GET", f"athlete/{athlete_id}/activities/{activity_id}")
            return data
        except Exception as e:
            if DEBUG:
                print(f"❌ Error fetching activity {activity_id}: {e}")
            raise
    
    @st.cache_data(ttl=3600)
    def get_activity_streams(self, athlete_id=None, activity_id=None):
        """Pega streams (série temporal) de 1 atividade"""
        if athlete_id is None:
            athlete_id = ATHLETE_ID
        
        if activity_id is None:
            raise ValueError("activity_id é obrigatório")
        
        try:
            data = self._make_request("GET", f"athlete/{athlete_id}/activities/{activity_id}/streams")
            return data
        except Exception as e:
            if DEBUG:
                print(f"❌ Error fetching streams for {activity_id}: {e}")
            raise
    
    @st.cache_data(ttl=3600)
    def get_athlete_profile(self, athlete_id=None):
        """Pega perfil do atleta"""
        if athlete_id is None:
            athlete_id = ATHLETE_ID
        
        try:
            data = self._make_request("GET", f"athlete/{athlete_id}")
            return data
        except Exception as e:
            if DEBUG:
                print(f"❌ Error fetching athlete profile: {e}")
            raise


def init_client():
    """Inicializa o cliente da API"""
    if not INTERVALS_API_KEY:
        raise ValueError("❌ INTERVALS_ICU_API_KEY não está configurada!")
    
    return IntervalsClient(INTERVALS_API_KEY)
