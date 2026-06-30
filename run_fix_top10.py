#!/usr/bin/env python3
"""Run fixed pipeline: backfill_returns(qfq) + stats + top10 for today"""
import sys, os
os.chdir("/home/ubuntu/Sequoia-X-a")
sys.path.insert(0, ".")

import sqlite3
from datetime import date
from collections import defaultdict

from daily_picks import (
    backfill_returns, compute_stats, recompute_historical_stats,
    compute_weights, pick_top10, backfill_top10_returns, log
)

DB = "data/sequoia_v2.db"
today = date.today().isoformat()

print(f"\n{'='*60}")
print(f"  Sequoia-X 修复流水线")
print(f"  日期: {today}")
print(f"{'='*60}")

# 1. backfill_returns - recalculate all T+1/3/5/10 with close_qfq
print(f"\n[1/6] Backfill returns (close_qfq)...")
backfill_returns()

# 2. compute_stats
print(f"\n[2/6] Compute strategy stats (10d rolling)...")
compute_stats(lookback=10)

# 3. recompute historical stats (fix the 4 missing strategies)
print(f"\n[3/6] Recompute historical stats (all 11 strategies)...")
recompute_historical_stats(lookback=10)

# 4. Reconstruct today's strategy data from strategy_picks
print(f"\n[4/6] Reconstruct strategy data from DB...")
conn = sqlite3.connect(DB)
rows = conn.execute(
    "SELECT strategy, symbol, name FROM strategy_picks WHERE date=?", (today,)
).fetchall()
conn.close()

strategies = {}
for sname, sym, name in rows:
    strategies.setdefault(sname, {"symbols": [], "names": []})
    strategies[sname]["symbols"].append(sym)
    strategies[sname]["names"].append(name if name else sym)

data = {
    "date": today,
    "strategies": [
        {"strategy": sname, "symbols": v["symbols"], "names": v["names"]}
        for sname, v in strategies.items()
    ]
}
total_picks = sum(len(s["symbols"]) for s in data["strategies"])
print(f"  {len(data['strategies'])} strategies, {total_picks} total picks")

# 5. compute weights and pick top10
print(f"\n[5/6] Compute weights & pick top10...")
weights, _ = compute_weights()
print(f"  Strategies with weights: {len(weights)}")
for k, v in sorted(weights.items(), key=lambda x: -x[1])[:5]:
    print(f"    {k.replace('Strategy',''):20s} {v:.3f}")
if len(weights) > 5:
    print(f"    ... and {len(weights)-5} more")

results = pick_top10(data, weights)

# 6. backfill top10 returns
print(f"\n[6/6] Backfill top10 returns...")
backfill_top10_returns()

# Show results
print(f"\n{'='*60}")
print(f"  今日榜单 ({today})")
print(f"{'='*60}")

conn = sqlite3.connect(DB)
for cat, label in [("overall", "全市场 Top10"), ("mainboard", "主板 Top10")]:
    print(f"\n  [{label}]")
    rows = conn.execute(
        "SELECT rank,symbol,name,score,strategies FROM daily_top10 WHERE date=? AND category=? ORDER BY rank",
        (today, cat)
    ).fetchall()
    for r in rows:
        print(f"  #{r[0]} {r[1]} {r[2]}  {r[3]:.3f}  [{r[4]}]")

n = conn.execute("SELECT COUNT(*) FROM daily_top10").fetchone()[0]
print(f"\n  daily_top10 总计: {n} rows")
conn.close()

print(f"\n{'='*60}")
print(f"  DONE")
print(f"{'='*60}")
