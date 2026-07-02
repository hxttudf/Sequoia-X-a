#!/usr/bin/env python3
"""Sequoia-X 每日选股 + 加权 Top10 + 回测 + 策略分析"""
import sys, json, sqlite3, subprocess, time
from datetime import date, timedelta
from collections import defaultdict

DB_PATH = "data/sequoia_v2.db"
LOG_FILE = "logs/daily_picks.log"

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def run_strategies():
    log("运行策略...")
    r = subprocess.run(
        [".venv-host/bin/python3", "main.py", "--json", "--no-sync"],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        log(f"策略运行失败: {r.stderr[:200]}")
        return None
    lines = r.stdout.strip().split("\n")
    json_start = json_end = None
    depth = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if json_start is None and stripped == "{":
            json_start = i
        if json_start is not None:
            depth += stripped.count("{") - stripped.count("}")
            if depth == 0 and i > json_start:
                json_end = i + 1
                break
    if json_start is None or json_end is None:
        log("未找到 JSON 输出")
        return None
    return json.loads("\n".join(lines[json_start:json_end]))

def _get_name_map(conn, symbols):
    """从 stock_basics 取最新 name 映射，避免依赖外部 API"""
    if not symbols:
        return {}
    ph = ",".join("?" * len(symbols))
    rows = conn.execute(
        f"SELECT symbol, name FROM stock_basics WHERE symbol IN ({ph}) "
        "GROUP BY symbol HAVING MAX(date)", symbols).fetchall()
    return {r[0]: r[1] for r in rows if r[1] and r[1] != r[0]}

def save_picks(data):
    conn = sqlite3.connect(DB_PATH)
    run_date = data["date"]
    all_syms = set()
    for s in data["strategies"]:
        all_syms.update(s["symbols"])
    name_map = _get_name_map(conn, list(all_syms))
    inserted = 0
    for s in data["strategies"]:
        sname = s["strategy"]
        for sym in s["symbols"]:
            name = name_map.get(sym, sym)
            row = conn.execute("SELECT close_qfq FROM stock_daily WHERE symbol=? AND date=?", (sym, run_date)).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO strategy_picks (date, strategy, symbol, name, close_price) VALUES (?,?,?,?,?)",
                (run_date, sname, sym, name, row[0] if row else None))
            inserted += 1
    conn.commit()
    conn.close()
    log(f"保存 {inserted} 条选股 (日期: {run_date})")

def backfill_returns():
    conn = sqlite3.connect(DB_PATH)
    today = date.today().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT id, date, symbol, close_price FROM strategy_picks WHERE (next_close IS NULL OR ret_3d IS NULL OR ret_5d IS NULL OR ret_10d IS NULL) AND date < ?",
        (today,)).fetchall()
    if not rows: conn.close(); return
    updated = 0
    for row_id, pick_date, sym, base_price in rows:
        # Get future closes — 用前复权 close_qfq 避免除权日跳跃
        future = conn.execute(
            "SELECT close_qfq FROM stock_daily WHERE symbol=? AND date > ? ORDER BY date", (sym, pick_date)).fetchall()
        if not future: continue

        # base price 也用 close_qfq（不依赖存储的 close_price，那可能是后复权）
        bp_row = conn.execute(
            "SELECT close_qfq FROM stock_daily WHERE symbol=? AND date=?", (sym, pick_date)).fetchone()
        bp = bp_row[0] if bp_row else None
        if not bp: continue

        # T+1 (only if not already filled)
        if conn.execute("SELECT next_close FROM strategy_picks WHERE id=?", (row_id,)).fetchone()[0] is None:
            ret_1d = round((future[0][0] / bp - 1) * 100, 2)
            conn.execute("UPDATE strategy_picks SET next_close=?, next_return=? WHERE id=?",
                         (future[0][0], ret_1d, row_id))

        # T+3/5/10
        updates = {}
        for offset, col in [(3, "ret_3d"), (5, "ret_5d"), (10, "ret_10d")]:
            if offset <= len(future):
                updates[col] = round((future[offset - 1][0] / bp - 1) * 100, 2)
        if updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE strategy_picks SET {sets} WHERE id=?", list(updates.values()) + [row_id])

        updated += 1
    conn.commit()
    conn.close()
    if updated: log(f"回填 {updated} 条收益率 (T+1/3/5/10)")

