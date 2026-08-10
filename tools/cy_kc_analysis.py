import urllib.request, json, pandas as pd, numpy as np

def analyze_index(code, name):
    url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen=120'
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        kdata = json.loads(r.read())

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

    return df

cyb = analyze_index('sz399006', 'Chinext')
kc = analyze_index('sh000688', 'STAR50')

data = [
    ('Chinext', cyb, 3343.96, 3246.08),
    ('STAR50', kc, 1635.96, 1588.52)
]

for name, df, price, prev in data:
    today_norm = pd.Timestamp.now().normalize()
    full = df[df['date'] < today_norm]
    last = full.iloc[-1]
    last20 = full.tail(20)
    last5 = full.tail(5)
    prev5 = full.tail(10).head(5)

    vol_ratio = last['volume'] / last['vol_ma20'] if pd.notna(last['vol_ma20']) else 0
    chg = (price - prev) / prev * 100

    range20 = last20.copy()
    range20['range'] = (range20['high']-range20['low'])/range20['close']*100
    down_d = (range20['close'] < range20['close'].shift(1)).sum()
    peak20 = range20['close'].max()
    trough20 = range20['close'].min()
    dd20 = (trough20-peak20)/peak20*100

    high_60 = full.tail(60)['high'].max()
    low_60 = full.tail(60)['low'].min()

    vol_delta = (last5['volume'].mean() - prev5['volume'].mean())/prev5['volume'].mean()*100
    price_delta = (last5['close'].mean() - prev5['close'].mean())/prev5['close'].mean()*100

    arr = 'BULL' if last['ma5'] > last['ma10'] > last['ma20'] > last['ma60'] else \
          ('BEAR' if last['ma5'] < last['ma10'] < last['ma20'] < last['ma60'] else 'MIXED')

    print('=' * 60)
    print('  {0}'.format(name))
    print('=' * 60)
    print()
    print('--- Price & MA ---')
    print('  Now: {0:.0f}  Chg: {1:+.1f}%  Prev: {2:.0f}'.format(price, chg, prev))
    print('  MA5: {0:.0f}  MA10: {1:.0f}  MA20: {2:.0f}  MA60: {3:.0f}'.format(
        last['ma5'], last['ma10'], last['ma20'], last['ma60']))
    print('  Alignment: {0}'.format(arr))
    print('  vs MA20: {0:+.1f}%  vs MA60: {1:+.1f}%'.format(
        (price/last['ma20']-1)*100, (price/last['ma60']-1)*100))

    print()
    print('--- Momentum ---')
    s = 'GOLDEN' if last['macd'] > last['signal'] else 'DEAD'
    print('  MACD: {0:.1f}  Signal: {1:.1f}  Hist: {2:.1f}  [{3}]'.format(
        last['macd'], last['signal'], last['hist'], s))
    print('  RSI6: {0:.1f}  RSI14: {1:.1f}'.format(full['rsi6'].iloc[-1], full['rsi14'].iloc[-1]))
    print('  Vol ratio: {0:.2f} ({1})'.format(vol_ratio,
        'HIGH' if vol_ratio>1.2 else ('LOW' if vol_ratio<0.8 else 'NORM')))

    print()
    print('--- Risk ---')
    print('  ATR14: {0:.0f} ({1:.1f}%)'.format(last['atr14'], last['atr14']/last['close']*100))
    bb_pos = (price-last['bb_low'])/(last['bb_up']-last['bb_low'])*100
    print('  BB: up {0:.0f} mid {1:.0f} low {2:.0f}  pos {3:.0f}%'.format(
        last['bb_up'], last['bb_mid'], last['bb_low'], bb_pos))
    print('  20d avg range: {0:.1f}%  down days: {1}/20'.format(range20['range'].mean(), down_d))
    print('  60d H: {0:.0f}  L: {1:.0f}'.format(high_60, low_60))
    print('  vs 60d H: {0:+.1f}%  vs 60d L: {1:+.1f}%'.format(
        (price/high_60-1)*100, (price/low_60-1)*100))
    print('  20d max DD: {0:.1f}% ({1:.0f}->{2:.0f})'.format(dd20, peak20, trough20))

    print()
    print('--- Vol-Price ---')
    print('  Price 5d vs prev5d: {0:+.1f}%  Vol: {1:+.1f}%'.format(price_delta, vol_delta))
    if price_delta < 0 and vol_delta < -10:
        print('  => Shrinking decline')
    elif price_delta > 0 and vol_delta < -10:
        print('  => Weak bounce, no vol')
    elif price_delta > 0 and vol_delta > 10:
        print('  => Healthy rally')
    elif price_delta < 0 and vol_delta > 10:
        print('  => Distribution / panic')

    print()
    print('--- Last 10d ---')
    last10 = full.tail(10)
    max_v = last10['volume'].max()
    for i, (_, r) in enumerate(last10.iterrows()):
        bar = '#' * max(1, int(r['volume']/max_v*25))
        p_chg = ''
        if i > 0:
            p_chg = '{0:+.1f}%'.format((r['close']/last10.iloc[i-1]['close']-1)*100)
        print('  {0}  {1:8.0f}  {2:>7s}  {3}'.format(
            r['date'].strftime('%m/%d'), r['close'], p_chg, bar))
    print()
