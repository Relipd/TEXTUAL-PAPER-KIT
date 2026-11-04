# -*- coding: utf-8 -*-
"""
A股年报「管理层讨论与分析」章节批量抓取

两阶段架构：
  阶段A  规划：按公司查询巨潮公告列表，确定每个公司-年度对应哪一份年报全文，写入 _plan.csv
  阶段B  抓取：并发下载 PDF → 定位章节 → 抽取该区间页 → 另存 年份-代码-简称.pdf

特性：连接复用、全局限速、指数退避重试、断点续传（已存在的文件直接跳过）、
      失败清单单独落盘、分板块切换接口参数。

用法：
  python -m mda_kit.fetch_mda --plan-only      # 只生成清单，不下载
  python -m mda_kit.fetch_mda                  # 全量（读已有清单，缺则先规划）
  python -m mda_kit.fetch_mda --procs 8        # 下载/解析进程数
  python -m mda_kit.fetch_mda --codes 000858 --years 2024
  python -m mda_kit.fetch_mda --replan         # 强制重建清单
"""
import argparse
import csv
import os
import re
import sys
import threading
import time
import random
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed

import requests
import pymupdf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .config import settings

CSV_PATH = str(settings.targets_csv)
OUT_DIR = str(settings.mda_dir)
TMP_DIR = os.path.join(OUT_DIR, "_tmp")
PLAN = os.path.join(OUT_DIR, "_plan.csv")
MANIFEST = os.path.join(OUT_DIR, "_manifest.csv")
FAILED = os.path.join(OUT_DIR, "_failed.csv")
STRICT_BOUNDARY = settings.strict_boundary