def compute_stats(lookback=10):
    conn = sqlite3.connect(DB_PATH)
    today = date.today().strftime("%Y-%m-%d")
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM strategy_picks ORDER BY date DESC LIMIT ?", (lookback,)).fetchall()]
    if not dates: conn.close(); return
    date_placeholders = ",".join("?" * len(dates))

    for sname in ["MaVolumeStrategy","TurtleTradeStrategy","HighTightFlagStrategy",
                  "LimitUpShakeoutStrategy","UptrendLimitDownStrategy","RpsBreakoutStrategy",
                  "PrivatePlacementStrategy","FiftyTwoWeekHighStrategy","LimitUpPullbackStrategy",
                  "MABullishMACDStrategy","BollingerSqueezeStrategy"]:
        # T+1 stats
        stats_1d = conn.execute(
            f"SELECT COUNT(*), SUM(CASE WHEN next_return>0 THEN 1 ELSE 0 END), AVG(next_return) "
            f"FROM strategy_picks WHERE strategy=? AND next_return IS NOT NULL "
            f"AND next_return BETWEEN -20 AND 20 AND date IN ({date_placeholders})",
            [sname] + dates).fetchone()

        if not stats_1d or not stats_1d[0]:
            continue

        t, w, ar = stats_1d
        wr = round(w / t * 100, 1) if t else 0
        ar = round(ar, 2) if ar else 0
        daily_avg = conn.execute(
            f"SELECT AVG(daily_avg) FROM ("
            f"SELECT AVG(next_return) as daily_avg FROM strategy_picks "
            f"WHERE strategy=? AND next_return IS NOT NULL "
            f"AND next_return BETWEEN -20 AND 20 AND date IN ({date_placeholders}) "
            f"GROUP BY date)",
            [sname] + dates).fetchone()[0]
        total_ret_1d = round(((1 + daily_avg / 100) ** len(dates) - 1) * 100, 2) if daily_avg else 0

        # Multi-day stats (3d/5d/10d)
        multi = {}
        for day, col in [("3d", "ret_3d"), ("5d", "ret_5d"), ("10d", "ret_10d")]:
            md = conn.execute(
                f"SELECT COUNT(*), SUM(CASE WHEN {col}>0 THEN 1 ELSE 0 END), AVG({col}) "
                f"FROM strategy_picks WHERE strategy=? AND {col} IS NOT NULL "
                f"AND {col} BETWEEN -50 AND 50 AND date IN ({date_placeholders})",
                [sname] + dates).fetchone()
            if md and md[0] and md[0] > 0:
                mt, mw, mar = md
                mwr = round(mw / mt * 100, 1) if mt else 0
                mar = round(mar, 2) if mar else 0
                mdaily = conn.execute(
                    f"SELECT AVG(daily_avg) FROM ("
                    f"SELECT AVG({col}) as daily_avg FROM strategy_picks "
                    f"WHERE strategy=? AND {col} IS NOT NULL "
                    f"AND {col} BETWEEN -50 AND 50 AND date IN ({date_placeholders}) "
                    f"GROUP BY date)",
                    [sname] + dates).fetchone()[0]
                mtotal = round(((1 + mdaily / 100) ** len(dates) - 1) * 100, 2) if mdaily else 0
                multi[f"win_rate_{day}"] = mwr
                multi[f"avg_return_{day}"] = mar
                multi[f"total_return_{day}"] = mtotal
            else:
                multi[f"win_rate_{day}"] = 0
                multi[f"avg_return_{day}"] = 0
                multi[f"total_return_{day}"] = 0

        columns = "date,strategy,lookback_days,total_picks,win_count,win_rate,avg_return,total_return"
        values = "?,?,?,?,?,?,?,?"
        params = [today, sname, lookback, t, w, wr, ar, total_ret_1d]

        for day in ["3d", "5d", "10d"]:
            columns += f",win_rate_{day},avg_return_{day},total_return_{day}"
            values += ",?,?,?"
            params += [multi[f"win_rate_{day}"], multi[f"avg_return_{day}"], multi[f"total_return_{day}"]]

        conn.execute(
            f"INSERT OR REPLACE INTO strategy_stats ({columns}) VALUES ({values})", params)

    conn.commit()
    conn.close()
    log(f"更新策略统计 (回看{lookback}天)")

