import streamlit as st
import streamlit.components.v1
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
from collections import deque

# Only create detector ONCE - preserve history across refreshes
if "enhanced_detector" not in st.session_state:
    st.session_state.enhanced_detector = CleanAggressiveMoveDetector()

d = st.session_state.enhanced_detector

# Safety patches only - never wipe the detector
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
if not hasattr(d, "volume_history"):
    d.volume_history = deque(maxlen=30)
if not hasattr(d, "delta_history"):
    d.delta_history = deque(maxlen=30)
if not hasattr(d, "price_history"):
    d.price_history = deque(maxlen=30)
if not hasattr(d, "aggression_history"):
    d.aggression_history = deque(maxlen=20)
# =================================================================================

from binance_futures_feed import trade_queue, mark_price, start_binance_feed, order_book, open_interest, htf_candles

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
    "last_buy_alert_time": 0.0,
    "last_sell_alert_time": 0.0,
    "last_lgp_alert_time": 0.0,
    "alert_repeat_interval": 30,
    "buy_signal_start": 0.0,
    "sell_signal_start": 0.0,
    "buy_signal_duration": 0.0,
    "sell_signal_duration": 0.0,
    "confidence_history_short": [],
    "auto_refresh": True,
    "refresh_interval": 1,
    "refresh_count": 0,
    "last_signal_time_display": "Never",
    "uptime_start": time.time(),
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
cutoff_60 = now - 60  # full 60s for MTF

if trade_queue:
    full_df = pd.DataFrame(list(trade_queue))
    full_df = full_df.sort_values("time", ascending=False)
    full_df["value"] = full_df["price"] * full_df["qty"]
    # 10s slice for display and primary detection
    df = full_df[full_df["time"] >= cutoff].copy()
    # 60s slice passed to detector for MTF windows
    df_mtf = full_df[full_df["time"] >= cutoff_60].copy()
    st.session_state.trade_history.extend(df.to_dict("records"))
    st.session_state.trade_history = st.session_state.trade_history[-2000:]
else:
    df = pd.DataFrame(columns=["time", "side", "price", "qty", "value"])
    df_mtf = pd.DataFrame(columns=["time", "side", "price", "qty", "value"])

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

htf_1h = htf_candles.get("trend_1h", "NEUTRAL")
htf_4h = htf_candles.get("trend_4h", "NEUTRAL")
oi_chg = open_interest.get("change_pct", 0.0)
oi_spk = open_interest.get("spike", False)
ob_imb = order_book.get("imbalance", 0.0)

# Safe call - works with both old and new enhanced_detection.py
import inspect
_detect_fn = st.session_state.enhanced_detector.detect_clean_aggressive_move
_detect_params = inspect.signature(_detect_fn).parameters
_detect_kwargs = dict(df=df_mtf, window_seconds=WINDOW_SECONDS, current_time=time.time())
if "htf_trend_1h" in _detect_params:
    _detect_kwargs["htf_trend_1h"] = htf_1h
    _detect_kwargs["htf_trend_4h"] = htf_4h
if "oi_change_pct" in _detect_params:
    _detect_kwargs["oi_change_pct"] = oi_chg
    _detect_kwargs["oi_spike"] = oi_spk
if "ob_imbalance" in _detect_params:
    _detect_kwargs["ob_imbalance"] = ob_imb
aggressive_data = _detect_fn(**_detect_kwargs)

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
raw_buy = aggressive_data.get("raw_buy", False)
raw_sell = aggressive_data.get("raw_sell", False)
mtf_confirmed = aggressive_data.get("mtf_confirmed", False)
mtf_score = aggressive_data.get("mtf_score", 0)
mtf_details = aggressive_data.get("mtf_details", {})
trend_direction = aggressive_data.get("trend_direction", "NEUTRAL")
trend_strength = aggressive_data.get("trend_strength", 0)
trend_allows_buy = aggressive_data.get("trend_allows_buy", True)
trend_allows_sell = aggressive_data.get("trend_allows_sell", True)
trend_reason_buy = aggressive_data.get("trend_reason_buy", "")
trend_reason_sell = aggressive_data.get("trend_reason_sell", "")
htf_trend_1h = aggressive_data.get("htf_trend_1h", "NEUTRAL")
htf_trend_4h = aggressive_data.get("htf_trend_4h", "NEUTRAL")
short_trend = aggressive_data.get("short_trend", "NEUTRAL")
oi_change_pct = aggressive_data.get("oi_change_pct", 0.0)
oi_spike = aggressive_data.get("oi_spike", False)
ob_imbalance_pct = aggressive_data.get("ob_imbalance_pct", 0.0)

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
    try:
        token = st.session_state.get("telegram_token", "").strip()
        chat_id = st.session_state.get("telegram_chat_id", "").strip()
        enabled = st.session_state.get("telegram_enabled", False)
        if not enabled or not token or not chat_id:
            return False
        if not chat_id.lstrip("-").isdigit():
            return False
        url = "https://api.telegram.org/bot" + token + "/sendMessage"
        # Try with markdown first
        resp = requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)
        if resp.status_code == 200:
            return True
        # Fallback - plain text (handles markdown parse errors)
        resp2 = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
        return resp2.status_code == 200
    except Exception as e:
        print("Telegram error: " + str(e))
        return False

