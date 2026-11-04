# -*- coding: utf-8 -*-
"""Central configuration for the MD&A annual-report toolkit.

All paths are resolved relative to the repository root unless an absolute
path is supplied. A ``config.toml`` file at the repository root (or the
path in ``MDA_KIT_CONFIG``) overrides the defaults.

Example
-------
    cp config.example.toml config.toml
    # edit paths / network settings

The package intentionally does not ship any data. ``data/`` is git-ignored.
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("MDA_KIT_CONFIG", REPO_ROOT / "config.toml"))
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _load_toml() -> dict:
    if CONFIG_PATH.exists():
        raw = CONFIG_PATH.read_bytes().decode("utf-8-sig")
        return tomllib.loads(raw)
    return {}


_CFG = _load_toml()


def _get(section: str, key: str, default):
    return _CFG.get(section, {}).get(key, default)


def _as_path(value, default: Path) -> Path:
    if value is None:
        return default
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p)


DATA_DIR = _as_path(os.environ.get("MDA_DATA_DIR") or _get("paths", "data_dir", "data"),
                    REPO_ROOT / "data")
MDA_DIR = _as_path(_get("paths", "mda_dir", None), DATA_DIR / "MDA")
BIZ_DIR = _as_path(_get("paths", "biz_dir", None), DATA_DIR / "MDA_BIZ")
DB_PATH = _as_path(_get("paths", "db_path", None), DATA_DIR / "mda_sections.sqlite")
TARGETS_CSV = _as_path(_get("paths", "targets_csv", None), DATA_DIR / "targets.csv")
EXPORT_DIR = _as_path(_get("paths", "export_dir", None), DATA_DIR / "exports")
MANIFEST = MDA_DIR / "_manifest.csv"
PLAN = MDA_DIR / "_plan.csv"
FAILED = MDA_DIR / "_failed.csv"
FIX_CSV = MDA_DIR / "_fix_upstream.csv"
BACKUP_DIR = MDA_DIR / "_backup_fix"
NEEQ_CACHE = MDA_DIR / "_neeq_cache"

RPS = float(_get("network", "rps", 8.0))
DL_RPS = float(_get("network", "dl_rps", 2.0))
TRUST_ENV = bool(_get("network", "trust_env", False))
USER_AGENT = str(_get("network", "user_agent", DEFAULT_USER_AGENT))
STRICT_BOUNDARY = bool(_get("extract", "strict_boundary", False))


class _Settings:
    """Simple attribute container used by the pipeline modules."""

    data_dir = DATA_DIR
    mda_dir = MDA_DIR
    biz_dir = BIZ_DIR
    db_path = DB_PATH
    targets_csv = TARGETS_CSV
    export_dir = EXPORT_DIR
    manifest = MANIFEST
    plan = PLAN
    failed = FAILED
    fix_csv = FIX_CSV
    backup_dir = BACKUP_DIR
    neeq_cache = NEEQ_CACHE
    rps = RPS
    dl_rps = DL_RPS
    trust_env = TRUST_ENV
    user_agent = USER_AGENT
    strict_boundary = STRICT_BOUNDARY


settings = _Settings()


def ensure_dirs() -> None:
    """Create the writable directories used by the pipeline."""
    for d in (DATA_DIR, MDA_DIR, BIZ_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def as_dict() -> dict:
    return {
        "repo_root": str(REPO_ROOT),
        "data_dir": str(DATA_DIR),
        "mda_dir": str(MDA_DIR),
        "biz_dir": str(BIZ_DIR),
        "db_path": str(DB_PATH),
        "targets_csv": str(TARGETS_CSV),
        "export_dir": str(EXPORT_DIR),
        "rps": RPS,
        "dl_rps": DL_RPS,
        "trust_env": TRUST_ENV,
    }
