"""自进化引擎 — 参数网格寻优 + 自适应阈值

双层架构：
1. 每日自适应 (Tier 2)：根据近20笔信号T+5胜率微调参数
2. 每周参数寻优 (Tier 1)：过去60天全量回测，选最优参数组合

结果写入 strategy_params 表，策略启动时读取覆盖默认值。
"""
import sqlite3
import json
from datetime import date, timedelta
from itertools import product

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

DB_PATH = "data/sequoia_v2.db"
STRATEGY_NAME = "BottomFirstVolStrategy"

# ── 参数网格（每周寻优探索空间） ──
PARAM_GRID = {
    "price_from_low_max": [0.20, 0.35],
    "range_20d_min": [0.02, 0.03, 0.05],
    "range_20d_max": [0.15, 0.20, 0.25],
    "vol_ratio_min": [1.2, 1.5, 1.8],
    "ma60_dist_max": [0.08, 0.12, 0.15],
    "consolidation_amp_max": [1.0, 2.0],
}


def _ensure_table():
    """确保 strategy_params 表存在"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategy_params (
            strategy TEXT NOT NULL,
            date     TEXT NOT NULL,
            source   TEXT NOT NULL DEFAULT 'adaptive',
            params   TEXT NOT NULL,
            score    REAL,
            PRIMARY KEY (strategy, date, source)
        )
    """)
    conn.commit()
    conn.close()


