"""
交易信号引擎。基于技术指标综合打分，判定买卖操作并计算具体点位。
"""

import numpy as np

from config import DEFAULT_ACCOUNT_CAPITAL, MAX_POSITION_PCT, RISK_PER_TRADE_PCT


class TradingSignalEngine:
    """交易信号引擎"""

    def analyze(self, code: str, name: str, indicators: dict) -> dict:
        """
        综合分析，返回完整交易建议。
        indicators 来自 TechnicalAnalyzer.compute_all()
        """
        price = indicators["price"]

        # 各维度评分
        trend_score = self._score_trend(indicators, price)
        momentum_score = self._score_momentum(indicators)
        volume_score = self._score_volume(indicators)
        sr_score = self._score_support_resistance(indicators, price)
        pattern_score = self._score_patterns(indicators)

        total = trend_score + momentum_score + volume_score + sr_score + pattern_score

        # 判定操作
        action, action_cn = self._determine_action(total)

        # 计算点位
        entry = self._calc_entry(indicators, price, action)
        stop_loss = self._calc_stop_loss(indicators, price)
        take_profit = self._calc_take_profit(indicators, price)

        # 仓位建议
        atr = indicators.get("atr14", price * 0.03)
        position_pct = min(
            MAX_POSITION_PCT * 100,
            RISK_PER_TRADE_PCT * DEFAULT_ACCOUNT_CAPITAL / max(2 * atr * 100, 1) * 100
        )

        # 风险评估
        risk = self._assess_risk(total, indicators)

        # 关键信号摘要
        key_notes = self._generate_notes(indicators, price, total)

        return {
            "code": code,
            "name": name,
            "trading_score": round(total, 1),
            "action": action,
            "action_cn": action_cn,
            "entry": {
                "price": entry,
                "type": "limit" if action in ("strong_buy", "buy") else "market",
                "suggestion": self._entry_suggestion(action, entry, price),
            },
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "position_pct": round(position_pct, 1),
            "risk_assessment": risk,
            "indicators": {
                "ma5": indicators["ma"].get("ma5"),
                "ma10": indicators["ma"].get("ma10"),
                "ma20": indicators["ma"].get("ma20"),
                "ma60": indicators["ma"].get("ma60"),
                "macd": indicators["macd"].get("macd"),
                "signal": indicators["macd"].get("signal"),
                "histogram": indicators["macd"].get("histogram"),
                "rsi6": indicators["rsi"].get("rsi6"),
                "rsi14": indicators["rsi"].get("rsi14"),
                "bb_upper": indicators["bb"].get("upper"),
                "bb_middle": indicators["bb"].get("middle"),
                "bb_lower": indicators["bb"].get("lower"),
                "atr14": indicators.get("atr14"),
                "volume_ratio": indicators["volume"].get("ratio"),
            },
            "support_levels": indicators.get("support_levels", []),
            "resistance_levels": indicators.get("resistance_levels", []),
            "key_notes": key_notes,
        }

    # ==================================================================
    # 趋势评分 (30)
    # ==================================================================
    def _score_trend(self, ind: dict, price: float) -> float:
        score = 0.0
        ma = ind["ma"]

        # Price vs MAs
        score += 3 if price > ma.get("ma5", 0) else 0
        score += 3 if price > ma.get("ma10", 0) else 0
        score += 5 if price > ma.get("ma20", 0) else 0
        score += 7 if price > ma.get("ma60", 0) else 0

        # MA 排列
        score += 4 if ma.get("ma5", 0) > ma.get("ma10", 0) else 0
        score += 3 if ma.get("ma10", 0) > ma.get("ma20", 0) else 0
        score += 5 if ma.get("ma20", 0) > ma.get("ma60", 0) else 0

        return score

    # ==================================================================
    # 动量评分 (20)
    # ==================================================================
    def _score_momentum(self, ind: dict) -> float:
        score = 0.0
        macd = ind["macd"]
        macd_val = macd.get("macd") or 0
        sig_val = macd.get("signal") or 0
        hist = macd.get("histogram") or 0

        # MACD 信号
        if macd_val > sig_val and macd_val > 0:
            score += 8
        elif macd_val > sig_val and macd_val <= 0:
            score += 3

        # 柱状图方向
        if hist > 0:
            score += 5
        elif hist < -0.5:
            score -= 5

        # RSI 位置
        rsi14 = ind["rsi"].get("rsi14") or 50
        if 40 <= rsi14 <= 70:
            score += 7
        elif 30 <= rsi14 < 40:
            score += 3
        elif rsi14 > 85:
            score += 0
        else:
            score += 2

        return score

    # ==================================================================
    # 量能评分 (15)
    # ==================================================================
    def _score_volume(self, ind: dict) -> float:
        score = 0.0
        vol = ind["volume"]
        ratio = vol.get("ratio", 1.0)

        if ratio > 1.2 and vol.get("trend_up"):
            score += 8
        elif ratio > 1.0:
            score += 4

        if vol.get("trend_up"):
            score += 4

        up_down = vol.get("up_down_ratio", 1.0)
        if up_down > 1.2:
            score += 3
        elif up_down > 1.0:
            score += 1

        return score

    # ==================================================================
    # 支撑/阻力评分 (20)
    # ==================================================================
    def _score_support_resistance(self, ind: dict, price: float) -> float:
        score = 0.0
        supports = ind.get("support_levels", [])
        resistances = ind.get("resistance_levels", [])

        # 最近支撑的距离
        if supports:
            nearest_sup = max(supports)
            dist_sup = (price - nearest_sup) / price
            if dist_sup < 0.03:
                score += 7
            elif dist_sup < 0.05:
                score += 4

        # 最近阻力的距离（空间越大越好）
        if resistances:
            nearest_res = min(resistances)
            dist_res = (nearest_res - price) / price
            if dist_res > 0.05:
                score += 7
            elif dist_res > 0.03:
                score += 4
            else:
                score += 1

        # 布林带位置
        bb = ind["bb"]
        bb_range = bb["upper"] - bb["lower"]
        if bb_range > 0:
            pos = (price - bb["lower"]) / bb_range
            if pos < 0.3:
                score += 6
            elif pos < 0.7:
                score += 3
            else:
                score += 1

        return score

    # ==================================================================
    # K线形态评分 (15)
    # ==================================================================
    def _score_patterns(self, ind: dict) -> float:
        score = 0.0
        patterns = ind.get("patterns", [])

        for p in patterns:
            name = p.split("(")[0] if "(" in p else p
            if "BullishEngulfing" in name:
                score += 8
            elif "Hammer" in name:
                score += 6
            elif "Doji" in name:
                score += 3
            elif "BearishEngulfing" in name:
                score -= 5

        return max(0, min(15, score))

    # ==================================================================
    # 操作判定
    # ==================================================================
    def _determine_action(self, total: float) -> tuple[str, str]:
        if total >= 75:
            return "strong_buy", "强烈买入"
        elif total >= 60:
            return "buy", "买入"
        elif total >= 45:
            return "hold", "观望"
        elif total >= 30:
            return "reduce", "减仓"
        else:
            return "strong_sell", "强烈卖出"

    # ==================================================================
    # 点位计算
    # ==================================================================
    def _calc_entry(self, ind: dict, price: float, action: str) -> float:
        if action in ("strong_buy", "buy"):
            # 建议在MA20附近或当前价买入
            ma20 = ind["ma"].get("ma20", price)
            if price > ma20 * 1.02:
                return round(ma20 * 1.01, 2)  # 略高于MA20
            return round(price, 2)
        return round(price, 2)

    def _calc_stop_loss(self, ind: dict, price: float) -> float:
        atr = ind.get("atr14", price * 0.03)
        supports = ind.get("support_levels", [])

        # ATR止损
        atr_sl = price - 2 * atr
        # 支撑位止损
        support_sl = max(supports) * 0.97 if supports else price * 0.93
        # 硬止损（至少7%）
        hard_sl = price * 0.93

        return round(max(atr_sl, support_sl, hard_sl), 2)

    def _calc_take_profit(self, ind: dict, price: float) -> dict:
        resistances = ind.get("resistance_levels", [])
        bb_upper = ind["bb"].get("upper", price * 1.15)

        # TP1: 第一阻力位，约10-15%收益
        if resistances:
            tp1 = min(resistances[0], price * 1.15)
        else:
            tp1 = price * 1.10
        tp1_pct = round((tp1 - price) / price * 100, 1)

        # TP2: BB上轨或远期阻力，约20-30%收益
        if len(resistances) > 1:
            tp2 = min(resistances[-1], bb_upper, price * 1.30)
        else:
            tp2 = min(bb_upper, price * 1.25)
        tp2_pct = round((tp2 - price) / price * 100, 1)

        return {
            "tp1": round(tp1, 2),
            "tp1_pct": tp1_pct,
            "tp1_action": "减仓1/3",
            "tp2": round(tp2, 2),
            "tp2_pct": tp2_pct,
            "tp2_action": "剩余全部平仓",
        }

    # ==================================================================
    # 风险评估
    # ==================================================================
    def _assess_risk(self, total: float, ind: dict) -> str:
        atr = ind.get("atr14", 0)
        price = ind.get("price", 100)
        vol_ratio = ind["volume"].get("ratio", 1.0)

        if atr / price > 0.05 or vol_ratio > 3:
            return "高"
        elif total < 45 or (atr / price > 0.03):
            return "中高"
        elif total < 60:
            return "中等"
        return "低"

    def _entry_suggestion(self, action: str, entry: float, price: float) -> str:
        if action in ("strong_buy", "buy"):
            if entry >= price:
                return f"可现价 {price} 买入"
            return f"建议挂单 {entry} 附近买入"
        elif action == "hold":
            return "暂时观望，等待明确信号"
        elif action == "reduce":
            return "建议减仓，逢高卖出"
        return "建议清仓离场"

    def _generate_notes(self, ind: dict, price: float, total: float) -> list[str]:
        notes = []
        ma = ind["ma"]
        macd = ind["macd"]
        rsi = ind["rsi"]
        vol = ind["volume"]

        # 多头排列
        if ma.get("ma5", 0) > ma.get("ma10", 0) > ma.get("ma20", 0) > ma.get("ma60", 0):
            notes.append("均线多头排列，趋势向好")
        elif price > ma.get("ma60", 0) and ma.get("ma20", 0) > ma.get("ma60", 0):
            notes.append("中期趋势向好，短周期待确认")

        # MACD
        if (macd.get("macd") or 0) > (macd.get("signal") or 0):
            hist = macd.get("histogram") or 0
            if hist > 0:
                notes.append("MACD金叉，动能增强")
            else:
                notes.append("MACD金叉但动能衰减，关注是否死叉")
        else:
            notes.append("MACD死叉，短期偏空")

        # RSI
        rsi14 = rsi.get("rsi14") or 50
        if rsi14 > 80:
            notes.append(f"RSI={rsi14}，严重超买，注意回调风险")
        elif rsi14 < 30:
            notes.append(f"RSI={rsi14}，超卖区域，可能存在反弹机会")

        # 量能
        ratio = vol.get("ratio", 1.0)
        if ratio > 1.5:
            notes.append(f"量比={ratio}，放量明显")
        elif ratio < 0.5:
            notes.append(f"量比={ratio}，缩量")

        # 形态
        patterns = ind.get("patterns", [])
        bullish = [p for p in patterns if "Bullish" in p or "Hammer" in p]
        bearish = [p for p in patterns if "Bearish" in p]
        if bullish:
            notes.append(f"出现看涨形态: {', '.join(bullish)}")
        if bearish:
            notes.append(f"出现看跌形态: {', '.join(bearish)}")

        return notes
