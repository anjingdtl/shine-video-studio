"""Preserve explicitly authored editorial timecodes across render segments."""

import re
from itertools import combinations

# H3 renders one segment of 4 to 15 seconds.  The editorial cut table is the
# user's own timing; the render segments are derived from it and must cover
# it exactly, so both boundaries come from the same two numbers.
MIN_SEGMENT_S = 4.0
MAX_SEGMENT_S = 15.0
MIN_SEGMENT_MS = int(MIN_SEGMENT_S * 1000)
MAX_SEGMENT_MS = int(MAX_SEGMENT_S * 1000)


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
    if not indices:
        # A table that has no three-segment grouping still has a legal
        # grouping in most cases: two editorial shots, or a short table, are
        # common.  The crew is served one grouping; without it the model is
        # asked to invent an arithmetic the server then rejects.
        indices = _fallback_indices(boundaries, count)
    return {
        "editorial_shot_count": count,
        "cut_starts_s": [boundary / 1000 for boundary in boundaries[:-1]],
        "total_s": boundaries[-1] / 1000,
        "segments": [
            {"first_shot": a + 1, "last_shot": b, "duration_s": (boundaries[b] - boundaries[a]) / 1000}
            for a, b in zip(indices, indices[1:], strict=False)
        ],
    }


def _fallback_indices(boundaries: list[int], count: int) -> tuple[int, ...]:
    """The best legal grouping when no three-segment grouping exists.

    The search minimises the longest render segment and prefers more segments
    when two groupings are equally balanced, so the result follows the
    editorial table as closely as H3's 4-to-15-second window allows.
    """
    memo: dict[tuple[int, int], tuple[int, tuple[int, ...]] | None] = {}

    def solve(segments: int, start: int):
        """The smallest achievable longest segment for shots[start:] in `segments` parts."""
        if start == count:
            return None
        if segments == 1:
            length = boundaries[count] - boundaries[start]
            if MIN_SEGMENT_MS <= length <= MAX_SEGMENT_MS:
                return (length, (count,))
            return None
        key = (segments, start)
        if key in memo:
            return memo[key]
        best = None
        for cut in range(start + 1, count):
            length = boundaries[cut] - boundaries[start]
            if length < MIN_SEGMENT_MS:
                continue
            if length > MAX_SEGMENT_MS:
                break
            rest = solve(segments - 1, cut)
            if rest is None:
                continue
            candidate = (max(length, rest[0]), (cut, *rest[1]))
            if best is None or candidate[0] < best[0]:
                best = candidate
        memo[key] = best
        return best

    candidates = []
    for segments in range(1, count + 1):
        result = solve(segments, 0)
        if result is not None:
            candidates.append((result[0], -segments, result[1]))
    if not candidates:
        return ()
    best = min(candidates)
    return (0, *best[2])


def editorial_shot_lengths(timing: dict) -> list[float]:
    """The length of every editorial shot, in table order."""
    starts = list(timing["cut_starts_s"]) + [timing["total_s"]]
    return [round(end - start, 3) for start, end in zip(starts, starts[1:], strict=False)]


def editorial_timing_blocker(timing: dict) -> dict | None:
    """Why an editorial cut table cannot become H3 render segments, if it cannot.

    Some cut tables have no legal grouping at all: a 1.7 second shot cannot be
    a render segment, and merging it with a neighbour produces a segment that
    is not 4 to 15 seconds.  Asking a model to satisfy such a table is asking
    the impossible, so the run is refused with the real reason instead of a
    contract rejection the model could never repair.
    """
    if timing.get("segments"):
        return None
    lengths = editorial_shot_lengths(timing)
    return {
        "code": "EDITORIAL_TIMING_UNGROUPABLE",
        "editorial_shot_count": timing["editorial_shot_count"],
        "editorial_shot_lengths_s": lengths,
        "total_s": timing["total_s"],
        "min_segment_s": MIN_SEGMENT_S,
        "max_segment_s": MAX_SEGMENT_S,
        "shortest_shot_s": min(lengths) if lengths else None,
        "longest_shot_s": max(lengths) if lengths else None,
    }


def editorial_timing_plan(timing: dict, beat_ids: list[str] | None = None) -> dict:
    """The exact render-segment recipe the crew must emit.

    The plan is computed, not negotiated: the server checks the director's
    plan against this same arithmetic, so giving the crew the finished
    numbers is the only way for the model and the server to agree.  When the
    beat ids are known they are resolved here, because a segment's opening
    beat and every internal cut are addressed by beat id.
    """
    if len(beat_ids or []) != timing["editorial_shot_count"]:
        beat_ids = None
    starts = timing["cut_starts_s"]
    segments = []
    for index, segment in enumerate(timing["segments"], 1):
        first, last = segment["first_shot"], segment["last_shot"]
        segment_start = starts[first - 1]
        cuts = []
        for shot_no in range(first + 1, last + 1):
            cuts.append({
                "editorial_shot": shot_no,
                "beat_id": beat_ids[shot_no - 1] if beat_ids else None,
                "start_s": round(starts[shot_no - 1] - segment_start, 3),
            })
        segments.append({
            "index": index,
            "editorial_shots": [first, last],
            "opening_beat_id": beat_ids[first - 1] if beat_ids else None,
            "beat_ids": [beat_ids[first - 1], beat_ids[last - 1]] if beat_ids else None,
            "duration_s": segment["duration_s"],
            "segment_start_s": segment_start,
            "cuts": cuts,
        })
    return {
        "total_s": timing["total_s"],
        "editorial_shot_count": timing["editorial_shot_count"],
        "editorial_shot_lengths_s": editorial_shot_lengths(timing),
        "segment_count": len(segments),
        "segments": segments,
        "beat_ids": list(beat_ids) if beat_ids else None,
    }