def recompute_historical_stats(lookback=10):
    """重算所有历史日期的 strategy_stats，按日期分别以 lookback 天滚动窗口计算"""
    conn = sqlite3.connect(DB_PATH)
    today = date.today().strftime("%Y-%m-%d")

    # 获取所有历史日期（排除今天）
    all_dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM strategy_picks WHERE date < ? ORDER BY date", (today,)).fetchall()]

    if not all_dates:
        conn.close()
        return

    strategies = ["MaVolumeStrategy", "TurtleTradeStrategy", "HighTightFlagStrategy",
                  "LimitUpShakeoutStrategy", "UptrendLimitDownStrategy", "RpsBreakoutStrategy",
                  "PrivatePlacementStrategy", "FiftyTwoWeekHighStrategy", "LimitUpPullbackStrategy",
                  "MABullishMACDStrategy", "BollingerSqueezeStrategy"]

    updated = 0
    for i, target_date in enumerate(all_dates):
        # 确定 lookback 窗口: target_date 及之前最近的 lookback 个日期
        window_start = max(0, i - lookback + 1)
        window_dates = all_dates[window_start:i + 1]

        date_placeholders = ",".join("?" * len(window_dates))

        for sname in strategies:
            # T+1 stats
            stats_1d = conn.execute(
                f"SELECT COUNT(*), SUM(CASE WHEN next_return>0 THEN 1 ELSE 0 END), AVG(next_return) "
                f"FROM strategy_picks WHERE strategy=? AND next_return IS NOT NULL "
                f"AND next_return BETWEEN -20 AND 20 AND date IN ({date_placeholders})",
                [sname] + window_dates).fetchone()

            if not stats_1d or not stats_1d[0]:
                continue

            t, w, ar = stats_1d
            wr = round(w / t * 100, 1) if t else 0
            ar = round(ar, 2) if ar else 0
            daily_avg = conn.execute(
                f"SELECT AVG(daily_avg) FROM ("
                f"SELECT AVG(next_return) as daily_avg FROM strategy_picks "
                f"WHERE strategy=? AND next_return IS NOT NULL "
                f"AND next_return BETWEEN -20 AND 20 AND date IN ({date_placeholders}) "
                f"GROUP BY date)",
                [sname] + window_dates).fetchone()[0]
            total_ret_1d = round(((1 + daily_avg / 100) ** len(window_dates) - 1) * 100, 2) if daily_avg else 0

            # Multi-day stats (3d/5d/10d)
            multi = {}
            for day, col in [("3d", "ret_3d"), ("5d", "ret_5d"), ("10d", "ret_10d")]:
                md = conn.execute(
                    f"SELECT COUNT(*), SUM(CASE WHEN {col}>0 THEN 1 ELSE 0 END), AVG({col}) "
                    f"FROM strategy_picks WHERE strategy=? AND {col} IS NOT NULL "
                    f"AND {col} BETWEEN -50 AND 50 AND date IN ({date_placeholders})",
                    [sname] + window_dates).fetchone()
                if md and md[0] and md[0] > 0:
                    mt, mw, mar = md
                    mwr = round(mw / mt * 100, 1) if mt else 0
                    mar = round(mar, 2) if mar else 0
                    mdaily = conn.execute(
                        f"SELECT AVG(daily_avg) FROM ("
                        f"SELECT AVG({col}) as daily_avg FROM strategy_picks "
                        f"WHERE strategy=? AND {col} IS NOT NULL "
                        f"AND {col} BETWEEN -50 AND 50 AND date IN ({date_placeholders}) "
                        f"GROUP BY date)",
                        [sname] + window_dates).fetchone()[0]
                    mtotal = round(((1 + mdaily / 100) ** len(window_dates) - 1) * 100, 2) if mdaily else 0
                    multi[f"win_rate_{day}"] = mwr
                    multi[f"avg_return_{day}"] = mar
                    multi[f"total_return_{day}"] = mtotal
                else:
                    multi[f"win_rate_{day}"] = 0
                    multi[f"avg_return_{day}"] = 0
                    multi[f"total_return_{day}"] = 0

            columns = "date,strategy,lookback_days,total_picks,win_count,win_rate,avg_return,total_return"
            values = "?,?,?,?,?,?,?,?"
            params = [target_date, sname, lookback, t, w, wr, ar, total_ret_1d]

            for day in ["3d", "5d", "10d"]:
                columns += f",win_rate_{day},avg_return_{day},total_return_{day}"
                values += ",?,?,?"
                params += [multi[f"win_rate_{day}"], multi[f"avg_return_{day}"], multi[f"total_return_{day}"]]

            conn.execute(
                f"INSERT OR REPLACE INTO strategy_stats ({columns}) VALUES ({values})", params)
            updated += 1

    conn.commit()
    conn.close()
    log(f"重算历史策略统计: {updated} 条 (共 {len(all_dates)} 个日期)")

