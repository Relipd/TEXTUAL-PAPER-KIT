# -*- coding: utf-8 -*-
"""times.py — 人员关键时间节点抽取（公告表格/段落通用四列）

输出四个标准节点（公告表格/段落里通用的四列）：
  become_cpa    何时成为注册会计师
  start_listed  何时开始从事上市公司审计
  start_firm    何时开始在本所执业
  start_company 何时开始为本公司提供审计服务

支持两种版式：
  1. 行式表格：项目合伙人 | 张芹 | 2000年 | 1998年 | 2000年 | 2006年
  2. 叙述式：项目合伙人：刘钧，1999年成为中国注册会计师，1998年起开始从事
     上市公司审计，1998年起在中审众环执业，2022年起为本公司提供审计服务。
"""
from __future__ import annotations

import re

FIELDS = ('become_cpa', 'start_listed', 'start_firm', 'start_company')
FIELD_CN = {
    'become_cpa': '成为注册会计师',
    'start_listed': '开始从事上市公司审计',
    'start_firm': '开始在本所执业',
    'start_company': '开始为本公司提供审计服务',
}

HEADER_PAT = {
    'become_cpa': re.compile(r'何时成为注册会计师|成为注册会计师时间|何时成为注会|注册会计师执业时间|成为注册会计师|何时成为'),
    'start_listed': re.compile(r'何时开始从事上市公司审计|开始从事上市公司审计时间|从事上市公司审计时间|开始从事上市公司审计|何时开始从事|开始从事上市公司'),
    'start_firm': re.compile(r'何时开始在本所执业|开始在本所执业时间|在本所执业时间|开始在本所执业|何时开始在本所'),
    'start_company': re.compile(r'何时开始为本公司提供审计服务|开始为本公司提供审计服务时间|开始为公司提供审计服务时间|开始为本公司提供审计服务|何时开始为本公司'),
}

PURE_YEAR = re.compile(r'^((?:19|20)\d{2})\s*年?(?:\s*(\d{1,2})\s*月)?$')
YEAR_ANY = re.compile(r'(?:19|20)\d{2}')


def parse_year(txt):
    """单元格 -> '2012' / '2012-03' / ''"""
    if not txt:
        return ''
    t = re.sub(r'\s+', '', txt)
    m = PURE_YEAR.match(t)
    if not m:
        return ''
    y = m.group(1)
    mo = m.group(2)
    if mo and 1 <= int(mo) <= 12:
        return f'{y}-{int(mo):02d}'
    return y


def header_columns(rows, first_role_idx):
    """从角色行之上的表头里识别四个时间列 -> {field: (x0, x1)}"""
    cols = {}
    lo = max(0, first_role_idx - 6)
    for i in range(lo, min(len(rows), first_role_idx + 1)):
        row = rows[i]
        for (x0, x1, txt) in row['cells']:
            t = re.sub(r'\s+', '', txt)
            for f, rx in HEADER_PAT.items():
                if f in cols:
                    continue
                if rx.fullmatch(t):
                    cols[f] = (x0, x1)
                elif rx.search(t) and len(t) <= 16:
                    # 表头被拆成多行时只登记第一段，后续用 x 对齐补
                    cols[f] = (x0, x1)
    return cols


def _norm_cols(cols):
    """同一列重复登记时合并为并集"""
    return {f: (v[0], v[1]) for f, v in cols.items()}


def seq_map(tokens):
    """没有表头时按出现顺序映射到四列（证监会标准表的固定顺序）"""
    out = {f: '' for f in FIELDS}
    for f, v in zip(FIELDS, tokens):
        out[f] = v
    return out


def by_columns(tokens_with_x, cols):
    """按表头列 x 位置对齐取值"""
    out = {f: '' for f in FIELDS}
    centers = {f: (v[0] + v[1]) / 2 for f, v in cols.items()}
    for (x, v) in tokens_with_x:
        if not v:
            continue
        best, bd = None, 1e9
        for f, c in centers.items():
            d = abs(c - x)
            if d < bd:
                bd, best = d, f
        if best and bd <= 80 and not out[best]:
            out[best] = v
    return out


