import streamlit as st
import pandas as pd
import numpy as np
import time
from datetime import datetime
import sys
import requests
import importlib

# ================= FORCE FRESH DETECTOR - FIXES min_trades ERROR =================
import enhanced_detection
importlib.reload(enhanced_detection)
from enhanced_detection import CleanAggressiveMoveDetector

if "enhanced_detector" in st.session_state:
    del st.session_state["enhanced_detector"]

st.session_state.enhanced_detector = CleanAggressiveMoveDetector()

d = st.session_state.enhanced_detector
if not hasattr(d, "min_trades"):
    d.min_trades = 10
if not hasattr(d, "min_volume"):
    d.min_volume = 0.5
if not hasattr(d, "consolidation_threshold"):
    d.consolidation_threshold = 0.08
if not hasattr(d, "min_price_move"):
    d.min_price_move = 0.15
if not hasattr(d, "min_trades_per_sec"):
    d.min_trades_per_sec = 5
if not hasattr(d, "volume_spike_threshold"):
    d.volume_spike_threshold = 2.5
if not hasattr(d, "delta_concentration"):
    d.delta_concentration = 0.60
if not hasattr(d, "signal_cooldown"):
    d.signal_cooldown = 3
if not hasattr(d, "last_signal_time"):
    d.last_signal_time = None
if not hasattr(d, "whale_dominance"):
    d.whale_dominance = 0.25
from collections import deque
if not hasattr(d, "volume_history"):
    d.volume_history = deque(maxlen=30)
if not hasattr(d, "delta_history"):
    d.delta_history = deque(maxlen=30)
if not hasattr(d, "price_history"):
    d.price_history = deque(maxlen=30)
if not hasattr(d, "aggression_history"):
    d.aggression_history = deque(maxlen=20)
# =================================================================================

from binance_futures_feed import trade_queue, mark_price, start_binance_feed

WINDOW_SECONDS = 10
MAX_ROWS_DISPLAY = 30
WHALE_QTY = 0.5
MIN_DELTA_ABS = 0.05
STRUCTURE_LOOKBACK = 20

st.set_page_config(layout="wide", page_title="Futures Trading Pro v5.0")
st.title("Binance Futures Trading Dashboard v5.0 - BTCUSDT Perpetual")

st.markdown("""
<style>
    .signal-card {
        padding: 0.75rem;
        border-radius: 8px;
        margin: 0.5rem 0;
        border-left: 4px solid;
        font-weight: 600;
    }
    .signal-long { background: rgba(16, 185, 129, 0.1); border-left-color: #10b981; }
    .signal-short { background: rgba(239, 68, 68, 0.1); border-left-color: #ef4444; }
    .signal-warning { background: rgba(245, 158, 11, 0.1); border-left-color: #f59e0b; }
    .signal-danger { background: rgba(220, 38, 38, 0.2); border-left-color: #dc2626; }
    .signal-neutral { background: rgba(107, 114, 128, 0.1); border-left-color: #6b7280; }
    .lgp-meter { height: 20px; border-radius: 10px; margin: 10px 0; overflow: hidden; }
    .lgp-fill { height: 100%; transition: width 0.5s ease; }
</style>
""", unsafe_allow_html=True)

# ================= INITIALIZE FEED =================
if "feed_started" not in st.session_state:
    start_binance_feed()
    st.session_state.feed_started = True
    st.session_state.trade_history = []
    st.session_state.lgp_history = []
    st.session_state.liquidation_events = []

