"""Real unittest execution with a minimal JUnit adapter for this fixed demo.

This adapter covers pass/failure/error/skip for ordinary cases used here. It is
not a general-purpose replacement for a framework's native JUnit reporter.
"""
import argparse
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from app import TaskList


class TaskListTests(unittest.TestCase):
    def setUp(self):
        self.app = TaskList()

    def test_complete_and_archive(self):
        self.app.complete("A")
        self.assertTrue(self.app.tasks["A"]["completed"])
        self.app.archive("A")
        self.assertIn("A", self.app.archived_ids())

    def test_reject_active_archive(self):
        with self.assertRaises(ValueError):
            self.app.archive("A")
        self.assertFalse(self.app.tasks["A"]["completed"])
        self.assertEqual([], self.app.archived_ids())

    @unittest.skip("External synchronization is not implemented in this fictional fixture")
    def test_external_sync(self):
        self.fail("Must not be reported as a passing test")


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.rows.append((test, None, ""))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.rows.append((test, "failure", self._exc_info_to_string(err, test)))

    def addError(self, test, err):
        super().addError(test, err)
        self.rows.append((test, "error", self._exc_info_to_string(err, test)))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.rows.append((test, "skipped", reason))


def broken_archive(self, task_id):
    # Intentional local fault: lose the record instead of retaining its archive.
    del self.tasks[task_id]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("pass", "fail", "skip", "zero"), required=True)
    parser.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args()
    selected = [] if args.scenario == "zero" else ["test_complete_and_archive"]
    if args.scenario in {"pass", "fail"}:
        selected.append("test_reject_active_archive")
    elif args.scenario == "skip":
        selected.append("test_external_sync")
    suite = unittest.TestSuite(TaskListTests(name) for name in selected)
    runner = unittest.TextTestRunner(resultclass=RecordingResult, verbosity=2)
    if args.scenario == "fail":
        with patch.object(TaskList, "archive", broken_archive):
            result = runner.run(suite)
    else:
        result = runner.run(suite)
    root = ET.Element("testsuite", tests=str(result.testsRun), failures=str(len(result.failures)),
                      errors=str(len(result.errors)), skipped=str(len(result.skipped)))
    for test, status, details in result.rows:
        case = ET.SubElement(root, "testcase", classname=type(test).__name__, name=test._testMethodName)
        if status:
            ET.SubElement(case, status).text = details
    ET.ElementTree(root).write(args.junit, encoding="utf-8", xml_declaration=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
