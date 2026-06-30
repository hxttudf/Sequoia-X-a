#!/usr/bin/env python3
import sqlite3
from datetime import date

DB_PATH = "data/sequoia_v2.db"
conn = sqlite3.connect(DB_PATH)

# 1. name 修复检查
print("=== name 修复检查 ===")
bad = conn.execute("SELECT COUNT(*) FROM daily_top10 WHERE name=symbol").fetchone()[0]
total = conn.execute("SELECT COUNT(*) FROM daily_top10").fetchone()[0]
print(f"name==symbol: {bad}/{total}")
if bad:
    for r in conn.execute("SELECT date,symbol,name FROM daily_top10 WHERE name=symbol LIMIT 10").fetchall():
        print(f"  {r[0]} {r[1]} -> {r[2]}")

# 2. 每日每category数量
print("\n=== 每日数量 ===")
for r in conn.execute("SELECT date,category,COUNT(*) FROM daily_top10 GROUP BY date,category ORDER BY date DESC,category").fetchall():
    print(f"  {r[0]} [{r[1]}]: {r[2]} 只")

# 3. 回填收益率
today = date.today().strftime("%Y-%m-%d")
rows = conn.execute("SELECT id,date,symbol FROM daily_top10 WHERE ret_1d IS NULL AND date < ?", (today,)).fetchall()
print(f"\n需回填: {len(rows)} 条")
updated = 0
for row_id, pick_date, sym in rows:
    base = conn.execute("SELECT close_qfq FROM stock_daily WHERE symbol=? AND date=?", (sym, pick_date)).fetchone()
    if not base or not base[0]:
        continue
    bp = base[0]
    future = conn.execute("SELECT close_qfq FROM stock_daily WHERE symbol=? AND date > ? ORDER BY date", (sym, pick_date)).fetchall()
    if not future:
        continue
    rets = {}
    for off, col in [(1, "ret_1d"), (3, "ret_3d"), (5, "ret_5d"), (10, "ret_10d")]:
        if off <= len(future):
            rets[col] = round((future[off-1][0] / bp - 1) * 100, 2)
    if rets:
        sets = ", ".join(f"{k}=?" for k in rets)
        conn.execute(f"UPDATE daily_top10 SET {sets} WHERE id=?", list(rets.values()) + [row_id])
        updated += 1
conn.commit()
print(f"回填完成: {updated} 条")

# 4. 验证历史表现
print("\n=== 全市场 Top10 历史表现 ===")
print(f"{'日期':<12} {'数':>3} {'T+1':>7} {'T+3':>7} {'T+5':>7} {'T+10':>7}")
print("-" * 50)
for r in conn.execute(
    "SELECT date,AVG(ret_1d),AVG(ret_3d),AVG(ret_5d),AVG(ret_10d),COUNT(*) "
    "FROM daily_top10 WHERE ret_1d IS NOT NULL AND category='overall' GROUP BY date ORDER BY date DESC"
).fetchall():
    d1 = r[1] or 0; d3 = r[2] or 0; d5 = r[3] or 0; d10 = r[4] or 0; cnt = r[5] or 0
    print(f"{r[0]:<12} {cnt:>3} {d1:>+6.2f}% {d3:>+6.2f}% {d5:>+6.2f}% {d10:>+6.2f}%")

print("\n=== 主板 Top10 历史表现 ===")
print(f"{'日期':<12} {'数':>3} {'T+1':>7} {'T+3':>7} {'T+5':>7} {'T+10':>7}")
print("-" * 50)
for r in conn.execute(
    "SELECT date,AVG(ret_1d),AVG(ret_3d),AVG(ret_5d),AVG(ret_10d),COUNT(*) "
    "FROM daily_top10 WHERE ret_1d IS NOT NULL AND category='mainboard' GROUP BY date ORDER BY date DESC"
).fetchall():
    d1 = r[1] or 0; d3 = r[2] or 0; d5 = r[3] or 0; d10 = r[4] or 0; cnt = r[5] or 0
    print(f"{r[0]:<12} {cnt:>3} {d1:>+6.2f}% {d3:>+6.2f}% {d5:>+6.2f}% {d10:>+6.2f}%")

# 5. 5天明细
print("\n=== 5天明细 ===")
for d in ["2026-06-25", "2026-06-24", "2026-06-23", "2026-06-22", "2026-06-18"]:
    print(f"\n--- {d} ---")
    for cat, label in [("overall", "全市场"), ("mainboard", "主板")]:
        rows = conn.execute(
            "SELECT rank,symbol,name,score,strategies FROM daily_top10 "
            "WHERE date=? AND category=? ORDER BY rank", (d, cat)).fetchall()
        print(f"  [{label}] ({len(rows)}只)")
        for r in rows:
            print(f"    {r[0]:>2}  {r[1]:<8}{r[2]:<8}{r[3]:.3f}  {r[4]}")

conn.close()