UA_HEADERS = {
    "User-Agent": settings.user_agent,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ---------------------------------------------------------------- 网络层
_thread_local = threading.local()


def session():
    """每线程一个 Session，复用 TCP 连接；首次建立时预热以取得必要的 cookie。"""
    s = getattr(_thread_local, "s", None)
    if s is None:
        s = requests.Session()
        s.trust_env = settings.trust_env                      # 本机代理已失效，强制直连
        ad = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
        s.mount("https://", ad)
        s.mount("http://", ad)
        s.headers.update(UA_HEADERS)
        try:                                     # 未预热时接口会返回 403
            s.get("http://www.cninfo.com.cn/new/index", timeout=20)
        except Exception:
            pass
        _thread_local.s = s
    return s


class RateLimiter:
    """全局令牌节流，避免并发过高触发风控。"""

    def __init__(self, rps):
        self.interval = 1.0 / rps
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                delay = self.next_at - now
                self.next_at += self.interval
            else:
                delay = 0.0
                self.next_at = now + self.interval
        if delay > 0:
            time.sleep(delay)


_limiter = RateLimiter(settings.rps)


# ---------------------------------------------------------------- 目标清单
def load_targets(csv_path=CSV_PATH):
    """返回 {代码: {'name': 简称, 'years': {年份: 统计截止日}}}，仅 12-31 年报行，已去重。"""
    targets = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            accper = (row.get("Accper") or "").strip()
            if not accper.endswith("/12/31") and not accper.endswith("-12-31"):
                continue
            code = (row.get("Stkcd") or "").strip().zfill(6)
            if not code.isdigit():
                continue
            rec = targets.setdefault(code, {"name": (row.get("ShortName") or "").strip(), "years": {}})
            rec["years"][int(accper[:4])] = accper
    return targets


# ---------------------------------------------------------------- 巨潮接口
_org_map = None
_org_lock = threading.Lock()


def org_map():
    global _org_map
    with _org_lock:
        if _org_map is None:
            r = session().get("http://www.cninfo.com.cn/new/data/szse_stock.json",
                              timeout=60)
            r.raise_for_status()
            _org_map = {x["code"]: x.get("orgId") for x in r.json().get("stockList", [])}
        return _org_map


def board_of(code):
    if code.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return "sse", "sh"
    if code.startswith(("8", "4", "920")):
        return "bj", "bj"
    return "szse", "sz"


def _retry(fn, tries=4, base=1.2):
    last = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(base * (2 ** k) + random.random())
    raise last


def _query_by_code(code, org):
    column, plate = board_of(code)
    payload = {
        "pageNum": 1, "pageSize": 30, "column": column, "tabName": "fulltext",
        "plate": plate, "stock": f"{code},{org}", "searchkey": "", "secdate": "",
        "category": "category_ndbg_szsh", "trade": "", "seDate": "",
        "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    headers = {"Referer": f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}&orgId={org}"}

    def _go():
        _limiter.wait()
        r = session().post("https://www.cninfo.com.cn/new/hisAnnouncement/query",
                           data=payload, headers=headers, timeout=45)
        r.raise_for_status()
        return r.json().get("announcements") or []

    return _retry(_go)


def query_annual_reports(code):
    """查某公司的年报公告。

    920xxx 为北交所转板代码，其历史年报挂在新三板旧代码下。orgId 形如
    「gfbj0 + 旧六位代码」，可直接还原旧代码后重查，无需全文检索接口。
    """
    org = org_map().get(code)
    if not org:
        return []
    anns = _query_by_code(code, org)
    if not anns and code.startswith("920") and org.startswith("gfbj0"):
        old = org[5:]
        if old.isdigit() and len(old) == 6 and old != code and old in org_map():
            try:
                anns = _query_by_code(old, org)
            except Exception:
                anns = []
    return anns


BAD_TITLE = ("摘要", "英文", "English", "港股", "已取消", "更正公告", "补充公告",
             "问询", "监管工作函", "审计报告", "半年度")


def pick_reports(anns, years):
    """同一年有多版时取披露时间最新的一版（严格判定标题必须是年报正文）。"""
    best = {}
    for a in anns:
        title = (a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
        if "年度报告" not in title and "年报" not in title:
            continue
        m = re.search(r"(20\d{2})\s*年", title)
        if not m:
            continue
        y = int(m.group(1))
        if years and y not in years:
            continue
        if not _valid_title(title, y):
            continue
        ts = a.get("announcementTime") or 0
        if y not in best or ts > best[y]["ts"]:
            best[y] = {"ts": ts, "title": title, "size": a.get("adjunctSize") or 0,
                       "url": "https://static.cninfo.com.cn/" + a["adjunctUrl"]}
    return best


def fill_missing(targets, missing, api_workers=3):
    """用逐公司接口补齐全市场检索遗漏的公司-年度。

    missing: {代码: {需要补的年份集合}}
    """
    rows, lock = [], threading.Lock()

    def work(code):
        try:
            picked = pick_reports(query_annual_reports(code), missing[code])
        except Exception:
            picked = {}
        local = [{"code": code, "name": targets[code]["name"], "year": y,
                  "url": picked[y]["url"], "title": picked[y]["title"],
                  "ts": picked[y]["ts"], "size": picked[y]["size"], "alts": ""}
                 for y in sorted(missing[code]) if y in picked]
        if local:
            with lock:
                rows.extend(local)

    with ThreadPoolExecutor(max_workers=api_workers) as ex:
        list(ex.map(work, list(missing)))
    return rows


# ---------------------------------------------------------------- 全量检索接口
SEARCH_URL = "https://www.cninfo.com.cn/new/fulltextSearch/full"
SEARCH_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "http://www.cninfo.com.cn",
    "Referer": "http://www.cninfo.com.cn/new/fulltextSearch",
}


def _search_once(year, page, page_size=100):
    payload = {
        "searchkey": f"{year}年年度报告",
        "sdate": f"{year + 1}-01-01", "edate": f"{year + 1}-12-31",
        "isfulltext": "false", "sortName": "pubdate", "sortType": "desc",
        "pageNum": page, "pageSize": page_size, "type": "", "isHLtitle": "true",
    }
    _limiter.wait()
    r = session().post(SEARCH_URL, data=payload, headers=SEARCH_HEADERS, timeout=60)
    if r.status_code != 200:
        raise IOError(f"HTTP {r.status_code}")
    j = r.json()
    return j.get("totalAnnouncement") or 0, (j.get("announcements") or [])


def search_year(year, page, tries=5):
    """带退避的检索；被限流时自动降速重试。"""
    last = None
    for k in range(tries):
        try:
            return _search_once(year, page)
        except Exception as e:
            last = e
            code = str(e)
            wait = 60 if "403" in code else 3 * (2 ** k)
            time.sleep(wait + random.random() * 5)
    raise last


# ---------------------------------------------------------------- 章节定位
CN_DIGIT = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
            "七": 7, "八": 8, "九": 9, "十": 10}


def cn2int(s):
    if s == "十":
        return 10
    if "十" in s:
        a, b = s.split("十", 1)
        return (CN_DIGIT.get(a, 1) if a else 1) * 10 + (CN_DIGIT.get(b, 0) if b else 0)
    return CN_DIGIT.get(s, 0)


SEC_RE = re.compile(r"^第?\s*([一二三四五六七八九十]{1,3})\s*[节章]\s*(.*)$")
DOT_TAIL = re.compile(r"[\s.。·]+$")
BAD_TITLE_CH = re.compile(r'[“”"《》…]')
DOT_LEADER = re.compile(r"\.{4,}|…{2,}")

MD_TITLES = ("管理层讨论与分析", "经营情况讨论与分析")
ALT_TITLES = ("董事会报告",)


def _fix_spacing(line):
    """逐字排版（「第 一 章 公 司 简 介」）会击穿标题匹配，按空格密度判断后压平。"""
    s = line.strip()
    if not s:
        return s
    cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
    if cjk and s.count(" ") >= cjk * 0.5:
        return re.sub(r"\s+", "", s)
    return s


