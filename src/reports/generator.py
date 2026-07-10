"""
报告生成器。将研究/交易结果输出为 Markdown 和 JSON 格式。
"""

import json
from datetime import datetime
from pathlib import Path

from config import WEEKLY_DIR, DAILY_DIR, STOCK_POOL_FILE


def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def generate_weekly_report(
    industries: list[dict],
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    """
    生成周度研究报告。
    返回 (md_path, json_path)
    """
    if output_dir is None:
        output_dir = WEEKLY_DIR
    _ensure_dir(output_dir)

    today = datetime.now().strftime("%Y%m%d")
    week_num = datetime.now().isocalendar()[1]
    base_name = f"{today}-W{week_num:02d}"

    # --- JSON 输出: 完整股票池 ---
    pool_data = {
        "report_date": datetime.now().strftime("%Y-%m-%d"),
        "report_type": "weekly",
        "version": "1.0.0",
        "industries": industries,
        "summary": {
            "total_industries_selected": len(industries),
            "total_stocks_recommended": sum(len(ind.get("stocks", [])) for ind in industries),
            "methodology_version": "1.0.0",
        },
    }

    json_path = output_dir / f"{base_name}-pool.json"
    json_path.write_text(json.dumps(pool_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同时更新 stock_pool.json 作为交易员读取的当前股票池
    _ensure_dir(STOCK_POOL_FILE.parent)
    STOCK_POOL_FILE.write_text(json.dumps(pool_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Markdown 报告 ---
    md = _build_weekly_md(pool_data)
    md_path = output_dir / f"{base_name}-report.md"
    md_path.write_text(md, encoding="utf-8")

    print(f"[Report] 周报已生成: {md_path}")
    print(f"[Report] 股票池已保存: {json_path}")
    print(f"[Report] 当前股票池: {STOCK_POOL_FILE}")

    return md_path, json_path


def _build_weekly_md(data: dict) -> str:
    lines = []
    lines.append(f"# 周度投资研究报告")
    lines.append(f"**日期**: {data['report_date']}  |  **方法论版本**: {data['summary']['methodology_version']}")
    lines.append(f"**入选行业**: {data['summary']['total_industries_selected']}  |  **推荐个股**: {data['summary']['total_stocks_recommended']} 只")
    lines.append("")
    lines.append("---")
    lines.append("")

    for ind in data["industries"]:
        lines.append(f"## #{ind['rank']} {ind.get('industry_display', ind['industry_name'])}")
        lines.append(f"**综合得分**: {ind['composite_score']} / 100")
        lines.append("")
        lines.append(f"| 维度 | 得分 |")
        lines.append(f"|------|------|")
        lines.append(f"| 动量 | {ind['momentum_score']} / 50 |")
        lines.append(f"| 基本面 | {ind['fundamental_score']} / 50 |")
        if ind.get("policy_catalyst_score"):
            lines.append(f"| 政策催化 | {ind.get('policy_catalyst_score', 0)} / 10 |")
        if ind.get("policy_catalyst_notes"):
            lines.append(f"\n**政策催化评估**: {ind['policy_catalyst_notes']}")
        lines.append("")

        stocks = ind.get("stocks", [])
        if stocks:
            lines.append("### 推荐个股")
            lines.append("")
            lines.append("| 排名 | 代码 | 名称 | 综合得分 | 价值(50) | 成长(20) | 技术(30) | PE | ROE | 最新价 |")
            lines.append("|------|------|------|----------|----------|----------|----------|----|-----|--------|")
            for s in stocks:
                m = s.get("metrics", {})
                lines.append(
                    f"| {s.get('rank', '-')} | {s['code']} | {s['name']} | "
                    f"{s['composite_score']} | {s.get('value_score', '-')} | "
                    f"{s.get('growth_score', '-')} | {s.get('technical_score', '-')} | "
                    f"{m.get('pe_dynamic', '-')} | {m.get('roe', '-')} | "
                    f"{m.get('price_latest', '-')} |"
                )
            lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("> 免责声明: 本报告由量化模型自动生成，不构成投资建议。投资有风险，入市须谨慎。")
    return "\n".join(lines)


def generate_daily_report(
    suggestions: list[dict],
    market_snapshot: dict | None = None,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    """
    生成每日交易建议报告。
    返回 (md_path, json_path)
    """
    if output_dir is None:
        output_dir = DAILY_DIR
    _ensure_dir(output_dir)

    today = datetime.now().strftime("%Y%m%d")
    base_name = today

    # --- JSON ---
    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "market_snapshot": market_snapshot or {},
        "suggestions": suggestions,
    }
    json_path = output_dir / f"{base_name}-signals.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Markdown ---
    md = _build_daily_md(data)
    md_path = output_dir / f"{base_name}-trading.md"
    md_path.write_text(md, encoding="utf-8")

    print(f"[Report] 日报已生成: {md_path}")
    return md_path, json_path


def _build_daily_md(data: dict) -> str:
    lines = []
    lines.append(f"# 每日交易建议")
    lines.append(f"**生成时间**: {data['generated_at']}")
    lines.append("")

    ms = data.get("market_snapshot", {})
    if ms:
        lines.append("## 大盘概览")
        sh = ms.get("sh_index", "-")
        sh_chg = ms.get("sh_change_pct", 0)
        sz = ms.get("sz_index", "-")
        sz_chg = ms.get("sz_change_pct", 0)
        lines.append(f"- 上证指数: {sh}  ({sh_chg:+.2f}%)" if sh_chg else f"- 上证指数: {sh}")
        lines.append(f"- 深证成指: {sz}  ({sz_chg:+.2f}%)" if sz_chg else f"- 深证成指: {sz}")
        lines.append("")

    lines.append("---")
    lines.append("")

    action_emoji = {
        "strong_buy": "[STRONG BUY]",
        "buy": "[BUY]",
        "hold": "[HOLD]",
        "reduce": "[REDUCE]",
        "strong_sell": "[STRONG SELL]",
    }

    for s in data["suggestions"]:
        action = action_emoji.get(s.get("action", ""), s.get("action", ""))
        lines.append(f"## {s['name']} ({s['code']}) — {action}  [{s['trading_score']}分]")
        lines.append("")

        entry = s.get("entry", {})
        if entry:
            lines.append(f"- **建议入场价**: {entry.get('price', '-')} 元 ({entry.get('suggestion', '')})")

        sl = s.get("stop_loss")
        if sl:
            lines.append(f"- **止损价**: {sl} 元")

        tp = s.get("take_profit", {})
        if tp:
            lines.append(f"- **止盈1**: {tp.get('tp1', '-')} 元 ({tp.get('tp1_pct', '-')}%) → {tp.get('tp1_action', '')}")
            lines.append(f"- **止盈2**: {tp.get('tp2', '-')} 元 ({tp.get('tp2_pct', '-')}%) → {tp.get('tp2_action', '')}")

        risk = s.get("risk_assessment")
        if risk:
            lines.append(f"- **风险评估**: {risk}")

        lines.append("")
        notes = s.get("key_notes", [])
        if notes:
            lines.append("**关键信号**:")
            for n in notes:
                lines.append(f"  - {n}")
            lines.append("")

        # 技术指标表
        ind = s.get("indicators", {})
        if ind:
            lines.append("### 技术指标")
            lines.append("| 指标 | 数值 |")
            lines.append("|------|------|")
            for k in ["ma5", "ma10", "ma20", "ma60"]:
                if k in ind:
                    lines.append(f"| {k.upper()} | {ind[k]} |")
            for k in ["macd", "signal", "histogram"]:
                if k in ind:
                    lines.append(f"| MACD_{k} | {ind[k]} |")
            for k in ["rsi6", "rsi14"]:
                if k in ind:
                    lines.append(f"| {k.upper()} | {ind[k]} |")
            for k in ["bb_upper", "bb_middle", "bb_lower", "atr14", "volume_ratio"]:
                if k in ind:
                    lines.append(f"| {k} | {ind[k]} |")
            lines.append("")

        lines.append("---")
        lines.append("")

    lines.append("> 免责声明: 本报告由量化模型自动生成，不构成投资建议。投资有风险，入市须谨慎。")
    return "\n".join(lines)
