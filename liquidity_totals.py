

import json
import websockets
import asyncio

SYMBOL = "btcusdt"
DEPTH_LEVELS = 20   # top 20 levels only

async def order_book():
    url = f"wss://fstream.binance.com/ws/{SYMBOL}@depth{DEPTH_LEVELS}@100ms"

    async with websockets.connect(url) as ws:
        print("Connected to Binance Order Book")

        while True:
            msg = await ws.recv()
            data = json.loads(msg)

            bids = data.get("b", [])
            asks = data.get("a", [])

            total_bid_liq = sum(float(bid[1]) for bid in bids)
            total_ask_liq = sum(float(ask[1]) for ask in asks)

            print(
                f"{SYMBOL.upper()} | "
                f"Bid Liquidity: {total_bid_liq:.2f} | "
                f"Ask Liquidity: {total_ask_liq:.2f}"
            )

asyncio.run(order_book())