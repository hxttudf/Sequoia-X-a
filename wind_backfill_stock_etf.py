#!/usr/bin/env python3
"""Wind补齐股票+ETF缺8/25和8/26的K线(用户要求: 先补今昨两天看效果).
get_stock_price_indicators 50只/批, 严格超时+拆5+单只隔离挂起.
注意: 该接口返回不复权价, close_qfq=open/high/low=close(最新交易日不复权=qfq, 与wind_patch_remaining同口径)."""
import json, os, sqlite3, subprocess, sys
from datetime import datetime

SKILL = os.path.expanduser('~/.agents/skills/wind-mcp-skill')
DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
DATES = ['2026-08-26', '2026-08-25']

def wind_call(codes, timeout=60):
    """批量查价格指标. codes: 带后缀列表. 返回 [(裸代码, rec)] 或 None."""
    params = json.dumps({"windcode": ",".join(codes[:50]),
                         "indexes": "今日开盘价,今日最高价,今日最低价,最新成交价,成交量,成交额,最新交易日,交易状态"}, ensure_ascii=False)
    try:
        r = subprocess.run(["node", "scripts/cli.mjs", "call", "stock_data", "get_stock_price_indicators", params],
                           cwd=SKILL, capture_output=True, text=True, timeout=timeout)
        d = json.loads(r.stdout)
        if d.get('isError'):
            return None
        data = json.loads(d['content'][0]['text'])['data']
        cols = [c['name'] for c in data['columns']]
        out = []
        for row in data['rows']:
            rec = dict(zip(cols, row))
            wc = (rec.get('Wind代码') or '').split('.')[0]
            if wc:
                out.append((wc, rec))
        return out
    except Exception as e:
        print(f"    [wind_call异常] {type(e).__name__}: {str(e)[:80]}", flush=True)
        return None

def wccode(code):
    """A股代码转Wind后缀."""
    c = code.split('.')[0]  # 清理脏码如000001.SH
    if c[0] in '5':
        return c + '.SH'
    if c[0] in '013689':  # 深主板/创业板/科创/北交所
        return c + ('.BJ' if c[0] == '9' or c[0] == '4' else '.SZ' if c[0] in '03' else '.SH')
    return None  # 指数等不处理

def write_row(conn, code, rec):
    """写stock_daily一行; 无行情(停牌)返回False."""
    d = str(rec.get('最新交易日') or '')
    date = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else ''
    try:
        o = float(rec.get('今日开盘价')); h = float(rec.get('今日最高价'))
        l = float(rec.get('今日最低价')); c = float(rec.get('最新成交价'))
        v = float(rec.get('成交量') or 0); amt = float(rec.get('成交额') or 0)
    except (TypeError, ValueError):
        return False
    conn.execute(
        "INSERT OR REPLACE INTO stock_daily (symbol,date,open,high,low,close,volume,amount,close_qfq,open_qfq,high_qfq,low_qfq) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (code, date, o, h, l, c, v, amt, c, o, h, l))
    return True

def main():
    conn = sqlite3.connect(DB, timeout=60)
    grand_done = grand_fail = 0
    for DATE in DATES:
        missing = [r[0].split('.')[0] for r in conn.execute(
            "SELECT DISTINCT s.symbol FROM stock_basics s WHERE s.symbol NOT LIKE '%.%' AND s.symbol NOT IN "
            "(SELECT DISTINCT symbol FROM stock_daily WHERE date=?)", (DATE,))]
        print(f"\n=== 补 {DATE}: 缺{len(missing)}只 ===", flush=True)
        if not missing:
            continue
        done = fail = 0
        fail_codes = []
        for i in range(0, len(missing), 50):
            batch_raw = [c for c in missing[i:i + 50]]
            batch = [w for w in (wccode(c) for c in batch_raw) if w]
            rows = wind_call(batch)
            rest_raw = batch_raw
            if rows:
                got = {c for c, _ in rows}
                n0 = done
                for code, rec in rows:
                    if write_row(conn, code, rec): done += 1
                rest_raw = [c for c in batch_raw if c not in got]
            conn.commit()
            # 拆5重试
            for j in range(0, len(rest_raw), 5):
                sub = rest_raw[j:j + 5]
                r5 = wind_call([wccode(c) for c in sub if wccode(c)])
                if r5:
                    got5 = {c for c, _ in r5}
                    for code, rec in r5:
                        if write_row(conn, code, rec): done += 1
                    sub = [c for c in sub if c not in got5]
                conn.commit()
                for code in sub:
                    w = wccode(code)
                    if not w: continue
                    r1 = wind_call([w], timeout=30)
                    if r1 and r1[0][0]:
                        if write_row(conn, code, r1[0][1]): done += 1
                    else:
                        fail_codes.append(code); fail += 1
                    conn.commit()
                if j % 100 == 0:
                    print(f"    进度{i+len(batch_raw)}/{len(missing)} 累计成功{done}", flush=True)
            if (i // 50) % 20 == 0:
                print(f"  批{i//50+1}/{(len(missing)+49)//50} 成功{done} 失败{fail}", flush=True)
        print(f"✅ {DATE}: 成功{done} 未补{fail}(多为停牌/退市; 前10: {fail_codes[:10]})", flush=True)
        grand_done += done; grand_fail += fail
    conn.close()
    print(f"\n🎯 全部完成: 两日合计成功{grand_done}, 未补{grand_fail}")

if __name__ == '__main__':
    main()
