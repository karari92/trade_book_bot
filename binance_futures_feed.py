"""
Binance Futures WebSocket Feed
Real-time futures trade data for BTCUSDT perpetual
"""

import websocket
import json
import threading
import time
from collections import deque

# Global trade queue (shared with dashboard)
trade_queue = deque(maxlen=1000)

# Global funding rate and mark price
funding_rate = {"rate": 0, "next_funding_time": 0}
mark_price = {"price": 0, "timestamp": 0}
open_interest = {"value": 0, "timestamp": 0}

# WebSocket connections
ws_trade = None
ws_mark = None
ws_funding = None

def on_trade_message(ws, message):
    """Handle incoming trade messages"""
    try:
        data = json.loads(message)
        
        trade = {
            "time": data["T"] / 1000,  # Convert to seconds
            "side": "BUY" if data["m"] == False else "SELL",  # m=true means buyer is market maker (sell)
            "price": float(data["p"]),
            "qty": float(data["q"]),
        }
        
        trade_queue.append(trade)
        
    except Exception as e:
        print(f"Error processing trade: {e}")

def on_mark_price_message(ws, message):
    """Handle mark price updates"""
    try:
        data = json.loads(message)
        
        global mark_price
        mark_price = {
            "price": float(data["p"]),
            "index_price": float(data["i"]),
            "funding_rate": float(data["r"]),
            "timestamp": data["E"] / 1000
        }
        
    except Exception as e:
        print(f"Error processing mark price: {e}")

def on_error(ws, error):
    """Handle WebSocket errors"""
    print(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    """Handle WebSocket close"""
    print(f"WebSocket closed: {close_status_code} - {close_msg}")

def on_open(ws):
    """Handle WebSocket open"""
    print(f"WebSocket connected: {ws.url}")

def start_trade_stream():
    """Start the trade stream WebSocket"""
    global ws_trade
    
    ws_trade = websocket.WebSocketApp(
        "wss://fstream.binance.com/ws/btcusdt@aggTrade",
        on_message=on_trade_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )
    
    ws_trade.run_forever()

def start_mark_price_stream():
    """Start the mark price stream WebSocket"""
    global ws_mark
    
    ws_mark = websocket.WebSocketApp(
        "wss://fstream.binance.com/ws/btcusdt@markPrice@1s",
        on_message=on_mark_price_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )
    
    ws_mark.run_forever()

def start_binance_feed():
    """Initialize all WebSocket connections"""
    print("🚀 Starting Binance Futures feed...")
    
    # Start trade stream in separate thread
    trade_thread = threading.Thread(target=start_trade_stream, daemon=True)
    trade_thread.start()
    
    # Start mark price stream in separate thread
    mark_thread = threading.Thread(target=start_mark_price_stream, daemon=True)
    mark_thread.start()
    
    print("✅ Binance Futures feed started")
    print("   - Aggregate Trades: wss://fstream.binance.com/ws/btcusdt@aggTrade")
    print("   - Mark Price: wss://fstream.binance.com/ws/btcusdt@markPrice@1s")

if __name__ == "__main__":
    # Test the feed
    start_binance_feed()
    
    print("\nWaiting for data...")
    time.sleep(5)
    
    print(f"\nTrades received: {len(trade_queue)}")
    if trade_queue:
        latest = trade_queue[-1]
        print(f"Latest trade: {latest['side']} {latest['qty']} @ ${latest['price']:,.2f}")
    
    if mark_price["price"] > 0:
        print(f"Mark Price: ${mark_price['price']:,.2f}")
        print(f"Funding Rate: {mark_price['funding_rate']*100:.4f}%")
    
    # Keep running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")