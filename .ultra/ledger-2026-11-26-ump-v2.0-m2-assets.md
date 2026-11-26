# ledger — plan: docs/ultra/plans/2026-11-26-ump-v2.0-m2-assets.md

- Task 1: in progress — bounded asset catalogue, SVG authoring, PNG/JPEG import, tool schema/dispatch, and owned Telegram-cache reader implemented. Final targeted suites: `test_assets.py` 30/30, `test_composition_tools.py` 9/9, `test_storyboard.py` 8/8; compileall and diff-check pass. Review-fix pass covers escaped CSS URLs, declared SVG bounds, PNG/JPEG structure, catalogue invariants, blob integrity, lock metadata revisions, crash recovery, and the 128 MiB/256-version cap; awaiting task commit and final checklist.
- Task 2: pending
- Task 3: pending
- Task 4: pending

## Rulings

- Debug finding: JPEG parser failure came from the copied test fixture containing extra bytes, not marker-walk logic; compared the fixture byte-for-byte with the generated Pillow fixture, corrected the Base64, and confirmed exact match before rerunning tests. Cost if wrong: would have changed a correct parser to accommodate malformed test data.
- Plan change: Task 1 adds an inter-process catalogue lock after the concurrency test exposed same-version/temp-file collisions across writers; registration serializes briefly, preserving immutable history.
- Task 1 Ruling: enforce the tool-schema metadata caps at the storage boundary and limit canonical parameters to 16 KiB so direct store callers cannot bypass bounded catalogue records. Keep `__init__.py` unchanged because its registration loop already iterates `SCHEMAS`; cost if wrong: long metadata needs shortening, and future registration refactors need an explicit registration assertion.
- Task 1 Ruling: `locked` freezes the content hash, not metadata; same-byte metadata corrections append a new immutable version that inherits the lock, preserving history while allowing provenance/license corrections. Cost if wrong: use a new ID if lock was intended to freeze metadata too.
- Task 1 Ruling: cap stored asset bytes at 128 MiB and version/file count at 256, counting orphan/temp files and never auto-deleting immutable data; keep aggregate DATA_ROOT retention/quota in M0. Cost if wrong: the asset cap may be reached early, or crash leftovers may need operator cleanup.
- Task 1 Ruling: recover same-hash orphan blobs under the catalogue lock and skip conflicting version paths rather than overwrite/delete them. Cost if wrong: history may have version gaps and unindexed conflicting orphans remain for manual cleanup.
