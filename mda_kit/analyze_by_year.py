# -*- coding: utf-8 -*-
"""按年份输出章节识别指标，用于跨年试点与全量验收。
用法: python -m mda_kit.analyze [db路径] [--csv 输出.csv]
"""
import argparse
import sqlite3
import sys

ORDER = ["industry", "business", "core_comp", "main_ops", "non_main", "assets",
         "invest", "subsidiary", "structured", "outlook", "research"]
LABEL = {"industry": "行业情况", "business": "主要业务", "core_comp": "核心竞争力",
         "main_ops": "主营业务分析", "non_main": "非主营业务", "assets": "资产及负债",
         "invest": "投资状况", "subsidiary": "子公司参股", "structured": "结构化主体",
         "outlook": "未来发展", "research": "接待调研"}


def main():
    ap = argparse.ArgumentParser()
    from .config import settings
    ap.add_argument("db", nargs="?", default=str(settings.db_path))
    ap.add_argument("--csv", default="")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    c = con.cursor()
    years = [r[0] for r in c.execute("SELECT DISTINCT year FROM documents ORDER BY year")]
    total = {r[0]: r[1] for r in c.execute("SELECT year, COUNT(*) FROM documents GROUP BY year")}

    print(f"库：{args.db}")
    print(f"年份：{years}  文档 {sum(total.values())} 份\n")

    print("== 各年章节覆盖率 ==")
    print("年份  份数  " + " ".join(f"{LABEL[k][:4]:>6}" for k in ORDER) + "   平均章数")
    for y in years:
        row = {k: 0 for k in ORDER}
        for k, nz in c.execute("SELECT section_key, SUM(chars>0) FROM sections "
                               "WHERE year=? GROUP BY section_key", (y,)):
            row[k] = nz or 0
        n = total[y] or 1
        avg = c.execute("SELECT AVG(n_sections) FROM documents WHERE year=?", (y,)).fetchone()[0] or 0
        print(f"{y}  {n:>5}  " + " ".join(f"{row[k] / n * 100:5.1f}%" for k in ORDER)
              + f"   {avg:4.1f}")
    print("\n（权重按各年文件份数归一）")

    print("\n== 各年质量标记 ==")
    flags = ["ok", "truncated", "wrong_chapter", "wrong_start", "empty"]
    print("年份  " + " ".join(f"{f[:10]:>12}" for f in flags))
    for y in years:
        d = dict(c.execute("SELECT quality_flag, COUNT(*) FROM documents WHERE year=? "
                           "GROUP BY quality_flag", (y,)).fetchall())
        print(f"{y}  " + " ".join(f"{d.get(f, 0):>12}" for f in flags))

    print("\n== 各年识别模式 ==")
    modes = [r[0] for r in c.execute("SELECT DISTINCT detect_mode FROM documents ORDER BY 1")]
    print("年份  " + " ".join(f"{m[:10]:>12}" for m in modes))
    for y in years:
        d = dict(c.execute("SELECT detect_mode, COUNT(*) FROM documents WHERE year=? "
                           "GROUP BY detect_mode", (y,)).fetchall())
        print(f"{y}  " + " ".join(f"{d.get(m, 0):>12}" for m in modes))

    print("\n== 完备度（各年 11 章齐全占比）==")
    for y in years:
        n = total[y]
        full = c.execute("SELECT COUNT(*) FROM documents WHERE year=? AND n_sections=11", (y,)).fetchone()[0]
        low = c.execute("SELECT COUNT(*) FROM documents WHERE year=? AND n_sections<=3", (y,)).fetchone()[0]
        print(f"  {y}: 11章 {full:>5} ({full / n * 100:4.1f}%)   0-3章 {low:>5} ({low / n * 100:4.1f}%)")

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig") as f:
            f.write("year,docs," + ",".join(ORDER) + ",avg_sections,ok,truncated,wrong_chapter,"
                    "wrong_start,empty,full11,low3\n")
            for y in years:
                n = total[y] or 1
                row = {k: 0 for k in ORDER}
                for k, nz in c.execute("SELECT section_key, SUM(chars>0) FROM sections "
                                       "WHERE year=? GROUP BY section_key", (y,)):
                    row[k] = nz or 0
                d = dict(c.execute("SELECT quality_flag, COUNT(*) FROM documents WHERE year=? "
                                   "GROUP BY quality_flag", (y,)).fetchall())
                avg = c.execute("SELECT AVG(n_sections) FROM documents WHERE year=?", (y,)).fetchone()[0] or 0
                full = c.execute("SELECT COUNT(*) FROM documents WHERE year=? AND n_sections=11", (y,)).fetchone()[0]
                low = c.execute("SELECT COUNT(*) FROM documents WHERE year=? AND n_sections<=3", (y,)).fetchone()[0]
                vals = [str(y), str(total[y]), *[str(row[k]) for k in ORDER], f"{avg:.2f}",
                        str(d.get("ok", 0)), str(d.get("truncated", 0)), str(d.get("wrong_chapter", 0)),
                        str(d.get("wrong_start", 0)), str(d.get("empty", 0)), str(full), str(low)]
                f.write(",".join(vals) + "\n")
        print("\nCSV:", args.csv)
    con.close()


if __name__ == "__main__":
    sys.exit(main())
