# Delivery Phase Gates

Plan-Version: 3

## G1 Outcome
Status: REVIEWED
Outcome: Deliver one vendor-neutral local-core revision in which a shared state root is governed by an explicit directed multi-agent route graph with per-edge task/consultation modes, principal-bound identities, one-to-many task assignment, many-to-many isolation, and safe one-edge compatibility.
Definition-of-done: Acceptance items 1-12 in Contract v3 remain covered; focused regressions prove ASCII-only authority identities and fail-closed admission of parent-format roots before mutation; every mapped local check passes; an independent immutable review is accepted by the parent; two docs-only closure commits authorize and then record publication to the existing public `origin/main`; public GitHub readback proves the exact branch, description, README claims, and reviewed source/test blob identities without any private/live deployment.

## G2 Scope
Status: REVIEWED
Allowed: Release closure may change only `docs/delivery/GATES.md`, `docs/delivery/LOG.md`, and `docs/delivery/STATUS.md`; run local validators and Git read-only checks; make one docs-only release-authorization commit and one docs-only acceptance commit; push local `main` to the existing public `origin/main`; update only the public GitHub repository description; and read back public GitHub branch, metadata, README, source, tests, and blob identities.
Forbidden: Source/test/config edits, force-push, remote replacement, branch rewriting, releases/tags, live/private repositories or profiles, service activation, credentials, private identities/paths, network federation, consensus, replication, discovery, multi-host transport, broad refactors, and enterprise scaffolding.
Rollback: Stop without force on any auth, divergence, push, metadata, or readback failure. Before publication, normally revert the docs-only authorization commit if needed; after publication, use a normal forward or revert commit only. Never mutate private/live state or rewrite public history.

## G3 Plan
Status: REVIEWED
Owner: Parent-authorized public release closer; the focused implementation and independent immutable review are complete.
Phases: Under P9 closure, record parent acceptance of the completed immutable review and review RELEASE_EXIT; commit the three release records; push to the unchanged existing public `origin/main`; verify branch/public metadata; set and verify the plain-language description; read back README and reviewed source/test blobs; then record and review ACCEPT_EXIT in a second docs-only commit, push it, and perform final exact remote readback.
Stop-condition: Finish only when local `HEAD` equals public `origin/main`, the repository is public with the exact approved description, public README assertions hold, reviewed source/test blob identities match implementation commit `4e161821643cecb666895f6b8cca78925a8b5fd3`, the final worktree is clean, and ACCEPT_EXIT is REVIEWED; otherwise stop at the first auth, divergence, push, metadata, or readback failure without force.

## G4 Acceptance Map
Status: REVIEWED
Checks: retain the completed immutable review evidence for focused 24/24, routing 35/35, full 71/71, py_compile/diff, direct authority/migration adversarial probes, artifact/tree/blob claims, scans, and clean replay; validate the delivery record and `--require-exit RELEASE_EXIT`; require exact pre-push remote SHA; push without force; verify `git ls-remote`, GitHub repository/API visibility/default-branch SHA, exact description, and raw/API README assertions; compare all remote source/test blob identities with implementation commit `4e161821643cecb666895f6b8cca78925a8b5fd3`; validate `--require-exit ACCEPT_EXIT`; push the docs-only acceptance commit; and repeat exact final remote, file, metadata, blob, and clean-sync checks.
Environment: Existing repository, local Git object database, profile validators, authenticated `gh`/SSH for the existing public GitHub repository, and unauthenticated public raw/API readback; no installs, broad test reruns, source/test edits, service changes, credentials disclosure, or private/live state.
Evidence: LOG.md records the completed reviewer probes without claiming a wrapper verdict, parent acceptance, exact authorization and acceptance commits, remote SHAs, public URL/visibility/default branch, exact description, README assertion readback, reviewed source/test blob matches, validator outputs, and final clean synchronized status; STATUS.md provides the release and closure state.

