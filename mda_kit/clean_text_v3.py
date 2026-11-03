# -*- coding: utf-8 -*-
"""
MD&A 章节正文清洗（第三版）

相对临时目录旧版（clean_text.py）的改动
  1. 数字密度规则不再误伤叙述句：仅当行末没有句末标点时才按“数字占比>25%”删除。
     旧版把「营业收入 87.35 亿元，同比下降 1.77%。」这类完整叙述句整行删掉，
     抽样审计显示句末数字句被删率 100%（约 1.75M 字）。
  2. 清洗前先做保守重排：上一行是≥25 字且未结束的长句、本行是≤14 字的短尾，
     合并为一行，救回被“连续短行”规则误删的句尾碎片。
  3. 页眉判据改为「含公司后缀 + 年份 + 年度报告」且整行≤60 字，无条件删除；
     另保留“短行重复≥3 次”的页眉/页脚频次判据。
  4. 增补表格残留清理：纯编号行、表头行（项目/合计/小计/其中/本期/上期…）、
     以（元）（%）（万元）等结尾的列名行、单位/币种行。
  5. 连续短行规则增加“行内无句末标点”前提，避免误删短叙述行；
     必须在重排之后执行。
  6. 输出附加字段：
     - clean_chars  清洗后字数
     - is_na        原文只有“√适用/□不适用”一类勾选（章节不适用）
     - table_only   清洗后为空、但原文非空（整节基本是表格/数字）
     便于下游按 clean_chars / table_only 筛选，而不是面对空串无法区分。
"""
import re
import sqlite3
import sys

from .config import settings

DB = str(settings.db_path)

# ---- 行级判据 ----
HEADER = re.compile(r"(股份有限公司|有限责任公司|有限公司|集团).{0,30}20\d{2}\s*年年度报告")
YEAR_REPORT = re.compile(r"20\d{2}\s*年\s*年度报告")
PAGENO = re.compile(r"^[-—\s]*\d{1,4}[-—\s]*$")
PAGEWORD = re.compile(r"^第\s*\d+\s*页(\s*共\s*\d+\s*页)?$")
CHECK = re.compile(r"^[\ue000-\uf8ff√✓☑☒■□●○\s]*(?:适用|不适用)?[\s\ue000-\uf8ff√✓☑☒■□●○]*(?:适用|不适用)?\s*$")
CAPTION = re.compile(r"^单位[：:]\s*(元|人民币|万元|亿元|股|万股)\s*(币种.*)?$|^币种[：:]\s*(人民币|元)\s*$")
CHECKBOX_ONLY = re.compile(r"^[\s\ue000-\uf8ff√✓☑☒■□●○（）()、,，。；;:：/／\-—]*"
                           r"(?:(?:适用|不适用)[\s\ue000-\uf8ff√✓☑☒■□●○（）()、,，。；;:：/／\-—]*)+$")
SYMBOL_ONLY = re.compile(r"^[/／\-—%％·、,，.。\s|｜]+$")
PURE_NUM = re.compile(r"^[（(]?\s*\d{1,3}\s*[）)、.．]?\s*$")
TABLE_HEAD_TAIL = re.compile(r"[（(](?:元|万元|亿元|%|％|万股|股|人民币)[）)]$")
TABLE_HEAD_START = re.compile(r"^(?:项目|合计|小计|其中|本期|上期|期末|期初|年初|年末|本年|上年|"
                              r"增减|金额|数量|比例|名称|类别)[\s：:]")
DIGIT = re.compile(r"[0-9]")
CJK = re.compile(r"[\u4e00-\u9fa5]")
ENDS = re.compile(r"[。！？；：]\s*$")
HEADING_MARK = re.compile(r"^(?:[一二三四五六七八九十]{1,3}[、.．]|[（(][一二三四五六七八九十\d]{1,3}[）)]|"
                          r"\d{1,2}(?:[.．]\d{1,2})*[、.．]?\s)")
MULTI_COL = re.compile(r"\s{2,}|\|")


def _is_junk_line(s):
    if not s:
        return True
    if PAGENO.match(s) or PAGEWORD.match(s) or CHECK.match(s) or CHECKBOX_ONLY.match(s):
        return True
    if CAPTION.match(s) or SYMBOL_ONLY.match(s):
        return True
    if HEADER.search(s) and len(s) <= 60:
        return True
    if YEAR_REPORT.search(s) and len(s) <= 60:
        return True
    if PURE_NUM.match(s):
        return True
    if ENDS.search(s):
        return False              # 句末行不按数字密度误删
    if len(s) <= 60 and DIGIT.findall(s) and len(DIGIT.findall(s)) / len(s) > 0.25:
        return True
    if TABLE_HEAD_TAIL.search(s) or TABLE_HEAD_START.match(s):
        return True
    cells = [c for c in MULTI_COL.split(s) if c.strip()]
    if len(cells) >= 3:
        avg = sum(len(c) for c in cells) / len(cells)
        if avg < 12:
            return True
    return False


def _is_na(text):
    """整节只有勾选（√适用 / □不适用）。"""
    if not text or ("适用" not in text):
        return False
    t = re.sub(r"不适用|适用", "", text)
    t = re.sub(r"[\s\ue000-\uf8ff√✓☑☒■□●○（）()、,，。；;:：/／\-—]", "", t)
    return len(t) == 0