def play_sound(sound_type="beep"):
    """Play sound using base64 encoded WAV via st.audio - works in Streamlit"""
    if not st.session_state.get("sound_enabled", True):
        return
    import struct
    import wave
    import io
    import base64

    def make_wav(freqs_durs):
        sample_rate = 22050
        frames = b""
        for freq, dur in freqs_durs:
            num_samples = int(sample_rate * dur)
            for i in range(num_samples):
                val = int(32767 * 0.4 * np.sin(2 * np.pi * freq * i / sample_rate))
                frames += struct.pack("<h", val)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(frames)
        buf.seek(0)
        return buf

    if sound_type == "buy":
        wav = make_wav([(660, 0.15), (880, 0.2)])
    elif sound_type == "sell":
        wav = make_wav([(880, 0.15), (550, 0.2)])
    elif sound_type == "warn":
        wav = make_wav([(440, 0.1), (440, 0.1), (440, 0.1)])
    else:
        wav = make_wav([(880, 0.2)])

    b64 = base64.b64encode(wav.read()).decode()
    html = (
        "<audio autoplay style='display:none'>"
        "<source src='data:audio/wav;base64," + b64 + "' type='audio/wav'>"
        "</audio>"
    )
    st.components.v1.html(html, height=0)


def show_toast(message):
    try:
        st.toast(message)
    except Exception:
        pass

def fire_alert(title, message, sound_type="beep"):
    if st.session_state.get("sound_enabled", True):
        play_sound(sound_type)
        show_toast(title)
    send_telegram_alert(message)

# ================= FIRE ALERTS (REPEAT EVERY 30s WHILE ACTIVE) =================
REPEAT_INTERVAL = st.session_state.get("alert_repeat_interval", 30)

# BUY Signal - fire on first trigger, then repeat every 30s while still active
if aggressive_buy:
    if not st.session_state.prev_aggressive_buy:
        st.session_state.buy_signal_start = now
    time_since_last = now - st.session_state.get("last_buy_alert_time", 0.0)
    should_alert = (not st.session_state.prev_aggressive_buy) or (time_since_last >= REPEAT_INTERVAL)
    if should_alert:
        duration_active = int(now - st.session_state.get("buy_signal_start", now))
        repeat_tag = "" if not st.session_state.prev_aggressive_buy else " [STILL ACTIVE " + str(duration_active) + "s]"
        msg = "BUY SIGNAL" + repeat_tag + "\nTime: " + current_time_str + "\nPrice: $" + "{:,.2f}".format(current_price) + "\nConfidence: " + str(aggression_score) + "%\nVolume: " + "{:.1f}".format(volume_zscore) + " sigma\nDelta: " + "{:.1%}".format(delta_ratio)
        fire_alert("BUY Signal " + str(aggression_score) + "%" + repeat_tag, msg, "buy")
        st.session_state.last_buy_alert_time = now
        st.session_state.prev_aggressive_buy = True
else:
    if st.session_state.prev_aggressive_buy:
        duration = int(now - st.session_state.get("buy_signal_start", now))
        st.session_state.buy_signal_duration = duration
        end_msg = "BUY SIGNAL ENDED\nTime: " + current_time_str + "\nPrice: $" + "{:,.2f}".format(current_price) + "\nDuration: " + str(duration) + "s\nConfidence dropped below threshold"
        fire_alert("BUY Signal ENDED (lasted " + str(duration) + "s)", end_msg, "warn")
    st.session_state.prev_aggressive_buy = False
    st.session_state.last_buy_alert_time = 0.0
    st.session_state.buy_signal_start = 0.0

# SELL Signal - fire on first trigger, then repeat every 30s while still active
if aggressive_sell:
    if not st.session_state.prev_aggressive_sell:
        st.session_state.sell_signal_start = now
    time_since_last = now - st.session_state.get("last_sell_alert_time", 0.0)
    should_alert = (not st.session_state.prev_aggressive_sell) or (time_since_last >= REPEAT_INTERVAL)
    if should_alert:
        duration_active = int(now - st.session_state.get("sell_signal_start", now))
        repeat_tag = "" if not st.session_state.prev_aggressive_sell else " [STILL ACTIVE " + str(duration_active) + "s]"
        msg = "SELL SIGNAL" + repeat_tag + "\nTime: " + current_time_str + "\nPrice: $" + "{:,.2f}".format(current_price) + "\nConfidence: " + str(aggression_score) + "%\nVolume: " + "{:.1f}".format(volume_zscore) + " sigma\nDelta: " + "{:.1%}".format(delta_ratio)
        fire_alert("SELL Signal " + str(aggression_score) + "%" + repeat_tag, msg, "sell")
        st.session_state.last_sell_alert_time = now
        st.session_state.prev_aggressive_sell = True
