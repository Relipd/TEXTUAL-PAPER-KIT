# -*- coding: utf-8 -*-
"""采集流水线：plan（归类）→ fetch（下载）→ slice（截取关键页），附 status / import。

阶段之间以 SQLite 任务表解耦，任一阶段可单独重跑、断点续传、只补失败项。
人员抽取与主库装载分别由 :mod:`audit_kit.reextract` 与 :mod:`audit_kit.store` 负责。

用法::

    python -m audit_kit.pipeline plan  --workers 3   # 检索并归类公告清单
    python -m audit_kit.pipeline fetch --workers 16  # 并发下载原始 PDF
    python -m audit_kit.pipeline slice --workers 8   # 截取关键页 + 导出文本
    python -m audit_kit.pipeline status              # 各阶段进度
    python -m audit_kit.pipeline import              # 导入历史 notices.jsonl
    python -m audit_kit.pipeline run                 # 依次跑完 plan/fetch/slice
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed

from . import config as cfg
from . import crawl as an
from . import paths

YEARS = cfg.YEARS      # 采集覆盖的披露年（与 config 同口径）
KWS = cfg.KEYWORDS     # 检索关键词

DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    code TEXT, name TEXT, title TEXT, date TEXT, disc_year INTEGER,
    type TEXT, url TEXT UNIQUE,
    status TEXT DEFAULT 'new',   -- new/fetched/sliced/parsed/failed
    tries INTEGER DEFAULT 0,
    raw TEXT, pdf TEXT, txt TEXT, pages TEXT,
    a_partner TEXT, a_signer TEXT, a_reviewer TEXT,
    h_partner TEXT, h_signer TEXT, h_reviewer TEXT,
    audit_year INTEGER, firm_notice TEXT, firm_official TEXT,
    prev_firm TEXT, tenure TEXT, fee TEXT,
    src TEXT, note TEXT, ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_st ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_code ON tasks(code, disc_year);
"""


def connect():
    paths.ensure(paths.DATA, paths.RAW_DIR, paths.PDF_DIR, paths.TXT_DIR)
    c = sqlite3.connect(str(paths.PIPELINE_DB), timeout=60)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=NORMAL')
    c.executescript(DDL)
    return c


def uni():
    return an.load_universe()


# ---------------- 1. plan：逐公司检索 + 归类 ----------------
def cmd_plan(limit=0, workers=3):
    """逐公司检索（已验证唯一可靠路径）；已入库公司自动跳过，支持增量"""
    u = uni()
    active = load_active_codes()
    codes = [c for c in sorted(u) if c in active]
    conn = connect()
    have = {r[0] for r in conn.execute('SELECT DISTINCT code FROM tasks')}
    todo = [c for c in codes if c not in have]
    if limit:
        todo = todo[:limit]
    print(f'名单 {len(codes)}，已有 {len(have)}，待检索 {len(todo)}，并发 {workers}', flush=True)
    t0 = time.time()
    n_new = 0
    lock_conn = conn

    def work(code):
        try:
            ns = an.fetch_notices(code, kws=('会计师事务所',))
            hits = [n for n in ns if an.classify(n['title']) != '其他']
            if len(hits) < 3:
                ns += an.fetch_notices(code, kws=('审计机构',))
        except Exception as e:
            return code, [], str(e)[:40]
        out = []
        for n in ns:
            if re.search(r'(受托管理|受托管理人|临时受托)', n['title']):
                continue
            typ = an.classify(n['title'])
            if typ == '其他':
                continue
            out.append((code, re.sub(r'\s+', '', u.get(code, code)), n['title'], n['date'],
                        int(n['date'][:4]), typ, n['url']))
        return code, out, ''

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, c) for c in todo]
        for fu in as_completed(futs):
            code, rows, err = fu.result()
            if rows:
                lock_conn.executemany(
                    """INSERT OR IGNORE INTO tasks(code,name,title,date,disc_year,type,url,ts)
                       VALUES(?,?,?,?,?,?,?,datetime('now','localtime'))""", rows)
                n_new += len(rows)
            done += 1
            if done % 100 == 0:
                lock_conn.commit()
                el = time.time() - t0
                print(f'  {done}/{len(todo)} 新增 {n_new} 用时 {el/60:.1f}m '
                      f'ETA {el/done*(len(todo)-done)/60:.0f}m', flush=True)
    lock_conn.commit()
    print(f'plan 完成：新增 {n_new} 条，用时 {(time.time()-t0)/60:.1f} 分钟')
    cmd_status()
    conn.close()


