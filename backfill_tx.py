#!/usr/bin/env python3
"""用腾讯财经 API 回填 A 股日 K 数据 — 全量版"""
import subprocess
import json
import sqlite3
import time
from datetime import datetime

DB_PATH = "data/sequoia_v2.db"
SLEEP = 0.02

def curl_get(url: str) -> dict | None:
    try:
        r = subprocess.run(
            ["curl", "-sL", "--connect-timeout", "10", "--max-time", "20", url],
            capture_output=True, text=True, timeout=25
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return json.loads(r.stdout)
    except Exception:
        return None

def get_stock_list() -> list[tuple[str, str, str]]:
    """从腾讯 API 获取板块成分股列表"""
    stocks = []
    # 沪市主板 (sh)
    for page in range(0, 2000, 100):
        url = f"http://ifzq.gtimg.cn/appstock/app/rank/stockFundFlow/rank?board=sh_a&_page={page}&_limit=100"
        data = curl_get(url)
        if not data or data.get("code") != 0:
            break
        items = data.get("data", {}).get("rank", []) or data.get("data", [])
        if not items:
            break
        for item in items:
            code = item.get("code", "")
            name = item.get("name", "")
            if code:
                stocks.append((code, name, "1"))
        if len(items) < 100:
            break
        time.sleep(0.1)

    # 深市 (sz)
    for page in range(0, 2000, 100):
        url = f"http://ifzq.gtimg.cn/appstock/app/rank/stockFundFlow/rank?board=sz_a&_page={page}&_limit=100"
        data = curl_get(url)
        if not data or data.get("code") != 0:
            break
        items = data.get("data", {}).get("rank", []) or data.get("data", [])
        if not items:
            break
        for item in items:
            code = item.get("code", "")
            name = item.get("name", "")
            if code:
                stocks.append((code, name, "0"))
        if len(items) < 100:
            break
        time.sleep(0.1)

    return stocks

def fetch_kline(code: str, market: str) -> list[dict]:
    prefix = "sh" if market == "1" else "sz"
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,2000,qfq"
    data = curl_get(url)
    if not data:
        return []
    stock_key = f"{prefix}{code}"
    stock_data = data.get("data", {})
    if isinstance(stock_data, list):
        return []
    days = stock_data.get(stock_key, {}).get("qfqday", [])
    rows = []
    for d in days:
        if len(d) < 6:
            continue
        rows.append({
            "date": d[0],
            "open": float(d[1]),
            "close": float(d[2]),
            "high": float(d[3]),
            "low": float(d[4]),
            "volume": float(d[5]),
        })
    return rows

def main():
    t0 = time.time()
    print(f"[{datetime.now()}] 获取股票列表(腾讯API)...")
    all_stocks = get_stock_list()
    print(f"腾讯全量: {len(all_stocks)} 只")

    if not all_stocks:
        print("获取列表失败，退出")
        return

    conn = sqlite3.connect(DB_PATH)
    existing = set(r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stock_daily").fetchall())
    print(f"库里已有: {len(existing)} 只")

    missing = [(c, n, m) for c, n, m in all_stocks if c not in existing]
    print(f"需要回填: {len(missing)} 只")

    if not missing:
        print("全部入库，退出")
        conn.close()
        return

    total = len(missing)
    done = 0
    fails = 0

    for code, name, market in missing:
        try:
            klines = fetch_kline(code, market)
            if klines:
                conn.executemany(
                    "INSERT OR REPLACE INTO stock_daily (symbol, date, open, high, low, close, volume, turnover) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(code, k["date"], k["open"], k["high"], k["low"], k["close"],
                      k["volume"], round(k["volume"] * k["close"], 2))
                     for k in klines]
                )
                conn.commit()
                done += 1
                if done % 50 == 0:
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (total - done) / rate if rate > 0 else 0
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] {done}/{total} "
                          f"({done*100//total}%) 速度{rate:.1f}/s ETA{eta/60:.0f}min "
                          f"失败{fails}")
            else:
                fails += 1
            time.sleep(SLEEP)
        except Exception as e:
            fails += 1
            if fails <= 5:
                print(f"  [{code}] {e}")
            time.sleep(0.5)

    conn.close()
    print(f"\n完成！成功 {done} 只，失败 {fails} 只，耗时 {(time.time()-t0)/60:.1f} 分钟")

if __name__ == "__main__":
    main()
