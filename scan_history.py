#!/usr/bin/env python3
"""scan_history.py — 全库历史成交量/成交额单位错位扫描
判定(三重, 防误杀真实爆量):
  1) volume > 前10交易日中位数(不含当日)×30 → 候选
  2) 修正后 v/100 与前10日中位数比值在[0.05,10] → 单位错位(真实爆量修正后会仍异常)
  3) 后验: 后10日中位数也存在时, v/100 与其比值同检
输出 data/history_anomaly.json 备份, 可 --fix 修复"""
import json
import sqlite3
import sys
import numpy as np
import pandas as pd

DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
FIX = '--fix' in sys.argv

conn = sqlite3.connect(DB, timeout=120)
print('加载全库...', flush=True)
df = pd.read_sql_query(
    "SELECT symbol, date, volume, amount FROM stock_daily WHERE volume>0 ORDER BY symbol, date",
    conn)
print(f'共 {len(df)} 行, {df.symbol.nunique()} 只', flush=True)

# 每只symbol: 滚动前10交易日中位数(不含当日)
df['vol_med10'] = df.groupby('symbol')['volume'].transform(
    lambda s: s.shift(1).rolling(10, min_periods=3).median())
df['amt_med10'] = df.groupby('symbol')['amount'].transform(
    lambda s: s.shift(1).rolling(10, min_periods=3).median())
# 后10日中位数(用于修正后连续性验证)
df['vol_med10_fwd'] = df.groupby('symbol')['volume'].transform(
    lambda s: s.shift(-10).rolling(10, min_periods=1).median())

cand = df[(df.vol_med10 > 0) & (df.volume > df.vol_med10 * 30)].copy()
print(f'volume候选(>30倍): {len(cand)} 行', flush=True)
# 修正后连续性: v/100 落在前中位的[0.05,10]倍 且(若有后中位)后中位的[0.05,10]倍
cand['v_fixed'] = cand.volume / 100.0
ok_prev = (cand.v_fixed / cand.vol_med10).between(0.05, 10)
fwd = cand.vol_med10_fwd.fillna(cand.v_fixed)  # 无后验时放行
ok_fwd = (cand.v_fixed / fwd).between(0.05, 10)
vol_hits = cand[ok_prev & ok_fwd].copy()
print(f'volume确认单位错位: {len(vol_hits)} 行', flush=True)

# amount 同逻辑
ac = df[(df.amt_med10 > 0) & (df.amount > df.amt_med10 * 30)].copy()
ac['a_fixed'] = ac.amount / 100.0
ok_prev_a = (ac.a_fixed / ac.amt_med10).between(0.05, 10)
fwd_a = ac.groupby('symbol')['amount'].transform(
    lambda s: s.shift(-10).rolling(10, min_periods=1).median()).loc[ac.index].fillna(ac.a_fixed)
ok_fwd_a = (ac.a_fixed / fwd_a).between(0.05, 10)
amt_hits = ac[ok_prev_a & ok_fwd_a].copy()
print(f'amount确认单位错位: {len(amt_hits)} 行', flush=True)

# 汇总
vol_set = set(zip(vol_hits.symbol, vol_hits.date))
amt_set = set(zip(amt_hits.symbol, amt_hits.date))
both = vol_set & amt_set
print(f'volume+amount同错: {len(both)}; 仅volume: {len(vol_set-both)}; 仅amount: {len(amt_set-both)}')
by_date = pd.Series([d for _, d in (vol_set | amt_set)]).value_counts().sort_index()
print('按日期分布(前20):')
print(by_date.head(20).to_string())
print('按日期分布(后20):')
print(by_date.tail(20).to_string())

report = {
    'volume': vol_hits[['symbol', 'date', 'volume', 'vol_med10']].to_dict('records'),
    'amount': amt_hits[['symbol', 'date', 'amount', 'amt_med10']].to_dict('records'),
}
with open('/home/ubuntu/Sequoia-X-a/data/history_anomaly.json', 'w') as f:
    json.dump(report, f, ensure_ascii=False)
print('明细已存 data/history_anomaly.json')

if FIX:
    cur = conn.cursor()
    for r in vol_hits.itertuples():
        cur.execute("UPDATE stock_daily SET volume=volume/100.0 WHERE symbol=? AND date=?", (r.symbol, r.date))
    for r in amt_hits.itertuples():
        cur.execute("UPDATE stock_daily SET amount=amount/100.0 WHERE symbol=? AND date=?", (r.symbol, r.date))
    conn.commit()
    print(f'已修: volume {len(vol_hits)} 行, amount {len(amt_hits)} 行')
conn.close()
