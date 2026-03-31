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
import plotly.graph_objects as go

# ==========================================
# 1. AUTO-LOCATOR PATHS 
# ==========================================
def find_file(target_name):
    for root, dirs, files in os.walk("."):
        for name in files:
            if name.lower() == target_name.lower():
                return os.path.join(root, name)
    return target_name 

CN_RASTER = find_file("cn_zone.tif")
FLOW_ACC = find_file("flowacc_roads.tif")
BOUNDARY_SHP = find_file("boundary.shp")

# UI SETUP (Title & Subtitle)
st.set_page_config(page_title="Munich Hadern Runoff", layout="wide")
st.title("🌊 Munich Hadern Neighborhood Runoff Calculation")
st.markdown("*(Because nobody likes a surprise swimming pool in their basement.)* 🦆")
st.markdown("---")

@st.cache_data
def get_weather(date):
    url = f"https://archive-api.open-meteo.com/v1/archive?latitude=48.11&longitude=11.48&start_date={date}&end_date={date}&hourly=precipitation"
    try:
        data = requests.get(url).json()
        return data['hourly']['precipitation']
    except:
        return [0]

# ==========================================
# 2. LOAD BOUNDARY & CALCULATE ZOOM
# ==========================================
boundary_geojson = None
map_bounds = None 

try:
    if BOUNDARY_SHP and os.path.exists(BOUNDARY_SHP):
        bnd_gdf = gpd.read_file(BOUNDARY_SHP)
        bnd_gdf = bnd_gdf.to_crs("EPSG:4326")
        boundary_geojson = json.loads(bnd_gdf.to_json())
        
        tb = bnd_gdf.total_bounds
        lon_pad = (tb[2] - tb[0]) * 0.30  
        lat_pad = (tb[3] - tb[1]) * 0.30  
        
        map_bounds = [
            [tb[1] - lat_pad, tb[0] - lon_pad], 
            [tb[3] + lat_pad, tb[2] + lon_pad]  
        ]
except Exception as e:
    pass

# ==========================================
# 3. OPEN-SOURCE SPATIAL MATH & UI LAYOUT
# ==========================================
geojson_data = None
has_flood_features = False

def get_class_label(val):
    if val == 10: return "0 - 10 (Low Runoff)"
    elif val == 50: return "10 - 50 (Moderate Runoff)"
    elif val == 250: return "50 - 250 (High Accumulation)"
    elif val == 1000: return "250 - 1000 (Severe Accumulation)"
    elif val >= 5000: return "> 1000 (Extreme Flood Risk)"
    return "0"

col_map, col_controls = st.columns([2, 1], gap="large")

# ------------------------------------------
# RIGHT COLUMN (The 1/3 Control Panel)
# ------------------------------------------
with col_controls:
    st.markdown("### 🎛️ Control Panel")
    
    target_date = st.date_input("Select Analysis Date", datetime.date(2026, 1, 2))
    rain_data = get_weather(target_date)
    max_rain = max(rain_data)

    st.metric(label="🌧️ Peak Hourly Rain", value=f"{max_rain} mm")

    if max_rain == 0:
        st.success("☀️ Not a drop in sight! The map is safe. Go enjoy the sunshine.")
    else:
        st.warning("🌧️ Rain detected! Click below to see if you need an umbrella or a boat.")

    if st.button("🚀 Calculate Live Runoff", use_container_width=True):
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

                    max_hazard_val = float(np.max(total_flow))

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

                    if records:
                        gdf = gpd.GeoDataFrame(records, geometry='geometry')
                        gdf['Runoff_Range'] = gdf['gridcode'].apply(get_class_label)
                        
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
                        
                        if max_hazard_val < 50:
                            st.success("☔ Calculation complete! Just some minor puddles. Grab an umbrella!")
                        elif max_hazard_val < 250:
                            st.warning("🥾 Calculation complete! It's getting splashy out there. Wear your rain boots!")
                        else:
                            st.error("🛶 Calculation complete! Severe runoff detected. You might want to look into buying a canoe.")

                        st.markdown("---")
                        
                        # --- THE FIX: SMALLER GAUGE WITH MORE TOP MARGIN ---
                        fig = go.Figure(go.Indicator(
                            mode = "gauge+number",
                            value = max_hazard_val,
                            title = {'text': "Peak Hazard Detected", 'font': {'size': 16}}, # Slightly smaller font
                            gauge = {
                                'axis': {'range': [0, max(1500, max_hazard_val + 200)]},
                                'bar': {'color': "darkred"}, 
                                'steps': [
                                    {'range': [0, 10], 'color': "#e0f3fc"},      
                                    {'range': [10, 50], 'color': "#6baed6"},     
                                    {'range': [50, 250], 'color': "#4292c6"},    
                                    {'range': [250, 1000], 'color': "#2171b5"},  
                                    {'range': [1000, max(1500, max_hazard_val + 200)], 'color': "#08306b"} 
                                ]
                            }
                        ))
                        # height reduced to 180, top margin (t) increased to 50
                        fig.update_layout(height=180, margin=dict(l=20, r=20, t=50, b=10)) 
                        st.plotly_chart(fig, use_container_width=True)

                    else:
                        st.info("🌱 The ground drank it all! The soil did its job and absorbed the rain. Your sneakers are safe.")
                        
                except Exception as e:
                    st.error(f"🚨 Math Engine Error: {e}")

