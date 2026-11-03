# -*- coding: utf-8 -*-
"""
MD&A 分章节抽取入库（SQLite）—— 第三版识别/提取逻辑

相对第二版（build_sections_v2.py）的改动
  1. 标题解析更稳：
     - 支持 1. / 1.1 / 1.1.1（ASCII 点）、（1）等阿拉伯编号；标题允许含数字。
     - 标题必须像"标题"：以汉字/引号开头，无句读，汉字≥2，数字占比≤20%。
       （旧版把「1.892 万吨」「％ 下降0.68」这类表格碎片当成标题，污染一级体系判定。）
     - 复选框符号扩展 √✓☑☒■□，且只剥离复选框片段。
     - 被排版拆行的标题只在"下一行不是编号行、不重复、字号接近"时才拼接。
  2. 一级体系判定只考虑"连续递增≥3"的编号样式，并以"编号重启次数少"区分父级/子级，
     杜绝表格数字样式篡位（旧版把整份文档误判为 title_only）。
  3. 边界改为 bbox 切片：同一页出现两个标题时，按标题的 y 坐标切分正文，
     不再让两节共享整页（旧版深振业A「核心竞争力」「主营业务分析」各 5 页完全重复）。
     章节正文从本节标题之后开始，到"下一个同级标题 / 一级标题 / 规范章节"之前结束。
  4. 关键词兜底：编号体系里缺某章时，用"标题样"的关键词行补位（title_only 与混合排版）。
  5. 文档级质量标记：mda_page / has_mda_title / end_ok / quality_flag
     （empty / wrong_chapter / wrong_start / truncated / ok），
     并修正第二版 detect_mode 的错误（旧版只取 industry 一章的模式，industry 缺失时整份文
     件被误记为 absent，实际 298 份文件并非 absent）。
  6. 章节 boundary 记录真实来源（cn / cn_p / ar / ar_p / dot / title_only + _sub）。
"""
import os
import re
import sys
import time
import sqlite3
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import pymupdf

from .config import settings

BASE = str(settings.mda_dir)
DEFAULT_DB = str(settings.db_path)

# ---------- 文本工具 ----------
CHECKBOX = re.compile(r"[\ue000-\uf8ff√✓☑☒■□●○]\s*(?:适用|不适用)\s*")
NOISE = re.compile(r"[。，；：,;!?]")
CJK = re.compile(r"[\u4e00-\u9fa5]")
DIGIT = re.compile(r"[0-9]")
BAD_START = re.compile(r"^[0-9%％+\-—.．·*＊/／|｜]")
UNIT_TAIL = re.compile(r"[0-9%％]$")
PAGENO = re.compile(r"^[-—\s]*\d{1,4}[-—\s]*$")
PAGEWORD = re.compile(r"^第\s*\d+\s*页(\s*共\s*\d+\s*页)?$")
COMPANY_REPORT = re.compile(r"(股份有限公司|有限责任公司|有限公司|集团|公司)"
                            r".{0,30}20\d{2}\s*年\s*年度报告")

CN_NUM = {c: i + 1 for i, c in enumerate("一二三四五六七八九")}
CN_NUM["十"] = 10


def cn_to_int(s):
    """中文数字转整数（支持 十、十一、二十、二十三）。"""
    if s in CN_NUM:
        return CN_NUM[s]
    if "十" in s:
        a, _, b = s.partition("十")
        tens = CN_NUM.get(a, 1) if a else 1
        ones = CN_NUM.get(b, 0) if b else 0
        return tens * 10 + ones
    return 0


# ---------- 标题识别 ----------
ORD_CN = re.compile(r"^([一二三四五六七八九十]{1,3})[、.．]\s*(.+)$")
ORD_CN_P = re.compile(r"^[（(]([一二三四五六七八九十]{1,3})[）)]\s*(.+)$")
ORD_DOT = re.compile(r"^(\d{1,2}(?:[.．]\d{1,2}){1,2})[、．]?\s*(.+)$")
ORD_AR_P = re.compile(r"^[（(](\d{1,2})[）)]\s*(.+)$")
ORD_AR = re.compile(r"^(\d{1,2})[、.．]\s*(.+)$")

