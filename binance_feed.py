import json
import threading
import time
from collections import deque

import websocket

# ===== GLOBAL QUEUE (MUST BE TOP LEVEL) =====
trade_queue = deque(maxlen=500)

SYMBOL = "btcusdt"
STREAM_URL = f"wss://stream.binance.com:9443/ws/{SYMBOL}@trade"


def on_message(ws, message):
    data = json.loads(message)

    trade_queue.append({
        "time": time.time(),
        "side": "SELL" if data["m"] else "BUY",
        "price": float(data["p"]),
        "qty": float(data["q"])
    })


def start_binance_feed():
    ws = websocket.WebSocketApp(
        STREAM_URL,
        on_message=on_message
    )

    thread = threading.Thread(target=ws.run_forever, daemon=True)
    thread.start()