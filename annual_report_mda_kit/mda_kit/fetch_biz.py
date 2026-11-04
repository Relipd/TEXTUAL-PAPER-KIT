# -*- coding: utf-8 -*-
"""补抓 2015–2020 年「公司业务概要」章。

背景：2021 年披露准则修订前，「主要业务」与「核心竞争力」属独立的
「第三节 公司业务概要」，不在「管理层讨论与分析」内。上游只截取了 MD&A，
故这两年段的两章覆盖率仅 4–15%。

本脚本按 `MDA/_manifest.csv` 的公告 URL 重新下载原始年报，定位「公司业务概要」
章并截取，落到 `MDA_BIZ/{年份}/`，不改动既有 MDA 目录。

用法
  python fetch_biz.py --pilot 30            # 每年抽 5 份探测
  python fetch_biz.py --years 2015,2016 --apply --workers 4
  python fetch_biz.py --all --apply --workers 4
输出
  MDA_BIZ/_biz_manifest.csv
"""
import argparse
import csv
import gc
import os
import random
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pymupdf

from . import fix_upstream as F

from .config import settings

BASE = str(settings.data_dir)
OUT_DIR = str(settings.biz_dir)
DB = str(settings.db_path)
LOG = os.path.join(OUT_DIR, "_biz_manifest.csv")

BIZ_TITLE = ("公司业务概要", "业务概要", "公司业务概览", "业务概览")
VALID_KEYS = ("主要业务", "主营业务", "经营模式", "核心竞争力", "主要产品",
              "业绩驱动", "所处行业")


def locate_biz(doc, n):
    """返回 (start, end, method)，0-based，含端点。"""
    start = None
    method = ""
    # ① 书签
    for lv, ti, pg in doc.get_toc():
        if lv <= 3 and any(k in ti for k in BIZ_TITLE):
            for off in range(3):
                p = pg - 1 + off
                if 0 <= p < n and any(k in (doc[p].get_text() or "")[:400] for k in BIZ_TITLE):
                    start, method = p, "书签"
                    break
        if start is not None:
            break
    # ② 第X节 标题
    if start is None:
        for i in range(n):
            t = doc[i].get_text()
            if F.is_toc(t):
                continue
            for num, title, pos in F.page_headings(t):
                if any(k in title for k in BIZ_TITLE):
                    start, method = i, f"节标题(序号{num})"
                    break
            if start is not None:
                break
    # ③ 页首独立短行
    if start is None:
        for i in range(n):
            for s in F.top_lines(doc[i], 4):
                if any(k in s for k in BIZ_TITLE) and len(s) <= 26:
                    start, method = i, "页首标题"
                    break
            if start is not None:
                break
    if start is None:
        return None

    end = n - 1
    for i in range(start + 1, n):
        t = doc[i].get_text()
        span = max(len(t), 1)
        cut = None
        for num, title, pos in F.page_headings(t):
            if any(k in title for k in BIZ_TITLE):
                continue
            if pos / span > 0.55:
                continue
            cut = i if F.prefix_body_chars(t, pos) >= 16 else i - 1
            break
        if cut is not None:
            end = cut
            break
    end = max(end, start)
    return start, end, method


def validate(doc, s, e):
    txt = "\n".join(doc[i].get_text() for i in range(s, min(e + 1, doc.page_count)))
    hits = sum(1 for k in VALID_KEYS if k in txt)
    return hits, len(txt)


