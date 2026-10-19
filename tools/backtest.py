"""
周度回测引擎：对照组 vs 场景路由组。

三组对比（见 output/strategy/design-regime-framework.md §8.1）:
  A_固定权重   现有固定权重（价值50+成长20+技术30）—— 现状基线
  B_纯动量     MomentumModel（一直追动量）          —— 不路由的后果
  实验组_路由   RegimeRouter（场景→三模型切换）     —— 待验证框架

数据（离线，来自 tools/backtest_data.py / backtest_financials_pit.py）:
  data/backtest/kline/{code}.csv       个股前复权日线（date/open/high/low/close/volume/...）
  data/backtest/index_sh.csv           上证指数日线（regime 判定用）
  data/backtest/index_hs300.csv        沪深300日线（基准）
  data/backtest/universe.csv           流动股票池（近60日均额>阈值，候选池）
  data/backtest/financials_pit.csv     多报告期财务（epsTTM/roeAvg/YOYNI + 披露日 pubDate）

关键约束:
  - 场景判定与选股只用"当周及之前"数据（PIT），无未来函数。
  - 个股技术分用价格/成交量精确 PIT，且按模型口径区分（momentum/defensive/mean_reversion）。
  - 价值/成长用 PIT 财务：每周取 pubDate <= T 的最新财报（epsTTM→PE、roeAvg→ROE、YOYNI→利润增速）。
  - 周度调仓：每周最后交易日收盘选股，持有至次周最后交易日收盘。
  - 每期 Top5 等权。交易成本: 佣金万2.5(双边)+印花税千1(卖)+滑点0.1%(双边)。

用法:
    PYTHONPATH=. PYTHONIOENCODING=utf-8 py tools/backtest.py
"""

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

# baostock 兼容 pandas 2.x（防止间接 import fetcher 时踩坑）
if not hasattr(pd.DataFrame, "append"):
    pd.DataFrame.append = lambda self, other, ignore_index=False, **kwargs: pd.concat(
        [self, other], ignore_index=ignore_index
    )

from config import DATA_DIR, OUTPUT_DIR
from src.research.selection_models import get_model

BACKTEST_DIR = DATA_DIR / "backtest"
KLINE_DIR = BACKTEST_DIR / "kline"
OUT_BACKTEST_DIR = OUTPUT_DIR / "backtest"

# --- 回测参数（对应设计文档 §8.2） ---
TOP_N = 5
COMMISSION = 0.00025          # 佣金万2.5（单边）
STAMP = 0.001                 # 印花税千1（卖出）
SLIPPAGE = 0.001              # 滑点 0.1%（单边）
COST_PER_TURNOVER = COMMISSION * 2 + STAMP + SLIPPAGE * 2  # ≈0.35%/全换手

# regime → 模型映射（与 router.py 一致）
REGIME_MODEL_MAP = {"BULL": "momentum", "BEAR": "defensive", "SIDEWAYS": "mean_reversion"}

# regime → 目标仓位（设计文档 §3.6，Step 3 仓位联动；现金部分记 0 收益）
POSITION_BY_REGIME = {"BULL": 0.8, "BEAR": 0.2, "SIDEWAYS": 0.5}

# regime 判定阈值（与 config.py 一致）
RET20_BEAR = -3.0
RET20_BULL = 3.0


# ===================================================================
# PIT 场景判定（镜像 src/research/regime.py 的确认层，读本地数组）
# ===================================================================
def detect_regime_at(idx_dates: np.ndarray, idx_close: np.ndarray, T: np.datetime64) -> str | None:
    """在日期 T 用截至 T 的指数收盘价判定场景（BULL/BEAR/SIDEWAYS）。"""
    i = int(np.searchsorted(idx_dates, T, side="right"))
    if i < 61:
        return None
    close = idx_close[:i]
    ma20 = pd.Series(close).rolling(20).mean()
    ma60 = pd.Series(close).rolling(60).mean()
    ret20 = (close[-1] / close[-21] - 1) * 100
    ma20_now, ma60_now = float(ma20.iloc[-1]), float(ma60.iloc[-1])
    ma20_prev = float(ma20.iloc[-2])
    if ma20_now > ma60_now and ma20_now > ma20_prev:
        arrange = "多头"
    elif ma20_now < ma60_now and ma20_now < ma20_prev:
        arrange = "空头"
    else:
        arrange = "缠绕"
    if arrange == "空头" and ret20 < RET20_BEAR:
        return "BEAR"
    if arrange == "多头" and ret20 > RET20_BULL:
        return "BULL"
    return "SIDEWAYS"


