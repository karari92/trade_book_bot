"""
Enhanced Aggressive Move Detection – V2 (self‑healing)
"""

import pandas as pd
import numpy as np
from collections import deque

class CleanAggressiveMoveDetectorV2:
    """
    Enhanced detector for clean aggressive institutional moves
    All required attributes are guaranteed via _setattr_ fallback.
    """
    def _init_(self):
        # Set all instance attributes – if any are missing, this fixes them.
        self._set_defaults()
        print("✅ CleanAggressiveMoveDetectorV2 initialized with all attributes.")

    def _set_defaults(self):
        """Force all required attributes to exist."""
        # Enhanced thresholds
        self.volume_spike_threshold = getattr(self, 'volume_spike_threshold', 4.0)
        self.delta_concentration = getattr(self, 'delta_concentration', 0.75)
        self.min_price_move = getattr(self, 'min_price_move', 0.25)
        self.min_trades_per_sec = getattr(self, 'min_trades_per_sec', 8)
        self.whale_dominance = getattr(self, 'whale_dominance', 0.4)
        
        # Noise filtering – THESE WERE MISSING IN OLD VERSION
        self.min_volume = getattr(self, 'min_volume', 1.0)
        self.min_trades = getattr(self, 'min_trades', 15)
        self.consolidation_threshold = getattr(self, 'consolidation_threshold', 0.1)
        
        # History tracking
        if not hasattr(self, 'volume_history'):
            self.volume_history = deque(maxlen=30)
        if not hasattr(self, 'delta_history'):
            self.delta_history = deque(maxlen=30)
        if not hasattr(self, 'price_history'):
            self.price_history = deque(maxlen=30)
        if not hasattr(self, 'aggression_history'):
            self.aggression_history = deque(maxlen=20)
        
        # Cooldown
        self.last_signal_time = getattr(self, 'last_signal_time', None)
        self.signal_cooldown = getattr(self, 'signal_cooldown', 5)

    # ----- all methods remain exactly as before -----
    def update_history(self, volume, delta, price):
        self.volume_history.append(volume)
        self.delta_history.append(delta)
        self.price_history.append(price)
    
    def calculate_clean_zscore(self, current_value, history):
        if len(history) < 10:
            return 0
        sorted_history = sorted(history)
        trim_size = max(1, len(sorted_history) // 10)
        trimmed = sorted_history[trim_size:-trim_size] if len(sorted_history) > 2 else sorted_history
        mean = np.mean(trimmed)
        std = np.std(trimmed)
        if std == 0:
            return 0
        return (current_value - mean) / std
    
    def detect_whale_cluster(self, df, whale_threshold=0.5):
        if df.empty:
            return False, 0
        whales = df[df["qty"] >= whale_threshold]
        if len(whales) < 3:
            return False, 0
        whale_buy = whales[whales["side"] == "BUY"]["qty"].sum()
        whale_sell = whales[whales["side"] == "SELL"]["qty"].sum()
        total_whale = whale_buy + whale_sell
        if total_whale == 0:
            return False, 0
        whale_delta_ratio = abs(whale_buy - whale_sell) / total_whale
        is_clustered = whale_delta_ratio > 0.8
        return is_clustered, whale_delta_ratio
    
    def detect_momentum_acceleration(self, df):
        if len(df) < 20:
            return False
        quarter_size = len(df) // 4
        quarters = [df.iloc[i*quarter_size:(i+1)*quarter_size] for i in range(4)]
        quarter_deltas = []
        for q in quarters:
            if not q.empty:
                buy = q[q["side"] == "BUY"]["qty"].sum()
                sell = q[q["side"] == "SELL"]["qty"].sum()
                quarter_deltas.append(buy - sell)
        if len(quarter_deltas) < 3:
            return False
        is_accelerating = all(
            abs(quarter_deltas[i]) < abs(quarter_deltas[i+1])
            for i in range(len(quarter_deltas)-1)
        )
        return is_accelerating
    
    def is_in_consolidation(self, df):
        if df.empty or len(df) < 10:
            return False
        price_range = df["price"].max() - df["price"].min()
        avg_price = df["price"].mean()
        range_pct = (price_range / avg_price) * 100
        return range_pct < self.consolidation_threshold
    
    def detect_clean_aggressive_move(self, df, window_seconds=10, current_time=None):
        # Ensure all attributes exist (in case of unpickling or partial init)
        self._set_defaults()
        
        if df.empty or len(df) < self.min_trades:
            return self._empty_result()
        
        total_volume = df["qty"].sum()
        buy_volume = df[df["side"] == "BUY"]["qty"].sum()
        sell_volume = df[df["side"] == "SELL"]["qty"].sum()
        delta = buy_volume - sell_volume
        
        if total_volume < self.min_volume:
            return self._empty_result()
        
        avg_price = df["price"].mean()
        self.update_history(total_volume, delta, avg_price)
        
        first_price = df.iloc[-1]["price"]
        last_price = df.iloc[0]["price"]
        price_change_pct = ((last_price - first_price) / first_price) * 100
        
        if self.is_in_consolidation(df):
            return self._empty_result()
        
        volume_zscore = self.calculate_clean_zscore(total_volume, self.volume_history)
        delta_ratio = abs(delta) / total_volume if total_volume > 0 else 0
        trades_per_sec = len(df) / window_seconds if window_seconds > 0 else 0
        
        whale_clustered, whale_concentration = self.detect_whale_cluster(df)
        has_acceleration = self.detect_momentum_acceleration(df)
        
        if current_time and self.last_signal_time:
            if (current_time - self.last_signal_time) < self.signal_cooldown:
                return self._empty_result()
        
        aggressive_buy = (
            volume_zscore >= self.volume_spike_threshold and
            delta_ratio >= self.delta_concentration and
            delta > 0 and
            price_change_pct >= self.min_price_move and
            trades_per_sec >= self.min_trades_per_sec and
            whale_clustered and
            has_acceleration
        )
        
        aggressive_sell = (
            volume_zscore >= self.volume_spike_threshold and
            delta_ratio >= self.delta_concentration and
            delta < 0 and
            price_change_pct <= -self.min_price_move and
            trades_per_sec >= self.min_trades_per_sec and
            whale_clustered and
            has_acceleration
        )
        
        confidence = 0
        if aggressive_buy or aggressive_sell:
            confidence += min(25, volume_zscore * 4)
            confidence += min(25, delta_ratio * 30)
            confidence += min(20, abs(price_change_pct) * 50)
            confidence += min(15, whale_concentration * 18)
            confidence += min(15, trades_per_sec * 1.5)
            if has_acceleration:
                confidence += 10
            confidence = min(100, confidence)
            if current_time:
                self.last_signal_time = current_time
        
        if confidence < 70:
            aggressive_buy = False
            aggressive_sell = False
        
        return {
            "aggressive_buy": aggressive_buy,
            "aggressive_sell": aggressive_sell,
            "confidence": confidence,
            "metrics": {
                "volume_zscore": round(volume_zscore, 2),
                "delta_ratio": round(delta_ratio, 3),
                "price_change_pct": round(price_change_pct, 3),
                "trades_per_sec": round(trades_per_sec, 1),
                "whale_concentration": round(whale_concentration, 3),
                "has_acceleration": has_acceleration,
                "total_volume": round(total_volume, 4),
                "delta": round(delta, 4)
            }
        }
    
    def _empty_result(self):
        return {
            "aggressive_buy": False,
            "aggressive_sell": False,
            "confidence": 0,
            "metrics": {
                "volume_zscore": 0,
                "delta_ratio": 0,
                "price_change_pct": 0,
                "trades_per_sec": 0,
                "whale_concentration": 0,
                "has_acceleration": False,
                "total_volume": 0,
                "delta": 0
            }
        }