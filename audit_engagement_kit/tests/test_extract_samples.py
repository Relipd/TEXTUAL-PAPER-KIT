# -*- coding: utf-8 -*-
"""黄金样例回归测试：samples/pdf 的 3 份公告 -> 与 samples/expected/*.json 逐字段比对。

- 只依赖随仓库分发的样例文件与 pymupdf，离线运行，不读 data/、不读 FIN_Audit.csv；
- ``extract`` 部分比对 roles / persons / src / note（persons 逐字段）；
- ``meta`` 部分比对 audit_year / firm_notice / prev_firm / tenure / fee，
  机构名录用黄金 meta 里的机构名内联（避免依赖第三方 FIN_Audit.csv），
  601398 的公告正文从 PDF 现场读取。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from audit_kit import extract as MX
from audit_kit import meta as MM
from audit_kit import paths

SAMPLES = paths.ROOT / "samples"
EXPECTED_DIR = SAMPLES / "expected"
PDF_DIR = SAMPLES / "pdf"

#: 公告级字段（与黄金 JSON 的 meta 对齐）
META_FIELDS = ("audit_year", "firm_notice", "prev_firm", "tenure", "fee")


def _read_pdf_text(pdf_path):
    """现场读取 PDF 文字层（601398 的 meta 必须用 PDF 原文，不能用黄金回填）。"""
    import pymupdf

    with pymupdf.open(str(pdf_path)) as doc:
        return "\n".join(page.get_text("text") for page in doc)


def _diff(expected, actual, prefix="extract"):
    """逐字段递归比对，返回人类可读的差异列表（空表示一致）。"""
    diffs = []

    def walk(exp, act, path):
        if isinstance(exp, dict) and isinstance(act, dict):
            for key in exp:
                if key not in act:
                    diffs.append(f"{path}.{key}: 期望 {exp[key]!r}，实际缺失")
                else:
                    walk(exp[key], act[key], f"{path}.{key}")
            for key in act:
                if key not in exp:
                    diffs.append(f"{path}.{key}: 实际多出字段 {act[key]!r}")
        elif isinstance(exp, list) and isinstance(act, list):
            if len(exp) != len(act):
                diffs.append(f"{path}: 长度 期望 {len(exp)}，实际 {len(act)}")
            for i in range(min(len(exp), len(act))):
                walk(exp[i], act[i], f"{path}[{i}]")
        elif exp != act:
            diffs.append(f"{path}: 期望 {exp!r}，实际 {act!r}")

    walk(expected, actual, prefix)
    return diffs


class GoldenSamplesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = [
            (path, json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(EXPECTED_DIR.glob("*.json"))
        ]

    def test_three_samples_present(self):
        self.assertEqual(len(self.golden), 3, f"samples/expected 应有 3 份黄金结果: {EXPECTED_DIR}")
        pdfs = [p for p in PDF_DIR.iterdir() if p.suffix.lower() == ".pdf"]
        self.assertEqual(len(pdfs), 3, f"samples/pdf 应有 3 份样例 PDF: {PDF_DIR}")
        pdf_names = {p.name for p in pdfs}
        for golden_file, golden in self.golden:
            with self.subTest(sample=golden_file.stem):
                pdf_name = Path(golden["sample"]["file"]).name
                self.assertIn(pdf_name, pdf_names, f"{golden_file.name} 引用了不存在的 PDF {pdf_name}")

    def test_manifest_matches_golden(self):
        manifest_path = SAMPLES / "manifest.json"
        self.assertTrue(manifest_path.is_file(), f"缺少 {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 3, "samples/manifest.json 应有 3 条记录")
        by_code = {item["code"]: item for item in manifest}
        for golden_file, golden in self.golden:
            sample = golden["sample"]
            with self.subTest(sample=golden_file.stem):
                self.assertIn(sample["code"], by_code, "manifest 缺少该样例代码")
                entry = by_code[sample["code"]]
                self.assertEqual(sample["file"], entry["file"])
                self.assertEqual(sample["title"], entry["title"])
                self.assertEqual(sample["disc_year"], entry["disc_year"])

    def test_extract_matches_golden(self):
        self.assertTrue(self.golden, f"未找到黄金结果: {EXPECTED_DIR}")
        for golden_file, golden in self.golden:
            sample = golden["sample"]
            pdf = SAMPLES / sample["file"]
            with self.subTest(sample=golden_file.stem):
                self.assertTrue(pdf.is_file(), f"缺少样例 PDF: {pdf}")
                result = MX.extract_document(str(pdf))
                actual = {key: result[key] for key in ("roles", "persons", "src", "note")}
                diffs = _diff(golden["extract"], actual)
                self.assertEqual(
                    diffs, [],
                    f"\n{golden_file.name} 抽取结果与黄金不一致:\n  " + "\n  ".join(diffs),
                )

    def test_meta_matches_golden(self):
        for golden_file, golden in self.golden:
            sample = golden["sample"]
            pdf = SAMPLES / sample["file"]
            with self.subTest(sample=golden_file.stem):
                self.assertTrue(pdf.is_file(), f"缺少样例 PDF: {pdf}")
                text = _read_pdf_text(pdf)
                # 用黄金 meta 中的机构名当离线机构名录：不依赖 FIN_Audit.csv，
                # 但仍经过 parse_firm 的正则通道 / 名录最长匹配通道。
                firms = [x for x in (golden["meta"]["firm_notice"], golden["meta"]["prev_firm"]) if x]
                got = MM.parse_meta(text, sample["title"], sample["disc_year"], firms=firms)
                for field in META_FIELDS:
                    self.assertEqual(
                        got.get(field), golden["meta"][field],
                        f"{golden_file.name} 字段 {field} 不一致: "
                        f"期望 {golden['meta'][field]!r}，实际 {got.get(field)!r}",
                    )


if __name__ == "__main__":
    unittest.main()
