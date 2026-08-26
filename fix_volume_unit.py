import sqlite3, shutil, datetime
DB = '/home/ubuntu/databases/Sequoia选股.db'
# 备份
bak = '/home/ubuntu/Sequoia选股.db.bak_volbefore_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
shutil.copy2(DB, bak)
print("备份:", bak)

conn = sqlite3.connect(DB)
rows = conn.execute("""
  SELECT symbol, date, volume FROM stock_daily
  WHERE date IN ('2026-08-20','2026-08-21','2026-08-24','2026-08-25','2026-08-26')
  AND volume IS NOT NULL AND volume > 0
""").fetchall()
from collections import defaultdict
d = defaultdict(dict)
for s, dt, v in rows: d[s][dt] = v

fixed = 0
for target in ('2026-08-25', '2026-08-26'):
    for s, m in d.items():
        base = [m[x] for x in ('2026-08-20','2026-08-21','2026-08-24') if x in m]
        if not base or target not in m: continue
        med = sorted(base)[len(base)//2]
        if med <= 0: continue
        ratio = m[target]/med
        # 疑似股单位: 放大>20倍 且 ÷100 后落在正常量级(0.3~3.5倍基准)
        if ratio > 20:
            div100 = m[target]/100/med
            if 0.3 < div100 < 3.5:
                newv = round(m[target]/100, 2)
                cur = conn.execute(
                    "UPDATE stock_daily SET volume=? WHERE symbol=? AND date=? AND volume=?",
                    (newv, s, target, m[target]))
                fixed += cur.rowcount
conn.commit()
print(f"修复 {fixed} 行 volume (股→手 ÷100)")
conn.close()