def clean(text):
    """去页眉页脚/页码/表格碎片，只保留叙述文字。"""
    if not text:
        return ""
    lines = [l.strip() for l in text.split("\n")]
    lines = [l for l in lines if l]

    # 1) 段落内高频重复行（页眉/页脚）
    cnt = {}
    for l in lines:
        if 4 <= len(l) <= 50:
            cnt[l] = cnt.get(l, 0) + 1
    lines = [l for l in lines if not (cnt.get(l, 0) >= 3 and len(l) <= 50)]

    # 2) 行级垃圾剔除
    kept = [l for l in lines if not _is_junk_line(l)]

    # 3) 保守重排：长句被换行拆出的短尾并回上一行（救回句尾碎片）。
    #    只吸收"以句末标点收尾"的短尾，避免把表格单元格/小标题并进上一行。
    merged = []
    for l in kept:
        if (merged and len(merged[-1]) >= 25 and not ENDS.search(merged[-1])
                and len(l) <= 14 and ENDS.search(l)
                and not HEADING_MARK.match(l) and not _is_junk_line(l)):
            merged[-1] = merged[-1] + l
        else:
            merged.append(l)

    # 4) 连续短行块：≥3 行、每行 ≤14 字，且都无句末标点 → 表格块
    keep, i = [], 0
    while i < len(merged):
        if len(merged[i]) <= 14 and not ENDS.search(merged[i]):
            j = i
            while j < len(merged) and len(merged[j]) <= 14 and not ENDS.search(merged[j]):
                j += 1
            if j - i >= 3:
                i = j
                continue
        keep.append(merged[i])
        i += 1

    # 5) 句号判据：块内整体无句末标点且平均行长 <20 字 → 表格/表头块
    blocks, cur = [], []
    for l in keep:
        cur.append(l)
        if ENDS.search(l):
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    out = []
    for b in blocks:
        if len(b) >= 2 and not any(ENDS.search(x) for x in b[:-1]):
            avg = sum(len(x) for x in b) / len(b)
            if avg < 20:
                continue
        out.extend(b)

    txt = "\n".join(out)
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    txt = re.sub(r"\n{2,}", "\n", txt)
    return txt.strip()


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else DB
    con = sqlite3.connect(db)
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(sections)")]
    for col, typ in (("text_clean", "TEXT"), ("clean_chars", "INTEGER"),
                     ("is_na", "INTEGER DEFAULT 0"), ("table_only", "INTEGER DEFAULT 0")):
        if col not in cols:
            cur.execute(f"ALTER TABLE sections ADD COLUMN {col} {typ}")
    con.commit()

    maxid = cur.execute("SELECT COALESCE(MAX(id),0) FROM sections").fetchone()[0]
    n_all = cur.execute("SELECT COUNT(*) FROM sections WHERE chars>0").fetchone()[0]
    before = after = 0
    n_na = n_table = 0
    CHUNK = 50000
    done = 0
    for lo in range(0, maxid + 1, CHUNK):
        # 分块读取：全库 39 万节一次性 fetchall 会占 2–3 GB 内存
        rows = cur.execute("SELECT id, text, chars FROM sections "
                           "WHERE chars>0 AND id BETWEEN ? AND ?",
                           (lo, lo + CHUNK - 1)).fetchall()
        for sid, text, chars in rows:
            c = clean(text)
            na = 1 if _is_na(text) else 0
            tb = 1 if (text and not c and not na) else 0
            before += len(text or "")
            after += len(c)
            n_na += na
            n_table += tb
            cur.execute("""UPDATE sections SET text_clean=?, clean_chars=?, is_na=?, table_only=?
                           WHERE id=?""", (c, len(c), na, tb, sid))
        con.commit()
        done += len(rows)
        if done and done % 100000 < CHUNK:
            print(f"  已清洗 {done}/{n_all}", flush=True)
    print(f"清洗 {done} 条：{before/1e6:.1f} 百万字 → {after/1e6:.1f} 百万字，"
          f"保留 {after/max(before,1)*100:.1f}%")
    print(f"不适用章节 is_na={n_na}；纯表格章节 table_only={n_table}")
    # 分章节清洗强度
    print("\n分章节：")
    for lab, n, c0, c1, na in cur.execute("""
            SELECT section_label, COUNT(*), ROUND(AVG(chars)), ROUND(AVG(clean_chars)),
                   SUM(is_na) FROM sections WHERE chars>0 GROUP BY section_key
            ORDER BY COUNT(*) DESC"""):
        print(f"  {lab:<8} {n:>5} 份  原 {c0:>6.0f} 字 → 清洗后 {c1:>6.0f} 字  "
              f"保留 {c1/max(c0,1)*100:>4.0f}%  不适用 {na}")
    # 清洗强度分布
    print("\n净/原 分布：")
    for r in cur.execute("""
            SELECT CASE WHEN clean_chars*1.0/chars>=0.9 THEN '>=90%'
                        WHEN clean_chars*1.0/chars>=0.6 THEN '60-90%'
                        WHEN clean_chars*1.0/chars>=0.3 THEN '30-60%'
                        ELSE '<30%' END AS b, COUNT(*)
            FROM sections WHERE chars>0 GROUP BY b ORDER BY COUNT(*) DESC"""):
        print("  ", r)
    con.close()


if __name__ == "__main__":
    main()
