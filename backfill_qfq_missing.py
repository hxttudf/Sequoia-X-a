#!/usr/bin/env python3
"""回填 stock_daily 中 close_qfq 为空的行的前复权收盘价
根因: 次新股/无除权史股票, 腾讯只返回原始 day(无qfqday), 旧backfill只从qfqday取→None
修复: backfill_v2.fetch_kline_tx 已加 day 回退; 本脚本对全库空值重拉回填"""
import sqlite3, sys, time
sys.path.insert(0, "/home/ubuntu/Sequoia-X-a")
import backfill_v2

DB = "/home/ubuntu/databases/Sequoia选股.db"
conn = sqlite3.connect(DB, timeout=60)

# 找所有有 close_qfq 空值的股票
syms = [r[0] for r in conn.execute(
    """SELECT symbol FROM stock_daily
       WHERE close_qfq IS NULL GROUP BY symbol""").fetchall()]
print(f"需回填股票数: {len(syms)}", flush=True)

def market(c):
    return "1" if c.startswith(("6", "9")) else "0"

done = updated = 0
for code in syms:
    try:
        klines = backfill_v2.fetch_kline_tx(code, market(code))
    except Exception as e:
        print(f"  {code} 拉取异常: {e}", flush=True)
        continue
    if not klines:
        # 停牌/无法拉取, 跳过
        continue
    # 构建 date->qfq 映射
    qfq_map = {k["date"]: k.get("close_qfq") for k in klines}
    # 更新该股所有 close_qfq IS NULL 的行
    for d, q in qfq_map.items():
        if q is None:
            continue
        cur = conn.execute(
            "UPDATE stock_daily SET close_qfq=? WHERE symbol=? AND date=? AND close_qfq IS NULL",
            (q, code, d))
        updated += cur.rowcount
    done += 1
    if done % 50 == 0:
        conn.commit()
        print(f"  进度 {done}/{len(syms)} 已更新{updated}", flush=True)
    time.sleep(0.15)  # 控速防腾讯限流

conn.commit()
print(f"完成: 处理{done}只, 共更新{updated}行 close_qfq", flush=True)
conn.close()
