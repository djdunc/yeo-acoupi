import streamlit as st
import pandas as pd
from influxdb_client import InfluxDBClient
import plotly.express as px
from astral import LocationInfo
from astral.sun import sun
from datetime import timedelta

# Access secrets
# stored in .streamlit/secrets.toml
# INFLUX_URL = ""
# INFLUX_TOKEN = ""
# INFLUX_ORG = ""
# INFLUX_BUCKET = ""

URL = st.secrets["INFLUX_URL"]
TOKEN = st.secrets["INFLUX_TOKEN"]
ORG = st.secrets["INFLUX_ORG"]
BUCKET = st.secrets["INFLUX_BUCKET"]

# Connect to InfluxDB
client = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
query_api = client.query_api()

st.set_page_config(page_title="Yeo Acoupi UnoQ test", page_icon="🐦", layout="wide")
st.title("Yeo Acoupi Uno Q Garden Test")

st.sidebar.header("Controls")
time_options = {
    "Last 24 Hours": "-24h",
    "Last 2 Days": "-2d",
    "Last Week": "-7d",
    "Last Month": "-30d"
}
selected_time_label = st.sidebar.selectbox("Time Range", options=list(time_options.keys()), index=2)
selected_time_val = time_options[selected_time_label]

st.sidebar.divider()

# --- HEARTBEAT QUERY ---
hb_query = f'''
from(bucket: "{BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r["_measurement"] == "acoupi_heartbeat")
  |> filter(fn: (r) => r["_field"] == "status")
  |> keep(columns: ["_time", "_value"])
'''

try:
    hb_df = query_api.query_data_frame(hb_query)
    
    if type(hb_df) is list:
        hb_df = pd.concat(hb_df) if len(hb_df) > 0 else pd.DataFrame()
        
    if not hb_df.empty:
        # Get the timestamp of the very last pulse
        last_pulse = hb_df["_time"].max()
        # Convert to local time for display
        st.sidebar.success(f"Uno Q Online\nLast Pulse: {last_pulse.strftime('%H:%M:%S')}")
        
        # Create a sparkline of heartbeats per hour
        spark_df = hb_df.set_index("_time").resample("1h").size().reset_index(name="count")
        st.sidebar.markdown("**Heartbeats / Hour**")
        st.sidebar.line_chart(spark_df, x="_time", y="count", height=150)
    else:
        st.sidebar.error("System Offline: No pulses in last 24h")
except Exception as e:
    st.sidebar.warning("Waiting for heartbeat data...")

# --- DATA QUERY ---
flux_query = f'''
from(bucket: "{BUCKET}")
  |> range(start: {selected_time_val})
  |> filter(fn: (r) => r["_measurement"] == "acoupi_detections")
  |> filter(fn: (r) => r["_field"] == "value" or r["_field"] == "confidence" or r["_field"] == "detection_score")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
'''

df = query_api.query_data_frame(flux_query)

if not df.empty:
    # Handle naming inconsistency between 'confidence' and 'detection_score'
    # This prevents the KeyError crash
    possible_conf_cols = ["confidence", "detection_score"]
    conf_col = next((c for c in possible_conf_cols if c in df.columns), None)

    if conf_col:
        # 1. Filter for high-confidence detections
        df = df[df[conf_col] >= 0.6]

        if not df.empty:
            # 2. Rename and format
            df = df.rename(columns={"_time": "Time", "value": "Species", conf_col: "Confidence"})
            df["Time"] = pd.to_datetime(df["Time"])
            
            # --- DEVICE SELECTION ---
            if "topic" in df.columns:
                devices = df["topic"].dropna().unique().tolist()
                if devices:
                    selected_device = st.selectbox("Select Device", options=["All"] + devices)
                    if selected_device != "All":
                        df = df[df["topic"] == selected_device]
            
            if df.empty:
                st.info("No high-confidence detections for the selected device.")
                st.stop()

            # --- LIMIT TO TOP 12 SPECIES ---
            top_species = df["Species"].value_counts().nlargest(12).index
            df = df[df["Species"].isin(top_species)]

            # --- ROW 1: Full Width Timeline ---
            st.subheader("High Confidence Activity Timeline (>0.6)")
            hover_cols = ["Confidence", "topic"] if "topic" in df.columns else ["Confidence"]
            fig = px.scatter(df, 
                             x="Time", 
                             y="Species", 
                             color="Species", 
                             size="Confidence",
                             size_max=8,
                             opacity=0.7,
                             hover_data=hover_cols,
                             template="plotly_white",
                             height=400)
            
            fig.update_layout(
                margin=dict(l=0, r=0, t=30, b=0),
                legend=dict(itemclick="toggleothers", itemdoubleclick="toggle")
            )

            # --- NIGHTTIME SHADING ---
            # Approximating location
            loc = LocationInfo("London", "UK", "Europe/London", 51.5, -0.1)
            min_date = df["Time"].min().date() - timedelta(days=1)
            max_date = df["Time"].max().date() + timedelta(days=1)
            
            curr_date = min_date
            while curr_date <= max_date:
                try:
                    s_curr = sun(loc.observer, date=curr_date)
                    s_next = sun(loc.observer, date=curr_date + timedelta(days=1))
                    
                    fig.add_vrect(
                        x0=s_curr["sunset"], x1=s_next["sunrise"],
                        fillcolor="rgba(0, 0, 0, 0.05)",
                        opacity=1,
                        layer="below",
                        line_width=0
                    )
                except Exception:
                    pass
                curr_date += timedelta(days=1)

            # Constrain the x-axis to the data bounds (plus a small margin)
            x_min = df["Time"].min() - pd.Timedelta(hours=1)
            x_max = df["Time"].max() + pd.Timedelta(hours=1)
            fig.update_xaxes(range=[x_min, x_max])

            st.plotly_chart(fig, use_container_width=True)

            st.divider()

            # --- ROW 2: Leaderboard and Detections Side-by-Side ---
            col_left, col_right = st.columns(2)

            with col_left:
                st.subheader("Leaderboard (Top Tier)")
                leaderboard = df.groupby("Species").agg(
                    Count=("Species", "count"),
                    Avg_Conf=("Confidence", "mean")
                ).sort_values(by="Count", ascending=False).reset_index()
                
                leaderboard["Avg_Conf"] = leaderboard["Avg_Conf"].round(2)
                st.dataframe(leaderboard, hide_index=True, use_container_width=True)

            with col_right:
                st.subheader("Latest Detections")
                recent = df.sort_values("Time", ascending=False).head(10)[["Time", "Species", "Confidence"]]
                recent["Time"] = recent["Time"].dt.strftime('%H:%M:%S')
                st.dataframe(recent, hide_index=True, use_container_width=True)
        else:
            st.info("Detections exist, but none meet the 0.6 confidence threshold.")
    else:
        st.error(f"Data found, but no confidence field detected. Columns: {df.columns.tolist()}")
else:
    st.warning(f"No bird data found for: {selected_time_label}. (Check measurement: acoupi_detections)")