#!/usr/bin/env python3
"""verify_samples.py — 用新浪原始股数据独立复核 scan_history 报告里的行
判定: 库内值 ≈ 新浪原始股数 → 单位错位(该修); 库内值 ≈ 原始/100 → 正确手数(真实爆量, 不修)"""
import json
import subprocess
import sys

samples = json.loads(sys.argv[1])  # [(symbol, date, dbval), ...]

def sina_sym(c):
    return ('sh' if c[0] in '56' else 'sz') + c

cache = {}
stats = {'错位': 0, '正确': 0, '未知': 0}
verdicts = []
for sym, date, dbval in samples:
    if sym not in cache:
        url = (f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
               f"?symbol={sina_sym(sym)}&scale=240&ma=no&datalen=800")
        r = subprocess.run(['curl', '-sL', '-m', '20', '--noproxy', '*', url,
                            '-H', 'Referer: https://finance.sina.com.cn'],
                           capture_output=True, text=True)
        try:
            cache[sym] = {d['day']: float(d['volume']) for d in json.loads(r.stdout)}
        except Exception:
            cache[sym] = {}
    raw = cache[sym].get(date)
    if raw is None:
        stats['未知'] += 1
        verdicts.append((sym, date, dbval, None, '未知(新浪无此日)'))
        continue
    as_hand = raw / 100.0
    r1 = dbval / raw if raw else 9     # ≈1 → 库=原始股 → 错位
    r2 = dbval / as_hand if as_hand else 9  # ≈1 → 库=手 → 正确
    if abs(r1 - 1) < 0.2:
        v = '错位'   # 库=原始股
    elif abs(r2 - 1) < 0.2:
        v = '正确'   # 库=手
    else:
        v = '未知'
    stats[v] += 1
    verdicts.append((sym, date, dbval, raw, v))

print('统计:', stats)
for x in verdicts:
    print(x)