defaults = {
    "last_price": 0.0,
    "price_history": [],
    "aggression_score_history": [],
    "long_liquidations": 0,
    "short_liquidations": 0,
    "prev_aggressive_buy": False,
    "prev_aggressive_sell": False,
    "prev_lgp_high": False,
    "in_position": False,
    "position_type": None,
    "entry_price": 0.0,
    "entry_time": None,
    "prev_exit_triggered": False,
    "telegram_enabled": False,
    "sound_enabled": True,
    "exit_alerts_enabled": True,
    "exit_loss_pct": 0.5,
    "exit_profit_pct": 1.0,
    "telegram_token": "",
    "telegram_chat_id": "",
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ================= LOAD DATA =================
now = time.time()
cutoff = now - WINDOW_SECONDS

if trade_queue:
    df = pd.DataFrame(list(trade_queue))
    df = df[df["time"] >= cutoff]
    df = df.sort_values("time", ascending=False)
    df["value"] = df["price"] * df["qty"]
    st.session_state.trade_history.extend(df.to_dict("records"))
    st.session_state.trade_history = st.session_state.trade_history[-2000:]
else:
    df = pd.DataFrame(columns=["time", "side", "price", "qty", "value"])

# ================= LGP CALCULATION =================
def calculate_lgp(df):
    if df.empty or len(df) < 5:
        return 0, [], False, False
    latest_price = df.iloc[0]["price"]
    structure_df = df.head(STRUCTURE_LOOKBACK)
    range_high = structure_df["price"].max()
    range_low = structure_df["price"].min()
    sweep_high = latest_price > range_high * 1.001
    sweep_low = latest_price < range_low * 0.999
    buy_volume = df[df["side"] == "BUY"]["qty"].sum()
    sell_volume = df[df["side"] == "SELL"]["qty"].sum()
    delta = buy_volume - sell_volume
    failed_high = sweep_high and delta < 0
    failed_low = sweep_low and delta > 0
    lgp = 0
    signals = []
    if failed_high or failed_low:
        lgp += 40
        signals.append("Failed Structure Break")
    if delta <= -MIN_DELTA_ABS and latest_price > df.iloc[-1]["price"]:
        lgp += 20
        signals.append("Price-Delta Divergence")
    trades_per_sec = len(df) / WINDOW_SECONDS
    if trades_per_sec >= 5:
        lgp += 10
        signals.append("High Trade Speed")
    whales = df[df["qty"] >= WHALE_QTY]
    if len(df) > 0 and len(whales) / len(df) > 0.3:
        lgp += 10
        signals.append("High Whale Pct")
    lgp = min(lgp, 100)
    return lgp, signals, failed_high, failed_low

# ================= LIQUIDATION DETECTION =================
def detect_liquidations(df):
    if df.empty or len(df) < 10:
        return {"liquidation_detected": False, "liquidation_type": None, "severity": 0, "price_change_pct": 0}
    first_price = df.iloc[-1]["price"]
    last_price = df.iloc[0]["price"]
    price_change_pct = ((last_price - first_price) / first_price) * 100
    total_volume = df["qty"].sum()
    buy_volume = df[df["side"] == "BUY"]["qty"].sum()
    sell_volume = df[df["side"] == "SELL"]["qty"].sum()
    delta = buy_volume - sell_volume
    delta_ratio = abs(delta) / total_volume if total_volume > 0 else 0
    large_orders = df[df["qty"] >= WHALE_QTY * 2]
    large_ratio = len(large_orders) / len(df) if len(df) > 0 else 0
    long_liq = price_change_pct <= -0.5 and delta < -0.1 and delta_ratio > 0.7 and large_ratio > 0.3
    short_liq = price_change_pct >= 0.5 and delta > 0.1 and delta_ratio > 0.7 and large_ratio > 0.3
    severity = 0
    if long_liq or short_liq:
        severity = min(100, int(abs(price_change_pct) * 20 + delta_ratio * 50 + large_ratio * 30))
    return {
        "liquidation_detected": long_liq or short_liq,
        "liquidation_type": "LONG" if long_liq else "SHORT" if short_liq else None,
        "severity": severity,
        "price_change_pct": price_change_pct
    }

# ================= CALCULATE METRICS =================
lgp, lgp_signals, failed_high, failed_low = calculate_lgp(df)

aggressive_data = st.session_state.enhanced_detector.detect_clean_aggressive_move(
    df=df,
    window_seconds=WINDOW_SECONDS,
    current_time=time.time()
)

liq_data = detect_liquidations(df)

if liq_data["liquidation_detected"]:
    if liq_data["liquidation_type"] == "LONG":
        st.session_state.long_liquidations += 1
    else:
        st.session_state.short_liquidations += 1

aggression_score = aggressive_data["confidence"]
metrics = aggressive_data.get("metrics", {})
volume_zscore = metrics.get("volume_zscore", 0)
delta_ratio = metrics.get("delta_ratio", 0)
price_change_pct = metrics.get("price_change_pct", 0)
trades_per_sec = metrics.get("trades_per_sec", 0)
whale_concentration = metrics.get("whale_concentration", 0)
has_acceleration = metrics.get("has_acceleration", False)
aggressive_buy = aggressive_data["aggressive_buy"]
aggressive_sell = aggressive_data["aggressive_sell"]

total_trades = len(df)
total_volume = df["qty"].sum() if not df.empty else 0
buy_volume = df[df["side"] == "BUY"]["qty"].sum() if not df.empty else 0
sell_volume = df[df["side"] == "SELL"]["qty"].sum() if not df.empty else 0
delta = buy_volume - sell_volume

current_price = df.iloc[0]["price"] if not df.empty else st.session_state.get("last_price", 0.0)
if not current_price:
    current_price = 0.0
if not df.empty and current_price > 0:
    st.session_state.last_price = current_price

mp = mark_price if mark_price else {}
current_mark_price = mp.get("price", current_price)
index_price = mp.get("index_price", current_price)
current_funding = mp.get("funding_rate", 0)
funding_annual = current_funding * 3 * 365 * 100

price_change_pct_display = 0.0
if st.session_state.price_history and current_price > 0:
    old_price = st.session_state.price_history[-1][1]
    if old_price and old_price > 0:
        price_change_pct_display = (current_price - old_price) / old_price * 100

st.session_state.price_history.append((now, current_price))
st.session_state.price_history = st.session_state.price_history[-100:]
st.session_state.lgp_history.append(lgp)
st.session_state.lgp_history = st.session_state.lgp_history[-50:]
st.session_state.aggression_score_history.append(aggression_score)
st.session_state.aggression_score_history = st.session_state.aggression_score_history[-50:]

current_time_str = datetime.now().strftime("%H:%M:%S")

# ================= ALERT FUNCTIONS =================
def send_telegram_alert(message):
    if not st.session_state.telegram_enabled:
        return False
    try:
        token = st.session_state.get("telegram_token", "")
        chat_id = st.session_state.get("telegram_chat_id", "")
        if not token or not chat_id:
            return False
        url = "https://api.telegram.org/bot" + token + "/sendMessage"
        response = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=5)
        return response.status_code == 200
    except Exception:
        return False

