#!/usr/bin/env python3
"""Inspect a PDF before conversion and emit a machine-readable routing report.

PyMuPDF is preferred because it exposes text blocks, image objects and page
geometry.  If it is not installed, Poppler's ``pdfinfo``/``pdftotext`` are
used for a deliberately conservative text-only inspection.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    import pymupdf as fitz  # type: ignore
except ImportError:
    try:
        import fitz  # type: ignore
    except ImportError:  # pragma: no cover - exercised by the dependency-free path
        fitz = None

try:
    from pipeline_utils import json_dump, parse_pages, run_command
except ImportError:  # pragma: no cover - package-style import for callers
    from .pipeline_utils import json_dump, parse_pages, run_command


FORMULA_RE = re.compile(
    r"(?:[=^]|[∑√∞±×÷]|\\(?:frac|sqrt|sum|int|alpha|beta|gamma|delta|theta|lambda)|"
    r"[α-ωΑ-Ω])"
)
TABLE_WORD_RE = re.compile(r"(?:表格|表\s*(?:\d+|[一二三四五六七八九十]+)|\btable(?:\s+\d+)?\b)", re.I)
PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _command_available(*names: str) -> bool:
    return any(shutil.which(name) for name in names)


def available_engines() -> dict[str, dict[str, Any]]:
    """Report capability without importing heavyweight optional engines."""

    return {
        "pymupdf": {"available": fitz is not None, "kind": "python"},
        "pymupdf4llm": {"available": _module_available("pymupdf4llm"), "kind": "python"},
        "marker": {
            "available": _module_available("marker") or _command_available("marker_single", "marker"),
            "kind": "optional-local",
        },
        "docling": {
            "available": _module_available("docling") or _command_available("docling"),
            "kind": "optional-local",
        },
        "mineru": {
            "available": _module_available("mineru") or _command_available("mineru"),
            "kind": "optional-local",
        },
        "poppler": {
            "available": _command_available("pdftotext"),
            "kind": "native",
        },
        "tesseract": {
            "available": _command_available("tesseract"),
            "kind": "native-optional",
        },
    }


def _formula_metrics(text: str) -> tuple[int, float]:
    hits = 0
    for line in text.splitlines():
        compact = re.sub(r"\s+", "", line)
        if not compact or re.search(r"电话|联系|印刷|装订|教材意见|出版|网址|版权所有", line):
            continue
        if "×" in line and not re.search(r"\d|=|[A-Za-z]{2,}|10", line):
            continue
        if FORMULA_RE.search(line):
            hits += len(FORMULA_RE.findall(line))
    meaningful = max(1, len(re.sub(r"\s+", "", text)))
    return hits, hits / meaningful


def _table_suspected(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if TABLE_WORD_RE.search(text):
        return True
    aligned_rows = 0
    for line in lines:
        if "\t" in line or len(re.findall(r"\s{2,}", line)) >= 2:
            aligned_rows += 1
    return aligned_rows >= 3


def _multi_column_suspected(blocks: list[tuple[Any, ...]], width: float, height: float) -> bool:
    text_blocks = [block for block in blocks if len(block) >= 5 and str(block[4]).strip()]
    if len(text_blocks) < 4 or width <= 0:
        return False
    starts = sorted(float(block[0]) for block in text_blocks)
    gaps = [(starts[index + 1] - starts[index], index) for index in range(len(starts) - 1)]
    if not gaps:
        return False
    gap, split_index = max(gaps)
    if gap < width * 0.16:
        return False
    split = (starts[split_index] + starts[split_index + 1]) / 2
    left = [block for block in text_blocks if float(block[0]) < split]
    right = [block for block in text_blocks if float(block[0]) >= split]
    if len(left) < 2 or len(right) < 2:
        return False
    left_y = (min(float(block[1]) for block in left), max(float(block[3]) for block in left))
    right_y = (min(float(block[1]) for block in right), max(float(block[3]) for block in right))
    overlap = max(0.0, min(left_y[1], right_y[1]) - max(left_y[0], right_y[0]))
    return overlap > height * 0.18


def _garbage_metrics(text: str) -> tuple[float, int]:
    if not text:
        return 0.0, 0
    bad = text.count("�") + len(PRIVATE_USE_RE.findall(text))
    controls = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t\f")
    bad += controls
    return bad / max(1, len(text)), bad


def _record_from_fitz(page_number: int, page: Any) -> dict[str, Any]:
    text = page.get_text("text") or ""
    blocks = page.get_text("blocks") or []
    chars = len(text.strip())
    formula_hits, formula_density = _formula_metrics(text)
    garbage_ratio, garbage_count = _garbage_metrics(text)
    width = float(page.rect.width)
    height = float(page.rect.height)
    image_count = len(page.get_images(full=True))
    scan_suspected = chars < 30 and (image_count > 0 or chars == 0)
    return {
        "pdf_page": page_number,
        "text_chars": chars,
        "word_count": len(re.findall(r"\S+", text)),
        "image_count": image_count,
        "width_pt": round(width, 2),
        "height_pt": round(height, 2),
        "has_text": chars > 0,
        "scan_suspected": scan_suspected,
        "multi_column_suspected": _multi_column_suspected(blocks, width, height),
        "formula_hits": formula_hits,
        "formula_density": round(formula_density, 5),
        "formula_suspected": formula_hits >= 2 or formula_density >= 0.01,
        "table_suspected": _table_suspected(text),
        "garbage_ratio": round(garbage_ratio, 5),
        "garbage_count": garbage_count,
        "garbage_risk": garbage_ratio >= 0.01 or garbage_count >= 3,
    }


def _pdfinfo(path: Path) -> dict[str, str]:
    command = shutil.which("pdfinfo")
    if not command:
        return {}
    result = run_command([command, os_fspath(path)])
    info: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            info[key.strip()] = value.strip()
    return info


def os_fspath(path: Path) -> str:
    # Kept as a tiny helper so the fallback code is easy to monkeypatch in tests.
    return str(path)


def _record_from_poppler(page_number: int, text: str, size: tuple[float, float] | None) -> dict[str, Any]:
    chars = len(text.strip())
    formula_hits, formula_density = _formula_metrics(text)
    garbage_ratio, garbage_count = _garbage_metrics(text)
    width, height = size or (None, None)
    return {
        "pdf_page": page_number,
        "text_chars": chars,
        "word_count": len(re.findall(r"\S+", text)),
        "image_count": None,
        "width_pt": round(width, 2) if width else None,
        "height_pt": round(height, 2) if height else None,
        "has_text": chars > 0,
        "scan_suspected": chars < 30,
        "multi_column_suspected": False,
        "formula_hits": formula_hits,
        "formula_density": round(formula_density, 5),
        "formula_suspected": formula_hits >= 2 or formula_density >= 0.01,
        "table_suspected": _table_suspected(text),
        "garbage_ratio": round(garbage_ratio, 5),
        "garbage_count": garbage_count,
        "garbage_risk": garbage_ratio >= 0.01 or garbage_count >= 3,
    }


def _parse_size(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    match = re.search(r"([0-9.]+)\s*x\s*([0-9.]+)", value)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def _recommend(records: list[dict[str, Any]], engines: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    count = max(1, len(records))
    scan_ratio = sum(bool(record["scan_suspected"]) for record in records) / count
    formula_ratio = sum(bool(record["formula_suspected"]) for record in records) / count
    complex_ratio = sum(
        bool(record["multi_column_suspected"] or record["table_suspected"] or record["garbage_risk"])
        for record in records
    ) / count
    if scan_ratio >= 0.25:
        preferred = "mineru"
        reason = "scan_or_ocr_pages"
    elif formula_ratio >= 0.25 or complex_ratio >= 0.35:
        preferred = "marker"
        reason = "complex_layout_or_formula_pages"
    elif complex_ratio >= 0.15:
        preferred = "docling"
        reason = "moderate_layout_complexity"
    else:
        preferred = "pymupdf"
        reason = "born_digital_low_complexity"

    fallback = [preferred]
    for candidate in ("pymupdf", "docling", "marker", "mineru"):
        if candidate not in fallback:
            fallback.append(candidate)
    available_fallback = [name for name in fallback if engines.get(name, {}).get("available")]
    return preferred, [reason, *available_fallback]


def inspect_pdf(path: Path, page_spec: str | None = None) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    engines = available_engines()
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    if fitz is not None:
        document = fitz.open(str(path))
        try:
            pages = parse_pages(page_spec, document.page_count)
            for page_number in pages:
                records.append(_record_from_fitz(page_number, document.load_page(page_number - 1)))
            total_pages = document.page_count
        finally:
            document.close()
        extraction_backend = "pymupdf"
    else:
        info = _pdfinfo(path)
        try:
            total_pages = int(info.get("Pages", "0"))
        except ValueError:
            total_pages = 0
        if total_pages < 1:
            raise RuntimeError("PyMuPDF is unavailable and pdfinfo could not determine the page count")
        pages = parse_pages(page_spec, total_pages)
        text_command = shutil.which("pdftotext")
        if not text_command:
            raise RuntimeError("neither PyMuPDF nor pdftotext is available")
        size = _parse_size(info.get("Page size"))
        for page_number in pages:
            result = run_command([text_command, "-f", str(page_number), "-l", str(page_number), "-layout", str(path), "-"])
            records.append(_record_from_poppler(page_number, result.stdout, size))
        warnings.append("PyMuPDF is not installed; image bounds and column heuristics are unavailable")
        extraction_backend = "poppler-pdftotext"

    count = max(1, len(records))
    scan_ratio = sum(bool(record["scan_suspected"]) for record in records) / count
    formula_pages = sum(bool(record["formula_suspected"]) for record in records)
    formula_density = "high" if formula_pages / count >= 0.25 else "medium" if formula_pages else "low"
    complex_score = sum(
        bool(record["multi_column_suspected"] or record["table_suspected"] or record["garbage_risk"])
        for record in records
    ) / count
    layout_complexity = "high" if complex_score >= 0.35 else "medium" if complex_score >= 0.15 else "low"
    preferred, route = _recommend(records, engines)
    if preferred != "pymupdf" and not engines.get(preferred, {}).get("available"):
        warnings.append(f"recommended engine {preferred!r} is not installed; auto mode will use the best available fallback")
    if any(record["garbage_risk"] for record in records):
        warnings.append("one or more pages contain replacement/private/control characters")
    if any(record["scan_suspected"] for record in records):
        warnings.append("one or more pages may require OCR")

    return {
        "schema_version": 1,
        "source": str(path),
        "pages": total_pages,
        "inspected_pages": [record["pdf_page"] for record in records],
        "inspected_page_count": len(records),
        "text_layer": any(record["has_text"] for record in records),
        "scan_ratio": round(scan_ratio, 4),
        "layout_complexity": layout_complexity,
        "formula_density": formula_density,
        "scan_pages": [record["pdf_page"] for record in records if record["scan_suspected"]],
        "multi_column_pages": [record["pdf_page"] for record in records if record["multi_column_suspected"]],
        "formula_pages": [record["pdf_page"] for record in records if record["formula_suspected"]],
        "table_pages": [record["pdf_page"] for record in records if record["table_suspected"]],
        "garbage_pages": [record["pdf_page"] for record in records if record["garbage_risk"]],
        "recommended_engine": preferred,
        "auto_route": route,
        "extraction_backend": extraction_backend,
        "available_engines": engines,
        "page_records": records,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="input PDF")
    parser.add_argument("--pages", help="one-based range, for example 1-20 or 1-3,7")
    parser.add_argument("--json", type=Path, help="write the inspection report to this JSON file")
    args = parser.parse_args()
    try:
        report = inspect_pdf(args.pdf, args.pages)
    except Exception as exc:  # concise CLI error, with a non-zero status
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    json_dump(report, args.json)
    if args.json:
        print(f"inspection: {args.json.resolve()}")
        print(f"pages: {report['pages']} (inspected {report['inspected_page_count']})")
        print(f"recommended engine: {report['recommended_engine']}")
        for warning in report["warnings"]:
            print(f"WARN: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
