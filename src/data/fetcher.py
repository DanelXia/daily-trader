"""
统一数据获取层。基于 baostock 为主力数据源，akshare 补充申万行业指数数据。
"""

import random
import time
import functools
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import numpy as np
import diskcache

import baostock as bs

from config import (
    CACHE_DIR, CACHE_TTL_SNAPSHOT, CACHE_TTL_FINANCIAL,
    CACHE_TTL_KLINE, CACHE_TTL_FUND_FLOW,
)

# ---------------------------------------------------------------------------
# 缓存初始化
# ---------------------------------------------------------------------------
CACHE_DIR.mkdir(parents=True, exist_ok=True)
_cache = diskcache.Cache(str(CACHE_DIR))

# baostock 登录状态
_bs_logged_in = False
_bs_error_count = 0
_BS_MAX_ERRORS = 3


def _ensure_login():
    """确保 baostock 已登录，连接断开时自动重连"""
    global _bs_logged_in, _bs_error_count
    if not _bs_logged_in:
        try:
            bs.login()
            _bs_logged_in = True
            _bs_error_count = 0
        except Exception:
            _bs_logged_in = False
            time.sleep(3)
            bs.login()
            _bs_logged_in = True

    # 如果错误次数过多，强制重连
    if _bs_error_count >= _BS_MAX_ERRORS:
        try:
            bs.logout()
        except Exception:
            pass
        time.sleep(2)
        bs.login()
        _bs_logged_in = True
        _bs_error_count = 0


def _safe_bs_query(query_callable):
    """安全执行 baostock 查询，自动处理连接断开。
    参数: query_callable 是已绑定参数的 callable，如 lambda: bs.query_stock_basic()"""
    global _bs_error_count, _bs_logged_in
    for attempt in range(3):
        try:
            _ensure_login()
            result = query_callable()
            _bs_error_count = 0
            return result
        except (OSError, ConnectionError) as e:
            _bs_error_count += 1
            _bs_logged_in = False
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
        except Exception as e:
            _bs_error_count += 1
            if attempt == 2:
                raise
            time.sleep(2)
    return None


