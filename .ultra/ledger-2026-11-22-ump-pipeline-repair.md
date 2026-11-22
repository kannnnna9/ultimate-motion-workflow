# ledger — plan: docs/ultra/plans/2026-11-22-ump-pipeline-repair.md

Self-review: plan covers spec sections and review focus; interface conflict found in style approval semantics and resolved by spec amendment before implementation.

Task A: complete (tests: `python3 -m unittest discover -s tests -p 'test_jobs.py'` → 14 passed; commit pending with Task A changes).
Task B: complete (tests: acceptance 3 passed; approval sentence 15 passed; full suite after fixture migration 147 passed, 1 skipped).
Ruling: public document targets execute synchronously in dispatcher so brief/research/naskah snapshots are actually produced; media targets remain nonblocking through submit.
Task C: partial (visual smoke BLOCKED: NumPy/browser dependency absent; `verify_ump.py --help` passed, no real media claim).
Final review: CHANGES_REQUESTED findings fixed — approval evidence persisted and bound, style_decision route reachable after style selection, template brand seeded on select_style, nested brand.style_id normalized for rollback, and duplicate source IDs rejected. Full suite rerun: 147 passed, 1 skipped.