# ================= FIRE ALERTS =================
if aggressive_buy and not st.session_state.prev_aggressive_buy:
    msg = "BUY SIGNAL\n" + current_time_str + "\n$" + "{:,.2f}".format(current_price) + "\nConfidence: " + str(aggression_score) + "%\nVol: " + "{:.1f}".format(volume_zscore) + "sigma"
    send_telegram_alert(msg)
    if st.session_state.sound_enabled:
        st.toast("BUY Signal Detected!", icon="rocket")
    st.session_state.prev_aggressive_buy = True
elif not aggressive_buy:
    st.session_state.prev_aggressive_buy = False

if aggressive_sell and not st.session_state.prev_aggressive_sell:
    msg = "SELL SIGNAL\n" + current_time_str + "\n$" + "{:,.2f}".format(current_price) + "\nConfidence: " + str(aggression_score) + "%\nVol: " + "{:.1f}".format(volume_zscore) + "sigma"
    send_telegram_alert(msg)
    if st.session_state.sound_enabled:
        st.toast("SELL Signal Detected!", icon="warning")
    st.session_state.prev_aggressive_sell = True
elif not aggressive_sell:
    st.session_state.prev_aggressive_sell = False

if lgp >= 70 and not st.session_state.prev_lgp_high:
    msg = "LIQUIDITY GRAB DETECTED\n" + current_time_str + "\nLGP: " + str(lgp) + "%\n$" + "{:,.2f}".format(current_price)
    send_telegram_alert(msg)
    st.session_state.prev_lgp_high = True
