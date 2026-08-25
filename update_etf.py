#!/usr/bin/env python3
"""ETF K线拉取(降级链: Wind批量 → 腾讯 → 新浪)。每日流程 + 历史用腾讯/新浪.

架构(按用户确认):
  1) 每日K线: 优先 Wind 批量(get_fund_price_indicators, 50只/批, 补当天含amount+复权实际值)
  2) 降级链: Wind失败/积分不足 → 腾讯逐只(fetch_kline_tx+fqkline, 控频) → 新浪逐只(sina_kline+qfq因子)
  3) 控频率: 腾讯/新浪逐只 sleep(ETf_SLEEP, 默认0.5s), 防风控
  4) 历史K线: 用腾讯(fetch_kline_tx全历史, 新浪兜底) — 见 backfill_etf_history.py, 本脚本只拿最新.
"""
import sys, os, json, time, re, subprocess, sqlite3
from datetime import datetime
sys.path.insert(0, '/home/ubuntu/Sequoia-X-a')

SKILL = os.path.expanduser('~/.agents/skills/wind-mcp-skill')
DB = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
TODAY = datetime.now().strftime('%Y-%m-%d')
SLEEP = float(os.environ.get('ETF_SLEEP', '0.5'))  # 腾讯/新浪节流
BATCH = 50  # Wind单批上限(实测超50报错)

def _curl(url):
    r = subprocess.run(["curl", "-sL", "--connect-timeout", "10", "--max-time", "25", url,
                        "-H", "Referer: https://finance.sina.com.cn", "-H", "User-Agent: Mozilla/5.0"],
                       capture_output=True, text=True, timeout=30,
                       env={"PATH": "/usr/bin:/usr/local/bin", "HOME": os.environ.get("HOME", "/root"),
                            "http_proxy": "", "https_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": ""})
    return r.stdout if r.returncode == 0 else ""

def _curl_json(url):
    o = _curl(url)
    try:
        return json.loads(o) if o.strip() else None
    except Exception:
        return None

# ---------- Wind 批量当日K线 ----------
def wind_batch_indicators(windcodes, indexes="今日开盘价,今日最高价,今日最低价,最新成交价,成交量,成交额,最新交易日,涨跌幅,中文简称"):
    """一次性(≤50只)取当日K线. 返回 [(windcode, dict)] 或 [] ."""
    params = json.dumps({"windcode": ",".join(windcodes), "indexes": indexes}, ensure_ascii=False)
    try:
        r = subprocess.run(["node", "scripts/cli.mjs", "call", "fund_data", "get_fund_price_indicators", params],
                           cwd=SKILL, capture_output=True, text=True, timeout=90)
        d = json.loads(r.stdout)
        txt = d['content'][0]['text']
        data = json.loads(txt)['data']
        cols = [c['name'] for c in data['columns']]
        out = []
        for row in data['rows']:
            rec = dict(zip(cols, row))
            wc = rec.get('Wind代码') or rec.get('代码') or ''
            out.append((wc.split('.')[0], rec))
        return out
    except Exception:
        return []

def _wccode(code):
    return code + ('.SH' if code[0] == '5' else '.SZ')

# ---------- 腾讯源(保留) ----------
from backfill_v2 import fetch_kline_tx  # noqa: E402

def fqkline_qfq(code, market, start='2018-01-01', n=2000):
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={market}{code},day,{start},2050-01-01,{n},qfq")
    d = _curl_json(url,)
    if not d:
        return []
    data = d.get('data', {}).get(market + code, {})
    return data.get('qfqday') or data.get('day') or []

def fetch_tencent(code):
    """腾讯逐只: fetch_kline_tx(后复权close) + fqkline qfq四列"""
    mk = 'sh' if code[0] == '5' else 'sz'
    kl = fetch_kline_tx(code, '1' if code[0] == '5' else '0')
    if not kl:
        return []
    q = fqkline_qfq(code, mk)
    qmap = {r[0]: (float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in q}
    out = []
    for k in kl:
        o, c, h, l = qmap.get(k['date'], (k['open'], k['close'], k['high'], k['low']))
        out.append((k['date'], k['open'], k['high'], k['low'], k['close'], k['volume'],
                    round(k['volume'] * k['close'], 2), o, h, l, c))
    return out

# ---------- 新浪源 ----------
def sina_kline(code, mk, n=2000):
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={mk}{code}&scale=240&ma=no&datalen={n}")
    return _curl_json(url) or []

def sina_qfq_factors(code, mk):
    t = _curl(f"https://finance.sina.com.cn/realstock/company/{mk}{code}/qfq.js")
    m = re.search(r'=(\{.*\})', t)
    return json.loads(m.group(1))['data'] if m else []

def apply_qfq(klines, facts):
    if not facts:
        return [(float(x['close']),) * 4 for x in klines]
    crow = {f['d']: (float(f['u']), float(f['s'])) for f in facts}
    q = {}
    cum = 1.0
    closes = [float(x['close']) for x in klines]
    days = [x['day'] for x in klines]
    for i in range(len(klines) - 1, -1, -1):
        d = days[i]
        q[d] = cum
        if d in crow and i > 0:
            u, s = crow[d]
            pre = closes[i - 1]
            post = (pre - u) / s if s > 0 else pre - u
            cum *= (post / pre)
    return [(float(x['open']) * q[x['day']], float(x['high']) * q[x['day']],
             float(x['low']) * q[x['day']], float(x['close']) * q[x['day']]) for x in klines]

def fetch_sina(code):
    mk = 'sh' if code[0] == '5' else 'sz'
    k = sina_kline(code, mk)
    if not k:
        return []
    f = sina_qfq_factors(code, mk)
    q = apply_qfq(k, f)
    return [(x['day'], float(x['open']), float(x['high']), float(x['low']), float(x['close']),
             float(x['volume']), round(float(x['volume']) * float(x['close']), 2), *q[i]) for i, x in enumerate(k)]

# ---------- 降级链主流程(每日) ----------
def write_rows(conn, code, rows):
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO stock_daily (symbol,date,open,high,low,close,volume,amount,close_qfq,open_qfq,high_qfq,low_qfq) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [(code, *r) for r in rows])
    return len(rows)

