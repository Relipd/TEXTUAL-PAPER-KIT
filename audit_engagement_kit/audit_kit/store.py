# -*- coding: utf-8 -*-
"""SQLite 主库建表与装载：documents 表 / FTS 索引 DDL，以及 FIN_Audit 机构映射。

``build_from_tasks()`` 从采集任务表（``data/pipeline.db``）读取已完成切片/抽取的
公告，写入主库 ``documents``（基础字段 + 审计年度对应的 ``firm_official``）与
``documents_fts`` 全文索引；人员表 ``persons`` 由抽取回写阶段
（``audit_kit.reextract`` 的 apply）负责。

用法::

    python -m audit_kit build              # 由 data/pipeline.db 装载 documents
    python -m audit_kit build --limit 50   # 只装前 50 条
    python -m audit_kit build --rebuild    # 先删主库再重建
    python -m audit_kit build --dry-run    # 只统计，不写库
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

from . import paths

DDL = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    code TEXT, name TEXT,
    disc_year INTEGER,      -- 公告披露年
    audit_year INTEGER,     -- 公告所涉审计年度
    type TEXT, date TEXT, title TEXT,
    firm_notice TEXT,       -- 公告中拟聘任事务所
    firm_official TEXT,     -- 财经数据库披露的当年境内审计机构
    prev_firm TEXT,         -- 变更公告中的前任事务所
    tenure TEXT,            -- 公告披露的连续服务年限
    fee TEXT,               -- 公告披露的审计收费
    url TEXT, pdf TEXT, txt TEXT, pages TEXT,
    src TEXT, note TEXT,
    has_h INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER,
    market TEXT,            -- A / H
    role TEXT,              -- 项目合伙人 / 签字注册会计师 / 质量控制复核人
    name TEXT,
    concurrent INTEGER DEFAULT 0,  -- 合伙人兼任签字、复核人兼任A+H
  become_cpa          TEXT,
  start_listed        TEXT,
  start_firm          TEXT,
  start_company       TEXT,
  time_src            TEXT
);
CREATE INDEX IF NOT EXISTS idx_doc_code ON documents(code, disc_year);
CREATE INDEX IF NOT EXISTS idx_doc_firm ON documents(firm_official);
CREATE INDEX IF NOT EXISTS idx_person ON persons(name, role);
CREATE INDEX IF NOT EXISTS idx_person_doc ON persons(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title, content, code UNINDEXED, doc_id UNINDEXED, tokenize='unicode61'
);
"""


def load_auditor_map():
    """读取 FIN_Audit 映射：``(证券代码, 披露年) -> 境内审计机构``；文件缺失返回空表。"""
    m = {}
    p = paths.FIN_AUDIT_CSV
    if not p.exists():
        return m
    with open(p, encoding='utf-8-sig', newline='') as f:
        rd = csv.reader(f)
        next(rd); next(rd); next(rd)
        for r in rd:
            if len(r) > 6 and r[2] and r[6]:
                m[(r[0], r[2][:4])] = r[6]
    return m


def build_from_tasks(limit=0, rebuild=False, dry_run=False) -> int:
    """从任务表（status 为 sliced/parsed）装载主库 documents 表与 FTS 索引。

    :param limit: 最多装载的记录数（0=全部）
    :param rebuild: 先删除已有主库文件再重建
    :param dry_run: 只在内存库里演练，结果仅打印、不写盘
    :return: 0 成功，2 任务表缺失/读取失败
    """
    pdb = paths.PIPELINE_DB
    if not pdb.exists():
        print(f'错误：找不到采集任务表 {pdb}；请先运行采集流水线（plan → fetch → slice）。',
              file=sys.stderr)
        return 2

    q = ("SELECT code, name, disc_year, audit_year, type, date, title, firm_notice,"
         " prev_firm, tenure, fee, url, pdf, txt, pages, src, note,"
         " h_partner, h_signer, h_reviewer FROM tasks"
         " WHERE status IN ('sliced', 'parsed') ORDER BY id")
    params = ()
    if limit:
        q += ' LIMIT ?'
        params = (limit,)
    try:
        pconn = sqlite3.connect(str(pdb), timeout=60)
        try:
            rows = pconn.execute(q, params).fetchall()
        finally:
            pconn.close()
    except sqlite3.OperationalError as e:
        print(f'错误：读取采集任务表失败（{pdb}）：{e}', file=sys.stderr)
        return 2

    amap = load_auditor_map()
    if dry_run:
        conn = sqlite3.connect(':memory:')
    else:
        paths.ensure(paths.DB.parent)
        if rebuild and paths.DB.exists():
            paths.DB.unlink()
        conn = sqlite3.connect(str(paths.DB), timeout=60)
    try:
        conn.executescript(DDL)
        conn.execute('DELETE FROM documents')
        conn.execute('DELETE FROM documents_fts')
        n_h = 0
        for (code, name, disc_year, ay, typ, date, title, firm_notice, prev_firm, tenure,
             fee, url, pdf, txt, pages, src, note, h_partner, h_signer, h_reviewer) in rows:
            firm_off = amap.get((code, str(ay)), '') if ay else ''
            has_h = 1 if (h_partner or h_signer or h_reviewer) else 0
            n_h += has_h
            cur = conn.execute(
                """INSERT INTO documents(code,name,disc_year,audit_year,type,date,title,
                   firm_notice,firm_official,prev_firm,tenure,fee,url,pdf,txt,pages,src,note,has_h)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (code, name, disc_year, ay, typ, date, title,
                 firm_notice, firm_off, prev_firm, tenure, fee, url, pdf, txt, pages,
                 src, note, has_h))
            body = ''
            if txt:
                txt_path = Path(txt)
                if txt_path.is_file():
                    try:
                        with open(txt_path, encoding='utf-8', errors='ignore') as f:
                            body = f.read()[:60000]
                    except Exception:
                        body = ''
            conn.execute('INSERT INTO documents_fts(title,content,code,doc_id) VALUES(?,?,?,?)',
                         (title or '', body, code, cur.lastrowid))
        conn.commit()
        n_fts = conn.execute('SELECT COUNT(*) FROM documents_fts').fetchone()[0]
    finally:
        conn.close()

    if dry_run:
        print(f'[dry-run] 将装载 {len(rows)} 份公告（A+H 公告 {n_h} 份），'
              f'FTS 索引 {n_fts} 条，未写库')
    else:
        print(f'入库完成：{len(rows)} 份公告，A+H 公告 {n_h} 份，'
              f'FTS 索引 {n_fts} 条 -> {paths.rel(paths.DB)}')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog='audit_kit build',
        description='由采集任务表创建 SQLite 主库（建表 + 装载 documents 基础字段 + FTS 索引）')
    ap.add_argument('cmd', nargs='?', default='build', choices=['build'])
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--rebuild', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    return build_from_tasks(limit=a.limit, rebuild=a.rebuild, dry_run=a.dry_run)


if __name__ == '__main__':
    sys.exit(main())