def _get_picks_cursor(conn, days=20):
    """获取最近 N 天该策略的 picks（含 T+5 收益）"""
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT next_return, ret_3d, ret_5d
           FROM strategy_picks
           WHERE strategy=? AND date>=? AND ret_5d IS NOT NULL
           ORDER BY date DESC LIMIT 50""",
        (STRATEGY_NAME, cutoff),
    ).fetchall()
    return rows


def _sharpe(returns):
    """计算夏普比率（年化，假设每日调仓）"""
    if not returns or len(returns) < 3:
        return 0.0
    arr = [r for r in returns if r is not None and -20 < r < 20]
    if len(arr) < 3:
        return 0.0
    mean_r = sum(arr) / len(arr)
    std_r = (sum((x - mean_r) ** 2 for x in arr) / len(arr)) ** 0.5
    if std_r < 0.001:
        return 0.0
    return mean_r / std_r * (252 ** 0.5)  # T+5 → 年化近似


# ════════════════════════════════════════
# Tier 2: 每日自适应
# ════════════════════════════════════════

def adaptive_tune() -> dict:
    """根据近20笔信号T+5胜率微调参数"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    picks = _get_picks_cursor(conn)
    conn.close()

    # 读取当前已保存的参数（默认）
    current_params = _load_current_params()

    if not picks or len(picks) < 5:
        logger.info(f"adaptive_tune: 样本不足({len(picks)}), 跳过自适应")
        return current_params

    wins = sum(1 for r in picks if r[2] is not None and r[2] > 0)
    wr = wins / len(picks)

    # 零信号检测
    today_str = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    recent_zeros = conn.execute(
        """SELECT CASE WHEN MAX(cnt) IS NULL THEN 0 ELSE MAX(cnt) END FROM (
               SELECT date, COUNT(*) as cnt FROM strategy_picks
               WHERE strategy=? AND date>=?
               GROUP BY date
           )""",
        (STRATEGY_NAME, (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")),
    ).fetchone()[0]
    conn.close()

    new_params = dict(current_params)
    adjustments = []

    if wr < 0.35:
        # 太差了：放松阈值
        new_params["vol_ratio_min"] = round(max(1.1, current_params["vol_ratio_min"] * 0.85), 2)
        new_params["range_20d_max"] = round(min(0.35, current_params["range_20d_max"] * 1.15), 2)
        new_params["ma60_dist_max"] = round(min(0.25, current_params["ma60_dist_max"] * 1.2), 2)
        adjustments.append(f"胜率{wr:.0%}偏低→放松")
    elif wr > 0.60:
        # 太好了：收紧阈值，提高标准
        new_params["vol_ratio_min"] = round(min(3.0, current_params["vol_ratio_min"] * 1.15), 2)
        new_params["range_20d_max"] = round(max(0.10, current_params["range_20d_max"] * 0.85), 2)
        new_params["ma60_dist_max"] = round(max(0.05, current_params["ma60_dist_max"] * 0.85), 2)
        adjustments.append(f"胜率{wr:.0%}偏高→收紧")
    else:
        adjustments.append(f"胜率{wr:.0%}正常范围，不调整")

    # 连续5天零信号 → 强制大幅放松
    if recent_zeros >= 5:
        new_params["vol_ratio_min"] = max(1.0, new_params["vol_ratio_min"] * 0.8)
        new_params["range_20d_min"] = max(0.01, new_params["range_20d_min"] * 0.8)
        adjustments.append(f"连续{recent_zeros}天零信号→强制大幅放松")

    if adjustments:
        logger.info(f"adaptive_tune: {'; '.join(adjustments)}")

    _save_params(new_params, "adaptive", score=wr)
    return new_params


# ════════════════════════════════════════
# Tier 1: 每周网格寻优
# ════════════════════════════════════════

def grid_search(lookback_days: int = 20) -> dict:
    """遍历全部参数组合，回测过去 N 天 T+5收益率，选最优
    lookback_days=20（每日轻量模式），=60（每周完整模式）

    预计算特征向量 + 向量化参数测试，避免嵌套循环。
    """
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)

    cutoff = (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")
    signal_cutoff = (date.today() - timedelta(days=lookback_days * 2)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT symbol, date, close_qfq, volume, high, low "
        "FROM stock_daily WHERE date >= ? AND close_qfq IS NOT NULL "
        "ORDER BY symbol, date", (cutoff,)
    ).fetchall()
    conn.close()

    if not rows or len(rows) < 1000:
        logger.warning("grid_search: 样本不足，跳过")
        return _load_current_params()

    # ── Phase 1: 预计算特征向量 ──
    from collections import defaultdict
    stock_data = defaultdict(list)
    for r in rows:
        stock_data[r[0]].append({
            "date": r[1], "close": r[2], "volume": r[3],
            "high": r[4], "low": r[5],
        })

    features = []  # [(feature_tuple, T+5_return)]
    for sym, bars in stock_data.items():
        if len(bars) < 60:
            continue
        closes = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        n = len(closes)

        for i in range(60, n):
            if bars[i]["date"] < signal_cutoff:
                continue
            c = closes[i]
            if c <= 0:
                continue

            # 条件① 离250日最低比例
            low_250 = min(closes[max(0, i - 250):i])
            low_dist = c / low_250 - 1
            if low_dist > 0.40:  # 宽松预过滤，硬阈值后续参数测试
                pass

            # 条件② 20日涨幅
            ret_20d = c / closes[i - 20] - 1
            if ret_20d < 0.01 or ret_20d > 0.30:
                continue

            # 条件③ 放量倍数
            vol_ma20 = sum(volumes[i - 20:i]) / 20
            if vol_ma20 <= 0 or volumes[i] < vol_ma20 * 1.0:
                continue
            vol_ratio = volumes[i] / vol_ma20

            # 条件④ MA60距离
            ma60 = sum(closes[i - 60:i]) / 60
            if ma60 <= 0:
                continue
            ma60_dist = c / ma60 - 1
            if ma60_dist < -0.05 or ma60_dist > 0.25:
                continue

            # 条件⑤ 60日振幅
            amp = max(highs[i - 60:i]) / max(max(lows[i - 60:i]), 0.01) - 1
            if amp > 3.0:
                continue

            # T+5收益
            if i + 5 >= n:
                continue
            ret_5d = closes[i + 5] / c - 1
            if not (-20 < ret_5d < 20):
                continue

            features.append({
                "low_dist": low_dist, "ret_20d": ret_20d,
                "vol_ratio": vol_ratio, "ma60_dist": ma60_dist,
                "amp": amp, "ret_5d": ret_5d,
            })

    logger.info(f"grid_search: 预计算完成, {len(features)} 个信号候选")

    if len(features) < 20:
        logger.warning("grid_search: 信号候选不足({len(features)})")
        return _load_current_params()

    # ── Phase 2: 参数网格测试（向量化） ──
    keys = list(PARAM_GRID.keys())
    combos = list(product(*[PARAM_GRID[k] for k in keys]))
    logger.info(f"grid_search: {len(combos)} 种参数组合 × {len(features)} 个候选")

    best_combo = None
    best_sharpe = -999.0
    results = []

    for combo in combos:
        pl = combo[0]  # price_from_low_max
        r20min = combo[1]  # range_20d_min
        r20max = combo[2]  # range_20d_max
        vr = combo[3]  # vol_ratio_min
        m60 = combo[4]  # ma60_dist_max
        ca = combo[5]  # consolidation_amp_max

        returns = []
        for f in features:
            if f["low_dist"] > pl:
                continue
            if f["ret_20d"] < r20min or f["ret_20d"] > r20max:
                continue
            if f["vol_ratio"] < vr:
                continue
            if f["ma60_dist"] > m60:
                continue
            if f["amp"] > ca:
                continue
            returns.append(f["ret_5d"])

        if len(returns) < 10:
            continue

        sp = _sharpe(returns)
        results.append((dict(zip(keys, combo)), sp, len(returns)))
        if sp > best_sharpe:
            best_sharpe = sp
            best_combo = dict(zip(keys, combo))

    if best_combo is None:
        logger.warning("grid_search: 所有组合均未产生足够信号")
        return _load_current_params()

    logger.info(f"grid_search: 最优参数组合 {best_combo}, 夏普 {best_sharpe:.2f}, "
                f"信号量 {[r[2] for r in results if r[0]==best_combo][0]}")

    # 保存最优参数
    _save_params(best_combo, "grid_search", score=round(best_sharpe, 2))

    # 打印 Top5 组合供参考
    results.sort(key=lambda x: -x[1])
    logger.info("grid_search Top5:")
    for params, sp, cnt in results[:5]:
        logger.info(f"  Sharpe={sp:.2f} 信号={cnt}  {params}")

    return best_combo


# ════════════════════════════════════════
# 参数读写
# ════════════════════════════════════════

def _load_current_params() -> dict:
    """加载最新保存的参数，未找到时返回默认值"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        """SELECT params FROM strategy_params
           WHERE strategy=? ORDER BY date DESC, source DESC LIMIT 1""",
        (STRATEGY_NAME,),
    ).fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return {k: v[0] for k, v in PARAM_GRID.items()}


def _save_params(params: dict, source: str, score: float = None):
    """保存参数到 DB"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT OR REPLACE INTO strategy_params (strategy, date, source, params, score)
           VALUES (?, ?, ?, ?, ?)""",
        (STRATEGY_NAME, date.today().strftime("%Y-%m-%d"), source,
         json.dumps(params), score),
    )
    conn.commit()
    conn.close()


def is_friday() -> bool:
    """判断今天是否是周五（用于触发网格寻优）"""
    return date.today().weekday() == 4


def run_daily():
    """每日运行：自适应微调 + 每日网格寻优（周五做完整60天回测）"""
    logger.info("=== 自进化引擎开始 ===")

    # 总是先做自适应
    new_params = adaptive_tune()

    # 每日网格寻优（20天窗口）
    logger.info("每日网格寻优（20天窗口）...")
    daily_params = grid_search(lookback_days=20)

    # 周五：额外做60天全量网格寻优覆盖
    if is_friday():
        logger.info("周五 → 完整网格寻优（60天窗口）")
        weekly_params = grid_search(lookback_days=60)
        new_params = weekly_params  # 周优覆盖日优
    else:
        new_params = daily_params

    logger.info(f"自进化引擎结束，当前参数: {new_params}")
    return new_params
