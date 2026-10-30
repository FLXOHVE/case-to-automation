"""Run only the repository's synthetic, local unittest demonstration."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "skills/case-to-automation/scripts/run_and_verify.py"
spec = importlib.util.spec_from_file_location("verification_helper", HELPER)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def run_demo(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = output / "synthetic-project"
    shutil.copytree(ROOT / "examples/task_list", project, ignore=shutil.ignore_patterns("__pycache__"))
    scenarios = {}
    expected_results = {
        "pass": (0, {"total": 2, "passed": 2, "failed": 0, "error": 0, "skipped": 0, "unknown": 0}),
        "fail": (1, {"total": 2, "passed": 0, "failed": 2, "error": 0, "skipped": 0, "unknown": 0}),
        "skip": (0, {"total": 2, "passed": 1, "failed": 0, "error": 0, "skipped": 1, "unknown": 0}),
        "zero": (0, {"total": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0, "unknown": 0}),
    }
    for scenario in ("pass", "fail", "skip", "zero"):
        expected = ["TaskListTests::test_complete_and_archive", "TaskListTests::test_reject_active_archive"]
        if scenario == "skip":
            expected[-1] = "TaskListTests::test_external_sync"
        command = [sys.executable, "-X", "utf8", "run_tests.py", "--scenario", scenario, "--junit", "{junit}"]
        record = verifier.execute(project, project / "runs" / scenario, command,
                                  source_files=["app.py", "run_tests.py", "cases.md"],
                                  expected_count=len(expected), expected_tests=expected, timeout=30)
        expected_verdict = "verified_passed" if scenario == "pass" else "not_passed"
        expected_exit, expected_counts = expected_results[scenario]
        scenarios[scenario] = {"expected_verdict": expected_verdict, "actual_verdict": record["status"],
                               "counts": record["counts"], "native_exit_code": record["returncode"],
                               "reasons": record["reasons"],
                               "verdict_matches": (record["status"] == expected_verdict
                                   and record["returncode"] == expected_exit and record["counts"] == expected_counts
                                   and not record["timed_out"]
                                   and not any("junit_report_unavailable" in reason or "sources_changed" in reason
                                               for reason in record["reasons"]))}
    summary = {"scope": "Synthetic native unittest execution only; no AI code generation or live product testing",
               "scenarios": scenarios, "demo_verified": all(row["verdict_matches"] for row in scenarios.values())}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("demo-output"))
    args = parser.parse_args()
    try:
        summary = run_demo(args.output)
    except (OSError, ValueError) as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0 if summary["demo_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
