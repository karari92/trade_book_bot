
import json
import threading
from datetime import datetime
from binance import ThreadedWebsocketManager

# Shared state (THIS is what the dashboard will read later)
LIVE_TRADES = []
MAX_TRADES = 500

def handle_trade(msg):
    try:
        trade = {
            "time": datetime.fromtimestamp(msg["T"] / 1000).strftime("%H:%M:%S"),
            "side": "BUY" if msg["m"] is False else "SELL",
            "price": float(msg["p"]),
            "qty": float(msg["q"])
        }

        LIVE_TRADES.append(trade)

        # keep memory bounded
        if len(LIVE_TRADES) > MAX_TRADES:
            LIVE_TRADES.pop(0)

    except Exception as e:
        print("Trade parse error:", e)

def start_trade_stream(symbol="btcusdt"):
    twm = ThreadedWebsocketManager()
    twm.start()

    twm.start_trade_socket(
        symbol=symbol,
        callback=handle_trade
    )

    return twm