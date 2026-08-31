#!/usr/bin/env python3
"""Small dependency-free helpers shared by the PDF-to-Markdown scripts."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Sequence


PAGE_TOKEN_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


def parse_pages(spec: str | None, total_pages: int) -> list[int]:
    """Return one-based PDF page numbers for a CLI range such as ``1-3,7``."""

    if total_pages < 1:
        return []
    if not spec:
        return list(range(1, total_pages + 1))

    result: list[int] = []
    for token in spec.split(","):
        match = PAGE_TOKEN_RE.match(token)
        if not match:
            raise ValueError(f"invalid page range token: {token!r}")
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if first < 1 or last < 1 or first > last:
            raise ValueError(f"invalid page range token: {token!r}")
        if first > total_pages:
            continue
        result.extend(range(first, min(last, total_pages) + 1))

    seen: set[int] = set()
    ordered: list[int] = []
    for page in result:
        if page not in seen:
            seen.add(page)
            ordered.append(page)
    if not ordered:
        raise ValueError(f"page range {spec!r} does not intersect the PDF")
    return sorted(ordered)


def compact_page_range(pages: Sequence[int]) -> str:
    """Format sorted one-based pages as ``1-3,7`` for a manifest."""

    if not pages:
        return ""
    values = sorted(set(pages))
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def normalize_line(value: str) -> str:
    """Normalize a line for repeated-header/footer comparison."""

    value = value.replace("\u00a0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"[\-_=~·•.]{3,}", "", value).strip()
    return value


def json_dump(data: Any, destination: Path | None = None) -> None:
    """Write UTF-8 JSON to a file or stdout without platform mojibake."""

    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if destination is None:
        print(text, end="")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def run_command(
    args: Sequence[str | os.PathLike[str]],
    *,
    timeout: int = 120,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a native helper with predictable UTF-8 decoding on Windows."""

    return subprocess.run(
        [os.fspath(value) for value in args],
        cwd=os.fspath(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def find_repeated_edge_lines(page_texts: Iterable[str], minimum_fraction: float = 0.4) -> set[str]:
    """Find short lines repeated at the top/bottom of many pages."""

    pages = list(page_texts)
    if len(pages) < 2:
        return set()
    counts: dict[str, int] = {}
    for text in pages:
        lines = [normalize_line(line) for line in text.splitlines() if normalize_line(line)]
        edge_lines = set(lines[:5] + lines[-5:])
        for line in edge_lines:
            if 2 <= len(line) <= 120:
                counts[line] = counts.get(line, 0) + 1
    threshold = max(2, int(len(pages) * minimum_fraction + 0.999))
    return {line for line, count in counts.items() if count >= threshold}


def strip_repeated_edge_lines(text: str, repeated: set[str]) -> str:
    """Remove only repeated lines occurring in the first/last five lines."""

    if not repeated:
        return text
    raw_lines = text.splitlines()
    candidate_indices = set(range(min(5, len(raw_lines))))
    candidate_indices.update(range(max(0, len(raw_lines) - 5), len(raw_lines)))
    kept: list[str] = []
    for index, line in enumerate(raw_lines):
        if index in candidate_indices and normalize_line(line) in repeated:
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def safe_stem(path: Path) -> str:
    """Return a readable output stem while avoiding empty/path-like names."""

    stem = path.stem.strip() or "document"
    return re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", stem)
