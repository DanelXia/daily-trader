"""大盘综合分析脚本"""
import sys, io, urllib.request, json
import pandas as pd, numpy as np
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
now = datetime.now()

# === 1. 实时指数 ===
codes = {
    'sh000001': '上证指数', 'sz399001': '深证成指', 'sz399006': '创业板指',
    'sh000688': '科创50', 'sh000300': '沪深300', 'sh000016': '上证50',
    'sh000905': '中证500', 'sh000852': '中证1000',
}
print(f'=== 大盘综合分析  {now.strftime("%Y-%m-%d %H:%M")} ===')
print()
print('--- 实时指数 ---')
url = 'https://hq.sinajs.cn/list=' + ','.join(codes.keys())
req = urllib.request.Request(url, headers={
    'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'})
with urllib.request.urlopen(req, timeout=10) as resp:
    data = resp.read().decode('gbk')

idx_data = {}
for line in data.strip().split('\n'):
    parts = line.split('"')
    if len(parts) < 2:
        continue
    name_part = parts[0].split('hq_str_')[-1] if 'hq_str_' in parts[0] else parts[0]
    vals = parts[1].split(',')
    if len(vals) < 6:
        continue
    name = None
    for code, cn_name in codes.items():
        if code[2:] in name_part:
            name = cn_name
            break
    if not name:
        continue
    price = float(vals[3])
    prev = float(vals[2])
    chg = (price - prev) / prev * 100
    high = float(vals[4])
    low = float(vals[5])
    open_p = float(vals[1])
    amt = float(vals[9]) / 1e8 if len(vals) > 9 else 0
    idx_data[name] = {'price': price, 'chg': chg, 'high': high, 'low': low, 'open': open_p, 'amt': amt}
    arrow = '\u2191' if chg > 0.5 else ('\u2193' if chg < -0.5 else '\u2192')
    intraday = (price - open_p) / open_p * 100
    hl_range = (high - low) / prev * 100
    print(f'{name:<6} {price:>10.2f}  {arrow} {chg:+.2f}%  |  日内{intraday:+.1f}%  |  振幅{hl_range:.1f}%  |  成交{amt:.0f}亿')

# === 2. 涨跌家数 ===
print()
print('--- 市场宽度 ---')
try:
    url2 = 'https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=1.000001,0.399001,0.399006,1.000688&fields=f2,f3,f14,f104,f105,f106'
    req2 = urllib.request.Request(url2, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req2, timeout=10) as resp2:
        idx_detail = json.loads(resp2.read()).get('data', {}).get('diff', [])
    for item in idx_detail:
        name = item.get('f14', '')
        up = item.get('f104', 0) or 0
        down = item.get('f106', 0) or 0
        flat = item.get('f105', 0) or 0
        if up or down:
            total = up + down + flat
            up_pct = up / total * 100 if total else 0
            print(f'  {name}: 涨{up}家 / 跌{down}家 / 平{flat}家  ->  上涨占比 {up_pct:.1f}%')
except Exception as e:
    print(f'  涨跌家数接口失败: {e}')

# === 3. K线和量价 ===
print()
print('--- 量价深度分析 ---')
kurl = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=0&end=20500101&lmt=120'
req3 = urllib.request.Request(kurl, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req3, timeout=10) as resp3:
    kdata = json.loads(resp3.read())

klines = kdata.get('data', {}).get('klines', [])
rows = []
for k in klines:
    d, o, c, h, l, v, amt = k.split(',')
    rows.append({'date': pd.Timestamp(d), 'close': float(c), 'volume': float(v),
                 'amount': float(amt), 'high': float(h), 'low': float(l)})
df = pd.DataFrame(rows).sort_values('date').reset_index(drop=True)
df['vol_ma20'] = df['volume'].rolling(20).mean()
df['ma5'] = df['close'].rolling(5).mean()
df['ma10'] = df['close'].rolling(10).mean()
df['ma20'] = df['close'].rolling(20).mean()
df['ma60'] = df['close'].rolling(60).mean()

today_norm = pd.Timestamp.now().normalize()
full_days = df[df['date'] < today_norm]
last01 = full_days.iloc[-1]
last20 = full_days.tail(20)

cur_price = idx_data.get('上证指数', {}).get('price', last01['close'])
ma5 = full_days['ma5'].iloc[-1]
ma10 = full_days['ma10'].iloc[-1]
ma20 = full_days['ma20'].iloc[-1]
ma60 = full_days['ma60'].iloc[-1]

print(f'昨收: {last01["close"]:.0f}  实时: {cur_price:.0f}')
print(f'MA5: {ma5:.0f}  MA10: {ma10:.0f}  MA20: {ma20:.0f}  MA60: {ma60:.0f}')
print(f'当前 vs MA60: {(cur_price/ma60-1)*100:+.1f}%  |  vs MA20: {(cur_price/ma20-1)*100:+.1f}%')
print(f'MA5 vs MA20: {"多头" if ma5 > ma20 else "死叉"} ({(ma5-ma20)/ma20*100:+.1f}%)')

# 连阴
consecutive = 0
for i in range(len(full_days)-1, 0, -1):
    if full_days.iloc[i]['close'] < full_days.iloc[i-1]['close']:
        consecutive += 1
    else:
        break
