# -*- coding: utf-8 -*-
"""
股转下载的自动续传：等待 403 解除后，以极低速率补完剩余条目。

策略：
  1. 每 5 分钟用一次 PDF 探测是否解封（单请求，不构成压力）
  2. 解封后单线程下载，每条间隔 3 秒；一旦再遇 403 立即回到等待状态
  3. 断点续传：已存在的文件跳过
"""
import sys
import os
import re
import csv
import time
import requests

from . import fetch_mda as F

from .config import settings

BASE = str(settings.mda_dir)
COOLDOWN = 300          # 封禁后的等待秒数
GAP = 3.0               # 每条之间的间隔
PROBE_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.neeq.com.cn/"}


def pending():
    have = set()
    for fn in os.listdir(BASE):
        m = re.match(r"(\d{4})-(\d{6})-", fn)
        if m:
            have.add((m.group(2), int(m.group(1))))
    return [r for r in F.read_plan()
            if "neeq.com.cn" in (r["url"] or "") and (r["code"], int(r["year"])) not in have]


def alive(url):
    try:
        r = requests.get(url, headers=PROBE_UA, timeout=30, stream=True)
        ok = r.status_code == 200
        r.close()
        return ok
    except Exception:
        return False


def main():
    while True:
        todo = pending()
        print(f"[续传] 剩余 {len(todo)} 条", flush=True)
        if not todo:
            print("[续传] 全部完成", flush=True)
            return
        if not alive(todo[0]["url"]):
            print(f"[续传] 仍被限流，等待 {COOLDOWN}s 后再试", flush=True)
            time.sleep(COOLDOWN)
            continue

        ok = fail = 0
        for i, r in enumerate(todo, 1):
            rec = F.process_row(r, skip_existing=True)
            if rec["status"] == "OK":
                ok += 1
            elif rec["status"] == "已存在":
                pass
            else:
                fail += 1
                if "403" in (rec.get("method") or "") or rec["status"].startswith("异常"):
                    print(f"[续传] 第 {i} 条疑似再次被限流，暂停 5 分钟", flush=True)
                    time.sleep(COOLDOWN)
                    if not alive(r["url"]):
                        break
            with open(F.MANIFEST, "a", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["code", "name", "year", "status",
                                                  "pages", "start", "end", "method", "url"])
                w.writerow({k: rec.get(k, "") for k in w.fieldnames})
            if i % 10 == 0:
                print(f"[续传] {i}/{len(todo)} 成功 {ok} 失败 {fail}", flush=True)
            time.sleep(GAP)
        print(f"[续传] 本轮结束：成功 {ok} 失败 {fail}", flush=True)


if __name__ == "__main__":
    main()
