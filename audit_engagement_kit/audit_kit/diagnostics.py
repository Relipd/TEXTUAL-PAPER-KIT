# -*- coding: utf-8 -*-
"""diagnostics — 主库现状诊断：缺漏盘点与指标快照

两个入口（只读，不写库）：
    gaps_main(argv=None)   现状缺漏盘点：逐类量化剩余问题，给出可优化点
    stats_main(argv=None)  当前主库指标快照

命令行：
    python -m audit_kit gaps
    python -m audit_kit stats
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict

from . import paths


def gaps_main(argv=None):
    """现状缺漏盘点：逐类量化剩余问题，给出可优化点"""
    ap = argparse.ArgumentParser(
        prog='audit_kit gaps',
        description='现状缺漏盘点：无人员公告分类、角色缺失、时间节点、元字段、数据质量')
    ap.parse_args(argv)

    paths.require_db()
    con = sqlite3.connect(paths.DB)
    cur = con.cursor()

    N = cur.execute('select count(*) from documents').fetchone()[0]
    print(f'公告总数 {N}\n')

    # ---------- 1. 无人员公告分类 ----------
    print('=== 1. 无人员公告（{）===')
    nop = cur.execute("""select d.id, d.code, d.disc_year, d.type, d.title, d.src, d.txt
                         from documents d where not exists
                         (select 1 from persons p where p.doc_id=d.id and p.market='A')""").fetchall()
    print('数量', len(nop))
    cls = Counter()
    for did, code, dy, typ, title, src, txt in nop:
        t = title or ''
        if re.search(r'(审计报告|财务报表|年报|招股|募集|半年度|季度报告)', t):
            cls['非聘任公告：审计报告/年报/招股'] += 1
        elif re.search(r'(H\s*股|境外|B\s*股)', t):
            cls['H股/境外聘任（表外口径）'] += 1
        elif re.search(r'(内部审计|审计机构负责人|审计部)', t):
            cls['内部审计机构（非会计师事务所）'] += 1
        elif re.search(r'(独立董事|审计委员会|事前认可|意见|确认书|说明)', t):
            cls['独董/委员会意见类'] += 1
        elif re.search(r'(续聘|聘任|聘请|变更|改聘|更换).{0,30}(会计师事务所|审计机构|审计师)', t):
            cls['★目标类聘任公告（应为可抽）'] += 1
        elif re.search(r'(陈述意见|承诺函|受托管理|临时报告|进展公告)', t):
            cls['券商/事务所陈述意见（非项目信息表）'] += 1
        else:
            cls['其他'] += 1
    for k, v in cls.most_common():
        print(f'  {v:5d}  {k}')

    # ---------- 2. 角色缺失分布 ----------
    print('\n=== 2. 角色缺失（按公告）===')
    missing = cur.execute("""
    select d.id, d.title, d.type,
      (select count(*) from persons p where p.doc_id=d.id and p.market='A' and p.role='项目合伙人') np,
      (select count(*) from persons p where p.doc_id=d.id and p.market='A' and p.role='签字注册会计师') ns,
      (select count(*) from persons p where p.doc_id=d.id and p.market='A' and p.role='质量控制复核人') nr
    from documents d""").fetchall()
    comb = Counter()
    for did, title, typ, np_, ns, nr in missing:
        if np_ + ns + nr == 0:
            comb['全空'] += 1
        elif np_ == 0:
            comb['仅缺项目合伙人'] += 1
        elif ns == 0:
            comb['仅缺签字注册会计师'] += 1
        elif nr == 0:
            comb['仅缺质量控制复核人'] += 1
    print(dict(comb.most_common()))

    # ---------- 3. 时间节点缺失归因 ----------
    print('\n=== 3. 时间节点 ===')
    F = ('become_cpa', 'start_listed', 'start_firm', 'start_company')
    rows = cur.execute("""select p.time_src, p.become_cpa, p.start_listed, p.start_firm, p.start_company,
                          d.disc_year from persons p join documents d on d.id=p.doc_id where p.market='A'""").fetchall()
    print('人员记录', len(rows))
    by_year = defaultdict(lambda: [0, 0])
    for src, b, sl, sf, sc, dy in rows:
        by_year[str(dy)][0] += 1
        if b and sl and sf and sc:
            by_year[str(dy)][1] += 1
    print('按披露年四节点齐全率：', {y: f'{c/n:.0%}' for y, (n, c) in sorted(by_year.items())})
    srcs = Counter((s or '').split('+')[0].split('(')[0] for s, *_ in rows)
    print('按来源人数：', dict(srcs.most_common()))

    # ---------- 4. 元字段 ----------
    print('\n=== 4. 元字段 ===')
    print('firm_notice 空', cur.execute("select count(*) from documents where firm_notice is null or firm_notice=''").fetchone()[0])
    print('firm_official 空', cur.execute("select count(*) from documents where firm_official is null or firm_official=''").fetchone()[0])
    print('audit_year 空', cur.execute("select count(*) from documents where audit_year is null").fetchone()[0])
    print('fee 空', cur.execute("select count(*) from documents where fee is null or fee=''").fetchone()[0])

    # ---------- 5. 数据质量 ----------
    print('\n=== 5. 数据质量 ===')
    pr = cur.execute("""select count(*) from documents d
     join persons p1 on p1.doc_id=d.id and p1.role='项目合伙人' and p1.market='A'
     join persons p2 on p2.doc_id=d.id and p2.role='质量控制复核人' and p2.market='A' and p2.name=p1.name""").fetchone()[0]
    print('合伙人=复核人', pr)
    dup = cur.execute("""select count(*) from (select doc_id, role, name from persons group by doc_id, role, name having count(*)>1)""").fetchone()[0]
    print('同公告同角色重名', dup)
    odd = cur.execute("select count(*) from persons where length(name) not between 2 and 4").fetchone()[0]
    print('姓名长度异常', odd)

    # 姓名不在原文
    cnt = tot = 0
    for nm, txt in cur.execute("select p.name, d.txt from persons p join documents d on d.id=p.doc_id where p.market='A'"):
        if not txt or not os.path.exists(txt):
            continue
        tot += 1
        s = re.sub(r'\s+', '', open(txt, encoding='utf-8', errors='ignore').read())
        if nm not in s:
            cnt += 1
    print(f'姓名不在原文 {cnt}/{tot} = {cnt/max(1,tot):.2%}')

    # 各角色人数分布（异常多人/缺人）
    print('\n=== 6. 角色人数分布 ===')
    for role in ('项目合伙人', '签字注册会计师', '质量控制复核人'):
        d = Counter(c for (c,) in cur.execute(
            "select count(*) from persons where role=? and market='A' group by doc_id", (role,)))
        print(' ', role, dict(sorted(d.items())))

    con.close()
    return 0


def stats_main(argv=None):
    """当前主库指标快照"""
    ap = argparse.ArgumentParser(
        prog='audit_kit stats',
        description='当前主库指标快照：公告/人员规模、角色覆盖、时间节点、元字段、分年度齐全率')
    ap.parse_args(argv)

    paths.require_db()
    con = sqlite3.connect(paths.DB)
    cur = con.cursor()
    n = cur.execute('select count(*) from documents').fetchone()[0]
    print('docs', n, 'persons', cur.execute('select count(*) from persons').fetchone()[0])
    for role in ('项目合伙人', '签字注册会计师', '质量控制复核人'):
        c = cur.execute("select count(distinct doc_id) from persons where market='A' and role=?", (role,)).fetchone()[0]
        print(f'{role:12s} {c:6d} {c/n:.1%}')
    full = cur.execute("""select count(*) from (select doc_id from persons where market='A'
       group by doc_id having sum(role='项目合伙人')>=1 and sum(role='签字注册会计师')>=1
       and sum(role='质量控制复核人')>=1)""").fetchone()[0]
    print('三字段齐全', full, f'{full/n:.1%}')
    none = n - cur.execute("select count(distinct doc_id) from persons where market='A'").fetchone()[0]
    print('完全未抽取', none, f'{none/n:.1%}')
    tot = cur.execute("select count(*) from persons where market='A'").fetchone()[0]
    F = ('become_cpa', 'start_listed', 'start_firm', 'start_company')
    for f in F:
        c = cur.execute(f"select count(*) from persons where market='A' and {f} is not null and {f}<>''").fetchone()[0]
        print(f'  {f:14s} {c:6d} {c/tot:.1%}')
    all4 = cur.execute("""select count(*) from persons where market='A' and become_cpa<>'' and start_listed<>''
       and start_firm<>'' and start_company<>''""").fetchone()[0]
    print('  四节点齐全', all4, f'{all4/tot:.1%}')
    print('firm_notice', cur.execute("select count(*) from documents where firm_notice is not null and firm_notice<>''").fetchone()[0])
    print('audit_year', cur.execute("select count(*) from documents where audit_year is not null").fetchone()[0])
    print('fee', cur.execute("select count(*) from documents where fee is not null and fee<>''").fetchone()[0])
    print('P=R', cur.execute("""select count(*) from documents d
     join persons p1 on p1.doc_id=d.id and p1.role='项目合伙人' and p1.market='A'
     join persons p2 on p2.doc_id=d.id and p2.role='质量控制复核人' and p2.market='A' and p2.name=p1.name""").fetchone()[0])
    for y, c, f in cur.execute("""select disc_year, count(*), sum(case when np>=1 and ns>=1 and nr>=1 then 1 else 0 end)
      from (select d.id, d.disc_year,
         (select count(*) from persons p where p.doc_id=d.id and p.market='A' and p.role='项目合伙人') np,
         (select count(*) from persons p where p.doc_id=d.id and p.market='A' and p.role='签字注册会计师') ns,
         (select count(*) from persons p where p.doc_id=d.id and p.market='A' and p.role='质量控制复核人') nr
       from documents d) group by disc_year order by disc_year"""):
        print(y, c, f, f'{f/c:.1%}')
    con.close()
    return 0
