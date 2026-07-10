"""
行业分类管理。基于 baostock 的证监会行业分类，以及 akshare 申万行业指数。
"""

import pandas as pd

from src.data.fetcher import (
    fetch_industry_list,
    fetch_stock_industry_map,
    fetch_sw_industry_list,
    fetch_all_stocks_basic,
)


class IndustryMapper:
    """行业分类映射器（双分类体系：申万 + 证监会）"""

    def __init__(self):
        self._industry_df: pd.DataFrame | None = None        # 证监会行业统计
        self._stock_map: pd.DataFrame | None = None           # 股票→行业映射
        self._all_stocks: pd.DataFrame | None = None          # 全A股基本信息

        # 证监会分类
        self._industry_stocks: dict[str, list[str]] = {}      # industry → [stock_codes]
        self._stock_industry: dict[str, str] = {}              # stock_code → industry
        self._industry_name_map: dict[str, str] = {}           # industry → classification

        # 申万分类（从 akshare 获取）
        self._sw_codes: dict[str, str] = {}                   # name → code
        self._sw_names: dict[str, str] = {}                   # code → name

        # 行业→申万指数映射（用于近似匹配）
        self._csrc_to_sw: dict[str, str] = {}

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

        print(f"[IndustryMapper] 加载申万一级行业指数...")
        self._load_sw_industries()

        print(f"[IndustryMapper] 已加载 {len(self._industry_stocks)} 个证监会行业, "
              f"{len(self._sw_codes)} 个申万行业指数, "
              f"{len(self._stock_industry)} 只个股")

    def _load_sw_industries(self):
        """加载申万行业指数列表，并建立与证监会行业的映射"""
        df = fetch_sw_industry_list()
        if df is None or df.empty:
            print("  [警告] 无法加载申万行业指数，将使用证监会分类")
            return

        for _, row in df.iterrows():
            code = str(row.get("code", ""))
            name = str(row.get("name", ""))
            if code and name:
                self._sw_codes[name] = code
                self._sw_names[code] = name

        # 建立证监会→申万映射（按名称模糊匹配）
        self._build_csrc_to_sw_mapping()

    def _build_csrc_to_sw_mapping(self):
        """建立证监会行业到申万行业的近似映射"""
        # 关键词映射
        keyword_map = {
            "银行": "银行", "保险": "非银金融", "证券": "非银金融",
            "房地产": "房地产", "建筑": "建筑装饰", "建材": "建筑材料",
            "汽车": "汽车", "医药": "医药生物", "医疗": "医药生物",
            "电子": "电子", "半导体": "电子", "计算机": "计算机",
            "通信": "通信", "传媒": "传媒", "食品": "食品饮料",
            "饮料": "食品饮料", "白酒": "食品饮料", "家电": "家用电器",
            "电力": "公用事业", "煤炭": "煤炭", "石油": "石油石化",
            "石化": "石油石化", "钢铁": "钢铁", "有色": "有色金属",
            "化工": "基础化工", "农业": "农林牧渔", "军工": "国防军工",
            "纺织": "纺织服饰", "服装": "纺织服饰", "轻工": "轻工制造",
            "交通": "交通运输", "运输": "交通运输", "休闲": "社会服务",
            "旅游": "社会服务", "环保": "环保", "商贸": "商贸零售",
            "零售": "商贸零售", "机械": "机械设备", "电力设备": "电力设备",
            "新能源": "电力设备",
        }

        for csrc_ind in self._industry_stocks:
            for keyword, sw_name in keyword_map.items():
                if keyword in csrc_ind:
                    if sw_name in self._sw_codes:
                        self._csrc_to_sw[csrc_ind] = sw_name
                    break

    # ---- 证监会行业 ----

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

    # ---- 申万指数 ----

    def get_sw_industry_codes(self) -> list[str]:
        """获取所有申万一级行业指数代码"""
        return list(self._sw_names.keys())

    def get_sw_industry_name(self, code: str) -> str:
        """申万指数代码→名称"""
        return self._sw_names.get(code, code)

    def get_sw_industry_code(self, name: str) -> str | None:
        """申万行业名称→指数代码"""
        return self._sw_codes.get(name)

    def get_sw_index_for_csrc(self, csrc_industry: str) -> str | None:
        """证监会行业→对应的申万行业名称"""
        return self._csrc_to_sw.get(csrc_industry)
