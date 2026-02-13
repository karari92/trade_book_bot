
import asyncio
import websockets

async def test():
    url = "wss://fstream.binance.com/ws/btcusdt@depth@100ms"
    async with websockets.connect(url) as ws:
        print("✅ Connected to Binance Futures WebSocket")
        data = await ws.recv()
        print("📩 Data received:", data[:200])

asyncio.run(test())