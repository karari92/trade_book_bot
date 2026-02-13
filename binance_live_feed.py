from collections import deque
from binance import ThreadedWebsocketManager
import time

trade_queue = deque(maxlen=500)

def start_binance_feed(symbol="btcusdt"):
    def handle_trade(msg):
        trade_queue.append({
            "time": time.strftime("%H:%M:%S"),
            "side": "BUY" if msg["m"] is False else "SELL",
            "price": float(msg["p"]),
            "qty": float(msg["q"])
        })

    twm = ThreadedWebsocketManager()
    twm.start()
    twm.start_trade_socket(callback=handle_trade, symbol=symbol)