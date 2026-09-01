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
        "visual_assets": [],
        "image_references": 0,
        "execution_trace": [],
        "status": "in_progress",
    }
    write_state(state_path, state)
    return state


def load_visual_decisions(path: Path | None) -> dict[str, Any]:
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
        raw_page = entry.get("pdf_page")
        try:
            page = int(raw_page)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"formula decision has invalid pdf_page: {raw_page!r}") from exc
        candidate = entry.get("candidate", entry.get("source_text", entry.get("source")))
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError(f"formula decision on page {page} lacks candidate/source_text")
        normalized = dict(entry)
        normalized["pdf_page"] = page
        normalized["candidate"] = candidate
        result["formulas"][page].append(normalized)
        result["visual_pages"].add(page)
    return result


def load_formula_selection(path: Path | None) -> dict[int, list[str]] | None:
    """Optionally limit Phase 1 to an explicit audit selection of candidates."""

    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(f"formula selection must contain an items list: {path}")
    selected: dict[int, list[str]] = defaultdict(list)
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"formula selection item must be an object: {entry!r}")
        try:
            page = int(entry["pdf_page"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"formula selection item has invalid pdf_page: {entry!r}") from exc
        candidate = entry.get("candidate", entry.get("source_text", entry.get("source")))
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError(f"formula selection item lacks candidate/source_text: {entry!r}")
        selected[page].append(candidate)
    return dict(selected)


def filter_formula_candidates(
    candidates: list[tuple[pymupdf.Rect, str]],
    selection: dict[int, list[str]] | None,
    page_number: int,
) -> list[tuple[pymupdf.Rect, str]]:
    """Keep each explicitly selected candidate once, preserving PDF order."""

    if selection is None:
        return candidates
    remaining = Counter(selection.get(page_number, []))
    filtered: list[tuple[pymupdf.Rect, str]] = []
    for rect, text in candidates:
        if remaining[text] <= 0:
            continue
        filtered.append((rect, text))
        remaining[text] -= 1
    missing = {text: count for text, count in remaining.items() if count}
    if missing:
        raise ValueError(f"formula selection not found on PDF page {page_number}: {missing!r}")
    return filtered


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

    source_asset = decision.get("source_asset")
    source_page = decision.get("source_pdf_page")
    valid_asset = False
    if source_asset is not None:
        if not isinstance(source_asset, str) or not source_asset.strip():
            errors.append("source_asset must be a non-empty relative path")
        else:
            asset_path = Path(source_asset)
            if asset_path.is_absolute():
                errors.append("source_asset must stay relative to the Markdown directory")
            else:
                root = output_dir.resolve()
                resolved = (root / asset_path).resolve()
                try:
                    resolved.relative_to(root)
                except ValueError:
                    errors.append("source_asset escapes the Markdown directory")
                else:
                    valid_asset = resolved.is_file()
                    if not valid_asset:
                        errors.append(f"source_asset does not exist: {source_asset}")
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
    if disposition == "latex_confirmed":
        latex = decision.get("latex")
        if not isinstance(latex, str) or not latex.strip():
            errors.append("latex_confirmed requires non-empty latex")
    if disposition == "crop_only":
        reason = decision.get("unresolved_reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("crop_only requires unresolved_reason")

    if errors:
        return None, errors
    normalized = dict(decision)
    normalized["source"] = candidate
    normalized["source_text"] = candidate
    normalized["candidate"] = candidate
    normalized["pdf_page"] = page_number
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
    assets = [str(asset) for asset in state.get("visual_assets", [])]
    loaded = load_visual_decisions(visual_decisions_path)
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
        candidate_decisions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for supplied in supplied_by_page.get(page_number, []):
            candidate_decisions[str(supplied["candidate"])].append(supplied)

        for candidate in formula_candidates.get(page_number, []):
            choices = candidate_decisions.get(candidate, [])
            if not choices:
                missing_decision = True
                trace.append(
                    {
                        "pdf_page": page_number,
                        "candidate": candidate,
                        "visual_source": None,
                        "visual_inspected": False,
                        "decision": None,
                        "error": "missing visual decision",
                    }
                )
                continue
            raw_decision = choices.pop(0)
            normalized, errors = validate_visual_formula_decision(
                raw_decision,
                page_number=page_number,
                candidate=candidate,
                output_dir=md_path.parent,
            )
            if normalized is None:
                missing_decision = True
                trace.append(
                    {
                        "pdf_page": page_number,
                        "candidate": candidate,
                        "visual_source": raw_decision.get("source_asset") or raw_decision.get("source_pdf_page"),
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
                    "visual_source": visual_source,
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
                    "region": "bounded formula crop",
                    "reason": reason,
                }
                if normalized.get("source_asset"):
                    item["source_asset"] = normalized["source_asset"]
                if normalized.get("source_pdf_page"):
                    item["source_pdf_page"] = normalized["source_pdf_page"]
                page_pending.append(item)
                page_notes.append(f"PDF 第 {page_number} 页公式已视觉检查，但结构仍不安全；保留裁剪并未生成 LaTeX：{reason}")

        for extra_candidate, extra_decisions in candidate_decisions.items():
            for _extra in extra_decisions:
                missing_decision = True
                trace.append(
                    {
                        "pdf_page": page_number,
                        "candidate": extra_candidate,
                        "visual_source": _extra.get("source_asset") or _extra.get("source_pdf_page"),
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
                    "candidate": extra["candidate"],
                    "visual_source": extra.get("source_asset") or extra.get("source_pdf_page"),
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
        formula_selection = load_formula_selection(formula_selection_path)
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

            formula_candidates = filter_formula_candidates(
                formula_bboxes(page), formula_selection, page_number
            )
            formula_lines = {text for _rect, text in formula_candidates}
            formula_candidates_by_page[str(page_number)] = sorted(formula_lines)
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

            for formula_index, (rect, _text) in enumerate(formula_candidates, start=1):
                relative = Path("assets") / f"pdf_page_{page_number:03d}_formula_{formula_index:02d}.png"
                try:
                    render_crop(page, rect, output_dir / relative, dpi=220)
                    page_assets.append(relative.as_posix())
                except Exception:
                    page_reasons.append("formula-crop-render-failed")

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