else:
    if st.session_state.prev_aggressive_sell:
        duration = int(now - st.session_state.get("sell_signal_start", now))
        st.session_state.sell_signal_duration = duration
        end_msg = "SELL SIGNAL ENDED\nTime: " + current_time_str + "\nPrice: $" + "{:,.2f}".format(current_price) + "\nDuration: " + str(duration) + "s\nConfidence dropped below threshold"
        fire_alert("SELL Signal ENDED (lasted " + str(duration) + "s)", end_msg, "warn")
    st.session_state.prev_aggressive_sell = False
    st.session_state.last_sell_alert_time = 0.0
    st.session_state.sell_signal_start = 0.0

# LGP Alert - fire on first trigger, then repeat every 30s while still active
if lgp >= 70:
    time_since_last = now - st.session_state.get("last_lgp_alert_time", 0.0)
    should_alert = (not st.session_state.prev_lgp_high) or (time_since_last >= REPEAT_INTERVAL)
    if should_alert:
        repeat_tag = "" if not st.session_state.prev_lgp_high else " [STILL ACTIVE " + str(int(time_since_last)) + "s]"
        msg = "LIQUIDITY GRAB DETECTED" + repeat_tag + "\nTime: " + current_time_str + "\nPrice: $" + "{:,.2f}".format(current_price) + "\nLGP Score: " + str(lgp) + "%\nSignals: " + ", ".join(lgp_signals)
        fire_alert("Liquidity Grab " + str(lgp) + "%" + repeat_tag, msg, "warn")
        st.session_state.last_lgp_alert_time = now
        st.session_state.prev_lgp_high = True
else:
    st.session_state.prev_lgp_high = False
    st.session_state.last_lgp_alert_time = 0.0

if st.session_state.get("exit_alerts_enabled", True) and st.session_state.in_position:
    entry = st.session_state.entry_price
    pos_type = st.session_state.position_type
    if entry > 0 and current_price > 0:
        if pos_type == "BUY":
            pnl_pct = (current_price - entry) / entry * 100
        else:
            pnl_pct = (entry - current_price) / entry * 100
        loss_pct = st.session_state.get("exit_loss_pct", 0.5)
        profit_pct = st.session_state.get("exit_profit_pct", 1.0)
        if pnl_pct <= -loss_pct:
            if not st.session_state.prev_exit_triggered:
                msg = "STOP LOSS HIT\nPosition: " + pos_type + "\nEntry: $" + "{:,.2f}".format(entry) + "\nCurrent: $" + "{:,.2f}".format(current_price) + "\nLoss: " + "{:.2f}%".format(pnl_pct)
                fire_alert("STOP LOSS " + "{:.2f}%".format(pnl_pct), msg, "sell")
                st.session_state.prev_exit_triggered = True
                st.error("STOP LOSS HIT: " + "{:.2f}%".format(pnl_pct))
        elif pnl_pct >= profit_pct:
            if not st.session_state.prev_exit_triggered:
                msg = "TAKE PROFIT HIT\nPosition: " + pos_type + "\nEntry: $" + "{:,.2f}".format(entry) + "\nCurrent: $" + "{:,.2f}".format(current_price) + "\nProfit: +" + "{:.2f}%".format(pnl_pct)
                fire_alert("TAKE PROFIT +" + "{:.2f}%".format(pnl_pct), msg, "buy")
                st.session_state.prev_exit_triggered = True
                st.success("TAKE PROFIT HIT: +" + "{:.2f}%".format(pnl_pct))
        elif (pos_type == "BUY" and aggressive_sell) or (pos_type == "SELL" and aggressive_buy):
            if not st.session_state.prev_exit_triggered:
                msg = "SIGNAL REVERSAL\nPosition: " + pos_type + "\nCurrent PnL: " + "{:.2f}%".format(pnl_pct)
                fire_alert("Signal Reversal", msg, "warn")
                st.session_state.prev_exit_triggered = True
                st.warning("SIGNAL REVERSAL - Consider exiting")
        else:
            st.session_state.prev_exit_triggered = False

if aggressive_buy and aggression_score >= 75 and not st.session_state.in_position:
    st.session_state.in_position = True
    st.session_state.position_type = "BUY"
    st.session_state.entry_price = current_price
    st.session_state.entry_time = datetime.now()
    st.session_state.prev_exit_triggered = False
    send_telegram_alert("AUTO LONG OPENED\nPrice: $" + "{:,.2f}".format(current_price) + "\nConfidence: " + str(aggression_score) + "%")

elif aggressive_sell and aggression_score >= 75 and not st.session_state.in_position:
    st.session_state.in_position = True
    st.session_state.position_type = "SELL"
    st.session_state.entry_price = current_price
    st.session_state.entry_time = datetime.now()
    st.session_state.prev_exit_triggered = False
    send_telegram_alert("AUTO SHORT OPENED\nPrice: $" + "{:,.2f}".format(current_price) + "\nConfidence: " + str(aggression_score) + "%")


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
# Track confidence trend for fade detection
conf_short = st.session_state.get("confidence_history_short", [])
conf_short.append(aggression_score)
conf_short = conf_short[-5:]
st.session_state.confidence_history_short = conf_short

# Detect fading momentum - confidence dropping over last 5 readings
is_fading = False
fade_pct = 0
if len(conf_short) >= 3:
    recent_avg = sum(conf_short[-2:]) / 2
    older_avg = sum(conf_short[:2]) / 2
    if older_avg > 0:
        fade_pct = int(((older_avg - recent_avg) / older_avg) * 100)
        is_fading = fade_pct >= 15 and (st.session_state.prev_aggressive_buy or st.session_state.prev_aggressive_sell)

