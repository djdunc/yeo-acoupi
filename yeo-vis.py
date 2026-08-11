import os
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from influxdb_client import InfluxDBClient
from astral import LocationInfo
from astral.sun import sun
from datetime import datetime, timedelta
import warnings

warnings.simplefilter("ignore")

# --- CONFIGURATION & SECRETS ---
st.set_page_config(
    page_title="Yeo Acoupi Bioacoustic Dashboard",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded"
)

def get_secret(key):
    """Retrieve configuration secrets with multi-tier fallback."""
    if key in st.secrets:
        return st.secrets[key]
    if f"STREAMLIT_{key}" in os.environ:
        return os.environ[f"STREAMLIT_{key}"]
    if key in os.environ:
        return os.environ[key]
    raise KeyError(f"Could not find secret '{key}' in Streamlit secrets or environment variables.")

try:
    URL = get_secret("INFLUX_URL")
    TOKEN = get_secret("INFLUX_TOKEN")
    ORG = get_secret("INFLUX_ORG")
    BUCKET = get_secret("INFLUX_BUCKET")
except Exception as err:
    st.error(f"Configuration Error: {err}")
    st.stop()

# Connect to InfluxDB
@st.cache_resource
def get_influx_client(url, token, org):
    return InfluxDBClient(url=url, token=token, org=org, timeout=30000)

client = get_influx_client(URL, TOKEN, ORG)
query_api = client.query_api()

