"""
CLI 入口。支持以下命令：
  python -m src.main research    # 运行周度研究
  python -m src.main trade       # 运行每日交易分析
  python -m src.main status      # 查看当前股票池
"""

import json
import sys
from datetime import datetime

# baostock 兼容 pandas 2.x
import pandas as pd
if not hasattr(pd.DataFrame, 'append'):
    pd.DataFrame.append = lambda self, other, ignore_index=False, **kwargs: pd.concat([self, other], ignore_index=ignore_index)

import click

from config import STOCK_POOL_FILE, REGIME_STATE_FILE, REGIME_HISTORY_FILE, DATA_DIR
from src.data.industry import IndustryMapper
from src.research.stock_selection import StockSelector


def _load_full_pool_universe() -> list[str] | None:
    """构建全池候选股代码列表。

    优先用回测流动股票池（近60日均额>2亿，与回测「实验组_路由」对齐，由
    tools/backtest_financials_pit.py 生成），无则返回 None 让 score_universe
    回退到所有证监会行业成分股并集。
    """
    universe_file = DATA_DIR / "backtest" / "universe.csv"
    if universe_file.exists():
        try:
            df = pd.read_csv(universe_file, dtype={"code": str})
            codes = [str(c).zfill(6) for c in df["code"]]
            if codes:
                return codes
        except Exception:
            pass
    return None


@click.group()
def cli():
    """Daily Trader - A股投资研究与交易系统"""
    pass