def one(row, meta, apply):
    code, name, year = row
    url = meta[0]
    rec = dict(code=code, name=name, year=year, url=url, start="", end="", pages="",
               method="", hits="", chars="", verdict="")
    try:
        data = F.fetch(url)
    except Exception as e:  # noqa: BLE001
        rec["verdict"] = f"download_fail:{type(e).__name__}"
        return rec
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        rec["verdict"] = f"open_fail:{e}"
        return rec
    n = doc.page_count
    loc = locate_biz(doc, n)
    if not loc:
        rec["verdict"] = "no_biz_chapter"
        doc.close()
        return rec
    s, e, method = loc
    hits, chars = validate(doc, s, e)
    rec.update(start=s + 1, end=e + 1, pages=e - s + 1, method=method,
               hits=hits, chars=chars)
    if hits < 2 or (e - s + 1) > 20:
        rec["verdict"] = "reject"
        doc.close()
        return rec
    rec["verdict"] = "ok"
    dst = os.path.join(OUT_DIR, str(year), f"{year}-{code}-{name}.pdf")
    if apply:
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".part"
            out = pymupdf.open()
            out.insert_pdf(doc, from_page=s, to_page=e)
            # 直接落盘：tobytes 会在内存中再复制一份完整 PDF，5 并发下曾触发 MemoryError
            out.save(tmp, garbage=3, deflate=True)
            out.close()
            os.replace(tmp, dst)
            rec["verdict"] = "saved"
        except MemoryError:
            rec["verdict"] = "write_fail:MemoryError"
        except Exception as ex:  # noqa: BLE001
            rec["verdict"] = f"write_fail:{ex}"
    doc.close()
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2015,2016,2017,2018,2019,2020")
    ap.add_argument("--pilot", type=int, default=0, help="每年抽取 N 份，只探测")
    ap.add_argument("--limit", type=int, default=0, help="每年最多处理 N 份")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true", help="已存在的切片也重新抓取")
    ap.add_argument("--codes", default="", help="只处理这些代码（逗号分隔）")
    ap.add_argument("--all", action="store_true", help="处理全部年份清单")
    args = ap.parse_args()

    years = [int(y) for y in args.years.split(",") if y.strip()]
    con = sqlite3.connect(DB)
    man = F.load_manifest()
    # 并入旧代码还原得到的补充 URL（2015–2020 年 608 份，清单里原本没有记录）
    extra = os.path.join(OUT_DIR, "_biz_urls_extra.csv")
    if os.path.exists(extra):
        with open(extra, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                man.setdefault((r["code"], str(r["year"])), (r["url"], 0, 0))
        print("并入补充 URL:", extra)

    rows = []
    for y in years:
        sub = con.execute("SELECT code, name FROM documents WHERE year=?", (y,)).fetchall()
        sub = sorted(sub)
        if args.pilot:
            random.seed(20251029 + y)
            sub = random.sample(sub, min(args.pilot, len(sub)))
        elif args.limit:
            sub = sub[: args.limit]
        for code, name in sub:
            rows.append((code, name, y))

    todo, miss, skip = [], 0, 0
    for code, name, y in rows:
        meta = man.get((code, str(y)))
        if not meta:
            miss += 1
            continue
        if args.apply and not args.force:
            dst = os.path.join(OUT_DIR, str(y), f"{y}-{code}-{name}.pdf")
            if os.path.exists(dst) and os.path.getsize(dst) > 10000:
                skip += 1
                continue
        todo.append(((code, name, y), meta))
    print(f"待处理 {len(todo)} 份（已存在跳过 {skip}，无清单 URL {miss}）"
          f"workers={args.workers} apply={args.apply}")

    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    res = []
    BATCH = 200
    for lo in range(0, len(todo), BATCH):
        batch = todo[lo:lo + BATCH]
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(one, r, m, args.apply): r for r, m in batch}
            for fu in as_completed(futs):
                res.append(fu.result())
        gc.collect()
        print(f"  {min(lo + BATCH, len(todo))}/{len(todo)}  {time.time() - t0:.0f}s", flush=True)

    # 与既有清单合并，避免断点续跑覆盖前一轮记录
    old = []
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8-sig") as f:
            old = list(csv.DictReader(f))
    merged = {(r["code"], str(r["year"])): r for r in old}
    for r in res:
        merged[(r["code"], str(r["year"]))] = r
    with open(LOG, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(next(iter(merged.values())).keys()))
        w.writeheader()
        for r in sorted(merged.values(), key=lambda x: (str(x["year"]), x["code"])):
            w.writerow(r)

    from collections import Counter
    print("结果:", dict(Counter(r["verdict"] for r in res)))
    for y in years:
        sub = [r for r in res if r["year"] == y]
        if sub:
            print(f"  {y}({len(sub)}):", dict(Counter(r["verdict"] for r in sub)))
    ok = [r for r in res if r["verdict"] == "saved"]
    if ok:
        print(f"  平均 {sum(int(r['pages']) for r in ok) / len(ok):.1f} 页，"
              f"{sum(int(r['chars']) for r in ok) / len(ok):.0f} 字/份")
    print("写入:", LOG, f"用时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
