"""
市场场景识别器（MarketRegimeDetector）。

快慢两层信号：
  快信号（预警层）：跌破 MA20 / MA20 拐头向下 —— 提前提示趋势可能转弱。
  慢信号（确认层）：MA20/MA60 排列 + 20 日涨跌幅 —— 确认场景 BULL/BEAR/SIDEWAYS。
  叠加层（overlay）：波动率 250 日分位数 > 阈值 → HIGHVOL 标记。

确认层决策规则（依据 20 年实测：趋势确认周期地板 ≈ 29~30 个交易日）：
  - 空头排列 且 ret20 < -3%  → BEAR
  - 多头排列 且 ret20 > +3%  → BULL
  - 其余                     → SIDEWAYS
"""

import numpy as np
import pandas as pd

from config import (
    REGIME_INDEX_CODE,
    REGIME_RET20_BEAR_THRESHOLD,
    REGIME_RET20_BULL_THRESHOLD,
    REGIME_HIGHVOL_PCT,
    REGIME_WARNING_MA,
)
from src.data.fetcher import fetch_index_kline


class MarketRegimeDetector:
    """市场场景识别器。检测当前市场处于 BULL / BEAR / SIDEWAYS（可叠加 HIGHVOL）。"""

    def __init__(self, code: str = REGIME_INDEX_CODE):
        self.code = code

    def detect(self) -> dict | None:
        """检测当前场景，返回完整场景字典；数据不足时返回 None。"""
        df = fetch_index_kline(self.code, days=380)
        if df is None or df.empty or len(df) < 60:
            return None

        close = df["close"].astype(float)
        n = len(df)
        last = df.iloc[-1]
        date_str = last["date"].strftime("%Y-%m-%d")

        # 均线
        ma20 = close.rolling(REGIME_WARNING_MA).mean()
        ma60 = close.rolling(60).mean()

        # 20 日涨跌幅
        ret20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if n > 20 else 0.0

        # MA 排列：多头 / 空头 / 缠绕（要求 MA20 方向一致）
        ma20_now = float(ma20.iloc[-1])
        ma60_now = float(ma60.iloc[-1])
        ma20_prev = float(ma20.iloc[-2]) if n > 1 else ma20_now
        if ma20_now > ma60_now and ma20_now > ma20_prev:
            arrange = "多头"
        elif ma20_now < ma60_now and ma20_now < ma20_prev:
            arrange = "空头"
        else:
            arrange = "缠绕"

        # 波动率 250 日分位（HIGHVOL overlay 用）
        vol20 = close.pct_change().rolling(20).std() * np.sqrt(250)
        vol_pct = self._rolling_percentile_rank(vol20, 250)

        # 确认层 regime
        if arrange == "空头" and ret20 < REGIME_RET20_BEAR_THRESHOLD:
            regime = "BEAR"
        elif arrange == "多头" and ret20 > REGIME_RET20_BULL_THRESHOLD:
            regime = "BULL"
        else:
            regime = "SIDEWAYS"

        # HIGHVOL overlay
        highvol = bool(vol_pct is not None and vol_pct > REGIME_HIGHVOL_PCT)

        # 预警层
        warning = self._detect_warning(close, ma20)

        features = {
            "close": round(float(close.iloc[-1]), 2),
            "ret20": round(ret20, 2),
            "arrange": arrange,
            "ma20": round(ma20_now, 2),
            "ma60": round(ma60_now, 2),
            "vol_pct": round(vol_pct, 3) if vol_pct is not None else None,
            "warning": warning,
        }

        return {
            "date": date_str,
            "regime": regime,
            "highvol": highvol,
            "features": features,
        }

    # ------------------------------------------------------------------
    # 预警层
    # ------------------------------------------------------------------
    def _detect_warning(self, close: pd.Series, ma20: pd.Series) -> str | None:
        """预警层：跌破 MA20 或 MA20 拐头向下。返回中文预警描述或 None。"""
        n = len(close)
        if n < 21:
            return None
        c_now = float(close.iloc[-1])
        c_prev = float(close.iloc[-2])
        ma20_now = float(ma20.iloc[-1])
        ma20_prev = float(ma20.iloc[-2])

        broke_below = c_prev >= ma20_prev and c_now < ma20_now
        ma20_turning = ma20_now < ma20_prev

        if broke_below and ma20_turning:
            return "跌破MA20且MA20拐头向下"
        if broke_below:
            return "跌破MA20"
        if ma20_turning:
            return "MA20拐头向下"
        return None

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _rolling_percentile_rank(series: pd.Series, window: int) -> float | None:
        """当前值在最近 window 个值中的分位数（0~1，排除自身）。"""
        vals = series.dropna()
        if len(vals) < window:
            return None
        recent = vals.iloc[-window:]
        cur = recent.iloc[-1]
        return float((recent.iloc[:-1] < cur).sum() / (window - 1))
