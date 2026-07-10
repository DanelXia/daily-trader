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

from config import STOCK_POOL_FILE
from src.data.industry import IndustryMapper
from src.research.industry_analysis import IndustryAnalyzer
from src.research.stock_selection import StockSelector


@click.group()
def cli():
    """Daily Trader - A股投资研究与交易系统"""
    pass


@cli.command()
@click.option("--industry", default=None, help="指定行业代码（跳过行业分析，直接选股）")
@click.option("--top-industries", default=3, help="最优行业数量")
@click.option("--top-stocks", default=5, help="每个行业最优个股数量")
def research(industry, top_industries, top_stocks):
    """运行周度行业研究与个股筛选"""
    print("=" * 60)
    print(f"  Daily Trader - 周度研究报告  {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 60)
    print()

    # 加载行业映射
    mapper = IndustryMapper()
    mapper.load()

    if industry:
        # 指定行业模式：跳过行业分析，直接选股
        industries = [{"industry_name": industry, "industry_display": industry}]
    else:
        # 全行业分析
        analyzer = IndustryAnalyzer(mapper)
        industries = analyzer.analyze(top_n=top_industries)

    if not industries:
        print("[错误] 未找到符合条件的行业")
        sys.exit(1)

    # 个股筛选
    selector = StockSelector(mapper)
    for i, ind in enumerate(industries):
        ind["rank"] = i + 1
        industry_name = ind["industry_name"]
        print(f"\n{'=' * 40}")
        print(f"  行业 #{ind['rank']}: {ind.get('industry_display', industry_name)} - 得分 {ind.get('composite_score', 'N/A')}")
        print(f"{'=' * 40}")
        stocks = selector.score_stocks(industry_name, top_n=top_stocks)
        for j, s in enumerate(stocks):
            s["rank"] = j + 1
        ind["stocks"] = stocks

        if stocks:
            print(f"  入选 {len(stocks)} 只个股:")
            for s in stocks:
                m = s.get("metrics", {})
                print(f"    {s['rank']}. {s['name']}({s['code']}) "
                      f"综合={s['composite_score']} PE={m.get('pe_dynamic','-')} "
                      f"价格={m.get('price_latest','-')}")

    # 生成报告
    from src.reports.generator import generate_weekly_report
    md_path, json_path = generate_weekly_report(industries)

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

        # 生成交易信号
        signal = engine.analyze(code, name, indicators)
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

    # 生成报告
    from src.reports.generator import generate_daily_report
    md_path, json_path = generate_daily_report(suggestions, market_snapshot)

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
        print(f"  #{ind['rank']} {ind['industry_name']} (得分: {ind['composite_score']})")
        for s in ind.get("stocks", []):
            print(f"      {s['rank']}. {s['name']}({s['code']}) 综合={s['composite_score']}")


if __name__ == "__main__":
    cli()
