#!/usr/bin/env python3
"""verify_unknowns_sina.py — 62条未知用新浪1023根复核(覆盖~4.2年, 2022-06之前仍缺的放弃)"""
import json
import subprocess
import time

vd = json.load(open('/home/ubuntu/Sequoia-X-a/data/verify_volume_verdict.json'))
unks = [v for v in vd if v['v'] == '未知' and 'em' not in v]
syms = sorted({v['symbol'] for v in unks})
by_sym = {}
for v in unks:
    by_sym.setdefault(v['symbol'], {})[v['date']] = v['db']

def sina_sym(c):
    return ('sh' if c[0] in '56' else 'sz') + c

stats = {'错位': 0, '正确': 0, '未知': 0}
for i, sym in enumerate(syms):
    url = (f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
           f"?symbol={sina_sym(sym)}&scale=240&ma=no&datalen=1023")
    r = subprocess.run(['curl', '-sL', '-m', '20', '--noproxy', '*', url,
                        '-H', 'Referer: https://finance.sina.com.cn'], capture_output=True, text=True)
    try:
        arr = json.loads(r.stdout)
        data = {d['day']: float(d['volume']) for d in arr}
    except Exception:
        data = {}
    time.sleep(0.4)
    for date, dbval in by_sym[sym].items():
        raw = data.get(date)
        if raw is None or raw <= 0:
            stats['未知'] += 1
            vd.append({'symbol': sym, 'date': date, 'db': dbval, 'v': '未知', 'src': 'sina2'})
            continue
        as_hand = raw / 100.0
        r1 = dbval / raw
        r2 = dbval / as_hand if as_hand else 9
        if abs(r1 - 1) < 0.25:
            tag = '错位'
        elif abs(r2 - 1) < 0.25:
            tag = '正确'
        else:
            tag = '未知'
        stats[tag] += 1
        vd.append({'symbol': sym, 'date': date, 'db': dbval, 'sina_raw': raw,
                   'ratio': round(r1 if tag == '错位' else r2, 3), 'v': tag, 'src': 'sina2'})
    if (i + 1) % 10 == 0:
        print(f'{i+1}/{len(syms)} {stats}', flush=True)

json.dump(vd, open('/home/ubuntu/Sequoia-X-a/data/verify_volume_verdict.json', 'w'), ensure_ascii=False)
print('最终:', stats)
