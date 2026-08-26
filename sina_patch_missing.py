#!/usr/bin/env python3
"""新浪补缺8/25: getKLineData接口, 240分钟=日K, datalen够覆盖."""
import json, sqlite3, subprocess, sys, time, concurrent.futures

DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
DATE = sys.argv[1] if len(sys.argv) > 1 else '2026-08-25'

def sina_symbol(code):
    return ('sh' if code[0] in '56' else 'sz') + code

conn = sqlite3.connect(DB, timeout=60)
missing = [r[0] for r in conn.execute(
    "SELECT s.symbol FROM stock_basics s WHERE s.symbol NOT LIKE '%.%' AND s.symbol NOT IN "
    "(SELECT DISTINCT symbol FROM stock_daily WHERE date=?)", (DATE,))]
print(f"待补{DATE}: {len(missing)}只", flush=True)

done = fail = 0
buf = []
def work(code):
    url = f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol={sina_symbol(code)}&scale=240&ma=no&datalen=10"
    try:
        out = subprocess.run(['curl', '-s', '--noproxy', '*', '-m', '12', url], capture_output=True, text=True, timeout=15).stdout
        arr = json.loads(out)
        for k in arr:
            if k['day'] == DATE:
                return code, k
    except Exception:
        pass
    return code, None

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(work, c): c for c in missing}
    for i, fu in enumerate(concurrent.futures.as_completed(futs)):
        code_, k = fu.result()
        if k:
            o,h,l,c = float(k['open']), float(k['high']), float(k['low']), float(k['close'])
            v = float(k.get('volume', 0) or 0)
            buf.append((code_, DATE, o,h,l,c, v, 0, c,o,h,l))
            done += 1
        else:
            fail += 1
        if len(buf) >= 200:
            conn.executemany("INSERT OR REPLACE INTO stock_daily (symbol,date,open,high,low,close,volume,amount,close_qfq,open_qfq,high_qfq,low_qfq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buf)
            buf=[]; conn.commit()
            print(f"  进度{done+fail}/{len(missing)} 成功{done}", flush=True)
if buf:
    conn.executemany("INSERT OR REPLACE INTO stock_daily (symbol,date,open,high,low,close,volume,amount,close_qfq,open_qfq,high_qfq,low_qfq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buf)
conn.commit(); conn.close()
print(f"完成: 成功{done} 失败{fail}(含停牌/退市/北交所)", flush=True)
