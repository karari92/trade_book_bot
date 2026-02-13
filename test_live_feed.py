
import time
from live_feed import start_trade_stream, LIVE_TRADES

print("Starting Binance live trade feed...")
twm = start_trade_stream("btcusdt")

try:
    while True:
        time.sleep(2)
        if LIVE_TRADES:
            print(LIVE_TRADES[-1])
except KeyboardInterrupt:
    print("Stopping stream...")
    twm.stop()