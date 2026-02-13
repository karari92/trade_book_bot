
from collections import deque

# holds last N events
trade_events = deque(maxlen=500)