# ===================================================================
# 三维子评分（镜像 stock_selection.py 的 _score_*，读本地数组 PIT）
# ===================================================================
def _liquidity(volume: np.ndarray) -> float:
    """流动性分(0~5)，基于近20日均量。"""
    avg_vol = volume[-20:].mean()
    if avg_vol > 1e7:
        return 5.0
    if avg_vol > 5e6:
        return 3.0
    if avg_vol > 1e6:
        return 1.0
    return 0.0


def _score_technical_momentum(close: np.ndarray, volume: np.ndarray, price: float) -> float:
    """追强势：20/60日动量 + 均线多头 + 放量（原技术分口径）。"""
    score = 12 * min(max((close[-1] / close[-20] - 1) * 100 / 10 + 0.5, 0), 1.0)
    if len(close) >= 60:
        score += 8 * min(max((close[-1] / close[-60] - 1) * 100 / 20 + 0.5, 0), 1.0)
        ma20 = close[-20:].mean()
        ma60 = close[-60:].mean()
        if price > ma20 > ma60:
            score += 5
        elif price > ma20:
            score += 2
    return score + _liquidity(volume)


def _score_technical_defensive(close: np.ndarray, volume: np.ndarray, price: float) -> float:
    """相对抗跌：低回撤 + 低波动 + 不破位 + 流动性（不追动量）。"""
    score = 0.0
    if len(close) >= 60:
        # 回撤稳定性(0~12)：60日最大回撤越小越稳
        running_max = np.maximum.accumulate(close)
        mdd = float(np.min(close / running_max - 1))  # <=0
        score += 12 * (1 - min(max(-mdd / 0.30, 0), 1.0))
        # 波动稳定性(0~8)：20日年化波动率越低越稳
        rets = np.diff(close[-21:]) / close[-21:-1]
        vol = float(np.std(rets, ddof=1)) * np.sqrt(252)
        score += 8 * (1 - min(max(vol / 0.60, 0), 1.0))
        # 不破位(0~5)：站上 MA60 记满，仅站上 MA20 记 3
        ma20 = close[-20:].mean()
        ma60 = close[-60:].mean()
        if price > ma60:
            score += 5
        elif price > ma20:
            score += 3
    else:
        score += 10.0  # 数据不足给基准分
    return score + _liquidity(volume)


def _score_technical_mean_reversion(close: np.ndarray, volume: np.ndarray, price: float) -> float:
    """超跌低吸：20/60日跌幅越深越高分 + 回踩支撑，不追高。"""
    score = 0.0
    if len(close) >= 20:
        ret20 = (close[-1] / close[-20] - 1) * 100
        score += 12 * min(max(-ret20 / 15 + 0.5, 0), 1.0)
    if len(close) >= 60:
        ret60 = (close[-1] / close[-60] - 1) * 100
        score += 8 * min(max(-ret60 / 30 + 0.5, 0), 1.0)
        ma20 = close[-20:].mean()
        ma60 = close[-60:].mean()
        if price < ma20 and price > ma60:
            score += 5  # 回踩支撑
        elif price < ma60:
            score += 2  # 超跌
    else:
        score += 5.0
    return score + _liquidity(volume)


def _score_technical(close: np.ndarray, volume: np.ndarray, style: str = "momentum") -> tuple[float, float]:
    """技术分(0~30) + 最新价，按模型口径(technical_style)区分。close/volume 为截至 T 的序列。"""
    n = len(close)
    if n < 20:
        return 10.0, float(close[-1])
    price = float(close[-1])
    if style == "defensive":
        score = _score_technical_defensive(close, volume, price)
    elif style == "mean_reversion":
        score = _score_technical_mean_reversion(close, volume, price)
    else:
        score = _score_technical_momentum(close, volume, price)
    return min(score, 30), price


