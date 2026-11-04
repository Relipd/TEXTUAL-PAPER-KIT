# 复现流程

本文给出从零跑通「采集 → 切片 → 建库 → 抽取 → 回写 → 专项修复 → 导出 → 核对」的
完整步骤，以及常见故障处理。所有命令统一写成 `python -m audit_kit <cmd>`；
执行 `pip install -e .` 之后，`audit-kit <cmd>` 与之等价。

## 1. 环境准备

### 1.1 Python 与依赖

- Python **≥ 3.9**（建议 3.10–3.12）；
- 基础依赖：`pymupdf`、`openpyxl`、`requests`；
- 可选依赖：`ocrmypdf`（仅扫描件 OCR 补救需要）。

```bash
# 方式一：直接安装依赖
pip install -r requirements.txt

# 方式二：以可编辑模式安装本包（推荐，同时得到 audit-kit 命令）
pip install -e .
```

OCR 依赖单独安装：

```bash
pip install -r requirements-ocr.txt
# 或
pip install -e ".[ocr]"
```

Windows 控制台如出现中文乱码，先设置：

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

### 1.2 第三方名单 `FIN_Audit.csv`

采集、建库与「事务所年报口径」需要审计意见名单。该数据属第三方授权数据，
**仓库不附带**，需按 [data-source](data-source.md) 第 3 节的格式自备，
放到仓库根目录 `FIN_Audit.csv`，或用环境变量指定：

```bash
# Linux / macOS
export AUDIT_KIT_FIN_AUDIT_CSV=/path/to/FIN_Audit.csv
# Windows PowerShell（把 <你的路径> 换成实际位置）
$env:AUDIT_KIT_FIN_AUDIT_CSV = "<你的路径>\FIN_Audit.csv"
```

**只跑样例演示与单份 PDF 解析时不需要这份名单**，也不需要联网：

```bash
python -m audit_kit demo
python -m audit_kit parse samples/pdf/000002_1225067818.PDF --json
```

### 1.3 Tesseract 语言包（仅 OCR）

`ocr` 命令调用 OCRmyPDF，需要 Tesseract 及中文语言包：

1. 安装 Tesseract 与 Ghostscript（或使用 OCRmyPDF 的 `--output-type pdf --optimize 0`
   内置后端）；
2. 下载 `chi_sim.traineddata`（推荐 `tessdata_best`），放到
   `ocr/tessdata/`（仓库根目录下，可被 `.gitignore` 忽略），程序会自动设置
   `TESSDATA_PREFIX`；也可安装到系统 Tesseract 的 tessdata 目录。

### 1.4 数据目录

运行命令前不需要手动建目录，`audit_kit.paths` 会按需创建：

```
data/
├─ pipeline.db            采集任务表
├─ audit_contracts.db     主库（documents / persons / documents_fts）
├─ raw/                   原始 PDF 缓存（可按公告 ID 重建）
├─ pdf/<年份>/            关键页切片
├─ text/<年份>/           切片文本
├─ ocr/<年份>/            OCR 结果
└─ v2_results.jsonl       抽取中间结果
output/                   导出的 Excel（不入库）
```

路径可用环境变量覆盖：`AUDIT_KIT_DATA_DIR`、`AUDIT_KIT_DB`、`AUDIT_KIT_FIN_AUDIT_CSV`、`AUDIT_KIT_OUTPUT_DIR`
（见 [data-model](data-model.md) 第 4 节）。

## 2. 分步复现

### 第 0 步：自检

```bash
python -m audit_kit --version          # 版本号
python -m audit_kit --help             # 命令清单
python -m audit_kit demo               # 离线核对 3 份样例，期望 3/3 PASS
```

### 第 1 步：采集（联网）

三阶段任务流水线，阶段之间以 `data/pipeline.db` 解耦，可单独重跑、断点续传：

```bash
# 1.1 逐公司检索公告清单并归类（只补尚未入库的公司）
python -m audit_kit plan --workers 3

# 1.2 并发下载原始 PDF（只处理 status='new'）
python -m audit_kit fetch --workers 16

# 1.3 截取关键页 + 导出切片文本（只处理 status='fetched'）
python -m audit_kit slice --workers 8

# 查看各阶段进度
python -m audit_kit status
```

