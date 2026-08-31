#!/usr/bin/env python3
"""Optional deterministic QA for Agent-authored PDF transcription Markdown.

This helper checks traceability and Markdown hygiene. It does not decide
headings, reading order, formula meaning, table cell relationships, or which
visuals matter; those decisions belong to the Agent reading the source PDF.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)")
PAGE_RE = re.compile(r"<!--\s*PDF page\s+(\d+)(?:\s*\|\s*printed page\s+(\d+|unknown))?\s*-->")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}
DISPLAY_MATH_RE = re.compile(r"\$\$(?P<content>[\s\S]*?)\$\$")
INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(?P<content>[^$\n]*?)(?<!\\)\$(?!\$)")
PAREN_MATH_RE = re.compile(r"\\\((?P<content>[\s\S]*?)(?<!\\)\\\)")
BRACKET_MATH_RE = re.compile(r"\\\[(?P<content>[\s\S]*?)(?<!\\)\\\]")
MATH_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]")
MATH_TEXT_LABEL_RE = re.compile(r"\\(?:text|mathrm|operatorname)\s*\{[^{}]*\}")
TRANSCRIPTION_NOTE_RE = re.compile(r"\[Transcription note:", re.IGNORECASE)
EXPECTED_LETTER_SCRIPTS = (
    "LATIN",
    "GREEK",
    "CJK UNIFIED IDEOGRAPH",
    "CJK COMPATIBILITY",
    "HIRAGANA",
    "KATAKANA",
    "MICRO SIGN",
    "IDEOGRAPHIC ITERATION MARK",
)
COMPACT_TOKEN_RE = re.compile(
    r"(?<![_^{}\\])\b(?:[A-Za-z]{2,}\d+|[A-Za-z]+\d{2,}|[A-Za-z]+\d+[A-Za-z]+\d+)\b"
)
FLATTENED_UNIT_RE = re.compile(
    r"(?<![\\^_{}A-Za-z])(?:m\s*/\s*s|N\s*/\s*kg|m\s*·\s*s)\s*\d+(?![A-Za-z0-9])"
)
FULLWIDTH_MATH_PUNCT_RE = re.compile(r"[（），。＋－＝：；]")
FULLWIDTH_EQUATION_LABEL_RE = re.compile(r"（\s*\d+\s*）")
LATEX_STRUCTURE_RE = re.compile(
    r"(?:[_^{}]|\\(?:frac|dfrac|tfrac|sqrt|cdot|times|mathrm|text|operatorname|"
    r"sin|cos|tan|log|ln|Delta|alpha|beta|gamma|theta|mu|pi|sum|int|le|ge|approx|pm|perp|parallel)(?![A-Za-z]))"
)
MATH_TOKEN_RE = re.compile(r"[A-Za-z]+|\d+(?:\.\d+)?")
PLAIN_NUMBER_RE = re.compile(r"(?:(?<=\s)|(?<=[=＋＝]))\d+(?:\.\d+)?(?=\s|$)")
BROKEN_EQUAL_RE = re.compile(r"=\s*=")
FRAGMENT_LINE_RE = re.compile(r"(?:[A-Za-z]{1,4}|\d+(?:\.\d+)?|[=＋－+\-])")
ARTIFACT_WARN_THRESHOLD = 3
ARTIFACT_FAIL_THRESHOLD = 6


def _check(checks: list[dict[str, Any]], check_id: str, status: str, message: str, **details: Any) -> None:
    checks.append({"id": check_id, "status": status, "message": message, **details})


def _image_matches(markdown: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for match in IMAGE_RE.finditer(markdown):
        alt = match.group(1) or ""
        reference = match.group(2) or match.group(3) or ""
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//)", reference, re.I):
            continue
        reference = unquote(reference.split("#", 1)[0].split("?", 1)[0])
        result.append((alt, reference))
    return result


def local_image_refs(markdown: str) -> list[str]:
    """Return local Markdown image paths for callers that need a small API."""

    return [reference for _alt, reference in _image_matches(markdown)]


def _math_matches(markdown: str) -> list[tuple[str, str, int]]:
    """Return math spans as ``(delimiter_kind, content, character_offset)``."""

    matches: list[tuple[str, str, int]] = []
    for kind, pattern in (
        ("display", DISPLAY_MATH_RE),
        ("inline", INLINE_MATH_RE),
        ("parenthesized", PAREN_MATH_RE),
        ("bracketed", BRACKET_MATH_RE),
    ):
        matches.extend((kind, match.group("content"), match.start()) for match in pattern.finditer(markdown))
    return sorted(matches, key=lambda item: item[2])


def suspicious_math_blocks(markdown: str) -> list[str]:
    """Find math spans that contain likely prose rather than a formula.

    Short labels explicitly wrapped in ``\\text{...}`` are tolerated. Any CJK
    character outside such a label is treated as a high-confidence extraction
    error: even a short fragment may be prose that was accidentally wrapped in
    a math block.
    """

    findings: list[str] = []
    for kind, content, offset in _math_matches(markdown):
        content_without_labels = MATH_TEXT_LABEL_RE.sub("", content)
        if not MATH_CJK_RE.search(content_without_labels):
            continue
        line_number = markdown.count("\n", 0, offset) + 1
        snippet = " ".join(content.split())
        findings.append(f"{kind} math at line {line_number} contains likely Chinese prose: {snippet[:160]}")
    return findings


def _is_unexpected_letter(character: str) -> bool:
    if not character.isalpha():
        return False
    name = unicodedata.name(character, "")
    return bool(name) and not any(script in name for script in EXPECTED_LETTER_SCRIPTS)


def suspicious_garbage(markdown: str) -> list[str]:
    """Find high-confidence Unicode extraction garbage.

    Replacement/private-use characters always fail.  Other scripts are only
    reported when they cluster tightly, reducing false positives for ordinary
    Latin, Greek, and CJK textbook text while catching mixed-script corruption.
    """

    findings: list[str] = []
    if "�" in markdown:
        findings.append("replacement character U+FFFD")
    if PRIVATE_USE_RE.search(markdown):
        findings.append("private-use character")

    unexpected = [index for index, character in enumerate(markdown) if _is_unexpected_letter(character)]
    if len(unexpected) >= 2:
        tightly_clustered = any(second - first <= 24 for first, second in zip(unexpected, unexpected[1:]))
        if tightly_clustered:
            examples = "".join(markdown[index] for index in unexpected[:8])
            findings.append(f"clustered unexpected-script letters near text: {examples}")
    return findings


def _transcription_note_stats(markdown: str, page_count: int) -> tuple[int, float]:
    count = len(TRANSCRIPTION_NOTE_RE.findall(markdown))
    ratio = count / max(1, page_count)
    return count, ratio


def _nearest_pdf_page(markdown: str, offset: int) -> int | None:
    page: int | None = None
    for match in PAGE_RE.finditer(markdown):
        if match.start() > offset:
            break
        page = int(match.group(1))
    return page


def _artifact_structure_hits(content: str) -> list[str]:
    return LATEX_STRUCTURE_RE.findall(content)


def _math_artifact_score(content: str) -> tuple[int, list[dict[str, Any]]]:
    """Score high-confidence signs of a flattened PDF text-layer formula.

    The score is intentionally structural. It does not attempt to infer the
    equation's meaning or repair it.
    """

    raw_content = content
    content = MATH_TEXT_LABEL_RE.sub("", content)
    signals: list[dict[str, Any]] = []

    def add_signal(signal_id: str, points: int, evidence: Any) -> None:
        signals.append({"id": signal_id, "score": points, "evidence": evidence})

    compact_tokens = COMPACT_TOKEN_RE.findall(content)
    if compact_tokens:
        add_signal("digit_letter_flattening", 2, compact_tokens[:8])

    plain_numbers = PLAIN_NUMBER_RE.findall(content)
    if len(plain_numbers) >= 2 and len(content) >= 20:
        add_signal("isolated_number_fragments", 2, plain_numbers[:8])

    fragment_lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and FRAGMENT_LINE_RE.fullmatch(line.strip())
    ]
    if len(fragment_lines) >= 2 or (BROKEN_EQUAL_RE.search(content) and "\\frac" not in content):
        add_signal("fraction_like_broken_structure", 3, fragment_lines[:6] or ["spaced equality"])

    flattened_units = FLATTENED_UNIT_RE.findall(content)
    if flattened_units:
        add_signal("flattened_unit_exponent", 1, flattened_units[:8])

    fullwidth_punctuation = FULLWIDTH_MATH_PUNCT_RE.findall(content)
    if fullwidth_punctuation:
        add_signal("fullwidth_math_punctuation", 1, fullwidth_punctuation[:8])

    if FULLWIDTH_EQUATION_LABEL_RE.search(content):
        add_signal("orphan_equation_label", 2, FULLWIDTH_EQUATION_LABEL_RE.findall(content)[:4])

    # Keep explicit LaTeX structure visible for the density screen.  Removing
    # \\mathrm{...} labels is useful for token signals, but would otherwise
    # make a correctly formatted unit-heavy formula look flattened.
    structure_hits = _artifact_structure_hits(raw_content)
    if (
        len(content) >= 24
        and len(structure_hits) <= 2
        and re.search(r"[A-Za-z]", content)
        and re.search(r"\d", content)
    ):
        add_signal("low_latex_structure_density", 1, {"structure_hits": len(structure_hits)})

    tokens = MATH_TOKEN_RE.findall(content)
    token_kinds = ["letter" if token[0].isalpha() else "number" for token in tokens]
    transitions = sum(first != second for first, second in zip(token_kinds, token_kinds[1:]))
    if len(tokens) >= 7 and transitions >= 4 and len(structure_hits) <= 3:
        add_signal("dense_alternating_tokens", 2, {"tokens": tokens[:12], "transitions": transitions})

    score = sum(signal["score"] for signal in signals)
    return score, signals


def suspicious_formula_artifacts(markdown: str) -> list[dict[str, Any]]:
    """Report math spans that look like flattened text-layer extraction.

    This is a structural screen only. It deliberately does not decide whether
    a formula is physically correct and never rewrites the source expression.
    """

    findings: list[dict[str, Any]] = []
    for kind, content, offset in _math_matches(markdown):
        if kind != "display":
            continue
        score, signals = _math_artifact_score(content)
        if score < ARTIFACT_WARN_THRESHOLD:
            continue
        status = "FAIL" if score >= ARTIFACT_FAIL_THRESHOLD else "WARN"
        line_number = markdown.count("\n", 0, offset) + 1
        pdf_page = _nearest_pdf_page(markdown, offset)
        page_label = f"PDF page {pdf_page}" if pdf_page is not None else "the nearest source page"
        findings.append(
            {
                "status": status,
                "artifact_score": score,
                "signals": signals,
                "pdf_page": pdf_page,
                "line": line_number,
                "snippet": " ".join(content.split())[:200],
                "message": (
                    f"{page_label} math block appears to contain flattened extraction artifacts; "
                    "reopen the source page and visually verify it."
                ),
            }
        )
    return findings


def delimiter_errors(markdown: str) -> list[str]:
    errors: list[str] = []
    display_count = markdown.count("$$")
    if display_count % 2:
        errors.append(f"unbalanced display-math delimiters: {display_count} occurrences of $$")
    without_display = markdown.replace("$$", "")
    inline_count = len(re.findall(r"(?<!\\)\$(?!\$)", without_display))
    if inline_count % 2:
        errors.append(f"unbalanced inline-math delimiters: {inline_count} unescaped $ markers")
    for opening, closing, label in ((r"(?<!\\)\\\(", r"(?<!\\)\\\)", r"\\( \\)"), (r"(?<!\\)\\\[", r"(?<!\\)\\\]", r"\\[ \\]")):
        starts = len(re.findall(opening, markdown))
        ends = len(re.findall(closing, markdown))
        if starts != ends:
            errors.append(f"unbalanced {label} delimiters: {starts} open vs {ends} close")
    return errors


def _heading_warnings(markdown: str) -> list[str]:
    headings: list[tuple[int, str, int]] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        match = HEADING_RE.match(line)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip(), line_number))
    warnings: list[str] = []
    previous = 0
    for level, _title, line_number in headings:
        if previous and level > previous + 1:
            warnings.append(f"heading level jumps from H{previous} to H{level} at line {line_number}")
        previous = level
    if not headings:
        warnings.append("Markdown has no headings")
    return warnings


def _manifest_check(
    manifest_path: Path | None,
    markdown_path: Path,
    image_pairs: list[tuple[str, str]],
    pages: list[int],
) -> tuple[str, str, dict[str, Any]]:
    if manifest_path is None:
        return "PASS", "no optional manifest supplied", {"present": False}
    if not manifest_path.is_file():
        return "FAIL", f"manifest not found: {manifest_path}", {"present": False}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return "FAIL", f"manifest is not valid JSON: {exc}", {"present": False}
    problems: list[str] = []
    output_markdown = manifest.get("output_markdown")
    if output_markdown and Path(output_markdown).resolve() != markdown_path.resolve():
        problems.append("manifest output_markdown does not point to the validated Markdown")
    if "images" in manifest and manifest["images"] != len(image_pairs):
        problems.append(f"manifest images={manifest['images']} but Markdown has {len(image_pairs)} local image references")
    manifest_pages = manifest.get("pdf_pages")
    if isinstance(manifest_pages, list) and pages and [int(value) for value in manifest_pages] != pages:
        problems.append("manifest pdf_pages differs from Markdown page markers")
    status = "FAIL" if problems else "PASS"
    return status, "; ".join(problems) if problems else "optional manifest is consistent", {
        "present": True,
        "problems": problems,
        "manifest": manifest,
    }


def validate_markdown(
    markdown_path: Path,
    *,
    expected_pages: Iterable[int] | None = None,
    expected_printed_pages: tuple[int, int] | None = None,
    manifest_path: Path | None = None,
    forbidden: Iterable[str] = (),
    require_alt_stem: bool = False,
    require_printed_pages: bool = False,
) -> dict[str, Any]:
    """Return a JSON-serializable PASS/WARN/FAIL report."""

    markdown_path = markdown_path.resolve()
    markdown = markdown_path.read_text(encoding="utf-8")
    expected = list(expected_pages) if expected_pages is not None else None
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    image_pairs = _image_matches(markdown)
    refs = [reference for _alt, reference in image_pairs]
    missing: list[str] = []
    outside: list[str] = []
    empty_alt: list[str] = []
    alt_mismatch: list[str] = []
    root = markdown_path.parent.resolve()
    for alt, reference in image_pairs:
        if not alt.strip():
            empty_alt.append(reference)
        try:
            target = (root / reference).resolve()
            target.relative_to(root)
        except ValueError:
            outside.append(reference)
            continue
        if not target.is_file():
            missing.append(reference)
        if require_alt_stem and Path(reference).stem != alt.strip():
            alt_mismatch.append(reference)
    if missing:
        errors.append(f"{len(missing)} referenced image(s) are missing")
        _check(checks, "images.exists", "FAIL", "referenced image(s) are missing", paths=missing)
    else:
        _check(checks, "images.exists", "PASS", "all local image references exist", count=len(refs))
    if outside:
        errors.append(f"{len(outside)} image reference(s) escape the Markdown directory")
        _check(checks, "images.path_safety", "FAIL", "image paths escape the Markdown directory", paths=outside)
    else:
        _check(checks, "images.path_safety", "PASS", "image paths stay inside the Markdown directory")
    duplicate_refs = sorted(reference for reference, count in Counter(refs).items() if count > 1)
    if duplicate_refs:
        warnings.append(f"{len(duplicate_refs)} image path(s) are referenced more than once")
        _check(checks, "images.duplicates", "WARN", "duplicate image references found", paths=duplicate_refs)
    else:
        _check(checks, "images.duplicates", "PASS", "no duplicate image references")
    if empty_alt or alt_mismatch:
        details = empty_alt + alt_mismatch
        warnings.append(f"{len(details)} image alt/path issue(s) need review")
        _check(checks, "images.alt", "WARN", "image alt text is incomplete or unstable", paths=details)
    else:
        _check(checks, "images.alt", "PASS", "image alt text is present")

    referenced_files = {
        (root / reference).resolve()
        for reference in refs
        if (root / reference).resolve().is_file()
    }
    all_assets = {
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    }
    orphan_assets = sorted(str(path.relative_to(root)) for path in all_assets - referenced_files)
    if orphan_assets:
        warnings.append(f"{len(orphan_assets)} visual asset(s) are not referenced by Markdown")
        _check(checks, "images.orphans", "WARN", "orphan visual assets found", paths=orphan_assets)
    else:
        _check(checks, "images.orphans", "PASS", "no orphan visual assets")

    marker_matches = list(PAGE_RE.finditer(markdown))
    pages = [int(match.group(1)) for match in marker_matches]
    printed_pages: list[int | None] = [
        int(match.group(2)) if match.group(2) and match.group(2) != "unknown" else None
        for match in marker_matches
    ]
    if expected is not None:
        if pages == expected:
            _check(checks, "pages.coverage", "PASS", "page markers exactly cover the requested pages", pages=pages)
        else:
            errors.append(f"page markers {pages} do not match requested pages {expected}")
            _check(checks, "pages.coverage", "FAIL", "page marker coverage mismatch", actual=pages, expected=expected)
    elif pages:
        _check(checks, "pages.coverage", "PASS", "PDF page markers are present", count=len(pages))
    else:
        warnings.append("no PDF page markers found")
        _check(checks, "pages.coverage", "WARN", "no PDF page markers found")
    duplicate_pages = sorted(page for page, count in Counter(pages).items() if count > 1)
    if duplicate_pages:
        errors.append(f"duplicate page markers: {duplicate_pages}")
        _check(checks, "pages.duplicates", "FAIL", "duplicate PDF page markers found", pages=duplicate_pages)
    else:
        _check(checks, "pages.duplicates", "PASS", "no duplicate PDF page markers")
    if expected is not None and pages:
        gaps = sorted(set(expected) - set(pages))
        if gaps:
            warnings.append(f"page marker gaps detected: {gaps}")
            _check(checks, "pages.continuity", "WARN", "page marker gaps detected", gaps=gaps)
        else:
            _check(checks, "pages.continuity", "PASS", "page marker continuity is acceptable")

    known_printed = [value for value in printed_pages if value is not None]
    if require_printed_pages and len(known_printed) != len(printed_pages):
        warnings.append(f"{len(printed_pages) - len(known_printed)} printed page marker(s) are unknown")
        _check(checks, "pages.printed", "WARN", "printed page mapping is incomplete")
    elif len(known_printed) >= 2:
        transitions = [
            (first, second)
            for first, second in zip(known_printed, known_printed[1:])
            if second != first + 1
        ]
        if transitions:
            warnings.append("printed page numbers are not continuous where known")
            _check(checks, "pages.printed", "WARN", "printed page continuity needs review", transitions=transitions)
        else:
            _check(checks, "pages.printed", "PASS", "printed page sequence is continuous where known")
    else:
        _check(checks, "pages.printed", "PASS", "printed page mapping is optional or insufficient to check")
    if expected_printed_pages is not None:
        if known_printed and known_printed[0] == expected_printed_pages[0] and known_printed[-1] == expected_printed_pages[1]:
            _check(checks, "pages.printed_range", "PASS", "printed page range matches the requested range")
        else:
            errors.append(f"printed page range does not match requested range {list(expected_printed_pages)}")
            _check(checks, "pages.printed_range", "FAIL", "printed page range mismatch", actual=known_printed)

    math_errors = delimiter_errors(markdown)
    if math_errors:
        errors.extend(math_errors)
        _check(checks, "math.delimiters", "FAIL", "LaTeX delimiters are unbalanced", errors=math_errors)
    else:
        _check(checks, "math.delimiters", "PASS", "LaTeX delimiters are balanced")
    suspicious_math = suspicious_math_blocks(markdown)
    if suspicious_math:
        errors.append(f"{len(suspicious_math)} math block(s) contain likely Chinese prose")
        _check(
            checks,
            "math.chinese_prose",
            "FAIL",
            "math block(s) contain likely Chinese prose; visually recheck the source page",
            blocks=suspicious_math,
        )
    else:
        _check(checks, "math.chinese_prose", "PASS", "no obvious Chinese prose inside math")
    artifact_findings = suspicious_formula_artifacts(markdown)
    artifact_failures = [finding for finding in artifact_findings if finding["status"] == "FAIL"]
    artifact_pages = sorted(
        {finding["pdf_page"] for finding in artifact_findings if finding["pdf_page"] is not None}
    )
    if artifact_findings:
        if artifact_failures:
            errors.append(
                f"{len(artifact_failures)} high-confidence formula extraction artifact(s) require visual recheck"
            )
            artifact_status = "FAIL"
        else:
            warnings.append(
                f"{len(artifact_findings)} formula block(s) may contain extraction artifacts and require visual recheck"
            )
            artifact_status = "WARN"
        _check(
            checks,
            "math.extraction_artifact",
            artifact_status,
            "reopen each flagged source page and visually verify the formula; validator does not repair formulas",
            findings=artifact_findings,
            pdf_pages=artifact_pages,
        )
    else:
        _check(checks, "math.extraction_artifact", "PASS", "no high-probability formula extraction artifacts")

    heading_warnings = _heading_warnings(markdown)
    if heading_warnings:
        warnings.extend(heading_warnings)
        _check(checks, "markdown.headings", "WARN", "Markdown heading structure needs review", details=heading_warnings)
    else:
        _check(checks, "markdown.headings", "PASS", "Markdown heading structure is present")
    garbage = suspicious_garbage(markdown)
    if garbage:
        errors.append("suspicious Unicode garbage requires visual recheck: " + ", ".join(garbage))
        _check(checks, "text.garbage", "FAIL", "suspicious Unicode garbage found; recheck the source page", patterns=garbage)
    else:
        _check(checks, "text.garbage", "PASS", "no obvious extraction garbage")
    note_count, note_ratio = _transcription_note_stats(markdown, len(pages))
    if note_count >= 10 and (note_ratio >= 0.20 or note_count >= 25):
        warnings.append(
            f"excessive unresolved transcription notes: {note_count} note(s) across {len(pages)} page marker(s)"
        )
        _check(
            checks,
            "notes.excessive",
            "WARN",
            "excessive unresolved transcription notes; resolve visually when possible",
            count=note_count,
            page_ratio=round(note_ratio, 4),
        )
    else:
        _check(
            checks,
            "notes.excessive",
            "PASS",
            "transcription note volume is not excessive",
            count=note_count,
            page_ratio=round(note_ratio, 4),
        )
    for value in forbidden:
        if value in markdown:
            errors.append(f"forbidden residual text found: {value!r}")
            _check(checks, "text.forbidden", "FAIL", "forbidden residual text found", value=value)
    if not any(check["id"] == "text.forbidden" and check["status"] == "FAIL" for check in checks):
        _check(checks, "text.forbidden", "PASS", "no forbidden residual text found")

    manifest_status, manifest_message, manifest_details = _manifest_check(
        manifest_path, markdown_path, image_pairs, pages
    )
    if manifest_status == "FAIL":
        errors.append(manifest_message)
    _check(checks, "manifest.consistency", manifest_status, manifest_message, **manifest_details)

    status = "FAIL" if errors else "WARN" if warnings else "PASS"
    return {
        "schema_version": 2,
        "status": status,
        "markdown": str(markdown_path),
        "summary": {
            "image_references": len(refs),
            "unique_image_references": len(set(refs)),
            "orphan_assets": len(orphan_assets),
            "page_markers": len(pages),
            "inline_math_markers": len(re.findall(r"(?<!\\)\$(?!\$)", markdown.replace("$$", ""))),
            "display_math_blocks": markdown.count("$$") // 2,
            "suspicious_math_blocks": len(suspicious_math),
            "suspicious_formula_artifact": len(artifact_findings),
            "transcription_notes": note_count,
            "suspicious_garbage": len(garbage),
            "headings": sum(1 for line in markdown.splitlines() if HEADING_RE.match(line)),
            "printed_page_check_required": require_printed_pages,
        },
        "pages": pages,
        "printed_pages": printed_pages,
        "missing_images": missing,
        "orphan_assets": orphan_assets,
        "duplicate_image_references": duplicate_refs,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


def _expected_pages(args: argparse.Namespace) -> list[int] | None:
    if args.page_start is None and args.page_end is None:
        return None
    if args.page_start is None or args.page_end is None or args.page_start > args.page_end:
        raise ValueError("--page-start and --page-end must be supplied together in ascending order")
    return list(range(args.page_start, args.page_end + 1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--page-start", type=int)
    parser.add_argument("--page-end", type=int)
    parser.add_argument("--printed-page-start", type=int)
    parser.add_argument("--printed-page-end", type=int)
    parser.add_argument("--forbid", action="append", default=[])
    parser.add_argument("--require-alt-stem", action="store_true")
    parser.add_argument("--require-printed-pages", action="store_true")
    parser.add_argument("--strict", action="store_true", help="return non-zero for WARN as well as FAIL")
    args = parser.parse_args()
    path = args.markdown.resolve()
    if not path.is_file():
        print(f"ERROR: Markdown file not found: {path}", file=sys.stderr)
        return 2
    try:
        if (args.printed_page_start is None) != (args.printed_page_end is None):
            raise ValueError("--printed-page-start and --printed-page-end must be supplied together")
        report = validate_markdown(
            path,
            expected_pages=_expected_pages(args),
            expected_printed_pages=(args.printed_page_start, args.printed_page_end)
            if args.printed_page_start is not None
            else None,
            manifest_path=args.manifest.resolve() if args.manifest else None,
            forbidden=args.forbid,
            require_alt_stem=args.require_alt_stem,
            require_printed_pages=args.require_printed_pages,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        args.json.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.json.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"markdown: {path}")
    print(f"status: {report['status']}")
    print(f"image references: {report['summary']['image_references']}")
    print(f"page markers: {report['summary']['page_markers']}")
    return 0 if report["status"] == "PASS" or (report["status"] == "WARN" and not args.strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
