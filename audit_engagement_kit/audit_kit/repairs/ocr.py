# -*- coding: utf-8 -*-
"""ocr — 用 OCRmyPDF 给扫描件补文字层，再跑同一套版面抽取

适用对象（只处理本地已有的 PDF，不外抓）：
  1. src='scan'：整份无文字层；
  2. 无人员记录且文本几乎为空（<50 字）的公告。

流程：
  候选 -> ocrmypdf(-l chi_sim+eng --skip-text --output-type pdf --optimize 0)
       -> data/ocr/{年份}/同名.pdf
       -> audit_kit.extract.extract_document 重新抽取
       -> data/ocr_results.jsonl
       -> （可选 --apply）回写 documents / persons，src 记为 ocr

用法：
  python -m audit_kit repairs ocr --limit 6   # 先小样本试跑
  python -m audit_kit repairs ocr             # 全量候选
  python -m audit_kit repairs ocr --apply     # OCR 后直接回写主库
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

from .. import paths

DB = paths.DB
OCR_DIR = paths.OCR_DIR
OUT = paths.DATA / 'ocr_results.jsonl'
TESSDATA = paths.TESSDATA_DIR
PY = sys.executable


# 只对「聘任类公告及配套陈述/承诺函」做 OCR：审计报告、年报、招股书不在口径内
TITLE_OK = re.compile(r'(续聘|聘任|聘请|变更|改聘|更换|解聘).{0,30}(会计师事务所|审计机构|审计师|会计师)'
                      r'|(会计师事务所|审计机构).{0,30}(续聘|聘任|变更|改聘)')
TITLE_BAD = re.compile(r'(财务报表及审计报告|审计报告及财务报表|年度报告|招股说明书|募集说明书|'
                       r'内部控制审计报告|半年度报告|季度报告)')
MAX_PAGES = 12


def page_count(pdf):
    try:
        import pymupdf
        d = pymupdf.open(pdf)
        n = len(d)
        d.close()
        return n
    except Exception:
        return -1


def candidates():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    rows = list(cur.execute("""select id, code, disc_year, title, pdf, txt, src from documents
                               where src='scan' order by id"""))
    ids = {r[0] for r in rows}
    for did, code, dy, title, pdf, txt, src in cur.execute(
            """select id, code, disc_year, title, pdf, txt, src from documents d
               where not exists (select 1 from persons p where p.doc_id=d.id and p.market='A')
               order by id"""):
        if did in ids or not pdf or not os.path.exists(pdf):
            continue
        L = 0
        if txt and os.path.exists(txt):
            try:
                L = len(open(txt, encoding='utf-8', errors='ignore').read().strip())
            except Exception:
                L = 0
        if L < 50:
            rows.append((did, code, dy, title, pdf, txt, src))
    con.close()
    out = []
    for r in rows:
        t = r[3] or ''
        if TITLE_BAD.search(t):
            continue
        if not TITLE_OK.search(t):
            continue
        n = page_count(r[4])
        if n < 0 or n > 60:          # 超长文档不是聘任公告
            continue
        out.append(r)
    return out


def head_pdf(src, dst, max_pages=MAX_PAGES):
    """只取前 N 页送 OCR（项目信息表在公告前段），控制耗时"""
    import pymupdf
    d = pymupdf.open(src)
    if len(d) <= max_pages:
        d.close()
        return src
    nd = pymupdf.open()
    nd.insert_pdf(d, from_page=0, to_page=max_pages - 1)
    nd.save(dst, garbage=3, deflate=True)
    nd.close()
    d.close()
    return dst


def ocr_one(src, dst, jobs=4, timeout=900):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    env = dict(os.environ)
    # 项目内自带语言包时优先使用；没有则沿用系统 TESSDATA 默认设置
    if TESSDATA.is_dir():
        env['TESSDATA_PREFIX'] = str(TESSDATA)
    tmp_head = dst + '.head.pdf'
    feed = head_pdf(src, tmp_head)
    cmd = [PY, '-m', 'ocrmypdf', '-l', 'chi_sim+eng', '--skip-text',
           '--output-type', 'pdf', '--optimize', '0', '--jobs', str(jobs),
           '--quiet', feed, dst]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        rc, err = p.returncode, (p.stderr or '')[-300:]
    except subprocess.TimeoutExpired:
        rc, err = 99, 'timeout'
    finally:
        if feed != src and os.path.exists(tmp_head):
            try:
                os.remove(tmp_head)
            except Exception:
                pass
    return rc, time.time() - t0, err


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='audit_kit repairs ocr',
        description='用 OCRmyPDF 给扫描件补文字层并重新抽取（--apply 时写主库）')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args(argv)

    import importlib.util
    if importlib.util.find_spec('ocrmypdf') is None:
        print('未检测到 ocrmypdf：本修复依赖它给扫描件补文字层。')
        print('请先安装（pip install ocrmypdf），并确认本机已安装 Tesseract 及 chi_sim 语言包。')
        return 1

    from .. import extract as MX
    from .. import meta as MM
    firms = MM.firm_names()

    cand = candidates()
    print(f'候选 {len(cand)} 份')
    if a.limit:
        cand = cand[:a.limit]

    ok = fail = recovered = 0
    recs = []
    t0 = time.time()
    for i, (did, code, dy, title, pdf, txt, src) in enumerate(cand, 1):
        rel = os.path.relpath(pdf, str(paths.PDF_DIR))
        dst = os.path.join(str(OCR_DIR), rel)
        if not (os.path.exists(dst) and os.path.getsize(dst) > 1000):
            rc, sec, err = ocr_one(pdf, dst, a.jobs)
            if rc != 0 or not os.path.exists(dst):
                fail += 1
                print(f'  [{i}/{len(cand)}] OCR 失败 {did}: {err[-120:]}')
                continue
        ok += 1
        try:
            res = MX.extract_document(dst)
        except Exception as e:
            print(f'  [{i}/{len(cand)}] 抽取失败 {did}: {e}')
            continue
        text = ''
        try:
            import pymupdf
            d = pymupdf.open(dst)
            text = '\n'.join(pg.get_text() for pg in d)
            d.close()
        except Exception:
            pass
        meta = MM.parse_meta(text, title, dy, firms)
        n_p = len(res['persons'])
        if n_p:
            recovered += 1
        recs.append({'id': did, 'src': 'ocr', 'persons': res['persons'],
                     'roles': res['roles'], 'meta': meta, 'ocr_chars': len(text),
                     'ocr_pdf': dst})
        if i % 10 == 0 or i == len(cand):
            print(f'  [{i}/{len(cand)}] 成功 {ok} 失败 {fail} 抽到人员 {recovered} '
                  f'用时 {time.time()-t0:.0f}s', flush=True)

    with open(OUT, 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'OCR 完成：处理 {len(cand)} 份，成功 {ok}，失败 {fail}，'
          f'其中抽到人员 {recovered} 份 -> {OUT}')

    if a.apply:
        apply(recs)
    return 0


def apply(recs):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    n_doc = n_p = 0
    for r in recs:
        did = r['id']
        if not r['persons']:
            continue
        m = r.get('meta') or {}
        cur.execute("update documents set src='ocr', audit_year=coalesce(?,audit_year),"
                    " firm_notice=coalesce(?,firm_notice), fee=coalesce(?,fee) where id=?",
                    (m.get('audit_year') or None, m.get('firm_notice') or None,
                     m.get('fee') or None, did))
        cur.execute("delete from persons where doc_id=?", (did,))
        p_role = {}
        for p in r['persons']:
            p_role.setdefault(p['role'], []).append(p['name'])
        partner = p_role.get('项目合伙人', [])
        for p in r['persons']:
            t = p.get('times') or {}
            conc = 1 if (p['role'] == '签字注册会计师' and p['name'] in partner) else 0
            cur.execute('insert into persons(doc_id,market,role,name,concurrent,'
                        'become_cpa,start_listed,start_firm,start_company,time_src)'
                        ' values(?,?,?,?,?,?,?,?,?,?)',
                        (did, 'A', p['role'], p['name'], conc,
                         t.get('become_cpa') or None, t.get('start_listed') or None,
                         t.get('start_firm') or None, t.get('start_company') or None,
                         p.get('source') or None))
            n_p += 1
        n_doc += 1
    con.commit()
    print(f'回写 OCR 结果：{n_doc} 份公告，{n_p} 条人员记录')
    con.close()
    return 0
