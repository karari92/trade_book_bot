import pandas as pd
import numpy as np
from collections import deque

class CleanAggressiveMoveDetector:

    def _init_(self):
        self.volume_spike_threshold = 2.5
        self.delta_concentration = 0.60
        self.min_price_move = 0.15
        self.min_trades_per_sec = 5
        self.whale_dominance = 0.25
        self.min_volume = 0.5
        self.min_trades = 10
        self.consolidation_threshold = 0.08
        self.volume_history = deque(maxlen=30)
        self.delta_history = deque(maxlen=30)
        self.price_history = deque(maxlen=30)
        self.aggression_history = deque(maxlen=20)
        self.last_signal_time = None
        self.signal_cooldown = 3

    def update_history(self, volume, delta, price):
        self.volume_history.append(volume)
        self.delta_history.append(delta)
        self.price_history.append(price)

    def calculate_clean_zscore(self, current_value, history):
        if len(history) < 5:
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
        if len(whales) < 2:
            return False, 0
        whale_buy = whales[whales["side"] == "BUY"]["qty"].sum()
        whale_sell = whales[whales["side"] == "SELL"]["qty"].sum()
        total_whale = whale_buy + whale_sell
        if total_whale == 0:
            return False, 0
        whale_delta_ratio = abs(whale_buy - whale_sell) / total_whale
        is_clustered = whale_delta_ratio > 0.65
        return is_clustered, whale_delta_ratio

    def detect_momentum_acceleration(self, df):
        if len(df) < 15:
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
        increases = sum(1 for i in range(len(quarter_deltas)-1)
                       if abs(quarter_deltas[i]) < abs(quarter_deltas[i+1]))
        return increases >= 2

    def is_in_consolidation(self, df):
        if df.empty or len(df) < 10:
            return False
        price_range = df["price"].max() - df["price"].min()
        avg_price = df["price"].mean()
        range_pct = (price_range / avg_price) * 100
        return range_pct < self.consolidation_threshold

    def detect_clean_aggressive_move(self, df, window_seconds=10, current_time=None):
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
        volume_zscore = self.calculate_clean_zscore(total_volume, self.volume_history)
        delta_ratio = abs(delta) / total_volume if total_volume > 0 else 0
        trades_per_sec = len(df) / window_seconds if window_seconds > 0 else 0
        whale_clustered, whale_concentration = self.detect_whale_cluster(df)
        has_acceleration = self.detect_momentum_acceleration(df)
        aggressive_buy = (
            volume_zscore >= self.volume_spike_threshold and
            delta_ratio >= self.delta_concentration and
            delta > 0 and
            price_change_pct >= self.min_price_move and
            trades_per_sec >= self.min_trades_per_sec
        )
        aggressive_sell = (
            volume_zscore >= self.volume_spike_threshold and
            delta_ratio >= self.delta_concentration and
            delta < 0 and
            price_change_pct <= -self.min_price_move and
            trades_per_sec >= self.min_trades_per_sec
        )
        confidence = 0
        if volume_zscore >= 1.0:
            confidence += min(25, volume_zscore * 8)
        if delta_ratio >= 0.4:
            confidence += min(25, (delta_ratio - 0.4) * 40)
        if abs(price_change_pct) >= 0.1:
            confidence += min(20, abs(price_change_pct) * 40)
        if trades_per_sec >= 3:
            confidence += min(15, trades_per_sec * 2.5)
        if whale_clustered:
            confidence += 10
        if has_acceleration:
            confidence += 5
        confidence = min(100, confidence)
        if confidence < 50:
            aggressive_buy = False
            aggressive_sell = False
        if (aggressive_buy or aggressive_sell) and current_time:
            self.last_signal_time = current_time
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

CleanAggressiveMoveDetectorV2 = CleanAggressiveMoveDetector