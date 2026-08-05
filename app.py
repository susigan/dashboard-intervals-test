"""
app.py — App principal Streamlit
Test Intervals.icu API Integration
"""
import streamlit as st
import pandas as pd
from utils.intervals_client import init_client

# Page config
st.set_page_config(
    page_title="Intervals.icu Test",
    page_icon="🏃",
    layout="wide"
)

st.title("🏃 Intervals.icu API Test")
st.markdown("Testing Intervals.icu API integration")

# ──────────────────────────────────────────────────────────────────
# SIDEBAR — Controlos
# ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Configurações")
    
    # Teste de autenticação
    if st.button("🔐 Testar Autenticação"):
        try:
            client = init_client()
            profile = client.get_athlete_profile()
            st.success("✅ Autenticação bem-sucedida!")
            st.write(profile)
        except Exception as e:
            st.error(f"❌ Erro: {e}")

# ──────────────────────────────────────────────────────────────────
# TAB 1 — Listar Atividades
# ──────────────────────────────────────────────────────────────────

tab1, tab2, tab3 = st.tabs(["📊 Atividades", "🔍 Detalhes", "📈 Streams"])

with tab1:
    st.header("📊 Todas as Atividades")
    
    col1, col2 = st.columns(2)
    with col1:
        years = st.slider("Anos a carregar:", 1, 5, 1)
    with col2:
        if st.button("🔄 Carregar Atividades"):
            st.session_state.load_activities = True
    
    if st.button("🔄 Carregar Atividades"):
        try:
            with st.spinner("Carregando atividades..."):
                client = init_client()
                activities = client.get_activities()
                
                if activities:
                    df = pd.DataFrame(activities)
                    st.success(f"✅ Carregadas {len(activities)} atividades")
                    
                    # Mostrar tabela
                    st.dataframe(df, use_container_width=True)
                    
                    # Stats
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("Total de Atividades", len(df))
                    with col2:
                        st.metric("Distância Total (km)", f"{df.get('distance', pd.Series()).sum():.1f}")
                    with col3:
                        st.metric("Tempo Total (h)", f"{df.get('duration', pd.Series()).sum() / 3600:.1f}")
                    with col4:
                        st.metric("RPE Médio", f"{df.get('rpe', pd.Series()).mean():.1f}")
                    
                    # Download CSV
                    csv = df.to_csv(index=False)
                    st.download_button(
                        "📥 Download CSV",
                        csv,
                        "activities.csv",
                        "text/csv"
                    )
                else:
                    st.warning("⚠️ Nenhuma atividade encontrada")
        
        except Exception as e:
            st.error(f"❌ Erro: {e}")

# ──────────────────────────────────────────────────────────────────
# TAB 2 — Detalhes de 1 Atividade
# ──────────────────────────────────────────────────────────────────

with tab2:
    st.header("🔍 Detalhes de Atividade")
    
    activity_id = st.text_input("ID da Atividade (ex: 12345678):")
    
    if st.button("📖 Carregar Detalhes"):
        if not activity_id:
            st.warning("⚠️ Insira um ID de atividade")
        else:
            try:
                with st.spinner("Carregando detalhes..."):
                    client = init_client()
                    activity = client.get_activity_details(activity_id=activity_id)
                    
                    st.success("✅ Atividade carregada")
                    
                    # Mostrar dados principais
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("Data", activity.get("start_date_local", "N/A"))
                    with col2:
                        st.metric("Tipo", activity.get("type", "N/A"))
                    with col3:
                        st.metric("Duração (min)", activity.get("duration", 0) // 60)
                    with col4:
                        st.metric("Distância (km)", f"{activity.get('distance', 0):.1f}")
                    
                    st.divider()
                    
                    # Dados de HR, Power, RPE
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.subheader("❤️ Heart Rate")
                        st.write(f"Avg: {activity.get('heart_rate_avg', 'N/A')} bpm")
                        st.write(f"Max: {activity.get('heart_rate_max', 'N/A')} bpm")
                    with col2:
                        st.subheader("⚡ Power")
                        st.write(f"Avg: {activity.get('power_avg', 'N/A')} W")
                        st.write(f"Max: {activity.get('power_max', 'N/A')} W")
                        st.write(f"Normalized: {activity.get('power_normalized', 'N/A')} W")
                    with col3:
                        st.subheader("📊 RPE")
                        st.write(f"RPE: {activity.get('rpe', 'N/A')}/10")
                        st.write(f"Elevation: {activity.get('elevation', 'N/A')} m")
                    
                    st.divider()
                    
                    # Custom Fields
                    custom_fields = activity.get("custom_fields", {})
                    if custom_fields:
                        st.subheader("📝 Custom Fields")
                        st.json(custom_fields)
                    
                    st.divider()
                    
                    # Todos os dados em JSON
                    st.subheader("📋 Dados Completos (JSON)")
                    st.json(activity)
            
            except Exception as e:
                st.error(f"❌ Erro: {e}")

# ──────────────────────────────────────────────────────────────────
# TAB 3 — Streams (série temporal)
# ──────────────────────────────────────────────────────────────────

with tab3:
    st.header("📈 Streams (Série Temporal)")
    
    activity_id_streams = st.text_input("ID da Atividade para streams:")
    
    if st.button("📊 Carregar Streams"):
        if not activity_id_streams:
            st.warning("⚠️ Insira um ID de atividade")
        else:
            try:
                with st.spinner("Carregando streams..."):
                    client = init_client()
                    streams = client.get_activity_streams(activity_id=activity_id_streams)
                    
                    st.success("✅ Streams carregados")
                    
                    # Mostrar dados disponíveis
                    if "data" in streams:
                        stream_data = streams["data"]
                        st.write(f"✅ Streams disponíveis: {len(stream_data)} tipos de dados")
                        
                        # Listar tipos de stream
                        for stream_type in stream_data:
                            st.write(f"- {stream_type}")
                    
                    st.divider()
                    
                    # Dados completos
                    st.subheader("📋 Dados Completos")
                    st.json(streams)
            
            except Exception as e:
                st.error(f"❌ Erro: {e}")

# ──────────────────────────────────────────────────────────────────
# FOOTER
# ──────────────────────────────────────────────────────────────────

st.divider()
st.info("💡 **Tip:** Guarda a API key em Railway → Variables → `INTERVALS_ICU_API_KEY`")
