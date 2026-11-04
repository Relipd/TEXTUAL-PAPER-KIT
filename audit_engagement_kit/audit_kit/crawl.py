# -*- coding: utf-8 -*-
"""巨潮资讯（CNINFO）公告采集：会话与节流、检索、分类、下载、关键页切片。

数据源：巨潮资讯网（cninfo）。本模块只负责采集与切片，公告文本的字段抽取
在 :mod:`audit_kit.extract` 中完成。

用法::

    python -m audit_kit crawl 000002                   # 单公司
    python -m audit_kit crawl 000002,600000 --limit 5  # 多家公司
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import threading
import time
from datetime import date
from pathlib import Path

import pymupdf
import requests

from . import config as cfg
from . import paths

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
BASE = 'http://www.cninfo.com.cn'
PDFBASE = 'http://static.cninfo.com.cn'
_local = threading.local()
_org = None
_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_req = [0.0]
MIN_INTERVAL = cfg.MIN_REQUEST_INTERVAL   # 检索接口全局限速（秒），QPS ≈ 2
_ban = {'until': 0.0}


def sess():
    s = getattr(_local, 's', None)
    if s is None:
        s = requests.Session()
        s.headers.update({
            'User-Agent': UA,
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
        })
        try:
            s.get(f'{BASE}/new/index', timeout=30)
        except Exception:
            pass
        s.headers.update({
            'Referer': f'{BASE}/new/commonUrl?url=disclosure/list/notice',
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': BASE,
        })
        _local.s = s
    return s


def throttle():
    """全局限速 + 封禁熔断"""
    while True:
        with _rate_lock:
            now = time.time()
            if now < _ban['until']:
                wait = _ban['until'] - now
            else:
                gap = now - _last_req[0]
                if gap >= MIN_INTERVAL:
                    _last_req[0] = now
                    return
                wait = MIN_INTERVAL - gap
        time.sleep(min(wait, 5))


def trip_ban(seconds=90):
    with _rate_lock:
        _ban['until'] = max(_ban['until'], time.time() + seconds)


_co_names = None


def company_names():
    """上市公司简称集合（用于抽取时排除证券简称被误判为人名）。"""
    global _co_names
    if _co_names is None:
        try:
            _co_names = set(load_universe().values())
        except Exception:
            _co_names = set()
    return _co_names


# ---------------- 检索 ----------------
def org_map():
    global _org
    with _lock:
        if _org is None:
            lst = sess().get(f'{BASE}/new/data/szse_stock.json', timeout=60).json()['stockList']
            _org = {x['code']: x['orgId'] for x in lst}
        return _org


def load_universe(csv_path=None):
    """读取 FIN_Audit 名单：``{证券代码: 证券简称}``。

    默认读 :data:`audit_kit.paths.FIN_AUDIT_CSV`；该文件为第三方数据，不随仓库
    分发，缺失时抛出带说明的 FileNotFoundError（命令行入口会打印成中文错误）。
    """
    csv_path = Path(csv_path) if csv_path is not None else paths.FIN_AUDIT_CSV
    if not csv_path.exists():
        raise FileNotFoundError(
            f'找不到审计名单 CSV：{csv_path}\n'
            '该文件为第三方数据（CSMAR 审计意见表），不随仓库分发；'
            '请把 CSV 放到仓库根目录，或用环境变量 AUDIT_KIT_FIN_AUDIT_CSV 指向它。')
    seen = {}
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        rd = csv.reader(f)
        next(rd); next(rd); next(rd)
        for r in rd:
            if r and r[0] and r[0] not in seen:
                seen[r[0]] = r[1]
    return seen


EXCLUDE = r'(履职|评估报告|监督职责|专项说明|问询|回复|审核意见|议案|工作报告|鉴证|内部控制审计报告|财务报表审计报告|董事会决议|股东大会决议|股东会决议|法律意见|独立董事|事前认可|审计委员会|会计准则|会计政策|会计估计|控股股东)'


def classify(title):
    t = title.replace(' ', '')
    if re.search(EXCLUDE, t):
        return '其他'
    if not re.search(r'(会计师事务所|审计机构|审计师|会计师)', t):
        return '其他'
    if not re.search(r'(续聘|变更|改聘|聘任|聘请|更换|解聘|新聘|首次)', t):
        return '其他'
    if re.search(r'(复核人|签字会计师|签字注册会计师)', t) and not re.search(r'(续聘|变更会计师事务所|聘任会计师事务所)', t):
        return '其他'
    if re.search(r'(变更|改聘|更换)', t):
        return '变更'
    if '续聘' in t:
        return '续聘'
    if re.search(r'(聘任|聘请|新聘|首次)', t):
        return '聘任'
    return '其他'


def fetch_notices(code, start=cfg.START_DATE, end=None, retries=3,
                  kws=cfg.KEYWORDS):
    end = end or date.today().isoformat()
    org = org_map().get(code)
    if not org:
        return []
    if code[0] == '6':
        col, plate = 'sse', 'sh'
    elif code[0] in '89':          # 北交所 / 新三板：需空 plate
        col, plate = 'szse', ''
    else:
        col, plate = 'szse', 'sz'
    raw = []
    for kw in kws:
        page = 1
        while page <= 3:
            d = {'pageNum': page, 'pageSize': 30, 'column': col, 'tabName': 'fulltext',
                 'plate': plate, 'stock': f'{code},{org}', 'searchkey': kw,
                 'secid': '', 'category': '', 'trade': '', 'seDate': f'{start}~{end}',
                 'sortName': '', 'sortType': '', 'isHLtitle': 'true'}
            j = None
            banned = False
            for attempt in range(retries):
                throttle()
                try:
                    r = sess().post(f'{BASE}/new/hisAnnouncement/query', data=d, timeout=30)
                    if r.status_code == 403:
                        banned = True
                        trip_ban(120 + attempt * 120)
                        time.sleep(120 + attempt * 120)
                        continue
                    j = r.json()
                    banned = False
                    break
                except Exception:
                    time.sleep(1.5)
            if banned:
                raise RuntimeError('http403')
            if not j:
                break
            raw.extend(j.get('announcements') or [])
            if not j.get('hasMore'):
                break
            page += 1
            time.sleep(0.1)
        time.sleep(0.05)
    seen, res = set(), []
    for a in raw:
        if a['announcementId'] in seen:
            continue
        seen.add(a['announcementId'])
        t = re.sub('<[^>]+>', '', a['announcementTitle']).strip()
        res.append({'code': code, 'title': t,
                    'date': time.strftime('%Y-%m-%d', time.localtime(a['announcementTime'] / 1000)),
                    'url': f"{PDFBASE}/{a['adjunctUrl']}", 'aid': a['announcementId']})
    res.sort(key=lambda x: x['date'])
    return res


# ---------------- 路径与切片 ----------------
KEY_HINTS = ('项目合伙人', '签字注册会计师', '质量控制复核人', '项目质量复核人',
             '复核合伙人', '项目组成员')


def data_root():
    """数据根目录（``data/``）。"""
    paths.ensure(paths.DATA)
    return str(paths.DATA)


def cache_dir():
    """原始 PDF 缓存目录（``data/raw/``）。"""
    paths.ensure(paths.RAW_DIR)
    return str(paths.RAW_DIR)


def out_dir(kind, year):
    """切片输出目录（``data/<kind>/<year>/``）。"""
    d = paths.DATA / str(kind) / str(year)
    paths.ensure(d)
    return str(d)


def safe_filename(s, maxlen=90):
    """去除 Windows 非法字符与空白，限制长度"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', '', s or '')
    s = re.sub(r'\s+', '', s)
    return s[:maxlen].strip('. ')