# ─── 新增：加权 Top10 + 策略分析 ───

def compute_weights() -> tuple[dict, set]:
    """返回 (权重字典, 有数据的策略名集合) — 融合 T+1/T+3/T+5/T+10 多日表现"""
    conn = sqlite3.connect(DB_PATH)
    weights = {}
    # 加载所有有历史统计的策略（去掉 LIMIT 7）
    for row in conn.execute(
        "SELECT strategy, win_rate, avg_return, total_picks, "
        "win_rate_3d, avg_return_3d, win_rate_5d, avg_return_5d, win_rate_10d, avg_return_10d "
        "FROM strategy_stats WHERE lookback_days=10 ORDER BY date DESC").fetchall():
        sname = row[0]
        if sname in weights:
            continue  # 每个策略只取最新一条
        cnt = row[3] or 0

        # 计算单个时间维度的子分数（沿用现有逻辑）
        def dim_score(wr, ar):
            wr = wr or 0
            ar = ar or 0
            if wr > 0 and ar > 0:
                return wr * (1 + ar / 100)
            elif wr > 0:
                return wr * 0.5  # 负收益策略减半
            else:
                return wr * 0.1  # 胜率也负的基本忽略

        # 各时间维度: (win_rate, avg_return), row 索引见 SQL
        dims = {
            "1d":  (row[1], row[2]),
            "3d":  (row[4], row[5]),
            "5d":  (row[6], row[7]),
            "10d": (row[8], row[9]),
        }
        target_weights = {"1d": 0.40, "3d": 0.30, "5d": 0.20, "10d": 0.10}

        # 计算各维度子分数，跳过数据为空的维度（NULL 表示该维度无数据）
        scores = {}
        active_tw = {}
        for dim, (wr_val, ar_val) in dims.items():
            if wr_val is not None or ar_val is not None:
                scores[dim] = dim_score(wr_val, ar_val)
                active_tw[dim] = target_weights[dim]

        if not active_tw:
            raw = 0.01  # 所有维度均无数据
        else:
            # 有数据维度的目标权重归一化，加权求和
            tw_sum = sum(active_tw.values())
            raw = sum((active_tw[d] / tw_sum) * scores[d] for d in active_tw)

        if cnt < 5:
            raw *= cnt / 5
        weights[sname] = max(raw, 0.01)

    # 今天有信号但无历史统计的策略，给默认小权重
    today = date.today().isoformat()
    for row in conn.execute(
        "SELECT DISTINCT strategy FROM strategy_picks WHERE date=?", (today,)).fetchall():
        if row[0] not in weights:
            weights[row[0]] = 0.05  # 默认中性权重

    conn.close()
    total = sum(weights.values())
    return ({k: round(v/total,3) for k,v in weights.items()} if total > 0 else {}, set(weights.keys()))