print(f'连续阴线: {consecutive}天')

# === 4. 波动特征 ===
print()
print('--- 近期波动 ---')
last20_copy = last20.copy()
last20_copy['range_pct'] = (last20_copy['high'] - last20_copy['low']) / last20_copy['close'] * 100
down_days = (last20_copy['close'] < last20_copy['close'].shift(1)).sum()
print(f'近20日均振幅: {last20_copy["range_pct"].mean():.1f}%')
print(f'昨日振幅: {(last01["high"]-last01["low"])/last01["close"]*100:.1f}%')
print(f'阴线占比: {down_days}/20 ({down_days/20*100:.0f}%)')

# 最大回撤(20天)
peak20 = last20_copy['close'].max()
trough20 = last20_copy['close'].min()
dd20 = (trough20 - peak20) / peak20 * 100
print(f'近20天最大回撤: {dd20:.1f}% (高{peak20:.0f} -> 低{trough20:.0f})')

# === 5. 量价背离 ===
print()
print('--- 量价背离检测 ---')
last5 = full_days.tail(5)
prev5 = full_days.tail(10).head(5)
vol_delta = (last5['volume'].mean() - prev5['volume'].mean()) / prev5['volume'].mean() * 100
price_delta = (last5['close'].mean() - prev5['close'].mean()) / prev5['close'].mean() * 100
print(f'价: 近5日 vs 前5日 = {price_delta:+.1f}%')
print(f'量: 近5日 vs 前5日 = {vol_delta:+.1f}%')
if price_delta < 0 and vol_delta < -10:
    print('-> 价跌量缩：典型缩量阴跌，没有恐慌盘也没有抄底盘')
elif price_delta > 0 and vol_delta < -10:
    print('-> 价涨量缩：无量反弹，不可持续')

# 放量/缩量天数统计
avg_vol20 = full_days['volume'].tail(20).mean()
last20_vol_ratio = last20_copy['volume'] / avg_vol20
high_vol = (last20_vol_ratio > 1.2).sum()
low_vol = (last20_vol_ratio < 0.8).sum()
normal_vol = 20 - high_vol - low_vol
print(f'近20天量能: 放量{high_vol}天  /  正常{normal_vol}天  /  缩量{low_vol}天')

# === 6. 今日预估 ===
print()
print('--- 今日预估 ---')
today_amt = sum(d['amt'] for d in idx_data.values()) * 1e8
first_hour = idx_data.get('上证指数', {}).get('amt', 3000)
# 首小时通常占全天 25-30%
est_full_amt = first_hour / 0.27 * 1e8
print(f'两市已成交: {today_amt/1e8:.0f}亿')
print(f'上证首小时: {first_hour:.0f}亿')
print(f'预估全天(上证): {est_full_amt/1e8:.0f}亿')

# === 7. 指数分化排名 ===
print()
print('--- 指数强弱排名 ---')
sorted_idx = sorted(idx_data.items(), key=lambda x: x[1]['chg'])
for name, d in sorted_idx:
    bar = '-' * max(1, int(abs(d['chg']) * 5))
    print(f'  {name:<6} {d["chg"]:+.2f}%  {bar}')

# === 8. 综合判断 ===
print()
print('=== 综合判断 ===')
# 收集信号
signals = []

# 均线
if cur_price < ma5 < ma10:
    signals.append(('空', '均线空头排列，价格在所有短期均线下方'))
elif cur_price < ma20:
    signals.append(('空', f'跌破MA20({ma20:.0f})'))

# 量能
if vol_delta < -15:
    signals.append(('空', '持续缩量，市场失去活性'))
elif vol_delta < -5:
    signals.append(('空', '量能下滑中'))

# 连阴
if consecutive >= 3:
    signals.append(('空', f'连续{consecutive}根阴线，短期超卖'))

# 回撤
if dd20 < -3:
    signals.append(('空', f'20天回撤{dd20:.1f}%，尚未达到历史底部均值'))

# 涨跌比
sse_up_pct = 0
for item in idx_detail:
    if item.get('f14') == '上证指数':
        up = item.get('f104', 0) or 0
        down = item.get('f106', 0) or 0
        if up + down > 0:
            sse_up_pct = up / (up + down) * 100

if sse_up_pct > 0:
    if sse_up_pct < 30:
        signals.append(('空', f'上涨占比仅{sse_up_pct:.0f}%，普跌格局'))
    elif sse_up_pct > 70:
        signals.append(('多', f'上涨占比{sse_up_pct:.0f}%，普涨格局'))

# 指数分化
chgs = [d['chg'] for d in idx_data.values()]
if max(chgs) - min(chgs) > 3:
    signals.append(('警', f'指数严重分化(极差{max(chgs)-min(chgs):.1f}%)，结构性行情'))

for direction, msg in signals:
    tag = {'空': '[-]', '多': '[+]', '警': '[!]'}[direction]
    print(f'  {tag} {msg}')

if all(d == '空' for d, _ in signals):
    print()
    print('  结论: 全面偏空，缩量阴跌格局延续。等待放量信号出现。')
