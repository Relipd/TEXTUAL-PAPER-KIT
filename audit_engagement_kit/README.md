# 中国 A 股审计续聘公告语料工具包

> AUDIT-ENGAGEMENT-KIT：从巨潮资讯网采集 A 股「续聘 / 变更 / 聘任会计师事务所」公告，
> 重建 PDF 版面并抽取项目组成员与四个关键时间节点，存入 SQLite（含 FTS5 全文索引）并导出 Excel。

本 kit 是 **TEXTUAL-PAPER-KIT** 仓库的一员，与 `annual_report_mda_kit`（年报 MD&A）并列。

## Abstract

`audit_engagement_kit` is an open-source Python toolkit that collects
auditor engagement announcements of Chinese A-share listed companies from CNINFO
(2020–2026), reconstructs the PDF layout of the "project information" section, and
extracts engagement partners, signing certified public accountants, quality control
reviewers, and four career time nodes (`become_cpa`, `start_listed`, `start_firm`,
`start_company`). Results are persisted to a SQLite database with FTS5 full-text
search and exported to Excel for archival and panel analysis. The extraction is
rule-based and deterministic: no LLM, no network at run time for an already
downloaded corpus. This repository ships **code, documentation, and three public
sample announcements with golden results only** — the full corpus, the third-party
licensed auditor-opinion list (`FIN_Audit.csv`), and the raw PDF archive are not
distributed.

## 中文简介

上市公司在续聘、变更或聘任会计师事务所时，会发布公告披露「(二) 项目信息」：项目合伙人、
签字注册会计师、项目质量控制复核人，以及每个人「何时成为注册会计师、何时开始从事上市
公司审计、何时开始在本所执业、何时开始为本公司提供审计服务」四个时间节点。这些信息是
研究审计师—客户关系、签字师轮换与披露质量的一手材料，但它们以多列表格的 PDF 形式存在，
文本流被换行拆散，不能靠简单正则可靠提取。

本工具包用**版面坐标重建**（把 PDF 页面还原成「视觉行 → 单元格」）加四重姓名校验来做抽取，
把公告转成人员级与公告级结构化记录，并保留抽取来源标签以便逐条复核。仓库只包含代码、
文档与 3 份公开样例，不包含全量数据库、原始 PDF 或第三方授权名单。

