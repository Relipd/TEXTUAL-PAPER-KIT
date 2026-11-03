# 中国 A 股年报 MD&A 语料工具包

> MDA-TEXTUAL-TOOL：爬虫获取上市公司年报并批量裁剪关键分部 PDF，同时采用定位识别各部分文本信息，以 SQLite 进行存储。

一个可复用的 Python 工具包：从巨潮资讯网 / 全国股转系统下载年报 PDF，定位并截取
「管理层讨论与分析」（MD&A）章节，切分为规范主题章节，清洗正文，存入 SQLite（含 FTS5 全文索引），
并按年导出 CSV / Stata 面板。

> 本仓库只包含**代码**，不包含任何年报 PDF、数据库、财务数据或导出结果。
> 代码来自一个 A 股 MD&A 文本研究项目，已剥离项目专用路径与数据。

---

## 功能

- **下载与规划**：两阶段架构——先规划「公司-年度 → 年报公告 URL」，再并发下载；支持断点续传、失败清单、代理开关、全局限速。
- **MD&A 定位**：书签 / 页首页标题 / 页内关键词三档起点；章节标题收尾；末页断句顺延；切片校验。
- **章节切分**：识别中文序号、括号序号、阿拉伯序号、`1.1`/`（1）` 等编号；按标题 bbox 切分正文；11 个规范章节槽位。
- **正文清洗**：去页眉页脚、页码、勾选框、单位行、表格碎片；保护含数字的叙述句；输出 `text` / `text_clean`。
- **存储**：SQLite `documents` / `sections` / `biz_sections` / `sections_merged` / `sections_fts`。
- **导出**：按年宽表 CSV、Stata 长表 `.dta`、章节索引 CSV。
- **可选**：2015–2020 年「公司业务概要」补抓与合并视图；北交所/新三板旧代码补取；异常文件重新定位。

---

## 目录结构

```
annual_report_mda_kit/
├─ mda_kit/
│  ├─ config.py              # 唯一配置入口（config.toml + 环境变量）
│  ├─ fetch_mda.py           # CNINFO 规划 + 下载 + MD&A 定位/截取
│  ├─ fix_upstream.py        # 异常文件重新下载/定位/重切（v4）
│  ├─ neeq_fill.py           # NEEQ（股转）补取
│  ├─ neeq_resume.py         # NEEQ 403 冷却后续传
│  ├─ build_sections_v3.py   # 章节切分 + SQLite 入库 + FTS
│  ├─ clean_text_v3.py       # 正文清洗（text_clean / clean_chars / is_na / table_only）
│  ├─ fetch_biz.py           # 2015–2020 公司业务概要补抓（可选）
│  ├─ build_biz_sections.py  # 业务概要二级切分（可选）
│  ├─ merge_biz.py           # 建立 sections_merged 视图（可选）
│  ├─ export_by_year.py      # 按年导出 CSV / .dta / 索引
│  ├─ rebuild_fts.py         # 重建 FTS5
│  ├─ analyze_by_year.py     # 按年覆盖率/质量报告
│  ├─ resolve_missing_biz.py # 北交所旧代码还原后补 URL（可选）
│  └─ __main__.py            # `python -m mda_kit <command>` 调度器
├─ docs/                     # 流程、数据模型、配置、限制
├─ examples/targets.example.csv
├─ tests/                    # 纯函数单元测试（不联网）
├─ config.example.toml
├─ requirements.txt
└─ pyproject.toml
```

---

## 安装

要求：Python **3.11+**（使用 `tomllib` 读取配置）。

```bash
git clone <your-repo-url>
cd annual_report_mda_kit

python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# 或：pip install -e ".[dev]"
```

---

## 配置

```bash
cp config.example.toml config.toml
```

`config.toml` 默认从仓库根目录读取；也可用环境变量 `MDA_KIT_CONFIG` 指定其他路径。
所有相对路径都相对于仓库根目录。完整说明见 [docs/configuration.md](docs/configuration.md)。

