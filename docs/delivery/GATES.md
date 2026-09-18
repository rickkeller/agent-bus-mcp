# Delivery Phase Gates

Plan-Version: 2

## G1 Outcome
Status: REVIEWED
Outcome: Add and publicly release a separate advice-only consultation lane with local ask/read and MCP claim/answer while preserving formal tasks.
Definition-of-done: Bounded durable records, fail-closed authority and fencing, plain-language docs, all acceptance checks, public `origin/main` and README readback, and a verified plain-language GitHub description.

## G2 Scope
Status: REVIEWED
Allowed: Source, tests, README, pyproject metadata, docs/delivery, pushing verified commits to the existing public `origin/main`, and updating only the GitHub repository description.
Forbidden: Installs, service/deployment changes, credentials, history rewriting, visibility changes, private topology, automatic task effects, and unrelated scaffolding.
Rollback: Revert the public commits normally and restore the prior repository description; never rewrite public history.

## G3 Plan
Status: REVIEWED
Owner: Repository maintainer executing locally under the owner's explicit publication authorization.
Phases: P1 gates; P2 vertical RED-GREEN implementation; P3 plain-language docs; P4 verification and local commit; P5 public push, metadata update, readback, and acceptance record.
Stop-condition: Stop after all mapped checks pass, public `main`, README, visibility, URL, and description are read back, the acceptance record is pushed, and the worktree is clean.

## G4 Acceptance Map
Status: REVIEWED
Checks: Focused consultation, queue, and MCP pytest; full pytest; python3 -m py_compile src/agent_bus_mcp/*.py; git diff --check; delivery validator; MCP schema/formal-tool regression check; phase validator through ACCEPT_EXIT; tracked-tree, complete-history, and staged redacted scans; git status; `git ls-remote` for public `main`; GitHub API readback of visibility, URL, description, and README.
Environment: Existing repository Python environment and temporary pytest state roots plus the already-authenticated existing GitHub remote; no package installation or service deployment.
Evidence: LOG.md records RED→GREEN evidence, focused 35-pass result, full 36-pass result, static checks, schema/formal-task compatibility, redacted scan totals, public commit/readme readback, and metadata readback; STATUS.md is the final summary.

## G5 Execution Capability
Status: REVIEWED
Agent-executable: All implementation, test, static, Git, validator, redacted scan, authenticated push, metadata update, and public readback checks.
Privilege-boundary: Existing GitHub authentication is available and the owner explicitly authorized this public update; service deployment and credentials remain out of scope.
Operator-action-limit: No operator action requested.

## G6 Pre-code Review
Status: REVIEWED
Reviewer: Parent agent fresh review against Contract v2, Plan v2, the immutable implementation commit, independent 36-test replay, public-safety evidence, authenticated remote, and owner publication authorization.
Verdict: REVIEWED

## Phase Exits
Implementation-evidence: Complete diff inspected; changed paths stay within G2; the five closed MCP schemas match the README; the three formal task schemas, dispatch branches, and queue methods are AST-identical to the starting commit.
Verification-evidence: Focused 35 passed; full 36 passed; py_compile, diff check, delivery validation, and tracked-tree plus complete-history safety scan passed with zero suspicious findings.
IMPLEMENT_EXIT: REVIEWED
VERIFY_EXIT: REVIEWED
RELEASE_EXIT: REVIEWED
ACCEPT_EXIT: REVIEWED
