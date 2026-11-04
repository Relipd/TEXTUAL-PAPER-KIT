# -*- coding: utf-8 -*-
"""meta 模块单元测试：审计年度 / 事务所 / 前任 / 连续年限 / 审计收费 / parse_meta。

全部用合成公告文本，不依赖 data/ 与 FIN_Audit.csv。
"""
from __future__ import annotations

import unittest

from audit_kit import meta as M

TITLE_WITH_YEAR = "关于拟续聘2024年度会计师事务所的公告"
TITLE_NO_YEAR = "贵州茅台关于变更会计师事务所的公告"


class ParseAuditYearTest(unittest.TestCase):
    def test_title_year_preferred(self):
        self.assertEqual(M.parse_audit_year(TITLE_WITH_YEAR, "", 2024), "2024")

    def test_body_fallback_within_plus_one(self):
        text = "公司拟续聘会计师事务所为公司2025年度财务报表审计机构。"
        self.assertEqual(M.parse_audit_year(TITLE_NO_YEAR, text, 2024), "2025")

    def test_out_of_window_candidate_ignored(self):
        # 标题里的 2020 超出披露年 ±1 窗口，正文里的 2024 应胜出
        text = "公司为公司2024年度财务报表审计机构。"
        self.assertEqual(M.parse_audit_year("2020年度会计师事务所续聘公告", text, 2024), "2024")

    def test_without_disc_year_uses_first_candidate(self):
        self.assertEqual(M.parse_audit_year("", "2023年度财务报表审计机构", None), "2023")

    def test_prefers_disclosure_year_over_earlier_candidate(self):
        # 正文里 2023 与 2024 都在窗口内，优先取披露年 2024
        text = "公司2023年度审计费用为10万元。"
        self.assertEqual(M.parse_audit_year(TITLE_WITH_YEAR, text, 2024), "2024")

    def test_no_year_returns_empty(self):
        self.assertEqual(M.parse_audit_year("", "", None), "")


class ParseFirmTest(unittest.TestCase):
    FIRMS = sorted(
        [
            "安永华明会计师事务所（特殊普通合伙）",
            "安永华明会计师事务所",
            "华明会计师事务所",
        ],
        key=len,
        reverse=True,
    )

    def test_regex_from_body(self):
        text = "公司拟聘任会计师事务所为天职国际会计师事务所（特殊普通合伙），聘期一年。"
        self.assertEqual(M.parse_firm("", text, []), "天职国际会计师事务所（特殊普通合伙）")

    def test_catalog_longest_match(self):
        text = "审计机构为安永华明会计师事务所（特殊普通合伙）。"
        self.assertEqual(M.parse_firm("", text, self.FIRMS), "安永华明会计师事务所（特殊普通合伙）")

    def test_catalog_matches_shorter_name(self):
        text = "审计机构为华明会计师事务所。"
        self.assertEqual(M.parse_firm("", text, self.FIRMS), "华明会计师事务所")

    def test_catalog_allows_paren_width_difference(self):
        firms = ["德勤华永会计师事务所(特殊普通合伙)"]
        text = "德勤华永会计师事务所（特殊普通合伙）"
        self.assertEqual(M.parse_firm("", text, firms), "德勤华永会计师事务所(特殊普通合伙)")

    def test_title_fallback(self):
        self.assertEqual(
            M.parse_firm("关于变更天职国际会计师事务所的公告", "", []),
            "天职国际会计师事务所",
        )

    def test_no_firm_returns_empty(self):
        self.assertEqual(M.parse_firm("无", "", []), "")


class ParsePrevFirmTest(unittest.TestCase):
    def test_yuan_pin_ren(self):
        text = "公司原聘任会计师事务所为德勤华永会计师事务所（特殊普通合伙），拟变更为安永华明会计师事务所（特殊普通合伙）。"
        self.assertEqual(
            M.parse_prev_firm(text, []),
            "德勤华永会计师事务所（特殊普通合伙）",
        )

    def test_qian_ren(self):
        text = "前任会计师事务所为天职国际会计师事务所（特殊普通合伙）"
        self.assertEqual(M.parse_prev_firm(text, []), "天职国际会计师事务所（特殊普通合伙）")

    def test_yuan_shen_ji_ji_gou(self):
        text = "原审计机构为信永中和会计师事务所（特殊普通合伙）"
        self.assertEqual(M.parse_prev_firm(text, []), "信永中和会计师事务所（特殊普通合伙）")

    def test_no_prev_firm_returns_empty(self):
        self.assertEqual(M.parse_prev_firm("无", []), "")


class ParseTenureTest(unittest.TestCase):
    def test_formulations(self):
        cases = {
            "公司已连续6年为公司提供审计服务": "6",
            "连续服务8年": "8",
            "服务年限：10年": "10",
            "已连续服务3年": "3",
            "连续12年": "12",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(M.parse_tenure(text), expected)

    def test_out_of_range_or_missing(self):
        self.assertEqual(M.parse_tenure("连续服务0年"), "")
        self.assertEqual(M.parse_tenure("连续服务45年"), "")
        self.assertEqual(M.parse_tenure(""), "")


class ParseFeeTest(unittest.TestCase):
    def test_wan_yuan(self):
        text = "公司拟支付2024年度审计费用为人民币138万元（含税）"
        self.assertEqual(M.parse_fee(text), "138万元")

    def test_comma_separated(self):
        self.assertEqual(M.parse_fee("审计费用为1,388万元"), "1388万元")

    def test_decimal(self):
        self.assertEqual(M.parse_fee("审计收费为5983.39万元"), "5983.39万元")

    def test_yuan_unit(self):
        # parse_fee 只接受 0 < 金额 <= 100000
        self.assertEqual(M.parse_fee("审计费用为20000元"), "20000元")

    def test_business_income_is_not_fee(self):
        # 前置上下文含「业务收入」时，该金额不应被当成审计费用
        text = "公司2024年度业务收入为5,000万元，审计费用为200万元"
        self.assertEqual(M.parse_fee(text), "")

    def test_empty(self):
        self.assertEqual(M.parse_fee(""), "")


class ParseMetaTest(unittest.TestCase):
    TEXT = (
        "公司拟聘任会计师事务所为天职国际会计师事务所（特殊普通合伙），"
        "拟为公司2024年度财务报表审计机构。"
        "原聘任会计师事务所为德勤华永会计师事务所（特殊普通合伙）。"
        "公司已连续5年为公司提供审计服务。"
        "本期审计费用为人民币200万元。"
    )

    def test_synthetic_announcement(self):
        got = M.parse_meta(self.TEXT, TITLE_NO_YEAR, 2024, firms=[])
        self.assertEqual(
            got,
            {
                "audit_year": "2024",
                "firm_notice": "天职国际会计师事务所（特殊普通合伙）",
                "prev_firm": "德勤华永会计师事务所（特殊普通合伙）",
                "tenure": "5",
                "fee": "200万元",
            },
        )

    def test_without_firm_catalog(self):
        # 未提供正文可识别的机构写法、又给空名录时，firm_notice 应为空
        got = M.parse_meta("公司拟续聘会计师事务所，审计费用为50万元。", TITLE_WITH_YEAR, 2024, firms=[])
        self.assertEqual(got["audit_year"], "2024")
        self.assertEqual(got["firm_notice"], "")
        self.assertEqual(got["fee"], "50万元")


if __name__ == "__main__":
    unittest.main()
