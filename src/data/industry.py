"""
行业分类管理。基于 baostock 的证监会行业分类。
"""

import pandas as pd

from src.data.fetcher import (
    fetch_stock_industry_map,
    fetch_all_stocks_basic,
)


class IndustryMapper:
    """行业分类映射器（证监会分类）"""

    def __init__(self):
        self._stock_map: pd.DataFrame | None = None           # 股票→行业映射
        self._all_stocks: pd.DataFrame | None = None          # 全A股基本信息

        self._industry_stocks: dict[str, list[str]] = {}      # industry → [stock_codes]
        self._stock_industry: dict[str, str] = {}              # stock_code → industry
        self._industry_name_map: dict[str, str] = {}           # industry → classification

    def load(self):
        """加载行业分类数据"""
        print("[IndustryMapper] 加载全A股基本信息...")
        self._all_stocks = fetch_all_stocks_basic()

        print(f"[IndustryMapper] 加载证监会行业分类...")
        self._stock_map = fetch_stock_industry_map()
        if self._stock_map is not None and not self._stock_map.empty:
            for _, row in self._stock_map.iterrows():
                stock_code = row["code"]
                industry = row["industry"]
                if stock_code and industry:
                    self._stock_industry[stock_code] = industry
                    self._industry_stocks.setdefault(industry, []).append(stock_code)
                    if industry not in self._industry_name_map:
                        self._industry_name_map[industry] = row.get("industryClassification", "")

        print(f"[IndustryMapper] 已加载 {len(self._industry_stocks)} 个证监会行业, "
              f"{len(self._stock_industry)} 只个股")

    def get_all_industries(self) -> list[str]:
        """获取所有证监会行业名称（过滤掉成分股过少的）"""
        min_stocks = 10
        return [ind for ind, stocks in self._industry_stocks.items()
                if len(stocks) >= min_stocks]

    def get_stocks_in_industry(self, industry: str) -> list[str]:
        """获取某个行业的所有成分股"""
        return self._industry_stocks.get(industry, [])

    def get_industry_for_stock(self, stock_code: str) -> str | None:
        """获取某只股票的证监会行业"""
        return self._stock_industry.get(stock_code)

    def get_industry_name(self, industry: str) -> str:
        """获取行业的完整显示名称"""
        cls = self._industry_name_map.get(industry, "")
        return f"{industry}({cls})" if cls else industry
