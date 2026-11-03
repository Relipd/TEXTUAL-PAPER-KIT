# -*- coding: utf-8 -*-
"""从「公司业务概要」切片中识别并提取 主要业务 / 核心竞争力 / 主要资产变化。

背景：2015–2020 年这三项属「第三节 公司业务概要」，与 MD&A 平行。
`fetch_biz.py` 已把该章切成 `MDA_BIZ/{年份}/*.pdf`，本脚本做二级切分并入库。

入库表 `biz_sections`（与 `sections` 并列，不改动 11 槽位结构）：
  code, year, name, section_key(biz_main/biz_core/biz_asset), title_raw,
  start_page, end_page, n_pages, chars, text, text_clean, clean_chars, level

切分规则
  1. 以行首编号判定标题：一、/（一）/1、/（1）/1.1；中文序号为一级，其余为二级。
  2. 标题行须满足：长度 4–44、无句读、汉字 ≥2。
  3. 每个目标取首个匹配标题，正文到「下一个同级或更高级标题」为止；
     同级标题优先，避免「（一）行业格局」把「二、核心竞争力」截断。
  4. 标题里没有关键词时，回退到「含关键词的独立短行」。

用法
  python build_biz_sections.py --pilot 50
  python build_biz_sections.py --apply --workers 4
"""
import argparse
import glob
import os
import random
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pymupdf

from .clean_text_v3 import _is_na, clean

from .config import settings

BASE = str(settings.data_dir)
SRC = str(settings.biz_dir)
DB = str(settings.db_path)

CN_HEAD = re.compile(r"^([一二三四五六七八九十]{1,3})\s*[、.．]\s*(.*)$")
CN_P_HEAD = re.compile(r"^[（(]\s*([一二三四五六七八九十]{1,3})\s*[）)]\s*(.*)$")
AR_HEAD = re.compile(r"^(\d{1,2})\s*[、.．]\s*(.*)$")
AR_P_HEAD = re.compile(r"^[（(]\s*(\d{1,2})\s*[）)]\s*(.*)$")
DOT_HEAD = re.compile(r"^(\d{1,2}\.\d{1,2})\s*(.*)$")
NOISE = re.compile(r"[。；，,;!?！？]")
CJK = re.compile(r"[\u4e00-\u9fa5]")

# 关键词优先级：越具体越靠前
RULES = [
    ("core_comp", ("核心竞争力",)),
    ("assets_change", ("主要资产重大变化", "主要资产发生重大变化", "资产发生重大变化", "主要资产变化")),
    ("business", ("主要业务", "主营业务", "经营模式", "主要产品", "业务概要", "经营情况")),
]
LABEL = {"business": "主要业务", "core_comp": "核心竞争力", "assets_change": "主要资产变化"}


def parse_heading(line):
    """返回 (level, title) 或 None。level 1=章内一级，2=二级。"""
    s = line.strip()
    if not (4 <= len(s) <= 44):
        return None
    for rx, lv in ((CN_HEAD, 1), (CN_P_HEAD, 2), (AR_HEAD, 2), (AR_P_HEAD, 2), (DOT_HEAD, 2)):
        m = rx.match(s)
        if m:
            title = m.group(2).strip()
            title = re.sub(r"[\s.。·]+$", "", title)
            if len(title) >= 2 and not NOISE.search(title) and len(CJK.findall(title)) >= 2:
                return lv, title
    return None


def match_key(title):
    for key, kws in RULES:
        for kw in kws:
            if kw in title:
                return key
    return None


def read_lines(path):
    doc = pymupdf.open(path)
    out = []
    for pno, page in enumerate(doc):
        for l in (page.get_text("text") or "").split(chr(10)):
            t = l.strip()
            if t:
                out.append((pno + 1, t))
    doc.close()
    return out


NUM_ONLY = re.compile(r"^[（(]?\s*[一二三四五六七八九十\d]{1,3}\s*[）)、.．]$")


def merge_numbering(lines):
    """编号独占一行时与下一行合并。

    部分年报的标题被排版拆成两行：「一、」+「报告期内公司所从事的主要业务……」。
    编号行长度仅 2–4，单行解析必然被 `parse_heading` 拒绝，导致整章零产出
    （600128 弘业股份 2015、600151 航天机电 2015 均属此类）。
    """
    out, i = [], 0
    while i < len(lines):
        p, t = lines[i]
        if NUM_ONLY.match(t) and i + 1 < len(lines):
            out.append((p, t + lines[i + 1][1]))
            i += 2
            continue
        out.append((p, t))
        i += 1
    return out


