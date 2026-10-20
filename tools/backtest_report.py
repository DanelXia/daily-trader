"""
回测绩效打分与报告生成器。

读取 tools/backtest.py 输出的 output/backtest/results.json，按设计文档 §8.4/8.5
计算六维指标并打分，输出对比报告到 output/backtest/report.md。

打分规则（§8.5，分档）:
  年化收益(25): <0→0, 0-10%→8, 10-20%→15, 20-30%→20, >30%→25
  最大回撤(20): >30%→0, 20-30%→8, 10-20%→14, <10%→20
  夏普(15):     <0→0, 0-0.5→5, 0.5-1→9, 1-1.5→12, >1.5→15
  胜率(10):     10 × 盈利周占比
  盈亏比(10):   <1→0, 1-1.5→4, 1.5-2→7, >2→10
  Alpha(20):    <0→0, 0-5%→10, 5-10%→15, >10%→20

用法:
    PYTHONPATH=. PYTHONIOENCODING=utf-8 py tools/backtest_report.py
"""

import json
import math
from pathlib import Path

import numpy as np

from config import OUTPUT_DIR

OUT_BACKTEST_DIR = OUTPUT_DIR / "backtest"
RESULTS_FILE = OUT_BACKTEST_DIR / "results.json"
REPORT_FILE = OUT_BACKTEST_DIR / "report.md"

GROUP_LABELS = {
    "A_固定权重": "对照组A（固定权重）",
    "B_纯动量": "对照组B（纯动量）",
    "实验组_路由": "实验组（场景路由）",
    "实验组_路由_仓位": "实验组（路由+仓位）",
}


# ===================================================================
# 指标计算
# ===================================================================
def annualized_return(equity: list[float], n_weeks: int) -> float:
    total = equity[-1] - 1
    if total <= -1:
        return -1.0
    years = n_weeks / 52
    if years <= 0:
        return 0.0
    return (1 + total) ** (1 / years) - 1


def max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    mdd = 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    return -mdd


def sharpe(weekly: list[float]) -> float:
    w = np.array(weekly)
    std = w.std(ddof=1)
    if std == 0:
        return 0.0
    return float(w.mean() / std * math.sqrt(52))


def win_rate(weekly: list[float]) -> float:
    return float(sum(1 for x in weekly if x > 0) / len(weekly))


def profit_loss_ratio(weekly: list[float]) -> float:
    wins = [x for x in weekly if x > 0]
    losses = [x for x in weekly if x < 0]
    if not losses:
        return float("inf") if wins else 0.0
    if not wins:
        return 0.0
    return float(np.mean(wins) / abs(np.mean(losses)))


def compute_metrics(equity: list[float], weekly: list[float]) -> dict:
    n = len(weekly)
    ann = annualized_return(equity, n)
    mdd = max_drawdown(equity)
    sp = sharpe(weekly)
    wr = win_rate(weekly)
    plr = profit_loss_ratio(weekly)
    return {
        "total_return": equity[-1] - 1,
        "annualized": ann,
        "max_drawdown": mdd,
        "sharpe": sp,
        "win_rate": wr,
        "profit_loss_ratio": plr,
    }


# ===================================================================
# 打分
# ===================================================================
def score_annualized(v: float) -> float:
    if v < 0:
        return 0
    if v < 0.10:
        return 8
    if v < 0.20:
        return 15
    if v < 0.30:
        return 20
    return 25


def score_drawdown(v: float) -> float:
    if v > 0.30:
        return 0
    if v > 0.20:
        return 8
    if v > 0.10:
        return 14
    return 20


def score_sharpe(v: float) -> float:
    if v < 0:
        return 0
    if v < 0.5:
        return 5
    if v < 1:
        return 9
    if v < 1.5:
        return 12
    return 15


def score_win_rate(v: float) -> float:
    return round(10 * v, 1)


def score_plr(v: float) -> float:
    if math.isinf(v):
        return 10
    if v < 1:
        return 0
    if v < 1.5:
        return 4
    if v < 2:
        return 7
    return 10


def score_alpha(v: float) -> float:
    if v < 0:
        return 0
    if v < 0.05:
        return 10
    if v < 0.10:
        return 15
    return 20


