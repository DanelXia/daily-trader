"""计算 8/4 至今涨幅排名前100的股票（数据源：新浪日K线）"""
import sys
sys.path.insert(0, 'D:/work/daily-trader')

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import akshare as ak

BASE_DATE = '2026-08-04'


def get_symbol(code):
    if code.startswith(('6', '9')):
        return f'sh{code}'
    if code.startswith(('8', '4')):
        return f'bj{code}'
    return f'sz{code}'


def fetch_kline(code):
    symbol = get_symbol(code)
    url = (
        'https://quotes.sina.cn/cn/api/jsonp_v2.php/data/'
        'CN_MarketDataService.getKLineData'
        f'?symbol={symbol}&scale=240&ma=no&datalen=20'
    )
    headers = {'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'}
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        text = resp.read().decode('utf-8')
        if '(' in text:
            text = text[text.index('(') + 1:text.rindex(')')]
        rows = json.loads(text)
        if not rows:
            return None
        # 找 >= 8/4 的第一个交易日收盘作为基准，最后一个交易日收盘作为现价
        base_close = None
        last_close = None
        last_date = None
        for r in rows:
            day = r.get('day', '')[:10]
            try:
                close = float(r['close'])
            except (KeyError, TypeError, ValueError):
                continue
            if close <= 0:
                continue
            if last_date is None or day > last_date:
                last_date = day
                last_close = close
            if day >= BASE_DATE and base_close is None:
                base_close = close
        if base_close is None or last_close is None or base_close <= 0:
            return None
        gain = (last_close - base_close) / base_close * 100
        return (code, round(gain, 2), round(base_close, 2), round(last_close, 2))
    except Exception:
        return None


def main():
    print('获取股票列表...')
    df = ak.stock_info_a_code_name()
    codes = df['code'].astype(str).tolist()
    names = dict(zip(df['code'].astype(str), df['name'].astype(str)))
    print(f'共 {len(codes)} 只股票，开始计算...')

    results = []
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=25) as ex:
        futures = {ex.submit(fetch_kline, c): c for c in codes}
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            if r:
                results.append(r)
            if done % 500 == 0:
                print(f'  进度 {done}/{len(codes)} ({time.time()-t0:.0f}s, 有效{len(results)})')

    results.sort(key=lambda x: -x[1])

    print(f'\n成功获取 {len(results)} 只股票涨幅\n')
    print('=' * 70)
    print(f'  {BASE_DATE} 至今涨幅排名前100')
    print('=' * 70)
    print(f'{"排名":<4} {"代码":<8} {"名称":<12} {"8/4收盘":>10} {"最新收盘":>10} {"涨幅":>8}')
    print('-' * 70)

    top100 = results[:100]
    for i, (code, gain, base, last) in enumerate(top100):
        name = names.get(code, '?')
        print(f'{i+1:<4} {code:<8} {name:<12} {base:>10.2f} {last:>10.2f} {gain:>+7.2f}%')

    out = []
    for code, gain, base, last in results:
        out.append({'code': code, 'name': names.get(code, ''), 'gain_pct': gain,
                    'base_close': base, 'last_close': last})
    with open('output/rank_0804_0813.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\n完整结果已保存: output/rank_0804_0813.json')


if __name__ == '__main__':
    main()