**目录**：[项目解决什么问题](#1-项目解决什么问题) ·
[特性](#2-特性) · [数据来源与合规](#3-数据来源与合规声明) ·
[快速开始](#4-快速开始) · [CLI 命令](#5-cli-命令清单) ·
[目录结构](#6-目录结构) · [复现流程](#7-完整复现流程) ·
[规模与质量](#8-数据规模与质量指标) · [输出 schema](#9-输出-schema-简述) ·
[已知限制](#10-已知限制) · [引用与许可](#11-引用与许可) · [文档索引](#12-文档索引)

## 1. 项目解决什么问题

**输入**：巨潮资讯网披露的聘任类公告 PDF（变更 / 续聘 / 聘任会计师事务所）。

**输出**：两条粒度的结构化记录。

| 粒度 | 字段 |
|---|---|
| 人员级 | 职位、姓名、是否兼任、四个关键时间节点、时间节点来源 |
| 公告级 | 证券代码/简称、披露年、审计年度、公告类型、披露日期、事务所（公告口径）、事务所（年报口径）、前任事务所、连续服务年限、审计收费、公告链接与本地文件、抽取来源 |

**典型用途**：审计师—客户关系匹配、项目合伙人/签字师轮换与任期、审计市场集中度、
披露质量与信息含量、项目组结构等实证研究。

**难点**：公告的项目信息多为多列表格，PDF 文本流会把「邵建 / 克」拆成两行、把
「项目合 / 伙人」拆成两行；按文本流做正则会抓到「时间」「家次」「经验」甚至证券简称。
本项目因此选择先重建版面、再做角色定位与姓名校验。

## 2. 特性

- **版面重建而非纯正则**：用 `get_text("words")` 的坐标把页面还原成视觉行与单元格，
  在单元格层面按角色标签、表头列、姓名列取值，解决表格被文本流拆散的问题。
- **四种版式**：行式表格（角色在左、姓名在右）、田字表格（角色为表头、姓名为行标签）、
  叙述式（「项目合伙人：张三，2012 年成为……」）、合称式（「项目合伙人及签字注册
  会计师 1：王璟」）；另有「姓名在前、职务在后」「姓名 + 职务连写」等变体。
- **姓名四重校验**：姓氏白名单（含复姓与稀姓）→ 停用词 → 上市公司简称黑名单 →
  否定式上下文（「近三年未因执业行为受处罚」不能当姓名）。
- **A/H 字段兼容**：schema 保留 `market`（A/H）与 `has_h` 以兼容历史全量数据库；
  公开版抽取引擎输出 A 股项目组成员（历史快照中的 19 条 H 股人员由旧版抽取器产生）。
- **OCR 兜底**：无文字层的扫描件用 OCRmyPDF（Tesseract + `chi_sim`）补文字层后，
  按同一套版面逻辑重抽；候选自动筛选，只送前 12 页控制耗时。
- **正式公告优先去重**：同一事项的正式公告与独立意见/承诺函/陈述意见并存时，删除
  非正式文件中的重复人员；缺失的时间节点用同公司同名记录跨公告回填（正式公告优先）。
- **双事务所口径**：`firm_notice`（公告拟聘）与 `firm_official`（年报实聘）并列，
  便于核对「拟聘 ≠ 实聘」。
- **SQLite + FTS5**：`documents` / `persons` / `documents_fts` 三表，支持按代码、姓名、
  事务所索引查询与公告原文全文检索。
- **Excel 导出**：人员明细（长表）、公告明细、公司年度面板（去重）、统计四个工作表。
- **确定性、可增量**：全部规则化处理，同一输入结果稳定；采集分阶段任务化，可断点续传；
  `demo` 离线自检，不需要网络、数据库或第三方名单。

## 3. 数据来源与合规声明

- 数据来源为巨潮资讯网（CNINFO，<http://www.cninfo.com.cn>）的**公开披露**公告；
  版权归披露主体与巨潮资讯网所有。
- 采集应遵守巨潮的服务条款与访问频率限制。程序内置全局节流（检索接口约 2 次/秒）
  与 403 熔断退避，**请勿**关闭节流或高并发压测。
- 本仓库**不包含**：全量公告 PDF（约 12 GB）、全量 SQLite 主库、全量交付 Excel、
  审计意见名单 `FIN_Audit.csv`（CSMAR 等第三方授权数据）。仓库仅含代码、文档、
  3 份公开样例公告及其黄金结果。
- `samples/` 中的样例仅用于演示与回归测试，不代表仓库包含全量数据。
- 使用者需自行取得并遵守 `FIN_Audit.csv` 等第三方数据的使用授权；因采集、使用或
  再分发数据产生的合规责任由使用者自行承担。代码采用 MIT 协议（见
  [LICENSE](LICENSE)）。

## 4. 快速开始

环境要求：Python **≥ 3.9**。

```bash
# 方式一：直接安装依赖
pip install -r requirements.txt

# 方式二：以可编辑模式安装本包（同时得到 audit-kit 命令）
pip install -e .
```

**无需联网、无需数据库、无需 `FIN_Audit.csv`**，即可用 `samples/` 验证安装：

```bash
python -m audit_kit parse samples/pdf/000002_1225067818.PDF --json
```

输出（节选，实际含全部 3 名成员）：

```json
{
  "file": "samples/pdf/000002_1225067818.PDF",
  "roles": {
    "项目合伙人": ["杨誉民"],
    "签字注册会计师": ["许湘照"],
    "质量控制复核人": ["黄天义"]
  },
  "persons": [
    {
      "role": "项目合伙人",
      "name": "杨誉民",
      "times": {"become_cpa": "2007", "start_listed": "2004",
                "start_firm": "", "start_company": ""},
      "years": [],
      "source": "prose+ctx"
    }
  ],
  "src": "prose",
  "note": ""
}
```

回归 3 份样例的黄金结果（新装环境最快的自检方式，数秒完成、不联网、不读数据库）：

```bash
python -m audit_kit demo
```

```
PASS 000002_1225067818        src=prose    项目合伙人:1、签字注册会计师:1、质量控制复核人:1
PASS 600519_1219506502        src=layout   项目合伙人:1、签字注册会计师:2、质量控制复核人:1
PASS 601398_ah                src=rawtext  签字注册会计师:3、质量控制复核人:1

样例 3 份，通过 3，失败 0
```

`demo` 逐项比对 `samples/expected/*.json` 中的 `roles` / `persons` / `src` / `note`，
不联网、不读数据库、不需要第三方名单，适合安装后自检与 CI。

## 5. CLI 命令清单

统一入口：`python -m audit_kit <command>`；`pip install -e .` 之后
`audit-kit <command>` 等价。数据路径可用环境变量覆盖（见 [docs/data-model](docs/data-model.md)）。

| 命令 | 说明 | 联网 | 写主库 |
|---|---|:--:|:--:|
| `demo` | 用 `samples/` 的 3 份样例核对抽取行为 | — | — |
| `parse <pdf>` | 解析单份公告 PDF，打印人员与时间节点；`--json` 输出机器可读结果，`--title` / `--disc-year` 同时抽公告级字段 | — | — |
| `crawl <code>` | 单家（或多家）公司检索、下载原始 PDF 并切片（试跑/补抓用）；支持多个代码，`--limit` 限量，`--start` 默认 2020-01-01、`--end` 默认当天 | ✓ | — |
| `plan` | 阶段 1：逐公司检索公告清单并归类 | ✓ | 任务表 |
| `fetch` | 阶段 2：并发下载原始 PDF | ✓ | 任务表 |
| `slice` | 阶段 3：截取关键页并导出文本 | — | 任务表 |
| `status` | 查看采集任务各阶段进度 | — | — |
| `import` | 把历史 `notices.jsonl` 导入任务表 | — | 任务表 |
| `build` | 由任务表创建 SQLite 主库（建表 + 基础字段） | — | ✓ |
| `extract` | 全量抽取人员与时间节点 → `v2_results.jsonl`；`--workers N` 指定进程数，`--out` 指定输出，`--apply` 边抽边写 | — | 可选 |
| `apply` | 只把 `v2_results.jsonl` 回写主库（自动备份） | — | ✓ |
| `collisions` | 修复同名公告切片撞名并重抽 | — | ✓ |
| `ocr` | 扫描件 OCR 补文字层后重抽；`--limit N` 试跑，`--apply` 回写 | — | 可选 |
| `prefer-formal` | 正式公告优先去重 + 跨公告回填时间节点 | — | ✓ |
| `firms` | 按审计年度重算事务所（年报口径） | — | ✓ |
| `export` | 导出 Excel 主表（4 个工作表）；`--out` 指定路径 | — | — |
| `sample` | 生成人工核对样本与疑点清单；`--out` 指定路径 | — | — |
| `gaps` | 缺漏盘点（无人员公告分类、角色缺失、节点归因） | — | — |
| `stats` | 指标快照 | — | — |

常用参数：

- `plan` / `fetch` / `slice`：`--workers N` 并发数、`--limit N` 限量试跑；
- `build`：`--limit N` 小样本、`--rebuild` 重建、`--dry-run` 只统计不写库；
- `export` / `sample`：`--out <path>` 覆盖默认输出（默认写入 `output/`，该目录不入库）。

## 6. 目录结构

```
audit_engagement_kit/
├─ audit_kit/                 Python 包（pip install -e . 的安装对象）
│  ├─ __main__.py              python -m audit_kit 入口
│  ├─ cli.py                   命令分发（含 parse / demo）
│  ├─ crawl.py                 检索、下载、关键页切片
│  ├─ pipeline.py              采集三阶段任务流水线（plan/fetch/slice/status/import）
│  ├─ store.py                 SQLite 建表与基础字段装载
│  ├─ extract.py               版面重建 + 人员抽取引擎
│  ├─ times.py                 四个时间节点抽取规则
│  ├─ meta.py                  公告级元字段（审计年度/事务所/收费等）
│  ├─ reextract.py             全量重跑与回写
│  ├─ repairs/                 专项修复（collisions / ocr / prefer_formal / firms）
│  ├─ export.py                Excel 导出
│  ├─ sample.py                人工核对样本
│  ├─ diagnostics.py           缺漏盘点与指标快照
│  ├─ paths.py                 统一路径解析（环境变量覆盖）
│  └─ config.py                年份、关键词、角色、字段等常量
├─ samples/                    公开样例（3 份 PDF + 黄金结果 + 清单）
│  ├─ pdf/
│  ├─ expected/
│  ├─ manifest.json
│  └─ README.md
├─ docs/                       详细文档（见文末索引）
│  └─ ci.yml.example            CI 模板（并入 TEXTUAL-PAPER-KIT 后放到根 `.github/workflows/`）
├─ tests/                      单元测试（stdlib unittest，离线、不依赖 data/）
├─ README.md                   本文件
├─ CITATION.cff                引用信息
├─ LICENSE                     MIT
├─ requirements.txt            运行依赖
├─ requirements-ocr.txt        OCR 可选依赖
├─ pyproject.toml              包元数据与入口
└─ FIN_Audit.csv               审计意见名单（第三方授权数据，需自备，不入库）
```

运行后生成的数据目录（均在 `.gitignore` 中）：

```
data/
├─ pipeline.db                采集任务表
├─ audit_contracts.db         主库（documents / persons / documents_fts）
├─ raw/                       原始 PDF 缓存（可清理、可重建）
├─ pdf/<年份>/                关键页切片
├─ text/<年份>/               切片文本
├─ ocr/<年份>/                OCR 结果
├─ v2_results.jsonl           抽取中间结果
└─ audit_contracts_bak_*.db   回写前自动备份
output/                       导出的 Excel
```

## 7. 完整复现流程

完整步骤、参数与故障排查见 [docs/reproduce](docs/reproduce.md)。摘要：

```bash
pip install -e .                                            # 0. 安装
python -m audit_kit demo                                   #    离线自检

python -m audit_kit plan --workers 3                       # 1. 采集：检索清单
python -m audit_kit fetch --workers 16                     #    下载 PDF
python -m audit_kit slice --workers 8                      #    切关键页 + 文本
python -m audit_kit status                                 #    查看进度

python -m audit_kit build                                  # 2. 建库

python -m audit_kit extract --workers 8                    # 3. 全量抽取
python -m audit_kit apply                                  #    回写主库（自动备份）

python -m audit_kit collisions                             # 4. 专项修复
python -m audit_kit ocr --apply
python -m audit_kit prefer-formal
python -m audit_kit firms

python -m audit_kit export                                 # 5. 导出 Excel
python -m audit_kit sample                                 #    核对样本
python -m audit_kit gaps && python -m audit_kit stats     # 6. 诊断
```

采集阶段需要 `FIN_Audit.csv` 与网络；已有数据时后续步骤可完全离线。增量更新只需重跑
`plan → fetch → slice → build → extract → apply`（各阶段自动跳过已完成任务），详见
[docs/reproduce](docs/reproduce.md) 第 4 节。

## 8. 数据规模与质量指标

以下为**本地全量运行参考值**（数据截止 2026-11-03，一次完整运行的结果；随增量更新变化）。
口径见 [docs/data-source](docs/data-source.md)，质量细节见 [docs/limitations](docs/limitations.md)。

| 指标 | 数值 |
|---|---|
| 公告总数 | 31603 |
| 覆盖公司 | 5284 |
| 人员记录 | 98608（A 股 98589 + H 股 19，历史全量快照） |
| 公司年度面板（去重后） | 29756 行 |
| 关键页切片 / 切片文本 | 31603 份 / 31603 份 |
| 主库体积 | 约 390 MB（373 MiB，含 FTS5 索引） |
| 原始 PDF 缓存 | 约 12 GB（可清理、可重建） |

其中「人员记录」含历史全量快照的 19 条 H 股人员（由旧版抽取器产生）；**公开版复现为
A 股口径**，H 股相关列仅用于兼容历史数据库。

| 质量指标 | 数值 |
|---|---|
| 三字段齐全（合伙人 + 签字人 + 复核人） | 27874 条公告（88.2%） |
| 项目合伙人非空 | 29247（92.5%） |
| 签字注册会计师非空 | 30742（97.3%） |
| 质量控制复核人非空 | 29307（92.7%） |
| 完全未抽取 | 751（2.4%） |
| 姓名格式合规（2–4 汉字） | 100% |
| 姓名可在原文检索到 | 99.97% |
| 四节点齐全（人员级） | 65666（66.6%） |

## 9. 输出 schema 简述

完整字段、取值域与 Excel 列定义见 [docs/data-model](docs/data-model.md)。

**SQLite 主库**（`data/audit_contracts.db`）：

| 表 | 内容 | 关键列 |
|---|---|---|
| `documents` | 一份公告一行 | `code, name, disc_year, audit_year, type, date, title, firm_notice, firm_official, prev_firm, tenure, fee, url, pdf, txt, pages, src, note, has_h` |
| `persons` | 一名项目组成员一行 | `doc_id, market, role, name, concurrent, become_cpa, start_listed, start_firm, start_company, time_src` |
| `documents_fts` | FTS5 全文索引 | `title, content, code, doc_id` |

```sql
-- 某公司各年度项目组成员与四个时间节点
SELECT d.disc_year, p.role, p.name, p.concurrent,
       p.become_cpa, p.start_listed, p.start_firm, p.start_company
FROM persons p JOIN documents d ON d.id = p.doc_id
WHERE d.code = '000002'
ORDER BY d.disc_year, p.role;
```

**Excel**（`output/审计机构聘任公告_项目成员信息.xlsx`）四个工作表：`人员明细`（长表）、
`公告明细`（一份公告一行）、`公司年度面板`（按「公司-披露年」去重）、`统计`（指标汇总）。

**中间结果**：`data/v2_results.jsonl`，每行一份公告，含 `roles`、`persons`（时间节点与
来源）、`src`、`note`、`meta`（审计年度/事务所/前任/年限/收费），由 `apply` 回写主库。

## 10. 已知限制

- 751 份公告（2.4%）无人员：标题属聘任类但原文确无项目信息表，或属审计报告/独董意见/
  H 股文件等不在本口径的文档；
- 四节点齐全率 66.6%：2020 年公告多数不写四个节点，2026 年约 62%，属披露格式演进；
- 合伙人 = 复核人同名 234 条：表格相邻行串列所致，已全部进入核对样本的疑点清单；
- 姓名不在原文 33 条：OCR 误差或跨公告回填导致，已标记待复核；
- H 股人员仅见于历史全量快照（18 份公告、19 条记录）；公开版抽取引擎输出 A 股项目
  组成员，`market` / `has_h` 列仅用于兼容历史数据库；
- `firm_notice`（拟聘）与 `firm_official`（实聘）可能不一致，学术使用以年报口径为准；
- 前任事务所、连续服务年限、审计收费为自愿披露字段，缺失率高，不宜作主变量。

详见 [docs/limitations](docs/limitations.md)。

## 11. 引用与许可

如果本项目对你的研究有帮助，请引用（见 [CITATION.cff](CITATION.cff)）：

```bibtex
@software{audit_engagement_notice_corpus,
  title        = {audit_engagement_kit},
  author       = {{audit_engagement_kit contributors}},
  year         = {2026},
  version      = {0.1.0},
  license      = {MIT},
  note         = {A-share auditor engagement announcement corpus and extraction toolkit}
}
```

**代码许可**：[MIT](LICENSE)。**数据许可**：公告原文版权归披露主体与巨潮资讯网所有；
`FIN_Audit.csv` 为第三方授权数据，不随仓库分发；使用者需自行承担数据采集与使用的
合规责任。

## 12. 文档索引

| 文档 | 内容 |
|---|---|
| [docs/data-source](docs/data-source.md) | 数据来源、公告类型、披露年/审计年度、双事务所口径、A/H、时间节点、`FIN_Audit.csv` 准备 |
| [docs/extraction](docs/extraction.md) | 为什么不用纯正则、版面重建、四种版式、姓名校验、节点对齐、兜底链、三处专项处理 |
| [docs/reproduce](docs/reproduce.md) | 环境搭建、分步命令、完整命令串、增量更新、常见故障 |
| [docs/data-model](docs/data-model.md) | 三张表逐字段、`time_src` 取值、Excel 四个工作表、`v2_results.jsonl`、环境变量 |
| [docs/limitations](docs/limitations.md) | 质量指标、已知问题、使用提醒、改进方向 |
| [samples/README](samples/README.md) | 三份样例各展示什么、黄金结果怎么用、如何添加样例 |
