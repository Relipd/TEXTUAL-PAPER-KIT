# 处理流程

```
targets.csv
   │
   ▼
[1] 规划：CNINFO 年报公告 URL ──► data/MDA/_plan.csv
   │
   ▼
[2] 下载 + 定位 + 截取 ──► data/MDA/{year}/{year}-{code}-{name}.pdf
   │
   ▼
[3] 章节切分 + 入库 ──► data/mda_sections.sqlite (documents / sections)
   │
   ▼
[4] 清洗 ──► sections.text_clean / clean_chars / is_na / table_only
   │
   ├── [可选] 业务概要 ──► biz_sections ──► sections_merged
   │
   ▼
[5] 导出 ──► data/exports/sections_wide_*.csv / sections_index_*.dta / _sections_*.csv
```

## 1. 规划（`fetch_mda.py`）

- 目标清单提供 `(code, year)`；
- 用 CNINFO `szse_stock.json` 取 `orgId`；
- 通过 `hisAnnouncement/query` 按公司查询年报公告；或用 `fulltextSearch/full` 按年度全市场检索；
- 过滤摘要、英文版、港股、更正/补充公告等；按「年度报告」标题与年份选择；
- 生成 `_plan.csv`（代码、年度、标题、URL、大小等）。

## 2. 下载与截取（`fetch_mda.py`）

- 每个进程独立 Session + 限速；
- 断点续传：目标 PDF 已存在则跳过；
- 下载后打开 PDF，定位 MD&A 起止页，截取区间另存为切片；
- 写 `_manifest.csv` 记录成功/失败/URL；失败写 `_failed.csv`；
- `fix_upstream.py` 用于异常文件的重新下载/定位/重切（v4 启发式）。

MD&A 定位优先级：

1. PDF 书签（严格匹配「管理层讨论与分析」等标题）；
2. 泛化书签 + 页首校验；
3. 页首「第X节」章标题；
4. 页内关键词 + 上下文校验。

收尾：任意「第X节 / 第X章」标题、页首章级标题、或文档末尾；末页断句时最多向后顺延 5 页。

## 3. 章节切分与入库（`build_sections_v3.py`）

- 从 PDF 的文本行拿字号、bbox；
- 识别 5 类编号：`cn`（一、）、`cn_p`（（一））、`ar`（1、）、`ar_p`（（1））、`dot`（1.1）；
- 标题形态校验、表格行过滤、拆行标题拼接；
- 判定一级体系（连续递增 ≥3、章级样式优先、编号重启次数）；
- 每章按标题 bbox 切正文，记录边界来源；
- 写 `documents` / `sections`，重建 FTS（可用 `--no-fts` 关闭）。

## 4. 清洗（`clean_text_v3.py`）

- 去相邻重复页眉页脚、页码、勾选框、单位/币种行、纯编号、表头行；
- 数字密度规则只作用于**行末无句末标点**的行；
- 保守重排：长句 + 短句尾合并，避免「连续短行」误删句尾；
- 连续短行块、无标点短块判为表格；
- 写 `text_clean`、`clean_chars`、`is_na`、`table_only`。

## 5. 导出（`export_by_year.py`）

- 宽表：每份年报一行 × 16 列；
- Stata 长表：每份年报 × 11 章节槽位；
- 索引 CSV：仅非空章节，便于人工抽查；
- 默认数据源 `sections`；使用 `--source sections_merged` 可包含业务概要合并结果。

## 关键失败模式

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 规划为空 | 目标 CSV 列名/格式不对 | 确认 `Stkcd/ShortName/Accper`；Accper 12/31 |
| 403 / 空公告列表 | 需要预热 cookie、UA/Referer 不对、代理污染 | 保持会话预热；`trust_env = true` 若必须走代理；降低 rps |
| 定位失败 | 年报无书签/标题格式特殊 | 用 `fix_upstream.py` 重试；查看 `_fix_upstream.csv` |
| 章节为空 | 表格型章节、页数过少、PDF 切片残缺 | 看 `chars` / `is_na` / `table_only` / `quality_flag` |
| 导出无数据 | 数据库路径/年份不对，或未跑 clean | 检查 config 与 `documents` 表 |
