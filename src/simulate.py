"""
纸面交易模拟引擎（Paper Trading）。

10 万虚拟本金，每日按「场景路由选股 → 技术信号 → 买卖执行 → 记账盈亏+原因」模拟实盘，
每周复盘一次。复用实盘代码 regime / selection_models / signals / fetcher，但资金与持仓
独立存于 data/paper/，不影响实盘 data/positions.json 等文件。

命令（在仓库根目录，用 py 3.12）：
  py -m src.simulate weekly    # 每周全池选股（离线，秒出），生成 data/paper/watchlist.json
  py -m src.simulate daily     # 每日盘后：估值 + 止盈止损 + 信号买卖 + 记账
  py -m src.simulate review    # 每周复盘（量化绩效 + 逐笔盈亏与原因）
  py -m src.simulate status    # 查看当前持仓与账户
  py -m src.simulate reset --yes  # 重置为 10 万本金（清空持仓/交易/净值历史）

口径：
  - 成交价 = 盘后收盘价（daily 应在 15:00 收盘后运行，用当日收盘价估值与成交）。
  - 选股节奏 = 每周全池选股一次（weekly），每日仅对持仓 + 股票池(~10只)做技术信号。
  - 单只最大仓位 10%；总仓位上限随场景：BULL 80% / BEAR 20% / SIDEWAYS 50%（HIGHVOL ×0.5）。
  - 止盈止损按场景固定：BEAR -5%/+10%，SIDEWAYS -5%/+15%，BULL -8%/+20%。
  - 交易成本：佣金万2.5(双边) + 印花税千1(卖) + 滑点0.1%(双边)。
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

if not hasattr(pd.DataFrame, "append"):
    pd.DataFrame.append = lambda self, other, ignore_index=False, **kwargs: pd.concat(
        [self, other], ignore_index=ignore_index
    )

from config import DATA_DIR, OUTPUT_DIR  # noqa: E402
from src.research.selection_models import get_model  # noqa: E402
from src.research.regime import MarketRegimeDetector  # noqa: E402

BASE_DIR = Path(__file__).parent.parent
PAPER_DIR = DATA_DIR / "paper"
OUT_PAPER_DIR = OUTPUT_DIR / "paper"
PORTFOLIO_FILE = PAPER_DIR / "portfolio.json"
TRADE_LOG_FILE = PAPER_DIR / "trade_log.json"
EQUITY_FILE = PAPER_DIR / "equity_history.json"
WATCHLIST_FILE = PAPER_DIR / "watchlist.json"

INITIAL_CAPITAL = 100_000.0
SINGLE_STOCK_PCT = 0.10                      # 单只最大仓位 10%
BUY_COST_RATE = 0.00025 + 0.001              # 佣金万2.5 + 滑点0.1%
SELL_COST_RATE = 0.00025 + 0.001 + 0.001     # 佣金 + 印花税千1 + 滑点

REGIME_MODEL_MAP = {"BULL": "momentum", "BEAR": "defensive", "SIDEWAYS": "mean_reversion"}
POSITION_BY_REGIME = {"BULL": 0.8, "BEAR": 0.2, "SIDEWAYS": 0.5}
STOP_LOSS_PCT = {"BULL": -0.08, "BEAR": -0.05, "SIDEWAYS": -0.05}
TAKE_PROFIT_PCT = {"BULL": 0.20, "BEAR": 0.10, "SIDEWAYS": 0.15}

ACTION_CN = {
    "strong_buy": "强烈买入", "buy": "买入", "hold": "观望",
    "reduce": "减仓", "strong_sell": "强烈卖出",
}


def _score_str(signal):
    """信号得分后缀，如 '(62.5分)'；signal 为空返回 ''。"""
    return f"({signal['trading_score']}分)" if signal else ""


# ===================================================================
# 文件读写
# ===================================================================
def _load_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _default_portfolio() -> dict:
    return {
        "initial_capital": INITIAL_CAPITAL,
        "cash": INITIAL_CAPITAL,
        "start_date": _today(),
        "positions": {},
    }


def load_portfolio() -> dict:
    p = _load_json(PORTFOLIO_FILE, None)
    if p is None:
        p = _default_portfolio()
    return p


def save_portfolio(p: dict):
    _save_json(PORTFOLIO_FILE, p)


def load_trade_log() -> list:
    return _load_json(TRADE_LOG_FILE, [])


def save_trade_log(log: list):
    _save_json(TRADE_LOG_FILE, log)


def load_equity() -> list:
    return _load_json(EQUITY_FILE, [])


def save_equity(hist: list):
    _save_json(EQUITY_FILE, hist)


def load_watchlist() -> dict | None:
    return _load_json(WATCHLIST_FILE, None)


# ===================================================================
# 场景识别（live，读当前指数；不写 regime_state 等实盘状态文件）
# ===================================================================
def detect_regime_state() -> dict:
    """返回 {regime, highvol, target_position_pct, model, model_label, technical_style, features}。"""
    detection = MarketRegimeDetector().detect()
    if detection is None:
        regime = "SIDEWAYS"
        highvol = False
        features = {}
    else:
        regime = detection["regime"]
        highvol = detection["highvol"]
        features = detection.get("features", {})
    model = get_model(REGIME_MODEL_MAP[regime])
    target_pos = POSITION_BY_REGIME[regime] * (0.5 if highvol else 1.0)
    return {
        "regime": regime,
        "highvol": highvol,
        "target_position_pct": round(target_pos, 2),
        "model": model,
        "model_label": model.label,
        "technical_style": model.technical_style,
        "features": features,
    }


# ===================================================================
# 每周全池选股（离线，秒出；复用 tools/backtest.py 的 PIT 打分）
# ===================================================================
def cmd_weekly(top_n: int = 10):
    sys.path.insert(0, str(BASE_DIR / "tools"))
    from backtest import (  # noqa: E402
        load_stocks, load_index, detect_regime_at,
        _score_technical, _score_value, _score_growth, financials_at,
    )

    names = {}
    try:
        from src.data.fetcher import fetch_all_stocks_basic  # noqa: E402
        df = fetch_all_stocks_basic()
        names = dict(zip(df["code"].astype(str).str.zfill(6), df["name"].astype(str)))
    except Exception:
        pass

    stocks = load_stocks()
    idx = load_index("sh")
    idx_dates = idx["date"].to_numpy(dtype="datetime64[ns]")
    idx_close = pd.to_numeric(idx["close"], errors="coerce").to_numpy(dtype="float64")
    T = np.datetime64(idx_dates[-1], "ns")
    regime = detect_regime_at(idx_dates, idx_close, T) or "SIDEWAYS"
    model = get_model(REGIME_MODEL_MAP[regime])

    print(f"场景: {regime} → 模型: {model.label}（technical_style={model.technical_style}）")
    print(f"最新交易日: {pd.Timestamp(T).date()}")

    rows = []
    for code, d in stocks.items():
        i = int(np.searchsorted(d["dates"], T, side="right"))
        if i < 20:
            continue
        tech, price = _score_technical(d["close"][:i], d["volume"][:i], model.technical_style)
        fin = financials_at(d["fin"], T)
        value = _score_value(fin, price)
        growth = _score_growth(fin)
        w = model.stock_weights
        total = (value / 50 * w["value"]) + (growth / 20 * w["growth"]) + (tech / 30 * w["technical"])
        eps = fin.get("eps_ttm") if fin else None
        pe = round(price / eps, 1) if (eps and eps > 0) else None
        roe = round(fin.get("roe"), 1) if fin and fin.get("roe") is not None else None
        rows.append({
            "code": code,
            "name": names.get(code, code),
            "composite_score": round(total, 1),
            "value": round(value, 1), "growth": round(growth, 1), "tech": round(tech, 1),
            "price": round(price, 2), "pe": pe, "roe": roe,
        })

    rows.sort(key=lambda x: -x["composite_score"])
    top = rows[:top_n]

    watchlist = {
        "date": str(pd.Timestamp(T).date()),
        "regime": regime,
        "model": REGIME_MODEL_MAP[regime],
        "model_label": model.label,
        "target_position_pct": POSITION_BY_REGIME[regime],
        "stocks": top,
    }
    _save_json(WATCHLIST_FILE, watchlist)

    print(f"\nTop{len(top)}（{model.label}，目标仓位 {POSITION_BY_REGIME[regime] * 100:.0f}%）:")
    print("-" * 64)
    for i, r in enumerate(top):
        pe = f"{r['pe']}" if r["pe"] is not None else "-"
        roe = f"{r['roe']}%" if r["roe"] is not None else "-"
        print(f"{i + 1}. {r['name']}({r['code']})  综合={r['composite_score']}  "
              f"价={r['price']}  PE={pe}  ROE={roe}")
        print(f"    价值{r['value']} / 成长{r['growth']} / 技术{r['tech']}")
    print(f"\n股票池已写入: {WATCHLIST_FILE}")


# ===================================================================
# 每日模拟交易
# ===================================================================
def _analyze(code: str, name: str, regime_state: dict):
    """计算单只股票指标与信号；数据不足返回 (None, None)。"""
    from src.trading.technical import TechnicalAnalyzer
    from src.trading.signals import TradingSignalEngine

    ta = TechnicalAnalyzer()
    engine = TradingSignalEngine()
    indicators = ta.compute_all(code)
    if indicators is None:
        return None, None
    rs = {"regime": regime_state["regime"], "highvol": regime_state["highvol"],
          "target_position_pct": regime_state["target_position_pct"]}
    signal = engine.analyze(code, name, indicators, regime_state=rs)
    return indicators, signal


def cmd_daily(date: str | None = None):
    today = date or _today()
    backfill = date is not None
    try:
        wkday = datetime.strptime(today, "%Y-%m-%d").weekday()
    except ValueError:
        print(f"日期格式错误: {today}，应为 YYYY-MM-DD")
        return
    if wkday >= 5:
        print(f"{today} 周末，非交易日，跳过")
        return

    equity_hist = load_equity()
    trade_log = load_trade_log()

    if backfill:
        # 补跑历史交易日：撤销该日已有净值记录，用最新可得数据重跑。
        # 若该日已有成交，需回滚交易才能重跑，这里保守拒绝以免破坏持仓一致性。
        day_trades = [t for t in trade_log if t.get("date") == today]
        if day_trades:
            print(f"[中止] {today} 已有 {len(day_trades)} 笔成交，补跑需回滚交易，"
                  f"请先 reset 或手动处理")
            return
        prev = [e for e in equity_hist if e.get("date") != today]
        if len(prev) == len(equity_hist):
            print(f"[补跑] {today} 暂无净值记录，按最新数据重跑")
        else:
            print(f"[补跑] 清除 {today} 旧净值记录，按最新数据重跑")
        equity_hist = prev
    else:
        if equity_hist and equity_hist[-1].get("date") == today:
            print(f"{today} 已运行过（净值历史最后一条为今日），跳过避免重复记账")
            return
        if datetime.now().hour < 15:
            print("[警告] 当前未到收盘(15:00)，将用最新可得收盘价（可能为上一交易日），"
                  "建议盘后运行")

    portfolio = load_portfolio()
    watchlist = load_watchlist()
    rs = detect_regime_state()
    regime = rs["regime"]
    model_label = rs["model_label"]
    max_positions = max(1, int(rs["target_position_pct"] / SINGLE_STOCK_PCT))

    print("=" * 64)
    print(f"  模拟盘每日交易  {today}  {datetime.now().strftime('%H:%M')}")
    print("=" * 64)
    print(f"  场景: {regime}  →  模型: {model_label}  →  目标仓位 {rs['target_position_pct'] * 100:.0f}%"
          f"  →  最多 {max_positions} 只")
    print(f"  现金: {portfolio['cash']:,.2f}  |  持仓: {len(portfolio['positions'])} 只\n")

    # ---- 1) 持仓：估值 + 止盈止损 + 信号 ---- #
    trades_today = []
    to_sell = []
    for code, pos in list(portfolio["positions"].items()):
        indicators, signal = _analyze(code, pos["name"], rs)
        if indicators is None:
            print(f"  [持仓] {pos['name']}({code}) K线不足，跳过")
            continue
        price = indicators["price"]
        pos["price"] = round(price, 2)  # 更新最新收盘价用于估值

        reason = None
        if price <= pos["stop_loss"]:
            reason = f"止损（现价{price:.2f} ≤ 止损{pos['stop_loss']:.2f}）"
        elif price >= pos["take_profit"]:
            reason = f"止盈（现价{price:.2f} ≥ 目标{pos['take_profit']:.2f}）"
        elif signal is not None and signal["action"] == "strong_sell":
            reason = f"信号转弱（{ACTION_CN.get(signal['action'])} {signal['trading_score']}分）"
        else:
            act = ACTION_CN.get(signal["action"], signal["action"]) if signal else "-"
            print(f"  [持仓] {pos['name']}({code})  价={price:.2f}  "
                  f"信号={act}{_score_str(signal)}")
            if signal and signal["action"] == "reduce":
                print(f"        [提示] 减仓信号，模拟盘暂不自动减仓，留待周复盘评估")
            continue

        to_sell.append((code, pos, price, reason, signal))
        if signal:
            print(f"  [持仓] {pos['name']}({code})  价={price:.2f}  "
                  f"信号={ACTION_CN.get(signal['action'])}{_score_str(signal)}")

    # ---- 2) 执行卖出 ---- #
    for code, pos, price, reason, signal in to_sell:
        shares = pos["shares"]
        proceeds = shares * price * (1 - SELL_COST_RATE)
        pnl = proceeds - pos["cost"]
        pnl_pct = (pnl / pos["cost"] * 100) if pos["cost"] else 0.0
        portfolio["cash"] += proceeds
        del portfolio["positions"][code]
        decision = {
            "exit_trigger": reason,
            "entry_context": {
                "entry_price": pos.get("entry_price"),
                "entry_date": pos.get("entry_date"),
                "entry_reason": pos.get("entry_reason"),
                "cost": pos.get("cost"),
                "stop_loss": pos.get("stop_loss"),
                "take_profit": pos.get("take_profit"),
            },
            "exit_signal": {
                "action": signal["action"],
                "action_cn": ACTION_CN.get(signal["action"]),
                "trading_score": signal["trading_score"],
                "key_notes": signal.get("key_notes"),
            } if signal else None,
        }
        trade_log.append({
            "date": today, "action": "SELL", "code": code, "name": pos["name"],
            "shares": shares, "price": round(price, 2),
            "amount": round(proceeds, 2), "reason": reason,
            "regime": regime, "model": model_label,
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
            "entry_price": pos["entry_price"], "entry_date": pos["entry_date"],
            "decision": decision,
        })
        trades_today.append(f"卖出 {pos['name']} {shares}股 @{price:.2f}  {reason}  盈亏{pnl:+,.2f}")
        print(f"  [卖出] {pos['name']}({code})  {shares}股 @{price:.2f}  {reason}  "
              f"盈亏 {pnl:+,.2f} ({pnl_pct:+.2f}%)")

    # ---- 3) 买入候选（按综合分排序，信号 BUY/STRONG BUY 且仓位有空余）---- #
    if watchlist is None or not watchlist.get("stocks"):
        print("\n[警告] 未找到股票池 data/paper/watchlist.json，请先运行 weekly；今日仅处理持仓止盈止损")
    else:
        equity_now = portfolio["cash"] + sum(
            p["shares"] * p.get("price", p["entry_price"])
            for p in portfolio["positions"].values()
        )
        print(f"\n  [选股] 股票池 {watchlist.get('date', '?')}（{watchlist.get('model_label', '')}），"
              f"逐只评估买入信号：")

        bought = 0
        for cand in watchlist["stocks"]:
            code = cand["code"]
            if code in portfolio["positions"]:
                continue
            if len(portfolio["positions"]) >= max_positions:
                print(f"  [止] 已满 {max_positions} 只，停止建仓")
                break
            indicators, signal = _analyze(code, cand["name"], rs)
            if indicators is None:
                print(f"  [候选] {cand['name']}({code}) K线不足，跳过")
                continue
            price = indicators["price"]
            act = signal["action"] if signal else "hold"
            if act not in ("strong_buy", "buy"):
                print(f"  [候选] {cand['name']}({code})  {ACTION_CN.get(act, act)}"
                      f"{_score_str(signal)}，不买入")
                continue

            budget = min(equity_now * SINGLE_STOCK_PCT, portfolio["cash"])
            shares = int(budget / price / 100) * 100
            if shares < 100:
                continue
            cost = shares * price * (1 + BUY_COST_RATE)
            if cost > portfolio["cash"]:
                continue

            stop_loss = round(price * (1 + STOP_LOSS_PCT[regime]), 2)
            take_profit = round(price * (1 + TAKE_PROFIT_PCT[regime]), 2)
            reason = (f"{regime}/{model_label} 信号{ACTION_CN.get(act)}"
                      f"{_score_str(signal)} "
                      f"综合{cand['composite_score']}")
            portfolio["cash"] -= cost
            portfolio["positions"][code] = {
                "name": cand["name"], "shares": shares,
                "entry_price": round(price, 2), "entry_date": today,
                "cost": round(cost, 2), "stop_loss": stop_loss,
                "take_profit": take_profit, "price": round(price, 2),
                "entry_reason": reason,
            }
            decision = {
                "market": {
                    "regime": regime,
                    "highvol": rs.get("highvol"),
                    "target_position_pct": rs.get("target_position_pct"),
                    "features": rs.get("features"),
                },
                "selection": {
                    "composite_score": cand.get("composite_score"),
                    "value": cand.get("value"),
                    "growth": cand.get("growth"),
                    "tech": cand.get("tech"),
                    "pe": cand.get("pe"),
                    "roe": cand.get("roe"),
                },
                "signal": {
                    "action": signal["action"] if signal else None,
                    "action_cn": ACTION_CN.get(signal["action"]) if signal else None,
                    "trading_score": signal["trading_score"] if signal else None,
                    "entry": signal.get("entry") if signal else None,
                    "stop_loss": signal.get("stop_loss") if signal else None,
                    "take_profit": signal.get("take_profit") if signal else None,
                    "key_notes": signal.get("key_notes") if signal else None,
                },
                "sizing": {
                    "single_stock_pct": SINGLE_STOCK_PCT,
                    "budget": round(budget, 2),
                    "shares": shares,
                    "cost": round(cost, 2),
                },
                "reason": reason,
            }
            trade_log.append({
                "date": today, "action": "BUY", "code": code, "name": cand["name"],
                "shares": shares, "price": round(price, 2),
                "amount": round(cost, 2), "reason": reason,
                "regime": regime, "model": model_label,
                "signal_score": signal["trading_score"] if signal else None,
                "stop_loss": stop_loss, "take_profit": take_profit,
                "pnl": None, "pnl_pct": None,
                "decision": decision,
            })
            trades_today.append(f"买入 {cand['name']} {shares}股 @{price:.2f}  {reason}")
            print(f"  [买入] {cand['name']}({code})  {shares}股 @{price:.2f}  "
                  f"成本{cost:,.2f}  止损{stop_loss}  止盈{take_profit}")
            bought += 1
            equity_now = portfolio["cash"] + sum(
                p["shares"] * p.get("price", p["entry_price"])
                for p in portfolio["positions"].values()
            )
        if bought == 0 and len(portfolio["positions"]) < max_positions:
            print("  [无] 股票池内今日无满足 BUY 信号的标的")

    # ---- 4) 记账 + 净值 ---- #
    market_value = sum(
        p["shares"] * p.get("price", p["entry_price"])
        for p in portfolio["positions"].values()
    )
    equity = portfolio["cash"] + market_value
    pnl = equity - portfolio["initial_capital"]
    pnl_pct = pnl / portfolio["initial_capital"] * 100

    equity_hist.append({
        "date": today, "cash": round(portfolio["cash"], 2),
        "market_value": round(market_value, 2), "equity": round(equity, 2),
        "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
        "regime": regime,
        "positions": {
            code: {"name": p["name"], "shares": p["shares"],
                   "price": p.get("price", p["entry_price"])}
            for code, p in portfolio["positions"].items()
        },
    })

    save_portfolio(portfolio)
    save_trade_log(trade_log)
    save_equity(equity_hist)

    print(f"\n{'=' * 64}")
    print(f"  今日完成  |  现金 {portfolio['cash']:,.2f}  |  市值 {market_value:,.2f}")
    print(f"  账户权益 {equity:,.2f}  |  累计盈亏 {pnl:+,.2f} ({pnl_pct:+.2f}%)")
    if trades_today:
        print(f"  今日成交 {len(trades_today)} 笔")
        for t in trades_today:
            print(f"    · {t}")
    print(f"{'=' * 64}")


# ===================================================================
# 每周复盘
# ===================================================================
def _print_decision_basis(t: dict, indent: str = "        "):
    """打印一笔已平仓交易的决策依据（入场依据 + 出场信号关键要点）。"""
    d = t.get("decision") or {}
    ec = d.get("entry_context") or {}
    if ec.get("entry_reason"):
        print(f"{indent}入场依据: {ec['entry_reason']}")
    es = d.get("exit_signal") or {}
    if es:
        notes = "；".join(es.get("key_notes") or []) or "无"
        print(f"{indent}出场信号: {es.get('action_cn', '-')} "
              f"{es.get('trading_score', '-')}分 | {notes}")


def cmd_review():
    portfolio = load_portfolio()
    trade_log = load_trade_log()
    equity_hist = load_equity()

    start_date = portfolio.get("start_date", "-")
    closed = [t for t in trade_log if t.get("action") == "SELL"]
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl") or 0) <= 0]
    realized = sum(t.get("pnl") or 0 for t in closed)

    market_value = sum(
        p["shares"] * p.get("price", p["entry_price"])
        for p in portfolio["positions"].values()
    )
    equity = portfolio["cash"] + market_value
    total_pnl = equity - portfolio["initial_capital"]
    total_pnl_pct = total_pnl / portfolio["initial_capital"] * 100

    # 最大回撤（净值序列）
    max_dd = 0.0
    peak = None
    for e in equity_hist:
        eq = e.get("equity", portfolio["initial_capital"])
        peak = eq if peak is None else max(peak, eq)
        if peak > 0:
            dd = (eq - peak) / peak * 100
            max_dd = min(max_dd, dd)

    # 基准（沪深300）
    bench = None
    try:
        from src.data.fetcher import fetch_index_kline
        df = fetch_index_kline("sh.000300", days=90)
        if df is not None and not df.empty and start_date != "-":
            s = pd.to_datetime(start_date)
            d = df[df["date"] >= s]
            if not d.empty and len(d) >= 2:
                bench = (float(d.iloc[-1]["close"]) / float(d.iloc[0]["close"]) - 1) * 100
    except Exception:
        bench = None

    # 周度分组
    weeks = {}
    for t in closed:
        wk = pd.to_datetime(t["date"]).strftime("%G-W%V")
        weeks.setdefault(wk, []).append(t)

    print("=" * 64)
    print(f"  模拟盘周复盘  {_today()}")
    print("=" * 64)
    print(f"  起始 {start_date}  |  初始本金 {portfolio['initial_capital']:,.0f}")
    print(f"  当前权益 {equity:,.2f}  |  累计盈亏 {total_pnl:+,.2f} ({total_pnl_pct:+.2f}%)")
    print(f"  已实现盈亏 {realized:+,.2f}  |  已平仓 {len(closed)} 笔"
          f"（盈 {len(wins)} / 亏 {len(losses)}）")
    if closed:
        print(f"  胜率 {len(wins) / len(closed) * 100:.1f}%")
        avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(t['pnl'] for t in losses) / len(losses)) if losses else 0
        print(f"  盈亏比 {avg_win / avg_loss:.2f}" if avg_loss else "  盈亏比 -")
    print(f"  最大回撤 {max_dd:.2f}%  |  沪深300同期 {bench:+.2f}%" if bench is not None
          else f"  最大回撤 {max_dd:.2f}%")
    print(f"  当前持仓 {len(portfolio['positions'])} 只（市值 {market_value:,.2f}）")
    for code, p in portfolio["positions"].items():
        up = (p.get("price", 0) / p["entry_price"] - 1) * 100 if p["entry_price"] else 0
        print(f"    · {p['name']}({code})  {p['shares']}股  "
              f"成本{p['entry_price']} 现价{p.get('price')}  {up:+.2f}%")

    if closed:
        print(f"\n  逐笔平仓记录:")
        print("  " + "-" * 60)
        for t in closed:
            print(f"    {t['date']}  {t['name']}({t['code']})  "
                  f"{t['entry_price']}→{t['price']}  {t['pnl']:+,.2f} ({t['pnl_pct']:+.2f}%)")
            print(f"        原因: {t['reason']}")
            _print_decision_basis(t)

    if weeks:
        print(f"\n  周度已实现盈亏:")
        for wk in sorted(weeks):
            ts = weeks[wk]
            w_pnl = sum(t.get("pnl") or 0 for t in ts)
            w_wins = sum(1 for t in ts if (t.get("pnl") or 0) > 0)
            print(f"    {wk}: {w_pnl:+,.2f}  ({len(ts)} 笔，胜 {w_wins})")

    # 写报告
    lines = []
    lines.append(f"# 模拟盘周复盘 {_today()}\n")
    lines.append(f"- 起始：{start_date}　初始本金：{portfolio['initial_capital']:,.0f}")
    lines.append(f"- 当前权益：{equity:,.2f}　累计盈亏：{total_pnl:+,.2f} ({total_pnl_pct:+.2f}%)")
    lines.append(f"- 已实现盈亏：{realized:+,.2f}　已平仓：{len(closed)} 笔"
                 f"（盈 {len(wins)} / 亏 {len(losses)}）")
    if closed:
        avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(t['pnl'] for t in losses) / len(losses)) if losses else 0
        lines.append(f"- 胜率：{len(wins) / len(closed) * 100:.1f}%　"
                     f"盈亏比：{avg_win / avg_loss:.2f}" if avg_loss else
                     f"- 胜率：{len(wins) / len(closed) * 100:.1f}%")
    lines.append(f"- 最大回撤：{max_dd:.2f}%　沪深300同期：{bench:+.2f}%\n" if bench is not None
                 else f"- 最大回撤：{max_dd:.2f}%\n")
    if closed:
        lines.append("## 逐笔平仓记录\n")
        lines.append("| 日期 | 股票 | 入场→出场 | 盈亏 | 原因 |")
        lines.append("|------|------|-----------|------|------|")
        for t in closed:
            lines.append(f"| {t['date']} | {t['name']}({t['code']}) | "
                         f"{t['entry_price']}→{t['price']} | "
                         f"{t['pnl']:+,.2f} ({t['pnl_pct']:+.2f}%) | {t['reason']} |")
        lines.append("\n## 平仓决策依据\n")
        for t in closed:
            d = t.get("decision") or {}
            ec = d.get("entry_context") or {}
            es = d.get("exit_signal") or {}
            lines.append(f"### {t['date']} {t['name']}({t['code']})")
            lines.append(f"- 入场依据：{ec.get('entry_reason', '-')}")
            if es:
                notes = "；".join(es.get("key_notes") or []) or "无"
                lines.append(f"- 出场信号：{es.get('action_cn', '-')} "
                             f"{es.get('trading_score', '-')}分（{notes}）")
            lines.append("")
    if portfolio["positions"]:
        lines.append("\n## 当前持仓\n")
        lines.append("| 股票 | 股数 | 成本 | 现价 | 浮盈 |")
        lines.append("|------|------|------|------|------|")
        for code, p in portfolio["positions"].items():
            up = (p.get("price", 0) / p["entry_price"] - 1) * 100 if p["entry_price"] else 0
            lines.append(f"| {p['name']}({code}) | {p['shares']} | {p['entry_price']} | "
                         f"{p.get('price')} | {up:+.2f}% |")
    report = OUT_PAPER_DIR / f"review-{_today()}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  报告: {report}")


# ===================================================================
# 状态 / 重置
# ===================================================================
def cmd_status():
    portfolio = load_portfolio()
    equity_hist = load_equity()
    print(f"起始 {portfolio.get('start_date', '-')}  本金 {portfolio['initial_capital']:,.0f}")
    print(f"现金 {portfolio['cash']:,.2f}")
    market_value = sum(p["shares"] * p.get("price", p["entry_price"])
                       for p in portfolio["positions"].values())
    equity = portfolio["cash"] + market_value
    pnl = equity - portfolio["initial_capital"]
    print(f"持仓 {len(portfolio['positions'])} 只  市值 {market_value:,.2f}")
    print(f"权益 {equity:,.2f}  盈亏 {pnl:+,.2f} ({pnl / portfolio['initial_capital'] * 100:+.2f}%)")
    for code, p in portfolio["positions"].items():
        up = (p.get("price", 0) / p["entry_price"] - 1) * 100 if p["entry_price"] else 0
        print(f"  · {p['name']}({code})  {p['shares']}股  "
              f"成本{p['entry_price']} 现价{p.get('price')}  {up:+.2f}%  "
              f"止损{p.get('stop_loss')} 止盈{p.get('take_profit')}")
    print(f"已平仓 {len([t for t in load_trade_log() if t.get('action') == 'SELL'])} 笔"
          f"  净值记录 {len(equity_hist)} 天")


def cmd_reset(confirmed: bool):
    if not confirmed:
        print("重置将清空持仓/交易/净值历史。确认请加 --yes")
        return
    for f in (PORTFOLIO_FILE, TRADE_LOG_FILE, EQUITY_FILE):
        if f.exists():
            f.unlink()
    save_portfolio(_default_portfolio())
    print("已重置为 10 万本金，历史清空")


# ===================================================================
def main():
    parser = argparse.ArgumentParser(description="纸面交易模拟引擎")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("weekly", help="每周全池选股（离线）").add_argument(
        "--top", type=int, default=10)
    d = sub.add_parser("daily", help="每日盘后模拟交易")
    d.add_argument("--date", type=str, default=None,
                   help="补跑指定交易日（YYYY-MM-DD），用最新可得数据重跑该日")
    sub.add_parser("review", help="每周复盘")
    sub.add_parser("status", help="查看账户")
    r = sub.add_parser("reset", help="重置")
    r.add_argument("--yes", action="store_true", help="确认清空")

    args = parser.parse_args()
    if args.cmd == "weekly":
        cmd_weekly(args.top)
    elif args.cmd == "daily":
        cmd_daily(args.date)
    elif args.cmd == "review":
        cmd_review()
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "reset":
        cmd_reset(args.yes)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
