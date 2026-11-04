# -*- coding: utf-8 -*-
"""按年份导出章节库（宽表 / Stata 长表 / 索引 CSV）。

与 export_sections_v3.py 的差异
  1. 支持任意年份，可一次导出 2015–2025；
  2. 数据源默认取 `sections_merged`（2015–2020 年的主要业务/核心竞争力优先用
     「公司业务概要」来源），并保留 `src` 列便于溯源；
  3. 每年单独落盘，避免单文件过大。

用法
  python -m mda_kit.export --years 2015
  python -m mda_kit.export --years all
  python -m mda_kit.export --years all --source sections           # 只用 MD&A 原始表
  python -m mda_kit.export --years all --source sections_merged    # 使用合并视图
"""
import argparse
import os
import sqlite3
import time

import pandas as pd

from .config import settings

DB = str(settings.db_path)
OUT = str(settings.export_dir)
KEYS = [("industry", "行业情况"), ("business", "主要业务"), ("core_comp", "核心竞争力"),
        ("main_ops", "主营业务分析"), ("non_main", "非主营业务"), ("assets", "资产及负债"),
        ("invest", "投资状况"), ("subsidiary", "子公司参股"), ("structured", "结构化主体"),
        ("outlook", "未来发展"), ("research", "接待调研")]


def export_year(conn, year, source):
    t0 = time.time()
    # 1) Stata 长表
    idx = pd.read_sql_query("""
        SELECT d.code, d.year, d.name, d.pages AS mda_pages,
               d.detect_mode, d.quality_flag,
               s.section_key, s.section_label, s.title_raw,
               s.start_page, s.end_page, s.n_pages,
               s.chars, COALESCE(s.clean_chars, 0) AS clean_chars,
               COALESCE(s.is_na, 0) AS is_na, COALESCE(s.table_only, 0) AS table_only
        FROM documents d
        JOIN (SELECT * FROM {src} WHERE year = ?) s
          ON s.code = d.code AND s.year = d.year
        WHERE d.year = ?
        ORDER BY d.code, s.section_key
    """.format(src=source), conn, params=(year, year))
    idx.to_stata(os.path.join(OUT, f"sections_index_{year}.dta"),
                 write_index=False, version=118)

    # 2) 宽表
    cols = ",\n           ".join(
        f"MAX(CASE WHEN s.section_key='{k}' THEN s.text_clean END) AS {lab}" for k, lab in KEYS)
    wide = pd.read_sql_query("""
        SELECT d.code, d.year, d.name, d.pages AS mda_pages, d.quality_flag,
           {cols}
        FROM documents d
        JOIN (SELECT * FROM {src} WHERE year = ?) s
          ON s.code = d.code AND s.year = d.year
        WHERE d.year = ?
        GROUP BY d.code, d.year, d.name, d.pages, d.quality_flag
        ORDER BY d.code
    """.format(cols=cols, src=source), conn, params=(year, year))
    wide.to_csv(os.path.join(OUT, f"sections_wide_{year}.csv"),
                index=False, encoding="utf-8-sig")

    # 3) 章节索引（仅非空行）
    qa = pd.read_sql_query("""
        SELECT s.code AS 代码, s.year AS 年度, s.name AS 简称,
               s.section_label AS 章节, s.title_raw AS 原始标题,
               s.start_page AS 起始页, s.end_page AS 结束页, s.n_pages AS 页数,
               s.chars AS 原始字数, COALESCE(s.clean_chars,0) AS 清洗后字数,
               COALESCE(s.is_na,0) AS 不适用, COALESCE(s.table_only,0) AS 纯表格,
               d.detect_mode AS 识别模式
        FROM documents d
        JOIN (SELECT * FROM {src} WHERE year = ? AND chars > 0) s
          ON s.code = d.code AND s.year = d.year
        WHERE d.year = ?
        ORDER BY s.code, s.section_key
    """.format(src=source), conn, params=(year, year))
    qa.to_csv(os.path.join(OUT, f"_sections_{year}.csv"),
              index=False, encoding="utf-8-sig")

    labs = [lab for _k, lab in KEYS]
    core = wide[["主营业务分析", "核心竞争力", "未来发展"]]
    has = core.apply(lambda r: sum(isinstance(v, str) and bool(v.strip()) for v in r), axis=1)
    total = wide[labs].apply(lambda c: c.fillna("").str.len()).sum().sum()
    print(f"{year}: 文档 {len(wide)}  长表 {len(idx)} 行  索引 {len(qa)} 行  "
          f"三章齐全 {(has == 3).mean() * 100:.1f}%  平均净字数 "
          f"{int(total / max(len(wide), 1))}  用时 {time.time() - t0:.0f}s", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="all")
    ap.add_argument("--source", default="sections",
                    choices=["sections_merged", "sections"])
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    os.makedirs(OUT, exist_ok=True)
    if args.source == "sections_merged":
        try:
            conn.execute("SELECT 1 FROM sections_merged LIMIT 1")
        except sqlite3.OperationalError:
            print("提示: 未找到 sections_merged 视图，回退到 sections（先运行 merge 可启用合并视图）")
            args.source = "sections"
    if args.years == "all":
        years = [r[0] for r in conn.execute("SELECT DISTINCT year FROM documents ORDER BY year")]
    else:
        years = [int(y) for y in args.years.split(",")]
    print(f"数据源 {args.source}，年份 {years}")
    t0 = time.time()
    for y in years:
        export_year(conn, y, args.source)
    conn.close()
    print(f"完成，总用时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
