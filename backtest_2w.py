#!/usr/bin/env python3
"""回测：单日顺序版 — T日选股 → T+1收益"""
import sys, sqlite3
sys.path.insert(0, "/home/ubuntu/Sequoia-X-a")

import pandas as pd
from collections import defaultdict
import logging
logging.disable(logging.CRITICAL)

from sequoia_x.data.engine import DataEngine
from sequoia_x.core.config import Settings
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
from sequoia_x.strategy.private_placement import PrivatePlacementStrategy

DB_PATH = "data/sequoia_v2.db"

# 加载
conn = sqlite3.connect(DB_PATH)
df_all = pd.read_sql("SELECT symbol, date, close FROM stock_daily ORDER BY symbol, date", conn)
all_dates = sorted(df_all["date"].unique().tolist())
conn.close()

# 回测最近3天
test_days = all_dates[-5:-2]  # 3天
print(f"回测: {test_days[0]} ~ {test_days[-1]} ({len(test_days)}天 T+1)\n")
strategies = [
    ("MaVolume", MaVolumeStrategy),
    ("TurtleTrade", TurtleTradeStrategy),
    ("HighTightFlag", HighTightFlagStrategy),
    ("LimitUpShakeout", LimitUpShakeoutStrategy),
    ("UptrendLimitDown", UptrendLimitDownStrategy),
    ("RPS", RpsBreakoutStrategy),
]

agg = defaultdict(lambda: {"picks": 0, "wins": 0, "total_ret": 0.0})
daily_picks = defaultdict(lambda: defaultdict(set))

import time
t0 = time.time()

for test_day in test_days:
    next_day = all_dates[all_dates.index(test_day) + 1]
    print(f"--- {test_day} (→{next_day}) ---")
    
    conn = sqlite3.connect(DB_PATH)
    
    for sname, SCls in strategies:
        settings = Settings()
        engine = DataEngine(settings)
        
        # Filter by date
        original = engine.get_ohlcv
        def make_filtered(td):
            def f(symbol):
                return pd.read_sql(
                    "SELECT date, open, high, low, close, volume, turnover "
                    "FROM stock_daily WHERE symbol=? AND date<=? ORDER BY date",
                    conn, params=(symbol, td))
            return f
        engine.get_ohlcv = make_filtered(test_day)
        engine.get_local_symbols = lambda td=test_day: [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM stock_daily WHERE date<=?", (td,)).fetchall()]
        
        strategy = SCls(engine=engine, settings=settings)
        try:
            selected = strategy.run()
        except:
            selected = []
        
        wins = 0
        total_ret = 0
        for sym in selected:
            try:
                tc = float(df_all[(df_all["symbol"]==sym)&(df_all["date"]==test_day)]["close"].iloc[0])
                nc = float(df_all[(df_all["symbol"]==sym)&(df_all["date"]==next_day)]["close"].iloc[0])
                ret = (nc/tc-1)*100
                total_ret += ret
                if ret > 0: wins += 1
                daily_picks[test_day][sym].add(sname)
            except:
                continue
        
        if selected:
            wr = wins / len(selected) * 100 if selected else 0
            avg = total_ret / len(selected) if selected else 0
            agg[sname]["picks"] += len(selected)
            agg[sname]["wins"] += wins
            agg[sname]["total_ret"] += total_ret
            print(f"  {sname:<20s}: {len(selected):>3}只 胜率{wr:>5.0f}% 均收益{avg:>+6.2f}%")
        else:
            print(f"  {sname:<20s}:  0只")
    
    conn.close()

print(f"\n{'='*55}")
print(f"汇总 ({len(test_days)}天)")
print(f"{'='*55}")
for sname, _ in strategies:
    r = agg[sname]
    if r["picks"]:
        wr = r["wins"]/r["picks"]*100
        avg = r["total_ret"]/r["picks"]
        print(f"  {sname:<20s}: {r['picks']:>3}次 胜率{wr:>5.0f}% 均收益{avg:>+6.2f}%")
    else:
        print(f"  {sname:<20s}: 无信号")

# 共振
multi = {1:[0,0,0], 2:[0,0,0], 3:[0,0,0]}
for d, stocks in daily_picks.items():
    nd = all_dates[all_dates.index(d)+1]
    for sym, strats in stocks.items():
        n = min(len(strats), 3)
        try:
            tc=float(df_all[(df_all["symbol"]==sym)&(df_all["date"]==d)]["close"].iloc[0])
            nc=float(df_all[(df_all["symbol"]==sym)&(df_all["date"]==nd)]["close"].iloc[0])
            ret=(nc/tc-1)*100
            multi[n][0]+=1
            multi[n][1]+=(1 if ret>0 else 0)
            multi[n][2]+=ret
        except: continue

print(f"\n多策略共振:")
for n in [1,2,3]:
    p,w,tr=multi[n]
    if p: print(f"  {n}策略: {p}次 胜率{w/p*100:.0f}% 均收益{tr/p:+.2f}%")

print(f"\n耗时 {(time.time()-t0):.0f}s")
