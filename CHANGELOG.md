# Changelog

## 0.3.0

Published on the existing public history. The parent is the previous public tip. `v0.2.0` and `v0.1.0` were not moved.

Added:

- `ask`, one NATS request/reply question on `ask.<work>`. It does not create a board post, a task, or a consultation. Nobody listening returns `no_responders` at once.
- Optional `AGENT_BUS_NATS_URL`. Without it, `ask` is refused.
- A valid bearer on `GET /mcp` returns 405 with `Allow: POST`. A missing or wrong bearer is still 401.
- The package does not install a NATS server and does not start a listener. A stranger runs both.

## 0.2.0

Published on the existing public history. The parent commit is the 0.1.0 baseline, tagged `v0.1.0`.

Live compatibility of an old client with this package was not tested. A human has not onboarded from the documentation alone. An independent review of this publication was not run.

The 0.1.0 simple pair remains the default MCP server: `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID`, loopback only, bearer token, and the original five tools. Graph mode and the TLS host are opt-in.

Added, without removing those five tools:

- Board tools on the MCP door for one configured endpoint: `post_board`, `read_board`, `poll_unread`, `ack_board`, and `promote_board`. The simple pair refuses them.
- `read_result`, which returns one promoted task's stored result only to the endpoint that promoted the post. Every other caller is refused.
- A mutual-TLS host, enrollment command, and TLS client: `agent-bus-remote`, `agent-bus-enroll`, and `agent-bus-remote-call`.
- `wait_available` on that host. It returns a hint. It does not acknowledge, claim, or start a program.
- An optional local process, `agent-bus-hermes-adapt`, that can start one operator-approved argv. The package tests do not call a model.
- The helper address check refuses `100.64.0.0/10` unless `AGENT_BUS_ALLOW_TAILSCALE` is exactly `1`.

See `MIGRATION.md` before upgrading a directory that already has state.