```toml
[paths]
data_dir  = "data"
mda_dir   = "data/MDA"
db_path   = "data/mda_sections.sqlite"
targets_csv = "data/targets.csv"
export_dir = "data/exports"

[network]
rps = 8.0       # API 限速
dl_rps = 2.0    # 每个下载进程的限速
trust_env = false
```

`data/` 已被 `.gitignore` 忽略；大数据请放在仓库外。

---

## 准备目标清单

`fetch_mda.py` 读取一个 CSV，至少包含三列（来自 CSMAR 或自建清单）：

| 列名 | 含义 | 示例 |
|---|---|---|
| `Stkcd` | 六位股票代码 | `000001` |
| `ShortName` | 股票简称 | `平安银行` |
| `Accper` | 统计截止日 | `2025/12/31` |

只处理 `Accper` 以 `12/31` 或 `12-31` 结尾的年度行。

参考：[examples/targets.example.csv](examples/targets.example.csv)

```csv
Stkcd,ShortName,Accper
000001,平安银行,2025/12/31
600519,贵州茅台,2025/12/31
```

放到 `data/targets.csv`（或修改 `targets_csv`）。

---

## 快速开始（MD&A 主链路）

```bash
# 1) 只规划，不下载：生成 data/MDA/_plan.csv
python -m mda_kit download --plan-only

# 2) 下载 + 定位 + 截取
python -m mda_kit download --planner search

# 3) 章节切分 + 入库（按年份；可 --codes/--from-file 增量）
python -m mda_kit sections 2025

# 4) 正文清洗
python -m mda_kit clean

# 5) 导出（默认 sections；不使用业务概要时无需 merge）
python -m mda_kit export --years 2025

# 6) 重建全文索引（可选）
python -m mda_kit fts

# 7) 质量/覆盖报告
python -m mda_kit analyze
```

输出：

```
data/exports/
  sections_wide_2025.csv      # 每份年报一行 × 16 列
  sections_index_2025.dta     # Stata 长表，文档 × 11 章节槽位
  _sections_2025.csv          # 章节索引（仅非空）
data/mda_sections.sqlite      # SQLite 主库
```

> 也可以直接运行模块：`python -m mda_kit.fetch_mda --help`、
> `python -m mda_kit.build_sections_v3 --help` 等。详细阶段说明见 [docs/pipeline.md](docs/pipeline.md)。

---

## 可选：公司业务概要（2015–2020）

2021 年披露准则修订前，「主要业务」「核心竞争力」属于独立的「第三节 公司业务概要」，
不在 MD&A 内。若需要这两章的早年覆盖：

```bash
python -m mda_kit fetch-biz --all --apply       # 下载业务概要章
python -m mda_kit biz-sections --apply          # 二级切分，写 biz_sections 表
python -m mda_kit merge                         # 建 sections_merged 视图
python -m mda_kit export --years all --source sections_merged
```

`fetch_biz.py` 依赖 `data/MDA/_manifest.csv`（`fetch_mda.py` 下载时生成）；
没有公告 URL 的北交所旧代码可用：

```bash
python -m mda_kit resolve --limit 10            # 探测
python -m mda_kit resolve --apply               # 写入 MDA_BIZ/_biz_urls_extra.csv
```

---

## 可选：异常文件重新定位 / NEEQ 补取

```bash
# 只探测，不覆盖
python -m mda_kit fix-upstream --pilot 20

# 按质量标记重下/重定位
python -m mda_kit fix-upstream --flags truncated --limit 50 --apply

# 股转系统补取（920xxx 转板前历史年报）
python -m mda_kit neeq --limit 10
python -m mda_kit neeq-resume
```

---

## 命令参考

