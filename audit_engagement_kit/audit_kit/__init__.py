# -*- coding: utf-8 -*-
"""audit_kit — 上市公司审计机构聘任公告采集与结构化抽取工具包。

本包把巨潮资讯网（CNINFO）披露的「续聘 / 变更 / 聘任会计师事务所」类公告
采集、切片，并从公告「(二) 项目信息」中抽取项目组成员（项目合伙人、
签字注册会计师、质量控制复核人）及其四个关键时间节点，最终落到
SQLite / Excel。

子模块：
    crawl        检索、下载、关键页切片
    pipeline     三阶段任务流水线（plan → fetch → slice）
    store        SQLite 建表与装载
    extract      版面重建 + 人员抽取引擎
    times        四个时间节点抽取规则
    meta         公告级元字段（审计年度 / 事务所 / 收费等）
    reextract    全量重跑与回写
    repairs      同名冲突、OCR、正式公告优先、事务所口径等专项修复
    export       导出 Excel 主表
    sample       生成人工核对样本
    diagnostics  缺漏盘点与指标快照
"""
from __future__ import annotations

__version__ = "0.1.0"
