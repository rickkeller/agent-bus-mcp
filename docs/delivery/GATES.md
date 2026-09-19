# Delivery Phase Gates

Plan-Version: 3

## G1 Outcome
Status: REVIEWED
Outcome: Deliver one vendor-neutral local-core revision in which a shared state root is governed by an explicit directed multi-agent route graph with per-edge task/consultation modes, principal-bound identities, one-to-many task assignment, many-to-many isolation, and safe one-edge compatibility.
Definition-of-done: Acceptance items 1-12 in Contract v3 remain covered; focused regressions prove ASCII-only authority identities and fail-closed admission of parent-format roots before mutation; every mapped local check passes; a focused implementation commit is followed by an evidence-only release-candidate commit because a commit cannot truthfully name itself; no push, deployment, credential, private identity, or live-state change occurs.

## G2 Scope
Status: REVIEWED
Allowed: Source under `src/agent_bus_mcp`, tests, README.md, SECURITY.md, pyproject metadata, `docs/delivery/*`, local validators/tests/static scans, Git staging, one focused implementation commit, and one evidence-only release-candidate commit.
Forbidden: Push/publication, live/private repositories or profiles, service activation, credentials, private identities/paths, network federation, consensus, replication, discovery, multi-host transport, broad refactors, and enterprise scaffolding.
Rollback: Remove or normally revert the two local repair commits; no external state is changed. Stop immediately if a safe graph authority model contradicts two-agent state compatibility.

## G3 Plan
Status: REVIEWED
Owner: Focused local implementation agent; parent owns later public review, RELEASE, and ACCEPT.
Phases: Return the rejected 05fc178 artifact from review to IMPLEMENT; P7 runs one vertical RED-GREEN repair cycle for ASCII identities and one for parent-format migration admission; P9 pins and verifies the focused implementation commit, then records its exact commit/tree/blob-count/byte-count in an evidence-only release-candidate commit.
Stop-condition: Stop with the focused implementation commit and evidence-only release-candidate commit clean and verified, with RELEASE_EXIT/ACCEPT_EXIT pending, or with one concrete safety/spec contradiction that cannot be resolved inside the bounded local core.

## G4 Acceptance Map
Status: REVIEWED
Checks: focused `tests/test_routing.py` regressions for ASCII-only policy, legacy-pair, and graph-bound identities before state-root creation; focused parent-format task/consultation migration tests for matching both-mode lifecycle and absent-edge/wrong-mode refusal before policy creation with byte preservation; all routing and affected inherited suites; `python3 -m pytest -q`; `python3 -m py_compile src/agent_bus_mcp/*.py`; `git diff --check`; delivery validator; phase validator exact PRECODE then `--require-exit IMPLEMENT_EXIT` and `--require-exit VERIFY_EXIT`; deterministic tracked-content scan for forbidden private identifiers, private paths, and credential-shaped assignments; changed-path/scope review; exact Git blob count and summed blob sizes; commit replay and clean status.
Environment: Existing repository Python environment, temporary pytest state roots, local Git object database, and profile skill validators; no installs, network publication, service changes, credentials, private state, or operator runs.
Evidence: LOG.md records each expected RED, focused GREEN counts, inherited/full suite counts, the rejected 05fc178 artifact as historical evidence only, the new implementation commit/tree and deterministic `git ls-tree` blob-count/summed-blob-size basis, py_compile/diff/validator outputs, scan totals, exit-gate outputs, evidence-only commit replay, changed paths, and clean status; STATUS.md provides the resume/handoff state.

## G5 Execution Capability
Status: REVIEWED
Agent-executable: All delivery edits, vertical tests, bounded source/docs edits, local static checks, deterministic scans, Git tree pinning, two local commits, and exact commit replay are executable in this repository.
Privilege-boundary: None for this slice. Public release, remote publication, private deployment, credentials, and live service operations remain outside this agent's authority and scope.
Operator-action-limit: No operator action is requested; the parent may independently review and later own RELEASE/ACCEPT.

## G6 Pre-code Review
Status: REVIEWED
Reviewer: Focused implementation agent, freshly comparing Contract v3 and the authoritative Plan v3 scope/outcome with the three independent blocker findings, current code/tests/docs, repository capabilities, and the explicit public-only/no-push boundary.
Verdict: REVIEWED

## Phase Exits
Historical-rejected-evidence: Commit `05fc178d4644b5c7c85c9a06d16bf8e98a1fa743`, tree `244c89f13515af9ec6afafc371ea53457c278d87`, and pre-commit implementation tree `7c68e0fa5d942530826796ea4e54b65396fa85fd` remain historical only. Independent review rejected 05fc178 for Unicode-confusable identities, policy-bypassing parent-format migration, and an unsupported `100278`-byte evidence claim. Its actual final-tree basis is 24 Git blobs / 101776 summed blob bytes; its pre-commit implementation-tree basis is 24 Git blobs / 99619 summed blob bytes.
Implementation-evidence: PENDING for the new focused repair commit and immutable tree.
Verification-evidence: PENDING for checks against the new focused repair artifact.
IMPLEMENT_EXIT: PENDING
VERIFY_EXIT: PENDING
RELEASE_EXIT: PENDING
ACCEPT_EXIT: PENDING