def slice_key_pages(src, out_pdf, out_txt):
    """截取含项目成员信息的页面，另存 PDF 并导出对应文本

    返回 (页码列表, 是否整篇保留)
    """
    doc = pymupdf.open(src)
    hits = [i for i in range(len(doc))
            if any(k in doc[i].get_text() for k in KEY_HINTS)]
    whole = False
    if not hits:
        pages = list(range(len(doc)))
        whole = True
    else:
        keep = set(hits)
        for i in hits:
            if i > 0:
                keep.add(i - 1)          # 带上「(二)项目信息」标题页
        pages = sorted(keep)
    nd = pymupdf.open()
    for i in pages:
        nd.insert_pdf(doc, from_page=i, to_page=i)
    nd.save(out_pdf, garbage=3, deflate=True)
    nd.close()
    txt = '\n'.join(doc[i].get_text() for i in pages)
    with open(out_txt, 'w', encoding='utf-8') as f:
        f.write(txt)
    doc.close()
    return pages, whole


def _download(url, raw):
    """把单份公告 PDF 下载到 ``raw``（已存在且非空则跳过），返回是否可用。"""
    if raw.exists() and raw.stat().st_size > 2000:
        return True
    try:
        r = sess().get(url, timeout=90)
        if len(r.content) > 2000 and r.content[:4] == b'%PDF':
            raw.parent.mkdir(parents=True, exist_ok=True)
            with open(raw, 'wb') as f:
                f.write(r.content)
            return True
    except Exception:
        pass
    return False


