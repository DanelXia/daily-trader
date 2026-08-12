"""
连板主线选股模型

当市场缺乏明确主线板块，但出现多只 3 连板以上的跳空涨停股票时，
自动识别并输出高动能标的列表。只负责发现股票，不负责技术打分。
"""

from datetime import datetime

import numpy as np
import pandas as pd

from config import (
    CONSECUTIVE_ZT_MIN_COUNT,
    CONSECUTIVE_ZT_MIN_STOCKS,
    CONSECUTIVE_ZT_MAIN_THREAD_THRESHOLD,
    CONSECUTIVE_ZT_KLINE_LOOKBACK,
    CONSECUTIVE_ZT_LIMIT_UP_TOLERANCE,
)
from src.data.fetcher import (
    fetch_limit_up_pool,
    fetch_previous_zt_pool,
    fetch_stock_kline,
)


class ConsecutiveZtScanner:
    """连板主线扫描器 — 全市场扫描跳空连板股"""

    def __init__(self):
        self.today_str = datetime.now().strftime("%Y%m%d")

    def scan(self) -> dict | None:
        """
        扫描全市场，返回符合条件的跳空连板股列表。

        返回 dict 包含 trigger / no_main_thread / industry_distribution / stocks / summary
        未触发或数据获取失败返回 None
        """
        # 盘中不运行（需要当日 K 线确认）
        if datetime.now().hour < 15:
            return None

        # 1. 获取涨停池
        pool = fetch_limit_up_pool()
        if pool is None or pool.empty:
            return None

        # 2. 过滤连板数 >= MIN_COUNT
        candidates = pool[pool["consecutive_count"] >= CONSECUTIVE_ZT_MIN_COUNT].copy()
        if candidates.empty:
            return None

        # 3. K 线验证跳空连板
        gap_stocks = []
        for _, row in candidates.iterrows():
            code = str(row["code"]).strip()
            name = str(row.get("name", "")).strip()
            if not code:
                continue
            result = self._verify_gap_limit_up(code, name, int(row["consecutive_count"]))
            if result is None:
                continue
            is_valid, gap_days = result
            if not is_valid:
                continue

            seal_time = str(row.get("first_seal_time", "")).strip()
            industry = str(row.get("industry", "")).strip()
            limit_up_type = self._classify(
                gap_days, seal_time, int(row["consecutive_count"])
            )

            gap_stocks.append({
                "code": code,
                "name": str(row["name"]).strip(),
                "consecutive_count": int(row["consecutive_count"]),
                "gap_days": gap_days,
                "first_seal_time": seal_time,
                "industry": industry,
                "limit_up_type": limit_up_type,
            })

        # 4. 检查是否触发模型
        if len(gap_stocks) < CONSECUTIVE_ZT_MIN_STOCKS:
            return None

        # 5. 行业聚合 & 主线判断
        industry_dist = {}
        for s in gap_stocks:
            ind = s["industry"] or "未知"
            industry_dist[ind] = industry_dist.get(ind, 0) + 1

        total = len(gap_stocks)
        max_ind_pct = max(industry_dist.values()) / total if industry_dist else 0
        no_main_thread = max_ind_pct < CONSECUTIVE_ZT_MAIN_THREAD_THRESHOLD

        main_industry = None
        if not no_main_thread:
            main_industry = max(industry_dist, key=industry_dist.get)

        # 6. 生成摘要
        industry_list = "、".join(
            f"{ind}({cnt}只)" for ind, cnt in
            sorted(industry_dist.items(), key=lambda x: -x[1])[:5]
        )
        if no_main_thread:
            summary = (
                f"市场暂无明确主线，存在 {total} 只 {CONSECUTIVE_ZT_MIN_COUNT}+连板跳空股，"
                f"分布在 {len(industry_dist)} 个行业：{industry_list}"
            )
            trading_suggestion = (
                "连板股分散在多个行业，未形成一致主线。"
                "建议控制仓位，关注最先分歧回封的龙头，警惕高位炸板风险。"
                "若后续行业集中度提升，可适度加大仓位。"
            )
        else:
            summary = (
                f"主线明确：{main_industry}（占比 {max_ind_pct:.0%}），"
                f"共 {total} 只跳空连板股，集中在 {industry_list}"
            )
            trading_suggestion = (
                f"连板股集中在 {main_industry} 行业，主线明确。"
                "可关注板块内龙头分歧后的低吸机会，注意板块一致性风险。"
            )

        return {
            "triggered": True,
            "no_main_thread": no_main_thread,
            "main_thread_industry": main_industry,
            "industry_distribution": dict(
                sorted(industry_dist.items(), key=lambda x: -x[1])
            ),
            "stocks": gap_stocks,
            "summary": summary,
            "trading_suggestion": trading_suggestion,
        }

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _verify_gap_limit_up(
        self, code: str, name: str, consecutive_count: int
    ) -> tuple | None:
        """
        验证股票最近 N 天是否为跳空涨停。

        返回 (is_valid: bool, gap_days: int) 或 None（数据不足）。
        """
        df = fetch_stock_kline(code, days=CONSECUTIVE_ZT_KLINE_LOOKBACK)
        if df is None or df.empty or len(df) < CONSECUTIVE_ZT_MIN_COUNT + 1:
            return None

        df = df.sort_values("date", ascending=True).reset_index(drop=True)

        open_ = df["open"].astype(float).values
        high = df["high"].astype(float).values
        low = df["low"].astype(float).values
        close = df["close"].astype(float).values

        # 从最近一天开始往前验证
        limit_up_days = 0
        gap_days = 0
        for i in range(len(close) - 1, -1, -1):
            if i == 0:
                break
            prev_close = close[i - 1]
            limit_up_price = self._calc_limit_up_price(prev_close, code, name)

            # 检查是否封涨停（收盘价 ≈ 涨停价）
            tol = CONSECUTIVE_ZT_LIMIT_UP_TOLERANCE
            is_sealed = self._near(close[i], limit_up_price, tol)

            if not is_sealed:
                break

            limit_up_days += 1

            # 检查跳空：今日最低 > 昨日收盘
            if low[i] > prev_close:
                gap_days += 1

            if limit_up_days >= consecutive_count:
                break

        if limit_up_days >= CONSECUTIVE_ZT_MIN_COUNT:
            return (True, gap_days)

        return (False, 0)

    def _calc_limit_up_price(
        self, prev_close: float, code: str, name: str = ""
    ) -> float:
        """计算涨停价。主板 10%，科创板/创业板 20%，ST 5%，北交所 30%。"""
        # ST 股票（名称含 ST）
        if "ST" in name.upper() or "*ST" in name.upper():
            return round(prev_close * 1.05, 2)
        # 科创板 688 / 创业板 300
        if code.startswith("688") or code.startswith("300"):
            return round(prev_close * 1.20, 2)
        # 北交所: 8 开头（非 688，8xx）
        if code.startswith("8"):
            return round(prev_close * 1.30, 2)
        # 主板
        return round(prev_close * 1.10, 2)

    def _classify(
        self, gap_days: int, seal_time: str, consecutive_count: int
    ) -> str:
        """分类涨停类型。一字板=开盘即封死全日未开；跳空连板=有缺口但非一字"""
        is_seal_at_open = bool(seal_time and seal_time.replace(":", "") <= "092510")
        if gap_days >= consecutive_count and is_seal_at_open:
            return "一字板（跳空）"
        elif gap_days > 0 and is_seal_at_open:
            return f"一字板（{gap_days}/{consecutive_count}跳空）"
        elif gap_days > 0:
            return f"跳空连板({gap_days}/{consecutive_count}天)"
        else:
            if is_seal_at_open:
                return "秒板"
            return "换手板"

    @staticmethod
    def _near(a: float, b: float, tolerance: float) -> bool:
        """判断两个浮点数是否在容差范围内相等（相对容差）"""
        if b == 0:
            return abs(a - b) < 0.01
        return abs(a - b) / abs(b) <= tolerance


