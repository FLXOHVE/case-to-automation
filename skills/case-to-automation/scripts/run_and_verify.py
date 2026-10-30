"""Run a reviewed native test command and verify this run's JUnit evidence.

Requires Python3.10+ stdlib, plus the target project's own test environment.
This is an optional JUnit adapter, not a business assertion engine or sandbox.
Commands execute only when explicitly supplied after --. Inspect target/side
effects first; a skill/tool invocation does not expand task authorization.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET


def contained(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path outside selected project: {path}")
    return resolved


def fingerprints(project: Path, files: list[str]) -> dict[str, str]:
    result = {}
    for name in files:
        path = contained(project, project / name)
        if not path.is_file():
            raise ValueError(f"Test source not found: {path}")
        result[path.relative_to(project).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def read_junit(path: Path) -> list[dict]:
    """Read actual testcase elements; suite totals alone cannot prove execution."""
    def tag(node):
        return node.tag.rsplit("}", 1)[-1]

    root = ET.parse(path).getroot()
    if tag(root) not in {"testsuites", "testsuite"}:
        raise ValueError("Not a JUnit testsuite/testsuites document")
    tests, case_statuses = [], {}
    for case in root.iter():
        if tag(case) != "testcase":
            continue
        events = [tag(child) for child in case]
        attrs = case.attrib
        name, classname = attrs.get("name", ""), attrs.get("classname", "")
        if not name:
            raise ValueError("JUnit testcase has no name")
        native_status = attrs.get("status", "").strip().lower()
        native_result = attrs.get("result", "").strip().lower()
        if "error" in events or "error" in {native_status, native_result}:
            status = "error"
        elif "failure" in events or {native_status, native_result} & {"fail", "failed", "failure"}:
            status = "failed"
        elif "skipped" in events or native_status in {"notrun", "disabled", "skipped"} or native_result in {"suppressed", "skipped"}:
            status = "skipped"
        elif native_status not in {"", "run", "passed", "success", "completed"} or native_result not in {"", "completed", "passed", "success"}:
            status = "unknown"
        else:
            status = "passed"
        tests.append({"id": f"{classname}::{name}" if classname else name,
                      "classname": classname, "name": name, "status": status})
        case_statuses[id(case)] = status
    # Suite setup/teardown failures may sit outside testcase elements. Do not
    # adopt green cases while silently discarding those failures or omissions.
    for suite in root.iter():
        if tag(suite) not in {"testsuite", "testsuites"}:
            continue
        suite_events = {tag(child) for child in suite} & {"error", "failure", "skipped"}
        if suite_events:
            raise ValueError("Suite-level result outside testcases: " + ", ".join(sorted(suite_events)))
        descendant_counts = Counter(case_statuses[id(case)] for case in suite.iter() if tag(case) == "testcase")
        for counter, status in (("errors", "error"), ("failures", "failed"), ("skipped", "skipped"), ("disabled", "skipped")):
            if counter not in suite.attrib:
                continue
            try:
                declared = int(suite.attrib[counter])
            except ValueError as exc:
                raise ValueError(f"Invalid suite {counter} counter") from exc
            if declared < 0 or declared > descendant_counts[status]:
                raise ValueError(f"Unaccounted suite {counter}: {declared}")
    return tests


def assess(returncode: int, tests: list[dict], expected_count: int,
           expected_tests: list[str], *, timed_out: bool = False,
           sources_changed: bool = False, report_error: str = "") -> dict:
    reasons = []
    if timed_out:
        reasons.append("native_command_timed_out")
    if returncode != 0:
        reasons.append(f"native_exit_code={returncode}")
    if report_error:
        reasons.append("junit_report_unavailable: " + report_error)
    if not tests:
        reasons.append("zero_reported_tests")
    if len(tests) != expected_count:
        reasons.append(f"test_count_mismatch: expected={expected_count}, actual={len(tests)}")
    actual_ids = {test["id"] for test in tests}
    if not expected_tests:
        reasons.append("expected_test_ids_not_declared")
    missing = sorted(set(expected_tests) - actual_ids)
    if missing:
        reasons.append("selected_tests_missing: " + ", ".join(missing))
    unexpected = sorted(actual_ids - set(expected_tests))
    if unexpected:
        reasons.append("unexpected_tests: " + ", ".join(unexpected))
    if expected_tests and Counter(test["id"] for test in tests) != Counter(expected_tests):
        reasons.append("selected_test_occurrences_differ")
    for status in ("failed", "error", "skipped", "unknown"):
        count = sum(test["status"] == status for test in tests)
        if count:
            reasons.append(f"{status}_tests={count}")
    if sources_changed:
        reasons.append("selected_sources_changed_during_run")
    return {"status": "verified_passed" if not reasons else "not_passed", "reasons": reasons,
            "counts": {"total": len(tests), **{status: sum(t["status"] == status for t in tests)
                      for status in ("passed", "failed", "error", "skipped", "unknown")}}}


def execute(project: Path, output: Path, command: list[str], *, source_files: list[str],
            expected_count: int, expected_tests: list[str], timeout: float = 300) -> dict:
    project = project.resolve()
    if not project.is_dir():
        raise ValueError("Project directory does not exist")
    output = contained(project, output)
    if output == project or not command or not source_files:
        raise ValueError("Provide a new run subdirectory, test source files and native command")
    if expected_count < 1 or len(expected_tests) != expected_count or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Provide every expected JUnit test ID (repeat occurrences); their count and timeout must be positive")
    before = fingerprints(project, source_files)
    # A new directory prevents old green evidence from being adopted accidentally.
    output.mkdir(parents=True, exist_ok=False)
    junit = output / "junit.xml"
    argv = [part.replace("{junit}", str(junit)).replace("{output_dir}", str(output)) for part in command]
    record = {"command": argv, "cwd": str(project), "sources_before": before,
              "expected_count": expected_count, "expected_tests": expected_tests,
              "junit": str(junit), "stdout_log": str(output / "native.log"),
              "started_at_epoch": time.time(), "timeout_s": timeout}
    timed_out, start_error, returncode = False, "", -1
    started = time.monotonic()
    try:
        with (output / "native.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(argv, cwd=project, stdout=log, stderr=subprocess.STDOUT,
                                       shell=False)
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                returncode = process.wait(timeout=10)
    except OSError as exc:
        start_error = f"{type(exc).__name__}: {exc}"
    tests, report_error = [], start_error
    try:
        if not junit.is_file() or junit.is_symlink():
            raise ValueError("Native command did not create a regular junit.xml in this new run directory")
        tests = read_junit(junit)
    except (OSError, ET.ParseError, ValueError) as exc:
        report_error = "; ".join(filter(None, [report_error, f"{type(exc).__name__}: {exc}"]))
    try:
        after = fingerprints(project, source_files)
    except (OSError, ValueError) as exc:
        after = {"error": str(exc)}
    record.update(assess(returncode, tests, expected_count, expected_tests, timed_out=timed_out,
                         sources_changed=before != after, report_error=report_error))
    record.update(returncode=returncode, timed_out=timed_out, duration_s=time.monotonic()-started,
                  sources_after=after, tests=tests,
                  scope="Native test execution evidence; business assertions and cleanup semantics require source review")
    if timed_out:
        record["recovery_note"] = "Native process stopped; verify any child processes/external effects using project recovery rules before retrying."
    (output / "verification.json").write_text(json.dumps(record, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New run directory inside project; never overwritten")
    parser.add_argument("--source", action="append", required=True, help="Selected source path relative to project; repeat as needed")
    parser.add_argument("--expected-count", required=True, type=int)
    parser.add_argument("--expected-test", action="append", required=True, help="Every exact JUnit classname::name (or name without classname); repeat for each selected test")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="After --: reviewed native argv; {junit} is the new XML output path")
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    output = args.output if args.output.is_absolute() else args.project / args.output
    try:
        result = execute(args.project, output, command, source_files=args.source,
                         expected_count=args.expected_count, expected_tests=args.expected_test, timeout=args.timeout)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Run verification could not start: {exc}\n")
    print(json.dumps({"status": result["status"], "counts": result["counts"],
                      "reasons": result["reasons"], "evidence": str(output.resolve() / "verification.json")}, ensure_ascii=False))
    return 0 if result["status"] == "verified_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
