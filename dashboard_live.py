import streamlit as st
import websocket
import json
import threading
import time

st.set_page_config(page_title="Live Trades", layout="wide")
st.title("📊 Binance BTCUSDT Live Trades")

# ---------------- STATE ----------------
if "trades" not in st.session_state:
    st.session_state.trades = []

if "ws_started" not in st.session_state:
    st.session_state.ws_started = False

# ---------------- WEBSOCKET ----------------
def on_message(ws, message):
    data = json.loads(message)
    trade = {
        "time": time.strftime("%H:%M:%S"),
        "side": "BUY" if not data["m"] else "SELL",
        "price": data["p"],
        "qty": data["q"],
    }
    st.session_state.trades.append(trade)
    st.session_state.trades = st.session_state.trades[-50:]

def start_ws():
    ws = websocket.WebSocketApp(
        "wss://stream.binance.com:9443/ws/btcusdt@trade",
        on_message=on_message,
    )
    ws.run_forever()

# ---------------- START WS ONCE ----------------
if not st.session_state.ws_started:
    threading.Thread(target=start_ws, daemon=True).start()
    st.session_state.ws_started = True

# ---------------- UI ----------------
st.subheader("Latest Trades")

for trade in reversed(st.session_state.trades[-10:]):
    color = "🟢" if trade["side"] == "BUY" else "🔴"
    st.write(
        f"{trade['time']} | {color} {trade['side']} | "
        f"Price: {trade['price']} | Qty: {trade['qty']}"
    )

# auto refresh every second
time.sleep(1)
st.experimental_rerun()