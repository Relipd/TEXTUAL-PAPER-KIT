# -*- coding: utf-8 -*-
"""paths 模块测试：默认路径布局 + 环境变量覆盖（导入时求值，覆盖用子进程验证）。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from audit_kit import paths

#: 仓库根：<repo>/tests/test_paths.py 的上一级
REPO_ROOT = Path(__file__).resolve().parents[1]

ENV_KEYS = ("AUDIT_KIT_DATA_DIR", "AUDIT_KIT_DB", "AUDIT_KIT_FIN_AUDIT_CSV", "AUDIT_KIT_OUTPUT_DIR")


def _make_tempdir(prefix):
    """在系统临时目录下建一个随机子目录并返回 Path。

    不用 ``tempfile.mkdtemp``：它在 Windows 上以 0700 权限建目录，个别受限
    沙箱（如 DSH 的 workspace-write）会因此拒绝后续写入；这里用默认权限建
    目录，位置仍是 ``tempfile.gettempdir()``，测试结束由 shutil.rmtree 清理。
    """
    base = Path(tempfile.gettempdir())
    while True:
        candidate = base / f"{prefix}{uuid.uuid4().hex[:10]}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate

PATH_FIELDS = (
    "ROOT",
    "DATA",
    "DB",
    "PIPELINE_DB",
    "RAW_DIR",
    "PDF_DIR",
    "TXT_DIR",
    "OCR_DIR",
    "TESSDATA_DIR",
    "V2_RESULTS",
    "FIN_AUDIT_CSV",
    "OUTPUT_DIR",
    "EXPORT_XLSX",
    "SAMPLE_XLSX",
)


def _clean_env(overrides=None):
    env = {key: value for key, value in os.environ.items() if key not in ENV_KEYS}
    env["PYTHONIOENCODING"] = "utf-8"
    if overrides:
        env.update(overrides)
    return env


def _probe_paths(overrides=None, timeout=120):
    """在干净环境里 import 一次 paths，返回所有路径字段的字符串值。"""
    code = (
        "import json\n"
        "from audit_kit import paths\n"
        f"names = {list(PATH_FIELDS)!r}\n"
        "print(json.dumps({n: str(getattr(paths, n)) for n in names}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=_clean_env(overrides),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise AssertionError(f"paths 子进程失败:\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


class DefaultLayoutTest(unittest.TestCase):
    def test_imported_root_is_repo_root(self):
        # 同进程也能验证：ROOT 由文件位置推导，与环境变量无关
        self.assertEqual(Path(paths.ROOT), REPO_ROOT)

    def test_defaults_point_to_repo_root(self):
        info = _probe_paths()
        expected = {
            "ROOT": REPO_ROOT,
            "DATA": REPO_ROOT / "data",
            "DB": REPO_ROOT / "data" / "audit_contracts.db",
            "PIPELINE_DB": REPO_ROOT / "data" / "pipeline.db",
            "RAW_DIR": REPO_ROOT / "data" / "raw",
            "PDF_DIR": REPO_ROOT / "data" / "pdf",
            "TXT_DIR": REPO_ROOT / "data" / "text",
            "OCR_DIR": REPO_ROOT / "data" / "ocr",
            "TESSDATA_DIR": REPO_ROOT / "ocr" / "tessdata",
            "V2_RESULTS": REPO_ROOT / "data" / "v2_results.jsonl",
            "FIN_AUDIT_CSV": REPO_ROOT / "FIN_Audit.csv",
            "OUTPUT_DIR": REPO_ROOT / "output",
            "EXPORT_XLSX": REPO_ROOT / "output" / "审计机构聘任公告_项目成员信息.xlsx",
            "SAMPLE_XLSX": REPO_ROOT / "output" / "审计机构聘任公告_核对样本.xlsx",
        }
        for field, wanted in expected.items():
            with self.subTest(field=field):
                self.assertEqual(Path(info[field]), wanted, f"{field} 默认路径不正确")


class EnvOverrideTest(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_tempdir("mda_paths_")
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)

    def test_all_overrides_take_effect(self):
        data_dir = self.tmp / "my_data"
        db_path = self.tmp / "custom" / "main.db"
        csv_path = self.tmp / "input" / "fin.csv"
        out_dir = self.tmp / "exports"
        info = _probe_paths(
            {
                "AUDIT_KIT_DATA_DIR": str(data_dir),
                "AUDIT_KIT_DB": str(db_path),
                "AUDIT_KIT_FIN_AUDIT_CSV": str(csv_path),
                "AUDIT_KIT_OUTPUT_DIR": str(out_dir),
            }
        )
        self.assertEqual(Path(info["DATA"]), data_dir.resolve())
        self.assertEqual(Path(info["DB"]), db_path.resolve())
        self.assertEqual(Path(info["FIN_AUDIT_CSV"]), csv_path.resolve())
        self.assertEqual(Path(info["OUTPUT_DIR"]), out_dir.resolve())
        self.assertEqual(
            Path(info["EXPORT_XLSX"]),
            out_dir.resolve() / "审计机构聘任公告_项目成员信息.xlsx",
        )
        self.assertEqual(Path(info["SAMPLE_XLSX"]), out_dir.resolve() / "审计机构聘任公告_核对样本.xlsx")

    def test_data_dir_override_propagates_to_children(self):
        data_dir = self.tmp / "only_data"
        info = _probe_paths({"AUDIT_KIT_DATA_DIR": str(data_dir)})
        resolved = data_dir.resolve()
        self.assertEqual(Path(info["DATA"]), resolved)
        self.assertEqual(Path(info["DB"]), resolved / "audit_contracts.db")
        self.assertEqual(Path(info["PIPELINE_DB"]), resolved / "pipeline.db")
        self.assertEqual(Path(info["RAW_DIR"]), resolved / "raw")
        self.assertEqual(Path(info["PDF_DIR"]), resolved / "pdf")
        self.assertEqual(Path(info["TXT_DIR"]), resolved / "text")
        self.assertEqual(Path(info["OCR_DIR"]), resolved / "ocr")
        self.assertEqual(Path(info["V2_RESULTS"]), resolved / "v2_results.jsonl")
        # 未被覆盖的字段仍指向仓库根
        self.assertEqual(Path(info["FIN_AUDIT_CSV"]), REPO_ROOT / "FIN_Audit.csv")
        self.assertEqual(Path(info["OUTPUT_DIR"]), REPO_ROOT / "output")


class HelperTest(unittest.TestCase):
    def test_rel_inside_root(self):
        inside = paths.ROOT / "data" / "demo.db"
        self.assertEqual(Path(paths.rel(inside)), Path("data") / "demo.db")

    def test_rel_outside_root_is_unchanged(self):
        # 系统临时目录一定在仓库之外（只取路径，不写入）
        outside = Path(tempfile.gettempdir())
        self.assertEqual(Path(paths.rel(outside)), outside)

    def test_ensure_creates_nested_dirs(self):
        tmp = _make_tempdir("mda_ensure_")
        self.addCleanup(shutil.rmtree, str(tmp), ignore_errors=True)
        target = tmp / "a" / "b" / "c"
        self.assertIsNone(paths.ensure(target))
        self.assertTrue(target.is_dir())
        self.assertIsNone(paths.ensure())  # 空参数也要安全


if __name__ == "__main__":
    unittest.main()