def _is_toc_page(text):
    head = text[:600]
    if DOT_LEADER.search(head):
        return True
    return "目录" in re.sub(r"\s", "", head[:80])


def _page_headings(text):
    """抽取该页所有独占行的「第X节/第X章」标题，返回 [(序号, 标题, 标题在该页的字符偏移)]。"""
    if _is_toc_page(text):
        return []
    lines = text.split("\n")
    out, offset = [], 0
    for k, line in enumerate(lines):
        s = _fix_spacing(line)
        m = SEC_RE.match(s)
        if m:
            num = cn2int(m.group(1))
            title = m.group(2).strip()
            if not title and k + 1 < len(lines):
                title = _fix_spacing(lines[k + 1])
            title = DOT_TAIL.sub("", title)
            if 0 < len(title) <= 22 and not BAD_TITLE_CH.search(title) and not DOT_LEADER.search(title):
                out.append((num, title, offset))
        offset += len(line) + 1
    return out


def _narrow_by_toc(doc, lo, hi):
    """在 [lo, hi] 内用二级书签把「董事会报告」收窄到其讨论与分析子节。"""
    toc = [(i, lv, ti, pg - 1) for i, (lv, ti, pg) in enumerate(doc.get_toc())]
    for k, (idx, lv, ti, pg) in enumerate(toc):
        if lv < 2 or not (lo <= pg <= hi) or "讨论与分析" not in ti:
            continue
        end = hi
        for _i2, lv2, _t2, pg2 in toc[k + 1:]:
            if lv2 <= lv:
                end = min(hi, pg2 - 1)
                break
        return pg, max(pg, end)
    return None


def _heading_ratio(text, page_headings):
    """返回该页第一个可用的 (序号, 标题, 标题在页面中的偏移占比)。"""
    span = max(len(text), 1)
    if not page_headings:
        return None
    num, title, pos = page_headings[0]
    return num, title, pos / span


def _locate_by_toc(doc, n):
    """书签优先：开销接近零，覆盖大多数有目录的 PDF。"""
    toc = doc.get_toc()
    if not toc:
        return None
    entries = [(i, lv, ti, pg - 1) for i, (lv, ti, pg) in enumerate(toc)]
    for k, (idx, lv, ti, pg) in enumerate(entries):
        if not (pg >= 0 and lv <= 3):
            continue
        if not ("讨论与分析" in ti or ("Management Discussion" in ti and lv <= 2)):
            continue
        # 书签页码可能偏一页，用前 3 页文本校验
        start = None
        for off in range(3):
            p = pg + off
            if p < 0 or p >= n:
                continue
            t = doc[p].get_text()
            if "讨论与分析" in t[:600] or "Management Discussion" in t[:600]:
                start = p
                break
        if start is None:
            continue
        end = n - 1
        for _i2, lv2, _t2, pg2 in entries[k + 1:]:
            if lv2 <= lv:
                end = pg2 - 1
                break
        end = min(max(start, end), n - 1)
        # 末页若仍含本节正文（下一节标题落在页面后半），保留整页
        nxt = end + 1
        if nxt < n and not STRICT_BOUNDARY:
            t = doc[nxt].get_text()
            info = _heading_ratio(t, _page_headings(t))
            if info and info[2] > 0.4:
                end = nxt
        return start, end, "书签目录"
    return None


def _locate_by_scan(doc, n):
    """流式扫描：逐页抽文本，命中下一节标题立即停止，避免读取全文。"""
    start = alt = None
    mda_num = alt_num = 0
    for i in range(n):
        t = doc[i].get_text()
        heads = _page_headings(t)
        if not heads:
            continue
        span = max(len(t), 1)
        if start is None and alt is None:
            for num, title, pos in heads:
                if any(k in title for k in MD_TITLES):
                    start, mda_num = i, num
                    break
                if any(k in title for k in ALT_TITLES):
                    alt, alt_num = i, num
                    break
            if start is None and alt is None:
                continue
            # 本节起始页内若已出现下一节，直接返回
        base_num = mda_num if start is not None else alt_num
        if start is not None or alt is not None:
            for num, title, pos in heads:
                if num > base_num:
                    end = i if (pos / span > 0.4 and not STRICT_BOUNDARY) else i - 1
                    if start is not None:
                        return start, max(start, end), f"正文节标题(第{mda_num}节)"
                    sec = max(alt, end)
                    sub = _narrow_by_toc(doc, alt, sec)
                    if sub:
                        return sub[0], sub[1], f"董事会报告-子节(第{alt_num}节)"
                    return alt, sec, f"董事会报告替代(第{alt_num}节)"
    if start is not None:
        return start, n - 1, f"正文节标题(第{mda_num}节)"
    if alt is not None:
        sec = n - 1
        sub = _narrow_by_toc(doc, alt, sec)
        if sub:
            return sub[0], sub[1], f"董事会报告-子节(第{alt_num}节)"
        return alt, sec, f"董事会报告替代(第{alt_num}节)"
    return None


