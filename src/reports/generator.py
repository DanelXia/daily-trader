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
    intraday: dict | None = None,
    sentiment: dict | None = None,
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
        "intraday": intraday,
        "sentiment": sentiment,
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

        total_amt = ms.get("total_amount")
        vol_ratio = ms.get("market_volume_ratio")
        if total_amt and vol_ratio:
            if vol_ratio > 1.2:
                vol_level = "放量"
            elif vol_ratio < 0.8:
                vol_level = "缩量"
            else:
                vol_level = "正常"
            lines.append(f"- 两市成交额: {total_amt:.0f}亿  |  大盘量比: {vol_ratio:.2f}  |  量能: {vol_level}")
        lines.append("")

    # 大盘日内分时分析
    intraday = data.get("intraday")
    if intraday and intraday.get("bars"):
        lines.append("## 大盘日内分析")
        bars = intraday["bars"]
        sh_prev = intraday.get("sh_prev_close", "-")
        sz_prev = intraday.get("sz_prev_close", "-")
        cy_prev = intraday.get("cy_prev_close", "-")
        lines.append(f"昨日收盘: 上证 {sh_prev}  |  深证 {sz_prev}  |  创业板 {cy_prev}")
        lines.append("")

        # 分时走势表
        lines.append("### 30分钟分时走势")
        lines.append("")
        lines.append("| 时间 | 上证 | 日涨跌 | 深证 | 日涨跌 | 创业板 | 日涨跌 | 上证量(亿) |")
        lines.append("|------|------|--------|------|--------|--------|--------|------------|")
        for b in bars:
            sh = b["sh"]
            sh_p = b["sh_pct"]
            sz = b["sz"]
            sz_p = b["sz_pct"]
            cy = b["cy"]
            cy_p = b["cy_pct"]
            amt = b["sh_amt"]
            # highlight worst values
            cy_mark = f"**{cy}**" if b == bars[-1] and cy_p < -3 else str(cy)
            sh_mark = f"**{sh}**" if b == bars[-1] else str(sh)
            lines.append(
                f"| {b['time']} | {sh_mark} | {sh_p:+.2f}% | {sz} | {sz_p:+.2f}% | "
                f"{cy_mark} | {cy_p:+.2f}% | {amt:.0f} |"
            )
        lines.append("")

        # 成交量分布
        total_sh = intraday.get("total_sh_amt", 1)
        lines.append("### 上证 成交量分布")
        lines.append("")
        lines.append("| 时段 | 成交额(亿) | 占比 | 特征 |")
        lines.append("|------|-----------|------|------|")
        for b in bars:
            a = b["sh_amt"]
            pct = a / total_sh * 100 if total_sh > 0 else 0
            if pct > 30:
                label = "开盘巨量"
            elif pct > 12:
                label = "上午活跃"
            elif pct > 8:
                label = "午盘衰减"
            else:
                label = "午后枯竭"
            lines.append(f"| {b['time']} | {a:.0f} | {pct:.1f}% | {label} |")
        lines.append("")

        am_pct = intraday.get("am_pct", 0)
        lines.append(f"- 上午占比: {am_pct:.1f}%  |  下午占比: {100-am_pct:.1f}%")
        lines.append("")

        summary = intraday.get("summary", "")
        if summary:
            lines.append(f"> {summary}")
            lines.append("")

    # 散户情绪分析
    sentiment = data.get("sentiment")
    if sentiment and sentiment.get("total_posts_analyzed", 0) > 0:
        lines.append("## 散户情绪分析")
        lines.append("")
        idx = sentiment["sentiment_index"]
        ratio = sentiment["bullish_ratio"]
        total = sentiment["total_posts_analyzed"]

        if idx >= 70:
            mood = "极度乐观"
        elif idx >= 60:
            mood = "偏多"
        elif idx >= 45:
            mood = "中性观望"
        elif idx >= 35:
            mood = "偏空"
        else:
            mood = "极度悲观"

        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 情绪指数 | {idx:.1f} / 100 ({mood}) |")
        lines.append(f"| 看多比例 | {ratio:.1%} |")
        lines.append(f"| 样本量 | {total} 帖 |")
        lines.append(f"| 看多帖 | {sentiment.get('bullish_count', 0)} |")
        lines.append(f"| 看空帖 | {sentiment.get('bearish_count', 0)} |")
        lines.append(f"| 中性帖 | {sentiment.get('neutral_count', 0)} |")
        lines.append("")

        pb = sentiment.get("platform_breakdown", {})
        if pb:
            parts = ", ".join(f"{k}: {v}帖" for k, v in pb.items())
            lines.append(f"**来源分布**: {parts}")
            lines.append("")

        top_kw = sentiment.get("top_keywords", [])
        if top_kw:
            kw_parts = ", ".join(f"{item['keyword']}({item['count']})" for item in top_kw)
            lines.append(f"**热门关键词**: {kw_parts}")
            lines.append("")

        summary_text = sentiment.get("summary_text", "")
        if summary_text:
            lines.append(f"> {summary_text}")
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
