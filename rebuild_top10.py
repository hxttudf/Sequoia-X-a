#!/usr/bin/env python3
"""从 strategy_picks 重建最近5个交易日的 daily_top10 双榜单（主板10+非主板10）

防未来函数：
- 信号：仅用该日 strategy_picks（当天产生的信号）
- 权重：用 strategy_stats 中 date <= target_date 的最新统计
  （strategy_stats 是基于过去 N 天滚动窗口回算的，不引入未来看不到的数据）
- 收益率：用 close_qfq（前复权），避免除权跳变
"""
import sqlite3
from collections import defaultdict
from datetime import date

DB_PATH = "data/sequoia_v2.db"
MAIN_BOARD_PREFIX = ("600", "601", "603", "000", "001", "002", "003")
TARGETS = ["2026-06-25", "2026-06-24", "2026-06-23", "2026-06-22", "2026-06-18"]

def _migrate():
    conn = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_top10)").fetchall()]
    if "category" not in cols:
        conn.execute("ALTER TABLE daily_top10 ADD COLUMN category TEXT")
    conn.commit(); conn.close()

def get_name_map(conn, symbols):
    """从 stock_basics 取最新 name 映射"""
    if not symbols:
        return {}
    ph = ",".join("?" * len(symbols))
    rows = conn.execute(
        f"SELECT symbol, name FROM stock_basics WHERE symbol IN ({ph}) "
        "GROUP BY symbol HAVING MAX(date)", symbols).fetchall()
    return {r[0]: r[1] for r in rows if r[1] and r[1] != r[0]}

def get_weights(conn, target_date):
    """取截止 target_date 的最新策略统计计算加权权重"""
    rows = conn.execute(
        "SELECT strategy, win_rate, avg_return, total_picks, "
        "win_rate_3d, avg_return_3d, win_rate_5d, avg_return_5d, win_rate_10d, avg_return_10d "
        "FROM strategy_stats WHERE lookback_days=10 AND date <= ? "
        "ORDER BY date DESC", (target_date,)).fetchall()
    if not rows:
        return {}
    seen = set(); weights = {}
    for row in rows:
        sname = row[0]
        if sname in seen:
            continue
        seen.add(sname)
        def dim_score(wr, ar):
            wr = wr or 0; ar = ar or 0
            if wr > 0 and ar > 0: return wr * (1 + ar / 100)
            elif wr > 0: return wr * 0.5
            else: return wr * 0.1
        dims = {"1d": (row[1], row[2]), "3d": (row[4], row[5]),
                "5d": (row[6], row[7]), "10d": (row[8], row[9])}
        tw = {"1d": 0.40, "3d": 0.30, "5d": 0.20, "10d": 0.10}
        scores = {}; active = {}
        for d, (wv, av) in dims.items():
            if wv is not None or av is not None:
                scores[d] = dim_score(wv, av); active[d] = tw[d]
        if not active:
            raw = 0.01
        else:
            tws = sum(active.values())
            raw = sum((active[d] / tws) * scores[d] for d in active)
        cnt = row[3] or 0
        if cnt < 5: raw *= cnt / 5
        weights[sname] = max(raw, 0.01)
    total = sum(weights.values())
    return {k: round(v / total, 3) for k, v in weights.items()} if total > 0 else {}

def rebuild():
    _migrate()
    conn = sqlite3.connect(DB_PATH)
    rebuilt = 0
    for target_date in TARGETS:
        conn.execute("DELETE FROM daily_top10 WHERE date=?", (target_date,))
        picks = conn.execute(
            "SELECT strategy, symbol FROM strategy_picks WHERE date=?",
            (target_date,)).fetchall()
        if not picks:
            print(f"  {target_date}: 无信号，跳过"); continue
        all_syms = list(set(s for _, s in picks))
        names = get_name_map(conn, all_syms)
        weights = get_weights(conn, target_date)
        scores = defaultdict(float)
        strat_set = defaultdict(set)
        for sname, sym in picks:
            w = weights.get(sname, 0.05)
            if w > scores[sym]: scores[sym] = w
            strat_set[sym].add(sname.replace("Strategy", ""))
        # 共振加分
        for sym in list(scores.keys()):
            extra = len(strat_set[sym]) - 1
            if extra > 0:
                scores[sym] = round(scores[sym] * (1 + 0.15 * extra), 4)
        ranked = sorted(scores.items(), key=lambda x: (-x[1], -len(strat_set[x[0]])))
        is_main = lambda s: s.startswith(MAIN_BOARD_PREFIX)
        CATEGORIES = [("overall", ranked),
                      ("mainboard", [(s, sc) for s, sc in ranked if is_main(s)])]
        for cat, source in CATEGORIES:
            for rank, (sym, score) in enumerate(source[:10], 1):
                row = conn.execute(
                    "SELECT close_qfq FROM stock_daily WHERE symbol=? AND date=?",
                    (sym, target_date)).fetchone()
                cp = row[0] if row else None
                nm = names.get(sym, sym)
                conn.execute(
                    "INSERT INTO daily_top10 (date,category,rank,symbol,name,score,strategies,close_price) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (target_date, cat, rank, sym, nm, round(score, 3),
                     ",".join(sorted(strat_set[sym])), cp))
                rebuilt += 1
        print(f"  {target_date}: overall={[s for s,_ in ranked[:10]]} | mainboard={[s for s,_ in ranked[:10] if is_main(s)]}")
    conn.commit()
    print(f"重建完成: {rebuilt} 条")
    conn.close()

if __name__ == "__main__":
    rebuild()
