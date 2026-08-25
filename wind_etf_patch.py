#!/usr/bin/env python3
"""Wind批量补ETF当日K线(省积分): get_fund_price_indicators 一次最多50只 → 补stock_daily当天数据
只补"当天(最新交易日)在这只股票上缺失的那根K线" — 历史序列已存在, 不需重拉, 最省积分.
全市场~1580只 / 50只每批 ≈ 32次调用. 调用方式: node scripts/cli.mjs call fund_data get_fund_price_indicators
"""
import json, os, subprocess, sqlite3, sys, time
from datetime import datetime

SKILL = os.path.expanduser('~/.agents/skills/wind-mcp-skill')
DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
BATCH = 50

def wind_indicators(windcodes, indexes):
    """一次批量调get_fund_price_indicators, 返回[(windcode, {字段:值})]"""
    params = json.dumps({"windcode": ",".join(windcodes), "indexes": indexes}, ensure_ascii=False)
    r = subprocess.run(["node", "scripts/cli.mjs", "call", "fund_data", "get_fund_price_indicators", params],
                       cwd=SKILL, capture_output=True, text=True, timeout=90)
    try:
        d = json.loads(r.stdout)
        txt = d['content'][0]['text']
        data = json.loads(txt)['data']
        cols = [c['name'] for c in data['columns']]
        rows = data['rows']
        # rows 每行需含 Wind代码 字段(最后添加时对应 col)
        out = []
        for row in rows:
            rec = dict(zip(cols, row))
            # 找到Wind代码列
            wc = rec.get('Wind代码') or rec.get('股票代码') or rec.get('代码')
            out.append((wc, rec))
        return out
    except Exception as e:
        print(f"  wind调用异常: {e}", file=sys.stderr)
        return []

def wccode(code):
    return code + ('.SH' if code[0] == '5' else '.SZ')

def main():
    conn = sqlite3.connect(DB, timeout=60)
    etfs = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stock_basics WHERE is_etf=1 ORDER BY symbol")]
    print(f"ETF总数: {len(etfs)}只, 分批{BATCH}只/次, 约{(len(etfs)+BATCH-1)//BATCH}次调用")
    if not etfs:
        return
    indexes = "今日开盘价,今日最高价,今日最低价,最新成交价,成交量,最新交易日,涨跌幅,中文简称"
    done = fails = 0
    written_date = None
    for i in range(0, len(etfs), BATCH):
        batch = etfs[i:i + BATCH]
        wcs = [wccode(c) for c in batch]
        rows = wind_indicators(wcs, indexes)
        if not rows:
            fails += len(batch)
            continue
        for wc, rec in rows:
            code = (wc or '').split('.')[0]
            if not code:
                continue
            d = rec.get('最新交易日') or ''
            if len(d) == 8:
                date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            else:
                date = datetime.now().strftime('%Y-%m-%d')
            written_date = date
            try:
                o = float(rec.get('今日开盘价')); h = float(rec.get('今日最高价'))
                l = float(rec.get('今日最低价')); c = float(rec.get('最新成交价'))
                v = float(rec.get('成交量') or 0)
                # 最新交易日前复权=不复权, qfq四列=实际
                conn.execute(
                    "INSERT OR REPLACE INTO stock_daily (symbol,date,open,high,low,close,volume,amount,close_qfq,open_qfq,high_qfq,low_qfq) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (code, date, o, h, l, c, v, round(v * c, 2), c, o, h, l))
                done += 1
            except Exception as e:
                fails += 1
        conn.commit()
        print(f"  批{i//BATCH+1}/{(len(etfs)+BATCH-1)//BATCH} 累计{done}成功 {fails}失败", flush=True)
        time.sleep(0.5)
    conn.commit(); conn.close()
    print(f"✅ Wind补当日K线完成: {done}只成功, {fails}失败, 数据日期={written_date}")

if __name__ == '__main__':
    main()
