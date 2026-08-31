#!/usr/bin/env python3
"""Validate an analysis-ready Markdown conversion.

The validator deliberately reports PASS/WARN/FAIL instead of treating an
exit code from a converter as proof of semantic quality. WARN is a usable
result for exploratory/sample conversions; ``--strict`` turns warnings into a
non-zero exit code for CI.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)")
PAGE_RE = re.compile(r"<!--\s*PDF page\s+(\d+)(?:\s*\|\s*printed page\s+(\d+|unknown))?\s*-->")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")
MATH_SIGNAL_RE = re.compile(r"(?:[=^]|[∑√∞±×÷]|\\(?:frac|sqrt|sum|int)|[α-ωΑ-Ω])")


def _image_matches(markdown: str) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for match in IMAGE_RE.finditer(markdown):
        alt = match.group(1) or ""
        ref = match.group(2) or match.group(3) or ""
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//)", ref, re.I):
            continue
        ref = unquote(ref.split("#", 1)[0].split("?", 1)[0])
        values.append((alt, ref))
    return values


def local_image_refs(markdown: str) -> list[str]:
    """Compatibility helper retained from the first version of the skill."""

    return [ref for _alt, ref in _image_matches(markdown)]


def delimiter_errors(markdown: str) -> list[str]:
    errors: list[str] = []
    display_count = markdown.count("$$")
    if display_count % 2:
        errors.append(f"unbalanced display-math delimiters: {display_count} occurrences of $$")
    without_display = markdown.replace("$$", "")
    single_count = len(re.findall(r"(?<!\\)\$(?!\$)", without_display))
    if single_count % 2:
        errors.append(f"unbalanced inline-math delimiters: {single_count} unescaped $ markers")
    paren_open = len(re.findall(r"(?<!\\)\\\(", markdown))
    paren_close = len(re.findall(r"(?<!\\)\\\)", markdown))
    if paren_open != paren_close:
        errors.append(f"unbalanced \\( \\) delimiters: {paren_open} open vs {paren_close} close")
    bracket_open = len(re.findall(r"(?<!\\)\\\[", markdown))
    bracket_close = len(re.findall(r"(?<!\\)\\\]", markdown))
    if bracket_open != bracket_close:
        errors.append(f"unbalanced \\[ \\] delimiters: {bracket_open} open vs {bracket_close} close")
    return errors


def _check(checks: list[dict[str, Any]], check_id: str, status: str, message: str, **details: Any) -> None:
    checks.append({"id": check_id, "status": status, "message": message, **details})


def _edge_repetition(markdown: str, page_count: int) -> list[str]:
    lines = []
    for raw in markdown.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or line.startswith(("#", "<!--", "!")) or line.startswith(">"):
            continue
        if re.fullmatch(r"[-_=~·•.]{3,}", line):
            continue
        if 3 <= len(line) <= 120:
            lines.append(line)
    counts = Counter(lines)
    threshold = max(3, math.ceil(max(2, page_count) * 0.5))
    return sorted(line for line, count in counts.items() if count >= threshold)


def _heading_checks(markdown: str) -> tuple[list[str], list[str], dict[str, int]]:
    warnings: list[str] = []
    empty: list[str] = []
    headings: list[tuple[int, str, int]] = []
    counts: Counter[str] = Counter()
    for index, raw in enumerate(markdown.splitlines()):
        match = HEADING_RE.match(raw)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        headings.append((level, title, index))
        counts[str(level)] += 1
        if not title:
            empty.append(f"line {index + 1}")
    previous_level = 0
    for level, _title, index in headings:
        if previous_level and level > previous_level + 1:
            warnings.append(f"heading level jumps from H{previous_level} to H{level} at line {index + 1}")
        previous_level = level
    lines = markdown.splitlines()
    for position, (_level, title, index) in enumerate(headings):
        end = headings[position + 1][2] if position + 1 < len(headings) else len(lines)
        content = [line.strip() for line in lines[index + 1 : end] if line.strip()]
        if not content:
            empty.append(f"{title or '<empty>'} at line {index + 1}")
    return warnings, empty, dict(counts)


def _manifest_check(
    manifest_path: Path | None,
    markdown_path: Path,
    refs: list[tuple[str, str]],
    pages: list[int],
) -> tuple[str, str, dict[str, Any]]:
    if manifest_path is None:
        return "WARN", "no conversion manifest supplied", {"present": False}
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
    if "images" in manifest and manifest["images"] != len(refs):
        problems.append(f"manifest images={manifest['images']} but Markdown has {len(refs)} local image references")
    manifest_pages = manifest.get("pdf_pages")
    if isinstance(manifest_pages, list) and pages and [int(value) for value in manifest_pages] != pages:
        problems.append("manifest pdf_pages differs from Markdown page markers")
    status = "FAIL" if problems else "PASS"
    message = "; ".join(problems) if problems else "manifest is consistent with Markdown references"
    return status, message, {"present": True, "problems": problems, "manifest": manifest}


def validate_markdown(
    markdown_path: Path,
    *,
    expected_pages: Iterable[int] | None = None,
    expected_printed_pages: tuple[int, int] | None = None,
    manifest_path: Path | None = None,
    forbidden: Iterable[str] = (),
    require_alt_stem: bool = False,
) -> dict[str, Any]:
    """Return a JSON-serializable validation report."""

    markdown_path = markdown_path.resolve()
    markdown = markdown_path.read_text(encoding="utf-8")
    expected = list(expected_pages) if expected_pages is not None else None
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    image_pairs = _image_matches(markdown)
    refs = [ref for _alt, ref in image_pairs]
    missing: list[str] = []
    outside: list[str] = []
    alt_empty: list[str] = []
    alt_mismatch: list[str] = []
    for alt, ref in image_pairs:
        if not alt.strip():
            alt_empty.append(ref)
        try:
            target = (markdown_path.parent / ref).resolve()
            target.relative_to(markdown_path.parent.resolve())
        except ValueError:
            outside.append(ref)
            continue
        if not target.is_file():
            missing.append(ref)
        if require_alt_stem and Path(ref).stem != alt.strip():
            alt_mismatch.append(ref)
    if missing:
        errors.append(f"{len(missing)} referenced image(s) are missing")
        _check(checks, "images.exists", "FAIL", "referenced images are missing", missing=missing)
    else:
        _check(checks, "images.exists", "PASS", "all local image references exist", count=len(refs))
    if outside:
        errors.append(f"{len(outside)} image reference(s) escape the Markdown output directory")
        _check(checks, "images.path_safety", "FAIL", "image paths escape output directory", paths=outside)
    else:
        _check(checks, "images.path_safety", "PASS", "image paths stay beside the Markdown file")
    duplicate_refs = sorted(ref for ref, count in Counter(refs).items() if count > 1)
    if duplicate_refs:
        warnings.append(f"{len(duplicate_refs)} image path(s) are referenced more than once")
        _check(checks, "images.duplicates", "WARN", "duplicate image references found", paths=duplicate_refs)
    else:
        _check(checks, "images.duplicates", "PASS", "no duplicate image references")
    if alt_empty:
        warnings.append(f"{len(alt_empty)} image(s) have empty alt text")
        _check(checks, "images.alt", "WARN", "all image references should have stable alt text", paths=alt_empty)
    elif require_alt_stem and alt_mismatch:
        warnings.append(f"{len(alt_mismatch)} alt/path stem mismatch(es)")
        _check(checks, "images.alt", "WARN", "alt text does not equal the referenced filename stem", paths=alt_mismatch)
    else:
        _check(checks, "images.alt", "PASS", "image alt text is present and stable")

    referenced_files: set[Path] = set()
    for ref in refs:
        candidate = (markdown_path.parent / ref).resolve()
        if candidate.is_file():
            referenced_files.add(candidate)
    all_assets = {
        path.resolve()
        for path in markdown_path.parent.rglob("*")
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".svg"}
    }
    orphan_assets = sorted(str(path.relative_to(markdown_path.parent.resolve())) for path in all_assets - referenced_files)
    if orphan_assets:
        warnings.append(f"{len(orphan_assets)} image asset(s) are not referenced by Markdown")
        _check(checks, "images.orphans", "WARN", "orphan visual assets found", paths=orphan_assets)
    else:
        _check(checks, "images.orphans", "PASS", "no orphan visual assets")
    full_page_assets = [ref for ref in refs if re.search(r"(?:^|/)(?:page|scan)[_-]?\d+", Path(ref).stem, re.I)]
    if full_page_assets and len(full_page_assets) > max(3, len(refs) * 0.5):
        warnings.append("a large fraction of visual assets look like full-page captures")
        _check(checks, "images.crop_quality", "WARN", "possible overuse of full-page screenshots", paths=full_page_assets)
    else:
        _check(checks, "images.crop_quality", "PASS", "no excessive full-page screenshot pattern detected")

    marker_matches = list(PAGE_RE.finditer(markdown))
    pages = [int(match.group(1)) for match in marker_matches]
    printed_pages: list[int | None] = [int(match.group(2)) if match.group(2) and match.group(2) != "unknown" else None for match in marker_matches]
    unassociated_images: list[str] = []
    for image_match in IMAGE_RE.finditer(markdown):
        ref = image_match.group(2) or image_match.group(3) or ""
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//)", ref, re.I):
            continue
        if not any(marker.start() < image_match.start() for marker in marker_matches):
            unassociated_images.append(unquote(ref.split("#", 1)[0].split("?", 1)[0]))
    if unassociated_images:
        warnings.append(f"{len(unassociated_images)} image reference(s) occur before any PDF page marker")
        _check(checks, "images.page_association", "WARN", "image/page association needs review", paths=unassociated_images)
    else:
        _check(checks, "images.page_association", "PASS", "image references are associated with a page marker")
    if expected is not None:
        if pages == expected:
            _check(checks, "pages.coverage", "PASS", "page markers exactly cover the requested pages", pages=pages)
        else:
            errors.append(f"page markers {pages} do not match requested pages {expected}")
            _check(checks, "pages.coverage", "FAIL", "page marker coverage mismatch", actual=pages, expected=expected)
    elif pages:
        _check(checks, "pages.coverage", "PASS", "page markers are present", count=len(pages))
    else:
        warnings.append("no PDF page markers found")
        _check(checks, "pages.coverage", "WARN", "no PDF page markers found")
    duplicate_pages = sorted(page for page, count in Counter(pages).items() if count > 1)
    if duplicate_pages:
        errors.append(f"duplicate page markers: {duplicate_pages}")
        _check(checks, "pages.duplicates", "FAIL", "duplicate PDF page markers found", pages=duplicate_pages)
    else:
        _check(checks, "pages.duplicates", "PASS", "no duplicate PDF page markers")
    if pages:
        unique_pages = sorted(set(pages))
        gaps = []
        for first, second in zip(unique_pages, unique_pages[1:]):
            if second > first + 1 and (expected is None or any(first < value < second for value in expected)):
                gaps.extend(range(first + 1, second))
        if gaps:
            warnings.append(f"page marker gaps detected: {gaps}")
            _check(checks, "pages.continuity", "WARN", "page markers are not continuous", gaps=gaps)
        else:
            _check(checks, "pages.continuity", "PASS", "page marker continuity is acceptable")
    if len([value for value in printed_pages if value is not None]) >= 2:
        numeric_printed = [value for value in printed_pages if value is not None]
        printed_gaps = [
            (numeric_printed[index], numeric_printed[index + 1])
            for index in range(len(numeric_printed) - 1)
            if numeric_printed[index + 1] != numeric_printed[index] + 1
        ]
        if printed_gaps:
            warnings.append("printed page numbers are not a continuous sequence; verify a section boundary or mapping")
            _check(checks, "pages.printed_continuity", "WARN", "printed page continuity needs review", transitions=printed_gaps)
        else:
            _check(checks, "pages.printed_continuity", "PASS", "printed page sequence is continuous where known")
    else:
        _check(checks, "pages.printed_continuity", "WARN", "printed page numbers are unknown or insufficient for continuity checking")
    if expected_printed_pages is not None:
        numeric_printed = [value for value in printed_pages if value is not None]
        if numeric_printed and numeric_printed[0] == expected_printed_pages[0] and numeric_printed[-1] == expected_printed_pages[1]:
            _check(checks, "pages.printed_range", "PASS", "printed page range matches the requested range", actual=[numeric_printed[0], numeric_printed[-1]])
        else:
            errors.append(
                f"printed page range {numeric_printed[:1] + numeric_printed[-1:] if numeric_printed else []} "
                f"does not match requested range {list(expected_printed_pages)}"
            )
            _check(checks, "pages.printed_range", "FAIL", "printed page range mismatch", actual=numeric_printed, expected=list(expected_printed_pages))

    formula_errors = delimiter_errors(markdown)
    if formula_errors:
        errors.extend(formula_errors)
        _check(checks, "formulas.delimiters", "FAIL", "LaTeX delimiters are unbalanced", errors=formula_errors)
    else:
        _check(
            checks,
            "formulas.delimiters",
            "PASS",
            "LaTeX delimiters are balanced",
            inline=len(re.findall(r"(?<!\\)\$(?!\$)", markdown.replace("$$", ""))) // 2,
            display=markdown.count("$$") // 2,
        )
    math_signals = len(MATH_SIGNAL_RE.findall(markdown))
    manifest_data: dict[str, Any] | None = None
    if manifest_path and manifest_path.is_file():
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest_data = None
    expected_formula_blocks = manifest_data.get("formula_blocks") if manifest_data else None
    if (expected_formula_blocks and expected_formula_blocks > 0 and math_signals == 0) or (pages and math_signals == 0 and len(markdown) > 3000):
        warnings.append("formula signal is present in the source/manifest but Markdown contains no math signal")
        _check(checks, "formulas.presence", "WARN", "formula presence may need review", math_signals=math_signals, expected=expected_formula_blocks)
    else:
        _check(checks, "formulas.presence", "PASS", "formula signal is not suspiciously absent", math_signals=math_signals)

    heading_warnings, empty_headings, heading_counts = _heading_checks(markdown)
    if heading_warnings:
        warnings.extend(heading_warnings)
        _check(checks, "markdown.heading_levels", "WARN", "heading hierarchy has jumps", details=heading_warnings)
    else:
        _check(checks, "markdown.heading_levels", "PASS", "heading hierarchy has no obvious jumps")
    if empty_headings:
        warnings.append(f"{len(empty_headings)} empty Markdown section(s)")
        _check(checks, "markdown.empty_sections", "WARN", "empty headings found", headings=empty_headings)
    else:
        _check(checks, "markdown.empty_sections", "PASS", "no empty Markdown sections")
    if not heading_counts:
        warnings.append("Markdown has no headings")
        _check(checks, "markdown.headings", "WARN", "no Markdown headings found")
    else:
        _check(checks, "markdown.headings", "PASS", "Markdown heading structure is present", counts=heading_counts)

    garbage_patterns: list[str] = []
    if "�" in markdown:
        garbage_patterns.append("replacement character U+FFFD")
    if PRIVATE_USE_RE.search(markdown):
        garbage_patterns.append("private-use character")
    if re.search(r"[^\w\s\u4e00-\u9fff.,!?;:()\[\]{}+=*/\\|<>%$#@&'\"`~—–×·•-]{8,}", markdown):
        garbage_patterns.append("long symbol-only run")
    if garbage_patterns:
        warnings.append("possible extraction garbage: " + ", ".join(garbage_patterns))
        _check(checks, "text.garbage", "WARN", "possible extraction garbage found", patterns=garbage_patterns)
    else:
        _check(checks, "text.garbage", "PASS", "no obvious extraction garbage")
    repeated = _edge_repetition(markdown, len(pages))
    if repeated:
        warnings.append(f"possible repeated headers/footers: {repeated[:5]}")
        _check(checks, "text.repeated_edges", "WARN", "repeated edge-like lines may pollute the text", lines=repeated)
    else:
        _check(checks, "text.repeated_edges", "PASS", "no obvious repeated header/footer pollution")

    forbidden_found = False
    for forbidden in forbidden:
        if forbidden in markdown:
            forbidden_found = True
            errors.append(f"forbidden residual text found: {forbidden!r}")
            _check(checks, "text.forbidden", "FAIL", "forbidden residual text found", value=forbidden)
    if not forbidden_found:
        _check(checks, "text.forbidden", "PASS", "no forbidden residual text found")

    manifest_status, manifest_message, manifest_details = _manifest_check(manifest_path, markdown_path, image_pairs, pages)
    if manifest_status == "FAIL":
        errors.append(manifest_message)
    elif manifest_status == "WARN":
        warnings.append(manifest_message)
    _check(checks, "manifest.consistency", manifest_status, manifest_message, **manifest_details)

    # A check-level WARN/FAIL must affect the aggregate status even when its
    # human-readable message was not added by a specialized branch above.
    for check in checks:
        if check["status"] == "FAIL" and check["message"] not in errors:
            errors.append(check["message"])
        elif check["status"] == "WARN" and check["message"] not in warnings:
            warnings.append(check["message"])

    status = "FAIL" if errors else "WARN" if warnings else "PASS"
    return {
        "schema_version": 1,
        "status": status,
        "markdown": str(markdown_path),
        "summary": {
            "image_references": len(refs),
            "unique_image_references": len(set(refs)),
            "orphan_assets": len(orphan_assets),
            "page_markers": len(pages),
            "inline_math_markers": len(re.findall(r"(?<!\\)\$(?!\$)", markdown.replace("$$", ""))),
            "display_math_blocks": markdown.count("$$") // 2,
            "headings": sum(heading_counts.values()),
            "formula_signal_count": math_signals,
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


def _expected_from_args(args: argparse.Namespace) -> list[int] | None:
    if args.page_start is None and args.page_end is None:
        return None
    if args.page_start is None or args.page_end is None or args.page_start > args.page_end:
        raise ValueError("--page-start and --page-end must be supplied together in ascending order")
    return list(range(args.page_start, args.page_end + 1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path, help="Markdown file to validate")
    parser.add_argument("--json", type=Path, help="write a machine-readable report")
    parser.add_argument("--manifest", type=Path, help="conversion_manifest.json")
    parser.add_argument("--page-start", type=int, help="expected first PDF page marker")
    parser.add_argument("--page-end", type=int, help="expected last PDF page marker")
    parser.add_argument("--printed-page-start", type=int, help="reserved for explicit printed-page checks")
    parser.add_argument("--printed-page-end", type=int, help="reserved for explicit printed-page checks")
    parser.add_argument("--forbid", action="append", default=[], help="literal residual string to reject; repeatable")
    parser.add_argument("--require-alt-stem", action="store_true", help="require alt text to equal the image filename stem")
    parser.add_argument("--strict", action="store_true", help="return failure for WARN as well as FAIL")
    args = parser.parse_args()
    path = args.markdown.resolve()
    if not path.is_file():
        print(f"ERROR: Markdown file not found: {path}", file=sys.stderr)
        return 2
    try:
        expected = _expected_from_args(args)
        if (args.printed_page_start is None) != (args.printed_page_end is None):
            raise ValueError("--printed-page-start and --printed-page-end must be supplied together")
        report = validate_markdown(
            path,
            expected_pages=expected,
            expected_printed_pages=(args.printed_page_start, args.printed_page_end)
            if args.printed_page_start is not None and args.printed_page_end is not None
            else None,
            manifest_path=args.manifest.resolve() if args.manifest else None,
            forbidden=args.forbid,
            require_alt_stem=args.require_alt_stem,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        destination = args.json.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"markdown: {path}")
    print(f"status: {report['status']}")
    print(f"image references: {report['summary']['image_references']} (unique: {report['summary']['unique_image_references']})")
    print(f"orphan assets: {report['summary']['orphan_assets']}")
    print(f"page markers: {report['summary']['page_markers']}")
    print(f"inline math markers: {report['summary']['inline_math_markers']}")
    print(f"display math blocks: {report['summary']['display_math_blocks']}")
    for error in report["errors"]:
        print(f"FAIL: {error}")
    for warning in report["warnings"]:
        print(f"WARN: {warning}")
    if args.json:
        print(f"report: {args.json.resolve()}")
    if report["status"] == "FAIL" or (args.strict and report["status"] == "WARN"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
