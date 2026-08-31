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
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)")
PAGE_RE = re.compile(r"<!--\s*PDF page\s+(\d+)(?:\s*\|\s*printed page\s+(\d+|unknown))?\s*-->")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}


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

    heading_warnings = _heading_warnings(markdown)
    if heading_warnings:
        warnings.extend(heading_warnings)
        _check(checks, "markdown.headings", "WARN", "Markdown heading structure needs review", details=heading_warnings)
    else:
        _check(checks, "markdown.headings", "PASS", "Markdown heading structure is present")
    garbage = []
    if "�" in markdown:
        garbage.append("replacement character U+FFFD")
    if PRIVATE_USE_RE.search(markdown):
        garbage.append("private-use character")
    if garbage:
        warnings.append("possible extraction garbage: " + ", ".join(garbage))
        _check(checks, "text.garbage", "WARN", "possible extraction garbage found", patterns=garbage)
    else:
        _check(checks, "text.garbage", "PASS", "no obvious extraction garbage")
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
