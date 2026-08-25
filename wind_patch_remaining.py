#!/usr/bin/env python3
"""健壮补齐缺8/25的ETF: Wind批量(50/批, 严格超时) → 拆5只重试 → 单只重试(严格超时)
彻底隔离"某个windcode挂起"导致整批卡死的问题. 腾讯/新浪限流, 不降级(等恢复后另一脚本补)."""
import json, os, subprocess, sqlite3, sys, time
from datetime import datetime

SKILL = os.path.expanduser('~/.agents/skills/wind-mcp-skill')
DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
TODAY = datetime.now().strftime('%Y-%m-%d')

def wind_call(windcodes, timeout=45):
    """带严格超时; 返回 dict 或 None(超时/异常). """
    params = json.dumps({"windcode": ",".join(windcodes),
                         "indexes": "今日开盘价,今日最高价,今日最低价,最新成交价,成交量,成交额,最新交易日,中文简称"}, ensure_ascii=False)
    try:
        r = subprocess.run(["node", "scripts/cli.mjs", "call", "fund_data", "get_fund_price_indicators", params],
                           cwd=SKILL, capture_output=True, text=True, timeout=timeout)
        d = json.loads(r.stdout)
        data = json.loads(d['content'][0]['text'])['data']
        cols = [c['name'] for c in data['columns']]
        out = []
        for row in data['rows']:
            rec = dict(zip(cols, row))
            wc = rec.get('Wind代码') or ''
            out.append((wc.split('.')[0], rec))
        return out
    except Exception:
        return None

def wccode(code):
    return code + ('.SH' if code[0] == '5' else '.SZ')

def write_row(conn, code, rec):
    d = rec.get('最新交易日') or ''
    date = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else TODAY
    try:
        o = float(rec.get('今日开盘价')); h = float(rec.get('今日最高价'))
        l = float(rec.get('今日最低价')); c = float(rec.get('最新成交价'))
        v = float(rec.get('成交量') or 0); amt = float(rec.get('成交额') or 0)
    except (TypeError, ValueError):
        return False  # 停牌/无行情(字段None), 跳过
    conn.execute(
        "INSERT OR REPLACE INTO stock_daily (symbol,date,open,high,low,close,volume,amount,close_qfq,open_qfq,high_qfq,low_qfq) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (code, date, o, h, l, c, v, amt, c, o, h, l))
    return True

def main():
    conn = sqlite3.connect(DB, timeout=60)
    missing = [r[0] for r in conn.execute(
        "SELECT DISTINCT s.symbol FROM stock_basics s WHERE s.is_etf=1 AND s.symbol NOT IN "
        "(SELECT DISTINCT symbol FROM stock_daily WHERE date=?)", (TODAY,))]
    print(f"待补缺8/25: {len(missing)}只")
    if not missing:
        conn.close(); return
    done = fail = 0
    fail_codes = []
    # 大投批(50) → 取到即写; 拿不到拆5 → 单只
    for i in range(0, len(missing), 50):
        batch = missing[i:i + 50]
        rows = wind_call([wccode(c) for c in batch])
        if rows:
            got = {c for c, _ in rows}
            for code, rec in rows:
                if write_row(conn, code, rec): done += 1
            rest = [c for c in batch if c not in got]
        else:
            rest = batch
        conn.commit()
        # 拆5只重试
        for j in range(0, len(rest), 5):
            sub = rest[j:j + 5]
            r5 = wind_call([wccode(c) for c in sub])
            if r5:
                got5 = {c for c, _ in r5}
                for code, rec in r5:
                    if write_row(conn, code, rec): done += 1
                sub = [c for c in sub if c not in got5]
            conn.commit()
            # 单只(严格超时) + 降级小批避免挂起
            for code in sub:
                r1 = wind_call([wccode(code)], timeout=25)
                if r1 and r1[0][0]:
                    if write_row(conn, code, r1[0][1]): done += 1
                else:
                    fail_codes.append(code); fail += 1
                conn.commit()
        print(f"  批{i//50+1}/{(len(missing)+49)//50} 累计{done}成功 {fail}失败 {len(fail_codes)}待补", flush=True)
    conn.close()
    print(f"✅ 补齐完成: {done}只成功, {fail}只仍未补(前10: {fail_codes[:10]})")

if __name__ == '__main__':
    main()
