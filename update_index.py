#!/usr/bin/env python3
"""update_index.py — 指数日线日更(腾讯源, 只补缺失)
覆盖: 上证指数/深证成指/创业板指/科创50/沪深300/中证500/上证50
口径: 指数无复权, close_qfq=close; volume=手(腾讯指数day第6列)
腾讯index day行格式: [date, open, close, high, low, volume, ...] — 注意与股票[fqkline]同构
用法: python3 update_index.py [--full]  # --full全量重拉近800根
"""
import json
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime

DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
TODAY = date.today().isoformat()

# symbol → 腾讯代码 (沿用库内既有后缀风格 .SH/.SZ)
INDEXES = {
    '000001.SH': 'sh000001',   # 上证指数
    '399001.SZ': 'sz399001',   # 深证成指
    '399006.SZ': 'sz399006',   # 创业板指
    '000688.SH': 'sh000688',   # 科创50
    '000300.SH': 'sh000300',   # 沪深300
    '000905.SH': 'sh000905',   # 中证500
    '000016.SH': 'sh000016',   # 上证50
}
NAMES = {
    '000001.SH': '上证指数', '399001.SZ': '深证成指', '399006.SZ': '创业板指',
    '000688.SH': '科创50', '000300.SH': '沪深300', '000905.SH': '中证500',
    '000016.SH': '上证50',
}


def tencent_index_kline(txcode, n=800):
    """腾讯指数日K: [date, open, close, high, low, volume]"""
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={txcode},day,,,{n},day")
    r = subprocess.run(['curl', '-sL', '-m', '15', '--noproxy', '*', url,
                        '-H', 'Referer: https://gu.qq.com/'], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        data = d.get('data', {}).get(txcode, {})
        return data.get('day') or []
    except Exception:
        return []


def main():
    full = '--full' in sys.argv
    conn = sqlite3.connect(DB, timeout=60)
    cur = conn.cursor()
    total_new = 0
    report = []
    for sym, txcode in INDEXES.items():
        last = cur.execute("SELECT MAX(date) FROM stock_daily WHERE symbol=?", (sym,)).fetchone()[0]
        if last and not full:
            # 只补缺失: 从最后日期次日起拉(余量3天防缺口)
            n = 10
        else:
            n = 800
        rows = tencent_index_kline(txcode, n=n)
        if not rows:
            report.append(f"{sym}: 拉取失败")
            continue
        inserted = 0
        for row in rows:
            d, o, c, h, l = row[0], float(row[1]), float(row[2]), float(row[3]), float(row[4])
            v = float(row[5]) if len(row) > 5 and row[5] else 0.0
            if d == last:
                continue
            if last and d <= last and not full:
                continue
            # 指数无复权: close_qfq=close; 成交额腾讯指数日线不提供, amount/turnover留空(前端显示0, 与ETF一致)
            cur.execute(
                "INSERT OR REPLACE INTO stock_daily "
                "(symbol,date,open,high,low,close,volume,close_qfq,open_qfq,high_qfq,low_qfq,update_time) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sym, d, o, h, l, c, v, c, o, h, l,
                 datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            inserted += 1
        # basics登记(指数is_etf=2: 0=股票/1=ETF/2=指数, close=最新)
        if rows:
            lr = rows[-1]
            cur.execute(
                "INSERT OR REPLACE INTO stock_basics (symbol,date,name,close,mktcap,nmc,updated_at,is_etf) "
                "VALUES (?,?,?,?,?,?,datetime('now','localtime'),2)",
                (sym, lr[0], NAMES[sym], float(lr[2]), 0, 0))
        total_new += inserted
        new_last = cur.execute("SELECT MAX(date) FROM stock_daily WHERE symbol=?", (sym,)).fetchone()[0]
        report.append(f"{sym}({NAMES[sym]}): 补{inserted}行 → {new_last}")
        time.sleep(0.3)
    conn.commit()
    conn.close()
    print(f"指数日更完成: 新增{total_new}行")
    for r in report:
        print(" ", r)


if __name__ == '__main__':
    main()
