"""Offline regressions adapted to unittest from the upstream helper checks."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/case-to-automation"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("portable_runner", SKILL / "scripts/run_and_verify.py")
packager = load("portable_packager", SKILL / "scripts/package_skill.py")
demo = load("portable_demo", ROOT / "tools/demo.py")


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def read_xml(self, value):
        target = self.root / "junit.xml"
        target.write_text(value, encoding="utf-8")
        return runner.read_junit(target)

    def test_zero_test_suite_totals_are_not_execution(self):
        tests = self.read_xml('<testsuite tests="5" failures="0"/>')
        result = runner.assess(0, tests, 5, ["x"] * 5)
        self.assertEqual("not_passed", result["status"])
        self.assertIn("zero_reported_tests", result["reasons"])

    def test_native_failures_errors_and_skips_are_rejected(self):
        for event, expected in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
            with self.subTest(event=event):
                tests = self.read_xml(f'<testsuite><testcase name="x"><{event}/></testcase></testsuite>')
                self.assertEqual(expected, tests[0]["status"])
                self.assertEqual("not_passed", runner.assess(0, tests, 1, ["x"])["status"])

    def test_selected_ids_occurrences_exit_code_and_sources_must_match(self):
        tests = self.read_xml('<testsuite><testcase classname="settings" name="save"/></testsuite>')
        self.assertEqual("verified_passed", runner.assess(0, tests, 1, ["settings::save"])["status"])
        for count, selected in ((2, ["settings::save"]), (1, ["settings::other"]), (1, [])):
            self.assertEqual("not_passed", runner.assess(0, tests, count, selected)["status"])
        self.assertEqual("not_passed", runner.assess(2, tests, 1, ["settings::save"])["status"])
        self.assertEqual("not_passed", runner.assess(0, tests, 1, ["settings::save"], sources_changed=True)["status"])
        self.assertEqual("not_passed", runner.assess(0, tests * 2, 2, ["settings::save", "other"])["status"])

    def test_namespaces_and_unknown_status_fail_closed(self):
        tests = self.read_xml('<testsuites xmlns="urn:xunit"><testsuite><testcase name="x" status="mystery"/></testsuite></testsuites>')
        self.assertEqual("unknown", tests[0]["status"])
        self.assertEqual("not_passed", runner.assess(0, tests, 1, ["x"])["status"])

    def test_native_result_attributes_are_not_ignored(self):
        for value, status in (("failed", "failed"), ("error", "error"), ("unknown", "unknown"),
                              ("suppressed", "skipped"), ("completed", "passed")):
            with self.subTest(value=value):
                tests = self.read_xml(f'<testsuite><testcase name="x" result="{value}"/></testsuite>')
                self.assertEqual(status, tests[0]["status"])

    def test_suite_level_errors_and_unaccounted_counters_are_rejected(self):
        invalid = [
            '<testsuite><testcase name="x"/><error/></testsuite>',
            '<testsuites><testsuite><testcase name="x"/></testsuite><failure/></testsuites>',
            '<testsuite errors="1"><testcase name="x"/></testsuite>',
            '<testsuite failures="1"><testcase name="x"/></testsuite>',
            '<testsuite skipped="1"><testcase name="x"/></testsuite>',
            '<testsuite errors="unknown"><testcase name="x"/></testsuite>',
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read_xml(value)

    def test_nested_suite_counts_do_not_double_count_errors(self):
        tests = self.read_xml('<testsuites errors="1"><testsuite errors="1"><testcase name="x"><error/></testcase></testsuite></testsuites>')
        self.assertEqual(1, len(tests))
        self.assertEqual("error", tests[0]["status"])

    def test_missing_name_and_wrong_root_are_rejected(self):
        for value in ('<testsuite><testcase/></testsuite>', '<report/>'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read_xml(value)

    def test_missing_report_is_not_passed_even_with_zero_exit(self):
        (self.root / "selected.py").write_text("pass", encoding="utf-8")
        result = runner.execute(self.root, self.root / "run", [sys.executable, "-c", "pass"],
                                source_files=["selected.py"], expected_count=1, expected_tests=["x"])
        self.assertEqual("not_passed", result["status"])
        self.assertTrue(any("junit_report_unavailable" in reason for reason in result["reasons"]))

    def test_timeout_is_rejected_with_recovery_note(self):
        (self.root / "selected.py").write_text("pass", encoding="utf-8")
        result = runner.execute(self.root, self.root / "run", [sys.executable, "-c", "import time; time.sleep(5)"],
                                source_files=["selected.py"], expected_count=1, expected_tests=["x"], timeout=0.05)
        self.assertTrue(result["timed_out"])
        self.assertEqual("not_passed", result["status"])
        self.assertIn("recovery_note", result)

    def test_source_outside_project_is_rejected_before_launch(self):
        project = self.root / "project"
        project.mkdir()
        (self.root / "other.py").write_text("pass", encoding="utf-8")
        with patch.object(runner.subprocess, "Popen") as process:
            with self.assertRaises(ValueError):
                runner.execute(project, project / "run", [sys.executable, "-c", "pass"],
                               source_files=["../other.py"], expected_count=1, expected_tests=["x"])
            process.assert_not_called()

    def test_archive_allowlist_excludes_data_and_is_immutable(self):
        skill = self.root / "skill"
        (skill / "references").mkdir(parents=True)
        (skill / "LICENSE").write_text("Test license", encoding="utf-8")
        (skill / "SKILL.md").write_text("[Guide](references/guide.md)", encoding="utf-8")
        (skill / "references/guide.md").write_text("Portable content", encoding="utf-8")
        (skill / ".env").write_text("private sample", encoding="utf-8")
        output = self.root / "skill.zip"
        packager.build(skill, output)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual({"case-to-automation/SKILL.md", "case-to-automation/LICENSE", "case-to-automation/references/guide.md", "case-to-automation/manifest.json"}, set(archive.namelist()))
            self.assertIsNone(archive.testzip())
        with self.assertRaises(FileExistsError):
            packager.build(skill, output)

    def test_archive_rejects_local_outside_and_missing_references(self):
        skill = self.root / "skill"
        skill.mkdir()
        (skill / "LICENSE").write_text("Test license", encoding="utf-8")
        for value in ('[Local](C:/machine/guide.md)', '[Local](file:///C:/guide.md)',
                      '[Guide](../outside.md)', '![Image](assets/missing.png)',
                      '[Guide][guide]\n\n[guide]: missing.md'):
            (skill / "SKILL.md").write_text(value, encoding="utf-8")
            with self.subTest(value=value), self.assertRaises(ValueError):
                packager.build(skill, self.root / "invalid.zip")
        self.assertFalse((self.root / "invalid.zip").exists())

    def test_actual_skill_packages_without_external_files(self):
        manifest = packager.build(SKILL, self.root / "actual.zip")
        self.assertIn("LICENSE", {row["path"] for row in manifest["files"]})
        self.assertIn("scripts/run_and_verify.py", {row["path"] for row in manifest["files"]})

    def test_actual_unittest_pass_fail_skip_zero_have_honest_verdicts(self):
        summary = demo.run_demo(self.root / "demo")
        self.assertTrue(summary["demo_verified"])
        scenarios = summary["scenarios"]
        self.assertEqual(2, scenarios["pass"]["counts"]["passed"])
        self.assertGreater(scenarios["fail"]["counts"]["failed"], 0)
        self.assertEqual(1, scenarios["skip"]["counts"]["skipped"])
        self.assertEqual(0, scenarios["zero"]["counts"]["total"])
        self.assertEqual(0, scenarios["zero"]["native_exit_code"])
        self.assertEqual("not_passed", scenarios["zero"]["actual_verdict"])
        self.assertIn("zero_reported_tests", scenarios["zero"]["reasons"])
        with self.assertRaises(FileExistsError):
            demo.run_demo(self.root / "demo")

    def test_demo_does_not_confuse_missing_report_with_expected_failure(self):
        execute = demo.verifier.execute

        def missing_failure_report(project, output, command, **kwargs):
            result = execute(project, output, command, **kwargs)
            if "fail" in command:
                result["counts"] = {key: 0 for key in result["counts"]}
                result["reasons"] = ["junit_report_unavailable: missing"]
            return result

        with patch.object(demo.verifier, "execute", side_effect=missing_failure_report):
            summary = demo.run_demo(self.root / "missing-report")
        self.assertFalse(summary["demo_verified"])
        self.assertFalse(summary["scenarios"]["fail"]["verdict_matches"])


if __name__ == "__main__":
    unittest.main()
