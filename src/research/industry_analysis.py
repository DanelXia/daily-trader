"""
行业评分排名引擎（轻量版）。
优先使用K线动量数据（更可靠），减少对baostock财务API的依赖。
"""

import numpy as np
import pandas as pd

from src.data.fetcher import (
    fetch_sw_industry_kline,
    fetch_stock_financials,
    fetch_stock_kline,
)
from src.data.industry import IndustryMapper


class IndustryAnalyzer:
    """行业分析引擎"""

    def __init__(self, mapper: IndustryMapper):
        self.mapper = mapper

    def analyze(self, top_n: int = 5) -> list[dict]:
        """执行全行业分析，返回 Top-N 行业评分"""
        industries = self.mapper.get_all_industries()
        if not industries:
            print("[IndustryAnalyzer] 错误: 无行业数据")
            return []

        print(f"[IndustryAnalyzer] 分析 {len(industries)} 个行业...")

        # 预加载申万指数K线
        sw_data = self._load_sw_index_data()

        results = []
        for i, industry in enumerate(industries):
            if (i + 1) % 10 == 0:
                print(f"  进度: {i+1}/{len(industries)}")
            try:
                momentum = self._score_momentum(industry, sw_data)
                fundamental = self._score_fundamental(industry)
                composite = momentum + fundamental
                results.append({
                    "industry_name": industry,
                    "industry_display": self.mapper.get_industry_name(industry),
                    "momentum_score": round(momentum, 1),
                    "fundamental_score": round(fundamental, 1),
                    "capital_flow_score": 0,
                    "policy_catalyst_score": 0,
                    "composite_score": round(composite, 1),
                })
            except Exception as e:
                continue

        results.sort(key=lambda x: x["composite_score"], reverse=True)

        # 归一化
        if results:
            scores = [r["composite_score"] for r in results]
            min_s, max_s = min(scores), max(scores)
            if max_s > min_s:
                for r in results:
                    r["composite_score"] = round(
                        50 + 50 * (r["composite_score"] - min_s) / (max_s - min_s), 1
                    )

        print(f"[IndustryAnalyzer] 完成，共评估 {len(results)} 个行业")
        return results[:top_n]

    def _load_sw_index_data(self) -> dict:
        """加载申万行业指数K线数据"""
        sw_data = {}
        sw_codes = self.mapper.get_sw_industry_codes()
        if not sw_codes:
            return sw_data

        for code in sw_codes:
            df = fetch_sw_industry_kline(code, days=100)
            if df is not None and not df.empty:
                name = self.mapper.get_sw_industry_name(code)
                sw_data[name] = df
        return sw_data

    # ------------------------------------------------------------------
    # 动量 (60 分) — 申万指数 + 成分股K线
    # ------------------------------------------------------------------
    def _score_momentum(self, industry: str, sw_data: dict) -> float:
        sw_name = self.mapper.get_sw_index_for_csrc(industry)

        # 优先用申万指数
        if sw_name and sw_name in sw_data:
            df = sw_data[sw_name]
            if "close" in df.columns and len(df) >= 5:
                close = df["close"].values
                idx_20 = max(0, len(close) - min(20, len(close)))
                ret_20d = (close[-1] / close[idx_20] - 1) * 100

                idx_60 = max(0, len(close) - min(60, len(close)))
                ret_60d = (close[-1] / close[idx_60] - 1) * 100

                score_20d = 35 * min(max(ret_20d / 8 + 0.5, 0), 1.0)
                score_60d = 25 * min(max(ret_60d / 15 + 0.5, 0), 1.0)
                return score_20d + score_60d

        # 回退：用成分股K线平均
        return self._score_momentum_from_stocks(industry)

    def _score_momentum_from_stocks(self, industry: str) -> float:
        """用成分股K线近似行业动量"""
        stocks = self.mapper.get_stocks_in_industry(industry)
        if not stocks:
            return 30.0

        # 只用缓存数据（不发起新请求），取前3只
        sample = stocks[:3]
        returns = []
        for code in sample:
            df = fetch_stock_kline(code, days=30)
            if df is not None and not df.empty and len(df) >= 20:
                try:
                    close = df["close"].values
                    ret = (close[-1] / close[-20] - 1) * 100
                    returns.append(ret)
                except Exception:
                    pass

        if returns:
            avg_ret = np.mean(returns)
            return 60 * min(max(avg_ret / 10 + 0.5, 0), 1.0)
        return 30.0

    # ------------------------------------------------------------------
    # 基本面 (40 分) — 轻量采样
    # ------------------------------------------------------------------
    def _score_fundamental(self, industry: str) -> float:
        stocks = self.mapper.get_stocks_in_industry(industry)
        if not stocks:
            return 20.0

        # 行业规模分 (20)
        score_scale = 20 * min(len(stocks) / 200, 1.0)

        # 只取前2只做财务分析
        sample = stocks[:2]
        roe_values = []

        for code in sample:
            fin = fetch_stock_financials(code)
            if fin:
                roe = fin.get("净资产收益率") or fin.get("dupontROE")
                if roe and isinstance(roe, (int, float)) and -100 < roe < 100:
                    roe_values.append(roe)

        # ROE分 (20)
        if roe_values:
            median_roe = np.median(roe_values)
            score_roe = 20 * min(max(median_roe / 15, 0), 1.0)
        else:
            score_roe = 10.0

        return score_scale + score_roe
