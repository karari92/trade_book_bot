
from collections import deque
from threading import Lock

# shared buffer for latest trades
trade_buffer = deque(maxlen=100)
lock = Lock()

def add_trade(trade):
    with lock:
        trade_buffer.append(trade)

def get_trades():
    with lock:
        return list(trade_buffer)
