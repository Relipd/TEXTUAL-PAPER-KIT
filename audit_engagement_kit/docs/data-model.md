# 数据字典

本文定义语料库的落库结构、Excel 导出字段与中间结果格式。口径含义见
[data-source](data-source.md)。

## 1. SQLite 主库

默认路径 `data/audit_contracts.db`（可用环境变量 `AUDIT_KIT_DB` 覆盖）。三张表：
`documents`（公告）、`persons`（人员）、`documents_fts`（FTS5 全文索引）。

- 建表 DDL 见 `audit_kit/store.py` 的 `DDL`；
- 主键为自增整数，公告与人员通过 `persons.doc_id = documents.id` 关联；
- 文本字段空值在库里表现为 `NULL` 或空字符串 `''`，查询时建议两者都判空；
- 建议以只读方式打开（`sqlite3.connect("file:data/audit_contracts.db?mode=ro", uri=True)`）。

### 1.1 `documents`（公告表）

| 字段 | 类型 | 取值 / 含义 |
|---|---|---|
| `id` | INTEGER PK | 公告自增主键，与 `v2_results.jsonl` 的 `id` 对应 |
| `code` | TEXT | 6 位证券代码，如 `000002` |
| `name` | TEXT | 证券简称，如 `万科A` |
| `disc_year` | INTEGER | 披露年（公告发布年份），2020–2026 |
| `audit_year` | INTEGER | 审计年度；优先取标题年份，约束在 `[披露年-2, 披露年+1]`；无法确定时为 `NULL` |
| `type` | TEXT | 公告类型：`变更` / `续聘` / `聘任` |
| `date` | TEXT | 披露日期，`YYYY-MM-DD` |
| `title` | TEXT | 公告标题原文 |
| `firm_notice` | TEXT | 事务所（公告口径）：公告中拟聘任的会计师事务所名称，经机构名录最长匹配规范化 |
| `firm_official` | TEXT | 事务所（年报口径）：`FIN_Audit` 中该（证券代码, 审计年度）的境内审计事务所 |
| `prev_firm` | TEXT | 前任事务所，仅变更类公告披露；稀疏（见质量指标） |
| `tenure` | TEXT | 公告披露的连续服务年限，纯数字字符串如 `6` |
| `fee` | TEXT | 公告披露的审计收费，含单位，如 `41万元`、`5983.39万元` |
| `url` | TEXT | 公告原文 PDF 的巨潮链接 |
| `pdf` | TEXT | 关键页切片 PDF 的本地路径（公开仓库不含数据文件） |
| `txt` | TEXT | 切片文本的本地路径（公开仓库不含数据文件） |
| `pages` | TEXT | 切片页数，如 `4页`；整篇保留时形如 `12页(整篇)` |
| `src` | TEXT | 抽取通道，见 1.4 |
| `note` | TEXT | 抽取异常备注（如 `open:...`、`no_text_layer`）；正常为空 |
| `has_h` | INTEGER | 是否含 H 股人员：`1` / `0`；公开版 `apply` 重写后统一为 `0`（抽取引擎只输出 A 股人员，历史全量快照中 18 份公告为 `1`） |

索引：`idx_doc_code(code, disc_year)`、`idx_doc_firm(firm_official)`。

### 1.2 `persons`（人员表）

| 字段 | 类型 | 取值 / 含义 |
|---|---|---|
| `id` | INTEGER PK | 人员自增主键 |
| `doc_id` | INTEGER | 外键 → `documents.id` |
| `market` | TEXT | `A` / `H`；公开版抽取引擎只写 `A`，`H` 仅见于历史全量快照（兼容保留） |
| `role` | TEXT | `项目合伙人` / `签字注册会计师` / `质量控制复核人` |
| `name` | TEXT | 姓名，2–4 个汉字；已通过姓氏白名单、停用词、证券简称黑名单、否定式上下文校验 |
| `concurrent` | INTEGER | `1` = 该签字注册会计师同时是本公告的项目合伙人（兼任）；`0` = 否 |
| `become_cpa` | TEXT | 成为注册会计师：`YYYY` 或 `YYYY-MM`，未披露为 `NULL` |
| `start_listed` | TEXT | 开始从事上市公司审计：同上 |
| `start_firm` | TEXT | 开始在本所执业：同上 |
| `start_company` | TEXT | 开始为本公司提供审计服务：同上 |
| `time_src` | TEXT | 时间节点/姓名来源标签，见 1.3 |

索引：`idx_person(name, role)`、`idx_person_doc(doc_id)`。
H 股人员只记录 `role` / `name`，四个时间节点通常为空。公开版 `apply` 把 `persons`
整体重写为本次抽取的 A 股记录（`market='A'`），**不保留**历史库中由早期流程写入的
H 股人员。

