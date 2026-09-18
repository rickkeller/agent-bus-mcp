# Delivery Plan

Plan-Version: 1

## P1: Establish public-safe delivery gates
Allowed actions: write `docs/delivery/*` only. Done when PRECODE passes. Validation: phase validator on `.`. Authority: local.

## P2: Add consultation behavior by vertical TDD
Allowed actions: edit source and tests. Done when RED evidence is captured and focused tests pass. Validation: focused pytest selections. Authority: local.

## P3: Explain the product plainly
Allowed actions: edit README and package metadata. Done when the first screen explains both modes, an example, and limits. Validation: content inspection and full tests. Authority: local.

## P4: Verify and commit
Allowed actions: run the acceptance map, update records, and commit. Done when every check passes, one commit exists, and the worktree is clean. Validation: exact checks in GATES. Authority: local.

Revision history: v1 admitted the single consultation-lane package.
