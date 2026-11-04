# -*- coding: utf-8 -*-
"""collisions — 修复同名公告共用同一 PDF 切片的问题（"拿错文件"）

背景：切片文件名由「公司-代码-年份-标题」生成。同一公司同一年同标题的两份公告
（如原公告与更正版、承诺函）会撞名，后一份公告的 pdf/txt 指向了前一份的切片，
导致抽取结果张冠李戴或为空。

处理：对每个撞名的公告，按其 announcementId 从 data/raw 取原始 PDF 重新切片，
文件名追加 -{aid} 保证唯一，然后重新抽取并回写人员与元字段。
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3

from .. import crawl as an
from .. import extract as MX
from .. import meta as MM
from .. import paths


def stem_of(pdf):
    return os.path.splitext(os.path.basename(pdf))[0]


def main(argv=None):
    argparse.ArgumentParser(
        prog='audit_kit repairs collisions',
        description='按 announcementId 重建撞名公告的切片并重新抽取回写（写主库）').parse_args(argv)

    con = sqlite3.connect(paths.DB)
    cur = con.cursor()
    groups = cur.execute("""select pdf, group_concat(id) from documents
                            where pdf is not null group by pdf having count(*)>1""").fetchall()
    print(f'撞名组 {len(groups)}')
    firms = MM.firm_names()
    fixed = skipped = n_person = 0
    for pdf, ids in groups:
        for did in [int(x) for x in ids.split(',')]:
            code, dy, url, title, txt = cur.execute(
                'select code, disc_year, url, title, txt from documents where id=?', (did,)).fetchone()
            m = re.search(r'/(\d+)\.PDF$', url or '', re.I)
            aid = m.group(1) if m else ''
            rp = os.path.join(str(paths.RAW_DIR), f'{code}_{dy}_{aid}.pdf') if aid else ''
            if not (rp and os.path.exists(rp)):
                skipped += 1
                continue
            year_dir = os.path.dirname(pdf)
            new_pdf = os.path.join(year_dir, f'{stem_of(pdf)}-{aid}.pdf')
            new_txt = new_pdf[:-4] + '.txt'
            try:
                if not os.path.exists(new_pdf):
                    an.slice_key_pages(rp, new_pdf, new_txt)
            except Exception as e:
                print('  切片失败', did, e)
                skipped += 1
                continue
            try:
                res = MX.extract_document(new_pdf)
            except Exception as e:
                print('  抽取失败', did, e)
                skipped += 1
                continue
            body = ''
            try:
                body = open(new_txt, encoding='utf-8', errors='ignore').read()
            except Exception:
                pass
            meta = MM.parse_meta(body, title, dy, firms)
            cur.execute('update documents set pdf=?, txt=?, audit_year=?, firm_notice=?, '
                        'prev_firm=?, tenure=?, fee=?, src=? where id=?',
                        (new_pdf, new_txt, meta['audit_year'] or None, meta['firm_notice'] or None,
                         meta['prev_firm'] or None, meta['tenure'] or None, meta['fee'] or None,
                         res['src'], did))
            cur.execute('delete from persons where doc_id=?', (did,))
            partner = [p['name'] for p in res['persons'] if p['role'] == '项目合伙人']
            for p in res['persons']:
                t = p.get('times') or {}
                conc = 1 if (p['role'] == '签字注册会计师' and p['name'] in partner) else 0
                cur.execute('insert into persons(doc_id,market,role,name,concurrent,'
                            'become_cpa,start_listed,start_firm,start_company,time_src)'
                            ' values(?,?,?,?,?,?,?,?,?,?)',
                            (did, 'A', p['role'], p['name'], conc,
                             t.get('become_cpa') or None, t.get('start_listed') or None,
                             t.get('start_firm') or None, t.get('start_company') or None,
                             p.get('source') or None))
                n_person += 1
            fixed += 1
    con.commit()
    left = cur.execute('select count(*) from (select pdf from documents where pdf is not null '
                       'group by pdf having count(*)>1)').fetchone()[0]
    print(f'修复 {fixed} 份，跳过 {skipped}，写入人员 {n_person}，剩余撞名组 {left}')
    con.close()
    return 0
