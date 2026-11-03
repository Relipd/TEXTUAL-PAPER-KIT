# 数据模型

数据库默认位置：`data/mda_sections.sqlite`（可在 `config.toml` 修改）。
所有表由 `build_sections_v3.py` / `build_biz_sections.py` 自动创建。

## documents

文件级元数据与质量标记。

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `code` | 六位股票代码（TEXT，保留前导零） |
| `year` | 年度 |
| `name` | 股票简称 |
| `file` | 原始文件名（`{year}-{code}-{name}.pdf`） |
| `pages` | MD&A 切片页数 |
| `n_sections` | 识别到的非空章节数（0–11） |
| `detect_mode` | 标题体系：`cn` / `ar` / `title_only` / `none` 等 |
| `quality_flag` | `ok` / `truncated` / `wrong_chapter` / `wrong_start` / `empty` |
| `mda_page` | 「管理层讨论与分析」标题所在页（0=未找到） |
| `has_mda_title` | 标题是否在切片前 15% 页内 |
| `end_ok` | 末页是否像正常收尾 |
| `n_headings` | 识别到的标题行数 |
| `built_at` | 入库时间 |

## sections

每份文件 × 11 个章节槽位；未识别也写空行（`chars=0`），便于拼面板。

| 字段 | 说明 |
|---|---|
| `id`, `doc_id` | 主键 / 外键（documents.id） |
| `code`, `year`, `name` | 冗余元数据 |
| `section_key` | 11 个键之一（见下） |
| `section_label` | 中文名 |
| `title_raw` | 原报告标题（保留变体） |
| `start_page`, `end_page`, `n_pages` | 起止页（1-based，含端点） |
| `chars` | 原始正文字数 |
| `text` | 原始正文（按标题 bbox 切片） |
| `text_clean` | 清洗后正文（仅叙述文字） |
| `clean_chars` | 清洗后字数 |
| `is_na` | 1 = 原文只有「√适用/□不适用」 |
| `table_only` | 1 = 原文非空、清洗后为空（整节基本是表格） |
| `boundary` | 本章起点标题的来源：`cn` / `cn_p_sub` / `ar` / `kw` / `absent` / `overspan` 等 |

**空串语义**

- `chars=0`：未识别到该章；
- `chars>0 & clean_chars=0`：一定是 `is_na=1` 或 `table_only=1`；
- 用 `clean_chars` 判断「可用于文本分析」的长度，不要用宽表空串。

### 11 个章节键

| key | 中文名 |
|---|---|
| `industry` | 行业情况 |
| `business` | 主要业务 |
| `core_comp` | 核心竞争力 |
| `main_ops` | 主营业务分析 |
| `non_main` | 非主营业务 |
| `assets` | 资产及负债 |
| `invest` | 投资状况 |
| `subsidiary` | 子公司参股 |
| `structured` | 结构化主体 |
| `outlook` | 未来发展 |
| `research` | 接待调研 |

## biz_sections（可选）

2015–2020 年「公司业务概要」的二级切分；字段与 `sections` 类似，`section_key` 为：

- `business`：主要业务
- `core_comp`：核心竞争力
- `assets_change`：主要资产变化

## sections_merged（可选视图）

以 `sections` 为基，2015–2020 年 `business` / `core_comp` 若在 `biz_sections` 有非空结果则替换；
额外 `src` 列：`md_a` / `biz_overview`。行数与 `sections` 相同。

> 注意：`sections_merged` 的早年两章来自「公司业务概要」，不是 MD&A。若研究对象是 MD&A，请使用 `sections`。

## sections_fts

FTS5 外部内容表，挂在 `sections.text`，`rowid` 对应 `sections.id`。
注意：`unicode61` 分词对连续中文按整词处理，子串检索请使用 `LIKE`；`biz_sections` 未入该索引。

## 索引

`idx_sec_key`、`idx_sec_year`、`idx_sec_code`、`idx_sec_doc`，以及 FTS5 内部表。
