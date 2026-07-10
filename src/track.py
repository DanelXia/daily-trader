"""
每日价格跟踪脚本。和基准价比对，计算累计收益率。
用法: python src/track.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
pd.DataFrame.append = lambda self, other, ignore_index=False, **kwargs: pd.concat([self, other], ignore_index=ignore_index)

from src.data.fetcher import fetch_stock_kline, close

BASE_DIR = Path(__file__).parent.parent
BASELINE_FILE = BASE_DIR / "data" / "baseline.json"
TRACK_FILE = BASE_DIR / "data" / "tracking.json"


def track():
    # 读基准
    if not BASELINE_FILE.exists():
        print("基准文件不存在，请先运行 research")
        sys.exit(1)

    with open(BASELINE_FILE, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    # 读历史跟踪
    tracking = {}
    if TRACK_FILE.exists():
        with open(TRACK_FILE, "r", encoding="utf-8") as f:
            tracking = json.load(f)

    today = datetime.now().strftime("%Y-%m-%d")

    # 跳过非交易日
    if datetime.now().weekday() >= 5:
        print(f"{today} 周末，跳过")
        return

    # 获取今日价格
    print(f"=== {today} 每日跟踪 ===")
    print()

    total_return = 0
    results = []

    stocks = baseline.get("stocks", {})
    for code, info in stocks.items():
        name = info["name"]
        base_price = info["price"]

        df = fetch_stock_kline(code, days=5)
        if df is not None and not df.empty:
            today_price = float(df["close"].iloc[-1])
            pct = round((today_price - base_price) / base_price * 100, 2)
        else:
            today_price = base_price
            pct = 0

        results.append({
            "code": code, "name": name,
            "base_price": base_price,
            "today_price": today_price,
            "pct_change": pct,
            "industry": info.get("industry", ""),
        })
        total_return += pct
        sign = "+" if pct >= 0 else ""
        print(f"  {name}({code})  {base_price} -> {today_price}  ({sign}{pct}%)")

    avg_return = round(total_return / len(stocks), 2) if stocks else 0
    print(f"\n  等权平均收益: {avg_return:+.2f}%")

    # 保存
    tracking[today] = {
        "avg_return": avg_return,
        "stocks": results,
    }
    with open(TRACK_FILE, "w", encoding="utf-8") as f:
        json.dump(tracking, f, ensure_ascii=False, indent=2)

    # 累计统计
    if len(tracking) > 1:
        print(f"\n  累计跟踪 {len(tracking)} 天")
        all_returns = [d["avg_return"] for d in tracking.values()]
        cumulative = sum(all_returns)
        print(f"  累计收益: {cumulative:+.2f}%")


if __name__ == "__main__":
    track()
    close()
