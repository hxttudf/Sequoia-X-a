#!/usr/bin/env python3
"""verify_unknowns_em.py — 62条未知(腾讯缺该日)用东财K线第三源复核
东财 kline 返回 volume=手"""
import json
import subprocess
import time

vd = json.load(open('/home/ubuntu/Sequoia-X-a/data/verify_volume_verdict.json'))
unks = [v for v in vd if v['v'] == '未知']
syms = sorted({v['symbol'] for v in unks})
by_sym = {}
for v in unks:
    by_sym.setdefault(v['symbol'], {})[v['date']] = v['db']

def em_secid(code):
    if code.startswith(('6', '9', '5')):
        return f"1.{code}"
    return f"0.{code}"

def em_daily(code, years=8):
    """东财日K, lmt够长; volume=f5(手)"""
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={em_secid(code)}"
           f"&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=0"
           f"&beg=20180101&end=20500101&lmt=2100")
    r = subprocess.run(['curl', '-sL', '-m', '15', '--noproxy', '*', url,
                        '-H', 'Referer: https://quote.eastmoney.com/'],
                       capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        kl = d.get('data', {}).get('klines') or []
        out = {}
        for line in kl:
            p = line.split(',')
            out[p[0]] = float(p[5])  # f56=volume(手)
        return out
    except Exception:
        return {}

stats = {'错位': 0, '正确': 0, '未知': 0}
for i, sym in enumerate(syms):
    em = em_daily(sym)
    time.sleep(0.3)
    for date, dbval in by_sym[sym].items():
        raw = em.get(date)
        if raw is None or raw <= 0:
            stats['未知'] += 1
            tag = '未知'
            rec = {'symbol': sym, 'date': date, 'db': dbval, 'em': raw, 'v': tag}
        else:
            ratio = dbval / raw
            if 0.8 <= ratio <= 1.25:
                tag = '正确'
            elif 80 <= ratio <= 125:
                tag = '错位'
            else:
                tag = '未知'
            stats[tag] += 1
            rec = {'symbol': sym, 'date': date, 'db': dbval, 'em': raw, 'ratio': round(ratio, 3), 'v': tag}
        vd.append(rec)
    if (i + 1) % 10 == 0:
        print(f'{i+1}/{len(syms)} {stats}', flush=True)

json.dump(vd, open('/home/ubuntu/Sequoia-X-a/data/verify_volume_verdict.json', 'w'), ensure_ascii=False)
print('最终(含之前23错位637正确):', stats)