elif lgp < 70:
    st.session_state.prev_lgp_high = False

# Exit Alerts
if st.session_state.exit_alerts_enabled and st.session_state.in_position:
    entry = st.session_state.entry_price
    pos_type = st.session_state.position_type
    if entry > 0 and current_price > 0:
        if pos_type == "BUY":
            pnl_pct = (current_price - entry) / entry * 100
        else:
            pnl_pct = (entry - current_price) / entry * 100
        if pnl_pct <= -st.session_state.exit_loss_pct:
            if not st.session_state.prev_exit_triggered:
                send_telegram_alert("STOP LOSS HIT\nLoss: " + "{:.2f}%".format(pnl_pct))
                st.session_state.prev_exit_triggered = True
                st.error("STOP LOSS HIT: " + "{:.2f}%".format(pnl_pct))
        elif pnl_pct >= st.session_state.exit_profit_pct:
            if not st.session_state.prev_exit_triggered:
                send_telegram_alert("TAKE PROFIT HIT\nProfit: " + "{:.2f}%".format(pnl_pct))
                st.session_state.prev_exit_triggered = True
                st.success("TAKE PROFIT HIT: +" + "{:.2f}%".format(pnl_pct))
        elif (pos_type == "BUY" and aggressive_sell) or (pos_type == "SELL" and aggressive_buy):
            if not st.session_state.prev_exit_triggered:
                send_telegram_alert("SIGNAL REVERSAL\nPnL: " + "{:.2f}%".format(pnl_pct))
                st.session_state.prev_exit_triggered = True
                st.warning("SIGNAL REVERSAL - Consider exiting")
        else:
            st.session_state.prev_exit_triggered = False

# Position Auto-Tracking
if aggressive_buy and aggression_score >= 75 and not st.session_state.in_position:
    st.session_state.in_position = True
    st.session_state.position_type = "BUY"
    st.session_state.entry_price = current_price
    st.session_state.entry_time = datetime.now()
    st.session_state.prev_exit_triggered = False
    send_telegram_alert("AUTO LONG opened @ $" + "{:,.2f}".format(current_price))

elif aggressive_sell and aggression_score >= 75 and not st.session_state.in_position:
    st.session_state.in_position = True
    st.session_state.position_type = "SELL"
    st.session_state.entry_price = current_price
    st.session_state.entry_time = datetime.now()
    st.session_state.prev_exit_triggered = False
    send_telegram_alert("AUTO SHORT opened @ $" + "{:,.2f}".format(current_price))

# ================= HEADER METRICS =================
st.subheader("Real-Time Futures Metrics")
top_cols = st.columns(6)

with top_cols[0]:
    if current_price > 0:
        st.metric("Futures Price", "${:,.2f}".format(current_price), "{:+.2f}%".format(price_change_pct_display))
    else:
        st.metric("Futures Price", "Loading...", "")

with top_cols[1]:
    if current_mark_price and current_mark_price > 0:
        basis = current_price - index_price
        st.metric("Mark Price", "${:,.2f}".format(current_mark_price), "${:+.2f} basis".format(basis))
    else:
        st.metric("Mark Price", "Loading...", "")

with top_cols[2]:
    st.metric("Funding Rate", "{:.4f}%".format(current_funding * 100), "{:.1f}% APR".format(funding_annual))

with top_cols[3]:
    st.metric("Delta", "{:+.4f}".format(delta), "{:+.1f}%".format((delta / total_volume * 100) if total_volume > 0 else 0))

with top_cols[4]:
    conf_label = "Strong" if aggression_score >= 80 else "Moderate" if aggression_score >= 60 else "Low"
    st.metric("Confidence", "{:.0f}%".format(aggression_score), conf_label)

with top_cols[5]:
    liq_status = "ACTIVE" if liq_data["liquidation_detected"] else "Clear"
    st.metric("Liquidations", "L:" + str(st.session_state.long_liquidations) + " S:" + str(st.session_state.short_liquidations), liq_status)

