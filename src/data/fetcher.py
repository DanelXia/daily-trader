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
    SPLIT_DETECTION_THRESHOLD,
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

def _detect_and_adjust_splits(df: pd.DataFrame) -> pd.DataFrame:
    """
    检测并修正 baostock 无法自动处理的 ETF/股票拆分。

    baostock 的 adjustflag 对 ETF 份额拆分无效，导致拆分前后价格出现
    断崖式跳变。本函数通过 pct_change（不受拆分影响）与实际收盘价
    对比来自动检测拆分，并向后修正历史 OHLC 数据以保持价格连续性。

    算法：
      expected_close = prev_close * (1 + pct_change[i] / 100)
      若 |close[i] - expected_close| / expected_close > 阈值 → 拆分
      拆分因子 = close[i] / expected_close
      将所有拆分日前的 OHLC 乘以该因子
    """
    if df is None or df.empty or len(df) < 2:
        return df

    df = df.copy()
    df = df.sort_values("date").reset_index(drop=True)

    closes = df["close"].values
    pct_changes = df["pct_change"].values
    n = len(df)
    splits_found = 0

    for i in range(1, n):
        if pd.isna(pct_changes[i]) or pd.isna(closes[i]) or pd.isna(closes[i - 1]):
            continue

        expected = closes[i - 1] * (1.0 + pct_changes[i] / 100.0)
        if expected <= 0:
            continue

        deviation = abs(closes[i] - expected) / expected

        if deviation > SPLIT_DETECTION_THRESHOLD:
            factor = float(closes[i]) / float(expected)
            splits_found += 1

            for col in ["open", "high", "low", "close"]:
                df.loc[: i - 1, col] = (
                    df.loc[: i - 1, col].astype(float) * factor
                )

            date_str = str(df.loc[i, "date"])[:10]
            print(
                f"  [SplitDetected] {date_str}  "
                f"deviation={deviation:.1%}  factor={factor:.4f}"
            )

    if splits_found > 0:
        print(f"  [SplitAdjust] {splits_found} split(s) corrected")

    return df


