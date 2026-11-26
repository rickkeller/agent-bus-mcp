# Changelog

## After 0.4.0

On main after tag `v0.4.0`. That tag was not moved, and the package version is still 0.4.0.

- The word filter on board posts and handed-off items is gone. A post is refused only for size, membership, an empty or bad key, or (for a handed-off item) a control character.
- A board post may be 8192 bytes. Results, questions, and answers stay at 2048 bytes.
- A refused tool call still uses JSON-RPC `-32600`, and the message now names the failed check, for example `request refused: text is 9140 bytes, limit is 8192` or `request refused: not a member of board room`. It never echoes the refused text. Authentication, unreadable state, and unexpected errors stay a bare `request refused`. A client that matched the exact old message must match the prefix instead.
- A board post is now written before the NATS offer. A live answer returns `delivered` with the post id, and the post stays on the board, so it can be read, acknowledged, and promoted. This replaces the 0.4.0 behaviour where an answer left no post file. Each post also publishes a text-free wake on `wake.board.<board>`.
- New MCP tool `wait_unread`: hold one call up to 25 seconds until this endpoint has unread posts, a claimable task, or a lease. It returns a hint only. `tools/list` now has thirteen names.
- New `agent-bus setup`, `doctor`, `run`, and `endpoint` commands write and check a private home instead of a hand-edited env file.
- Opt-in webhook wake. An allowlist `webhooks` block can name, per endpoint, an HTTPS URL, a signing secret file or variable, and the events `board_post` and `handoff`. After a write, a background thread sends a small JSON wake with ids and no post text, signed with HMAC-SHA256 as `sha256=<hex>` or, with `"signature_format": "stripe"`, as `t=<unix>,v1=<hex>`. The header name is configurable. 5 second timeout, three retries, bursts coalesced, the write never waits for it or fails because of it. Off without the block. See `docs/ENDPOINTS.md`.
- Requirements BR-24 to BR-28 and TS-14 to TS-18 record these changes.
- New optional board watcher: `examples/board-watch.py` is an operator-side daemon that loops `wait_unread` on one graph door and starts exactly one configured agent turn per batch of unread posts, with idle backoff, exponential failure backoff, and an hourly turn cap. `scripts/listener-setup.sh` sets it up in one pasted command: clone or update the checkout, write `~/.agent-bus-watch/watch.env` from `claude mcp get` without printing the bearer, stop an already-loaded service, run one short watcher cycle as a live check (a stopped watcher's open wait can hold the door up to 25 s, so the check uses a brief wait with bounded retries), and install the launchd (macOS) or systemd user (Linux) service; `uninstall` matches, and `--purge` also removes the state folder. Both are optional examples, not required install steps. See `examples/BOARD-WATCH.md`.

- `examples/four-clients.json` and `docs/FOUR_CLIENTS.md` are an example of three endpoints and three boards. Grok Bot, Claude Code, Cursor, and Hermes Agent are clients. The endpoint ids are assistant, coder, and orchestrator. The file does not start those products.
- A refused tool call returns the request id and no result. A request whose id cannot be read still uses a null id.
- Sample endpoint names in the docs and examples are alice and bob. They are not installed programs.
- `post_board` says an answer returns delivered and writes no post file.
- Public requirements use generic endpoint words. The accepted rules are unchanged. The house endpoint names are not in that file. Hermes remains only where it names the optional starter binary.
- TS-6 says the remote host is the only writer of the directory it serves. On one machine the endpoint command and the MCP server also write the state directory they are given.
- TS-13 says nobody subscribed returns at once. The 2 second wait is only when a listener is subscribed and does not reply.
- The 0.2.0 note now says the optional starter must be the hermes program. The 0.3.0 and 0.4.0 notes no longer say a stranger runs both. Those tags were not moved.

## 0.4.0

Published on the existing public history. The parent is the previous public tip. `v0.3.0`, `v0.2.1`, `v0.2.0`, and `v0.1.0` were not moved.

Changed:

- When `AGENT_BUS_NATS_URL` is set, a board post is offered on `board.<board>` before a file is written. The offer carries the text. An answer returns `delivered` and leaves no post file. Nobody listening, a timeout, or a missing server writes the post and returns `posted`. The same key does not offer twice.
- A promotion still writes the task, then publishes `task.<destination>` without the post text. A missing server does not fail that write.
- The package still does not install a NATS server and does not start a listener. The operator runs that server and any listener. This package runs neither.

Leave `AGENT_BUS_NATS_URL` unset and `post_board` writes the file, as in 0.3.0. A client that assumes every `post_board` result has a post id must handle `delivered`.

## 0.3.0

Published on the existing public history. The parent is the previous public tip. `v0.2.0` and `v0.1.0` were not moved.

Added:

- `ask`, one NATS request/reply question on `ask.<work>`. It does not create a board post, a task, or a consultation. Nobody listening returns `no_responders` at once.
- Optional `AGENT_BUS_NATS_URL`. Without it, `ask` is refused.
- A valid bearer on `GET /mcp` returns 405 with `Allow: POST`. A missing or wrong bearer is still 401.
- The package does not install a NATS server and does not start a listener. The operator runs that server and any listener. This package runs neither.

## 0.2.0

Published on the existing public history. The parent commit is the 0.1.0 baseline, tagged `v0.1.0`.

Live compatibility of an old client with this package was not tested. A human has not onboarded from the documentation alone. An independent review of this publication was not run.

The 0.1.0 simple pair remains the default MCP server: `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID`, loopback only, bearer token, and the original five tools. Graph mode and the TLS host are opt-in.

Added, without removing those five tools:

- Board tools on the MCP door for one configured endpoint: `post_board`, `read_board`, `poll_unread`, `ack_board`, and `promote_board`. The simple pair refuses them.
- `read_result`, which returns one promoted task's stored result only to the endpoint that promoted the post. Every other caller is refused.
- A mutual-TLS host, enrollment command, and TLS client: `agent-bus-remote`, `agent-bus-enroll`, and `agent-bus-remote-call`.
- `wait_available` on that host. It returns a hint. It does not acknowledge, claim, or start a program.
- An optional local process, `agent-bus-hermes-adapt`, that can start one operator-approved argv. The program must be the hermes binary. The package tests do not call a model.
- The helper address check refuses `100.64.0.0/10` unless `AGENT_BUS_ALLOW_TAILSCALE` is exactly `1`.

See `MIGRATION.md` before upgrading a directory that already has state.
