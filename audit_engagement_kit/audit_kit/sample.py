# -*- coding: utf-8 -*-
"""sample — 生成人工核对样本 审计机构聘任公告_核对样本.xlsx（人员长表口径）

列：职位 / 姓名 / 会计师事务所 / 四个关键时间节点 + 原文片段 + 疑点标记
抽样：按「时间节点来源 × 披露年」分层，另加高疑点样本，共 300 条人员记录。

输出默认写到 ``paths.SAMPLE_XLSX``（output/ 目录下），可用 ``--out`` 覆盖。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import paths

DB = paths.DB
OUT = paths.SAMPLE_XLSX
FIELDS = ('become_cpa', 'start_listed', 'start_firm', 'start_company')
SRC_CN = {'table': '表格行', 'table_t': '表格列', 'prose': '段落叙述',
          'secfallback': '段落兜底', 'text': '文本流'}

HEAD_FILL = PatternFill('solid', fgColor='1F4E78')
HEAD_FONT = Font(color='FFFFFF', bold=True, size=10, name='微软雅黑')
TITLE_FONT = Font(bold=True, size=13, name='微软雅黑')
BODY_FONT = Font(size=9, name='微软雅黑')
WARN_FILL = PatternFill('solid', fgColor='FCE4D6')
BAD_FILL = PatternFill('solid', fgColor='F8CBAD')
THIN = __import__('openpyxl').styles.Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def load():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute('''
        SELECT d.id AS doc_id, d.code, d.name AS co, d.disc_year AS dy, d.audit_year AS ay,
               d.type, d.date, d.title, d.firm_notice, d.firm_official, d.txt, d.src,
               p.market, p.role, p.name, p.concurrent,
               p.become_cpa, p.start_listed, p.start_firm, p.start_company, p.time_src
        FROM persons p JOIN documents d ON d.id = p.doc_id
        WHERE p.market = 'A' ''')]
    con.close()
    return rows


def ynum(v):
    if not v:
        return None
    m = re.match(r'(\d{4})', str(v))
    return int(m.group(1)) if m else None


def flags_for(r, freq, body_cache):
    fl = []
    if not all(r.get(f) for f in FIELDS):
        miss = [f for f in FIELDS if not r.get(f)]
        fl.append('时间节点缺失:' + '/'.join({
            'become_cpa': '成为注会', 'start_listed': '从事上市审计',
            'start_firm': '本所执业', 'start_company': '为本公司服务'}[f] for f in miss))
    ys = {f: ynum(r.get(f)) for f in FIELDS}
    # 披露里「先从业、后考取注会」并不罕见，只有跨度大于 5 年才作为疑点
    if ys['become_cpa'] and ys['start_listed'] and ys['start_listed'] - ys['become_cpa'] < -5:
        fl.append('时间倒序:从事审计早于成为注会5年以上')
    if ys['become_cpa'] and ys['start_firm'] and ys['start_firm'] - ys['become_cpa'] < -5:
        fl.append('时间倒序:本所执业早于成为注会5年以上')
    if ys['start_company'] and ys['start_listed'] and ys['start_company'] < ys['start_listed'] - 1:
        fl.append('时间倒序:服务本公司早于从事审计')
    if ys['start_company'] and ys['become_cpa'] and ys['start_company'] < ys['become_cpa']:
        fl.append('时间倒序:服务本公司早于成为注会')
    for f in FIELDS:
        if ys[f] and (ys[f] < 1980 or ys[f] > 2027):
            fl.append(f'年份异常:{f}')
    if r['time_src'] in ('secfallback', 'text') or (r['time_src'] or '').startswith(('secfallback', 'text')):
        fl.append('低置信来源')
    if (r.get('src') == 'ocr') or (r.get('time_src') or '').startswith('ocr'):
        fl.append('OCR识别(需复核)')
    if r['concurrent']:
        fl.append('兼任(合伙人=签字人)')
    if r['name'] in freq and freq[r['name']] >= 60:
        fl.append('超高频姓名')
    body = body_cache.get(r['doc_id'])
    if body is not None and r['name'] not in body:
        fl.append('姓名不在原文')
    if r['firm_notice'] and r['firm_official']:
        a = r['firm_notice'].replace('（', '(').replace('）', ')')
        b = r['firm_official'].replace('（', '(').replace('）', ')')
        if b not in a and a not in b:
            fl.append('事务所两口径不一致')
    return fl


def severity(fl):
    s = 0
    for f in fl:
        if f.startswith(('姓名不在原文', '年份异常', '超高频')):
            s += 4
        elif f.startswith('时间倒序:服务本公司'):
            s += 3
        elif f.startswith('时间倒序'):
            s += 2
        elif f.startswith('低置信来源'):
            s += 2
        elif f.startswith('OCR识别'):
            s += 2
        elif f.startswith('时间节点缺失'):
            s += 1
        elif f.startswith('事务所'):
            s += 1
    return s


def snippet(txt_path, name='', limit=260):
    """以被核对人姓名为中心截取原文片段，便于逐条核对"""
    if not txt_path or not os.path.exists(txt_path):
        return ''
    s = open(txt_path, encoding='utf-8', errors='ignore').read()
    s = re.sub(r'\s+', '', s)
    if name and name in s:
        # 优先取「角色标签 + 姓名」这一处，避免命中独立性段落里的同名
        pos = [m.start() for m in re.finditer(re.escape(name), s)]
        label = re.compile(r'项目合伙人|签字注册会计师|签字会计师|质量控制复核|质量复核|复核人')
        best = pos[0]
        for i in pos:
            if label.search(s[max(0, i - 30):i]):
                best = i
                break
        st = max(0, best - limit // 3)
        return s[st:st + limit]
    m = re.search(r'[（(]二[）)]项目|项目组成员|项目成员|项目人员信息', s)
    st = m.start() if m else max(0, s.find('项目合伙人') - 60)
    return s[st:st + limit]


def write_sheet(ws, headers, rows, widths, freeze='A2', wrap_cols=()):
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font = HEAD_FILL, HEAD_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = BORDER
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=len(headers)):
        for cell in row:
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = Alignment(vertical='top', wrap_text=(cell.column in wrap_cols))
    ws.freeze_panes = freeze
    ws.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{ws.max_row}'


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='audit_kit sample',
        description='生成人工核对样本 Excel（人员长表口径，300 条）')
    ap.add_argument('--out', default=None,
                    help=f'输出 xlsx 路径（默认 {OUT}）')
    a = ap.parse_args(argv)
    out = Path(a.out) if a.out else OUT
    paths.ensure(out.parent)

    paths.require_db()
    rows = load()
    con = sqlite3.connect(DB)
    n_docs = con.execute('select count(*) from documents').fetchone()[0]
    con.close()
    random.seed(20261104)
    freq = Counter(r['name'] for r in rows)
    body_cache = {}
    print('人员记录', len(rows))

    # 预计算疑点
    for r in rows:
        if r['doc_id'] not in body_cache:
            body_cache[r['doc_id']] = None
            if r['txt'] and os.path.exists(r['txt']):
                try:
                    body_cache[r['doc_id']] = re.sub(r'\s+', '', open(r['txt'], encoding='utf-8', errors='ignore').read())
                except Exception:
                    pass
        r['flags'] = flags_for(r, freq, body_cache)
        r['sev'] = severity(r['flags'])

    # 分层抽样：来源 × 披露年，每层至多 3 条
    strata = defaultdict(list)
    for r in rows:
        src = (r['time_src'] or 'none').split('(')[0]
        if src not in SRC_CN:
            src = 'secfallback' if src.startswith('secfallback') else 'text'
        strata[(src, str(r['dy']))].append(r)
    picked = []
    seen = set()
    for key in sorted(strata):
        lst = strata[key]
        take = min(3, len(lst))
        for r in random.sample(lst, take):
            k = (r['code'], r['role'], r['name'])   # 同一人跨年只留一条，保证样本多样性
            if k not in seen:
                seen.add(k)
                picked.append(r)
    # 高疑点样本 60 条
    flagged = sorted(rows, key=lambda r: -r['sev'])
    for r in flagged[:60]:
        k = (r['code'], r['role'], r['name'])
        if k not in seen:
            seen.add(k)
            picked.append(r)
    # 随机补足 300
    rest = [r for r in rows if (r['code'], r['role'], r['name']) not in seen]
    random.shuffle(rest)
    for r in rest:
        if len(picked) >= 300:
            break
        picked.append(r)
    picked = picked[:300]
    # 高疑点优先展示，便于先核对问题
    picked.sort(key=lambda r: (-r['sev'], str(r['dy']), r['code']))

    data = []
    for n, r in enumerate(picked, 1):
        src = (r['time_src'] or '').split('(')[0]
        data.append([
            n, r['code'], r['co'], r['dy'], r['ay'], r['role'], r['name'],
            '是' if r['concurrent'] else '',
            r['firm_notice'] or '', r['firm_official'] or '',
            r.get('become_cpa') or '', r.get('start_listed') or '',
            r.get('start_firm') or '', r.get('start_company') or '',
            SRC_CN.get(src, src), snippet(r['txt'], r['name']),
            '；'.join(r['flags']),
            '', '', '', '',
        ])

    wb = Workbook()
    ws0 = wb.active
    ws0.title = '说明'
    ws0['A1'] = f"审计机构聘任公告 人员抽取人工核对样本（{time.strftime('%Y-%m-%d')} 生成）"
    ws0['A1'].font = TITLE_FONT
    notes = [
        '',
        '一、口径',
        f'  仅基于本地已下载的公告 PDF 切片（{n_docs} 份），不含任何增量抓取。',
        '  每条记录 = 公告中的一名项目组成员：职位 / 姓名 / 会计师事务所 / 四个关键时间节点。',
        '  四个时间节点取自公告「(二)项目信息」表格或叙述段落的固定表述：',
        '    成为注册会计师 / 开始从事上市公司审计 / 开始在本所执业 / 开始为本公司提供审计服务。',
        '',
        '二、抽样方法',
        f'  从 {len(rows)} 条人员记录中按「时间节点来源 × 披露年」分层抽样，另加 60 条规则扫描出的高疑点样本，共 300 条。',
        '  时间节点来源（O 列）：表格行＝角色在左、姓名在右的行式表格；表格列＝表头为角色的田字表；',
        '  段落叙述＝「项目合伙人：张三，2012 年成为注册会计师……」这类叙述；段落兜底＝仅在项目信息段落内补抓，无时间节点。',
        '',
        '三、疑点标记（Q 列）规则',
        '  时间倒序：开始从事上市公司审计早于成为注册会计师等不合逻辑的先后关系（多为串列）',
        '  时间节点缺失：公告未披露或本次未抽到',
        '  年份异常：不在 1980-2027 区间',
        '  低置信来源：来自段落兜底/文本流，建议重点核对',
        '  姓名不在原文：抽取的姓名在该公告文本中检索不到',
        '  事务所两口径不一致：公告口径与年报口径名称不同（可能是真实现象，也可能抽错）',
        '',
        '四、核对填写',
        '  R 列「核对结论」填 对 / 错 / 部分对；S 列「正确值」写应改成的值；T 列「备注」写依据。',
    ]
    for i, t in enumerate(notes, 2):
        ws0.cell(row=i, column=1, value=t).font = BODY_FONT
    ws0.column_dimensions['A'].width = 120

    headers = ['序号', '证券代码', '证券简称', '披露年', '审计年度', '职位', '姓名', '兼任',
               '会计师事务所(公告口径)', '会计师事务所(年报口径)',
               '成为注册会计师', '开始从事上市公司审计', '开始在本所执业', '开始为本公司提供审计服务',
               '时间节点来源', '原文片段（项目信息段落）', '疑点标记',
               '核对结论', '正确值', '备注']
    ws1 = wb.create_sheet('核对样本')
    write_sheet(ws1, headers, data,
                [5, 9, 12, 7, 8, 15, 10, 6, 26, 26, 13, 16, 13, 20, 12, 72, 30, 10, 14, 22],
                freeze='C2', wrap_cols=(16, 17, 20))
    for i in range(2, ws1.max_row + 1):
        cell = ws1.cell(row=i, column=17)
        if cell.value:
            sev = severity(str(cell.value).split('；'))
            cell.fill = BAD_FILL if sev >= 3 else WARN_FILL

    ws2 = wb.create_sheet('疑点清单')
    frows = []
    for n, r in enumerate(sorted(rows, key=lambda x: -x['sev'])[:400], 1):
        frows.append([n, r['code'], r['co'], r['dy'], r['role'], r['name'],
                      r.get('become_cpa') or '', r.get('start_listed') or '',
                      r.get('start_firm') or '', r.get('start_company') or '',
                      '；'.join(r['flags']), r['sev'], r['doc_id']])
    write_sheet(ws2, ['序号', '证券代码', '证券简称', '披露年', '职位', '姓名',
                      '成为注会', '从事上市审计', '本所执业', '为本公司服务', '疑点', '严重度', 'doc_id'],
                frows, [5, 9, 12, 7, 15, 10, 10, 12, 10, 13, 52, 7, 8], freeze='C2', wrap_cols=(11,))

    wb.save(out)
    print('saved', out, '样本', len(data), '条；疑点', len(frows), '条')
    print('来源分布:', dict(Counter((r['time_src'] or '').split('(')[0] for r in picked)))
    return 0