def _locate_by_keyword(doc, n):
    """兜底：按关键词就近匹配，逐个页面查找。"""
    first = None
    for i in range(n):
        t = doc[i].get_text()
        if _is_toc_page(t):
            continue
        if first is None and "管理层讨论与分析" in t[:400]:
            first = i
            continue
        if first is not None:
            if re.search(r"第\s*[一二三四五六七八九十]{1,3}\s*节", t[:400]) or "重要事项" in t[:200]:
                return first, max(first, i - 1), "关键词兜底"
    if first is not None:
        return first, n - 1, "关键词兜底"
    return None


def locate_mda(doc):
    """返回 (起始页, 结束页, 方法说明)，页码 0-based。失败返回 None。"""
    n = doc.page_count
    r = _locate_by_toc(doc, n)
    if r:
        return r
    r = _locate_by_scan(doc, n)
    if r:
        return r
    return _locate_by_keyword(doc, n)


def extract_pages(doc, start, end):
    out = pymupdf.open()
    out.insert_pdf(doc, from_page=start, to_page=end)
    buf = out.tobytes(deflate=True, garbage=3)
    out.close()
    return buf


# ---------------------------------------------------------------- 下载
def download(url, path=None, ref=None, tries=4):
    """下载 PDF。传 path 则落盘，否则直接返回字节（内存解析，不产生临时文件）。"""
    headers = {"Referer": ref} if ref else {}
    last = None
    for k in range(tries):
        try:
            _limiter.wait()
            if path:
                with session().get(url, headers=headers, timeout=180, stream=True) as r:
                    r.raise_for_status()
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(131072):
                            f.write(chunk)
                size = os.path.getsize(path)
                with open(path, "rb") as f:
                    magic = f.read(5)
                if size < 20000 or not magic.startswith(b"%PDF"):
                    raise IOError(f"非 PDF 响应 size={size}")
                return None
            with session().get(url, headers=headers, timeout=180) as r:
                r.raise_for_status()
                data = r.content
            if len(data) < 20000 or not data.startswith(b"%PDF"):
                raise IOError(f"非 PDF 响应 size={len(data)}")
            return data
        except Exception as e:
            last = e
            wait = 45 if "403" in str(e) else 1.5 * (2 ** k)
            time.sleep(wait + random.random() * 5)
    raise last


# ---------------------------------------------------------------- 单条处理
def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\r\n\t]', "", s).strip()


def _belongs_to(doc, n, code, name="", title=""):
    """校验文档是否属于该公司。

    仅拦截债券担保方年报（标题含「担保」）——这类文件挂在发行人公告下，
    内容却是担保方的年报。证券代码不可靠：借壳、更名会让历史年报沿用旧代码
    （如深赤湾A 000022 → 招商港口 001872），按代码比对会大量误杀。
    """
    return "担保" not in (title or "")


def process_row(row, skip_existing=True):
    code, name, year = row["code"], row["name"], int(row["year"])
    urls = [row["url"]] + [u for u in (row.get("alts") or "").split("|") if u]
    rec = {"code": code, "name": name, "year": year, "status": "", "pages": "",
           "start": "", "end": "", "method": "", "url": row["url"]}
    fname = f"{year}-{code}-{safe_name(name)}.pdf"
    ydir = os.path.join(OUT_DIR, str(year))
    fpath = os.path.join(ydir, fname)
    legacy = os.path.join(OUT_DIR, fname)          # 兼容扁平目录的旧文件
    if skip_existing:
        for p in (fpath, legacy):
            if os.path.exists(p) and os.path.getsize(p) > 2000:
                rec["status"] = "已存在"
                return rec

    for idx, url in enumerate(urls):
        try:
            # 全程内存解析：不落临时文件，避免删除受限导致磁盘堆积
            raw = download(url, ref=f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}")
            doc = pymupdf.open(stream=raw, filetype="pdf")
            n = doc.page_count
            if not _belongs_to(doc, n, code, name, row.get("title", "")):
                doc.close()
                rec["method"] = f"非本公司文件(候选{idx+1})"
                continue
            loc = locate_mda(doc)
            if not loc:
                doc.close()
                rec["status"] = "定位失败"
                rec["method"] = "无可识别的管理层讨论与分析章节"
                continue
            start, end, method = loc
            rec.update({"start": start + 1, "end": end + 1,
                        "pages": end - start + 1, "method": method})
            data = extract_pages(doc, start, end)
            doc.close()
            os.makedirs(ydir, exist_ok=True)
            with open(fpath, "wb") as f:
                f.write(data)
            rec["status"] = "OK"
            rec["url"] = url
            return rec
        except Exception as e:
            rec["status"] = f"异常:{type(e).__name__}"
            rec["method"] = str(e)[:80]
    if not rec["status"]:
        rec["status"] = "非本公司文件"
        if not rec["method"]:
            rec["method"] = "所有候选均未通过归属校验"
    return rec