def _score_value(fin: dict | None, price: float) -> float:
    """价值分(0~50)。镜像 stock_selection._score_value，PB/总资产在财务快照中缺省→0。"""
    if fin is None:
        return 15.0
    score = 0.0
    eps = fin.get("eps_ttm")
    pe = price / eps if (eps and eps > 0 and price) else None
    if pe and 0 < pe < 200:
        if pe < 15:
            score += 15
        elif pe < 25:
            score += 10
        elif pe < 40:
            score += 5
        elif pe < 60:
            score += 2
    else:
        score += 5
    roe = fin.get("roe")
    if roe is not None:
        if roe >= 20:
            score += 15
        elif roe >= 15:
            score += 12
        elif roe >= 10:
            score += 9
        elif roe >= 5:
            score += 5
        elif roe > 0:
            score += 2
    else:
        score += 5
    return min(score, 50)


def _score_growth(fin: dict | None) -> float:
    """成长分(0~20)。镜像 stock_selection._score_growth。"""
    if fin is None:
        return 5.0
    score = 0.0
    rev_g = fin.get("revenue_growth")
    if rev_g is not None:
        if rev_g >= 30:
            score += 10
        elif rev_g >= 20:
            score += 8
        elif rev_g >= 10:
            score += 6
        elif rev_g >= 0:
            score += 3
    profit_g = fin.get("profit_growth")
    if profit_g is not None:
        if profit_g >= 30:
            score += 10
        elif profit_g >= 15:
            score += 7
        elif profit_g >= 0:
            score += 4
    return min(score, 20)


def _combine(value: float, growth: float, technical: float, weights: dict) -> float:
    """按权重合并三维分（镜像 selection_models 的归一化加权）。"""
    w_v = weights.get("value", 50)
    w_g = weights.get("growth", 20)
    w_t = weights.get("technical", 30)
    return (value / 50 * w_v) + (growth / 20 * w_g) + (technical / 30 * w_t)


# ===================================================================
# 数据加载
# ===================================================================
def load_stocks() -> dict[str, dict]:
    """加载流动股票池(universe.csv)的个股日线 + PIT 财务到内存。返回 {code: {dates,close,volume,fin}}。"""
    universe = load_universe()
    pit_map = load_financials_pit()
    stocks = {}
    for code in universe:
        f = KLINE_DIR / f"{code}.csv"
        if not f.exists():
            continue
        try:
            df = pd.read_csv(f, usecols=["date", "close", "volume"])
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
            dates = df["date"].to_numpy(dtype="datetime64[ns]")
            close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype="float64")
            volume = pd.to_numeric(df["volume"], errors="coerce").to_numpy(dtype="float64")
        except Exception:
            continue
        if len(dates) < 20:
            continue
        stocks[code] = {
            "dates": dates,
            "close": close,
            "volume": volume,
            "fin": pit_map.get(code),
        }
    return stocks


def load_universe() -> list[str]:
    """加载流动股票池（近60日均额>阈值，由 backtest_financials_pit.py 生成）；无则回退全部 kline。"""
    f = BACKTEST_DIR / "universe.csv"
    if not f.exists():
        return sorted(p.stem for p in KLINE_DIR.glob("*.csv"))
    df = pd.read_csv(f, dtype={"code": str})
    return [str(c).zfill(6) for c in df["code"]]


def load_financials_pit() -> dict[str, list[dict]]:
    """加载 PIT 财务 → {code: [按 pubDate 升序的报告行 {pubDate,eps_ttm,roe,profit_growth}]}。

    roeAvg / YOYNI 为 baostock 小数，换算为百分比以匹配 _score_value/_score_growth 阈值。
    """
    f = BACKTEST_DIR / "financials_pit.csv"
    if not f.exists():
        return {}
    df = pd.read_csv(f, dtype={"code": str})
    df["pubDate"] = pd.to_datetime(df["pubDate"], errors="coerce")
    pit = {}
    for code, grp in df.groupby("code"):
        rows = []
        for _, r in grp.iterrows():
            pub = r["pubDate"]
            if pd.isna(pub):
                continue
            rows.append({
                "pubDate": pub.to_datetime64(),
                "eps_ttm": _nan_to_none(r.get("epsTTM")),
                "roe": _nan_to_pct(r.get("roeAvg")),
                "profit_growth": _nan_to_pct(r.get("YOYNI")),
            })
        rows.sort(key=lambda x: x["pubDate"])
        pit[str(code)] = rows
    return pit


