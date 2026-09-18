#!/usr/bin/env python3
"""增量更新今日A股日线 — 自动取当日日期"""
import concurrent.futures
import json
import os
import sqlite3
import sys
from datetime import date, datetime

import backfill_v2
from volume_guard import guard_row  # 成交量单位守卫(根治股/手混写)

DB_PATH = 'data/sequoia_v2.db'
TODAY = date.today().isoformat()
FAIL_LOG_DIR = 'data'


def now_str():
    """当前时间戳(拉取更新时间, 写入stock_daily.update_time)"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _qt_today_k(code, market):
    """腾讯qt实时快照合成当日K(fqkline限流时兜底; 收盘后=当日完整K). 不复权: close_qfq=close"""
    import subprocess as _sp
    prefix = "sh" if market == "1" else "sz"
    code_qt = code.split(".")[0] if "." in code else code
    url = f"https://qt.gtimg.cn/q={prefix}{code_qt}"
    try:
        r = _sp.run(["curl", "-sL", "-m", "12", "--noproxy", "*", url,
                     "-H", "Referer: https://gu.qq.com/"], capture_output=True)
        t = r.stdout.decode("gbk", errors="ignore").strip()
        if "=" not in t or "~" not in t:
            return None
        f = t.split('"')[1].split("~")
        if len(f) < 38:
            return None
        price, prev, openp = float(f[3]), float(f[4]), float(f[5])
        if price <= 0 or openp <= 0:
            return None
        # 索引(实测): [30]时间戳 [31]涨跌额 [32]涨跌幅% [33]最高 [34]最低 [35]价/量/额 [36]量(手) [37]额(万元)
        hi, lo = float(f[33]), float(f[34])
        if hi < max(openp, price) or lo > min(openp, price):
            return None  # 字段口径变化防护
        vol = float(f[6])
        amt = float(f[37]) * 1e4 if f[37] else 0.0
        return {"date": TODAY, "open": openp, "high": hi, "low": lo, "close": price,
                "volume": vol, "close_qfq": price, "amount": amt}
    except Exception:
        return None


def fetch_one(code, name, market):
    klines = backfill_v2.fetch_kline_tx(code, market)
    if klines:
        today = next((k for k in klines if k['date'] == TODAY), None)
        return code, today, klines   # 第3项=全窗口(含close_qfq), 供close_qfq自愈回写
    # fqkline失败/无今日根(限流) → 腾讯qt实时合成当日K
    k = _qt_today_k(code, market)
    return (code, k, []) if k else (code, None, [])


def main():
    t0 = datetime.now()
    ts = lambda: datetime.now().strftime('%H:%M:%S')

    print(f'[{ts()}] 获取全量A股列表...')
    all_stocks = backfill_v2.get_all_stocks_sina()
    if not all_stocks:
        # 降级: 用本地stock_basics表(上次fetch_basics更新的全量列表)
        conn0 = sqlite3.connect(DB_PATH)
        all_stocks = [(r[0], r[1], '1' if r[0].startswith(('6', '9')) else '0')
                      for r in conn0.execute("SELECT symbol, name FROM stock_basics WHERE is_etf=0")]
        conn0.close()
        print(f'新浪列表失败, 降级用本地表: {len(all_stocks)} 只')
    if not all_stocks:
        print('获取A股列表失败')
        sys.exit(1)
    print(f'全量: {len(all_stocks)} 只')

    filtered = [(c, n, m) for c, n, m in all_stocks if not c.startswith(('8', '4', '920'))]
    skipped_bj = len(all_stocks) - len(filtered)
    print(f'跳过北交所: {skipped_bj} 只')

    conn = sqlite3.connect(DB_PATH)
    existing_today = {r[0] for r in conn.execute(
        'SELECT DISTINCT symbol FROM stock_daily WHERE date = ?', (TODAY,)
    ).fetchall()}
    print(f'今日已有: {len(existing_today)} 只')

    # 只补缺失模式(默认): 跳过今天已成功的, 大幅缩短时长防cron超时
    # 全量重拉: 传 --full 参数(覆盖盘中数据场景)
    only_missing = '--full' not in sys.argv
    if only_missing:
        to_fetch = [(c, n, m) for c, n, m in filtered if c not in existing_today]
        print(f'只补缺失模式: {len(to_fetch)} 只(跳过已有{len(existing_today)})')
    else:
        to_fetch = filtered  # 每次都全量重拉当天(覆盖盘中数据, update_time记录真实拉取时刻)
        print(f'需获取: {len(to_fetch)} 只(强制刷新当天)')

    if not to_fetch:
        conn.close()
        print(f'全部完成: 总数{len(filtered)}, 跳过北交所{skipped_bj}, 今日已有{len(existing_today)}')
        return

    done = fails = 0
    failed_symbols = []
    qfq_fixed = 0
    ins_fixed = 0
    batch = []
    qfq_batch = []
    ins_batch = []   # 整根缺失补插(带OHLC+量) — UPDATE补不上缺失行, 必须INSERT

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_one, c, n, m): c for c, n, m in to_fetch}
        for future in concurrent.futures.as_completed(futures):
            try:
                code, k, klines = future.result(timeout=30)
            except Exception:
                code = futures[future]
                k, klines = None, []
            if klines:
                # 自愈(独立于当日根): ①整根缺失→INSERT补根 ②close_qfq不一致→UPDATE(前复权回溯性)
                stored = {r[0]: r[1] for r in conn.execute(
                    "SELECT date, close_qfq FROM stock_daily WHERE symbol=?", (code,))}
                for w in klines:
                    nq = w.get('close_qfq')
                    if nq is None:
                        continue
                    oq = stored.get(w['date'])
                    if oq is None:
                        # 整根缺失 → 补插(带完整OHLC+量); 防脏数据: 仅补合法根
                        if w.get('close') and w.get('open'):
                            vol = w.get('volume') or 0
                            amt = w.get('amount') or round(vol * w['close'], 2)
                            _r = (nq / w['close']) if w.get('close') else 1.0
                            ins_batch.append((code, w['date'], w['open'], w['high'], w['low'],
                                              w['close'], vol, amt, nq,
                                              round(w['open'] * _r, 3), round(w['high'] * _r, 3), round(w['low'] * _r, 3)))
                    elif abs(nq - oq) > 0.0005:
                        # 一并修 o/h/l_qfq: 除权后原只改 close_qfq → OHLC 不一致(阴/阳线错乱)
                        _r = (nq / w['close']) if w.get('close') else 1.0
                        qfq_batch.append((nq, round(w['open'] * _r, 3), round(w['high'] * _r, 3),
                                          round(w['low'] * _r, 3), code, w['date']))
            if k is None:
                fails += 1
                failed_symbols.append(code)
            else:
                # 单位守卫: 源若混入"股"单位自动÷100(腾讯本=手, 防未来源变更)
                v, _, _ = guard_row(conn, code, TODAY, k['volume'], None)
                _cq = k.get('close_qfq')
                _r = (_cq / k['close']) if (_cq and k.get('close')) else 1.0
                row = (code, TODAY, k['open'], k['high'], k['low'],
                       k['close'], v, round(v * k['close'], 2), _cq,
                       round(k['open'] * _r, 3), round(k['high'] * _r, 3), round(k['low'] * _r, 3))
                batch.append(row)
                done += 1
                if len(batch) >= 200:
                    conn.executemany(
                        'INSERT OR REPLACE INTO stock_daily '
                        '(symbol,date,open,high,low,close,volume,turnover,close_qfq,open_qfq,high_qfq,low_qfq,update_time) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        [b + (now_str(),) for b in batch]
                    )
                    if ins_batch:
                        conn.executemany(
                            'INSERT OR REPLACE INTO stock_daily '
                            '(symbol,date,open,high,low,close,volume,turnover,close_qfq,open_qfq,high_qfq,low_qfq,update_time) '
                            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            [b + (now_str(),) for b in ins_batch])
                        ins_fixed += len(ins_batch)
                        ins_batch.clear()
                    if qfq_batch:
                        conn.executemany('UPDATE stock_daily SET close_qfq=?,open_qfq=?,high_qfq=?,low_qfq=? WHERE symbol=? AND date=?', qfq_batch)
                        qfq_fixed += len(qfq_batch)
                        qfq_batch.clear()
                    conn.commit()
                    elapsed = (datetime.now() - t0).total_seconds()
                    rate = done / elapsed if elapsed > 0 else 0
                    print(f'[{ts()}] {done}/{len(to_fetch)}  '
                          f'({done*100//len(to_fetch)}%) {rate:.1f}/s  失败{fails}  修qfq{qfq_fixed}  补根{ins_fixed}')
                    batch.clear()

    if batch:
        conn.executemany(
            'INSERT OR REPLACE INTO stock_daily '
            '(symbol,date,open,high,low,close,volume,turnover,close_qfq,open_qfq,high_qfq,low_qfq,update_time) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [b + (now_str(),) for b in batch]
        )
        conn.commit()
    if ins_batch:
        conn.executemany(
            'INSERT OR REPLACE INTO stock_daily '
            '(symbol,date,open,high,low,close,volume,turnover,close_qfq,open_qfq,high_qfq,low_qfq,update_time) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [b + (now_str(),) for b in ins_batch])
        ins_fixed += len(ins_batch)
        ins_batch.clear()
        conn.commit()
    if qfq_batch:
        conn.executemany('UPDATE stock_daily SET close_qfq=? WHERE symbol=? AND date=?', qfq_batch)
        qfq_fixed += len(qfq_batch)
        qfq_batch.clear()
        conn.commit()
    if ins_fixed or qfq_fixed:
        print(f'自愈合计: 补根{ins_fixed}行, 修qfq{qfq_fixed}行')

    total_valid = len(to_fetch) if to_fetch else len(filtered)
    fail_rate = fails / total_valid if total_valid else 0.0
    # missing模式: 缺失股多为退市/长期停牌(拉不到属正常), 按绝对数判断(≤10只失败算ok)
    if only_missing:
        ok = fails <= 30
    else:
        ok = fail_rate < 0.05

    elapsed = (datetime.now() - t0).total_seconds() / 60

    # 写失败股票列表
    fail_path = os.path.join(FAIL_LOG_DIR, f'failed_stocks_{TODAY}.json')
    with open(fail_path, 'w') as f:
        json.dump({'date': TODAY, 'count': fails, 'symbols': failed_symbols}, f, ensure_ascii=False)

    # 摘要 JSON 输出（最后一行，方便 cron 解析）
    summary = {
        'date': TODAY,
        'mode': 'missing' if only_missing else 'full',
        'total': len(all_stocks),
        'bj_skip': skipped_bj,
        'existing': len(existing_today),
        'new': done,
        'fail': fails,
        'fail_rate': round(fail_rate, 4),
        'ok': ok,
        'elapsed_min': round(elapsed, 1)
    }
    print(json.dumps(summary, ensure_ascii=False))
    # 记录K线更新日志(最新交易日, 供查询端直接使用, 免每只MAX(date))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS kline_update_log (id INTEGER PRIMARY KEY AUTOINCREMENT, latest_date TEXT, updated_at TEXT, source TEXT)")
        conn.execute("INSERT INTO kline_update_log (latest_date, updated_at, source) VALUES (?, datetime('now','localtime'), 'update_daily')", (TODAY,))
        conn.commit()
    except Exception as e:
        print(f'更新日志写入失败: {e}')
    finally:
        conn.close()  # 日志写入后再关闭连接(修复 closed database)

    if not ok:
        print(f'⚠ 失败率 {fail_rate:.1%} 超标（阈值5%），退出码1')
        sys.exit(1)


if __name__ == '__main__':
    main()