# ---------------- 单公司采集入口 ----------------
def main(argv=None) -> int:
    """采集单家（或多家）公司的聘任类公告并截取关键页，产物落在 ``data/`` 下。"""
    ap = argparse.ArgumentParser(
        prog='audit_kit crawl',
        description='采集公司聘任类公告（续聘/变更/聘任）并截取含项目信息的页面')
    ap.add_argument('codes', nargs='+', metavar='CODE',
                    help='股票代码，可多个；也支持逗号分隔，如 000002,600000')
    ap.add_argument('--start', default=cfg.START_DATE,
                    help=f'检索起始日（默认 {cfg.START_DATE}）')
    ap.add_argument('--end', default=None,
                    help='检索截止日（默认至当天）')
    ap.add_argument('--limit', type=int, default=0, help='每家公司最多处理 N 条公告（0=不限）')
    args = ap.parse_args(argv)

    try:
        universe = load_universe()
    except FileNotFoundError as e:
        print(f'错误：{e}', file=sys.stderr)
        return 2

    codes = [c for arg in args.codes for c in re.split(r'[,，\s]+', arg) if c]
    paths.ensure(paths.RAW_DIR, paths.PDF_DIR, paths.TXT_DIR)
    t0 = time.time()
    n_notice = n_raw = n_slice = 0
    errors = 0
    for code in codes:
        name = universe.get(code, code)
        co = re.sub(r'\s+', '', name or code)
        try:
            notices = fetch_notices(code, args.start, args.end)
        except Exception as e:
            print(f'错误：{code} 检索失败：{e}', file=sys.stderr)
            errors += 1
            continue
        notices = [n for n in notices if classify(n['title']) != '其他']
        if args.limit:
            notices = notices[:args.limit]
        print(f'[{code}] {co}：候选公告 {len(notices)} 条', flush=True)
        for i, n in enumerate(notices, 1):
            y = n['date'][:4]
            typ = classify(n['title'])
            raw = Path(cache_dir()) / f"{code}_{y}_{n['aid']}.pdf"
            ok = _download(n['url'], raw)
            n_notice += 1
            if not ok:
                print(f'  [{i}/{len(notices)}] {n["date"]} {typ} 下载失败：{n["title"]}',
                      file=sys.stderr, flush=True)
                errors += 1
                continue
            n_raw += 1
            stem = safe_filename(f"{co}-{code}-{y}-{n['title']}")
            out_pdf = Path(out_dir('pdf', y)) / (stem + '.pdf')
            out_txt = Path(out_dir('text', y)) / (stem + '.txt')
            try:
                if out_pdf.exists() and out_txt.exists():
                    with pymupdf.open(str(out_pdf)) as d:
                        pages = list(range(len(d)))
                    note = f'{len(pages)}页（切片已存在）'
                else:
                    paths.ensure(out_pdf.parent, out_txt.parent)
                    pages, whole = slice_key_pages(str(raw), str(out_pdf), str(out_txt))
                    note = f'{len(pages)}页' + ('(整篇)' if whole else '')
                    n_slice += 1
            except Exception as e:
                print(f'  [{i}/{len(notices)}] 切片失败：{e}', file=sys.stderr, flush=True)
                errors += 1
                continue
            print(f'  [{i}/{len(notices)}] {n["date"]} {typ} {note}  {n["title"]}', flush=True)
            print(f'      -> {paths.rel(out_pdf)}', flush=True)
    print(f'完成：{len(codes)} 家公司，公告 {n_notice} 条，可下载 {n_raw} 条，'
          f'新切片 {n_slice} 条，用时 {time.time() - t0:.0f}s')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