# ---------------------------------------------------------------- 阶段A：清单
PLAN_COLS = ["code", "name", "year", "url", "title", "ts", "size", "alts"]
RAW_COLS = ["code", "year", "title", "ts", "url", "size"]


def read_plan():
    if not os.path.exists(PLAN):
        return []
    with open(PLAN, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


_ANN = re.compile(r"(20\d{2})\s*年?\s*(?:年度?报告|年?报)(全文)?(（[^）]{0,12}）)?\s*$")


def _valid_title(title, year):
    """是否确为该会计年度的年报正文。

    必须以「{年}年(+度)报告」收尾（可带「全文」或「（更新后）」等后缀），
    从而排除《关于2020年年度报告更正的公告》《独立董事意见》《延期披露公告》等
    仅在标题中提及年度报告的无关文件。
    """
    t = (title or "").replace(" ", "").replace("　", "")
    t = re.sub(r"^[^：:]{0,24}[：:]", "", t)          # 去掉「公司名：」前缀
    m = _ANN.search(t)
    if not m or m.group(1) != str(year):
        return False
    suffix = m.group(3) or ""
    if any(k in suffix for k in ("英文", "English", "摘要", "节选", "英文版")):
        return False
    return True


def plan_by_search(targets, years, workers=6):
    """全市场按年检索年报（fulltextSearch），替代逐公司查询。

    每个会计年度的年报在次年 1-12 月披露，按「{年}年年度报告」检索约 150 页，
    11 年合计约 1,400 次请求，比逐公司查询少 4 倍以上。
    """
    raw_path = os.path.join(OUT_DIR, "_raw_annual.csv")
    fh = open(raw_path, "a", encoding="utf-8-sig", newline="")
    w = csv.DictWriter(fh, fieldnames=RAW_COLS)
    if fh.tell() == 0:
        w.writeheader()
    lock = threading.Lock()
    state = {"rows": 0, "pages": 0}

    def collect(year, arr):
        local = []
        for a in arr:
            code = (a.get("secCode") or "").strip()
            if code not in targets:
                continue
            title = (a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "").strip()
            url = a.get("adjunctUrl") or ""
            if not url:
                continue
            local.append({"code": code, "year": year, "title": title,
                          "ts": a.get("announcementTime") or 0,
                          "url": "https://static.cninfo.com.cn/" + url,
                          "size": a.get("adjunctSize") or 0})
        if local:
            with lock:
                w.writerows(local)
                state["rows"] += len(local)
        return len(local)

    def fetch_page(year, page):
        total, arr = search_year(year, page)
        collect(year, arr)
        with lock:
            state["pages"] += 1
            if state["pages"] % 50 == 0:
                fh.flush()
                print(f"   已抓 {state['pages']} 页，命中 {state['rows']} 条", flush=True)

    print("[规划] 全市场按年检索年报 …", flush=True)
    t0 = time.time()
    tasks = []
    for y in years:
        total, arr = search_year(y, 1)
        npages = min((total + 99) // 100, 500)
        collect(y, arr)
        print(f"   {y} 年度：检索 {total} 条 / {npages} 页", flush=True)
        tasks += [(y, p) for p in range(2, npages + 1)]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fetch_page, y, p) for y, p in tasks]
        for fu in as_completed(futs):
            try:
                fu.result()
            except Exception as e:
                print(f"   [页失败] {e}", flush=True)
    fh.close()
    print(f"[规划] 检索完成，用时 {time.time() - t0:.0f}s，命中 {state['rows']} 条", flush=True)
    return _rebuild_plan(targets)