class DivergenceTracker:
    """连板分歧回调跟踪器 — 寻找断板后完成首次健康回调的股票"""

    def __init__(self):
        self.today_str = datetime.now().strftime("%Y%m%d")

    def scan(self) -> dict | None:
        """
        扫描全市场，寻找曾3+连板 → 已断板 → 现处回调企稳的股票。

        回溯近12个交易日涨停池，与断板回调10天上限对齐。

        返回 {"triggered": bool, "stocks": [...], "summary": str} 或 None
        """
        if datetime.now().hour < 15:
            return None

        # 1. 获取今天涨停池（用于排除仍在连板的股票）
        today_pool = fetch_limit_up_pool()
        if today_pool is not None and not today_pool.empty:
            still_zt_codes = set(today_pool["code"].astype(str).str.strip())
        else:
            still_zt_codes = set()

        # 2. 收集近N个交易日有过3+连板的股票（回溯扫描）
        from datetime import timedelta
        seen_codes = set()
        broken_candidates = []

        for days_back in range(1, 13):  # 回溯1~12天，对齐断板10天上限
            lookback_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
            if days_back == 1:
                pool = fetch_previous_zt_pool()
            else:
                pool = fetch_limit_up_pool(date=lookback_date)

            if pool is None or pool.empty:
                continue

            prev_zt = pool[pool["consecutive_count"] >= CONSECUTIVE_ZT_MIN_COUNT]
            for _, row in prev_zt.iterrows():
                code = str(row["code"]).strip()
                if code in seen_codes:
                    continue
                if code in still_zt_codes:
                    continue  # 仍在连板中
                seen_codes.add(code)
                broken_candidates.append({
                    "code": code,
                    "name": str(row.get("name", "")).strip(),
                    "prev_zt_days": int(row["consecutive_count"]),
                    "prev_price": float(row["latest_price"]),
                    "industry": str(row.get("industry", "")).strip(),
                })

        if not broken_candidates:
            return None

        # 3. 对每只断板股，拉K线评估回调质量
        scored_stocks = []
        for bc in broken_candidates:
            result = self._evaluate_pullback(bc)
            if result and result["divergence_score"] >= 60:
                scored_stocks.append(result)

        if not scored_stocks:
            return None

        # 按评分排序
        scored_stocks.sort(key=lambda x: -x["divergence_score"])

        summary = (
            f"发现 {len(scored_stocks)} 只断板回调股，"
            f"曾 {CONSECUTIVE_ZT_MIN_COUNT}+ 连板现已断板并缩量企稳。"
            f"回调质量最高的: {scored_stocks[0]['name']}({scored_stocks[0]['divergence_score']}分)"
        )

        return {
            "triggered": True,
            "stocks": scored_stocks,
            "summary": summary,
        }

    def _evaluate_pullback(self, candidate: dict) -> dict | None:
        """评估单只断板股的回调质量。返回评分 dict 或 None（数据不足/不符合条件）"""
        code = candidate["code"]
        name = candidate["name"]

        df = fetch_stock_kline(code, days=20)
        if df is None or df.empty or len(df) < 10:
            return None

        df = df.sort_values("date", ascending=True).reset_index(drop=True)
        open_ = df["open"].astype(float).values
        high = df["high"].astype(float).values
        low = df["low"].astype(float).values
        close = df["close"].astype(float).values
        volume = df["volume"].astype(float).values

        # 如果K线最新日期不是今天，用实时行情补充今天的OHLC
        from datetime import datetime as dt
        today_str = dt.now().strftime("%Y-%m-%d")
        last_kline_date = str(df["date"].iloc[-1])[:10]

        if last_kline_date != today_str:
            # 尝试获取实时价格
            try:
                from src.data.fetcher import fetch_realtime_price
                rt = fetch_realtime_price(code)
                if rt and rt.get("current") and rt["current"] > 0:
                    # 将实时数据追加到数组尾部
                    open_ = np.append(open_, rt.get("open", rt["current"]))
                    high = np.append(high, rt.get("high", rt["current"]))
                    low = np.append(low, rt.get("low", rt["current"]))
                    close = np.append(close, rt["current"])
                    # 实时成交量不可靠，用最近5日均量估算
                    avg_vol = np.mean(volume[-5:]) if len(volume) >= 5 else volume[-1]
                    volume = np.append(volume, avg_vol)
            except Exception:
                pass  # 如果实时数据不可用，用现有K线

        # 找断板后的数据：先找最近的涨停日作为峰值
        peak_idx = -1
        for i in range(len(close) - 1, -1, -1):
            prev_c = close[i - 1] if i > 0 else close[0]
            limit_up = self._calc_limit_up_price(prev_c, code, "")
            if self._near(close[i], limit_up, CONSECUTIVE_ZT_LIMIT_UP_TOLERANCE):
                peak_idx = i
                break

        if peak_idx < 3:  # 数据不足
            return None

        peak_price = float(close[peak_idx])
        current_price = float(close[-1])
        pullback_pct = (peak_price - current_price) / peak_price if peak_price > 0 else 0

        # 当前价未跌破峰值=无回调，不纳入分歧候选
        if pullback_pct <= 0:
            return None

        # 断板天数：峰值到末尾
        days_since_break = len(close) - 1 - peak_idx
        if days_since_break < 1 or days_since_break > 10:
            return None

        # 确认峰值前有 ≥3 连板
        peak_zt_days = 1  # 峰值日本身
        for i in range(peak_idx - 1, 0, -1):
            prev_c = close[i - 1]
            limit_up = self._calc_limit_up_price(prev_c, code, "")
            if self._near(close[i], limit_up, CONSECUTIVE_ZT_LIMIT_UP_TOLERANCE):
                peak_zt_days += 1
            else:
                break

        if peak_zt_days < CONSECUTIVE_ZT_MIN_COUNT:
            return None

        # ---- 评分 ----
        score = 0

        # 缩量 (30分): 断板后均量 vs 连板期均量
        zt_vol = volume[peak_idx - peak_zt_days + 1 : peak_idx + 1]
        post_vol = volume[peak_idx + 1 :]
        if len(zt_vol) > 0 and len(post_vol) > 0:
            avg_zt_vol = np.mean(zt_vol)
            avg_post_vol = np.mean(post_vol)
            vol_contract = avg_post_vol / avg_zt_vol if avg_zt_vol > 0 else 999
            if vol_contract < 0.7:
                score += 30
            elif vol_contract < 1.0:
                score += 20
            elif vol_contract < 1.3:
                score += 10
        else:
            vol_contract = 0

        # 支撑有效 (25分): 当前价 vs MA5/MA10
        ma5 = float(np.mean(close[-6:])) if len(close) >= 6 else float(np.mean(close[-5:]))
        ma10 = float(np.mean(close[-11:])) if len(close) >= 11 else float(np.mean(close[-10:])) if len(close) >= 10 else current_price
        if current_price >= ma5:
            score += 25
            held_support = "MA5"
        elif current_price >= ma10:
            score += 15
            held_support = "MA10"
        else:
            held_support = "破位"

        # 回调幅度 (20分): 3%-8% 最佳，8%-15% 可接受
        if 0.03 <= pullback_pct <= 0.08:
            score += 20
        elif 0.08 < pullback_pct <= 0.15:
            score += 10
        elif pullback_pct < 0.03:
            score += 5

        # 企稳信号 (25分)
        body = abs(close[-1] - open_[-1])
        spread = high[-1] - low[-1]
        is_doji = spread > 0 and body / spread < 0.001
        lower_shadow = min(open_[-1], close[-1]) - low[-1]
        is_hammer = (lower_shadow >= 2 * body) and body > 0

        today_pct = (close[-1] - close[-2]) / close[-2] if len(close) >= 2 else 0

        if is_hammer or is_doji:
            score += 25
            stabilization = "锤子线" if is_hammer else "十字星"
        elif -1 <= today_pct * 100 <= 3:
            score += 15
            stabilization = f"缩量小阳({today_pct:+.1%})" if today_pct >= 0 else f"窄幅整理({today_pct:+.1%})"
        else:
            stabilization = "未企稳"

        return {
            "code": code,
            "name": name,
            "peak_zt_days": peak_zt_days,
            "days_since_break": days_since_break,
            "peak_price": round(peak_price, 2),
            "current_price": round(current_price, 2),
            "pullback_pct": round(pullback_pct * 100, 1),
            "volume_contract": round(vol_contract, 2) if isinstance(vol_contract, float) else vol_contract,
            "held_support": held_support,
            "stabilization": stabilization,
            "divergence_score": score,
            "industry": candidate.get("industry", ""),
        }

    def _calc_limit_up_price(self, prev_close: float, code: str, name: str = "") -> float:
        """计算涨停价。与 ConsecutiveZtScanner 中的实现一致"""
        if "ST" in name.upper() or "*ST" in name.upper():
            return round(prev_close * 1.05, 2)
        if code.startswith("688") or code.startswith("300"):
            return round(prev_close * 1.20, 2)
        if code.startswith("8"):
            return round(prev_close * 1.30, 2)
        return round(prev_close * 1.10, 2)

    @staticmethod
    def _near(a: float, b: float, tolerance: float) -> bool:
        if b == 0:
            return abs(a - b) < 0.01
        return abs(a - b) / abs(b) <= tolerance
