# -*- coding: utf-8 -*-
"""export — 从主库导出 Excel：公告明细、公司年度面板、统计、A+H 清单

输出默认写到 ``paths.EXPORT_XLSX``（output/ 目录下），可用 ``--out`` 覆盖。
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from . import paths

ADB = paths.DB
OUT = paths.EXPORT_XLSX

ROLES = [('A', '项目合伙人'), ('A', '签字注册会计师'), ('A', '质量控制复核人'),
         ('H', '项目合伙人'), ('H', '签字注册会计师'), ('H', '质量控制复核人')]
KEYS = {(m, r): f'{m.lower()}_{i}' for i, (m, r) in enumerate(ROLES)}


def load():
    c = sqlite3.connect(ADB, timeout=60)
    c.row_factory = sqlite3.Row
    docs = [dict(r) for r in c.execute('SELECT * FROM documents')]
    by_id = {d['id']: d for d in docs}
    for d in docs:
        for m, r in ROLES:
            d[KEYS[(m, r)]] = []
    for p in c.execute('SELECT doc_id,market,role,name,concurrent,become_cpa,start_listed,'
                       'start_firm,start_company,time_src FROM persons'):
        d = by_id.get(p['doc_id'])
        k = KEYS.get((p['market'], p['role']))
        if d is not None and k:
            d[k].append(p['name'])
    for d in docs:
        for k in d:
            if k.startswith(('a_', 'h_')) and isinstance(d[k], list):
                d[k] = '、'.join(d[k])
    c.close()
    return docs


def load_persons():
    """人员长表（职位 / 姓名 / 事务所 / 关键时间节点）"""
    c = sqlite3.connect(ADB, timeout=60)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute('''
        SELECT d.code, d.name AS co_name, d.disc_year, d.audit_year, d.type, d.date,
               d.firm_notice, d.firm_official, d.title, d.url, d.pdf,
               p.market, p.role, p.name, p.concurrent,
               p.become_cpa, p.start_listed, p.start_firm, p.start_company, p.time_src
        FROM persons p JOIN documents d ON d.id = p.doc_id
        ORDER BY d.code, d.disc_year, p.role, p.name''')]
    c.close()
    return rows


def panel(docs):
    rank = {'变更': 0, '续聘': 1, '聘任': 2}
    best = {}
    for d in docs:
        k = (d['code'], d['disc_year'])
        key = (rank.get(d['type'], 9), d['date'] or '')
        if k not in best or key < best[k][0]:
            best[k] = (key, d)
    return [v[1] for _, v in sorted(best.items())]


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='audit_kit export',
        description='从主库导出 Excel（公告明细 / 公司年度面板 / 统计 / 人员明细）')
    ap.add_argument('--out', default=None,
                    help=f'输出 xlsx 路径（默认 {OUT}）')
    a = ap.parse_args(argv)
    out = Path(a.out) if a.out else OUT
    paths.ensure(out.parent)

    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    paths.require_db()
    docs = load()
    pnl = panel(docs)
    print('documents', len(docs), 'panel', len(pnl))

    wb = Workbook()
    ws = wb.active
    ws.title = '公告明细'
    cols = [
        ('证券代码', 'code', 10), ('证券简称', 'name', 12), ('披露年', 'disc_year', 7),
        ('审计年度', 'audit_year', 8), ('公告类型', 'type', 8), ('披露日期', 'date', 11),
        ('项目合伙人', 'a_0', 11), ('签字注册会计师', 'a_1', 20), ('质量控制复核人', 'a_2', 15),
        ('H股项目合伙人', 'h_3', 13), ('H股签字注册会计师', 'h_4', 15), ('H股复核人', 'h_5', 11),
        ('事务所(公告)', 'firm_notice', 26), ('事务所(数据库)', 'firm_official', 26),
        ('前任事务所', 'prev_firm', 24), ('连续年限', 'tenure', 8), ('审计收费', 'fee', 12),
        ('抽取来源', 'src', 12), ('关键页', 'pages', 8), ('公告标题', 'title', 40),
        ('公告链接', 'url', 46), ('PDF文件', 'pdf', 50),
    ]
    ws.append([c[0] for c in cols])
    for d in sorted(docs, key=lambda x: (x['code'], x['disc_year'] or 0, x['date'] or '')):
        ws.append([d.get(k, '') for _, k, _ in cols])
    for i, (_, _, w) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws2 = wb.create_sheet('公司年度面板')
    cols2 = [('证券代码', 'code', 10), ('证券简称', 'name', 12), ('年度', 'disc_year', 7),
             ('审计年度', 'audit_year', 8), ('公告类型', 'type', 8), ('披露日期', 'date', 11),
             ('事务所', 'firm_official', 30), ('项目合伙人', 'a_0', 11),
             ('签字注册会计师', 'a_1', 20), ('质量控制复核人', 'a_2', 15),
             ('H股合伙人', 'h_3', 12), ('H股签字注师', 'h_4', 14), ('H股复核人', 'h_5', 11),
             ('审计收费', 'fee', 12), ('公告标题', 'title', 36), ('公告链接', 'url', 44)]
    ws2.append([c[0] for c in cols2])
    for d in sorted(pnl, key=lambda x: (x['code'], x['disc_year'] or 0)):
        ws2.append([d.get(k, '') for _, k, _ in cols2])
    for i, (_, _, w) in enumerate(cols2, 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # 统计
    ws3 = wb.create_sheet('统计')
    n = len(docs)
    full = sum(1 for d in docs if d['a_0'] and d['a_1'] and d['a_2'])
    ws3.append(['指标', '数值'])
    ws3.append(['公告总数', n])
    ws3.append(['公司数', len({d['code'] for d in docs})])
    ws3.append(['面板行数', len(pnl)])
    ws3.append(['三字段齐全', full])
    ws3.append(['齐全率', f'{full/max(1,n):.1%}'])
    for lab, k in (('项目合伙人', 'a_0'), ('签字注册会计师', 'a_1'), ('质量控制复核人', 'a_2')):
        c = sum(1 for d in docs if d[k])
        ws3.append([f'{lab}非空', f'{c} ({c/max(1,n):.1%})'])
    ws3.append(['A+H 公告', sum(1 for d in docs if d['h_3'] or d['h_4'] or d['h_5'])])
    ws3.append(['', ''])
    ws3.append(['按披露年', '公告数 / 齐全率'])
    for y, v in sorted(Counter(d['disc_year'] for d in docs).items()):
        f = sum(1 for d in docs if d['disc_year'] == y and d['a_0'] and d['a_1'] and d['a_2'])
        ws3.append([str(y), f'{v} / {f/max(1,v):.0%}'])
    ws3.append(['', ''])
    ws3.append(['公告类型', str(dict(Counter(d['type'] for d in docs)))])
    ws3.append(['抽取来源', str(dict(Counter(d['src'] for d in docs)))])
    ws3.append(['事务所非空(公告口径)', sum(1 for d in docs if d['firm_notice'])])
    ws3.append(['事务所非空(数据库口径)', sum(1 for d in docs if d['firm_official'])])
    ws3.append(['审计年度非空', sum(1 for d in docs if d['audit_year'])])
    ws3.append(['连续年限非空', sum(1 for d in docs if d['tenure'])])
    ws3.append(['审计收费非空', sum(1 for d in docs if d['fee'])])
    ws3.column_dimensions['A'].width = 26
    ws3.column_dimensions['B'].width = 50

    # ---------- 人员明细（长表）----------
    wsP = wb.create_sheet('人员明细', 0)
    colsP = [('证券代码', 'code', 10), ('证券简称', 'co_name', 12), ('披露年', 'disc_year', 7),
             ('审计年度', 'audit_year', 8), ('职位', 'role', 15), ('姓名', 'name', 10),
             ('兼任', 'concurrent', 7), ('会计师事务所(公告口径)', 'firm_notice', 28),
             ('会计师事务所(年报口径)', 'firm_official', 28),
             ('成为注册会计师', 'become_cpa', 13), ('开始从事上市公司审计', 'start_listed', 15),
             ('开始在本所执业', 'start_firm', 13), ('开始为本公司提供审计服务', 'start_company', 18),
             ('时间节点来源', 'time_src', 12), ('市场', 'market', 6),
             ('公告类型', 'type', 8), ('公告日期', 'date', 11), ('公告标题', 'title', 38),
             ('公告链接', 'url', 44)]
    prow = load_persons()
    wsP.append([c[0] for c in colsP])
    for r in prow:
        wsP.append([r.get(k, '') if r.get(k) is not None else '' for _, k, _ in colsP])
    for i, (_, _, w) in enumerate(colsP, 1):
        wsP.column_dimensions[get_column_letter(i)].width = w
    wsP.freeze_panes = 'D2'
    wsP.auto_filter.ref = wsP.dimensions

    n_person = len(prow)
    ws3.append(['人员记录数', n_person])
    for lab, k in (('成为注册会计师', 'become_cpa'), ('开始从事上市公司审计', 'start_listed'),
                   ('开始在本所执业', 'start_firm'), ('开始为本公司提供审计服务', 'start_company')):
        c = sum(1 for r in prow if r.get(k))
        ws3.append([f'人员·{lab}', f'{c} ({c/max(1,n_person):.1%})'])

    for sh in (ws, ws2, ws3, wsP):
        for c in sh[1]:
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = PatternFill('solid', fgColor='4F6228')
            c.alignment = Alignment(horizontal='center', vertical='center')
        sh.freeze_panes = 'A2'
        if sh is not ws3:
            sh.auto_filter.ref = sh.dimensions
    wb.save(out)
    print('saved', out)
    return 0
