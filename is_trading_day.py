#!/usr/bin/env python3
"""判断今日是否为A股交易日 — 基于深交所官方交易日历"""
import sqlite3
import sys
from datetime import date

DB_PATH = "/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db"


def is_trading_day(d: date | None = None) -> bool:
    if d is None:
        d = date.today()

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT is_trading FROM trade_calendar WHERE date = ?",
        (d.isoformat(),),
    ).fetchone()
    conn.close()

    if row is None:
        # 日历里没有 → 拉取并重试
        import subprocess, os
        subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "sync_calendar.py")],
            capture_output=True, timeout=30,
        )
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT is_trading FROM trade_calendar WHERE date = ?",
            (d.isoformat(),),
        ).fetchone()
        conn.close()

    return bool(row and row[0])


if __name__ == "__main__":
    result = is_trading_day()
    print(f"{'交易日' if result else '非交易日'}")
    sys.exit(0 if result else 1)