def load_active_codes():
    """FIN_Audit 中「披露年在采集范围内且当年有审计机构」的公司代码。"""
    path = paths.FIN_AUDIT_CSV
    if not path.exists():
        raise FileNotFoundError(
            f'找不到审计名单 CSV：{path}（第三方数据，不随仓库分发；'
            '可用环境变量 AUDIT_KIT_FIN_AUDIT_CSV 指定）')
    years = {'2020', '2021', '2022', '2023', '2024', '2025'}
    act = set()
    with open(path, encoding='utf-8-sig', newline='') as f:
        rd = csv.reader(f)
        next(rd); next(rd); next(rd)
        for r in rd:
            if len(r) > 6 and r[2][:4] in years and r[6]:
                act.add(r[0])
    return act


# ---------------- 2. fetch：下载 ----------------
def _download(task):
    tid, url, raw = task
    if os.path.exists(raw) and os.path.getsize(raw) > 2000:
        return tid, raw, ''
    try:
        r = an.sess().get(url, timeout=90)
        if len(r.content) > 2000 and r.content[:4] == b'%PDF':
            with open(raw, 'wb') as f:
                f.write(r.content)
            return tid, raw, ''
        return tid, '', f'http:{len(r.content)}'
    except Exception as e:
        return tid, '', str(e)[:60]


def cmd_fetch(workers=16, limit=0):
    conn = connect()
    q = "SELECT id,url,code,disc_year FROM tasks WHERE status='new'"
    if limit:
        q += f' LIMIT {limit}'
    rows = conn.execute(q).fetchall()
    print(f'待下载 {len(rows)} 条，并发 {workers}', flush=True)
    t0 = time.time()
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = []
        for tid, url, code, y in rows:
            raw = str(paths.RAW_DIR / f'{code}_{y}_{os.path.basename(url).replace(".PDF","")}.pdf')
            futs.append(ex.submit(_download, (tid, url, raw)))
        for i, fu in enumerate(as_completed(futs), 1):
            tid, raw, err = fu.result()
            if raw:
                conn.execute("UPDATE tasks SET status='fetched',raw=?,ts=datetime('now','localtime') WHERE id=?",
                             (raw, tid))
                ok += 1
            else:
                conn.execute("UPDATE tasks SET tries=tries+1,note=?,status=CASE WHEN tries>=2 THEN 'failed' ELSE 'new' END WHERE id=?",
                             (err, tid))
                fail += 1
            if i % 200 == 0:
                conn.commit()
                print(f'  {i}/{len(rows)} 成功 {ok} 失败 {fail} {time.time()-t0:.0f}s', flush=True)
    conn.commit()
    print(f'fetch 完成：成功 {ok}，失败 {fail}，用时 {(time.time()-t0)/60:.1f} 分钟')
    conn.close()


# ---------------- 3. slice：截取关键页 ----------------
def _do_slice(args):
    tid, raw, code, name, year, title = args
    try:
        co = re.sub(r'\s+', '', name or code)
        stem = an.safe_filename(f'{co}-{code}-{year}-{title}')
        out_pdf = paths.PDF_DIR / str(year) / (stem + '.pdf')
        out_txt = paths.TXT_DIR / str(year) / (stem + '.txt')
        paths.ensure(out_pdf.parent, out_txt.parent)
        pages, whole = an.slice_key_pages(raw, str(out_pdf), str(out_txt))
        return tid, str(out_pdf), str(out_txt), f'{len(pages)}页' + ('(整篇)' if whole else ''), ''
    except Exception as e:
        return tid, '', '', '', str(e)[:80]