@cli.command()
@click.option("--industry", default=None, help="指定行业代码（跳过全池选股，只在该行业内选股）")
@click.option("--top-stocks", default=5, help="最优个股数量")
def research(industry, top_stocks):
    """运行周度研究（场景路由 + 全池选股）"""
    print("=" * 60)
    print(f"  Daily Trader - 周度研究报告  {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 60)
    print()

    # 加载行业映射
    mapper = IndustryMapper()
    mapper.load()

    # 市场场景识别 + 模型路由（失败则回退基础固定权重）
    from src.research.regime import MarketRegimeDetector
    from src.research.router import RegimeRouter
    from src.research.selection_models import get_model

    regime_info = None
    model = get_model(None)
    try:
        detection = MarketRegimeDetector().detect()
        if detection:
            state = RegimeRouter().route(detection)
            model = get_model(state["model"])
            regime_info = {
                "regime": state["regime"],
                "detected_regime": state["detected_regime"],
                "highvol": state["highvol"],
                "model": model.label,
                "target_position_pct": state["target_position_pct"],
                "features": state["features"],
            }
            print(f"\n  市场场景: {state['regime']}  →  选股模型: {model.label}"
                  f"  →  目标仓位: {state['target_position_pct'] * 100:.0f}%")
            print(f"  模型过滤: {model.filter_hint}\n")
    except Exception as e:
        print(f"[警告] 场景识别失败，回退基础固定权重: {e}")

    selector = StockSelector(mapper)

    if industry:
        # 指定行业模式：只在该行业内选股
        stocks = selector.score_stocks(industry, top_n=top_stocks, weights=model.stock_weights,
                                       technical_style=model.technical_style)
        industries = [{
            "industry_name": industry,
            "industry_display": industry,
            "rank": 1,
            "stocks": stocks,
        }]
    else:
        # 全池选股（回测 §8.7：行业层硬过滤在熊市防守上劣化，改为全池打分取 Top-N）
        universe = _load_full_pool_universe()
        stocks = selector.score_universe(top_n=top_stocks, weights=model.stock_weights,
                                         technical_style=model.technical_style, codes=universe)
        industries = [{
            "industry_name": "全池选股",
            "industry_display": "全池选股",
            "rank": 1,
            "stocks": stocks,
        }]

    if not industries or not any(ind.get("stocks") for ind in industries):
        print("[错误] 未选出符合条件的个股")
        sys.exit(1)

    # 个股筛选结果展示
    for ind in industries:
        for j, s in enumerate(ind.get("stocks", [])):
            s["rank"] = j + 1

        print(f"\n{'=' * 40}")
        print(f"  #{ind['rank']}: {ind.get('industry_display', ind['industry_name'])}")
        print(f"{'=' * 40}")
        stocks = ind.get("stocks", [])
        if stocks:
            print(f"  入选 {len(stocks)} 只个股:")
            for s in stocks:
                m = s.get("metrics", {})
                print(f"    {s['rank']}. {s['name']}({s['code']}) "
                      f"综合={s['composite_score']} PE={m.get('pe_dynamic','-')} "
                      f"价格={m.get('price_latest','-')}")
        else:
            print("  [无]")

    # 生成报告
    from src.reports.generator import generate_weekly_report
    md_path, json_path = generate_weekly_report(industries, regime_info=regime_info)

    print(f"\n{'=' * 60}")
    print(f"  研究完成!")
    print(f"  报告: {md_path}")
    print(f"  数据: {json_path}")
    print(f"  股票池: {STOCK_POOL_FILE}")
    print(f"{'=' * 60}")


@cli.command()
@click.option("--stock", default=None, help="指定股票代码（单只分析）")
@click.option("--pool", default=None, help="指定股票池JSON路径（默认读取 data/stock_pool.json）")
def trade(stock, pool):
    """运行每日交易分析"""
    from src.trading.technical import TechnicalAnalyzer
    from src.trading.signals import TradingSignalEngine

    print("=" * 60)
    print(f"  Daily Trader - 每日交易建议  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    print()

    # 加载股票列表
    if stock:
        stocks = [{"code": stock, "name": stock}]
    else:
        pool_path = pool or str(STOCK_POOL_FILE)
        try:
            with open(pool_path, "r", encoding="utf-8") as f:
                pool_data = json.load(f)
            stocks = []
            for ind in pool_data.get("industries", []):
                for s in ind.get("stocks", []):
                    stocks.append({"code": s["code"], "name": s["name"], "industry": ind["industry_name"]})
        except FileNotFoundError:
            print(f"[错误] 股票池文件未找到: {pool_path}")
            print("请先运行 research 命令生成股票池")
            sys.exit(1)

    if not stocks:
        print("[错误] 没有可分析的股票")
        sys.exit(1)

    # 技术分析
    ta = TechnicalAnalyzer()
    engine = TradingSignalEngine()

    # 大盘快照
    from src.data.fetcher import fetch_index_snapshot
    market_snapshot = fetch_index_snapshot()
    market_vol_ratio = market_snapshot.get("market_volume_ratio", 1.0)
    total_amt = market_snapshot.get("total_amount", 0)
    if total_amt:
        vol_label = "放量" if market_vol_ratio > 1.2 else ("缩量" if market_vol_ratio < 0.8 else "正常")
        print(f"\n大盘成交额: {total_amt:.0f}亿  |  量比: {market_vol_ratio:.2f}  |  {vol_label}\n")

    # 市场场景识别（仓位/止损联动）
    regime_state = None
    try:
        from src.research.regime import MarketRegimeDetector
        from src.research.router import RegimeRouter
        detection = MarketRegimeDetector().detect()
        if detection:
            st = RegimeRouter().route(detection)
            regime_state = {
                "regime": st["regime"],
                "highvol": st["highvol"],
                "target_position_pct": st["target_position_pct"],
            }
            hv = "（高波动）" if st["highvol"] else ""
            print(f"市场场景: {st['regime']}{hv}  →  账户目标仓位: {st['target_position_pct'] * 100:.0f}%\n")
    except Exception as e:
        print(f"[警告] 场景识别失败: {e}")

    # 散户情绪分析（提前获取，用于个股逆向加分）
    sentiment = None
    sentiment_index = None
    if datetime.now().hour >= 15:
        try:
            from src.research.sentiment import SentimentAnalyzer
            sa = SentimentAnalyzer()
            sentiment = sa.analyze()
            if sentiment:
                sentiment_index = sentiment["sentiment_index"]
                print(f"散户情绪: {sentiment['summary_text']}\n")
        except Exception as e:
            print(f"[警告] 散户情绪分析失败: {e}")

    suggestions = []
    for i, s in enumerate(stocks):
        code = s["code"]
        name = s.get("name", code)
        print(f"\n[{i+1}/{len(stocks)}] 分析 {name}({code})...")

        # 计算技术指标
        indicators = ta.compute_all(code)
        if indicators is None:
            print(f"  [跳过] 无法获取K线数据")
            continue

        # 生成交易信号（传入情绪指数用于逆向加分，传入筹码数据用于集中度加减分）
        concentration = None
        try:
            from src.data.fetcher import fetch_shareholder_data
            concentration = fetch_shareholder_data(code)
        except Exception:
            pass

        signal = engine.analyze(code, name, indicators, market_vol_ratio, sentiment_index, concentration, regime_state)
        signal["source"] = "stock_pool"
        if s.get("industry"):
            signal["industry"] = s["industry"]
        suggestions.append(signal)

        action_map = {
            "strong_buy": "STRONG BUY",
            "buy": "BUY",
            "hold": "HOLD",
            "reduce": "REDUCE",
            "strong_sell": "STRONG SELL",
        }
        action_str = action_map.get(signal["action"], signal["action"])
        print(f"  {action_str}  [{signal['trading_score']}分]"
              f"  入场={signal['entry']['price']}  止损={signal['stop_loss']}")

    # ================================================================
    # 连板主线选股模型（额外分析，不影响现有流程）
    # ================================================================
    consecutive_zt_result = None
    try:
        from src.trading.consecutive_zt import ConsecutiveZtScanner
        scanner = ConsecutiveZtScanner()
        zt_result = scanner.scan()

        if zt_result and zt_result.get("triggered"):
            zt_stocks = zt_result["stocks"]
            print(f"\n[连板选股] 发现 {len(zt_stocks)} 只跳空连板股，"
                  f"主线: {'不明确' if zt_result['no_main_thread'] else zt_result.get('main_thread_industry', '')}")

            # 去重：排除已在 stock_pool 中的股票
            existing_codes = {s["code"] for s in stocks}
            zt_analyzed = 0
            for zt_stock in zt_stocks:
                zt_code = zt_stock["code"]
                if zt_code in existing_codes:
                    continue

                zt_name = zt_stock["name"]
                print(f"  [{zt_name}({zt_code})] {zt_stock['consecutive_count']}连板 "
                      f"| {zt_stock['limit_up_type']} | {zt_stock['industry']}")

                # 走完整技术分析+信号打分流程
                indicators = ta.compute_all(zt_code)
                if indicators is None:
                    print(f"    [跳过] 无法获取K线数据")
                    continue

                concentration = None
                try:
                    concentration = fetch_shareholder_data(zt_code)
                except Exception:
                    pass

                signal = engine.analyze(
                    zt_code, zt_name, indicators,
                    market_vol_ratio, sentiment_index, concentration, regime_state
                )
                signal["source"] = "consecutive_zt"
                signal["industry"] = zt_stock["industry"]
                signal["zt_info"] = {
                    "consecutive_count": zt_stock["consecutive_count"],
                    "gap_days": zt_stock["gap_days"],
                    "limit_up_type": zt_stock["limit_up_type"],
                }
                suggestions.append(signal)
                zt_analyzed += 1

                action_str = action_map.get(signal["action"], signal["action"])
                print(f"    {action_str}  [{signal['trading_score']}分]"
                      f"  入场={signal['entry']['price']}  止损={signal['stop_loss']}")

            print(f"  [连板选股] 新增分析 {zt_analyzed} 只股票")
            consecutive_zt_result = zt_result
        else:
            print(f"\n[连板选股] 今日无满足条件的跳空连板股")
    except Exception as e:
        print(f"\n[警告] 连板主线选股模型失败: {e}")

    # ================================================================
    # 分歧回调企稳扫描（连板断板后的回调机会）
    # ================================================================
    divergence_result = None
    try:
        from src.trading.consecutive_zt import DivergenceTracker
        tracker = DivergenceTracker()
        div_result = tracker.scan()

        if div_result and div_result.get("triggered"):
            div_stocks = div_result["stocks"]
            print(f"\n[分歧回调] 发现 {len(div_stocks)} 只断板回调企稳股")
            for ds in div_stocks[:5]:
                print(f"  [{ds['name']}({ds['code']})] "
                      f"曾{ds['peak_zt_days']}连板 断板{ds['days_since_break']}天 "
                      f"回调{ds['pullback_pct']}% 评分{ds['divergence_score']} "
                      f"支撑={ds['held_support']} | {ds['stabilization']}")

            # 去重
            existing_codes = {s["code"] for s in stocks}
            for zt_sig in suggestions:
                existing_codes.add(zt_sig["code"])

            div_analyzed = 0
            for ds in div_stocks:
                if ds["code"] in existing_codes:
                    continue

                indicators = ta.compute_all(ds["code"])
                if indicators is None:
                    continue

                concentration = None
                try:
                    concentration = fetch_shareholder_data(ds["code"])
                except Exception:
                    pass

                signal = engine.analyze(
                    ds["code"], ds["name"], indicators,
                    market_vol_ratio, sentiment_index, concentration, regime_state
                )
                signal["source"] = "divergence"
                signal["industry"] = ds.get("industry", "")
                signal["divergence_info"] = {
                    "peak_zt_days": ds["peak_zt_days"],
                    "days_since_break": ds["days_since_break"],
                    "pullback_pct": ds["pullback_pct"],
                    "held_support": ds["held_support"],
                    "stabilization": ds["stabilization"],
                    "divergence_score": ds["divergence_score"],
                }
                suggestions.append(signal)
                div_analyzed += 1

                action_str = action_map.get(signal["action"], signal["action"])
                print(f"    {action_str} [{signal['trading_score']}分]"
                      f"  入场={signal['entry']['price']}  止损={signal['stop_loss']}")

            if div_analyzed > 0:
                print(f"  [分歧回调] 新增分析 {div_analyzed} 只股票")
            divergence_result = div_result
        else:
            print(f"\n[分歧回调] 今日无满足条件的断板回调企稳股")
    except Exception as e:
        print(f"\n[警告] 分歧回调扫描失败: {e}")

    # 生成报告
    from src.reports.generator import generate_daily_report
    from src.data.fetcher import fetch_intraday_analysis
    intraday = fetch_intraday_analysis()
    if intraday:
        print(f"\n大盘分时分析: {intraday.get('summary', '')}")
    md_path, json_path = generate_daily_report(
        suggestions, market_snapshot, intraday, sentiment,
        consecutive_zt=consecutive_zt_result,
        divergence=divergence_result,
        regime_state=regime_state,
    )

    print(f"\n{'=' * 60}")
    print(f"  交易分析完成!")
    print(f"  报告: {md_path}")
    print(f"  数据: {json_path}")
    print(f"{'=' * 60}")


@cli.command()
def status():
    """查看当前股票池摘要"""
    try:
        with open(STOCK_POOL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"股票池不存在: {STOCK_POOL_FILE}")
        print("请先运行 research 命令")
        return

    print(f"当前股票池 ({data['report_date']})")
    print(f"入选行业: {data['summary']['total_industries_selected']}")
    print(f"推荐个股: {data['summary']['total_stocks_recommended']} 只")
    print()
    for ind in data["industries"]:
        score = ind.get("composite_score")
        score_str = f" (得分: {score})" if score is not None else ""
        print(f"  #{ind['rank']} {ind['industry_name']}{score_str}")
        for s in ind.get("stocks", []):
            print(f"      {s['rank']}. {s['name']}({s['code']}) 综合={s['composite_score']}")


@cli.command()
def regime():
    """检测当前市场场景并输出路由结果"""
    from src.research.regime import MarketRegimeDetector
    from src.research.router import RegimeRouter

    print("=" * 60)
    print(f"  市场场景识别  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    detector = MarketRegimeDetector()
    detection = detector.detect()
    if detection is None:
        print("[错误] 无法获取指数数据，场景识别失败")
        sys.exit(1)

    router = RegimeRouter()
    state = router.route(detection)

    f = state["features"]
    switched = state["regime"] != state["detected_regime"]

    print(f"\n  检测场景: {state['detected_regime']}")
    print(f"  确认场景: {state['regime']}" + ("  [防抖确认中]" if switched else ""))
    print(f"  高波动:   {'是' if state['highvol'] else '否'}")
    print(f"  选股模型: {state['model']}")
    print(f"  目标仓位: {state['target_position_pct'] * 100:.0f}%")
    print(f"  预警信号: {f.get('warning') or '无'}")
    print(f"\n  特征:")
    print(f"    收盘={f['close']}  20日涨跌={f['ret20']}%  排列={f['arrange']}")
    print(f"    MA20={f['ma20']}  MA60={f['ma60']}  波动分位={f['vol_pct']}")
    print(f"\n  状态文件: {REGIME_STATE_FILE}")
    print(f"  历史文件: {REGIME_HISTORY_FILE}")


if __name__ == "__main__":
    cli()
