"""
完整新策略选股 — 8/11 收盘数据选股，8/12 实盘验证
"""
import sys
sys.path.insert(0, 'D:/work/daily-trader')

import pandas as pd
import numpy as np
if not hasattr(pd.DataFrame, 'append'):
    pd.DataFrame.append = lambda self, other, ignore_index=False, **kwargs: pd.concat([self, other], ignore_index=ignore_index)

from src.data.fetcher import fetch_limit_up_pool, fetch_stock_kline, fetch_realtime_price
from src.trading.consecutive_zt import ConsecutiveZtScanner, DivergenceTracker
from config import (
    CONSECUTIVE_ZT_MIN_COUNT, CONSECUTIVE_ZT_LIMIT_UP_TOLERANCE,
    CONSECUTIVE_ZT_MIN_STOCKS, CONSECUTIVE_ZT_MAIN_THREAD_THRESHOLD,
)
from datetime import datetime, timedelta

SIM_DATE = '20260811'
scanner = ConsecutiveZtScanner()
tracker = DivergenceTracker()

print('=' * 65)
print('  新策略完整选股 — 模拟 8/11 收盘后')
print('=' * 65)

# ================================================================
# 1. 连板主线选股
# ================================================================
print()
print('【一、连板主线选股】')

pool = fetch_limit_up_pool(date=SIM_DATE)
zt_selected = {}

if pool is None or pool.empty:
    print('无法获取 8/11 涨停池')
else:
    candidates = pool[pool['consecutive_count'] >= CONSECUTIVE_ZT_MIN_COUNT].copy()
    print(f'8/11 涨停池: {len(pool)}只, 3+连板候选: {len(candidates)}只')

    gap_stocks = []
    for _, row in candidates.iterrows():
        code = str(row['code']).strip()
        name = str(row.get('name', '')).strip()
        if not code:
            continue
        result = scanner._verify_gap_limit_up(code, name, int(row['consecutive_count']))
        if result is None:
            continue
        is_valid, gap_days = result
        if not is_valid:
            continue

        seal_time = str(row.get('first_seal_time', '')).strip()
        industry = str(row.get('industry', '')).strip()
        lu_type = scanner._classify(gap_days, seal_time, int(row['consecutive_count']))

        gap_stocks.append({
            'code': code, 'name': str(row['name']).strip(),
            'consecutive_count': int(row['consecutive_count']),
            'gap_days': gap_days, 'limit_up_type': lu_type,
            'industry': industry,
        })

    if len(gap_stocks) < CONSECUTIVE_ZT_MIN_STOCKS:
        print(f'跳空连板股: {len(gap_stocks)}只 (< {CONSECUTIVE_ZT_MIN_STOCKS}，未触发)')
    else:
        ind_dist = {}
        for s in gap_stocks:
            ind = s['industry'] or '未知'
            ind_dist[ind] = ind_dist.get(ind, 0) + 1
        total = len(gap_stocks)
        max_pct = max(ind_dist.values()) / total if ind_dist else 0
        no_main = max_pct < CONSECUTIVE_ZT_MAIN_THREAD_THRESHOLD
        main_ind = None if no_main else max(ind_dist, key=ind_dist.get)

        print(f'触发! {total}只跳空连板股')
        print(f'主线: {"不明确" if no_main else main_ind} (集中度{max_pct:.0%})')
        for ind, cnt in sorted(ind_dist.items(), key=lambda x: -x[1]):
            print(f'  {ind}: {cnt}只')
        for s in gap_stocks:
            print(f'  {s["name"]}({s["code"]}) {s["consecutive_count"]}连板 '
                  f'{s["limit_up_type"]} | {s["industry"]}')
            zt_selected[s['code']] = s

# ================================================================
# 2. 分歧回调企稳 — 回溯 8/8~8/10，评估至 8/11
# ================================================================
print()
print('【二、分歧回调企稳】')

pool_811 = fetch_limit_up_pool(date=SIM_DATE)
still_zt_codes = set(pool_811['code'].astype(str).str.strip()) if pool_811 is not None else set()

seen_codes = set()
broken_candidates = []

for days_back in range(1, 7):  # 回溯1~6天
    lookback_date = (datetime.strptime(SIM_DATE, '%Y%m%d') - timedelta(days=days_back)).strftime('%Y%m%d')
    prev_pool = fetch_limit_up_pool(date=lookback_date)
    if prev_pool is None or prev_pool.empty:
        continue

    prev_zt = prev_pool[prev_pool['consecutive_count'] >= CONSECUTIVE_ZT_MIN_COUNT]
    for _, row in prev_zt.iterrows():
        code = str(row['code']).strip()
        if code in seen_codes or code in still_zt_codes:
            continue
        seen_codes.add(code)
        broken_candidates.append({
            'code': code,
            'name': str(row.get('name', '')).strip(),
            'prev_zt_days': int(row['consecutive_count']),
            'prev_price': float(row['latest_price']),
            'industry': str(row.get('industry', '')).strip(),
        })

print(f'回溯 8/6~8/10，断板候选: {len(broken_candidates)}只')

all_evaluated = []
div_selected = []