# 关键词按“具体程度”排序：命中越靠前，越像该章的正式标题
TARGETS = {
    "industry":   ("行业情况", ["所处行业", "行业情况", "行业格局", "行业竞争", "行业发展", "行业说明"]),
    "business":   ("主要业务", ["从事的主要业务", "从事的业务", "主要业务", "业务情况",
                                "业务概要", "产品与服务", "经营模式"]),
    "core_comp":  ("核心竞争力", ["核心竞争力"]),
    "main_ops":   ("主营业务分析", ["主营业务分析", "经营情况讨论", "报告期内经营情况",
                                    "经营情况概述", "经营分析", "概述"]),
    "non_main":   ("非主营业务", ["非主营业务"]),
    "assets":     ("资产及负债", ["资产及负债", "资产、负债", "资产负债", "财务状况", "资产负债状况"]),
    "invest":     ("投资状况", ["投资状况", "对外投资", "对外股权投资", "投资情况", "股权投资"]),
    "subsidiary": ("子公司参股", ["主要控股参股", "主要子公司", "参股公司", "控股子公司"]),
    "structured": ("结构化主体", ["结构化主体"]),
    "outlook":    ("未来发展", ["未来发展的讨论与分析", "未来发展", "未来展望",
                                "经营计划", "发展战略", "发展规划"]),
    "research":   ("接待调研", ["接待调研", "调研沟通", "调研活动", "沟通采访", "投资者关系活动"]),
}
STYLE_DEPTH_CN = {"cn_p": 1, "dot": 1, "ar": 2, "ar_p": 3}
STYLE_DEPTH_AR = {"ar_p": 1, "dot": 1, "cn_p": 2, "cn": 2}
STYLE_DEPTH_CNP = {"ar": 1, "dot": 1, "ar_p": 2, "cn": 2}
STYLE_DEPTH_OTHER = {"cn_p": 1, "ar": 1, "dot": 1, "ar_p": 2, "cn": 1}
# 编号样式的固有层级（与“谁是本文件一级体系”无关）：
# 一、/1、= 章； （一）= 节； 1.1 = 节； （1）= 条
GLOBAL_DEPTH = {"cn": 1, "ar": 1, "cn_p": 2, "dot": 2, "ar_p": 3}

# “正式标题”关键词：命中者优先当章节起点（避免把伞状章节/邻近小节当成本章）
PRIMARY = {
    "industry":   ["所处行业", "行业情况"],
    "business":   ["从事的主要业务", "从事的业务", "主要业务"],
    "core_comp":  ["核心竞争力"],
    "main_ops":   ["主营业务分析"],
    "non_main":   ["非主营业务"],
    "assets":     ["资产及负债", "资产、负债"],
    "invest":     ["投资状况", "对外投资"],
    "subsidiary": ["主要控股参股", "主要子公司"],
    "structured": ["结构化主体"],
    "outlook":    ["未来发展", "未来展望"],
    "research":   ["接待调研", "调研活动"],
}
ORDER = ["non_main", "industry", "business", "core_comp", "main_ops", "assets",
         "invest", "subsidiary", "structured", "outlook", "research"]
# 极少见的裸标题（整行恰好是这些词时归一化）
EXACT_KEYS = {"行业": "industry", "所处行业": "industry", "经营情况": "main_ops",
              "调研": "research", "调研活动": "research"}

RESCUE_MAXLEN = 30


def classify(title):
    for key in ORDER:
        for kw in TARGETS[key][1]:
            if kw in title:
                return key
    return None


def _ambiguous_title(title):
    """同时命中多个规范章节的“伞状标题”不作为单一章节锚点。"""
    hits = [key for key in ORDER if any(kw in title for kw in TARGETS[key][1])]
    if len(hits) > 1:
        if "非主营业务" in title and "non_main" in hits:
            return False
        return True
    return False


def heading_key(title):
    t = title.strip()
    if t in EXACT_KEYS:
        return EXACT_KEYS[t]
    if _ambiguous_title(title):
        return None
    return classify(title)


