# Round 41.6 Kickoff — App Validation Cleanup

Date: 2026-04-16

## Why This Round Exists

Round 41.5 completed the closure audit for Round 41 and found that Round 41
was not yet closure-grade.

This round exists only to resolve the smallest remaining blockers needed to
close Round 41 honestly.

## Scope

In scope:

1. Make the Round 41 proof path self-contained
2. Stabilize the `r41-3` direct transparent-proxy proof
3. Determine whether one minimal live-app recheck is still required, and if so,
   define and prove it

Out of scope:

- redesigning the product
- reopening the full Round 41 implementation scope
- unrelated workflow cleanup
- broad doc rewrites beyond what is needed to close Round 41 honestly

## Starting Assumptions

- Round 41 core implementation is largely present
- `P9.5` field-alignment fix is already landed
- `clean-run.sh` failure-path `verify_run_id` fix is already landed
- remaining concerns are about closure-grade proof, reproducibility, and proof
  stability rather than broad missing implementation

## Success Condition

Round 41.6 succeeds if it produces a narrow, reproducible verification path that
lets the council close Round 41 without hand-waving flaky or out-of-band proof.