for bc in broken_candidates:
    code = bc['code']
    name = bc['name']

    df = fetch_stock_kline(code, days=30)
    if df is None or df.empty:
        continue
    df = df.sort_values('date', ascending=True).reset_index(drop=True)
    df = df[df['date'] <= '2026-08-11'].copy()
    if len(df) < 10:
        continue

    close = df['close'].astype(float).values
    open_ = df['open'].astype(float).values
    high = df['high'].astype(float).values
    low = df['low'].astype(float).values
    volume = df['volume'].astype(float).values

    # Find peak
    peak_idx = -1
    for i in range(len(close) - 1, -1, -1):
        prev_c = close[i - 1] if i > 0 else close[0]
        limit_up = tracker._calc_limit_up_price(prev_c, code, '')
        if tracker._near(close[i], limit_up, CONSECUTIVE_ZT_LIMIT_UP_TOLERANCE):
            peak_idx = i
            break

    if peak_idx < 3:
        continue

    peak_zt_days = 1
    for i in range(peak_idx - 1, 0, -1):
        prev_c = close[i - 1]
        limit_up = tracker._calc_limit_up_price(prev_c, code, '')
        if tracker._near(close[i], limit_up, CONSECUTIVE_ZT_LIMIT_UP_TOLERANCE):
            peak_zt_days += 1
        else:
            break

    if peak_zt_days < CONSECUTIVE_ZT_MIN_COUNT:
        continue

    peak_price = float(close[peak_idx])
    current_price = float(close[-1])
    pullback_pct = (peak_price - current_price) / peak_price if peak_price > 0 else 0

    if pullback_pct <= 0:
        continue

    days_since_break = len(close) - 1 - peak_idx
    if days_since_break < 1 or days_since_break > 10:
        continue

    # ---- Scoring ----
    score = 0
    zt_vol = volume[peak_idx - peak_zt_days + 1 : peak_idx + 1]
    post_vol = volume[peak_idx + 1 :]
    if len(zt_vol) > 0 and len(post_vol) > 0:
        avg_zt_vol = np.mean(zt_vol)
        avg_post_vol = np.mean(post_vol)
        vol_contract = avg_post_vol / avg_zt_vol if avg_zt_vol > 0 else 999
        if vol_contract < 0.7: score += 30
        elif vol_contract < 1.0: score += 20
        elif vol_contract < 1.3: score += 10
    else:
        vol_contract = 0

    ma5 = float(np.mean(close[-6:])) if len(close) >= 6 else float(np.mean(close[-5:]))
    ma10 = float(np.mean(close[-11:])) if len(close) >= 11 else float(np.mean(close[-10:])) if len(close) >= 10 else current_price
    if current_price >= ma5:
        score += 25; held_support = 'MA5'
    elif current_price >= ma10:
        score += 15; held_support = 'MA10'
    else:
        held_support = '破位'

    if 0.03 <= pullback_pct <= 0.08:
        score += 20
    elif 0.08 < pullback_pct <= 0.15:
        score += 10
    elif pullback_pct < 0.03:
        score += 5

    body = abs(close[-1] - open_[-1])
    spread = high[-1] - low[-1]
    is_doji = spread > 0 and body / spread < 0.001 if spread > 0 else False
    lower_shadow = min(open_[-1], close[-1]) - low[-1]
    is_hammer = (lower_shadow >= 2 * body) and body > 0
    today_pct = (close[-1] - close[-2]) / close[-2] if len(close) >= 2 else 0

    if is_hammer or is_doji:
        score += 25
        stabilization = '锤子线' if is_hammer else '十字星'
    elif -1 <= today_pct * 100 <= 3:
        score += 15
        stabilization = f'缩量小阳({today_pct:+.1%})' if today_pct >= 0 else f'窄幅整理({today_pct:+.1%})'
    else:
        stabilization = '未企稳'

    result = {
        'code': code, 'name': name,
        'peak_zt_days': peak_zt_days, 'days_since_break': days_since_break,
        'peak_price': round(peak_price, 2), 'current_price': round(current_price, 2),
        'pullback_pct': round(pullback_pct * 100, 1),
        'volume_contract': round(vol_contract, 2),
        'held_support': held_support, 'stabilization': stabilization,
        'divergence_score': score,
        'industry': bc['industry'],
    }
    all_evaluated.append(result)
    if score >= 60:
        div_selected.append(result)

all_evaluated.sort(key=lambda x: -x['divergence_score'])

print(f'有效回调候选: {len(all_evaluated)}只, 通过60分: {len(div_selected)}只')
for r in all_evaluated:
    passed = 'PASS' if r['divergence_score'] >= 60 else 'FAIL'
    print(f'  [{passed}] {r["name"]}({r["code"]}) {r["divergence_score"]}分 '
          f'{r["peak_zt_days"]}连板 断板{r["days_since_break"]}天 回调-{r["pullback_pct"]}% '
          f'支撑={r["held_support"]} | {r["stabilization"]}')

# ================================================================
# 3. 汇总 + 8/12 实盘验证
# ================================================================
print()
print('=' * 65)
print('  8/12 实盘验证')
print('=' * 65)

all_picks = []
for code, s in zt_selected.items():
    all_picks.append((code, s['name'], '连板选股', f'{s["consecutive_count"]}连板', s['limit_up_type']))
for r in div_selected:
    all_picks.append((r['code'], r['name'], '分歧回调',
                      f'{r["peak_zt_days"]}连板', f'断板{r["days_since_break"]}天 回调-{r["pullback_pct"]}%'))

print(f'\n新策略共选出: {len(all_picks)}只\n')

up_count = 0
for code, name, source, extra, extra2 in all_picks:
    rt = fetch_realtime_price(code)
    if rt:
        chg = rt['pct_change']
        arrow = 'UP' if chg > 0 else ('DOWN' if chg < 0 else '--')
        if chg > 0:
            up_count += 1
        print(f'  [{source}] {name}({code}) {extra} {extra2}')
        print(f'    8/12: {rt["current"]} {arrow} {chg:+.1f}%  '
              f'(O={rt["open"]} H={rt["high"]} L={rt["low"]})')
    else:
        print(f'  [{source}] {name}({code}) 无数据')

print(f'\n上涨: {up_count}/{len(all_picks)}')
print(f'平均涨幅验证完成')
