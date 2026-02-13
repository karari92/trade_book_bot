
def process_depth(message):
    data = json.loads(message)

    bids = data.get("b", [])
    asks = data.get("a", [])

    bid_volume = sum(float(b[1]) for b in bids)
    ask_volume = sum(float(a[1]) for a in asks)

    imbalance = bid_volume - ask_volume

    if abs(imbalance) > 5:
        side = "BUY PRESSURE" if imbalance > 0 else "SELL PRESSURE"
        print(f"{side} | Bid: {bid_volume:.2f} | Ask: {ask_volume:.2f}")