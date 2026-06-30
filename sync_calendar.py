#!/usr/bin/env python3
"""从深交所 API 同步交易日历到本地 DB"""
import json
import sqlite3
import sys
import urllib.request
from datetime import date

DB_PATH = "/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db"
SZSE_API = "http://www.szse.cn/api/report/exchange/onepersistenthour/monthList?month={year}-{month:02d}"


def fetch_month(year: int, month: int) -> list[tuple[str, int]]:
    """返回 [(date_str, is_trading), ...]"""
    url = SZSE_API.format(year=year, month=month)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return [(d["jyrq"], 1 if d["jybz"] == "1" else 0) for d in data.get("data", [])]


def sync(months_back: int = 3, months_forward: int = 6):
    """同步过去 N 月 + 未来 N 月"""
    today = date.today()
    conn = sqlite3.connect(DB_PATH)
    total = 0
    for offset in range(-months_back, months_forward + 1):
        y = today.year
        m = today.month + offset
        while m < 1:
            y -= 1; m += 12
        while m > 12:
            y += 1; m -= 12
        try:
            rows = fetch_month(y, m)
        except Exception as e:
            print(f"  {y}-{m:02d} 拉取失败: {e}", file=sys.stderr)
            continue
        for d, t in rows:
            conn.execute(
                "INSERT OR REPLACE INTO trade_calendar (date, is_trading) VALUES (?, ?)",
                (d, t),
            )
        conn.commit()
        tradedays = sum(1 for _, t in rows if t)
        total += len(rows)
        print(f"  {y}-{m:02d}: {len(rows)}天, {tradedays}交易日")
    conn.close()
    print(f"共同步 {total} 条")


if __name__ == "__main__":
    sync()