def from_row_cells(cells, name_right, cols=None):
    """行式表格：姓名右侧的纯年份单元格 -> 四节点

    cells: [(x0, x1, text)]；name_right: 姓名单元格右边界
    """
    toks = []
    for (x0, x1, txt) in cells:
        if x0 < name_right - 3:
            continue
        y = parse_year(txt)
        if y:
            toks.append((x0, y))
    if not toks:
        return {f: '' for f in FIELDS}, []
    toks.sort()
    vals = [v for _x, v in toks]
    if cols:
        mapped = by_columns(toks, cols)
        # 表头只认出部分列时，用顺序法兜底空位
        if sum(1 for v in mapped.values() if v) < min(4, len(vals)):
            seq = seq_map(vals)
            for f in FIELDS:
                if not mapped[f]:
                    mapped[f] = seq[f]
        return mapped, vals
    return seq_map(vals), vals


PROSE_PATS = {
    'become_cpa': [
        r'((?:19|20)\d{2})\s*年(?:\d{1,2}\s*月)?(?:\d{1,2}\s*日)?\s*(?:注册成为|注册为|取得|获得|成为)'
        r'[^，。；;]{0,8}?(?:香港)?(?:中国)?(?:注册)?(?:执业)?会计师',
        r'((?:19|20)\d{2})\s*年(?:起|开始|初)?[^，。；;]{0,8}?成为注册会计师',
        r'((?:19|20)\d{2})\s*年(?:起|开始|初)?[^，。；;]{0,10}?获得中国注册会计师资质',
    ],
    'start_listed': [
        r'((?:19|20)\d{2})\s*年(?:起|开始|初)?[^，。；;]{0,6}?(?:开始)?从事(?:境内外)?(?:上市)?公司审计',
        r'((?:19|20)\d{2})\s*年(?:起|开始)?[^，。；;]{0,10}?开始从事上市公司审计',
        r'((?:19|20)\d{2})\s*年(?:起|开始|初)?[^，。；;]{0,6}?从事(?:审计|注册会计师)(?:相关)?工作',
    ],
    'start_firm': [
        r'((?:19|20)\d{2})\s*年(?:起|开始|初)?[^，。；;]{0,14}?在(?:本所|该所|[^，。；;]{2,24}?)执业',
        r'((?:19|20)\d{2})\s*年(?:起|开始|初)?[^，。；;]{0,14}?加入[^，。；;]{2,20}?会计师事务所',
        r'((?:19|20)\d{2})\s*年(?:起|开始|初)?[^，。；;]{0,10}?开始在本所执业',
    ],
    'start_company': [
        r'((?:19|20)\d{2})\s*年(?:起|开始|初)?[^，。；;]{0,6}?为(?:本)?公司提供(?:财务)?审计服务',
        r'((?:19|20)\d{2})\s*年(?:起|开始|初)?[^，。；;]{0,6}?为本公司提供审计服务',
        r'自?\s*((?:19|20)\d{2})\s*年(?:度)?(?:起|开始)?[^，。；;]{0,12}?(?:担任|为)(?:本公司|公司)'
        r'[^，。；;]{0,12}?(?:审计|签字)',
        r'((?:19|20)\d{2})\s*年(?:起|开始|初)?[^，。；;]{0,6}?为[^，。；;]{2,20}?提供(?:财务)?审计服务',
    ],
}


def from_prose(desc):
    """叙述式：从「项目合伙人：张三，2012年成为……」这段描述里取四节点"""
    out = {f: '' for f in FIELDS}
    if not desc:
        return out
    d = re.sub(r'\s+', '', desc)
    for f, pats in PROSE_PATS.items():
        for p in pats:
            m = re.search(p, d)
            if m:
                out[f] = m.group(1)
                break
    return out


def fill_missing(dst, src):
    for f in FIELDS:
        if not dst.get(f) and src.get(f):
            dst[f] = src[f]
    return dst
