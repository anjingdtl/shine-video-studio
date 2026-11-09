from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "backend" / "tests"
RUNS = ROOT / "work" / "test-refactor" / "runs"


def git_names(*args: str) -> set[str]:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        return set()
    return {line.replace("\\", "/").strip() for line in result.stdout.splitlines() if line.strip()}


def changed_paths(base_ref: str | None) -> set[str] | None:
    base = base_ref or "origin/main"
    merge_base = subprocess.run(
        ["git", "merge-base", "HEAD", base], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if merge_base.returncode:
        return None
    base_commit = merge_base.stdout.strip()
    changed = git_names("diff", "--name-only", f"{base_commit}...HEAD")
    changed |= git_names("diff", "--name-only")
    changed |= git_names("diff", "--cached", "--name-only")
    changed |= git_names("ls-files", "--others", "--exclude-standard")
    return changed


def _words(value: str) -> set[str]:
    return {
        word.casefold()
        for word in re.split(r"[^A-Za-z0-9]+", value)
        if len(word) > 1 and word.casefold() not in {
            "backend", "shine", "tests", "test", "api", "service", "services",
            "base", "common", "utils", "models", "model", "schemas", "schema",
            "engine", "core", "routes", "router", "repository", "repositories",
            "manager", "controller", "__init__",
        }
    }


def quick_selection(paths: set[str] | None, args) -> tuple[list[str], str, str]:
    """Return test targets, selection breadth, and a concise reason for the report."""
    if args.no_diff:
        return [], "unit", "explicit full L1 unit suite"
    if paths is None:
        return [], "unit", "Git base unavailable; running the full L1 unit suite"
    if not paths:
        return [], "unit", "no changed paths; running the full L1 unit suite"

    targets: set[str] = set()
    broad_reasons = []
    uncertain = False
    test_files = sorted(TESTS.glob("test_*.py"))

    for relative in sorted(paths):
        path = Path(relative)
        if relative.startswith("backend/tests/test_") and path.suffix == ".py":
            if (ROOT / path).is_file():
                targets.add(path.as_posix())
            continue
        if relative == "backend/tests/conftest.py":
            broad_reasons.append("shared pytest fixture or collection policy changed")
            continue
        if relative in {"backend/pyproject.toml", "backend/tests/__init__.py"}:
            broad_reasons.append(f"test configuration or shared test package changed: {relative}")
            continue
        if relative == "scripts/run_tests.py":
            contract_test = "backend/tests/test_test_tiers.py"
            if (ROOT / contract_test).is_file():
                targets.add(contract_test)
            continue

        source_path = path
        if relative.startswith("backend/shine/"):
            source_path = Path(*path.parts[2:])
            if source_path.name in {"main.py", "models.py", "services.py", "db.py", "config.py"} or relative in {
                "backend/shine/comfyops/deployment.py",
                "backend/shine/comfyops/installation.py",
                "backend/shine/comfyops/instances.py",
                "backend/shine/comfyops/host_env.py",
            }:
                broad_reasons.append(f"cross-module contract changed: {relative}")
                continue
            if "security" in relative.casefold() or "auth" in relative.casefold():
                broad_reasons.append(f"security boundary changed: {relative}")
                continue
            if "quality" in relative.casefold() or "production" in relative.casefold():
                broad_reasons.append(f"cross-module workflow changed: {relative}")
                continue
        elif not relative.startswith(("scripts/", "backend/")):
            # Docs and frontend changes still run the complete L1 unit suite.
            continue

        words = _words("_".join(source_path.parts))
        matches = [
            file for file in test_files
            if words & _words(file.stem)
        ]
        if matches:
            targets.update(file.relative_to(ROOT).as_posix() for file in matches)
        elif relative.startswith(("backend/shine/", "scripts/")):
            uncertain = True

    if broad_reasons:
        return [], "integration", "; ".join(broad_reasons)
    if uncertain:
        return sorted(targets), "unit", "module mapping uncertain; running all L1 unit tests and known targets"
    if len(targets) > 24:
        return [], "integration", f"{len(targets)} candidate modules; expanding to the complete integration tier"
    if targets:
        return sorted(targets), "unit", f"{len(targets)} changed-module test file(s) plus all L1 unit tests"
    return [], "unit", "no backend test mapping; running the full L1 unit suite"


def log_process(label: str, argv: list[str], log_path: Path, *, env=None) -> tuple[int, float]:
    started = time.monotonic()
    print(f"\n=== {label} ===", flush=True)
    print("$ " + subprocess.list2cmdline(argv), flush=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(
            argv,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
        code = process.wait()
    elapsed = time.monotonic() - started
    print(f"[{label}] exit={code} elapsed={elapsed:.1f}s log={log_path}", flush=True)
    return code, elapsed


def parse_junit(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"available": False}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {"available": False}
    cases = list(root.iter("testcase"))
    failures = []
    skipped = 0
    errors = 0
    for case in cases:
        failure = case.find("failure")
        error = case.find("error")
        skip = case.find("skipped")
        if failure is not None:
            failures.append((case, failure))
        elif error is not None:
            failures.append((case, error))
            errors += 1
        elif skip is not None:
            skipped += 1
    return {
        "available": True,
        "collected": len(cases),
        "passed": len(cases) - len(failures) - skipped,
        "failed": len(failures) - errors,
        "errors": errors,
        "skipped": skipped,
        "failures": failures,
    }


def failure_category(message: str) -> str:
    text = message.casefold()
    if any(token in text for token in ("database is locked", "operationalerror", "sqlite busy")):
        return "database contention"
    if any(token in text for token in ("timeoutexception", "timeouterror", "timed out", "timeout expired")):
        return "timeout or wait"
    if any(token in text for token in ("filenotfounderror", "no such file or directory", "winerror 3")):
        return "path or environment"
    if any(token in text for token in ("importerror", "modulenotfounderror", "no module named")):
        return "dependency or import"
    if any(token in text for token in ("winerror", "job object", "powershell")):
        return "operating-system integration"
    if "assertionerror" in text or "assert " in text:
        return "assertion or behavior"
    return "test/runtime failure"


def tier_collection_counts(log_path: Path) -> tuple[int | None, int | None, int | None]:
    if not log_path.exists():
        return None, None, None
    pattern = re.compile(r"\[shine-tier\] total=(\d+) selected=(\d+) deselected=(\d+)")
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.search(line)
        if match:
            return tuple(int(value) for value in match.groups())
    return None, None, None


def print_test_summary(tier: str, junit_path: Path, pytest_log: Path, total_elapsed: float) -> bool:
    stats = parse_junit(junit_path)
    print(f"\n=== {tier.upper()} test summary ===")
    if not stats["available"]:
        print("Collection/execution summary unavailable: pytest did not produce a readable JUnit report.")
        print(f"Elapsed: {total_elapsed:.1f}s")
        return False
    total, selected, deselected = tier_collection_counts(pytest_log)
    if total is None:
        total = stats["collected"]
        selected = stats["collected"]
        deselected = 0
    executed = stats["passed"] + stats["failed"] + stats["errors"]
    print(f"All collected: {total} | Selected: {selected} | Deselected by tier: {deselected}")
    print(
        f"Executed: {executed} | PASS: {stats['passed']} | "
        f"FAIL: {stats['failed'] + stats['errors']} | SKIP: {stats['skipped']}"
    )
    print(f"Elapsed: {total_elapsed:.1f}s")
    failures = stats["failures"]
    if failures:
        groups = Counter(failure_category(element.attrib.get("message", "") + (element.text or ""))
                         for _, element in failures)
        print("Failure attribution (from exception evidence): " + ", ".join(
            f"{name}={count}" for name, count in sorted(groups.items())
        ))
        for case, element in failures[:20]:
            node = "::".join(part for part in (case.attrib.get("classname"), case.attrib.get("name")) if part)
            first_line = (element.text or element.attrib.get("message", "")).strip().splitlines()
            detail = first_line[0][:260] if first_line else element.attrib.get("message", "")[:260]
            print(f"  {node}: {detail}")
    else:
        print("Failure attribution: no failed test cases.")

    return bool(
        stats["passed"] > 0
        and stats["failed"] == 0
        and stats["errors"] == 0
    )


def print_slowest_tests(log_path: Path, limit: int) -> None:
    duration = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s+(setup|call|teardown)\s+(.+?)\s*$")
    rows = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = duration.match(line)
            if match:
                rows.append((float(match.group(1)), match.group(2), match.group(3)))
    print(f"Slowest {min(limit, len(rows))} test phases:")
    for seconds, phase, nodeid in sorted(rows, reverse=True)[:limit]:
        print(f"  {seconds:8.2f}s {phase:8s} {nodeid}")
    if not rows:
        print("  No duration rows were reported by pytest.")


def print_slowest_modules(log_path: Path, limit: int = 20) -> None:
    pattern = re.compile(r"^\[shine-module\] (\d+(?:\.\d+)?)s phases=(\d+) cases=(\d+) (.+)$")
    rows = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = pattern.match(line.strip())
            if match:
                rows.append((float(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4)))
    print(f"Slowest {min(limit, len(rows))} test modules by cumulative phase time:")
    for seconds, phases, cases, module in rows[:limit]:
        print(f"  {seconds:8.2f}s {cases:4d} cases {phases:4d} phases {module}")
    if not rows:
        print("  No module-duration rows were reported by pytest.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Shine backend tests by tier.")
    parser.add_argument("tier", choices=("quick", "integration", "full", "profile"))
    parser.add_argument("--module", action="append", default=[], help="Limit integration to matching test module names.")
    parser.add_argument("--changed-from", default=None, help="Git ref for quick diff analysis (default: origin/main).")
    parser.add_argument(
        "--changed",
        action="store_true",
        help="For integration runs, use the Git diff to select mapped L2 files; uncertain changes run all L2.",
    )
    parser.add_argument("--no-diff", action="store_true", help="Run the complete L1 unit set without diff targeting.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    RUNS.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / f"{run_id}-{args.tier}"
    run_dir.mkdir(parents=True, exist_ok=True)
    junit_path = run_dir / "pytest.xml"
    pytest_log = run_dir / "pytest.log"
    overall_started = time.monotonic()
    failure_codes = []

    tier_label = {"quick": "L1 quick", "integration": "L2 integration", "full": "L3 backend full", "profile": "L3 profile"}[args.tier]
    print(f"Tier: {tier_label}")
    print(f"Repository: {ROOT}")
    print(f"Host platform: {sys.platform} ({os.name})")
    print(f"Report directory: {run_dir}")

    if args.tier in {"quick", "full"}:
        ruff_log = run_dir / "ruff.log"
        code, _ = log_process(
            "Ruff static checks",
            [sys.executable, "-m", "ruff", "check", "backend/shine", "backend/alembic", "backend/tests", "scripts/run_tests.py"],
            ruff_log,
        )
        if code:
            failure_codes.append(code)

    pytest_args = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        "backend/pyproject.toml",
        "backend/tests",
        "--shine-runner-tier",
        args.tier,
        "--junitxml",
        str(junit_path),
        "--durations=50" if args.tier == "profile" else "--durations=10",
        "-ra",
    ]
    if args.tier == "quick":
        invalid_modules = [
            module for module in args.module
            if not (TESTS / f"test_{module}.py").is_file()
        ]
        if invalid_modules:
            print("Unknown quick target module(s): " + ", ".join(invalid_modules))
            print("Expected a backend/tests/test_<module>.py file.")
            return 2
        changed = None if args.no_diff else changed_paths(args.changed_from)
        targets, scope, reason = quick_selection(changed, args)
        print(f"Diff-aware scope: {scope}; reason: {reason}")
        if changed is None:
            print("Changed paths: unavailable; the full L1 unit set will run.")
        else:
            print(f"Changed paths examined: {len(changed)}")
        pytest_args += ["--shine-quick-scope", scope]
        for target in targets:
            pytest_args += ["--shine-quick-target", target]
        for module in args.module:
            target = f"backend/tests/test_{module}.py"
            if (ROOT / target).is_file():
                pytest_args += ["--shine-quick-target", target]
    elif args.tier == "integration":
        if args.changed:
            changed = changed_paths(args.changed_from)
            targets, scope, reason = quick_selection(changed, args)
            safe_targeting = scope != "integration" and bool(targets) and "uncertain" not in reason
            print(f"Diff-aware L2 scope: {'changed modules' if safe_targeting else 'complete integration'}; reason: {reason}")
            if changed is None:
                print("Changed paths: unavailable; the complete L2 suite will run.")
            else:
                print(f"Changed paths examined: {len(changed)}")
            if safe_targeting:
                for target in targets:
                    pytest_args += ["--shine-integration-target", target]
        for module in args.module:
            pytest_args += ["--shine-module", module]

    test_code, _ = log_process("pytest", pytest_args, pytest_log)
    if test_code:
        failure_codes.append(test_code)

    if args.tier == "full":
        data_dir = run_dir / "alembic-data"
        data_dir.mkdir(parents=True, exist_ok=True)
        database_path = data_dir / "fresh.sqlite3"
        env = os.environ.copy()
        env["SHINE_DATA_DIR"] = str(data_dir)
        env["SHINE_DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
        alembic_log = run_dir / "alembic.log"
        code, _ = log_process(
            "Isolated Alembic upgrade",
            [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
            alembic_log,
            env=env,
        )
        if code:
            failure_codes.append(code)

    elapsed = time.monotonic() - overall_started
    pytest_ok = print_test_summary(tier_label, junit_path, pytest_log, elapsed)
    print_slowest_tests(pytest_log, 50 if args.tier == "profile" else 10)
    print_slowest_modules(pytest_log)
    print(f"Slow-test details: {pytest_log}")
    print(f"JUnit report: {junit_path}")
    print(f"Logs: {run_dir}")
    print("Not run by this host command: other operating system, real ComfyUI/GPU/model, and browser E2E; see the L3 CI jobs.")
    success = pytest_ok and not failure_codes
    print(f"Result: {'PASS' if success else 'FAIL'}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