# --- SPECIES REFERENCE DATABASE ---
@st.cache_data
def load_species_database():
    """
    Loads bird and bat reference species lists from CSV files.
    Builds comprehensive lookup dictionaries by numeric species_id, class_name, and scientific name.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    bird_paths = [
        os.path.join(base_dir, "data", "yeo_valley_bird_species_list.csv"),
        os.path.join(base_dir, "yeo_valley_bird_species_list.csv"),
        "/Users/dunc/Library/CloudStorage/Dropbox/code/CASA/yeo/src/acoupi_yeo_valley/yeo_valley_bird_species_list.csv"
    ]
    bat_paths = [
        os.path.join(base_dir, "data", "yeo_valley_bat_species_list.csv"),
        os.path.join(base_dir, "yeo_valley_bat_species_list.csv"),
        "/Users/dunc/Library/CloudStorage/Dropbox/code/CASA/yeo/src/acoupi_yeo_valley/yeo_valley_bat_species_list.csv"
    ]
    
    bird_df = None
    for p in bird_paths:
        if os.path.exists(p):
            try:
                bird_df = pd.read_csv(p)
                bird_df["category"] = "Bird"
                break
            except Exception:
                pass
                
    if bird_df is None:
        try:
            url = "https://raw.githubusercontent.com/djdunc/acoupi-yeo-valley/main/src/acoupi_yeo_valley/yeo_valley_bird_species_list.csv"
            bird_df = pd.read_csv(url)
            bird_df["category"] = "Bird"
        except Exception:
            bird_df = pd.DataFrame(columns=["species_id", "class_name", "common_name", "species"])
            bird_df["category"] = "Bird"
            
    bat_df = None
    for p in bat_paths:
        if os.path.exists(p):
            try:
                bat_df = pd.read_csv(p)
                bat_df["category"] = "Bat"
                break
            except Exception:
                pass
                
    if bat_df is None:
        try:
            url = "https://raw.githubusercontent.com/djdunc/acoupi-yeo-valley/main/src/acoupi_yeo_valley/yeo_valley_bat_species_list.csv"
            bat_df = pd.read_csv(url)
            bat_df["category"] = "Bat"
        except Exception:
            bat_df = pd.DataFrame(columns=["species_id", "class_name", "common_name", "species"])
            bat_df["category"] = "Bat"
            
    combined = pd.concat([bird_df, bat_df], ignore_index=True)
    
    id_map = {}
    class_map = {}
    species_map = {}
    
    for _, row in combined.iterrows():
        sid = str(row.get("species_id", "")).strip()
        cname = str(row.get("common_name", "")).strip()
        sname = str(row.get("species", "")).strip()
        cls = str(row.get("class_name", "")).strip()
        cat = str(row.get("category", "Unknown")).strip()
        
        meta = {
            "common_name": cname,
            "species": sname,
            "class_name": cls,
            "category": cat
        }
        
        if sid:
            id_map[sid] = meta
            try:
                id_map[str(int(float(sid)))] = meta
            except (ValueError, TypeError):
                pass
                
        if cls:
            class_map[cls.lower()] = meta
        if sname:
            species_map[sname.lower()] = meta
            
    return combined, id_map, class_map, species_map

_, id_map, class_map, species_map = load_species_database()

def resolve_species(val):
    """
    Translates raw species strings or numeric IDs into common name, scientific name, and category.
    """
    if pd.isna(val) or val is None:
        return {"common_name": "Unknown", "species": "Unknown", "category": "Unknown"}
        
    val_str = str(val).strip()
    if not val_str or val_str.lower() in ("nan", "none", "null", "<na>"):
        return {"common_name": "Unknown", "species": "Unknown", "category": "Unknown"}
        
    # Check numeric ID match (e.g. '83' -> Robin, '220' -> Soprano pipistrelle)
    if val_str in id_map:
        return id_map[val_str]
    try:
        int_key = str(int(float(val_str)))
        if int_key in id_map:
            return id_map[int_key]
    except (ValueError, TypeError):
        pass
        
    val_lower = val_str.lower()
    # Check class name match (e.g. 'barbar' -> Barbastelle)
    if val_lower in class_map:
        return class_map[val_lower]
        
    # Check scientific species name match (e.g. 'pipistrellus pipistrellus' -> Common pipistrelle)
    if val_lower in species_map:
        return species_map[val_lower]
        
    # Check underscore separated class format (e.g. 'Accipiter nisus_Eurasian Sparrowhawk')
    if "_" in val_str:
        parts = val_str.split("_", 1)
        if len(parts) == 2 and parts[1].strip():
            return {
                "common_name": parts[1].strip(),
                "species": parts[0].strip(),
                "category": "Bird"
            }
            
    # Default fallback for unmapped strings / environmental sounds
    return {
        "common_name": val_str,
        "species": val_str,
        "category": "Other"
    }

# --- SIDEBAR CONTROLS ---
st.sidebar.markdown("## ⚙️ Dashboard Controls")

time_options = {
    "Last 24 Hours": "-24h",
    "Last 2 Days": "-2d",
    "Last 7 Days": "-7d",
    "Last 14 Days": "-14d",
    "Last 30 Days": "-30d"
}
selected_time_label = st.sidebar.selectbox("Time Window", options=list(time_options.keys()), index=2)
selected_time_val = time_options[selected_time_label]

confidence_threshold = st.sidebar.slider(
    "Confidence Threshold",
    min_value=0.0,
    max_value=1.0,
    value=0.60,
    step=0.05,
    help="Filter detections below this model confidence score"
)

category_filter = st.sidebar.selectbox(
    "Taxa / Category Filter",
    options=["All Species (Birds & Bats)", "Birds Only 🐦", "Bats Only 🦇", "All Detections (incl. Other)"],
    index=0
)

st.sidebar.divider()

# --- HEARTBEAT & SYSTEM TELEMETRY QUERY ---
@st.cache_data(ttl=60)
def fetch_heartbeat_data():
    hb_query = f'''
    from(bucket: "{BUCKET}")
      |> range(start: -24h)
      |> filter(fn: (r) => r["_measurement"] == "acoupi_heartbeat")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    try:
        hb_df = query_api.query_data_frame(hb_query)
        if isinstance(hb_df, list):
            hb_df = pd.concat(hb_df, ignore_index=True) if len(hb_df) > 0 else pd.DataFrame()
        return hb_df
    except Exception as e:
        return pd.DataFrame()

hb_df = fetch_heartbeat_data()

# Process Heartbeat Data
active_devices_count = 0
device_hb_summary = {}

if not hb_df.empty:
    # Resolve device identifier
    if "device_id" in hb_df.columns:
        hb_df["Device"] = hb_df["device_id"].fillna(hb_df.get("topic", "unknown"))
    else:
        hb_df["Device"] = hb_df.get("topic", "unknown")
    hb_df["Device"] = hb_df["Device"].astype(str).apply(lambda d: d.split("/")[1] if "/" in d else d)
    
    if "_time" in hb_df.columns:
        hb_df["_time"] = pd.to_datetime(hb_df["_time"])
        latest_hb = hb_df.sort_values("_time", ascending=False).groupby("Device").first().reset_index()
        now_utc = pd.Timestamp.now(tz="UTC")
        
        st.sidebar.markdown("### 📡 Device Status (24h)")
        for _, dev_row in latest_hb.iterrows():
            dev_name = dev_row["Device"]
            last_seen = dev_row["_time"]
            diff_hours = (now_utc - last_seen).total_seconds() / 3600.0
            
            if diff_hours <= 2:
                status_icon = "🟢"
                status_text = "Online"
            elif diff_hours <= 12:
                status_icon = "🟡"
                status_text = "Stale"
            else:
                status_icon = "🔴"
                status_text = "Offline"
                
            time_str = last_seen.strftime("%H:%M:%S (%d %b)")
            st.sidebar.markdown(f"**{status_icon} `{dev_name}`**: {status_text}  \n<small>Last: {time_str}</small>", unsafe_allow_html=True)
            active_devices_count += 1
            
        # Sparkline of Heartbeat frequency
        spark_df = hb_df.set_index("_time").resample("1h").size().reset_index(name="Pulses")
        st.sidebar.markdown("**Heartbeats / Hour**")
        st.sidebar.line_chart(spark_df, x="_time", y="Pulses", height=130)
else:
    st.sidebar.warning("No heartbeat data in the last 24h")

# --- DETECTIONS QUERY ---
@st.cache_data(ttl=60)
def fetch_detection_data(time_val):
    flux_query = f'''
    from(bucket: "{BUCKET}")
      |> range(start: {time_val})
      |> filter(fn: (r) => r["_measurement"] == "acoupi_detections")
      |> filter(fn: (r) => r["_field"] == "c" or r["_field"] == "confidence" or r["_field"] == "confidence_score" or r["_field"] == "detection_score" or r["_field"] == "value" or r["_field"] == "s" or r["_field"] == "label" or r["_field"] == "species")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    try:
        df = query_api.query_data_frame(flux_query)
        if isinstance(df, list):
            df = pd.concat(df, ignore_index=True) if len(df) > 0 else pd.DataFrame()
        return df
    except Exception as e:
        return pd.DataFrame()

raw_df = fetch_detection_data(selected_time_val)

# --- HEADER TITLE ---
st.title("🌿 Yeo Valley Bioacoustic Monitoring")
st.markdown("Real-time bioacoustic detection & telemetry dashboard powered by **Acoupi Uno Q** & **InfluxDB**.")

if raw_df.empty:
    st.warning(f"No acoustic detection data found for the selected time range ({selected_time_label}).")
    st.stop()

# --- DATA PROCESSING & RESOLUTION ---
df = raw_df.copy()

# 1. Format Time
if "_time" in df.columns:
    df["Time"] = pd.to_datetime(df["_time"])
elif "Time" in df.columns:
    df["Time"] = pd.to_datetime(df["Time"])
else:
    st.error("No timestamp column found in detection data.")
    st.stop()

# 2. Extract Device
if "device_id" in df.columns:
    df["Device"] = df["device_id"].fillna(df.get("topic", "unknown"))
else:
    df["Device"] = df.get("topic", "unknown")
df["Device"] = df["Device"].astype(str).apply(lambda d: d.split("/")[1] if "/" in d else d)

# 3. Extract Confidence Score
confidence_series = pd.Series(index=df.index, dtype="float64")
for col in ["c", "confidence", "confidence_score", "detection_score"]:
    if col in df.columns:
        confidence_series = confidence_series.fillna(pd.to_numeric(df[col], errors="coerce"))
df["Confidence"] = confidence_series

# 4. Extract and Resolve Species Identity
species_raw = pd.Series(index=df.index, dtype="object")
for col in ["s", "label", "species", "value"]:
    if col in df.columns:
        valid_vals = df[col].astype(str).replace({"nan": None, "None": None, "": None, "<NA>": None})
        species_raw = species_raw.fillna(valid_vals)

resolved_meta = [resolve_species(v) for v in species_raw]
df["Common_Name"] = [m["common_name"] for m in resolved_meta]
df["Scientific_Name"] = [m["species"] for m in resolved_meta]
df["Category"] = [m["category"] for m in resolved_meta]

# Drop records missing confidence or common name
df = df.dropna(subset=["Confidence", "Common_Name"])

# 5. Device Filter in Sidebar (now that devices are extracted)
available_devices = sorted(df["Device"].dropna().unique().tolist())
selected_device = st.sidebar.selectbox("Device Selection", options=["All Devices"] + available_devices, index=0)

if selected_device != "All Devices":
    df = df[df["Device"] == selected_device]

# 6. Apply Taxa Filter
if category_filter == "Birds Only 🐦":
    df = df[df["Category"] == "Bird"]
elif category_filter == "Bats Only 🦇":
    df = df[df["Category"] == "Bat"]
elif category_filter == "All Species (Birds & Bats)":
    df = df[df["Category"].isin(["Bird", "Bat"])]

# 7. Apply Confidence Threshold Filter
high_conf_df = df[df["Confidence"] >= confidence_threshold]

# --- KPI METRICS ROW ---
col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)

total_detections = len(high_conf_df)
unique_species = high_conf_df["Common_Name"].nunique() if not high_conf_df.empty else 0
top_species = high_conf_df["Common_Name"].mode().iloc[0] if not high_conf_df.empty else "None"
avg_confidence = f"{high_conf_df['Confidence'].mean():.1%}" if not high_conf_df.empty else "N/A"
reporting_nodes = df["Device"].nunique()

col_m1.metric("High-Conf Detections", f"{total_detections:,}")
col_m2.metric("Species Identified", f"{unique_species}")
col_m3.metric("Top Species", f"{top_species}")
col_m4.metric("Avg Confidence", f"{avg_confidence}")
col_m5.metric("Active Nodes", f"{reporting_nodes}")

st.divider()

if high_conf_df.empty:
    st.info(f"Detections exist for this selection, but none meet the confidence threshold of {confidence_threshold:.2f}.")
    st.stop()

# --- TIMELINE VISUALIZATION ---
st.subheader(f"📈 Species Activity Timeline (Confidence ≥ {confidence_threshold:.2f})")

# Limit to top 15 species for timeline visual clarity
top_timeline_species = high_conf_df["Common_Name"].value_counts().nlargest(15).index.tolist()
plot_df = high_conf_df[high_conf_df["Common_Name"].isin(top_timeline_species)].copy()

# Sort species by category and frequency
species_order = plot_df["Common_Name"].value_counts().index.tolist()

fig_timeline = px.scatter(
    plot_df,
    x="Time",
    y="Common_Name",
    color="Category",
    size="Confidence",
    size_max=9,
    opacity=0.8,
    category_orders={"Common_Name": species_order},
    color_discrete_map={"Bird": "#2b8a3e", "Bat": "#5f3dc4", "Other": "#868e96"},
    hover_data={
        "Time": "|%Y-%m-%d %H:%M:%S",
        "Common_Name": True,
        "Scientific_Name": True,
        "Confidence": ":.2f",
        "Device": True,
        "Category": True
    },
    template="plotly_white",
    height=420
)

# Solar Calculations for Yeo Valley / Bristol area (51.32 N, -2.71 W)
loc = LocationInfo("Yeo Valley", "UK", "Europe/London", 51.32, -2.71)
min_date = plot_df["Time"].min().date() - timedelta(days=1)
max_date = plot_df["Time"].max().date() + timedelta(days=1)

curr_date = min_date
while curr_date <= max_date:
    try:
        s_curr = sun(loc.observer, date=curr_date)
        s_next = sun(loc.observer, date=curr_date + timedelta(days=1))
        fig_timeline.add_vrect(
            x0=s_curr["sunset"],
            x1=s_next["sunrise"],
            fillcolor="rgba(30, 41, 59, 0.08)",
            opacity=1,
            layer="below",
            line_width=0,
            annotation_text="Night" if curr_date == min_date + timedelta(days=1) else None,
            annotation_position="top left"
        )
    except Exception:
        pass
    curr_date += timedelta(days=1)

x_min = plot_df["Time"].min() - pd.Timedelta(hours=1)
x_max = plot_df["Time"].max() + pd.Timedelta(hours=1)
fig_timeline.update_xaxes(range=[x_min, x_max], title="Timestamp (UTC)")
fig_timeline.update_yaxes(title="Common Name")
fig_timeline.update_layout(
    margin=dict(l=0, r=0, t=20, b=10),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

st.plotly_chart(fig_timeline, use_container_width=True)

st.divider()

# --- ROW 2: LEADERBOARD & DIURNAL/NOCTURNAL DISTRIBUTION ---
col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("🏆 Species Leaderboard")
    leaderboard = high_conf_df.groupby(["Common_Name", "Scientific_Name", "Category"]).agg(
        Count=("Common_Name", "count"),
        Max_Confidence=("Confidence", "max"),
        Avg_Confidence=("Confidence", "mean")
    ).sort_values(by="Count", ascending=False).reset_index()
    
    leaderboard["Max_Confidence"] = leaderboard["Max_Confidence"].apply(lambda x: f"{x:.2f}")
    leaderboard["Avg_Confidence"] = leaderboard["Avg_Confidence"].apply(lambda x: f"{x:.2f}")
    
    st.dataframe(
        leaderboard,
        column_config={
            "Common_Name": st.column_config.TextColumn("Common Name", width="medium"),
            "Category": st.column_config.TextColumn("Category", width="small"),
            "Scientific_Name": st.column_config.TextColumn("Scientific Name", width="medium"),
            "Count": st.column_config.ProgressColumn(
                "Detections",
                format="%d",
                min_value=0,
                max_value=int(leaderboard["Count"].max()) if not leaderboard.empty else 100
            ),
            "Max_Confidence": st.column_config.TextColumn("Max Conf", width="small"),
            "Avg_Confidence": st.column_config.TextColumn("Avg Conf", width="small"),
        },
        hide_index=True,
        use_container_width=True,
        height=380
    )

with col_right:
    st.subheader("⏰ Diurnal & Nocturnal Activity Profile")
    dist_df = high_conf_df.copy()
    dist_df["Hour"] = dist_df["Time"].dt.hour
    
    hourly_counts = dist_df.groupby(["Hour", "Category"]).size().reset_index(name="Count")
    
    fig_hourly = px.bar(
        hourly_counts,
        x="Hour",
        y="Count",
        color="Category",
        color_discrete_map={"Bird": "#2b8a3e", "Bat": "#5f3dc4", "Other": "#868e96"},
        barmode="stack",
        template="plotly_white",
        labels={"Hour": "Hour of Day (0-23 UTC)", "Count": "Detections"},
        height=380
    )
    fig_hourly.update_layout(
        margin=dict(l=0, r=0, t=20, b=10),
        xaxis=dict(tickmode="linear", tick0=0, dtick=2),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig_hourly, use_container_width=True)

st.divider()

# --- ROW 3: RECENT DETECTIONS & TELEMETRY TABS ---
tab_recent, tab_telemetry, tab_species_ref = st.tabs(["🕒 Recent Detections Feed", "📊 Hardware & System Telemetry", "📖 Species Reference List"])

with tab_recent:
    st.subheader("Latest Recorded Detections")
    recent = high_conf_df.sort_values("Time", ascending=False).head(50)[
        ["Time", "Device", "Common_Name", "Scientific_Name", "Category", "Confidence"]
    ].copy()
    
    recent["Formatted_Time"] = recent["Time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    recent_display = recent[["Formatted_Time", "Device", "Common_Name", "Scientific_Name", "Category", "Confidence"]]
    
    st.dataframe(
        recent_display,
        column_config={
            "Formatted_Time": st.column_config.TextColumn("Timestamp (UTC)"),
            "Device": st.column_config.TextColumn("Device Node"),
            "Common_Name": st.column_config.TextColumn("Common Name"),
            "Scientific_Name": st.column_config.TextColumn("Scientific Name"),
            "Category": st.column_config.TextColumn("Category"),
            "Confidence": st.column_config.NumberColumn("Confidence", format="%.2f")
        },
        hide_index=True,
        use_container_width=True,
        height=320
    )

with tab_telemetry:
    st.subheader("Hardware Telemetry (Cellular & Wi-Fi Nodes)")
    if not hb_df.empty and ("cpu" in hb_df.columns or "mem_free" in hb_df.columns or "shm" in hb_df.columns):
        telemetry_df = hb_df.dropna(subset=["_time"]).copy()
        
        # Filter for devices reporting numeric telemetry
        t_cols = [c for c in ["cpu", "mem_free", "mem_used", "shm"] if c in telemetry_df.columns]
        valid_telemetry = telemetry_df.dropna(subset=t_cols, how="all")
        
        if not valid_telemetry.empty:
            tel_col1, tel_col2 = st.columns(2)
            
            with tel_col1:
                if "cpu" in valid_telemetry.columns:
                    fig_cpu = px.line(
                        valid_telemetry,
                        x="_time",
                        y="cpu",
                        color="Device",
                        title="CPU Utilization (%)",
                        template="plotly_white",
                        labels={"_time": "Time", "cpu": "CPU %"}
                    )
                    fig_cpu.update_layout(margin=dict(l=0, r=0, t=40, b=10))
                    st.plotly_chart(fig_cpu, use_container_width=True)
                    
            with tel_col2:
                if "mem_free" in valid_telemetry.columns:
                    fig_mem = px.line(
                        valid_telemetry,
                        x="_time",
                        y="mem_free",
                        color="Device",
                        title="Free Memory (MB)",
                        template="plotly_white",
                        labels={"_time": "Time", "mem_free": "RAM Free (MB)"}
                    )
                    fig_mem.update_layout(margin=dict(l=0, r=0, t=40, b=10))
                    st.plotly_chart(fig_mem, use_container_width=True)
                    
            if "shm" in valid_telemetry.columns:
                st.markdown("**RAM Disk (/run/shm) Space (MB)**")
                fig_shm = px.line(
                    valid_telemetry,
                    x="_time",
                    y="shm",
                    color="Device",
                    template="plotly_white",
                    labels={"_time": "Time", "shm": "SHM Available (MB)"}
                )
                fig_shm.update_layout(margin=dict(l=0, r=0, t=10, b=10), height=250)
                st.plotly_chart(fig_shm, use_container_width=True)
        else:
            st.info("No numerical telemetry (CPU/Memory/SHM) reported in current window.")
    else:
        st.info("Telemetry data (CPU/RAM/SHM) is available when cellular nodes report heartbeats.")

with tab_species_ref:
    st.subheader("Reference Taxa Catalog")
    st.caption("Mapped species database containing 208 British birds and 17 British bat species.")
    species_catalog, _, _, _ = load_species_database()
    st.dataframe(
        species_catalog[["species_id", "category", "common_name", "species", "class_name"]],
        column_config={
            "species_id": st.column_config.NumberColumn("Numeric ID", format="%d"),
            "category": st.column_config.TextColumn("Category"),
            "common_name": st.column_config.TextColumn("Common Name"),
            "species": st.column_config.TextColumn("Scientific Name"),
            "class_name": st.column_config.TextColumn("Class Identifier")
        },
        hide_index=True,
        use_container_width=True,
        height=320
    )