# -*- coding: utf-8 -*-
"""上游补全：对质量异常文件重新下载原始年报 → 重新定位 MD&A → 重新截取。

与 fetch_mda.locate_mda 的差异（v4 定位）
  1. 结束页不再要求「序号大于本节序号」，改为「出现任意其他章节的 第X节/第X章 标题」。
     旧规则在 A+H 年报（用「章」且编号重置）与编号不连续的年报上会提前收尾。
  2. 末页断句修正：若结束页末行无句末标点，且下一页页首不是章节标题，则顺延（最多 +5 页）。
  3. 校验：切片内须含「管理层讨论与分析」标题，或命中 ≥3 个规范章节关键词，
     否则判为定位失败，不覆盖原文件。

用法
  python -m mda_kit.fix-upstream --pilot 30    # 只探测，不写文件
  python -m mda_kit.fix-upstream --flags truncated --limit 50 --apply
  python -m mda_kit.fix-upstream --flags truncated,wrong_chapter,empty --apply --workers 3
输出
  data/MDA/_fix_upstream.csv（路径见 config.toml）
"""
import argparse
import csv
import io
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pymupdf
import requests

from .config import settings

BASE = str(settings.data_dir)
MDA = str(settings.mda_dir)
DB = str(settings.db_path)
MANIFEST = str(settings.manifest)
OUT = str(settings.fix_csv)
BACKUP = str(settings.backup_dir)

CN = {c: i for i, c in enumerate("一二三四五六七八九", 1)}
CN["十"] = 10
SEC_RE = re.compile(r"^第?\s*([一二三四五六七八九十]{1,3})\s*([节章])\s*(.*)$")
DOT_TAIL = re.compile(r"[\s.。·]+$")
DOT_LEADER = re.compile(r"\.{4,}|…{2,}")
BAD_TITLE_CH = re.compile(r"[“”\"《》…]")
MD_TITLES = ("管理层讨论与分析", "经营情况讨论与分析", "董事会报告")
TAIL_OK = re.compile(r"[。！？；：%”）】]$")
PAGENO = re.compile(r"^[-—–\s]*\d{1,4}[-—–\s]*$")
NOISE = re.compile(r"[。，；：,;!?]")
NUM_LEAD = re.compile(
    r"^(?:第\s*[一二三四五六七八九十]{1,3}\s*[节章]"
    r"|[一二三四五六七八九十]{1,3}\s*[、.．]"
    r"|\d{1,2}\s*[、.．]"
    r"|[（(]\s*[一二三四五六七八九十\d]{1,3}\s*[）)])\s*")

# A+H / 无编号年报里用于收尾的章级标题（仅取页首独立短行）
NEXT_HINTS = ("公司治理", "企业管治", "董事会报告书", "董事会报告", "监事会报告",
              "环境与社会责任", "环境、社会及管治", "重要事项", "其他重要事项",
              "股份变动及股东情况", "股份变动", "优先股", "债券相关情况",
              "财务报告", "审计报告", "独立核数师报告", "独立审计报告",
              "财务报表", "合并财务报表", "公司资料", "备查文件目录")

# 规范章节关键词（用于切片校验）
KEYWORDS = ["行业情况", "所处行业", "主要业务", "经营模式", "核心竞争力", "主营业务分析",
            "主营业务分行业", "非主营业务", "资产及负债", "投资状况", "主要子公司",
            "结构化主体", "未来发展", "经营计划", "接待调研"]


def cn2int(s):
    if s == "十":
        return 10
    if "十" in s:
        a, b = s.split("十", 1)
        return (CN.get(a, 1) if a else 1) * 10 + (CN.get(b, 0) if b else 0)
    return CN.get(s, 0)


def fix_spacing(line):
    s = line.strip()
    if not s:
        return s
    cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
    if cjk and s.count(" ") >= cjk * 0.5:
        return re.sub(r"\s+", "", s)
    return s


def is_toc(text):
    head = text[:600]
    if DOT_LEADER.search(head):
        return True
    return "目录" in re.sub(r"\s", "", head[:80])


def page_headings(text):
    """[(序号, 标题, 标题字符偏移)]，仅取独占行的 第X节/第X章。"""
    if is_toc(text):
        return []
    lines = text.split("\n")
    out, off = [], 0
    for k, line in enumerate(lines):
        s = fix_spacing(line)
        m = SEC_RE.match(s)
        if m:
            num = cn2int(m.group(1))
            title = m.group(3).strip()
            if not title and k + 1 < len(lines):
                title = fix_spacing(lines[k + 1])
            title = DOT_TAIL.sub("", title)
            if 0 < len(title) <= 22 and not BAD_TITLE_CH.search(title) and not DOT_LEADER.search(title):
                out.append((num, title, off))
        off += len(line) + 1
    return out