def build_depths(heads, top_style):
    """按文档实际出现的编号样式推断层级：1=章，2=节，3=条，4=款。
    典型层级：一、→（一）→1、→（1）；缺（一）时 1、直接是第 2 层。"""
    styles = {h[7] for h in heads if h[7] not in ("title_only",)}
    d = {top_style: 1}
    has_cnp = "cn_p" in styles
    if top_style == "cn":
        d["cn_p"] = 2
        d["ar"] = d["dot"] = 3 if has_cnp else 2
        d["ar_p"] = d["ar"] + 1
    elif top_style == "ar":
        d.update({"cn_p": 2, "cn": 2, "dot": 2, "ar_p": 2})
    elif top_style == "cn_p":
        d.update({"cn": 2, "ar": 2, "dot": 2, "ar_p": 3})
    else:
        for st in styles:
            d.setdefault(st, 2)
    for st in styles:
        d.setdefault(st, 2)
    return d


def kw_rank(title, key):
    for i, kw in enumerate(TARGETS[key][1]):
        if kw in title:
            return i
    return 99


def _valid_title(title):
    """标题形态校验：像标题，而不是正文/表格碎片。"""
    if not (2 <= len(title) <= 44):
        return False
    if NOISE.search(title):
        return False
    if len(CJK.findall(title)) < 2:
        return False
    if BAD_START.search(title):
        return False
    if UNIT_TAIL.search(title):
        return False
    if len(DIGIT.findall(title)) / max(len(title), 1) > 0.2:
        return False
    return True


def parse_line(text):
    """返回 (样式, 序号, 标题) 或 None。样式：cn / cn_p / dot / ar_p / ar。"""
    s = CHECKBOX.sub("", text.strip()).strip(" 　\t")
    if not s:
        return None
    m = ORD_DOT.match(s)
    if m:
        title = m.group(2).strip()
        if _valid_title(title):
            parts = re.split(r"[.．]", m.group(1))
            try:
                num = int(parts[0]) * 100 + int(parts[1])
                if len(parts) > 2:
                    num = num * 10 + int(parts[2])
            except ValueError:
                num = 0
            return "dot", num, title
    m = ORD_CN_P.match(s)
    if m:
        title = m.group(2).strip()
        if _valid_title(title):
            return "cn_p", cn_to_int(m.group(1)), title
    m = ORD_CN.match(s)
    if m:
        title = m.group(2).strip()
        if _valid_title(title):
            return "cn", cn_to_int(m.group(1)), title
    m = ORD_AR_P.match(s)
    if m:
        title = m.group(2).strip()
        if _valid_title(title):
            return "ar_p", int(m.group(1)), title
    m = ORD_AR.match(s)
    if m:
        title = m.group(2).strip()
        if _valid_title(title):
            return "ar", int(m.group(1)), title
    return None