def _migrate_daily_top10():
    """迁移 daily_top10 表：添加 category 列 + UNIQUE(date,category,symbol)"""
    conn = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_top10)").fetchall()]
    if 'category' in cols:
        conn.close()
        return
    log("迁移 daily_top10 表结构: 添加 category 列...")
    conn.execute("ALTER TABLE daily_top10 ADD COLUMN category TEXT")
    conn.execute("""CREATE TABLE daily_top10_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'overall',
        rank INTEGER NOT NULL, symbol TEXT NOT NULL, name TEXT,
        score REAL, strategies TEXT, close_price REAL,
        ret_1d REAL, ret_3d REAL, ret_5d REAL, ret_10d REAL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(date, category, symbol))""")
    # 显式列名映射，避免 column order 不对齐
    conn.execute("""INSERT INTO daily_top10_v2 
        (id, date, category, rank, symbol, name, score, strategies, close_price, ret_1d, ret_3d, ret_5d, ret_10d, created_at)
        SELECT id, date, 'overall', rank, symbol, name, score, strategies, close_price, ret_1d, ret_3d, ret_5d, ret_10d, created_at
        FROM daily_top10""")
    conn.execute("DROP TABLE daily_top10")
    conn.execute("ALTER TABLE daily_top10_v2 RENAME TO daily_top10")
    conn.commit()
    conn.close()
    log("daily_top10 表迁移完成")

def pick_top10(data, weights):
    """生成全市场 Top10 + 主板 Top10 双榜单"""
    _migrate_daily_top10()

    scores = defaultdict(float)
    strat_set = defaultdict(set)
    all_syms = set()
    for s in data["strategies"]:
        sname = s["strategy"]
        w = weights.get(sname, 0)
        for sym in s["symbols"]:
            if w > scores[sym]:
                scores[sym] = w
            strat_set[sym].add(sname.replace("Strategy",""))
            all_syms.add(sym)

    # 多策略共振加分（每多一个 +15%）
    for sym in list(scores.keys()):
        extra = len(strat_set[sym]) - 1
        if extra > 0:
            scores[sym] = round(scores[sym] * (1 + 0.15 * extra), 4)

    # ── 成交额破平 ──
    conn = sqlite3.connect(DB_PATH)
    run_date = data["date"]
    placeholders = ",".join("?" * len(scores))
    turnover_map = {}
    for sym, amt in conn.execute(
        f"SELECT symbol, turnover FROM stock_daily WHERE symbol IN ({placeholders}) AND date=?",
        list(scores.keys()) + [run_date]
    ).fetchall():
        if amt and amt > 0:
            turnover_map[sym] = amt

    if turnover_map:
        max_log = max(__import__('math').log10(v) for v in turnover_map.values())
        min_log = min(__import__('math').log10(v) for v in turnover_map.values())
        log_range = max_log - min_log if max_log > min_log else 1
        for sym in scores:
            amt = turnover_map.get(sym)
            if amt:
                log_val = __import__('math').log10(amt)
                bonus = round(0.005 * (log_val - min_log) / log_range, 5)
                scores[sym] = round(scores[sym] + bonus, 5)

    # ── 排序 ──
    ranked = sorted(scores.items(), key=lambda x: (-x[1], -len(strat_set[x[0]])))

    MAIN_BOARD_PREFIX = ("600", "601", "603", "000", "001", "002", "003")
    is_main = lambda sym: sym.startswith(MAIN_BOARD_PREFIX)

    all_results = []
    CATEGORIES = [("overall", "全市场", ranked),
                  ("mainboard", "主板", [(s, sc) for s, sc in ranked if is_main(s)])]

    # ── 从 stock_basics 取 name 映射 ──
    name_map = _get_name_map(conn, list(all_syms))

    for cat, cat_label, source in CATEGORIES:
        conn.execute("DELETE FROM daily_top10 WHERE date=? AND category=?", (run_date, cat))
        for rank, (sym, score) in enumerate(source[:10], 1):
            row = conn.execute("SELECT close_qfq FROM stock_daily WHERE symbol=? AND date=?",
                               (sym, run_date)).fetchone()
            cp = row[0] if row else None
            nm = name_map.get(sym, sym)
            all_results.append({"rank":rank,"symbol":sym,"name":nm,
                                "score":round(score,3),"category":cat,
                                "strategies":",".join(sorted(strat_set[sym])),"close_price":cp})
            conn.execute(
                "INSERT OR IGNORE INTO daily_top10 (date,category,rank,symbol,name,score,strategies,close_price) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (run_date, cat, rank, sym, nm, round(score,3),
                 ",".join(sorted(strat_set[sym])), cp))

    conn.commit()
    conn.close()
    n_overall = len([r for r in all_results if r['category']=='overall'])
    n_main = len([r for r in all_results if r['category']=='mainboard'])
    log(f"精选榜单: 全市场{n_overall} + 主板{n_main} = {len(all_results)} 只")
    return all_results

