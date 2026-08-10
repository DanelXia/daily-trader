import json, urllib.request, pandas as pd, numpy as np, time, random
import sys
sys.stdout.reconfigure(encoding='utf-8')

# Load stock pool
with open('data/stock_pool.json', 'r', encoding='utf-8') as f:
    pool = json.load(f)

# Hard-tech industry keywords
tech_keywords = ['计算机', '通信', '电子', '半导体', '软件', '专用设备', '通用设备', '电气机械', '仪器仪表']

# Collect all tech stocks from pool
tech_stocks = []
for ind in pool['industries']:
    name = ind.get('industry_name', '')
    if any(kw in name for kw in tech_keywords):
        for s in ind.get('stocks', []):
            tech_stocks.append({
                'code': s['code'],
                'name': s['name'],
                'industry': name,
            })

# Add stocks from daily report
report_tech = [
    ('603986', '兆易创新', '半导体'),
    ('002281', '光迅科技', '光通信'),
    ('300738', '奥飞数据', '数据中心'),
    ('002558', '巨人网络', '游戏/AI'),
]
for code, name, ind in report_tech:
    if not any(s['code'] == code for s in tech_stocks):
        tech_stocks.append({'code': code, 'name': name, 'industry': ind})

print(f'Total tech stocks to scan: {len(tech_stocks)}')

results = []
for stock in tech_stocks:
    code = stock['code']
    name = stock['name']
    ind = stock['industry']

    if code.startswith('6'):
        symbol = f'sh{code}'
    elif code.startswith('0') or code.startswith('3'):
        symbol = f'sz{code}'
    else:
        continue

    url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=120'
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            kdata = json.loads(r.read())
    except:
        continue

    if not kdata or len(kdata) < 60:
        continue

    rows = []
    for k in kdata:
        rows.append({
            'date': pd.Timestamp(k['day']),
            'close': float(k['close']),
            'volume': float(k['volume']),
            'high': float(k['high']),
            'low': float(k['low'])
        })
    df = pd.DataFrame(rows).sort_values('date').reset_index(drop=True)

    if len(df) < 40:
        continue

    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma60'] = df['close'].rolling(60).mean()
    df['vol_ma20'] = df['volume'].rolling(20).mean()

    ema12 = df['close'].ewm(span=12).mean()
    ema26 = df['close'].ewm(span=26).mean()
    df['macd'] = ema12 - ema26
    df['signal'] = df['macd'].ewm(span=9).mean()
    df['hist'] = df['macd'] - df['signal']

    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    df['rsi14'] = 100 - (100/(1 + gain.ewm(span=14).mean()/loss.ewm(span=14).mean()))

    df['tr'] = np.maximum(df['high']-df['low'],
                np.maximum(abs(df['high']-df['close'].shift(1)),
                           abs(df['low']-df['close'].shift(1))))
    df['atr14'] = df['tr'].rolling(14).mean()

    last = df.iloc[-1]
    last20 = df.tail(20)
    last5 = df.tail(5)

    price = last['close']

    # 20d max drawdown
    peak20 = last20['close'].max()
    trough20 = last20['close'].min()
    dd20 = (trough20 - peak20) / peak20 * 100

    # Skip if no significant decline
    if dd20 > -5:
        continue

    # 5d / 10d return
    ret_5d = (last['close'] / df.iloc[-6]['close'] - 1) * 100 if len(df) > 5 else 0

    # Price vs MA5
    dist_ma5 = (price / last['ma5'] - 1) * 100 if pd.notna(last['ma5']) else 0

    # Volume contraction
    vol_ratio = last5['volume'].mean() / last20['volume'].mean() if last20['volume'].mean() > 0 else 1

    # MACD hist improvement
    last5_hist = df['hist'].tail(5).mean()
    prior5_hist = df['hist'].tail(10).head(5).mean()
    hist_improving = last5_hist > prior5_hist

    # Range contracting
    last5_range = ((last5['high'] - last5['low']) / last5['close']).mean()
    prior5_range = ((df.tail(10).head(5)['high'] - df.tail(10).head(5)['low']) / df.tail(10).head(5)['close']).mean()
    range_contracting = last5_range < prior5_range

    # RSI14
    rsi14 = df['rsi14'].iloc[-1] if pd.notna(df['rsi14'].iloc[-1]) else 50

    # MACD status
    macd_golden = last['macd'] > last['signal'] if pd.notna(last['macd']) and pd.notna(last['signal']) else False
    hist_val = last['hist'] if pd.notna(last['hist']) else 0

    # Scoring
    score = 0

    # Decline magnitude
    if dd20 < -20:
        score += 3
    elif dd20 < -15:
        score += 2
    elif dd20 < -10:
        score += 1

    # Price near MA5
    if -1 < dist_ma5 < 2:
        score += 2
    elif -3 < dist_ma5 < 3:
        score += 1

    # Volume contraction
    if vol_ratio < 0.6:
        score += 3
    elif vol_ratio < 0.8:
        score += 2
    elif vol_ratio < 1.0:
        score += 1

    # Hist improving
    if hist_improving:
        score += 2

    # Range contracting
    if range_contracting:
        score += 1

    # RSI14 sweet spot
    if 30 <= rsi14 <= 45:
        score += 2
    elif 45 < rsi14 <= 55:
        score += 1

    # MACD golden cross
    if macd_golden:
        score += 2

    # MA5 > MA20
    if pd.notna(last['ma5']) and pd.notna(last['ma20']) and last['ma5'] > last['ma20']:
        score += 1

    results.append({
        'code': code,
        'name': name,
        'industry': ind,
        'price': round(price, 2),
        'dd20': round(dd20, 1),
        'ret_5d': round(ret_5d, 1),
        'rsi14': round(rsi14, 1),
        'vol_ratio': round(vol_ratio, 2),
        'hist': round(hist_val, 4),
        'golden': macd_golden,
        'hist_imp': hist_improving,
        'range_con': range_contracting,
        'dist_ma5': round(dist_ma5, 1),
        'score': score
    })
    time.sleep(random.uniform(0.3, 0.6))

results.sort(key=lambda x: x['score'], reverse=True)

print()
print('=' * 80)
print('  近期下跌企稳的硬科技公司  (Score: 企稳信号综合强度)')
print('=' * 80)
print()
print(f'  {"代码":<8} {"名称":<8} {"行业":<12} {"价格":>7} {"20dDD":>7} {"5d":>6} {"RSI14":>6} {"量比":>5} {"MACD":>10} {"得分":>4}')
print(f'  {"-"*80}')

for r in results[:15]:
    macd_str = 'GOLDEN' if r['golden'] else ('绿收窄' if r['hist_imp'] else 'DEAD')
    print(f'  {r["code"]:<8} {r["name"]:<8} {r["industry"]:<12} {r["price"]:>7.2f} {r["dd20"]:>6.1f}% {r["ret_5d"]:>5.1f}% {r["rsi14"]:>6.1f} {r["vol_ratio"]:>4.2f}x {macd_str:>10} {r["score"]:>4}')

print()
print('  评分维度: 跌幅深度 + 量缩 + 价稳(MA5附近) + MACD收敛 + RSI适中 + 振幅收窄 + 金叉加分')
if not results:
    print('  (No stocks met the screening criteria)')
