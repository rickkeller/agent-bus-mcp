# AGENTS.md

This package is a local durable bus. A worker may claim, answer, complete, or fail. Graph mode is one endpoint id plus the JSON allowlist (`AGENT_BUS_CONFIG` and `AGENT_BUS_ENDPOINT_ID`). A member in that mode may post and read a board, and the author may promote one post into one handoff. The simple pair is `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID`. It has no board. NATS and Hermes are optional. A board exists only when the config has a `boards` entry with an id and at least two members. Installing does not create that entry. The folder `boards/<id>/posts/` appears when a post is first written.

With `AGENT_BUS_NATS_URL` unset, that write does not notify the other member. They see the post by reading or by `poll_unread`, then `ack_board`. When the variable is set, the post is one request on `board.<board>` and one reply, not a broadcast. An answer leaves no post file. Nobody listening writes the file. Only promotion publishes `task.<destination>`. The README sections Start here and How the other side sees a post spell this out. `examples/board_listener.py` is an example listener, not a package command.

The board tools are not tied to a product. Any agent that can call this server can post, read, poll, and ack. `hermes_adapter.py` is the one starter, and it starts only the `hermes` binary. Another agent wakes itself: a listener such as `examples/board_listener.py`, `wait_available` from a process that stays connected, or `poll_unread` on a timer that agent runs. See the README section Any agent can use the board.

The endpoint that promoted that post may read the stored result. Every other endpoint is refused. An answer does not become a task. A board post does not become a task.

Do not add secrets, host paths, chat ids, or tokens. `.env.example` holds only `REPLACE_WITH_…` placeholders.

## Layout

```
src/agent_bus_mcp/
  __init__.py            exports DurableQueue, QueueRefused, RoutePolicy
  queue.py               RoutePolicy and the principal-bound DurableQueue (tasks, consultations, leases, idempotency)
  mcp.py                 MCPApplication (ASGI, bearer auth, POST /mcp JSON-RPC, thirteen tools) and the `agent-bus-mcp` entry point
  ask.py                 optional NATS request/reply client; does not install a server
  notify.py              optional NATS live offer and wake for a written board post; does not start a listener
  remote.py              TLS host: one state directory, one enrolled certificate per endpoint
  enroll.py              local enrollment and revocation, not a remote tool
  remote_call.py         TLS client for that host
  endpoint.py            `agent-bus-endpoint`, the local sender for one configured endpoint
  setup.py               `agent-bus setup`, `doctor`, `run`, and `endpoint`. Writes ~/.agent-bus and does not print the bearer token.
  endpoints.py           allowlist loader for chat and handoff
  hermes_adapter.py      optional starter; the program must be the hermes binary
  webhook.py             opt-in signed webhook wake (config `webhooks` block), sent off the write path
  recurring.py           `agent-bus-public-research`, an example recurring task producer
  research_manifest.json example manifest (uses a .invalid domain)
tests/                   pytest suite
systemd/                 example user units. Review them before use and never enable them from here
docs/REQUIREMENTS.md     requirements and specification
docs/ENDPOINTS.md        how to connect installed endpoints
docs/REMOTE.md           TLS host
docs/HERMES.md           optional process adapter
docs/FOUR_CLIENTS.md     four products on one bus; example only
examples/two-endpoints.json  board config for the README Start here path
examples/four-clients.json   three endpoints and three boards
examples/alice-bob.json      chat-only config; no boards entry
examples/board_listener.py   example NATS reply listener; not a package command
examples/board-watch.py      operator-side watcher; loops wait_unread and starts one agent turn per batch
scripts/listener-setup.sh    one-command setup for that watcher; operator convenience, not a package command
.env.example             environment placeholders
SECURITY.md              threat model and operator responsibilities
```

## Run and test

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[test]'
python3 -m pytest -q
python3 -m compileall -q src tests
```

For a manual run, use a throwaway state directory such as `$(mktemp -d)`, a random bearer secret, and a free port.

## Conventions

- Keep the boundary closed. Don't add MCP tools that create questions, chats, pointers, or ordinary tasks, and don't add tools that fetch URLs, open files, run commands, route to another worker, or call an external webhook. The one outbound request is the operator-configured webhook wake in `webhook.py`: off without a `webhooks` block, never chosen by a tool argument, ids only and never post text, signed, sent on a background thread so it cannot block or fail a write, and logged by scheme and host only. A graph-bound member may post, read, and poll a board. Polling does not acknowledge. `ack_board` names the exact posts. Only the author may promote that post, which creates one handoff to one other member. Remote callers never choose source, worker, author, or endpoint IDs.
- Every state change goes through the process lock, atomic JSON replacement and fsync. Keep lease fencing intact.
- Keep the limits (byte caps, expiry ranges, the four-reference cap, the ID character set) in step with README.md. Change both together.
- A refused tool call keeps JSON-RPC code `-32600` and names the failed check: byte counts, limits, positions, and field names only. Do not echo secrets or the refused content. Authentication, revocation, a closed TLS wait, unreadable stored state, and unexpected errors stay a bare `request refused`. Raise `QueueRefused` with that reason, or return the same text as the error message.
- `uvicorn` and `nats-py` are the runtime dependencies. `nats-py` is the client. This package does not install a NATS server or start a listener. Don't add more without a strong reason.
- Every behaviour change needs a test.