def backfill_top10_returns():
    conn = sqlite3.connect(DB_PATH)
    today = date.today().strftime("%Y-%m-%d")
    # 同时检查所有收益列，避免 ret_1d已填但ret_3d未填的情况
    rows = conn.execute(
        "SELECT id,date,symbol FROM daily_top10 "
        "WHERE (ret_1d IS NULL OR ret_3d IS NULL OR ret_5d IS NULL OR ret_10d IS NULL) "
        "AND date < ?", (today,)).fetchall()
    if not rows: conn.close(); return
    updated = 0
    for row_id, pick_date, sym in rows:
        base = conn.execute("SELECT close_qfq FROM stock_daily WHERE symbol=? AND date=?", (sym,pick_date)).fetchone()
        if not base or not base[0]: continue
        bp = base[0]
        future = conn.execute("SELECT close_qfq FROM stock_daily WHERE symbol=? AND date>? ORDER BY date", (sym,pick_date)).fetchall()
        if not future: continue
        rets = {}
        for off, col in [(1,"ret_1d"),(3,"ret_3d"),(5,"ret_5d"),(10,"ret_10d")]:
            if off <= len(future):
                rets[col] = round((future[off-1][0]/bp-1)*100, 2)
        if rets:
            sets = ", ".join(f"{k}=?" for k in rets)
            conn.execute(f"UPDATE daily_top10 SET {sets} WHERE id=?", list(rets.values())+[row_id])
            updated += 1
    conn.commit()
    conn.close()
    if updated: log(f"回填 top10 收益: {updated} 条")

def analyze_strategies(weights):
    conn = sqlite3.connect(DB_PATH)
    today = date.today().strftime("%Y-%m-%d")
    for sname, weight in weights.items():
        stats = conn.execute(
            "SELECT win_rate, avg_return, total_picks FROM strategy_stats "
            "WHERE strategy=? AND lookback_days=10 ORDER BY date DESC LIMIT 1", (sname,)).fetchone()
        if not stats: continue
        wr, ar, cnt = stats
        sug = []
        if cnt < 5: sug.append("信号太少，样本不足")
        elif wr and wr < 35: sug.append("胜率偏低，考虑反转或弃用")
        elif wr and ar and ar < -1: sug.append("虽胜率高但盈亏比差")
        if ar and ar > 3: sug.append("收益稳定，可提高仓位")
        if cnt and cnt > 200: sug.append("信号过多需叠加过滤")
        conn.execute(
            "INSERT OR REPLACE INTO strategy_analysis (date,strategy,weight,win_rate_10d,avg_return_10d,signal_count_10d,suggestion) "
            "VALUES (?,?,?,?,?,?,?)",
            (today,sname,weight,round(wr,1)if wr else 0,round(ar,2)if ar else 0,cnt or 0,"; ".join(sug) if sug else "暂无调整建议"))
    conn.commit()
    conn.close()