def financials_at(pit_rows: list[dict] | None, T: np.datetime64) -> dict | None:
    """取截至 T 已披露的最新财报（pubDate <= T），返回 _score_value/_score_growth 期望的 fin dict。"""
    if not pit_rows:
        return None
    latest = None
    for r in pit_rows:
        if r["pubDate"] <= T:
            latest = r
        else:
            break
    if latest is None:
        return None
    return {
        "eps_ttm": latest["eps_ttm"],
        "roe": latest["roe"],
        "revenue_growth": None,  # PIT 数据未采集营收增速，成长分仅由利润增速贡献
        "profit_growth": latest["profit_growth"],
    }


def _nan_to_none(v):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _nan_to_pct(v):
    """baostock 小数 → 百分比；缺失/NaN 返回 None。"""
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v) * 100
    except (TypeError, ValueError):
        return None


def load_index(name: str) -> pd.DataFrame:
    f = BACKTEST_DIR / f"index_{name}.csv"
    df = pd.read_csv(f, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    return df


def weekly_rebalance_dates(idx: pd.DataFrame) -> list[pd.Timestamp]:
    """取每周最后一个交易日作为调仓日（收盘选股，次周持有）。"""
    iso = idx["date"].dt.isocalendar()
    wk = idx.assign(year=iso.year.astype(int), week=iso.week.astype(int))
    return wk.groupby(["year", "week"])["date"].max().sort_values().tolist()


# ===================================================================
# 回测模拟
# ===================================================================
def stock_close_at(data: dict, T: np.datetime64) -> float | None:
    i = int(np.searchsorted(data["dates"], T, side="right"))
    if i == 0:
        return None
    return float(data["close"][i - 1])


def score_stock_at(data: dict, T: np.datetime64, model) -> float | None:
    """在日期 T 对单只股票打分（PIT）。数据不足返回 None。"""
    i = int(np.searchsorted(data["dates"], T, side="right"))
    if i < 20:
        return None
    tech, price = _score_technical(data["close"][:i], data["volume"][:i], model.technical_style)
    fin = financials_at(data["fin"], T)
    value = _score_value(fin, price)
    growth = _score_growth(fin)
    return _combine(value, growth, tech, model.stock_weights)


def simulate_group(
    dates: list[pd.Timestamp],
    stocks: dict[str, dict],
    model_selector,
    idx_dates: np.ndarray,
    idx_close: np.ndarray,
    apply_position: bool = False,
) -> dict:
    """模拟一组策略，返回 {equity, weekly, holdings, regimes, positions}。

    apply_position=True 时按场景目标仓位（BULL 80%/BEAR 20%/SIDEWAYS 50%）缩放周收益，
    现金部分记 0 收益；用于验证 Step 3 仓位联动是否提升风险调整后绩效。
    """
    equity = [1.0]
    weekly = []
    holdings_all = []
    regimes_all = []
    positions_all = []
    prev_holdings: set[str] | None = None

    for i in range(len(dates) - 1):
        T = dates[i]
        T_next = dates[i + 1]
        T64 = np.datetime64(T, "ns")
        Tn64 = np.datetime64(T_next, "ns")

        # 1) 确定模型 + 场景
        if model_selector == "router" or apply_position:
            regime = detect_regime_at(idx_dates, idx_close, T64)
            if regime is None:
                regime = "SIDEWAYS"  # 早期数据不足时按震荡处理
            regimes_all.append(regime)
        else:
            regime = None
            regimes_all.append(None)

        if model_selector == "router":
            model = get_model(REGIME_MODEL_MAP[regime])
        else:
            model = model_selector

        position = POSITION_BY_REGIME.get(regime, 1.0) if apply_position else 1.0
        positions_all.append(round(position, 3))

        # 2) 全池打分，取 Top5
        scored = []
        for code, data in stocks.items():
            s = score_stock_at(data, T64, model)
            if s is not None:
                scored.append((code, s))
        scored.sort(key=lambda x: -x[1])
        top5 = [c for c, _ in scored[:TOP_N]]
        if len(top5) < TOP_N:
            # 极端情况下候选不足，按已有持仓等权处理
            top5 = top5[:] or (list(prev_holdings or []))
        holdings_all.append(top5)

        # 3) 周收益（T 收盘 → T_next 收盘）
        rets = []
        for code in top5:
            c0 = stock_close_at(stocks[code], T64)
            c1 = stock_close_at(stocks[code], Tn64)
            if c0 and c1:
                rets.append(c1 / c0 - 1)
            else:
                rets.append(0.0)
        gross = float(np.mean(rets)) if rets else 0.0

        # 4) 换手成本
        if prev_holdings is None:
            turnover = 1.0
        else:
            turnover = len(set(top5) - prev_holdings) / TOP_N
        net_full = gross - turnover * COST_PER_TURNOVER
        net = position * net_full  # 现金部分 0 收益

        equity.append(equity[-1] * (1 + net))
        weekly.append(net)
        prev_holdings = set(top5)

    return {
        "equity": [round(e, 6) for e in equity],
        "weekly": [round(w, 6) for w in weekly],
        "holdings": holdings_all,
        "regimes": regimes_all,
        "positions": positions_all,
    }


# ===================================================================
# 主流程
# ===================================================================
def main():
    t0 = time.time()
    print("加载指数与个股数据...")
    idx_sh = load_index("sh")
    idx_hs300 = load_index("hs300")

    stocks = load_stocks()
    print(f"个股: {len(stocks)} 只  |  耗时 {time.time()-t0:.1f}s")

    dates = weekly_rebalance_dates(idx_sh)
    print(f"调仓周: {len(dates)} 周  ({dates[0].date()} ~ {dates[-1].date()})")

    idx_dates = idx_sh["date"].to_numpy(dtype="datetime64[ns]")
    idx_close = pd.to_numeric(idx_sh["close"], errors="coerce").to_numpy(dtype="float64")

    groups = {
        "A_固定权重": (get_model(None), False),        # 基础固定权重 {50,20,30}
        "B_纯动量": (get_model("momentum"), False),     # 一直追动量
        "实验组_路由": ("router", False),               # 场景→三模型切换（满仓）
        "实验组_路由_仓位": ("router", True),           # 场景路由 + Step3 仓位联动
    }

    results = {"config": {
        "top_n": TOP_N,
        "cost_per_turnover": round(COST_PER_TURNOVER, 5),
        "start": str(dates[0].date()),
        "end": str(dates[-1].date()),
    }, "groups": {}}

    for name, (selector, apply_position) in groups.items():
        print(f"\n回测: {name} ...")
        g = simulate_group(dates, stocks, selector, idx_dates, idx_close,
                           apply_position=apply_position)
        results["groups"][name] = g
        total_ret = g["equity"][-1] - 1
        print(f"  {name}: 累计收益 {total_ret:+.2%}  净值 {g['equity'][-1]:.4f}")

    # 基准（沪深300，同一调仓日历）
    hs_dates = idx_hs300["date"].to_numpy(dtype="datetime64[ns]")
    hs_close = pd.to_numeric(idx_hs300["close"], errors="coerce").to_numpy(dtype="float64")
    bench = [1.0]
    for i in range(len(dates) - 1):
        c0 = stock_close_at({"dates": hs_dates, "close": hs_close, "volume": hs_close}, np.datetime64(dates[i], "ns"))
        c1 = stock_close_at({"dates": hs_dates, "close": hs_close, "volume": hs_close}, np.datetime64(dates[i + 1], "ns"))
        r = c1 / c0 - 1 if (c0 and c1) else 0.0
        bench.append(bench[-1] * (1 + r))
    results["benchmark"] = {"equity": [round(e, 6) for e in bench]}
    print(f"\n基准(沪深300): 累计收益 {bench[-1]-1:+.2%}")

    OUT_BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_BACKTEST_DIR / "results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n回测结果: {out}  (总耗时 {time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
