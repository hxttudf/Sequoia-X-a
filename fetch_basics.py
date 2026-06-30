#!/usr/bin/env python3
"""拉取全市场基本面数据到 stock_basics 表 (PE/PB/市值等)"""
import sqlite3, subprocess, json, time
from datetime import date

DB_PATH = "data/sequoia_v2.db"

def fetch_all_basics():
    conn = sqlite3.connect(DB_PATH)
    today = date.today().strftime("%Y-%m-%d")
    total = 0
    
    for page in range(1, 200):
        url = (
            "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page={page}&num=80&sort=symbol&asc=1"
            "&node=hs_a&symbol=&_s_r_a=init"
        )
        r = subprocess.run(
            ["curl", "-sL", "--connect-timeout", "8", "--max-time", "15", url],
            capture_output=True, text=True, timeout=20)
        
        try:
            stocks = json.loads(r.stdout)
        except:
            break
        
        if not stocks or not isinstance(stocks, list):
            break
        
        for s in stocks:
            code = s.get("code", "")
            if not code:
                continue
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO stock_basics "
                    "(symbol, date, name, close, pe, pb, mktcap, nmc, turnover, amount, change_pct) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        code, today,
                        s.get("name", ""),
                        float(s.get("trade", 0) or 0),
                        float(s.get("per", 0) or 0),
                        float(s.get("pb", 0) or 0),
                        float(s.get("mktcap", 0) or 0),
                        float(s.get("nmc", 0) or 0),
                        float(s.get("turnoverratio", 0) or 0),
                        float(s.get("amount", 0) or 0),
                        float(s.get("changepercent", 0) or 0),
                    ))
                total += 1
            except (ValueError, TypeError):
                continue
        
        if len(stocks) < 80:
            break
        time.sleep(0.03)
    
    conn.commit()
    conn.close()
    return total


if __name__ == "__main__":
    print("拉取全市场基本面数据...")
    t0 = time.time()
    n = fetch_all_basics()
    elapsed = time.time() - t0
    print(f"完成: {n} 只, 耗时 {elapsed:.1f}s")
