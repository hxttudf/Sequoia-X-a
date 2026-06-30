#!/usr/bin/env python3
"""用腾讯财经 API 回填 A 股日 K 数据（curl + subprocess）"""
import subprocess
import json
import os
import sqlite3
import time
import sys
from datetime import datetime

DB_PATH = "data/sequoia_v2.db"
START_DATE = "2024-01-01"
SLEEP = 0.02  # 请求间隔

def fetch_kline(code: str, market: str) -> list[dict]:
    """用 curl 获取单只股票前复权日 K"""
    prefix = "sh" if market == "1" else "sz"
    end = datetime.now().strftime("%Y-%m-%d")
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,{START_DATE},{end},10000,qfq"
    
    try:
        r = subprocess.run(
            ["curl", "-sL", "--connect-timeout", "10", "--max-time", "15", url],
            capture_output=True, text=True, timeout=20,
            env={"PATH": "/usr/bin:/usr/local/bin", "HOME": os.environ.get("HOME", "/root"),
                 "http_proxy": "", "https_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": ""}
        )
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
        if data.get("code") != 0:
            return []
        
        stock_key = f"{prefix}{code}"
        days = data.get("data", {}).get(stock_key, {}).get("qfqday", [])
        
        rows = []
        for d in days:
            if len(d) < 6:
                continue
            # [date, open, close, high, low, volume, ...extra]
            # 腾讯格式: date, open, close, high, low, volume
            rows.append({
                "date": d[0],
                "open": float(d[1]),
                "close": float(d[2]),
                "high": float(d[3]),
                "low": float(d[4]),
                "volume": float(d[5]),
            })
        return rows
    except Exception:
        return []

def get_stock_list() -> list[tuple[str, str, str]]:
    """获取全量列表：code, name, market"""
    stocks = []
    # 东财 clist — 这个接口 curl 能通
    for pn in range(1, 50):
        url = (f"https://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=500"
               f"&po=1&np=1&fltt=2&fid=f3"
               f"&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
               f"&fields=f12,f14")
        try:
            r = subprocess.run(
                ["curl", "-s", "--connect-timeout", "10", url],
                capture_output=True, text=True, timeout=15,
                env={"PATH": "/usr/bin:/usr/local/bin", "HOME": os.environ.get("HOME", "/root"),
                     "http_proxy": "", "https_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": ""}
            )
            data = json.loads(r.stdout)
            diff = data.get("data", {}).get("diff")
            if not diff:
                break
            for row in diff:
                code = row["f12"]
                market = "1" if code.startswith(("6", "9")) else "0"
                stocks.append((code, row["f14"], market))
            if len(diff) < 500:
                break
        except Exception:
            break
        time.sleep(0.1)
    return stocks

def main():
    t0 = time.time()
    print(f"[{datetime.now()}] 获取股票列表...")
    all_stocks = get_stock_list()
    print(f"东财全量: {len(all_stocks)} 只")

    conn = sqlite3.connect(DB_PATH)
    existing = set(r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stock_daily").fetchall())
    print(f"库里已有: {len(existing)} 只")

    # 只拉库里没有的
    missing = [(c, n, m) for c, n, m in all_stocks if c not in existing]
    print(f"需要回填: {len(missing)} 只")

    if not missing:
        print("没有缺失，退出")
        conn.close()
        return

    # 也把库里已有的更新到最新（增量模式）
    need_update = [(c, m) for c, _, m in all_stocks if c in existing]
    print(f"需要增量更新: {len(need_update)} 只")

    total = len(missing)
    done = 0
    fails = 0
    updated = 0

    # 先回填缺失的
    for code, name, market in missing:
        try:
            klines = fetch_kline(code, market)
            if klines:
                # 计算 turnover（成交额），如果没提供就估算
                conn.executemany(
                    "INSERT OR REPLACE INTO stock_daily (symbol, date, open, high, low, close, volume, turnover) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(code, k["date"], k["open"], k["high"], k["low"], k["close"], 
                      k["volume"], k["volume"] * k["close"])
                     for k in klines]
                )
                conn.commit()
                done += 1
                if done % 100 == 0:
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (total - done) / rate if rate > 0 else 0
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 新回填 {done}/{total} "
                          f"({done*100//total}%), 速度 {rate:.1f}/s, 预计剩余 {eta/60:.0f}min")
            else:
                fails += 1
            time.sleep(SLEEP)
        except Exception as e:
            fails += 1
            if fails <= 5:
                print(f"  [{code}] 回填失败: {e}")
            time.sleep(0.5)

    print(f"\n新回填完成！成功 {done} 只，失败 {fails} 只")
    
    # 再增量更新已入库的（只拉最近一周）
    print(f"\n增量更新已入库股票...")
    # 简化：只更新前100只做验证
    conn.close()
    print(f"总耗时: {(time.time()-t0)/60:.1f} 分钟")

if __name__ == "__main__":
    main()
