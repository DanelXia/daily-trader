"""
技术指标计算引擎。支持 MA / MACD / RSI / 布林带 / ATR / K线形态识别。
优先使用 TA-Lib，如未安装则回退到纯 pandas/numpy 实现。
"""

import numpy as np
import pandas as pd

from src.data.fetcher import fetch_stock_kline
from config import (
    MA_PERIODS, MACD_PARAMS, RSI_PERIODS,
    BB_PERIOD, BB_STD, ATR_PERIOD, KLINE_LOOKBACK,
)

# ---------------------------------------------------------------------------
# 纯 numpy 指标实现（TA-Lib 回退方案）
# ---------------------------------------------------------------------------

def _sma(series: np.ndarray, period: int) -> np.ndarray:
    """简单移动平均"""
    result = np.full_like(series, np.nan, dtype=float)
    if len(series) >= period:
        cumsum = np.cumsum(np.insert(series, 0, 0))
        result[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
    return result


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均"""
    result = np.full_like(series, np.nan, dtype=float)
    if len(series) < period:
        return result
    multiplier = 2.0 / (period + 1)
    result[period - 1] = np.mean(series[:period])
    for i in range(period, len(series)):
        result[i] = (series[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD 计算"""
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line[~np.isnan(macd_line)], signal)
    # 对齐
    full_signal = np.full_like(macd_line, np.nan)
    start = np.argwhere(~np.isnan(macd_line))[signal - 1:][0][0] if len(np.argwhere(~np.isnan(macd_line))) > signal else 0
    if start > 0 and len(signal_line) > 0:
        full_signal[start:start + len(signal_line)] = signal_line[-min(len(signal_line), len(full_signal) - start):]
    histogram = macd_line - full_signal
    return macd_line, full_signal, histogram


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI 计算"""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = np.full_like(close, np.nan, dtype=float)
    avg_loss = np.full_like(close, np.nan, dtype=float)

    if len(close) > period:
        avg_gain[period] = np.mean(gain[1:period + 1])
        avg_loss[period] = np.mean(loss[1:period + 1])
        for i in range(period + 1, len(close)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period

    rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
    rsi_values = 100 - (100 / (1 + rs))
    rsi_values[:period] = np.nan
    return rsi_values


def _bbands(close: np.ndarray, period: int = 20, nbdev: float = 2.0):
    """布林带"""
    middle = _sma(close, period)
    std = np.full_like(close, np.nan, dtype=float)
    for i in range(period - 1, len(close)):
        std[i] = np.std(close[i - period + 1:i + 1], ddof=1)
    upper = middle + nbdev * std
    lower = middle - nbdev * std
    return upper, middle, lower


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """ATR 计算"""
    tr = np.maximum(
        high - low,
        np.maximum(
            np.abs(high - np.roll(close, 1)),
            np.abs(low - np.roll(close, 1))
        )
    )
    tr[0] = high[0] - low[0]
    atr_values = _ema(tr, period)
    # Use Wilder's smoothing: atr[period] = mean(tr[1:period+1]), then EMA
    result = np.full_like(close, np.nan, dtype=float)
    if len(close) > period:
        result[period] = np.mean(tr[1:period + 1])
        for i in range(period + 1, len(close)):
            result[i] = (result[i - 1] * (period - 1) + tr[i]) / period
    return result


# ---------------------------------------------------------------------------
# K线形态识别（简化实现）
# ---------------------------------------------------------------------------

def _detect_doji(df: pd.DataFrame, threshold: float = 0.001) -> list[str]:
    """检测十字星"""
    body = np.abs(df["close"].values - df["open"].values)
    spread = df["high"].values - df["low"].values
    doji_mask = (body / np.where(spread == 0, 1e-10, spread)) < threshold
    dates = df["date"].values[doji_mask]
    return [f"Doji({d})" for d in dates[-5:]]  # 最近5个


def _detect_hammer(df: pd.DataFrame) -> list[str]:
    """检测锤子线"""
    body = np.abs(df["close"].values - df["open"].values)
    lower_shadow = np.minimum(df["open"].values, df["close"].values) - df["low"].values
    upper_shadow = df["high"].values - np.maximum(df["open"].values, df["close"].values)
    spread = df["high"].values - df["low"].values

    # 下影线 >= 2倍实体，上影线很小
    hammer_mask = (
        (lower_shadow >= 2 * body) &
        (upper_shadow <= 0.3 * body) &
        (spread > 0)
    )
    dates = df["date"].values[hammer_mask]
    return [f"Hammer({d})" for d in dates[-5:]]


def _detect_engulfing(df: pd.DataFrame) -> list[str]:
    """检测吞没形态"""
    open_ = df["open"].values
    close_ = df["close"].values
    patterns = []

    for i in range(1, len(open_)):
        prev_body = close_[i - 1] - open_[i - 1]
        curr_body = close_[i] - open_[i]

        # 多头吞没：前阴后阳，实体放大
        if prev_body < 0 and curr_body > 0 and abs(curr_body) > abs(prev_body):
            patterns.append(f"BullishEngulfing({df['date'].values[i]})")
        # 空头吞没：前阳后阴，实体放大
        elif prev_body > 0 and curr_body < 0 and abs(curr_body) > abs(prev_body):
            patterns.append(f"BearishEngulfing({df['date'].values[i]})")

    return patterns[-5:]


# ===================================================================
# TechnicalAnalyzer
# ===================================================================

class TechnicalAnalyzer:
    """技术分析引擎"""

    def compute_all(self, code: str) -> dict | None:
        """
        计算单只股票所有技术指标。
        返回 dict 或 None（数据不足时）。
        """
        df = fetch_stock_kline(code, days=KLINE_LOOKBACK)
        if df is None or df.empty or len(df) < 60:
            print(f"    [{code}] K线数据不足 ({len(df) if df is not None else 0} 条)")
            return None

        close = df["close"].astype(float).values
        high = df["high"].astype(float).values
        low = df["low"].astype(float).values
        volume = df["volume"].astype(float).values

        # -- MA --
        ma = {}
        for p in MA_PERIODS:
            ma[f"ma{p}"] = round(float(_sma(close, p)[-1]), 2)

        # -- MACD --
        macd_line, signal_line, histogram = _macd(close, *MACD_PARAMS)
        last_valid = np.argwhere(~np.isnan(macd_line))[-1][0] if np.any(~np.isnan(macd_line)) else -1

        # -- RSI --
        rsi = {}
        for p in RSI_PERIODS:
            rsi_arr = _rsi(close, p)
            rsi[f"rsi{p}"] = round(float(rsi_arr[-1]), 1) if not np.isnan(rsi_arr[-1]) else None

        # -- 布林带 --
        bb_upper, bb_middle, bb_lower = _bbands(close, BB_PERIOD, BB_STD)

        # -- ATR --
        atr_arr = _atr(high, low, close, ATR_PERIOD)

        # -- 量能 --
        vol_20d = np.mean(volume[-20:]) if len(volume) >= 20 else np.mean(volume)
        vol_ratio = round(float(volume[-1] / vol_20d), 2) if vol_20d > 0 else 1.0
        vol_5d_trend = np.mean(volume[-5:]) > np.mean(volume[-10:-5]) if len(volume) >= 10 else False

        # 涨跌量能对比
        up_vol = np.sum(volume[-5:][np.diff(close[-6:]) > 0]) if len(close) >= 6 else 0
        down_vol = np.sum(volume[-5:][np.diff(close[-6:]) < 0]) if len(close) >= 6 else 0
        up_down_vol = round(float(up_vol / down_vol), 2) if down_vol > 0 else 1.0

        # -- K线形态 --
        patterns = []
        patterns.extend(_detect_doji(df.tail(20)))
        patterns.extend(_detect_hammer(df.tail(20)))
        patterns.extend(_detect_engulfing(df.tail(20)))

        # -- 支撑/阻力 --
        supports, resistances = self._find_levels(df)

        return {
            "code": code,
            "price": round(float(close[-1]), 2),
            "ma": ma,
            "macd": {
                "macd": round(float(macd_line[-1]), 3) if last_valid >= 0 else None,
                "signal": round(float(signal_line[-1]), 3) if last_valid >= 0 else None,
                "histogram": round(float(histogram[-1]), 3) if last_valid >= 0 else None,
            },
            "rsi": rsi,
            "bb": {
                "upper": round(float(bb_upper[-1]), 2),
                "middle": round(float(bb_middle[-1]), 2),
                "lower": round(float(bb_lower[-1]), 2),
            },
            "atr14": round(float(atr_arr[-1]), 2) if not np.isnan(atr_arr[-1]) else round(float(np.mean(high - low)), 2),
            "volume": {
                "ratio": vol_ratio,
                "trend_up": bool(vol_5d_trend),
                "up_down_ratio": up_down_vol,
            },
            "patterns": patterns,
            "support_levels": supports,
            "resistance_levels": resistances,
        }

    def _find_levels(self, df: pd.DataFrame) -> tuple[list[float], list[float]]:
        """查找支撑和阻力位"""
        close = df["close"].astype(float).values
        price = close[-1]

        # 简单方法：使用MA60和布林下轨作为支撑，前高作为阻力
        ma60 = _sma(close, 60)[-1] if len(close) >= 60 else close[-1]
        ma20 = _sma(close, 20)[-1] if len(close) >= 20 else close[-1]
        _, bb_mid, bb_low = _bbands(close, BB_PERIOD, BB_STD)

        supports = sorted([round(float(x), 2) for x in [ma60, float(bb_low[-1]), float(bb_mid[-1])]
                          if not np.isnan(x) and x < price], reverse=True)
        if not supports:
            supports = [round(price * 0.95, 2)]

        # 阻力：BB上轨、近期高点、整数关口
        highs = df["high"].astype(float).values[-20:]
        recent_high = float(np.max(highs))
        bb_up = float(_bbands(close, BB_PERIOD, BB_STD)[0][-1])

        resistances = sorted([round(float(x), 2) for x in [bb_up, recent_high]
                             if not np.isnan(x) and x > price])
        if not resistances:
            resistances = [round(price * 1.10, 2)]

        return supports[:3], resistances[:3]
