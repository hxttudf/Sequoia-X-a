#!/usr/bin/env python3
"""拉取东财全部概念板块成分 → 个股→概念映射表(concept_members)"""
import sqlite3, json, time, urllib.request

DB = '/home/ubuntu/databases/概念映射.db'
HDRS = {'Referer': 'https://quote.eastmoney.com/', 'User-Agent': 'Mozilla/5.0'}

def fetch(url, retries=3):
    """用curl --noproxy拉取(urllib直连东财被断连, curl带Referer稳定)"""
    import subprocess
    for attempt in range(retries):
        try:
            r = subprocess.run(
                ["curl", "-sL", "-m", "15", "--noproxy", "*", url,
                 "-H", "Referer: https://quote.eastmoney.com/",
                 "-H", "User-Agent: Mozilla/5.0"],
                capture_output=True, timeout=20)
            if r.returncode != 0 or not r.stdout:
                raise ConnectionError(f"curl exit {r.returncode}")
            return json.loads(r.stdout.decode('utf-8'))
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))

def main():
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS concepts (code TEXT PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS concept_members (code TEXT, concept TEXT, name TEXT, PRIMARY KEY(code, concept))")
    conn.execute("CREATE TABLE IF NOT EXISTS fetch_log (concept TEXT, ts TEXT, n INT)")
    # 1) 板块列表(分页拉全504个)
    boards = []
    for pn in range(1, 12):
        d = fetch(f"https://push2delay.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:3&fields=f12,f14")
        diff = (d.get('data') or {}).get('diff') or []
        if not diff: break
        boards.extend([(x['f12'], x['f14']) for x in diff])
        time.sleep(0.3)
    print(f"板块总数: {len(boards)}", flush=True)
    conn.executemany("INSERT OR REPLACE INTO concepts VALUES (?,?)", boards)
    conn.commit()
    # 2) 每板块成分
    total_members = 0
    for i, (bk, name) in enumerate(boards):
        try:
            d = fetch(f"https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=1000&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b:{bk}&fields=f12,f14")
            diff = (d.get('data') or {}).get('diff') or []
            rows = [(x['f12'], name, x['f14']) for x in diff]
            if rows:
                conn.executemany("INSERT OR REPLACE INTO concept_members VALUES (?,?,?)", rows)
                conn.execute("INSERT INTO fetch_log VALUES (?, datetime('now','localtime'), ?)", (name, len(rows)))
                total_members += len(rows)
            conn.commit()
        except Exception as e:
            print(f"  {name}({bk}) 失败: {e}", flush=True)
        time.sleep(0.25)
        if i % 50 == 0:
            print(f"  进度 {i+1}/{len(boards)} 累计成分{total_members}", flush=True)
    conn.close()
    print(f"完成: {len(boards)}板块, {total_members}条成分")

if __name__ == '__main__':
    main()