# ================= LIQUIDATION ALERT =================
if liq_data["liquidation_detected"]:
    st.markdown("---")
    st.markdown(
        "<div class='signal-card signal-danger'>"
        "<h3>LIQUIDATION CASCADE DETECTED</h3>"
        "<p><strong>" + str(liq_data["liquidation_type"]) + " Liquidation | Severity: " + str(liq_data["severity"]) + "%</strong></p>"
        "<p>Price Move: " + "{:.2f}%".format(liq_data["price_change_pct"]) + "</p>"
        "</div>",
        unsafe_allow_html=True
    )

# ================= LGP METER =================
st.markdown("---")
st.subheader("Liquidity Grab Probability (LGP)")
lgp_label = "HIGH RISK" if lgp >= 70 else "MEDIUM" if lgp >= 40 else "LOW"
meter_cols = st.columns([3, 1])
with meter_cols[0]:
    fill_color = "#ef4444" if lgp >= 70 else "#f59e0b" if lgp >= 40 else "#10b981"
    st.markdown(
        "<div class='lgp-meter' style='background:#374151;'>"
        "<div class='lgp-fill' style='width:" + str(lgp) + "%; background:" + fill_color + ";'></div>"
        "</div>",
        unsafe_allow_html=True
    )
with meter_cols[1]:
    st.metric("LGP Score", str(lgp) + "%", lgp_label)

if lgp_signals:
    st.write("Active Signals: " + " | ".join(lgp_signals))

# ================= ENHANCED ANALYSIS =================
st.markdown("---")
st.subheader("Enhanced Move Analysis")
anal_cols = st.columns(5)
with anal_cols[0]:
    st.metric("Delta Ratio", "{:.2%}".format(delta_ratio))
with anal_cols[1]:
    st.metric("Price Change", "{:.3f}%".format(price_change_pct))
with anal_cols[2]:
    st.metric("Trades/Sec", "{:.1f}".format(trades_per_sec))
with anal_cols[3]:
    st.metric("Volume Z-Score", "{:.2f}s".format(volume_zscore))
with anal_cols[4]:
    st.metric("Whale Conc", "{:.1%}".format(whale_concentration))

# ================= SIGNAL CARDS =================
st.markdown("---")
st.subheader("Long/Short Signals")
sig_cols = st.columns(2)

with sig_cols[0]:
    if aggressive_buy:
        funding_ok = current_funding < 0
        st.markdown(
            "<div class='signal-card signal-long'>"
            "<h3>LONG SIGNAL</h3>"
            "<p>Confidence: <strong>" + str(aggression_score) + "%</strong></p>"
            "<p>Vol: " + "{:.1f}s".format(volume_zscore) +
            " | Delta: " + "{:.1%}".format(delta_ratio) +
            " | Whales: " + "{:.1%}".format(whale_concentration) + "</p>"
            "<p>Momentum: " + ("Accelerating" if has_acceleration else "Spike") +
            " | Funding: " + ("Favorable" if funding_ok else "Caution") + "</p>"
            "<p><strong>Consider LONG entry</strong></p>"
            "</div>",
            unsafe_allow_html=True
        )
    elif delta > 0.05:
        st.markdown(
            "<div class='signal-card signal-neutral'><h4>Mild Long Bias</h4>"
            "<p>Buying pressure, no confirmed signal yet</p></div>",
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            "<div class='signal-card signal-neutral'><h4>No Long Signal</h4>"
            "<p>Monitoring market...</p></div>",
            unsafe_allow_html=True
        )

with sig_cols[1]:
    if aggressive_sell:
        funding_ok = current_funding > 0
        st.markdown(
            "<div class='signal-card signal-short'>"
            "<h3>SHORT SIGNAL</h3>"
            "<p>Confidence: <strong>" + str(aggression_score) + "%</strong></p>"
            "<p>Vol: " + "{:.1f}s".format(volume_zscore) +
            " | Delta: " + "{:.1%}".format(delta_ratio) +
            " | Whales: " + "{:.1%}".format(whale_concentration) + "</p>"
            "<p>Momentum: " + ("Accelerating" if has_acceleration else "Spike") +
            " | Funding: " + ("Favorable" if funding_ok else "Caution") + "</p>"
            "<p><strong>Consider SHORT entry</strong></p>"
            "</div>",
            unsafe_allow_html=True
        )
    elif delta < -0.05:
        st.markdown(
            "<div class='signal-card signal-neutral'><h4>Mild Short Bias</h4>"
            "<p>Selling pressure, no confirmed signal yet</p></div>",
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            "<div class='signal-card signal-neutral'><h4>No Short Signal</h4>"
            "<p>Monitoring market...</p></div>",
            unsafe_allow_html=True
        )

