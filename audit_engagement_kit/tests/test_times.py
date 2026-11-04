# -*- coding: utf-8 -*-
"""times 模块单元测试：parse_year / from_row_cells / from_prose / fill_missing。"""
from __future__ import annotations

import unittest

from audit_kit import times as T


class ParseYearTest(unittest.TestCase):
    def test_year_only(self):
        self.assertEqual(T.parse_year("2012年"), "2012")

    def test_year_month(self):
        self.assertEqual(T.parse_year("2012年3月"), "2012-03")
        self.assertEqual(T.parse_year("2012 年 11 月"), "2012-11")

    def test_year_without_suffix(self):
        self.assertEqual(T.parse_year("1999"), "1999")

    def test_whitespace_is_ignored(self):
        self.assertEqual(T.parse_year("  2012 年 "), "2012")

    def test_invalid_month_keeps_year(self):
        self.assertEqual(T.parse_year("2012年13月"), "2012")

    def test_non_year_text_returns_empty(self):
        self.assertEqual(T.parse_year("项目合伙人"), "")
        self.assertEqual(T.parse_year("2012年度"), "")

    def test_empty_inputs(self):
        self.assertEqual(T.parse_year(""), "")
        self.assertEqual(T.parse_year(None), "")


class FromRowCellsTest(unittest.TestCase):
    CELLS = [
        (10, 40, "项目合伙人"),
        (60, 90, "张芹"),
        (120, 160, "2000年"),
        (180, 210, "1998年"),
        (240, 280, "2000年"),
        (300, 350, "2006年"),
    ]

    def test_sequence_without_header(self):
        times, raw = T.from_row_cells(self.CELLS, name_right=95)
        self.assertEqual(
            times,
            {
                "become_cpa": "2000",
                "start_listed": "1998",
                "start_firm": "2000",
                "start_company": "2006",
            },
        )
        self.assertEqual(raw, ["2000", "1998", "2000", "2006"])

    def test_named_columns(self):
        cols = {
            "become_cpa": (100, 150),
            "start_listed": (170, 220),
            "start_firm": (230, 290),
            "start_company": (295, 360),
        }
        times, _raw = T.from_row_cells(self.CELLS, name_right=95, cols=cols)
        self.assertEqual(
            times,
            {
                "become_cpa": "2000",
                "start_listed": "1998",
                "start_firm": "2000",
                "start_company": "2006",
            },
        )

    def test_partial_header_falls_back_to_sequence(self):
        cols = {"start_company": (295, 360)}
        times, _raw = T.from_row_cells(self.CELLS, name_right=95, cols=cols)
        self.assertEqual(
            times,
            {
                "become_cpa": "2000",
                "start_listed": "1998",
                "start_firm": "2000",
                "start_company": "2006",
            },
        )

    def test_cells_left_of_name_are_ignored(self):
        cells = [(10, 80, "2012年"), (120, 160, "2000年"), (180, 210, "1998年")]
        times, raw = T.from_row_cells(cells, name_right=100)
        self.assertEqual(raw, ["2000", "1998"])
        self.assertEqual(times["become_cpa"], "2000")
        self.assertEqual(times["start_listed"], "1998")
        self.assertEqual(times["start_firm"], "")
        self.assertEqual(times["start_company"], "")

    def test_no_year_cells(self):
        times, raw = T.from_row_cells([(10, 80, "张三"), (100, 140, "无")], name_right=0)
        self.assertEqual(raw, [])
        self.assertEqual(times, {"become_cpa": "", "start_listed": "", "start_firm": "", "start_company": ""})


class FromProseTest(unittest.TestCase):
    DESC = (
        "张三，1999年成为中国注册会计师，1998年起开始从事上市公司审计，"
        "1998年起在中审众环会计师事务所执业，2022年起为本公司提供审计服务。"
    )

    def test_all_four_nodes(self):
        self.assertEqual(
            T.from_prose(self.DESC),
            {
                "become_cpa": "1999",
                "start_listed": "1998",
                "start_firm": "1998",
                "start_company": "2022",
            },
        )

    def test_partial_nodes(self):
        got = T.from_prose("李四，2010年成为注册会计师，2015年起为本公司提供审计服务。")
        self.assertEqual(got["become_cpa"], "2010")
        self.assertEqual(got["start_company"], "2015")
        self.assertEqual(got["start_listed"], "")
        self.assertEqual(got["start_firm"], "")

    def test_empty_description(self):
        self.assertEqual(
            T.from_prose(""),
            {"become_cpa": "", "start_listed": "", "start_firm": "", "start_company": ""},
        )


class FillMissingTest(unittest.TestCase):
    def test_fills_only_missing_fields(self):
        dst = {"become_cpa": "2001", "start_listed": "", "start_firm": "1999", "start_company": ""}
        src = {"become_cpa": "1990", "start_listed": "1995", "start_firm": "1998", "start_company": "2020"}
        out = T.fill_missing(dst, src)
        self.assertIs(out, dst, "fill_missing 应原地更新并返回 dst")
        self.assertEqual(
            out,
            {"become_cpa": "2001", "start_listed": "1995", "start_firm": "1999", "start_company": "2020"},
        )

    def test_empty_source_keeps_dst(self):
        dst = {"become_cpa": "2001", "start_listed": "", "start_firm": "", "start_company": ""}
        out = T.fill_missing(dst, {"become_cpa": "", "start_listed": "", "start_firm": "", "start_company": ""})
        self.assertEqual(out["become_cpa"], "2001")
        self.assertEqual(out["start_listed"], "")


if __name__ == "__main__":
    unittest.main()
