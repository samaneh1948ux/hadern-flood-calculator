import streamlit as st
import requests
import datetime
import folium
import os
import subprocess
import sys
import json
import streamlit.components.v1 as components

# PATHS
BASE_DIR = r"E:\Esri\Online_Data"
TEMP_DIR = os.path.join(BASE_DIR, "Streamlit_Temp")
os.makedirs(TEMP_DIR, exist_ok=True)
GEOJSON_PATH = os.path.join(TEMP_DIR, "live_runoff.geojson")
WORKER_SCRIPT = os.path.join(BASE_DIR, "arcpy_worker.py")

# UI SETUP
st.set_page_config(page_title="Hadern Flood Watch", layout="wide")
st.title("🌊 Hadern Live Runoff Calculator")

target_date = st.date_input("Select Analysis Date", datetime.date(2026, 1, 2))

@st.cache_data
def get_weather(date):
    url = f"https://archive-api.open-meteo.com/v1/archive?latitude=48.11&longitude=11.48&start_date={date}&end_date={date}&hourly=precipitation"
    data = requests.get(url).json()
    return data['hourly']['precipitation']

rain_data = get_weather(target_date)
max_rain = max(rain_data)

col1, col2 = st.columns(2)
col1.metric(label="Peak Hourly Rain", value=f"{max_rain} mm")

if max_rain == 0:
    col2.success("No rain detected. Map is safe.")
else:
    col2.warning(f"Rain detected! Click the button below to run the heavy ArcGIS math.")

# THE TRIGGER
if st.button("🚀 Calculate Live Spatial Runoff"):
    if max_rain > 0:
        with st.spinner("Waking up ArcGIS in the background... Please wait 15 seconds..."):
            try:
                result = subprocess.run(
                    [sys.executable, WORKER_SCRIPT, str(max_rain)],
                    capture_output=True, text=True, check=True
                )
                st.success("✅ Calculation complete! Map updated.")
            except subprocess.CalledProcessError as e:
                st.error(f"🚨 BACKGROUND ARCPY FAILED:\n{e.stderr}")
    elif max_rain == 0:
        if os.path.exists(GEOJSON_PATH):
            os.remove(GEOJSON_PATH)

# ==========================================
# DRAW THE DYNAMIC MAP
# ==========================================
st.markdown("### 🗺️ Live Runoff Map (Hadern)")

m = folium.Map(location=[48.11, 11.48], zoom_start=15, tiles='CartoDB positron')

def get_water_color(value):
    if value == 10: return '#6baed6'      
    elif value == 50: return '#4292c6'    
    elif value == 250: return '#2171b5'   
    elif value == 1000: return '#08519c'  
    elif value == 5000: return '#08306b'  
    else: return '#ffffff'

# Check if the map actually has polygons inside it
has_flood_features = False
if max_rain > 0 and os.path.exists(GEOJSON_PATH):
    try:
        with open(GEOJSON_PATH, 'r') as f:
            geo_data = json.load(f)
            if len(geo_data.get('features', [])) > 0:
                has_flood_features = True
    except Exception:
        pass

# Draw the flood shapes ONLY if water actually exists
if has_flood_features:
    folium.GeoJson(
        GEOJSON_PATH,
        name="Runoff",
        style_function=lambda feature: {
            'fillColor': get_water_color(feature['properties'].get('gridcode', 0)),
            'color': 'none',   # <-- THE FIX: No more jagged outlines
            'weight': 0,       # <-- THE FIX: Zero thickness on borders
            'fillOpacity': 0.8
        },
        tooltip=folium.GeoJsonTooltip(
            fields=['gridcode'], 
            aliases=['Runoff Class (Upper Limit):'], 
            localize=True
        )
    ).add_to(m)
else:
    # Draw the safe marker
    folium.Marker(
        [48.11, 11.48], 
        popup="Safe/Dry (Ground absorbed the rain)", 
        icon=folium.Icon(color="green", icon="info-sign")
    ).add_to(m)

# Native HTML Renderer
components.html(m._repr_html_(), height=600)