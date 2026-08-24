#!/usr/bin/env python3
"""ETF前复权回填: 腾讯fqkline qfq(前复权) → stock_daily的close_qfq/open_qfq/high_qfq/low_qfq
腾讯接口对ETF默认不给复权(fetch_kline_tx返回None), 用闭市价兜底导致无真前复权;
此脚本用腾讯正规前复权接口重拉全部ETF, 补全qfq四列."""
import json, urllib.request, sqlite3, sys, time
from datetime import datetime

DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'

def fqkline(code, market, start='2018-01-01', n=2000):
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={market}{code},day,{start},2050-01-01,{n},qfq")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
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
    done = fails = rows = 0
    t0 = time.time()
    for sym in etfs:
        market = 'sh' if sym[0] == '5' else 'sz'
        k = fqkline(sym, market)
        if not k:
            fails += 1
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(etfs)} 失败{fails} 更新{rows}行 {time.time()-t0:.0f}s", flush=True)
            continue
        # k: [[date, open, close, high, low, vol], ...] — 只补qfq四列, 不动原始OHLC/涨跌幅
        c.executemany(
            "UPDATE stock_daily SET close_qfq=?, open_qfq=?, high_qfq=?, low_qfq=?, update_time=? "
            "WHERE symbol=? AND date=?",
            [(float(r[2]), float(r[1]), float(r[3]), float(r[4]),
              datetime.now().strftime('%Y-%m-%d %H:%M:%S'), sym, r[0])
             for r in k])
        rows += len(k)
        done += 1
        if done % 200 == 0:
            print(f"  {done}/{len(etfs)} 失败{fails} 更新{rows}行 {time.time()-t0:.0f}s", flush=True)
    c.commit()
    c.close()
    print(f"✅ ETF前复权回填完成: {len(etfs)}只, 失败{fails}, 更新{rows}行, 耗时{time.time()-t0:.0f}s", flush=True)

if __name__ == '__main__':
    main()