# ------------------------------------------
# LEFT COLUMN (The 2/3 Map Area)
# ------------------------------------------
with col_map:
    st.markdown("### 🗺️ Live Hazard Map")
    
    st.markdown("""
    <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 10px; padding: 10px; background-color: #f8f9fa; border: 1px solid #e0e0e0; border-radius: 5px;">
        <strong style="margin-right: 5px; font-family: sans-serif;">Hazard Index:</strong>
        <div style="display: flex; align-items: center; font-family: sans-serif; font-size: 13px;"><span style="background-color:#6baed6; width:14px; height:14px; display:inline-block; margin-right:4px; border-radius:3px;"></span> 0-10</div>
        <div style="display: flex; align-items: center; font-family: sans-serif; font-size: 13px;"><span style="background-color:#4292c6; width:14px; height:14px; display:inline-block; margin-right:4px; border-radius:3px;"></span> 10-50</div>
        <div style="display: flex; align-items: center; font-family: sans-serif; font-size: 13px;"><span style="background-color:#2171b5; width:14px; height:14px; display:inline-block; margin-right:4px; border-radius:3px;"></span> 50-250</div>
        <div style="display: flex; align-items: center; font-family: sans-serif; font-size: 13px;"><span style="background-color:#08519c; width:14px; height:14px; display:inline-block; margin-right:4px; border-radius:3px;"></span> 250-1000</div>
        <div style="display: flex; align-items: center; font-family: sans-serif; font-size: 13px;"><span style="background-color:#08306b; width:14px; height:14px; display:inline-block; margin-right:4px; border-radius:3px;"></span> > 1000</div>
    </div>
    """, unsafe_allow_html=True)

    m = folium.Map(location=[48.11, 11.48], zoom_start=14, tiles='CartoDB positron')

    def get_water_color(value):
        if value == 10: return '#6baed6'      
        elif value == 50: return '#4292c6'    
        elif value == 250: return '#2171b5'   
        elif value == 1000: return '#08519c'  
        elif value >= 5000: return '#08306b'  
        else: return '#ffffff'

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

    if has_flood_features and geojson_data:
        hover_tooltip = folium.GeoJsonTooltip(fields=['Runoff_Range'], aliases=['Amount:'], style="font-weight: bold;")
        click_popup = folium.GeoJsonPopup(fields=['Runoff_Range'], aliases=['Amount:'], style="font-weight: bold;")

        folium.GeoJson(
            geojson_data,
            name="Runoff",
            style_function=lambda feature: {
                'fillColor': get_water_color(feature['properties'].get('gridcode', 0)),
                'color': 'none', 
                'weight': 0,     
                'fillOpacity': 0.85
            },
            tooltip=hover_tooltip,
            popup=click_popup
        ).add_to(m)

    if map_bounds:
        m.fit_bounds(map_bounds)

    components.html(m._repr_html_(), height=700)