import streamlit as st
import requests
import datetime
import folium
import json
import streamlit.components.v1 as components
import numpy as np
import rasterio
from rasterio import features
import geopandas as gpd
from shapely.geometry import shape, box
import os

# ==========================================
# 1. TRUE CLOUD PATHS (No E: Drive)
# ==========================================
# This tells the cloud to just look in the GitHub folder right next to the script
CN_RASTER = "Base_File/cn_zone.tif"
FLOW_ACC = "Base_File/FlowAcc_Roads.tif"
BOUNDARY_SHP = "Base_File/Boundary.shp"

# UI SETUP
st.set_page_config(page_title="Hadern Flood Watch", layout="wide")
st.title("🌊 Hadern Live Runoff Calculator (Cloud Edition)")

target_date = st.date_input("Select Analysis Date", datetime.date(2026, 1, 2))

@st.cache_data
def get_weather(date):
    url = f"https://archive-api.open-meteo.com/v1/archive?latitude=48.11&longitude=11.48&start_date={date}&end_date={date}&hourly=precipitation"
    try:
        data = requests.get(url).json()
        return data['hourly']['precipitation']
    except:
        return [0]

rain_data = get_weather(target_date)
max_rain = max(rain_data)

col1, col2 = st.columns(2)
col1.metric(label="Peak Hourly Rain", value=f"{max_rain} mm")

if max_rain == 0:
    col2.success("No rain detected. Map is safe.")
else:
    col2.warning(f"Rain detected! Click calculate to run live Numpy spatial math.")

# ==========================================
# 2. LOAD BOUNDARY & CALCULATE AGGRESSIVE ZOOM
# ==========================================
boundary_geojson = None
map_bounds = None 

try:
    if os.path.exists(BOUNDARY_SHP):
        bnd_gdf = gpd.read_file(BOUNDARY_SHP)
        bnd_gdf = bnd_gdf.to_crs("EPSG:4326")
        boundary_geojson = json.loads(bnd_gdf.to_json())
        
        tb = bnd_gdf.total_bounds
        
        # MASSIVE 30% BUFFER TO FORCE ZOOM OUT
        lon_pad = (tb[2] - tb[0]) * 0.30  
        lat_pad = (tb[3] - tb[1]) * 0.30  
        
        map_bounds = [
            [tb[1] - lat_pad, tb[0] - lon_pad], 
            [tb[3] + lat_pad, tb[2] + lon_pad]  
        ]
except Exception as e:
    st.error(f"Error loading boundary: {e}")

# ==========================================
# 3. OPEN-SOURCE SPATIAL MATH
# ==========================================
geojson_data = None
has_flood_features = False

if st.button("🚀 Calculate Live Spatial Runoff"):
    if max_rain > 0:
        with st.spinner("Crunching spatial matrices..."):
            try:
                with rasterio.open(CN_RASTER) as src_cn:
                    cn_data = src_cn.read(1).astype(np.float32)
                    transform = src_cn.transform
                    crs = src_cn.crs
                    
                with rasterio.open(FLOW_ACC) as src_flow:
                    flow_data = src_flow.read(1).astype(np.float32)

                min_rows = min(cn_data.shape[0], flow_data.shape[0])
                min_cols = min(cn_data.shape[1], flow_data.shape[1])
                cn_data = cn_data[:min_rows, :min_cols]
                flow_data = flow_data[:min_rows, :min_cols]

                cn_data = np.where(cn_data <= 0, 0.1, cn_data) 
                S = (25400.0 / cn_data) - 254.0
                Ia = 0.2 * S
                runoff_depth = np.where(max_rain > Ia, ((max_rain - Ia)**2) / ((max_rain - Ia) + S), 0)
                total_flow = runoff_depth * flow_data

                category_flow = np.zeros_like(total_flow, dtype=np.int32)
                category_flow[total_flow > 0] = 10
                category_flow[total_flow >= 50] = 50
                category_flow[total_flow >= 250] = 250
                category_flow[total_flow >= 1000] = 1000
                category_flow[total_flow >= 5000] = 5000

                mask = category_flow > 0
                shapes = features.shapes(category_flow, mask=mask, transform=transform)
                
                records = []
                for geom, val in shapes:
                    records.append({'geometry': shape(geom), 'gridcode': int(val)})

                # --- THE RESTORED CRS FIX THAT I ACCIDENTALLY DELETED ---
                if records:
                    gdf = gpd.GeoDataFrame(records, geometry='geometry')
                    if crs is not None:
                        try:
                            gdf.set_crs(crs, inplace=True, allow_override=True)
                            gdf = gdf.to_crs("EPSG:4326")
                        except Exception:
                            try:
                                gdf.set_crs(crs.to_wkt(), inplace=True, allow_override=True)
                                gdf = gdf.to_crs("EPSG:4326")
                            except Exception:
                                gdf.set_crs("EPSG:25832", inplace=True, allow_override=True)
                                gdf = gdf.to_crs("EPSG:4326")
                    else:
                        gdf.set_crs("EPSG:25832", inplace=True, allow_override=True)
                        gdf = gdf.to_crs("EPSG:4326")

                    geojson_data = json.loads(gdf.to_json())
                    has_flood_features = True
                    st.success("✅ Calculation complete!")
                else:
                    st.info("Ground absorbed the rain.")
                    
            except Exception as e:
                st.error(f"🚨 Math Engine Error: {e}")

# ==========================================
# 4. DRAW THE DYNAMIC MAP
# ==========================================
st.markdown("### 🗺️ Live Runoff Map (Hadern)")

m = folium.Map(tiles='CartoDB positron')

def get_water_color(value):
    if value == 10: return '#6baed6'      
    elif value == 50: return '#4292c6'    
    elif value == 250: return '#2171b5'   
    elif value == 1000: return '#08519c'  
    elif value >= 5000: return '#08306b'  
    else: return '#ffffff'

if has_flood_features and geojson_data:
    folium.GeoJson(
        geojson_data,
        name="Runoff",
        style_function=lambda feature: {
            'fillColor': get_water_color(feature['properties'].get('gridcode', 0)),
            'color': 'none', 
            'weight': 0,
            'fillOpacity': 0.85
        },
        tooltip=folium.GeoJsonTooltip(fields=['gridcode'], aliases=['Runoff Class:'])
    ).add_to(m)

if boundary_geojson:
    folium.GeoJson(
        boundary_geojson,
        name="Study Boundary",
        style_function=lambda x: {
            'color': '#8B0000',
            'weight': 3,
            'dashArray': '5, 5',
            'fillOpacity': 0
        }
    ).add_to(m)

if map_bounds:
    m.fit_bounds(map_bounds)

components.html(m._repr_html_(), height=600)