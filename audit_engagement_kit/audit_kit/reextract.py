# -*- coding: utf-8 -*-
"""reextract.py — 用 extract / meta 重跑全量抽取并回写主库（由原 reextract_v2.py 迁移）

用法（包内命令）::

    python -m audit_kit extract --workers 8          # 抽取，写 data/v2_results.jsonl
    python -m audit_kit extract --workers 8 --limit 20 --out data/_v2_20.jsonl
    python -m audit_kit apply                        # 备份主库后回写 documents/persons
    python -m audit_kit extract --workers 8 --apply  # 一步到位

``--limit N`` 只抽前 N 份（按 id 升序），用于回归/冒烟测试；``--out`` 指定临时
输出，避免覆盖全量结果文件。默认路径统一来自 :mod:`audit_kit.paths`。
"""
from __future__ import annotations

import argparse, json, os, shutil, sqlite3, time
from concurrent.futures import ProcessPoolExecutor

from . import paths

DB = str(paths.DB)

_firms = None


def _init():
    global _firms
    from . import meta as MM
    from . import extract as MX
    _firms = MM.firm_names()
    # 上市公司简称黑名单：避免把「华天酒店」「宏润建设」这类证券简称当人名
    try:
        from . import crawl as an
        MX.set_co_names(set(an.load_universe().values()))
    except Exception:
        pass


def _work(rec):
    from . import extract as MX
    from . import meta as MM
    did, code, name, disc, typ, date, title, url, pdf, txt, pages = rec
    out = {'id': did, 'roles': {}, 'persons': [], 'src': 'none', 'note': '',
           'meta': {}, 'scores': {}}
    try:
        res = MX.extract_document(pdf)
        out['roles'] = res['roles']
        out['persons'] = res['persons']
        out['src'], out['note'] = res['src'], res['note']
    except Exception as e:
        out['note'] = f'extract:{e}'[:80]
    text = ''
    if txt and os.path.exists(txt):
        try:
            text = open(txt, encoding='utf-8', errors='ignore').read()
        except Exception:
            text = ''
    try:
        out['meta'] = MM.parse_meta(text, title, disc, _firms)
    except Exception as e:
        out['meta'] = {'err': str(e)[:60]}
    return out


def run(workers, limit=0, out=None):
    """全量（或前 limit 份）抽取，结果写 jsonl；out 默认 paths.V2_RESULTS。"""
    out = str(out or paths.V2_RESULTS)
    con = sqlite3.connect(DB)
    cur = con.cursor()
    recs = cur.execute("""select id, code, name, disc_year, type, date, title, url, pdf, txt, pages
                          from documents order by id""").fetchall()
    con.close()
    if limit and limit > 0:
        recs = recs[:limit]
    print(f'待抽取 {len(recs)} 份')
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=workers, initializer=_init) as ex, \
            open(out, 'w', encoding='utf-8') as f:
        for out_rec in ex.map(_work, recs, chunksize=32):
            f.write(json.dumps(out_rec, ensure_ascii=False) + '\n')
            done += 1
            if done % 4000 == 0:
                el = time.time() - t0
                print(f'  {done}/{len(recs)}  {el:.0f}s  {done/el:.0f}/s', flush=True)
    print(f'抽取完成 {done} 份，用时 {time.time()-t0:.0f}s -> {out}')


