#!/usr/bin/env python3
"""刷新拼音首字母映射 pinyin_map.json (股票+ETF, 基于stock_basics最新全量)
在 fetch_basics 拉取股票基础信息之后调用"""
import os, sqlite3, json, re, sys
sys.path.insert(0, '/home/ubuntu/Sequoia-X-a')
from pypinyin import lazy_pinyin  # noqa: E402

DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
OUT = '/home/ubuntu/trend-stockscope/pinyin_map.json'

SC = sqlite3.connect(DB)
# 每股取最新日期记录(不依赖统一MAX(date): 某天部分股票未写入时也能拿到全量)
rows = SC.execute(
    "SELECT b.symbol, b.name FROM stock_basics b "
    "JOIN (SELECT symbol, MAX(date) md FROM stock_basics GROUP BY symbol) x "
    "ON b.symbol=x.symbol AND b.date=x.md").fetchall()
SC.close()

pmap = {}
for sym, name in rows:
    nm = re.sub(r'^(XD|XR|DR)', '', name or '')
    py = [p for p in lazy_pinyin(nm) if p]
    initials = ''.join(p[0] for p in py if p[0].isalpha()).lower()
    full = ''.join(py).lower()
    if initials:
        pmap[sym] = {'name': nm, 'initials': initials, 'full': full}

json.dump(pmap, open(OUT, 'w'), ensure_ascii=False)
# 注意: 不打印 OUT 绝对路径, 否则 cron no-agent 投递会把真实存在的 .json 路径当附件发给用户
print(f'拼音映射已刷新: {len(pmap)} 只')
