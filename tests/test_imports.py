# -*- coding: utf-8 -*-
"""Smoke test: every pipeline module imports without side effects."""
import importlib
import unittest

MODULES = [
    "mda_kit.config",
    "mda_kit.fetch_mda",
    "mda_kit.fix_upstream",
    "mda_kit.neeq_fill",
    "mda_kit.neeq_resume",
    "mda_kit.build_sections_v3",
    "mda_kit.clean_text_v3",
    "mda_kit.fetch_biz",
    "mda_kit.build_biz_sections",
    "mda_kit.merge_biz",
    "mda_kit.export_by_year",
    "mda_kit.rebuild_fts",
    "mda_kit.resolve_missing_biz",
    "mda_kit.analyze_by_year",
]


class ImportTests(unittest.TestCase):
    def test_import_all(self):
        for name in MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_cli_entrypoint(self):
        from mda_kit import __main__

        self.assertTrue(callable(__main__.main))


if __name__ == "__main__":
    unittest.main()
