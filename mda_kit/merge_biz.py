# -*- coding: utf-8 -*-
"""把「公司业务概要」的识别结果并入统一视图，并输出合并前后的覆盖率对比。

视图 `sections_merged`：以 `sections`（MD&A 11 槽位）为主，2015–2020 年的
business / core_comp 若在 `biz_sections` 中有非空结果，则以 biz_sections 覆盖。
覆盖后仍保留 `src` 列标注来源（md_a / biz_overview），便于溯源与分源检验。

用法
  python merge_biz.py            # 建视图并打印对比
  python merge_biz.py --drop     # 删除视图
"""
import argparse
import sqlite3
import sys

from .config import settings

DB = str(settings.db_path)

VIEW = """
CREATE VIEW sections_merged AS
SELECT s.code, s.year, s.name, s.section_key, s.section_label, s.title_raw,
       s.start_page, s.end_page, s.n_pages, s.chars, s.text, s.text_clean,
       s.clean_chars, s.is_na, s.table_only, s.boundary, 'md_a' AS src
FROM sections s
WHERE NOT (
    s.year BETWEEN 2015 AND 2020
    AND s.section_key IN ('business', 'core_comp')
    AND EXISTS (SELECT 1 FROM biz_sections b
                WHERE b.code = s.code AND b.year = s.year
                  AND b.section_key = s.section_key AND b.clean_chars > 0)
)
UNION ALL
SELECT b.code, b.year, b.name, b.section_key, b.section_label, b.title_raw,
       b.start_page, b.end_page, b.n_pages, b.chars, b.text, b.text_clean,
       b.clean_chars, b.is_na, b.table_only, '' AS boundary, 'biz_overview' AS src
FROM biz_sections b
WHERE b.clean_chars > 0 AND b.section_key IN ('business', 'core_comp')
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--drop", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    if args.drop:
        cur.execute("DROP VIEW IF EXISTS sections_merged")
        con.commit()
        print("已删除视图 sections_merged")
        return 0

    cur.execute("DROP VIEW IF EXISTS sections_merged")
    cur.execute(VIEW)
    con.commit()

    print("视图 sections_merged 已建立\n")
    print("== 主要业务 / 核心竞争力：合并前后覆盖率 ==")
    print("年份   份数   主要业务(MD&A→合并)   核心竞争力(MD&A→合并)   来源构成")
    # 先取出年份清单：复用同一 cursor 迭代时再执行其他查询会中断迭代
    years = cur.execute("SELECT year, COUNT(*) FROM documents GROUP BY year ORDER BY year").fetchall()
    for y, n in years:
        def cov(key, src_table):
            q = (f"SELECT COUNT(*) FROM {src_table} WHERE year=? AND section_key=? "
                 f"AND clean_chars>0")
            return cur.execute(q, (y, key)).fetchone()[0]
        b0, c0 = cov("business", "sections"), cov("core_comp", "sections")
        b1 = cur.execute("SELECT COUNT(*) FROM sections_merged WHERE year=? AND section_key='business' "
                         "AND clean_chars>0", (y,)).fetchone()[0]
        c1 = cur.execute("SELECT COUNT(*) FROM sections_merged WHERE year=? AND section_key='core_comp' "
                         "AND clean_chars>0", (y,)).fetchone()[0]
        src = cur.execute("SELECT src, COUNT(*) FROM sections_merged WHERE year=? AND section_key IN "
                          "('business','core_comp') AND clean_chars>0 GROUP BY src", (y,)).fetchall()
        src_s = " ".join(f"{k}:{v}" for k, v in src)
        print(f"{y}  {n:>5}   {b0 / n * 100:5.1f}% → {b1 / n * 100:5.1f}%      "
              f"{c0 / n * 100:5.1f}% → {c1 / n * 100:5.1f}%      {src_s}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
