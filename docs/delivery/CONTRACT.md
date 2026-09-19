# Delivery Contract

Version: 3
Owner: repository maintainer

## Outcome
Evolve the public local Agent Bus core from one configured producer/worker pair into a policy-controlled directed graph. One state root must support at least three configured agents, one-to-many formal-task assignment, many-to-many authorized routes, and strict cross-route isolation while preserving explicit task-versus-consultation authority.

## Boundaries
- Allowed: repository source, tests, README, SECURITY.md, package metadata, and `docs/delivery/*`; local test/static commands; one local commit for parent review.
- Forbidden: pushing or publishing, service/deployment changes, credentials, live/private profiles or state, private identities or paths, network federation, consensus, replicated storage, service discovery, live multi-host transport, and unrelated refactors or scaffolding.
- Compatibility boundary: existing two-agent deployments remain expressible as a one-edge graph. Retain an unambiguous safe constructor path where possible, document any call-site migration, and never keep singleton producer/worker fields as the internal authority model.

## Acceptance
1. A route policy configures at least three distinct agents and a closed directional edge allowlist.
2. One bound controller can assign formal tasks to at least two workers on one state root.
3. At least two bound producers can address allowed workers; a missing edge is refused without exposing queue contents.
4. Source identity comes only from a bound local API object/principal, never from enqueue/consultation payload fields.
5. Workers claim, read, and finish only records addressed to their bound identity; cross-worker attempts are refused or return no record without content leakage.
6. Idempotency is namespaced by mode, source, and destination: producers may reuse a key, while replay on one route returns the original record.
7. Task return binding and consultation origin binding remain fixed through terminal transitions; no arbitrary delivery authority is added.
8. A two-agent one-edge graph and the legacy constructor remain covered, with redundant caller identity overrides removed and migration documented.
9. Route edges carry explicit `task` and/or `consultation` modes so advice authority remains separate from formal task authority.
10. The README opening states many-agent, one-to-many, policy-controlled many-to-many behavior and explains that controllers coordinate without monopolizing transport.
11. Negative tests cover spoofed source fields, forbidden routes, wrong workers, stale leases, duplicate terminal transitions, unsafe IDs, and cross-route isolation.
12. Focused RED/GREEN evidence, the full suite, py_compile, diff check, delivery and phase validators through VERIFY_EXIT, and a public-repository private-identifier/secret scan pass before one local commit. RELEASE_EXIT and ACCEPT_EXIT remain pending for the parent.

## Evidence and stop condition
Use temporary local state roots and the repository Python environment only. Stop after the immutable implementation tree and final local commit pass the mapped checks, the worktree is clean, no private/live path changed, and no push occurred. Rollback is removal or normal revert of the single local commit; no external state is changed and no operator action is required.
