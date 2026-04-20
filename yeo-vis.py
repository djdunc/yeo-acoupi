import streamlit as st
import pandas as pd
from influxdb_client import InfluxDBClient
import plotly.express as px

# Access secrets
URL = st.secrets["INFLUX_URL"]
TOKEN = st.secrets["INFLUX_TOKEN"]
ORG = st.secrets["INFLUX_ORG"]
BUCKET = st.secrets["INFLUX_BUCKET"]

# Connect to InfluxDB
client = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
query_api = client.query_api()

st.set_page_config(page_title="Yeo Acoupi UnoQ test", page_icon="🐦", layout="wide")
st.title("Yeo Acoupi Uno Q Garden Test")

# --- DATA QUERY ---
flux_query = f'''
from(bucket: "{BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r["_measurement"] == "acoupi_data")
  |> filter(fn: (r) => r["_field"] == "value" or r["_field"] == "confidence_score")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "value", "confidence_score", "label"])
'''

df = query_api.query_data_frame(flux_query)

if not df.empty:
    # 1. Filter the DataFrame for high-confidence detections
    df = df[df["confidence_score"] >= 0.6]

    # 2. Proceed with renaming and formatting
    if not df.empty:
        df = df.rename(columns={"_time": "Time", "value": "Species", "confidence_score": "Confidence"})
        df["Time"] = pd.to_datetime(df["Time"])
        
        # --- ROW 1: Full Width Timeline ---
        st.subheader("High Confidence Activity Timeline (>0.6)")
        fig = px.scatter(df, 
                         x="Time", 
                         y="Species", 
                         color="Species", 
                         size="Confidence",
                         hover_data=["Confidence"],
                         template="plotly_white",
                         height=400)
        
        fig.update_layout(margin=dict(l=0, r=0, t=30, b=0))
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
    st.warning("No data found in the last 24 hours.")