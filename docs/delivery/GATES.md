# Delivery Phase Gates

Plan-Version: 1

## G1 Outcome
Status: REVIEWED
Outcome: Add a separate advice-only consultation lane with local ask/read and MCP claim/answer while preserving formal tasks.
Definition-of-done: Bounded durable records, fail-closed authority and fencing, plain-language docs, all acceptance checks, one local commit, and no push.

## G2 Scope
Status: REVIEWED
Allowed: Source, tests, README, pyproject metadata, and docs/delivery in this repository.
Forbidden: Installs, live services, credentials, external writes, private topology, automatic task effects, and unrelated scaffolding.
Rollback: Reset the single local outcome commit to its parent; no external state is changed.

## G3 Plan
Status: REVIEWED
Owner: Repository maintainer executing locally.
Phases: P1 gates; P2 vertical RED-GREEN implementation; P3 plain-language docs; P4 verification and local commit.
Stop-condition: Stop after all mapped checks pass and one focused local commit leaves a clean worktree; do not push.

## G4 Acceptance Map
Status: REVIEWED
Checks: Focused consultation, queue, and MCP pytest; full pytest; python3 -m py_compile src/agent_bus_mcp/*.py; git diff --check; delivery validator; MCP schema/formal-tool regression check; phase validator through VERIFY_EXIT; tracked-tree, complete-history, and staged redacted scans; git status.
Environment: Existing repository Python environment and temporary pytest state roots; no package installation or live service.
Evidence: LOG.md records the inherited RED→GREEN evidence, focused 35-pass result, full 36-pass result, static checks, schema/formal-task compatibility, and redacted scan totals; STATUS.md is the handoff summary.

## G5 Execution Capability
Status: REVIEWED
Agent-executable: All implementation, test, static, Git, validator, and redacted scan checks.
Privilege-boundary: None; pushing and deployment are explicitly out of scope.
Operator-action-limit: No operator action requested.

## G6 Pre-code Review
Status: REVIEWED
Reviewer: Implementing agent fresh review against contract and Plan v1.
Verdict: REVIEWED

## Phase Exits
Implementation-evidence: Complete diff inspected; changed paths stay within G2; the five closed MCP schemas match the README; the three formal task schemas, dispatch branches, and queue methods are AST-identical to the starting commit.
Verification-evidence: Focused 35 passed; full 36 passed; py_compile, diff check, delivery validation, and tracked-tree plus complete-history safety scan passed with zero suspicious findings.
IMPLEMENT_EXIT: REVIEWED
VERIFY_EXIT: REVIEWED
RELEASE_EXIT: BLOCKED: Parent push and public-target readback are outside this local-only slice.
ACCEPT_EXIT: BLOCKED: Public acceptance requires the parent-owned push and readback.
