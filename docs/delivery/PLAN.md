# Delivery Plan

Plan-Version: 3

## P1: Establish public-safe delivery gates
Historical consultation release step; complete under Plan v1.

## P2: Add consultation behavior by vertical TDD
Historical consultation implementation step; complete under Plan v1.

## P3: Explain the product plainly
Historical consultation documentation step; complete under Plan v1.

## P4: Verify and commit
Historical consultation verification step; complete under Plan v1.

## P5: Publish and read back
Historical consultation publication step; complete under Plan v2.

## P6: Admit the N-to-N core revision
Objective: replace the completed release plan with one bounded public-core graph slice and review G1-G6. Allowed paths/actions: `docs/delivery/*` only. Done when Contract v3, Plan v3, current status/log, and all six reviewed gates agree and the phase validator prints exact `PRECODE PASS`. Validation: delivery-record validator and phase validator. Expected evidence: validator output in LOG.md. Authority: local; no publication.

## P7: Prove and implement the policy graph by vertical TDD
Objective: add one behavior slice at a time for one-to-many tasks, many-to-many isolation/idempotency, then per-edge consultation modes and principal-bound MCP use. Allowed paths/actions: `src/agent_bus_mcp/*` and `tests/*`; test edits must precede each production edit. Done when each focused test first fails for the missing behavior, then passes with the smallest implementation, and inherited security/lease tests stay green. Validation: focused pytest selections plus affected suites. Expected evidence: exact RED reasons and GREEN counts in LOG.md. Authority: local.

## P8: Document the graph and bounded migration
Objective: update the first screen and security/API documentation so ordinary readers see many-agent, one-to-many, and many-to-many behavior before jargon. Allowed paths/actions: README.md, SECURITY.md, package metadata, and delivery records. Done when controller coordination, explicit per-edge modes, principal binding, fixed return/origin binding, one-edge compatibility, and deferred transport adapters are documented without private identifiers. Validation: content inspection, full tests, and public-safety scan. Expected evidence: changed-path review and scan totals. Authority: local.

## P9: Verify the immutable tree and create one local commit
Objective: pin the implementation as an immutable Git tree, review IMPLEMENT_EXIT, execute the complete acceptance map, review VERIFY_EXIT, leave RELEASE/ACCEPT pending, and make one local commit. Allowed paths/actions: mapped verification commands, staging, delivery-record updates, and one local commit; no push. Done when focused and full tests, py_compile, diff check, delivery validator, phase validators, public-safety scan, final commit replay, and clean status pass. Validation: exact commands in GATES.md. Expected evidence: immutable tree ID, counts, final commit SHA, and clean status in LOG.md/STATUS.md. Authority: local; parent owns release and acceptance.

Revision history: v1 admitted the consultation lane; v2 added its authorized public release; v3 admits the bounded policy-controlled N-to-N core and explicitly forbids publication or live deployment in this slice.