def total_score(m: dict, alpha: float) -> dict:
    parts = {
        "年化收益": (score_annualized(m["annualized"]), 25),
        "Alpha": (score_alpha(alpha), 20),
        "最大回撤": (score_drawdown(m["max_drawdown"]), 20),
        "夏普": (score_sharpe(m["sharpe"]), 15),
        "胜率": (score_win_rate(m["win_rate"]), 10),
        "盈亏比": (score_plr(m["profit_loss_ratio"]), 10),
    }
    total = sum(v for v, _ in parts.values())
    return {"parts": parts, "total": round(total, 1)}


# ===================================================================
# 报告渲染
# ===================================================================
def _pct(v: float) -> str:
    return f"{v * 100:+.2f}%"


def _plr_str(v: float) -> str:
    return "∞" if math.isinf(v) else f"{v:.2f}"


def render(results: dict) -> str:
    cfg = results.get("config", {})
    benchmark = results["benchmark"]["equity"]
    groups = results["groups"]
    bench_n = len(benchmark) - 1

    bench_metrics = compute_metrics(benchmark, [0.0] * bench_n)  # 基准周收益由净值反推
    bench_weekly = [benchmark[i + 1] / benchmark[i] - 1 for i in range(bench_n)]
    bench_metrics = compute_metrics(benchmark, bench_weekly)

    # 各组指标 + 打分
    rows = {}
    for name, g in groups.items():
        m = compute_metrics(g["equity"], g["weekly"])
        alpha = m["annualized"] - bench_metrics["annualized"]
        sc = total_score(m, alpha)
        rows[name] = {"metrics": m, "alpha": alpha, "score": sc}

    lines = []
    lines.append("# 回测绩效报告：对照组 vs 场景路由组")
    lines.append("")
    lines.append(f"- 区间: {cfg.get('start', '-')} ~ {cfg.get('end', '-')}（周度调仓，Top{cfg.get('top_n', 5)} 等权）")
    lines.append(f"- 交易成本/全换手: {cfg.get('cost_per_turnover', 0.35) * 100:.2f}%  |  基准: 沪深300")
    lines.append("")
    lines.append("## 一、综合绩效对比")
    lines.append("")
    lines.append("| 组别 | 累计收益 | 年化 | 最大回撤 | 夏普 | 胜率 | 盈亏比 | Alpha | 综合分 |")
    lines.append("|------|----------|------|----------|------|------|--------|-------|--------|")
    lines.append(
        f"| 基准(沪深300) | {_pct(bench_metrics['total_return'])} | {_pct(bench_metrics['annualized'])} | "
        f"{bench_metrics['max_drawdown']*100:.1f}% | {bench_metrics['sharpe']:.2f} | "
        f"{bench_metrics['win_rate']*100:.1f}% | {_plr_str(bench_metrics['profit_loss_ratio'])} | — | — |"
    )
    for name, r in rows.items():
        m = r["metrics"]
        lines.append(
            f"| {GROUP_LABELS.get(name, name)} | {_pct(m['total_return'])} | {_pct(m['annualized'])} | "
            f"{m['max_drawdown']*100:.1f}% | {m['sharpe']:.2f} | "
            f"{m['win_rate']*100:.1f}% | {_plr_str(m['profit_loss_ratio'])} | "
            f"{_pct(r['alpha'])} | **{r['score']['total']}** |"
        )
    lines.append("")
    lines.append("## 二、分项打分")
    lines.append("")
    lines.append("| 组别 | 年化(25) | Alpha(20) | 回撤(20) | 夏普(15) | 胜率(10) | 盈亏比(10) | 合计 |")
    lines.append("|------|----------|-----------|----------|----------|----------|------------|------|")
    for name, r in rows.items():
        p = r["score"]["parts"]
        lines.append(
            f"| {GROUP_LABELS.get(name, name)} | {p['年化收益'][0]} | {p['Alpha'][0]} | "
            f"{p['最大回撤'][0]} | {p['夏普'][0]} | {p['胜率'][0]} | {p['盈亏比'][0]} | "
            f"**{r['score']['total']}** |"
        )
    lines.append("")

    # 通过标准判定（§8.6）
    a = rows.get("A_固定权重")
    exp = rows.get("实验组_路由")
    lines.append("## 三、通过标准判定（§8.6）")
    lines.append("")
    if a and exp:
        c1 = exp["score"]["total"] > a["score"]["total"]
        c2 = exp["metrics"]["max_drawdown"] <= a["metrics"]["max_drawdown"] + 1e-9
        c3 = bear_return(results, "实验组_路由") > bear_return(results, "A_固定权重")
        lines.append(f"1. 综合分 > 对照组A：`{c1}`（{exp['score']['total']} vs {a['score']['total']}）")
        lines.append(f"2. 最大回撤 ≤ 对照组A：`{c2}`（{exp['metrics']['max_drawdown']*100:.1f}% vs {a['metrics']['max_drawdown']*100:.1f}%）")
        lines.append(f"3. BEAR 区间亏损小于对照组A：`{c3}`（{bear_return(results, '实验组_路由')*100:+.1f}% vs {bear_return(results, 'A_固定权重')*100:+.1f}%）")
        passed = c1 and c2 and c3
        lines.append("")
        lines.append(f"**结论: {'✅ 框架有效' if passed else '❌ 未通过'}**（需同时满足三条）")
    lines.append("")

    # BEAR 区间表现
    lines.append("## 四、BEAR 区间表现（下行段防守）")
    lines.append("")
    lines.append("| 组别 | BEAR 周数 | BEAR 累计收益 |")
    lines.append("|------|-----------|---------------|")
    regimes = groups.get("实验组_路由", {}).get("regimes", [])
    bear_n = sum(1 for r in regimes if r == "BEAR")
    for name in groups:
        br = bear_return(results, name)
        lines.append(f"| {GROUP_LABELS.get(name, name)} | {bear_n} | {_pct(br)} |")
    lines.append("")

    # 实验组逐周 regime 分布
    lines.append("## 五、实验组场景分布")
    lines.append("")
    from collections import Counter
    cnt = Counter(r for r in regimes if r)
    total_w = sum(cnt.values())
    dist = "、".join(f"{k} {v}周({v/total_w*100:.0f}%)" for k, v in cnt.most_common())
    lines.append(f"- 场景分布: {dist}")
    lines.append("")
    lines.append("> 注：「实验组（路由+仓位）」额外叠加 Step 3 仓位联动（BULL 80%/BEAR 20%/SIDEWAYS 50%，现金记 0 收益），")
    lines.append("> 「实验组（场景路由）」仍为满仓、仅验证选股模型路由。财务数据为严格 PIT")
    lines.append("> （披露日 pubDate ≤ T），技术分按模型口径区分（追强势/抗跌/超跌）。")
    lines.append("")
    lines.append("> **已知局限**：")
    lines.append("> 1. 幸存者偏差：股票池为当前存活 + 流动性过滤（近60日均额>2亿），已退市/长期停牌标的不在回测内。")
    lines.append("> 2. PIT 财务未采集营收增速，成长分仅由利润增速（YOYNI）贡献。")
    lines.append("")
    lines.append("> 免责声明: 本报告由量化模型自动生成，不构成投资建议。")
    return "\n".join(lines)


def bear_return(results: dict, group_name: str) -> float:
    """计算某组在 BEAR 周的累计收益（用实验组的 regime 序列对齐）。"""
    g = results["groups"][group_name]
    regimes = results["groups"].get("实验组_路由", {}).get("regimes", [])
    weekly = g["weekly"]
    n = min(len(regimes), len(weekly))
    ret = 0.0
    for i in range(n):
        if regimes[i] == "BEAR":
            ret = (1 + ret) * (1 + weekly[i]) - 1
    return ret


def main():
    if not RESULTS_FILE.exists():
        print(f"[错误] 回测结果不存在: {RESULTS_FILE}")
        print("请先运行: PYTHONPATH=. py tools/backtest.py")
        return
    results = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    md = render(results)
    REPORT_FILE.write_text(md, encoding="utf-8")
    print(f"[Report] 回测报告: {REPORT_FILE}")
    print(md)


if __name__ == "__main__":
    main()
