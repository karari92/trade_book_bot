import streamlit as st
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
import sys
import requests  # for Telegram alerts
import base64

from binance_feed import trade_queue, start_binance_feed
from enhanced_detection import CleanAggressiveMoveDetector

# ================= CONFIG =================
WINDOW_SECONDS = 10
MAX_ROWS_DISPLAY = 30
WHALE_QTY = 0.5
MIN_DELTA_ABS = 0.05
STRUCTURE_LOOKBACK = 20
WHALE_SPEED_THRESHOLD = 6

# AGGRESSIVE MOVE DETECTION CONFIG
AGGRESSIVE_MOVE_THRESHOLD = 3.0
AGGRESSIVE_VOLUME_SPIKE = 5.0
AGGRESSIVE_PRICE_MOVE = 0.3
AGGRESSIVE_DELTA_RATIO = 0.7
PUMP_DUMP_THRESHOLD = 0.5

st.set_page_config(layout="wide", page_title="Liquidity Grab Pro Enhanced")
st.title("🎯 Liquidity Grab & Clean Aggressive Move Detection — v4.2 ALERTS")

# ================= CSS STYLING (unchanged) =================
st.markdown("""
<style>
    .big-font { font-size:28px !important; font-weight:700; }
    .metric-card { 
        padding: 1rem; 
        border-radius: 10px; 
        background: #1e1e2e;
        border-left: 5px solid;
        margin-bottom: 1rem;
    }
    .aggressive-buy { background: linear-gradient(135deg, #065f46 0%, #047857 100%); }
    .aggressive-sell { background: linear-gradient(135deg, #991b1b 0%, #dc2626 100%); }
    .buy-gradient { background: linear-gradient(135deg, #059669 0%, #10b981 100%); }
    .sell-gradient { background: linear-gradient(135deg, #dc2626 0%, #ef4444 100%); }
    .neutral-gradient { background: linear-gradient(135deg, #6b7280 0%, #9ca3af 100%); }
    .warning-gradient { background: linear-gradient(135deg, #d97706 0%, #f59e0b 100%); }
    .danger-gradient { background: linear-gradient(135deg, #7c2d12 0%, #dc2626 100%); }
    .lgp-high { background: linear-gradient(135deg, #7c3aed 0%, #8b5cf6 100%); }
    .lgp-medium { background: linear-gradient(135deg, #d97706 0%, #f59e0b 100%); }
    .lgp-low { background: linear-gradient(135deg, #059669 0%, #10b981 100%); }
    
    .signal-card {
        padding: 0.75rem;
        border-radius: 8px;
        margin: 0.5rem 0;
        border-left: 4px solid;
        font-weight: 600;
    }
    .signal-buy { background: rgba(16, 185, 129, 0.1); border-left-color: #10b981; }
    .signal-sell { background: rgba(239, 68, 68, 0.1); border-left-color: #ef4444; }
    .signal-warning { background: rgba(245, 158, 11, 0.1); border-left-color: #f59e0b; }
    .signal-danger { background: rgba(220, 38, 38, 0.1); border-left-color: #dc2626; }
    .signal-lgp { background: rgba(124, 58, 237, 0.1); border-left-color: #8b5cf6; }
    
    .lgp-meter {
        height: 20px;
        border-radius: 10px;
        margin: 10px 0;
        overflow: hidden;
        position: relative;
    }
    .lgp-fill {
        height: 100%;
        transition: width 0.5s ease;
    }
    .lgp-labels {
        display: flex;
        justify-content: space-between;
        margin-top: 5px;
        font-size: 12px;
        color: #9ca3af;
    }
    
    .flash-red {
        animation: flashRed 1s infinite;
    }
    .flash-green {
        animation: flashGreen 1s infinite;
    }
    .flash-purple {
        animation: flashPurple 1s infinite;
    }
    @keyframes flashRed {
        0%, 100% { background-color: rgba(220, 38, 38, 0.1); }
        50% { background-color: rgba(220, 38, 38, 0.3); }
    }
    @keyframes flashGreen {
        0%, 100% { background-color: rgba(16, 185, 129, 0.1); }
        50% { background-color: rgba(16, 185, 129, 0.3); }
    }
    @keyframes flashPurple {
        0%, 100% { background-color: rgba(124, 58, 237, 0.1); }
        50% { background-color: rgba(124, 58, 237, 0.3); }
    }
</style>
""", unsafe_allow_html=True)

