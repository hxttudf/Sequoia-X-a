#!/usr/bin/env python3
"""verify_all_volume.py — 722条volume候选全量独立复核(腾讯源, 原始=手)
判定: 库内/腾讯 ≈1 → 正确(真实爆量); ≈100 → 单位错位(该修)
腾讯fqkline全历史, 每symbol拉一次, 控频0.3s, 输出数据/verify_volume_verdict.json"""
import json
import subprocess
import sys
import time

DBS = json.load(open('/home/ubuntu/Sequoia-X-a/data/history_anomaly.json'))
cands = DBS['volume']
syms = sorted({r['symbol'] for r in cands})
by_sym = {}
for r in cands:
    by_sym.setdefault(r['symbol'], {})[r['date']] = r['volume']

def tencent_sym(c):
    return ('sh' if c[0] in '56' else 'sz') + c

def tencent_daily(code, n=800):
    """腾讯日K(不复权day), volume=手"""
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={tencent_sym(code)},day,,, {n},day".replace(',day,,, ', ',day,,,'))
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={tencent_sym(code)},day,,,{n},day")
    r = subprocess.run(['curl', '-sL', '-m', '15', '--noproxy', '*', url,
                        '-H', 'Referer: https://gu.qq.com/'], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        data = d.get('data', {}).get(tencent_sym(code), {})
        rows = data.get('day') or data.get('qfqday') or []
        return {row[0]: float(row[5]) for row in rows}
    except Exception:
        return {}

verdicts = []
stats = {'错位': 0, '正确': 0, '未知': 0}
for i, sym in enumerate(syms):
    tx = tencent_daily(sym)
    time.sleep(0.3)
    for date, dbval in by_sym[sym].items():
        raw = tx.get(date)
        if raw is None or raw <= 0:
            stats['未知'] += 1
            verdicts.append({'symbol': sym, 'date': date, 'db': dbval, 'tx': raw, 'v': '未知'})
            continue
        ratio = dbval / raw
        if 0.8 <= ratio <= 1.25:
            v = '正确'
        elif 80 <= ratio <= 125:
            v = '错位'
        else:
            v = '未知'
        stats[v] += 1
        verdicts.append({'symbol': sym, 'date': date, 'db': dbval, 'tx': raw, 'ratio': round(ratio, 3), 'v': v})
    if (i + 1) % 40 == 0:
        print(f'{i+1}/{len(syms)} {stats}', flush=True)

json.dump(verdicts, open('/home/ubuntu/Sequoia-X-a/data/verify_volume_verdict.json', 'w'), ensure_ascii=False)
print('最终:', stats)
