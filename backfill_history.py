#!/usr/bin/env python3
"""回填最近3天选股到 strategy_picks"""
import sys, sqlite3
sys.path.insert(0, "/home/ubuntu/Sequoia-X-a")

import pandas as pd
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
conn = sqlite3.connect(DB_PATH)
all_dates = sorted(set(r[0] for r in conn.execute(
    "SELECT DISTINCT date FROM stock_daily ORDER BY date").fetchall()))

# 回填最近3天（不含今天）
test_days = [d for d in all_dates[-5:-1] if d < "2026-06-16"]

strategies = [
    ("MaVolumeStrategy", MaVolumeStrategy),
    ("TurtleTradeStrategy", TurtleTradeStrategy),
    ("HighTightFlagStrategy", HighTightFlagStrategy),
    ("LimitUpShakeoutStrategy", LimitUpShakeoutStrategy),
    ("UptrendLimitDownStrategy", UptrendLimitDownStrategy),
    ("RpsBreakoutStrategy", RpsBreakoutStrategy),
    ("PrivatePlacementStrategy", PrivatePlacementStrategy),
]

for test_day in test_days:
    print(f"\n--- {test_day} ---")
    settings = Settings()
    engine = DataEngine(settings)
    
    def make_filtered(td, c):
        def f(sym):
            return pd.read_sql(
                "SELECT date, open, high, low, close, volume, turnover "
                "FROM stock_daily WHERE symbol=? AND date<=? ORDER BY date",
                c, params=(sym, td))
        return f
    
    engine.get_ohlcv = make_filtered(test_day, conn)
    engine.get_local_symbols = lambda td=test_day: [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM stock_daily WHERE date<=?", (td,)).fetchall()]
    
    for sname, SCls in strategies:
        s = SCls(engine=engine, settings=settings)
        try:
            picks = s.run()
        except Exception as e:
            print(f"  {sname}: ERROR {e}")
            picks = []
        
        for sym in picks:
            row = conn.execute(
                "SELECT close FROM stock_daily WHERE symbol=? AND date=?",
                (sym, test_day)).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO strategy_picks (date, strategy, symbol, name, close_price) "
                "VALUES (?,?,?,?,?)",
                (test_day, sname, sym, sym, row[0] if row else None))
        
        if picks:
            print(f"  {sname}: {len(picks)}只")

conn.commit()
conn.close()
print("\n回填完成")
