"""Full-text PDF structural parsing and scientific figure/table/asset extraction.

Combines:
- pypdf (BSD-3-Clause) for page count, document metadata, and raw embedded bitmap streams;
- pdfplumber (MIT) for word coordinates, section heading detection, caption parsing, and table extraction;
- PDFFigures 2.0 / Paper2Assets / PaperForge spatial heuristics for Figure-Caption-Mention linking
  and multi-panel (A/B/C/D) compound figure bounding box preservation;
- pypdfium2 (Apache-2.0 / BSD-3-Clause) + Pillow (MIT-CMU) for high-DPI compound figure region rendering
  so vector spectra, reaction schemes, and multi-panel labels are never lost.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    AccessStatus,
    AssetKind,
    AssetRecord,
    AssetStatus,
    ExactLocator,
    PassageRecord,
    RightsMetadata,
    RightsStatus,
    SourceOrigin,
    SourceRecord,
    TransformationMetadata,
    VerificationLevel,
    compute_sha256,
)

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
SECTION_HEADER_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*)|[I|V|X]+)?\.?\s*"
    r"(abstract|introduction|background|experimental(?:\s+section)?|materials\s+and\s+methods|"
    r"methods|results(?:\s+and\s+discussion)?|discussion|conclusion(?:s)?|references|"
    r"acknowledg(?:e)?ments|supporting\s+information|摘要|引言|实验部分|材料与方法|结果与讨论|结论|参考文献)\b",
    re.IGNORECASE,
)
CAPTION_START_RE = re.compile(
    r"^(?:(Figure|Fig\.|Scheme|Table|Chart|图|表)\s*([S]?\d+[A-Za-z]?))\s*[:.\-–—]?\s*(.*)$",
    re.IGNORECASE,
)
FIGURE_MENTION_RE = re.compile(
    r"\b(Fig(?:ure)?\.?\s*[S]?\d+[A-Za-z]?(?:\s*[a-fA-F]|\([a-fA-F]\))?|"
    r"Scheme\s*[S]?\d+[A-Za-z]?|Table\s*[S]?\d+[A-Za-z]?|图\s*\d+|表\s*\d+)\b",
    re.IGNORECASE,
)
PANEL_TOKEN_RE = re.compile(r"^(?:\([a-hA-H]\)|[a-hA-H]\)|[A-H])$")
PANEL_IN_CAPTION_RE = re.compile(r"(?:\(([a-hA-H])\)|\b([a-hA-H])\)\s)")


def normalize_figure_id(prefix: str, num: str) -> str:
    p = prefix.lower().rstrip(".")
    if p in ("fig", "figure", "图"):
        return f"Figure {num}"
    if p in ("table", "表"):
        return f"Table {num}"
    if p == "scheme":
        return f"Scheme {num}"
    return f"{prefix.capitalize()} {num}"


def infer_asset_kind_from_caption(fig_id: str, caption: str) -> str:
    lower = f"{fig_id} {caption}".lower()
    if fig_id.lower().startswith("table"):
        return AssetKind.DATA_TABLE.value
    if fig_id.lower().startswith("scheme") or any(k in lower for k in ("scheme", "synthesis", "reaction", "mechanism", "route", "机理", "反应")):
        return AssetKind.REACTION_SCHEME.value
    if any(k in lower for k in ("spectra", "spectrum", "ftir", "nmr", "uv-vis", "fluorescence", "raman", "xps", "ms", "光谱", "谱图", "波数")):
        return AssetKind.SPECTRUM.value
    if any(k in lower for k in ("tem", "sem", "afm", "confocal", "microscopy", "image", "imaging", "显微", "成像")):
        return AssetKind.MICROGRAPH.value
    if any(k in lower for k in ("setup", "apparatus", "reactor", "device", "instrument", "装置")):
        return AssetKind.EXPERIMENTAL_SETUP.value
    return AssetKind.FIGURE.value


def group_words_into_lines(words: List[Dict[str, Any]], y_tolerance: float = 3.5) -> List[Dict[str, Any]]:
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (round(float(w.get("top", 0.0)) / y_tolerance), float(w.get("x0", 0.0))))
    lines: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    current_top: Optional[float] = None

    for w in sorted_words:
        w_top = float(w.get("top", 0.0))
        if current_top is None or abs(w_top - current_top) <= y_tolerance:
            current.append(w)
            current_top = w_top if current_top is None else (current_top * (len(current) - 1) + w_top) / len(current)
        else:
            current.sort(key=lambda item: float(item.get("x0", 0.0)))
            lines.append(_build_line_dict(current))
            current = [w]
            current_top = w_top
    if current:
        current.sort(key=lambda item: float(item.get("x0", 0.0)))
        lines.append(_build_line_dict(current))
    return lines


def _build_line_dict(line_words: List[Dict[str, Any]]) -> Dict[str, Any]:
    text = " ".join(str(w.get("text", "")).strip() for w in line_words if str(w.get("text", "")).strip())
    x0 = min(float(w.get("x0", 0.0)) for w in line_words)
    top = min(float(w.get("top", 0.0)) for w in line_words)
    x1 = max(float(w.get("x1", 0.0)) for w in line_words)
    bottom = max(float(w.get("bottom", 0.0)) for w in line_words)
    return {
        "text": text,
        "x0": round(x0, 2),
        "top": round(top, 2),
        "x1": round(x1, 2),
        "bottom": round(bottom, 2),
        "word_count": len(line_words),
    }


def extract_captions_from_lines(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    captions: List[Dict[str, Any]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = CAPTION_START_RE.match(line["text"])
        if m:
            prefix, num, rest = m.group(1), m.group(2), m.group(3)
            fig_id = normalize_figure_id(prefix, num)
            cap_lines = [line]
            j = i + 1
            while j < n:
                nxt = lines[j]
                vertical_gap = nxt["top"] - cap_lines[-1]["bottom"]
                if vertical_gap > 14.0 or CAPTION_START_RE.match(nxt["text"]) or SECTION_HEADER_RE.match(nxt["text"]):
                    break
                cap_lines.append(nxt)
                if nxt["text"].endswith(".") and len(" ".join(x["text"] for x in cap_lines)) > 120:
                    j += 1
                    break
                j += 1
            full_caption = " ".join(x["text"] for x in cap_lines).strip()
            x0 = min(x["x0"] for x in cap_lines)
            top = min(x["top"] for x in cap_lines)
            x1 = max(x["x1"] for x in cap_lines)
            bottom = max(x["bottom"] for x in cap_lines)
            panel_matches = [ (pm[0] or pm[1]).lower() for pm in PANEL_IN_CAPTION_RE.findall(full_caption) if (pm[0] or pm[1]) ]
            captions.append({
                "figure_id": fig_id,
                "caption": full_caption,
                "bbox_pt": [round(x0, 2), round(top, 2), round(x1, 2), round(bottom, 2)],
                "caption_panels": sorted(set(panel_matches)),
            })
            i = j
        else:
            i += 1
    return captions


def compute_compound_figure_bbox(
    page_width: float,
    page_height: float,
    caption_info: Dict[str, Any],
    images: List[Dict[str, Any]],
    rects: List[Dict[str, Any]],
    curves: List[Dict[str, Any]],
    words: List[Dict[str, Any]],
) -> Tuple[List[float], List[str]]:
    """Compute a unified bounding box above (or below) the figure caption that preserves all panels A/B/C and axes."""
    cap_x0, cap_top, cap_x1, cap_bottom = caption_info["bbox_pt"]
    search_top = max(24.0, cap_top - page_height * 0.62)
    search_bottom = max(search_top + 20.0, cap_top - 2.0)

    visual_boxes: List[Tuple[float, float, float, float]] = []
    for im in images:
        ix0, itop, ix1, ibot = (
            float(im.get("x0", 0.0)),
            float(im.get("top", 0.0)),
            float(im.get("x1", 0.0)),
            float(im.get("bottom", 0.0)),
        )
        if ibot <= cap_bottom + 8.0 and itop >= search_top - 20.0:
            visual_boxes.append((ix0, itop, ix1, ibot))

    for obj in list(rects) + list(curves):
        ox0, otop, ox1, obot = (
            float(obj.get("x0", 0.0)),
            float(obj.get("top", 0.0)),
            float(obj.get("x1", 0.0)),
            float(obj.get("bottom", 0.0)),
        )
        w = ox1 - ox0
        h = obot - otop
        # Exclude full-page border frames or tiny dots
        if w > page_width * 0.94 and h > page_height * 0.90:
            continue
        if (w >= 8.0 or h >= 8.0) and obot <= cap_top + 4.0 and otop >= search_top - 10.0:
            visual_boxes.append((ox0, otop, ox1, obot))

    detected_panels = set(caption_info.get("caption_panels", []))
    for w_obj in words:
        txt = str(w_obj.get("text", "")).strip()
        wtop = float(w_obj.get("top", 0.0))
        wbot = float(w_obj.get("bottom", 0.0))
        if search_top - 10.0 <= wtop and wbot <= cap_top + 2.0:
            if PANEL_TOKEN_RE.match(txt):
                letter = re.sub(r"[^a-hA-H]", "", txt).lower()
                if letter:
                    detected_panels.add(letter)
                    visual_boxes.append((
                        float(w_obj.get("x0", 0.0)),
                        wtop,
                        float(w_obj.get("x1", 0.0)),
                        wbot,
                    ))

    if visual_boxes:
        vx0 = max(12.0, min(b[0] for b in visual_boxes) - 10.0)
        vtop = max(12.0, min(b[1] for b in visual_boxes) - 10.0)
        vx1 = min(page_width - 12.0, max(max(b[2] for b in visual_boxes), cap_x1) + 10.0)
        vbot = min(page_height - 12.0, cap_bottom + 4.0)
    else:
        # Fallback region above caption
        vx0 = max(24.0, min(cap_x0, page_width * 0.08))
        vtop = max(36.0, cap_top - page_height * 0.35)
        vx1 = min(page_width - 24.0, max(cap_x1, page_width * 0.92))
        vbot = min(page_height - 12.0, cap_bottom + 4.0)

    return [round(vx0, 2), round(vtop, 2), round(vx1, 2), round(vbot, 2)], sorted(detected_panels)


def render_pdf_region_to_png(
    pdf_path: Path,
    page_1based: int,
    bbox_pt: List[float],
    out_png: Path,
    dpi: int = 216,
) -> Optional[Tuple[int, int]]:
    """Render a specific PDF page bounding box [x0, top, x1, bottom] (in PDF points) at high DPI using pypdfium2."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None

    scale = dpi / 72.0
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_1based - 1]
        bitmap = page.render(scale=scale)
        pil_img = bitmap.to_pil()
        img_w, img_h = pil_img.size
        x0, top, x1, bottom = bbox_pt
        px0 = max(0, min(img_w - 1, int(round(x0 * scale))))
        ptop = max(0, min(img_h - 1, int(round(top * scale))))
        px1 = max(px0 + 1, min(img_w, int(round(x1 * scale))))
        pbot = max(ptop + 1, min(img_h, int(round(bottom * scale))))
        cropped = pil_img.crop((px0, ptop, px1, pbot))
        out_png.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(str(out_png), format="PNG")
        return cropped.size
    finally:
        doc.close()


