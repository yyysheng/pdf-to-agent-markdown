#!/usr/bin/env python3
"""Vision-first, conservative transcription runner for the local textbook PDFs.

This runner is deliberately small and deterministic.  PyMuPDF is used only to
read ordinary prose, inspect page geometry, and render bounded visual evidence.
It never promotes a text-layer formula to LaTeX without visual confirmation.
Unconfirmed mathematical expressions remain in the source text and receive a
bounded formula crop plus an explicit checkpoint entry for visual review.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pymupdf


PAGE_MARKER_RE = re.compile(r"<!--\s*PDF page\s+(\d+)\s*-->")
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇]+章(?:\s+|　+).{1,100}$")
NUMBERED_HEADING_RE = re.compile(r"^(?:\d{1,2}(?:[.．]\d{1,2})?|\d{1,2})[.．、]\s*.{1,100}$")
LABEL_HEADINGS = {
    "目录",
    "致同学们",
    "序言",
    "问题",
    "实验",
    "演示",
    "思考与讨论",
    "做一做",
    "科学方法",
    "拓展学习",
    "科学漫步",
    "练习与应用",
    "复习与提高",
    "课题研究",
    "学生实验",
    "索引",
}
VISUAL_CUE_RE = re.compile(
    r"图\s*[0-9一二三四五六七八九十.-]+|表\s*[0-9一二三四五六七八九十.-]+|"
    r"示意图|装置|如图|曲线|坐标轴|实验|电路|光路|受力图"
)
MATH_CUE_RE = re.compile(
    r"(?:＝|=|∝|≤|≥|√|∑|∫|→|⊥|∥|×|·|\^|_|[αβγδεζηθλμπρωΔΩ])"
)
MATH_STRUCTURE_RE = re.compile(r"(?:=|＝|√|∑|∫|∝|≤|≥|→|⊥|∥|\d\s*[×x]\s*10)")
PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
EXPECTED_LETTER_SCRIPTS = (
    "LATIN",
    "GREEK",
    "CJK UNIFIED",
    "CJK COMPATIBILITY",
    "HIRAGANA",
    "KATAKANA",
    "MICRO SIGN",
    "IDEOGRAPHIC ITERATION MARK",
)
SKILL_NAME = "pdf-to-agent-markdown"
FORMULA_DISPOSITIONS = {
    "latex_confirmed",
    "crop_only",
    "not_formula",
    "removed",
    "markdown_sufficient",
}


def compact(value: str) -> str:
    return re.sub(r"[ \t\u00a0\u2002\u2003]+", " ", value).strip()


def _unexpected_letter(character: str) -> bool:
    if not character.isalpha():
        return False
    name = unicodedata.name(character, "")
    return bool(name) and not any(script in name for script in EXPECTED_LETTER_SCRIPTS)


def sanitize_line(line: str) -> tuple[str, bool]:
    """Remove only high-confidence extraction garbage from emitted text.

    The original glyphs remain available in the source PDF and in any bounded
    formula/visual crop.  A square placeholder is intentionally used instead
    of inventing a replacement character or a guessed symbol.
    """

    changed = False
    chars = list(line)
    if "�" in line or PRIVATE_USE_RE.search(line):
        changed = True
    unexpected = [i for i, character in enumerate(chars) if _unexpected_letter(character)]
    if unexpected:
        changed = True
    if not changed:
        return line, False
    cleaned = []
    for character in chars:
        if character == "�" or PRIVATE_USE_RE.match(character) or _unexpected_letter(character):
            cleaned.append("□")
        else:
            cleaned.append(character)
    return "".join(cleaned), True


def line_is_heading(line: str) -> bool:
    if not line or len(line) > 110:
        return False
    if CHAPTER_RE.match(line):
        return True
    if NUMBERED_HEADING_RE.match(line) and not line.endswith(("。", "！", "？", ".", ";", "；")):
        return True
    if line in LABEL_HEADINGS:
        return True
    return len(line) <= 42 and line.endswith("—")


def likely_formula(line: str) -> bool:
    if len(line) > 140 or not MATH_CUE_RE.search(line):
        return False
    if not MATH_STRUCTURE_RE.search(line):
        return False
    chinese = len(re.findall(r"[\u4e00-\u9fff]", line))
    return chinese <= 2


def join_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    result = lines[0]
    for line in lines[1:]:
        if not result:
            result = line
        elif result[-1].isascii() and line[0].isascii():
            result += " " + line
        else:
            result += line
    return result.strip()


def common_noise(document: pymupdf.Document) -> set[str]:
    counts: Counter[str] = Counter()
    for page in document:
        lines = [compact(line) for line in page.get_text("text", sort=True).splitlines() if compact(line)]
        for line in lines[:5] + lines[-5:]:
            if len(line) <= 60:
                counts[line] += 1
    threshold = max(4, round(document.page_count * 0.035))
    return {line for line, count in counts.items() if count >= threshold}


def clean_page_lines(page: pymupdf.Page, noise: set[str]) -> tuple[list[str], bool]:
    raw_lines = [compact(line) for line in page.get_text("text", sort=True).splitlines()]
    raw_lines = [line for line in raw_lines if line]
    lines: list[str] = []
    changed = False
    last = len(raw_lines) - 1
    for index, raw_line in enumerate(raw_lines):
        if re.fullmatch(r"[_—\-]{5,}", raw_line):
            continue
        if raw_line in noise and (index < 5 or index > last - 5):
            continue
        if re.fullmatch(r"\d{1,3}", raw_line) and (index < 3 or index > last - 3):
            continue
        if raw_line in {
            "普通高中教科书",
            "高中物理必修第一册",
            "高中物理必修第二册",
            "高中物理必修第三册",
        }:
            continue
        line, line_changed = sanitize_line(raw_line)
        changed = changed or line_changed
        lines.append(line)
    return lines, changed


def _rect_from_spans(spans: list[dict[str, Any]]) -> pymupdf.Rect | None:
    boxes = [pymupdf.Rect(span["bbox"]) for span in spans if span.get("bbox")]
    if not boxes:
        return None
    rect = boxes[0]
    for box in boxes[1:]:
        rect |= box
    return rect


def formula_bboxes(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    """Find bounded candidate formula lines for visual retention only."""

    candidates: list[tuple[pymupdf.Rect, str]] = []
    data = page.get_text("dict", sort=True)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line_data in block.get("lines", []):
            spans = line_data.get("spans", [])
            text = compact("".join(str(span.get("text", "")) for span in spans))
            if not text or not likely_formula(text):
                continue
            rect = _rect_from_spans(spans)
            if rect is None:
                continue
            rect = (rect + (-8, -8, 8, 8)).intersect(page.rect)
            if rect.width < 18 or rect.height < 10:
                continue
            if rect.width * rect.height > page.rect.width * page.rect.height * 0.22:
                continue
            # The opening pages of this textbook set carry decorative formulas
            # in the background.  They are not source expressions and should
            # not become retained formula evidence.
            if page.number < 15 and rect.y0 > page.rect.height * 0.75:
                continue
            candidates.append((rect, text))

    deduped: list[tuple[pymupdf.Rect, str]] = []
    for rect, text in candidates:
        if any(rect.intersects(old_rect) and rect.get_area() <= old_rect.get_area() * 1.25 for old_rect, _ in deduped):
            continue
        deduped.append((rect, text))
    return deduped[:6]


def _rect_union(rects: list[pymupdf.Rect]) -> pymupdf.Rect:
    if not rects:
        raise ValueError("cannot union an empty set of rectangles")
    result = pymupdf.Rect(rects[0])
    for rect in rects[1:]:
        result |= rect
    return result


def _median(values: list[float], default: float) -> float:
    if not values:
        return default
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _overlap_ratio(first_start: float, first_end: float, second_start: float, second_end: float) -> float:
    overlap = max(0.0, min(first_end, second_end) - max(first_start, second_start))
    denominator = min(first_end - first_start, second_end - second_start)
    return overlap / denominator if denominator > 0 else 0.0


def _text_line_records(page: pymupdf.Page) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    data = page.get_text("dict", sort=True)
    for block_index, block in enumerate(data.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line_index, line_data in enumerate(block.get("lines", [])):
            spans = line_data.get("spans", [])
            rect = _rect_from_spans(spans)
            text = compact("".join(str(span.get("text", "")) for span in spans))
            if rect is None or not text:
                continue
            records.append(
                {
                    "block_index": block_index,
                    "line_index": line_index,
                    "rect": rect,
                    "text": text,
                }
            )
    return records


def _formula_rects_related(first: pymupdf.Rect, second: pymupdf.Rect) -> bool:
    """Return whether two candidate boxes plausibly share one math region."""

    minimum_height = min(first.height, second.height)
    maximum_height = max(first.height, second.height)
    vertical_overlap = _overlap_ratio(first.y0, first.y1, second.y0, second.y1)
    horizontal_overlap = _overlap_ratio(first.x0, first.x1, second.x0, second.x1)
    horizontal_gap = max(first.x0, second.x0) - min(first.x1, second.x1)
    vertical_gap = max(first.y0, second.y0) - min(first.y1, second.y1)
    center_delta = abs(first.y0 + first.height / 2 - second.y0 - second.height / 2)

    same_line = (
        vertical_overlap >= 0.35
        and center_delta <= max(6.0, maximum_height * 0.70)
        # PDF text extraction often emits distant fragments from one visual
        # baseline as separate blocks.  The relation is still limited to
        # overlapping baselines and candidate boxes; ordinary prose is never
        # joined by this rule.
        and horizontal_gap <= max(120.0, minimum_height * 6.0)
    )
    stacked_fraction_or_derivation = (
        horizontal_overlap >= 0.30
        and vertical_gap <= max(18.0, maximum_height * 0.90)
    )
    return same_line or stacked_fraction_or_derivation


def formula_candidate_groups(
    page: pymupdf.Page,
    candidates: list[tuple[pymupdf.Rect, str]],
) -> list[dict[str, Any]]:
    """Group only visual regions; never concatenate candidate text.

    The grouping signal is intentionally geometric: same-line proximity or
    vertically stacked boxes with meaningful x-overlap.  The returned group
    carries candidate indices and a context rectangle, leaving all math
    interpretation to the visually inspecting Agent.
    """

    if not candidates:
        return []

    parent = list(range(len(candidates)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    for first_index, (first_rect, _first_text) in enumerate(candidates):
        for second_index in range(first_index + 1, len(candidates)):
            second_rect = candidates[second_index][0]
            if _formula_rects_related(first_rect, second_rect):
                union(first_index, second_index)

    grouped: dict[int, list[int]] = defaultdict(list)
    for index in range(len(candidates)):
        grouped[find(index)].append(index)

    lines = _text_line_records(page)
    line_height = _median([line["rect"].height for line in lines], default=16.0)
    result: list[dict[str, Any]] = []
    for indices in sorted(grouped.values(), key=lambda values: values[0]):
        member_rects = [candidates[index][0] for index in indices]
        member_rect = _rect_union(member_rects)
        member_height = max(rect.height for rect in member_rects)
        line_like = member_rect.height <= max(line_height * 2.0, member_height * 1.35)
        anchor_lines = [
            line
            for line in lines
            if line["rect"].intersects(member_rect)
            or abs(line["rect"].y0 + line["rect"].height / 2 - (member_rect.y0 + member_rect.height / 2))
            <= max(line_height * 0.85, 8.0)
        ]

        if line_like and anchor_lines:
            closest = min(
                anchor_lines,
                key=lambda line: abs(
                    line["rect"].y0 + line["rect"].height / 2 - (member_rect.y0 + member_rect.height / 2)
                ),
            )
            context_lines = [
                line
                for line in anchor_lines
                if abs(line["rect"].y0 + line["rect"].height / 2 - (closest["rect"].y0 + closest["rect"].height / 2))
                <= max(line_height * 0.85, 8.0)
            ]
            context_type = "line"
        else:
            context_lines = [
                line
                for line in lines
                if line["rect"].y1 >= member_rect.y0 - line_height * 2.2
                and line["rect"].y0 <= member_rect.y1 + line_height * 2.2
                and (
                    _overlap_ratio(line["rect"].x0, line["rect"].x1, member_rect.x0, member_rect.x1) >= 0.15
                    or line["block_index"] in {item["block_index"] for item in anchor_lines}
                )
            ][:7]
            context_type = "region"

        context_base = _rect_union([line["rect"] for line in context_lines] + [member_rect])
        horizontal_margin = max(line_height * 0.75, member_height * 0.35)
        vertical_margin = max(line_height * 0.65, member_height * 0.30)
        context_rect = (context_base + (-horizontal_margin, -vertical_margin, horizontal_margin, vertical_margin)).intersect(
            page.rect
        )
        one_based_indices = [index + 1 for index in indices]
        first_index = one_based_indices[0]
        last_index = one_based_indices[-1]
        group_id = f"pdf-page-{page.number + 1:03d}-group-{first_index:02d}"
        result.append(
            {
                "group_id": group_id,
                "candidate_indices": one_based_indices,
                "candidate_bbox": member_rect,
                "context_bbox": context_rect,
                "context_type": context_type,
                "context_filename": (
                    f"pdf_page_{page.number + 1:03d}_formula_{first_index:02d}_context.png"
                    if first_index == last_index
                    else f"pdf_page_{page.number + 1:03d}_formula_group_{first_index:02d}_{last_index:02d}_context.png"
                ),
            }
        )
    return result


def image_bboxes(page: pymupdf.Page, repeated_xrefs: set[int]) -> list[pymupdf.Rect]:
    # Cover and contents thumbnails are layout/decorative material for this
    # textbook set, not necessary visual evidence for the transcribed content.
    if page.number < 5:
        return []
    page_area = page.rect.width * page.rect.height
    candidates: list[tuple[float, pymupdf.Rect]] = []
    seen: set[tuple[float, float, float, float]] = set()
    for info in page.get_image_info(xrefs=True):
        xref = int(info.get("xref") or 0)
        if not xref or xref in repeated_xrefs:
            continue
        rect = pymupdf.Rect(info["bbox"]).intersect(page.rect)
        area = rect.width * rect.height
        if rect.width < 28 or rect.height < 28 or area < page_area * 0.008 or area > page_area * 0.65:
            continue
        if rect.width > page.rect.width * 0.9 or rect.height > page.rect.height * 0.9:
            continue
        pixel_scale = max(info.get("width", 0) / max(rect.width, 1), info.get("height", 0) / max(rect.height, 1))
        if pixel_scale > 4.5:
            continue
        key = tuple(round(value, 1) for value in rect)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((area, rect))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return [rect for _area, rect in candidates[:6]]


def render_crop(page: pymupdf.Page, rect: pymupdf.Rect, destination: Path, dpi: int) -> None:
    expanded = (rect + (-6, -6, 6, 6)).intersect(page.rect)
    if expanded.is_empty or expanded.width <= 0 or expanded.height <= 0:
        raise ValueError("visual bbox does not intersect page")
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), clip=expanded, alpha=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(destination))


def _rect_values(rect: pymupdf.Rect) -> list[float]:
    return [round(float(value), 2) for value in rect]


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def page_markdown(
    page_number: int,
    lines: list[str],
    *,
    visual_assets: list[str],
    formula_lines: set[str],
    page_notes: list[str],
) -> str:
    output: list[str] = [f"<!-- PDF page {page_number} -->", ""]
    paragraphs: list[str] = []

    def flush() -> None:
        if paragraphs:
            output.append(join_lines(paragraphs))
            output.append("")
            paragraphs.clear()

    for line in lines:
        if line in formula_lines:
            flush()
            # Keep the text-layer expression as source text.  It is deliberately
            # not put in $$...$$ until an Agent has visually confirmed it.
            output.extend([line, ""])
            continue
        if line_is_heading(line):
            flush()
            if CHAPTER_RE.match(line):
                level = "##"
            else:
                level = "###"
            output.extend([f"{level} {line}", ""])
            continue
        if re.match(r"^(?:\d+[.．、]|[（(][一二三四五六七八九十]+[）)])", line):
            flush()
            output.extend([f"- {line}", ""])
            continue
        paragraphs.append(line)
        if line.endswith(("。", "！", "？", "；", ".", "!", "?", ";")):
            flush()
    flush()

    for note in page_notes:
        output.extend([f"> [Transcription note: {note}]", ""])
    for asset in visual_assets:
        if "_formula_" in asset:
            alt = f"PDF page {page_number} formula visual evidence"
        else:
            alt = f"PDF page {page_number} necessary visual evidence"
        output.extend([f"![{alt}]({asset})", ""])
    return "\n".join(output).rstrip() + "\n\n"


def reset_output(
    md_path: Path,
    state_path: Path,
    source: Path,
    total_pages: int,
    *,
    skill_revision: str,
) -> dict[str, Any]:
    output_dir = md_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = output_dir / "assets"
    if asset_dir.is_dir():
        for pattern in ("pdf_page_*_visual_*.png", "pdf_page_*_formula_*.png"):
            for old_asset in asset_dir.glob(pattern):
                try:
                    old_asset.unlink(missing_ok=True)
                except PermissionError:
                    # A transient Windows reader lock must not abort the book;
                    # a later render either replaces the asset or records a
                    # concrete crop failure in Phase 2.
                    continue
    md_path.write_text(
        f"# {source.stem}\n\n"
        "> [Transcription note: 原始 PDF 是唯一来源；PDF 页面视觉是公式、表格、图示和阅读顺序的主要证据。文本层仅作为普通正文复制辅助；未确认的数学结构保留原文并附局部视觉证据。]\n\n"
        "## 目录与正文\n\n",
        encoding="utf-8",
    )
    state = {
        "schema_version": 3,
        "skill": {"name": SKILL_NAME, "revision": skill_revision},
        "source": str(source.resolve()),
        "pdf_pages": total_pages,
        "requested_pdf_pages": list(range(1, total_pages + 1)),
        "processed_pdf_pages": [],
        "last_completed_pdf_page": 0,
        "completed_pdf_pages": 0,
        "last_transcribed_pdf_page": 0,
        "current_section": None,
        "output": str(md_path.resolve()),
        "pending_review": [],
        "visual_review_required": [],
        "visual_review_reasons": {},
        "visual_verified_pdf_pages": [],
        "visual_review_decisions": {},
        "formula_candidates": {},
        "formula_candidate_records": {},
        "formula_review_groups": {},
        "formula_not_formula": 0,
        "formula_removed": 0,
        "visual_assets": [],
        "image_references": 0,
        "execution_trace": [],
        "status": "in_progress",
    }
    write_state(state_path, state)
    return state


def load_visual_decisions(
    path: Path | None,
    *,
    book_identifier: str | None = None,
) -> dict[str, Any]:
    """Load decisions produced after the Agent inspected rendered PDF visuals."""

    result: dict[str, Any] = {
        "formulas": defaultdict(list),
        "visual_pages": set(),
        "text_pages": set(),
    }
    if path is None:
        return result
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        formula_entries = data
        visual_pages: list[Any] = []
    elif isinstance(data, dict):
        formula_entries = data.get("formula_decisions", data.get("decisions", []))
        visual_pages = data.get("visual_confirmed_pages", [])
        text_pages = data.get("text_visual_confirmed_pages", [])
        if not formula_entries and isinstance(data.get("pages"), dict):
            formula_entries = []
            for raw_page, page_data in data["pages"].items():
                if not isinstance(page_data, dict):
                    continue
                for entry in page_data.get("formulas", []):
                    if isinstance(entry, dict):
                        formula_entries.append({"pdf_page": raw_page, **entry})
                visual_pages.append(raw_page)
    else:
        raise ValueError(f"visual decision file must contain an object or list: {path}")
    if not isinstance(formula_entries, list):
        raise ValueError(f"formula_decisions must be a list: {path}")
    for raw_page in visual_pages:
        try:
            result["visual_pages"].add(int(raw_page))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"visual_confirmed_pages contains an invalid page: {raw_page!r}") from exc
    for raw_page in text_pages:
        try:
            result["text_pages"].add(int(raw_page))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"text_visual_confirmed_pages contains an invalid page: {raw_page!r}") from exc
    for entry in formula_entries:
        if not isinstance(entry, dict):
            raise ValueError(f"formula decision must be an object: {entry!r}")
        item_book = entry.get("book", entry.get("source_book"))
        if book_identifier is not None and item_book is not None:
            if not isinstance(item_book, str) or _book_key(item_book) != _book_key(book_identifier):
                continue
        raw_page = entry.get("pdf_page")
        try:
            page = int(raw_page)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"formula decision has invalid pdf_page: {raw_page!r}") from exc
        candidate = entry.get("candidate", entry.get("source_text", entry.get("source")))
        candidate_index = entry.get("candidate_index")
        candidate_id = entry.get("candidate_id")
        if not isinstance(candidate, str) or not candidate.strip():
            if candidate_index is None and not (isinstance(candidate_id, str) and candidate_id.strip()):
                raise ValueError(f"formula decision on page {page} lacks candidate/source_text or candidate id")
            candidate = ""
        normalized = dict(entry)
        normalized["pdf_page"] = page
        normalized["candidate"] = candidate.strip()
        result["formulas"][page].append(normalized)
        result["visual_pages"].add(page)
    return result


def _book_key(value: str) -> str:
    name = Path(value).name
    if name.casefold().endswith(".pdf"):
        name = name[:-4]
    return name.casefold()


def load_formula_selection(
    path: Path | None,
    *,
    book_identifier: str | None = None,
) -> dict[int, list[dict[str, Any] | str]] | None:
    """Optionally limit Phase 1 to an explicit audit selection of candidates."""

    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("books"), dict):
        entries = []
        for book, book_data in data["books"].items():
            if not isinstance(book_data, dict):
                continue
            for item in book_data.get("items", book_data.get("candidates", [])):
                if isinstance(item, dict):
                    entries.append({"book": book, **item})
    else:
        entries = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(f"formula selection must contain an items list: {path}")
    selected: dict[int, list[dict[str, Any] | str]] = defaultdict(list)
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"formula selection item must be an object: {entry!r}")
        item_book = entry.get("book", entry.get("source_book"))
        if book_identifier is not None and item_book is not None:
            if not isinstance(item_book, str) or _book_key(item_book) != _book_key(book_identifier):
                continue
        try:
            page = int(entry["pdf_page"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"formula selection item has invalid pdf_page: {entry!r}") from exc
        if page <= 0:
            raise ValueError(f"formula selection item has non-positive pdf_page: {entry!r}")
        candidate = entry.get("candidate", entry.get("source_text", entry.get("source")))
        candidate_index = entry.get("candidate_index")
        candidate_id = entry.get("candidate_id")
        if candidate_index is not None:
            if isinstance(candidate_index, bool) or not isinstance(candidate_index, int) or candidate_index <= 0:
                raise ValueError(f"formula selection item has invalid candidate_index: {entry!r}")
        if candidate_id is not None and (not isinstance(candidate_id, str) or not candidate_id.strip()):
            raise ValueError(f"formula selection item has invalid candidate_id: {entry!r}")
        if not isinstance(candidate, str) or not candidate.strip():
            if candidate_index is None and not (isinstance(candidate_id, str) and candidate_id.strip()):
                raise ValueError(f"formula selection item lacks candidate/source_text or candidate id: {entry!r}")
            normalized = dict(entry)
            normalized["pdf_page"] = page
            selected[page].append(normalized)
        else:
            selected[page].append(candidate.strip())
    return dict(selected)


def selected_formula_candidate_indices(
    candidates: list[tuple[pymupdf.Rect, str]],
    selection: dict[int, list[dict[str, Any] | str]] | None,
    page_number: int,
) -> list[int]:
    """Resolve an audit selection to stable, one-based PDF candidate indices."""

    if selection is None:
        return list(range(len(candidates)))
    selected: list[int] = []
    used: set[int] = set()
    for entry in selection.get(page_number, []):
        candidate_index: int | None = None
        candidate: str | None = None
        candidate_id: str | None = None
        if isinstance(entry, dict):
            raw_index = entry.get("candidate_index")
            if raw_index is not None:
                if isinstance(raw_index, bool) or not isinstance(raw_index, int):
                    raise ValueError(f"formula selection has invalid candidate_index on PDF page {page_number}: {entry!r}")
                candidate_index = raw_index
            raw_id = entry.get("candidate_id")
            if raw_id is not None:
                candidate_id = raw_id if isinstance(raw_id, str) else None
                if candidate_id is None:
                    raise ValueError(f"formula selection has invalid candidate_id on PDF page {page_number}: {entry!r}")
                match = re.search(r"(?:formula|candidate)[-_]0*(\d+)$", candidate_id, flags=re.IGNORECASE)
                if match:
                    parsed_index = int(match.group(1))
                    if candidate_index is not None and candidate_index != parsed_index:
                        raise ValueError(f"candidate_index disagrees with candidate_id on PDF page {page_number}: {entry!r}")
                    candidate_index = parsed_index
            raw_candidate = entry.get("candidate", entry.get("source_text", entry.get("source")))
            if raw_candidate is not None:
                if not isinstance(raw_candidate, str) or not raw_candidate.strip():
                    raise ValueError(f"formula selection has invalid candidate text on PDF page {page_number}: {entry!r}")
                candidate = raw_candidate.strip()
        elif isinstance(entry, str) and entry.strip():
            candidate = entry.strip()
        else:
            raise ValueError(f"formula selection entry is invalid on PDF page {page_number}: {entry!r}")

        if candidate_index is not None:
            if candidate_index <= 0 or candidate_index > len(candidates):
                raise ValueError(f"formula selection candidate_index {candidate_index} not found on PDF page {page_number}")
            if candidate is not None and candidates[candidate_index - 1][1] != candidate:
                raise ValueError(
                    f"formula selection candidate_index {candidate_index} does not match candidate text on PDF page {page_number}"
                )
            resolved = candidate_index - 1
        else:
            if candidate is None:
                raise ValueError(f"formula selection item cannot resolve a candidate on PDF page {page_number}: {entry!r}")
            resolved = next(
                (index for index, (_rect, text) in enumerate(candidates) if index not in used and text == candidate),
                -1,
            )
            if resolved < 0:
                raise ValueError(f"formula selection candidate {candidate!r} not found on PDF page {page_number}")
        if resolved in used:
            continue
        used.add(resolved)
        selected.append(resolved)
    return sorted(selected)


def filter_formula_candidates(
    candidates: list[tuple[pymupdf.Rect, str]],
    selection: dict[int, list[dict[str, Any] | str]] | None,
    page_number: int,
) -> list[tuple[pymupdf.Rect, str]]:
    """Keep each explicitly selected candidate once, preserving PDF order."""

    return [candidates[index] for index in selected_formula_candidate_indices(candidates, selection, page_number)]


def _validate_relative_asset(
    value: Any,
    *,
    field: str,
    output_dir: Path,
    errors: list[str],
    required: bool,
) -> bool:
    if value is None:
        if required:
            errors.append(f"{field} is required")
        return False
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field} must be a non-empty relative path")
        return False
    asset_path = Path(value)
    if asset_path.is_absolute():
        errors.append(f"{field} must stay relative to the Markdown directory")
        return False
    root = output_dir.resolve()
    resolved = (root / asset_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        errors.append(f"{field} escapes the Markdown directory")
        return False
    if not resolved.is_file():
        errors.append(f"{field} does not exist: {value}")
        return False
    return True


def validate_visual_formula_decision(
    decision: dict[str, Any],
    *,
    page_number: int,
    candidate: str,
    output_dir: Path,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate the Agent's visual decision before it can affect Markdown."""

    errors: list[str] = []
    disposition = decision.get("disposition")
    if disposition == "conservative_latex":
        errors.append("legacy conservative_latex is not an executable decision")
    elif disposition not in FORMULA_DISPOSITIONS:
        errors.append(f"unknown disposition: {disposition!r}")
    if decision.get("verification") != "visual":
        errors.append("verification must be visual")

    source_page = decision.get("source_pdf_page")
    valid_asset = _validate_relative_asset(
        decision.get("source_asset"),
        field="source_asset",
        output_dir=output_dir,
        errors=errors,
        required=False,
    )
    valid_page = False
    if source_page is not None:
        if isinstance(source_page, bool) or not isinstance(source_page, int) or source_page <= 0:
            errors.append("source_pdf_page must be a positive integer")
        else:
            valid_page = source_page == page_number
            if not valid_page:
                errors.append(f"source_pdf_page must match candidate page {page_number}")
    if not valid_asset and not valid_page:
        errors.append("source_asset or matching source_pdf_page is required")
    visual_formula_dispositions = {"latex_confirmed", "crop_only", "not_formula", "removed"}
    if disposition in visual_formula_dispositions:
        _validate_relative_asset(
            decision.get("context_asset"),
            field="context_asset",
            output_dir=output_dir,
            errors=errors,
            required=True,
        )
        context_type = decision.get("context_type")
        if context_type not in {"line", "region"}:
            errors.append("context_type must be line or region")
        source_candidates = decision.get("source_candidates")
        if not isinstance(source_candidates, list) or not source_candidates:
            errors.append("source_candidates must be a non-empty list")
        elif any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in source_candidates):
            errors.append("source_candidates must contain positive integers")
        candidate_id = decision.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            errors.append("candidate_id must be a non-empty string")
    if disposition == "latex_confirmed":
        latex = decision.get("latex")
        if not isinstance(latex, str) or not latex.strip():
            errors.append("latex_confirmed requires non-empty latex")
    if disposition == "crop_only":
        reason = decision.get("unresolved_reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("crop_only requires unresolved_reason")
    if disposition in {"not_formula", "removed"}:
        reason = decision.get("reason", decision.get("unresolved_reason"))
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{disposition} requires a concrete reason")
    candidate_index = decision.get("candidate_index")
    if candidate_index is not None and (
        isinstance(candidate_index, bool) or not isinstance(candidate_index, int) or candidate_index <= 0
    ):
        errors.append("candidate_index must be a positive integer")

    if errors:
        return None, errors
    normalized = dict(decision)
    normalized["source"] = candidate
    normalized["source_text"] = candidate
    normalized["candidate"] = candidate
    normalized["pdf_page"] = page_number
    if disposition in {"not_formula", "removed"} and "reason" not in normalized:
        normalized["reason"] = normalized.get("unresolved_reason")
    return normalized, []


def append_formula_decision_blocks(
    markdown: str,
    formulas_by_page: dict[int, list[dict[str, Any]]],
) -> str:
    """Append only Agent-confirmed visual formulas; never rewrite text candidates."""

    matches = list(PAGE_MARKER_RE.finditer(markdown))
    chunks: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        start = match.start()
        chunks.append(markdown[cursor:start])
        block = markdown[start:end].rstrip()
        additions: list[str] = []
        for decision in formulas_by_page.get(int(match.group(1)), []):
            if decision.get("disposition") != "latex_confirmed":
                continue
            source_text = decision.get("source_text", decision.get("source", ""))
            visual_source = decision.get("source_asset") or f"PDF page {decision.get('source_pdf_page', match.group(1))}"
            additions.extend(
                [
                    "### Visually reconstructed formula",
                    "",
                    f"Text-layer locator (secondary only): `{source_text}`",
                    "",
                    f"Visual source (Agent inspection): `{visual_source}`",
                    "",
                    *(
                        [
                            f"Visual context source (Agent inspection): `{decision['context_asset']}`",
                            "",
                        ]
                        if decision.get("context_asset")
                        else []
                    ),
                    "$$",
                    str(decision["latex"]),
                    "$$",
                    "",
                ]
            )
        if additions:
            block += "\n\n" + "\n".join(additions).rstrip()
        chunks.append(block + "\n\n")
        cursor = end
    chunks.append(markdown[cursor:])
    return "".join(chunks)


def append_page_notes(markdown: str, notes_by_page: dict[int, list[str]]) -> str:
    """Append concrete Phase 2 notes to their source-page blocks."""

    matches = list(PAGE_MARKER_RE.finditer(markdown))
    chunks: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        start = match.start()
        chunks.append(markdown[cursor:start])
        block = markdown[start:end].rstrip()
        page_notes = notes_by_page.get(int(match.group(1)), [])
        if page_notes:
            block += "\n\n" + "\n\n".join(f"> [Transcription note: {note}]" for note in page_notes)
        chunks.append(block + "\n\n")
        cursor = end
    chunks.append(markdown[cursor:])
    return "".join(chunks)


def phase2_visual_review(
    document: pymupdf.Document,
    md_path: Path,
    state: dict[str, Any],
    *,
    visual_decisions_path: Path,
) -> dict[str, Any]:
    """Apply only explicit Agent visual decisions to the Phase 1 output."""

    queued_pages = [int(page) for page in state.get("visual_review_required", [])]
    formula_candidates = {
        int(page): [str(value) for value in values]
        for page, values in state.get("formula_candidates", {}).items()
    }
    records_by_page: dict[int, list[dict[str, Any]]] = {}
    raw_records = state.get("formula_candidate_records", {})
    if isinstance(raw_records, dict):
        for raw_page, records in raw_records.items():
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                continue
            if isinstance(records, list):
                records_by_page[page] = [record for record in records if isinstance(record, dict)]
    for page, candidates in formula_candidates.items():
        if page in records_by_page:
            continue
        records_by_page[page] = [
            {
                "candidate_index": index,
                "candidate_id": f"pdf-page-{page:03d}-formula-{index:02d}",
                "candidate": candidate,
                "source_asset": f"assets/pdf_page_{page:03d}_formula_{index:02d}.png",
                "context_asset": None,
                "context_type": "line",
                "source_candidates": [index],
            }
            for index, candidate in enumerate(candidates, start=1)
        ]
    assets = [str(asset) for asset in state.get("visual_assets", [])]
    loaded = load_visual_decisions(
        visual_decisions_path,
        book_identifier=Path(str(state.get("source", ""))).stem or None,
    )
    supplied_by_page: dict[int, list[dict[str, Any]]] = loaded["formulas"]
    visual_confirmed_pages: set[int] = loaded["visual_pages"]
    text_visual_confirmed_pages: set[int] = loaded["text_pages"]
    notes_by_page: dict[int, list[str]] = {}
    formula_blocks: dict[int, list[dict[str, Any]]] = defaultdict(list)
    pending: list[dict[str, Any]] = []
    remaining_queue: list[int] = []
    verified: set[int] = set(int(page) for page in state.get("visual_verified_pdf_pages", []))
    decisions: dict[str, dict[str, Any]] = {}
    trace: list[dict[str, Any]] = []

    for page_number in queued_pages:
        reasons = list(state.get("visual_review_reasons", {}).get(str(page_number), []))
        page_visual_assets = [asset for asset in assets if f"pdf_page_{page_number:03d}_visual_" in asset]
        page_decision: dict[str, Any] = {"formulas": [], "visuals": [], "status": "visual_review_required"}
        page_pending: list[dict[str, Any]] = []
        page_notes: list[str] = []
        missing_decision = False
        expected_records = records_by_page.get(page_number, [])
        if not expected_records:
            expected_records = [
                {
                    "candidate_index": index,
                    "candidate_id": f"pdf-page-{page_number:03d}-formula-{index:02d}",
                    "candidate": candidate,
                    "source_asset": f"assets/pdf_page_{page_number:03d}_formula_{index:02d}.png",
                    "context_asset": None,
                    "context_type": "line",
                    "source_candidates": [index],
                }
                for index, candidate in enumerate(formula_candidates.get(page_number, []), start=1)
            ]
        supplied_decisions = supplied_by_page.get(page_number, [])
        used_supplied: set[int] = set()

        for record_index, record in enumerate(expected_records, start=1):
            candidate = str(record.get("candidate", ""))
            candidate_index = int(record.get("candidate_index", record_index))
            candidate_id = str(record.get("candidate_id", f"pdf-page-{page_number:03d}-formula-{candidate_index:02d}"))
            matched_index: int | None = None
            for supplied_index, supplied in enumerate(supplied_decisions):
                if supplied_index in used_supplied:
                    continue
                supplied_index_value = supplied.get("candidate_index")
                if supplied_index_value is not None:
                    try:
                        if int(supplied_index_value) == candidate_index:
                            matched_index = supplied_index
                            break
                    except (TypeError, ValueError):
                        continue
                if supplied.get("candidate_id") == candidate_id:
                    matched_index = supplied_index
                    break
                if supplied.get("candidate") == candidate:
                    matched_index = supplied_index
                    break

            if matched_index is None:
                missing_decision = True
                trace.append(
                    {
                        "pdf_page": page_number,
                        "candidate": candidate,
                        "candidate_id": candidate_id,
                        "source_candidates": record.get("source_candidates"),
                        "visual_source": record.get("source_asset"),
                        "context_source": record.get("context_asset"),
                        "visual_inspected": False,
                        "decision": None,
                        "error": "missing visual decision",
                    }
                )
                continue
            used_supplied.add(matched_index)
            supplied_decision = supplied_decisions[matched_index]
            raw_decision = dict(supplied_decision)
            for provenance_key in (
                "candidate_id",
                "candidate_index",
                "source_asset",
                "context_asset",
                "context_type",
                "source_candidates",
            ):
                if raw_decision.get(provenance_key) is None and record.get(provenance_key) is not None:
                    raw_decision[provenance_key] = record[provenance_key]
            if raw_decision.get("source_pdf_page") is None:
                raw_decision["source_pdf_page"] = page_number
            candidate_mismatch = raw_decision.get("candidate") not in {None, "", candidate}
            normalized, errors = validate_visual_formula_decision(
                raw_decision,
                page_number=page_number,
                candidate=candidate,
                output_dir=md_path.parent,
            )
            if candidate_mismatch:
                errors = ["decision candidate does not match the selected PDF candidate"] + errors
                normalized = None
            if normalized is None:
                missing_decision = True
                trace.append(
                    {
                        "pdf_page": page_number,
                        "candidate": candidate,
                        "candidate_id": candidate_id,
                        "candidate_index": candidate_index,
                        "source_candidates": record.get("source_candidates"),
                        "visual_source": raw_decision.get("source_asset") or raw_decision.get("source_pdf_page"),
                        "context_source": raw_decision.get("context_asset"),
                        "visual_inspected": False,
                        "decision": "invalid",
                        "errors": errors,
                    }
                )
                continue
            page_decision["formulas"].append(normalized)
            disposition = str(normalized["disposition"])
            visual_source = normalized.get("source_asset") or f"PDF page {page_number}"
            trace.append(
                {
                    "pdf_page": page_number,
                    "candidate": candidate,
                    "candidate_id": normalized.get("candidate_id", candidate_id),
                    "candidate_index": normalized.get("candidate_index", candidate_index),
                    "source_candidates": normalized.get("source_candidates"),
                    "visual_source": visual_source,
                    "context_source": normalized.get("context_asset"),
                    "context_type": normalized.get("context_type"),
                    "visual_inspected": True,
                    "decision": disposition,
                    "latex": normalized.get("latex"),
                }
            )
            if disposition == "latex_confirmed":
                formula_blocks[page_number].append(normalized)
            elif disposition == "crop_only":
                reason = str(normalized["unresolved_reason"])
                item = {
                    "pdf_page": page_number,
                    "kind": "formula",
                    "status": "visually_reviewed_unresolved",
                    "region": "formula candidate context group",
                    "reason": reason,
                }
                for provenance_key in (
                    "source_asset",
                    "context_asset",
                    "source_pdf_page",
                    "candidate_id",
                    "candidate_index",
                    "source_candidates",
                    "context_type",
                ):
                    if normalized.get(provenance_key) is not None:
                        item[provenance_key] = normalized[provenance_key]
                page_pending.append(item)
                page_notes.append(f"PDF 第 {page_number} 页公式已视觉检查，但结构仍不安全；保留裁剪并未生成 LaTeX：{reason}")

        for supplied_index, extra in enumerate(supplied_decisions):
            if supplied_index in used_supplied:
                continue
            missing_decision = True
            extra_candidate = extra.get("candidate") or extra.get("candidate_id") or "<unknown candidate>"
            trace.append(
                {
                    "pdf_page": page_number,
                    "candidate": extra_candidate,
                    "candidate_id": extra.get("candidate_id"),
                    "candidate_index": extra.get("candidate_index"),
                    "source_candidates": extra.get("source_candidates"),
                    "visual_source": extra.get("source_asset") or extra.get("source_pdf_page"),
                    "context_source": extra.get("context_asset"),
                    "visual_inspected": False,
                    "decision": "unexpected candidate",
                    "error": "decision has no matching formula candidate",
                }
            )

        if page_visual_assets and page_number in visual_confirmed_pages:
            page_decision["visuals"].append(
                {
                    "disposition": "necessary_crop_retained",
                    "verification": "visual",
                    "source_assets": page_visual_assets,
                }
            )
        elif page_visual_assets:
            missing_decision = True
            page_decision["visuals"].append({"disposition": "visual_review_required"})
        elif "visual-evidence-not-cropped" in reasons and page_number in visual_confirmed_pages:
            page_decision["visuals"].append({"disposition": "markdown_sufficient", "verification": "visual"})
        elif "embedded-visual-retention" in reasons or "visual-evidence-not-cropped" in reasons:
            missing_decision = True
            page_decision["visuals"].append({"disposition": "visual_review_required"})
        elif "meaningful-visual-cue" in reasons:
            if page_number in visual_confirmed_pages:
                page_decision["visuals"].append({"disposition": "markdown_sufficient", "verification": "visual"})
            else:
                missing_decision = True
                page_decision["visuals"].append({"disposition": "visual_review_required"})

        if "text-layer-anomaly" in reasons:
            if page_number in text_visual_confirmed_pages:
                page_decision["text"] = {
                    "disposition": "placeholder_retained",
                    "verification": "visual",
                    "reason": "page visual inspected; text-layer anomaly retained as an explicit placeholder without guessing a glyph",
                }
            else:
                missing_decision = True
                page_decision["text"] = {"disposition": "visual_review_required"}
        if any(reason in reasons for reason in ("formula-crop-render-failed", "visual-crop-render-failed")):
            missing_decision = True

        if missing_decision:
            remaining_queue.append(page_number)
            page_decision["status"] = "visual_review_required"
        elif page_pending:
            page_decision["status"] = "completed_with_review_items"
            pending.extend(page_pending)
        else:
            page_decision["status"] = "visually_verified"
            verified.add(page_number)
        if page_notes:
            notes_by_page[page_number] = sorted(set(page_notes))
        decisions[str(page_number)] = page_decision

    for page_number, extra_decisions in supplied_by_page.items():
        if page_number in queued_pages:
            continue
        for extra in extra_decisions:
            trace.append(
                {
                    "pdf_page": page_number,
                    "candidate": extra.get("candidate") or extra.get("candidate_id") or "<unknown candidate>",
                    "candidate_id": extra.get("candidate_id"),
                    "candidate_index": extra.get("candidate_index"),
                    "source_candidates": extra.get("source_candidates"),
                    "visual_source": extra.get("source_asset") or extra.get("source_pdf_page"),
                    "context_source": extra.get("context_asset"),
                    "visual_inspected": False,
                    "decision": "unexpected page",
                    "error": "decision page was not in the Phase 1 review queue",
                }
            )

    markdown = md_path.read_text(encoding="utf-8")
    markdown = append_formula_decision_blocks(markdown, formula_blocks)
    markdown = append_page_notes(markdown, notes_by_page)
    md_path.write_text(markdown, encoding="utf-8", newline="\n")

    trace_path = md_path.parent / "execution_trace.json"
    trace_document = {
        "schema_version": 1,
        "runner": "vision_first_pdf_to_md.py",
        "source": state.get("source"),
        "skill_revision": state.get("skill", {}).get("revision"),
        "visual_decisions": str(visual_decisions_path.resolve()),
        "events": trace,
    }
    trace_path.write_text(json.dumps(trace_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state.update(
        {
            "visual_review_required": sorted(set(remaining_queue)),
            "pending_review": pending,
            "visual_verified_pdf_pages": sorted(verified),
            "visual_review_decisions": decisions,
            "formula_latex_confirmed": sum(
                1 for page in decisions.values() for item in page.get("formulas", []) if item.get("disposition") == "latex_confirmed"
            ),
            "formula_crop_only_pending": sum(
                1 for page in decisions.values() for item in page.get("formulas", []) if item.get("disposition") == "crop_only"
            ),
            "formula_not_formula": sum(
                1 for page in decisions.values() for item in page.get("formulas", []) if item.get("disposition") == "not_formula"
            ),
            "formula_removed": sum(
                1 for page in decisions.values() for item in page.get("formulas", []) if item.get("disposition") == "removed"
            ),
            "execution_trace": trace,
            "execution_trace_path": trace_path.name,
            "status": "visual_review" if remaining_queue else ("completed_with_review_items" if pending else "completed"),
        }
    )
    return state


def convert_book(
    source: Path,
    target_root: Path,
    *,
    force_fresh: bool,
    skill_revision: str,
    pages: list[int] | None = None,
    visual_decisions_path: Path | None = None,
    formula_selection_path: Path | None = None,
) -> dict[str, Any]:
    with pymupdf.open(str(source)) as document:
        total_pages = document.page_count
        output_dir = target_root / source.stem
        md_path = output_dir / f"{source.stem}.md"
        state_path = output_dir / "conversion_state.json"
        state = reset_output(
            md_path,
            state_path,
            source,
            total_pages,
            skill_revision=skill_revision,
        )
        if pages is None:
            requested_pages = list(range(1, total_pages + 1))
        else:
            requested_pages = sorted(set(int(page) for page in pages))
            invalid_pages = [page for page in requested_pages if page < 1 or page > total_pages]
            if invalid_pages:
                raise ValueError(f"requested PDF page(s) out of range: {invalid_pages}")
        formula_selection = load_formula_selection(formula_selection_path, book_identifier=source.stem)
        state["requested_pdf_pages"] = requested_pages
        write_state(state_path, state)
        noise = common_noise(document)
        xref_counts = Counter(
            int(info.get("xref") or 0)
            for source_page in document
            for info in source_page.get_image_info(xrefs=True)
            if info.get("xref")
        )
        repeated_xrefs = {xref for xref, count in xref_counts.items() if count > 4}
        assets: list[str] = []
        review_pages: list[int] = []
        review_reasons: dict[str, list[str]] = {}
        formula_candidates_by_page: dict[str, list[str]] = {}
        formula_candidate_records_by_page: dict[str, list[dict[str, Any]]] = {}
        formula_review_groups_by_page: dict[str, list[dict[str, Any]]] = {}
        current_section: str | None = None
        processed_pages: list[int] = []

        for page_number in requested_pages:
            page_index = page_number - 1
            page = document[page_index]
            source_text = page.get_text("text", sort=True)
            lines, text_changed = clean_page_lines(page, noise)
            notes: list[str] = []
            if not lines:
                lines = ["[本页未取得可用文本层，内容以 PDF 页面视觉为准。]"]
                notes.append(f"PDF 第 {page_number} 页未取得可用文本层；当前正文仅作占位，需以该页 PDF 视觉补全。")

            all_formula_candidates = formula_bboxes(page)
            selected_candidate_indices = selected_formula_candidate_indices(
                all_formula_candidates,
                formula_selection,
                page_number,
            )
            formula_candidates = [all_formula_candidates[index] for index in selected_candidate_indices]
            formula_lines = {text for _rect, text in formula_candidates}
            formula_candidates_by_page[str(page_number)] = [text for _rect, text in formula_candidates]
            all_formula_groups = formula_candidate_groups(page, all_formula_candidates)
            group_by_candidate: dict[int, dict[str, Any]] = {}
            for group in all_formula_groups:
                for candidate_index in group["candidate_indices"]:
                    group_by_candidate[int(candidate_index)] = group
            selected_groups: list[dict[str, Any]] = []
            selected_group_ids: set[str] = set()
            for candidate_index in selected_candidate_indices:
                group = group_by_candidate.get(candidate_index + 1)
                if group is not None and group["group_id"] not in selected_group_ids:
                    selected_groups.append(group)
                    selected_group_ids.add(group["group_id"])
            formula_group_payloads: list[dict[str, Any]] = []
            for group in selected_groups:
                formula_group_payloads.append(
                    {
                        "group_id": group["group_id"],
                        "source_candidates": list(group["candidate_indices"]),
                        "context_type": group["context_type"],
                        "context_asset": f"assets/{group['context_filename']}",
                        "candidate_bbox": _rect_values(group["candidate_bbox"]),
                        "context_bbox": _rect_values(group["context_bbox"]),
                    }
                )
            formula_review_groups_by_page[str(page_number)] = formula_group_payloads
            visual_candidates = image_bboxes(page, repeated_xrefs)
            page_assets: list[str] = []
            page_reasons: list[str] = []

            if formula_candidates:
                page_reasons.append("formula-or-mathematical-expression")
            elif MATH_CUE_RE.search(source_text):
                page_reasons.append("possible-mathematical-expression")
            if VISUAL_CUE_RE.search(source_text):
                page_reasons.append("meaningful-visual-cue")
            if visual_candidates:
                page_reasons.append("embedded-visual-retention")
            if text_changed:
                page_reasons.append("text-layer-anomaly")
                notes.append(f"PDF 第 {page_number} 页文本层出现无法可靠确认的字符，异常位置以 □ 表示；请以该页 PDF 视觉为准。")

            for visual_index, rect in enumerate(visual_candidates, start=1):
                relative = Path("assets") / f"pdf_page_{page_number:03d}_visual_{visual_index:02d}.png"
                try:
                    render_crop(page, rect, output_dir / relative, dpi=170)
                    page_assets.append(relative.as_posix())
                except Exception:
                    page_reasons.append("visual-crop-render-failed")

            rendered_formula_assets: dict[int, str] = {}
            for candidate_index in selected_candidate_indices:
                rect, _text = all_formula_candidates[candidate_index]
                relative = Path("assets") / f"pdf_page_{page_number:03d}_formula_{candidate_index + 1:02d}.png"
                try:
                    render_crop(page, rect, output_dir / relative, dpi=220)
                    page_assets.append(relative.as_posix())
                    rendered_formula_assets[candidate_index + 1] = relative.as_posix()
                except Exception:
                    page_reasons.append("formula-crop-render-failed")

            rendered_context_assets: dict[str, str] = {}
            for group in selected_groups:
                relative = Path("assets") / group["context_filename"]
                try:
                    render_crop(page, group["context_bbox"], output_dir / relative, dpi=180)
                    page_assets.append(relative.as_posix())
                    rendered_context_assets[group["group_id"]] = relative.as_posix()
                except Exception:
                    page_reasons.append("formula-context-crop-render-failed")

            formula_records: list[dict[str, Any]] = []
            for candidate_index in selected_candidate_indices:
                rect, candidate_text = all_formula_candidates[candidate_index]
                group = group_by_candidate.get(candidate_index + 1)
                if group is None:
                    source_candidates = [candidate_index + 1]
                    context_type = "line"
                    context_asset = None
                    context_bbox = rect
                else:
                    source_candidates = list(group["candidate_indices"])
                    context_type = str(group["context_type"])
                    context_asset = rendered_context_assets.get(group["group_id"])
                    context_bbox = group["context_bbox"]
                formula_records.append(
                    {
                        "candidate_index": candidate_index + 1,
                        "candidate_id": f"pdf-page-{page_number:03d}-formula-{candidate_index + 1:02d}",
                        "candidate": candidate_text,
                        "source_asset": rendered_formula_assets.get(candidate_index + 1),
                        "context_asset": context_asset,
                        "context_type": context_type,
                        "source_candidates": source_candidates,
                        "candidate_bbox": _rect_values(rect),
                        "context_bbox": _rect_values(context_bbox),
                    }
                )
            formula_candidate_records_by_page[str(page_number)] = formula_records

            if (VISUAL_CUE_RE.search(source_text) or MATH_CUE_RE.search(source_text)) and not page_assets:
                page_reasons.append("visual-evidence-not-cropped")
            if page_reasons:
                review_pages.append(page_number)
                review_reasons[str(page_number)] = sorted(set(page_reasons))

            for asset in page_assets:
                if asset not in assets:
                    assets.append(asset)
            page_text = page_markdown(
                page_number,
                lines,
                visual_assets=page_assets,
                formula_lines=formula_lines,
                page_notes=notes,
            )
            with md_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(page_text)
            for line in lines:
                if line_is_heading(line):
                    current_section = line
            processed_pages.append(page_number)
            state.update(
                {
                    "last_completed_pdf_page": page_number,
                    "completed_pdf_pages": len(processed_pages),
                    "last_transcribed_pdf_page": page_number,
                    "current_section": current_section,
                    "requested_pdf_pages": requested_pages,
                    "processed_pdf_pages": processed_pages,
                    "pending_review": [],
                    "visual_review_required": sorted(set(review_pages)),
                    "visual_review_reasons": review_reasons,
                    "formula_candidates": formula_candidates_by_page,
                    "formula_candidate_records": formula_candidate_records_by_page,
                    "formula_review_groups": formula_review_groups_by_page,
                    "visual_assets": assets,
                    "image_references": len(assets),
                    "status": "in_progress",
                }
            )
            write_state(state_path, state)
            print(f"{source.name}: {page_number}/{total_pages}", flush=True)

        state["status"] = "visual_review" if review_pages else "completed"
        write_state(state_path, state)
        if review_pages and visual_decisions_path is not None:
            state = phase2_visual_review(
                document,
                md_path,
                state,
                visual_decisions_path=visual_decisions_path.resolve(),
            )
            write_state(state_path, state)
        return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--only", action="append")
    parser.add_argument("--pages", help="comma-separated PDF page numbers for a canary or targeted audit")
    parser.add_argument("--visual-decisions", type=Path, help="Agent-produced visual formula decisions JSON")
    parser.add_argument("--formula-selection", type=Path, help="JSON manifest limiting the formula candidates for an audit")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--skill-revision", default="working-tree")
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    target_root = args.target_root.resolve()
    sources = sorted(source_root.glob("*.pdf"), key=lambda path: path.name)
    if args.only:
        selected = set(args.only)
        sources = [path for path in sources if path.stem in selected or path.name in selected]
    if not sources:
        print(f"No PDFs found under {source_root}", file=sys.stderr)
        return 2
    pages = None
    if args.pages:
        try:
            pages = [int(value.strip()) for value in args.pages.split(",") if value.strip()]
        except ValueError as exc:
            parser.error(f"--pages must be a comma-separated list of integers: {exc}")
        if not pages:
            parser.error("--pages must contain at least one page number")
    target_root.mkdir(parents=True, exist_ok=True)
    for source in sources:
        state = convert_book(
            source,
            target_root,
            force_fresh=args.fresh,
            skill_revision=args.skill_revision,
            pages=pages,
            visual_decisions_path=args.visual_decisions,
            formula_selection_path=args.formula_selection,
        )
        print(
            json.dumps(
                {
                    "book": source.name,
                    "status": state.get("status"),
                    "completed_pdf_pages": state.get("completed_pdf_pages"),
                    "visual_review_required": len(state.get("visual_review_required", [])),
                    "pending_review": len(state.get("pending_review", [])),
                    "formula_latex_confirmed": state.get("formula_latex_confirmed", 0),
                    "execution_trace": state.get("execution_trace_path"),
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
