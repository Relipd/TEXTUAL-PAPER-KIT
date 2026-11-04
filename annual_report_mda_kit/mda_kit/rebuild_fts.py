# -*- coding: utf-8 -*-
"""Rebuild FTS5 index and verify row counts."""
import sqlite3
import sys
import time

from .config import settings


def main() -> int:
    db = sys.argv[1] if len(sys.argv) > 1 else str(settings.db_path)
    con = sqlite3.connect(db, timeout=120)
    t = time.time()
    try:
        con.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
        con.commit()
    except sqlite3.OperationalError as exc:
        print(f"FTS rebuild failed (database has no sections_fts?): {exc}", file=sys.stderr)
        con.close()
        return 1
    print("FTS rebuilt in %.1fs" % (time.time() - t))
    print("docs:", con.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    print("sections:", con.execute("SELECT COUNT(*) FROM sections").fetchone()[0])
    print("fts rows:", con.execute("SELECT COUNT(*) FROM sections_fts").fetchone()[0])
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