# ================= START FEED =================
if "feed_started" not in st.session_state:
    start_binance_feed()
    st.session_state.feed_started = True
    st.session_state.trade_history = []
    st.session_state.lgp_history = []
    st.session_state.enhanced_detector = CleanAggressiveMoveDetector()

# ================= INITIALIZE SESSION STATE =================
defaults = {
    "last_speed": 0.0,
    "last_price": 0.0,
    "price_history": [],
    "delta_history": [],
    "volume_history": [],
    "speed_history": [],
    "lgp_history": [],
    "regime_history": [],
    "whale_volume_history": [],
    "retail_volume_history": [],
    "aggression_score_history": [],
    # Alert states
    "prev_lgp_high": False,
    "prev_aggressive_buy": False,
    "prev_aggressive_sell": False,
    "prev_confidence_high": False,
    "in_position": False,
    "position_type": None,
    "entry_price": 0.0,
    "entry_time": None,
    "prev_exit_triggered": False,
    "telegram_enabled": False,
    "sound_enabled": True,
    "exit_alerts_enabled": True,
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ================= LOAD & PROCESS DATA =================
now = time.time()
cutoff = now - WINDOW_SECONDS

if trade_queue:
    df = pd.DataFrame(list(trade_queue))
    df = df[df["time"] >= cutoff]
    df = df.sort_values("time", ascending=False)
    
    df["value"] = df["price"] * df["qty"]
    df["intensity"] = df["qty"] * df["price"]
    
    st.session_state.trade_history.extend(df.to_dict('records'))
    st.session_state.trade_history = st.session_state.trade_history[-2000:]
else:
    df = pd.DataFrame(columns=["time", "side", "price", "qty", "value", "intensity"])

# ================= LIQUIDITY GRAB PROBABILITY (LGP) CALCULATION =================
def calculate_lgp(df, structure_lookback=STRUCTURE_LOOKBACK):
    """Calculate Liquidity Grab Probability (0-100)"""
    if df.empty or len(df) < 5:
        return 0, [], False, False
    
    latest_price = df.iloc[0]["price"]
    
    structure_df = df.head(structure_lookback)
    range_high = structure_df["price"].max() if not structure_df.empty else latest_price
    range_low = structure_df["price"].min() if not structure_df.empty else latest_price
    
    sweep_high = latest_price > range_high * 1.001
    sweep_low = latest_price < range_low * 0.999
    
    total_volume = df["qty"].sum()
    buy_volume = df[df["side"] == "BUY"]["qty"].sum()
    sell_volume = df[df["side"] == "SELL"]["qty"].sum()
    delta = buy_volume - sell_volume
    
    failed_high = sweep_high and delta < 0
    failed_low = sweep_low and delta > 0
    
    whales = df[df["qty"] >= WHALE_QTY]
    whale_buy = whales.loc[whales["side"] == "BUY", "qty"].sum() if not whales.empty else 0
    whale_sell = whales.loc[whales["side"] == "SELL", "qty"].sum() if not whales.empty else 0
    whale_delta = whale_buy - whale_sell
    
    retail = df[df["qty"] < WHALE_QTY]
    retail_buy = retail.loc[retail["side"] == "BUY", "qty"].sum() if not retail.empty else 0
    retail_sell = retail.loc[retail["side"] == "SELL", "qty"].sum() if not retail.empty else 0
    retail_delta = retail_buy - retail_sell
    
    lgp = 0
    signals = []
    
    if failed_high or failed_low:
        lgp += 40
        signals.append("Failed Structure Break")
    
    bullish_divergence = delta <= -MIN_DELTA_ABS and latest_price > df.iloc[-1]["price"]
    bearish_divergence = delta >= MIN_DELTA_ABS and latest_price < df.iloc[-1]["price"]
    
    if bullish_divergence or bearish_divergence:
        lgp += 20
        signals.append("Price-Delta Divergence")
    
    bull_absorption = whale_buy > 0 and delta > 0 and latest_price <= df.iloc[-1]["price"]
    bear_absorption = whale_sell > 0 and delta < 0 and latest_price >= df.iloc[-1]["price"]
    
    if bull_absorption or bear_absorption:
        lgp += 20
        signals.append("Whale Absorption")
    
    trades_per_sec = len(df) / WINDOW_SECONDS if WINDOW_SECONDS > 0 else 0
    if trades_per_sec >= 5:
        lgp += 10
        signals.append("High Trade Speed")
    
    bull_wrd = retail_delta < 0 and whale_delta > 0 and latest_price > df.iloc[-1]["price"]
    bear_wrd = retail_delta > 0 and whale_delta < 0 and latest_price < df.iloc[-1]["price"]
    
    if bull_wrd or bear_wrd:
        lgp += 20
        signals.append("Whale-Retail Divergence")
    
    whale_ratio = len(whales) / len(df) if len(df) > 0 else 0
    if whale_ratio > 0.3:
        lgp += 10
        signals.append("High Whale %")
    
    lgp = min(lgp, 100)
    is_liquidity_grab = lgp >= 70 or failed_high or failed_low
    
    return lgp, signals, failed_high, failed_low

# ================= CALCULATE ALL METRICS =================
lgp, lgp_signals, failed_high, failed_low = calculate_lgp(df)

# Run ENHANCED aggressive move detection
aggressive_data = st.session_state.enhanced_detector.detect_clean_aggressive_move(
    df=df,
    window_seconds=WINDOW_SECONDS,
    current_time=time.time()
)

# Extract metrics
aggression_score = aggressive_data["confidence"]
metrics = aggressive_data.get("metrics", {})
volume_zscore = metrics.get("volume_zscore", 0)
delta_ratio = metrics.get("delta_ratio", 0)
price_change_pct = metrics.get("price_change_pct", 0)
trades_per_sec = metrics.get("trades_per_sec", 0)

# Update LGP history
st.session_state.lgp_history.append(lgp)
st.session_state.lgp_history = st.session_state.lgp_history[-50:]

# ================= CORE METRICS =================
total_trades = len(df)
total_volume = df["qty"].sum() if not df.empty else 0
total_value = df["value"].sum() if "value" in df.columns and not df.empty else 0

buy_df = df[df["side"] == "BUY"]
sell_df = df[df["side"] == "SELL"]

buy_volume = buy_df["qty"].sum() if not buy_df.empty else 0
sell_volume = sell_df["qty"].sum() if not sell_df.empty else 0
delta = buy_volume - sell_volume

whales = df[df["qty"] >= WHALE_QTY]
retail = df[df["qty"] < WHALE_QTY]

whale_count = len(whales)
retail_count = len(retail)
whale_ratio = whale_count / total_trades if total_trades > 0 else 0

whale_buy = whales.loc[whales["side"] == "BUY", "qty"].sum() if not whales.empty else 0
whale_sell = whales.loc[whales["side"] == "SELL", "qty"].sum() if not whales.empty else 0
whale_delta = whale_buy - whale_sell

retail_buy = retail.loc[retail["side"] == "BUY", "qty"].sum() if not retail.empty else 0
retail_sell = retail.loc[retail["side"] == "SELL", "qty"].sum() if not retail.empty else 0
retail_delta = retail_buy - retail_sell

# Fix: Ensure current_price is never None
current_price = df.iloc[0]["price"] if not df.empty else st.session_state.get("last_price", 0.0)
if current_price is None or current_price == 0:
    current_price = 0.0

if not df.empty and current_price > 0:
    st.session_state.last_price = current_price

price_change = 0.0
price_change_pct_display = 0.0
if st.session_state.price_history and current_price > 0:
    old_price = st.session_state.price_history[-1][1] if st.session_state.price_history else current_price
    if old_price and old_price > 0:
        price_change = current_price - old_price
        price_change_pct_display = (price_change / old_price * 100)

st.session_state.price_history.append((now, current_price))
st.session_state.price_history = st.session_state.price_history[-100:]
st.session_state.aggression_score_history.append(aggression_score)
st.session_state.aggression_score_history = st.session_state.aggression_score_history[-50:]

# ================= 🚨 ENHANCED ALERT SYSTEM (v4.2) =================
# ------------------------------------------------------------------
# 1. Sound Alert Helper
# ------------------------------------------------------------------
def play_sound(sound_type="beep"):
    """Embed base64 audio and autoplay using HTML."""
    # Base64 encoded short beep sound (WAV)
    beep_base64 = "data:audio/wav;base64,//uQRAAAAWMSLwUIYAAsYkXgoQwAEaYLWfkWgAI0wWs/ItAAAGDgYtAgAyN+QWaAAihwMWm4Gpm0V3L3t5+t///9//+///////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////8="
    if sound_type == "beep":
        audio_html = f'<audio autoplay><source src="{beep_base64}" type="audio/wav"></audio>'
        st.markdown(audio_html, unsafe_allow_html=True)

# ------------------------------------------------------------------
# 2. Telegram Alert Helper
# ------------------------------------------------------------------
def send_telegram_alert(message):
    """Send message via Telegram bot."""
    if not st.session_state.telegram_enabled:
        return
    token = st.session_state.get("telegram_token", "")
    chat_id = st.session_state.get("telegram_chat_id", "")
    if token and chat_id:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
            requests.post(url, data=payload, timeout=2)
        except:
            pass  # Silent fail – don't crash dashboard

# ------------------------------------------------------------------
# 3. Current States
# ------------------------------------------------------------------
lgp_high = lgp >= 70
aggressive_buy = aggressive_data["aggressive_buy"]
aggressive_sell = aggressive_data["aggressive_sell"]
confidence_high = aggression_score >= 80

# ------------------------------------------------------------------
# 4. Position Management (for Exit Alerts)
# ------------------------------------------------------------------
# Enter position when a clean aggressive signal appears
if st.session_state.exit_alerts_enabled:
    if aggressive_buy and not st.session_state.prev_aggressive_buy:
        st.session_state.in_position = True
        st.session_state.position_type = "BUY"
        st.session_state.entry_price = current_price
        st.session_state.entry_time = now
    elif aggressive_sell and not st.session_state.prev_aggressive_sell:
        st.session_state.in_position = True
        st.session_state.position_type = "SELL"
        st.session_state.entry_price = current_price
        st.session_state.entry_time = now

# Exit conditions (configurable via sidebar)
exit_reason = None
if st.session_state.in_position and st.session_state.exit_alerts_enabled:
    pos_type = st.session_state.position_type
    entry = st.session_state.entry_price
    if entry > 0:
        if pos_type == "BUY":
            # Stop loss: price drops below entry - exit_loss_pct%
            loss_pct = (entry - current_price) / entry * 100
            if loss_pct >= st.session_state.get("exit_loss_pct", 0.5):
                exit_reason = f"Stop Loss ({loss_pct:.1f}%)"
            # Take profit: price rises above entry + exit_profit_pct%
            profit_pct = (current_price - entry) / entry * 100
            if profit_pct >= st.session_state.get("exit_profit_pct", 1.0):
                exit_reason = f"Take Profit ({profit_pct:.1f}%)"
            # Reversal signal: aggressive sell appears while in long
            if aggressive_sell and not st.session_state.prev_aggressive_sell:
                exit_reason = "Reversal Signal (SELL)"
        elif pos_type == "SELL":
            loss_pct = (current_price - entry) / entry * 100
            if loss_pct >= st.session_state.get("exit_loss_pct", 0.5):
                exit_reason = f"Stop Loss ({loss_pct:.1f}%)"
            profit_pct = (entry - current_price) / entry * 100
            if profit_pct >= st.session_state.get("exit_profit_pct", 1.0):
                exit_reason = f"Take Profit ({profit_pct:.1f}%)"
            if aggressive_buy and not st.session_state.prev_aggressive_buy:
                exit_reason = "Reversal Signal (BUY)"

    if exit_reason and not st.session_state.prev_exit_triggered:
        # Exit alert!
        st.toast(f"🚪 *Exit Signal* – {exit_reason}", icon="🚪")
        if st.session_state.sound_enabled:
            play_sound("beep")
        # Send Telegram alert
        msg = f"🚪 Exit Alert\nReason: {exit_reason}\nSymbol: BTCUSDT\nEntry: {entry:.2f}\nExit: {current_price:.2f}\nP&L: {((current_price-entry)/entry*100):+.1f}%"
        send_telegram_alert(msg)
        
        # Clear position
        st.session_state.in_position = False
        st.session_state.position_type = None
        st.session_state.entry_price = 0.0
        st.session_state.entry_time = None
        st.session_state.prev_exit_triggered = True
else:
    st.session_state.prev_exit_triggered = False

# ------------------------------------------------------------------
# 5. Entry Alerts (Sound + Telegram)
# ------------------------------------------------------------------
# LGP High
if lgp_high and not st.session_state.prev_lgp_high:
    msg = f"🎯 Liquidity Grab Detected!\nLGP: {lgp}%\nPrice: {current_price:.2f}"
    st.toast(f"🎯 *Liquidity Grab Detected!* LGP: {lgp}%", icon="⚠️")
    if st.session_state.sound_enabled:
        play_sound("beep")
    send_telegram_alert(msg)
    if lgp >= 90:
        st.balloons()

# Aggressive BUY
if aggressive_buy and not st.session_state.prev_aggressive_buy:
    conf = aggressive_data['confidence']
    msg = f"🚀 Clean Aggressive BUY\nConfidence: {conf}%\nPrice: {current_price:.2f}\nVolume Z: {volume_zscore:.1f}σ\nWhale: {metrics.get('whale_concentration',0):.1%}"
    st.toast(f"🚀 *Clean Aggressive BUY* (Confidence: {conf}%)", icon="✅")
    if st.session_state.sound_enabled:
        play_sound("beep")
    send_telegram_alert(msg)

# Aggressive SELL
if aggressive_sell and not st.session_state.prev_aggressive_sell:
    conf = aggressive_data['confidence']
    msg = f"🔻 Clean Aggressive SELL\nConfidence: {conf}%\nPrice: {current_price:.2f}\nVolume Z: {volume_zscore:.1f}σ\nWhale: {metrics.get('whale_concentration',0):.1%}"
    st.toast(f"🔻 *Clean Aggressive SELL* (Confidence: {conf}%)", icon="✅")
    if st.session_state.sound_enabled:
        play_sound("beep")
    send_telegram_alert(msg)

# High Confidence
if confidence_high and not st.session_state.prev_confidence_high:
    msg = f"✨ High Confidence Signal\nConfidence: {aggression_score}%\nLGP: {lgp}%"
    st.toast(f"✨ *High Confidence Signal* – {aggression_score}%", icon="⭐")
    if st.session_state.sound_enabled:
        play_sound("beep")
    send_telegram_alert(msg)

# ------------------------------------------------------------------
# 6. Update previous states
# ------------------------------------------------------------------
st.session_state.prev_lgp_high = lgp_high
st.session_state.prev_aggressive_buy = aggressive_buy
st.session_state.prev_aggressive_sell = aggressive_sell
st.session_state.prev_confidence_high = confidence_high
# ==================================================================

# ================= HEADER METRICS =================
st.subheader("📊 Real-Time Market Metrics")

top_cols = st.columns(5)

with top_cols[0]:
    if current_price > 0:
        st.metric("Current Price", f"${current_price:,.2f}", f"{price_change_pct_display:+.2f}%")
    else:
        st.metric("Current Price", "Waiting for data...", "0%")

with top_cols[1]:
    lgp_color = "🔴" if lgp >= 70 else "🟡" if lgp >= 40 else "🟢"
    st.metric(f"{lgp_color} LGP Score", f"{lgp}%", f"{'High' if lgp >= 70 else 'Medium' if lgp >= 40 else 'Low'}")

with top_cols[2]:
    conf_color = "🟢" if aggression_score >= 80 else "🟡" if aggression_score >= 70 else "⚪"
    st.metric(f"{conf_color} Confidence", f"{aggression_score:.0f}%", "Clean Signal" if aggression_score >= 80 else "")

with top_cols[3]:
    delta_color = "🟢" if delta > 0 else "🔴" if delta < 0 else "⚪"
    st.metric(f"{delta_color} Delta", f"{delta:+.4f}", f"{(delta/total_volume*100) if total_volume > 0 else 0:+.1f}%")

with top_cols[4]:
    # Show position status
    if st.session_state.in_position:
        pos_emoji = "🟢 LONG" if st.session_state.position_type == "BUY" else "🔴 SHORT"
        entry_px = st.session_state.entry_price
        if entry_px > 0:
            pnl = (current_price - entry_px) / entry_px * 100 if st.session_state.position_type == "BUY" else (entry_px - current_price) / entry_px * 100
            pos_label = f"{pos_emoji} ({pnl:+.1f}%)"
        else:
            pos_label = pos_emoji
    else:
        pos_label = "➖ NO POSITION"
    st.metric("Position", pos_label, f"{total_trades} trades")

# ================= LGP METER =================
st.markdown("---")
st.subheader("🎯 Liquidity Grab Probability (LGP)")

lgp_label = "🔴 HIGH RISK" if lgp >= 70 else "🟡 MEDIUM" if lgp >= 40 else "🟢 LOW"

meter_cols = st.columns([3, 1])
with meter_cols[0]:
    st.markdown(f"""
    <div class='lgp-meter' style='background: #374151;'>
        <div class='lgp-fill' style='width: {lgp}%; background: {'#ef4444' if lgp >= 70 else '#f59e0b' if lgp >= 40 else '#10b981'};'></div>
    </div>
    <div class='lgp-labels'>
        <span>Low (0-39%)</span>
        <span>Medium (40-69%)</span>
        <span>High (70-100%)</span>
    </div>
    """, unsafe_allow_html=True)

with meter_cols[1]:
    st.metric("LGP Score", f"{lgp}%", f"{lgp_label}")

if lgp_signals:
    st.subheader("📡 LGP Signals Detected")
    signal_cols = st.columns(min(3, len(lgp_signals)))
    for idx, signal in enumerate(lgp_signals):
        with signal_cols[idx % len(signal_cols)]:
            st.markdown(f"""
            <div class='signal-card signal-lgp'>
                <h4>🎯 {signal}</h4>
            </div>
            """, unsafe_allow_html=True)

# ================= AGGRESSIVE MOVE DETAILS =================
st.markdown("---")
st.subheader("🔍 Enhanced Aggressive Move Analysis")

agg_cols = st.columns(5)
with agg_cols[0]:
    st.metric("Delta Ratio", f"{delta_ratio:.2%}")
with agg_cols[1]:
    st.metric("Price Change", f"{price_change_pct:.2f}%")
with agg_cols[2]:
    st.metric("Trades/Sec", f"{trades_per_sec:.1f}")
with agg_cols[3]:
    st.metric("Volume Z-Score", f"{volume_zscore:.1f}σ", help="Standard deviations from average volume")
with agg_cols[4]:
    whale_conc = metrics.get("whale_concentration", 0)
    st.metric("Whale Concentration", f"{whale_conc:.1%}", help="% of whales on same side (>80% = clustered)")

# ================= AGGRESSIVE SIGNALS =================
st.markdown("---")
st.subheader("🚨 Aggressive Signals")

signal_container = st.container()
with signal_container:
    sig_cols = st.columns(2)
    
    with sig_cols[0]:
        is_liquidity_grab = lgp >= 70 or failed_high or failed_low
        if is_liquidity_grab:
            direction_text = "BUY" if failed_low else "SELL" if failed_high else "LIQUIDITY GRAB"
            st.markdown(f"""
            <div class='signal-card signal-danger'>
                <h4>🎯 LIQUIDITY GRAB ({direction_text})</h4>
                <p>High probability of institutional stop hunting</p>
                <small>LGP: {lgp}% | Structure Break Detected</small>
            </div>
            """, unsafe_allow_html=True)
        
        if aggressive_data["aggressive_buy"]:
            whale_conc = metrics.get("whale_concentration", 0)
            has_accel = metrics.get("has_acceleration", False)
            st.markdown(f"""
            <div class='signal-card signal-buy'>
                <h4>🚀 CLEAN AGGRESSIVE BUY</h4>
                <p>🐳 Institutional accumulation detected - Enhanced Signal</p>
                <small>✅ Confidence: {aggressive_data['confidence']}% | 
                       Volume: {volume_zscore:.1f}σ | 
                       Delta: {delta_ratio:.1%} | 
                       Whale: {whale_conc:.1%} | 
                       Acceleration: {'✅' if has_accel else '❌'}</small>
            </div>
            """, unsafe_allow_html=True)
        
        if aggressive_data["aggressive_sell"]:
            whale_conc = metrics.get("whale_concentration", 0)
            has_accel = metrics.get("has_acceleration", False)
            st.markdown(f"""
            <div class='signal-card signal-sell'>
                <h4>🔻 CLEAN AGGRESSIVE SELL</h4>
                <p>🐳 Institutional distribution detected - Enhanced Signal</p>
                <small>✅ Confidence: {aggressive_data['confidence']}% | 
                       Volume: {volume_zscore:.1f}σ | 
                       Delta: {delta_ratio:.1%} | 
                       Whale: {whale_conc:.1%} | 
                       Acceleration: {'✅' if has_accel else '❌'}</small>
            </div>
            """, unsafe_allow_html=True)
    
    with sig_cols[1]:
        if aggressive_data["confidence"] >= 90:
            st.markdown(f"""
            <div class='signal-card signal-buy' style='border-left-color: #fbbf24;'>
                <h4>⭐ ULTRA HIGH CONFIDENCE</h4>
                <p>Exceptional signal quality - All factors aligned</p>
                <small>Confidence: {aggressive_data['confidence']}% | Trade with conviction</small>
            </div>
            """, unsafe_allow_html=True)
        elif aggressive_data["confidence"] >= 80:
            st.markdown(f"""
            <div class='signal-card signal-warning'>
                <h4>✨ HIGH CONFIDENCE</h4>
                <p>Strong institutional signal detected</p>
                <small>Confidence: {aggressive_data['confidence']}% | Favorable setup</small>
            </div>
            """, unsafe_allow_html=True)
        elif aggressive_data["confidence"] >= 70:
            st.markdown(f"""
            <div class='signal-card signal-lgp'>
                <h4>💫 GOOD CONFIDENCE</h4>
                <p>Solid signal with institutional backing</p>
                <small>Confidence: {aggressive_data['confidence']}% | Monitor closely</small>
            </div>
            """, unsafe_allow_html=True)
        
        if (aggressive_data["aggressive_buy"] or aggressive_data["aggressive_sell"]):
            has_accel = metrics.get("has_acceleration", False)
            if has_accel:
                st.markdown(f"""
                <div class='signal-card signal-buy' style='border-left-color: #10b981;'>
                    <h4>📈 MOMENTUM ACCELERATING</h4>
                    <p>Multi-quarter momentum building</p>
                    <small>Sustained institutional pressure detected</small>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class='signal-card signal-warning'>
                    <h4>⚡ MOMENTUM SPIKE</h4>
                    <p>Strong move but not yet accelerating</p>
                    <small>Watch for continuation or reversal</small>
                </div>
                """, unsafe_allow_html=True)

# ================= WHALE VS RETAIL =================
st.markdown("---")
st.subheader("🐳 Whale vs Retail")

wh_cols = st.columns(4)
with wh_cols[0]:
    st.metric("Whale Count", whale_count, f"{whale_ratio*100:.1f}%")
with wh_cols[1]:
    whale_delta_display = f"{whale_delta:.4f}"
    whale_delta_pct = (whale_delta/(whales['qty'].sum()))*100 if not whales.empty and whales['qty'].sum() > 0 else 0
    st.metric("Whale Δ", whale_delta_display, f"{whale_delta_pct:+.1f}%")
with wh_cols[2]:
    st.metric("Retail Count", retail_count, f"{(retail_count/total_trades*100) if total_trades > 0 else 0:.1f}%")
with wh_cols[3]:
    retail_delta_display = f"{retail_delta:.4f}"
    retail_delta_pct = (retail_delta/(retail['qty'].sum()))*100 if not retail.empty and retail['qty'].sum() > 0 else 0
    st.metric("Retail Δ", retail_delta_display, f"{retail_delta_pct:+.1f}%")

# ================= DUAL CHARTS =================
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    if len(st.session_state.lgp_history) > 5:
        st.subheader("📈 LGP Trend")
        lgp_data = pd.DataFrame({
            'LGP': st.session_state.lgp_history,
            'Index': range(len(st.session_state.lgp_history))
        })
        st.line_chart(lgp_data['LGP'], height=250)

with chart_col2:
    if len(st.session_state.aggression_score_history) > 5:
        st.subheader("📊 Aggression Score Trend")
        agg_data = pd.DataFrame({
            'Aggression': st.session_state.aggression_score_history,
            'Index': range(len(st.session_state.aggression_score_history))
        })
        st.line_chart(agg_data['Aggression'], height=250)

# ================= TRADE TAPE =================
st.markdown("---")
st.subheader("📊 Live Trade Tape")

if not df.empty:
    display_df = df.head(MAX_ROWS_DISPLAY).copy()
    display_df["timestamp"] = pd.to_datetime(display_df["time"], unit="s")
    display_df["time"] = display_df["timestamp"].dt.strftime('%H:%M:%S.%f').str[:-3]
    
    display_df["side"] = display_df["side"].apply(lambda x: f"🟢 {x}" if x == "BUY" else f"🔴 {x}")
    display_df["qty"] = display_df["qty"].apply(lambda x: f"{x:.4f}")
    display_df["value"] = display_df["value"].apply(lambda x: f"${x:,.2f}")
    display_df["price"] = display_df["price"].apply(lambda x: f"{x:.4f}")
    
    st.dataframe(
        display_df[["time", "side", "price", "qty", "value"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "time": st.column_config.TextColumn("Time", width="small"),
            "side": st.column_config.TextColumn("Side", width="small"),
            "price": st.column_config.TextColumn("Price", width="small"),
            "qty": st.column_config.TextColumn("Quantity", width="small"),
            "value": st.column_config.TextColumn("Value", width="small")
        }
    )
    
    st.caption(f"Showing {len(display_df)} of {len(df)} trades in last {WINDOW_SECONDS} seconds")
else:
    st.info("⏳ Waiting for trade data...")

# ================= SIDEBAR CONTROLS =================
with st.sidebar:
    st.title("⚙️ Detection Controls")
    
    st.subheader("LGP Settings")
    lgp_threshold_high = st.slider("High LGP Threshold", 60, 90, 70, 5)
    lgp_threshold_medium = st.slider("Medium LGP Threshold", 30, 60, 40, 5)
    
    st.subheader("Aggression Settings")
    agg_threshold = st.slider("Aggression Threshold (σ)", 1.0, 5.0, AGGRESSIVE_MOVE_THRESHOLD, 0.1)
    delta_ratio_thresh = st.slider("Min Delta Ratio", 0.3, 0.9, AGGRESSIVE_DELTA_RATIO, 0.05)
    
    st.markdown("---")
    st.subheader("🔔 Alert Settings")
    
    # Sound alerts
    st.session_state.sound_enabled = st.checkbox("🔊 Sound Alerts", value=st.session_state.get("sound_enabled", True))
    
    # Exit alerts
    st.session_state.exit_alerts_enabled = st.checkbox("🚪 Exit Alerts", value=st.session_state.get("exit_alerts_enabled", True))
    if st.session_state.exit_alerts_enabled:
        col1, col2 = st.columns(2)
        with col1:
            st.session_state.exit_loss_pct = st.slider("Stop Loss %", 0.1, 2.0, 0.5, 0.1)
        with col2:
            st.session_state.exit_profit_pct = st.slider("Take Profit %", 0.2, 5.0, 1.0, 0.1)
    
    # Telegram alerts
    st.session_state.telegram_enabled = st.checkbox("📲 Telegram Alerts", value=st.session_state.get("telegram_enabled", False))
    if st.session_state.telegram_enabled:
        st.session_state.telegram_token = st.text_input("Bot Token", type="password", value=st.session_state.get("telegram_token", ""))
        st.session_state.telegram_chat_id = st.text_input("Chat ID", value=st.session_state.get("telegram_chat_id", ""))
        if st.session_state.telegram_token and st.session_state.telegram_chat_id:
            st.success("Telegram configured ✓")
        else:
            st.warning("Enter token and chat ID")
    
    st.markdown("---")
    st.subheader("🎯 Enhanced Features")
    st.info("""
    v4.2 Upgrades:
    ✅ Sound Alerts (browser beep)
    ✅ Exit Alerts (SL/TP, reversal)
    ✅ Telegram Integration
    ✅ Position Tracking
    """)
    
    st.markdown("---")
    st.subheader("System Info")
    st.write(f"Pandas: {pd.__version__}")
    st.write(f"Streamlit: {st.__version__}")
    st.write(f"Python: {sys.version.split()[0]}")
    st.write(f"Trades in memory: {len(st.session_state.trade_history)}")
    st.write(f"Current LGP: {lgp}%")
    st.write(f"Time: {datetime.now().strftime('%H:%M:%S')}")

# ================= FOOTER =================
st.markdown("---")
footer_cols = st.columns(3)
with footer_cols[1]:
    current_time = datetime.now().strftime('%H:%M:%S')
    status = "🎯 LIQUIDITY GRAB" if lgp >= 70 else "🚀 CLEAN SIGNAL" if aggression_score >= 80 else "🚨 AGGRESSIVE" if aggression_score >= 70 else "⚠️ WATCHING" if lgp >= 40 or aggression_score >= 40 else "✅ NORMAL"
    st.caption(f"📡 {status} | 🔄 Window: {WINDOW_SECONDS}s | 📊 v4.2 ALERTS")
    st.caption(f"⏰ Last update: {current_time} | 🎯 LGP: {lgp}% | ⚡ Confidence: {aggression_score:.0f}% | 🐳 Clean Signals Only")

# Auto-refresh
time.sleep(1)
st.rerun()