# ================= HIGHER TIMEFRAME TREND ==================
st.markdown("---")
st.subheader("Multi-Timeframe Trend Analysis")
htf_cols = st.columns(5)
with htf_cols[0]:
    t4h = htf_candles.get("trend_4h", "NEUTRAL")
    st.metric("4H Trend (Primary)", t4h, "EMA9: $" + str(htf_candles.get("ema9_4h", 0)))
with htf_cols[1]:
    t1h = htf_candles.get("trend_1h", "NEUTRAL")
    st.metric("1H Trend (Confirm)", t1h, "EMA9: $" + str(htf_candles.get("ema9_1h", 0)))
with htf_cols[2]:
    st.metric("10s Trend (Short)", short_trend)
with htf_cols[3]:
    votes = sum([t4h == trend_direction, t1h == trend_direction, short_trend == trend_direction])
    st.metric("Combined Trend", trend_direction, str(votes) + "/3 aligned | " + str(trend_strength) + "% strength")
with htf_cols[4]:
    buy_ok = "ALLOWED" if trend_allows_buy else "BLOCKED"
    sell_ok = "ALLOWED" if trend_allows_sell else "BLOCKED"
    st.metric("Long/Short Filter", "L: " + buy_ok, "S: " + sell_ok)

htf_age = time.time() - htf_candles.get("last_update", 0)
if htf_age > 350:
    st.warning("HTF candles loading... (updates every 5 min). Short-term EMA active.")
else:
    st.caption("HTF updated " + str(int(htf_age)) + "s ago | 4H: " + str(htf_candles.get("ema9_4h",0)) + " vs " + str(htf_candles.get("ema21_4h",0)))

# ================= ORDER BOOK IMBALANCE ==================
st.markdown("---")
st.subheader("Order Book Imbalance")
ob = order_book
ob_cols = st.columns(5)
imb_pct = ob.get("imbalance_pct", 0)
imb_direction = "BID HEAVY" if imb_pct > 10 else "ASK HEAVY" if imb_pct < -10 else "BALANCED"
imb_color = "green" if imb_pct > 10 else "red" if imb_pct < -10 else "normal"
with ob_cols[0]:
    st.metric("Imbalance", str(imb_pct) + "%", imb_direction)
with ob_cols[1]:
    st.metric("Bid Volume", str(ob.get("bid_volume", 0)) + " BTC")
with ob_cols[2]:
    st.metric("Ask Volume", str(ob.get("ask_volume", 0)) + " BTC")
with ob_cols[3]:
    best_bid = ob["bids"][0][0] if ob.get("bids") else 0
    st.metric("Best Bid", "$" + "{:,.2f}".format(best_bid) if best_bid else "Loading")
with ob_cols[4]:
    best_ask = ob["asks"][0][0] if ob.get("asks") else 0
    st.metric("Best Ask", "$" + "{:,.2f}".format(best_ask) if best_ask else "Loading")

if abs(imb_pct) >= 20:
    imb_msg = "STRONG BID pressure - buyers dominating order book" if imb_pct > 0 else "STRONG ASK pressure - sellers dominating order book"
    st.markdown("<div class='signal-card " + ("signal-long" if imb_pct > 0 else "signal-short") + "'>" + imb_msg + " (" + str(imb_pct) + "%)" + "</div>", unsafe_allow_html=True)

# ================= OPEN INTEREST ==================
st.markdown("---")
st.subheader("Open Interest")
oi = open_interest
oi_cols = st.columns(4)
with oi_cols[0]:
    oi_val = oi.get("value", 0)
    st.metric("Open Interest", "{:,.0f} BTC".format(oi_val) if oi_val > 0 else "Loading")
with oi_cols[1]:
    oi_chg = oi.get("change_pct", 0)
    st.metric("10s Change", "{:+.4f}%".format(oi_chg), oi.get("direction", "FLAT"))
with oi_cols[2]:
    spike = oi.get("spike", False)
    st.metric("Spike Detected", "YES" if spike else "No", ">=0.5% move" if spike else "Normal")
with oi_cols[3]:
    oi_dir = oi.get("direction", "FLAT")
    if oi_dir == "UP" and current_price > 0:
        oi_signal = "BULLISH (OI+Price up = longs adding)"
    elif oi_dir == "DOWN" and current_price > 0:
        oi_signal = "SHORT COVERING (OI down = closing)"
    else:
        oi_signal = "Monitoring"
    st.metric("OI Signal", oi_signal[:20])

if oi.get("spike"):
    spike_dir = oi.get("direction", "FLAT")
    spike_msg = ("OI SPIKE UP - New money entering market. " + ("Confirms LONG if price rising" if current_price > 0 else "")) if spike_dir == "UP" else "OI SPIKE DOWN - Positions closing. Potential reversal or squeeze."
    spike_class = "signal-long" if spike_dir == "UP" else "signal-warning"
    st.markdown("<div class='signal-card " + spike_class + "'><strong>OI SPIKE: " + spike_msg + "</strong></div>", unsafe_allow_html=True)