说明：

- `plan` 以 `FIN_Audit.csv` 中「2020–2025 会计年度有审计记录」的公司为名单，
  跳过任务表里已有记录的公司，因此可反复执行做增量；
- 检索接口有全局节流（约 2 次/秒），遇 403 会自动熔断并退避；并发数可按网络情况
  调低（如 `--workers 2`）；
- 单公司试跑可用 `python -m audit_kit crawl 000002`（检索 + 下载 + 切片），
  支持多个代码与 `--limit` 限量；`--start` 默认 2020-01-01，`--end` 默认当天；
- 如已有历史 `data/notices.jsonl`，可用 `python -m audit_kit import` 导入任务表。

### 第 2 步：建库

```bash
python -m audit_kit build
```

由任务表创建 `data/audit_contracts.db`（`documents` / `persons` / `documents_fts`），
并写入公告基础字段与年报口径事务所。常用参数：

| 参数 | 作用 |
|---|---|
| `--limit N` | 只处理前 N 条，用于小样本试跑 |
| `--rebuild` | 删除现有主库后重建 |
| `--dry-run` | 只统计、不写库（内存库演练） |

### 第 3 步：全量抽取与回写

```bash
# 只抽取：产出 data/v2_results.jsonl
python -m audit_kit extract --workers 8

# 只回写：读 v2_results.jsonl 重写 documents/persons（自动备份主库）
python -m audit_kit apply

# 一步到位（边抽边写）
python -m audit_kit extract --workers 8 --apply
```

回写前会把主库备份为 `data/audit_contracts_bak_<YYYYMMDD_HHMM>.db`；
`--out` 可把抽取结果写到其它路径（默认 `data/v2_results.jsonl`）。
全量抽取在 8 进程下约 5 分钟（本地参考值），实际取决于 CPU 与磁盘。

### 第 4 步：专项修复（有对应问题时执行）

```bash
# 同名公告撞名：按 announcementId 重新切片并重抽（写主库）
python -m audit_kit collisions

# 扫描件 OCR：先试跑少量，再加 --apply 回写
python -m audit_kit ocr --limit 6
python -m audit_kit ocr --apply

# 正式公告优先去重 + 跨公告回填时间节点（写主库）
python -m audit_kit prefer-formal

# 按审计年度重算事务所（年报口径）（写主库）
python -m audit_kit firms
```

建议顺序：`collisions` → `ocr` → `prefer-formal` → `firms`。这些命令会直接写主库
（`ocr` 需加 `--apply`），执行前请自行备份 `data/audit_contracts.db`。OCR 结果缓存在
`data/ocr/`，重复执行不会重复识别；`data/raw/`（约 12 GB）可在全流程完成后清理。

### 第 5 步：导出与核对

```bash
# 导出四个工作表：人员明细 / 公告明细 / 公司年度面板 / 统计
python -m audit_kit export
# 默认 output/审计机构聘任公告_项目成员信息.xlsx，可用 --out 指定

# 生成 300 条人工核对样本 + 400 条疑点清单
python -m audit_kit sample
# 默认 output/审计机构聘任公告_核对样本.xlsx，可用 --out 指定

# 诊断
python -m audit_kit gaps     # 无人员公告分类、角色缺失、节点归因
python -m audit_kit stats    # 指标快照
```

### 第 6 步：回归样例

```bash
# 与 samples/expected/*.json 逐项比对 roles / persons / src / note
python -m audit_kit demo
# 期望输出：样例 3 份，通过 3，失败 0
```

`demo` 不联网、不读数据库、不需要 `FIN_Audit.csv`，适合在 CI 与本地安装后自检。

## 3. 完整命令串

