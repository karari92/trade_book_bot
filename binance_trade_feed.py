import json
import time
from datetime import datetime
from binance import ThreadedWebsocketManager

# ================= CONFIG =================
SYMBOL = "BTCUSDT"
MIN_QTY = 0.04          # strict scalping filter
ABSORB_QTY = 0.25       # absorption threshold
LOG_FILE = "events.log"

# ================= STATE =================
recent_trades = []

# ================= HELPERS =================
def log_event(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def now():
    return datetime.utcnow().strftime("%H:%M:%S")

# ================= TRADE HANDLER =================
def handle_trade(msg):
    global recent_trades

    try:
        price = float(msg["p"])
        qty = float(msg["q"])
        side = "SELL" if msg["m"] else "BUY"

        # basic filter
        if qty < MIN_QTY:
            return

        # store recent trades (rolling window)
        recent_trades.append((side, price, qty))
        recent_trades = recent_trades[-20:]

        total_buy = sum(q for s, _, q in recent_trades if s == "BUY")
        total_sell = sum(q for s, _, q in recent_trades if s == "SELL")

        event = None

        # ================= LIQUIDITY SWEEP =================
        if side == "BUY" and total_buy > total_sell * 2:
            event = f"[{now()}] BUY-SIDE LIQUIDITY SWEEP | price={price} | vol={round(total_buy,3)}"

        elif side == "SELL" and total_sell > total_buy * 2:
            event = f"[{now()}] SELL-SIDE LIQUIDITY SWEEP | price={price} | vol={round(total_sell,3)}"

        # ================= ABSORPTION =================
        if qty >= ABSORB_QTY:
            event = f"[{now()}] ABSORPTION | {side} | price={price} | qty={qty}"

        # ================= OUTPUT =================
        if event:
            print(event)
            log_event(event)

    except Exception as e:
        print("Error:", e)

# ================= MAIN =================
if __name__ == "__main__":
    print("Starting Binance Trade Feed...")
    print("Symbol:", SYMBOL)

    twm = ThreadedWebsocketManager()
    twm.start()

    twm.start_trade_socket(
        symbol=SYMBOL,
        callback=handle_trade
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping feed...")
        twm.stop()