def cached(ttl: int):
    """装饰器：带 TTL 的 diskcache 缓存"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = f"{func.__name__}:{args}:{kwargs}"
            result = _cache.get(key)
            if result is not None:
                return result
            result = func(*args, **kwargs)
            if result is not None and not (isinstance(result, pd.DataFrame) and result.empty):
                _cache.set(key, result, expire=ttl)
            return result
        return wrapper
    return decorator


# ===================================================================
# 股票基本信息
# ===================================================================

@cached(ttl=CACHE_TTL_FINANCIAL)
def fetch_all_stocks_basic() -> pd.DataFrame:
    """获取全A股基本信息（代码、名称、上市日期、状态）"""
    _ensure_login()
    rs = _safe_bs_query(bs.query_stock_basic)
    if rs is None:
        return pd.DataFrame()
    df = rs.get_data()
    if df is None or df.empty:
        return pd.DataFrame()
    # 过滤: 只保留A股，排除退市
    if "type" in df.columns:
        df = df[df["type"] == "1"].copy()
    if "status" in df.columns:
        df = df[~df["status"].isin(["0"])].copy()
    # 标准化代码
    df["code"] = df["code"].str.replace("sh.", "").str.replace("sz.", "")
    if "code_name" in df.columns:
        df = df.rename(columns={"code_name": "name", "ipoDate": "ipo_date"})
    return df


@cached(ttl=CACHE_TTL_SNAPSHOT)
def fetch_stock_basic_info(code: str) -> dict:
    """获取单只股票的基本信息（PE/PB/市值等，基于最新财报）"""
    _ensure_login()
    # 尝试获取估值数据
    try:
        import akshare as ak
        time.sleep(random.uniform(2, 5))
        df = ak.stock_zh_a_spot_em()
        row = df[df["代码"].str.contains(code)]
        if not row.empty:
            r = row.iloc[0]
            return {
                "code": code,
                "price": float(r.get("最新价", 0)),
                "pe_dynamic": float(r.get("市盈率-动态", 0)),
                "pb": float(r.get("市净率", 0)),
                "total_market_cap": float(r.get("总市值", 0)),
                "float_market_cap": float(r.get("流通市值", 0)),
                "pct_change": float(r.get("涨跌幅", 0)),
                "pct_change_60d": float(r.get("60日涨跌幅", 0)),
            }
    except Exception:
        pass
    return None


# ===================================================================
# 行业分类（证监会行业）
# ===================================================================

@cached(ttl=CACHE_TTL_FINANCIAL)
def fetch_stock_industry_map() -> pd.DataFrame:
    """获取全A股行业分类（证监会分类）"""
    rs = _safe_bs_query(lambda: bs.query_stock_industry())
    if rs is None:
        return pd.DataFrame()
    df = rs.get_data()
    df["code"] = df["code"].str.replace("sh.", "").str.replace("sz.", "")
    return df[["code", "code_name", "industry", "industryClassification"]]


@cached(ttl=CACHE_TTL_FINANCIAL)
def fetch_industry_list() -> pd.DataFrame:
    """获取行业列表及其成分股数量统计"""
    df = fetch_stock_industry_map()
    if df is None or df.empty:
        return pd.DataFrame()
    # 按 industry 分组统计
    industry_stats = df.groupby("industry").agg(
        stock_count=("code", "count"),
        classification=("industryClassification", "first"),
    ).reset_index()
    industry_stats = industry_stats.sort_values("stock_count", ascending=False)
    return industry_stats


# ===================================================================
# K线数据
# ===================================================================

@cached(ttl=CACHE_TTL_KLINE)
def fetch_stock_kline(code: str, days: int = 120, adjust: str = "2") -> Optional[pd.DataFrame]:
    """
    获取个股日K线（后复权）。
    adjust: "1"=后复权 "2"=前复权 "3"=不复权
    """
    _ensure_login()
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days + 60)).strftime("%Y-%m-%d")

    # 确定前缀
    prefix = "sh." if code.startswith("6") else "sz."
    full_code = f"{prefix}{code}"

    rs = _safe_bs_query(lambda: bs.query_history_k_data_plus(
        full_code,
        "date,open,high,low,close,volume,amount,turn,pctChg",
        start_date=start_date, end_date=end_date,
        frequency="d", adjustflag=adjust,
    ))
    df = rs.get_data()
    if df is None or df.empty:
        return None

    df = df.rename(columns={
        "date": "date", "open": "open", "high": "high",
        "low": "low", "close": "close", "volume": "volume",
        "amount": "amount", "turn": "turnover", "pctChg": "pct_change",
    })
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_change"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    # 过滤掉空数据
    df = df[df["volume"] > 0].copy()
    return df.tail(days)


# ===================================================================
# 行业指数数据（通过 akshare 申万指数）
# ===================================================================

def fetch_sw_industry_list() -> pd.DataFrame:
    """获取申万一级行业指数列表（通过 akshare）"""
    try:
        import akshare as ak
        time.sleep(random.uniform(2, 3))
        df = ak.index_realtime_sw(symbol="一级行业")
        if df is not None and not df.empty:
            df = df.rename(columns={
                "指数代码": "code", "指数名称": "name",
                "最新价": "price", "涨跌幅": "pct_change",
                "成交量": "volume", "成交额": "amount",
            })
        return df
    except Exception:
        return None


@cached(ttl=CACHE_TTL_KLINE)
def fetch_sw_industry_kline(code: str, days: int = 100) -> Optional[pd.DataFrame]:
    """获取申万行业指数K线"""
    try:
        import akshare as ak
        time.sleep(random.uniform(3, 5))
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")
        df = ak.index_hist_sw(symbol=code, period="day", start_date=start, end_date=end)
        if df is not None and not df.empty:
            df.columns = [c.lower() for c in df.columns]
            rename_map = {}
            for cn, en in [("日期", "date"), ("开盘", "open"), ("最高", "high"),
                           ("最低", "low"), ("收盘", "close"), ("成交量", "volume"),
                           ("成交额", "amount"), ("涨跌幅", "pct_change")]:
                for c in df.columns:
                    if cn in c:
                        rename_map[c] = en
            df = df.rename(columns=rename_map)
            for col in ["close", "volume", "pct_change"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return None


# ===================================================================
# 大盘指数
# ===================================================================

def fetch_index_snapshot() -> dict:
    """获取上证指数和深证成指快照"""
    _ensure_login()
    result = {}
    try:
        # 上证
        rs = _safe_bs_query(lambda: bs.query_history_k_data_plus(
            "sh.000001", "date,close,pctChg",
            start_date=(datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d"),
            frequency="d", adjustflag="3",
        ))
        df = rs.get_data() if rs else pd.DataFrame()
        if not df.empty:
            result["sh_index"] = float(df["close"].iloc[-1])
            result["sh_change_pct"] = float(df["pctChg"].iloc[-1])

        # 深证
        rs = _safe_bs_query(lambda: bs.query_history_k_data_plus(
            "sz.399001", "date,close,pctChg",
            start_date=(datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d"),
            frequency="d", adjustflag="3",
        ))
        df = rs.get_data() if rs else pd.DataFrame()
        if not df.empty:
            result["sz_index"] = float(df["close"].iloc[-1])
            result["sz_change_pct"] = float(df["pctChg"].iloc[-1])
    except Exception:
        pass
    return result


# ===================================================================
# 交易日历
# ===================================================================

def is_trading_day(date: Optional[datetime] = None) -> bool:
    """检查是否为A股交易日"""
    if date is None:
        date = datetime.now()
    if date.weekday() >= 5:
        return False
    return True


# ===================================================================
# 财务数据
# ===================================================================

@cached(ttl=CACHE_TTL_FINANCIAL)
def fetch_stock_profit_data(code: str, year: int = None, quarter: int = None) -> Optional[pd.DataFrame]:
    """获取个股利润表数据"""
    _ensure_login()
    prefix = "sh." if code.startswith("6") else "sz."
    full_code = f"{prefix}{code}"
    try:
        rs = bs.query_profit_data(code=full_code, year=year, quarter=quarter)
        df = rs.get_data()
        return df
    except Exception:
        return None


@cached(ttl=CACHE_TTL_FINANCIAL)
def fetch_stock_balance_data(code: str, year: int = None, quarter: int = None) -> Optional[pd.DataFrame]:
    """获取个股资产负债表数据"""
    _ensure_login()
    prefix = "sh." if code.startswith("6") else "sz."
    full_code = f"{prefix}{code}"
    try:
        rs = bs.query_balance_data(code=full_code, year=year, quarter=quarter)
        df = rs.get_data()
        return df
    except Exception:
        return None


@cached(ttl=CACHE_TTL_FINANCIAL)
def fetch_growth_data(code: str, year: int = None, quarter: int = None) -> Optional[pd.DataFrame]:
    """获取个股成长性指标"""
    _ensure_login()
    prefix = "sh." if code.startswith("6") else "sz."
    full_code = f"{prefix}{code}"
    try:
        rs = bs.query_growth_data(code=full_code, year=year, quarter=quarter)
        df = rs.get_data()
        return df
    except Exception:
        return None


@cached(ttl=CACHE_TTL_FINANCIAL)
def fetch_operation_data(code: str, year: int = None, quarter: int = None) -> Optional[pd.DataFrame]:
    """获取个股营运能力指标"""
    _ensure_login()
    prefix = "sh." if code.startswith("6") else "sz."
    full_code = f"{prefix}{code}"
    try:
        rs = bs.query_operation_data(code=full_code, year=year, quarter=quarter)
        df = rs.get_data()
        return df
    except Exception:
        return None


@cached(ttl=CACHE_TTL_FINANCIAL)
def fetch_dupont_data(code: str, year: int = None, quarter: int = None) -> Optional[pd.DataFrame]:
    """获取个股杜邦指标（含ROE）"""
    _ensure_login()
    prefix = "sh." if code.startswith("6") else "sz."
    full_code = f"{prefix}{code}"
    try:
        rs = _safe_bs_query(lambda: bs.query_dupont_data(code=full_code, year=year, quarter=quarter))
        df = rs.get_data() if rs else pd.DataFrame()
        return df
    except Exception:
        return None


@cached(ttl=CACHE_TTL_FINANCIAL)
def fetch_stock_financials(code: str) -> Optional[dict]:
    """获取个股关键财务指标，返回字典格式（轻量版，仅杜邦分析）"""
    dupont = fetch_dupont_data(code)

    if dupont is None or dupont.empty:
        return None

    result = {}
    latest = dupont.iloc[0]
    for col in dupont.columns:
        try:
            if col != "code" and col != "code_name":
                result[col] = float(latest[col]) if latest[col] else None
        except Exception:
            pass

    return result if result else None


def invalidate_cache(pattern: Optional[str] = None):
    """清除匹配模式的缓存条目"""
    if pattern is None:
        _cache.clear()
    else:
        for key in list(_cache.iterkeys()):
            if pattern in key:
                del _cache[key]


def close():
    """登出 baostock"""
    global _bs_logged_in
    if _bs_logged_in:
        try:
            bs.logout()
        except Exception:
            pass
        _bs_logged_in = False
