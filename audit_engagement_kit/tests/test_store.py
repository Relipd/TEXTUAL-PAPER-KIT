# -*- coding: utf-8 -*-
"""store.build_from_tasks 单元测试。

在临时目录里造一个最小的 ``pipeline.db``（tasks 表 DDL 与 audit_kit/pipeline.py 一致），
再用子进程 + ``AUDIT_KIT_DATA_DIR`` / ``AUDIT_KIT_DB`` / ``AUDIT_KIT_FIN_AUDIT_CSV`` 指向临时目录调用
``build_from_tasks``，断言主库 documents / documents_fts / firm_official。

绝不读写仓库里真实的 data/ 与 output/。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from audit_kit import paths

REPO_ROOT = paths.ROOT

#: 与 audit_kit/pipeline.py 中 tasks 表的 DDL 保持一致（只建测试需要的表）
TASKS_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    code TEXT, name TEXT, title TEXT, date TEXT, disc_year INTEGER,
    type TEXT, url TEXT UNIQUE,
    status TEXT DEFAULT 'new',   -- new/fetched/sliced/parsed/failed
    tries INTEGER DEFAULT 0,
    raw TEXT, pdf TEXT, txt TEXT, pages TEXT,
    a_partner TEXT, a_signer TEXT, a_reviewer TEXT,
    h_partner TEXT, h_signer TEXT, h_reviewer TEXT,
    audit_year INTEGER, firm_notice TEXT, firm_official TEXT,
    prev_firm TEXT, tenure TEXT, fee TEXT,
    src TEXT, note TEXT, ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_st ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_code ON tasks(code, disc_year);
"""

TXT_1 = "万科A 2024年度审计机构为德勤华永会计师事务所（特殊普通合伙）。"
TXT_2 = "贵州茅台 2025年度拟聘任天职国际会计师事务所（特殊普通合伙）。"

FIRM_1 = "德勤华永会计师事务所（特殊普通合伙）"

_TEMP_BASE = "unset"


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


def _write_fin_audit(path, rows):
    """写一个 3 行表头 + 数据行的合成 FIN_Audit.csv（第 1/3/7 列分别被使用）。"""
    lines = ["证券代码,证券简称,会计年度,a,b,c,审计机构名称"] * 3 + list(rows)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


class BuildFromTasksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_tempdir("mda_store_")
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)

        self.data_dir = self.tmp / "data"
        self.data_dir.mkdir()
        self.db_path = self.data_dir / "audit_contracts.db"
        self.pipeline_db = self.data_dir / "pipeline.db"
        self.csv_path = self.tmp / "FIN_Audit.csv"

        txt_dir = self.data_dir / "text"
        txt_dir.mkdir()
        self.txt1 = txt_dir / "000002.txt"
        self.txt2 = txt_dir / "600519.txt"
        self.txt1.write_text(TXT_1, encoding="utf-8")
        self.txt2.write_text(TXT_2, encoding="utf-8")

        conn = sqlite3.connect(str(self.pipeline_db))
        try:
            conn.executescript(TASKS_DDL)
            insert = (
                "INSERT INTO tasks(code,name,title,date,disc_year,type,url,status,txt,audit_year)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)"
            )
            conn.execute(
                insert,
                ("000002", "万科A", "关于拟续聘2024年度会计师事务所的公告",
                 "2024-04-01", 2024, "续聘", "http://example.test/1", "sliced",
                 str(self.txt1), 2024),
            )
            conn.execute(
                insert,
                ("600519", "贵州茅台", "贵州茅台关于变更会计师事务所的公告",
                 "2024-04-03", 2024, "变更", "http://example.test/2", "sliced",
                 str(self.txt2), 2025),
            )
            # 非 sliced/parsed 的任务不应入库
            conn.execute(
                insert,
                ("601398", "工商银行", "关于聘任会计师事务所的公告",
                 "2024-08-07", 2024, "聘任", "http://example.test/3", "new",
                 str(self.txt1), 2024),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ helpers
    def _run_build(self, csv_path=None):
        """在临时目录环境里跑 build_from_tasks，返回 (proc, info)。"""
        env = dict(os.environ)
        env.update(
            {
                "AUDIT_KIT_DATA_DIR": str(self.data_dir),
                "AUDIT_KIT_DB": str(self.db_path),
                "AUDIT_KIT_FIN_AUDIT_CSV": str(csv_path if csv_path is not None else self.csv_path),
                "PYTHONIOENCODING": "utf-8",
            }
        )
        env.pop("AUDIT_KIT_OUTPUT_DIR", None)
        code = (
            "import json\n"
            "from audit_kit import paths, store\n"
            "rc = store.build_from_tasks()\n"
            "print(json.dumps({'rc': rc, 'data': str(paths.DATA), 'db': str(paths.DB),"
            " 'pipeline': str(paths.PIPELINE_DB)}))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"build 子进程失败:\nstdout={proc.stdout}\nstderr={proc.stderr}",
        )
        info = json.loads(proc.stdout.strip().splitlines()[-1])
        return proc, info

    def _read_db(self):
        conn = sqlite3.connect(str(self.db_path))
        try:
            documents = conn.execute(
                "SELECT code, audit_year, firm_official, has_h FROM documents ORDER BY id"
            ).fetchall()
            n_fts = conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
            fts = dict(conn.execute("SELECT code, content FROM documents_fts").fetchall())
            n_persons = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
        finally:
            conn.close()
        return documents, n_fts, fts, n_persons

    def assert_tmp_only(self, info):
        """防止误写真实 data/：所有路径都必须在临时目录内。"""
        tmp_root = str(self.tmp.resolve())
        for key in ("data", "db", "pipeline"):
            with self.subTest(path=key):
                self.assertTrue(
                    str(Path(info[key]).resolve()).startswith(tmp_root),
                    f"{key} 指向了临时目录之外: {info[key]}",
                )
        self.assertNotEqual(
            Path(info["data"]).resolve(),
            (REPO_ROOT / "data").resolve(),
            "AUDIT_KIT_DATA_DIR 覆盖未生效，测试可能写到了真实 data/",
        )

    # ------------------------------------------------------------ tests
    def test_build_with_fin_audit(self):
        _write_fin_audit(self.csv_path, ["000002,万科A,2024,a,b,c," + FIRM_1])
        _proc, info = self._run_build()
        self.assert_tmp_only(info)
        self.assertEqual(info["rc"], 0)
        self.assertEqual(Path(info["db"]), self.db_path.resolve())
        self.assertEqual(Path(info["pipeline"]), self.pipeline_db.resolve())

        documents, n_fts, fts, n_persons = self._read_db()
        self.assertEqual(
            documents,
            [
                ("000002", 2024, FIRM_1, 0),
                ("600519", 2025, "", 0),
            ],
            "documents 行内容不符合预期（含 firm_official / has_h）",
        )
        self.assertEqual(n_fts, 2)
        self.assertEqual(fts["000002"], TXT_1)
        self.assertEqual(fts["600519"], TXT_2)
        self.assertEqual(n_persons, 0, "build 阶段不应写入 persons 表")

    def test_build_without_fin_audit_gives_empty_firm_official(self):
        missing_csv = self.tmp / "missing_FIN_Audit.csv"
        self.assertFalse(missing_csv.exists())
        _proc, info = self._run_build(csv_path=missing_csv)
        self.assert_tmp_only(info)
        self.assertEqual(info["rc"], 0)

        documents, n_fts, _fts, _n_persons = self._read_db()
        self.assertEqual([row[0] for row in documents], ["000002", "600519"])
        self.assertEqual([row[2] for row in documents], ["", ""], "缺少 FIN_Audit 时 firm_official 应为空")
        self.assertEqual(n_fts, 2)

    def test_missing_pipeline_db_returns_2(self):
        self.pipeline_db.unlink()
        proc, info = self._run_build()
        self.assertEqual(info["rc"], 2)
        self.assertIn("找不到采集任务表", proc.stderr)
        self.assertFalse(self.db_path.exists(), "任务表缺失时不应创建主库")


if __name__ == "__main__":
    unittest.main()