def cmd_slice(workers=8, limit=0):
    conn = connect()
    q = "SELECT id,raw,code,name,disc_year,title FROM tasks WHERE status='fetched'"
    if limit:
        q += f' LIMIT {limit}'
    rows = conn.execute(q).fetchall()
    print(f'待截取 {len(rows)} 条，进程 {workers}', flush=True)
    t0 = time.time()
    ok = fail = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_do_slice, r) for r in rows]
        for i, fu in enumerate(as_completed(futs), 1):
            tid, pdf, txt, pages, err = fu.result()
            if pdf:
                conn.execute("UPDATE tasks SET status='sliced',pdf=?,txt=?,pages=?,ts=datetime('now','localtime') WHERE id=?",
                             (pdf, txt, pages, tid))
                ok += 1
            else:
                conn.execute("UPDATE tasks SET status='failed',note=? WHERE id=?", (err, tid))
                fail += 1
            if i % 200 == 0:
                conn.commit()
                print(f'  {i}/{len(rows)} 成功 {ok} 失败 {fail} {time.time()-t0:.0f}s', flush=True)
    conn.commit()
    print(f'slice 完成：成功 {ok}，失败 {fail}，用时 {(time.time()-t0)/60:.1f} 分钟')
    conn.close()


def cmd_import():
    """把历史 notices.jsonl 导入任务表，按已有文件推断状态"""
    src = paths.DATA / 'notices.jsonl'
    if not os.path.exists(src):
        print('无历史数据')
        return
    c = connect()
    n = 0
    for ln in open(src, encoding='utf-8'):
        ln = ln.strip()
        if not ln:
            continue
        r = json.loads(ln)
        pdf, txt = r.get('file') or '', r.get('text') or ''
        if pdf and txt and os.path.exists(pdf) and os.path.exists(txt):
            st = 'parsed' if r.get('src') not in ('none', 'dlfail', 'error', '') else 'sliced'
        elif pdf and os.path.exists(pdf):
            st = 'sliced'
        else:
            st = 'new'
        raw = str(paths.RAW_DIR / os.path.basename(pdf)) if pdf else ''
        if st == 'new' and r.get('url'):
            aid = os.path.basename(r['url']).replace('.PDF', '')
            cand = str(paths.RAW_DIR / f"{r['code']}_{r['year']}_{aid}.pdf")
            if os.path.exists(cand):
                raw, st = cand, 'fetched'
        c.execute("""INSERT OR IGNORE INTO tasks(code,name,title,date,disc_year,type,url,status,
                     raw,pdf,txt,pages,a_partner,a_signer,a_reviewer,h_partner,h_signer,h_reviewer,
                     audit_year,firm_notice,prev_firm,tenure,fee,src,note,ts)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))""",
                  (r['code'], r.get('name'), r.get('title'), r.get('date'),
                   int(r['year']) if str(r.get('year', '')).isdigit() else None,
                   r.get('type'), r.get('url'), st, raw, pdf, txt, r.get('pages'),
                   r.get('partner'), r.get('signer'), r.get('reviewer'),
                   r.get('h_partner'), r.get('h_signer'), r.get('h_reviewer'),
                   int(r['audit_year']) if str(r.get('audit_year', '')).isdigit() else None,
                   r.get('firm_notice'), r.get('prev_firm'), r.get('tenure'), r.get('fee'),
                   r.get('src'), r.get('note')))
        n += 1
    c.commit()
    print('导入', n, '条历史记录')
    cmd_status()
    c.close()


def cmd_status():
    c = connect()
    for st, n in c.execute('SELECT status, COUNT(*) FROM tasks GROUP BY status'):
        print(f'  {st:8} {n}')
    print('  合计', c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0])
    print('  公司数', c.execute('SELECT COUNT(DISTINCT code) FROM tasks').fetchone()[0])
    c.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog='audit_kit.pipeline',
        description='采集流水线：plan（归类）→ fetch（下载）→ slice（截取），另含 status / import')
    ap.add_argument('cmd', choices=['plan', 'fetch', 'slice', 'status', 'run', 'import'])
    ap.add_argument('--workers', type=int, default=0)
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'import':
            cmd_import()
        elif a.cmd == 'plan':
            cmd_plan(a.limit, a.workers or 3)
        elif a.cmd == 'fetch':
            cmd_fetch(a.workers or 16, a.limit)
        elif a.cmd == 'slice':
            cmd_slice(a.workers or 8, a.limit)
        elif a.cmd == 'status':
            cmd_status()
        else:
            cmd_plan(0, 3)
            cmd_fetch(16)
            cmd_slice(8)
    except FileNotFoundError as e:
        print(f'错误：{e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
