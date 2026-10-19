"""
回测财务快照下载器。

拉取全A股票池的财务关键指标（EPS_TTM / ROE / 营收增速 / 净利增速），
保存到 data/backtest/financials.csv，作为回测中"价值/成长"评分的当前值代理。

说明：baostock 财务数据不带披露日字段，无法做严格 PIT，故采用"当前最新值"
代理，所有回测日期共用同一份快照。价值/成长是慢变量，周度内几乎不变，
此简化对"动量权重路由"这一核心假设的影响可忽略。

用法:
    PYTHONPATH=. PYTHONIOENCODING=utf-8 py tools/backtest_financials.py
"""

from pathlib import Path

import pandas as pd

# baostock 兼容 pandas 2.x（get_data 内部使用已移除的 DataFrame.append）
if not hasattr(pd.DataFrame, "append"):
    pd.DataFrame.append = lambda self, other, ignore_index=False, **kwargs: pd.concat(
        [self, other], ignore_index=ignore_index
    )

from config import DATA_DIR
from src.data.fetcher import fetch_stock_financials
from tools.backtest_data import get_universe

BACKTEST_DIR = DATA_DIR / "backtest"
FIN_FILE = BACKTEST_DIR / "financials.csv"


def load_done() -> set[str]:
    """返回已下载过的股票代码集合（用于断点续传）。"""
    if not FIN_FILE.exists():
        return set()
    try:
        df = pd.read_csv(FIN_FILE, dtype={"code": str})
        return set(df["code"].astype(str))
    except Exception:
        return set()


def _extract(code: str) -> dict:
    """提取回测所需 4 个财务字段（缺失填 None）。"""
    row = {"code": code, "eps_ttm": None, "roe": None,
           "revenue_growth": None, "profit_growth": None}
    try:
        fin = fetch_stock_financials(code)
    except Exception:
        fin = None
    if not fin:
        return row
    row["eps_ttm"] = fin.get("epsTTM")
    row["roe"] = fin.get("净资产收益率")
    row["revenue_growth"] = fin.get("营业收入增长率")
    row["profit_growth"] = fin.get("净利润增长率")
    return row


def _flush(rows: list[dict]):
    """把累积的行追加写入 CSV（带列头）。"""
    if not rows:
        return
    new = pd.DataFrame(rows)
    if FIN_FILE.exists():
        old = pd.read_csv(FIN_FILE, dtype={"code": str})
        combined = pd.concat([old, new], ignore_index=True)
    else:
        combined = new
    combined.to_csv(FIN_FILE, index=False)
    rows.clear()


def main():
    codes = get_universe()
    print(f"回测股票池: {len(codes)} 只")

    done = load_done()
    remaining = [c for c in codes if c not in done]
    print(f"已缓存: {len(done)}  待下载: {len(remaining)}")

    rows = []
    ok = fail = 0
    for i, code in enumerate(remaining):
        row = _extract(code)
        rows.append(row)  # 成功/失败都写一行（失败填 None），避免下次重复查询
        if row["roe"] is not None or row["eps_ttm"] is not None:
            ok += 1
        else:
            fail += 1
        if (i + 1) % 100 == 0:
            print(f"  进度 {i+1}/{len(remaining)}  ok={ok} fail={fail}")
            _flush(rows)

    _flush(rows)
    print(f"财务快照完成: ok={ok} fail={fail}  →  {FIN_FILE}")


if __name__ == "__main__":
    main()
