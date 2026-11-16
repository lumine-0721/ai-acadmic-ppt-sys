#!/usr/bin/env python3
"""Optional read-only PDF page-level text, table, and image provenance probe for SEAA.

Reuses pypdf==7.0.1 and pdfplumber==0.11.10 to record 1-based file page numbers,
source SHA-256, extracted character/word counts, detected table/image boxes, and
scan/raster-only boundaries. Never promotes metadata or abstract text to full-text
scientific verification, and never claims copyright/reuse clearance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import audit_files as audit

SUPPORTED_PYPDF = "7.0.1"
SUPPORTED_PDFPLUMBER = "0.11.10"
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")


def parse_pages_spec(spec, total_pages):
    if not spec:
        return list(range(1, total_pages + 1))
    selected = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a_str, b_str = part.split("-", 1)
            a, b = int(a_str), int(b_str)
            if a <= 0 or b <= 0 or a > b or b > total_pages:
                raise ValueError(f"page range {part!r} out of bounds (1..{total_pages})")
            selected.update(range(a, b + 1))
        else:
            p = int(part)
            if p <= 0 or p > total_pages:
                raise ValueError(f"page number {p} out of bounds (1..{total_pages})")
            selected.add(p)
    if not selected:
        raise ValueError("empty page selection")
    return sorted(selected)


def probe_pdf(source, pages_spec=None, include_text=False, extract_images_dir=None):
    from importlib.metadata import version
    import pdfplumber
    from pypdf import PdfReader

    ver_pypdf = version("pypdf")
    ver_plumber = version("pdfplumber")
    if ver_pypdf != SUPPORTED_PYPDF or ver_plumber != SUPPORTED_PDFPLUMBER:
        raise ValueError(
            f"expected pypdf=={SUPPORTED_PYPDF} and pdfplumber=={SUPPORTED_PDFPLUMBER}; "
            f"found pypdf=={ver_pypdf}, pdfplumber=={ver_plumber}"
        )

    if audit.sniff(source) != "pdf":
        raise ValueError(f"{source}: not a valid PDF file header")

    before_sha = audit.sha256(source)
    reader = PdfReader(str(source))
    total_pages = len(reader.pages)
    target_pages = parse_pages_spec(pages_spec, total_pages)

    raw_meta = reader.metadata or {}
    meta_dict = {str(k).lstrip("/"): str(v) for k, v in raw_meta.items() if v is not None}

    candidate_dois = set()
    for val in meta_dict.values():
        for m in DOI_RE.findall(val):
            candidate_dois.add(m.rstrip(".,;)"))

    page_records = []
    text_pages_count = 0
    raster_or_empty_pages = []

    if extract_images_dir is not None:
        if extract_images_dir.exists():
            raise FileExistsError("extract-images directory exists; choose a new path")
        extract_images_dir.mkdir(parents=True, exist_ok=False)

    with pdfplumber.open(str(source)) as doc:
        for pno in target_pages:
            pypdf_page = reader.pages[pno - 1]
            plumber_page = doc.pages[pno - 1]

            pypdf_text = pypdf_page.extract_text() or ""
            plumber_text = plumber_page.extract_text() or ""
            words = plumber_page.extract_words() or []
            tables = plumber_page.find_tables() or []
            images = plumber_page.images or []

            for m in DOI_RE.findall(pypdf_text):
                candidate_dois.add(m.rstrip(".,;)"))

            char_count = max(len(pypdf_text.strip()), len(plumber_text.strip()))
            has_extractable_text = char_count > 0
            if has_extractable_text:
                text_pages_count += 1
                page_status = "extractable_text"
            elif len(images) > 0:
                page_status = "raster_only_no_text_layer"
                raster_or_empty_pages.append(pno)
            else:
                page_status = "empty_or_vector_only_no_text"
                raster_or_empty_pages.append(pno)

            saved_images = []
            if extract_images_dir is not None:
                for idx, img_file in enumerate(pypdf_page.images, 1):
                    safe_ext = Path(img_file.name).suffix.lower() or ".bin"
                    out_file = extract_images_dir / f"page-{pno:03d}-img-{idx:02d}{safe_ext}"
                    out_file.write_bytes(img_file.data)
                    saved_images.append({
                        "index_on_page": idx,
                        "name": img_file.name,
                        "path": str(out_file.resolve()),
                        "sha256": audit.sha256(out_file),
                        "bytes": len(img_file.data),
                    })

            row = {
                "file_page": pno,
                "page_box_pt": [float(plumber_page.width), float(plumber_page.height)],
                "page_read_status": page_status,
                "pypdf_char_count": len(pypdf_text.strip()),
                "pdfplumber_char_count": len(plumber_text.strip()),
                "word_count": len(words),
                "image_count": len(images),
                "image_boxes": [
                    {
                        "x0": round(float(im.get("x0", 0.0)), 2),
                        "top": round(float(im.get("top", 0.0)), 2),
                        "x1": round(float(im.get("x1", 0.0)), 2),
                        "bottom": round(float(im.get("bottom", 0.0)), 2),
                    }
                    for im in images
                ],
                "table_count": len(tables),
                "tables": [
                    {
                        "bbox": [round(float(x), 2) for x in tbl.bbox],
                        "rows": tbl.extract(),
                    }
                    for tbl in tables
                ],
            }
            if saved_images:
                row["extracted_image_files"] = saved_images
            if include_text:
                row["text_excerpt"] = plumber_text if plumber_text.strip() else pypdf_text
            page_records.append(row)

    after_sha = audit.sha256(source)
    if before_sha != after_sha:
        raise ValueError("source file hash changed during read-only PDF probe")

    if text_pages_count == 0:
        observed_read_scope = "no_extractable_text_layer"
    elif len(target_pages) < total_pages:
        observed_read_scope = "partial_pages_text_extracted"
    else:
        observed_read_scope = "all_inspected_pages_checked"

    return {
        "format": "seaa-pdf-probe/1",
        "source": str(source.resolve()),
        "sha256": before_sha,
        "libraries": {
            "pypdf": ver_pypdf,
            "pdfplumber": ver_plumber,
        },
        "total_file_pages": total_pages,
        "inspected_file_pages": target_pages,
        "text_extractable_pages_count": text_pages_count,
        "non_text_file_pages": raster_or_empty_pages,
        "observed_read_scope": observed_read_scope,
        "metadata_declarations": meta_dict,
        "candidate_dois_unverified": sorted(candidate_dois),
        "governance": {
            "scientific_claim_verified": False,
            "metadata_or_abstract_is_not_full_text": True,
            "rights_status": "UNKNOWN_REQUIRES_SEPARATE_CLEARANCE",
            "note": "Mechanical page/text/table/image extraction only; does not verify scientific claims or license permissions.",
        },
        "pages": page_records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Input PDF path")
    parser.add_argument("--out", type=Path, required=True, help="New JSON output path")
    parser.add_argument("--pages", type=str, default=None, help="Optional 1-based page list/range, e.g. 1,3-4")
    parser.add_argument("--include-text", action="store_true", help="Include extracted page text in output JSON")
    parser.add_argument("--extract-images-dir", type=Path, default=None, help="Optional new directory to dump page images")
    args = parser.parse_args()

    try:
        if args.out.exists():
            raise FileExistsError("output exists; choose a new path")
        report = probe_pdf(
            args.source,
            pages_spec=args.pages,
            include_text=args.include_text,
            extract_images_dir=args.extract_images_dir,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(
            f"PASS pdf_pages={report['total_file_pages']} "
            f"inspected={len(report['inspected_file_pages'])} "
            f"text_pages={report['text_extractable_pages_count']} "
            f"scope={report['observed_read_scope']}"
        )
        return 0
    except ImportError:
        print(
            "FAIL optional dependency missing; install requirements-pdf-probe.txt in an isolated venv",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, KeyError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