def extract_lines(doc):
    """一次遍历取每页文本行；按 (y, x) 排序。返回 (pages, body_size)。"""
    pages = []
    all_sizes = []
    for page in doc:
        lines = []
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                spans = ln["spans"]
                txt = "".join(sp["text"] for sp in spans).strip()
                if not txt:
                    continue
                size = max((sp["size"] for sp in spans), default=10.0)
                lines.append({"txt": txt, "y0": ln["bbox"][1], "y1": ln["bbox"][3],
                              "x0": ln["bbox"][0], "size": size})
                all_sizes.extend([size] * min(len(txt), 20))
        lines.sort(key=lambda l: (round(l["y0"], 1), l["x0"]))
        pages.append(lines)
    all_sizes.sort()
    body = all_sizes[len(all_sizes) // 2] if all_sizes else 10.0
    return pages, body


def _in_table_row(lines, i):
    """同一 y 带上有 ≥3 条短行 → 大概率是三线表单元格，不作为标题。"""
    y = lines[i]["y0"]
    same = 0
    for j, l2 in enumerate(lines):
        if j == i:
            continue
        if abs(l2["y0"] - y) <= 2.5 and len(l2["txt"]) <= 25:
            same += 1
    return same >= 2


def collect(pages):
    """收集候选标题行（含被排版拆行的标题）。"""
    rows = []
    for pno, lines in enumerate(pages):
        used = set()
        for i, l in enumerate(lines):
            if i in used:
                continue
            txt = l["txt"]
            if not (2 <= len(txt) <= 44):
                continue
            if _in_table_row(lines, i):
                continue
            p = parse_line(txt)
            if not p:
                continue
            style, num, title = p
            y0, y1 = l["y0"], l["y1"]
            # 拼接被拆行的标题：下一行不是编号行、不是重复、字号接近、合计不过长
            if len(title) <= 10 and i + 1 < len(lines):
                nxt = lines[i + 1]
                nt = nxt["txt"]
                if (2 <= len(nt) <= 24 and not NOISE.search(nt) and not parse_line(nt)
                        and len(CJK.findall(nt)) >= 2
                        and not BAD_START.search(nt)
                        and nt not in title
                        and abs(nxt["size"] - l["size"]) <= 1.0
                        and len(title) + len(nt) <= 44):
                    title = title + nt
                    y1 = nxt["y1"]
                    used.add(i + 1)
            title = CHECKBOX.sub("", title).strip()
            if not _valid_title(title):
                continue
            rows.append((pno, y0, y1, style, num, title, l["size"]))
    return rows


def _run_stats(items):
    """items=[(page,num,size)] 按文档顺序；返回 (连续递增长度, 编号重启次数)。"""
    run = cur = 1
    restarts = 0
    for i in range(1, len(items)):
        prev, cur_ = items[i - 1], items[i]
        if cur_[0] == prev[0] and cur_[1] == prev[1]:
            continue
        if cur_[1] == prev[1] + 1:
            cur += 1
            run = max(run, cur)
        else:
            restarts += 1
            cur = 1
    return run, restarts


def detect_top_level(rows):
    """一级体系判定：只考虑"连续递增≥3"的样式；
    章级样式（cn/ar）优先，其次才是（一）//（1）/1.1 等节级样式；
    同级样式按 字号 > 编号重启次数少 > 首次出现靠前 排序。"""
    by_style = {}
    for pno, _y0, _y1, style, num, _t, size in rows:
        by_style.setdefault(style, []).append((pno, num, size))
    eligible = []
    for style, items in by_style.items():
        items = sorted(items)
        run, restarts = _run_stats(items)
        if run < 3:
            continue
        sizes = sorted(i[2] for i in items)
        med = sizes[len(sizes) // 2]
        first = min(i[0] for i in items)
        eligible.append((style, (round(med, 1), -restarts, -first, run, len(items))))
    if not eligible:
        return None, 0
    chapter = [e for e in eligible if e[0] in ("cn", "ar")]
    pool = chapter or eligible
    style, key = max(pool, key=lambda e: e[1])
    return style, key[3]


def _title_like(line, body):
    """是否像一条独立标题（用于关键词兜底）。"""
    t = line["txt"]
    if not (3 <= len(t) <= RESCUE_MAXLEN):
        return False
    if NOISE.search(t) or BAD_START.search(t) or UNIT_TAIL.search(t):
        return False
    if len(CJK.findall(t)) < 3:
        return False
    if line["size"] < body - 0.6:
        return False
    return True


def scan_headings(pages, body):
    """返回 (headings, mode, n_rescue)。
    headings: [[page, y0, y1, num, title, tag, key, style]]
    tag ∈ 样式名 / 样式名_sub / kw。
    """
    rows = collect(pages)
    top_style, run = detect_top_level(rows)
    numbered = run >= 3

    heads = []
    if numbered:
        for pno, y0, y1, style, num, title, _size in rows:
            tag = style if style == top_style else style + "_sub"
            heads.append([pno, y0, y1, num, title, tag, heading_key(title), style])

    # 关键词兜底：编号体系没覆盖到的规范章节，用"标题样"关键词行补位
    covered = {h[6] for h in heads if h[6]}
    rescue = 0
    if len(covered) < len(TARGETS):
        for pno, lines in enumerate(pages):
            for i, l in enumerate(lines):
                t = l["txt"]
                key = heading_key(t)
                if not key or key in covered:
                    continue
                if not _title_like(l, body):
                    continue
                if _in_table_row(lines, i):
                    continue
                kws = TARGETS[key][1]
                starts_kw = any(t.startswith(kw) for kw in kws)
                top_page = l["y0"] < 150 and any(kw in t for kw in kws) and len(t) <= 12
                # 正文里顺带提及不算；字号明显大于正文或位于页首的短标题才算
                if not (starts_kw or top_page):
                    continue
                if l["size"] < body + 0.3 and not top_page:
                    continue
                heads.append([pno, l["y0"], l["y1"], 0, t, "kw", key, "title_only"])
                covered.add(key)
                rescue += 1
                if len(covered) >= len(TARGETS):
                    break
            if len(covered) >= len(TARGETS):
                break

    heads.sort(key=lambda h: (h[0], h[1]))
    mode = top_style if numbered else "title_only"
    if numbered:
        styles = {h[7] for h in heads if h[7] != "title_only"}
        if "cn" in styles:
            mode = "cn"
        elif "ar" in styles:
            mode = "ar"
    if not heads:
        mode = "none"
    return heads, mode, rescue


# ---------- 章节正文切片 ----------
def slice_text(pages, start, end):
    """start=(page, y1_after_heading)，end=(page, y0_of_boundary) 或 None。
    返回 (text, 最后包含文字的页码)。"""
    p0, y_start = start
    p1, y_end = (len(pages) - 1, 1e9) if end is None else end
    if p1 < p0:
        return "", p0
    out = []
    last = p0
    for p in range(p0, min(p1, len(pages) - 1) + 1):
        for l in pages[p]:
            if p == p0 and l["y1"] <= y_start + 0.5:
                continue
            if end is not None and p == p1 and l["y0"] >= y_end - 0.5:
                continue
            out.append(l["txt"])
            last = p
    return "\n".join(out), last


def pick_starts(heads, top_style, depths):
    """每个规范章节取一个起点：
    1) 标题含该章最正式关键词（PRIMARY）者优先；
    2) 层级越靠外越优先；3) 关键词越具体越优先；4) 取文档中靠前者。"""
    best = {}
    for h in heads:
        pno, y0, y1, num, title, tag, key, style = h
        if not key:
            continue
        primary = 0 if any(k in title for k in PRIMARY[key]) else 1
        level = 9 if tag == "kw" else depths.get(style, 2)
        cand = (primary, level, kw_rank(title, key), pno, y0, y1, title, tag, style)
        cur = best.get(key)
        if cur is None or cand < cur:
            best[key] = cand
    starts = []
    for key, (primary, level, kr, pno, y0, y1, title, tag, style) in best.items():
        starts.append({"key": key, "p": pno, "y0": y0, "y": y1,
                       "title": title, "tag": tag, "style": style,
                       "kw_rank": kr, "level": level, "primary": primary})
    starts.sort(key=lambda s: (s["p"], s["y0"]))
    return starts


def _is_footer(l, body):
    t = l["txt"].strip()
    if PAGENO.match(t) or PAGEWORD.match(t):
        return True
    if COMPANY_REPORT.search(t) and len(t) <= 60:
        return True
    if l["size"] < body - 0.6:
        return True
    return False


TAIL_PUNCT = re.compile(r"[。！？；：%”）】》〉]$")
TAIL_WORD = re.compile(r"(?:否|是|无|不适用|适用|未披露|不涉及|已完成|未完成|未披露、透露或泄露未公开重大信息)$")
URL_RE = re.compile(r"https?://|www\.|cninfo|szse|sse\.com", re.I)
EXT_TAIL = re.compile(r"(?:\.docx?|\.pdf|\.xlsx?|\.html?|\.htm)$", re.I)
# 「接待调研」一节的末页常以调研记录表名称、附件名或记录编号收尾，文档本身是完整的
RECORD_TAIL = re.compile(r"(?:记录表|纪要|调研记录|活动记录|活动附件)[》〉]?$")
# 调研表格里还会出现问卷地址、投票系统参数、调研档案名与访谈条目
TAIL_REF = re.compile(r"(投资者关系|调研活动|orgId=|stockcode=|\.do\?|%20\d|栏目|档案|活动表|\?(?:[a-z]{0,10})?\d{3,})")


def _tail_complete(seq):
    """判断末页是否正常收尾。

    年报末页的常见形态是「正文末句 + 投资者关系活动记录表」：最后一行是表格单元
    （「否」「0」「29」）或问卷链接，本身不是句子。旧版只看最后一行，把这类正常
    文件大量判为 truncated。改为看末若干行的整体形态。
    """
    if not seq:
        return False
    last = seq[-1]
    if len(last) <= 14:
        return True
    if TAIL_PUNCT.search(last) or TAIL_WORD.search(last) or URL_RE.search(last):
        return True
    if EXT_TAIL.search(last) or RECORD_TAIL.search(last) or TAIL_REF.search(last):
        return True
    # 调研记录表常带编号、附件名或问卷链接（「…记录表-17111401」「…记录表》编号：2022-005」）
    if len(last) <= 60 and re.search(r"(记录表|纪要|调研记录|活动记录)", last):
        return True
    if re.search(r"\.(?:docx?|pdf|xlsx?|html?|htm)(?:\?|$)", last, re.I):
        return True
    if re.search(r"[\ue000-\uf8ff□√☑✓■]", last) and re.search(r"[是否]$", last):
        return True
    # 最后一处句末标点之后若全是短行，视为「正文已收尾 + 表格」
    for i in range(len(seq) - 1, -1, -1):
        if TAIL_PUNCT.search(seq[i]):
            return all(len(x) <= 14 for x in seq[i + 1:])
    return False


def find_mda_page(pages, body):
    """章标题所在页。优先「管理层讨论与分析」；A+H 年报常写作「董事会报告书」，作为第二轮回退。"""
    for keys in (("管理层讨论与分析", "经营情况讨论与分析"),
                 ("董事会报告书", "董事会报告")):
        for pno, lines in enumerate(pages):
            for l in lines:
                t = l["txt"]
                if len(t) > 26 or NOISE.search(t):
                    continue
                if any(k in t for k in keys) and l["size"] >= body - 0.3:
                    return pno
    return None


def extract(path):
    doc = pymupdf.open(path)
    npages = doc.page_count
    pages, body = extract_lines(doc)
    heads, mode, rescue = scan_headings(pages, body)

    top_style = mode
    depths = build_depths(heads, top_style)
    starts = pick_starts(heads, top_style, depths)

    out = {}
    for i, s in enumerate(starts):
        p0, sy0 = s["p"], s["y0"]
        start_depth = depths.get(s["style"], 2)
        cands = []
        for s2 in starts[i + 1:]:
            if (s2["p"], s2["y0"]) > (p0, sy0):
                cands.append((s2["p"], s2["y0"]))
        # 本节之后"同级或更高级"的标题都是结束边界（跳过更深层的小节标题）
        for h in heads:
            if (h[0], h[1]) <= (p0, sy0):
                continue
            if depths.get(h[7], 2) <= start_depth:
                cands.append((h[0], h[1]))
        chosen = None
        for bp, by0 in sorted(set(cands)):
            if (bp, by0) <= (p0, sy0):
                continue
            text, last = slice_text(pages, (p0, s["y"]), (bp, by0))
            if text.strip():
                chosen = (text, last)
                break
        if chosen is None:
            text, last = slice_text(pages, (p0, s["y"]), None)
        else:
            text, last = chosen
        if not text.strip():
            continue
        span = last - p0 + 1
        boundary = s["tag"]
        if span > npages * 0.7 and npages >= 10 and len(starts) > 1:
            boundary = "overspan"
        rec = {"key": s["key"], "label": TARGETS[s["key"]][0], "title": s["title"],
               "start": p0 + 1, "end": last + 1, "n_pages": span,
               "chars": len(text), "text": text, "mode": boundary}
        old = out.get(s["key"])
        if old is None or rec["chars"] > old["chars"]:
            out[s["key"]] = rec

    # ---- 文档级质量标记 ----
    mda_page = find_mda_page(pages, body)
    last_lines = [l for l in pages[-1] if not _is_footer(l, body)] if pages else []

    # 末页收尾行：取最后 6 条非页脚/页码行。不能用 _is_footer 过滤小字号行——
    # 年报末页常是表格，单元格字号小于正文，会被整体滤掉，导致 end_ok 误判为截断。
    tail_seq = []
    for l in reversed(pages[-1] if pages else []):
        _t = l["txt"].strip()
        if not _t:
            continue
        # 页脚「公司名 + 20XX 年年度报告」常用空格填充到整行宽，长度会远超 60，
        # 必须按去掉空白后的长度判断，否则它会成为“末行”，把所有正常文件判成截断。
        if PAGENO.match(_t) or PAGEWORD.match(_t):
            continue
        _flat = re.sub(r"\s", "", _t)
        # 页脚既可能是「2025 年年度报告」，也可能是中文数字「二零一五年年度报告」，只认年度报告字样
        if "年度报告" in _flat and len(_flat) <= 40:
            continue
        tail_seq.append(_t)
        if len(tail_seq) >= 6:
            break
    tail_seq.reverse()
    tail = tail_seq[-1] if tail_seq else ""
    if not tail and last_lines:
        tail = last_lines[-1]["txt"]
        tail_seq = [tail]
    end_ok = _tail_complete(tail_seq)
    n_sec = sum(1 for r in out.values() if r["chars"] > 0)
    if n_sec == 0:
        quality = "empty"
    elif mda_page is None and n_sec < 3:
        quality = "wrong_chapter"
    elif mda_page is not None and mda_page > max(2, int(npages * 0.15)):
        quality = "wrong_start"
    elif not end_ok:
        quality = "truncated"
    else:
        quality = "ok"
    has_mda = mda_page is not None and mda_page <= max(2, int(npages * 0.15))
    doc.close()

    results = []
    for key, (label, _kw) in TARGETS.items():
        if key in out:
            r = out[key]
            results.append({"key": key, "label": label, "title": r["title"],
                            "start": r["start"], "end": r["end"], "n_pages": r["n_pages"],
                            "chars": r["chars"], "text": r["text"], "mode": r["mode"]})
        else:
            results.append({"key": key, "label": label, "title": "", "start": None,
                            "end": None, "n_pages": 0, "chars": 0, "text": "",
                            "mode": "absent"})
    meta = {"mode": mode, "has_mda": has_mda, "mda_page": (mda_page + 1) if mda_page is not None else 0,
            "end_ok": end_ok, "quality": quality, "n_headings": len(heads), "n_rescue": rescue}
    return npages, results, meta


# ---------- 入库 ----------
def init_db(conn, reset=True):
    if reset:
        conn.executescript("DROP TABLE IF EXISTS sections_fts; DROP TABLE IF EXISTS sections;"
                           "DROP TABLE IF EXISTS documents;")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY, code TEXT, year INTEGER, name TEXT, file TEXT,
        pages INTEGER, n_sections INTEGER, detect_mode TEXT,
        quality_flag TEXT, mda_page INTEGER, has_mda_title INTEGER, end_ok INTEGER,
        n_headings INTEGER, built_at TEXT,
        UNIQUE(code, year));
    CREATE TABLE IF NOT EXISTS sections (
        id INTEGER PRIMARY KEY, doc_id INTEGER, code TEXT, year INTEGER, name TEXT,
        section_key TEXT, section_label TEXT, title_raw TEXT,
        start_page INTEGER, end_page INTEGER, n_pages INTEGER, chars INTEGER,
        text TEXT, boundary TEXT, text_clean TEXT,
        clean_chars INTEGER, is_na INTEGER DEFAULT 0, table_only INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS idx_sec_key ON sections(section_key);
    CREATE INDEX IF NOT EXISTS idx_sec_year ON sections(year);
    CREATE INDEX IF NOT EXISTS idx_sec_code ON sections(code);
    CREATE INDEX IF NOT EXISTS idx_sec_doc ON sections(doc_id);
    CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
        text, content='sections', content_rowid='id', tokenize='unicode61');
    """)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("year", nargs="?", default="2025")
    ap.add_argument("--db", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sample", type=int, default=0, help="随机抽样 N 份（用于快速验证）")
    ap.add_argument("--seed", type=int, default=20251028)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--keep", action="store_true", help="不删除已存在的目标库")
    ap.add_argument("--shard", default="", help="分片运行，如 1/4 表示第1片（4片）")
    ap.add_argument("--append", action="store_true", help="追加到已有库（分片用）")
    ap.add_argument("--no-fts", action="store_true", help="末尾不重建 FTS（分片用）")
    ap.add_argument("--codes", default="", help="只重建这些代码（逗号分隔，隐含 --append）")
    ap.add_argument("--from-file", default="", help="从文件读取代码清单（每行一个，隐含 --append）")
    args = ap.parse_args()

    year = args.year
    db = args.db or DEFAULT_DB
    src = os.path.join(BASE, year)
    files = sorted(f for f in os.listdir(src) if f.endswith(".pdf"))
    if args.sample:
        import random
        random.seed(args.seed)
        files = random.sample(files, min(args.sample, len(files)))
    only = set()
    if args.codes:
        only |= {c.strip() for c in args.codes.split(",") if c.strip()}
    if args.from_file:
        with open(args.from_file, encoding="utf-8-sig") as f:
            only |= {l.strip() for l in f if l.strip()}
    if only:
        files = [f for f in files
                 if (m2 := re.match(r"\d{4}-(\d{6})-", f)) and m2.group(1) in only]
        args.append = True          # 增量重建不得清库
        print(f"增量重建：{len(files)} 份")
    if args.shard:
        si, sn = (int(x) for x in args.shard.split("/"))
        files = files[si - 1::sn]
    if args.limit:
        files = files[:args.limit]

    if os.path.exists(db) and not (args.keep or args.append):
        os.remove(db)
    conn = sqlite3.connect(db, timeout=120)
    init_db(conn, reset=not args.append)
    cur = conn.cursor()

    tasks = []
    for fn in files:
        m = re.match(r"(\d{4})-(\d{6})-(.+)\.pdf$", fn)
        if m:
            tasks.append((fn, int(m.group(1)), m.group(2), m.group(3)))

    t0 = time.time()
    done = nsec = 0
    modes, quals = Counter(), Counter()

    def work(t):
        fn, y, code, name = t
        try:
            return t, extract(os.path.join(src, fn))
        except Exception as e:
            print("  [ERR]", fn, repr(e), flush=True)
            return t, (0, [], {})

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, t) for t in tasks]
        for i, fu in enumerate(as_completed(futs), 1):
            (fn, y, code, name), (npages, secs, meta) = fu.result()
            if npages == 0:
                continue
            found = sum(1 for s in secs if s["chars"] > 0)
            mode = meta.get("mode", "")
            modes[mode] += 1
            quals[meta.get("quality", "")] += 1
            cur.execute("SELECT id FROM documents WHERE code=? AND year=?", (code, y))
            row = cur.fetchone()
            if row:
                doc_id = row[0]
                cur.execute("DELETE FROM sections WHERE doc_id=?", (doc_id,))
                cur.execute("""UPDATE documents SET pages=?,n_sections=?,detect_mode=?,
                               quality_flag=?,mda_page=?,has_mda_title=?,end_ok=?,n_headings=?,built_at=?
                               WHERE id=?""",
                            (npages, found, mode, meta.get("quality"), meta.get("mda_page", 0),
                             int(meta.get("has_mda", 0)), int(meta.get("end_ok", 0)),
                             meta.get("n_headings", 0), time.strftime("%Y-%m-%d %H:%M"), doc_id))
            else:
                cur.execute("""INSERT INTO documents(code,year,name,file,pages,n_sections,
                               detect_mode,quality_flag,mda_page,has_mda_title,end_ok,n_headings,built_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (code, y, name, fn, npages, found, mode, meta.get("quality"),
                             meta.get("mda_page", 0), int(meta.get("has_mda", 0)),
                             int(meta.get("end_ok", 0)), meta.get("n_headings", 0),
                             time.strftime("%Y-%m-%d %H:%M")))
                doc_id = cur.lastrowid
            for s in secs:
                cur.execute("""INSERT INTO sections(doc_id,code,year,name,section_key,section_label,
                               title_raw,start_page,end_page,n_pages,chars,text,boundary)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (doc_id, code, y, name, s["key"], s["label"], s["title"],
                             s["start"], s["end"], s["n_pages"], s["chars"], s["text"], s["mode"]))
            done += 1
            nsec += found
            if i % 200 == 0:
                conn.commit()
                print(f"   {i}/{len(tasks)}  入库 {done} 份 / {nsec} 个非空章节  "
                      f"{time.time() - t0:.0f}s", flush=True)

    conn.commit()
    if not args.no_fts:
        cur.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
        conn.commit()

    print(f"\n=== {year} 年入库结果（v3）===")
    for lab, n, ac, ap_ in cur.execute("""
            SELECT section_label, COUNT(*), ROUND(AVG(chars)), ROUND(AVG(n_pages),1)
            FROM sections WHERE year=? AND chars>0 GROUP BY section_key ORDER BY COUNT(*) DESC""",
            (int(year),)):
        print(f"  {lab:<8} {n:>5} 份   平均 {ac:>6.0f} 字  {ap_:>4.1f} 页")
    print("文档模式:", dict(modes))
    print("质量标记:", dict(quals))
    print(f"完成 {done} 份，用时 {time.time() - t0:.0f}s，库：{db}")
    conn.close()


if __name__ == "__main__":
    main()
