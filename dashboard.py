import json
import websocket
from datetime import datetime
from live_feed_buffer import add_trade

SYMBOL = "btcusdt"
STREAM_URL = f"wss://stream.binance.com:9443/ws/{SYMBOL}@trade"

def on_message(ws, message):
    trade = json.loads(message)

    data = {
        "time": datetime.fromtimestamp(trade["T"] / 1000).strftime("%H:%M:%S"),
        "price": float(trade["p"]),
        "qty": float(trade["q"]),
        "side": "BUY" if trade["m"] is False else "SELL"
    }

    add_trade(data)

    print(
        f'{data["time"]} | {data["side"]} | price={data["price"]} | qty={data["qty"]}'
    )

def on_open(ws):
    print("✅ Connected to Binance LIVE trade stream")

ws = websocket.WebSocketApp(
    STREAM_URL,
    on_message=on_message
)

ws.on_open = on_open
ws.run_forever()