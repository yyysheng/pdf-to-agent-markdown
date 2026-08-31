#!/usr/bin/env python3
"""Convert a PDF to analysis-ready Markdown with stable page markers.

The converter is intentionally usable with only Poppler.  When PyMuPDF is
installed it adds block-aware ordering, image-object crops and formula-region
captures.  Marker, Docling and MinerU are optional adapters: auto mode chooses
them only when inspection/quality signals justify escalation and the engine is
available in the current environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import pymupdf as fitz  # type: ignore
except ImportError:
    try:
        import fitz  # type: ignore
    except ImportError:  # pragma: no cover - dependency-free fallback is tested
        fitz = None

try:
    from inspect_pdf import FORMULA_RE, inspect_pdf
except ImportError:  # pragma: no cover - package-style import for callers
    from .inspect_pdf import FORMULA_RE, inspect_pdf

try:
    from pipeline_utils import (
        compact_page_range,
        find_repeated_edge_lines,
        json_dump,
        normalize_line,
        parse_pages,
        run_command,
        safe_stem,
        strip_repeated_edge_lines,
    )
except ImportError:  # pragma: no cover - package-style import for callers
    from .pipeline_utils import (
        compact_page_range,
        find_repeated_edge_lines,
        json_dump,
        normalize_line,
        parse_pages,
        run_command,
        safe_stem,
        strip_repeated_edge_lines,
    )


SCIENTIFIC_RE = re.compile(
    r"(?P<number>\d+(?:\.\d+)?)\s*(?:×|x|X|\*)\s*10\s*(?P<exponent>[−-]?\d+)"
)
TABLE_HINT_RE = re.compile(r"(?:表格|表\s*(?:\d+|[一二三四五六七八九十]+)|\btable(?:\s+\d+)?\b)", re.I)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg"}


@dataclass
class Options:
    input_pdf: Path
    output_dir: Path
    pages: list[int]
    requested_engine: str
    keep_page_markers: bool = True
    extract_images: bool = True
    crop_images: bool = True
    ocr: bool = False
    ocr_lang: str = "eng"
    table_mode: str = "both"
    formula_mode: str = "latex"
    printed_page_offset: int | None = None
    printed_page_map: Path | None = None
    quality_gate: bool = True


@dataclass
class Asset:
    stem: str
    kind: str
    pdf_page: int
    bbox: list[float] | None
    description: str

    @property
    def filename(self) -> str:
        return f"{self.stem}.png"

    @property
    def markdown(self) -> str:
        # The alt text deliberately uses the exact stable asset stem.  A
        # human-readable description lives in the manifest and nearby text.
        return f"![{self.stem}]({self.filename})"


@dataclass
class PageResult:
    pdf_page: int
    printed_page: int | None
    raw_text: str
    text: str
    text_chars: int
    engine: str
    assets: list[Asset] = field(default_factory=list)
    formula_blocks: int = 0
    formula_converted: int = 0
    formula_uncertain: int = 0
    table_detected: bool = False
    table_structured: bool = False
    warnings: list[str] = field(default_factory=list)
    quality_status: str = "PASS"


def _available_command(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _read_printed_map(path: Path | None) -> dict[int, int]:
    if not path:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("mapping"), dict):
        data = data["mapping"]
    if not isinstance(data, dict):
        raise ValueError("printed page map must be a JSON object")
    result: dict[int, int] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            value = value.get("printed_page")
        if value is not None:
            result[int(key)] = int(value)
    return result


def _candidate_printed_page(text: str) -> int | None:
    lines = [normalize_line(line) for line in text.splitlines() if normalize_line(line)]
    for line in reversed(lines[-8:]):
        line = line.strip("-–—· ")
        if re.fullmatch(r"\d{1,4}", line):
            return int(line)
    return None


def _detect_printed_pages(texts: dict[int, str], options: Options) -> dict[int, int]:
    explicit = _read_printed_map(options.printed_page_map)
    if explicit:
        return {page: explicit[page] for page in texts if page in explicit}
    if options.printed_page_offset is not None:
        return {page: page + options.printed_page_offset for page in texts}

    candidates = {page: _candidate_printed_page(text) for page, text in texts.items()}
    candidates = {page: value for page, value in candidates.items() if value is not None}
    if len(candidates) < 2:
        return {}
    # Accept only a sequence that proves itself over at least two adjacent PDF
    # pages.  A lone number in body text must not become a guessed page map.
    accepted: dict[int, int] = {}
    ordered = sorted(candidates)
    run_pages: list[int] = [ordered[0]]
    for page in ordered[1:]:
        if page == run_pages[-1] + 1 and candidates[page] == candidates[run_pages[-1]] + 1:
            run_pages.append(page)
        else:
            if len(run_pages) >= 2:
                accepted.update({item: candidates[item] for item in run_pages})
            run_pages = [page]
    if len(run_pages) >= 2:
        accepted.update({item: candidates[item] for item in run_pages})
    return accepted


def _block_text(block: dict[str, Any]) -> str:
    if block.get("text"):
        return str(block["text"])
    lines: list[str] = []
    for line in block.get("lines", []):
        spans = [str(span.get("text", "")) for span in line.get("spans", [])]
        if spans:
            lines.append("".join(spans))
    return "\n".join(lines)


def _dedupe_bboxes(bboxes: Iterable[Iterable[float]], tolerance: float = 2.0) -> list[list[float]]:
    result: list[list[float]] = []
    for raw in bboxes:
        bbox = [float(value) for value in raw]
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        if any(all(abs(bbox[index] - other[index]) <= tolerance for index in range(4)) for other in result):
            continue
        result.append(bbox)
    return result


def _ordered_fitz_blocks(page: Any, multi_column: bool) -> tuple[list[dict[str, Any]], list[list[float]]]:
    data = page.get_text("dict") or {}
    text_blocks: list[dict[str, Any]] = []
    image_bboxes: list[list[float]] = []
    for block in data.get("blocks", []):
        if block.get("type") == 0:
            text = _block_text(block).strip()
            if text:
                item = dict(block)
                item["text"] = text
                item["bbox"] = [float(value) for value in block.get("bbox", (0, 0, 0, 0))]
                text_blocks.append(item)
        elif block.get("type") == 1 and block.get("bbox"):
            image_bboxes.append([float(value) for value in block["bbox"]])

    if multi_column and len(text_blocks) >= 4:
        starts = sorted(block["bbox"][0] for block in text_blocks)
        gaps = [(starts[index + 1] - starts[index], index) for index in range(len(starts) - 1)]
        gap, split_index = max(gaps, default=(0.0, 0))
        if gap > page.rect.width * 0.16:
            split = (starts[split_index] + starts[split_index + 1]) / 2
            left = [block for block in text_blocks if block["bbox"][0] < split]
            right = [block for block in text_blocks if block["bbox"][0] >= split]
            text_blocks = sorted(left, key=lambda block: (block["bbox"][1], block["bbox"][0]))
            text_blocks += sorted(right, key=lambda block: (block["bbox"][1], block["bbox"][0]))
        else:
            text_blocks.sort(key=lambda block: (block["bbox"][1], block["bbox"][0]))
    else:
        text_blocks.sort(key=lambda block: (block["bbox"][1], block["bbox"][0]))

    # Some PDFs expose image xrefs but do not emit image blocks in the text
    # dictionary. Rect lookup is a fallback only: adding both sources creates
    # many overlapping duplicate crops in textbooks with tiled backgrounds.
    if not image_bboxes:
        for image in page.get_images(full=True):
            try:
                xref = image[0]
                image_bboxes.extend([[float(value) for value in rect] for rect in page.get_image_rects(xref)])
            except Exception:
                continue
    return text_blocks, _dedupe_bboxes(image_bboxes)


def _poppler_text(path: Path, page: int) -> str:
    command = _available_command("pdftotext")
    if not command:
        raise RuntimeError("PyMuPDF and pdftotext are both unavailable")
    result = run_command([command, "-f", str(page), "-l", str(page), "-layout", str(path), "-"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"pdftotext failed for page {page}")
    return result.stdout


def _ocr_page(path: Path, page: int, language: str) -> str | None:
    pdftoppm = _available_command("pdftoppm")
    tesseract = _available_command("tesseract")
    if not pdftoppm or not tesseract:
        return None
    with tempfile.TemporaryDirectory(prefix="pdf_md_ocr_") as temp_dir:
        image = Path(temp_dir) / "page.png"
        rendered = run_command(
            [pdftoppm, "-f", str(page), "-l", str(page), "-singlefile", "-png", "-r", "200", str(path), str(image.with_suffix(""))],
            timeout=300,
        )
        if rendered.returncode != 0 or not image.is_file():
            return None
        result = run_command([tesseract, str(image), "stdout", "-l", language], timeout=300)
        if result.returncode != 0:
            return None
        return result.stdout


def _save_clip(page: Any, bbox: list[float], destination: Path, scale: float = 2.0) -> bool:
    if fitz is None:
        return False
    try:
        rect = fitz.Rect(bbox).intersect(page.rect)
        if rect.width < 3 or rect.height < 3:
            return False
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
        pixmap.save(str(destination))
        return destination.is_file() and destination.stat().st_size > 0
    except Exception:
        return False


def _union_bbox(bboxes: Iterable[list[float]], page: Any, padding: float = 8.0) -> list[float] | None:
    values = list(bboxes)
    if not values:
        return None
    x0 = max(0.0, min(value[0] for value in values) - padding)
    y0 = max(0.0, min(value[1] for value in values) - padding)
    x1 = min(float(page.rect.width), max(value[2] for value in values) + padding)
    y1 = min(float(page.rect.height), max(value[3] for value in values) + padding)
    return [x0, y0, x1, y1]


def _table_bbox(page: Any, text_blocks: list[dict[str, Any]]) -> list[float] | None:
    candidates: list[list[float]] = []
    for block in text_blocks:
        text = block.get("text", "")
        lines = [line for line in text.splitlines() if line.strip()]
        aligned = sum("\t" in line or len(re.findall(r"\s{2,}", line)) >= 2 for line in lines)
        if TABLE_HINT_RE.search(text) or aligned >= 2:
            candidates.append(block["bbox"])
    if len(candidates) < 2:
        return None
    return _union_bbox(candidates, page)


def _formula_bboxes(text_blocks: list[dict[str, Any]]) -> list[list[float]]:
    return [block["bbox"] for block in text_blocks if _formula_candidate(block.get("text", ""))]


def _filter_visual_bboxes(page: Any, bboxes: Iterable[list[float]], has_text: bool) -> list[list[float]]:
    """Drop full-page backgrounds and nested duplicate image tiles."""

    page_area = max(1.0, float(page.rect.width * page.rect.height))
    candidates: list[list[float]] = []
    for raw in bboxes:
        bbox = [
            max(0.0, min(float(page.rect.width), raw[0])),
            max(0.0, min(float(page.rect.height), raw[1])),
            max(0.0, min(float(page.rect.width), raw[2])),
            max(0.0, min(float(page.rect.height), raw[3])),
        ]
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        area = width * height
        if width < 20 or height < 20 or area < 400:
            continue
        if has_text and area / page_area >= 0.86:
            # Text-bearing pages with a full-page raster background should not
            # become a page screenshot just because the PDF has an image XObject.
            continue
        candidates.append(bbox)

    ordered = sorted(candidates, key=lambda value: (value[2] - value[0]) * (value[3] - value[1]), reverse=True)
    kept: list[list[float]] = []
    for bbox in ordered:
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        contained = False
        for parent in kept:
            parent_area = (parent[2] - parent[0]) * (parent[3] - parent[1])
            if (
                parent[0] <= bbox[0] + 1
                and parent[1] <= bbox[1] + 1
                and parent[2] >= bbox[2] - 1
                and parent[3] >= bbox[3] - 1
                and area / max(1.0, parent_area) < 0.9
            ):
                contained = True
                break
        if not contained:
            kept.append(bbox)
    return kept


def _formula_candidate(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    if not compact or not FORMULA_RE.search(line):
        return False
    if re.search(r"电话|联系|印刷|装订|教材意见|出版|网址|版权所有", line):
        return False
    if "×" in line and not re.search(r"\d|=|[A-Za-z]{2,}|10", line):
        return False
    if len(compact) <= 120:
        return True
    math_chars = sum(char in "=^∑√∞±×÷\\()[]{}" or char.isdigit() for char in compact)
    return math_chars / max(1, len(compact)) > 0.28


def _latexize_line(line: str) -> tuple[str, bool]:
    recognized = False

    def replace(match: re.Match[str]) -> str:
        nonlocal recognized
        recognized = True
        exponent = match.group("exponent").replace("−", "-")
        return f"${match.group('number')}\\times 10^{{{exponent}}}$"

    return SCIENTIFIC_RE.sub(replace, line), recognized


def _process_formula_text(text: str, mode: str) -> tuple[str, int, int, int]:
    blocks = converted = uncertain = 0
    output: list[str] = []
    for line in text.splitlines():
        candidate = _formula_candidate(line)
        if candidate:
            blocks += 1
        if mode == "latex":
            converted_line, did_convert = _latexize_line(line)
            if did_convert:
                converted += 1
                output.append(converted_line)
            else:
                if candidate:
                    uncertain += 1
                output.append(line)
        else:
            if candidate and mode in {"text", "image"}:
                uncertain += 1
            output.append(line)
    return "\n".join(output).strip(), blocks, converted, uncertain


def _structured_table(text: str) -> str | None:
    """Return a table only when the source already exposes clear separators."""

    candidates = [line.strip() for line in text.splitlines() if "|" in line and line.count("|") >= 2]
    if len(candidates) < 2:
        return None
    rows: list[list[str]] = []
    for line in candidates:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 2:
            rows.append(cells)
    if len(rows) < 2:
        return None
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = "| " + " | ".join(rows[0]) + " |"
    divider = "| " + " | ".join("---" for _ in range(width)) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join([header, divider, *body])


def _clean_page_text(text: str, repeated_edges: set[str]) -> str:
    text = text.replace("\f", "\n")
    text = strip_repeated_edge_lines(text, repeated_edges)
    lines: list[str] = []
    previous_blank = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            if not previous_blank:
                lines.append("")
            previous_blank = True
            continue
        lines.append(line)
        previous_blank = False
    return "\n".join(lines).strip()


def _asset_for_crop(
    page: Any,
    output_dir: Path,
    *,
    page_number: int,
    index: int,
    kind: str,
    bbox: list[float],
    description: str,
) -> Asset | None:
    stem = f"{kind}_p{page_number:03d}_{index:02d}"
    destination = output_dir / f"{stem}.png"
    if not _save_clip(page, bbox, destination):
        return None
    return Asset(stem=stem, kind=kind, pdf_page=page_number, bbox=bbox, description=description)


def _extract_fitz_page(
    document: Any,
    page_number: int,
    record: dict[str, Any],
    options: Options,
    repeated_edges: set[str],
) -> PageResult:
    page = document.load_page(page_number - 1)
    text_blocks, image_bboxes = _ordered_fitz_blocks(page, bool(record.get("multi_column_suspected")))
    raw_text = "\n\n".join(block["text"] for block in text_blocks).strip()
    warnings: list[str] = []
    if len(raw_text.strip()) < 20 and options.ocr:
        ocr_text = _ocr_page(options.input_pdf, page_number, options.ocr_lang)
        if ocr_text:
            raw_text = ocr_text
        else:
            warnings.append("OCR requested but pdftoppm/tesseract did not produce text")
    elif len(raw_text.strip()) < 20 and record.get("scan_suspected"):
        warnings.append("possible scan page has no usable text layer; OCR or MinerU is recommended")

    cleaned = _clean_page_text(raw_text, repeated_edges)
    processed, formula_blocks, formula_converted, formula_uncertain = _process_formula_text(cleaned, options.formula_mode)
    assets: list[Asset] = []
    asset_index = 1
    page_area = max(1.0, float(page.rect.width * page.rect.height))
    visual_bboxes = _filter_visual_bboxes(
        page,
        image_bboxes,
        bool(cleaned) and not bool(record.get("scan_suspected")),
    )
    for clipped in visual_bboxes:
        area = max(0.0, (clipped[2] - clipped[0]) * (clipped[3] - clipped[1]))
        is_full_page_scan = area / page_area >= 0.86 and (len(cleaned) < 30 or record.get("scan_suspected"))
        if is_full_page_scan:
            if options.extract_images and (options.ocr or record.get("scan_suspected")):
                asset = _asset_for_crop(
                    page,
                    options.output_dir,
                    page_number=page_number,
                    index=asset_index,
                    kind="scan",
                    bbox=clipped,
                    description="full-page scan retained because the page has no reliable text layer",
                )
                if asset:
                    assets.append(asset)
                    asset_index += 1
            continue
        if options.extract_images and options.crop_images:
            asset = _asset_for_crop(
                page,
                options.output_dir,
                page_number=page_number,
                index=asset_index,
                kind="fig",
                bbox=clipped,
                description="embedded PDF image object crop",
            )
            if asset:
                assets.append(asset)
                asset_index += 1

    table_detected = bool(record.get("table_suspected"))
    table_structured = False
    table_text = _structured_table(processed) if table_detected and options.table_mode in {"markdown", "both"} else None
    if table_text:
        table_structured = True
        processed = f"{processed}\n\n{table_text}".strip()
    elif table_detected and options.table_mode in {"markdown", "both"}:
        warnings.append("table detected but structured Markdown table was not reliable; visual crop is preferred")

    if table_detected and options.extract_images and options.crop_images and options.table_mode in {"image", "both"}:
        bbox = _table_bbox(page, text_blocks)
        if bbox:
            asset = _asset_for_crop(
                page,
                options.output_dir,
                page_number=page_number,
                index=asset_index,
                kind="table",
                bbox=bbox,
                description="table region crop for visual verification",
            )
            if asset:
                assets.append(asset)
                asset_index += 1
        else:
            warnings.append("table is suspected but no stable table bounding box was found")

    if formula_uncertain and options.extract_images and options.crop_images and options.formula_mode in {"latex", "image"}:
        for bbox in _dedupe_bboxes(_formula_bboxes(text_blocks)):
            formula_bbox = _union_bbox([bbox], page, padding=8.0) or bbox
            asset = _asset_for_crop(
                page,
                options.output_dir,
                page_number=page_number,
                index=asset_index,
                kind="formula_uncertain",
                bbox=formula_bbox,
                description="formula region retained because automatic LaTeX conversion was uncertain",
            )
            if asset:
                assets.append(asset)
                asset_index += 1
                # One representative crop per page is enough to flag the
                # uncertainty without flooding the output directory.
                break
        warnings.append("one or more formula-like regions were preserved as text and flagged for review")
    elif formula_uncertain:
        warnings.append("one or more formula-like regions were preserved as text; enable --crop-images for source crops")

    return PageResult(
        pdf_page=page_number,
        printed_page=None,
        raw_text=raw_text,
        text=processed,
        text_chars=len(processed),
        engine="pymupdf",
        assets=assets,
        formula_blocks=formula_blocks,
        formula_converted=formula_converted,
        formula_uncertain=formula_uncertain,
        table_detected=table_detected,
        table_structured=table_structured,
        warnings=warnings,
        quality_status="WARN" if warnings or len(processed) < 20 else "PASS",
    )


def _extract_poppler_page(
    page_number: int,
    options: Options,
    repeated_edges: set[str],
    scan_suspected: bool,
) -> PageResult:
    raw_text = _poppler_text(options.input_pdf, page_number)
    warnings: list[str] = ["PyMuPDF is unavailable; using Poppler text extraction"]
    if len(raw_text.strip()) < 20 and options.ocr:
        ocr_text = _ocr_page(options.input_pdf, page_number, options.ocr_lang)
        if ocr_text:
            raw_text = ocr_text
            warnings.append("text supplied by optional Tesseract OCR")
        else:
            warnings.append("OCR requested but pdftoppm/tesseract did not produce text")
    if len(raw_text.strip()) < 20 and scan_suspected:
        warnings.append("possible scan page has no usable text layer")
    cleaned = _clean_page_text(raw_text, repeated_edges)
    processed, formula_blocks, formula_converted, formula_uncertain = _process_formula_text(cleaned, options.formula_mode)
    table_detected = bool(TABLE_HINT_RE.search(processed))
    table_structured = False
    table_text = _structured_table(processed) if table_detected and options.table_mode in {"markdown", "both"} else None
    if table_text:
        table_structured = True
        processed = f"{processed}\n\n{table_text}".strip()
    elif table_detected and options.table_mode in {"markdown", "both"}:
        warnings.append("table-like text was found but no reliable structured table was produced")
    if options.extract_images or options.crop_images:
        warnings.append("image extraction/cropping skipped because PyMuPDF is not installed")
    if formula_uncertain:
        warnings.append("formula-like regions were preserved as text; install PyMuPDF to retain source crops")
    return PageResult(
        pdf_page=page_number,
        printed_page=None,
        raw_text=raw_text,
        text=processed,
        text_chars=len(processed),
        engine="poppler",
        formula_blocks=formula_blocks,
        formula_converted=formula_converted,
        formula_uncertain=formula_uncertain,
        table_detected=table_detected,
        table_structured=table_structured,
        warnings=warnings,
        quality_status="WARN",
    )


def _engine_command(engine: str) -> str | None:
    if engine == "marker":
        return _available_command("marker_single", "marker")
    if engine == "docling":
        return _available_command("docling")
    if engine == "mineru":
        return _available_command("mineru")
    return None


def _select_engine(requested: str, inspection: dict[str, Any], pages: list[int]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if requested != "auto":
        if requested == "pymupdf" and fitz is None:
            warnings.append("requested PyMuPDF is unavailable; using Poppler compatibility path")
            return "poppler", warnings
        if requested in {"marker", "docling", "mineru"} and _engine_command(requested) is None:
            raise RuntimeError(
                f"requested engine {requested!r} is not installed; see requirements-advanced.txt/requirements-mineru.txt"
            )
        if requested in {"marker", "docling", "mineru"}:
            all_pages = list(range(1, int(inspection.get("pages", 0)) + 1))
            if pages != all_pages:
                raise RuntimeError(
                    f"explicit engine {requested!r} currently supports whole-document output only; "
                    "use --engine auto for a safe partial-range conversion"
                )
        return requested, warnings

    recommended = str(inspection.get("recommended_engine", "pymupdf"))
    if recommended == "pymupdf" and fitz is not None:
        return "pymupdf", warnings
    if recommended in {"marker", "docling", "mineru"} and _engine_command(recommended):
        # External engines are currently whole-document adapters.  Do not
        # violate a sample/partial request by silently converting other pages.
        all_pages = list(range(1, int(inspection.get("pages", 0)) + 1))
        if pages == all_pages:
            warnings.append(f"{recommended} is available; it will be considered only if the fast-path quality gate fails")
        else:
            warnings.append(f"{recommended} is available but partial conversion is requested; keeping the page-local core path")
    if fitz is not None:
        if recommended != "pymupdf":
            warnings.append(f"recommended engine {recommended!r} unavailable for this run; falling back to PyMuPDF")
        return "pymupdf", warnings
    if _available_command("pdftotext"):
        warnings.append("PyMuPDF is unavailable; falling back to Poppler")
        return "poppler", warnings
    raise RuntimeError("no usable conversion engine found; install requirements-core.txt")


def _try_external_engine(engine: str, options: Options) -> tuple[str | None, list[str]]:
    """Run an optional whole-document adapter when its CLI is present.

    External outputs are accepted only if they contain page markers for every
    requested page.  Otherwise the canonical page-local result remains in
    place and the manifest records why escalation was not applied.
    """

    command = _engine_command(engine)
    if not command:
        return None, [f"{engine} is not installed"]
    all_pages = list(range(1, 1 + _pdf_page_count(options.input_pdf)))
    if options.pages != all_pages:
        return None, [f"{engine} adapter is not used for partial ranges because its CLI output is whole-document"]
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"pdf_md_{engine}_") as temp_dir:
        temp = Path(temp_dir)
        if engine == "marker":
            args = [command, str(options.input_pdf), "--output_dir", str(temp)]
        elif engine == "docling":
            args = [command, str(options.input_pdf), "--to", "md", "--output", str(temp)]
        else:
            args = [command, "-p", str(options.input_pdf), "-o", str(temp), "-b", "pipeline"]
        try:
            result = run_command(args, timeout=3600)
        except Exception as exc:
            return None, [f"{engine} invocation failed: {exc}"]
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
            return None, [f"{engine} invocation failed: {' '.join(detail) or 'unknown error'}"]
        markdown_files = sorted(temp.rglob("*.md"))
        if not markdown_files:
            return None, [f"{engine} completed but produced no Markdown file"]
        candidate = markdown_files[0].read_text(encoding="utf-8", errors="replace")
        expected = set(options.pages)
        markers = {int(value) for value in re.findall(r"<!--\s*PDF page\s+(\d+)\s*-->", candidate)}
        if not expected.issubset(markers):
            return None, [f"{engine} output lacks stable page markers; core output retained"]
        # Preserve the canonical output directory contract by copying only
        # assets referenced by the external Markdown and rewriting paths.
        for source in markdown_files[0].parent.rglob("*"):
            if source.is_file() and source.suffix.lower() in IMAGE_SUFFIXES:
                destination = options.output_dir / f"{engine}_{source.name}"
                shutil.copy2(source, destination)
                candidate = candidate.replace(source.name, destination.name)
        warnings.append(f"accepted whole-document Markdown from {engine}")
        return candidate, warnings


def _pdf_page_count(path: Path) -> int:
    if fitz is not None:
        document = fitz.open(str(path))
        try:
            return document.page_count
        finally:
            document.close()
    command = _available_command("pdfinfo")
    if command:
        result = run_command([command, str(path)])
        match = re.search(r"^Pages:\s*(\d+)", result.stdout, re.M)
        if match:
            return int(match.group(1))
    raise RuntimeError("cannot determine PDF page count")


def _marker(pdf_page: int, printed_page: int | None) -> str:
    if printed_page is None:
        return f"<!-- PDF page {pdf_page} | printed page unknown -->"
    return f"<!-- PDF page {pdf_page} | printed page {printed_page} -->"


def _page_markdown(page: PageResult, keep_markers: bool) -> str:
    chunks: list[str] = []
    if keep_markers:
        chunks.append(_marker(page.pdf_page, page.printed_page))
    if page.text:
        chunks.append(page.text)
    else:
        chunks.append(f"> [No usable text extracted from PDF page {page.pdf_page}; inspect the source or run OCR.]" )
    for asset in page.assets:
        chunks.append(asset.markdown)
    if page.formula_uncertain:
        chunks.append(
            f"> **Formula uncertainty (PDF page {page.pdf_page}):** formula-like text was preserved without silent guessing; review the source crop if present."
        )
    if page.warnings:
        # Keep warnings concise and machine-readable without hiding content.
        for warning in page.warnings:
            if "formula-like" not in warning:
                chunks.append(f"> **Conversion note (PDF page {page.pdf_page}):** {warning}")
    return "\n\n".join(chunk for chunk in chunks if chunk.strip())


def _quality_gate_pages(results: list[PageResult]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    for result in results:
        if result.text_chars == 0:
            errors.append(f"PDF page {result.pdf_page} has no extracted text")
            warnings.append(f"PDF page {result.pdf_page} has no extracted text")
        elif result.text_chars < 20:
            warnings.append(f"PDF page {result.pdf_page} has unusually low extracted text ({result.text_chars} chars)")
        if result.formula_uncertain:
            warnings.append(f"PDF page {result.pdf_page} has {result.formula_uncertain} uncertain formula region(s)")
        if result.table_detected and not (result.table_structured or any(asset.kind == "table" for asset in result.assets)):
            warnings.append(f"PDF page {result.pdf_page} has a table signal without visual or structured output")
    status = "FAIL" if errors else "WARN" if warnings else "PASS"
    return {"status": status, "errors": errors, "warnings": warnings}


def convert(options: Options) -> dict[str, Any]:
    options.output_dir.mkdir(parents=True, exist_ok=True)
    inspection = inspect_pdf(options.input_pdf, compact_page_range(options.pages))
    selected_engine, selection_warnings = _select_engine(options.requested_engine, inspection, options.pages)
    external_output: str | None = None
    external_warnings: list[str] = []
    if selected_engine in {"marker", "docling", "mineru"}:
        external_output, external_warnings = _try_external_engine(selected_engine, options)
        if not external_output:
            raise RuntimeError(
                f"explicit engine {selected_engine!r} did not produce a stable, page-addressable Markdown result: "
                + "; ".join(external_warnings)
            )
        selection_warnings.extend(external_warnings)
    all_page_texts: dict[int, str] = {}
    records_by_page = {int(record["pdf_page"]): record for record in inspection["page_records"]}

    core_engine = "pymupdf" if selected_engine in {"marker", "docling", "mineru"} and fitz is not None else selected_engine
    if core_engine == "pymupdf":
        document = fitz.open(str(options.input_pdf))  # type: ignore[union-attr]
        try:
            for page in options.pages:
                all_page_texts[page] = document.load_page(page - 1).get_text("text") or ""
            repeated_edges = find_repeated_edge_lines(all_page_texts.values())
            page_results = [
                _extract_fitz_page(document, page, records_by_page.get(page, {}), options, repeated_edges)
                for page in options.pages
            ]
        finally:
            document.close()
    else:
        for page in options.pages:
            all_page_texts[page] = _poppler_text(options.input_pdf, page)
        repeated_edges = find_repeated_edge_lines(all_page_texts.values())
        page_results = [
            _extract_poppler_page(
                page,
                options,
                repeated_edges,
                bool(records_by_page.get(page, {}).get("scan_suspected")),
            )
            for page in options.pages
        ]

    printed_pages = _detect_printed_pages(all_page_texts, options)
    for result in page_results:
        result.printed_page = printed_pages.get(result.pdf_page)
        if result.printed_page is None:
            result.warnings.append("printed page number is unknown; no guess was made")
            result.quality_status = "WARN"

    quality = _quality_gate_pages(page_results) if options.quality_gate else {"status": "SKIPPED", "errors": [], "warnings": []}
    if options.requested_engine == "auto" and quality["status"] == "FAIL":
        for candidate in ("marker", "docling", "mineru"):
            if _engine_command(candidate):
                external_output, external_warnings = _try_external_engine(candidate, options)
                if external_output:
                    selected_engine = candidate
                    break

    title = options.input_pdf.stem
    body: list[str] = [f"# {title}", "", "> Generated by PDF-to-MD with Necessary Image Cropping Retained.", f"> PDF page range: {compact_page_range(options.pages)}"]
    if printed_pages:
        body.append("> Printed page numbers are included in page markers only when detected or explicitly mapped; PDF page index and printed page are distinct.")
    else:
        body.append("> Printed page mapping: unknown for this run; use --printed-page-offset or --printed-page-map rather than guessing.")
    body.extend(["", "---", ""])
    if external_output:
        body.append(external_output.strip())
    else:
        body.extend(_page_markdown(result, options.keep_page_markers) for result in page_results)
    markdown = "\n\n---\n\n".join(chunk for chunk in body if chunk is not None and chunk.strip()) + "\n"
    markdown_path = options.output_dir / f"{safe_stem(options.input_pdf)}.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    engines_used: dict[str, list[int]] = {}
    if external_output:
        engines_used[selected_engine] = list(options.pages)
    else:
        for result in page_results:
            engines_used.setdefault(result.engine, []).append(result.pdf_page)
        if selected_engine not in engines_used:
            engines_used.setdefault(selected_engine, list(options.pages))

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "source": str(options.input_pdf.resolve()),
        "output_markdown": str(markdown_path.resolve()),
        "page_range": compact_page_range(options.pages),
        "pdf_pages": options.pages,
        "inspection": {
            "text_layer": inspection.get("text_layer"),
            "scan_ratio": inspection.get("scan_ratio"),
            "layout_complexity": inspection.get("layout_complexity"),
            "formula_density": inspection.get("formula_density"),
            "scan_pages": inspection.get("scan_pages", []),
            "multi_column_pages": inspection.get("multi_column_pages", []),
            "formula_pages": inspection.get("formula_pages", []),
            "table_pages": inspection.get("table_pages", []),
            "garbage_pages": inspection.get("garbage_pages", []),
            "recommended_engine": inspection.get("recommended_engine"),
        },
        "printed_page_mapping": {
            str(page): printed_pages.get(page) for page in options.pages
        },
        "engine": options.requested_engine,
        "selected_engine": selected_engine,
        "engines_used": engines_used,
        "images": len([asset for result in page_results for asset in result.assets]),
        "image_assets": [
            {
                "filename": asset.filename,
                "alt": asset.stem,
                "kind": asset.kind,
                "pdf_page": asset.pdf_page,
                "bbox": asset.bbox,
                "description": asset.description,
            }
            for result in page_results
            for asset in result.assets
        ],
        "tables": sum(result.table_detected for result in page_results),
        "structured_tables": sum(result.table_structured for result in page_results),
        "formula_blocks": sum(result.formula_blocks for result in page_results),
        "formula_converted": sum(result.formula_converted for result in page_results),
        "formula_uncertain": sum(result.formula_uncertain for result in page_results),
        "warnings": [
            *inspection.get("warnings", []),
            *selection_warnings,
            *external_warnings,
            *quality["warnings"],
            *[warning for result in page_results for warning in result.warnings],
        ],
        "quality_gate": quality,
        "validation": "PENDING",
        "options": {
            "keep_page_markers": options.keep_page_markers,
            "extract_images": options.extract_images,
            "crop_images": options.crop_images,
            "ocr": options.ocr,
            "table_mode": options.table_mode,
            "formula_mode": options.formula_mode,
        },
        "pages_detail": [
            {
                "pdf_page": result.pdf_page,
                "printed_page": result.printed_page,
                "engine": selected_engine if external_output else result.engine,
                "text_chars": result.text_chars,
                "formula_blocks": result.formula_blocks,
                "formula_uncertain": result.formula_uncertain,
                "table_detected": result.table_detected,
                "table_structured": result.table_structured,
                "images": len(result.assets),
                "warnings": result.warnings,
                "quality_status": result.quality_status,
            }
            for result in page_results
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = options.output_dir / "conversion_manifest.json"
    json_dump(manifest, manifest_path)

    # Import the validator after writing the Markdown so the script also works
    # when called from a package or from a different current working directory.
    try:
        from validate_md_assets import validate_markdown
    except ImportError:  # pragma: no cover
        from .validate_md_assets import validate_markdown
    validation = validate_markdown(
        markdown_path,
        expected_pages=options.pages if options.keep_page_markers else None,
        manifest_path=manifest_path,
    )
    validation_path = options.output_dir / "validation_report.json"
    json_dump(validation, validation_path)
    manifest["validation"] = validation["status"]
    manifest["validation_report"] = str(validation_path.resolve())
    json_dump(manifest, manifest_path)

    return {
        "markdown": markdown_path,
        "manifest": manifest_path,
        "validation_report": validation_path,
        "manifest_data": manifest,
        "validation": validation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="input PDF")
    parser.add_argument("-o", "--output", type=Path, required=True, help="output directory")
    parser.add_argument("--pages", help="one-based range, e.g. 1-20 or 1-3,7")
    parser.add_argument("--engine", choices=("auto", "pymupdf", "marker", "docling", "mineru"), default="auto")
    parser.add_argument("--ocr", action="store_true", help="use Tesseract when a page has no usable text layer")
    parser.add_argument("--ocr-lang", default="eng", help="Tesseract language code, default: eng")
    parser.add_argument("--keep-page-markers", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--extract-images", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--crop-images", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--table-mode", choices=("both", "image", "markdown", "none"), default="both")
    parser.add_argument("--formula-mode", choices=("latex", "text", "image"), default="latex")
    parser.add_argument("--printed-page-offset", type=int, help="explicit printed_page = pdf_page + offset")
    parser.add_argument("--printed-page-map", type=Path, help="JSON object mapping PDF page to printed page")
    parser.add_argument("--quality-gate", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    pdf = args.pdf.resolve()
    if not pdf.is_file():
        print(f"ERROR: PDF not found: {pdf}", file=sys.stderr)
        return 2
    try:
        total = _pdf_page_count(pdf)
        pages = parse_pages(args.pages, total)
        result = convert(
            Options(
                input_pdf=pdf,
                output_dir=args.output.resolve(),
                pages=pages,
                requested_engine=args.engine,
                keep_page_markers=args.keep_page_markers,
                extract_images=args.extract_images,
                crop_images=args.crop_images,
                ocr=args.ocr,
                ocr_lang=args.ocr_lang,
                table_mode=args.table_mode,
                formula_mode=args.formula_mode,
                printed_page_offset=args.printed_page_offset,
                printed_page_map=args.printed_page_map.resolve() if args.printed_page_map else None,
                quality_gate=args.quality_gate,
            )
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    manifest = result["manifest_data"]
    print(f"markdown: {result['markdown'].resolve()}")
    print(f"manifest: {result['manifest'].resolve()}")
    print(f"validation: {manifest.get('validation', 'PENDING')}")
    print(f"engine: {manifest.get('selected_engine')} (requested {manifest.get('engine')})")
    print(f"pages: {manifest.get('page_range')}; images: {manifest.get('images')}; formulas: {manifest.get('formula_blocks')}")
    for warning in manifest.get("warnings", []):
        print(f"WARN: {warning}")
    return 0 if manifest.get("validation") != "FAIL" and manifest.get("quality_gate", {}).get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
