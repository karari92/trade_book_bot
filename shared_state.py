

# shared_state.py
from collections import deque
import threading

lock = threading.Lock()

# store last 100 trades
trades = deque(maxlen=100)

# live status flag
is_live = False