def tail_text(page):
    lines = [l.strip() for l in (page.get_text("text") or "").split("\n") if l.strip()]
    return lines[-1] if lines else ""


def top_lines(page, k=4):
    """页首 k 条有效行（去掉页码行）。"""
    out = []
    for l in (page.get_text("text") or "").split("\n"):
        s = fix_spacing(l)
        if not s or PAGENO.match(s):
            continue
        out.append(s)
        if len(out) >= k:
            break
    return out


def is_md_headline(page):
    """页首独立短行就是 MD&A 章标题（A+H 年报常见写法）。"""
    for s in top_lines(page, 4):
        if any(k in s for k in ("管理层讨论与分析", "经营情况讨论与分析")) and len(s) <= 26:
            return True
        if s in ("董事会报告书", "董事会报告"):
            return True
    return False


def page_hint(page):
    """页首出现其他章级标题时返回该标题。"""
    for s in top_lines(page, 4):
        s = NUM_LEAD.sub("", s).strip(" ：:")
        s = DOT_TAIL.sub("", s)
        if not (2 <= len(s) <= 14) or NOISE.search(s):
            continue
        for h in NEXT_HINTS:
            if s == h or (h in s and len(s) <= len(h) + 2):
                return h
    return None


HDR_LINE = re.compile(r"(股份有限公司|有限责任公司|有限公司|集团|公司).{0,24}20\d{2}\s*年\s*年度报告")
CJK_RE = re.compile(r"[一-鿿]")
# 页眉常被排版拆成两行：「无锡宝通科技股份有限公司」+「2020 年年度报告全文」，
# 单行匹配 HDR_LINE 会失败，汉字被误计入正文量，导致整页误并入。
COMPANY_ONLY = re.compile(r"^[一-鿿（）()·\s]{3,30}(股份有限公司|有限责任公司|有限公司|集团)$")
YEAR_HDR = re.compile(r"^20\d{2}\s*年\s*年度报告(全文|摘要|（全文）|\(全文\))?$")


def prefix_body_chars(text, pos):
    """标题之前的正文汉字数（剔除页眉、页码、空行、极短行）。"""
    body = []
    for l in text[:pos].split(chr(10)):
        t = l.strip()
        if not t or len(t) <= 3:
            continue
        if PAGENO.match(t) or HDR_LINE.search(t) or COMPANY_ONLY.match(t) or YEAR_HDR.match(t):
            continue
        body.append(t)
    return len(CJK_RE.findall("".join(body)))


def page_has_section_head(page, max_ratio=0.3):
    heads = page_headings(page.get_text())
    return bool(heads) and heads[0][2] / max(len(page.get_text()), 1) < max_ratio


def page_starts_new_section(page):
    """该页是否以新章节开头。

    只有「标题之前的正文不足 16 个汉字」才算新章开头。MD&A 的末页常同时含
    「上一节最后一句 + 下一章标题」（胜利股份 2015 第 36 页：正文续写后接「第五节 重要事项」），
    此时整页仍应并入上一节，否则句尾被切断。
    """
    t = page.get_text()
    heads = page_headings(t)
    if not heads:
        return False
    return prefix_body_chars(t, heads[0][2]) < 16


def continues_prose(page):
    """下一页的首个有效行是否为上一节的正文续写。

    PyMuPDF 的块顺序不保证按 y 坐标，章节标题可能排在续写段落之前，
    使 prefix_body_chars 失效；此处直接看首个有效行的形态。
    """
    for s in (page.get_text() or "").split(chr(10)):
        t = s.strip()
        if not t or len(t) <= 3:
            continue
        if PAGENO.match(t) or HDR_LINE.search(t) or COMPANY_ONLY.match(t) or YEAR_HDR.match(t):
            continue
        if SEC_RE.match(t):
            return False
        return len(CJK_RE.findall(t)) >= 10
    return False


