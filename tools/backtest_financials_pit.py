"""
回测 PIT 财务数据下载器（带披露日，消除前视偏差）。

与 backtest_financials.py（当前值快照）不同，本脚本拉取每只股票**多个报告期**
的财务指标，并记录每期的 `pubDate`（披露日）与 `statDate`（报告期截止日），
供回测引擎按「截至当周 pubDate <= T 的最新财报」做严格的 point-in-time 选股。

数据源：baostock query_profit_data（epsTTM/roeAvg）+ query_growth_data（YOYNI）。
字段：code, statDate, pubDate, epsTTM, roeAvg, YOYNI。

股票池缩减：用已下载的 kline 成交额筛流动性子集（近60日均额 > 阈值），
避免全A × 多期财报的数十小时下载。阈值默认 2 亿元，对应 ~2000 只中大市值股，
贴近 live 策略的 min_market_cap_billion=50 隐含池。

用法:
    PYTHONPATH=. PYTHONIOENCODING=utf-8 py tools/backtest_financials_pit.py
"""

from pathlib import Path

import pandas as pd

# baostock 兼容 pandas 2.x
if not hasattr(pd.DataFrame, "append"):
    pd.DataFrame.append = lambda self, other, ignore_index=False, **kwargs: pd.concat(
        [self, other], ignore_index=ignore_index
    )

from config import DATA_DIR
from src.data.fetcher import fetch_stock_profit_data, fetch_growth_data

BACKTEST_DIR = DATA_DIR / "backtest"
KLINE_DIR = BACKTEST_DIR / "kline"
UNIVERSE_FILE = BACKTEST_DIR / "universe.csv"
PIT_FILE = BACKTEST_DIR / "financials_pit.csv"

AMOUNT_THRESHOLD = 2e8   # 近60日均成交额 > 2 亿
LOOKBACK_PERIODS = 6     # 报告期数量（覆盖回测区间 + 披露滞后）


def build_universe() -> list[str]:
    """从 kline 成交额筛流动性子集，写 universe.csv。"""
    if UNIVERSE_FILE.exists():
        df = pd.read_csv(UNIVERSE_FILE, dtype={"code": str})
        return df["code"].tolist()

    codes = []
    for f in sorted(KLINE_DIR.glob("*.csv")):
        try:
            df = pd.read_csv(f, usecols=["amount"])
            amt = pd.to_numeric(df["amount"], errors="coerce").dropna()
            if len(amt) >= 60 and amt.tail(60).mean() > AMOUNT_THRESHOLD:
                codes.append(f.stem)
        except Exception:
            continue
    pd.DataFrame({"code": codes}).to_csv(UNIVERSE_FILE, index=False)
    return codes


def report_periods() -> list[tuple[int, int]]:
    """回测区间所需报告期（按时间正序，覆盖 2025Q1 ~ 2026Q2）。"""
    periods = []
    for year, quarters in ((2025, [1, 2, 3, 4]), (2026, [1, 2])):
        for q in quarters:
            periods.append((year, q))
    return periods[:LOOKBACK_PERIODS] if LOOKBACK_PERIODS else periods


def _num(row, col):
    try:
        v = row.get(col)
        return float(v) if v is not None and v == v else None  # 排除 NaN
    except (TypeError, ValueError):
        return None


def extract(code: str, periods: list[tuple[int, int]]) -> list[dict]:
    """提取单只股票多期财务，返回行列表（每个 (code,period) 一行）。"""
    rows = []
    for year, quarter in periods:
        row = {
            "code": code, "year": year, "quarter": quarter,
            "statDate": None, "pubDate": None,
            "epsTTM": None, "roeAvg": None, "YOYNI": None,
        }
        try:
            profit = fetch_stock_profit_data(code, year=year, quarter=quarter)
        except Exception:
            profit = None
        if profit is not None and not profit.empty:
            p = profit.iloc[0]
            row["statDate"] = str(p.get("statDate", ""))[:10] or None
            row["pubDate"] = str(p.get("pubDate", ""))[:10] or None
            row["epsTTM"] = _num(p, "epsTTM")
            row["roeAvg"] = _num(p, "roeAvg")

        try:
            growth = fetch_growth_data(code, year=year, quarter=quarter)
        except Exception:
            growth = None
        if growth is not None and not growth.empty:
            g = growth.iloc[0]
            if not row["statDate"]:
                row["statDate"] = str(g.get("statDate", ""))[:10] or None
            if not row["pubDate"]:
                row["pubDate"] = str(g.get("pubDate", ""))[:10] or None
            row["YOYNI"] = _num(g, "YOYNI")

        rows.append(row)
    return rows


def load_done() -> set[str]:
    if not PIT_FILE.exists():
        return set()
    try:
        df = pd.read_csv(PIT_FILE, dtype={"code": str})
        return set(df["code"].astype(str))
    except Exception:
        return set()


def _flush(rows: list[dict]):
    if not rows:
        return
    new = pd.DataFrame(rows)
    if PIT_FILE.exists():
        old = pd.read_csv(PIT_FILE, dtype={"code": str})
        combined = pd.concat([old, new], ignore_index=True)
    else:
        combined = new
    combined.to_csv(PIT_FILE, index=False)
    rows.clear()


def main():
    codes = build_universe()
    print(f"流动股票池: {len(codes)} 只（近60日均额>{AMOUNT_THRESHOLD/1e8:.0f}亿）")

    periods = report_periods()
    print(f"报告期: {len(periods)} 个 ({periods[0]} ~ {periods[-1]})")

    done = load_done()
    remaining = [c for c in codes if c not in done]
    print(f"已缓存: {len(done)}  待下载: {len(remaining)}")

    rows = []
    for i, code in enumerate(remaining):
        rows.extend(extract(code, periods))
        if (i + 1) % 100 == 0:
            print(f"  进度 {i+1}/{len(remaining)}")
            _flush(rows)

    _flush(rows)
    print(f"PIT 财务完成 → {PIT_FILE}")


if __name__ == "__main__":
    main()
