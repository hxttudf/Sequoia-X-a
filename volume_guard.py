#!/usr/bin/env python3
"""volume_guard.py — 成交量单位守卫(根治单位错位)

全库口径(铁律):
  stock_daily.volume = 手 (A股/ETF统一), amount = 元
  新浪/Wind getKLineData 原始 = 股 → 入库前必须 ÷100
  腾讯 fetch_kline_tx = 手 → 原样

守卫机制(三层):
  L1 静态转换: 新浪源数据调用 normalize_sina(volume, amount) 强制÷100
  L2 写入前哨兵: guard_row() 用该symbol前N日中位数检测>30倍异常, 自动÷100纠正
  L3 收盘后巡检: scan_day(date) 全表扫当日异常, 返回+可选自动修复(供cron调用)

所有写 stock_daily 的脚本必须:
  1) 新浪源数据先过 normalize_sina()
  2) INSERT 前逐行过 guard_row(conn, symbol, date, volume, amount)
"""
import sqlite3
from datetime import datetime, timedelta

DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
ANOMALY_RATIO = 30.0   # 超30倍中位数判为单位错位(真实放量极少超此值)
LOOKBACK_DAYS = 10     # 取前N个自然日的历史做基准


def _median(vals):
    vals = sorted(v for v in vals if v and v > 0)
    n = len(vals)
    if n == 0:
        return None
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _hist_median(conn, symbol, before_date, col='volume'):
    """该symbol某列在before_date前LOOKBACK_DAYS内的中位数(排除0/NULL)"""
    start = (datetime.strptime(before_date, '%Y-%m-%d') - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    rows = conn.execute(
        f"SELECT {col} FROM stock_daily WHERE symbol=? AND date>=? AND date<? AND {col}>0",
        (symbol, start, before_date)).fetchall()
    return _median([r[0] for r in rows])


def normalize_sina(volume, amount=None):
    """L1: 新浪原始数据(股/元) → 库内口径(手/元). 新浪amount已是元, 不动."""
    v = (volume or 0) / 100.0
    return v, amount


def guard_row(conn, symbol, date, volume, amount=None, fix=True):
    """L2: 单行哨兵. 返回(volume, amount, corrected:bool).
    volume超前10日中位数30倍 → 判定为'股'混入, ÷100纠正.
    amount超30倍 → 同样÷100(新浪整批写错场景)."""
    corrected = False
    if volume and volume > 0:
        med = _hist_median(conn, symbol, date, 'volume')
        if med and volume > med * ANOMALY_RATIO:
            if fix:
                volume = volume / 100.0
                corrected = True
            else:
                return volume, amount, True
    if amount and amount > 0:
        # ETF amount正确口径 = vol(手)×100份×close; 判定用自洽比而非中位数(中位数基准被历史口径污染)
        row = conn.execute("SELECT volume, close FROM stock_daily WHERE symbol=? AND date=?", (symbol, date)).fetchone()
        vol_now, close_now = row if row else (None, None)
        if symbol[:2] in ('51', '15', '56', '58') and vol_now and close_now:
            r = amount / (vol_now * close_now)
            if 80 <= r <= 125:  # 偏大100倍(老口径对齐时会冒出, 不算错)
                pass
            elif r > 30 or r < 0.008:  # 明显异常才标记
                if fix:
                    amount = amount / 100.0
                    corrected = True
                else:
                    return volume, amount, True
    return volume, amount, corrected


def scan_day(date, fix=False, conn=None):
    """L3: 全表巡检某日. 返回异常列表[(symbol, volume, median, fixed)]. fix=True直接改库."""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB, timeout=60)
    rows = conn.execute(
        "SELECT symbol, volume, amount, close, close_qfq FROM stock_daily WHERE date=? AND volume>0",
        (date,)).fetchall()
    anomalies = []
    for sym, v, amt, c, cq in rows:
        med = _hist_median(conn, sym, date, 'volume')
        if not med:
            continue
        if v > med * ANOMALY_RATIO:
            anomalies.append((sym, v, round(med, 1), 'volume'))
            if fix:
                conn.execute("UPDATE stock_daily SET volume=volume/100.0 WHERE symbol=? AND date=?", (sym, date))
        if amt and c and v and sym[:2] in ('51', '15', '56', '58'):
            r = amt / (v * c)
            if 0.8 <= r <= 1.25:  # 偏小100倍(历史老病: amount=vol手×close缺×100)
                anomalies.append((sym, amt, round(v * c * 100, 1), 'amount_small'))
                if fix:
                    conn.execute("UPDATE stock_daily SET amount=amount*100.0 WHERE symbol=? AND date=?", (sym, date))
    if fix:
        conn.commit()
    if own:
        conn.close()
    return anomalies


if __name__ == '__main__':
    import sys
    # 用法: python3 volume_guard.py 2026-08-27 [--fix]
    d = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y-%m-%d')
    fix = '--fix' in sys.argv
    ans = scan_day(d, fix=fix)
    print(f"{d} 异常{len(ans)}条" + ("(已修)" if fix else "( dry-run )"))
    for a in ans[:30]:
        print(' ', a)