| 命令 | 对应模块 | 作用 |
|---|---|---|
| `download` | `fetch_mda` | 规划 + 下载 + MD&A 截取 |
| `fix-upstream` | `fix_upstream` | 异常文件重新定位/重切 |
| `neeq` | `neeq_fill` | NEEQ 公告查询/下载 |
| `neeq-resume` | `neeq_resume` | NEEQ 限流冷却后续传 |
| `sections` | `build_sections_v3` | 章节切分 + SQLite 入库 |
| `clean` | `clean_text_v3` | 正文清洗 |
| `fetch-biz` | `fetch_biz` | 公司业务概要下载（可选） |
| `biz-sections` | `build_biz_sections` | 业务概要二级切分（可选） |
| `merge` | `merge_biz` | 建立合并视图（可选） |
| `export` | `export_by_year` | 按年导出 CSV/.dta/索引 |
| `fts` | `rebuild_fts` | 重建 FTS5 |
| `analyze` | `analyze_by_year` | 按年覆盖/质量报告 |
| `resolve` | `resolve_missing_biz` | 北交所旧代码补 URL（可选） |

完整参数：`python -m mda_kit <command> --help`。

---

## 数据模型

见 [docs/data-model.md](docs/data-model.md)。核心表：

- `documents`：每份 PDF 一行（代码、年度、页数、质量标记、MD&A 标题页等）。
- `sections`：每份文件 × 11 个章节槽位；`text` 原始文本、`text_clean` 清洗文本、`chars`/`clean_chars`、`is_na`、`table_only`、`boundary`。
- `biz_sections`：2015–2020 业务概要的 3 项（可选）。
- `sections_merged`：`sections` + 业务概要覆盖的合并视图（可选）。
- `sections_fts`：挂在 `sections.text` 的 FTS5 索引。

11 个章节键：`industry` / `business` / `core_comp` / `main_ops` / `non_main` /
`assets` / `invest` / `subsidiary` / `structured` / `outlook` / `research`。

质量标记：`ok` / `truncated` / `wrong_chapter` / `wrong_start` / `empty`。

---

## 设计要点

1. **两阶段规划/下载**：先确定「公司-年度 → 年报全文公告」，再下载；避免盲目翻页。
2. **站点适配**：请求带 Referer/UA、会话预热、令牌桶限速、指数退避；支持代理开关。
3. **MD&A 定位 v4**：书签（严格/泛化）→ 页首标题 → 页内关键词；收尾看任意「第X节/第X章」或页首章级标题；末页断句顺延；切片校验。
4. **章节切分**：只把「连续递增 ≥3」的编号样式当候选体系；按字号/重启次数分父子级；按标题 y 坐标切正文，避免同页重复。
5. **清洗**：数字密度规则只对「行末无句末标点」生效，避免误删「营业收入 1.2 亿元，同比增长 5%。」；连续短行块用于三线表去除。
6. **存储**：原始文本与清洗文本并存；`chars=0` 才是未识别，`chars>0 & clean_chars=0` 一定是 `is_na` 或 `table_only`。

---

## 测试

```bash
python -m unittest discover -s tests -v
```

测试只覆盖清洗、编号解析、章节归类等纯函数，不访问网络、不需要数据。

---

## 限制与合规

详见 [docs/limitations.md](docs/limitations.md)。要点：

- 仅支持有文本层的 PDF；扫描件需要 OCR（本仓库不包含 OCR 流程）。
- 巨潮/NEEQ 接口与页面可能变化；请先小样本探测，遵守网站服务条款与 robots 约定，限速访问。
- 仓库不附带任何年报 PDF、财务数据或第三方数据库；使用者需自行确认数据授权。
- 本工具面向 A 股年报的结构化抽取，**不是**情感分析/大模型工具；文本质量与源 PDF 排版强相关。
- Windows 路径已用 `os.path.join` 兼容；中文编码统一 UTF-8。

---

## License

代码采用 Apache License 2.0（见 [LICENSE](LICENSE)）。第三方数据的版权归原披露方所有，不在本仓库许可范围内。

## 致谢

本工具包由 A 股 MD&A 文本研究项目的爬虫、章节定位、清洗与存储脚本整理而来。
