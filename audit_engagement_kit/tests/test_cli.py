# -*- coding: utf-8 -*-
"""CLI 冒烟测试：``python -m audit_kit`` 的 version / help / demo / parse / 未知命令。

全部用 sys.executable 子进程、cwd=仓库根，离线运行，不读写数据库。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from audit_kit import __version__, paths

REPO_ROOT = paths.ROOT
SAMPLE_PDF = REPO_ROOT / "samples" / "pdf" / "000002_1225067818.PDF"


def _run_cli(*args, timeout=600):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONPATH", None)  # 保证从仓库根解析本仓库的包
    return subprocess.run(
        [sys.executable, "-m", "audit_kit", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


class CliTest(unittest.TestCase):
    def assert_ok(self, proc):
        self.assertEqual(
            proc.returncode,
            0,
            f"退出码应为 0\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )

    def test_version(self):
        proc = _run_cli("--version")
        self.assert_ok(proc)
        self.assertEqual(proc.stdout.strip(), f"audit_kit {__version__}")

    def test_help(self):
        proc = _run_cli("--help")
        self.assert_ok(proc)
        out = proc.stdout
        self.assertIn("用法: python -m audit_kit", out)
        self.assertIn("parse <pdf>", out)
        self.assertIn("demo", out)
        self.assertIn("pip install -e .", out)

    def test_no_args_prints_usage_with_exit_code_2(self):
        proc = _run_cli()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("用法: python -m audit_kit", proc.stdout)

    def test_demo_passes_all_samples(self):
        proc = _run_cli("demo")
        self.assert_ok(proc)
        out = proc.stdout
        self.assertIn("样例 3 份，通过 3，失败 0", out)
        self.assertEqual(out.count("PASS "), 3, f"demo 输出:\n{out}")
        self.assertNotIn("FAIL ", out)

    def test_parse_sample_json(self):
        self.assertTrue(SAMPLE_PDF.is_file(), f"缺少样例 PDF: {SAMPLE_PDF}")
        relative = SAMPLE_PDF.relative_to(REPO_ROOT)
        proc = _run_cli("parse", str(relative), "--json")
        self.assert_ok(proc)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["roles"]["项目合伙人"], ["杨誉民"])
        self.assertEqual(payload["roles"]["签字注册会计师"], ["许湘照"])
        self.assertEqual(payload["roles"]["质量控制复核人"], ["黄天义"])
        self.assertEqual(payload["src"], "prose")
        persons = {(p["role"], p["name"]) for p in payload["persons"]}
        self.assertIn(("项目合伙人", "杨誉民"), persons)
        self.assertIn(("签字注册会计师", "许湘照"), persons)

    def test_parse_missing_file_exit_code_2(self):
        proc = _run_cli("parse", str(REPO_ROOT / "samples" / "pdf" / "no_such_file.PDF"), "--json")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("文件不存在", proc.stderr)

    def test_unknown_command_exit_code_2(self):
        proc = _run_cli("bogus-command")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("未知命令", proc.stderr)


if __name__ == "__main__":
    unittest.main()
