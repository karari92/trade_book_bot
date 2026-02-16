import pandas as pd
import numpy as np
from collections import deque
from datetime import datetime


class CleanAggressiveMoveDetector:

    def __init__(self):
        # Core thresholds
        self.volume_spike_threshold = 2.5
        self.delta_concentration = 0.60
        self.min_price_move = 0.15
        self.min_trades_per_sec = 5
        self.whale_dominance = 0.25
        self.min_volume = 0.5
        self.min_trades = 10
        self.consolidation_threshold = 0.08
        self.signal_cooldown = 3
        self.last_signal_time = None

        # History buffers
        self.volume_history = deque(maxlen=30)
        self.delta_history = deque(maxlen=30)
        self.price_history = deque(maxlen=30)
        self.aggression_history = deque(maxlen=20)

        # Multi-timeframe buffers
        self.mtf_volume_history = {
            "10s": deque(maxlen=30),
            "30s": deque(maxlen=30),
            "60s": deque(maxlen=30),
        }
        self.mtf_delta_history = {
            "10s": deque(maxlen=30),
            "30s": deque(maxlen=30),
            "60s": deque(maxlen=30),
        }

        # Trend filter state
        self.ema_fast = None
        self.ema_slow = None
        self.trend_direction = "NEUTRAL"
        self.trend_strength = 0

        # Win rate tracker
        self.signal_log = []
        self.open_signal = None

    # -- History helpers ----------------------------------------------

    def update_history(self, volume, delta, price):
        self.volume_history.append(volume)
        self.delta_history.append(delta)
        self.price_history.append(price)

    def update_mtf_history(self, df, label, window_seconds):
        if df.empty:
            return
        cutoff = df["time"].max() - window_seconds
        window_df = df[df["time"] >= cutoff]
        if window_df.empty:
            return
        vol = window_df["qty"].sum()
        buy = window_df[window_df["side"] == "BUY"]["qty"].sum()
        sell = window_df[window_df["side"] == "SELL"]["qty"].sum()
        delta = buy - sell
        self.mtf_volume_history[label].append(vol)
        self.mtf_delta_history[label].append(delta)

    # -- Z-Score ------------------------------------------------------

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

    # -- Whale cluster ------------------------------------------------

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
        return whale_delta_ratio > 0.65, whale_delta_ratio

    # -- Momentum acceleration ----------------------------------------

    def detect_momentum_acceleration(self, df):
        if len(df) < 15:
            return False
        quarter_size = len(df) // 4
        quarters = [df.iloc[i * quarter_size:(i + 1) * quarter_size] for i in range(4)]
        quarter_deltas = []
        for q in quarters:
            if not q.empty:
                buy = q[q["side"] == "BUY"]["qty"].sum()
                sell = q[q["side"] == "SELL"]["qty"].sum()
                quarter_deltas.append(buy - sell)
        if len(quarter_deltas) < 3:
            return False
        increases = sum(
            1 for i in range(len(quarter_deltas) - 1)
            if abs(quarter_deltas[i]) < abs(quarter_deltas[i + 1])
        )
        return increases >= 2

    def is_in_consolidation(self, df):
        if df.empty or len(df) < 10:
            return False
        price_range = df["price"].max() - df["price"].min()
        avg_price = df["price"].mean()
        range_pct = (price_range / avg_price) * 100
        return range_pct < self.consolidation_threshold

    # -- TREND FILTER -------------------------------------------------

    def update_trend(self, current_price, htf_trend_1h="NEUTRAL", htf_trend_4h="NEUTRAL"):
        """
        Multi-layered trend:
        - 4H trend = primary bias (strongest weight)
        - 1H trend = secondary confirmation
        - 10s EMA = short-term momentum
        All three must agree for full confirmation.
        """
        k_fast = 2.0 / (9 + 1)
        k_slow = 2.0 / (21 + 1)
        if self.ema_fast is None:
            self.ema_fast = current_price
            self.ema_slow = current_price
        else:
            self.ema_fast = current_price * k_fast + self.ema_fast * (1 - k_fast)
            self.ema_slow = current_price * k_slow + self.ema_slow * (1 - k_slow)

        diff_pct = ((self.ema_fast - self.ema_slow) / self.ema_slow) * 100 if self.ema_slow else 0

        if diff_pct > 0.05:
            short_trend = "BULL"
        elif diff_pct < -0.05:
            short_trend = "BEAR"
        else:
            short_trend = "NEUTRAL"

        # Score each timeframe
        bull_votes = sum([
            htf_trend_4h == "BULL",
            htf_trend_1h == "BULL",
            short_trend == "BULL",
        ])
        bear_votes = sum([
            htf_trend_4h == "BEAR",
            htf_trend_1h == "BEAR",
            short_trend == "BEAR",
        ])

        # 4H trend has veto power - if 4H is strong, override
        if htf_trend_4h == "BULL" and bull_votes >= 2:
            self.trend_direction = "BULL"
            self.trend_strength = min(100, bull_votes * 33 + int(abs(diff_pct) * 100))
        elif htf_trend_4h == "BEAR" and bear_votes >= 2:
            self.trend_direction = "BEAR"
            self.trend_strength = min(100, bear_votes * 33 + int(abs(diff_pct) * 100))
        elif bull_votes >= 2:
            self.trend_direction = "BULL"
            self.trend_strength = min(80, bull_votes * 25)
        elif bear_votes >= 2:
            self.trend_direction = "BEAR"
            self.trend_strength = min(80, bear_votes * 25)
        else:
            self.trend_direction = "NEUTRAL"
            self.trend_strength = 0

        self.htf_trend_1h = htf_trend_1h
        self.htf_trend_4h = htf_trend_4h
        self.short_trend = short_trend

        return self.trend_direction, self.trend_strength

    def trend_allows_signal(self, signal_type):
        if self.trend_direction == "NEUTRAL":
            return True, "Neutral - both directions allowed"
        if signal_type == "BUY" and self.trend_direction == "BULL":
            return True, "Bull trend confirms LONG"
        if signal_type == "BUY" and self.trend_direction == "BEAR":
            return False, "Bear trend blocks LONG"
        if signal_type == "SELL" and self.trend_direction == "BEAR":
            return True, "Bear trend confirms SHORT"
        if signal_type == "SELL" and self.trend_direction == "BULL":
            return False, "Bull trend blocks SHORT"
        return True, "Trend check passed"

    # -- MULTI-TIMEFRAME CONFIRMATION ---------------------------------

    def get_mtf_confirmation(self, signal_type):
        score = 1
        details = {"10s": "PASS", "30s": "WARMING", "60s": "WARMING"}

        for label in ["30s", "60s"]:
            v_hist = self.mtf_volume_history[label]
            d_hist = self.mtf_delta_history[label]
            if len(v_hist) < 3 or len(d_hist) < 3:
                details[label] = "WARMING"
                continue
            avg_delta = float(np.mean(list(d_hist)[-3:]))
            avg_vol = float(np.mean(list(v_hist)[-3:]))
            if avg_vol == 0:
                details[label] = "NO DATA"
                continue
            delta_ratio_mtf = abs(avg_delta) / avg_vol

            if signal_type == "BUY" and avg_delta > 0 and delta_ratio_mtf >= 0.40:
                score += 1
                details[label] = "PASS"
            elif signal_type == "SELL" and avg_delta < 0 and delta_ratio_mtf >= 0.40:
                score += 1
                details[label] = "PASS"
            else:
                details[label] = "FAIL"

        confirmed = score >= 2
        return confirmed, score, details

    # -- WIN RATE TRACKER ---------------------------------------------

    def log_signal(self, signal_type, price, confidence, timestamp=None):
        ts = timestamp or datetime.now()
        if self.open_signal is not None:
            self._resolve_signal(price, "EXPIRED", ts)
        self.open_signal = {
            "type": signal_type,
            "entry_price": price,
            "confidence": confidence,
            "timestamp": ts,
            "outcome": None,
            "exit_price": None,
            "pnl_pct": None,
            "duration": None,
        }

    def _resolve_signal(self, current_price, outcome, timestamp=None):
        if self.open_signal is None:
            return
        ts = timestamp or datetime.now()
        entry = self.open_signal["entry_price"]
        sig_type = self.open_signal["type"]
        if sig_type == "BUY":
            pnl = (current_price - entry) / entry * 100
        else:
            pnl = (entry - current_price) / entry * 100
        if outcome == "AUTO":
            outcome = "WIN" if pnl > 0 else "LOSS"
        duration = int((ts - self.open_signal["timestamp"]).total_seconds())
        self.open_signal.update({
            "outcome": outcome,
            "exit_price": current_price,
            "pnl_pct": round(pnl, 3),
            "duration": duration,
        })
        self.signal_log.append(dict(self.open_signal))
        self.signal_log = self.signal_log[-100:]
        self.open_signal = None

    def resolve_open_signal(self, current_price, outcome="AUTO"):
        self._resolve_signal(current_price, outcome)

    def get_win_rate_stats(self):
        resolved = [s for s in self.signal_log if s["outcome"] in ("WIN", "LOSS")]
        if not resolved:
            return {
                "total": 0, "wins": 0, "losses": 0,
                "win_rate": 0.0, "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0, "avg_pnl": 0.0,
                "best_trade": 0.0, "worst_trade": 0.0,
                "expectancy": 0.0, "recent_10_wr": 0.0,
            }
        wins = [s for s in resolved if s["outcome"] == "WIN"]
        losses = [s for s in resolved if s["outcome"] == "LOSS"]
        win_pnls = [s["pnl_pct"] for s in wins]
        loss_pnls = [s["pnl_pct"] for s in losses]
        all_pnls = [s["pnl_pct"] for s in resolved]
        avg_win = float(np.mean(win_pnls)) if win_pnls else 0.0
        avg_loss = float(np.mean(loss_pnls)) if loss_pnls else 0.0
        win_rate = len(wins) / len(resolved)
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
        recent = resolved[-10:]
        recent_wins = sum(1 for s in recent if s["outcome"] == "WIN")
        return {
            "total": len(resolved),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate * 100, 1),
            "avg_win_pct": round(avg_win, 3),
            "avg_loss_pct": round(avg_loss, 3),
            "avg_pnl": round(float(np.mean(all_pnls)), 3),
            "best_trade": round(max(all_pnls), 3),
            "worst_trade": round(min(all_pnls), 3),
            "expectancy": round(expectancy, 3),
            "recent_10_wr": round(recent_wins / len(recent) * 100, 1) if recent else 0.0,
        }

    def get_signal_log(self):
        return list(reversed(self.signal_log[-20:]))

    # -- MAIN DETECTION -----------------------------------------------

    def detect_clean_aggressive_move(self, df, window_seconds=10, current_time=None, htf_trend_1h="NEUTRAL", htf_trend_4h="NEUTRAL", oi_change_pct=0.0, oi_spike=False, ob_imbalance=0.0):
        if df.empty or len(df) < self.min_trades:
            return self._empty_result()

        total_volume = df["qty"].sum()
        buy_volume = df[df["side"] == "BUY"]["qty"].sum()
        sell_volume = df[df["side"] == "SELL"]["qty"].sum()
        delta = buy_volume - sell_volume

        if total_volume < self.min_volume:
            return self._empty_result()

        avg_price = df["price"].mean()
        last_price = df.iloc[0]["price"]
        first_price = df.iloc[-1]["price"]
        price_change_pct = ((last_price - first_price) / first_price) * 100

        self.update_history(total_volume, delta, avg_price)
        self.update_mtf_history(df, "10s", 10)
        self.update_mtf_history(df, "30s", 30)
        self.update_mtf_history(df, "60s", 60)

        trend_dir, trend_str = self.update_trend(last_price, htf_trend_1h, htf_trend_4h)

        volume_zscore = self.calculate_clean_zscore(total_volume, self.volume_history)
        delta_ratio = abs(delta) / total_volume if total_volume > 0 else 0
        trades_per_sec = len(df) / window_seconds if window_seconds > 0 else 0
        whale_clustered, whale_concentration = self.detect_whale_cluster(df)
        has_acceleration = self.detect_momentum_acceleration(df)

        raw_buy = (
            volume_zscore >= self.volume_spike_threshold and
            delta_ratio >= self.delta_concentration and
            delta > 0 and
            price_change_pct >= self.min_price_move and
            trades_per_sec >= self.min_trades_per_sec
        )
        raw_sell = (
            volume_zscore >= self.volume_spike_threshold and
            delta_ratio >= self.delta_concentration and
            delta < 0 and
            price_change_pct <= -self.min_price_move and
            trades_per_sec >= self.min_trades_per_sec
        )

        mtf_confirmed_buy, mtf_score_buy, mtf_details_buy = False, 0, {}
        mtf_confirmed_sell, mtf_score_sell, mtf_details_sell = False, 0, {}
        if raw_buy:
            mtf_confirmed_buy, mtf_score_buy, mtf_details_buy = self.get_mtf_confirmation("BUY")
        if raw_sell:
            mtf_confirmed_sell, mtf_score_sell, mtf_details_sell = self.get_mtf_confirmation("SELL")

        trend_allows_buy, trend_reason_buy = self.trend_allows_signal("BUY")
        trend_allows_sell, trend_reason_sell = self.trend_allows_signal("SELL")

        aggressive_buy = raw_buy and mtf_confirmed_buy and trend_allows_buy
        aggressive_sell = raw_sell and mtf_confirmed_sell and trend_allows_sell

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

        mtf_score = max(mtf_score_buy, mtf_score_sell)
        if mtf_score == 3:
            confidence = min(100, confidence + 10)
        elif mtf_score == 2:
            confidence = min(100, confidence + 5)
        if aggressive_buy and trend_dir == "BULL":
            confidence = min(100, confidence + 8)
        if aggressive_sell and trend_dir == "BEAR":
            confidence = min(100, confidence + 8)

        # Open Interest scoring
        if oi_spike and oi_change_pct > 0 and raw_buy:
            confidence = min(100, confidence + 8)
        if oi_spike and oi_change_pct < 0 and raw_sell:
            confidence = min(100, confidence + 5)
        if oi_spike and oi_change_pct < 0 and raw_buy:
            confidence = max(0, confidence - 5)

        # Order book imbalance scoring
        if ob_imbalance > 0.15 and raw_buy:
            confidence = min(100, confidence + 7)
        elif ob_imbalance < -0.15 and raw_sell:
            confidence = min(100, confidence + 7)
        elif ob_imbalance > 0.15 and raw_sell:
            confidence = max(0, confidence - 5)
        elif ob_imbalance < -0.15 and raw_buy:
            confidence = max(0, confidence - 5)

        confidence = min(100, int(confidence))

        if confidence < 50:
            aggressive_buy = False
            aggressive_sell = False

        if aggressive_buy and current_time:
            self.log_signal("BUY", last_price, confidence)
            self.last_signal_time = current_time
        elif aggressive_sell and current_time:
            self.log_signal("SELL", last_price, confidence)
            self.last_signal_time = current_time

        return {
            "aggressive_buy": aggressive_buy,
            "aggressive_sell": aggressive_sell,
            "confidence": confidence,
            "raw_buy": raw_buy,
            "raw_sell": raw_sell,
            "mtf_confirmed": mtf_confirmed_buy or mtf_confirmed_sell,
            "mtf_score": mtf_score,
            "mtf_details": mtf_details_buy if raw_buy else mtf_details_sell,
            "trend_direction": trend_dir,
            "trend_strength": trend_str,
            "trend_allows_buy": trend_allows_buy,
            "trend_allows_sell": trend_allows_sell,
            "trend_reason_buy": trend_reason_buy,
            "trend_reason_sell": trend_reason_sell,
            "htf_trend_1h": htf_trend_1h,
            "htf_trend_4h": htf_trend_4h,
            "short_trend": getattr(self, "short_trend", "NEUTRAL"),
            "oi_change_pct": oi_change_pct,
            "oi_spike": oi_spike,
            "ob_imbalance": ob_imbalance,
            "ob_imbalance_pct": round(ob_imbalance * 100, 2),
            "metrics": {
                "volume_zscore": round(volume_zscore, 2),
                "delta_ratio": round(delta_ratio, 3),
                "price_change_pct": round(price_change_pct, 3),
                "trades_per_sec": round(trades_per_sec, 1),
                "whale_concentration": round(whale_concentration, 3),
                "has_acceleration": has_acceleration,
                "total_volume": round(total_volume, 4),
                "delta": round(delta, 4),
            }
        }

    def _empty_result(self):
        return {
            "aggressive_buy": False,
            "aggressive_sell": False,
            "confidence": 0,
            "raw_buy": False,
            "raw_sell": False,
            "mtf_confirmed": False,
            "mtf_score": 0,
            "mtf_details": {},
            "trend_direction": self.trend_direction,
            "trend_strength": self.trend_strength,
            "trend_allows_buy": True,
            "trend_allows_sell": True,
            "trend_reason_buy": "No data",
            "trend_reason_sell": "No data",
            "htf_trend_1h": "NEUTRAL",
            "htf_trend_4h": "NEUTRAL",
            "short_trend": "NEUTRAL",
            "oi_change_pct": 0.0,
            "oi_spike": False,
            "ob_imbalance": 0.0,
            "ob_imbalance_pct": 0.0,
            "metrics": {
                "volume_zscore": 0, "delta_ratio": 0,
                "price_change_pct": 0, "trades_per_sec": 0,
                "whale_concentration": 0, "has_acceleration": False,
                "total_volume": 0, "delta": 0,
            }
        }


CleanAggressiveMoveDetectorV2 = CleanAggressiveMoveDetector