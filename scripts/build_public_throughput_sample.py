"""Build the bounded public airport-day sample from the verified 2018 archive."""

from __future__ import annotations

import argparse
import csv
import hashlib
import sqlite3
from pathlib import Path

SOURCE_SHA256 = "cc58a15d6538b0ec0bfecc8d854a5f58472d0ad48167113fbdca3c2fb71b8f23"
FIELDS = ("airport", "date", "reported_screened", "checkpoint_hour_rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    if hashlib.sha256(args.source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise SystemExit("Expected the hash-verified 2018 core SQLite partition.")
    with sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise SystemExit("SQLite integrity check failed.")
        if db.execute("SELECT COUNT(*) FROM duplicate_keys").fetchone() != (0,):
            raise SystemExit("Archive contained unexpected duplicate keys.")
        rows = db.execute(
            """
            SELECT airport_code, date, SUM(throughput), COUNT(*)
            FROM nonconflicting_observations
            GROUP BY airport_code, date
            ORDER BY airport_code, date
            """
        ).fetchall()
    if len(rows) != 829 or sum(row[3] for row in rows) != 15_948:
        raise SystemExit("Unexpected airport-day or checkpoint-hour count.")
    with args.output.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(FIELDS)
        writer.writerows(rows)


if __name__ == "__main__":
    main()
