#!/usr/bin/env python3
"""Rebuild the committed SEAA fixtures under fixtures/seaa/ (pure Python, no third-party imports).

Usage (from the repo root):  python tools/build_seaa_fixtures.py

Outputs
  fixtures/seaa/synthetic-probe.pdf        4 pages: text+ruled table / vector only / raster only / vector only
  fixtures/seaa/plos-1003285.pdf           4-page text PDF with a ruled table and the DOI 10.1371/journal.pcbi.1003285.
                                           SYNTHETIC stand-in for an open-access paper: the text is invented.
  fixtures/seaa/core-literature-catalog.json  12 invented catalog records (one duplicate DOI) for offline discovery tests

Nothing here contacts the network and nothing is real literature.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "fixtures" / "seaa"


# ---------------------------------------------------------------- minimal PDF writer
def _pdf(pages: list[dict], info: dict | None = None) -> bytes:
    """pages: [{"stream": str, "image": bool}] -> PDF bytes. Fonts: F1 = Helvetica."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    catalog = add(b"")  # placeholder, filled below
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    image = None
    if any(p.get("image") for p in pages):
        raw = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0])  # 2x2 RGB
        image = add(
            b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB "
            b"/BitsPerComponent 8 /Length " + str(len(raw)).encode() + b" >>\nstream\n" + raw + b"\nendstream"
        )
    page_ids = []
    for p in pages:
        data = p["stream"].encode("latin-1")
        content = add(b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream")
        xobj = f" /XObject << /Im1 {image} 0 R >>" if p.get("image") else ""
        page_ids.append(add(
            f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >>{xobj} >> /Contents {content} 0 R >>".encode()
        ))
    objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objs[pages_obj - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    info_id = None
    if info:
        body = " ".join(f"/{k} ({v})" for k, v in info.items())
        info_id = add(f"<< {body} >>".encode("latin-1"))
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    trailer = f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R" + (f" /Info {info_id} 0 R" if info_id else "")
    out += (trailer + f" >>\nstartxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def text(x: int, y: int, size: int, s: str) -> str:
    s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"BT /F1 {size} Tf {x} {y} Td ({s}) Tj ET"


def table(x: int, y_top: int, col_w: int, row_h: int, rows: list[list[str]]) -> list[str]:
    """Ruled table: every cell edge is a stroked line so pdfplumber.find_tables() sees it."""
    n_rows, n_cols = len(rows), len(rows[0])
    right, bottom = x + col_w * n_cols, y_top - row_h * n_rows
    ops = []
    for r in range(n_rows + 1):
        y = y_top - r * row_h
        ops.append(f"{x} {y} m {right} {y} l S")
    for c in range(n_cols + 1):
        cx = x + c * col_w
        ops.append(f"{cx} {y_top} m {cx} {bottom} l S")
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            ops.append(text(x + c * col_w + 6, y_top - (r + 1) * row_h + 8, 10, cell))
    return ops


# ---------------------------------------------------------------- fixtures
def synthetic_probe() -> bytes:
    p1 = [text(50, 740, 12, "Synthetic probe page one: text and a ruled table.")]
    p1 += table(50, 700, 100, 24, [["Item", "Value"], ["Alpha", "7"]])
    p2 = ["100 100 200 100 re S", "100 100 m 300 200 l S"]  # vector only, no text
    p3 = ["q 200 0 0 200 100 400 cm /Im1 Do Q"]  # raster only, no text layer
    p4 = ["50 50 m 550 50 l S"]  # vector only
    return _pdf([{"stream": "\n".join(p1)}, {"stream": "\n".join(p2)},
                 {"stream": "\n".join(p3), "image": True}, {"stream": "\n".join(p4)}],
                info={"Title": "Synthetic probe fixture"})


def plos_like() -> bytes:
    banner = "SYNTHETIC TEST DOCUMENT - invented text standing in for an open-access PLOS Computational Biology article."
    p1 = [
        text(50, 750, 9, banner),
        text(50, 730, 14, "A fictional model of signal gain in a two-compartment system"),
        text(50, 712, 9, "doi:10.1371/journal.pcbi.1003285 (identifier kept only so DOI detection can be tested)"),
        text(50, 690, 10, "Abstract. We describe an invented model and report invented numbers for testing."),
    ]
    p1 += table(50, 650, 110, 24, [["Parameter", "Value", "Unit"], ["k_on", "0.8", "1/s"], ["k_off", "0.2", "1/s"]])
    p2 = [
        text(50, 750, 12, "Results"),
        text(50, 730, 10, "Figure 1 is described here in words only. The fictional gain rises linearly with input."),
        text(50, 714, 10, "Reference identifier: 10.1371/journal.pcbi.1003285"),
    ]
    p3 = [text(50, 750, 12, "Discussion"), text(50, 730, 10, "Nothing on this page is a real finding.")]
    p4 = [text(50, 750, 12, "References"), text(50, 730, 10, "[1] Placeholder reference for fixture purposes.")]
    return _pdf([{"stream": "\n".join(p)} for p in (p1, p2, p3, p4)],
                info={"Title": "Synthetic stand-in for an open-access article"})


def catalog() -> list[dict]:
    """12 invented records. Records 3 and 12 share a DOI (written differently) to exercise dedup."""
    topics = [
        ("Ratiometric FRET probe for mitochondrial H2O2 in living cells", "A ratiometric FRET probe built on a polymer micelle reports H2O2 in mitochondria. 聚合物胶束 载体 提高了线粒体靶向性。"),
        ("Polymeric micelle carriers for organelle-targeted fluorescent probes", "聚合物胶束 can carry hydrophobic dyes to the 线粒体 while keeping FRET efficiency."),
        ("Boronate-based H2O2 sensing: kinetics and selectivity", "Boronate oxidation gives a selective H2O2 response in PBS; FRET read-out discussed."),
        ("Mitochondria-targeted ratiometric imaging of reactive oxygen species", "线粒体 靶向 ratiometric imaging of ROS with H2O2 calibration curves."),
        ("Design rules for FRET pairs in nanoscale carriers", "FRET pair spacing inside micelles and its effect on ratio response."),
        ("Calibration of fluorescence ratio readouts", "Generic calibration practice for ratiometric fluorescence measurements."),
        ("Long-term stability of dye-loaded polymer nanoparticles", "聚合物胶束 and nanoparticle storage stability under light."),
        ("Cell uptake of block-copolymer micelles", "Endocytosis routes of 聚合物胶束 in several cell lines."),
        ("Hydrogen peroxide signalling overview", "Review of H2O2 as a second messenger; no imaging probe content."),
        ("Quantifying ratio imaging noise", "Noise sources in two-channel ratio imaging, FRET included."),
        ("Mitochondrial membrane potential indicators", "线粒体 membrane potential dyes compared; unrelated to H2O2."),
        ("Ratiometric FRET probe for mitochondrial H2O2 in living cells (preprint)", "Preprint version of the first record with the same DOI."),
    ]
    rows = []
    for i, (title, abstract) in enumerate(topics, start=1):
        doi = f"10.99999/fixture.{i:03d}"
        if i == 12:
            doi = "https://doi.org/10.99999/FIXTURE.001"  # same DOI as record 1, different spelling
        rows.append({
            "title": title,
            "doi": doi,
            "authors": [f"Fixture Author {i}A", f"Fixture Author {i}B"],
            "year": 2015 + (i % 9),
            "publication": "Journal of Invented Results",
            "abstract": abstract,
            "note": "Invented catalog record for offline tests; not real literature.",
        })
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "synthetic-probe.pdf").write_bytes(synthetic_probe())
    (OUT / "plos-1003285.pdf").write_bytes(plos_like())
    (OUT / "core-literature-catalog.json").write_text(
        json.dumps(catalog(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote SEAA fixtures under", OUT)


if __name__ == "__main__":
    main()