# ================= MULTI-TIMEFRAME PANEL ==================
st.markdown("---")
st.subheader("Multi-Timeframe Confirmation")
mtf_cols = st.columns(4)
with mtf_cols[0]:
    tf_10 = mtf_details.get("10s", "WAITING")
    st.metric("10s (Primary)", tf_10)
with mtf_cols[1]:
    tf_30 = mtf_details.get("30s", "WARMING")
    st.metric("30s (Confirm)", tf_30)
with mtf_cols[2]:
    tf_60 = mtf_details.get("60s", "WARMING")
    st.metric("60s (Confirm)", tf_60)
with mtf_cols[3]:
    st.metric("MTF Score", str(mtf_score) + "/3", "CONFIRMED" if mtf_confirmed else "NOT CONFIRMED")

if raw_buy and not mtf_confirmed:
    st.warning("Raw BUY signal detected but MTF not confirmed yet - waiting for 30s/60s agreement")
if raw_sell and not mtf_confirmed:
    st.warning("Raw SELL signal detected but MTF not confirmed yet - waiting for 30s/60s agreement")

st.markdown("---")
st.subheader("Long/Short Signals")
sig_cols = st.columns(2)

with sig_cols[0]:
    if aggressive_buy:
        funding_ok = current_funding < 0
        buy_start = st.session_state.get("buy_signal_start", now)
        time_active = int(now - buy_start) if buy_start > 0 else 0
        next_alert = max(0, st.session_state.get("alert_repeat_interval", 30) - int(now - st.session_state.get("last_buy_alert_time", now)))
        repeat_info = "Next alert in: " + str(next_alert) + "s" if time_active > 0 else "Just fired!"
        card_class = "signal-warning" if is_fading else "signal-long"
        fade_warning = " - MOMENTUM FADING " + str(fade_pct) + "%" if is_fading else ""
        st.markdown(
            "<div class='signal-card " + card_class + "'>"
            "<h3>LONG SIGNAL - ACTIVE" + fade_warning + "</h3>"
            "<p>Confidence: <strong>" + str(aggression_score) + "%</strong>"
            + (" (dropping)" if is_fading else "") + "</p>"
            "<p>Vol: " + "{:.1f}s".format(volume_zscore) +
            " | Delta: " + "{:.1%}".format(delta_ratio) +
            " | Whales: " + "{:.1%}".format(whale_concentration) + "</p>"
            "<p>Momentum: " + ("Accelerating" if has_acceleration else "Spike") +
            " | Funding: " + ("Favorable" if funding_ok else "Caution") + "</p>"
            "<p>Active: " + str(time_active) + "s | " + repeat_info + "</p>"
            "<p><strong>" + ("Consider exiting LONG" if is_fading else "Consider LONG entry") + "</strong></p>"
            "</div>",
            unsafe_allow_html=True
        )
    elif st.session_state.get("buy_signal_duration", 0) > 0 and (now - st.session_state.get("buy_signal_start", 0)) < 10:
        duration = st.session_state.buy_signal_duration
        st.markdown(
            "<div class='signal-card signal-warning'>"
            "<h3>LONG SIGNAL ENDED</h3>"
            "<p>Signal lasted: <strong>" + str(duration) + "s</strong></p>"
            "<p>Momentum exhausted - conditions no longer met</p>"
            "<p>Monitor for re-entry or reversal</p>"
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
        sell_start = st.session_state.get("sell_signal_start", now)
        time_active = int(now - sell_start) if sell_start > 0 else 0
        next_alert = max(0, st.session_state.get("alert_repeat_interval", 30) - int(now - st.session_state.get("last_sell_alert_time", now)))
        repeat_info = "Next alert in: " + str(next_alert) + "s" if time_active > 0 else "Just fired!"
        card_class = "signal-warning" if is_fading else "signal-short"
        fade_warning = " - MOMENTUM FADING " + str(fade_pct) + "%" if is_fading else ""
        st.markdown(
            "<div class='signal-card " + card_class + "'>"
            "<h3>SHORT SIGNAL - ACTIVE" + fade_warning + "</h3>"
            "<p>Confidence: <strong>" + str(aggression_score) + "%</strong>"
            + (" (dropping)" if is_fading else "") + "</p>"
            "<p>Vol: " + "{:.1f}s".format(volume_zscore) +
            " | Delta: " + "{:.1%}".format(delta_ratio) +
            " | Whales: " + "{:.1%}".format(whale_concentration) + "</p>"
            "<p>Momentum: " + ("Accelerating" if has_acceleration else "Spike") +
            " | Funding: " + ("Favorable" if funding_ok else "Caution") + "</p>"
            "<p>Active: " + str(time_active) + "s | " + repeat_info + "</p>"
            "<p><strong>" + ("Consider exiting SHORT" if is_fading else "Consider SHORT entry") + "</strong></p>"
            "</div>",
            unsafe_allow_html=True
        )
    elif st.session_state.get("sell_signal_duration", 0) > 0 and (now - st.session_state.get("sell_signal_start", 0)) < 10:
        duration = st.session_state.sell_signal_duration
        st.markdown(
            "<div class='signal-card signal-warning'>"
            "<h3>SHORT SIGNAL ENDED</h3>"
            "<p>Signal lasted: <strong>" + str(duration) + "s</strong></p>"
            "<p>Momentum exhausted - conditions no longer met</p>"
            "<p>Monitor for re-entry or reversal</p>"
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
st.subheader("Signal Debug Panel")
debug_cols = st.columns(2)

with debug_cols[0]:
    st.write("*BUY Signal Conditions:*")
    d = st.session_state.enhanced_detector
    c1 = volume_zscore >= d.volume_spike_threshold
    c2 = delta_ratio >= d.delta_concentration
    c3 = delta > 0
    c4 = price_change_pct >= d.min_price_move
    c5 = trades_per_sec >= d.min_trades_per_sec
    c6 = aggression_score >= 50

    st.markdown(("green" if c1 else "red") + " Vol Z-Score: " + "{:.2f}".format(volume_zscore) + " (need " + str(d.volume_spike_threshold) + ")")
    st.markdown(("green" if c2 else "red") + " Delta Ratio: " + "{:.2%}".format(delta_ratio) + " (need " + "{:.0%}".format(d.delta_concentration) + ")")
    st.markdown(("green" if c3 else "red") + " Delta Direction: " + "{:+.4f}".format(delta) + " (need > 0)")
    st.markdown(("green" if c4 else "red") + " Price Change: " + "{:.3f}%".format(price_change_pct) + " (need +" + str(d.min_price_move) + "%)")
    st.markdown(("green" if c5 else "red") + " Trades/Sec: " + "{:.1f}".format(trades_per_sec) + " (need " + str(d.min_trades_per_sec) + ")")
    st.markdown(("green" if c6 else "red") + " Confidence: " + str(aggression_score) + "% (need 50%)")

    passing_buy = sum([c1, c2, c3, c4, c5, c6])
    if passing_buy == 6:
        st.success("BUY SIGNAL FIRING!")
    else:
        st.warning("BUY blocked: " + str(passing_buy) + "/6 conditions met")

with debug_cols[1]:
    st.write("*SELL Signal Conditions:*")
    s1 = volume_zscore >= d.volume_spike_threshold
    s2 = delta_ratio >= d.delta_concentration
    s3 = delta < 0
    s4 = price_change_pct <= -d.min_price_move
    s5 = trades_per_sec >= d.min_trades_per_sec
    s6 = aggression_score >= 50

    st.markdown(("green" if s1 else "red") + " Vol Z-Score: " + "{:.2f}".format(volume_zscore) + " (need " + str(d.volume_spike_threshold) + ")")
    st.markdown(("green" if s2 else "red") + " Delta Ratio: " + "{:.2%}".format(delta_ratio) + " (need " + "{:.0%}".format(d.delta_concentration) + ")")
    st.markdown(("green" if s3 else "red") + " Delta Direction: " + "{:+.4f}".format(delta) + " (need < 0)")
    st.markdown(("green" if s4 else "red") + " Price Change: " + "{:.3f}%".format(price_change_pct) + " (need -" + str(d.min_price_move) + "%)")
    st.markdown(("green" if s5 else "red") + " Trades/Sec: " + "{:.1f}".format(trades_per_sec) + " (need " + str(d.min_trades_per_sec) + ")")
    st.markdown(("green" if s6 else "red") + " Confidence: " + str(aggression_score) + "% (need 50%)")

    passing_sell = sum([s1, s2, s3, s4, s5, s6])
    if passing_sell == 6:
        st.error("SELL SIGNAL FIRING!")
    else:
        st.warning("SELL blocked: " + str(passing_sell) + "/6 conditions met")

st.write("*History warmup:* " + str(len(d.volume_history)) + "/30 samples collected")
st.write("*Trades in window:* " + str(total_trades) + " | *Window:* " + str(WINDOW_SECONDS) + "s")

st.write("*Context Filters:*")
oi_ok_buy = not (oi_spike and open_interest.get("change_pct", 0) < 0)
ob_ok_buy = order_book.get("imbalance", 0) > -0.15
htf_ok_buy = trend_allows_buy
st.markdown(("green" if htf_ok_buy else "red") + " HTF Trend (LONG): " + ("ALLOWED - " + htf_trend_4h + " 4H + " + htf_trend_1h + " 1H" if htf_ok_buy else "BLOCKED - " + trend_reason_buy))
st.markdown(("green" if ob_ok_buy else "red") + " Order Book (LONG): " + str(order_book.get("imbalance_pct", 0)) + "% imbalance " + ("(bids support)" if ob_ok_buy else "(asks dominating - caution)"))
st.markdown(("green" if oi_ok_buy else "orange") + " Open Interest: " + "{:+.4f}%".format(open_interest.get("change_pct", 0)) + (" SPIKE" if oi_spike else " normal") + (" - OI falling may weaken signal" if not oi_ok_buy else ""))
st.markdown(("green" if mtf_confirmed else "red") + " MTF Score: " + str(mtf_score) + "/3 - " + ("Confirmed across timeframes" if mtf_confirmed else "Not yet confirmed"))

# ================= WIN RATE TRACKER ==================
st.markdown("---")
st.subheader("Win Rate Tracker")
detector = st.session_state.enhanced_detector
stats = detector.get_win_rate_stats()

if stats["total"] == 0:
    st.info("No completed signals yet. Win rate tracking starts automatically once signals fire and resolve.")
else:
    wr_cols = st.columns(5)
    with wr_cols[0]:
        wr_color = "normal" if stats["win_rate"] >= 50 else "inverse"
        st.metric("Win Rate", str(stats["win_rate"]) + "%", str(stats["wins"]) + "W / " + str(stats["losses"]) + "L")
    with wr_cols[1]:
        st.metric("Total Signals", str(stats["total"]), "Last 10: " + str(stats["recent_10_wr"]) + "%")
    with wr_cols[2]:
        st.metric("Avg Win", "+" + str(stats["avg_win_pct"]) + "%")
    with wr_cols[3]:
        st.metric("Avg Loss", str(stats["avg_loss_pct"]) + "%")
    with wr_cols[4]:
        exp_color = "+" if stats["expectancy"] > 0 else ""
        st.metric("Expectancy", exp_color + str(stats["expectancy"]) + "%", "Best: +" + str(stats["best_trade"]) + "%")

    # Signal log table
    log = detector.get_signal_log()
    if log:
        st.write("*Recent Signals:*")
        rows = []
        for s in log:
            rows.append({
                "Time": s["timestamp"].strftime("%H:%M:%S") if hasattr(s["timestamp"], "strftime") else str(s["timestamp"]),
                "Type": s["type"],
                "Entry": "$" + "{:,.2f}".format(s["entry_price"]),
                "Exit": "$" + "{:,.2f}".format(s["exit_price"]) if s["exit_price"] else "Open",
                "PnL": (("+" if s["pnl_pct"] and s["pnl_pct"] > 0 else "") + str(s["pnl_pct"]) + "%") if s["pnl_pct"] is not None else "-",
                "Outcome": s["outcome"] if s["outcome"] else "Open",
                "Duration": str(s["duration"]) + "s" if s["duration"] else "-",
                "Confidence": str(s["confidence"]) + "%",
            })
        log_df = pd.DataFrame(rows)
        st.dataframe(log_df, use_container_width=True, hide_index=True)

    # Manual resolve buttons
    if detector.open_signal is not None:
        os = detector.open_signal
        st.write("*Open Signal:* " + os["type"] + " @ $" + "{:,.2f}".format(os["entry_price"]) + " | Confidence: " + str(os["confidence"]) + "%")
        rc1, rc2, rc3 = st.columns(3)
        with rc1:
            if st.button("Mark WIN", use_container_width=True):
                detector.resolve_open_signal(current_price, "WIN")
                st.rerun()
        with rc2:
            if st.button("Mark LOSS", use_container_width=True):
                detector.resolve_open_signal(current_price, "LOSS")
                st.rerun()
        with rc3:
            if st.button("Auto Resolve", use_container_width=True):
                detector.resolve_open_signal(current_price, "AUTO")
                st.rerun()

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
    st.session_state.alert_repeat_interval = st.slider("Repeat Interval (sec)", 10, 120, st.session_state.get("alert_repeat_interval", 30), 5)
    st.caption("Alerts repeat every " + str(st.session_state.get("alert_repeat_interval", 30)) + "s while signal is active")
    st.markdown("---")
    st.subheader("Auto Refresh")
    st.session_state.auto_refresh = st.toggle("Auto Refresh", value=st.session_state.get("auto_refresh", True))
    if st.session_state.auto_refresh:
        st.session_state.refresh_interval = st.select_slider(
            "Refresh Speed",
            options=[1, 2, 3, 5],
            value=st.session_state.get("refresh_interval", 1),
            format_func=lambda x: str(x) + "s"
        )
        st.caption("Refreshing every " + str(st.session_state.refresh_interval) + "s")
        if st.button("PAUSE", use_container_width=True):
            st.session_state.auto_refresh = False
            st.rerun()
    else:
        st.warning("Auto refresh PAUSED")
        if st.button("RESUME", use_container_width=True):
            st.session_state.auto_refresh = True
            st.rerun()
    if st.session_state.exit_alerts_enabled:
        st.session_state.exit_loss_pct = st.slider("Stop Loss %", 0.1, 2.0, st.session_state.exit_loss_pct, 0.1)
        st.session_state.exit_profit_pct = st.slider("Take Profit %", 0.5, 5.0, st.session_state.exit_profit_pct, 0.1)
    st.session_state.telegram_enabled = st.checkbox("Telegram Alerts", value=st.session_state.telegram_enabled)
    if st.session_state.telegram_enabled:
        st.session_state.telegram_token = st.text_input("Bot Token", type="password", value=st.session_state.get("telegram_token", ""), help="Get from @BotFather on Telegram")
        st.session_state.telegram_chat_id = st.text_input("Chat ID", value=st.session_state.get("telegram_chat_id", ""), help="Must be a number. Click AUTO-FETCH to get it.")

        token = st.session_state.get("telegram_token", "").strip()
        chat_id = st.session_state.get("telegram_chat_id", "").strip()

        # AUTO-FETCH CHAT ID BUTTON
        if token:
            if st.button("AUTO-FETCH CHAT ID", use_container_width=True, help="Send any message to your bot first, then click this"):
                try:
                    url = "https://api.telegram.org/bot" + token + "/getUpdates"
                    resp = requests.get(url, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        updates = data.get("result", [])
                        if updates:
                            # Get the most recent chat ID
                            found_id = None
                            for update in reversed(updates):
                                msg = update.get("message") or update.get("channel_post") or {}
                                chat = msg.get("chat", {})
                                if chat.get("id"):
                                    found_id = str(chat["id"])
                                    chat_name = chat.get("first_name") or chat.get("title") or "Unknown"
                                    break
                            if found_id:
                                st.session_state.telegram_chat_id = found_id
                                st.success("Chat ID found: " + found_id + " (" + chat_name + ")")
                                st.rerun()
                            else:
                                st.warning("No messages found. Send a message to your bot first, then click again.")
                        else:
                            st.warning("No updates found. Open your bot in Telegram and send /start or any message, then click again.")
                    elif resp.status_code == 401:
                        st.error("Invalid Bot Token. Check token from @BotFather.")
                    else:
                        st.error("Failed: " + str(resp.status_code) + " - " + resp.text)
                except Exception as e:
                    st.error("Error: " + str(e))

        # STATUS + TEST
        if token and chat_id:
            # Validate chat_id is numeric
            if not chat_id.lstrip("-").isdigit():
                st.error("Chat ID must be a number (e.g. 1234567890). Got: " + chat_id)
            else:
                st.success("Telegram configured")
                if st.button("TEST TELEGRAM", use_container_width=True):
                    test_msg = "TRADING BOT - TEST ALERT\nDashboard is working!\nTime: " + datetime.now().strftime("%H:%M:%S") + "\nPrice: $" + "{:,.2f}".format(current_price) + "\nStatus: All systems operational"
                    try:
                        url = "https://api.telegram.org/bot" + token + "/sendMessage"
                        resp = requests.post(url, json={"chat_id": chat_id, "text": test_msg, "parse_mode": "Markdown"}, timeout=10)
                        if resp.status_code == 200:
                            st.success("Sent! Check your Telegram now.")
                        elif resp.status_code == 400:
                            err = resp.json().get("description", "")
                            if "chat not found" in err:
                                st.error("Chat not found. Open your bot in Telegram and send /start first.")
                            elif "parse" in err.lower():
                                # Retry without markdown
                                resp2 = requests.post(url, json={"chat_id": chat_id, "text": test_msg.replace("*","")}, timeout=10)
                                if resp2.status_code == 200:
                                    st.success("Sent! (plain text mode)")
                                else:
                                    st.error("Failed: " + str(resp2.status_code))
                            else:
                                st.error("Failed: " + err)
                        elif resp.status_code == 401:
                            st.error("Invalid token. Get a new one from @BotFather.")
                        else:
                            st.error("Failed: " + str(resp.status_code) + " - " + resp.text)
                    except Exception as e:
                        st.error("Error: " + str(e))
        elif token and not chat_id:
            st.info("Click AUTO-FETCH CHAT ID after sending a message to your bot")
        else:
            st.warning("Enter your Bot Token first")
    st.markdown("---")
    st.subheader("Test Alerts")
    tcol1, tcol2 = st.columns(2)
    with tcol1:
        if st.button("TEST SOUND", use_container_width=True):
            play_sound("buy")
            st.toast("Sound test fired!")
            st.success("If you heard a beep - sound works!")
    with tcol2:
        if st.button("TEST ALL", use_container_width=True):
            play_sound("warn")
            st.toast("Full alert test!")
            send_telegram_alert("TEST ALERT\nAll systems working!\nTime: " + datetime.now().strftime("%H:%M:%S"))
            st.success("Sound + Telegram test sent!")

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

# ================= AUTO REFRESH ENGINE =================
st.session_state.refresh_count = st.session_state.get("refresh_count", 0) + 1
uptime_secs = int(time.time() - st.session_state.get("uptime_start", time.time()))
uptime_hrs = uptime_secs // 3600
uptime_mins = (uptime_secs % 3600) // 60
uptime_str = str(uptime_hrs) + "h " + str(uptime_mins) + "m"

# Status bar at very bottom
st.markdown("---")
status_cols = st.columns(4)
with status_cols[0]:
    auto_on = st.session_state.get("auto_refresh", True)
    st.markdown("*Auto Refresh:* " + ("RUNNING" if auto_on else "PAUSED"))
with status_cols[1]:
    st.markdown("*Uptime:* " + uptime_str)
with status_cols[2]:
    st.markdown("*Refresh #:* " + str(st.session_state.refresh_count))
with status_cols[3]:
    warmup = len(st.session_state.enhanced_detector.volume_history)
    st.markdown("*Warmup:* " + str(warmup) + "/30 " + ("READY" if warmup >= 30 else "warming"))

# Auto refresh control in sidebar (add near top)
if st.session_state.get("auto_refresh", True):
    interval = st.session_state.get("refresh_interval", 1)
    time.sleep(interval)
    st.rerun()