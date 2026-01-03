import io
from datetime import date, datetime, time

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import meteostat as ms

st.set_page_config(page_title="Clima (Cidade/País) - Meteostat", layout="wide")
st.title("Consulta Climática por Cidade e País (Meteostat)")
st.caption("Busca por País + Cidade, período em dias, gráficos e download em CSV.")

# -----------------------------
# Geocoding (Cidade/País -> lat/lon/elevation)
# -----------------------------
@st.cache_data(show_spinner=False, ttl=60 * 60)
def geocode_city_country(city: str, country: str, max_results: int = 10, language: str = "pt"):
    """
    Usa Open-Meteo Geocoding API para obter resultados de localização.
    Documentação: endpoint /v1/search com parâmetros name, count, language, format, countryCode.
    """
    url = "https://geocoding-api.open-meteo.com/v1/search"
    query = f"{city}, {country}".strip().strip(",")
    params = {
        "name": query,
        "count": max_results,
        "format": "json",
        "language": language
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("results", []) or []

# -----------------------------
# Meteostat helpers
# -----------------------------
@st.cache_data(show_spinner=False, ttl=60 * 60)
def nearby_stations(lat: float, lon: float, elev: float):
    """
    Obtém estações próximas do ponto (lat/lon/elev). Mantemos tudo interno.
    """
    point = ms.Point(lat, lon, elev)
    stations = ms.stations.nearby(point, limit=10)  # interno; UI não expõe
    return stations.reset_index()  # traz coluna 'id'

@st.cache_data(show_spinner=False, ttl=60 * 30)
def fetch_daily(station_id: str, start_d: date, end_d: date) -> pd.DataFrame:
    ts = ms.daily(ms.Station(id=station_id), start_d, end_d)
    return ts.fetch()

@st.cache_data(show_spinner=False, ttl=60 * 30)
def fetch_hourly(station_id: str, start_d: date, end_d: date) -> pd.DataFrame:
    start_dt = datetime.combine(start_d, time(0, 0))
    end_dt = datetime.combine(end_d, time(23, 59))
    ts = ms.hourly(ms.Station(id=station_id), start_dt, end_dt)
    return ts.fetch()

def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df_reset = df.reset_index()  # inclui coluna "time"
    df_reset.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")

# -----------------------------
# UI - parâmetros solicitados
# -----------------------------
with st.sidebar:
    st.header("Parâmetros de busca")

    country = st.text_input("País", value="Australia")
    city = st.text_input("Cidade", value="Brisbane")

    period = st.date_input(
        "Período (por dias)",
        value=(date(2024, 1, 1), date(2024, 1, 31))
    )

    granularity = st.radio("Tipo de dado", ["Diário (recomendado)", "Horário"], index=0)

# Normaliza datas
if isinstance(period, tuple) and len(period) == 2:
    start_d, end_d = period
else:
    start_d, end_d = period, period

if start_d > end_d:
    st.error("A data inicial não pode ser maior que a data final.")
    st.stop()

# -----------------------------
# 1) Geocode
# -----------------------------
try:
    with st.spinner("Localizando cidade e país..."):
        places = geocode_city_country(city=city, country=country, max_results=10, language="pt")
except Exception as e:
    st.error(f"Falha ao geocodificar '{city}, {country}'. Detalhe: {e}")
    st.stop()

if not places:
    st.warning("Nenhuma localização encontrada. Verifique a grafia de Cidade/País e tente novamente.")
    st.stop()

# Se houver ambiguidade, usuário escolhe a melhor correspondência
place_labels = []
for p in places:
    admin1 = p.get("admin1", "")
    admin2 = p.get("admin2", "")
    ctry = p.get("country", "")
    label = f"{p.get('name','')} — {admin1} {admin2} ({ctry}) [lat={p.get('latitude')}, lon={p.get('longitude')}]"
    place_labels.append(label)

selected_place_label = st.selectbox("Local encontrado (selecione se houver mais de um)", place_labels, index=0)
selected_place = places[place_labels.index(selected_place_label)]

lat = float(selected_place["latitude"])
lon = float(selected_place["longitude"])
elev = float(selected_place.get("elevation") or 0.0)

with st.expander("Detalhes do local (opcional)"):
    st.json({
        "name": selected_place.get("name"),
        "country": selected_place.get("country"),
        "admin1": selected_place.get("admin1"),
        "timezone": selected_place.get("timezone"),
        "latitude": lat,
        "longitude": lon,
        "elevation": elev
    })

# -----------------------------
# 2) Estação automática (sem expor raio/limite)
# -----------------------------
try:
    with st.spinner("Selecionando automaticamente a estação meteorológica mais apropriada..."):
        st_df = nearby_stations(lat, lon, elev)
except Exception as e:
    st.error(f"Falha ao buscar estações próximas no Meteostat. Detalhe: {e}")
    st.stop()

if st_df.empty:
    st.warning("Nenhuma estação próxima foi encontrada para este local.")
    st.stop()

# Seleciona automaticamente a primeira (mais próxima)
default_station_id = st_df.loc[0, "id"]
default_station_name = st_df.loc[0, "name"] if "name" in st_df.columns else default_station_id

# Permite troca apenas se quiser (sem expor parâmetros técnicos)
with st.expander("Trocar estação (opcional)"):
    st_df["label"] = st_df.apply(
        lambda r: f"{r['id']} — {r.get('name','(sem nome)')} (distância: {int(r.get('distance', 0))} m)",
        axis=1
    )
    station_label = st.selectbox("Estação", st_df["label"].tolist(), index=0)
    station_id = station_label.split("—")[0].strip()


st.subheader("Estação selecionada")
st.write(f"**{station_id}** — {default_station_name}")

# Inventory (opcional, mas muito útil)
with st.expander("Disponibilidade de dados da estação (inventory)"):
    inv = ms.stations.inventory(station_id)
    st.write(f"Dados disponíveis de **{inv.start}** até **{inv.end}**.")
    if getattr(inv, "df", None) is not None:
        st.dataframe(inv.df, use_container_width=True)

# -----------------------------
# 3) Buscar série (por dias)
# -----------------------------
try:
    with st.spinner("Baixando dados climáticos..."):
        if granularity.startswith("Diário"):
            df = fetch_daily(station_id, start_d, end_d)
        else:
            df = fetch_hourly(station_id, start_d, end_d)
except Exception as e:
    st.error(f"Falha ao buscar série temporal. Detalhe: {e}")
    st.stop()

if df.empty:
    st.warning("A consulta retornou vazio para o período selecionado. Tente outro intervalo de datas.")
    st.stop()

st.subheader("Pré-visualização dos dados")
st.dataframe(df.head(50), use_container_width=True)

# -----------------------------
# 4) Gráficos dinâmicos + Download CSV
# -----------------------------
df_plot = df.reset_index()  # coluna time
time_col = "time"
numeric_cols = [c for c in df_plot.columns if c != time_col and pd.api.types.is_numeric_dtype(df_plot[c])]

st.subheader("Gráficos")
default_cols = [c for c in ["temp", "tmin", "tmax", "prcp", "wspd", "pres", "rhum"] if c in numeric_cols]
selected_cols = st.multiselect("Selecione variáveis", options=numeric_cols, default=default_cols or numeric_cols[:2])

if selected_cols:
    fig = px.line(df_plot, x=time_col, y=selected_cols, title="Série temporal (interativa)")
    st.plotly_chart(fig, use_container_width=True)

    # Se houver precipitação, dá para mostrar barras também
    if "prcp" in selected_cols:
        fig2 = px.bar(df_plot, x=time_col, y="prcp", title="Precipitação (prcp)")
        st.plotly_chart(fig2, use_container_width=True)
else:
    st.info("Selecione ao menos uma variável para plotar.")

st.subheader("Download")
st.download_button(
    label="Baixar CSV",
    data=df_to_csv_bytes(df),
    file_name=f"meteostat_{country}_{city}_{station_id}_{start_d}_{end_d}.csv".replace(" ", "_"),
    mime="text/csv"
)
