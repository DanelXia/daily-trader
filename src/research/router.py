"""
场景路由器（RegimeRouter）。

将检测到的市场场景映射到对应的选股模型，并通过防抖机制避免频繁切换。

映射关系（Step 1 先输出 model 名称字符串，Step 2 再接入实际选股模型）：
  BULL      -> momentum      （动量突破）
  BEAR      -> defensive     （防守红利）
  SIDEWAYS  -> mean_reversion（均值回归）
"""

import json
from config import (
    REGIME_DEBOUNCE_DAYS,
    REGIME_STRONG_SIGNAL_RET,
    REGIME_HISTORY_FILE,
    REGIME_STATE_FILE,
)

REGIME_MODEL_MAP = {
    "BULL": "momentum",
    "BEAR": "defensive",
    "SIDEWAYS": "mean_reversion",
}

# 目标仓位上限（HIGHVOL 时再 ×0.5）
REGIME_POSITION_MAP = {
    "BULL": 0.8,
    "BEAR": 0.2,
    "SIDEWAYS": 0.5,
}


class RegimeRouter:
    """场景路由器：regime → 模型映射 + 防抖确认。"""

    def __init__(self, history_file=None, state_file=None):
        self.history_file = history_file or REGIME_HISTORY_FILE
        self.state_file = state_file or REGIME_STATE_FILE

    def route(self, detection: dict) -> dict:
        """根据检测结果输出当前确认场景与模型。返回完整状态字典。"""
        date = detection["date"]
        detected = detection["regime"]
        highvol = detection["highvol"]
        features = detection["features"]

        history = self._load_history()
        state = self._load_state()
        prev_confirmed = state.get("regime") if state else None

        confirmed = self._apply_debounce(detected, prev_confirmed, features, history, date)

        # 追加今日检测结果到历史（记录的是"检测 regime"，用于防抖）
        history.append({"date": date, "regime": detected})
        self._save_history(history)

        result = {
            "date": date,
            "regime": confirmed,
            "detected_regime": detected,
            "highvol": highvol,
            "model": REGIME_MODEL_MAP.get(confirmed, "unknown"),
            "target_position_pct": self._position_pct(confirmed, highvol),
            "features": features,
        }
        self._save_state(result)
        return result

    # ------------------------------------------------------------------
    # 防抖
    # ------------------------------------------------------------------
    def _apply_debounce(self, detected, prev_confirmed, features, history, date) -> str:
        """决定本次确认的场景。"""
        if prev_confirmed is None or detected == prev_confirmed:
            return detected

        # 强信号穿透：ret20 绝对值超阈值，跳过防抖立即切换
        ret20 = features.get("ret20") or 0.0
        if abs(ret20) >= REGIME_STRONG_SIGNAL_RET:
            return detected

        # 防抖：最近 N 日（含今日）连续检测到同一新场景才切换
        n = REGIME_DEBOUNCE_DAYS
        recent = [h["regime"] for h in history[-(n - 1):]] + [detected]
        if len(recent) >= n and all(r == detected for r in recent):
            return detected

        return prev_confirmed

    # ------------------------------------------------------------------
    # 仓位
    # ------------------------------------------------------------------
    @staticmethod
    def _position_pct(regime: str, highvol: bool) -> float:
        pct = REGIME_POSITION_MAP.get(regime, 0.5)
        if highvol:
            pct *= 0.5
        return round(max(pct, 0.1), 2)

    # ------------------------------------------------------------------
    # 文件读写
    # ------------------------------------------------------------------
    def _load_history(self) -> list:
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_history(self, history: list):
        # 只保留最近 60 条，防止文件无限增长
        history = history[-60:]
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def _load_state(self) -> dict | None:
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save_state(self, state: dict):
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