### 1.3 `time_src` 取值

`time_src` 记录该条人员记录的姓名与节点来自哪条通道。基础值与后缀可组合，
例如 `table+ctx`、`prose+bf`、`table(*)+bf`。

| 取值 | 含义 |
|---|---|
| `table` | 行式表格（角色在左、姓名在右，右侧为年份列） |
| `table_t` | 田字表格（角色为表头、姓名为行标签，按列对齐） |
| `prose` | 叙述式段落（「项目合伙人：张三，2012 年成为……」） |
| `secfallback` | 项目信息段落锚点兜底：只取到姓名，节点靠后续回填 |
| `text` | PDF 文本流兜底：只取到姓名 |
| `+prose` | 在上述来源基础上，用叙述式补齐了部分时间节点（如 `table+prose`） |
| `+ctx` | 用「姓名锚定上下文」（姓名前后 240 字）补齐了部分节点 |
| `+bf` | 跨公告回填：用同公司其它公告中同名人员的记录补齐（正式公告优先） |
| `(*)` | 公告未单列签字注册会计师，沿用项目合伙人本人（该人 `concurrent=1`） |

### 1.4 公告级 `src` 取值

`documents.src` 是公告命中的抽取通道集合，多通道以 `+` 连接、按字典序排列：

| 取值 | 含义 |
|---|---|
| `layout` | 版面通道取到人员 |
| `prose` | 叙述式通道取到人员 |
| `rawtext` | 文本流通道取到姓名 |
| `secfallback` | 项目信息段落锚点兜底取到姓名 |
| `concurrent` | 触发「合伙人兼任签字人」补充 |
| `ocr` | 该公告经 OCR 补文字层后重抽 |
| `scan` | 无文字层，未做 OCR 前无法抽取 |
| `error` | 打开/解析失败 |
| `none` | 未取到人员且无其他通道命中 |

例：`layout+prose` 表示版面取到人员、叙述式补充了节点；`rawtext` 表示整份公告只走了
文本流兜底（样例 `601398_ah` 即属此类）。

### 1.5 `documents_fts`（全文索引）

```sql
CREATE VIRTUAL TABLE documents_fts USING fts5(
    title, content, code UNINDEXED, doc_id UNINDEXED, tokenize='unicode61'
);
```

| 列 | 含义 |
|---|---|
| `title` | 公告标题 |
| `content` | 切片文本前 60000 字符 |
| `code` | 证券代码（不建索引，仅随结果返回） |
| `doc_id` | → `documents.id`（不建索引） |

常用查询：

```sql
-- 某公司各年度项目组成员与四个时间节点
SELECT d.disc_year, p.role, p.name, p.concurrent,
       p.become_cpa, p.start_listed, p.start_firm, p.start_company
FROM persons p JOIN documents d ON d.id = p.doc_id
WHERE d.code = '000002'
ORDER BY d.disc_year, p.role;

-- 按姓名追踪某人的项目角色
SELECT d.code, d.name, d.disc_year, p.role
FROM persons p JOIN documents d ON d.id = p.doc_id
WHERE p.name = '张三'
ORDER BY d.disc_year;

-- 全文检索原文（返回高亮片段）
SELECT code, snippet(documents_fts, 1, '[', ']', '...', 10)
FROM documents_fts
WHERE documents_fts MATCH '连续服务';
```

## 2. Excel 导出（4 个工作表）

由 `python -m audit_kit export` 从主库生成，默认输出
`output/审计机构聘任公告_项目成员信息.xlsx`。四个工作表的关系：

```
人员明细（长表，一人一行） ──┐
公告明细（一份公告一行）   ──┼── 统计（指标汇总）
公司年度面板（去重后）     ──┘
```

### 2.1 人员明细（19 列，长表）

| 列 | 来源 | 说明 |
|---|---|---|
| 证券代码 / 证券简称 | `documents.code/name` | |
| 披露年 / 审计年度 | `documents.disc_year/audit_year` | |
| 职位 | `persons.role` | 三类角色 |
| 姓名 | `persons.name` | |
| 兼任 | `persons.concurrent` | 1 = 合伙人兼任签字人 |
| 会计师事务所（公告口径） | `documents.firm_notice` | |
| 会计师事务所（年报口径） | `documents.firm_official` | 主用字段 |
| 成为注册会计师 | `persons.become_cpa` | `YYYY` / `YYYY-MM` |
| 开始从事上市公司审计 | `persons.start_listed` | 同上 |
| 开始在本所执业 | `persons.start_firm` | 同上 |
| 开始为本公司提供审计服务 | `persons.start_company` | 同上 |
| 时间节点来源 | `persons.time_src` | 见 1.3 |
| 市场 | `persons.market` | A / H |
| 公告类型 / 公告日期 / 公告标题 / 公告链接 | `documents` | 便于回溯原文 |

