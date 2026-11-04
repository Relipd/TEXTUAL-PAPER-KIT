# -*- coding: utf-8 -*-
"""meta.py — 公告元字段抽取（审计年度 / 事务所 / 前任 / 连续年限 / 审计收费）

相对旧版的三处修正
------------------
1. 审计年度以标题年份为首选，并约束在 [披露年-2, 披露年+1]，避免正文里
   「成立于 2008 年」之类被误当审计年度；
2. 事务所名称改用 FIN_Audit 的机构名录做最长匹配，覆盖率从 36% 提升；
3. 审计收费只认「审计费用/审计报酬」邻近的金额，排除业务收入等干扰。
"""
from __future__ import annotations

import csv
import re

from . import paths

_FIRM_CACHE = None


def firm_names(csv_path=None):
    """从 FIN_Audit.csv 收集境内审计事务所规范名（按长度降序）"""
    global _FIRM_CACHE
    if _FIRM_CACHE is not None:
        return _FIRM_CACHE
    names = set()
    path = csv_path or paths.FIN_AUDIT_CSV
    try:
        with open(path, encoding='utf-8-sig', newline='') as f:
            rd = csv.reader(f)
            next(rd); next(rd); next(rd)
            for r in rd:
                if len(r) > 6 and r[6]:
                    nm = r[6].strip()
                    if nm:
                        names.add(nm)
    except Exception:
        pass
    _FIRM_CACHE = sorted(names, key=len, reverse=True)
    return _FIRM_CACHE


def _norm_firm(s):
    s = (s or '').strip()
    s = re.sub(r'^[（(]以下简称[^）)]*[）)]', '', s)
    s = re.sub(r'[（(]以下简称[^）)]*[）)]', '', s)
    s = re.sub(r'[（(]以下称[^）)]*[）)]', '', s)
    s = s.strip(' ：:，,。；;、\t\n')
    return s[:60]


def match_firm(text, firms, exclude=()):
    """在文本中做最长机构名匹配"""
    t = re.sub(r'[ \t\u3000]+', '', text or '')
    for f in firms:
        key = re.sub(r'[ \t\u3000]', '', f)
        if key in exclude:
            continue
        # 允许括号全半角差异
        pat = re.escape(key).replace(r'\(', '[（(]').replace(r'\)', '[）)]')
        if re.search(pat, t):
            return f
    return ''


AUDIT_YEAR_TITLE = re.compile(r'(20\d{2})\s*年度?')
AUDIT_YEAR_BODY = [
    re.compile(r'为公司\s*(20\d{2})\s*年度'),
    re.compile(r'拟为(?:本)?公司\s*(20\d{2})\s*年度'),
    re.compile(r'(20\d{2})\s*年度(?:财务报[表告]|财务报表|年报)?审计(?:机构|业务)'),
    re.compile(r'(20\d{2})\s*年度(?:的)?(?:财务审计|内部控制审计)'),
    re.compile(r'聘请[^。；]{0,40}?为公司\s*(20\d{2})\s*年度'),
    re.compile(r'(20\d{2})\s*年度(?:财务报[表告])?'),
]


def parse_audit_year(title, text, disc_year=None):
    cands = []
    if title:
        cands += [int(m.group(1)) for m in AUDIT_YEAR_TITLE.finditer(title)]
    for rx in AUDIT_YEAR_BODY:
        cands += [int(m.group(1)) for m in rx.finditer(text or '')]
    try:
        dy = int(disc_year)
    except Exception:
        dy = None
    if dy:
        ok = [y for y in cands if dy - 2 <= y <= dy + 1]
        if ok:
            from collections import Counter
            c = Counter(ok)
            # 优先等于披露年（续聘当年年度审计）或披露年-1（次年年报审计）
            for pref in (dy, dy - 1, dy + 1, dy - 2):
                if c.get(pref):
                    return str(pref)
            return str(c.most_common(1)[0][0])
    if cands:
        return str(cands[0])
    return ''


def parse_firm(title, text, firms):
    """事务所（公告口径）：先正文「拟聘任的会计师事务所」再标题"""
    tt = re.sub(r'[ \t\u3000]+', '', text or '')
    pats = [
        r'(?:拟聘任|拟续聘|拟聘请|拟变更|拟改聘|聘任|续聘|聘请|变更)(?:的)?会计师事务所(?:名称)?(?:为|是|[:：])([^\n，。；;]{4,45}?会计师事务所(?:[（(]特殊普通合伙[）)])?)',
        r'会计师事务所(?:的)?(?:基本)?情况[\s\S]{0,80}?名\s*称\s*[:：]\s*([^\n，。；;]{4,45}?会计师事务所(?:[（(]特殊普通合伙[）)])?)',
        r'名\s*称\s*[:：]\s*([^\n，。；;]{4,45}?会计师事务所(?:[（(]特殊普通合伙[）)])?)',
        r'(?:机构名称|事务所名称)\s*[:：]\s*([^\n，。；;]{4,45}?会计师事务所(?:[（(]特殊普通合伙[）)])?)',
    ]
    for pat in pats:
        m = re.search(pat, tt)
        if m:
            nm = _norm_firm(m.group(1))
            if nm and '会计师事务所' in nm:
                return nm
    # 机构名录最长匹配：优先正文，其次标题
    nm = match_firm(tt, firms)
    if nm:
        return _norm_firm(nm)
    if title:
        t2 = re.sub(r'20\d{2}\s*年度', '', title)
        m = re.search(r'(?:续聘|聘任|聘请|变更|改聘)(?:公司)?(?:的)?(.{4,45}?会计师事务所)', t2)
        if m:
            return _norm_firm(m.group(1))
        nm = match_firm(title, firms)
        if nm:
            return _norm_firm(nm)
    return ''


