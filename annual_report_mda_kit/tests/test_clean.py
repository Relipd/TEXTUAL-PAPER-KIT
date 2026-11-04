# -*- coding: utf-8 -*-
import unittest

from mda_kit.clean_text_v3 import clean


class CleanTextTests(unittest.TestCase):
    def test_header_page_number_and_units_removed(self):
        raw = (
            "XX股份有限公司2025 年年度报告\n"
            "15\n"
            "√适用 □不适用\n"
            "单位：元\n"
            "报告期内，公司实现营业收入1,234.56万元，同比增长12.3%。\n"
            "长沙  长沙振业城  商业  80%\n"
        )
        out = clean(raw)
        self.assertIn("营业收入", out)
        self.assertIn("同比增长12.3%", out)
        self.assertNotIn("单位：元", out)
        self.assertNotIn("√适用", out)
        self.assertNotIn("15", out.splitlines())
        self.assertNotIn("长沙", out)

    def test_numeric_narrative_line_is_kept(self):
        text = "公司营业收入87.35亿元，同比下降1.77%。"
        self.assertIn("87.35亿元", clean(text))

    def test_empty_input(self):
        self.assertEqual(clean(""), "")


if __name__ == "__main__":
    unittest.main()
