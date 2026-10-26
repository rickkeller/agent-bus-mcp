# Security policy

Keep the bearer token only in a private env file, never in source control; rotate it immediately after suspected exposure. Tokens never belong in git. Keep persistent queue state in an operator-created 0700 directory.

The bridge binds to loopback. Remote access requires an operator-controlled, authenticated TLS reverse proxy. The bearer-authenticated MCP adapter is constructed around one principal-bound queue object; MCP schemas contain no source, producer, worker, agent, or destination overrides. Principal identities are restricted to exact built-in strings containing 1–64 ASCII letters, digits, `_`, or `-`; Unicode identities are refused. `RoutePolicy` is a closed directional allowlist, each edge separately allows `task` and/or `consultation`, and the exact policy is bound to the state root. Opening a parent-format root without `policy.json` validates every stored producer-to-worker edge and record mode before persisting the proposed policy; one incompatible record refuses the migration without changing records. A worker can claim or finish only records addressed to its configured principal, while terminal results and answers remain readable only by their original producer.

Reference validation accepts only syntactic public HTTPS; it does not establish source safety or trustworthiness. Treat task goals, references, results, consultation questions, origin references, and answers as untrusted input. Consultation schemas provide no fields for attachments, task state, or execution authority. Do not put commands or credentials in consultation free text; answers remain advice-only and cannot change task state.

This release is a local durable core and state model, not network federation. It does not provide multi-host identity, service discovery, replicated storage, consensus, transport encryption, or transport authentication between hosts. An operator adding an adapter must authenticate its agent principal before constructing or selecting the corresponding bound queue object.

The public-research producer and its manifest are an example workload, not the product. Its reserved `.invalid` reference must not be treated as live content.

Report vulnerabilities privately through a GitHub Security Advisory. Do not place secrets, task contents, or state records in a public report.
