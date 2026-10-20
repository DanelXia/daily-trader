"""
回测数据下载器。

拉全A（剔除北交所/ST/次新）近 13 个月前复权日线 + 指数日线到 data/backtest/，
供回测引擎按日期切片使用。支持断点续传（已存在的 CSV 跳过）。

用法:
    PYTHONPATH=. py tools/backtest_data.py
"""

import sys
from pathlib import Path
from datetime import datetime

import pandas as pd

# baostock 兼容 pandas 2.x（get_data 内部使用已移除的 DataFrame.append）
if not hasattr(pd.DataFrame, "append"):
    pd.DataFrame.append = lambda self, other, ignore_index=False, **kwargs: pd.concat(
        [self, other], ignore_index=ignore_index
    )

from config import BASE_DIR, DATA_DIR
from src.data.fetcher import fetch_all_stocks_basic, fetch_stock_kline, fetch_index_kline

BACKTEST_DIR = DATA_DIR / "backtest"
KLINE_DIR = BACKTEST_DIR / "kline"
KLINE_DIR.mkdir(parents=True, exist_ok=True)

# 近 13 个月（含 MA60 warmup）。fetch_stock_kline 内部会再往前加 60 日历天。
LOOKBACK_DAYS = 400


def is_ashare(code: str) -> bool:
    """沪深 A 股（沪主板6 / 深主板0 / 创业板3 / 科创板688=6）。排除北交所(8/9/4)。"""
    return len(code) == 6 and code[0] in "036"


def get_universe() -> list[str]:
    """构建回测股票池：沪深A股，剔除 ST 和上市不足 60 个交易日的次新。"""
    df = fetch_all_stocks_basic()
    if df is None or df.empty:
        print("[错误] 无法获取全A股列表")
        return []

    codes = []
    for _, row in df.iterrows():
        code = str(row["code"])
        if not is_ashare(code):
            continue
        name = str(row.get("name", "") or "")
        if "ST" in name.upper():
            continue
        ipo = str(row.get("ipo_date", "") or "")
        if ipo and ipo not in ("None", "", "nan"):
            try:
                if (datetime.now() - pd.to_datetime(ipo)).days < 60:
                    continue
            except Exception:
                pass
        codes.append(code)
    return codes


def download_kline(code: str) -> str:
    """下载单只股票日线，返回 ok / skip / empty / error。"""
    path = KLINE_DIR / f"{code}.csv"
    if path.exists():
        return "skip"
    try:
        df = fetch_stock_kline(code, days=LOOKBACK_DAYS, adjust="2")
    except Exception:
        return "error"
    if df is None or df.empty:
        return "empty"
    df.to_csv(path, index=False)
    return "ok"


def download_index(code: str, name: str) -> str | None:
    """下载指数日线，返回文件路径。"""
    path = BACKTEST_DIR / f"index_{name}.csv"
    df = fetch_index_kline(code, days=LOOKBACK_DAYS)
    if df is None or df.empty:
        return None
    df.to_csv(path, index=False)
    return str(path)


def download_industry() -> str | None:
    """下载证监会行业分类映射（code → 行业大类），写 industry.csv。"""
    from src.data.fetcher import fetch_stock_industry_map
    df = fetch_stock_industry_map()
    if df is None or df.empty:
        return None
    df = df[["code", "industry"]].dropna()
    df["industry"] = df["industry"].astype(str).str.strip()
    df = df[df["industry"] != ""]
    path = BACKTEST_DIR / "industry.csv"
    df.to_csv(path, index=False)
    return str(path)


def main():
    codes = get_universe()
    print(f"回测股票池: {len(codes)} 只")

    ok = skip = empty = error = 0
    for i, code in enumerate(codes):
        r = download_kline(code)
        if r == "ok":
            ok += 1
        elif r == "skip":
            skip += 1
        elif r == "empty":
            empty += 1
        else:
            error += 1
        if (i + 1) % 100 == 0:
            print(f"  进度 {i+1}/{len(codes)}  ok={ok} skip={skip} empty={empty} error={error}")

    print(f"个股日线完成: ok={ok} skip={skip} empty={empty} error={error}")

    for idx_code, idx_name in (("sh.000001", "sh"), ("sh.000300", "hs300")):
        p = download_index(idx_code, idx_name)
        print(f"指数 {idx_name}: {'已保存 ' + p if p else '失败'}")

    ind_path = download_industry()
    print(f"行业分类: {'已保存 ' + ind_path if ind_path else '失败'}")


if __name__ == "__main__":
    main()
