#!/usr/bin/env python3
"""缠论信号按板块聚合 — 每天买卖信号按概念板块总结
用法: signal_board_summary.py [日期]  (默认最新信号日; 可传多个日期)
"""
import sqlite3, sys, os
from collections import defaultdict

PICKS = '/home/ubuntu/databases/trend_picks.db'
CONCEPT = '/home/ubuntu/databases/概念映射.db'

# 板块显示名称美化: 概念板块名后缀"概念"去掉
def pretty(c):
    return c[:-2] if c.endswith('概念') else c

def summarize(date):
    pc = sqlite3.connect(PICKS)
    cc = sqlite3.connect(CONCEPT)
    try:
        cc.execute("SELECT 1 FROM concept_members LIMIT 1")
    except Exception:
        print(f"⚠️ 概念映射表不存在: 先跑 fetch_concepts.py")
        return
    # 当日信号
    sigs = pc.execute(
        "SELECT symbol,name,signal_type,strength FROM chanlun_signals "
        "WHERE signal_date=? AND status='ok'", (date,)).fetchall()
    if not sigs:
        print(f"📭 {date} 无信号")
        return
    # 板块映射(每股→多个概念)
    mem = cc.execute("SELECT code,concept FROM concept_members").fetchall()
    cmap = defaultdict(set)
    for code, concept in mem:
        cmap[code].add(concept)
    # 聚合: 板块 → {类型: [(名称,强度)...]}
    agg = defaultdict(lambda: defaultdict(list))
    buy_types = {'一买', '二买', '三买'}
    for sym, name, stype, strength in sigs:
        sym = sym.zfill(6)
        for concept in cmap.get(sym, []):
            agg[concept][stype].append((name, strength))
    if not agg:
        print(f"📭 {date} 信号无板块映射")
        return
    # 非题材杂板块黑名单(涨停统计/交易概念, 无聚集意义)
    BLACKLIST = {'昨日涨停', '昨日涨停_含一字', '昨日首板', '昨日连板', '东方财富热股',
                 '融资融券', '转融券标的', '机构重仓', '基金重仓', '深股通', '沪股通',
                 'MSCI中国', '标准普尔', '富时罗素', 'AH股', '破净股', '低价股', '高送转',
                 '预盈预增', '预亏预减', '区块链', '次新股', '壳资源', 'ST股', '百元股'}
    rows = []
    for concept, by_type in agg.items():
        if concept in BLACKLIST:
            continue
        n_buy = sum(len(v) for k, v in by_type.items() if k in buy_types)
        n_sell = sum(len(v) for k, v in by_type.items() if k not in buy_types)
        strong_n = sum(1 for v in by_type.values() for _, s in v if s == 'strong')
        total = n_buy + n_sell
        rows.append((n_buy, total, n_sell, strong_n, concept, by_type))
    rows.sort(key=lambda r: (-r[0], -r[1]))  # 按买入信号数优先
    out = [f"━━━ 📊 缠论板块信号 {date} ━━━", ""]
    shown = 0
    for n_buy, total, n_sell, strong_n, concept, by_type in rows:
        if total < 3:
            continue  # ≥3信号才显示(1-2只无聚集意义)
        shown += 1
        parts = []
        for t in ['一买', '二买', '三买', '一卖', '二卖', '三卖']:
            if t in by_type:
                names = [f"{n}{'⭐' if s=='strong' else ''}" for n, s in by_type[t]]
                parts.append(f"{t}{len(by_type[t])}({','.join(names[:4])})")
        strong_tag = f" 强{strong_n}" if strong_n else ""
        out.append(f"▍{pretty(concept)} (买{n_buy} 卖{n_sell}{strong_tag})")
        out.append("  " + " ｜ ".join(parts))
        out.append("")
    if shown == 0:
        print(f"📭 {date} 无≥3信号的板块聚集")
        return
    print("\n".join(out))

if __name__ == '__main__':
    dates = sys.argv[1:] or None
    if dates:
        for d in dates:
            summarize(d)
            print()
    else:
        pc = sqlite3.connect(PICKS)
        last = pc.execute("SELECT MAX(signal_date) FROM chanlun_signals WHERE status='ok'").fetchone()[0]
        pc.close()
        summarize(last)
