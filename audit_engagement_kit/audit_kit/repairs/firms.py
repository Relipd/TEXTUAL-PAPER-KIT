# -*- coding: utf-8 -*-
"""firms — 按当前审计年度重算「事务所（数据库口径）」

用例：审计年度修正后，用 FIN_Audit 的 (证券代码, 会计年度) -> 境内审计事务所
重新匹配，填补空缺。
"""
from __future__ import annotations

import argparse
import sqlite3

from .. import paths
from ..store import load_auditor_map

DB = paths.DB


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='audit_kit repairs firms',
        description='按 FIN_Audit 映射重算 documents.firm_official（写主库）')
    ap.add_argument('--dry-run', action='store_true', help='只统计不写库')
    a = ap.parse_args(argv)

    amap = load_auditor_map()
    con = sqlite3.connect(DB)
    cur = con.cursor()
    rows = cur.execute('select id, code, audit_year, firm_official from documents').fetchall()
    filled = 0
    for did, code, ay, old in rows:
        new = amap.get((code, str(ay)), '') if ay else ''
        if new and not old:
            filled += 1
        if not a.dry_run:
            cur.execute('update documents set firm_official=? where id=?', (new or old or None, did))
    if not a.dry_run:
        con.commit()
    n = cur.execute("select count(*) from documents where firm_official is not null and firm_official<>''").fetchone()[0]
    print(f'FIN_Audit 映射 {len(amap)} 条；补空 {filled}；现覆盖 {n}')
    if a.dry_run:
        print('（dry-run：未写库）')
    con.close()
    return 0