def _rebuild_plan(targets):
    """从检索结果原始表汇总清单。

    每个公司-年度保留最多 4 个候选（按披露时间倒序），抓取阶段逐个尝试，
    以便在首选文件不属于本公司时自动回退。
    """
    raw_path = os.path.join(OUT_DIR, "_raw_annual.csv")
    cands = {}
    with open(raw_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            code, year = r["code"], int(r["year"])
            if code not in targets or not _valid_title(r["title"], year):
                continue
            cands.setdefault((code, year), []).append(
                {"ts": int(r["ts"] or 0), "title": r["title"], "url": r["url"],
                 "size": r.get("size") or 0})

    rows = []
    for (code, year), lst in cands.items():
        lst.sort(key=lambda v: v["ts"], reverse=True)
        seen = set()
        uniq = []
        for v in lst:
            if v["url"] in seen:
                continue
            seen.add(v["url"])
            uniq.append(v)
            if len(uniq) >= 4:
                break
        head = uniq[0]
        rows.append({"code": code, "name": targets[code]["name"], "year": year,
                     "url": head["url"], "title": head["title"], "ts": head["ts"],
                     "size": head["size"],
                     "alts": "|".join(v["url"] for v in uniq[1:])})
    rows.sort(key=lambda r: (r["code"], r["year"]))
    with open(PLAN, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PLAN_COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"[规划] 清单 {len(rows)} 个公司-年度，写入 {PLAN}\n", flush=True)
    return rows


def search_by_name(name, page_size=100):
    """按公司简称检索其全部公告（用于代码变更的北交所公司）。"""
    payload = {
        "searchkey": f"{name} 年度报告", "sdate": "", "edate": "",
        "isfulltext": "false", "sortName": "pubdate", "sortType": "desc",
        "pageNum": 1, "pageSize": page_size, "type": "", "isHLtitle": "true",
    }

    def _go():
        _limiter.wait()
        r = session().post(SEARCH_URL, data=payload, headers=SEARCH_HEADERS, timeout=60)
        r.raise_for_status()
        return r.json().get("announcements") or []

    return _retry(_go)


def plan_by_name(targets, pairs, workers=3):
    """按简称补漏：920xxx 等转板公司的历史年报挂在新三板旧代码下，
    逐公司接口按当前代码查不到，只能按名称检索。"""
    by_name = {}
    for c, y in pairs:
        by_name.setdefault(targets[c]["name"], []).append((c, y))
    print(f"[补漏] 按简称检索 {len(by_name)} 家公司（{len(pairs)} 个公司-年度）…", flush=True)
    rows, lock = [], threading.Lock()

    def work(name):
        local = []
        code_years = {y: c for c, y in by_name[name]}
        try:
            anns = search_by_name(name)
        except Exception:
            return
        for a in anns:
            title = (a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
            core = re.sub(r"^\[[^\]]*\]", "", title)          # 去掉 [定期报告] 前缀
            core = re.sub(r"^[^：:]{1,20}[：:]", "", core)        # 去掉 “公司名：”
            m = re.search(r"(20\d{2})年年度报告", core)
            if not m:
                continue
            y = int(m.group(1))
            if y not in code_years or not _valid_title(core, y):
                continue
            code = code_years[y]
            local.append({"code": code, "name": name, "year": y,
                          "url": "https://static.cninfo.com.cn/" + a["adjunctUrl"],
                          "title": core, "ts": a.get("announcementTime") or 0,
                          "size": a.get("adjunctSize") or 0, "alts": ""})
        if local:
            with lock:
                rows.extend(local)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, list(by_name)))

    best = {}
    for r in rows:
        k = (r["code"], r["year"])
        if k not in best or r["ts"] > best[k]["ts"]:
            best[k] = r
    out = sorted(best.values(), key=lambda r: (r["code"], r["year"]))
    print(f"[补漏] 按简称找回 {len(out)} 个公司-年度", flush=True)
    return out


def build_plan(targets, codes, yfilter=None, api_workers=5):
    """逐公司查询公告列表（备用方案），**增量落盘**，中断后可续。"""
    done_codes = {r["code"] for r in read_plan()}
    todo = [c for c in codes if c not in done_codes]
    print(f"[规划] 待查询 {len(todo)} 家（已有 {len(done_codes)} 家）…", flush=True)
    if not todo:
        return read_plan()

    fh = open(PLAN, "a", encoding="utf-8-sig", newline="")
    w = csv.DictWriter(fh, fieldnames=PLAN_COLS)
    if fh.tell() == 0:
        w.writeheader()
    lock = threading.Lock()
    t0 = time.time()
    counter = {"n": 0}

    def work(code):
        t = targets[code]
        years = set(t["years"])
        if yfilter:
            years &= yfilter
        if not years:
            return
        try:
            picked = pick_reports(query_annual_reports(code), years)
        except Exception:
            picked = {}
        local = [{"code": code, "name": t["name"], "year": y,
                  "url": picked[y]["url"] if y in picked else "",
                  "title": picked[y]["title"] if y in picked else "",
                  "ts": picked[y]["ts"] if y in picked else 0, "size": 0, "alts": ""}
                 for y in sorted(years)]
        with lock:
            w.writerows(local)
            counter["n"] += 1
            if counter["n"] % 100 == 0:
                fh.flush()
                el = time.time() - t0
                print(f"   {counter['n']}/{len(todo)} 家  {counter['n'] / el:.1f} 家/s", flush=True)

    with ThreadPoolExecutor(max_workers=api_workers) as ex:
        list(ex.map(work, todo))
    fh.close()

    rows = read_plan()
    hit = sum(1 for r in rows if r["url"])
    print(f"[规划] 完成：{len(rows)} 个公司-年度，可下载 {hit}，缺年报 {len(rows) - hit}\n", flush=True)
    return rows