```bash
# 0. 安装
pip install -e .

# 1. 采集（联网；已有数据可跳过）
python -m audit_kit plan --workers 3
python -m audit_kit fetch --workers 16
python -m audit_kit slice --workers 8
python -m audit_kit status

# 2. 建库
python -m audit_kit build

# 3. 抽取与回写
python -m audit_kit extract --workers 8
python -m audit_kit apply

# 4. 专项修复
python -m audit_kit collisions
python -m audit_kit ocr --apply
python -m audit_kit prefer-formal
python -m audit_kit firms

# 5. 导出与核对
python -m audit_kit export
python -m audit_kit sample
python -m audit_kit demo
python -m audit_kit gaps
python -m audit_kit stats
```

## 4. 增量更新

数据更新时不需要全量重跑，各阶段都按任务状态断点续传：

1. `plan`：只检索任务表中还没有记录的公司；已入库公司自动跳过；
2. `fetch`：只下载 `status='new'` 的公告；失败任务重试 2 次后标 `failed`；
3. `slice`：只处理 `status='fetched'`；
4. `build`：重建/补齐主库基础行（不要用 `--rebuild`，以免清空历史）；
5. `extract` + `apply`：重跑全部公告的抽取并回写（约 5 分钟，最稳妥）；
6. `ocr` 按最新候选重新筛选；`firms` 在审计年度修正后重算；
7. `export` / `sample` 重新生成交付物。

判断某条数据卡在哪一步：查 `data/pipeline.db` 的 `tasks` 表（`status`、`tries`、`note`），
再核对 `data/pdf/` 切片是否完整。

## 5. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| 检索返回 403、大量超时或 504 | 触发巨潮限流/封禁 | 程序已内置全局节流与 403 熔断退避；进一步调低 `--workers`、隔一段时间再跑；`fetch` 可反复执行只补 `status='new'` |
| `plan`/`crawl` 提示找不到公司与代码 | 缺少 `FIN_Audit.csv` 或格式不符 | 按 [data-source](data-source.md) 第 3 节核对「3 行表头 + 列位」；或用 `AUDIT_KIT_FIN_AUDIT_CSV` 指定 |
| `ocr` 报找不到语言包 / `chi_sim` | 缺 Tesseract 中文语言包 | 下载 `chi_sim.traineddata` 放到 `ocr/tessdata/` 或系统 tessdata；确认 Tesseract 可执行 |
| `ocr` 报 Ghostscript 相关错误 | OCRmyPDF 默认依赖 Ghostscript | 安装 Ghostscript，或使用 `--output-type pdf --optimize 0`（程序默认即此配置） |
| `ImportError: No module named ocrmypdf` | 未装可选 OCR 依赖 | `pip install -r requirements-ocr.txt` |
| `sqlite3.OperationalError: no such module: fts5` | 当前 Python 的 SQLite 未编译 FTS5 | 换用 python.org / 发行版自带 FTS5 的 Python；先自检：`python -c "import sqlite3; sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)')"` |
| Windows 报路径过长 | `data/pdf/<年份>/<长标题>.pdf` 路径超 260 字符 | 开启 Windows 长路径支持，或用 `AUDIT_KIT_DATA_DIR` 指向更短的目录 |
| 控制台中文乱码 | Windows 默认代码页 | 设置 `PYTHONIOENCODING=utf-8` |
| 切片只有「N页(整篇)」或抽取结果为空 | 公告无文字层 / 关键页未命中 | 属正常兜底；对扫描件运行 `ocr`，对同名公告运行 `collisions` |
| 主库被写坏 / 回写前想回滚 | 专项修复直接写库，且不自动备份 | `apply` 回写前会自动备份；`collisions` / `ocr --apply` / `prefer-formal` / `firms` 不备份，执行前请手动复制主库；也可用 `data/audit_contracts_bak_*.db` 恢复 |

## 6. 合规与资源提示

- 采集请遵守巨潮资讯网的服务条款与访问频率限制，不要关闭节流或高并发压测；
- 公告原文版权归披露主体与巨潮资讯网所有，本仓库不分发全量 PDF；
- 全量原始 PDF 约 12 GB；切片、文本与 OCR 结果另有数 GB，请预留磁盘空间；
- 第三方审计意见名单（`FIN_Audit.csv`）需自行取得授权，不要将其提交到公开仓库。
