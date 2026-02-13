

[12:02, 13/02/2026] S.I: import streamlit as st
import pandas as pd
import numpy as np
import time
from datetime import datetime
import sys
import requests
import base64
import importlib

from binance_futures_feed import trade_queue, mark_price, start_binance_feed

# === FORCE LOAD OF NEW DETECTOR CLASS (RENAMED – NO CACHE) ===
import enhanced_detection
importlib.reload(enhanced_detection)
from enhanced_detection import CleanAggressiveMoveDetectorV2 as CleanAggressiveMoveDetector  # alias to keep code unchanged

# Delete any stale instance
if "enhanced_detector" in st.session_state:
    del st.session_state.enhanced_detector

# Create brand new instance
st.session_state.enhanced_detector = CleanAggressiveMoveDetector()

# 🛡️ FALLBACK: if for any reason min_trades is missing, set it…
[12:13, 13/02/2026] S.I: # Add this AFTER line 282 (after aggressive_data calculation)

# ================= ALERT SYSTEM =================
def send_telegram_alert(message):
    """Send Telegram alert if enabled"""
    if not st.session_state.telegram_enabled:
        return False
    
    try:
        token = st.session_state.get("telegram_token") or st.secrets.get("TELEGRAM_TOKEN")
        chat_id = st.session_state.get("telegram_chat_id") or st.secrets.get("TELEGRAM_CHAT_ID")
        
        if not token or not chat_id:
            return False
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        response = requests.post(url, json=payload, timeout=5)
        return response.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def play_sound_alert(alert_type="BUY"):
    """Play sound alert (browser notification sound)"""
    if not st.session_state.sound_enabled:
        return
    
    # Use browser's notification API via JavaScript
    frequency = 1000 if alert_type == "BUY" else 500  # Hz
    st.markdown(f"""
    <script>
    try {{
        var audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        var oscillator = audioCtx.createOscillator();
        oscillator.frequency.value = {frequency};
        oscillator.connect(audioCtx.destination);
        oscillator.start();
        setTimeout(() => oscillator.stop(), 200);
    }} catch(e) {{
        console.log("Audio not supported");
    }}
    </script>
    """, unsafe_allow_html=True)

# ================= SIGNAL ALERTS =================
# Check for new signals and send alerts
current_time = datetime.now().strftime('%H:%M:%S')
current_price = df.iloc[0]["price"] if not df.empty else 0

# BUY Signal Alert
if aggressive_buy and not st.session_state.prev_aggressive_buy:
    message = f"""
🚀 *CLEAN AGGRESSIVE BUY SIGNAL*
⏰ Time: {current_time}
💰 Price: ${current_price:,.2f}
📊 Confidence: {aggressive_data['confidence']}%
📈 Volume: {volume_zscore:.1f}σ
⚖️ Delta: {delta_ratio:.1%}
🐳 Whales: {metrics.get('whale_concentration', 0):.1%}
"""
    play_sound_alert("BUY")
    send_telegram_alert(message)
    st.session_state.prev_aggressive_buy = True
elif not aggressive_buy:
    st.session_state.prev_aggressive_buy = False

# SELL Signal Alert
if aggressive_sell and not st.session_state.prev_aggressive_sell:
    message = f"""
🔻 *CLEAN AGGRESSIVE SELL SIGNAL*
⏰ Time: {current_time}
💰 Price: ${current_price:,.2f}
📊 Confidence: {aggressive_data['confidence']}%
📈 Volume: {volume_zscore:.1f}σ
⚖️ Delta: {delta_ratio:.1%}
🐳 Whales: {metrics.get('whale_concentration', 0):.1%}
"""
    play_sound_alert("SELL")
    send_telegram_alert(message)
    st.session_state.prev_aggressive_sell = True
elif not aggressive_sell:
    st.session_state.prev_aggressive_sell = False

# LGP High Alert
if lgp >= 70 and not st.session_state.prev_lgp_high:
    message = f"""
🎯 *LIQUIDITY GRAB DETECTED*
⏰ Time: {current_time}
💰 Price: ${current_price:,.2f}
🎯 LGP Score: {lgp}%
⚠️ Signals: {', '.join(lgp_signals)}
"""
    play_sound_alert("WARNING")
    send_telegram_alert(message)
    st.session_state.prev_lgp_high = True
elif lgp < 70:
    st.session_state.prev_lgp_high = False

# ================= EXIT ALERTS =================
if st.session_state.exit_alerts_enabled and st.session_state.in_position:
    entry_price = st.session_state.entry_price
    position_type = st.session_state.position_type
    
    if entry_price > 0 and current_price > 0:
        # Calculate PnL
        if position_type == "BUY":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:  # SELL
            pnl_pct = (entry_price - current_price) / entry_price * 100
        
        # Check Stop Loss
        if pnl_pct <= -st.session_state.get("exit_loss_pct", 0.5):
            if not st.session_state.prev_exit_triggered:
                message = f"""
🚨 *STOP LOSS HIT*
⏰ Time: {current_time}
📍 Position: {position_type}
💰 Entry: ${entry_price:,.2f}
💰 Current: ${current_price:,.2f}
📉 Loss: {pnl_pct:.2f}%
🚪 Exit recommended!
"""
                play_sound_alert("WARNING")
                send_telegram_alert(message)
                st.session_state.prev_exit_triggered = True
                st.warning(f"🚨 STOP LOSS HIT: {pnl_pct:.2f}% loss")
        
        # Check Take Profit
        elif pnl_pct >= st.session_state.get("exit_profit_pct", 1.0):
            if not st.session_state.prev_exit_triggered:
                message = f"""
✅ *TAKE PROFIT HIT*
⏰ Time: {current_time}
📍 Position: {position_type}
💰 Entry: ${entry_price:,.2f}
💰 Current: ${current_price:,.2f}
📈 Profit: {pnl_pct:.2f}%
💰 Take profits!
"""
                play_sound_alert("BUY")
                send_telegram_alert(message)
                st.session_state.prev_exit_triggered = True
                st.success(f"✅ TAKE PROFIT HIT: {pnl_pct:.2f}% profit")
        
        # Check Signal Reversal
        elif (position_type == "BUY" and aggressive_sell) or (position_type == "SELL" and aggressive_buy):
            if not st.session_state.prev_exit_triggered:
                message = f"""
🔄 *SIGNAL REVERSAL*
⏰ Time: {current_time}
📍 Current Position: {position_type}
🔄 Opposite Signal Detected
💰 Current PnL: {pnl_pct:.2f}%
⚠️ Consider exiting
"""
                play_sound_alert("WARNING")
                send_telegram_alert(message)
                st.session_state.prev_exit_triggered = True
                st.warning(f"🔄 SIGNAL REVERSAL: Consider exiting {position_type} position")
        else:
            st.session_state.prev_exit_triggered = False