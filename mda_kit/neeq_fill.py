# -*- coding: utf-8 -*-
"""
全国股转系统（NEEQ）补取通道

适用对象：920xxx 北交所代码公司。这类公司 2021 年前在新三板挂牌，
年报在股转披露平台，巨潮无存档。

要点：
  - 旧代码由巨潮 orgId 还原（gfbj0 + 六位旧代码）
  - 公告接口 companyAnnouncement.do 必须带 noticeType[]=5、disclosureType[]=5、
    xxfcbj[]=3、siteId=1、needFields[] 数组，缺一即返回空
  - 列表按 xxssdq 排序（非日期），需翻完所有页再按标题筛选
"""
import sys
import os
import re
import csv
import json
import time
import requests
from concurrent.futures import ProcessPoolExecutor, as_completed

from . import fetch_mda as F

from .config import settings

BASE = str(settings.mda_dir)
CACHE = str(settings.neeq_cache)

H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.neeq.com.cn/disclosure/announcement.html",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.neeq.com.cn",
}
API = "https://www.neeq.com.cn/disclosureInfoController/companyAnnouncement.do"
FIELDS = ["companyCd", "companyName", "disclosureTitle", "disclosurePostTitle",
          "destFilePath", "publishDate", "xxfcbj", "fileExt", "xxzrlx"]


def _session():
    s = requests.Session()
    s.trust_env = settings.trust_env
    s.headers.update(H)
    s.get("https://www.neeq.com.cn/disclosure/announcement.html", timeout=30)
    return s


def fetch_all(cd, tries=3):
    """取某公司全部公告（带本地缓存）。返回 list[dict]。"""
    os.makedirs(CACHE, exist_ok=True)
    cache = os.path.join(CACHE, f"{cd}.json")
    if os.path.exists(cache):
        try:
            data = json.load(open(cache, encoding="utf-8"))
            if data:                       # 空文件多为 403 误写的，忽略并重查
                return data
        except Exception:
            pass
    s = _session()
    out, page, total = [], 0, None
    while True:
        cb = "jQuery37105924028546479179_" + str(int(time.time() * 1000))
        body = [("noticeType[]", "5"), ("disclosureType[]", "5"), ("disclosureSubtype[]", ""),
                ("page", str(page)), ("companyCd", cd), ("isNewThree", "1"), ("keyword", ""),
                ("xxfcbj[]", "3"), ("hyType[]", "")]
        body += [("needFields[]", f) for f in FIELDS]
        body += [("siteId", "1"), ("sortfield", "xxssdq"), ("sorttype", "asc")]
        got = None
        for k in range(tries):
            try:
                r = s.post(API, params={"callback": cb}, data=body, timeout=45)
                if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
                    raise IOError(f"HTTP {r.status_code}")
                t = r.text
                j = json.loads(t[t.find("(") + 1:t.rfind(")")])
                got = j[0]["listInfo"]
                break
            except Exception:
                time.sleep(60 * (k + 1))   # 403 时按分钟级退避
                s = _session()
        if got is None:
            break
        items = got.get("content") or []
        out += items
        tp = got.get("totalPages") or 0
        if total is None:
            total = tp
        page += 1
        if page >= tp or not items:
            break
        time.sleep(0.6)
    if out:                                # 只在有数据时落缓存
        json.dump(out, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    return out


def pick(anns, name, years):
    """从公告中挑出各年度年报正文，取披露日期最新的一版。"""
    best = {}
    for a in anns:
        title = a.get("disclosureTitle") or ""
        if "[定期报告]" not in title:
            continue
        if a.get("companyName") and name and name not in a["companyName"] and a["companyName"] not in name:
            continue
        core = re.sub(r"^\[[^\]]*\]", "", title)
        core = re.sub(r"^[^：:]{1,24}[：:]", "", core)
        m = re.search(r"(20\d{2})年年度报告", core)
        if not m:
            continue
        y = int(m.group(1))
        if years and y not in years:
            continue
        if not F._valid_title(core, y):
            continue
        path = a.get("destFilePath") or ""
        if not path:
            continue
        ts = a.get("pubDate") or 0
        if y not in best or ts > best[y]["ts"]:
            best[y] = {"ts": ts, "title": core,
                       "url": "https://www.neeq.com.cn" + path,
                       "size": 0}
    return best


def main(limit=0, workers=1):
    targets = F.load_targets()
    rows = F.read_plan()
    have = {(r["code"], int(r["year"])) for r in rows}
    miss = [(c, y) for c, t in targets.items() for y in t["years"] if (c, y) not in have]
    bj = [k for k in miss if k[0].startswith("920")]
    by_code = {}
    for c, y in bj:
        by_code.setdefault(c, set()).add(y)
    codes = list(by_code)
    if limit:
        codes = codes[:limit]
    print(f"[股转] 待补 {len(bj)} 个公司-年度，涉及 {len(codes)} 家", flush=True)

    om = F.org_map()
    extra, lock = [], __import__("threading").Lock()
    t0 = time.time()

    def work(code):
        org = om.get(code) or ""
        if not org.startswith("gfbj0"):
            return
        old = org[5:]
        name = targets[code]["name"]
        try:
            anns = fetch_all(old)
        except Exception as e:
            print(f"   查询失败 {code}->{old} {type(e).__name__}", flush=True)
            return
        time.sleep(2.0)                       # 降低对股转站的压力
        picked = pick(anns, name, by_code[code])
        loc = [{"code": code, "name": name, "year": y,
                "url": picked[y]["url"], "title": picked[y]["title"],
                "ts": picked[y]["ts"], "size": 0, "alts": ""} for y in sorted(picked)]
        if loc:
            with lock:
                extra.extend(loc)
                print(f"   {code} {name} 旧码{old}: {len(anns)} 条公告，命中 {len(loc)} 个年度", flush=True)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, c) for c in codes]
        for i, fu in enumerate(as_completed(futs), 1):
            fu.result()
            if i % 40 == 0:
                print(f"   ...{i}/{len(codes)} 家，已命中 {len(extra)} 个年度，{time.time()-t0:.0f}s", flush=True)

    print(f"[股转] 命中 {len(extra)} 个公司-年度", flush=True)
    if not extra:
        return

    merged = {(r["code"], int(r["year"])): r for r in rows}
    for r in extra:
        merged[(r["code"], int(r["year"]))] = r
    rows = [merged[k] for k in sorted(merged)]
    with open(F.PLAN, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=F.PLAN_COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"[股转] 清单更新为 {len(rows)} 条，开始下载解析 …", flush=True)

    ok = fail = exist = 0
    res = []
    with ProcessPoolExecutor(max_workers=6, initializer=F._proc_init,
                             initargs=(F.OUT_DIR, F.TMP_DIR, F.STRICT_BOUNDARY, 1.5)) as ex:
        futs = [ex.submit(F._process_chunk, extra[i:i + 4], True) for i in range(0, len(extra), 4)]
        for fu in as_completed(futs):
            for rec in fu.result():
                res.append(rec)
                if rec["status"] == "OK":
                    ok += 1
                elif rec["status"] == "已存在":
                    exist += 1
                else:
                    fail += 1
    print(f"[股转] 下载结果: 成功 {ok} 已存在 {exist} 失败 {fail}", flush=True)
    with open(F.MANIFEST, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "name", "year", "status", "pages",
                                          "start", "end", "method", "url"])
        for r in res:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    main(limit=lim)
