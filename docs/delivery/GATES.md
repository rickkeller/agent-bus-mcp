# Delivery Phase Gates

Plan-Version: 3

## G1 Outcome
Status: REVIEWED
Outcome: Deliver one vendor-neutral local-core revision in which a shared state root is governed by an explicit directed multi-agent route graph with per-edge task/consultation modes, principal-bound identities, one-to-many task assignment, many-to-many isolation, and safe one-edge compatibility.
Definition-of-done: Acceptance items 1-12 in Contract v3 are covered by behavior tests and plain documentation; every mapped local check passes against a pinned tree and final commit; one local commit exists; no push, deployment, credential, private identity, or live-state change occurs.

## G2 Scope
Status: REVIEWED
Allowed: Source under `src/agent_bus_mcp`, tests, README.md, SECURITY.md, pyproject metadata, `docs/delivery/*`, local validators/tests/static scans, Git staging, and one local commit.
Forbidden: Push/publication, live/private repositories or profiles, service activation, credentials, private identities/paths, network federation, consensus, replication, discovery, multi-host transport, broad refactors, and enterprise scaffolding.
Rollback: Remove or normally revert the one local commit; no external state is changed. Stop immediately if a safe graph authority model contradicts two-agent state compatibility.

## G3 Plan
Status: REVIEWED
Owner: Focused local implementation agent; parent owns later public review, RELEASE, and ACCEPT.
Phases: P6 gate revision and exact PRECODE; P7 vertical RED-GREEN graph slices; P8 first-screen and migration/security documentation; P9 immutable-tree implementation review, full verification, VERIFY review, one local commit, and commit replay.
Stop-condition: Stop with one clean verified local commit and RELEASE_EXIT/ACCEPT_EXIT pending, or with one concrete safety/spec contradiction that cannot be resolved inside the bounded local core.

## G4 Acceptance Map
Status: REVIEWED
Checks: `python3 -m pytest -q tests/test_routing.py` for graph, one-to-many, many-to-many, route modes, identity binding, idempotency namespaces, lifecycle binding, wrong-worker, stale/duplicate/unsafe isolation, and one-edge compatibility; affected inherited queue/consultation/MCP/recurring suites; `python3 -m pytest -q`; `python3 -m py_compile src/agent_bus_mcp/*.py`; `git diff --check`; delivery validator; phase validator exact PRECODE then `--require-exit IMPLEMENT_EXIT` and `--require-exit VERIFY_EXIT`; deterministic tracked-content scan for forbidden private identifiers, private paths, and credential-shaped assignments; changed-path/scope review; final commit replay and clean status.
Environment: Existing repository Python environment, temporary pytest state roots, local Git object database, and profile skill validators; no installs, network publication, service changes, credentials, private state, or operator runs.
Evidence: LOG.md records each expected RED, focused GREEN counts, inherited/full suite counts, immutable tree ID, py_compile/diff/validator outputs, scan file/byte/finding totals, exit-gate outputs, final commit SHA/replay, changed paths, and clean status; STATUS.md provides the resume/handoff state.

## G5 Execution Capability
Status: REVIEWED
Agent-executable: All delivery edits, vertical tests, bounded source/docs edits, local static checks, deterministic scans, Git tree pinning, one local commit, and exact commit replay are executable in this repository.
Privilege-boundary: None for this slice. Public release, remote publication, private deployment, credentials, and live service operations remain outside this agent's authority and scope.
Operator-action-limit: No operator action is requested; the parent may independently review and later own RELEASE/ACCEPT.

## G6 Pre-code Review
Status: REVIEWED
Reviewer: Focused implementation agent, freshly comparing Contract v3, Plan v3, current singleton code/tests/docs, all twelve acceptance items, repository capabilities, and the explicit public-only/no-push boundary.
Verdict: REVIEWED

## Phase Exits
Implementation-evidence: Immutable Git tree `7c68e0fa5d942530826796ea4e54b65396fa85fd` contains the complete P7/P8 source, tests, and public documentation; all 18 changed paths are inside G2, RoutePolicy/API/routing/compatibility checks exist, and the affected 42-test batch passed before pinning.
Verification-evidence: Against implementation tree `7c68e0fa5d942530826796ea4e54b65396fa85fd`, `python3 -m pytest -q tests/test_routing.py` printed 6 passes and `python3 -m pytest -q` printed 42 passes; collection was consultation 18, MCP 10, queue 7, recurring 1, routing 6. `python3 -m py_compile src/agent_bus_mcp/*.py` and `git diff --check` exited 0 with no diagnostics. Delivery validation printed `PASS: delivery record valid (Working, P9)`. The deterministic 24-file / 100278-byte tracked-tree scan found zero private identifiers, zero secret signatures, and zero review-required credential assignments; its sole credential-shaped assignment was the intended environment lookup in `mcp.py`.
IMPLEMENT_EXIT: REVIEWED
VERIFY_EXIT: REVIEWED
RELEASE_EXIT: PENDING
ACCEPT_EXIT: PENDING
