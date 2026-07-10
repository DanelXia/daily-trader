"""
个股筛选打分引擎。
基于 baostock 财务数据，对证监会行业内成分股进行多维评分。
评分体系：价值 50 + 成长 20 + 技术 30 = 100。
"""

import numpy as np
import pandas as pd

from src.data.fetcher import (
    fetch_stock_financials,
    fetch_stock_kline,
    fetch_all_stocks_basic,
)
from src.data.industry import IndustryMapper
from config import STOCK_SCREENING, TOP_STOCKS_PER_INDUSTRY


class StockSelector:
    """个股筛选引擎"""

    def __init__(self, mapper: IndustryMapper):
        self.mapper = mapper
        self._all_stocks: pd.DataFrame | None = None
        self._stock_cache: dict = {}

    def _get_stock_info(self, code: str) -> dict | None:
        """获取股票基本信息（带缓存）"""
        if code in self._stock_cache:
            return self._stock_cache[code]
        if self._all_stocks is None:
            self._all_stocks = fetch_all_stocks_basic()
        row = self._all_stocks[self._all_stocks["code"] == code]
        if row.empty:
            return None
        info = {
            "code": code,
            "name": row["name"].iloc[0],
            "ipo_date": row["ipo_date"].iloc[0],
        }
        self._stock_cache[code] = info
        return info

    def apply_hard_filters(self, codes: list[str]) -> list[str]:
        """应用硬过滤条件"""
        passed = []
        for code in codes:
            info = self._get_stock_info(code)
            if info is None:
                continue

            name = info["name"]

            # ST 过滤
            if STOCK_SCREENING["exclude_st"] and "ST" in str(name):
                continue

            # 上市天数过滤
            ipo = str(info.get("ipo_date", ""))
            if ipo and ipo != "None" and ipo != "":
                try:
                    ipo_dt = pd.to_datetime(ipo)
                    days_listed = (pd.Timestamp.now() - ipo_dt).days
                    if days_listed < STOCK_SCREENING["min_listing_days"]:
                        continue
                except Exception:
                    pass

            passed.append(code)

        return passed

    def score_stocks(self, industry: str, top_n: int = TOP_STOCKS_PER_INDUSTRY) -> list[dict]:
        """对指定行业成分股打分排序"""
        all_codes = self.mapper.get_stocks_in_industry(industry)
        if not all_codes:
            print(f"  [StockSelector] 行业 {industry} 无成分股数据")
            return []

        codes = self.apply_hard_filters(all_codes)
        if not codes:
            print(f"  [StockSelector] 行业 {industry} 所有成分股均未通过过滤")
            return []

        print(f"  [StockSelector] {industry}: {len(codes)}/{len(all_codes)} 通过过滤 "
              f"(筛选 {min(top_n, len(codes))} 只)")

        # 取通过过滤的股票中采样进行深度分析
        sample_size = min(len(codes), 50)
        sample = codes[:sample_size]

        results = []
        for code in sample:
            try:
                score, details = self._score_stock(code)
                if score is None:
                    continue

                info = self._get_stock_info(code)
                results.append({
                    "code": code,
                    "name": info["name"] if info else code,
                    "composite_score": round(score, 1),
                    "value_score": round(details.get("value", 0), 1),
                    "growth_score": round(details.get("growth", 0), 1),
                    "technical_score": round(details.get("technical", 0), 1),
                    "metrics": {
                        "pe_dynamic": details.get("pe"),
                        "pb": details.get("pb"),
                        "roe": details.get("roe"),
                        "price_latest": details.get("price"),
                        "market_cap_billion": details.get("cap_billion"),
                        "revenue_growth_yoy": details.get("revenue_growth"),
                        "profit_growth_yoy": details.get("profit_growth"),
                        "price_change_20d_pct": details.get("ret_20d"),
                        "price_change_60d_pct": details.get("ret_60d"),
                    },
                })
            except Exception as e:
                continue

        results.sort(key=lambda x: x["composite_score"], reverse=True)
        return results[:top_n]

    # ------------------------------------------------------------------
    # 综合评分：价值 50 + 成长 20 + 技术 30
    # ------------------------------------------------------------------
    def _score_stock(self, code: str) -> tuple[float | None, dict]:
        """综合评分，返回 (total_score, details_dict)"""
        details = {}

        # 财务（可能失败，使用默认分）
        fin = fetch_stock_financials(code)

        # 价值 (50)
        details["value"] = self._score_value(fin, details)

        # 成长 (20)
        details["growth"] = self._score_growth(fin, details)

        # 技术 (30) — 从K线计算，这个最关键且通常能成功
        details["technical"] = self._score_technical(code, details)

        total = (details["value"] + details["growth"] + details["technical"])
        return total, details

    # ------------------------------------------------------------------
    # 价值 (50): PE 15 + PB 10 + ROE 15 + 资产规模 10
    # ------------------------------------------------------------------
    def _score_value(self, fin: dict | None, details: dict) -> float:
        score = 0.0
        if fin is None:
            return 15.0  # 无财务数据给基准分

        # PE 估值 (15)
        pe = fin.get("市盈率") or fin.get("PE")
        if pe and isinstance(pe, (int, float)) and 0 < pe < 200:
            details["pe"] = round(float(pe), 1)
            if pe < 15:
                score += 15
            elif pe < 25:
                score += 10
            elif pe < 40:
                score += 5
            elif pe < 60:
                score += 2
        else:
            score += 5

        # PB 估值 (10)
        pb = fin.get("市净率") or fin.get("PB")
        if pb and isinstance(pb, (int, float)) and pb > 0:
            details["pb"] = round(float(pb), 1)
            if pb < 2:
                score += 10
            elif pb < 3:
                score += 7
            elif pb < 4:
                score += 4
            elif pb < 6:
                score += 2

        # ROE (15) — 巴菲特的 ROE>20% 标准
        roe = fin.get("净资产收益率") or fin.get("dupontROE")
        if roe and isinstance(roe, (int, float)):
            details["roe"] = round(float(roe), 1)
            if roe >= 20:
                score += 15
            elif roe >= 15:
                score += 12
            elif roe >= 10:
                score += 9
            elif roe >= 5:
                score += 5
            elif roe > 0:
                score += 2
        else:
            score += 5

        # 总资产规模 (10) — 规模越大抗风险能力越强
        total_assets = fin.get("总资产")
        if total_assets and isinstance(total_assets, (int, float)):
            if total_assets > 1e11:
                score += 10
            elif total_assets > 5e10:
                score += 8
            elif total_assets > 1e10:
                score += 6
            elif total_assets > 1e9:
                score += 3

        return min(score, 50)

    # ------------------------------------------------------------------
    # 成长 (20): 营收增速 10 + 利润增速 10
    # ------------------------------------------------------------------
    def _score_growth(self, fin: dict | None, details: dict) -> float:
        score = 0.0
        if fin is None:
            return 5.0

        # 营收增长率 (10)
        rev_g = (fin.get("营业收入增长率") or fin.get("主营收入增长率")
                 or fin.get("NPParentCompanyGrowRate"))
        if rev_g and isinstance(rev_g, (int, float)):
            details["revenue_growth"] = round(float(rev_g), 1)
            if rev_g >= 30:
                score += 10
            elif rev_g >= 20:
                score += 8
            elif rev_g >= 10:
                score += 6
            elif rev_g >= 0:
                score += 3

        # 净利润增长率 (10)
        profit_g = fin.get("净利润增长率") or fin.get("NPParentCompanyGrowRate")
        if profit_g and isinstance(profit_g, (int, float)):
            details["profit_growth"] = round(float(profit_g), 1)
            if profit_g >= 30:
                score += 10
            elif profit_g >= 15:
                score += 7
            elif profit_g >= 0:
                score += 4

        return min(score, 20)

    # ------------------------------------------------------------------
    # 技术 (30): 20日动量 12 + 60日动量 8 + 均线 5 + 流动性 5
    # ------------------------------------------------------------------
    def _score_technical(self, code: str, details: dict) -> float:
        score = 10.0  # 默认
        df = fetch_stock_kline(code, days=120)
        if df is None or df.empty or len(df) < 20:
            return score

        close = df["close"].values
        price = float(close[-1])
        details["price"] = round(price, 2)

        # 20日收益 (12)
        if len(close) >= 20:
            ret_20d = (close[-1] / close[-20] - 1) * 100
            details["ret_20d"] = round(ret_20d, 1)
            score = 12 * min(max(ret_20d / 10 + 0.5, 0), 1.0)

        # 60日收益 (8)
        if len(close) >= 60:
            ret_60d = (close[-1] / close[-60] - 1) * 100
            details["ret_60d"] = round(ret_60d, 1)
            score += 8 * min(max(ret_60d / 20 + 0.5, 0), 1.0)

        # 均线多头 (5)
        if len(close) >= 60:
            ma20 = np.mean(close[-20:])
            ma60 = np.mean(close[-60:])
            if price > ma20 > ma60:
                score += 5
            elif price > ma20:
                score += 2

        # 成交量流动性 (5)
        if "volume" in df.columns:
            avg_vol = df["volume"].tail(20).mean()
            if avg_vol > 1e7:
                score += 5
            elif avg_vol > 5e6:
                score += 3
            elif avg_vol > 1e6:
                score += 1

        return min(score, 30)