一行 = 一名项目组成员在一份公告中的记录；同一人出现在多份公告中会有多行。

### 2.2 公告明细（22 列）

| 列 | 来源 | 说明 |
|---|---|---|
| 证券代码 / 证券简称 / 披露年 / 审计年度 | `documents` | |
| 公告类型 / 披露日期 | `documents` | |
| 项目合伙人 / 签字注册会计师 / 质量控制复核人 | `persons` 按角色聚合 | 多人以「、」连接 |
| H 股项目合伙人 / H 股签字注册会计师 / H 股复核人 | `persons.market='H'` | 多数为空 |
| 事务所（公告）/ 事务所（数据库） | `firm_notice` / `firm_official` | 双口径 |
| 前任事务所 / 连续年限 / 审计收费 | `documents` | 稀疏字段 |
| 抽取来源 / 关键页 | `src` / `pages` | |
| 公告标题 / 公告链接 / PDF 文件 | `documents` | |

一行 = 一份公告（不去重，31603 行）。

### 2.3 公司年度面板（16 列）

字段同公告明细的精简版，但**按 `(证券代码, 披露年)` 去重**：同一事项的多份公告
按「变更 > 续聘 > 聘任」优先，同期取最早披露的一份。一行 = 一家公司一个披露年
（29756 行），适合做年度面板回归。

### 2.4 统计（指标 / 数值）

预先计算的汇总：公告总数、公司数、面板行数、三字段齐全数与齐全率、各角色非空率、
A+H 公告数、按披露年的公告数与齐全率、公告类型分布、抽取来源分布、事务所两口径
非空数、审计年度/连续年限/审计收费非空数、人员记录数、四个时间节点覆盖率。

## 3. `v2_results.jsonl`（全量抽取中间结果）

`python -m audit_kit extract` 的输出，默认 `data/v2_results.jsonl`（`--out` 可覆盖），
每行一份公告，是「抽取 → 回写主库」之间的中间态。结构：

```json
{
  "id": 12345,
  "roles": {
    "项目合伙人": ["杨誉民"],
    "签字注册会计师": ["许湘照"],
    "质量控制复核人": ["黄天义"]
  },
  "persons": [
    {
      "role": "项目合伙人",
      "name": "杨誉民",
      "times": {
        "become_cpa": "2007",
        "start_listed": "2004",
        "start_firm": "",
        "start_company": ""
      },
      "years": [],
      "source": "prose+ctx"
    }
  ],
  "src": "prose",
  "note": "",
  "meta": {
    "audit_year": "2026",
    "firm_notice": "德勤华永会计师事务所(特殊普通合伙)",
    "prev_firm": "",
    "tenure": "",
    "fee": "1388万元"
  },
  "scores": {}
}
```

| 字段 | 说明 |
|---|---|
| `id` | `documents.id`，`apply` 依此定位公告 |
| `roles` | 按角色聚合的姓名列表（与 `persons` 冗余，便于快速查看） |
| `persons[].role/name` | 职位与姓名 |
| `persons[].times` | 四个时间节点；无值用空字符串 |
| `persons[].years` | 表格中原始年份单元格列表，供审计/调试；**不入库** |
| `persons[].source` | 人员来源标签，回写后即 `persons.time_src` |
| `src` / `note` | 公告级抽取通道 / 备注 |
| `meta` | 公告级元字段；解析失败时可能为 `{"err": "..."}` |
| `scores` | 预留字段，当前为空对象 |

`apply` 回写时会保留公告原有的 `code/name/type/date/title/url/pdf/txt/pages` 等字段，
只更新 `audit_year`、事务所、前任、年限、收费、`src`、`note`、`has_h`，并重建
`documents` 与 `persons` 两张表（`has_h` 统一写 `0`）；回写前自动备份主库为
`data/audit_contracts_bak_<YYYYMMDD_HHMM>.db`。`documents_fts` 由 `build` 阶段写入
（每条取切片文本前 60000 字符），`apply` 不重写其正文。

## 4. 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `AUDIT_KIT_DATA_DIR` | 数据根目录 | `<repo>/data` |
| `AUDIT_KIT_DB` | 主库路径 | `<data>/audit_contracts.db` |
| `AUDIT_KIT_FIN_AUDIT_CSV` | 审计意见名单路径 | `<repo>/FIN_Audit.csv` |
| `AUDIT_KIT_OUTPUT_DIR` | 导出目录 | `<repo>/output` |

其余路径（原始 PDF、切片、文本、OCR、`pipeline.db`、`v2_results.jsonl`）由数据根目录
派生，定义见 `audit_kit/paths.py`。