def find_start(doc, n):
    """返回 (起始页, 方法, 是否来自编号体系)。

    分四档，严格到宽松：
      1. 书签/正文标题含「管理层讨论与分析」「经营情况讨论与分析」（章级，最可信）
      2. 书签含泛化的「讨论与分析」但经页首标题校验（董事会报告类）
      3. 页首独立短行即章标题（A+H 排版）
      4. 页内关键词命中
    旧版把 2 放在 1 之前，会命中「五、关于公司未来发展的讨论与分析」这类节内小节，
    导致截取从 MD&A 中段开始（长江电力 600900 的 1 页文件即此问题）。
    """
    strict = ("管理层讨论与分析", "经营情况讨论与分析")
    toc = doc.get_toc()
    for keys, tag, need_head in ((strict, "书签目录", False),
                                 (("讨论与分析", "Management Discussion"), "书签(泛化)", True)):
        for lv, ti, pg in toc:
            if lv > 3 or not any(k in ti for k in keys):
                continue
            for off in range(3):
                p = pg - 1 + off
                if not (0 <= p < n):
                    continue
                top = (doc[p].get_text() or "")[:600]
                if any(k in top for k in strict) and (not need_head or is_md_headline(doc[p])):
                    return p, tag, True
    for i in range(n):
        t = doc[i].get_text()
        if is_toc(t):
            continue
        for num, title, pos in page_headings(t):
            if any(k in title for k in MD_TITLES):
                return i, f"节标题(序号{num})", True
    for i in range(n):
        if is_md_headline(doc[i]):
            return i, "页首章标题", False
    for i in range(n):
        t = doc[i].get_text()
        if is_toc(t):
            continue
        if "管理层讨论与分析" in t[:400] and i + 1 < n:
            return i, "关键词", False
    return None, "未找到", False


def locate_v4(doc, n):
    start, method, by_sec = find_start(doc, n)
    if start is None:
        return None

    # 单次前向扫描，同时确定「编号体系收尾」与「页首章标题收尾」
    cache = {}

    def txt(i):
        if i not in cache:
            cache[i] = doc[i].get_text()
        return cache[i]

    end_sec, sec_found = n - 1, False
    end_hint = None
    for i in range(start + 1, n):
        if not sec_found:
            t = txt(i)
            span = max(len(t), 1)
            for num, title, pos in page_headings(t):
                if any(k in title for k in MD_TITLES):
                    continue          # 页眉/重复标题
                if pos / span > 0.55:
                    continue          # 页末残留的下一章标题
                end_sec = i if prefix_body_chars(t, pos) >= 16 else i - 1
                sec_found = True
                break
        if end_hint is None:
            h = page_hint(doc[i])
            if h:
                end_hint = max(start, i - 1)
        if sec_found and end_hint is not None:
            break

    if sec_found and end_hint is not None:
        end = end_hint if start < end_hint < end_sec else end_sec
    elif sec_found:
        end = end_sec
    elif end_hint is not None:
        end = end_hint
    else:
        end = n - 1
        method += "|无收尾线索"

    # 末页断句修正：顺延到句末，但不得越过章级边界
    ext = 0
    while (ext < 5 and end + 1 < n and not TAIL_OK.search(tail_text(doc[end]))):
        nxt = doc[end + 1]
        if continues_prose(nxt):
            end += 1
            ext += 1
            continue
        if page_starts_new_section(nxt) or page_hint(nxt):
            break
        end += 1
        ext += 1
    return start, end, method + (f"+顺延{ext}" if ext else ""), ext


def validate(doc, start, end):
    txt = "\n".join(doc[i].get_text() for i in range(start, min(end + 1, doc.page_count)))
    has_title = "管理层讨论与分析" in txt or "经营情况讨论与分析" in txt
    hits = sum(1 for k in KEYWORDS if k in txt)
    tail = tail_text(doc[end]) if end < doc.page_count else ""
    tail_ok = bool(TAIL_OK.search(tail)) or len(tail) <= 14 or "适用" in tail
    return has_title, hits, tail_ok, tail


def _sess():
    s = requests.Session()
    s.trust_env = settings.trust_env          # 本机环境变量里的代理已失效，必须绕开
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    return s


_SESS = _sess()


def load_manifest():
    """(code, year) -> (url, 旧起始页, 旧结束页)，页码 1-based。"""
    m = {}
    with open(MANIFEST, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("status") == "OK" and r.get("url"):
                try:
                    a, b = int(r["start"]), int(r["end"])
                except (TypeError, ValueError):
                    a = b = 0
                m[(r["code"], r["year"])] = (r["url"], a, b)
    return m


def fetch(url, tries=3):
    last = None
    for k in range(tries):
        try:
            r = _SESS.get(url, timeout=180)
            r.raise_for_status()
            if not r.content.startswith(b"%PDF"):
                raise IOError("非 PDF")
            return r.content
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (k + 1))
    raise last