def load_plan():
    return [r for r in read_plan() if r["url"]]


# ---------------------------------------------------------------- 阶段B：抓取
class Reporter:
    def __init__(self, total):
        self.total = total
        self.done = 0
        self.ok = 0
        self.exist = 0
        self.fail = 0
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.mbuf, self.fbuf = [], []
        self.mfh = open(MANIFEST, "a", encoding="utf-8-sig", newline="")
        self.ffh = open(FAILED, "a", encoding="utf-8-sig", newline="")
        self.cols = ["code", "name", "year", "status", "pages", "start", "end", "method", "url"]
        self.mw = csv.DictWriter(self.mfh, fieldnames=self.cols)
        self.fw = csv.DictWriter(self.ffh, fieldnames=self.cols)
        if self.mfh.tell() == 0:
            self.mw.writeheader()
        if self.ffh.tell() == 0:
            self.fw.writeheader()

    def add(self, rec):
        with self.lock:
            self.done += 1
            if rec["status"] == "OK":
                self.ok += 1
            elif rec["status"] == "已存在":
                self.exist += 1
            else:
                self.fail += 1
                self.fbuf.append({k: rec.get(k, "") for k in self.cols})
            if rec["status"] == "OK":
                self.mbuf.append({k: rec.get(k, "") for k in self.cols})
            if len(self.mbuf) >= 40:
                self.mw.writerows(self.mbuf); self.mbuf = []
                self.mfh.flush()
            if len(self.fbuf) >= 20:
                self.fw.writerows(self.fbuf); self.fbuf = []
                self.ffh.flush()
            if self.done % 25 == 0 or self.done == self.total:
                self._print()

    def _print(self):
        el = time.time() - self.t0
        rate = self.done / el if el > 0 else 0
        eta = (self.total - self.done) / rate / 60 if rate > 0 else 0
        print(f"  {self.done}/{self.total}  成功{self.ok} 跳过{self.exist} 失败{self.fail}  "
              f"{rate:.1f}/s  剩余 {eta:.0f} 分钟", flush=True)

    def close(self):
        with self.lock:
            if self.mbuf:
                self.mw.writerows(self.mbuf)
            if self.fbuf:
                self.fw.writerows(self.fbuf)
            self.mfh.close(); self.ffh.close()


# ---------------------------------------------------------------- 多进程
def _proc_init(out_dir, tmp_dir, strict, rps):
    """子进程初始化：同步路径配置与独立限速器。"""
    global OUT_DIR, TMP_DIR, STRICT_BOUNDARY, _limiter
    OUT_DIR, TMP_DIR, STRICT_BOUNDARY = out_dir, tmp_dir, strict
    _limiter = RateLimiter(rps)


def _process_chunk(rows, skip_existing):
    return [process_row(r, skip_existing) for r in rows]


