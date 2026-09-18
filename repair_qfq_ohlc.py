# -*- coding: utf-8 -*-
"""修复 stock_daily 的 qfq OHLC 不一致 (除权后 close_qfq 更新了, o/h/l_qfq 没更新 → 阴线/阳线错乱)。
正确值可本地推导: qfq_开/高/低 = 原始开/高/低 × (close_qfq / close)。
幂等。先备份 qfq 4列。"""
import sqlite3, time, os
DB = "/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db"
print("db size %.0f MB" % (os.path.getsize(DB)/1e6), flush=True)
c = sqlite3.connect(DB, timeout=180)
c.execute("PRAGMA busy_timeout=180000")
t = time.time()

print("[1/3] 备份 qfq 列 -> stock_daily_qfqbkp ...", flush=True)
c.execute("DROP TABLE IF EXISTS stock_daily_qfqbkp")
c.execute("""CREATE TABLE stock_daily_qfqbkp AS
             SELECT symbol,date,open_qfq,high_qfq,low_qfq,close_qfq FROM stock_daily""")
c.commit()
n = c.execute("SELECT COUNT(*) FROM stock_daily_qfqbkp").fetchone()[0]
print(f"   备份 {n} 行  ({time.time()-t:.0f}s)", flush=True)

print("[2/3] 修复 o/h/l_qfq (仅动不一致的根) ...", flush=True)
cur = c.execute("""UPDATE stock_daily SET
     open_qfq = ROUND(open *close_qfq/close, 3),
     high_qfq = ROUND(high *close_qfq/close, 3),
     low_qfq  = ROUND(low  *close_qfq/close, 3)
   WHERE close > 0 AND close_qfq > 0
     AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL
     AND (open_qfq IS NULL OR high_qfq IS NULL OR low_qfq IS NULL
          OR ABS(open_qfq - open *close_qfq/close) > 0.005
          OR ABS(high_qfq - high *close_qfq/close) > 0.005
          OR ABS(low_qfq  - low  *close_qfq/close) > 0.005)""")
c.commit()
print(f"   修复 {cur.rowcount} 行  ({time.time()-t:.0f}s)", flush=True)

print("[3/3] 验证 ...", flush=True)
bad = c.execute("""SELECT COUNT(*) FROM stock_daily WHERE close_qfq>0 AND date>='2026-08-01'
   AND (high_qfq < MAX(open_qfq,close_qfq)-0.001 OR low_qfq > MIN(open_qfq,close_qfq)+0.001)""").fetchone()[0]
tot = c.execute("SELECT COUNT(*) FROM stock_daily WHERE close_qfq>0 AND date>='2026-08-01'").fetchone()[0]
print(f"   2026-08起非法bar: {bad}/{tot}")
for r in c.execute("SELECT date,open_qfq,high_qfq,low_qfq,close_qfq FROM stock_daily WHERE symbol='600009' AND date>='2026-09-10' AND close_qfq>0 ORDER BY date"):
    print("   600009", r)
c.close()
print("done", flush=True)
