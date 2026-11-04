# -*- coding: utf-8 -*-
"""全局常量：采集年份范围、检索关键词、角色与时间节点字段名。

这些值是数据口径的一部分，改动会影响可复现性，README 中有对应说明。
"""
from __future__ import annotations

#: 采集覆盖的披露年
YEARS = ("2020", "2021", "2022", "2023", "2024", "2025", "2026")

#: 巨潮资讯检索关键词（命中其一即进入候选）
KEYWORDS = ("会计师事务所", "审计机构")

#: 三类聘任公告的候选筛选正则（用于 classify，见 crawl.classify）
#: 归一口径：变更 > 续聘 > 聘任
NOTICE_TYPES = ("变更", "续聘", "聘任")

#: 公告中的角色名（与 persons.role 一致）
ROLE_PARTNER = "项目合伙人"
ROLE_SIGNER = "签字注册会计师"
ROLE_REVIEWER = "质量控制复核人"
ROLES = (ROLE_PARTNER, ROLE_SIGNER, ROLE_REVIEWER)
MARKETS = ("A", "H")

#: 人员级时间节点字段（顺序即标准表列序）
TIME_FIELDS = ("become_cpa", "start_listed", "start_firm", "start_company")
TIME_FIELD_CN = {
    "become_cpa": "成为注册会计师",
    "start_listed": "开始从事上市公司审计",
    "start_firm": "开始在本所执业",
    "start_company": "开始为本公司提供审计服务",
}

#: 采集默认起始日；截止日留空表示取当天（见 crawl.fetch_notices）
START_DATE = "2020-01-01"

#: 巨潮接口并发节流（秒）：检索接口全局最小间隔
MIN_REQUEST_INTERVAL = 0.5
