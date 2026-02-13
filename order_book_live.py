

import asyncio
import json
import websockets
import time

SYMBOL = "btcusdt"          # change later if needed
DEPTH_LEVEL = 5             # top 5 bids / asks
WS_URL = f"wss://fstream.binance.com/ws/{SYMBOL}@depth{DEPTH_LEVEL}@100ms"


async def order_book_listener():
    while True:
        try:
            print("🔌 Connecting to Binance Futures WebSocket...")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                print("✅ Connected. Listening to live order book...\n")

                async for message in ws:
                    data = json.loads(message)

                    bids = data.get("b", [])
                    asks = data.get("a", [])

                    if not bids or not asks:
                        continue

                    best_bid_price = float(bids[0][0])
                    best_bid_qty = float(bids[0][1])

                    best_ask_price = float(asks[0][0])
                    best_ask_qty = float(asks[0][1])

                    imbalance = best_bid_qty - best_ask_qty

                    print(
                        f"BID {best_bid_price:.2f} ({best_bid_qty:.3f}) | "
                        f"ASK {best_ask_price:.2f} ({best_ask_qty:.3f}) | "
                        f"IMBALANCE: {imbalance:+.3f}"
                    )

        except Exception as e:
            print(f"⚠️ Connection lost: {e}")
            print("♻️ Reconnecting in 3 seconds...\n")
            time.sleep(3)


if __name__ == "__main__":
    asyncio.run(order_book_listener())