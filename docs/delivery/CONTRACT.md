# Delivery Contract

Version: 2
Owner: repository maintainer

## Outcome
Add a separate, advice-only consultation lane so the configured local producer can ask a bounded question, the configured MCP worker can claim and answer it once, and the producer can read the answer without changing the formal task queue.

## Boundaries
- Allowed: repository source, tests, README, package metadata, this delivery record, pushing the verified commits to the existing public `origin/main`, and updating only the GitHub repository description under the owner's explicit authorization.
- Forbidden: package installation, service/deployment changes, credentials, history rewriting, visibility changes, private environment details, and unrelated refactors.
- Non-goals: multi-worker routing, remote question creation, attachments, commands, execution authority, or automatic task creation/completion.

## Acceptance
Existing task behavior remains green; consultation happy path, MCP end-to-end path, idempotency, authority, lease, expiry, size, competing-answer, and task-separation cases pass; README begins in plain language; static and delivery checks pass; tracked content and complete Git history scans report no unallowlisted forbidden or credential-shaped content; the verified commits and plain-language description are read back from the public repository.

## Evidence and stop condition
Use repository-local tests and static checks before release, then push only to the existing public `origin/main`, update only the repository description, and read both back. Rollback is a normal public revert plus restoration of the prior description; never rewrite public history. No operator action is required.
