# 配置说明

配置按以下顺序解析（后者覆盖前者）：

1. 代码内默认值（`mda_kit/config.py`）；
2. 仓库根目录的 `config.toml`（或 `MDA_KIT_CONFIG` 指向的文件）；
3. 环境变量 `MDA_DATA_DIR`（只覆盖 `data_dir`）。

相对路径一律相对仓库根目录；绝对路径原样使用。

## [paths]

| 键 | 默认 | 说明 |
|---|---|---|
| `data_dir` | `data` | 数据根目录；可指向仓库外的大盘 |
| `mda_dir` | `{data_dir}/MDA` | 年报切片 PDF |
| `biz_dir` | `{data_dir}/MDA_BIZ` | 业务概要切片（可选） |
| `db_path` | `{data_dir}/mda_sections.sqlite` | SQLite 库 |
| `targets_csv` | `{data_dir}/targets.csv` | 公司-年度清单 |
| `export_dir` | `{data_dir}/exports` | 导出文件目录 |

若只改 `data_dir`，`mda_dir` 等未显式配置的路径会自动跟随。

## [network]

| 键 | 默认 | 说明 |
|---|---|---|
| `rps` | 8.0 | CNINFO 公告 API 全局限速（请求/秒） |
| `dl_rps` | 2.0 | 每个下载进程的 PDF 下载限速 |
| `trust_env` | false | 是否使用环境变量中的 HTTP(S)_PROXY；默认绕开失效代理 |
| `user_agent` | Chrome UA | 可选，覆盖默认 UA |

站点有风控，**不要**把 `rps`/`dl_rps` 调得过高；遇到 403 先降速、检查预热与代理。

## [extract]

| 键 | 默认 | 说明 |
|---|---|---|
| `strict_boundary` | false | 预留给更严格的边界模式，当前不建议改 |

## 命令行覆盖

多数脚本支持命令行覆盖单个参数，例如：

```bash
python -m mda_kit sections 2025 --db D:\corpus\mda.sqlite --sample 200
python -m mda_kit export --years 2020,2021 --source sections
```

路径优先级：命令行 > `config.toml` > 默认值。

## 目标清单格式

`fetch_mda.py` 读取 `Stkcd`（代码）、`ShortName`（简称）、`Accper`（截止日）。
`Accper` 需为 `YYYY/12/31` 或 `YYYY-12-31`；其他季度/半年行会被跳过。
代码会 `zfill(6)`，因此 `1` 和 `000001` 都可识别。
