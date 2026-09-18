# Delivery Contract

Version: 1
Owner: repository maintainer

## Outcome
Add a separate, advice-only consultation lane so the configured local producer can ask a bounded question, the configured MCP worker can claim and answer it once, and the producer can read the answer without changing the formal task queue.

## Boundaries
- Allowed: repository source, tests, README, package metadata, and this delivery record.
- Forbidden: package installation, network/service changes, credentials, deployment, pushing, private environment details, and unrelated refactors.
- Non-goals: multi-worker routing, remote question creation, attachments, commands, execution authority, or automatic task creation/completion.

## Acceptance
Existing task behavior remains green; consultation happy path, MCP end-to-end path, idempotency, authority, lease, expiry, size, competing-answer, and task-separation cases pass; README begins in plain language; static and delivery checks pass; tracked content and complete Git history scans report no unallowlisted forbidden or credential-shaped content; one local commit leaves a clean worktree.

## Evidence and stop condition
Use repository-local tests and static checks only. Stop after one focused local commit; do not push. Rollback is that commit's parent. No operator action is required.
