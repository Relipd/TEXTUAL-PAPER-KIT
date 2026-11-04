# 限制、合规与已知问题

## 数据与合规

- 本仓库只包含代码，不附带年报 PDF、数据库、财务数据或导出文件。
- 年报 PDF 的版权归上市公司/披露平台；下载与使用须遵守巨潮资讯网、全国股转系统的服务条款、
  robots 约定与所在机构的数据合规要求。
- 请控制请求频率（默认 `rps=8`、`dl_rps=2`），不要并发轰炸站点。
- 不要把下载的 PDF 或数据库上传到公开仓库；`.gitignore` 已默认忽略 `data/`、`*.pdf`、`*.sqlite`。

## 适用范围

- 面向中国 A 股年报（含北交所/新三板转板公司）的 MD&A 抽取；
- 需要 PDF 有文本层；扫描件请先 OCR（本仓库不含 OCR 流程）；
- 文本规则针对中文年报排版，其他语言/市场需调整 `TARGETS` 与清洗规则。

## 已知限制

1. **站点接口易变**：巨潮 `hisAnnouncement/query`、`fulltextSearch/full` 与 NEEQ
   `companyAnnouncement.do` 可能调整参数/风控；先小样本探测，必要时改 `fetch_mda.py`。
2. **定位是启发式**：书签/页首标题/关键词三档定位无法保证 100%；质量标记 `truncated` /
   `wrong_chapter` / `wrong_start` / `empty` 用于标记残余异常，需人工抽查。
3. **章节只覆盖 11 个规范章节**：MD&A 中的其他小节（风险因素、经营讨论等）不在 `sections` 内；
   需要完整 MD&A 文本时，请从 `MDA/{year}/*.pdf` 重新提取全文。
4. **表格清洗是启发式**：三线表/跨页表格仍可能残留；`table_only` 表示整节基本是表格。
5. **FTS 分词限制**：`unicode61` 对连续中文按整词处理，子串检索请用 `LIKE`；
   `sections_fts` 只覆盖 `sections.text`，不含可选的 `biz_sections`。
6. **`sections_merged` 不是 MD&A**：2015–2020 年它会把「公司业务概要」的两章合并进来；
   研究 MD&A 时请用 `sections`。
7. **不包含财务/行情数据**：`Size`、`Roa`、`Ret` 等控制变量需自行从 CSMAR/Wind 等获取。
8. **Windows/路径**：包内路径均通过 `os.path.join`；`config.toml` 中可用正斜杠或双反斜杠。
9. **并发/内存**：全量 PDF 下载与切分较耗 I/O；建议先小样本（`--limit`/`--sample`）验证。

## 常见问题

**Q: 运行 `download` 报 targets CSV 找不到？**
把清单放到 `data/targets.csv`，或在 `config.toml` 修改 `paths.targets_csv`。

**Q: 规划结果为空 / 403？**
先确认目标 CSV 的 `Accper` 为 `YYYY/12/31`；降低 `rps`；检查代理（默认 `trust_env=false`）；
必要时手工浏览巨潮页面确认接口仍可用。

**Q: `sections` 全是空行？**
PDF 未下载、年份目录不对，或 PDF 是扫描件；检查 `data/MDA/{year}/` 与 `documents` 表。

**Q: 导出报 `no such table: sections_merged`？**
默认数据源是 `sections`；只有跑过 `merge`（业务概要可选链路）后才用 `--source sections_merged`。
