# -*- coding: utf-8 -*-
"""repairs — 主库专项修复。

这些模块都在已抽取的主库（documents / persons）上做定点修正，均不带 --apply
以外的副作用入口，默认直接写库，使用前请先备份 ``data/audit_contracts.db``。

子模块：
    collisions    修复「同名公告共用同一 PDF 切片」（拿错文件）：按
                  announcementId 从 data/raw 重新切片，文件名追加 ``-{aid}``，
                  再重新抽取并回写人员与元字段。
    ocr           用 OCRmyPDF 给扫描件补文字层（写 data/ocr/ 与
                  data/ocr_results.jsonl），可选 --apply 回写主库，src 记为 ocr。
    prefer_formal 以正式公告为准：删除非正式文件（独立意见 / 承诺函等）里的
                  重复人员，并跨公告回填缺失的时间节点（来源加 '+bf'）。
    firms         按当前审计年度，用 FIN_Audit 的（证券代码, 会计年度）映射
                  重算 documents.firm_official，填补空缺。
"""
