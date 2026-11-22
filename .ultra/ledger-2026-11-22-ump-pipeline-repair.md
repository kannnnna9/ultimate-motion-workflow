# ledger — plan: docs/ultra/plans/2026-11-22-ump-pipeline-repair.md

Self-review: plan covers spec sections and review focus; interface conflict found in style approval semantics and resolved by spec amendment before implementation.

Task A: complete (tests: `python3 -m unittest discover -s tests -p 'test_jobs.py'` → 14 passed; commit pending with Task A changes).
Ruling: frame render now requires `naskah_approved` and successful preview approval advances to `final_ready`; this follows the approved spec and invalidates the previous style-before-script route if left unchanged.
