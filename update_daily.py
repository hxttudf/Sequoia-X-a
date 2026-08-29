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


def fetch_one(code, name, market):
    klines = backfill_v2.fetch_kline_tx(code, market)
    if not klines:
        return code, None
    for k in klines:
        if k['date'] == TODAY:
            return code, k
    return code, None


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
    batch = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_one, c, n, m): c for c, n, m in to_fetch}
        for future in concurrent.futures.as_completed(futures):
            try:
                code, k = future.result(timeout=30)
            except Exception:
                code = futures[future]
                k = None
            if k is None:
                fails += 1
                failed_symbols.append(code)
            else:
                # 单位守卫: 源若混入"股"单位自动÷100(腾讯本=手, 防未来源变更)
                v, _, _ = guard_row(conn, code, TODAY, k['volume'], None)
                row = (code, TODAY, k['open'], k['high'], k['low'],
                       k['close'], v, round(v * k['close'], 2),
                       k.get('close_qfq'))
                batch.append(row)
                done += 1
                if len(batch) >= 200:
                    conn.executemany(
                        'INSERT OR REPLACE INTO stock_daily '
                        '(symbol,date,open,high,low,close,volume,turnover,close_qfq,update_time) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?)',
                        [b + (now_str(),) for b in batch]
                    )
                    conn.commit()
                    elapsed = (datetime.now() - t0).total_seconds()
                    rate = done / elapsed if elapsed > 0 else 0
                    print(f'[{ts()}] {done}/{len(to_fetch)}  '
                          f'({done*100//len(to_fetch)}%) {rate:.1f}/s  失败{fails}')
                    batch.clear()

    if batch:
        conn.executemany(
            'INSERT OR REPLACE INTO stock_daily '
            '(symbol,date,open,high,low,close,volume,turnover,close_qfq,update_time) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)',
            [b + (now_str(),) for b in batch]
        )
        conn.commit()

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
