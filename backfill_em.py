#!/usr/bin/env python3
"""用东财 API 回填 A 股日 K 数据（绕过 akshare/urllib3 的 TLS bug）"""
import http.client
import json
import sqlite3
import time
import sys
from datetime import datetime, timedelta

DB_PATH = "data/sequoia_v2.db"
START_DATE = "20240101"
BATCH_SIZE = 200
SLEEP = 0.05  # 每次请求间隔，别把东财打爆

def em_request(path: str) -> dict:
    """用 http.client 请求东财 API"""
    conn = http.client.HTTPSConnection("push2his.eastmoney.com", timeout=15)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        data = r.read().decode()
        return json.loads(data)
    finally:
        conn.close()

def get_all_stocks() -> list[tuple[str, str]]:
    """获取全量 A 股列表"""
    stocks = []
    for pn in range(1, 100):
        path = (f"/api/qt/clist/get?pn={pn}&pz=500&po=1&np=1&fltt=2"
                f"&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
                f"&fields=f12,f14")
        data = em_request(path)
        if not data.get("data") or not data["data"].get("diff"):
            break
        for row in data["data"]["diff"]:
            stocks.append((row["f12"], row["f14"]))
        if len(data["data"]["diff"]) < 500:
            break
        time.sleep(0.1)
    return stocks

def get_kline(code: str, market: str, start: str, end: str) -> list[dict]:
    """获取单只股票日 K 线"""
    secid = f"{market}.{code}"
    path = (f"/api/qt/stock/kline/get?secid={secid}"
            f"&fields1=f1,f2,f3,f4,f5,f6"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57"
            f"&klt=101&fqt=1"  # 日K, 后复权
            f"&beg={start}&end={end}&lmt=10000")
    data = em_request(path)
    if not data.get("data") or not data["data"].get("klines"):
        return []
    
    rows = []
    for line in data["data"]["klines"]:
        parts = line.split(",")
        if len(parts) >= 7:
            rows.append({
                "date": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": float(parts[5]),
                "turnover": float(parts[6]),
            })
    return rows

def main():
    print(f"[{datetime.now()}] 获取股票列表...")
    all_stocks = get_all_stocks()
    print(f"东财全量: {len(all_stocks)} 只")

    conn = sqlite3.connect(DB_PATH)
    existing = set(r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stock_daily").fetchall())
    print(f"库里已有: {len(existing)} 只")

    missing = [(c, n) for c, n in all_stocks if c not in existing]
    print(f"需要回填: {len(missing)} 只")

    if not missing:
        print("没有缺失，退出")
        conn.close()
        return

    today = datetime.now().strftime("%Y%m%d")
    total = len(missing)
    done = 0
    fails = 0

    for i in range(0, total, BATCH_SIZE):
        batch = missing[i:i + BATCH_SIZE]
        for code, name in batch:
            try:
                market = "1" if code.startswith(("6", "9")) else "0"
                klines = get_kline(code, market, START_DATE, today)
                
                if klines:
                    conn.executemany(
                        "INSERT OR REPLACE INTO stock_daily (symbol, date, open, high, low, close, volume, turnover) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [(code, k["date"], k["open"], k["high"], k["low"], k["close"], k["volume"], k["turnover"])
                         for k in klines]
                    )
                    conn.commit()
                    done += 1
                    if done % 50 == 0:
                        print(f"[{datetime.now()}] 已完成 {done}/{total} ({done*100//total}%), 失败 {fails}")
                else:
                    fails += 1
                
                time.sleep(SLEEP)
                
            except Exception as e:
                fails += 1
                if fails <= 5:
                    print(f"  [{code} {name}] 失败: {e}")
                time.sleep(1)

    conn.close()
    print(f"\n完成！成功 {done} 只，失败 {fails} 只")

if __name__ == "__main__":
    main()
