#!/usr/bin/env python3
"""ETF日线拉取: 东财ETF列表 → 腾讯K线 → stock_basics + stock_daily
ETF数据与股票同库, stockscope/缠论零改动自动支持"""
import sys, json, time, urllib.request, sqlite3
from datetime import datetime, timedelta
sys.path.insert(0, '/home/ubuntu/Sequoia-X-a')
from backfill_v2 import fetch_kline_tx  # noqa: E402

DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
TODAY = datetime.now().strftime('%Y-%m-%d')
UA = {'User-Agent': 'Mozilla/5.0'}

def fetch_etf_list():
    """东财push2delay ETF列表(沪深, 分页) → [(code, name, mktcap)]"""
    out = []
    for pn in range(1, 30):
        url = (f"https://push2delay.eastmoney.com/api/qt/clist/get?pn={pn}&pz=200&po=1&np=1&fltt=2&invt=2"
               f"&fid=f3&fs=b:MK0021,b:MK0022,b:MK0023,b:MK0024&fields=f12,f14,f20")
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15).read())
        except Exception as e:
            print(f'列表页{pn}失败: {e}')
            break
        diff = d.get('data', {}).get('diff') or []
        if not diff:
            break
        for x in diff:
            code, name = str(x.get('f12', '')), x.get('f14', '')
            if code and name:
                out.append((code, name, x.get('f20')))
        if len(out) >= d.get('data', {}).get('total', 0):
            break
        time.sleep(0.3)
    return out

def main():
    conn = sqlite3.connect(DB)
    etfs = fetch_etf_list()
    print(f'ETF列表: {len(etfs)}只')
    if not etfs:
        conn.close()
        return

    conn.execute("""CREATE TABLE IF NOT EXISTS stock_daily (
        symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
        volume REAL, amount REAL, close_qfq REAL,
        PRIMARY KEY (symbol, date))""")

    done = fails = 0
    for code, name, mktcap in etfs:
        # 复用A股拉取链路(腾讯fetch_kline_tx): market='1'沪 '0'深
        market = '1' if code[0] == '5' else '0'
        try:
            klines = fetch_kline_tx(code, market)
        except Exception as e:
            fails += 1
            print(f'  {code} {name} 拉取失败: {e}')
            continue
        if not klines:
            fails += 1
            continue
        # 写入全部K线(INSERT OR REPLACE, 幂等)
        batch = []
        for k in klines:
            batch.append((code, k['date'], k['open'], k['high'], k['low'], k['close'],
                          k['volume'], round(k['volume'] * k['close'], 2), k.get('close_qfq')))
        conn.executemany(
            'INSERT OR REPLACE INTO stock_daily (symbol, date, open, high, low, close, volume, amount, close_qfq) '
            'VALUES (?,?,?,?,?,?,?,?,?)', batch)
        # basics(最新价/市值用东财f20, 万元)
        last = klines[-1]
        conn.execute(
            "INSERT OR REPLACE INTO stock_basics (symbol, date, name, close, mktcap, nmc, updated_at) "
            "VALUES (?,?,?,?,?,?,datetime('now','localtime'))",
            (code, TODAY, name, last['close'], (mktcap or 0) / 10000, (mktcap or 0) / 10000))
        done += 1
    conn.commit()
    conn.close()
    print(f'ETF完成: {done}只, 失败{fails}')

if __name__ == '__main__':
    main()
