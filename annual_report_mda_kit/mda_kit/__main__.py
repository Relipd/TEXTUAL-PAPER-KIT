# -*- coding: utf-8 -*-
"""Command dispatcher: ``python -m mda_kit <command> [args...]``.

Each command maps to a module that still exposes its own ``main()``; this
wrapper only keeps the command names short and uniform.
"""
from __future__ import annotations

import runpy
import sys

from .config import ensure_dirs

COMMANDS = {
    "download": "fetch_mda",
    "fix-upstream": "fix_upstream",
    "neeq": "neeq_fill",
    "neeq-resume": "neeq_resume",
    "sections": "build_sections_v3",
    "clean": "clean_text_v3",
    "fetch-biz": "fetch_biz",
    "biz-sections": "build_biz_sections",
    "merge": "merge_biz",
    "export": "export_by_year",
    "fts": "rebuild_fts",
    "analyze": "analyze_by_year",
    "resolve": "resolve_missing_biz",
}


def _usage() -> str:
    lines = ["用法: python -m mda_kit <command> [args...]", "", "可用命令:"]
    for name, mod in COMMANDS.items():
        lines.append(f"  {name:<14} -> mda_kit.{mod}")
    lines += [
        "",
        "也可以直接运行模块，例如: python -m mda_kit.fetch_mda --help",
        "配置: cp config.example.toml config.toml 后修改路径；--help 查看各命令参数。",
    ]
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(_usage())
        return 0
    cmd = sys.argv.pop(1)
    if cmd not in COMMANDS:
        print(_usage())
        return 2
    ensure_dirs()
    runpy.run_module(f"mda_kit.{COMMANDS[cmd]}", run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