# ---------------------------------------------------------------- 主流程
TEST_SET = [("000858", "五粮液", [2024]),
            ("600519", "贵州茅台", [2023]),
            ("300750", "宁德时代", [2022]),
            ("000002", "万科A", [2020]),
            ("688981", "中芯国际", [2021])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--codes", default="")
    ap.add_argument("--years", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--procs", type=int, default=8, help="下载+解析的进程数，绕开 GIL")
    ap.add_argument("--api-workers", type=int, default=5, help="规划阶段的并发查询数")
    ap.add_argument("--rps", type=float, default=settings.rps, help="规划阶段限速，请求/秒")
    ap.add_argument("--dl-rps", type=float, default=settings.dl_rps, help="每个下载进程限速，请求/秒")
    ap.add_argument("--out", default="")
    ap.add_argument("--planner", choices=["search", "company"], default="search",
                    help="search=全市场按年检索(默认)；company=逐公司查询")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--replan", action="store_true")
    ap.add_argument("--no-fill", action="store_true", help="跳过逐公司补漏")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    global OUT_DIR, TMP_DIR, PLAN, MANIFEST, FAILED, STRICT_BOUNDARY, _limiter
    if args.out:
        OUT_DIR = args.out
        TMP_DIR = os.path.join(OUT_DIR, "_tmp")
        PLAN = os.path.join(OUT_DIR, "_plan.csv")
        MANIFEST = os.path.join(OUT_DIR, "_manifest.csv")
        FAILED = os.path.join(OUT_DIR, "_failed.csv")
    STRICT_BOUNDARY = args.strict
    _limiter = RateLimiter(args.rps)

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)

    targets = load_targets()
    yfilter = {int(y) for y in args.years.split(",") if y.strip().isdigit()} if args.years else None

    # 决定处理范围
    if args.test:
        rows = []
        for code, name, yrs in TEST_SET:
            anns = query_annual_reports(code)
            picked = pick_reports(anns, set(yrs))
            for y in yrs:
                if y in picked:
                    rows.append({"code": code, "name": name, "year": y,
                                 "url": picked[y]["url"], "title": picked[y]["title"]})
    else:
        if args.codes:
            codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
        else:
            codes = list(targets)
        if args.limit:
            codes = codes[: args.limit]
        cset = {c for c in codes if c in targets}
        for c in codes:
            if c not in targets:
                print(f"[跳过] {c} 不在 CSV 清单内")

        all_years = sorted({y for t in targets.values() for y in t["years"]})
        if yfilter:
            all_years = [y for y in all_years if y in yfilter]

        if args.planner == "search":
            raw = os.path.join(OUT_DIR, "_raw_annual.csv")
            if args.replan and os.path.exists(raw):
                os.remove(raw)
            if os.path.exists(raw) and os.path.getsize(raw) > 100:
                print("[规划] 复用已有检索结果，重建清单 …")
                rows = _rebuild_plan(targets)
            else:
                rows = plan_by_search(targets, all_years, workers=args.api_workers)

            # 补漏：全市场检索存在召回缺口，用逐公司接口核对缺失的公司-年度
            have = {(r["code"], int(r["year"])) for r in rows}
            missing = {}
            for c in cset:
                need = {y for y in targets[c]["years"]
                        if y in all_years and (c, y) not in have}
                if need:
                    missing[c] = need
            if missing and not args.no_fill:
                n_need = sum(len(v) for v in missing.values())
                print(f"[规划] 检索遗漏 {n_need} 个公司-年度（{len(missing)} 家公司），逐公司核对 …", flush=True)
                extra = fill_missing(targets, missing, api_workers=3)
                merged = {(r["code"], int(r["year"])): r for r in rows}
                for r in extra:
                    merged[(r["code"], int(r["year"]))] = r
                rows = [merged[k] for k in sorted(merged)]
                with open(PLAN, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=PLAN_COLS)
                    w.writeheader()
                    w.writerows(rows)
                print(f"[规划] 补齐 {len(extra)} 个，清单合计 {len(rows)}\n", flush=True)

            # 二次补漏：代码变更公司（920xxx 转板）的历史年报挂在新三板旧代码下
            have2 = {(r["code"], int(r["year"])) for r in rows}
            pairs = [(c, y) for c in cset for y in targets[c]["years"]
                     if y in all_years and (c, y) not in have2]
            if pairs and not args.no_fill:
                extra2 = plan_by_name(targets, pairs, workers=3)
                if extra2:
                    merged = {(r["code"], int(r["year"])): r for r in rows}
                    for r in extra2:
                        merged[(r["code"], int(r["year"]))] = r
                    rows = [merged[k] for k in sorted(merged)]
                    with open(PLAN, "w", encoding="utf-8-sig", newline="") as f:
                        w = csv.DictWriter(f, fieldnames=PLAN_COLS)
                        w.writeheader()
                        w.writerows(rows)
                    print(f"[规划] 按简称找回 {len(extra2)} 个，清单合计 {len(rows)}\n", flush=True)
        else:
            if os.path.exists(PLAN) and not args.replan:
                rows = load_plan()
            else:
                rows = build_plan(targets, sorted(cset), yfilter, args.api_workers)
            rows = [r for r in rows if r["url"]]

        rows = [r for r in rows if r["url"] and r["code"] in cset
                and (yfilter is None or int(r["year"]) in yfilter)]
        total_mb = sum(int(r.get("size") or 0) for r in rows) / 1024
        print(f"[规划] 待抓取 {len(rows)} 个公司-年度，预计下载 {total_mb/1024:.1f} GB\n")

    if args.plan_only:
        print(f"[规划] 清单已写入 {PLAN}")
        return

    print(f"[抓取] 任务 {len(rows)} 条，进程 {args.procs}\n", flush=True)
    rep = Reporter(len(rows))
    CHUNK = 4
    try:
        with ProcessPoolExecutor(max_workers=args.procs, initializer=_proc_init,
                                 initargs=(OUT_DIR, TMP_DIR, STRICT_BOUNDARY, args.dl_rps)) as ex:
            futs = [ex.submit(_process_chunk, rows[i:i + CHUNK], not args.force)
                    for i in range(0, len(rows), CHUNK)]
            for fu in as_completed(futs):
                for rec in fu.result():
                    rep.add(rec)
    except KeyboardInterrupt:
        print("\n[中断] 已完成的部分已落盘，重新运行同一命令即可续传")
    finally:
        rep.close()
        print(f"\n完成：成功 {rep.ok}，跳过 {rep.exist}，失败 {rep.fail}")
        print(f"输出：{OUT_DIR}")
        if rep.fail:
            print(f"失败清单：{FAILED}  （重新运行同命令会重试失败的条目）")


if __name__ == "__main__":
    main()
