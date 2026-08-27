#!/usr/bin/env python3
"""修复 ETF 8/25-8/27 新浪补数 volume 漏÷100 + 8/25 amount 异常
判定: ETF(15/51-58开头) + volume > 前5日中位*30 (ETF阈值放宽到30倍, 因ETF正常波动大)
只UPDATE命中行; amount仅修8/25 volume命中行的同日amount/100? 不 — amount异常量级
需独立判定: amount > 前5日中位*30 才÷100"""
import sqlite3

DB = "/home/ubuntu/databases/Sequoia选股.db"
DAYS = ["2026-08-25", "2026-08-26", "2026-08-27"]

conn = sqlite3.connect(DB, timeout=60)
vol_total, amt_total = 0, 0
for day in DAYS:
    # volume 修复
    rows = conn.execute(
        """
        SELECT s.symbol, s.volume, s.close, s.close_qfq, (
            SELECT AVG(m.volume) FROM (
                SELECT volume FROM stock_daily WHERE symbol=s.symbol AND date>=date(?, '-7 day') AND date<? AND volume>0
            ) m
        )
        FROM stock_daily s
        WHERE s.date = ? AND s.volume > 0
          AND (s.symbol GLOB '1[56]*' OR s.symbol GLOB '5[1-8]*')
          AND s.close = s.close_qfq
        """,
        (day, day, day),
    ).fetchall()
    hits = [(sym, v) for sym, v, c, cq, med in rows if med and v > med * 30]
    print(f"{day} ETF volume 候选{len(rows)} 命中{len(hits)}")
    for sym, v in hits:
        conn.execute(
            "UPDATE stock_daily SET volume = volume/100.0 WHERE symbol=? AND date=?",
            (sym, day),
        )
        vol_total += 1
    # amount 修复 (同判定逻辑, 独立扫)
    rows2 = conn.execute(
        """
        SELECT s.symbol, s.amount, (
            SELECT AVG(m.amount) FROM (
                SELECT amount FROM stock_daily WHERE symbol=s.symbol AND date>=date(?, '-7 day') AND date<? AND amount>0
            ) m
        )
        FROM stock_daily s
        WHERE s.date = ? AND s.amount > 0
          AND (s.symbol GLOB '1[56]*' OR s.symbol GLOB '5[1-8]*')
          AND s.close = s.close_qfq
        """,
        (day, day, day),
    ).fetchall()
    hits2 = [(sym, a) for sym, a, med in rows2 if med and a > med * 30]
    print(f"{day} ETF amount 候选{len(rows2)} 命中{len(hits2)}")
    for sym, a in hits2:
        conn.execute(
            "UPDATE stock_daily SET amount = amount/100.0 WHERE symbol=? AND date=?",
            (sym, day),
        )
        amt_total += 1
conn.commit()
print(f"共修 volume {vol_total} 行, amount {amt_total} 行")
# 复验 159329
rows = conn.execute(
    "SELECT date, close, volume, amount FROM stock_daily WHERE symbol='159329' AND date>='2026-08-24' ORDER BY date"
).fetchall()
for r in rows:
    print(r)
conn.close()