def parse_prev_firm(text, firms):
    tt = re.sub(r'[ \t\u3000]+', '', text or '')
    for pat in (r'原(?:聘任|聘请|续聘|审计)(?:的)?会计师事务所(?:名称)?(?:为|是|[:：])\s*([^\n，。；;]{4,45}?会计师事务所(?:[（(]特殊普通合伙[）)])?)',
                r'原(?:审计机构|会计师事务所)(?:名称)?(?:为|是|[:：])\s*([^\n，。；;]{4,45}?会计师事务所(?:[（(]特殊普通合伙[）)])?)',
                r'前任(?:会计师事务所|审计机构)(?:名称)?(?:为|是|[:：])\s*([^\n，。；;]{4,45}?会计师事务所(?:[（(]特殊普通合伙[）)])?)',
                r'拟变更(?:的)?会计师事务所(?:名称)?(?:为|是|[:：])\s*([^\n，。；;]{4,45}?会计师事务所(?:[（(]特殊普通合伙[）)])?)',
                r'解聘(?:的)?(?:会计师)?事务所(?:名称)?(?:为|是|[:：])\s*([^\n，。；;]{4,45}?会计师事务所(?:[（(]特殊普通合伙[）)])?)'):
        m = re.search(pat, tt)
        if m:
            return _norm_firm(m.group(1))
    m = re.search(r'原(?:聘任|聘请)(?:的)?(?:会计师)?事务所([^\n]{0,30}?)(?:为|是)([^\n，。；;]{4,45}?会计师事务所)', tt)
    if m:
        return _norm_firm(m.group(2))
    # 宽松兜底：原审计机构为「XX会计师事务所」/「XX所」
    m = re.search(r'(?:原|前任)(?:聘任|聘请|续聘|审计)?(?:的)?(?:会计师)?事务所(?:名称)?(?:为|是|[:：])\s*'
                  r'([^\n，。；;]{2,20}?(?:会计师事务所(?:[（(]特殊普通合伙[）)])?|[^\n，。；;]{2,12}所))', tt)
    if m:
        return _norm_firm(m.group(1))
    return ''


def parse_tenure(text):
    tt = re.sub(r'[ \t\u3000]+', '', text or '')
    for pat in (r'(?:已)?连续\s*(?:为(?:本)?公司)?\s*(?:提供(?:审计|财务审计)?服务)?\s*(\d{1,2})\s*年',
                r'连续\s*(\d{1,2})\s*年(?:为|担任|提供)',
                r'已连续服务\s*(\d{1,2})\s*年',
                r'已(?:为(?:本)?公司)?提供\s*(\d{1,2})\s*年(?:的)?(?:审计|财务审计)?服务',
                r'连续服务\s*(\d{1,2})\s*年',
                r'服务年限(?:为|：|:)?\s*(\d{1,2})\s*年',
                r'(?:已)?连续(?:提供)?(?:了)?\s*(\d{1,2})\s*年'):
        m = re.search(pat, tt)
        if m:
            v = int(m.group(1))
            if 1 <= v <= 40:
                return str(v)
    return ''


FEE_PATS = [
    r'(?:本期|本年度|年度|202\d年度)?(?:财务报[表告]审计费用|审计费用|审计收费|审计报酬)'
    r'(?:总额|合计)?(?:为|是|共|计|[:：])?\s*(?:人民币)?\s*([\d,，]{2,12}(?:\.\d{1,2})?)\s*(万元|元)',
    r'(?:财务报[表告]审计费用|审计费用|审计收费|审计报酬)(?:总额|合计)?(?:为|是|约|共计)?\s*'
    r'([\d,，]{2,12}(?:\.\d{1,2})?)\s*(万元|元)',
    r'(?:审计费用|审计收费|审计报酬)[^。；;]{0,24}?([\d,，]{2,12}(?:\.\d{1,2})?)\s*(万元|元)\s*[（(]?含税',
    r'([\d,，]{2,12}(?:\.\d{1,2})?)\s*(万元|元)\s*[（(]?(?:含税)?[）)]?\s*[，,。]?\s*其中',
]
FEE_STOP = re.compile(r'(?:业务收入|营业收入|赔偿|资产|净资产|收费总额|客户)')


def parse_fee(text):
    tt = re.sub(r'[ \t\u3000]+', '', text or '')
    best, best_score = '', -99
    for m in re.finditer(FEE_PATS[0], tt):
        seg = m.group(0)
        pre = tt[max(0, m.start() - 30):m.start()]
        if FEE_STOP.search(pre):
            continue
        val = m.group(1).replace('，', ',').replace(',', '')
        try:
            num = float(val)
        except Exception:
            continue
        if num <= 0 or num > 100000:
            continue
        score = 0
        if '审计费用' in seg:
            score += 2
        if '总额' in seg or '合计' in seg:
            score -= 2
        if '内控' in seg or '内部控制' in seg:
            score -= 1
        if '收入' in pre:
            score -= 3
        if score > best_score:
            best_score, best = score, f'{num:g}{m.group(2)}'
    return best


def parse_meta(text, title, disc_year=None, firms=None):
    firms = firms if firms is not None else firm_names()
    return {
        'audit_year': parse_audit_year(title, text, disc_year),
        'firm_notice': parse_firm(title, text, firms),
        'prev_firm': parse_prev_firm(text, firms),
        'tenure': parse_tenure(text),
        'fee': parse_fee(text),
    }