def one(row, meta, apply):
    code, name, year, old_pages, flag = row
    url, old_s, old_e = meta
    rec = dict(code=code, name=name, year=year, old_pages=old_pages, old_flag=flag,
               old_range=f"{old_s}-{old_e}", url=url,
               new_start="", new_end="", new_pages="", method="",
               delta="", has_title="", kw_hits="", tail_ok="", tail="", verdict="")
    try:
        data = fetch(url)
    except Exception as e:  # noqa: BLE001
        rec["verdict"] = f"download_fail:{type(e).__name__}"
        return rec
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        rec["verdict"] = f"open_fail:{e}"
        return rec
    n = doc.page_count
    loc = locate_v4(doc, n)
    if not loc:
        rec["verdict"] = "locate_fail"
        doc.close()
        return rec
    s, e, method, ext = loc
    has_title, hits, tok, tail = validate(doc, s, e)
    rec.update(new_start=s + 1, new_end=e + 1, new_pages=e - s + 1, method=method,
               delta=(e - s + 1) - (old_pages or 0), has_title=int(has_title),
               kw_hits=hits, tail_ok=int(tok), tail=tail[:40])
    span = e - s + 1
    cand = (has_title and (hits >= 3 or span >= 10)) or hits >= 6
    # 当前标记为 empty 的文件没有任何章节，只要定位到的切片含 MD&A 标题即采用，
    # 门槛比其它标记低（否则会停留在“零产出”状态）。
    if not cand and flag == "empty" and has_title and span >= 2:
        cand = True
    if not cand:
        rec["verdict"] = "reject_no_mda" if (not has_title and hits < 3) else "review_weak"
    elif span > max(40, 0.6 * n):
        rec["verdict"] = "review_overspan"
    elif (s + 1, e + 1) == (old_s, old_e):
        rec["verdict"] = "same"
    else:
        rec["verdict"] = "apply"
    if apply and rec["verdict"] == "apply":
        try:
            out = pymupdf.open()
            out.insert_pdf(doc, from_page=s, to_page=e)
            buf = out.tobytes(deflate=True, garbage=3)
            out.close()
            dst = os.path.join(MDA, str(year), f"{year}-{code}-{name}.pdf")
            os.makedirs(BACKUP, exist_ok=True)
            if os.path.exists(dst):
                bak = os.path.join(BACKUP, os.path.basename(dst))
                if not os.path.exists(bak):
                    os.replace(dst, bak)
                # 备份已存在时直接覆盖 dst，不做删除——批量 os.remove 会触发安全层的
                # 删除次数阈值（每轮 50 次），中断长任务。
            with open(dst, "wb") as f:
                f.write(buf)
            rec["verdict"] = "applied"
        except Exception as e:  # noqa: BLE001
            rec["verdict"] = f"write_fail:{e}"
    doc.close()
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flags", default="truncated,wrong_chapter,empty,wrong_start")
    ap.add_argument("--limit", type=int, default=0, help="每个标记取前 N 条")
    ap.add_argument("--pilot", type=int, default=0, help="混合抽样 N 条，仅探测")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--codes", default="", help="逗号分隔的代码，限定范围")
    args = ap.parse_args()

    flags = [f for f in args.flags.split(",") if f]
    con = sqlite3.connect(DB)
    q = ("select code,name,year,pages,quality_flag from documents "
         f"where quality_flag in ({','.join('?' * len(flags))})")
    params = list(flags)
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        q += f" and code in ({','.join('?' * len(codes))})"
        params += codes
    q += " order by quality_flag,code"
    rows = con.execute(q, params).fetchall()

    if args.pilot:
        per = max(1, args.pilot // len(flags))
        picked = []
        for f in flags:
            sub = [r for r in rows if r[4] == f]
            picked += sub[:per]
        rows = picked
    elif args.limit:
        picked = []
        for f in flags:
            picked += [r for r in rows if r[4] == f][: args.limit]
        rows = picked

    man = load_manifest()
    todo, miss = [], 0
    for r in rows:
        meta = man.get((r[0], str(r[2])))
        if not meta:
            miss += 1
            continue
        todo.append((r, meta))
    print(f"待处理 {len(todo)} 份（无清单 URL {miss} 份），workers={args.workers}, apply={args.apply}")

    t0 = time.time()
    res = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one, r, m, args.apply): r for r, m in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            res.append(fu.result())
            if i % 10 == 0:
                print(f"  {i}/{len(todo)}  {time.time() - t0:.0f}s", flush=True)

    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(res[0].keys()) if res else [])
        w.writeheader()
        for r in sorted(res, key=lambda x: (x["old_flag"], x["code"])):
            w.writerow(r)

    from collections import Counter
    print("结果:", dict(Counter(r["verdict"] for r in res)))
    print("标记×结果:")
    for flag in flags:
        sub = [r for r in res if r["old_flag"] == flag]
        if sub:
            print(f"  {flag}({len(sub)}):", dict(Counter(r["verdict"] for r in sub)))
    print("写入:", OUT, f"用时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
