#!/usr/bin/env python3
"""拉取东财全部板块成分(概念/行业/地域三维度) → 个股→板块映射表(concept_members)
用法: fetch_concepts.py [concept|industry|region]  (默认全拉)
"""
import sqlite3, json, time, subprocess

DB = '/home/ubuntu/databases/concept_map.db'

DIMS = {
    'concept': ('m:90+t:3', '概念'),
    'industry': ('m:90+t:2', '行业'),
    'region': ('m:90+t:1', '地域'),
}

def fetch(url, retries=3):
    """用curl --noproxy拉取(urllib直连东财被断连, curl带Referer稳定)"""
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
    targets = [a for a in __import__('sys').argv[1:]] or list(DIMS.keys())
    conn = sqlite3.connect(DB)
    # concepts 加 dimension 列(存量兼容: 默认 concept)
    conn.execute("CREATE TABLE IF NOT EXISTS concepts (code TEXT PRIMARY KEY, name TEXT, dimension TEXT DEFAULT 'concept')")
    conn.execute("CREATE TABLE IF NOT EXISTS concept_members (code TEXT, concept TEXT, name TEXT, dimension TEXT DEFAULT 'concept', PRIMARY KEY(code, concept, dimension))")
    conn.execute("CREATE TABLE IF NOT EXISTS fetch_log (concept TEXT, dimension TEXT, ts TEXT, n INT)")
    try:
        conn.execute("SELECT dimension FROM concepts LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE concepts ADD COLUMN dimension TEXT DEFAULT 'concept'")
    try:
        conn.execute("SELECT dimension FROM concept_members LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE concept_members ADD COLUMN dimension TEXT DEFAULT 'concept'")
    try:
        conn.execute("SELECT dimension FROM fetch_log LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE fetch_log ADD COLUMN dimension TEXT DEFAULT 'concept'")
    total_all = 0
    for dim in targets:
        if dim not in DIMS:
            print(f"未知维度 {dim}, 可选: {list(DIMS.keys())}")
            continue
        fs, dim_label = DIMS[dim]
        print(f"=== {dim_label}板块({dim}) ===", flush=True)
        # 1) 板块列表
        boards = []
        for pn in range(1, 12):
            d = fetch(f"https://push2delay.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs={fs}&fields=f12,f14")
            diff = (d.get('data') or {}).get('diff') or []
            if not diff:
                break
            boards.extend([(x['f12'], x['f14'], dim) for x in diff])
            time.sleep(0.3)
        print(f"  板块数: {len(boards)}", flush=True)
        # 清理该维度旧数据(周更语义: 成分可能变动, 全量重写该维度)
        conn.execute("DELETE FROM concepts WHERE dimension=?", (dim,))
        conn.execute("DELETE FROM concept_members WHERE dimension=?", (dim,))
        conn.execute("DELETE FROM fetch_log WHERE dimension=?", (dim,))
        conn.executemany("INSERT OR REPLACE INTO concepts VALUES (?,?,?)", boards)
        conn.commit()
        # 2) 每板块成分
        total_members = 0
        for i, (bk, name, d2) in enumerate(boards):
            try:
                d = fetch(f"https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=1000&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b:{bk}&fields=f12,f14")
                diff = (d.get('data') or {}).get('diff') or []
                rows = [(x['f12'], name, x['f14'], dim) for x in diff]
                if rows:
                    conn.executemany("INSERT OR REPLACE INTO concept_members VALUES (?,?,?,?)", rows)
                    conn.execute("INSERT INTO fetch_log VALUES (?,?, datetime('now','localtime'), ?)", (name, dim, len(rows)))
                    total_members += len(rows)
                conn.commit()
            except Exception as e:
                print(f"  {name}({bk}) 失败: {e}", flush=True)
            time.sleep(0.25)
            if i % 50 == 0:
                print(f"  进度 {i+1}/{len(boards)} 累计成分{total_members}", flush=True)
        total_all += total_members
        print(f"  {dim_label}完成: {len(boards)}板块, {total_members}条成分", flush=True)
    conn.close()
    print(f"全部完成, 累计 {total_all} 条成分")

if __name__ == '__main__':
    main()
