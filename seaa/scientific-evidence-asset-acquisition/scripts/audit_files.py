#!/usr/bin/env python3
"""Read-only fingerprints and limited structural CSV/TSV audits; no scientific inference."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sniff(path):
    with path.open("rb") as f:
        head = f.read(512)
    if head.startswith(b"%PDF-"): return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"): return "png"
    if head.startswith(b"\xff\xd8\xff"): return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")): return "gif"
    if head.startswith(b"PK\x03\x04"): return "zip_container"
    if b"<html" in head.lower() or b"<!doctype html" in head.lower(): return "html"
    try:
        head.decode("utf-8-sig")
        return "utf8_text"
    except UnicodeDecodeError:
        return "unknown_binary"


def csv_audit(path, max_rows):
    seen, header, missing, bad_rows = set(), [], [], []
    rows = duplicates = malformed = 0
    complete = True
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter="\t" if path.suffix.lower() == ".tsv" else ",", strict=True)
        header = next(reader, [])
        missing = [0] * len(header)
        for row in reader:
            if rows >= max_rows:
                complete = False
                break
            rows += 1
            if len(row) != len(header):
                malformed += 1
                if len(bad_rows) < 20: bad_rows.append(reader.line_num)
            for i in range(len(header)):
                if i >= len(row) or not row[i].strip(): missing[i] += 1
            key = tuple(row)
            if key in seen: duplicates += 1
            seen.add(key)
    return {"header":header, "duplicate_header_names":len(header)-len(set(header)),
            "rows_scanned":rows, "scan_complete":complete, "missing_by_column_index":missing,
            "ragged_rows":malformed, "first_ragged_line_numbers":bad_rows,
            "exact_duplicate_rows":duplicates,
            "interpretation":"Structural observations only; duplicates may be legitimate repeats."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--max-rows", type=int, default=100000)
    p.add_argument("files", nargs="+", type=Path)
    a = p.parse_args()
    if a.max_rows <= 0: p.error("--max-rows must be positive")
    records, failed = [], False
    if any(a.out.resolve() == f.resolve() for f in a.files):
        p.error("output must not overwrite an input")
    for path in a.files:
        row = {"path":str(path.resolve())}
        try:
            row.update(size_bytes=path.stat().st_size, sha256=sha256(path), observed_format=sniff(path))
            if path.suffix.lower() in (".csv", ".tsv"): row["table_audit"] = csv_audit(path, a.max_rows)
            row["status"] = "READ"
        except (OSError, UnicodeError, csv.Error) as exc:
            row.update(status="ERROR", error=str(exc)); failed = True
        records.append(row)
    try:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        with a.out.open("x", encoding="utf-8", newline="\n") as f:
            json.dump({"files":records}, f, ensure_ascii=False, indent=2); f.write("\n")
    except OSError as exc:
        print(f"FAIL {exc}", file=sys.stderr); return 1
    print(f"{'PARTIAL' if failed else 'PASS'} files={len(records)} output={a.out}")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())

