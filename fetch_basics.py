#!/usr/bin/env python3
"""拉取全市场基本面数据到 stock_basics 表 (PE/PB/市值等)"""
import re
import sqlite3, subprocess, json, time
from datetime import date

DB_PATH = "data/sequoia_v2.db"

def fetch_all_basics():
    conn = sqlite3.connect(DB_PATH)
    today = date.today().strftime("%Y-%m-%d")
    total = 0
    
    def _page(p):
        """取单页; 间歇性空响应最多重试5次(防分页被瞬时空页截断)"""
        url = (
            "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page={p}&num=80&sort=symbol&asc=1"
            "&node=hs_a&symbol=&_s_r_a=init"
        )
        for _ in range(5):
            r = subprocess.run(
                ["curl", "-sL", "--connect-timeout", "8", "--max-time", "15", url],
                capture_output=True, text=True, timeout=20)
            try:
                d = json.loads(r.stdout)
            except Exception:
                d = None
            if isinstance(d, list) and len(d) > 0:
                return d
            time.sleep(0.6)
        return None

    empty_streak = 0
    for page in range(1, 200):
        stocks = _page(page)
        if stocks is None:
            empty_streak += 1
            if empty_streak >= 3:
                break
            continue
        empty_streak = 0
        for s in stocks:
            code = s.get("code", "")
            if not code:
                continue
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO stock_basics "
                    "(symbol, date, name, close, pe, pb, mktcap, nmc, turnover, amount, change_pct, updated_at, is_etf) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'),0)",
                    (
                        code, today,
                        re.sub(r'^(XD|XR|DR)', '', s.get("name", "") or ""),
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
