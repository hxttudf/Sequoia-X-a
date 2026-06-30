#!/usr/bin/env python3
"""A股日K回填：新浪列表 + 腾讯K线"""
import subprocess, json, sqlite3, time, sys, os
from datetime import datetime

DB_PATH = "data/sequoia_v2.db"
SLEEP = 0.03

def curl(url: str) -> dict | None:
    """HTTP GET with curl, bypassing system proxy (Sina/Tencent are domestic APIs)"""
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

def get_all_stocks_sina() -> list[tuple[str, str, str]]:
    """新浪分页获取全量A股"""
    stocks = []
    for page in range(1, 200):
        url = (f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"Market_Center.getHQNodeData?page={page}&num=80&sort=symbol&asc=1"
               f"&node=hs_a&symbol=&_s_r_a=init")
        data = curl(url)
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        for item in data:
            code = item.get("code", "")
            name = item.get("name", "")
            mkt = "1" if code.startswith(("6", "9")) else "0"
            if code:
                stocks.append((code, name, mkt))
        if len(data) < 80:
            break
        time.sleep(0.05)
    return stocks

def fetch_kline_tx(code: str, market: str) -> list[dict]:
    """腾讯API获取后复权日K + 前复权收盘价"""
    prefix = "sh" if market == "1" else "sz"
    base = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,640,"
    
    # 后复权（主数据）
    data = curl(base + "hfq")
    if not data or data.get("code") != 0:
        return []
    stock_key = f"{prefix}{code}"
    stock_data = data.get("data", {})
    if not isinstance(stock_data, dict):
        return []
    days = stock_data.get(stock_key, {}).get("hfqday", [])
    if not days:
        days = stock_data.get(stock_key, {}).get("day", [])
    
    # 前复权（仅取收盘价）
    qfq_close = {}
    try:
        qfq_data = curl(base + "qfq")
        if qfq_data and qfq_data.get("code") == 0:
            qfq_stock = qfq_data.get("data", {})
            if isinstance(qfq_stock, dict):
                qfq_days = qfq_stock.get(stock_key, {}).get("qfqday", [])
                for d in qfq_days:
                    if len(d) >= 3:
                        qfq_close[d[0]] = float(d[2])
    except:
        pass
    
    rows = []
    for d in days:
        if len(d) < 6:
            continue
        try:
            rows.append({
                "date": d[0], "open": float(d[1]), "close": float(d[2]),
                "high": float(d[3]), "low": float(d[4]), "volume": float(d[5]),
                "close_qfq": qfq_close.get(d[0]),  # None if unavailable
            })
        except (ValueError, IndexError):
            continue
    return rows

def main():
    t0 = time.time()
    # 写文件日志避免 nohup 缓冲问题
    logf = open("backfill_progress.log", "a", buffering=1)  # line buffered
    
    def log(msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()
    
    log("新浪获取A股列表...")
    all_stocks = get_all_stocks_sina()
    log(f"全量: {len(all_stocks)} 只")

    if not all_stocks:
        log("获取列表失败")
        logf.close()
        return

    conn = sqlite3.connect(DB_PATH)
    existing = set(r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stock_daily").fetchall())
    log(f"库里已有: {len(existing)} 只")
    
    missing = [(c, n, m) for c, n, m in all_stocks if c not in existing]
    skipped_bj = [(c, n, m) for c, n, m in missing if c.startswith(('8','4','920'))]
    missing = [(c, n, m) for c, n, m in missing if not c.startswith(('8','4','920'))]
    log(f"需回填: {len(missing)} 只 (跳过北交所{len(skipped_bj)}只)")
    
    if not missing:
        conn.close()
        log("全部入库")
        logf.close()
        return

    done = fails = 0
    total = len(missing)
    
    for code, name, market in missing:
        try:
            klines = fetch_kline_tx(code, market)
            if klines:
                conn.executemany(
                    "INSERT OR REPLACE INTO stock_daily (symbol,date,open,high,low,close,volume,turnover,close_qfq) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    [(code, k["date"], k["open"], k["high"], k["low"], k["close"],
                      k["volume"], round(k["volume"] * k["close"], 2),
                      k.get("close_qfq")) for k in klines]
                )
                conn.commit()
                done += 1
                if done % 50 == 0 or done == 1:
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (total - done) / rate if rate > 0 else 0
                    log(f"{done}/{total} ({done*100//total}%) {rate:.1f}/s ETA{eta/60:.0f}min 失败{fails}")
            else:
                fails += 1
            time.sleep(SLEEP)
        except Exception as e:
            fails += 1
            if fails <= 5:
                log(f"  [{code}] {e}")
            time.sleep(0.5)

    conn.close()
    elapsed = (time.time() - t0) / 60
    log(f"完成! 新增 {done} 只, 失败 {fails} 只, 耗时 {elapsed:.1f}min")
    logf.close()

if __name__ == "__main__":
    main()