@cached(ttl=CACHE_TTL_KLINE)
def fetch_stock_kline(code: str, days: int = 120, adjust: str = "2") -> Optional[pd.DataFrame]:
    """
    获取个股日K线（前复权，自动检测并修正 ETF/股票拆分）。
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
    # 自动检测并修正 ETF/股票拆分（baostock 的 adjustflag 对 ETF 无效）
    df = _detect_and_adjust_splits(df)
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
    """获取上证指数和深证成指快照（含成交额 + 大盘量比）"""
    _ensure_login()
    result = {}
    try:
        lookback = 25  # 取25天，用于计算20日均量
        start_date = (datetime.now() - timedelta(days=lookback + 10)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        # 上证
        rs = _safe_bs_query(lambda: bs.query_history_k_data_plus(
            "sh.000001", "date,close,pctChg,volume,amount",
            start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="3",
        ))
        df_sh = rs.get_data() if rs else pd.DataFrame()
        if not df_sh.empty:
            df_sh["close"] = pd.to_numeric(df_sh["close"], errors="coerce")
            df_sh["pctChg"] = pd.to_numeric(df_sh["pctChg"], errors="coerce")
            df_sh["amount"] = pd.to_numeric(df_sh["amount"], errors="coerce")
            df_sh = df_sh[df_sh["amount"] > 0].tail(lookback)
            result["sh_index"] = float(df_sh["close"].iloc[-1])
            result["sh_change_pct"] = float(df_sh["pctChg"].iloc[-1])
            result["sh_amount"] = float(df_sh["amount"].iloc[-1]) / 1e8  # 元→亿
            sh_amounts = df_sh["amount"].values

        # 深证
        rs = _safe_bs_query(lambda: bs.query_history_k_data_plus(
            "sz.399001", "date,close,pctChg,volume,amount",
            start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="3",
        ))
        df_sz = rs.get_data() if rs else pd.DataFrame()
        if not df_sz.empty:
            df_sz["close"] = pd.to_numeric(df_sz["close"], errors="coerce")
            df_sz["pctChg"] = pd.to_numeric(df_sz["pctChg"], errors="coerce")
            df_sz["amount"] = pd.to_numeric(df_sz["amount"], errors="coerce")
            df_sz = df_sz[df_sz["amount"] > 0].tail(lookback)
            result["sz_index"] = float(df_sz["close"].iloc[-1])
            result["sz_change_pct"] = float(df_sz["pctChg"].iloc[-1])
            result["sz_amount"] = float(df_sz["amount"].iloc[-1]) / 1e8  # 元→亿
            sz_amounts = df_sz["amount"].values

        # 两市合计 + 大盘量比 (vs 20日均值)
        if "sh_amount" in result and "sz_amount" in result:
            result["total_amount"] = round(result["sh_amount"] + result["sz_amount"], 1)
            # 对齐两指数数据长度，取交集
            min_len = min(len(sh_amounts), len(sz_amounts))
            if min_len > 1:
                sh_recent = sh_amounts[-min_len:]
                sz_recent = sz_amounts[-min_len:]
                total_daily = (sh_recent + sz_recent) / 1e8
                today_total = total_daily[-1]
                # 20日均量（不含今天）
                avg_n = min(20, len(total_daily) - 1)
                avg_total = total_daily[-1 - avg_n:-1].mean() if avg_n > 0 else today_total
                result["market_volume_ratio"] = round(today_total / avg_total, 2) if avg_total > 0 else 1.0
            else:
                result["market_volume_ratio"] = 1.0
    except Exception:
        pass
    return result


def fetch_intraday_analysis() -> dict | None:
    """获取今日30分钟分时数据，用于大盘日内分析（盘后调用）"""
    try:
        import json
        import urllib.request

        def _fetch(symbol):
            url = (
                f"https://quotes.sina.cn/cn/api/jsonp_v2.php/data/"
                f"CN_MarketDataService.getKLineData"
                f"?symbol={symbol}&scale=30&ma=no&datalen=16"
            )
            req = urllib.request.Request(url, headers={
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0",
            })
            resp = urllib.request.urlopen(req, timeout=15)
            text = resp.read().decode("utf-8")
            if "(" in text:
                text = text[text.index("(") + 1:text.rindex(")")]
            return json.loads(text)

        today_str = datetime.now().strftime("%Y-%m-%d")

        sh_all = _fetch("sh000001")
        sz_all = _fetch("sz399001")
        cy_all = _fetch("sz399006")

        def split(rows):
            today = [d for d in rows if d["day"].startswith(today_str)]
            yesterday = [d for d in rows if not d["day"].startswith(today_str)]
            return today, yesterday

        sh_t, sh_y = split(sh_all)
        sz_t, sz_y = split(sz_all)
        cy_t, cy_y = split(cy_all)

        if not sh_t:
            return None

        sh_prev = float(sh_y[-1]["close"]) if sh_y else None
        sz_prev = float(sz_y[-1]["close"]) if sz_y else None
        cy_prev = float(cy_y[-1]["close"]) if cy_y else None

        bars = []
        total_sh_amt = 0
        for i in range(len(sh_t)):
            s = sh_t[i]
            s_c = float(s["close"])
            s_a = float(s["amount"]) / 1e8
            total_sh_amt += s_a
            s_pct = round((s_c - sh_prev) / sh_prev * 100, 2) if sh_prev else 0

            z = sz_t[i] if i < len(sz_t) else None
            z_c = float(z["close"]) if z else 0
            z_pct = round((z_c - sz_prev) / sz_prev * 100, 2) if z and sz_prev else 0

            c = cy_t[i] if i < len(cy_t) else None
            c_c = float(c["close"]) if c else 0
            c_pct = round((c_c - cy_prev) / cy_prev * 100, 2) if c and cy_prev else 0

            bars.append({
                "time": s["day"][11:16],
                "sh": round(s_c, 0),
                "sh_pct": s_pct,
                "sz": round(z_c, 0),
                "sz_pct": z_pct,
                "cy": round(c_c, 0),
                "cy_pct": c_pct,
                "sh_amt": round(s_a, 0),
            })

        am_amt = sum(b["sh_amt"] for b in bars if b["time"] <= "11:30")
        am_pct = round(am_amt / total_sh_amt * 100, 1) if total_sh_amt > 0 else 0

        # 生成摘要
        sh_final = bars[-1]["sh_pct"] if bars else 0
        cy_final = bars[-1]["cy_pct"] if bars else 0
        vol_peak = max(b["sh_amt"] for b in bars) if bars else 0

        if sh_final > -0.5 and cy_final > -1:
            trend = "窄幅震荡"
        elif sh_final > -1 and cy_final < -3:
            trend = "权重护盘，小票走弱"
        elif abs(sh_final - cy_final) < 1.5:
            trend = "普跌" if sh_final < -1 else "普涨"
        else:
            trend = "分化加剧"

        if am_pct > 70 and sh_final < -0.5:
            summary = f"缩量{trend}，开盘放量后持续走低，资金早盘出逃午后休眠"
        elif am_pct > 70:
            summary = f"量能集中于早盘，上午占比{am_pct:.0f}%，午后缩量明显"
        else:
            summary = f"{trend}，量能分布较均匀"

        return {
            "bars": bars,
            "sh_prev_close": round(sh_prev, 0) if sh_prev else None,
            "sz_prev_close": round(sz_prev, 0) if sz_prev else None,
            "cy_prev_close": round(cy_prev, 0) if cy_prev else None,
            "total_sh_amt": round(total_sh_amt, 0),
            "am_pct": am_pct,
            "summary": summary,
        }
    except Exception:
        return None# ===================================================================
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


# ===================================================================
# 实时价格（新浪行情 API）
# ===================================================================

def fetch_realtime_price(code: str) -> dict | None:
    """
    获取个股实时行情（新浪 API）。
    返回: {name, open, prev_close, current, high, low, volume, amount, pct_change}
    """
    prefix = "sh" if code.startswith("6") else "sz"
    url = f"http://hq.sinajs.cn/list={prefix}{code}"
    try:
        import requests
        r = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=10)
        if r.status_code != 200 or not r.text.strip():
            return None
        parts = r.text.split(",")
        if len(parts) < 9:
            return None
        name = parts[0].split('"')[1] if '"' in parts[0] else ""
        return {
            "name": name,
            "open": float(parts[1]) if parts[1] else 0,
            "prev_close": float(parts[2]) if parts[2] else 0,
            "current": float(parts[3]) if parts[3] else 0,
            "high": float(parts[4]) if parts[4] else 0,
            "low": float(parts[5]) if parts[5] else 0,
            "volume": float(parts[8]) if len(parts) > 8 and parts[8] else 0,
            "amount": float(parts[9]) if len(parts) > 9 and parts[9] else 0,
            "pct_change": round((float(parts[3]) - float(parts[2])) / float(parts[2]) * 100, 2) if parts[2] and parts[3] else 0,
        }
    except Exception:
        return None


def fetch_realtime_prices(codes: list[str]) -> dict[str, dict]:
    """批量获取实时行情，返回 {code: {...}}"""
    result = {}
    for code in codes:
        data = fetch_realtime_price(code)
        if data:
            result[code] = data
        time.sleep(0.1)  # 避免请求过快
    return result
