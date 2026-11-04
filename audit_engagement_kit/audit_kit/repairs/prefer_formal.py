# -*- coding: utf-8 -*-
"""prefer_formal — 以正式公告为准，并跨公告回填人员时间节点

背景：同一公司同一事项往往有「正式公告 + 独立意见 / 承诺函 / 陈述意见」多份文件。
非正式文件里的人员信息不全（或根本不是项目信息表），而正式公告里是齐全的。
本模块做两件事：

1. 去重：同一（公司, 披露年, 姓名, 职位）同时出现在正式公告与非正式文件时，
   删除非正式文件里的那条；
2. 回填：人员缺某个时间节点时，用同一公司在其它公告里该人员的同名记录补齐
   （优先正式公告），来源标记加 '+bf'。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from collections import defaultdict

from .. import paths

DB = paths.DB
F = ('become_cpa', 'start_listed', 'start_firm', 'start_company')
INFORMAL = re.compile(r'(独立意见|独立董事|事前认可|审计委员会|承诺函|承诺$|陈述意见|'
                      r'受托管理|确认书|基本情况|专项说明|核查意见)')


def main(argv=None):
    argparse.ArgumentParser(
        prog='audit_kit repairs prefer-formal',
        description='删除非正式文件中的重复人员，并跨公告回填时间节点（写主库）').parse_args(argv)

    con = sqlite3.connect(DB)
    cur = con.cursor()

    docs = {r[0]: {'code': r[1], 'dy': r[2], 'title': r[3] or ''}
            for r in cur.execute('select id, code, disc_year, title from documents')}
    persons = cur.execute('''select id, doc_id, role, name, become_cpa, start_listed,
                             start_firm, start_company, time_src from persons where market='A' ''').fetchall()

    def formal(doc):
        return not INFORMAL.search(docs[doc]['title'])

    # ---------- 1. 同公司同年同名同职位：正式优先，删非正式 ----------
    keep = defaultdict(list)
    for pid, did, role, nm, *rest in persons:
        d = docs.get(did)
        if not d:
            continue
        key = (d['code'], d['dy'], role, nm)
        keep[key].append((0 if formal(did) else 1, pid, did))

    del_ids = []
    for key, lst in keep.items():
        if len(lst) < 2:
            continue
        lst.sort()
        for pref, pid, did in lst[1:]:
            if pref == 1:          # 后面的是非正式文件里的
                del_ids.append(pid)
    if del_ids:
        cur.executemany('delete from persons where id=?', [(i,) for i in del_ids])
    print('删除非正式文件的重复人员', len(del_ids))

    # ---------- 2. 跨公告回填时间节点 ----------
    cur.execute('delete from persons where id in (select id from persons where 0)')  # noop
    persons = cur.execute('''select id, doc_id, role, name, become_cpa, start_listed,
                             start_firm, start_company, time_src from persons where market='A' ''').fetchall()
    by_name = defaultdict(lambda: defaultdict(list))   # (code,name) -> field -> [(prio, value)]
    for pid, did, role, nm, *vals in persons:
        d = docs.get(did)
        if not d:
            continue
        prio = 0 if formal(did) else 1
        for f, v in zip(F, vals[:4]):
            if v:
                by_name[(d['code'], nm)][f].append((prio, v))

    updates = []
    filled = 0
    for pid, did, role, nm, *vals in persons:
        d = docs.get(did)
        if not d:
            continue
        miss = [f for f, v in zip(F, vals[:4]) if not v]
        if not miss:
            continue
        src = vals[4] or ''
        new_vals = list(vals[:4])
        changed = False
        for f in miss:
            cand = by_name.get((d['code'], nm), {}).get(f)
            if cand:
                cand.sort()
                new_vals[F.index(f)] = cand[0][1]
                changed = True
        if changed:
            filled += 1
            if '+bf' not in src:
                src = (src + '+bf') if src else 'bf'
            updates.append((new_vals[0] or None, new_vals[1] or None, new_vals[2] or None,
                            new_vals[3] or None, src, pid))
    if updates:
        cur.executemany('update persons set become_cpa=?, start_listed=?, start_firm=?, '
                        'start_company=?, time_src=? where id=?', updates)
    print('回填时间节点的人员', filled)

    con.commit()
    tot = cur.execute("select count(*) from persons where market='A'").fetchone()[0]
    all4 = cur.execute("""select count(*) from persons where market='A' and become_cpa<>'' and
       start_listed<>'' and start_firm<>'' and start_company<>''""").fetchone()[0]
    print(f'现有人员 {tot}，四节点齐全 {all4} ({all4/max(1,tot):.1%})')
    con.close()
    return 0
