"""
用今日实时价格重新分析股票池，寻找可建仓标的。
将新浪实时行情作为最新一根K线追加到 baostock 日K，重算技术指标与交易信号。
"""
import json
import numpy as np
import pandas as pd
from datetime import datetime

from config import STOCK_POOL_FILE
from src.data.fetcher import (
    fetch_stock_kline, fetch_realtime_price, fetch_shareholder_data,
    fetch_index_snapshot,
)
from src.trading.technical import (
    _sma, _macd, _rsi, _bbands, _atr,
    _detect_doji, _detect_hammer, _detect_engulfing, TechnicalAnalyzer,
)
from src.trading.signals import TradingSignalEngine
from config import MA_PERIODS, MACD_PARAMS, RSI_PERIODS, BB_PERIOD, BB_STD, ATR_PERIOD


def compute_with_realtime(code: str, rt: dict) -> dict | None:
    """复制 TechnicalAnalyzer.compute_all 逻辑，但把实时价作为最新K线追加。"""
    df = fetch_stock_kline(code, days=120)
    if df is None or df.empty or len(df) < 60:
        return None

    today = pd.Timestamp(datetime.now().strftime("%Y-%m-%d"))
    if df.iloc[-1]["date"] == today:
        df = df.iloc[:-1].copy()  # 已有今日行则替换

    new_row = {
        "date": today,
        "open": rt["open"],
        "high": rt["high"],
        "low": rt["low"],
        "close": rt["current"],
        "volume": rt["volume"],
        "amount": rt["amount"],
        "turnover": 0.0,
        "pct_change": rt["pct_change"],
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    close = df["close"].astype(float).values
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    volume = df["volume"].astype(float).values

    ma = {}
    for p in MA_PERIODS:
        ma[f"ma{p}"] = round(float(_sma(close, p)[-1]), 2)

    macd_line, signal_line, histogram = _macd(close, *MACD_PARAMS)
    last_valid = np.argwhere(~np.isnan(macd_line))[-1][0] if np.any(~np.isnan(macd_line)) else -1

    rsi = {}
    for p in RSI_PERIODS:
        rsi_arr = _rsi(close, p)
        rsi[f"rsi{p}"] = round(float(rsi_arr[-1]), 1) if not np.isnan(rsi_arr[-1]) else None

    bb_upper, bb_middle, bb_lower = _bbands(close, BB_PERIOD, BB_STD)
    atr_arr = _atr(high, low, close, ATR_PERIOD)

    vol_20d = np.mean(volume[-20:]) if len(volume) >= 20 else np.mean(volume)
    vol_ratio = round(float(volume[-1] / vol_20d), 2) if vol_20d > 0 else 1.0
    vol_5d_trend = np.mean(volume[-5:]) > np.mean(volume[-10:-5]) if len(volume) >= 10 else False

    up_vol = np.sum(volume[-5:][np.diff(close[-6:]) > 0]) if len(close) >= 6 else 0
    down_vol = np.sum(volume[-5:][np.diff(close[-6:]) < 0]) if len(close) >= 6 else 0
    up_down_vol = round(float(up_vol / down_vol), 2) if down_vol > 0 else 1.0

    patterns = []
    patterns.extend(_detect_doji(df.tail(20)))
    patterns.extend(_detect_hammer(df.tail(20)))
    patterns.extend(_detect_engulfing(df.tail(20)))

    ta = TechnicalAnalyzer()
    supports, resistances = ta._find_levels(df)

    return {
        "code": code,
        "price": round(float(close[-1]), 2),
        "ma": ma,
        "macd": {
            "macd": round(float(macd_line[-1]), 3) if last_valid >= 0 else None,
            "signal": round(float(signal_line[-1]), 3) if last_valid >= 0 else None,
            "histogram": round(float(histogram[-1]), 3) if last_valid >= 0 else None,
        },
        "rsi": rsi,
        "bb": {
            "upper": round(float(bb_upper[-1]), 2),
            "middle": round(float(bb_middle[-1]), 2),
            "lower": round(float(bb_lower[-1]), 2),
        },
        "atr14": round(float(atr_arr[-1]), 2) if not np.isnan(atr_arr[-1]) else round(float(np.mean(high - low)), 2),
        "volume": {
            "ratio": vol_ratio,
            "trend_up": bool(vol_5d_trend),
            "up_down_ratio": up_down_vol,
        },
        "patterns": patterns,
        "support_levels": supports,
        "resistance_levels": resistances,
    }


def main():
    with open(STOCK_POOL_FILE, "r", encoding="utf-8") as f:
        pool = json.load(f)

    stocks = []
    for ind in pool["industries"]:
        for s in ind["stocks"]:
            stocks.append({"code": s["code"], "name": s["name"], "industry": ind["industry_name"]})

    market = fetch_index_snapshot()
    mvr = market.get("market_volume_ratio", 1.0)
    print(f"大盘量比: {mvr}  上证: {market.get('sh_index')} ({market.get('sh_change_pct')}%)  "
          f"深证: {market.get('sz_index')} ({market.get('sz_change_pct')}%)")

    engine = TradingSignalEngine()
    results = []

    for s in stocks:
        code = s["code"]
        rt = fetch_realtime_price(code)
        if not rt:
            print(f"[跳过] {s['name']}({code}) 实时行情获取失败")
            continue

        inds = compute_with_realtime(code, rt)
        if inds is None:
            print(f"[跳过] {s['name']}({code}) K线数据不足")
            continue

        conc = None
        try:
            conc = fetch_shareholder_data(code)
        except Exception:
            pass

        sig = engine.analyze(code, s["name"], inds, mvr, None, conc)
        sig["industry"] = s["industry"]
        sig["realtime"] = {
            "current": rt["current"],
            "prev_close": rt["prev_close"],
            "pct_change": rt["pct_change"],
        }
        results.append(sig)

    # 排序：建仓信号优先（strong_buy > buy），同信号按分数
    action_rank = {"strong_buy": 0, "buy": 1, "hold": 2, "reduce": 3, "strong_sell": 4}
    results.sort(key=lambda r: (action_rank.get(r["action"], 9), -r["trading_score"]))

    print("\n" + "=" * 100)
    print(f"{'代码':<8}{'名称':<10}{'信号':<12}{'分数':>6}  {'实时价':>8}  {'涨跌':>7}  "
          f"{'入场价':>8}  {'止损':>8}  {'止盈1':>8}  风险  筹码")
    print("=" * 100)
    for r in results:
        rt = r["realtime"]
        conc = r.get("concentration") or {}
        hold_txt = ""
        if conc:
            hold_txt = f"{conc['holder_count']/10000:.1f}万户({conc['holder_change_pct']:+.0%})"
        print(f"{r['code']:<8}{r['name']:<10}{r['action']:<12}{r['trading_score']:>6}  "
              f"{rt['current']:>8}  {rt['pct_change']:>+6.2f}%  "
              f"{r['entry']['price']:>8}  {r['stop_loss']:>8}  {r['take_profit']['tp1']:>8}  "
              f"{r['risk_assessment']:<4}  {hold_txt}")

    print("\n" + "=" * 100)
    print("可建仓候选（BUY 及以上）：")
    print("=" * 100)
    for r in results:
        if r["action"] in ("strong_buy", "buy"):
            rt = r["realtime"]
            print(f"\n■ {r['name']}({r['code']})  [{r['action'].upper()}] {r['trading_score']}分")
            print(f"  实时价 {rt['current']} ({rt['pct_change']:+.2f}%)  |  入场 {r['entry']['price']}  |  "
                  f"止损 {r['stop_loss']}  |  止盈 {r['take_profit']['tp1']}({r['take_profit']['tp1_pct']}%)")
            print(f"  风险 {r['risk_assessment']}  |  仓位 {r['position_pct']}%")
            for n in r.get("key_notes", []):
                print(f"    - {n}")

    with open("data/realtime_reanalysis.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print("\n已保存 data/realtime_reanalysis.json")


if __name__ == "__main__":
    main()
