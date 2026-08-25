#!/usr/bin/env python3
"""股票生命线回测v2: 对比多组"生命线规则"的区分度(在线=安全/离线=危险)
规则候选:
  A) 收盘 vs MA20
  B) 收盘 vs MA60
  C) 收盘 vs (MA20+MA60)/2
  D) 收盘 vs MA20 且 MA20>MA60 (趋势过滤: 双线多头才安全)
  E) 收盘 vs MA20 且放量确认(收盘<MA20且量>1.5×20日均量)=强危险
逐日对齐, 统计 安全/危险 的未来5日收益与上涨概率."""
import sqlite3, numpy as np
DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
FWD = 20

def load(sym, c):
    rows = c.execute("SELECT close_qfq, volume FROM stock_daily WHERE symbol=? ORDER BY date", (sym,)).fetchall()
    if len(rows) < 130:
        return None
    close = np.array([r[0] or 0 for r in rows], float)
    vol = np.array([r[1] for r in rows], float)
    return close, vol

def fut_ret(close, day):
    return (close[day + FWD] / close[day] - 1) * 100 if day + FWD < len(close) else np.nan

def run(rules, sample=800):
    c = sqlite3.connect(DB)
    syms = [r[0] for r in c.execute(
        "SELECT symbol FROM stock_daily GROUP BY symbol HAVING COUNT(*)>=160 ORDER BY RANDOM() LIMIT ?", (sample,)).fetchall()]
    # stats[rulename] = {safe:[], danger:[], strong:[]}
    stats = {rn: {'safe': [], 'danger': [], 'strong': []} for rn in rules}
    for sym in syms:
        d = load(sym, c)
        if d is None:
            continue
        close, vol = d
        n = len(close)
        def ma(w, i):
            return close[i - w + 1:i + 1].mean() if i >= w - 1 else np.nan
        def vol20(i):
            return vol[i - 19:i + 1].mean() if i >= 19 else np.nan
        for i in range(61, n - FWD):
            c20, c60 = ma(20, i), ma(60, i)
            mid = (c20 + c60) / 2 if not np.isnan(c20) and not np.isnan(c60) else np.nan
            r = fut_ret(close, i)
            if np.isnan(r):
                continue
            L = close[i]
            v20 = vol20(i)
            # 各规则判定
            for rn in rules:
                st = 'safe'
                if rn == 'A': st = 'safe' if close[i] >= ma(20, i) else 'danger'
                elif rn == 'B': st = 'safe' if close[i] >= ma(60, i) else 'danger'
                elif rn == 'C': st = 'safe' if close[i] >= mid else 'danger'
                elif rn == 'D':
                    if close[i] >= ma(20, i) and ma(20, i) > ma(60, i): st = 'safe'
                    else: st = 'danger'
                elif rn == 'E':
                    if close[i] < ma(20, i) and vol[i] > 1.5 * v20: st = 'strong'
                    elif close[i] >= ma(20, i): st = 'safe'
                    else: st = 'danger'
                if not np.isnan(r):
                    stats[rn][st].append(r)
    c.close()
    out = {}
    for rn, d in stats.items():
        out[rn] = {}
        for k, v in d.items():
            if v:
                out[rn][k] = {'n': len(v), 'mean': round(float(np.mean(v)), 2),
                              'up': round(sum(1 for x in v if x > 0) / len(v) * 100, 1)}
            else:
                out[rn][k] = {'n': 0, 'mean': 0, 'up': 0}
    return out

if __name__ == '__main__':
    rules = ['A', 'B', 'C', 'D', 'E']
    names = {'A': '收盘vs MA20', 'B': '收盘vs MA60', 'C': '收盘vs(MA20+60)/2',
             'D': '收盘>MA20 且 MA20>MA60', 'E': '收盘<MA20且放量(强危险)'}
    print(f"{'规则':<26} {'状态':<8} {'样本':>7} {'5日均收益':>9} {'上涨概率':>9}")
    r = run(rules)
    for rn in rules:
        for st, lab in [('safe', '安全/线上'), ('danger', '危险/线下'), ('strong', '强危险')]:
            d = r[rn][st]
            if d['n'] > 0:
                print(f"{names[rn]:<26} {lab:<8} {d['n']:>7} {d['mean']:>9.2f} {d['up']:>9.1f}")
        print()
