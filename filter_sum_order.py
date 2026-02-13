

import streamlit as st
from datetime import datetime
import time
import random

# ---------------------------
# PAGE CONFIG
# ---------------------------
st.set_page_config(
    page_title="Trade Book Dashboard",
    layout="wide"
)

# ---------------------------
# HEADER
# ---------------------------
st.title("Trade Book Dashboard – Layer 2")
st.success("UI is live and updating")

# ---------------------------
# SYSTEM STATUS
# ---------------------------
st.subheader("System Status")

col1, col2 = st.columns(2)

with col1:
    st.write("Python is running")
    st.write("This is NOT Binance yet")

with col2:
    st.write("Current Time")
    st.code(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

st.divider()

# ---------------------------
# PLACEHOLDER FOR LIVE DATA
# ---------------------------
st.subheader("Live Trades (Simulated)")

# Create table placeholder
table_placeholder = st.empty()

# ---------------------------
# SIMULATED TRADE FEED
# ---------------------------
def generate_trade():
    return {
        "time": datetime.now().strftime("%H:%M:%S"),
        "side": random.choice(["BUY", "SELL"]),
        "price": round(random.uniform(72000, 73000), 2),
        "qty": round(random.uniform(0.01, 0.5), 4)
    }

# Store trades in session state
if "trades" not in st.session_state:
    st.session_state.trades = []

# ---------------------------
# UPDATE LOOP (SAFE)
# ---------------------------
new_trade = generate_trade()
st.session_state.trades.insert(0, new_trade)

# Keep last 15 trades
st.session_state.trades = st.session_state.trades[:15]

# Render table
table_placeholder.table(st.session_state.trades)

# Slow refresh (prevents crashes)
time.sleep(1)
st.rerun()