def apply(db=None, out=None):
    """备份主库后，用抽取结果重建 documents / persons。

    公开版不再保留旧 H 股人员（原库中由历史流程写入的 market='H' 记录），
    因此 has_h 统一写 0、persons 全部为本次抽取的 A 股记录。
    """
    import shutil
    db = str(db or paths.DB)
    out = str(out or paths.V2_RESULTS)
    bak = db.replace('.db', f'_bak_{time.strftime("%Y%m%d_%H%M")}.db')
    if not os.path.exists(bak):
        shutil.copy2(db, bak)
        print('备份 ->', bak)
    con = sqlite3.connect(db)
    cur = con.cursor()
    # persons 表补时间节点列（幂等）
    have = {r[1] for r in cur.execute('PRAGMA table_info(persons)')}
    for col in ('become_cpa', 'start_listed', 'start_firm', 'start_company', 'time_src'):
        if col not in have:
            cur.execute(f'ALTER TABLE persons ADD COLUMN {col} TEXT')
    con.commit()
    old = {r[0]: r for r in cur.execute(
        "select id, code, name, disc_year, audit_year, type, date, title, firm_notice, firm_official,"
        " prev_firm, tenure, fee, url, pdf, txt, pages, src, note, has_h from documents")}

    n_doc = n_p = 0
    cur.execute('DELETE FROM persons')
    cur.execute('DELETE FROM documents')
    for line in open(out, encoding='utf-8'):
        o = json.loads(line)
        did = o['id']
        base = old.get(did)
        if not base:
            continue
        m = o.get('meta') or {}
        roles = o.get('roles') or {}
        a_p = roles.get('项目合伙人', [])
        a_s = roles.get('签字注册会计师', [])
        a_r = roles.get('质量控制复核人', [])
        has_h = 0
        cur.execute(
            """INSERT INTO documents(id, code, name, disc_year, audit_year, type, date, title,
               firm_notice, firm_official, prev_firm, tenure, fee, url, pdf, txt, pages, src, note, has_h)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (did, base[1], base[2], base[3], m.get('audit_year') or None, base[5], base[6], base[7],
             m.get('firm_notice') or None, base[9], m.get('prev_firm') or None,
             m.get('tenure') or None, m.get('fee') or None,
             base[13], base[14], base[15], base[16],
             o.get('src'), o.get('note') or '', has_h))
        tmap = {(p['role'], p['name']): p for p in (o.get('persons') or [])}
        for role, names in (('项目合伙人', a_p), ('签字注册会计师', a_s), ('质量控制复核人', a_r)):
            for nm in names:
                conc = 1 if (role == '签字注册会计师' and nm in a_p) else 0
                p = tmap.get((role, nm)) or {}
                t = p.get('times') or {}
                cur.execute('INSERT INTO persons(doc_id, market, role, name, concurrent,'
                            ' become_cpa, start_listed, start_firm, start_company, time_src)'
                            ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (did, 'A', role, nm, conc,
                             t.get('become_cpa') or None, t.get('start_listed') or None,
                             t.get('start_firm') or None, t.get('start_company') or None,
                             p.get('source') or None))
                n_p += 1
        n_doc += 1
    con.commit()
    print(f'回写完成：{n_doc} 份公告，{n_p} 条人员记录')
    con.close()


def apply_main(argv=None):
    """``python -m audit_kit apply`` 入口：只把已有抽取结果回写主库。"""
    ap = argparse.ArgumentParser(prog='audit_kit apply',
                                 description='把抽取结果 jsonl 回写主库（先自动备份）')
    ap.add_argument('--db', default=None, help='主库路径（默认 paths.DB）')
    ap.add_argument('--out', default=None, help='输入 jsonl（默认 paths.V2_RESULTS）')
    a = ap.parse_args(argv)
    apply(db=a.db, out=a.out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='audit_kit extract',
                                 description='全量重跑抽取（可 --apply 回写主库）')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--limit', type=int, default=0,
                    help='只抽取前 N 份公告（按 id 升序；0=全量，用于回归测试）')
    ap.add_argument('--out', default=None,
                    help='输出 jsonl 路径（默认 paths.V2_RESULTS，即 data/v2_results.jsonl）')
    ap.add_argument('--apply', action='store_true', help='回写主库（先自动备份）')
    ap.add_argument('--skip-extract', action='store_true', help='跳过抽取，只用已有 jsonl 回写')
    a = ap.parse_args(argv)
    if not a.skip_extract:
        run(a.workers, limit=a.limit, out=a.out)
    if a.apply:
        apply(out=a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