def show_summary(lookback=10):
    conn = sqlite3.connect(DB_PATH)
    today = date.today().strftime("%Y-%m-%d")

    # ── 策略统计 ──
    print(f"\n{'='*80}")
    print(f"📊 策略回测 ({lookback}天滚动)")
    print(f"{'='*80}")
    print(f"{'策略':<18s} {'信号':>5s} {'T+1胜率':>7s} {'T+1均':>7s} {'T+3胜率':>7s} {'T+3均':>7s} {'T+5胜率':>7s} {'T+5均':>7s}")
    print("-" * 80)
    for row in conn.execute(
        "SELECT strategy,total_picks,win_rate,avg_return,"
        "win_rate_3d,avg_return_3d,win_rate_5d,avg_return_5d "
        "FROM strategy_stats WHERE lookback_days=? "
        "AND date=(SELECT MAX(date) FROM strategy_stats s2 WHERE s2.strategy=strategy_stats.strategy AND s2.lookback_days=?)",
        (lookback, lookback)).fetchall():
        sname = row[0].replace("Strategy","")
        vals = [row[1]]  # total_picks
        for i in range(2, 8):
            v = row[i]
            if v is None:
                vals.append("   N/A")
            elif i % 2 == 0:  # win_rate column
                vals.append(f"{v:>6.1f}%")
            else:  # avg_return column
                vals.append(f"{v:>+6.2f}%")
        print(f"{sname:<18s} {vals[0]:>5d} {vals[1]:>7s} {vals[2]:>7s} {vals[3]:>7s} {vals[4]:>7s} {vals[5]:>7s} {vals[6]:>7s}")

    # ── 今日精选榜单 ──
    print(f"\n{'='*60}")
    print(f"🔥 精选榜单 ({today})")
    print(f"{'='*60}")

    for cat, cat_label in [("overall", "全市场 Top10"), ("mainboard", "主板 Top10")]:
        print(f"\n  【{cat_label}】")
        print(f"  {'#':<4}{'代码':<8}{'名称':<8}{'得分':>6}  策略共振")
        print("  " + "-"*56)
        rows = conn.execute(
            "SELECT rank,symbol,name,score,strategies FROM daily_top10 WHERE date=? AND category=? ORDER BY rank",
            (today, cat)).fetchall()
        if rows:
            for r in rows:
                print(f"  {r[0]:<4}{r[1]:<8}{r[2]:<8}{r[3]:>6.3f}  {r[4]}")
        else:
            print(f"  (暂无)")

    # ── 历史表现 ──
    for cat, cat_label in [("overall", "全市场 Top10"), ("mainboard", "主板 Top10")]:
        print(f"\n{'='*60}")
        print(f"📈 {cat_label} 历史表现")
        print(f"{'='*60}")
        rows = conn.execute(
            "SELECT date,AVG(ret_1d),AVG(ret_3d),AVG(ret_5d),AVG(ret_10d),COUNT(*) "
            "FROM daily_top10 WHERE date<? AND ret_1d IS NOT NULL AND category=? GROUP BY date ORDER BY date DESC LIMIT ?",
            (today, cat, lookback)).fetchall()
        if rows:
            print(f"{'日期':<12s} {'数':>3s} {'T+1':>7s} {'T+3':>7s} {'T+5':>7s} {'T+10':>7s}")
            print("-"*50)
            for r in rows:
                d1 = r[1] or 0; d3 = r[2] or 0; d5 = r[3] or 0; d10 = r[4] or 0; cnt = r[5] or 0
                print(f"{r[0]:<12s} {cnt:>3d} {d1:>+6.2f}% {d3:>+6.2f}% {d5:>+6.2f}% {d10:>+6.2f}%")
        else:
            print(f"  (暂无历史数据)")

    # ── 策略分析 ──
    print(f"\n{'='*60}")
    print(f"🧠 策略有效性分析")
    print(f"{'='*60}")
    for row in conn.execute(
        "SELECT strategy,weight,win_rate_10d,avg_return_10d,signal_count_10d,suggestion "
        "FROM strategy_analysis WHERE date=? ORDER BY weight DESC", (today,)).fetchall():
        sname = row[0].replace("Strategy","")
        print(f"\n  [{sname}] 权={row[1]:.3f} | {row[4]}次 | 胜率{row[2]}% | 均{row[3]:+.2f}%")
        if row[5]: print(f"    💡 {row[5]}")

    conn.close()

if __name__ == "__main__":
    import os; os.makedirs("logs", exist_ok=True)

    if "--stats" in sys.argv:
        backfill_returns()
        compute_stats()
        recompute_historical_stats()
        backfill_top10_returns()
        show_summary()
        sys.exit(0)

    # 0. 拉取基本面数据（每天一次）
    import subprocess as _sp
    _sp.run([".venv-host/bin/python3", "fetch_basics.py"], timeout=90)
    log("基本面数据已更新")

    data = run_strategies()
    if not data:
        log("策略运行失败，退出")
        sys.exit(1)

    save_picks(data)
    backfill_returns()
    compute_stats()

    weights, _ = compute_weights()
    top10 = pick_top10(data, weights)
    backfill_top10_returns()
    analyze_strategies(weights)
    show_summary()
    log("完成")
