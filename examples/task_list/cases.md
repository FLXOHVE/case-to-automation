# Fictional source test cases

These synthetic cases are supplied to demonstrate automation and result verification. They do not describe a real product.

## TC-001 — Complete and archive a task

Preparation: a fresh task list contains active task A.

1. Complete A. Expect A to be marked completed.
2. Archive A. Expect A to remain available in the archive.

Automation: `TaskListTests::test_complete_and_archive`.

## TC-002 — Reject archiving an active task

Preparation: a fresh task list contains active task A.

1. Try to archive A. Expect a validation error, with A still active and absent from the archive.

Automation: `TaskListTests::test_reject_active_archive`.

## Deliberately incomplete scope

External synchronization is unspecified. The demo's skipped test explicitly represents this missing implementation; it must not be counted as a passing result.
