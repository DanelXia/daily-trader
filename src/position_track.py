"""
持仓收益跟踪。基于 positions.json 中的建仓价，计算每笔持仓的浮动盈亏。
用法: python src/position_track.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
pd.DataFrame.append = lambda self, other, ignore_index=False, **kwargs: pd.concat([self, other], ignore_index=ignore_index)

from src.data.fetcher import fetch_stock_kline, close

BASE_DIR = Path(__file__).parent.parent
POSITION_FILE = BASE_DIR / "data" / "positions.json"
P_L_FILE = BASE_DIR / "data" / "pnl_history.json"


def track_positions():
    if not POSITION_FILE.exists():
        print("无持仓文件")
        return

    with open(POSITION_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    positions = data.get("positions", {})
    if not positions:
        print("无持仓")
        return

    # 读历史
    pnl_history = []
    if P_L_FILE.exists():
        with open(P_L_FILE, "r", encoding="utf-8") as f:
            pnl_history = json.load(f)

    today = datetime.now().strftime("%Y-%m-%d")
    if datetime.now().weekday() >= 5:
        print(f"{today} 周末，跳过")
        return

    print(f"=== {today} 持仓盈亏复盘 ===")
    print(f"账户资金: ¥{data['account_capital']:,}  |  建仓日期: {data['start_date']}")
    print()

    total_pnl = 0
    total_cost = 0
    day_results = []

    for code, pos in positions.items():
        name = pos["name"]
        entry = pos["entry_price"]
        shares = pos["shares"]
        cost = pos["cost"]
        stop = pos["stop_loss"]
        tp = pos["take_profit"]
        total_cost += cost

        df = fetch_stock_kline(code, days=5)
        if df is not None and not df.empty:
            current = float(df["close"].iloc[-1])
        else:
            current = entry

        pnl = (current - entry) * shares
        pnl_pct = round((current - entry) / entry * 100, 2)
        total_pnl += pnl

        # 止损止盈提醒
        alert = ""
        if current <= stop:
            alert = " *** 触发止损! ***"
        elif current >= tp:
            alert = " *** 触发止盈! ***"

        sign = "+" if pnl >= 0 else ""
        bar = _bar(pnl_pct, 20)
        print(f"  {name:{' '}{6}}({code})  ¥{entry:.2f} → ¥{current:.2f}  "
              f"{sign}{pnl_pct:.1f}%  ¥{sign}{pnl:.0f}  {bar}{alert}")

        day_results.append({
            "code": code, "name": name,
            "entry_price": entry, "current_price": current,
            "shares": shares, "cost": cost,
            "pnl": round(pnl, 2), "pnl_pct": pnl_pct,
            "stop_loss": stop, "take_profit": tp,
            "stop_triggered": current <= stop,
            "tp_triggered": current >= tp,
        })

    total_pnl_pct = round(total_pnl / total_cost * 100, 2) if total_cost else 0
    sign = "+" if total_pnl >= 0 else ""
    print(f"\n  总成本: ¥{total_cost:,}  |  浮动盈亏: {sign}¥{total_pnl:,.0f}  ({sign}{total_pnl_pct}%)")

    # 与等权持有 benchmark 对比
    if pnl_history:
        prev = pnl_history[-1]
        day_change = round(total_pnl_pct - prev["total_pnl_pct"], 2)
        print(f"  较昨日: {day_change:+.2f}%")

    print(f"\n  止损需关注: {', '.join(r['code'] for r in day_results if r['stop_triggered'])}" if any(r['stop_triggered'] for r in day_results) else "  无触发止损")
    print(f"  止盈需关注: {', '.join(r['code'] for r in day_results if r['tp_triggered'])}" if any(r['tp_triggered'] for r in day_results) else "  无触发止盈")

    # 保存
    pnl_history.append({
        "date": today,
        "total_cost": total_cost,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": total_pnl_pct,
        "positions": day_results,
    })
    with open(P_L_FILE, "w", encoding="utf-8") as f:
        json.dump(pnl_history, f, ensure_ascii=False, indent=2)


def _bar(pct: float, width: int = 20) -> str:
    """迷你进度条"""
    filled = int(abs(pct) / 5 * width)
    filled = min(filled, width)
    if pct >= 0:
        return "█" * filled + "░" * (width - filled)
    else:
        return "░" * (width - filled) + "█" * filled


if __name__ == "__main__":
    track_positions()
    close()
