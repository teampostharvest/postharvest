#!/usr/bin/env python3
"""Regenerate the XLSX golden for golang/export_xlsx.go (_test.go).

Byte truth, not belief: the golden workbook is produced by the REAL Python
exporter (backend/exporters/xlsx_exporter.py, openpyxl) applied to the
post bytes that the HTTP goldens already prove byte-for-byte
(golang/httpapi/testdata/parse_response_*.json — real pipeline output).

The Go test decodes those SAME golden posts, runs WriteXLSX, and compares
the two workbooks' logical cell model (values, number formats, panes,
auto-filter, widths, hyperlinks).  File bytes can never match between
openpyxl and excelize (different zip/package metadata), so the honest proof
is cell-model equality — and the flat rows themselves are additionally
byte-proven by WriteCSV (same FLAT_COLUMNS).

Usage:
    ./.venv/bin/python golang/testdata/gen_xlsx_golden.py

Output:
    golang/testdata/facebook_posts_golden.xlsx

After changing xlsx_exporter.py or the httpapi goldens, DO NOT hand-edit
the golden: re-run this script, then make the Go exporter match.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # golang/testdata -> repo root

sys.path.insert(0, str(REPO))

from backend.exporters.xlsx_exporter import EXPORT_FILENAME, export_xlsx  # noqa: E402
from backend.exporters.safety import safe_filename  # noqa: E402

# Fixed export context — must match golang/export_xlsx_test.go constants.
JOB_ID = "job_7"
SOURCE = "postharvest"
EXPORTED_AT = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
SCHEMA_VERSION = "1.0"

OUT = HERE / "facebook_posts_golden.xlsx"


def main() -> None:
    safe_filename(OUT.name)
    # The posts are the exact bytes the HTTP goldens prove equal to the
    # Python pipeline — decode them the same way the Go test will.
    posts: list[dict] = []
    for name in (
        "parse_response_dom_sample.json",
        "parse_response_browser_snapshot_html.json",
        "parse_response_browser_snapshot_graphql.json",
    ):
        doc = json.loads((REPO / "golang" / "httpapi" / "testdata" / name).read_text())
        posts.extend(doc["posts"])
    export_xlsx(
        posts,
        OUT,
        job_id=JOB_ID,
        source=SOURCE,
        exported_at=EXPORTED_AT,
        schema_version=SCHEMA_VERSION,
    )
    print(f"xlsx golden regenerated: {OUT} ({len(posts)} posts)")


if __name__ == "__main__":
    main()