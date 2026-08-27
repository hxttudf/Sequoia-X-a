#!/usr/bin/env python3
"""修复 8/25 + 8/26 新浪补数漏网股票的 volume 单位(股→手 ÷100)
判定: 非ETF + volume > 前置5日中位数*50 + close=close_qfq(新浪不复权源)
只UPDATE命中行, 不动其他数据"""
import sqlite3

DB = "/home/ubuntu/databases/Sequoia选股.db"
DAYS = ["2026-08-25", "2026-08-26"]

conn = sqlite3.connect(DB, timeout=60)
total = 0
for day in DAYS:
    rows = conn.execute(
        f"""
        SELECT s.symbol, s.volume, (
            SELECT AVG(m.volume) FROM (
                SELECT volume FROM stock_daily WHERE symbol=s.symbol AND date>=date(?, '-7 day') AND date< ?
            ) m WHERE m.volume > 0
        )
        FROM stock_daily s
        WHERE s.date = ? AND s.volume > 0
          AND s.symbol NOT GLOB '1[56]*' AND s.symbol NOT GLOB '5[1-8]*'
          AND s.close = s.close_qfq
        """,
        (day, day, day),
    ).fetchall()
    hits = [(sym, v) for sym, v, med in rows if med and v > med * 50]
    print(f"{day}: 候选{len(rows)} 命中{len(hits)}")
    for sym, v in hits:
        conn.execute(
            "UPDATE stock_daily SET volume = volume/100.0 WHERE symbol=? AND date=?",
            (sym, day),
        )
        total += 1
conn.commit()
print(f"共修复 {total} 行")
# 复验
for day in DAYS:
    n = conn.execute(
        f"""SELECT COUNT(*) FROM stock_daily s WHERE s.date=? AND s.volume>0
            AND s.symbol NOT GLOB '1[56]*' AND s.symbol NOT GLOB '5[1-8]*'
            AND s.volume > 10 * (SELECT AVG(m.volume) FROM (SELECT volume FROM stock_daily WHERE symbol=s.symbol AND date>=date(?,'-7 day') AND date<?) m WHERE m.volume>0)
            AND s.close=s.close_qfq""",
        (day, day, day),
    ).fetchone()[0]
    print(f"{day} 修后残留异常: {n}")
conn.close()
