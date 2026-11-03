"""Preserve explicitly authored editorial timecodes across render segments."""

import re
from itertools import combinations


def editorial_timing_preview(brief: str) -> dict | None:
    """Group an explicit cut table without changing its timestamps or creating jobs."""
    brief = brief.replace("\r\n", "\n")
    table = re.search(r"(?m)^\|\s*#\s*\|\s*切点\s*\|\s*时长\s*\|[^\n]*\n"
                      r"\|[-: |]+\n((?:\|[^\n]+(?:\n|$))+)", brief)
    if not table:
        return None
    boundaries = [0]
    for index, line in enumerate(table[1].splitlines(), 1):
        row = re.match(r"\|\s*(\d+)\s*\|\s*(\d{1,2}\.\d{1,3})\s*\|\s*(\d{1,2}\.\d{1,3})s\s*\|", line)
        if not row or int(row[1]) != index:
            return None
        start, length = (round(float(row[i]) * 1000) for i in (2, 3))
        if start != boundaries[-1] or length <= 0:
            return None
        boundaries.append(start + length)
    count = len(boundaries) - 1
    if not 1 <= count <= 100:
        return None
    options = []
    for first, second in combinations(range(1, count), 2):
        indices = (0, first, second, count)
        lengths = [boundaries[b] - boundaries[a] for a, b in zip(indices, indices[1:], strict=False)]
        if all(4000 <= length <= 15000 for length in lengths):
            options.append((max(lengths) - min(lengths), indices))
    indices = min(options)[1] if options else ()
    return {
        "editorial_shot_count": count,
        "cut_starts_s": [boundary / 1000 for boundary in boundaries[:-1]],
        "total_s": boundaries[-1] / 1000,
        "segments": [
            {"first_shot": a + 1, "last_shot": b, "duration_s": (boundaries[b] - boundaries[a]) / 1000}
            for a, b in zip(indices, indices[1:], strict=False)
        ],
    }
