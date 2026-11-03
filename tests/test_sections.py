# -*- coding: utf-8 -*-
import unittest

from mda_kit.build_sections_v3 import heading_key, parse_line


class HeadingParserTests(unittest.TestCase):
    def test_cn_numbering(self):
        style, num, title = parse_line("六、公司关于公司未来发展的讨论与分析")
        self.assertEqual(style, "cn")
        self.assertEqual(num, 6)
        self.assertIn("未来发展的讨论", title)

    def test_paren_chinese_numbering(self):
        style, _, title = parse_line("（一）行业格局和趋势")
        self.assertEqual(style, "cn_p")
        self.assertEqual(title, "行业格局和趋势")

    def test_dot_numbering(self):
        style, num, title = parse_line("1.1 经营模式")
        self.assertEqual(style, "dot")
        self.assertEqual(num, 101)
        self.assertEqual(title, "经营模式")

    def test_paren_arabic_numbering(self):
        style, _, title = parse_line("（1）主要产品及经营情况")
        self.assertEqual(style, "ar_p")
        self.assertIn("主要产品", title)

    def test_table_fragment_is_not_heading(self):
        self.assertIsNone(parse_line("1.892 万吨"))

    def test_heading_key(self):
        self.assertEqual(heading_key("报告期内公司从事的主要业务"), "business")
        self.assertEqual(heading_key("报告期内公司所处行业情况"), "industry")
        self.assertIsNone(
            heading_key("报告期内公司所从事的主要业务、经营模式、行业情况说明")
        )


if __name__ == "__main__":
    unittest.main()