# ================= FUNDING ANALYSIS =================
st.markdown("---")
st.subheader("Funding Rate Analysis")
fund_cols = st.columns(4)
with fund_cols[0]:
    st.metric("8hr Rate", "{:.4f}%".format(current_funding * 100))
with fund_cols[1]:
    st.metric("Annualized", "{:.1f}%".format(funding_annual))
with fund_cols[2]:
    if current_funding > 0.01:
        sentiment = "Extremely Bullish"
    elif current_funding > 0:
        sentiment = "Bullish"
    elif current_funding < -0.01:
        sentiment = "Extremely Bearish"
    else:
        sentiment = "Neutral/Bearish"
    st.metric("Sentiment", sentiment)
with fund_cols[3]:
    strategy = "Fade funding" if abs(current_funding) > 0.01 else "Follow trend"
    st.metric("Strategy Hint", strategy)

# ================= CHARTS =================
st.markdown("---")
chart1, chart2 = st.columns(2)
with chart1:
    if len(st.session_state.lgp_history) > 5:
        st.subheader("LGP Trend")
        st.line_chart(pd.DataFrame({"LGP": st.session_state.lgp_history}), height=200)
with chart2:
    if len(st.session_state.aggression_score_history) > 5:
        st.subheader("Confidence Trend")
        st.line_chart(pd.DataFrame({"Confidence": st.session_state.aggression_score_history}), height=200)

# ================= TRADE TAPE =================
st.markdown("---")
st.subheader("Live Trade Tape")
if not df.empty:
    display_df = df.head(MAX_ROWS_DISPLAY).copy()
    display_df["timestamp"] = pd.to_datetime(display_df["time"], unit="s")
    display_df["time"] = display_df["timestamp"].dt.strftime("%H:%M:%S")
    display_df["side"] = display_df["side"].apply(lambda x: "BUY" if x == "BUY" else "SELL")
    display_df["qty"] = display_df["qty"].apply(lambda x: "{:.4f}".format(x))
    display_df["value"] = display_df["value"].apply(lambda x: "${:,.0f}".format(x))
    display_df["price"] = display_df["price"].apply(lambda x: "{:.2f}".format(x))
    st.dataframe(display_df[["time", "side", "price", "qty", "value"]], use_container_width=True, hide_index=True)
    st.caption("Showing " + str(len(display_df)) + " of " + str(len(df)) + " trades in last " + str(WINDOW_SECONDS) + "s")
else:
    st.info("Waiting for trade data...")