def parse_scientific_pdf(
    pdf_path: Path,
    source_id: str = "SRC001",
    pages_spec: Optional[List[int]] = None,
    output_assets_dir: Optional[Path] = None,
    asset_id_start: int = 1,
    license_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse a scientific PDF into a SourceRecord, section-aware PassageRecords, and Figure/Table AssetRecords."""
    import pdfplumber
    from pypdf import PdfReader

    pdf_path = pdf_path.resolve()
    file_sha = compute_sha256(pdf_path)
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    target_pages = pages_spec if pages_spec else list(range(1, total_pages + 1))

    raw_meta = reader.metadata or {}
    meta_dict = {str(k).lstrip("/"): str(v) for k, v in raw_meta.items() if v is not None}

    candidate_dois = set()
    for val in meta_dict.values():
        for m in DOI_RE.findall(val):
            candidate_dois.add(m.rstrip(".,;)").lower())

    passages: List[PassageRecord] = []
    assets: List[AssetRecord] = []
    page_summaries: List[Dict[str, Any]] = []
    raw_caption_items: List[Dict[str, Any]] = []

    current_section = "Front_Matter"
    passage_counter = 1
    asset_counter = asset_id_start
    text_extractable_pages = 0

    with pdfplumber.open(str(pdf_path)) as doc:
        for pno in target_pages:
            pypdf_page = reader.pages[pno - 1]
            plumber_page = doc.pages[pno - 1]
            pw, ph = float(plumber_page.width), float(plumber_page.height)

            words = plumber_page.extract_words(extra_attrs=["fontname", "size"]) or []
            lines = group_words_into_lines(words)
            tables = plumber_page.find_tables() or []
            images = plumber_page.images or []
            rects = getattr(plumber_page, "rects", []) or []
            curves = getattr(plumber_page, "curves", []) or []

            page_text = (plumber_page.extract_text() or pypdf_page.extract_text() or "").strip()
            for m in DOI_RE.findall(page_text):
                candidate_dois.add(m.rstrip(".,;)").lower())

            if page_text:
                text_extractable_pages += 1
                page_status = "extractable_text"
            elif images:
                page_status = "raster_only_no_text_layer"
            else:
                page_status = "empty_or_vector_only_no_text"

            # Detect captions on this page
            page_captions = extract_captions_from_lines(lines)
            for cap in page_captions:
                cap["page"] = pno
                cap["page_width"] = pw
                cap["page_height"] = ph
                cap["images"] = images
                cap["rects"] = rects
                cap["curves"] = curves
                cap["words"] = words
                raw_caption_items.append(cap)

            # Segment lines into section-aware passages
            buffer_lines: List[str] = []
            char_cursor = 0
            for ln in lines:
                txt = ln["text"].strip()
                if not txt:
                    continue
                sec_match = SECTION_HEADER_RE.match(txt)
                if sec_match and len(txt) <= 75:
                    if buffer_lines:
                        para_text = " ".join(buffer_lines).strip()
                        mentions = [ normalize_figure_id(*m.split(None, 1)) if len(m.split(None, 1)) == 2 else m for m in FIGURE_MENTION_RE.findall(para_text) ]
                        passages.append(
                            PassageRecord(
                                passage_id=f"PSG_{source_id}_{passage_counter:03d}",
                                source_id=source_id,
                                page=pno,
                                section=current_section,
                                text=para_text,
                                char_span=(char_cursor, char_cursor + len(para_text)),
                                figure_mentions=sorted(set(mentions)),
                                candidate_dois=sorted(set(m.rstrip(".,;)").lower() for m in DOI_RE.findall(para_text))),
                            )
                        )
                        char_cursor += len(para_text) + 1
                        passage_counter += 1
                        buffer_lines = []
                    current_section = sec_match.group(1).strip().title()
                    continue

                buffer_lines.append(txt)
                if len(" ".join(buffer_lines)) >= 550 and txt.endswith("."):
                    para_text = " ".join(buffer_lines).strip()
                    mentions = [ normalize_figure_id(*m.split(None, 1)) if len(m.split(None, 1)) == 2 else m for m in FIGURE_MENTION_RE.findall(para_text) ]
                    passages.append(
                        PassageRecord(
                            passage_id=f"PSG_{source_id}_{passage_counter:03d}",
                            source_id=source_id,
                            page=pno,
                            section=current_section,
                            text=para_text,
                            char_span=(char_cursor, char_cursor + len(para_text)),
                            figure_mentions=sorted(set(mentions)),
                            candidate_dois=sorted(set(m.rstrip(".,;)").lower() for m in DOI_RE.findall(para_text))),
                        )
                    )
                    char_cursor += len(para_text) + 1
                    passage_counter += 1
                    buffer_lines = []

            if buffer_lines:
                para_text = " ".join(buffer_lines).strip()
                mentions = [ normalize_figure_id(*m.split(None, 1)) if len(m.split(None, 1)) == 2 else m for m in FIGURE_MENTION_RE.findall(para_text) ]
                passages.append(
                    PassageRecord(
                        passage_id=f"PSG_{source_id}_{passage_counter:03d}",
                        source_id=source_id,
                        page=pno,
                        section=current_section,
                        text=para_text,
                        char_span=(char_cursor, char_cursor + len(para_text)),
                        figure_mentions=sorted(set(mentions)),
                        candidate_dois=sorted(set(m.rstrip(".,;)").lower() for m in DOI_RE.findall(para_text))),
                    )
                )
                passage_counter += 1

            # Extract structured tables on this page
            for t_idx, tbl in enumerate(tables, start=1):
                rows = tbl.extract()
                bbox = [round(float(x), 2) for x in tbl.bbox]
                matching_cap = next(
                    (c for c in page_captions if c["figure_id"].lower().startswith("table")),
                    None,
                )
                table_id = matching_cap["figure_id"] if matching_cap else f"Table p{pno}-{t_idx}"
                caption_str = matching_cap["caption"] if matching_cap else f"Extracted structured table on page {pno} ({len(rows)} rows)."
                aid = f"A{asset_counter:03d}"
                asset_counter += 1

                table_path: Optional[str] = None
                table_sha: Optional[str] = None
                table_status = AssetStatus.LOCATED.value
                if output_assets_dir is not None:
                    output_assets_dir.mkdir(parents=True, exist_ok=True)
                    t_file = output_assets_dir / f"{source_id}_p{pno:03d}_table_{t_idx:02d}.json"
                    t_file.write_text(
                        json.dumps({"table_id": table_id, "page": pno, "bbox_pt": bbox, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                    table_path = str(t_file.resolve())
                    table_sha = compute_sha256(t_file)
                    table_status = AssetStatus.ACQUIRED.value

                assets.append(
                    AssetRecord(
                        asset_id=aid,
                        source_id=source_id,
                        evidence_ids=[],
                        asset_kind=AssetKind.DATA_TABLE.value,
                        status=table_status,
                        figure_or_table_id=table_id,
                        source_locator=f"page {pno}, {table_id}, bbox={bbox}",
                        page=pno,
                        bbox_pt=bbox,
                        panel_labels=[],
                        is_multi_panel=False,
                        caption=caption_str,
                        mention_contexts=[],
                        conditions=["Preserve original column headers, units, and row qualifiers"],
                        scientific_immutable=["Cell numerical values", "Column units", "Sample identifiers"],
                        path=table_path,
                        sha256=table_sha,
                        format="json" if table_path else None,
                        table_rows=rows,
                        rights=_build_rights_metadata(license_hint, page_text),
                        transformation=TransformationMetadata(
                            kind="original",
                            basis="Structured table cell extraction via pdfplumber.find_tables().",
                        ),
                    )
                )

            # Extract embedded raw bitmap streams on pages without explicit figure captions (e.g. scan/embedded plots)
            if not [c for c in page_captions if not c["figure_id"].lower().startswith("table")] and len(pypdf_page.images) > 0:
                for img_idx, img_obj in enumerate(pypdf_page.images, start=1):
                    aid = f"A{asset_counter:03d}"
                    asset_counter += 1
                    img_bbox = (
                        [
                            round(float(images[img_idx - 1].get("x0", 0.0)), 2),
                            round(float(images[img_idx - 1].get("top", 0.0)), 2),
                            round(float(images[img_idx - 1].get("x1", pw)), 2),
                            round(float(images[img_idx - 1].get("bottom", ph)), 2),
                        ]
                        if img_idx - 1 < len(images)
                        else [0.0, 0.0, round(pw, 2), round(ph, 2)]
                    )
                    img_path: Optional[str] = None
                    img_sha: Optional[str] = None
                    img_fmt: Optional[str] = None
                    img_status = AssetStatus.LOCATED.value
                    if output_assets_dir is not None:
                        output_assets_dir.mkdir(parents=True, exist_ok=True)
                        ext = Path(img_obj.name).suffix.lower() or ".png"
                        out_f = output_assets_dir / f"{source_id}_p{pno:03d}_embedded_{img_idx:02d}{ext}"
                        out_f.write_bytes(img_obj.data)
                        img_path = str(out_f.resolve())
                        img_sha = compute_sha256(out_f)
                        img_fmt = ext.lstrip(".").replace("jpg", "jpeg")
                        img_status = AssetStatus.ACQUIRED.value

                    assets.append(
                        AssetRecord(
                            asset_id=aid,
                            source_id=source_id,
                            evidence_ids=[],
                            asset_kind=AssetKind.FIGURE.value,
                            status=img_status,
                            figure_or_table_id=f"Embedded-Image p{pno}-{img_idx}",
                            source_locator=f"page {pno}, embedded image #{img_idx}, bbox={img_bbox}",
                            page=pno,
                            bbox_pt=img_bbox,
                            panel_labels=[],
                            is_multi_panel=False,
                            caption=f"Unlabeled embedded bitmap #{img_idx} on page {pno} ({page_status}).",
                            mention_contexts=[],
                            conditions=["Requires caption/context verification before scientific use"],
                            scientific_immutable=["Original bitmap pixel values"],
                            path=img_path,
                            sha256=img_sha,
                            format=img_fmt,
                            rights=_build_rights_metadata(license_hint, page_text),
                            transformation=TransformationMetadata(
                                kind="original",
                                basis="Direct embedded bitmap stream extraction via pypdf.",
                            ),
                        )
                    )

            page_summaries.append({
                "file_page": pno,
                "page_box_pt": [round(pw, 2), round(ph, 2)],
                "page_read_status": page_status,
                "char_count": len(page_text),
                "word_count": len(words),
                "table_count": len(tables),
                "image_count": len(images),
                "caption_count": len(page_captions),
            })

    # Process captioned Figures / Schemes with Mention Context linking & Compound Region Rendering
    for cap in raw_caption_items:
        fig_id: str = cap["figure_id"]
        if fig_id.lower().startswith("table"):
            continue
        pno = cap["page"]
        bbox_pt, panels = compute_compound_figure_bbox(
            cap["page_width"],
            cap["page_height"],
            cap,
            cap["images"],
            cap["rects"],
            cap["curves"],
            cap["words"],
        )
        is_multi = len(panels) >= 2
        kind = (
            AssetKind.COMPOUND_FIGURE.value
            if is_multi
            else infer_asset_kind_from_caption(fig_id, cap["caption"])
        )

        # Link mention contexts across all passages
        mentions_text: List[str] = []
        fig_num_token = fig_id.split()[-1].lower()
        for psg in passages:
            lower_psg = psg.text.lower()
            if (
                fig_id.lower() in lower_psg
                or f"fig. {fig_num_token}" in lower_psg
                or f"figure {fig_num_token}" in lower_psg
                or f"scheme {fig_num_token}" in lower_psg
            ):
                mentions_text.append(f"[p.{psg.page} {psg.section}] {psg.text[:280]}")

        aid = f"A{asset_counter:03d}"
        asset_counter += 1
        fig_path: Optional[str] = None
        fig_sha: Optional[str] = None
        fig_fmt: Optional[str] = None
        px_size: Optional[Tuple[int, int]] = None
        fig_status = AssetStatus.LOCATED.value

        if output_assets_dir is not None:
            safe_fig = re.sub(r"[^A-Za-z0-9_-]+", "_", fig_id)
            out_png = output_assets_dir / f"{source_id}_p{pno:03d}_{safe_fig}.png"
            px_size = render_pdf_region_to_png(pdf_path, pno, bbox_pt, out_png, dpi=216)
            if out_png.is_file():
                fig_path = str(out_png.resolve())
                fig_sha = compute_sha256(out_png)
                fig_fmt = "png"
                fig_status = AssetStatus.ACQUIRED.value

        immutable_items = ["Axis labels and scales", "Legend and sample identifiers", "Caption experimental conditions"]
        if is_multi:
            immutable_items.append(f"Multi-panel labels ({', '.join(panels)}) and inter-panel comparison context must not be silently cropped out")

        assets.append(
            AssetRecord(
                asset_id=aid,
                source_id=source_id,
                evidence_ids=[],
                asset_kind=kind,
                status=fig_status,
                figure_or_table_id=fig_id,
                source_locator=f"page {pno}, {fig_id}, bbox={bbox_pt}" + (f", panels={panels}" if panels else ""),
                page=pno,
                bbox_pt=bbox_pt,
                panel_labels=panels,
                is_multi_panel=is_multi,
                caption=cap["caption"],
                mention_contexts=mentions_text[:5],
                conditions=[f"Extracted from {fig_id} on page {pno}; preserve caption context and conditions"],
                scientific_immutable=immutable_items,
                path=fig_path,
                sha256=fig_sha,
                format=fig_fmt,
                pixel_size=px_size,
                rights=_build_rights_metadata(license_hint, cap["caption"]),
                transformation=TransformationMetadata(
                    kind="original",
                    basis="High-DPI full figure region crop preserving multi-panel labels and axes via pypdfium2 + pdfplumber.",
                ),
            )
        )

    after_sha = compute_sha256(pdf_path)
    if file_sha != after_sha:
        raise ValueError(f"Read-only violation: SHA-256 changed on {pdf_path}")

    # Determine title and DOI from metadata or first passage
    first_text = " ".join(p.text for p in passages[:3])
    title = meta_dict.get("Title") or meta_dict.get("title") or pdf_path.stem
    if title.lower().startswith("untitled") or len(title.strip()) < 4:
        title = pdf_path.stem
    primary_doi = sorted(candidate_dois)[0] if candidate_dois else None
    detected_license = _detect_open_license(first_text, license_hint)

    access_status = (
        AccessStatus.FULL_TEXT_LOCAL.value
        if text_extractable_pages > 0
        else AccessStatus.UNAVAILABLE.value
    )
    ver_level = (
        VerificationLevel.EVIDENCE_LOCATED.value
        if text_extractable_pages > 0
        else VerificationLevel.SOURCE_LOCATED.value
    )

    source_record = SourceRecord(
        source_id=source_id,
        title=str(title).strip(),
        origin=SourceOrigin.PRIMARY_RESEARCH.value,
        access_status=access_status,
        verification_level=ver_level,
        url_or_path=str(pdf_path),
        local_path=str(pdf_path),
        sha256=file_sha,
        authors=[meta_dict["Author"]] if meta_dict.get("Author") else None,
        year=_extract_year_from_meta(meta_dict, first_text),
        doi=primary_doi,
        publication=meta_dict.get("Subject") or None,
        peer_reviewed=True if primary_doi else None,
        license_name=detected_license,
        abstract_text=next((p.text for p in passages if p.section.lower() == "abstract"), None),
        inspected_pages=target_pages,
        total_pages=total_pages,
        usage_notes=(
            f"Parsed {len(target_pages)}/{total_pages} pages ({text_extractable_pages} with extractable text); "
            f"extracted {len(passages)} passages and {len(assets)} figures/tables."
        ),
    )

    # Update rights on assets if open license detected on document level
    if detected_license:
        for a in assets:
            a.rights = _build_rights_metadata(detected_license, first_text, source_title=source_record.title, doi=primary_doi)

    return {
        "source": source_record,
        "passages": passages,
        "assets": assets,
        "page_summaries": page_summaries,
        "candidate_dois_unverified": sorted(candidate_dois),
    }


def _detect_open_license(text: str, license_hint: Optional[str] = None) -> Optional[str]:
    if license_hint:
        return license_hint
    lower = text.lower()
    if "creative commons attribution" in lower or "cc by" in lower or "cc-by" in lower:
        return "CC-BY-4.0"
    if "cc0" in lower or "public domain" in lower:
        return "CC0-1.0"
    return None


def _build_rights_metadata(
    license_hint: Optional[str],
    context_text: str,
    source_title: str = "",
    doi: Optional[str] = None,
) -> RightsMetadata:
    lic = _detect_open_license(context_text, license_hint)
    attr = f"Source: {source_title}" + (f" (DOI: {doi})" if doi else "")
    if lic and ("CC-BY" in lic.upper() or "CC0" in lic.upper()):
        return RightsMetadata(
            status=RightsStatus.ALLOWED.value,
            basis=f"Open-access license detected ({lic}); reuse permitted with proper attribution.",
            attribution=attr or f"Licensed under {lic}",
            license_id=lic,
            commercial_use_allowed=True,
            derivatives_allowed=True,
        )
    return RightsMetadata(
        status=RightsStatus.UNKNOWN.value,
        basis="No explicit open-access reuse license verified in document header; requires copyright clearance before commercial republication.",
        attribution=attr or "Original authors / publisher copyright applies.",
        license_id=None,
        commercial_use_allowed=None,
        derivatives_allowed=None,
    )


def _extract_year_from_meta(meta: Dict[str, str], first_text: str) -> Optional[int]:
    for k in ("CreationDate", "ModDate", "Date"):
        val = meta.get(k, "")
        m = re.search(r"(19\d{2}|20[0-2]\d)", val)
        if m:
            return int(m.group(1))
    m2 = re.search(r"\b(19\d{2}|20[0-2]\d)\b", first_text)
    return int(m2.group(1)) if m2 else None

