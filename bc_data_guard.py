#!/usr/bin/env python3
"""数据完整性体检 + 自动补缺 —— 结构性防假信号
体检: 逐股验K线窗口无缺日; 某日"大批股票缺"(>alert) = 系统性故障(日更挂了), 告警.
另: 半空日检测 —— 权威交易日内当日个股数 < 中位*0.5 = 日更半空(数据源未发布/链没跑完), 直接告警.
补缺(--fix): 对缺日股票调 fill_gaps 定向补齐 —— 源有则补(修复); 源也无=停牌(保留, 不算缺陷).
用法: bc_data_guard.py [--fix] [--days 250] [--alert 50]
动因(2026-10-26事故): 旧版用 "当日股数>=中位*0.3" 判交易日 → 半空日(1587只)被踢出日历,
  于是既不算缺日、也不纳入比对基准, 报"无系统性缺日 OK", 假数据静默过闸。
"""
import sqlite3, sys, subprocess
from collections import Counter, defaultdict

DB = '/home/ubuntu/databases/Sequoia选股.db'
FY = '/home/ubuntu/Sequoia-X-a/.venv-host/bin/python3'
FG = '/home/ubuntu/Sequoia-X-a/fill_gaps.py'
WIN = 250
ALERT = 50
FIX = '--fix' in sys.argv
for a in sys.argv[1:]:
    if a.startswith('--days'):
        WIN = int(a.split('=')[1])
    if a.startswith('--alert'):
        ALERT = int(a.split('=')[1])


def scan():
    d = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=120)
    lookback = str(WIN + 70)
    cnt = Counter(r[0] for r in d.execute(
        "SELECT date FROM stock_daily WHERE close_qfq>0 "
        "AND date >= date((SELECT MAX(date) FROM stock_daily), '-" + lookback + " day')"))
    if not cnt:
        d.close(); return 0, 0, [], Counter(), 0, []
    med = sorted(cnt.values())[len(cnt) // 2]
    # 权威交易日历是否存在
    try:
        cal_tbl = {r[0] for r in d.execute("SELECT date FROM trade_calendar WHERE is_trading=1")}
    except Exception:
        cal_tbl = set()
    # 构建交易日集合: 权威日历优先(是交易日就纳入, 即便半空); 日历未覆盖的旧日期退回启发式
    real = set()
    for dt in cnt:
        if dt in cal_tbl:
            real.add(dt)
        elif cnt[dt] >= med * 0.3:
            real.add(dt)
    win_dates = set(sorted(real)[-WIN:])
    # 半空日: 窗口内任一交易日, 当日个股数 < 中位*0.5 = 日更半空, 强制告警(即便它"看起来像休市")
    half = sorted((dt, cnt.get(dt, 0)) for dt in win_dates if cnt.get(dt, 0) < med * 0.5)
    per = defaultdict(set)
    for sym, dt in d.execute("SELECT symbol,date FROM stock_daily WHERE close_qfq>0 AND date >= ?",
                             (min(win_dates),)):
        per[sym].add(dt)
    d.close()
    by_date = Counter(); gap_syms = []; tot = 0
    for sym, ds in per.items():
        first, last = min(ds), max(ds)
        miss = {x for x in win_dates if first <= x <= last} - ds
        if miss:
            gap_syms.append(sym); tot += len(miss)
            for m in miss:
                by_date[m] += 1
    return len(win_dates), len(per), gap_syms, by_date, tot, half


nd, nstock, gaps, bydate, tot, half_days = scan()
print(f"交易日历 {nd} 天, 股票 {nstock} 只")
print(f"缺日: {len(gaps)} 只 / 共 {tot} 根")
for dt, n in bydate.most_common(6):
    print(f"   {dt}: {n} 只缺" + ("  <== 系统性缺日!" if n > ALERT else ""))
alert_dates = [dt for dt, n in bydate.items() if n > ALERT]

if FIX and gaps:
    print(f"→ --fix 定向补缺 {len(gaps)} 只...", flush=True)
    r = subprocess.run([FY, FG, '--syms', ','.join(gaps)], capture_output=True, text=True)
    for ln in (r.stdout or '').strip().splitlines()[-3:]:
        print("   " + ln)
    nd2, _, gaps2, bydate2, tot2, half2 = scan()
    print(f"补缺后: 缺日 {len(gaps2)} 只 / 共 {tot2} 根 (残=停牌/源无数据, 非缺陷)")
    gaps, bydate = gaps2, bydate2
    nd, tot = nd2, tot2
    half_days = half2

# === ETF 复权跳变体检: 份额折算/分红未回补 → K线假跳变(假信号) ===
_d = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=120)
_etf_jumps = _d.execute("""
    SELECT symbol, date, pc, close_qfq FROM (
      SELECT symbol, date, close_qfq,
        lag(close_qfq) OVER (PARTITION BY symbol ORDER BY date) pc,
        lag(date) OVER (PARTITION BY symbol ORDER BY date) pd
      FROM stock_daily WHERE close_qfq>0
        AND symbol IN (SELECT symbol FROM stock_basics WHERE is_etf=1 AND length(symbol)=6))
    WHERE pc>0 AND abs(close_qfq/pc-1)>0.30 AND (julianday(date)-julianday(pd))<=7
      AND date >= date((SELECT MAX(date) FROM stock_daily), '-400 day')""").fetchall()
_d.close()
if _etf_jumps:
    print(f"⚠️ ETF 复权跳变 {len(_etf_jumps)} 根 (疑似份额折算未回补 → 需 fix_etf_qfq 整段重建):")
    for r in _etf_jumps[:10]:
        print(f"   {r[0]} {r[1]} {r[2]}->{r[3]}")
else:
    print("ETF 复权: 无跳变 ✓")

if half_days:
    print(f"ALERT: 检测到 {len(half_days)} 个半空日(当日个股<中位50%) {[(d, n) for d, n in half_days]} "
          f"— 日线未拉全/数据源未发布, 下游信号不可信")
if alert_dates:
    print(f"ALERT: 检测到 {len(alert_dates)} 个系统性缺日 {sorted(alert_dates)} — 日更故障, 需补数据+查因")
if half_days or alert_dates:
    sys.exit(2)
print("OK: 无系统性缺日 (残余为停牌/源无数据, 正常)")
