#!/usr/bin/env python3
"""回填 stock_daily.close_qfq — 存量数据前复权收盘价"""
import concurrent.futures
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime

DB_PATH = "data/sequoia_v2.db"

def curl(url: str) -> dict | None:
    try:
        r = subprocess.run(
            ["curl", "-sL", "--connect-timeout", "10", "--max-time", "20", url],
            capture_output=True, text=True, timeout=25,
            env={"PATH": "/usr/bin:/usr/local/bin", "HOME": os.environ.get("HOME", "/root"),
                 "http_proxy": "", "https_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": ""}
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return json.loads(r.stdout)
    except:
        return None

def fetch_qfq_for_stock(code: str) -> dict[str, float]:
    """返回 {date: close_qfq} 字典"""
    market = "1" if code.startswith(("6", "9")) else "0"
    prefix = "sh" if market == "1" else "sz"
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,640,qfq"
    
    data = curl(url)
    if not data or data.get("code") != 0:
        return {}
    
    stock_key = f"{prefix}{code}"
    stock_data = data.get("data", {})
    if not isinstance(stock_data, dict):
        return {}
    
    qfq_days = stock_data.get(stock_key, {}).get("qfqday", [])
    if not qfq_days:
        qfq_days = stock_data.get(stock_key, {}).get("day", [])
    
    result = {}
    for d in qfq_days:
        if len(d) >= 3:
            try:
                result[d[0]] = float(d[2])
            except (ValueError, IndexError):
                continue
    return result

def backfill_symbol(code, conn):
    """回填单只股票的前复权收盘价"""
    qfq_map = fetch_qfq_for_stock(code)
    if not qfq_map:
        return 0, 0
    
    # 批量 UPDATE
    updated = 0
    for date_str, qfq_close in qfq_map.items():
        conn.execute(
            "UPDATE stock_daily SET close_qfq = ? WHERE symbol = ? AND date = ?",
            (qfq_close, code, date_str)
        )
        updated += 1
    
    conn.commit()
    return len(qfq_map), updated

def main():
    t0 = time.time()
    ts = lambda: datetime.now().strftime("%H:%M:%S")
    
    conn = sqlite3.connect(DB_PATH)
    
    # 获取需要回填的股票列表（close_qfq 为 NULL 的）
    cur = conn.execute("""
        SELECT symbol, COUNT(*) as cnt 
        FROM stock_daily 
        WHERE close_qfq IS NULL 
        GROUP BY symbol
        ORDER BY cnt DESC
    """)
    to_backfill = cur.fetchall()
    print(f"[{ts()}] 需要回填: {len(to_backfill)} 只股票")
    
    if not to_backfill:
        print("全部已完成")
        conn.close()
        return
    
    total_rows = sum(r[1] for r in to_backfill)
    print(f"[{ts()}] 总行数: {total_rows}")
    
    # 并发拉取
    done = fails = 0
    total_stocks = len(to_backfill)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_qfq_for_stock, code): code 
                   for code, _ in to_backfill}
        
        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            try:
                qfq_map = future.result()
                if qfq_map:
                    # Batch UPDATE
                    batch = [(qfq_close, code, date_str) 
                             for date_str, qfq_close in qfq_map.items()]
                    conn.executemany(
                        "UPDATE stock_daily SET close_qfq = ? WHERE symbol = ? AND date = ?",
                        batch
                    )
                    conn.commit()
                    done += 1
                else:
                    fails += 1
            except Exception as e:
                fails += 1
            
            if (done + fails) % 200 == 0:
                elapsed = time.time() - t0
                rate = (done + fails) / elapsed if elapsed > 0 else 0
                eta = (total_stocks - done - fails) / rate if rate > 0 else 0
                print(f"[{ts()}] {done+fails}/{total_stocks} "
                      f"(完成{done} 失败{fails}) {rate:.1f}/s ETA{eta/60:.0f}min")
            
            time.sleep(0.02)  # 避免打爆API
    
    conn.close()
    elapsed = (time.time() - t0) / 60
    print(f"[{ts()}] 完成! {done}只成功, {fails}只失败, 耗时{elapsed:.1f}min")

if __name__ == "__main__":
    main()
