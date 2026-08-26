
#!/usr/bin/env python3
"""腾讯精准补缺: 只拉指定日期缺失的symbol(Wind额度用尽后的兜底)."""
import sqlite3, sys, time, concurrent.futures
sys.path.insert(0, '/home/ubuntu/Sequoia-X-a')
import backfill_v2

DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
DATE = sys.argv[1] if len(sys.argv) > 1 else '2026-08-26'

def market_of(code):
    return '1' if code[0] in '56' else '0'

conn = sqlite3.connect(DB, timeout=60)
missing = [r[0] for r in conn.execute(
    "SELECT s.symbol FROM stock_basics s WHERE s.symbol NOT LIKE '%.%' AND s.symbol NOT IN "
    "(SELECT DISTINCT symbol FROM stock_daily WHERE date=?)", (DATE,))]
print(f"待补{DATE}: {len(missing)}只", flush=True)

done = fail = 0
buf = []
def work(code):
    try:
        ks = backfill_v2.fetch_kline_tx(code, market_of(code))
        for k in ks:
            if k.get('date') == DATE:
                return code, k
    except Exception:
        pass
    return code, None

with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
    futs = {ex.submit(work, c): c for c in missing}
    for fu in concurrent.futures.as_completed(futs):
        code_, k = fu.result()
        if k:
            buf.append((code_, k['date'], k.get('open'), k.get('high'), k.get('low'),
                        k.get('close'), k.get('volume', 0), k.get('amount', 0),
                        k.get('close_qfq'), k.get('open_qfq'), k.get('high_qfq'), k.get('low_qfq')))
            done += 1
        else:
            fail += 1
        if len(buf) >= 200:
            conn.executemany("INSERT OR REPLACE INTO stock_daily (symbol,date,open,high,low,close,volume,amount,close_qfq,open_qfq,high_qfq,low_qfq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buf)
            buf = []
            conn.commit()
            print(f"  进度{done+fail}/{len(missing)} 成功{done}", flush=True)
if buf:
    conn.executemany("INSERT OR REPLACE INTO stock_daily (symbol,date,open,high,low,close,volume,amount,close_qfq,open_qfq,high_qfq,low_qfq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buf)
conn.commit()
conn.close()
print(f"完成: 成功{done} 失败{fail}(含停牌/退市)", flush=True)
