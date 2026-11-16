"""Scientific literature discovery, metadata normalization, deduplication, and ranking.

Adapts multi-source literature discovery patterns (PaperSeek + Crossref/OpenAlex REST schemas).
Strictly separates metadata/abstract hits from verified full-text evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
import urllib.parse
import urllib.request

from .models import (
    AccessStatus,
    PassageRecord,
    ProcessingRecord,
    SourceOrigin,
    SourceRecord,
    VerificationLevel,
    compute_sha256,
)

DOI_PATTERN = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
TAG_RE = re.compile(r"<[^>]+>")
TOKEN_RE = re.compile(r"[a-z0-9\u4e00-\u9fff]+")


def normalize_doi(raw_doi: Optional[str]) -> Optional[str]:
    if not raw_doi or not isinstance(raw_doi, str):
        return None
    cleaned = raw_doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:", "DOI:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    m = DOI_PATTERN.search(cleaned)
    if not m:
        return None
    return m.group(1).rstrip(".,;)").lower()


def clean_html_text(raw: Optional[str]) -> str:
    if not raw:
        return ""
    unescaped = html.unescape(raw)
    stripped = TAG_RE.sub(" ", unescaped)
    return re.sub(r"\s+", " ", stripped).strip()


def tokenize_text(text: str) -> List[str]:
    if not text:
        return []
    return TOKEN_RE.findall(text.lower())


def title_similarity(title_a: str, title_b: str) -> float:
    tokens_a = set(tokenize_text(title_a))
    tokens_b = set(tokenize_text(title_b))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def build_layered_search_strategy(
    topic: str,
    research_questions: Optional[List[str]] = None,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
) -> Dict[str, Any]:
    """Formulate a structured multi-layer search strategy from a scientific topic."""
    questions = research_questions or [f"What are the primary experimental findings and mechanisms for {topic}?"]
    tokens = [t for t in tokenize_text(topic) if len(t) >= 2]
    core_terms = tokens[:8] if tokens else [topic.strip()]
    query_string = " AND ".join(f'"{t}"' if " " in t else t for t in core_terms[:5])
    return {
        "topic": topic,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_window": {"year_min": year_min, "year_max": year_max},
        "questions": questions,
        "layers": [
            {
                "layer": "foundational_and_reviews",
                "goal": "Establish terminology, baseline mechanisms, and evaluation metrics",
                "query": f"({query_string}) AND (review OR mechanism OR principle)",
            },
            {
                "layer": "primary_empirical",
                "goal": "Locate primary experimental conditions, quantitative performance, and controls",
                "query": f"({query_string}) AND (experiment OR sensitivity OR selectivity OR detection OR yield)",
            },
            {
                "layer": "critical_and_limitations",
                "goal": "Identify interfering species, negative results, condition boundaries, or competing hypotheses",
                "query": f"({query_string}) AND (limitation OR interference OR comparison OR challenge)",
            },
        ],
        "governance_rule": "Search hits and metadata records are registered as METADATA_ONLY or ABSTRACT_ONLY until full text is verified.",
    }


def parse_crossref_or_custom_entry(
    entry: Dict[str, Any],
    idx: int,
    query: Optional[str] = None,
    retrieved_at: Optional[str] = None,
) -> Tuple[SourceRecord, List[PassageRecord]]:
    """Normalize a Crossref REST item, OpenAlex item, or local curated literature JSON record."""
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else entry
    raw_doi = entry.get("doi") or meta.get("DOI") or meta.get("doi")
    norm_doi = normalize_doi(str(raw_doi)) if raw_doi else None

    # Extract title
    title_val = entry.get("title") or meta.get("title") or meta.get("display_name") or ""
    if isinstance(title_val, list):
        title = clean_html_text(title_val[0] if title_val else "")
    else:
        title = clean_html_text(str(title_val))
    if not title:
        title = f"Untitled Scientific Source {idx}"

    # Extract authors
    authors: List[str] = []
    raw_authors = entry.get("authors") or meta.get("author") or meta.get("authorships") or []
    if isinstance(raw_authors, list):
        for a in raw_authors:
            if isinstance(a, str):
                authors.append(a.strip())
            elif isinstance(a, dict):
                if "given" in a or "family" in a:
                    name = f"{a.get('given', '')} {a.get('family', '')}".strip()
                    if name:
                        authors.append(name)
                elif "author" in a and isinstance(a["author"], dict):
                    dn = a["author"].get("display_name", "").strip()
                    if dn:
                        authors.append(dn)
                elif "name" in a:
                    authors.append(str(a["name"]).strip())

    # Extract publication year
    year: Optional[int] = None
    if isinstance(entry.get("year"), int):
        year = entry["year"]
    elif isinstance(meta.get("publication_year"), int):
        year = meta["publication_year"]
    else:
        for date_key in ("published-print", "published-online", "published", "created", "issued"):
            d_obj = meta.get(date_key)
            if isinstance(d_obj, dict):
                parts = d_obj.get("date-parts")
                if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
                    if isinstance(parts[0][0], int):
                        year = parts[0][0]
                        break

    # Extract publication venue
    pub_val = (
        entry.get("publication")
        or meta.get("container-title")
        or meta.get("short-container-title")
        or meta.get("publisher")
        or ""
    )
    if isinstance(pub_val, list):
        publication = clean_html_text(pub_val[0] if pub_val else "")
    else:
        publication = clean_html_text(str(pub_val))

    # Extract abstract or curated summary notes
    raw_abstract = entry.get("abstract") or meta.get("abstract") or ""
    abstract_text = clean_html_text(str(raw_abstract))
    curated_evidence_note = clean_html_text(str(entry.get("evidence") or ""))
    platform_note = clean_html_text(str(entry.get("platform") or ""))
    mechanism_note = clean_html_text(str(entry.get("mechanism") or ""))
    validation_note = clean_html_text(str(entry.get("validation") or ""))
    ros_note = clean_html_text(str(entry.get("ros") or ""))

    combined_notes_parts: List[str] = []
    if ros_note:
        combined_notes_parts.append(f"Target/Analyte: {ros_note}")
    if platform_note:
        combined_notes_parts.append(f"Platform: {platform_note}")
    if mechanism_note:
        combined_notes_parts.append(f"Mechanism: {mechanism_note}")
    if validation_note:
        combined_notes_parts.append(f"Validation: {validation_note}")
    if curated_evidence_note:
        combined_notes_parts.append(f"Curated summary: {curated_evidence_note}")
    synthesized_summary = "; ".join(combined_notes_parts)

    effective_abstract = abstract_text or synthesized_summary

    # Check if a local full-text PDF exists
    local_pdf = entry.get("local_path") or entry.get("pdf_path")
    sha256_val: Optional[str] = None
    access_status = AccessStatus.ABSTRACT_ONLY.value if effective_abstract else AccessStatus.METADATA_ONLY.value
    verification_level = (
        VerificationLevel.EVIDENCE_LOCATED.value
        if effective_abstract
        else VerificationLevel.SOURCE_LOCATED.value
    )

    if local_pdf:
        p = Path(str(local_pdf))
        if p.is_file():
            sha256_val = compute_sha256(p)
            access_status = AccessStatus.FULL_TEXT_LOCAL.value
            verification_level = VerificationLevel.SEMANTICALLY_CHECKED.value

    # License
    license_name: Optional[str] = entry.get("license")
    if not license_name and isinstance(meta.get("license"), list) and meta["license"]:
        first_lic = meta["license"][0]
        if isinstance(first_lic, dict):
            license_name = first_lic.get("URL") or first_lic.get("content-version")

    doc_type = str(meta.get("type") or entry.get("origin") or "journal-article").lower()
    origin = SourceOrigin.REVIEW.value if "review" in doc_type or "综述" in str(entry.get("role", "")) else SourceOrigin.PRIMARY_RESEARCH.value
    url_or_path = (
        str(entry.get("url_or_path") or meta.get("URL") or (f"https://doi.org/{norm_doi}" if norm_doi else f"local:record-{idx:03d}"))
    )

    source_id = str(entry.get("source_id") or f"SRC{idx:03d}")
    now_str = retrieved_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    usage_notes = (
        f"Ingested via literature discovery ({access_status}); "
        f"{'abstract/curated summary available, full PDF not attached (marked ABSTRACT_ONLY).' if access_status == AccessStatus.ABSTRACT_ONLY.value else 'metadata verified.'}"
    )
    if access_status == AccessStatus.FULL_TEXT_LOCAL.value:
        usage_notes = f"Full-text local PDF verified with SHA-256 ({sha256_val[:12]}...)."

    src = SourceRecord(
        source_id=source_id,
        title=title,
        origin=origin,
        access_status=access_status,
        verification_level=verification_level,
        url_or_path=url_or_path,
        local_path=str(local_pdf) if local_pdf else None,
        sha256=sha256_val,
        authors=authors if authors else None,
        year=year,
        doi=norm_doi,
        publication=publication or None,
        peer_reviewed=True if norm_doi else None,
        license_name=license_name,
        retrieval_query=query,
        retrieved_at=now_str,
        abstract_text=effective_abstract or None,
        usage_notes=usage_notes,
        extra={
            k: entry[k]
            for k in ("ros", "platform", "mechanism", "validation", "role", "evidence")
            if k in entry
        },
    )

    passages: List[PassageRecord] = []
    if effective_abstract:
        passages.append(
            PassageRecord(
                passage_id=f"PSG_{source_id}_ABS",
                source_id=source_id,
                page=1,
                section="Abstract" if abstract_text else "Curated_Metadata_Summary",
                text=effective_abstract,
                char_span=(0, len(effective_abstract)),
                figure_mentions=[],
                candidate_dois=[norm_doi] if norm_doi else [],
            )
        )
    return src, passages


def deduplicate_sources(
    sources_with_passages: Iterable[Tuple[SourceRecord, List[PassageRecord]]],
    title_threshold: float = 0.85,
) -> Tuple[List[SourceRecord], List[PassageRecord], List[Dict[str, Any]]]:
    """Deduplicate sources by normalized DOI first, then by high token Jaccard title similarity."""
    unique_sources: List[SourceRecord] = []
    all_passages: List[PassageRecord] = []
    duplicates_log: List[Dict[str, Any]] = []
    seen_dois: Dict[str, SourceRecord] = {}

    access_priority = {
        AccessStatus.FULL_TEXT_LOCAL.value: 5,
        AccessStatus.USER_DATA_VERIFIED.value: 4,
        AccessStatus.ABSTRACT_ONLY.value: 3,
        AccessStatus.USER_NOTES_ONLY.value: 2,
        AccessStatus.METADATA_ONLY.value: 1,
        AccessStatus.UNAVAILABLE.value: 0,
    }

    for src, passages in sources_with_passages:
        norm_doi = normalize_doi(src.doi)
        if norm_doi and norm_doi in seen_dois:
            existing = seen_dois[norm_doi]
            duplicates_log.append({
                "dropped_source_id": src.source_id,
                "kept_source_id": existing.source_id,
                "reason": f"exact_doi_match:{norm_doi}",
                "title": src.title,
            })
            # Upgrade existing if new record has fuller access
            if access_priority.get(src.access_status, 0) > access_priority.get(existing.access_status, 0):
                existing.access_status = src.access_status
                existing.local_path = src.local_path
                existing.sha256 = src.sha256
            if not existing.abstract_text and src.abstract_text:
                existing.abstract_text = src.abstract_text
            continue

        matched_existing: Optional[SourceRecord] = None
        for cand in unique_sources:
            sim = title_similarity(src.title, cand.title)
            if sim >= title_threshold and (src.year == cand.year or src.year is None or cand.year is None):
                matched_existing = cand
                duplicates_log.append({
                    "dropped_source_id": src.source_id,
                    "kept_source_id": cand.source_id,
                    "reason": f"title_jaccard_similarity:{sim:.3f}",
                    "title": src.title,
                })
                break

        if matched_existing is not None:
            if not matched_existing.doi and norm_doi:
                matched_existing.doi = norm_doi
                seen_dois[norm_doi] = matched_existing
            continue

        if norm_doi:
            src.doi = norm_doi
            seen_dois[norm_doi] = src
        unique_sources.append(src)
        all_passages.extend(passages)

    # Re-index source IDs cleanly if needed while preserving passage links
    return unique_sources, all_passages, duplicates_log


def score_source_relevance(src: SourceRecord, query_terms: List[str]) -> float:
    if not query_terms:
        return 1.0
    haystack = " ".join(
        filter(
            None,
            [
                src.title,
                src.abstract_text or "",
                src.publication or "",
                json.dumps(src.extra, ensure_ascii=False) if src.extra else "",
            ],
        )
    ).lower()
    hits = sum(1 for term in query_terms if term.lower() in haystack)
    base_score = hits / len(query_terms)
    bonus = 0.25 if src.access_status == AccessStatus.FULL_TEXT_LOCAL.value else (
        0.10 if src.access_status == AccessStatus.ABSTRACT_ONLY.value else 0.0
    )
    return round(min(1.0, base_score + bonus), 4)


def discover_from_local_catalog(
    catalog_path: Path,
    query: str = "",
    max_results: int = 25,
    start_index: int = 1,
) -> Dict[str, Any]:
    """Discover, deduplicate, and rank literature from a local Crossref/OpenAlex/curated JSON catalog."""
    raw = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    items: List[Dict[str, Any]]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("items") or raw.get("results") or raw.get("sources") or raw.get("message", {}).get("items", [])
    else:
        raise ValueError(f"Unsupported catalog structure in {catalog_path}")

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parsed_pairs: List[Tuple[SourceRecord, List[PassageRecord]]] = []
    for idx, entry in enumerate(items, start=start_index):
        if isinstance(entry, dict):
            parsed_pairs.append(parse_crossref_or_custom_entry(entry, idx, query=query, retrieved_at=now_str))

    unique_sources, passages, dup_log = deduplicate_sources(parsed_pairs)
    query_terms = [t for t in tokenize_text(query) if len(t) >= 2]

    scored: List[Tuple[float, SourceRecord]] = []
    for s in unique_sources:
        score = score_source_relevance(s, query_terms)
        s.extra["relevance_score"] = score
        scored.append((score, s))

    scored.sort(key=lambda pair: (pair[0], pair[1].year or 0), reverse=True)
    selected_sources = [s for score, s in scored if score > 0 or not query_terms][:max_results]
    selected_ids = {s.source_id for s in selected_sources}
    selected_passages = [p for p in passages if p.source_id in selected_ids]

    strategy = build_layered_search_strategy(query or catalog_path.stem)
    return {
        "strategy": strategy,
        "catalog_path": str(catalog_path.resolve()),
        "total_raw_records": len(items),
        "deduplicated_count": len(unique_sources),
        "duplicates_removed": dup_log,
        "selected_sources": selected_sources,
        "selected_passages": selected_passages,
    }


def query_crossref_online(
    query: str,
    rows: int = 5,
    timeout_sec: float = 8.0,
    start_index: int = 1,
) -> Dict[str, Any]:
    """Optional live Crossref REST query with graceful error reporting when offline/sandboxed."""
    encoded = urllib.parse.quote(query)
    url = f"https://api.crossref.org/works?query={encoded}&rows={int(rows)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ResearchEvidenceAssetCore/1.0 (mailto:research-core@example.org)"},
    )
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        items = payload.get("message", {}).get("items", [])
        pairs = [
            parse_crossref_or_custom_entry(item, idx, query=query, retrieved_at=now_str)
            for idx, item in enumerate(items, start=start_index)
        ]
        sources, passages, dup_log = deduplicate_sources(pairs)
        return {
            "status": "ONLINE_OK",
            "url": url,
            "sources": sources,
            "passages": passages,
            "duplicates_removed": dup_log,
        }
    except Exception as exc:
        return {
            "status": "OFFLINE_OR_BLOCKED",
            "url": url,
            "error": str(exc),
            "sources": [],
            "passages": [],
            "duplicates_removed": [],
        }

