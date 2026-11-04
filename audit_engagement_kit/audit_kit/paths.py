# -*- coding: utf-8 -*-
"""统一路径解析。

所有模块只从这里取路径，禁止在业务代码里硬编码绝对路径或自行拼仓库根目录。

默认目录布局::

    <repo>/
    ├─ data/                 采集与抽取产物（不入库，见 .gitignore）
    │  ├─ raw/               下载的原始 PDF（可按 announcementId 重建）
    │  ├─ pdf/               关键页切片
    │  ├─ text/              切片文本
    │  ├─ ocr/               扫描件 OCR 结果
    │  ├─ pipeline.db        采集任务表
    │  ├─ audit_contracts.db 主库
    │  └─ v2_results.jsonl   抽取结果
    ├─ output/               导出的 Excel（不入库）
    ├─ samples/              随仓库发布的样例公告与黄金结果
    └─ FIN_Audit.csv         审计意见名单（第三方数据，需用户自备，不入库）

可用环境变量覆盖：

============== ==========================================
AUDIT_KIT_DATA_DIR   数据目录（默认 ``<repo>/data``）
AUDIT_KIT_DB         主库路径（默认 ``<data>/audit_contracts.db``）
AUDIT_KIT_FIN_AUDIT_CSV  审计意见名单 CSV 路径（默认 ``<repo>/FIN_Audit.csv``）
AUDIT_KIT_OUTPUT_DIR 导出目录（默认 ``<repo>/output``）
============== ==========================================
"""
from __future__ import annotations

import os
from pathlib import Path

#: 仓库根目录（本文件位于 <repo>/audit_kit/paths.py）
ROOT: Path = Path(__file__).resolve().parent.parent


def _env_path(name: str, default: Path):
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


#: 数据根目录
DATA: Path = _env_path("AUDIT_KIT_DATA_DIR", ROOT / "data")
#: 主库（documents / persons / documents_fts）
DB: Path = _env_path("AUDIT_KIT_DB", DATA / "audit_contracts.db")
#: 采集任务表
PIPELINE_DB: Path = DATA / "pipeline.db"
#: 下载的原始 PDF
RAW_DIR: Path = DATA / "raw"
#: 关键页切片
PDF_DIR: Path = DATA / "pdf"
#: 切片文本
TXT_DIR: Path = DATA / "text"
#: 扫描件 OCR 结果
OCR_DIR: Path = DATA / "ocr"
#: Tesseract 语言包（项目内自带，OCR 用）
TESSDATA_DIR: Path = ROOT / "ocr" / "tessdata"
#: 全量抽取结果
V2_RESULTS: Path = DATA / "v2_results.jsonl"
#: 审计意见名单（CSMAR 等第三方数据，不随仓库分发）
FIN_AUDIT_CSV: Path = _env_path("AUDIT_KIT_FIN_AUDIT_CSV", ROOT / "FIN_Audit.csv")
#: 导出目录
OUTPUT_DIR: Path = _env_path("AUDIT_KIT_OUTPUT_DIR", ROOT / "output")

#: Excel 交付物默认文件名
EXPORT_XLSX: Path = OUTPUT_DIR / "审计机构聘任公告_项目成员信息.xlsx"
#: 人工核对样本默认文件名
SAMPLE_XLSX: Path = OUTPUT_DIR / "审计机构聘任公告_核对样本.xlsx"


def ensure(*dirs: Path) -> None:
    """创建目录（幂等）。"""
    for d in dirs or ():
        Path(d).mkdir(parents=True, exist_ok=True)


def require_db(db=None) -> Path:
    """返回主库路径；不存在时给出可操作的提示并退出（避免裸 sqlite 报错）。"""
    p = Path(db) if db is not None else DB
    if not p.exists():
        raise SystemExit(
            f"错误：找不到主库 {p}\n"
            "请先按 docs/reproduce.md 跑完 build → extract → apply，"
            "或用环境变量 AUDIT_KIT_DB 指向已有主库。")
    return p


def rel(path) -> str:
    """把绝对路径转成相对仓库根目录的展示路径（日志用）。"""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except (ValueError, OSError):
        return str(path)
