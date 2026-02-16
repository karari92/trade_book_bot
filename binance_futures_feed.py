"""
Binance Futures Feed - Trades, Mark Price, Order Book, Open Interest, HTF Candles
"""
import websocket
import json
import threading
import time
import requests
from collections import deque

trade_queue = deque(maxlen=1000)
mark_price = {"price": 0, "index_price": 0, "funding_rate": 0, "timestamp": 0}
open_interest = {"value": 0, "prev_value": 0, "change_pct": 0, "timestamp": 0, "spike": False, "direction": "FLAT"}
order_book = {"bids": [], "asks": [], "bid_volume": 0, "ask_volume": 0, "imbalance": 0.0, "imbalance_pct": 0.0, "timestamp": 0}
htf_candles = {"1h": [], "4h": [], "trend_1h": "NEUTRAL", "trend_4h": "NEUTRAL", "ema9_1h": 0.0, "ema21_1h": 0.0, "ema9_4h": 0.0, "ema21_4h": 0.0, "last_update": 0}

ws_trade = None
ws_mark = None
ws_depth = None
BASE_REST = "https://fapi.binance.com"

def on_trade_message(ws, message):
    try:
        data = json.loads(message)
        trade = {"time": data["T"] / 1000, "side": "BUY" if data["m"] == False else "SELL", "price": float(data["p"]), "qty": float(data["q"])}
        trade_queue.append(trade)
    except Exception as e:
        print("Trade error: " + str(e))

def on_mark_price_message(ws, message):
    try:
        data = json.loads(message)
        global mark_price
        mark_price = {"price": float(data["p"]), "index_price": float(data["i"]), "funding_rate": float(data["r"]), "timestamp": data["E"] / 1000}
    except Exception as e:
        print("Mark price error: " + str(e))

def on_depth_message(ws, message):
    try:
        data = json.loads(message)
        bids = [[float(p), float(q)] for p, q in data.get("b", [])[:20]]
        asks = [[float(p), float(q)] for p, q in data.get("a", [])[:20]]
        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        total = bid_vol + ask_vol
        imbalance = (bid_vol - ask_vol) / total if total > 0 else 0.0
        global order_book
        order_book = {"bids": bids, "asks": asks, "bid_volume": round(bid_vol, 4), "ask_volume": round(ask_vol, 4), "imbalance": round(imbalance, 4), "imbalance_pct": round(imbalance * 100, 2), "timestamp": time.time()}
    except Exception as e:
        print("Depth error: " + str(e))

def fetch_open_interest():
    global open_interest
    while True:
        try:
            resp = requests.get(BASE_REST + "/fapi/v1/openInterest?symbol=BTCUSDT", timeout=5)
            if resp.status_code == 200:
                new_val = float(resp.json()["openInterest"])
                prev_val = open_interest["value"] if open_interest["value"] > 0 else new_val
                change_pct = ((new_val - prev_val) / prev_val * 100) if prev_val > 0 else 0.0
                open_interest = {"value": round(new_val, 2), "prev_value": round(prev_val, 2), "change_pct": round(change_pct, 4), "timestamp": time.time(), "spike": abs(change_pct) >= 0.5, "direction": "UP" if change_pct > 0 else "DOWN" if change_pct < 0 else "FLAT"}
        except Exception as e:
            print("OI error: " + str(e))
        time.sleep(10)

def calc_ema(prices, period):
    if not prices:
        return 0.0
    k = 2.0 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema

def classify_trend(ema_fast, ema_slow):
    if ema_slow == 0:
        return "NEUTRAL"
    diff_pct = ((ema_fast - ema_slow) / ema_slow) * 100
    if diff_pct > 0.05:
        return "BULL"
    elif diff_pct < -0.05:
        return "BEAR"
    return "NEUTRAL"

def fetch_htf_candles_loop():
    global htf_candles
    while True:
        try:
            def get_candles(interval, limit=50):
                url = BASE_REST + "/fapi/v1/klines?symbol=BTCUSDT&interval=" + interval + "&limit=" + str(limit)
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    return [{"time": c[0]/1000, "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in resp.json()]
                return []

            c1h = get_candles("1h")
            c4h = get_candles("4h")
            cl1h = [c["close"] for c in c1h]
            cl4h = [c["close"] for c in c4h]
            ema9_1h = calc_ema(cl1h, 9) if cl1h else 0.0
            ema21_1h = calc_ema(cl1h, 21) if cl1h else 0.0
            ema9_4h = calc_ema(cl4h, 9) if cl4h else 0.0
            ema21_4h = calc_ema(cl4h, 21) if cl4h else 0.0
            htf_candles = {"1h": c1h[-10:], "4h": c4h[-10:], "trend_1h": classify_trend(ema9_1h, ema21_1h), "trend_4h": classify_trend(ema9_4h, ema21_4h), "ema9_1h": round(ema9_1h, 2), "ema21_1h": round(ema21_1h, 2), "ema9_4h": round(ema9_4h, 2), "ema21_4h": round(ema21_4h, 2), "last_update": time.time()}
            print("HTF updated: 1h=" + htf_candles["trend_1h"] + " 4h=" + htf_candles["trend_4h"])
        except Exception as e:
            print("HTF error: " + str(e))
        time.sleep(300)

def on_error(ws, error):
    print("WS error: " + str(error))

def on_close(ws, close_status_code, close_msg):
    print("WS closed")

def on_open(ws):
    print("WS connected: " + str(ws.url))

def start_trade_stream():
    global ws_trade
    while True:
        try:
            ws_trade = websocket.WebSocketApp("wss://fstream.binance.com/ws/btcusdt@aggTrade", on_message=on_trade_message, on_error=on_error, on_close=on_close, on_open=on_open)
            ws_trade.run_forever()
        except Exception as e:
            print("Trade stream error: " + str(e))
        time.sleep(5)

def start_mark_price_stream():
    global ws_mark
    while True:
        try:
            ws_mark = websocket.WebSocketApp("wss://fstream.binance.com/ws/btcusdt@markPrice@1s", on_message=on_mark_price_message, on_error=on_error, on_close=on_close, on_open=on_open)
            ws_mark.run_forever()
        except Exception as e:
            print("Mark stream error: " + str(e))
        time.sleep(5)

def start_depth_stream():
    global ws_depth
    while True:
        try:
            ws_depth = websocket.WebSocketApp("wss://fstream.binance.com/ws/btcusdt@depth20@100ms", on_message=on_depth_message, on_error=on_error, on_close=on_close, on_open=on_open)
            ws_depth.run_forever()
        except Exception as e:
            print("Depth stream error: " + str(e))
        time.sleep(5)

def start_binance_feed():
    print("Starting Binance Futures feed...")
    for target in [start_trade_stream, start_mark_price_stream, start_depth_stream, fetch_open_interest, fetch_htf_candles_loop]:
        threading.Thread(target=target, daemon=True).start()
    print("All feeds started: Trades, Mark Price, Order Book, Open Interest, HTF Candles")

if __name__ == "__main__":
    start_binance_feed()
    time.sleep(10)
    print("Trades: " + str(len(trade_queue)))
    print("OI: " + str(open_interest.get("value", 0)))
    print("Book Imbalance: " + str(order_book.get("imbalance_pct", 0)) + "%")
    print("1h Trend: " + htf_candles.get("trend_1h", "NEUTRAL"))
    print("4h Trend: " + htf_candles.get("trend_4h", "NEUTRAL"))