# TEXTUAL-PAPER-KIT

**面向中国上市公司文本实证研究的可复用文本工具包合集。**

从「公开披露文本」到「可回归的数据集」：采集公告/年报 → 版面与章节解析 → 结构化入库
（SQLite + FTS5）→ 导出 CSV / Stata / Excel，每一步都可复现、可逐条复核。
从方法到工具，为论文简化工作量

## Abstract

`TEXTUAL-PAPER-KIT` is a collection of reusable, rule-based Python toolkits that turn
Chinese listed-firm disclosures into research-ready datasets: downloading filings from
CNINFO, reconstructing PDF layout or chapter structure, extracting structured fields,
and storing them in SQLite (with FTS5 full-text search) for export to CSV / Stata /
Excel. Each kit is self-contained, ships offline unit tests and a small set of public
sample documents, and contains **code only** — no full corpus, no licensed third-party
data, and no LLM-generated labels.

## 包含的 kit

| Kit | 研究对象 | 主要产出 | Python | License |
|---|---|---|---|---|
| [annual_report_mda_kit](annual_report_mda_kit/) | 年报「管理层讨论与分析」(MD&A) 章节 | 11 个主题章节文本、SQLite、CSV / Stata `.dta` | 3.11+ | [Apache-2.0](annual_report_mda_kit/LICENSE) |
| [audit_engagement_kit](audit_engagement_kit/) | 续聘 / 变更 / 聘任会计师事务所公告 | 项目组成员与四个关键时间节点、SQLite、Excel 四个工作表 | 3.9+ | [MIT](audit_engagement_kit/LICENSE) |

两套 kit 的共同设计：

- **规则化、确定性抽取**：版面坐标 / 编号样式 / 固定表述规则，不使用 LLM，同一输入结果稳定；
- **可复核**：保留抽取来源标签（`src` / `time_src`）与原文片段，便于抽样核对；
- **结构化存储**：SQLite + FTS5 全文索引，支持按代码、姓名、事务所检索原文；
- **分阶段、可增量**：采集/解析/入库/修复/导出解耦，支持断点续跑与只补失败项；
- **只带代码**：数据、PDF、数据库与第三方授权数据都不入库。

## 目录结构

```
TEXTUAL-PAPER-KIT/
├─ README.md                     本文件
├─ CITATION.cff                  整仓引用信息
├─ LICENSE-NOTICE.md             各 kit 的许可说明（整仓无统一 LICENSE）
├─ .github/workflows/ci.yml      根级 CI：分别测试两个 kit
├─ annual_report_mda_kit/        年报 MD&A 工具包（Apache-2.0）
│  ├─ mda_kit/                   下载、章节定位、切分、清洗、入库、导出
│  ├─ docs/                      流程、数据模型、配置、限制
│  ├─ examples/                  目标清单样例
│  ├─ tests/                     纯函数单元测试（不联网）
│  └─ config.example.toml
└─ audit_engagement_kit/         审计续聘公告工具包（MIT）
   ├─ audit_kit/                 采集、切片、版面抽取、元字段、修复、导出
   ├─ docs/                      口径、方法、复现、数据字典、限制
   ├─ samples/                   3 份公开样例公告 + 黄金结果
   ├─ tests/                     离线单元测试
   └─ pyproject.toml
```

## 快速开始

每个 kit 相互独立，只安装自己要用的那个。

### annual_report_mda_kit（年报 MD&A）

```bash
cd annual_report_mda_kit
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml      # 按需改路径/限速
python -m mda_kit download --plan-only  # 先规划，不下载
python -m mda_kit download              # 下载 + MD&A 定位 + 截取
python -m mda_kit sections 2025         # 章节切分 + 入库
python -m mda_kit clean                 # 正文清洗
python -m mda_kit export --years 2025   # 导出 CSV / .dta
python -m unittest discover -s tests -v # 离线自检
```

详见 [annual_report_mda_kit/README.md](annual_report_mda_kit/README.md)。

### audit_engagement_kit（审计续聘公告）

```bash
cd audit_engagement_kit
pip install -r requirements.txt
python -m audit_kit demo                              # 离线自检（3 份样例）
python -m audit_kit parse samples/pdf/000002_1225067818.PDF --json
python -m audit_kit plan --workers 3                  # 检索公告清单（联网）
python -m audit_kit fetch --workers 16                # 下载原始 PDF
python -m audit_kit slice --workers 8                 # 截取关键页 + 文本
python -m audit_kit build                             # 建 SQLite 主库
python -m audit_kit extract --workers 8               # 抽取人员与时间节点
python -m audit_kit apply                             # 回写主库（自动备份）
python -m audit_kit export                            # 导出 Excel
```

详见 [audit_engagement_kit/README.md](audit_engagement_kit/README.md)。

## 设计原则

1. **先结构，后字段**。公告与年报的 PDF 文本流会被表格换行拆散，纯正则不可靠；两套 kit 都先重建
   版面（视觉行 → 单元格）或章节结构，再在结构上取字段。
2. **宁缺毋滥**。只取原文明确写出的值，不推算、不插值；公告未披露则留空，并保留来源标签。
3. **可复核性优先于覆盖率**。每个字段都能回溯到原文片段或单元格；核对样本与疑点清单是交付物的一部分。
4. **数据与代码分离**。仓库只放代码、文档与少量公开样例；全量数据在本地 `data/`（已 gitignore）。
5. **合规访问**。内置限速与退避，请勿关闭节流或压测数据源；遵守巨潮资讯网等站点的服务条款。

## 数据与合规

- 数据来源为巨潮资讯网（CNINFO）等平台的**公开披露**文件，版权归披露主体与原平台所有。
- 本仓库**不包含**：公告/年报 PDF、SQLite 数据库、导出的 CSV/Excel/Stata、第三方授权数据
  （如 CSMAR 审计意见名单 `FIN_Audit.csv`）。需要时请自行获取并遵守其授权。
- 因采集、使用或再分发数据产生的合规责任由使用者自行承担；代码许可见下节。

## License

本仓库**不设统一 LICENSE**，各 kit 沿用各自目录下的许可：

| 目录 | License |
|---|---|
| `annual_report_mda_kit/` | Apache License 2.0 |
| `audit_engagement_kit/` | MIT |

详见 [LICENSE-NOTICE.md](LICENSE-NOTICE.md)。第三方数据不在代码许可范围内。

## 引用

若这些工具或由其生成的数据对你的研究有帮助，请引用：

```bibtex
@software{textual_paper_kit,
  title   = {TEXTUAL-PAPER-KIT: reusable textual-data toolkits for Chinese listed-firm research},
  author  = {{TEXTUAL-PAPER-KIT contributors}},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/Relipd/TEXTUAL-PAPER-KIT}
}
```

机器可读版本见 [CITATION.cff](CITATION.cff)。

## 新增一个 kit 的约定

- 独立目录（`*_kit/`），自带 `README.md`、`docs/`、`tests/`、`requirements.txt` 或 `pyproject.toml`、`LICENSE`；
- 自带 `.gitignore`，确保数据/PDF/数据库/导出物不入库；
- 根级 `README.md` 的 kit 表格与 `.github/workflows/ci.yml` 各加一行/job；
- 测试必须离线、可在干净环境运行；README 提供一条「无需数据即可自检」的命令。

## 致谢

两个 kit 均来自作者的 A 股文本实证研究项目：annual_report_mda_kit 源自年报 MD&A 语料构建流程，
audit_engagement_kit 源自会计师事务所聘任公告语料构建流程。
如需现有数据结果，可邮件联系作者