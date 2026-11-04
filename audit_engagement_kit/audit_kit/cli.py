# -*- coding: utf-8 -*-
"""命令行入口：``python -m audit_kit <command>`` 或安装后的 ``audit-kit <command>``。

设计：每个业务子命令都委托给对应模块的 ``main(argv)``，本文件只负责分发、
以及两个不依赖数据库的轻量命令（``parse`` / ``demo``）。

::

    python -m audit_kit --help
    python -m audit_kit parse samples/pdf/000002_1225067818.PDF --json
    python -m audit_kit demo                # 用 samples 验证安装与抽取行为
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, paths

#: 子命令 -> (模块名, main 属性名, 一行说明)
_COMMANDS = {
    "crawl": ("crawl", "main", "采集单家公司的聘任公告并切片（联网）"),
    "plan": ("pipeline", "main", "阶段 1：逐公司检索公告清单（联网）"),
    "fetch": ("pipeline", "main", "阶段 2：并发下载原始 PDF（联网）"),
    "slice": ("pipeline", "main", "阶段 3：截取关键页并导出文本"),
    "status": ("pipeline", "main", "查看采集任务各阶段进度"),
    "import": ("pipeline", "main", "把历史 notices.jsonl 导入任务表"),
    "build": ("store", "main", "由任务表创建 SQLite 主库（建表 + 装基础字段）"),
    "extract": ("reextract", "main", "全量抽取人员与时间节点 → v2_results.jsonl"),
    "apply": ("reextract", "apply_main", "把抽取结果回写主库（自动备份）"),
    "collisions": ("repairs.collisions", "main", "修复同一事项多份公告的切片撞名"),
    "ocr": ("repairs.ocr", "main", "扫描件 OCR 补文字层后重抽（需 ocrmypdf）"),
    "prefer-formal": ("repairs.prefer_formal", "main", "正式公告优先去重 + 跨公告回填时间节点"),
    "firms": ("repairs.firms", "main", "按年报口径重算 firm_official"),
    "export": ("export", "main", "导出 Excel 主表（人员明细 / 公告明细 / 面板 / 统计）"),
    "sample": ("sample", "main", "生成人工核对样本（审计机构聘任公告_核对样本.xlsx）"),
    "gaps": ("diagnostics", "gaps_main", "缺漏盘点（无人员公告分类、角色缺失、节点归因）"),
    "stats": ("diagnostics", "stats_main", "数据指标快照"),
}


def _usage() -> str:
    lines = [
        "audit_kit — 上市公司审计机构聘任公告采集与结构化抽取",
        "",
        "用法: python -m audit_kit <command> [options]",
        "      audit-kit <command> [options]        （pip install -e . 之后）",
        "",
        "核心命令:",
        "  demo                     用 samples/ 验证安装与抽取行为（不联网、不读写数据库）",
        "  parse <pdf>              解析单份公告 PDF，打印人员与时间节点",
        "",
        "流水线命令:",
    ]
    for name, (_, _, desc) in _COMMANDS.items():
        lines.append(f"  {name:<22} {desc}")
    lines += [
        "",
        "示例:",
        "  python -m audit_kit parse samples/pdf/000002_1225067818.PDF --json",
        "  python -m audit_kit demo",
        "  python -m audit_kit plan --workers 3",
        "  python -m audit_kit extract --workers 8",
        "  python -m audit_kit apply",
        "",
        "详细说明见 README.md 与 docs/。",
    ]
    return "\n".join(lines)


def _resolve(command: str):
    """把子命令解析为可调用对象；模块按需导入，缺依赖时报错更友好。"""
    spec = _COMMANDS.get(command)
    if spec is None:
        return None
    module_name, attr, _ = spec
    try:
        module = __import__(f"{__package__}.{module_name}", fromlist=[attr])
    except ImportError as exc:  # 例如没装 ocrmypdf 时的可选依赖
        raise SystemExit(f"[audit_kit] 无法加载 {module_name}: {exc}") from exc
    return getattr(module, attr)


# ---------------------------------------------------------------- parse
def _read_text(pdf: Path) -> str:
    import pymupdf

    with pymupdf.open(str(pdf)) as doc:
        return "\n".join(page.get_text("text") for page in doc)


def _format_parse(result: dict) -> str:
    lines = [f"抽取通道: {result.get('src') or '-'}"
             + (f"　备注: {result['note']}" if result.get("note") else "")]
    persons = {(p["role"], p["name"]): p for p in result.get("persons") or []}
    for role, names in (result.get("roles") or {}).items():
        if not names:
            continue
        lines.append(f"{role}:")
        for name in names:
            p = persons.get((role, name)) or {}
            t = p.get("times") or {}
            flags = []
            if p.get("concurrent"):
                flags.append("兼任")
            if p.get("source"):
                flags.append(f"来源={p['source']}")
            head = f"  - {name}" + (f"（{'，'.join(flags)}）" if flags else "")
            lines.append(head)
            nodes = [f"{k}={t.get(k)}" for k in ("become_cpa", "start_listed",
                                                 "start_firm", "start_company")
                     if t.get(k)]
            if nodes:
                lines.append("    " + "；".join(nodes))
    if len(lines) == 1:
        lines.append("（未识别到项目组成员）")
    return "\n".join(lines)


def cmd_parse(argv) -> int:
    ap = argparse.ArgumentParser(prog="audit-kit parse",
                                 description="解析单份公告 PDF（离线，不建库）")
    ap.add_argument("pdf", help="PDF 路径，例如 samples/pdf/000002_1225067818.PDF")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--max-pages", type=int, default=0, help="最多解析前 N 页（0=全部）")
    ap.add_argument("--title", default="", help="公告标题（提供后同时抽取公告级元字段）")
    ap.add_argument("--disc-year", type=int, default=None, help="披露年（元字段约束用）")
    args = ap.parse_args(argv)

    from . import extract as MX
    from . import meta as MM

    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"[audit_kit] 文件不存在: {pdf}", file=sys.stderr)
        return 2
    result = MX.extract_document(str(pdf), max_pages=args.max_pages or None)
    payload = {
        "file": str(pdf),
        "roles": result["roles"],
        "persons": result["persons"],
        "src": result["src"],
        "note": result["note"],
    }
    if args.title:
        firms = MM.firm_names()
        payload["meta"] = MM.parse_meta(_read_text(pdf), args.title, args.disc_year, firms)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"文件: {pdf}")
        print(_format_parse(result))
        if payload.get("meta"):
            print("公告级字段:")
            for key, value in payload["meta"].items():
                if value:
                    print(f"  {key}: {value}")
    return 0


# ---------------------------------------------------------------- demo
def _diff(expected, actual, prefix="extract"):
    diffs = []

    def walk(exp, act, path):
        if isinstance(exp, dict) and isinstance(act, dict):
            for key in exp:
                walk(exp[key], act.get(key), f"{path}.{key}")
        elif isinstance(exp, list) and isinstance(act, list):
            if exp != act:
                diffs.append(f"{path}: 期望 {exp!r}，实际 {act!r}")
        elif exp != act:
            diffs.append(f"{path}: 期望 {exp!r}，实际 {act!r}")

    walk(expected, actual, prefix)
    return diffs


def cmd_demo(argv) -> int:
    ap = argparse.ArgumentParser(prog="audit-kit demo",
                                 description="用 samples/ 的 3 份样例公告验证抽取行为")
    ap.add_argument("--verbose", action="store_true", help="打印每份样例的抽取结果")
    args = ap.parse_args(argv)

    expected_dir = paths.ROOT / "samples" / "expected"
    files = sorted(expected_dir.glob("*.json"))
    if not files:
        print(f"[audit_kit] 未找到样例黄金结果: {expected_dir}", file=sys.stderr)
        return 2

    from . import extract as MX

    failed = 0
    for golden_file in files:
        golden = json.loads(golden_file.read_text(encoding="utf-8"))
        pdf = paths.ROOT / "samples" / golden["sample"]["file"]
        if not pdf.exists():
            print(f"FAIL {golden_file.stem}: 缺少 PDF {pdf}")
            failed += 1
            continue
        result = MX.extract_document(str(pdf))
        diffs = _diff(golden["extract"], {k: result[k] for k in ("roles", "persons", "src", "note")})
        if diffs:
            failed += 1
            print(f"FAIL {golden_file.stem}")
            for line in diffs:
                print("     " + line)
        else:
            roles = "、".join(f"{k}:{len(v)}" for k, v in result["roles"].items() if v) or "无"
            print(f"PASS {golden_file.stem:<24} src={result['src']:<8} {roles}")
            if args.verbose:
                print(_format_parse(result))
    print(f"\n样例 {len(files)} 份，通过 {len(files) - failed}，失败 {failed}")
    return 1 if failed else 0


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0 if argv else 2
    if argv[0] in ("-V", "--version", "version"):
        print(f"audit_kit {__version__}")
        return 0

    command, rest = argv[0], argv[1:]
    if command == "parse":
        return cmd_parse(rest)
    if command == "demo":
        return cmd_demo(rest)
    if command in ("plan", "fetch", "slice", "status", "import", "run"):
        # pipeline.main 的首个位置参数是子命令名，需原样带上
        rest = [command, *rest]

    func = _resolve(command)
    if func is None:
        print(f"[audit_kit] 未知命令: {command}\n", file=sys.stderr)
        print(_usage(), file=sys.stderr)
        return 2
    result = func(rest)
    return int(result or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
