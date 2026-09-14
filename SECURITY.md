# Security policy

Keep the bearer token only in a private env file, never in source control; rotate it immediately after suspected exposure. Tokens never belong in git. Keep persistent queue state in an operator-created 0700 directory.

The bridge binds to loopback. Remote access requires an operator-controlled, authenticated TLS reverse proxy. Reference validation accepts only syntactic public HTTPS; it does not establish source safety or trustworthiness. Treat goals, references, fetched material, and results as untrusted input.

The public-research producer and its manifest are an example workload, not the product. Its reserved `.invalid` reference must not be treated as live content.

Report vulnerabilities privately through a GitHub Security Advisory. Do not place secrets, task contents, or state records in a public report.