# ================= SIDEBAR =================
with st.sidebar:
    st.title("Controls")

    st.subheader("Position Manager")
    if st.session_state.in_position:
        entry = st.session_state.entry_price
        pos_type = st.session_state.position_type
        if entry > 0 and current_price > 0:
            if pos_type == "BUY":
                pnl = (current_price - entry) / entry * 100
            else:
                pnl = (entry - current_price) / entry * 100
            pnl_color = "#10b981" if pnl > 0 else "#ef4444"
            st.markdown(
                "<div style='padding:1rem; background:#1e293b; border-radius:8px; border-left:4px solid " + pnl_color + "'>"
                "<strong>Position:</strong> " + pos_type + "<br>"
                "<strong>Entry:</strong> $" + "{:,.2f}".format(entry) + "<br>"
                "<strong>Current:</strong> $" + "{:,.2f}".format(current_price) + "<br>"
                "<strong>PnL:</strong> <span style='color:" + pnl_color + "; font-size:1.3em; font-weight:700'>" + "{:+.2f}%".format(pnl) + "</span>"
                "</div>",
                unsafe_allow_html=True
            )
            if st.button("CLOSE POSITION", use_container_width=True):
                if entry > 0 and current_price > 0:
                    if pos_type == "BUY":
                        closed_pnl = (current_price - entry) / entry * 100
                    else:
                        closed_pnl = (entry - current_price) / entry * 100
                    send_telegram_alert("Position closed\nType: " + pos_type + "\nPnL: " + "{:+.2f}%".format(closed_pnl))
                st.session_state.in_position = False
                st.session_state.position_type = None
                st.session_state.entry_price = 0.0
                st.session_state.entry_time = None
                st.session_state.prev_exit_triggered = False
                st.rerun()
    else:
        st.info("No active position")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("LONG", use_container_width=True):
                st.session_state.in_position = True
                st.session_state.position_type = "BUY"
                st.session_state.entry_price = current_price
                st.session_state.entry_time = datetime.now()
                st.session_state.prev_exit_triggered = False
                st.rerun()
        with c2:
            if st.button("SHORT", use_container_width=True):
                st.session_state.in_position = True
                st.session_state.position_type = "SELL"
                st.session_state.entry_price = current_price
                st.session_state.entry_time = datetime.now()
                st.session_state.prev_exit_triggered = False
                st.rerun()

    st.markdown("---")
    st.subheader("Alert Settings")
    st.session_state.sound_enabled = st.checkbox("Sound Alerts", value=st.session_state.sound_enabled)
    st.session_state.exit_alerts_enabled = st.checkbox("Exit Alerts", value=st.session_state.exit_alerts_enabled)
    if st.session_state.exit_alerts_enabled:
        st.session_state.exit_loss_pct = st.slider("Stop Loss %", 0.1, 2.0, st.session_state.exit_loss_pct, 0.1)
        st.session_state.exit_profit_pct = st.slider("Take Profit %", 0.5, 5.0, st.session_state.exit_profit_pct, 0.1)
    st.session_state.telegram_enabled = st.checkbox("Telegram Alerts", value=st.session_state.telegram_enabled)
    if st.session_state.telegram_enabled:
        st.session_state.telegram_token = st.text_input("Bot Token", type="password", value=st.session_state.get("telegram_token", ""))
        st.session_state.telegram_chat_id = st.text_input("Chat ID", value=st.session_state.get("telegram_chat_id", ""))
        if st.session_state.telegram_token and st.session_state.telegram_chat_id:
            st.success("Telegram ready")
        else:
            st.warning("Enter token and Chat ID")

    st.markdown("---")
    st.subheader("Detection Tuning")
    new_vol = st.slider("Volume Threshold (sigma)", 1.0, 5.0, float(st.session_state.enhanced_detector.volume_spike_threshold), 0.1)
    st.session_state.enhanced_detector.volume_spike_threshold = new_vol
    new_delta = st.slider("Min Delta Ratio", 0.3, 0.9, float(st.session_state.enhanced_detector.delta_concentration), 0.05)
    st.session_state.enhanced_detector.delta_concentration = new_delta

    st.markdown("---")
    st.subheader("System Info")
    st.write("Pandas: " + pd.__version__)
    st.write("Streamlit: " + st.__version__)
    st.write("Python: " + sys.version.split()[0])
    st.write("Trades cached: " + str(len(st.session_state.trade_history)))
    st.write("Time: " + datetime.now().strftime("%H:%M:%S"))
    st.write("LGP: " + str(lgp) + "%")
    st.write("Confidence: " + str(aggression_score) + "%")

# ================= FOOTER =================
st.markdown("---")
status = "LIQUIDITY GRAB" if lgp >= 70 else "CLEAN SIGNAL" if aggression_score >= 80 else "WATCHING" if lgp >= 40 else "NORMAL"
st.caption("v5.0 FULLY OPTIMIZED | " + status + " | " + datetime.now().strftime("%H:%M:%S") + " | LGP: " + str(lgp) + "% | Confidence: " + str(aggression_score) + "%")

time.sleep(1)
st.rerun()