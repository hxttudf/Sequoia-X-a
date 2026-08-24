#!/usr/bin/env python3
"""ETF数据全面修复:
1. 真实OHLC(open/high/low/close/volume): 用fetch_kline_tx(code, 'sh'/'sz') — 修复market='1'/'0'导致的错误价格(如510300写成2.04)
2. 前复权qfq四列: 用腾讯fqkline qfq — 补全(之前close_qfq为NULL用close兜底, 非真复权)
一次循环两请求, 后台运行."""
import sys, sqlite3, json, urllib.request, time
from datetime import datetime
sys.path.insert(0, '/home/ubuntu/Sequoia-X-a')
from backfill_v2 import fetch_kline_tx

DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'

def fqkline_qfq(code, market, start='2018-01-01', n=2000):
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={market}{code},day,{start},2050-01-01,{n},qfq")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        d = json.load(urllib.request.urlopen(req, timeout=15))
        data = d.get('data', {}).get(market + code, {})
        return data.get('qfqday') or data.get('day') or []
    except Exception:
        return []

def main():
    c = sqlite3.connect(DB, timeout=60)
    etfs = [r[0] for r in c.execute(
        "SELECT DISTINCT symbol FROM stock_daily WHERE symbol LIKE '5%' OR symbol LIKE '15%' OR symbol LIKE '16%'").fetchall()]
    print(f"ETF总数: {len(etfs)}", flush=True)
    done = real_fail = qfq_fail = 0
    real_rows = qfq_rows = 0
    t0 = time.time()
    for sym in etfs:
        market = 'sh' if sym[0] == '5' else 'sz'
        # 1) 真实OHLC
        try:
            k = fetch_kline_tx(sym, market)
            if k:
                c.executemany(
                    "UPDATE stock_daily SET open=?, high=?, low=?, close=?, volume=? WHERE symbol=? AND date=?",
                    [(r['open'], r['high'], r['low'], r['close'], r['volume'], sym, r['date']) for r in k])
                real_rows += len(k)
        except Exception:
            real_fail += 1
        # 2) 前复权qfq四列
        q = fqkline_qfq(sym, market)
        if q:
            c.executemany(
                "UPDATE stock_daily SET close_qfq=?, open_qfq=?, high_qfq=?, low_qfq=?, update_time=? WHERE symbol=? AND date=?",
                [(float(r[2]), float(r[1]), float(r[3]), float(r[4]),
                  datetime.now().strftime('%Y-%m-%d %H:%M:%S'), sym, r[0]) for r in q])
            qfq_rows += len(q)
        else:
            qfq_fail += 1
        done += 1
        if done % 200 == 0:
            print(f"  {done}/{len(etfs)} 真实{real_fail}失败 前复权{qfq_fail}失败 真实{real_rows}行 qfq{qfq_rows}行 {time.time()-t0:.0f}s", flush=True)
    c.commit(); c.close()
    print(f"✅ ETF修复完成: {len(etfs)}只, 真实失败{real_fail}, 前复权失败{qfq_fail}, 真实{real_rows}行, qfq{qfq_rows}行, {time.time()-t0:.0f}s", flush=True)

if __name__ == '__main__':
    main()
