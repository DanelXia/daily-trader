import urllib.request, json, pandas as pd, numpy as np

url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=116.00700&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=1&end=20500101&lmt=120'
req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read())
klines = data['data']['klines']

rows = []
for k in klines:
    parts = k.split(',')
    rows.append({
        'date': pd.Timestamp(parts[0]),
        'open': float(parts[1]),
        'close': float(parts[2]),
        'high': float(parts[3]),
        'low': float(parts[4]),
        'volume': float(parts[5]),
        'amount': float(parts[6]),
        'turnover': float(parts[10]),
    })
df = pd.DataFrame(rows).sort_values('date').reset_index(drop=True)

# Indicators
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
df['rsi6'] = 100 - (100/(1 + gain.ewm(span=6).mean()/loss.ewm(span=6).mean()))
df['rsi14'] = 100 - (100/(1 + gain.ewm(span=14).mean()/loss.ewm(span=14).mean()))

df['bb_mid'] = df['close'].rolling(20).mean()
bb_std = df['close'].rolling(20).std()
df['bb_up'] = df['bb_mid'] + 2*bb_std
df['bb_low'] = df['bb_mid'] - 2*bb_std

df['tr'] = np.maximum(df['high']-df['low'],
            np.maximum(abs(df['high']-df['close'].shift(1)),
                       abs(df['low']-df['close'].shift(1))))
df['atr14'] = df['tr'].rolling(14).mean()

last = df.iloc[-1]
prev = df.iloc[-2]
last20 = df.tail(20)
last5 = df.tail(5)
prev5 = df.tail(10).head(5)

price = last['close']
vol_ratio = last['volume'] / last['vol_ma20'] if pd.notna(last['vol_ma20']) else 0
chg = (last['close'] - prev['close']) / prev['close'] * 100

# Volume-price quadrant
vol_delta = (last5['volume'].mean() - prev5['volume'].mean())/prev5['volume'].mean()*100
price_delta = (last5['close'].mean() - prev5['close'].mean())/prev5['close'].mean()*100

# 20d stats
range20 = last20.copy()
range20['range'] = (range20['high']-range20['low'])/range20['close']*100
down_d = (range20['close'] < range20['close'].shift(1)).sum()
peak20 = range20['close'].max()
trough20 = range20['close'].min()
dd20 = (trough20-peak20)/peak20*100

high_60 = df.tail(60)['high'].max()
low_60 = df.tail(60)['low'].min()

arr = 'BULL' if last['ma5'] > last['ma10'] > last['ma20'] > last['ma60'] else \
      ('BEAR' if last['ma5'] < last['ma10'] < last['ma20'] < last['ma60'] else 'MIXED')

print('=' * 60)
print('  腾讯控股 (00700.HK)')
print('=' * 60)
print()
print('--- Price & MA ---')
print('  Close: {0:.1f}  Chg: {1:+.1f}%  Open: {2:.1f}  High: {3:.1f}  Low: {4:.1f}'.format(
    price, chg, last['open'], last['high'], last['low']))
print('  MA5: {0:.1f}  MA10: {1:.1f}  MA20: {2:.1f}  MA60: {3:.1f}'.format(
    last['ma5'], last['ma10'], last['ma20'], last['ma60']))
print('  Alignment: {0}'.format(arr))
print('  vs MA20: {0:+.1f}%  vs MA60: {1:+.1f}%'.format(
    (price/last['ma20']-1)*100, (price/last['ma60']-1)*100))
print('  Turnover: {0:.2f}%'.format(last['turnover']))

print()
print('--- Momentum ---')
s = 'GOLDEN' if last['macd'] > last['signal'] else 'DEAD'
print('  MACD: {0:.2f}  Signal: {1:.2f}  Hist: {2:.2f}  [{3}]'.format(
    last['macd'], last['signal'], last['hist'], s))
print('  RSI6: {0:.1f}  RSI14: {1:.1f}'.format(df['rsi6'].iloc[-1], df['rsi14'].iloc[-1]))
print('  Vol ratio: {0:.2f} ({1})'.format(vol_ratio,
    'HIGH' if vol_ratio>1.2 else ('LOW' if vol_ratio<0.8 else 'NORM')))

print()
print('--- Risk ---')
print('  ATR14: {0:.1f} ({1:.1f}%)'.format(last['atr14'], last['atr14']/last['close']*100))
bb_pos = (price-last['bb_low'])/(last['bb_up']-last['bb_low'])*100
print('  BB: up {0:.1f} mid {1:.1f} low {2:.1f}  pos {3:.0f}%'.format(
    last['bb_up'], last['bb_mid'], last['bb_low'], bb_pos))
print('  20d avg range: {0:.1f}%  down days: {1}/20'.format(range20['range'].mean(), down_d))
print('  60d H: {0:.1f}  L: {1:.1f}'.format(high_60, low_60))
print('  vs 60d H: {0:+.1f}%  vs 60d L: {1:+.1f}%'.format(
    (price/high_60-1)*100, (price/low_60-1)*100))
print('  20d max DD: {0:.1f}% ({1:.1f}->{2:.1f})'.format(dd20, peak20, trough20))

print()
print('--- Vol-Price 5d vs prev5d ---')
print('  Price: {0:+.1f}%  Vol: {1:+.1f}%'.format(price_delta, vol_delta))
if price_delta < 0 and vol_delta < -10:
    print('  => Shrinking decline (缩量下跌)')
elif price_delta > 0 and vol_delta < -10:
    print('  => Weak bounce, no vol (无量弱反弹)')
elif price_delta > 0 and vol_delta > 10:
    print('  => Healthy rally (放量上涨)')
elif price_delta < 0 and vol_delta > 10:
    print('  => Distribution / panic (放量下跌)')

print()
print('--- Last 10d ---')
last10 = df.tail(10)
max_v = last10['volume'].max()
for i, (_, r) in enumerate(last10.iterrows()):
    bar = '#' * max(1, int(r['volume']/max_v*25))
    p_chg = ''
    if i > 0:
        p_chg = '{0:+.1f}%'.format((r['close']/last10.iloc[i-1]['close']-1)*100)
    print('  {0}  {1:8.1f}  {2:>7s}  {3}'.format(
        r['date'].strftime('%m/%d'), r['close'], p_chg, bar))

# Key levels
print()
print('--- Key Levels ---')
print('  Resistance: {0:.1f} (BB up) / {1:.1f} (60d H)'.format(last['bb_up'], high_60))
print('  Support: {0:.1f} (MA20) / {1:.1f} (BB low) / {2:.1f} (60d L)'.format(
    last['ma20'], last['bb_low'], low_60))
print('  Pivot (today high): {0:.1f}'.format(last['high']))