## G5 Execution Capability
Status: REVIEWED
Agent-executable: The three-file delivery edits, validators, two docs-only commits, non-force push to the existing origin, GitHub description update, public/API readback, blob comparisons, and final clean synchronization checks are executable with the already verified repository access.
Privilege-boundary: The user and parent explicitly authorized publication to this existing public repository. Private deployment, profiles, services, credentials, private identities/state, and unrelated repositories remain outside authority and scope.
Operator-action-limit: No operator action is requested. Stop rather than ask for retries if authentication, expected remote ancestry, push, metadata, or public readback fails.

## G6 Pre-code Review
Status: REVIEWED
Reviewer: Focused implementation gates were reviewed locally; for RELEASE the parent accepted the independent immutable evidence and explicitly authorized bounded publication to the existing public repository, while this release closer re-reviewed the source/test freeze, remote-divergence stop, docs-only closure, public readback, and private/live exclusions.
Verdict: REVIEWED

## Phase Exits
Historical-rejected-evidence: Commit `05fc178d4644b5c7c85c9a06d16bf8e98a1fa743`, tree `244c89f13515af9ec6afafc371ea53457c278d87`, and pre-commit implementation tree `7c68e0fa5d942530826796ea4e54b65396fa85fd` remain historical only. Independent review rejected 05fc178 for Unicode-confusable identities, policy-bypassing parent-format migration, and an unsupported `100278`-byte evidence claim. Its actual final-tree basis is 24 Git blobs / 101776 summed blob bytes; its pre-commit implementation-tree basis is 24 Git blobs / 99619 summed blob bytes.
Implementation-evidence: Implementation artifact commit `4e161821643cecb666895f6b8cca78925a8b5fd3`, tree `dfcb1061fa72302bc62cc50c806da5f189ccf317`, 24 Git blobs / 121111 summed blob bytes. Its exact four-path delta is `docs/delivery/LOG.md`, `docs/delivery/STATUS.md`, `src/agent_bus_mcp/queue.py`, and `tests/test_routing.py`; route-edge exact-type and parent-record fail-closed repairs remain within G2.
Verification-evidence: Exact implementation commit 4e16182 replay passed: focused repaired behavior 24 tests; routing suite 35 tests; full suite 71 tests; py_compile and commit/worktree diff checks exit 0; delivery validator `PASS: delivery record valid (Working, P9)`; four-path scope check passed; deterministic 24-file / 121111-byte tracked-blob private-path/private-key/provider-token/bearer-value/credential-assignment scan found 0; immutable replay matched commit and tree with a clean worktree.
Release-review-evidence: The independent reviewer completed every required immutable probe before its wrapper timed out: focused 24/24, routing 35/35, full 71/71, py_compile and diff checks, direct authority/migration adversarial probes with byte-preserving refusals and matching legacy lifecycle, artifact/tree/blob claim checks, scans with zero actionable findings, and final clean repository verification. The timed-out wrapper returned no verdict; the parent accepted this completed immutable evidence as release approval.
Acceptance-evidence: Authorization commit `2ef7a58659b0b252463b216065d86320bbefe1d3` was pushed without force to public `https://github.com/rickkeller/agent-bus-mcp`; `git ls-remote` and GitHub API readback both returned that exact main SHA, PUBLIC visibility, and default branch `main`. The verified description is `A policy-controlled local bus where many AI agents can ask questions and assign bounded work.` Raw/API README bytes matched and its opening asserts one logical bus/many configured principals, policy-controlled directed many-to-many pathways, virtual one-to-many controller assignment, explicit two-agent compatibility, and no multi-host network federation. GitHub API tree identities and independent raw Git-blob hashes for all 10 `src/` and `tests/` blobs matched reviewed implementation commit `4e161821643cecb666895f6b8cca78925a8b5fd3`.
IMPLEMENT_EXIT: REVIEWED
VERIFY_EXIT: REVIEWED
RELEASE_EXIT: REVIEWED
ACCEPT_EXIT: REVIEWED