def extract(path):
    lines = merge_numbering(read_lines(path))
    heads = []          # (index, level, title, key)
    for i, (pno, t) in enumerate(lines):
        h = parse_heading(t)
        if h:
            heads.append((i, h[0], h[1], match_key(h[1])))
    if not heads:
        return []

    out = {}
    for pos, (idx, lv, title, key) in enumerate(heads):
        if key is None:
            continue
        end = len(lines)
        for idx2, lv2, _t2, _k2 in heads[pos + 1:]:
            if lv2 <= lv:
                end = idx2
                break
        body = [t for _p, t in lines[idx + 1:end]]
        text = chr(10).join(body).strip()
        if len(CJK.findall(text)) < 30:
            continue
        p0 = lines[idx][0]
        p1 = lines[max(end - 1, idx)][0]
        rec = {"key": key, "label": LABEL[key], "title": title,
               "start": p0, "end": p1, "n_pages": max(1, p1 - p0 + 1),
               "chars": len(text), "text": text}
        old = out.get(key)
        if old is None or rec["chars"] > old["chars"]:
            out[key] = rec
    return list(out.values())


def init_table(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS biz_sections (
        id INTEGER PRIMARY KEY, code TEXT, year INTEGER, name TEXT,
        section_key TEXT, section_label TEXT, title_raw TEXT,
        start_page INTEGER, end_page INTEGER, n_pages INTEGER, chars INTEGER,
        text TEXT, text_clean TEXT, clean_chars INTEGER,
        is_na INTEGER DEFAULT 0, table_only INTEGER DEFAULT 0,
        UNIQUE(code, year, section_key)
    );
    CREATE INDEX IF NOT EXISTS idx_biz_key ON biz_sections(section_key);
    CREATE INDEX IF NOT EXISTS idx_biz_year ON biz_sections(year);
    """)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(SRC, "*", "*.pdf")))
    if args.pilot:
        random.seed(20251029)
        files = random.sample(files, min(args.pilot, len(files)))
    if args.limit:
        files = files[: args.limit]
    print(f"待识别 {len(files)} 份")

    tasks = []
    for p in files:
        m = re.match(r"(\d{4})-(\d{6})-(.+)\.pdf$", os.path.basename(p))
        if m:
            tasks.append((p, int(m.group(1)), m.group(2), m.group(3)))

    t0 = time.time()
    res = []

    def work(t):
        p, y, code, name = t
        try:
            return t, extract(p)
        except Exception as e:  # noqa: BLE001
            print("  [ERR]", os.path.basename(p), repr(e), flush=True)
            return t, []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, t) for t in tasks]
        for i, fu in enumerate(as_completed(futs), 1):
            res.append(fu.result())
            if i % 1000 == 0:
                print(f"  {i}/{len(tasks)}  {time.time() - t0:.0f}s", flush=True)

    from collections import Counter
    cnt = Counter()
    for _t, secs in res:
        for s in secs:
            cnt[s["key"]] += 1
    print("命中:", dict(cnt), f"/ {len(res)} 份")

    if args.apply:
        con = sqlite3.connect(DB)
        init_table(con)
        cur = con.cursor()
        n = 0
        for (p, y, code, name), secs in res:
            cur.execute("DELETE FROM biz_sections WHERE code=? AND year=?", (code, y))
            for s in secs:
                c = clean(s["text"])
                cur.execute("""INSERT INTO biz_sections(code,year,name,section_key,section_label,
                               title_raw,start_page,end_page,n_pages,chars,text,text_clean,
                               clean_chars,is_na,table_only) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (code, y, name, s["key"], s["label"], s["title"], s["start"],
                             s["end"], s["n_pages"], s["chars"], s["text"], c, len(c),
                             1 if _is_na(s["text"]) else 0,
                             1 if (s["text"] and not c) else 0))
                n += 1
            if n % 2000 == 0:
                con.commit()
        con.commit()
        print(f"入库 {n} 条 → biz_sections")
        for y, k, n2, avg in con.execute("""SELECT year, section_key, COUNT(*), ROUND(AVG(clean_chars))
                                            FROM biz_sections GROUP BY year, section_key
                                            ORDER BY year, section_key"""):
            print(f"  {y} {LABEL.get(k, k):<8} {n2:>5} 份  清洗后平均 {avg} 字")
        con.close()
    print(f"用时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