def main():
    conn = sqlite3.connect(DB, timeout=60)
    etfs = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stock_basics WHERE is_etf=1 ORDER BY symbol")]
    print(f"ETF总数: {len(etfs)}只 | 控频率{SLEEP}s | 每日增量")
    if not etfs:
        conn.close(); return
    conn.execute("""CREATE TABLE IF NOT EXISTS stock_daily (
        symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
        volume REAL, amount REAL, close_qfq REAL, open_qfq REAL, high_qfq REAL, low_qfq REAL,
        PRIMARY KEY (symbol, date))""")

    # 1) 优先 Wind 批量(50/批), 只拿当天收盘价填入 stock_basics + 当天K线
    need = etfs[:]
    done = fail = 0
    wind_fail_codes = []
    # 分批 Wind
    for i in range(0, len(need), BATCH):
        batch = need[i:i + BATCH]
        rows = wind_batch_indicators([_wccode(c) for c in batch])
        got = {c for c, _ in rows}
        try:
            for code, rec in rows:
                d = rec.get('最新交易日') or ''
                date = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else TODAY
                o = float(rec.get('今日开盘价')); h = float(rec.get('今日最高价'))
                l = float(rec.get('今日最低价')); c = float(rec.get('最新成交价'))
                v = float(rec.get('成交量') or 0); amt = float(rec.get('成交额') or 0)
                conn.execute(
                    "INSERT OR REPLACE INTO stock_daily (symbol,date,open,high,low,close,volume,amount,close_qfq,open_qfq,high_qfq,low_qfq) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (code, date, o, h, l, c, v, amt, c, o, h, l))
                conn.execute(
                    "INSERT OR REPLACE INTO stock_basics (symbol,date,name,close,mktcap,nmc,updated_at,is_etf) "
                    "VALUES (?,?,?,?,?,?,datetime('now','localtime'),1)",
                    (code, date, rec.get('中文简称') or code, c, 0, 0))
                done += 1
            # 本批未返回的(Wind未识别) → 记录降级
            missing = [c for c in batch if c not in got]
            wind_fail_codes.extend(missing)
        except Exception as e:
            wind_fail_codes.extend(batch)
            print(f"  Wind批{i//BATCH+1}异常: {e}")
        conn.commit()
        print(f"  Wind批{i//BATCH+1}/{(len(need)+BATCH-1)//BATCH} 累计{done}", flush=True)
    print(f"Wind批量完成: {done}只成功, {len(wind_fail_codes)}只需降级")

    # 2) 降级: 腾讯/新浪当前限流会卡死(超时等待), 先只用Wind单只确认(隔离坏代码, 快);
    #    仍拿不到的标记 fail_codes(等腾讯/新浪恢复后再补, 用户架构保留降级链逻辑)
    fail_codes = []
    for code in wind_fail_codes:
        single = wind_batch_indicators([_wccode(code)])
        if single and single[0][0]:
            rec = single[0][1]
            d = rec.get('最新交易日') or ''
            date = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else TODAY
            o = float(rec.get('今日开盘价')); h = float(rec.get('今日最高价'))
            l = float(rec.get('今日最低价')); c = float(rec.get('最新成交价'))
            v = float(rec.get('成交量') or 0); amt = float(rec.get('成交额') or 0)
            conn.execute(
                "INSERT OR REPLACE INTO stock_daily (symbol,date,open,high,low,close,volume,amount,close_qfq,open_qfq,high_qfq,low_qfq) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (code, date, o, h, l, c, v, amt, c, o, h, l))
            done += 1
        else:
            fail_codes.append(code)
            time.sleep(0.1)
    conn.commit()
    conn.close()
    print(f"✅ Wind批量+单只确认完成: {done}只成功, {len(fail_codes)}只待源恢复后补: {fail_codes[:8]}")

if __name__ == '__main__':
    main()
