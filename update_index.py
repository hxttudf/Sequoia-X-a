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
def _qt_index_today(txcode):
    """腾讯qt实时快照合成指数当日K(fqkline风控兜底). 返回[date,open,close,high,low]或None"""
    import subprocess as _sp
    try:
        r = _sp.run(["curl", "-sL", "-m", "12", "--noproxy", "*", f"https://qt.gtimg.cn/q={txcode}",
                     "-H", "Referer: https://gu.qq.com/"], capture_output=True)
        t = r.stdout.decode("gbk", errors="ignore").strip()
        if "=" not in t or "~" not in t:
            return None
        f = t.split('"')[1].split("~")
        if len(f) < 38:
            return None
        price, openp = float(f[3]), float(f[5])
        if price <= 0 or openp <= 0:
            return None
        hi, lo = float(f[33]), float(f[34])
        if hi < max(openp, price) or lo > min(openp, price):
            return None
        ts = f[30]  # yyyymmddHHMMSS
        d = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        return {"date": d, "open": openp, "close": price, "high": hi, "low": lo}
    except Exception:
        return None


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
            # 腾讯K线接口风控 → qt实时快照兜底(仅当日1根)
            k = _qt_index_today(txcode)
            rows = [[k['date'], k['open'], k['close'], k['high'], k['low']]] if k else []
            src_tag = '(qt兜底)' if rows else ''
        else:
            src_tag = ''
        if not rows:
            report.append(f"{sym}: 拉取失败")
            continue
        inserted = 0
        for row in rows:
            try:
                d, o, c, h, l = row[0], float(row[1]), float(row[2]), float(row[3]), float(row[4])
            except (TypeError, ValueError):
                continue  # 腾讯风控/脏行跳过
            v = float(row[5]) if len(row) > 5 and row[5] else 0.0
            if d == last:
                continue
            if last and d <= last and not full:
                continue
            # 完整性校验: high>=max(o,c) 且 low<=min(o,c) — 防列序错位入库(曾发生[open,close,high,low]被当[open,high,low,close]写入)
            if h < max(o, c) - 1e-6 or l > min(o, c) + 1e-6:
                print(f"  ⚠跳过异常行 {sym} {d}: o={o} h={h} l={l} c={c}")
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
