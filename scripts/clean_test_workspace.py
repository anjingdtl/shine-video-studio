r"""Clean the caller's test root by the one cleanup rule of the repository.

The rule is normative (``AGENTS.md`` §6.3) and this script is its only executor, so a
round's closing cleanup and a large-scale cleanup cannot drift apart.  Nothing about
this machine is hardcoded: the test root always comes from the caller.

    python scripts/clean_test_workspace.py --work-root <dir>            # dry run (the default)
    python scripts/clean_test_workspace.py --work-root <dir> --apply    # delete

The rule, per entry.  An entry is a top-level entry of the test root, or one round
directory below ``manual\`` or ``acceptance\`` (the two layout containers of
``AGENTS.md`` §6.1 are transparent):

1. Loose files at the top of an entry are evidence and are kept.
2. A pytest basetemp (children mostly ``test_*``) is removed wholesale, whatever its size.
3. A shard archive (children all ``bt-*``) loses its ``bt-*`` and keeps its ``chunk-*.log``.
4. A directory without any file is removed.
5. A directory whose on-disk size is below the threshold is light evidence and is kept.
6. A directory at or above the threshold is a data directory: its contents are deleted,
   except ``comfyops/{isolated-packages,models,setup}``, directories named
   ``evidence`` / ``browser-evidence`` / ``logs`` / ``handoff-history``, and directories
   that directly contain a real safetensors weight (kept and reported).
7. Junctions and symbolic links are removed as links only; their targets are never entered.

A real weight copy is never deleted here.  Relocate it to the model store by hand first.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import stat
import struct
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

MIB = 1024 * 1024
DEFAULT_MIN_BYTES = 100 * MIB
REAL_WEIGHT_MIN_BYTES = 20 * MIB
REPARSE_POINT = 0x400
DIRECTORY = 0x10
PROTECTED_TAILS = {("comfyops", "isolated-packages"), ("comfyops", "models"), ("comfyops", "setup")}
EVIDENCE_NAMES = {"evidence", "browser-evidence", "logs", "handoff-history"}
# Layout containers of AGENTS.md §6.1: transparent, their round directories are the entries.
CONTAINER_NAMES = {"manual", "acceptance"}
WINDOWS = sys.platform == "win32"

if WINDOWS:
    _kernel32 = ctypes.windll.kernel32
    _kernel32.GetCompressedFileSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    _kernel32.GetCompressedFileSizeW.restype = ctypes.c_uint32
    _kernel32.GetLastError.restype = ctypes.c_ulong


def _long(path: str) -> str:
    """Extended-length form, so deep trees do not hit MAX_PATH."""
    if not WINDOWS:
        return path
    full = os.path.abspath(path)
    return full if full.startswith("\\\\?\\") else "\\\\?\\" + full


def _short(path: str) -> str:
    return path[4:] if path.startswith("\\\\?\\") else path


def on_disk_size(path: str) -> int:
    """Bytes really allocated: sparse and compressed files report what they actually occupy."""
    try:
        if WINDOWS:
            high = ctypes.c_uint32(0)
            low = _kernel32.GetCompressedFileSizeW(path, ctypes.byref(high))
            if low == 0xFFFFFFFF and _kernel32.GetLastError():
                return 0
            return (high.value << 32) | low
        info = os.lstat(path)
        blocks = getattr(info, "st_blocks", None)
        return int(blocks) * 512 if blocks is not None else int(info.st_size)
    except OSError:
        return 0


def _is_dir(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & DIRECTORY) or stat.S_ISDIR(info.st_mode)


def _is_link(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT)


def _children(path: str) -> list[tuple[str, str, os.stat_result]]:
    """``(name, path, lstat)`` of one directory; links are listed but never entered."""
    try:
        with os.scandir(path) as entries:
            found = []
            for entry in entries:
                with contextlib.suppress(OSError):
                    found.append((entry.name, entry.path, entry.stat(follow_symlinks=False)))
            return found
    except OSError:
        return []


def _real_dirs(path: str) -> list[tuple[str, str, os.stat_result]]:
    return [child for child in _children(path) if _is_dir(child[2]) and not _is_link(child[2])]


def is_real_weight(path: str, size: int) -> bool:
    """A safetensors header followed by non-zero tensor bytes: a real model file, never test junk."""
    try:
        with open(path, "rb") as stream:
            head = stream.read(8)
            if len(head) < 8:
                return False
            header_len = struct.unpack("<Q", head)[0]
            if not 0 < header_len <= min(size - 8, 50_000_000) or header_len <= 2:
                return False
            header = json.loads(stream.read(header_len).decode("utf-8"))
            tensors = [v for v in header.values() if isinstance(v, dict)] if isinstance(header, dict) else []
            if not any("dtype" in tensor for tensor in tensors):
                return False
            body = 8 + header_len
            span = max(1, size - body)
            for quarter in (1, 2, 3):
                stream.seek(min(size - 1, body + span * quarter // 4))
                if any(stream.read(65536)):
                    return True
    except (OSError, ValueError):
        return False
    return False


@dataclass
class EntryReport:
    """What the rule decided for one top-level entry."""

    name: str
    size_before: int = 0
    deleted_bytes: int = 0
    deleted_files: int = 0
    actions: Counter = field(default_factory=Counter)
    removed_links: list[str] = field(default_factory=list)
    protected: list[tuple[str, int]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class WorkspaceCleaner:
    """Apply the cleanup rule to every top-level entry of one test root."""

    def __init__(self, root: Path | str, *, min_bytes: int = DEFAULT_MIN_BYTES,
                 real_weight_min_bytes: int = REAL_WEIGHT_MIN_BYTES, keep: Sequence[str] = (),
                 prune_links: bool = False) -> None:
        self.root = Path(root)
        self.min_bytes = int(min_bytes)
        self.real_weight_min_bytes = int(real_weight_min_bytes)
        self.keep = frozenset(keep)
        self.prune_links = bool(prune_links)
        self._sizes: dict[str, int] = {}

    def plan(self) -> list[EntryReport]:
        """Decide everything and delete nothing."""
        return self._run(apply=False)

    def apply(self) -> list[EntryReport]:
        return self._run(apply=True)

    # ---- the rule ----
    def _entries(self) -> Iterator[tuple[str, str]]:
        """``(name, path)`` of every entry; a link is never taken for a directory."""
        base = _long(str(self.root))
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if name in CONTAINER_NAMES and _is_dir(info) and not _is_link(info):
                for round_name in sorted(os.listdir(path)):
                    yield f"{name}/{round_name}", os.path.join(path, round_name)
            else:
                yield name, path

    def _run(self, *, apply: bool) -> list[EntryReport]:
        self._sizes.clear()
        reports = []
        for name, path in self._entries():
            if name in self.keep:
                reports.append(EntryReport(name=name, actions=Counter({"kept-by-name": 1})))
                continue
            try:
                info = os.lstat(path)
            except OSError:
                continue
            report = EntryReport(name=name)
            if not _is_dir(info) or _is_link(info):
                # rule 1: a loose file (or a loose link) at the top of an entry is left alone
                report.actions["kept-loose-file"] += 1
                report.size_before = 0 if _is_link(info) or _is_dir(info) else on_disk_size(path)
            else:
                report.size_before = self._scan(path)
                self._entry(path, report, apply=apply)
            reports.append(report)
        return reports

    def _entry(self, path: str, report: EntryReport, *, apply: bool) -> None:
        if self._is_basetemp(path):
            self._rm_tree(path, report, apply=apply)
            report.actions["removed-basetemp"] += 1
        elif basetemps := self._shard_basetemps(path):
            for basetemp in basetemps:
                self._rm_tree(basetemp, report, apply=apply)
            report.actions["removed-bt-kept-logs"] += 1
        elif self._is_empty_tree(path):
            self._rm_tree(path, report, apply=apply)
            report.actions["removed-empty-tree"] += 1
        else:
            for child_name, child, info in _children(path):
                if _is_link(info):
                    continue  # rule 7: a link in an entry that is kept only goes with --prune-links
                if _is_dir(info):
                    report.actions[self._dir(child, child_name, report, apply=apply)] += 1
                # rule 1: a loose file at the top of the entry is evidence
            if self.prune_links:
                self._prune_links(path, report, apply=apply)
        if apply:
            # an entry left without any file goes away with its contents
            with contextlib.suppress(OSError):
                os.rmdir(path)

    def _dir(self, path: str, name: str, report: EntryReport, *, apply: bool) -> str:
        """Decide one directory below an entry; return the action taken."""
        if self._is_basetemp(path):
            self._rm_tree(path, report, apply=apply)
            return "removed-basetemp"
        if basetemps := self._shard_basetemps(path):
            for basetemp in basetemps:
                self._rm_tree(basetemp, report, apply=apply)
            return "removed-bt-kept-logs"
        if self._is_empty_tree(path):
            self._rm_tree(path, report, apply=apply)
            return "removed-empty-tree"
        reason = self._keep_reason(path, name)
        if reason:
            report.protected.append((_short(path), self._sizes.get(path, 0)))
            return f"kept-{reason}"
        if self._sizes.get(path, 0) < self.min_bytes:
            return "kept-light"
        self._purge(path, report, apply=apply)
        return "purged-data-dir"

    def _purge(self, path: str, report: EntryReport, *, apply: bool) -> None:
        """Rule 6: a data directory loses its contents, except what the rule protects."""
        for child_name, child, info in _children(path):
            if _is_link(info):
                self._rm_link(child, info, report, apply=apply)
            elif _is_dir(info):
                reason = self._keep_reason(child, child_name)
                if reason:
                    report.protected.append((_short(child), self._sizes.get(child, 0)))
                else:
                    self._purge(child, report, apply=apply)
            else:
                self._rm_file(child, report, apply=apply)
        if apply:
            with contextlib.suppress(OSError):
                os.rmdir(path)

    def _keep_reason(self, path: str, name: str) -> str | None:
        parts = _short(path).replace("/", "\\").lower().split("\\")
        if any((parts[i], parts[i + 1]) in PROTECTED_TAILS for i in range(len(parts) - 1)):
            return "model-package-or-config"
        if name.lower() in EVIDENCE_NAMES:
            return "evidence-dir"
        if self._has_real_weight(path):
            return "real-weight"
        return None

    # ---- facts ----
    def _scan(self, path: str) -> int:
        """On-disk size of a tree (links are not followed), cached bottom-up."""
        total = 0
        for _, child, info in _children(path):
            if _is_link(info):
                continue
            total += self._scan(child) if _is_dir(info) else on_disk_size(child)
        self._sizes[path] = total
        return total

    def _is_basetemp(self, path: str) -> bool:
        dirs = _real_dirs(path)
        tests = [child for child in dirs if child[0].startswith("test_")]
        loose = [child for child in _children(path) if not _is_dir(child[2]) and not _is_link(child[2])]
        return bool(tests) and len(tests) == len(dirs) and all(child[0] == ".lock" for child in loose)

    def _shard_basetemps(self, path: str) -> list[str]:
        dirs = _real_dirs(path)
        basetemps = [child for child in dirs if child[0].startswith("bt-")]
        return [child[1] for child in basetemps] if basetemps and len(basetemps) == len(dirs) else []

    def _is_empty_tree(self, path: str) -> bool:
        for _, child, info in _children(path):
            if _is_link(info) or not _is_dir(info):
                return False
            if not self._is_empty_tree(child):
                return False
        return True

    def _has_real_weight(self, path: str) -> bool:
        for _, child, info in _children(path):
            if _is_link(info) or _is_dir(info):
                continue
            if info.st_size >= self.real_weight_min_bytes and is_real_weight(child, info.st_size):
                return True
        return False

    # ---- primitives ----
    def _rm_file(self, path: str, report: EntryReport, *, apply: bool) -> None:
        report.deleted_files += 1
        report.deleted_bytes += on_disk_size(path)
        if not apply:
            return
        for attempt in (1, 2):
            try:
                os.unlink(path)
                return
            except PermissionError:
                if attempt == 1:
                    with contextlib.suppress(OSError):
                        os.chmod(path, stat.S_IWRITE)
            except OSError as exc:
                report.errors.append(f"{_short(path)}: {exc}")
                return

    def _rm_link(self, path: str, info: os.stat_result, report: EntryReport, *, apply: bool) -> None:
        """Rule 7: only the link itself goes; the target is never read or entered."""
        report.removed_links.append(_short(path))
        if not apply:
            return
        try:
            if _is_dir(info):
                os.rmdir(path)
            else:
                os.unlink(path)
        except OSError as exc:
            report.errors.append(f"{_short(path)}: {exc}")

    def _rm_tree(self, path: str, report: EntryReport, *, apply: bool) -> None:
        stack = [(path, False)]
        while stack:
            directory, done = stack.pop()
            if done:
                if apply:
                    with contextlib.suppress(OSError):
                        os.rmdir(directory)
                continue
            stack.append((directory, True))
            for _, child, info in _children(directory):
                if _is_link(info):
                    self._rm_link(child, info, report, apply=apply)
                elif _is_dir(info):
                    stack.append((child, False))
                else:
                    self._rm_file(child, report, apply=apply)

    def _prune_links(self, path: str, report: EntryReport, *, apply: bool) -> None:
        for _, child, info in _children(path):
            if _is_link(info):
                self._rm_link(child, info, report, apply=apply)
            elif _is_dir(info):
                self._prune_links(child, report, apply=apply)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean a test root by the single cleanup rule (dry run by default).")
    parser.add_argument("--work-root", default=None, help="the test root to clean (fallback: SHINE_TEST_ROOT)")
    parser.add_argument("--apply", action="store_true", help="delete; the default only reports")
    parser.add_argument("--min-mib", type=int, default=DEFAULT_MIN_BYTES // MIB,
                        help="on-disk size from which a directory counts as a data directory")
    parser.add_argument("--keep", action="append", default=[],
                        help="top-level entry that is never touched (repeatable)")
    parser.add_argument("--prune-links", action="store_true",
                        help="also remove links found inside entries that are otherwise kept")
    args = parser.parse_args(argv)

    from shine_test_root import work_root  # the CLI is the only caller-facing side of this module

    cleaner = WorkspaceCleaner(work_root(args.work_root), min_bytes=args.min_mib * MIB,
                              keep=args.keep, prune_links=args.prune_links)
    reports = cleaner.apply() if args.apply else cleaner.plan()
    deleted = sum(report.deleted_bytes for report in reports)
    errors = sum(len(report.errors) for report in reports)
    print(f"模式: {'正式执行 (--apply)' if args.apply else '演练，未删除任何东西'}  测试根: {cleaner.root}")
    for report in reports:
        actions = "  ".join(f"{action}×{count}" for action, count in sorted(report.actions.items()))
        print(f"  {report.size_before / MIB:9.1f} MiB → 删 {report.deleted_bytes / MIB:9.1f} MiB"
              f"（文件 {report.deleted_files:6d}）  [{actions}]  {report.name}")
        for path, size in report.protected:
            print(f"            保留 {size / MIB:9.1f} MiB  {path}")
        for path in report.removed_links:
            print(f"            拆除链接（不进入目标）  {path}")
        for message in report.errors:
            print(f"            错误  {message}")
    print(f"合计: 删除 {deleted / MIB:.1f} MiB，错误 {errors} 个")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
