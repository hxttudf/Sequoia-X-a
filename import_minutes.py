#!/usr/bin/env python3
"""导入分钟数据 ZIP → stock_minutes 表"""
import sqlite3
import zipfile
import csv
import sys
import os
from datetime import datetime

DB_PATH = '/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db'
ZIP_DIR = '/home/ubuntu/data/minutes'

def import_zip(zip_path, conn):
    """导入单个年度 zip"""
    year = os.path.basename(zip_path).split('_')[0]
    print(f'[{datetime.now():%H:%M:%S}] 处理 {zip_path} ...', flush=True)
    
    with zipfile.ZipFile(zip_path, 'r') as zf:
        files = zf.namelist()
        total = len(files)
        rows_total = 0
        errors = 0
        
        batch = []
        for i, fname in enumerate(files):
            try:
                # 读取 CSV (带 BOM)
                raw = zf.read(fname).decode('utf-8-sig')
                reader = csv.DictReader(raw.splitlines())
                
                for row in reader:
                    try:
                        batch.append((
                            row['代码'].strip(),
                            row['时间'].strip(),
                            float(row['开盘价']) if row['开盘价'] else None,
                            float(row['收盘价']) if row['收盘价'] else None,
                            float(row['最高价']) if row['最高价'] else None,
                            float(row['最低价']) if row['最低价'] else None,
                            float(row['成交额']) if row['成交额'] else None,
                        ))
                    except (ValueError, KeyError):
                        errors += 1
                        continue
                
                # 批量写入
                if len(batch) >= 50000:
                    conn.executemany(
                        'INSERT OR IGNORE INTO stock_minutes (symbol,datetime,open,close,high,low,amount) '
                        'VALUES (?,?,?,?,?,?,?)', batch
                    )
                    conn.commit()
                    rows_total += len(batch)
                    batch.clear()
                    
                    if (i+1) % 500 == 0:
                        print(f'  [{datetime.now():%H:%M:%S}] {i+1}/{total} csv, '
                              f'{rows_total/1e6:.1f}M rows', flush=True)
                        
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f'  Error {fname}: {e}', flush=True)
        
        # 剩余批次
        if batch:
            conn.executemany(
                'INSERT OR IGNORE INTO stock_minutes (symbol,datetime,open,close,high,low,amount) '
                'VALUES (?,?,?,?,?,?,?)', batch
            )
            conn.commit()
            rows_total += len(batch)
        
        print(f'  [{datetime.now():%H:%M:%S}] {year}: {rows_total/1e6:.1f}M rows, '
              f'{total} files, {errors} errors', flush=True)
        return rows_total

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=OFF')
    
    zips = sorted(f for f in os.listdir(ZIP_DIR) if f.endswith('.zip'))
    
    if not zips:
        print('No zip files found in', ZIP_DIR)
        conn.close()
        return
    
    print(f'找到 {len(zips)} 个 zip 文件', flush=True)
    
    total_rows = 0
    for zf in zips:
        path = os.path.join(ZIP_DIR, zf)
        total_rows += import_zip(path, conn)
    
    conn.close()
    print(f'\n[{datetime.now():%H:%M:%S}] 全部完成! 总计 {total_rows/1e6:.1f}M 行')

if __name__ == '__main__':
    main()
