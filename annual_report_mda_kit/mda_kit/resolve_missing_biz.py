# -*- coding: utf-8 -*-
"""为「公司业务概要」补抓中缺少公告 URL 的记录还原旧代码并查询公告。

背景：2015–2020 年有 648 份文件在 `MDA/_manifest.csv` 中没有 URL。其中北交所
920xxx 公司在转板前挂牌新三板，巨潮的 orgId 内嵌了旧代码（`gfbj0430478` → 430478）。
本脚本据此还原旧代码，再按旧代码查询年度报告公告，输出可用的 URL 清单。

用法
  python -m mda_kit.resolve --limit 10     # 试探 10 家
  python -m mda_kit.resolve --apply        # 全量并写入 biz_dir/_biz_urls_extra.csv
"""
import argparse
import csv
import os
import re
import sqlite3
import sys
import time

from . import fetch_mda as FM
from . import fix_upstream as F

from .config import settings

BASE = str(settings.data_dir)
DB = str(settings.db_path)
OUT = os.path.join(str(settings.biz_dir), "_biz_urls_extra.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--years", default="2015,2016,2017,2018,2019,2020")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    years = [int(y) for y in args.years.split(",")]
    man = F.load_manifest()
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT code, name, year FROM documents WHERE year BETWEEN ? AND ?",
                       (min(years), max(years))).fetchall()
    missing = [(c, n, y) for c, n, y in rows if (c, str(y)) not in man]
    print(f"2015–2020 共 {len(rows)} 份，缺 URL {len(missing)} 份")
    if args.limit:
        missing = missing[: args.limit]

    om = FM.org_map()
    print(f"orgId 映射 {len(om)} 条")

    found, no_org, no_ann = [], 0, 0
    t0 = time.time()
    cache = {}
    for i, (code, name, year) in enumerate(missing, 1):
        org = om.get(code)
        if not org:
            no_org += 1
            continue
        m = re.match(r"gfbj0(\d{6})", org)
        old_code = m.group(1) if m else code
        if old_code not in cache:
            try:
                cache[old_code] = FM.query_annual_reports(old_code)
            except Exception as e:  # noqa: BLE001
                print("  [ERR]", code, repr(e)[:60])
                cache[old_code] = []
            time.sleep(0.4)
        anns = cache[old_code]
        picked = FM.pick_reports(anns, [year]) if anns else {}
        if year in picked:
            found.append(dict(code=code, name=name, year=year, org=org,
                              old_code=old_code, url=picked[year]["url"],
                              title=picked[year].get("title", "")))
        else:
            no_ann += 1
        if i % 20 == 0:
            print(f"  {i}/{len(missing)}  命中 {len(found)}  {time.time() - t0:.0f}s", flush=True)

    print(f"结果：命中 {len(found)}，无 orgId {no_org}，有 orgId 但无该年公告 {no_ann}")
    if found:
        by_year = {}
        for r in found:
            by_year[r["year"]] = by_year.get(r["year"], 0) + 1
        print("分年命中:", dict(sorted(by_year.items())))
        for r in found[:5]:
            print("  样例:", r["code"], r["year"], r["old_code"], r["url"][-40:])

    if args.apply and found:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(found[0].keys()))
            w.writeheader()
            w.writerows(found)
        print("写入:", OUT)
    con.close()


if __name__ == "__main__":
    sys.exit(main())
